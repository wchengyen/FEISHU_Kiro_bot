# Resource Metrics History Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add persistent hourly CloudWatch metric storage with daily downsampling, a sync script, history API, and frontend trend panel for Dashboard Resources.

**Architecture:** SQLite with two storage tiers: monthly raw databases (`raw_metrics_YYYY_MM.db`) for permanent hourly data, and a unified aggregated database (`aggregated_metrics.db`) for 180-day daily rollups (min/avg/p95/max). A standalone Python script runs daily via cron to sync the previous 24 hours from CloudWatch. A new Flask API endpoint serves history queries, and the Vue frontend renders an expandable trend panel.

**Tech Stack:** Python 3, Flask, SQLite, boto3, Vue 3 (CDN), pytest

---

## File Structure

| File | Action | Responsibility |
|------|--------|---------------|
| `dashboard/metrics_store.py` | Create | Connection management for raw/aggregated DBs, schema creation, hourly write, daily query, downsampling, cross-month history lookup |
| `scripts/sync_resource_metrics.py` | Create | Standalone script: discover resources, fetch CloudWatch CPUUtilization per hour, bulk write to raw DB, trigger downsampling |
| `dashboard/api.py` | Modify | Add `GET /resources/<id>/history` route |
| `dashboard/static/app.js` | Modify | Add expandable history panel with 24h/7d/30d/180d range switch and SVG trend chart |
| `tests/test_metrics_store.py` | Create | Unit tests for DB writes, queries, downsampling logic |
| `tests/test_sync_resource_metrics.py` | Create | Integration tests for sync script with mocked CloudWatch |
| `tests/test_dashboard_api_resources_history.py` | Create | API route tests for the new history endpoint |

---

## Task 1: `dashboard/metrics_store.py` — Core Storage Manager

**Files:**
- Create: `dashboard/metrics_store.py`
- Test: `tests/test_metrics_store.py`

- [ ] **Step 1: Write the failing test for DB path helpers**

```python
# tests/test_metrics_store.py
import os
import sqlite3
import tempfile
from datetime import datetime

import pytest

from dashboard.metrics_store import (
    MetricsStore,
    _raw_db_path,
    _aggregated_db_path,
)


def test_raw_db_path_format():
    path = _raw_db_path(2026, 4, base_dir="/tmp/metrics")
    assert path == "/tmp/metrics/raw_metrics_2026_04.db"


def test_aggregated_db_path():
    path = _aggregated_db_path(base_dir="/tmp/metrics")
    assert path == "/tmp/metrics/aggregated_metrics.db"
```

- [ ] **Step 2: Run the failing test**

```bash
pytest tests/test_metrics_store.py::test_raw_db_path_format tests/test_metrics_store.py::test_aggregated_db_path -v
```

Expected: FAIL with `ImportError: cannot import name '_raw_db_path'`

- [ ] **Step 3: Implement DB path helpers and store initialization**

```python
# dashboard/metrics_store.py
import os
import sqlite3
from datetime import datetime, timedelta


DEFAULT_BASE_DIR = os.path.join(os.path.dirname(__file__), "..", "memory_db")


def _raw_db_path(year: int, month: int, base_dir: str | None = None) -> str:
    d = base_dir or DEFAULT_BASE_DIR
    return os.path.join(d, f"raw_metrics_{year}_{month:02d}.db")


def _aggregated_db_path(base_dir: str | None = None) -> str:
    d = base_dir or DEFAULT_BASE_DIR
    return os.path.join(d, "aggregated_metrics.db")


def _ensure_hourly_table(conn: sqlite3.Connection):
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS hourly_metrics (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            resource_id TEXT NOT NULL,
            metric_name TEXT NOT NULL,
            timestamp INTEGER NOT NULL,
            value REAL NOT NULL,
            region TEXT,
            created_at INTEGER DEFAULT (strftime('%s','now')),
            UNIQUE(resource_id, metric_name, timestamp)
        )
        """
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_hourly_lookup ON hourly_metrics(resource_id, metric_name, timestamp)"
    )
    conn.commit()


def _ensure_daily_table(conn: sqlite3.Connection):
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS daily_aggregated (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            resource_id TEXT NOT NULL,
            metric_name TEXT NOT NULL,
            date TEXT NOT NULL,
            min_value REAL NOT NULL,
            avg_value REAL NOT NULL,
            p95_value REAL NOT NULL,
            max_value REAL NOT NULL,
            region TEXT,
            UNIQUE(resource_id, metric_name, date)
        )
        """
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_daily_lookup ON daily_aggregated(resource_id, metric_name, date)"
    )
    conn.commit()


class MetricsStore:
    def __init__(self, base_dir: str | None = None):
        self.base_dir = base_dir or DEFAULT_BASE_DIR
        os.makedirs(self.base_dir, exist_ok=True)
        self._raw_conns: dict[str, sqlite3.Connection] = {}
        self._agg_conn: sqlite3.Connection | None = None

    def _raw_conn(self, year: int, month: int) -> sqlite3.Connection:
        key = f"{year}_{month:02d}"
        if key not in self._raw_conns:
            path = _raw_db_path(year, month, self.base_dir)
            conn = sqlite3.connect(path)
            _ensure_hourly_table(conn)
            self._raw_conns[key] = conn
        return self._raw_conns[key]

    def _agg_conn(self) -> sqlite3.Connection:
        if self._agg_conn is None:
            path = _aggregated_db_path(self.base_dir)
            conn = sqlite3.connect(path)
            _ensure_daily_table(conn)
            self._agg_conn = conn
        return self._agg_conn

    def close(self):
        for conn in self._raw_conns.values():
            conn.close()
        self._raw_conns.clear()
        if self._agg_conn:
            self._agg_conn.close()
            self._agg_conn = None
```

