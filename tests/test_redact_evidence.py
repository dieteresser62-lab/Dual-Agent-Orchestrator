"""Public export invariants and the explicit experimental role occupancy."""
import json
from pathlib import Path

import pytest

from scripts import probe_reviewer as probe
from scripts.qualification import canary, prerequisites, redact_evidence as redact
from scripts.qualification.profiles import BOUNDARY_IMPLEMENTER, BOUNDARY_REVIEWER
from native_provider_schema import provider_capability
from agent_roles import AgentRoleName, AgentSlot
from role_certification import load_role_certifications, CertificationError, _validate_role_canaries


ROOT = probe.ROOT
PROVIDERS = tuple(provider_capability(cap)['provider'] for cap in (BOUNDARY_REVIEWER, BOUNDARY_IMPLEMENTER))


def redactor():
    return redact.Redactor('/home/test-account', ('test-account',), ('test-machine',))


def test_runtime_patterns_replace_values_and_keys_without_literal_accounts():
    source = {'/home/test-account/private': ['/home/other-account/repo', 'test-account@test-machine',
                'test-account denied /var/tmp/private/x', '/tmp/fixture'],
              'checks': {'secret': False}, 'status': 'failed', 'count': 512}
    public, changes = redactor().transform(source)
    assert '<HOME>/private' in public and '/home/<user>/repo' in public['<HOME>/private']
    assert '<email>' in public['<HOME>/private']
    assert public['checks'] == source['checks'] and public['status'] == source['status']
    assert public['count'] == 512
    assert any(item['field_type'] == 'object-key' for item in changes)
    redactor().check(public)
    redactor().check(changes)


def test_deterministic_export_binds_sources_and_preserves_outcomes(tmp_path):
    source = tmp_path / 'private.json'
    source.write_text(json.dumps({'status': 'failed', 'checks': {'native': False}, 'count': 512,
                                 'rationale': 'test-account@test-machine: /home/test-account/x'}))
    original = source.read_bytes()
    bundle = redact.Bundle(redactor())
    bundle.add('report.json.gz', source, 'private-report')
    output = tmp_path / 'public'
    first = bundle.finish(output)
    before = {path.name: path.read_bytes() for path in output.iterdir()}
    assert bundle.finish(output) == first
    assert before == {path.name: path.read_bytes() for path in output.iterdir()}
    entry = first['files'][0]
    assert entry['source_sha256'] == probe.sha(original)
    assert entry['derived_sha256'] == probe.sha(before['report.json.gz']) != entry['source_sha256']
    assert source.read_bytes() == original
    assert redact.verify(output, redactor=redactor()) == first
    public = probe.read_evidence(output / 'report.json.gz')
    assert public['checks'] == {'native': False} and public['count'] == 512 and public['status'] == 'failed'
    (output / 'report.json.gz').write_bytes(before['report.json.gz'] + b'changed')
    with pytest.raises(ValueError, match='digest'):
        redact.verify(output, redactor=redactor())


@pytest.mark.parametrize('change', ('status', 'count', 'check'))
def test_export_rejects_changed_judgment_count_or_check_before_writing(tmp_path, change):
    source = tmp_path / 'original.json'
    source.write_text(json.dumps({'status': 'failed', 'count': 512, 'checks': {'safe': False}}))
    bundle = redact.Bundle(redactor())
    document = bundle.add('report.json', source, 'source')
    if change == 'status': document['status'] = 'passed'
    elif change == 'count': document['count'] = 128
    else: document['checks']['safe'] = True
    with pytest.raises(ValueError, match='undeclared change'):
        bundle.finish(tmp_path / 'public')
    assert not (tmp_path / 'public').exists()


def test_residual_personal_pattern_and_key_collisions_fail_closed(tmp_path):
    class BrokenRedactor(redact.Redactor):
        def text(self, value): return value, []
    source = tmp_path / 'source.json'
    source.write_text(json.dumps({'text': '/home/test-account/private'}))
    bundle = redact.Bundle(BrokenRedactor('/home/test-account', ('test-account',), ('test-machine',)))
    bundle.add('output.json', source, 'source')
    with pytest.raises(ValueError, match='personal pattern'):
        bundle.finish(tmp_path / 'public')
    with pytest.raises(ValueError, match='merges'):
        redactor().transform({'/home/test-account/x': 1, '<HOME>/x': 2})


