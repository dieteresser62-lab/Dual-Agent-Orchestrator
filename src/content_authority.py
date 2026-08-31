"""Canonical content evidence shared by validation and artifact replay."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
from typing import Sequence


VALIDATION_MATRIX_DIGEST_V1 = "validation-matrix-v1"
RAW_OUTPUT_DIGEST_V1 = "raw-output-v1"


@dataclass(frozen=True, slots=True)
class ValidationCapture:
    """Exact command output plus the compact state-v3 projection."""

    command: str
    outcome: str
    exit_code: int
    stdout: str
    stderr: str
    compact_output: str

    def __post_init__(self) -> None:
        if not self.command.strip():
            raise ValueError("validation capture command must not be empty")
        if self.outcome not in {
            "pass",
            "fail",
            "timeout",
            "missing",
            "unavailable",
        }:
            raise ValueError("validation capture outcome is invalid")
        if isinstance(self.exit_code, bool) or not isinstance(self.exit_code, int):
            raise ValueError("validation capture exit_code must be an integer")
        for value, label in (
            (self.stdout, "stdout"),
            (self.stderr, "stderr"),
            (self.compact_output, "compact_output"),
        ):
            if not isinstance(value, str):
                raise ValueError(f"validation capture {label} must be text")


def validation_output_digest(
    captures: Sequence[ValidationCapture],
    digest_format: str = VALIDATION_MATRIX_DIGEST_V1,
) -> str:
    """Reproduce the pre-R8 attestation digest without silent redefinition."""
    if not captures:
        raise ValueError("validation content must contain at least one capture")
    if digest_format == RAW_OUTPUT_DIGEST_V1:
        if len(captures) != 1:
            raise ValueError("raw-output digest requires exactly one capture")
        payload = captures[0].compact_output.encode("utf-8")
    elif digest_format == VALIDATION_MATRIX_DIGEST_V1:
        items: list[dict[str, object]] = []
        for item in captures:
            if item.outcome in {"missing", "unavailable"}:
                items.append(
                    {
                        "command": item.command,
                        "outcome": item.outcome,
                        "detail": item.stderr,
                    }
                )
            else:
                items.append(
                    {
                        "command": item.command,
                        "outcome": item.outcome,
                        "exit_code": item.exit_code,
                        "stdout": item.stdout,
                        "stderr": item.stderr,
                    }
                )
        payload = json.dumps(
            items,
            ensure_ascii=True,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    else:
        raise ValueError("validation content digest format is unsupported")
    return hashlib.sha256(payload).hexdigest()


__all__ = [
    "RAW_OUTPUT_DIGEST_V1",
    "VALIDATION_MATRIX_DIGEST_V1",
    "ValidationCapture",
    "validation_output_digest",
]
