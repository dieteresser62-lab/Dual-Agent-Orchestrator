"""Versioned provider-specific compatibility and Phase-0 protection profiles."""
from __future__ import annotations

from dataclasses import dataclass


LEGACY_CANDIDATE = "agy"
LEGACY_REFERENCE = "claude"  # allowlist:provider -- profile configuration: v5 reference
LEGACY_CAPABILITIES = {LEGACY_CANDIDATE: "antigravity", LEGACY_REFERENCE: "claude"}  # allowlist:provider -- profile configuration: v5 transport
LEGACY_REFERENCE_DECISION_KEY = "claude_special_decision"  # allowlist:provider -- certification data: v5 result field
LEGACY_REFERENCE_EVIDENCE_ID = "claude_slice_review"  # allowlist:provider -- certification data: v5 saved review
LEGACY_REFERENCE_DIGEST_KEY = "claude_slice_review_sha256"  # allowlist:provider -- certification data: v5 proof
LEGACY_REFERENCE_DESCRIPTION = "Previously validated Claude slice review from quality Q3: "  # allowlist:provider -- certification data: v5 request
DEFAULT_REVIEW_CAPABILITY = "claude"  # allowlist:provider -- profile configuration: production budget baseline
IMPLEMENTER_CAPABILITY = "codex"  # allowlist:provider -- profile configuration: production budget baseline
LEGACY_RUNTIME = {
    LEGACY_CANDIDATE: {"model": "gemini-3.1-pro-high", "effort": "high", "isolation": True},
    LEGACY_REFERENCE: {"model": "opus", "effort": "high", "isolation": False},
}
LEGACY_BLIND_WORDS = ("antigravity", "agy", "claude", "codex", "gemini")  # allowlist:provider -- certification data: v5 self-identification scan


@dataclass(frozen=True)
class ProtectionProfile:
    name: str
    capability: str
    cli_flags: tuple[str, ...]
    deny_rules: tuple[str, ...]
    settings_mode: str
    needs_isolated_home: bool
    soft_denial_without_result: bool
    environment: tuple[tuple[str, str], ...]


PROTECTION_PROFILES = {
    "agy": ProtectionProfile(
        "agy", "antigravity",  # allowlist:provider -- profile configuration: AGY Phase-0
        ("-p", "--output-format", "json", "--json-schema", "{schema}",
         "--print-timeout", "590s", "--disable-slash-commands", "--sandbox",
         "--model", "{model}", "--effort", "high", "--agent", "dao-reviewer"),
        ("command(*)", "write_file(*)", "mcp(*)", "read_url(*)", "execute_url(*)",
         "read_file(/tmp)", "read_file(/home)", "read_file(/root)",
         "read_file(/mnt)", "read_file(/proc)", "read_file(/run)"),
        "request-review", True, True, (("AGY_CLI_DISABLE_AUTO_UPDATE", "true"),)),
    "claude": ProtectionProfile(  # allowlist:provider -- profile configuration: Claude Phase-0
        "claude", "claude",  # allowlist:provider -- profile configuration: Claude Phase-0
        ("-p", "--output-format", "json", "--model", "{model}", "--effort", "high",
         "--tools", "Read", "--allowedTools", "Read",
         "--disallowedTools", "Bash,Edit,Write,NotebookEdit,Grep,Glob",
         "--permission-mode", "dontAsk", "--safe-mode",
         "--strict-mcp-config", "--prompt-suggestions", "false",
         "--add-dir", "{runtime}",
         "--system-prompt", "{policy}",
         "--json-schema", "{schema_json}", "--no-session-persistence",
         "--disable-slash-commands", "--restricted"),
        (), "dontAsk", False, False, ()),
}


@dataclass(frozen=True)
class ReviewEnvelopeProfile:
    success_field: str
    success_value: object
    error_field: str
    denials_field: str
    schema_echo_field: str | None


REVIEW_ENVELOPES = {
    "antigravity": ReviewEnvelopeProfile("status", "SUCCESS", "error", "denied_actions", "json_schema"),
    "claude": ReviewEnvelopeProfile("is_error", False, "is_error", "permission_denials", None),  # allowlist:provider -- profile configuration: measured reference envelope
}
