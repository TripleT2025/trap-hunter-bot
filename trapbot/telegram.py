# -*- coding: utf-8 -*-
"""ชั้น Telegram: จัดรูปแบบการ์ดสัญญาณ + ยิงเข้าแชทส่วนตัว + รับคำสั่ง + ส่งรูปกราฟ

การ์ด 3 สถานะ (ตามข้อเสนอรวม 3 AI - 26 พ.ย. 2026):
  เข้าได้ตามแผน (ACTION)    - ข้อความเต็ม + เสียงแจ้งเตือน ล็อกหน้าจอรู้ทันทีว่าต้องลุก
  เฝ้าดู (WATCH)            - จังหวะเกือบผ่าน ส่งเงียบ (ไม่มีเสียง) ขึ้นให้ดูทุกจังหวะ ไม่ซ่อน
  ยกเลิก (CANCEL)           - การ์ดที่เคยเรียกเข้าแล้วต้องเลิกความ -> ข้อความ "ใหม่" พร้อมเสียง
"""
import json
import sys

import requests

from .data import fmt_price, fmt_th_time
from .reason import build_reason
from .regime import label as regime_label


def format_message(sig) -> str:
    """การ์ดสถานะ "เข้าได้ตามแผน": สินทรัพย์ ประเภทไม้ ทิศทาง Entry SL TP1-3 เหตุผล เกรด ยืนยันเสริม"""
    direction = "BUY (ซื้อ)" if sig.side == "BUY" else "SELL (ขาย)"

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
    venue_line = sig.extras.get("venue_line")
    expiry_line = sig.extras.get("expiry_line")
    regime = sig.extras.get("regime")

    lines = [
        f"<b>ACTION | เข้าได้ตามแผน | {sig.display_name}</b>",
        f"ประเภทไม้: {sig.trade_type}",
        f"ทิศทาง: <b>{direction}</b>",
        f"เวลาสัญญาณ: {fmt_th_time(sig.ts)}",
        *([f"ตลาด/สัญญา: {venue_line}"] if venue_line else []),
        *([f"<b>{expiry_line}</b>"] if expiry_line else []),
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
        *([f"สภาพตลาด: {regime_label(regime)} (โหมดปรับเกณฑ์อัตโนมัติเปิดอยู่)"] if regime else []),
        *([grade_line] if grade_line else []),
        *([f"ยืนยันข้ามตลาด: {conf_line}"] if conf_line else []),
        *([f"ข่าว: {news_line}"] if news_line else []),
        f"ขนาดไม้: เสี่ยงไม่เกิน 1% ของพอร์ต | ถูก SL = จังหวะพลาด ห้ามตามแก้ตัว",
    ]
    return "\n".join(lines)


def format_watch_message(item: dict) -> str:
    """การ์ดสถานะ "เฝ้าดู": จังหวะเกือบผ่าน - ส่งเงียบ ไม่มีเสียง"""
    side = "ซื้อ" if item["side"] == "BUY" else "ขาย"
    lines = [
        f"<b>WATCH | เฝ้าดู | {item['display']}</b>",
        f"เกิดการกวาดที่ {item['level_kind']} {fmt_price(item['level_price'], item['asset'])} "
        f"แล้วโครงสร้างกลับตัวด้าน{side}",
        f"ยังไม่ยิงเพราะ: {item['fail_th']}",
        "ถ้าแท่งถัดไปยืนยันครบ การ์ดเต็ม (พร้อมเสียง) จะตามมาทันที - ตอนนี้แค่จับตา ยังไม่ต้องลุก",
    ]
    return "\n".join(lines)


def format_cancel_message(card: dict, reason: str) -> str:
    """ข้อความสถานะ "ยกเลิก": ส่งเป็นข้อความใหม่พร้อมเสียง ไม่แก้ข้อความเดิม"""
    side = "ซื้อ" if card["side"] == "BUY" else "ขาย"
    lines = [
        f"<b>CANCEL | ยกเลิกการ์ด | {card['display']}</b>",
        f"การ์ด{side}ที่ยิงไปเมื่อ {fmt_th_time(_parse(card.get('sent_ts')))} ต้องยกเลิกแล้ว",
        f"เหตุผล: {reason}",
        "ถ้ายังไม่ได้เข้าไม้ = ข้ามไม้นี้ไปเลย อย่าไล่ตาม | ถ้าเข้าแล้วและยังไม่ถึงจุดคุ้มทุน พิจารณาตัดตามวินัยของแผนเดิม",
    ]
    return "\n".join(lines)


def _parse(iso: str | None):
    from datetime import datetime

    if not iso:
        return None
    try:
        return datetime.fromisoformat(iso)
    except Exception:
        return None


def _api(token: str, method: str):
    return f"https://api.telegram.org/bot{token}/{method}"


def send(cfg: dict, text: str, silent: bool = False) -> bool:
    """ยิงข้อความเข้า Telegram Bot API (ฟรี)
    silent=True = การ์ดเฝ้าดู ไม่มีเสียงเตือน ขึ้นเงียบ ๆ ให้เห็นเวลาเปิดแชท"""
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
    if silent:
        payload["disable_notification"] = True
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
