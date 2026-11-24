#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""เครื่องมือทดสอบย้อนหลัง (Backtest Scan)

ไล่เทียนย้อนหลังทีละแท่ง จำลองว่า "ถ้าระบบรันอยู่จริงตอนนั้น จะยิงไม้อะไรบ้าง"
และจำลองผลลัพธ์ตามแผนเก็บกำไร 30/40/30 พร้อมย้าย SL ถึงจุดคุ้มทุนหลังถึง TP1

ใช้เพื่อ: ตรวจว่าตรรกะทำงานจริง + เก็บตัวอย่างสัญญาณสำหรับดูด้วยตา
"""
import argparse

import numpy as np

from trapbot.config import load_config
from trapbot.data import fetch_daily_data, fetch_htf_data, fetch_trigger_data
from trapbot.detector import detect
from trapbot.liquidity import build_levels
from trapbot.signal import build_signal

DISPLAY_NAMES = {"BTCUSDT": "BTC/USDT", "XAUUSD": "ทองคำ (XAUUSD)"}


def evaluate_outcome(df, sig, entry_idx_closed):
    """จำลองผลลัพธ์หลังเกิดสัญญาณ ณ แท่งที่ entry (แท่งปิดล่าสุด)

    แผนเก็บกำไร: TP1 เก็บ 0.3R แล้ว SL ย้ายมาจุดคุ้มทุน
                TP2 เก็บอีก 0.4R แล้ว SL ย้ายมา TP1
                TP3 เก็บอีก 0.3R
    คืน (คลาสผล, R สุทธิ)
    """
    side = 1 if sig.side == "BUY" else -1
    entry, sl = sig.entry, sig.sl
    tp1, tp2, tp3 = sig.tp1, sig.tp2, sig.tp3
    cur_sl = sl
    realized = 0.0

    highs = df["high"].values[entry_idx_closed + 1 :]
    lows = df["low"].values[entry_idx_closed + 1 :]

    def hit_sl(h, l):
        return l <= cur_sl if side == 1 else h >= cur_sl

    def hit_tp(tp, h, l):
        return h >= tp if side == 1 else l <= tp

    for h, l in zip(highs, lows):
        # เช็ค SL ก่อน TP ในแท่งเดียวกัน (ระมัดระวังไปทางเสียมากกว่า)
        # ถ้าโดน SL ก่อนถึง TP1 = ขาดทุนเต็มไม้ -1R
        # ถ้า SL ถูกย้ายมาจุดคุ้มทุนแล้ว (เก็บ TP1 ไปแล้ว) = ได้กำไรส่วนที่เก็บไว้
        if hit_sl(h, l):
            return ("SL", -1.0 if realized == 0 else realized)
        if hit_tp(tp1, h, l) and realized < 0.3:
            realized += 0.3
            cur_sl = entry  # ย้าย SL ถึงจุดคุ้มทุน
            continue
        if hit_tp(tp2, h, l) and realized < 0.7:
            realized += 0.4
            cur_sl = tp1
            continue
        if hit_tp(tp3, h, l) and realized < 1.0:
            return ("TP3", 1.0)
    return ("OPEN", realized)


def run(asset: str, days: int, cfg: dict, max_signals: int = 20) -> list:
    acfg = cfg["assets"][asset]
    eng = dict(cfg["engine"])
    eng.setdefault("min_pierce_atr", 0.05)

    df = fetch_trigger_data(acfg, days * 96)
    df_htf = fetch_htf_data(acfg)
    df_daily = fetch_daily_data(acfg)
    bars_per_step = {"15m": 96, "30m": 48, "1h": 24}.get(acfg["trigger_tf"], 96)
    total = len(df)
    warmup = eng["lookback_bars"]
    cooldown_until_idx = -1
    signals = []

    print(f"[{asset}] ข้อมูล {total} แท่ง ({df['ts'].iloc[0]} -> {df['ts'].iloc[-1]})")

    for i in range(warmup, total - 5):
        if i < cooldown_until_idx:
            continue
        # สแกนทุกๆ 30 นาที (2 แท่ง 15m) เพื่อความเร็ว - จังหวะที่จับได้เหมือนกัน
        if (i - warmup) % 2 != 0:
            continue
        window = df.iloc[max(0, i - eng["lookback_bars"]) : i + 1].reset_index(drop=True)
        levels = build_levels(window, df_daily, float(window["close"].iloc[-1]), acfg.get("round_step", 1), eng)
        hits = detect(window, df_htf, levels, eng, bool(acfg.get("delta_enabled")))
        for hit in hits:
            sig = build_signal(hit, levels, asset, DISPLAY_NAMES.get(asset, asset), eng)
            if sig is None:
                continue
            if signals and (sig.level_kind == signals[-1][0].level_kind and abs(sig.entry - signals[-1][0].entry) < sig.r * 0.5):
                continue  # จังหวะซ้ำเดิมใกล้เกิน
            sig.ts = window["ts"].iloc[-1]
            sig.entry_idx = i
            outcome, r_mult = evaluate_outcome(df, sig, i)
            signals.append((sig, outcome, r_mult))
            cooldown_until_idx = i + 16  # cooldown ประมาณ 4 ชม.
            break
        if len(signals) >= max_signals:
            break
    return signals


def main() -> int:
    parser = argparse.ArgumentParser(description="ทดสอบระบบย้อนหลัง")
    parser.add_argument("--asset", default="BTCUSDT")
    parser.add_argument("--days", type=int, default=45)
    parser.add_argument("--max-signals", type=int, default=20)
    parser.add_argument("--config", default=None)
    args = parser.parse_args()

    cfg = load_config(args.config)
    if args.asset not in cfg["assets"]:
        raise SystemExit(f"ไม่รู้จักสินทรัพย์ {args.asset}")

    signals = run(args.asset, args.days, cfg, args.max_signals)
    print("\n" + "=" * 70)
    print(f"ผลทดสอบย้อนหลัง {args.asset} ({args.days} วันล่าสุด): พบ {len(signals)} ไม้")
    print("=" * 70)

    wins = 0
    total_r = 0.0
    for sig, outcome, r_mult in signals:
        total_r += r_mult
        if r_mult > 0:
            wins += 1
        print(
            f"{sig.ts.strftime('%d/%m %H:%M')} UTC | {sig.side:4s} | {sig.trade_type[:6]} | "
            f"กับดัก={sig.level_kind:12s} | Entry={sig.entry:,.1f} SL={sig.sl:,.1f} "
            f"TP2={sig.tp2:,.1f} (1:{sig.rr_tp2:.1f}) | ผล={outcome:4s} R={r_mult:+.1f}"
        )
    if signals:
        print("-" * 70)
        print(f"อัตราทำกำไร (ได้กำไรไม่ติดลบ): {wins}/{len(signals)} = {wins/len(signals)*100:.0f}%")
        print(f"R สุทธิรวม (แผน 30/40/30 + ย้าย SL): {total_r:+.1f}R | เฉลี่ย {total_r/len(signals):+.2f}R ต่อไม้")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
