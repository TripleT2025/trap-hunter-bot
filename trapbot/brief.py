# -*- coding: utf-8 -*-
"""สรุปเช้าประจำวัน + รายงานประจำสัปดาห์ (Morning Brief & Weekly Report)

ทำไมต้องมี: ระบบตั้งใจเงียบเมื่อไม่มีจังหวะคุณภาพ แต่ "เงียบ" กับ "ตาย" หน้าตาเหมือนกัน
สรุปเช้าทุกวัน 08:00 น. (เวลาไทย) จึงทำหน้าที่ 2 อย่างพร้อมกัน:
  1. ผู้ใช้เห็นรายงานทุกเช้า = รู้ว่าระบบยังหายใจอยู่เสมอ (heartbeat)
  2. ได้ภาพรวมตลาดเช้าวันจริง: ราคาตอนนี้ เปลี่ยนแปลง 24 ชม. สภาพตลาด กับดักใกล้ตัว ข่าวแรงวันนี้

วันจันทร์มีของแถม (Weekly Report): สถิติสัปดาห์ win-rate/R สุทธิ + พอร์ตจำลองพร้อมกราฟ
+ "วงจรเรียนรู้": ให้ AI อ่านสถิติแล้วเสนอปรับพารามิเตอร์พร้อมเหตุผล (แค่เสนอ - ไม่แก้เอง)

กลไกกันส่งซ้ำ: เก็บวันที่ส่งล่าสุดไว้ใน state (สแกนทุก 5 นาที จะเข้าเงื่อนไขช่วงเช้าหลายรอบ
แต่ส่งได้ 1 ครั้ง/วันเท่านั้น)
"""
import json
import sys

from .data import bangkok_now, fmt_price


def maybe_send_brief(cfg: dict, state: dict, tg_cfg: dict, ctx: dict, send_fn, send_photo_fn) -> bool:
    """ถ้าอยู่ในช่วงเวลาสรุปเช้าและยังไม่ส่งวันนี้ -> ส่งและคืน True"""
    if not cfg.get("enabled"):
        return False
    try:
        now = bangkok_now()
        hour = int(cfg.get("hour", 8))
        window = int(cfg.get("window_minutes", 15))
        today = now.strftime("%Y-%m-%d")
        if now.hour != hour or now.minute >= window or state.get("brief_date") == today:
            return False

        is_monday = now.weekday() == 0
        text = _compose_brief(cfg, state, ctx)
        if is_monday and cfg.get("weekly", True):
            text += "\n\n" + _weekly_section(state, cfg, tg_cfg)
            png = _weekly_png(state)
            if png and send_photo_fn:
                try:
                    send_photo_fn(tg_cfg, png, caption="รายงานสัปดาห์ - Equity Curve พอร์ตจำลอง")
                except Exception as e:
                    print(f"[brief] ส่งกราฟรายสัปดาห์ไม่สำเร็จ ({e.__class__.__name__})", file=sys.stderr)
        send_fn(tg_cfg, text)
        state["brief_date"] = today
        return True
    except Exception as e:
        print(f"[brief] สรุปเช้าพลาด ({e.__class__.__name__}) - ไม่กระทบรอบสแกนหลัก", file=sys.stderr)
        return False


def _compose_brief(cfg: dict, state: dict, ctx: dict) -> str:
    """เรียบการ์ดสรุปเช้าจากข้อมูลรอบสแกนล่าสุด - ส่วนหัวเป็นไทยเต็ม มี AI ช่วยเขียนบทสรุป"""
    from .calendar import today_summary

    lines = [f"<b>สรุปเช้า Trap Hunter | {bangkok_now().strftime('%d/%m/%Y')}</b>"]
    regimes = ctx.get("regimes") or {}
    if not regimes:
        lines.append("(รอบนี้ดึงข้อมูลตลาดไม่ครบ - ข้อมูลเต็มจะมาในรอบถัดไป)")
    for asset, info in regimes.items():
        chg = info.get("change_24h")
        chg_s = f" ({chg:+.2f}% 24ชม.)" if chg is not None else ""
        lines.append(f"{info.get('display', asset)}: {fmt_price(info['price'], asset)}{chg_s} | ตลาด{info.get('regime_label', '?')}")

    nearby = state.get("levels_cache") or {}
    if nearby:
        lines.append("")
        lines.append("กับดักใกล้ตัวที่ควรจับตา:")
        for asset, lvs in nearby.items():
            if lvs:
                display = (regimes.get(asset) or {}).get("display", asset)
                lines.append(f"  {display}: {' | '.join(lvs)}")

    news = today_summary()
    if news:
        lines.append("")
        lines.append("ข่าวแรงวันนี้ (เวลาไทย):")
        for n in news:
            lines.append(f"  {n}")
    else:
        lines.append("")
        lines.append("วันนี้ไม่มีข่าวแรงระดับ High ของ USD")

    ai_summary = _ai_market_summary(cfg, state, ctx)
    if ai_summary:
        lines.append("")
        lines.append(f"<b>มุมมอง AI วันนี้:</b> {ai_summary}")

    lines.append("")
    lines.append("ถึงจะเงียบตลอดวันก็ปกติ - การ์ดจะเด้งทันทีเมื่อจังหวะผ่านครบทุกชั้นยืนยัน (พิมพ์ /status ถามได้ตลอด)")
    return "\n".join(lines)