- [ ] **Step 4: Run test to verify it passes**

```bash
pytest tests/test_metrics_store.py::test_raw_db_path_format tests/test_metrics_store.py::test_aggregated_db_path -v
```

Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add dashboard/metrics_store.py tests/test_metrics_store.py
git commit -m "feat(metrics): add MetricsStore with raw/aggregated DB path helpers and schema creation"
```

- [ ] **Step 6: Write the failing test for write_hourly and query_hourly**

```python
# tests/test_metrics_store.py (append)

def test_write_and_query_hourly():
    with tempfile.TemporaryDirectory() as tmpdir:
        store = MetricsStore(base_dir=tmpdir)
        records = [
            ("ec2:cn-north-1:i-123", "cpu_utilization", 1714113600, 12.5, "cn-north-1"),
            ("ec2:cn-north-1:i-123", "cpu_utilization", 1714117200, 15.2, "cn-north-1"),
            ("ec2:cn-north-1:i-456", "cpu_utilization", 1714113600, 8.0, "cn-north-1"),
        ]
        store.write_hourly(records)

        result = store.query_hourly("ec2:cn-north-1:i-123", "cpu_utilization", 1714113000, 1714118000)
        assert len(result) == 2
        assert result[0]["timestamp"] == 1714113600
        assert result[0]["value"] == 12.5
        assert result[1]["timestamp"] == 1714117200
        assert result[1]["value"] == 15.2

        store.close()
```

- [ ] **Step 7: Run the failing test**

```bash
pytest tests/test_metrics_store.py::test_write_and_query_hourly -v
```

Expected: FAIL with `AttributeError: 'MetricsStore' object has no attribute 'write_hourly'`

- [ ] **Step 8: Implement write_hourly and query_hourly**

```python
# dashboard/metrics_store.py (append inside MetricsStore class)

    def write_hourly(self, records: list[tuple]):
        """Bulk insert hourly records with UPSERT.

        records: list of (resource_id, metric_name, timestamp, value, region)
        """
        if not records:
            return
        # Group by (year, month) to write to correct DB
        grouped: dict[tuple[int, int], list[tuple]] = {}
        for r in records:
            ts = r[2]
            dt = datetime.utcfromtimestamp(ts)
            key = (dt.year, dt.month)
            grouped.setdefault(key, []).append(r)

        for (year, month), rows in grouped.items():
            conn = self._raw_conn(year, month)
            conn.executemany(
                """
                INSERT INTO hourly_metrics (resource_id, metric_name, timestamp, value, region)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(resource_id, metric_name, timestamp) DO UPDATE SET
                    value=excluded.value,
                    region=excluded.region
                """,
                rows,
            )
            conn.commit()

    def query_hourly(self, resource_id: str, metric_name: str, start_ts: int, end_ts: int) -> list[dict]:
        """Query hourly data across one or two monthly DBs."""
        start_dt = datetime.utcfromtimestamp(start_ts)
        end_dt = datetime.utcfromtimestamp(end_ts)
        months = []
        y, m = start_dt.year, start_dt.month
        while (y, m) <= (end_dt.year, end_dt.month):
            months.append((y, m))
            m += 1
            if m > 12:
                m = 1
                y += 1

        results = []
        for year, month in months:
            conn = self._raw_conn(year, month)
            cursor = conn.execute(
                """
                SELECT timestamp, value FROM hourly_metrics
                WHERE resource_id = ? AND metric_name = ? AND timestamp >= ? AND timestamp <= ?
                ORDER BY timestamp
                """,
                (resource_id, metric_name, start_ts, end_ts),
            )
            for row in cursor.fetchall():
                results.append({"timestamp": row[0], "value": row[1]})
        return results
```

- [ ] **Step 9: Run test to verify it passes**

```bash
pytest tests/test_metrics_store.py::test_write_and_query_hourly -v
```

Expected: PASS

- [ ] **Step 10: Commit**

```bash
git add dashboard/metrics_store.py tests/test_metrics_store.py
git commit -m "feat(metrics): add hourly write and query with cross-month lookup"
```

- [ ] **Step 11: Write the failing test for downsampling and query_daily**

```python
# tests/test_metrics_store.py (append)

