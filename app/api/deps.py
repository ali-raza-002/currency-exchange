"""Shared FastAPI dependencies.

Routes take services, never sessions or repositories, so swapping the
storage layer would not touch a route signature.
"""

from __future__ import annotations

from datetime import date
from typing import Annotated

from fastapi import Depends, Query
from sqlalchemy.orm import Session

from app.ai import QueryInterpreter, build_interpreter
from app.config import Settings, get_settings
from app.db import get_session
from app.services import ConversionService, RateService

SessionDep = Annotated[Session, Depends(get_session)]
SettingsDep = Annotated[Settings, Depends(get_settings)]

# Letters only, either case; the services upper-case it. Digits and
# symbols are refused here rather than reported as an unknown currency.
CURRENCY_CODE_PATTERN = r'^[A-Za-z]{3}$'

MIN_HISTORY_POINTS = 1
MAX_HISTORY_POINTS = 365
DEFAULT_HISTORY_POINTS = 30


def get_today() -> date:
    """The current date, as a dependency so tests can pin it."""

    return date.today()


TodayDep = Annotated[date, Depends(get_today)]


def get_rate_service(session: SessionDep) -> RateService:
    return RateService(session)


def get_conversion_service(session: SessionDep) -> ConversionService:
    return ConversionService(session)


def get_interpreter(settings: SettingsDep) -> QueryInterpreter:
    return build_interpreter(settings)


RateServiceDep = Annotated[RateService, Depends(get_rate_service)]
ConversionServiceDep = Annotated[
    ConversionService, Depends(get_conversion_service)
]
InterpreterDep = Annotated[QueryInterpreter, Depends(get_interpreter)]


class Pagination:
    """Bounded ``limit``/``offset``, so no request can ask for everything."""

    def __init__(
        self,
        settings: SettingsDep,
        limit: Annotated[
            int | None,
            Query(ge=1, description='Rows per page.'),
        ] = None,
        offset: Annotated[int, Query(ge=0, description='Rows to skip.')] = 0,
    ) -> None:
        requested = limit or settings.default_page_size
        self.limit = min(requested, settings.max_page_size)
        self.offset = offset


PaginationDep = Annotated[Pagination, Depends(Pagination)]
