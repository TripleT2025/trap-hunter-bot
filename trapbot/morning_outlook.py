# -*- coding: utf-8 -*-
"""พยากรณ์ทิศทางรายวัน BTC + ทองคำ (Daily Outlook) - การ์ดเช้าที่อ่านจบใน 1 นาที

โจทย์: ผู้ใช้อยากเปิด Telegram ตอนเช้า 08:00 น. (เวลาไทย) แล้วเห็นทันทีว่า
"วันนี้ BTC กับทองคำ ไปทางไหน" พร้อม "จุดรับ-จุดต้าน" ที่ใช้วางแผนได้จริง และต้องสวยพอให้อ่านสนุก

ทิศทางมาจากไหน (โปร่งใสทุกข้อ ไม่ใช่กล่องดำ):
  1. EMA20 vs EMA50 บนกราฟรายวัน          (แนวโน้มหลัก)
  2. ราคาล่าสุดอยู่เหนือ/ใต้ EMA20 รายวัน  (ตำแหน่งเทียบแนวโน้ม)
  3. โมเมนตัม 5 วัน                       (แรงส่งล่าสุด)
  4. ทะลุกรอบ 3 วันหรือยัง                 (โครงสร้างสั้น)
  5. โครงสร้างกราฟ 1 ชม. (EMA20/EMA50)    (ทิศทางรายชั่วโมงสอดคล้องไหม)
  6. ปัจจัยรอบตัว: ทองดูทิศ DXY / BTC ดู Funding (เชิงจิตวิทยาตลาด)

จุดรับ-จุดต้านรวบรวมจาก 3 ตระกูลที่เทรดเดอร์ทั่วโลกมองเหมือนกัน:
  - Pivot Points คลาสสิกของวันก่อน (PP/R1/R2/S1/S2)
  - จุดสูง-ต่ำวันก่อน (PDH/PDL) + จุดสูง-ต่ำ 5 วัน
  - เลขสวยกลม (round step ของแต่ละสินทรัพย์) และเส้น EMA20 รายวัน
แล้วคัดเฉพาะโซนที่ "ใกล้ราคาที่สุด" ฝั่งละ 2 โซน โดยรวมโซนที่ชนกันเป็นจุดเดียว

แผนเล่น (Plan A / Plan B) สรุปเป็นประโยคเดียวที่อ่านแล้วรู้ทันทีว่าต้องรออะไร
พร้อมจุดยกเลิก (invalidation) อัตโนมัติที่ 0.25 ATR หลังโซน - สอดคล้องปรัชญาเดียวกับตัวสแกนหลัก

กราฟแนบ: เทียนรายวัน 60 แท่ง + เส้น EMA20/EMA50 + เส้นจุดรับ (เขียว) - จุดต้าน (แดง)
ข้อความบนกราฟเป็นอังกฤษล้วน เพราะเซิร์ฟเวอร์ GitHub ไม่มีฟอนต์ไทย (ความหมายไทยอยู่บนการ์ดข้อความ)

กันส่งซ้ำ: เก็บ state["outlook_date"] ตามรอบที่ส่งแล้ว - เหมือนกลไกของสรุปเช้า (brief.py)
ถ้าดึงข้อมูลล้มทั้งสองสินทรัพย์จะไม่จดวัน แล้วลองใหม่รอบสแกนถัดไป (ทุก 5 นาที) จนหมดช่วงเช้า
"""
import sys

from .data import bangkok_now, fetch_htf_data, fmt_price, price_decimals

THAI_WEEKDAYS = ["จันทร์", "อังคาร", "พุธ", "พฤหัสบดี", "ศุกร์", "เสาร์", "อาทิตย์"]
THAI_MONTHS = [
    "มกราคม", "กุมภาพันธ์", "มีนาคม", "เมษายน", "พฤษภาคม", "มิถุนายน",
    "กรกฎาคม", "สิงหาคม", "กันยายน", "ตุลาคม", "พฤศจิกายน", "ธันวาคม",
]

