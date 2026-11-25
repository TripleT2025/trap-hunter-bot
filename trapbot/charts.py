# -*- coding: utf-8 -*-
"""ชั้นวาดกราฟแนบการ์ดสัญญาณ (Chart Renderer)

ปรัชญา: การ์ดตัวเลขอ่านช้า กราฟเห็นจังหวะใน 3 วินาที - เลยวาด "ภาพจังหวะเดียวกับที่ระบบเห็น"
ให้ไปแปะบนการ์ดสัญญาณด้วย sendPhoto

ในภาพจะมี:
  - เทียน 80 แท่งล่าสุดของไทม์เฟรมหลัก (สีเขียวขึ้น/แดงลง)
  - แผนที่กับดัก: เส้นประเทาของระดับสำคัญใกล้ราคา (EQH/PDH/เลขสวย ฯลฯ)
  - โซน Entry (แถบฟ้า) SL (แดง) TP1-TP3 (เขียวเข้มไปอ่อน)
  - จุดกับดัก: วงกลมที่ตำแหน่ง sweep + reclaim ของไม้นี้

หมายเหตุ: ตัวหนังสือในภาพเป็นอังกฤษเท่านั้น เพราะเซิร์ฟเวอร์ GitHub ไม่มีฟอนต์ไทย
ความหมายระดับต่าง ๆ อ่านต่อได้จากการ์ดข้อความภาษาไทยที่ส่งมาพร้อมภาพ
"""
import io
import sys

COLOR_UP = "#26a69a"
COLOR_DOWN = "#ef5350"


def signal_chart_png(sig, df, levels) -> bytes | None:
    """วาดกราฟจังหวะสัญญาณ - คืน PNG bytes หรือ None ถ้าพลาด (ไม่กระทบการส่งการ์ด)"""
    try:
        import matplotlib

        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
        from matplotlib.patches import Circle, Rectangle

        n = min(80, len(df))
        d = df.iloc[-n:].reset_index(drop=True)
        x = list(range(n))

        fig, ax = plt.subplots(figsize=(11, 6), dpi=110, constrained_layout=True)

        # ---- เทียน ----
        for i in range(n):
            o, h, l, c = (float(d[k].iloc[i]) for k in ("open", "high", "low", "close"))
            col = COLOR_UP if c >= o else COLOR_DOWN
            ax.vlines(i, l, h, color=col, linewidth=0.9, zorder=2)
            body_low, body_h = min(o, c), max(abs(c - o), 1e-9)
            ax.add_patch(Rectangle((i - 0.36, body_low), 0.72, body_h, color=col, zorder=3))

        # ---- โซนเข้า / SL / TP ----
        side = 1 if sig.side == "BUY" else -1
        start = n - 6
        ax.axhspan(sig.entry_low, sig.entry_high, xmin=0, color="#3b82f6", alpha=0.18, zorder=1)
        ax.hlines(sig.entry, start - 1, n - 1, color="#3b82f6", linewidth=1.6, linestyles="-", label="Entry")
        ax.hlines(sig.sl, start - 1, n - 1, color=COLOR_DOWN, linewidth=1.6, linestyles="-", label="SL")
        ax.hlines(sig.tp1, start - 1, n - 1, color="#16a34a", linewidth=1.2, linestyles="--", label="TP1")
        ax.hlines(sig.tp2, start - 1, n - 1, color="#15803d", linewidth=1.2, linestyles="--", label="TP2")
        ax.hlines(sig.tp3, start - 1, n - 1, color="#166534", linewidth=1.2, linestyles="--", label="TP3")

        # ---- แผนที่กับดักใกล้ราคา (ห่างไม่เกิน 3R) ----
        seen = set()
        for lv in levels or []:
            if abs(lv.price - sig.entry) > 3 * max(sig.r, 1e-9):
                continue
            key = round(lv.price, 6)
            if key in seen:
                continue
            seen.add(key)
            ax.hlines(lv.price, 0, n - 1, color="#94a3b8", linewidth=0.8, linestyles=":")
            ax.text(0.5, lv.price, f" {lv.kind}", fontsize=7, color="#64748b", va="center")

        # ---- จุดเกิดเหตุ: sweep + reclaim ของไม้นี้ ----
        try:
            sweep_ts, reclaim_ts = sig.extras.get("sweep_ts"), sig.extras.get("reclaim_ts")
            if sweep_ts is not None:
                sidx = _index_of(d, sweep_ts)
                if sidx is not None:
                    extreme = float(d["low"].iloc[sidx]) if side == 1 else float(d["high"].iloc[sidx])
                    ax.add_patch(Circle((sidx, extreme), 1.4, color="#f59e0b", fill=False, linewidth=1.8, zorder=4))
                    ax.text(sidx, extreme, "  TRAP", fontsize=8, color="#b45309", va="center")
            if reclaim_ts is not None:
                ridx = _index_of(d, reclaim_ts)
                if ridx is not None:
                    ax.add_patch(Circle((ridx, float(d["close"].iloc[ridx])), 1.4, color="#8b5cf6", fill=False, linewidth=1.8, zorder=4))
                    ax.text(ridx, float(d["close"].iloc[ridx]), "  CONFIRM", fontsize=8, color="#6d28d9", va="center")
        except Exception:
            pass  # ขาดจุดประกอบเรื่องก็ยังมีกราฟให้ดู

        ax.set_xlim(-1, n + 1)
        # หัวกราฟใช้สัญลักษณ์อังกฤษล้วน (เซิร์ฟเวอร์ GitHub ไม่มีฟอนต์ไทย - ความหมายไทยอยู่บนการ์ดข้อความ)
        ax.set_title(f"TrapHunter | {sig.asset} | {sig.side} @ {sig.entry:g}", fontsize=13, fontweight="bold")
        ax.grid(alpha=0.25, linewidth=0.5)
        ax.legend(loc="upper left", fontsize=8, ncol=5, framealpha=0.85)
        ax.tick_params(labelsize=8)
        _hide_index_axis(ax)

        buf = io.BytesIO()
        fig.savefig(buf, format="png")
        plt.close(fig)
        return buf.getvalue()
    except Exception as e:
        print(f"[charts] วาดกราฟสัญญาณไม่สำเร็จ ({e.__class__.__name__})", file=sys.stderr)
        return None


def _index_of(d, ts) -> int | None:
    try:
        target = ts
        for i, t in enumerate(d["ts"]):
            if t == target:
                return i
        return None
    except Exception:
        return None


def _hide_index_axis(ax) -> None:
    """ซ่อนแกน x (ไม่จำเป็นต้องรู้เวลาแบบเป๊ะ - การ์ดข้อความมีเวลาแล้ว)"""
    import matplotlib.pyplot as plt

    ax.set_xticks([])
