"""Search Console adapter, OAuth flow state and the token vault (offline)."""

import logging
import sys
from datetime import date, timedelta

import pytest
import respx
from pydantic import SecretStr

from app.core.config import Settings
from app.core.exceptions import ConfigurationException
from app.monitoring.errors import (
    GscNotConnected,
    GscOAuthError,
    GscReauthorizationRequired,
    GscStorageUnavailable,
)
from app.monitoring.oauth import AUTH_URL, SCOPE, OAuthFlows
from app.monitoring.vault import DpapiVault, select_vault
from app.tools.errors import (
    ProviderAuthenticationError,
    ProviderRateLimitError,
    ProviderRequestError,
)
from app.tools.search_console_tool import SearchConsoleTool
from tests.gsc_support import (
    CLIENT_ID,
    CLIENT_SECRET,
    PROPERTY,
    REFRESH_TOKEN,
    daily,
    install,
    row,
    settings,
)


@pytest.fixture
def google():
    with respx.mock(assert_all_called=False) as mock:
        yield install(mock)


def tool(
    fake_sleep, *, token: str | None = REFRESH_TOKEN, **overrides
) -> SearchConsoleTool:
    async def provider() -> str | None:
        return token

    return SearchConsoleTool(
        settings(**overrides), refresh_token_provider=provider, sleep=fake_sleep
    )


def yesterday(offset: int = 5) -> str:
    return (date.today() - timedelta(days=offset)).isoformat()


# -- configuration and connection state ------------------------------------------------------------


async def test_unconfigured_client_raises_configuration_error_naming_the_setting(
    fake_sleep,
) -> None:
    unconfigured = SearchConsoleTool(Settings(), sleep=fake_sleep)
    with pytest.raises(ConfigurationException) as excinfo:
        await unconfigured.list_sites()
    assert "GOOGLE_CLIENT_ID" in str(excinfo.value)


async def test_no_refresh_token_means_not_connected_and_no_google_call(
    google, fake_sleep
) -> None:
    with pytest.raises(GscNotConnected):
        await tool(fake_sleep, token=None).list_sites()
    assert google.token_forms == [] and google.auth_headers == []


# -- authorization code exchange --------------------------------------------------------------------


async def test_exchange_code_sends_pkce_verifier_and_returns_a_readonly_grant(
    google, fake_sleep
) -> None:
    grant = await tool(fake_sleep).exchange_code("auth-code", "the-verifier")
    form = google.token_forms[0]
    assert form["grant_type"] == "authorization_code" and form["code"] == "auth-code"
    assert form["code_verifier"] == "the-verifier" and form["client_id"] == CLIENT_ID
    assert grant.refresh_token == "1//NEW-REFRESH-TOKEN-456" and grant.scope == SCOPE


@pytest.mark.parametrize(
    ("scope", "refresh", "message"),
    [
        (SCOPE + " https://www.googleapis.com/auth/webmasters", "1//x", "more access"),
        ("https://www.googleapis.com/auth/userinfo.email", "1//x", "read-only"),
        (SCOPE, None, "no refresh token"),
    ],
)
async def test_exchange_rejects_broader_wrong_or_missing_grants(
    google, fake_sleep, scope, refresh, message
) -> None:
    google.code_scope, google.code_refresh_token = scope, refresh
    with pytest.raises(GscOAuthError, match=message):
        await tool(fake_sleep).exchange_code("c", "v")


async def test_exchange_with_an_expired_code_is_an_oauth_error_not_a_reauthorization(
    google, fake_sleep
) -> None:
    google.token_error = "invalid_grant"
    with pytest.raises(
        GscOAuthError, match="start the connection again|Start the connection again"
    ):
        await tool(fake_sleep).exchange_code("old", "v")


async def test_redirect_uri_mismatch_is_explained_without_echoing_google_details(
    google, fake_sleep
) -> None:
    google.token_error = "redirect_uri_mismatch"
    with pytest.raises(GscOAuthError) as excinfo:
        await tool(fake_sleep).exchange_code("c", "v")
    assert "redirect URI" in str(excinfo.value) and "secret detail" not in str(
        excinfo.value
    )


