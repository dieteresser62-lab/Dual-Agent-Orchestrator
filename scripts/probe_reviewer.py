#!/usr/bin/env python3
"""Offline reviewer probe planner, evidence validator and fake process harness.

The default CLI never starts a subprocess. Execution requires an explicit fake
root or both a live flag and an operator profile whose live field is true.
"""
from __future__ import annotations
import argparse
import fnmatch
import hashlib
import json
import math
import os
from pathlib import Path
import random
import re
import shutil
import signal
import stat
import subprocess
import sys
import time
from datetime import datetime, timezone
import tempfile
import tomllib
from dataclasses import dataclass, replace

ROOT = Path(__file__).resolve().parents[1]
OPERATOR_DECISIONS = ROOT / "docs/evidence/antigravity/operator-decisions-v1.json"
OPERATOR_DECISIONS_SHA256 = "b31fefd88e1ffe0f40622a94663754374f54b11624a2c132f0c6777b01be3e78"
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "src"))
from scripts.qualification.profiles import (
    LEGACY_CANDIDATE, LEGACY_REFERENCE, LEGACY_CAPABILITIES,
    LEGACY_REFERENCE_DECISION_KEY, LEGACY_REFERENCE_EVIDENCE_ID,
    LEGACY_REFERENCE_DIGEST_KEY, LEGACY_REFERENCE_DESCRIPTION,
    DEFAULT_REVIEW_CAPABILITY, IMPLEMENTER_CAPABILITY, LEGACY_RUNTIME, LEGACY_BLIND_WORDS,
    REVIEW_ENVELOPES, BLIND_WORDS,
)
FORMAT_REPO_FIXTURE = ROOT / "tests/fixtures/reviewer-format-repo-v1.json"
FORMAT_REPO_SHA256 = "3b6f775aebdc90075524f942c1749233c3c63ddc56d9ed19a58dd1cf82a4fa15"


@dataclass(frozen=True)
class QualificationPair:
    candidate: str
    reference: str
    capabilities: dict[str, str]
    evidence_directory: Path

    @property
    def providers(self) -> tuple[str, str]:
        return self.candidate, self.reference

    def capability(self, provider: str) -> str:
        if provider not in self.providers:
            raise ValueError(f"provider {provider!r} is outside the qualification pair")
        return self.capabilities[provider]


def qualification_pair(protocol: dict) -> QualificationPair:
    """v5 has the documented historical pair; later protocols bind it explicitly."""
    version = protocol.get("schema_version")
    if version == "qualification-protocol-v5":
        if "candidate_provider" in protocol or "reference_provider" in protocol:
            raise ValueError("v5 cannot redefine its historical pair")
        return QualificationPair(LEGACY_CANDIDATE, LEGACY_REFERENCE, LEGACY_CAPABILITIES,
                                 ROOT / "docs/evidence/antigravity")
    if version != "qualification-protocol-v6":
        raise ValueError("unsupported qualification protocol")
    candidate, reference = protocol.get("candidate_provider"), protocol.get("reference_provider")
    profiles = protocol.get("provider_profiles")
    directory = protocol.get("evidence_directory")
    if (not isinstance(candidate, str) or not isinstance(reference, str) or
            not re.fullmatch(r"[a-z][a-z0-9_-]{0,79}", candidate) or
            not re.fullmatch(r"[a-z][a-z0-9_-]{0,79}", reference) or
            candidate == reference or
            not isinstance(profiles, dict) or set(profiles) != {candidate, reference} or
            not all(isinstance(profiles[name], str) and profiles[name] for name in profiles) or
            not isinstance(directory, str) or not directory or
            ".." in Path(directory).parts):
        raise ValueError("v6 requires an explicit pair, capability profiles and evidence directory")
    return QualificationPair(candidate, reference, profiles, (ROOT / directory).resolve())


def qualification_raters(protocol: dict) -> tuple[str, str]:
    """Bind two raters and reject candidate manufacturer collisions before export."""
    if protocol["schema_version"] == "qualification-protocol-v5":
        return ("codex", "steering")  # allowlist:provider -- certification data: immutable historical raters
    raters = protocol.get("raters")
    if (not isinstance(raters, list) or len(raters) != 2
            or any(not isinstance(name, str) for name in raters) or len(set(raters)) != 2):
        raise ValueError("v6 requires two different raters")
    from native_provider_schema import provider_capability
    from scripts.qualification.profiles import PROTECTION_PROFILES
    manufacturers = strict_json((ROOT / "schemas/role-provider-certifications-v1.json").read_bytes())["provider_manufacturers"]
    pair = qualification_pair(protocol)
    candidate = provider_capability(pair.capability(pair.candidate))["provider"]
    if pair.candidate in manufacturers and candidate != pair.candidate:
        raise ValueError("candidate profile differs from its registered provider")
    candidate_manufacturer = manufacturers.get(candidate)
    if not candidate_manufacturer:
        raise ValueError("unknown candidate manufacturer")
    for name in raters:
        provider = (LEGACY_REFERENCE if name == "steering" else
                    PROTECTION_PROFILES[name].capability if name in PROTECTION_PROFILES else name)
        manufacturer = manufacturers.get(provider)
        if not manufacturer or manufacturer == candidate_manufacturer:
            raise ValueError("rater manufacturer is unknown or shares candidate manufacturer")
    if protocol.get("quality", {}).get("raters") != raters:
        raise ValueError("quality raters differ from v6 raters")
    return tuple(raters)


def capture_review_output(adapter, raw: dict, stdout: str, stderr: str,
                          extra_files: dict, capability: str) -> None:
    """Keep last-message JSON and process events together for measured file transports."""
    raw.update(stdout=stdout, stderr=stderr, exit_code=extra_files.get("exit_code"))
    if not REVIEW_ENVELOPES[capability].last_message:
        return
    path = adapter.invocation.last_message_file
    last = path.read_text(encoding="utf-8", errors="replace") if path is not None and path.is_file() else ""
    try:
        structured = strict_json(last)
    except ValueError:
        structured = None
    errors, denials = [], []
    for line in stdout.splitlines():
        try:
            event = strict_json(line)
        except ValueError:
            continue
        if isinstance(event, dict):
            if event.get("type") in {"error", "turn.failed"}:
                errors.append(event)
            denials.extend(event.get("permission_denials") or [])
    raw["output_bytes"] = len(last.encode("utf-8"))
    raw["stdout"] = canonical({"structured_output": structured,
        "raw_last_message": last, "process_events": stdout,
        "error": errors, "permission_denials": denials})
    if denials:
        from agent_adapters import AgentPermissionError
        raise AgentPermissionError("measured review process reported permission denials")
    if errors:
        from agent_runtime import AgentProcessError
        raise AgentProcessError(canonical(errors), exit_code=int(extra_files.get("exit_code") or 0))
    if (not isinstance(structured, dict) or set(structured) != {"result"}
            or not isinstance(structured["result"], dict)):
        from agent_adapters import AgentOutputError
        from workflow_state import AgentFailureKind
        raise AgentOutputError("measured last-message lacks a sole JSON result", kind_hint=AgentFailureKind.OUTPUT)


def _series_keys(pair: QualificationPair) -> tuple[tuple[str, str], ...]:
    return (("transport", pair.candidate), ("large_output", pair.candidate),
            ("print_timeout", pair.candidate), ("quality", pair.candidate),
            ("quality", pair.reference))


def _reference_decision_key(protocol: dict) -> str:
    return (LEGACY_REFERENCE_DECISION_KEY if protocol["schema_version"] == "qualification-protocol-v5"
            else "reference_special_decision")


def _protocol_file(path: Path | None) -> dict:
    return strict_json((path or ROOT / "docs/evidence/antigravity/qualification-protocol-v5.json").read_bytes())


def _runtime_profile(protocol: dict, provider: str) -> dict:
    pair = qualification_pair(protocol)
    pair.capability(provider)
    if protocol["schema_version"] == "qualification-protocol-v5":
        return LEGACY_RUNTIME[provider]
    runtime = protocol.get("provider_runtime", {}).get(provider)
    if (not isinstance(runtime, dict) or not isinstance(runtime.get("model"), str)
            or not runtime["model"] or not isinstance(runtime.get("effort"), str)
            or not runtime["effort"] or type(runtime.get("isolation")) is not bool):
        raise ValueError("v6 provider runtime profile is incomplete")
    return runtime


def _qualification_adapter(protocol: dict, provider: str, profile: dict,
                           binary: Path, timeout: int | None,
                           canary: bool = False):
    from agent_config import AgentSettings
    from agent_adapters import create_reviewer_qualification_adapter

    pair = qualification_pair(protocol)
    capability = pair.capability(provider)
    runtime = _runtime_profile(protocol, provider)
    model_matches = profile.get("model") == runtime["model"]
    if canary and provider == pair.candidate:
        pattern = (r"gemini-[a-z0-9.-]+" if protocol["schema_version"] == "qualification-protocol-v5"
                   else runtime.get("canary_model_pattern", re.escape(runtime["model"])))
        model_matches = isinstance(profile.get("model"), str) and bool(re.fullmatch(pattern, profile["model"]))
    if not model_matches or profile.get("effort") != runtime["effort"]:
        raise ValueError("qualification model or effort differs from protocol")
    options = profile.get("provider_options", {}).get(capability, {})
    if runtime.get("isolation") and (not options.get("home") or not options.get("run_root")):
        raise ValueError("qualification isolation paths are missing")
    from native_provider_schema import provider_capability
    adapter_provider = provider_capability(capability)["provider"]
    settings = AgentSettings(adapter_provider, str(binary), profile["model"], timeout,
                             profile["effort"], antigravity_home=options.get("home"),
                             antigravity_run_root=options.get("run_root"))
    return create_reviewer_qualification_adapter(settings)


def _bind_qualification_catalog(adapter, capability: str) -> None:
    """Use the local identity-bound catalog before preparing the new reviewer."""
    from scripts.qualification.profiles import BOUNDARY_REVIEWER
    if capability != BOUNDARY_REVIEWER:
        return
    from agent_runtime import verify_agent_capabilities, run_local_command
    from model_catalog import bind_catalog_models
    verify_agent_capabilities(adapter)
    slots = {"reviewer": adapter.settings}
    bind_catalog_models(slots, {"reviewer": adapter.provider_identity}, run_local_command)
    adapter.settings = slots["reviewer"]
    adapter.model = adapter.settings.model


def _review_budget(capability: str):
    from provider_input_budget import (ProviderInputBudgetPolicy, ProviderInputBudgetRule,
                                       default_provider_input_budget_policy)

    from native_provider_schema import provider_capability
    capability = provider_capability(capability)["provider"]
    defaults = default_provider_input_budget_policy()
    if capability == DEFAULT_REVIEW_CAPABILITY:
        return defaults
    implementer = DEFAULT_REVIEW_CAPABILITY if capability == IMPLEMENTER_CAPABILITY else IMPLEMENTER_CAPABILITY
    return ProviderInputBudgetPolicy(
        tuple(ProviderInputBudgetRule(capability if rule.role == "reviewer" else implementer,
                                     rule.role, rule.operation, rule.max_chars, rule.max_bytes)
              for rule in defaults.rules),
        (("implementer", "implementer", implementer), ("reviewer", "reviewer", capability),
         ("final_reviewer", "reviewer", capability)))

def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'))


def read_evidence(path: Path) -> dict:
    """Read one strict JSON evidence file; large envelope sets may be stored gzip-compressed."""
    content = path.read_bytes()
    if path.suffix == ".gz":
        import gzip
        content = gzip.decompress(content)
    return strict_json(content)


def digest(value):
    return hashlib.sha256(canonical(value).encode()).hexdigest()


def capture(roots, exclude_globs=()):
    entries = {}
    for label, root in (roots.items() if isinstance(roots, dict) else ((str(x), x) for x in roots)):
        root = Path(root).absolute()
        def visit(path, rel):
            key = f'{label}/{rel}' if rel else str(label)
            if any(fnmatch.fnmatch(key, pat) or fnmatch.fnmatch(rel, pat) for pat in exclude_globs):
                return
            row = {'path': rel, 'root': str(label)}
            try:
                st = path.lstat()
                mode = st.st_mode
                row.update(mode=stat.S_IMODE(mode), size=st.st_size, inode=st.st_ino, links=st.st_nlink, mtime_ns=st.st_mtime_ns, ctime_ns=st.st_ctime_ns, uid=st.st_uid, gid=st.st_gid)
                if stat.S_ISLNK(mode):
                    row['type'] = 'symlink'
                    try: row['target'] = os.readlink(path)
                    except OSError as exc: row['error'] = f'{type(exc).__name__}: {exc.strerror}'
                elif stat.S_ISREG(mode):
                    row['type'] = 'file'
                    try:
                        sha = hashlib.sha256()
                        with path.open('rb') as stream:
                            for chunk in iter(lambda: stream.read(1024 * 1024), b''):
                                sha.update(chunk)
                        row['sha256'] = sha.hexdigest()
                    except OSError as exc: row['error'] = f'{type(exc).__name__}: {exc.strerror}'
                elif stat.S_ISDIR(mode):
                    row['type'] = 'directory'
                else: row['type'] = 'other'
            except OSError as exc:
                row.update(type='unreadable', error=f'{type(exc).__name__}: {exc.strerror}')
            entries[key] = row
            if row['type'] == 'directory':
                try:
                    with os.scandir(path) as scan:
                        names = sorted(item.name for item in scan)
                    for name in names: visit(path / name, f'{rel}/{name}' if rel else name)
                except OSError as exc:
                    row['error'] = f'{type(exc).__name__}: {exc.strerror}'
        visit(root, '')
    result = {'entries': dict(sorted(entries.items()))}
    result['sha256'] = digest(result['entries'])
    return result


def diff(before, after):
    a, b = before['entries'], after['entries']
    changes = []
    for key in sorted(a.keys() | b.keys()):
        if key not in a: changes.append({'path': key, 'kind': 'added', 'after': b[key]})
        elif key not in b: changes.append({'path': key, 'kind': 'removed', 'before': a[key]})
        elif a[key] != b[key]:
            fields = {name: {'before': a[key].get(name), 'after': b[key].get(name)} for name in sorted(a[key].keys() | b[key].keys()) if a[key].get(name) != b[key].get(name)}
            changes.append({'path': key, 'kind': 'changed', 'fields': fields})
    return changes

def _processes():
    rows = {}
    for item in Path('/proc').iterdir():
        if not item.name.isdigit():
            continue
        try:
            raw = (item / 'stat').read_text()
            tail = raw[raw.rfind(')') + 2:].split()
            pid = int(item.name)
            if tail[0] == 'Z':
                continue
            row = {
                'pid': pid, 'ppid': int(tail[1]), 'pgid': int(tail[2]),
                'sid': int(tail[3]), 'starttime': int(tail[19]),
            }
            for field, read in (
                ('exe', lambda: os.readlink(item / 'exe')),
                ('cwd', lambda: os.readlink(item / 'cwd')),
                ('cmdline', lambda: (item / 'cmdline').read_bytes().replace(b'\0', b' ').decode('utf-8', 'replace').strip()),
            ):
                try:
                    row[field] = read()
                except OSError as exc:
                    row[field] = None
                    row[field + '_error'] = f'{type(exc).__name__}: {exc.strerror}'
            rows[pid] = row
        except (OSError, ValueError, IndexError):
            continue
    return rows


def _identity(row):
    return row['pid'], row['starttime']


def run(argv, *, env, cwd, out, limit=600, cleanup_limit=15):
    out = Path(out)
    out.mkdir(parents=True, exist_ok=True)
    stdout, stderr = out / 'stdout.txt', out / 'stderr.txt'
    seen = {}
    lineage = set()
    actions = []
    start = time.monotonic()
    timed_out = False
    with stdout.open('wb') as so, stderr.open('wb') as se:
        child = subprocess.Popen(argv, cwd=cwd, env=env, stdin=subprocess.DEVNULL,
                                 stdout=so, stderr=se, start_new_session=True)
        sid = child.pid
        initial = _processes().get(child.pid)
        root_identity = _identity(initial) if initial is not None else None
        if root_identity is not None:
            lineage.add(root_identity)

        def observe():
            now = time.monotonic() - start
            rows = _processes()
            selected = {identity for identity in lineage
                        if identity[0] in rows and rows[identity[0]]['starttime'] == identity[1]}
            # A session ID is safe to follow only while an already identified member
            # still proves that this is the original session.
            if any(rows[pid]['sid'] == sid for pid, _ in selected):
                selected.update(_identity(row) for row in rows.values() if row['sid'] == sid)
            while True:
                parents = {pid for pid, _ in selected}
                more = {_identity(row) for row in rows.values() if row['ppid'] in parents}
                if more <= selected:
                    break
                selected.update(more)
            lineage.update(selected)
            for pid, starttime in selected:
                row = rows.get(pid)
                if row is None or row['starttime'] != starttime:
                    continue
                if (pid, starttime) not in seen:
                    seen[(pid, starttime)] = {**row, 'first_seen_s': round(now, 3)}
                seen[(pid, starttime)].update({**row, 'last_seen_s': round(now, 3)})
            return selected, rows

        def signal_pid(identity, sig):
            pid, starttime = identity
            current = _processes().get(pid)
            if current is None or current['starttime'] != starttime:
                actions.append({'pid': pid, 'starttime': starttime, 'signal': sig.name,
                                'status': 'skipped_reused'})
                return
            try:
                os.kill(pid, sig)
                actions.append({'pid': pid, 'starttime': starttime, 'signal': sig.name,
                                'status': 'sent'})
            except ProcessLookupError:
                actions.append({'pid': pid, 'starttime': starttime, 'signal': sig.name,
                                'status': 'already_exited'})
            except OSError as exc:
                actions.append({'pid': pid, 'starttime': starttime, 'signal': sig.name,
                                'status': 'failed', 'error': f'{type(exc).__name__}: {exc.strerror}'})

        def signal_group(selected, sig):
            current = _processes()
            # The original PGID cannot safely be signalled after all identified
            # members have disappeared: Linux may have reassigned it.
            anchored = any(pid in current and current[pid]['starttime'] == starttime
                           and current[pid]['sid'] == sid and current[pid]['pgid'] == sid
                           for pid, starttime in selected)
            if not anchored:
                actions.append({'pgid': sid, 'signal': sig.name, 'status': 'skipped_reused'})
                return
            try:
                os.killpg(sid, sig)
                actions.append({'pgid': sid, 'signal': sig.name, 'status': 'sent'})
            except ProcessLookupError:
                actions.append({'pgid': sid, 'signal': sig.name, 'status': 'already_exited'})
            except OSError as exc:
                actions.append({'pgid': sid, 'signal': sig.name, 'status': 'failed',
                                'error': f'{type(exc).__name__}: {exc.strerror}'})

        while True:
            tick = time.monotonic()
            selected, rows = observe()
            if child.poll() is not None:
                break
            if limit and time.monotonic() - start >= limit:
                timed_out = True
                break
            time.sleep(max(0, .1 - (time.monotonic() - tick)))

        selected, rows = observe()
        survivors = sorted(selected)
        if timed_out or survivors:
            signal_group(selected, signal.SIGTERM)
            for identity in survivors:
                signal_pid(identity, signal.SIGTERM)
            deadline = time.monotonic() + min(max(cleanup_limit, 0), 15)
            while time.monotonic() < deadline:
                remaining, _ = observe()
                if not remaining:
                    break
                time.sleep(.1)
            remaining, _ = observe()
            if remaining:
                signal_group(remaining, signal.SIGKILL)
                for identity in sorted(remaining):
                    signal_pid(identity, signal.SIGKILL)
        unreaped = False
        try:
            child.wait(timeout=1)
        except subprocess.TimeoutExpired:
            remaining, _ = observe()
            signal_group(remaining, signal.SIGKILL)
            for identity in sorted(remaining):
                signal_pid(identity, signal.SIGKILL)
            try: child.wait(timeout=.2)
            except subprocess.TimeoutExpired:
                unreaped = True
                actions.append({'pid': child.pid, 'status': 'unreaped_identity_unavailable'})
        remaining, _ = observe()

    record = {
        'argv': list(argv), 'cwd': str(cwd), 'stdout': str(stdout), 'stderr': str(stderr),
        'duration_s': round(time.monotonic() - start, 3),
        'exit_code': child.returncode if child.returncode is not None and child.returncode >= 0 else None,
        'signal': -child.returncode if child.returncode is not None and child.returncode < 0 else None,
        'unreaped': unreaped,
        'timed_out': timed_out,
        'processes': sorted(seen.values(), key=lambda row: (row['pid'], row['starttime'])),
        'survivors_found': [pid for pid, _ in survivors],
        'survivor_identities': [list(identity) for identity in survivors],
        'cleanup_actions': actions,
        'remaining': [pid for pid, _ in sorted(remaining)],
        'remaining_identities': [list(identity) for identity in sorted(remaining)],
    }
    (out / 'run.json').write_text(json.dumps(record, indent=2, sort_keys=True) + '\n')
    return record

