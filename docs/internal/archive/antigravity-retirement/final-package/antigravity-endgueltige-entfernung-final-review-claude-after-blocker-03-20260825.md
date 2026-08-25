REVIEWER: claude

# Claude-Gesamtreview – Endgültige Entfernung von Antigravity (nach CODEX-BLOCKER-03)

Datum: 2026-08-25

Manuelles Gesamtreview außerhalb von `run_task`, ohne Provideraufruf, ohne
strukturierte JSON-Ausgabe und ohne Canary. Produktcode, Tests, Schemas,
Konfiguration und alle bestehenden Plan-, Slice- und Reviewberichte waren
strikt read-only; ich habe ausschließlich diese Datei erzeugt.

## Reviewgrenze und tatsächlich geprüfter Stand

- Basis vor dem Arbeitspaket: `b3d49ff5ff2d`
- Commitstand: `48d6aac0eacd`
- zusätzlich der uncommittete Arbeitsbaum mit Slice 04 und der
  Blocker-03-Korrektur

Geprüft wurden `git diff b3d49ff..HEAD`, `git diff HEAD`, `git status --short`
sowie alle benannten getrackten und ungetrackten Dokumente als Dateien. Der
Arbeitsbaum umfasst neun geänderte Produkt- und Testpfade und fünf ungetrackte
Reviewdokumente; es gibt keine weitere Änderung.

Die vorgelegte Evidenz (`86 passed in 12.54s`, `1171 passed in 72.38s`) habe
ich vereinbarungsgemäß nicht reproduziert. Ich habe stattdessen eine fokussierte
Teilmatrix und gezielte providerfreie Gegenproben ausgeführt; alle sind unten
exakt dokumentiert. `git diff --check` habe ich mit Exitcode `0` bestätigt.
Die Gesamtzahl `1171` habe ich indirekt verifiziert: Eine Selektion über den
Fail-closed-Protokollpfad meldete `5 passed, 1166 deselected`, also exakt 1171
gesammelte Tests.

## 1. CODEX-BLOCKER-03 – unabhängig nachgeprüft und geschlossen

Der Kern der Korrektur ist der Schematyp
`"finding_id": {"type": "string", "pattern": "^C-(0[1-9]|[1-9][0-9]*)(?![\\s\\S])"}`.
Der Offlinevalidator wertet `pattern` mit `re.search()` ohne Flags aus
(`src/schema_validation.py:145-147`); `^` bindet damit an Position 0 und
`(?![\s\S])` an das tatsächliche Ende der Eingabe, weil `[\s\S]` jedes Zeichen
einschließlich Zeilenumbrüchen trifft. Das ist exakt die Semantik von
`fullmatch()`.

Ich habe das nicht angenommen, sondern gemessen. Für **alle drei** Recordformen
habe ich einen gültigen `C-01`-Record serialisiert, das Feld manipuliert und
sowohl `validate_artifact_document()` als auch `ArtifactRecord.from_dict()`
aufgerufen:

| Nachgestelltes Zeichen | `re.search` | `fullmatch` | von einer der drei Formen akzeptiert |
|---|---|---|---|
| LF | nein | nein | keine |
| CR | nein | nein | keine |
| CRLF | nein | nein | keine |
| TAB | nein | nein | keine |
| Leerzeichen | nein | nein | keine |
| NUL | nein | nein | keine |
| U+2028 | nein | nein | keine |
| U+2029 | nein | nein | keine |
| VT, FF, NEL (Zusatzproben) | nein | nein | keine |
| LF + `A-01` | nein | nein | keine |

Führende und eingebettete Varianten (`\nC-01`, `" C-01"`, `C-\n01`,
`X\nC-01`) werden ebenfalls von beiden Grenzen abgewiesen — die
Nicht-MULTILINE-Bindung von `^` hält.

