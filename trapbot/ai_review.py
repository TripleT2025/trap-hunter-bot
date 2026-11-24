# -*- coding: utf-8 -*-
"""ชั้น AI ตรวจสอบรอบสุดท้าย (ไฮบริด) - รันบน OpenRouter

อัลกอริทึมคือผู้คัดจังหวะ ส่วน AI ทำสองอย่าง "เฉพาะตอนมีสัญญาณเกิดขึ้น" (สัญญาณผ่านเกณฑ์ครบ
ไม่บ่อยอยู่แล้ว จึงกินเครดิตน้อยมาก ประมาณไม่กี่เซนต์ต่อเดือน):
  1. ตรวจซ้ำว่าจังหวะสมเหตุสมผล (โหวตอนุมัติ/ยับยั้ง)
  2. เขียนเหตุผลภาษาไทยให้อ่านลื่นกว่าเทมเพลต

การใส่คีย์: ตั้ง Secret ชื่อ OPENROUTER_API_KEY บน GitHub แล้วระบบดึงเองอัตโนมัติ
(รองรับ OPENAI_API_KEY ตกไว้เป็นทางเลือกสำรอง)
ถ้าไม่ใส่คีย์ หรือเรียกไม่สำเร็จ ระบบยังทำงานเต็มรูปแบบด้วยเทมเพลตเหตุผล (ไม่มีผลข้างเคียง)
"""
import json
import os
import sys

import requests

from .reason import build_reason


def get_api_key() -> str:
    """อ่านคีย์จาก environment: เอา OPENROUTER_API_KEY มาก่อน ถ้าไม่มีค่อยใช้ OPENAI_API_KEY"""
    return os.environ.get("OPENROUTER_API_KEY", "") or os.environ.get("OPENAI_API_KEY", "")


def _extract_json(content: str) -> dict:
    """ดึง JSON ออกจากคำตอบโมเดลอย่างทนทาน (บางโมเดลชอบแทรกข้อความหรือ ``` ครอบ)"""
    text = content.strip()
    if text.startswith("```"):
        text = text.strip("`").lstrip("json").strip()
    try:
        return json.loads(text)
    except Exception:
        pass
    # เอาช่วง { ... } ชุดใหญ่สุดในข้อความมาลองอีกครั้ง
    i, j = text.find("{"), text.rfind("}")
    if i >= 0 and j > i:
        return json.loads(text[i : j + 1])
    raise ValueError("ไม่พบ JSON ในคำตอบของโมเดล")


def review_and_rewrite(cfg: dict, sig) -> tuple[bool, str]:
    """คืน (อนุมัติหรือไม่, เหตุผลภาษาไทยฉบับสุดท้าย)"""
    if not cfg.get("enabled"):
        return True, build_reason(sig)
    api_key = cfg.get("api_key", "") or get_api_key()
    if not api_key:
        print("[ai_review] ยังไม่มี OPENROUTER_API_KEY - ใช้เทมเพลตเหตุผล (ระบบยังทำงานปกติ)", file=sys.stderr)
        return True, build_reason(sig)

    from .data import fmt_price

    facts = {
        "asset": sig.display_name,
        "side": sig.side,
        "entry": fmt_price(sig.entry, sig.asset),
        "sl": fmt_price(sig.sl, sig.asset),
        "tp": [fmt_price(sig.tp1, sig.asset), fmt_price(sig.tp2, sig.asset), fmt_price(sig.tp3, sig.asset)],
        "trap_level_kind": sig.level_kind,
        "trap_touches": sig.touches,
        "htf_bias": sig.bias,
        "market_regime": sig.extras.get("regime"),
        "volume_ratio": sig.extras.get("vol_ratio"),
        "wick_pct": sig.extras.get("wick_pct"),
        "reclaim_bars": sig.extras.get("reclaim_bars"),
        "rr_at_tp2": round(sig.rr_tp2, 2),
    }
    prompt = (
        "คุณเป็นผู้คุมคุณภาพสัญญาณเทรดแบบ liquidity sweep reversal\n"
        "ข้อมูลจังหวะ:\n" + json.dumps(facts, ensure_ascii=False) + "\n"
        "ตรวจ 2 ข้อ: (1) SL อยู่หลังไส้กับดักจริงไหม (2) ทิศทางสอดคล้องกับข้อมูลไหม\n"
        'ตอบเป็น JSON เท่านั้น: {"approved": true/false, "reason_thai": "เหตุผล 1-2 ประโยค ภาษาไทย อ่านง่าย '
        "โดยระบุว่าเกิดการแทงหลอกกิน SL แล้วกลับตัว เลขวอลลุ่ม/ไส้เทียนถ้ามี\"}"
    )
    try:
        url = f"{cfg.get('base_url', 'https://openrouter.ai/api/v1').rstrip('/')}/chat/completions"
        headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
            # OpenRouter ขอแค่ระบุแหล่งที่มา (ไม่บังคับ แต่ช่วยดูสถิติบนแดชบอร์ด)
            "HTTP-Referer": "https://github.com/trap-hunter-bot",
            "X-Title": "Trap Hunter Bot",
        }
        r = requests.post(
            url,
            headers=headers,
            json={
                "model": cfg.get("model", "openai/gpt-4o-mini"),
                "messages": [{"role": "user", "content": prompt}],
                "temperature": 0.2,
                "max_tokens": 300,
            },
            timeout=45,
        )
        r.raise_for_status()
        content = r.json()["choices"][0]["message"]["content"]
        data = _extract_json(content)
        approved = bool(data.get("approved", True))
        reason = str(data.get("reason_thai", "")).strip() or build_reason(sig)
        return approved, reason
    except Exception as e:
        print(f"[ai_review] เรียก AI ไม่สำเร็จ ({e.__class__.__name__}) - ใช้เทมเพลตเหตุผลแทน", file=sys.stderr)
        return True, build_reason(sig)
