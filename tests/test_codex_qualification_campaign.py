"""Exercise the complete v6 campaign using native adapters and fake processes."""
import copy
import json
from pathlib import Path
import subprocess
from types import SimpleNamespace

import pytest

from scripts import probe_reviewer as probe
from scripts.qualification import blind, campaign, prerequisites, run_format
from scripts.qualification.profiles import LEGACY_CANDIDATE
from tests.test_codex_review_adapter import _entry  # allowlist:provider -- transport: fake package identity

PROTOCOL = probe.ROOT / 'docs/evidence' / 'codex' / 'qualification-protocol-v6.json'  # allowlist:provider -- certification data: frozen candidate protocol
HISTORICAL = probe.qualification_pair(probe._protocol_file(None)).evidence_directory


@pytest.fixture
def fake_campaign(tmp_path, monkeypatch):
    import agent_runtime
    protocol = probe.read_evidence(PROTOCOL)
    pair = probe.qualification_pair(protocol)
    entry = _entry(tmp_path)
    profiles = {}
    for provider in pair.providers:
        binary = tmp_path / provider
        binary.write_text('fake process only; never execute')
        runtime = protocol['provider_runtime'][provider]
        profile = tmp_path / (provider + '.toml')
        profile.write_text(f'live = true\ncommit_sha = "{"c" * 40}"\nbinary = "{binary}"\n'
                           f'model = "{runtime["model"]}"\neffort = "{runtime["effort"]}"\ntimeout_seconds = 600\n')
        profiles[provider] = profile
    timeout = tmp_path / 'timeout.toml'
    timeout.write_text(profiles[pair.candidate].read_text().replace('timeout_seconds = 600', 'timeout_seconds = 1'))
    profiles['timeout'] = timeout
    original = probe._qualification_adapter
    def adapter_factory(*args, **kwargs):
        adapter = original(*args, **kwargs)
        adapter.provider_identity = SimpleNamespace(entry_path=str(entry), kind='verified', digest='fake',
                                                    launch_prefix=(adapter.settings.binary,))
        return adapter
    monkeypatch.setattr(probe, '_qualification_adapter', adapter_factory)
    monkeypatch.setattr(probe, '_current_commit', lambda: 'c' * 40)
    monkeypatch.setattr(probe, '_assert_committed_qualification_code', lambda: None)
    monkeypatch.setattr(agent_runtime, 'verify_agent_capabilities', lambda *a, **kw: None)
    monkeypatch.setattr(agent_runtime, '_bound_launch_command', lambda adapter, command: list(command))
    catalogs = []
    def local(command):
        catalogs.append(command)
        return 0, json.dumps({'models': [{'slug': protocol['provider_runtime'][pair.candidate]['model']}]}), ''
    monkeypatch.setattr(agent_runtime, 'run_local_command', local)
    format_cases = probe.read_evidence(probe.ROOT / 'tests/fixtures/reviewer-format-s6-v1.json')['cases']
    historical_series = probe.read_evidence(HISTORICAL / 'qualification-series-v1.json')
    envelopes = probe.read_evidence(HISTORICAL / 'qualification-envelopes-v1.json.gz')
    raw = {row['call_id']: row['envelope']['envelope'] for row in envelopes['envelopes']}
    qualities = {}
    for row in historical_series['attempts']:
        if row['kind'] == 'quality' and row['status'] == 'success':
            qualities[row['provider'], row['case_id']] = probe.measured_review_result(raw[row['call_id']])
    state = {'kind': 'transport', 'case': 'F2:1', 'provider': pair.candidate, 'mode': 'valid',
             'profiles': profiles, 'pair': pair, 'catalogs': catalogs, 'processes': []}
    def process(adapter, command, stdin, **kwargs):
        state['processes'].append(command)
        if state['kind'] == 'print_timeout':
            raise subprocess.TimeoutExpired(command, 1)
        case = state['case'].split(':')[0]
        if state['kind'] == 'quality':
            historical_provider = LEGACY_CANDIDATE if state['provider'] == pair.candidate else pair.reference
            response = copy.deepcopy(qualities[historical_provider, case])
        elif state['kind'] == 'large_output':
            response = copy.deepcopy(format_cases['F5']['envelope']['structured_output']['result'])
            template = response['new_findings'][0]
            response['new_findings'] = []
            for number in range(1, int(case) + 1):
                finding = copy.deepcopy(template)
                finding.update(finding_id=f'R-{number:02d}', affected_paths=[f'src/boundary_{number:04d}.py'],
                               summary=f'Independent inclusive boundary defect in `src/boundary_{number:04d}.py`.',
                               acceptance_test={'kind': 'prose', 'text': f'Assert the inclusive boundary in `src/boundary_{number:04d}.py`.'})
                response['new_findings'].append(finding)
        else:
            response = copy.deepcopy(format_cases[case]['envelope']['structured_output']['result'])
            if case == 'F6':
                response.update(rule_id='DISCOVERY_OUTPUT_LIMIT', rationale='Discovery capacity is exhausted; partial results cannot be authoritative.')
        response['request_id'] = adapter.invocation.request_id
        if state['provider'] == pair.candidate:
            assert '--output-last-message' in command and 'model_catalog_json=' in ' '.join(command)
            payload = {'result': response} if state['mode'] == 'valid' else None
            adapter.invocation.last_message_file.write_text(json.dumps(payload))
            stdout = '{"type":"turn.completed"}\n'
        else:
            stdout = json.dumps({'is_error': False, 'permission_denials': [], 'structured_output': {'result': response}})
        return subprocess.CompletedProcess(command, 0, stdout, '')
    monkeypatch.setattr(agent_runtime, '_run_agent_process', process)
    return state


