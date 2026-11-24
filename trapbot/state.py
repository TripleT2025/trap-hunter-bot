# -*- coding: utf-8 -*-
"""ความจำของระบบ: กันยิงซ้ำ + นับสัญญาณรายวัน

GitHub Actions รันในเครื่องใหม่ทุกครั้ง จึงเก็บสถานะเป็นไฟล์ state/state.json
แล้ว commit กลับเข้า repo ตามขั้นตอนใน workflow (ดู .github/workflows/signal.yml)
"""
import json
import os
from datetime import datetime, timezone

STATE_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "state", "state.json")


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


def level_key(asset: str, side: str, level_price: float) -> str:
    return f"{asset}|{side}|{level_price:.2f}"


def check_and_update(state: dict, asset: str, side: str, level_price: float,
                     eng: dict) -> tuple[bool, str]:
    """เช็คกติกาความถี่ทั้งหมด ผ่าน -> อัปเดตสถานะและคืน (True, เหตุผล) / ไม่ผ่าน -> (False, เหตุผล)"""
    now = datetime.now(timezone.utc)
    today = _today()

    day_count = state["daily"].get(today, 0)
    if day_count >= eng["max_signals_per_day"]:
        return False, "ครบเพดานสัญญาณรายวันแล้ว"

    last_ts = state["last_signal"].get(level_key(asset, side, level_price))
    if last_ts:
        try:
            dt = datetime.fromisoformat(last_ts)
            hours = (now - dt).total_seconds() / 3600
            if hours < eng["cooldown_hours"]:
                return False, f"ระดับเดิมเพิ่งยิงไป {hours:.1f} ชม. ก่อน"
        except Exception:
            pass

    sym_last = max(
        [t for k, t in state["last_signal"].items() if k.startswith(f"{asset}|")] or [None]
    )
    if sym_last:
        try:
            dt = datetime.fromisoformat(sym_last)
            hours = (now - dt).total_seconds() / 3600
            if hours < eng["symbol_cooldown_hours"]:
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
