# -*- coding: utf-8 -*-
"""แผนที่กับดักสภาพคล่อง (Liquidity Trap Map)

หัวใจของระบบ: แทนที่จะใช้อินดิเคเตอร์ทั่วไป ระบบจะ "สำรวจว่า SL ของรายย่อยกองอยู่ตรงไหน"
โดยหาระดับราคา 5 ตระกูลที่คนใหญ่ชอบมาแทงกิน:

  1. EQH/EQL  - จุดสูง/จุดต่ำที่ลงตัวเท่ากัน (stop loss รายย่อยเกาะกลุ่มอยู่ตรงนั้น)
  2. PDH/PDL  - จุดสูง/ต่ำของวันก่อนหน้า (ทุกคนมองเห็น = กับดักสาธารณะ)
  3. SESSION  - จุดสูง/ต่ำของช่วงเซสชั่น เอเชีย/ลอนดอน/นิวยอร์ก
  4. ROUND    - เลขสวยกลมๆ (60000, 3550) ที่จิตวิทยาฝูงชนผูกขาด
  5. FVG      - โพรงราคา (Fair Value Gap) ที่ตลาดมักย้อนมาอุด

ระดับที่ "จับต้องได้ยากขึ้นเรื่อยๆ" จะได้คะแนนหนา (touches เยอะ + ยังไม่ถูกอุด)
"""
from dataclasses import dataclass, field
from datetime import timedelta

import numpy as np
import pandas as pd


@dataclass
class Level:
    price: float
    kind: str          # EQH / EQL / PDH / PDL / SESSION_HIGH / SESSION_LOW / ROUND / FVG_BULL / FVG_BEAR
    touches: int = 1   # จำนวนจุดสัมผัส (กองยิ่งใหญ่ยิ่งเยอะ)
    major: bool = False
    note: str = ""

    def label(self) -> str:
        names = {
            "EQH": "จุดสูงเท่ากัน", "EQL": "จุดต่ำเท่ากัน",
            "PDH": "จุดสูงวันก่อน", "PDL": "จุดต่ำวันก่อน",
            "SESSION_HIGH": "สูงสุดเซสชั่น", "SESSION_LOW": "ต่ำสุดเซสชั่น",
            "ROUND": "เลขสวย", "FVG_BULL": "โพรงราคาขาขึ้น", "FVG_BEAR": "โพรงราคาขาลง",
        }
        return names.get(self.kind, self.kind)


def atr(df: pd.DataFrame, n: int = 14) -> pd.Series:
    h, l, c = df["high"], df["low"], df["close"]
    pc = c.shift(1)
    tr = pd.concat([(h - l), (h - pc).abs(), (l - pc).abs()], axis=1).max(axis=1)
    return tr.rolling(n).mean()


def _pivots(df: pd.DataFrame, k: int = 2):
    """หาจุดกลับตัว (swing high/low): แท่งที่สูงสุด/ต่ำสุดกว่าเพื่อนบ้าน k แท่งซ้ายขวา"""
    highs, lows = df["high"].values, df["low"].values
    n = len(df)
    ph, pl = [], []
    for i in range(k, n - k):
        if highs[i] == max(highs[i - k : i + k + 1]):
            ph.append((i, highs[i]))
        if lows[i] == min(lows[i - k : i + k + 1]):
            pl.append((i, lows[i]))
    return ph, pl


def _cluster(values: list[float], tol: float) -> list[tuple[float, int]]:
    """รวมจุดที่อยู่ห่างกันไม่เกิน tol ให้เป็นกองเดียว (คืนราคากอง + จำนวนจุดสัมผัส)"""
    out = []
    for v in sorted(values):
        if out and abs(v - out[-1][0]) <= tol:
            p, cnt = out[-1]
            out[-1] = ((p * cnt + v) / (cnt + 1), cnt + 1)
        else:
            out.append((v, 1))
    return out


