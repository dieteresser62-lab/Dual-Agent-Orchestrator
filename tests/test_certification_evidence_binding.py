"""Provider-free regressions for branch-review B's public qualification contracts."""
import copy
import json
import subprocess
from argparse import Namespace
from pathlib import Path
from types import SimpleNamespace

import pytest

from scripts import probe_reviewer as probe
from scripts.qualification import redact_evidence as redact, run_implementer_package as runner
from scripts.qualification.topology_evidence import project
from native_provider_schema import provider_capability
from tests.test_role_certification import _copy_sources
from tests.test_redact_evidence import PROVIDERS, redactor
from agent_roles import AgentSlot
from agent_config import AgentSettings
from role_certification import CertificationError, CertificationErrorCode, load_role_certifications, read_qualification_evidence
from workflow_run_setup import _apply_resumed_agent_profiles

ROOT = probe.ROOT
HARDENING = 'docs/evidence/codex/implementer-hardening-v1.json'  # allowlist:provider -- certification data: standard implementer proof
HARDENING_MANIFEST = 'docs/evidence/codex/implementer-hardening-redaction-manifest-v1.json'  # allowlist:provider -- certification data: separate provenance
REVIEW = ROOT / 'docs/evidence' / PROVIDERS[0]
IMPLEMENT = ROOT / 'docs/evidence' / PROVIDERS[1]
LEGACY_PROVIDER = provider_capability(probe.LEGACY_CAPABILITIES[probe.LEGACY_CANDIDATE])['provider']


@pytest.mark.parametrize('path', (HARDENING, HARDENING_MANIFEST))
@pytest.mark.parametrize('mutation', ('delete', 'replace'))
def test_hardened_baseline_evidence_fails_closed_at_start_and_resume(tmp_path, monkeypatch, path, mutation):
    _copy_sources(tmp_path)
    table = load_role_certifications(root=tmp_path)
    selected = table.entries[0]
    assert selected.status == 'certified'
    assert selected.evidence_path.endswith('/implementer/role-certification-v1.json')
    file = tmp_path/path
    if mutation == 'delete':
        file.unlink()
    else:
        file.write_bytes(b'{}\n')
    with pytest.raises(CertificationError) as start:
        load_role_certifications(root=tmp_path)
    assert start.value.code is CertificationErrorCode.EVIDENCE_INVALID
    monkeypatch.setattr('workflow_run_setup.load_role_certifications', lambda: load_role_certifications(root=tmp_path))
    slots = {slot.value: SimpleNamespace() for slot in AgentSlot}
    state = SimpleNamespace(protocol_binding=SimpleNamespace())
    with pytest.raises(CertificationError) as resume:
        _apply_resumed_agent_profiles(Namespace(slot_settings=slots), state)
    assert resume.value.code is CertificationErrorCode.EVIDENCE_INVALID


def test_hardened_baseline_cannot_drop_shared_binding_or_restore_old_index(tmp_path):
    _copy_sources(tmp_path)
    path = tmp_path / load_role_certifications(root=tmp_path).entries[0].evidence_path
    index = json.loads(path.read_bytes())
    index['shared_evidence'] = [ref for ref in index['shared_evidence'] if ref['path'] != HARDENING]
    path.write_text(json.dumps(index))
    table_path = tmp_path/'schemas/role-provider-certifications-v1.json'
    table = json.loads(table_path.read_bytes())
    table['certifications'][0]['evidence']['sha256'] = probe.sha(path.read_bytes())
    table_path.write_text(json.dumps(table))
    with pytest.raises(CertificationError) as missing:
        load_role_certifications(root=tmp_path)
    assert missing.value.code is CertificationErrorCode.EVIDENCE_INVALID
    historical = tmp_path/'docs/evidence/role-certification-v1.json'
    assert probe.sha(historical.read_bytes()) == 'cb28369f17cc4c8ad66442cc0afd3488cc042fe72693e9bf4d5c7b2f4f2f78fa'
    table['certifications'][0]['evidence'] = {
        'path': str(historical.relative_to(tmp_path)), 'sha256': probe.sha(historical.read_bytes())}
    table_path.write_text(json.dumps(table))
    with pytest.raises(CertificationError) as old:
        load_role_certifications(root=tmp_path)
    assert old.value.code is CertificationErrorCode.EVIDENCE_INVALID


