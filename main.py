#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Trap Hunter Bot - ตัวประสานงานหลัก (รัน 1 รอบต่อ tick จาก GitHub Actions ทุก 5 นาที)

ลำดับการทำงานรอบเดียว (รุ่นปรับปรุงตามข้อเสนอ 3 AI - 26 พ.ย. 2026):
  1. โหลด config + ความจำ (state) แล้วอ่านคำสั่ง Telegram (/status /why /levels /pause ฯลฯ)
  2. รวมดึงข้อมูล 7 สินทรัพย์ (BTC, ทอง, เงิน, SOL, XRP, น้ำมัน, NAS100) - yfinance รวมเป็นชุด
  3. ยกเลิกการ์ดที่หมดอายุทั้งหมดก่อน (ข้อความใหม่พร้อมเสียง - ส่งได้แม้ช่วง /pause)
  4. ทีละสินทรัพย์: สแกนกับดัก -> จังหวะ "เกือบผ่าน" ส่งการ์ดเฝ้าดู (เงียบ ไม่มีเสียง)
     -> ประตู "ยังเข้าได้" (อายุ/ราคา) -> เกรด (B เงียบจนกว่าจะพิสูจน์กำไร)
     -> รหัสเหตุการณ์ถาวรกันซ้ำ -> วาดกราฟ -> ยิงการ์ด "เข้าได้ตามแผน" (พร้อมเสียง)
  5. สมุด 3 เล่ม (พบ -> ยังเข้าได้ -> ยิงจริง) + พอร์ตจำลองเดินตามราคา
     + สวิตช์กันขาดทุนเกินเพดานรายวัน + แจ้งผลทันทีเมื่อไม้ปิด (TP/SL)
  6. สรุปเช้า (ส่งชดเชยถ้าระบบมาช้า ไม่ข้ามเงียบ ๆ) + รายงานสัปดาห์วันจันทร์
  7. Watchdog ping (Healthchecks.io) - รอบล้ม ping /fail เพื่อไม่ตายเงียบ

สิ่งที่ถอดออกตามมติ 3 AI: ชั้น AI ตรวจซ้ำสัญญาณ (กลับไปใช้เทมเพลตเหตุผล - เกณฑ์คงที่
ตรวจย้อนหลังได้ ประหยัดค่า OpenRouter), regime ปรับเกณฑ์อัตโนมัติ (ปิด - วัดผลต้องได้
เกณฑ์คงที่), พยากรณ์ทิศทางเช้า (ปิดใช้ - เปลี่ยนเป็นแผน if-then บนสรุปเช้าแทน)