def build_levels(df: pd.DataFrame, df_daily: pd.DataFrame | None, price_now: float,
                 round_step: float, eng: dict) -> list[Level]:
    """สร้างแผนที่กับดักทั้งหมดจากเทียนไทม์เฟรมหลัก + เทียนรายวัน"""
    a = atr(df, 14).iloc[-1]
    if not np.isfinite(a) or a <= 0:
        return []
    tol = eng["eq_tolerance_atr"] * a
    min_touch = eng["min_touches_major"]
    levels: list[Level] = []

    # 1) EQH / EQL - กอง SL ของรายย่อย
    ph, pl = _pivots(df, k=2)
    for price, cnt in _cluster([p for _, p in ph], tol):
        if cnt >= min_touch:
            levels.append(Level(price, "EQH", cnt, cnt >= min_touch + 1))
    for price, cnt in _cluster([p for _, p in pl], tol):
        if cnt >= min_touch:
            levels.append(Level(price, "EQL", cnt, cnt >= min_touch + 1))

    # 2) PDH / PDL - จุดสูง/ต่ำของวันก่อน (กับดักสาธารณะ ใครๆ ก็เห็น)
    if df_daily is not None and len(df_daily) >= 2:
        row = df_daily.iloc[-2]
        levels.append(Level(float(row["high"]), "PDH", 3, True))
        levels.append(Level(float(row["low"]), "PDL", 3, True))

    # 3) SESSION H/L - จุดสูง/ต่ำของช่วงเซสชั่นล่าสุดที่ปิดแล้ว
    dfx = df.iloc[-96:].copy()  # ประมาณ 24 ชม. ของเทียน 15 นาที
    session_ranges = [(0, 7, "ASIA"), (7, 13, "LONDON"), (13, 20, "NY")]
    for start_h, end_h, name in session_ranges:
        mask = (dfx["ts"].dt.hour >= start_h) & (dfx["ts"].dt.hour < end_h)
        sub = dfx[mask]
        if len(sub) >= 8:
            levels.append(Level(float(sub["high"].max()), f"SESSION_HIGH", 2, False, name))
            levels.append(Level(float(sub["low"].min()), f"SESSION_LOW", 2, False, name))

    # 4) ROUND NUMBERS - เลขกลมที่จิตวิทยาฝูงชนผูกขาด
    base = round(price_now / round_step) * round_step
    for step_back in (-2, -1, 0, 1, 2):
        levels.append(Level(base + step_back * round_step, "ROUND", 2, False))

    # 5) FVG - โพรงราคาที่ตลาดมักย้อนมาอุด
    h, l = df["high"].values, df["low"].values
    for i in range(max(0, len(df) - 60), len(df) - 2):
        # โพรงขาขึ้น: จุดต่ำแท่งปัจจุบันสูงกว่าจุดสูงของ 2 แท่งก่อน
        if l[i] > h[i - 2]:
            zone_bottom, zone_top = h[i - 2], l[i]
            later = df.iloc[i + 1 :]
            if (later["low"] <= zone_bottom).sum() == 0:  # ยังไม่ถูกอุด
                levels.append(Level((zone_bottom + zone_top) / 2, "FVG_BULL", 1, False))
        # โพรงขาลง
        if h[i] < l[i - 2]:
            zone_bottom, zone_top = h[i], l[i - 2]
            later = df.iloc[i + 1 :]
            if (later["high"] >= zone_top).sum() == 0:
                levels.append(Level((zone_bottom + zone_top) / 2, "FVG_BEAR", 1, False))

    # รวมระดับที่ชนกัน (อยู่ใกล้กัน = กองกับดักหนาขึ้น)
    levels.sort(key=lambda x: x.price)
    merged: list[Level] = []
    for lv in levels:
        if merged and abs(lv.price - merged[-1].price) <= tol:
            main = merged[-1]
            main.touches += lv.touches
            if lv.kind in ("PDH", "PDL") or lv.major:
                main.major = True
            if lv.touches > main.touches and lv.kind not in ("ROUND",):
                main.kind = lv.kind  # ให้ชนิดที่ "จริงจังกว่า" เป็นตัวแทน
        else:
            merged.append(lv)
    return merged


def levels_above(levels: list[Level], price: float) -> list[Level]:
    return sorted([x for x in levels if x.price > price], key=lambda x: x.price)


def levels_below(levels: list[Level], price: float) -> list[Level]:
    return sorted([x for x in levels if x.price < price], key=lambda x: -x.price)
