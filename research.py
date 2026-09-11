"""
Harness Penyelidikan Strategi Trading
======================================

Tujuan alat ni BUKAN untuk cari strategi untung. Tujuannya untuk
memberitahu kau dengan jujur bila strategi kau TAK untung — sebelum
kau letak duit sebenar.

Tiga benda yang dia paksa:

1. WALK-FORWARD: parameter ditala atas data lama (train), diuji atas data
   yang strategi tak pernah nampak (test). Nombor test yang penting.
   Nombor train sentiasa nampak cantik — itu tak bermakna apa-apa.

2. KOS SEBENAR: fee Luno, saiz order minimum, tracking tunai. Tiada
   trade yang mustahil dalam realiti.

3. METRIK JUJUR: bukan untung je. Max drawdown (rugi paling teruk dari
   puncak), Sharpe (pulangan berbanding risiko), dan banding dengan
   buy & hold. Strategi yang untung RM10 tapi drawdown 40% adalah teruk.

Cara run:
    python3 fetch_data.py              # tarik & cache data harga
    .venv/bin/python research.py       # jalankan semua strategi
"""

import math
import statistics
from dataclasses import dataclass, field


# --------------------------------------------------------------------------
# Broker: simulasi kos & had sebenar
# --------------------------------------------------------------------------

class Broker:
    """Simulasi akaun. Tolak setiap trade yang mustahil dalam realiti."""

    def __init__(self, cash, fee_rate=0.0035, min_order_btc=0.0001):
        self.cash0 = cash
        self.cash = cash
        self.btc = 0.0
        self.fee_rate = fee_rate
        self.min_order_btc = min_order_btc
        self.fees_paid = 0.0
        self.buys = 0
        self.sells = 0
        self.rejected = 0

    def buy(self, price, myr):
        """Beli guna 'myr' ringgit. Pulang True kalau berjaya."""
        if myr <= 0 or myr > self.cash + 1e-9:
            self.rejected += 1
            return False
        fee = myr * self.fee_rate
        got = (myr - fee) / price
        if got < self.min_order_btc:
            self.rejected += 1
            return False
        self.cash -= myr
        self.btc += got
        self.fees_paid += fee
        self.buys += 1
        return True

    def sell(self, price, btc):
        """Jual 'btc'. Pulang True kalau berjaya."""
        btc = min(btc, self.btc)
        if btc < self.min_order_btc:
            self.rejected += 1
            return False
        proceeds = btc * price
        fee = proceeds * self.fee_rate
        self.cash += proceeds - fee
        self.btc -= btc
        self.fees_paid += fee
        self.sells += 1
        return True

    def sell_all(self, price):
        return self.sell(price, self.btc)

    def equity(self, price):
        """Nilai portfolio kalau dijual sekarang (lepas fee keluar)."""
        return self.cash + self.btc * price * (1 - self.fee_rate)


# --------------------------------------------------------------------------
# Strategi: warisi kelas ni untuk uji idea kau sendiri
# --------------------------------------------------------------------------

class Strategy:
    name = "base"

    def setup(self, warmup_prices):
        """Dipanggil sekali dengan harga tempoh warmup. Set parameter di sini."""

    def on_bar(self, i, price, prev_price, broker):
        """Dipanggil setiap hari. Buat keputusan trade di sini."""


class BuyHold(Strategy):
    name = "buy & hold"

    def __init__(self):
        self.done = False

    def on_bar(self, i, price, prev_price, broker):
        if not self.done:
            broker.buy(price, broker.cash)
            self.done = True


class DCA(Strategy):
    """Beli jumlah tetap setiap N hari sehingga modal habis."""
    name = "DCA"

    def __init__(self, every_days=7, slices=12):
        self.every = every_days
        self.slices = slices
        self.amt = None

    def setup(self, warmup_prices):
        self.amt = None

    def on_bar(self, i, price, prev_price, broker):
        if self.amt is None:
            self.amt = broker.cash0 / self.slices
        if i % self.every == 0:
            broker.buy(price, min(self.amt, broker.cash))


