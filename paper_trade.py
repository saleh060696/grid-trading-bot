"""
Paper Trading — banyak strategi serentak atas harga LIVE Luno
==============================================================

Semua strategi kongsi SATU suapan harga. Itu penting: mereka dinilai atas
bar yang sama, pada masa yang sama. Tiada seorang pun dapat data lebih
baik, jadi perbezaan keputusan datang dari strategi sahaja.

Ciri penting:
- Guna endpoint ticker AWAM Luno. TIADA API key perlu.
- Tak pernah execute. Tiada duit bergerak. Tiada risiko.
- Satu fail state, tulisan atomik — selamat dari restart dan kill.
- Setiap tick, seluruh sejarah dimainkan semula untuk setiap strategi.
  Menghapuskan kelas pepijat state yang rosak separuh jalan.
- Buy & hold sentiasa disertakan sebagai penanda aras, walaupun tak dipilih.

Cara guna:
    .venv/bin/python paper_trade.py init --strategies all --interval 1h
    .venv/bin/python paper_trade.py tick
    .venv/bin/python paper_trade.py status

Jadualkan (setiap 10 minit) dengan crontab -e:
    */10 * * * * cd ~/grid-trading-bot && .venv/bin/python paper_trade.py tick
"""

import argparse
import csv
import json
import os
import sys
import urllib.request
from datetime import datetime, timezone

from research import (BollingerRevert, Broker, BuyHold, BuyTheDip, Donchian,
                      Grid, MACD, MACross, RSIRevert, Rebalance)

STATE = "paper_state.json"
LOG = "paper_decisions.csv"
TICKER = "https://api.luno.com/api/1/ticker?pair=XBTMYR"

INTERVAL_SEC = {"5m": 300, "15m": 900, "1h": 3600, "4h": 14400, "1d": 86400}

STRATEGIES = {
    "buyhold": lambda: BuyHold(),
    "rebalance": lambda: Rebalance(band=0.10),
    "grid": lambda: Grid(num_grids=5),
    "grid_stop": lambda: Grid(num_grids=5, stop_loss=0.02),
    "donchian": lambda: Donchian(),
    "macross": lambda: MACross(10, 30),
    "macd": lambda: MACD(),
    "rsi": lambda: RSIRevert(),
    "bollinger": lambda: BollingerRevert(),
    "dip": lambda: BuyTheDip(),
}


def fetch_price():
    req = urllib.request.Request(TICKER, headers={"User-Agent": "paper/1.0"})
    with urllib.request.urlopen(req, timeout=20) as r:
        d = json.load(r)
    bid, ask = float(d["bid"]), float(d["ask"])
    return {"last": float(d["last_trade"]), "bid": bid, "ask": ask,
            "spread_pct": (ask - bid) / bid * 100 if bid else 0.0}


def load_state():
    if not os.path.exists(STATE) or os.path.getsize(STATE) == 0:
        sys.exit(f"'{STATE}' tak jumpa atau kosong. Run dulu: paper_trade.py init")
    with open(STATE) as fh:
        st = json.load(fh)
    if "strategies" not in st:
        sys.exit(f"'{STATE}' format lama (satu strategi). "
                 "Run 'init --force' untuk mula sesi berbilang strategi.")
    return st


def save_state(st):
    tmp = STATE + ".tmp"
    with open(tmp, "w") as fh:
        json.dump(st, fh, indent=2)
    os.replace(tmp, STATE)          # atomik


