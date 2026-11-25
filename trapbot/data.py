# -*- coding: utf-8 -*-
"""ชั้นดึงข้อมูลตลาด (Data Layer) - รองรับ 7 สินทรัพย์

- Binance (ฟรี ไม่ต้องใช้ key): BTCUSDT, SOLUSDT, XRPUSDT
  ได้เทียนครบ OHLCV + taker buy volume ซึ่งใช้ประกอบ "แรงซื้อสุทธิ (Delta)"
- yfinance (ฟรี): XAUUSD (GC=F), XAGUSD (SI=F), WTIUSD (CL=F), NAS100 (NQ=F)
  ไม่มีข้อมูล delta -> ใช้กฎ fast reclaim แทน
- ข้อมูลเสริมสำหรับยืนยันข้ามตลาด (Confluence): funding rate + open interest (BTC)
  และดัชนีดอลลาร์ DXY (ต่อต้านทอง/เงิน/NAS100)

เทคนิคกันโดน Yahoo เข้มงวด: รวมดึง yfinance เป็น "ชุด" ครั้งเดียวต่อรอบ (prefetch_yf_batches)
แล้วโมดูลอื่นดึงจาก cache ในหน่วยความจำ - ลดจำนวน HTTP request จาก 12 เหลือ 3 ต่อรอบ

ทุกอย่าง normalize เป็น UTC และ DataFrame คอลัมน์เดียวกันเสมอ:
  ts (UTC datetime), open, high, low, close, volume, taker_buy (มีเฉพาะ Binance)
"""
import time
from datetime import datetime, timedelta, timezone

import pandas as pd

TH_TZ = timezone(timedelta(hours=7))  # เวลาไทย (ไม่มี DST จึงคงที่ +7 ตลอด)

BINANCE_FALLBACK_HOSTS = ["https://data-api.binance.vision", "https://api.binance.com", "https://api1.binance.com"]

# ---- จัดรูปราคาให้อ่านง่ายตามสินทรัพย์ ----
_PRICE_DECIMALS = {
    "BTCUSDT": 0,
    "XAUUSD": 1,
    "XAGUSD": 2,
    "SOLUSDT": 2,
    "XRPUSDT": 4,
    "WTIUSD": 2,
    "NAS100": 1,
}

DISPLAY_NAMES = {
    "BTCUSDT": "BTC/USDT",
    "XAUUSD": "ทองคำ (XAUUSD)",
    "XAGUSD": "เงิน (XAGUSD)",
    "SOLUSDT": "Solana (SOL/USDT)",
    "XRPUSDT": "XRP/USDT",
    "WTIUSD": "น้ำมันดิบ (WTI)",
    "NAS100": "NAS100 (NQ=F)",
}


def price_decimals(asset: str) -> int:
    return _PRICE_DECIMALS.get(asset, 2)


def fmt_price(value: float, asset: str) -> str:
    return f"{value:,.{price_decimals(asset)}f}"


# ================= Binance (spot + derivatives สำหรับ confluence) =================

def _binance_get(path: str, host: str, params: str = "", timeout: int = 20):
    """GET จาก Binance พร้อมสลับ host สำรองอัตโนมัติ คืน dict/list หรือ raise"""
    import requests

    hosts = [host] + [h for h in BINANCE_FALLBACK_HOSTS if h != host]
    last_err = None
    for h in hosts:
        try:
            url = f"{h}{path}" + (f"?{params}" if params else "")
            r = requests.get(url, timeout=timeout)
            if r.status_code == 200:
                return r.json()
            last_err = f"HTTP {r.status_code} จาก {h}"
        except Exception as e:
            last_err = f"{e.__class__.__name__} จาก {h}"
        time.sleep(1)
    raise RuntimeError(f"Binance เรียกไม่สำเร็จ ({last_err})")


def _binance_klines(host: str, symbol: str, interval: str, limit: int) -> pd.DataFrame:
    # คอลัมน์ 10 ของ Binance kline คือ taker buy base asset volume (แรงซื้อสุทธิ)
    rows = _binance_get("/api/v3/klines", host, f"symbol={symbol}&interval={interval}&limit={limit}")
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


