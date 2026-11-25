"""Local persistence: profile values, settings, and per-site rules."""
from __future__ import annotations

import json
import os
import sys
import tempfile
import threading
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

from .fields import FIELD_DEFS, all_field_defs

APP_DIR_NAME = "SmartFormFiller"
DEFAULT_SETTINGS: Dict[str, Any] = {
    "browser": "auto",              # auto | chrome | edge | custom
    "browser_path": "",
    "port": 0,                      # 0 = choose a free port automatically
    "fill_empty_only": True,
    "auto_scan": True,
    "remember_rules": True,
    "highlight": True,
    "last_url": "",
    "close_browser_on_exit": False,
    "window_geometry": "1280x840",
}


def _app_dir() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(sys.argv[0]).resolve().parent


def default_home() -> Path:
    override = os.environ.get("SMART_FORM_FILLER_HOME", "").strip()
    if override:
        return Path(override).expanduser()
    appdata = os.environ.get("APPDATA")
    base = Path(appdata) if appdata else Path.home() / "AppData" / "Roaming"
    return base / APP_DIR_NAME


def home_candidates() -> List[Path]:
    """Writable locations in preference order.

    Some managed Windows machines (or endpoint-protection policies) deny
    writes to %APPDATA% even for the signed-in user.  A desktop app must not
    fail to start because of that, so fall back to LocalAppData, the program
    folder, and finally the system temp directory.
    """
    candidates: List[Path] = []
    env_home = os.environ.get("SMART_FORM_FILLER_HOME", "").strip()
    if env_home:
        candidates.append(Path(env_home).expanduser())
    appdata = os.environ.get("APPDATA")
    if appdata:
        candidates.append(Path(appdata) / APP_DIR_NAME)
    localappdata = os.environ.get("LOCALAPPDATA")
    if localappdata:
        candidates.append(Path(localappdata) / APP_DIR_NAME)
    candidates.append(_app_dir() / f"{APP_DIR_NAME}-Data")
    candidates.append(Path(tempfile.gettempdir()) / APP_DIR_NAME)
    candidates.append(Path.cwd() / f"{APP_DIR_NAME}-Data")
    # Preserve order, drop duplicates.
    return list(dict.fromkeys(candidates))


def _ensure_writable(path: Path) -> bool:
    try:
        path.mkdir(parents=True, exist_ok=True)
        probe = path / f".write-test-{os.getpid()}-{threading.get_ident()}"
        probe.write_text("ok", encoding="utf-8")
        probe.unlink()
        return True
    except OSError:
        return False


def choose_writable_home() -> Path:
    tried: List[str] = []
    for candidate in home_candidates():
        if _ensure_writable(candidate):
            return candidate
        tried.append(str(candidate))
    raise RuntimeError(
        "无法创建可写的数据目录，已尝试：\n"
        + "\n".join(tried)
        + "\n请把程序放到有写入权限的目录（例如“下载”或桌面）后重试。"
    )


