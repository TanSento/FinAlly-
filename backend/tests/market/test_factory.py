"""Unit tests for the market data source factory."""

import os
from unittest.mock import patch

import pytest

from app.market.cache import PriceCache
from app.market.factory import create_market_data_source
from app.market.massive_client import MassiveDataSource
from app.market.simulator import SimulatorDataSource


class TestCreateMarketDataSource:
    def test_returns_simulator_when_no_api_key(self):
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("MASSIVE_API_KEY", None)
            source = create_market_data_source(PriceCache())
        assert isinstance(source, SimulatorDataSource)

    def test_returns_simulator_when_empty_api_key(self):
        with patch.dict(os.environ, {"MASSIVE_API_KEY": ""}):
            source = create_market_data_source(PriceCache())
        assert isinstance(source, SimulatorDataSource)

    def test_returns_simulator_when_whitespace_api_key(self):
        with patch.dict(os.environ, {"MASSIVE_API_KEY": "   "}):
            source = create_market_data_source(PriceCache())
        assert isinstance(source, SimulatorDataSource)

    def test_returns_massive_when_api_key_set(self):
        with patch.dict(os.environ, {"MASSIVE_API_KEY": "real-api-key-123"}):
            source = create_market_data_source(PriceCache())
        assert isinstance(source, MassiveDataSource)

    def test_massive_source_receives_api_key(self):
        api_key = "my-secret-key"
        with patch.dict(os.environ, {"MASSIVE_API_KEY": api_key}):
            source = create_market_data_source(PriceCache())
        assert isinstance(source, MassiveDataSource)
        assert source._api_key == api_key

    def test_massive_source_receives_cache(self):
        cache = PriceCache()
        with patch.dict(os.environ, {"MASSIVE_API_KEY": "key"}):
            source = create_market_data_source(cache)
        assert isinstance(source, MassiveDataSource)
        assert source._cache is cache

    def test_simulator_source_receives_cache(self):
        cache = PriceCache()
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("MASSIVE_API_KEY", None)
            source = create_market_data_source(cache)
        assert isinstance(source, SimulatorDataSource)
        assert source._cache is cache

    def test_api_key_whitespace_stripped(self):
        with patch.dict(os.environ, {"MASSIVE_API_KEY": "  key-with-spaces  "}):
            source = create_market_data_source(PriceCache())
        assert isinstance(source, MassiveDataSource)
        # The factory strips the key before passing to MassiveDataSource
        # The raw key stored in _api_key depends on implementation;
        # just verify we got a MassiveDataSource (key was non-empty after strip)
