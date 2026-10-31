"""Graph schema registry, constraints/indexes, Neo4j client lifecycle, health and errors."""

import logging

import pytest
from pydantic import SecretStr

from app.core.config import Settings
from app.graph.graph_config import GraphConfig
from app.graph.graph_exceptions import (
    GraphAuthenticationError,
    GraphNotConfiguredError,
    GraphQueryError,
    GraphUnavailableError,
)
from app.graph.graph_health import GraphStatus, check_health
from app.graph.graph_schema import (
    CONSTRAINTS,
    INDEXES,
    NODE_PROPERTIES,
    REGISTRY,
    Label,
    Rel,
    check_relationship,
)
from app.graph.neo4j_client import Neo4jClient, map_driver_error

SECRET = "neo4j-super-secret-pw"


# -- schema ------------------------------------------------------------------------------


def test_all_required_labels_and_relationships_exist() -> None:
    assert {lab.value for lab in Label} == {
        "Company", "Product", "Service", "Technology", "Feature", "Integration",
        "Keyword", "Topic", "Article", "Source", "Claim",
    }  # fmt: skip
    required = {
        ("Company", "OFFERS", "Product"), ("Company", "PROVIDES", "Service"),
        ("Product", "USES", "Technology"), ("Product", "HAS_FEATURE", "Feature"),
        ("Product", "INTEGRATES_WITH", "Integration"), ("Company", "COMPETES_WITH", "Company"),
        ("Product", "COMPETES_WITH", "Product"), ("Keyword", "BELONGS_TO", "Topic"),
        ("Article", "TARGETS", "Keyword"), ("Article", "COVERS", "Topic"),
        ("Article", "LINKS_TO", "Article"), ("Source", "SUPPORTS", "Claim"),
        ("Claim", "MENTIONS", "Company"), ("Claim", "MENTIONS", "Product"),
        ("Claim", "MENTIONS", "Service"), ("Claim", "MENTIONS", "Technology"),
        ("Claim", "MENTIONS", "Feature"), ("Article", "CITES", "Source"),
        ("Product", "RELATED_TO", "Product"), ("Company", "OPERATES_IN", "Topic"),
        ("Technology", "RELATED_TO", "Technology"),
    }  # fmt: skip
    for src, rel, dst in required:
        assert check_relationship(rel, src, dst) is None, (src, rel, dst)
    assert set(REGISTRY) == set(Rel)
    assert NODE_PROPERTIES[Label.COMPANY][:2] == ("canonical_name", "normalized_name")


@pytest.mark.parametrize(
    ("rel", "src", "dst", "reason"),
    [
        ("OFFERS", "Product", "Company", "cannot be the source"),  # wrong direction
        ("HAS_FEATURE", "Company", "Feature", "cannot be the source"),
        ("COMPETES_WITH", "Company", "Product", "same type"),
        ("SUPPORTS", "Claim", "Source", "cannot be the source"),
        ("DROP_ALL", "Company", "Product", "unknown"),
        ("OFFERS", "Company); MATCH (n) DETACH DELETE n //", "Product", "unknown"),
    ],
)
def test_invalid_relationships_are_rejected(rel, src, dst, reason) -> None:
    assert reason in (check_relationship(rel, src, dst) or "")


def test_constraints_and_indexes_are_idempotent_and_community_safe() -> None:
    statements = [s for _, s in [*CONSTRAINTS, *INDEXES]]
    assert all("IF NOT EXISTS" in s for s in statements)
    assert not any(
        word in s.upper()
        for s in statements
        for word in ("DROP", "APOC", "NODE KEY", "IS NOT NULL")
    )
    names = [n for n, _ in [*CONSTRAINTS, *INDEXES]]
    assert len(names) == len(set(names))
    assert "graphnode_id_unique" in names and all(
        f"{lab.value.lower()}_id_unique" in names for lab in Label
    )
    for wanted in ("company_canonical_name_idx", "company_normalized_name_idx", "company_official_domain_idx",
                   "source_url_idx", "source_domain_idx", "keyword_normalized_keyword_idx", "claim_verification_status_idx"):  # fmt: skip
        assert wanted in names


# -- config / client ---------------------------------------------------------------------


def configured(**kw) -> Settings:
    return Settings(neo4j_password=SecretStr(SECRET), **kw)


def test_config_never_exposes_password() -> None:
    cfg = GraphConfig.from_settings(configured())
    assert cfg.is_configured and SECRET not in repr(cfg)
    assert not GraphConfig.from_settings(Settings()).is_configured


def test_client_is_lazy_and_needs_credentials() -> None:
    calls = []
    client = Neo4jClient(configured(), driver_factory=lambda *a, **k: calls.append(1))
    assert calls == [] and not client.is_connected  # nothing at construction
    with pytest.raises(GraphNotConfiguredError):
        Neo4jClient(Settings())._get_driver()


class FakeResult:
    def __init__(self, rows):
        self.rows = rows

    async def fetch(self, n):
        class R:
            def __init__(self, d):
                self.d = d

            def data(self):
                return self.d

        return [R(r) for r in self.rows]


