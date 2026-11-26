# -*- coding: utf-8 -*-
"""ดับเพลิงเงียบ (Watchdog) - กัน "ระบบตายโดยเราไม่รู้"

ที่มา (เหตุการณ์จริง 26 พ.ย. 2026): GitHub Actions ทิ้งตาราง cron เงียบ ๆ
บอทไม่ได้รันทั้งเช้า สรุปเช้าไม่ถูกส่ง แต่ไม่มีใครรู้ตัว
เพราะ "เงียบเพราะไม่มีไม้" กับ "เงียบเพราะระบบตาย" หน้าตาเหมือนกัน

วิธีใช้ (ฟรี ไม่มีค่าใช้จ่าย):
  1. สมัคร healthchecks.io (แผนฟรี) กดสร้าง Check หนึ่งอัน
  2. ตั้ง Period = 10 นาที, Grace = 15 นาที
  3. เอา Ping URL ของ Check ไปใส่ GitHub Secret ชื่อ HEALTHCHECKS_URL
     (ตัวอย่างรูปแบบ: https://hc-ping.com/xxxx)
  4. เสร็จ - ทุกรอบสแกนที่จบปกติ ระบบจะ ping เข้า URL นั้นเอง
     ถ้าเงียบเกินช่วง Grace ทาง Healthchecks จะอีเมล/แจ้งเตือนให้ทันที
     ถ้ารอบไหน main.py ล้ม ระบบจะ ping /fail ให้เห็นเลยว่ารอบนั้นพัง

ไม่ใส่ URL = ข้ามไปเฉย ๆ ไม่มีผลข้างเคียงใด ๆ
"""
import os
import sys

import requests


def get_url(cfg: dict | None = None) -> str:
    """อ่าน URL: config มาก่อน ถ้าว่างใช้ environment HEALTHCHECKS_URL"""
    if cfg and cfg.get("url"):
        return str(cfg["url"]).strip()
    return os.environ.get("HEALTHCHECKS_URL", "").strip()


def ping(ok: bool = True, cfg: dict | None = None) -> bool:
    """แจ้งว่ารอบสแกนจบปกติ (ping) หรือล้ม (ping /fail) - ไม่มี URL หรือล้มคืน False เสมอ"""
    url = get_url(cfg)
    if not url:
        return False
    try:
        target = url.rstrip("/") if ok else url.rstrip("/") + "/fail"
        requests.get(target, timeout=10)
        return True
    except Exception as e:
        print(f"[watchdog] ping ไม่สำเร็จ ({e.__class__.__name__}) - ข้าม", file=sys.stderr)
        return False
