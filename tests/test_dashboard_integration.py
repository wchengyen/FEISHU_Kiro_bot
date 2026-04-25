#!/usr/bin/env python3
"""Test that the dashboard blueprint is properly integrated into webhook_server."""

import os

# Set required env vars before importing webhook_server
os.environ.setdefault("FEISHU_APP_ID", "test")
os.environ.setdefault("FEISHU_APP_SECRET", "test")
os.environ.setdefault("ENABLE_MEMORY", "false")

from webhook_server import webhook_app


def test_dashboard_routes_registered():
    """Verify dashboard routes exist in the webhook app's url_map."""
    assert webhook_app is not None, "webhook_app should be initialized"

    rules = [r.rule for r in webhook_app.url_map.iter_rules()]

    assert "/dashboard/" in rules, "/dashboard/ route should be registered"
    assert "/api/dashboard/auth" in rules, "/api/dashboard/auth route should be registered"
    assert "/api/dashboard/events" in rules, "/api/dashboard/events route should be registered"