def test_rebound_canary_digest_is_accepted_after_string_redaction(tmp_path):
    provider = PROVIDERS[1]
    profile = {'provider': provider, 'model': 'opus', 'host': 'test-machine'}
    raw = {'request_id': 'bound-canary', 'status': 'success', 'evidence': {
        'request_document': {'request_id': 'bound-canary', 'hint': '/home/test-account/repo'},
        'writer_schema': {'type': 'object'}, 'result_document': {'ready': True}}}
    checks = dict.fromkeys(('writer', 'domain', 'effective_rights', 'isolation_postcheck', 'no_denials'), True)
    item = canary.role_document('implementer', provider, 'I1', profile, raw, checks, 'fixture')
    source = tmp_path / 'canary.json'
    source.write_text(json.dumps({'schema_version': 'role-canary-v1', 'status': 'passed', 'slots': {'implementer': item}}))
    bundle = redact.Bundle(redactor())
    public = bundle.add('role-canary-v1.json', source, 'private-canary')
    redact.bind_canary(bundle, 'role-canary-v1.json')
    bundle.finish(tmp_path / 'public')
    _validate_role_canaries(public, provider=provider, role=AgentRoleName.IMPLEMENTER,
                           slot=AgentSlot.IMPLEMENTER, model_family_pattern=None)
    assert public['slots']['implementer']['proof']['checks'] == checks


def test_nested_explicit_json_bindings_are_recomputed_but_byte_commitments_remain(tmp_path):
    identity = {'entry_path': '/home/test-account/bin/tool', 'sha256': 'a' * 64}
    profile = {'binary_identity': identity, 'binary_identity_sha256': probe.digest(identity)}
    source = tmp_path / 'source.json'
    source.write_text(json.dumps({'profile': profile, 'profile_sha256': probe.digest(profile),
                                 'record_id': 'private-original-record', 'checks': {'safe': True}}))
    bundle = redact.Bundle(redactor())
    public = bundle.add('report.json', source, 'source')
    bundle.finish(tmp_path / 'public')
    assert public['profile']['binary_identity_sha256'] == probe.digest(public['profile']['binary_identity'])
    assert public['profile_sha256'] == probe.digest(public['profile']) != probe.digest(profile)
    assert public['profile']['binary_identity']['sha256'] == 'a' * 64
    assert public['record_id'] == 'private-original-record' and public['checks'] == {'safe': True}


@pytest.mark.parametrize('path', ('../private.json', '/tmp/private.json'))
def test_public_manifest_rejects_external_references(tmp_path, path):
    with pytest.raises(ValueError, match='bundle-relative'):
        redact.public_path(tmp_path, path)


