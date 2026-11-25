#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
农资经营系统 · 最小可用闭环（MVP）
--------------------------------
零依赖：Python 3.8+ 标准库 + SQLite。

它验证的是一条真实链路：
    周报填报（写入） -> 数据库（存储） -> 指标计算（聚合） -> 规则引擎（建议） -> 前端实时刷新

生产环境要替换的部分：ORM/连接池、鉴权、审批、任务调度、缓存、LLM 建议。
"""

import json
import os
import random
import sqlite3
import datetime
from http.server import ThreadingHTTPServer, SimpleHTTPRequestHandler
from urllib.parse import urlparse, parse_qs

BASE = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(BASE, "data.db")
WEB_DIR = os.path.join(BASE, "web")
PORT = int(os.environ.get("PORT", "8787"))

REGIONS = ["华北", "华东", "华中", "华南", "西南", "西北", "东北"]
SCALE = {"华北": 18600, "华东": 21400, "华中": 13800, "华南": 8600,
         "西南": 10200, "西北": 6200, "东北": 7200}
CATS = ["复合肥", "单质肥", "农药", "种子", "农膜", "特肥/水溶肥"]
CAT_PCT = {"复合肥": 38, "单质肥": 16, "农药": 22, "种子": 12, "农膜": 5, "特肥/水溶肥": 7}
CAT_MARGIN = {"复合肥": 17.2, "单质肥": 13.8, "农药": 26.5, "种子": 19.0, "农膜": 12.6, "特肥/水溶肥": 30.4}

WEEKS = ["2026-W%02d" % w for w in range(35, 45)]
SEASON = [0.95, 0.98, 1.02, 1.05, 1.00, 0.93, 0.88, 0.92, 1.08, 0.97]
REGION_MOD = {  # (回款率偏移, 库存天数偏移, 履约率偏移)
    "华北": (-2.5, 3, 0.4), "华东": (1.5, -4, 1.2), "华中": (0.0, 1, 0.0),
    "华南": (-4.0, 8, -1.5), "西南": (2.0, -2, 0.6), "西北": (-6.5, 11, -2.2),
    "东北": (1.0, 4, 0.3),
}


# --------------------------------------------------------------------------
# 数据库
# --------------------------------------------------------------------------
def db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    conn = db()
    conn.executescript("""
    CREATE TABLE IF NOT EXISTS weekly (
        id            INTEGER PRIMARY KEY AUTOINCREMENT,
        week          TEXT NOT NULL,
        region        TEXT NOT NULL,
        category      TEXT NOT NULL,
        revenue       REAL NOT NULL DEFAULT 0,
        target        REAL NOT NULL DEFAULT 0,
        gross_margin  REAL NOT NULL DEFAULT 0,
        collect_rate  REAL NOT NULL DEFAULT 0,
        inventory_days REAL NOT NULL DEFAULT 0,
        receivable    REAL NOT NULL DEFAULT 0,
        expiring      REAL NOT NULL DEFAULT 0,
        orders        INTEGER NOT NULL DEFAULT 0,
        fill_rate     REAL NOT NULL DEFAULT 0,
        owner         TEXT DEFAULT '',
        note          TEXT DEFAULT '',
        updated_at    TEXT DEFAULT '',
        UNIQUE(week, region, category)
    );
    CREATE TABLE IF NOT EXISTS audit (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        ts TEXT, action TEXT, payload TEXT
    );
    """)
    cur = conn.execute("SELECT COUNT(*) c FROM weekly").fetchone()
    if cur["c"] == 0:
        seed(conn)
    conn.commit()
    conn.close()


def seed(conn):
    rnd = random.Random(42)
    now = datetime.datetime.now().strftime("%Y-%m-%d %H:%M")
    rows = []
    for wi, week in enumerate(WEEKS):
        for region in REGIONS:
            for cat in CATS:
                share = CAT_PCT[cat] / 100.0
                weekly_base = SCALE[region] / 52.0 * share
                rev = weekly_base * SEASON[wi] * (1 + (rnd.random() - 0.5) * 0.14)
                tgt = weekly_base * 1.04 * (1 + (rnd.random() - 0.5) * 0.06)
                cm, cm2, cf = REGION_MOD[region]
                col = max(58, min(98, 87 + cm + (rnd.random() - 0.5) * 6 - wi * 0.35))
                inv = max(30, 62 + cm2 + (rnd.random() - 0.5) * 10)
                order = max(6, int(rev / 6 * (1 + (rnd.random() - 0.5) * 0.5)))
                fill = max(80, min(99.5, 96 + cf + (rnd.random() - 0.5) * 3))
                ar = rev * (2.0 + rnd.random() * 0.9)
                exp = rev * (0.03 + rnd.random() * 0.06)
                rows.append((week, region, cat, round(rev, 1), round(tgt, 1),
                             round(CAT_MARGIN[cat] + (rnd.random() - 0.5) * 2.4, 1),
                             round(col, 1), round(inv, 1), round(ar, 1), round(exp, 1),
                             order, round(fill, 1), "李财务", "", now))
    conn.executemany("""INSERT INTO weekly
        (week,region,category,revenue,target,gross_margin,collect_rate,inventory_days,
         receivable,expiring,orders,fill_rate,owner,note,updated_at)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""", rows)
    conn.execute("INSERT INTO audit(ts,action,payload) VALUES (?,?,?)",
                 (now, "seed", json.dumps({"rows": len(rows)}, ensure_ascii=False)))


# --------------------------------------------------------------------------
# 指标计算
# --------------------------------------------------------------------------
def all_weeks(conn):
    return [r["week"] for r in conn.execute(
        "SELECT DISTINCT week FROM weekly ORDER BY week").fetchall()]


def fetch_rows(conn, weeks, region):
    if not weeks:
        return []
    ph = ",".join("?" * len(weeks))
    sql = "SELECT * FROM weekly WHERE week IN (%s)" % ph
    args = list(weeks)
    if region and region != "全国":
        sql += " AND region=?"
        args.append(region)
    return [dict(r) for r in conn.execute(sql, args).fetchall()]


def agg(rows):
    if not rows:
        return dict(rev=0, tgt=0, mar=0, col=0, inv=0, ar=0, exp=0, orders=0,
                    fill=0, rate=0, weeks=[])
    rev = sum(r["revenue"] for r in rows)
    tgt = sum(r["target"] for r in rows)
    w = rev or 1
    mar = sum(r["revenue"] * r["gross_margin"] for r in rows) / w
    col = sum(r["revenue"] * r["collect_rate"] for r in rows) / w
    inv = sum(r["revenue"] * r["inventory_days"] for r in rows) / w
    orders = sum(r["orders"] for r in rows) or 1
    fill = sum(r["orders"] * r["fill_rate"] for r in rows) / orders
    last = max(r["week"] for r in rows)
    last_rows = [r for r in rows if r["week"] == last]
    ar = sum(r["receivable"] for r in last_rows)
    exp = sum(r["expiring"] for r in last_rows)
    return dict(rev=rev, tgt=tgt, mar=mar, col=col, inv=inv, ar=ar, exp=exp,
                orders=orders, fill=fill, rate=(rev / tgt * 100) if tgt else 0,
                weeks=sorted({r["week"] for r in rows}))


def group_by(rows, key):
    out = {}
    for r in rows:
        out.setdefault(r[key], []).append(r)
    return out


def severity_rank(s):
    return {"P0": 0, "P1": 1, "P2": 2}.get(s, 3)


def advise(cur, old, rows, region):
    """规则引擎：把指标异常翻译成"事实 + 动作 + 影响金额"。"""
    a, b = agg(cur), agg(old)
    tips = []

    if a["rate"] and a["rate"] < 95:
        gap = a["tgt"] - a["rev"]
        tips.append(dict(level="P0", module="销售",
                         title="目标缺口 %s 万元（完成 %.1f%%）" % (round(gap), a["rate"]),
                         evidence="本周期收入 %.0f 万 / 目标 %.0f 万，缺口 %.0f 万" % (a["rev"], a["tgt"], gap),
                         action="按大区拆缺口派单：先补目标完成率最低的 2 个大区",
                         impact="+%s 万" % round(gap)))

    if a["col"] and a["col"] < 88:
        risk = a["ar"] * (100 - a["col"]) / 100
        tips.append(dict(level="P0", module="资金",
                         title="回款率跌破 88%%（当前 %.1f%%）" % a["col"],
                         evidence="应收余额 %.0f 万，按当前回款率测算风险敞口 %.0f 万" % (a["ar"], risk),
                         action="超账期客户停止发货，回款率纳入业务员提成 30% 权重",
                         impact="敞口 %s 万" % round(risk)))

    if a["inv"] and a["inv"] > 60:
        tips.append(dict(level="P1", module="供应链",
                         title="库存周转 %.0f 天，高于 60 天目标" % a["inv"],
                         evidence="周转每降 10 天可释放资金约 %.0f 万" % (a["rev"] * 10 / 365),
                         action="滞销 SKU 停止采购 + 跨区调拨，优先清理临期批次",
                         impact="释放 %s 万" % round(a["rev"] * (a["inv"] - 60) / 365)))

    if a["exp"] and a["rev"]:
        days = max(7, 7 * len(a["weeks"]))
        stock_value = (a["inv"] / 365.0) * (a["rev"] * 365.0 / days) * 0.82
        ratio = a["exp"] / stock_value * 100 if stock_value else 0
        if ratio > 3:
            tips.append(dict(level="P1", module="库存",
                             title="临期库存 %.0f 万，占库存 %.1f%%" % (a["exp"], ratio),
                             evidence="库存估值约 %.0f 万，临期率超过 3%% 警戒线" % stock_value,
                             action="纳入促销池并调往高动销大区，45 天内清零",
                             impact="避免损失 %s 万" % round(a["exp"] * 0.6)))

    if b["rev"] and a["rev"] < b["rev"] * 0.93:
        drop = b["rev"] - a["rev"]
        groups = group_by(rows, "region")
        worst = min(groups.items(), key=lambda kv: sum(x["revenue"] for x in kv[1]))[0] if groups else "-"
        tips.append(dict(level="P0", module="销售",
                         title="收入环比下滑 %.1f%%" % ((1 - a["rev"] / b["rev"]) * 100),
                         evidence="上期 %.0f 万 → 本期 %.0f 万，最大拖累大区：%s" % (b["rev"], a["rev"], worst),
                         action="核查竞品价格与断货 SKU，启动阶段性价格授权",
                         impact="挽损 %s 万" % round(drop)))

    groups = group_by(cur, "region")
    if region == "全国" and len(groups) > 1:
        worst = min(groups.items(), key=lambda kv: agg(kv[1])["col"])
        wag = agg(worst[1])
        if wag["col"] < 82:
            tips.append(dict(level="P1", module="资金",
                             title="%s 回款率仅 %.1f%%，低于全国均值" % (worst[0], wag["col"]),
                             evidence="该大区应收 %.0f 万，占全国 %.0f%%" % (wag["ar"], wag["ar"] / max(1, a["ar"]) * 100),
                             action="派驻催收 + 下调该大区信用额度 15%",
                             impact="风险 %s 万" % round(wag["ar"] * (88 - wag["col"]) / 100)))

    groups = group_by(cur, "category")
    if groups:
        ranked = sorted(groups.items(),
                        key=lambda kv: (sum(x["revenue"] * x["gross_margin"] for x in kv[1]) /
                                        max(1, sum(x["revenue"] for x in kv[1]))), reverse=True)
        best = ranked[0]
        best_mar = sum(x["revenue"] * x["gross_margin"] for x in best[1]) / max(1, sum(x["revenue"] for x in best[1]))
        if best_mar > a["mar"] + 4:
            tips.append(dict(level="P2", module="结构",
                             title="品类结构可优化：%s 毛利 %.1f%%，高出整体 %.1f pt" % (best[0], best_mar, best_mar - a["mar"]),
                             evidence="高毛利品类占比 %.1f%%，每提升 1pt 可增毛利约 %.0f 万" % (
                                 sum(x["revenue"] for x in best[1]) / max(1, a["rev"]) * 100, a["rev"] * 0.01 * best_mar / 100),
                             action="把该品类纳入大区必推清单，配套技术服务与示范田",
                             impact="+%s 万/期" % round(a["rev"] * 0.01 * best_mar / 100)))

    tips.sort(key=lambda t: severity_rank(t["level"]))
    return tips[:6]


def build_state(region, period):
    conn = db()
    weeks = all_weeks(conn)
    if not weeks:
        conn.close()
        return {"empty": True}

    n = {"周": 1, "4周": 4, "年": len(weeks)}.get(period, 4)
    sel = weeks[-n:]
    prev = weeks[max(0, len(weeks) - 2 * n):len(weeks) - n]

    cur = fetch_rows(conn, sel, region)
    old = fetch_rows(conn, prev, region)
    a, b = agg(cur), agg(old)

    # 序列：最近 12 周
    span = weeks[-12:]
    span_rows = fetch_rows(conn, span, region)
    by_week = group_by(span_rows, "week")
    series = []
    for w in span:
        rs = by_week.get(w, [])
        ag = agg(rs)
        series.append(dict(week=w, revenue=round(ag["rev"], 1), target=round(ag["tgt"], 1),
                           margin=round(ag["mar"], 1)))

    # 品类 / 大区
    cat_rows = group_by(cur, "category")
    categories = []
    for name, rs in cat_rows.items():
        ag = agg(rs)
        categories.append(dict(name=name, revenue=round(ag["rev"], 1),
                               margin=round(ag["mar"], 1),
                               share=round(ag["rev"] / a["rev"] * 100, 1) if a["rev"] else 0))
    categories.sort(key=lambda x: -x["revenue"])

    reg_rows = group_by(cur, "region")
    regions = []
    for name, rs in reg_rows.items():
        ag = agg(rs)
        regions.append(dict(name=name, revenue=round(ag["rev"], 1),
                            target=round(ag["tgt"], 1), rate=round(ag["rate"], 1),
                            collect=round(ag["col"], 1)))
    regions.sort(key=lambda x: -x["rate"])

    def delta(key, invert=False):
        if not b["rev"] or not a["rev"]:
            return 0.0
        d = (a[key] - b[key])
        return round(d, 1)

    kpis = [
        dict(key="rev", label="销售收入", value=round(a["rev"], 1), unit="万元",
             delta=round((a["rev"] / b["rev"] - 1) * 100, 1) if b["rev"] else 0, good=True,
             note="目标 %.0f 万" % a["tgt"]),
        dict(key="mar", label="综合毛利率", value=round(a["mar"], 1), unit="%",
             delta=round(a["mar"] - b["mar"], 1), good=a["mar"] >= 18,
             note="目标 19.0%"),
        dict(key="col", label="回款率", value=round(a["col"], 1), unit="%",
             delta=round(a["col"] - b["col"], 1), good=a["col"] >= 88,
             note="红线 88%"),
        dict(key="inv", label="库存周转", value=round(a["inv"], 1), unit="天",
             delta=round(a["inv"] - b["inv"], 1), good=a["inv"] <= 60,
             note="目标 ≤ 60 天"),
        dict(key="rate", label="目标达成率", value=round(a["rate"], 1), unit="%",
             delta=round(a["rate"] - b["rate"], 1), good=a["rate"] >= 100,
             note="累计 %.0f / %.0f 万" % (a["rev"], a["tgt"])),
        dict(key="fill", label="订单履约率", value=round(a["fill"], 1), unit="%",
             delta=round(a["fill"] - b["fill"], 1), good=a["fill"] >= 96,
             note="本期订单 %d 单" % a["orders"]),
    ]

    tips = advise(cur, old, cur, region)

    recent = [dict(r) for r in conn.execute(
        "SELECT id,week,region,category,revenue,target,gross_margin,collect_rate,"
        "inventory_days,receivable,expiring,orders,fill_rate,owner,note,updated_at "
        "FROM weekly ORDER BY week DESC, id DESC LIMIT 60").fetchall()]

    meta = dict(regions=REGIONS, categories=CATS, weeks=weeks, period=period, region=region)
    conn.close()

    return dict(empty=False, region=region, period=period,
                period_label={"周": "本周", "4周": "近 4 周", "年": "全周期"}.get(period, period),
                range=[sel[0], sel[-1]] if sel else [],
                updated_at=recent[0]["updated_at"] if recent else "",
                kpis=kpis, series=series, categories=categories, regions=regions,
                suggestions=tips, recent=recent, meta=meta,
                raw=dict(rev=round(a["rev"], 1), ar=round(a["ar"], 1), exp=round(a["exp"], 1)))


# --------------------------------------------------------------------------
# HTTP
# --------------------------------------------------------------------------
class Handler(SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=WEB_DIR, **kwargs)

    def log_message(self, fmt, *args):
        print("[%s] %s" % (datetime.datetime.now().strftime("%H:%M:%S"), fmt % args))

    def _json(self, obj, code=200):
        body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _body(self):
        n = int(self.headers.get("Content-Length") or 0)
        if not n:
            return {}
        return json.loads(self.rfile.read(n).decode("utf-8"))

    def do_GET(self):
        u = urlparse(self.path)
        if u.path == "/api/state":
            qs = parse_qs(u.query)
            region = (qs.get("region") or ["全国"])[0]
            period = (qs.get("period") or ["4周"])[0]
            return self._json(build_state(region, period))
        if u.path == "/api/meta":
            conn = db()
            weeks = all_weeks(conn)
            conn.close()
            return self._json(dict(regions=REGIONS, categories=CATS, weeks=weeks))
        if u.path == "/api/health":
            return self._json(dict(ok=True, db=os.path.exists(DB_PATH)))
        return super().do_GET()

    def do_POST(self):
        u = urlparse(self.path)
        if u.path != "/api/weekly":
            return self._json({"error": "not found"}, 404)
        try:
            d = self._body()
            need = ["week", "region", "category"]
            for k in need:
                if not d.get(k):
                    return self._json({"error": "缺少字段 %s" % k}, 400)
            if d["region"] not in REGIONS or d["category"] not in CATS:
                return self._json({"error": "大区或品类不合法"}, 400)
            if d["week"] not in WEEKS:
                return self._json({"error": "周次不合法"}, 400)

            num = lambda k, dv=0: float(d.get(k) if d.get(k) not in (None, "") else dv)
            now = datetime.datetime.now().strftime("%Y-%m-%d %H:%M")
            conn = db()
            conn.execute("""
              INSERT INTO weekly (week,region,category,revenue,target,gross_margin,collect_rate,
                 inventory_days,receivable,expiring,orders,fill_rate,owner,note,updated_at)
              VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
              ON CONFLICT(week,region,category) DO UPDATE SET
                 revenue=excluded.revenue, target=excluded.target,
                 gross_margin=excluded.gross_margin, collect_rate=excluded.collect_rate,
                 inventory_days=excluded.inventory_days, receivable=excluded.receivable,
                 expiring=excluded.expiring, orders=excluded.orders,
                 fill_rate=excluded.fill_rate, owner=excluded.owner,
                 note=excluded.note, updated_at=excluded.updated_at
            """, (d["week"], d["region"], d["category"], num("revenue"), num("target"),
                  num("gross_margin"), num("collect_rate"), num("inventory_days"),
                  num("receivable"), num("expiring"), int(num("orders")), num("fill_rate"),
                  d.get("owner", ""), d.get("note", ""), now))
            conn.execute("INSERT INTO audit(ts,action,payload) VALUES (?,?,?)",
                         (now, "upsert", json.dumps(d, ensure_ascii=False)))
            conn.commit()
            conn.close()
            return self._json(dict(ok=True, updated_at=now))
        except Exception as e:
            return self._json({"error": str(e)}, 400)

    def do_DELETE(self):
        u = urlparse(self.path)
        if not u.path.startswith("/api/weekly/"):
            return self._json({"error": "not found"}, 404)
        try:
            rid = int(u.path.rsplit("/", 1)[-1])
            conn = db()
            conn.execute("DELETE FROM weekly WHERE id=?", (rid,))
            conn.execute("INSERT INTO audit(ts,action,payload) VALUES (?,?,?)",
                         (datetime.datetime.now().strftime("%Y-%m-%d %H:%M"), "delete",
                          json.dumps({"id": rid})))
            conn.commit()
            conn.close()
            return self._json(dict(ok=True))
        except Exception as e:
            return self._json({"error": str(e)}, 400)


def main():
    init_db()
    srv = ThreadingHTTPServer(("127.0.0.1", PORT), Handler)
    print("农资经营系统 MVP 已启动： http://127.0.0.1:%d" % PORT)
    print("数据库： %s" % DB_PATH)
    print("Ctrl+C 停止")
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        print("\n已停止")


if __name__ == "__main__":
    main()
