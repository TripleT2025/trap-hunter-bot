# -*- coding: utf-8 -*-
"""ชั้นรับคำสั่งจาก Telegram (ปุ่มควบคุมอยู่ในมือคุณ)

ข้อจำกัดที่ควรรู้: GitHub Actions เป็นงานตามตาราง ระบบจึง "กวาดอ่านข้อความใหม่ทุก 5 นาที"
คำสั่งจะถูกตอบภายในไม่เกิน ~5 นาที ไม่ใช่ทันทีแบบเซิร์ฟเวอร์เช่า (แลกด้วยค่าเช่า $0)

คำสั่งที่รองรับ:
  /status  - ระบบตอนนี้: สภาพตลาดรายสินทรัพย์ ราคาล่าสุด พอร์ตจำลอง หยุดชั่วคราวหรือไม่
  /why     - "วันนี้เจออะไร ทำไมไม่ยิง": สมุดกิจวัน + การ์ดที่ถูกตัดพร้อมเหตุผล
             (แยก "เงียบเพราะไม่มีไม้" กับ "เงียบเพราะระบบตาย" ได้ทันที)
  /levels  - กับดักใกล้ตัวล่าสุดของแต่ละสินทรัพย์ (เก็บจากรอบสแกนถัดไป 1 รอบ)
  /history - สมุดจดผล: ไม้ล่าสุด 8 ไม้ สถานะ TP/SL
  /stats   - สรุปสัปดาห์: win-rate, R สุทธิ, แยกตามเกรด, พอร์ตจำลอง
  /pause   - หยุดส่งสัญญาณชั่วคราว (/pause หรือ /pause 2 = หยุด 2 ชม.)
  /resume  - เลิกหยุด กลับมาส่งปกติ
  /help    - ใช้คำสั่งอะไรได้บ้าง

หมายเหตุเทคนิค: คำสั่งถูกอ่าน "ก่อน" รอบสแกนของรอบนั้น จึงตอบจากบริบทรอบล่าสุด
ที่เก็บค้างไว้ใน state (state["scan_ctx"]) ไม่ใช่จากรอบที่กำลังวิ่ง
"""
import sys

from . import journal, paper
from .data import fmt_price, fmt_th_time


HELP_TEXT = (
    "คำสั่งที่ใช้ได้ (ตอบภายใน ~5 นาที):\n"
    "/status - สถานะระบบทุกสินทรัพย์ตอนนี้\n"
    "/why - วันนี้เจออะไร ทำไมไม่ยิง\n"
    "/levels - กับดักใกล้ตัวล่าสุด\n"
    "/history - ประวัติไม้ล่าสุด + ผล\n"
    "/stats - สรุปสัปดาห์ + พอร์ตจำลอง\n"
    "/pause หรือ /pause 2 - หยุดส่งสัญญาณ 1-2 ชม.\n"
    "/resume - เลิกหยุด\n"
    "/help - ดูคำสั่งทั้งหมด"
)


def process(tg_cfg: dict, state: dict, ctx: dict) -> None:
    """อ่านข้อความใหม่ในแชทแล้วตอบ - tg_cfg คือ dict telegram (มี bot_token)
    (ctx เก็บไว้เพื่อความเข้ากันได้ - การตอบใช้ state["scan_ctx"] ที่เก็บไว้จากรอบล่าสุด)"""
    if not tg_cfg.get("enabled", True) or not tg_cfg.get("bot_token"):
        return
    try:
        from .telegram import get_updates, send

        updates, next_offset = get_updates(tg_cfg, state.get("tg_offset", 0) or 0)
        if not updates:
            return
        state["tg_offset"] = next_offset
        for msg in updates:
            text = str(msg.get("text", "")).strip()
            chat_id = str(msg.get("chat", {}).get("id", ""))
            if not text.startswith("/") or not chat_id:
                continue
            reply = _dispatch(state, text)
            try:
                send({"bot_token": tg_cfg.get("bot_token", ""), "chat_id": chat_id, "parse_mode": "HTML"}, reply)
            except Exception as e:
                print(f"[commands] ตอบกลับไม่สำเร็จ ({e.__class__.__name__})", file=sys.stderr)
    except Exception as e:
        print(f"[commands] กวาดข้อความไม่สำเร็จ ({e.__class__.__name__}) - ข้ามรอบนี้", file=sys.stderr)


