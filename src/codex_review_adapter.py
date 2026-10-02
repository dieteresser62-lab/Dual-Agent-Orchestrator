"""Native review transport with an isolated, read-only permission profile."""

from __future__ import annotations

import json
import hashlib
import os
import shutil
import stat
import tempfile
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

from agent_adapters import AgentOutputError, CapabilitySpec, CodexToolActivity, _BaseAdapter  # allowlist:provider -- transport: tool lifetime observer
from provider_metrics import event_usage, stream_model_metrics
from agent_config import AgentSettings
from model_catalog import hardened_reviewer_catalog, reviewer_model_row_sha256, catalog_row_matches
from agent_roles import AgentRoleName
from native_provider_schema import (
    CODEX_REVIEW_DISABLED_FEATURES, NativeProviderSchemaError,  # allowlist:provider -- transport: reviewer CLI binding
    assert_provider_capabilities, codex_review_permission_config,  # allowlist:provider -- transport: reviewer CLI binding
    exact_cli_version_pattern, normalize_transport_profile, validate_codex_review_package_root,  # allowlist:provider -- transport: package boundary
)
from native_review_contract import NativeReviewContractError, canonical_native_review_json, validate_native_review_transport_binding
from native_review_request import NativeReviewRequestBundle
from provider_input_budget import PreparedProviderInput, ProviderInputComponent
from reviewer_input import (
    REVIEW_INPUT_PATH_PLACEHOLDER, REVIEW_PACKET_CHUNK_CHARS, ReviewerInputError,
    build_reviewer_input, measured_reviewer_path_text,
)
from role_binding import RoleBinding, binding_for
from agent_runtime import AgentProcessError, ReviewerWorkspace, create_read_only_reviewer_workspace, is_provider_overload_error

def _tree_fingerprint(root: Path) -> tuple[tuple[object, ...], ...]:
    records = []
    for path in (root, *sorted(root.rglob("*"))):
        metadata = path.lstat()
        mode = metadata.st_mode
        kind = "link" if stat.S_ISLNK(mode) else "dir" if stat.S_ISDIR(mode) else "file" if stat.S_ISREG(mode) else "other"
        digest = hashlib.sha256(path.read_bytes()).hexdigest() if kind == "file" else os.readlink(path) if kind == "link" else ""
        records.append((path.relative_to(root).as_posix(), kind, stat.S_IMODE(mode),
                        metadata.st_dev, metadata.st_ino, metadata.st_nlink, digest))
    return tuple(records)


def _unique_json_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate response key")
        result[key] = value
    return result


def _invalid_json_constant(value: str) -> None:
    raise ValueError(f"nonfinite JSON constant: {value}")


def codex_package_root(entry_path: str) -> Path:  # allowlist:provider -- transport: reviewer CLI binding
    """Derive the one npm package containing the identity-bound CLI entry."""
    try:
        target = Path(entry_path).resolve(strict=True)
    except (OSError, RuntimeError) as exc:
        raise AgentOutputError("Codex reviewer package entry is missing") from exc  # allowlist:provider -- transport: reviewer CLI binding
    matches = [parent for parent in target.parents
               if parent.parts[-3:] == ("node_modules", "@openai", "codex")]  # allowlist:provider -- transport: unique npm package root
    if len(matches) != 1 or target != matches[0] / "bin" / "codex.js":  # allowlist:provider -- transport: bound npm entry
        raise AgentOutputError("Codex reviewer package root is missing or ambiguous")  # allowlist:provider -- transport: reviewer CLI binding
    try:
        validate_codex_review_package_root(matches[0])  # allowlist:provider -- transport: reviewer package boundary
    except NativeProviderSchemaError as exc:
        raise AgentOutputError("Codex reviewer package root is missing or ambiguous", technical_text=str(exc)) from exc  # allowlist:provider -- transport: reviewer package boundary
    return matches[0]


