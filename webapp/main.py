from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, PlainTextResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles

from webapp.db import get_engine
from webapp.deps import NotAuthenticated, NotAuthorized
from webapp.models import Base
from webapp.routes import admin, customer, feedback, history
from webapp.templates import ASSET_VERSION

SITE_URL = "https://ballingpronostics.site"


@asynccontextmanager
async def lifespan(app: FastAPI):
    engine = get_engine()
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    yield


app = FastAPI(title="Balling Predictions", lifespan=lifespan)
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
    pages = ["/", "/login", "/register"]
    urls = "".join(f"<url><loc>{SITE_URL}{p}</loc></url>" for p in pages)
    xml = f'<?xml version="1.0" encoding="UTF-8"?><urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">{urls}</urlset>'
    return Response(content=xml, media_type="application/xml")


@app.exception_handler(NotAuthenticated)
async def handle_not_authenticated(request: Request, exc: NotAuthenticated) -> RedirectResponse:
    return RedirectResponse(url=f"/login?next={request.url.path}", status_code=303)


@app.exception_handler(NotAuthorized)
async def handle_not_authorized(request: Request, exc: NotAuthorized) -> RedirectResponse:
    return RedirectResponse(url="/forbidden", status_code=303)


app.include_router(customer.router)
app.include_router(admin.router)
app.include_router(history.router)
app.include_router(feedback.router)
