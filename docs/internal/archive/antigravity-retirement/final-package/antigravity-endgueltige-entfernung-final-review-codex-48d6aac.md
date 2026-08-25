# Codex-Abschlussreview: endgültige Entfernung von Antigravity

Datum: 2026-08-25

Reviewbasis: `b3d49ff5ff2d` (`docs: archive native contract closure`)
Reviewstand: `48d6aac0eacd` (`feat(orchestrator): complete antigravity retirement`)

## Urteil

Der Paketstand ist noch nicht abschlussfähig. Die produktive Topologie ist auf
Codex und Claude reduziert, Claude besitzt die alleinige unabhängige
Freigabeautorität, `structured-v2` und die Agentprofile sind in den
Neulaufzustand integriert, und der dokumentierte Abschlusslauf ist grün. Zwei
ausführbare Vertragsabweichungen verletzen jedoch die ausdrücklichen
Retirement- und v2-Abnahmekriterien.

Codex erteilt keine eigene Freigabe. Die folgenden Blocker müssen korrigiert
und anschließend von Claude unabhängig disponiert werden.

## Geprüfte Evidenz

- Vollständiger Paketdiff `git diff b3d49ff..48d6aac` einschließlich Plan,
  aller drei Slices, Runtime, Schemas, Replay, CLI, Dokumentation und Tests.
- Dokumentierter Matrixstand aus Slice 03: `1160 passed in 74.44s`.
- Dokumentierter fokussierter Retirementguard-Lauf: `31 passed in 5.36s`.
- Aktuelles `git diff --check`: Exitcode `0`; ausschließlich bekannte
  Zeilenendewarnungen.
- Die vollständige Testsuite und externe Provider-Canaries wurden im Review
  nicht wiederholt.
- Zwei kleine providerfreie Gegenproben wurden ausschließlich zur
  Falsifikation konkreter Vertragsbehauptungen ausgeführt.

## CODEX-BLOCKER-01 – `structured-v2` akzeptiert weiterhin `A-*`-Finding-IDs

Der Arbeitsplan verlangt, dass alle v2-Schemata und Domänenkonverter nur
`C-*`-Findings akzeptieren. Pflichtszenario 14 fordert ausdrücklich die
Ablehnung eines v2-Findings mit `A-01`; Stopbedingung 4 untersagt jedes
v2-Schema, das `A-*` akzeptiert.

Diese Invariante ist an der autoritativen Artefaktschicht nicht umgesetzt:

- `FindingTransitionPayload.__post_init__()` validiert `finding_id` in
  `src/artifact_models.py` nur als allgemeinen Identifier.
- `ReviewPayload` und `CorrectionWorkUnitPayload` verwenden für
  `finding_ids` ebenfalls nur die allgemeine Identifierprüfung.
- `schemas/orchestrator-artifact-v2.schema.json` bindet die entsprechenden
  Felder in `finding_transition`, `review` und `correction_work_unit` an
  `identifier`, `identifiers` beziehungsweise `non_empty_identifiers`, nicht
  an einen `C-*`-Typ.

Eine providerfreie Gegenprobe konstruierte nacheinander
`FindingTransitionPayload`, `ReviewPayload` und `CorrectionWorkUnitPayload`
mit `A-01`. Alle drei Objekte wurden vom Python-Modell akzeptiert, gegen das
gebündelte v2-Schema validiert und über `ArtifactRecord.from_dict()` ohne
Fehler deserialisiert. Damit kann ein fachlich ausgemusterter Namespace in die
append-only v2-Recordkette gelangen; eine spätere Ablehnung durch Replay oder
Workflow wäre zu spät und würde außerdem die behauptete geschlossene
Schema-/Domänengrenze verletzen.

### Abnahmekriterium

1. Ein gemeinsamer, exakt auf `^C-(0[1-9]|[1-9][0-9]*)$` begrenzter
   Finding-ID-Vertrag wird in Modell und Artefaktschema eingeführt.
2. Er wird mindestens auf `FindingTransitionPayload.finding_id`,
   `ReviewPayload.finding_ids` und `CorrectionWorkUnitPayload.finding_ids`
   angewendet.
3. Modellkonstruktion, Schema-Validierung und `ArtifactRecord.from_dict()`
   weisen `A-01` für alle drei Recordformen fail-closed ab; gültige `C-*`-IDs
   roundtrippen unverändert.
