# -*- coding: utf-8 -*-
import json
import urllib.request

BASE = "http://127.0.0.1:8787"


def get(path):
    with urllib.request.urlopen(BASE + path) as r:
        return json.loads(r.read().decode("utf-8"))


def post(payload):
    req = urllib.request.Request(BASE + "/api/weekly",
                                 data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
                                 headers={"Content-Type": "application/json"}, method="POST")
    with urllib.request.urlopen(req) as r:
        return json.loads(r.read().decode("utf-8"))


def delete(i):
    req = urllib.request.Request(BASE + "/api/weekly/%d" % i, method="DELETE")
    with urllib.request.urlopen(req) as r:
        return json.loads(r.read().decode("utf-8"))


s = get("/api/state?region=%E5%85%A8%E5%9B%BD&period=4%E5%91%A8")
row = s["recent"][0]
print("BEFORE id=%s rev=%s collect=%s | 全国收入=%.1f 回款率=%.1f 建议=%d"
      % (row["id"], row["revenue"], row["collect_rate"], s["raw"]["rev"],
         [k for k in s["kpis"] if k["key"] == "col"][0]["value"], len(s["suggestions"])))

# 模拟"华北复合肥本周回款恶化 + 库存积压"
payload = dict(week="2026-W44", region="华北", category="复合肥",
               revenue=row["revenue"], target=row["target"],
               gross_margin=row["gross_margin"], collect_rate=45,
               inventory_days=120, receivable=row["receivable"],
               expiring=row["expiring"], orders=row["orders"],
               fill_rate=row["fill_rate"], owner="测试员", note="接口写入测试")
print("POST ->", post(payload))

s2 = get("/api/state?region=%E5%85%A8%E5%9B%BD&period=4%E5%91%A8")
kpi = {k["key"]: k for k in s2["kpis"]}
print("AFTER  华北回款率=%.1f 周转=%.1f | 全国回款率=%.1f 建议=%d"
      % ([r for r in s2["recent"] if r["id"] == row["id"]][0]["collect_rate"],
         [r for r in s2["recent"] if r["id"] == row["id"]][0]["inventory_days"],
         kpi["col"]["value"], len(s2["suggestions"])))
for t in s2["suggestions"]:
    print("   ", t["level"], t["title"], "=>", t["impact"])

# 大区口径校验
sr = get("/api/state?region=%E8%A5%BF%E5%8C%97&period=4%E5%91%A8")
print("单大区(西北): 收入=%.1f 回款率=%.1f 大区数=%d"
      % (sr["raw"]["rev"], [k for k in sr["kpis"] if k["key"] == "col"][0]["value"], len(sr["regions"])))

# 还原
restore = dict(payload)
restore["collect_rate"] = row["collect_rate"]
restore["inventory_days"] = row["inventory_days"]
restore["owner"] = row["owner"]
restore["note"] = row["note"]
post(restore)
s3 = get("/api/state?region=%E5%85%A8%E5%9B%BD&period=4%E5%91%A8")
print("RESTORED 全国回款率=%.1f 建议=%d" % ([k for k in s3["kpis"] if k["key"] == "col"][0]["value"],
                                            len(s3["suggestions"])))
print("OK")
