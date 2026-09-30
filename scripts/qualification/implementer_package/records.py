"""Read fully validated authoritative records, never state/cache conclusions."""
from __future__ import annotations

import json
from pathlib import Path
import sys


def read_records(repo: Path) -> tuple[list[dict], list[dict]]:
    # Importing the store has no provider or workflow side effects.
    source_root = str(Path(__file__).resolve().parents[3] / "src")
    if source_root not in sys.path:
        sys.path.insert(0, source_root)
    from artifact_models import ProviderContentPayload, artifact_payload_document
    from artifact_store import ArtifactStore
    from artifact_replay import replay_artifacts
    artifacts = repo / ".orchestrator/artifacts"
    roots = [path for path in artifacts.iterdir() if path.is_dir() and (path / "records").exists()] if artifacts.exists() else []
    if len(roots) != 1:
        raise ValueError("Expected exactly one authoritative run chain")
    store = ArtifactStore(repo, roots[0].name)
    chain = store.load_chain()
    replay_artifacts(chain, roots[0].name)
    kinds = {item.record_type.value for item in chain}
    ledger = [item.payload for item in chain if item.record_type.value == "side_effect"
              and item.payload.effect_class == "ledger" and item.payload.phase == "result"]
    if not {"run_identity", "run_profile", "task"}.issubset(kinds) or len(ledger) != 1:
        raise ValueError("Incomplete authoritative baseline or side-effect ledger")
    records, results = [], []
    for record in chain:
        payload = record.payload
        records.append({"record_id": record.record_id, "record_type": record.record_type.value,
                        "payload": artifact_payload_document(payload)})
        if isinstance(payload, ProviderContentPayload) and payload.role.value == "implementer":
            results.append(json.loads(store.read_blob(payload.blob)))
    return records, results


def project(records: list[dict], results: list[dict]) -> dict:
    def payloads(kind):
        return [item["payload"] for item in records if item["record_type"] == kind]
    attempts = payloads("provider_attempt")
    commits = [item for item in payloads("side_effect") if item["effect_class"] == "git_commit"
               and item["phase"] == "result" and item["operation"][0] == "slice_commit"]
    stopped = [item["rule_id"] for item in results if item.get("result_type") == "stop_result"]
    completions = payloads("workflow_completion")
    completion = completions[-1]["outcome"] if completions else None
    if not completion and any(item["binding_kind"] == "implementation_handoff" for item in payloads("binding")):
        completion = "completed"
    effects = payloads("side_effect")
    completed_effects = [item["effect_key"] for item in effects if item["phase"] == "result"]
    intents = {item["effect_key"] for item in effects if item["phase"] == "intent"}
    ledger_clean = (len(completed_effects) == len(set(completed_effects)) and set(completed_effects) == intents)
    return {"valid_records": bool(records), "records": records, "attempts": attempts,
            "denials": [denial for item in attempts + payloads("invocation_failure")
                       for denial in item.get("permission_denials", [])],
            "implementer_results": results, "stop_reasons": sorted(set(stopped)),
            "completion": completion, "slice_commits": len(commits),
            "ledger_clean": ledger_clean,
            "uninspectable_results": [item for item in attempts
                                      if item.get("role") == "implementer" and item.get("failure_kind") == "output"],
            "resume_checks": payloads("resume_check")}


def implementation_active(repo: Path) -> bool:
    try:
        records, _ = read_records(repo)
    except (ValueError, OSError, RuntimeError):
        return False
    attempts = [item["payload"] for item in records if item["record_type"] == "provider_attempt"]
    active = {item["work_unit_id"] for item in attempts
              if item["phase"] == "started" and item["operation"] == "implementer_implementation"}
    from provider_process import ProcessStatus, observe_process
    effects = [item["payload"] for item in records if item["record_type"] == "side_effect"]
    finished = {item["effect_key"] for item in effects if item["phase"] == "result"}
    for item in effects:
        if (item["effect_class"] == "provider_start" and item["phase"] == "intent"
            and item["work_unit_id"] in active and item["effect_key"] not in finished
            and item["operation"][1] == "implementer_implementation"):
            observed = observe_process(repo / item["operation"][6], item["effect_key"])
            if observed.status is ProcessStatus.RUNNING:
                return True
    return False
