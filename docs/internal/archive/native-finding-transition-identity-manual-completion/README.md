# Work-Unit-gebundene Findingtransition-Identität – manueller Abschluss

## Status

**ORCHESTRATORLAUF NICHT FORTSETZEN / MANUELL ABGESCHLOSSEN**

Der Bootstraplauf auf `feature/native-finding-transition-identity` wurde nach
der erfolgreichen Implementierung, der vollständigen Validierung und Claudes
positiver Slicefreigabe kontrolliert beendet. Der persistierte State-v3-Lauf
`watch-20260823-185613.401370Z-ed88fa6c8a98` bleibt zusammen mit seiner
append-only Recordkette und den Checkpoints unverändert als historische
Evidenz erhalten. Er wird nicht mit `run_task --resume` fortgesetzt.

Der Benutzer hat am 23.08.2026 ausdrücklich auf den ausgefallenen
Antigravity-Slicereview und auf einen zusätzlichen branchweiten Komplettreview
verzichtet und den lokalen Abschlusscommit sowie den Merge nach `master`
autorisiert. Dieser Abschluss erfindet weder eine Antigravity-Freigabe noch
einen terminalen Workflowzustand.

## Fachlicher Abschluss

`ProductionWorkflowDriver._persist_review_finding_transitions()` bindet neue
strukturierte Idempotenzschlüssel für `opened`, `reclassified` und echte
`status_changed`-Transitionen an die autoritative Work Unit. Dabei gelten
folgende Grenzen:

- Identische Wiederholungen derselben Work Unit bleiben No-ops.
- Gleiche Findingtransitionen verschiedener Work Units erhalten getrennte
  Schlüssel.
- Ein vorhandener historischer Schlüssel wird nur bei identischer Work Unit
  und identischer Recordsemantik wiederverwendet.
- Ein historischer Schlüssel einer anderen Work Unit wird nicht übernommen.
- Eine abweichende Bedeutung innerhalb derselben Work Unit hält weiterhin
  fail-closed an.
- Die bestehende Identität für reine `status_rationale`-Revisionen und der
  nicht strukturierte Legacypfad bleiben unverändert.

Die Regressionen decken die drei umgestellten Transitionsarten, die
Upgrade-Grenze, Cross-Work-Unit-Trennung, semantische Konflikte,
Statusbegründungsrevisionen, Reklassifizierung und Replayprojektion ab.

## Review- und Validierungsnachweis

- Implementierungsfingerprint:
  `8cd17fc7cec3bf8aa35af503baf6b17fc0f17adfee844acad763ebbfeb9c0a3c`
- Orchestrator-Attestierung: `1216 passed in 137.94s`
- Claude-Reviewrecord:
  `ar1-8c5ddf986040b096b80b6421699e674f77aec47fa8106c3d0e54b7ab9ba92ea2`
- Claude-Entscheidung: `approved`, keine neuen Findings
- Unabhängige Abschlussvalidierung: `1216 passed in 187.07s`
- Antigravity-Freigabe: `NOT_RECORDED`
- Branchweiter Komplettreview: gemäß Benutzerentscheidung nicht ausgeführt

Antigravity scheiterte nach Claudes Freigabe beim Aufbau seines
read-only Prüflaufs. Der selektive Snapshot enthielt nur die beiden geänderten
Dateien; das Modell versuchte zusätzlich die im Plan erwähnte unveränderte
Abhängigkeit `src/artifact_replay.py` zu lesen. Die Providerhülle beendete den
Versuch deshalb mit `invalid_args`/`no such file or directory`. Dieser Fehler
betrifft die Snapshotgrenze des noch textbasierten Antigravity-Pfads, nicht die
fachliche Implementierung dieses Pakets.

## Archivinhalt

- [Ursprünglicher Auftrag](Phase_2_Bootstrap_Workunitgebundene_Findingtransition_Idempotenz.md)
- [Implementierungs-Handoff](Phase_2_Bootstrap_Workunitgebundene_Findingtransition_Idempotenz-implement.md)
- [Watcher-Sidecar des Auftrags](Phase_2_Bootstrap_Workunitgebundene_Findingtransition_Idempotenz.md.watch.json)
- [Watcher-Sidecar der Implementierung](Phase_2_Bootstrap_Workunitgebundene_Findingtransition_Idempotenz-implement.md.watch.json)
- [Freigegebener Arbeitsplan](phase-2-bootstrap-workunitgebundene-findingtransition-idempotenz-arbeitsplan.md)
- [Unveränderte konsolidierte Laufprojektion](phase-2-bootstrap-workunitgebundene-findingtransition-idempotenz-implement-review-dc4d2724.md)
- [Slicebericht](slice-phase-2-bootstrap-workunitgebundene-findingtransition-idempotenz-arbeits-01-findingtransition-schlussel-an-die-work-unit-binden.md)

Historische Pfadangaben und der verwaltete Freigabestatus in den projizierten
Laufdokumenten werden nicht nachträglich umgeschrieben. `.orchestrator/state.json`,
Records und Checkpoints wurden nicht manuell verändert.
