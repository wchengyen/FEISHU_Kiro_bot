#!/usr/bin/env python3
"""Tests for dashboard scheduler CRUD API routes."""

import pytest
from flask import Flask

from dashboard import dashboard_bp, _sessions
import dashboard.api  # noqa: F401 — registers routes via side effect


@pytest.fixture
def client(monkeypatch, tmp_path):
    """Create a test client with the dashboard blueprint registered."""
    monkeypatch.setattr("dashboard.DASHBOARD_TOKEN", "test-secret-token")
    _sessions.clear()

    # Use a temporary file for scheduled jobs to avoid side effects
    test_jobs_file = tmp_path / "test_scheduled_jobs.json"
    monkeypatch.setattr("scheduler.JOBS_FILE", test_jobs_file)

    app = Flask(__name__)
    app.register_blueprint(dashboard_bp)
    with app.test_client() as c:
        yield c
    _sessions.clear()


@pytest.fixture
def auth_client(client):
    """Log in and return an authenticated test client."""
    resp = client.post("/api/dashboard/auth", json={"token": "test-secret-token"})
    assert resp.status_code == 200
    return client


def test_scheduler_crud(auth_client):
    # 1. Create job via POST
    payload = {
        "user_id": "test-user",
        "frequency": "每天",
        "time_str": "09:00",
        "prompt": "check EC2 status",
    }
    resp = auth_client.post("/api/dashboard/scheduler", json=payload)
    assert resp.status_code == 200
    assert resp.json["ok"] is True
    assert "job_id" in resp.json
    job_id = resp.json["job_id"]

    # 2. List jobs via GET and verify job_id is in list
    resp = auth_client.get("/api/dashboard/scheduler")
    assert resp.status_code == 200
    assert resp.json["ok"] is True
    jobs = resp.json["jobs"]
    ids = [j["id"] for j in jobs]
    assert job_id in ids

    # 3. Update via PUT (disable)
    resp = auth_client.put(f"/api/dashboard/scheduler/{job_id}", json={"enabled": False})
    assert resp.status_code == 200
    assert resp.json == {"ok": True}

    # Verify job is disabled
    resp = auth_client.get("/api/dashboard/scheduler")
    assert resp.status_code == 200
    job = next(j for j in resp.json["jobs"] if j["id"] == job_id)
    assert job["enabled"] is False

    # 4. Delete job via DELETE
    resp = auth_client.delete(f"/api/dashboard/scheduler/{job_id}")
    assert resp.status_code == 200
    assert resp.json == {"ok": True}

    # Verify job is gone
    resp = auth_client.get("/api/dashboard/scheduler")
    assert resp.status_code == 200
    jobs = resp.json["jobs"]
    ids = [j["id"] for j in jobs]
    assert job_id not in ids
