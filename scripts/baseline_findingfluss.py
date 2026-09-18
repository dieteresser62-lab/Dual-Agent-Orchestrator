#!/usr/bin/env python3
"""Rekonstruiert den Findingfluss eines Laufs aus seiner Recordkette.

Beantwortet die drei Messfragen aus Backlogpunkt 68: wie viele der am Laufende
offenen Findings in einem Slicereview eröffnet wurden, wie viele davon ein
späterer Planslice fachlich hätte tragen können und wie viele erstmals im
branchweiten Review entstanden.

Aufruf: baseline_findingfluss.py <artifacts/<run-id>> [...]
Die Kette wird gelesen, nicht validiert; das Ergebnis ist eine Auswertung,
keine autoritative Projektion.
"""
from __future__ import annotations

import collections
import json
import pathlib
import re
import sys

CODE_PATH = re.compile(r'\b(?:app|pipeline|src|tests|scripts)/[\w./-]+\.(?:ts|py|json|css|md|html|js)\b')


def load_records(run_dir: pathlib.Path) -> list[dict]:
    """Liest Einzel- und Sammelrecords und ordnet sie nach Revision."""
    records: list[dict] = []
    for path in (run_dir / 'records').glob('*.json'):
        payload = json.loads(path.read_text(encoding='utf-8'))
        if 'record' in payload:
            records.append(payload['record'])
        else:
            records.extend(payload['records'])
    records.sort(key=lambda record: record.get('revision', -1))
    return records


def work_unit_kinds(records: list[dict]) -> tuple[dict[str, str], dict[str, str]]:
    """Ordnet jeder Arbeitseinheit ihre Art und ihre Slice-Id zu."""
    steps: dict[str, set[str]] = collections.defaultdict(set)
    slices: dict[str, str] = {}
    for record in records:
        if record['record_type'] != 'workflow_transition':
            continue
        payload = record['payload']
        unit = payload.get('work_unit_id')
        if unit is None:
            continue
        unit = str(unit)
        if payload.get('step'):
            steps[unit].add(payload['step'])
        if payload.get('slice_id') is not None:
            slices.setdefault(unit, str(payload['slice_id']))

    kinds = {}
    for unit, unit_steps in steps.items():
        if {'claude_final_review', 'codex_final_review'} & unit_steps:  # allowlist:provider -- persisted steps
            kinds[unit] = 'abschlussreview'
        elif 'codex_final_correction' in unit_steps:  # allowlist:provider -- persisted step
            kinds[unit] = 'korrekturrunde'
        elif {'claude_slice_review', 'codex_implementation'} & unit_steps:  # allowlist:provider -- persisted steps
            kinds[unit] = 'slice'
        else:
            kinds[unit] = 'sonstige'
    return kinds, slices


