"""Unit tests for the PriceCache."""

import threading
from datetime import datetime

import pytest

from app.market.cache import PriceCache
from app.market.models import PriceUpdate


def make_update(ticker: str, price: float, prev: float = 0.0) -> PriceUpdate:
    return PriceUpdate(ticker=ticker, price=price, previous_price=prev, timestamp=datetime.now())


class TestPriceCacheBasicOps:
    def test_get_missing_returns_none(self):
        cache = PriceCache()
        assert cache.get("AAPL") is None

    def test_update_and_get(self):
        cache = PriceCache()
        update = make_update("AAPL", 190.0, 189.0)
        cache.update(update)
        assert cache.get("AAPL") is update

    def test_update_overwrites(self):
        cache = PriceCache()
        cache.update(make_update("AAPL", 190.0))
        new = make_update("AAPL", 191.0)
        cache.update(new)
        assert cache.get("AAPL") is new

    def test_remove_existing(self):
        cache = PriceCache()
        cache.update(make_update("AAPL", 190.0))
        cache.remove("AAPL")
        assert cache.get("AAPL") is None

    def test_remove_nonexistent_is_safe(self):
        cache = PriceCache()
        cache.remove("AAPL")  # should not raise

    def test_get_all_empty(self):
        cache = PriceCache()
        assert cache.get_all() == {}

    def test_get_all_returns_copy(self):
        cache = PriceCache()
        cache.update(make_update("AAPL", 190.0))
        result = cache.get_all()
        result["AAPL"] = None  # mutate the returned dict
        assert cache.get("AAPL") is not None  # original unchanged

    def test_update_batch(self):
        cache = PriceCache()
        updates = [
            make_update("AAPL", 190.0),
            make_update("GOOGL", 175.0),
            make_update("MSFT", 420.0),
        ]
        cache.update_batch(updates)
        assert cache.get("AAPL") is updates[0]
        assert cache.get("GOOGL") is updates[1]
        assert cache.get("MSFT") is updates[2]

    def test_update_batch_empty_list(self):
        cache = PriceCache()
        cache.update_batch([])  # should not raise
        assert cache.get_all() == {}

    def test_get_all_contains_all_updated(self):
        cache = PriceCache()
        updates = [make_update("AAPL", 190.0), make_update("TSLA", 250.0)]
        cache.update_batch(updates)
        all_prices = cache.get_all()
        assert "AAPL" in all_prices
        assert "TSLA" in all_prices
        assert len(all_prices) == 2


class TestPriceCacheThreadSafety:
    def test_concurrent_writes_do_not_raise(self):
        cache = PriceCache()
        errors = []

        def writer(ticker: str):
            try:
                for i in range(100):
                    cache.update(make_update(ticker, float(i)))
            except Exception as e:
                errors.append(e)

        threads = [threading.Thread(target=writer, args=(f"T{i}",)) for i in range(10)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert errors == []

    def test_concurrent_reads_and_writes(self):
        cache = PriceCache()
        cache.update(make_update("AAPL", 190.0))
        errors = []

        def writer():
            try:
                for i in range(50):
                    cache.update(make_update("AAPL", float(190 + i)))
            except Exception as e:
                errors.append(e)

        def reader():
            try:
                for _ in range(50):
                    cache.get("AAPL")
                    cache.get_all()
            except Exception as e:
                errors.append(e)

        threads = (
            [threading.Thread(target=writer) for _ in range(3)]
            + [threading.Thread(target=reader) for _ in range(3)]
        )
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert errors == []