def bar_start(ts, secs):
    return int(ts // secs * secs)


def replay_one(closes, name, st):
    """Main semula sejarah untuk satu strategi. Pulang broker."""
    broker = Broker(st["capital"], st["fee"], st["min_order_btc"])
    warmup = st["warmup"]
    if len(closes) <= warmup:
        return broker, False
    strat = STRATEGIES[name]()
    strat.setup(closes[:warmup])
    traded = closes[warmup - 1:]
    for i in range(1, len(traded)):
        strat.on_bar(i, traded[i], traded[i - 1], broker)
    return broker, True


def evaluate(st, price):
    """Nilai semua strategi pada harga semasa. Pulang senarai baris."""
    closes = [b[1] for b in st["bars"]]
    rows = []
    for name in st["strategies"]:
        broker, active = replay_one(closes, name, st)
        rows.append({
            "name": name,
            "active": active,
            "equity": broker.equity(price),
            "cash": broker.cash,
            "btc": broker.btc,
            "buys": broker.buys,
            "sells": broker.sells,
            "rejected": broker.rejected,
            "fees": broker.fees_paid,
            "actions": broker.buys + broker.sells,
        })
    return rows


def log_rows(ts, price, rows, bh_equity):
    exists = os.path.exists(LOG)
    with open(LOG, "a", newline="") as fh:
        w = csv.writer(fh)
        if not exists:
            w.writerow(["masa", "harga", "strategi", "tindakan", "btc",
                        "tunai", "ekuiti", "vs_buyhold", "trade", "tolak"])
        for r in rows:
            w.writerow([ts, round(price, 2), r["name"], r["flag"],
                        round(r["btc"], 8), round(r["cash"], 2),
                        round(r["equity"], 2),
                        round(r["equity"] - bh_equity, 2),
                        r["actions"], r["rejected"]])


def cmd_init(args):
    # Fail KOSONG bukan sesi sah. Ia boleh timbul dari redirect shell yang
    # gagal atau tulisan terputus — jangan halang init kerananya.
    if os.path.exists(STATE) and os.path.getsize(STATE) > 0 and not args.force:
        sys.exit(f"'{STATE}' dah wujud. Guna --force untuk mula semula "
                 "(sejarah sedia ada akan HILANG).")
    if args.interval not in INTERVAL_SEC:
        sys.exit(f"Interval tak sah. Pilihan: {', '.join(INTERVAL_SEC)}")

    if args.strategies.strip().lower() == "all":
        chosen = list(STRATEGIES)
    else:
        chosen = [s.strip() for s in args.strategies.split(",") if s.strip()]
        bad = [s for s in chosen if s not in STRATEGIES]
        if bad:
            sys.exit(f"Strategi tak dikenali: {', '.join(bad)}\n"
                     f"Pilihan: {', '.join(STRATEGIES)}")
    if not chosen:
        sys.exit("Perlu sekurang-kurangnya satu strategi.")
    if "buyhold" not in chosen:
        chosen.insert(0, "buyhold")   # penanda aras sentiasa ada

    st = {
        "interval": args.interval,
        "capital": args.capital,
        "fee": args.fee,
        "min_order_btc": args.min_order,
        "warmup": args.warmup,
        "started": datetime.now(timezone.utc).isoformat(),
        "bars": [],
        "first_price": None,
        "strategies": chosen,
        "last_actions": {s: 0 for s in chosen},
    }
    save_state(st)
    hrs = args.warmup * INTERVAL_SEC[args.interval] / 3600
    print("Paper trading dimulakan — berbilang strategi, satu suapan harga.")
    print(f"  strategi : {len(chosen)} -> {', '.join(chosen)}")
    print(f"  interval : {args.interval}")
    print(f"  modal    : RM{args.capital:,.2f} setiap satu")
    print(f"  fee      : {args.fee * 100:.2f}%")
    print(f"  warmup   : {args.warmup} bar (~{hrs:.0f} jam / {hrs/24:.1f} hari)")
    print("\nSeterusnya: jadualkan 'paper_trade.py tick'.")


def cmd_tick(args):
    st = load_state()
    secs = INTERVAL_SEC[st["interval"]]
    now = datetime.now(timezone.utc)
    px = fetch_price()
    start = bar_start(now.timestamp(), secs)

    bars = st["bars"]
    if bars and bars[-1][0] == start:
        bars[-1][1] = px["last"]
        new_bar = False
    else:
        bars.append([start, px["last"]])
        new_bar = True
    if st["first_price"] is None:
        st["first_price"] = px["last"]

    rows = evaluate(st, px["last"])
    # Penanda aras = baris strategi buyhold itu sendiri, supaya angka
    # 'vs_buyhold' dalam log sepadan tepat dengan papan pendahulu status.
    bh_row = next((r for r in rows if r["name"] == "buyhold"), None)
    bh_equity = bh_row["equity"] if bh_row else st["capital"]

    moved = []
    for r in rows:
        changed = r["actions"] != st["last_actions"].get(r["name"], 0)
        r["flag"] = "TINDAKAN" if changed else "-"
        if changed:
            moved.append(r["name"])
            st["last_actions"][r["name"]] = r["actions"]

    save_state(st)

    warm_left = max(0, st["warmup"] + 1 - len(bars))
    tag = (f"warmup ({warm_left} bar lagi)" if warm_left
           else (f"TINDAKAN: {', '.join(moved)}" if moved else "tiada perubahan"))
    print(f"{now.strftime('%Y-%m-%d %H:%M')} UTC  RM{px['last']:,.0f}  "
          f"spread {px['spread_pct']:.4f}%  [{tag}]")

    if rows and rows[0]["active"] and (moved or new_bar):
        log_rows(now.isoformat(), px["last"], rows, bh_equity)


def cmd_status(args):
    st = load_state()
    bars = st["bars"]
    if not bars:
        print("Belum ada data. Run 'tick' dahulu.")
        return
    px = fetch_price()
    rows = evaluate(st, px["last"])
    started = datetime.fromisoformat(st["started"])
    age_h = (datetime.now(timezone.utc) - started).total_seconds() / 3600
    cap = st["capital"]

    print(f"Interval    : {st['interval']}   |   modal RM{cap:,.2f} setiap strategi")
    print(f"Bermula     : {started.strftime('%Y-%m-%d %H:%M')} UTC "
          f"({age_h:.1f} jam / {age_h/24:.1f} hari)")
    print(f"Bar dikumpul: {len(bars)}  (warmup {st['warmup']})")
    print(f"Harga kini  : RM{px['last']:,.2f}  (spread {px['spread_pct']:.4f}%)")
    print()

    if not rows[0]["active"]:
        left = st["warmup"] + 1 - len(bars)
        secs = INTERVAL_SEC[st["interval"]]
        print(f"Masih warmup — {left} bar lagi (~{left * secs / 3600:.1f} jam) "
              "sebelum strategi mula buat keputusan.")
        return

    bh = next((r for r in rows if r["name"] == "buyhold"), None)
    bh_eq = bh["equity"] if bh else cap

    rows.sort(key=lambda r: r["equity"], reverse=True)
    print(f"{'#':>2} {'strategi':<12} {'ekuiti':>10} {'pulangan':>10} "
          f"{'vs B&H':>9} {'trade':>6} {'tolak':>6} {'fee':>7}")
    print("-" * 70)
    for i, r in enumerate(rows, 1):
        mark = " *" if r["name"] == "buyhold" else ""
        print(f"{i:>2} {r['name'] + mark:<12} {r['equity']:>10.2f} "
              f"{(r['equity']/cap - 1) * 100:>9.2f}% "
              f"{r['equity'] - bh_eq:>+9.2f} {r['actions']:>6} "
              f"{r['rejected']:>6} {r['fees']:>7.2f}")
    print("-" * 70)
    print("  * penanda aras")

    if age_h < 24 * 90:
        print(f"\nNota: {age_h/24:.1f} hari data. Dengan interval {st['interval']}, "
              f"satu sampel bebas ~30 hari.")
        print("Kedudukan pada peringkat ni hampir semuanya nasib. Sasarkan "
              "beberapa bulan sebelum baca apa-apa makna.")


def main():
    ap = argparse.ArgumentParser(
        description="Paper trading berbilang strategi atas harga live Luno")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("init", help="mulakan sesi paper trading baru")
    p.add_argument("--strategies", default="all",
                   help=f"senarai dipisah koma, atau 'all'. "
                        f"Pilihan: {', '.join(STRATEGIES)}")
    p.add_argument("--interval", default="1h",
                   help=f"pilihan: {', '.join(INTERVAL_SEC)}")
    p.add_argument("--capital", type=float, default=350.0)
    p.add_argument("--fee", type=float, default=0.0035)
    p.add_argument("--min-order", type=float, default=0.0001)
    p.add_argument("--warmup", type=int, default=30)
    p.add_argument("--force", action="store_true")
    p.set_defaults(func=cmd_init)

    p = sub.add_parser("tick", help="rekod harga & nilai semua strategi")
    p.set_defaults(func=cmd_tick)

    p = sub.add_parser("status", help="papan pendahulu semasa")
    p.set_defaults(func=cmd_status)

    args = ap.parse_args()
    args.func(args)


if __name__ == "__main__":
    try:
        main()
    except (OSError, ValueError) as exc:
        sys.exit(f"Ralat: {exc}")
    except KeyboardInterrupt:
        sys.exit("\nDibatalkan.")