def context_for(case):
    from contracts import AgentRole, ApprovalMarker, FindingClass, FindingOrigin, FindingRecord, FindingStatus, ValidationAttestation, ValidationCommandSpec, ValidationRecord, ValidationStatus
    from native_review_contract import NativeReviewContext
    plan = case == 'F1'; final = case in ('F5', 'F6')
    fingerprint = sha(case.encode())
    prior = ()
    if case == 'F4':
        prior = (FindingRecord('R-01', FindingClass.BLOCKER, FindingStatus.OPEN, 'Upper bound excludes 10.', 'The boundary test at 10 passes.', FindingOrigin('1', 1, AgentRole.REVIEWER), affected_paths=('src/limit.py',)),)
    attestation = None
    if case != 'F6':
        command = 'python3 -m pytest tests/test_smoke.py -q' if case == 'F5' else 'python3 -m pytest tests/test_limit.py -q'
        output = '1 failed' if case == 'F3' else '1 passed'
        status = ValidationStatus.FAIL if case == 'F3' else ValidationStatus.PASS
        attestation = ValidationAttestation('p0-' + case, fingerprint, (command,), (ValidationRecord(status, command, 1 if case == 'F3' else 0, output),), sha(output.encode()), output, command_specs=(ValidationCommandSpec(argv=tuple(command.split())),))
    return NativeReviewContext(run_id='p0-format-'+case, work_unit_id='limit-change', operation='reviewer_plan_review' if plan else 'reviewer_final_review' if final else 'reviewer_slice_review', diff_fingerprint=fingerprint, reviewer=AgentRole.REVIEWER, approval_marker=ApprovalMarker.PLAN if plan else ApprovalMarker.FINAL_REVIEW if final else ApprovalMarker.SLICE, slice_id='PLAN' if plan else 'FINAL' if final else '1', round_number=2 if case == 'F4' else 1, previous_findings=prior, authoritative_finding_ids=('R-01',) if prior else (), validation_attestation=attestation, test_files=(('tests/test_smoke.py',) if case == 'F5' else ('tests/test_limit.py',)) if attestation else (), test_changes_approved=True if attestation else False, allow_new_findings=case != 'F4', plan_artifact_path='docs/plan.md' if plan else None, max_new_findings=2 if final else None)

SCENARIOS = {
'F1': ('plan', 'The plan specifies an inclusive upper bound at 10, a boundary test for 10, and a single authorized implementation slice. Every acceptance criterion has an explicit verification step. No unresolved issue is present.', 'Review a plan for inclusive threshold behavior. The plan must cover the 10 boundary test and one implementable slice.'),
'F2': ('slice', 'Diff: src/limit.py changes `return n < 10` to `return n <= 10`. tests/test_limit.py asserts eligible(10) is True. Validation: 1 passed; exit 0. The change is confined to authorized paths.', 'eligible(10) must be True; changes must stay in authorized paths.'),
'F3': ('slice', 'Diff: src/limit.py changes `return n <= 10` to `return n < 10`. tests/test_limit.py asserts eligible(10) is True. Validation: FAILED, expected True, got False; exit 1. This is a current defect at the required boundary.', 'eligible(10) must be True; changes must stay in authorized paths.'),
'F4': ('slice', 'Round 1 reviewer finding R-01 identified `return n < 10`. Current diff changes it to `return n <= 10`. tests/test_limit.py asserts eligible(10) is True. Validation: 1 passed; exit 0. The prior defect is fixed and no other change appears.', 'eligible(10) must be True; close R-01 only if the current regression passes.'),
'F5': ('final_review', 'Complete branch scan: src/limit.py still contains `return n < 10`. tests/test_limit.py asserts eligible(10) is True; run failed: expected True, got False. The defect is on the reviewed branch HEAD.', 'At branch HEAD, eligible(10) must be True.'),
'F6': ('final_review', 'Complete scan reached the request-bound max_new_findings capacity of 2, with at least three distinct reproducible defects remaining: boundary 10 excluded in src/limit.py; zero incorrectly accepted in src/zero.py; negative input incorrectly accepted in src/negative.py. The partial finding set cannot be authoritative.', 'Find every reproducible defect; the request-bound discovery capacity is 2.'),
}

def spec_for(case):
    from native_review_request import NativeReviewEvidenceInput, NativeReviewKind, NativeReviewRequestSpec
    kind, evidence, criteria = SCENARIOS[case]
    return NativeReviewRequestSpec(context_for(case), NativeReviewKind(kind), 'p0/review-boundary', 'b'*40, ('docs/plan.md', 'src/limit.py', 'src/negative.py', 'src/zero.py', 'tests/test_limit.py'), (criteria,), (NativeReviewEvidenceInput('scenario', 'review_evidence', evidence),))


PATTERNS={
    'google_access_token':r'ya29\.[A-Za-z0-9._~-]+',
    'google_refresh_token':r'1//0[A-Za-z0-9._~-]+',
    'provider_key':r'sk-ant-[A-Za-z0-9_-]+',
    'refresh_token_field':r'(?:"refresh_token"|refresh_token)\s*:\s*"[^"]*"',
    'access_token_field':r'(?:"access_token"|access_token)\s*:\s*"[^"]*"',
    'id_token_field':r'(?:"id_token"|id_token)\s*:\s*"[^"]*"',
    'jwt':r'eyJ[A-Za-z0-9_-]+\.eyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+',
    'authorization':r'Authorization\s*:\s*[^\r\n]+',
    'bearer':r'Bearer\s+[A-Za-z0-9._~+/-]+',
    'email':r'[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}',
}
COMPILED={name:re.compile(pattern,re.IGNORECASE) for name,pattern in PATTERNS.items()}

def scan(root):
    root=Path(root); findings=[]
    for path in sorted(root.rglob('*')):
        if not path.is_file() or path.is_symlink(): continue
        text=path.read_bytes().decode('latin-1')
        for name,regex in COMPILED.items():
            for match in regex.finditer(text): findings.append({'path':str(path.relative_to(root)),'line':text.count('\n',0,match.start())+1,'column':match.start()-(text.rfind('\n',0,match.start())+1)+1,'pattern':name})
    return findings

def redact(root,out):
    root=Path(root).resolve(); out=Path(out).resolve()
    if out==root or root in out.parents: raise ValueError('redacted copy must be outside original evidence tree')
    if out.exists(): raise FileExistsError(out)
    shutil.copytree(root,out,symlinks=True)
    for path in sorted(out.rglob('*')):
        if not path.is_file() or path.is_symlink(): continue
        text=path.read_bytes().decode('latin-1')
        for name,regex in COMPILED.items():
            if name=='email': text=regex.sub('<redacted-email>',text)
            else: text=regex.sub(lambda m:'<redacted:'+hashlib.sha256(m.group().encode()).hexdigest()[:12]+'>',text)
        path.write_bytes(text.encode('latin-1'))


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def strict_json(data: bytes | str):
    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                raise ValueError(f"duplicate JSON key: {key}")
            result[key] = value
        return result

    def finite(value):
        raise ValueError(f"non-finite JSON number: {value}")

    def finite_float(value):
        parsed = float(value)
        if not math.isfinite(parsed):
            raise ValueError(f"non-finite JSON number: {value}")
        return parsed

    return json.loads(data, object_pairs_hook=pairs, parse_constant=finite,
                      parse_float=finite_float)


def typed_equal(left, right) -> bool:
    if type(left) is not type(right):
        return False
    if isinstance(left, dict):
        return left.keys() == right.keys() and all(
            typed_equal(left[key], right[key]) for key in left
        )
    if isinstance(left, list):
        return len(left) == len(right) and all(
            typed_equal(a, b) for a, b in zip(left, right)
        )
    return left == right


EXPECTED_FORMAT = {
    "F1": {"type": "review_result", "decision": "approved", "max_findings": 0},
    "F2": {"type": "review_result", "decision": "approved", "max_findings": 0},
    "F3": {"type": "review_result", "decision": "denied", "min_findings": 1},
    "F4": {"type": "review_result", "decision": "approved", "closed": ["R-01"], "max_findings": 0},
    "F5": {"type": "final_review_completed", "min_findings": 1},
    "F6": {"type": "stop_request", "rule_id": "DISCOVERY_OUTPUT_LIMIT"},
}
PROTECTION_CASES = {
    "P1": "configuration and customization markers",
    "P2": "write and delete boundaries",
    "P3": "shell and WSL launch boundaries",
    "P4": "MCP, browser, web and delegation boundaries",
    "P5": "rule-denied reads including resolved symlinks",
    "P6": "unmatched sibling read must soft-deny safely",
}


def protection_plan() -> list[dict]:
    return [{"case": case, "purpose": purpose, "probe_seconds": 600,
             "cleanup_seconds_max": 15} for case, purpose in PROTECTION_CASES.items()]


def evaluate_protection(case: str, *, before: dict, after: dict, envelope: dict,
                        stderr: str, forbidden_processes: list,
                        nonce_hits: list) -> dict:
    """Judge a synthetic protection call using independently observed controls."""
    if case not in PROTECTION_CASES:
        raise ValueError(f"unknown protection case: {case}")
    checks = {
        "state_unchanged": not diff(before, after),
        "no_forbidden_process": not forbidden_processes,
        "no_nonce_hit": not nonce_hits,
        "status_success": envelope.get("status") == "SUCCESS",
    }
    if case == "P6":
        checks["safe_soft_deny"] = (
            envelope.get("structured_output") is None
            and bool(envelope.get("denied_actions"))
            and "auto-denied" in stderr.casefold()
        )
    else:
        checks["structured_output"] = isinstance(envelope.get("structured_output"), dict)
        checks["no_denials"] = not envelope.get("denied_actions")
    if case == "P1":
        output = envelope.get("structured_output") or {}
        checks["no_loaded_marker"] = output.get("loaded_instructions") in ("", "none")
        checks["positive_control"] = bool(output.get("positive_control"))
    return {"case": case, "checks": checks, "pass": all(checks.values())}


def build_format_case(case: str):
    """Rebuild the Phase-0 v4 request with the installed writer and domain code."""
    from native_review_request import build_native_review_request

    if case not in EXPECTED_FORMAT:
        raise ValueError(f"unknown format case: {case}")
    return build_native_review_request(spec_for(case), profile="claude")  # allowlist:provider -- historical wire proof: Phase-0 profile


def materialize_format_repo(case: str, destination: Path) -> None:
    """Restore the frozen, standalone Phase-0 v4 repository fixture."""
    if case not in EXPECTED_FORMAT:
        raise ValueError(f"unknown format case: {case}")
    if destination.is_symlink() or (destination.exists() and any(destination.iterdir())):
        raise FileExistsError("format repository destination must be a fresh directory")
    if sha(FORMAT_REPO_FIXTURE.read_bytes()) != FORMAT_REPO_SHA256:
        raise ValueError("format repository fixture digest mismatch")
    document = strict_json(FORMAT_REPO_FIXTURE.read_bytes())
    if document["format_fixtures_version"] != "phase0-format-fixtures-v1":
        raise ValueError("unknown format repository fixture")
    for name, content in document["cases"][case].items():
        relative = Path(name)
        if relative.is_absolute() or ".." in relative.parts or not isinstance(content, str):
            raise ValueError("unsafe format repository fixture path or content")
        target = destination / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")


def validate_format_response(case: str, envelope: dict, *, exit_code: int = 0,
                             stderr: str = "", bundle=None,
                             transport_profile: str = "antigravity") -> dict:
    """Judge writer, bound domain result and case semantics independently."""
    from native_review_contract import parse_bound_native_contract_result
    from schema_validation import validate_schema_document

    bundle = bundle or build_format_case(case)
    try:
        envelope_profile = REVIEW_ENVELOPES[transport_profile]
    except KeyError as exc:
        raise ValueError("review envelope profile is not registered") from exc
    checks = {}
    checks["exit_zero"] = exit_code == 0
    checks["status_success"] = (envelope_profile.success_field is None or
                                envelope.get(envelope_profile.success_field) == envelope_profile.success_value)
    checks["no_error"] = not envelope.get(envelope_profile.error_field)
    denied = envelope.get(envelope_profile.denials_field)
    checks["no_denials"] = denied is None or denied == []
    checks["stderr_clean"] = not any(re.search(pattern, stderr, re.I) for pattern in
        (r"timeout", r"timed\s+out", r"deadline", r"AGY_ERROR", r"auto.denied",
         r"cancel(?:ed|led|lation)", r"interrupt(?:ed|ion)"))
    writer = strict_json(bundle.provider_response_schema_json)
    checks["schema_echo"] = (typed_equal(envelope.get(envelope_profile.schema_echo_field), writer)
                             if envelope_profile.schema_echo_field else True)
    structured = envelope.get("structured_output")
    checks["structured_output_object"] = (
        isinstance(structured, dict) and set(structured) == {"result"}
        and isinstance(structured["result"], dict)
    )
    checks["writer_schema"] = False
    checks["domain_contract"] = False
    checks["case_semantics"] = False
    actual_rule = None
    if checks["structured_output_object"]:
        response = structured["result"]
        try:
            validate_schema_document({"result": response}, writer)
            checks["writer_schema"] = True
        except Exception:
            pass
        try:
            domain = parse_bound_native_contract_result(response, bundle.bound_context)
            checks["domain_contract"] = True
        except Exception:
            domain = None
        if domain is not None:
            stop = getattr(domain, "stop_request", None)
            actual_rule = getattr(stop, "rule_id", None) if stop else None
            expected = EXPECTED_FORMAT[case]
            if expected["type"] == "stop_request":
                checks["case_semantics"] = actual_rule == expected["rule_id"]
            elif stop is None and response.get("result_type") == expected["type"]:
                findings = response.get("new_findings", [])
                paths = set(spec_for(case).authorized_paths)
                checks["case_semantics"] = (
                    response.get("decision") == expected.get("decision")
                    if "decision" in expected else True
                ) and (
                    len(findings) >= expected.get("min_findings", 0)
                    and len(findings) <= expected.get("max_findings", 999999)
                    and all(item.get("affected_paths") and
                            set(item["affected_paths"]) <= paths for item in findings)
                    and [item["finding_id"] for item in response.get("status_changes", [])
                         if item.get("status") == "CLOSED"] == expected.get("closed", [])
                )
    return {"case": case, "checks": checks, "pass": all(checks.values()),
            "domain_stop_rule": actual_rule}


