# Slice 06: Zentraler Contract- und Finding-Kern

**Status:** implementiert, vollständig validiert und durch Claude sowie Antigravity freigegeben
**Feature-Branch:** `feature/orchestrator-modernization`
**Branch-Basis des Slice:** `021a2aa62444567f197ecf3031fac51d87629c0c`
**GitHub-Status:** nur lokal; kein Upstream; Push und Merge sind nicht Bestandteil dieses Slice
**Arbeitsplan:** [orchestrator-modernization-work-plan.md](orchestrator-modernization-work-plan.md)

## Ziel

Ein neues typisiertes Contract-Modul führt schrittbezogene v3-Marker, Revieweridentität, Validierung, Teststatus, Findings, Implementiererantworten, Pre-Mortem, Stop-Anforderungen und strukturierte Anker in einer einzigen Parse- und Validierungsgrenze zusammen. Der Validator arbeitet fail-closed: fehlende oder widersprüchliche Pflichtrecords ergeben kein implizites Verdikt.

Der neue Kern wird additiv hinter dem expliziten Entwicklungsmodus vorbereitet. Der aktive v2-Ablauf verwendet bis zum gemeinsamen Cutover in Slice 18 weiterhin unverändert seine Phase-/Legacy-Marker, Prompts und Parsersemantik. Slice 06 schafft die typsichere Schnittstelle für State v3 und die spätere asymmetrische Reviewkette, aktiviert diese aber noch nicht.

## Akzeptanzkriterien

- Jeder v3-Agentenaufruf wird über einen expliziten typisierten Review- oder Codex-Schrittcontract beschrieben und durch den gemeinsamen Dispatcher validiert; es gibt keinen Validatoraufruf ohne Schritt-, Rollen-/Akteurs- und Markerbindung.
- Zulässige v3-Verdikte sind ausschließlich `PLAN_APPROVAL`, `SLICE_APPROVAL` und `FINAL_APPROVAL`; `PHASE1_APPROVAL`, `PHASE2_APPROVAL`, `CODEX_APPROVAL` und `CLAUDE_APPROVAL` werden im v3-Contract abgelehnt.
- `REVIEWER` wird separat und passend zum Schrittcontract validiert; Finding-IDs verwenden ausschließlich das zugehörige 1-basierte Quellenpräfix `C-01` beziehungsweise `A-01`.
- Ein typisierter Finding-Record bewahrt Klasse, Status, Kurzbeschreibung, Akzeptanzkriterium, Ursprungsslice, Runde, meldende Instanz, Implementiererantwort und Schlussbegründung über Öffnen, Bestreiten, Herabstufen und Schließen hinweg.
- `BLOCKER` verhindert ein positives Verdikt, solange er offen ist. Ein offenes `OBSERVATION`-Finding bleibt im Ergebnis erhalten, verhindert ein positives Verdikt aber nicht.
- `FINDING_RESPONSE: REJECTED` dokumentiert den Widerspruch und lässt das Finding offen; schließen oder von `BLOCKER` auf `OBSERVATION` herabstufen darf nur ein späterer Record des meldenden Reviewers.
- Jede gültige Reviewantwort enthält mindestens einen Finding- oder Prüfrecord. Ohne konkrete Schwäche ist ein typisierter, nicht blockierender `OBSERVATION`-Prüfrecord mit geprüften Dimensionen, größtem Restrisiko und realistischer Bruchbedingung Pflicht; pauschale Zustimmung ohne diese Evidenz ist ungültig.
- Ein positives Verdikt setzt ein vollständiges `PRE_MORTEM`, ein parsbares passendes Verdikt, `VALIDATION_RESULT: PASS` mit dazu passendem Exitcode, einen zulässigen Teststatus und keine offenen Blocker voraus. Ein negatives Verdikt setzt mindestens einen offenen Blocker voraus.
- `STOP_REQUESTED` und ein Marker aus der Freigabefamilie sind in derselben Antwort unvereinbar.
- `ANCHOR`-Records werden mit stabiler ID, vom Aufrufer gebundener Herkunft, Input/Fixture, Erwartungswert und Toleranz-/Rundungsregel typisiert geparst. Ein Vergleich erkennt neue, entfernte, verschobene oder inhaltlich geänderte Anker deterministisch; das Nutzer-Gate und die Rücksetzung der Planreviewkette folgen erst in Slice 11.
- Mehrfach vorkommende oder unbekannte singuläre Records, unvollständige Felder, Quellenpräfix-Konflikte und Markertext innerhalb abgegrenzter Prompt-Snapshots werden fail-closed behandelt.
- Die neuen v3-Promptbausteine leiten ihre Pflichtmarker aus dem jeweiligen Review- oder Codex-Schrittcontract ab; bestehende v2-Promptfunktionen und deren Text bleiben unverändert.
- Die bisherigen öffentlich importierten Parser- und Validatorfunktionen in `src/orchestrator.py` behalten Verhalten und Signaturen, damit der aktive Altpfad und bestehende Läufe bis Slice 18 startbar bleiben.
- Vollsuite und bestehender Dry-Run bleiben grün.

