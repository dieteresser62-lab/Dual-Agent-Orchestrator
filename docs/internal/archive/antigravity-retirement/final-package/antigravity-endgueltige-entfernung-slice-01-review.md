# Antigravity-Endentfernung – Slice 01

## Reviewstatus

Implementiert, lokal vollständig validiert und zur manuellen Claude-Prüfung
bereit. Es wurde weder `run_task` aufgerufen noch ein Commit, Merge oder Push
ausgeführt.

## Bindung

- Arbeitsplan: `docs/internal/antigravity-endgueltige-entfernung-arbeitsplan.md`
- Slice: `01 – Atomarer Structured-v2-, Vertrags- und Evidenzcutover`
- Ausgangs-HEAD: `b3d49ff`
- Reviewgegenstand: vollständiger uncommitteter Arbeitsbaumdiff gegen
  `b3d49ff`

## Umgesetzter Schnitt

### Structured-v2 und native Verträge

- Neue, geschlossene v2-Schemata für Artifactrecords, Codex-Request/-Resultat
  und Claude-Review-Request/-Resultat angelegt.
- Neue Läufe werden an `structured-v2` mit Schemafassung `2` gebunden.
- Native Transportidentitäten atomar auf `native-codex-v2` und
  `native-claude-review-v2` gehoben; damit ist die Plan-Observation `C-08`
  innerhalb dieses Slice erledigt.
- Codex-Dispositionen und Claude-Findings akzeptieren in v2 ausschließlich
  `C-*`. Eine Codex-Disposition auf `A-01` scheitert explizit bereits am
  statischen v2-Resultatschema vor der Domänenkonvertierung.
- Das v2-Codex-Reader-Schema verlangt `finding_dispositions` auch beim
  `plan_result`; es enthält keinen stillen historischen v1-Kompatibilitätsast.
- Alle fünf v2-Schemaidentitäten und Titel sind konsistent als v2
  ausgewiesen.

### Claude-only Workflowautorität

- Positive Claude-Planreviews führen direkt zu Plancommit/Handoff oder zum
  Abschluss der Planung.
- Positive Claude-Slicereviews führen direkt zum lokalen Slicecommit.
- Positive Claude-Finalreviews schließen bei leerer Findingmenge direkt ab.
- Ablehnungen führen weiterhin in die gebundene Codex-Revision beziehungsweise
  Korrekturrunde.
- Commitautorisierung, Auditprojektion, Record-ahead-Recovery und
  Finalreview-Preflight benötigen genau eine aktuelle, attestierungsgebundene
  Claude-Freigabe. Codex erhält keine Selbstfreigabeautorität.
- Der scripted Dry Run durchläuft Planung, zwei Slices und Finalreview bereits
  in Slice 01 ohne Antigravity-Schritt.

### Resume- und Artifactgrenze

- Artifactrecords verwenden das geschlossene v2-Schema und weisen
  Antigravity als Provider, Reviewer und Findingreporter ab.
- `legacy-state-v3` und `structured-v1` werden beim Resume mit dem stabilen
  Diagnosecode `UNSUPPORTED-PROTOCOL` abgewiesen. Es wird weder migriert noch
  auf einen Text- oder v1-Pfad zurückgefallen.
- Watch-Sidecars und technische Resume-Diagnosen akzeptieren beziehungsweise
  benennen den aktuellen `structured-v2`-Modus.

### V1-Evidenzrückbau

- Der aktive v1-Corpus, sein Manifest und das Probeprogramm wurden aus dem
  ausführbaren Test-/Scriptpfad entfernt.
- Die beiden v1-Corpus-/Probe-Testmodule wurden entfernt.
- Die drei Quellen liegen bytegleich und ausdrücklich nichtautoritativ unter
  `docs/internal/archive/native-contract-v1-evidence/`.
- Archivhashes:
  - `corpus.jsonl`: `16ebf8c6488b251ead6aeffd5785d91242e45f10f929c1a9a71e16c7369c1332`
  - `manifest.json`: `1dbd375a134ae894b5144d9f1aeb0096bdd53ad29732246a1bd6005482f1c089`
  - `native_contract_probe.py`: `32269a8572f579a746fcb129bf672ee18fc23046e1d86d1c0ea332a99150860c`
