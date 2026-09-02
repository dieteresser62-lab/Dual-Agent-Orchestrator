from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

import pytest
from zoneinfo import ZoneInfo

from agent_adapters import (
    AgentOutputError,
    AgentPermissionError,
    NativeClaudeReviewAdapter,
)
from agent_config import AgentSettings
from agent_runtime import (
    AgentInvocationError,
    AgentProcessError,
    QuotaReachedError,
    classify_agent_failure,
    parse_quota_reset,
    wait_until_quota_resume,
)
from workflow_state import AgentFailureKind


RECEIVED = datetime(2026, 8, 12, 10, 0, tzinfo=timezone.utc)


def _claude_adapter() -> NativeClaudeReviewAdapter:
    return NativeClaudeReviewAdapter(
        AgentSettings("claude", "claude", "sonnet", 1800, "high")
    )


@pytest.mark.parametrize("role", ["codex", "claude"])
def test_absolute_offset_reset_is_normalized_per_role(role: str) -> None:
    parsed = parse_quota_reset(
        role,
        "usage cap; resets at 2026-08-12T14:30:00+02:00",
        received_at=RECEIVED,
    )

    assert parsed is not None
    assert parsed.reset_at_utc == datetime(2026, 8, 12, 12, 30, tzinfo=timezone.utc)
    assert parsed.parse_path == f"{role}:text:absolute"
    assert parsed.source_timezone == "UTC+02:00"


@pytest.mark.parametrize(
    ("text", "seconds"),
    (("try again in 90 seconds", 90), ("rate limit resets in 3 minutes", 180)),
)
def test_relative_reset_uses_fixed_received_timestamp(text: str, seconds: int) -> None:
    parsed = parse_quota_reset("claude", text, received_at=RECEIVED)

    assert parsed is not None
    assert parsed.reset_at_utc == RECEIVED + timedelta(seconds=seconds)
    assert parsed.parse_path == "claude:text:relative"


def test_claude_local_clock_reset_with_iana_timezone_is_automatic_evidence() -> None:
    received = datetime(2026, 8, 17, 16, 47, 33, tzinfo=timezone.utc)

    parsed = parse_quota_reset(
        "claude",
        "You've hit your session limit · resets 8:10pm (Europe/Berlin)",
        received_at=received,
    )

    assert parsed is not None
    assert parsed.reset_at_utc == datetime(
        2026, 8, 17, 18, 10, tzinfo=timezone.utc
    )
    assert parsed.parse_path == "claude:text:local-clock"
    assert parsed.source_timezone == "Europe/Berlin"


def test_claude_session_limit_process_failure_is_quota_with_automatic_reset() -> None:
    received = datetime(2026, 8, 20, 16, 34, 33, tzinfo=timezone.utc)
    failure = classify_agent_failure(
        "claude",
        AgentProcessError(
            "You've hit your session limit · resets 8:40pm (Europe/Berlin)",
            exit_code=1,
            provider_data={"type": "result", "subtype": "success"},
        ),
        invocation_id="inv-claude-session-limit",
        received_at=received,
    )

    assert isinstance(failure, QuotaReachedError)
    assert failure.process_exit_code == 1
    assert failure.quota_reset is not None
    assert failure.quota_reset.reset_at_utc == datetime(
        2026, 8, 20, 18, 40, tzinfo=timezone.utc
    )
    assert failure.quota_reset.parse_path == "claude:text:local-clock"
    assert failure.quota_reset.source_timezone == "Europe/Berlin"


