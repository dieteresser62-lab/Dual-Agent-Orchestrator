"""Small, bounded provider telemetry without prompts or tool output."""

import json
import os
import re


def event_usage(stdout: str) -> dict[str, object]:
    totals: dict[str, int] = {}
    for line in stdout.splitlines():
        try:
            event = json.loads(line)
        except (ValueError, TypeError):
            continue
        if not isinstance(event, dict) or event.get("type") != "turn.completed":
            continue
        usage = event.get("usage")
        if not isinstance(usage, dict):
            continue
        for source, target in (("input_tokens", "input_tokens"),
                               ("cached_input_tokens", "cache_read_input_tokens"),
                               ("output_tokens", "output_tokens")):
            value = usage.get(source)
            if isinstance(value, int) and not isinstance(value, bool) and value >= 0:
                totals[target] = totals.get(target, 0) + value
    return {"usage": totals} if totals else {}


def _redacted_excerpt(value: object, limit: int = 200) -> str:
    if not isinstance(value, str):
        return "[input unavailable]"
    for name, secret in os.environ.items():
        if re.search(r"token|secret|password|credential|api.?key", name, re.I) and len(secret) >= 4:
            value = value.replace(secret, "[redacted]")
    value = re.sub(r"(?i)((?:bearer|basic)\s+)\S+", r"\1[redacted]", value)
    value = re.sub(r"(?i)((?:[\w-]*(?:token|secret|password|api[_-]?key)[\w-]*)\s*[=:]\s*)(?:\"[^\"]*\"|'[^']*'|[^\s;,]+)",
                   r"\1[redacted]", value)
    value = re.sub(r"(?i)(--[\w-]*(?:token|secret|password|api[_-]?key)[\w-]*\s+)(?:\"[^\"]*\"|'[^']*'|\S+)",
                   r"\1[redacted]", value)
    value = re.sub(r"(?:sk-|ghp_|github_pat_)[A-Za-z0-9_-]+", "[redacted]", value)
    value = re.sub(r"(https?://)[^/\s@]+@", r"\1[redacted]@", value)
    value = " ".join(value.split())
    value = "".join(char if ord(char) >= 32 and ord(char) != 127 else "?" for char in value)
    if not value:
        return "[input unavailable]"
    return value[:limit] + ("…" if len(value) > limit else "")


def permission_denial_summaries(value: object) -> list[dict[str, str]]:
    if not isinstance(value, list):
        return []
    summaries = []
    for denial in value[:16]:
        denial = denial if isinstance(denial, dict) else {}
        data = denial.get("tool_input", denial.get("input", {}))
        data = data if isinstance(data, dict) else {}
        excerpt = data.get("file_path", data.get("path", data.get("command", denial.get("input_excerpt"))))
        summaries.append({
            "tool_name": _redacted_excerpt(denial.get("tool_name"), 64),
            "tool_use_id": _redacted_excerpt(denial.get("tool_use_id"), 100),
            "input_excerpt": _redacted_excerpt(excerpt),
        })
    return summaries


def failure_metrics(envelope: dict, keys: tuple[str, ...]) -> dict[str, object]:
    return {key: permission_denial_summaries(envelope[key]) if key == "permission_denials" else envelope[key]
            for key in keys if key in envelope}


def log_permission_denials(metrics: dict[str, object]) -> None:
    from logging import getLogger
    for denial in metrics.get("permission_denials", []):
        getLogger(__name__).warning("[PERMISSION_DENIAL] %s", json.dumps(denial, ensure_ascii=False))
