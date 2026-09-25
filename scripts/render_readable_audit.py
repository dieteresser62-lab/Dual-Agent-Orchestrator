#!/usr/bin/env python3
"""Render a validated audit chain into a separate directory without providers."""

from __future__ import annotations

import argparse
from contextlib import ExitStack
import json
from pathlib import Path
import shutil
import sys
import tempfile

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from artifact_replay import replay_artifacts
from artifact_models import CorrectionWorkUnitPayload, WorkUnitPayload
from artifact_store import ArtifactStore
from audit_document_contract import PLAN_APPENDIX_HEADING
from readable_audit import (
    AuditFacts, authored_slice_sections, read_approved_plan,
    render_overall, render_plan_appendix, render_slice,
)
from semantic_markdown import parse_semantic_markdown


def _load_read_only(stack: ExitStack, repository: Path, run_id: str) -> tuple[AuditFacts, ArtifactStore]:
    temporary = Path(stack.enter_context(tempfile.TemporaryDirectory(prefix="audit-render-")))
    source = repository / ".orchestrator" / "artifacts" / run_id
    if not source.is_dir():
        raise ValueError(f"record chain is missing: {run_id}")
    destination = temporary / ".orchestrator" / "artifacts" / run_id
    destination.mkdir(parents=True)
    for name in ("records", "blobs"):
        shutil.copytree(source / name, destination / name)
    store = ArtifactStore(temporary, run_id)
    records = store.load_chain()
    replay = replay_artifacts(records, run_id)
    facts = AuditFacts(
        replay,
        read_blob=store.read_blob,
        read_plan=lambda commit, path: read_approved_plan(repository, commit, path),
    )
    return facts, store


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repository", type=Path, required=True)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--plan-run-id")
    args = parser.parse_args()
    repository = args.repository.resolve()
    output = args.output.resolve()
    if output == repository or output.is_relative_to(repository):
        parser.error("output must be outside the source repository")
    with ExitStack() as stack:
        facts, _ = _load_read_only(stack, repository, args.run_id)
        results: list[dict[str, object]] = []

        def emit(relative: str, markdown: str) -> None:
            parse_semantic_markdown(markdown, path=relative, require_managed=True)
            target = output / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(markdown, encoding="utf-8")
            source = repository / relative
            results.append({
                "path": relative,
                "before_bytes": source.stat().st_size if source.is_file() else None,
                "after_bytes": target.stat().st_size,
            })

        identity = facts.replay.run_identity
        if identity is None:
            raise ValueError("chain lacks run identity")
        if identity.audit_report_path:
            emit(
                identity.audit_report_path,
                render_overall(facts, task=Path(identity.task_file).stem, branch=identity.branch),
            )
        if facts.plan is not None:
            slice_scopes = ((spec.slice_id, spec.paths) for spec in facts.plan.slices)
        else:
            slice_scopes = (
                (record.payload.slice_id, record.payload.paths)
                for record in facts.records
                if isinstance(record.payload, (WorkUnitPayload, CorrectionWorkUnitPayload))
            )
        seen_slices: set[str] = set()
        for slice_id, scope in slice_scopes:
            if slice_id in seen_slices or not facts.slice_units(slice_id):
                continue
            seen_slices.add(slice_id)
            paths = [path for path in scope if path.startswith("docs/internal/slice-") and path.endswith(".md")]
            if len(paths) != 1:
                raise ValueError(f"Slice {slice_id} lacks one audit path")
            source = repository / paths[0]
            implementation, deviations = (
                authored_slice_sections(source.read_text(encoding="utf-8"))
                if source.is_file() else ("Noch nicht dokumentiert.", "Keine.")
            )
            emit(paths[0], render_slice(facts, int(slice_id), implementation=implementation, deviations=deviations))
        if facts.plan is not None:
            plan_facts = facts
            if args.plan_run_id:
                plan_facts, _ = _load_read_only(stack, repository, args.plan_run_id)
                if plan_facts.plan is None or plan_facts.plan.approved_plan_commit != facts.plan.approved_plan_commit or plan_facts.plan.work_plan_path != facts.plan.work_plan_path:
                    raise ValueError("planning chain does not match approved plan")
            else:
                matches = []
                for candidate in (repository / ".orchestrator" / "artifacts").iterdir():
                    if candidate.name == args.run_id or not (candidate / "records").is_dir():
                        continue
                    previous, _ = _load_read_only(stack, repository, candidate.name)
                    if (
                        previous.replay.run_identity is not None
                        and previous.replay.run_identity.execution_mode == "PLAN_ONLY"
                        and previous.plan is not None
                        and previous.plan.approved_plan_commit == facts.plan.approved_plan_commit
                        and previous.plan.work_plan_path == facts.plan.work_plan_path
                    ):
                        matches.append(previous)
                if len(matches) == 1:
                    plan_facts = matches[0]
                elif len(matches) > 1:
                    raise ValueError("multiple matching planning chains; select --plan-run-id")
            plan_path = facts.plan.work_plan_path
            approved = read_approved_plan(repository, facts.plan.approved_plan_commit, plan_path)
            prefix = approved.split(f"\n## {PLAN_APPENDIX_HEADING}\n", 1)[0].rstrip("\n")
            emit(plan_path, prefix + "\n\n" + render_plan_appendix(plan_facts))
        print(json.dumps(results, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
