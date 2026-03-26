"""Thread-safe in-memory price cache."""

import threading

from .models import PriceUpdate


class PriceCache:
    """Shared store for latest prices.

    Written by the market data background task.
    Read by SSE streams and REST endpoints.
    """

    def __init__(self):
        self._prices: dict[str, PriceUpdate] = {}
        self._lock = threading.Lock()

    def update(self, price: PriceUpdate) -> None:
        """Update a single ticker's price."""
        with self._lock:
            self._prices[price.ticker] = price

    def update_batch(self, prices: list[PriceUpdate]) -> None:
        """Update multiple tickers atomically."""
        with self._lock:
            for p in prices:
                self._prices[p.ticker] = p

    def get(self, ticker: str) -> PriceUpdate | None:
        """Get latest price for a ticker."""
        with self._lock:
            return self._prices.get(ticker)

    def get_all(self) -> dict[str, PriceUpdate]:
        """Get snapshot of all latest prices."""
        with self._lock:
            return dict(self._prices)

    def remove(self, ticker: str) -> None:
        """Remove a ticker from the cache."""
        with self._lock:
            self._prices.pop(ticker, None)
