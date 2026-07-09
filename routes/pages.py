"""Full HTML page routes — one per tab, plus /settings."""
from __future__ import annotations

from fastapi import APIRouter, Request, Form
from fastapi.responses import RedirectResponse

import config
from engine_state import state, save_settings
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


@router.get("/tab6")
def tab6_page(request: Request):
    ctx = {"request": request, "active_tab": "tab6", **vc.build_money_management_context()}
    return templates.TemplateResponse(request, "tab6.html", ctx)


@router.post("/settings/money_management")
def settings_money_management(
    starting_balance: float = Form(...), base_trade_amount: float = Form(...),
    max_trade_amount: float = Form(...), recovery_percent: float = Form(...),
    dynamic_mode: bool = Form(False), profit_split_recovery_pct: float = Form(...),
    reset_mode: str = Form(...), reset_after_n_wins: int = Form(5),
):
    with state.lock:
        state.mm_settings = {
            "starting_balance": starting_balance, "base_trade_amount": base_trade_amount,
            "max_trade_amount": max_trade_amount, "recovery_percent": recovery_percent / 100.0,
            "dynamic_mode": dynamic_mode, "profit_split_recovery_pct": profit_split_recovery_pct / 100.0,
            "reset_mode": reset_mode, "reset_after_n_wins": reset_after_n_wins,
        }
    save_settings()
    return RedirectResponse(url="/tab6", status_code=303)


@router.get("/settings")
def settings_page(request: Request, saved: bool = False):
    with state.lock:
        tab1 = dict(state.tab1_settings)
        tab3 = dict(state.tab3_settings)
    ctx = {"request": request, "active_tab": "settings", "tab1": tab1, "tab3": tab3,
           "pattern_options": config.PATTERN_OPTIONS, "pattern_slugs": config.PATTERN_SLUGS, "saved": saved}
    return templates.TemplateResponse(request, "settings.html", ctx)


@router.post("/settings/tab1")
async def settings_tab1(request: Request):
    """
    One checkbox per base pattern (config.PATTERN_OPTIONS), each with its own
    F1-F5 filter checkboxes — parsed dynamically since the field count varies
    per pattern (FastAPI's Form(...) params can't express that). ATR length/
    mult/SMA length and the display toggles stay single global fields.
    """
    form = await request.form()

    def checked(name: str) -> bool:
        return form.get(name) is not None

    patterns = {}
    for name in config.PATTERN_OPTIONS:
        slug = config.PATTERN_SLUGS[name]
        patterns[name] = {
            "enabled": checked(f"enabled_{slug}"),
            "filters": {key: checked(f"{slug}_{key}") for key in ("f1", "f2", "f3", "f4", "f5")},
        }

    with state.lock:
        state.tab1_settings = {
            "patterns": patterns,
            "atr_length": int(form.get("atr_length", config.DEFAULT_ATR_LENGTH)),
            "atr_mult": float(form.get("atr_mult", config.DEFAULT_ATR_MULTIPLIER)),
            "atr_sma_length": int(form.get("atr_sma_length", config.DEFAULT_ATR_SMA_LENGTH)),
            "min_signals": int(form.get("min_signals", config.DEFAULT_MIN_SIGNALS)),
            "show_ema": checked("show_ema"), "show_signals": checked("show_signals"),
            "early_entry_enabled": checked("early_entry_enabled"),
            "early_entry_lead_sec": int(form.get("early_entry_lead_sec", config.DEFAULT_TAB1_EARLY_ENTRY_LEAD_SEC)),
        }
    save_settings()
    return RedirectResponse(url="/settings?saved=1", status_code=303)


@router.post("/settings/tab3")
def settings_tab3(
    refresh_interval: int = Form(...), chart_refresh_interval: int = Form(...),
    observation_burst: int = Form(...),
    max_entry_price: float = Form(...), hard_block_price: float = Form(...),
    min_profit_factor: float = Form(...), early_exit_loss_pct: int = Form(...),
    pressure_confirm_count: int = Form(...), max_spread: float = Form(...),
    min_liquidity: float = Form(...), pressure_threshold: float = Form(...),
    depth_stable_tolerance: float = Form(...), immediate_mode: bool = Form(False),
):
    with state.lock:
        state.tab3_settings = {
            "refresh_interval": refresh_interval, "chart_refresh_interval": chart_refresh_interval,
            "observation_burst": observation_burst,
            "max_entry_price": max_entry_price, "hard_block_price": hard_block_price,
            "min_profit_factor": min_profit_factor, "early_exit_loss_pct": early_exit_loss_pct / 100.0,
            "pressure_confirm_count": pressure_confirm_count, "max_spread": max_spread,
            "min_liquidity": min_liquidity, "pressure_threshold": pressure_threshold,
            "depth_stable_tolerance": depth_stable_tolerance, "immediate_mode": immediate_mode,
        }
    save_settings()
    return RedirectResponse(url="/settings?saved=1", status_code=303)
