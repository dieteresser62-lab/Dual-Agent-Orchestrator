"""Project completed, validated topology records without running a provider."""
from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime
from zoneinfo import ZoneInfo
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'src'))


def _runs(repo: Path, run_ids=None):
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
        if not (folder / 'records').is_dir() or run_ids is not None and folder.name not in run_ids:
            continue
        store = ReadOnlyStore(repo, folder.name)
        chain = store.load_chain()
        replay_artifacts(chain, folder.name)
        batches = {}
        for batch in (folder/'records').glob('arb1-*.json'):
            for document in json.loads(batch.read_bytes())['records']:
                batches[document['record_id']] = batch
        refs, steps, occupancy = [], [], {}
        for record in chain:
            kind = record.record_type.value
            payload = artifact_payload_document(record.payload)
            path = folder / 'records' / (record.record_id + '.json')
            path = batches.get(record.record_id, path)
            refs.append({'record_id': record.record_id, 'record_type': kind,
                         'sha256': hashlib.sha256(path.read_bytes()).hexdigest(),
                         **({'record_file': path.name} if path.name.startswith('arb1-') else {})})
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
                        'validation_attestation', 'provider_attempt', 'invocation_failure'}:
                # Retain model telemetry, judgments and attempts; omit machine paths and binary internals.
                selected = {key: value for key, value in payload.items()
                            if key not in {'binary_identity', 'binary_identity_sha256', 'provider_text', 'command'}}
                steps.append({'record_id': record.record_id, 'record_type': kind, 'payload': selected})
            if kind == 'side_effect' and payload['effect_class'] == 'git_commit' and payload['phase'] == 'result':
                steps.append({'record_id': record.record_id, 'record_type': kind,
                              'operation': payload['operation'][0], 'commit': payload['result']})
        runs.append({'run_id': folder.name, 'occupancy': occupancy, 'steps': steps, 'records': refs})
    return runs, baseline


def project(repo: Path, log: Path, *, orchestrator_commit: str, remaining_provider_processes: int) -> dict:
    runs, baseline = _runs(repo)
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


def project_followup(repo: Path, log: Path, *, remaining_provider_processes: int) -> dict:
    """Project a multi-version follow-up; causes/fixes are operator annotations."""
    text = log.read_text(encoding='utf-8')
    first = re.search(r'\[(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})\]', text)
    if first is None or not text.rstrip().endswith('== ENDE rc=0') or remaining_provider_processes != 0:
        raise ValueError('topology follow-up completion evidence is incomplete')
    started = datetime.strptime(first[1], '%Y-%m-%d %H:%M:%S').replace(tzinfo=ZoneInfo('Europe/Berlin'))
    cutoff = started.astimezone(ZoneInfo('UTC')).strftime('%Y%m%d-%H%M%SZ')
    run_ids = {folder.name for folder in (repo/'.orchestrator/artifacts').iterdir() if folder.name >= cutoff}
    runs, _ = _runs(repo, run_ids)
    steps = [step for run in runs for step in run['steps']]
    final = [step for step in steps if step['record_type'] == 'final_review_completed']
    if (not runs or not any(step['record_type'] == 'workflow_completion' for step in steps)
        or len(final) != 1 or len(final[0]['payload']['new_findings']) != 2 or 'followup02' not in text):
        raise ValueError('topology follow-up lacks completed records')
    headers = list(re.finditer(r'^== (Folgelauf[^\n]*orch=([a-f0-9]+)[^\n]*)$', text, re.M))
    sections = []
    for index, header in enumerate(headers):
        block = text[header.end():headers[index + 1].start() if index + 1 < len(headers) else len(text)]
        end = re.search(r'^== .*?exit=(\d+) snap_head=([a-f0-9]+)', block, re.M)
        if end is None:
            raise ValueError('topology follow-up section has no exit result')
        sections.append({'stage': 'plan' if 'Umsetzung' not in header[1] else 'implementation',
                         'orchestrator_commit': header[2], 'exit_code': int(end[1]), 'snapshot_head': end[2]})
    halted = re.findall(r'Workflow stopped: exit=3 .*?step=(\w+).*?kind=(\w+)', text)
    if len(halted) != 4 or [item['exit_code'] for item in sections] != [3, 0, 3, 3, 3, 0]:
        raise ValueError('topology follow-up halt evidence differs')
    annotations = [
        ('quoted-heredoc substitution false alarm', 16, '5a7d6f8'),
        ('permission halt; full input was not retained, cause cannot be reconstructed', 17, '4811480'),
        ('opaque-write false alarm for fd duplication and special parameter', 18, '0ac5466'),
        ('provider capacity treated as a process halt', 20, None),
    ]
    halts = [{'step': step, 'failure_kind': kind, 'cause': cause, 'correcting_round': round_number,
              'correcting_commit': commit, 'correction_note': 'Diagnosis added; original cause remains unknown' if round_number == 17 else
              'Implemented after the measured run; commit not yet assigned' if commit is None else 'Corrected before a later resume'}
             for (step, kind), (cause, round_number, commit) in zip(halted, annotations)]
    return {'schema_version': 'topology-followup-v1', 'date': started.date().isoformat(), 'status': 'completed',
            'measurement_scope': 'Multiple orchestrator versions; not a single-commit measurement',
            'private_source': {'repository': str(repo), 'log': str(log)},
            'runs': runs, 'sections': sections, 'halts': halts,
            'log_sha256': hashlib.sha256(log.read_bytes()).hexdigest(),
            'remaining_provider_processes': remaining_provider_processes,
            'process_observation_source': 'Operator log inspection; not reconstructed from records',
            'halt_annotation_source': 'Operator findings and correction-round reports',
            'followup': {'name': 'followup02', 'finding_count': 2, 'executed': False},
            'final_review_note': 'Final review completed with two discoveries; not finding-free approval'}
