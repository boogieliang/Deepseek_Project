"""Minimal Chrome DevTools Protocol client and browser launcher.

Only a page target is attached.  The client is intentionally synchronous: the
GUI runs browser work on a worker thread, and one request is in flight at a
time.  That keeps the implementation small and avoids races.
"""
from __future__ import annotations

import json
import os
import shutil
import socket
import subprocess
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

try:
    import websocket  # type: ignore
except Exception:  # pragma: no cover - surfaced with a friendly message
    websocket = None


class CDPError(RuntimeError):
    pass


BROWSER_CANDIDATES = (
    # Edge is present on every supported Windows install and is Chromium-based,
    # so it is the safest automatic choice.
    ("edge", r"Microsoft\Edge\Application\msedge.exe"),
    ("chrome", r"Google\Chrome\Application\chrome.exe"),
)


def _candidate_roots() -> List[Path]:
    roots = []
    for env in ("ProgramFiles", "ProgramFiles(x86)", "LOCALAPPDATA"):
        value = os.environ.get(env)
        if value:
            roots.append(Path(value))
    return roots


def find_browser_paths(settings: Optional[Dict[str, Any]] = None) -> List[str]:
    """Return browser executables in preference order."""
    settings = settings or {}
    choice = str(settings.get("browser") or "auto").lower()
    custom = str(settings.get("browser_path") or "").strip().strip('"')
    if choice == "custom" and custom and Path(custom).is_file():
        return [custom]
    if choice in ("chrome", "edge"):
        names = [n for n in BROWSER_CANDIDATES if n[0] == choice]
    else:
        names = list(BROWSER_CANDIDATES)
    found_paths: List[str] = []
    for _name, relative in names:
        for root in _candidate_roots():
            path = root / relative
            if path.is_file():
                found_paths.append(str(path))
                break
        else:
            for exe in ("chrome.exe", "msedge.exe"):
                found = shutil.which(exe)
                if found and (choice == "auto" or exe.startswith(choice)):
                    found_paths.append(found)
                    break
    if custom and Path(custom).is_file():
        found_paths.append(custom)
    # Preserve order, drop duplicates.
    return list(dict.fromkeys(found_paths))


def find_browser_path(settings: Optional[Dict[str, Any]] = None) -> Optional[str]:
    paths = find_browser_paths(settings)
    return paths[0] if paths else None


def free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _local_request(url: str, method: str = "GET", timeout: float = 2.0) -> Any:
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    req = urllib.request.Request(url, method=method)
    with opener.open(req, timeout=timeout) as resp:
        raw = resp.read().decode("utf-8", "replace")
    try:
        return json.loads(raw)
    except ValueError:
        return raw


@dataclass
class PageTarget:
    id: str
    url: str
    title: str
    ws_url: str


