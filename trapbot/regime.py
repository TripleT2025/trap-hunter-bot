# -*- coding: utf-8 -*-
"""ชั้นวัดสภาพตลาดและปรับความเข้มกรองอัตโนมัติ (Market Regime Adaptation)

คำถามที่เป็นหัวใจ: กติกาค่าคงที่หนึ่งชุด ใช้กับตลาดสองสภาพไม่ได้ -

  ช่วงผันผวนแรง (ข่าวใหญ่ / ตลาดสั่นแรง): เทียนกับดักเกิดง่ายและมีเสียงรบกวนเยอะ
    -> ต้องยกเกณฑ์ "ของจริง" ให้สูงขึ้น: วอลลุ่มยืนยันต้องหนักกว่า, ไส้ต้องยาวกว่า,
       เป้าขั้นต่ำต้องไกลขึ้น (คุ้มกับความเสี่ยงที่โต), SL ห่างไส้มากขึ้น
       (ตลาดชอบพาดกลับไปมาลึกกว่าไส้เดิม) และยกเพดานกรอง "เทียนยักษ์จากข่าว" ขึ้นด้วย
       (ไม่งั้นช่วงผันผวนจะโดนกรองทิ้งหมดจนไม่มีสัญญาณเลย)

  ช่วงตลาดเงียบ (ความผันผวนต่ำ): จังหวะหลอกเกิดน้อยและเบา
    -> คลายเกณฑ์ลงเล็กน้อยให้ยังจับจังหวะได้ และลดเป้าขั้นต่ำให้สมดุลกับความผันผวน
       (ราคาเดินแคบ เป้าไกลเกินจริงไม่สมเหตุสมผล)

วิธีวัด (โปร่งใส อธิบายได้ ไม่ใช่กล่องดำ): เทียบ ATR(14) ของกราฟ 1 ชม. "ตอนนี้"
กับค่ามัธยฐานย้อนหลัง 100 แท่งของตัวเอง
  ratio < 0.70        -> QUIET    (ตลาดเงียบ - คลายเกณฑ์)
  ratio 0.70 - 1.50   -> NORMAL   (ปกติ - ใช้ค่าพื้นฐาน)
  ratio > 1.50        -> VOLATILE (ผันผวนแรง - เข้มกรองทุกชั้น)
คำนวณจากราคาจริงทุกรอบสแกน ไม่เรียก AI เพิ่ม ไม่มีต้นทุน
ปิดฟีเจอร์นี้ได้ด้วย regime.enabled: false ใน config.yaml
"""
from .liquidity import atr

# ค่าที่จะถูก "ทับ" ลงบนค่าพื้นฐานของ engine เมื่อเข้าสภาพตลาดนั้นๆ (ค่าที่ไม่ระบุ = ใช้ค่าพื้นฐาน)
REGIME_PROFILES = {
    "QUIET": {
        "volume_mult": 1.5,      # คลายเกณฑ์วอลลุ่มลง (ตลาดเงียบ วอลลุ่มธรรมดาก็ถือว่าโดดเด่นแล้ว)
        "min_rr_tp2": 1.2,       # ลดเป้าขั้นต่ำให้สมดุลกับความผันผวนที่หดตัว
        "wick_min_ratio": 0.45,  # ไส้ยาวน้อยกว่าเดิมนิดเดียวก็นับเป็นการหลอก
        "sl_atr_mult": 0.30,     # SL ห่างไส้ขึ้นนิด กันตลาดเกาะกลับมายั่วขัดสันหลัง
    },
    "NORMAL": {},
    "VOLATILE": {
        "volume_mult": 2.2,      # วอลลุ่มต้องหนักกว่าปกติชัดๆ ถึงจะเป็นของจริง ไม่ใช่เสียงรบกวน
        "min_rr_tp2": 1.8,       # เป้าต้องไกลขึ้น คุ้มกับความเสี่ยงที่โตขึ้นตามตลาด
        "wick_min_ratio": 0.55,  # ไส้ต้องยาวกว่าเดิม - การหลอกต้องหลอกชัดๆ
        "sl_atr_mult": 0.40,     # SL ห่างไส้มากขึ้น กันตลาดพาดลึกกว่าไส้เดิม
        "vol_guard_atr": 4.5,    # ยกเพดานกรอง "เทียนยักษ์จากข่าว" ขึ้น ไม่งั้นช่วงผันผวนโดนกรองหมด
    },
}

REGIME_TH = {"QUIET": "เงียบ", "NORMAL": "ปกติ", "VOLATILE": "ผันผวนแรง"}


def classify(df_htf, cfg: dict | None = None) -> dict:
    """วัดสภาพตลาดปัจจุบันจาก ATR ของกราฟใหญ่เทียบค่ากลางของตัวเอง - คืน (ชื่อสภาพ, อัตราส่วน)"""
    cfg = cfg or {}
    if cfg.get("enabled") is False:
        return {"regime": "NORMAL", "ratio": 1.0}
    try:
        s = atr(df_htf, 14).dropna().iloc[-150:]
        if len(s) < 60:
            return {"regime": "NORMAL", "ratio": 1.0}
        now = float(s.iloc[-1])
        med = float(s.rolling(100, min_periods=60).median().iloc[-1])
        if med <= 0 or not now > 0:
            return {"regime": "NORMAL", "ratio": 1.0}
        ratio = now / med
    except Exception:
        return {"regime": "NORMAL", "ratio": 1.0}
    quiet = float(cfg.get("quiet_ratio", 0.70))
    volatile = float(cfg.get("volatile_ratio", 1.50))
    if ratio < quiet:
        regime = "QUIET"
    elif ratio > volatile:
        regime = "VOLATILE"
    else:
        regime = "NORMAL"
    return {"regime": regime, "ratio": round(ratio, 2)}


def apply_regime(eng: dict, reg: dict) -> dict:
    """คัดลอกค่า engine แล้วทับด้วยโปรไฟล์ของสภาพตลาดปัจจุบัน - ค่าเดิมไม่ถูกแก้"""
    eng2 = dict(eng)
    eng2.update(REGIME_PROFILES.get(reg.get("regime", "NORMAL"), {}))
    eng2["regime"] = reg.get("regime", "NORMAL")
    eng2["regime_ratio"] = reg.get("ratio", 1.0)
    return eng2


def label(regime: str) -> str:
    """ชื่อสภาพตลาดภาษาไทย สำหรับแสดงในการ์ดและ log"""
    return REGIME_TH.get(regime, regime)