4. Bridge-, Replay- und Projektionspfade bleiben für gültige v2-Records grün.

## CODEX-BLOCKER-02 – aktive A-Namensraumzweige umgehen den Retirementguard

Zwei aktive Produktionsfunktionen enthalten weiterhin die ausgemusterte
Claude/A-Präfixverzweigung:

- `src/prompts.py` berechnet das Präfix als `C` für Claude und andernfalls
  als `A`, obwohl der Reviewvertrag nur noch Claude zulässt.
- `src/orchestrator.py::_carry_forward_findings()` inventarisiert weiterhin
  beide Präfixe `("C", "A")` und erzeugt bei ID-Kollisionen für jeden
  Nicht-Claude-Reporter eine neue `A-*`-ID, obwohl Finding-Ursprünge nur noch
  Claude erlauben.

Die Zweige sind unter den heutigen Typinvarianten unerreichbar, bleiben aber
aktive produktive Retirementlogik und widersprechen der endgültigen, nicht
temporären Entfernung. Sie können bei einer späteren Lockerung vorgelagerter
Validierung wieder wirksam werden.

Der statische Retirementguard erkennt beide Formen nicht. Eine providerfreie
Gegenprobe über `_retirement_hits()` lieferte für die originalen Ausschnitte
aus `src/prompts.py` und `src/orchestrator.py` jeweils eine leere Trefferliste.
Damit erfüllt der Guard sein Abnahmekriterium nicht, jede neue produktive
`A-*`-Referenz zu verhindern.

### Abnahmekriterium

1. `build_v3_review_contract()` verwendet ausschließlich den festen
   `C`-Namensraum und besitzt keinen alternativen A-Zweig.
2. `_carry_forward_findings()` nummeriert ausschließlich `C-*`-Findings und
   besitzt weder ein A-Präfixregister noch eine Nicht-Claude-Fallbacklogik.
3. Der Retirementguard erhält strukturelle Negativkontrollen, die genau diese
   beiden Arten von Quelltext-Rückfall erkennen, ohne allgemeine harmlose
   Buchstabenverwendungen zu verbieten.
4. Die fokussierten Prompt-, Orchestrator- und Language-Consistency-Tests
   belegen gültige C-Nummerierung, Kollisionsbehandlung und Guardwirkung.

## Finding- und Freigabestatus

Die früheren Claude-Findings `C-09`, `C-10` und `C-11` sind im Slice-03-Bericht
formal geschlossen. Für `C-08` findet sich dagegen in den geprüften
Paketdokumenten nur der ursprüngliche `NEW_FINDING`-Eintrag, aber kein
maschinenlesbarer `FINDING_STATUS: C-08 | CLOSED`. Die technische Forderung
aus `C-08` ist umgesetzt; vor einer positiven Claude-Gesamtfreigabe muss
Claude den eigenen Status trotzdem ausdrücklich schließen oder eskalieren.

## Reviewevidenz und Pre-Mortem

Geprüfte Dimensionen: vollständiger Paketdiff, Rollen- und Step-Topologie,
Freigabeautorität, strukturierte-v2-Modelle und -Schemas, Findingnamespace,
Replay-/Korrekturgrenzen, Agentprofile, automatische Commitgrenze,
Retirementinventur, Dokumentationskonsistenz und vorhandene Testevidenz.

Größtes Restrisiko nach Behebung der Blocker bleibt die noch absichtlich
vorhandene textuelle Ingress-Kompatibilität: Sie gehört laut Plan in ein
späteres Arbeitspaket und darf weder als strukturierte Autorität noch als Weg
zur Wiedereinführung eines dritten Reviewers wachsen.

Pre-Mortem: In drei Monaten wird ein Hilfsrecord oder eine neue
Korrekturfunktion mit dem allgemeinen Identifiertyp statt dem engeren
Finding-ID-Typ gebaut. Ein historisch vertrautes `A-*` gelangt dadurch wieder
in die Recordkette, während der tokenbasierte Guard die indirekte
Präfixkonstruktion übersieht. Ohne gemeinsame Modell-/Schema-Typisierung und
gezielte strukturelle Guardkontrollen würde der Fehler erst beim Resume oder
in einer Korrekturrunde sichtbar.

FINAL_REPORT_READY: NO
STATUS: DONE