## Scope

### Produktive Programmdateien

- `src/contracts.py` (neu): Enums und unveränderliche Records, Review-/Codex-Schrittcontracts, gemeinsamer v3-Dispatcher, Finding-Lebenszyklus sowie Ankervergleich.
- `src/prompts.py`: ausschließlich additive v3-Contract-/Promptbausteine; bestehende v2-Builder werden nicht umformuliert.
- `src/orchestrator.py`: schmaler Kompatibilitätsrand beziehungsweise expliziter Development-Mode-Einstieg zum neuen Validator; die aktive v2-Aufrufkette bleibt unverändert.

### Dokumentation

- `docs/internal/orchestrator-modernization-work-plan.md`: Link und tatsächlicher Slice-Status.
- diese Slice-MD.

### Genehmigungspflichtige Testpfade

- `tests/test_contracts.py` (neu)
- `tests/test_parsing.py`
- `tests/test_prompts.py`

## Nicht-Scope

- Keine State-v3-Datei, Work-Unit- oder Resume-Logik; dies folgt in Slice 07.
- Keine Auditprojektion in Plan-/Slice-Dokumente; dies folgt in Slice 08.
- Keine Aktivierung der Branch-/Committransaktion, Test-, Anker-, Stop- oder Dateigrenzen-Gates; dies folgt in Slice 09, Slice 11 und Slice 12.
- Keine asymmetrische Claude-/Antigravity-State-Maschine und kein automatisches vollständiges Antigravity-Abschlussreview; dies folgt in Slice 10.
- Keine Umstellung des Defaultpfads oder der Root-Instruktionsdateien auf den v3-Marker-Namensraum; dies erfolgt atomar in Slice 18.
- Keine Änderungen an Agentenadaptern, CLI-Konfiguration, Repository-Diff, Pfadpolicy, State-v2-Dateien oder Watch-Modus.
- Kein Push oder Merge.

## Diff-Risiko vor dem ersten Code-Edit

**Branch-Check:** `feature/orchestrator-modernization`; entspricht dem Arbeitsplan.

**Status vor Slice-Beginn:** Der Arbeitsbaum enthielt ausschließlich die unversionierte Editor-Lockdatei `docs/internal/.~lock.slice-orchestrator-modernization-03-three-agent-adapters.md#`. Sie gehört nicht zu Slice 06 und wird weder gelesen noch verändert, reviewed, gestaged oder committet.

**HEAD:** `021a2aa62444567f197ecf3031fac51d87629c0c`.

**Merge-Base mit `master`:** `0bd3baddbac307b90e85db4c9bdcb5a787558652`.

**Geplante Dateien:** drei produktive Programmdateien, Arbeitsplan, Slice-MD und drei Testpfade. Die Grenze von zehn produktiven Dateien wird nicht erreicht.

**Voraussichtliche Änderungstiefe:** hoch innerhalb des neuen Contract-Moduls, niedrig im aktiven Ablauf. Das Hauptrisiko ist eine versehentliche Änderung der v2-Parser- oder Promptsemantik vor dem atomaren Cutover.

**Gefährdete bestehende Tests:** Markerpriorität, Delimiter-Injection-Schutz, Finding-Lebenszyklus des Altpfads, exakter Promptvertrag und bestehender Dry-Run.

