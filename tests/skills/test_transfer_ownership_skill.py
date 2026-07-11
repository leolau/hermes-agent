"""Contract checks for the built-in ownership-transfer skill."""

from pathlib import Path
import re


SKILL = (
    Path(__file__).parents[2]
    / "skills"
    / "software-development"
    / "transfer-ownership"
    / "SKILL.md"
)


def test_transfer_skill_requires_approval_and_single_owner() -> None:
    content = SKILL.read_text(encoding="utf-8")
    name = re.search(r"^name: (.+)$", content, re.MULTILINE)
    description = re.search(r"^description: (.+)$", content, re.MULTILINE)

    assert name is not None and name.group(1) == "transfer-ownership"
    assert description is not None
    assert description.group(1).endswith(".")
    assert "clarify" in content
    assert "explicit approval" in content
    assert "hermes owner transfer <TARGET_USER_ID> --approve" in content
    assert "current owner's explicit approval" in content
