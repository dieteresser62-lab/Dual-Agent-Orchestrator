from __future__ import annotations

import json
import itertools
import logging
import os
import re
import shutil
import subprocess
import sys
import tomllib
from dataclasses import replace
from pathlib import Path

import pytest
import cli
import orchestrator
from cli import (
    ConfigError,
    build_parser,
    detect_test_command,
    load_repo_config,
    main,
    parse_args,
    run_cli,
)
from agent_runtime import QuotaWaitPolicy, TransientRetryPolicy
from cli import ALLOWED_ENV_NAMES


def _write_config(repo: Path, text: str) -> Path:
    path = repo / "orchestrator.toml"
    path.write_text(text, encoding="utf-8")
    return path


def test_repository_config_loads_complete_provider_input_budget_table() -> None:
    config = load_repo_config(Path(__file__).resolve().parents[1] / "orchestrator.toml")

    rule = config.provider_input_budget.select(
        "claude", "reviewer", "reviewer_final_review"
    )
    assert rule.max_chars == 4_000_000
    assert rule.max_bytes == 16_000_000
    assert config.workflow.max_transport_failures == 3
    assert config.workflow.max_contract_rejections == 3
    assert config.workflow.merge_completed_branch is True


@pytest.mark.parametrize("timeout", (0, 17))
def test_agy_budget_and_timeout_parse_but_candidate_cannot_start(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch, timeout: int) -> None:
    operations = (
        ("codex", "implementer", "implementer_plan"),  # allowlist:provider -- profile configuration: complete budget table
        ("codex", "implementer", "implementer_plan_revision"),  # allowlist:provider -- profile configuration: complete budget table
        ("codex", "implementer", "implementer_implementation"),  # allowlist:provider -- profile configuration: complete budget table
        ("codex", "implementer", "implementer_correction"),  # allowlist:provider -- profile configuration: complete budget table
        ("antigravity", "reviewer", "reviewer_plan_review"),
        ("antigravity", "reviewer", "reviewer_slice_review"),
        ("antigravity", "reviewer", "reviewer_final_review"),
    )
    budget = "".join(
        f'[[provider_input_budget]]\nprovider = "{provider}"\nrole = "{role}"\n'
        f'operation = "{operation}"\nmax_chars = 4000000\nmax_bytes = 16000000\n'
        for provider, role, operation in operations
    )
    config_path = _write_config(
        tmp_path,
        '[roles]\nreviewer = "agy"\nfinal_reviewer = "agy"\n'
        '[agent_profiles.agy]\nprovider = "antigravity"\nmodel = "gemini-3.1-pro-high"\n'
        f'effort = "high"\ntimeout_seconds = {timeout}\n' + budget,
    )
    config = load_repo_config(config_path)
    assert config.agent_profiles["agy"].timeout_seconds == (timeout or None)
    assert config.provider_input_budget.select(
        "antigravity", "reviewer", "reviewer_final_review"
    ).max_bytes == 16_000_000
    selected = parse_args([], cwd=tmp_path, environ={})
    assert selected.slot_settings["reviewer"].name == "antigravity"
    assert selected.slot_settings["final_reviewer"].name == "antigravity"
    assert selected.slot_settings["reviewer"].timeout_seconds == (timeout or None)

    from role_certification import load_role_certifications
    root = Path(__file__).resolve().parents[1]
    candidate_root = tmp_path / "candidate_registry"
    for relative in (
        "schemas/role-provider-certifications-v1.json",
        "schemas/native-provider-schema-capabilities-v2.json",
        "docs/evidence/role-certification-v1.json",
        "docs/evidence/role-certification-reviewer-restricted-v1.json",
        "docs/evidence/role-certification-candidates-v1.json",
        "docs/evidence/codex/reviewer-candidate-v1.json",  # allowlist:provider -- certification data: reviewer candidate proof
        "docs/evidence/antigravity/capability-v1.json",
        "docs/evidence/antigravity/canary-v1.json",
    ) + tuple(row[key]["path"] for row in json.loads((root / "schemas/role-provider-certifications-v1.json").read_text())["certifications"]
              for key in ("evidence", "canary_evidence") if key in row):
        target = candidate_root / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(root / relative, target)
    table_path = candidate_root / "schemas/role-provider-certifications-v1.json"
    document = json.loads(table_path.read_text())
    for row in document["certifications"]:
        if row["provider"] == "antigravity":
            row["status"] = "candidate"
    table_path.write_text(json.dumps(document))
    monkeypatch.setattr(cli, "load_role_certifications", lambda: load_role_certifications(root=candidate_root))
    with pytest.raises(ConfigError, match="not-certified"):
        parse_args([], cwd=tmp_path, environ={})


def test_shipped_roles_and_profiles_resolve_with_final_inheritance(tmp_path: Path) -> None:
    args = parse_args([], cwd=tmp_path, environ={})
    assert {slot: setting.name for slot, setting in args.slot_settings.items()} == {
        "implementer": "codex", "reviewer": "claude", "final_reviewer": "claude",
    }
    assert args.slot_settings["final_reviewer"] == args.slot_settings["reviewer"]


@pytest.mark.parametrize("timeout", (0, 17))
def test_experimental_agy_requires_explicit_slot_selection_and_accepts_timeout(
        tmp_path: Path, timeout: int) -> None:
    shipped = (Path(__file__).resolve().parents[1] / "orchestrator.toml").read_text()
    changed = shipped.replace('reviewer = "review"', 'reviewer = "experimental_antigravity"', 1)
    for operation in ("reviewer_plan_review", "reviewer_slice_review"):
        changed = changed.replace(
            f'provider = "claude"\nrole = "reviewer"\noperation = "{operation}"',  # allowlist:provider -- profile configuration: complete budget table
            f'provider = "antigravity"\nrole = "reviewer"\noperation = "{operation}"')
    changed += ('\n[agent_profiles.experimental_antigravity]\nprovider = "antigravity"\n'
                'model = "gemini-4-pro"\neffort = "high"\n'
                f'timeout_seconds = {timeout}\n')
    _write_config(tmp_path, changed)
    selected = parse_args([], cwd=tmp_path, environ={})
    assert selected.slot_settings["reviewer"].name == "antigravity"
    assert selected.slot_settings["reviewer"].model == "gemini-4-pro"
    assert selected.slot_settings["reviewer"].timeout_seconds == (timeout or None)
    assert selected.slot_settings["implementer"].name == "codex"  # allowlist:provider -- profile configuration: fixed implementer
    assert selected.slot_settings["final_reviewer"].name == "claude"  # allowlist:provider -- profile configuration: default final reviewer


