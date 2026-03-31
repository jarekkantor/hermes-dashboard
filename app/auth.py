"""Secret-code authentication with signed session cookies."""

from __future__ import annotations

import hashlib
import hmac
import secrets
from datetime import datetime, timedelta, timezone
from functools import wraps

from fastapi import APIRouter, Request, Response
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from itsdangerous import BadSignature, URLSafeTimedSerializer

from .config import DASHBOARD_SECRET, SESSION_TTL_DAYS

router = APIRouter()

_templates = None

def _get_templates():
    global _templates
    if _templates is None:
        from pathlib import Path
        from jinja2 import Environment, FileSystemLoader
        tpl_dir = str(Path(__file__).resolve().parent.parent / "templates")
        env = Environment(loader=FileSystemLoader(tpl_dir), auto_reload=True, cache_size=0)
        _templates = Jinja2Templates(directory=tpl_dir)
        _templates.env = env
    return _templates

_signer: URLSafeTimedSerializer | None = None

COOKIE_NAME = "hermes_dash_session"
MAX_AGE = SESSION_TTL_DAYS * 86400


def _get_signer() -> URLSafeTimedSerializer:
    global _signer
    if _signer is None:
        _signer = URLSafeTimedSerializer(DASHBOARD_SECRET, salt="hermes-dash")
    return _signer


def is_authenticated(request: Request) -> bool:
    token = request.cookies.get(COOKIE_NAME)
    if not token:
        return False
    try:
        data = _get_signer().loads(token, max_age=MAX_AGE)
        return data.get("auth") == "ok"
    except (BadSignature, Exception):
        return False


@router.get("/login", response_class=HTMLResponse)
async def login_page(request: Request, error: str = ""):
    if is_authenticated(request):
        return RedirectResponse("/", status_code=302)
    return _get_templates().TemplateResponse(request, "login.html", {"error": error})


@router.post("/login")
async def login_submit(request: Request):
    form = await request.form()
    code = form.get("code", "")

    if not DASHBOARD_SECRET:
        return RedirectResponse("/login?error=Dashboard+secret+not+configured", status_code=302)

    if not hmac.compare_digest(str(code), DASHBOARD_SECRET):
        return RedirectResponse("/login?error=Invalid+code", status_code=302)

    token = _get_signer().dumps({"auth": "ok"})
    response = RedirectResponse("/", status_code=302)
    response.set_cookie(
        COOKIE_NAME,
        token,
        max_age=MAX_AGE,
        httponly=True,
        secure=True,
        samesite="lax",
    )
    return response


@router.get("/logout")
async def logout(request: Request):
    response = RedirectResponse("/login", status_code=302)
    response.delete_cookie(COOKIE_NAME)
    return response
