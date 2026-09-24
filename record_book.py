"""
Perekam Orderbook — kumpul bukti sama ada limit order kita akan terisi
=======================================================================

REKA BENTUK (ditulis semula selepas versi pertama mati dalam 33 jam)

Versi pertama guna cursor: simpan timestamp dagangan terakhir, minta
semua dagangan sejak itu. Ia gagal kekal, dan sebabnya berbaloi difahami:

  - Luno pulangkan maksimum 100 dagangan, dan ia yang TERAWAL sejak
    'since', bukan yang terkini.
  - GitHub Actions throttle jadual */5 kepada ~4 jam sekali.
  - Jadi setiap run cursor bergerak 100 dagangan (~1.5 jam) sementara
    4 jam berlalu. Ia ketinggalan ~2.5 jam setiap run.
  - Bila cursor melebihi 24 jam, Luno pulangkan HTTP 400. Run crash
    sebelum sempat kemas kini state, jadi cursor tak pernah pulih.
    Kematian kekal selepas ~10 run.

Pengajarannya: cursor tak boleh berfungsi bila kita poll lebih jarang
daripada cap. Jadi kita berhenti cuba kira SETIAP dagangan.

Sebaliknya setiap run ialah SAMPEL BEBAS:

  - Volum sebenar datang dari rolling_24_hour_volume pada ticker. Luno
    yang kira, jadi ia lengkap tanpa mengira kekerapan poll kita.
  - Dagangan yang disampel beritahu TABURAN — berapa peratus volum
    berlaku pada setiap jarak dari mid.
  - Gabungan dua itu bagi kadar volum pada setiap aras harga, yang
    dibahagi dengan kedalaman barisan memberi jawapan sebenar:
    berapa lama order kita perlu menunggu untuk terisi.

Reka bentuk ni kebal terhadap jurang poll. Run yang terlepas cuma
bermakna satu sampel kurang, bukan data rosak.

Guna data AWAM Luno. Tiada API key. Tiada duit.

    .venv/bin/python record_book.py            # rekod satu sampel
    .venv/bin/python record_book.py --analyse  # ringkasan
"""

import argparse
import datetime as _dt
import json
import os
import statistics
import sys
import time

from orderbook import fetch_book, fetch_ticker, fetch_trades, queue_ahead

DATA = "book_log.jsonl"

# Tetingkap sampel. Cukup pendek untuk jarang kena cap 100 dagangan,
# cukup panjang untuk tangkap taburan harga yang bermakna.
SAMPLE_WINDOW_MIN = 90

OFFSETS = [0.0005, 0.001, 0.002, 0.005, 0.01, 0.02]


def record():
    book = fetch_book()
    tick = fetch_ticker()
    mid = (book["bids"][0][0] + book["asks"][0][0]) / 2

    since = int(time.time() * 1000) - SAMPLE_WINDOW_MIN * 60000
    trades = fetch_trades(since_ms=since)

    sample_vol = sum(t["volume"] for t in trades)
    # Jualan-taker (is_buy=False) ialah yang memakan sebelah BID — iaitu
    # yang boleh mengisi limit beli kita. Belian-taker makan sebelah ask.
    sell_vol = sum(t["volume"] for t in trades if not t["is_buy"])
    span_min = ((trades[-1]["ts"] - trades[0]["ts"]) / 60000) if len(trades) > 1 else 0.0
    capped = len(trades) >= 100

    row = {
        "ts": book["ts"],
        "mid": mid,
        "spread_pct": (book["asks"][0][0] - book["bids"][0][0]) / mid * 100,
        # Volum LENGKAP 24 jam, dikira oleh Luno — kebal terhadap jurang poll.
        "vol_24h": tick["vol_24h"],
        "sample": {
            "n": len(trades),
            "span_min": round(span_min, 1),
            "volume": round(sample_vol, 8),
            "capped": capped,
        },
        "sell_frac": round(sell_vol / sample_vol, 6) if sample_vol > 0 else 0.0,
        # Barisan tepat di bid terbaik: inilah yang perlu habis dimakan
        # sebelum order kita di aras yang sama boleh terisi.
        "best_bid_queue": round(book["bids"][0][1], 8),
        "best_ask_queue": round(book["asks"][0][1], 8),
        "depth": {},
        # Pecahan volum SAMPEL yang berlaku pada/bawah setiap aras beli.
        # Ini taburan, bukan jumlah — jadi ia sah walau sampel terpotong.
        "frac": {},
    }
    for off in OFFSETS:
        bp = round(mid * (1 - off))
        ap = round(mid * (1 + off))
        row["depth"][str(off)] = {
            "buy_queue": round(queue_ahead(book, "buy", float(bp)), 8),
            "sell_queue": round(queue_ahead(book, "sell", float(ap)), 8),
        }
        below = sum(t["volume"] for t in trades if not t["is_buy"] and t["price"] <= bp)
        above = sum(t["volume"] for t in trades if t["is_buy"] and t["price"] >= ap)
        row["frac"][str(off)] = {
            "below": round(below / sample_vol, 6) if sample_vol > 0 else 0.0,
            "above": round(above / sample_vol, 6) if sample_vol > 0 else 0.0,
        }

    with open(DATA, "a") as fh:
        fh.write(json.dumps(row) + "\n")

    flag = " (kena cap 100)" if capped else ""
    print(f"RM{mid:,.0f}  spread {row['spread_pct']:.4f}%  "
          f"24j {tick['vol_24h']:.2f} BTC  "
          f"sampel {len(trades)} dagangan/{span_min:.0f}min{flag}")


