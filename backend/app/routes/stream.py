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
