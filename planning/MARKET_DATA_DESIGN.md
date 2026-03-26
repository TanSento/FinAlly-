# Market Data Backend — Detailed Implementation Design

Complete implementation guide for the market data subsystem: unified interface, price cache, simulator (GBM), Massive API client, SSE streaming, and FastAPI integration.

---

## 1. Module Layout

```
backend/
  app/
    market/
      __init__.py           # Package exports
      models.py             # PriceUpdate, Direction, TickerConfig
      interface.py          # MarketDataSource ABC
      cache.py              # PriceCache (thread-safe in-memory store)
      factory.py            # create_market_data_source()
      simulator.py          # SimulatorDataSource (GBM + correlation + events)
      massive_client.py     # MassiveDataSource (Polygon.io REST polling)
    routes/
      stream.py             # SSE endpoint: GET /api/stream/prices
```

---

## 2. Data Models — `models.py`

```python
"""Market data models shared across all implementations."""

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum


class Direction(Enum):
    UP = "up"
    DOWN = "down"
    UNCHANGED = "unchanged"


@dataclass
class PriceUpdate:
    """A single price tick for one ticker."""

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

    def to_sse_dict(self) -> dict:
        """Serialize for SSE event payload."""
        return {
            "ticker": self.ticker,
            "price": self.price,
            "previousPrice": self.previous_price,
            "direction": self.direction.value,
            "timestamp": self.timestamp.isoformat(),
        }


@dataclass
class TickerConfig:
    """Simulation parameters for a single ticker."""

    seed_price: float
    annual_drift: float       # mu: annualized expected return
    annual_volatility: float  # sigma: annualized volatility
```

---

## 3. Abstract Interface — `interface.py`

```python
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
```

---

## 4. Price Cache — `cache.py`

The cache decouples producers (simulator/Massive) from consumers (SSE streams, API endpoints). Producers write; consumers read. Thread-safe via `threading.Lock` since asyncio tasks may run on different threads.

```python
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
```

---

## 5. Simulator — `simulator.py`

### 5.1 Constants and Seed Data

```python
"""Simulated market data using correlated Geometric Brownian Motion."""

import asyncio
import math
import random

import numpy as np
from datetime import datetime

from .models import PriceUpdate, TickerConfig
from .interface import MarketDataSource
from .cache import PriceCache

# --- Time step ---
UPDATE_INTERVAL = 0.5  # seconds between ticks
SECONDS_PER_YEAR = 252 * 6.5 * 3600  # ~5,896,800 (trading year)
DT = UPDATE_INTERVAL / SECONDS_PER_YEAR

# --- Random events ---
EVENT_PROBABILITY = 0.002   # ~0.2% chance per tick per ticker
EVENT_MAGNITUDE_MIN = 0.02  # 2% sudden move
EVENT_MAGNITUDE_MAX = 0.05  # 5% sudden move

# --- Seed prices and parameters for the default 10 tickers ---
TICKER_CONFIGS: dict[str, TickerConfig] = {
    "AAPL":  TickerConfig(seed_price=190.0,  annual_drift=0.10, annual_volatility=0.25),
    "GOOGL": TickerConfig(seed_price=175.0,  annual_drift=0.08, annual_volatility=0.28),
    "MSFT":  TickerConfig(seed_price=420.0,  annual_drift=0.12, annual_volatility=0.24),
    "AMZN":  TickerConfig(seed_price=185.0,  annual_drift=0.10, annual_volatility=0.30),
    "TSLA":  TickerConfig(seed_price=250.0,  annual_drift=0.05, annual_volatility=0.55),
    "NVDA":  TickerConfig(seed_price=130.0,  annual_drift=0.15, annual_volatility=0.45),
    "META":  TickerConfig(seed_price=500.0,  annual_drift=0.10, annual_volatility=0.35),
    "JPM":   TickerConfig(seed_price=195.0,  annual_drift=0.06, annual_volatility=0.20),
    "V":     TickerConfig(seed_price=280.0,  annual_drift=0.08, annual_volatility=0.18),
    "NFLX":  TickerConfig(seed_price=620.0,  annual_drift=0.10, annual_volatility=0.35),
}

DEFAULT_CONFIG = TickerConfig(
    seed_price=0.0,  # replaced by random_seed_price()
    annual_drift=0.08,
    annual_volatility=0.30,
)

# --- Sector groupings for correlation ---
TECH = {"AAPL", "GOOGL", "MSFT", "AMZN", "NVDA", "META", "NFLX"}
FINANCE = {"JPM", "V"}
```

### 5.2 Math Helpers

