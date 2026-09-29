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
from dataclasses import replace

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
FORMAT_REPO_FIXTURE = ROOT / "tests/fixtures/reviewer-format-repo-v1.json"
FORMAT_REPO_SHA256 = "3b6f775aebdc90075524f942c1749233c3c63ddc56d9ed19a58dd1cf82a4fa15"

def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'))


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
                             stderr: str = "", bundle=None) -> dict:
    """Judge writer, bound domain result and case semantics independently."""
    from native_review_contract import parse_bound_native_contract_result
    from schema_validation import validate_schema_document

    bundle = bundle or build_format_case(case)
    checks = {}
    checks["exit_zero"] = exit_code == 0
    checks["status_success"] = envelope.get("status") == "SUCCESS"
    checks["no_error"] = not envelope.get("error")
    denied = envelope.get("denied_actions")
    checks["no_denials"] = denied is None or denied == []
    checks["stderr_clean"] = not any(re.search(pattern, stderr, re.I) for pattern in
        (r"timeout", r"timed\s+out", r"deadline", r"AGY_ERROR", r"auto.denied",
         r"cancel(?:ed|led|lation)", r"interrupt(?:ed|ion)"))
    writer = strict_json(bundle.provider_response_schema_json)
    checks["schema_echo"] = typed_equal(envelope.get("json_schema"), writer)
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


def validate_qualification(document: dict) -> None:
    if not __debug__:
        raise RuntimeError("optimized mode cannot validate the protocol")
    assert document["schema_version"] == "qualification-protocol-v1"
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
        (ROOT / "docs/evidence/antigravity/phase-0-v1.json").read_bytes())
    assert document["quality"]["corpus_sha256"] == sha(
        (ROOT / "tests/fixtures/reviewer-quality-corpus-v1.json").read_bytes())
    assert document["format_regression_sha256"] == sha(
        (ROOT / "tests/fixtures/reviewer-format-s6-v1.json").read_bytes())


def qualification_ready(protocol: dict, corpus: dict) -> bool:
    """The quality corpus cannot be frozen or measured before operator review."""
    validate_qualification(protocol)
    return (protocol["quality"].get("corpus_operator_review") == "approved"
            and corpus.get("operator_review") == "approved"
            and sha(json.dumps(corpus, ensure_ascii=False, indent=2,
                               sort_keys=True).encode() + b"\n")
            == protocol["quality"]["corpus_sha256"])


def probe_plan(protocol: dict) -> list[dict]:
    validate_qualification(protocol)
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
        result.append({"series": "quality", "case": case, "providers": 2})
    for slot in ("reviewer", "final_reviewer"):
        result.append({"series": "canary", "slot": slot})
    return result


def qualification_cases(kind: str, provider: str) -> tuple[str, ...]:
    """The frozen, ordered call set for one independently restartable series."""
    if kind == "transport" and provider == "agy":
        return tuple(f"{case}:{session}" for case in EXPECTED_FORMAT for session in (1, 2))
    if kind == "large_output" and provider == "agy":
        return ("128", "512")
    if kind == "print_timeout" and provider == "agy":
        return ("T1",)
    if kind == "quality" and provider in {"agy", "claude"}:  # allowlist:provider -- transport: Slice-5 bound reviewer qualification
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
    if row["case_id"] not in qualification_cases(row["kind"], row["provider"]):
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