def test_claude_session_limit_error_envelope_is_quota_with_automatic_reset() -> None:
    received = datetime(2026, 8, 20, 17, 25, 13, tzinfo=timezone.utc)
    envelope = {
        "type": "result",
        "subtype": "success",
        "is_error": True,
        "result": "You've hit your session limit · resets 8:40pm (Europe/Berlin)",
    }
    adapter = _claude_adapter()

    with pytest.raises(AgentOutputError) as captured:
        adapter.extract_output(json.dumps(envelope), "", {})
    # run_agent attaches the real Claude process exit code before classification.
    captured.value.exit_code = 1

    failure = classify_agent_failure(
        "claude",
        captured.value,
        invocation_id="inv-claude-session-envelope",
        received_at=received,
    )

    assert isinstance(failure, QuotaReachedError)
    assert failure.process_exit_code == 1
    assert failure.quota_reset is not None
    assert failure.quota_reset.reset_at_utc == datetime(
        2026, 8, 20, 18, 40, tzinfo=timezone.utc
    )
    assert failure.quota_reset.parse_path == "claude:text:local-clock"
    assert failure.quota_reset.source_timezone == "Europe/Berlin"


def test_quota_classification_preserves_distinct_technical_text() -> None:
    received = datetime(2026, 8, 20, 16, 34, 33, tzinfo=timezone.utc)
    technical_text = (
        "You've hit your session limit · resets 8:40pm (Europe/Berlin)"
    )

    failure = classify_agent_failure(
        "claude",
        AgentOutputError(
            "review process failed",
            provider_text="bounded provider summary",
            technical_text=technical_text,
            exit_code=1,
        ),
        invocation_id="inv-claude-distinct-technical-text",
        received_at=received,
    )

    assert isinstance(failure, QuotaReachedError)
    assert failure.provider_text == "bounded provider summary"
    assert failure.technical_text == technical_text
    assert failure.process_exit_code == 1


def test_claude_session_limit_review_prose_is_not_a_technical_quota() -> None:
    failure = classify_agent_failure(
        "claude",
        AgentOutputError(
            "claude returned invalid JSON",
            provider_text="A session limit is a future operational risk",
            technical_text="invalid JSON response envelope",
        ),
        invocation_id="inv-claude-review-prose",
        received_at=RECEIVED,
    )

    assert not isinstance(failure, QuotaReachedError)
    assert failure.kind is AgentFailureKind.OUTPUT


def test_local_clock_reset_rolls_forward_to_next_day() -> None:
    received = datetime(2026, 8, 17, 19, 0, tzinfo=timezone.utc)

    parsed = parse_quota_reset(
        "claude",
        "session limit; resets 8:10pm (Europe/Berlin)",
        received_at=received,
    )

    assert parsed is not None
    assert parsed.reset_at_utc == datetime(
        2026, 8, 18, 18, 10, tzinfo=timezone.utc
    )


def test_codex_dated_local_reset_uses_host_timezone() -> None:
    received = datetime(2026, 8, 19, 16, 2, 7, tzinfo=timezone.utc)

    parsed = parse_quota_reset(
        "codex",
        "You've hit your usage limit. Visit the usage page or try again at "
        "Aug 20th, 2026 5:36 AM.",
        received_at=received,
        local_timezone=ZoneInfo("Europe/Berlin"),
    )

    assert parsed is not None
    assert parsed.reset_at_utc == datetime(
        2026, 8, 20, 3, 36, tzinfo=timezone.utc
    )
    assert parsed.parse_path == "codex:text:dated-local"
    assert parsed.source_timezone == "Europe/Berlin"


@pytest.mark.parametrize(
    "text",
    (
        "usage limit; try again at Aug 19th, 2026 5:36 PM",
        "usage limit; try again at Feb 30th, 2026 5:36 AM",
        "usage limit; account period ends Aug 20th, 2026 5:36 AM",
        "usage limit; try again at Aug 20th, 2026 5:36 AM or "
        "Aug 20th, 2026 6:36 AM",
    ),
)
def test_codex_dated_local_reset_rejects_past_invalid_or_ambiguous_text(
    text: str,
) -> None:
    received = datetime(2026, 8, 19, 16, 2, 7, tzinfo=timezone.utc)

    assert (
        parse_quota_reset(
            "codex",
            text,
            received_at=received,
            local_timezone=ZoneInfo("Europe/Berlin"),
        )
        is None
    )