```python
def random_seed_price() -> float:
    """Random seed price for unknown tickers."""
    return round(random.uniform(20.0, 200.0), 2)


def gbm_step(price: float, drift: float, volatility: float, z: float) -> float:
    """One Geometric Brownian Motion step.

    S(t+dt) = S(t) * exp((mu - sigma^2/2)*dt + sigma*sqrt(dt)*Z)
    """
    exponent = (drift - 0.5 * volatility**2) * DT + volatility * math.sqrt(DT) * z
    return price * math.exp(exponent)


def apply_random_event(price: float) -> float:
    """Possibly apply a sudden 2-5% price shock."""
    if random.random() < EVENT_PROBABILITY:
        magnitude = random.uniform(EVENT_MAGNITUDE_MIN, EVENT_MAGNITUDE_MAX)
        direction = random.choice([-1, 1])
        return price * (1 + direction * magnitude)
    return price


def build_correlation_matrix(tickers: list[str]) -> np.ndarray:
    """Sector-based correlation matrix for correlated GBM draws."""
    n = len(tickers)
    corr = np.eye(n)
    for i in range(n):
        for j in range(i + 1, n):
            ti, tj = tickers[i], tickers[j]
            if ti in TECH and tj in TECH:
                rho = 0.6
            elif ti in FINANCE and tj in FINANCE:
                rho = 0.5
            elif (ti in TECH and tj in FINANCE) or (ti in FINANCE and tj in TECH):
                rho = 0.3
            else:
                rho = 0.2
            corr[i, j] = rho
            corr[j, i] = rho
    return corr


def generate_correlated_normals(corr_matrix: np.ndarray) -> np.ndarray:
    """Correlated standard normal draws via Cholesky decomposition."""
    L = np.linalg.cholesky(corr_matrix)
    z = np.random.standard_normal(len(corr_matrix))
    return L @ z
```

### 5.3 SimulatorDataSource Class

```python
class SimulatorDataSource(MarketDataSource):
    """Simulated market data with correlated GBM and random events."""

    def __init__(self, price_cache: PriceCache, update_interval: float = UPDATE_INTERVAL):
        self._cache = price_cache
        self._update_interval = update_interval
        self._tickers: list[str] = []          # ordered list (matches correlation matrix indices)
        self._prices: dict[str, float] = {}    # ticker -> current price
        self._configs: dict[str, TickerConfig] = {}
        self._corr_matrix: np.ndarray | None = None
        self._task: asyncio.Task | None = None

    async def start(self, tickers: list[str]) -> None:
        for t in tickers:
            self._add_ticker_internal(t.upper())
        self._rebuild_correlation()
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
        if t not in self._prices:
            self._add_ticker_internal(t)
            self._rebuild_correlation()

    async def remove_ticker(self, ticker: str) -> None:
        t = ticker.upper()
        if t in self._prices:
            self._tickers.remove(t)
            del self._prices[t]
            del self._configs[t]
            self._cache.remove(t)
            self._rebuild_correlation()

    def get_latest(self, ticker: str) -> PriceUpdate | None:
        return self._cache.get(ticker.upper())

    def get_all_latest(self) -> dict[str, PriceUpdate]:
        return self._cache.get_all()

    # --- Private helpers ---

    def _add_ticker_internal(self, ticker: str) -> None:
        config = TICKER_CONFIGS.get(ticker)
        if config is None:
            config = TickerConfig(
                seed_price=random_seed_price(),
                annual_drift=DEFAULT_CONFIG.annual_drift,
                annual_volatility=DEFAULT_CONFIG.annual_volatility,
            )
        self._tickers.append(ticker)
        self._prices[ticker] = config.seed_price
        self._configs[ticker] = config

    def _rebuild_correlation(self) -> None:
        if self._tickers:
            self._corr_matrix = build_correlation_matrix(self._tickers)
        else:
            self._corr_matrix = None

    async def _simulation_loop(self) -> None:
        while True:
            self._tick()
            await asyncio.sleep(self._update_interval)

    def _tick(self) -> None:
        if not self._tickers or self._corr_matrix is None:
            return

        now = datetime.now()
        z_values = generate_correlated_normals(self._corr_matrix)
        updates: list[PriceUpdate] = []

        for i, ticker in enumerate(self._tickers):
            previous = self._prices[ticker]
            config = self._configs[ticker]

            new_price = gbm_step(previous, config.annual_drift, config.annual_volatility, z_values[i])
            new_price = apply_random_event(new_price)
            new_price = max(new_price, 0.01)  # floor at 1 cent

            self._prices[ticker] = new_price
            updates.append(PriceUpdate(
                ticker=ticker,
                price=round(new_price, 2),
                previous_price=round(previous, 2),
                timestamp=now,
            ))

        self._cache.update_batch(updates)
```