Gültig und in allen drei Formen akzeptiert bleiben `C-1`, `C-01`, `C-09`,
`C-10` und `C-100`. Ungültig und in allen drei Formen abgewiesen bleiben
`A-01`, `C-0`, `C-00`, `C-001` sowie zusätzlich `F-07`, `c-01`, `C-`, `C--1`,
`C-01a` und der Leerstring.

Zur Wertemengen-Gleichheit: Über ein Korpus von 31 Werten — die fünf gültigen,
die zehn ungültigen und alle zwölf Zeichenanhänge plus führende Varianten —
gibt es **null** Divergenzen zwischen `re.search(schema_pattern, v)` und
`_FINDING_ID_RE.fullmatch(v)`. Schema und Modell akzeptieren nach der Korrektur
nachweislich dieselbe Menge.

Zur Frage der globalen Semantik: Die Lookahead-Bindung steht an genau einer
Stelle. Ich habe alle acht `pattern`-Vorkommen des Artifact-v2-Schemas
aufgelöst; nur `$defs/finding_id` verwendet `(?![\s\S])`, die übrigen sieben
sind unverändert. Der Validator selbst wurde nicht angefasst — `schema_validation.py`
steht in keiner Änderungsliste. Die Korrektur verengt also einen einzelnen
Typ, statt die `pattern`-Auswertung global zu verändern.

Zur Übertragungsfrage: `orchestrator-artifact-v2.schema.json` wird
ausschließlich über `_SCHEMA_PATH` in `src/artifact_models.py:32` geladen. Eine
repositoryweite Suche nach `orchestrator-artifact` in `src/` liefert genau
diese eine Fundstelle. Die Provider-Writerschemata entstehen dagegen aus
`native-agent-codex-*-v2` beziehungsweise `native-agent-review-*-v2`
(`src/native_codex_request.py:32`, `src/native_review_request.py:35`). Die
Endebindung erreicht also weder Codex noch Claude als Structured-Output-Schema.

**CODEX-BLOCKER-03: geschlossen.**

## 2. CODEX-BLOCKER-01 und CODEX-BLOCKER-02 – geschlossen

**Blocker 01.** Ich habe die Finding-ID-Träger des v2-Schemas vollständig
aufgelöst: Es existieren genau drei — `finding_transition.finding_id`,
`review.finding_ids[]` und `correction_work_unit.finding_ids[]` — und alle drei
zeigen auf `#/$defs/finding_id` beziehungsweise die davon abgeleiteten Mengen
`finding_ids` und `non_empty_finding_ids`. Ein vierter aktiver Träger existiert
nicht; das ist keine Behauptung aus dem Codex-Bericht, sondern das Ergebnis
einer rekursiven Traversierung des geladenen Schemadokuments. Auf Python-Seite
decken `_require_finding_id()` und `_require_unique_finding_ids()` exakt
dieselben drei Stellen ab.

Die Kardinalitätsregeln sind unverändert übernommen: `review` mit `denied` und
leerer Menge bleibt zulässig, `approved` ohne Findings und ohne Evidenz bleibt
abgewiesen, `correction_work_unit` mit leerer Menge bleibt abgewiesen.

**Blocker 02.** `src/prompts.py` und `src/orchestrator.py::_carry_forward_findings()`
enthalten kein `"A"`-Literal mehr. Verhaltensgeprüft statt quelltextgeprüft:
Für die Vorgeschichten `()`, `("C-01",)`, `("C-01","C-02")`, `("C-09",)`,
`("C-99",)` und `("C-1","C-10")` liefert `build_v3_review_contract()` exakt
`C-01`, `C-02`, `C-03`, `C-10`, `C-100` und `C-11`, jeweils ohne jede
`A-`-Sequenz im gerenderten Prompt. Die verbliebene Verwendung von
`finding.origin.reporter` in `_carry_forward_findings()` ist Bestandteil des
Identitätstupels, nicht der Präfixwahl.