def test_structured_unix_timestamp_is_normalized_to_utc() -> None:
    reset = datetime(2026, 8, 12, 10, 5, tzinfo=timezone.utc)
    parsed = parse_quota_reset(
        "claude",
        "usage cap reached",
        received_at=RECEIVED,
        provider_data={"reset_at": int(reset.timestamp())},
    )

    assert parsed is not None
    assert parsed.reset_at_utc == reset
    assert parsed.parse_path == "claude:structured:reset_at"
    assert parsed.source_timezone == "UTC"


@pytest.mark.parametrize(
    ("adapter", "envelope", "role"),
    (
        (
            _claude_adapter(),
            {
                "is_error": True,
                "subtype": "rate_limit",
                "result": "capacity unavailable",
                "retry_after_seconds": 45,
            },
            "claude",
        ),
    ),
)
def test_adapter_structured_quota_is_classified_without_prose_marker(
    adapter: object, envelope: dict[str, object], role: str
) -> None:
    with pytest.raises(AgentOutputError) as captured:
        adapter.extract_output(json.dumps(envelope), "", {})

    failure = classify_agent_failure(
        role, captured.value, invocation_id="inv-envelope", received_at=RECEIVED
    )

    assert isinstance(failure, QuotaReachedError)
    assert failure.provider_text == "capacity unavailable"
    assert failure.quota_reset is not None
    assert failure.quota_reset.reset_at_utc == RECEIVED + timedelta(seconds=45)


def test_negative_structured_retry_delay_is_not_automatic_evidence() -> None:
    parsed = parse_quota_reset(
        "codex",
        "usage cap reached",
        received_at=RECEIVED,
        provider_data={"retry_after_seconds": -1},
    )

    assert parsed is None


@pytest.mark.parametrize(
    "text",
    (
        "usage cap reached; check your account later",
        "retry in 10 minutes or retry in 20 minutes",
        "resets at 2026-08-12T12:00:00Z or 2026-08-12T13:00:00Z",
    ),
)
def test_unknown_or_ambiguous_reset_fails_safe(text: str) -> None:
    assert parse_quota_reset("codex", text, received_at=RECEIVED) is None


def test_quota_classification_keeps_raw_text_and_invocation_identity() -> None:
    raw = "usage cap reached; try again in 5 minutes"
    failure = classify_agent_failure(
        "claude", RuntimeError(raw), invocation_id="inv-17", received_at=RECEIVED
    )

    assert isinstance(failure, QuotaReachedError)
    assert failure.provider_text == raw
    assert failure.invocation_id == "inv-17"
    assert failure.quota_reset is not None
    assert failure.quota_reset.reset_at_utc == RECEIVED + timedelta(minutes=5)


@pytest.mark.parametrize(
    ("error", "kind"),
    (
        (FileNotFoundError("No such file: claude"), AgentFailureKind.BINARY),
        (TimeoutError("request timeout"), AgentFailureKind.TIMEOUT),
        (AgentPermissionError("permission denied"), AgentFailureKind.PERMISSION),
        (RuntimeError("401 unauthorized"), AgentFailureKind.AUTH),
        (RuntimeError("DNS name resolution failed"), AgentFailureKind.NETWORK),
        (PermissionError("private runtime permission denied"), AgentFailureKind.PERMISSION),
        (RuntimeError("invalid JSON response"), AgentFailureKind.OUTPUT),
        (AgentProcessError("Execution error", exit_code=7), AgentFailureKind.PROCESS),
    ),
)
def test_non_quota_failures_are_distinct(error: Exception, kind: AgentFailureKind) -> None:
    failure = classify_agent_failure(
        "codex", error, invocation_id="inv-kind", received_at=RECEIVED
    )

    assert isinstance(failure, AgentInvocationError)
    assert not isinstance(failure, QuotaReachedError)
    assert failure.kind is kind