**Nicht anfassen:** die Editor-Lockdatei, `.orchestrator`-State/Checkpoints, Agentenadapter, CLI-/TOML-Konfiguration, Diff-/Pfadmodule, Watcher, Root-Instruktionsdateien und bestehende v2-Prompttexte.

**Rollback-Strategie:** Änderungen werden dateiweise durch Gegenpatches zurückgeführt; die neue Datei wird nur nach ausdrücklicher Freigabe entfernt. Kein Hard Reset und kein pauschales Checkout.

## Teständerungs-Autorisierung

Der Ausgangsdiff gegen HEAD ist für alle drei vorgesehenen Testpfade leer. `tests/test_contracts.py` existiert noch nicht; die beiden bestehenden Dateien sind vor dem ersten Edit an folgende SHA-256 gebunden:

```text
087fba8259462b00851cd027e80ddc9ca154a281ae1749c5d6d05586343fc2d7  tests/test_parsing.py
c016c3e14cd3cf93e2dd125082580cdd1c9e99c1abeda67a01d90c39a227647e  tests/test_prompts.py
```

Der Nutzer hat die drei genannten Pfade am 2026-08-11 ausdrücklich freigegeben und zugleich entschieden, dass ab Slice 06 alle zur dokumentierten Slice-Intention gehörenden Teständerungen des aktuellen Modernisierungsarbeitsplans ohne erneutes separates Nutzergate zulässig sind. Für Slice 06 gilt folgende Testintention:

- tabellengetriebene Positiv-/Negativfälle für alle schrittbezogenen v3-Marker, Reviewerrollen und Quellenpräfixe,
- vollständiger Finding-Lebenszyklus mit Öffnen, Implementiererzustimmung/-widerspruch, Herabstufung und reviewergebundenem Schließen ohne Feldverlust,
- blockierende `BLOCKER` und nicht blockierende, weiterhin dokumentierte `OBSERVATION`-Records,
- Ablehnung pauschaler Zustimmung ohne Finding-/Prüfrecord sowie Akzeptanz eines vollständigen Prüfrecords,
- fehlendes/unvollständiges Pre-Mortem, fehlendes/unparsbares oder widersprüchliches Verdikt, rote Validierung, unzulässiger Teststatus und `STOP_REQUESTED`-Konflikt,
- Parsing und deterministischer Vergleich strukturierter Anker,
- Delimiter-/Duplikat-/unbekannte-Record-Fälle und Ablehnung aller Phase-/Legacy-Marker im v3-Contract,
- additive v3-Promptbausteine und Regressionstests, die Signaturen, Marker und wesentliche Textbestandteile des aktiven v2-Contracts unverändert festhalten.

```text
TEST_FILES_TOUCHED: tests/test_contracts.py,tests/test_parsing.py,tests/test_prompts.py
TEST_CHANGE_APPROVAL: YES | ausdrückliche Slice-06-Freigabe und arbeitsplanweite Vorabautorisierung des Nutzers vom 2026-08-11
```

## Geplante Validierung

1. Gezielte Tests der drei genehmigten Testpfade.
2. `PYTHONDONTWRITEBYTECODE=1 python3 -m pytest tests/ -v -p no:cacheprovider` gemäß Repositoryregel.
3. `python3 -m py_compile src/contracts.py src/prompts.py src/orchestrator.py`.
4. `./run_task --dry-run --skip-git-check --test-command ''` zur Regression des aktiven Altpfads.
5. `git diff --check`, Scopeprüfung und Test-Diff-Fingerprintprüfung.
6. Danach scopegenaues Claude-Sonnet-5-High-Review mit dem quota-reduzierten externen Reviewpaket.
7. Nach geschlossenen Claude-Blockern vollständiges Antigravity-Abschlussreview über den gesamten Slice-Diff seit dem hier dokumentierten Slice-Startcommit, nicht nur über den letzten Korrekturdelta.

## Durchgeführte Änderungen

