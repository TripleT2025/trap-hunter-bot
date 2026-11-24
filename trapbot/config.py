# -*- coding: utf-8 -*-
"""โหลดไฟล์ตั้งค่า config.yaml + อ่านค่าจาก environment variables"""
import os

import yaml

DEFAULT_CONFIG_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "config.yaml")


def load_config(path: str = None) -> dict:
    path = path or DEFAULT_CONFIG_PATH
    with open(path, "r", encoding="utf-8") as f:
        cfg = yaml.safe_load(f)

    # environment variables มีน้ำหนักสูงกว่าไฟล์ (GitHub Actions ใส่ secret ผ่าน env)
    cfg.setdefault("telegram", {})
    cfg["telegram"]["bot_token"] = os.environ.get("TELEGRAM_BOT_TOKEN", "")
    cfg["telegram"]["chat_id"] = os.environ.get("TELEGRAM_CHAT_ID", "")

    cfg.setdefault("ai", {})
    # OpenRouter key มาก่อน (ตั้ง Secret ชื่อ OPENROUTER_API_KEY บน GitHub)
    # ถ้าไม่มีจะไล่ไปใช้ OPENAI_API_KEY เก่าได้ แล้วสุดท้ายใช้ค่าใน config.yaml เอง
    cfg["ai"]["api_key"] = os.environ.get("OPENROUTER_API_KEY", "") or os.environ.get("OPENAI_API_KEY", "")

    cfg.setdefault("runtime", {})
    cfg["runtime"]["dry_run"] = str(os.environ.get("DRY_RUN", "")).lower() in ("1", "true", "yes")

    return cfg
