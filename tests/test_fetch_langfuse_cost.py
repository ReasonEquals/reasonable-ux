"""Tests for _fetch_langfuse_cost.

Verifies: env-var guard, single-page cost accumulation, pagination, zero-cost → None,
exception → None, and time.sleep call. LangfuseAPI and time.sleep are both patched at
their source modules (correct for function-local imports).
"""
import sys
from pathlib import Path
from unittest.mock import MagicMock, call, patch

sys.path.insert(0, str(Path(__file__).parent.parent))
from run import _fetch_langfuse_cost


def _make_trace(cost):
    t = MagicMock()
    t.total_cost = cost
    return t


def _make_response(traces, next_page=None):
    resp = MagicMock()
    resp.data = traces
    resp.meta.next_page = next_page
    return resp


def _patched(api_instance, monkeypatch):
    """Return a context manager that patches LangfuseAPI + time.sleep with creds set."""
    monkeypatch.setenv("LANGFUSE_PUBLIC_KEY", "pub")
    monkeypatch.setenv("LANGFUSE_SECRET_KEY", "sec")
    mock_cls = MagicMock(return_value=api_instance)
    return patch("langfuse.api.LangfuseAPI", mock_cls), mock_cls


def test_missing_public_key_returns_none(monkeypatch):
    monkeypatch.delenv("LANGFUSE_PUBLIC_KEY", raising=False)
    monkeypatch.delenv("LANGFUSE_SECRET_KEY", raising=False)
    assert _fetch_langfuse_cost("s1") is None


def test_missing_secret_key_returns_none(monkeypatch):
    monkeypatch.setenv("LANGFUSE_PUBLIC_KEY", "pub")
    monkeypatch.delenv("LANGFUSE_SECRET_KEY", raising=False)
    assert _fetch_langfuse_cost("s1") is None


def test_single_page_returns_cost(monkeypatch):
    api = MagicMock()
    api.trace.list.return_value = _make_response([_make_trace(0.25), _make_trace(0.1)])
    cm, _ = _patched(api, monkeypatch)
    with cm, patch("time.sleep"):
        result = _fetch_langfuse_cost("s1")
    assert result == round(0.35, 6)


def test_pagination_accumulates_cost(monkeypatch):
    api = MagicMock()
    api.trace.list.side_effect = [
        _make_response([_make_trace(0.5)], next_page=2),
        _make_response([_make_trace(0.3)], next_page=None),
    ]
    cm, _ = _patched(api, monkeypatch)
    with cm, patch("time.sleep"):
        result = _fetch_langfuse_cost("s1")
    assert result == round(0.8, 6)
    assert api.trace.list.call_count == 2


def test_zero_cost_returns_none(monkeypatch):
    api = MagicMock()
    api.trace.list.return_value = _make_response([_make_trace(0.0), _make_trace(None)])
    cm, _ = _patched(api, monkeypatch)
    with cm, patch("time.sleep"):
        result = _fetch_langfuse_cost("s1")
    assert result is None


def test_exception_returns_none(monkeypatch):
    api = MagicMock()
    api.trace.list.side_effect = Exception("network error")
    cm, _ = _patched(api, monkeypatch)
    with cm, patch("time.sleep"):
        result = _fetch_langfuse_cost("s1")
    assert result is None


def test_sleep_is_called(monkeypatch):
    api = MagicMock()
    api.trace.list.return_value = _make_response([])
    cm, _ = _patched(api, monkeypatch)
    sleep_mock = MagicMock()
    with cm, patch("time.sleep", sleep_mock):
        _fetch_langfuse_cost("s1")
    assert sleep_mock.call_args_list == [call(3)]