class Grid(Strategy):
    """Grid trading — versi yang kita dah bina & sahkan."""
    name = "grid"

    def __init__(self, num_grids=5, stop_loss=None):
        self.ng = num_grids
        self.stop_loss = stop_loss

    def setup(self, warmup_prices):
        self.low, self.high = min(warmup_prices), max(warmup_prices)
        if self.high <= self.low:
            self.lines = None
            return
        self.lines = [self.low + (self.high - self.low) * k / self.ng
                      for k in range(self.ng + 1)]
        self.hold = [False] * self.ng
        self.btc_at = [0.0] * self.ng
        self.halted = False
        self.stop_level = (self.low * (1 - self.stop_loss)
                           if self.stop_loss else None)
        self.per = None

    def on_bar(self, i, price, prev_price, broker):
        if self.lines is None:
            return
        if self.per is None:
            self.per = broker.cash0 / self.ng

        if self.stop_level is not None:
            if not self.halted and price < self.stop_level:
                for k in range(self.ng):
                    if self.hold[k]:
                        if broker.sell(price, self.btc_at[k]):
                            self.hold[k] = False
                            self.btc_at[k] = 0.0
                self.halted = True
            elif self.halted and self.low <= price <= self.high:
                self.halted = False
        if self.halted:
            return

        for k in range(self.ng):
            lo, up = self.lines[k], self.lines[k + 1]
            if not self.hold[k] and prev_price > lo >= price:
                before = broker.btc
                if broker.buy(lo, min(self.per, broker.cash)):
                    self.hold[k] = True
                    self.btc_at[k] = broker.btc - before
            elif self.hold[k] and prev_price < up <= price:
                if broker.sell(up, self.btc_at[k]):
                    self.hold[k] = False
                    self.btc_at[k] = 0.0


class MACross(Strategy):
    """Momentum ringkas: beli bila MA pendek potong atas MA panjang."""
    name = "MA cross"

    def __init__(self, fast=10, slow=30):
        self.fast, self.slow = fast, slow
        self.hist = []
        self.invested = False

    def setup(self, warmup_prices):
        self.hist = list(warmup_prices)
        self.invested = False

    def on_bar(self, i, price, prev_price, broker):
        self.hist.append(price)
        if len(self.hist) < self.slow + 1:
            return
        f_now = statistics.mean(self.hist[-self.fast:])
        s_now = statistics.mean(self.hist[-self.slow:])
        f_prev = statistics.mean(self.hist[-self.fast - 1:-1])
        s_prev = statistics.mean(self.hist[-self.slow - 1:-1])
        if f_prev <= s_prev and f_now > s_now and not self.invested:
            if broker.buy(price, broker.cash):
                self.invested = True
        elif f_prev >= s_prev and f_now < s_now and self.invested:
            if broker.sell_all(price):
                self.invested = False


# --------------------------------------------------------------------------
# Enjin & metrik
# --------------------------------------------------------------------------

@dataclass
class Result:
    name: str = ""
    net: float = 0.0
    ret_pct: float = 0.0
    max_dd_pct: float = 0.0
    sharpe: float = 0.0
    trades: int = 0
    rejected: int = 0
    fees: float = 0.0
    equity: list = field(default_factory=list)


def run(prices, strategy, cash=350.0, fee=0.0035, min_order=0.0001, warmup=20,
        bars_per_year=365):
    """Jalankan satu strategi atas satu siri harga."""
    if len(prices) <= warmup + 1:
        return None
    broker = Broker(cash, fee, min_order)
    strategy.setup(prices[:warmup])
    curve = []
    traded = prices[warmup - 1:]
    for i in range(1, len(traded)):
        strategy.on_bar(i, traded[i], traded[i - 1], broker)
        curve.append(broker.equity(traded[i]))
    if not curve:
        return None

    peak, max_dd = curve[0], 0.0
    for v in curve:
        peak = max(peak, v)
        if peak > 0:
            max_dd = max(max_dd, (peak - v) / peak)

    rets = [curve[i] / curve[i - 1] - 1 for i in range(1, len(curve))
            if curve[i - 1] > 0]
    sharpe = 0.0
    if len(rets) > 2:
        sd = statistics.stdev(rets)
        if sd > 1e-12:
            sharpe = (statistics.mean(rets) / sd) * math.sqrt(bars_per_year)

    net = curve[-1] - cash
    return Result(
        name=strategy.name,
        net=net,
        ret_pct=net / cash * 100,
        max_dd_pct=max_dd * 100,
        sharpe=sharpe,
        trades=broker.buys + broker.sells,
        rejected=broker.rejected,
        fees=broker.fees_paid,
        equity=curve,
    )


