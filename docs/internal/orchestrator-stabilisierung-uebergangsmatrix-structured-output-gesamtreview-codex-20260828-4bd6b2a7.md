# Codex-Gesamtreview – Übergangsmatrix und Structured-Output-Resilienz

## 1. Reviewidentität und Grenze

- Reviewer: Codex
- Reviewdatum: 28. August 2026
- Reviewart: manuelles, adversariales Gesamtreview außerhalb von `run_task`
- Zielbranch: `feature/orchestrator-transition-matrix-output-resilience`
- Basis: `de1bd9ea40392c17e47efcc8d243c7de1438ae59` (`master` bei
  Paketbeginn)
- committed Branchstand zu Reviewbeginn:
  `6b609f4d23a684c3badcc99948bb3502d0839719`
- geprüfter Implementierungs-Snapshot: committed Range `de1bd9e..6b609f4`
  plus der von Claude freigegebene, noch nicht durch Codex committed
  Slice-03-Arbeitsbaum
- Inhaltsfingerprint des geprüften Snapshots:
  `4bd6b2a7f17568273b15b1c152ba78b70ebe3ad0d45d37fab86ad7554b08deec`

Der Fingerprint ist SHA-256 über die lexikographisch sortierte Folge aus
Repositorypfad, NUL-Byte und Git-Blob-ID aller 17 gegenüber `master`
geänderten oder ungetrackten Paketdateien. Dieses Reviewdokument selbst ist
nicht Bestandteil dieses Implementierungsfingerprints.

Der gewünschte Slice-03-Commit wurde nicht von Codex erzeugt. Der aktive
Repositoryvertrag verbietet Codex ausdrücklich Branchwechsel, Staging und
Commits; diese Git-Transaktion gehört dem Nutzer oder dem Orchestrator. Dieser
Bericht prüft deshalb den exakt inhaltsgebundenen Arbeitsbaum und erteilt
keine Eigenfreigabe.

## 2. Geprüfter Umfang

Der Gesamtstand umfasst:

- den freigegebenen manuellen Arbeitsplan und die drei vollständigen
  Slice-Reviewketten;
- die unabhängige Übergangs-, Gate-, Replay-, Mirror- und
  Checkpointmatrix aus Slice 1;
- die Writerschema-, Feldlängen-, Prompt- und
  Structured-Output-Druckmessung aus Slice 2;
- den erweiterten providerfreien Dry-Run-Harness, die elf gebundenen
  Resilienzfälle, deren Manifest, Roadmap und Abschlussnachweis aus Slice 3.

Die 17 gebundenen Paketpfade sind:

- `docs/internal/ORCHESTRATOR_ROADMAP_PHASE_2_PLUS.md`
- `docs/internal/orchestrator-stabilisierung-providerfreier-resilienznachweis.md`
- `docs/internal/orchestrator-stabilisierung-uebergangsmatrix-structured-output-arbeitsplan.md`
- `docs/internal/orchestrator-stabilisierung-uebergangsmatrix-structured-output-slice-01-review.md`
- `docs/internal/orchestrator-stabilisierung-uebergangsmatrix-structured-output-slice-02-review.md`
- `docs/internal/orchestrator-stabilisierung-uebergangsmatrix-structured-output-slice-03-review.md`
- `src/dry_run_scenarios.py`
- `src/prompts.py`
- `src/schema_validation.py`
- `tests/fixtures/orchestrator_resilience/manifest-v1.json`
- `tests/fixtures/structured_output_pressure/manifest-v1.json`
- `tests/test_dry_run_scenarios.py`
- `tests/test_orchestrator_resilience_evidence.py`
- `tests/test_prompts.py`
- `tests/test_schema_validation.py`
- `tests/test_structured_output_pressure.py`
- `tests/test_workflow_transition_matrix.py`

## 3. Methode

Geprüft wurden der vollständige Paketdiff gegen `master`, alle neuen und
geänderten Produktiv-, Test-, Fixture- und Dokumentationsdateien, die aktiven
Rootverträge sowie die Historie der Claude-Befunde `C-01` bis `C-17`.
Besonderer Fokus lag auf:

- Unabhängigkeit des literalen Übergangsoracles von produktiven
  Replay-/Carry-Forward-Ableitungen;
- vollständiger Klassifikation von Work-Unit-Kanten und produktiven
  Gatetripeln aus Grund, Regelidentität und Gateart;
- Record-/Mirror-/Checkpoint-Fenstern, Finding-Ledger-Erhalt sowie
  Resume- und Idempotenzverhalten;
- exakter, fail-closed Klassifikation des Claude-Structured-Output-Envelopes;
- vollständiger `maxLength`-Inventur über den schemaformenden Kontextraum und
  synthetischen Grenzproben bei 50, 80, 95, 100 und 101 Prozent;
- vollständigen Korrekturjourneys über die reale `WorkflowEngine`;
- nichtzirkulärer Bindung des Resilienzmanifests an elf tatsächlich
  ausgeführte, szenariospezifische pytest-Knoten;
