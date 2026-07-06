"""Full HTML page routes — one per tab, plus /settings."""
from __future__ import annotations

from fastapi import APIRouter, Request, Form
from fastapi.responses import RedirectResponse

import config
from engine_state import state
from templates_engine import templates
import view_context as vc

router = APIRouter()


@router.get("/")
def root():
    return RedirectResponse(url="/tab1")


@router.get("/tab1")
def tab1_page(request: Request):
    ctx = {"request": request, "active_tab": "tab1", **vc.build_tab1_context()}
    return templates.TemplateResponse(request, "tab1.html", ctx)


@router.get("/tab2")
def tab2_page(request: Request):
    ctx = {"request": request, "active_tab": "tab2", **vc.build_tab2_context()}
    return templates.TemplateResponse(request, "tab2.html", ctx)


@router.get("/tab3")
def tab3_page(request: Request):
    ctx = {"request": request, "active_tab": "tab3", **vc.build_tab3_context()}
    return templates.TemplateResponse(request, "tab3.html", ctx)


@router.get("/tab4")
def tab4_page(request: Request):
    ctx = {"request": request, "active_tab": "tab4", **vc.build_tab4_context()}
    return templates.TemplateResponse(request, "tab4.html", ctx)


@router.get("/tab5")
def tab5_page(request: Request):
    ctx = {"request": request, "active_tab": "tab5", **vc.build_tab5_context()}
    return templates.TemplateResponse(request, "tab5.html", ctx)


@router.get("/tab5/trade/{trade_id}")
def trade_detail_page(request: Request, trade_id: int):
    ctx = {"request": request, "active_tab": "tab5", **vc.build_trade_detail_context(trade_id)}
    return templates.TemplateResponse(request, "trade_detail.html", ctx)


@router.get("/settings")
def settings_page(request: Request, saved: bool = False):
    with state.lock:
        tab1 = dict(state.tab1_settings)
        tab3 = dict(state.tab3_settings)
    ctx = {"request": request, "active_tab": "settings", "tab1": tab1, "tab3": tab3,
           "pattern_options": config.PATTERN_OPTIONS, "saved": saved}
    return templates.TemplateResponse(request, "settings.html", ctx)


@router.post("/settings/tab1")
def settings_tab1(
    mode: str = Form(...), atr_length: int = Form(...), atr_mult: float = Form(...),
    atr_sma_length: int = Form(...), min_signals: int = Form(...),
    f1: bool = Form(False), f2: bool = Form(False), f3: bool = Form(False),
    f4: bool = Form(False), f5: bool = Form(False),
    show_ema: bool = Form(False), show_signals: bool = Form(False),
):
    with state.lock:
        state.tab1_settings = {
            "mode": mode, "atr_length": atr_length, "atr_mult": atr_mult,
            "atr_sma_length": atr_sma_length, "min_signals": min_signals,
            "enabled": {"f1": f1, "f2": f2, "f3": f3, "f4": f4, "f5": f5},
            "show_ema": show_ema, "show_signals": show_signals,
        }
    return RedirectResponse(url="/settings?saved=1", status_code=303)


@router.post("/settings/tab3")
def settings_tab3(
    refresh_interval: int = Form(...), chart_refresh_interval: int = Form(...),
    observation_burst: int = Form(...), stake: float = Form(...),
    max_entry_price: float = Form(...), hard_block_price: float = Form(...),
    min_profit_factor: float = Form(...), early_exit_loss_pct: int = Form(...),
    pressure_confirm_count: int = Form(...), max_spread: float = Form(...),
    min_liquidity: float = Form(...), pressure_threshold: float = Form(...),
    depth_stable_tolerance: float = Form(...),
):
    with state.lock:
        state.tab3_settings = {
            "refresh_interval": refresh_interval, "chart_refresh_interval": chart_refresh_interval,
            "observation_burst": observation_burst, "stake": stake,
            "max_entry_price": max_entry_price, "hard_block_price": hard_block_price,
            "min_profit_factor": min_profit_factor, "early_exit_loss_pct": early_exit_loss_pct / 100.0,
            "pressure_confirm_count": pressure_confirm_count, "max_spread": max_spread,
            "min_liquidity": min_liquidity, "pressure_threshold": pressure_threshold,
            "depth_stable_tolerance": depth_stable_tolerance,
        }
    return RedirectResponse(url="/settings?saved=1", status_code=303)