def test_wait_uses_bounded_sleeps_and_emits_distinct_phase_events() -> None:
    clock = [RECEIVED]
    sleeps: list[float] = []
    heartbeats: list[str] = []

    def sleep(seconds: float) -> None:
        sleeps.append(seconds)
        clock[0] += timedelta(seconds=seconds)

    wait_until_quota_resume(
        role="claude",
        task_label="task-14",
        work_unit_id=9,
        reset_at_utc=RECEIVED + timedelta(seconds=12),
        resume_at_utc=RECEIVED + timedelta(seconds=12),
        heartbeat_interval_seconds=5,
        now_fn=lambda: clock[0],
        sleep_fn=sleep,
        heartbeat_fn=heartbeats.append,
    )

    assert sleeps == [5.0, 5.0, 2.0]
    assert len(heartbeats) == 5
    assert all("role=claude" in item and "work_unit=9" in item for item in heartbeats)
    assert heartbeats[0].startswith("quota wait entered:")
    assert sum(
        item.startswith("quota wait heartbeat:") for item in heartbeats
    ) == 2
    assert heartbeats[-2].startswith("quota reset reached:")
    assert heartbeats[-1].startswith("quota wait resumed:")
    assert "reset_local=" in heartbeats[0] and "resume_local=" in heartbeats[0]


def test_wait_is_interruptible_without_internal_retry() -> None:
    with pytest.raises(KeyboardInterrupt):
        wait_until_quota_resume(
            role="codex",
            task_label="task-14",
            work_unit_id=2,
            reset_at_utc=RECEIVED + timedelta(minutes=1),
            resume_at_utc=RECEIVED + timedelta(minutes=1),
            heartbeat_interval_seconds=10,
            now_fn=lambda: RECEIVED,
            sleep_fn=lambda _seconds: (_ for _ in ()).throw(KeyboardInterrupt()),
            heartbeat_fn=lambda _message: None,
        )


def test_wait_rejects_resume_before_reset_directly() -> None:
    with pytest.raises(ValueError, match="cannot precede"):
        wait_until_quota_resume(
            role="claude", task_label="task-invalid", work_unit_id=1,
            reset_at_utc=RECEIVED + timedelta(minutes=2),
            resume_at_utc=RECEIVED + timedelta(minutes=1),
            heartbeat_interval_seconds=10,
        )


def test_large_margin_logs_reset_before_single_fake_clock_sleep() -> None:
    clock = [RECEIVED]
    events: list[str] = []
    sleeps: list[float] = []
    resume = RECEIVED + timedelta(days=3)

    def sleep(seconds: float) -> None:
        assert events[-1].startswith("quota reset reached:")
        sleeps.append(seconds)
        clock[0] += timedelta(seconds=seconds)

    wait_until_quota_resume(
        role="claude", task_label="large-margin", work_unit_id=2,
        reset_at_utc=RECEIVED, resume_at_utc=resume,
        heartbeat_interval_seconds=3_600,
        now_fn=lambda: clock[0], sleep_fn=sleep, heartbeat_fn=events.append,
    )

    assert sleeps == [3 * 24 * 60 * 60]
    assert clock[0] == resume
    assert events[-1].startswith("quota wait resumed:")


def test_wait_rejects_naive_injected_clock() -> None:
    with pytest.raises(ValueError, match="timezone-aware"):
        wait_until_quota_resume(
            role="claude",
            task_label="task-14",
            work_unit_id=5,
            reset_at_utc=RECEIVED + timedelta(seconds=1),
            resume_at_utc=RECEIVED + timedelta(seconds=1),
            heartbeat_interval_seconds=3_600,
            now_fn=lambda: datetime(2026, 8, 12, 10, 0),
            sleep_fn=lambda _seconds: None,
            heartbeat_fn=lambda _message: None,
        )