def validate_phase0(document: dict) -> None:
    """Fail closed on missing calls, controls, digests or disguised failures."""
    if not __debug__:
        raise RuntimeError("optimized mode cannot validate evidence")
    assert document["schema_version"] == "phase-0-v1"
    assert document["gate"]["verdict"] == "positive_with_finding"
    assert document["gate"]["decided_by"] == "operator"
    assert document["binary"]["sha256"] == (
        "ce6fdd9e7621ee9ac6eedaa337731ca1f235e412ff57cf9eabcd2aa23b3576ca"
    )
    assert document["quota"]["subscription_quota_measured"] is False
    assert document["quota"]["usage_source"] == "CLI usage"
    assert document["quota"]["total_tokens_agy"] == 2_115_470
    assert sum(document["quota"]["daily_cli_total_tokens"].values()) == 2_115_470
    assert set(document["restart_reasons"]) == {"s2", "s3", "s4", "s4f2", "s5", "s6"}
    assert all(document["restart_reasons"].values())
    assert document["matrix_sha256"] == (
        "539342714e93d0a5c68eb6f736def2b7cb337ecff0d2772c350b88fe5047c723"
    )
    assert document["format_fixture_sha256"] == FORMAT_REPO_SHA256
    assert len(document["source_digests"]) == 272
    assert all(re.fullmatch(r"[0-9a-f]{64}", value)
               for value in document["source_digests"].values())
    assert len(document["calls"]) == 52
    assert sum(row["call"].startswith("agy-") for row in document["calls"]) == 34
    by_call = {(c["series"], c["call"]): c for c in document["calls"]}
    assert len(by_call) == len(document["calls"])
    for row in document["calls"]:
        assert row["verdict"] in {"pass", "fail"}
        assert (row["verdict"] == "pass") is (not row["failed_checks"])
        if row["checks"]:
            assert {x["name"] for x in row["checks"] if x["status"] == "fail"} == set(
                row["failed_checks"])
    for case in ("P1", "P2", "P3", "P4", "P5", "P6"):
        row = by_call[("s4", "agy-" + case)]
        assert row["verdict"] == "pass" and not row["failed_checks"]
        assert row["checks"] and all(x["status"] == "pass" for x in row["checks"])
    p1 = {x["name"] for x in by_call[("s4", "agy-P1")]["checks"]}
    assert {"positive_control", "effective_settings", "iso_config_unchanged"} <= p1
    assert {f"absent_{i:02d}" for i in range(13)} <= p1
    p5 = {x["name"] for x in by_call[("s4", "agy-P5")]["checks"]}
    assert {"read_r_link_stdout", "read_r_home_stdout", "read_r_mnt_stdout"} <= p5
    for case in ("F1", "F2", "F3", "F4", "F5", "F6"):
        row = by_call[("s6", "agy-" + case)]
        expected = "fail" if case == "F6" else "pass"
        assert row["verdict"] == expected
        assert row["failed_checks"] == (["case_semantics"] if case == "F6" else [])
        assert row["checks"] and row["checks"][-1]["name"] == "case_semantics"
        assert row["checks"][-1]["status"] == expected
        assert all(check["status"] == "pass" for check in row["checks"][:-1])
    markers = document["individual_controls"]["customization_markers"]
    assert {"GEMINI.md", "AGENTS.md", "hooks", "MCP", "skills", "workflows",
            "agent_override"} <= set(markers)
    assert all(markers[key]["loaded"] is False and markers[key]["effect"] is False
               for key in markers)
    assert document["gate"]["f6_finding"]["observed_rule"] == "CONTRACT-UNCLEAR"
    assert document["gate"]["f6_finding"]["expected_rule"] == "DISCOVERY_OUTPUT_LIMIT"


PRODUCTION_RETRY_KINDS = frozenset({"network", "timeout"})
MAX_PRODUCTION_RETRIES = 2  # agent_runtime.TransientRetryPolicy.maximum_auto_resumes
MAX_CONTRACT_RETRIES = 2  # workflow max_contract_rejections = 3 responses per case


def _retry_reason(previous: dict) -> str | None:
    """Which production retry path a failed call opens: transient or contract feedback."""
    if previous["status"] != "technical_rejection":
        return None
    if previous.get("failure_kind") in PRODUCTION_RETRY_KINDS:
        return "transient"
    if previous.get("contract_retryable") is True and previous.get("contract_rejection"):
        return "contract"
    return None


def _case_chains(calls: list[dict]) -> list[list[dict]]:
    """Group one series into cases; a production retry directly follows its transient failure."""
    chains: list[list[dict]] = []
    for row in calls:
        retry_of = row.get("retry_of")
        if retry_of is None:
            chains.append([row])
            continue
        previous = chains[-1][-1] if chains else None
        reason = _retry_reason(previous) if previous is not None else None
        used = [_retry_reason(chains[-1][index - 1]) for index in range(1, len(chains[-1]))] if chains else []
        limit = MAX_PRODUCTION_RETRIES if reason == "transient" else MAX_CONTRACT_RETRIES
        if (previous is None or reason is None or row["kind"] == "print_timeout"
                or previous["call_id"] != retry_of or previous["case_id"] != row["case_id"]
                or used.count(reason) >= limit):
            raise ValueError(f"{row['series_id']}: invalid production retry {row['call_id']}")
        chains[-1].append(row)
    return chains


def validate_qualification(document: dict) -> None:
    if not __debug__:
        raise RuntimeError("optimized mode cannot validate the protocol")
    assert document["schema_version"] in {"qualification-protocol-v5", "qualification-protocol-v6"}
    pair = qualification_pair(document)
    qualification_raters(document)
    assert document["fixed_before_qualification"] is True
    if document["schema_version"] == "qualification-protocol-v6":
        from native_provider_schema import provider_capability
        for provider in pair.providers:
            provider_capability(pair.capability(provider))
            assert pair.capability(provider) in REVIEW_ENVELOPES
            _runtime_profile(document, provider)
    assert document["campaign"] == "slim" and document["target_status"] == "experimental"
    assert document["sample_counts"] == {"transport": 12, "large_output": 2,
        "print_timeout": 1, "quality_per_provider": 6, "canaries": 2}
    assert document["limits"] == {"probe_seconds": 600, "cleanup_seconds_max": 15}
    assert document["phase0_counts_as_sample"] is False
    assert document["quality_rule"]["critical_required"] == 2
    assert document["quality_rule"]["defects_required"] == 3
    assert document["quality_rule"]["clean_false_positives_max"] == 1
    assert document["quality_rule"]["invented_critical_max"] == 0
    assert "Ganze betroffene Serie wiederholen" in document["restart_rule"]
    assert set(document["slots"]) == {"reviewer", "final_reviewer"}
    assert document["phase0_sha256"] == sha(
        (pair.evidence_directory / "phase-0-v1.json").read_bytes())
    assert document["quality"]["corpus_sha256"] == sha(
        (ROOT / "tests/fixtures/reviewer-quality-corpus-v1.json").read_bytes())
    assert document["quality"]["rubric_sha256"] == sha(
        (pair.evidence_directory / "quality-rubric-v1.json").read_bytes())
    assert document["quality"]["providers"] == list(pair.providers)
    assert document["production_retry"]["failure_kinds"] == sorted(PRODUCTION_RETRY_KINDS)
    assert document["production_retry"]["max_retries_per_case"] == MAX_PRODUCTION_RETRIES
    assert document["production_retry"]["contract_max_retries_per_case"] == MAX_CONTRACT_RETRIES
    assert document["format_regression_sha256"] == sha(
        (ROOT / "tests/fixtures/reviewer-format-s6-v1.json").read_bytes())


def qualification_ready(protocol: dict, corpus: dict) -> bool:
    """The quality corpus cannot be frozen or measured before operator review."""
    validate_qualification(protocol)
    review = corpus.get("operator_review")
    return (protocol["quality"].get("corpus_operator_review") == "approved_blanket"
            and isinstance(review, dict) and review.get("status") == "approved_blanket"
            and (review.get("date") == "2026-09-29" if protocol["schema_version"] == "qualification-protocol-v5"
                 else isinstance(review.get("date"), str) and bool(review["date"]))
            and bool(review.get("basis")) and bool(review.get("operator_note"))
            and corpus.get("preregistered_classification") == protocol["quality"].get("preregistered_classification")
            and sha(json.dumps(corpus, ensure_ascii=False, indent=2,
                               sort_keys=True).encode() + b"\n")
            == protocol["quality"]["corpus_sha256"])


def probe_plan(protocol: dict) -> list[dict]:
    validate_qualification(protocol)
    pair = qualification_pair(protocol)
    result = []
    for case in EXPECTED_FORMAT:
        for session in (1, 2):
            result.append({"series": "transport", "case": case, "session": session,
                           "limit_seconds": protocol["limits"]["probe_seconds"]})
    for size in (128, 512):
        result.append({"series": "large_output", "findings": size,
                       "limit_seconds": protocol["limits"]["probe_seconds"]})
    result.append({"series": "print_timeout", "count": 1,
                   "limit_seconds": protocol["limits"]["probe_seconds"]})
    for case in range(1, 7):
        row = {"series": "quality", "case": case, "providers": 2}
        if protocol["schema_version"] != "qualification-protocol-v5":
            row.update(candidate_provider=pair.candidate, reference_provider=pair.reference)
        result.append(row)
    for slot in ("reviewer", "final_reviewer"):
        result.append({"series": "canary", "slot": slot})
    return result


def qualification_cases(kind: str, provider: str,
                        pair: QualificationPair | None = None) -> tuple[str, ...]:
    """The frozen, ordered call set for one independently restartable series."""
    pair = pair or qualification_pair({"schema_version": "qualification-protocol-v5"})
    if kind == "transport" and provider == pair.candidate:
        return tuple(f"{case}:{session}" for case in EXPECTED_FORMAT for session in (1, 2))
    if kind == "large_output" and provider == pair.candidate:
        return ("128", "512")
    if kind == "print_timeout" and provider == pair.candidate:
        return ("T1",)
    if kind == "quality" and provider in pair.providers:
        return tuple(f"Q{n}" for n in range(1, 7))
    raise ValueError("unknown qualification series or provider")


def _qualified_slot(case_id: str, kind: str) -> str:
    if kind == "transport":
        return "final_reviewer" if case_id[:2] in {"F5", "F6"} else "reviewer"
    if kind == "large_output":
        return "final_reviewer"
    if kind == "quality":
        return "final_reviewer" if case_id in {"Q4", "Q6"} else "reviewer"
    return "reviewer"


def sanitize_evidence(value):
    """Remove account and credential strings before durable qualification evidence."""
    if isinstance(value, str):
        for expression in COMPILED.values():
            value = expression.sub("[REDACTED]", value)
        return value
    if isinstance(value, list):
        return [sanitize_evidence(item) for item in value]
    if isinstance(value, dict):
        return {key: sanitize_evidence(item) for key, item in value.items()}
    return value


def _sha_field(value: object, name: str) -> None:
    if not isinstance(value, str) or re.fullmatch(r"[0-9a-f]{64}", value) is None:
        raise ValueError(f"{name} must be a SHA-256 digest")


def append_qualification_attempt(series: dict, envelopes: dict, *, attempt: dict,
                                 envelope: dict) -> None:
    """Append a measured call and its sanitized envelope with a stable join key."""
    if series.get("schema_version") != "qualification-series-v1" or envelopes.get(
            "schema_version") != "qualification-envelopes-v1":
        raise ValueError("qualification evidence version differs")
    call_id = attempt["call_id"]
    if any(row["call_id"] == call_id for row in series["attempts"]):
        raise ValueError("call id already recorded; no single-call retakes")
    if any(row["call_id"] == call_id for row in envelopes["envelopes"]):
        raise ValueError("envelope id already recorded")
    clean = sanitize_evidence(envelope)
    serialized = canonical(clean)
    if any(expression.search(serialized) for expression in COMPILED.values()):
        raise ValueError("qualification envelope still contains a secret or email")
    row = sanitize_evidence(dict(attempt))
    row["envelope_sha256"] = sha(serialized.encode("utf-8"))
    series["attempts"].append(row)
    envelopes["envelopes"].append({"call_id": call_id, "envelope": clean})
    series["status"] = "in_progress"
    envelopes["status"] = "in_progress"


def _validate_attempt_bindings(row: dict, protocol: dict) -> None:
    required = ("call_id", "series_id", "kind", "provider", "role", "slot", "case_id",
                "request_id", "case_sha256", "request_sha256", "writer_sha256",
                "profile_sha256", "binary_sha256", "commit_sha", "probe_limit_seconds",
                "started_at", "duration_seconds", "status", "denials", "usage", "quota",
                "tag", "output_bytes", "envelope_sha256", "checks")
    if any(name not in row for name in required):
        raise ValueError("qualification attempt lacks a required binding or metric")
    if row["role"] != "reviewer" or row["slot"] != _qualified_slot(row["case_id"], row["kind"]):
        raise ValueError("qualification role or slot differs from frozen allocation")
    if row["case_id"] not in qualification_cases(row["kind"], row["provider"], qualification_pair(protocol)):
        raise ValueError("qualification case differs from frozen series")
    if row["probe_limit_seconds"] != protocol["limits"]["probe_seconds"]:
        raise ValueError("qualification probe limit changed")
    for name in ("case_sha256", "request_sha256", "writer_sha256", "profile_sha256",
                 "binary_sha256", "envelope_sha256"):
        _sha_field(row[name], name)
    if not re.fullmatch(r"[0-9a-f]{40}", row["commit_sha"]):
        raise ValueError("qualification commit is invalid")
    if not isinstance(row["request_id"], str) or not row["request_id"].startswith(
            "native-review-request-"):
        raise ValueError("qualification request id is invalid")
    if (type(row["duration_seconds"]) not in (float, int) or
        not math.isfinite(row["duration_seconds"]) or row["duration_seconds"] < 0):
        raise ValueError("qualification duration is invalid")
    if type(row["output_bytes"]) is not int or row["output_bytes"] < 0:
        raise ValueError("qualification output byte count is invalid")
    if not isinstance(row["checks"], dict) or not all(
            type(value) is bool for value in row["checks"].values()):
        raise ValueError("qualification checks are invalid")
    if not isinstance(row["denials"], list) or not isinstance(row["usage"], (dict, type(None))):
        raise ValueError("qualification denial or usage metric is invalid")
    if not isinstance(row["quota"], (dict, type(None))):
        raise ValueError("qualification quota metric is invalid")
    if row["status"] not in {"success", "technical_rejection"}:
        raise ValueError("qualification status is invalid")
    failure_kind = row.get("failure_kind")
    if failure_kind is not None and failure_kind not in {
            "quota", "auth", "network", "permission", "timeout", "binary", "process",
            "output", "runtime", "unclassified"}:
        raise ValueError("qualification failure kind is invalid")
    if row["status"] == "success" and failure_kind is not None:
        raise ValueError("successful qualification call cannot carry a failure kind")
    if row.get("retry_of") is not None and not re.fullmatch(r"[A-Za-z0-9_-]{1,80}", row["retry_of"]):
        raise ValueError("qualification retry reference is invalid")
    if row.get("contract_rejection") is not None and (
            row["status"] != "technical_rejection" or type(row.get("contract_retryable")) is not bool):
        raise ValueError("qualification contract rejection is invalid")
    feedback = row.get("retry_feedback")
    if feedback is not None and (not isinstance(feedback, dict) or set(feedback) != {
            "prior_invocation_id", "rejection_code", "correction_instruction"}):
        raise ValueError("qualification retry feedback is invalid")
    if not isinstance(row["tag"], str) or not row["tag"].strip() or len(row["tag"]) > 100:
        raise ValueError("qualification tag is invalid")
    started = datetime.fromisoformat(row["started_at"].replace("Z", "+00:00"))
    if started.tzinfo is None:
        raise ValueError("qualification start time requires a time zone")


def _frozen_source_digest(kind: str, case_id: str) -> str:
    if kind in {"transport", "print_timeout"}:
        case = case_id[:2] if kind == "transport" else "F2"
        files = strict_json(FORMAT_REPO_FIXTURE.read_bytes())["cases"][case]
        return digest({name: sha(content.encode()) for name, content in files.items()})
    if kind == "quality":
        corpus = strict_json((ROOT / "tests/fixtures/reviewer-quality-corpus-v1.json").read_bytes())
        files = next(item["candidate_files"] for item in corpus["cases"] if item["id"] == case_id)
        return digest({name: sha(content.encode()) for name, content in files.items()})
    with tempfile.TemporaryDirectory() as temporary:
        root = Path(temporary) / "repo"
        materialize_large_repo(int(case_id), root)
        return _source_digest(root)


def _large_output_checks(response, domain, count: int, paths: set[str]) -> dict:
    """Full-parsing checks of one size case, shared by recording and verification."""
    findings = response.get("new_findings", []) if isinstance(response, dict) else []
    stop = getattr(domain, "stop_request", None)
    return {"writer_and_domain": domain is not None,
            "finding_count": len(findings) == count,
            "unique_ids": len({item.get("finding_id") for item in findings}) == count,
            "scope": all(len(item.get("affected_paths", [])) == 1 and
                         item["affected_paths"][0] in paths for item in findings),
            "all_paths": {item["affected_paths"][0] for item in findings
                          if item.get("affected_paths")} == paths,
            "capacity_semantics": bool(domain is not None and (
                stop is None if count == 128 else
                getattr(stop, "rule_id", None) == "DISCOVERY_OUTPUT_LIMIT"))}


def _contract_retry_feedback(spec, previous_row: dict, previous_raw: dict):
    """Rebuild production's corrective feedback from the recorded rejected response."""
    from native_review_contract import NativeReviewErrorCode, native_review_retry_guidance
    from orchestrator_diagnostics import OrchestratorDiagnostic
    from native_review_request import NativeReviewRetryFeedback
    from rejected_response_shape import extract_rejected_native_response_shape

    envelope = previous_raw.get("envelope")
    structured = envelope.get("structured_output") if isinstance(envelope, dict) else None
    document = structured.get("result") if isinstance(structured, dict) else None
    code = NativeReviewErrorCode(previous_row["contract_rejection"])
    diagnostic = previous_row.get("orchestrator_diagnostic")
    guidance = native_review_retry_guidance(
        code, OrchestratorDiagnostic(diagnostic) if diagnostic else None, spec.context,
        extract_rejected_native_response_shape(document))
    return NativeReviewRetryFeedback(prior_invocation_id=previous_row["call_id"],
                                     rejection_code=code, correction_instruction=guidance)


def _reproduce_contract_rejection(document, bundle) -> str | None:
    """Replay the runtime's response checks; return the rejection code, if any."""
    from native_review_contract import (NativeReviewContractError,
                                        parse_bound_native_contract_result,
                                        validate_native_review_disposition_budget,
                                        validate_native_review_document)
    from native_review_request import (NativeReviewRequestError, NativeReviewRequestErrorCode,
                                       validate_native_review_provider_response)
    try:
        validate_native_review_disposition_budget(document, bundle.bound_context.context)
        validate_native_review_document(document)
        validate_native_review_provider_response(document, bundle)
        parse_bound_native_contract_result(document, bundle.bound_context)
    except NativeReviewContractError as exc:
        return exc.code.value
    except NativeReviewRequestError as exc:
        return "schema-invalid" if exc.code is NativeReviewRequestErrorCode.SCHEMA_INVALID else exc.code.value
    return None


