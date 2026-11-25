#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Trap Hunter Bot - ตัวประสานงานหลัก (รัน 1 รอบต่อ tick จาก GitHub Actions ทุก 5 นาที)

ลำดับการทำงานรอบเดียว:
  1. โหลด config + ความจำ (state) แล้วอ่านคำสั่ง Telegram ที่คุณพิมพ์มา (/status /pause ฯลฯ)
  2. รวมดึงข้อมูล 7 สินทรัพย์ (BTC, ทอง, เงิน, SOL, XRP, น้ำมัน, NAS100) - yfinance รวมเป็นชุด
  3. ทีละสินทรัพย์: สแกนกับดัก -> เกรดคุณภาพ -> ยืนยันข้ามตลาด -> เช็คข่าวแรง -> AI ตรวจซ้ำ
     -> วาดกราฟจังหวะ -> ยิงการ์ด + กราฟเข้า Telegram
  4. สมุดจดผล + พอร์ตจำลองเดินตามราคาทุกรอบ แจ้งผลทันทีเมื่อไม้ปิด (TP/SL)
  5. สรุปเช้า 08:00 ไทยทุกวัน + รายงานสัปดาห์พร้อมกราฟพอร์ตทุกวันจันทร์ + เตือนเมื่อสภาพตลาดเปลี่ยน

