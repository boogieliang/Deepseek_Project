"""Entry point for the Smart Form Filler desktop application."""
from __future__ import annotations

import json
import os
import sys
import tempfile
import traceback
from pathlib import Path


def _error_candidates() -> list[Path]:
    candidates: list[Path] = []
    home = os.environ.get("SMART_FORM_FILLER_HOME", "").strip()
    if home:
        candidates.append(Path(home))
    for env in ("APPDATA", "LOCALAPPDATA"):
        value = os.environ.get(env)
        if value:
            candidates.append(Path(value) / "SmartFormFiller")
    exe_dir = Path(sys.executable).resolve().parent if getattr(sys, "frozen", False) else Path(__file__).resolve().parent
    candidates.append(exe_dir)
    candidates.append(Path(tempfile.gettempdir()) / "SmartFormFiller")
    return list(dict.fromkeys(candidates))


def _error_log_path() -> Path:
    for directory in _error_candidates():
        try:
            directory.mkdir(parents=True, exist_ok=True)
            probe = directory / f".write-test-{os.getpid()}"
            probe.write_text("ok", encoding="utf-8")
            probe.unlink()
            return directory / "startup-error.log"
        except OSError:
            continue
    return Path(tempfile.gettempdir()) / "SmartFormFiller-startup-error.log"


def _write_error(text: str) -> Path:
    path = _error_log_path()
    try:
        path.write_text(text, encoding="utf-8")
    except OSError:
        pass
    return path


def _show_startup_error(message: str, log_path: Path) -> None:
    """Windowed EXEs have no console, so surface startup failures in a dialog."""
    try:
        import tkinter as tk
        from tkinter import messagebox

        root = tk.Tk()
        root.withdraw()
        root.attributes("-topmost", True)
        summary = message.strip().splitlines()
        detail = "\n".join(summary[-6:])[:1800]
        messagebox.showerror(
            "智填启动失败",
            "程序启动时遇到错误，已停止。\n\n"
            f"{detail}\n\n"
            f"详细日志：{log_path}\n\n"
            "如果日志提示无法写入 AppData，请把程序放到“下载”或桌面等可写目录后重试。",
        )
        root.destroy()
    except Exception:
        pass


def _run_gui_smoke(out: Path) -> int:
    import tkinter as tk

    from formfiller.gui import SmartFormFillerApp, enable_dpi_awareness

    enable_dpi_awareness()
    root = tk.Tk()
    app = SmartFormFillerApp(root)
    root.update_idletasks()
    root.update()
    payload = {
        "ok": True,
        "geometry": root.winfo_geometry(),
        "screen": [root.winfo_screenwidth(), root.winfo_screenheight()],
        "scale": app.scale,
        "data_dir": str(app.storage.home),
        "uses_fallback": bool(getattr(app.storage, "used_fallback", False)),
        "tree_columns": list(app.tree["columns"]),
        "tabs": [app.notebook.tab(tab, "text") for tab in app.notebook.tabs()],
    }
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    root.after(300, root.destroy)
    root.mainloop()
    return 0


def _out_path(flag: str) -> Path:
    out = Path.cwd() / f"{flag.strip('-')}-result.json"
    for index, arg in enumerate(sys.argv):
        if arg == flag and index + 1 < len(sys.argv) and not sys.argv[index + 1].startswith("-"):
            out = Path(sys.argv[index + 1])
        elif arg.startswith(flag + "="):
            out = Path(arg.split("=", 1)[1])
    return out


def main() -> int:
    try:
        if "--selftest" in sys.argv:
            from formfiller.selftest import run_selftest

            out = _out_path("--selftest")
            result = run_selftest()
            out.parent.mkdir(parents=True, exist_ok=True)
            out.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
            return 0 if result.get("ok") else 1

        if "--gui-smoke" in sys.argv:
            return _run_gui_smoke(_out_path("--gui-smoke"))

        import tkinter as tk

        from formfiller.gui import SmartFormFillerApp, enable_dpi_awareness

        enable_dpi_awareness()
        root = tk.Tk()
        SmartFormFillerApp(root)
        root.mainloop()
        return 0
    except Exception:
        text = traceback.format_exc()
        log_path = _write_error(text)
        _show_startup_error(text, log_path)
        raise


if __name__ == "__main__":
    raise SystemExit(main())