@pytest.mark.parametrize("provider,baseline,older,newer,major", [
    ("antigravity", "1.2.12", "1.2.11", "1.2.13", "2.0.0"),
    ("claude", "2.1.283 (Claude Code)", "2.1.282 (Claude Code)",  # allowlist:provider -- transport: CLI version evidence
     "2.1.284 (Claude Code)", "3.0.0 (Claude Code)"),  # allowlist:provider -- transport: CLI version evidence
    ("codex", "codex-cli 0.156.1", "codex-cli 0.156.0",  # allowlist:provider -- transport: CLI version evidence
     "codex-cli 0.156.2", "codex-cli 1.0.0"),  # allowlist:provider -- transport: CLI version evidence
])
def test_fake_cli_version_policy_accepts_new_majors_and_rejects_older(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch, provider: str,
        baseline: str, older: str, newer: str, major: str) -> None:
    import agent_runtime
    from agent_adapters import CapabilitySpec
    binary = tmp_path / provider
    binary.write_bytes(b"fake executable")
    binary.chmod(0o755)
    monkeypatch.setenv("PATH", str(tmp_path))
    version = baseline

    def fake_run(argv, timeout=20):
        return (0, version + "\n", "") if argv[-1] == "--version" else (0, "--required-flag\n", "")

    monkeypatch.setattr(agent_runtime, "run_local_command", fake_run)

    class FakeAdapter:
        name = provider
        cli_binary = str(binary)
        model = "test"
        effort = "high"
        timeout = 10
        reviewer = provider != "codex"  # allowlist:provider -- transport: fake role binding
        required_hosts = ()
        capability = CapabilitySpec(("--version",), ("--help",),
                                    (rf"^{re.escape(baseline)}$",), ("--required-flag",))
        capability_verified = False

        @staticmethod
        def validate_process_output(stderr):
            assert stderr == ""

    for candidate in (baseline, newer, major):
        version = candidate
        adapter = FakeAdapter()
        agent_runtime.verify_agent_capabilities(adapter)
        assert adapter.capability_verified
    version = older
    with pytest.raises(agent_runtime.AgentCompatibilityError, match="Unsupported"):
        agent_runtime.verify_agent_capabilities(FakeAdapter())


def test_toml_final_profile_and_usd_budget_reach_adapters(tmp_path: Path) -> None:
    shipped = (Path(__file__).resolve().parents[1] / "orchestrator.toml").read_text(encoding="utf-8")
    _write_config(tmp_path, shipped.replace('final_reviewer = "review"', 'final_reviewer = "final"') + """
[agent_profiles.final]
provider = "claude"
model = "sonnet"
effort = "xhigh"
timeout_seconds = 0
[agent_profiles.final.provider_options.claude]
max_budget_usd = 10.0
""")
    args = parse_args([], cwd=tmp_path, environ={})
    assert len(args.repo_config.provider_input_budget.rules) == 7
    final = args.slot_settings["final_reviewer"]
    assert (final.model, final.effort, final.timeout_seconds, final.max_budget_usd) == ("sonnet", "xhigh", None, 10.0)
    from agent_adapters import build_agent_registry
    adapters = build_agent_registry(args.slot_settings)
    assert adapters["final_reviewer"].max_budget_usd == 10.0
    assert adapters["reviewer"].max_budget_usd is None
    assert adapters["final_reviewer"] is not adapters["reviewer"]
    assert (adapters["final_reviewer"].model, adapters["final_reviewer"].effort,
            adapters["final_reviewer"].timeout) == ("sonnet", "xhigh", None)


@pytest.mark.parametrize("providers", itertools.product(("codex", "claude"), repeat=3))
def test_all_provider_topologies_parse_and_qualified_separated_ones_start(tmp_path: Path, providers: tuple[str, str, str]) -> None:
    def profile(name: str, provider: str) -> str:
        model = "sol" if provider == "codex" else "opus"
        return f'[agent_profiles.{name}]\nprovider = "{provider}"\nmodel = "{model}"\neffort = "high"\n'
    config = _write_config(tmp_path, '[roles]\nimplementer = "impl"\nreviewer = "rev"\nfinal_reviewer = "fin"\n' + ''.join(profile(name, provider) for name, provider in zip(("impl", "rev", "fin"), providers)))
    assert set(load_repo_config(config).agent_profiles) >= {"impl", "rev", "fin"}
    if providers[0] != providers[1] and providers[1] == providers[2]:
        assert parse_args([], cwd=tmp_path, environ={}).slot_settings["final_reviewer"].name == providers[2]
    else:
        with pytest.raises(ConfigError, match="slot=.*provider="):
            parse_args([], cwd=tmp_path, environ={})


@pytest.mark.parametrize("fragment,diagnostic", (
    ('[roles]\nreviewer = "missing"\n', "missing agent profile"),
    ('[agent_profiles.unused]\nprovider = "claude"\nmodel = "opus"\neffort = "extreme"\n', "effort"),
    ('[agent_profiles.unused]\nprovider = "claude"\nmodel = "opus"\neffort = "high"\nunknown = 1\n', "unknown"),
    ('[agent_profiles.review]\ntimeout_seconds = -1\n', "timeout_seconds"),
    ('[agent_profiles.review.provider_options.claude]\nmax_budget_usd = inf\n', "positive number"),
    ('[agent_profiles.review.provider_options.claude]\nmax_budget_usd = -1.0\n', "positive number"),
    ('[agent_profiles.implementation.provider_options.claude]\nmax_budget_usd = 1.0\n', "require provider claude"),
))
def test_invalid_or_unused_profiles_fail_syntactically(tmp_path: Path, fragment: str, diagnostic: str) -> None:
    with pytest.raises(ConfigError, match=diagnostic):
        load_repo_config(_write_config(tmp_path, fragment))


def test_profile_precedence_and_unknown_environment_are_strict(tmp_path: Path) -> None:
    _write_config(tmp_path, '[agent_profiles.review]\nmodel = "sonnet"\neffort = "low"\ntimeout_seconds = 17\n')
    args = parse_args(["--reviewer-model", "opus"], cwd=tmp_path, environ={"RUN_TASK_REVIEWER_MODEL": "fable", "RUN_TASK_REVIEWER_EFFORT": ""})
    assert (args.slot_settings["reviewer"].model, args.slot_settings["reviewer"].effort, args.slot_settings["reviewer"].timeout_seconds) == ("opus", "low", 17)
    assert args.slot_settings["final_reviewer"] == args.slot_settings["reviewer"]
    with pytest.raises(ConfigError, match="RUN_TASK_UNKNOWN"):
        parse_args([], cwd=tmp_path, environ={"RUN_TASK_UNKNOWN": "1"})
    with pytest.raises(ConfigError, match="RUN_TASK_CLAUDE_MAX_BUDGET_USD"):
        parse_args([], cwd=tmp_path, environ={"RUN_TASK_CLAUDE_MAX_BUDGET_USD": "1"})
    with pytest.raises(SystemExit):
        parse_args(["--claude-max-budget-usd", "1"], cwd=tmp_path, environ={})  # allowlist:provider -- profile configuration: retired flag rejection


def test_run_task_environment_mentions_are_allowlisted() -> None:
    root = Path(__file__).resolve().parents[1]
    text = "\n".join(path.read_text(encoding="utf-8") for path in [*sorted((root / "src").glob("*.py")), root / "README.md", root / "Quickstart.md"])
    mentioned = set(re.findall(r"RUN_TASK_[A-Z][A-Z0-9_]*", text))
    assert mentioned <= ALLOWED_ENV_NAMES


