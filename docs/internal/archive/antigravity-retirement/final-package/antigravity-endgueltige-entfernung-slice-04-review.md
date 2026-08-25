# Slice 04 – Korrekturslice für geschlossene Finding-IDs und Retirementguard

## Anlass

Das manuelle Codex-Gesamtreview des Stands `48d6aac0eacd` hat zwei
ausführbare Blocker festgestellt:

1. Die autoritative `structured-v2`-Artefaktschicht akzeptiert `A-01` noch in
   Finding-Transitionen, Reviewrecords und Korrektur-Work-Units.
2. `src/prompts.py` und `src/orchestrator.py` enthalten noch unerreichbare,
   aber aktive A-Namensraumzweige, die der Retirementguard nicht erkennt.

Dieser Slice ist eine reine Konvergenzkorrektur. Er führt keine neue Rolle,
keinen neuen Providerpfad und keine neue Observation ein.

## Ziel

- Ein gemeinsamer C-Finding-ID-Vertrag gilt an Python-Modell, JSON-Schema und
  Deserialisierungsgrenze.
- Alle aktiven Findingnummerierungen sind bedingungslos Claude-/C-gebunden.
- Der Retirementguard erkennt auch indirekte A-Präfixkonstruktionen.
- Gültige `C-*`-Records, Kollisionsmigration, Replay und Projektion bleiben
  unverändert funktionsfähig.

## Exakter Änderungspfad

- `docs/internal/antigravity-endgueltige-entfernung-slice-04-review.md`
- `schemas/orchestrator-artifact-v2.schema.json`
- `src/artifact_models.py`
- `src/orchestrator.py`
- `src/prompts.py`
- `tests/test_artifact_models.py`
- `tests/test_artifact_migration.py`
- `tests/test_language_consistency.py`
- `tests/test_orchestrator_runtime.py`
- `tests/test_prompts.py`

## Umsetzung

1. In `src/artifact_models.py` einen kanonischen Finding-ID-Prüfer für
   `^C-(0[1-9]|[1-9][0-9]*)$` ergänzen und ihn für
   `CorrectionWorkUnitPayload.finding_ids`, `ReviewPayload.finding_ids` und
   `FindingTransitionPayload.finding_id` verwenden.
2. Im v2-Artefaktschema entsprechende wiederverwendbare Definitionen für
   einzelne, leere und nichtleere C-Finding-ID-Mengen anlegen und dieselben
   drei Recordformen daran binden.
3. In `src/prompts.py` die nächste Finding-ID ausschließlich im
   `C-*`-Namensraum bestimmen.
4. In `_carry_forward_findings()` nur C-IDs inventarisieren und Kollisionen
   ausschließlich auf die nächste freie C-ID abbilden.
5. Den Retirementguard um eng gebundene Muster für einen `else "A"`-
   Präfixfallback und eine explizite `("C", "A")`-Präfixinventur erweitern.
6. Providerfreie Regressionen ergänzen, die `A-01` auf Modell-, Schema- und
   Deserialisierungsebene abweisen, gültige C-Records roundtrippen lassen und
   beide strukturellen Guardrückfälle erkennen.

## Akzeptanzkriterien

- `A-01` wird in allen drei betroffenen v2-Recordformen bereits vom
  Python-Modell abgewiesen.
- Ein direkt manipuliertes v2-Dokument mit `A-01` wird vom gebündelten Schema
  und damit von `ArtifactRecord.from_dict()` abgewiesen.
- Gültige `C-01`-Records roundtrippen unverändert.
- Prompt- und Carry-forward-Logik besitzen keinen A-Fallback; wiederverwendete
  C-IDs werden weiterhin stabil und deterministisch auf freie C-IDs migriert.
- Der Retirementguard schlägt auf beide entfernten Quelltextformen an.
- Die fokussierte Matrix ist grün.
- Die vollständige Repositorymatrix ist grün.
- `git diff --check` ist sauber.

## Validierung

