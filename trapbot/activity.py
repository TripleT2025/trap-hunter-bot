# -*- coding: utf-8 -*-
"""สมุดกิจวัน (Daily Activity Ledger) - ทำให้ "ความเงียบ" มีตัวเลขอธิบายได้

ที่มา: สมุด 3 เล่มตามข้อเสนอรวม 3 AI คือแยกให้ชัดว่าระบบเห็นอะไรมาบ้าง
และตัดการ์ดทิ้งด้วยเหตุผลอะไร จนถึงการ์ดที่ลง Telegram จริง:

  พบ (โครงสร้างกวาด-กลับตัวครบ) -> ยังเข้าได้ (ผ่านประตูเวลา/ราคา) -> ยิงจริง (ลง Telegram)

พอมีเลข 3 ชั้นนี้ แยกปัญหาได้ทันทีว่า:
  - เงียบเพราะไม่มีไม้ (พบน้อย)
  - เงียบเพราะความเร็ว (พบเยอะแต่ส่งไม่ทัน - ต้องแก้ฝั่ง runner)
  - เงียบเพราะคุณภาพ (ผ่านมาเยอะแต่ถูกกรอง - ดูเหตุผลที่จดไว้)
ทั้งยังใช้เป็น "ใบเสร็จการทำงานเมื่อวาน" บนสรุปเช้า และตัวตอบ /why ได้ทันที
"""
import sys
from datetime import datetime, timedelta, timezone

from .data import bangkok_now, TH_TZ


# ---------- พื้นฐาน ----------

def today_key() -> str:
    """วันที่วันนี้ตามเวลาไทย (ทุกตัวนับของสมุดกิจวันใช้เขตเวลาไทยเดียวกัน)"""
    return bangkok_now().strftime("%Y-%m-%d")


def ledger(state: dict) -> dict:
    """ดึงสมุดของวันนี้ (ยังไม่มีสร้างใหม่ให้เอง) - เก่ากว่า 10 วันถูกทิ้ง"""
    days = state.setdefault("daily_ledger", {})
    today = today_key()
    rec = days.setdefault(today, _new_day())
    for d in [d for d in days if d < today]:
        del days[d]
    return rec


def _new_day() -> dict:
    return {
        "seen": 0,            # พบ: โครงสร้างกวาด-กลับตัวครบ (ก่อนประตูทุกชั้น)
        "watch": 0,           # เฝ้าดู: เกือบผ่าน ส่งแบบเงียบให้ติดตาม
        "actionable": 0,      # ยังเข้าได้: ผ่านประตูเวลา+ราคา
        "sent": 0,            # ยิงจริง: ลง Telegram พร้อมเสียง
        "missed": {},         # เหตุผลตัดการ์ด: {"เหตุผล": จำนวน}
        "missed_total": 0,
        "paper_closed_r": 0.0,  # ผลไม้จำลองที่ปิดวันนี้ (R รวม)
        "paper_closed_n": 0,
    }


def yesterday_key() -> str:
    return (bangkok_now() - timedelta(days=1)).strftime("%Y-%m-%d")


# ---------- บันทึกเหตุการณ์ ----------

def record_seen(state: dict, key: str) -> None:
    """นับ "พบ" - นับเหตุการณ์ (แท่งกวาด) ครั้งเดียว แม้สแกนเจอซ้ำหลายรอบ"""
    rec = ledger(state)
    keys = rec.setdefault("seen_keys", [])
    if key in keys:
        return
    keys.append(key)
    if len(keys) > 120:
        del keys[: len(keys) - 120]
    rec["seen"] += 1


def record_missed(state: dict, reason: str) -> None:
    """นับการ์ดที่ถูกตัดพร้อมเหตุผล"""
    rec = ledger(state)
    rec["missed"][reason] = rec["missed"].get(reason, 0) + 1
    rec["missed_total"] += 1


def add_watch(state: dict) -> None:
    ledger(state)["watch"] += 1


def add_actionable(state: dict) -> None:
    ledger(state)["actionable"] += 1


def add_sent(state: dict) -> None:
    ledger(state)["sent"] += 1


def add_paper_close(state: dict, r: float) -> None:
    rec = ledger(state)
    rec["paper_closed_r"] = round(rec["paper_closed_r"] + r, 2)
    rec["paper_closed_n"] += 1


def log_missed_card(state: dict, display: str, side: str, level_kind: str, price: float, reason: str) -> None:
    """จดการ์ดที่ "พบแต่ส่งไม่ทัน" ลงสมุดย้อนหลัง (ใช้ตอบ /why และสรุปเช้า)"""
    entries = state.setdefault("missed", [])
    entries.append(
        {
            "ts": datetime.now(timezone.utc).isoformat(),
            "display": display,
            "side": side,
            "level_kind": level_kind,
            "price": price,
            "reason": reason,
        }
    )
    if len(entries) > 60:
        del entries[: len(entries) - 60]
    record_missed(state, reason.split("(")[0].strip())


def log_watch_card(state: dict, display: str, side: str, level_kind: str, price: float, fail_th: str) -> None:
    """จดการ์ดเฝ้าดู (เกือบผ่าน) ลงสมุด"""
    entries = state.setdefault("watch_log", [])
    entries.append(
        {
            "ts": datetime.now(timezone.utc).isoformat(),
            "display": display,
            "side": side,
            "level_kind": level_kind,
            "price": price,
            "fail": fail_th,
        }
    )
    if len(entries) > 40:
        del entries[: len(entries) - 40]


# ---------- รายงาน ----------

