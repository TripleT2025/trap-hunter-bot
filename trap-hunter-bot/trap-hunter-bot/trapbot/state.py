# -*- coding: utf-8 -*-
"""ความจำของระบบ: กันยิงซ้ำ + นับสัญญาณรายวัน

GitHub Actions รันในเครื่องใหม่ทุกครั้ง จึงเก็บสถานะเป็นไฟล์ state/state.json
แล้ว commit กลับเข้า repo ตามขั้นตอนใน workflow (ดู .github/workflows/signal.yml)

การกันซ้ำรุ่นใหม่ (รับจากข้อเสนอ 3 AI - 26 พ.ย. 2026): "รหัสเหตุการณ์ถาวร"
รหัส = ตลาด + ฝั่ง + ระดับราคา + เวลาแท่งที่กวาด (เช่น XAUUSD|BUY|3550.0|202611261315)
เหตุการณ์เดียวกัน (แท่งกวาดเดียวกัน) ส่งได้ครั้งเดียวตลอดชีวิต - ตรวจย้อนหลังได้ ฟรี ไม่ผ่าน AI
จังหวะ "ใหม่" (แท่งกวาดใหม่) ที่ระดับเดิม ยิงได้ทันทีไม่มี cooldown มาขวาง
"""
import json
import os
from datetime import datetime, timezone

STATE_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "state", "state.json")

EVENT_KEEP = 300


def _today() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


def load() -> dict:
    try:
        with open(STATE_PATH, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {"last_signal": {}, "daily": {}}


def save(state: dict) -> None:
    os.makedirs(os.path.dirname(STATE_PATH), exist_ok=True)
    with open(STATE_PATH, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=1)


def ensure_keys(state: dict) -> None:
    """เติมคีย์ใหม่ให้ไฟล์ state เก่า (ที่สร้างก่อนการอัปเดต 26 พ.ย.) ใช้ได้ทันที"""
    for key, default in (
        ("events", {}),            # รหัสเหตุการณ์ที่ส่งแล้ว -> เวลาที่ส่ง
        ("watch_events", {}),      # รหัสเหตุการณ์ที่ส่งการ์ดเฝ้าดูไปแล้ว
        ("missed", []),            # การ์ด "พบแต่ส่งไม่ทัน"
        ("watch_log", []),         # การ์ดเฝ้าดูล่าสุด
        ("active_cards", {}),      # การ์ดที่ยิงแล้วกำลังถูกเฝ้าติดตาม
        ("daily_ledger", {}),      # สมุดกิจวัน (activity.py)
        ("scan_ctx", {}),          # บริบทสแกนล่าสุด (ใช้ตอบ /status /why)
        ("last_ok_scan", None),    # รอบล่าสุดที่ระบบหายใจสำเร็จ
        ("loss_stop_date", None),  # วันที่สวิตช์กันขาดทุนเกินเพดานทำงานล่าสุด
        ("last_signal", {}),
        ("daily", {}),
        ("levels_cache", {}),
        ("regime_cache", {}),
        ("journal", []),
    ):
        state.setdefault(key, default)


def level_key(asset: str, side: str, level_price: float) -> str:
    return f"{asset}|{side}|{level_price:.2f}"


def event_key(asset: str, side: str, level_price: float, sweep_ts) -> str:
    """รหัสเหตุการณ์ถาวร: ตลาด + ฝั่ง + ระดับ + เวลาแท่งกวาด (ถึงนาที)"""
    try:
        stamp = sweep_ts.strftime("%Y%m%d%H%M")
    except Exception:
        stamp = "unknown"
    return f"{asset}|{side}|{level_price:.2f}|{stamp}"


def check_and_update(state: dict, asset: str, side: str, level_price: float,
                     eng: dict, sweep_ts=None) -> tuple[bool, str]:
    """เช็คกติกาความถี่ทั้งหมด ผ่าน -> อัปเดตสถานะและคืน (True, เหตุผล) / ไม่ผ่าน -> (False, เหตุผล)

    ลำดับ: เพดานรายวัน -> รหัสเหตุการณ์ถาวร (แท่งกวาดเดียวกันส่งครั้งเดียว)
    -> cooldown ระดับเดิม (ใช้เฉพาะเมื่อตั้ง cooldown_hours > 0)
    """
    now = datetime.now(timezone.utc)
    today = _today()

    day_count = state["daily"].get(today, 0)
    if day_count >= eng["max_signals_per_day"]:
        return False, "ครบเพดานสัญญาณรายวันแล้ว"

    if eng.get("event_dedup", True) and sweep_ts is not None:
        key = event_key(asset, side, level_price, sweep_ts)
        if state["events"].get(key):
            return False, "เหตุการณ์นี้ (แท่งกวาดเดียวกัน) ส่งการ์ดไปแล้ว"
        state["events"][key] = now.isoformat()
        if len(state["events"]) > EVENT_KEEP:
            keep = sorted(state["events"].items(), key=lambda x: x[1])[-EVENT_KEEP // 2:]
            state["events"] = dict(keep)

    cooldown = float(eng.get("cooldown_hours", 0) or 0)
    if cooldown > 0:
        last_ts = state["last_signal"].get(level_key(asset, side, level_price))
        if last_ts:
            try:
                dt = datetime.fromisoformat(last_ts)
                hours = (now - dt).total_seconds() / 3600
                if hours < cooldown:
                    return False, f"ระดับเดิมเพิ่งยิงไป {hours:.1f} ชม. ก่อน"
            except Exception:
                pass

    sym_last = max(
        [t for k, t in state["last_signal"].items() if k.startswith(f"{asset}|")] or [None]
    )
    sym_cd = float(eng.get("symbol_cooldown_hours", 0) or 0)
    if sym_last and sym_cd > 0:
        try:
            dt = datetime.fromisoformat(sym_last)
            hours = (now - dt).total_seconds() / 3600
            if hours < sym_cd:
                return False, f"{asset} เพิ่งมีสัญญาณไป {hours:.1f} ชม. ก่อน"
        except Exception:
            pass

    # ผ่านทุกกติกา -> บันทึก
    state["last_signal"][level_key(asset, side, level_price)] = now.isoformat()
    # เก็บ key ล่าสุดไว้ไม่เกิน 200 อัน กันไฟล์โต
    if len(state["last_signal"]) > 200:
        keep = sorted(state["last_signal"].items(), key=lambda x: x[1])[-100:]
        state["last_signal"] = dict(keep)
    state["daily"][today] = day_count + 1
    # ล้างวันเก่า
    state["daily"] = {k: v for k, v in state["daily"].items() if k >= today}
    return True, "ผ่านกติกาความถี่"


def seen_event(state: dict, sweep_ts, asset: str, side: str, level_price: float) -> bool:
    """เหตุการณ์นี้ (หรือการ์ดเฝ้าดูของเหตุการณ์นี้) เคยส่งไปแล้วหรือยัง - ใช้กันซ้ำการ์ดเฝ้าดู"""
    key = event_key(asset, side, level_price, sweep_ts)
    return bool(state["events"].get(key)) or bool((state.get("watch_events") or {}).get(key))


def mark_watch_event(state: dict, sweep_ts, asset: str, side: str, level_price: float) -> None:
    key = event_key(asset, side, level_price, sweep_ts)
    events = state.setdefault("watch_events", {})
    events[key] = datetime.now(timezone.utc).isoformat()
    if len(events) > EVENT_KEEP:
        keep = sorted(events.items(), key=lambda x: x[1])[-EVENT_KEEP // 2:]
        state["watch_events"] = dict(keep)