def fetch_funding_rate(symbol: str) -> float | None:
    """อัตรา Funding ล่าสุดของคอยแบบฟิวเจอร์ส (บวก = ฝั่ง Long จ่ายค่าธรรมเนียม = ฝั่ง Long แน่น)"""
    try:
        data = _binance_get("/fapi/v1/premiumIndex", BINANCE_FALLBACK_HOSTS[0], f"symbol={symbol}")
        return float(data.get("lastFundingRate") or 0.0)
    except Exception:
        return None


def fetch_oi_change(symbol: str) -> float | None:
    """เปลี่ยนแปลง Open Interest 24 ชม. ล่าสุดเป็น % (บวก = เงินไหลเข้าตลาดฟิวเจอร์ส)"""
    try:
        rows = _binance_get(
            "/futures/data/openInterestHist",
            BINANCE_FALLBACK_HOSTS[0],
            f"symbol={symbol}&period=1h&limit=25",
        )
        if not rows or len(rows) < 2:
            return None
        first, last = float(rows[0]["sumOpenInterest"]), float(rows[-1]["sumOpenInterest"])
        if first <= 0:
            return None
        return round((last - first) / first * 100, 2)
    except Exception:
        return None


# ================= yfinance (มี cache + ดึงเป็นชุด) =================

_YF_CACHE: dict[tuple[str, str], pd.DataFrame] = {}

YF_PERIOD_BY_INTERVAL = {"15m": "60d", "30m": "60d", "1h": "90d", "1d": "30d"}


def _normalize_yf(df: pd.DataFrame) -> pd.DataFrame | None:
    if df is None or df.empty:
        return None
    if isinstance(df.columns, pd.MultiIndex):
        df = df.copy()
        lvl0 = set(map(str, df.columns.get_level_values(0)))
        FIELD_NAMES = {"Open", "High", "Low", "Close", "Adj Close", "Volume"}
        if lvl0 & FIELD_NAMES:
            # โครงสร้าง (Field, Ticker) - ใช้ level0
            df.columns = [c[0] for c in df.columns]
        else:
            # โครงสร้าง (Ticker, Field) - ใช้ level1
            df.columns = [c[1] for c in df.columns]
        if df.columns.duplicated().any():
            df = df.loc[:, ~df.columns.duplicated()]
    df = df.rename(columns={"Open": "open", "High": "high", "Low": "low", "Close": "close", "Volume": "volume"})
    df = df.dropna(subset=["open", "high", "low", "close"])
    if df.empty:
        return None
    df.index = pd.to_datetime(df.index)
    if df.index.tz is None:
        df.index = df.index.tz_localize("UTC")
    else:
        df.index = df.index.tz_convert("UTC")
    out = df[["open", "high", "low", "close", "volume"]].astype(float).reset_index()
    out.columns = ["ts", "open", "high", "low", "close", "volume"]
    out["taker_buy"] = float("nan")  # สินทรัพย์กลุ่มนี้ไม่มีแรงซื้อสุทธิ
    return out


def _extract_yf_symbol(raw: pd.DataFrame, sym: str, total: int) -> pd.DataFrame | None:
    """ดึงข้อมูลของสัญลักษณ์เดียวออกจากผล yf.download แบบหลายสัญลักษณ์
    รองรับทั้งสองรูปแบบคอลัมน์ที่ yfinance ใช้ (level0=ticker หรือ level1=ticker)"""
    if raw is None or raw.empty:
        return None
    if not isinstance(raw.columns, pd.MultiIndex):
        return raw if total == 1 else None
    lvl0 = set(map(str, raw.columns.get_level_values(0)))
    lvl1 = set(map(str, raw.columns.get_level_values(1)))
    if sym in lvl0:
        return raw[sym]
    if sym in lvl1:
        return raw.xs(sym, axis=1, level=1)
    return None


