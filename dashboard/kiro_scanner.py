#!/usr/bin/env python3
"""Scanner for Kiro CLI agents and skills."""

import json
import os
import re
import shutil
from pathlib import Path

import yaml

AGENTS_DIR = Path(os.path.expanduser("~/.kiro/agents"))
SKILLS_DIR = Path(os.path.expanduser("~/.kiro/skills"))

_SKILL_NAME_RE = re.compile(r"^[a-zA-Z0-9_\-]+$")


def _skill_ref(skill_name: str) -> str:
    return f"skill://.kiro/skills/{skill_name}/SKILL.md"


def _agent_file(agent_name: str) -> Path:
    return Path(AGENTS_DIR) / f"{agent_name}.json"


def create_skill(name: str, description: str) -> bool:
    """Create ~/.kiro/skills/<name>/SKILL.md with YAML frontmatter.

    Returns True on success, False if name is invalid or skill already exists.
    """
    if not name or not _SKILL_NAME_RE.match(name):
        return False

    skill_dir = Path(SKILLS_DIR) / name
    skill_file = skill_dir / "SKILL.md"

    if skill_file.exists():
        return False

    skill_dir.mkdir(parents=True, exist_ok=True)

    frontmatter = yaml.safe_dump(
        {"name": name, "description": description, "triggers": []},
        allow_unicode=True,
        sort_keys=False,
    )

    content = f"---\n{frontmatter}---\n\n# {name}\n"
    skill_file.write_text(content, encoding="utf-8")
    return True


def delete_skill(name: str) -> bool:
    """Delete ~/.kiro/skills/<name>/ and remove skill:// references from all agents.

    Returns True if the skill directory was removed.
    """
    skill_dir = Path(SKILLS_DIR) / name
    if not skill_dir.exists():
        return False

    ref = _skill_ref(name)

    # Clean references from all agents
    for agent_file in Path(AGENTS_DIR).glob("*.json"):
        try:
            data = json.loads(agent_file.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            continue

        if not isinstance(data, dict):
            continue

        resources = data.get("resources", [])
        if isinstance(resources, list) and ref in resources:
            resources.remove(ref)
            data["resources"] = resources
            agent_file.write_text(
                json.dumps(data, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )

    shutil.rmtree(skill_dir)
    return True


def get_agent_skills(agent_name: str) -> list[dict]:
    """Read agent JSON and return list of linked skill dicts.

    Each dict has keys: name, resource.
    """
    agent_file = _agent_file(agent_name)
    if not agent_file.exists():
        return []

    try:
        data = json.loads(agent_file.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return []

    if not isinstance(data, dict):
        return []

    resources = data.get("resources", [])
    if not isinstance(resources, list):
        return []

    skills = []
    for resource in resources:
        if not isinstance(resource, str):
            continue
        if resource.startswith("skill://.kiro/skills/") and resource.endswith("/SKILL.md"):
            skill_name = resource[len("skill://.kiro/skills/") : -len("/SKILL.md")]
            skills.append({"name": skill_name, "resource": resource})

    return skills


def add_skill_to_agent(agent_name: str, skill_name: str) -> bool:
    """Append skill:// reference to agent's resources if not present.

    Returns True if the reference was added, False if agent not found or invalid.
    """
    agent_file = _agent_file(agent_name)
    if not agent_file.exists():
        return False

    try:
        data = json.loads(agent_file.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return False

    if not isinstance(data, dict):
        return False

    ref = _skill_ref(skill_name)
    resources = data.get("resources", [])
    if not isinstance(resources, list):
        resources = []

    if ref in resources:
        return True  # Already present — idempotent

    resources.append(ref)
    data["resources"] = resources
    agent_file.write_text(
        json.dumps(data, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return True


def remove_skill_from_agent(agent_name: str, skill_name: str) -> bool:
    """Remove skill:// reference from agent's resources.

    Returns True if the reference was removed (or wasn't present), False on error.
    """
    agent_file = _agent_file(agent_name)
    if not agent_file.exists():
        return False

    try:
        data = json.loads(agent_file.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return False

    if not isinstance(data, dict):
        return False

    ref = _skill_ref(skill_name)
    resources = data.get("resources", [])
    if not isinstance(resources, list):
        return True

    if ref not in resources:
        return True

    resources.remove(ref)
    data["resources"] = resources
    agent_file.write_text(
        json.dumps(data, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return True


def list_agents() -> list[dict]:
    """Scan ~/.kiro/agents/*.json and return list of agent info dicts."""
    agents_dir = Path(AGENTS_DIR)
    if not agents_dir.exists():
        return []

    agents: list[dict] = []
    for agent_file in agents_dir.glob("*.json"):
        try:
            data = json.loads(agent_file.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            continue

        if not isinstance(data, dict):
            continue

        agents.append(
            {
                "name": data.get("name", ""),
                "description": data.get("description", ""),
                "tools": data.get("tools", []),
                "resources": data.get("resources", []),
            }
        )

    return agents


def _extract_frontmatter(content: str) -> tuple[str | None, str]:
    """Extract YAML frontmatter from markdown content.

    Returns (frontmatter_yaml, remaining_content) or (None, content) if no frontmatter.
    """
    if not content.startswith("---"):
        return None, content

    # Find the closing ---
    end_idx = content.find("\n---", 3)
    if end_idx == -1:
        return None, content

    frontmatter = content[3:end_idx].strip()
    remaining = content[end_idx + 4 :].lstrip("\n")
    return frontmatter, remaining


def get_skill_content(name: str) -> str | None:
    """Read the raw content of ~/.kiro/skills/<name>/SKILL.md.

    Returns None if the skill doesn't exist.
    """
    skill_file = Path(SKILLS_DIR) / name / "SKILL.md"
    if not skill_file.exists():
        return None
    try:
        return skill_file.read_text(encoding="utf-8")
    except OSError:
        return None


def list_skills() -> list[dict]:
    """Scan ~/.kiro/skills/**/SKILL.md and return list of skill info dicts."""
    skills_dir = Path(SKILLS_DIR)
    if not skills_dir.exists():
        return []

    skills: list[dict] = []
    for skill_file in skills_dir.rglob("SKILL.md"):
        try:
            content = skill_file.read_text(encoding="utf-8")
        except OSError:
            continue

        frontmatter, remainder = _extract_frontmatter(content)

        if frontmatter is not None:
            try:
                meta = yaml.safe_load(frontmatter) or {}
            except yaml.YAMLError:
                meta = {}
        else:
            meta = {}

        if "name" in meta:
            name = meta["name"]
        else:
            name = skill_file.parent.name

        if "description" in meta:
            description = meta["description"]
        else:
            # Use first non-empty line as description
            first_line = content.strip().splitlines()[0] if content.strip() else ""
            description = first_line

        skills.append(
            {
                "name": name,
                "description": description,
                "triggers": meta.get("triggers", []),
                "path": str(skill_file),
            }
        )

    return skills
