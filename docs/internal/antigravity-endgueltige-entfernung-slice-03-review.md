# Slice 03 – Dokumentation, Retirementguard und Gesamtabschluss

## Status

Slice 03 des Arbeitsplans `antigravity-endgueltige-entfernung-arbeitsplan.md`
ist vollständig implementiert und noch nicht committed. Die Reviewbasis ist der
von Claude freigegebene Slice-02-Commit:

- Basiscommit: `5193eec feat(orchestrator): remove antigravity runtime`
- Arbeitsbranch: `master`
- Entwicklungsmodus: manuell, ohne `run_task` und ohne Provideraufruf

## Umgesetzter Abschluss

### Aktive Rollen- und Betriebsverträge

- `ANTIGRAVITY.md` wurde gelöscht.
- `AGENTS.md`, `CLAUDE.md` und `CODEX.md` sind auf die aktive
  Codex-Claude-Topologie und `structured-v2` synchronisiert.
- README, Quickstart, Beispielauftrag, UML, interne Übersicht, Roadmap und
  Referenzarchitektur beschreiben Antigravity nicht mehr als Prozessrolle.
- Der Marktvergleich darf Antigravity weiterhin als externes Vergleichsprodukt
  nennen und grenzt es ausdrücklich vom Orchestrator ab.
- Historische `legacy-state-v3`- und `structured-v1`-Läufe werden als nicht
  unterstützte Protokolle dokumentiert; es gibt keine Migrations- oder
  Fallbackzusage.
- Der Normalbetrieb bleibt unverändert automatisiert: Der Orchestrator erzeugt
  die vollständige Validierungsattestierung, Claude reviewt denselben
  Fingerprint und eine positive Freigabe autorisiert den lokalen Commit.

### Nichtautoritative Archivierung

Die fünf abgelösten v1-Schemata und die fünf benannten Übergangsdokumente
liegen unter `docs/internal/archive/antigravity-retirement/`. Die dortige
`README.md` kennzeichnet sie ausdrücklich als historische, nichtautoritative
Evidenz. Aktive Runtime und Tests dürfen diesen Bereich nicht lesen.

Archiviert wurden:

- `orchestrator-artifact-v1.schema.json`
- `native-agent-codex-request-v1.schema.json`
- `native-agent-codex-result-v1.schema.json`
- `native-agent-review-request-v1.schema.json`
- `native-agent-review-result-v1.schema.json`
- `OPTION_CLAUDE_ONLY_REVIEW_POLICY.md`
- `native-agent-json-manueller-bootstrap.md`
- `native-claude-review-path-arbeitsplan.md`
- `native-claude-review-path-review.md`
- `phase-2-arbeitspaket-1-erkenntnisse-und-stabilisierung-1-1.md`

### Statischer Retirementguard

`tests/test_language_consistency.py` inventarisiert aktive Produkt-, Test-,
Schema-, Konfigurations- und Root-Dokumentpfade und stoppt bei produktiven
Resten von:

- Antigravity, `agy`, `agy.exe` und `ANTIGRAVITY_*`,
- `A-*`-Findingregeln,
- den fünf archivierten Workflow-/Agent-v1-Identitäten,
- den abgelösten nativen Transport-v1-Identitäten.

Die einzigen aktiven v1-Ausnahmen bleiben die unabhängig versionierten und
vollständig ausgeschriebenen Provider-Subset-Register
`native-provider-schema-capabilities-v1` und
`native-provider-schema-exceptions-v1`. Es gibt keinen allgemeinen
`*-v1`-Skip. Eine synthetische Negativkontrolle beweist, dass ein neu
eingefügter aktiver Antigravity-Verweis den Guard auslöst. Zwei notwendige
historische `A-01`-Negativbeispiele sind zeilengenau mit
`# retirement-negative-control` markiert; die Markierung ist keine globale
Pfad- oder Musterfreigabe.

Ein eigener Test stellt zusätzlich sicher, dass aktive Runtime und Tests den
Archivbereich nicht lesen. Der bisherige Test, der ein historisches
v1-Schemaartefakt aktiv aus dem Archiv geladen hätte, wurde deshalb entfernt.

## Übernommene Slice-02-Observation

FINDING_RESPONSE: C-09 | ACCEPTED | Die mit den Zwei-Reviewer-Szenarien entfernte allgemeine Vertragsabdeckung wurde als Codex-Claude-only-Verhaltensregression wiederhergestellt: eine Attestierung wird je Fingerprint nur einmal erzeugt und von Claude wiederverwendet; eine unvollständige Attestierung erreicht weder Claude noch Commit; ein stale Claude-Reviewfingerprint erzwingt Revalidierung vor dem Commit; Resume aus dem persistierten Claude-Schritt startet Codex nicht erneut. Zusätzlich prüft die lokale Reviewnormalisierung den gebundenen Testmarker, ohne Finding-ID, Klasse, Zusammenfassung oder Status zu verändern.

Die neuen Regressionen liegen in:

- `tests/test_workflow.py`
- `tests/test_review_runtime_hardening.py`

Diese Pfade ergänzen den ursprünglichen Slice-03-Pfad gezielt zur Behebung des
von Claude in Slice 02 eröffneten Findings `C-09`. Die beiden zeilengenauen
Negativkontrollmarkierungen liegen zusätzlich in
`tests/test_contracts.py` und `tests/test_native_review_contract.py`; die
Entfernung des aktiven Archivreaders betrifft
`tests/test_native_review_request.py`.

## Validierung

### Fokussierte Matrix

```text
PYTHONDONTWRITEBYTECODE=1 python3 -m pytest \
  tests/test_language_consistency.py \
  tests/test_final_review_preflight.py \
  tests/test_git_service.py \
  tests/test_parsing.py \
  tests/test_plan_handoff.py \
  tests/test_prompts.py \
  tests/test_review_runtime_hardening.py \
  tests/test_workflow.py \
  -q -p no:cacheprovider
```

- Ergebnis: `199 passed in 18.90s`
- Exitcode: `0`

### Vollständige Repositorymatrix

```text
PYTHONDONTWRITEBYTECODE=1 python3 -m pytest tests/ -v -p no:cacheprovider
```

- Ergebnis: `1148 passed in 81.79s`
- Exitcode: `0`
- Vollständiges Log: `/tmp/dao-antigravity-retirement-slice03-pytest.log`
- SHA-256 des Logs:
  `df8e827144a95eec0da7c57e2b9c30e70533ea2927473622ee864bea0bc7e9a7`

### Diffhygiene

- `git diff --check`: Exitcode `0`
- Git meldet ausschließlich die bestehende Windows-Zeilenendenkonfiguration;
  es verbleibt kein Whitespacefehler.

## Grenzen und Reviewfokus

- Die im Arbeitsplan und in den Slice-Berichten selbst enthaltenen
  historischen Antigravity-Bezüge bleiben bis zur branchweiten Freigabe aktive
  Entwicklungsevidenz. Sie werden erst beim abschließenden Archivierungsschritt
  verschoben und sind deshalb nicht Teil des Produkt-Retirementguards.
- Die bestehenden nativen Codex-/Claude-Transportoptionen sind keine
  Antigravity-Skipflags und bleiben in diesem Arbeitspaket erhalten.
- Der spätere Rückbau verbleibender Text-Ingressadapter ist laut Arbeitsplan ein
  separates Folgepaket; Slice 03 führt keinen stillen Parserumbau durch.
- Claude soll besonders prüfen, ob der Retirementguard eng genug ist, ohne
  historische Negativkontrollen oder den externen Marktvergleich zu
  fehlklassifizieren, und ob `C-09` durch echte Verhaltensnachweise geschlossen
  ist.

TEST_FILES_TOUCHED: tests/test_contracts.py, tests/test_language_consistency.py, tests/test_native_review_contract.py, tests/test_native_review_request.py, tests/test_review_runtime_hardening.py, tests/test_workflow.py

IMPLEMENTATION_READY: 03 | YES

STATUS: DONE

---

## Claude Implementierungsreview – Slice 03

Manueller Review außerhalb von `run_task`, ohne Provideraufruf und ohne
strukturierte JSON-Ausgabe. Reviewbasis: Slice-02-Commit
`5193eec feat(orchestrator): remove antigravity runtime`, der vollständige
uncommittete Diff `git diff 5193eec` samt der untracked Pfade
`docs/internal/archive/antigravity-retirement/`, `AGENTS.md`, der Arbeitsplan
sowie der Slice-02-Reviewbericht. Die vorgelegte Validierungsevidenz
(`199 passed in 18.90s`, `1148 passed in 81.79s`, Logdigest
`df8e8271…0bc7e9a7`, `git diff --check` Exitcode `0`) habe ich
vereinbarungsgemäß nicht reproduziert. Alle unten benannten Gegenproben sind
providerfrei, lesen nur den Arbeitsbaum und ändern ihn nicht.

### Gesamteindruck

Die Dokumentations-, Rollen- und Archivarbeit dieses Slice ist sauber und
vollständig. Die endgültige Topologie ist in allen aktiven Verträgen korrekt
hergestellt, das Archiv ist bitgenau und nichtautoritativ, und `C-09` ist
durch echte Verhaltensregressionen geschlossen. Der Slice scheitert an genau
einer Stelle: Der neue Retirementguard, also das Bauteil, das den erreichten
Zustand künftig festhalten soll, hält seine eigene Zusage nicht. Er ist gegen
wiederkehrende `A-*`-Findingregeln praktisch wirkungslos und inventarisiert
mehrere aktive Pfade nicht. Der Rest des Slice ist freigabereif; deshalb ist
der Befund eng und mechanisch behebbar.

### 1. Planerfüllung und endgültige Topologie

`ANTIGRAVITY.md` ist gelöscht, und die Synchronisationsregel in `AGENTS.md:10`
nennt nur noch `AGENTS.md`, `CLAUDE.md` und `CODEX.md`. Die Abschnitte
„Structured artifact authority" sind in allen drei Rollenverträgen
zeichengleich auf `structured-v2`, „There is no text-parser fallback",
`UNSUPPORTED-PROTOCOL` und „never silently migrated" gehoben; der
Vertragstest `test_root_roles_share_structured_artifact_authority_contract`
prüft genau diese Fragmente und wurde entsprechend nachgezogen.