def _verify_recorded_result(row: dict, raw: dict) -> None:
    """Rebuild the transmitted request and independently parse recorded output."""
    from native_review_contract import parse_bound_native_contract_result
    from native_review_request import (build_native_review_request,
                                       validate_native_review_provider_response)
    from antigravity_adapter import classify_agy_stderr

    spec = build_qualification_spec(row["kind"], row["case_id"],
                                    run_id=f"qualification-{row['series_id']}-{row['call_id']}")
    bundle = build_native_review_request(spec, profile="antigravity" if row["provider"] == "agy" else "claude")  # allowlist:provider -- transport: Slice-5 bound reviewer qualification
    stored_request = raw.get("request_document")
    stored_writer = raw.get("writer_schema")
    if (not isinstance(stored_request, dict) or not isinstance(stored_writer, dict) or
        row["request_id"] != stored_request.get("request_id") or
        row["request_sha256"] != sha(canonical(stored_request).encode()) or
        row["writer_sha256"] != sha(canonical(stored_writer).encode()) or
        row["case_sha256"] != _frozen_source_digest(row["kind"], row["case_id"])):
        raise ValueError("recorded request, writer or case binding is inconsistent")
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
        timeout_kind, timeout_code = classify_agy_stderr(raw.get("stderr", ""))
        if (row["status"] != "technical_rejection" or timeout_code != "print-timeout"
            or isinstance(response, dict) or raw.get("technical_error") is None
            or row["checks"] != {"print_timeout": True, "no_valid_stop": True}):
            raise ValueError("print timeout did not fail safely as a technical rejection")
        return
    if row["status"] != "success":
        if raw.get("technical_error") is None:
            raise ValueError("failed attempt lacks its technical diagnosis")
        return
    if raw.get("technical_error") is not None or not isinstance(response, dict):
        raise ValueError("successful attempt lacks a sole result")
    if row["provider"] == "agy" and not typed_equal(
            envelope.get("json_schema"), strict_json(bundle.provider_response_schema_json)):
        raise ValueError("transmitted AGY writer echo differs")
    validate_native_review_provider_response(response, bundle)
    domain = parse_bound_native_contract_result(response, bundle.bound_context)
    if row["kind"] == "transport":
        actual = validate_format_response(row["case_id"][:2], envelope,
            exit_code=int(raw.get("exit_code") or 0), stderr=raw.get("stderr", ""), bundle=bundle)
        if actual["checks"] != row["checks"]:
            raise ValueError("transport checks differ from the recorded envelope")
    elif row["kind"] == "large_output":
        count = int(row["case_id"])
        findings = response.get("new_findings", [])
        paths = set(spec.authorized_paths)
        if (len(findings) != count or
            len({item["finding_id"] for item in findings}) != count or
            {item["affected_paths"][0] for item in findings
             if len(item["affected_paths"]) == 1} != paths or
            any(len(item["affected_paths"]) != 1 for item in findings) or
            (count == 128 and domain.stopped) or
            (count == 512 and (not domain.stopped or
             domain.stop_request.rule_id != "DISCOVERY_OUTPUT_LIMIT"))):
            raise ValueError("large output is truncated, out of scope or wrong at capacity")
        if not all(row["checks"].values()):
            raise ValueError("large output checks do not certify full parsing")
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
    for row in attempts:
        _validate_attempt_bindings(row, protocol)
        owner = (row["kind"], row["provider"])
        if row["series_id"] in series_owners and series_owners[row["series_id"]] != owner:
            raise ValueError("series id is reused across provider or campaign kind")
        series_owners[row["series_id"]] = owner
        clean = by_envelope[row["call_id"]]
        if sanitize_evidence(clean) != clean or sha(canonical(clean).encode()) != row["envelope_sha256"]:
            raise ValueError("qualification envelope is unsanitized or changed")
        _verify_recorded_result(row, clean)
        groups.setdefault((row["kind"], row["provider"]), []).append(row)
    verdicts = {}
    for key, rows in groups.items():
        expected = qualification_cases(*key)
        by_series: dict[str, list[dict]] = {}
        for row in rows:
            by_series.setdefault(row["series_id"], []).append(row)
        earlier: list[dict] = []
        for series_id, calls in by_series.items():
            observed = tuple(row["case_id"] for row in calls)
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
            if earlier and len(earlier[-1][0]) == len(expected) and all(earlier[-1][1]):
                raise ValueError("successful series cannot be restarted")
            earlier.append((calls, call_pass))
            verdicts[series_id] = {"kind": key[0], "provider": key[1],
                                   "passed": len(calls) == len(expected) and all(call_pass),
                                   "incomplete": len(calls) != len(expected), "calls": len(calls),
                                   "failed_cases": [row["case_id"] for row, ok in zip(calls, call_pass) if not ok]}
    for kind, provider in (("transport", "agy"), ("large_output", "agy"),
                           ("print_timeout", "agy"), ("quality", "agy"), ("quality", "claude")):  # allowlist:provider -- transport: Slice-5 bound reviewer qualification
        if (kind, provider) not in groups:
            verdicts[f"pending-{kind}-{provider}"] = {
                "kind": kind, "provider": provider, "passed": False,
                "incomplete": True, "calls": 0, "failed_cases": []}
    return verdicts


def timeout_proposal(series: dict, verdicts: dict, protocol: dict) -> dict:
    """Propose, never install, the finite measured timeout."""
    eligible = {name for name, result in verdicts.items() if result["passed"] and
                result["provider"] == "agy" and result["kind"] in {"transport", "large_output", "quality"}}
    measurements = [row for row in series["attempts"] if row["series_id"] in eligible]
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


