"""The FastAPI application. Start it with:  uvicorn app.main:app"""
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from starlette.datastructures import Headers
from starlette.responses import Response

from app import config, db
from app.errors import ServiceError
from app.routers import auth, inventory, products, purchase_orders, reports, sales, suppliers

# Auth uses the Authorization header (no cookies), so credentials stay off
CORS_METHODS = ["GET", "POST", "PUT", "PATCH", "DELETE"]
CORS_HEADERS = ["Authorization", "Content-Type"]


class StrictCORSMiddleware(CORSMiddleware):
    """Starlette's CORS middleware, except a refused preflight carries no Access-Control-* headers at all
    (Starlette still lists the allowed methods and headers on its 400)."""

    def preflight_response(self, request_headers: Headers) -> Response:
        response = super().preflight_response(request_headers)
        if response.status_code != 200:
            for name in [key for key in response.headers if key.lower().startswith("access-control-")]:
                del response.headers[name]
        return response


@asynccontextmanager
async def lifespan(app: FastAPI):
    # The schema is managed by Alembic (docs/DATABASE.md); never run against a database that isn't up to date
    db.check_schema_is_current(db.engine)
    yield


async def service_error_response(request: Request, exc: ServiceError) -> JSONResponse:
    """Services raise app.errors exceptions; they become the same {"detail": ...} responses as HTTPException."""
    return JSONResponse(status_code=exc.status_code, content={"detail": exc.detail})


def create_app() -> FastAPI:
    """Builds the app. Settings are read here, so a bad CORS_ORIGINS or DOCS_ENABLED stops the app before it starts."""
    docs = config.docs_enabled()
    application = FastAPI(
        lifespan=lifespan,
        # DOCS_ENABLED=false removes all three (and Swagger's OAuth redirect page with them)
        docs_url="/docs" if docs else None,
        redoc_url="/redoc" if docs else None,
        openapi_url="/openapi.json" if docs else None,
    )
    application.add_middleware(
        StrictCORSMiddleware,
        allow_origins=config.cors_origins(),
        allow_credentials=False,
        allow_methods=CORS_METHODS,
        allow_headers=CORS_HEADERS,
    )
    application.add_exception_handler(ServiceError, service_error_response)
    for module in (auth, products, inventory, sales, suppliers, purchase_orders, reports):
        application.include_router(module.router)
    return application


app = create_app()
