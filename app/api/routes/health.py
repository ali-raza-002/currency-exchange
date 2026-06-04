"""Service index and health probe."""

from __future__ import annotations

from fastapi import APIRouter
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError

from app import __version__
from app.api.deps import RateServiceDep, SessionDep, SettingsDep, TodayDep
from app.providers.base import BASE_CURRENCY
from app.schemas import HealthOut, ServiceIndexOut

router = APIRouter(tags=['service'])

ENDPOINTS = [
    'GET /healthz',
    'GET /v1/rates',
    'GET /v1/rates/{currency}',
    'GET /v1/rates/{currency}/history',
    'GET /v1/convert',
    'POST /v1/convert/ask',
]


@router.get('/', response_model=ServiceIndexOut, summary='Service index')
async def index() -> ServiceIndexOut:
    return ServiceIndexOut(
        service='currency-exchange',
        version=__version__,
        base_currency=BASE_CURRENCY,
        docs='/docs',
        endpoints=ENDPOINTS,
    )


@router.get(
    '/healthz',
    response_model=HealthOut,
    summary='Liveness and data freshness',
)
def healthz(
    session: SessionDep,
    rates: RateServiceDep,
    settings: SettingsDep,
    today: TodayDep,
) -> HealthOut:
    """Reports whether the database answers and how fresh the data is.

    Always returns 200 -- a probe that 500s tells you less than one that
    says exactly which part is unhappy.
    """

    try:
        session.execute(text('SELECT 1'))
    except SQLAlchemyError as exc:
        return HealthOut(
            status='degraded',
            version=__version__,
            database=f'unreachable: {exc.__class__.__name__}',
            rates_provider=settings.rates_provider,
            nlq_interpreter=settings.nlq_provider,
        )

    latest = rates.latest_rate_date()
    currencies = rates.known_currencies() if latest else []
    # The scheduler only logs a failed fetch, so a dead upstream would
    # otherwise go unnoticed while every answer quietly ages.
    age = (today - latest).days if latest else None
    if age is None:
        status = 'empty'
    elif age > settings.stale_after_days:
        status = 'stale'
    else:
        status = 'ok'
    return HealthOut(
        status=status,
        version=__version__,
        database='reachable',
        rates_provider=settings.rates_provider,
        nlq_interpreter=settings.nlq_provider,
        latest_rate_date=latest,
        latest_rate_age_days=age,
        stored_currencies=len(currencies),
    )