def test_public_measurements_and_promotion_are_digest_bound():
    table = load_role_certifications()
    for provider in PROVIDERS:
        directory = ROOT / 'docs/evidence' / provider
        manifest = redact.verify(directory)
        for file in manifest['files']:
            assert file['source_sha256'] != file['derived_sha256']
        for entry in table.entries:
            if entry.provider != provider or entry.status != 'experimental': continue
            proof = probe.read_evidence(ROOT / entry.evidence_path)
            claims = '\n'.join(item['proves'] for item in proof['slots'][entry.slot.value])
            names = ['redaction-manifest-v1.json', 'role-canary-v1.json', 'phase0-results.json']
            names += ['qualification-series-v1.json', 'qualification-envelopes-v1.json.gz', 'quality-results-v1.json', 'operator-decisions-v1.json'] if provider == PROVIDERS[0] else ['implementer-package-report-v1.json']
            for name in names:
                assert f'docs/evidence/{provider}/{name} sha256={probe.sha((directory/name).read_bytes())}' in claims
    reviewer = ROOT / 'docs/evidence' / PROVIDERS[0]
    protocol = reviewer / 'qualification-protocol-v6.json'
    verdict = probe.qualification_summary(probe.read_evidence(reviewer/'qualification-series-v1.json'),
        probe.read_evidence(reviewer/'qualification-envelopes-v1.json.gz'), probe.read_evidence(protocol),
        probe.read_evidence(reviewer/'quality-results-v1.json'), probe.read_evidence(reviewer/'operator-decisions-v1.json'))
    assert verdict['qualified_for_canary'] is True and verdict['size_qualification'] == 'operator_override'
    assert verdict['size_override']['failed_case'] == '512'
    prerequisites.verify(protocol, reviewer/'phase0-results.json')
    implementer = ROOT / 'docs/evidence' / PROVIDERS[1]
    package = probe.read_evidence(implementer/'implementer-package-report-v1.json')
    from scripts.qualification.implementer_package.evaluate import summarize
    assert summarize(package['tasks'], mode=package['mode']) == package
    assert package['verdict'] == 'passed' and package['passed_tasks'] == 5 and package['absolute_errors'] == []
    phase = probe.read_evidence(implementer/'phase0-results.json')
    assert phase['safe_cases'] == 7 and phase['passed_cases'] == 5
    assert phase['not_measured_cases'] == ['W4']
    decisions = probe.read_evidence(implementer/'operator-decisions-v1.json')
    assert phase['operator_decision']['sha256'] == probe.sha((implementer/'operator-decisions-v1.json').read_bytes())
    assert decisions['decisions'][0]['finding_cases'] == ['W2', 'W4', 'W8']
    for entry in phase['protection']:
        path = redact.public_path(implementer, entry['path'])
        report = probe.read_evidence(path)
        assert probe.sha(path.read_bytes()) == entry['sha256'] and report['passed'] == entry['passed']
    for case in ['W2', 'W4', 'W8']:
        assert probe.read_evidence(implementer/'phase0'/f'{case}.json')['passed'] is False


def test_experimental_topology_starts_and_standard_remains_default(tmp_path):
    from cli import parse_args
    from agent_adapters import build_slot_agent_registry
    from agent_config import DEFAULT_ROLE_PROFILES
    table = load_role_certifications()
    selected = {AgentSlot.IMPLEMENTER: PROVIDERS[1], AgentSlot.REVIEWER: PROVIDERS[0], AgentSlot.FINAL_REVIEWER: PROVIDERS[0]}
    assert all(entry.status == 'experimental' for entry in table.require_occupancy(selected).values())
    config = tmp_path / 'orchestrator.toml'
    config.write_text('[roles]\nimplementer="impl"\nreviewer="review"\nfinal_reviewer="review"\n'
        f'[agent_profiles.impl]\nprovider="{PROVIDERS[1]}"\nmodel="opus"\neffort="high"\ntimeout_seconds=600\n'
        f'[agent_profiles.review]\nprovider="{PROVIDERS[0]}"\nmodel="sol"\neffort="high"\ntimeout_seconds=600\n')
    args = parse_args(['--config', str(config)], cwd=tmp_path, environ={})
    registry = build_slot_agent_registry(args.slot_settings)
    assert set(registry) == {'implementer', 'reviewer', 'final_reviewer'}
    defaults = parse_args([], cwd=ROOT, environ={})
    assert DEFAULT_ROLE_PROFILES['implementer'] == 'implementation'
    assert defaults.slot_settings['implementer'].name == PROVIDERS[0]
    assert defaults.slot_settings['reviewer'].name == defaults.slot_settings['final_reviewer'].name == PROVIDERS[1]
    for bad in ({**selected, AgentSlot.IMPLEMENTER: PROVIDERS[0]}, {**selected, AgentSlot.REVIEWER: PROVIDERS[1]}):
        with pytest.raises(CertificationError, match='manufacturers must differ'):
            table.require_occupancy(bad)


def test_public_evidence_has_no_personal_or_account_patterns():
    red = redact.Redactor.runtime()
    for path in (ROOT/'docs/evidence').rglob('*'):
        if not path.is_file() or path.suffix not in {'.json', '.gz'}: continue
        red.check(probe.read_evidence(path))