# --------------------------------------------------------------------------
# Walk-forward: bahagian paling penting dalam fail ni
# --------------------------------------------------------------------------

def walk_forward(prices, factory, param_grid, train_days=240, test_days=120,
                 step=60, cash=350.0, fee=0.0035, min_order=0.0001, warmup=20,
                 bars_per_year=365):
    """
    Tala parameter atas tetingkap TRAIN, uji atas tetingkap TEST seterusnya
    yang strategi belum pernah nampak. Ulang sepanjang data.

    factory    : fungsi(params) -> Strategy
    param_grid : senarai dict parameter untuk dicuba

    Pulang senarai fold. Setiap fold ada nombor in-sample (IS) dan
    out-of-sample (OOS). Kalau IS cantik tapi OOS teruk, kau overfit.
    """
    folds = []
    start = 0
    while start + train_days + test_days <= len(prices):
        train = prices[start:start + train_days]
        test = prices[start + train_days - warmup:start + train_days + test_days]

        scored = []
        for params in param_grid:
            r = run(train, factory(**params), cash, fee, min_order, warmup,
                    bars_per_year)
            if r:
                scored.append((r.net, params, r))
        if not scored:
            start += step
            continue

        best_net, best_params, best_is = max(scored, key=lambda x: x[0])
        oos = run(test, factory(**best_params), cash, fee, min_order, warmup,
                  bars_per_year)
        # Benchmark atas tetingkap OOS yang SAMA. Strategi yang untung tapi
        # kalah pada buy & hold bukan edge — dia cuma versi lemah "beli je".
        bench = run(test, BuyHold(), cash, fee, min_order, warmup, bars_per_year)
        if oos and bench:
            folds.append({
                "params": best_params,
                "is_net": best_net,
                "is_ret": best_is.ret_pct,
                "oos_net": oos.net,
                "oos_ret": oos.ret_pct,
                "oos_dd": oos.max_dd_pct,
                "oos_trades": oos.trades,
                "bh_net": bench.net,
                "beat_bh": oos.net > bench.net,
            })
        start += step
    return folds


def report_walk_forward(folds, label=""):
    """Cetak keputusan walk-forward dengan amaran overfitting."""
    if not folds:
        print(f"{label}: tiada fold cukup data.")
        return

    is_nets = [f["is_net"] for f in folds]
    oos_nets = [f["oos_net"] for f in folds]
    is_mean = statistics.mean(is_nets)
    oos_mean = statistics.mean(oos_nets)
    win = 100 * sum(1 for x in oos_nets if x > 0) / len(oos_nets)

    print(f"\n{'=' * 68}")
    print(f"WALK-FORWARD: {label}   ({len(folds)} fold)")
    print(f"{'=' * 68}")
    bh_mean = statistics.mean(f["bh_net"] for f in folds)
    beat = sum(1 for f in folds if f["beat_bh"])

    print(f"{'fold':>4} {'parameter dipilih':<24} {'IS net':>9} {'OOS net':>9} "
          f"{'buy&hold':>9} {'menang':>7}")
    print("-" * 80)
    for i, f in enumerate(folds, 1):
        p = ", ".join(f"{k}={v}" for k, v in f["params"].items())
        print(f"{i:>4} {p:<24} {f['is_net']:>9.2f} {f['oos_net']:>9.2f} "
              f"{f['bh_net']:>9.2f} {'ya' if f['beat_bh'] else '-':>7}")
    print("-" * 80)
    print(f"     {'PURATA':<24} {is_mean:>9.2f} {oos_mean:>9.2f} {bh_mean:>9.2f}")
    print(f"\n  Fold OOS untung     : {win:.1f}%")
    print(f"  Jurang IS - OOS     : RM{is_mean - oos_mean:.2f}")
    print(f"  Kalahkan buy & hold : {beat}/{len(folds)} fold "
          f"({100 * beat / len(folds):.0f}%)")

    if oos_mean > 0 and oos_mean < bh_mean:
        print("\n  >> Untung OOS, TAPI kalah pada buy & hold. Strategi ni cuma")
        print("     tangkap sebahagian kenaikan BTC, dengan lebih banyak kerja")
        print("     dan lebih banyak fee. Bukan edge.")
    elif is_mean > 0 and oos_mean <= 0:
        print("\n  >> AMARAN OVERFIT: untung atas data lama, rugi atas data baru.")
        print("     Parameter tu jumpa bunyi bising, bukan edge.")
    elif oos_mean <= 0:
        print("\n  >> Strategi ni rugi atas data baru. Tiada edge dikesan.")
    elif is_mean - oos_mean > abs(oos_mean):
        print("\n  >> BERHATI-HATI: OOS positif tapi jauh lebih lemah dari IS.")
        print("     Sebahagian besar 'edge' tu kemungkinan overfit.")
    else:
        print("\n  >> OOS positif dan konsisten dengan IS. Ini yang kau cari.")
        print("     Sahkan dengan paper trading sebelum guna duit sebenar.")


