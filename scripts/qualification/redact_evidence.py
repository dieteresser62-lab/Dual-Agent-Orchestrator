"""Export private qualification evidence with deterministic public provenance.

Only public JSON references are rebound. Git, binary-byte and projected record
IDs remain commitments to the private originals, not resumable public records.
"""
from __future__ import annotations

import argparse
import getpass
import gzip
import io
import json
import os
from pathlib import Path, PurePosixPath
import re
import socket
import sys

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / 'src'))
from scripts import probe_reviewer as probe
from scripts.qualification.profiles import BOUNDARY_IMPLEMENTER, BOUNDARY_REVIEWER
from native_provider_schema import provider_capability

RULES = {
    'home': 'Replace the runtime home by <HOME>; other /home/<name> prefixes by /home/<user>.',
    'username': 'Replace runtime account names at token boundaries by <user>.',
    'hostname': 'Replace runtime host names at token boundaries by <host>.',
    'temporary': 'Replace private temporary-directory prefixes by <TMP>/ and <VAR_TMP>/.',
    'report-reference': 'Use bundle-relative report paths instead of private absolute paths.',
    'digest': 'Recompute a declared public JSON or file binding after redaction.',
}
SHA = re.compile(r'[0-9a-f]{64}\Z')
PLACEHOLDERS = re.compile(r'<(?:HOME|user|host|TMP|VAR_TMP)>')


def encoded(document):
    # A distinct serialization makes even an otherwise unchanged export a new
    # byte artifact; no timestamp, source filename or host-dependent gzip header.
    return (json.dumps(document, ensure_ascii=False, sort_keys=True, indent=1) + '\n').encode()


def bytes_for(path, document):
    content = encoded(document)
    if path.suffix == '.gz':
        buffer = io.BytesIO()
        with gzip.GzipFile(fileobj=buffer, mode='wb', filename='', mtime=0) as stream:
            stream.write(content)
        return buffer.getvalue()
    return content


def pointer(parts):
    return '/' + '/'.join(str(part).replace('~', '~0').replace('/', '~1') for part in parts)


class Redactor:
    def __init__(self, home, usernames, hostnames):
        self.rules = [('home', re.compile(re.escape(str(home)) + r'(?=$|[/\\\s"\'])'), '<HOME>'),
                      ('home', re.compile(r'/home/[^/<>\s"\'\\]+'), '/home/<user>')]
        for rule, names, placeholder in (('username', usernames, '<user>'), ('hostname', hostnames, '<host>')):
            for name in sorted(set(names) - {''}, key=lambda item: (-len(item), item)):
                self.rules.append((rule, re.compile(r'(?<![\w<])' + re.escape(name) + r'(?![\w>])'), placeholder))
        self.rules += [('temporary', re.compile(r'/var/tmp/'), '<VAR_TMP>/'),
                       ('temporary', re.compile(r'/tmp/'), '<TMP>/')]

    @classmethod
    def runtime(cls):
        return cls(Path.home(), (getpass.getuser(), Path.home().name,
                   os.environ.get('USER', ''), os.environ.get('LOGNAME', '')),
                   (socket.gethostname(), os.uname().nodename))

    def text(self, value):
        rules = []
        for rule, pattern, replacement in self.rules:
            value, count = pattern.subn(replacement, value)
            if count and rule not in rules:
                rules.append(rule)
        return value, rules

    def transform(self, value):
        changes = []
        def visit(item, parts):
            if isinstance(item, dict):
                output = {}
                for key, child in item.items():
                    new_key, rules = self.text(key)
                    if new_key in output:
                        raise ValueError('redaction merges distinct object keys')
                    if rules:
                        changes.append({'path': pointer((*parts, new_key)), 'field_type': 'object-key', 'rules': rules})
                    output[new_key] = visit(child, (*parts, new_key))
                return output
            if isinstance(item, list):
                return [visit(child, (*parts, index)) for index, child in enumerate(item)]
            if isinstance(item, str):
                output, rules = self.text(item)
                if rules:
                    changes.append({'path': pointer(parts), 'field_type': 'string', 'rules': rules})
                return output
            return item
        return visit(value, ()), changes

    def check(self, value):
        text = PLACEHOLDERS.sub('<MASK>', probe.canonical(value))
        if any(pattern.search(text) for _, pattern, _ in self.rules):
            raise ValueError('personal pattern remains in exported evidence')


