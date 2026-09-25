# Slice 1 von 4 – Prüfer-Policy für entscheidbare Risiken und Testabdeckung

<!-- audit:status:begin -->
Freigegeben in Runde 1 · 0 Befunde, 0 geschlossen · Validierung grün
<!-- audit:status:end -->

## Ziel

<!-- audit:goal:begin -->
Die native Policy verpflichtet den Prüfer, im Snapshot entscheidbare Aussagen nachzumessen und einen bestätigten Mangel als Finding zu erfassen.
<!-- audit:goal:end -->

## Akzeptanzkriterien

<!-- audit:acceptance:begin -->
- Gegen SOURCE ist die Policy ausdrücklich auf Plan-, Slice- und Schlussreviews anwendbar: Ein im bereitgestellten Snapshot entscheidbares Restrisiko wird anhand der Quelle geprüft; bestätigt es sich, entsteht ein Finding statt einer bloßen Restrisiko-Notiz.
- Gegen SOURCE darf „nicht verifizierbar“ nur mit konkretem Grund verwendet werden, warum der tatsächlich bereitgestellte Snapshot die Entscheidung nicht erlaubt; der Prüfer prüft vorhandene Dateien und Diff, bevor er diese Grenze behauptet.
- Gegen SOURCE benennt der Prüfer bei behaupteter Abdeckung eines Akzeptanzkriteriums durch einen Test dessen tragende Assertion und den Bezug zum betroffenen Pfad oder eröffnet ein Finding. Ein fokussierter Test hält den verbindlichen Wortlaut der Policy fest; das native Review-Schema bleibt unverändert.
<!-- audit:acceptance:end -->

## Umfang

<!-- audit:scope:begin -->
- `docs/internal/kettenende-nachbessern-implement-review-7cf1c8a7.md`
- `docs/internal/slice-kettenende-nachbessern-arbeitsplan-01-prufer-policy-fur-entscheidbare-risiken-und-testabdeckung.md`
- `src/prompts.py`
- `tests/test_prompts.py`
<!-- audit:scope:end -->

## Umsetzung

> Noch nicht dokumentiert.

## Abweichungen vom Plan

> Keine.

## Verlauf

<!-- audit:history:begin -->
- Runde 1: Umsetzung · Validierung grün · Prüfurteil freigegeben · 0 neu, 0 geschlossen.
<!-- audit:history:end -->

## Befunde

<!-- audit:findings:begin -->
Keine.
<!-- audit:findings:end -->

## Validierung

<!-- audit:validation:begin -->
- Runde 1: `python3 -m pytest tests/ -v -m 'not crash_harness'` · grün · Exitcode 0.
<!-- audit:validation:end -->

## Abschlussprüfung

<!-- audit:approval:begin -->
Geprüft:
> Korrektheit: Die neuen Stringfragmente in src/prompts.py ergeben, zusammengesetzt, einen zusammenhängenden Policy-Text. Sie stehen vor der Slice-Commit-Pflicht in NATIVE_CLAUDE_SYSTEM_POLICY. Alle drei Akzeptanzkriterien sind wörtlich abgebildet: Geltung für Plan-, Slice- und Schlussreviews, Finding statt bloßer Restrisiko-Notiz, 'not verifiable' nur mit konkretem Grund nach Prüfung von Dateien und Diff sowie Benennung der tragenden Assertion samt Pfadbezug. Testabdeckung: tests/test_prompts.py::test_claude_policy_requires_source_checks_for_risks_and_test_coverage prüft acht Teilstrings per `assert required in policy` direkt gegen NATIVE_CLAUDE_SYSTEM_POLICY aus src/prompts.py. Ich habe jeden Teilstring über die Grenzen der Python-Stringliterale hinweg gegen die Konkatenation abgeglichen, alle passen. Verträge: Das Schema bleibt unverändert, weil der Diff nur die beiden Pfade berührt. Der Umfang liegt innerhalb der Allowlist. Die Attestation validation-[Hash ausgelassen] ist PASS für den gebundenen Fingerprint [Hash ausgelassen]…. Sicherheit und Resume: Es gibt nur statischen Prompttext, keine Zustands- oder Seiteneffektpfade.

Größtes Restrisiko:
> Der Begriff 'SOURCE' ist im Policy-Text nicht definiert. Er stammt aus dem Arbeitsplan. Ein Prüfer könnte ihn ohne entsprechenden Request-Kontext uneinheitlich deuten. Außerdem sichert der Test nur Teilstrings ab und nicht, dass diese Policy tatsächlich für Plan- und Schlussreviews an Claude übergeben wird. Der Snapshot enthält die Aufrufstellen nicht. Das bestehende Präfix meines eigenen Systemprompts stimmt aber mit der Konstante überein.

Bruchbedingung:
> Die Zusicherung bricht, wenn Plan- oder Schlussreviews eine andere Policy-Konstante als NATIVE_CLAUDE_SYSTEM_POLICY verwenden. Sie bricht ebenso, wenn ein späterer Umbau die Fragmente so umbricht oder umformuliert, dass der Sinn verloren geht und die geprüften Teilstrings trotzdem erhalten bleiben.

Vorab-Risikoanalyse:
> Wenn dieser Slice später scheitert, dann wahrscheinlich, weil die Policy nur als Text wirkt: Prüfer halten sich möglicherweise nicht daran, und kein deterministischer Validator erzwingt die Nennung der Assertion. Möglich ist auch, dass 'SOURCE' für ein Schlussreview ohne Snapshot-Kontext missverstanden wird. Beides liegt im Rahmen des Plans, der ausdrücklich nur eine Policy-Formulierung ohne Schemaänderung verlangt.
<!-- audit:approval:end -->

<!-- audit:reference:begin -->
Technischer Bezug: Lauf `watch-20260925-173857.063332Z-042e93b2e5d0`, Arbeitseinheit(en) 2; Nachweise in der Recordkette.
<!-- audit:reference:end -->
