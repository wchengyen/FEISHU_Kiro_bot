#!/usr/bin/env python3
"""Dashboard API routes for agents, skills, config, and mappings."""

import os
from flask import jsonify, request

from dashboard import dashboard_bp, require_auth
from dashboard.kiro_scanner import list_agents, list_skills
from dashboard.config_store import ConfigStore, CORE_KEYS


SENSITIVE_KEYS = {"WEBHOOK_TOKEN", "DASHBOARD_TOKEN"}


@dashboard_bp.route("/api/dashboard/agents", methods=["GET"])
@require_auth
def get_agents():
    return jsonify({"ok": True, "agents": list_agents()})


@dashboard_bp.route("/api/dashboard/skills", methods=["GET"])
@require_auth
def get_skills():
    return jsonify({"ok": True, "skills": list_skills()})


@dashboard_bp.route("/api/dashboard/config", methods=["GET"])
@require_auth
def get_config():
    store = ConfigStore(env_path=os.environ.get("ENV_PATH", ".env"))
    cfg = store.read_core_config()
    for key in SENSITIVE_KEYS:
        if key in cfg:
            cfg[key] = "***"
    return jsonify({"ok": True, "config": cfg})


@dashboard_bp.route("/api/dashboard/config", methods=["POST"])
@require_auth
def post_config():
    payload = request.get_json(silent=True) or {}
    store = ConfigStore(env_path=os.environ.get("ENV_PATH", ".env"))
    updates = {k: v for k, v in payload.items() if k in CORE_KEYS}
    if updates:
        store.write_core_config(updates)
    return jsonify({"ok": True})


@dashboard_bp.route("/api/dashboard/mappings", methods=["GET"])
@require_auth
def get_mappings():
    store = ConfigStore(env_path=os.environ.get("ENV_PATH", ".env"))
    mappings = store.read_mappings()
    return jsonify({"ok": True, "mappings": mappings})


@dashboard_bp.route("/api/dashboard/mappings", methods=["POST"])
@require_auth
def post_mappings():
    payload = request.get_json(silent=True) or {}
    store = ConfigStore(env_path=os.environ.get("ENV_PATH", ".env"))
    mappings = payload.get("mappings", [])
    store.write_mappings(mappings)
    return jsonify({"ok": True})
