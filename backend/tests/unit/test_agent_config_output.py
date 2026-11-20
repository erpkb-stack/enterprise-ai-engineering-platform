"""Agents saved with a partial config (older sandbox agents) must come back with every field the UI reads."""
from app.api.v1.agents import _full_config


def test_partial_config_is_filled_with_defaults():
    legacy = {"model": "mock-reasoner-1", "tools": ["calculator"], "provider": "mock", "max_steps": 3,
              "policies": {"evaluate_output": False, "require_citations": False}}
    out = _full_config(legacy)
    assert out["retry"]["max_attempts"] == 2 and out["memory"]["window_messages"] == 12 and out["fallbacks"] == []
    assert out["tools"] == ["calculator"] and out["max_steps"] == 3
    assert out["policies"]["require_citations"] is False and out["policies"]["allow_delegation"] is False


def test_invalid_config_keeps_stored_values_and_fills_missing_keys():
    out = _full_config({"max_steps": 10_000, "model": "m"})
    assert out["max_steps"] == 10_000 and out["model"] == "m" and "retry" in out


def test_missing_config_stays_none():
    assert _full_config(None) is None