def test_hardening_export_retains_checks_decisions_record_commitments_and_limits():
    document = probe.read_evidence(ROOT/HARDENING)
    assert document['orchestrator_commit'] == '03091d7e9e59b84a16b70420ed6569621f3126ba'
    pairs = document['offline']['pairs']
    assert [row['total_checks'] for row in pairs] == [62, 23, 83]
    assert all(row['passed'] and len(row['checks']) == row['passed_checks'] == row['total_checks']
               and all(check['status'] == 'passed' for check in row['checks']) for row in pairs)
    assert all(row['effort'] == 'high' for row in pairs)
    live = document['live']
    assert live['rejections'] == live['remaining_provider_processes'] == live['new_followup_tasks'] == 0
    assert live['max_observed_model_silence_seconds'] == 65
    assert [row['exit_code'] for row in live['process_results']] == [1, 0, 0]
    assert live['incidents'][0]['classification'] == 'operator-prerequisite'
    assert live['incidents'][0]['provider_calls'] == 0 and live['incidents'][0]['product_finding'] is False
    assert [len(run['records']) for run in live['runs']] == [48, 85]
    steps = [step for run in live['runs'] for step in run['steps']]
    assert [s['payload']['verdict'] for s in steps if s['record_type'] == 'review'] == ['approved', 'approved']
    final = next(s['payload'] for s in steps if s['record_type'] == 'final_review_completed')
    assert final['scan_complete'] and final['new_findings'] == []
    assert live['local_merge_commit'].startswith('777154c')
    for run in live['runs']:
        assert len({r['record_id'] for r in run['records']}) == len(run['records'])
        assert all(redact.SHA.fullmatch(row['sha256']) for row in run['records'])
    assert all(redact.SHA.fullmatch(row['sha256']) for row in document['source_commitments'])
    assert [document['measurement_scope'][key] for key in ('live_runs', 'tasks', 'repositories')] == [1, 1, 1]


def bound_paths():
    paths = set()
    for directory in (REVIEW, IMPLEMENT):
        document = probe.read_evidence(directory/'role-certification-v1.json')
        paths.update(item['path'] for item in document['shared_evidence'])
        completion = probe.read_evidence(directory/'phase0-results.json')
        for kind in ('protection', 'format'):
            paths.update(str(directory.relative_to(ROOT)/item['path']) for item in completion.get(kind, []))
    legacy = probe.qualification_pair({'schema_version': 'qualification-protocol-v5'}).evidence_directory
    manifest = legacy / 'redaction-manifest-v1.json'
    paths.add(str(manifest.relative_to(ROOT)))
    paths.update(str(legacy.relative_to(ROOT)/item['path']) for item in probe.read_evidence(manifest)['files'])
    return sorted(paths)


def test_preregistered_digest_resolves_to_table_bound_redacted_bytes(tmp_path):
    _copy_sources(tmp_path)
    directory = probe.qualification_pair({'schema_version': 'qualification-protocol-v5'}).evidence_directory
    path = str(directory.relative_to(ROOT) / 'phase-0-v1.json')
    original = probe.read_evidence(directory / 'qualification-protocol-v5.json')['phase0_sha256']
    public = read_qualification_evidence(tmp_path, path, original)
    assert public == (tmp_path / path).read_bytes()
    assert probe.sha(public) != original
    with pytest.raises(CertificationError) as exc:
        read_qualification_evidence(tmp_path, path, '0' * 64)
    assert exc.value.code is CertificationErrorCode.EVIDENCE_INVALID


@pytest.mark.parametrize('filename', ('phase-0-v1.json', 'redaction-manifest-v1.json'))
def test_preregistered_resolution_rejects_replaced_file_or_manifest(tmp_path, filename):
    _copy_sources(tmp_path)
    directory = probe.qualification_pair({'schema_version': 'qualification-protocol-v5'}).evidence_directory
    base = directory.relative_to(ROOT)
    original = probe.read_evidence(directory / 'qualification-protocol-v5.json')['phase0_sha256']
    (tmp_path / base / filename).write_bytes(b'{}\n')
    with pytest.raises(CertificationError) as exc:
        read_qualification_evidence(tmp_path, str(base / 'phase-0-v1.json'), original)
    assert exc.value.code is CertificationErrorCode.EVIDENCE_INVALID