def _dispatch(state: dict, text: str) -> str:
    parts = text.split()
    cmd = parts[0].lower().split("@")[0]

    if cmd in ("/start", "/help"):
        return HELP_TEXT

    if cmd == "/status":
        return _status(state)

    if cmd == "/why":
        return _why(state)

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
        return f"หยุดส่งสัญญาณถึง {fmt_th_time(until)} - ระบบยังสแกนและจดผลตามปกติ แต่ไม่ส่งการ์ดใหม่ (ข้อความยกเลิกการ์ดยังส่งให้เสมอ)"

    if cmd == "/resume":
        state["paused_until"] = None
        return "กลับมาส่งสัญญาณตามปกติแล้ว"

    return HELP_TEXT


def _scan_ctx(state: dict) -> dict:
    return state.get("scan_ctx") or {}


def _liveness_line(state: dict) -> str:
    """ระบบหายใจครั้งล่าสุดเมื่อไหร่ - แยก "ไม่มีไม้" ออกจาก "ระบบตาย" """
    iso = state.get("last_ok_scan")
    if not iso:
        return "ระบบ: ยังไม่มีรอบสแกนสำเร็จตั้งแต่เริ่มใช้ฟีเจอร์นี้"
    try:
        from datetime import datetime, timezone

        age_h = (datetime.now(timezone.utc) - datetime.fromisoformat(iso)).total_seconds() / 3600
        if age_h < 0.5:
            return f"ระบบ: หายใจปกติ (สแกนสำเร็จ {age_h * 60:.0f} นาทีก่อน)"
        return f"ระบบ: สแกนสำเร็จล่าสุด {age_h:.1f} ชม. ก่อน - ถ้าเกิน 1 ชม. แปลว่าตัวรันมีปัญหา ให้กด Run workflow ใน GitHub หรือดู watchdog"
    except Exception:
        return "ระบบ: ไม่ทราบสถานะรอบล่าสุด"


def _status(state: dict) -> str:
    ctx = _scan_ctx(state)
    lines = [f"สุดท้ายสแกน: {fmt_th_time(_parse_iso(ctx.get('scanned_at')))}" if ctx.get("scanned_at") else "ยังไม่มีรอบสแกนสำเร็จ"]
    lines.append(_liveness_line(state))
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
    lines.append("เงียบไม่มีการ์ด? พิมพ์ /why จะบอกว่าเงียบเพราะไม่มีไม้ หรือระบบมีปัญหา")
    return "\n".join(lines)


def _why(state: dict) -> str:
    """ตอบคำถามคนเดียวที่สำคัญ: "วันนี้เจออะไร ทำไมไม่ยิง" """
    from .activity import missed_recent, today_line, watch_recent

    ctx = _scan_ctx(state)
    lines = [f"<b>รายงานวันนี้เจออะไร | {fmt_th_time(_parse_iso(ctx.get('scanned_at')))[:11] if ctx.get('scanned_at') else 'ยังไม่มีรอบสแกน'}</b>"]
    lines.append(_liveness_line(state))
    lines.append(today_line(state))

    for asset, info in (ctx.get("regimes") or {}).items():
        lines.append(f"{info.get('display', asset)}: ตลาด{info.get('regime_label', '?')}{_chg(info)}")

    missed = missed_recent(state, 4)
    if missed:
        lines.append("การ์ดที่พบแต่ส่งไม่ทัน / ถูกตัด (ล่าสุด):")
        lines.extend(missed)
    watch = watch_recent(state, 4)
    if watch:
        lines.append("จังหวะเฝ้าดูล่าสุด (เกือบผ่าน):")
        lines.extend(watch)
    if not missed and not watch:
        lines.append("วันนี้ยังไม่มีจังหวะที่ผ่านโครงสร้างกวาด-กลับตัวเลย (ปกติ - ระบบออกแบบมาให้รอไม้คุณภาพ)")
    return "\n".join(lines)


def _chg(info: dict) -> str:
    chg = info.get("change_24h")
    return f" ({chg:+.2f}% 24ชม.)" if chg is not None else ""


def _parse_iso(iso: str | None):
    from datetime import datetime

    if not iso:
        return None
    try:
        return datetime.fromisoformat(iso)
    except Exception:
        return None


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