หลักการออกแบบ: ทุกระบบเสริมคลุม try/except ของตัวเอง - อะไรล้มก็ล้มอย่างเดียว
ชั้นสแกนหลัก "ไม่เคย" สะดุดเพราะระบบเสริม และถ้าโดนหยุดชั่วคราว (/pause) ระบบยังสแกน จดผล ตอบคำสั่งครบ
"""
import argparse
import sys
from datetime import datetime, timedelta, timezone

from trapbot import commands as tg_commands
from trapbot import journal, paper
from trapbot.ai_review import review_and_rewrite
from trapbot.brief import maybe_send_brief
from trapbot.calendar import news_line as calendar_news_line
from trapbot.charts import signal_chart_png
from trapbot.config import load_config
from trapbot.confluence import STATUS_TH
from trapbot.confluence import check as confluence_check
from trapbot.data import (
    DISPLAY_NAMES,
    collect_yf_specs,
    dxy_trend,
    fetch_daily_data,
    fetch_dxy_daily,
    fetch_htf_data,
    fetch_trigger_data,
    fmt_price,
    prefetch_yf_batches,
)
from trapbot.detector import detect
from trapbot.grades import grade_signal
from trapbot.liquidity import build_levels
from trapbot.regime import apply_regime, classify, label as regime_label
from trapbot.signal import build_signal
from trapbot.state import check_and_update, load, save
from trapbot.telegram import format_message, send, send_photo


def is_paused(state: dict) -> bool:
    """อยู่ในช่วง /pause หรือไม่ (ระบบยังสแกนและจดผล - หยุดแค่การส่งการ์ดใหม่)"""
    until = state.get("paused_until")
    if not until:
        return False
    try:
        return datetime.fromisoformat(until) > datetime.now(timezone.utc)
    except Exception:
        return False


def top_level_strings(levels, price_now: float, asset: str, n: int = 3) -> list[str]:
    """กับดักใกล้ตัว N อันดับ (เรียงตามระยะจากราคา) สำหรับเก็บ cache ไว้ตอบ /levels และสรุปเช้า"""
    try:
        near = sorted(levels, key=lambda l: abs(l.price - price_now))[:n]
        return [f"{l.kind} {fmt_price(l.price, asset)}" for l in near]
    except Exception:
        return []


def scan_asset(asset: str, acfg: dict, eng: dict) -> tuple[list, dict | None]:
    """สแกนสินทรัพย์หนึ่งตัว คืน (ลิสต์สัญญาณที่ผ่านครบชั้นยืนยัน, ข้อมูลรอบสแกน) ข้อมูลใช้ซ้ำได้ - ไม่ต้องดึงซ้ำ"""
    df = fetch_trigger_data(acfg, eng["lookback_bars"])
    df_htf = fetch_htf_data(acfg)
    df_daily = fetch_daily_data(acfg)
    if df is None or len(df) < 100:
        print(f"[{asset}] ข้อมูลไม่พอ (ได้ {0 if df is None else len(df)} แท่ง) - ข้าม")
        return [], None

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
            # เก็บพิกัดจุดเกิดเหตุไว้วาดบนกราฟ (ตบหน้า = TRAP, ยืนยัน = CONFIRM)
            try:
                dfc_ts = df["ts"].iloc[:-1]
                sig.extras["sweep_ts"] = dfc_ts.iloc[hit["sweep_idx"]]
                sig.extras["reclaim_ts"] = dfc_ts.iloc[hit["reclaim_idx"]]
            except Exception:
                pass
            signals.append(sig)
    meta = {
        "df": df,
        "df_htf": df_htf,
        "df_daily": df_daily,
        "price_now": price_now,
        "reg": reg,
        "levels": levels,
        "eng_asset": eng_asset,
    }
    return signals, meta


def main() -> int:
    parser = argparse.ArgumentParser(description="Trap Hunter Bot - สแกนจังหวะกับดักแล้วยิงเข้า Telegram")
    parser.add_argument("--config", default=None, help="พาธไฟล์ config.yaml")
    parser.add_argument("--dry-run", action="store_true", help="ไม่ยิง Telegram แค่พิมพ์การ์ดออกจอ")
    args = parser.parse_args()

    cfg = load_config(args.config)
    if args.dry_run:
        cfg["runtime"]["dry_run"] = True
    eng = cfg["engine"]
    feats = cfg.get("features") or {}
    tg_cfg = cfg["telegram"]
    tg_ready = bool(tg_cfg.get("bot_token")) and bool(tg_cfg.get("chat_id"))
    dry_run = bool(cfg["runtime"].get("dry_run"))

    state = load()
    for key in ("last_signal", "daily", "journal", "levels_cache", "regime_cache"):
        state.setdefault(key, {} if key in ("last_signal", "daily", "levels_cache", "regime_cache") else [])

    ctx = {"regimes": {}, "scanned_at": None}

    # ---- 1) อ่านคำสั่ง Telegram ที่พิมพ์ค้างไว้ (/status /levels /pause ...) ----
    try:
        cmd_cfg = dict(tg_cfg)
        cmd_cfg["enabled"] = (feats.get("commands") or {}).get("enabled", True)
        tg_commands.process(cmd_cfg, state, ctx)
    except Exception as e:
        print(f"[commands] ล้ม ({e.__class__.__name__}) - ข้าม", file=sys.stderr)

    paused = is_paused(state)
    if paused:
        print("สถานะ: หยุดส่งสัญญาณชั่วคราว (/pause) - ระบบยังสแกน จดผล และตอบคำสั่งปกติ")

    # ---- 2) รวมดึงข้อมูล yfinance เป็นชุดเดียว (กันโดน rate limit) ----
    try:
        n = prefetch_yf_batches(collect_yf_specs(cfg["assets"]))
        print(f"[data] ดึงข้อมูล yfinance รวมชุดสำเร็จ {n} ชุดข้อมูล")
    except Exception as e:
        print(f"[data] prefetch ล้ม ({e.__class__.__name__}) - ตัวที่พลาดจะดึงรายตัวแทน", file=sys.stderr)

    dxy_tr = None
    try:
        dxy_tr = dxy_trend(fetch_dxy_daily())
    except Exception:
        pass

    sent_any = False
    bars_for_track: dict[str, tuple[float, float]] = {}

    # ---- 3) สแกนทีละสินทรัพย์ ----
    for asset, acfg in cfg["assets"].items():
        if not acfg.get("enabled", False):
            continue
        try:
            signals, meta = scan_asset(asset, acfg, eng)
        except Exception as e:
            print(f"[{asset}] สแกนล้มเหลว: {e.__class__.__name__}: {e}", file=sys.stderr)
            continue

        # เก็บข้อมูลบริบทรอบนี้ไว้ให้คำสั่ง/สรุปเช้า แล้วเดินไม้ค้างด้วยแท่งล่าสุด (ใช้ข้อมูลที่สแกนแล้ว ไม่ดึงซ้ำ)
        if meta is not None:
            try:
                price_now = meta["price_now"]
                bars_for_track[asset] = (float(meta["df"]["high"].iloc[-2]), float(meta["df"]["low"].iloc[-2]))
                df_htf_ctx = meta["df_htf"]
                reg_now = meta["reg"]
                cut = datetime.now(timezone.utc) - timedelta(hours=24)
                old = df_htf_ctx[df_htf_ctx["ts"] >= cut]
                chg = (price_now / float(old["close"].iloc[0]) - 1) * 100 if len(old) else None
                ctx["regimes"][asset] = {
                    "display": DISPLAY_NAMES.get(asset, asset),
                    "regime_label": regime_label(reg_now["regime"]),
                    "price": price_now,
                    "change_24h": round(chg, 2) if chg is not None else None,
                }
                ctx["scanned_at"] = datetime.now(timezone.utc)
                state["levels_cache"][asset] = top_level_strings(meta["levels"], price_now, asset)
                old_reg = (state["regime_cache"].get(asset) or {}).get("regime")
                state["regime_cache"][asset] = {"regime": reg_now["regime"], "display": DISPLAY_NAMES.get(asset, asset)}
                if old_reg and old_reg != reg_now["regime"] and tg_ready and not dry_run and not paused:
                    try:
                        send(tg_cfg, f"เปลี่ยนสภาพตลาด {DISPLAY_NAMES.get(asset, asset)}: "
                                     f"{regime_label(old_reg)} -> {regime_label(reg_now['regime'])} "
                                     f"(ATR เท่ากับ {reg_now['ratio']} ของค่ากลาง) - ระบบปรับเกณฑ์กรองให้พอดีใหม่แล้วอัตโนมัติ")
                    except Exception:
                        pass
            except Exception as e:
                print(f"[{asset}] เก็บบริบทล้ม ({e.__class__.__name__}) - สแกนยังปกติ", file=sys.stderr)

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

            # ---- ชั้นเสริม: ข่าวแรง + ยืนยันข้ามตลาด + เกรดคุณภาพ ----
            news = None
            try:
                if (feats.get("calendar") or {}).get("enabled", True):
                    news = calendar_news_line(float((feats.get("calendar") or {}).get("hours_ahead", 3)))
            except Exception:
                news = None
            conf = None
            try:
                if (feats.get("confluence") or {}).get("enabled", True):
                    conf = confluence_check(sig, dxy_tr)
            except Exception:
                conf = None
            grade, score, _notes = grade_signal(sig, conf.get("status") if conf else None, news)
            sig.extras["news_line"] = news
            sig.extras["conf_line"] = f"{conf['line']} ({STATUS_TH.get(conf.get('status'), '')})" if conf else None
            sig.extras["grade_line"] = f"เกรดคุณภาพไม้: <b>{grade}</b> ({score} คะแนน)"

            approved, reason = review_and_rewrite(cfg["ai"], sig)
            if not approved:
                print(f"[{asset}] AI ยับยั้งสัญญาณนี้")
                continue
            sig.reason = reason
            card = format_message(sig)
            png = None
            try:
                if (feats.get("charts") or {}).get("enabled", True) and meta is not None:
                    png = signal_chart_png(sig, meta["df"], meta["levels"])
            except Exception as e:
                print(f"[{asset}] วาดกราฟล้ม ({e.__class__.__name__}) - ส่งแค่การ์ดข้อความ", file=sys.stderr)

            if dry_run:
                print("=" * 62)
                print("[DRY RUN] การ์ดที่จะยิงเข้า Telegram:")
                print(card)
                if png:
                    print(f"[DRY RUN] + รูปกราฟแนบ {len(png) / 1024:.0f} KB")
            elif not tg_ready:
                # ยังไม่ได้ใส่คีย์ Telegram: ระบบยังทำงานเต็มรูปแบบ พิมพ์การ์ดไว้ใน log แทน
                print("=" * 62)
                print("[ตั้งค่ายังไม่ครบ] ยังไม่มี TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID - วิธีใส่อยู่ README ขั้นที่ 5")
                print("ระบบสแกนและคัดจังหวะทำงานปกติ พิมพ์การ์ดไว้ใน log นี้แทนการส่ง:")
                print(card)
            elif paused:
                print(f"[{asset}] มีสัญญาณ ({sig.side} @ {sig.entry}) แต่อยู่ช่วง /pause - ไม่ส่ง ไม่จด")
                continue
            else:
                try:
                    if png:
                        send_photo(tg_cfg, png, caption=f"{sig.display_name} - กราฟจังหวะกับดัก")
                    send(tg_cfg, card)
                    print(f"[{asset}] ยิงสัญญาณ {sig.side} @ {sig.entry} (เกรด {grade}) เข้า Telegram แล้ว")
                except Exception as e:
                    print(f"[{asset}] ส่ง Telegram ล้ม: {e.__class__.__name__}: {e}", file=sys.stderr)
                    continue
                try:
                    journal.record(state, sig, grade)
                    if (feats.get("paper") or {}).get("enabled", True):
                        paper.ensure(state, feats.get("paper") or {})
                        paper.open_position(state["paper"], sig, grade)
                except Exception as e:
                    print(f"[{asset}] จดสมุด/พอร์ตจำลองล้ม ({e.__class__.__name__})", file=sys.stderr)
            sent_any = True
            break  # สินทรัพย์ละ 1 ไม้ต่อรอบสแกน (5 นาที) - กันสแปม ไม้ถัดไปออกรอบหน้า

    # ---- 4) เดินไม้ค้างทั้งสมุดจดผลและพอร์ตจำลองด้วยราคาล่าสุด ----
    try:
        journal.update_open(state, bars_for_track)
    except Exception as e:
        print(f"[journal] เดินไม้ล้ม ({e.__class__.__name__})", file=sys.stderr)
    try:
        if (feats.get("paper") or {}).get("enabled", True):
            paper.ensure(state, feats.get("paper") or {})
            closed = paper.update(state["paper"], bars_for_track)
            for rec in closed:
                print(f"[paper] ปิดไม้ {rec['asset']} {rec['outcome']} R={rec['r']:+.1f} pnl={rec['pnl_usd']:+,.2f}$ equity={rec['equity']:,.2f}$")
                if tg_ready and not dry_run:
                    try:
                        arrow = "เปิดหมวยไม้จำลอง" if rec["r"] == 0 else ("กำไร" if rec["pnl_usd"] >= 0 else "ขาดทุน")
                        send(tg_cfg, f"ผลไม้จำลอง {rec['display']}: ปิดแบบ {rec['outcome']} ({rec['r']:+.1f}R) "
                                     f"{arrow} {rec['pnl_usd']:+,.2f}$ | พอร์ตตอนนี้ {rec['equity']:,.2f}$")
                    except Exception:
                        pass
    except Exception as e:
        print(f"[paper] เดินพอร์ตล้ม ({e.__class__.__name__})", file=sys.stderr)

    # ---- 5) สรุปเช้า + รายงานสัปดาห์ (08:00 ไทย, วันจันทร์แถมรายงานสัปดาห์) ----
    if tg_ready and not dry_run and not paused:
        try:
            fcfg = dict(feats.get("morning_brief") or {})
            fcfg["ai_cfg"] = cfg["ai"]
            if maybe_send_brief(fcfg, state, tg_cfg, ctx, send_fn=send, send_photo_fn=send_photo):
                print("[brief] ส่งสรุปเช้าแล้ว")
        except Exception as e:
            print(f"[brief] ล้ม ({e.__class__.__name__}) - ไม่กระทบรอบหลัก", file=sys.stderr)

    save(state)
    if not sent_any:
        print("จบรอบ: ไม่มีสัญญาณใหม่")
    return 0


if __name__ == "__main__":
    sys.exit(main())