def test_downsample_and_query_daily():
    with tempfile.TemporaryDirectory() as tmpdir:
        store = MetricsStore(base_dir=tmpdir)
        # Insert hourly data for 2026-04-25
        base = int(datetime(2026, 4, 25, 0, 0, 0).timestamp())
        records = [
            ("ec2:cn-north-1:i-123", "cpu_utilization", base + h * 3600, float(10 + h), "cn-north-1")
            for h in range(24)
        ]
        store.write_hourly(records)

        # Downsample
        store.downsample_month(2026, 4)

        # Query daily
        result = store.query_daily("ec2:cn-north-1:i-123", "cpu_utilization", "2026-04-25", "2026-04-25")
        assert len(result) == 1
        row = result[0]
        assert row["date"] == "2026-04-25"
        assert row["min_value"] == 10.0
        assert row["avg_value"] == 21.5  # average of 10..33
        assert row["max_value"] == 33.0
        assert row["p95_value"] == 32.0  # 95th percentile of 24 values

        store.close()
```

- [ ] **Step 12: Run the failing test**

```bash
pytest tests/test_metrics_store.py::test_downsample_and_query_daily -v
```

Expected: FAIL with `AttributeError: 'MetricsStore' object has no attribute 'downsample_month'`

- [ ] **Step 13: Implement downsample_month, query_daily, and cleanup_old_daily**

```python
# dashboard/metrics_store.py (append inside MetricsStore class)

    def downsample_month(self, year: int, month: int) -> int:
        """Aggregate hourly data for a given month into daily_aggregated."""
        conn = self._raw_conn(year, month)
        cursor = conn.execute(
            """
            SELECT resource_id, metric_name, date(timestamp, 'unixepoch') as dt,
                   MIN(value), AVG(value), MAX(value)
            FROM hourly_metrics
            WHERE strftime('%Y-%m', datetime(timestamp, 'unixepoch')) = ?
            GROUP BY resource_id, metric_name, date(timestamp, 'unixepoch')
            ORDER BY resource_id, metric_name, dt
            """,
            (f"{year}-{month:02d}",),
        )
        rows = cursor.fetchall()
        if not rows:
            return 0

        agg_conn = self._agg_conn()
        inserted = 0
        for resource_id, metric_name, dt, min_val, avg_val, max_val in rows:
            # Compute p95 from raw values
            p95_cursor = conn.execute(
                """
                SELECT value FROM hourly_metrics
                WHERE resource_id = ? AND metric_name = ?
                  AND date(timestamp, 'unixepoch') = ?
                ORDER BY value
                """,
                (resource_id, metric_name, dt),
            )
            values = [r[0] for r in p95_cursor.fetchall()]
            p95_val = values[int(len(values) * 0.95)] if values else 0.0

            agg_conn.execute(
                """
                INSERT INTO daily_aggregated (resource_id, metric_name, date, min_value, avg_value, p95_value, max_value, region)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(resource_id, metric_name, date) DO UPDATE SET
                    min_value=excluded.min_value,
                    avg_value=excluded.avg_value,
                    p95_value=excluded.p95_value,
                    max_value=excluded.max_value,
                    region=excluded.region
                """,
                (resource_id, metric_name, dt, min_val, round(avg_val, 2), round(p95_val, 2), max_val, None),
            )
            inserted += 1
        agg_conn.commit()
        return inserted

    def query_daily(self, resource_id: str, metric_name: str, start_date: str, end_date: str) -> list[dict]:
        conn = self._agg_conn()
        cursor = conn.execute(
            """
            SELECT date, min_value, avg_value, p95_value, max_value FROM daily_aggregated
            WHERE resource_id = ? AND metric_name = ? AND date >= ? AND date <= ?
            ORDER BY date
            """,
            (resource_id, metric_name, start_date, end_date),
        )
        return [
            {
                "date": row[0],
                "min_value": row[1],
                "avg_value": row[2],
                "p95_value": row[3],
                "max_value": row[4],
            }
            for row in cursor.fetchall()
        ]

    def cleanup_old_daily(self, keep_days: int = 180) -> int:
        """Delete daily aggregated records older than keep_days."""
        cutoff = (datetime.utcnow() - timedelta(days=keep_days)).strftime("%Y-%m-%d")
        conn = self._agg_conn()
        cursor = conn.execute(
            "DELETE FROM daily_aggregated WHERE date < ?",
            (cutoff,),
        )
        conn.commit()
        return cursor.rowcount
```

- [ ] **Step 14: Run test to verify it passes**

```bash
pytest tests/test_metrics_store.py::test_downsample_and_query_daily -v
```

Expected: PASS

- [ ] **Step 15: Commit**

```bash
git add dashboard/metrics_store.py tests/test_metrics_store.py
git commit -m "feat(metrics): add daily downsampling, query, and cleanup"
```

- [ ] **Step 16: Write the failing test for query_history routing**

```python
# tests/test_metrics_store.py (append)

