"""Phase 7 Stage 1 — master-account registry API tests.

Driven through the real FastAPI app + a real MasterAccountService backed by an
in-memory fake Supabase. The fake enforces the same two constraints the
migration does (UNIQUE login, partial-UNIQUE assigned_product) so the
one-master-per-product invariant is exercised end-to-end, not mocked away.
"""

from __future__ import annotations

import copy
import uuid

import pytest
from fastapi.testclient import TestClient

from src.config import Settings
from src.core.master_account_service import MasterAccountService
from src.engine.runtime import AppRuntime, set_runtime
from src.main import app


# -------------------- in-memory fake Supabase --------------------


class _UniqueViolation(Exception):
    code = "23505"

    def __init__(self, msg: str = "duplicate key value violates unique constraint"):
        super().__init__(msg)


class _Result:
    def __init__(self, data):
        self.data = data


class _Query:
    """Minimal query-builder mimicking the supabase-py chains the service uses."""

    def __init__(self, table: "_Table"):
        self._t = table
        self._op = "select"
        self._payload = None
        self._filters: list[tuple[str, object]] = []
        self._limit: int | None = None

    # builders
    def select(self, *_a, **_k):
        self._op = "select"
        return self

    def insert(self, row):
        self._op = "insert"
        self._payload = row
        return self

    def update(self, payload):
        self._op = "update"
        self._payload = payload
        return self

    def delete(self):
        self._op = "delete"
        return self

    def eq(self, col, val):
        self._filters.append((col, val))
        return self

    def order(self, col, desc=False):
        self._order = (col, desc)
        return self

    def limit(self, n):
        self._limit = n
        return self

    # exec
    def _match(self, row) -> bool:
        return all(row.get(c) == v for c, v in self._filters)

    def execute(self):
        rows = self._t.rows
        if self._op == "select":
            out = [copy.deepcopy(r) for r in rows if self._match(r)]
            if getattr(self, "_order", None):
                col, desc = self._order
                out.sort(key=lambda r: r.get(col) or "", reverse=desc)
            if self._limit is not None:
                out = out[: self._limit]
            return _Result(out)
        if self._op == "insert":
            new = copy.deepcopy(self._payload)
            new.setdefault("id", str(uuid.uuid4()))
            self._t._check_login_unique(new)
            self._t._check_product_unique(new)
            self._t.rows.append(new)
            return _Result([copy.deepcopy(new)])
        if self._op == "update":
            updated = []
            for r in rows:
                if self._match(r):
                    candidate = {**r, **self._payload}
                    self._t._check_product_unique(candidate, exclude_id=r["id"])
                    r.update(self._payload)
                    updated.append(copy.deepcopy(r))
            return _Result(updated)
        if self._op == "delete":
            keep = [r for r in rows if not self._match(r)]
            removed = [r for r in rows if self._match(r)]
            self._t.rows = keep
            return _Result([copy.deepcopy(r) for r in removed])
        raise AssertionError(self._op)


class _Table:
    def __init__(self):
        self.rows: list[dict] = []

    def _check_login_unique(self, row):
        for r in self.rows:
            if r.get("login") == row.get("login"):
                raise _UniqueViolation("duplicate key (login)")

    def _check_product_unique(self, row, exclude_id=None):
        p = row.get("assigned_product")
        if p is None:
            return
        for r in self.rows:
            if r["id"] == exclude_id:
                continue
            if r.get("assigned_product") == p:
                raise _UniqueViolation("duplicate key (assigned_product)")


class FakeSupabase:
    """Raw-client shape: `.table(name)` → query builder."""

    def __init__(self):
        self._tables: dict[str, _Table] = {}

    def table(self, name):
        self._tables.setdefault(name, _Table())
        return _Query(self._tables[name])

    # convenience for tests to seed/inspect rows directly
    def _seed(self, name, row):
        t = self._tables.setdefault(name, _Table())
        t.rows.append(row)


# -------------------- fixtures --------------------


def _settings() -> Settings:
    return Settings(
        SUPABASE_URL="https://x.supabase.co",
        SUPABASE_SERVICE_ROLE_KEY="k",
        METAAPI_TOKEN="t",
        METAAPI_MASTER_ACCOUNT_ID="00000000-0000-0000-0000-000000000000",
        APP_ENV="development",
        LOG_LEVEL="INFO",
    )


_HDR = {"X-Admin-Key": "test-admin-secret"}


@pytest.fixture
def client(monkeypatch):
    from unittest.mock import MagicMock

    monkeypatch.setenv("ADMIN_KEY", "test-admin-secret")
    rt = AppRuntime(_settings(), MagicMock(), MagicMock(), MagicMock())
    fake = FakeSupabase()
    rt.master_accounts = MasterAccountService(fake)  # type: ignore[assignment]
    set_runtime(rt)
    try:
        yield TestClient(app), fake
    finally:
        set_runtime(None)