Fokussiert:

```bash
python3 -m pytest \
  tests/test_artifact_models.py \
  tests/test_artifact_migration.py \
  tests/test_prompts.py \
  tests/test_orchestrator_runtime.py \
  tests/test_language_consistency.py -v
```

Vollständig:

```bash
python3 -m pytest tests/ -v
```

## Reviewauftrag an Claude

Claude prüft den vollständigen Slice-04-Diff adversarial gegen beide
Codex-Blocker. Als Korrekturslice darf der Review keine neue Observation
erzeugen. Ein neuer ausführbarer Defekt ist ein Blocker; andernfalls müssen
die beiden Befunde ausdrücklich als geschlossen bestätigt werden.

## Implementierungsergebnis

- Das Artefaktmodell und das gebündelte v2-Schema verwenden nun denselben
  kanonischen C-Finding-ID-Vertrag.
- `A-01` scheitert für Finding-Transition, Review und Correction-Work-Unit an
  Modell-, Schema- und Deserialisierungsgrenze.
- Prompt- und Carry-forward-Logik besitzen keinen A-Namensraumzweig mehr.
- Der Retirementguard erkennt die beiden entfernten strukturellen
  Rückfallformen durch reale Inventur-Negativkontrollen.
- Der stärkere Modellvertrag machte einen bisherigen Migrations-Negativtest
  unerreichbar. Der Test bestätigt nun ausdrücklich die frühere fail-closed
  Ablehnung vor der Migrationslogik; die übrigen Migrations-Near-Misses bleiben
  unverändert erhalten.

## Validierungsergebnis

- Fokussierte Matrix: `197 passed in 35.62s`.
- Vollständige Repositorymatrix: `1168 passed in 71.33s`.
- Externe Provider und Canaries wurden nicht aufgerufen.

TEST_FILES_TOUCHED: tests/test_artifact_models.py, tests/test_artifact_migration.py, tests/test_language_consistency.py, tests/test_orchestrator_runtime.py, tests/test_prompts.py
IMPLEMENTATION_READY: 04 | YES
STATUS: DONE

---

## Claude-Review zu Slice 04

REVIEWER: claude

Manueller Korrekturslice-Review außerhalb von `run_task`, ohne Provideraufruf
und ohne strukturierte JSON-Ausgabe. Produktcode und Tests waren strikt
read-only. Reviewbasis: letzter freigegebener Paketstand `48d6aac0eacd`, der
vollständige Diff `git diff 48d6aac` über neun Pfade sowie die drei untracked
Reviewdokumente, die ich ausdrücklich als Dateien gelesen habe. Die vorgelegte
Evidenz (`197 passed in 35.62s`, `1168 passed in 71.33s`) habe ich nicht
reproduziert; `git diff --check` mit Exitcode `0` habe ich bestätigt. Alle
Gegenproben sind providerfrei.

### Disposition der beiden Codex-Blocker

**CODEX-BLOCKER-01 – `structured-v2` akzeptiert `A-*`-Finding-IDs: CLOSED.**

Der Vertrag ist an allen drei Grenzen identisch und lückenlos gebunden. Ich
habe die Musterinventur des v2-Schemas vollständig aufgelöst: Es existieren
genau drei Finding-ID-Träger — `correction_work_unit.finding_ids`,
`review.finding_ids` und `finding_transition.finding_id` — und alle drei
zeigen jetzt auf `#/$defs/finding_id` beziehungsweise die davon abgeleiteten
Mengen. Ein vierter Träger existiert nicht. Auf Python-Seite decken
`_require_finding_id()` und `_require_unique_finding_ids()` exakt dieselben
drei Stellen ab.

Am Modell habe ich für alle drei Recordformen `A-01`, `A-99`, `a-01`, `F-07`,
`C-0`, `C-00`, `C-001` und die leere ID durchgespielt — jede wird abgewiesen.
Gültige IDs bleiben zulässig, einschließlich der Randfälle `C-1`, `C-10` und
`C-100`, und alle drei Records roundtrippen über `to_dict()`/`from_dict()`
identisch.

