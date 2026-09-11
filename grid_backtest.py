"""
Grid Trading Backtest — Luno (XBTMYR)
=======================================

Apa script ni buat:
- Tarik data harga historical (daily candle) BTC/MYR dari Luno API
- Simulate strategy grid trading: letak beberapa "grid line" antara harga
  bawah dan atas, beli bila harga jatuh ke satu line, jual bila harga naik
  ke line seterusnya
- Kira untung/rugi lepas potong fee, dan banding dengan buy & hold

PENTING:
- Ini backtest, bukan bot live. Tak ada duit sebenar bergerak.
- Endpoint /candles Luno WAJIB pakai API key (READ-ONLY dah cukup, tak perlu
  bagi permission trading). Generate kat: Luno app > Settings > API Keys
- Jangan taip key terus dalam command (nanti masuk shell history). Guna
  environment variable — tengok "Cara run" bawah ni.

Cara run:
    python3 -m venv .venv
    .venv/bin/pip install requests

    # cara selamat: set env var dulu (ruang kosong depan 'export' = tak masuk history)
     export LUNO_KEY_ID=xxxxx
     export LUNO_KEY_SECRET=yyyyy
    .venv/bin/python grid_backtest.py

    # nak test logic dulu tanpa API key:
    .venv/bin/python grid_backtest.py --demo
    .venv/bin/python grid_backtest.py --csv harga.csv
"""

import argparse
import os
import sys
from datetime import datetime, timedelta, timezone

try:
    import requests
except ImportError:
    sys.exit(
        "Module 'requests' tak dijumpai.\n"
        "  python3 -m venv .venv\n"
        "  .venv/bin/pip install requests\n"
        "  .venv/bin/python grid_backtest.py --demo"
    )

LUNO_BASE_URL = "https://api.luno.com/api/exchange/1"

# Luno hantar maksimum 1000 candle setiap request.
LUNO_MAX_CANDLES = 1000


def fetch_daily_candles(pair, days, key_id, key_secret):
    """Tarik daily OHLC candle dari Luno untuk 'days' hari lepas."""
    if days > LUNO_MAX_CANDLES:
        raise ValueError(
            f"--days {days} melebihi had Luno ({LUNO_MAX_CANDLES} candle setiap request)."
        )

    since_ms = int((datetime.now(timezone.utc) - timedelta(days=days)).timestamp() * 1000)
    params = {"pair": pair, "since": since_ms, "duration": 86400}

    try:
        resp = requests.get(
            f"{LUNO_BASE_URL}/candles",
            params=params,
            auth=(key_id, key_secret),
            timeout=15,
        )
    except requests.exceptions.RequestException as exc:
        raise RuntimeError(f"Gagal sambung ke Luno API: {exc}") from exc

    if resp.status_code in (401, 403):
        raise RuntimeError(
            "Luno tolak API key (HTTP %d). Semak key ID/secret betul, key masih "
            "aktif, dan ada permission READ." % resp.status_code
        )
    if resp.status_code == 429:
        raise RuntimeError("Kena rate limit Luno (HTTP 429). Tunggu sekejap, cuba lagi.")
    resp.raise_for_status()

    candles = resp.json().get("candles", [])
    if not candles:
        raise ValueError(
            f"Tak dapat candle data untuk '{pair}'. Semak nama pair (contoh XBTMYR) "
            "dan pastikan --days cukup panjang."
        )

    # Luno tak jamin urutan — susun ikut masa supaya simulasi jalan betul.
    candles.sort(key=lambda c: int(c["timestamp"]))
    timestamps = [int(c["timestamp"]) for c in candles]
    closes = [float(c["close"]) for c in candles]
    return timestamps, closes


def load_csv_closes(path):
    """Baca harga close dari CSV. Ambil column 'close' kalau ada header."""
    import csv

    with open(path, newline="") as fh:
        rows = list(csv.reader(fh))
    if not rows:
        raise ValueError(f"CSV '{path}' kosong.")

    header, start = rows[0], 0
    col = 0
    lowered = [c.strip().lower() for c in header]
    if "close" in lowered:
        col, start = lowered.index("close"), 1

    closes = []
    for row in rows[start:]:
        if not row or not row[col].strip():
            continue
        closes.append(float(row[col]))
    if len(closes) < 2:
        raise ValueError(f"CSV '{path}' perlu sekurang-kurangnya 2 baris harga.")
    return closes


