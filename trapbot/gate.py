# -*- coding: utf-8 -*-
"""ประตู "ยังเข้าได้ไหม" + อายุการ์ด + การยกเลิก (Tradability Gate & Card Lifecycle)

ที่มา: ทั้ง 3 AI เห็นพร้องกันว่า การ์ดที่มาช้ากว่าหน้าต่างเข้าไม้ หรือราคาวิ่งหนีไปแล้ว
ควรถูกจัดว่า "พบแต่ส่งไม่ทัน" ไม่ใช่ยิงออกไปให้ผู้ใช้ไล่ราคา

กติกา 3 ชั้นก่อนยิงการ์ด "เข้าได้ตามแผน":
  1. อายุ      - แท่งยืนยันปิดมาแล้วไม่เกิน max_age_minutes (การ์ดมีเวลาหมดอายุชัดเจน)
  2. ราคายังอยู่ - ราคาล่าสุดยังไม่หลุดเลยโซนเข้าเกิน beyond_entry_atr ATR
                 (หลุดไปแล้ว = กลับตัวที่รอไม่เกิด ส่งไม่ได้)
  3. ไม่วิ่งหนี  - ราคาวิ่งพ้นโซนเข้าเกิน chase_r เท่าของความเสี่ยง = ไล่ไม้ ไม่ใช่แผน
ไม่ผ่านชั้นไหน -> จดสมุด "พบแต่ส่งไม่ทัน" พร้อมเหตุผล แล้วเงียบ (ผู้ใช้ดูย้อนได้ที่ /why)

การ์ดที่ยิงออกไปแล้วถูก "เฝ้าติดตาม" จนจบด้วย 2 เหตุ:
  - หมดอายุ (พ้น max_age_minutes โดยไม่มีอะไรเกิดขึ้น) -> แจ้งยกเลิก เพื่อกัน "ไล่เข้าทีหลัง"
  - ราคาปิดทะลุไส้กวาดฝั่งตรงข้าม (โครงสร้างกลับตัวพังแล้ว) -> แจ้งยกเลิกทันที
ข้อความยกเลิกเป็น "ข้อความใหม่พร้อมเสียง" เสมอ ไม่ใช่การแก้ข้อความเดิม
"""
import sys
from datetime import datetime, timedelta, timezone

from .data import bangkok_now


# ---------- ประตูก่อนยิง ----------

def evaluate(sig, price_now: float, atr: float, cfg: dict) -> tuple[bool, str]:
    """ตัดสินว่าจังหวะนี้ยังเข้าได้ตามแผนหรือยัง - คืน (ผ่านไหม, เหตุผลภาษาไทย)"""
    if not cfg.get("enabled", True):
        return True, "ประตูเวลาปิดอยู่ (config)"
    now = datetime.now(timezone.utc)
    ts = sig.ts
    if ts is not None:
        try:
            if getattr(ts, "tzinfo", None) is None:
                ts = ts.replace(tzinfo=timezone.utc)
            age_min = (now - ts).total_seconds() / 60.0
            max_age = float(cfg.get("max_age_minutes", 30))
            if age_min > max_age:
                return False, f"หมดเวลา (แท่งยืนยันปิดมาแล้ว {age_min:.0f} นาที > {max_age:.0f} นาที)"
        except Exception:
            pass
    try:
        beyond = float(cfg.get("beyond_entry_atr", 0.15)) * atr
        chase = float(cfg.get("chase_r", 0.5)) * sig.r
        if sig.side == "BUY":
            if price_now < sig.entry_low - beyond:
                return False, "ราคาหลุดโซนเข้าไปแล้ว (กลับตัวที่รอยังไม่เกิด)"
            if price_now > sig.entry_high + chase:
                return False, "ราคาวิ่งไกลเกินโซนเข้า (ไล่ไม้ไม่ใช่แผน)"
        else:
            if price_now > sig.entry_high + beyond:
                return False, "ราคาพุ่งทะลุโซนเข้าไปแล้ว (กลับตัวที่รอยังไม่เกิด)"
            if price_now < sig.entry_low - chase:
                return False, "ราคาวิ่งไกลเกินโซนเข้า (ไล่ไม้ไม่ใช่แผน)"
    except Exception:
        pass
    return True, "ยังเข้าได้ตามแผน"