def test_frozen_prerequisites_and_legacy_source_commitments_remain_bound():
    legacy = probe.qualification_pair({'schema_version': 'qualification-protocol-v5'}).evidence_directory
    original_digests = {
        'phase-0-v1.json': '51218e146b13f922b539fada2084718fb10d5d345471caaf674419ba5f9665c7',
        'poc-reference-v1.json': 'b2b55390e3fdcf0ca75118f9fbd47c6ae10677e1ffc128bb1c6a25eca6b1628b',
        'isolation-v1.json': '33ce921c70ed9c66a535471bfc66d47446db960eecb43e46e275026ec0671a6c',
    }
    manifest = redact.verify(legacy)
    entries = {item['path']: item for item in manifest['files']}
    for name, digest in original_digests.items():
        assert entries[name]['source_sha256'] == digest
        assert probe.sha((legacy / name).read_bytes()) == entries[name]['derived_sha256']
        assert (entries[name]['derived_sha256'] == digest) is (name == 'isolation-v1.json')
    reviewer = ROOT / 'docs/evidence' / PROVIDERS[0]
    assert probe.sha((reviewer / 'phase-0-v1.json').read_bytes()) == 'f241302d273095eb1b3ba4dad9a64da4a1ad09df031b8bc022184c35c5b7eaac'
    assert probe.sha((reviewer / 'qualification-protocol-v6.json').read_bytes()) == 'fa1e65f4734b230657f685921d965a5fe66223458c4d5a80e07a60a4c85c6222'


def test_subscription_observations_removed_but_operational_usage_retained():
    source = {'schema_version': 'qualification-series-v1', 'attempts': [{
        'quota': {'five_hour_remaining_percent': 42, 'weekly_refresh_in': 'private', 'note': 'account observation'},
        'usage': {'tokens': 7}, 'session_id': 'random-session', 'service_tier': 'standard', 'checks': {'native': True}}]}
    public, changes = redactor().transform(source)
    assert public['attempts'][0] == {**source['attempts'][0], 'quota': {}}
    assert all(item['field_type'] == 'removed-field' and item['rules'] == ['account'] for item in changes)
    redactor().check(public)
    phase = {'quota': {'total_tokens_agy': 7, 'daily_cli_total_tokens': {'date': 7},
        'subscription_quota_measured': False, 'usage_source': 'CLI usage', 'daily_subscription_limit': 'private'}}
    assert redactor().transform(phase)[0]['quota'] == {key: value for key, value in phase['quota'].items()
                                                    if key != 'daily_subscription_limit'}


def test_legacy_export_preserves_qualification_and_rebinds_dependencies(tmp_path):
    import shutil
    legacy = probe.qualification_pair({'schema_version': 'qualification-protocol-v5'}).evidence_directory
    private = tmp_path / 'private'
    shutil.copytree(legacy, private)
    series = probe.read_evidence(private / 'qualification-series-v1.json')
    series['attempts'][0]['quota'] = {'five_hour_remaining_percent': 17, 'five_hour_reset_in': 'private'}
    (private / 'qualification-series-v1.json').write_bytes(redact.encoded(series))
    canary = probe.read_evidence(private / 'canary-v1.json')
    canary['shared_evidence']['qualification']['sha256'] = probe.sha((private / 'qualification-series-v1.json').read_bytes())
    (private / 'canary-v1.json').write_bytes(redact.encoded(canary))
    before = {path.name: path.read_bytes() for path in private.iterdir()}
    protocol = probe.read_evidence(private / 'qualification-protocol-v5.json')
    envelopes = probe.read_evidence(private / 'qualification-envelopes-v1.json.gz')
    quality = probe.read_evidence(private / 'quality-results-v1.json')
    decisions = probe.read_evidence(private / 'operator-decisions-v1.json')
    verdict = probe.qualification_summary(series, envelopes, protocol, quality, decisions)
    output = tmp_path / 'public'
    first = redact.export_historical_qualification(private, output, redactor())
    assert redact.export_historical_qualification(private, output, redactor()) == first
    assert {path.name: path.read_bytes() for path in private.iterdir()} == before
    assert probe.qualification_summary(probe.read_evidence(output / 'qualification-series-v1.json'),
        envelopes, probe.read_evidence(output / 'qualification-protocol-v5.json'), quality, decisions) == verdict
    for name in ('phase-0-v1.json', 'qualification-series-v1.json'):
        assert probe.read_evidence(output / name) == probe.read_evidence(legacy / name)
    for name in ('canary-v1.json', 'isolation-v1.json', *(f'qualification-protocol-v{i}.json' for i in range(1, 6))):
        assert (output / name).read_bytes() == before[name]