- Der aktive Differentialnachweis erzeugt alle acht Writerformen dynamisch aus
  gebundenen v2-Kontexten und prüft sowohl unerwartete lokale Ablehnung als
  auch stille Überpermissivität providerfrei.
- Die eigenständigen Provider-Subset-Register bleiben als v1-Register aktiv;
  alle 13 Regressionstestverweise lösen weiterhin auf existierende Tests auf.

## Notwendige Scopeergänzungen

Vier Testpfade waren im freigegebenen Slice nicht eigens aufgeführt, wurden
aber durch die dort ausdrücklich verlangte atomare Vollmatrix unmittelbar
betroffen und deshalb minimal mitgeführt:

- `tests/test_agent_adapters.py`
- `tests/test_agent_runtime.py`
- `tests/test_inbox_watcher.py`
- `tests/test_orchestrator_watch_cli.py`

Sie enthalten keine vorgezogene Slice-02-Providerentfernung. Geändert wurden
nur v2-Vertragsfixtures beziehungsweise Watch-Protokollassertionen, die nach
dem Schema- und Protokollcutover andernfalls zwingend rot geblieben wären.

## Validierung

- Vollständige Repositorymatrix:
  `python3 -m pytest tests/ -v`
  → `1263 passed, 2 Pytest-Cachewarnungen in 110.69s`
  (die Warnungen betreffen ausschließlich den schreibgeschützten
  `.pytest_cache` und keine Testfunktionalität)
- Native Vertrags-/Adaptermatrix nach Verschärfung des v2-Readers:
  `179 passed`
- Watch-/CLI-Matrix: `48 passed`
- Dynamischer v2-Differential- und Providerregister-Nachweis: `11 passed`
- Alle fünf neuen JSON-Dateien mit `python3 -m json.tool` geprüft.
- `git diff --check` ist sauber; die Ausgabe enthält ausschließlich die
  vorhandenen Git-Zeilenendewarnungen.

## Reviewschwerpunkte für Claude

1. Sind regulärer Pfad und Record-ahead-Replay für Plan, Slice und Finalreview
   semantisch identisch und jeweils direkt an Claude angeschlossen?
2. Kann irgendein v2-Schema oder v2-Domänenmodell noch Antigravity oder ein
   `A-*`-Finding akzeptieren?
3. Bleibt Commitautorisierung fail-closed bei fehlender, fremder, veralteter
   oder nicht attestierter Claude-Freigabe?
4. Ist `UNSUPPORTED-PROTOCOL` vor jedem Zugriff auf historische Runtimeketten
   stabil und ohne Migration wirksam?
5. Ist das v1-Evidenzarchiv nachweislich nicht ausführbar und kann es keine
   v2-Autoritätsaussage liefern?
6. Ist der dynamische Differentialnachweis stark genug, unerwartete lokale
   Ablehnung und stille Überpermissivität zu erkennen?
7. Sind die vier dokumentierten Scopeergänzungen tatsächlich nur atomare
   Folgewirkungen von Slice 01 und keine vorgezogene Slice-02-Arbeit?

## Offene Grenze

Die noch im Arbeitsbaum vorhandenen, für v2 unerreichbaren Antigravity-Adapter,
Legacy-Enums, Konfigurationswerte, Parser- und Dokumentationsreste gehören
ausdrücklich zu Slice 02 beziehungsweise Slice 03. Sie dürfen diesen Slice nur
dann blockieren, wenn ein ausführbarer `structured-v2`-Pfad sie noch erreichen
oder als Autorität verwenden kann.

---

## Claude Slice-Review 01 – Atomarer Structured-v2-, Vertrags- und Evidenzcutover

REVIEWER: claude

**Reviewer:** Claude (Sonnet, Effort `high`) · **Datum:** 25. August 2026 ·
**Modus:** manuell, read-only, adversarial, außerhalb von `run_task`

Ausgangs-HEAD `b3d49ff`, Slice uncommitted. Nichts implementiert, weder
Produktcode noch Tests verändert, kein Provideraufruf, keine Vollsuite erneut
ausgeführt. Der vorhandene Slicebericht ist unverändert. Ausgeführt wurden
ausschließlich read-only Abfragen und kleine providerfreie Gegenproben zur
Prüfung konkreter Defekthypothesen. `git diff --check` ist sauber.

### 1. Structured-v2-Cutover

