from datetime import date

import httpx
import pytest
from universe_scanner.clients.jquants import JQuantsApiVersion, JQuantsClient, JQuantsError


@pytest.mark.asyncio
async def test_v2_listed_info_uses_api_key_and_data_pagination():
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        assert request.headers["x-api-key"] == "api-key"
        assert request.url.path == "/v2/equities/master"
        if "pagination_key=next" in str(request.url):
            return httpx.Response(200, json={"data": [{"Code": "4385"}]})
        return httpx.Response(200, json={"data": [{"Code": "7203"}], "pagination_key": "next"})

    async with JQuantsClient(api_key="api-key", api_version=JQuantsApiVersion.V2) as client:
        assert client._client is not None
        await client._client.aclose()
        client._client = httpx.AsyncClient(
            base_url="https://api.jquants.com/v2",
            transport=httpx.MockTransport(handler),
        )
        rows = await client.listed_info(as_of=date(2026, 4, 20))

    assert rows == [{"Code": "7203"}, {"Code": "4385"}]
    assert len(requests) == 2


@pytest.mark.asyncio
async def test_v2_requires_api_key():
    with pytest.raises(JQuantsError, match="requires api_key"):
        async with JQuantsClient(api_version=JQuantsApiVersion.V2):
            pass


@pytest.mark.asyncio
async def test_v1_authenticates_with_refresh_token():
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.url.path == "/v1/token/auth_refresh":
            assert request.url.params["refreshtoken"] == "refresh"
            return httpx.Response(200, json={"idToken": "id-token"})
        assert request.url.path == "/v1/prices/daily_quotes"
        assert request.headers["Authorization"] == "Bearer id-token"
        return httpx.Response(200, json={"daily_quotes": [{"Code": "7203"}]})

    client = JQuantsClient(
        refresh_token="refresh",
        api_version=JQuantsApiVersion.V1,
        base_url="https://api.jquants.com/v1",
    )
    client._client = httpx.AsyncClient(
        base_url="https://api.jquants.com/v1",
        transport=httpx.MockTransport(handler),
    )
    try:
        await client._authenticate_v1()
        rows = await client.daily_quotes(target_date=date(2026, 4, 20))
    finally:
        await client.__aexit__()

    assert rows == [{"Code": "7203"}]
    assert [request.url.path for request in requests] == [
        "/v1/token/auth_refresh",
        "/v1/prices/daily_quotes",
    ]


@pytest.mark.asyncio
async def test_v2_financial_summaries_use_fins_summary_and_data_pagination():
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        assert request.headers["x-api-key"] == "api-key"
        assert request.url.path == "/v2/fins/summary"
        if "pagination_key=next" in str(request.url):
            return httpx.Response(
                200,
                json={
                    "data": [
                        {
                            "DisclosureNumber": "20260420-002",
                            "Code": "6758",
                            "DisclosedDate": "2026-04-20",
                            "DisclosedTime": "15:30:00",
                            "TypeOfDocument": "DividendForecastRevision",
                        }
                    ]
                },
            )
        return httpx.Response(
            200,
            json={
                "data": [
                    {
                        "DisclosureNumber": "20260420-001",
                        "Code": "7203",
                        "DisclosedDate": "2026-04-20",
                        "DisclosedTime": "15:30:00",
                        "TypeOfDocument": "ForecastRevision",
                    }
                ],
                "pagination_key": "next",
            },
        )

    async with JQuantsClient(api_key="api-key", api_version=JQuantsApiVersion.V2) as client:
        assert client._client is not None
        await client._client.aclose()
        client._client = httpx.AsyncClient(
            base_url="https://api.jquants.com/v2",
            transport=httpx.MockTransport(handler),
        )
        rows = await client.financial_summaries_by_date(date(2026, 4, 20))

    assert [row["Code"] for row in rows] == ["7203", "6758"]
    assert len(requests) == 2
    assert requests[0].url.params["date"] == "2026-04-20"