def test_restoring_private_original_does_not_bypass_redaction_binding(tmp_path):
    _copy_sources(tmp_path)
    directory = probe.qualification_pair({'schema_version': 'qualification-protocol-v5'}).evidence_directory
    path = str(directory.relative_to(ROOT) / 'phase-0-v1.json')
    original_digest = probe.read_evidence(directory / 'qualification-protocol-v5.json')['phase0_sha256']
    original = subprocess.check_output(['git', 'show', f'0f1d63e:{path}'], cwd=ROOT)
    assert probe.sha(original) == original_digest
    (tmp_path / path).write_bytes(original)
    for verify in (lambda: read_qualification_evidence(tmp_path, path, original_digest),
                   lambda: load_role_certifications(root=tmp_path)):
        with pytest.raises(CertificationError) as exc:
            verify()
        assert exc.value.code is CertificationErrorCode.EVIDENCE_INVALID


@pytest.mark.parametrize('path', bound_paths())
@pytest.mark.parametrize('mutation', ('delete', 'replace'))
def test_all_shared_and_case_files_fail_closed_at_start_and_resume(tmp_path, monkeypatch, path, mutation):
    _copy_sources(tmp_path)
    file = tmp_path/path
    if mutation == 'delete':
        file.unlink()
    else:
        file.write_bytes(b'{}\n')
    with pytest.raises(CertificationError) as exc:
        load_role_certifications(root=tmp_path)
    assert exc.value.code is CertificationErrorCode.EVIDENCE_INVALID
    monkeypatch.setattr('workflow_run_setup.load_role_certifications', lambda: load_role_certifications(root=tmp_path))
    selected = dict(zip(AgentSlot, (PROVIDERS[1], PROVIDERS[0], PROVIDERS[0])))
    models = dict(zip(AgentSlot, ('opus', 'gpt-6.1-sol', 'gpt-6.1-sol')))
    settings = {slot.value: AgentSettings(selected[slot], selected[slot], models[slot], 900, 'high') for slot in AgentSlot}
    state = SimpleNamespace(protocol_binding=SimpleNamespace(**{
        f'{slot.value}_profile': SimpleNamespace(provider=selected[slot], model=models[slot]) for slot in AgentSlot}))
    with pytest.raises(CertificationError) as resumed:
        _apply_resumed_agent_profiles(Namespace(slot_settings=settings), state)
    assert resumed.value.code is CertificationErrorCode.EVIDENCE_INVALID


def test_topology_projection_binds_records_and_retains_final_discoveries():
    topology = probe.read_evidence(IMPLEMENT/'topology-run-v1.json')
    assert topology['orchestrator_commit'].startswith('8fd548e')
    assert [entry['exit_code'] for entry in topology['process_results']] == [0, 130, 130, 0]
    assert [entry['step'] for entry in topology['interruptions']] == ['implementer_implementation', 'reviewer_slice_review']
    assert topology['remaining_provider_processes'] == 0
    assert [len(run['records']) for run in topology['runs']] == [48, 127]
    for run in topology['runs']:
        assert len({item['record_id'] for item in run['records']}) == len(run['records'])
        assert all(redact.SHA.fullmatch(item['sha256']) for item in run['records'])
    steps = [step for run in topology['runs'] for step in run['steps']]
    commits = [step['commit'] for step in steps if step['record_type'] == 'side_effect']
    assert [commit[:7] for commit in commits] == ['b1c9fb8', '0c12d5f']
    assert any(step['record_type'] == 'finding_transition' and step['payload']['finding_id'] == 'R-01'
               and step['payload']['finding_status'] == 'closed' for step in steps)
    final = next(step['payload'] for step in steps if step['record_type'] == 'final_review_completed')
    assert [item['finding_id'] for item in final['new_findings']] == ['R-02', 'R-03']
    assert {'plan_result', 'implementation_result', 'correction_result'} <= {
        step.get('result_type') for step in steps if step['record_type'] == 'provider_content'}