def receipt_yesterday(state: dict) -> str | None:
    """ใบเสร็จการทำงานเมื่อวาน - ข้อความสั้น ๆ ใช้บนสรุปเช้า"""
    days = state.get("daily_ledger") or {}
    rec = days.get(yesterday_key())
    if not rec:
        return None
    parts = [f"พบจังหวะ {rec.get('seen', 0)} เหตุการณ์"]
    if rec.get("watch"):
        parts.append(f"เฝ้าดู {rec['watch']}")
    parts.append(f"ยิงจริง {rec.get('sent', 0)} ใบ")
    if rec.get("missed_total"):
        top = sorted(rec.get("missed", {}).items(), key=lambda x: -x[1])[:2]
        why = ", ".join(f"{k} {v}" for k, v in top)
        parts.append(f"ตัดทิ้ง {rec['missed_total']} (เหตุผลหลัก: {why})")
    if rec.get("paper_closed_n"):
        parts.append(f"ไม้จำลองปิด {rec['paper_closed_n']} ไม้ รวม {rec['paper_closed_r']:+.1f}R")
    return "ใบเสร็จเมื่อวาน: " + " | ".join(parts)


def today_line(state: dict) -> str:
    """สรุปสมุดวันนี้ (ใช้ตอบ /why)"""
    rec = ledger(state)
    parts = [
        f"พบ {rec.get('seen', 0)}",
        f"เฝ้าดู {rec.get('watch', 0)}",
        f"ยังเข้าได้ {rec.get('actionable', 0)}",
        f"ยิงจริง {rec.get('sent', 0)}",
    ]
    if rec.get("missed_total"):
        parts.append(f"ตัดทิ้ง {rec['missed_total']}")
    return "สมุดวันนี้ (เวลาไทย): " + " | ".join(parts)


def missed_recent(state: dict, n: int = 5) -> list[str]:
    """การ์ด "พบแต่ส่งไม่ทัน" ล่าสุด n ใบ เป็นข้อความพร้อมอ่าน"""
    from .data import fmt_price, fmt_th_time

    entries = (state.get("missed") or [])[-n:][::-1]
    lines = []
    for e in entries:
        ts = None
        try:
            ts = datetime.fromisoformat(e["ts"])
        except Exception:
            pass
        when = fmt_th_time(ts)[:11] if ts else ""
        lines.append(
            f"  {when} {e['display']} {e['side']} @ {e['level_kind']} {fmt_price(e['price'], '')} - {e['reason']}"
        )
    return lines


def watch_recent(state: dict, n: int = 5) -> list[str]:
    """การ์ดเฝ้าดูล่าสุด n ใบ"""
    from .data import fmt_price, fmt_th_time

    entries = (state.get("watch_log") or [])[-n:][::-1]
    lines = []
    for e in entries:
        ts = None
        try:
            ts = datetime.fromisoformat(e["ts"])
        except Exception:
            pass
        when = fmt_th_time(ts)[:11] if ts else ""
        lines.append(f"  {when} {e['display']} {e['side']} @ {e['level_kind']} {fmt_price(e['price'], '')} - {e['fail']}")
    return lines


# ---------- แผน if-then ของสรุปเช้า ----------

_SIDE_BY_KIND = {
    "PDL": "BUY", "EQL": "BUY", "SESSION_LOW": "BUY",
    "PDH": "SELL", "EQH": "SELL", "SESSION_HIGH": "SELL",
}


def if_then_plan(state: dict, max_assets: int = 4) -> list[str]:
    """แผน if-then จากกับดักใกล้ตัวล่าสุด: ถ้าเกิดนั้น -> ทำนี้ (แทนการพยากรณ์ทิศทาง)
    รูปแบบ: ถ้า BTC กวาด จุดต่ำวันก่อน (PDL) 60,000 แล้วปิดกลับขึ้น -> รอการ์ดซื้อ"""
    from .reason import _kind_th

    cache = state.get("levels_cache") or {}
    regime = state.get("regime_cache") or {}
    lines = []
    for asset, lvs in list(cache.items())[:max_assets]:
        if not lvs:
            continue
        display = (regime.get(asset) or {}).get("display", asset)
        for lv in lvs[:1]:
            try:
                kind, price = lv.rsplit(" ", 1)
            except ValueError:
                continue
            side = _SIDE_BY_KIND.get(kind)
            if side == "BUY":
                lines.append(f"ถ้า {display} ถูกกวาดที่ {_kind_th(kind)} {price} แล้วปิดกลับขึ้น -> รอการ์ดซื้อ")
            elif side == "SELL":
                lines.append(f"ถ้า {display} ถูกดันกวาดที่ {_kind_th(kind)} {price} แล้วปิดกลับลง -> รอการ์ดขาย")
            else:
                lines.append(f"ถ้า {display} มีการกวาดที่ {_kind_th(kind)} {price} -> จับตาทั้งสองทิศ การ์ดจะสรุปทิศให้")
    return lines


# ---------- สมุด Google Sheets อัตโนมัติ (ทางเลือก ผ่าน Google Apps Script Web App) ----------

def webhook_event(cfg: dict | None, payload: dict) -> None:
    """ถ้าตั้ง trade_log_webhook ไว้ จะยิง JSON เหตุการณ์เข้า URL นั้น (เช่น Apps Script
    ที่เขียนต่อไป Google Sheets) - ล้มเงียบ ๆ ไม่มีผลกับการสแกน"""
    if not cfg or not cfg.get("enabled") or not cfg.get("url"):
        return
    try:
        import requests

        requests.post(str(cfg["url"]), json=payload, timeout=10)
    except Exception as e:
        print(f"[activity] webhook ล้ม ({e.__class__.__name__}) - ข้าม", file=sys.stderr)
