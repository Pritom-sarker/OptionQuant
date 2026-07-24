"""Full HTML page routes — one per tab, plus /settings."""
from __future__ import annotations

import time

from fastapi import APIRouter, Request, Form, UploadFile, File
from fastapi.responses import RedirectResponse, Response

import backup
import config
import money_management as mm
import trade_db
from engine_state import state, save_settings
from templates_engine import templates
import view_context as vc

router = APIRouter()


@router.get("/")
def root():
    return RedirectResponse(url="/about")


@router.get("/about")
def about_page(request: Request):
    ctx = {"request": request, "active_tab": "about"}
    return templates.TemplateResponse(request, "about.html", ctx)


@router.get("/tab1")
def tab1_page(request: Request):
    ctx = {"request": request, "active_tab": "tab1", **vc.build_tab1_context()}
    return templates.TemplateResponse(request, "tab1.html", ctx)


@router.get("/tab2")
def tab2_page(request: Request):
    ctx = {"request": request, "active_tab": "tab2", **vc.build_tab2_context()}
    return templates.TemplateResponse(request, "tab2.html", ctx)


@router.get("/live-trade")
def live_trade_page(request: Request):
    ctx = {"request": request, "active_tab": "live_trade", **vc.build_live_trade_context()}
    return templates.TemplateResponse(request, "live_trade.html", ctx)


@router.get("/analytics")
def analytics_page(request: Request):
    ctx = {"request": request, "active_tab": "analytics",
           **vc.build_tab5_context(), **vc.build_money_management_context()}
    return templates.TemplateResponse(request, "analytics.html", ctx)


@router.get("/tab5/trade/{trade_id}")
def trade_detail_page(request: Request, trade_id: int):
    ctx = {"request": request, "active_tab": "analytics", **vc.build_trade_detail_context(trade_id)}
    return templates.TemplateResponse(request, "trade_detail.html", ctx)


@router.get("/tab5/skipped/{candidate_id}")
def skipped_detail_page(request: Request, candidate_id: int):
    ctx = {"request": request, "active_tab": "analytics", **vc.build_skipped_detail_context(candidate_id)}
    return templates.TemplateResponse(request, "skipped_detail.html", ctx)


@router.get("/tab3")
@router.get("/tab4")
def _redirect_to_live_trade():
    return RedirectResponse(url="/live-trade")


@router.get("/tab5")
@router.get("/tab6")
def _redirect_to_analytics():
    return RedirectResponse(url="/analytics")


@router.post("/settings/money_management")
async def settings_money_management(request: Request):
    """
    Tiered Money Management settings (see money_management.py's module
    docstring for the cycle/win-pool model). Parsed from raw form data
    rather than typed Form(...) params because the recovery-tier rows are a
    variable-length repeated group (tier_start[]/tier_end[]/tier_pct[]).
    """
    form = await request.form()

    def f(name: str, default: float = 0.0) -> float:
        return float(form.get(name, default) or default)

    tier_starts = form.getlist("tier_start")
    tier_ends = form.getlist("tier_end")
    tier_pcts = form.getlist("tier_pct")
    tiers = []
    for s, e, p in zip(tier_starts, tier_ends, tier_pcts):
        if not (s and e and p):
            continue
        tiers.append({"start": int(s), "end": int(e), "pct": float(p) / 100.0})

    maximum_cycle_orders = int(f("maximum_cycle_orders", 10))
    tier_errors = mm.validate_tiers(tiers, maximum_cycle_orders)

    with state.lock:
        state.mm_settings = {
            "starting_balance": f("starting_balance", 1000.0),
            "base_stake": f("base_stake", 1.0),
            "static_lp_pct": f("static_lp_pct", 20.0) / 100.0,
            "max_first_order_stake": f("max_first_order_stake", 3.0),
            "maximum_cycle_orders": maximum_cycle_orders,
            "fallback_mode": form.get("fallback_mode", "stop"),
            "cycle_timeout_lp_pct": f("cycle_timeout_lp_pct", 20.0) / 100.0,
            "win_pool_contribution_pct": f("win_pool_contribution_pct", 20.0) / 100.0,
            "win_pool_lp_coverage_pct": f("win_pool_lp_coverage_pct", 50.0) / 100.0,
        }
        if not tier_errors:
            state.mm_tiers = tiers
    save_settings()
    if tier_errors:
        return RedirectResponse(url="/settings?tier_error=1", status_code=303)
    return RedirectResponse(url="/settings?saved=1", status_code=303)


@router.get("/settings")
def settings_page(request: Request, saved: bool = False, reset: bool = False,
                   imported: bool = False, import_error: bool = False, tier_error: bool = False):
    with state.lock:
        tab1 = dict(state.tab1_settings)
        tab3 = dict(state.tab3_settings)
    mm_ctx = vc.build_money_management_context()   # provides settings/tiers/tier_errors for the MM form section
    ctx = {"request": request, "active_tab": "settings", "tab1": tab1, "tab3": tab3,
           "settings": mm_ctx["settings"], "tiers": mm_ctx["tiers"], "tier_errors": mm_ctx["tier_errors"],
           "tier_save_error": tier_error,
           "pattern_options": config.PATTERN_OPTIONS, "pattern_slugs": config.PATTERN_SLUGS,
           "saved": saved, "reset": reset, "imported": imported, "import_error": import_error,
           "engine_health": vc.build_engine_health_context()}
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
    immediate_entry_window_sec: int = Form(...),
    entry_deadline_sec: int = Form(...),
    fast_poll_lead_sec: int = Form(...), fast_poll_interval_sec: float = Form(...),
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
            "immediate_entry_window_sec": immediate_entry_window_sec,
            "entry_deadline_sec": entry_deadline_sec,
            "fast_poll_lead_sec": fast_poll_lead_sec, "fast_poll_interval_sec": fast_poll_interval_sec,
        }
    save_settings()
    return RedirectResponse(url="/settings?saved=1", status_code=303)


@router.post("/settings/reset_database")
def reset_database():
    """
    Wipes every candidate/trade/snapshot row (trade_db.reset_database) — Tab
    3/4/5/6 all go back to a clean slate, and Tab 6's money-management
    sizing naturally resets to base_trade_amount since it derives the loss
    basket from trade_db, not separately-tracked state. Also clears
    state.tab3_slots since those in-memory positions reference row ids that
    just got deleted; a trade already OPEN when this runs is abandoned
    (never settled/recorded) rather than left dangling against a dead id.
    """
    trade_db.reset_database()
    with state.lock:
        state.tab3_slots = []
    return RedirectResponse(url="/settings?reset=1", status_code=303)


@router.get("/settings/backup/export")
def backup_export():
    """Downloads a zip of the live settings file + the live SQLite DB file,
    unchanged — see backup.py's module docstring for why raw files instead
    of a JSON row dump."""
    data = backup.export_backup()
    filename = f"optionquant_backup_{time.strftime('%Y%m%d_%H%M%S')}.zip"
    return Response(content=data, media_type="application/zip",
                     headers={"Content-Disposition": f'attachment; filename="{filename}"'})


@router.post("/settings/backup/import")
async def backup_import(backup_file: UploadFile = File(...)):
    """Restores settings + database from a previously exported zip. Fully
    replaces whatever's currently running — see backup.import_backup's
    docstring."""
    data = await backup_file.read()
    try:
        backup.import_backup(data)
    except Exception:
        return RedirectResponse(url="/settings?import_error=1", status_code=303)
    return RedirectResponse(url="/settings?imported=1", status_code=303)
