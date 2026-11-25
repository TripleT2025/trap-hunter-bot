# -*- coding: utf-8 -*-
"""สมุดจดผลจริง (Signal Journal)

ทุกไม้ที่ยิงเข้า Telegram จะถูกจดไว้ใน state แล้วระบบ "กลับมาติดตามผลเอง"
ทุกรอบสแกน (ทุก 5 นาที) โดยใช้ไฮ-โลว์ของเทียนล่าสุดเดินตามแผนเก็บกำไรเดิมของระบบ:

  TP1 เก็บ 0.3R แล้ว SL ย้ายมาจุดคุ้มทุน
  TP2 เก็บอีก 0.4R แล้ว SL ย้ายมา TP1
  TP3 เก็บอีก 0.3R = ปิดไม้ 1.0R
  ถ้าโดน SL ก่อน TP1 = -1.0R / โดน SL หลังเก็บบางส่วน = เก็บส่วนที่ได้ไว้

ปลายสัปดาห์ระบบสรุป win-rate และ R สุทธิให้เป็นเลขจริง - วัดกันด้วยสถิติ ไม่ใช่ความรู้สึก
"""

KEEP_DEFAULT = 100


def record(state: dict, sig, grade: str, keep: int = KEEP_DEFAULT) -> dict:
    """จดไม้ใหม่ลงสมุด - คืน record ที่เพิ่งจด"""
    from datetime import datetime, timezone

    rec = {
        "ts": datetime.now(timezone.utc).isoformat(),
        "asset": sig.asset,
        "display": sig.display_name,
        "side": sig.side,
        "entry": sig.entry,
        "sl": sig.sl,
        "tp1": sig.tp1,
        "tp2": sig.tp2,
        "tp3": sig.tp3,
        "level_kind": sig.level_kind,
        "grade": grade,
        "cur_sl": sig.sl,
        "realized_r": 0.0,
        "outcome": "OPEN",
        "closed_ts": None,
    }
    state.setdefault("journal", []).append(rec)
    if len(state["journal"]) > keep:
        state["journal"] = state["journal"][-keep:]
    return rec


def ladder_step(rec: dict, high: float, low: float) -> bool:
    """เดินไม้ก้าวหนึ่งด้วยไฮ-โลว์ล่าสุด - คืน True ถ้าไม้เพิ่งปิดสมบูรณ์"""
    side = 1 if rec["side"] == "BUY" else -1
    entry = rec["entry"]

    def hit_sl():
        return low <= rec["cur_sl"] if side == 1 else high >= rec["cur_sl"]

    def hit_tp(tp):
        return high >= tp if side == 1 else low <= tp

    if rec["outcome"] != "OPEN":
        return False
    # โดน SL ก่อน (ระมัดระวังทางเสียมากกว่าเมื่อแท่งเดียวแตะทั้ง SL และ TP)
    if hit_sl():
        from datetime import datetime, timezone

        if rec["realized_r"] <= 0:
            rec["realized_r"], rec["outcome"] = -1.0, "SL"
        else:
            rec["outcome"] = f"PARTIAL {rec['realized_r']:+.1f}R"
        rec["closed_ts"] = datetime.now(timezone.utc).isoformat()
        return True
    if rec["realized_r"] < 0.3 and hit_tp(rec["tp1"]):
        rec["realized_r"] += 0.3
        rec["cur_sl"] = entry  # ย้าย SL ถึงจุดคุ้มทุน
    if rec["realized_r"] < 0.7 and hit_tp(rec["tp2"]):
        rec["realized_r"] += 0.4
        rec["cur_sl"] = rec["tp1"]
    if rec["realized_r"] < 1.0 and hit_tp(rec["tp3"]):
        rec["realized_r"], rec["outcome"] = 1.0, "TP3"
        from datetime import datetime, timezone

        rec["closed_ts"] = datetime.now(timezone.utc).isoformat()
        return True
    return False


def update_open(state: dict, bars: dict[str, tuple[float, float]]) -> int:
    """อัปเดตไม้ที่ยังเปิดทุกไม้ด้วยไฮ-โลว์ล่าสุดของแต่ละสินทรัพย์ - คืนจำนวนไม้ที่เพิ่งปิด"""
    closed = 0
    for rec in state.get("journal", []):
        bar = bars.get(rec["asset"])
        if bar and ladder_step(rec, bar[0], bar[1]):
            closed += 1
    return closed


def weekly_stats(state: dict) -> dict:
    """สถิติ 7 วันล่าสุด: จำนวนไม้, ไม้ที่ได้กำไร, win-rate, R สุทธิ, แยกตามเกรด"""
    from datetime import datetime, timedelta, timezone

    cutoff = (datetime.now(timezone.utc) - timedelta(days=7)).isoformat()
    recs = [r for r in state.get("journal", []) if r["ts"] >= cutoff]
    closed = [r for r in recs if r["outcome"] != "OPEN"]
    wins = [r for r in closed if r["realized_r"] > 0]
    total_r = sum(r["realized_r"] for r in closed)
    by_grade = {}
    for r in closed:
        by_grade.setdefault(r.get("grade", "?"), []).append(r["realized_r"])
    grade_line = " | ".join(
        f"{g}: {len(rs)} ไม้ รวม {sum(rs):+.1f}R" for g, rs in sorted(by_grade.items())
    )
    return {
        "total": len(recs),
        "closed": len(closed),
        "wins": len(wins),
        "win_rate": (len(wins) / len(closed) * 100) if closed else None,
        "total_r": total_r,
        "grade_line": grade_line,
    }


def format_history(state: dict, n: int = 8) -> str:
    """ข้อความ /history - ไม้ล่าสุดพร้อมสถานะ"""
    from datetime import datetime

    from .data import fmt_price, fmt_th_time

    recs = state.get("journal", [])[-n:][::-1]
    if not recs:
        return "ยังไม่มีประวัติสัญญาณ"
    lines = ["สมุดจดผลล่าสุด:"]
    for r in recs:
        arrow = "ซื้อ" if r["side"] == "BUY" else "ขาย"
        status = r["outcome"]
        rr = f"{r['realized_r']:+.1f}R" if status != "OPEN" else f"เปิดอยู่ (SL ที่ {fmt_price(r['cur_sl'], r['asset'])})"
        lines.append(
            f"{fmt_th_time(datetime.fromisoformat(r['ts']))[:11]} | {r['display'][:14]} {arrow} | "
            f"Entry {fmt_price(r['entry'], r['asset'])} | เกรด {r.get('grade','?')} | {rr}"
        )
    return "\n".join(lines)


def summary_text(state: dict) -> str:
    """ข้อความสรุปสั้น ๆ ใช้ทั้ง /stats และส่วนท้ายของสรุปเช้าวันจันทร์"""
    s = weekly_stats(state)
    if s["total"] == 0:
        return "สัปดาห์นี้ยังไม่มีสัญญาณ (ระบบรอจังหวะคุณภาพตามดีไซน์)"
    lines = [f"สัปดาห์นี้: {s['total']} ไม้ (ปิดแล้ว {s['closed']})"]
    if s["closed"]:
        lines.append(f"ได้กำไร {s['wins']}/{s['closed']} = {s['win_rate']:.0f}% | R สุทธิ {s['total_r']:+.1f}R")
        if s["grade_line"]:
            lines.append(f"แยกตามเกรด: {s['grade_line']}")
    return "\n".join(lines)
