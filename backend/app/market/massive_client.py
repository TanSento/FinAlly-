"""Market data from the Massive (Polygon.io) REST API."""

import asyncio
import logging
from datetime import datetime, timezone

import httpx

from .cache import PriceCache
from .interface import MarketDataSource
from .models import PriceUpdate

logger = logging.getLogger(__name__)

BASE_URL = "https://api.polygon.io"
DEFAULT_POLL_INTERVAL = 15.0  # seconds — safe for free tier (5 calls/min)


class MassiveDataSource(MarketDataSource):
    """Polls the Massive (Polygon.io) snapshot endpoint for live prices."""

    def __init__(
        self,
        api_key: str,
        price_cache: PriceCache,
        poll_interval: float = DEFAULT_POLL_INTERVAL,
    ):
        self._api_key = api_key
        self._cache = price_cache
        self._poll_interval = poll_interval
        self._tickers: set[str] = set()
        self._task: asyncio.Task | None = None

    async def start(self, tickers: list[str]) -> None:
        self._tickers = {t.upper() for t in tickers}
        self._task = asyncio.create_task(self._poll_loop())

    async def stop(self) -> None:
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass

    async def add_ticker(self, ticker: str) -> None:
        self._tickers.add(ticker.upper())

    async def remove_ticker(self, ticker: str) -> None:
        t = ticker.upper()
        self._tickers.discard(t)
        self._cache.remove(t)

    def get_latest(self, ticker: str) -> PriceUpdate | None:
        return self._cache.get(ticker.upper())

    def get_all_latest(self) -> dict[str, PriceUpdate]:
        return self._cache.get_all()

    # --- Private ---

    async def _poll_loop(self) -> None:
        async with httpx.AsyncClient(timeout=10.0) as client:
            while True:
                await self._fetch_and_update(client)
                await asyncio.sleep(self._poll_interval)

    async def _fetch_and_update(self, client: httpx.AsyncClient) -> None:
        if not self._tickers:
            return

        try:
            response = await client.get(
                f"{BASE_URL}/v2/snapshot/locale/us/markets/stocks/tickers",
                params={
                    "tickers": ",".join(sorted(self._tickers)),
                    "apiKey": self._api_key,
                },
            )
            response.raise_for_status()
        except httpx.HTTPStatusError as e:
            logger.warning("Massive API HTTP error: %s", e.response.status_code)
            return
        except httpx.RequestError as e:
            logger.warning("Massive API request error: %s", e)
            return

        data = response.json()
        updates = self._parse_snapshots(data)
        self._cache.update_batch(updates)

    def _parse_snapshots(self, data: dict) -> list[PriceUpdate]:
        """Parse the snapshot response into PriceUpdate objects."""
        updates: list[PriceUpdate] = []

        for t in data.get("tickers", []):
            try:
                # Prefer minute close (freshest), fall back to day close
                min_data = t.get("min")
                if min_data and min_data.get("c") is not None:
                    price = min_data["c"]
                else:
                    price = t["day"]["c"]

                previous_price = t["prevDay"]["c"]

                # 'updated' is nanoseconds
                ts = datetime.fromtimestamp(
                    t["updated"] / 1_000_000_000, tz=timezone.utc
                )

                updates.append(PriceUpdate(
                    ticker=t["ticker"],
                    price=round(price, 2),
                    previous_price=round(previous_price, 2),
                    timestamp=ts,
                ))
            except (KeyError, TypeError) as e:
                logger.warning("Failed to parse ticker %s: %s", t.get("ticker"), e)
                continue

        return updates
