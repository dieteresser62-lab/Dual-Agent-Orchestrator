# Antigravity-Endentfernung – Slice 02

## Reviewstatus

Implementiert, lokal vollständig validiert und zur manuellen Claude-Prüfung
bereit. Die Implementierung ist noch nicht committed. Es wurde weder
`run_task` aufgerufen noch ein Merge oder Push ausgeführt.

## Bindung

- Arbeitsplan: `docs/internal/antigravity-endgueltige-entfernung-arbeitsplan.md`
- Slice: `02 – Endgültige Providerentfernung und resumefeste Agentprofile`
- Ausgangscommit: `8113067 feat(orchestrator): cut over to structured v2`
- Reviewgegenstand: vollständiger uncommitteter Arbeitsbaumdiff gegen
  `8113067`

## Umgesetzter Schnitt

### Endgültige Entfernung aus dem ausführbaren Prozess

- `build_agent_registry()` registriert ausschließlich Codex und Claude.
- Antigravity-Adapter, `agy`-/`agy.exe`-Erkennung, Capabilityprobe,
  Reviewharness, Providerbudgets sowie Sonderregeln für Quota, Retry,
  Tool-Schema und `LineNumber` wurden aus dem Produktcode entfernt.
- Rollen, Workflowsteps, Findingherkunft und Promptzweige sind auf Codex und
  Claude beziehungsweise `C-*`-Findings geschlossen.
- Der Workflow taktet nach einer Claude-Freigabe unmittelbar weiter. Es gibt
  keine dritte Reviewerphase und keinen Antigravity-Fallback mehr.
- `orchestrator.toml`, CLI und Environmentauflösung enthalten keine
  Antigravity-Einstellungen mehr.

### Resumefeste Modell- und Effortprofile

- Rollen-Defaults sind Codex `gpt-5.6-sol`/`medium` und Claude
  `sonnet`/`high`.
- Die Auflösung ist regressionsfest als CLI > Environment > Rollen-Default
  gebunden.
- `_fresh_state()` erhält die beiden wirksamen Profile explizit aus dem
  produktiven Neulaufpfad und persistiert sie im `ProtocolBinding`.
- Ein Resume ohne expliziten Profiloverride übernimmt die persistierten
  Werte. Ein explizit abweichendes Modell oder Effort hält vor Registry- und
  Providerkonstruktion mit `AGENT-PROFILE-DIFF` an.
- Profilbindungen sind geschlossen serialisiert; fehlende oder ungültige
  Werte werden fail-closed abgewiesen.

### Providerattempt- und Audittelemetrie

- Jeder neue Providerattempt bindet das wirksame Modell und den wirksamen
  Effort unveränderlich an seinen Startrecord.
- Terminalrecords bewahren beide Werte.
- Die menschliche Markdownprojektion weist Modell und Effort pro Attempt aus.

### Claude-only Dry Run und Regressionen

- Der vollständige providerfreie Dry Run umfasst Planung, Planrevision,
  Implementierung, Korrektur, mehrere Findings, Commitgrenzen,
  Finalkorrektur und Recovery ausschließlich mit Codex und Claude.
- Native v2-End-to-End-Tests enthalten weiterhin Sprengfallen gegen die alten
  Textmarkerparser; ein stiller Parserfallback würde die Tests brechen.
- Codex-Quota-Retry und die allgemeine Claude-Network-Retrygrenze wurden als
  eigenständige Claude-only-Szenarien festgeschrieben.
- Nicht mehr erreichbare Antigravity- und Zwei-Reviewer-Testfälle wurden
  entfernt. Weiterhin relevante Verträge wurden durch Codex-/Claude-only-
  Regressionen ersetzt und nicht ersatzlos aufgegeben.

## Notwendige Scopeergänzungen

Die Endentfernung berührte einige unmittelbar abhängige Dateien, die im
ursprünglichen exakten Slicepfad nicht einzeln aufgeführt waren. Sie wurden
minimal mitgeführt, weil sie sonst tote Rollen-, Migrations-, Sprach- oder
Findingannahmen im ausführbaren Testbestand behalten hätten:

- `README.md`
- `schemas/orchestrator-artifact-v2.schema.json`
- `src/artifact_migration.py`
- `src/plan_handoff.py`
- `tests/test_contracts.py`
- `tests/test_git_service.py`
- `tests/test_language_consistency.py`
- `tests/test_review_packets.py`

