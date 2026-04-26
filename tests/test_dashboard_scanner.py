#!/usr/bin/env python3
"""Tests for dashboard.kiro_scanner"""

import json
import pytest

from dashboard.kiro_scanner import (
    list_agents,
    list_skills,
    create_skill,
    delete_skill,
    get_agent_skills,
    add_skill_to_agent,
    remove_skill_from_agent,
)


class TestListAgents:
    def test_list_agents_returns_list_with_name_and_description(self, tmp_path, monkeypatch):
        """list_agents should return a list of dicts with name and description."""
        agents_dir = tmp_path / ".kiro" / "agents"
        agents_dir.mkdir(parents=True)

        # Create a valid agent JSON file
        agent_file = agents_dir / "agent1.json"
        agent_file.write_text(json.dumps({
            "name": "TestAgent",
            "description": "A test agent",
            "tools": ["tool1", "tool2"],
            "resources": ["res1"],
        }))

        monkeypatch.setattr("dashboard.kiro_scanner.AGENTS_DIR", str(agents_dir))
        agents = list_agents()

        assert isinstance(agents, list)
        assert len(agents) == 1
        assert agents[0]["name"] == "TestAgent"
        assert agents[0]["description"] == "A test agent"
        assert agents[0]["tools"] == ["tool1", "tool2"]
        assert agents[0]["resources"] == ["res1"]

    def test_list_agents_skips_malformed_json(self, tmp_path, monkeypatch):
        """list_agents should skip files that fail to parse."""
        agents_dir = tmp_path / ".kiro" / "agents"
        agents_dir.mkdir(parents=True)

        valid = agents_dir / "valid.json"
        valid.write_text(json.dumps({"name": "Valid", "description": "ok"}))

        invalid = agents_dir / "invalid.json"
        invalid.write_text("not json")

        monkeypatch.setattr("dashboard.kiro_scanner.AGENTS_DIR", str(agents_dir))
        agents = list_agents()

        assert len(agents) == 1
        assert agents[0]["name"] == "Valid"

    def test_list_agents_missing_dir_returns_empty(self, tmp_path, monkeypatch):
        """list_agents should return empty list when agents dir doesn't exist."""
        nonexistent = tmp_path / ".kiro" / "agents"
        monkeypatch.setattr("dashboard.kiro_scanner.AGENTS_DIR", str(nonexistent))
        agents = list_agents()
        assert agents == []


class TestListSkills:
    def test_list_skills_returns_list_with_name_and_description(self, tmp_path, monkeypatch):
        """list_skills should return a list of dicts with name and description."""
        skills_dir = tmp_path / ".kiro" / "skills"
        skill_dir = skills_dir / "my-skill"
        skill_dir.mkdir(parents=True)

        skill_md = skill_dir / "SKILL.md"
        skill_md.write_text("""---
name: MySkill
description: A test skill
triggers: ["trigger1", "trigger2"]
---

# Content
""")

        monkeypatch.setattr("dashboard.kiro_scanner.SKILLS_DIR", str(skills_dir))
        skills = list_skills()

        assert isinstance(skills, list)
        assert len(skills) == 1
        assert skills[0]["name"] == "MySkill"
        assert skills[0]["description"] == "A test skill"
        assert skills[0]["triggers"] == ["trigger1", "trigger2"]
        assert "path" in skills[0]

    def test_list_skills_missing_frontmatter_uses_fallback(self, tmp_path, monkeypatch):
        """If frontmatter is missing, use directory name as name, first line as description."""
        skills_dir = tmp_path / ".kiro" / "skills"
        skill_dir = skills_dir / "fallback-skill"
        skill_dir.mkdir(parents=True)

        skill_md = skill_dir / "SKILL.md"
        skill_md.write_text("This is the first line.\n\nMore content.\n")

        monkeypatch.setattr("dashboard.kiro_scanner.SKILLS_DIR", str(skills_dir))
        skills = list_skills()

        assert len(skills) == 1
        assert skills[0]["name"] == "fallback-skill"
        assert skills[0]["description"] == "This is the first line."

    def test_list_skills_missing_dir_returns_empty(self, tmp_path, monkeypatch):
        """list_skills should return empty list when skills dir doesn't exist."""
        nonexistent = tmp_path / ".kiro" / "skills"
        monkeypatch.setattr("dashboard.kiro_scanner.SKILLS_DIR", str(nonexistent))
        skills = list_skills()
        assert skills == []


