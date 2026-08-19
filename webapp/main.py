from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, PlainTextResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from sqlalchemy import text

from webapp.db import get_engine
from webapp.deps import NotAuthenticated, NotAuthorized
from webapp.i18n import LocaleMiddleware, localize_path
from webapp.models import Base
from webapp.routes import admin, customer, feedback, history, webhooks
from webapp.templates import ASSET_VERSION

SITE_URL = "https://ballingpronostics.site"


@asynccontextmanager
async def lifespan(app: FastAPI):
    engine = get_engine()
    async with engine.begin() as connection:
        # One-time: the `predictions` table is new (moved out of
        # data/football.db — see models.Prediction). If this is the first
        # boot to create it, any prediction_id already sitting on usage_log
        # rows points at the old, now-gone SQLite-backed table — left as-is
        # those values would collide with this brand-new id sequence and
        # show a *different* match's data instead of "unavailable".
        is_first_boot_with_predictions_table = (
            await connection.execute(text("SELECT to_regclass('predictions')"))
        ).scalar_one() is None
        await connection.run_sync(Base.metadata.create_all)
        if is_first_boot_with_predictions_table:
            await connection.execute(text("UPDATE usage_log SET prediction_id = NULL"))
    yield


app = FastAPI(title="Balling Predictions", lifespan=lifespan)
app.add_middleware(LocaleMiddleware)
app.mount("/static", StaticFiles(directory="webapp/static"), name="static")
app.mount("/assets", StaticFiles(directory="static"), name="assets")


@app.get("/__version__")
async def app_version() -> dict[str, str]:
    """Polled client-side (see base.html) so an open tab notices a deploy
    happened and reloads itself instead of showing stale markup/CSS."""
    return {"version": ASSET_VERSION}


# --- SEO: crawlers/search engines look for these at the domain root,
# regardless of what's mounted under /static or /assets. ---


@app.get("/favicon.ico", include_in_schema=False)
async def favicon_ico() -> FileResponse:
    return FileResponse("static/favicon.ico", media_type="image/x-icon")


@app.get("/robots.txt", include_in_schema=False)
async def robots_txt() -> PlainTextResponse:
    # Everything below requires login anyway (a crawler can't get past the
    # login wall), but disallowing explicitly keeps them out of the index
    # even if a link to one leaks somewhere. / , /login, /register and
    # static assets are allowed by omission.
    private_paths = [
        "/admin", "/simulate", "/account", "/history", "/feedback", "/payment",
        "/verify-email", "/reset-password", "/forgot-password", "/logout",
    ]
    disallow_lines = "".join(f"Disallow: {p}\n" for p in private_paths)
    return PlainTextResponse(
        f"User-agent: *\n{disallow_lines}\nSitemap: {SITE_URL}/sitemap.xml\n"
    )


@app.get("/sitemap.xml", include_in_schema=False)
async def sitemap_xml() -> Response:
    pages = ["/", "/login", "/register", "/privacy", "/terms"]
    entries = []
    for p in pages:
        en_url, fr_url = f"{SITE_URL}{p}", f"{SITE_URL}{localize_path(p, 'fr')}"
        entries.append(
            f'<url><loc>{en_url}</loc>'
            f'<xhtml:link rel="alternate" hreflang="en" href="{en_url}"/>'
            f'<xhtml:link rel="alternate" hreflang="fr" href="{fr_url}"/></url>'
        )
        entries.append(
            f'<url><loc>{fr_url}</loc>'
            f'<xhtml:link rel="alternate" hreflang="en" href="{en_url}"/>'
            f'<xhtml:link rel="alternate" hreflang="fr" href="{fr_url}"/></url>'
        )
    xml = (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9" '
        'xmlns:xhtml="http://www.w3.org/1999/xhtml">' + "".join(entries) + "</urlset>"
    )
    return Response(content=xml, media_type="application/xml")


@app.exception_handler(NotAuthenticated)
async def handle_not_authenticated(request: Request, exc: NotAuthenticated) -> RedirectResponse:
    next_path = localize_path(request.url.path, getattr(request.state, "locale", "en"))
    login_path = localize_path("/login", getattr(request.state, "locale", "en"))
    return RedirectResponse(url=f"{login_path}?next={next_path}", status_code=303)


@app.exception_handler(NotAuthorized)
async def handle_not_authorized(request: Request, exc: NotAuthorized) -> RedirectResponse:
    return RedirectResponse(
        url=localize_path("/forbidden", getattr(request.state, "locale", "en")), status_code=303
    )


app.include_router(customer.router)
app.include_router(admin.router)
app.include_router(history.router)
app.include_router(feedback.router)
app.include_router(webhooks.router)