def test_followup_topology_retains_halts_versions_and_final_discoveries():
    topology = probe.read_evidence(IMPLEMENT/'topology-followup-v1.json')
    assert topology['schema_version'] == 'topology-followup-v1'
    assert [section['orchestrator_commit'] for section in topology['sections']] == [
        '96cf4a5', '5a7d6f8', '5a7d6f8', '4811480', '0ac5466', '0ac5466']
    assert [section['exit_code'] for section in topology['sections']] == [3, 0, 3, 3, 3, 0]
    assert [halt['correcting_round'] for halt in topology['halts']] == [16, 17, 18, 20]
    assert topology['halts'][-1]['correcting_commit'] is None
    assert 'cannot be reconstructed' in topology['halts'][1]['cause']
    assert topology['remaining_provider_processes'] == 0
    assert topology['followup'] == {'name': 'followup02', 'finding_count': 2, 'executed': False}
    assert [len(run['records']) for run in topology['runs']] == [63, 164]
    steps = [step for run in topology['runs'] for step in run['steps']]
    commits = [step['commit'][:7] for step in steps if step['record_type'] == 'side_effect']
    assert commits == ['c35013d', '05e4011', 'e7da371']
    assert topology['sections'][-1]['snapshot_head'] == 'e7da371'
    assert any(step['record_type'] == 'finding_transition' and step['payload']['finding_id'] == 'R-01'
               and step['payload']['finding_status'] == 'closed' for step in steps)
    final = next(step['payload'] for step in steps if step['record_type'] == 'final_review_completed')
    assert len(final['new_findings']) == 2
    assert any(step['record_type'] == 'validation_attestation' for step in steps)
    assert any(ref.get('record_file', '').startswith('arb1-') for run in topology['runs'] for ref in run['records'])
    for run in topology['runs']:
        assert all(redact.SHA.fullmatch(ref['sha256']) for ref in run['records'])


def test_followup_export_preserves_outcomes_and_private_sources(tmp_path):
    from scripts.qualification.topology_evidence import project_followup
    # Use the public projection as fixture, keeping this test independent of private paths.
    source = tmp_path/'original.json'
    document = probe.read_evidence(IMPLEMENT/'topology-followup-v1.json')
    source.write_bytes(redact.encoded(document))
    before = source.read_bytes()
    bundle = redact.Bundle(redactor())
    exported = bundle.add('topology-followup-v1.json', source, 'followup-fixture')
    bundle.finish(tmp_path/'public')
    assert exported == document and source.read_bytes() == before
    assert redact.outcomes(exported) == redact.outcomes(document)
    log = tmp_path/'incomplete.log'
    log.write_text('interrupted')
    with pytest.raises(ValueError, match='incomplete'):
        project_followup(tmp_path, log, remaining_provider_processes=0)


def test_topology_tool_validates_chain_without_providers(tmp_path, monkeypatch):
    from artifact_store import ArtifactStore
    record = SimpleNamespace(record_id='bound', record_type=SimpleNamespace(value='side_effect'), payload=None)
    folder = tmp_path/'.orchestrator/artifacts/run/records'
    folder.mkdir(parents=True)
    (folder/'bound.json').write_text('{"committed":true}')
    def load(self):
        self._refresh_cache_with_context((record,), None)
        return (record,)
    monkeypatch.setattr(ArtifactStore, 'load_chain', load)
    monkeypatch.setattr(ArtifactStore, '_refresh_head_cache', lambda *args, **kwargs: pytest.fail('private cache write'))
    checked = []
    monkeypatch.setattr('artifact_replay.replay_artifacts', lambda chain, run: checked.append(run))
    monkeypatch.setattr('artifact_models.artifact_payload_document', lambda payload: {
        'effect_class': 'git_commit', 'phase': 'result', 'operation': ['slice_commit'], 'result': 'a'*40})
    monkeypatch.setattr('scripts.qualification.topology_evidence.subprocess.run',
                        lambda *args, **kwargs: SimpleNamespace(stdout='tests/layout.mjs\n'))
    log = tmp_path/'run.log'
    log.write_text('Planlauf exit=0\nUmsetzungslauf exit=130\nResume exit=130\nResume 2 exit=0\n')
    result = project(tmp_path, log, orchestrator_commit='a'*40, remaining_provider_processes=0)
    assert checked == ['run']
    assert result['runs'][0]['records'][0]['sha256'] == probe.sha((folder/'bound.json').read_bytes())
    with pytest.raises(ValueError, match='incomplete'):
        project(tmp_path, log, orchestrator_commit='a'*40, remaining_provider_processes=1)


