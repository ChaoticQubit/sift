"""Agno hello-world: framework constructs offline, version pin holds. No API call."""

from __future__ import annotations

from importlib.metadata import version

from sift.agents import hello


def test_agno_version_pin():
    major, minor = (int(x) for x in version("agno").split(".")[:2])
    assert major == 2 and minor >= 7  # >=2.7,<3 (CLAUDE.md rule 3)


def test_hello_agent_constructs(settings):
    agent = hello.build(settings)
    assert agent.name == "sift-hello"
    assert agent.model.id == settings.llm_model