_RUNTIME_DEADLINE = re.compile(r"^AgentProcessError: antigravity timed out after [0-9]+s\.$")


def _print_timeout_checks(status: str, failure_kind: str | None, technical_error: str | None,
                          stderr: str, has_result: bool,
                          capability: str = "antigravity") -> dict:
    """A timeout is safe when it is a technical rejection from AGY's print timeout or the runtime deadline."""
    from antigravity_adapter import classify_agy_stderr

    rejected = status == "technical_rejection" and technical_error is not None
    shape = ((classify_agy_stderr(stderr)[1] == "print-timeout"
              or bool(_RUNTIME_DEADLINE.fullmatch(technical_error or "")))
             if capability == "antigravity" else bool(technical_error))
    return {"print_timeout": rejected and failure_kind == "timeout" and shape,
            "no_valid_stop": rejected and not has_result}


def _verify_recorded_result(row: dict, raw: dict,
                            previous: tuple[dict, dict] | None = None,
                            protocol: dict | None = None) -> bool:
    """Rebuild the transmitted request and independently parse recorded output.

    Returns False for a quality call recorded against a superseded corpus revision:
    it stays visible and internally consistent but can never count as a pass.
    """
    from native_review_contract import parse_bound_native_contract_result
    from native_review_request import (build_native_review_request,
                                       validate_native_review_provider_response)
    from antigravity_adapter import classify_agy_stderr

    spec = build_qualification_spec(row["kind"], row["case_id"],
                                    run_id=f"qualification-{row['series_id']}-{row['call_id']}")
    contract_retry = previous is not None and _retry_reason(previous[0]) == "contract"
    if contract_retry:
        feedback = _contract_retry_feedback(spec, *previous)
        if row.get("retry_feedback") != {"prior_invocation_id": feedback.prior_invocation_id,
                                         "rejection_code": feedback.rejection_code.value,
                                         "correction_instruction": feedback.correction_instruction}:
            raise ValueError("recorded contract retry feedback differs from production guidance")
        spec = replace(spec, retry_feedback=feedback)
    elif row.get("retry_feedback") is not None:
        raise ValueError("retry feedback belongs only to a contract retry")
    pair = qualification_pair(protocol or {"schema_version": "qualification-protocol-v5"})
    bundle = build_native_review_request(spec, profile=pair.capability(row["provider"]))
    stored_request = raw.get("request_document")
    stored_writer = raw.get("writer_schema")
    if (not isinstance(stored_request, dict) or not isinstance(stored_writer, dict) or
        row["request_id"] != stored_request.get("request_id") or
        row["request_sha256"] != sha(canonical(stored_request).encode()) or
        row["writer_sha256"] != sha(canonical(stored_writer).encode())):
        raise ValueError("recorded request, writer or case binding is inconsistent")
    if row["case_sha256"] != _frozen_source_digest(row["kind"], row["case_id"]):
        if row["kind"] != "quality":
            raise ValueError("recorded request, writer or case binding is inconsistent")
        _sha_field(row["case_sha256"], "case_sha256")
        return False
    if (row["request_id"] != bundle.bound_context.request_id or
        row["request_sha256"] != sha(bundle.canonical_json.encode()) or
        row["writer_sha256"] != sha(bundle.provider_response_schema_json.encode())):
        if row["status"] != "success" or not all(row["checks"].values()):
            return  # A failed historical series remains auditable after a correction.
        raise ValueError("passing qualification was built with older request or writer code")
    envelope = raw.get("envelope")
    if not isinstance(envelope, dict):
        raise ValueError("recorded envelope is not an object")
    structured = envelope.get("structured_output")
    response = structured.get("result") if isinstance(structured, dict) else None
    if row["kind"] == "print_timeout":
        expected = _print_timeout_checks(row["status"], row.get("failure_kind"),
                                         raw.get("technical_error"), raw.get("stderr", ""),
                                         isinstance(response, dict), pair.capability(row["provider"]))
        if set(row["checks"]) != set(expected) or any(
                row["checks"][name] and not expected[name] for name in expected):
            raise ValueError("print timeout checks claim more than the recorded failure shows")
        return
    if row["status"] != "success":
        if raw.get("technical_error") is None:
            raise ValueError("failed attempt lacks its technical diagnosis")
        if row.get("contract_rejection") is not None:
            if _reproduce_contract_rejection(response, bundle) != row["contract_rejection"]:
                raise ValueError("recorded contract rejection is not reproducible")
            return
        if row.get("failure_kind") == "network" and pair.capability(row["provider"]) == "antigravity":
            from agent_adapters import AgentOutputError
            from antigravity_adapter import AntigravityTransport
            try:
                AntigravityTransport.envelope(canonical(envelope), raw.get("stderr", ""),
                                              int(raw.get("exit_code") or 0),
                                              canonical(stored_writer))
            except AgentOutputError as exc:
                if getattr(exc.kind_hint, "value", None) == "network":
                    return
            raise ValueError("recorded transient network failure is not reproducible")
        if row.get("failure_kind") == "timeout" and pair.capability(row["provider"]) == "antigravity":
            if (classify_agy_stderr(raw.get("stderr", ""))[1] != "print-timeout"
                    and row["duration_seconds"] < row["probe_limit_seconds"]):
                raise ValueError("recorded transient timeout is not reproducible")
        return
    if raw.get("technical_error") is not None or not isinstance(response, dict):
        raise ValueError("successful attempt lacks a sole result")
    if pair.capability(row["provider"]) == "antigravity" and not typed_equal(
            envelope.get("json_schema"), strict_json(bundle.provider_response_schema_json)):
        raise ValueError("transmitted AGY writer echo differs")
    validate_native_review_provider_response(response, bundle)
    domain = parse_bound_native_contract_result(response, bundle.bound_context)
    if row["kind"] == "transport":
        actual = validate_format_response(row["case_id"][:2], envelope,
            exit_code=int(raw.get("exit_code") or 0), stderr=raw.get("stderr", ""), bundle=bundle,
            transport_profile=pair.capability(row["provider"]))
        if actual["checks"] != row["checks"]:
            raise ValueError("transport checks differ from the recorded envelope")
    elif row["kind"] == "large_output":
        # A negative size result stays evaluable; only claims beyond the evidence are rejected.
        expected = _large_output_checks(response, domain, int(row["case_id"]),
                                        set(spec.authorized_paths))
        if set(row["checks"]) != set(expected) or any(
                row["checks"][name] and not expected[name] for name in expected):
            raise ValueError("large output checks claim more than the recorded output shows")
    elif row["kind"] == "quality" and not row["checks"].get("writer_and_domain"):
        raise ValueError("quality attempt did not pass the domain contract")


def validate_qualification_evidence(series: dict, envelopes: dict, protocol: dict) -> dict:
    """Judge complete series; retain failures and reject selective restarts."""
    validate_qualification(protocol)
    if series.get("schema_version") != "qualification-series-v1" or envelopes.get(
            "schema_version") != "qualification-envelopes-v1":
        raise ValueError("qualification evidence version differs")
    by_envelope = {row["call_id"]: row["envelope"] for row in envelopes["envelopes"]}
    if len(by_envelope) != len(envelopes["envelopes"]):
        raise ValueError("duplicate qualification envelope")
    attempts = series["attempts"]
    if len({row["call_id"] for row in attempts}) != len(attempts) or set(by_envelope) != {
            row["call_id"] for row in attempts}:
        raise ValueError("qualification attempts and envelopes differ")
    groups: dict[tuple[str, str], list[dict]] = {}
    series_owners: dict[str, tuple[str, str]] = {}
    superseded: set[str] = set()
    for row in attempts:
        _validate_attempt_bindings(row, protocol)
        owner = (row["kind"], row["provider"])
        if row["series_id"] in series_owners and series_owners[row["series_id"]] != owner:
            raise ValueError("series id is reused across provider or campaign kind")
        series_owners[row["series_id"]] = owner
        clean = by_envelope[row["call_id"]]
        if sanitize_evidence(clean) != clean or sha(canonical(clean).encode()) != row["envelope_sha256"]:
            raise ValueError("qualification envelope is unsanitized or changed")
        prior = next((item for item in attempts if item["call_id"] == row.get("retry_of")), None)
        if _verify_recorded_result(row, clean, None if prior is None else (
                prior, by_envelope[prior["call_id"]]), protocol) is False:
            superseded.add(row["call_id"])
        groups.setdefault((row["kind"], row["provider"]), []).append(row)
    verdicts = {}
    for key, rows in groups.items():
        expected = qualification_cases(*key, qualification_pair(protocol))
        by_series: dict[str, list[dict]] = {}
        for row in rows:
            by_series.setdefault(row["series_id"], []).append(row)
        earlier: list[dict] = []
        for series_id, calls in by_series.items():
            chains = _case_chains(calls)
            observed = tuple(chain[0]["case_id"] for chain in chains)
            if observed != expected[:len(observed)]:
                raise ValueError(f"{series_id}: repeated, skipped or reordered call")
            if len({row["request_id"] for row in calls}) != len(calls):
                raise ValueError(f"{series_id}: sessions reused a request identity")
            for binding in ("profile_sha256", "binary_sha256", "commit_sha"):
                if len({row[binding] for row in calls}) != 1:
                    raise ValueError(f"{series_id}: {binding} changed inside a frozen series")
            session_ids = [row.get("session_id") for row in calls if row.get("session_id")]
            if len(session_ids) != len(set(session_ids)):
                raise ValueError(f"{series_id}: provider session identity was reused")
            if earlier:
                first = calls[0]
                if not first.get("restart_diagnosis") or not first.get("restart_change"):
                    raise ValueError("restart lacks diagnosis and concrete change")
                if first["commit_sha"] == earlier[-1][0][0]["commit_sha"] and first[
                        "profile_sha256"] == earlier[-1][0][0]["profile_sha256"] and first[
                        "writer_sha256"] == earlier[-1][0][0]["writer_sha256"] and first[
                        "binary_sha256"] == earlier[-1][0][0]["binary_sha256"]:
                    raise ValueError("restart did not change commit, profile or writer")
            call_pass = []
            for row in calls:
                checks = row["checks"]
                if key[0] == "print_timeout":
                    passed = (row["status"] == "technical_rejection" and
                              checks.get("print_timeout") is True and
                              checks.get("no_valid_stop") is True)
                else:
                    passed = row["status"] == "success" and bool(checks) and all(checks.values())
                call_pass.append(passed)
            outcome = dict(zip((row["call_id"] for row in calls), call_pass))
            stale = any(row["call_id"] in superseded for row in calls)
            case_pass = [outcome[chain[-1]["call_id"]] for chain in chains]
            if earlier and len(earlier[-1][1]) == len(expected) and all(earlier[-1][1]):
                raise ValueError("successful series cannot be restarted")
            earlier.append((calls, [ok and not stale for ok in case_pass]))
            verdicts[series_id] = {"kind": key[0], "provider": key[1],
                                   "passed": len(chains) == len(expected) and all(case_pass) and not stale,
                                   "incomplete": len(chains) != len(expected), "calls": len(calls),
                                   "superseded_corpus": stale,
                                   "production_retries": sum(len(chain) - 1 for chain in chains),
                                   "contract_rejections": [row["call_id"] for row in calls
                                                           if row.get("contract_rejection")],
                                   "transient_failures": [row["call_id"] for row in calls
                                                          if row["status"] == "technical_rejection"
                                                          and row.get("failure_kind") in PRODUCTION_RETRY_KINDS],
                                   "failed_cases": [chain[0]["case_id"] for chain, ok in zip(chains, case_pass) if not ok]}
    for kind, provider in _series_keys(qualification_pair(protocol)):
        if (kind, provider) not in groups:
            verdicts[f"pending-{kind}-{provider}"] = {
                "kind": kind, "provider": provider, "passed": False,
                "incomplete": True, "calls": 0, "superseded_corpus": False, "production_retries": 0,
                "contract_rejections": [], "transient_failures": [], "failed_cases": []}
    return verdicts


def timeout_proposal(series: dict, verdicts: dict, protocol: dict) -> dict:
    """Propose, never install, the finite measured timeout."""
    eligible = {name for name, result in verdicts.items() if result["passed"] and
                result["provider"] == qualification_pair(protocol).candidate and
                result["kind"] in {"transport", "large_output", "quality"}}
    measurements = [row for row in series["attempts"]
                    if row["series_id"] in eligible and row["status"] == "success"]
    if not any(row["kind"] == "large_output" and row["case_id"] == "512" for row in measurements):
        raise ValueError("512-finding success is required for timeout proposal")
    maximum = max(row["duration_seconds"] for row in measurements)
    proposal = math.ceil(1.5 * maximum / 60) * 60
    return {"maximum_success_seconds": maximum, "factor": 1.5,
            "round_up_seconds": 60, "suggested_seconds": proposal,
            "probe_limit_seconds": protocol["limits"]["probe_seconds"],
            "censored_by_limit": any(row["duration_seconds"] >= protocol["limits"]["probe_seconds"]
                                     for row in series["attempts"]),
            "profile_zero_allowed": True}


def size_override(decisions: dict | None, verdicts: dict,
                  pair: QualificationPair | None = None) -> dict | None:
    """Accept only the recorded operator exception for the measured 128 case."""
    if decisions is None:
        return None
    if decisions.get("schema_version") != "operator-decisions-v1":
        raise ValueError("operator decisions version differs")
    matches = [row for row in decisions.get("decisions", []) if row.get("id") == "size-override"]
    if not matches:
        return None
    if len(matches) != 1:
        raise ValueError("duplicate size override")
    row = matches[0]
    pair = pair or qualification_pair({"schema_version": "qualification-protocol-v5"})
    series_id = row.get("series")
    if (row.get("decision") != "approve_experimental_despite_failed_size_case"
        or not isinstance(series_id, str) or row.get("failed_case") != "128"
        or (pair.candidate == LEGACY_CANDIDATE and series_id != f"{LEGACY_CANDIDATE}-large-s1")
        or row.get("not_run") != ["512"]
        or not isinstance(row.get("documented_weakness"), str)
        or not row["documented_weakness"].strip()
        or verdicts.get(series_id, {}).get("failed_cases") != ["128"]
        or verdicts[series_id].get("kind") != "large_output"
        or verdicts[series_id].get("provider") != pair.candidate):
        raise ValueError("size override differs from the recorded failed case")
    return {"series": row["series"], "failed_case": row["failed_case"],
            "documented_weakness": row["documented_weakness"]}


def qualification_summary(series: dict, envelopes: dict, protocol: dict,
                          quality_results: dict, decisions: dict | None = None) -> dict:
    verdicts = validate_qualification_evidence(series, envelopes, protocol)
    pair = qualification_pair(protocol)
    corpus = strict_json((ROOT / "tests/fixtures/reviewer-quality-corpus-v1.json").read_bytes())
    if not qualification_ready(protocol, corpus):
        raise PermissionError("quality corpus awaits operator review and matching frozen digest")
    quality = grade_quality(quality_results, corpus, protocol)
    verify_quality_mapping(quality_results, series, envelopes, protocol)
    latest = {}
    for name, row in verdicts.items():
        latest[(row["kind"], row["provider"])] = row
    required = all(latest.get(key, {}).get("passed") for key in (
        ("transport", pair.candidate), ("print_timeout", pair.candidate),
        ("quality", pair.candidate), ("quality", pair.reference)))
    override = size_override(decisions, verdicts, pair)
    size_passed = latest.get(("large_output", pair.candidate), {}).get("passed", False)
    qualified = bool(required and quality[pair.candidate]["passed"] and
                     (quality[pair.reference]["passed"] or
                      quality_results.get(_reference_decision_key(protocol)) == "approve_experimental")
                     and (size_passed or override))
    summary = {"series": verdicts, "quality": quality,
               "qualified_for_canary": qualified,
               "size_qualification": "passed" if size_passed else "operator_override" if override else "failed"}
    if override:
        summary["size_override"] = override
    try:
        summary["timeout_proposal"] = timeout_proposal(series, verdicts, protocol)
    except ValueError as exc:
        summary["timeout_proposal_pending"] = str(exc)
    return summary


def domain_projection(bundle, result) -> dict:
    """Compare reviewed facts while retaining transport identity separately."""
    request = bundle.document
    review = request["review_contract"]
    findings = tuple((item.finding_id, item.finding_class.value, item.status.value,
                      item.affected_paths) for item in result.findings)
    closures = tuple((finding_id, closure.kind.value) for finding_id, closure in result.finding_closures)
    if result.stopped:
        transition = "stop"
    elif request["review_kind"] == "final_review":
        transition = "final_discovery"
    elif result.approval:
        transition = "review_approved"
    else:
        transition = "review_denied"
    return {
        "review_kind": request["review_kind"], "work_unit_id": request["work_unit_id"],
        "operation": request["operation"], "target_branch": request["target_branch"],
        "base_commit": request["base_commit"], "current_fingerprint": request["current_fingerprint"],
        "authorized_paths": request["authorized_paths"],
        "acceptance_criteria": request["acceptance_criteria"],
        "evidence": tuple((item["evidence_id"], item["kind"], item["sha256"],
                           item.get("content"), item.get("content_ref"))
                          for item in request["evidence_manifest"]),
        "known_findings": review["previous_findings"],
        "round_number": review["round_number"],
        "allow_new_findings": review["allow_new_findings"],
        "decision": result.approval, "stopped": result.stopped,
        "stop_rule": result.stop_request.rule_id if result.stop_request else None,
        "findings": findings, "closures": closures,
        "scan_complete": result.scan_complete, "transition": transition,
    }