def test_query_history_routes_to_hourly():
    with tempfile.TemporaryDirectory() as tmpdir:
        store = MetricsStore(base_dir=tmpdir)
        base = int(datetime(2026, 4, 25, 0, 0, 0).timestamp())
        store.write_hourly([
            ("ec2:cn-north-1:i-123", "cpu_utilization", base, 10.0, "cn-north-1"),
        ])
        result = store.query_history("ec2:cn-north-1:i-123", "cpu_utilization", "24h")
        assert result["granularity"] == "hourly"
        assert len(result["data"]) == 1
        store.close()


def test_query_history_routes_to_daily():
    with tempfile.TemporaryDirectory() as tmpdir:
        store = MetricsStore(base_dir=tmpdir)
        base = int(datetime(2026, 4, 25, 0, 0, 0).timestamp())
        store.write_hourly([
            ("ec2:cn-north-1:i-123", "cpu_utilization", base + h * 3600, float(10 + h), "cn-north-1")
            for h in range(24)
        ])
        store.downsample_month(2026, 4)

        result = store.query_history("ec2:cn-north-1:i-123", "cpu_utilization", "180d")
        assert result["granularity"] == "daily"
        assert len(result["data"]) == 1
        store.close()
```

- [ ] **Step 17: Run the failing test**

```bash
pytest tests/test_metrics_store.py::test_query_history_routes_to_hourly tests/test_metrics_store.py::test_query_history_routes_to_daily -v
```

Expected: FAIL with `AttributeError: 'MetricsStore' object has no attribute 'query_history'`

- [ ] **Step 18: Implement query_history**

```python
# dashboard/metrics_store.py (append inside MetricsStore class)

    def query_history(self, resource_id: str, metric_name: str, range_label: str) -> dict:
        """Unified history query. range_label: 24h, 7d, 30d, 180d."""
        now = datetime.utcnow()
        if range_label == "24h":
            start = now - timedelta(hours=24)
            granularity = "hourly"
        elif range_label == "7d":
            start = now - timedelta(days=7)
            granularity = "hourly"
        elif range_label == "30d":
            start = now - timedelta(days=30)
            granularity = "hourly"
        elif range_label == "180d":
            start = now - timedelta(days=180)
            granularity = "daily"
        else:
            raise ValueError(f"Unsupported range: {range_label}")

        if granularity == "hourly":
            data = self.query_hourly(
                resource_id, metric_name,
                int(start.timestamp()), int(now.timestamp()),
            )
            values = [d["value"] for d in data if d["value"] is not None]
            stats = self._compute_stats(values)
        else:
            data_raw = self.query_daily(
                resource_id, metric_name,
                start.strftime("%Y-%m-%d"), now.strftime("%Y-%m-%d"),
            )
            data = [{"timestamp": int(datetime.strptime(d["date"], "%Y-%m-%d").timestamp()), **d}
                    for d in data_raw]
            values = [d["avg_value"] for d in data_raw]
            stats = {
                "min": round(min([d["min_value"] for d in data_raw]), 1) if data_raw else None,
                "avg": round(sum(values) / len(values), 1) if values else None,
                "p95": round(sorted([d["p95_value"] for d in data_raw])[int(len(data_raw) * 0.95)], 1) if data_raw else None,
                "max": round(max([d["max_value"] for d in data_raw]), 1) if data_raw else None,
            }

        return {
            "resource_id": resource_id,
            "metric": metric_name,
            "range": range_label,
            "granularity": granularity,
            "data": data,
            "stats": stats,
        }

    @staticmethod
    def _compute_stats(values: list[float]) -> dict:
        if not values:
            return {"min": None, "avg": None, "p95": None, "max": None}
        sorted_vals = sorted(values)
        idx = int(len(sorted_vals) * 0.95)
        p95 = sorted_vals[min(idx, len(sorted_vals) - 1)]
        return {
            "min": round(min(values), 1),
            "avg": round(sum(values) / len(values), 1),
            "p95": round(p95, 1),
            "max": round(max(values), 1),
        }
```

- [ ] **Step 19: Run test to verify it passes**

```bash
pytest tests/test_metrics_store.py::test_query_history_routes_to_hourly tests/test_metrics_store.py::test_query_history_routes_to_daily -v
```

Expected: PASS

- [ ] **Step 20: Commit**

```bash
git add dashboard/metrics_store.py tests/test_metrics_store.py
git commit -m "feat(metrics): add unified query_history with range routing and stats"
```

---

## Task 2: `scripts/sync_resource_metrics.py` — Sync Script

**Files:**
- Create: `scripts/sync_resource_metrics.py`
- Test: `tests/test_sync_resource_metrics.py`

- [ ] **Step 1: Write the failing test for argument parsing**

```python
# tests/test_sync_resource_metrics.py
from unittest.mock import patch, MagicMock
import pytest

from scripts.sync_resource_metrics import parse_args


def test_parse_args_backfill():
    args = parse_args(["--backfill"])
    assert args.backfill is True
    assert args.incremental is False


def test_parse_args_incremental():
    args = parse_args(["--incremental"])
    assert args.backfill is False
    assert args.incremental is True