# ป้ายชื่อโซนภาษาไทย - แสดงบนการ์ดให้รู้ว่าจุดนี้มาจากไหน
KIND_TH = {
    "PDH": "จุดสูงวันก่อน (PDH)", "PDL": "จุดต่ำวันก่อน (PDL)",
    "PP": "จุดกึ่งกลาง Pivot", "R1": "แนวต้าน Pivot R1", "R2": "แนวต้าน Pivot R2",
    "S1": "แนวรับ Pivot S1", "S2": "แนวรับ Pivot S2",
    "ROUND": "เลขสวยกลม", "SWING_HIGH": "จุดสูงสุด 5 วัน", "SWING_LOW": "จุดต่ำสุด 5 วัน",
    "EMA20_D": "เส้นแนวโน้ม EMA20 รายวัน",
}

DISPLAY_EN = {"BTCUSDT": "BTC/USDT", "XAUUSD": "GOLD (XAUUSD)"}

TREND_TH = {"UP": "ขาขึ้น", "DOWN": "ขาลง", "SIDEWAYS": "อึดอัด/ไล่ระดับ"}


# ============================== ดึงข้อมูล ==============================

def _fetch_daily_6m(acfg: dict):
    """เทียนรายวันย้อนหลัง ~6 เดือน เฉพาะสำหรับรายงานนี้ (EMA50 ต้องการประวัติยาวกว่าชุดสแกนปกติ)"""
    try:
        if acfg.get("source") == "binance":
            from .data import _binance_klines

            return _binance_klines(acfg["binance_host"], acfg["symbol"], "1d", 200)
        import yfinance as yf

        from .data import _normalize_yf

        raw = yf.download(acfg["yf_symbol"], interval="1d", period="6mo", progress=False, auto_adjust=False)
        return _normalize_yf(raw)
    except Exception as e:
        print(f"[outlook] ดึงเทียนรายวันล้ม ({e.__class__.__name__})", file=sys.stderr)
        return None


# ============================== วิเคราะห์ทิศทาง ==============================

def _trend_votes(df_daily, df_htf, extra: dict) -> dict:
    """ให้คะแนนทิศทางจาก 6 สัญญาณ คืน (ทิศทาง, โหวตขึ้น, โหวตลง, บรรทัดอธิบาย)"""
    close = df_daily["close"]
    ema20 = close.ewm(span=20, adjust=False).mean()
    ema50 = close.ewm(span=50, adjust=False).mean()
    c_now = float(close.iloc[-1])

    mom5 = (c_now / float(close.iloc[-6]) - 1) * 100 if len(close) >= 6 else 0.0
    hi3 = float(df_daily["high"].iloc[-4:-1].max()) if len(df_daily) >= 4 else c_now
    lo3 = float(df_daily["low"].iloc[-4:-1].min()) if len(df_daily) >= 4 else c_now

    hc = df_htf["close"]
    h_ema20 = float(hc.ewm(span=20, adjust=False).mean().iloc[-1])
    h_ema50 = float(hc.ewm(span=50, adjust=False).mean().iloc[-1]) if len(hc) >= 50 else h_ema20

    checks = [
        ("EMA20 รายวันอยู่เหนือ EMA50", 1 if float(ema20.iloc[-1]) > float(ema50.iloc[-1]) else -1),
        ("ราคายืนเหนือ EMA20 รายวัน", 1 if c_now > float(ema20.iloc[-1]) else -1),
        ("โมเมนตัม 5 วันเป็นบวก", 1 if mom5 > 0 else -1),
        ("ทะลุจุดสูง 3 วันแล้ว", 1 if c_now > hi3 else (-1 if c_now < lo3 else 0)),
        ("กราฟ 1 ชม. โครงสร้างขาขึ้น", 1 if h_ema20 > h_ema50 else -1),
    ]
    # ปัจจัยรอบตัว: ทอง -> ดอลลาร์แรงเป็นตัวถ่วง / BTC -> Funding สูง = ฝูง Long แน่น เสี่ยงถูกตีขา
    if extra.get("dxy") == "DOWN":
        checks.append(("ดอลลาร์อ่อนตัว หนุนราคา", 1))
    elif extra.get("dxy") == "UP":
        checks.append(("ดอลลาร์แรง กดราคา", -1))
    else:
        checks.append(("ดอลลาร์นิ่ง เป็นกลาง", 0))

    ups = sum(1 for _, v in checks if v > 0)
    downs = sum(1 for _, v in checks if v < 0)
    if ups > downs:
        direction = "UP"
    elif downs > ups:
        direction = "DOWN"
    else:
        direction = "SIDEWAYS"

    lines = []
    for name, v in checks:
        mark = "✓" if v > 0 else ("✗" if v < 0 else "–")
        lines.append(f"{mark} {name}")
    mom_txt = f"{'+' if mom5 >= 0 else ''}{mom5:.2f}%"
    lines[2] = f"{'✓' if mom5 > 0 else ('✗' if mom5 < 0 else '–')} โมเมนตัม 5 วัน {mom_txt}"
    return {"direction": direction, "ups": ups, "downs": downs, "total": len(checks), "detail": lines}


