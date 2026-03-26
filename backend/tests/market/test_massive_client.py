"""Unit tests for the MassiveDataSource (Polygon.io client)."""

import asyncio
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from app.market.cache import PriceCache
from app.market.massive_client import DEFAULT_POLL_INTERVAL, MassiveDataSource
from app.market.models import Direction, PriceUpdate


# --- Sample Massive API response fixture ---

SAMPLE_RESPONSE = {
    "count": 2,
    "status": "OK",
    "tickers": [
        {
            "ticker": "AAPL",
            "todaysChange": 1.23,
            "todaysChangePerc": 0.65,
            "updated": 1605192894630916600,  # nanoseconds
            "day": {"o": 189.5, "h": 191.2, "l": 188.9, "c": 190.73, "v": 52341000, "vw": 190.12},
            "prevDay": {"o": 188.0, "h": 189.8, "l": 187.5, "c": 189.50, "v": 48200000, "vw": 188.9},
            "min": {"o": 190.6, "h": 190.8, "l": 190.55, "c": 190.73, "v": 125000, "vw": 190.68},
        },
        {
            "ticker": "GOOGL",
            "todaysChange": -0.50,
            "todaysChangePerc": -0.29,
            "updated": 1605192894630916600,
            "day": {"o": 174.0, "h": 176.0, "l": 173.5, "c": 174.50, "v": 20000000, "vw": 174.80},
            "prevDay": {"o": 173.0, "h": 175.5, "l": 172.8, "c": 175.00, "v": 18000000, "vw": 174.20},
            "min": None,  # min data absent — should fall back to day close
        },
    ],
}


class TestMassiveDataSourceParsing:
    def _make_source(self) -> MassiveDataSource:
        return MassiveDataSource(api_key="test-key", price_cache=PriceCache())

    def test_parse_uses_min_close_when_present(self):
        source = self._make_source()
        updates = source._parse_snapshots(SAMPLE_RESPONSE)
        aapl = next(u for u in updates if u.ticker == "AAPL")
        assert aapl.price == 190.73  # min.c

    def test_parse_falls_back_to_day_close(self):
        source = self._make_source()
        updates = source._parse_snapshots(SAMPLE_RESPONSE)
        googl = next(u for u in updates if u.ticker == "GOOGL")
        assert googl.price == 174.50  # day.c (min is None)

    def test_parse_uses_prev_day_close_as_previous_price(self):
        source = self._make_source()
        updates = source._parse_snapshots(SAMPLE_RESPONSE)
        aapl = next(u for u in updates if u.ticker == "AAPL")
        assert aapl.previous_price == 189.50  # prevDay.c

    def test_parse_timestamp_from_nanoseconds(self):
        source = self._make_source()
        updates = source._parse_snapshots(SAMPLE_RESPONSE)
        aapl = next(u for u in updates if u.ticker == "AAPL")
        expected_ts = datetime.fromtimestamp(1605192894630916600 / 1_000_000_000, tz=timezone.utc)
        assert aapl.timestamp == expected_ts

    def test_parse_returns_all_tickers(self):
        source = self._make_source()
        updates = source._parse_snapshots(SAMPLE_RESPONSE)
        assert len(updates) == 2
        tickers = {u.ticker for u in updates}
        assert tickers == {"AAPL", "GOOGL"}

    def test_parse_empty_tickers_list(self):
        source = self._make_source()
        updates = source._parse_snapshots({"tickers": []})
        assert updates == []

    def test_parse_missing_tickers_key(self):
        source = self._make_source()
        updates = source._parse_snapshots({})
        assert updates == []

    def test_parse_price_rounded_to_two_decimals(self):
        source = self._make_source()
        data = {
            "tickers": [{
                "ticker": "TEST",
                "updated": 1605192894630916600,
                "day": {"c": 190.123456},
                "prevDay": {"c": 189.987654},
                "min": None,
            }]
        }
        updates = source._parse_snapshots(data)
        assert updates[0].price == round(190.123456, 2)

    def test_parse_skips_malformed_ticker(self):
        source = self._make_source()
        data = {
            "tickers": [
                {"ticker": "BAD"},  # missing required fields
                {
                    "ticker": "GOOD",
                    "updated": 1605192894630916600,
                    "day": {"c": 100.0},
                    "prevDay": {"c": 99.0},
                    "min": None,
                },
            ]
        }
        updates = source._parse_snapshots(data)
        assert len(updates) == 1
        assert updates[0].ticker == "GOOD"

    def test_parse_min_c_none_falls_back_to_day(self):
        source = self._make_source()
        data = {
            "tickers": [{
                "ticker": "AAPL",
                "updated": 1605192894630916600,
                "day": {"c": 190.0},
                "prevDay": {"c": 189.0},
                "min": {"c": None},  # c is explicitly None
            }]
        }
        updates = source._parse_snapshots(data)
        assert updates[0].price == 190.0  # fallback to day.c

    def test_direction_computed_correctly(self):
        source = self._make_source()
        updates = source._parse_snapshots(SAMPLE_RESPONSE)
        aapl = next(u for u in updates if u.ticker == "AAPL")
        # price=190.73 > previous=189.50 → UP
        assert aapl.direction == Direction.UP


