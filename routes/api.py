"""
HTML-partial endpoints polled by each page's JS. Each renders the *same*
Jinja2 partial the full page includes, so there is exactly one place
defining a tab's live HTML.
"""
from __future__ import annotations

from fastapi import APIRouter, Request

from templates_engine import templates
import view_context as vc

router = APIRouter(prefix="/api")


@router.get("/tab1/live")
def tab1_live(request: Request):
    ctx = {"request": request, **vc.build_tab1_context()}
    return templates.TemplateResponse(request, "partials/tab1_live.html", ctx)


@router.get("/tab2/live")
def tab2_live(request: Request):
    ctx = {"request": request, **vc.build_tab2_context()}
    return templates.TemplateResponse(request, "partials/tab2_live.html", ctx)


@router.get("/tab3/live")
def tab3_live(request: Request):
    ctx = {"request": request, **vc.build_tab3_context()}
    return templates.TemplateResponse(request, "partials/tab3_live.html", ctx)


@router.get("/tab4/live")
def tab4_live(request: Request):
    ctx = {"request": request, **vc.build_tab4_context()}
    return templates.TemplateResponse(request, "partials/tab4_live.html", ctx)


@router.get("/tab5/live")
def tab5_live(request: Request):
    ctx = {"request": request, **vc.build_tab5_context()}
    return templates.TemplateResponse(request, "partials/tab5_live.html", ctx)