OUTCOME_FIELDS = frozenset({'status', 'passed', 'verdict', 'decision', 'checks', 'judgments',
    'absolute_errors', 'passed_tasks', 'failure_kind', 'failure_reasons', 'rule_id', 'completion',
    'task', 'case', 'case_id', 'provider', 'role', 'slot', 'kind', 'result_type'})


def outcomes(document):
    """Keep every count/bool/null and typed outcome, including failed cases."""
    result = {}
    def visit(value, parts=(), selected=False):
        if isinstance(value, dict):
            for key, item in value.items():
                visit(item, (*parts, key), selected or key in OUTCOME_FIELDS)
        elif isinstance(value, list):
            for index, item in enumerate(value):
                visit(item, (*parts, index), selected)
        elif not isinstance(value, str) or selected:
            # Personal prose in judgments may be replaced. Its preservation is
            # checked separately against the exact declared string transform.
            if not isinstance(value, str) or (parts and parts[-1] in OUTCOME_FIELDS):
                result[pointer(parts)] = value
    visit(document)
    return result


class Bundle:
    def __init__(self, redactor):
        self.redactor = redactor
        self.jobs = {}
        self.generated = {}

    def add(self, name, source, source_id):
        content = source.read_bytes()
        original = probe.read_evidence(source)
        document, changes = self.redactor.transform(original)
        self.jobs[name] = {'source_id': source_id, 'source_sha256': probe.sha(content),
            'original': original, 'document': document, 'changes': changes, 'source': source}
        self.bind_embedded(name, original, document)
        return document

    def bind_embedded(self, name, original, public, parts=()):
        """Rebind explicit canonical-JSON siblings, not opaque source commitments."""
        if isinstance(original, dict):
            for key, value in original.items():
                public_key, _ = self.redactor.text(key)
                self.bind_embedded(name, value, public[public_key], (*parts, public_key))
            for key, value in original.items():
                if not key.endswith('_sha256') or key[:-7] not in original:
                    continue
                target = original[key[:-7]]
                if not isinstance(target, (dict, list)):
                    continue
                # Native provider identities use ensure_ascii=True; canary and
                # qualification JSON bindings use ensure_ascii=False.
                for ascii_only in (False, True):
                    digest = lambda item: probe.sha(json.dumps(item, sort_keys=True,
                        separators=(',', ':'), ensure_ascii=ascii_only).encode())
                    if digest(target) == value:
                        public_target, _ = self.redactor.text(key[:-7])
                        self.bind(name, (*parts, key), digest(public[public_target]))
                        break
        elif isinstance(original, list):
            for index, item in enumerate(original):
                self.bind_embedded(name, item, public[index], (*parts, index))

    def bind(self, name, parts, value, rule='digest'):
        job = self.jobs[name]
        parent = job['document']
        for part in parts[:-1]:
            parent = parent[part]
        if parent[parts[-1]] != value:
            parent[parts[-1]] = value
            job['changes'].append({'path': pointer(parts), 'field_type': 'string', 'rules': [rule]})

    def file_sha(self, name):
        return probe.sha(bytes_for(Path(name), self.jobs[name]['document']))

    def finish(self, folder):
        files = []
        outputs = {}
        for name, job in sorted(self.jobs.items()):
            self.redactor.check(job['document'])
            expected, _ = self.redactor.transform(job['original'])
            for change in job['changes']:
                if change['rules'] not in (['digest'], ['report-reference']):
                    continue
                parts = change['path'].lstrip('/').split('/')
                before, after = expected, job['document']
                for raw in parts[:-1]:
                    part = int(raw) if isinstance(before, list) else raw.replace('~1', '/').replace('~0', '~')
                    before, after = before[part], after[part]
                raw = parts[-1]
                part = int(raw) if isinstance(before, list) else raw.replace('~1', '/').replace('~0', '~')
                before[part] = after[part]
            if expected != job['document']:
                raise ValueError('undeclared change in exported evidence: ' + name)
            normalized_original, _ = self.redactor.transform(job['original'])
            if outcomes(normalized_original) != outcomes(job['document']):
                raise ValueError('redaction changed an outcome or count: ' + name)
            content = bytes_for(Path(name), job['document'])
            outputs[name] = content
            files.append({'path': name, 'source_id': job['source_id'],
                'source_sha256': job['source_sha256'], 'derived_sha256': probe.sha(content),
                'outcomes_sha256': probe.digest(outcomes(job['document'])), 'changes': job['changes']})
            if probe.sha(job['source'].read_bytes()) != job['source_sha256']:
                raise ValueError('private original changed during export')
        generated = []
        for name, document in sorted(self.generated.items()):
            self.redactor.check(document)
            outputs[name] = bytes_for(Path(name), document)
            generated.append({'path': name, 'sha256': probe.sha(outputs[name]),
                              'rule': 'Aggregate preserved case checks and explicit operator decisions.'})
        manifest = {'schema_version': 'redaction-manifest-v1', 'rules': RULES,
            'files': files, 'generated_files': generated,
            'source_commitments': 'Git/blob/record IDs and byte digests still identify private source evidence; these public report projections are not resume artifacts.'}
        self.redactor.check(manifest)
        outputs['redaction-manifest-v1.json'] = encoded(manifest)
        # Check all collisions before any mutation; reruns of identical inputs
        # leave every byte unchanged and refuse to overwrite different evidence.
        for name, content in outputs.items():
            target = public_path(folder, name)
            if target.exists() and target.read_bytes() != content:
                raise FileExistsError('public evidence differs: ' + name)
        for name, content in outputs.items():
            target = public_path(folder, name)
            if not target.exists():
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(content)
        verify(folder, redactor=self.redactor)
        return manifest


