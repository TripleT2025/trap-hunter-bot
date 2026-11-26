# -*- coding: utf-8 -*-
"""สรุปเช้าประจำวัน + รายงานประจำสัปดาห์ (Morning Brief & Weekly Report)

ทำไมต้องมี: ระบบตั้งใจเงียบเมื่อไม่มีจังหวะคุณภาพ แต่ "เงียบ" กับ "ตาย" หน้าตาเหมือนกัน
สรุปเช้าทุกวัน 08:00 น. (เวลาไทย) จึงทำหน้าที่ 2 อย่างพร้อมกัน:
  1. ผู้ใช้เห็นรายงานทุกเช้า = รู้ว่าระบบยังหายใจอยู่เสมอ (heartbeat)
  2. ได้ภาพรวมตลาดเช้าวันจริง: ราคาตอนนี้ เปลี่ยนแปลง 24 ชม. สภาพตลาด กับดักใกล้ตัว ข่าวแรงวันนี้

อัปเดต 26 พ.ย. 2026 (จากข้อเสนอ 3 AI + เหตุการณ์จริงที่ GitHub cron ตายเงียบทั้งเช้า):
  - ส่งชดเชยถ้ามาช้า: ถ้ารอบรันของวันนี้ดีดกลับมาหลังหน้าต่าง 08:00-08:15
    สรุปจะถูกส่งทันทีพร้อมป้าย "ส่งชดเชย" - เลิกโหมด "ข้ามเงียบ ๆ" เดิม
  - แผนวันนี้เป็น if-then ("ถ้ากวาดระดับนี้แล้วกลับตัว -> รอการ์ดทิศนั้น") แทนการพยากรณ์ทิศ
  - มี "ใบเสร็จการทำงานเมื่อวาน": เจอกี่จังหวะ ยิงกี่ใบ ตัดเพราะอะไร (จากสมุดกิจวัน)
  - เลิกใช้ AI เขียนสรุปประจำวัน (ai_brief: false) - งานประจำใช้แม่แบบ ฟรี คงเส้นคงวา

วันจันทร์มีของแถม (Weekly Report): สถิติสัปดาห์ win-rate/R สุทธิ + พอร์ตจำลองพร้อมกราฟ
(ตรงนี้เรียก AI ได้เฉพาะถ้ามีคีย์ - งานนานๆ ครั้ง ไม่ใช่งานประจำ)

กลไกกันส่งซ้ำ: เก็บวันที่ส่งล่าสุดไว้ใน state (สแกนทุก 5 นาที จะเข้าเงื่อนไขเช้าหลายรอบ
แต่ส่งได้ 1 ครั้ง/วันเท่านั้น)
"""
import json
import sys

from .data import bangkok_now, fmt_price


def maybe_send_brief(cfg: dict, state: dict, tg_cfg: dict, ctx: dict, send_fn, send_photo_fn) -> bool:
    """ถ้าถึงเวลาสรุปเช้า (หรือมาช้ากว่านั้น) และยังไม่ส่งวันนี้ -> ส่งชดเชยทันทีและคืน True"""
    if not cfg.get("enabled"):
        return False
    try:
        now = bangkok_now()
        hour = int(cfg.get("hour", 8))
        window = int(cfg.get("window_minutes", 15))
        today = now.strftime("%Y-%m-%d")
        if state.get("brief_date") == today:
            return False
        if now.hour < hour:
            return False  # ยังไม่ถึงช่วงเช้า - รอรอบถัดไป
        # ถึงเวลาแล้ว (หรือระบบมาช้า): รอบแรกของวันที่ยังไม่ได้ส่ง = ส่งเลย ไม่ข้ามเงียบ ๆ
        late = not (now.hour == hour and now.minute < window)

        is_monday = now.weekday() == 0
        text = _compose_brief(cfg, state, ctx, late)
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


def _compose_brief(cfg: dict, state: dict, ctx: dict, late: bool = False) -> str:
    """เรียบการ์ดสรุปเช้าจากข้อมูลรอบสแกนล่าสุด - แผน if-then + ใบเสร็จเมื่อวาน"""
    from .activity import if_then_plan, receipt_yesterday
    from .calendar import today_summary

    head = "สรุปเช้า (ส่งชดเชย - ระบบมาช้ากว่าปกติ)" if late else "สรุปเช้า Trap Hunter"
    lines = [f"<b>{head} | {bangkok_now().strftime('%d/%m/%Y')}</b>"]
    regimes = ctx.get("regimes") or {}
    if not regimes:
        lines.append("(รอบนี้ดึงข้อมูลตลาดไม่ครบ - ข้อมูลเต็มจะมาในรอบถัดไป)")
    for asset, info in regimes.items():
        chg = info.get("change_24h")
        chg_s = f" ({chg:+.2f}% 24ชม.)" if chg is not None else ""
        lines.append(f"{info.get('display', asset)}: {fmt_price(info['price'], asset)}{chg_s} | ตลาด{info.get('regime_label', '?')}")

    receipt = receipt_yesterday(state)
    if receipt:
        lines.append("")
        lines.append(receipt)

    plan = if_then_plan(state)
    if plan:
        lines.append("")
        lines.append("แผนวันนี้ แบบ if-then (เห็นอะไรแล้วทำอะไร):")
        lines.extend(f"  {p}" for p in plan)
        lines.append("  (ระบบไม่พยากรณ์ทิศทางล่วงหน้า - รอให้ตลาดพิสูจน์ก่อนแล้วจึงยิงการ์ด)")

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

    lines.append("")
    lines.append("ถึงจะเงียบตลอดวันก็ปกติ - การ์ดจะเด้งทันทีเมื่อจังหวะผ่านครบทุกชั้นยืนยัน (พิมพ์ /why ถามได้ตลอดว่าเงียบเพราะอะไร)")
    return "\n".join(lines)


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