Am serialisierten Dokument habe ich `A-01` nachträglich in ein gültiges
`C-01`-Record injiziert: `validate_artifact_document()` **und**
`ArtifactRecord.from_dict()` weisen es in allen drei Formen mit
`schema validation failed at payload.finding…` ab. `from_dict()` validiert das
gebündelte Schema vor der Rehydrierung (`src/artifact_models.py:790-794`),
sodass die Schemagrenze tatsächlich vor der Modellgrenze greift.

Die Zulässigkeit leerer Findingmengen ist unverändert: `review` mit `denied`
und leerer Menge bleibt erlaubt (Schema ohne `minItems`, Python mit
`allow_empty=True`), `approved` ohne Findings und ohne Evidenz bleibt
abgewiesen, und `correction_work_unit` mit leerer Menge bleibt abgewiesen
(Schema `minItems: 1`, Python `allow_empty=False`). Die Umstellung von
`identifiers`/`non_empty_identifiers` auf die Finding-Varianten hat die
Kardinalitätsregeln eins zu eins übernommen.

Bemerkenswert und positiv: `src/contracts.py:15` führt seit Längerem
`SOURCE_FINDING_ID_PATTERN = re.compile(r"^C-(0[1-9]|[1-9][0-9]*)$")` — der
neue Modell- und Schemavertrag ist zeichengleich damit. Damit sprechen
Textvertrag, Artefaktmodell und JSON-Schema erstmals denselben Satz.

**CODEX-BLOCKER-02 – aktive A-Namensraumzweige: CLOSED.**

`src/prompts.py::build_v3_review_contract()` und
`src/orchestrator.py::_carry_forward_findings()` enthalten kein
`"A"`-Literal mehr. Ich habe die Nummerierung am Verhalten geprüft, nicht am
Quelltext: Für die Vorgeschichten `()`, `("C-01",)`, `("C-01","C-02")`,
`("C-09",)`, `("C-99",)` und `("C-1","C-10")` liefert der Prompt exakt `C-01`,
`C-02`, `C-03`, `C-10`, `C-100` und `C-11`, und in keinem gerenderten Prompt
kommt eine `A-`-Sequenz vor. Die Zehner- und Hundertergrenze ist damit
mitgeprüft.

In `_carry_forward_findings()` wird `finding.origin.reporter` weiterhin
verwendet — aber ausschließlich als Bestandteil des Identitätstupels, nicht
mehr zur Präfixwahl. Das ist korrekt und keine Restverzweigung. Die
Kollisionsmigration ist unverändert stabil und idempotent; ich habe die
mitgelieferte Regression `test_carry_forward_findings_migrates_reused_legacy_ids_stably`
direkt ausgeführt, die neben der Erstmigration auf `["C-01","C-02"]` auch die
Wiederanwendung auf die bereits migrierte Historie prüft.

### C-08 – erfüllt

