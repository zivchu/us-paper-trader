#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
US-1PCT-PAPER —— 美股 1%/日 目标模拟盘引擎（实验性质，非真实资金）

用法:
  python paper.py init
  python paper.py status [--prices "{\"TQQQ\":95.1}"]
  python paper.py buy  TQQQ 3.5 95.10 --reason "突破20日新高"
  python paper.py sell TQQQ 3.5 96.00 --reason "止盈"
  python paper.py mark --prices "{\"TQQQ\":95.1}" [--date 2026-09-17] [--diagnosis "市场regime"]
  python paper.py adjust --change "止损由-6%改为-5%" --reason "近5日止损触发率过高"
  python paper.py report --type decision --context ctx_decision.json
  python paper.py report --type pnl      --context ctx_pnl.json
"""
import argparse
import csv
import json
import sys
import datetime
from pathlib import Path
from string import Template

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

BASE = Path(__file__).resolve().parent
CFG_P = BASE / "config.json"
STATE_P = BASE / "state.json"
TRADES_P = BASE / "trades.csv"
HIST_P = BASE / "history.csv"
STRATLOG_P = BASE / "strategy_log.json"
REPORTS_D = BASE / "reports"

HIST_HEADER = ["date", "cash_usd", "positions_value_usd", "equity_usd", "equity_cny",
               "daily_ret_pct", "cum_ret_pct", "target_hit", "diagnosis_category"]
TRADES_HEADER = ["datetime", "action", "symbol", "qty", "price", "amount_usd", "reason"]


# ---------- 基础工具 ----------

def load_json(p, default=None):
    if p.exists():
        return json.loads(p.read_text(encoding="utf-8"))
    return default


def save_json(p, obj):
    p.write_text(json.dumps(obj, ensure_ascii=False, indent=2), encoding="utf-8")


def now_str():
    return datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def today():
    return datetime.date.today().isoformat()


def r2(x):
    return round(float(x) + 1e-12, 2)


def r4(x):
    return round(float(x), 4)


def read_csv_rows(p):
    if not p.exists():
        return []
    with p.open("r", encoding="utf-8", newline="") as f:
        return list(csv.DictReader(f))


def append_csv_row(p, header, row):
    new = not p.exists()
    with p.open("a", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=header)
        if new:
            w.writeheader()
        w.writerow(row)


def positions_value(state, prices):
    total = 0.0
    for sym, pos in state["positions"].items():
        px = prices.get(sym)
        if px is None:
            px = pos["avg_cost"]
        total += pos["qty"] * float(px)
    return total


# ---------- 命令 ----------

def cmd_init(cfg):
    if STATE_P.exists():
        print("[init] state.json 已存在，跳过初始化")
        return
    capital_usd = r2(cfg["capital_cny"] / cfg["usd_cny"])
    state = {
        "cash_usd": capital_usd,
        "positions": {},
        "realized_pnl_usd": 0.0,
        "peak_equity_usd": capital_usd,
        "strategy_version": cfg["strategy"]["version"],
        "kill_switch": False,
        "created_at": now_str(),
    }
    save_json(STATE_P, state)
    if not STRATLOG_P.exists():
        save_json(STRATLOG_P, [{
            "version": cfg["strategy"]["version"], "date": today(),
            "change": "初始策略 momentum_breakout v1",
            "reason": "动量突破入场 + 固定止损/止盈/时间止损 + 集中度限制，目标日收益 +1%（实验性高目标）",
        }])
    append_csv_row(HIST_P, HIST_HEADER, {
        "date": today(), "cash_usd": capital_usd, "positions_value_usd": 0,
        "equity_usd": capital_usd, "equity_cny": r2(capital_usd * cfg["usd_cny"]),
        "daily_ret_pct": 0, "cum_ret_pct": 0, "target_hit": 0, "diagnosis_category": "INIT",
    })
    print("[init] 初始资金 $%.2f（¥%.2f @ %.4f）" % (capital_usd, cfg["capital_cny"], cfg["usd_cny"]))


def cmd_status(cfg, prices=None):
    state = load_json(STATE_P)
    pv = positions_value(state, prices or {})
    eq = state["cash_usd"] + pv
    out = {
        "cash_usd": r2(state["cash_usd"]),
        "positions": state["positions"],
        "positions_value_usd": r2(pv),
        "equity_usd": r2(eq),
        "equity_cny": r2(eq * cfg["usd_cny"]),
        "realized_pnl_usd": r2(state["realized_pnl_usd"]),
        "peak_equity_usd": r2(state["peak_equity_usd"]),
        "kill_switch": state.get("kill_switch", False),
        "strategy_version": state["strategy_version"],
    }
    print(json.dumps(out, ensure_ascii=False, indent=2))


def cmd_buy(cfg, symbol, qty, price, reason):
    state = load_json(STATE_P)
    if state.get("kill_switch"):
        print("[buy] 拒绝：kill-switch 已触发（累计回撤超限），如需强制交易请人工确认")
        return 2
    slip = cfg["trading"]["slippage_pct"] / 100.0
    px = float(price) * (1 + slip)
    qty = round(float(qty), 6)
    amount = qty * px
    if amount > state["cash_usd"] + 1e-9:
        print("[buy] 拒绝：现金不足（需 $%.2f，仅有 $%.2f）" % (amount, state["cash_usd"]))
        return 2
    pos = state["positions"].get(symbol, {"qty": 0.0, "avg_cost": 0.0})
    new_qty = pos["qty"] + qty
    new_avg = (pos["qty"] * pos["avg_cost"] + qty * px) / new_qty
    state["positions"][symbol] = {"qty": round(new_qty, 6), "avg_cost": round(new_avg, 4)}
    state["cash_usd"] = r2(state["cash_usd"] - amount)
    save_json(STATE_P, state)
    append_csv_row(TRADES_P, TRADES_HEADER, {
        "datetime": now_str(), "action": "BUY", "symbol": symbol,
        "qty": qty, "price": round(px, 4), "amount_usd": r2(amount), "reason": reason,
    })
    print("[buy] %s x%.4f @ $%.4f（含滑点 %.2f%%）= $%.2f | 剩余现金 $%.2f"
          % (symbol, qty, px, cfg["trading"]["slippage_pct"], amount, state["cash_usd"]))
    return 0


def cmd_sell(cfg, symbol, qty, price, reason):
    state = load_json(STATE_P)
    pos = state["positions"].get(symbol)
    if not pos:
        print("[sell] 拒绝：无 %s 持仓" % symbol)
        return 2
    slip = cfg["trading"]["slippage_pct"] / 100.0
    px = float(price) * (1 - slip)
    qty = round(min(float(qty), pos["qty"]), 6)
    amount = qty * px
    state["realized_pnl_usd"] = r2(state["realized_pnl_usd"] + (px - pos["avg_cost"]) * qty)
    left = round(pos["qty"] - qty, 6)
    if left <= 1e-6:
        del state["positions"][symbol]
    else:
        state["positions"][symbol] = {"qty": left, "avg_cost": pos["avg_cost"]}
    state["cash_usd"] = r2(state["cash_usd"] + amount)
    save_json(STATE_P, state)
    append_csv_row(TRADES_P, TRADES_HEADER, {
        "datetime": now_str(), "action": "SELL", "symbol": symbol,
        "qty": qty, "price": round(px, 4), "amount_usd": r2(amount), "reason": reason,
    })
    print("[sell] %s x%.4f @ $%.4f = $%.2f | 现金 $%.2f" % (symbol, qty, px, amount, state["cash_usd"]))
    return 0


def cmd_mark(cfg, prices, date=None, diagnosis=""):
    state = load_json(STATE_P)
    date = date or today()
    missing = [s for s in state["positions"] if s not in prices]
    if missing:
        print("[mark] 错误：缺少持仓报价 %s" % ",".join(missing))
        return 2
    pv = positions_value(state, prices)
    eq = r2(state["cash_usd"] + pv)
    rows = read_csv_rows(HIST_P)
    by_date = {r["date"]: r for r in rows}
    prev_dates = sorted(d for d in by_date if d < date)
    prev_eq = float(by_date[prev_dates[-1]]["equity_usd"]) if prev_dates else eq
    first_eq = float(rows[0]["equity_usd"]) if rows else eq
    daily_ret = (eq / prev_eq - 1) * 100 if prev_eq else 0.0
    cum_ret = (eq / first_eq - 1) * 100 if first_eq else 0.0
    row = {
        "date": date, "cash_usd": r2(state["cash_usd"]), "positions_value_usd": r2(pv),
        "equity_usd": eq, "equity_cny": r2(eq * cfg["usd_cny"]),
        "daily_ret_pct": round(daily_ret, 3), "cum_ret_pct": round(cum_ret, 3),
        "target_hit": 1 if daily_ret >= cfg["target_daily_pct"] else 0,
        "diagnosis_category": diagnosis or "-",
    }
    by_date[date] = row
    with HIST_P.open("w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=HIST_HEADER)
        w.writeheader()
        for d in sorted(by_date):
            w.writerow(by_date[d])
    if eq > state["peak_equity_usd"]:
        state["peak_equity_usd"] = eq
    dd = (eq / state["peak_equity_usd"] - 1) * 100
    if dd <= -cfg["risk"]["max_drawdown_pct"] and not state.get("kill_switch"):
        state["kill_switch"] = True
        print("[mark] ⚠️ KILL-SWITCH 触发：自峰值回撤 %.2f%% 超过 %.0f%% 红线，建议清仓观望"
              % (dd, cfg["risk"]["max_drawdown_pct"]))
    save_json(STATE_P, state)
    print("[mark] %s 净值 $%.2f（日 %+.2f%% / 累计 %+.2f%% / 目标 %.1f%%：%s）"
          % (date, eq, daily_ret, cum_ret, cfg["target_daily_pct"],
             "达成" if row["target_hit"] else "未达成"))
    return 0


def cmd_adjust(change, reason):
    state = load_json(STATE_P)
    log = load_json(STRATLOG_P, [])
    new_ver = state["strategy_version"] + 1
    state["strategy_version"] = new_ver
    save_json(STATE_P, state)
    log.append({"version": new_ver, "date": today(), "change": change, "reason": reason})
    save_json(STRATLOG_P, log)
    print("[adjust] 策略版本 v%d -> v%d：%s" % (new_ver - 1, new_ver, change))


# ---------- HTML 报告 ----------

CSS = """
* { box-sizing: border-box; margin: 0; padding: 0; }
body { background:#0d1117; color:#e6edf3; font-family:"Segoe UI","Microsoft YaHei",sans-serif; padding:28px; line-height:1.65; }
.wrap { max-width:920px; margin:0 auto; }
h1 { font-size:22px; margin-bottom:4px; }
h2 { font-size:16px; color:#8b949e; border-bottom:1px solid #21262d; padding-bottom:6px; margin:26px 0 12px; }
.meta { color:#8b949e; font-size:12.5px; margin-bottom:18px; }
.cards { display:flex; flex-wrap:wrap; gap:12px; }
.card { background:#161b22; border:1px solid #21262d; border-radius:10px; padding:14px 18px; flex:1 1 150px; }
.card .k { color:#8b949e; font-size:12px; }
.card .v { font-size:20px; font-weight:600; margin-top:2px; }
.card .s { font-size:12px; color:#8b949e; }
.up { color:#f0506e; } .down { color:#3fb68b; } .flat { color:#8b949e; }
table { width:100%; border-collapse:collapse; font-size:13px; background:#161b22; border-radius:10px; overflow:hidden; }
th { background:#1c2128; color:#8b949e; text-align:left; padding:9px 12px; font-weight:500; }
td { padding:9px 12px; border-top:1px solid #21262d; vertical-align:top; }
.tag { display:inline-block; padding:1px 8px; border-radius:10px; font-size:11.5px; font-weight:600; }
.tag.buy { background:rgba(240,80,110,.15); color:#f0506e; }
.tag.sell { background:rgba(63,182,139,.15); color:#3fb68b; }
.tag.hold { background:rgba(139,148,158,.15); color:#8b949e; }
.box { background:#161b22; border:1px solid #21262d; border-radius:10px; padding:14px 18px; font-size:13.5px; white-space:pre-wrap; }
.warn { border-color:rgba(240,80,110,.4); }
.foot { margin-top:30px; color:#6e7681; font-size:12px; border-top:1px solid #21262d; padding-top:14px; }
svg text { font-family:"Segoe UI","Microsoft YaHei",sans-serif; }
"""

PAGE = Template("""<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>$title</title><style>$css</style></head>
<body><div class="wrap">$body</div></body></html>""")


def cls(x):
    return "up" if x > 0 else ("down" if x < 0 else "flat")


def sign(x, pct=False):
    s = ("%+.2f" % x)
    return s + ("%" if pct else "")


def curve_svg(rows, initial):
    """rows: list of dict from history.csv"""
    if len(rows) < 2:
        return '<div class="box">净值数据不足，曲线将在第 2 个记账日后生成。</div>'
    W, H, pad = 880, 200, 34
    eqs = [float(r["equity_usd"]) for r in rows]
    dates = [r["date"][5:] for r in rows]
    lo, hi = min(eqs + [initial]), max(eqs + [initial])
    span = (hi - lo) or 1.0
    hi, lo = hi + span * 0.08, lo - span * 0.08
    span = hi - lo

    def X(i):
        return pad + i * (W - 2 * pad) / (len(eqs) - 1)

    def Y(v):
        return H - pad - (v - lo) / span * (H - 2 * pad)

    pts = " ".join("%.1f,%.1f" % (X(i), Y(v)) for i, v in enumerate(eqs))
    y0 = Y(initial)
    color = "#f0506e" if eqs[-1] >= initial else "#3fb68b"
    parts = ['<svg viewBox="0 0 %d %d" width="100%%" style="background:#161b22;border:1px solid #21262d;border-radius:10px">' % (W, H)]
    for i in range(5):
        gv = lo + span * i / 4
        gy = Y(gv)
        parts.append('<line x1="%d" y1="%.1f" x2="%d" y2="%.1f" stroke="#21262d" stroke-width="1"/>' % (pad, gy, W - pad, gy))
        parts.append('<text x="4" y="%.1f" fill="#6e7681" font-size="10">%.0f</text>' % (gy + 3, gv))
    parts.append('<line x1="%d" y1="%.1f" x2="%d" y2="%.1f" stroke="#8b949e" stroke-dasharray="5,4" stroke-width="1"/>' % (pad, y0, W - pad, y0))
    parts.append('<text x="%d" y="%.1f" fill="#8b949e" font-size="10">本金 $%.0f</text>' % (W - pad - 90, y0 - 5, initial))
    parts.append('<polyline points="%s" fill="none" stroke="%s" stroke-width="2.2"/>' % (pts, color))
    parts.append('<circle cx="%.1f" cy="%.1f" r="3.5" fill="%s"/>' % (X(len(eqs) - 1), Y(eqs[-1]), color))
    parts.append('<text x="%d" y="%d" fill="#6e7681" font-size="10">%s</text>' % (pad, H - 8, dates[0]))
    parts.append('<text x="%d" y="%d" fill="#6e7681" font-size="10" text-anchor="end">%s</text>' % (W - pad, H - 8, dates[-1]))
    parts.append('</svg>')
    return "".join(parts)


def holdings_table(state, prices):
    rows = []
    for sym, pos in state["positions"].items():
        px = prices.get(sym)
        unr = (px - pos["avg_cost"]) * pos["qty"] if px else None
        unr_pct = (px / pos["avg_cost"] - 1) * 100 if px else None
        rows.append("<tr><td><b>%s</b></td><td>%.4f</td><td>$%.4f</td><td>%s</td><td class='%s'>%s</td></tr>" % (
            sym, pos["qty"], pos["avg_cost"],
            ("$%.2f" % px) if px else "无报价",
            cls(unr or 0),
            ("%s（%s）" % (sign(unr), sign(unr_pct, True))) if unr is not None else "—",
        ))
    if not rows:
        return '<div class="box">当前无持仓（空仓/全现金）。</div>'
    return ("<table><tr><th>标的</th><th>数量</th><th>成本价</th><th>现价</th><th>浮动盈亏</th></tr>"
            + "".join(rows) + "</table>")


def audit_table(audit):
    if not audit:
        return '<div class="box">本时段无交易用报价记录。</div>'
    rows = []
    for a in audit:
        src = " / ".join("%s $%.2f" % (k, v) for k, v in a.get("sources", {}).items())
        verdict = a.get("verdict", "OK")
        color = "#3fb68b" if verdict == "OK" else "#f0a050"
        rows.append("<tr><td><b>%s</b></td><td>%s</td><td>%.2f%%</td><td>$%.2f</td><td style='color:%s'>%s</td></tr>"
                    % (a["symbol"], src, a.get("deviation_pct", 0), a.get("used", 0), color, verdict))
    return ("<table><tr><th>标的</th><th>报价来源（交叉验证）</th><th>偏差</th><th>采用价</th><th>结论</th></tr>"
            + "".join(rows) + "</table>")


def render_decision(cfg, state, ctx):
    date = ctx.get("date", today())
    prices = ctx.get("prices", {})
    pv = positions_value(state, prices)
    eq = state["cash_usd"] + pv
    hist = read_csv_rows(HIST_P)
    cum = (eq / float(hist[0]["equity_usd"]) - 1) * 100 if hist else 0.0
    dec_rows = []
    for d in ctx.get("decisions", []):
        tag = {"BUY": "buy", "SELL": "sell"}.get(d["action"], "hold")
        dec_rows.append(
            "<tr><td><span class='tag %s'>%s</span></td><td><b>%s</b></td><td>%s</td><td>%s</td><td>%s</td><td>%s</td></tr>"
            % (tag, d["action"], d["symbol"],
               ("%.4f" % d["qty"]) if d.get("qty") else "—",
               ("$%.2f" % d["price"]) if d.get("price") else "—",
               ("$%.2f" % (d["qty"] * d["price"])) if d.get("qty") and d.get("price") else "—",
               d.get("reason", "")))
    dec_table = (("<table><tr><th>操作</th><th>标的</th><th>数量</th><th>价格</th><th>金额</th><th>底层逻辑</th></tr>"
                  + "".join(dec_rows) + "</table>") if dec_rows else
                 '<div class="box">今日无调仓操作（持有观望）。</div>')
    s = cfg["strategy"]
    e = s["exit_rules"]
    p = s["position_sizing"]
    body = []
    body.append("<h1>📈 美股模拟盘 · 盘中决策报告</h1>")
    body.append('<div class="meta">日期 %s ｜ 策略 %s v%d ｜ 目标日收益 +%.1f%% ｜ 模拟盘实验，非真实资金</div>'
                % (date, s["name"], state["strategy_version"], cfg["target_daily_pct"]))
    body.append('<div class="cards">'
                '<div class="card"><div class="k">账户净值</div><div class="v">$%s</div><div class="s">≈ ¥%s</div></div>'
                '<div class="card"><div class="k">现金</div><div class="v">$%s</div></div>'
                '<div class="card"><div class="k">持仓市值</div><div class="v">$%s</div></div>'
                '<div class="card"><div class="k">累计收益</div><div class="v %s">%s</div></div>'
                "</div>" % (format(eq, ",.2f"), format(eq * cfg["usd_cny"], ",.2f"),
                            format(state["cash_usd"], ",.2f"), format(pv, ",.2f"),
                            cls(cum), sign(cum, True)))
    body.append("<h2>🌐 市场环境判断</h2><div class='box'>%s\n\n%s</div>"
                % (ctx.get("market_regime", "—"), ctx.get("regime_detail", "")))
    body.append("<h2>🎯 今日决策（含底层逻辑）</h2>" + dec_table)
    body.append("<h2>💼 当前持仓</h2>" + holdings_table(state, prices))
    body.append("<h2>🔍 数据审计（报价交叉验证）</h2>" + audit_table(ctx.get("data_audit", [])))
    body.append("<h2>🛡️ 风控规则</h2><div class='box'>止损 %+.1f%% ｜ 止盈 %+.1f%% ｜ 时间止损 %d 天 ｜ 最高点回撤 %.1f%% 移动止盈\n"
                "单仓上限 %.0f%% ｜ 最多 %d 仓 ｜ 现金缓冲 %.0f%% ｜ 累计回撤 >%.0f%% 触发清仓红线\n"
                "过滤器：%s</div>"
                % (e["stop_loss_pct"], e["take_profit_pct"], e["time_stop_days"], e["trail_from_high_pct"],
                   p["max_position_pct"], p["max_positions"], p["cash_buffer_pct"],
                   cfg["risk"]["max_drawdown_pct"], "；".join(s["filters"])))
    if ctx.get("strategy_note"):
        body.append("<h2>🧠 策略说明</h2><div class='box'>%s</div>" % ctx["strategy_note"])
    if ctx.get("risks"):
        body.append("<h2>⚠️ 风险提示</h2><div class='box warn'>%s</div>" % ctx["risks"])
    body.append('<div class="foot">数据截止：%s ｜ 报告由多 Agent 协作生成，报价经 ≥2 独立来源交叉验证。<br>'
                '⚠️ 本报告为模拟盘实验记录，仅供学习研究，不构成任何投资建议。日收益 +1%% 为极激进的实验性目标（年化复利逾 10 倍），长期不可持续，请理性看待。</div>'
                % now_str())
    return PAGE.substitute(title="决策报告 %s" % date, css=CSS, body="\n".join(body))


def render_pnl(cfg, state, ctx):
    date = ctx.get("date", today())
    prices = ctx.get("prices", {})
    hist = read_csv_rows(HIST_P)
    row = next((r for r in hist if r["date"] == date), hist[-1] if hist else None)
    eq = float(row["equity_usd"]) if row else state["cash_usd"]
    daily = float(row["daily_ret_pct"]) if row else 0.0
    cum = float(row["cum_ret_pct"]) if row else 0.0
    hit = row and row.get("target_hit") == "1"
    gap = cfg["target_daily_pct"] - daily
    initial = float(hist[0]["equity_usd"]) if hist else eq
    trades = [t for t in read_csv_rows(TRADES_P) if t["datetime"].startswith(date)]
    t_rows = []
    for t in trades:
        tag = "buy" if t["action"] == "BUY" else "sell"
        t_rows.append("<tr><td>%s</td><td><span class='tag %s'>%s</span></td><td><b>%s</b></td><td>%s</td><td>$%s</td><td>$%s</td><td>%s</td></tr>"
                      % (t["datetime"][11:], tag, t["action"], t["symbol"], t["qty"], t["price"], t["amount_usd"], t["reason"]))
    t_table = (("<table><tr><th>时间</th><th>操作</th><th>标的</th><th>数量</th><th>成交价</th><th>金额</th><th>理由</th></tr>"
                + "".join(t_rows) + "</table>") if t_rows else
               '<div class="box">当日无成交。</div>')
    log = load_json(STRATLOG_P, [])
    log_rows = "".join("<tr><td>v%d</td><td>%s</td><td>%s</td><td>%s</td></tr>"
                       % (l["version"], l["date"], l["change"], l["reason"]) for l in log)
    body = []
    body.append("<h1>📊 美股模拟盘 · 收盘收益报告</h1>")
    body.append('<div class="meta">日期 %s ｜ 策略 v%d ｜ 目标日收益 +%.1f%% ｜ 模拟盘实验，非真实资金</div>'
                % (date, state["strategy_version"], cfg["target_daily_pct"]))
    body.append('<div class="cards">'
                '<div class="card"><div class="k">账户净值</div><div class="v">$%s</div><div class="s">≈ ¥%s</div></div>'
                '<div class="card"><div class="k">当日收益</div><div class="v %s">%s</div><div class="s">%s</div></div>'
                '<div class="card"><div class="k">累计收益</div><div class="v %s">%s</div></div>'
                '<div class="card"><div class="k">目标达成</div><div class="v %s">%s</div><div class="s">%s</div></div>'
                "</div>"
                % (format(eq, ",.2f"), format(eq * cfg["usd_cny"], ",.2f"),
                   cls(daily), sign(daily, True), "跑赢目标" if daily >= cfg["target_daily_pct"] else "距目标还差 %.2fpct" % gap,
                   cls(cum), sign(cum, True),
                   "up" if hit else "down", "✅ 达成" if hit else "❌ 未达成",
                   row.get("diagnosis_category", "-") if row else "-"))
    body.append("<h2>📉 净值曲线</h2>" + curve_svg(hist, initial))
    body.append("<h2>🧾 当日成交</h2>" + t_table)
    body.append("<h2>💼 收盘持仓</h2>" + holdings_table(state, prices))
    body.append("<h2>🌐 市场回顾</h2><div class='box'>%s</div>" % ctx.get("market_recap", "—"))
    body.append("<h2>🔬 收益归因（%s）</h2><div class='box'>%s</div>"
                % (ctx.get("diagnosis_category", "未分类"), ctx.get("diagnosis", "—")))
    if ctx.get("backtest_note"):
        body.append("<h2>🧪 策略回测与对比</h2><div class='box'>%s</div>" % ctx["backtest_note"])
    body.append("<h2>🧬 策略迭代记录</h2><table><tr><th>版本</th><th>日期</th><th>变更</th><th>原因</th></tr>%s</table>" % log_rows)
    if ctx.get("strategy_changes"):
        body.append("<h2>🔧 今日策略调整结论</h2><div class='box'>%s</div>" % ctx["strategy_changes"])
    body.append("<h2>🔍 数据审计</h2>" + audit_table(ctx.get("data_audit", [])))
    body.append('<div class="foot">数据截止：%s ｜ 报价经 ≥2 独立来源交叉验证。<br>'
                '⚠️ 本报告为模拟盘实验记录，仅供学习研究，不构成任何投资建议。</div>' % now_str())
    return PAGE.substitute(title="收益报告 %s" % date, css=CSS, body="\n".join(body))


def cmd_report(cfg, rtype, ctx_path, out=None):
    ctx_file = Path(ctx_path)
    if not ctx_file.is_absolute():
        ctx_file = BASE / ctx_path
    ctx = load_json(ctx_file, {})
    state = load_json(STATE_P)
    date = ctx.get("date", today())
    html = render_decision(cfg, state, ctx) if rtype == "decision" else render_pnl(cfg, state, ctx)
    REPORTS_D.mkdir(exist_ok=True)
    out_p = Path(out) if out else REPORTS_D / ("%s_%s.html" % (date, rtype))
    out_p.write_text(html, encoding="utf-8")
    print("[report] 已生成 %s" % out_p)


# ---------- 入口 ----------

def main():
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("init")
    sp = sub.add_parser("status"); sp.add_argument("--prices", default="{}")
    sp = sub.add_parser("buy"); sp.add_argument("symbol"); sp.add_argument("qty", type=float); sp.add_argument("price", type=float); sp.add_argument("--reason", default="")
    sp = sub.add_parser("sell"); sp.add_argument("symbol"); sp.add_argument("qty", type=float); sp.add_argument("price", type=float); sp.add_argument("--reason", default="")
    sp = sub.add_parser("mark"); sp.add_argument("--prices", required=True); sp.add_argument("--date", default=None); sp.add_argument("--diagnosis", default="")
    sp = sub.add_parser("adjust"); sp.add_argument("--change", required=True); sp.add_argument("--reason", required=True)
    sp = sub.add_parser("report"); sp.add_argument("--type", choices=["decision", "pnl"], required=True); sp.add_argument("--context", required=True); sp.add_argument("--out", default=None)
    args = ap.parse_args()
    cfg = load_json(CFG_P)
    if args.cmd == "init":
        cmd_init(cfg)
    elif args.cmd == "status":
        cmd_status(cfg, json.loads(args.prices))
    elif args.cmd == "buy":
        return cmd_buy(cfg, args.symbol.upper(), args.qty, args.price, args.reason)
    elif args.cmd == "sell":
        return cmd_sell(cfg, args.symbol.upper(), args.qty, args.price, args.reason)
    elif args.cmd == "mark":
        return cmd_mark(cfg, json.loads(args.prices), args.date, args.diagnosis)
    elif args.cmd == "adjust":
        cmd_adjust(args.change, args.reason)
    elif args.cmd == "report":
        cmd_report(cfg, args.type, args.context, args.out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