# -- token refresh ----------------------------------------------------------------------------------


async def test_access_token_is_cached_until_it_expires(google, fake_sleep) -> None:
    t = tool(fake_sleep)
    await t.list_sites()
    await t.list_sites()
    assert google.refresh_calls == 1
    t.forget_access_token()
    await t.list_sites()
    assert google.refresh_calls == 2
    assert google.token_forms[0]["refresh_token"] == REFRESH_TOKEN
    assert (
        google.token_forms[0]["client_secret"] == CLIENT_SECRET
    )  # sent only to Google's endpoint


async def test_revoked_authorization_raises_reauthorization_required(
    google, fake_sleep
) -> None:
    google.token_error = "invalid_grant"
    with pytest.raises(GscReauthorizationRequired):
        await tool(fake_sleep).verify_connection()


async def test_rejected_oauth_client_is_an_authentication_error(
    google, fake_sleep
) -> None:
    google.token_error, google.token_status = "invalid_client", 401
    with pytest.raises(ProviderAuthenticationError, match="OAuth client"):
        await tool(fake_sleep).verify_connection()


async def test_expired_access_token_triggers_one_refresh_and_one_retry(
    google, fake_sleep
) -> None:
    t = tool(fake_sleep)
    await t.list_sites()
    google.expire_access_token()  # Google starts answering 401 for the cached token
    sites = await t.list_sites()
    assert [s.site_url for s in sites] == ["https://www.example.com/", PROPERTY]
    assert (
        google.refresh_calls == 2
    )  # the first token plus exactly one refresh after the 401
    assert (
        google.auth_headers.count(google.auth_headers[-1]) == 1
    )  # the retry used the new token


async def test_a_second_401_after_refreshing_is_reported_not_looped(
    google, fake_sleep
) -> None:
    t = tool(fake_sleep)
    await t.list_sites()
    google.expire_access_token()
    google.reject_auth = True  # Google rejects even the freshly refreshed token
    with pytest.raises(ProviderAuthenticationError):
        await t.list_sites()
    assert google.refresh_calls == 2  # one initial + exactly one retry refresh; no loop


# -- sites -------------------------------------------------------------------------------------------


async def test_list_sites_marks_unverified_properties(google, fake_sleep) -> None:
    sites = {s.site_url: s for s in await tool(fake_sleep).list_sites()}
    assert (
        sites[PROPERTY].verified is True
        and sites[PROPERTY].permission_level == "siteOwner"
    )
    assert sites["https://www.example.com/"].verified is False


# -- Search Analytics ----------------------------------------------------------------------------------


async def test_query_performance_request_shape_and_row_parsing(
    google, fake_sleep
) -> None:
    google.detail_rows = [
        row(
            yesterday(),
            "q",
            "https://example.com/p",
            clicks=2,
            impressions=40,
            position=4.5,
        )
    ]
    google.detail_rows += [
        {
            "keys": ["only-one-key"],
            "clicks": 1,
            "impressions": 1,
            "ctr": 1,
            "position": 1,
        },
        {
            "keys": [yesterday(), "q", "p"],
            "clicks": "x",
            "impressions": 1,
            "ctr": 1,
            "position": 1,
        },
        "junk",
    ]
    rows = await tool(fake_sleep).query_performance(
        PROPERTY, yesterday(10), yesterday(), row_limit=500, start_row=0
    )
    body = google.query_bodies[0]
    assert (
        body["dimensions"] == ["date", "query", "page"] and body["dataState"] == "final"
    )
    assert (
        body["searchType"] == "web" and body["rowLimit"] == 100
    )  # capped by GSC_MAX_ROWS_PER_REQUEST
    assert google.query_paths[0].endswith(
        "/sites/sc-domain:example.com/searchAnalytics/query"
    )
    assert [(r.keys[1], r.clicks, r.position) for r in rows] == [
        ("q", 2.0, 4.5)
    ]  # malformed rows dropped


async def test_url_prefix_property_is_percent_encoded(google, fake_sleep) -> None:
    await tool(fake_sleep).query_performance(
        "https://www.example.com/", yesterday(10), yesterday()
    )
    assert (
        google.query_paths[0].endswith(
            "/sites/https://www.example.com//searchAnalytics/query"
        )
        or "https://www.example.com/" in google.query_paths[0]
    )


