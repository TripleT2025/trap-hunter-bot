# -*- coding: utf-8 -*-
"""ปฏิทินข่าวเศรษฐกิจ (Economic Calendar Guard)

ปัญหาที่แก้: เทียนกับดักที่เกิดจาก "ข่าวแรง" (FOMC, CPI, NFP, การประกาศดอกเบี้ย)
ไม่ใช่กับดักจิตวิทยา - เข้าไปแล้ว SL มักถูกกินเพราะราคาสวิงเกินโครงสร้าง

วิธีทำงาน: ดึงปฏิทินสัปดาห์ของ ForexFactory (JSON ฟรี ไม่ต้องมี key)
กรองเฉพาะข่าว "High impact" ของสกุล USD ที่จะตกในกรอบเวลา
[-30 นาทีที่แล้ว, +3 ชม.ข้างหน้า] - ถ้ามี จะติดป้ายเตือนบนการ์ดและหักคะแนนเกรด 1 แต้ม

ถ้าดึงไม่ได้ (เน็ตสะดุด/โดนบล็อก) คืนลิสต์ว่าง - ระบบสแกนปกติ ไม่มีผลข้างเคียง
"""
import sys
from datetime import datetime, timedelta, timezone

FF_URL = "https://nfs.faireconomy.media/ff_calendar_thisweek.json"

_CACHE = {"ts": None, "events": []}


def _fetch_events() -> list[dict]:
    """ดึงปฏิทินพร้อม cache 30 นาที (รอบสแกนทุก 5 นาทีไม่ต้องดึงซ้ำทุกครั้ง)"""
    now = datetime.now(timezone.utc)
    if _CACHE["ts"] and (now - _CACHE["ts"]).total_seconds() < 1800:
        return _CACHE["events"]
    try:
        import requests

        r = requests.get(FF_URL, timeout=15, headers={"User-Agent": "Mozilla/5.0 (trap-hunter-bot)"})
        r.raise_for_status()
        raw = r.json()
    except Exception as e:
        print(f"[calendar] ดึงปฏิทินข่าวไม่สำเร็จ ({e.__class__.__name__}) - ข้าม (ไม่กระทบการสแกน)", file=sys.stderr)
        _CACHE["ts"] = now
        _CACHE["events"] = []
        return []

    events = []
    for item in raw if isinstance(raw, list) else []:
        try:
            if str(item.get("impact", "")).lower() != "high":
                continue
            if str(item.get("country", "")).upper() not in ("USD", "US"):
                continue
            dt = datetime.fromisoformat(str(item["date"]).replace("Z", "+00:00"))
            events.append({"title": str(item.get("title", "?")), "ts": dt})
        except Exception:
            continue
    _CACHE["ts"] = now
    _CACHE["events"] = events
    return events


def nearby_high_impact(hours_ahead: float = 3.0, minutes_back: float = 30.0) -> list[dict]:
    """ข่าวแรงที่กำลังจะมาถึง (หรือเพิ่งตกผ่านไม่นาน) ในกรอบเวลาที่กำหนด"""
    now = datetime.now(timezone.utc)
    lo, hi = now - timedelta(minutes=minutes_back), now + timedelta(hours=hours_ahead)
    out = []
    for ev in _fetch_events():
        if lo <= ev["ts"] <= hi:
            mins = (ev["ts"] - now).total_seconds() / 60
            if mins >= 0:
                out.append({"title": ev["title"], "in_minutes": int(mins), "passed": False})
            else:
                out.append({"title": ev["title"], "in_minutes": int(-mins), "passed": True})
    out.sort(key=lambda x: x["in_minutes"] * (-1 if x["passed"] else 1))
    return out


def news_line(hours_ahead: float = 3.0) -> str | None:
    """ข้อความเตือนสำหรับแปะการ์ด - คืน None ถ้าโล่ง"""
    evs = nearby_high_impact(hours_ahead)
    if not evs:
        return None
    ev = evs[0]
    if ev["passed"]:
        return f"ข่าวแรงเพิ่งตกไป {ev['in_minutes']} นาที: {ev['title']} - ตลาดยังสั่นตามหลังข่าว"
    return f"ระวัง! มีข่าวแรงในอีก {ev['in_minutes']} นาที: {ev['title']}"


def today_summary(max_items: int = 3) -> list[str]:
    """สำหรับสรุปเช้า: ข่าวแรงของวันนี้ (ตามเวลาไทย) หลายรายการ"""
    from .data import bangkok_now

    today = bangkok_now().date()
    out = []
    for ev in _fetch_events():
        if ev["ts"].astimezone(bangkok_now().tzinfo).date() == today:
            out.append(f"{ev['ts'].astimezone(bangkok_now().tzinfo).strftime('%H:%M')} น. {ev['title']}")
        if len(out) >= max_items:
            break
    return out
