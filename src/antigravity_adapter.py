"""Measured AGY print transport composed with the native reviewer role binding.

The isolation owner supplies the workspace. This module never creates a HOME,
settings, agent definition, or repository snapshot on the provider's behalf.
"""

from __future__ import annotations

import json
import math
import re
from dataclasses import dataclass
from pathlib import Path

from agent_adapters import (
    AgentOutputError, CapabilitySpec, _BaseAdapter,
)
from agent_config import AgentSettings
from agent_roles import AgentRoleName
from native_provider_schema import (
    NativeProviderSchemaError, assert_provider_capabilities,
    exact_cli_version_pattern, normalize_transport_profile,
)
from native_review_contract import NativeReviewContractError, canonical_native_review_json
from native_review_request import NativeReviewRequestBundle
from provider_input_budget import PreparedProviderInput, ProviderInputComponent
from reviewer_input import REVIEW_PACKET_CHUNK_CHARS, ReviewerInputError, build_reviewer_input
from role_binding import RoleBinding, binding_for_role
from workflow_state import AgentFailureKind


STDERR_CLASSIFIER_VERSION = "agy-stderr-v1"
_TIMEOUT = re.compile(r"^\[agy\] print timeout after [0-9]+[smh](?:[0-9]+[smh])* with turn in progress; returning partial output$", re.I)
_SOFT_DENIAL = re.compile(r'^jetski: no output produced — a tool required the "read_file" permission that headless mode cannot prompt for, so it was auto-denied\..*$', re.I | re.S)
_AUTH = re.compile(r"(?:authentication required|not authenticated|login required)", re.I)
_QUOTA = re.compile(r"(?:quota exceeded|rate limit exceeded|resource exhausted)(?: for this account)?", re.I)
_NETWORK = re.compile(r"(?:network error|connection refused|connection reset|dns lookup failed|name resolution failed)", re.I)
_SCHEMA = re.compile(r"(?:invalid json schema|schema validation failed|invalid schema)", re.I)
_MODEL = re.compile(r"(?:unknown model|model not found|invalid model)", re.I)
_PERMISSION = re.compile(r"(?:permission denied|auto-denied|permission request rejected)", re.I)


