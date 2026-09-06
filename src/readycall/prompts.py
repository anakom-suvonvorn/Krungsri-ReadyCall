"""Loads the versioned LLM prompt files from `prompts/th/` (`D31`, `D18`).

Separate from `voiceprompts.py`, which loads the things a *caller hears*. These are the
things a *model is told*, and they differ in one way that matters: a spoken line is
rendered once at build time and cached by hash, while a prompt is rendered per call with
that call's variables and must be recorded by version alongside the output it produced.

Two rules the format enforces rather than documents:

* **A prompt is a file with a version in its name.** `summarize_intake.v1.md`. Editing a
  live prompt in place makes every stored result unattributable — `analyses.prompt_version`
  would name a file whose content has changed. A new prompt is a new file.
* **Every variable a template uses must be declared.** The front matter lists them, and
  rendering refuses an undeclared slot or a missing value. The failure this prevents is
  silent: `str.format` on a missing key raises, but a *typo'd* key that happens to exist
  renders the wrong thing and nobody ever sees it.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from readycall.errors import ConfigError
from readycall.logging import get_logger
from readycall.ports.llm import PromptRef

log = get_logger(__name__)

#: `prompts/th/<id>.<version>.md`
_FILENAME = re.compile(r"^(?P<id>[a-z0-9_]+)\.(?P<version>v\d+)\.md$")
_FRONT_MATTER = re.compile(r"^---\n(?P<yaml>.*?)\n---\n(?P<body>.*)$", re.S)
#: `{name}` but not `{{name}}` — the latter is a literal brace in a JSON example.
_SLOT = re.compile(r"(?<!\{)\{([a-z_][a-z0-9_]*)\}(?!\})")


@dataclass(frozen=True, slots=True)
class PromptTemplate:
    id: str
    version: str
    description: str
    variables: tuple[str, ...]
    body: str

    @property
    def ref(self) -> PromptRef:
        return PromptRef(id=self.id, version=self.version)

    @property
    def key(self) -> str:
        return f"{self.id}.{self.version}"

    def render(self, variables: dict[str, Any]) -> str:
        """Fill the template, refusing anything the front matter did not declare.

        Missing values are a `ConfigError` rather than an empty string on purpose: a
        prompt that silently loses its transcript still returns a confident-looking
        summary of nothing, which is `D16`'s hazard in the place it is least visible.
        """
        missing = [name for name in self.variables if name not in variables]
        if missing:
            raise ConfigError(f"prompt {self.key} is missing variables: {sorted(missing)}")
        undeclared = sorted(set(variables) - set(self.variables))
        if undeclared:
            raise ConfigError(f"prompt {self.key} was given undeclared variables: {undeclared}")
        return self.body.format(**{k: variables[k] for k in self.variables})


class PromptLibrary:
    """Every prompt on disk, keyed by `<id>.<version>`."""

    def __init__(self, templates: dict[str, PromptTemplate]) -> None:
        self._templates = templates

    def __len__(self) -> int:
        return len(self._templates)

    def get(self, ref: PromptRef) -> PromptTemplate:
        try:
            return self._templates[ref.key]
        except KeyError:
            known = ", ".join(sorted(self._templates)) or "none"
            raise ConfigError(f"no prompt {ref.key!r}; loaded: {known}") from None

    def latest(self, prompt_id: str) -> PromptTemplate:
        """The highest version of one prompt. For callers that do not pin a version."""
        matches = [t for t in self._templates.values() if t.id == prompt_id]
        if not matches:
            raise ConfigError(f"no prompt with id {prompt_id!r}")
        return max(matches, key=lambda t: int(t.version.lstrip("v")))

    @classmethod
    def load(cls, directory: Path | str = Path("prompts/th")) -> PromptLibrary:
        root = Path(directory)
        templates: dict[str, PromptTemplate] = {}
        if not root.exists():
            log.warning("no prompt directory; the rule-based path is the only one", path=str(root))
            return cls(templates)

        for path in sorted(root.glob("*.md")):
            name = _FILENAME.match(path.name)
            if not name:
                raise ConfigError(
                    f"{path.name}: prompt files are named `<id>.<version>.md`, e.g. "
                    "`summarize_intake.v1.md` — the version is part of the identity (`D18`)"
                )
            matter = _FRONT_MATTER.match(path.read_text(encoding="utf-8"))
            if not matter:
                raise ConfigError(f"{path.name}: missing the `---` front matter block")
            meta = yaml.safe_load(matter.group("yaml")) or {}
            body = matter.group("body").strip()

            declared = tuple(str(v) for v in (meta.get("variables") or ()))
            used = set(_SLOT.findall(body))
            if used - set(declared):
                raise ConfigError(
                    f"{path.name}: uses undeclared variables {sorted(used - set(declared))}"
                )
            if set(declared) - used:
                # Also an error, not a warning: a declared-but-unused variable means the
                # caller is computing something the model never sees, and the cost of
                # computing it is paid on every call for nothing.
                raise ConfigError(
                    f"{path.name}: declares unused variables {sorted(set(declared) - used)}"
                )

            template = PromptTemplate(
                id=str(meta.get("id") or name.group("id")),
                version=str(meta.get("version") or name.group("version")),
                description=str(meta.get("description", "")).strip(),
                variables=declared,
                body=body,
            )
            if template.id != name.group("id") or template.version != name.group("version"):
                raise ConfigError(
                    f"{path.name}: front matter says {template.key!r}, which disagrees with "
                    "the filename. The filename is the identity stored against every result"
                )
            templates[template.key] = template

        log.info("prompt library loaded", prompts=len(templates), directory=str(root))
        return cls(templates)


__all__ = ["PromptLibrary", "PromptTemplate"]
