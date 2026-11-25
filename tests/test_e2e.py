"""End-to-end browser smoke test (run with the project venv)."""
from __future__ import annotations

import http.server
import json
import os
import shutil
import socketserver
import sys
import threading
import time
from pathlib import Path

os.environ.setdefault("SMART_FORM_FILLER_NO_SANDBOX", "1")

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from formfiller.cdp import BrowserSession
from formfiller.engine import FormEngine
from formfiller.fields import all_field_defs
from formfiller.storage import Storage


class Handler(http.server.SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=str(ROOT / "assets"), **kwargs)

    def log_message(self, *args):
        pass


def main() -> int:
    httpd = socketserver.TCPServer(("127.0.0.1", 0), Handler)
    port = httpd.server_address[1]
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    tmp = ROOT / ".test-tmp"
    shutil.rmtree(tmp, ignore_errors=True)
    tmp.mkdir(parents=True, exist_ok=True)
    if True:
        home = tmp / "home"
        store = Storage(home)
        store.add_custom_field("期望薪资", "20k-30k")
        values = {
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
        store.set_values(values)

        browser = BrowserSession(home / "browser", store.settings, headless=True)
        try:
            browser.start(f"http://127.0.0.1:{port}/welcome.html")
            time.sleep(1.0)
            engine = FormEngine(browser)
            fields = engine.scan(store.values, store.field_defs(), {})
            print(f"SCANNED {len(fields)}")
            for item in fields:
                print(
                    f"  {item.field_label[:28]:30} -> {str(item.key):12} "
                    f"{item.confidence:.2f} {item.status} [{item.kind}]"
                )
            matched = [m for m in fields if m.key and m.value]
            print(f"MATCHED {len(matched)}")
            summary = engine.fill(fields, fill_empty_only=True)
            print("FILL", json.dumps(summary, ensure_ascii=False))
            time.sleep(0.3)
            state = browser.evaluate(
                """(() => {
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
            )
            print("STATE", json.dumps(state, ensure_ascii=False, indent=2))
            expected = {
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
            errors = []
            for key, want in expected.items():
                got = state.get(key)
                if got != want:
                    errors.append(f"{key}: expected {want!r}, got {got!r}")
            if errors:
                print("FAIL")
                for error in errors:
                    print(" -", error)
                return 1
            if summary["filled"] < 18:
                print(f"FAIL: only {summary['filled']} fields filled")
                return 1
            print("PASS")
            return 0
        finally:
            browser.close()
            httpd.shutdown()


if __name__ == "__main__":
    raise SystemExit(main())
