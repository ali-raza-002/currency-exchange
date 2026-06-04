"""The seam between "where rates come from" and "what we do with them".

Anything that can produce a dated set of euro reference rates satisfies
:class:`RateProvider`. Adding a second upstream means adding one module and
one registry entry -- no service or route changes.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass, field
from datetime import date
from typing import Protocol, runtime_checkable

BASE_CURRENCY = 'EUR'

CURRENCY_CODE = re.compile(r'[A-Z]{3}')


class RateProviderError(RuntimeError):
    """Upstream was unreachable or returned something unusable."""


@dataclass(frozen=True, slots=True)
class RateSnapshot:
    """All quotes published for a single day, keyed by ISO 4217 code.

    ``rates`` maps a currency code to the units of that currency bought by
    one euro. The base currency itself is never a key.
    """

    rate_date: date
    rates: dict[str, float] = field(default_factory=dict)
    base: str = BASE_CURRENCY

    def __post_init__(self) -> None:
        if not self.rates:
            raise ValueError('a snapshot must contain at least one rate')
        if self.base in self.rates:
            raise ValueError(
                f'the base currency {self.base} must not appear in rates'
            )
        for code, rate in self.rates.items():
            if not CURRENCY_CODE.fullmatch(code):
                raise ValueError(f'{code!r} is not an ISO 4217 code')
            # A zero or non-finite rate would later become a division by
            # zero, or an inf/nan that JSON cannot carry.
            if not math.isfinite(rate) or rate <= 0:
                raise ValueError(
                    f'rate for {code} must be positive and finite'
                )

    @property
    def currencies(self) -> list[str]:
        return sorted(self.rates)


@runtime_checkable
class RateProvider(Protocol):
    """Fetches the most recent published snapshot."""

    name: str

    def fetch_latest(self) -> RateSnapshot:
        """Return the newest snapshot, or raise :class:`RateProviderError`."""
        ...