async def test_start_row_pages_through_results(google, fake_sleep) -> None:
    google.detail_rows = [
        row(yesterday(), f"q{i}", "https://example.com/p") for i in range(150)
    ]
    t = tool(fake_sleep)
    first = await t.query_performance(PROPERTY, yesterday(10), yesterday())
    second = await t.query_performance(
        PROPERTY, yesterday(10), yesterday(), start_row=len(first)
    )
    assert (len(first), len(second)) == (100, 50) and second[0].keys[1] == "q100"


async def test_totals_request_by_date_only(google, fake_sleep) -> None:
    google.daily_rows = [daily(yesterday())]
    rows = await tool(fake_sleep).query_performance(
        PROPERTY, yesterday(10), yesterday(), dimensions=("date",)
    )
    assert google.query_bodies[0]["dimensions"] == ["date"] and rows[0].clicks == 10.0


@pytest.mark.parametrize(
    ("kwargs", "message"),
    [
        ({"property_id": "example.com"}, "property"),
        ({"property_id": "javascript:alert(1)"}, "property"),
        ({"start_date": "2026-13-01"}, "YYYY-MM-DD"),
        ({"start_date": "2026-11-05", "end_date": "2026-11-01"}, "after end"),
        ({"end_date": (date.today() + timedelta(days=2)).isoformat()}, "future"),
        ({"start_date": "2020-01-01"}, "older than"),
        ({"dimensions": ("date", "bogus")}, "dimensions"),
        ({"dimensions": ("date", "date")}, "dimensions"),
        ({"search_type": "podcast"}, "search type"),
        ({"data_state": "hourly_all"}, "data state"),
    ],
)
async def test_invalid_requests_are_rejected_before_any_google_call(
    google, fake_sleep, kwargs, message
) -> None:
    args = {
        "property_id": PROPERTY,
        "start_date": yesterday(10),
        "end_date": yesterday(),
    } | kwargs
    with pytest.raises(ProviderRequestError, match=message):
        await tool(fake_sleep).query_performance(**args)
    assert google.token_forms == [] and google.query_bodies == []


# -- provider errors -----------------------------------------------------------------------------------


async def test_rate_limit_is_retried_with_backoff_then_succeeds(
    google, fake_sleep, sleeps
) -> None:
    google.query_statuses = [429, 200]
    google.detail_rows = [row(yesterday(), "q", "https://example.com/p")]
    rows = await tool(fake_sleep).query_performance(
        PROPERTY, yesterday(10), yesterday()
    )
    assert len(rows) == 1 and len(sleeps) == 1


async def test_persistent_rate_limit_is_reported(google, fake_sleep) -> None:
    google.query_statuses = [429, 429, 429]
    with pytest.raises(ProviderRateLimitError):
        await tool(fake_sleep).query_performance(PROPERTY, yesterday(10), yesterday())


async def test_access_denied_403_is_not_retried(google, fake_sleep) -> None:
    google.query_statuses = [403]
    with pytest.raises(ProviderAuthenticationError):
        await tool(fake_sleep).query_performance(PROPERTY, yesterday(10), yesterday())
    assert len(google.auth_headers) == 1  # one attempt only


# -- secrets never leak ---------------------------------------------------------------------------------


async def test_tokens_and_secrets_never_appear_in_logs_or_errors(
    google, fake_sleep, caplog
) -> None:
    caplog.set_level(logging.DEBUG)
    t = tool(fake_sleep)
    google.query_statuses = [503, 200]
    await t.query_performance(PROPERTY, yesterday(10), yesterday())
    google.token_error = "invalid_grant"
    t.forget_access_token()
    with pytest.raises(GscReauthorizationRequired) as excinfo:
        await t.list_sites()
    text = caplog.text + str(excinfo.value)
    for secret in (REFRESH_TOKEN, CLIENT_SECRET, "access-token-1"):
        assert secret not in text