- Trennung der Dimensionen Sicherheit, Verfügbarkeit und Autonomie sowie
  Ausschluss volatiler Zeit-, Token- und Kostenschwellen.

Es wurden keine Provider, Canaries, Watcher, Bootstrap-Läufe oder
persistierten Orchestratorzustände gestartet oder verändert.

## 4. Ergebnis nach Prüfdimension

### Übergänge und Finding-Ledger

Die Matrix klassifiziert alle geordneten Work-Unit-Paare und bindet die
erreichbaren Kanten an literale Erwartungen. Replay und Carry-Forward werden
gegen unabhängige Sollwerte und Mutationen geprüft. Die Korrekturjourneys
belegen Slice-Denial, Final-Denial und eine zweite Korrekturrunde mit neuem
Blocker bis zum terminalen Finalreview; frühere Findinglinien bleiben
erhalten und geschlossen.

### Gateidentität und Sicherheitsgrenzen

Der Nachweis vergleicht Status, Grund, ab Zeichenposition null geparste
Regelkennung, Gateart, Fingerprint, sortierten Pfadsatz und Resume-Schritt.
Commit-HEAD-Drift, Slice-Boundary-Drift und Scopeverletzung bleiben trotz
gemeinsamem `GateReason.UNEXPECTED_FILE` unterscheidbar. Präfixlose
Produktionsgates sind eng an Grund und vollständige Detailgrammatik gebunden;
unbekannte Status-/Grundkombinationen scheitern fail-closed.

### Structured-Output-Druck

Die Änderung erweitert den lokalen Validator um die bereits in den
requestspezifischen Writerschemas verwendete `maxLength`-Semantik und ergänzt
eine weiche Promptreserve von 20 Prozent, ohne Findings, Dispositionen,
Reviewevidenz oder Pre-Mortem entfallen zu lassen. Die Inventur wird aus 48
schemaformenden Kontextvarianten abgeleitet, bindet alle begrenzten Stellen
bidirektional und prüft echte Schemafragmente an den Grenzen. Der exakte
Claude-Diagnoseenvelope bleibt transient; Provider-, Typ-, Subtyp- und
Vollständigkeits-Near-Misses bleiben Prozessfehler. Eine konkrete verworfene
Providerausgabe wird weder persistiert noch nachträglich interpretiert.

### Providerfreier Abschlussnachweis

Das Manifest bindet elf Szenario-IDs an eine unabhängige literale Sollmenge
aus Dimension, vollständigem Erwartungstext und eindeutiger parametrisierter
Testidentität. Die drei Korrekturfälle laufen als vollständige Journeys über
`ScriptedWorkflowSession` und `WorkflowEngine`; Happy Path, Record-ahead,
Drift-/Scopegates, Retryklassifikation und direkter Queueabschluss ergänzen
die Abdeckung. Der Bericht rendert deterministisch und trennt Sicherheit,
Verfügbarkeit und Autonomie.

### Plan- und Dokumentationskonsistenz

Der Arbeitsplan kennzeichnet sich ausdrücklich als manueller Plan außerhalb
des `PLAN_ONLY`-Handoffs. Roadmap und Abschlussnachweis behandeln einen realen
Provider-Canary korrekt als nachgelagerte Betriebsbeobachtung statt als Teil
der Repositorysuite. Die Produktivänderungen bleiben eng: ein weicher
Claude-Prompthinweis, lokale `maxLength`-Durchsetzung und der wiederverwendbare
providerfreie Dry-Run-Harness; Workflow-, State-, Record- und Gateproduktion
werden nicht gelockert.

## 5. Finding-Inventur

Die jeweils letzten Claude-Statuszeilen weisen sämtliche im Plan- und
Sliceverlauf eröffneten Findings `C-01` bis `C-17` als `CLOSED` aus. Codex
schließt oder reklassifiziert diese Reviewerbefunde nicht selbst. Im
branchweiten Codex-Abgleich wurde kein neuer ausführbarer Defekt gefunden.

## 6. Validierung

Agent-lokal und providerfrei ausgeführt:

```text
python3 -m pytest tests/ -v
1091 passed in 107.27s

git diff --check master
sauber
```

Diese Werte sind keine Orchestrator-Attestierung. Nur der Orchestrator darf
eine autoritative, fingerprintgebundene Validierungsattestierung erzeugen.

## 7. Restrisiken ohne ausführbaren Befund

Das größte Restrisiko ist die handgepflegte Sollmenge der elf
Resilienzfälle. Manifest, `EXPECTED_SCENARIOS` und `EVIDENCE_ORACLE` sind
untereinander bidirektional gebunden, werden aber nicht maschinell aus dem
Arbeitsplan abgeleitet. Eine spätere Planerweiterung muss daher bewusst an
allen drei Stellen nachvollzogen werden.