# ============================== จุดรับ-จุดต้าน ==============================

def _candidate_levels(df_daily, price_now: float, round_step: float) -> list[tuple[float, str]]:
    """รวมโซนสภาพคล่องทั้งหมดจาก 3 ตระกูล: Pivot วันก่อน / PDH-PDL + Swing 5 วัน / เลขสวย + EMA20"""
    prev = df_daily.iloc[-2]  # วันที่ปิดสมบูรณ์แล้ว
    h, l, c = float(prev["high"]), float(prev["low"]), float(prev["close"])
    pp = (h + l + c) / 3.0
    cands = [
        (h, "PDH"), (l, "PDL"),
        (pp, "PP"), (2 * pp - l, "R1"), (pp + (h - l), "R2"),
        (2 * pp - h, "S1"), (pp - (h - l), "S2"),
    ]
    win = df_daily.iloc[-6:-1]  # 5 วันก่อนหน้า (ไม่รวมแท่งวันนี้ที่ยังไม่ปิด)
    if len(win):
        cands.append((float(win["high"].max()), "SWING_HIGH"))
        cands.append((float(win["low"].min()), "SWING_LOW"))
    base = round(price_now / round_step) * round_step
    for k in (-1, 1):
        cands.append((base + k * round_step, "ROUND"))
    ema20 = float(df_daily["close"].ewm(span=20, adjust=False).mean().iloc[-1])
    cands.append((ema20, "EMA20_D"))
    return cands


def _pick_sides(cands: list[tuple[float, str]], price: float, atr_d: float, above: bool, n: int = 2):
    """คัดโซนใกล้ราคาที่สุดฝั่งละ n โซน โดยรวมโซนที่อยู่ใกล้กัน (ห่าง < 0.35 ATR) เป็นจุดเดียว"""
    pool = sorted(
        [(p, k) for p, k in cands if (p > price) == above and abs(p - price) > 0.05 * atr_d],
        key=lambda x: abs(x[0] - price),
    )
    picked: list[tuple[float, str]] = []
    for p, k in pool:
        if any(abs(p - q) < 0.35 * atr_d for q, _ in picked):
            continue
        picked.append((p, k))
        if len(picked) >= n:
            break
    picked.sort(key=lambda x: x[0], reverse=not above)  # รับเรียงจากใกล้ไปไกลล่าง / ต้านเรียงใกล้ไปไกลขึ้น
    return picked


# ============================== วิเคราะห์ 1 สินทรัพย์ ==============================