def _register(c, **overrides) -> dict:
    body = {
        "login": "100001",
        "broker": "InterStellarFinancial",
        "server": "InterStellarFinancial-Server",
        "currency": "USC",
        "metaapi_account_id": "acc-" + uuid.uuid4().hex[:8],
        "metaapi_region": "eu-west",
    }
    body.update(overrides)
    r = c.post("/masters", headers=_HDR, json=body)
    return r


# -------------------- auth gating --------------------


def test_requires_admin_key(client):
    c, _ = client
    assert c.get("/masters").status_code == 401
    assert c.post("/masters", json={}).status_code == 401


def test_503_when_admin_key_unset(monkeypatch):
    from unittest.mock import MagicMock

    monkeypatch.delenv("ADMIN_KEY", raising=False)
    rt = AppRuntime(_settings(), MagicMock(), MagicMock(), MagicMock())
    rt.master_accounts = MasterAccountService(FakeSupabase())  # type: ignore[assignment]
    set_runtime(rt)
    try:
        assert TestClient(app).get("/masters").status_code == 503
    finally:
        set_runtime(None)


# -------------------- register + list --------------------


def test_post_masters_creates_a_row(client):
    c, _ = client
    r = _register(c, login="100777")
    assert r.status_code == 200
    body = r.json()
    assert body["login"] == "100777"
    assert body["status"] == "standby"
    assert body["assigned_product"] is None
    assert "id" in body


def test_get_returns_expected_shape(client):
    c, _ = client
    _register(c, login="100001")
    _register(c, login="100002")
    r = c.get("/masters", headers=_HDR)
    assert r.status_code == 200
    body = r.json()
    assert body["count"] == 2
    logins = {m["login"] for m in body["masters"]}
    assert logins == {"100001", "100002"}
    # full shape on each row
    sample = body["masters"][0]
    for key in (
        "id",
        "login",
        "broker",
        "server",
        "currency",
        "metaapi_account_id",
        "metaapi_region",
        "assigned_product",
        "status",
    ):
        assert key in sample


def test_post_masters_without_currency_succeeds(client):
    """The Add-master UI no longer sends currency — POST must succeed and store
    it NULL, to be auto-filled from MetaApi on first connect."""
    c, _ = client
    body = {
        "login": "100888",
        "broker": "InterStellarFinancial",
        "server": "InterStellarFinancial-Server",
        "metaapi_account_id": "acc-" + uuid.uuid4().hex[:8],
        "metaapi_region": "eu-west",
    }
    r = c.post("/masters", headers=_HDR, json=body)
    assert r.status_code == 200
    out = r.json()
    assert out["login"] == "100888"
    assert out["status"] == "standby"
    # currency is present in the shape but unset (NULL) until MetaApi fills it.
    assert out.get("currency") in (None, "")


async def test_backfill_currency_fills_only_when_empty(client):
    """backfill_currency sets currency on the matching row only when it is empty,
    and never clobbers a row that already has one (e.g. the seeded master)."""
    c, fake = client
    acc = "acc-" + uuid.uuid4().hex[:8]
    # Register with no currency, then auto-fill from a MetaApi-reported value.
    mid = c.post(
        "/masters",
        headers=_HDR,
        json={
            "login": "100999",
            "broker": "InterStellarFinancial",
            "server": "InterStellarFinancial-Server",
            "metaapi_account_id": acc,
        },
    ).json()["id"]

    svc = MasterAccountService(fake)
    assert await svc.backfill_currency(acc, "USD") is True
    masters = {m["id"]: m for m in c.get("/masters", headers=_HDR).json()["masters"]}
    assert masters[mid]["currency"] == "USD"

    # Second call is a no-op (already set) but still reports "resolved".
    assert await svc.backfill_currency(acc, "EUR") is True
    masters = {m["id"]: m for m in c.get("/masters", headers=_HDR).json()["masters"]}
    assert masters[mid]["currency"] == "USD"

    # Unknown MetaApi account → not resolved (retry later), no row created.
    assert await svc.backfill_currency("acc-nope", "GBP") is False


def test_duplicate_login_returns_409(client):
    c, _ = client
    assert _register(c, login="100009").status_code == 200
    r = _register(c, login="100009")
    assert r.status_code == 409
    assert "already exists" in r.json()["detail"]


# -------------------- assign / unassign --------------------


def test_assign_sets_live_and_product(client):
    c, _ = client
    mid = _register(c, login="100100").json()["id"]
    r = c.post(f"/masters/{mid}/assign", headers=_HDR, json={"product": "gold_ai"})
    assert r.status_code == 200
    body = r.json()
    assert body["assigned_product"] == "gold_ai"
    assert body["status"] == "live"