หลักการออกแบบ: ทุกระบบเสริมคลุม try/except ของตัวเอง - อะไรล้มก็ล้มอย่างเดียว
ชั้นสแกนหลัก "ไม่เคย" สะดุดเพราะระบบเสริม และถ้าโดนหยุดชั่วคราว (/pause) ระบบยังสแกน จดผล ตอบคำสั่งครบ
"""
import argparse
import os
import sys
from datetime import datetime, timedelta, timezone

from trapbot import activity, gate, journal, paper, watchdog
from trapbot.brief import maybe_send_brief
from trapbot.calendar import news_line as calendar_news_line
from trapbot.charts import signal_chart_png
from trapbot.config import load_config
from trapbot.confluence import STATUS_TH
from trapbot.confluence import check as confluence_check
from trapbot.data import (
    DISPLAY_NAMES,
    TH_TZ,
    collect_yf_specs,
    dxy_trend,
    fetch_daily_data,
    fetch_dxy_daily,
    fetch_htf_data,
    fetch_trigger_data,
    fmt_price,
    fmt_th_time,
    prefetch_yf_batches,
)
from trapbot.detector import detect
from trapbot.grades import grade_signal
from trapbot.liquidity import atr, build_levels
from trapbot.morning_outlook import maybe_send_outlook
from trapbot.regime import apply_regime, classify, label as regime_label
from trapbot.signal import build_signal
from trapbot.state import (
    check_and_update,
    ensure_keys,
    load,
    mark_watch_event,
    save,
    seen_event,
)
from trapbot.telegram import (
    format_cancel_message,
    format_message,
    format_watch_message,
    send,
    send_photo,
)

MAX_WATCH_PER_ASSET = 2  # กันแชทท่วม: การ์ดเฝ้าดูส่งได้ไม่เกินนี้ต่อสินทรัพย์ต่อรอบ


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


def scan_asset(asset: str, acfg: dict, eng: dict) -> tuple[list, list, dict | None]:
    """สแกนสินทรัพย์หนึ่งตัว - คืน (สัญญาณเต็ม, จังหวะเกือบผ่าน [การ์ดเฝ้าดู], ข้อมูลรอบสแกน)"""
    df = fetch_trigger_data(acfg, eng["lookback_bars"])
    df_htf = fetch_htf_data(acfg)
    df_daily = fetch_daily_data(acfg)
    if df is None or len(df) < 100:
        print(f"[{asset}] ข้อมูลไม่พอ (ได้ {0 if df is None else len(df)} แท่ง) - ข้าม")
        return [], [], None

    price_now = float(df["close"].iloc[-1])

    # สภาพตลาด: โหมดปรับเกณฑ์อัตโนมัติถูกปิดตามมติ 3 AI (regime.enabled: false)
    # -> ใช้เกณฑ์คงที่ที่วัดผลได้ ตัวเลข ATR ยังโชว์ใน log เพื่อเทียบสภาพตลาด
    reg_cfg = eng.get("regime") or {}
    regime_on = bool(reg_cfg.get("enabled", True))
    if regime_on:
        reg = classify(df_htf, reg_cfg)
        eng_asset = apply_regime(eng, reg)
        print(f"[{asset}] สภาพตลาด: {regime_label(reg['regime'])} "
              f"(ATR 1 ชม. = {reg['ratio']} เท่าของค่ากลาง | โหมดปรับเกณฑ์อัตโนมัติ)")
    else:
        reg = {"regime": "NORMAL", "ratio": None}
        eng_asset = dict(eng)
        print(f"[{asset}] สภาพตลาด: ใช้เกณฑ์คงที่ (โหมดปรับเกณฑ์อัตโนมัติปิด)")

    a_val = float(atr(df, 14).iloc[-1])
    levels = build_levels(df, df_daily, price_now, acfg.get("round_step", 1), eng_asset)
    res = detect(df, df_htf, levels, eng_asset, bool(acfg.get("delta_enabled")))

    dfc_ts = df["ts"].iloc[:-1]  # เวลาของเทียนที่ปิดแล้ว (ใช้ทำรหัสเหตุการณ์ + วาดกราฟ)
    signals, near = [], []
    for hit in res["signals"]:
        sig = build_signal(hit, levels, asset, DISPLAY_NAMES.get(asset, asset), eng_asset)
        if sig is None:
            # โครงสร้างสวยแต่เป้าไม่ไกลพอ = เกือบผ่าน ขึ้นเฝ้าดู ไม่ทิ้งเงียบ ๆ
            res["near_misses"].append({**hit, "fail": "rr",
                                       "fail_th": "เป้าหมายไม่ไกลพอเทียบความเสี่ยง (R:R ต่ำกว่าเกณฑ์)"})
            continue
        sig.extras["regime"] = reg["regime"] if regime_on else None
        sig.extras["venue_line"] = acfg.get("venue_line")
        try:
            sweep_ts = dfc_ts.iloc[hit["sweep_idx"]]
            sig.extras["sweep_ts"] = sweep_ts          # พิกัดวาดกราฟ
            sig.extras["sweep_ts_dt"] = sweep_ts.to_pydatetime()  # รหัสเหตุการณ์
        except Exception:
            pass
        signals.append(sig)

    for nm in res["near_misses"]:
        try:
            nm_ts = dfc_ts.iloc[nm["sweep_idx"]].to_pydatetime()
        except Exception:
            nm_ts = None
        near.append({
            "asset": asset,
            "display": DISPLAY_NAMES.get(asset, asset),
            "side": nm["side"],
            "level_kind": nm["level"].kind,
            "level_price": nm["level"].price,
            "fail_th": nm["fail_th"],
            "sweep_ts": nm_ts,
        })

    meta = {
        "df": df,
        "df_htf": df_htf,
        "df_daily": df_daily,
        "price_now": price_now,
        "atr": a_val,
        "reg": reg,
        "regime_on": regime_on,
        "levels": levels,
        "eng_asset": eng_asset,
    }
    return signals, near, meta


def _send_cancel(tg_cfg, tg_ready, dry_run, card, default_reason: str) -> None:
    reason = card.get("cancel_reason") or default_reason
    text = format_cancel_message(card, reason)
    if dry_run:
        print("[DRY RUN] ข้อความยกเลิกการ์ด:")
        print(text)
        return
    if not tg_ready:
        print("[ยกเลิกการ์ด] ยังไม่มีคีย์ Telegram - พิมพ์ไว้ใน log แทน:")
        print(text)
        return
    try:
        send(tg_cfg, text)  # พร้อมเสียง - การยกเลิกต้องเห็นทันที
    except Exception as e:
        print(f"[cancel] ส่งข้อความยกเลิกล้ม ({e.__class__.__name__})", file=sys.stderr)


def _send_watch(tg_cfg, tg_ready, dry_run, paused, item: dict) -> bool:
    text = format_watch_message(item)
    if dry_run:
        print("[DRY RUN] การ์ดเฝ้าดู (เงียบ):")
        print(text)
        return True
    if not tg_ready or paused:
        return False
    try:
        send(tg_cfg, text, silent=True)  # เงียบ ไม่มีเสียง - ไม่ใช่ไม้ให้ลุก
        return True
    except Exception as e:
        print(f"[watch] ส่งการ์ดเฝ้าดูล้ม ({e.__class__.__name__})", file=sys.stderr)
        return False


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
    trad_cfg = eng.get("tradability") or {}
    grade_b_quiet = bool(feats.get("grade_b_quiet", True))
    watch_cfg = feats.get("watchdog") or {}

    # watchdog: config url ส่งต่อผ่าน env (ให้ __main__ ping ได้ทั้งสำเร็จและล้ม)
    if not watch_cfg.get("enabled", True):
        os.environ.pop("HEALTHCHECKS_URL", None)
    elif watch_cfg.get("url"):
        os.environ["HEALTHCHECKS_URL"] = str(watch_cfg["url"])

    state = load()
    ensure_keys(state)

    ctx = {"regimes": {}, "scanned_at": None}
    sent_any = False

    try:
        # ---- 1) อ่านคำสั่ง Telegram ที่พิมพ์ค้างไว้ (/status /why /levels /pause ...) ----
        try:
            cmd_cfg = dict(tg_cfg)
            cmd_cfg["enabled"] = (feats.get("commands") or {}).get("enabled", True)
            from trapbot import commands as tg_commands

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

        bars_for_track: dict[str, tuple[float, float]] = {}

        # ---- 3) ยกเลิกการ์ดที่หมดอายุทั้งหมดก่อน (ไม่ต้องรอดึงข้อมูล - ส่งได้แม้ช่วง /pause) ----
        for card in gate.expire_stale(state):
            _send_cancel(tg_cfg, tg_ready, dry_run, card, "หมดเวลาการ์ด")

        # ---- 4) สแกนทีละสินทรัพย์ ----
        for asset, acfg in cfg["assets"].items():
            if not acfg.get("enabled", False):
                continue
            try:
                signals, near, meta = scan_asset(asset, acfg, eng)
            except Exception as e:
                print(f"[{asset}] สแกนล้มเหลว: {e.__class__.__name__}: {e}", file=sys.stderr)
                continue

            if meta is not None:
                # การ์ดค้างของสินทรัพย์นี้: ราคาปิดทะลุจุดยกเลิกหรือยัง (ยกเลิกก่อนค่อยมองไม้ใหม่)
                try:
                    last_close = float(meta["df"]["close"].iloc[-2]) if len(meta["df"]) > 1 else None
                    for card in gate.check_invalidated(state, asset, last_close):
                        _send_cancel(tg_cfg, tg_ready, dry_run, card, "โครงสร้างพัง")
                except Exception as e:
                    print(f"[{asset}] เช็คการ์ดค้างล้ม ({e.__class__.__name__})", file=sys.stderr)

                # เก็บบริบทรอบนี้ไว้ตอบ /status /why + สรุปเช้า แล้วเดินไม้ค้างด้วยแท่งล่าสุด
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
                    if meta.get("regime_on"):
                        if old_reg and old_reg != reg_now["regime"] and tg_ready and not dry_run and not paused:
                            try:
                                send(tg_cfg, f"เปลี่ยนสภาพตลาด {DISPLAY_NAMES.get(asset, asset)}: "
                                             f"{regime_label(old_reg)} -> {regime_label(reg_now['regime'])} "
                                             f"(ATR เท่ากับ {reg_now['ratio']} ของค่ากลาง) - โหมดปรับเกณฑ์อัตโนมัติปรับกรองให้แล้ว")
                            except Exception:
                                pass
                except Exception as e:
                    print(f"[{asset}] เก็บบริบทล้ม ({e.__class__.__name__}) - สแกนยังปกติ", file=sys.stderr)

            # ---- 4a) จังหวะเกือบผ่าน -> การ์ดเฝ้าดู (เงียบ ไม่มีเสียง ไม่ซ่อน) ----
            watch_sent = 0
            for nm in near:
                try:
                    if watch_sent >= MAX_WATCH_PER_ASSET:
                        break
                    if nm["sweep_ts"] is not None and seen_event(state, nm["sweep_ts"], nm["asset"], nm["side"], nm["level_price"]):
                        continue  # เหตุการณ์นี้เคยส่งแล้ว (ทั้งการ์ดเต็มหรือเฝ้าดู)
                    sent = _send_watch(tg_cfg, tg_ready, dry_run, paused, nm)
                    if sent and not dry_run:
                        activity.add_watch(state)
                        activity.log_watch_card(state, nm["display"], nm["side"], nm["level_kind"], nm["level_price"], nm["fail_th"])
                        if nm["sweep_ts"] is not None:
                            mark_watch_event(state, nm["sweep_ts"], nm["asset"], nm["side"], nm["level_price"])
                    if sent:
                        watch_sent += 1
                except Exception as e:
                    print(f"[{asset}] การ์ดเฝ้าดูล้ม ({e.__class__.__name__})", file=sys.stderr)

            if not signals:
                print(f"[{asset}] รอบนี้ไม่มีจังหวะที่ผ่านครบทุกชั้นยืนยัน (ปกติ - ระบบออกแบบมาให้รอ)")
                continue

            # นับสมุด "พบ" (เหตุการณ์โครงสร้างครบที่ยังไม่เคยนับวันนี้ - dry-run ไม่เขียนสมุด)
            for sig in signals:
                if dry_run:
                    break
                try:
                    st = sig.extras.get("sweep_ts_dt") or sig.ts
                    activity.record_seen(state, f"{sig.asset}|{sig.side}|{sig.level_price:.2f}|{st:%Y%m%d%H%M}")
                except Exception:
                    pass

            # จัดลำดับคุณภาพ: R:R ที่ TP2 สูงสุดก่อน
            signals.sort(key=lambda s: s.rr_tp2, reverse=True)
            for sig in signals:
                # ---- ประตู "ยังเข้าได้": อายุ + ราคา - ไม่ทันจดสมุด "พบแต่ส่งไม่ทัน" ----
                ok, why = gate.evaluate(sig, meta["price_now"], meta["atr"], trad_cfg)
                if not ok:
                    print(f"[{asset}] พบแต่ส่งไม่ทัน: {why}")
                    if not dry_run:
                        activity.log_missed_card(state, sig.display_name, sig.side, sig.level_kind, sig.level_price, why)
                    continue
                if not dry_run:
                    activity.add_actionable(state)

                # ช่วง /pause: ไม่ส่ง ไม่จด ไม่กินโควตาเหตุการณ์ - ข้ามก่อนแตะ state
                if paused:
                    print(f"[{asset}] มีสัญญาณ ({sig.side} @ {sig.entry}) แต่อยู่ช่วง /pause - ไม่ส่ง ไม่จด")
                    continue

                # ---- ทอง (หรือสินทรัพย์ที่ตั้ง watch_outside_us): นอกเซสชั่นสหรัฐ = การ์ดเฝ้าดู ----
                if acfg.get("watch_outside_us") and not gate.in_us_session(acfg):
                    item = {
                        "asset": sig.asset, "display": sig.display_name, "side": sig.side,
                        "level_kind": sig.level_kind, "level_price": sig.level_price,
                        "fail_th": "นอกเซสชั่นตลาดสหรัฐ (โหมดเฝ้าดูทอง) - ฟีดหน่วง/ราคาไม่ตรงโบรก ยังไม่เปิดเข้าจริง",
                    }
                    sent = _send_watch(tg_cfg, tg_ready, dry_run, paused, item)
                    if sent and not dry_run:
                        activity.add_watch(state)
                        activity.log_watch_card(state, item["display"], item["side"], item["level_kind"], item["level_price"], item["fail_th"])
                        st = sig.extras.get("sweep_ts_dt") or sig.ts
                        if st is not None:
                            mark_watch_event(state, st, sig.asset, sig.side, sig.level_price)
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

                # ---- เกรด B เงียบ: ยังไม่ยิงจนกว่าเกรดจะพิสูจน์กำไรจริง (ดู /stats) ----
                if grade_b_quiet and grade == "B":
                    reason = "เกรด B ถูกเก็บไว้จนกว่าจะพิสูจน์กำไร (ดู /stats)"
                    activity.log_missed_card(state, sig.display_name, sig.side, sig.level_kind, sig.level_price, reason)
                    print(f"[{asset}] {reason}")
                    continue

                # ---- รหัสเหตุการณ์ถาวรกันซ้ำ (แท่งกวาดเดียวกันส่งครั้งเดียว) ----
                sweep_ts_dt = sig.extras.get("sweep_ts_dt") or sig.ts
                ok, msg = check_and_update(state, sig.asset, sig.side, sig.level_price, eng, sweep_ts=sweep_ts_dt)
                if not ok:
                    print(f"[{asset}] ข้ามสัญญาณ: {msg}")
                    continue

                # ---- อายุการ์ดบนหัวการ์ด: เข้าได้ถึงเมื่อไหร่ ----
                max_age = float(trad_cfg.get("max_age_minutes", 30))
                ts_utc = expire_ts = None
                try:
                    ts_utc = sig.ts if getattr(sig.ts, "tzinfo", None) else sig.ts.replace(tzinfo=timezone.utc)
                    expire_ts = ts_utc + timedelta(minutes=max_age)
                    sig.extras["expiry_line"] = (
                        f"เข้าได้ถึงประมาณ {fmt_th_time(expire_ts)} "
                        f"(หลังจากนั้นระบบจะแจ้งยกเลิกการ์ดเอง)"
                    )
                except Exception:
                    pass

                card = format_message(sig)
                png = None
                try:
                    if (feats.get("charts") or {}).get("enabled", True) and meta is not None:
                        png = signal_chart_png(sig, meta["df"], meta["levels"])
                except Exception as e:
                    print(f"[{asset}] วาดกราฟล้ม ({e.__class__.__name__}) - ส่งแค่การ์ดข้อความ", file=sys.stderr)

                if dry_run:
                    print("=" * 62)
                    print("[DRY RUN] การ์ดที่จะยิงเข้า Telegram (พร้อมเสียง):")
                    print(card)
                    if png:
                        print(f"[DRY RUN] + รูปกราฟแนบ {len(png) / 1024:.0f} KB")
                elif not tg_ready:
                    # ยังไม่ได้ใส่คีย์ Telegram: ระบบยังทำงานเต็มรูปแบบ พิมพ์การ์ดไว้ใน log แทน
                    print("=" * 62)
                    print("[ตั้งค่ายังไม่ครบ] ยังไม่มี TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID - วิธีใส่อยู่ README ขั้นที่ 5")
                    print("ระบบสแกนและคัดจังหวะทำงานปกติ พิมพ์การ์ดไว้ใน log นี้แทนการส่ง:")
                    print(card)
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
                        gate.register_card(state, sig, trad_cfg, expire_at=expire_ts)
                        if (feats.get("paper") or {}).get("enabled", True):
                            paper.ensure(state, feats.get("paper") or {})
                            paper.open_position(state["paper"], sig, grade)
                        activity.add_sent(state)
                        activity.webhook_event(
                            feats.get("trade_log_webhook"),
                            {"event": "signal", "asset": sig.asset, "display": sig.display_name,
                             "side": sig.side, "entry": sig.entry, "sl": sig.sl,
                             "tp1": sig.tp1, "tp2": sig.tp2, "tp3": sig.tp3,
                             "grade": grade, "ts": datetime.now(timezone.utc).isoformat()},
                        )
                    except Exception as e:
                        print(f"[{asset}] จดสมุด/พอร์ตจำลองล้ม ({e.__class__.__name__})", file=sys.stderr)
                sent_any = True
                break  # สินทรัพย์ละ 1 ไม้ต่อรอบสแกน (5 นาที) - กันสแปม ไม้ถัดไปออกรอบหน้า

        # ---- 5) เดินไม้ค้างทั้งสมุดจดผลและพอร์ตจำลองด้วยราคาล่าสุด + สวิตช์กันขาดทุนรายวัน ----
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
                    activity.add_paper_close(state, rec["r"])
                    activity.webhook_event(
                        feats.get("trade_log_webhook"),
                        {"event": "paper_close", "asset": rec["asset"], "display": rec["display"],
                         "outcome": rec["outcome"], "r": rec["r"], "pnl_usd": rec["pnl_usd"],
                         "ts": rec["closed_ts"]},
                    )
                    if tg_ready and not dry_run:
                        try:
                            arrow = "เปิดหมวยไม้จำลอง" if rec["r"] == 0 else ("กำไร" if rec["pnl_usd"] >= 0 else "ขาดทุน")
                            send(tg_cfg, f"ผลไม้จำลอง {rec['display']}: ปิดแบบ {rec['outcome']} ({rec['r']:+.1f}R) "
                                         f"{arrow} {rec['pnl_usd']:+,.2f}$ | พอร์ตตอนนี้ {rec['equity']:,.2f}$")
                        except Exception:
                            pass
                _check_loss_stop(state, feats, tg_cfg, tg_ready, dry_run)
        except Exception as e:
            print(f"[paper] เดินพอร์ตล้ม ({e.__class__.__name__})", file=sys.stderr)

        # ---- 6) สรุปเช้า (ส่งชดเชยถ้ามาช้า) + รายงานสัปดาห์วันจันทร์ ----
        if tg_ready and not dry_run and not paused:
            try:
                fcfg = dict(feats.get("morning_brief") or {})
                fcfg["ai_cfg"] = cfg["ai"]
                if maybe_send_brief(fcfg, state, tg_cfg, ctx, send_fn=send, send_photo_fn=send_photo):
                    print("[brief] ส่งสรุปเช้าแล้ว")
            except Exception as e:
                print(f"[brief] ล้ม ({e.__class__.__name__}) - ไม่กระทบรอบหลัก", file=sys.stderr)

        # ---- 7) พยากรณ์ทิศทางเช้า (ปิดใช้ตามมติ 3 AI - เปิดกลับได้ที่ features.morning_outlook.enabled) ----
        if tg_ready and not dry_run and not paused:
            try:
                ocfg = dict(feats.get("morning_outlook") or {})
                ocfg["ai_cfg"] = cfg["ai"]
                ocfg["dxy_trend"] = dxy_tr
                ocfg["_assets_cfg"] = cfg["assets"]
                if maybe_send_outlook(ocfg, state, tg_cfg, ctx, send_fn=send, send_photo_fn=send_photo):
                    print("[outlook] ส่งพยากรณ์ทิศทางเช้าแล้ว")
            except Exception as e:
                print(f"[outlook] ล้ม ({e.__class__.__name__}) - ไม่กระทบรอบหลัก", file=sys.stderr)

        if not sent_any:
            print("จบรอบ: ไม่มีสัญญาณใหม่")
        return 0
    finally:
        # เก็บบริบท + ชีพจรระบบ แล้วบันทึก state เสมอ แม้รอบจะล้มกลางทาง
        try:
            state["scan_ctx"] = {
                "scanned_at": ctx["scanned_at"].isoformat() if ctx.get("scanned_at") else None,
                "regimes": ctx.get("regimes") or {},
            }
        except Exception:
            pass
        if ctx.get("scanned_at"):
            state["last_ok_scan"] = datetime.now(timezone.utc).isoformat()
        try:
            save(state)
        except Exception as e:
            print(f"[state] บันทึก state ล้ม ({e.__class__.__name__})", file=sys.stderr)


def _check_loss_stop(state: dict, feats: dict, tg_cfg, tg_ready: bool, dry_run: bool) -> None:
    """สวิตช์กันขาดทุนเกินเพดานรายวัน (จากข้อเสนอ Nemotron - เกราะป้องกัน ไม่ใช่การเดาผล):
    ถ้าไม้จำลองที่ปิดวันนี้รวมกันแย่กว่าเพดาน (ค่าเริ่ม -3R) หยุดส่งการ์ดใหม่จนเที่ยงคืนวันนี้"""
    p = feats.get("paper") or {}
    cap = p.get("daily_loss_stop_r")
    if not p.get("daily_loss_stop_enabled", True) or cap is None:
        return
    rec = activity.ledger(state)
    if rec.get("paper_closed_r", 0.0) > float(cap):
        return
    if state.get("loss_stop_date") == activity.today_key():
        return
    state["loss_stop_date"] = activity.today_key()
    end_bkk = datetime.now(TH_TZ).replace(hour=23, minute=59, second=59, microsecond=0)
    state["paused_until"] = end_bkk.astimezone(timezone.utc).isoformat()
    msg = (f"สวิตช์กันขาดทุนทำงาน: ไม้จำลองปิดวันนี้รวม {rec.get('paper_closed_r', 0.0):+.1f}R "
           f"ต่ำกว่าเพดาน {float(cap):+.1f}R - หยุดส่งการ์ดใหม่จนเที่ยงคืนนี้ "
           f"(ระบบยังสแกนและจดผล พิมพ์ /resume เพื่อข้ามได้)")
    print(msg)
    if tg_ready and not dry_run:
        try:
            send(tg_cfg, msg)
        except Exception as e:
            print(f"[loss-stop] ส่งแจ้งล้ม ({e.__class__.__name__})", file=sys.stderr)


if __name__ == "__main__":
    try:
        code = main()
        watchdog.ping(ok=True)  # รอบปกติ: ชีพจรระบบ (Healthchecks.io)
        sys.exit(code)
    except SystemExit:
        raise
    except Exception:
        watchdog.ping(ok=False)  # รอบล้ม: บอก watchdog ให้เตือนผู้ใช้ทันที (ไม่ตายเงียบ)
        raise
