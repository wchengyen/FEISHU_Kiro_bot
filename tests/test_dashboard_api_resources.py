#!/usr/bin/env python3
"""Tests for dashboard resources API routes."""

from unittest.mock import patch

import pytest
from flask import Flask

from dashboard import dashboard_bp, _sessions
import dashboard.api  # noqa: F401
from dashboard.config_store import ConfigStore


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


@patch("dashboard.api.get_all_resources_with_metrics")
def test_get_resources(mock_get, auth_client):
    mock_get.return_value = {
        "resources": [
            {
                "id": "ec2:i-123",
                "type": "ec2",
                "name": "test1",
                "raw_id": "i-123",
                "status": "running",
                "meta": {},
                "sparkline": [10.0, 20.0],
                "current": 20.0,
            }
        ],
        "cached": False,
        "error": None,
    }
    resp = auth_client.get("/api/dashboard/resources")
    assert resp.status_code == 200
    assert resp.json["ok"] is True
    assert len(resp.json["resources"]) == 1
    assert resp.json["resources"][0]["id"] == "ec2:i-123"


@patch("dashboard.api.get_all_resources_with_metrics")
def test_get_resources_filter_by_type(mock_get, auth_client):
    mock_get.return_value = {
        "resources": [
            {"id": "ec2:i-123", "type": "ec2", "name": "test1", "raw_id": "i-123", "status": "running", "meta": {}, "sparkline": [], "current": None},
            {"id": "rds:my-db", "type": "rds", "name": "my-db", "raw_id": "my-db", "status": "available", "meta": {}, "sparkline": [], "current": None},
        ],
        "cached": False,
        "error": None,
    }
    resp = auth_client.get("/api/dashboard/resources?type=ec2")
    assert resp.status_code == 200
    assert len(resp.json["resources"]) == 1
    assert resp.json["resources"][0]["type"] == "ec2"


def test_get_pins(auth_client, monkeypatch, tmp_path):
    mappings_file = tmp_path / "dashboard_config.json"
    original_init = ConfigStore.__init__

    def patched_init(self, env_path=".env", mappings_path="dashboard_config.json"):
        original_init(self, env_path=env_path, mappings_path=str(mappings_file))

    monkeypatch.setattr("dashboard.api.ConfigStore.__init__", patched_init)

    resp = auth_client.get("/api/dashboard/resources/pins")
    assert resp.status_code == 200
    assert resp.json == {"ok": True, "pins": []}


def test_set_pins(auth_client, monkeypatch, tmp_path):
    mappings_file = tmp_path / "dashboard_config.json"
    original_init = ConfigStore.__init__

    def patched_init(self, env_path=".env", mappings_path="dashboard_config.json"):
        original_init(self, env_path=env_path, mappings_path=str(mappings_file))

    monkeypatch.setattr("dashboard.api.ConfigStore.__init__", patched_init)

    resp = auth_client.post("/api/dashboard/resources/pins", json={"pins": ["ec2:i-123"]})
    assert resp.status_code == 200
    assert resp.json == {"ok": True}

    resp = auth_client.get("/api/dashboard/resources/pins")
    assert resp.json["pins"] == ["ec2:i-123"]
