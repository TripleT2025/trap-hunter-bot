# -*- coding: utf-8 -*-
"""ชั้นรับคำสั่งจาก Telegram (ปุ่มควบคุมอยู่ในมือคุณ)

ข้อจำกัดที่ควรรู้: GitHub Actions เป็นงานตามตาราง ระบบจึง "กวาดอ่านข้อความใหม่ทุก 5 นาที"
คำสั่งจะถูกตอบภายในไม่เกิน ~5 นาที ไม่ใช่ทันทีแบบเซิร์ฟเวอร์เช่า (แลกด้วยค่าเช่า $0)

คำสั่งที่รองรับ:
  /status  - ระบบตอนนี้: สภาพตลาดรายสินทรัพย์ ราคาล่าสุด พอร์ตจำลอง หยุดชั่วคราวหรือไม่
  /levels  - กับดักใกล้ตัวล่าสุดของแต่ละสินทรัพย์ (เก็บจากรอบสแกนถัดไป 1 รอบ)
  /history - สมุดจดผล: ไม้ล่าสุด 8 ไม้ สถานะ TP/SL
  /stats   - สรุปสัปดาห์: win-rate, R สุทธิ, พอร์ตจำลอง
  /pause   - หยุดส่งสัญญาณชั่วคราว (/pause หรือ /pause 2 = หยุด 2 ชม.)
  /resume  - เลิกหยุด กลับมาส่งปกติ
  /help    - ใช้คำสั่งอะไรได้บ้าง
"""
import sys

from . import journal, paper
from .data import fmt_price, fmt_th_time


HELP_TEXT = (
    "คำสั่งที่ใช้ได้ (ตอบภายใน ~5 นาที):\n"
    "/status - สถานะระบบทุกสินทรัพย์ตอนนี้\n"
    "/levels - กับดักใกล้ตัวล่าสุด\n"
    "/history - ประวัติไม้ล่าสุด + ผล\n"
    "/stats - สรุปสัปดาห์ + พอร์ตจำลอง\n"
    "/pause หรือ /pause 2 - หยุดส่งสัญญาณ 1-2 ชม.\n"
    "/resume - เลิกหยุด\n"
    "/help - ดูคำสั่งทั้งหมด"
)


def process(tg_cfg: dict, state: dict, ctx: dict) -> None:
    """อ่านข้อความใหม่ในแชทแล้วตอบ - tg_cfg คือ dict telegram (มี bot_token)
    ctx มีข้อมูลรอบสแกนล่าสุด (อาจยังว่างในรอบแรก)"""
    if not tg_cfg.get("enabled", True) or not tg_cfg.get("bot_token"):
        return
    try:
        from .telegram import get_updates, send

        updates, next_offset = get_updates(tg_cfg, state.get("tg_offset", 0))
        if not updates:
            return
        state["tg_offset"] = next_offset
        for msg in updates:
            text = str(msg.get("text", "")).strip()
            chat_id = str(msg.get("chat", {}).get("id", ""))
            if not text.startswith("/") or not chat_id:
                continue
            reply = _dispatch(state, ctx, text)
            try:
                send({"bot_token": tg_cfg.get("bot_token", ""), "chat_id": chat_id, "parse_mode": "HTML"}, reply)
            except Exception as e:
                print(f"[commands] ตอบกลับไม่สำเร็จ ({e.__class__.__name__})", file=sys.stderr)
    except Exception as e:
        print(f"[commands] กวาดข้อความไม่สำเร็จ ({e.__class__.__name__}) - ข้ามรอบนี้", file=sys.stderr)


def _dispatch(state: dict, ctx: dict, text: str) -> str:
    parts = text.split()
    cmd = parts[0].lower().split("@")[0]

    if cmd in ("/start", "/help"):
        return HELP_TEXT

    if cmd == "/status":
        return _status(state, ctx)

    if cmd == "/levels":
        return _levels(state)

    if cmd == "/history":
        return journal.format_history(state)

    if cmd == "/stats":
        p = state.get("paper")
        lines = [journal.summary_text(state)]
        if p and p.get("start"):
            lines.append(paper.status_line(p))
            lines.append(f"ไม้เปิดค้าง: {', '.join(p['positions'].keys()) or 'ไม่มี'}")
        return "\n".join(lines)

    if cmd == "/pause":
        from datetime import datetime, timedelta, timezone

        hours = 1.0
        if len(parts) > 1:
            try:
                hours = max(0.25, min(float(parts[1]), 48.0))
            except ValueError:
                pass
        until = datetime.now(timezone.utc) + timedelta(hours=hours)
        state["paused_until"] = until.isoformat()
        return f"หยุดส่งสัญญาณถึง {fmt_th_time(until)} - ระบบยังสแกนและจดผลตามปกติ แต่ไม่ส่งการ์ดใหม่"

    if cmd == "/resume":
        state["paused_until"] = None
        return "กลับมาส่งสัญญาณตามปกติแล้ว"

    return HELP_TEXT


def _status(state: dict, ctx: dict) -> str:
    lines = [f"สุดท้ายสแกน: {fmt_th_time(ctx['scanned_at'])}" if ctx.get("scanned_at") else "กำลังสแกนรอบแรก..."]
    from datetime import datetime, timezone

    paused = state.get("paused_until")
    if paused:
        try:
            if datetime.fromisoformat(paused) > datetime.now(timezone.utc):
                lines.append(f"สถานะ: หยุดส่งชั่วคราวถึง {fmt_th_time(datetime.fromisoformat(paused))}")
            else:
                lines.append("สถานะ: ปกติ (หมายเหตุ: ตัวจับเวลาหยุดหมดอายุแล้ว)")
        except Exception:
            lines.append("สถานะ: ปกติ")
    else:
        lines.append("สถานะ: ทำงานปกติ ส่งสัญญาณตามจังหวะ")
    for asset, info in (ctx.get("regimes") or {}).items():
        price_s = f" | ราคา {fmt_price(info['price'], asset)}" if info.get("price") is not None else ""
        chg = info.get("change_24h")
        chg_s = f" ({chg:+.2f}% 24ชม.)" if chg is not None else ""
        lines.append(f"{info.get('display', asset)}: {info.get('regime_label','?')}{price_s}{chg_s}")
    p = state.get("paper")
    if p and p.get("start"):
        lines.append(paper.status_line(p))
    lines.append("เงียบไม่มีการ์ด = ยังไม่มีจังหวะผ่านครบชั้นยืนยัน (ปกติ - ระบบออกแบบมาให้รอไม้คุณภาพ)")
    return "\n".join(lines)


def _levels(state: dict) -> str:
    cache = state.get("levels_cache") or {}
    if not cache:
        return "ยังไม่มีข้อมูลระดับ (จะพร้อมใช้หลังรอบสแกนถัดไป 1 รอบ)"
    lines = ["กับดักใกล้ตัวล่าสุด:"]
    for asset, lvs in cache.items():
        if not lvs:
            continue
        display = (state.get("regime_cache") or {}).get(asset, {}).get("display", asset)
        lines.append(f"{display}: " + " | ".join(lvs))
    return "\n".join(lines)
