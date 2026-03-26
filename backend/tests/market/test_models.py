"""Unit tests for market data models."""

from datetime import datetime

import pytest

from app.market.models import Direction, PriceUpdate, TickerConfig


class TestDirection:
    def test_values(self):
        assert Direction.UP.value == "up"
        assert Direction.DOWN.value == "down"
        assert Direction.UNCHANGED.value == "unchanged"


class TestPriceUpdate:
    def test_direction_up(self):
        update = PriceUpdate(ticker="AAPL", price=191.0, previous_price=190.0, timestamp=datetime.now())
        assert update.direction == Direction.UP

    def test_direction_down(self):
        update = PriceUpdate(ticker="AAPL", price=189.0, previous_price=190.0, timestamp=datetime.now())
        assert update.direction == Direction.DOWN

    def test_direction_unchanged(self):
        update = PriceUpdate(ticker="AAPL", price=190.0, previous_price=190.0, timestamp=datetime.now())
        assert update.direction == Direction.UNCHANGED

    def test_to_sse_dict_keys(self):
        now = datetime.now()
        update = PriceUpdate(ticker="AAPL", price=191.0, previous_price=190.0, timestamp=now)
        d = update.to_sse_dict()
        assert set(d.keys()) == {"ticker", "price", "previousPrice", "direction", "timestamp"}

    def test_to_sse_dict_values(self):
        now = datetime.now()
        update = PriceUpdate(ticker="MSFT", price=420.0, previous_price=419.0, timestamp=now)
        d = update.to_sse_dict()
        assert d["ticker"] == "MSFT"
        assert d["price"] == 420.0
        assert d["previousPrice"] == 419.0
        assert d["direction"] == "up"
        assert d["timestamp"] == now.isoformat()

    def test_to_sse_dict_direction_down(self):
        update = PriceUpdate(ticker="TSLA", price=249.0, previous_price=250.0, timestamp=datetime.now())
        assert update.to_sse_dict()["direction"] == "down"

    def test_to_sse_dict_direction_unchanged(self):
        update = PriceUpdate(ticker="V", price=280.0, previous_price=280.0, timestamp=datetime.now())
        assert update.to_sse_dict()["direction"] == "unchanged"

    def test_ticker_stored_as_given(self):
        update = PriceUpdate(ticker="aapl", price=190.0, previous_price=189.0, timestamp=datetime.now())
        assert update.ticker == "aapl"

    def test_price_is_float(self):
        update = PriceUpdate(ticker="AAPL", price=190.5, previous_price=190.0, timestamp=datetime.now())
        assert isinstance(update.price, float)


class TestTickerConfig:
    def test_fields(self):
        config = TickerConfig(seed_price=100.0, annual_drift=0.08, annual_volatility=0.25)
        assert config.seed_price == 100.0
        assert config.annual_drift == 0.08
        assert config.annual_volatility == 0.25
