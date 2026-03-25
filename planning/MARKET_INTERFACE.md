# Market Data Interface Design

Unified Python interface for retrieving stock prices. The backend selects the implementation based on environment configuration: Massive API when `MASSIVE_API_KEY` is set, otherwise the built-in simulator.

## Core Data Model

```python
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum


class Direction(Enum):
    UP = "up"
    DOWN = "down"
    UNCHANGED = "unchanged"


@dataclass
class PriceUpdate:
    """A single price update for a ticker."""
    ticker: str
    price: float
    previous_price: float
    timestamp: datetime
    direction: Direction = field(init=False)

    def __post_init__(self):
        if self.price > self.previous_price:
            self.direction = Direction.UP
        elif self.price < self.previous_price:
            self.direction = Direction.DOWN
        else:
            self.direction = Direction.UNCHANGED
```

## Abstract Interface

```python
from abc import ABC, abstractmethod


class MarketDataSource(ABC):
    """Abstract interface for market data providers.

    Both the Massive API client and the simulator implement this interface.
    All downstream code (SSE streaming, price cache) depends only on this abstraction.
    """

    @abstractmethod
    async def start(self, tickers: list[str]) -> None:
        """Start producing price updates for the given tickers.

        Called once during app startup. Begins the background polling loop
        (Massive) or simulation loop (Simulator).
        """

    @abstractmethod
    async def stop(self) -> None:
        """Stop producing price updates. Called during app shutdown."""

    @abstractmethod
    async def add_ticker(self, ticker: str) -> None:
        """Add a ticker to the active set. Takes effect on the next update cycle."""

    @abstractmethod
    async def remove_ticker(self, ticker: str) -> None:
        """Remove a ticker from the active set."""

    @abstractmethod
    def get_latest(self, ticker: str) -> PriceUpdate | None:
        """Get the most recent price for a ticker, or None if not yet available."""

    @abstractmethod
    def get_all_latest(self) -> dict[str, PriceUpdate]:
        """Get the most recent prices for all active tickers."""
```

## Price Cache

A shared in-memory cache sits between the data source and SSE consumers. The data source writes to it; SSE streams read from it.

```python
import threading


class PriceCache:
    """Thread-safe in-memory cache for latest prices.

    Written to by the market data source background task.
    Read from by SSE streaming endpoints.
    """

    def __init__(self):
        self._prices: dict[str, PriceUpdate] = {}
        self._lock = threading.Lock()

    def update(self, price: PriceUpdate) -> None:
        with self._lock:
            self._prices[price.ticker] = price

    def update_batch(self, prices: list[PriceUpdate]) -> None:
        with self._lock:
            for p in prices:
                self._prices[p.ticker] = p

    def get(self, ticker: str) -> PriceUpdate | None:
        with self._lock:
            return self._prices.get(ticker)

    def get_all(self) -> dict[str, PriceUpdate]:
        with self._lock:
            return dict(self._prices)

    def remove(self, ticker: str) -> None:
        with self._lock:
            self._prices.pop(ticker, None)
```

## Factory Function

The backend creates the appropriate data source at startup based on environment configuration.

```python
import os


def create_market_data_source(price_cache: PriceCache) -> MarketDataSource:
    """Create the appropriate market data source based on environment."""
    api_key = os.environ.get("MASSIVE_API_KEY", "").strip()
    if api_key:
        from .massive_client import MassiveDataSource
        return MassiveDataSource(api_key=api_key, price_cache=price_cache)
    else:
        from .simulator import SimulatorDataSource
        return SimulatorDataSource(price_cache=price_cache)
```

## Massive Implementation Sketch

```python
import asyncio
import httpx
from datetime import datetime


class MassiveDataSource(MarketDataSource):
    """Market data from the Massive (Polygon.io) REST API."""

    BASE_URL = "https://api.polygon.io"

    def __init__(self, api_key: str, price_cache: PriceCache, poll_interval: float = 15.0):
        self._api_key = api_key
        self._cache = price_cache
        self._poll_interval = poll_interval
        self._tickers: set[str] = set()
        self._task: asyncio.Task | None = None

    async def start(self, tickers: list[str]) -> None:
        self._tickers = set(t.upper() for t in tickers)
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
        self._tickers.discard(ticker.upper())
        self._cache.remove(ticker.upper())

    def get_latest(self, ticker: str) -> PriceUpdate | None:
        return self._cache.get(ticker.upper())

    def get_all_latest(self) -> dict[str, PriceUpdate]:
        return self._cache.get_all()

    async def _poll_loop(self) -> None:
        async with httpx.AsyncClient(timeout=10.0) as client:
            while True:
                try:
                    await self._fetch_and_update(client)
                except httpx.HTTPStatusError:
                    pass  # log and continue
                await asyncio.sleep(self._poll_interval)

    async def _fetch_and_update(self, client: httpx.AsyncClient) -> None:
        if not self._tickers:
            return
        response = await client.get(
            f"{self.BASE_URL}/v2/snapshot/locale/us/markets/stocks/tickers",
            params={
                "tickers": ",".join(sorted(self._tickers)),
                "apiKey": self._api_key,
            },
        )
        response.raise_for_status()
        data = response.json()

        updates = []
        for t in data.get("tickers", []):
            price = t.get("min", {}).get("c") or t["day"]["c"]
            prev = t["prevDay"]["c"]
            ts = datetime.fromtimestamp(t["updated"] / 1_000_000_000)
            updates.append(PriceUpdate(
                ticker=t["ticker"],
                price=price,
                previous_price=prev,
                timestamp=ts,
            ))
        self._cache.update_batch(updates)
```

