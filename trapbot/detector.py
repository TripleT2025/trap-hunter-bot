# -*- coding: utf-8 -*-
"""เครื่องตรวจจับจังหวะกับดักกลับด้าน (Sweep & Reclaim Detector)

ปรัชญา: ระบบไม่เดาราคา แต่ "รอให้กับดักถูกพิสูจน์แล้วจบก่อน" แล้วค่อยแทงตามคนใหญ่
จังหวะที่รอคือ 3 องก์:

  องก์ 1 SWEEP   - ราคาแทงทะลุระดับกับดัก (กิน SL รายย่อย) ด้วยไส้เทียน + วอลลุ่มผิดปกติ
  องก์ 2 RECLAIM - ปิดเทียนกลับเข้าโซนเดิมภายใน 3 แท่ง (เร็ว = คนใหญ่ดูดของ ไม่ใช่เทรนด์ล้ม)
  องก์ 3 CONFIRM - แรงซื้อสุทธิ (Delta) ไม่ยอมตามราคาลง + ทิศทางกราฟใหญ่ไม่คัดค้าน

ทุกเงื่อนไขต้องผ่านครบ (AND) พร้อมกัน ถึงจะยิงสัญญาณ - นี่คือสาเหตุที่ได้ 0-2 ไม้/วัน

อัปเดต 26 พ.ย. 2026: จังหวะที่ผ่านโครงสร้าง (กวาด+กลับตัว) แต่ "เกือบผ่าน" ชั้นยืนยัน
(พลาดชั้นเดียว) ไม่ถูกทิ้งเงียบ ๆ อีกต่อไป - ถูกคืนมาเป็น near_misses เพื่อส่งการ์ดเฝ้าดู
แบบเงียบ (ไม่มีเสียง) ตามข้อเสนอ 3 AI: ขึ้นให้ดูทุกจังหวะ ไม่ซ่อน แต่ไม่รบกวน
"""
import numpy as np
import pandas as pd

from .liquidity import Level, atr, levels_above, levels_below


def htf_bias(df_htf: pd.DataFrame) -> str:
    """ทิศทางรวมจากกราฟใหญ่: EMA20 ตัด EMA50 + ตำแหน่งราคา (ขึ้น/ลง/เป็นกลาง)"""
    close = df_htf["close"]
    ema20 = close.ewm(span=20, adjust=False).mean().iloc[-1]
    ema50 = close.ewm(span=50, adjust=False).mean().iloc[-1]
    px = close.iloc[-1]
    if ema20 > ema50 and px > ema20:
        return "UP"
    if ema20 < ema50 and px < ema20:
        return "DOWN"
    return "NEUTRAL"


def _delta_series(df: pd.DataFrame) -> pd.Series:
    """แรงซื้อสุทธิต่อแท่ง = 2*taker_buy - volume (ประมาณจาก taker buy volume ของ Binance)"""
    return 2 * df["taker_buy"] - df["volume"]


def _find_sweep(df: pd.DataFrame, level: Level, a: float, eng: dict, side: str):
    """ค้นหาองก์ 1+2 ล่าสุดบนระดับที่กำหนด - คืน (sweep_idx, reclaim_idx) หรือ None

    side='BUY' คือแทงทะลุกับดักฝั่งล่าง (EQL/แนวรับ) แล้วยันกลับขึ้น -> สัญญาณซื้อ
    side='SELL' คือดันทะลุกับดักฝั่งบน (EQH/แนวต้าน) แล้วโดนกดกลับลง -> สัญญาณขาย
    ประเมินเฉพาะเทียนที่ "ปิดแล้ว" (ไม่เอาแท่งที่กำลังวิ่ง)
    """
    dfc = df.iloc[:-1]  # ตัดแท่งที่ยังไม่ปิด
    if len(dfc) < 60:
        return None
    lows, highs, closes, opens = dfc["low"].values, dfc["high"].values, dfc["close"].values, dfc["open"].values
    n = len(dfc)
    scan_from = max(0, n - (eng["reclaim_window"] + 3))

    for s in range(n - 1, scan_from - 1, -1):
        if side == "BUY":
            pierced = lows[s] < level.price - eng["min_pierce_atr"] * a
            back_above = closes[s] > level.price
        else:
            pierced = highs[s] > level.price + eng["min_pierce_atr"] * a
            back_below = closes[s] < level.price
        if not pierced:
            continue

        # ตบไส้: ไส้ต้องยาวอย่างน้อย wick_min_ratio ของช่วงเทียน
        rng = highs[s] - lows[s]
        if rng <= 0:
            continue
        if side == "BUY":
            wick = (min(opens[s], closes[s]) - lows[s]) / rng
        else:
            wick = (highs[s] - max(opens[s], closes[s])) / rng
        single_bar = back_above if side == "BUY" else back_below
        if wick < eng["wick_min_ratio"] and not single_bar:
            continue  # กินลึกแต่ไม่ตบไส้ ไม่ใช่จังหวะหลอกแบบเราตามหา

        # RECLAIM: ปิดกลับเข้าโซนภายใน reclaim_window แท่ง (แท่ง sweep เองนับด้วย)
        r_end = min(n - 1, s + eng["reclaim_window"])
        for r in range(s, r_end + 1):
            if side == "BUY" and closes[r] > level.price:
                return s, r
            if side == "SELL" and closes[r] < level.price:
                return s, r
    return None