Nachgeordnet kann die narrative Abschlussdokumentation gegenüber dem Manifest
driften, weil ihr Wortlaut nicht generiert oder digestgebunden ist. Außerdem
ist die Durable-Ledger-Emulation des Testdrivers absichtlich einfacher als
der append-only Produktionsspeicher; ihre Sicherheit hängt an der
vorgelagerten nativen Writer- und Domänenvalidierung, die in den
Falsifikationsproben tatsächlich auslöst.

## 8. Pre-Mortem und Übergabe

Am wahrscheinlichsten bricht der Nachweis künftig nach einer neuen
Workflowkante oder einem neuen Pflichtszenario: Produktionscode und Plan
werden erweitert, die drei handgepflegten Inventare bleiben jedoch bei elf
Fällen, und die bestehende Suite bleibt vollständig grün. Zweitwahrscheinlich
lockert ein späterer Testdriver-Umbau unbemerkt die native Vertragsvalidierung,
sodass die permissive Ledgeremulation fachlich unzulässige Updates akzeptiert.
Ein realistischer Frühindikator wäre eine Planänderung ohne gleichzeitige
Manifest- und Oracleänderung oder eine Journey-Mutation, die nicht mehr in
`NativeReviewRequestError` beziehungsweise `NativeCodexRequestError` endet.

Der geprüfte Snapshot ist aus Codex-Sicht bereit für das unabhängige
branchweite Claude-Gesamtreview. Die Freigabe bleibt Claude vorbehalten; ein
neuer ausführbarer Defekt ist im Finalreview als `BLOCKER` mit verweigerter
Entscheidung zu behandeln, nicht als neue `OBSERVATION`.

REVIEWER: codex
REVIEW_EVIDENCE: Vollständiger Paketdiff und alle 17 Pfade; Arbeitsplan und drei Slice-Reviewketten; unabhängige Übergangs-, Gate-, Replay-, Mirror- und Checkpointinvarianten; 48 Writerschemakontexte und Feldgrenzen; exakte und Near-Miss-Providerdiagnosen; elf manifestgebundene Resilienzfälle; drei vollständige Korrekturjourneys; Findingstatus C-01 bis C-17; vollständige agent-lokale Repositorysuite mit 1091 bestandenen Tests; sauberer Diffcheck | größtes Restrisiko ist die nicht aus dem Plan abgeleitete handgepflegte Szenario-Sollmenge | realistische Bruchbedingung ist eine neue verpflichtende Workflowkante ohne gleichzeitige Erweiterung von Manifest, EXPECTED_SCENARIOS und EVIDENCE_ORACLE
PRE_MORTEM: Eine neue Pflichtkante wird nur in Plan und Produktionscode ergänzt. Die elf bestehenden Szenarien bleiben gegenseitig konsistent und grün, der Abschlussbericht behauptet weiterhin Vollständigkeit, und die fehlende Kante fällt erst in einem realen Lauf auf.
CODEX_READINESS: READY_FOR_CLAUDE_FINAL_REVIEW
STATUS: DONE

---

## 9. Addendum nach Claudes branchweitem Denial

Claudes Gesamtreview vom 28. August 2026 hat die neuen Blocker `C-18` und
`C-19` eröffnet. Die frühere Reviewbereitschaft dieses Dokuments ist damit
überholt; Codex erteilt weiterhin keine Eigenfreigabe.

Beide Befunde wurden als eng begrenzte Finalkorrektur umgesetzt und im
Slice-03-Review additiv dokumentiert:

- `C-18`: `scope-violation` ist jetzt ein fingerprintgebundenes Nutzergate
  wie sämtliche produktiven `UNEXPECTED-PATH`-Emissionen; Policyart und
  fehlender Fingerprint sind rote Negativkontrollen.
- `C-19`: Die AST-Inventur verfolgt BoolOp-Fallbacks und die exakt aus den
  typisierten Preflight-Denials abgeleitete `error_code`-Menge. Fünfzehn
  bislang fehlende Preflight-/Fallbackidentitäten besitzen Source-Map-Zeilen,
  unabhängige Fälle und Emissionsbindungen. Ein unbekannter BoolOp-Fallback
  sowie eine entfernte Fallbackzeile werden jeweils als unbekannte Regel und
  unklassifiziertes Tripel erkannt.

Der fokussierte Korrekturlauf bestand mit `32 passed in 61.07s`, der breitere
Paketverbund mit `59 passed in 64.41s` und die vollständige Repositorysuite
mit `1092 passed in 121.42s`. Die endgültige Schließung von `C-18` und `C-19`
sowie jede Branchfreigabe bleiben Claude vorbehalten.

FINDING_RESPONSE: C-18 | ACCEPTED | Produktionsförmiges fingerprintgebundenes Scope-Nutzergate und negative Policy-/Fingerprintkontrollen umgesetzt.
FINDING_RESPONSE: C-19 | ACCEPTED | BoolOp- und typisierte error_code-Produzenten, vollständige Source-Map-Zeilen sowie unabhängige Rückrichtungsfalsifikationen umgesetzt.
CODEX_READINESS: READY_FOR_CLAUDE_CORRECTION_REVIEW
STATUS: DONE
