# -*- coding: utf-8 -*-
"""ชั้นให้เกรดคุณภาพสัญญาณ (A+ / A / B)

คะแนนรวมจากหลักฐานที่จับต้องได้ทั้งหมดของไม้นั้น:
  R:R, จุดสัมผัสกับดัก, ความแรงวอลลุ่ม, ความยาวไส้, ความเร็วการกลืนกลับ,
  ทิศกราฟใหญ่, Confluence ข้ามตลาด, ความปลอดภัยจากข่าวแรง

เกรดที่ออกมาเป็นแค่ "ป้ายบอกคุณภาพ" ไม่ได้กรองไม้ออก - ทุกไม้ที่ส่งผ่านชั้นยืนยันครบอยู่แล้ว
ผู้ใช้เลือกได้ว่าจะเทรดเฉพาะเกรดไหน
"""


def grade_signal(sig, conf_status: str | None, news_line: str | None) -> tuple[str, int, list[str]]:
    """คืน (เกรด, คะแนน, รายการเหตุผลย่อยภาษาไทย)"""
    score = 0
    notes: list[str] = []

    rr = sig.rr_tp2
    if rr >= 2.0:
        score += 2
        notes.append(f"R:R สวย 1:{rr:.1f} (+2)")
    elif rr >= 1.5:
        score += 1
        notes.append(f"R:R ผ่านเกณฑ์ 1:{rr:.1f} (+1)")

    if sig.touches >= 3:
        score += 1
        notes.append(f"กับดักถูกแตะ {sig.touches} ครั้ง กอง SL ใหญ่ (+1)")

    vol = sig.extras.get("vol_ratio")
    if vol is not None:
        if vol >= 2.5:
            score += 1
            notes.append(f"วอลลุ่มหนัก {vol} เท่า (+1)")
        elif vol >= 1.8:
            notes.append(f"วอลลุ่ม {vol} เท่า ผ่านเกณฑ์")

    wick = sig.extras.get("wick_pct")
    if wick is not None and wick >= 65:
        score += 1
        notes.append(f"ไส้ยาว {wick}% ถูกดันกลับชัดเจน (+1)")

    if sig.extras.get("fast_reclaim"):
        score += 1
        notes.append("กลืนกลับทันทีในแท่งเดียว ของจริง (+1)")

    aligned = (sig.side == "BUY" and sig.bias == "UP") or (sig.side == "SELL" and sig.bias == "DOWN")
    if aligned:
        score += 1
        notes.append("ทิศตรงกับกราฟใหญ่ (+1)")

    if conf_status == "aligned":
        score += 2
        notes.append("ตลาดคู่ยืนยันทิศทาง (+2)")
    elif conf_status == "conflict":
        score -= 1
        notes.append("ตลาดคู่ขัดแย้งกับไม้นี้ (-1)")

    if news_line:
        score -= 1
        notes.append("มีข่าวแรงใกล้ตัว ระวังสวิงเกินปกติ (-1)")
    else:
        notes.append("ไม่มีข่าวแรงราว 3 ชม. ข้างหน้า (+0)")

    if score >= 6:
        return "A+", score, notes
    if score >= 4:
        return "A", score, notes
    return "B", score, notes