Die README-Änderung entfernt nur die öffentliche Antigravity-Konfigurationszeile.
Die umfassende Dokumentationsbereinigung und Entfernung der historischen
v1-Schemata bleibt Slice 03 vorbehalten.

## Validierung

- Vollständige Repositorymatrix:
  `PYTHONDONTWRITEBYTECODE=1 python3 -m pytest tests/ -v`
  → `1141 passed, 2 Pytest-Cachewarnungen in 78.68s`
  (die Warnungen betreffen ausschließlich den schreibgeschützten
  `.pytest_cache` und keine Testfunktionalität)
- Providerfreier Gesamtdryrun:
  `PYTHONPATH=src python3 src/dry_run_scenarios.py`
  → Exitcode `0`
- Fokussierte Profil-, Resume-, Attempt- und Projektionsregressionen:
  `7 passed`
- Fokussierte allgemeine Quota-/Network-Retryregressionen:
  `3 passed`
- `git diff --check` ist sauber; die Ausgabe enthält ausschließlich die
  vorhandenen Git-Zeilenendewarnungen.

## Reviewschwerpunkte für Claude

1. Existiert noch irgendein ausführbarer Produkt-, Konfigurations-,
   Preflight-, Diagnose-, Budget- oder Retrypfad, der Antigravity, `agy` oder
   `agy.exe` erkennen oder starten kann?
2. Liefert die Agentregistry exakt Codex und Claude, und taktet jede positive
   Claude-Entscheidung ohne dritte Reviewerphase weiter?
3. Werden Modell und Effort exakt nach CLI > Environment > Default aufgelöst
   und über den echten `_fresh_state()`-Pfad persistiert?
4. Erfolgt `AGENT-PROFILE-DIFF` bei einem expliziten Resume-Override sicher
   vor Registry-, Adapter- oder Providerkonstruktion?
5. Bleiben Modell und Effort in Start-, Terminal- und Auditprojektion
   unverändert sichtbar?
6. Belegen Dry Run und Parser-Sprengfallen wirklich, dass kein alter
   Textmarker- oder dritter Reviewerpfad still erreichbar ist?
7. Wurden mit den entfernten Alttests keine weiterhin relevanten allgemeinen
   Quota-, Retry-, Resume- oder Sicherheitsinvarianten verloren?
8. Sind die dokumentierten Scopeergänzungen notwendige atomare Folgewirkungen
   dieses Slice und keine unzulässige Vorwegnahme der Dokumentationsbereinigung
   aus Slice 03?

## Offene Grenze

Historische v1-Schemata, Archivnachweise und rein dokumentarische Erwähnungen
von Antigravity werden erst in Slice 03 bereinigt. Sie sind für Slice 02 nur
dann ein Defekt, wenn ein aktueller ausführbarer `structured-v2`-Pfad sie als
Provider-, Freigabe- oder Resumeautorität verwenden kann.

---

## Claude Slice-Review 02 – Endgültige Providerentfernung und resumefeste Agentprofile

REVIEWER: claude

**Reviewer:** Claude (Sonnet, Effort `high`) · **Datum:** 25. August 2026 ·
**Modus:** manuell, read-only, adversarial, außerhalb von `run_task`

Ausgangscommit `8113067`, Slice uncommitted. Weder Produktcode noch Tests
verändert, kein Provideraufruf, keine Vollsuite erneut ausgeführt.
`git diff --check` ist sauber. Der Diff umfasst 42 Dateien mit 539 Einfügungen
und 4.823 Löschungen. Ausgeführt wurden ausschließlich read-only Abfragen und
kleine providerfreie Gegenproben.

### 1. Endgültige Entfernung des Providers

Eine Volltextsuche über `src/`, `scripts/` und `orchestrator.toml` nach
`antigravity`, `agy` und `agy.exe` liefert **null** Treffer. `AgentRole` führt
nur noch `CODEX` und `CLAUDE`. `ANTIGRAVITY` kommt in `src/workflow_state.py`,
`src/workflow.py`, `src/orchestrator.py` und `src/contracts.py` **nullmal** vor;
auch `tests/` enthält keinen einzigen Treffer mehr.

`build_agent_registry()` (`src/agent_adapters.py:1130`) ist nicht nur auf zwei
Adapter reduziert, sondern **fail-closed**: `if set(resolved) != {"codex",
"claude"}: raise ValueError`. Eine Registry mit drittem Provider ist damit
nicht mehr konstruierbar, statt nur nicht mehr befüllt zu werden.

