# -*- coding: utf-8 -*-
"""ชั้นยืนยันข้ามตลาด (Cross-Market Confluence)

แนวคิด: ไม้ที่ดีไม่ได้มาง่าย ๆ ในกราฟตัวเองอย่างเดียว - ตลาดอื่นควรยอมรับทิศทางเดียวกัน

  - BTC/SOL/XRP : ดู Funding rate ของฟิวเจอร์สคอยน์นั้น
      funding ต่ำ/ติดลบ = ฝั่ง Long ไม่แน่นเกิน -> เอื้อต่อไม้ BUY (ยังมีที่ว่างขึ้น)
      funding สูง = ฝูงชนกอง Long หนา -> BUY เสี่ยงถูกตีขา, SELL กลับมีโอกาส
      พร้อมรายงานการไหลเข้า-ออกของเงิน (Open Interest 24 ชม.)
  - ทอง / เงิน / NAS100 : ดูดัชนีดอลลาร์ DXY (เงินดอลลาร์แรง = กดทอง-เงิน-หุ้นเทค)
      BUY ทอง เมื่อ DXY อ่อน (DOWN) = สอดคล้อง / BUY ทอง ตอน DXY แรง (UP) = ขัดแย้ง
  - น้ำมัน : ไม่มีตัวเทียบฟรีที่เชื่อถือได้ -> ข้าม (neutral)

ทุกอย่างคืนเป็นข้อความบรรทัดเดียวอ่านง่าย + สถานะ aligned/conflict/neutral
ถ้าดึงข้อมูลไม่ได้ คืน None (ไม่มีผลต่อการยิงสัญญาณ - graceful)
"""


def coin_confluence(symbol: str, side: str) -> dict | None:
    """ยืนยันด้วย funding + OI สำหรับคอยน์ (Binance Futures ฟรี ไม่ต้องมี key)"""
    from .data import fetch_funding_rate, fetch_oi_change

    try:
        funding = fetch_funding_rate(symbol)
        oi_chg = fetch_oi_change(symbol)
    except Exception:
        return None
    if funding is None:
        return None

    f_pct = funding * 100  # แสดงเป็น %
    parts = [f"Funding {f_pct:+.3f}%"]
    status = "neutral"

    # เกณฑ์อ่านง่าย: 0.01% ครึ่งทางของ baseline 0.01%/8ชม. ที่ Binance ตั้ง
    if side == "BUY":
        if funding <= 0:
            status = "aligned"
            parts.append("ฝั่ง Long ไม่แน่น ที่ว่างยังเปิด")
        elif funding >= 0.0005:
            status = "conflict"
            parts.append("ฝูง Long กองหนา ระวังถูกตีขา")
        else:
            parts.append("ฝั่ง Long ปกติ")
    else:
        if funding >= 0.0005:
            status = "aligned"
            parts.append("ฝูง Long แน่น เอื้อไม้ SELL")
        elif funding <= 0:
            status = "conflict"
            parts.append("Short แน่นอยู่แล้ว ระวัง short squeeze")
        else:
            parts.append("ฝั่ง Short ปกติ")

    if oi_chg is not None:
        flow = "เงินไหลเข้า" if oi_chg > 0 else "เงินไหลออก"
        parts.append(f"OI 24ชม. {oi_chg:+.2f}% ({flow})")

    return {"status": status, "line": " | ".join(parts)}


def dollar_confluence(dxy_trend: str | None, side: str) -> dict | None:
    """ยืนยันด้วยทิศ DXY สำหรับทอง/เงิน/NAS100 (ดอลลาร์ตรงข้ามกลุ่มนี้)"""
    if not dxy_trend:
        return None
    th = {"UP": "ดอลลาร์กำลังแรง (5 วัน +)", "DOWN": "ดอลลาร์กำลังอ่อน (5 วัน -)", "FLAT": "ดอลลาร์นิ่ง"}
    if side == "BUY":
        status = "aligned" if dxy_trend in ("DOWN", "FLAT") else "conflict"
    else:
        status = "aligned" if dxy_trend in ("UP", "FLAT") else "conflict"
    if dxy_trend == "FLAT":
        status = "neutral"
    return {"status": status, "line": f"DXY {th[dxy_trend]}"}


def check(sig, dxy_trend: str | None) -> dict | None:
    """เลือกวิธียืนยันให้เหมาะกับสินทรัพย์ - คืน {status, line} หรือ None"""
    try:
        if sig.asset in ("BTCUSDT", "SOLUSDT", "XRPUSDT"):
            return coin_confluence(sig.asset, sig.side)
        if sig.asset in ("XAUUSD", "XAGUSD", "NAS100"):
            return dollar_confluence(dxy_trend, sig.side)
        return None  # น้ำมัน ฯลฯ ไม่มีตัวเทียบ - ไม่ต้องบังคับ
    except Exception:
        return None


STATUS_TH = {"aligned": "สอดคล้อง", "conflict": "ขัดแย้ง", "neutral": "กลาง ๆ"}
