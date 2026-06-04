"""Converting an amount between two currencies.

The stored rates are euro-based, so any other pair is a cross rate:
one unit of ``source`` is worth ``rate[target] / rate[source]`` units of
``target``, with the euro itself fixed at 1.0.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import ROUND_HALF_UP, Decimal

from sqlalchemy.orm import Session

from app.providers.base import BASE_CURRENCY
from app.services.errors import UnknownCurrencyError
from app.services.rates import RateService, normalise_code

# Enough precision to round-trip a rate without printing float noise.
RATE_STEP = Decimal('0.00000001')
AMOUNT_STEP = Decimal('0.0001')


@dataclass(frozen=True, slots=True)
class Conversion:
    """The answer, plus everything needed to audit it."""

    source: str
    target: str
    amount: float
    converted: float
    rate: float
    rate_date: date
    base: str = BASE_CURRENCY


class ConversionService:
    """Cross-rate conversion against the newest published snapshot."""

    def __init__(self, session: Session) -> None:
        self._rates = RateService(session)

    def convert(self, source: str, target: str, amount: float) -> Conversion:
        source_code = normalise_code(source)
        target_code = normalise_code(target)
        rate_date, snapshot = self._rates.latest_snapshot_rates()

        source_rate = _rate_for(snapshot, source_code)
        target_rate = _rate_for(snapshot, target_code)

        # Binary floats cannot hold 0.825 or 1.01 exactly, so float maths
        # rounds 1.01 EUR at 0.825 down to 0.8332. The sum is done in
        # Decimal, from the shortest decimal spelling of each input, and
        # rounded half-up the way a person checking it by hand would.
        exact_amount = Decimal(str(amount))
        rate = Decimal(str(target_rate)) / Decimal(str(source_rate))
        converted = (exact_amount * rate).quantize(AMOUNT_STEP, ROUND_HALF_UP)
        return Conversion(
            source=source_code,
            target=target_code,
            amount=float(exact_amount),
            converted=float(converted),
            rate=float(rate.quantize(RATE_STEP, ROUND_HALF_UP)),
            rate_date=rate_date,
        )

    def known_currencies(self) -> list[str]:
        return self._rates.known_currencies()


def _rate_for(snapshot: dict[str, float], currency: str) -> float:
    """Euro-relative rate, treating the base currency as exactly 1."""

    if currency == BASE_CURRENCY:
        return 1.0
    try:
        return snapshot[currency]
    except KeyError as exc:
        raise UnknownCurrencyError(currency) from exc