# ข้อความเหตุผลของแต่ละชั้นยืนยัน (ใช้บนการ์ดเฝ้าดู บอกว่า "ยังไม่ยิงเพราะอะไร")
_FAIL_TH = {
    "volume": "วอลลุ่มยังไม่หนักพอ (รอแท่งยืนยันหนักกว่านี้)",
    "guard": "เทียนกวาดใหญ่ผิดปกติ (น่าจะมาจากข่าว ระบบข้ามเทียนยักษ์)",
    "delta": "แรงซื้อ-ขายสุทธิไม่ยืนยันทิศกลับ",
    "reclaim": "กลับตัวช้าไป (ไม่ใช่การพุ่งกลับทันทีแบบของจริง)",
    "trend": "สวนกราฟใหญ่ (ระบบตั้งไม่ให้สวนเทรนด์)",
}


def detect(df: pd.DataFrame, df_htf: pd.DataFrame, levels: list[Level], eng: dict,
           delta_enabled: bool) -> dict:
    """สแกนเทียนที่ปิดแล้วล่าสุด - คืน {"signals": จังหวะเต็มผ่านครบชั้น, "near_misses": เกือบผ่าน (พลาดชั้นเดียว)}"""
    a = float(atr(df, 14).iloc[-1])
    if not np.isfinite(a) or a <= 0:
        return {"signals": [], "near_misses": []}
    bias = htf_bias(df_htf)
    vol_sma = df["volume"].rolling(20).mean()
    delta = _delta_series(df) if delta_enabled else None

    # เผื่อค่า min pierce ที่ไม่ได้อยู่ใน config (ใช้ 5% ของ ATR)
    eng = dict(eng)
    eng.setdefault("min_pierce_atr", 0.05)

    found: list[dict] = []
    near_misses: list[dict] = []
    for level in levels:
        for side in ("BUY", "SELL"):
            res = _find_sweep(df, level, a, eng, side)
            if res is None:
                continue
            s, r = res
            dfc = df.iloc[:-1]
            base = {
                "side": side,
                "level": level,
                "sweep_idx": s,
                "reclaim_idx": r,
                "atr": a,
                "bias": bias,
                "df": df,
            }

            # ประเมินทุกชั้นยืนยัน แล้วจดว่าพลาดชั้นไหน (จังหวะเกือบผ่าน = พลาดชั้นเดียว)
            fails: list[str] = []

            # ---- ชั้นยืนยัน 1: วอลลุ่มต้องมาสนับสนุนจริง ----
            vol_ok = np.isfinite(vol_sma.iloc[s]) and vol_sma.iloc[s] > 0 and \
                dfc["volume"].iloc[s] >= eng["volume_mult"] * vol_sma.iloc[s]
            if not vol_ok:
                fails.append("volume")

            # ---- ชั้นยืนยัน 2: กันข่าวแรงเกิน (เทียนยักษ์ = เดิมพันคลาดเคลื่อน) ----
            rng = float(dfc["high"].iloc[s] - dfc["low"].iloc[s])
            if rng > eng["vol_guard_atr"] * a:
                fails.append("guard")

            # ---- ชั้นยืนยัน 3: แรงซื้อ/ขายสุทธิยืนยัน หรือ (ทอง) ต้องกลับตัวเร็วพิเศษ ----
            if delta_enabled and delta is not None:
                d_s = delta.iloc[s]
                d_r = delta.iloc[r]
                if side == "BUY":
                    delta_ok = (d_s > 0) or (d_r > 0)  # ราคาลงแต่แรงซื้อสุทธิยังบวก = มีคนดูดของ
                else:
                    delta_ok = (d_s < 0) or (d_r < 0)
                if not delta_ok:
                    fails.append("delta")
            else:
                # ทอง (ไม่มีข้อมูล delta): ต้อง reclaim เร็วเป็นพิเศษ ภายใน 1-2 แท่ง
                if (r - s) > 1:
                    fails.append("reclaim")

            # ---- ชั้นยืนยัน 4: ทิศทางกราฟใหญ่ไม่คัดค้าน ----
            trend_ok = eng["allow_counter_trend"] or not (
                (side == "BUY" and bias == "DOWN") or (side == "SELL" and bias == "UP")
            )
            if not trend_ok:
                fails.append("trend")

            if fails:
                if len(fails) == 1:
                    item = dict(base)
                    item["fail"] = fails[0]
                    item["fail_th"] = _FAIL_TH.get(fails[0], fails[0])
                    near_misses.append(item)
                continue

            found.append(base)
    return {"signals": found, "near_misses": near_misses}