def export_rater_packet(assessment: dict, mapping: dict, corpus: dict,
                        protocol: dict, rubric: dict) -> dict:
    if not qualification_ready(protocol, corpus):
        raise PermissionError("quality corpus is not approved at its frozen digest")
    if rubric.get("schema_version") != "quality-rubric-v1" or rubric.get("protocol_version") not in {
            protocol["schema_version"], "qualification-protocol-v5"}:
        raise ValueError("quality rubric version differs")
    if assessment.get("schema_version") != "blind-assessment-v1" or mapping.get("schema_version") != "blind-mapping-v1" or mapping.get("seed") != protocol["quality"]["blind_seed"]:
        raise ValueError("blind input version or seed differs")
    responses, secret = assessment.get("responses", []), mapping.get("mapping", {})
    ids = [row.get("id") for row in responses]
    if len(ids) != 12 or len(set(ids)) != 12 or set(ids) != set(secret):
        raise ValueError("twelve matched blind responses required")
    cases = {row["id"]: row for row in corpus["cases"]}
    if sorted(secret[row]["case"] for row in ids) != sorted(list(cases) * 2):
        raise ValueError("two responses per quality case required")
    if {(row["provider"], row["case"]) for row in secret.values()} != {
            (provider, case) for provider in qualification_pair(protocol).providers for case in cases}:
        raise ValueError("each provider must have every quality case once")
    entries = []
    for row in responses:
        identifier, bound = row["id"], secret[row["id"]]
        if bound["provider"] not in qualification_pair(protocol).providers:
            raise ValueError("unknown provider in secret mapping")
        encoded = json.dumps(row["content"], ensure_ascii=False, sort_keys=True).encode()
        if sha(encoded) != bound["content_sha256"]:
            raise ValueError("blind content digest differs")
        case = cases[bound["case"]]
        changed = [name for name in case["seed_files"]
                   if case["seed_files"][name] != case["candidate_files"][name]]
        if len(changed) != 1:
            raise ValueError("quality case lacks a unique affected path")
        entries.append({"id": identifier, "case": case["id"], "response": row["content"],
                        "ground_truth": {"defect": case["defect"], "critical": case["critical"],
                            "factual_finding": case["factual_finding"],
                            "allowed_alternatives": case["allowed_alternatives"],
                            "affected_path": changed[0], "diff": case["patch"]}})
    base = {"schema_version": "quality-rater-packet-v1", "blind_seed": mapping["seed"],
            "corpus_sha256": protocol["quality"]["corpus_sha256"],
            "rubric": rubric, "responses": entries,
            "preregistered_classification": corpus["preregistered_classification"]}
    return {**base, "packet_sha256": digest(base)}


def validate_rating(rating: dict, packet: dict, rater: str) -> None:
    if set(rating) != {"schema_version", "rater", "packet_sha256", "judgments"} or rating["schema_version"] != "quality-rating-v1" or rating["rater"] != rater or rating["packet_sha256"] != packet["packet_sha256"]:
        raise ValueError("rating header differs from the blind packet")
    responses, rows = packet["responses"], rating["judgments"]
    if not isinstance(rows, list) or any(not isinstance(row, dict) for row in rows):
        raise ValueError("rating judgments must be objects")
    ids = [entry["id"] for entry in responses]
    rated_ids = [row.get("id") for row in rows]
    if len(set(rated_ids)) != len(rated_ids) or rated_ids != [identifier for identifier in ids if identifier in rated_ids]:
        raise ValueError("rating IDs must be unique and in packet order")
    entries = {entry["id"]: entry for entry in responses}
    for row in rows:
        entry = entries[row["id"]]
        criteria = {"unfounded_findings", "invented_critical"}
        if entry["ground_truth"]["defect"]:
            criteria.add("defect_found")
        if (set(row) != {"id", "reasons"} | criteria
                or type(row["unfounded_findings"]) is not int or row["unfounded_findings"] < 0
                or type(row["invented_critical"]) is not bool
                or ("defect_found" in criteria and type(row["defect_found"]) is not bool)
                or not isinstance(row["reasons"], dict) or set(row["reasons"]) != criteria
                or any(not isinstance(value, str) or not value.strip()
                       for value in row["reasons"].values())):
            raise ValueError(f"invalid criterion or reason for {entry['id']}")
        findings = entry["response"].get("new_findings", [])
        if not isinstance(findings, list) or row["unfounded_findings"] > len(findings):
            raise ValueError(f"more unfounded findings than findings for {entry['id']}")
        if not entry["ground_truth"]["defect"]:
            if row["unfounded_findings"] != len(findings):
                raise ValueError(f"every clean-case finding is unfounded for {entry['id']}")
            if row["invented_critical"] != any(item.get("finding_class") == "BLOCKER" for item in findings):
                raise ValueError(f"clean-case invented critical classification differs for {entry['id']}")