def grade_quality(results: dict, corpus: dict, protocol: dict) -> dict:
    """Apply the frozen absolute rule separately after blind judgments are fixed."""
    if results.get("schema_version") != "quality-results-v1":
        raise ValueError("quality result version differs")
    if results.get("blind_seed") != protocol["quality"]["blind_seed"]:
        raise ValueError("blind seed differs")
    judgments, mapping = results["judgments"], results["mapping"]
    if results.get("judgments_frozen_sha256") != digest(judgments):
        raise ValueError("blind judgments are not digest-frozen")
    frozen = datetime.fromisoformat(results["judgments_frozen_at"].replace("Z", "+00:00"))
    unblinded = datetime.fromisoformat(results["unblinded_at"].replace("Z", "+00:00"))
    if frozen.tzinfo is None or unblinded.tzinfo is None or not frozen < unblinded:
        raise ValueError("judgments must be frozen before unblinding")
    if len(judgments) != 12 or len(mapping) != 12 or set(mapping) != {
            row["id"] for row in judgments}:
        raise ValueError("twelve blind judgments and exact mapping required")
    cases = {case["id"]: case for case in corpus["cases"]}
    for item in mapping.values():
        if set(item) != {"provider", "case", "content_sha256", "input_sha256"}:
            raise ValueError("blind mapping is incomplete")
        _sha_field(item["content_sha256"], "content_sha256")
        _sha_field(item["input_sha256"], "input_sha256")
    scores = {}
    for provider in ("agy", "claude"):  # allowlist:provider -- transport: Slice-5 bound reviewer qualification
        selected = [(row, cases[mapping[row["id"]]["case"]]) for row in judgments
                    if mapping[row["id"]]["provider"] == provider]
        if {case["id"] for _, case in selected} != set(cases):
            raise ValueError("each provider must have exactly Q1–Q6")
        for row, case in selected:
            if (type(row.get("defect_found")) is not bool or
                type(row.get("invented_critical")) is not bool or
                type(row.get("false_positive_count")) is not int or
                row["false_positive_count"] < 0 or
                (not case["defect"] and row["defect_found"]) or
                not isinstance(row.get("reason"), str) or not row["reason"].strip()):
                raise ValueError("blind judgment lacks a reasoned classification")
        critical = sum(row["defect_found"] for row, case in selected if case["critical"])
        defects = sum(row["defect_found"] for row, case in selected if case["defect"])
        false_positives = sum(row["false_positive_count"] for row, case in selected if not case["defect"])
        invented = sum(row["invented_critical"] for row, _ in selected)
        passed = (critical == protocol["quality_rule"]["critical_required"] and
                  defects >= protocol["quality_rule"]["defects_required"] and
                  false_positives <= protocol["quality_rule"]["clean_false_positives_max"] and
                  invented <= protocol["quality_rule"]["invented_critical_max"])
        scores[provider] = {"passed": passed, "critical": critical, "defects": defects,
                            "clean_false_positives": false_positives,
                            "invented_critical": invented}
    special = results.get("claude_special_decision")  # allowlist:provider -- transport: Slice-5 bound reviewer qualification
    if scores["agy"]["passed"] and not scores["claude"]["passed"]:  # allowlist:provider -- transport: Slice-5 bound reviewer qualification
        if special not in {"approve_experimental", "deny_experimental"}:
            raise ValueError("operator decision required when reference quality fails")
    elif special is not None:
        raise ValueError("Claude special decision is permitted only for a weaker reference")  # allowlist:provider -- transport: Slice-5 bound reviewer qualification
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