def make_demo_closes(n=120):
    """Harga tiruan (sideways + noise) untuk test logic tanpa API key."""
    import math
    import random

    random.seed(1)
    return [
        300000 + 20000 * math.sin(i / 5.0) + random.gauss(0, 2500) for i in range(n)
    ]


def backtest_grid(closes, capital_myr, num_grids, fee_rate, warmup=20,
                  min_order_btc=0.0001, min_edge=0.0, stop_loss=None):
    """
    Simulate grid trading ringkas.

    Range grid diambil dari 'warmup' hari PERTAMA sahaja, lepas tu baru mula
    trade. Ini penting: kalau range diambil dari keseluruhan tempoh, backtest
    "tengok masa depan" (lookahead bias) dan hasil nampak lebih cantik dari
    apa yang boleh dicapai sebenarnya.

    Set warmup=0 kalau nak range dari keseluruhan data (TAK realistik).

    Dua gate kos ditambah (konsep "minimum net edge"):
    - min_order_btc: Luno tolak order bawah 0.0001 BTC. Kalau saiz setiap
      selang tak cukup besar, trade tu TAK boleh jadi dalam realiti.
    - min_edge: jarak antara garis grid mesti melebihi kos pergi-balik
      (2 x fee) campur margin ni. Kalau tak, setiap round trip rugi.

    stop_loss (contoh 0.02 = 2%): kalau harga jatuh bawah low*(1-stop_loss),
    jual SEMUA posisi dan berhenti beli. Sambung balik bila harga masuk
    semula dalam range. Ini tak buat duit — dia potong ekor kerugian bila
    harga tembus keluar range dan terus turun. Set None untuk matikan.
    """
    if num_grids < 1:
        raise ValueError("--grids mesti sekurang-kurangnya 1.")
    if capital_myr <= 0:
        raise ValueError("--capital mesti lebih dari 0.")
    if not 0 <= fee_rate < 1:
        raise ValueError("--fee mesti antara 0 dan 1 (contoh 0.001 = 0.1%).")
    if len(closes) < 2:
        raise ValueError("Perlu sekurang-kurangnya 2 hari data harga.")
    if stop_loss is not None and not 0 < stop_loss < 1:
        raise ValueError("--stop-loss mesti antara 0 dan 1 (contoh 0.02 = 2%).")

    if warmup > 0:
        if len(closes) <= warmup + 1:
            raise ValueError(
                f"Data cuma {len(closes)} hari tapi warmup {warmup} hari. "
                "Tambah --days atau kurangkan --warmup."
            )
        window = closes[:warmup]
        traded = closes[warmup - 1:]  # sambung dari hari terakhir warmup
    else:
        window = closes
        traded = closes

    low, high = min(window), max(window)
    if high <= low:
        raise ValueError("Harga flat dalam tempoh warmup — tak boleh bentuk grid.")

    lines = [low + (high - low) * i / num_grids for i in range(num_grids + 1)]
    capital_per_interval = capital_myr / num_grids

    # --- Gate 1: setiap round trip mesti ada edge lebih dari kos ---
    # Selang paling rapat (relatif) ialah yang paling atas sebab harga paling tinggi.
    spacing = (high - low) / num_grids
    worst_edge_pct = spacing / lines[-2]          # edge paling kecil antara semua selang
    breakeven_pct = 2 * fee_rate                  # fee beli + fee jual
    required_pct = breakeven_pct + min_edge
    edge_ok = worst_edge_pct > required_pct

    # --- Gate 2: saiz order mesti capai minimum Luno ---
    # BTC paling sedikit dibeli ialah pada harga paling tinggi (lines[-2]).
    smallest_btc = (capital_per_interval * (1 - fee_rate)) / lines[-2]
    order_ok = smallest_btc >= min_order_btc

    holding = [False] * num_grids
    btc_held = [0.0] * num_grids

    realised_pl = 0.0  # untung/rugi lepas tolak fee
    fees_paid = 0.0
    buys = sells = 0
    skipped_small = skipped_edge = 0
    stop_triggers = 0
    stop_realised = 0.0
    cash = capital_myr          # duit tunai belum digunakan
    skipped_cash = 0
    halted = False
    stop_level = low * (1 - stop_loss) if stop_loss is not None else None

    for day_idx in range(1, len(traded)):
        prev_price = traded[day_idx - 1]
        price = traded[day_idx]

        if stop_level is not None:
            if not halted and price < stop_level:
                # Tembus bawah range — jual semua, berhenti beli.
                for i in range(num_grids):
                    if holding[i]:
                        proceeds = btc_held[i] * price
                        fee = proceeds * fee_rate
                        fees_paid += fee
                        cash += proceeds - fee
                        pl = (proceeds - fee) - capital_per_interval
                        realised_pl += pl
                        stop_realised += pl
                        holding[i] = False
                        btc_held[i] = 0.0
                        sells += 1
                halted = True
                stop_triggers += 1
            elif halted and low <= price <= high:
                # Harga dah balik dalam range — sambung trading.
                halted = False

        if halted:
            continue

        for i in range(num_grids):
            lower, upper = lines[i], lines[i + 1]

            # Harga jatuh through garis bawah selang -> BELI
            if not holding[i] and prev_price > lower >= price:
                btc_would_get = (capital_per_interval * (1 - fee_rate)) / lower
                if btc_would_get < min_order_btc:
                    skipped_small += 1
                    continue
                if (upper - lower) / lower <= required_pct:
                    skipped_edge += 1
                    continue
                if cash < capital_per_interval:
                    # Duit dah habis (contoh: lepas stop-loss realisasi rugi).
                    skipped_cash += 1
                    continue
                fee = capital_per_interval * fee_rate
                # Fee ditolak dari duit belanja, jadi BTC yang dapat kurang sikit.
                cash -= capital_per_interval
                btc_held[i] = (capital_per_interval - fee) / lower
                fees_paid += fee
                holding[i] = True
                buys += 1

            # Harga naik through garis atas selang -> JUAL (kalau ada posisi)
            elif holding[i] and prev_price < upper <= price:
                proceeds = btc_held[i] * upper
                fee = proceeds * fee_rate
                fees_paid += fee
                cash += proceeds - fee
                realised_pl += (proceeds - fee) - capital_per_interval
                holding[i] = False
                btc_held[i] = 0.0
                sells += 1

    # Posisi yang masih terbuka: nilai pada harga akhir, tolak fee keluar.
    final_price = traded[-1]
    open_positions = sum(1 for h in holding if h)
    btc_outstanding = sum(btc_held)
    unrealised = sum(
        btc_held[i] * final_price * (1 - fee_rate) - capital_per_interval
        for i in range(num_grids)
        if holding[i]
    )

    # Sumber kebenaran tunggal: tunai + nilai pasaran posisi terbuka.
    net_profit = (cash + btc_outstanding * final_price * (1 - fee_rate)) - capital_myr

    # Benchmark: kalau modal sama dibeli terus hari pertama dan pegang je.
    entry, exit_ = traded[0], traded[-1]
    bh_btc = (capital_myr * (1 - fee_rate)) / entry
    buy_hold_pl = bh_btc * exit_ * (1 - fee_rate) - capital_myr

    return {
        "low": low,
        "high": high,
        "warmup": warmup,
        "days_traded": len(traded) - 1,
        "num_grids": num_grids,
        "buys": buys,
        "sells": sells,
        "trades": buys + sells,
        "realised_pl": realised_pl,
        "unrealised_pl": unrealised,
        "open_positions": open_positions,
        "btc_outstanding": btc_outstanding,
        "fees_paid": fees_paid,
        "spacing_pct": worst_edge_pct * 100,
        "breakeven_pct": breakeven_pct * 100,
        "required_pct": required_pct * 100,
        "edge_ok": edge_ok,
        "order_ok": order_ok,
        "smallest_btc": smallest_btc,
        "skipped_small": skipped_small,
        "skipped_edge": skipped_edge,
        "skipped_cash": skipped_cash,
        "cash_left": cash,
        "stop_loss": stop_loss,
        "stop_level": stop_level,
        "stop_triggers": stop_triggers,
        "stop_realised": stop_realised,
        "halted_at_end": halted,
        "net_profit": net_profit,
        "net_return_pct": (net_profit / capital_myr) * 100,
        "buy_hold_pl": buy_hold_pl,
        "buy_hold_pct": (buy_hold_pl / capital_myr) * 100,
    }