- `src/contracts.py` führt unveränderliche Enums und Records für Reviewer, Verdict-/Readinessmarker, Validierung, Stop, Findings, Implementiererantworten, Prüfevidenz und Anker ein. Review- und Codex-Antworten laufen über explizite Schrittcontracts und einen gemeinsamen Dispatcher.
- Der Reviewvalidator erzwingt Revieweridentität, Quellenpräfix, Finding-/Prüfrecord, Testscope, Validierungsbefehl/-exitcode, Pre-Mortem-Reihenfolge, Verdiktkonsistenz, Stop-Exklusivität und eine benannte Folgeslice für Red-State-Ausnahmen. Unbekannte, malformed, doppelte sowie v2-/Legacy-Marker scheitern fail-closed außerhalb delimitierter Evidenzblöcke.
- Finding-Records bewahren Ursprung, Beschreibung und Akzeptanztest; Codex-Antworten werden angehängt, ohne den Status zu ändern. Nur der meldende Reviewer darf Status oder Klasse ändern. Ein vollständiger Testlebenszyklus beweist Öffnen, Widerspruch, Herabstufen und Schließen ohne Feldverlust.
- Anker erhalten ihre Herkunft vom vertrauenswürdigen Aufrufer. Der deterministische Vergleich erkennt hinzugefügte, entfernte, verschobene und inhaltlich geänderte IDs.
- `src/prompts.py` erzeugt additive v3-Vertragsblöcke für Reviewer und Codex aus denselben typisierten Schrittcontracts; die bestehenden v2-Builder wurden inhaltlich nicht verändert.
- `src/orchestrator.py` exportiert ausschließlich additive Entwicklungs-Einstiege für Review- und Codex-Contractvalidierung; die aktive v2-Aufrufkette bleibt unangetastet.
- `tests/test_contracts.py` prüft den v3-Kern tabellengetrieben und über vollständige Lebenszyklen. `tests/test_parsing.py` und `tests/test_prompts.py` prüfen die additive Integrationsgrenze und den weiterhin unveränderten Altcontract.
- Anforderungen, Arbeitsplan und Übergabe dokumentieren Revision 8: Für Slices 06–19 entfällt die wiederholte Testfreigabe, während R-10 als Zielworkflow unverändert bleibt.

## Ausgeführte Validierung

Ausgangsvalidierung vor dem ersten Produktiv- und Testcode-Edit:

```text
VALIDATION_RESULT: PASS | PYTHONDONTWRITEBYTECODE=1 python3 -m pytest tests/test_parsing.py tests/test_prompts.py -q -p no:cacheprovider | 0 (35 passed)
VALIDATION_RESULT: PASS | PYTHONDONTWRITEBYTECODE=1 python3 -m pytest tests/test_contracts.py tests/test_parsing.py tests/test_prompts.py -q -p no:cacheprovider | 0 (86 passed)
VALIDATION_RESULT: PASS | PYTHONDONTWRITEBYTECODE=1 python3 -m pytest tests/ -v -p no:cacheprovider | 0 (233 passed vor Claude-Findings)
VALIDATION_RESULT: PASS | PYTHONDONTWRITEBYTECODE=1 python3 -m pytest tests/test_contracts.py tests/test_parsing.py tests/test_prompts.py -q -p no:cacheprovider | 0 (89 passed nach F-001/F-002)
VALIDATION_RESULT: PASS | PYTHONDONTWRITEBYTECODE=1 python3 -m pytest tests/ -v -p no:cacheprovider | 0 (236 passed nach F-001/F-002)
VALIDATION_RESULT: PASS | python3 -m py_compile src/contracts.py src/prompts.py src/orchestrator.py | 0
VALIDATION_RESULT: PASS | ./run_task --dry-run --skip-git-check --test-command '' | 0; aktiver v2-Altpfad vollständig durchlaufen
VALIDATION_RESULT: PASS | git diff --check | 0
```

Testbindung vor dem Claude-Review:

```text
f85e17148086577d252fcbf2ca09185c84720a84eb2f4d0668ced52f3a1c6799  tests/test_parsing.py (binärer Diff gegen Slice-Start-HEAD)
8c809a65b22da78f4a5d0175e01e558ce0c5ac07d17fbb72a05d06caf9143901  tests/test_prompts.py (binärer Diff gegen Slice-Start-HEAD)
53ac18a46e60101e5620a27ea7d92f5908dc06f367d8d76a44051580002a84eb  tests/test_contracts.py (vollständiger Dateiinhalt nach F-001/F-002)
```

## Abweichungen vom Plan