def test_parse_args_downsample():
    args = parse_args(["--downsample", "2026", "3"])
    assert args.downsample == [2026, 3]
```

- [ ] **Step 2: Run the failing test**

```bash
pytest tests/test_sync_resource_metrics.py::test_parse_args_backfill tests/test_sync_resource_metrics.py::test_parse_args_incremental tests/test_sync_resource_metrics.py::test_parse_args_downsample -v
```

Expected: FAIL with `ImportError: cannot import name 'parse_args'`

- [ ] **Step 3: Implement argument parsing and CloudWatch fetch helper**

```python
#!/usr/bin/env python3
# scripts/sync_resource_metrics.py
import argparse
import datetime
import logging
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from dashboard.resources import discover_all, _load_regions
from dashboard.metrics_store import MetricsStore

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
logger = logging.getLogger("sync_resource_metrics")


def parse_args(argv=None):
    parser = argparse.ArgumentParser(description="Sync resource metrics from CloudWatch to local SQLite")
    parser.add_argument("--backfill", action="store_true", help="Backfill past 30 days of hourly data")
    parser.add_argument("--incremental", action="store_true", help="Sync previous 24 hours")
    parser.add_argument("--downsample", nargs=2, type=int, metavar=("YEAR", "MONTH"), help="Downsample a specific month")
    parser.add_argument("--base-dir", default=None, help="Override metrics base directory")
    return parser.parse_args(argv)


def fetch_cloudwatch_hourly(resource, metric_name="CPUUtilization", hours=24, end=None):
    try:
        import boto3
    except ImportError:
        logger.warning("boto3 not installed, skipping CloudWatch fetch")
        return []

    region = resource.meta.get("region")
    kwargs = {"region_name": region} if region else {}
    client = boto3.client("cloudwatch", **kwargs)

    if end is None:
        end = datetime.datetime.utcnow()
    start = end - datetime.timedelta(hours=hours)

    if resource.type == "ec2":
        namespace = "AWS/EC2"
        dimension_name = "InstanceId"
        dimension_value = resource.raw_id
    elif resource.type == "rds":
        namespace = "AWS/RDS"
        dimension_name = "DBInstanceIdentifier"
        dimension_value = resource.raw_id
    else:
        return []

    resp = client.get_metric_statistics(
        Namespace=namespace,
        MetricName=metric_name,
        Dimensions=[{"Name": dimension_name, "Value": dimension_value}],
        StartTime=start,
        EndTime=end,
        Period=3600,
        Statistics=["Average"],
    )

    points = sorted(resp.get("Datapoints", []), key=lambda x: x["Timestamp"])
    records = []
    for p in points:
        ts = int(p["Timestamp"].replace(tzinfo=datetime.timezone.utc).timestamp())
        # Round to nearest hour
        ts = ts // 3600 * 3600
        records.append((resource.id, metric_name, ts, round(p["Average"], 2), region))
    return records
```

- [ ] **Step 4: Run test to verify it passes**

```bash
pytest tests/test_sync_resource_metrics.py::test_parse_args_backfill tests/test_sync_resource_metrics.py::test_parse_args_incremental tests/test_sync_resource_metrics.py::test_parse_args_downsample -v
```

Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add scripts/sync_resource_metrics.py tests/test_sync_resource_metrics.py
git commit -m "feat(sync): add sync script CLI and CloudWatch fetch helper"
```

- [ ] **Step 6: Write the failing test for backfill flow**

```python
# tests/test_sync_resource_metrics.py (append)
from datetime import datetime, timedelta
from unittest.mock import patch, MagicMock

from dashboard.resources import Resource


def test_backfill_flow():
    resource = Resource(
        id="ec2:us-east-1:i-123",
        type="ec2",
        name="test",
        raw_id="i-123",
        status="running",
        meta={"region": "us-east-1"},
    )

    mock_point_time = datetime(2026, 4, 25, 10, 0, 0, tzinfo=datetime.timezone.utc)
    mock_cw_response = {
        "Datapoints": [
            {"Timestamp": mock_point_time, "Average": 15.5},
        ]
    }

    with patch("scripts.sync_resource_metrics.discover_all", return_value=[resource]):
        with patch("boto3.client") as mock_client:
            mock_cw = MagicMock()
            mock_cw.get_metric_statistics.return_value = mock_cw_response
            mock_client.return_value = mock_cw
            from scripts.sync_resource_metrics import run_backfill
            import tempfile
            with tempfile.TemporaryDirectory() as tmpdir:
                count = run_backfill(base_dir=tmpdir)
                assert count >= 1
```

- [ ] **Step 7: Run the failing test**

```bash
pytest tests/test_sync_resource_metrics.py::test_backfill_flow -v
```

Expected: FAIL with `ImportError: cannot import name 'run_backfill'`

- [ ] **Step 8: Implement run_backfill, run_incremental, and main**

