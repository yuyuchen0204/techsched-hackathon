"""Agent skills: the per-role playbooks, as versioned files instead of strings in the source.

A skill is one markdown file under `config/agent_skills/`: YAML front matter declares the machine-readable part
(the tools the role may use, its budget, when it must escalate) and the body is the prose the model is given as
its playbook. Two reasons this is a file and not a constant:

* the tool list is *enforced* — `ToolContext.allowed_tools` is built from it, so a skill is a second, narrower
  fence inside the role gate (a recovery agent is allowed `submit_break` by role, but its skill withholds it, so
  it must delegate rest to the break agent);
* the playbook can be read, reviewed and changed without touching Python, and the loaded set is served at
  `/api/dev/skills` so what the agent was actually told is inspectable rather than implied.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml

from app.config import get_settings

log = logging.getLogger(__name__)
SKILL_DIR = "config/agent_skills"


@dataclass
class Skill:
    name: str
    title: str
    description: str
    tools: tuple[str, ...]          # empty = every tool the role gate already allows
    max_tool_calls: int | None
    max_searches: int | None
    escalate_when: list[str] = field(default_factory=list)
    body: str = ""
    source: str = ""

    def prompt_text(self) -> str:
        """What the model is actually given for this role."""
        head = f"{self.title}\n{self.description}".strip()
        tail = ""
        if self.escalate_when:
            tail = "\n\nEscalate to a human when:\n" + "\n".join(f"- {x}" for x in self.escalate_when)
        return f"{head}\n\n{self.body}".strip() + tail

    def as_dict(self) -> dict[str, Any]:
        return {"name": self.name, "title": self.title, "description": self.description, "tools": list(self.tools),
                "max_tool_calls": self.max_tool_calls, "max_searches": self.max_searches,
                "escalate_when": self.escalate_when, "body": self.body, "source": self.source}


def _parse(path: Path) -> Skill | None:
    text = path.read_text(encoding="utf-8")
    if not text.startswith("---"):
        log.warning("skill %s has no front matter; ignored", path.name)
        return None
    _, _, rest = text.partition("---")
    raw, sep, body = rest.partition("\n---")
    if not sep:
        log.warning("skill %s has an unterminated front matter block; ignored", path.name)
        return None
    meta = yaml.safe_load(raw) or {}
    budget = meta.get("budget") or {}
    tools = meta.get("tools") or []
    if isinstance(tools, str):                      # `tools: "*"` = do not narrow past the role gate
        tools = [] if tools.strip() == "*" else [tools]
    return Skill(name=str(meta.get("name") or path.stem), title=str(meta.get("title") or path.stem),
                 description=str(meta.get("description") or "").strip(), tools=tuple(str(t) for t in tools),
                 max_tool_calls=budget.get("tool_calls"), max_searches=budget.get("searches"),
                 escalate_when=[str(x) for x in (meta.get("escalate_when") or [])],
                 body=body.lstrip("\n").strip(), source=f"{SKILL_DIR}/{path.name}")


@lru_cache(maxsize=1)
def load_skills() -> dict[str, Skill]:
    root = get_settings().resolve_path(SKILL_DIR)
    if not root.is_dir():
        log.warning("no skill directory at %s — agents run without playbooks", root)
        return {}
    out: dict[str, Skill] = {}
    for path in sorted(root.glob("*.md")):
        try:
            skill = _parse(path)
        except Exception:  # a malformed skill must not take the app down
            log.exception("skill %s failed to parse; ignored", path.name)
            continue
        if skill is not None:
            out[skill.name] = skill
    return out


def get_skill(role: str) -> Skill | None:
    return load_skills().get(role)


def allowed_tools(role: str) -> frozenset[str] | None:
    """The skill's tool allow-list, or None when the skill does not narrow the role gate."""
    skill = get_skill(role)
    if skill is None or not skill.tools:
        return None
    return frozenset(skill.tools)


def reset_skills_cache() -> None:
    load_skills.cache_clear()