def analyze_asset(asset: str, acfg: dict, dxy: str | None = None) -> dict | None:
    """วิเคราะห์ครบชุดสำหรับสินทรัพย์หนึ่งตัว - ล้มคืน None เสมอ (ไม่กระทบส่วนอื่น)"""
    try:
        df_daily = _fetch_daily_6m(acfg)
        if df_daily is None or len(df_daily) < 30:
            print(f"[outlook] {asset}: เทียนรายวันไม่พอ ({0 if df_daily is None else len(df_daily)} แท่ง)")
            return None
        df_htf = fetch_htf_data(acfg)
        if df_htf is None or len(df_htf) < 60:
            df_htf = df_daily

        price = float(df_daily["close"].iloc[-1])
        from .liquidity import atr

        atr_d = float(atr(df_daily, 14).iloc[-1])
        if atr_d <= 0 or price <= 0:
            return None

        extra = {"dxy": dxy}
        funding = None
        if asset == "BTCUSDT":
            try:
                from .data import fetch_funding_rate

                funding = fetch_funding_rate(acfg["symbol"])
                extra["dxy"] = None
                if funding is not None:
                    extra["funding_strong_long"] = funding >= 0.0005
            except Exception:
                funding = None
        # ปัจจัยทอง: ใช้ทิศ DXY ที่ส่งเข้ามา (ถ้าไม่ส่งมาลองดึงเอง)
        if asset == "XAUUSD" and dxy is None:
            try:
                from .data import dxy_trend, fetch_dxy_daily

                extra["dxy"] = dxy_trend(fetch_dxy_daily())
            except Exception:
                extra["dxy"] = None

        t = _trend_votes(df_daily, df_htf, extra)
        t["_dxy"] = extra.get("dxy")  # เก็บทิศ DXY ไว้โชว์เป็นบรรทัดบริบทบนการ์ด

        prev_close = float(df_daily["close"].iloc[-2])
        chg_day = (price / prev_close - 1) * 100 if prev_close > 0 else 0.0

        cands = _candidate_levels(df_daily, price, float(acfg.get("round_step", 1)))
        supports = _pick_sides(cands, price, atr_d, above=False)
        resistances = _pick_sides(cands, price, atr_d, above=True)

        # ---- แผนเล่น A / B อัตโนมัติจากทิศทาง + โซนที่หาได้ ----
        s1 = supports[0][0] if supports else price - atr_d
        r1 = resistances[0][0] if resistances else price + atr_d
        r2 = resistances[1][0] if len(resistances) > 1 else r1 + atr_d
        s2 = supports[1][0] if len(supports) > 1 else s1 - atr_d
        cancel_buy = s1 - 0.25 * atr_d   # ยกเลิกไม้ซื้อ: ปิดใต้โซนรับเล็กน้อย
        cancel_sell = r1 + 0.25 * atr_d  # ยกเลิกไม้ขาย: ปิดเหนือโซนต้านเล็กน้อย

        if t["direction"] == "UP":
            plans = [
                f"รอย่อแตะโซนรับ <b>{fmt_price(s1, asset)}</b> ถ้ามีแท่งกลับตัวยืนยัน → จังหวะเข้าขาขึ้น เป้าแรก <b>{fmt_price(r1, asset)}</b> (ยกเลิกถ้าปิดใต้ <b>{fmt_price(cancel_buy, asset)}</b>)",
                f"ปิดเหนือโซนต้าน <b>{fmt_price(r1, asset)}</b> ยืนยัน → ตามกระแสขึ้น เป้าถัดไป <b>{fmt_price(r2, asset)}</b>",
            ]
        elif t["direction"] == "DOWN":
            plans = [
                f"รอดันแตะโซนต้าน <b>{fmt_price(r1, asset)}</b> ถ้ามีแท่งกลับตัวยืนยัน → จังหวะเข้าขาลง เป้าแรก <b>{fmt_price(s1, asset)}</b> (ยกเลิกถ้าปิดเหนือ <b>{fmt_price(cancel_sell, asset)}</b>)",
                f"ปิดใต้โซนรับ <b>{fmt_price(s1, asset)}</b> ยืนยัน → ตามกระแสลง เป้าถัดไป <b>{fmt_price(s2, asset)}</b>",
            ]
        else:
            plans = [
                f"ตลาดไล่ระดับในกรอบ: รอย่อขอบล่าง <b>{fmt_price(s1, asset)}</b> เข้ารอจุดต้าน <b>{fmt_price(r1, asset)}</b> (ยกเลิกถ้าปิดใต้ <b>{fmt_price(cancel_buy, asset)}</b>)",
                f"ทะลุกรอบยืนยัน (ปิดเหนือ <b>{fmt_price(r1, asset)}</b> หรือใต้ <b>{fmt_price(s1, asset)}</b>) → รอตามทิศที่ทะลุ",
            ]

        return {
            "asset": asset,
            "display": acfg.get("display") or _display_th(asset),
            "name_en": DISPLAY_EN.get(asset, asset),
            "price": price,
            "chg_day": chg_day,
            "atr_d": atr_d,
            "trend": t,
            "supports": supports,
            "resistances": resistances,
            "plans": plans,
            "funding": funding,
            "df_daily": df_daily,
        }
    except Exception as e:
        print(f"[outlook] วิเคราะห์ {asset} ล้ม ({e.__class__.__name__})", file=sys.stderr)
        return None


