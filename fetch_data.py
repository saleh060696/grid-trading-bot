"""
Tarik & cache data harga BTC. Tiada API key perlu.

Sumber: Binance public klines (BTCUSDT, sejarah penuh dari Ogos 2017),
diskala ke MYR guna kadar tersirat dari harga XBTMYR semasa di Luno.

Kenapa Binance, bukan Luno: endpoint candle Luno perlu API key, dan Kraken
cap 720 candle setiap request. Binance bagi sejarah penuh tanpa auth —
itu yang kita perlu untuk dapat sampel BEBAS yang cukup.

NOTA: ini ANGGARAN BTC/MYR guna satu kadar USD/MYR tetap. Pergerakan
ringgit tak diambil kira. Untuk XBTMYR sebenar, guna grid_backtest.py
dengan API key Luno kau.

    python3 fetch_data.py           # harian (default)
    python3 fetch_data.py 1h        # sejam
    python3 fetch_data.py 4h        # 4 jam
"""

import csv
import json
import sys
import time
import urllib.request
from datetime import datetime, timezone

BINANCE = "https://api.binance.com/api/v3/klines"
START_MS = 1502928000000          # 2017-08-17, kandidat terawal Binance
LIMIT = 1000


def get(url):
    req = urllib.request.Request(url, headers={"User-Agent": "research/1.0"})
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.load(r)


def fetch_all(interval):
    """Paginate seluruh sejarah. Pulang [(open_time_ms, close_price), ...]."""
    out = []
    start = START_MS
    while True:
        url = (f"{BINANCE}?symbol=BTCUSDT&interval={interval}"
               f"&limit={LIMIT}&startTime={start}")
        batch = get(url)
        if not batch:
            break
        out.extend((int(r[0]), float(r[4])) for r in batch)
        if len(batch) < LIMIT:
            break
        start = int(batch[-1][0]) + 1
        print(f"  ...{len(out):,} candle", end="\r", flush=True)
        time.sleep(0.12)          # sopan dengan API awam
    return out


def main():
    interval = sys.argv[1] if len(sys.argv) > 1 else "1d"
    out_path = f"btc_{interval}.csv"

    print(f"Tarik BTCUSDT {interval} dari Binance (sejarah penuh)...")
    rows = fetch_all(interval)
    if not rows:
        raise SystemExit("Tiada data diterima.")
    print(f"  {len(rows):,} candle diterima." + " " * 20)

    print("Tarik harga XBTMYR semasa dari Luno...")
    t = get("https://api.luno.com/api/1/ticker?pair=XBTMYR")
    myr_now = float(t["last_trade"])
    rate = myr_now / rows[-1][1]
    print(f"  XBTMYR RM{myr_now:,.0f} -> kadar tersirat {rate:.4f}")

    with open(out_path, "w", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["time", "close"])
        for ts, close in rows:
            w.writerow([datetime.fromtimestamp(ts / 1000, timezone.utc)
                        .isoformat(), round(close * rate, 2)])

    first = datetime.fromtimestamp(rows[0][0] / 1000, timezone.utc).date()
    last = datetime.fromtimestamp(rows[-1][0] / 1000, timezone.utc).date()
    span_days = (last - first).days
    print(f"\nDisimpan {len(rows):,} candle -> {out_path}")
    print(f"Julat: {first} hingga {last}  ({span_days:,} hari / {span_days/365:.1f} tahun)")


if __name__ == "__main__":
    main()