# --------------------------------------------------------------------------
# Strategi tambahan
# --------------------------------------------------------------------------

def _rsi(prices, period):
    """RSI guna smoothing Wilder. Pulang None kalau data tak cukup."""
    if len(prices) < period + 1:
        return None
    gains = losses = 0.0
    for i in range(1, period + 1):
        ch = prices[i] - prices[i - 1]
        gains += max(ch, 0.0)
        losses += max(-ch, 0.0)
    ag, al = gains / period, losses / period
    for i in range(period + 1, len(prices)):
        ch = prices[i] - prices[i - 1]
        ag = (ag * (period - 1) + max(ch, 0.0)) / period
        al = (al * (period - 1) + max(-ch, 0.0)) / period
    if al == 0:
        return 100.0
    rs = ag / al
    return 100.0 - 100.0 / (1.0 + rs)


class RSIRevert(Strategy):
    """Mean reversion: beli bila oversold, jual bila overbought."""
    name = "RSI revert"

    def __init__(self, period=14, buy_below=30, sell_above=70):
        self.period, self.lo, self.hi = period, buy_below, sell_above

    def setup(self, warmup_prices):
        self.hist = list(warmup_prices)
        self.invested = False

    def on_bar(self, i, price, prev_price, broker):
        self.hist.append(price)
        r = _rsi(self.hist[-(self.period * 4):], self.period)
        if r is None:
            return
        if r < self.lo and not self.invested:
            if broker.buy(price, broker.cash):
                self.invested = True
        elif r > self.hi and self.invested:
            if broker.sell_all(price):
                self.invested = False


class BollingerRevert(Strategy):
    """Beli bawah band bawah, jual atas band atas."""
    name = "Bollinger revert"

    def __init__(self, period=20, k=2.0):
        self.period, self.k = period, k

    def setup(self, warmup_prices):
        self.hist = list(warmup_prices)
        self.invested = False

    def on_bar(self, i, price, prev_price, broker):
        self.hist.append(price)
        if len(self.hist) < self.period + 1:
            return
        win = self.hist[-self.period:]
        mid = statistics.mean(win)
        sd = statistics.stdev(win) if len(win) > 1 else 0.0
        if sd <= 0:
            return
        if price < mid - self.k * sd and not self.invested:
            if broker.buy(price, broker.cash):
                self.invested = True
        elif price > mid + self.k * sd and self.invested:
            if broker.sell_all(price):
                self.invested = False


