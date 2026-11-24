# -*- coding: utf-8 -*-
"""เขียน "เหตุผลอย่างย่อ" จากข้อมูลจังหวะจริง - อ่านเข้าใจง่ายใน 1 ประโยคถึง 2 ประโยค"""
from .liquidity import atr


def build_reason(sig) -> str:
    ex = sig.extras
    parts = []
    direction_th = "แทงหลอกทะลุแนวรับ" if sig.side == "BUY" else "ดันหลอกทะลุแนวต้าน"

    parts.append(
        f"ราคา{direction_th}ที่ระดับ{sig.level_note + ' ' if sig.level_note else ''}"
        f"{_kind_th(sig.level_kind)} (จุดที่ SL รายย่อยกองอยู่)"
    )
    if ex.get("wick_pct") is not None and ex.get("wick_pct", 0) >= 50:
        parts.append(f"ไส้เทียนยาวถึง {ex['wick_pct']}% ของแท่ง")
    vol = ex.get("vol_ratio")
    if vol:
        parts.append(f"วอลลุ่มพุ่ง {vol} เท่าของปกติ")
    if ex.get("reclaim_bars", 99) <= 1:
        parts.append("แล้วปิดกลับเข้าโซนทันทีในแท่งเดียว")
    elif ex.get("reclaim_bars", 99) <= 2:
        parts.append("แล้วปิดกลับเข้าโซนภายใน 2 แท่ง")
    if sig.bias in ("UP", "DOWN"):
        bias_th = "ขาขึ้น" if sig.bias == "UP" else "ขาลง"
        parts.append(f"โดยกราฟใหญ่ยังเป็น{bias_th}")
    regime = ex.get("regime")
    if regime == "VOLATILE":
        parts.append("ช่วงนี้ตลาดผันผวนแรง ระบบจึงยกเกณฑ์ทุกชั้นให้เข้มขึ้นกว่าปกติก่อนยอมส่งไม้นี้")
    elif regime == "QUIET":
        parts.append("ช่วงนี้ตลาดเงียบ ระบบคลายเกณฑ์เล็กน้อยและลดเป้าขั้นต่ำให้สมดุลกับความผันผวน")

    text = " ".join(parts) + " -> ตัวหลอกผ่านพ้นแล้ว เข้าตามคลื่นกลับทิศ"
    return text


def _kind_th(kind: str) -> str:
    names = {
        "EQH": "จุดสูงเท่ากัน", "EQL": "จุดต่ำเท่ากัน",
        "PDH": "จุดสูงวันก่อน (PDH)", "PDL": "จุดต่ำวันก่อน (PDL)",
        "SESSION_HIGH": "สูงสุดเซสชั่น", "SESSION_LOW": "ต่ำสุดเซสชั่น",
        "ROUND": "เลขสวยกลม", "FVG_BULL": "โพรงราคา", "FVG_BEAR": "โพรงราคา",
    }
    return names.get(kind, kind)
