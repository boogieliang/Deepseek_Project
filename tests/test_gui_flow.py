"""Exercise the real GUI async flow: open -> auto scan -> fill."""
from __future__ import annotations

import json
import os
import shutil
import sys
import time
import tkinter as tk
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
os.environ["SMART_FORM_FILLER_NO_SANDBOX"] = "1"
os.environ["SMART_FORM_FILLER_HOME"] = str(ROOT / ".gui-e2e")
shutil.rmtree(ROOT / ".gui-e2e", ignore_errors=True)

sys.path.insert(0, str(ROOT))
from formfiller.gui import SmartFormFillerApp, enable_dpi_awareness  # noqa: E402
from formfiller.selftest import DEMO_VALUES  # noqa: E402

enable_dpi_awareness()
root = tk.Tk()
app = SmartFormFillerApp(root)
for key, value in DEMO_VALUES.items():
    if key in app.vars:
        app.vars[key].set(value)
app.storage.add_custom_field("期望薪资", "20k-30k")
app.url_var.set((ROOT / "assets" / "welcome.html").as_uri())
app.auto_scan_var.set(True)

state = {"stage": "open", "start": time.time(), "result": None}
root.after(500, app.open_url)


def finish(result: dict) -> None:
    state["result"] = result
    (ROOT / ".gui-flow.json").write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    root.after(300, root.destroy)


def poll() -> None:
    if time.time() - state["start"] > 120:
        finish({"ok": False, "error": "timeout", "stage": state["stage"],
                "logs": app.ui_logs[-10:] + app.engine.logs[-10:]})
        return
    if app.busy:
        root.after(400, poll)
        return
    if state["stage"] == "open":
        if app.matches:
            state["stage"] = "fill"
            app.fill_page()
            root.after(500, poll)
            return
        root.after(500, poll)
        return
    if state["stage"] == "fill":
        rows = [app.tree.item(i, "values") for i in app.tree.get_children()]
        statuses = {}
        for match, row in zip(app.matches, rows):
            statuses[str(match.key)] = row[3]
        filled = sum(1 for v in statuses.values() if v == "已填写")
        finish({
            "ok": filled >= 18,
            "scanned": len(app.matches),
            "filled": filled,
            "statuses": statuses,
            "logs": app.ui_logs[-10:] + app.engine.logs[-10:],
        })
        return
    root.after(500, poll)


root.after(800, poll)
root.mainloop()
print(json.dumps(state["result"], ensure_ascii=False, indent=2))
raise SystemExit(0 if state["result"] and state["result"].get("ok") else 1)
