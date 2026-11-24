# -*- coding: utf-8 -*-
"""ชั้นดึงข้อมูลตลาด (Data Layer)

- BTC: Binance public REST (ฟรี ไม่ต้องใช้ key) ได้เทียนครบ OHLCV + taker buy volume
  ซึ่ง field taker_buy ใช้ประกอบ "แรงซื้อสุทธิ (Delta)" แบบไม่ต้องเรียก API เพิ่ม
- ทองคำ: ฟิวเจอร์ส COMEX (GC=F) ผ่าน yfinance ฟรี ไม่มี taker data -> delta ใช้ไม่ได้
ทุกอย่าง normalize เป็น UTC และ DataFrame คอลัมน์เดียวกันเสมอ:
  ts (UTC datetime), open, high, low, close, volume, taker_buy (มีเฉพาะ BTC)
"""
import time
from datetime import datetime, timedelta, timezone

import pandas as pd

TH_TZ = timezone(timedelta(hours=7))  # เวลาไทย


BINANCE_FALLBACK_HOSTS = ["https://data-api.binance.vision", "https://api.binance.com", "https://api1.binance.com"]


def _binance_klines(host: str, symbol: str, interval: str, limit: int) -> pd.DataFrame:
    import requests

    rows = None
    hosts = [host] + [h for h in BINANCE_FALLBACK_HOSTS if h != host]
    last_err = None
    for h in hosts:  # สลับ host สำรองอัตโนมัติ ถ้าโดน rate limit หรือเน็ตสะดุด
        try:
            url = f"{h}/api/v3/klines?symbol={symbol}&interval={interval}&limit={limit}"
            r = requests.get(url, timeout=20)
            if r.status_code == 200:
                rows = r.json()
                break
            last_err = f"HTTP {r.status_code} จาก {h}"
        except Exception as e:
            last_err = f"{e.__class__.__name__} จาก {h}"
        time.sleep(1)
    if rows is None:
        raise RuntimeError(f"Binance เรียกไม่สำเร็จ ({last_err})")
    # คอลัมน์ 10 ของ Binance kline คือ taker buy base asset volume
    df = pd.DataFrame(
        [
            {
                "ts": datetime.fromtimestamp(row[0] / 1000, tz=timezone.utc),
                "open": float(row[1]),
                "high": float(row[2]),
                "low": float(row[3]),
                "close": float(row[4]),
                "volume": float(row[5]),
                "taker_buy": float(row[9]),
            }
            for row in rows
        ]
    )
    return df


def _flatten_yf(df: pd.DataFrame) -> pd.DataFrame:
    """yfinance รุ่นใหม่คืนคอลัมน์ MultiIndex (Price, Ticker) - ม้วนเดียวให้เรียบ"""
    if isinstance(df.columns, pd.MultiIndex):
        df.columns = [c[0] for c in df.columns]
    return df


def _yf_ohlcv(symbol: str, interval: str, period: str) -> pd.DataFrame:
    import yfinance as yf

    df = yf.download(symbol, interval=interval, period=period, progress=False, auto_adjust=False)
    df = _flatten_yf(df)
    df = df.rename(columns={"Open": "open", "High": "high", "Low": "low", "Close": "close", "Volume": "volume"})
    df = df.dropna(subset=["open", "high", "low", "close"])
    df.index = pd.to_datetime(df.index)
    if df.index.tz is None:
        df.index = df.index.tz_localize("UTC")
    else:
        df.index = df.index.tz_convert("UTC")
    out = df[["open", "high", "low", "close", "volume"]].astype(float).reset_index()
    out.columns = ["ts", "open", "high", "low", "close", "volume"]
    out["taker_buy"] = float("nan")  # ทองไม่มีแรงซื้อสุทธิ - ใส่ NaN ไว้ก่อน
    return out


def fetch_trigger_data(asset_cfg: dict, lookback: int) -> pd.DataFrame:
    """ดึงเทียนไทม์เฟรมหลัก (เช่น 15m) - จำนวนหลังพอสำหรับ lookback + กันเหลือ"""
    if asset_cfg["source"] == "binance":
        return _binance_klines(asset_cfg["binance_host"], asset_cfg["symbol"], asset_cfg["trigger_tf"], lookback + 100)
    period = "60d" if asset_cfg["trigger_tf"].endswith("m") else "1y"
    return _yf_ohlcv(asset_cfg["yf_symbol"], asset_cfg["trigger_tf"], period)


def fetch_htf_data(asset_cfg: dict) -> pd.DataFrame:
    """ดึงเทียนใหญ่ (เช่น 1h) สำหรับกำหนดทิศทางรวม"""
    if asset_cfg["source"] == "binance":
        return _binance_klines(asset_cfg["binance_host"], asset_cfg["symbol"], asset_cfg["htf"], 300)
    return _yf_ohlcv(asset_cfg["yf_symbol"], asset_cfg["htf"], "90d")


def fetch_daily_data(asset_cfg: dict) -> pd.DataFrame:
    """ดึงเทียนรายวัน เพื่อหา จุดสูง/ต่ำของวันก่อน (PDH/PDL) - กับดักใหญ่ระดับตลาดโลก"""
    if asset_cfg["source"] == "binance":
        return _binance_klines(asset_cfg["binance_host"], asset_cfg["symbol"], "1d", 30)
    return _yf_ohlcv(asset_cfg["yf_symbol"], "1d", "30d")


def prev_completed_daily(df_daily: pd.DataFrame) -> dict | None:
    """เทียนรายวันแท่งสุดท้ายมักยังไม่ปิด - ใช้แท่งก่อนหน้า (วันที่ปิดสมบูรณ์แล้ว)"""
    if df_daily is None or len(df_daily) < 2:
        return None
    row = df_daily.iloc[-2]
    return {"high": float(row["high"]), "low": float(row["low"]), "ts": row["ts"]}


def fmt_price(value: float, asset: str) -> str:
    """จัดรูปแบบราคาให้อ่านง่าย: BTC เว้นพัน ทองทศนิยม 1 ตำแหน่ง"""
    if asset.startswith("BTC"):
        return f"{value:,.0f}"
    return f"{value:,.1f}"


def fmt_th_time(ts) -> str:
    return ts.astimezone(TH_TZ).strftime("%d/%m/%Y %H:%M") + " น. (ไทย)"
