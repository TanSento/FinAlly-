"""Abstract market data source interface."""

from abc import ABC, abstractmethod

from .models import PriceUpdate


class MarketDataSource(ABC):
    """Contract implemented by both Simulator and Massive client.

    All downstream code (SSE streaming, price lookups, portfolio valuation)
    depends only on this abstraction.
    """

    @abstractmethod
    async def start(self, tickers: list[str]) -> None:
        """Begin producing price updates for the given tickers.

        Called once during FastAPI lifespan startup.
        """

    @abstractmethod
    async def stop(self) -> None:
        """Stop producing updates. Called during shutdown."""

    @abstractmethod
    async def add_ticker(self, ticker: str) -> None:
        """Add a ticker to the active set. Takes effect next update cycle."""

    @abstractmethod
    async def remove_ticker(self, ticker: str) -> None:
        """Remove a ticker from the active set and price cache."""

    @abstractmethod
    def get_latest(self, ticker: str) -> PriceUpdate | None:
        """Most recent price for a ticker, or None if unavailable."""

    @abstractmethod
    def get_all_latest(self) -> dict[str, PriceUpdate]:
        """Most recent prices for all active tickers."""
