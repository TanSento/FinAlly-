# Massive API Reference (formerly Polygon.io)

REST API for real-time and historical US stock market data.

## Authentication

**Base URL:** `https://api.polygon.io` (redirects to `api.massive.com`)

Two methods — use either:

```
# Query parameter
GET /v2/snapshot/locale/us/markets/stocks/tickers?apiKey=YOUR_KEY

# Authorization header
GET /v2/snapshot/locale/us/markets/stocks/tickers
Authorization: Bearer YOUR_KEY
```

## Rate Limits

| Tier | Calls/Minute | Data Delay | Key Features |
|------|-------------|------------|--------------|
| Free | 5 | 15-min delayed | End-of-day, delayed snapshots |
| Starter (~$29/mo) | 100 | 15-min delayed | More history |
| Developer (~$79/mo) | 1,000 | Real-time | `lastTrade`, `lastQuote` fields |
| Advanced (~$199/mo) | Unlimited | Real-time | Full history |

For the **free tier** (5 calls/min), poll no more than once every 15 seconds.

---

## Key Endpoint: Snapshot All Tickers

**This is the primary endpoint for our project.** One API call returns latest prices for all requested tickers.

```
GET /v2/snapshot/locale/us/markets/stocks/tickers?tickers=AAPL,GOOGL,MSFT
```

### Parameters

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `tickers` | string | No | Comma-separated list (e.g. `AAPL,GOOGL`). Omit for all tickers. |
| `include_otc` | boolean | No | Include OTC securities. Default: false. |

### Response

```json
{
  "count": 1,
  "status": "OK",
  "tickers": [
    {
      "ticker": "AAPL",
      "todaysChange": 1.23,
      "todaysChangePerc": 0.65,
      "updated": 1605192894630916600,
      "day": {
        "o": 189.50,
        "h": 191.20,
        "l": 188.90,
        "c": 190.73,
        "v": 52341000,
        "vw": 190.12
      },
      "prevDay": {
        "o": 188.00,
        "h": 189.80,
        "l": 187.50,
        "c": 189.50,
        "v": 48200000,
        "vw": 188.90
      },
      "min": {
        "o": 190.60,
        "h": 190.80,
        "l": 190.55,
        "c": 190.73,
        "v": 125000,
        "vw": 190.68,
        "av": 52341000,
        "n": 342,
        "t": 1684875540000
      },
      "lastTrade": {
        "p": 190.73,
        "s": 100,
        "x": 11,
        "t": 1605192894630916600,
        "i": "71675577320245",
        "c": [14, 41]
      },
      "lastQuote": {
        "p": 190.72,
        "P": 190.74,
        "s": 200,
        "S": 300,
        "t": 1605192959994246100
      }
    }
  ]
}
```

### Field Reference

**Top-level ticker fields:**

| Field | Type | Description |
|-------|------|-------------|
| `ticker` | string | Symbol (e.g. `AAPL`) |
| `todaysChange` | number | Absolute change from previous close |
| `todaysChangePerc` | number | Percentage change from previous close |
| `updated` | integer | Last updated (Unix **nanoseconds**) |
| `fmv` | number | Fair market value (Business plan only) |

**`day` / `prevDay` — daily OHLCV bar:**

| Field | Description |
|-------|-------------|
| `o` | Open price |
| `h` | High price |
| `l` | Low price |
| `c` | Close price |
| `v` | Volume |
| `vw` | Volume-weighted average price |

**`min` — most recent minute bar:**

| Field | Description |
|-------|-------------|
| `o`, `h`, `l`, `c` | Minute OHLC |
| `v` | Minute volume |
| `vw` | Minute VWAP |
| `av` | Accumulated daily volume |
| `n` | Number of trades in minute |
| `t` | Timestamp (Unix **milliseconds**) |

**`lastTrade` — most recent trade (Developer+ plans):**

| Field | Description |
|-------|-------------|
| `p` | Trade price |
| `s` | Trade size (shares) |
| `x` | Exchange ID |
| `t` | Timestamp (Unix **nanoseconds**) |
| `i` | Trade ID |
| `c` | Condition codes (array of ints) |

**`lastQuote` — most recent quote (Developer+ plans):**

| Field | Description |
|-------|-------------|
| `p` | Bid price |
| `P` | Ask price |
| `s` | Bid size |
| `S` | Ask size |
| `t` | Timestamp (Unix **nanoseconds**) |

---

## Single Ticker Snapshot

```
GET /v2/snapshot/locale/us/markets/stocks/tickers/{stocksTicker}
```

Same fields as above, but response has a singular `ticker` object (not a `tickers` array):

```json
{
  "status": "OK",
  "request_id": "abc123",
  "ticker": { /* same structure as above */ }
}
```

---

## Aggregates (OHLC Bars)

For historical data or end-of-day prices.

```
GET /v2/aggs/ticker/{stocksTicker}/range/{multiplier}/{timespan}/{from}/{to}
```

### Path Parameters

| Parameter | Type | Description |
|-----------|------|-------------|
| `stocksTicker` | string | Ticker symbol, e.g. `AAPL` |
| `multiplier` | integer | Timespan multiplier, e.g. `1` |
| `timespan` | string | `second`, `minute`, `hour`, `day`, `week`, `month`, `quarter`, `year` |
| `from` | string | Start date (`YYYY-MM-DD`) or Unix ms timestamp |
| `to` | string | End date (`YYYY-MM-DD`) or Unix ms timestamp |

