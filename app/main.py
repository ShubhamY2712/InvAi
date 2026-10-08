"""The FastAPI application. Start it with:  uvicorn app.main:app"""
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from app import db
from app.errors import ServiceError
from app.routers import auth, inventory, products, purchase_orders, reports, sales, suppliers


@asynccontextmanager
async def lifespan(app: FastAPI):
    # The schema is managed by Alembic (docs/DATABASE.md); never run against a database that isn't up to date
    db.check_schema_is_current(db.engine)
    yield

app = FastAPI(lifespan=lifespan)


@app.exception_handler(ServiceError)
async def service_error_response(request: Request, exc: ServiceError) -> JSONResponse:
    """Services raise app.errors exceptions; they become the same {"detail": ...} responses as HTTPException."""
    return JSONResponse(status_code=exc.status_code, content={"detail": exc.detail})


for module in (auth, products, inventory, sales, suppliers, purchase_orders, reports):
    app.include_router(module.router)