class TestCreateSkill:
    def test_create_skill_writes_file_with_frontmatter(self, tmp_path, monkeypatch):
        skills_dir = tmp_path / ".kiro" / "skills"
        monkeypatch.setattr("dashboard.kiro_scanner.SKILLS_DIR", str(skills_dir))

        ok = create_skill("new-skill", "A new skill for testing")
        assert ok is True

        skill_file = skills_dir / "new-skill" / "SKILL.md"
        assert skill_file.exists()

        content = skill_file.read_text(encoding="utf-8")
        assert "name: new-skill" in content
        assert "description: A new skill for testing" in content
        assert "triggers: []" in content

    def test_create_skill_rejects_duplicate(self, tmp_path, monkeypatch):
        skills_dir = tmp_path / ".kiro" / "skills"
        skill_dir = skills_dir / "existing"
        skill_dir.mkdir(parents=True)
        (skill_dir / "SKILL.md").write_text("---\nname: existing\n---\n")

        monkeypatch.setattr("dashboard.kiro_scanner.SKILLS_DIR", str(skills_dir))

        ok = create_skill("existing", "desc")
        assert ok is False

    def test_create_skill_rejects_invalid_name(self, tmp_path, monkeypatch):
        skills_dir = tmp_path / ".kiro" / "skills"
        monkeypatch.setattr("dashboard.kiro_scanner.SKILLS_DIR", str(skills_dir))

        assert create_skill("", "empty") is False
        assert create_skill("hello world", "space") is False
        assert create_skill("../escape", "traversal") is False
        assert create_skill("a/b", "slash") is False


class TestDeleteSkill:
    def test_delete_skill_removes_directory(self, tmp_path, monkeypatch):
        skills_dir = tmp_path / ".kiro" / "skills"
        skill_dir = skills_dir / "to-delete"
        skill_dir.mkdir(parents=True)
        (skill_dir / "SKILL.md").write_text("---\nname: to-delete\n---\n")

        monkeypatch.setattr("dashboard.kiro_scanner.SKILLS_DIR", str(skills_dir))
        monkeypatch.setattr("dashboard.kiro_scanner.AGENTS_DIR", str(tmp_path / ".kiro" / "agents"))

        ok = delete_skill("to-delete")
        assert ok is True
        assert not skill_dir.exists()

    def test_delete_skill_returns_false_for_missing(self, tmp_path, monkeypatch):
        skills_dir = tmp_path / ".kiro" / "skills"
        monkeypatch.setattr("dashboard.kiro_scanner.SKILLS_DIR", str(skills_dir))
        monkeypatch.setattr("dashboard.kiro_scanner.AGENTS_DIR", str(tmp_path / ".kiro" / "agents"))

        ok = delete_skill("nonexistent")
        assert ok is False

    def test_delete_skill_cleans_agent_references(self, tmp_path, monkeypatch):
        skills_dir = tmp_path / ".kiro" / "skills"
        agents_dir = tmp_path / ".kiro" / "agents"
        skill_dir = skills_dir / "shared-skill"
        skill_dir.mkdir(parents=True)
        (skill_dir / "SKILL.md").write_text("---\nname: shared-skill\n---\n")

        agents_dir.mkdir(parents=True)
        agent_file = agents_dir / "agent1.json"
        agent_file.write_text(json.dumps({
            "name": "agent1",
            "resources": [
                "skill://.kiro/skills/shared-skill/SKILL.md",
                "skill://.kiro/skills/other-skill/SKILL.md",
            ],
        }))

        monkeypatch.setattr("dashboard.kiro_scanner.SKILLS_DIR", str(skills_dir))
        monkeypatch.setattr("dashboard.kiro_scanner.AGENTS_DIR", str(agents_dir))

        ok = delete_skill("shared-skill")
        assert ok is True

        data = json.loads(agent_file.read_text(encoding="utf-8"))
        assert data["resources"] == ["skill://.kiro/skills/other-skill/SKILL.md"]