def test_merge_completion_setting_defaults_true_and_accepts_false(tmp_path: Path) -> None:
    assert load_repo_config(tmp_path / "missing.toml").workflow.merge_completed_branch is True
    path = _write_config(tmp_path, "[workflow]\nmerge_completed_branch = false\n")
    assert load_repo_config(path).workflow.merge_completed_branch is False


@pytest.mark.parametrize("pattern", (
    "", "/{run_id}", "../{run_id}", "x//{run_id}",
    "x/./{run_id}", "x/../{run_id}", "{run_id}.txt", "{foo}/{run_id}",
    "{run_id}\\other", "{run_id}/", "{run_id}/a b",
))
def test_archive_pattern_rejects_unsafe_config(tmp_path: Path, pattern: str) -> None:
    path = _write_config(tmp_path, f"[workflow]\narchive_run_directory = {json.dumps(pattern)}\n")
    with pytest.raises(ConfigError, match="workflow.archive_run_directory"):
        load_repo_config(path)


def test_archive_pattern_defaults_and_accepts_documented_tokens(tmp_path: Path) -> None:
    assert load_repo_config(tmp_path / "missing.toml").workflow.archive_run_directory == "{run_id}"
    path = _write_config(tmp_path, '[workflow]\narchive_run_directory = "{year}-feature/{run_id}"\n')
    assert load_repo_config(path).workflow.archive_run_directory == "{year}-feature/{run_id}"


def test_repository_base_branch_is_optional_and_strict(tmp_path: Path) -> None:
    assert load_repo_config(tmp_path / "missing.toml").repository.base_branch is None
    path = _write_config(tmp_path, '[repository]\nbase_branch = "trunk"\n')
    assert load_repo_config(path).repository.base_branch == "trunk"

    for text, message in (
        ('[repository]\nbranch = "main"\n', "Unknown key"),
        ('[repository]\nbase_branch = ""\n', "repository.base_branch"),
        ('[repository]\nbase_branch = "-trunk"\n', "plain branch name"),
        ('[repository]\nbase_branch = "my trunk"\n', "plain branch name"),
    ):
        _write_config(tmp_path, text)
        with pytest.raises(ConfigError, match=message):
            load_repo_config(path)


def test_provider_input_budget_config_is_closed_and_complete(tmp_path: Path) -> None:
    path = _write_config(
        tmp_path,
        """
[[provider_input_budget]]
provider = "codex"
role = "implementer"
operation = "implementer_implementation"
max_chars = 10
max_bytes = 20
unexpected = true
""",
    )
    with pytest.raises(ConfigError, match="Unknown key"):
        load_repo_config(path)

    path.write_text(
        """
[[provider_input_budget]]
provider = "codex"
role = "implementer"
operation = "implementer_implementation"
max_chars = 10
max_bytes = 20
""",
        encoding="utf-8",
    )
    with pytest.raises(ConfigError, match="must be complete"):
        load_repo_config(path)

    path.write_text(path.read_text(encoding="utf-8") * 2, encoding="utf-8")
    with pytest.raises(ConfigError, match="duplicate"):
        load_repo_config(path)


@pytest.fixture(autouse=True)
def _isolate_process_environment(monkeypatch) -> None:
    for name in (
        "RUN_TASK_CONFIG",
        "RUN_TASK_SKIP_GIT_CHECK",
        "RUN_TASK_TEST_CMD",
        "RUN_TASK_WATCH_STREAM_CHANNELS",
        "RUN_TASK_IMPLEMENTER_BINARY",
        "RUN_TASK_IMPLEMENTER_MODEL",
        "RUN_TASK_IMPLEMENTER_TIMEOUT",
        "RUN_TASK_IMPLEMENTER_EFFORT",
        "RUN_TASK_REVIEWER_BINARY",
        "RUN_TASK_REVIEWER_MODEL",
        "RUN_TASK_REVIEWER_TIMEOUT",
        "RUN_TASK_REVIEWER_EFFORT",
        "RUN_TASK_CLAUDE_MAX_BUDGET_USD",
        "RUN_TASK_QUOTA_AUTO_RESUME",
        "RUN_TASK_QUOTA_SAFETY_MARGIN",
        "RUN_TASK_QUOTA_MAX_WAIT",
        "RUN_TASK_QUOTA_MAX_AUTO_RESUMES",
        "RUN_TASK_QUOTA_HEARTBEAT_INTERVAL",
        "RUN_TASK_TRANSIENT_RETRY_AUTO",
        "RUN_TASK_TRANSIENT_RETRY_INITIAL_DELAY",
        "RUN_TASK_TRANSIENT_RETRY_MAX_DELAY",
        "RUN_TASK_TRANSIENT_RETRY_MAX_AUTO_RESUMES",
    ):
        monkeypatch.delenv(name, raising=False)


def test_task_file_accepts_positional_compatibility_path(tmp_path: Path) -> None:
    args = parse_args(["work.md"], cwd=tmp_path, environ={})

    assert args.task_file == "work.md"