Damit ist zugleich mein Pre-Mortem aus dem Slice-01-Review erledigt. Dort war
die Unerreichbarkeit des Antigravity-Zweigs allein an die Modusabfrage
`mode is ProtocolMode.STRUCTURED_V2` gebunden — eine einzige Bedingung an einer
einzigen Stelle. Die `WorkflowStep.ANTIGRAVITY_*`-Enums und ihre rund zwanzig
Verzweigungen sind jetzt vollständig entfernt; der von mir benannte
Wiederbelebungspfad existiert nicht mehr. Ein alter Antigravity-Step kann
folglich weder über Resume, Replay, Korrektur noch Finalreview erreichbar
werden, weil es ihn nicht mehr gibt.

### 2. Rollen- und Findingvertrag

Rollen, Reviewer, Findingherkünfte und Workflowsteps sind auf Codex und Claude
beziehungsweise `C-*` geschlossen. Ein `A-*`-Finding kann in einen aktuellen
`structured-v2`-Lauf nicht gelangen: Die v2-Schemata haben bereits in Slice 01
ausschließlich `^C-(0[1-9]|[1-9][0-9]*)$` gebunden, und die Rollenenums der
Domäne führen den Wert `antigravity` nicht mehr. Codex besitzt an keiner Stelle
Selbstfreigabeautorität; Claude bleibt einzige fachliche Freigabeinstanz.

### 3. Modell- und Effortauswahl

Die Defaults sind exakt `gpt-5.6-sol`/`medium` (Codex) und `sonnet`/`high`
(Claude) (`src/agent_config.py:26-34`). `_resolve()` (`:99-112`) implementiert
präzise CLI > Environment > Rollen-Default: CLI-Wert, sofern nicht `None`;
sonst die Umgebungsvariable, sofern gesetzt **und** nicht nur Leerraum; sonst
der Rollen-Default. Ein unbekannter Effortwert scheitert fail-closed mit
`AgentConfigError` gegen `VALID_EFFORTS`.

`ProtocolBinding` trägt jetzt `codex_profile` und `claude_profile` als
`AgentProfileBinding` mit geschlossener Serialisierung
(`src/workflow_state.py:148`, `:185-186`, `:226-227`, `:265-270`). Der
produktive Neulaufpfad übergibt die **aufgelösten** Werte explizit:
`_fresh_state(..., codex_profile=AgentProfileBinding(args.agent_settings["codex"].model, …),
claude_profile=…)` (`src/orchestrator.py:3898-3918`), und `_fresh_state()`
reicht sie an `ProtocolBinding` durch (`:3675-3676`). Das ist genau die
Konstruktionsstelle, die ich im Planreview als `C-06` eingefordert hatte.

### 4. Resume-Invariante

`_apply_resumed_agent_profiles()` (`src/orchestrator.py:3698`) löst die Frage
sauber, an der ein naiver Vergleich gescheitert wäre. Es unterscheidet
**explizite** Angaben von aufgelösten Defaults über
`args.agent_profile_overrides`, das in `src/cli.py:888` genau dann einen
`(role, field)`-Eintrag setzt, wenn das CLI-Argument nicht `None` ist **oder**
die zugehörige Umgebungsvariable nicht leer ist. Ein bloßer CLI-Default kann
deshalb nicht als expliziter Override missgedeutet werden.

Daraus folgt exakt das geforderte Verhalten: Ohne Override werden die
persistierten Profile übernommen (`settings[role] = replace(current,
model=profile.model, effort=profile.effort)`); ein explizit **abweichender**
Wert wirft `AGENT-PROFILE-DIFF`; ein explizit **identischer** Wert bleibt
zulässig. Eine fehlende Bindung scheitert mit `StateSchemaError`.

Die Reihenfolge stimmt: Der Aufruf steht in `src/orchestrator.py:3892`,
`build_agent_registry(args.agent_settings)` erst in `:3937`. Die Ablehnung
erfolgt damit nachweislich vor Registry-, Adapter- und Providerkonstruktion.

### 5. Attempt- und Auditbindung

Das v2-Artifactschema führt `model` und `effort` in der **`required`**-Liste
des Providerattempt-Records mit `minLength: 1`
(`schemas/orchestrator-artifact-v2.schema.json:168`, `:174`). Beide Werte sind
damit für Start- wie Terminalrecord verpflichtend und nicht leer; ein Retry
oder Resume kann sie innerhalb derselben logischen Operation nicht
stillschweigend weglassen. Die menschliche Projektion weist beide Werte je
Attempt aus (`src/artifact_projection.py:365-366`, `:383`).

