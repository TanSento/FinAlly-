"""Unit and integration tests for the SimulatorDataSource."""

import asyncio
import math
from datetime import datetime

import numpy as np
import pytest

from app.market.cache import PriceCache
from app.market.simulator import (
    DEFAULT_CONFIG,
    DT,
    EVENT_MAGNITUDE_MAX,
    EVENT_MAGNITUDE_MIN,
    FINANCE,
    TECH,
    TICKER_CONFIGS,
    SimulatorDataSource,
    apply_random_event,
    build_correlation_matrix,
    gbm_step,
    generate_correlated_normals,
    random_seed_price,
)


class TestGbmStep:
    def test_positive_output(self):
        """GBM always produces a positive price."""
        assert gbm_step(100.0, drift=0.10, volatility=0.25, z=0.0) > 0

    def test_extreme_negative_z(self):
        """Even with very negative noise, price stays positive."""
        price = gbm_step(100.0, drift=0.10, volatility=0.25, z=-10.0)
        assert price > 0

    def test_extreme_positive_z(self):
        """Very positive noise gives a much higher price."""
        price = gbm_step(100.0, drift=0.10, volatility=0.25, z=10.0)
        assert price > 100.0

    def test_zero_z_drift(self):
        """With z=0, price moves only by drift component."""
        drift = 0.10
        vol = 0.25
        exponent = (drift - 0.5 * vol**2) * DT
        expected = 100.0 * math.exp(exponent)
        result = gbm_step(100.0, drift=drift, volatility=vol, z=0.0)
        assert abs(result - expected) < 1e-10

    def test_scales_with_price(self):
        """Higher base prices give proportionally larger absolute moves."""
        low = gbm_step(10.0, drift=0.10, volatility=0.25, z=1.0)
        high = gbm_step(1000.0, drift=0.10, volatility=0.25, z=1.0)
        # Ratio of new prices should equal ratio of old prices (multiplicative)
        assert abs(high / low - 1000.0 / 10.0) < 1e-6


class TestRandomSeedPrice:
    def test_in_range(self):
        for _ in range(100):
            price = random_seed_price()
            assert 20.0 <= price <= 200.0

    def test_is_rounded(self):
        for _ in range(50):
            price = random_seed_price()
            assert round(price, 2) == price


class TestApplyRandomEvent:
    def test_returns_float(self):
        result = apply_random_event(100.0)
        assert isinstance(result, float)

    def test_price_changes_or_stays(self):
        # Just verify it returns a valid positive price
        result = apply_random_event(100.0)
        assert result > 0

    def test_event_magnitude_within_bounds(self):
        # With probability 1, an event should be within 5% of original
        # We can verify by running many iterations and checking max deviation
        original = 100.0
        max_deviation = 0.0
        for _ in range(1000):
            result = apply_random_event(original)
            deviation = abs(result - original) / original
            max_deviation = max(max_deviation, deviation)
        # Max event magnitude is EVENT_MAGNITUDE_MAX (0.05 = 5%)
        assert max_deviation <= EVENT_MAGNITUDE_MAX + 1e-9


class TestBuildCorrelationMatrix:
    def test_identity_single_ticker(self):
        corr = build_correlation_matrix(["AAPL"])
        assert corr.shape == (1, 1)
        assert corr[0, 0] == 1.0

    def test_symmetric(self):
        tickers = ["AAPL", "GOOGL", "JPM", "TSLA"]
        corr = build_correlation_matrix(tickers)
        assert np.allclose(corr, corr.T)

    def test_diagonal_ones(self):
        tickers = ["AAPL", "MSFT", "JPM"]
        corr = build_correlation_matrix(tickers)
        assert np.allclose(np.diag(corr), 1.0)

    def test_tech_tech_correlation(self):
        tickers = ["AAPL", "GOOGL"]
        corr = build_correlation_matrix(tickers)
        assert corr[0, 1] == 0.6
        assert corr[1, 0] == 0.6

    def test_finance_finance_correlation(self):
        tickers = ["JPM", "V"]
        corr = build_correlation_matrix(tickers)
        assert corr[0, 1] == 0.5

    def test_tech_finance_correlation(self):
        tickers = ["AAPL", "JPM"]
        corr = build_correlation_matrix(tickers)
        assert corr[0, 1] == 0.3

    def test_unknown_ticker_correlation(self):
        tickers = ["AAPL", "XYZ"]  # XYZ is unknown
        corr = build_correlation_matrix(tickers)
        assert corr[0, 1] == 0.2  # unknown -> mild correlation

    def test_positive_definite(self):
        tickers = ["AAPL", "GOOGL", "MSFT", "JPM", "V", "TSLA"]
        corr = build_correlation_matrix(tickers)
        eigenvalues = np.linalg.eigvalsh(corr)
        assert all(ev > 0 for ev in eigenvalues)

    def test_all_default_tickers(self):
        tickers = list(TICKER_CONFIGS.keys())
        corr = build_correlation_matrix(tickers)
        assert corr.shape == (len(tickers), len(tickers))
        eigenvalues = np.linalg.eigvalsh(corr)
        assert all(ev > 0 for ev in eigenvalues)