# ---------- ช่วงเวลาตลาดสหรัฐ (ใช้กับโหมดเฝ้าดูของทอง) ----------

def in_us_session(acfg: dict, now_th=None) -> bool:
    """อยู่ในช่วงตลาดสหรัฐเปิดหรือไม่ (เวลาไทย) - รองรับช่วงที่ข้ามเที่ยงคืน เช่น [20, 4]
    ไม่ได้ตั้ง us_session_th ไว้ = ถือว่าอยู่ในเซสชั่นตลอด"""
    span = acfg.get("us_session_th")
    if not span:
        return True
    try:
        now_th = now_th or bangkok_now()
        h = int(now_th.hour)
        a, b = int(span[0]), int(span[1])
        return (a <= h < b) if a <= b else (h >= a or h < b)
    except Exception:
        return True


# ---------- การ์ดที่ยิงแล้ว: เฝ้าติดตาม + ยกเลิก ----------

def register_card(state: dict, sig, trad_cfg: dict, expire_at=None) -> dict:
    """จดการ์ดที่เพิ่งยิงไว้เฝ้าติดตาม - จนหมดอายุหรือถูกยกเลิก
    expire_at: เวลาหมดอายุ (UTC datetime) คำนวณจากแท่งยืนยัน + max_age_minutes
               ไม่ได้ส่งมา = ใช้เวลาส่ง + max_age_minutes แทน"""
    now = datetime.now(timezone.utc)
    expire_min = float(trad_cfg.get("max_age_minutes", 30))
    if expire_at is None:
        expire_at = now + timedelta(minutes=expire_min)
    if getattr(expire_at, "tzinfo", None) is None:
        expire_at = expire_at.replace(tzinfo=timezone.utc)
    card = {
        "asset": sig.asset,
        "display": sig.display_name,
        "side": sig.side,
        "entry": sig.entry,
        "sl": sig.sl,
        "cancel_level": sig.extras.get("sweep_extreme"),
        "sent_ts": now.isoformat(),
        "expire_at": expire_at.isoformat(),
    }
    state.setdefault("active_cards", {})[f"{sig.asset}|{sig.side}"] = card
    return card


def expire_stale(state: dict) -> list[dict]:
    """หมดอายุการ์ดที่พ้นเวลาทั้งหมด (ใช้ได้แม้สินทรัพย์นั้นดึงข้อมูลล้ม) - ตัดออกจาก state แล้วคืนรายการ"""
    out = []
    cards = state.get("active_cards") or {}
    now = datetime.now(timezone.utc)
    for key, card in list(cards.items()):
        try:
            if datetime.fromisoformat(card["expire_at"]) <= now:
                card["cancel_reason"] = "หมดเวลาการ์ด (พ้นหน้าต่างเข้าไม้ ไม่ควรตามเข้า)"
                out.append(card)
                del cards[key]
        except Exception:
            del cards[key]
    return out


def check_invalidated(state: dict, asset: str, last_close: float) -> list[dict]:
    """เช็คการ์ดค้างของสินทรัพย์นี้: ราคาปิดทะลุไส้กวาดฝั่งตรงข้าม = โครงสร้างพัง ยกเลิกทันที"""
    out = []
    cards = state.get("active_cards") or {}
    for key, card in list(cards.items()):
        if card.get("asset") != asset:
            continue
        lvl = card.get("cancel_level")
        if lvl is None or last_close is None:
            continue
        broken = last_close < lvl if card["side"] == "BUY" else last_close > lvl
        if broken:
            from .data import fmt_price

            card["cancel_reason"] = (
                f"ราคาปิดทะลุจุดยกเลิก {fmt_price(lvl, asset)} (โครงสร้างกลับตัวพังแล้ว)"
            )
            out.append(card)
            del cards[key]
    return out
