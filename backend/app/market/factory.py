"""Factory for selecting the market data source at startup."""

import os

from .cache import PriceCache
from .interface import MarketDataSource


def create_market_data_source(price_cache: PriceCache) -> MarketDataSource:
    """Return MassiveDataSource if API key is set, else SimulatorDataSource."""
    api_key = os.environ.get("MASSIVE_API_KEY", "").strip()

    if api_key:
        from .massive_client import MassiveDataSource
        return MassiveDataSource(api_key=api_key, price_cache=price_cache)

    from .simulator import SimulatorDataSource
    return SimulatorDataSource(price_cache=price_cache)
