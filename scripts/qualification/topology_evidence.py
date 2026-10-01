"""Project completed, validated topology records without running a provider."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'src'))


def project(repo: Path, log: Path, *, orchestrator_commit: str, remaining_provider_processes: int) -> dict:
    from artifact_store import ArtifactStore
    from artifact_replay import replay_artifacts
    from artifact_models import artifact_payload_document

    class ReadOnlyStore(ArtifactStore):
        def _refresh_cache_with_context(self, chain, expected_cache_chain):
            # Head is a disposable cache. Validate records/blobs without repairing
            # private source evidence, even when that cache is stale or absent.
            pass

    runs, baseline = [], None
    for folder in sorted((repo / '.orchestrator/artifacts').iterdir()):
        if not (folder / 'records').is_dir():
            continue
        store = ReadOnlyStore(repo, folder.name)
        chain = store.load_chain()
        replay_artifacts(chain, folder.name)
        refs, steps, occupancy = [], [], {}
        for record in chain:
            kind = record.record_type.value
            payload = artifact_payload_document(record.payload)
            path = folder / 'records' / (record.record_id + '.json')
            refs.append({'record_id': record.record_id, 'record_type': kind,
                         'sha256': hashlib.sha256(path.read_bytes()).hexdigest()})
            if kind == 'run_identity' and baseline is None:
                baseline = payload['branch_base']
            if kind == 'run_profile':
                occupancy = {slot: {key: payload[slot][key] for key in ('provider', 'model', 'effort')}
                             | {'cli_version': payload[slot]['binary_identity']['version']}
                             for slot in ('implementer', 'reviewer', 'final_reviewer')}
            if kind == 'provider_content':
                result = json.loads(store.read_blob(record.payload.blob))
                result = result.get('result', result)
                steps.append({'record_id': record.record_id, 'record_type': kind,
                              'role': payload['role'], 'result_type': result.get('result_type'),
                              'request_id': result.get('request_id')})
            if kind in {'review', 'final_review_completed', 'finding_transition', 'workflow_completion',
                        'validation_attestation', 'provider_attempt'}:
                # Retain model telemetry, judgments and attempts; omit machine paths and binary internals.
                selected = {key: value for key, value in payload.items()
                            if key not in {'binary_identity', 'binary_identity_sha256', 'provider_text', 'command'}}
                steps.append({'record_id': record.record_id, 'record_type': kind, 'payload': selected})
            if kind == 'side_effect' and payload['effect_class'] == 'git_commit' and payload['phase'] == 'result':
                steps.append({'record_id': record.record_id, 'record_type': kind,
                              'operation': payload['operation'][0], 'commit': payload['result']})
        runs.append({'run_id': folder.name, 'occupancy': occupancy, 'steps': steps, 'records': refs})
    text = log.read_text(encoding='utf-8')
    stages = ('Planlauf exit=0', 'Umsetzungslauf exit=130', 'Resume exit=130', 'Resume 2 exit=0')
    if not all(stage in text for stage in stages) or remaining_provider_processes != 0:
        raise ValueError('topology completion/interruption evidence is incomplete')
    return {'schema_version': 'topology-run-v1', 'date': '2026-10-01', 'status': 'completed',
            'orchestrator_commit': orchestrator_commit, 'runs': runs,
            'log_sha256': hashlib.sha256(log.read_bytes()).hexdigest(),
            'interruptions': [{'signal': 'SIGINT', 'step': 'implementer_implementation', 'exit_code': 130},
                              {'signal': 'SIGINT', 'step': 'reviewer_slice_review', 'exit_code': 130,
                               'reason': 'No reviewer output for approximately 31 minutes.'}],
            'process_results': [{'stage': 'plan', 'exit_code': 0}, {'stage': 'implementation', 'exit_code': 130},
                                {'stage': 'resume', 'exit_code': 130}, {'stage': 'resume_2', 'exit_code': 0}],
            'remaining_provider_processes': remaining_provider_processes,
            'changed_paths': subprocess.run(['git', 'diff', '--name-only', baseline or
                next(step['commit'] for run in runs for step in run['steps'] if step['record_type'] == 'side_effect'),
                'HEAD'], cwd=repo, check=True, capture_output=True, text=True).stdout.splitlines(),
            'process_observation_source': 'Operator inspection reported on 2026-10-01; not reconstructed from records.',
            'final_review_note': 'Completion is not finding-free approval; final discoveries remain in the recorded payload.'}