def _display_th(asset: str) -> str:
    names = {"BTCUSDT": "BTC/USDT", "XAUUSD": "ทองคำ (XAUUSD)"}
    return names.get(asset, asset)


# ============================== เรียบการ์ดข้อความ ==============================

def _pct(p: float, price: float) -> str:
    d = (p - price) / price * 100
    return f"{'+' if d >= 0 else ''}{d:.2f}%"


def _trend_emoji(direction: str) -> str:
    return {"UP": "📈", "DOWN": "📉", "SIDEWAYS": "↔️"}.get(direction, "•")


def _context_line(o: dict) -> str | None:
    """บรรทัดปัจจัยรอบตัวเฉพาะสินทรัพย์ (Funding ของ BTC / DXY ของทอง)"""
    if o["asset"] == "BTCUSDT" and o.get("funding") is not None:
        f = o["funding"] * 100
        mood = "ฝูง Long แน่น ระวังถูกตีขา" if f >= 0.05 else ("ฝั่ง Long ไม่แน่น ที่ว่างเปิด" if f <= 0 else "ฝั่ง Long ปกติ")
        return f"💸 Funding: {f:+.3f}% ({mood})"
    if o["asset"] == "XAUUSD":
        dxy = (o["trend"].get("_dxy") or "")
        if dxy == "UP":
            return "💵 ดอลลาร์ (DXY) 5 วันล่าสุดแรง → เป็นแรงกดทอง"
        if dxy == "DOWN":
            return "💵 ดอลลาร์ (DXY) 5 วันล่าสุดอ่อน → เป็นแรงหนุนทอง"
        if dxy == "FLAT":
            return "💵 ดอลลาร์ (DXY) นิ่ง → ไม่เป็นแรงหนุนหรือกด"
    return None