class NativeCodexReviewAdapter(CodexToolActivity, _BaseAdapter):  # allowlist:provider -- transport: reviewer registration
    reviewer = True
    inherit_process_environment = False
    set_pwd = False
    live_stream_profile = "json-events"
    quota_reset_profile = "dated-local"
    required_hosts = ("chatgpt.com", "api.openai.com")
    requires_attempt_ledger = True
    capability = CapabilitySpec(
        version_args=("--version",), help_args=("exec", "--help"),
        supported_version_patterns=(exact_cli_version_pattern("codex"),),  # allowlist:provider -- transport: reviewer CLI binding
        required_help_flags=(
            "--model", "--ephemeral", "--json", "--output-last-message",
            "--output-schema",
        ),
    )

    def __init__(self, settings: AgentSettings, *, role_binding: RoleBinding | None = None) -> None:
        super().__init__(settings)
        self.role_binding = role_binding or binding_for("codex", AgentRoleName.REVIEWER)  # allowlist:provider -- certification data: reviewer rights
        if self.role_binding.role is not AgentRoleName.REVIEWER:
            raise TypeError("Codex reviewer requires reviewer role binding")  # allowlist:provider -- transport: reviewer CLI binding
        self._workspace: ReviewerWorkspace | None = None
        self._base: Path | None = None
        self._sealed: tuple | None = None
        self._started = False

    @contextmanager
    def review_execution_boundary(
        self, repo_root: Path, manifest_paths: tuple[str, ...] | None,
    ) -> Iterator[None]:
        source = repo_root.resolve()
        temp_root = Path(tempfile.gettempdir()).resolve()
        if temp_root == source or temp_root.is_relative_to(source):
            raise AgentOutputError("reviewer runtime root overlaps the reviewed repository")
        base = Path(tempfile.mkdtemp(prefix="dao-codex-review-"))  # allowlist:provider -- transport: reviewer CLI binding
        workspace: ReviewerWorkspace | None = None
        self._base = base
        self._started = False
        self._sealed = None
        try:
            workspace = create_read_only_reviewer_workspace(
                repo_root, manifest_paths, base_dir=base,
            )
            self._workspace = workspace
            workspace.container.chmod(0o700)
            (workspace.container / "input").mkdir(mode=0o700)
            yield
        finally:
            try:
                if self._started and workspace is not None:
                    if self._sealed is None or _tree_fingerprint(workspace.container) != self._sealed:
                        raise AgentOutputError("Codex reviewer container changed during review")  # allowlist:provider -- transport: reviewer CLI binding
            finally:
                self.cleanup()
                self._workspace = None
                self._sealed = None
                self._base = None
                if workspace is not None:
                    workspace.cleanup()
                shutil.rmtree(base, ignore_errors=True)

    def seal_provider_input(self) -> None:
        if self._workspace is None:
            raise AgentOutputError("Codex reviewer container is missing")  # allowlist:provider -- transport: reviewer CLI binding
        input_dir = self._workspace.container / "input"
        for path in sorted(input_dir.rglob("*"), key=lambda item: len(item.parts), reverse=True):
            if path.is_symlink() or not (path.is_file() or path.is_dir()):
                raise AgentOutputError("Codex reviewer input contains an unsafe path")  # allowlist:provider -- transport: reviewer CLI binding
            path.chmod(0o555 if path.is_dir() else 0o444)
        input_dir.chmod(0o555)
        self._workspace.container.chmod(0o555)
        self._sealed = _tree_fingerprint(self._workspace.container)

    def before_provider_process(self) -> None:
        if self._sealed is None or self._workspace is None:
            raise AgentOutputError("Codex reviewer container is not sealed")  # allowlist:provider -- transport: reviewer CLI binding
        if _tree_fingerprint(self._workspace.container) != self._sealed:
            raise AgentOutputError("Codex reviewer container changed before start")  # allowlist:provider -- transport: reviewer CLI binding
        path = self.invocation.runtime_dir / "model-catalog.json"
        try:
            unchanged = catalog_row_matches(path, self.model, reviewer_model_row_sha256(self.settings.reviewer_model_catalog_json, self.model))
        except (OSError, TypeError, ValueError) as exc:
            raise AgentOutputError("reviewer model catalog is unavailable before start") from exc
        if not unchanged:
            raise AgentOutputError("reviewer model catalog changed before start")
        self._started = True

    def prepared_execution_root(self) -> Path:
        if self._workspace is None:
            raise AgentOutputError("Codex reviewer container is missing")  # allowlist:provider -- transport: reviewer CLI binding
        return self._workspace.container

    def build_command(self, prompt: str) -> tuple[list[str], bool]:
        raise RuntimeError("Codex reviews require a native review bundle")  # allowlist:provider -- transport: reviewer CLI binding

    def prepare_provider_input(self, prompt: str) -> PreparedProviderInput:
        raise RuntimeError("Codex reviews require a native review bundle")  # allowlist:provider -- transport: reviewer CLI binding

    def prepare_native_provider_input(self, bundle: NativeReviewRequestBundle) -> PreparedProviderInput:
        if not isinstance(bundle, NativeReviewRequestBundle) or bundle.capability_profile != "codex-reviewer":  # allowlist:provider -- profile configuration: reviewer writer
            raise TypeError("Codex reviewer requires a bound reviewer request")  # allowlist:provider -- transport: reviewer CLI binding
        workspace = self._workspace
        if workspace is None:
            raise AgentOutputError("Codex reviewer container is missing")  # allowlist:provider -- transport: reviewer CLI binding
        if self.provider_identity is None or self.provider_identity.kind != "verified":
            raise AgentOutputError("Codex reviewer has no bound provider identity")  # allowlist:provider -- transport: reviewer CLI binding
        rights = dict(self.role_binding.permissions)
        expected = dict(binding_for("codex", AgentRoleName.REVIEWER).permissions)  # allowlist:provider -- certification data: reviewer rights
        if rights != expected:
            raise AgentOutputError("Codex reviewer rights differ from bound profile")  # allowlist:provider -- transport: reviewer CLI binding
        package_root = codex_package_root(self.provider_identity.entry_path)  # allowlist:provider -- transport: reviewer CLI binding
        runtime_dir = self._new_runtime_dir()
        from agent_config import codex_process_environment  # allowlist:provider -- transport: shared environment policy
        self.env = codex_process_environment()  # allowlist:provider -- transport: shared environment policy
        self.env.setdefault("PATH", os.defpath)
        try:
            try:
                catalog_json = hardened_reviewer_catalog(json.loads(self.settings.reviewer_model_catalog_json), self.model)
            except (TypeError, ValueError) as exc:
                raise AgentOutputError("reviewer model catalog is missing or invalid", technical_text=str(exc)) from exc
            if catalog_json != self.settings.reviewer_model_catalog_json:
                raise AgentOutputError("reviewer model catalog differs from hardened run binding")
            catalog_path = runtime_dir / "model-catalog.json"
            catalog_path.write_text(catalog_json, encoding="utf-8")
            catalog_path.chmod(0o600)
            input_dir = workspace.container / "input"
            reviewer_input = build_reviewer_input(
                bundle, input_dir, chunk_chars=REVIEW_PACKET_CHUNK_CHARS,
                measured_path_placeholder=REVIEW_INPUT_PATH_PLACEHOLDER,
            )
            self.invocation.reviewer_input = reviewer_input
            schema_json = bundle.provider_response_schema_json
            schema_path = runtime_dir / "response-schema.json"
            schema_path.write_text(schema_json, encoding="utf-8")
            last_path = runtime_dir / "last-message.json"
            self.invocation.response_schema_file = schema_path
            self.invocation.last_message_file = last_path
            self.invocation.request_id = bundle.bound_context.request_id
            directive = reviewer_input.directive
            policy = self.role_binding.policy
            command = [
                self.cli_binary, "exec", "--model", self.model, "--config",
                f'model_reasoning_effort="{self.effort}"',
                "--skip-git-repo-check", "--ephemeral", "--color", "never", "--json",
                "--output-schema", str(schema_path), "--output-last-message", str(last_path),
                "-C", str(workspace.container), "--ignore-user-config", "--ignore-rules",
                *(item for feature in CODEX_REVIEW_DISABLED_FEATURES for item in ("--disable", feature)),  # allowlist:provider -- transport: reviewer CLI binding
                "-c", 'web_search="disabled"',
                "-c", "project_doc_max_bytes=0",
                "-c", 'shell_environment_policy.inherit="core"',
                "-c", codex_review_permission_config(package_root),  # allowlist:provider -- transport: reviewer CLI binding
                "-c", 'default_permissions="dao-reviewer"',
                "-c", "model_catalog_json=" + json.dumps(str(catalog_path)),
                "-",
            ]
            profile = normalize_transport_profile(
                "codex-reviewer", command, bound_package_root=package_root,  # allowlist:provider -- profile configuration: reviewer profile
                bound_container=workspace.container, bound_runtime_dir=runtime_dir,
                bound_model_row_sha256=reviewer_model_row_sha256(catalog_json, self.model),
            )
            assert_provider_capabilities("codex-reviewer", (), profile=profile)  # allowlist:provider -- profile configuration: reviewer profile
            transmitted = (
                *reviewer_input.request_files, *reviewer_input.evidence_files,
                *reviewer_input.manifest_pages, reviewer_input.manifest_file,
            )
            if len(transmitted) != len(reviewer_input.components):
                raise ReviewerInputError("Codex reviewer manifest component count differs")  # allowlist:provider -- transport: reviewer CLI binding
            components = [
                *(ProviderInputComponent(item.name, item.content)
                  for item, _path in zip(reviewer_input.components, transmitted, strict=True)),
                ProviderInputComponent("system_policy", policy),
                ProviderInputComponent("response_schema", schema_json),
                ProviderInputComponent("start_directive", measured_reviewer_path_text(directive, input_dir)),
            ]
            return PreparedProviderInput(
                tuple(command), f"{policy}\n\n{directive}", tuple(components),
                allow_duplicate_indexed_content=True,
            )
        except (ReviewerInputError, NativeProviderSchemaError) as exc:
            raise AgentOutputError("Codex reviewer input differs from bound transport", technical_text=str(exc)) from exc  # allowlist:provider -- transport: reviewer CLI binding

    def extract_output(self, stdout: str, stderr: str, extra_files: dict[str, str]) -> str:
        self.metadata = {**event_usage(stdout), **stream_model_metrics(stdout)}
        path = self.invocation.last_message_file
        process_text = "\n".join(part for part in (stderr, stdout) if part)
        if (extra_files.get("exit_code") not in (None, "0")
            and (path is None or not path.is_file() or is_provider_overload_error(process_text))):
            raise AgentProcessError(process_text or "reviewer process failed without output",
                                    exit_code=int(extra_files["exit_code"]))
        if path is None or not path.is_file():
            raise AgentOutputError("Codex reviewer produced no last-message file")  # allowlist:provider -- transport: reviewer CLI binding
        raw = path.read_text(encoding="utf-8").strip()
        try:
            envelope = json.loads(
                raw, object_pairs_hook=_unique_json_object,
                parse_constant=_invalid_json_constant,
            )
        except (json.JSONDecodeError, ValueError) as exc:
            raise AgentOutputError("Codex reviewer last message is invalid JSON", provider_text=raw) from exc  # allowlist:provider -- transport: reviewer CLI binding
        if not isinstance(envelope, dict) or set(envelope) != {"result"} or not isinstance(envelope["result"], dict):
            raise AgentOutputError("Codex reviewer response lacks its sole result object")  # allowlist:provider -- transport: reviewer CLI binding
        result = envelope["result"]
        try:
            validate_native_review_transport_binding(
                result, request_id=self.invocation.request_id,
                schema_version=self.role_binding.contract, reviewer=self.role_binding.role.value,
            )
            return canonical_native_review_json(result)
        except NativeReviewContractError as exc:
            raise AgentOutputError(
                f"Codex reviewer response violates the local result schema: {exc.detail}",  # allowlist:provider -- transport: reviewer CLI binding
                provider_data=result,
                technical_text=f"{exc.code.value}: {exc.detail}",
                orchestrator_diagnostic=exc.orchestrator_diagnostic,
            ) from exc