def build_qualification_spec(kind: str, case_id: str, *, run_id: str):
    """Build the same NativeReviewRequestSpec used by production adapters."""
    from contracts import (AgentRole, ApprovalMarker, FindingClass, FindingOrigin,
                           FindingRecord, FindingStatus, ValidationAttestation,
                           ValidationCommandSpec, ValidationRecord, ValidationStatus)
    from native_review_request import NativeReviewEvidenceInput, NativeReviewKind, NativeReviewRequestSpec

    if kind == "transport":
        case = case_id.split(":", 1)[0]
        spec = spec_for(case)
        context = replace(spec.context, run_id=run_id)
        return replace(spec, context=context)
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
        return NativeReviewRequestSpec(
            context, NativeReviewKind(details["native_review_kind"]),
            "qualification/quality", binding["branch_base"],
            tuple(details["authorized_paths"]), tuple(details["acceptance_criteria"]),
            tuple(sorted(evidence, key=lambda item: item.evidence_id)))
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
                               restart_change: str | None) -> None:
    expected = qualification_cases(kind, provider)
    for row in attempts:
        if row["series_id"] == series_id and (row["kind"], row["provider"]) != (kind, provider):
            raise ValueError("series id belongs to another provider or campaign kind")
    prior_ids = list(dict.fromkeys(row["series_id"] for row in attempts
                                  if (row["kind"], row["provider"]) == (kind, provider)))
    same = [row for row in attempts if row["series_id"] == series_id]
    if same:
        if prior_ids[-1] != series_id:
            raise ValueError("an earlier series cannot resume after a restart")
        if len(same) >= len(expected) or case_id != expected[len(same)]:
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
    if len(prior) == len(expected) and all(row["status"] == "success" and
                                         all(row["checks"].values()) for row in prior):
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
        ["/usr/bin/git", "status", "--porcelain", "--", "src", "scripts/probe_reviewer.py"],
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
                           tag: str | None = None) -> dict:
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
    protocol = strict_json((ROOT / "docs/evidence/antigravity/qualification-protocol-v1.json").read_bytes())
    validate_qualification(protocol)
    if case_id not in qualification_cases(kind, provider):
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
    from agent_adapters import AgentSettings, NativeClaudeReviewAdapter  # allowlist:provider -- transport: Slice-5 bound reviewer qualification
    from antigravity_adapter import NativeAntigravityReviewAdapter
    from agent_runtime import OrchestratorConfig, run_native_review_agent
    from native_review_request import build_native_review_request
    from provider_input_budget import ProviderInputBudgetPolicy, ProviderInputBudgetRule, default_provider_input_budget_policy

    binary = Path(profile["binary"])
    if not binary.is_absolute() or not binary.is_file():
        raise ValueError("profile binary must be an absolute regular file")
    options = profile.get("provider_options", {}).get("antigravity", {})
    if provider == "agy":
        if profile.get("model") != "gemini-3.1-pro-high" or profile.get("effort") != "high":
            raise ValueError("AGY qualification fixes model and effort")
        if not options.get("home") or not options.get("run_root"):
            raise ValueError("AGY qualification requires isolated home and run root")
        settings = AgentSettings("antigravity", str(binary), profile["model"],
                                 qualified_timeout, profile["effort"],
                                 antigravity_home=options["home"], antigravity_run_root=options["run_root"])
        adapter = NativeAntigravityReviewAdapter(settings)
        capability = "antigravity"
    else:
        if profile.get("model") != "opus" or profile.get("effort") != "high":
            raise ValueError("Claude qualification fixes opus/high")  # allowlist:provider -- transport: Slice-5 bound reviewer qualification
        settings = AgentSettings("claude", str(binary), "opus", qualified_timeout, "high")  # allowlist:provider -- transport: Slice-5 bound reviewer qualification
        adapter = NativeClaudeReviewAdapter(settings)  # allowlist:provider -- transport: Slice-5 bound reviewer qualification
        capability = "claude"  # allowlist:provider -- transport: Slice-5 bound reviewer qualification
    spec = build_qualification_spec(kind, case_id, run_id=f"qualification-{series_id}-{call_id}")
    bundle = build_native_review_request(spec, profile=capability)
    _preflight_series_position(
        strict_json((output_dir / "qualification-series-v1.json").read_bytes())["attempts"]
        if (output_dir / "qualification-series-v1.json").exists() else [],
        kind=kind, provider=provider, series_id=series_id, case_id=case_id,
        commit_sha=profile["commit_sha"], profile_sha256=sha(profile_file.read_bytes()),
        binary_sha256=sha(binary.read_bytes()),
        writer_sha256=sha(bundle.provider_response_schema_json.encode()),
        restart_diagnosis=restart_diagnosis, restart_change=restart_change)
    defaults = default_provider_input_budget_policy()
    if provider == "agy":
        budget = ProviderInputBudgetPolicy(
            tuple(ProviderInputBudgetRule("antigravity" if rule.provider == "claude" else rule.provider,  # allowlist:provider -- transport: Slice-5 bound reviewer qualification
                                          rule.role, rule.operation, rule.max_chars, rule.max_bytes)
                  for rule in defaults.rules),
            (("implementer", "implementer", "codex"), ("reviewer", "reviewer", "antigravity"),  # allowlist:provider -- transport: Slice-5 bound reviewer qualification
             ("final_reviewer", "reviewer", "antigravity")))
    else:
        budget = defaults
    ledger = _QualificationLedger(ledger_path, call_id)
    raw = {}
    extract = adapter.extract_output
    def capture_output(stdout, stderr, extra_files):
        raw.update(stdout=stdout, stderr=stderr, exit_code=extra_files.get("exit_code"))
        return extract(stdout, stderr, extra_files)
    adapter.extract_output = capture_output
    start = time.monotonic()
    started_at = datetime.now(timezone.utc).isoformat()
    error = None
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
                                          stderr=raw.get("stderr", ""))["checks"]
    elif kind == "print_timeout":
        checks = {"print_timeout": bool(error and "timeout" in error.lower()),
                  "no_valid_stop": domain is None}
    elif kind == "large_output":
        response = envelope.get("structured_output", {}).get("result", {}) if isinstance(envelope, dict) else {}
        findings = response.get("new_findings", []) if isinstance(response, dict) else []
        expected = int(case_id)
        paths = set(spec.authorized_paths)
        checks = {"writer_and_domain": domain is not None,
                  "finding_count": len(findings) == expected,
                  "unique_ids": len({f.get("finding_id") for f in findings}) == expected,
                  "scope": all(len(f.get("affected_paths", [])) == 1 and
                               f["affected_paths"][0] in paths for f in findings),
                  "all_paths": {f["affected_paths"][0] for f in findings if f.get("affected_paths")} == paths,
                  "capacity_semantics": bool(domain is not None and
                      (getattr(domain, "stop_request", None) is None if expected == 128 else
                       getattr(getattr(domain, "stop_request", None), "rule_id", None) == "DISCOVERY_OUTPUT_LIMIT"))}
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
        "denials": (envelope.get("denied_actions") or []) if isinstance(envelope, dict) else [],
        "usage": envelope.get("usage") if isinstance(envelope, dict) else None,
        "quota": quota_observation if quota_observation is not None else (
            envelope.get("quota") if isinstance(envelope, dict) else None),
        "tag": selected_tag,
        "session_id": (envelope.get("conversation_id") or envelope.get("session_id"))
                      if isinstance(envelope, dict) else None,
        "output_bytes": len(raw.get("stdout", "").encode()), "checks": checks,
    }
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


