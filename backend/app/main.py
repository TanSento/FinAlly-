"""FastAPI application with market data lifecycle management."""

from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.responses import JSONResponse

from app.market import PriceCache, create_market_data_source

DEFAULT_TICKERS = ["AAPL", "GOOGL", "MSFT", "AMZN", "TSLA", "NVDA", "META", "JPM", "V", "NFLX"]


@asynccontextmanager
async def lifespan(app: FastAPI):
    # --- Startup ---
    cache = PriceCache()
    source = create_market_data_source(cache)
    await source.start(DEFAULT_TICKERS)

    app.state.price_cache = cache
    app.state.market_data = source

    yield

    # --- Shutdown ---
    await source.stop()


app = FastAPI(title="FinAlly Backend", lifespan=lifespan)

from app.routes.stream import router as stream_router  # noqa: E402
app.include_router(stream_router)


@app.get("/api/health")
async def health():
    """Health check endpoint."""
    return JSONResponse({"status": "ok"})
