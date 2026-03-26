"""Simulated market data using correlated Geometric Brownian Motion."""

import asyncio
import math
import random
from datetime import datetime

import numpy as np

from .cache import PriceCache
from .interface import MarketDataSource
from .models import PriceUpdate, TickerConfig

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