class TestGenerateCorrelatedNormals:
    def test_output_shape(self):
        corr = build_correlation_matrix(["AAPL", "GOOGL", "JPM"])
        z = generate_correlated_normals(corr)
        assert z.shape == (3,)

    def test_single_ticker(self):
        corr = build_correlation_matrix(["AAPL"])
        z = generate_correlated_normals(corr)
        assert z.shape == (1,)

    def test_returns_floats(self):
        corr = build_correlation_matrix(["AAPL", "MSFT"])
        z = generate_correlated_normals(corr)
        assert z.dtype in (np.float64, np.float32)


class TestTickerConfigs:
    def test_all_ten_default_tickers_present(self):
        expected = {"AAPL", "GOOGL", "MSFT", "AMZN", "TSLA", "NVDA", "META", "JPM", "V", "NFLX"}
        assert set(TICKER_CONFIGS.keys()) == expected

    def test_all_seed_prices_positive(self):
        for ticker, config in TICKER_CONFIGS.items():
            assert config.seed_price > 0, f"{ticker} seed price must be positive"

    def test_all_volatilities_positive(self):
        for ticker, config in TICKER_CONFIGS.items():
            assert config.annual_volatility > 0, f"{ticker} volatility must be positive"

    def test_default_config_has_sensible_values(self):
        assert DEFAULT_CONFIG.annual_drift > 0
        assert DEFAULT_CONFIG.annual_volatility > 0


class TestSimulatorDataSourceUnit:
    def test_add_known_ticker_uses_seed_price(self):
        cache = PriceCache()
        sim = SimulatorDataSource(price_cache=cache)
        sim._add_ticker_internal("AAPL")
        assert sim._prices["AAPL"] == TICKER_CONFIGS["AAPL"].seed_price

    def test_add_unknown_ticker_random_price(self):
        cache = PriceCache()
        sim = SimulatorDataSource(price_cache=cache)
        sim._add_ticker_internal("ZZZZ")
        assert 20.0 <= sim._prices["ZZZZ"] <= 200.0

    def test_rebuild_correlation_single_ticker(self):
        cache = PriceCache()
        sim = SimulatorDataSource(price_cache=cache)
        sim._add_ticker_internal("AAPL")
        sim._rebuild_correlation()
        assert sim._corr_matrix is not None
        assert sim._corr_matrix.shape == (1, 1)

    def test_rebuild_correlation_empty(self):
        cache = PriceCache()
        sim = SimulatorDataSource(price_cache=cache)
        sim._rebuild_correlation()
        assert sim._corr_matrix is None

    def test_tick_updates_cache(self):
        cache = PriceCache()
        sim = SimulatorDataSource(price_cache=cache)
        sim._add_ticker_internal("AAPL")
        sim._add_ticker_internal("MSFT")
        sim._rebuild_correlation()
        sim._tick()
        assert cache.get("AAPL") is not None
        assert cache.get("MSFT") is not None

    def test_tick_prices_are_positive(self):
        cache = PriceCache()
        sim = SimulatorDataSource(price_cache=cache)
        for ticker in TICKER_CONFIGS:
            sim._add_ticker_internal(ticker)
        sim._rebuild_correlation()
        for _ in range(10):
            sim._tick()
        for ticker in TICKER_CONFIGS:
            update = cache.get(ticker)
            assert update is not None
            assert update.price >= 0.01

    def test_tick_no_op_when_empty(self):
        cache = PriceCache()
        sim = SimulatorDataSource(price_cache=cache)
        sim._tick()  # should not raise
        assert cache.get_all() == {}

    def test_get_latest_returns_none_before_start(self):
        cache = PriceCache()
        sim = SimulatorDataSource(price_cache=cache)
        assert sim.get_latest("AAPL") is None

    def test_get_all_latest_returns_empty_before_start(self):
        cache = PriceCache()
        sim = SimulatorDataSource(price_cache=cache)
        assert sim.get_all_latest() == {}