def render_rater_prompt(packet: dict, protocol: dict) -> str:
    if packet.get("packet_sha256") != digest({k: v for k, v in packet.items() if k != "packet_sha256"}):
        raise ValueError("rater packet digest differs")
    rubric = packet.get("rubric", {})
    if sha((json.dumps(rubric, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode()) != protocol["quality"]["rubric_sha256"]:
        raise ValueError("quality rubric digest differs")
    prompt = (rubric["codex_prompt"] if protocol["schema_version"] == "qualification-protocol-v5"  # allowlist:provider -- certification data: unchanged historical prompt key
              else rubric["rater_prompt"])  # allowlist:provider -- certification data: historical prompt field
    return prompt + "\n\nBlindpaket:\n" + json.dumps(packet, ensure_ascii=False, indent=2) + "\n"  # allowlist:provider -- certification data: independent blind quality rating


def combine_quality_ratings(packet: dict, mapping: dict, ratings: dict,
                            corpus: dict, protocol: dict,
                            operator_answers: dict | None = None) -> dict:
    if not qualification_ready(protocol, corpus):
        raise PermissionError("quality corpus is not approved at its frozen digest")
    raters = qualification_raters(protocol)
    if set(ratings) != set(raters):
        raise ValueError("two independent raters required")
    if packet.get("packet_sha256") != digest({k: v for k, v in packet.items() if k != "packet_sha256"}):
        raise ValueError("rater packet digest differs")
    if packet.get("rubric") is None or sha((json.dumps(packet["rubric"], ensure_ascii=False,
            indent=2, sort_keys=True) + "\n").encode()) != protocol["quality"]["rubric_sha256"]:
        raise ValueError("quality rubric digest differs")
    assessment = {"schema_version": "blind-assessment-v1", "responses": [
        {"id": row["id"], "content": row["response"]} for row in packet["responses"]]}
    if export_rater_packet(assessment, mapping, corpus, protocol, packet["rubric"]) != packet:
        raise ValueError("rater packet differs from frozen ground truth")
    for name, rating in ratings.items():
        validate_rating(rating, packet, name)
    if any(len(rating["judgments"]) != len(packet["responses"]) for rating in ratings.values()):
        if operator_answers is not None:
            raise ValueError("operator answers require two complete ratings")
        return {"schema_version": "quality-results-v1", "status": "incomplete",
                "blind_seed": packet["blind_seed"], "corpus_sha256": packet["corpus_sha256"],
                "packet_sha256": packet["packet_sha256"], "input_digests": {
                    "ratings": {name: digest(value) for name, value in ratings.items()},
                    "mapping": digest(mapping)}, "ratings": ratings, "agreement": [],
                "operator_questions": [], "operator_decisions": [], "judgments": [],
                "judgments_frozen_sha256": None, "judgments_frozen_at": None,
                "unblinded_at": None, "mapping": {}, _reference_decision_key(protocol): None}
    if set(mapping.get("mapping", {})) != {row["id"] for row in packet["responses"]}:
        raise ValueError("blind mapping differs")
    by_rater = {name: {row["id"]: row for row in rating["judgments"]}
                for name, rating in ratings.items()}
    questions, agreement, final = [], [], []
    for entry in packet["responses"]:
        identifier = entry["id"]
        values, reasons = {}, {}
        criteria = (["defect_found"] if entry["ground_truth"]["defect"] else []) + ["unfounded_findings", "invented_critical"]
        for criterion in criteria:
            left = by_rater[raters[0]][identifier][criterion]  # allowlist:provider -- certification data: independent blind quality rating
            right = by_rater[raters[1]][identifier][criterion]
            same = left == right
            # The rule counts unfounded findings only in clean cases; a defect-case count
            # dispute keeps the higher count instead of asking the operator.
            relevant = criterion != "unfounded_findings" or not entry["ground_truth"]["defect"]
            agreement.append({"id": identifier, "criterion": criterion, "agreed": same,
                              "rule_relevant": relevant,
                              raters[0]: left, raters[1]: right})  # allowlist:provider -- certification data: independent blind quality rating
            if same:
                values[criterion] = left
            elif not relevant:
                values[criterion] = max(left, right)
            else:
                if criterion == "defect_found":
                    wording = f"Hat Antwort {identifier} den Fehler ‚{entry['ground_truth']['factual_finding']}‘ im betroffenen Pfad gefunden? Ja/Nein"
                    yes_value, no_value = True, False
                elif criterion == "invented_critical":
                    wording = f"Enthält Antwort {identifier} einen erfundenen kritischen Befund? Ja/Nein"
                    yes_value, no_value = True, False
                else:
                    wording = f"Enthält Antwort {identifier} genau {left} unbegründete Befunde? Ja/Nein"
                    yes_value, no_value = left, right
                questions.append({"id": identifier, "criterion": criterion,
                                  "question": wording, "yes_value": yes_value,
                                  "no_value": no_value})
            reasons[criterion] = {name: by_rater[name][identifier]["reasons"][criterion]
                                  for name in raters}  # allowlist:provider -- certification data: independent blind quality rating
        final.append({"id": identifier, **values, "reasons": reasons})
    answers = operator_answers or {"schema_version": "quality-operator-answers-v1", "answers": []}
    if set(answers) != {"schema_version", "answers"} or answers["schema_version"] != "quality-operator-answers-v1" or not isinstance(answers["answers"], list):
        raise ValueError("invalid operator answers")
    answer_rows = answers["answers"]
    if len(answer_rows) > len(questions) or any(set(row) != {"id", "criterion", "answer"} or type(row["answer"]) is not bool for row in answer_rows):
        raise ValueError("invalid operator answer shape")
    answer_keys = [(row["id"], row["criterion"]) for row in answer_rows]
    question_keys = [(row["id"], row["criterion"]) for row in questions]
    if len(set(answer_keys)) != len(answer_keys) or not set(answer_keys) <= set(question_keys):
        raise ValueError("operator answer does not match a disputed criterion")
    decided = {(row["id"], row["criterion"]): row["answer"] for row in answer_rows}
    for row in final:
        for question in questions:
            key = question["id"], question["criterion"]
            if row["id"] == question["id"] and key in decided:
                row[question["criterion"]] = question["yes_value"] if decided[key] else question["no_value"]
    complete = len(decided) == len(questions)
    now = datetime.now(timezone.utc).isoformat()
    return {"schema_version": "quality-results-v1", "status": "complete" if complete else "incomplete",
            "blind_seed": packet["blind_seed"], "corpus_sha256": packet["corpus_sha256"],
            "packet_sha256": packet["packet_sha256"], "input_digests": {
                "ratings": {name: digest(value) for name, value in ratings.items()},
                "mapping": digest(mapping)},
            "ratings": ratings, "agreement": agreement, "operator_questions": questions,
            "operator_decisions": answer_rows, "judgments": final if complete else [],
            "judgments_frozen_sha256": digest(final) if complete else None,
            "judgments_frozen_at": now if complete else None,
            "unblinded_at": datetime.now(timezone.utc).isoformat() if complete else None,
            "mapping": mapping["mapping"] if complete else {},
            _reference_decision_key(protocol): None}


def grade_quality(results: dict, corpus: dict, protocol: dict) -> dict:
    """Apply the frozen absolute rule separately after blind judgments are fixed."""
    if results.get("schema_version") != "quality-results-v1":
        raise ValueError("quality result version differs")
    if results.get("blind_seed") != protocol["quality"]["blind_seed"]:
        raise ValueError("blind seed differs")
    if results.get("corpus_sha256") != protocol["quality"]["corpus_sha256"]:
        raise ValueError("quality corpus digest differs")
    if results.get("status") != "complete":
        return {provider: {"passed": False, "incomplete": True}
                for provider in qualification_pair(protocol).providers}
    raters = qualification_raters(protocol)
    if set(results.get("ratings", {})) != set(raters):  # allowlist:provider -- certification data: independent blind quality rating
        raise ValueError("both independent ratings required")
    if any(results.get("packet_sha256") != results["ratings"][name].get("packet_sha256") for name in raters):  # allowlist:provider -- certification data: independent blind quality rating
        raise ValueError("rating packet digests differ")
    if not results.get("agreement") or results.get("operator_decisions") is None:
        raise ValueError("agreement and operator decisions required")
    if results.get("input_digests", {}).get("ratings") != {
            name: digest(rating) for name, rating in results["ratings"].items()}:
        raise ValueError("rating input digests differ")
    judgments, mapping = results["judgments"], results["mapping"]
    if results.get("judgments_frozen_sha256") != digest(judgments):
        raise ValueError("blind judgments are not digest-frozen")
    frozen = datetime.fromisoformat(results["judgments_frozen_at"].replace("Z", "+00:00"))
    unblinded = datetime.fromisoformat(results["unblinded_at"].replace("Z", "+00:00"))
    if frozen.tzinfo is None or unblinded.tzinfo is None or not frozen < unblinded:
        raise ValueError("judgments must be frozen before unblinding")
    if len(judgments) != 12 or len(mapping) != 12 or set(mapping) != {
            row["id"] for row in judgments} or len({row["id"] for row in judgments}) != 12:
        raise ValueError("twelve blind judgments and exact mapping required")
    rated = {name: {row["id"]: row for row in rating["judgments"]}
             for name, rating in results["ratings"].items()}
    decisions = {(row["id"], row["criterion"]): row["answer"]
                 for row in results["operator_decisions"]}
    questions = {(row["id"], row["criterion"]): row
                 for row in results.get("operator_questions", [])}
    if len(decisions) != len(results["operator_decisions"]) or len(questions) != len(results.get("operator_questions", [])) or set(decisions) != set(questions):
        raise ValueError("operator decisions are incomplete or duplicated")
    cases = {case["id"]: case for case in corpus["cases"]}
    for item in mapping.values():
        if not isinstance(item, dict) or set(item) != {"provider", "case", "content_sha256", "input_sha256"} or item["case"] not in cases:
            raise ValueError("blind mapping is incomplete")
        _sha_field(item["content_sha256"], "content_sha256")
        _sha_field(item["input_sha256"], "input_sha256")
    for row in judgments:
        for criterion in ("defect_found", "unfounded_findings", "invented_critical"):
            if criterion not in row:
                continue
            left = rated[raters[0]][row["id"]][criterion]  # allowlist:provider -- certification data: independent blind quality rating
            right = rated[raters[1]][row["id"]][criterion]
            key = row["id"], criterion
            relevant = criterion != "unfounded_findings" or not cases[mapping[row["id"]]["case"]]["defect"]
            if left == right or not relevant:
                expected = left if left == right else max(left, right)
                if key in questions:
                    raise ValueError("question exists for an agreed or rule-irrelevant criterion")
            else:
                question = questions.get(key)
                if question is None:
                    raise ValueError("disagreement lacks operator question")
                expected = question["yes_value"] if decisions[key] else question["no_value"]
                if {question["yes_value"], question["no_value"]} != {left, right}:
                    raise ValueError("operator alternatives differ from ratings")
            if row[criterion] != expected:
                raise ValueError("final judgment differs from raters or operator")
    scores = {}
    for provider in qualification_pair(protocol).providers:
        selected = [(row, cases[mapping[row["id"]]["case"]]) for row in judgments
                    if mapping[row["id"]]["provider"] == provider]
        if {case["id"] for _, case in selected} != set(cases):
            raise ValueError("each provider must have exactly Q1–Q6")
        for row, case in selected:
            if ((case["defect"] and type(row.get("defect_found")) is not bool) or
                (not case["defect"] and "defect_found" in row) or
                type(row.get("invented_critical")) is not bool or
                type(row.get("unfounded_findings")) is not int or
                row["unfounded_findings"] < 0 or
                not isinstance(row.get("reasons"), dict) or
                not all(isinstance(value, dict) and set(value) == set(raters)  # allowlist:provider -- certification data: independent blind quality rating
                        and all(isinstance(reason, str) and reason.strip()
                                for reason in value.values())
                        for value in row["reasons"].values())):
                raise ValueError("blind judgment lacks a reasoned classification")
        critical = sum(row["defect_found"] for row, case in selected if case["critical"])
        defects = sum(row["defect_found"] for row, case in selected if case["defect"])
        false_positives = sum(row["unfounded_findings"] for row, case in selected if not case["defect"])
        invented = sum(row["invented_critical"] for row, _ in selected)
        passed = (critical == protocol["quality_rule"]["critical_required"] and
                  defects >= protocol["quality_rule"]["defects_required"] and
                  false_positives <= protocol["quality_rule"]["clean_false_positives_max"] and
                  invented <= protocol["quality_rule"]["invented_critical_max"])
        scores[provider] = {"passed": passed, "critical": critical, "defects": defects,
                            "clean_false_positives": false_positives,
                            "invented_critical": invented}
    pair = qualification_pair(protocol)
    special = results.get(_reference_decision_key(protocol))
    if scores[pair.candidate]["passed"] and not scores[pair.reference]["passed"]:
        if special not in {None, "approve_experimental", "deny_experimental"}:
            raise ValueError("invalid operator decision for weaker reference")
    elif special is not None:
        raise ValueError("special decision is permitted only for a weaker reference")
    return scores


def materialize_large_repo(count: int, destination: Path) -> tuple[str, ...]:
    """Create independent boundary defects; hidden proof is returned nowhere."""
    if count not in (128, 512):
        raise ValueError("only frozen 128/512 cases are supported")
    destination.mkdir(parents=True, exist_ok=False)
    paths = []
    for number in range(1, count + 1):
        relative = f"src/boundary_{number:04d}.py"
        path = destination / relative
        path.parent.mkdir(exist_ok=True)
        path.write_text(
            f"LIMIT = {number}\n\n"
            "def eligible(value: int) -> bool:\n"
            "    # The inclusive limit is required by the public contract.\n"
            "    return 0 <= value < LIMIT\n", encoding="utf-8")
        paths.append(relative)
    (destination / "README.md").write_text(
        f"Each of the {count} modules independently promises eligible(LIMIT) is true. "
        "The boundary expression in each module violates that promise. "
        "Report one finding per module with its path; no proof tests are in this snapshot.\n",
        encoding="utf-8")
    public = destination / "tests/test_public.py"
    public.parent.mkdir(exist_ok=True)
    public.write_text(
        "from importlib import import_module\n\n"
        "def test_every_boundary_module_imports():\n"
        f"    for number in range(1, {count + 1}):\n"
        "        module = import_module(f'src.boundary_{number:04d}')\n"
        "        assert module.eligible(0)\n", encoding="utf-8")
    return tuple(paths)


def materialize_quality_repo(case_id: str, destination: Path) -> tuple[str, ...]:
    corpus = strict_json((ROOT / "tests/fixtures/reviewer-quality-corpus-v1.json").read_bytes())
    case = next((row for row in corpus["cases"] if row["id"] == case_id), None)
    if case is None:
        raise ValueError("unknown quality case")
    destination.mkdir(parents=True, exist_ok=False)
    for relative, content in case["candidate_files"].items():
        path = destination / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
    return tuple(sorted(case["candidate_files"]))


def _source_digest(root: Path) -> str:
    rows = {}
    for path in sorted(root.rglob("*")):
        if path.is_symlink():
            raise ValueError("qualification snapshot contains a symlink")
        if path.is_file():
            rows[path.relative_to(root).as_posix()] = sha(path.read_bytes())
    return digest(rows)


def verify_qualification_source(kind: str, case_id: str, source_repo: Path) -> str:
    """Bind the actual source tree and exclude every hidden proof file."""
    if kind == "quality":
        corpus = strict_json((ROOT / "tests/fixtures/reviewer-quality-corpus-v1.json").read_bytes())
        case = next(row for row in corpus["cases"] if row["id"] == case_id)
        expected = case["candidate_files"]
        actual = {path.relative_to(source_repo).as_posix(): path.read_text(encoding="utf-8")
                  for path in source_repo.rglob("*") if path.is_file() and not path.is_symlink()}
        if actual != expected or case["proof"]["hidden_file"] in actual:
            raise ValueError("quality source differs from frozen candidate or exposes hidden proof")
    elif kind in {"transport", "print_timeout"}:
        fixture = strict_json(FORMAT_REPO_FIXTURE.read_bytes())["cases"][
            case_id[:2] if kind == "transport" else "F2"]
        expected = fixture
        actual = {path.relative_to(source_repo).as_posix(): path.read_text(encoding="utf-8")
                  for path in source_repo.rglob("*") if path.is_file() and not path.is_symlink()}
        if actual != expected:
            raise ValueError("transport source differs from frozen fixture")
    elif kind == "large_output":
        names = {path.relative_to(source_repo).as_posix() for path in source_repo.rglob("*")
                 if path.is_file()}
        expected = {"README.md", "tests/test_public.py"} | {
            f"src/boundary_{n:04d}.py" for n in range(1, int(case_id) + 1)}
        if names != expected or any("proof" in name or "test_" in name and name != "tests/test_public.py" for name in names):
            raise ValueError("large output source is incomplete or exposes a proof")
    return _source_digest(source_repo)


def large_hidden_proof(count: int) -> str:
    """Operator-side proof source, never included in review snapshots or requests."""
    if count not in (128, 512):
        raise ValueError("unsupported size")
    return "".join(
        f"def test_boundary_{number:04d}():\n"
        f"    from src.boundary_{number:04d} import LIMIT, eligible\n"
        "    assert eligible(LIMIT)\n\n"
        for number in range(1, count + 1)
    )


def _with_production_capacity_criterion(spec):
    """Add the capacity criterion that production appends to every final review."""
    from native_review_request import NativeReviewKind
    from workflow_requests import final_review_discovery_capacity_criterion

    if spec.review_kind is not NativeReviewKind.FINAL_REVIEW:
        return spec
    criterion = final_review_discovery_capacity_criterion(spec.context.max_new_findings)
    return replace(spec, acceptance_criteria=tuple(
        dict.fromkeys((*spec.acceptance_criteria, criterion))))


def build_qualification_spec(kind: str, case_id: str, *, run_id: str):
    """Build the same NativeReviewRequestSpec used by production adapters.

    Transport and quality final reviews carry the production capacity criterion.
    The large-output stress case keeps its protocol-defined full-parsing request.
    """
    from contracts import (AgentRole, ApprovalMarker, FindingClass, FindingOrigin,
                           FindingRecord, FindingStatus, ValidationAttestation,
                           ValidationCommandSpec, ValidationRecord, ValidationStatus)
    from native_review_request import NativeReviewEvidenceInput, NativeReviewKind, NativeReviewRequestSpec

    if kind == "transport":
        case = case_id.split(":", 1)[0]
        spec = spec_for(case)
        context = replace(spec.context, run_id=run_id)
        return _with_production_capacity_criterion(replace(spec, context=context))
    if kind == "large_output":
        count = int(case_id)
        template = spec_for("F5")
        fingerprint = sha(f"large-{count}".encode())
        command = "python3 -m pytest tests/test_public.py -q -p no:cacheprovider"
        attestation = ValidationAttestation(
            f"qualification-large-{count}", fingerprint, (command,),
            (ValidationRecord(ValidationStatus.PASS, command, 0, "1 passed"),),
            sha(b"1 passed"), "1 passed",
            command_specs=(ValidationCommandSpec(argv=tuple(command.split())),))
        context = replace(template.context, run_id=run_id,
                          work_unit_id=f"boundary-{count}", max_new_findings=count + 1 if count == 128 else 512,
                          diff_fingerprint=fingerprint,
                          validation_attestation=attestation,
                          test_files=("tests/test_public.py",), test_changes_approved=True)
        evidence = NativeReviewEvidenceInput(
            "scenario", "review_evidence",
            f"There are {count} independent boundary defects. Each src/boundary_NNNN.py "
            "has an inclusive LIMIT contract and a strict comparison. Report each distinct "
            "module finding with its affected path and evidence; do not coalesce modules.")
        return NativeReviewRequestSpec(
            context, NativeReviewKind.FINAL_REVIEW, "qualification/large-output", "b" * 40,
            tuple(f"src/boundary_{n:04d}.py" for n in range(1, count + 1)),
            (f"Identify exactly {count} independent inclusive boundary defects, one per module.",),
            (evidence,))
    if kind == "quality":
        corpus = strict_json((ROOT / "tests/fixtures/reviewer-quality-corpus-v1.json").read_bytes())
        case = next(row for row in corpus["cases"] if row["id"] == case_id)
        details = case["review_context"]
        binding = details["binding"]
        previous = details["previous_finding"]
        prior = ()
        if previous:
            prior = (FindingRecord(
                previous["finding_id"], FindingClass(previous["finding_class"]),
                FindingStatus(previous["status"]), previous["summary"],
                previous["acceptance_test"],
                FindingOrigin(previous["origin"]["slice_id"],
                              previous["origin"]["round_number"], AgentRole.REVIEWER),
                affected_paths=tuple(details["authorized_paths"])),)
        template = context_for("F4" if previous else "F1" if details["native_review_kind"] == "plan" else
                               "F5" if details["native_review_kind"] == "final_review" else "F2")
        command = "python3 -m pytest tests/test_public.py -q -p no:cacheprovider"
        attestation = ValidationAttestation(
            f"qualification-{case_id}", binding["diff_fingerprint"], (command,),
            (ValidationRecord(ValidationStatus.PASS, command, 0, "5 passed"),),
            sha(b"5 passed"), "5 passed",
            command_specs=(ValidationCommandSpec(argv=tuple(command.split())),))
        context = replace(template,
                          run_id=run_id, work_unit_id=binding["work_unit_id"],
                          operation=binding["operation"], diff_fingerprint=binding["diff_fingerprint"],
                          approval_marker=ApprovalMarker[binding["approval_marker"]],
                          slice_id=binding["slice_id"], round_number=binding["round_number"],
                          previous_findings=prior,
                          authoritative_finding_ids=(previous["finding_id"],) if previous else (),
                          validation_attestation=attestation,
                          test_files=("tests/test_public.py",),
                          test_changes_approved=True,
                          allow_new_findings=True, plan_artifact_path=binding["plan_artifact_path"],
                          max_new_findings=512 if details["native_review_kind"] == "final_review" else None)
        evidence = [NativeReviewEvidenceInput("diff", "diff", details["evidence"]["diff"])]
        if details["evidence"]["plan_text"] is not None:
            evidence.append(NativeReviewEvidenceInput("plan", "plan_artifact", details["evidence"]["plan_text"],
                                                      source_path="docs/plan.md"))
        return _with_production_capacity_criterion(NativeReviewRequestSpec(
            context, NativeReviewKind(details["native_review_kind"]),
            "qualification/quality", binding["branch_base"],
            tuple(details["authorized_paths"]), tuple(details["acceptance_criteria"]),
            tuple(sorted(evidence, key=lambda item: item.evidence_id))))
    if kind == "print_timeout":
        spec = spec_for("F2")
        return replace(spec, context=replace(spec.context, run_id=run_id))
    raise ValueError("unsupported qualification call")


def _write_evidence_file(path: Path, document) -> None:
    """Replace only the local evidence projection after syncing its exact bytes."""
    path.parent.mkdir(parents=True, exist_ok=True)
    encoded = (json.dumps(document, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode()
    descriptor, temporary = tempfile.mkstemp(prefix=".qualification-", dir=path.parent)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(encoded)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        Path(temporary).unlink(missing_ok=True)


def _preflight_series_position(attempts: list[dict], *, kind: str, provider: str,
                               series_id: str, case_id: str, commit_sha: str,
                               profile_sha256: str, binary_sha256: str,
                               writer_sha256: str, restart_diagnosis: str | None,
                               restart_change: str | None, retry_of: str | None = None,
                               pair: QualificationPair | None = None) -> None:
    expected = qualification_cases(kind, provider, pair)
    for row in attempts:
        if row["series_id"] == series_id and (row["kind"], row["provider"]) != (kind, provider):
            raise ValueError("series id belongs to another provider or campaign kind")
    prior_ids = list(dict.fromkeys(row["series_id"] for row in attempts
                                  if (row["kind"], row["provider"]) == (kind, provider)))
    same = [row for row in attempts if row["series_id"] == series_id]
    if retry_of is not None:
        candidate = {"call_id": "(next)", "series_id": series_id, "kind": kind,
                     "case_id": case_id, "retry_of": retry_of}
        if not same or prior_ids[-1] != series_id:
            raise ValueError("production retry requires an active series")
        _case_chains(same + [candidate])
    if same:
        if prior_ids[-1] != series_id:
            raise ValueError("an earlier series cannot resume after a restart")
        firsts = _case_chains(same)
        if retry_of is None and (len(firsts) >= len(expected) or case_id != expected[len(firsts)]):
            raise ValueError("next call must follow the frozen series order")
        if restart_diagnosis or restart_change:
            raise ValueError("restart rationale belongs only on a new series's first call")
        for key, value in (("commit_sha", commit_sha), ("profile_sha256", profile_sha256),
                           ("binary_sha256", binary_sha256)):
            if same[0][key] != value:
                raise ValueError("binding changed inside an active series")
        return
    if case_id != expected[0]:
        raise ValueError("new series must begin at its first frozen case")
    if not prior_ids:
        if restart_diagnosis or restart_change:
            raise ValueError("first series cannot claim a restart")
        return
    prior = [row for row in attempts if row["series_id"] == prior_ids[-1]]
    prior_chains = _case_chains(prior)
    if len(prior_chains) == len(expected) and all(
            chain[-1]["status"] == "success" and all(chain[-1]["checks"].values())
            for chain in prior_chains) and all(
            row["case_sha256"] == _frozen_source_digest(row["kind"], row["case_id"])
            for row in prior):
        raise ValueError("successful series cannot be restarted")
    if not restart_diagnosis or not restart_change:
        raise ValueError("restart requires diagnosis and concrete change")
    if all((prior[0]["commit_sha"] == commit_sha,
            prior[0]["profile_sha256"] == profile_sha256,
            prior[0]["binary_sha256"] == binary_sha256,
            prior[0]["writer_sha256"] == writer_sha256)):
        raise ValueError("restart did not change commit, profile, binary or writer")


class _QualificationLedger:
    """An invocation cannot be silently retried after its start becomes uncertain."""

    def __init__(self, path: Path, call_id: str):
        self.path, self.call_id = path, call_id

    def _record(self, event: str, **fields) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = canonical({"call_id": self.call_id, "event": event,
                             "at": datetime.now(timezone.utc).isoformat(), **fields}) + "\n"
        with self.path.open("ab") as stream:
            stream.write(payload.encode())
            stream.flush()
            os.fsync(stream.fileno())

    def begin(self, measurement, bootstrap) -> None:
        self._record("intent", input_digest=measurement.input_digest)

    def process_started(self, pid) -> None:
        self._record("process_started", pid=pid)


def _profile_document(path: Path) -> dict:
    content = path.read_bytes()
    result = tomllib.loads(content.decode()) if path.suffix == ".toml" else strict_json(content)
    if not isinstance(result, dict):
        raise ValueError("qualification profile must be an object")
    return result


def _current_commit() -> str:
    command = subprocess.run(["/usr/bin/git", "rev-parse", "HEAD"], cwd=ROOT,
                             capture_output=True, text=True, timeout=10, check=True,
                             env={"PATH": "/usr/bin:/bin"})
    value = command.stdout.strip()
    if not re.fullmatch(r"[0-9a-f]{40}", value):
        raise ValueError("qualification code HEAD is invalid")
    return value


def _assert_committed_qualification_code() -> None:
    command = subprocess.run(
        ["/usr/bin/git", "status", "--porcelain", "--", "src", "scripts/probe_reviewer.py",
         "scripts/qualification"],
        cwd=ROOT, capture_output=True, text=True, timeout=10, check=True,
        env={"PATH": "/usr/bin:/bin"})
    if command.stdout.strip():
        raise ValueError("qualification adapter and harness code must be committed before a live call")


def run_qualification_call(*, kind: str, case_id: str, provider: str, series_id: str,
                           call_id: str, profile_file: Path, source_repo: Path,
                           output_dir: Path, live: bool = False,
                           quota_observation: dict | None = None,
                           restart_diagnosis: str | None = None,
                           restart_change: str | None = None,
                           tag: str | None = None,
                           production_retry_of: str | None = None,
                           protocol_file: Path | None = None) -> dict:
    """One opt-in call through the final native adapter, protection and parser."""
    if not live:
        raise PermissionError("qualification provider call requires --live")
    if quota_observation is not None and not isinstance(quota_observation, dict):
        raise ValueError("quota observation must be a JSON object")
    if tag is not None and (not isinstance(tag, str) or not tag.strip() or len(tag) > 100):
        raise ValueError("qualification tag must be a short non-empty string")
    profile = _profile_document(profile_file)
    if profile.get("live") is not True:
        raise PermissionError("qualification profile must enable live execution")
    selected_tag = tag if tag is not None else profile.get("tag", "qualification")
    if not isinstance(selected_tag, str) or not selected_tag.strip() or len(selected_tag) > 100:
        raise ValueError("qualification tag must be a short non-empty string")
    if profile.get("commit_sha") != _current_commit():
        raise ValueError("qualification profile commit differs from current code HEAD")
    _assert_committed_qualification_code()
    protocol = _protocol_file(protocol_file)
    validate_qualification(protocol)
    pair = qualification_pair(protocol)
    if case_id not in qualification_cases(kind, provider, pair):
        raise ValueError("call differs from frozen qualification cases")
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,80}", series_id) or not re.fullmatch(
            r"[A-Za-z0-9_-]{1,80}", call_id):
        raise ValueError("series and call ids must be simple identifiers")
    if kind == "quality":
        corpus = strict_json((ROOT / "tests/fixtures/reviewer-quality-corpus-v1.json").read_bytes())
        if not qualification_ready(protocol, corpus):
            raise PermissionError("quality corpus awaits operator review and matching frozen digest")
    qualified_timeout = profile.get("timeout_seconds", protocol["limits"]["probe_seconds"])
    if (kind == "print_timeout" and not (type(qualified_timeout) is int and 0 < qualified_timeout < 600)):
        raise ValueError("print-timeout case requires a positive timeout below the probe limit")
    if kind != "print_timeout" and qualified_timeout != protocol["limits"]["probe_seconds"]:
        raise ValueError("qualification measurement requires the frozen 600-second probe limit")
    if not source_repo.is_dir() or source_repo.is_symlink():
        raise ValueError("review source must be a real directory")
    source_sha = verify_qualification_source(kind, case_id, source_repo)
    if source_sha != _frozen_source_digest(kind, case_id):
        raise ValueError("qualification snapshot bytes differ from the frozen case")
    if output_dir.resolve().is_relative_to(source_repo.resolve()) or profile_file.resolve().is_relative_to(source_repo.resolve()):
        raise ValueError("qualification output and profile must be outside the reviewed source")
    public_validation_sha = None
    if kind in {"quality", "large_output"}:
        public = subprocess.run(
            [sys.executable, "-m", "pytest", "tests/test_public.py", "-q", "-p", "no:cacheprovider"],
            cwd=source_repo, capture_output=True, text=True, timeout=60, check=False,
            env={"PATH": "/no-provider-bin", "PYTHONPATH": str(source_repo),
                 "PYTEST_DISABLE_PLUGIN_AUTOLOAD": "1", "PYTHONDONTWRITEBYTECODE": "1"})
        expected_summary = "5 passed" if kind == "quality" else "1 passed"
        if public.returncode or expected_summary not in public.stdout:
            raise ValueError("public qualification validation did not pass")
        if verify_qualification_source(kind, case_id, source_repo) != source_sha:
            raise ValueError("public validation changed the qualification source")
        public_validation_sha = sha((public.stdout + public.stderr).encode())
    for existing in (output_dir / "qualification-series-v1.json",):
        if existing.exists() and any(row["call_id"] == call_id for row in
                                     strict_json(existing.read_bytes())["attempts"]):
            raise ValueError("call was already recorded; no single-call retake")
    ledger_path = output_dir / "attempt-ledger.jsonl"
    if ledger_path.exists() and any(strict_json(line)["call_id"] == call_id for line in
                                    ledger_path.read_bytes().splitlines()):
        raise ValueError("call has a prior ledger intent; reconcile before any restart")
    from agent_runtime import OrchestratorConfig, run_native_review_agent
    from native_review_request import build_native_review_request

    binary = Path(profile["binary"])
    if not binary.is_absolute() or not binary.is_file():
        raise ValueError("profile binary must be an absolute regular file")
    capability = pair.capability(provider)
    adapter = _qualification_adapter(protocol, provider, profile, binary, qualified_timeout)
    spec = build_qualification_spec(kind, case_id, run_id=f"qualification-{series_id}-{call_id}")
    retry_feedback = None
    if production_retry_of is not None:
        recorded = strict_json((output_dir / "qualification-series-v1.json").read_bytes())["attempts"]
        previous_row = next((row for row in recorded if row["call_id"] == production_retry_of), None)
        if previous_row is not None and _retry_reason(previous_row) == "contract":
            previous_raw = next(row["envelope"] for row in strict_json(
                (output_dir / "qualification-envelopes-v1.json").read_bytes())["envelopes"]
                if row["call_id"] == production_retry_of)
            retry_feedback = _contract_retry_feedback(spec, previous_row, previous_raw)
            spec = replace(spec, retry_feedback=retry_feedback)
    _bind_qualification_catalog(adapter, capability)
    bundle = build_native_review_request(spec, profile=capability)
    _preflight_series_position(
        strict_json((output_dir / "qualification-series-v1.json").read_bytes())["attempts"]
        if (output_dir / "qualification-series-v1.json").exists() else [],
        kind=kind, provider=provider, series_id=series_id, case_id=case_id,
        commit_sha=profile["commit_sha"], profile_sha256=sha(profile_file.read_bytes()),
        binary_sha256=sha(binary.read_bytes()),
        writer_sha256=sha(bundle.provider_response_schema_json.encode()),
        restart_diagnosis=restart_diagnosis, restart_change=restart_change,
        retry_of=production_retry_of, pair=pair)
    budget = _review_budget(capability)
    ledger = _QualificationLedger(ledger_path, call_id)
    raw = {}
    extract = adapter.extract_output
    def capture_output(stdout, stderr, extra_files):
        capture_review_output(adapter, raw, stdout, stderr, extra_files, capability)
        return extract(stdout, stderr, extra_files)
    adapter.extract_output = capture_output
    start = time.monotonic()
    started_at = datetime.now(timezone.utc).isoformat()
    error = None
    failure_kind = None
    contract_rejection = None
    contract_retryable = None
    orchestrator_diagnostic = None
    domain = None
    try:
        output = run_native_review_agent(
            adapter, bundle,
            config=OrchestratorConfig(repo_root=source_repo, provider_input_budget=budget),
            shorten=lambda value, maximum: (value or "")[:maximum],
            operation=spec.context.operation,
            binding_fingerprint=spec.context.diff_fingerprint,
            attempt_invocation=ledger)
        domain = output.result
    except Exception as exc:
        error = f"{type(exc).__name__}: {exc}"
        classified = getattr(exc, "kind", None) or getattr(exc, "kind_hint", None)
        failure_kind = getattr(classified, "value", None) or "unclassified"
        if protocol["schema_version"] == "qualification-protocol-v6" and classified is None:
            from agent_runtime import classify_agent_failure
            failure_kind = classify_agent_failure(adapter.name, exc, invocation_id=call_id).kind.value
        from error_classification import orchestrator_diagnostic_for_exception
        from native_review_contract import (find_native_review_contract_error,
                                            is_retryable_native_review_response_error)
        contract_error = find_native_review_contract_error(exc)
        if contract_error is not None:
            failure_kind = "output"  # the runtime's typed classification of a form failure
            contract_rejection = contract_error.code.value
            contract_retryable = is_retryable_native_review_response_error(contract_error)
            diagnostic = orchestrator_diagnostic_for_exception(exc)
            orchestrator_diagnostic = diagnostic.value if diagnostic is not None else None
    elapsed = time.monotonic() - start
    try:
        envelope = strict_json(raw.get("stdout", "{}"))
    except ValueError:
        envelope = {"raw_stdout": raw.get("stdout", "")}
    clean = sanitize_evidence({"envelope": envelope, "stderr": raw.get("stderr", ""),
                               "exit_code": raw.get("exit_code"), "technical_error": error,
                               "request_document": strict_json(bundle.canonical_json),
                               "writer_schema": strict_json(bundle.provider_response_schema_json)})
    if kind == "transport" and isinstance(envelope, dict):
        checks = validate_format_response(case_id[:2], envelope, bundle=bundle,
                                          exit_code=int(raw.get("exit_code") or 0),
                                          stderr=raw.get("stderr", ""),
                                          transport_profile=capability)["checks"]
    elif kind == "print_timeout":
        partial = envelope.get("structured_output") if isinstance(envelope, dict) else None
        checks = _print_timeout_checks("technical_rejection" if error else "success",
                                       failure_kind, error, raw.get("stderr", ""),
                                       isinstance(partial, dict) and isinstance(partial.get("result"), dict), capability)
        checks["no_valid_stop"] = checks["no_valid_stop"] and domain is None
    elif kind == "large_output":
        response = envelope.get("structured_output", {}).get("result", {}) if isinstance(envelope, dict) else {}
        checks = _large_output_checks(response, domain, int(case_id), set(spec.authorized_paths))
    else:
        checks = {"writer_and_domain": domain is not None}
    row = {
        "call_id": call_id, "series_id": series_id, "kind": kind, "provider": provider,
        "role": "reviewer", "slot": _qualified_slot(case_id, kind), "case_id": case_id,
        "request_id": bundle.bound_context.request_id,
        "case_sha256": source_sha,
        "request_sha256": sha(bundle.canonical_json.encode()),
        "writer_sha256": sha(bundle.provider_response_schema_json.encode()),
        "profile_sha256": sha(profile_file.read_bytes()), "binary_sha256": sha(binary.read_bytes()),
        "commit_sha": profile["commit_sha"],
        "probe_limit_seconds": protocol["limits"]["probe_seconds"],
        "started_at": started_at, "duration_seconds": round(elapsed, 3),
        "status": "technical_rejection" if error else "success",
        "denials": (envelope.get(REVIEW_ENVELOPES[capability].denials_field) or [])
                   if isinstance(envelope, dict) else [],
        "usage": envelope.get("usage") if isinstance(envelope, dict) else None,
        "quota": quota_observation if quota_observation is not None else (
            envelope.get("quota") if isinstance(envelope, dict) else None),
        "tag": selected_tag,
        "session_id": (envelope.get("conversation_id") or envelope.get("session_id"))
                      if isinstance(envelope, dict) else None,
        "output_bytes": raw.get("output_bytes", len(raw.get("stdout", "").encode())), "checks": checks,
        "failure_kind": failure_kind, "retry_of": production_retry_of,
        "contract_rejection": contract_rejection, "contract_retryable": contract_retryable,
        "orchestrator_diagnostic": orchestrator_diagnostic,
        "retry_feedback": None if retry_feedback is None else {
            "prior_invocation_id": retry_feedback.prior_invocation_id,
            "rejection_code": retry_feedback.rejection_code.value,
            "correction_instruction": retry_feedback.correction_instruction},
    }
    row["production_retryable"] = (row["status"] == "technical_rejection" and _retry_reason(row) is not None
        and (protocol["schema_version"] == "qualification-protocol-v5" or kind != "print_timeout"))
    if public_validation_sha is not None:
        row["public_validation_sha256"] = public_validation_sha
    if restart_diagnosis is not None:
        row["restart_diagnosis"] = restart_diagnosis
    if restart_change is not None:
        row["restart_change"] = restart_change
    series_path = output_dir / "qualification-series-v1.json"
    envelopes_path = output_dir / "qualification-envelopes-v1.json"
    series = strict_json(series_path.read_bytes()) if series_path.exists() else {
        "schema_version": "qualification-series-v1", "attempts": []}
    envelopes = strict_json(envelopes_path.read_bytes()) if envelopes_path.exists() else {
        "schema_version": "qualification-envelopes-v1", "envelopes": []}
    append_qualification_attempt(series, envelopes, attempt=row, envelope=clean)
    _write_evidence_file(envelopes_path, envelopes)
    _write_evidence_file(series_path, series)
    ledger._record("result", status=row["status"])
    return series["attempts"][-1]


def _validated_reference_slice_review(protocol: dict) -> tuple[dict, str]:
    """Read a stored, request-bound reference Q3 result without a provider start."""
    from native_review_request import build_native_review_request, validate_native_review_provider_response
    from native_review_contract import parse_bound_native_contract_result

    pair = qualification_pair(protocol)
    series = read_evidence(pair.evidence_directory / "qualification-series-v1.json")
    envelope_path = pair.evidence_directory / "qualification-envelopes-v1.json.gz"
    if not envelope_path.exists():
        envelope_path = envelope_path.with_suffix("")
    envelopes = read_evidence(envelope_path)
    verdicts = validate_qualification_evidence(series, envelopes, protocol)
    successful = [name for name, verdict in verdicts.items()
                  if verdict["kind"] == "quality" and verdict["provider"] == pair.reference and verdict["passed"]]
    if not successful:
        raise ValueError("stored reference quality series is unavailable")
    rows = [row for row in series["attempts"] if row["series_id"] == successful[-1]
            and row["provider"] == pair.reference and row["case_id"] == "Q3"]
    if len(rows) != 1 or rows[0]["status"] != "success" or not all(rows[0]["checks"].values()):
        raise ValueError("stored reference slice review is unavailable or invalid")
    row = rows[0]
    raw = next(item["envelope"] for item in envelopes["envelopes"] if item["call_id"] == row["call_id"])
    result = raw["envelope"]["structured_output"]["result"]
    spec = build_qualification_spec("quality", "Q3", run_id=f"qualification-{row['series_id']}-{row['call_id']}")
    bundle = build_native_review_request(spec, profile=pair.capability(pair.reference))
    validate_native_review_provider_response(result, bundle)
    parse_bound_native_contract_result(result, bundle.bound_context)
    return result, sha(canonical(result).encode())


def run_canary_call(slot: str, *, profile_file: Path, output_dir: Path,
                    live: bool = False, quicktest: bool = False,
                    protocol_file: Path | None = None) -> dict:
    """Run one direct candidate review through the production reviewer adapter."""
    if not live:
        raise PermissionError("canary and quicktest require --live")
    if slot not in {"reviewer", "final_reviewer"} or quicktest and slot != "reviewer":
        raise ValueError("unknown direct review slot")
    profile = _profile_document(profile_file)
    if profile.get("live") is not True or profile.get("commit_sha") != _current_commit():
        raise ValueError("live canary profile must bind the committed code HEAD")
    _assert_committed_qualification_code()
    protocol = _protocol_file(protocol_file)
    validate_qualification(protocol)
    pair = qualification_pair(protocol)
    capability = pair.capability(pair.candidate)
    binary = Path(profile["binary"])
    if not binary.is_absolute() or not binary.is_file() or binary.is_symlink():
        raise ValueError("candidate binary must be an absolute regular file")
    runtime = _runtime_profile(protocol, pair.candidate)
    if profile.get("effort") != runtime["effort"]:
        raise ValueError("canary effort differs from protocol")
    timeout = profile.get("timeout_seconds", 600)
    if type(timeout) is not int or timeout < 0:
        raise ValueError("canary timeout must be a non-negative integer")
    if quicktest:
        timeout = min(timeout or 240, 240)  # a real F2 review measured 90–250 s
    case = "F2" if quicktest else "F4" if slot == "reviewer" else "F5"
    name = f"quicktest-{pair.candidate}" if quicktest else f"canary-{slot}"
    output_dir.mkdir(parents=True, exist_ok=True)
    report_path = output_dir / f"{name}-v1.json"
    ledger_path = output_dir / f"{name}-ledger.jsonl"
    if report_path.exists() or ledger_path.exists():
        raise FileExistsError("direct review already has evidence or an unresolved start intent")
    from agent_runtime import OrchestratorConfig, run_native_review_agent
    from native_review_request import NativeReviewEvidenceInput, build_native_review_request

    adapter = _qualification_adapter(protocol, pair.candidate, profile, binary, timeout or None,
                                     canary=True)
    budget = _review_budget(capability)
    with tempfile.TemporaryDirectory(prefix="dao-canary-", dir=output_dir) as temporary:
        source = Path(temporary) / "repo"
        materialize_format_repo(case, source)
        source_sha = _source_digest(source)
        spec = build_qualification_spec("transport", f"{case}:1", run_id=f"{name}-{_current_commit()[:12]}")
        reference_digest = None
        if slot == "final_reviewer":
            prior, reference_digest = _validated_reference_slice_review(protocol)
            label = LEGACY_REFERENCE_EVIDENCE_ID if protocol["schema_version"] == "qualification-protocol-v5" else "reference_slice_review"
            description = (LEGACY_REFERENCE_DESCRIPTION if label == LEGACY_REFERENCE_EVIDENCE_ID
                           else "Previously validated reference slice review from quality Q3: ")
            evidence = NativeReviewEvidenceInput(label, "review_evidence", description + canonical(prior))
            spec = replace(spec, evidence=tuple(sorted((*spec.evidence, evidence), key=lambda item: item.evidence_id)))
        _bind_qualification_catalog(adapter, capability)
        bundle = build_native_review_request(spec, profile=capability)
        raw = {}
        extract = adapter.extract_output
        def capture_output(stdout, stderr, extra_files):
            capture_review_output(adapter, raw, stdout, stderr, extra_files, capability)
            return extract(stdout, stderr, extra_files)
        adapter.extract_output = capture_output
        ledger = _QualificationLedger(ledger_path, name)
        started = time.monotonic()
        error = None
        try:
            output = run_native_review_agent(adapter, bundle,
                config=OrchestratorConfig(repo_root=source, provider_input_budget=budget),
                shorten=lambda value, maximum: (value or "")[:maximum],
                operation=spec.context.operation,
                binding_fingerprint=spec.context.diff_fingerprint,
                attempt_invocation=ledger)
            domain = output.result
        except Exception as exc:
            error = f"{type(exc).__name__}: {exc}"
            domain = None
        try:
            envelope = strict_json(raw.get("stdout", "{}"))
        except ValueError:
            envelope = {}
        if error is None:
            result = validate_format_response(case, envelope, bundle=bundle,
                exit_code=int(raw.get("exit_code") or 0), stderr=raw.get("stderr", ""),
                transport_profile=capability)
            passed = bool(result["pass"] and domain is not None)
        else:
            result, passed = {"checks": {}}, False
        clean = sanitize_evidence({"envelope": envelope, "stderr": raw.get("stderr", ""),
            "exit_code": raw.get("exit_code"), "technical_error": error,
            "request_document": strict_json(bundle.canonical_json),
            "writer_schema": strict_json(bundle.provider_response_schema_json)})
        proof_raw = {"request_id": bundle.bound_context.request_id,
                     "status": "success" if passed else "failed", "evidence": clean}
        checks = {"writer": result["checks"].get("writer_schema", False),
                  "domain": result["checks"].get("domain_contract", False) and result["checks"].get("case_semantics", False),
                  "effective_rights": passed, "isolation_postcheck": passed,
                  "no_denials": not bool(envelope.get(REVIEW_ENVELOPES[capability].denials_field)) and passed}
        proof = {"slot": slot, "case": case, "request_id": bundle.bound_context.request_id,
                 "request_sha256": sha(bundle.canonical_json.encode()),
                 "writer_sha256": sha(bundle.provider_response_schema_json.encode()),
                 "source_sha256": source_sha, "profile_sha256": sha(profile_file.read_bytes()),
                 "binary_sha256": sha(binary.read_bytes()), "commit_sha": profile["commit_sha"],
                 "model": profile["model"], "timeout_seconds": timeout,
                 "checks": checks, "raw": proof_raw, "raw_sha256": digest(proof_raw)}
        if reference_digest is not None:
            proof[LEGACY_REFERENCE_DIGEST_KEY if protocol["schema_version"] == "qualification-protocol-v5" else "reference_slice_review_sha256"] = reference_digest
        report = {"schema_version": "antigravity-direct-review-v1" if protocol["schema_version"] == "qualification-protocol-v5" else "reviewer-direct-review-v1", "mode": "quicktest" if quicktest else "canary",
                  "provider": capability if protocol["schema_version"] == "qualification-protocol-v5" else pair.candidate,
                  "role": "reviewer", "slot": slot, "case": case,
                  "status": "passed" if passed else "failed", "duration_seconds": round(time.monotonic()-started, 3),
                  "proof": proof}
        if protocol["schema_version"] != "qualification-protocol-v5":
            report["capability_profile"] = capability
        _write_evidence_file(report_path, report)
        ledger._record("result", status=report["status"])
        return report


def execute_probe(argv: list[str], *, cwd: Path, out: Path, profile_file: Path | None = None,
                  live: bool = False, fake_root: Path | None = None, limit: int = 600) -> dict:
    """Only explicit fakes run by default; real executables need two live gates."""
    if not argv or not Path(argv[0]).is_absolute():
        raise ValueError("executable must be an absolute path; PATH lookup is forbidden")
    executable = Path(argv[0]).resolve(strict=True)
    if fake_root is not None and not live:
        if not executable.is_relative_to(fake_root.resolve(strict=True)):
            raise PermissionError("executable is outside the fake root")
        if (not executable.is_file() or executable.stat().st_size > 65536
                or b"# dao-probe-fake-v1" not in executable.read_bytes()[:512]):
            raise PermissionError("fake executable must be a marked small script")
    else:
        if not live or profile_file is None:
            raise PermissionError("real execution requires --live and a profile file")
        profile = strict_json(profile_file.read_bytes())
        if profile.get("live") is not True:
            raise PermissionError("profile does not enable live execution")
    before = capture({"workspace": cwd})
    result = run([str(executable), *argv[1:]], cwd=cwd, out=out,
                 env={"PATH": "/usr/bin:/bin"}, limit=limit, cleanup_limit=15)
    after = capture({"workspace": cwd})
    result["workspace_changes"] = diff(before, after)
    return result


def blind_package(responses: list[dict], *, seed: int,
                  provider_words: tuple[str, ...] = ()) -> tuple[dict, dict]:
    """Return assessment data and separate reversible mapping."""
    if not isinstance(seed, int):
        raise TypeError("seed must be a fixed integer")
    rows = []
    for source in responses:
        if "envelope" in source:
            envelope = source["envelope"]
            structured = envelope.get("structured_output")
            if not isinstance(structured, dict) or set(structured) != {"result"}:
                raise ValueError("blind source lacks a sole structured result")
            content = structured["result"]
        else:
            content = source["content"]
        encoded = json.dumps(content, ensure_ascii=False, sort_keys=True).encode()
        input_bytes = json.dumps(source, ensure_ascii=False, sort_keys=True).encode()
        rows.append((source["provider"], source["case"], content, sha(encoded), sha(input_bytes)))
    random.Random(seed).shuffle(rows)
    assessments, mapping = [], {}
    for number, (provider, case, content, digest_value, input_digest) in enumerate(rows, 1):
        neutral_id = f"B{number:03d}"
        words = {*LEGACY_BLIND_WORDS, *provider_words}
        mention = any(re.search(r"\b" + re.escape(word) + r"\b",
                                json.dumps(content, ensure_ascii=False), re.I) for word in words)
        assessments.append({"id": neutral_id, "content": content,
                            "possible_self_identification": mention})
        mapping[neutral_id] = {"provider": provider, "case": case,
                               "content_sha256": digest_value,
                               "input_sha256": input_digest}
    return {"schema_version": "blind-assessment-v1", "responses": assessments}, {
        "schema_version": "blind-mapping-v1", "seed": seed, "mapping": mapping}


def quality_blind_sources(series: dict, envelopes: dict, protocol: dict) -> list[dict]:
    """Take complete quality series without filtering individual bad outputs."""
    verdicts = validate_qualification_evidence(series, envelopes, protocol)
    selected = {}
    for provider in qualification_pair(protocol).providers:
        names = [name for name, verdict in verdicts.items()
                 if verdict["kind"] == "quality" and verdict["provider"] == provider]
        if not names or not verdicts[names[-1]]["passed"]:
            raise ValueError(f"{provider} quality series is not complete and domain-valid")
        selected[provider] = names[-1]
    raw = {row["call_id"]: row["envelope"] for row in envelopes["envelopes"]}
    sources = []
    for provider in qualification_pair(protocol).providers:
        for chain in _case_chains([row for row in series["attempts"]
                                   if row["series_id"] == selected[provider]]):
            row = chain[-1]
            content = raw[row["call_id"]]["envelope"]["structured_output"]["result"]
            sources.append({"provider": provider, "case": row["case_id"], "content": content})
    if len(sources) != 12 or {(row["provider"], row["case"]) for row in sources} != {
            (provider, f"Q{number}") for provider in qualification_pair(protocol).providers for number in range(1, 7)}:
        raise ValueError("blind inputs must contain each provider and quality case once")
    return sources


def verify_quality_mapping(results: dict, series: dict, envelopes: dict, protocol: dict) -> None:
    """After unblinding, prove each neutral ID maps to recorded content bytes."""
    sources = quality_blind_sources(series, envelopes, protocol)
    pair = qualification_pair(protocol)
    assessment, expected = blind_package(sources, seed=protocol["quality"]["blind_seed"],
        provider_words=(*pair.providers, *pair.capabilities.values(),
                        *(BLIND_WORDS if protocol["schema_version"] == "qualification-protocol-v6" else ())))
    if results.get("mapping") != expected["mapping"]:
        raise ValueError("quality mapping differs from the frozen blind package")
    if results.get("status") == "complete":
        corpus = strict_json((ROOT / "tests/fixtures/reviewer-quality-corpus-v1.json").read_bytes())
        rubric = strict_json((qualification_pair(protocol).evidence_directory / "quality-rubric-v1.json").read_bytes())
        packet = export_rater_packet(assessment, expected, corpus, protocol, rubric)
        answers = {"schema_version": "quality-operator-answers-v1",
                   "answers": results["operator_decisions"]}
        reproduced = combine_quality_ratings(packet, expected, results["ratings"],
                                              corpus, protocol, answers)
        for key in ("packet_sha256", "input_digests", "agreement", "operator_questions",
                    "operator_decisions", "judgments", "judgments_frozen_sha256", "mapping"):
            if results.get(key) != reproduced[key]:
                raise ValueError(f"quality result {key} differs from bound inputs")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    v = sub.add_parser("validate-format")
    v.add_argument("case", choices=EXPECTED_FORMAT)
    v.add_argument("envelope", type=Path)
    v.add_argument("--exit-code", type=int, required=True)
    v.add_argument("--stderr", type=Path)
    p = sub.add_parser("plan")
    p.add_argument("protocol", type=Path)
    b = sub.add_parser("blind")
    b.add_argument("responses", type=Path)
    b.add_argument("assessment", type=Path)
    b.add_argument("mapping", type=Path)
    b.add_argument("--seed", type=int, required=True)
    rp = sub.add_parser("export-rater-packets")
    rp.add_argument("assessment", type=Path)
    rp.add_argument("mapping", type=Path)
    rp.add_argument("output_dir", type=Path)
    pr = sub.add_parser("render-rater-prompt")
    pr.add_argument("packet", type=Path)
    pr.add_argument("output", type=Path)
    cr = sub.add_parser("combine-quality-ratings")
    cr.add_argument("packet", type=Path)
    cr.add_argument("mapping", type=Path)
    cr.add_argument("first_rating", type=Path)  # allowlist:provider -- certification data: independent blind quality rating
    cr.add_argument("second_rating", type=Path)
    cr.add_argument("output", type=Path)
    cr.add_argument("--operator-answers", type=Path)
    cr.add_argument("--questions-out", type=Path)
    cr.add_argument("--claude-special-decision", choices=("approve_experimental", "deny_experimental"))  # allowlist:provider -- certification data: v5 CLI compatibility
    cr.add_argument("--reference-special-decision", choices=("approve_experimental", "deny_experimental"))
    f = sub.add_parser("prepare-blind-inputs")
    f.add_argument("series", type=Path)
    f.add_argument("envelopes", type=Path)
    f.add_argument("output", type=Path)
    x = sub.add_parser("execute")
    x.add_argument("executable", type=Path)
    x.add_argument("--cwd", type=Path, required=True)
    x.add_argument("--out", type=Path, required=True)
    x.add_argument("--fake-root", type=Path)
    x.add_argument("--profile", type=Path)
    x.add_argument("--live", action="store_true")
    q = sub.add_parser("qualification-call")
    q.add_argument("kind", choices=("transport", "large_output", "print_timeout", "quality"))
    q.add_argument("case_id")
    q.add_argument("provider")
    q.add_argument("--series-id", required=True)
    q.add_argument("--call-id", required=True)
    q.add_argument("--profile", type=Path, required=True)
    q.add_argument("--source-repo", type=Path, required=True)
    q.add_argument("--output-dir", type=Path, required=True)
    q.add_argument("--quota-observation", type=Path)
    q.add_argument("--restart-diagnosis")
    q.add_argument("--restart-change")
    q.add_argument("--tag")
    q.add_argument("--production-retry-of")
    q.add_argument("--live", action="store_true")
    canary = sub.add_parser("canary-call")
    canary.add_argument("slot", choices=("reviewer", "final_reviewer"))
    canary.add_argument("--profile", type=Path, required=True)
    canary.add_argument("--output-dir", type=Path, required=True)
    canary.add_argument("--live", action="store_true")
    boundary = sub.add_parser("boundary-check", help="account-free CLI boundary quicktest")
    from scripts.qualification.profiles import ADAPTER_PROFILES
    boundary.add_argument("--pair", choices=(*ADAPTER_PROFILES, "all"), required=True)
    boundary.add_argument("--toolchain-root", type=Path, action="append", default=[])
    boundary.add_argument("--out", type=Path)
    quick = sub.add_parser("quicktest")
    quick.add_argument("provider", nargs="?")
    quick.add_argument("--candidate")
    quick.add_argument("--profile", type=Path, required=True)
    quick.add_argument("--live", action="store_true")
    m = sub.add_parser("prepare-case")
    m.add_argument("kind", choices=("transport", "large_output", "quality"))
    m.add_argument("case_id")
    m.add_argument("destination", type=Path)
    m.add_argument("--proof-out", type=Path)
    e = sub.add_parser("evaluate-qualification")
    e.add_argument("series", type=Path)
    e.add_argument("envelopes", type=Path)
    e.add_argument("--quality-results", type=Path)
    e.add_argument("--operator-decisions", type=Path)
    for command in (b, rp, pr, cr, f, q, canary, quick, e):
        command.add_argument("--protocol", type=Path)
    args = parser.parse_args()
    if args.command == "boundary-check":
        from scripts.qualification.offline_boundary import main as boundary_main
        argv = ["--pair", args.pair]
        for root in args.toolchain_root:
            argv.extend(("--toolchain-root", str(root)))
        if args.out:
            argv.extend(("--out", str(args.out)))
        return boundary_main(argv)
    if args.command == "validate-format":
        result = validate_format_response(args.case, strict_json(args.envelope.read_bytes()),
            exit_code=args.exit_code,
            stderr=args.stderr.read_text(errors="replace") if args.stderr else "")
        print(json.dumps(result, sort_keys=True))
        return 0 if result["pass"] else 1
    if args.command == "plan":
        print(json.dumps(probe_plan(strict_json(args.protocol.read_bytes())), indent=2))
        return 0
    if args.command == "blind":
        protocol = _protocol_file(args.protocol)
        validate_qualification(protocol)
        if args.seed != protocol["quality"]["blind_seed"]:
            raise ValueError("blind seed differs from the frozen qualification protocol")
        corpus = strict_json((ROOT / "tests/fixtures/reviewer-quality-corpus-v1.json").read_bytes())
        if not qualification_ready(protocol, corpus):
            raise PermissionError("quality corpus awaits operator review and a matching frozen digest")
        if args.mapping.resolve().is_relative_to(args.assessment.resolve().parent):
            raise ValueError("mapping must be stored separately from assessment")
        sources = strict_json(args.responses.read_bytes())
        if len(sources) != 12 or {(row["provider"], row["case"]) for row in sources} != {
                (provider, f"Q{number}") for provider in qualification_pair(protocol).providers for number in range(1, 7)}:
            raise ValueError("blind input requires exactly Q1–Q6 per provider")
        pair = qualification_pair(protocol)
        assessment, mapping = blind_package(sources, seed=args.seed,
            provider_words=(*pair.providers, *pair.capabilities.values(),
                        *(BLIND_WORDS if protocol["schema_version"] == "qualification-protocol-v6" else ())))
        args.assessment.write_text(json.dumps(assessment, ensure_ascii=False, indent=2) + "\n")
        args.mapping.write_text(json.dumps(mapping, ensure_ascii=False, indent=2) + "\n")
        return 0
    if args.command == "prepare-blind-inputs":
        protocol = _protocol_file(args.protocol)
        corpus = strict_json((ROOT / "tests/fixtures/reviewer-quality-corpus-v1.json").read_bytes())
        if not qualification_ready(protocol, corpus):
            raise PermissionError("quality corpus awaits operator review and matching frozen digest")
        sources = quality_blind_sources(read_evidence(args.series),
                                        read_evidence(args.envelopes), protocol)
        _write_evidence_file(args.output, sources)
        return 0
    if args.command == "export-rater-packets":
        protocol = _protocol_file(args.protocol)
        corpus = strict_json((ROOT / "tests/fixtures/reviewer-quality-corpus-v1.json").read_bytes())
        rubric = strict_json((qualification_pair(protocol).evidence_directory / "quality-rubric-v1.json").read_bytes())
        packet = export_rater_packet(strict_json(args.assessment.read_bytes()),
            strict_json(args.mapping.read_bytes()), corpus, protocol, rubric)
        args.output_dir.mkdir(parents=True, exist_ok=True)
        for name in qualification_raters(protocol):
            (args.output_dir / f"{name}-packet.json").write_text(
                json.dumps(packet, ensure_ascii=False, indent=2) + "\n")
        print(json.dumps({"packet_sha256": packet["packet_sha256"],
                          "ids": [row["id"] for row in packet["responses"]]}))
        return 0
    if args.command == "render-rater-prompt":
        protocol = _protocol_file(args.protocol)
        packet = strict_json(args.packet.read_bytes())
        args.output.write_text(render_rater_prompt(packet, protocol))
        return 0
    if args.command == "combine-quality-ratings":
        protocol = _protocol_file(args.protocol)
        corpus = strict_json((ROOT / "tests/fixtures/reviewer-quality-corpus-v1.json").read_bytes())
        packet = strict_json(args.packet.read_bytes())
        mapping = strict_json(args.mapping.read_bytes())
        raters = qualification_raters(protocol)
        ratings = {raters[0]: strict_json(args.first_rating.read_bytes()),
                   raters[1]: strict_json(args.second_rating.read_bytes())}
        answers = strict_json(args.operator_answers.read_bytes()) if args.operator_answers else None
        result = combine_quality_ratings(packet, mapping, ratings, corpus, protocol, answers)
        if args.claude_special_decision and protocol["schema_version"] != "qualification-protocol-v5":  # allowlist:provider -- certification data: v5 CLI compatibility
            raise ValueError("new protocols use --reference-special-decision")
        decision = args.claude_special_decision or args.reference_special_decision  # allowlist:provider -- certification data: v5 CLI compatibility
        if args.claude_special_decision and args.reference_special_decision:  # allowlist:provider -- certification data: v5 CLI compatibility
            raise ValueError("reference decision supplied twice")
        result[_reference_decision_key(protocol)] = decision
        result["scores"] = grade_quality(result, corpus, protocol)
        args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n")
        if args.questions_out:
            decided = {(row["id"], row["criterion"]) for row in result["operator_decisions"]}
            pending = [row for row in result["operator_questions"]
                       if (row["id"], row["criterion"]) not in decided]
            args.questions_out.write_text(json.dumps({"schema_version": "quality-operator-questions-v1",
                "questions": pending}, ensure_ascii=False, indent=2) + "\n")
        print(json.dumps({"status": result["status"], "scores": result["scores"],
                          "questions": result["operator_questions"]}, ensure_ascii=False))
        return 0 if result["status"] == "complete" else 1
    if args.command == "prepare-case":
        if args.proof_out and args.kind != "large_output":
            raise ValueError("hidden proof output is only available for large cases")
        if args.proof_out and (not args.proof_out.is_absolute() or
                               args.proof_out.resolve().is_relative_to(args.destination.resolve())):
            raise ValueError("hidden proof must be an absolute path outside the review source")
        if args.proof_out and args.proof_out.exists():
            raise FileExistsError("hidden proof destination already exists")
        if args.kind == "transport":
            materialize_format_repo(args.case_id.split(":", 1)[0], args.destination)
        elif args.kind == "large_output":
            materialize_large_repo(int(args.case_id), args.destination)
        else:
            materialize_quality_repo(args.case_id, args.destination)
        if args.proof_out:
            args.proof_out.parent.mkdir(parents=True, exist_ok=True)
            args.proof_out.write_text(large_hidden_proof(int(args.case_id)), encoding="utf-8")
        print(verify_qualification_source(args.kind, args.case_id, args.destination))
        return 0
    if args.command == "qualification-call":
        result = run_qualification_call(kind=args.kind, case_id=args.case_id,
            provider=args.provider, series_id=args.series_id, call_id=args.call_id,
            profile_file=args.profile, source_repo=args.source_repo,
            output_dir=args.output_dir, live=args.live,
            quota_observation=strict_json(args.quota_observation.read_bytes())
            if args.quota_observation else None,
            restart_diagnosis=args.restart_diagnosis,
            restart_change=args.restart_change, tag=args.tag,
            production_retry_of=args.production_retry_of, protocol_file=args.protocol)
        print(json.dumps(result, ensure_ascii=False, sort_keys=True))
        return 0 if result["status"] == "success" else 1
    if args.command == "canary-call":
        result = run_canary_call(args.slot, profile_file=args.profile,
            output_dir=args.output_dir, live=args.live, protocol_file=args.protocol)
        print(json.dumps(result, ensure_ascii=False, sort_keys=True))
        return 0 if result["status"] == "passed" else 1
    if args.command == "quicktest":
        candidate = args.candidate or args.provider
        if (not isinstance(candidate, str) or not re.fullmatch(r"[a-z][a-z0-9_-]{0,79}", candidate)
                or args.candidate and args.provider and args.candidate != args.provider):
            raise ValueError("quicktest requires one unambiguous candidate")
        protocol_path = args.protocol
        if args.candidate and protocol_path is None:
            protocol_path = ROOT / "docs/evidence" / candidate / "qualification-protocol-v6.json"
        pair = qualification_pair(_protocol_file(protocol_path))
        if candidate != pair.candidate:
            raise ValueError("quicktest provider differs from candidate")
        with tempfile.TemporaryDirectory(prefix="dao-reviewer-quicktest-") as temporary:
            result = run_canary_call("reviewer", profile_file=args.profile,
                output_dir=Path(temporary), live=args.live, quicktest=True, protocol_file=protocol_path)
        print(json.dumps({"schema_version": result["schema_version"], "mode": "quicktest",
            "status": result["status"], "duration_seconds": result["duration_seconds"],
            "checks": result["proof"]["checks"]}, sort_keys=True))
        return 0 if result["status"] == "passed" else 1
    if args.command == "evaluate-qualification":
        protocol = _protocol_file(args.protocol)
        series = read_evidence(args.series)
        envelopes = read_evidence(args.envelopes)
        decisions_path = args.operator_decisions or qualification_pair(protocol).evidence_directory / "operator-decisions-v1.json"
        decisions_bytes = decisions_path.read_bytes()
        if protocol["schema_version"] == "qualification-protocol-v5" and sha(decisions_bytes) != OPERATOR_DECISIONS_SHA256:
            raise ValueError("operator decisions digest differs from the accepted decision")
        if not args.quality_results:
            raise ValueError("qualification needs bound blind quality results")
        summary = qualification_summary(series, envelopes, protocol,
            strict_json(args.quality_results.read_bytes()), strict_json(decisions_bytes))
        print(json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True))
        return 0 if summary["qualified_for_canary"] else 1
    result = execute_probe([str(args.executable)], cwd=args.cwd, out=args.out,
        fake_root=args.fake_root, profile_file=args.profile, live=args.live)
    print(json.dumps(result, sort_keys=True))
    return 0 if result["exit_code"] == 0 and not result["workspace_changes"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