Die Forderung aus `C-08` hatte zwei Teile, und beide sind umgesetzt.
`src/workflow_state.py:143-144` führt `NATIVE_CLAUDE_REVIEW_TRANSPORT =
"native-claude-review-v2"` und `NATIVE_CODEX_RESULT_TRANSPORT =
"native-codex-v2"`; dieselben v2-Werte stehen in `src/artifact_models.py:213`
und `:283`, in `src/native_codex_request.py:30`, in
`src/native_review_request.py:32` sowie als `const` in
`schemas/native-agent-codex-request-v2.schema.json`,
`schemas/native-agent-review-request-v2.schema.json` und an beiden
Transportstellen des Artefaktschemas. `ProtocolBinding.__post_init__` bindet
jede gesetzte Transportangabe an `structured-v2`
(`src/workflow_state.py:201-213`, „native Claude review transport requires
structured-v2"). Der Retirementguard führt die abgelösten Werte
`native-codex-v1` und `native-claude-review-v1` in seiner Verbotsliste
(`tests/test_language_consistency.py:611-612`); beide lösen nachweislich aus.
Damit trägt der v2-Kern keine v1-Restidentität mehr, und die im Finding
verlangte Guardbindung existiert. `C-08` ist geschlossen.

### Die beiden neuen Guardmuster

Beide Muster erkennen genau die entfernten Formen. `\belse\s+["']A["']` trifft
den gelöschten Prompt-Fallback in doppelter und einfacher Quotierung und bei
zusätzlichem Leerraum; das Inventurmuster trifft
`for prefix in ("C", "A")` ebenso in einfacher Quotierung und mit Leerraum
innerhalb der Klammern. Beide sind case-sensitiv, was zur entfernten
Schreibweise passt.

Ich habe die Fehlalarmgrenze gemessen: `for prefix in ("C", "B")`,
`for prefix in ["C", "A"]` und `("A", "C")` lösen nicht aus, ebenso wenig
deutsche Prosa mit `"C"` und `"A"`. Das Fallbackmuster hingegen trifft auch
harmlose Buchstabenverwendungen wie `grade = "B" if score > 80 else "A"` oder
`ch = "9" if n < 10 else "A"`. Das ist die sichere Richtung — ein solcher Fund
macht den Guard rot und erzwingt einen menschlichen Blick, statt etwas still
durchzulassen — und heute ohne Wirkung, weil die aktive Fläche sauber ist.

Umgehungsvarianten existieren und sind trivial: `else ("A")`, die invertierte
Ternäre `"A" if not claude else "C"`, ein Dict-Lookup, die Listenform der
Inventur, eine ausgelagerte Tupelkonstante oder `chr(65)` passieren alle. Ich
stufe das ausdrücklich **nicht** als Defekt ein, weil der Guard hier nur die
sechste Schicht über einem bereits fail-closed geschlossenen Vertrag ist. Ich
habe die Geschlossenheit gemessen statt sie anzunehmen: Ein
`NEW_FINDING: A-05` — wie ihn ein wiedereingeführter Fallback erzeugen würde —
wird vom Textvertrag mit `invalid finding id 'A-05' (expected C-01)`
abgewiesen, ebenso `A-01` und `A-99`; `StepContract.existing_finding_ids`
weist eine `A-05`-Vorgeschichte bereits bei der Konstruktion ab
(`src/contracts.py:376-378`); der native Pfad bindet `expected_prefix = "C-"`;
und Artefaktmodell wie Schema weisen `A-*` ab. Ein umgangener Guard könnte
also keinen A-Namensraum in einen Lauf bringen.

### Negativkontrollmarkierungen

Repositoryweit existieren sechs `# retirement-negative-control`-Markierungen,
alle in Testdateien und alle auf echten Negativfixturen: vier neue in
`tests/test_artifact_models.py` (Zeilen 221, 233, 240, 297) auf den drei
`A-01`-Konstruktionsfällen und der `A-01`-Injektion in das serialisierte
Dokument, dazu die beiden bestehenden in `tests/test_contracts.py:464` und
`tests/test_native_review_contract.py:243`. Keine Markierung liegt auf
Produktcode, und keine liegt auf einer Zeile, die neben der Negativfixture
noch anderes verbirgt. Die siebte Fundstelle ist die Filterbedingung im Guard
selbst.

### Migrations-Negativtest

Die Änderung ist fachlich korrekt. Der bisherige Parametrisierungsfall
`wrong-finding-prefix` baute eine `CorrectionWorkUnitPayload` mit
`finding_ids=("F-07",)`; diese Konstruktion scheitert seit Slice 04 bereits im
`__post_init__`, der Fall wäre also nicht mehr aufbaubar gewesen. Der neue
`test_wrong_finding_prefix_is_rejected_before_correction_migration` prüft
genau diese frühere fail-closed Ablehnung. Die übrigen fünf Near-Miss-Fälle
(`wrong-round`, `approved-verdict`, `finding-outside-review`,
`correction-before-review`, `duplicate-review`) bleiben unverändert erhalten;
insbesondere bleibt mit `finding-outside-review` ein `C-`-Fall bestehen, der
die Teilmengenprüfung isoliert. Es geht keine Negativabdeckung verloren —
die Prüfung wandert nur an die Stelle, an der sie jetzt tatsächlich greift.

### Keine versteckte Replay-, Bridge- oder Projektionsregression

Alle produktiven Erzeuger von Finding-IDs schreiben ausschließlich `C-`:
`f"C-{next_number:02d}"` in `src/orchestrator.py:3054`,
`f"C-{next_finding_number:02d}"` in `src/prompts.py:61` und
`next_native_finding_id()` mit festem `C`. Ein Wert unterhalb `C-01` ist nicht
konstruierbar, weil beide Zähler aus `max(..., default=0) + 1` stammen. In der
lokalen `.orchestrator/`-Ablage habe ich nach `A-*`-Identitäten gesucht: Der
einzige Treffer `A-99` steht in einer historischen Logdatei
(`logs/work-unit-0005-claude_slice_review.attempt-1.log`) als Fließtext eines
alten Reviews, nicht in einer Recordkette. Kein persistierter v2-Record trägt
eine A-Identität, ein Resume bricht also nicht.

Die Testanzahl stützt das quantitativ: `1160` vor dem Slice, `1168` danach.
Das entspricht genau neun neuen Tests (drei Modellablehnungen, drei
Schema-/Deserialisierungsfälle, ein Migrationsfall, zwei Guard-Inventurfälle)
minus dem einen entfernten Parametrisierungsfall. Es ist also nichts still
weggefallen.

### Scope und Dokumentation

Der im Slice-Bericht deklarierte Änderungspfad umfasst zehn Einträge; `git
status` weist exakt die neun dort genannten Produkt- und Testpfade plus den
Bericht selbst aus. Es findet sich keine Änderung außerhalb des Scope, keine
neue Rolle, kein neuer Providerpfad und keine neue Observation. Der Bericht
beschreibt Ziel, Umsetzung, Akzeptanzkriterien, Validierung und
Implementierungsergebnis widerspruchsfrei zum tatsächlichen Diff.

FINDING_STATUS: C-08 | CLOSED | Beide Teilforderungen sind erfüllt und einzeln nachgeprüft. Die nativen Transportidentitäten sind vollständig auf v2 gehoben — `NATIVE_CLAUDE_REVIEW_TRANSPORT = "native-claude-review-v2"` und `NATIVE_CODEX_RESULT_TRANSPORT = "native-codex-v2"` in `src/workflow_state.py:143-144`, dieselben Werte in `src/artifact_models.py:213` und `:283`, `src/native_codex_request.py:30`, `src/native_review_request.py:32` sowie als `const` in beiden nativen v2-Requestschemata und an beiden Transportstellen von `schemas/orchestrator-artifact-v2.schema.json` — und `ProtocolBinding.__post_init__` bindet jede gesetzte Transportangabe an `structured-v2` (`src/workflow_state.py:201-213`). Die im Finding verlangte Guardbindung existiert ebenfalls: Der Retirementguard führt `native-codex-v1` und `native-claude-review-v1` als ausgeschriebene verbotene Identitäten (`tests/test_language_consistency.py:611-612`), und beide lösen nachweislich aus. Damit trägt der v2-Kern keine v1-Restidentität mehr, und die Entscheidung ist nicht stillschweigend getroffen worden.

REVIEW_EVIDENCE: Semantische Gleichheit von Python- und JSON-Schema-Findingvertrag über 15 Grenzfälle einschließlich `C-0`, `C-00`, `C-001`, `C-100`, `a-01` und Randleerraum; vollständige Auflösung der Finding-ID-Träger im v2-Schema auf genau drei Stellen ohne vierten Träger; Ablehnung von `A-01`, `A-99`, `a-01`, `F-07` und leerer ID an allen drei Modellgrenzen; Roundtrip gültiger `C-01`-, `C-1`-, `C-10`- und `C-100`-Records über `to_dict()`/`from_dict()`; Injektion von `A-01` in serialisierte Dokumente mit Ablehnung durch `validate_artifact_document()` und `ArtifactRecord.from_dict()` in allen drei Formen; unveränderte Kardinalitätsregeln für leere Findingmengen in Schema und Modell; Zeichengleichheit mit `contracts.SOURCE_FINDING_ID_PATTERN`; verhaltensgeprüfte Prompt-Nummerierung über sechs Vorgeschichten einschließlich der Grenzen `C-09→C-10` und `C-99→C-100`; Ausführung der Carry-forward-Stabilitäts- und Idempotenzregression; Treffer- und Fehlalarmgrenze beider neuer Guardmuster über je sieben Varianten; Nachweis der fail-closed Ablehnung von `A-05`, `A-01` und `A-99` im Textvertrag sowie der Konstruktionsablehnung in `StepContract`; Inventur der sechs `retirement-negative-control`-Markierungen; Prüfung der lokalen `.orchestrator/`-Ablage auf persistierte A-Identitäten; Testanzahlbilanz 1160 → 1168 gegen die neun neuen und einen entfernten Fall; Scopeabgleich zwischen deklariertem Änderungspfad und `git status`; `git diff --check` mit Exitcode 0 | Die beiden neuen Guardmuster sind enge Struktur-Tripwires und keine Barriere: `else ("A")`, eine invertierte Ternäre, ein Dict-Lookup, die Listenform der Präfixinventur, eine ausgelagerte Tupelkonstante und `chr(65)` passieren sie sämtlich, und das Fallbackmuster erzeugt umgekehrt Fehlalarme für harmlose Buchstabenverwendungen wie `grade = "B" if score > 80 else "A"`; tragend ist deshalb nicht der Guard, sondern der an fünf unabhängigen Stellen fail-closed geschlossene C-Vertrag | Ein künftiger Hilfsrecord oder eine neue Korrekturfunktion verwendet den allgemeinen `identifier`-Typ statt `finding_id`, sodass eine vierte Finding-ID-Trägerstelle ohne den engeren Vertrag entsteht — der Guard sieht sie nicht, weil sie keine der beiden Strukturformen verwendet, und das Schema akzeptiert sie, weil `identifier` beliebige Präfixe zulässt

PRE_MORTEM: In drei Monaten ist die wahrscheinlichste Fehlerursache nicht die Rückkehr des A-Namensraums über eine der jetzt geschlossenen drei Stellen, sondern das Entstehen einer vierten. Der v2-Vertrag kennt mit `identifier` weiterhin einen bequemen, permissiven Stringtyp, und jede neue Recordform — ein Findingindex, ein Korrekturbündel, ein Auditverweis — wird zuerst damit gebaut, weil er überall sonst im Schema steht. Der engere `finding_id`-Typ muss aktiv gewählt werden, und nichts erzwingt diese Wahl: Weder der Retirementguard, der nur zwei sehr spezifische Quelltextformen kennt, noch ein Test, der die Menge der Finding-ID-Träger festhält. Die zweite, leisere Variante betrifft die Schemagrenze selbst: Das JSON-Schema wird mit Pythons `re.search` ausgewertet, wo `$` auch vor einem abschließenden Zeilenumbruch trifft, sodass `"C-01\n"` das Schema passiert und erst am `fullmatch` des Modells scheitert. Heute ist das fail-closed, weil `from_dict()` beide Grenzen durchläuft; wer künftig eine Kette allein über `validate_artifact_document()` freigibt, verliert diese zweite Grenze, ohne dass ein Test das bemerkt.

SLICE_APPROVAL: 04 | YES

STATUS: DONE