class TestSimulatorDataSourceAsync:
    @pytest.mark.asyncio
    async def test_start_creates_task(self):
        cache = PriceCache()
        sim = SimulatorDataSource(price_cache=cache, update_interval=0.1)
        await sim.start(["AAPL"])
        assert sim._task is not None
        await sim.stop()

    @pytest.mark.asyncio
    async def test_produces_updates_after_start(self):
        cache = PriceCache()
        sim = SimulatorDataSource(price_cache=cache, update_interval=0.05)
        await sim.start(["AAPL", "GOOGL"])
        await asyncio.sleep(0.2)
        assert cache.get("AAPL") is not None
        assert cache.get("GOOGL") is not None
        await sim.stop()

    @pytest.mark.asyncio
    async def test_stop_cancels_task(self):
        cache = PriceCache()
        sim = SimulatorDataSource(price_cache=cache, update_interval=0.1)
        await sim.start(["AAPL"])
        await sim.stop()
        assert sim._task.cancelled() or sim._task.done()

    @pytest.mark.asyncio
    async def test_add_ticker_after_start(self):
        cache = PriceCache()
        sim = SimulatorDataSource(price_cache=cache, update_interval=0.05)
        await sim.start(["AAPL"])
        await asyncio.sleep(0.1)

        await sim.add_ticker("TSLA")
        await asyncio.sleep(0.15)

        assert cache.get("TSLA") is not None
        await sim.stop()

    @pytest.mark.asyncio
    async def test_add_ticker_idempotent(self):
        cache = PriceCache()
        sim = SimulatorDataSource(price_cache=cache, update_interval=0.1)
        await sim.start(["AAPL"])
        await sim.add_ticker("AAPL")  # already present
        assert sim._tickers.count("AAPL") == 1  # not duplicated
        await sim.stop()

    @pytest.mark.asyncio
    async def test_remove_ticker_clears_cache(self):
        cache = PriceCache()
        sim = SimulatorDataSource(price_cache=cache, update_interval=0.05)
        await sim.start(["AAPL", "TSLA"])
        await asyncio.sleep(0.15)
        assert cache.get("TSLA") is not None

        await sim.remove_ticker("TSLA")
        assert cache.get("TSLA") is None
        await sim.stop()

    @pytest.mark.asyncio
    async def test_remove_nonexistent_ticker_is_safe(self):
        cache = PriceCache()
        sim = SimulatorDataSource(price_cache=cache, update_interval=0.1)
        await sim.start(["AAPL"])
        await sim.remove_ticker("ZZZZ")  # should not raise
        await sim.stop()

    @pytest.mark.asyncio
    async def test_get_latest_after_tick(self):
        cache = PriceCache()
        sim = SimulatorDataSource(price_cache=cache, update_interval=0.05)
        await sim.start(["MSFT"])
        await asyncio.sleep(0.15)
        update = sim.get_latest("MSFT")
        assert update is not None
        assert update.ticker == "MSFT"
        assert update.price > 0
        await sim.stop()

    @pytest.mark.asyncio
    async def test_uppercase_normalization(self):
        cache = PriceCache()
        sim = SimulatorDataSource(price_cache=cache, update_interval=0.05)
        await sim.start(["aapl"])  # lowercase input
        await asyncio.sleep(0.15)
        assert cache.get("AAPL") is not None  # stored as uppercase
        await sim.stop()

    @pytest.mark.asyncio
    async def test_stop_is_idempotent(self):
        cache = PriceCache()
        sim = SimulatorDataSource(price_cache=cache, update_interval=0.1)
        await sim.start(["AAPL"])
        await sim.stop()
        await sim.stop()  # second stop should not raise
