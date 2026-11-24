# -*- coding: utf-8 -*-
"""คำนวณจุดเข้า (Entry) จุดตัดขาดทุน (SL) และเป้าทำกำไร 3 ระดับ (TP1-TP3)

ตรรกะการวางจุดทั้งหมดผูกกับ "โครงสร้างจริงของตลาด" ไม่ใช่ตัวเลขลอยๆ:

  Entry   : ราคาปิดของเทียนยืนยัน (โซนกว้าง 0.15 ATR รอบราคานั้น)
  SL      : เลยไส้สุดท้ายของกับดักออกไป 0.25 ATR
            - จุดนี้ปลอดภัยเชิงโครงสร้าง เพราะตลาดเพิ่งพิสูจน์ไปแล้วว่า
              "ลงลึกสุดแบบนั้นแล้วไม่ยอมอยู่" คนใหญ่จึงไม่มีเหตุผลจะดันกลับมาอีก
  TP1     : กับดักฝั่งตรงข้ามที่ใกล้ที่สุด (หรือ 1R) เก็บกำไรก้อนแรก แล้วขยับ SL ถึงจุดคุ้มทุน
  TP2     : กองสภาพคล่องฝั่งตรงข้ามกองใหญ่ (จุดเท่ากัน/PDH-PDL/โพรงราคา)
  TP3     : เป้าโครงสร้างใหญ่ระดับตลาดโลก (จุดสูง/ต่ำวันก่อน เลขสวย)

ไม้ที่ไม่ได้ TP2 อย่างน้อย 1.5R จะถูกทิ้งทั้งไม้ - คุณภาพมาก่อนจำนวน
"""
from dataclasses import dataclass, field

import numpy as np

from .liquidity import Level, levels_above, levels_below

MAJOR_KINDS = {"PDH", "PDL"}
INTRA_KINDS = {"SESSION_HIGH", "SESSION_LOW", "FVG_BULL", "FVG_BEAR"}


@dataclass
class Signal:
    asset: str
    display_name: str
    side: str                      # BUY / SELL
    trade_type: str                # ไม้สั้น / ไม้ยาว
    entry: float
    entry_low: float
    entry_high: float
    sl: float
    tp1: float = 0.0
    tp2: float = 0.0
    tp3: float = 0.0
    r: float = 0.0                 # ขนาดความเสี่ยงต่อหน่วย (ราคา)
    rr_tp2: float = 0.0
    level_price: float = 0.0
    level_kind: str = ""
    level_note: str = ""
    touches: int = 0
    bias: str = "NEUTRAL"
    reason: str = ""
    ts: object = None
    extras: dict = field(default_factory=dict)


def _tick_round(value: float, asset: str) -> float:
    return round(value, 0) if asset.startswith("BTC") else round(value, 1)


def build_signal(hit: dict, levels: list[Level], asset: str, display_name: str, eng: dict) -> Signal | None:
    df = hit["df"]
    side = hit["side"]
    a = hit["atr"]
    s, r = hit["sweep_idx"], hit["reclaim_idx"]
    level: Level = hit["level"]
    dfc = df.iloc[:-1]

    close_r = float(dfc["close"].iloc[r])
    entry = close_r
    entry_low, entry_high = entry - 0.15 * a, entry + 0.15 * a

    # ---- SL: เลยไส้สุดท้ายของกับดักออกไปเล็กน้อย ----
    if side == "BUY":
        sweep_extreme = float(dfc["low"].iloc[s : r + 1].min())
        sl = sweep_extreme - eng["sl_atr_mult"] * a
    else:
        sweep_extreme = float(dfc["high"].iloc[s : r + 1].max())
        sl = sweep_extreme + eng["sl_atr_mult"] * a
    risk = abs(entry - sl)
    if risk <= 0 or not np.isfinite(risk):
        return None

    # ---- หาเป้าจากกับดักฝั่งตรงข้าม ----
    if side == "BUY":
        pool = levels_above(levels, entry + 0.3 * risk)
    else:
        pool = levels_below(levels, entry - 0.3 * risk)
    pool_prices = [x.price for x in pool]

    def pick(lo_r: float, hi_r: float, above: float) -> float | None:
        """เลือกระดับฝั่งตรงข้ามที่อยู่ในช่วง [lo_r, hi_r] เท่าของความเสี่ยง และไกลกว่า `above`"""
        best = None
        for p in pool_prices:
            dist = abs(p - entry)
            if dist < lo_r * risk or dist > hi_r * risk:
                continue
            if side == "BUY" and p <= above:
                continue
            if side == "SELL" and p >= above:
                continue
            if best is None or dist < abs(best - entry):
                best = p
        return best

    tp1 = pick(0.8, 2.5, entry) or entry + (1.0 * risk if side == "BUY" else -1.0 * risk)
    tp2 = pick(1.8, 4.0, tp1) or tp1 + (1.2 * risk if side == "BUY" else -1.2 * risk)
    tp3_lo = max(2.8, abs(tp2 - entry) / risk + 0.8)
    tp3 = pick(tp3_lo, 6.0, tp2) or tp2 + (1.3 * risk if side == "BUY" else -1.3 * risk)

    # บังคับลำดับให้ TP1 < TP2 < TP3 ห่างกันอย่างน้อย 0.5R
    seq = [tp1, tp2, tp3]
    for i in range(1, 3):
        if (seq[i] - seq[i - 1]) * (1 if side == "BUY" else -1) < 0.5 * risk:
            seq[i] = seq[i - 1] + (0.5 * risk if side == "BUY" else -0.5 * risk)
    tp1, tp2, tp3 = seq

    rr_tp2 = abs(tp2 - entry) / risk
    if rr_tp2 < eng["min_rr_tp2"]:
        return None  # เป้าไกลไม่พอ คุ้มไม่ถึงความเสี่ยง -> ทิ้งไม้นี้

    # ---- ติดป้ายไม้สั้น/ไม้ยาว ตามขนาดกับดัก ----
    is_major = level.major or level.kind in MAJOR_KINDS
    trade_type = "ไม้ยาว (Swing 1-5 วัน)" if is_major else "ไม้สั้น (ปิดในวัน)"

    sig = Signal(
        asset=asset,
        display_name=display_name,
        side=side,
        trade_type=trade_type,
        entry=_tick_round(entry, asset),
        entry_low=_tick_round(entry_low, asset),
        entry_high=_tick_round(entry_high, asset),
        sl=_tick_round(sl, asset),
        tp1=_tick_round(tp1, asset),
        tp2=_tick_round(tp2, asset),
        tp3=_tick_round(tp3, asset),
        r=risk,
        rr_tp2=rr_tp2,
        level_price=level.price,
        level_kind=level.kind,
        level_note=level.note,
        touches=level.touches,
        bias=hit["bias"],
        ts=dfc["ts"].iloc[r],
    )

    sig.extras = {
        "vol_ratio": round(float(dfc["volume"].iloc[s] / float(df["volume"].rolling(20).mean().iloc[s])), 2),
        "wick_pct": None,
        "fast_reclaim": (r - s) == 0,
        "reclaim_bars": int(r - s),
        "delta_ok": hit.get("bias") is not None,
    }
    rng = float(dfc["high"].iloc[s] - dfc["low"].iloc[s])
    if rng > 0:
        if side == "BUY":
            wick = (min(float(dfc["open"].iloc[s]), close_r) - float(dfc["low"].iloc[s])) / rng
        else:
            wick = (float(dfc["high"].iloc[s]) - max(float(dfc["open"].iloc[s]), close_r)) / rng
        sig.extras["wick_pct"] = round(wick * 100)
    return sig