`ProtocolMode` führt jetzt `STRUCTURED_V2 = "structured-v2"`; `STRUCTURED_V1`
und `LEGACY_STATE_V3` bleiben ausschließlich zur Wiedererkennung erhalten.
`ProtocolBinding.__post_init__` bindet `structured-v2` an Schemafassung `"2"`
und verlangt für beide nativen Transporte ausdrücklich
`ProtocolMode.STRUCTURED_V2` — eine v1-Bindung mit v2-Transport wird
zurückgewiesen. Die Transportidentitäten sind auf `native-codex-v2` und
`native-claude-review-v2` gehoben; damit ist meine Plan-Observation `C-08`
tatsächlich innerhalb dieses Slice erledigt und nicht nur behauptet. Der
produktive Neulaufpfad setzt `ProtocolBinding(mode=ProtocolMode.STRUCTURED_V2,
schema_version="2", …)`.

Ein Mischbetrieb ist nicht auffindbar: Außerhalb des Enum-Members und eines
Docstrings enthält `src/` keine aktive v1-Protokoll- oder
Transportkonstante mehr. Beide Vertragsmodule melden `…-v2` als
`SCHEMA_VERSION`. Die fünf v2-Schemadateien tragen durchgehend v2-`$id` und
-Titel; die auf den ersten Blick uneinheitliche `$id`-Schreibweise ist **exakt
aus v1 ererbt** — jede Datei behält den Stil ihres Vorgängers und hebt nur die
Version. Das ist die minimale, richtige Änderung und kein Slice-01-Defekt.

### 2. Claude-only Workflowautorität

Ich habe die Verzweigung nach einer positiven Reviewentscheidung direkt gelesen
(`src/workflow.py:2286-2342`). Der v2-Ast ist hart gegated auf
`reviewer is AgentRole.CLAUDE and state.protocol_binding is not None and
state.protocol_binding.mode is ProtocolMode.STRUCTURED_V2` und führt
- Planreview einer `PLAN`-Work-Unit: Benutzergate, sonst `SLICE_COMMIT`
  (`plan_only`) beziehungsweise Abschluss der Work-Unit;
- Anchor-Planreview: an den persistierten Resume-Schritt;
- Finalreview: `WorkflowExecutionError` bei offenen Findings, sonst Abschluss;
- Slicereview: direkt auf `WorkflowStep.SLICE_COMMIT`.

Der verbleibende `WorkflowStep.ANTIGRAVITY_SLICE_REVIEW` steht ausschließlich
im `else`-Zweig, also für Läufe, deren Modus **nicht** `STRUCTURED_V2` ist.
Da neue Läufe immer v2 sind und v1/Legacy beim Resume abgewiesen werden, ist
dieser Zweig für jeden ausführbaren Lauf tot. Das entspricht exakt der
Reviewgrenze: Antigravity bleibt vorhanden, ist aber von keinem v2-Pfad
erreichbar.

`WorkflowCommitRequest` trägt jetzt genau `fingerprint`,
`attestation: ValidationAttestation` und `claude_review: ContractResult` —
also genau eine Claude-Reviewreferenz, fingerprint- und attestierungsgebunden.
Eine Suche nach einem Freigabepfad über `AgentRole.CODEX` liefert keinen
Treffer; Codex besitzt an keiner Stelle Selbstfreigabeautorität.

### 3. Entfernung von Antigravity aus dem v2-Vertrag

Alle fünf v2-Schemata enthalten **null** `antigravity`-Treffer und **null**
`[CA]-`-Muster; Findings sind durchgehend auf
`^C-(0[1-9]|[1-9][0-9]*)$` begrenzt, Reviewer-Enums lauten `["claude"]`, das
Rollenenum `["codex","claude","orchestrator","user"]`.

Providerfrei nachgestellt:

| Gegenprobe | Ergebnis |
|---|---|
| Codex-Disposition `A-01` | `schema-invalid` am statischen v2-Resultatschema, **vor** der Domänenkonvertierung |
| Kontrolle Codex-Disposition `C-01` | akzeptiert |
| Reviewresultat mit `reviewer: "antigravity"` | `schema-invalid` |
| Reviewresultat mit neuem Finding `A-01` | `schema-invalid` |

Damit ist die zentrale Behauptung des Sliceberichts belegt: Die Ablehnung
erfolgt statisch am Schema und nicht erst im Domänenkonverter.

