"""
Server-rendered PNG chart routes. Tab 1's candle chart (matplotlib) and Tab
2's 5 charts (Plotly, exported via kaleido) are rendered fresh on request,
reusing chart_builder.py completely unchanged. Tab 3/4's charts are already
saved to disk by background_worker.py's _save_tab3_charts and are served
directly from tab3_charts/.
"""
from __future__ import annotations
import io
import os

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import Response, FileResponse

import config
import chart_builder as chartb
import view_context as vc
from engine_state import state

router = APIRouter(prefix="/charts")

_PLACEHOLDER_PNG = None   # lazily-built 1x1 transparent PNG for "nothing to show yet"


def _placeholder() -> bytes:
    global _PLACEHOLDER_PNG
    if _PLACEHOLDER_PNG is None:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
        fig, ax = plt.subplots(figsize=(8, 3), dpi=100)
        ax.text(0.5, 0.5, "No data yet", ha="center", va="center", color="#888888")
        ax.set_xticks([]); ax.set_yticks([])
        buf = io.BytesIO()
        fig.savefig(buf, format="png")
        plt.close(fig)
        _PLACEHOLDER_PNG = buf.getvalue()
    return _PLACEHOLDER_PNG


def _matplotlib_png(fig) -> Response:
    buf = io.BytesIO()
    fig.savefig(buf, format="png", dpi=100, bbox_inches="tight")
    import matplotlib.pyplot as plt
    plt.close(fig)
    return Response(content=buf.getvalue(), media_type="image/png",
                     headers={"Cache-Control": "no-store"})


def _plotly_png(fig) -> Response:
    png_bytes = fig.to_image(format="png", scale=2)
    return Response(content=png_bytes, media_type="image/png",
                     headers={"Cache-Control": "no-store"})


@router.get("/tab1/candles.png")
def tab1_candles():
    with state.lock:
        df = state.tab1_df
        computed = state.tab1_computed
        settings = dict(state.tab1_settings)
    if df is None or computed is None:
        return Response(content=_placeholder(), media_type="image/png")
    fig = chartb.build_chart(df, computed["act_ok"], computed["pat_dir"], computed["results"],
                              settings["show_ema"], settings["show_signals"],
                              visible_candles=config.CHART_VISIBLE_CANDLES)
    return _matplotlib_png(fig)


@router.get("/tab2/{name}.png")
def tab2_chart(name: str):
    with state.lock:
        observer = state.tab2_observer
    if observer is None:
        return Response(content=_placeholder(), media_type="image/png")

    builders = {
        "price": chartb.build_tab2_price_chart,
        "pressure": chartb.build_tab2_pressure_chart,
        "depth_bar": chartb.build_tab2_depth_bar_chart,
        "ladder": chartb.build_tab2_ladder_chart,
        "checklist": chartb.build_tab2_checklist,
    }
    builder = builders.get(name)
    if builder is None:
        raise HTTPException(404, "unknown chart")
    return _plotly_png(builder(observer))


@router.get("/tab3/live_price.png")
def tab3_live_price(id: int = Query(None)):
    """id = the candidate's db_id (Tab 3 can have several active slots at
    once); picks the matching one, or the first active slot if omitted."""
    with state.lock:
        slots = list(state.tab3_slots)
    slot = None
    if id is not None:
        slot = next((s for s in slots if s["candidate"].db_id == id), None)
    elif slots:
        slot = slots[0]
    cand_snaps = slot["candidate"].snapshot_history if slot else []
    trade_snaps = slot["trade"].snapshot_history if slot and slot["trade"] else []
    return _matplotlib_png(chartb.build_tab3_live_price_chart(cand_snaps, trade_snaps))


@router.get("/tab3/file")
def tab3_saved_file(path: str = Query(...)):
    chart_dir = os.path.realpath(config.TAB3_CHART_DIR)
    real_path = os.path.realpath(path)
    if not real_path.startswith(chart_dir + os.sep) or not os.path.exists(real_path):
        raise HTTPException(404, "chart not found")
    return FileResponse(real_path, media_type="image/png")


@router.get("/tab6/balance.png")
def tab6_balance():
    ctx = vc.build_money_management_context()
    if not ctx.get("has_trades"):
        return Response(content=_placeholder(), media_type="image/png")
    fig = chartb.build_mm_balance_chart(ctx["hourly"], ctx["settings"]["starting_balance"])
    return _matplotlib_png(fig)


@router.get("/tab6/loss_basket.png")
def tab6_loss_basket():
    ctx = vc.build_money_management_context()
    if not ctx.get("has_trades"):
        return Response(content=_placeholder(), media_type="image/png")
    fig = chartb.build_mm_loss_basket_chart(ctx["curves"])
    return _matplotlib_png(fig)