- Der ursprünglich als ein `StepContract` beschriebene Einstieg ist in einen Review-`StepContract` und einen `CodexStepContract` getrennt. Ein gemeinsamer Dispatcher hält die einheitliche Validierungsgrenze aufrecht, während der Typ selbst unmögliche Kombinationen aus Approval- und Readinessmarker verhindert.
- `REVIEW_EVIDENCE` konkretisiert den im Plan geforderten No-Finding-`OBSERVATION`-Prüfrecord; `FINDING_RECLASSIFIED` macht Herabstufungen ohne Verlust der Finding-ID oder Ursprungsfelder explizit. Beide Records sind in Revision 8 der Markerübersicht ergänzt.

## Offene Risiken

- Der Marker-Namensraum ist sicherheitsrelevant: zu tolerantes Parsing könnte Text aus eingebetteten Snapshots oder veraltete Marker als Verdikt missverstehen.
- Finding-Updates sind zustandsbehaftet. Ohne unveränderliche Records oder explizite Merge-Regeln könnten Beschreibung, Akzeptanzkriterium, Ursprung oder Widerspruch still verloren gehen.
- Ein Prüfrecord ohne konkrete Schwäche darf nicht zu einer zweiten, schwächeren Freigabespur werden; Dimensionen, Restrisiko und Bruchbedingung müssen sämtlich inhaltlich vorhanden sein.
- Die tatsächliche Gate-Orchestrierung folgt in späteren Slices. Slice 06 beweist Contract-Entscheidungen, darf sie aber nicht irreführend als bereits aktiven Defaultworkflow dokumentieren.

## Review-Feedback von Claude

Claude Sonnet 5 mit Effort `high` prüfte zunächst das vollständige Slice-Paket seit `021a2aa` und führte den Harness genau einmal aus: 233 Tests bestanden, Diffcheck sauber, Schreibprobe blockiert. Der Lauf benötigte neun Turns, kostete laut JSON-Hülle 0,754 USD und meldete zwei Blocker:

- F-001: `_merge_review_findings` verlangte von Antigravity auch für offene Claude-Findings einen Statusrecord, obwohl nur Claude diese Findings ändern oder schließen darf. Ein gemischter Findingbestand hätte die spätere asymmetrische Kette deadlocken können.
- F-002: Der Reviewvalidator verwendete den je Runde wechselnden `contract.name` als Ankerherkunft. Ein unverändert erneut ausgegebener Anker wäre dadurch fälschlich als geändert erkannt worden.

Nach der Korrektur prüfte Claude ausschließlich beide Findings, `contracts.py`, die additive Promptgrenze und die Contracttests. Der Harness lief erneut genau einmal mit 236 grünen Tests. Der fokussierte Lauf benötigte fünf Turns und 0,358 USD; inhaltlich schloss er beide Findings ohne neuen Blocker, ließ aber die zwingenden Marker `FINDING_STATUS`, `OPEN_FINDINGS`, `PHASE2_APPROVAL` und `VALIDATION_RESULT` aus. Dieses fehlende parsbare Verdikt wurde fail-closed nicht als Freigabe gewertet.

Eine minimale Format-Recovery führte den Harness in einer frischen Reviewerinstanz erneut genau einmal aus und lieferte für 0,038 USD den formal gültigen Abschluss:

```text
FINDING_STATUS: F-001 | CLOSED | mixed-origin deadlock fixed and regression-covered
FINDING_STATUS: F-002 | CLOSED | stable explicit anchor origin fixed and regression-covered
VALIDATION_RESULT: PASS | python3 -m pytest tests/ -v | 0 (236 passed; diff clean; write probe blocked)
PRE_MORTEM: cross-reviewer state wiring could regress ownership filtering or stable anchor provenance
OPEN_FINDINGS: NONE
PHASE2_APPROVAL: YES
STATUS: DONE
```

## Review-Feedback von Antigravity

Antigravity erhielt das vollständige finale Slice-Paket seit dem persistierten Startcommit `021a2aa`: sämtliche drei Produktivdateien, alle drei Testpfade, Revision-8-Dokumente, die Slice-MD sowie beide Claude-Korrekturen. Das Paket war ausdrücklich kein Deltareview. Der unmittelbar vor dem Aufruf gebildete SHA-256-Fingerprint dieses Prüfstands lautet:

```text
5163e62349b50ed0a11a64ae8bc6b697eecaf787b00964648ea4c3b5e8bd7eef
```

Der Read-only-Harness lief genau einmal mit 236 grünen Tests. Antigravity bestätigte R-6, Finding-Lebenszyklus und Eigentumsgrenzen, F-001/F-002, Delimiterresistenz, den unveränderten R-10-Zielvertrag und die additive v2-Grenze. Als größtes Restrisiko nannte es Regex-Drift bei späteren Parseränderungen; die realistische Bruchbedingung wäre ein logisch widersprüchlicher Marker, der die fail-closed-Prüfung umgeht. Neue Findings entstanden nicht.

Die erste Antwort enthielt bereits `SLICE_APPROVAL: 06 | YES`, ließ aber `OPEN_FINDINGS: NONE` aus und wurde deshalb nicht als parsbares Verdikt akzeptiert. Dieselbe Antigravity-Conversation wurde ohne neuen Repositoryinput und ohne erneuten Harness ausschließlich zur Marker-Recovery fortgesetzt. Der gültige Abschluss lautet:

```text
REVIEWER: antigravity
VALIDATION_RESULT: PASS | python3 -m pytest tests/ -v | 0 (236 passed; diff clean; write probe blocked)
TEST_FILES_TOUCHED: tests/test_contracts.py,tests/test_parsing.py,tests/test_prompts.py
REVIEW_EVIDENCE: R-6, lifecycle, F-001/F-002, delimiter resistance, and R-10 integrity | regex marker drift | contradictory marker escapes fail-closed validation
PRE_MORTEM: future regex refactoring could weaken fail-closed validation
OPEN_FINDINGS: NONE
SLICE_APPROVAL: 06 | YES
STATUS: DONE
```

## Review-Antworten von Codex

- F-001 wurde akzeptiert. Nur offene Findings des aktuellen meldenden Reviewers verlangen nun `FINDING_STATUS`/`FINDING_RECLASSIFIED`; fremde Findings bleiben vollständig erhalten und wirken weiterhin als Blocker beziehungsweise Observation. Zwei Tests belegen Carry-forward und das weiterhin verbotene Fremdschließen.
- F-002 wurde akzeptiert. `StepContract.anchor_origin` bindet eine stabile, explizite Herkunft; ein ausgegebener Anker ohne diese Bindung scheitert. Zwei unterschiedliche Rundennamen mit derselben Herkunft vergleichen unverändert, eine tatsächliche Herkunftsänderung bleibt erkennbar.

## Entscheidungstabelle

| ID | Quelle | Finding | Klasse | Entscheidung | Umsetzung |
|---|---|---|---|---|---|
| F-001 | Claude | Fremde offene Findings erzeugen unauflösbare Updatepflicht | BLOCKER | CLOSED | Updatepflicht nach `origin.reporter` gefiltert; Carry-forward und Eigentumsgrenze getestet |
| F-002 | Claude | Rundenabhängiger Schrittname erzeugt falsche Ankeränderung | BLOCKER | CLOSED | stabile explizite `anchor_origin`; fehlende Bindung abgelehnt; Rundenvergleich getestet |

## Rückdokumentation in den Arbeitsplan

Der Arbeitsplan verlinkt diese Slice-MD und dokumentiert die arbeitsplanweite Revision-8-Ausnahme für Teständerungen ab Slice 06.

## Freigabestatus

- Teständerungen: ausdrücklich für die drei Slice-06-Pfade und vorab für alle weiteren scopegerechten Testanpassungen dieses Arbeitsplans autorisiert.
- Lokale Implementierung und Validierung: abgeschlossen; 236 Tests und aktiver v2-Dry-Run grün.
- Claude-Review: `PHASE2_APPROVAL: YES`; F-001/F-002 geschlossen, keine offenen Blocker.
- Antigravity-Review: vollständiger Slice-Diff geprüft; `SLICE_APPROVAL: 06 | YES`, `OPEN_FINDINGS: NONE`.
- Lokaler Commit: durch Antigravity autorisiert; scopegenaue Abschlussprüfung ausstehend.