def test_default_agents_file_resolves_from_repository_working_directory(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (tmp_path / "AGENTS.md").write_text("repository contract\n", encoding="utf-8")
    args = parse_args([], cwd=tmp_path, environ={})
    observed: list[str] = []
    monkeypatch.chdir(tmp_path)

    result = run_cli(
        args,
        run_pipeline_fn=lambda _task, runtime_args: observed.append(runtime_args.agents_file) or 0,
        watch_inbox_fn=lambda **_kwargs: 0,
        find_task_file_fn=lambda _path: tmp_path / "task.md",
    )

    assert result == 0
    assert observed == [str((tmp_path / "AGENTS.md").resolve())]


def test_missing_default_agents_file_warns_and_continues(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    args = parse_args([], cwd=tmp_path, environ={})
    called = False

    def pipeline(_task: Path, _args: object) -> int:
        nonlocal called
        called = True
        return 0

    monkeypatch.chdir(tmp_path)
    with caplog.at_level(logging.WARNING):
        result = run_cli(
            args,
            run_pipeline_fn=pipeline,
            watch_inbox_fn=lambda **_kwargs: 0,
            find_task_file_fn=lambda _path: tmp_path / "task.md",
        )

    assert result == 0
    assert called is True
    assert "Default repository agents file is missing" in caplog.text


def test_explicit_missing_agents_file_fails_closed_before_pipeline(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    missing = tmp_path / "missing-agents.md"
    args = parse_args(["--agents-file", str(missing)], cwd=tmp_path, environ={})
    called = False

    def pipeline(_task: Path, _args: object) -> int:
        nonlocal called
        called = True
        return 0

    monkeypatch.chdir(tmp_path)
    with caplog.at_level(logging.ERROR):
        result = run_cli(
            args,
            run_pipeline_fn=pipeline,
            watch_inbox_fn=lambda **_kwargs: 0,
            find_task_file_fn=lambda _path: tmp_path / "task.md",
        )

    assert result == 1
    assert called is False
    assert f"Explicit --agents-file does not exist: {missing}" in caplog.text


@pytest.mark.parametrize(
    ("argv", "resume_explicit", "task_explicit"),
    (
        (("--resume", "--task-file", "work.md"), True, True),
        (("--resume", "work.md"), True, False),
        (("--no-resume", "--task-file", "work.md"), False, True),
        (("--task-file", "work.md"), False, True),
        ((), False, False),
        (("--watch",), False, False),
    ),
)
def test_queue_finalization_origin_flags_are_only_explicit_cli_options(
    tmp_path: Path,
    argv: tuple[str, ...],
    resume_explicit: bool,
    task_explicit: bool,
) -> None:
    args = parse_args(list(argv), cwd=tmp_path, environ={})

    assert args.resume_explicit is resume_explicit
    assert args.task_file_explicit is task_explicit


def test_validation_retries_require_explicit_cli_flags(tmp_path: Path) -> None:
    default = parse_args([], cwd=tmp_path, environ={})
    requested = parse_args(
        ["--retry-incomplete-validation", "--retry-failed-validation"],
        cwd=tmp_path,
        environ={},
    )

    assert default.retry_incomplete_validation is False
    assert default.retry_failed_validation is False
    assert requested.retry_incomplete_validation is True
    assert requested.retry_failed_validation is True


@pytest.mark.parametrize(
    "flag",
    (
        "--native-claude-reviews",
        "--no-native-claude-reviews",
        "--native-codex-results",
        "--no-native-codex-results",
    ),
)
def test_native_transport_flags_are_retired(tmp_path: Path, flag: str) -> None:
    with pytest.raises(SystemExit) as caught:
        parse_args([flag], cwd=tmp_path, environ={})

    assert caught.value.code == 2


def test_quota_wait_policy_defaults_and_explicit_disable(tmp_path: Path) -> None:
    default = parse_args([], cwd=tmp_path, environ={})
    disabled = parse_args(
        [
            "--no-quota-auto-resume",
            "--quota-safety-margin",
            "15",
            "--quota-max-wait",
            "120",
            "--quota-max-auto-resumes",
            "0",
            "--quota-heartbeat-interval",
            "9",
        ],
        cwd=tmp_path,
        environ={},
    )

    assert default.quota_wait_policy == QuotaWaitPolicy()
    assert default.quota_wait_policy.maximum_wait_seconds == 604_800
    assert default.quota_wait_policy.heartbeat_interval_seconds == 3_600
    assert disabled.quota_wait_policy == QuotaWaitPolicy(
        automatic=False,
        safety_margin_seconds=15,
        maximum_wait_seconds=120,
        maximum_auto_resumes=0,
        heartbeat_interval_seconds=9,
    )


def test_quota_wait_policy_uses_cli_then_environment_then_defaults(tmp_path: Path) -> None:
    environment = {
        "RUN_TASK_QUOTA_AUTO_RESUME": "0",
        "RUN_TASK_QUOTA_SAFETY_MARGIN": "12",
        "RUN_TASK_QUOTA_MAX_WAIT": "900",
        "RUN_TASK_QUOTA_MAX_AUTO_RESUMES": "2",
        "RUN_TASK_QUOTA_HEARTBEAT_INTERVAL": "7",
    }
    from_environment = parse_args([], cwd=tmp_path, environ=environment)
    overridden = parse_args(
        ["--quota-auto-resume", "--quota-max-wait", "60"],
        cwd=tmp_path,
        environ=environment,
    )

    assert from_environment.quota_wait_policy == QuotaWaitPolicy(
        automatic=False,
        safety_margin_seconds=12,
        maximum_wait_seconds=900,
        maximum_auto_resumes=2,
        heartbeat_interval_seconds=7,
    )
    assert overridden.quota_wait_policy.automatic is True
    assert overridden.quota_wait_policy.maximum_wait_seconds == 60
    assert overridden.quota_wait_policy.safety_margin_seconds == 12


def test_transient_retry_policy_defaults_and_overrides(tmp_path: Path) -> None:
    default = parse_args([], cwd=tmp_path, environ={})
    environment = {
        "RUN_TASK_TRANSIENT_RETRY_AUTO": "0",
        "RUN_TASK_TRANSIENT_RETRY_INITIAL_DELAY": "7",
        "RUN_TASK_TRANSIENT_RETRY_MAX_DELAY": "40",
        "RUN_TASK_TRANSIENT_RETRY_MAX_AUTO_RESUMES": "3",
    }
    configured = parse_args(
        ["--transient-retry-auto", "--transient-retry-max-delay", "20"],
        cwd=tmp_path,
        environ=environment,
    )

    assert default.transient_retry_policy == TransientRetryPolicy()
    assert configured.transient_retry_policy == TransientRetryPolicy(
        automatic=True,
        initial_delay_seconds=7,
        maximum_delay_seconds=20,
        maximum_auto_resumes=3,
    )


@pytest.mark.parametrize(
    "args",
    (
        ["--quota-safety-margin", "-1"],
        ["--quota-max-wait", "0"],
        ["--quota-max-auto-resumes", "-1"],
        ["--quota-heartbeat-interval", "0"],
    ),
)
def test_invalid_quota_wait_policy_is_rejected(
    tmp_path: Path, args: list[str], capsys
) -> None:
    with pytest.raises(SystemExit):
        parse_args(args, cwd=tmp_path, environ={})

    assert "quota" in capsys.readouterr().err.lower()


def test_task_file_rejects_positional_and_explicit_paths(tmp_path: Path, capsys) -> None:
    with pytest.raises(SystemExit):
        parse_args(["first.md", "--task-file", "second.md"], cwd=tmp_path, environ={})

    assert "either positionally or with --task-file" in capsys.readouterr().err


def test_test_command_precedence_cli_environment_repo_and_detection(tmp_path: Path) -> None:
    _write_config(
        tmp_path,
        '[validation]\ndefault_command = ["from-repo"]\n',
    )

    cli = parse_args(
        ["--test-command", "from-cli"],
        cwd=tmp_path,
        environ={"RUN_TASK_TEST_CMD": "from-env"},
    )
    environment = parse_args([], cwd=tmp_path, environ={"RUN_TASK_TEST_CMD": "from-env"})
    repository = parse_args([], cwd=tmp_path, environ={})
    (tmp_path / "orchestrator.toml").unlink()
    (tmp_path / "pyproject.toml").write_text(
        '[tool.pytest.ini_options]\ntestpaths = ["tests"]\n', encoding="utf-8"
    )
    detected = parse_args([], cwd=tmp_path, environ={})

    assert cli.test_command == "from-cli"
    assert environment.test_command == "from-env"
    assert repository.test_command == "from-repo"
    assert detected.test_command == "python3 -m pytest tests/ -v"


def test_config_path_precedence_cli_over_environment(tmp_path: Path) -> None:
    cli_config = tmp_path / "cli.toml"
    env_config = tmp_path / "env.toml"
    cli_config.write_text('[validation]\ndefault_command = ["from-cli-config"]\n', encoding="utf-8")
    env_config.write_text('[validation]\ndefault_command = ["from-env-config"]\n', encoding="utf-8")

    args = parse_args(
        ["--config", str(cli_config)],
        cwd=tmp_path,
        environ={"RUN_TASK_CONFIG": str(env_config)},
    )

    assert args.config_file == cli_config.resolve()
    assert args.test_command == "from-cli-config"


def test_explicit_missing_config_is_rejected(tmp_path: Path) -> None:
    with pytest.raises(ConfigError, match="Explicit configuration file does not exist"):
        parse_args(["--config", "missing.toml"], cwd=tmp_path, environ={})


@pytest.mark.parametrize("arguments", [[], ["--config", ""]])
def test_empty_config_value_falls_through_to_optional_default(
    arguments: list[str], tmp_path: Path
) -> None:
    args = parse_args(arguments, cwd=tmp_path, environ={"RUN_TASK_CONFIG": ""})

    assert args.config_file is None


def test_empty_cli_config_value_falls_through_to_environment(tmp_path: Path) -> None:
    env_config = tmp_path / "environment.toml"
    env_config.write_text('[validation]\ndefault_command = ["environment"]\n', encoding="utf-8")

    args = parse_args(
        ["--config", ""],
        cwd=tmp_path,
        environ={"RUN_TASK_CONFIG": str(env_config)},
    )

    assert args.config_file == env_config.resolve()
    assert args.test_command == "environment"


def test_every_public_parser_action_has_help_text() -> None:
    parser = build_parser()

    for action in parser._actions:
        if action.dest in {"help", "auto"}:
            continue
        assert action.help not in {None, ""}, action.dest
    formatted = parser.format_help()
    assert "--dry-run" in formatted
    assert "Simulate agent responses" in formatted


def test_agent_setting_precedence_cli_over_environment_and_defaults(tmp_path: Path) -> None:
    _write_config(tmp_path, "[agent_profiles.review.provider_options.claude]\nmax_budget_usd = 2.5\n")
    args = parse_args(
        [
            "--reviewer-binary",
            "/cli/claude",
            "--reviewer-model",
            "opus",
            "--reviewer-timeout",
            "321",
            "--reviewer-effort",
            "high",
        ],
        cwd=tmp_path,
        environ={
            "RUN_TASK_REVIEWER_BINARY": "/env/claude",
            "RUN_TASK_REVIEWER_MODEL": "sonnet",
            "RUN_TASK_REVIEWER_TIMEOUT": "999",
            "RUN_TASK_REVIEWER_EFFORT": "low",
            "RUN_TASK_IMPLEMENTER_MODEL": "luna",
            "RUN_TASK_IMPLEMENTER_EFFORT": "high",
        },
    )

    claude = args.slot_settings["reviewer"]
    assert claude.binary == "/cli/claude"
    assert claude.model == "opus"
    assert claude.timeout_seconds == 321
    assert claude.effort == "high"
    assert claude.max_budget_usd == 2.5
    assert set(args.slot_settings) == {"implementer", "reviewer", "final_reviewer"}
    assert args.slot_settings["implementer"].model == "luna"
    assert args.slot_settings["implementer"].effort == "high"


def test_quota_conscious_reviewer_defaults_are_explicit(tmp_path: Path) -> None:
    args = parse_args([], cwd=tmp_path, environ={})

    assert args.slot_settings["reviewer"].model == "opus"
    assert args.slot_settings["reviewer"].effort == "high"
    assert args.slot_settings["reviewer"].timeout_seconds is None
    assert args.slot_settings["implementer"].timeout_seconds is None
    assert args.slot_settings["reviewer"].max_budget_usd is None
    assert args.slot_settings["implementer"].model == "sol"
    assert args.slot_settings["implementer"].effort == "high"


def test_provider_timeout_can_be_explicitly_disabled(tmp_path: Path) -> None:
    settings = parse_args(
        ["--implementer-timeout", "0", "--reviewer-timeout", "47"],
        cwd=tmp_path,
        environ={"RUN_TASK_IMPLEMENTER_TIMEOUT": "17", "RUN_TASK_REVIEWER_TIMEOUT": "0"},
    ).slot_settings
    assert settings["implementer"].timeout_seconds is None
    assert settings["reviewer"].timeout_seconds == 47
    assert parse_args(
        [], cwd=tmp_path, environ={"RUN_TASK_REVIEWER_TIMEOUT": "0"}
    ).slot_settings["reviewer"].timeout_seconds is None


def test_models_are_limited_to_the_selectable_families(tmp_path: Path) -> None:
    for argv, codex, claude in (
        (["--implementer-model", "sol"], "sol", "opus"),
        (["--implementer-model", "Terra"], "terra", "opus"),
        (["--implementer-model", "gpt-6-luna", "--reviewer-model", "SONNET"], "gpt-6-luna", "sonnet"),
        (["--implementer-model", "astra", "--reviewer-model", "fable"], "astra", "fable"),
    ):
        settings = parse_args(argv, cwd=tmp_path, environ={}).slot_settings
        assert (settings["implementer"].model, settings["reviewer"].model) == (codex, claude)

    for argv, message in (
        (["--implementer-model", "invalid model!"], "model must be a family"),
        (["--reviewer-model", "haiku"], "claude model must be one of opus"),
    ):
        with pytest.raises(ConfigError, match=message):
            parse_args(argv, cwd=tmp_path, environ={})


def test_effort_is_freely_selectable_within_the_known_levels(tmp_path: Path) -> None:
    settings = parse_args(
        ["--implementer-effort", "xhigh", "--reviewer-effort", "max"], cwd=tmp_path, environ={}
    ).slot_settings
    assert (settings["implementer"].effort, settings["reviewer"].effort) == ("xhigh", "max")


def test_manual_slice_gate_cli_overrides_repository_default(tmp_path: Path) -> None:
    _write_config(tmp_path, "[workflow]\nmanual_slice_gate = true\n")

    configured = parse_args([], cwd=tmp_path, environ={})
    overridden = parse_args(["--no-manual-slice-gate"], cwd=tmp_path, environ={})

    assert configured.manual_slice_gate is True
    assert overridden.manual_slice_gate is False


def test_workflow_gates_default_to_automatic_and_allow_explicit_overrides(
    tmp_path: Path,
) -> None:
    default = parse_args([], cwd=tmp_path, environ={})
    overridden = parse_args(
        [
            "--plan-gate",
            "--test-change-gate",
            "--plan-only",
            "--work-plan",
            "docs/internal/plan.md",
            "--target-branch",
            "feature/plan",
        ],
        cwd=tmp_path,
        environ={},
    )

    assert default.plan_gate is False
    assert default.test_change_gate is False
    assert default.plan_only is None
    assert overridden.plan_gate is True
    assert overridden.test_change_gate is True
    assert overridden.plan_only is True
    assert overridden.work_plan == "docs/internal/plan.md"
    assert overridden.target_branch == "feature/plan"


def _assert_scope_extension_default_is_automatic(repo: Path) -> None:
    _write_config(repo, "[workflow]\n")
    assert load_repo_config(repo / "orchestrator.toml").workflow.scope_extension_gate is False
    assert parse_args([], cwd=repo, environ={}).repo_config.workflow.scope_extension_gate is False


def test_scope_extension_gate_default_and_configured_value(tmp_path: Path) -> None:
    _assert_scope_extension_default_is_automatic(tmp_path)
    _write_config(tmp_path, "[workflow]\nscope_extension_gate = true\n")
    assert parse_args([], cwd=tmp_path, environ={}).repo_config.workflow.scope_extension_gate is True


def test_scope_extension_default_proof_kills_true_mutation(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    original = cli._load_workflow

    def default_true(data: object) -> cli.WorkflowConfig:
        configured = original(data)
        if isinstance(data, dict) and "scope_extension_gate" not in data:
            return replace(configured, scope_extension_gate=True)
        return configured

    monkeypatch.setattr(cli, "_load_workflow", default_true)
    with pytest.raises(AssertionError):
        _assert_scope_extension_default_is_automatic(tmp_path)


@pytest.mark.parametrize(
    "arguments",
    [
        ["--approve-gate", "--gate-rationale", "ok"],
        ["--resume", "--approve-gate"],
        ["--resume", "--gate-rationale", "ok"],
    ],
)
def test_gate_cli_decision_requires_explicit_resume_and_rationale(
    arguments: list[str], tmp_path: Path
) -> None:
    with pytest.raises(SystemExit):
        parse_args(arguments, cwd=tmp_path, environ={})


def test_gate_cli_records_explicit_approval_intent(tmp_path: Path) -> None:
    args = parse_args(
        [
            "--resume",
            "--approve-gate",
            "--gate-rationale",
            "reviewed exact persisted evidence",
        ],
        cwd=tmp_path,
        environ={},
    )

    assert args.gate_decision is True
    assert args.gate_rationale == "reviewed exact persisted evidence"


@pytest.mark.parametrize("arguments", (
    ["--acknowledge-post-merge", "a" * 40, "--post-merge-rationale", "reviewed"],
    ["--resume", "--acknowledge-post-merge", "a" * 40,
     "--post-merge-rationale", "reviewed"],
    ["--resume", "--task-file", "task.md", "--acknowledge-post-merge", "a" * 40],
    ["--resume", "--task-file", "task.md", "--post-merge-rationale", "reviewed"],
))
def test_post_merge_acknowledgment_requires_task_resume_commit_and_reason(
    arguments: list[str], tmp_path: Path,
) -> None:
    with pytest.raises(SystemExit):
        parse_args(arguments, cwd=tmp_path, environ={})


def test_post_merge_acknowledgment_parses_exact_commit(tmp_path: Path) -> None:
    args = parse_args([
        "--resume", "--task-file", "task.md", "--acknowledge-post-merge", "a" * 40,
        "--post-merge-rationale", "reviewed unknown outcome",
    ], cwd=tmp_path, environ={})
    assert args.acknowledge_post_merge == "a" * 40
    assert args.post_merge_rationale == "reviewed unknown outcome"


@pytest.mark.parametrize(
    ("environment", "message"),
    [
        ({"RUN_TASK_REVIEWER_TIMEOUT": "-1"}, "reviewer timeout"),
        ({"RUN_TASK_REVIEWER_EFFORT": "extreme"}, "reviewer effort"),
        ({"RUN_TASK_CLAUDE_MAX_BUDGET_USD": "free"}, "Unknown RUN_TASK"),
    ],
)
def test_invalid_agent_environment_is_a_configuration_error(
    environment: dict[str, str], message: str, tmp_path: Path
) -> None:
    with pytest.raises(ConfigError, match=message):
        parse_args([], cwd=tmp_path, environ=environment)


@pytest.mark.parametrize(
    ("source_name", "content", "expected"),
    [
        ("pyproject.toml", "[tool.pytest.ini_options]\n", "python3 -m pytest tests/ -v"),
        ("package.json", '{"scripts": {"test": "vitest"}}', "npm test"),
        ("Makefile", "  test: lint\n\tpytest\n", "make test"),
    ],
)
def test_detect_test_command(source_name: str, content: str, expected: str, tmp_path: Path) -> None:
    (tmp_path / source_name).write_text(content, encoding="utf-8")

    assert detect_test_command(tmp_path) == expected


def test_detect_test_command_returns_empty_without_supported_layout(tmp_path: Path) -> None:
    assert detect_test_command(tmp_path) == ""


def test_explicit_empty_test_commands_are_not_auto_detected(tmp_path: Path) -> None:
    (tmp_path / "package.json").write_text(
        '{"scripts": {"test": "vitest"}}', encoding="utf-8"
    )

    cli = parse_args(["--test-command", ""], cwd=tmp_path, environ={})
    environment = parse_args([], cwd=tmp_path, environ={"RUN_TASK_TEST_CMD": ""})
    _write_config(tmp_path, '[validation]\nrequired_artifacts = ["dist/**"]\n')
    repository = parse_args([], cwd=tmp_path, environ={})

    assert cli.test_command == ""
    assert environment.test_command == ""
    assert repository.test_command == ""


@pytest.mark.parametrize(
    ("declaration", "argv_key"),
    (
        ('default_shell_command = "cd app && flutter test"', "default_command"),
        ('product_shell_command = "cd app && flutter test"', "product_command"),
        (
            '[[validation.rules]]\npatterns = ["app/**"]\nshell_command = "cd app && flutter test"',
            "command",
        ),
    ),
)
@pytest.mark.parametrize("dry_run", (False, True))
def test_shell_validation_keys_fail_before_workflow_in_start_and_dry_run(
    declaration: str, argv_key: str, dry_run: bool, tmp_path: Path, monkeypatch, capsys
) -> None:
    _write_config(tmp_path, f"[validation]\n{declaration}\n")
    monkeypatch.chdir(tmp_path)
    calls: list[str] = []

    def workflow(*_args, **_kwargs):  # noqa: ANN002, ANN003
        calls.append("workflow")
        return 0

    result = main(
        ["--dry-run"] if dry_run else [],
        run_pipeline_fn=workflow,
        watch_inbox_fn=workflow,
        find_task_file_fn=lambda _path: tmp_path / "task.md",
    )

    assert result == 1
    assert calls == []
    assert (
        f'{argv_key} = ["sh", "-c", "cd app && flutter test"]'
        in capsys.readouterr().err
    )
    assert not (tmp_path / ".orchestrator").exists()


@pytest.mark.parametrize("operator", ("&&", "|", ";"))
@pytest.mark.parametrize("source", ("cli", "environment"))
@pytest.mark.parametrize("dry_run", (False, True))
def test_text_shell_operators_fail_before_workflow(
    operator: str, source: str, dry_run: bool, tmp_path: Path, monkeypatch, capsys
) -> None:
    monkeypatch.chdir(tmp_path)
    command = f"echo first {operator} echo second"
    args = (["--dry-run"] if dry_run else []) + (
        ["--test-command", command] if source == "cli" else []
    )
    if source == "environment":
        monkeypatch.setenv("RUN_TASK_TEST_CMD", command)
    calls: list[str] = []

    def workflow(*_args, **_kwargs):  # noqa: ANN002, ANN003
        calls.append("workflow")
        return 0

    result = main(
        args,
        run_pipeline_fn=workflow,
        watch_inbox_fn=workflow,
        find_task_file_fn=lambda _path: tmp_path / "task.md",
    )

    assert result == 1
    assert calls == []
    error = capsys.readouterr().err
    assert ("--test-command" if source == "cli" else "RUN_TASK_TEST_CMD") in error
    assert 'default_command = ["sh", "-c", "cd app && flutter test"]' in error


def test_simple_text_command_still_produces_argv(tmp_path: Path) -> None:
    args = parse_args(
        ["--test-command", 'python3 -c \'print("ok")\''],
        cwd=tmp_path,
        environ={},
    )
    assert args.test_command == 'python3 -c \'print("ok")\''


def test_detected_shell_syntax_is_rejected_before_workflow(
    tmp_path: Path, monkeypatch, capsys
) -> None:
    monkeypatch.setattr(cli, "detect_test_command", lambda _root: "cd app && flutter test")
    monkeypatch.chdir(tmp_path)
    calls: list[str] = []

    def workflow(*_args, **_kwargs):  # noqa: ANN002, ANN003
        calls.append("workflow")
        return 0

    result = main(
        [],
        run_pipeline_fn=workflow,
        watch_inbox_fn=workflow,
        find_task_file_fn=lambda _path: tmp_path / "task.md",
    )
    assert result == 1
    assert calls == []
    assert "automatic detection: text validation commands" in capsys.readouterr().err


def test_load_repo_config_accepts_portable_policy_schema(tmp_path: Path) -> None:
    path = _write_config(
        tmp_path,
        """
[paths]
productive = ["src/**/*.py"]
tests = ["tests/**"]
documentation = ["docs/**"]
generated = ["build/**"]

[[stop_rules]]
id = "DOMAIN-001"
description = "Stop on invariant changes."

[validation]
default_command = ["pytest"]
required_artifacts = ["dist/**/*.css"]
product_command = ["python3", "tests/product_smoke.py"]
product_timeout_seconds = 45

[[validation.rules]]
patterns = ["engine/**"]
command = ["npm", "run", "build:engine"]

[workflow]
manual_slice_gate = true
plan_gate = false
test_change_gate = true
scope_extension_gate = true
max_rounds_per_loop = 5
max_acceptance_reviews = 7
max_transport_failures = 4
max_contract_rejections = 8
""".strip(),
    )

    config = load_repo_config(path)

    assert config.source == path.resolve()
    assert config.paths.productive == ("src/**/*.py",)
    assert config.stop_rules[0].id == "DOMAIN-001"
    assert config.validation.default_command is not None
    assert config.validation.default_command.argv == ("pytest",)
    assert config.validation.required_artifacts == ("dist/**/*.css",)
    assert config.validation.product_command is not None
    assert config.validation.product_command.argv == (
        "python3",
        "tests/product_smoke.py",
    )
    assert config.validation.product_command.timeout_seconds == 45
    assert config.validation.rules[0].patterns == ("engine/**",)
    assert config.validation.rules[0].command.argv == (
        "npm",
        "run",
        "build:engine",
    )
    assert config.workflow.manual_slice_gate is True
    assert config.workflow.plan_gate is False
    assert config.workflow.test_change_gate is True
    assert config.workflow.scope_extension_gate is True
    assert config.workflow.max_rounds_per_loop == 5
    assert config.workflow.max_acceptance_reviews == 7
    assert config.workflow.max_transport_failures == 4
    assert config.workflow.max_contract_rejections == 8


@pytest.mark.parametrize(
    ("content", "message"),
    [
        ("unknown = true\n", "Unknown key(s) in root: unknown"),
        ("[workflow]\nmanual_slice_gate = \"yes\"\n", "must be a boolean"),
        ("[workflow]\nplan_gate = \"yes\"\n", "must be a boolean"),
        ("[workflow]\ntest_change_gate = \"yes\"\n", "must be a boolean"),
        ("[workflow]\nscope_extension_gate = \"yes\"\n", "must be a boolean"),
        ("[workflow]\nmerge_completed_branch = \"yes\"\n", "must be a boolean"),
        ("[workflow]\nmax_rounds_per_loop = 0\n", "must be a positive integer"),
        ("[workflow]\nmax_acceptance_reviews = 0\n", "must be a positive integer"),
        ("[workflow]\nmax_transport_failures = 0\n", "must be a positive integer"),
        ("[workflow]\nmax_contract_rejections = 0\n", "must be a positive integer"),
        ("[paths]\nproductive = [\"../outside/**\"]\n", "must not escape"),
        ("[paths]\nproductive = [\"C:/outside/**\"]\n", "must be relative"),
        ("[paths]\nproductive = [\"src\\\\**\"]\n", "platform-neutral separator"),
        ("[paths]\nproductive = []\n", "must contain at least one path pattern"),
        (
            "[paths]\nproductive = [\"src/**\", \"src/**\"]\n",
            "Invalid [paths] configuration: productive path patterns must be unique",
        ),
        (
            "[[stop_rules]]\nid = \"S-001\"\ndescription = \"one\"\n"
            "[[stop_rules]]\nid = \"S-001\"\ndescription = \"two\"\n",
            "Duplicate stop rule id: S-001",
        ),
        (
            '[validation]\ndefault_command = "pytest"\n',
            "validation default.default_command must be a non-empty string array",
        ),
        (
            '[validation]\ndefault_command = ["pytest"]\n'
            'default_shell_command = "pytest"\n',
            "default_shell_command is unsupported in structured-v2",
        ),
    ],
)
def test_load_repo_config_rejects_invalid_configuration(
    content: str, message: str, tmp_path: Path
) -> None:
    path = _write_config(tmp_path, content)

    with pytest.raises(ConfigError, match=re.escape(message)):
        load_repo_config(path)


def test_main_reports_config_error_before_calling_workflow(
    tmp_path: Path, monkeypatch, capsys
) -> None:
    _write_config(tmp_path, "unknown = true\n")
    called = False

    def fail_if_called(*_args, **_kwargs):  # noqa: ANN002, ANN003
        nonlocal called
        called = True
        return 0

    monkeypatch.chdir(tmp_path)
    result = main(
        [],
        run_pipeline_fn=fail_if_called,
        watch_inbox_fn=fail_if_called,
        find_task_file_fn=lambda _path: tmp_path / "task.md",
    )

    assert result == 1
    assert called is False
    assert "Configuration error:" in capsys.readouterr().err
    assert not (tmp_path / ".orchestrator").exists()


def test_main_reports_keyboard_interrupt_without_traceback(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.chdir(tmp_path)

    def interrupt(*_args: object, **_kwargs: object) -> int:
        raise KeyboardInterrupt

    result = main(
        [], run_pipeline_fn=interrupt, watch_inbox_fn=interrupt,
        find_task_file_fn=lambda _path: tmp_path / "task.md",
    )
    assert result == 130
    stderr = capsys.readouterr().err
    assert "interrupted; resume with --resume" in stderr
    assert "Traceback" not in stderr


def test_auto_resume_detects_unfinished_and_frozen_state(tmp_path: Path) -> None:
    state_dir = tmp_path / ".orchestrator"
    state_dir.mkdir()
    (state_dir / "state.json").write_text(
        json.dumps({"phase": "phase2", "phase2": {"status": "frozen"}}),
        encoding="utf-8",
    )

    args = parse_args([], cwd=tmp_path, environ={})

    assert args.resume is True
    assert args.auto_resume is True
    assert args.state_was_frozen is True


def test_completed_state_starts_new_run_with_notice(
    tmp_path: Path, caplog
) -> None:
    state_dir = tmp_path / ".orchestrator"
    state_dir.mkdir()
    (state_dir / "state.json").write_text(
        json.dumps({"phase": "done"}), encoding="utf-8"
    )

    args = parse_args([], cwd=tmp_path, environ={})

    assert args.resume is False
    assert args.force_overwrite_state is True
    assert args.completed_state_replaced is True

    with caplog.at_level(logging.INFO):
        result = run_cli(
            args,
            run_pipeline_fn=lambda *_args, **_kwargs: 0,
            watch_inbox_fn=lambda **_kwargs: 0,
            find_task_file_fn=lambda _path: tmp_path / "task.md",
        )

    assert result == 0
    assert "Existing state is completed (phase=done); starting a new run." in caplog.text


def test_watch_defaults_do_not_auto_resume_and_skip_git_check(tmp_path: Path) -> None:
    state_dir = tmp_path / ".orchestrator"
    state_dir.mkdir()
    (state_dir / "state.json").write_text(
        json.dumps({"phase": "phase1"}), encoding="utf-8"
    )

    args = parse_args(["--watch"], cwd=tmp_path, environ={})

    assert args.resume is False
    assert args.auto_resume is False
    assert args.skip_git_check is True
    assert args.plan_gate is False
    assert args.plan_gate_source == "repository-default"
    assert args.test_change_gate is False


def test_watch_plan_gate_can_be_enabled_explicitly(tmp_path: Path) -> None:
    cli_enabled = parse_args(["--watch", "--plan-gate"], cwd=tmp_path, environ={})
    _write_config(tmp_path, "[workflow]\nplan_gate = true\n")
    config_enabled = parse_args(["--watch"], cwd=tmp_path, environ={})

    assert cli_enabled.plan_gate is True
    assert cli_enabled.plan_gate_source == "cli"
    assert config_enabled.plan_gate is True
    assert config_enabled.plan_gate_source == "repository-default"


def test_cli_overrides_watch_environment_defaults(tmp_path: Path) -> None:
    args = parse_args(
        ["--watch", "--no-skip-git-check", "--agent-live-stream-channels", "stderr"],
        cwd=tmp_path,
        environ={
            "RUN_TASK_SKIP_GIT_CHECK": "1",
            "RUN_TASK_WATCH_STREAM_CHANNELS": "both",
        },
    )

    assert args.skip_git_check is False
    assert args.agent_live_stream_channels == "stderr"


def test_invalid_environment_boolean_is_a_configuration_error(tmp_path: Path) -> None:
    with pytest.raises(ConfigError, match="RUN_TASK_SKIP_GIT_CHECK"):
        parse_args([], cwd=tmp_path, environ={"RUN_TASK_SKIP_GIT_CHECK": "sometimes"})


def test_watch_default_logs_git_check_and_stream_configuration(
    tmp_path: Path, caplog
) -> None:
    args = parse_args(["--watch"], cwd=tmp_path, environ={})

    with caplog.at_level(logging.INFO):
        result = run_cli(
            args,
            run_pipeline_fn=lambda *_args, **_kwargs: 0,
            watch_inbox_fn=lambda **_kwargs: 0,
            find_task_file_fn=lambda _path: tmp_path / "task.md",
        )

    assert result == 0
    assert "live stream channels = stdout" in caplog.text
    assert "enabling --skip-git-check by default" in caplog.text
    assert "RUN_TASK_SKIP_GIT_CHECK" in caplog.text
    assert "disabling --plan-gate by default" not in caplog.text


def test_invalid_stream_environment_warning_uses_configured_log_format(tmp_path: Path) -> None:
    repo_root = Path(__file__).resolve().parents[1]
    (tmp_path / "task.md").write_text("# task\n", encoding="utf-8")
    env = os.environ.copy()
    env["RUN_TASK_WATCH_STREAM_CHANNELS"] = "invalid"

    result = subprocess.run(
        [
            sys.executable,
            str(repo_root / "src" / "cli.py"),
            "--dry-run",
            "--skip-git-check",
            "--test-command",
            "",
            "--no-agent-live-stream",
            "--agent-output",
            "none",
        ],
        cwd=tmp_path,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0
    assert "[WARNING] Invalid RUN_TASK_WATCH_STREAM_CHANNELS='invalid'" in result.stderr
    assert re.search(
        r"^\[\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}\] \[WARNING\] ",
        result.stderr,
        re.MULTILINE,
    )


def test_cli_and_runtime_defaults_cannot_drift() -> None:
    assert cli.DEFAULT_TASK_FILE == orchestrator.DEFAULT_TASK_FILE
    assert cli.DEFAULT_AGENTS_FILE == orchestrator.DEFAULT_AGENTS_FILE
    assert "--development-mode" not in build_parser().format_help()
    assert "--from-phase" not in build_parser().format_help()


def test_declared_python_floor_matches_standard_library_toml_requirement() -> None:
    repo_root = Path(__file__).resolve().parents[1]
    with (repo_root / "pyproject.toml").open("rb") as handle:
        project = tomllib.load(handle)["project"]

    assert project["requires-python"] == ">=3.11"
    assert project["dependencies"] == []


def test_readme_cli_defaults_match_resolved_parser_contract() -> None:
    repo_root = Path(__file__).resolve().parents[1]
    readme = (repo_root / "README.md").read_text(encoding="utf-8")

    assert "| `--resume` / `--no-resume` | automatisch |" in readme
    assert "| `--agent-output <none\\|summary\\|full>` | `none` |" in readme
    assert "| `--agent-live-stream` / `--no-agent-live-stream` | an |" in readme
    assert "| `--skip-git-check` / `--no-skip-git-check` | aus; im Watch-Modus an |" in readme
    assert "| Reviewer (Claude) | `--reviewer-binary`, `--reviewer-model`" in readme
    assert "`claude`, `opus`, ohne \u005aeitlimit, `high`" in readme
    assert "nicht Modell und Effort" in readme
    assert "`codex debug models`" in readme
    assert build_parser().get_default("agent_output") == "none"
    assert build_parser().get_default("agent_live_stream") is True