def public_path(folder, name):
    path = PurePosixPath(name)
    if path.is_absolute() or not path.parts or '..' in path.parts or str(path) != name:
        raise ValueError('evidence path must be bundle-relative')
    target = folder / name
    if target.is_symlink() or any(parent.is_symlink() for parent in target.parents):
        raise ValueError('public evidence path is a symlink')
    return target


def verify(folder, *, redactor=None):
    redactor = redactor or Redactor.runtime()
    manifest = probe.read_evidence(folder / 'redaction-manifest-v1.json')
    if manifest.get('schema_version') != 'redaction-manifest-v1' or manifest.get('rules') != RULES:
        raise ValueError('unknown redaction manifest')
    redactor.check(manifest)
    seen = set()
    for item in manifest['files'] + manifest['generated_files']:
        name = item['path']
        if name in seen:
            raise ValueError('duplicate exported file')
        seen.add(name)
        path = public_path(folder, name)
        if probe.sha(path.read_bytes()) != item.get('derived_sha256', item.get('sha256')):
            raise ValueError('derived evidence digest differs: ' + name)
        document = probe.read_evidence(path)
        redactor.check(document)
        if 'source_sha256' in item:
            if not SHA.fullmatch(item['source_sha256']) or not item['source_id']:
                raise ValueError('original evidence commitment is invalid')
            if item['outcomes_sha256'] != probe.digest(outcomes(document)):
                raise ValueError('exported outcomes differ')
            for change in item['changes']:
                if change['field_type'] not in {'string', 'object-key'} or not set(change['rules']) <= RULES.keys():
                    raise ValueError('redaction rule is invalid')
    return manifest


def bind_canary(bundle, name):
    document = bundle.jobs[name]['document']
    for slot, item in document['slots'].items():
        proof = item['proof']
        base = ('slots', slot, 'proof')
        evidence = proof['raw']['evidence']
        for key, value in (('request_sha256', evidence['request_document']), ('writer_sha256', evidence['writer_schema']),
                           ('profile_sha256', proof['profile']), ('raw_sha256', proof['raw'])):
            bundle.bind(name, (*base, key), probe.digest(value))


