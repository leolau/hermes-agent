"""Contract checks for the built-in artifact-promotion skill."""

from pathlib import Path
import re


SKILL = (
    Path(__file__).parents[2]
    / "skills"
    / "software-development"
    / "promote-artifact"
    / "SKILL.md"
)


def test_promote_skill_requires_approval_and_forbids_raw_data() -> None:
    content = SKILL.read_text(encoding="utf-8")
    name = re.search(r"^name: (.+)$", content, re.MULTILINE)
    description = re.search(r"^description: (.+)$", content, re.MULTILINE)

    assert name is not None and name.group(1) == "promote-artifact"
    assert description is not None
    assert len(description.group(1)) <= 60
    assert description.group(1).endswith(".")
    assert "clarify" in content
    assert "explicit approval" in content
    assert "hermes promote KIND:REF --approve" in content
    assert "Never promote raw application or user data" in content
