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


def actual_model_metrics(events: list[dict], *, warn: bool = True) -> dict[str, object]:
    """Record observed models separately from the configured model alias."""
    from logging import getLogger
    initial = None
    models = set()
    def add(value):
        if isinstance(value, str) and re.fullmatch(r"[A-Za-z0-9_.:/-]{1,160}", value):
            models.add(value)
    for event in events:
        if event.get("type") == "system" and event.get("subtype") == "init":
            value = event.get("model")
            if isinstance(value, str) and re.fullmatch(r"[A-Za-z0-9_.:/-]{1,160}", value):
                initial = value
        if event.get("type") == "assistant" and isinstance(event.get("message"), dict):
            add(event["message"].get("model"))
        if event.get("type") in {"turn.completed", "turn.started"}:
            add(event.get("model"))
        if event.get("type") == "result" and isinstance(event.get("modelUsage"), dict):
            for model in event["modelUsage"]:
                add(model)
    result = {}
    if initial is not None:
        result["init_model"] = initial
    if models:
        result["actual_models"] = sorted(models)
    if warn and (len(models) > 1 or (initial is not None and models - {initial})):
        getLogger(__name__).warning("[MODEL_CHANGE] init_model=%s actual_models=%s", initial, sorted(models))
    return result


def stream_model_metrics(stdout: str, *, warn: bool = True) -> dict[str, object]:
    events = []
    for line in stdout.splitlines():
        try:
            event = json.loads(line)
        except ValueError:
            continue
        if isinstance(event, dict):
            events.append(event)
    return actual_model_metrics(events, warn=warn)


def _redacted_excerpt(value: object, limit: int = 200) -> str:
    if not isinstance(value, str):
        return "[input unavailable]"
    for name, secret in os.environ.items():
        if re.search(r"token|secret|password|credential|api.?key", name, re.I) and len(secret) >= 4:
            value = value.replace(secret, "[redacted]")
    value = re.sub(r'(?i)("[^"\n]*(?:token|secret|password|credential|api[_-]?key)[^"\n]*"\s*:\s*)"(?:\\.|[^"\\])*"',
                   r'\1"[redacted]"', value)
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
        if "disposition" in denial:
            getLogger(__name__).warning("[PERMISSION_DENIAL] %s disposition=%s",
                                       json.dumps(denial, ensure_ascii=False), denial["disposition"])
        else:
            getLogger(__name__).warning("[PERMISSION_DENIAL] %s", json.dumps(denial, ensure_ascii=False))


def compact_stream_event(event: dict, state: dict, provider_label: str = "provider", version_field: str = "version") -> str | None:
    """Bound and redact the useful portions of a verbose JSON stream event."""
    kind = event.get("type")
    if kind == "system" and event.get("subtype") == "init":
        if state.get("provider_init_seen"):
            return None
        state["provider_init_seen"] = True
        return (provider_label + " model=" + _redacted_excerpt(event.get("model"), 80)
                + " version=" + _redacted_excerpt(event.get(version_field), 40))
    if kind == "system" and event.get("subtype") == "permission_denied":
        summary = state.get("tool_calls", {}).get(event.get("tool_use_id"))
        if summary is None:
            summary = permission_denial_summaries([event])[0]
        return "[PERMISSION_DENIAL] " + json.dumps(summary, ensure_ascii=False)
    message = event.get("message")
    content = message.get("content") if isinstance(message, dict) else None
    if kind not in {"assistant", "user"} or not isinstance(content, list):
        return None
    parts = []
    for block in content:
        if not isinstance(block, dict):
            continue
        if kind == "assistant" and block.get("type") == "tool_use":
            if isinstance(block.get("id"), str):
                state.setdefault("tool_calls", {})[block["id"]] = permission_denial_summaries([{
                    "tool_name": block.get("name"), "tool_use_id": block["id"], "tool_input": block.get("input"),
                }])[0]
            parts.append("tool=" + _redacted_excerpt(block.get("name"), 64)
                         + " input=" + _redacted_excerpt(json.dumps(block.get("input"), ensure_ascii=False), 200))
        elif kind == "assistant" and block.get("type") == "text":
            parts.append("text=" + _redacted_excerpt(block.get("text"), 200))
        elif kind == "user" and block.get("type") == "tool_result" and block.get("is_error") is True:
            value = block.get("content")
            if isinstance(value, list):
                value = " ".join(part.get("text", "") for part in value if isinstance(part, dict)
                                 and isinstance(part.get("text"), str))
            parts.append("tool_error=" + _redacted_excerpt(value, 200))
    return " | ".join(parts[:4]) if parts else None
