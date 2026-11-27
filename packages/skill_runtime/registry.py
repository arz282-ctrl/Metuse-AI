"""Versioned skill registry loading allowlisted vendored packs as untrusted reference data."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path

from packages.shared.errors import NotFoundError, ValidationDomainError

_REPO_ROOT = Path(__file__).resolve().parents[2]
_DEFAULT_SKILLS_DIR = _REPO_ROOT / "skills"

# Only the three approved Phase 2 production skill packs may ever be loaded.
ALLOWED_PACK_SPECS: dict[str, tuple[str, str, str]] = {
    "research-pack": (
        "1",
        "Research Pack",
        "Market, niche, audience, competitor, and content-gap research playbook.",
    ),
    "marketing-seo-pack": (
        "1",
        "Marketing, Writing & SEO/AEO Pack",
        "Content briefs, copywriting, hooks/scripts/CTAs, SEO metadata, and AEO answer passages.",
    ),
    "media-pack": (
        "1",
        "Media-Pack Creative Production",
        "Creative briefs, timed storyboards, shot lists, video prompts, and edit decision lists.",
    ),
}


@dataclass(frozen=True)
class SkillDocument:
    """Immutable versioned skill pack loaded strictly as untrusted reference data."""

    skill_id: str
    version: str
    display_name: str
    description: str
    source_path: str
    content_sha256: str
    raw_markdown: str

    def as_untrusted_reference_block(self, *, max_chars: int = 12000) -> str:
        """Frame skill markdown explicitly as untrusted reference data, never system policy."""
        excerpt = self.raw_markdown[:max_chars]
        return (
            f'<untrusted_skill_reference skill_id="{self.skill_id}" '
            f'version="{self.version}" sha256="{self.content_sha256[:16]}">\n'
            f"{excerpt}\n"
            "</untrusted_skill_reference>"
        )


class SkillRegistry:
    """In-memory versioned registry for the three allowlisted Phase 2 skill packs."""

    def __init__(self, skills_dir: Path | None = None) -> None:
        self._skills_dir = skills_dir or _DEFAULT_SKILLS_DIR
        self._cache: dict[tuple[str, str], SkillDocument] = {}

    def list_skills(self) -> list[SkillDocument]:
        """Load and return all allowlisted production skill packs."""
        return [self.get_skill(skill_id) for skill_id in sorted(ALLOWED_PACK_SPECS)]

    def get_skill(self, skill_id: str, *, version: str | None = None) -> SkillDocument:
        """Load a single allowlisted skill pack; reject unapproved pack IDs."""
        cleaned = skill_id.strip()
        if cleaned not in ALLOWED_PACK_SPECS:
            raise ValidationDomainError(
                f"Skill '{cleaned}' is not in the approved production skill allowlist",
                details={"allowed_skills": sorted(ALLOWED_PACK_SPECS)},
            )
        default_version, display_name, description = ALLOWED_PACK_SPECS[cleaned]
        resolved_version = (version or default_version).strip()
        if resolved_version != default_version:
            raise NotFoundError(f"Skill '{cleaned}' version '{resolved_version}' was not found")

        cache_key = (cleaned, resolved_version)
        if cache_key in self._cache:
            return self._cache[cache_key]

        skill_path = (self._skills_dir / cleaned / "SKILL.md").resolve()
        if not skill_path.is_file():
            raise NotFoundError(f"Skill file for '{cleaned}' is missing")

        raw_bytes = skill_path.read_bytes()
        sha256 = hashlib.sha256(raw_bytes).hexdigest()
        doc = SkillDocument(
            skill_id=cleaned,
            version=resolved_version,
            display_name=display_name,
            description=description,
            source_path=f"skills/{cleaned}/SKILL.md",
            content_sha256=sha256,
            raw_markdown=raw_bytes.decode("utf-8"),
        )
        self._cache[cache_key] = doc
        return doc
