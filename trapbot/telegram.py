# -*- coding: utf-8 -*-
"""ชั้น Telegram: จัดรูปแบบการ์ดสัญญาณ + ยิงเข้าแชทส่วนตัว + รับคำสั่ง + ส่งรูปกราฟ"""
import json
import sys

import requests

from .data import fmt_price, fmt_th_time
from .reason import build_reason
from .regime import label as regime_label


def format_message(sig) -> str:
    """การ์ดสัญญาณฉบับเต็ม: สินทรัพย์ ประเภทไม้ ทิศทาง Entry SL TP1-3 เหตุผล เกรด และยืนยันเสริม"""
    direction = f"BUY (ซื้อ)" if sig.side == "BUY" else "SELL (ขาย)"

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

    grade_line = sig.extras.get("grade_line")
    conf_line = sig.extras.get("conf_line")
    news_line = sig.extras.get("news_line")

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
        *([grade_line] if grade_line else []),
        *([f"ยืนยันข้ามตลาด: {conf_line}"] if conf_line else []),
        *([f"ข่าว: {news_line}"] if news_line else []),
        f"ขนาดไม้: เสี่ยงไม่เกิน 1% ของพอร์ต | ถูก SL = จังหวะพลาด ห้ามตามแก้ตัว",
    ]
    return "\n".join(lines)


def _api(token: str, method: str):
    return f"https://api.telegram.org/bot{token}/{method}"


def send(cfg: dict, text: str) -> bool:
    """ยิงข้อความเข้า Telegram Bot API (ฟรี)"""
    token = cfg.get("bot_token", "")
    chat_id = cfg.get("chat_id", "")
    if not token or not chat_id:
        raise ValueError("ยังไม่ได้ตั้งค่า TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID")
    payload = {
        "chat_id": chat_id,
        "text": text,
        "parse_mode": cfg.get("parse_mode", "HTML"),
        "disable_web_page_preview": True,
    }
    r = requests.post(_api(token, "sendMessage"), json=payload, timeout=25)
    if r.status_code != 200:
        raise RuntimeError(f"Telegram ตอบกลับ {r.status_code}: {r.text[:200]}")
    return True


def send_photo(cfg: dict, photo: bytes, caption: str = "") -> bool:
    """ส่งรูป (กราฟ) เข้าแชท พร้อมแคปชันสั้นได้ - คืน True เมื่อสำเร็จ"""
    token = cfg.get("bot_token", "")
    chat_id = cfg.get("chat_id", "")
    if not token or not chat_id:
        raise ValueError("ยังไม่ได้ตั้งค่า TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID")
    data = {"chat_id": chat_id, "parse_mode": cfg.get("parse_mode", "HTML")}
    if caption:
        data["caption"] = caption[:1000]
    files = {"photo": ("chart.png", photo, "image/png")}
    r = requests.post(_api(token, "sendPhoto"), data=data, files=files, timeout=40)
    if r.status_code != 200:
        raise RuntimeError(f"Telegram sendPhoto ตอบกลับ {r.status_code}: {r.text[:200]}")
    return True


def get_updates(cfg: dict, offset: int = 0) -> tuple[list[dict], int]:
    """ดึงข้อความใหม่ (สำหรับคำสั่ง) - คืน (รายการข้อความ, offset ถัดไป)
    ไม่ long-polling เพราะ Actions รันจบต้องไว - โดน 409/webhook ยุ่งก็คืนว่าง QUIET"""
    token = cfg.get("bot_token", "")
    if not token:
        return [], offset
    try:
        r = requests.get(
            _api(token, "getUpdates"),
            params={"offset": offset, "timeout": 0, "allowed_updates": json.dumps(["message"])},
            timeout=20,
        )
        if r.status_code != 200:
            return [], offset
        result = r.json().get("result", [])
        updates = [u.get("message", {}) for u in result if u.get("message")]
        next_offset = offset
        for u in result:
            next_offset = max(next_offset, u.get("update_id", 0) + 1)
        return updates, next_offset
    except Exception as e:
        print(f"[telegram] อ่านข้อความใหม่ไม่สำเร็จ ({e.__class__.__name__})", file=sys.stderr)
        return [], offset