def prefetch_yf_batches(symbols_by_interval: dict[str, list[str]]) -> int:
    """รวมดึง yfinance หลายสัญลักษณ์ในคำสั่งเดียวต่อไทม์เฟรม (กันโดน rate limit)

    คืนจำนวนสัญลักษณ์ที่ได้ข้อมูลสำเร็จ ตัวที่พลาดจะดึงรายตัวตอน fetch จริง (graceful)
    """
    ok = 0
    for interval, symbols in symbols_by_interval.items():
        symbols = [s for s in dict.fromkeys(symbols) if s]
        if not symbols:
            continue
        period = YF_PERIOD_BY_INTERVAL.get(interval, "60d")
        try:
            import yfinance as yf

            raw = yf.download(
                symbols, interval=interval, period=period, progress=False, auto_adjust=False,
                group_by="ticker", threads=True,
            )
        except Exception:
            continue
        if raw is None or raw.empty:
            continue
        for sym in symbols:
            try:
                norm = _normalize_yf(_extract_yf_symbol(raw, sym, len(symbols)))
                min_rows = 10 if interval == "1d" else 50  # เทียนรายวัน 30 วันได้แค่ ~25 แท่ง
                if norm is not None and len(norm) > min_rows:
                    _YF_CACHE[(sym, interval)] = norm
                    ok += 1
            except Exception:
                continue
    return ok


def _yf_ohlcv(symbol: str, interval: str, period: str) -> pd.DataFrame:
    cached = _YF_CACHE.get((symbol, interval))
    if cached is not None:
        return cached.copy()
    import yfinance as yf

    df = yf.download(symbol, interval=interval, period=period, progress=False, auto_adjust=False)
    norm = _normalize_yf(df)
    if norm is None:
        raise RuntimeError(f"yfinance ไม่มีข้อมูล {symbol} ({interval})")
    _YF_CACHE[(symbol, interval)] = norm
    return norm.copy()


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


def collect_yf_specs(assets: dict) -> dict[str, list[str]]:
    """รวมรายชื่อสัญลักษณ์ yfinance ทั้งหมดที่จะต้องใช้รอบนี้ (แยกตามไทม์เฟรม)"""
    specs: dict[str, list[str]] = {}
    for acfg in assets.values():
        if acfg.get("source") != "yfinance" or not acfg.get("enabled", False):
            continue
        for iv in (acfg.get("trigger_tf", "15m"), acfg.get("htf", "1h"), "1d"):
            specs.setdefault(iv, []).append(acfg["yf_symbol"])
    return specs


def fetch_dxy_daily() -> pd.DataFrame | None:
    """ดัชนีดอลลาร์ DXY รายวัน 10 วันล่าสุด - ใช้ยืนยันทิศทอง/เงิน/NAS100 (หลักการ: ดอลลาร์แรง ตัวต้านอ่อน)"""
    try:
        return _yf_ohlcv("DX-Y.NYB", "1d", "30d")
    except Exception:
        return None


def dxy_trend(df_dxy: pd.DataFrame | None) -> str | None:
    """ทิศทาง DXY ล่าสุด 5 วัน: UP / DOWN / FLAT"""
    try:
        if df_dxy is None or len(df_dxy) < 6:
            return None
        closes = df_dxy["close"].values
        a, b = float(closes[-6]), float(closes[-1])
        chg = (b - a) / a * 100
        if chg > 0.25:
            return "UP"
        if chg < -0.25:
            return "DOWN"
        return "FLAT"
    except Exception:
        return None


def prev_completed_daily(df_daily: pd.DataFrame) -> dict | None:
    """เทียนรายวันแท่งสุดท้ายมักยังไม่ปิด - ใช้แท่งก่อนหน้า (วันที่ปิดสมบูรณ์แล้ว)"""
    if df_daily is None or len(df_daily) < 2:
        return None
    row = df_daily.iloc[-2]
    return {"high": float(row["high"]), "low": float(row["low"]), "ts": row["ts"]}


def fmt_th_time(ts) -> str:
    return ts.astimezone(TH_TZ).strftime("%d/%m/%Y %H:%M") + " น. (ไทย)"


def bangkok_now():
    """เวลาไทยปัจจุบันเป็น datetime ที่มี tz"""
    return datetime.now(timezone.utc).astimezone(TH_TZ)
