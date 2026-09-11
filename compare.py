"""
Banding semua strategi, dengan walk-forward dan ujian sampel bebas.

    .venv/bin/python compare.py            # harian, tetingkap 120 hari
    .venv/bin/python compare.py 4h 180     # 4-jam, tetingkap 180 bar
    .venv/bin/python compare.py 1h 720     # sejam, tetingkap 720 bar (30 hari)
"""

import csv
import statistics
import sys

from research import (BollingerRevert, BuyHold, BuyTheDip, DCA, Donchian, Grid,
                      MACD, MACross, RSIRevert, Rebalance, run,
                      independent_windows, walk_forward, report_walk_forward)

CASH, FEE, MIN_ORDER, WARMUP = 350.0, 0.0035, 0.0001, 20

BARS_PER_YEAR = {"1d": 365, "4h": 365 * 6, "1h": 365 * 24}

BUILDERS = [
    ("buy & hold", lambda: BuyHold()),
    ("DCA", lambda: DCA(every_days=7, slices=8)),
    ("grid 5", lambda: Grid(num_grids=5)),
    ("grid 5 + stop 2%", lambda: Grid(num_grids=5, stop_loss=0.02)),
    ("MA cross 10/30", lambda: MACross(10, 30)),
    ("MACD 12/26/9", lambda: MACD()),
    ("RSI revert 14", lambda: RSIRevert()),
    ("Bollinger 20/2", lambda: BollingerRevert()),
    ("Donchian 20/10", lambda: Donchian()),
    ("buy the dip 10%", lambda: BuyTheDip()),
    ("rebalance 50/50", lambda: Rebalance(band=0.10)),
]


def load(tf):
    path = f"btc_{tf}.csv"
    try:
        with open(path) as fh:
            return [float(r["close"]) for r in csv.DictReader(fh)]
    except FileNotFoundError:
        sys.exit(f"'{path}' tak jumpa. Run dulu: python3 fetch_data.py {tf}")


def table(title, wins, bpy):
    print(f"\n{'=' * 90}")
    print(f"{title}   ({len(wins)} tetingkap)")
    print(f"{'=' * 90}")
    print(f"{'strategi':<20} {'mean':>9} {'median':>9} {'untung':>8} {'max dd':>8} "
          f"{'sharpe':>7} {'trade':>6} {'tolak':>6}")
    print("-" * 90)
    for label, mk in BUILDERS:
        rs = [r for r in (run(w, mk(), CASH, FEE, MIN_ORDER, WARMUP, bpy)
                          for w in wins) if r]
        if not rs:
            continue
        nets = [r.net for r in rs]
        rej = statistics.mean(r.rejected for r in rs)
        trd = statistics.mean(r.trades for r in rs)
        flag = "  <- byk DITOLAK" if rej > trd else ""
        print(f"{label:<20} {statistics.mean(nets):>9.2f} "
              f"{statistics.median(nets):>9.2f} "
              f"{100*sum(1 for x in nets if x>0)/len(nets):>7.1f}% "
              f"{statistics.mean(r.max_dd_pct for r in rs):>7.1f}% "
              f"{statistics.mean(r.sharpe for r in rs):>7.2f} "
              f"{trd:>6.1f} {rej:>6.1f}{flag}")


def main():
    tf = sys.argv[1] if len(sys.argv) > 1 else "1d"
    size = int(sys.argv[2]) if len(sys.argv) > 2 else 120
    bpy = BARS_PER_YEAR.get(tf, 365)

    prices = load(tf)
    print(f"Timeframe {tf} | {len(prices):,} bar | tetingkap {size} bar")
    print(f"Modal RM{CASH:.0f} | fee {FEE*100:.2f}% | min order {MIN_ORDER} BTC")

    indep = independent_windows(prices, size)
    table(f"SAMPEL BEBAS (tiada pertindihan) — {tf}", indep, bpy)
    print(f"\n  {len(indep)} sampel bebas. Di bawah ~30, satu tempoh pasaran "
          "tunggal boleh kuasai keputusan.")

    report_walk_forward(
        walk_forward(prices, lambda **p: Grid(**p),
                     [{"num_grids": n, "stop_loss": s}
                      for n in (2, 3, 5, 8, 10) for s in (None, 0.02, 0.05)],
                     train_days=size * 2, test_days=size, step=size,
                     cash=CASH, fee=FEE, min_order=MIN_ORDER, warmup=WARMUP,
                     bars_per_year=bpy),
        f"grid ({tf})")

    report_walk_forward(
        walk_forward(prices, lambda **p: Donchian(**p),
                     [{"entry": e, "exit": x}
                      for e in (10, 20, 55) for x in (5, 10, 20) if x < e],
                     train_days=size * 2, test_days=size, step=size,
                     cash=CASH, fee=FEE, min_order=MIN_ORDER, warmup=WARMUP,
                     bars_per_year=bpy),
        f"Donchian ({tf})")

    report_walk_forward(
        walk_forward(prices, lambda **p: MACross(**p),
                     [{"fast": f, "slow": s}
                      for f in (5, 10, 20) for s in (30, 50, 100) if f < s],
                     train_days=size * 2, test_days=size, step=size,
                     cash=CASH, fee=FEE, min_order=MIN_ORDER, warmup=WARMUP,
                     bars_per_year=bpy),
        f"MA cross ({tf})")


if __name__ == "__main__":
    main()