def main():
    parser = argparse.ArgumentParser(description="Grid trading backtest untuk Luno XBTMYR")
    parser.add_argument("--key", default=os.environ.get("LUNO_KEY_ID"),
                        help="Luno API Key ID (default: env LUNO_KEY_ID)")
    parser.add_argument("--secret", default=os.environ.get("LUNO_KEY_SECRET"),
                        help="Luno API Key Secret (default: env LUNO_KEY_SECRET)")
    parser.add_argument("--pair", default="XBTMYR", help="Trading pair (default: XBTMYR)")
    parser.add_argument("--days", type=int, default=180, help="Hari lookback (default: 180)")
    parser.add_argument("--capital", type=float, default=350.0, help="Modal MYR (default: 350)")
    parser.add_argument("--grids", type=int, default=10, help="Bilangan selang grid (default: 10)")
    parser.add_argument("--fee", type=float, default=0.0035,
                        help="Fee per trade. Default 0.0035 = 0.35%% (maker, tier bawah "
                             "RM5k volum 30-hari). Taker tier sama = 0.006")
    parser.add_argument("--min-order-btc", type=float, default=0.0001,
                        help="Saiz order minimum Luno untuk XBTMYR (default 0.0001 BTC)")
    parser.add_argument("--stop-loss", type=float, default=None, metavar="BUF",
                        help="Jual semua & berhenti beli bila harga jatuh BUF bawah "
                             "dasar grid, 0.02 = 2%%. Default: tiada stop-loss")
    parser.add_argument("--min-edge", type=float, default=0.0,
                        help="Margin tambahan atas breakeven sebelum trade, "
                             "0.002 = 0.2%% (default 0 = breakeven je)")
    parser.add_argument("--warmup", type=int, default=20,
                        help="Hari awal untuk tetapkan range grid, tak ditrade (default: 20). "
                             "0 = guna seluruh data (ada lookahead bias)")
    parser.add_argument("--csv", help="Backtest dari fail CSV, bukan API")
    parser.add_argument("--demo", action="store_true",
                        help="Guna harga tiruan untuk test script tanpa API key")
    args = parser.parse_args()

    if args.days < 2:
        parser.error("--days mesti sekurang-kurangnya 2.")
    if args.warmup < 0:
        parser.error("--warmup tak boleh negatif.")

    if args.demo:
        closes = make_demo_closes()
        print(f"MOD DEMO — harga TIRUAN, {len(closes)} hari. Bukan data sebenar.")
    elif args.csv:
        closes = load_csv_closes(args.csv)
        print(f"Baca {len(closes)} harga dari {args.csv}")
    else:
        if not args.key or not args.secret:
            parser.error(
                "Perlu API key. Set env LUNO_KEY_ID dan LUNO_KEY_SECRET, "
                "atau guna --demo / --csv untuk test tanpa key."
            )
        print(f"Tarik {args.days} hari data harian untuk {args.pair}...")
        timestamps, closes = fetch_daily_candles(args.pair, args.days, args.key, args.secret)
        first = datetime.fromtimestamp(timestamps[0] / 1000, timezone.utc).date()
        last = datetime.fromtimestamp(timestamps[-1] / 1000, timezone.utc).date()
        print(f"Dapat {len(closes)} hari data ({first} hingga {last}).")
        print(f"Harga terkini: RM{closes[-1]:,.2f}")

    if args.warmup > 0:
        print(f"Range grid ditetapkan dari {args.warmup} hari pertama, "
              f"trading disimulasi pada baki hari.")
    else:
        print("AMARAN: --warmup 0 guna harga masa depan untuk set range grid "
              "(lookahead bias). Hasil akan nampak lebih cantik dari realiti.")

    r = backtest_grid(closes, args.capital, args.grids, args.fee, args.warmup,
                      args.min_order_btc, args.min_edge, args.stop_loss)

    print("\n=== SEMAKAN KOS (sebelum tengok hasil) ===")
    print(f"Jarak antara garis grid : {r['spacing_pct']:.3f}%  (selang paling rapat)")
    print(f"Kos pergi-balik (2x fee): {r['breakeven_pct']:.3f}%")
    print(f"Perlu sekurang-kurangnya: {r['required_pct']:.3f}%")
    if r["edge_ok"]:
        print(f"  -> LULUS: edge bersih {r['spacing_pct'] - r['required_pct']:+.3f}% setiap round trip")
    else:
        print(f"  -> GAGAL: edge bersih {r['spacing_pct'] - r['required_pct']:+.3f}% "
              "— setiap round trip RUGI. Kurangkan --grids.")
    print(f"Saiz order terkecil     : {r['smallest_btc']:.8f} BTC "
          f"(RM{args.capital / args.grids:,.2f} setiap selang)")
    if r["order_ok"]:
        print(f"  -> LULUS: atas minimum Luno {args.min_order_btc} BTC")
    else:
        print(f"  -> GAGAL: bawah minimum Luno {args.min_order_btc} BTC "
              "— order akan DITOLAK. Kurangkan --grids atau tambah modal.")

    if r["stop_loss"] is not None:
        print(f"Stop-loss               : aktif pada RM{r['stop_level']:,.2f} "
              f"({r['stop_loss'] * 100:.1f}% bawah dasar grid)")
    else:
        print("Stop-loss               : TIADA — posisi dipegang walau harga terjunam")

    print("\n=== HASIL BACKTEST ===")
    print(f"Range grid    : RM{r['low']:,.2f} - RM{r['high']:,.2f}  ({r['num_grids']} selang)")
    print(f"Hari ditrade  : {r['days_traded']}")
    print(f"Modal         : RM{args.capital:,.2f}")
    print(f"Trade         : {r['trades']}  ({r['buys']} beli, {r['sells']} jual)")
    print(f"Untung direalisasi (lepas fee): RM{r['realised_pl']:,.2f}")
    print(f"Posisi belum jual : {r['open_positions']} selang, "
          f"{r['btc_outstanding']:.8f} BTC")
    print(f"Untung/rugi belum jual        : RM{r['unrealised_pl']:,.2f}")
    print(f"Jumlah fee dibayar            : RM{r['fees_paid']:,.2f}")
    print(f"UNTUNG BERSIH : RM{r['net_profit']:,.2f} ({r['net_return_pct']:.2f}%)")
    print(f"Buy & hold    : RM{r['buy_hold_pl']:,.2f} ({r['buy_hold_pct']:.2f}%)  <- pembanding")

    if r["stop_loss"] is not None:
        if r["stop_triggers"]:
            print(f"Stop-loss dicetuskan          : {r['stop_triggers']} kali, "
                  f"merealisasi RM{r['stop_realised']:,.2f}")
            if r["halted_at_end"]:
                print("  (masih berhenti di hujung tempoh — harga tak balik dalam range)")
        else:
            print("Stop-loss dicetuskan          : tidak pernah")

    if r["skipped_small"] or r["skipped_edge"] or r["skipped_cash"]:
        print(f"\nTrade tak jadi: {r['skipped_small']} sebab bawah saiz minimum, "
              f"{r['skipped_edge']} sebab edge tak cukup tutup fee, "
              f"{r['skipped_cash']} sebab duit tunai habis.")

    if r["trades"] == 0:
        print("\nNota: 0 trade — harga tak pernah lintas mana-mana garis grid. "
              "Cuba tambah --days, naikkan --grids, atau kurangkan --warmup.")

    print("\nNota: Ini simulasi berdasarkan harga LEPAS (historical). Prestasi lepas")
    print("tak menjamin prestasi akan datang. Grid trading rugi teruk kalau harga")
    print("trend turun berterusan tanpa naik balik dalam range yang ditetapkan.")
    print("Simulasi ini guna harga CLOSE harian sahaja — tak ambil kira slippage,")
    print("spread bid/ask, atau pergerakan dalam hari.")


if __name__ == "__main__":
    try:
        main()
    except (ValueError, RuntimeError, OSError) as exc:
        sys.exit(f"Ralat: {exc}")
    except KeyboardInterrupt:
        sys.exit("\nDibatalkan.")
