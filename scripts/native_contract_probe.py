#!/usr/bin/env python3
"""Probe provider Structured-Output schema features outside the repository.

This is deliberately a transport probe, not a workflow driver.  Every provider
process runs in a fresh temporary directory with a read-only/no-write sandbox,
and the repository status is compared byte-for-byte before and after the run.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import subprocess
import sys
import tempfile
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from native_provider_schema import normalize_transport_profile  # noqa: E402


FEATURE_SCHEMAS: dict[str, dict[str, Any]] = {
    "closed_object": {
        "type": "object",
        "properties": {"value": {"type": "string", "const": "ok"}},
        "required": ["value"],
        "additionalProperties": False,
    },
    "min_max_items": {
        "type": "object",
        "properties": {
            "values": {
                "type": "array",
                "minItems": 1,
                "maxItems": 2,
                "items": {"type": "string"},
            }
        },
        "required": ["values"],
        "additionalProperties": False,
    },
    "const_list": {
        "type": "object",
        "properties": {
            "values": {"type": "array", "const": ["C-01", "C-02"]}
        },
        "required": ["values"],
        "additionalProperties": False,
    },
    "positional_tuple": {
        "type": "object",
        "properties": {
            "values": {
                "type": "array",
                "prefixItems": [
                    {"type": "string", "const": "C-01"},
                    {"type": "integer", "const": 2},
                ],
                "items": False,
                "minItems": 2,
                "maxItems": 2,
            }
        },
        "required": ["values"],
        "additionalProperties": False,
    },
    "nested_one_of": {
        "type": "object",
        "properties": {
            "result": {
                "oneOf": [
                    {
                        "type": "object",
                        "properties": {
                            "kind": {"type": "string", "const": "yes"}
                        },
                        "required": ["kind"],
                        "additionalProperties": False,
                    },
                    {
                        "type": "object",
                        "properties": {
                            "kind": {"type": "string", "const": "no"}
                        },
                        "required": ["kind"],
                        "additionalProperties": False,
                    },
                ]
            }
        },
        "required": ["result"],
        "additionalProperties": False,
    },
    "nested_any_of": {
        "type": "object",
        "properties": {
            "result": {
                "anyOf": [
                    {
                        "type": "object",
                        "properties": {
                            "kind": {"type": "string", "const": "yes"}
                        },
                        "required": ["kind"],
                        "additionalProperties": False,
                    },
                    {
                        "type": "object",
                        "properties": {
                            "kind": {"type": "string", "const": "no"}
                        },
                        "required": ["kind"],
                        "additionalProperties": False,
                    },
                ]
            }
        },
        "required": ["result"],
        "additionalProperties": False,
    },
}


def _run(command: list[str], *, cwd: Path, stdin: str | None = None) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        command,
        cwd=cwd,
        input=stdin,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=300,
        check=False,
    )


def _repository_status(repo_root: Path) -> str:
    completed = _run(
        ["git", "status", "--porcelain=v1", "--untracked-files=all"],
        cwd=repo_root,
    )
    if completed.returncode != 0:
        raise RuntimeError(completed.stderr.strip() or "git status failed")
    return completed.stdout


def _version(binary: str, *, cwd: Path) -> str:
    completed = _run([binary, "--version"], cwd=cwd)
    if completed.returncode != 0:
        raise RuntimeError(completed.stderr.strip() or f"{binary} --version failed")
    return (completed.stdout or completed.stderr).strip()


def _codex_probe(binary: str, schema: dict[str, Any], workdir: Path) -> tuple[list[str], subprocess.CompletedProcess[str]]:
    schema_path = workdir / "response-schema.json"
    output_path = workdir / "last-message.json"
    schema_path.write_text(
        json.dumps(schema, ensure_ascii=False, sort_keys=True, separators=(",", ":")),
        encoding="utf-8",
    )
    command = [
        binary,
        "exec",
        "--model",
        "gpt-5.6-sol",
        "--config",
        'model_reasoning_effort="medium"',
        "--skip-git-repo-check",
        "--ephemeral",
        "--sandbox",
        "read-only",
        "--color",
        "never",
        "--json",
        "--output-schema",
        str(schema_path),
        "--output-last-message",
        str(output_path),
        "-",
    ]
    return command, _run(
        command,
        cwd=workdir,
        stdin="Return the smallest JSON value accepted by the supplied schema. Use no tools.",
    )


def _claude_probe(binary: str, schema: dict[str, Any], workdir: Path) -> tuple[list[str], subprocess.CompletedProcess[str]]:
    schema_json = json.dumps(
        schema, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    )
    command = [
        binary,
        "-p",
        "--output-format",
        "json",
        "--model",
        "sonnet",
        "--effort",
        "high",
        "--tools",
        "Read",
        "--allowedTools",
        "Read",
        "--disallowedTools",
        "Bash,Edit,Write,NotebookEdit,Grep,Glob",
        "--permission-mode",
        "dontAsk",
        "--setting-sources",
        "user",
        "--safe-mode",
        "--strict-mcp-config",
        "--prompt-suggestions",
        "false",
        "--add-dir",
        str(workdir),
        "--json-schema",
        schema_json,
        "--no-session-persistence",
        "--disable-slash-commands",
        "Return the smallest JSON value accepted by the supplied schema. Use no tools.",
    ]
    return command, _run(command, cwd=workdir)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--provider", choices=("codex", "claude"), required=True)
    parser.add_argument("--feature", choices=tuple(FEATURE_SCHEMAS), required=True)
    parser.add_argument("--binary")
    parser.add_argument("--repo-root", type=Path, default=Path.cwd())
    args = parser.parse_args()

    repo_root = args.repo_root.resolve()
    before = _repository_status(repo_root)
    binary = args.binary or args.provider
    with tempfile.TemporaryDirectory(prefix=f"dao-{args.provider}-schema-probe-") as raw:
        workdir = Path(raw)
        version = _version(binary, cwd=workdir)
        if args.provider == "codex":
            command, completed = _codex_probe(
                binary, FEATURE_SCHEMAS[args.feature], workdir
            )
        else:
            command, completed = _claude_probe(
                binary, FEATURE_SCHEMAS[args.feature], workdir
            )
    after = _repository_status(repo_root)
    if after != before:
        raise RuntimeError("provider probe changed the repository worktree")

    report = {
        "provider": args.provider,
        "binary_name": Path(binary).name,
        "cli_version": version,
        "feature": args.feature,
        "accepted": completed.returncode == 0,
        "returncode": completed.returncode,
        "command": command,
        "normalized_transport_profile": normalize_transport_profile(
            args.provider, command
        ).document,
        "stdout": completed.stdout[-4000:],
        "stderr": completed.stderr[-4000:],
        "repository_status_unchanged": True,
    }
    print(json.dumps(report, ensure_ascii=False, sort_keys=True, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