class BrowserSession:
    """Owns one Chromium process/profile and one attached page target."""

    def __init__(
        self,
        data_dir: Path,
        settings: Optional[Dict[str, Any]] = None,
        *,
        headless: bool = False,
    ) -> None:
        self.data_dir = Path(data_dir)
        self.settings = dict(settings or {})
        self.headless = headless
        self.port: int = 0
        self.process: Optional[subprocess.Popen] = None
        self.ws = None
        self.ws_url: str = ""
        self.target: Optional[PageTarget] = None
        self.browser_path: Optional[str] = None
        self._lock = threading.RLock()
        self._events: List[Dict[str, Any]] = []
        self._next_id = 0
        self._log: List[str] = []
        self.used_no_sandbox = False
        self.user_data_dir = self.data_dir / "browser-profile"
        self.user_data_dir.mkdir(parents=True, exist_ok=True)

    # ------------------------------------------------------------------ status
    @property
    def connected(self) -> bool:
        return self.ws is not None and self.target is not None

    @property
    def logs(self) -> Tuple[str, ...]:
        return tuple(self._log)

    def _note(self, text: str) -> None:
        stamp = time.strftime("%H:%M:%S")
        self._log.append(f"[{stamp}] {text}")
        if len(self._log) > 200:
            del self._log[:-200]

    def _http(self, path: str, method: str = "GET", timeout: float = 2.0) -> Any:
        return _local_request(f"http://127.0.0.1:{self.port}{path}", method=method, timeout=timeout)

    # ------------------------------------------------------------------ launch
    def _port_is_live(self) -> bool:
        try:
            self._http("/json/version", timeout=1.2)
            return True
        except Exception:
            return False

    def start(self, initial_url: str = "about:blank", timeout: float = 25.0) -> None:
        with self._lock:
            if self.connected and self._port_is_live():
                return
            # A previous process on the same profile would swallow the new
            # launch and keep its old debugging port, so stop it first.
            if self.process is not None and self.process.poll() is None:
                if self.port and self._port_is_live():
                    self.close_ws()
                    try:
                        self._connect_page(initial_url=None)
                        if self._health_check():
                            return
                    except Exception:
                        pass
                self.close()
            else:
                self.close_ws()

            paths = find_browser_paths(self.settings)
            if not paths:
                raise CDPError("未找到 Chrome 或 Edge，请在“设置”中指定浏览器路径。")

            configured_port = int(self.settings.get("port") or 0)
            if configured_port and self._port_is_live():
                self.port = configured_port
                self._connect_page(initial_url=None)
                self._note(f"已连接到 127.0.0.1:{self.port} 上现有的浏览器")
                return

            env_flag = os.environ.get("SMART_FORM_FILLER_NO_SANDBOX", "").lower() in ("1", "true", "yes")
            errors: List[str] = []
            self.browser_path = paths[0]
            for path in paths:
                # Try the normal browser sandbox first.  Some managed/hosted
                # Windows environments cannot start a Chromium renderer inside
                # their own job object; the compatibility retry is only used
                # after the sandboxed attempt demonstrably fails.
                attempts = [True] if env_flag else [False, True]
                for no_sandbox in attempts:
                    self.port = configured_port or free_port()
                    try:
                        self._launch_process(path, initial_url, no_sandbox, timeout)
                        self._connect_page(initial_url=initial_url)
                        if not self._health_check():
                            raise CDPError("页面渲染进程没有响应")
                        self.browser_path = path
                        self.used_no_sandbox = no_sandbox
                        self._note(
                            "浏览器已连接，可以开始扫描页面"
                            + ("（兼容模式）" if no_sandbox else "")
                        )
                        return
                    except Exception as exc:
                        message = str(exc).strip().splitlines()[0] if str(exc).strip() else exc.__class__.__name__
                        errors.append(f"{Path(path).name}: {message}")
                        self.close()
            detail = "；".join(errors[-3:])
            raise CDPError(
                "无法启动可自动化的浏览器。请确认 Chrome/Edge 可用，"
                "或在“设置”中更换浏览器。"
                + (f"\n诊断：{detail}" if detail else "")
            )

    def _launch_process(self, path: str, initial_url: str, no_sandbox: bool, timeout: float) -> None:
        args = [
            path,
            f"--remote-debugging-port={self.port}",
            "--remote-allow-origins=*",
            f"--user-data-dir={self.user_data_dir}",
            "--no-first-run",
            "--no-default-browser-check",
            "--disable-background-mode",
            "--disable-session-crashed-bubble",
            "--hide-crash-restore-bubble",
            "--disable-features=Translate,MediaRouter",
            "--new-window",
        ]
        if no_sandbox:
            args.extend(["--no-sandbox", "--disable-dev-shm-usage"])
        if self.headless:
            args.extend(["--headless=new", "--disable-gpu", "--window-size=1280,900"])
        args.append(initial_url or "about:blank")
        creationflags = 0
        if os.name == "nt":
            creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        self._note(f"启动浏览器：{path}" + ("（兼容模式）" if no_sandbox else ""))
        launch_log = self.data_dir / "browser-launch.log"
        try:
            handle = launch_log.open("ab")
        except OSError:
            handle = None
        self.process = subprocess.Popen(
            args,
            stdin=subprocess.DEVNULL,
            stdout=handle or subprocess.DEVNULL,
            stderr=handle or subprocess.DEVNULL,
            creationflags=creationflags,
        )
        if handle is not None:
            handle.close()
        deadline = time.time() + timeout
        while time.time() < deadline:
            if self._port_is_live():
                return
            if self.process.poll() is not None:
                tail = ""
                try:
                    tail = launch_log.read_text(encoding="utf-8", errors="replace").strip().splitlines()[-1]
                except Exception:
                    pass
                raise CDPError(
                    "浏览器启动失败，可能被安全软件拦截。请尝试手动打开 Chrome/Edge，"
                    "或在设置中更换浏览器。"
                    + (f"（{tail[:220]}）" if tail else "")
                )
            time.sleep(0.25)
        raise CDPError("浏览器调试端口启动超时，请重试或更换浏览器。")

    def _health_check(self) -> bool:
        try:
            return self.evaluate("1 + 1", timeout=8) == 2
        except Exception:
            return False

    def _page_targets(self) -> List[PageTarget]:
        raw = self._http("/json/list", timeout=3.0)
        targets: List[PageTarget] = []
        if not isinstance(raw, list):
            return targets
        for item in raw:
            if not isinstance(item, dict) or item.get("type") != "page":
                continue
            ws_url = item.get("webSocketDebuggerUrl")
            if not ws_url:
                continue
            targets.append(
                PageTarget(
                    id=str(item.get("id", "")),
                    url=str(item.get("url", "")),
                    title=str(item.get("title", "")),
                    ws_url=str(ws_url),
                )
            )
        return targets

    def list_tabs(self) -> List[PageTarget]:
        if not self.port:
            return []
        try:
            return self._page_targets()
        except Exception:
            return []

    def _create_page(self, url: str = "about:blank") -> None:
        # Chrome requires PUT for /json/new.
        try:
            self._http(f"/json/new?{urllib.parse.quote(url, safe='')}", method="PUT", timeout=5.0)
        except Exception:
            self._http(f"/json/new?{urllib.parse.quote(url, safe='')}", timeout=5.0)

    def _connect_page(self, initial_url: Optional[str] = None) -> None:
        targets = self._page_targets()
        if not targets:
            self._create_page(initial_url or "about:blank")
            deadline = time.time() + 5.0
            while time.time() < deadline and not targets:
                time.sleep(0.2)
                targets = self._page_targets()
        if not targets:
            raise CDPError("浏览器已启动，但没有可用的页面标签。")
        target = targets[0]
        self._open_ws(target)

    def _open_ws(self, target: PageTarget) -> None:
        if websocket is None:
            raise CDPError("缺少 websocket-client 组件，无法连接浏览器。")
        self.close_ws()
        try:
            self.ws = websocket.create_connection(
                target.ws_url,
                timeout=15,
                enable_multithread=True,
                suppress_origin=True,
            )
        except TypeError:
            self.ws = websocket.create_connection(target.ws_url, timeout=15, enable_multithread=True)
        except Exception as exc:
            raise CDPError(f"连接浏览器调试通道失败：{exc}") from exc
        self.ws.settimeout(1.0)
        self.target = target
        self.ws_url = target.ws_url
        self._events.clear()
        self.send("Page.enable", timeout=6)
        self.send("Runtime.enable", timeout=6)

    def attach_tab(self, target: PageTarget) -> None:
        with self._lock:
            self._open_ws(target)
            self._note(f"已切换到标签页：{target.title or target.url}")

    def close_ws(self) -> None:
        if self.ws is not None:
            try:
                self.ws.close()
            except Exception:
                pass
        self.ws = None
        self.ws_url = ""
        self.target = None

    def close(self, keep_process: bool = False, keep_page: bool = False) -> None:
        self.close_ws()
        if self.process is not None and not keep_process:
            try:
                if self.process.poll() is None:
                    self.process.terminate()
                    try:
                        self.process.wait(timeout=4)
                    except subprocess.TimeoutExpired:
                        self.process.kill()
            except Exception:
                pass
            self.process = None

    # ------------------------------------------------------------------ protocol
    def send(self, method: str, params: Optional[Dict[str, Any]] = None, timeout: float = 45.0) -> Dict[str, Any]:
        if self.ws is None:
            raise CDPError("浏览器尚未连接")
        with self._lock:
            self._next_id += 1
            msg_id = self._next_id
            payload: Dict[str, Any] = {"id": msg_id, "method": method}
            if params:
                payload["params"] = params
            try:
                self.ws.send(json.dumps(payload, ensure_ascii=False))
            except Exception as exc:
                self.close_ws()
                raise CDPError(f"向浏览器发送指令失败：{exc}") from exc
            deadline = time.time() + timeout
            while True:
                remaining = max(0.05, deadline - time.time())
                try:
                    self.ws.settimeout(min(remaining, 1.0))
                    raw = self.ws.recv()
                except Exception as exc:
                    # websocket timeout is retryable; a closed socket is not.
                    if time.time() >= deadline:
                        raise CDPError(f"浏览器响应超时：{method}") from exc
                    if not self.ws:
                        raise CDPError("与浏览器的连接已断开") from exc
                    continue
                if not raw:
                    continue
                try:
                    data = json.loads(raw)
                except ValueError:
                    continue
                if data.get("id") == msg_id:
                    if "error" in data:
                        error = data["error"]
                        raise CDPError(str(error.get("message") or error))
                    return data.get("result") or {}
                if "method" in data:
                    self._events.append(data)
                    if len(self._events) > 300:
                        del self._events[:-300]

    def _take_event(self, method: str) -> Optional[Dict[str, Any]]:
        for idx, event in enumerate(self._events):
            if event.get("method") == method:
                return self._events.pop(idx)
        return None

    def wait_event(self, method: str, timeout: float = 25.0) -> bool:
        event = self._take_event(method)
        if event is not None:
            return True
        deadline = time.time() + timeout
        while time.time() < deadline:
            event = self._take_event(method)
            if event is not None:
                return True
            try:
                self.ws.settimeout(min(0.5, max(0.05, deadline - time.time())))
                raw = self.ws.recv()
            except Exception:
                if not self.ws:
                    return False
                continue
            if not raw:
                continue
            try:
                data = json.loads(raw)
            except ValueError:
                continue
            if data.get("method"):
                self._events.append(data)
        return False

    def evaluate(
        self,
        expression: str,
        context_id: Optional[int] = None,
        *,
        await_promise: bool = False,
        timeout: float = 60.0,
    ) -> Any:
        params: Dict[str, Any] = {
            "expression": expression,
            "returnByValue": True,
            "awaitPromise": bool(await_promise),
            "userGesture": True,
        }
        if context_id is not None:
            params["contextId"] = context_id
        result = self.send("Runtime.evaluate", params, timeout=timeout)
        if result.get("exceptionDetails"):
            details = result["exceptionDetails"]
            message = details.get("text") or "页面脚本执行失败"
            exception = details.get("exception") or {}
            if isinstance(exception, dict) and exception.get("description"):
                message = str(exception["description"]).splitlines()[0]
            raise CDPError(message)
        remote = result.get("result") or {}
        if remote.get("subtype") == "error":
            raise CDPError(str(remote.get("description") or "页面脚本执行失败"))
        return remote.get("value")

    def navigate(self, url: str, timeout: float = 30.0) -> None:
        if not url:
            return
        if not self.connected:
            self.start(url)
            return
        self._events.clear()
        result = self.send("Page.navigate", {"url": url}, timeout=15)
        error = (result or {}).get("errorText")
        if error:
            raise CDPError(f"打开页面失败：{error}")
        self.wait_event("Page.loadEventFired", timeout=timeout)
        time.sleep(0.5)

    def reload(self, timeout: float = 30.0) -> None:
        if not self.connected:
            raise CDPError("浏览器尚未连接")
        self._events.clear()
        self.send("Page.reload", {"ignoreCache": False}, timeout=15)
        self.wait_event("Page.loadEventFired", timeout=timeout)
        time.sleep(0.5)

    def current_url(self) -> str:
        try:
            return str(self.evaluate("location.href", timeout=10) or "")
        except Exception:
            return ""

    def title(self) -> str:
        try:
            return str(self.evaluate("document.title", timeout=10) or "")
        except Exception:
            return ""

    # ------------------------------------------------------------------ frames
    def frames(self) -> List[Dict[str, Any]]:
        tree = self.send("Page.getFrameTree", timeout=15).get("frameTree") or {}
        out: List[Dict[str, Any]] = []

        def walk(node: Dict[str, Any], parent: Optional[str], path: List[str]) -> None:
            frame = node.get("frame") or {}
            frame_id = str(frame.get("id") or "")
            if not frame_id:
                return
            label = str(frame.get("name") or "").strip()
            url = str(frame.get("url") or "")
            if not label:
                if parent is None:
                    label = "主页面"
                else:
                    # Use the path component as a friendly iframe name.
                    tail = url.rstrip("/").rsplit("/", 1)[-1] if url else "iframe"
                    label = tail[:32] or "iframe"
            child_path = path + [label]
            out.append(
                {
                    "frame_id": frame_id,
                    "parent_id": parent,
                    "url": url,
                    "path": " > ".join(child_path),
                    "is_main": parent is None,
                }
            )
            for child in node.get("childFrames") or []:
                walk(child, frame_id, child_path)

        walk(tree, None, [])
        return out

    def create_context(self, frame_id: str) -> Optional[int]:
        try:
            result = self.send(
                "Page.createIsolatedWorld",
                {
                    "frameId": frame_id,
                    "worldName": "smart_form_filler",
                    "grantUniveralAccess": False,
                },
                timeout=15,
            )
            return int(result.get("executionContextId"))
        except CDPError:
            return None