Repositoryweit gibt es exakt drei Finding-ID-Erzeuger, alle bedingungslos
C-gebunden: `src/native_review_contract.py:876` (`prefix = "C"`),
`src/orchestrator.py:3054` und `src/prompts.py:61` (beide `f"C-{...:02d}"`).
Beide Zähler stammen aus `max(..., default=0) + 1`, ein Wert unterhalb `C-01`
ist nicht konstruierbar. In `artifact_replay.py`, `artifact_projection.py`,
`artifact_bridge.py` und `artifact_migration.py` findet sich keine
A-Präfixlogik; die dortigen `prefix`-Treffer sind Markdown-Zeilenpräfixe und
`removeprefix("work-unit-")`.

## 3. Planerfüllung, Topologie und Freigabeautorität

Der aktive Bestand ist antigravityfrei: Eine Suche nach `antigravity`, `agy`,
`agy.exe` und `ANTIGRAVITY_` über `src/`, `tests/`, `schemas/`, `run_task`,
`orchestrator.toml`, `workflow.puml` und `pyproject.toml` liefert **null**
Treffer (die Guarddatei selbst ausgenommen). `AgentRole` führt nur `codex` und
`claude`; das Schema bindet `reviewer` auf `{"enum": ["claude"]}`.
`build_agent_registry()` ist fail-closed („agent settings must contain exactly
codex and claude"). `ANTIGRAVITY.md` existiert nicht mehr; unter `schemas/`
stehen nur die fünf v2-Verträge und die beiden absichtlich eigenständig
versionierten Provider-Subset-Register.

Die Commitautorisierung habe ich im Quelltext gelesen statt aus dem Bericht
übernommen (`src/workflow.py:3368-3428`). Der Commit setzt kumulativ voraus:
eine Attestierung für **denselben** Fingerprint wie der aktuelle Diff,
`attestation.passed` oder `complete` mit benanntem Red-State-Folgeslice,
`claude.approval is True` **und** `claude.validation == attestation`, keinen
offenen `BLOCKER`, und — falls aktiviert — das manuelle Gate. Fehlt oder
veraltet die Bindung, springt der Lauf auf `CLAUDE_PLAN_REVIEW` beziehungsweise
`CLAUDE_SLICE_REVIEW` zurück und committet nicht. In dieser Bedingung kommt
keine zweite Reviewerinstanz vor: Nach positiver Claude-Freigabe taktet der
Workflow ohne Zustimmung Dritter weiter und stößt genau einen Commit an. Codex
besitzt an keiner Stelle Freigabeautorität.

Die Git-Grenze hält: `src/git_service.py` ruft ausschließlich `add`,
`cat-file`, `commit`, `diff`, `ls-files`, `merge-base`, `rev-list`,
`rev-parse`, `switch` und `symbolic-ref` auf. Die Verben `push`, `merge`,
`remote`, `fetch` und `pull` kommen im gesamten Produktcode nicht vor.

Zu Gesamt-Abnahmekriterium 14 („genau drei Slices"): Es liegen vier
Sliceberichte vor. Slice 04 ist kein vierter Implementierungsslice, sondern die
Korrekturrunde, die der Plan in §6.2 und §6.4 selbst für den Fall eines
gefundenen Blockers vorsieht; sie wurde durch den Codex-Selbstcheck ausgelöst
und von mir als Korrekturslice freigegeben. Die geplante Zerlegung des
Arbeitspakets besteht weiterhin aus drei Implementierungsslices. Ich werte das
als planhaft und nicht als Widerspruch, halte die wörtliche Abweichung aber
ausdrücklich fest. Kriterium 16 (Archivierung, lokaler Commit, Merge nach
`master`) ist bewusst noch offen — es folgt konstruktionsgemäß erst nach dieser
Freigabe und dem ausdrücklichen Git-Handoff.

## 4. Structured-v2 und Fail-closed-Verhalten

Neue Läufe sind an `structured-v2` gebunden; historische `legacy-state-v3`- und
`structured-v1`-Zustände werden mit `UNSUPPORTED-PROTOCOL` fail-closed
abgewiesen. Ich habe die zugehörigen Regressionen gezielt selektiert und
ausgeführt: `5 passed, 1166 deselected in 3.07s`. Die nativen
Transportidentitäten sind vollständig auf v2 gehoben, und
`ProtocolBinding.__post_init__` bindet jede gesetzte Transportangabe an
`structured-v2` (`src/workflow_state.py:201-213`).

Agentprofile: Die Defaults sind exakt `gpt-5.6-sol`/`medium` und
`sonnet`/`high` (`src/agent_config.py:27-33`). Die Driftprüfung
`_apply_resumed_agent_profiles()` läuft in `src/orchestrator.py:3887`, also
nachweislich **vor** `build_agent_registry(args.agent_settings)` in `:3932`;
ein abweichendes explizites Profil hält mit `AGENT-PROFILE-DIFF` an, bevor ein
Provider startet.

## 5. Retirementguard, Archive und Dokumentation

Der Guard leitet seine Inventur aus den Pfadklassen in `orchestrator.toml` ab
und erfasst 105 Dateien; die Grundlinie ist sauber (null Treffer). Nicht
inventarisiert sind nur `.gitattributes`, `.gitignore` (in keiner Pfadklasse
deklariert), die elf namentlich gebundenen Evidenzdokumente dieses
Arbeitspakets unter `docs/internal/` und die Guarddatei selbst über
`THIS_FILE`. Ich habe die Erkennung einzeln gemessen; alle dreizehn geprüften
Rückfallformen lösen aus:

`antigravity`, `ANTIGRAVITY_`, `agy`, `agy.exe`, `native-codex-v1`,
`native-claude-review-v1`, `orchestrator-artifact-v1`, `A-02`, `A-*` im
Fließtext, `"A-"` als Präfixliteral, `^[CA]-` als Schemamuster sowie die beiden
in Slice 04 ergänzten Formen `prefix = "C" if … else "A"` und
`for prefix in ("C", "A")`.

Kein Fehlalarm entsteht für `[A-Za-z]`-Zeichenklassen, SHA-256-Muster,
`for prefix in ("C", "B")` und — wichtig — für die beiden aktiven Register
`native-provider-schema-capabilities-v1` und
`native-provider-schema-exceptions-v1`. Diese passieren nicht über eine
Ausnahme, sondern weil der Guard als Verbotsliste konkreter abgelöster
Identitäten arbeitet; sie werden also nicht fälschlich als Workflow-v1-Rest
gewertet.

Die Negativkontrollen sind sauber getrennt: Sechs
`# retirement-negative-control`-Markierungen liegen ausschließlich in
Testdateien auf echten `A-01`-Fixtures, keine auf Produktcode. Das Archiv
`docs/internal/archive/antigravity-retirement/` enthält die zehn abgelösten
Dokumente und Schemata plus eine README, die sie als nichtautoritative
historische Evidenz kennzeichnet; weder Runtime noch aktiver Test liest daraus.
Der Marktvergleich gibt Antigravity nur über eine exakte, inhaltsgebundene
Zeilen-Allowlist frei, deren Mengengleichheit gegen das reale Dokument
testgesichert ist.

## 6. Findinginventur

Das Arbeitspaket enthält elf Claude-eigene Findings, `C-01` bis `C-11`. Für
jedes existiert genau ein von mir verfasster `FINDING_STATUS: … | CLOSED` in
einem Claude-Reviewabschnitt: `C-01` bis `C-04` und `C-05` bis `C-07` in den
Planreviews des Arbeitsplans, `C-08` im Slice-04-Bericht, `C-09` bis `C-11` in
den Slice-03-Berichten. Kein Claude-Finding ist offen, und keine Schließung
stützt sich auf eine Codex-Antwort oder einen Codex-Selbstcheck — alle beruhen
auf eigenen Gegenproben. Die drei Codex-Blocker sind oben technisch
nachgeprüft und geschlossen.

## 7. Eigene Gegenproben (providerfrei, exakt)

1. Finding-ID-Wertemengenvergleich Schema gegen Modell über 31 Werte:
   null Divergenzen.
2. Serialisierte Injektion je Recordform für zwölf Zeichenanhänge und vier
   Positionsvarianten gegen `validate_artifact_document()` **und**
   `ArtifactRecord.from_dict()`: keine Akzeptanz.
3. Rekursive Musterinventur des Artifact-v2-Schemas: acht `pattern`-Vorkommen,
   davon genau eines mit Lookahead-Endebindung.
4. Rekursive Trägerinventur der Finding-IDs im Schema: genau drei Träger.
5. Promptnummerierung über sechs Vorgeschichten einschließlich der Grenzen
   `C-09→C-10` und `C-99→C-100`.
6. Guard-Erkennungsmatrix: dreizehn Rückfallformen, fünf Fehlalarmkontrollen.
7. Fokussierte Matrix `tests/test_artifact_models.py`,
   `tests/test_artifact_migration.py`, `tests/test_language_consistency.py`,
   `tests/test_prompts.py`, `tests/test_orchestrator_runtime.py`:
   `200 passed in 34.57s`.
8. Fail-closed-Protokollselektion: `5 passed, 1166 deselected in 3.07s`
   (bestätigt zugleich die Gesamtzahl 1171).
9. `git diff --check`: Exitcode `0`.

## 8. Ein Befund unterhalb der Blockerschwelle, ausdrücklich benannt

Bei der Suche nach weiteren Instanzen der Blocker-03-Klasse habe ich alle acht
Schemamuster einzeln gegen einen nachgestellten LF geprüft und jeweils
end-to-end verfolgt, ob das Modell die Lücke schließt. Sieben Muster lassen den
LF am Schema passieren, werden aber vom Modell per `fullmatch()` abgewiesen —
also fail-closed, wie zuvor bei `finding_id`.

Eine Ausnahme: `work_unit.paths[]`. Der Wert `"src/a.py\n"` passiert sowohl
`validate_artifact_document()` als auch `ArtifactRecord.from_dict()`, weil
`_require_path()` nicht regexbasiert prüft, sondern über `PurePosixPath`, und
`PurePosixPath("src/a.py\n").as_posix()` den Wert unverändert zurückgibt.

Das ist **kein** Blocker dieses Arbeitspakets, und ich sage ausdrücklich warum:
`_require_path()` ist zeichengleich mit dem Stand `b3d49ff` vor dem
Arbeitspaket, liegt in keinem Änderungspfad der vier Slices und ist damit keine
Regression dieser Arbeit. Eine ausführbare Folge konnte ich zudem nicht zeigen:
Pfade entstehen zeilenbasiert aus Planparser, Git-Ausgabe oder CLI, sodass ein
eingebetteter Zeilenumbruch dort nicht entsteht; er setzt eine Handmanipulation
der Recordkette voraus, und der so erzeugte Pfad scheitert anschließend am
Scopeabgleich gegen den kanonischen Diff. Ich führe ihn als größtes
Restrisiko in `REVIEW_EVIDENCE`, damit er nicht verloren geht.

FINDING_STATUS: C-01 | CLOSED | Der Nachweisverbund aus Corpus, Manifest, Canaries und Differentialmatrix wurde beim v1→v2-Cutover planhaft aufgelöst statt nachgezogen; die Vollmatrix ist ohne diese Bindungen grün. In den Slice-01- und Slice-02-Reviews geprüft und geschlossen.
FINDING_STATUS: C-02 | CLOSED | Der Codex-Resultatvertrag liegt als `native-agent-codex-result-v2` vor und bindet Dispositionen auf `^C-(0[1-9]|[1-9][0-9]*)$`; eine Disposition auf `A-01` wird vor der Domänenkonvertierung fail-closed abgewiesen.
FINDING_STATUS: C-03 | CLOSED | Die Konstruktionsstelle des `ProtocolBinding` liegt im Änderungspfad; `_fresh_state()` übergibt beide aufgelösten Agentprofile, und ein Resume ohne Optionen übernimmt sie.
FINDING_STATUS: C-04 | CLOSED | Im Planreview disponiert und im Plan aufgenommen.
FINDING_STATUS: C-05 | CLOSED | Im Planreview disponiert und im Plan aufgenommen.
FINDING_STATUS: C-06 | CLOSED | Die geforderte Konstruktionsstelle für die resumefeste Profilbindung ist benannt und in Slice 02 als `_fresh_state(..., codex_profile=…, claude_profile=…)` umgesetzt.
FINDING_STATUS: C-07 | CLOSED | Das Provider-Subset-Register bleibt bewusst unter seiner eigenständigen `-v1`-Identität aktiv; der Retirementguard arbeitet als Verbotsliste konkreter abgelöster Identitäten und schlägt darauf nicht an, wie ich einzeln gegengeprüft habe.
FINDING_STATUS: C-08 | CLOSED | Beide nativen Transportidentitäten sind auf v2 gehoben (`native-claude-review-v2`, `native-codex-v2` in `src/workflow_state.py:143-144`, gleichlautend in `src/artifact_models.py:213` und `:283`, `src/native_codex_request.py:30`, `src/native_review_request.py:32` sowie als `const` in beiden nativen v2-Requestschemata und an beiden Transportstellen des Artifact-v2-Schemas); `ProtocolBinding.__post_init__` bindet jede gesetzte Transportangabe an `structured-v2`; der Retirementguard führt `native-codex-v1` und `native-claude-review-v1` als ausgeschriebene verbotene Identitäten, und beide lösen in meiner Erkennungsmatrix aus. Formal im Slice-04-Bericht geschlossen.
FINDING_STATUS: C-09 | CLOSED | Alle fünf allgemeinen Verhaltensverträge sind als Codex-Claude-only-Regressionen über echte Ausführungspfade wiederhergestellt und von mir mit zwei Gegenproben auf Nichtvakuität geprüft.
FINDING_STATUS: C-10 | CLOSED | Die Guardinventur ist aus `orchestrator.toml` abgeleitet und deckt gegen `git ls-files` abgeglichen die gesamte aktive Fläche ab; die Findingnamespace-Regel erkennt `A-02`, `A-*`, `"A-"` und `^[CA]-` und bleibt fehlalarmfrei für `[A-Za-z]` und SHA-256-Muster.
FINDING_STATUS: C-11 | CLOSED | Die Zeilenausnahmen für Marktvergleich und interne README sind auf exakte Inhaltsgleichheit umgestellt; alle drei historischen Rollenzeilen aus `5193eec` und eine frei erfundene Drei-Rollen-Tabellenzeile lösen den Guard aus, und jede Mutation einer erlaubten Zeile verliert die Freigabe.

REVIEW_EVIDENCE: Vollständiger Paketdiff `b3d49ff..HEAD` plus Arbeitsbaum und alle dreizehn benannten Dokumente; Rollen-, Step- und Registrytopologie mit null Antigravity-Treffern über `src/`, `tests/`, `schemas/`, `run_task`, `orchestrator.toml`, `workflow.puml` und `pyproject.toml`; Commitautorisierung im Quelltext gelesen (`src/workflow.py:3368-3428`) mit fingerprintgleicher Attestierung, `claude.validation == attestation`, Blockerfreiheit und Rücksprung auf den Reviewstep bei veralteter Bindung; Git-Verbinventur ohne `push`, `merge`, `remote`, `fetch`, `pull`; `structured-v2`-Bindung und `UNSUPPORTED-PROTOCOL` fail-closed selektiv ausgeführt; rekursive Musterinventur des Artifact-v2-Schemas (acht Muster, genau eines mit Lookahead) und rekursive Trägerinventur der Finding-IDs (genau drei Träger); Wertemengen-Gleichheit von Schema und Modell über 31 Werte ohne Divergenz; serialisierte Injektion je Recordform für zwölf Zeichenanhänge und vier Positionsvarianten gegen beide Grenzen; lokale Ladung des Artifact-Schemas als einzige Fundstelle und Trennung von den Provider-Writerschemata; drei C-gebundene Finding-ID-Erzeuger und A-freie Replay-, Projektions-, Bridge- und Migrationslogik; Promptnummerierung über sechs Vorgeschichten einschließlich `C-99→C-100`; Agentprofil-Defaults und Driftprüfung vor Registryaufbau; Guard-Erkennungsmatrix über dreizehn Rückfallformen mit fünf Fehlalarmkontrollen und sauberer Grundlinie über 105 inventarisierte Dateien; Archivvollständigkeit und Findinginventur `C-01` bis `C-11`; fokussierte Matrix `200 passed in 34.57s`; `git diff --check` Exitcode `0` | Die Blocker-03-Klasse ist im Artifact-v2-Schema nicht global geschlossen, sondern nur für `finding_id`: Sieben weitere Muster lassen einen nachgestellten LF am Schema passieren und werden nur deshalb nicht wirksam, weil das Python-Modell `fullmatch()` verwendet; die einzige Stelle ohne diese zweite Grenze ist `work_unit.paths[]`, wo `"src/a.py\n"` sowohl `validate_artifact_document()` als auch `ArtifactRecord.from_dict()` passiert, weil `_require_path()` über `PurePosixPath` statt über eine Regex prüft — zeichengleich mit dem Stand `b3d49ff`, in keinem Änderungspfad dieses Arbeitspakets und ohne demonstrierbare ausführbare Folge, weil Pfade zeilenbasiert entstehen und ein manipulierter Pfad am Scopeabgleich scheitert | Eine künftige Recordform führt einen vierten Findingbezug ein und verwendet den permissiven `identifier`-Typ statt `finding_id`, oder eine neue Freigabe stützt sich allein auf `validate_artifact_document()` ohne die anschließende Modellgrenze — in beiden Fällen fällt die heute doppelte Absicherung auf eine einzelne, schwächere Grenze zurück, ohne dass Guard oder Test anschlagen

PRE_MORTEM: In drei Monaten scheitert nicht die Entfernung, sondern ihre Statik. Der heutige Stand ist an fünf unabhängigen Stellen gegen den A-Namensraum geschlossen — Textvertrag, `StepContract`, nativer Reviewvertrag, Artefaktmodell und JSON-Schema — und der Retirementguard ist die sechste, nicht tragende Schicht. Genau diese Redundanz macht ihn verwundbar: Sie ist nirgends als Invariante festgehalten, sondern verteilt sich über fünf Module, die niemand gemeinsam liest. Der wahrscheinlichste Verlauf ist, dass eine neue Audit- oder Korrekturrecordform einen vierten Findingbezug einführt und aus Bequemlichkeit auf `identifier` zeigt, weil dieser Typ überall sonst im Schema steht und der engere `finding_id` aktiv gewählt werden muss. Nichts erzwingt diese Wahl: Der Guard kennt nur zwei sehr spezifische Quelltextformen, und kein Test hält die Menge der Finding-ID-Träger fest. Die zweite, leisere Variante ist die Stringende-Semantik: `finding_id` ist jetzt korrekt an `(?![\s\S])` gebunden, die sieben übrigen Muster stehen weiterhin auf `$` und verlassen sich stillschweigend auf das `fullmatch()` des Modells. Wer künftig eine Kette allein über `validate_artifact_document()` freigibt — etwa in einem Reparatur- oder Importwerkzeug —, verliert diese zweite Grenze für sieben Felder auf einmal, und bei `work_unit.paths[]` gibt es sie schon heute nicht.

FINAL_APPROVAL: YES

STATUS: DONE