class TestMassiveDataSourceTickerManagement:
    def _make_source(self) -> MassiveDataSource:
        return MassiveDataSource(api_key="test", price_cache=PriceCache())

    @pytest.mark.asyncio
    async def test_start_stores_uppercase_tickers(self):
        source = self._make_source()
        await source.start(["aapl", "googl"])
        assert source._tickers == {"AAPL", "GOOGL"}

    @pytest.mark.asyncio
    async def test_add_ticker_uppercases(self):
        source = self._make_source()
        await source.start([])
        await source.add_ticker("tsla")
        assert "TSLA" in source._tickers

    @pytest.mark.asyncio
    async def test_remove_ticker_clears_from_set_and_cache(self):
        cache = PriceCache()
        source = MassiveDataSource(api_key="test", price_cache=cache)
        await source.start(["AAPL", "MSFT"])

        # Manually put something in cache
        from app.market.models import PriceUpdate
        cache.update(PriceUpdate(ticker="AAPL", price=190.0, previous_price=189.0, timestamp=__import__("datetime").datetime.now()))

        await source.remove_ticker("AAPL")
        assert "AAPL" not in source._tickers
        assert cache.get("AAPL") is None

    @pytest.mark.asyncio
    async def test_remove_nonexistent_ticker_is_safe(self):
        source = self._make_source()
        await source.start(["AAPL"])
        await source.remove_ticker("ZZZZ")  # should not raise

    def test_get_latest_delegates_to_cache(self):
        cache = PriceCache()
        source = MassiveDataSource(api_key="test", price_cache=cache)
        assert source.get_latest("AAPL") is None

        from datetime import datetime
        update = PriceUpdate(ticker="AAPL", price=190.0, previous_price=189.0, timestamp=datetime.now())
        cache.update(update)
        assert source.get_latest("AAPL") is update

    def test_get_latest_uppercases_ticker(self):
        cache = PriceCache()
        source = MassiveDataSource(api_key="test", price_cache=cache)
        from datetime import datetime
        update = PriceUpdate(ticker="AAPL", price=190.0, previous_price=189.0, timestamp=datetime.now())
        cache.update(update)
        assert source.get_latest("aapl") is update

    def test_get_all_latest_delegates_to_cache(self):
        cache = PriceCache()
        source = MassiveDataSource(api_key="test", price_cache=cache)
        from datetime import datetime
        update = PriceUpdate(ticker="AAPL", price=190.0, previous_price=189.0, timestamp=datetime.now())
        cache.update(update)
        all_prices = source.get_all_latest()
        assert "AAPL" in all_prices


class TestMassiveDataSourceLifecycle:
    @pytest.mark.asyncio
    async def test_start_creates_background_task(self):
        source = MassiveDataSource(api_key="test", price_cache=PriceCache(), poll_interval=9999)
        # We don't want actual HTTP calls — patch the poll loop
        with patch.object(source, "_poll_loop", new_callable=AsyncMock):
            await source.start(["AAPL"])
            assert source._task is not None
            source._task.cancel()
            try:
                await source._task
            except (asyncio.CancelledError, Exception):
                pass

    @pytest.mark.asyncio
    async def test_stop_cancels_task(self):
        source = MassiveDataSource(api_key="test", price_cache=PriceCache(), poll_interval=9999)

        async def long_running():
            await asyncio.sleep(9999)

        source._task = asyncio.create_task(long_running())
        await source.stop()
        assert source._task.cancelled() or source._task.done()

    @pytest.mark.asyncio
    async def test_stop_with_no_task_is_safe(self):
        source = MassiveDataSource(api_key="test", price_cache=PriceCache())
        await source.stop()  # task is None — should not raise

    @pytest.mark.asyncio
    async def test_fetch_and_update_skips_when_no_tickers(self):
        source = MassiveDataSource(api_key="test", price_cache=PriceCache())
        mock_client = AsyncMock()
        await source._fetch_and_update(mock_client)
        mock_client.get.assert_not_called()

    @pytest.mark.asyncio
    async def test_fetch_and_update_handles_http_error(self):
        cache = PriceCache()
        source = MassiveDataSource(api_key="test", price_cache=cache)
        source._tickers = {"AAPL"}

        mock_response = MagicMock()
        mock_response.status_code = 403
        mock_client = AsyncMock()
        mock_client.get.return_value = mock_response
        mock_response.raise_for_status.side_effect = httpx.HTTPStatusError(
            "Forbidden", request=MagicMock(), response=mock_response
        )

        await source._fetch_and_update(mock_client)
        # No exception raised, cache unchanged
        assert cache.get("AAPL") is None

    @pytest.mark.asyncio
    async def test_fetch_and_update_handles_request_error(self):
        cache = PriceCache()
        source = MassiveDataSource(api_key="test", price_cache=cache)
        source._tickers = {"AAPL"}

        mock_client = AsyncMock()
        mock_client.get.side_effect = httpx.ConnectError("Connection failed", request=MagicMock())

        await source._fetch_and_update(mock_client)
        # No exception raised
        assert cache.get("AAPL") is None

    @pytest.mark.asyncio
    async def test_fetch_and_update_populates_cache(self):
        cache = PriceCache()
        source = MassiveDataSource(api_key="test", price_cache=cache)
        source._tickers = {"AAPL", "GOOGL"}

        mock_response = MagicMock()
        mock_response.json.return_value = SAMPLE_RESPONSE
        mock_response.raise_for_status.return_value = None

        mock_client = AsyncMock()
        mock_client.get.return_value = mock_response

        await source._fetch_and_update(mock_client)

        assert cache.get("AAPL") is not None
        assert cache.get("GOOGL") is not None

    def test_default_poll_interval(self):
        source = MassiveDataSource(api_key="test", price_cache=PriceCache())
        assert source._poll_interval == DEFAULT_POLL_INTERVAL

    def test_custom_poll_interval(self):
        source = MassiveDataSource(api_key="test", price_cache=PriceCache(), poll_interval=5.0)
        assert source._poll_interval == 5.0
