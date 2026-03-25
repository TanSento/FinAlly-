# Market Simulator Design

The simulator generates realistic stock price movements when no Massive API key is configured. It runs as an in-process asyncio background task with zero external dependencies.

## Approach: Geometric Brownian Motion (GBM)

GBM is the standard model for stock price simulation. Each tick, the price evolves as:

```
S(t+dt) = S(t) * exp((mu - sigma^2/2) * dt + sigma * sqrt(dt) * Z)
```

Where:
- `S(t)` = current price
- `mu` = drift (annualized expected return)
- `sigma` = volatility (annualized)
- `dt` = time step as fraction of a year
- `Z` = standard normal random variable

This produces prices that:
- Cannot go negative (multiplicative, not additive)
- Have log-normal distribution (realistic)
- Show larger absolute moves at higher price levels (realistic)

## Ticker Configuration

Each ticker has drift and volatility parameters. Realistic defaults based on historical characteristics:

```python
from dataclasses import dataclass


@dataclass
class TickerConfig:
    """Configuration for a simulated ticker."""
    seed_price: float
    annual_drift: float     # mu: annualized expected return
    annual_volatility: float  # sigma: annualized volatility


TICKER_CONFIGS: dict[str, TickerConfig] = {
    "AAPL":  TickerConfig(seed_price=190.0, annual_drift=0.10, annual_volatility=0.25),
    "GOOGL": TickerConfig(seed_price=175.0, annual_drift=0.08, annual_volatility=0.28),
    "MSFT":  TickerConfig(seed_price=420.0, annual_drift=0.12, annual_volatility=0.24),
    "AMZN":  TickerConfig(seed_price=185.0, annual_drift=0.10, annual_volatility=0.30),
    "TSLA":  TickerConfig(seed_price=250.0, annual_drift=0.05, annual_volatility=0.55),
    "NVDA":  TickerConfig(seed_price=130.0, annual_drift=0.15, annual_volatility=0.45),
    "META":  TickerConfig(seed_price=500.0, annual_drift=0.10, annual_volatility=0.35),
    "JPM":   TickerConfig(seed_price=195.0, annual_drift=0.06, annual_volatility=0.20),
    "V":     TickerConfig(seed_price=280.0, annual_drift=0.08, annual_volatility=0.18),
    "NFLX":  TickerConfig(seed_price=620.0, annual_drift=0.10, annual_volatility=0.35),
}

# Default config for dynamically added tickers
DEFAULT_CONFIG = TickerConfig(
    seed_price=0.0,  # overridden by random_seed_price()
    annual_drift=0.08,
    annual_volatility=0.30,
)
```

Dynamically added tickers (not in the seed list) get a random seed price between $20 and $200.

## Correlated Moves

Stocks don't move independently. The simulator uses a correlation matrix via Cholesky decomposition to generate correlated random draws.

```python
import numpy as np

# Simplified sector-based correlation
# Tech stocks (AAPL, GOOGL, MSFT, AMZN, NVDA, META, NFLX) correlate ~0.6
# Finance (JPM, V) correlate ~0.5 with each other, ~0.3 with tech
# TSLA is loosely correlated with everything ~0.3

def build_correlation_matrix(tickers: list[str]) -> np.ndarray:
    """Build a correlation matrix based on sector groupings."""
    n = len(tickers)
    corr = np.eye(n)

    tech = {"AAPL", "GOOGL", "MSFT", "AMZN", "NVDA", "META", "NFLX"}
    finance = {"JPM", "V"}

    for i in range(n):
        for j in range(i + 1, n):
            ti, tj = tickers[i], tickers[j]
            if ti in tech and tj in tech:
                rho = 0.6
            elif ti in finance and tj in finance:
                rho = 0.5
            elif (ti in tech and tj in finance) or (ti in finance and tj in tech):
                rho = 0.3
            else:
                rho = 0.2  # unknown tickers get mild correlation
            corr[i, j] = rho
            corr[j, i] = rho

    return corr


def generate_correlated_normals(corr_matrix: np.ndarray) -> np.ndarray:
    """Generate correlated standard normal random variables."""
    L = np.linalg.cholesky(corr_matrix)
    z = np.random.standard_normal(len(corr_matrix))
    return L @ z
```

