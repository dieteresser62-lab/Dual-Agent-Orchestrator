"""Manifest-bound Phase-0 completion is a separate, provider-free artifact."""
import json
from pathlib import Path

import pytest

from scripts import probe_reviewer as probe
from scripts.qualification import campaign, prerequisites

PROTOCOL = probe.ROOT / 'docs/evidence' / 'codex' / 'qualification-protocol-v6.json'  # allowlist:provider -- certification data: candidate prerequisite manifest


def reports(root):
    manifest = probe.read_evidence(probe.qualification_pair(probe.read_evidence(PROTOCOL)).evidence_directory / 'phase-0-v1.json')
    groups = []
    for kind, ids in (('protection', manifest['cases']), ('format', manifest['format_cases'])):
        paths = []
        for case in ids:
            path = root / (case + '.json')
            path.write_text(json.dumps({'case': case, 'passed': True, 'checks': {'control': True},
                'profile' if kind == 'protection' else 'capability': manifest['profile']}))
            paths.append(path)
        groups.append(paths)
    return groups


def test_completion_binds_all_report_bytes_without_resealing_manifest(tmp_path, monkeypatch):
    protocol = probe.read_evidence(PROTOCOL)
    manifest = probe.qualification_pair(protocol).evidence_directory / 'phase-0-v1.json'
    before = manifest.read_bytes()
    protection, formats = reports(tmp_path)
    output = tmp_path / 'completion.json'
    monkeypatch.setattr(prerequisites.sys, 'argv', ['prerequisites.py', '--protocol', str(PROTOCOL),
        *[arg for path in protection for arg in ('--protection', str(path))],
        *[arg for path in formats for arg in ('--format', str(path))], '--out', str(output)])
    assert prerequisites.main() == 0
    prerequisites.verify(PROTOCOL, output)
    record = probe.read_evidence(output)
    assert record['manifest_sha256'] == probe.sha(before) == protocol['phase0_sha256']
    assert record['counts_as_sample'] is False and manifest.read_bytes() == before
    with pytest.raises(FileExistsError):
        prerequisites.record(PROTOCOL, protection, formats, output)


@pytest.mark.parametrize('change', ['missing', 'duplicate', 'failed', 'unknown', 'profile'])
def test_incomplete_or_wrong_probes_cannot_be_recorded(tmp_path, change):
    protection, formats = reports(tmp_path)
    if change == 'missing':
        protection.pop()
    elif change == 'duplicate':
        protection[-1] = protection[0]
    else:
        path = protection[0]
        report = probe.read_evidence(path)
        if change == 'failed':
            report['passed'] = False
        elif change == 'unknown':
            report['checks']['control'] = 'unknown'
        else:
            report['profile'] = 'other-profile'
        path.write_text(json.dumps(report))
    with pytest.raises(ValueError):
        prerequisites.record(PROTOCOL, protection, formats, tmp_path / 'completion.json')


@pytest.mark.parametrize('change', ['report', 'manifest-binding', 'protocol-binding', 'missing-case'])
def test_completion_rechecks_original_artifacts_and_binding(tmp_path, change):
    protection, formats = reports(tmp_path)
    output = tmp_path / 'completion.json'
    record = prerequisites.record(PROTOCOL, protection, formats, output)
    if change == 'report':
        protection[0].write_text(protection[0].read_text() + '\n')
    elif change == 'manifest-binding':
        record['manifest_sha256'] = '0' * 64
    elif change == 'protocol-binding':
        record['protocol_sha256'] = '0' * 64
    else:
        record['format'].pop()
    output.write_text(json.dumps(record))
    with pytest.raises(ValueError):
        prerequisites.verify(PROTOCOL, output)


def test_pending_manifest_stops_campaign_before_any_process(tmp_path, monkeypatch):
    calls = []
    monkeypatch.setattr(campaign.subprocess, 'run', lambda *a, **kw: calls.append(a))
    pair = probe.qualification_pair(probe.read_evidence(PROTOCOL))
    with pytest.raises(PermissionError, match='--phase0-results'):
        campaign.run_block(kind='transport', provider=pair.candidate, series_id='preflight',
            profile=tmp_path / 'unused', protocol=PROTOCOL, evidence_dir=tmp_path / 'out',
            source_dir=tmp_path / 'sources', cases=probe.qualification_cases('transport', pair.candidate, pair), live=True)
    assert calls == [] and not (tmp_path / 'sources').exists()