Die Prozessrollen sind geschlossen: `AgentRole` (`src/contracts.py:25-27`)
führt ausschließlich `codex` und `claude`; `workflow.puml` nennt in allen
Aktivitäten nur Codex und Claude; `example-plan-task.md:51` verlangt nur noch
Claudes Planfingerprint. Eine Suche über den gesamten Arbeitsbaum
(ohne `.git`, `.venv*`, `__pycache__`, `.orchestrator`, `tmp` und den
Archivbereich) findet aktive Antigravity-Vorkommen nur noch in
`docs/reference/market-comparison.md` sowie in den Entwicklungsdokumenten
`docs/internal/README.md` und den vier Dateien dieses Arbeitspakets — also
genau dort, wo sie laut Arbeitsplan bis zur branchweiten Freigabe stehen
bleiben dürfen.

Der Normalbetrieb ist unverändert und korrekt beschrieben: Nur der
Orchestrator validiert, Attestierungen werden fingerprintgebunden
zwischengespeichert und Claude erhält die gebundene Evidenz
(`README.md:168`, `:172`); ein freigebender Slice-Review verlangt eine
vollständige erfolgreiche Attestierung für denselben Fingerprint
(`README.md:203`), und der lokale Commit `Slice NN: …` entsteht danach
automatisch (`README.md:20`, `:230`). Die CLI führt 67 lange Optionen; darunter
ist kein Antigravity-Flag, kein Reviewerwahlparameter und kein
Fallbackschalter.

### 2. Archivgrenze

Unter `docs/internal/archive/antigravity-retirement/` liegen exakt die fünf
abgelösten v1-Schemata und die fünf benannten Übergangsdokumente plus die
`README.md` — elf Dateien, keine mehr, keine weniger. Ich habe die inhaltliche
Erhaltung geprüft, indem ich für alle zehn Dateien
`git show 5193eec:<alter Pfad>` zeilenendennormalisiert gegen die Archivkopie
gehasht habe: alle zehn SHA-256-Werte sind identisch, die rohen Werte
unterscheiden sich ausschließlich durch die repositoryweite CRLF-Konfiguration
des Arbeitsbaums. Die alten aktiven Pfade existieren nicht mehr.

Die Archiv-`README.md` kennzeichnet den Inhalt ausdrücklich als
„nichtautoritative historische Evidenz", verbietet das Lesen durch Produktcode,
Schemaresolver, Resume, Tests und Entscheidungslogik und grenzt die beiden
weiterhin aktiven Provider-Subset-Register korrekt ab.

Keine Reaktivierung als Autorität: Die Schemaauflösung ist durchgängig v2 und
dateinamensfest (`src/artifact_models.py:31`, `src/native_codex_contract.py:34`,
`src/native_review_contract.py:46`, `src/native_codex_request.py:32`,
`src/native_review_request.py:35`); ein v1-Resolver existiert nicht. Weder
`src/**/*.py` noch `tests/**/*.py` enthält einen Dateisystemzugriff auf
`docs/internal/archive`; die Treffer auf „archive" in
`src/artifact_migration.py` und `src/orchestrator.py` betreffen ausschließlich
In-Memory-Ledgerschlüssel. Der Wegfall von
`test_historical_schema_only_claude_results_are_not_v2_and_remain_byte_stable`
ist damit konsistent: Der Test las
`docs/internal/archive/native-codex-claude-correction-loop`, also den zweiten
nichtautoritativen Archivbereich, dessen Lesen das Akzeptanzkriterium
ausdrücklich untersagt.

### 3. Retirementguard – hier scheitert der Slice

Zwei Teilaussagen des Guards halten der Gegenprobe stand: Die Markierung
`# retirement-negative-control` ist tatsächlich zeilengenau
(`tests/test_language_consistency.py:533-537` filtert einzelne Zeilen, nicht
Dateien oder Muster), sie existiert genau zweimal
(`tests/test_contracts.py:464`, `tests/test_native_review_contract.py:243`),
und es gibt keinen allgemeinen `*-v1`-Skip — der Guard arbeitet als
Verbotsliste konkreter Identitäten, weshalb
`native-provider-schema-capabilities-v1` und
`native-provider-schema-exceptions-v1` ohne jede Ausnahmeregel passieren.
Auch die zehn aufgezählten Zeichenketten-Identitäten greifen: Ich habe
`antigravity`, `ANTIGRAVITY_`, `agy`, `agy.exe`, `orchestrator-artifact-v1`,
`native-codex-v1` und `native-claude-review-v1` einzeln gegen
`_retirement_hits()` geführt; jede löst aus.

Zwei andere Teilaussagen halten nicht.

**Erstens ist die `A-*`-Findingregel praktisch wirkungslos.** Der Ausdruck in
`tests/test_language_consistency.py:553` lautet

```python
re.search(r"(?:A-01|A-\\\*|\\[CA\\].*finding|\\^\\[CA\\])", text)
```

In diesem Rawstring matcht jedes `\\` ein *literales Backslash-Zeichen*. Drei
der vier Alternativen können deshalb nur auf backslash-escapten Text
anschlagen; die beabsichtigte Erkennung einer `[CA]`-Zeichenklasse in
unescaptem Quelltext ist unerreichbar. Ich habe das rein im Speicher
falsifiziert, ohne den Arbeitsbaum zu verändern: Ich lade die Inventur aus dem
Testmodul, ersetze den Text genau einer Datei und lasse `_retirement_hits()`
über das Ergebnis laufen.

- Setzt man in `schemas/native-agent-codex-result-v2.schema.json` das
  Dispositionsmuster von `"^C-(0[1-9]|[1-9][0-9]*)$"` zurück auf
  `"^[CA]-(0[1-9]|[1-9][0-9]*)$"` — also exakt der Defekt, den ich im
  Planreview als `C-02` eingefordert hatte —, liefert der Guard **null
  Treffer**.
- Fügt man in `src/workflow.py` `LEGACY_FINDING = "A-02"` ein: **null Treffer**.
- Fügt man in `src/workflow.py` den Kommentar
  `# reviewer may raise A-* findings` ein: **null Treffer**.
- Nur die Literale `A-01` und das markdown-escapte `A-\*` lösen aus.

Damit ist das Akzeptanzkriterium „Der Retirementguard scheitert bei jedem
neuen produktiven Antigravity-, `A-*`- oder Workflow-/Agent-v1-Verweis" für
die `A-*`-Dimension nachweislich nicht erfüllt. Ausgerechnet die
Wiederherstellung der fremden Findingnamespace-Autorität — der schwerwiegendste
denkbare Rückfall, weil er einen zweiten Findingeigentümer wieder
kontraktfähig machte — passiert den Guard lautlos.

**Zweitens entgehen aktive Pfade der Inventur.** `_retirement_active_files()`
(`:504-529`) sammelt aus `src/`, `tests/` und `schemas/` nur Dateien mit
Suffix `.py` oder `.json`, überspringt jeden Pfad, der ein Segment `fixtures`
enthält, und ergänzt neun namentlich hartkodierte Rootdateien. Nicht erfasst
sind unter anderem:

- `run_task` — der produktive Einstiegspunkt, der bis Slice 02 die Variable
  `RUN_TASK_ANTIGRAVITY_BINARY` trug und in `orchestrator.toml` ausdrücklich
  als produktiver Pfad deklariert ist (`[paths] productive`);
- `docs/**` insgesamt, einschließlich der aktiven Referenzdokumente
  `docs/reference/architecture-and-domain-concept.md` und
  `docs/reference/market-comparison.md`, die `orchestrator.toml` als
  `documentation` führt;
- `pyproject.toml` und `task.md`;
- jede künftige Rootdatei, weil die Liste hartkodiert und nicht abgeleitet ist.

Für `docs/reference/architecture-and-domain-concept.md` ist die Lücke real und
nicht nur theoretisch: Der einzige andere Test, der dieses Dokument auf
Rollenbegriffe prüft, ist
`test_active_user_docs_use_only_the_state_v3_role_model` (`:199-214`), und
dessen `forbidden`-Liste enthält keinen Antigravity-Token. Eine dort
wiedereingesetzte Antigravity-Prozessrollenaussage passiert die gesamte Matrix.
Zugleich ist die pauschale `fixtures`-Ausnahme eine allgemeine Pfadfreigabe,
die der Arbeitsplan so nicht vorsieht — sie ist heute unschädlich, weil
`tests/fixtures/` nachweislich keinen einzigen der zehn Tokens enthält, aber
sie ist eben nicht auf einen ausdrücklich nichtautoritativen Archivbereich
beschränkt.

**Warum die Negativkontrolle das nicht auffängt:**
`test_retirement_guard_negative_control` (`:568-570`) ruft `_retirement_hits()`
direkt mit einer synthetischen Zeichenkette und einem Pfad auf, der gar nicht
existiert. Sie belegt den Matcher für den Fall `antigravity`, betritt aber die
Inventur nie und deckt keine der beiden Lücken ab. Die Kontrolle ist damit
echt, aber zu schmal für die Zusage, die der Slicebericht ihr zuschreibt.

