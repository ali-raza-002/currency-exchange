"""HTTP behaviour: status codes, shapes and the error contract."""

from __future__ import annotations

import inspect
from datetime import timedelta
from http import HTTPStatus
from urllib.parse import quote as quote_path

import pytest
from fastapi.testclient import TestClient

from app import __version__
from app.api.deps import get_today
from app.api.routes import convert, health, rates
from tests.conftest import TODAY

TODAY_ISO = TODAY.isoformat()


def test_index_lists_the_endpoints(client: TestClient) -> None:
    body = client.get('/').json()

    assert body['service'] == 'currency-exchange'
    assert body['version'] == __version__
    assert body['base_currency'] == 'EUR'
    assert 'GET /v1/convert' in body['endpoints']


def test_health_reports_empty_before_any_ingest(client: TestClient) -> None:
    body = client.get('/healthz').json()

    assert body['status'] == 'empty'
    assert body['database'] == 'reachable'
    assert body['latest_rate_date'] is None


def test_health_reports_freshness_once_seeded(
    seeded_client: TestClient,
) -> None:
    body = seeded_client.get('/healthz').json()

    assert body['status'] == 'ok'
    assert body['latest_rate_date'] == TODAY_ISO
    assert body['rates_provider'] == 'static'
    assert body['nlq_interpreter'] == 'rules'


@pytest.mark.parametrize(
    ('days_later', 'status'), [(0, 'ok'), (5, 'ok'), (6, 'stale')]
)
def test_health_flags_a_snapshot_older_than_the_threshold(
    seeded_client: TestClient, days_later: int, status: str
) -> None:
    app = seeded_client.app
    app.dependency_overrides[get_today] = lambda: (
        TODAY + timedelta(days=days_later)
    )

    body = seeded_client.get('/healthz').json()

    assert body['status'] == status
    assert body['latest_rate_age_days'] == days_later
    assert body['latest_rate_date'] == TODAY_ISO


def test_health_never_fails_the_probe(client: TestClient) -> None:
    assert client.get('/healthz').status_code == HTTPStatus.OK


def test_listing_rates_returns_the_latest_snapshot(
    seeded_client: TestClient,
) -> None:
    body = seeded_client.get('/v1/rates').json()

    assert body['rate_date'] == TODAY_ISO
    assert body['base'] == 'EUR'
    assert body['total'] == 4
    assert [row['currency'] for row in body['rates']] == [
        'GBP',
        'JPY',
        'SEK',
        'USD',
    ]


def test_listing_rates_pages(seeded_client: TestClient) -> None:
    body = seeded_client.get('/v1/rates?limit=2&offset=2').json()

    assert body['has_more'] is False
    assert body['offset'] == 2
    assert len(body['rates']) == 2


def test_the_page_size_is_capped(seeded_client: TestClient) -> None:
    body = seeded_client.get('/v1/rates?limit=10000').json()

    assert body['limit'] == 200


def test_listing_rates_filters_by_currency(
    seeded_client: TestClient,
) -> None:
    body = seeded_client.get('/v1/rates?currency=USD&currency=JPY').json()

    assert body['total'] == 2
    assert {row['currency'] for row in body['rates']} == {'USD', 'JPY'}


def test_listing_rates_before_any_ingest_is_a_conflict(
    client: TestClient,
) -> None:
    response = client.get('/v1/rates')

    assert response.status_code == HTTPStatus.CONFLICT
    assert 'app.cli seed' in response.json()['detail']


def test_a_single_quote_carries_its_change(
    seeded_client: TestClient,
) -> None:
    body = seeded_client.get('/v1/rates/USD').json()

    assert body['currency'] == 'USD'
    assert body['rate_date'] == TODAY_ISO
    assert body['change'] is not None


def test_a_lower_case_code_is_accepted(seeded_client: TestClient) -> None:
    assert seeded_client.get('/v1/rates/usd').json()['currency'] == 'USD'


def test_an_unknown_currency_is_a_404(seeded_client: TestClient) -> None:
    response = seeded_client.get('/v1/rates/XYZ')

    assert response.status_code == HTTPStatus.NOT_FOUND
    assert 'XYZ' in response.json()['detail']


def test_a_malformed_code_is_rejected_before_the_service(
    seeded_client: TestClient,
) -> None:
    assert (
        seeded_client.get('/v1/rates/DOLLAR').status_code
        == HTTPStatus.UNPROCESSABLE_ENTITY
    )


@pytest.mark.parametrize('code', ['123', 'U$D', 'U-D'])
def test_a_code_that_is_not_three_letters_is_rejected(
    seeded_client: TestClient, code: str
) -> None:
    quote = seeded_client.get('/v1/rates/' + quote_path(code))
    conversion = seeded_client.get(
        '/v1/convert', params={'from': code, 'to': 'USD'}
    )

    assert quote.status_code == HTTPStatus.UNPROCESSABLE_ENTITY
    assert conversion.status_code == HTTPStatus.UNPROCESSABLE_ENTITY