## Simulator Implementation Sketch

```python
import asyncio
from datetime import datetime


class SimulatorDataSource(MarketDataSource):
    """Simulated market data using geometric Brownian motion."""

    def __init__(self, price_cache: PriceCache, update_interval: float = 0.5):
        self._cache = price_cache
        self._update_interval = update_interval
        self._tickers: dict[str, float] = {}  # ticker -> current price
        self._task: asyncio.Task | None = None

    async def start(self, tickers: list[str]) -> None:
        for t in tickers:
            self._tickers[t.upper()] = SEED_PRICES.get(t.upper(), random_seed_price())
        self._task = asyncio.create_task(self._simulation_loop())

    async def stop(self) -> None:
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass

    async def add_ticker(self, ticker: str) -> None:
        t = ticker.upper()
        if t not in self._tickers:
            self._tickers[t] = SEED_PRICES.get(t, random_seed_price())

    async def remove_ticker(self, ticker: str) -> None:
        t = ticker.upper()
        self._tickers.pop(t, None)
        self._cache.remove(t)

    def get_latest(self, ticker: str) -> PriceUpdate | None:
        return self._cache.get(ticker.upper())

    def get_all_latest(self) -> dict[str, PriceUpdate]:
        return self._cache.get_all()

    async def _simulation_loop(self) -> None:
        while True:
            self._tick()
            await asyncio.sleep(self._update_interval)

    def _tick(self) -> None:
        now = datetime.now()
        updates = []
        for ticker, current_price in self._tickers.items():
            previous = current_price
            new_price = simulate_gbm_step(current_price, ...)
            self._tickers[ticker] = new_price
            updates.append(PriceUpdate(
                ticker=ticker,
                price=new_price,
                previous_price=previous,
                timestamp=now,
            ))
        self._cache.update_batch(updates)
```

## Integration with FastAPI

```python
from contextlib import asynccontextmanager
from fastapi import FastAPI


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup
    cache = PriceCache()
    source = create_market_data_source(cache)
    default_tickers = get_watchlist_tickers()  # from DB
    await source.start(default_tickers)

    app.state.price_cache = cache
    app.state.market_data = source

    yield

    # Shutdown
    await source.stop()

app = FastAPI(lifespan=lifespan)
```

## SSE Streaming

The SSE endpoint reads from the price cache on a regular cadence and pushes updates to connected clients.

```python
import asyncio
import json
from starlette.responses import StreamingResponse


async def price_stream(cache: PriceCache):
    """Generator that yields SSE events from the price cache."""
    while True:
        prices = cache.get_all()
        for ticker, update in prices.items():
            event_data = json.dumps({
                "ticker": update.ticker,
                "price": update.price,
                "previousPrice": update.previous_price,
                "direction": update.direction.value,
                "timestamp": update.timestamp.isoformat(),
            })
            yield f"data: {event_data}\n\n"
        await asyncio.sleep(0.5)


@app.get("/api/stream/prices")
async def stream_prices(request: Request):
    cache = request.app.state.price_cache
    return StreamingResponse(
        price_stream(cache),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
```

## Module Layout

```
backend/
  app/
    market/
      __init__.py
      models.py          # PriceUpdate, Direction
      interface.py        # MarketDataSource ABC
      cache.py            # PriceCache
      factory.py          # create_market_data_source()
      massive_client.py   # MassiveDataSource
      simulator.py        # SimulatorDataSource
```

## Design Decisions

| Decision | Rationale |
|----------|-----------|
| ABC with async methods | Both implementations are async (httpx for Massive, asyncio.sleep for sim) |
| PriceCache separate from source | Decouples producers from consumers; SSE reads cache without knowing the source |
| `threading.Lock` in cache | PriceCache may be read from multiple async tasks; lock prevents data races |
| Direction computed in `__post_init__` | Frontend needs up/down/unchanged for flash colors; computed once at creation |
| Factory uses lazy imports | Avoids importing httpx when only using the simulator |
| Poll interval configurable | 15s for free tier, 2-5s for paid tiers |
| Tickers stored as uppercase set | Massive API is case-sensitive; normalize once at the boundary |