def _reject_duplicate(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate JSON key")
        result[key] = value
    return result


def _reject_constant(value: str) -> object:
    raise ValueError(f"non-finite JSON constant {value}")


def _finite_float(value: str) -> float:
    parsed = float(value)
    if not math.isfinite(parsed):
        raise ValueError("non-finite JSON number")
    return parsed


def strict_json_object(text: str) -> dict[str, object]:
    try:
        value = json.loads(text, object_pairs_hook=_reject_duplicate, parse_constant=_reject_constant, parse_float=_finite_float)
    except (ValueError, TypeError) as exc:
        raise AgentOutputError("antigravity stdout is not one strict JSON object", kind_hint=AgentFailureKind.OUTPUT) from exc
    if not isinstance(value, dict):
        raise AgentOutputError("antigravity stdout must be an object", kind_hint=AgentFailureKind.OUTPUT)
    return value


def _same_json_type(left: object, right: object) -> bool:
    if type(left) is not type(right):
        return False
    if isinstance(left, dict):
        return left.keys() == right.keys() and all(_same_json_type(left[key], right[key]) for key in left)
    if isinstance(left, list):
        return len(left) == len(right) and all(_same_json_type(a, b) for a, b in zip(left, right, strict=True))
    return left == right


def classify_agy_stderr(stderr: str) -> tuple[AgentFailureKind | None, str]:
    """Classify only versioned diagnostics; never return untrusted stderr text."""
    if not stderr.strip():
        return None, "none"
    if _TIMEOUT.fullmatch(stderr.strip()):
        return AgentFailureKind.TIMEOUT, "print-timeout"
    if _SOFT_DENIAL.fullmatch(stderr.strip()):
        return AgentFailureKind.PERMISSION, "soft-read-denial"
    for pattern, kind, code in (
        (_AUTH, AgentFailureKind.AUTH, "authentication"),
        (_QUOTA, AgentFailureKind.QUOTA, "quota"),
        (_NETWORK, AgentFailureKind.NETWORK, "network"),
        (_SCHEMA, AgentFailureKind.OUTPUT, "schema"),
        (_MODEL, AgentFailureKind.PROCESS, "model"),
        (_PERMISSION, AgentFailureKind.PERMISSION, "permission"),
    ):
        if pattern.fullmatch(stderr.strip()):
            return kind, code
    if stderr.lstrip().startswith("AGY_ERROR:"):
        return AgentFailureKind.PROCESS, "agy-error"
    return AgentFailureKind.PROCESS, "unknown-stderr"


@dataclass(frozen=True)
class AntigravityWorkspace:
    """Prepared, isolated paths owned and sealed by the Slice-4 boundary."""

    container: Path
    repo: Path
    input_dir: Path
    home: Path
    agent_file: Path
    settings_file: Path
    log_file: Path

    def __post_init__(self) -> None:
        for path in (self.container, self.repo, self.input_dir, self.home):
            if not path.is_dir() or path.is_symlink():
                raise ValueError("antigravity workspace has an invalid directory")
        if self.input_dir != self.container / "input" or self.repo != self.container / "repo":
            raise ValueError("antigravity repo and input must be directly inside the container")
        for path in (self.agent_file, self.settings_file):
            if not path.is_file() or path.is_symlink():
                raise ValueError("antigravity workspace lacks a regular agent or settings file")
        if self.log_file.is_symlink() or not self.log_file.parent.is_dir():
            raise ValueError("antigravity log path is invalid")


class AntigravityTransport:
    """Provider-only transport facts; no role policy or contract is embedded here."""

    @staticmethod
    def command(settings: AgentSettings, workspace: AntigravityWorkspace, directive: str, schema: Path) -> list[str]:
        return [
            settings.binary, "-p", directive, "--output-format", "json",
            "--json-schema", str(schema), "--print-timeout",
            f"{settings.timeout_seconds or 0}s", "--disable-slash-commands",
            "--sandbox", "--model", settings.model, "--effort", settings.effort,
            "--log-file", str(workspace.log_file), "--agent", "dao-reviewer",
        ]

    @staticmethod
    def envelope(stdout: str, stderr: str, exit_code: int, writer_json: str) -> tuple[dict[str, object], dict[str, int]]:
        stderr_kind, stderr_code = classify_agy_stderr(stderr)
        if stderr_kind is not None:
            raise AgentOutputError(
                f"antigravity {stderr_code} ({STDERR_CLASSIFIER_VERSION})",
                exit_code=exit_code, kind_hint=stderr_kind,
            )
        if exit_code != 0 and not stdout.strip():
            raise AgentOutputError(
                f"antigravity {AntigravityTransport.exit_diagnostic(exit_code)} ({STDERR_CLASSIFIER_VERSION})",
                exit_code=exit_code, kind_hint=AgentFailureKind.PROCESS,
            )
        envelope = strict_json_object(stdout)
        usage = envelope.get("usage")
        clean_usage = {
            key: value for key, value in usage.items()
            if key in {"input_tokens", "output_tokens", "thinking_tokens", "cache_read_tokens", "total_tokens"}
            and type(value) is int and value >= 0
        } if isinstance(usage, dict) else {}

        def fail(code: str, kind: AgentFailureKind) -> None:
            raise AgentOutputError(
                f"antigravity {code} ({STDERR_CLASSIFIER_VERSION})",
                technical_text=f"antigravity {code} ({STDERR_CLASSIFIER_VERSION})",
                exit_code=exit_code, kind_hint=kind,
            )

        if exit_code != 0:
            fail(AntigravityTransport.exit_diagnostic(exit_code), AgentFailureKind.PROCESS)
        if envelope.get("status") != "SUCCESS":
            fail("unsuccessful-status", AgentFailureKind.OUTPUT)
        if envelope.get("error") not in (None, ""):
            fail("envelope-error", AgentFailureKind.OUTPUT)
        if envelope.get("denied_actions") not in (None, []):
            fail("denied-actions", AgentFailureKind.PERMISSION)
        if not isinstance(envelope.get("structured_output"), dict):
            fail("missing-structured-output", AgentFailureKind.OUTPUT)
        writer = strict_json_object(writer_json)
        if not _same_json_type(envelope.get("json_schema"), writer):
            fail("schema-echo-mismatch", AgentFailureKind.OUTPUT)
        return envelope, clean_usage

    @staticmethod
    def exit_diagnostic(exit_code: int) -> str:
        return {1: "configuration-exit", 3: "model-api-exit"}.get(exit_code, "nonzero-exit")


class NativeAntigravityReviewAdapter(_BaseAdapter):
    reviewer = True
    required_hosts: tuple[str, ...] = ()
    inherit_process_environment = False
    environment_passthrough = ("WSL_INTEROP", "WSL_DISTRO_NAME", "WSLENV")
    stdin_closed_when_unused = True
    suppress_live_stream = True
    requires_attempt_ledger = True
    sanitize_reviewer_environment = False
    set_pwd = False
    egress_disclosure = (
        "Google", ("repository snapshot", "native review request", "evidence",
                   "writer schema", "reviewer policy")
    )
    capability = CapabilitySpec(
        version_args=("--version",), help_args=("--help",),
        supported_version_patterns=(exact_cli_version_pattern("antigravity"),),
        required_help_flags=(
            "-p", "--output-format", "--json-schema", "--print-timeout",
            "--disable-slash-commands", "--sandbox", "--model", "--effort",
            "--log-file", "--agent",
        ),
    )

    def __init__(self, settings: AgentSettings, *, role_binding: RoleBinding | None = None) -> None:
        if settings.name != "antigravity" or settings.effort not in {"low", "medium", "high", "max"}:
            raise ValueError("antigravity requires an explicit measured model and effort")
        super().__init__(settings)
        self.role_binding = role_binding or binding_for_role(AgentRoleName.REVIEWER)
        if self.role_binding.role is not AgentRoleName.REVIEWER:
            raise TypeError("antigravity has no implementer binding")
        self.prepared_workspace: AntigravityWorkspace | None = None
        self._writer_json: str | None = None
        self._request_id: str | None = None

    def bind_prepared_workspace(self, workspace: AntigravityWorkspace) -> None:
        if not isinstance(workspace, AntigravityWorkspace):
            raise TypeError("antigravity requires a prepared isolation workspace")
        self.prepared_workspace = workspace

    def prepared_execution_root(self) -> Path:
        if self.prepared_workspace is None:
            raise AgentOutputError("antigravity isolation workspace is not prepared", kind_hint=AgentFailureKind.PERMISSION)
        return self.prepared_workspace.container

    def build_command(self, prompt: str) -> tuple[list[str], bool]:
        raise RuntimeError("antigravity requires a native review bundle")

    def prepare_provider_input(self, prompt: str) -> PreparedProviderInput:
        raise RuntimeError("antigravity requires a native review bundle")

    def prepare_native_provider_input(self, bundle: NativeReviewRequestBundle) -> PreparedProviderInput:
        workspace = self.prepared_workspace
        if workspace is None:
            raise AgentOutputError("antigravity isolation workspace is not prepared", kind_hint=AgentFailureKind.PERMISSION)
        if not isinstance(bundle, NativeReviewRequestBundle) or bundle.capability_profile != "antigravity":
            raise TypeError("antigravity requires a provider-bound native review bundle")
        agent_text = workspace.agent_file.read_text(encoding="utf-8")
        if self.role_binding.policy not in agent_text:
            raise AgentOutputError("antigravity agent lacks the bound reviewer policy", kind_hint=AgentFailureKind.PERMISSION)
        try:
            reviewer_input = build_reviewer_input(bundle, workspace.input_dir, chunk_chars=REVIEW_PACKET_CHUNK_CHARS)
            schema_json = bundle.provider_response_schema_json
            schema_path = workspace.input_dir / "writer-schema.json"
            schema_path.write_bytes(schema_json.encode("utf-8"))
            directive = reviewer_input.directive
            command = AntigravityTransport.command(self.settings, workspace, directive, schema_path)
            profile = normalize_transport_profile("antigravity", command)
            assert_provider_capabilities("antigravity", (), profile=profile)
            transmitted_files = (
                *reviewer_input.request_files, *reviewer_input.evidence_files,
                *reviewer_input.manifest_pages, reviewer_input.manifest_file,
            )
            if len(transmitted_files) != len(reviewer_input.components):
                raise ReviewerInputError("antigravity manifest component count differs")
            components = [
                *(ProviderInputComponent(item.name, path.read_text(encoding="utf-8"))
                  for item, path in zip(reviewer_input.components, transmitted_files, strict=True)),
                ProviderInputComponent("system_policy", agent_text),
                ProviderInputComponent("response_schema", schema_path.read_text(encoding="utf-8")),
                ProviderInputComponent("start_directive", directive),
            ]
            self.env = {
                "HOME": str(workspace.home), "PATH": "/usr/local/bin:/usr/bin:/bin",
                "LANG": "C.UTF-8", "TERM": "dumb", "AGY_CLI_DISABLE_AUTO_UPDATE": "true",
            }
            self._writer_json = schema_json
            self._request_id = bundle.bound_context.request_id
            return PreparedProviderInput(tuple(command), None, tuple(components), allow_duplicate_indexed_content=True)
        except (ReviewerInputError, NativeProviderSchemaError) as exc:
            raise AgentOutputError("antigravity provider input is invalid", kind_hint=AgentFailureKind.OUTPUT) from exc

    def validate_process_output(self, stderr: str) -> None:
        # The versioned classifier considers stderr jointly with the envelope.
        _ = stderr

    def stream_filter(self, channel: str, line: str, state: dict[str, str | bool]) -> bool:
        return False

    def extract_output(self, stdout: str, stderr: str, extra_files: dict[str, str]) -> str:
        if self._writer_json is None or self._request_id is None:
            raise AgentOutputError("antigravity request binding is missing", kind_hint=AgentFailureKind.OUTPUT)
        exit_code = int(extra_files.get("exit_code", "0"))
        if stdout.strip():
            preliminary = (
                strict_json_object(stdout)
                if classify_agy_stderr(stderr)[0] is None else {}
            )
            preliminary_usage = preliminary.get("usage")
            if isinstance(preliminary_usage, dict):
                self.metadata = {"usage": {
                    key: value for key, value in preliminary_usage.items()
                    if key in {"input_tokens", "output_tokens", "thinking_tokens", "cache_read_tokens", "total_tokens"}
                    and type(value) is int and value >= 0
                }}
        envelope, usage = AntigravityTransport.envelope(stdout, stderr, exit_code, self._writer_json)
        self.metadata = {"usage": usage, "num_turns": envelope.get("num_turns")}
        structured = envelope["structured_output"]
        result = structured.get("result") if isinstance(structured, dict) else None
        if not isinstance(structured, dict) or set(structured) != {"result"} or not isinstance(result, dict):
            raise AgentOutputError("antigravity structured output lacks its sole result object", kind_hint=AgentFailureKind.OUTPUT)
        if result.get("request_id") != self._request_id:
            raise AgentOutputError("antigravity response request binding differs", kind_hint=AgentFailureKind.OUTPUT)
        if result.get("schema_version") != self.role_binding.contract:
            raise AgentOutputError("antigravity response role contract differs", kind_hint=AgentFailureKind.OUTPUT)
        try:
            return canonical_native_review_json(result)
        except NativeReviewContractError as exc:
            raise AgentOutputError("antigravity response violates local result schema", kind_hint=AgentFailureKind.OUTPUT) from exc

    def cleanup(self) -> None:
        super().cleanup()
        self._writer_json = None
        self._request_id = None
        self.prepared_workspace = None