### Query Parameters

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `adjusted` | boolean | true | Adjust for splits |
| `sort` | string | `asc` | `asc` or `desc` |
| `limit` | integer | 5000 | Max results (up to 50,000) |

### Response

```json
{
  "ticker": "AAPL",
  "adjusted": true,
  "queryCount": 2,
  "resultsCount": 2,
  "status": "OK",
  "results": [
    {
      "o": 130.465,
      "h": 132.63,
      "l": 130.23,
      "c": 131.03,
      "v": 70790813,
      "vw": 131.6292,
      "n": 609890,
      "t": 1617249600000
    }
  ],
  "next_url": "https://api.polygon.io/v2/aggs/..."
}
```

Result fields: `o` (open), `h` (high), `l` (low), `c` (close), `v` (volume), `vw` (VWAP), `n` (trades), `t` (timestamp in Unix **milliseconds**).

---

## Daily Open/Close

```
GET /v1/open-close/{stocksTicker}/{date}
```

| Parameter | Type | Description |
|-----------|------|-------------|
| `stocksTicker` | string | Ticker symbol |
| `date` | string | Date as `YYYY-MM-DD` |
| `adjusted` | boolean | Default: true |

### Response

```json
{
  "status": "OK",
  "symbol": "AAPL",
  "from": "2023-01-09",
  "open": 324.66,
  "high": 326.2,
  "low": 322.3,
  "close": 325.12,
  "volume": 26122646,
  "afterHours": 322.1,
  "preMarket": 324.5
}
```

---

## Ticker Reference / Search

```
GET /v3/reference/tickers?search=apple&market=stocks&active=true
```

| Parameter | Type | Description |
|-----------|------|-------------|
| `ticker` | string | Exact ticker match |
| `search` | string | Search by company name (partial match) |
| `type` | string | e.g. `CS` for common stock |
| `market` | string | `stocks`, `crypto`, `fx`, `otc`, `indices` |
| `active` | boolean | Default: true |
| `limit` | integer | Default: 100 (max 1,000) |

### Response

```json
{
  "status": "OK",
  "count": 1,
  "results": [
    {
      "ticker": "AAPL",
      "name": "Apple Inc.",
      "market": "stocks",
      "locale": "us",
      "primary_exchange": "XNAS",
      "type": "CS",
      "active": true,
      "currency_name": "usd"
    }
  ]
}
```

---

## Python Code Examples

### Fetch Snapshot for Multiple Tickers

```python
import httpx

API_KEY = "your-api-key"
BASE_URL = "https://api.polygon.io"

async def fetch_snapshots(tickers: list[str]) -> dict:
    """Fetch latest price snapshots for the given tickers."""
    async with httpx.AsyncClient() as client:
        response = await client.get(
            f"{BASE_URL}/v2/snapshot/locale/us/markets/stocks/tickers",
            params={"tickers": ",".join(tickers), "apiKey": API_KEY},
            timeout=10.0,
        )
        response.raise_for_status()
        return response.json()
```

### Parse Snapshot into Price Data

```python
from dataclasses import dataclass
from datetime import datetime


@dataclass
class PriceUpdate:
    ticker: str
    price: float
    previous_price: float
    change: float
    change_percent: float
    timestamp: datetime


def parse_snapshot(ticker_data: dict) -> PriceUpdate:
    """Extract a PriceUpdate from a Massive snapshot ticker object."""
    # Use day close as current price; fall back to minute close
    price = ticker_data["day"]["c"]
    if ticker_data.get("min"):
        price = ticker_data["min"]["c"]

    prev_close = ticker_data["prevDay"]["c"]

    # updated field is in nanoseconds
    ts = datetime.fromtimestamp(ticker_data["updated"] / 1_000_000_000)

    return PriceUpdate(
        ticker=ticker_data["ticker"],
        price=price,
        previous_price=prev_close,
        change=ticker_data["todaysChange"],
        change_percent=ticker_data["todaysChangePerc"],
        timestamp=ts,
    )


def parse_all_snapshots(response: dict) -> list[PriceUpdate]:
    """Parse the full snapshot response into a list of PriceUpdates."""
    return [parse_snapshot(t) for t in response.get("tickers", [])]
```

### Polling Loop

```python
import asyncio

POLL_INTERVAL = 15  # seconds (safe for free tier: 5 calls/min)

async def poll_prices(tickers: list[str]):
    """Continuously poll for price updates."""
    while True:
        try:
            data = await fetch_snapshots(tickers)
            updates = parse_all_snapshots(data)
            for u in updates:
                print(f"{u.ticker}: ${u.price:.2f} ({u.change_percent:+.2f}%)")
        except httpx.HTTPStatusError as e:
            print(f"API error: {e.response.status_code}")
        await asyncio.sleep(POLL_INTERVAL)
```

---

## Gotchas

1. **Timestamp units vary** — `updated`, `lastTrade.t`, `lastQuote.t` are Unix **nanoseconds**. Aggregate `t` and `min.t` are Unix **milliseconds**. Dividing by the wrong factor is a common bug.
2. **Snapshot data gap** — Snapshots clear at 3:30 AM EST and repopulate from 4:00 AM EST. During this window, data may be empty or stale.
3. **Plan-gated fields** — `lastTrade`, `lastQuote` require Developer+ plans. `fmv` requires Business plan. On free tier, these fields may be absent.
4. **Free tier delay** — Snapshot prices are delayed 15 minutes on the free tier.
5. **Tickers are case-sensitive** — Always use uppercase for US stocks (e.g. `AAPL`, not `aapl`).
6. **Rate limit headers** — The API does not return standard rate-limit headers; you must track call frequency yourself.
