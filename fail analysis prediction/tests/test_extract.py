import io
import json
import urllib.error
from datetime import date
from email.message import Message

import pytest

from failmodel.config import Settings, WrapperSettings
from failmodel.extract import WrapperClient, WrapperError, date_windows, load_raw, run_extracts
from failmodel.sources import SOURCES


class FakeResponse(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class FakeClock:
    def __init__(self):
        self.now = 0.0
        self.sleeps = []

    def clock(self):
        return self.now

    def sleep(self, seconds):
        self.sleeps.append(seconds)
        self.now += seconds


def _settings(**kw):
    return WrapperSettings(base_url="https://wrapper.test", api_key="k", **kw)


def _ok_payload(rows):
    return {"resultSets": [rows], "outputParameters": {"@RowCount": len(rows)}, "returnValue": 0}


def test_execute_sends_allowlisted_call_and_parses_output():
    seen = {}

    def opener(request, timeout):
        seen["url"] = request.full_url
        seen["headers"] = dict(request.headers)
        seen["body"] = json.loads(request.data)
        return FakeResponse(json.dumps(_ok_payload([{"TradeDate": "2025-01-02", "ID": 1}])).encode())

    clock = FakeClock()
    client = WrapperClient(_settings(), opener=opener, sleep=clock.sleep, clock=clock.clock)
    result = client.run_extract("usp_FailModel_Extract_BetaTrades", date(2025, 1, 2), date(2025, 1, 2))

    assert seen["url"] == "https://wrapper.test/wsi-db-wrapper/stored-procs/execute"
    assert seen["headers"]["X-api-key"] == "k"
    assert seen["body"]["databaseName"] == "OperationsPortalDB"
    assert seen["body"]["commandTimeoutSeconds"] == 300
    assert [p["name"] for p in seen["body"]["parameters"]] == ["FromDate", "ToDate", "RowCount"]
    assert result.row_count == 1 and len(result.frame) == 1


def test_429_is_retried_after_retry_after_seconds():
    calls = {"n": 0}

    def opener(request, timeout):
        calls["n"] += 1
        if calls["n"] == 1:
            headers = Message()
            headers["Retry-After"] = "7"
            raise urllib.error.HTTPError(request.full_url, 429, "Too Many", headers, io.BytesIO(b"slow down"))
        return FakeResponse(json.dumps(_ok_payload([])).encode())

    clock = FakeClock()
    client = WrapperClient(_settings(), opener=opener, sleep=clock.sleep, clock=clock.clock)
    client.run_extract("p", date(2025, 1, 2), date(2025, 1, 2))
    assert calls["n"] == 2
    assert 7 in clock.sleeps


def test_client_error_is_not_retried():
    def opener(request, timeout):
        raise urllib.error.HTTPError(request.full_url, 400, "Bad", Message(), io.BytesIO(b"not allowlisted"))

    clock = FakeClock()
    client = WrapperClient(_settings(), opener=opener, sleep=clock.sleep, clock=clock.clock)
    with pytest.raises(WrapperError, match="not allowlisted"):
        client.run_extract("p", date(2025, 1, 2), date(2025, 1, 2))


def test_calls_are_paced_under_the_rate_limit():
    def opener(request, timeout):
        return FakeResponse(json.dumps(_ok_payload([])).encode())

    clock = FakeClock()
    client = WrapperClient(_settings(calls_per_minute=60), opener=opener, sleep=clock.sleep, clock=clock.clock)
    for _ in range(3):
        client.run_extract("p", date(2025, 1, 2), date(2025, 1, 2))
    assert clock.sleeps == [pytest.approx(1.0), pytest.approx(1.0)]


def test_date_windows_skip_weekends_for_daily_sources():
    windows = date_windows(SOURCES["beta_trades"], date(2025, 1, 3), date(2025, 1, 6))
    assert windows == [(date(2025, 1, 3), date(2025, 1, 3)), (date(2025, 1, 6), date(2025, 1, 6))]
    weekly = date_windows(SOURCES["beta_fail_snapshots"], date(2025, 1, 1), date(2025, 1, 10))
    assert weekly == [(date(2025, 1, 1), date(2025, 1, 7)), (date(2025, 1, 8), date(2025, 1, 10))]


def test_run_extracts_is_resumable(tmp_path):
    calls = {"n": 0}

    def opener(request, timeout):
        calls["n"] += 1
        return FakeResponse(json.dumps(_ok_payload([{"TradeDate": "2025-01-02", "ID": calls["n"]}])).encode())

    clock = FakeClock()
    settings = Settings(data_dir=tmp_path)
    client = WrapperClient(_settings(), opener=opener, sleep=clock.sleep, clock=clock.clock)
    run_extracts(["beta_trades"], date(2025, 1, 2), date(2025, 1, 3), settings, client)
    run_extracts(["beta_trades"], date(2025, 1, 2), date(2025, 1, 3), settings, client)
    assert calls["n"] == 2
    assert len(load_raw("beta_trades", settings.raw_dir)) == 2
    assert (settings.raw_dir / "_manifest.csv").exists()
