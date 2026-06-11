from __future__ import annotations

import os

os.environ.setdefault("SUPABASE_URL", "https://example.supabase.co")
os.environ.setdefault("SUPABASE_SERVICE_ROLE_KEY", "test")
os.environ.setdefault("SUPABASE_CUSTOMERS_URL", "https://customers.example.supabase.co")
os.environ.setdefault("SUPABASE_CUSTOMERS_SERVICE_ROLE_KEY", "test-customers")
os.environ.setdefault("METAAPI_TOKEN", "test")
os.environ.setdefault("METAAPI_MASTER_ACCOUNT_ID", "00000000-0000-0000-0000-000000000000")
os.environ.setdefault("APP_ENV", "development")

import httpx  # noqa: E402
import pytest  # noqa: E402

from src.config import reset_settings  # noqa: E402
from src.services import chart_img  # noqa: E402

PNG = b"\x89PNG\r\n\x1a\n" + b"fake-image-bytes"


@pytest.fixture(autouse=True)
def _configure_chartimg(monkeypatch):
    monkeypatch.setenv("CHARTIMG_API_KEY", "test-key")
    monkeypatch.setenv("TV_LAYOUT_ID", "test-layout")
    reset_settings()
    yield
    reset_settings()


class _FakeResponse:
    def __init__(self, status_code: int, content: bytes) -> None:
        self.status_code = status_code
        self.content = content

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise httpx.HTTPStatusError("error", request=None, response=None)


class _FakeAsyncClient:
    """Stand-in for httpx.AsyncClient that returns a canned response."""

    def __init__(self, response=None, raise_exc=None, **_kwargs) -> None:
        self._response = response
        self._raise_exc = raise_exc
        self.calls: list[dict] = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def post(self, url, headers=None, json=None):
        self.calls.append({"url": url, "headers": headers, "json": json})
        if self._raise_exc is not None:
            raise self._raise_exc
        return self._response


async def test_capture_returns_bytes_on_200(monkeypatch):
    captured = {}

    def _factory(**kwargs):
        client = _FakeAsyncClient(response=_FakeResponse(200, PNG), **kwargs)
        captured["client"] = client
        return client

    monkeypatch.setattr(chart_img.httpx, "AsyncClient", _factory)

    result = await chart_img.capture_layout_snapshot(symbol="OANDA:XAUUSD", interval="5m")

    assert result == PNG
    call = captured["client"].calls[0]
    # Layout id lives in the URL path; it must not appear in the body.
    assert call["url"].endswith("/tradingview/layout-chart/test-layout")
    assert call["headers"]["x-api-key"] == "test-key"
    assert call["json"] == {
        "symbol": "OANDA:XAUUSD",
        "interval": "5m",
        "width": 1920,
        "height": 1080,
    }


async def test_capture_returns_none_on_error(monkeypatch):
    def _factory(**kwargs):
        return _FakeAsyncClient(raise_exc=httpx.ConnectError("boom"), **kwargs)

    monkeypatch.setattr(chart_img.httpx, "AsyncClient", _factory)

    result = await chart_img.capture_layout_snapshot(symbol="OANDA:XAUUSD", interval="5m")

    assert result is None


async def test_capture_returns_none_when_not_configured(monkeypatch):
    monkeypatch.setenv("CHARTIMG_API_KEY", "")
    monkeypatch.setenv("TV_LAYOUT_ID", "")
    reset_settings()

    # Should short-circuit without ever constructing an httpx client.
    def _boom(**_kwargs):
        raise AssertionError("httpx client must not be created when unconfigured")

    monkeypatch.setattr(chart_img.httpx, "AsyncClient", _boom)

    result = await chart_img.capture_layout_snapshot(symbol="OANDA:XAUUSD", interval="5m")
    assert result is None