```python
# scripts/sync_resource_metrics.py (append)


def run_backfill(base_dir=None):
    store = MetricsStore(base_dir=base_dir)
    resources = discover_all()
    logger.info(f"Discovered {len(resources)} resources for backfill")
    total = 0
    for resource in resources:
        try:
            records = fetch_cloudwatch_hourly(resource, hours=24 * 30)
            if records:
                store.write_hourly(records)
                total += len(records)
                logger.info(f"Backfilled {len(records)} points for {resource.id}")
        except Exception as e:
            logger.warning(f"Backfill failed for {resource.id}: {e}")
    store.close()
    logger.info(f"Backfill complete: {total} total points")
    return total


def run_incremental(base_dir=None):
    store = MetricsStore(base_dir=base_dir)
    resources = discover_all()
    logger.info(f"Discovered {len(resources)} resources for incremental sync")
    total = 0
    for resource in resources:
        try:
            records = fetch_cloudwatch_hourly(resource, hours=24)
            if records:
                store.write_hourly(records)
                total += len(records)
                logger.info(f"Synced {len(records)} points for {resource.id}")
        except Exception as e:
            logger.warning(f"Sync failed for {resource.id}: {e}")

    # Downsample previous month if it has just completed
    now = datetime.datetime.utcnow()
    prev_month = now.month - 1 or 12
    prev_year = now.year if now.month > 1 else now.year - 1
    try:
        inserted = store.downsample_month(prev_year, prev_month)
        if inserted:
            logger.info(f"Downsampled {inserted} daily rows for {prev_year}-{prev_month:02d}")
    except Exception as e:
        logger.warning(f"Downsample failed for {prev_year}-{prev_month:02d}: {e}")

    # Cleanup old aggregated data
    try:
        deleted = store.cleanup_old_daily(keep_days=180)
        if deleted:
            logger.info(f"Cleaned up {deleted} old daily rows")
    except Exception as e:
        logger.warning(f"Cleanup failed: {e}")

    store.close()
    logger.info(f"Incremental sync complete: {total} total points")
    return total


def main():
    args = parse_args()
    if args.backfill:
        run_backfill(base_dir=args.base_dir)
    elif args.incremental:
        run_incremental(base_dir=args.base_dir)
    elif args.downsample:
        year, month = args.downsample
        store = MetricsStore(base_dir=args.base_dir)
        inserted = store.downsample_month(year, month)
        logger.info(f"Downsampled {inserted} rows for {year}-{month:02d}")
        store.close()
    else:
        # Default to incremental
        run_incremental(base_dir=args.base_dir)


if __name__ == "__main__":
    main()
```

- [ ] **Step 9: Run test to verify it passes**

```bash
pytest tests/test_sync_resource_metrics.py::test_backfill_flow -v
```

Expected: PASS

- [ ] **Step 10: Commit**

```bash
git add scripts/sync_resource_metrics.py tests/test_sync_resource_metrics.py
git commit -m "feat(sync): add backfill, incremental, and main entrypoint"
```

---

## Task 3: `dashboard/api.py` — History API Route

**Files:**
- Modify: `dashboard/api.py`
- Test: `tests/test_dashboard_api_resources_history.py`

- [ ] **Step 1: Write the failing test for the history endpoint**

```python
# tests/test_dashboard_api_resources_history.py
from unittest.mock import patch
import pytest
from flask import Flask

from dashboard import dashboard_bp, _sessions
import dashboard.api  # noqa: F401


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setattr("dashboard.DASHBOARD_TOKEN", "test-secret-token")
    _sessions.clear()
    app = Flask(__name__)
    app.register_blueprint(dashboard_bp)
    with app.test_client() as c:
        yield c
    _sessions.clear()


@pytest.fixture
def auth_client(client):
    resp = client.post("/api/dashboard/auth", json={"token": "test-secret-token"})
    assert resp.status_code == 200
    return client


@patch("dashboard.api.MetricsStore")
def test_get_resource_history_24h(mock_store_cls, auth_client):
    mock_store = mock_store_cls.return_value
    mock_store.query_history.return_value = {
        "resource_id": "ec2:cn-north-1:i-123",
        "metric": "cpu_utilization",
        "range": "24h",
        "granularity": "hourly",
        "data": [{"timestamp": 1714113600, "value": 12.5}],
        "stats": {"min": 5.0, "avg": 12.5, "p95": 20.0, "max": 30.0},
    }

    resp = auth_client.get("/api/dashboard/resources/ec2:cn-north-1:i-123/history?range=24h")
    assert resp.status_code == 200
    assert resp.json["ok"] is True
    assert resp.json["granularity"] == "hourly"
    assert resp.json["data"][0]["value"] == 12.5
    assert resp.json["stats"]["avg"] == 12.5
    mock_store.close.assert_called_once()
```

- [ ] **Step 2: Run the failing test**

```bash
pytest tests/test_dashboard_api_resources_history.py::test_get_resource_history_24h -v
```

Expected: FAIL with `AssertionError: 404 != 200` (route does not exist)

- [ ] **Step 3: Add the history route to `dashboard/api.py`**