### 4. Resume- und Replaygrenzen

`resolve_resume_state()` (`src/artifact_migration.py`) prüft
`state.effective_protocol_mode` als **allererste** Anweisung und wirft bei
allem außer `STRUCTURED_V2` einen `ArtifactResumeError` mit
`ReplayDiagnosticCode.UNSUPPORTED_PROTOCOL`. Die Ablehnung liegt damit vor
`ArtifactStore(...).load_chain()`, vor `replay_artifacts(...)` und vor jedem
Provideraufruf; der Docstring hält ausdrücklich fest, dass historische Zustände
unangetastet auf der Platte bleiben und ohne Migration abgewiesen werden.

Der Fehlmetadatenfall ist fail-closed in die richtige Richtung aufgelöst:
`effective_protocol_mode` liefert bei fehlender Bindung `LEGACY_STATE_V3` —
ein Altzustand kann also nicht durch fehlende Metadaten als v2 gelten. Drei
Tests binden den Diagnosecode (`tests/test_artifact_migration.py:143`,
`tests/test_orchestrator_runtime.py:3476`, `tests/test_state_io.py:457`).

### 5. V1-Evidenzrückbau

Alle drei Archivdateien sind **bytegleich** zu den in `b3d49ff` gelöschten
Originalen; ich habe die Digests gegen `git show b3d49ff:<pfad>` neu berechnet
und mit den im Slicebericht dokumentierten Werten verglichen:

| Datei | SHA-256 | bytegleich |
|---|---|---|
| `corpus.jsonl` | `16ebf8c6488b251e…` | ja |
| `manifest.json` | `1dbd375a134ae894…` | ja |
| `native_contract_probe.py` | `32269a8572f579a7…` | ja |

Alle drei dokumentierten Werte stimmen exakt. `git status` bestätigt die
Löschung von `scripts/native_contract_probe.py`, beider Corpusfixtures sowie
der beiden aktiven Testmodule `tests/test_native_contract_corpus.py` und
`tests/test_native_contract_probe.py`. Der Archivpfad liegt unter
`docs/internal/` und ist damit kein Test-, Script-, Packaging- oder
Importpfad; die dortigen Daten können weder Resume noch eine
v2-Vertragsentscheidung beeinflussen.

### 6. Differential- und Providerregister

Der aktive Differentialnachweis erzeugt weiterhin alle acht Writerformen und
belegt deren Verschiedenheit über `assert len(digests) == 8`. Beide
Beweisrichtungen sind vorhanden: die rote Kontrolle
(`test_deliberately_loosened_writer_rule_trips_the_red_control`) für
writer-valides, lokal unerwartet abgelehntes JSON, und die
Überpermissivitätskontrollen
(`test_closed_own_finding_mutations_are_rejected_by_writer_and_domain`,
`test_denial_cannot_satisfy_blocker_state_by_reopening_closed_blocker`) für
fachlich unzulässiges JSON, das beide Schichten akzeptieren würden.

Die Provider-Subset-Register bleiben planmäßig unter ihrer eigenständigen
Identität `native-provider-schema-exceptions-v1` aktiv. Alle **13**
`regression_test`-Verweise lösen auf existierende Testfunktionen auf — ich habe
jeden einzelnen statisch aufgelöst. Sie leiten keine v2-Autoritätsaussage ab,
sondern versionieren ausschließlich den Provider-Subset-Vertrag.

### 7. Scopeergänzungen

Die vier zusätzlichen Testpfade umfassen zusammen 41 geänderte Zeilen
(25 Einfügungen, 16 Löschungen). Eine gezielte Suche nach vorgezogener
Slice-02-Arbeit findet weder `build_agent_registry`-Änderungen noch
Adapter-, Registry- oder Binärerkennungsentfernung. Der einzige
Antigravity-Bezug ist die **Entfernung** eines Fixture-Enums
`["claude", "antigravity"]` — also eine zwingende Folge des v2-Schemacutovers,
keine Providerbereinigung. Die Einordnung des Sliceberichts ist damit
zutreffend.

### Befunde

