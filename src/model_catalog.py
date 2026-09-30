"""Resolve configured families from the identity-bound local CLI catalog."""

import json
import re
from dataclasses import replace

from agent_config import MODEL_FAMILIES


def hardened_reviewer_catalog(catalog: object, model: str) -> str:
    """Keep model capabilities but remove every opt-in extra tool surface."""
    select_catalog_model(model, catalog, resume=True)
    result = json.loads(json.dumps(catalog))
    rows = result["models"] if isinstance(result, dict) else result
    for row in rows:
        for key in ("multi_agent_version", "multi_agent_reasoning_effort", "experimental_supported_tools",
                    "supports_search_tool", "web_search_tool_type"):
            row.pop(key, None)
    return json.dumps(result, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def select_catalog_model(requested: str, catalog: object, *, resume: bool = False) -> str:
    rows = catalog.get("models") if isinstance(catalog, dict) else catalog
    if not isinstance(rows, list):
        raise ValueError("model catalog must contain a models list")
    seen = set()
    for row in rows:
        if not isinstance(row, dict) or not isinstance(row.get("slug"), str) or row["slug"] in seen:
            raise ValueError("model catalog contains invalid or duplicate slugs")
        seen.add(row["slug"])
    families = MODEL_FAMILIES["codex"]  # allowlist:provider -- profile configuration: runtime family catalog
    if resume or requested not in families:
        if requested not in seen:
            raise ValueError(f"bound model {requested!r} is missing from the model catalog" if resume
                             else f"configured model {requested!r} is missing from the model catalog")
        return requested
    matches = [row for row in rows
               if re.fullmatch(r"gpt-[0-9]+(?:\.[0-9]+)*-" + re.escape(requested), row["slug"])
               and row.get("supported_in_api") is True and row.get("visibility") == "list"]
    if not matches:
        raise ValueError(f"model catalog has no eligible model for family {requested!r}")
    if any(not isinstance(row.get("priority"), int) or isinstance(row["priority"], bool) for row in matches):
        raise ValueError(f"model catalog has invalid priority for family {requested!r}")
    best = min(row["priority"] for row in matches)
    winners = [row["slug"] for row in matches if row["priority"] == best]
    if len(winners) != 1:
        raise ValueError(f"model catalog is ambiguous for family {requested!r}: {winners}")
    return winners[0]


def bind_catalog_models(slots: dict, identities: dict, run_command, *, resume: bool = False) -> None:
    """One catalog per distinct bound binary; resume checks availability only."""
    from logging import getLogger
    catalogs = {}
    updates = {}
    for slot, settings in slots.items():
        if settings.name != "codex":  # allowlist:provider -- profile configuration: local catalog binding
            continue
        identity = identities[slot]
        if identity.kind != "verified":
            continue  # Scripted transports have no provider binary or catalog.
        if identity.digest not in catalogs:
            rc, out, err = run_command([*identity.launch_prefix, "debug", "models"])
            if rc != 0:
                raise ValueError(f"slot={slot} catalog command failed at {identity.entry_path} (exit {rc})")
            try:
                catalogs[identity.digest] = json.loads(out)
            except (ValueError, TypeError) as exc:
                raise ValueError(f"slot={slot} model catalog returned invalid JSON") from exc
        model = select_catalog_model(settings.model, catalogs[identity.digest], resume=resume)
        catalog_json = hardened_reviewer_catalog(catalogs[identity.digest], model) if slot in {"reviewer", "final_reviewer"} else None
        updates[slot] = replace(settings, model=model, reviewer_model_catalog_json=catalog_json)
        getLogger(__name__).info("Model bound: slot=%s model=%s%s", slot, settings.model,
                                "→" + model if settings.model != model else "")
    slots.update(updates)