def _asset_section(o: dict, ai_comment: str | None = None) -> str:
    d = o["trend"]["direction"]
    t = o["trend"]
    dec = price_decimals(o["asset"])
    chg = o["chg_day"]
    lines = [
        f"🥇 <b>{o['display']}</b>" if o["asset"] == "XAUUSD" else f"🪙 <b>{o['display']}</b>",
        f"💰 ราคาล่าสุด: <b>{fmt_price(o['price'], o['asset'])}</b> (เทียบปิดเมื่อวาน <b>{'+' if chg >= 0 else ''}{chg:.2f}%</b>)",
        f"🧭 ทิศทางวันนี้: {_trend_emoji(d)} <b>{TREND_TH[d]} ({d})</b>",
        f"   น้ำหนักสัญญาณ: <b>{t['ups']}/{t['total']}</b> เอียงขึ้น | <b>{t['downs']}/{t['total']}</b> เอียงลง",
    ]
    ctx = _context_line(o)
    if ctx:
        lines.append(f"   {ctx}")
    lines.append("📏 <i>เหตุผลโปร่งใสทุกข้อ:</i>")
    for ln in t["detail"]:
        lines.append(f"   {ln}")
    lines.append(f"📏 คาดช่วงเดินของวันนี้ ~{o['atr_d']:.{dec}f} หน่วย (ATR 14 วัน)")
    lines.append("")
    lines.append("🟢 <b>จุดรับ (Support)</b>")
    for i, (p, kind) in enumerate(o["supports"], start=1):
        lines.append(f"   S{i} ─ <b>{fmt_price(p, o['asset'])}</b> ({KIND_TH.get(kind, kind)}) | ห่าง {_pct(p, o['price'])}")
    lines.append("🔴 <b>จุดต้าน (Resistance)</b>")
    for i, (p, kind) in enumerate(o["resistances"], start=1):
        lines.append(f"   R{i} ─ <b>{fmt_price(p, o['asset'])}</b> ({KIND_TH.get(kind, kind)}) | ห่าง {_pct(p, o['price'])}")
    lines.append("")
    lines.append("🎯 <b>แผนเล่นวันนี้</b>")
    lines.append(f"   🅰️ {o['plans'][0]}")
    lines.append(f"   🅱️ {o['plans'][1]}")
    if ai_comment:
        lines.append("")
        lines.append(f"✨ <i>มุมมอง AI เสริม:</i> {ai_comment}")
    return "\n".join(lines)


def _compose_card(outlooks: list[dict], ai_by_asset: dict[str, str | None]) -> str:
    now = bangkok_now()
    date_th = f"วัน{THAI_WEEKDAYS[now.weekday()]}ที่ {now.day} {THAI_MONTHS[now.month - 1]} {now.year + 543}"
    sep = "━━━━━━━━━━━━━━━━━━━━"
    lines = [
        "🌅 <b>DAILY OUTLOOK | สรุปทิศทางเช้า</b>",
        f"<i>{date_th} | เวลาไทย 08:00 น.</i>",
        sep,
    ]
    for i, o in enumerate(outlooks):
        if i:
            lines.append(sep)
        lines.append(_asset_section(o, ai_by_asset.get(o["asset"])))
    lines.append(sep)
    lines.append(
        "⚠️ <i>จุดรับ-จุดต้านคือโซนสภาพคล่องที่ราคามักสะท้อน ไม่ใช่คำรับประกัน - "
        "รอเทียนยืนยันก่อนลงมือเสมอ และคุมความเสี่ยงไม่เกิน 1% ของพอร์ตต่อไม้</i>"
    )
    return "\n".join(lines)


# ============================== กราฟแนบ (อังกฤษล้วนตามข้อจำกัดฟอนต์) ==============================