Kein Blocker, keine Observation. Alle im Slicebericht aufgestellten
Behauptungen, die ich geprüft habe, halten der Nachrechnung stand; insbesondere
habe ich keine Aussage ungeprüft übernommen, sondern Archivdigests,
Schemainhalte, Verzweigungslogik, Ablehnungsreihenfolge und Registerverweise
direkt am Code beziehungsweise an den Dateien verifiziert.

REVIEW_EVIDENCE: Unveränderliche `structured-v2`-Bindung samt Schemafassung 2 und v2-Transportidentitäten einschließlich Bindungsvalidierung; Abwesenheit aktiver v1-Protokoll-/Transportkonstanten; v2-Herkunft aller fünf Schemata mit aus v1 ererbtem `$id`-Stil; statische Ablehnung von `A-01`, `reviewer=antigravity` und `A-*`-Findings vor der Domänenkonvertierung mit positiver `C-01`-Kontrolle; Gating des Antigravity-Zweigs hinter `mode is not STRUCTURED_V2` und direkte Claude-Anschlüsse für Plan-, Slice- und Finalfreigabe einschließlich Nullfinding-Bedingung im Finalreview; `WorkflowCommitRequest` mit genau einer fingerprint- und attestierungsgebundenen Claude-Reviewreferenz und ohne Codex-Freigabepfad; `UNSUPPORTED-PROTOCOL` als erste Anweisung in `resolve_resume_state()` vor Chainladen, Replay und Provideraufruf, mit fail-closed Fallback auf `LEGACY_STATE_V3` bei fehlender Bindung und drei bindenden Tests; Bytegleichheit und Digestrichtigkeit aller drei Archivdateien gegen `b3d49ff`; Entfernung beider aktiver v1-Testmodule; acht dynamische Writerformen mit roter Kontrolle und Überpermissivitätskontrollen; Auflösbarkeit aller 13 Registerverweise; Minimalität der vier Scopeergänzungen | Slice 01 ist mit 44 geänderten und fünf neuen Dateien der mit Abstand größte Einzelschnitt des Pakets und stellt Protokoll, fünf Schemata, beide Vertragsmodule, Artifactkette, Recovery, Evidenzrückbau und den scripted Dry Run in einem Schritt um; die Korrektheit ruht damit stark auf der grünen Vollmatrix, deren Attestierung ich vereinbarungsgemäß nicht reproduziert habe | Die noch vorhandenen `WorkflowStep.ANTIGRAVITY_*`-Enums und ihre rund zwanzig Verzweigungen in `src/workflow.py` sind heute allein durch die Modusabfrage `mode is ProtocolMode.STRUCTURED_V2` unerreichbar; wer in Slice 02 diese Abfrage vereinfacht oder eine der Verzweigungen umbaut, bevor die Enums entfernt sind, kann den toten Zweig unbemerkt wieder erreichbar machen

PRE_MORTEM: In drei Monaten ist die wahrscheinlichste Fehlerursache, dass die Unerreichbarkeit des Antigravity-Zweigs mit seiner Entfernung verwechselt wird. Slice 01 lässt `WorkflowStep.ANTIGRAVITY_PLAN_REVIEW`, `…_SLICE_REVIEW` und `…_FINAL_REVIEW` samt rund zwanzig Verzweigungen in `src/workflow.py` bewusst stehen und macht sie ausschließlich über die Modusbedingung `state.protocol_binding.mode is ProtocolMode.STRUCTURED_V2` tot — eine einzige Bedingung an einer einzigen Stelle. Wer in Slice 02 zuerst an dieser Verzweigung aufräumt, etwa indem er die Modusabfrage als „inzwischen immer wahr" vereinfacht, bevor die Enums und Steps verschwinden, stellt den alten `else`-Ast unbemerkt wieder scharf: Eine positive Claude-Slicefreigabe liefe dann wieder auf `ANTIGRAVITY_SLICE_REVIEW` statt auf `SLICE_COMMIT`, und weil kein Provider mehr existiert, bliebe der Lauf ohne verständliche Diagnose stehen. Die zweite, leisere Variante betrifft das v1-Evidenzarchiv: Es ist heute nur deshalb nichtautoritativ, weil es unter `docs/internal/` liegt und kein Test es importiert — eine spätere Bequemlichkeit, es als Fixturequelle „wiederzuverwenden", würde genau die Autoritätsgrenze aufheben, die dieser Slice gezogen hat.

SLICE_APPROVAL: 01 | YES

STATUS: DONE
