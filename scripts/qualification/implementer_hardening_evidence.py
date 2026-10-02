"""Export Task D's existing offline/live proof without invoking a provider.

Private records and logs are read only. The intermediate projection belongs in
the operator's log directory; Bundle supplies deterministic redaction provenance.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import re
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from scripts.qualification import redact_evidence as redact
from scripts.qualification.topology_evidence import _runs

EVIDENCE = 'implementer-hardening-v1.json'
MANIFEST = 'implementer-hardening-redaction-manifest-v1.json'
PAIRS = ('codex-implementer', 'codex-reviewer', 'claude-implementer')  # allowlist:provider -- certification data: measured CLI pairs


def commitment(path: Path, source_id: str) -> dict:
    return {'source_id': source_id, 'sha256': hashlib.sha256(path.read_bytes()).hexdigest()}


def project(repo: Path, log: Path, offline: Path, offline_stdout: Path,
            config: Path, *, orchestrator_commit: str) -> dict:
    runs, _ = _runs(repo)
    if len(runs) != 2 or runs[0]['occupancy'] != runs[1]['occupancy']:
        raise ValueError('expected one plan/implementation handoff with identical occupancy')
    steps = [step for run in runs for step in run['steps']]
    reviews = [step for step in steps if step['record_type'] == 'review']
    finals = [step for step in steps if step['record_type'] == 'final_review_completed']
    if (len(reviews) != 2 or any(step['payload']['verdict'] != 'approved' for step in reviews)
        or len(finals) != 1 or not finals[0]['payload']['scan_complete']
        or finals[0]['payload']['new_findings'] or finals[0]['payload']['occurrences']
        or not any(step['record_type'] == 'workflow_completion'
                   and step['payload']['outcome'] == 'completed' for step in steps)):
        raise ValueError('live record proof is not completed without findings')
    text = log.read_text(encoding='utf-8')
    measured_commits = re.findall(r'^== .*? start orch=([a-f0-9]+)', text, re.M)
    if (not re.fullmatch(r'[a-f0-9]{40}', orchestrator_commit) or not measured_commits
        or any(not orchestrator_commit.startswith(commit) for commit in measured_commits)):
        raise ValueError('orchestrator commit differs from the operator log')
    exits = re.findall(r'^== (plan|umsetzung) exit=(\d+) snap_head=([a-f0-9]+) (\S+)', text, re.M)
    remaining = re.findall(r'^== verbliebene Provider-Prozesse: (\d+)', text, re.M)
    if ([int(row[1]) for row in exits] != [1, 0, 0] or remaining != ['0', '0', '0']
        or not text.rstrip().endswith('== ENDE rc=0')):
        raise ValueError('operator exit/process observations are incomplete')
    first_provider = text.index('provider attempt started')
    incident = "configured base branch 'main' is not a local branch"
    if incident not in text[:first_provider]:
        raise ValueError('expected pre-provider operator incident is absent')
    sources = [commitment(log, 'live-log'), commitment(config, 'live-config'),
               commitment(offline_stdout, 'offline-stdout')]
    reported = [json.loads(line) for line in offline_stdout.read_text().splitlines() if line.strip()]
    pairs = []
    for pair in PAIRS:
        folder = offline / pair
        report = json.loads((folder / 'report.json').read_bytes())
        if report['pair'] != pair or not report['passed'] or not report['checks']:
            raise ValueError('offline proof failed: ' + pair)
        checks = [{'check': row['check'], 'status': row['status']} for row in report['checks']]
        if any(row['status'] != 'passed' for row in checks):
            raise ValueError('offline check failed: ' + pair)
        if report not in reported:
            raise ValueError('offline stdout differs from report: ' + pair)
        request = json.loads((folder/'requests.jsonl').read_text().splitlines()[0])
        effort = request.get('reasoning', request.get('output_config', {}))['effort']
        provider = pair.split('-')[0]
        version = next(profile['cli_version'] for profile in runs[0]['occupancy'].values()
                       if profile['provider'] == provider)
        pairs.append({'pair': pair, 'passed': report['passed'], 'passed_checks': len(checks),
                      'total_checks': len(checks), 'checks': checks,
                      'model': request['model'], 'effort': effort, 'cli_version': version,
                      'cli_version_source': 'Operator measurement: same installations as the live run',
                      'elapsed_seconds': report['elapsed_seconds']})
        sources.extend(commitment(path, 'offline/' + pair + '/' + path.name)
                       for path in sorted(folder.iterdir()) if path.is_file())
    public_runs = []
    for run in runs:
        selected = []
        for step in run['steps']:
            kind, payload = step['record_type'], step.get('payload', {})
            if kind == 'provider_attempt' and payload['phase'] == 'succeeded':
                keys = ('slot', 'operation', 'phase', 'started_at', 'ended_at', 'duration_seconds',
                        'model', 'effort', 'attempt_number')
            elif kind == 'review':
                keys = ('reviewer', 'verdict', 'finding_ids', 'request_id', 'response_sha256')
            elif kind == 'final_review_completed':
                keys = ('reviewer', 'new_findings', 'occurrences', 'scan_complete',
                        'reviewed_head_commit', 'validation_attestation_record_id', 'response_sha256')
            elif kind == 'workflow_completion':
                keys = ('outcome', 'final_binding_id')
            elif kind == 'validation_attestation':
                keys = ('results', 'attested_by', 'output_digest', 'content_record_id')
            elif kind in {'side_effect', 'provider_content'}:
                selected.append(step)
                continue
            else:
                continue
            selected.append({'record_id': step['record_id'], 'record_type': kind,
                             'payload': {key: payload[key] for key in keys}})
        public_runs.append({'run_id': run['run_id'], 'records': run['records'], 'steps': selected})
    timeline = []
    for timestamp, message in re.findall(r'^\[([^\]]+)\] \[INFO\] (.*)$', text, re.M):
        if ('[AGENT_RESULT]' in message and any(marker in message for marker in
                ('ready=', 'decision=', 'result_type=final_review_completed'))
            or message.startswith('Validation matrix ')):
            timeline.append({'time': timestamp, 'event': message})
    head = subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=repo, text=True).strip()
    if not head.startswith(exits[-1][2]):
        raise ValueError('clone HEAD differs from the operator completion log')
    historical_table = subprocess.check_output(
        ['git', 'show', orchestrator_commit + ':schemas/role-provider-certifications-v1.json'], cwd=ROOT)
    transport = next(row for row in json.loads(historical_table)['certifications']
                     if row['capability_profile'] == 'codex-implementer')  # allowlist:provider -- certification data: measured transport profile
    return {'schema_version': 'implementer-hardening-v1', 'date': '2026-10-02', 'status': 'completed',
            'orchestrator_commit': orchestrator_commit, 'occupancy': runs[0]['occupancy'],
            'hardening': {'capability_profile': transport['capability_profile'],
                          'transport_profile': transport['probe_profile'],
                          'capability_sha256': transport['capability_sha256'],
                          'policy_sha256': transport['policy_sha256'],
                          'rights_sha256': transport['rights_sha256']},
            'offline': {'mode': 'real CLIs against loopback fake; decoys only; no account',
                        'toolchain': 'Node v22.23.2; explicit read root', 'pairs': pairs},
            'live': {'runs': public_runs, 'timeline': timeline, 'timeline_timezone': 'Europe/Berlin',
                     'process_results': [{'stage': stage, 'exit_code': int(code), 'snapshot_head': sha,
                                          'ended_at': '2026-10-02 ' + time}
                                         for stage, code, sha, time in exits],
                     'local_merge_commit': head,
                     'rejections': len(re.findall(r'\[PERMISSION_DENIAL\]', text)),
                     'rejection_measurement': 'Permission-denial events in the operator live log',
                     'denied_reviews': sum(
                         step['payload']['verdict'] != 'approved' for step in reviews),
                     'max_observed_model_silence_seconds': max(map(int, re.findall(r'model silence: (\d+)s', text))),
                     'silence_measurement': 'Maximum logged sample; active tool commands excluded. Not continuous telemetry.',
                     'remaining_provider_processes': 0, 'new_followup_tasks': 0,
                     'process_observation_source': 'Operator log observations after each process exit',
                     'incidents': [{'time': '2026-10-02 01:56:44', 'classification': 'operator-prerequisite',
                                    'detail': 'Clone lacked local main branch; corrected by operator before retry.',
                                    'provider_calls': 0, 'product_finding': False}]},
            'source_commitments': sources,
            'measurement_scope': {'live_runs': 1, 'tasks': 1, 'repositories': 1,
                                  'note': 'One standard-occupancy task in an isolated clone. No general reliability claim.'}}


def export(document: dict, source: Path, output: Path) -> None:
    content = (json.dumps(document, ensure_ascii=False, sort_keys=True, indent=2) + '\n').encode()
    if source.exists() and source.read_bytes() != content:
        raise FileExistsError('intermediate source differs')
    source.write_bytes(content)
    bundle = redact.Bundle(redact.Redactor.runtime())
    bundle.add(EVIDENCE, source, 'task-d-hardening-projection')
    bundle.notes['separate_manifest'] = 'Task D is an independent export; historical Task A and reviewer manifests remain unchanged.'
    bundle.notes['projection'] = 'Select operational outcomes and check names; omit private paths, account, usage and quota metadata before redaction. Byte digests commit to complete private logs and records.'
    # Bundle's canonical manifest name is retained in staging for verification.
    staging = source.parent / (source.stem + '-redacted-bundle')
    bundle.finish(staging)
    targets = {EVIDENCE: staging/EVIDENCE, MANIFEST: staging/'redaction-manifest-v1.json'}
    for name, path in targets.items():
        target = output/name
        if target.exists() and target.read_bytes() != path.read_bytes():
            raise FileExistsError('public evidence differs: ' + name)
    output.mkdir(parents=True, exist_ok=True)
    for name, path in targets.items():
        (output/name).write_bytes(path.read_bytes())


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ('repo', 'log', 'offline', 'offline-stdout', 'config', 'source-out', 'out'):
        parser.add_argument('--' + name, type=Path, required=True)
    parser.add_argument('--orchestrator-commit', required=True)
    args = parser.parse_args()
    export(project(args.repo, args.log, args.offline, args.offline_stdout, args.config,
                   orchestrator_commit=args.orchestrator_commit), args.source_out, args.out)


if __name__ == '__main__':
    main()