def test_assign_enforces_one_master_per_product(client):
    """Assigning gold_ai to B while A holds it demotes A to standby — the
    one-master-per-product (UNIQUE) invariant, enforced via clear-then-assign."""
    c, _ = client
    a = _register(c, login="200001").json()["id"]
    b = _register(c, login="200002").json()["id"]

    assert (
        c.post(f"/masters/{a}/assign", headers=_HDR, json={"product": "gold_ai"}).status_code
        == 200
    )
    r = c.post(f"/masters/{b}/assign", headers=_HDR, json={"product": "gold_ai"})
    assert r.status_code == 200
    assert r.json()["assigned_product"] == "gold_ai"
    assert r.json()["status"] == "live"

    # End state: exactly one master holds gold_ai (B); A demoted to standby.
    masters = c.get("/masters", headers=_HDR).json()["masters"]
    gold = [m for m in masters if m["assigned_product"] == "gold_ai"]
    assert len(gold) == 1
    assert gold[0]["id"] == b
    a_row = next(m for m in masters if m["id"] == a)
    assert a_row["assigned_product"] is None
    assert a_row["status"] == "standby"


def test_assign_rejects_unknown_product(client):
    c, _ = client
    mid = _register(c, login="100200").json()["id"]
    r = c.post(f"/masters/{mid}/assign", headers=_HDR, json={"product": "crypto_ai"})
    assert r.status_code == 400


def test_assign_unknown_master_404(client):
    c, _ = client
    r = c.post(
        f"/masters/{uuid.uuid4()}/assign", headers=_HDR, json={"product": "gold_ai"}
    )
    assert r.status_code == 404


def test_unassign_returns_to_standby(client):
    c, _ = client
    mid = _register(c, login="100300").json()["id"]
    c.post(f"/masters/{mid}/assign", headers=_HDR, json={"product": "multi_cfd_ai"})
    r = c.post(f"/masters/{mid}/unassign", headers=_HDR)
    assert r.status_code == 200
    assert r.json()["assigned_product"] is None
    assert r.json()["status"] == "standby"
    # product is now free for another master
    other = _register(c, login="100301").json()["id"]
    r2 = c.post(f"/masters/{other}/assign", headers=_HDR, json={"product": "multi_cfd_ai"})
    assert r2.status_code == 200


# -------------------- delete --------------------


def test_delete_standby_master_ok(client):
    c, _ = client
    mid = _register(c, login="100400").json()["id"]
    r = c.delete(f"/masters/{mid}", headers=_HDR)
    assert r.status_code == 200
    assert r.json()["deleted"] is True
    assert c.get("/masters", headers=_HDR).json()["count"] == 0


def test_delete_live_master_returns_409(client):
    c, _ = client
    mid = _register(c, login="100500").json()["id"]
    c.post(f"/masters/{mid}/assign", headers=_HDR, json={"product": "gold_ai"})
    r = c.delete(f"/masters/{mid}", headers=_HDR)
    assert r.status_code == 409
    assert "live" in r.json()["detail"]
    # still present
    assert c.get("/masters", headers=_HDR).json()["count"] == 1


def test_delete_unknown_master_404(client):
    c, _ = client
    r = c.delete(f"/masters/{uuid.uuid4()}", headers=_HDR)
    assert r.status_code == 404


# -------------------- Stage 2 fallback (service-level) --------------------


async def test_get_master_for_product_falls_back_to_gold_ai(client):
    """multi_cfd_ai with no row of its own resolves to the gold_ai master."""
    c, fake = client
    mid = _register(c, login="100600").json()["id"]
    c.post(f"/masters/{mid}/assign", headers=_HDR, json={"product": "gold_ai"})

    svc = MasterAccountService(fake)
    resolved = await svc.get_master_for_product("multi_cfd_ai")
    assert resolved is not None
    assert resolved["login"] == "100600"
    assert resolved["assigned_product"] == "gold_ai"


# -------------------- password-based auto-provisioning --------------------
#
# These drive the real provisioning code path in MetaApiClientPool by mocking
# the MetaApi SDK class it instantiates, so create_account / deploy /
# wait_connected and the SDK→HTTP error mapping are exercised end-to-end.


class _FakeProvisionAccount:
    """Stand-in for the MetatraderAccount returned by create_account()."""

    def __init__(self) -> None:
        self.id = "prov-acc-123"
        self.region = "london"
        self.base_currency = "USD"
        self.state = "CREATED"
        self.connection_status = "CONNECTED"
        self.deployed = False
        self.connected = False

    async def deploy(self) -> None:
        self.deployed = True

    async def wait_connected(self, *_a, **_k) -> None:
        self.connected = True