def test_history_returns_every_stored_publication_date(
    seeded_client: TestClient,
) -> None:
    body = seeded_client.get('/v1/rates/USD/history').json()

    assert body['currency'] == 'USD'
    assert len(body['points']) == 2
    assert body['points'][0]['rate_date'] == TODAY_ISO


def test_history_honours_the_points_limit(
    seeded_client: TestClient,
) -> None:
    body = seeded_client.get('/v1/rates/USD/history?points=1').json()

    assert len(body['points']) == 1


def test_history_rejects_an_out_of_range_limit(
    seeded_client: TestClient,
) -> None:
    assert (
        seeded_client.get('/v1/rates/USD/history?points=0').status_code
        == HTTPStatus.UNPROCESSABLE_ENTITY
    )


def test_conversion_returns_the_rate_it_used(
    seeded_client: TestClient,
) -> None:
    body = seeded_client.get('/v1/convert?from=USD&to=JPY&amount=100').json()

    assert body['source'] == 'USD'
    assert body['target'] == 'JPY'
    assert body['amount'] == 100
    assert body['rate_date'] == TODAY_ISO
    assert body['converted'] > 0


def test_conversion_defaults_to_one_unit(
    seeded_client: TestClient,
) -> None:
    body = seeded_client.get('/v1/convert?from=EUR&to=USD').json()

    assert body['amount'] == 1
    assert body['converted'] == 1.05


def test_conversion_rejects_a_non_positive_amount(
    seeded_client: TestClient,
) -> None:
    response = seeded_client.get('/v1/convert?from=EUR&to=USD&amount=0')

    assert response.status_code == HTTPStatus.UNPROCESSABLE_ENTITY


@pytest.mark.parametrize('amount', ['inf', 'nan', '1e13'])
def test_conversion_rejects_a_non_finite_or_huge_amount(
    seeded_client: TestClient, amount: str
) -> None:
    response = seeded_client.get(
        f'/v1/convert?from=EUR&to=USD&amount={amount}'
    )

    assert response.status_code == HTTPStatus.UNPROCESSABLE_ENTITY


@pytest.mark.parametrize(
    'question', ['0 dollars in yen', 'convert 5000bn dollars to yen']
)
def test_asking_with_an_unusable_amount_is_a_422_not_a_500(
    seeded_client: TestClient, question: str
) -> None:
    response = seeded_client.post(
        '/v1/convert/ask', json={'question': question}
    )

    assert response.status_code == HTTPStatus.UNPROCESSABLE_ENTITY
    assert 'amount must be greater than 0' in response.json()['detail']


def test_conversion_with_an_unknown_currency_is_a_404(
    seeded_client: TestClient,
) -> None:
    response = seeded_client.get('/v1/convert?from=USD&to=XYZ')

    assert response.status_code == HTTPStatus.NOT_FOUND


def test_asking_in_words_answers_from_stored_rates(
    seeded_client: TestClient,
) -> None:
    response = seeded_client.post(
        '/v1/convert/ask',
        json={'question': 'how much is 250 dollars in japanese yen'},
    )
    body = response.json()

    assert response.status_code == HTTPStatus.OK
    assert body['interpreter'] == 'rules'
    assert body['conversion']['source'] == 'USD'
    assert body['conversion']['target'] == 'JPY'
    assert body['conversion']['amount'] == 250
    assert body['conversion']['rate_date'] == TODAY_ISO


def test_an_unreadable_question_is_a_422(
    seeded_client: TestClient,
) -> None:
    response = seeded_client.post(
        '/v1/convert/ask', json={'question': 'what is the weather'}
    )

    assert response.status_code == HTTPStatus.UNPROCESSABLE_ENTITY
    assert 'currencies' in response.json()['detail']


def test_an_empty_question_is_rejected_by_validation(
    seeded_client: TestClient,
) -> None:
    response = seeded_client.post('/v1/convert/ask', json={'question': ''})

    assert response.status_code == HTTPStatus.UNPROCESSABLE_ENTITY


def test_the_openapi_document_is_generated(client: TestClient) -> None:
    schema = client.get('/openapi.json').json()

    assert schema['info']['version'] == __version__
    assert '/v1/convert/ask' in schema['paths']


def test_handlers_that_block_run_in_the_threadpool() -> None:
    """SQLAlchemy and the Anthropic client are synchronous.

    Called from an ``async def`` handler they would stall the event loop
    for every other request, so only handlers free of I/O may be async.
    """

    blocking = {
        route.path: inspect.iscoroutinefunction(route.endpoint)
        for router in (convert.router, health.router, rates.router)
        for route in router.routes
        if route.path != '/'
    }

    assert blocking
    assert not any(blocking.values()), blocking