def test_successor_protocol_checks_both_manufacturers_and_historical_decision_is_explicit():
    protocol = probe.read_evidence(REVIEW/'qualification-protocol-v6.json')
    assert probe.qualification_raters(protocol) == (probe.LEGACY_CANDIDATE, 'steering')
    decisions = probe.read_evidence(REVIEW/'operator-decisions-v1.json')
    decision = next(row for row in decisions['decisions'] if row['id'] == 'rater-neutrality-v6')
    assert decision['rater_manufacturer'] == decision['reference_manufacturer']
    protocol['schema_version'] = 'qualification-protocol-v7'
    with pytest.raises(ValueError, match='manufacturer'):
        probe.qualification_raters(protocol)
    protocol['raters'] = protocol['quality']['raters'] = [probe.LEGACY_CANDIDATE, LEGACY_PROVIDER]
    assert probe.qualification_raters(protocol) == (probe.LEGACY_CANDIDATE, LEGACY_PROVIDER)
    protocol['raters'] = protocol['quality']['raters'] = [PROVIDERS[0], probe.LEGACY_CANDIDATE]
    with pytest.raises(ValueError, match='manufacturer'):
        probe.qualification_raters(protocol)


def test_email_case_insensitive_accounts_and_embedded_quota_are_removed(tmp_path):
    source = {'text': 'TEST-ACCOUNT on Test-Machine at Test.Person@example.org',
              'stdout': json.dumps({'type': 'rate_limit_event', 'rate_limit_info': {'utilization': .4,
                       'overageDisabledReason': 'disabled'}, 'organization_id': 'private-id',
                       'session_id': 'operational-random-id', 'uuid': 'event-random-id'})+'\n'+
                        json.dumps({'type': 'result', 'checks': {'safe': True}, 'count': 8})+'\n',
              'checks': {'safe': False}, 'passed': False, 'count': 8}
    original = tmp_path/'original.json'; original.write_text(json.dumps(source))
    bundle = redact.Bundle(redactor()); public = bundle.add('report.json', original, 'source')
    assert public['text'] == '<user> on <host> at <email>'
    assert redactor().text('Person@localhost')[0] == '<email>'
    assert redactor().text('/opt/node_modules/@vendor/package')[0] == '/opt/node_modules/@vendor/package'
    events = [json.loads(line) for line in public['stdout'].splitlines()]
    assert events[0] == {'type': 'rate_limit_event', 'session_id': 'operational-random-id', 'uuid': 'event-random-id'}
    assert public['checks'] == source['checks'] and public['count'] == source['count']
    assert events[1]['checks'] == {'safe': True}
    manifest = bundle.finish(tmp_path/'public')
    assert any('account' in change['rules'] for change in manifest['files'][0]['changes'])
    assert redact.verify(tmp_path/'public', redactor=redactor()) == manifest
    assert redactor().transform(public)[0] == public
    for field in ('accountId', 'organization_uuid', 'ORG_ID', 'subscription_id', 'utilization', 'rate_limit_info'):
        with pytest.raises(ValueError, match='account'):
            redactor().check({'stdout': json.dumps({field: 'private'})})


def test_all_public_streams_and_manifests_are_clean_and_w4_is_unmeasured():
    for directory in (REVIEW, IMPLEMENT):
        redact.verify(directory)
        for path in directory.rglob('*'):
            if path.is_file() and path.suffix in {'.json', '.gz'}:
                redact.Redactor.runtime().check(probe.read_evidence(path))
    completion = probe.read_evidence(IMPLEMENT/'phase0-results.json')
    assert completion['not_measured_cases'] == ['W4'] and completion['safe_cases'] == 7
    w4 = next(row for row in completion['protection'] if row['case'] == 'W4')
    assert w4['safe'] is None and w4['measurement'] == 'not_measured_provider_refusal_offline_proven'
    decisions = probe.read_evidence(IMPLEMENT/'operator-decisions-v1.json')
    assert 'W4' in decisions['decisions'][0]['not_measured']


