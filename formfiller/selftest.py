"""Headless end-to-end self test, also useful in the packaged EXE."""
from __future__ import annotations

import http.server
import os
import shutil
import socketserver
import tempfile
import threading
import time
import traceback
from pathlib import Path
from typing import Any, Dict

from .cdp import BrowserSession
from .engine import FormEngine
from .fields import human_key
from .gui import resource_path
from .storage import Storage

DEMO_VALUES = {
    "name": "张三",
    "gender": "男",
    "birthday": "1990-05-20",
    "phone": "13800138000",
    "email": "zhangsan@example.com",
    "province": "广东省",
    "city": "深圳市",
    "district": "南山区",
    "address": "科技园路 1 号",
    "postal_code": "518000",
    "company": "深智科技有限公司",
    "position": "产品经理",
    "school": "中山大学",
    "major": "计算机科学与技术",
    "degree": "本科",
    "wechat": "zhangsan_wechat",
    "remarks": "期待与您沟通",
    "agree": "是",
}

EXPECTED = {
    "fullname": "张三",
    "gender": "male",
    "birthday": "1990-05-20",
    "mobile": "13800138000",
    "email": "zhangsan@example.com",
    "email_confirm": "zhangsan@example.com",
    "province": "广东省",
    "city": "深圳市",
    "address": "科技园路 1 号",
    "zip": "518000",
    "company": "深智科技有限公司",
    "job_title": "产品经理",
    "education": "本科",
    "university": "中山大学",
    "major": "计算机科学与技术",
    "expected_salary": "20k-30k",
    "notes": "期待与您沟通",
    "agree": True,
    "wechat_shadow": "zhangsan_wechat",
    "school_iframe": "中山大学",
}


class _Handler(http.server.SimpleHTTPRequestHandler):
    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, directory=str(resource_path("assets")), **kwargs)

    def log_message(self, *args: Any) -> None:
        pass


def _state_js() -> str:
    return """(() => {
      const names = ['fullname','gender','birthday','mobile','email','email_confirm',
                     'province','city','address','zip','company','job_title','education',
                     'university','major','expected_salary','notes','agree'];
      const out = {};
      for (const n of names) {
        const els = Array.from(document.getElementsByName(n));
        if (!els.length) continue;
        const el = els[0];
        if (el.type === 'radio') out[n] = (els.find(e => e.checked) || {}).value || '';
        else if (el.type === 'checkbox') out[n] = el.checked;
        else out[n] = el.value;
      }
      out.wechat_shadow = document.getElementById('wechat-host').shadowRoot.querySelector('input').value;
      const frame = document.getElementById('frame');
      try { out.school_iframe = frame.contentDocument.querySelector('input').value; } catch (e) {}
      return out;
    })()"""


def run_selftest() -> Dict[str, Any]:
    os.environ["SMART_FORM_FILLER_NO_SANDBOX"] = "1"
    httpd = socketserver.ThreadingTCPServer(("127.0.0.1", 0), _Handler)
    port = httpd.server_address[1]
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    temp = Path(tempfile.mkdtemp(prefix="sff-selftest-"))
    browser: BrowserSession | None = None
    result: Dict[str, Any] = {"ok": False}
    try:
        store = Storage(temp / "home")
        store.add_custom_field("期望薪资", "20k-30k")
        store.set_values(DEMO_VALUES)
        browser = BrowserSession(temp / "browser", store.settings, headless=True)
        browser.start(f"http://127.0.0.1:{port}/welcome.html")
        time.sleep(0.8)
        engine = FormEngine(browser)
        matches = engine.scan(store.values, store.field_defs(), {})
        summary = engine.fill(matches, fill_empty_only=True)
        time.sleep(0.2)
        state = browser.evaluate(_state_js())
        errors = []
        for key, want in EXPECTED.items():
            got = state.get(key)
            if got != want:
                errors.append(f"{key}: expected {want!r}, got {got!r}")
        result = {
            "ok": not errors and summary.get("failed", 0) == 0,
            "scanned": len(matches),
            "matched": sum(1 for m in matches if m.key),
            "summary": summary,
            "state": state,
            "errors": errors,
        }
    except Exception:
        result = {"ok": False, "error": traceback.format_exc()}
    finally:
        if browser is not None:
            try:
                browser.close()
            except Exception:
                pass
        httpd.shutdown()
        shutil.rmtree(temp, ignore_errors=True)
    return result