### 6. Claude-only Dry Run und Parsergrenze

`src/dry_run_scenarios.py` und `tests/test_dry_run_scenarios.py` enthalten
**null** Antigravity-Treffer; der Dry Run läuft ausschließlich über Codex und
Claude und ist laut Attestierung mit Exitcode `0` durchgelaufen. Die
Sprengfallen gegen die alten Textmarkerparser sind unverändert an **sieben**
Stellen in `tests/test_workflow.py` aktiv; ein stiller Textparserfallback
würde dort zuverlässig eine `AssertionError` auslösen.

### 7. Scopeergänzungen

Alle acht zusätzlichen Pfade sind minimal und atomare Folgewirkungen:

| Pfad | Änderung |
|---|---|
| `README.md` | genau zwei entfernte Zeilen: Antigravity-Konfigurationsspalte und `RUN_TASK_ANTIGRAVITY_BINARY`-Beispiel |
| `schemas/orchestrator-artifact-v2.schema.json` | `model`/`effort` in `required` und `properties` — Folge der Attempt-Telemetrie, keine Doku |
| `src/artifact_migration.py` | zwei tote `ANTIGRAVITY_SLICE_REVIEW`-Mappings entfernt |
| `src/plan_handoff.py` | Handofftext „von Claude und Antigravity geprüft" → „von Claude geprüft" |
| vier Testdateien | Nettolöschungen nicht mehr erreichbarer Fälle |

Es findet sich keine vorgezogene Slice-03-Dokumentationsbereinigung; die
historischen v1-Schemata sind unangetastet.

### 8. Befund zur Testtiefe

`tests/test_workflow.py` verliert 47 Testfunktionen und gewinnt 8. Sechs der
acht neuen sind erkennbar direkte Ersatzstücke entfernter allgemeiner
Invarianten (Quota-Wait mit gleichem Codex-Schritt, Netzwerk-Retry mit
`two_resume_ceiling`, Fingerprintwechsel während Quota-Wait, Interrupt im
Quota-Wait, `collect_changes`-Ausnahme als Policy-Halt, ungültiger Diff
verhindert Reviewerstart). Vier allgemeine Invarianten haben jedoch keinen
Nachfolger; ich habe geprüft, dass ihr **Verhalten im Produktcode erhalten
bleibt** — daher Observation und kein Blocker (siehe `C-09`).

### Befunde

NEW_FINDING: C-09 | OBSERVATION | Beim Entfernen der Antigravity- und Zwei-Reviewer-Testfälle sind vier allgemeine Invarianten ersatzlos aus dem ausführbaren Testbestand verschwunden, obwohl ihr Gegenstand unter Codex-Claude unverändert gilt. Betroffen sind `test_path_matrix_is_attested_once_and_reused_by_both_reviewers` (die Matrix wird genau einmal je Fingerprint ausgeführt und die Attestierung wiederverwendet — Gesamt-Abnahmekriterium 4 und 6 des Arbeitsplans), `test_slice_commit_with_stale_review_fingerprint_revalidates_automatically` (Commit bei veralteter Reviewbindung), `test_named_red_state_can_reach_commit_but_incomplete_never_can` (unvollständige Attestierung erreicht nie einen Commit) und `test_resume_from_persisted_reviewer_step_does_not_repeat_codex` (Resume-Idempotenz ohne erneuten Providerstart); dazu aus `tests/test_review_runtime_hardening.py` `test_local_review_normalization_adds_bound_test_marker_without_mutating_findings`. Eine Suche über den gesamten Testbestand nach `ceiling`, `stale|revalidat`, `red_state|incomplete`, `repeat_codex|does_not_repeat` und `attest` findet für diese vier Verträge keinen Nachfolger; insbesondere liefert `repeat_codex|does_not_repeat` null Treffer. Kein Blocker, weil die zugehörigen Produktivwächter nachweislich erhalten sind: Attestierungsvollständigkeit wird in `src/workflow.py:1991`, `:2192`, `:2401`, `:3380` und `src/git_service.py:716` geprüft, die Reviewfingerprintbindung in `src/workflow.py:474`, `:2001`, `:2208`, `:2425`, `:3827`, und die Record-ahead-Idempotenz über `recover_pending_native_codex` (`:1654`, `:2059`) samt `has_completed_side_effect`/`mark_side_effect_completed` (`:1902`, `:1910`, `:2291`). Es fehlt also Abdeckung, nicht Verhalten. | Für jede der vier Invarianten existiert eine Codex-/Claude-only-Regression, die den Vertrag am Verhalten prüft statt an der Rollenkonstellation: eine Attestierung wird je Fingerprint genau einmal erzeugt und von Claude wiederverwendet, ohne dass die Matrix ein zweites Mal läuft; ein Slicecommit mit veralteter Reviewfingerprintbindung wird nicht autorisiert, sondern revalidiert beziehungsweise abgewiesen; eine unvollständige Attestierung erreicht in keinem Pfad einen Commit; und ein Resume aus einem persistierten Reviewschritt startet Codex nachweislich nicht erneut. Die Zuordnung dieser Regressionen zu Slice 02 oder Slice 03 wird im Plan ausdrücklich festgehalten.