`docs/reference/market-comparison.md` selbst ist in Ordnung: Ich habe alle neun
verbliebenen Antigravity-Vorkommen einzeln geprüft. Zeile 48 und 102 grenzen
das Produkt ausdrücklich ab („Externes Vergleichsprodukt … ist aber kein
Bestandteil dieses Orchestrators", „gehört weder zur Laufzeit noch zur Review-
oder Freigabetopologie"); die übrigen sind Zeilen einer Produktvergleichsmatrix,
Quellenlinks oder Empfehlungen für *andere* Werkzeuge. Eine Projektrolle wird
nirgends mehr behauptet, und die frühere Aussage „Feste Rollen Codex → Claude →
Antigravity" ist korrekt auf „Feste Rollen Codex → Claude" reduziert.

### 4. Finding C-09 – geschlossen

Ich habe die fünf Verhaltensverträge einzeln und nicht per Quelltextsuche
geprüft. Alle fünf laufen über echte Ausführungspfade
(`WorkflowEngine.run_current_work_unit` beziehungsweise
`normalize_review_contract_output` + `validate_review_response`), nicht über
Stringvergleiche.

1. **Attestierung genau einmal je Fingerprint, von Claude wiederverwendet.**
   `test_claude_reuses_single_fingerprint_attestation_without_matrix_rerun`
   (`tests/test_workflow.py:554`) fährt eine Matrix mit Default- plus
   Pfadregel und prüft `len(driver.validation_requests) == 1`, die exakte
   Befehlsexpansion `("npm test", "npm run build:engine")`, genau einen
   Reviewerlauf auf demselben Fingerprint, das Vorkommen von
   `npm run build:engine | PASS | exit=0` **im Reviewerprompt** und die
   Bindung derselben Attestierung an den Commit. Der Zähler ist beweisbar
   sensitiv: Dieselbe Testdatei belegt an anderer Stelle `validation_calls ==
   []` (`:3514`) und einen einelementigen Lauf mit fremdem Fingerprint
   (`:3844`), `FakeDriver.validate()` (`:363-364`) zählt bedingungslos.
2. **Unvollständige Attestierung erreicht weder Claude noch Commit.**
   `test_incomplete_attestation_never_reaches_claude_or_commit` (`:588`)
   erzwingt `WorkflowExecutionError` und prüft `reviewer_calls == []` **und**
   `commit_calls == []` — die Commit-Assertion fehlte dem entfernten Original
   sogar. Gegenprobe: Setzt man in derselben Konstellation
   `invalid_attestation=None`, ergibt sich `completed=True`,
   `reviewer_calls=1`, `commit_calls=1`. Die leeren Listen sind also durch die
   Unvollständigkeit verursacht und nicht vakuum.
3. **Slicecommit mit veraltetem Claude-Reviewfingerprint.**
   `test_slice_commit_with_stale_claude_fingerprint_revalidates_before_commit`
   (`:608`) startet im persistierten Schritt `SLICE_COMMIT` ohne passende
   Bindung und weist nach, dass vor dem Commit auf dem aktuellen Fingerprint
   revalidiert **und** neu reviewt wird (`validation_calls ==
   [changes.fingerprint]`, ein Claude-Review, `codex_calls == []`, genau ein
   Commit, dessen `fingerprint` dem aktuellen entspricht). Gegenüber dem
   entfernten Original ist die Fingerprintbindung des Commits neu.
4. **Resume startet Codex nicht erneut.**
   `test_resume_from_persisted_claude_step_does_not_repeat_codex` (`:631`)
   unterbricht real im Reviewer, verifiziert den persistierten Schritt
   `CLAUDE_SLICE_REVIEW` und setzt mit `codex_outputs=[]` fort — ein
   Codexaufruf würde bereits am leeren Pool scheitern, zusätzlich gilt
   `resumed.codex_calls == []`. Gegenprobe: Derselbe Treiber aus dem
   Startschritt erzeugt `codex_calls = 1`, die Assertion unterscheidet also
   tatsächlich.
5. **Reviewnormalisierung ergänzt nur den Testmarker.**
   `test_claude_normalization_adds_bound_test_marker_without_mutating_finding`
   (`tests/test_review_runtime_hardening.py:227`) prüft, dass
   `TEST_FILES_TOUCHED: tests/test_workflow.py` als zweite Zeile ergänzt wird,
   und validiert das Ergebnis anschließend zurück: Finding-ID, Klasse,
   Zusammenfassung, Status und Statusbegründung bleiben unverändert.

Alle fünf Tests laufen bei mir grün (`24 passed in 4.67s` zusammen mit
`tests/test_language_consistency.py`). Damit ist die in Slice 02 benannte
Abdeckungslücke geschlossen, und zwar am Verhalten statt an der
Rollenkonstellation. `C-09` ist geschlossen.

Als Restnotiz ohne Findingcharakter: Die positive Hälfte des entfernten
`test_named_red_state_can_reach_commit_but_incomplete_never_can` — ein
vollständiger *roter* Attestierungsstand darf mit benanntem Folgeslice
committen — ist in `tests/test_workflow.py` nicht nachgebildet worden. Der rote
Pfad selbst bleibt abgedeckt
(`test_complete_failing_attestation_reaches_reviewer_without_red_state:2962`,
`test_plan_bound_complete_failing_attestation_reaches_reviewer_as_red_packet:2981`),
und der Commit mit `red_state_followup_slice` ist auf Git- und Dry-Run-Ebene
belegt (`tests/test_git_service.py:857`, `tests/test_dry_run_scenarios.py:154`).
Das war nicht Bestandteil des Abnahmetests zu `C-09` und ist kein Befund.

### 5. Grenzen und Überpermissivität

Kein Antigravity-Skipflag, kein optionaler Reviewerparameter, kein
deaktivierter Adapterstub, kein Legacyreader und kein aktiver v1-Schemaresolver
bleibt zurück. `AgentRole` ist zweiwertig; `build_agent_registry()` bleibt aus
Slice 02 fail-closed; `STRUCTURED_V1` existiert in `src/workflow_state.py:139`
ausschließlich als Ablehnungsgrund für `UNSUPPORTED-PROTOCOL`.

Die Optionen `--native-codex-results` und `--native-claude-reviews` samt ihren
`--no-`-Formen sind Transportschalter der beiden verbliebenen Rollen; sie
können weder einen dritten Reviewer noch einen Antigravity-Textpfad erzeugen,
weil die Rollenaufzählung das ausschließt. Ich beanstande sie nicht. Der laut
Arbeitsplan spätere Rückbau der Text-Ingressadapter ist in diesem Slice
erkennbar nicht vorweggenommen: Es gibt keinen neuen Parserpfad und keine neue
Textquelle.

Zwei aktive Identitäten mit `-v1`-Suffix jenseits der beiden benannten
Registerausnahmen habe ich gefunden — `review-packet-v1` und
`diff-coverage-manifest-v1` (`src/review_packets.py:368`, `:399`). Sie gehören
weder zum Workflow- noch zum Agentvertrag und benötigen wegen der
Verbotslistenbauart des Guards keine Ausnahme; sie sind kein Befund, aber ein
Grund, den Guard bei einer Korrektur nicht auf ein allgemeines `-v1`-Verbot
umzustellen.

Die historischen Antigravity-Bezüge im Arbeitsplan, in `docs/internal/README.md`
und in den vier Slice-Reviewdokumenten habe ich vereinbarungsgemäß als
notwendige Entwicklungsevidenz bis zur branchweiten Freigabe behandelt.

### Befunde

REVIEWER: claude
FINDING_STATUS: C-09 | CLOSED | Alle fünf allgemeinen Verhaltensverträge sind als Codex-Claude-only-Regressionen über echte Ausführungspfade wiederhergestellt und einzeln nachgeprüft: `test_claude_reuses_single_fingerprint_attestation_without_matrix_rerun` (`tests/test_workflow.py:554`) belegt genau einen Matrixlauf je Fingerprint samt Befehlsexpansion, Wiederverwendung im Reviewerprompt und Commitbindung; `test_incomplete_attestation_never_reaches_claude_or_commit` (`:588`) belegt `reviewer_calls == []` und `commit_calls == []`, und meine Gegenprobe mit `invalid_attestation=None` liefert in derselben Konstellation `reviewer_calls=1`/`commit_calls=1`, die Assertionen sind also nicht vakuum; `test_slice_commit_with_stale_claude_fingerprint_revalidates_before_commit` (`:608`) belegt Revalidierung und erneuten Claude-Review vor einem fingerprintgebundenen Commit ohne Codexlauf; `test_resume_from_persisted_claude_step_does_not_repeat_codex` (`:631`) unterbricht real im Reviewer und setzt mit leerem Codexpool fort, wobei meine Gegenprobe aus dem Startschritt `codex_calls=1` erzeugt; `test_claude_normalization_adds_bound_test_marker_without_mutating_finding` (`tests/test_review_runtime_hardening.py:227`) ergänzt den gebundenen Testmarker und validiert anschließend zurück, dass Finding-ID, Klasse, Zusammenfassung, Status und Statusbegründung unverändert sind. Die Absicherung erfolgt am Verhalten, nicht per Quelltextsuche.
NEW_FINDING: C-10 | BLOCKER | Der Retirementguard erfüllt sein eigenes Akzeptanzkriterium „scheitert bei jedem neuen produktiven Antigravity-, `A-*`- oder Workflow-/Agent-v1-Verweis" nicht, und zwar an zwei unabhängig belegten Stellen. (a) Die `A-*`-Regel in `tests/test_language_consistency.py:553` lautet `re.search(r"(?:A-01|A-\\\*|\\[CA\\].*finding|\\^\\[CA\\])", text)`; in diesem Rawstring matcht jedes `\\` ein literales Backslash-Zeichen, sodass drei der vier Alternativen nur auf backslash-escapten Text anschlagen können und die beabsichtigte Erkennung einer `[CA]`-Zeichenklasse in unescaptem Quelltext unerreichbar ist. Ich habe das rein im Speicher über `_retirement_active_files()` mit textersetzter Einzeldatei falsifiziert: Das Zurücksetzen des Dispositionsmusters in `schemas/native-agent-codex-result-v2.schema.json` von `"^C-(0[1-9]|[1-9][0-9]*)$"` auf `"^[CA]-(0[1-9]|[1-9][0-9]*)$"` — exakt der Rückfall, den `C-02` verhindern sollte — liefert null Guard-Treffer, ebenso `LEGACY_FINDING = "A-02"` und der Kommentar `# reviewer may raise A-* findings` in `src/workflow.py`; nur die Literale `A-01` und `A-\*` lösen aus. (b) `_retirement_active_files()` (`:504-529`) inventarisiert aus `src/`, `tests/` und `schemas/` nur `.py`/`.json`, überspringt jeden Pfad mit Segment `fixtures` und ergänzt neun hartkodierte Rootdateien; nicht erfasst sind `run_task` (in `orchestrator.toml` ausdrücklich als produktiver Pfad deklariert und bis Slice 02 Träger von `RUN_TASK_ANTIGRAVITY_BINARY`), `docs/**` einschließlich der aktiven Referenzdokumente, `pyproject.toml`, `task.md` und jede künftige Rootdatei. Die Lücke ist nicht theoretisch: Der einzige weitere Test, der `docs/reference/architecture-and-domain-concept.md` auf Rollenbegriffe prüft, ist `test_active_user_docs_use_only_the_state_v3_role_model` (`:199-214`), dessen `forbidden`-Liste keinen Antigravity-Token enthält, sodass eine dort wiedereingesetzte Antigravity-Prozessrollenaussage die gesamte Matrix passiert. `test_retirement_guard_negative_control` (`:568-570`) deckt beides nicht ab, weil es `_retirement_hits()` direkt mit einer synthetischen Zeichenkette und einem nicht existierenden Pfad aufruft und die Inventur nie betritt. | Die `A-*`-Regel wird so korrigiert, dass sie unescapten Quelltext trifft, und die Inventur wird von der hartkodierten Rootliste auf die in `orchestrator.toml` deklarierten produktiven, Test-, Schema-, Konfigurations- und Dokumentationspfade umgestellt, wobei Ausnahmen ausschließlich die beiden ausdrücklich nichtautoritativen Archivbereiche unter `docs/internal/archive/` bleiben. Die Negativkontrolle wird zu einer Inventurkontrolle erweitert, die je Mutation belegt, dass der Guard anschlägt: `"^[CA]-(0[1-9]|[1-9][0-9]*)$"` in einem aktiven v2-Schema, `A-02` in einer aktiven Quelldatei, ein `A-*`-Verweis in aktivem Fließtext, ein Antigravity-Verweis in `run_task` und ein Antigravity-Prozessrollensatz in `docs/reference/architecture-and-domain-concept.md`. Zusätzlich wird die pauschale `fixtures`-Freigabe entweder entfernt oder als benannte, begründete Ausnahme geführt. Abnahme mit VALIDATE: ["python3","-m","pytest","tests/test_language_consistency.py","-q"]

PRE_MORTEM: In drei Monaten ist die wahrscheinlichste Fehlerursache, dass genau der Guard, der die Rückkehr verhindern sollte, als erledigt gilt und niemand ihn mehr liest. Er ist grün, sein Name verspricht Vollständigkeit, und der Slicebericht schreibt ihm eine „echte synthetische Negativkontrolle" zu — drei Signale, die zusammen jede weitere Prüfung entmutigen. Wer später den Findingvertrag anfasst, etwa um eine zweite Findingherkunft für ein neues Werkzeug einzuführen, wird das Dispositionsmuster in den v2-Schemata wieder auf eine Zeichenklasse öffnen; der Guard schweigt, die Vollmatrix bleibt grün, und die fremde Findingautorität ist zurück, bevor jemand über die Architekturentscheidung gesprochen hat, die dieses Arbeitspaket ausdrücklich als eigenes Vorhaben deklariert hat. Die zweite, leisere Variante betrifft die hartkodierte Rootliste: Das nächste Root- oder Referenzdokument — eine `OPERATIONS.md`, eine erweiterte Architekturreferenz — entsteht außerhalb der Inventur, und die Rollenbeschreibung darin ist ab dem ersten Tag ungeschützt. Beide Varianten fallen erst auf, wenn ein echter Lauf eine Findingherkunft akzeptiert, die es nicht mehr geben soll.

SLICE_APPROVAL: 03 | NO

STATUS: DONE

---

## Codex-Korrekturrunde zu C-10

### Einordnung

Der von Claude nachgewiesene Blocker war reproduzierbar. Der bisherige
Findingnamespace-Ausdruck erkannte weder ein unescaptes `A-02` noch einen
unescapten `A-*`-Hinweis oder das frühere Writerschemamuster mit der
Zeichenklasse für zwei Findingherkünfte. Zudem war die Inventur auf
hartkodierte Dateitypen und Rootdateien begrenzt und übersprang Fixtures
pauschal.

FINDING_RESPONSE: C-10 | ACCEPTED | Der Retirementguard wird aus den aktiven Pfadklassen in `orchestrator.toml` aufgebaut, erfasst Produktcode, Skripte, Schemas, Tests einschließlich Fixtures, Konfiguration, Dokumentation, `run_task` und künftig neu angelegte Root-Markdown-Dateien. Die Findingnamespace-Regel erkennt unescapte numerische `A-*`-IDs, den wörtlichen `A-*`-Namespace, einen isolierten `"A-"`-Präfix und das frühere `^[CA]-...`-Writerschemamuster. Parametrisierte Inventurmutationen belegen alle von Claude verlangten Rückfälle. Die externe Marktvergleichsausnahme ist zeilen- und kontextgebunden; eine dort ergänzte Prozessrollenaussage löst weiterhin den Guard aus. Die pauschale Fixture-Ausnahme wurde entfernt.

### Änderungen

- `orchestrator.toml` deklariert nun auch aktive Skript- und Schemapfade sowie
  Root-UML-Dateien. Damit ist die Guardinventur aus der kanonischen
  Repositoryklassifikation ableitbar statt parallel hartkodiert.
- `_retirement_active_files()` liest die Pfadklassen aus `orchestrator.toml`,
  expandiert deren vollständige aktive Fläche und entfernt ausschließlich
  deklarierte generierte Pfade, den nichtautoritativen Bereich
  `docs/internal/archive/` sowie die namentlich gebundene laufende
  Arbeitspaket-Evidenz.
- `tests/fixtures/**` wird nicht mehr pauschal übersprungen.
- Die Negativkontrolle mutiert über eine temporäre Repositoryinventur:
  - das v2-Schemamuster auf `^[CA]-...`,
  - eine aktive Quelle mit `A-02`,
  - aktiven Fließtext mit `A-*`,
  - `run_task` mit Antigravity-Binary,
  - die Referenzarchitektur mit einer Antigravity-Prozessrolle,
  - ein Testfixture mit `A-02`,
  - eine künftig neu angelegte `OPERATIONS.md` mit einer Antigravity-Rolle.
- Der Marktvergleich behält zulässige externe Produktvergleiche. Ein eigener
  Gegenbeweis hängt eine aktive Prozessrollenaussage an und verlangt einen
  Guardtreffer.
- Durch den schärferen Guard sichtbar gewordene unerreichbare
  Zweit-Reviewer-Präfixzweige wurden in `src/native_review_contract.py` und
  `src/artifact_migration.py` auf die einzige aktive Claude-Herkunft `C-*`
  geschlossen. Der Replaykommentar verwendet keine abgelöste Namespaceform
  mehr; der Migrationstest prüft einen fremden Präfix nun mit `F-07`.

### Validierung

Claudes exakter Akzeptanzbefehl:

```text
python3 -m pytest tests/test_language_consistency.py -q
```

- Ergebnis: `26 passed in 5.40s`
- Exitcode: `0`

Fokussierte Vertrags-, Konfigurations-, Migrations- und Replaymatrix:

```text
PYTHONDONTWRITEBYTECODE=1 python3 -m pytest \
  tests/test_language_consistency.py \
  tests/test_cli.py \
  tests/test_native_review_contract.py \
  tests/test_artifact_migration.py \
  tests/test_structured_artifact_regressions.py \
  -q -p no:cacheprovider
```

- Ergebnis: `182 passed in 28.46s`
- Exitcode: `0`

Vollständige Repositorymatrix:

```text
PYTHONDONTWRITEBYTECODE=1 python3 -m pytest tests/ -v -p no:cacheprovider
```

- Ergebnis: `1155 passed in 79.03s`
- Exitcode: `0`

### Erneuter Reviewfokus

Claude soll insbesondere falsifizieren, ob die aus `orchestrator.toml`
abgeleitete Inventur einen deklarierten aktiven Pfad verfehlt, ob die
Findingnamespace-Erkennung unerwünschte `[A-Za-z]`-Muster fehlklassifiziert,
ob die Marktvergleichsausnahme eine aktive Rollenbehauptung verstecken kann
und ob die Bereinigung der unerreichbaren Präfixzweige Replay oder Migration
inhaltlich verändert.

TEST_FILES_TOUCHED: tests/test_artifact_migration.py, tests/test_language_consistency.py

IMPLEMENTATION_READY: 03 | YES

STATUS: DONE

---

## Claude Korrekturreview zu C-10

Manueller Korrekturreview außerhalb von `run_task`, ohne Provideraufruf und
ohne strukturierte JSON-Ausgabe. Basis: `5193eec`, der vollständige aktuelle
`git diff 5193eec` samt untracked Archivbereich, `AGENTS.md`, der Arbeitsplan,
mein unveränderter Slice-03-Reviewabschnitt mit `C-10` und die danach
angehängte Codex-Korrekturrunde. Die vorgelegte Evidenz (`26 passed in 5.40s`,
`182 passed in 28.46s`, `1155 passed in 79.03s`, `git diff --check` Exitcode
`0`) habe ich vereinbarungsgemäß nicht reproduziert; den Akzeptanzbefehl zu
`C-10` habe ich einmal nachvollzogen (`26 passed in 5.51s`). Alle Gegenproben
sind providerfrei und verändern den Arbeitsbaum nicht.

### Gesamteinordnung

Beide Teildefekte aus `C-10` sind behoben, und zwar substanziell und nicht
kosmetisch: Die Inventur ist tatsächlich aus `orchestrator.toml` abgeleitet und
deckt jetzt jede aktive Fläche ab, und der Findingnamespace-Matcher erkennt
sämtliche von mir verlangten Rückfälle. `C-10` ist geschlossen.

Die Korrekturrunde hat dabei jedoch eine neue Ausnahme eingeführt, die genau
die Zeilen freigibt, deren Korrektur ein Akzeptanzkriterium dieses Slice ist.
Das ist ein neuer ausführbarer Defekt und wird als `C-11` eröffnet.

### 1. Konfigurationsgebundene Inventur – behoben

`_retirement_active_files()` lädt `[paths]` aus `orchestrator.toml`, vereinigt
die Klassen `productive`, `tests` und `documentation` und expandiert jedes
Muster über `root.glob()`; die Sonderbehandlung `pattern + "/*"` für Muster mit
Endung `/**` ist nötig und korrekt, weil pathlib bei terminalem `**` nur
Verzeichnisse liefert. Ich habe die Inventur gegen den realen Arbeitsbaum
abgeglichen: 105 Dateien; über `git ls-files` plus untracked Dateien bleiben
genau sieben aktive Pfade außerhalb, und alle sieben sind erklärt —
`.gitattributes` und `.gitignore` (in keiner Pfadklasse deklariert), die vier
namentlich gebundenen Evidenzdateien dieses Arbeitspakets und die Guarddatei
selbst über `THIS_FILE`.

Erfasst sind jetzt nachweislich `run_task`, `workflow.puml`, `pyproject.toml`,
`task.md`, `docs/**` einschließlich beider Referenzdokumente, `schemas/**`,
`tests/fixtures/**` und `src/**`. Ich habe die Ableitung zusätzlich gegen ein
temporäres Repository mit synthetischen Pfaden geprüft: Ein tiefer Unterordner
(`src/deep/a/b/c.py`, `docs/a/b/deep.md`), eine endungsfremde Fixturedatei
(`tests/a/b/deep_fixture.bin`), der extensionlose deklarierte Pfad `run_task`,
eine künftige `scripts/probe.py` und eine neu angelegte Root-`OPERATIONS.md`
werden alle inventarisiert — letztere ohne jede neue hartkodierte Rootliste,
allein über das deklarierte `*.md`. Die Bindung ist außerdem belastbar: Entfernt
man `*.md` testweise aus `documentation`, fällt `OPERATIONS.md` sofort aus der
Inventur. Die Zusage ist damit nicht dekorativ, sondern trägt.

Ausgenommen werden ausschließlich die deklarierten `generated`-Muster, der
gesamte historische Bereich `docs/internal/archive/` über eine echte
Elternprüfung und die namentlich gebundene laufende Arbeitspaket-Evidenz
(`docs/internal/` plus Präfix `antigravity-endgueltige-entfernung-`). Die
pauschale `fixtures`-Freigabe ist ersatzlos entfallen; `tests/fixtures/**` steht
jetzt unter dem Guard.

Zu den Ergänzungen in `orchestrator.toml`: `scripts/**/*.py` und
`schemas/**/*.json` ändern die produktive Klassifikation faktisch nicht, weil
`classify_path()` (`src/gates.py:245-257`) unbekannte Pfade ohnehin als
`PRODUCTIVE` behandelt — beide Muster machen nur explizit, was vorher als
Fallback galt. `*.puml` verschiebt `workflow.puml` von diesem Fallback in die
Klasse `DOCUMENTATION`. Das ist die einzige echte Verhaltensänderung: Der
einzige Konsument der Klassen ist `evaluate_productive_file_limit()`
(`src/workflow.py:2349`, `:3516`, `:3582`), sodass eine `workflow.puml`-Änderung
nicht mehr gegen die Höchstzahl produktiver Änderungseinheiten zählt. Das ist
semantisch korrekt — eine UML-Datei ist Dokumentation und steht dort neben
`*.md` — und keine unerwünschte Nebenwirkung.

### 2. Findingnamespace-Matcher – behoben

Alle von mir verlangten Erkennungen greifen; ich habe sie einzeln gegen
`_retirement_hits()` geführt:

| Eingabe | Ergebnis |
|---|---|
| `LEGACY = "A-02"` | Treffer |
| `# reviewer may raise A-* findings` | Treffer |
| `prefix = "A-"` | Treffer |
| `"pattern": "^[CA]-(0[1-9]\|[1-9][0-9]*)$"` | Treffer |
| `re.match(r"[CA]-(0[1-9])", x)` (ohne Caret) | Treffer |
| `finding A-01` | Treffer |
| `identifiers use the [CA] namespace for findings` | Treffer |

Die Fehlalarmgrenze hält für die relevanten Fälle: `^[A-Za-z0-9_]+$`,
`^[0-9a-f]{64}$` als SHA-256-Muster, `"A-Z"` als Bereichsangabe und ein
ISO-Datum lösen nicht aus. Ausschlaggebend ist, dass die vierte Alternative
das Literal `[CA]` verlangt, das in allgemeinen Zeichenklassen nicht vorkommt.

Die zeilengenauen historischen Negativkontrollen sind unverändert eng: genau
zwei Vorkommen von `# retirement-negative-control`
(`tests/test_contracts.py:464`, `tests/test_native_review_contract.py:243`),
gefiltert wird strikt zeilenweise über `splitlines()`. Weder eine Datei noch
ein Pfad wird dadurch freigegeben.

### 3. Echte Inventur-Negativkontrollen – behoben

`test_retirement_guard_inventory_negative_controls` ruft `_retirement_hits()`
nicht mehr isoliert auf. Der Test legt ein temporäres Repository mit der
echten `orchestrator.toml` an, schreibt die Mutation an ihren realen relativen
Pfad, ruft `_retirement_active_files(tmp_path)` auf und verlangt zuerst
`target in active_files` und erst danach einen Treffer. Alle sieben von mir
geforderten Mutationen sind parametrisiert vorhanden — `^[CA]-…` im aktiven
v2-Schema, `A-02` in einer aktiven Quelldatei, `A-*` in aktivem Fließtext, ein
Antigravity-Binary in `run_task`, eine Antigravity-Prozessrolle in der
Referenzarchitektur, `A-02` unter `tests/fixtures/` und eine
Antigravity-Prozessrolle in einer neuen Root-`OPERATIONS.md`. Ich habe für alle
sieben Zielpfade unabhängig bestätigt, dass sie die Inventur tatsächlich
passieren. Damit ist genau die Lücke geschlossen, an der die alte
Negativkontrolle vorbeilief.

### 4. Marktvergleich – hier bleibt der Slice offen

`_allowed_external_market_line()` ist dateigebunden (nur
`docs/reference/market-comparison.md`) und zeilengebunden — aber innerhalb der
Zeile ist die Ausnahme **strukturgebunden statt inhaltsgebunden**. Die erste
Bedingung lautet `stripped.startswith("|")` und gibt damit **jede** Tabellenzeile
dieser Datei frei, unabhängig davon, was in ihr steht. Vier weitere Bedingungen
geben ganze Absätze über ihren Zeilenanfang frei
(`### 6.3 Google Antigravity`, `Google positioniert Antigravity`,
`Antigravity bietet damit`, `Offizielle Quellen:`).

Das ist nicht theoretisch. Genau die drei Zeilen, deren Korrektur Slice 03 im
Marktvergleich vorgenommen hat, fallen sämtlich unter diese Struktur. Ich habe
die aktuelle Datei genommen und jeweils **nur eine** Zeile auf ihren
Vor-Slice-03-Stand zurückgesetzt:

- Zeile 48 zurück auf „| Google Antigravity 2.0 | … | Stellt den hier
  verwendeten unabhängigen Reviewer bereit … |" → **kein Guardtreffer**.
- Zeile 58 zurück auf „| Dual-Agent Orchestrator | … | Feste Rollen
  Codex → Claude → Antigravity; … |" → **kein Guardtreffer**.
- Zeile 102 zurück auf „Antigravity bietet damit … In diesem Projekt wird es
  bewusst auf einen unabhängigen, schreibgeschützten Abschlussreviewer nach
  Claude begrenzt." → **kein Guardtreffer**.

Ebenso passiert eine frei erfundene neue Tabellenzeile
„| Dual-Agent Orchestrator | Rollen | Codex, Claude und Antigravity sind die
drei aktiven Prozessrollen dieses Orchestrators |" den Guard lautlos. Der
Guard kann die Rücknahme derjenigen Änderung, die er absichern soll, also nicht
erkennen.

Die mitgelieferte Gegenkontrolle
`test_market_comparison_exception_does_not_hide_a_process_role` beweist das
nicht, weil sie eine **freistehende Prosazeile** anhängt, die unter keine der
sechs Ausnahmebedingungen fällt. Sie prüft damit den einzigen Formulierungstyp,
der ohnehin nie freigegeben war.

Zwei Nebenbeobachtungen zur selben Funktion, die den Befund einordnen: Die
Bedingung `"externes Vergleichsprodukt" in stripped.casefold()` kann
niemals wahr werden, weil der Suchstring ein großes `V` enthält und die
Vergleichsseite `casefold()`-normalisiert ist — die einzige inhaltsbezogene
Bedingung der Ausnahme ist tot und wird durch die Strukturbedingung `|`
maskiert. Und die Zeilenausnahme für `docs/internal/README.md` teilt denselben
Bauplan: Sie filtert Zeilen, die den Archivlink oder den Arbeitsplandateinamen
enthalten, sodass eine auf dieselbe Zeile gesetzte Rollenbehauptung
(„…(archive/antigravity-retirement/) Antigravity ist die dritte aktive
Prozessrolle.") ebenfalls unentdeckt bleibt. Heute steht dort nur ein reiner
Linktext, der Bauplan ist aber derselbe.

### 5. Durch den Guard offengelegte aktive Restzweige – unbedenklich

Alle vier Punkte halten der Prüfung stand:

- `next_native_finding_id()` und `_validate_response_events()` dürfen `C`
  beziehungsweise `C-` fest verdrahten, weil `NativeReviewContext.__post_init__`
  (`src/native_review_contract.py:182-185`) jede Konstruktion mit einem anderen
  Reviewer über `reviewer must be claude` fail-closed abweist. Der entfernte
  Zweig war nachweislich unerreichbar.
- `_recoverable_pending_correction_record()` bindet über
  `reviewer_by_step = {WorkflowStep.CLAUDE_SLICE_REVIEW: "claude"}`
  (`src/artifact_migration.py:628-631`) und kehrt für jeden anderen Schritt
  sofort mit `False` zurück. `reviewer` bleibt für `logical_id` und den
  Payloadvergleich in Gebrauch, ist also keine tote Variable; der feste Präfix
  `C-` kann keine Wiederaufnahme verändern, weil er schon vorher der einzige
  erreichbare Wert war.
- Der Migrationstest prüft mit `F-07` weiterhin genau einen fremden
  Findingpräfix; die Parametrisierung führt unverändert sechs Fehlermodi, und
  `finding-outside-review` bleibt als eigener `C-`-Fall daneben bestehen. Es
  geht keine Negativabdeckung verloren.
- Die Änderung in `src/artifact_replay.py:256-260` betrifft ausschließlich den
  Docstringtext oberhalb von `target = …`; ausführbarer Code ist nicht berührt.

### Restrisiken ohne Findingcharakter

Die Guardinventur ist jetzt exakt so vollständig wie die Klassifikation, aus
der sie abgeleitet wird — das war die von mir verlangte Abnahme, hat aber eine
Kehrseite: Ein künftig neu eingeführter aktiver Dateityp, der in keiner
Pfadklasse deklariert ist (etwa ein `src/config.yaml` oder eine
extensionlose Rootdatei neben `run_task`), bleibt ungeschützt, obwohl
`classify_path()` ihn als produktiv behandeln würde. Zweitens verengt jede
spätere Verkleinerung von `[paths]` den Guard still mit; das ist ein sichtbarer,
reviewpflichtiger Eingriff, aber kein Test hält die Breite der Klassen fest.
Drittens erzeugt das `re.I`-Flag der Namespaceregel Fehlalarme für harmlose
Muster wie `[ca]-(0[1-9])`, ein isoliertes `a-1` in einer Aufzählung oder einen
Windowspfad `C:\Users\A-1\file`; das ist die sichere Richtung — ein roter Guard
statt eines stillen Durchlaufs — und heute ohne Wirkung, weil die aktive Fläche
sauber ist. Viertens greift beim Migrationsmodus `wrong-finding-prefix` bereits
die vorgelagerte Teilmengenprüfung gegen `("C-01", "C-07")`, sodass der
Präfixvergleich nicht isoliert wird; das war vor der Korrektur mit `A-07`
identisch und ist keine Regression dieser Runde.

### Befunde

REVIEWER: claude
FINDING_STATUS: C-10 | CLOSED | Beide Teildefekte sind behoben und einzeln nachgeprüft. (a) Die Findingnamespace-Regel erkennt jetzt alle von mir belegten Rückfälle: `LEGACY = "A-02"`, `# reviewer may raise A-* findings`, `prefix = "A-"`, `"^[CA]-(0[1-9]|[1-9][0-9]*)$"`, `[CA]-(0[1-9])` ohne Caret sowie `[CA]` in Fließtext lösen sämtlich aus, während `^[A-Za-z0-9_]+$`, `^[0-9a-f]{64}$`, `"A-Z"` und ein ISO-Datum nicht auslösen. (b) `_retirement_active_files()` leitet die Inventur aus `[paths]` in `orchestrator.toml` ab; ich habe sie gegen `git ls-files` plus untracked Dateien abgeglichen — 105 Dateien, und genau sieben aktive Pfade bleiben außerhalb, alle erklärt (`.gitattributes`, `.gitignore`, die vier namentlich gebundenen Evidenzdateien, die Guarddatei über `THIS_FILE`). `run_task`, `workflow.puml`, `pyproject.toml`, `task.md`, `docs/**`, `schemas/**` und `tests/fixtures/**` sind erfasst, die pauschale Fixture-Freigabe ist entfallen, und eine neu angelegte Root-`OPERATIONS.md` wird allein über das deklarierte `*.md` inventarisiert; entfernt man `*.md` testweise aus `documentation`, fällt sie sofort heraus, die Bindung ist also belastbar. Die sieben parametrisierten Negativkontrollen laufen zuerst über `_retirement_active_files()` eines temporären Repositorys und verlangen `target in active_files`, bevor sie einen Treffer fordern; ich habe für alle sieben Zielpfade unabhängig bestätigt, dass sie die Inventur passieren. Die Ausnahmen sind auf deklarierte `generated`-Muster, `docs/internal/archive/` und die namentlich gebundene Arbeitspaket-Evidenz begrenzt.
NEW_FINDING: C-11 | BLOCKER | Die in dieser Korrekturrunde neu eingeführte Marktvergleichsausnahme `_allowed_external_market_line()` (`tests/test_language_consistency.py:563-579`) ist zwar datei- und zeilengebunden, innerhalb der Zeile aber strukturgebunden statt inhaltsgebunden: Die erste Bedingung `stripped.startswith("|")` gibt jede Tabellenzeile von `docs/reference/market-comparison.md` frei, unabhängig vom Inhalt, und vier weitere Bedingungen geben ganze Absätze über ihren Zeilenanfang frei (`### 6.3 Google Antigravity`, `Google positioniert Antigravity`, `Antigravity bietet damit`, `Offizielle Quellen:`). Damit deckt die Ausnahme genau die Zeilen ab, deren Korrektur ein Akzeptanzkriterium dieses Slice ist. Ich habe die aktuelle Datei genommen und jeweils nur eine Zeile auf ihren Vor-Slice-03-Stand aus `5193eec` zurückgesetzt: Zeile 48 („| Google Antigravity 2.0 | … | Stellt den hier verwendeten unabhängigen Reviewer bereit … |"), Zeile 58 („| Dual-Agent Orchestrator | … | Feste Rollen Codex → Claude → Antigravity; … |") und Zeile 102 („Antigravity bietet damit … In diesem Projekt wird es bewusst auf einen unabhängigen, schreibgeschützten Abschlussreviewer nach Claude begrenzt.") — in allen drei Fällen meldet der Guard null Treffer. Ebenso passiert eine neue Tabellenzeile „| Dual-Agent Orchestrator | Rollen | Codex, Claude und Antigravity sind die drei aktiven Prozessrollen dieses Orchestrators |" lautlos. Die mitgelieferte Gegenkontrolle `test_market_comparison_exception_does_not_hide_a_process_role` beweist das Gegenteil nicht, weil sie eine freistehende Prosazeile anhängt, die unter keine der sechs Bedingungen fällt. Verschärfend ist die einzige inhaltsbezogene Bedingung tot: `"externes Vergleichsprodukt" in stripped.casefold()` kann wegen des großen `V` im Suchstring nie wahr werden und wird von der Strukturbedingung maskiert. Dieselbe Bauart trägt die Zeilenausnahme für `docs/internal/README.md`, bei der eine auf die Linkzeile gesetzte Rollenbehauptung („…(archive/antigravity-retirement/) Antigravity ist die dritte aktive Prozessrolle.") ebenfalls unentdeckt bleibt. | Beide Zeilenausnahmen werden inhaltsgebunden statt strukturgebunden formuliert: Eine Zeile darf nur dann freigegeben werden, wenn sie Antigravity ausdrücklich als externes Produkt kennzeichnet beziehungsweise reiner Link-, Quellen- oder Überschriftentext ohne Rollenaussage ist; eine Freigabe allein wegen `|` am Zeilenanfang oder wegen eines Absatzpräfixes entfällt, und die tote `casefold()`-Bedingung wird korrigiert. Die Gegenkontrollen werden parametrisiert um genau die Fälle erweitert, die heute durchrutschen, und verlangen je Fall einen Guardtreffer: die aus `5193eec` zurückgesetzten Zeilen 48, 58 und 102 von `docs/reference/market-comparison.md`, eine neue Tabellenzeile mit einer Prozessrollenaussage und eine Rollenbehauptung, die in `docs/internal/README.md` auf dieselbe Zeile wie der Archivlink gesetzt wird. Abnahme mit VALIDATE: ["python3","-m","pytest","tests/test_language_consistency.py","-q"]

PRE_MORTEM: In drei Monaten ist die wahrscheinlichste Fehlerursache, dass der Marktvergleich als die eine Datei gilt, in der Antigravity „erlaubt" ist, und diese Verkürzung von der Ausnahme selbst bestätigt wird. Der Guard ist grün, die Ausnahme trägt den Namen `_allowed_external_market_line`, und eine Gegenkontrolle scheint sie abzusichern — drei Signale, die niemanden mehr hinsehen lassen. Wer später die Vergleichstabellen aktualisiert, etwa weil eine neue Antigravity-Version erscheint, schreibt die Topologiespalte des eigenen Produkts beiläufig wieder in ihre alte Form, weil sie sich in einer Tabelle über Agententopologien natürlich liest; der Guard schweigt, die Vollmatrix bleibt grün, und die Referenzdokumentation behauptet wieder eine dreirollige Prozesstopologie, die es im Code seit Slice 02 nicht mehr gibt. Die zweite, leisere Variante betrifft die Kopplung an `orchestrator.toml`: Eine spätere Aufräumaktion verengt eine Pfadklasse — etwa `docs/**` auf `docs/reference/**` —, und der Guard verliert seine Fläche stillschweigend mit, weil kein Test die Breite der Klassen festhält. Beide Varianten fallen erst auf, wenn ein Außenstehender die Dokumentation gegen den Code liest.

SLICE_APPROVAL: 03 | NO

STATUS: DONE

---

## Codex-Korrekturrunde zu C-11

### Einordnung

Der Befund war reproduzierbar. Die bisherige Ausnahme ließ jede Tabellenzeile
im Marktvergleich sowie mehrere Absatzformen allein aufgrund ihrer Struktur
durch. Damit konnten sowohl historische Drei-Rollen-Aussagen als auch neue
Prozessrollensätze auf einer formal erlaubten Zeile den Guard umgehen. Die
interne README-Linkausnahme hatte dieselbe Schwäche.

FINDING_RESPONSE: C-11 | ACCEPTED | Sämtliche Struktur- und Präfixfreigaben wurden entfernt. Zulässige Antigravity-Erwähnungen werden nun ausschließlich durch exakte, vollständig inhaltsgebundene Zeilen in `docs/reference/market-comparison.md` beziehungsweise zwei exakte reine Linkzeilen in `docs/internal/README.md` freigegeben. Jede Textänderung, Ergänzung oder Rollenbehauptung auf derselben Zeile macht die Ausnahme ungültig und wird anschließend vom Retirementmatcher erkannt. Parametrisierte Gegenkontrollen setzen die drei von Claude benannten Zeilen auf ihren historischen Rollenstand zurück, fügen eine frei erfundene Drei-Rollen-Tabellenzeile ein und hängen eine aktive Rollenbehauptung direkt an den Archivlink; alle fünf Mutationen lösen den Guard aus.

### Änderungen

- `_ALLOWED_MARKET_ANTIGRAVITY_LINES` enthält exakt die zehn gegenwärtig
  zulässigen externen Vergleichs-, Überschrifts-, Quellen- und
  Empfehlungszeilen. Der Test verlangt Mengengleichheit zwischen diesen Zeilen
  und allen Antigravity-Vorkommen im realen Marktvergleich.
- `_ALLOWED_INTERNAL_RETIREMENT_LINK_LINES` enthält exakt den Arbeitsplanlink
  und den Archivlink. Freier Zusatztext auf derselben Zeile ist nicht erlaubt.
- `_allowed_retirement_reference_line()` prüft nur noch exakte
  Inhaltsgleichheit; Tabellenanfang, Absatzpräfix und `casefold()`-Teilstrings
  erteilen keine Freigabe mehr.
- Vier parametrisierte Marktmutationen belegen:
  - die alte Aussage, Antigravity stelle den unabhängigen Reviewer bereit,
  - die alte Topologie `Codex → Claude → Antigravity`,
  - die alte Begrenzung auf einen Abschlussreviewer nach Claude,
  - eine frei erfundene Tabellenzeile mit drei aktiven Prozessrollen.
- Eine weitere Gegenkontrolle hängt die aktive Prozessrollenaussage direkt an
  die ansonsten zulässige Archivlinkzeile in `docs/internal/README.md`.

### Validierung

Exakter C-11-Akzeptanzbefehl:

```text
python3 -m pytest tests/test_language_consistency.py -q
```

- Ergebnis: `31 passed in 5.36s`
- Exitcode: `0`

Vollständige Repositorymatrix:

```text
PYTHONDONTWRITEBYTECODE=1 python3 -m pytest tests/ -v -p no:cacheprovider
```

- Ergebnis: `1160 passed in 74.44s`
- Exitcode: `0`

### Erneuter Reviewfokus

Claude soll besonders prüfen, ob jede der zehn genehmigten Marktzeilen exakt
dem aktuellen Dokument entspricht, ob eine beliebige Mutation derselben Zeile
die Ausnahme verliert, ob die Gegenkontrollen wirklich die realen historischen
Zeilen ersetzen und ob ein zusätzlicher Prozessrollensatz auf einer Link-,
Quellen-, Überschrifts- oder Tabellenzeile verborgen werden kann.

TEST_FILES_TOUCHED: tests/test_language_consistency.py

IMPLEMENTATION_READY: 03 | YES

STATUS: DONE

---

## Claude Korrekturreview zu C-11

Manueller Korrekturreview außerhalb von `run_task`, ohne Provideraufruf und
ohne strukturierte JSON-Ausgabe. Basis: `5193eec`, der vollständige aktuelle
`git diff 5193eec` samt untracked Archivbereich, `AGENTS.md`, der Arbeitsplan,
mein Korrekturreview mit `C-11` und die danach angehängte Codex-Korrekturrunde.
Gegenüber der vorigen Runde hat sich ausschließlich
`tests/test_language_consistency.py` verändert; alle übrigen Diffstat-Zeilen
sind unverändert, was die Zusage `TEST_FILES_TOUCHED` bestätigt. Die vorgelegte
Evidenz (`31 passed in 5.36s`, `1160 passed in 74.44s`) habe ich nicht
reproduziert; den Akzeptanzbefehl zu `C-11` habe ich einmal nachvollzogen
(`31 passed in 6.02s`) und `git diff --check` mit Exitcode `0` bestätigt — die
Ausgabe enthält ausschließlich die bekannten CRLF-Konfigurationswarnungen und
keinen Whitespacefehler. Alle Gegenproben sind providerfrei und verändern den
Arbeitsbaum nicht.

### Gesamteinordnung

`C-11` ist behoben, und zwar an der Wurzel statt am Symptom. Die
Ausnahmemechanik ist von Struktur- auf reine Inhaltsgleichheit umgestellt; ich
konnte keine Formulierungsvariante mehr finden, die eine aktive Rollenaussage
an ihr vorbeibringt. `C-10` ist dabei nicht regrediert. Damit steht kein
offenes Claude-Blockerfinding mehr.

### 1. Vollständig inhaltsgebundene Freigaben – bestätigt

`_allowed_retirement_reference_line()` (`tests/test_language_consistency.py`)
besteht nur noch aus zwei pfadgebundenen Mengenprüfungen
(`stripped in _ALLOWED_MARKET_ANTIGRAVITY_LINES` beziehungsweise
`stripped in _ALLOWED_INTERNAL_RETIREMENT_LINK_LINES`) und einem `return False`
für jeden anderen Pfad. `startswith("|")`, die vier Absatz- und
Überschriftspräfixe und die tote `casefold()`-Teilstringprüfung sind
ersatzlos entfallen; es gibt keine verbliebene Freigabe, die auf Zeilenform
statt Zeileninhalt beruht.

Ich habe die Empfindlichkeit gegen die realen Zeilen geprüft. Jede der
folgenden Mutationen einer zugelassenen Marktzeile verliert die Freigabe und
löst den Guard aus:

| Mutation einer erlaubten Zeile | Guard |
|---|---|
| ein zusätzliches Zeichen am Zeilenende | Treffer |
| ein fehlendes Zeichen am Zeilenende | Treffer |
| zusätzliche Tabellenspalte `\| aktiver Reviewer \|` | Treffer |
| doppeltes Leerzeichen im Zeileninneren | Treffer |
| geschütztes Leerzeichen statt normalem Leerzeichen | Treffer |
| Schreibweise `ANTIGRAVITY` statt `Antigravity` | Treffer |

Ebenso lösen alle Formen aus, die unter der alten Strukturregel frei waren:
eine beliebige neue Tabellenzeile mit Rollenaussage, eine erweiterte
Überschrift `### 6.3 Google Antigravity 2.0 ist der dritte Reviewer …`, ein
Absatz mit dem Anfang `Antigravity bietet damit …`, einer mit
`Google positioniert Antigravity …` und eine Zeile mit dem Anfang
`Offizielle Quellen:`.

Zur `.strip()`-Frage: Die Normalisierung entfernt ausschließlich führenden und
abschließenden Leerraum. Eine erlaubte Zeile behält ihre Freigabe daher, wenn
man sie einrückt oder ihr Leerzeichen anhängt — beides transportiert
keinerlei Inhalt, weder eine Rollenbehauptung noch eine zusätzliche
Tabellenspalte, weil jedes einzelne Nicht-Leerraum-Zeichen exakt
übereinstimmen muss. Leerraum im Zeileninneren wird nicht normalisiert und
bricht die Gleichheit sofort, wie die Tabelle oben zeigt. `.strip()` kann
damit nichts verbergen.

### 2. Marktvergleichsinventur – bestätigt

`_ALLOWED_MARKET_ANTIGRAVITY_LINES` enthält zehn Zeilen. Ich habe unabhängig
die Menge aller Zeilen aus `docs/reference/market-comparison.md` gebildet, die
`antigravity` case-insensitiv enthalten: ebenfalls zehn, und beide Mengen sind
identisch — `only-in-doc` und `only-in-allowlist` sind leer.
`test_market_comparison_allows_only_exact_external_product_lines` fordert genau
diese Mengengleichheit, sodass eine neue, eine entfernte und eine veränderte
Zeile gleichermaßen auffallen.

Die zehn Zeilen habe ich einzeln inhaltlich geprüft: die
Zusammenfassungszeile, die Antigravity in einer Produktaufzählung nennt und
die eigene Topologie ausdrücklich auf „Codex und Claude" festlegt; zwei
Vergleichstabellenzeilen zu Antigravity 2.0, davon eine mit dem expliziten
Zusatz „ist aber kein Bestandteil dieses Orchestrators"; die Kopfzeile der
Fähigkeitstabelle, in der Antigravity eine Produktspalte ist; die Überschrift
`### 6.3 Google Antigravity 2.0`; zwei Absätze des Abschnitts 6.3, deren
zweiter Antigravity ausdrücklich „weder zur Laufzeit noch zur Review- oder
Freigabetopologie" zählt; die Quellenzeile; und zwei
Empfehlungstabellenzeilen, die für Bedürfnisse außerhalb dieses Orchestrators
auf andere Werkzeuge verweisen. Keine dieser Zeilen behauptet eine aktive
Orchestratorrolle.

Eine Rollenbehauptung in einer bestehenden Tabellen-, Quellen-, Überschriften-
oder Prosazeile kann die Ausnahme nicht passieren, weil jede solche
Behauptung die Zeile verändert und die Gleichheit damit bricht.

### 3. Historische und erfundene Marktmutationen – bestätigt

`test_market_comparison_rejects_retired_role_lines` arbeitet auf dem realen
Dokument: Es liest `market-comparison.md`, prüft mit
`assert current_line in current`, dass die Zeile heute genau so existiert,
ersetzt sie mit `str.replace(..., 1)` und verlangt einen Treffer im mutierten
Dokumenttext. Das ist keine losgelöste synthetische Zeichenkette.

Ich habe alle vier Fälle unabhängig nachgestellt, indem ich die jeweilige
Zeile positionsgenau aus `git show 5193eec:docs/reference/market-comparison.md`
zurückgeschrieben habe:

- Zeile 48 zurück auf „… Stellt den hier verwendeten unabhängigen Reviewer
  bereit …" → Treffer.
- Zeile 58 zurück auf „… Feste Rollen Codex → Claude → Antigravity; …" →
  Treffer.
- Zeile 102 zurück auf „… In diesem Projekt wird es bewusst auf einen
  unabhängigen, schreibgeschützten Abschlussreviewer nach Claude begrenzt." →
  Treffer.

Alle drei Zeilen, die in meinem `C-11`-Befund noch lautlos durchliefen, lösen
jetzt aus. Zusätzlich habe ich die frei erfundene Drei-Rollen-Tabellenzeile
nicht nur ersetzend wie im Test, sondern **eingefügt** geprüft — auch die
Einfügung löst aus.

### 4. Interne README-Linkausnahme – bestätigt

`_ALLOWED_INTERNAL_RETIREMENT_LINK_LINES` enthält exakt zwei Einträge, den
Arbeitsplanlink und die Archivlinkzeile. Der Abgleich gegen
`docs/internal/README.md` ergibt ebenfalls exakt zwei Antigravity-Zeilen, und
beide Mengen sind identisch. Ich habe vier Versteckversuche geprüft; alle
lösen den Guard aus:

- „Antigravity ist die dritte aktive Prozessrolle." an die Archivlinkzeile
  angehängt,
- dieselbe Aussage an den Arbeitsplanlink angehängt,
- dieselbe Aussage **vor** den Arbeitsplanlink gestellt,
- die Archivlinkzeile in eine zusätzliche Markdown-Tabellenspalte gepackt.

### 5. Keine Regression von C-10 – bestätigt

Die aus `orchestrator.toml` abgeleitete Inventur ist unverändert vollständig:
105 Dateien, und über `git ls-files` plus untracked Dateien bleiben dieselben
sieben erklärten Pfade außerhalb (`.gitattributes`, `.gitignore`, die vier
namentlich gebundenen Evidenzdateien, die Guarddatei über `THIS_FILE`).
`run_task`, `workflow.puml`, `pyproject.toml`, `task.md`, `docs/reference/**`,
`schemas/**` und `tests/fixtures/**` sind weiterhin erfasst; die sieben
parametrisierten Inventurkontrollen einschließlich `OPERATIONS.md` sind
unverändert vorhanden.

Der Findingnamespace-Matcher ist unverändert: `LEGACY = "A-02"`,
`may raise A-* findings`, `prefix = "A-"` und `"^[CA]-(0[1-9])$"` lösen aus,
während `^[A-Za-z0-9_]+$` und `^[0-9a-f]{64}$` keinen Fehlalarm erzeugen. Die
zeilengenauen historischen Negativkontrollen sind weiterhin genau zwei
(`tests/test_contracts.py`, `tests/test_native_review_contract.py`).

### Restrisiken ohne Findingcharakter

Drei Punkte bleiben, keiner davon handlungsrelevant für diesen Slice. Erstens
ist der Guard tokenbasiert: Eine Rollenbehauptung, die das Wort Antigravity
vermeidet („das Produkt aus Abschnitt 6.3 ist der dritte Reviewer"), wird von
keiner Regel erfasst. Das gilt repositoryweit und ist keine Eigenschaft der
Ausnahme. Zweitens ist eine Allowlist eine Fixierung, keine Semantikprüfung:
Wer Dokument und Allowlist gemeinsam ändert, bleibt grün — sichtbar wird das
nur im Review des Diffs, dafür aber zuverlässig, weil beide Änderungen im
selben Commit stehen müssen. Drittens gilt die Mengengleichheitsprüfung nur
für den Marktvergleich, nicht für `docs/internal/README.md`; dort fällt eine
verwaiste Allowlistzeile nicht auf. Eine Umgehung entsteht daraus nicht, weil
jede neue Antigravity-Zeile mangels exakter Übereinstimmung sofort trifft.
Viertens bleibt die Zeilenmarkierung `# retirement-negative-control` eine
inhaltsunabhängige Freigabe für die markierte Zeile; sie ist vom Arbeitsplan
ausdrücklich so vorgesehen, kommt genau zweimal vor und wurde in beiden
vorigen Runden geprüft.

### Befunde

REVIEWER: claude
FINDING_STATUS: C-11 | CLOSED | Sämtliche Strukturfreigaben sind entfernt; `_allowed_retirement_reference_line()` besteht nur noch aus zwei pfadgebundenen Mengenprüfungen auf exakte Zeilengleichheit plus `return False`. Ich habe die drei Zeilen, die in meinem Befund lautlos durchliefen, positionsgenau aus `git show 5193eec:docs/reference/market-comparison.md` zurückgeschrieben — Zeile 48 („Stellt den hier verwendeten unabhängigen Reviewer bereit"), Zeile 58 („Feste Rollen Codex → Claude → Antigravity") und Zeile 102 („auf einen unabhängigen, schreibgeschützten Abschlussreviewer nach Claude begrenzt") — und alle drei lösen jetzt den Guard aus; eine frei erfundene Drei-Rollen-Tabellenzeile löst sowohl ersetzend als auch eingefügt aus. Jede Mutation einer erlaubten Zeile verliert die Freigabe: ein Zeichen mehr, ein Zeichen weniger, eine zusätzliche Tabellenspalte, doppeltes Leerzeichen im Zeileninneren, ein geschütztes Leerzeichen und eine geänderte Groß-/Kleinschreibung ergeben sämtlich einen Treffer. `.strip()` kann nichts verbergen, weil es nur führenden und abschließenden Leerraum entfernt und jedes Nicht-Leerraum-Zeichen exakt übereinstimmen muss. Die Allowlist deckt sich exakt mit den zehn Antigravity-Zeilen des realen Marktvergleichs (`only-in-doc` und `only-in-allowlist` leer), der Test fordert echte Mengengleichheit, und alle zehn Zeilen sind inhaltlich geprüft externe Produktvergleiche, Überschrift, Quellen oder Werkzeugempfehlungen ohne aktive Orchestratorrolle. Die parametrisierten Gegenkontrollen mutieren den realen Dokumenttext statt einer losgelösten Zeichenkette. Für `docs/internal/README.md` enthält die Allowlist exakt den Arbeitsplan- und den Archivlink; eine angehängte, eine vorangestellte und eine in eine zusätzliche Tabellenspalte gepackte Rollenbehauptung lösen alle aus. `C-10` ist dabei nicht regrediert: Inventur unverändert bei 105 Dateien mit denselben sieben erklärten Ausnahmen, Matcher unverändert treffsicher für `A-02`, `A-*`, `"A-"` und `^[CA]-` und weiterhin fehlalarmfrei für `[A-Za-z]` und SHA-256-Muster.

REVIEW_EVIDENCE: Vollständige Ablösung aller Struktur- und Präfixfreigaben durch reine Zeilengleichheit; Empfindlichkeit der Ausnahme gegen sechs Mutationsklassen einer erlaubten Zeile einschließlich zusätzlicher Tabellenspalte, innerer Leerraumänderung, geschütztem Leerzeichen und Groß-/Kleinschreibung; Grenzen der `.strip()`-Normalisierung; exakte Mengengleichheit von Allowlist und den zehn realen Antigravity-Zeilen des Marktvergleichs sowie den zwei Zeilen der internen README; inhaltliche Einzelprüfung aller zehn zugelassenen Marktzeilen auf Rollenfreiheit; positionsgenaue Rückschreibung der drei historischen Zeilen aus `5193eec` und eingefügte wie ersetzende Drei-Rollen-Tabellenzeile; vier Versteckversuche an beiden internen Linkzeilen; Nichtregression der aus `orchestrator.toml` abgeleiteten Inventur gegen `git ls-files` plus untracked Dateien; Nichtregression des Findingnamespace-Matchers in beide Richtungen; Unverändertheit aller übrigen Diffstat-Zeilen gegenüber der Vorrunde; `git diff --check` mit Exitcode 0 | Der Guard bleibt tokenbasiert und allowlistfixiert: Eine Rollenbehauptung, die das Wort Antigravity vermeidet, sowie eine gemeinsame Änderung von Dokument und Allowlist im selben Commit bleiben allein dem menschlichen Diffreview überlassen | Eine spätere Aktualisierung des Marktvergleichs ändert eine zugelassene Zeile inhaltlich und zieht die Allowlist mechanisch nach, ohne dass jemand prüft, ob die neue Formulierung noch frei von einer Orchestratorrolle ist

PRE_MORTEM: In drei Monaten ist die wahrscheinlichste Fehlerursache nicht mehr eine Lücke im Guard, sondern die Bequemlichkeit im Umgang mit ihm. `_ALLOWED_MARKET_ANTIGRAVITY_LINES` fixiert zehn vollständige Absatz- und Tabellenzeilen; jede redaktionelle Pflege des Marktvergleichs — eine neue Antigravity-Version, eine korrigierte Quellenliste, eine umformulierte Spalte — macht den Guard rot. Die schnellste Reaktion ist, die geänderte Zeile in die Allowlist zu kopieren, und genau dabei geht die inhaltliche Prüfung verloren, ob die neue Formulierung Antigravity noch als externes Produkt oder wieder als Rolle beschreibt. Der Test würde weiterhin grün melden, weil er Mengengleichheit prüft und nicht Bedeutung. Die zweite, leisere Variante betrifft die Kopplung an `orchestrator.toml`: Eine spätere Verengung einer Pfadklasse verkleinert die Guardfläche stillschweigend mit, weil kein Test die Breite der Klassen festhält. Beide Varianten fallen erst auf, wenn jemand die Dokumentation gegen den Code liest.

SLICE_APPROVAL: 03 | YES

STATUS: DONE