@pytest.mark.parametrize('change', ('call_id', 'envelope', 'checks', 'unexpected_check', 'new_attempt', 'legacy_v6'))
def test_size_override_binds_only_the_exact_measured_attempt(change):
    protocol = probe.read_evidence(REVIEW/'qualification-protocol-v6.json')
    pair = probe.qualification_pair(protocol)
    decisions = probe.read_evidence(REVIEW/'operator-decisions-v1.json')
    series = probe.read_evidence(REVIEW/'qualification-series-v1.json')
    row = decisions['decisions'][0]
    verdicts = {row['series']: {'kind': 'large_output', 'provider': pair.candidate, 'failed_cases': ['512']}}
    assert probe.size_override(decisions, verdicts, pair, protocol, series)
    legacy_shape = copy.deepcopy(decisions)
    legacy_shape['decisions'][0].update(failed_case='128', not_run=['512'])
    legacy_verdicts = copy.deepcopy(verdicts)
    legacy_verdicts[row['series']]['failed_cases'] = ['128']
    with pytest.raises(ValueError, match='explicit protocol'):
        probe.size_override(legacy_shape, legacy_verdicts, pair)
    if change == 'call_id': row['call_id'] += '-other'
    elif change == 'envelope': row['envelope_sha256'] = 'f'*64
    elif change == 'checks': row['failed_checks'] = ['finding_count']
    elif change == 'unexpected_check':
        next(call for call in series['attempts'] if call['call_id'] == row['call_id'])['checks']['scope'] = False
    elif change == 'new_attempt':
        call = copy.deepcopy(next(call for call in series['attempts'] if call['call_id'] == row['call_id']))
        call['call_id'] += '-new'; series['attempts'].append(call)
    else:
        row.update(failed_case='128', not_run=['512'])
    with pytest.raises(ValueError):
        probe.size_override(decisions, verdicts, pair, protocol, series)


def test_package_code_identity_records_exact_git_bytes(tmp_path, monkeypatch):
    table = tmp_path/'schemas/role-provider-certifications-v1.json'
    table.parent.mkdir(); table.write_text('{"gate":"operator-copy"}')
    calls = []
    def run(command, **kwargs):
        calls.append((command, kwargs['cwd']))
        return SimpleNamespace(stdout='a'*40+'\n' if command[-1]=='HEAD' else b' M schemas/role-provider-certifications-v1.json\n')
    monkeypatch.setattr(runner.subprocess, 'run', run)
    identity = runner.orchestrator_identity(tmp_path)
    assert identity == {'commit': 'a'*40, 'status_porcelain_sha256': probe.sha(b' M schemas/role-provider-certifications-v1.json\n'),
                        'certification_table_sha256': probe.sha(table.read_bytes())}
    assert all(cwd == tmp_path for _, cwd in calls)


def test_missing_legacy_model_pattern_has_typed_entry_error(tmp_path):
    _copy_sources(tmp_path)
    path = tmp_path/'schemas/role-provider-certifications-v1.json'
    table = json.loads(path.read_text())
    row = next(row for row in table['certifications'] if row.get('model_family_pattern'))
    row.pop('model_family_pattern'); path.write_text(json.dumps(table))
    with pytest.raises(CertificationError) as exc:
        load_role_certifications(root=tmp_path)
    assert exc.value.code is CertificationErrorCode.ENTRY_INVALID
    assert 'model family pattern is required' in str(exc.value)


def test_slot_scope_measured_profiles_and_timeout_documentation():
    table = load_role_certifications()
    for reviewer, final in ((LEGACY_PROVIDER, LEGACY_PROVIDER), (PROVIDERS[0], LEGACY_PROVIDER)):
        assert table.require_occupancy({AgentSlot.IMPLEMENTER: PROVIDERS[1], AgentSlot.REVIEWER: reviewer,
                                       AgentSlot.FINAL_REVIEWER: final}, models={AgentSlot.IMPLEMENTER: 'opus', AgentSlot.REVIEWER: 'gemini-3.1-pro-high' if reviewer == LEGACY_PROVIDER else 'gpt-6.1-sol', AgentSlot.FINAL_REVIEWER: 'gemini-3.1-pro-high'})
    for name in ('AGENTS.md', 'CLAUDE.md', 'CODEX.md'):  # allowlist:provider -- documentation guard: root CLI entries
        assert 'per slot' in (ROOT/name).read_text()
    for name in ('Quickstart.md', 'docs/reference/einrichtung.md'):
        text = (ROOT/name).read_text()
        assert text.count('timeout_seconds = 900') == 2
        assert 'je Slot' in text and '20–60 Minuten' in text
    review = (ROOT/'docs/reference/reviewer-certification.md').read_text()
    impl = (ROOT/'docs/reference/implementer-certification.md').read_text()
    assert '`gpt-6.1-sol`/`high`' in review and '`medium`' in review
    assert '**W4 ist nicht gemessen' in impl and '`opus`/`high`' in impl
    assert all(entry.model_family_pattern is None for entry in table.entries if entry.provider == PROVIDERS[0])