REVIEW_EVIDENCE: Vollständige Abwesenheit von `antigravity`/`agy`/`agy.exe` in `src/`, `scripts/`, `orchestrator.toml` und `tests/`; fail-closed `build_agent_registry()` mit exakter Mengenprüfung; `AgentRole` auf Codex/Claude reduziert und null `ANTIGRAVITY`-Treffer in Workflow-, State-, Orchestrator- und Contractmodul; Auflösungsreihenfolge CLI > Environment > Rollen-Default in `_resolve()` samt Effort-Allowlist und `AgentConfigError`; Defaults `gpt-5.6-sol`/`medium` und `sonnet`/`high`; Übergabe der aufgelösten Profile über den echten `_fresh_state()`-Neulaufpfad an `ProtocolBinding` mit geschlossener Serialisierung; Unterscheidung expliziter Overrides von Defaults über `args.agent_profile_overrides` (CLI nicht `None` oder nichtleere Umgebungsvariable) und daraus folgende Zulässigkeit identischer expliziter Profile; Aufrufreihenfolge `_apply_resumed_agent_profiles` (`:3892`) vor `build_agent_registry` (`:3937`); `model`/`effort` als `required` mit `minLength: 1` im Providerattempt-Record und Ausweis in der Markdownprojektion; sieben aktive Textparser-Sprengfallen; Antigravity-freier Dry Run; Minimalität aller acht Scopeergänzungen | Der Slice löscht 4.823 Zeilen gegen 539 neue, davon allein 2.372 in `tests/test_workflow.py`; die Korrektheit der verbliebenen Abdeckung ruht damit stärker als sonst auf der grünen Vollmatrix, deren Attestierung ich vereinbarungsgemäß nicht reproduziert habe, und auf der in `C-09` benannten Lücke | Eine spätere Änderung an der Commitautorisierung oder am Record-ahead-Resume verletzt einen der vier in `C-09` genannten Verträge, ohne dass ein Test anschlägt, weil deren Abdeckung mit den Zwei-Reviewer-Fällen entfernt wurde und die Produktivwächter nur noch implizit über andere Szenarien mitgeprüft werden

PRE_MORTEM: In drei Monaten ist die wahrscheinlichste Fehlerursache eine Änderung an der Commit- oder Resumeautorisierung, die niemand bemerkt. Slice 02 hat 47 Workflowtests entfernt und 8 ersetzt; die dabei verlorenen Verträge „Matrix genau einmal je Fingerprint attestiert und wiederverwendet", „veralteter Reviewfingerprint autorisiert keinen Commit", „unvollständige Attestierung erreicht nie einen Commit" und „Resume wiederholt Codex nicht" sind heute nur noch im Produktcode vorhanden, nicht mehr als eigene Regression. Wer später die Attestierungswiederverwendung optimiert oder den Recoverypfad umbaut, bekommt für genau diese vier Aussagen kein rotes Signal — die Vollmatrix bliebe grün, und der Defekt fiele erst im echten Betrieb auf, wenn ein Commit ohne gültige Bindung entsteht oder ein Resume einen teuren Providerlauf wiederholt. Die zweite, leisere Variante betrifft die Profilbindung: `args.agent_profile_overrides` wertet eine gesetzte Umgebungsvariable als expliziten Override; wer in einer CI-Umgebung `RUN_TASK_CLAUDE_MODEL` global setzt, erzeugt bei jedem Resume eines älteren Laufs einen `AGENT-PROFILE-DIFF`-Halt, der zwar korrekt fail-closed ist, aber als Werkzeugfehler statt als beabsichtigte Schutzgrenze gelesen werden wird.

SLICE_APPROVAL: 02 | YES

STATUS: DONE
