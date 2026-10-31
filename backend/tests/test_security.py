"""API token authentication, rate limits and the validation-error envelope."""

import pytest
from fastapi.testclient import TestClient

from app.core.config import Settings, get_settings
from app.core.rate_limit import RateLimits, SlidingWindowLimiter
from app.main import create_app
from tests.auth_support import AUTH_HEADERS, TEST_API_TOKEN

PROTECTED_GET = "/api/v1/graph/health"


class FakeClock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


def _app_with_limits(**overrides: int) -> TestClient:
    app = create_app()
    clock = FakeClock()
    app.state.rate_limits = RateLimits.from_settings(Settings(**overrides), clock)
    app.state.test_clock = clock
    return TestClient(app)


# -- authentication ----------------------------------------------------------


def test_health_routes_are_public() -> None:
    client = TestClient(create_app())
    assert client.get("/api/v1/health").status_code == 200
    assert client.get("/").status_code == 200


def test_missing_token_is_401_with_bearer_challenge() -> None:
    response = TestClient(create_app()).get(PROTECTED_GET)
    assert response.status_code == 401
    assert response.headers["www-authenticate"] == "Bearer"
    assert response.json()["error"]["code"] == "authentication_required"


@pytest.mark.parametrize(
    "header",
    [
        "Bearer wrong-token",
        f"Basic {TEST_API_TOKEN}",
        f"Bearer {TEST_API_TOKEN}x",
        f"Bearer {TEST_API_TOKEN[:-1]}",
        "Bearer ",
    ],
)
def test_wrong_or_malformed_token_is_rejected(header: str) -> None:
    response = TestClient(create_app()).get(
        PROTECTED_GET, headers={"Authorization": header}
    )
    assert response.status_code == 401
    assert TEST_API_TOKEN not in response.text


def test_valid_token_reaches_the_route(client: TestClient) -> None:
    response = client.get(PROTECTED_GET)
    assert response.status_code == 200
    assert response.json()["status"] == "not_configured"  # no Neo4j in tests


def test_every_non_health_route_requires_the_token() -> None:
    app = create_app()
    client = TestClient(app, raise_server_exceptions=False)
    public = {"/", "/api/v1/health", "/api/v1/health/ready"}
    checked = 0
    # The OpenAPI schema lists every mounted route (app.routes nests routers).
    for path, operations in app.openapi()["paths"].items():
        if path in public:
            continue
        assert path.startswith("/api/v1/"), path
        concrete = path.replace("{", "").replace("}", "")  # any value; auth runs first
        for method in operations:
            response = client.request(method.upper(), concrete)
            assert response.status_code == 401, (method, path, response.status_code)
            checked += 1
    assert checked > 30


def test_unset_token_refuses_protected_routes_but_app_starts(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("AURORA_API_TOKEN")
    get_settings.cache_clear()
    client = TestClient(create_app())
    assert client.get("/api/v1/health").status_code == 200
    response = client.get(PROTECTED_GET, headers=AUTH_HEADERS)
    assert response.status_code == 503
    assert "AURORA_API_TOKEN" in response.json()["error"]["message"]


@pytest.mark.parametrize("value", ["", "   ", "short-token"])
def test_blank_or_short_token_is_a_configuration_error(
    monkeypatch: pytest.MonkeyPatch, value: str
) -> None:
    monkeypatch.setenv("AURORA_API_TOKEN", value)
    get_settings.cache_clear()
    response = TestClient(create_app()).get(
        PROTECTED_GET, headers={"Authorization": f"Bearer {value}"}
    )
    assert response.status_code == 503
    assert response.json()["error"]["code"] == "configuration_error"


def test_token_is_never_logged(caplog: pytest.LogCaptureFixture) -> None:
    client = TestClient(create_app())
    client.get(PROTECTED_GET, headers={"Authorization": "Bearer guess-1234"})
    client.get(PROTECTED_GET, headers=AUTH_HEADERS)
    assert TEST_API_TOKEN not in caplog.text
    assert "guess-1234" not in caplog.text


# -- brute force and rate limits --------------------------------------------


def test_repeated_wrong_tokens_lock_the_client_out() -> None:
    client = _app_with_limits(api_auth_max_failures=3, api_auth_lockout_seconds=60)
    bad = {"Authorization": "Bearer nope"}
    for _ in range(3):
        assert client.get(PROTECTED_GET, headers=bad).status_code == 401
    locked = client.get(PROTECTED_GET, headers=AUTH_HEADERS)  # even the right token
    assert locked.status_code == 429
    assert int(locked.headers["retry-after"]) >= 1
    client.app.state.test_clock.now += 61
    assert client.get(PROTECTED_GET, headers=AUTH_HEADERS).status_code == 200


def test_missing_header_does_not_count_towards_lockout() -> None:
    client = _app_with_limits(api_auth_max_failures=2)
    for _ in range(5):
        assert client.get(PROTECTED_GET).status_code == 401
    assert client.get(PROTECTED_GET, headers=AUTH_HEADERS).status_code == 200


def test_write_routes_are_rate_limited_per_client() -> None:
    client = _app_with_limits(api_write_rate_limit_per_minute=2)
    body = {"query": ""}  # invalid on purpose: no provider is called
    for _ in range(2):
        response = client.post("/api/v1/research", json=body, headers=AUTH_HEADERS)
        assert response.status_code == 422
    limited = client.post("/api/v1/research", json=body, headers=AUTH_HEADERS)
    assert limited.status_code == 429
    assert limited.json()["error"]["code"] == "rate_limited"
    # reads are not write-limited
    assert client.get(PROTECTED_GET, headers=AUTH_HEADERS).status_code == 200


def test_sliding_window_limiter_expires_and_bounds_keys() -> None:
    clock = FakeClock()
    limiter = SlidingWindowLimiter(2, 10, clock=clock, max_keys=3)
    assert limiter.acquire("a") is None
    assert limiter.acquire("a") is None
    assert limiter.acquire("a") == pytest.approx(10)
    clock.now += 10
    assert limiter.acquire("a") is None
    for key in "bcde":
        limiter.acquire(key)
    assert len(limiter._events) == 3  # oldest keys evicted


# -- validation envelope ------------------------------------------------------


def test_validation_errors_use_the_error_envelope(client: TestClient) -> None:
    secret_like = "do-not-echo-this-value"
    response = client.post(
        "/api/v1/research", json={"query": "", "unexpected": secret_like}
    )
    assert response.status_code == 422
    error = response.json()["error"]
    assert error["code"] == "validation_error"
    assert error["message"].startswith("Request validation failed: body.")
    assert {"location", "message", "type"} <= set(error["details"][0])
    assert secret_like not in response.text
    assert "detail" not in response.json()
