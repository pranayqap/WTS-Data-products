"""WSI DB Wrapper client and resumable extract runner.

Calls ``POST /wsi-db-wrapper/stored-procs/execute`` for the allowlisted
``OperationsPortalDB.dbo.usp_FailModel_Extract_*`` procedures, paced under the wrapper's
100-calls-per-minute limit, and lands each date window as Parquet under ``data/raw/<source>/``.
"""

from __future__ import annotations

import csv
import json
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path

import pandas as pd

from .config import Settings, WrapperSettings
from .sources import SOURCES, Source


class WrapperError(RuntimeError):
    pass


@dataclass
class CallResult:
    frame: pd.DataFrame
    row_count: int | None
    seconds: float


class WrapperClient:
    def __init__(self, settings: WrapperSettings, opener=None, sleep=time.sleep, clock=time.monotonic):
        if not settings.base_url:
            raise WrapperError("WSI_WRAPPER_BASE_URL is not set")
        if not (settings.api_key or settings.bearer_token):
            raise WrapperError("set WSI_WRAPPER_API_KEY or WSI_WRAPPER_BEARER_TOKEN")
        self.settings = settings
        self._open = opener or urllib.request.urlopen
        self._sleep = sleep
        self._clock = clock
        self._min_interval = 60.0 / max(settings.calls_per_minute, 1)
        self._last_call = None

    def _headers(self) -> dict:
        headers = {"Content-Type": "application/json", "Accept": "application/json"}
        if self.settings.bearer_token:
            headers["Authorization"] = f"Bearer {self.settings.bearer_token}"
        else:
            headers["X-Api-Key"] = self.settings.api_key
        return headers

    def _pace(self) -> None:
        if self._last_call is not None:
            wait = self._min_interval - (self._clock() - self._last_call)
            if wait > 0:
                self._sleep(wait)
        self._last_call = self._clock()

    def execute(self, procedure: str, parameters: list[dict]) -> dict:
        body = json.dumps(
            {
                "databaseName": self.settings.database,
                "schema": self.settings.schema,
                "procedureName": procedure,
                "parameters": parameters,
                "commandTimeoutSeconds": self.settings.command_timeout_seconds,
            }
        ).encode()
        url = f"{self.settings.base_url}/wsi-db-wrapper/stored-procs/execute"
        attempt = 0
        while True:
            self._pace()
            request = urllib.request.Request(url, data=body, headers=self._headers(), method="POST")
            try:
                with self._open(request, timeout=self.settings.command_timeout_seconds + 30) as response:
                    return json.loads(response.read().decode())
            except urllib.error.HTTPError as exc:
                detail = exc.read().decode(errors="replace") if hasattr(exc, "read") else ""
                retryable = exc.code == 429 or exc.code >= 500
                if not retryable or attempt >= self.settings.max_retries:
                    raise WrapperError(f"{procedure}: HTTP {exc.code}: {detail}") from exc
                retry_after = exc.headers.get("Retry-After") if exc.headers else None
                delay = float(retry_after) if retry_after else min(2 ** attempt * 5, 120)
            except urllib.error.URLError as exc:
                if attempt >= self.settings.max_retries:
                    raise WrapperError(f"{procedure}: {exc.reason}") from exc
                delay = min(2 ** attempt * 5, 120)
            attempt += 1
            self._sleep(delay)

    def run_extract(self, procedure: str, from_date: date, to_date: date) -> CallResult:
        start = time.monotonic()
        payload = self.execute(
            procedure,
            [
                {"name": "FromDate", "sqlDbType": "Date", "value": from_date.isoformat(), "direction": "Input"},
                {"name": "ToDate", "sqlDbType": "Date", "value": to_date.isoformat(), "direction": "Input"},
                {"name": "RowCount", "sqlDbType": "Int", "direction": "Output"},
            ],
        )
        result_sets = payload.get("resultSets") or [[]]
        frame = pd.DataFrame(result_sets[0])
        outputs = payload.get("outputParameters") or {}
        row_count = outputs.get("@RowCount", outputs.get("RowCount"))
        return CallResult(frame, None if row_count is None else int(row_count), time.monotonic() - start)


def date_windows(source: Source, start: date, end: date) -> list[tuple[date, date]]:
    """Split [start, end] into call windows. One-day sources skip weekends."""
    windows = []
    step = max(source.chunk_days, 1)
    cursor = start
    while cursor <= end:
        stop = min(cursor + timedelta(days=step - 1), end)
        if not (step == 1 and cursor.weekday() >= 5):
            windows.append((cursor, stop))
        cursor = stop + timedelta(days=1)
    return windows


def window_path(raw_dir: Path, source_id: str, window: tuple[date, date]) -> Path:
    return raw_dir / source_id / f"{window[0]:%Y%m%d}_{window[1]:%Y%m%d}.parquet"


def run_extracts(
    source_ids: list[str],
    start: date,
    end: date,
    settings: Settings,
    client: WrapperClient,
    overwrite: bool = False,
) -> pd.DataFrame:
    """Pull every window for each source. Existing files are skipped, so reruns resume."""
    manifest_path = settings.raw_dir / "_manifest.csv"
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    log = []
    for source_id in source_ids:
        source = SOURCES[source_id]
        for window in date_windows(source, start, end):
            path = window_path(settings.raw_dir, source_id, window)
            if path.exists() and not overwrite:
                continue
            result = client.run_extract(source.procedure, *window)
            status = "ok"
            if result.row_count is not None and result.row_count != len(result.frame):
                status = f"row_count_mismatch({result.row_count})"
            path.parent.mkdir(parents=True, exist_ok=True)
            result.frame.to_parquet(path, index=False)
            entry = {
                "source": source_id,
                "from": window[0].isoformat(),
                "to": window[1].isoformat(),
                "rows": len(result.frame),
                "seconds": round(result.seconds, 2),
                "status": status,
            }
            log.append(entry)
            new_file = not manifest_path.exists()
            with manifest_path.open("a", newline="") as fh:
                writer = csv.DictWriter(fh, fieldnames=list(entry))
                if new_file:
                    writer.writeheader()
                writer.writerow(entry)
    return pd.DataFrame(log)


def load_raw(source_id: str, raw_dir: Path) -> pd.DataFrame:
    folder = raw_dir / source_id
    files = sorted(folder.glob("*.parquet")) if folder.exists() else []
    if not files:
        return pd.DataFrame()
    frames = [pd.read_parquet(f) for f in files]
    frames = [f for f in frames if len(f)]
    return pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()


def write_raw(frame: pd.DataFrame, source_id: str, raw_dir: Path, name: str = "part") -> Path:
    path = raw_dir / source_id / f"{name}.parquet"
    path.parent.mkdir(parents=True, exist_ok=True)
    frame.to_parquet(path, index=False)
    return path
