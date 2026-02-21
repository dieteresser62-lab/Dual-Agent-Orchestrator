from __future__ import annotations

from copy import deepcopy

from agent_runtime import QuotaReachedError
from orchestrator import freeze_current_phase


class _DummyContext:
    def __init__(self) -> None:
        self.saved_states: list[dict] = []

    def save_state(self, state: dict) -> None:
        self.saved_states.append(deepcopy(state))


def test_freeze_current_phase_marks_phase1_frozen_and_rewinds_cycle() -> None:
    state = {
        "phase": "phase1",
        "updated_at": "old",
        "phase1": {"status": "running", "cycle": 3, "error": None},
        "phase2": {"status": "pending", "cycle": 0, "error": None},
    }
    ctx = _DummyContext()

    freeze_current_phase(state, QuotaReachedError("claude", "usage cap reached"), ctx)

    assert state["phase"] == "phase1"
    assert state["phase1"]["status"] == "frozen"
    assert state["phase1"]["cycle"] == 2
    assert "quota/rate limit reached" in str(state["phase1"]["error"])
    assert state["updated_at"] != "old"
    assert len(ctx.saved_states) == 1


def test_freeze_current_phase_marks_phase2_frozen_without_negative_cycle() -> None:
    state = {
        "phase": "phase2",
        "updated_at": "old",
        "phase1": {"status": "completed", "cycle": 2, "error": None},
        "phase2": {"status": "running", "cycle": 0, "error": None},
    }
    ctx = _DummyContext()

    freeze_current_phase(state, QuotaReachedError("gemini", "too many requests"), ctx)

    assert state["phase"] == "phase2"
    assert state["phase2"]["status"] == "frozen"
    assert state["phase2"]["cycle"] == 0
    assert "quota/rate limit reached" in str(state["phase2"]["error"])
    assert state["updated_at"] != "old"
    assert len(ctx.saved_states) == 1
