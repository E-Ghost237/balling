from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.responses import RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from webapp.db import get_engine
from webapp.deps import NotAuthenticated, NotAuthorized
from webapp.models import Base
from webapp.routes import admin, customer, feedback, history


@asynccontextmanager
async def lifespan(app: FastAPI):
    engine = get_engine()
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    yield


app = FastAPI(title="Balling Predictions", lifespan=lifespan)
app.mount("/static", StaticFiles(directory="webapp/static"), name="static")
app.mount("/assets", StaticFiles(directory="static"), name="assets")

templates = Jinja2Templates(directory="webapp/templates")


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