def _chart_png(o: dict) -> bytes | None:
    """เทียนรายวัน 60 แท่ง + EMA20/50 + เส้นจุดรับ (เขียว) - จุดต้าน (แดง) ธีมมืดอ่านสบายตา"""
    try:
        import io

        import matplotlib

        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
        from matplotlib.patches import Rectangle

        df = o["df_daily"]
        n = min(60, len(df))
        d = df.iloc[-n:].reset_index(drop=True)
        dec = price_decimals(o["asset"])

        fig, ax = plt.subplots(figsize=(10, 5.4), dpi=110, constrained_layout=True)
        fig.patch.set_facecolor("#0d1220")
        ax.set_facecolor("#0d1220")

        for i in range(n):
            op, h, l, c = (float(d[k].iloc[i]) for k in ("open", "high", "low", "close"))
            col = "#26a69a" if c >= op else "#ef5350"
            ax.vlines(i, l, h, color=col, linewidth=0.9, zorder=2)
            ax.add_patch(Rectangle((i - 0.36, min(op, c)), 0.72, max(abs(c - op), 1e-9), color=col, zorder=3))

        ema20 = d["close"].ewm(span=20, adjust=False).mean()
        ema50 = d["close"].ewm(span=50, adjust=False).mean() if len(d) >= 50 else None
        ax.plot(range(n), ema20, color="#4fc3f7", linewidth=1.4, label="EMA20", zorder=4)
        if ema50 is not None:
            ax.plot(range(n), ema50, color="#ffb74d", linewidth=1.4, label="EMA50", zorder=4)

        # ---- เส้นจุดรับ/จุดต้าน/ราคาปัจจุบัน + ป้ายชื่อ (จัดคิวป้ายทั้งหมดรวมกัน กันป้ายทับป้าย) ----
        min_gap = (float(d["high"].max()) - float(d["low"].min())) * 0.045
        # บรรทัดแรกคือป้ายราคาปัจจุบัน (เส้นประจุดขาววาดแยกต่างหากที่ราคาจริง - ค่า None = ไม่วาดเส้น)
        labels = [(None, o["price"], f"NOW {o['price']:,.{dec}f}", "#e8eaed")]
        for tag, lst, color in (("S", o["supports"], "#2ecc71"), ("R", o["resistances"], "#ff6b6b")):
            for i, (p, kind) in enumerate(lst, start=1):
                labels.append((p, p, f"{tag}{i} {p:,.{dec}f}", color))
        labels.sort(key=lambda x: x[1])
        for j in range(1, len(labels)):
            if labels[j][1] - labels[j - 1][1] < min_gap:
                p, y, txt, col = labels[j]
                labels[j] = (p, y + min_gap - (labels[j][1] - labels[j - 1][1]), txt, col)
        for line_p, y, txt, col in labels:
            if line_p is not None:
                ax.axhline(line_p, color=col, linestyle="--", linewidth=1.2, alpha=0.9, zorder=4)
            ax.text(
                n + 0.4, y, f" {txt}", color=col, fontsize=9, va="center", clip_on=False,
                fontweight="bold", zorder=6,
                bbox=dict(facecolor="#0d1220", edgecolor="none", alpha=0.9, pad=1.2),
            )
        ax.axhline(o["price"], color="#e8eaed", linestyle=":", linewidth=1.1, zorder=4)
        ax.set_xlim(-1, n + 9)
        ax.set_title(
            f"{o['name_en']} | Daily Outlook | {bangkok_now().strftime('%d %b %Y')} | Trap Hunter Bot",
            color="#e8eaed", fontsize=12, fontweight="bold",
        )
        ax.tick_params(colors="#9aa0b0", labelsize=8)
        ax.set_xticks([])
        ax.grid(alpha=0.15, color="#3a4258", linewidth=0.5)
        for spine in ax.spines.values():
            spine.set_color("#3a4258")
        ax.yaxis.set_major_formatter(lambda x, _: f"{x:,.{dec}f}")
        ax.legend(loc="upper left", fontsize=8, framealpha=0.15, labelcolor="#e8eaed")

        buf = io.BytesIO()
        fig.savefig(buf, format="png", facecolor=fig.get_facecolor())
        plt.close(fig)
        return buf.getvalue()
    except Exception as e:
        print(f"[outlook] วาดกราฟล้ม ({e.__class__.__name__})", file=sys.stderr)
        return None


# ============================== AI มุมมองเสริม (ถ้ามี key) ==============================