@pytest.mark.parametrize('case', probe.EXPECTED_FORMAT)
def test_format_probe_binds_catalog_and_uses_native_fake_process(tmp_path, fake_campaign, case):
    state = fake_campaign
    state['case'] = case + ':1'
    source = tmp_path / 'format-source'
    probe.materialize_format_repo(case, source)
    result = run_format.run_format(case=case, provider=state['pair'].candidate,
        protocol_file=PROTOCOL, profile_file=state['profiles'][state['pair'].candidate],
        source=source, output=tmp_path / 'format-out', live=True)
    assert result['passed'] and state['catalogs'] and len(state['processes']) == 1
    assert '"event":"intent"' in (tmp_path / 'format-out/ledger.jsonl').read_text()


def test_missing_large_last_message_is_recorded_without_attribute_error(tmp_path, fake_campaign, monkeypatch):
    state = fake_campaign
    state.update(kind='large_output', case='128', mode='null')
    source = tmp_path / 'large-source'
    probe.materialize_large_repo(128, source)
    row = probe.run_qualification_call(kind='large_output', case_id='128', provider=state['pair'].candidate,
        series_id='large', call_id='large-128', profile_file=state['profiles'][state['pair'].candidate],
        source_repo=source, output_dir=tmp_path / 'out', protocol_file=PROTOCOL, live=True)
    assert row['status'] == 'technical_rejection' and row['failure_kind'] == 'output'
    assert not row['checks']['writer_and_domain'] and not row['checks']['finding_count']
    assert (tmp_path / 'out/qualification-series-v1.json').is_file()


def test_entire_native_campaign_blind_ratings_evaluation_and_canaries(tmp_path, fake_campaign, monkeypatch, capsys):
    state = fake_campaign
    pair = state['pair']
    evidence = tmp_path / 'campaign'
    def cli(argv):
        monkeypatch.setattr(probe.sys, 'argv', ['probe_reviewer.py', *argv])
        code = probe.main()
        printed = capsys.readouterr().out
        return code, {} if argv[0] == 'prepare-case' else probe.strict_json(printed)
    def invoke(command):
        if command[2] == 'prepare-case':
            cli(command[2:])
            return {}
        state.update(kind=command[3], case=command[4], provider=command[5])
        _, row = cli([*command[2:], '--live'])
        return row
    order = [('transport', pair.candidate), ('large_output', pair.candidate),
             ('print_timeout', pair.candidate), ('quality', pair.candidate), ('quality', pair.reference)]
    for kind, provider in order:
        rows = campaign.run_block(kind=kind, provider=provider, series_id=provider + '-' + kind,
            protocol=PROTOCOL, profile=state['profiles']['timeout' if kind == 'print_timeout' else provider],
            evidence_dir=evidence, source_dir=tmp_path / 'sources',
            cases=probe.qualification_cases(kind, provider, pair), invoke=invoke)
        assert all(all(row['checks'].values()) for row in rows), rows
    series = evidence / 'qualification-series-v1.json'
    envelopes = evidence / 'qualification-envelopes-v1.json'
    output = tmp_path / 'blind'
    report = blind.prepare(protocol_path=PROTOCOL, series_path=series, envelopes_path=envelopes, output_dir=output)
    assert report['responses'] == 12
    raters = probe.qualification_raters(probe.read_evidence(PROTOCOL))
    packet_path = output / 'packets' / (raters[0] + '-packet.json')
    packet = probe.read_evidence(packet_path)
    assert packet == probe.read_evidence(output / 'packets' / (raters[1] + '-packet.json'))
    rating_files = []
    for rater in raters:
        rating = {'schema_version': 'quality-rating-v1', 'rater': rater,
                  'packet_sha256': packet['packet_sha256'], 'judgments': []}
        for entry in packet['responses']:
            defect = entry['ground_truth']['defect']
            count = len(entry['response'].get('new_findings', []))
            row = {'id': entry['id'], 'unfounded_findings': max(0, count - int(defect)), 'invented_critical': False}
            if defect:
                row['defect_found'] = True
            row['reasons'] = {key: 'Synthetic offline judgment of the copied response.' for key in row if key != 'id'}
            rating['judgments'].append(row)
        path = tmp_path / (rater + '.json')
        path.write_text(json.dumps(rating))
        rating_files.append(path)
    quality = tmp_path / 'quality.json'
    code, combined = cli(['combine-quality-ratings', str(packet_path),
        str(output / 'private/mapping.json'), *map(str, rating_files), str(quality),
        '--protocol', str(PROTOCOL), '--reference-special-decision', 'approve_experimental'])
    assert code == 0 and combined['status'] == 'complete'
    decisions = tmp_path / 'operator-decisions.json'
    decisions.write_text(json.dumps({'schema_version': 'operator-decisions-v1', 'decisions': []}))
    code, verdict = cli(['evaluate-qualification', str(series), str(envelopes), '--protocol', str(PROTOCOL),
        '--quality-results', str(quality), '--operator-decisions', str(decisions)])
    assert code == 0 and verdict['qualified_for_canary'] and verdict['size_qualification'] == 'passed'
    for slot, case in (('reviewer', 'F4:1'), ('final_reviewer', 'F5:1')):
        state.update(kind='transport', case=case, provider=pair.candidate)
        result = probe.run_canary_call(slot, profile_file=state['profiles'][pair.candidate],
            output_dir=tmp_path / 'canaries', protocol_file=PROTOCOL, live=True, reference_evidence_dir=evidence)
        assert result['status'] == 'passed'
    assert len(state['processes']) == 29  # 12 + 2 + 1 + 6 + 6 + 2; every provider process is fake.