---

## 6. Massive API Client — `massive_client.py`

### 6.1 Key Design Points

- Uses the **Snapshot All Tickers** endpoint: one call returns prices for all requested tickers.
- Default poll interval: 15s (safe for free tier at 5 calls/min).
- Timestamps: `updated` field is **nanoseconds**, `min.t` is **milliseconds** — handle both.
- Price priority: prefer `min.c` (latest minute close) over `day.c` (daily close) for freshest data.
- Previous price: `prevDay.c` (yesterday's close).
- Free tier omits `lastTrade`/`lastQuote` — never depend on them.

### 6.2 Full Implementation

```python
"""Market data from the Massive (Polygon.io) REST API."""

import asyncio
import logging

import httpx
from datetime import datetime, timezone

from .models import PriceUpdate
from .interface import MarketDataSource
from .cache import PriceCache

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
```

---

## 7. Factory — `factory.py`

```python
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
```

Lazy imports keep `httpx` out of memory when using only the simulator.

---

## 8. Package Init — `__init__.py`

```python
"""Market data package — unified interface for price data."""

from .models import PriceUpdate, Direction, TickerConfig
from .interface import MarketDataSource
from .cache import PriceCache
from .factory import create_market_data_source

__all__ = [
    "PriceUpdate",
    "Direction",
    "TickerConfig",
    "MarketDataSource",
    "PriceCache",
    "create_market_data_source",
]
```

---

## 9. SSE Streaming Endpoint — `routes/stream.py`

```python
"""Server-Sent Events endpoint for live price streaming."""

import asyncio
import json

from fastapi import APIRouter, Request
from starlette.responses import StreamingResponse

from app.market.cache import PriceCache

router = APIRouter()

SSE_PUSH_INTERVAL = 0.5  # seconds between SSE pushes


async def price_event_generator(cache: PriceCache):
    """Yield SSE-formatted price events from the cache."""
    while True:
        prices = cache.get_all()
        for update in prices.values():
            payload = json.dumps(update.to_sse_dict())
            yield f"data: {payload}\n\n"
        await asyncio.sleep(SSE_PUSH_INTERVAL)


@router.get("/api/stream/prices")
async def stream_prices(request: Request):
    """SSE endpoint — streams live price updates to the frontend."""
    cache: PriceCache = request.app.state.price_cache
    return StreamingResponse(
        price_event_generator(cache),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )
```

### Frontend SSE Client (reference)

```typescript
const source = new EventSource("/api/stream/prices");

source.onmessage = (event) => {
  const data = JSON.parse(event.data);
  // data: { ticker, price, previousPrice, direction, timestamp }
  updateTicker(data);
};

source.onerror = () => {
  // EventSource auto-reconnects — just update UI status indicator
  setConnectionStatus("reconnecting");
};
```

---

## 10. FastAPI Integration — Lifespan Setup

```python
"""FastAPI app with market data lifecycle management."""

from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.market import PriceCache, create_market_data_source


def get_default_tickers() -> list[str]:
    """Load default watchlist tickers from DB (or hardcoded fallback)."""
    # In production this reads from the watchlist table:
    #   SELECT ticker FROM watchlist WHERE user_id = 'default'
    # Fallback for startup before DB is ready:
    return ["AAPL", "GOOGL", "MSFT", "AMZN", "TSLA", "NVDA", "META", "JPM", "V", "NFLX"]


@asynccontextmanager
async def lifespan(app: FastAPI):
    # --- Startup ---
    cache = PriceCache()
    source = create_market_data_source(cache)
    tickers = get_default_tickers()
    await source.start(tickers)

    app.state.price_cache = cache
    app.state.market_data = source

    yield

    # --- Shutdown ---
    await source.stop()


app = FastAPI(lifespan=lifespan)

# Register routes
from app.routes.stream import router as stream_router
app.include_router(stream_router)
```

### Accessing Market Data from Other Endpoints

Any endpoint that needs prices reads from `request.app.state`:

```python
from fastapi import APIRouter, Request

router = APIRouter()


@router.get("/api/watchlist")
async def get_watchlist(request: Request):
    """Return watchlist with latest prices."""
    cache = request.app.state.price_cache
    # ... fetch watchlist tickers from DB ...
    result = []
    for ticker in watchlist_tickers:
        update = cache.get(ticker)
        result.append({
            "ticker": ticker,
            "price": update.price if update else None,
            "previousPrice": update.previous_price if update else None,
            "direction": update.direction.value if update else None,
            "timestamp": update.timestamp.isoformat() if update else None,
        })
    return result


@router.post("/api/watchlist")
async def add_to_watchlist(request: Request, body: dict):
    """Add a ticker to the watchlist and start tracking its price."""
    ticker = body["ticker"].upper()
    source = request.app.state.market_data
    await source.add_ticker(ticker)
    # ... insert into DB ...


@router.delete("/api/watchlist/{ticker}")
async def remove_from_watchlist(request: Request, ticker: str):
    """Remove a ticker from the watchlist and stop tracking it."""
    source = request.app.state.market_data
    await source.remove_ticker(ticker.upper())
    # ... delete from DB ...
```

---

## 11. Data Flow Diagram

```
                         ┌──────────────────────┐
                         │   Environment Check   │
                         │  MASSIVE_API_KEY set? │
                         └──────┬───────┬────────┘
                           yes  │       │  no
                    ┌───────────▼─┐   ┌─▼───────────────┐
                    │   Massive   │   │   Simulator      │
                    │  DataSource │   │   DataSource     │
                    │             │   │                  │
                    │ polls every │   │ GBM tick every   │
                    │ 15s via     │   │ 500ms with       │
                    │ httpx       │   │ correlated noise │
                    └──────┬──────┘   └────────┬─────────┘
                           │                   │
                           │  update_batch()   │
                           ▼                   ▼
                    ┌──────────────────────────────┐
                    │         PriceCache           │
                    │  dict[str, PriceUpdate]      │
                    │  (thread-safe, in-memory)    │
                    └──────────┬───────────────────┘
                               │
                    ┌──────────┼───────────┐
                    │          │           │
                    ▼          ▼           ▼
              SSE Stream   REST APIs   Trade Execution
              /api/stream  /api/       (fill at current
              /prices      watchlist   cached price)
```

---

## 12. Python Dependencies

Add to `backend/pyproject.toml`:

```toml
[project]
dependencies = [
    "fastapi>=0.115",
    "uvicorn[standard]>=0.34",
    "httpx>=0.28",
    "numpy>=2.2",
]
```

- `httpx` — async HTTP client for Massive API (also useful elsewhere)
- `numpy` — Cholesky decomposition for correlated random draws
- `fastapi` + `uvicorn` — web framework and ASGI server
- SSE uses `starlette.responses.StreamingResponse` (included with FastAPI)

---

## 13. Key Design Decisions

| Decision | Rationale |
|----------|-----------|
| ABC with async methods | Both implementations use async (httpx for Massive, asyncio.sleep for simulator) |
| PriceCache separate from source | Decouples producers from consumers; SSE reads without knowing the source |
| `threading.Lock` in cache | Multiple async tasks may access concurrently; lock prevents data races |
| Direction computed in `__post_init__` | Frontend needs up/down/unchanged for flash colors; computed once |
| Factory with lazy imports | Avoids importing httpx when using only the simulator |
| `to_sse_dict()` on PriceUpdate | Keeps serialization logic close to the data model |
| Tickers always uppercased | Massive API is case-sensitive; normalize at the boundary |
| Minute close preferred over day close | Freshest available price from Massive snapshots |
| `update_batch()` on cache | All tickers update atomically per tick — no partial state visible to readers |
| Simulator stores ordered `list[str]` | Correlation matrix indices must match ticker order |

---

## 14. Gotchas and Edge Cases

### Massive API
- **Timestamp units vary**: `updated` is nanoseconds; `min.t` is milliseconds. Dividing by the wrong factor is a common bug.
- **3:30-4:00 AM EST gap**: Snapshots clear and repopulate daily. Data may be empty during this window.
- **Free tier**: Prices are 15-min delayed. `lastTrade`/`lastQuote` fields are absent.
- **Rate limits**: No headers returned — track call frequency yourself. Free tier = max 5 calls/min.
- **Missing tickers**: If a requested ticker doesn't exist, it's simply absent from the response (no error).

### Simulator
- **Correlation matrix rebuild**: Adding/removing a ticker rebuilds the full matrix. This is O(n^2) but n is small (10-20 tickers), so negligible.
- **Price floor**: GBM can't produce negative prices mathematically, but floating-point edge cases are clamped to $0.01.
- **Event overlay**: Random events apply after GBM step. Events are independent of the correlation structure (intentional — they represent ticker-specific news).

### SSE
- **No client disconnect detection**: The generator runs until the client disconnects. Starlette handles cleanup when the connection drops.
- **All tickers per push**: Every SSE push sends all tickers. With 10-20 tickers this is ~2-4KB per push — negligible bandwidth.
- **EventSource auto-reconnect**: The browser's `EventSource` API handles reconnection automatically. The frontend just needs to update a status indicator.

---

## 15. Testing Strategy

### Unit Tests

```python
# test_models.py
def test_price_update_direction_up():
    update = PriceUpdate(ticker="AAPL", price=191.0, previous_price=190.0, timestamp=datetime.now())
    assert update.direction == Direction.UP

def test_price_update_direction_down():
    update = PriceUpdate(ticker="AAPL", price=189.0, previous_price=190.0, timestamp=datetime.now())
    assert update.direction == Direction.DOWN

def test_price_update_to_sse_dict():
    now = datetime.now()
    update = PriceUpdate(ticker="AAPL", price=191.0, previous_price=190.0, timestamp=now)
    d = update.to_sse_dict()
    assert d["ticker"] == "AAPL"
    assert d["direction"] == "up"


# test_simulator.py
def test_gbm_step_positive():
    """GBM always produces positive prices."""
    price = gbm_step(100.0, drift=0.10, volatility=0.25, z=0.0)
    assert price > 0

def test_gbm_step_with_extreme_negative_z():
    price = gbm_step(100.0, drift=0.10, volatility=0.25, z=-5.0)
    assert price > 0  # GBM cannot go negative

def test_correlation_matrix_symmetric():
    tickers = ["AAPL", "GOOGL", "JPM"]
    corr = build_correlation_matrix(tickers)
    assert np.allclose(corr, corr.T)
    assert np.allclose(np.diag(corr), 1.0)

def test_correlation_matrix_positive_definite():
    tickers = ["AAPL", "GOOGL", "MSFT", "JPM", "V"]
    corr = build_correlation_matrix(tickers)
    eigenvalues = np.linalg.eigvalsh(corr)
    assert all(ev > 0 for ev in eigenvalues)


# test_cache.py
def test_cache_update_and_get():
    cache = PriceCache()
    update = PriceUpdate(ticker="AAPL", price=190.0, previous_price=189.0, timestamp=datetime.now())
    cache.update(update)
    assert cache.get("AAPL") == update
    assert cache.get("GOOGL") is None

def test_cache_remove():
    cache = PriceCache()
    update = PriceUpdate(ticker="AAPL", price=190.0, previous_price=189.0, timestamp=datetime.now())
    cache.update(update)
    cache.remove("AAPL")
    assert cache.get("AAPL") is None


# test_massive_client.py
def test_parse_snapshots():
    """Verify parsing of a real Massive API response shape."""
    raw = {
        "tickers": [{
            "ticker": "AAPL",
            "todaysChange": 1.23,
            "todaysChangePerc": 0.65,
            "updated": 1605192894630916600,  # nanoseconds
            "day": {"o": 189.5, "h": 191.2, "l": 188.9, "c": 190.73, "v": 52341000, "vw": 190.12},
            "prevDay": {"o": 188.0, "h": 189.8, "l": 187.5, "c": 189.50, "v": 48200000, "vw": 188.9},
            "min": {"o": 190.6, "h": 190.8, "l": 190.55, "c": 190.73, "v": 125000, "vw": 190.68},
        }]
    }
    source = MassiveDataSource(api_key="test", price_cache=PriceCache())
    updates = source._parse_snapshots(raw)
    assert len(updates) == 1
    assert updates[0].ticker == "AAPL"
    assert updates[0].price == 190.73  # min.c preferred
    assert updates[0].previous_price == 189.50  # prevDay.c
```

### Integration Tests

```python
# test_simulator_integration.py
import asyncio

async def test_simulator_produces_updates():
    """Simulator writes to cache after starting."""
    cache = PriceCache()
    sim = SimulatorDataSource(price_cache=cache, update_interval=0.1)
    await sim.start(["AAPL", "GOOGL"])
    await asyncio.sleep(0.3)  # wait for a few ticks
    assert cache.get("AAPL") is not None
    assert cache.get("GOOGL") is not None
    await sim.stop()

async def test_simulator_add_remove_ticker():
    cache = PriceCache()
    sim = SimulatorDataSource(price_cache=cache, update_interval=0.1)
    await sim.start(["AAPL"])
    await asyncio.sleep(0.2)

    await sim.add_ticker("TSLA")
    await asyncio.sleep(0.2)
    assert cache.get("TSLA") is not None

    await sim.remove_ticker("TSLA")
    assert cache.get("TSLA") is None
    await sim.stop()
```