def _ai_comment(ai_cfg: dict, o: dict) -> str | None:
    try:
        from .ai_review import ai_text, get_api_key

        if not get_api_key():
            return None
        t = o["trend"]
        facts = {
            "asset": o["display"],
            "direction": t["direction"],
            "votes_up": t["ups"],
            "votes_down": t["downs"],
            "price": round(o["price"], 4),
            "supports": [round(p, 4) for p, _ in o["supports"]],
            "resistances": [round(p, 4) for p, _ in o["resistances"]],
            "momentum_5d_pct": None,
        }
        prompt = (
            "คุณเป็นนักวิเคราะห์ช่วยเทรดคนไทย อ่านข้อมูลพยากรณ์เช้านี้:\n"
            + str(facts)
            + "\nเขียนมุมมองภาษาไทย 1-2 ประโยค ชี้ว่าวันนี้ควรจับตาอะไรเป็นพิเศษ (เช่น โซนไหนสำคัญ "
            "ถ้าราคาทำอะไรแล้วต้องระวัง) อ่านง่าย ตรงประเด็น ห้ามคำแนะนำซื้อขายตรง ๆ ไม่ต้องทักทาย"
        )
        return ai_text(ai_cfg or {}, prompt, max_tokens=200)
    except Exception as e:
        print(f"[outlook] AI คอมเมนต์ล้ม ({e.__class__.__name__})", file=sys.stderr)
        return None


# ============================== ทางเข้าหลัก ==============================

def maybe_send_outlook(cfg: dict, state: dict, tg_cfg: dict, ctx: dict, send_fn, send_photo_fn) -> bool:
    """ถ้าอยู่ช่วงเช้า (hour ตามเวลาไทย) และยังไม่ส่งวันนี้ -> วิเคราะห์ + ยิงการ์ด + กราฟ แล้วคืน True"""
    if not cfg.get("enabled"):
        return False
    try:
        now = bangkok_now()
        hour = int(cfg.get("hour", 8))
        window = int(cfg.get("window_minutes", 15))
        today = now.strftime("%Y-%m-%d")
        if now.hour != hour or now.minute >= window or state.get("outlook_date") == today:
            return False

        assets = list(cfg.get("assets") or ["BTCUSDT", "XAUUSD"])
        assets_cfg = cfg.get("_assets_cfg") or {}  # main.py ส่ง config สินทรัพย์มาให้ (round_step ฯลฯ)
        dxy = cfg.get("dxy_trend")
        if dxy is None:
            try:
                from .data import dxy_trend, fetch_dxy_daily

                dxy = dxy_trend(fetch_dxy_daily())
            except Exception:
                dxy = None

        outlooks = []
        for asset in assets:
            o = analyze_asset(asset, assets_cfg.get(asset) or {"round_step": 1}, dxy)
            if o:
                outlooks.append(o)
        if not outlooks:
            print("[outlook] ดึงข้อมูลไม่สำเร็จทั้งหมด - จะลองใหม่รอบสแกนถัดไป", file=sys.stderr)
            return False

        ai_by_asset: dict[str, str | None] = {}
        if cfg.get("ai_comment", True):
            for o in outlooks:
                ai_by_asset[o["asset"]] = _ai_comment(cfg.get("ai_cfg") or {}, o)

        card = _compose_card(outlooks, ai_by_asset)
        send_fn(tg_cfg, card)

        if cfg.get("send_chart", True) and send_photo_fn:
            for o in outlooks:
                png = _chart_png(o)
                if png:
                    try:
                        caption = f"📈 {o['display']} | เส้นเขียว = จุดรับ | เส้นแดง = จุดต้าน | กราฟรายวัน 60 แท่ง + EMA20/50"
                        send_photo_fn(tg_cfg, png, caption=caption)
                    except Exception as e:
                        print(f"[outlook] ส่งรูปล้ม ({e.__class__.__name__})", file=sys.stderr)

        state["outlook_date"] = today
        print(f"[outlook] ส่งพยากรณ์ทิศทางเช้าแล้ว ({', '.join(o['asset'] for o in outlooks)})")
        return True
    except Exception as e:
        print(f"[outlook] พยากรณ์เช้าล้ม ({e.__class__.__name__}) - ไม่กระทบรอบสแกนหลัก", file=sys.stderr)
        return False