def analyse(run_dir: pathlib.Path) -> dict:
    records = load_records(run_dir)
    kinds, unit_slice = work_unit_kinds(records)
    planned = {int(s) for unit, s in unit_slice.items() if s.isdigit() and kinds.get(unit) == 'slice'}
    last_implementation = max((int(u) for u, k in kinds.items() if k == 'slice'), default=0)

    scope = {}
    for record in records:
        if record['record_type'] != 'slice_boundary':
            continue
        payload = record['payload']
        slice_id = int(payload['slice_id'])
        if slice_id in planned:
            grouped = {path for group in payload.get('scope_change_groups', []) for path in group}
            scope[slice_id] = grouped or set(payload.get('paths', []))

    opened: dict[str, dict] = {}
    final: dict[str, dict] = {}
    after_implementation: dict[str, str] = {}
    for record in records:
        if record['record_type'] != 'finding_transition':
            continue
        payload = record['payload']
        finding = payload['finding_id']
        unit = str(payload.get('work_unit_id'))
        if payload.get('action') == 'opened':
            text = ' '.join(str(payload.get(key) or '') for key in ('summary', 'rationale', 'acceptance_test'))
            opened[finding] = {
                'unit': unit,
                'kind': kinds.get(unit, 'sonstige'),
                'slice': payload.get('origin_slice_id'),
                'severity': payload.get('severity'),
                'paths': {p for p in CODE_PATH.findall(text) if not p.startswith('docs/')},
            }
        final[finding] = {'status': payload.get('finding_status'), 'unit': unit}
        if unit.isdigit() and int(unit) <= last_implementation:
            after_implementation[finding] = payload.get('finding_status')

    from_slice = [f for f, v in opened.items() if v['kind'] == 'slice']
    carried, orphaned = [], []
    for finding in from_slice:
        origin = opened[finding]['slice']
        if not str(origin or '').isdigit():
            continue
        origin = int(origin)
        targets = sorted(s for s, paths in scope.items() if s > origin and opened[finding]['paths'] & paths)
        (carried if targets else orphaned).append((finding, origin, targets))

    provider = collections.Counter()
    for record in records:
        if record['record_type'] == 'provider_attempt' and record['payload'].get('duration_seconds'):
            provider[record['payload']['operation']] += record['payload']['duration_seconds']

    return {
        'run': run_dir.name,
        'planned_slices': len(planned),
        'opened': opened,
        'final': final,
        'open_after_implementation': [f for f, s in after_implementation.items() if s == 'open'],
        'open_at_end': [f for f, v in final.items() if v['status'] == 'open'],
        'from_slice': from_slice,
        'carried_by_later_slice': carried,
        'orphaned': orphaned,
        'provider_seconds': provider,
    }


def report(result: dict) -> None:
    opened, final = result['opened'], result['final']
    severity = collections.Counter(v['severity'] for v in opened.values())
    context = collections.Counter(v['kind'] for v in opened.values())
    still_open = [f for f in result['open_at_end'] if f in opened]
    total = sum(result['provider_seconds'].values())
    tail = sum(s for op, s in result['provider_seconds'].items() if 'final' in op or 'correction' in op)

    print(f"\n{'=' * 72}\n{result['run']}\n{'=' * 72}")
    print(f"  Planslices                        : {result['planned_slices']}")
    print(f"  Findings eröffnet                 : {len(opened)}  "
          f"({', '.join(f'{k}={v}' for k, v in severity.most_common())})")
    print(f"  Eröffnungskontext                 : {', '.join(f'{k}={v}' for k, v in context.most_common())}")
    print(f"  offen am Ende der Implementierung : {len(result['open_after_implementation'])}")
    print(f"  offen am Laufende                 : {len(result['open_at_end'])}")
    if still_open:
        print(f"    davon nach Kontext              : "
              f"{', '.join(f'{k}={v}' for k, v in collections.Counter(opened[f]['kind'] for f in still_open).most_common())}")

    decided_locally = sum(1 for f in result['from_slice']
                          if final[f]['unit'] == opened[f]['unit'] and final[f]['status'] == 'closed')
    print(f"\n  Von {len(result['from_slice'])} Slicereview-Findings in der eigenen Arbeitseinheit entschieden: {decided_locally}")
    carried, orphaned = result['carried_by_later_slice'], result['orphaned']
    checked = len(carried) + len(orphaned)
    if checked:
        print(f"  Ein späterer Planslice hätte sie im Scope getragen: {len(carried)} von {checked}"
              f"  ({len(carried) * 100 // checked} %)")
        print(f"  Keiner — Fall für die Branchplanung              : {len(orphaned)}")
    if total:
        print(f"\n  Providerzeit {total / 3600:.2f} h, davon Abschluss und Korrektur {tail * 100 / total:.1f} %")


if __name__ == '__main__':
    if len(sys.argv) < 2:
        raise SystemExit(__doc__)
    for argument in sys.argv[1:]:
        report(analyse(pathlib.Path(argument)))