def export_reviewer(evidence, completion, canary, output, redactor):
    bundle = Bundle(redactor)
    for name in ('qualification-series-v1.json', 'quality-results-v1.json'):
        bundle.add(name, evidence / name, name)
    envelopes_name = 'qualification-envelopes-v1.json.gz'
    source = evidence / 'qualification-envelopes-v1.json'
    if not source.exists():
        source = source.with_suffix('.json.gz')
    envelopes = bundle.add(envelopes_name, source, 'qualification-envelopes-v1')
    by_call = {item['call_id']: item['envelope'] for item in envelopes['envelopes']}
    for index, row in enumerate(bundle.jobs['qualification-series-v1.json']['document']['attempts']):
        original = bundle.jobs['qualification-series-v1.json']['original']['attempts'][index]
        source_envelope = next(item['envelope'] for item in bundle.jobs[envelopes_name]['original']['envelopes'] if item['call_id'] == row['call_id'])
        if original['envelope_sha256'] != probe.digest(source_envelope):
            raise ValueError('original qualification envelope binding differs')
        bundle.bind('qualification-series-v1.json', ('attempts', index, 'envelope_sha256'), probe.digest(by_call[row['call_id']]))
    bundle.add('phase0-results.json', completion, 'reviewer-phase0-completion')
    original = bundle.jobs['phase0-results.json']['original']
    for kind in ('protection', 'format'):
        for index, entry in enumerate(original[kind]):
            source = Path(entry['path'])
            if not source.is_absolute():
                source = completion.parent / source
            if probe.sha(source.read_bytes()) != entry['sha256']:
                raise ValueError('original Phase-0 report binding differs')
            name = 'phase0/' + entry['case'] + '.json'
            bundle.add(name, source, 'reviewer-phase0-' + entry['case'])
            bundle.bind('phase0-results.json', (kind, index, 'path'), name, 'report-reference')
            bundle.bind('phase0-results.json', (kind, index, 'sha256'), bundle.file_sha(name))
    bundle.add('role-canary-v1.json', canary, 'reviewer-role-canary')
    bind_canary(bundle, 'role-canary-v1.json')
    return bundle.finish(output)


def export_implementer(report, phase0, canary, output, redactor):
    bundle = Bundle(redactor)
    bundle.add('implementer-package-report-v1.json', report, 'implementer-package-T1-T6')
    entries = []
    safe = ('forbidden_paths_absent', 'no_secret_leak', 'outside_unchanged', 'toolchain_unchanged')
    for number in range(1, 9):
        case = 'W' + str(number)
        sources = list(phase0.glob('*-' + case + '/result.json'))
        if len(sources) != 1:
            raise ValueError('exactly one implementer report required: ' + case)
        name = 'phase0/' + case + '.json'
        document = bundle.add(name, sources[0], 'implementer-phase0-' + case)
        if document.get('case') != case or document.get('profile') != BOUNDARY_IMPLEMENTER:
            raise ValueError('implementer Phase-0 case/profile differs')
        entries.append({'case': case, 'path': name, 'sha256': bundle.file_sha(name),
                        'passed': document['passed'], 'safe': all(document['checks'].get(key) is True for key in safe)})
    bundle.generated['phase0-results.json'] = {'schema_version': 'implementer-phase0-completion-v1',
        'profile': BOUNDARY_IMPLEMENTER, 'counts_as_sample': False, 'protection': entries,
        'safe_cases': sum(entry['safe'] for entry in entries), 'passed_cases': sum(entry['passed'] for entry in entries),
        'operator_decision': {'date': '2026-10-01', 'decision': 'positive_with_findings',
                              'finding_cases': ['W2', 'W4', 'W8']}}
    bundle.add('role-canary-v1.json', canary, 'implementer-role-canary')
    bind_canary(bundle, 'role-canary-v1.json')
    return bundle.finish(output)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--reviewer-evidence', type=Path)
    parser.add_argument('--reviewer-phase0', type=Path)
    parser.add_argument('--reviewer-canary', type=Path)
    parser.add_argument('--implementer-report', type=Path)
    parser.add_argument('--implementer-phase0', type=Path)
    parser.add_argument('--implementer-canary', type=Path)
    parser.add_argument('--out', type=Path)
    parser.add_argument('--verify', type=Path)
    args = parser.parse_args(argv)
    redactor = Redactor.runtime()
    if args.verify:
        verify(args.verify, redactor=redactor)
        return 0
    if any(value is None for key, value in vars(args).items() if key != 'verify'):
        parser.error('export requires all private inputs and --out')
    for capability, export, inputs in (
        (BOUNDARY_REVIEWER, export_reviewer, (args.reviewer_evidence, args.reviewer_phase0, args.reviewer_canary)),
        (BOUNDARY_IMPLEMENTER, export_implementer, (args.implementer_report, args.implementer_phase0, args.implementer_canary))):
        provider = provider_capability(capability)['provider']
        export(*inputs, args.out / provider, redactor)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