class _FakeAccountApi:
    def __init__(self, account, calls, raiser) -> None:
        self._account = account
        self._calls = calls
        self._raiser = raiser

    async def create_account(self, dto: dict):
        self._calls.append(dto)
        if self._raiser is not None:
            raise self._raiser
        return self._account


def _patch_sdk(monkeypatch, account=None, raiser=None):
    """Patch metaapi_client.MetaApi → a fake; return (calls, account)."""
    import src.core.metaapi_client as mc

    account = account or _FakeProvisionAccount()
    calls: list[dict] = []

    class _FakeMetaApi:
        def __init__(self, _token):
            self.metatrader_account_api = _FakeAccountApi(account, calls, raiser)

    monkeypatch.setattr(mc, "MetaApi", _FakeMetaApi)
    return calls, account


def test_post_masters_with_password_provisions(client, monkeypatch):
    """Password path: SDK provisions the account and the row is auto-populated
    with its id/region/currency; the password is forwarded to the SDK but never
    persisted or returned."""
    c, fake = client
    calls, account = _patch_sdk(monkeypatch)

    r = c.post(
        "/masters",
        headers=_HDR,
        json={
            "login": "500123",
            "broker": "KVB Prime",
            "server": "KVBPrime-Live",
            "password": "s3cr3t-mt5-pw",
        },
    )

    assert r.status_code == 200, r.text
    body = r.json()
    # Auto-populated from the connected MetaApi account.
    assert body["metaapi_account_id"] == "prov-acc-123"
    assert body["metaapi_region"] == "london"
    assert body["currency"] == "USD"
    assert body["status"] == "standby"

    # SDK received the right DTO incl. the password — and the account deployed.
    assert calls and calls[0]["type"] == "cloud-g2"
    assert calls[0]["platform"] == "mt5"
    assert calls[0]["login"] == "500123"
    assert calls[0]["password"] == "s3cr3t-mt5-pw"
    assert account.deployed and account.connected

    # Password must never leak into the response or the persisted row.
    assert "password" not in body
    stored = fake._tables["master_accounts"].rows[0]
    assert "password" not in stored


def test_post_masters_password_invalid_credentials_401(client, monkeypatch):
    """SDK auth failure → 401 invalid_credentials."""
    from metaapi_cloud_sdk.clients.error_handler import UnauthorizedException

    c, _ = client
    _patch_sdk(monkeypatch, raiser=UnauthorizedException("bad creds"))

    r = c.post(
        "/masters",
        headers=_HDR,
        json={
            "login": "500124",
            "broker": "KVB Prime",
            "server": "KVBPrime-Live",
            "password": "wrong-pw",
        },
    )
    assert r.status_code == 401
    assert r.json()["detail"] == "invalid_credentials"


def test_post_masters_password_invalid_server_400(client, monkeypatch):
    """SDK validation failure (e.g. bad server) → 400 invalid_server."""
    from metaapi_cloud_sdk.clients.error_handler import ValidationException

    c, _ = client
    _patch_sdk(monkeypatch, raiser=ValidationException("unknown server"))

    r = c.post(
        "/masters",
        headers=_HDR,
        json={
            "login": "500125",
            "broker": "KVB Prime",
            "server": "Nope-Server",
            "password": "pw",
        },
    )
    assert r.status_code == 400
    assert r.json()["detail"] == "invalid_server"


def test_post_masters_ambiguous_credentials_422(client):
    """Both password and metaapi_account_id → 422 ambiguous_credentials."""
    c, _ = client
    r = c.post(
        "/masters",
        headers=_HDR,
        json={
            "login": "500126",
            "broker": "KVB Prime",
            "server": "KVBPrime-Live",
            "metaapi_account_id": "acc-xyz",
            "password": "pw",
        },
    )
    assert r.status_code == 422
    assert r.json()["detail"] == "ambiguous_credentials"


def test_post_masters_missing_credentials_422(client):
    """Neither password nor metaapi_account_id → 422 missing_credentials."""
    c, _ = client
    r = c.post(
        "/masters",
        headers=_HDR,
        json={
            "login": "500127",
            "broker": "KVB Prime",
            "server": "KVBPrime-Live",
        },
    )
    assert r.status_code == 422
    assert r.json()["detail"] == "missing_credentials"


def test_post_masters_backward_compat_skips_provisioning(client, monkeypatch):
    """metaapi_account_id path is unchanged: the SDK is never touched."""
    import src.core.metaapi_client as mc

    c, _ = client

    class _BoomMetaApi:
        def __init__(self, _token):
            raise AssertionError("MetaApi must not be constructed on the id path")

    monkeypatch.setattr(mc, "MetaApi", _BoomMetaApi)

    r = _register(c, login="500128", metaapi_account_id="pre-existing-id")
    assert r.status_code == 200
    body = r.json()
    assert body["metaapi_account_id"] == "pre-existing-id"
    assert body["status"] == "standby"
