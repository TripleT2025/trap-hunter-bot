# -*- coding: utf-8 -*-
"""ชั้น Telegram: จัดรูปแบบการ์ดสัญญาณ + ยิงเข้าแชทส่วนตัว"""
import json

import requests

from .data import fmt_price, fmt_th_time
from .reason import build_reason
from .regime import label as regime_label


def format_message(sig) -> str:
    """การ์ดสัญญาณฉบับเต็ม ตามรูปแบบที่กำหนด: สินทรัพย์ ประเภทไม้ ทิศทาง Entry SL TP1-3 เหตุผล"""
    direction = f"BUY (ซื้อ)" if sig.side == "BUY" else "SELL (ขาย)"
    side_th = "ขาขึ้น" if sig.side == "BUY" else "ขาลง"

    def pct(p):
        try:
            d = (p - sig.entry) / sig.entry * 100
            return f"{'+' if d >= 0 else ''}{d:.2f}%"
        except Exception:
            return ""

    def rmult(p):
        try:
            return f"{abs(p - sig.entry) / sig.r:.1f}R"
        except Exception:
            return ""

    lines = [
        f"<b>DAILY SIGNAL | {sig.display_name}</b>",
        f"ประเภทไม้: {sig.trade_type}",
        f"ทิศทาง: <b>{direction}</b>",
        f"เวลาสัญญาณ: {fmt_th_time(sig.ts)}",
        "",
        f"โซนเข้า (Entry): {fmt_price(sig.entry_low, sig.asset)} - {fmt_price(sig.entry_high, sig.asset)}",
        f"จุดตัดขาดทุน (SL): {fmt_price(sig.sl, sig.asset)}  ({pct(sig.sl)})",
        "",
        f"TP1: {fmt_price(sig.tp1, sig.asset)}  ({rmult(sig.tp1)}) | เก็บ 30% แล้วขยับ SL ถึงจุดคุ้มทุน",
        f"TP2: {fmt_price(sig.tp2, sig.asset)}  ({rmult(sig.tp2)}) | เก็บ 40%",
        f"TP3: {fmt_price(sig.tp3, sig.asset)}  ({rmult(sig.tp3)}) | เก็บที่เหลือ",
        "",
        f"<b>เหตุผล (อ่านง่าย):</b> {sig.reason or build_reason(sig)}",
        "",
        f"คุณภาพจังหวะ: R:R ที่ TP2 = 1:{sig.rr_tp2:.1f} | กับดักมี {sig.touches} จุดสัมผัส",
        *([f"สภาพตลาด: {regime_label(reg)} | ระบบปรับเกณฑ์กรองให้พอดีอัตโนมัติ"] if (reg := sig.extras.get("regime")) else []),
        f"ขนาดไม้: เสี่ยงไม่เกิน 1% ของพอร์ต | ถูก SL = จังหวะพลาด ห้ามตามแก้ตัว",
    ]
    return "\n".join(lines)


def send(cfg: dict, text: str) -> bool:
    """ยิงข้อความเข้า Telegram Bot API (ฟรี)"""
    token = cfg.get("bot_token", "")
    chat_id = cfg.get("chat_id", "")
    if not token or not chat_id:
        raise ValueError("ยังไม่ได้ตั้งค่า TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID")
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    payload = {
        "chat_id": chat_id,
        "text": text,
        "parse_mode": cfg.get("parse_mode", "HTML"),
        "disable_web_page_preview": True,
    }
    r = requests.post(url, json=payload, timeout=20)
    if r.status_code != 200:
        raise RuntimeError(f"Telegram ตอบกลับ {r.status_code}: {r.text[:200]}")
    return True
