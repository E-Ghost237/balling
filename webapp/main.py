from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.responses import RedirectResponse
from fastapi.staticfiles import StaticFiles

from webapp.db import get_engine
from webapp.deps import NotAuthenticated, NotAuthorized
from webapp.models import Base
from webapp.routes import admin, customer, feedback, history
from webapp.templates import ASSET_VERSION


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
