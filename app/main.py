"""Hermes Agent Dashboard — main FastAPI application."""

from __future__ import annotations

from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.middleware.base import BaseHTTPMiddleware

from . import analytics, system_health
from .auth import is_authenticated, router as auth_router
from .database import close_dbs, init_dashboard_db, init_state_db
from .proxy import close_client as close_anthropic_client, router as proxy_router
from .openai_proxy import close_client as close_openai_client, router as openai_proxy_router

BASE_DIR = Path(__file__).resolve().parent.parent


@asynccontextmanager
async def lifespan(app: FastAPI):
    await init_dashboard_db()
    await init_state_db()
    yield
    await close_dbs()
    await close_anthropic_client()
    await close_openai_client()


app = FastAPI(title="Hermes Dashboard", lifespan=lifespan)

from jinja2 import Environment, FileSystemLoader
_jinja_env = Environment(
    loader=FileSystemLoader(str(BASE_DIR / "templates")),
    auto_reload=True,
    cache_size=0,
)
templates = Jinja2Templates(directory=str(BASE_DIR / "templates"))
templates.env = _jinja_env


class AuthMiddleware(BaseHTTPMiddleware):
    OPEN_PATHS = {"/login", "/v1/", "/health"}

    async def dispatch(self, request: Request, call_next):
        path = request.url.path
        if any(path.startswith(p) for p in self.OPEN_PATHS):
            return await call_next(request)
        if path.startswith("/static"):
            return await call_next(request)
        if not is_authenticated(request):
            return RedirectResponse("/login", status_code=302)
        return await call_next(request)


app.add_middleware(AuthMiddleware)

app.mount("/static", StaticFiles(directory=str(BASE_DIR / "static")), name="static")

app.include_router(openai_proxy_router)
app.include_router(proxy_router)
app.include_router(auth_router)


@app.get("/health")
async def health():
    return {"status": "ok"}


@app.get("/")
async def dashboard_page(request: Request):
    return templates.TemplateResponse(request, "index.html")


# ── API endpoints for dashboard data ─────────────────────────────────

@app.get("/api/kpis")
async def api_kpis():
    return await analytics.get_kpis()


@app.get("/api/daily-usage")
async def api_daily_usage(days: int = 30):
    return await analytics.get_daily_usage(days)


@app.get("/api/daily-cost")
async def api_daily_cost(days: int = 30):
    return await analytics.get_daily_cost(days)


@app.get("/api/platform-usage")
async def api_platform_usage(days: int = 30):
    return await analytics.get_platform_usage(days)


@app.get("/api/platform-summary")
async def api_platform_summary(days: int = 30):
    return await analytics.get_platform_summary(days)


@app.get("/api/tool-usage")
async def api_tool_usage(days: int = 30):
    return await analytics.get_tool_usage(days)


@app.get("/api/tool-usage-timeline")
async def api_tool_usage_timeline(days: int = 30):
    return await analytics.get_tool_usage_over_time(days)


@app.get("/api/model-distribution")
async def api_model_distribution(days: int = 30):
    return await analytics.get_model_distribution(days)


@app.get("/api/hourly-heatmap")
async def api_hourly_heatmap(days: int = 30):
    return await analytics.get_hourly_heatmap(days)


@app.get("/api/context-depth")
async def api_context_depth(days: int = 30):
    return await analytics.get_context_depth(days)


@app.get("/api/output-distribution")
async def api_output_distribution(days: int = 30):
    return await analytics.get_output_distribution(days)


@app.get("/api/errors")
async def api_errors(days: int = 30):
    return await analytics.get_error_stats(days)


@app.get("/api/recent-requests")
async def api_recent_requests(limit: int = 100):
    return await analytics.get_recent_requests(limit)


@app.get("/api/top-cost-days")
async def api_top_cost_days(limit: int = 10):
    return await analytics.get_top_cost_days(limit)


@app.get("/api/cache-efficiency")
async def api_cache_efficiency(days: int = 30):
    return await analytics.get_cache_efficiency(days)


@app.get("/api/recent-sessions")
async def api_recent_sessions(limit: int = 20):
    return await analytics.get_recent_sessions(limit)


@app.get("/api/system-health")
async def api_system_health():
    return await system_health.get_system_health()


@app.get("/api/uptime-timeline")
async def api_uptime_timeline(hours: int = 168):
    return await system_health.get_uptime_timeline(hours)


@app.get("/api/cron-jobs")
async def api_cron_jobs():
    return await system_health.get_cron_jobs()


@app.get("/api/error-log-stats")
async def api_error_log_stats(days: int = 30):
    from .analytics import get_error_log_stats
    return await get_error_log_stats(days)


@app.get("/api/token-analysis")
async def api_token_analysis(days: int = 30):
    from .analytics import get_token_analysis
    return await get_token_analysis(days)


@app.get("/api/token-forensics")
async def api_token_forensics():
    from .analytics import get_token_forensics
    return await get_token_forensics()
