"""
BTCUSD Polymarket Signal Viewer — FastAPI entry point.

VISUALISATION + PAPER TRADING SIMULATION ONLY. No wallet, no real order
placement, no live Polymarket trading, ever.

Replaces the previous Streamlit app: a background thread per tab now drives
the trading engine independently of any browser page being open, and each
page polls its own live-partial endpoint on its own timer — genuinely
isolating refresh behavior per tab (unlike Streamlit's rerun-the-whole-script
model). See routes/pages.py, routes/api.py, routes/charts.py and
background_worker.py.
"""
from __future__ import annotations
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from background_worker import start_background_threads
from routes import pages, api, charts


@asynccontextmanager
async def lifespan(app: FastAPI):
    start_background_threads()
    yield


app = FastAPI(title="BTCUSD Polymarket Signal Viewer", lifespan=lifespan)

app.mount("/static", StaticFiles(directory="static"), name="static")

app.include_router(pages.router)
app.include_router(api.router)
app.include_router(charts.router)


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=True)