def blind_package(responses: list[dict], *, seed: int) -> tuple[dict, dict]:
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
        mention = bool(re.search(r"\b(?:antigravity|agy|claude|codex|gemini)\b",  # allowlist:provider -- certification data: self-identification scan
                                 json.dumps(content, ensure_ascii=False), re.I))
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
    for provider in ("agy", "claude"):  # allowlist:provider -- transport: Slice-5 bound reviewer qualification
        names = [name for name, verdict in verdicts.items()
                 if verdict["kind"] == "quality" and verdict["provider"] == provider]
        if not names or not verdicts[names[-1]]["passed"]:
            raise ValueError(f"{provider} quality series is not complete and domain-valid")
        selected[provider] = names[-1]
    raw = {row["call_id"]: row["envelope"] for row in envelopes["envelopes"]}
    sources = []
    for provider in ("agy", "claude"):  # allowlist:provider -- transport: Slice-5 bound reviewer qualification
        for row in series["attempts"]:
            if row["series_id"] != selected[provider]:
                continue
            content = raw[row["call_id"]]["envelope"]["structured_output"]["result"]
            sources.append({"provider": provider, "case": row["case_id"], "content": content})
    if len(sources) != 12 or {(row["provider"], row["case"]) for row in sources} != {
            (provider, f"Q{number}") for provider in ("agy", "claude") for number in range(1, 7)}:  # allowlist:provider -- transport: Slice-5 bound reviewer qualification
        raise ValueError("blind inputs must contain each provider and quality case once")
    return sources


