#!/usr/bin/env python3
"""Tests for dashboard API routes."""

import pytest
from flask import Flask

from dashboard import dashboard_bp, _sessions
import dashboard.api  # noqa: F401 — registers routes via side effect
from dashboard.config_store import ConfigStore


@pytest.fixture
def client(monkeypatch):
    """Create a test client with the dashboard blueprint registered."""
    monkeypatch.setattr("dashboard.DASHBOARD_TOKEN", "test-secret-token")
    _sessions.clear()

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


def test_get_agents(auth_client):
    resp = auth_client.get("/api/dashboard/agents")
    assert resp.status_code == 200
    assert "agents" in resp.json


def test_get_skills(auth_client):
    resp = auth_client.get("/api/dashboard/skills")
    assert resp.status_code == 200
    assert "skills" in resp.json


def test_get_config(auth_client, monkeypatch, tmp_path):
    env_file = tmp_path / "test.env"
    env_file.write_text("KIRO_AGENT=my-agent\nWEBHOOK_TOKEN=secret123\n")
    monkeypatch.setenv("ENV_PATH", str(env_file))

    resp = auth_client.get("/api/dashboard/config")
    assert resp.status_code == 200
    assert "config" in resp.json
    assert resp.json["config"]["WEBHOOK_TOKEN"] == "***"


def test_post_mappings(auth_client, monkeypatch, tmp_path):
    mappings_file = tmp_path / "dashboard_config.json"

    # Patch ConfigStore.__init__ in api.py so the default mappings_path points to tmp
    original_init = ConfigStore.__init__

    def patched_init(self, env_path=".env", mappings_path="dashboard_config.json"):
        original_init(self, env_path=env_path, mappings_path=str(mappings_file))

    monkeypatch.setattr("dashboard.api.ConfigStore.__init__", patched_init)

    payload = {"mappings": [{"alert_keyword": "cpu", "agent": "infra-agent"}]}
    resp = auth_client.post("/api/dashboard/mappings", json=payload)
    assert resp.status_code == 200
    assert resp.json == {"ok": True}