class TestGetAgentSkills:
    def test_get_agent_skills_extracts_skill_names(self, tmp_path, monkeypatch):
        agents_dir = tmp_path / ".kiro" / "agents"
        agents_dir.mkdir(parents=True)
        agent_file = agents_dir / "my-agent.json"
        agent_file.write_text(json.dumps({
            "name": "my-agent",
            "resources": [
                "skill://.kiro/skills/skill-a/SKILL.md",
                "file://.kiro/skills/skill-b/SKILL.md",
                "skill://.kiro/skills/skill-c/SKILL.md",
            ],
        }))

        monkeypatch.setattr("dashboard.kiro_scanner.AGENTS_DIR", str(agents_dir))

        skills = get_agent_skills("my-agent")
        assert len(skills) == 2
        assert {s["name"] for s in skills} == {"skill-a", "skill-c"}

    def test_get_agent_skills_missing_agent(self, tmp_path, monkeypatch):
        agents_dir = tmp_path / ".kiro" / "agents"
        monkeypatch.setattr("dashboard.kiro_scanner.AGENTS_DIR", str(agents_dir))

        skills = get_agent_skills("missing")
        assert skills == []


class TestAddSkillToAgent:
    def test_add_skill_to_agent_appends_resource(self, tmp_path, monkeypatch):
        agents_dir = tmp_path / ".kiro" / "agents"
        agents_dir.mkdir(parents=True)
        agent_file = agents_dir / "agent1.json"
        agent_file.write_text(json.dumps({
            "name": "agent1",
            "resources": ["skill://.kiro/skills/existing/SKILL.md"],
        }))

        monkeypatch.setattr("dashboard.kiro_scanner.AGENTS_DIR", str(agents_dir))

        ok = add_skill_to_agent("agent1", "new-skill")
        assert ok is True

        data = json.loads(agent_file.read_text(encoding="utf-8"))
        assert "skill://.kiro/skills/new-skill/SKILL.md" in data["resources"]
        assert "skill://.kiro/skills/existing/SKILL.md" in data["resources"]

    def test_add_skill_to_agent_idempotent(self, tmp_path, monkeypatch):
        agents_dir = tmp_path / ".kiro" / "agents"
        agents_dir.mkdir(parents=True)
        agent_file = agents_dir / "agent1.json"
        agent_file.write_text(json.dumps({
            "name": "agent1",
            "resources": ["skill://.kiro/skills/existing/SKILL.md"],
        }))

        monkeypatch.setattr("dashboard.kiro_scanner.AGENTS_DIR", str(agents_dir))

        ok1 = add_skill_to_agent("agent1", "existing")
        ok2 = add_skill_to_agent("agent1", "existing")
        assert ok1 is True
        assert ok2 is True

        data = json.loads(agent_file.read_text(encoding="utf-8"))
        assert data["resources"].count("skill://.kiro/skills/existing/SKILL.md") == 1

    def test_add_skill_to_agent_missing_agent(self, tmp_path, monkeypatch):
        agents_dir = tmp_path / ".kiro" / "agents"
        monkeypatch.setattr("dashboard.kiro_scanner.AGENTS_DIR", str(agents_dir))

        ok = add_skill_to_agent("missing", "skill")
        assert ok is False


class TestRemoveSkillFromAgent:
    def test_remove_skill_from_agent_filters_resource(self, tmp_path, monkeypatch):
        agents_dir = tmp_path / ".kiro" / "agents"
        agents_dir.mkdir(parents=True)
        agent_file = agents_dir / "agent1.json"
        agent_file.write_text(json.dumps({
            "name": "agent1",
            "resources": [
                "skill://.kiro/skills/skill-a/SKILL.md",
                "skill://.kiro/skills/skill-b/SKILL.md",
            ],
        }))

        monkeypatch.setattr("dashboard.kiro_scanner.AGENTS_DIR", str(agents_dir))

        ok = remove_skill_from_agent("agent1", "skill-a")
        assert ok is True

        data = json.loads(agent_file.read_text(encoding="utf-8"))
        assert data["resources"] == ["skill://.kiro/skills/skill-b/SKILL.md"]

    def test_remove_skill_from_agent_missing_agent(self, tmp_path, monkeypatch):
        agents_dir = tmp_path / ".kiro" / "agents"
        monkeypatch.setattr("dashboard.kiro_scanner.AGENTS_DIR", str(agents_dir))

        ok = remove_skill_from_agent("missing", "skill")
        assert ok is False