```python
# dashboard/api.py — add import at top
from dashboard.metrics_store import MetricsStore

# dashboard/api.py — add route near existing resources routes
@dashboard_bp.route("/api/dashboard/resources/<path:resource_id>/history", methods=["GET"])
@require_auth
def get_resource_history(resource_id):
    metric = request.args.get("metric", "cpu_utilization")
    range_label = request.args.get("range", "24h")
    valid_ranges = {"24h", "7d", "30d", "180d"}
    if range_label not in valid_ranges:
        return jsonify({"ok": False, "error": f"Invalid range. Use one of: {', '.join(valid_ranges)}"}), 400

    store = MetricsStore()
    try:
        result = store.query_history(resource_id, metric, range_label)
        return jsonify({"ok": True, **result})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500
    finally:
        store.close()
```

- [ ] **Step 4: Run test to verify it passes**

```bash
pytest tests/test_dashboard_api_resources_history.py::test_get_resource_history_24h -v
```

Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add dashboard/api.py tests/test_dashboard_api_resources_history.py
git commit -m "feat(api): add GET /resources/<id>/history endpoint"
```

---

## Task 4: `dashboard/static/app.js` — Frontend History Panel

**Files:**
- Modify: `dashboard/static/app.js`

- [ ] **Step 1: Add expand/collapse state and history data to ResourcesPage setup**

```javascript
// Inside ResourcesPage setup(), add after existing refs:
const expandedId = ref(null);
const historyData = ref(null);
const historyLoading = ref(false);
const historyRange = ref("24h");
const historyRanges = ["24h", "7d", "30d", "180d"];
```

- [ ] **Step 2: Add async loadHistory function**

```javascript
async function loadHistory(resourceId, range) {
  historyLoading.value = true;
  historyData.value = null;
  try {
    const data = await api(`/resources/${encodeURIComponent(resourceId)}/history?range=${range}`);
    historyData.value = data;
  } catch (e) {
    historyData.value = { error: e.message };
  } finally {
    historyLoading.value = false;
  }
}
```

- [ ] **Step 3: Add toggleExpand helper**

```javascript
function toggleExpand(id) {
  if (expandedId.value === id) {
    expandedId.value = null;
    historyData.value = null;
  } else {
    expandedId.value = id;
    loadHistory(id, historyRange.value);
  }
}
```

- [ ] **Step 4: Add history chart SVG helper**

```javascript
function historyChartSvg(data, color) {
  if (!data || data.length < 2) return '<span style="color:#cbd5e1">-</span>';
  const values = data.map(d => d.value != null ? d.value : d.avg_value);
  const valid = values.filter(v => v != null);
  if (valid.length < 2) return '<span style="color:#cbd5e1">-</span>';
  const min = Math.min(...valid);
  const max = Math.max(...valid);
  const range = max - min || 1;
  const pts = values.map((v, i) => {
    if (v == null) return "";
    const x = (i / (values.length - 1)) * 100;
    const y = 60 - ((v - min) / range) * 60;
    return `${x},${y}`;
  }).filter(Boolean).join(" ");
  return `<svg viewBox="0 0 100 60" width="100%" height="120" style="display:block"><polyline fill="none" stroke="${color}" stroke-width="2" points="${pts}"/></svg>`;
}
```

- [ ] **Step 5: Modify the table row template to add expand button and expanded row**

In the ResourcesPage template, change the `<tr>` for resources to:

```html
<tr v-for="r in filteredResources" :key="r.id" :class="{ pinned: isPinned(r.id) }">
  <td><button class="pin-btn" @click="togglePin(r.id)">{{ isPinned(r.id) ? '★' : '☆' }}</button></td>
  <td>{{ r.name }}</td>
  <td><span :class="'badge badge-' + r.type">{{ r.type }}</span></td>
  <td>{{ r.meta.region || '-' }}</td>
  <td>{{ r.type === 'ec2' ? (r.meta.instance_type || '-') : (r.meta.db_instance_class || '-') }}</td>
  <td>{{ r.type === 'ec2' ? (r.meta.os || '-') : (r.meta.engine || '-') }}</td>
  <td><code class="tag">{{ r.raw_id }}</code></td>
  <td>{{ r.status }}</td>
  <td>
    <span v-for="(v, k) in r.tags" :key="k" class="badge badge-tag" :title="k + ': ' + v">
      {{ k }}:{{ v }}
    </span>
    <span v-if="!r.tags || Object.keys(r.tags).length === 0" style="color:#cbd5e1">-</span>
  </td>
  <td v-html="sparklineSvg(r.sparkline, sparklineColor(r.type))"></td>
  <td>{{ formatStats(r.stats_7d) }}</td>
  <td>{{ formatStats(r.stats_30d) }}</td>
  <td><button class="pin-btn" @click="toggleExpand(r.id)">{{ expandedId === r.id ? '▼' : '▶' }}</button></td>