class Donchian(Strategy):
    """Trend following: beli bila cecah harga tertinggi N hari, jual bila terendah."""
    name = "Donchian breakout"

    def __init__(self, entry=20, exit=10):
        self.entry, self.exit = entry, exit

    def setup(self, warmup_prices):
        self.hist = list(warmup_prices)
        self.invested = False

    def on_bar(self, i, price, prev_price, broker):
        if len(self.hist) >= self.entry:
            hi = max(self.hist[-self.entry:])
            lo = min(self.hist[-self.exit:]) if len(self.hist) >= self.exit else None
            if price > hi and not self.invested:
                if broker.buy(price, broker.cash):
                    self.invested = True
            elif lo is not None and price < lo and self.invested:
                if broker.sell_all(price):
                    self.invested = False
        self.hist.append(price)


class BuyTheDip(Strategy):
    """Beli bila jatuh X% dari puncak N hari, jual bila naik balik Y%."""
    name = "buy the dip"

    def __init__(self, lookback=30, dip=0.10, take_profit=0.10):
        self.lookback, self.dip, self.tp = lookback, dip, take_profit

    def setup(self, warmup_prices):
        self.hist = list(warmup_prices)
        self.entry_price = None

    def on_bar(self, i, price, prev_price, broker):
        self.hist.append(price)
        peak = max(self.hist[-self.lookback:])
        if self.entry_price is None:
            if peak > 0 and price <= peak * (1 - self.dip):
                if broker.buy(price, broker.cash):
                    self.entry_price = price
        else:
            if price >= self.entry_price * (1 + self.tp):
                if broker.sell_all(price):
                    self.entry_price = None


class Rebalance(Strategy):
    """
    Volatility harvesting: kekalkan nisbah tetap BTC/tunai.
    Harga naik -> jual sikit. Harga turun -> beli sikit.
    Ini versi 'pintar' grid, tanpa andaian range tetap.
    """
    name = "rebalance 50/50"

    def __init__(self, target=0.5, band=0.05):
        self.target, self.band = target, band

    def setup(self, warmup_prices):
        self.started = False

    def on_bar(self, i, price, prev_price, broker):
        eq = broker.equity(price)
        if eq <= 0:
            return
        if not self.started:
            broker.buy(price, broker.cash * self.target)
            self.started = True
            return
        w = (broker.btc * price) / eq
        if w > self.target + self.band:
            excess_myr = (w - self.target) * eq
            broker.sell(price, excess_myr / price)
        elif w < self.target - self.band:
            short_myr = (self.target - w) * eq
            broker.buy(price, min(short_myr, broker.cash))


def _ema_series(prices, period):
    k = 2.0 / (period + 1)
    out, e = [], prices[0]
    for p in prices:
        e = p * k + e * (1 - k)
        out.append(e)
    return out


class MACD(Strategy):
    """Beli bila MACD potong atas garis signal, jual bila potong bawah."""
    name = "MACD"

    def __init__(self, fast=12, slow=26, signal=9):
        self.fast, self.slow, self.signal = fast, slow, signal

    def setup(self, warmup_prices):
        self.hist = list(warmup_prices)
        self.invested = False

    def on_bar(self, i, price, prev_price, broker):
        self.hist.append(price)
        if len(self.hist) < self.slow + self.signal + 2:
            return
        h = self.hist[-(self.slow + self.signal) * 3:]
        macd = [f - s for f, s in zip(_ema_series(h, self.fast),
                                      _ema_series(h, self.slow))]
        sig = _ema_series(macd, self.signal)
        if macd[-2] <= sig[-2] and macd[-1] > sig[-1] and not self.invested:
            if broker.buy(price, broker.cash):
                self.invested = True
        elif macd[-2] >= sig[-2] and macd[-1] < sig[-1] and self.invested:
            if broker.sell_all(price):
                self.invested = False


def independent_windows(prices, size=120):
    """
    Tetingkap TIDAK bertindih — sampel bebas sebenar.

    Tetingkap bertindih (step kecil) nampak macam banyak data, tapi
    sebenarnya data yang sama dikira berulang kali. Dia buat keputusan
    nampak lebih meyakinkan dari sepatutnya. Sentiasa semak berapa
    sampel BEBAS kau ada sebelum percaya apa-apa nombor.
    """
    return [prices[i:i + size] for i in range(0, len(prices) - size + 1, size)]
