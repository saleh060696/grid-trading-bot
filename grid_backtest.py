"""
Grid Trading Backtest — Luno (XBTMYR)
=======================================

Apa script ni buat:
- Tarik data harga historical (daily candle) BTC/MYR dari Luno API
- Simulate strategy grid trading: letak beberapa "grid line" antara harga
  bawah dan atas, beli bila harga jatuh ke satu line, jual bila harga naik
  ke line seterusnya
- Kira untung/rugi kasar (SEBELUM potong fee) dan bersih (LEPAS potong fee)

PENTING:
- Ini backtest, bukan bot live. Tak ada duit sebenar bergerak.
- Saya (Claude) TAK dapat test script ni sendiri sebab environment saya
  ada network restriction (tak boleh call api.luno.com terus). So kau yang
  run kat laptop kau — kalau ada error, copy paste error tu kat saya,
  kita fix sama-sama.
- Perlu API key Luno (boleh guna PERMISSION READ-ONLY je, tak perlu bagi
  permission trading untuk backtest ni). Generate kat:
  Luno app > Settings > API Keys

Cara run:
    pip install requests
    python3 grid_backtest.py --key YOUR_KEY_ID --secret YOUR_KEY_SECRET
"""

import argparse
import time
from datetime import datetime, timedelta

import requests

LUNO_BASE_URL = "https://api.luno.com/api/exchange/1"


def fetch_daily_candles(pair: str, days: int, key_id: str, key_secret: str):
    """Tarik daily OHLC candle dari Luno untuk 'days' hari lepas."""
    since_ms = int((datetime.utcnow() - timedelta(days=days)).timestamp() * 1000)
    url = f"{LUNO_BASE_URL}/candles"
    params = {
        "pair": pair,
        "since": since_ms,
        "duration": 86400,  # 1 hari dalam saat
    }
    resp = requests.get(url, params=params, auth=(key_id, key_secret), timeout=15)
    resp.raise_for_status()
    data = resp.json()
    candles = data.get("candles", [])
    if not candles:
        raise ValueError(
            "Tak dapat candle data. Kemungkinan pair salah, API key tak "
            "cukup permission, atau format response Luno berubah."
        )
    closes = [float(c["close"]) for c in candles]
    timestamps = [int(c["timestamp"]) for c in candles]
    return timestamps, closes


def backtest_grid(closes, capital_myr: float, num_grids: int, fee_rate: float):
    """
    Simulate grid trading ringkas.

    - Range grid ditentukan dari harga min/max dalam tempoh backtest
    - Grid dibahagi 'num_grids' selang sama besar
    - Modal dibahagikan sama rata untuk setiap selang grid
    - Beli bila harga jatuh through garis bawah selang, jual bila harga
      naik through garis atas selang (lepas dah beli)
    """
    low = min(closes)
    high = max(closes)
    if high <= low:
        raise ValueError("Harga tak bergerak (flat) dalam tempoh ni — tak boleh grid.")

    lines = [low + (high - low) * i / num_grids for i in range(num_grids + 1)]
    capital_per_interval = capital_myr / num_grids

    # state[i] = True kalau posisi 'holding' untuk selang antara lines[i] dan lines[i+1]
    holding = [False] * num_grids
    btc_held = [0.0] * num_grids

    gross_profit = 0.0
    fees_paid = 0.0
    trades = 0

    for day_idx in range(1, len(closes)):
        prev_price = closes[day_idx - 1]
        price = closes[day_idx]

        for i in range(num_grids):
            lower = lines[i]
            upper = lines[i + 1]

            # Harga jatuh through garis bawah selang -> BELI
            if not holding[i] and prev_price > lower >= price:
                btc_bought = capital_per_interval / lower
                fee = capital_per_interval * fee_rate
                fees_paid += fee
                btc_held[i] = btc_bought
                holding[i] = True
                trades += 1

            # Harga naik through garis atas selang -> JUAL (kalau ada posisi)
            elif holding[i] and prev_price < upper <= price:
                sell_value = btc_held[i] * upper
                fee = sell_value * fee_rate
                fees_paid += fee
                gross_profit += sell_value - capital_per_interval
                holding[i] = False
                btc_held[i] = 0.0
                trades += 1

    # Kira unrealised value untuk posisi yang masih 'holding' pada harga akhir
    final_price = closes[-1]
    unrealised = sum(
        btc_held[i] * final_price - capital_per_interval
        for i in range(num_grids)
        if holding[i]
    )

    net_profit = gross_profit + unrealised - fees_paid

    return {
        "low": low,
        "high": high,
        "num_grids": num_grids,
        "trades": trades,
        "gross_profit_realised": gross_profit,
        "unrealised_pl": unrealised,
        "fees_paid": fees_paid,
        "net_profit": net_profit,
        "net_return_pct": (net_profit / capital_myr) * 100,
    }


def main():
    parser = argparse.ArgumentParser(description="Grid trading backtest untuk Luno XBTMYR")
    parser.add_argument("--key", required=True, help="Luno API Key ID")
    parser.add_argument("--secret", required=True, help="Luno API Key Secret")
    parser.add_argument("--pair", default="XBTMYR", help="Trading pair (default: XBTMYR)")
    parser.add_argument("--days", type=int, default=60, help="Berapa hari lookback (default: 60)")
    parser.add_argument("--capital", type=float, default=350.0, help="Modal MYR (default: 350)")
    parser.add_argument("--grids", type=int, default=10, help="Bilangan grid line (default: 10)")
    parser.add_argument("--fee", type=float, default=0.002, help="Fee rate per trade, contoh 0.002 = 0.2%% (SEMAK fee sebenar kau kat Luno)")
    args = parser.parse_args()

    print(f"Tarik {args.days} hari data harian untuk {args.pair}...")
    timestamps, closes = fetch_daily_candles(args.pair, args.days, args.key, args.secret)
    print(f"Dapat {len(closes)} hari data. Harga terkini: RM{closes[-1]:,.2f}")

    result = backtest_grid(closes, args.capital, args.grids, args.fee)

    print("\n=== HASIL BACKTEST ===")
    print(f"Range harga  : RM{result['low']:,.2f} - RM{result['high']:,.2f}")
    print(f"Modal        : RM{args.capital:,.2f}")
    print(f"Bilangan grid: {result['num_grids']}")
    print(f"Jumlah trade : {result['trades']}")
    print(f"Untung realised (sebelum fee): RM{result['gross_profit_realised']:,.2f}")
    print(f"Untung/rugi belum jual (posisi masih pegang): RM{result['unrealised_pl']:,.2f}")
    print(f"Fee dibayar  : RM{result['fees_paid']:,.2f}")
    print(f"UNTUNG BERSIH: RM{result['net_profit']:,.2f} ({result['net_return_pct']:.2f}%)")
    print("\nNota: Ini simulasi berdasarkan harga LEPAS (historical). Prestasi lepas")
    print("tak menjamin prestasi akan datang. Grid trading rugi teruk kalau harga")
    print("trend turun berterusan tanpa naik balik dalam range yang ditetapkan.")


if __name__ == "__main__":
    main()
