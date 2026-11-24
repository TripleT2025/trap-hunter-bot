#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Trap Hunter Bot - ตัวประสานงานหลัก (รัน 1 รอบต่อ tick จาก cron/GitHub Actions)

ลำดับการทำงานรอบเดียว:
  1. โหลด config + ความจำของระบบ (state)
  2. ทีละสินทรัพย์: ดึงข้อมูล -> สร้างแผนที่กับดัก -> ตรวจจับจังหวะ -> คำนวณจุดเข้า
  3. กรองด้วยกติกาความถี่ (cooldown + เพดานรายวัน)
  4. ให้ AI ตรวจซ้ำ (ถ้าเปิดไว้) แล้วยิงเข้า Telegram
"""
import argparse
import sys

from trapbot.ai_review import review_and_rewrite
from trapbot.config import load_config
from trapbot.data import fetch_daily_data, fetch_htf_data, fetch_trigger_data
from trapbot.detector import detect
from trapbot.liquidity import build_levels
from trapbot.regime import apply_regime, classify, label as regime_label
from trapbot.signal import build_signal
from trapbot.state import check_and_update, load, save
from trapbot.telegram import format_message, send

DISPLAY_NAMES = {"BTCUSDT": "BTC/USDT", "XAUUSD": "ทองคำ (XAUUSD)"}


def scan_asset(asset: str, acfg: dict, eng: dict) -> list:
    """สแกนสินทรัพย์หนึ่งตัว คืนลิสต์สัญญาณที่ผ่านการยืนยันครบทุกชั้น"""
    df = fetch_trigger_data(acfg, eng["lookback_bars"])
    df_htf = fetch_htf_data(acfg)
    df_daily = fetch_daily_data(acfg)
    if df is None or len(df) < 100:
        print(f"[{asset}] ข้อมูลไม่พอ (ได้ {0 if df is None else len(df)} แท่ง) - ข้าม")
        return []

    price_now = float(df["close"].iloc[-1])

    # วัดสภาพตลาดรอบนี้ (เงียบ/ปกติ/ผันผวนแรง) แล้วปรับความเข้มกรองให้พอดีอัตโนมัติ
    reg = classify(df_htf, eng.get("regime"))
    eng_asset = apply_regime(eng, reg)
    print(f"[{asset}] สภาพตลาด: {regime_label(reg['regime'])} (ATR 1 ชม. = {reg['ratio']} เท่าของค่ากลาง)")

    levels = build_levels(df, df_daily, price_now, acfg.get("round_step", 1), eng_asset)
    hits = detect(df, df_htf, levels, eng_asset, bool(acfg.get("delta_enabled")))

    signals = []
    for hit in hits:
        sig = build_signal(hit, levels, asset, DISPLAY_NAMES.get(asset, asset), eng_asset)
        if sig is not None:
            sig.extras["regime"] = reg["regime"]
            signals.append(sig)
    return signals


def main() -> int:
    parser = argparse.ArgumentParser(description="Trap Hunter Bot - สแกนจังหวะกับดักแล้วยิงเข้า Telegram")
    parser.add_argument("--config", default=None, help="พาธไฟล์ config.yaml")
    parser.add_argument("--dry-run", action="store_true", help="ไม่ยิง Telegram แค่พิมพ์การ์ดออกจอ")
    args = parser.parse_args()

    cfg = load_config(args.config)
    if args.dry_run:
        cfg["runtime"]["dry_run"] = True
    eng = cfg["engine"]

    state = load()
    sent_any = False

    for asset, acfg in cfg["assets"].items():
        if not acfg.get("enabled", False):
            continue
        try:
            signals = scan_asset(asset, acfg, eng)
        except Exception as e:
            print(f"[{asset}] สแกนล้มเหลว: {e.__class__.__name__}: {e}", file=sys.stderr)
            continue

        if not signals:
            print(f"[{asset}] รอบนี้ไม่มีจังหวะที่ผ่านครบทุกชั้นยืนยัน (ปกติ - ระบบออกแบบมาให้รอ)")
            continue

        # จัดลำดับคุณภาพ: R:R ที่ TP2 สูงสุดก่อน
        signals.sort(key=lambda s: s.rr_tp2, reverse=True)
        for sig in signals:
            ok, msg = check_and_update(state, sig.asset, sig.side, sig.level_price, eng)
            if not ok:
                print(f"[{asset}] ข้ามสัญญาณ: {msg}")
                continue
            approved, reason = review_and_rewrite(cfg["ai"], sig)
            if not approved:
                print(f"[{asset}] AI ยับยั้งสัญญาณนี้ ({msg})")
                continue
            sig.reason = reason
            card = format_message(sig)
            tg_ready = bool(cfg["telegram"].get("bot_token")) and bool(cfg["telegram"].get("chat_id"))
            if cfg["runtime"].get("dry_run"):
                print("=" * 62)
                print("[DRY RUN] การ์ดที่จะยิงเข้า Telegram:")
                print(card)
            elif not tg_ready:
                # ยังไม่ได้ใส่คีย์ Telegram: ระบบยังทำงานเต็มรูปแบบ พิมพ์การ์ดไว้ใน log แทน
                # สถานะ workflow ยังเป็นสีเขียว - ใส่คีย์ตาม README ขั้นที่ 5 เมื่อไหร่การ์ดเด้งเข้า Telegram ทันที
                print("=" * 62)
                print("[ตั้งค่ายังไม่ครบ] ยังไม่มี TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID - วิธีใส่อยู่ README ขั้นที่ 5")
                print("ระบบสแกนและคัดจังหวะทำงานปกติ พิมพ์การ์ดไว้ใน log นี้แทนการส่ง:")
                print(card)
            else:
                send(cfg["telegram"], card)
                print(f"[{asset}] ยิงสัญญาณ {sig.side} @ {sig.entry} เข้า Telegram แล้ว")
            sent_any = True
            break  # ต่อรอบสแกน (5 นาที) สินทรัพย์ละ 1 ไม้พอ - กันสแปมช่วงตลาดรุนแรง ไม้ถัดไปออกรอบหน้าในอีก 5 นาที

    save(state)
    if not sent_any:
        print("จบรอบ: ไม่มีสัญญาณใหม่")
    return 0


if __name__ == "__main__":
    sys.exit(main())