</tr>
<tr v-if="expandedId === r.id" :key="r.id + '-history'">
  <td colspan="13" style="background:#f8fafc;padding:16px">
    <div style="max-width:800px">
      <div style="display:flex;gap:8px;margin-bottom:12px">
        <button
          v-for="rng in historyRanges"
          :key="rng"
          :class="{ active: historyRange === rng }"
          @click="historyRange = rng; loadHistory(r.id, rng)"
          style="padding:4px 12px;border:1px solid #cbd5e1;border-radius:4px;background:#fff;cursor:pointer"
          :style="historyRange === rng ? 'background:#3b82f6;color:#fff;border-color:#3b82f6' : ''"
        >{{ rng }}</button>
      </div>
      <div v-if="historyLoading" style="color:#64748b">加载中...</div>
      <div v-else-if="historyData && historyData.error" style="color:#ef4444">{{ historyData.error }}</div>
      <div v-else-if="historyData && historyData.ok">
        <div style="font-size:12px;color:#64748b;margin-bottom:4px">
          粒度: {{ historyData.granularity }}
        </div>
        <div v-html="historyChartSvg(historyData.data, sparklineColor(r.type))"></div>
        <div style="display:flex;gap:24px;margin-top:8px;font-size:13px">
          <span>MIN: <b>{{ historyData.stats.min != null ? historyData.stats.min + '%' : '-' }}</b></span>
          <span>AVG: <b>{{ historyData.stats.avg != null ? historyData.stats.avg + '%' : '-' }}</b></span>
          <span>P95: <b>{{ historyData.stats.p95 != null ? historyData.stats.p95 + '%' : '-' }}</b></span>
          <span>MAX: <b>{{ historyData.stats.max != null ? historyData.stats.max + '%' : '-' }}</b></span>
        </div>
      </div>
    </div>
  </td>
</tr>
```

Also add a new `<th>` header for the expand column:

```html
<th style="width:40px"></th>
```

- [ ] **Step 6: Update the return object in setup() to expose new state and helpers**

```javascript
return {
  resources, pins, filterType, searchQ, filterRegion, filterStatus, filterClass,
  filterOs, filterTagKey, filterTagValue, onlyPinned,
  isPinned, togglePin, sparklineSvg, sparklineColor, formatStats, resetFilters,
  filteredResources, load,
  expandedId, historyData, historyLoading, historyRange, historyRanges,
  toggleExpand, loadHistory, historyChartSvg,
};
```

- [ ] **Step 7: Commit**

```bash
git add dashboard/static/app.js
git commit -m "feat(frontend): add expandable history panel with 24h/7d/30d/180d range switch"
```

---

## Task 5: Integration Verification

- [ ] **Step 1: Run all new tests together**

```bash
pytest tests/test_metrics_store.py tests/test_sync_resource_metrics.py tests/test_dashboard_api_resources_history.py -v
```

Expected: All PASS

- [ ] **Step 2: Run full test suite to ensure no regressions**

```bash
pytest tests/ -v
```

Expected: All PASS (or at least no new failures in existing tests)

- [ ] **Step 3: Verify the sync script can be executed**

```bash
python3 scripts/sync_resource_metrics.py --help
```

Expected: Shows usage with `--backfill`, `--incremental`, `--downsample`, `--base-dir`

- [ ] **Step 4: Add executable permission to the script**

```bash
chmod +x scripts/sync_resource_metrics.py
```

- [ ] **Step 5: Document cron setup in a comment inside the script or a README note**

Add this header comment to the top of `scripts/sync_resource_metrics.py` (after the shebang):

```python
"""
Cron setup example:
    0 3 * * * cd /home/ubuntu/kiro-devops && /usr/bin/python3 scripts/sync_resource_metrics.py --incremental >> /var/log/kiro-metrics-sync.log 2>&1

First run:
    python3 scripts/sync_resource_metrics.py --backfill
"""
```

- [ ] **Step 6: Final commit**

```bash
git add scripts/sync_resource_metrics.py
git commit -m "docs(sync): add cron setup example in script header"
```

---

## Self-Review

**1. Spec coverage:**
- ✅ Monthly raw DB (`raw_metrics_YYYY_MM.db`) — Task 1
- ✅ Unified aggregated DB (`aggregated_metrics.db`) — Task 1
- ✅ Daily downsampling (min/avg/p95/max) — Task 1
- ✅ Standalone sync script with `--backfill` / `--incremental` — Task 2
- ✅ History API `GET /resources/<id>/history?range=` — Task 3
- ✅ Frontend expandable panel with range switch — Task 4
- ✅ Cron integration — Task 5

**2. Placeholder scan:**
- ✅ No TBD/TODO/fill-in-details
- ✅ Every step shows exact code or exact command
- ✅ No "add appropriate error handling" vagueness

**3. Type consistency:**
- ✅ `MetricsStore` method signatures match across tasks
- ✅ `range_label` values (`24h`, `7d`, `30d`, `180d`) consistent in store, API, and frontend
- ✅ `resource_id` format consistent with existing codebase (`ec2:region:raw_id`)
