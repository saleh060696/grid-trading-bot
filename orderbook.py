"""
Model Pengisian Sedar-Orderbook
================================

Masalah yang alat ni selesaikan:

Semua backtest dalam projek ni andaikan limit order terisi sebaik sahaja
harga sentuh aras kita. Itu TAK BENAR. Dalam realiti kau beratur di
belakang order lain pada harga yang sama, dan kau hanya terisi selepas
volum di depan kau habis dimakan.

Kalau andaian tu salah, setiap nombor dalam projek ni terlebih optimis —
bukan sedikit, tapi secara sistematik, sebab kita andaikan fee maker
(0.35%) yang hanya sah kalau limit order kita betul-betul terisi.

Model ni guna data AWAM Luno sahaja. Tiada API key. Tiada duit.

Mekanik keutamaan harga-masa:

  Limit BELI pada harga P terisi bila:
    1. Ada dagangan pada harga BAWAH P
       -> aras kita disapu sepenuhnya, kita terisi; atau
    2. Volum jualan kumulatif TEPAT pada P melebihi volum yang
       beratur di depan kita masa order diletak.

  Limit JUAL adalah cerminannya.
"""

import json
import time
import urllib.request
from dataclasses import dataclass, field

BASE = "https://api.luno.com/api/1"
PAIR = "XBTMYR"


def _get(url):
    req = urllib.request.Request(url, headers={"User-Agent": "research/1.0"})
    with urllib.request.urlopen(req, timeout=20) as r:
        return json.load(r)


def fetch_book(pair=PAIR):
    """Orderbook penuh (100 aras setiap sisi). Pulang harga menurun untuk
    bid, menaik untuk ask — iaitu terbaik dahulu."""
    d = _get(f"{BASE}/orderbook_top?pair={pair}")
    bids = [(float(x["price"]), float(x["volume"])) for x in d["bids"]]
    asks = [(float(x["price"]), float(x["volume"])) for x in d["asks"]]
    bids.sort(key=lambda x: -x[0])
    asks.sort(key=lambda x: x[0])
    return {"ts": int(d["timestamp"]), "bids": bids, "asks": asks}


def fetch_trades(pair=PAIR, since_ms=None):
    """Dagangan terkini, disusun MENAIK ikut masa (terlama dahulu)."""
    url = f"{BASE}/trades?pair={pair}"
    if since_ms is not None:
        url += f"&since={int(since_ms)}"
    d = _get(url)
    t = [{"ts": int(x["timestamp"]), "price": float(x["price"]),
          "volume": float(x["volume"]), "is_buy": bool(x["is_buy"])}
         for x in d.get("trades", [])]
    t.sort(key=lambda x: x["ts"])
    return t


def queue_ahead(book, side, price):
    """
    Volum yang ada keutamaan lebih tinggi dari order baru pada 'price'.

    Untuk BELI: semua bid pada harga LEBIH BAIK (lebih tinggi), campur
    apa yang sedia ada tepat pada harga kita.
    """
    total = 0.0
    if side == "buy":
        for p, v in book["bids"]:
            if p > price or abs(p - price) < 1e-9:
                total += v
    else:
        for p, v in book["asks"]:
            if p < price or abs(p - price) < 1e-9:
                total += v
    return total


@dataclass
class LimitOrder:
    side: str                  # "buy" atau "sell"
    price: float
    volume: float
    placed_ts: int
    queue_ahead: float
    consumed: float = 0.0      # volum dimakan di depan kita sejak diletak
    filled: float = 0.0
    swept: bool = False        # aras kita dilepasi sepenuhnya
    fill_ts: int = None

    @property
    def done(self):
        return self.filled >= self.volume - 1e-12


class QueueFillModel:
    """Jejak limit order terbuka dan isi mengikut aliran dagangan sebenar."""

    def __init__(self):
        self.open = []
        self.fills = []

    def place(self, book, side, price, volume, ts=None):
        o = LimitOrder(side=side, price=price, volume=volume,
                       placed_ts=ts if ts is not None else int(time.time() * 1000),
                       queue_ahead=queue_ahead(book, side, price))
        self.open.append(o)
        return o

    def process(self, trades):
        """Suapkan dagangan mengikut urutan masa. Pulang order yang terisi."""
        newly = []
        for t in trades:
            for o in list(self.open):
                if t["ts"] <= o.placed_ts or o.done:
                    continue
                if o.side == "buy":
                    # Jualan agresif memakan sebelah bid.
                    if t["is_buy"]:
                        continue
                    if t["price"] < o.price - 1e-9:
                        o.swept = True
                        o.filled = o.volume          # aras kita dilepasi
                        o.fill_ts = t["ts"]
                    elif abs(t["price"] - o.price) < 1e-9:
                        o.consumed += t["volume"]
                        extra = o.consumed - o.queue_ahead
                        if extra > 0:
                            o.filled = min(o.volume, extra)
                            if o.done:
                                o.fill_ts = t["ts"]
                else:
                    # Belian agresif memakan sebelah ask.
                    if not t["is_buy"]:
                        continue
                    if t["price"] > o.price + 1e-9:
                        o.swept = True
                        o.filled = o.volume
                        o.fill_ts = t["ts"]
                    elif abs(t["price"] - o.price) < 1e-9:
                        o.consumed += t["volume"]
                        extra = o.consumed - o.queue_ahead
                        if extra > 0:
                            o.filled = min(o.volume, extra)
                            if o.done:
                                o.fill_ts = t["ts"]
                if o.done:
                    self.open.remove(o)
                    self.fills.append(o)
                    newly.append(o)
        return newly

    def touched_but_unfilled(self, trades):
        """
        Order di mana harga SENTUH aras kita tapi kita tak terisi.

        Ini tepat jurang yang backtest kita abaikan: model lama akan
        kira semua ni sebagai untung.
        """
        out = []
        for o in self.open:
            for t in trades:
                if t["ts"] <= o.placed_ts:
                    continue
                if abs(t["price"] - o.price) < 1e-9:
                    out.append(o)
                    break
        return out
