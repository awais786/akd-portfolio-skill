"""The skill files are instructions for Claude, so check what can be checked: they are well-formed, they use the
right commands, and every one keeps the safety rules (nothing is placed, no PIN)."""
from __future__ import annotations

import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1] / "skills"
SKILLS = sorted(p.parent for p in ROOT.glob("*/SKILL.md"))


def _read(name):
    return (ROOT / name / "SKILL.md").read_text()


def _frontmatter(text):
    m = re.match(r"^---\n(.*?)\n---\n", text, re.S)
    assert m, "missing frontmatter"
    return dict(line.split(": ", 1) for line in m.group(1).splitlines() if ": " in line)


def test_the_expected_skills_exist():
    assert {p.name for p in SKILLS} >= {"akd-portfolio", "akd-sell-ticket", "akd-buy-ticket"}


@pytest.mark.parametrize("skill", SKILLS, ids=lambda p: p.name)
def test_every_skill_is_well_formed_and_safe(skill):
    text = (skill / "SKILL.md").read_text()
    fm = _frontmatter(text)
    assert fm["name"] == skill.name
    assert 40 <= len(fm["description"]) <= 1024
    assert "--side" not in text                                  # the old flag form is gone
    assert "Nothing was placed" in text or "Nothing is placed" in text
    assert "PIN" in text                                         # the no-PIN rule is spelled out


def test_the_sell_skill_uses_ticket_sell_and_held():
    text = _read("akd-sell-ticket")
    assert "ticket sell" in text and "--held" in text and "ticket buy" not in text and "--free-cash" not in text
    assert "akd-portfolio/run.sh" in text


def test_the_buy_skill_uses_ticket_buy_and_free_cash():
    text = _read("akd-buy-ticket")
    assert "ticket buy" in text and "--free-cash" in text and "ticket sell" not in text and "--held" not in text
    assert "akd-portfolio/run.sh" in text


@pytest.mark.parametrize("name", ["akd-sell-ticket", "akd-buy-ticket"])
def test_ticket_skills_never_log_in_silently_and_never_place_anything(name):
    text = _read(name)
    assert "never log in" in text.lower() or "ask before" in text.lower()   # no silent re-fetch
    assert "limit" in text.lower()                                           # limit orders only
    assert "place this yourself" in text.lower() or "you place" in text.lower()
