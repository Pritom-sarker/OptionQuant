"""
HTML-partial endpoints polled by each page's JS. Each renders the *same*
Jinja2 partial the full page includes, so there is exactly one place
defining a tab's live HTML.

Every handler is wrapped so a transient exception (a race on shared state, a
flaky upstream fetch, etc.) never leaks a raw 500 page or crashes the
worker — it's logged server-side and reported as a 503, which the frontend's
poll loop already treats as a failure: it leaves the last-good render in
place and flips the tab's status dot to DISCONNECTED after a few consecutive
failures, rather than overwriting good content with an error.
"""
from __future__ import annotations
import logging

from fastapi import APIRouter, Request
from fastapi.responses import PlainTextResponse

from templates_engine import templates
import view_context as vc

router = APIRouter(prefix="/api")
log = logging.getLogger("api")


def _render_live(request: Request, partial: str, build_ctx):
    try:
        ctx = {"request": request, **build_ctx()}
        return templates.TemplateResponse(request, partial, ctx)
    except Exception:
        log.exception("live endpoint failed: %s", partial)
        return PlainTextResponse("temporarily unavailable", status_code=503)


@router.get("/tab1/live")
def tab1_live(request: Request):
    return _render_live(request, "partials/tab1_live.html", vc.build_tab1_context)


@router.get("/tab2/live")
def tab2_live(request: Request):
    return _render_live(request, "partials/tab2_live.html", vc.build_tab2_context)


@router.get("/tab3/live")
def tab3_live(request: Request):
    return _render_live(request, "partials/tab3_live.html", vc.build_tab3_context)


@router.get("/tab4/live")
def tab4_live(request: Request):
    return _render_live(request, "partials/tab4_live.html", vc.build_tab4_context)


@router.get("/tab5/live")
def tab5_live(request: Request):
    return _render_live(request, "partials/tab5_live.html", vc.build_tab5_context)


@router.get("/tab6/live")
def tab6_live(request: Request):
    return _render_live(request, "partials/tab6_live.html", vc.build_money_management_context)
