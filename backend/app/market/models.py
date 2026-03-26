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