def _atomic_write(path: Path, data: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(prefix=path.name + ".", suffix=".tmp", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as fh:
            fh.write(data)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp_name, path)
    finally:
        try:
            if os.path.exists(tmp_name):
                os.unlink(tmp_name)
        except OSError:
            pass


def _read_json(path: Path, default: Any) -> Any:
    try:
        with path.open("r", encoding="utf-8-sig") as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return default


class Storage:
    def __init__(self, home: Optional[Path] = None) -> None:
        if home is not None:
            self.home = Path(home)
            self.home.mkdir(parents=True, exist_ok=True)
            self.used_fallback = False
        else:
            self.home = choose_writable_home()
            self.used_fallback = self.home != default_home()
        self.profile_path = self.home / "profile.json"
        self.settings_path = self.home / "settings.json"
        self.rules_path = self.home / "rules.json"
        self.browser_data_dir = self.home / "browser-profile"
        self._lock = threading.RLock()
        self.profile: Dict[str, Any] = {}
        self.settings: Dict[str, Any] = {}
        self.rules: Dict[str, Any] = {"version": 1, "hosts": {}}
        self.load()

    # ------------------------------------------------------------------ load/save
    def load(self) -> None:
        with self._lock:
            self.settings = dict(DEFAULT_SETTINGS)
            loaded = _read_json(self.settings_path, {})
            if isinstance(loaded, dict):
                for key, value in loaded.items():
                    if key in DEFAULT_SETTINGS:
                        self.settings[key] = value

            profile_raw = _read_json(self.profile_path, {})
            values: Dict[str, Any] = {}
            custom: List[Dict[str, str]] = []
            if isinstance(profile_raw, dict) and isinstance(profile_raw.get("values"), dict):
                values = dict(profile_raw["values"])
                custom = [
                    {"key": str(x.get("key", "")), "label": str(x.get("label", ""))}
                    for x in profile_raw.get("custom_fields", [])
                    if isinstance(x, dict) and x.get("key") and x.get("label")
                ]
            elif isinstance(profile_raw, dict):
                # Accept a plain key/value JSON export.
                values = dict(profile_raw)
            for fd in FIELD_DEFS:
                values.setdefault(fd.key, "")
            self.profile = {
                "values": values,
                "custom_fields": custom,
                "updated_at": str(profile_raw.get("updated_at", "")) if isinstance(profile_raw, dict) else "",
            }

            rules_raw = _read_json(self.rules_path, {})
            if isinstance(rules_raw, dict) and isinstance(rules_raw.get("hosts"), dict):
                self.rules = {
                    "version": 1,
                    "hosts": {
                        str(host): {str(sig): str(key) for sig, key in rules.items() if isinstance(rules, dict)}
                        for host, rules in rules_raw["hosts"].items()
                        if isinstance(rules, dict)
                    },
                }

    def save_profile(self) -> None:
        with self._lock:
            self.profile["updated_at"] = datetime.now().isoformat(timespec="seconds")
            _atomic_write(self.profile_path, json.dumps(self.profile, ensure_ascii=False, indent=2))

    def save_settings(self) -> None:
        with self._lock:
            _atomic_write(self.settings_path, json.dumps(self.settings, ensure_ascii=False, indent=2))

    def save_rules(self) -> None:
        with self._lock:
            _atomic_write(self.rules_path, json.dumps(self.rules, ensure_ascii=False, indent=2))

    # ------------------------------------------------------------------ profile
    @property
    def values(self) -> Dict[str, Any]:
        return self.profile.setdefault("values", {})

    @property
    def custom_fields(self) -> List[Dict[str, str]]:
        return self.profile.setdefault("custom_fields", [])

    def field_defs(self):
        return all_field_defs(self.custom_fields)

    def get_value(self, key: str) -> str:
        from .fields import human_key
        return human_key(self.values.get(key, ""))

    def set_values(self, values: Dict[str, Any]) -> None:
        with self._lock:
            for key, value in values.items():
                self.values[key] = value
            self.save_profile()

    def add_custom_field(self, label: str, value: str = "") -> str:
        with self._lock:
            existing = {x["key"] for x in self.custom_fields}
            idx = 1
            while f"custom_{idx}" in existing:
                idx += 1
            key = f"custom_{idx}"
            self.custom_fields.append({"key": key, "label": label.strip() or f"自定义字段 {idx}"})
            self.values[key] = value
            self.save_profile()
            return key

    def update_custom_field(self, key: str, label: str, value: str) -> None:
        with self._lock:
            for item in self.custom_fields:
                if item["key"] == key:
                    item["label"] = label.strip() or item["label"]
                    break
            self.values[key] = value
            self.save_profile()

    def remove_custom_field(self, key: str) -> None:
        with self._lock:
            self.custom_fields[:] = [x for x in self.custom_fields if x["key"] != key]
            self.values.pop(key, None)
            self.save_profile()

    def export_profile(self, path: Path) -> None:
        payload = {
            "app": "SmartFormFiller",
            "version": 1,
            "exported_at": datetime.now().isoformat(timespec="seconds"),
            "values": self.values,
            "custom_fields": self.custom_fields,
        }
        _atomic_write(Path(path), json.dumps(payload, ensure_ascii=False, indent=2))

    def import_profile(self, path: Path) -> int:
        raw = _read_json(Path(path), None)
        if not isinstance(raw, dict):
            raise ValueError("文件不是有效的 JSON 对象")
        values: Dict[str, Any] = {}
        custom: List[Dict[str, str]] = []
        if isinstance(raw.get("values"), dict):
            values = dict(raw["values"])
            custom = [
                {"key": str(x.get("key", "")), "label": str(x.get("label", ""))}
                for x in raw.get("custom_fields", [])
                if isinstance(x, dict) and x.get("key") and x.get("label")
            ]
        else:
            values = dict(raw)
        known = {fd.key for fd in FIELD_DEFS}
        custom_keys = {x["key"] for x in custom}
        with self._lock:
            for key, value in values.items():
                if key in known or key in custom_keys:
                    self.values[key] = value
                elif isinstance(value, (str, int, float, bool)):
                    # Import unknown keys as custom fields so nothing is lost.
                    idx = len(self.custom_fields) + 1
                    new_key = f"custom_{idx}"
                    while new_key in self.values:
                        idx += 1
                        new_key = f"custom_{idx}"
                    self.custom_fields.append({"key": new_key, "label": str(key)})
                    self.values[new_key] = value
            for item in custom:
                if item["key"] not in {x["key"] for x in self.custom_fields}:
                    self.custom_fields.append(item)
            self.save_profile()
        return len(values)

    # ------------------------------------------------------------------ rules
    def get_rules_for(self, host: str) -> Dict[str, str]:
        with self._lock:
            return dict(self.rules.get("hosts", {}).get(host, {}))

    def set_rule(self, host: str, signature: str, key: Optional[str]) -> None:
        if not host or not signature:
            return
        with self._lock:
            hosts = self.rules.setdefault("hosts", {})
            bucket = hosts.setdefault(host, {})
            if key:
                bucket[signature] = key
            else:
                bucket.pop(signature, None)
            if not bucket:
                hosts.pop(host, None)
            self.save_rules()

    def clear_rules(self, host: Optional[str] = None) -> None:
        with self._lock:
            if host:
                self.rules.get("hosts", {}).pop(host, None)
            else:
                self.rules = {"version": 1, "hosts": {}}
            self.save_rules()