## Random Events

Occasional sudden price moves add drama and realism. On each tick, each ticker has a small probability of an "event" — a sharp move.

```python
import random

EVENT_PROBABILITY = 0.002  # ~0.2% chance per tick per ticker
EVENT_MAGNITUDE_MIN = 0.02  # 2% move
EVENT_MAGNITUDE_MAX = 0.05  # 5% move


def apply_random_event(price: float) -> float:
    """Possibly apply a sudden price event. Returns the price (modified or not)."""
    if random.random() < EVENT_PROBABILITY:
        magnitude = random.uniform(EVENT_MAGNITUDE_MIN, EVENT_MAGNITUDE_MAX)
        direction = random.choice([-1, 1])
        return price * (1 + direction * magnitude)
    return price
```

## GBM Step Function

```python
import math


UPDATE_INTERVAL = 0.5  # seconds between ticks

# Trading year: ~252 days, ~6.5 hours/day
SECONDS_PER_YEAR = 252 * 6.5 * 3600  # ~5,896,800
DT = UPDATE_INTERVAL / SECONDS_PER_YEAR  # time step as fraction of year


def gbm_step(price: float, drift: float, volatility: float, z: float) -> float:
    """Compute one GBM step.

    Args:
        price: Current price.
        drift: Annualized drift (mu).
        volatility: Annualized volatility (sigma).
        z: Standard normal random variable (possibly correlated).

    Returns:
        New price after one time step.
    """
    exponent = (drift - 0.5 * volatility**2) * DT + volatility * math.sqrt(DT) * z
    return price * math.exp(exponent)
```

## Full Simulator Implementation

```python
import asyncio
import random
import math
import numpy as np
from datetime import datetime

from .models import PriceUpdate
from .interface import MarketDataSource
from .cache import PriceCache


class SimulatorDataSource(MarketDataSource):
    """Simulated market data using correlated GBM with random events."""

    def __init__(self, price_cache: PriceCache, update_interval: float = 0.5):
        self._cache = price_cache
        self._update_interval = update_interval
        self._tickers: list[str] = []
        self._prices: dict[str, float] = {}
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

    def _add_ticker_internal(self, ticker: str) -> None:
        config = TICKER_CONFIGS.get(ticker)
        if config is None:
            seed = random.uniform(20.0, 200.0)
            config = TickerConfig(
                seed_price=seed,
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
        updates = []

        for i, ticker in enumerate(self._tickers):
            previous = self._prices[ticker]
            config = self._configs[ticker]

            # GBM step with correlated noise
            new_price = gbm_step(previous, config.annual_drift, config.annual_volatility, z_values[i])

            # Random event overlay
            new_price = apply_random_event(new_price)

            # Clamp to prevent negative (shouldn't happen with GBM, but safety)
            new_price = max(new_price, 0.01)

            self._prices[ticker] = new_price
            updates.append(PriceUpdate(
                ticker=ticker,
                price=round(new_price, 2),
                previous_price=round(previous, 2),
                timestamp=now,
            ))

        self._cache.update_batch(updates)
```

## Characteristics

| Property | Value |
|----------|-------|
| Update interval | 500ms |
| Price model | Geometric Brownian motion |
| Correlation | Sector-based via Cholesky decomposition |
| Random events | ~0.2% chance per tick, 2-5% magnitude |
| Seed prices | Realistic for known tickers, $20-$200 random for unknown |
| Dependencies | `numpy` only (for correlation matrix math) |
| Thread safety | Writes to `PriceCache` which uses `threading.Lock` |

## Module Layout

```
backend/
  app/
    market/
      simulator.py     # SimulatorDataSource class
```

The `TickerConfig`, `TICKER_CONFIGS`, GBM math, correlation helpers, and event logic all live in `simulator.py` as module-level functions and constants. No reason to split further — the module stays under ~150 lines.