class FakeTx:
    def __init__(self, log, rows):
        self.log, self.rows = log, rows

    async def run(self, query, params):
        self.log.append((query, params))
        return FakeResult(self.rows)


class FakeSession:
    def __init__(self, driver, database):
        self.driver, self.database = driver, database

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    async def execute_read(self, fn):
        self.driver.modes.append("read")
        if self.driver.error:
            raise self.driver.error
        return await fn(FakeTx(self.driver.log, self.driver.rows))

    async def execute_write(self, fn):
        self.driver.modes.append("write")
        if self.driver.error:
            raise self.driver.error
        return await fn(FakeTx(self.driver.log, self.driver.rows))


class FakeDriver:
    def __init__(self, *, rows=None, error=None, verify_error=None):
        self.rows, self.error, self.verify_error = rows or [], error, verify_error
        self.log, self.modes, self.closed, self.kwargs, self.databases = (
            [],
            [],
            False,
            {},
            [],
        )

    def session(self, database=None):
        self.databases.append(database)
        return FakeSession(self, database)

    async def verify_connectivity(self):
        if self.verify_error:
            raise self.verify_error

    async def get_server_info(self):
        class Info:
            agent = "Neo4j/5.26.0"
            protocol_version = (5, 4)

        return Info()

    async def close(self):
        self.closed = True


def client_with(driver: FakeDriver, **settings) -> Neo4jClient:
    def factory(uri, **kwargs):
        driver.kwargs = {"uri": uri, **kwargs}
        return driver

    return Neo4jClient(configured(**settings), driver_factory=factory)


async def test_queries_use_managed_transactions_timeouts_database_and_pool() -> None:
    driver = FakeDriver(rows=[{"ok": 1}])
    client = client_with(
        driver,
        neo4j_database="aurora",
        neo4j_query_timeout_seconds=7,
        neo4j_max_connection_pool_size=9,
    )
    assert await client.execute_read("RETURN $x AS ok", {"x": 1}) == [{"ok": 1}]
    await client.execute_write("RETURN 1", {})
    (query, params), _ = driver.log
    assert query.timeout == 7 and params == {"x": 1}
    assert driver.modes == ["read", "write"] and driver.databases == [
        "aurora",
        "aurora",
    ]
    assert (
        driver.kwargs["max_connection_pool_size"] == 9
        and driver.kwargs["auth"][1] == SECRET
    )
    await client.close()
    assert driver.closed and not client.is_connected


def test_driver_errors_are_mapped_without_messages() -> None:
    import neo4j.exceptions as ne

    assert isinstance(
        map_driver_error(ne.ServiceUnavailable("host down " + SECRET)),
        GraphUnavailableError,
    )
    assert isinstance(map_driver_error(ne.SessionExpired("x")), GraphUnavailableError)
    auth = ne.AuthError("bad password " + SECRET)
    assert isinstance(map_driver_error(auth), GraphAuthenticationError)
    assert SECRET not in str(map_driver_error(auth))
    syntax = ne.CypherSyntaxError("Invalid input 'MATC'")
    mapped = map_driver_error(syntax)
    assert isinstance(mapped, GraphQueryError) and "MATC" not in str(mapped)
    assert GraphUnavailableError("x").retryable and not GraphQueryError().retryable


async def test_client_errors_surface_as_graph_errors(caplog) -> None:
    import neo4j.exceptions as ne

    client = client_with(FakeDriver(error=ne.ServiceUnavailable("down")))
    with caplog.at_level(logging.DEBUG), pytest.raises(GraphUnavailableError):
        await client.execute_read("RETURN 1", {})
    assert SECRET not in caplog.text


async def test_health_states_are_accurate() -> None:
    import neo4j.exceptions as ne

    assert (
        await check_health(Neo4jClient(Settings()))
    ).status is GraphStatus.NOT_CONFIGURED
    passive = await check_health(client_with(FakeDriver()), probe=False)
    assert (
        passive.status is GraphStatus.DISCONNECTED
    )  # never "connected" without a check
    ok = await check_health(client_with(FakeDriver(rows=[{"version": "5.1.0"}])))
    assert ok.status is GraphStatus.CONNECTED and ok.server["agent"].startswith("Neo4j")
    down = await check_health(
        client_with(FakeDriver(verify_error=ne.ServiceUnavailable("x")))
    )
    assert down.status is GraphStatus.UNAVAILABLE
    bad = await check_health(client_with(FakeDriver(verify_error=ne.AuthError("x"))))
    assert (
        bad.status is GraphStatus.UNAVAILABLE and bad.detail == "authentication failed"
    )


def test_importing_graph_modules_opens_no_connection() -> None:
    import importlib

    for mod in ("app.graph.neo4j_client", "app.graph.graph_repository", "app.graph.graph_sync_service",
                "app.graph.graph_query_service", "app.api.routes.graph"):  # fmt: skip
        importlib.import_module(mod)
    from app.graph import neo4j_client

    assert neo4j_client._shared is None or not neo4j_client._shared.is_connected