def test_the_client_secret_is_not_exposed_by_repr_or_settings_dump() -> None:
    s = settings(google_refresh_token=SecretStr(REFRESH_TOKEN))
    assert CLIENT_SECRET not in repr(s) and REFRESH_TOKEN not in repr(s)
    assert CLIENT_SECRET not in str(s.model_dump()) and REFRESH_TOKEN not in str(
        s.model_dump()
    )


# -- OAuth flow state ---------------------------------------------------------------------------------


class Clock:
    def __init__(self) -> None:
        self.now = 100.0

    def __call__(self) -> float:
        return self.now


def test_authorization_url_requests_only_the_readonly_scope_with_pkce() -> None:
    from urllib.parse import parse_qs, urlsplit

    flows = OAuthFlows(600, Clock())
    start = flows.begin(CLIENT_ID, "http://127.0.0.1:8000/cb")
    parts = urlsplit(start.url)
    query = {k: v[0] for k, v in parse_qs(parts.query).items()}
    assert f"{parts.scheme}://{parts.netloc}{parts.path}" == AUTH_URL
    assert query["scope"] == SCOPE and "readonly" in query["scope"]
    assert query["access_type"] == "offline" and query["prompt"] == "consent"
    assert query["code_challenge_method"] == "S256" and query["response_type"] == "code"
    assert query["include_granted_scopes"] == "false" and len(query["state"]) >= 40


def test_state_is_single_use_expiring_and_bound_to_its_pkce_verifier() -> None:
    import base64
    import hashlib
    from urllib.parse import parse_qs, urlsplit

    clock = Clock()
    flows = OAuthFlows(600, clock)
    query = {
        k: v[0]
        for k, v in parse_qs(
            urlsplit(flows.begin(CLIENT_ID, "http://x/cb").url).query
        ).items()
    }
    verifier = flows.consume(query["state"])
    expected = (
        base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest())
        .rstrip(b"=")
        .decode()
    )
    assert (
        expected == query["code_challenge"]
    )  # the verifier matches the challenge sent to Google
    assert flows.consume(query["state"]) is None  # replay refused
    assert flows.consume("never-issued") is None

    stale = {
        k: v[0]
        for k, v in parse_qs(
            urlsplit(flows.begin(CLIENT_ID, "http://x/cb").url).query
        ).items()
    }
    clock.now += 601
    assert flows.consume(stale["state"]) is None  # expired


def test_pending_authorizations_are_bounded() -> None:
    flows = OAuthFlows(600, Clock())
    from urllib.parse import parse_qs, urlsplit

    first = parse_qs(urlsplit(flows.begin(CLIENT_ID, "http://x/cb").url).query)[
        "state"
    ][0]
    for _ in range(30):
        flows.begin(CLIENT_ID, "http://x/cb")
    assert flows.consume(first) is None  # the oldest was evicted


# -- vault -----------------------------------------------------------------------------------------------


@pytest.mark.skipif(sys.platform != "win32", reason="Windows DPAPI")
def test_dpapi_vault_round_trips_and_never_stores_plaintext() -> None:
    vault = DpapiVault()
    sealed = vault.seal(REFRESH_TOKEN)
    assert REFRESH_TOKEN not in sealed and vault.open(sealed) == REFRESH_TOKEN
    assert vault.seal(REFRESH_TOKEN) != sealed  # DPAPI blobs are salted


@pytest.mark.skipif(sys.platform != "win32", reason="Windows DPAPI")
def test_dpapi_vault_rejects_tampered_or_foreign_blobs() -> None:
    vault = DpapiVault()
    import base64

    raw = bytearray(base64.b64decode(vault.seal(REFRESH_TOKEN)))
    raw[-5] ^= 0xFF
    with pytest.raises(GscStorageUnavailable):
        vault.open(base64.b64encode(bytes(raw)).decode())
    with pytest.raises(GscStorageUnavailable):
        vault.open("not base64 !!")


def test_vault_selection_modes() -> None:
    assert select_vault("environment") is None
    if sys.platform == "win32":
        assert isinstance(select_vault("auto"), DpapiVault)
    else:
        assert select_vault("auto") is None
        with pytest.raises(GscStorageUnavailable):
            select_vault("dpapi")