def _ai_market_summary(cfg: dict, state: dict, ctx: dict) -> str | None:
    """ให้ AI เขียนมุมมองตลาด 2-3 ประโยคจากข้อมูลจริง - ไม่มี key / ล้ม = คืน None (ไม่มีผลข้างเคียง)"""
    try:
        from .ai_review import ai_text, get_api_key

        if not (cfg.get("ai_brief", True) and get_api_key()):
            return None
        facts = [
            {
                "asset": info.get("display", asset),
                "price": round(info["price"], 2) if info.get("price") is not None else None,
                "change_24h_pct": info.get("change_24h"),
                "regime": info.get("regime_label"),
            }
            for asset, info in (ctx.get("regimes") or {}).items()
        ]
        if not facts:
            return None
        prompt = (
            "คุณเป็นนักวิเคราะห์ช่วยเทรดคนไทย ข้อมูลตลาดเช้านี้:\n"
            + json.dumps(facts, ensure_ascii=False)
            + "\nเขียนมุมมองภาษาไทย 2-3 ประโยค อ่านง่าย บอกว่าสภาพตลาดรวมเป็นอย่างไร "
            "ตัวไหนน่าจับตาและเพราะอะไร (เน้นจิตทัศน์ liquidity เช่น ระดับที่แรงซื้อขายอาจไล่ล่า) "
            "ห้ามให้คำแนะนำลงทุนแบบซื้อ/ขายตรง ๆ ไม่ต้องทักทาย"
        )
        return ai_text(cfg.get("ai_cfg") or {}, prompt, max_tokens=350)
    except Exception as e:
        print(f"[brief] AI สรุปตลาดพลาด ({e.__class__.__name__})", file=sys.stderr)
        return None


def _weekly_section(state: dict, cfg: dict, tg_cfg: dict) -> str:
    """ส่วนรายงานสัปดาห์: สถิติ + พอร์ตจำลอง + ข้อเสนอจากวงจรเรียนรู้"""
    from . import journal, paper

    lines = ["<b>รายงานประจำสัปดาห์</b>"]
    lines.append(journal.summary_text(state))
    p = state.get("paper")
    if p and p.get("start"):
        lines.append(paper.status_line(p))
        wc = paper.weekly_closed(p)
        if wc:
            pnl = sum(r["pnl_usd"] for r in wc)
            lines.append(f"พอร์ตจำลองสัปดาห์นี้: ปิด {len(wc)} ไม้ กำไร-ขาดทุนรวม {pnl:+,.2f}$")

    suggestion = _ai_weekly_suggestion(cfg, state, p)
    if suggestion:
        lines.append("")
        lines.append("<b>ข้อเสนอจากวงจรเรียนรู้ (AI เสนอ - ยังไม่แก้ระบบเอง):</b>")
        lines.append(suggestion)
        lines.append("(ถ้าอยากใช้ข้อเสนอไหน ทักมาบอกผู้ช่วยของคุณเพื่อปรับ config ให้)")
    return "\n".join(lines)


def _ai_weekly_suggestion(cfg: dict, state: dict, paper_state: dict | None) -> str | None:
    try:
        from .ai_review import ai_text, get_api_key
        from . import journal

        if not get_api_key():
            return None
        stats = journal.weekly_stats(state)
        payload = {
            "signals_week": stats,
            "paper": None
            if not paper_state
            else {
                "equity": paper_state.get("equity"),
                "start": paper_state.get("start"),
                "closed_recent": (paper_state.get("closed") or [])[-10:],
            },
        }
        prompt = (
            "คุณเป็นนักสถิติระบบเทรด liquidity sweep อ่านผลสัปดาห์นี้:\n"
            + json.dumps(payload, ensure_ascii=False)
            + "\nเสนอ 1-3 ข้อปรับปรุงภาษาไทยสั้น ๆ เช่น ควรเข้ม/คลายเกณฑ์ไหน สินทรัพย์ไหนคุ้ม "
            "ถ้าข้อมูลน้อยเกินวิเคราะห์ ให้บอกตรง ๆ ว่ายังต้องเก็บข้อมูลอีก ห้ามแต่งเลขเอง"
        )
        return ai_text(cfg.get("ai_cfg") or {}, prompt, max_tokens=380)
    except Exception as e:
        print(f"[brief] ข้อเสนอรายสัปดาห์พลาด ({e.__class__.__name__})", file=sys.stderr)
        return None


def _weekly_png(state: dict) -> bytes | None:
    p = state.get("paper")
    if not p or not p.get("start"):
        return None
    from .paper import equity_chart_png

    return equity_chart_png(p)
