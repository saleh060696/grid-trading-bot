# Grid Trading Research — BTC/MYR

Kajian sama ada trading bot boleh untung pada modal RM350 di Luno Malaysia.
Jawapan setakat ini: tiada strategi yang diuji mengalahkan buy & hold selepas kos.
Paper trading sedang berjalan untuk mengesahkan pada data hadapan.

## Fail

| fail | guna |
|---|---|
| `paper_trade.py` | Paper trading LIVE — 10 strategi serentak. Tak execute apa-apa. |
| `research.py` | Enjin backtest: Broker, 11 strategi, walk-forward, metrik |
| `compare.py` | Jalankan semua strategi + walk-forward atas data sejarah |
| `fetch_data.py` | Tarik sejarah BTC dari Binance (tiada API key) |
| `grid_backtest.py` | Backtest grid khusus, guna data XBTMYR sebenar (perlu API key Luno) |
| `orderbook.py` | Model pengisian sedar-barisan — adakah limit order kita betul-betul terisi |
| `record_book.py` | Rekod orderbook XBTMYR sebenar untuk jawab soalan itu |

## Arahan harian

```bash
cd ~/grid-trading-bot

.venv/bin/python paper_trade.py status      # papan pendahulu semasa
tail -20 paper_cron.log                     # cron berjalan elok?
```

## Paper trading

Berjalan automatik setiap 10 minit melalui cron. Tiada API key. Tiada duit bergerak.

```bash
crontab -l                                  # tengok jadual
crontab -r                                  # BERHENTI sepenuhnya
.venv/bin/python paper_trade.py tick        # jalankan manual sekali
```

Mulakan semula dengan strategi lain (sejarah sedia ada HILANG):

```bash
.venv/bin/python paper_trade.py init --strategies rebalance,grid,donchian --force
```

Pilihan: `buyhold, rebalance, grid, grid_stop, donchian, macross, macd, rsi,
bollinger, dip`. `buyhold` sentiasa dimasukkan sebagai penanda aras.

## Analisis pengisian orderbook

Semua backtest andaikan limit order terisi sebaik harga sentuh aras kita.
Itu tak benar — kita beratur di belakang order lain. `record_book.py`
kumpul data sebenar untuk mengukur jurang tu. Berjalan automatik setiap
5 minit melalui cron.

```bash
.venv/bin/python record_book.py --analyse
```

Kalau nisbah bawah 1.0, limit order kita duduk TAK TERISI walaupun harga
sentuh aras — bermakna fee maker 0.35% yang diandaikan seluruh projek ni
tak sah, dan hasil backtest terlebih optimis.

## Backtest sejarah

```bash
.venv/bin/python fetch_data.py 1h           # atau 1d, 4h
.venv/bin/python compare.py 1h 720          # timeframe, saiz tetingkap (bar)
```

## Uji idea sendiri

Tambah kelas dalam `research.py`:

```python
class IdeaAku(Strategy):
    name = "idea aku"

    def setup(self, warmup_prices):
        self.hist = list(warmup_prices)

    def on_bar(self, i, price, prev_price, broker):
        self.hist.append(price)
        if <syarat beli>:
            broker.buy(price, broker.cash * 0.25)
        elif <syarat jual>:
            broker.sell_all(price)
```

`broker` tolak sendiri order bawah minimum Luno (0.0001 BTC) dan bila tunai
tak cukup — jadi backtest tak boleh tunjuk trade yang mustahil.

Daftar dalam `STRATEGIES` (paper_trade.py) dan `BUILDERS` (compare.py).

## Peraturan yang datang dari data, bukan teori

1. **Baca lajur OOS sahaja.** In-sample sentiasa nampak cantik. MA cross
   pernah tunjuk IS +RM138 dan OOS −RM9.72 pada fold yang sama.
2. **Banding dengan buy & hold, bukan dengan sifar.** Donchian untung
   OOS +RM21.54 tapi buy & hold buat +RM65.89 atas tetingkap yang sama.
   Untung tapi kalah penanda aras bukan edge.
3. **Kira sampel BEBAS, bukan tetingkap bertindih.** 121 tetingkap
   bertindih atas 2 tahun data sebenarnya cuma ~6 keping data bebas.
4. **Semak lajur `tolak`.** DCA pernah nampak jadi pemenang sebab 11 daripada
   14 ordernya ditolak — ia duduk atas tunai, bukan strategi.
5. **Trade lebih = untung kurang.** Korelasi bilangan trade lawan pulangan
   atas 110 sampel bebas: **−0.946**.
6. **Sentuh bukan bermakna terisi.** Limit order beratur di belakang order
   lain. Semak `record_book.py --analyse` sebelum percaya mana-mana hasil
   yang andaikan fee maker.

## Kos sebenar (semak semula kalau berubah)

- Fee Luno Malaysia, volum 30-hari < RM5,000: maker **0.35%**, taker **0.6%**
- Round trip maker = 0.7%. Bot mesti guna limit order sahaja.
- Saiz order minimum XBTMYR: **0.0001 BTC** (~RM32)
- Pada RM350, itu hadkan kau ~10 selang grid sebelum order ditolak