def verify_quality_mapping(results: dict, series: dict, envelopes: dict, protocol: dict) -> None:
    """After unblinding, prove each neutral ID maps to recorded content bytes."""
    sources = quality_blind_sources(series, envelopes, protocol)
    _, expected = blind_package(sources, seed=protocol["quality"]["blind_seed"])
    if results.get("mapping") != expected["mapping"]:
        raise ValueError("quality mapping differs from the frozen blind package")


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
    q.add_argument("provider", choices=("agy", "claude"))  # allowlist:provider -- transport: Slice-5 bound reviewer qualification
    q.add_argument("--series-id", required=True)
    q.add_argument("--call-id", required=True)
    q.add_argument("--profile", type=Path, required=True)
    q.add_argument("--source-repo", type=Path, required=True)
    q.add_argument("--output-dir", type=Path, required=True)
    q.add_argument("--quota-observation", type=Path)
    q.add_argument("--restart-diagnosis")
    q.add_argument("--restart-change")
    q.add_argument("--tag")
    q.add_argument("--live", action="store_true")
    m = sub.add_parser("prepare-case")
    m.add_argument("kind", choices=("transport", "large_output", "quality"))
    m.add_argument("case_id")
    m.add_argument("destination", type=Path)
    m.add_argument("--proof-out", type=Path)
    e = sub.add_parser("evaluate-qualification")
    e.add_argument("series", type=Path)
    e.add_argument("envelopes", type=Path)
    e.add_argument("--quality-results", type=Path)
    args = parser.parse_args()
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
        protocol = strict_json((ROOT / "docs/evidence/antigravity/qualification-protocol-v1.json").read_bytes())
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
                (provider, f"Q{number}") for provider in ("agy", "claude") for number in range(1, 7)}:  # allowlist:provider -- transport: Slice-5 bound reviewer qualification
            raise ValueError("blind input requires exactly Q1–Q6 per provider")
        assessment, mapping = blind_package(sources, seed=args.seed)
        args.assessment.write_text(json.dumps(assessment, ensure_ascii=False, indent=2) + "\n")
        args.mapping.write_text(json.dumps(mapping, ensure_ascii=False, indent=2) + "\n")
        return 0
    if args.command == "prepare-blind-inputs":
        protocol = strict_json((ROOT / "docs/evidence/antigravity/qualification-protocol-v1.json").read_bytes())
        corpus = strict_json((ROOT / "tests/fixtures/reviewer-quality-corpus-v1.json").read_bytes())
        if not qualification_ready(protocol, corpus):
            raise PermissionError("quality corpus awaits operator review and matching frozen digest")
        sources = quality_blind_sources(strict_json(args.series.read_bytes()),
                                        strict_json(args.envelopes.read_bytes()), protocol)
        _write_evidence_file(args.output, sources)
        return 0
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
            restart_change=args.restart_change, tag=args.tag)
        print(json.dumps(result, ensure_ascii=False, sort_keys=True))
        return 0 if result["status"] == "success" else 1
    if args.command == "evaluate-qualification":
        protocol = strict_json((ROOT / "docs/evidence/antigravity/qualification-protocol-v1.json").read_bytes())
        series = strict_json(args.series.read_bytes())
        envelopes = strict_json(args.envelopes.read_bytes())
        verdicts = validate_qualification_evidence(series, envelopes, protocol)
        summary = {"series": verdicts}
        eligible = False
        if args.quality_results:
            corpus = strict_json((ROOT / "tests/fixtures/reviewer-quality-corpus-v1.json").read_bytes())
            if not qualification_ready(protocol, corpus):
                raise PermissionError("quality corpus awaits operator review and matching frozen digest")
            quality_results = strict_json(args.quality_results.read_bytes())
            summary["quality"] = grade_quality(quality_results, corpus, protocol)
            verify_quality_mapping(quality_results, series, envelopes, protocol)
            eligible = (all(row["passed"] for row in verdicts.values()) and
                        summary["quality"]["agy"]["passed"] and
                        (summary["quality"]["claude"]["passed"] or  # allowlist:provider -- transport: Slice-5 bound reviewer qualification
                         quality_results["claude_special_decision"] == "approve_experimental"))  # allowlist:provider -- transport: Slice-5 bound reviewer qualification
        try:
            summary["timeout_proposal"] = timeout_proposal(series, verdicts, protocol)
        except ValueError as exc:
            summary["timeout_proposal_pending"] = str(exc)
            eligible = False
        summary["qualified_for_canary"] = eligible
        print(json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True))
        return 0 if eligible else 1
    result = execute_probe([str(args.executable)], cwd=args.cwd, out=args.out,
        fake_root=args.fake_root, profile_file=args.profile, live=args.live)
    print(json.dumps(result, sort_keys=True))
    return 0 if result["exit_code"] == 0 and not result["workspace_changes"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
