# -*- coding: utf-8 -*-
"""พอร์ตจำลอง (Paper Trading Ledger)

ไอเดีย: "ถ้าใช้เงินจริงตามระบบนี้จะเป็นยังไง" - ตอบด้วยบัญชีกระดาษที่จำลองตามสัญญาณจริงทุกไม้

  - เริ่มพอร์ต 10,000$ (แก้ได้ใน config)
  - ทุกสัญญาณที่ยิงออกจริง (ผ่าน AI แล้ว) เปิดไม้จำลองด้วยความเสี่ยง 1% ของพอร์ต
  - เดิน SL/TP ด้วยกฎเดียวกับสมุดจดผล (TP1 30% ย้ายคุ้มทุน -> TP2 40% -> TP3 30%)
  - เมื่อไม้ปิด equity จริงเปลี่ยนตาม R สุทธิ: profit_usd = ทุนเสี่ยง(1%) x R
  - เก็บ equity curve เพื่อวาดกราฟส่งรายสัปดาห์

หมายเหตุตรงไปตรงมา: นี่คือจำลอง - ไม่คิดค่าธรรมเนียม ไม่คิด slippage ไม่คิดสเปรด
เพื่อวัด "คุณภาพของระบบสัญญาณ" โดยเฉพาะ ไม่ใช่คำรับประกันกำไรจริง
"""
import sys


def ensure(state: dict, cfg: dict) -> dict:
    """มีพอร์ตอยู่แล้วหรือยัง - ยังไม่มีสร้างใหม่ตาม config"""
    paper = state.get("paper")
    if paper and paper.get("start"):
        return paper
    start = float(cfg.get("start_equity", 10000.0))
    paper = {
        "start": start,
        "equity": start,
        "risk_pct": float(cfg.get("risk_pct", 1.0)),
        "positions": {},
        "equity_curve": [[_now_iso(), start]],
        "closed": [],
    }
    state["paper"] = paper
    return paper


def _now_iso() -> str:
    from datetime import datetime, timezone

    return datetime.now(timezone.utc).isoformat()


def open_position(paper: dict, sig, grade: str) -> bool:
    """เปิดไม้จำลอง (สินทรัพย์ละ 1 ไม้เท่านั้น - ถ้ามีไม้ค้างของตัวเองอยู่ข้าม)"""
    asset = sig.asset
    if asset in paper["positions"]:
        return False
    risk_usd = paper["equity"] * paper["risk_pct"] / 100.0
    paper["positions"][asset] = {
        "ts": _now_iso(),
        "side": sig.side,
        "entry": sig.entry,
        "sl": sig.sl,
        "tp1": sig.tp1,
        "tp2": sig.tp2,
        "tp3": sig.tp3,
        "cur_sl": sig.sl,
        "realized_r": 0.0,
        "outcome": "OPEN",
        "risk_usd": round(risk_usd, 2),
        "grade": grade,
        "display": sig.display_name,
    }
    return True


def update(paper: dict, bars: dict[str, tuple[float, float]]) -> list[dict]:
    """เดินไม้ที่เปิดอยู่ทุกตัวด้วยไฮ-โลว์ล่าสุด - คืนรายการไม้ที่เพิ่งปิด"""
    from .journal import ladder_step

    closed = []
    for asset, pos in list(paper["positions"].items()):
        bar = bars.get(asset)
        if not bar:
            continue
        before = pos["realized_r"]
        if ladder_step(pos, bar[0], bar[1]):
            r_delta = pos["realized_r"] - before
            pnl = pos["risk_usd"] * pos["realized_r"]
            # R ที่เก็บระหว่างทางถูกนับครั้งเดียวตอนปิดไม้ (equity เปลี่ยนตอนปิดเท่านั้น)
            paper["equity"] = round(paper["equity"] + pnl, 2)
            rec = {
                "closed_ts": _now_iso(),
                "asset": asset,
                "display": pos["display"],
                "side": pos["side"],
                "grade": pos["grade"],
                "outcome": pos["outcome"],
                "r": pos["realized_r"],
                "pnl_usd": round(pnl, 2),
                "equity": paper["equity"],
            }
            paper["closed"].append(rec)
            if len(paper["closed"]) > 60:
                paper["closed"] = paper["closed"][-60:]
            paper["equity_curve"].append([_now_iso(), paper["equity"]])
            if len(paper["equity_curve"]) > 400:
                paper["equity_curve"] = paper["equity_curve"][-400:]
            del paper["positions"][asset]
            closed.append(rec)
    return closed


def status_line(paper: dict) -> str:
    """บรรทัดสถานะพอร์ตสำหรับ /status และสรุปเช้า"""
    pnl = paper["equity"] - paper["start"]
    pct = pnl / paper["start"] * 100
    open_n = len(paper["positions"])
    return f"พอร์ตจำลอง: {paper['equity']:,.2f}$ ({pnl:+,.2f}$ = {pct:+.2f}%) | ไม้เปิดค้าง {open_n} ไม้"


def weekly_closed(paper: dict) -> list[dict]:
    from datetime import datetime, timedelta, timezone

    cutoff = (datetime.now(timezone.utc) - timedelta(days=7)).isoformat()
    return [r for r in paper.get("closed", []) if r["closed_ts"] >= cutoff]


def equity_chart_png(paper: dict) -> bytes | None:
    """กราฟเส้น equity สำหรับรายงานสัปดาห์ - คืน PNG bytes หรือ None"""
    try:
        import io

        import matplotlib

        matplotlib.use("Agg")
        import matplotlib.dates as mdates
        import matplotlib.pyplot as plt
        from datetime import datetime

        curve = paper.get("equity_curve", [])
        if len(curve) < 2:
            return None
        xs = [datetime.fromisoformat(p[0]) for p in curve]
        ys = [p[1] for p in curve]
        fig, ax = plt.subplots(figsize=(9, 4.5), dpi=110, constrained_layout=True)
        ax.plot(xs, ys, color="#0f766e", linewidth=2.2)
        ax.axhline(paper["start"], color="#9ca3af", linestyle="--", linewidth=1)
        ax.fill_between(xs, paper["start"], ys, color="#0f766e", alpha=0.12)
        ax.set_title("Trap Hunter - Paper Trading Equity Curve (7 assets)", fontsize=13)
        ax.set_ylabel("Equity (USD)")
        ax.grid(alpha=0.3)
        ax.xaxis.set_major_formatter(mdates.DateFormatter("%d/%m"))
        buf = io.BytesIO()
        fig.savefig(buf, format="png")
        plt.close(fig)
        return buf.getvalue()
    except Exception as e:
        print(f"[paper] วาดกราฟไม่สำเร็จ ({e.__class__.__name__})", file=sys.stderr)
        return None