def test_redacted_fixtures_preserve_envelopes_and_trace_semantics():
    folder = ROOT / 'tests/fixtures'
    manifest = redact.verify(folder)
    assert {row['path'] for row in manifest['files']} == {
        'antigravity-envelopes-v1.json', 'phase0-traces/s1/codex-P2.json'}  # allowlist:provider -- transport: recorded fake fixtures
    fixture = probe.read_evidence(folder / manifest['files'][0]['path'])
    assert all(row[key].startswith('/fixtures/agy/') for row in fixture['cases'] for key in ('source', 'stderr_source'))


@pytest.mark.parametrize('indent', (None, 2, '\t'))
def test_minimal_export_preserves_original_format_and_key_order(indent):
    source = {'z': '/var/tmp/dao-agy-review-<uid>', 'path': '/home/test-account/bin',
              'a': [{'path': '/tmp/fixture'}, {'path': '/usr/lib/library'}]}
    raw = (json.dumps(source, indent=indent) + '\n').encode()
    public, changes = redactor().transform(source)
    assert public == {**source, 'path': '<HOME>/bin'}
    assert changes == [{'path': '/path', 'field_type': 'string', 'rules': ['home']}]
    assert redact.preserved_json(raw, public) == raw.replace(b'/home/test-account/bin', b'<HOME>/bin')


@pytest.mark.parametrize('position', ('first', 'middle', 'last'))
def test_minimal_export_deletes_fields_without_reformatting_neighbors(position):
    members = [('a', 1), ('z', {'nested': True})]
    members.insert({'first': 0, 'middle': 1, 'last': 2}[position], ('account_id', 'private'))
    raw = (json.dumps(dict(members), indent=2) + '\n').encode()
    public, _ = redactor().transform(json.loads(raw))
    output = redact.preserved_json(raw, public)
    assert json.loads(output) == public
    assert b'  "z": {\n    "nested": true\n  }' in output
    assert b'  "a": 1' in output and b'account_id' not in output


@pytest.mark.parametrize(('filename', 'digest'), (
    ('qualification-protocol-v1.json', '552864ac16678449e0542d69528e26a6d723a224e8f9c2b555f089ff4c58d26b'),
    ('qualification-protocol-v2.json', 'eec62a336bfb562a255349cf18c87c72e76d107d67ddf7da1e1b124b796e1e95'),
    ('qualification-protocol-v3.json', '3ceebb07569254812093681d692c4f56b400b345b4ff8b4bcd29c8ee97406f12'),
    ('qualification-protocol-v4.json', 'ea842c09175dddc30ab191d29cb14e992edcfe80aba3586b553007d8409414ee'),
    ('qualification-protocol-v5.json', 'e421988505eba7666b275459b7ecd2cd3e63e12569650262187abaa255176f35'),
    ('isolation-v1.json', '33ce921c70ed9c66a535471bfc66d47446db960eecb43e46e275026ec0671a6c'),
    ('canary-v1.json', '6d1e20e8b11d52e3d3f8d8e26e64b06b5d3dd7c18d90645b01c14bffb8c912d2'),
))
def test_preregistered_and_reference_documents_keep_exact_0f1d63e_bytes(filename, digest):
    directory = probe.qualification_pair({'schema_version': 'qualification-protocol-v5'}).evidence_directory
    assert probe.sha((directory / filename).read_bytes()) == digest
