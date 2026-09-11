"""
Perekam Orderbook — kumpul bukti sama ada limit order kita akan terisi
=======================================================================

Setiap kali dipanggil, rekod:
- Snapshot orderbook (kedalaman pada beberapa jarak dari mid)
- Semua dagangan sejak panggilan terakhir

Daripada data ni, analyse.py kira soalan yang backtest tak boleh jawab:
kalau kita letak limit order pada aras grid, berapa kerap ia betul-betul
terisi — bukan sekadar disentuh harga?

Guna data AWAM Luno. Tiada API key. Tiada duit.

    .venv/bin/python record_book.py            # rekod satu snapshot
    .venv/bin/python record_book.py --analyse  # ringkasan apa yang dikumpul
"""

import argparse
import json
import os
import statistics
import sys
import time

from orderbook import fetch_book, fetch_trades, queue_ahead

DATA = "book_log.jsonl"
STATE = "book_state.json"

# Jarak dari mid yang kita jejak (grid tipikal kita ~1% jarak antara garis)
OFFSETS = [0.0005, 0.001, 0.002, 0.005, 0.01, 0.02]


def record():
    book = fetch_book()
    mid = (book["bids"][0][0] + book["asks"][0][0]) / 2

    last_ts = None
    if os.path.exists(STATE):
        try:
            with open(STATE) as fh:
                last_ts = json.load(fh).get("last_trade_ts")
        except (ValueError, OSError):
            last_ts = None

    since = last_ts if last_ts else int(time.time() * 1000) - 900000
    trades = fetch_trades(since_ms=since)
    trades = [t for t in trades if last_ts is None or t["ts"] > last_ts]

    row = {
        "ts": book["ts"],
        "mid": mid,
        "spread_pct": (book["asks"][0][0] - book["bids"][0][0]) / mid * 100,
        "depth": {},
        "traded": {},
        "n_trades": len(trades),
        "volume": round(sum(t["volume"] for t in trades), 8),
    }
    for off in OFFSETS:
        bp = round(mid * (1 - off))
        ap = round(mid * (1 + off))
        row["depth"][str(off)] = {
            "buy_queue": round(queue_ahead(book, "buy", float(bp)), 8),
            "sell_queue": round(queue_ahead(book, "sell", float(ap)), 8),
        }
        row["traded"][str(off)] = {
            "at_or_below": round(sum(t["volume"] for t in trades
                                     if not t["is_buy"] and t["price"] <= bp), 8),
            "at_or_above": round(sum(t["volume"] for t in trades
                                     if t["is_buy"] and t["price"] >= ap), 8),
        }

    with open(DATA, "a") as fh:
        fh.write(json.dumps(row) + "\n")

    if trades:
        tmp = STATE + ".tmp"
        with open(tmp, "w") as fh:
            json.dump({"last_trade_ts": trades[-1]["ts"]}, fh)
        os.replace(tmp, STATE)

    print(f"RM{mid:,.0f}  spread {row['spread_pct']:.4f}%  "
          f"{len(trades)} dagangan baru ({row['volume']:.4f} BTC)")


def analyse():
    if not os.path.exists(DATA):
        sys.exit(f"'{DATA}' tak jumpa. Run 'record_book.py' dahulu.")
    rows = []
    with open(DATA) as fh:
        for line in fh:
            line = line.strip()
            if line:
                try:
                    rows.append(json.loads(line))
                except ValueError:
                    continue
    if not rows:
        sys.exit("Tiada data sah dalam log.")

    span_h = (rows[-1]["ts"] - rows[0]["ts"]) / 3600000
    total_vol = sum(r["volume"] for r in rows)
    print(f"Snapshot   : {len(rows)}")
    print(f"Tempoh     : {span_h:.1f} jam")
    print(f"Volum      : {total_vol:.4f} BTC "
          f"({total_vol/span_h:.3f} BTC/jam)" if span_h > 0 else "")
    print(f"Spread     : median {statistics.median(r['spread_pct'] for r in rows):.4f}%")
    print()
    print("Adakah limit order kita akan TERISI pada setiap jarak?")
    print("(volum kumulatif didagang di aras itu vs median saiz barisan)")
    print()
    print(f"{'jarak':>8} {'barisan(med)':>13} {'didagang':>11} {'nisbah':>8} {'terisi?':>9}")
    print("-" * 56)
    for off in OFFSETS:
        k = str(off)
        q = statistics.median(r["depth"][k]["buy_queue"] for r in rows)
        v = sum(r["traded"][k]["at_or_below"] for r in rows)
        ratio = v / q if q > 0 else 0
        verdict = "YA" if ratio >= 1 else ("hampir" if ratio >= 0.5 else "tidak")
        print(f"{off*100:>7.2f}% {q:>13.4f} {v:>11.4f} {ratio:>7.2f}x {verdict:>9}")
    print()
    print("nisbah >= 1.0 bermakna volum cukup untuk makan barisan depan kita.")
    print("Bawah 1.0, limit order kita duduk TAK TERISI walau harga sentuh aras.")


def main():
    ap = argparse.ArgumentParser(description="Rekod orderbook Luno untuk analisis pengisian")
    ap.add_argument("--analyse", action="store_true", help="ringkaskan data dikumpul")
    args = ap.parse_args()
    if args.analyse:
        analyse()
    else:
        record()


if __name__ == "__main__":
    try:
        main()
    except (OSError, ValueError) as exc:
        sys.exit(f"Ralat: {exc}")
    except KeyboardInterrupt:
        sys.exit("\nDibatalkan.")