def _load():
    if not os.path.exists(DATA):
        sys.exit(f"'{DATA}' tak jumpa. Run dulu: record_book.py")
    rows, old = [], 0
    with open(DATA) as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                r = json.loads(line)
            except ValueError:
                continue
            if "frac" in r and "vol_24h" in r:      # format baharu
                rows.append(r)
            else:
                old += 1
    if old:
        print(f"(melangkau {old} baris format lama — skema cursor sebelum "
              "penulisan semula)\n")
    if not rows:
        sys.exit("Tiada sampel format baharu lagi. Tunggu run seterusnya.")
    return rows


def analyse():
    rows = _load()
    span_h = (rows[-1]["ts"] - rows[0]["ts"]) / 3600000
    vol24 = statistics.median(r["vol_24h"] for r in rows)
    rate_h = vol24 / 24

    print(f"Sampel      : {len(rows)}")
    print(f"Tempoh      : {span_h:.1f} jam ({span_h/24:.1f} hari)")
    print(f"Volum 24-jam: {vol24:.2f} BTC (median) -> {rate_h:.3f} BTC/jam")
    print(f"Spread      : median {statistics.median(r['spread_pct'] for r in rows):.4f}%")
    capped = sum(1 for r in rows if r["sample"]["capped"])
    if capped:
        print(f"Nota        : {capped}/{len(rows)} sampel kena cap 100 dagangan. "
              "Taburan masih sah; jumlah datang dari ticker.")
    # Metrik utama: pusing ganti barisan di bid terbaik.
    #
    # Kenapa ini, bukan taburan jarak jauh: untuk mengukur berapa volum
    # didagang 1% bawah mid, tetingkap sampel perlu cukup panjang untuk
    # harga bergerak 1% — berjam-jam. Tapi cap 100 dagangan hadkan sampel
    # kepada beberapa minit masa pasaran sibuk. Jadi taburan jarak jauh
    # sentiasa kosong dan tak bermakna.
    #
    # Yang BOLEH diukur dengan tetingkap pendek: bila harga berada di satu
    # aras, berapa lama barisan di situ mengambil masa untuk habis. Itu
    # soalan pengisian sebenar, dan ia tak bergantung pada pergerakan harga.
    sell_frac = statistics.mean(r.get("sell_frac", 0.5) for r in rows)
    sell_rate = rate_h * sell_frac
    bidq = statistics.median(r.get("best_bid_queue", 0) for r in rows)

    print()
    print("PUSING GANTI BARISAN di bid terbaik")
    print()
    print(f"  Barisan di bid terbaik  : {bidq:.4f} BTC (median)")
    print(f"  Volum jualan-taker      : {sell_frac*100:.1f}% daripada semua volum")
    print(f"  Kadar makan bid         : {sell_rate:.4f} BTC/jam")
    if sell_rate > 0:
        mins = bidq / sell_rate * 60
        print(f"  Masa habiskan barisan   : {mins:.0f} minit")
        print()
        if mins < 15:
            print("  -> Barisan pusing cepat. Limit order berpeluang baik terisi")
            print("     SELAGI harga kekal di aras itu.")
        elif mins < 60:
            print("  -> Barisan pusing sederhana. Order kau terisi hanya kalau")
            print("     harga berlegar di aras itu sekurang-kurangnya sejam.")
        else:
            print("  -> Barisan pusing PERLAHAN. Harga jarang kekal selama ini")
            print("     di satu aras, jadi order kau kemungkinan besar tak terisi.")
    print()
    print("Kedalaman kumulatif pada setiap jarak (untuk rujukan):")
    print(f"{'jarak':>7} {'barisan beli':>14} {'jam pada kadar ni':>20}")
    print("-" * 44)
    for off in OFFSETS:
        q = statistics.median(r["depth"][str(off)]["buy_queue"] for r in rows)
        h = q / sell_rate if sell_rate > 0 else 0
        print(f"{off*100:>6.2f}% {q:>14.4f} {h:>19.1f}h")
    print()
    print("Lajur kanan ialah masa untuk makan SELURUH buku sampai aras itu —")
    print("had atas, sebab harga tak turun lurus. Anggap ia isyarat kasar.")

    _coverage(rows)


def _coverage(rows):
    """Liputan ikut jam (waktu Malaysia). Dedahkan jurang pengumpulan."""
    by_hour = {}
    for r in rows:
        h = (_dt.datetime.fromtimestamp(r["ts"] / 1000, _dt.timezone.utc).hour + 8) % 24
        by_hour[h] = by_hour.get(h, 0) + 1

    covered = sorted(by_hour)
    print()
    print(f"LIPUTAN — {len(covered)}/24 jam ada sampel (waktu Malaysia)")
    missing = [h for h in range(24) if h not in by_hour]
    if missing:
        print(f"  {len(missing)} jam kosong: {', '.join(f'{h:02d}' for h in missing)}")
        us = [h for h in missing if h >= 21 or h <= 6]
        if us:
            print("  Termasuk jam sesi Amerika — tempoh paling sibuk.")
    else:
        print("  Liputan penuh 24 jam.")


def main():
    ap = argparse.ArgumentParser(description="Rekod orderbook Luno untuk analisis pengisian")
    ap.add_argument("--analyse", action="store_true")
    args = ap.parse_args()
    analyse() if args.analyse else record()


if __name__ == "__main__":
    try:
        main()
    except (OSError, ValueError) as exc:
        sys.exit(f"Ralat: {exc}")
    except KeyboardInterrupt:
        sys.exit("\nDibatalkan.")
