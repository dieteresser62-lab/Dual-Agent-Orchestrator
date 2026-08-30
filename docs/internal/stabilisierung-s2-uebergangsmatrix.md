# S2 – Übergangs- und Divergenzmatrix

Stand: 2026-08-31. Untersucht ist `structured-v2` auf dem R4-Stand des
Branches `feature/state-authority-consolidation`. Die Matrix beschreibt den
Istzustand; der spätere Authority-Cutover bleibt ausdrücklich aus. Normativ sind
heute die append-only Records zusammen mit dem noch autoritativen
`state-v3`-Mirror. `head.json`, Checkpoints, Audit-Markdown, Request-/Response-
Dateien und Review-Pakete sind Caches beziehungsweise Projektionen.

## Leseschlüssel und Gesamtbefund

- **Record → Mirror** bedeutet: Der Record wird zuerst append-only
  geschrieben; History/State und anschließend Checkpoint folgen. Ein Absturz
  kann deshalb Records voraus, aber nicht regulär den Mirror voraus hinterlassen.
- **Mirror → Baseline-Record** bedeutet: `checkpoint()` übernimmt einen bereits
  im Speicher gebildeten State, schreibt fehlende Baseline-Records und erst
  danach State/Checkpoint. Auch hier ist der Record nach dem ersten Append voraus.
- Jeder Append ist durch `run_id`, `record_type`, `idempotency_key`,
  `previous_record_id`, Payload und Fingerprint gebunden. Wiederholung ist nur
  bei semantischer Gleichheit zulässig.
- **entfällt** heißt nur, dass der Record/State-Doppelvergleich nach einem
  Record-Authority-Cutover entfällt. Record-interne Schema-, Ketten- und
  Referenzprüfungen bleiben davon unberührt.
- **wird generisch** bezeichnet genau einen künftigen, recordbasierten
  Cacheintegritätsabgleich: Cache fehlt → neu projizieren; Cache-Digest weicht
  ab → verwerfen und neu projizieren; die Cachekopie besitzt keine Authority.
- **bleibt bewusst** ist für Record-interne Kausalität, unveränderliche
  Provider-Bindungen und irreversible externe Side Effects reserviert.

Die Inventur ergibt 26 Kanten, 33 `mismatch(...)`-Stellen in
`artifact_migration.py`, 22 direkte `ArtifactResumeError`-Stellen dort, 23
`ArtifactBridgeError`-Stellen in Migration, Bridge und Orchestrator sowie fünf
`_recoverable_*`-Prädikate (vier in `artifact_migration.py`, eines für den
finalen Review-Mirror in `orchestrator.py`).

## Matrix

### A01 — Protokollbindung, Recordkette und Replay

1. **Autoritativer Eingaberecord:** Neue R1-Läufe binden zuerst
   `RunIdentity` und `RunProfile`, danach folgt `Task`; ein atomarer
   Finding-Handoff-Import darf als vorbereitender Genesisrecord davorliegen.
   Vor R1 geschriebene Ketten dürfen beide Runrecords auslassen. Erwartet wird
   stets eine lückenlose Kette bis `head_record_id`; vor dem Replay werden
   `ProtocolBinding` und Store-Kette geprüft.
2. **State-/Cachefelder:** `version`, `run_id`, `protocol_binding` sowie
   `head.json`; kein fachlicher State wird bei der Prüfung geschrieben.
3. **Schreibreihenfolge und Crashpunkte:** Recorddatei → rekonstruierbarer
   `head.json` → später State/Checkpoint. Crash nach Recorddatei, vor
   `head.json`, nach `head.json`, vor State und zwischen State und Checkpoint ist
   möglich; `load_chain()` rekonstruiert den Head, eine kaputte/lückenhafte
   Kette bleibt fail-closed.
4. **Idempotenz:** Kettenvalidierung ist read-only. Ein erneuter Append muss den
   identischen Idempotenzschlüssel und dieselbe Payload besitzen.
5. **Recoverable-Sonderfall:** keiner; historische Protokolle, fehlende Records
   und ungültige Ketten sind absichtlich nicht reparierbar.
6. **Semantik-/Protokollversion:** ausschließlich `structured-v2`, Schema 2,
   native Transporte `native-codex-v2` und `native-claude-review-v2`.
7. **Externe Side Effects:** keine während des Resume-Checks.

Bewertung: **bleibt bewusst** — ohne Ketten- und Protokollprüfung wäre die
Authority selbst unbestimmt.

### A02 — Finding-Handoff beim Resume

1. **Autoritativer Eingaberecord:** Quellketten-`FindingHandoffExport` und
   Zielketten-`FindingHandoffImport`; als Folgerecord wird ein `WorkUnit` mit
   `finding_import_record_id` erwartet.
2. **State-/Cachefelder:** `finding_handoff_source_run_id`,
   `finding_handoff_export_record_id`, `approved_plan_commit`, Taskdatei-Digest
   und importierte Finding-Zustände in `runtime_history`.
3. **Schreibreihenfolge und Crashpunkte:** Export-Record → Veröffentlichung der
   Taskdatei/Queuebewegung → Import-Record in der Zielkette → Work-unit/State →
   Checkpoint. Absturz ist nach jedem Pfeil möglich; besonders kritisch sind
   Export ohne Datei, Datei ohne Import und Import ohne Mirror.
4. **Idempotenz:** Export und Import sind content-addressed; Reuse ist nur bei
   identischem Quellhead, Plan-Commit, Transition-Digest, Zielpfad und
   Taskdigest erlaubt.
5. **Recoverable-Sonderfall:** keiner mit `_recoverable_*`; ein ungebundener,
   fehlender oder nicht mehr revalidierbarer Import stoppt.
6. **Semantik-/Protokollversion:** `structured-v2`; Quell- und Zielrun werden
   getrennt vollständig replayed.
7. **Externe Side Effects:** Schreiben/Verschieben der Taskdatei und
   Queuebewegung können zwischen Export und Import liegen.

Bewertung: **bleibt bewusst** — der Run-übergreifende Authority-Transfer und
die externe Taskdatei benötigen eine eigene atomare Grenze.

### A03 — Taskvertrag

1. **Autoritativer Eingaberecord:** genau ein unveränderlicher `Task`-Record;
   der nächste zulässige Baselinerecord ist `Plan` oder `WorkUnit`.
2. **State-/Cachefelder:** `target_branch`, `task_scope_patterns`,
   `task_digest`; außerdem `task_file`, `branch` und `execution_mode`, die der
   Task-Record nicht vollständig trägt.
3. **Schreibreihenfolge und Crashpunkte:** `checkpoint()` appendet den
   `Task`-Record vor Auditprojektion, State und Checkpoint. Crash nach dem
   Append erzeugt Record-vor-Mirror; die Prüfung selbst ist read-only.
4. **Idempotenz:** Schlüssel `task:accepted`; ein zweiter Record oder eine
   abweichende Payload wird abgelehnt.
5. **Recoverable-Sonderfall:** keiner.
6. **Semantik-/Protokollversion:** `TaskPayload` in Schema 2 gegen State 3.
7. **Externe Side Effects:** keine zwischen Task-Record und Mirror außer der
   Auditprojektion.

Bewertung: **entfällt** — nach dem Cutover wird der Taskvertrag nur aus dem
einmaligen Record gelesen; die Eindeutigkeitsprüfung des Records bleibt.

### A04 — Work-unit, Slice, Runde und Finding-Zuordnung

1. **Autoritativer Eingaberecord:** `WorkUnit` oder `CorrectionWorkUnit`;
   Folgerecorde sind Agentresultat, Review, Finding-Transition oder die nächste
   Work-unit-Runde.
2. **State-/Cachefelder:** `work_units[*].work_unit_id/slice_id/kind`,
   `round_number`, `open_findings`, Slice-`scope_paths`, aktueller
   Work-unit-/Slice-Zeiger und `finding_import_record_id`-Ableitung.
3. **Schreibreihenfolge und Crashpunkte:** Work-unit-Record → Engine-History und
   In-memory-State → Baseline/Projektion → State → Checkpoint. Crash nach Record,
   nach History, nach State oder vor Checkpoint ist möglich.
4. **Idempotenz:** Work-unit-Schlüssel bindet ID, Runde, Pfade, Findingmenge und
   Importrecord. Exakt gleiche Wiederholung ist erlaubt, semantisch andere nicht.
5. **Recoverable-Sonderfall:**
   `_recoverable_pending_correction_record` und
   `_recoverable_pending_slice_denial_record` erkennen die zwei erlaubten
   Denial→Correction-/Slice-Work-unit-Fenster.
   `_recoverable_pending_work_record` ist nur deren logisches ODER und eröffnet
   kein drittes oder normales Work-unit-Fenster; alle Pfade verlangen den
   eindeutig gebundenen vorherigen Denial mit `C-`-Findings.
6. **Semantik-/Protokollversion:** `structured-v2`; PLAN/SLICE/CORRECTION werden
   durch Work-unit-Typ und State-`kind` unterschieden.
7. **Externe Side Effects:** Provider kann erst nach dem Work-unit-Checkpoint
   gestartet werden; vor dem nächsten dauerhaften State kann ansonsten die
   Auditprojektion geschrieben sein.

Bewertung: **entfällt** — Runde, Pfade und Findingattribution werden künftig
aus der Recordfolge projiziert; Record-interne Reihenfolge bleibt gültig.

### A05 — Freigegebener Plan

1. **Autoritativer Eingaberecord:** genau ein `Plan`-Record mit
   `approved_plan_commit` und Slices; Folgerecord ist die Implementierungs-
   `WorkUnit` beziehungsweise ein `implementation_handoff`-`Binding`.
2. **State-/Cachefelder:** `work_plan_path`, `approved_plan_commit`,
   `planned_slices`, Slice-Scope und `current_work_unit`.
3. **Schreibreihenfolge und Crashpunkte:** externer Plan-Commit → `Plan`-Record
   → optional Binding/Handoff → State → Checkpoint. Crash nach Git-Commit vor
   Record ist nicht aus Records allein erkennbar; nach Record vor State ist die
   Kette voraus.
4. **Idempotenz:** der Plan ist immutable und muss exakt einmal vorliegen; Pfad,
   Commit und Slice-Tupel sind vollständig gebunden.
5. **Recoverable-Sonderfall:** keiner; fehlender oder mehrfacher Plan ist
   fail-closed.
6. **Semantik-/Protokollversion:** `PlanPayload` Schema 2, State 3.
7. **Externe Side Effects:** Git-Commit des geprüften Plans.

Bewertung: **entfällt** — die Statekopie des Plans wird Projektion; die
Record-Eindeutigkeit und Git-Objektprüfung bleiben bewusst erhalten.

### A06 — Gate-Entscheidungen

1. **Autoritativer Eingaberecord:** `Gate`; erwartet wird je nach Entscheidung
   ein Resume/Work-unit-/Provider-Übergang oder kein weiterer Record.
2. **State-/Cachefelder:** `work_units[*].gate_decisions`; der aktuelle
   `gate.status/reason/detail/fingerprint/paths/resume_step` ist nur teilweise
   im Gate-Record repräsentiert.
3. **Schreibreihenfolge und Crashpunkte:** Entscheidung entsteht im State →
   `persist_gate_decision()` appendet `Gate` → `checkpoint()` persistiert den
   State. Crash vor Append hinterlässt nur flüchtigen State, nach Append einen
   Record-vor-Mirror-Zustand, nach State einen veralteten Checkpoint.
4. **Idempotenz:** abgeleiteter Gate-Schlüssel und Payload machen identische
   Wiederholung zulässig; die Setprojektion erkennt fehlende/zusätzliche
   Entscheidungen.
5. **Recoverable-Sonderfall:** keiner; aktuelle Pending-Gates sind selbst eine
   Recordlücke, siehe F04.
6. **Semantik-/Protokollversion:** `GatePayload` Schema 2 gegen
   `GateDecisionRecord`/`GateRecord` State 3.
7. **Externe Side Effects:** eine Userentscheidung liegt außerhalb des
   Prozesses; der Record muss ihr dauerhafter Beleg sein.

Bewertung: **entfällt** — abgeschlossene Entscheidungen werden aus Records
projiziert. Vor S4 muss der nicht entschiedene Gatezustand recordfähig werden.

### A07 — Finding-Transitionen und importierte Findings

1. **Autoritativer Eingaberecord:** `Review` plus geordnete
   `FindingTransition`-Records oder `FindingHandoffImport`; Folgerecord ist eine
   Codex-Antworttransition, Reviewer-Statusänderung oder Correction-Work-unit.
2. **State-/Cachefelder:** `runtime_history.findings`, `open_findings`,
   `latest_claude_review`, importierte Status und Attributionen.
3. **Schreibreihenfolge und Crashpunkte:** `Review` → jede Finding-Transition
   einzeln → History/State → Checkpoint. Crash zwischen zwei Transitionen oder
   nach der letzten Transition vor dem Mirror ist möglich.
4. **Idempotenz:** jede Transition bindet Finding-ID, Actor, Action, Status,
   Runde und Rationale; Replay ist geordnet und gleiche Append-Wiederholung
   erlaubt.
5. **Recoverable-Sonderfall:** `_recoverable_pending_review_finding_gap`
   akzeptiert ausschließlich die vollständige, zusammenhängende Menge der
   Transitionen eines bereits dauerhaften Reviews vor dessen Mirrorupdate.
6. **Semantik-/Protokollversion:** `FindingTransitionPayload` Schema 2; nur
   Claude darf schließen/reklassifizieren, Codex darf nur antworten.
7. **Externe Side Effects:** Providerreview ist vor `Review` bereits erfolgt;
   danach keine außer Auditprojektion.

Bewertung: **entfällt** — Findingstatus und offene Menge sind reine
Replayprojektionen; Reviewer-Authority bleibt im Transition-Schema.

### A08 — Validierungsanforderung und Attestierung

1. **Autoritativer Eingaberecord:** `ValidationRequest`; nach externer
   Ausführung wird `ValidationAttestation`, danach gegebenenfalls `Binding`
   erwartet.
2. **State-/Cachefelder:** `runtime_history.attestations`, Test-Fingerprint und
   Resultatprojektion. Der Record bewahrt Commandspec, Outcome, Exitcode und
   `output_sha256`. `ValidationAttestation.attestation_id` bleibt im Envelope
   als `ArtifactRecord.logical_id`, der Fingerprint im Recordfingerprint und
   Commandspec/Reihenfolge in `results[*].command` erhalten. Nicht erhalten
   bleiben `ValidationAttestation.output_digest` und die Bytes von
   `ValidationRecord.output`; `ValidationAttestation.summary` ist zwar aus den
   Outcome-Zahlen deterministisch berechenbar, diese Projektion ist aber noch
   nicht als Recordvertrag benannt. Ein Digest rekonstruiert Outputbytes nicht.
3. **Schreibreihenfolge und Crashpunkte:** Request-Record → Testprozess →
   Attestation-Record → History/State → Checkpoint. Crash vor Test, während
   Test, nach Side Effect vor Attestation und nach Attestation vor State sind
   unterscheidbar.
4. **Idempotenz:** Request und Attestation besitzen getrennte Schlüssel;
   Wiederholung einer nicht attestierten Ausführung kann externe Tests erneut
   starten, eine vorhandene identische Attestation wird wiederverwendet.
5. **Recoverable-Sonderfall:** keiner.
6. **Semantik-/Protokollversion:** `ValidationRequestPayload` und
   `ValidationAttestationPayload`, Schema 2.
7. **Externe Side Effects:** Testbefehle können Dateien, Prozesse oder lokale
   Dienste beeinflussen; ihre Ausführung ist nicht durch den Append atomar.

Bewertung: **entfällt** — der Record/State-Mengenvergleich entfällt nach dem
Cutover; zuvor ist die verlustfreie Recordform ein S4-STOP. Die bewusste
Request-vor-Ausführung-vor-Attestation-Kausalität bleibt recordintern.

### A09 — Quota-Pause

1. **Autoritativer Eingaberecord:** fehlgeschlagener terminaler
   `ProviderAttempt`, gefolgt von `QuotaPause`; nächster Record ist ein
   `ResumeCheck` oder neuer `ProviderAttempt`.
2. **State-/Cachefelder:** `invocation_failures[*]`, Gate/Work-unit/Slice-
   WAITING-Zustand, Rolle, Repository-Fingerprint und `resume_at_utc`.
3. **Schreibreihenfolge und Crashpunkte:** terminaler Attempt → Quota-Record →
   Failure/Gate im State → Checkpoint. Crash nach jedem Record kann den Mirror
   zurücklassen; Schedulerzeit kann während eines Crashs verstreichen.
4. **Idempotenz:** Rolle, Fingerprint und Retryzeit bilden die projizierte
   Faktenmenge; gleicher Record ist wiederholbar.
5. **Recoverable-Sonderfall:** keiner; der Setvergleich unterscheidet
   MIRROR_AHEAD von sonstiger Ambiguität.
6. **Semantik-/Protokollversion:** `QuotaPausePayload` Schema 2 gegen State 3.
7. **Externe Side Effects:** Timer/Wakeup außerhalb des Prozesses.

Bewertung: **entfällt** — Pause und Resumezeit werden aus Records projiziert;
die derzeit nur im Failure-State liegenden Diagnosedetails blockieren S4.

### A10 — Transienter Retry

1. **Autoritativer Eingaberecord:** fehlgeschlagener terminaler
   `ProviderAttempt`, danach `TransientRetry`; Folgerecord ist `ResumeCheck`
   oder neuer Attempt.
2. **State-/Cachefelder:** `invocation_failures[*]`, WAITING-Gate, Rolle,
   Fingerprint, Retryzeit und Versuchszähler.
3. **Schreibreihenfolge und Crashpunkte:** terminaler Attempt → Retry-Record →
   State → Checkpoint; ein Wakeup kann zwischen allen Stufen eintreffen.
4. **Idempotenz:** `(role, fingerprint, retry_at, attempt)` ist die verglichene
   Faktenmenge; ein abweichender Versuch ist ein neuer Übergang.
5. **Recoverable-Sonderfall:** keiner.
6. **Semantik-/Protokollversion:** `TransientRetryPayload` Schema 2.
7. **Externe Side Effects:** Timer/Wakeup und möglicherweise erneuter
   Providerstart.

Bewertung: **entfällt** — die Retryfolge ist recordable; vollständige
Failure-/Policydaten müssen vor S4 ergänzt werden.

### A11 — Provider-Inputmessung, Bootstrapcheck und Final-Preflight

1. **Autoritativer Eingaberecord:** `ProviderInputMeasurement`, optional
   `FinalReviewPreflight`; danach `ProviderAttempt(started)` oder ein dauerhafter
   Stop/Gate.
2. **State-/Cachefelder:** `bootstrap_checks` mit Kind, Transition-Fingerprint,
   Provider, Rolle, Operation, Work-unit, semantischem Digest, Entscheidung und
   Fehlercode. `run_final_review_preflight()` vergleicht zusätzlich Records mit
   `state.run_id`, `current_step`, `current_work_unit.kind/id`,
   `task_scope_patterns`, Slice-Pfaden/`commit_ref`, Work-unit-Status und
   Gateentscheidungen/-pfaden.
3. **Schreibreihenfolge und Crashpunkte:** Measurement-Record →
   `bootstrap_checks` in State/Checkpoint → bei Finalreview lokaler Preflight →
   Preflight-Record → erneut State/Checkpoint → Providerstart. Crash nach
   Measurement, nach erster State-Datei, vor/ nach Preflight und vor Provider-
   start ist möglich. Der Preflight ist read-only, aber sein Ergebnis wird vor
   dem Providerstart append-only festgehalten.
4. **Idempotenz:** Measurement und Preflight binden Recordhead,
   Transition-/Input-/Policydigest und Komponentengrößen. Reuse erfordert
   identische Payload und Kontext. `_approved_external_paths()` akzeptiert
   externe Pfade nur bei passendem State-Gate und strukturiertem Gate-/Commit-
   Record mit identischem Fingerprint.
5. **Recoverable-Sonderfall:** keiner; `_persist_bootstrap_state()` schließt die
   Record-vor-State-Lücke durch idempotente Wiederholung. Preflight-Denials wie
   `STATE-TRANSITION-MISMATCH`, `MEASUREMENT-RUN-MISMATCH`,
   `FOREIGN-RUN-RECORD` und `SLICE-BINDING-MISSING` sind keine Recovery-
   Ausnahmen.
6. **Semantik-/Protokollversion:** Schema 2, Providerrollen Codex/Claude;
   Stateprojektion 3.
7. **Externe Side Effects:** lokaler Finalreview-Preflight; Provider wird erst
   nach dessen dauerhafter Freigabe gestartet.

Bewertung: **entfällt** — Bootstrapfakten und Cursor/Slicestatus werden nach
Record-Authority aus einer Quelle projiziert. Record-interne Preflight-
Referenz-, Fingerprint-, Attestierungs- und Agentresultatprüfungen bleiben.

### A12 — ResumeCheck und Vorgängerhead

1. **Autoritativer Eingaberecord:** der unmittelbare Recordvorgänger plus
   `ResumeCheck(expected_head_id, repository_fingerprint, outcome)`; danach
   folgt je nach Outcome Work-unit, Gate oder Providerattempt.
2. **State-/Cachefelder:** aktiver Test-/Repository-Fingerprint, Resume-Gate und
   aktueller Schritt; `expected_head_id` steht nicht im State.
3. **Schreibreihenfolge und Crashpunkte:** Repository messen → ResumeCheck
   append → State/Checkpoint → Side Effect. Crash nach Messung vor Record kann
   neu messen, nach Record vor State muss denselben Vorgänger anerkennen.
4. **Idempotenz:** der Check ist an den direkten Vorgängerhead gebunden; ein
   anderer Head ist kein Retry derselben Operation.
5. **Recoverable-Sonderfall:** keiner.
6. **Semantik-/Protokollversion:** `ResumeCheckPayload` Schema 2.
7. **Externe Side Effects:** Git-Repository kann sich zwischen Messung und
   Fortsetzung extern ändern.

Bewertung: **bleibt bewusst** — die Headbindung schützt die zeitliche Aussage
der Messung und ist kein Mirrorvergleich.

### A13 — Binding-Referenzen

1. **Autoritativer Eingaberecord:** `Binding` referenziert eine gültige
   `ValidationAttestation` und freigebende `Review`-Records; danach Commit-
   Work-unit oder Workflowabschluss.
2. **State-/Cachefelder:** `approved_plan_commit`, Slice-`commit_ref`,
   Implementierungshandoff und abgeleitete Review-/Attestation-IDs.
3. **Schreibreihenfolge und Crashpunkte:** Review/Attestation → externer Commit
   oder Handoffziel → Binding → State → Checkpoint. Crash nach Ziel vor Binding
   ist die wichtigste irreversible Lücke; nach Binding vor State ist Record
   voraus.
4. **Idempotenz:** Bindingart, Ziel, Attestation und sortierte Approval-IDs sind
   immutable; Fingerprint und Work-unit müssen übereinstimmen.
5. **Recoverable-Sonderfall:** keiner; unbekannte, nicht freigegebene oder
   fingerprint-falsche Referenzen sind fail-closed.
6. **Semantik-/Protokollversion:** `BindingPayload` Schema 2.
7. **Externe Side Effects:** Git-Commit oder Publikation eines
   Implementierungshandoffs.

Bewertung: **bleibt bewusst** — referenzielle Integrität zwischen Records ist
auch ohne Mirror eine eigenständige Sicherheitsgrenze.

### A14 — Slice-Commit

1. **Autoritativer Eingaberecord:** gültige Review- und Attestation-Records;
   vor dem Git-Commit steht `SideEffect(intent)`, danach `SideEffect(result)`
   und `Binding(binding_kind=commit)`.
2. **State-/Cachefelder:** `slices[*].commit_ref/status`, Work-unit-Status,
   `completed_side_effects` und aktueller Schritt.
3. **Schreibreihenfolge und Crashpunkte:** Intent mit vorherigem HEAD und
   erwartetem Baum → pfadexakter Git-Commit → Resultat mit Commit-SHA →
   Binding → Engine/State/Checkpoint. Offene Git-Intents werden vor der
   Neuberechnung des veränderten Worktrees über Work-unit und Slice-ID
   aufgefunden; der durch den Commit veränderte HEAD oder Diff kann ihre
   Wiederfindung deshalb nicht verhindern.
4. **Idempotenz:** Bei offenem Intent bedeutet unveränderter vorheriger HEAD
   "nicht erfolgt"; genau ein Childcommit mit demselben erwarteten Baum
   bedeutet "erfolgt". Jede andere Parent-/Baumlage ist unbekannt und stoppt.
5. **Recoverable-Sonderfall:** keiner.
6. **Semantik-/Protokollversion:** `BindingPayload` Schema 2, Git-SHA als Ziel.
7. **Externe Side Effects:** Git-Commit ist dauerhaft, liegt aber zwischen
   seinem autoritativen Intent und Resultat.

Bewertung: **bleibt bewusst** — R4 deckt die irreversible Kante mit exakter
Parent-/Baum-Reconciliation; diese Prüfung bleibt auch nach dem Cutover.

### A15 — Workflowabschluss

1. **Autoritativer Eingaberecord:** finaler Binding-Record;
   `WorkflowCompletion(outcome, final_binding_id)` ist der terminale
   Folgerecord.
2. **State-/Cachefelder:** `current_step=completed`, Slice-/Work-unit-Status und
   terminale History/Watch-Projektion.
3. **Schreibreihenfolge und Crashpunkte:** finaler Binding → Completion-Record
   in Baseline → Auditprojekt → State → Checkpoint → optionale Watch-
   Queuebewegung. Der dazwischen liegende Git-Commit des Auditdokuments ist als
   eigene Kante A16 inventarisiert. Crash ist nach jeder Stufe möglich.
4. **Idempotenz:** terminaler Outcome und final_binding_id sind immutable;
   identischer Completion-Append ist wiederholbar.
5. **Recoverable-Sonderfall:** keiner; ein Mirrorabschluss ohne Record oder ein
   ungültiges Finalbinding stoppt.
6. **Semantik-/Protokollversion:** `WorkflowCompletionPayload` Schema 2.
7. **Externe Side Effects:** der Audit-Git-Commit A16 und danach Watch-
   Task/Ergebnisverschiebung, jeweils erst nach terminaler Persistenz.

Bewertung: **entfällt** — terminaler State wird aus dem Completion-Record
projiziert; die Finalbinding-Prüfung bleibt recordintern.

### A16 — Finaler Git-Commit der verwalteten Auditprojektion

1. **Autoritativer Eingaberecord:** finaler `Binding` und
   `WorkflowCompletion` samt Auditprojektion; `SideEffect(intent/result)` bindet
   den Audit-Commit zusätzlich an vorherigen HEAD und erwarteten Baum.
2. **State-/Cachefelder:** `audit_report_path`, `branch`, projizierter
   Auditinhalt und die redundante Completionliste; die Git-SHA steht im
   Side-effect-Resultat und im zurückgegebenen `WorkflowRunResult.commit_ref`.
3. **Schreibreihenfolge und Crashpunkte:** finaler Review/Checkpoint → Git- und
   Pfadprüfung → erwarteten Baum ohne Staging berechnen → Intent →
   `commit_managed_audit_report()` mit `git add` und `git commit` → Resultat → SHA zurückgeben. Crash nach Commit vor Resultat
   wird über Parent und Baum lokal klassifiziert.
4. **Idempotenz:** unveränderter Vor-HEAD erlaubt die noch nicht erfolgte
   Operation auch im `claude_final_review`-/`completed`-Resume und lässt
   `finalize_audit()` denselben offenen Intent wiederverwenden; exakt erwarteter
   Childbaum wird als erfolgt bestätigt. Fremde HEADs, Parents, Bäume oder
   Stagingpfade stoppen.
5. **Recoverable-Sonderfall:** keiner. Die Git-Funktion restauriert den Index
   nur bei gefangener Exception und unverändertem HEAD; ein harter Prozesscrash
   besitzt keinen `_recoverable_*`-Pfad.
6. **Semantik-/Protokollversion:** Workflow `structured-v2`, Record-Schema 2;
   `SideEffectPayload` wurde additiv ohne Versionshebung aufgenommen.
7. **Externe Side Effects:** `git add` verändert den Index; `git commit -m
   "docs: finalize orchestrator audit"` erzeugt einen irreversiblen Commit und
   bewegt HEAD.

Bewertung: **bleibt bewusst** — R4 implementiert die eigene
Intent-/Resultat-Reconciliation; sie verschwindet nicht im Cacheabgleich.

### B01 — Generischer Append und durable-but-reported-failed

1. **Autoritativer Eingaberecord:** vollständig validierter Recordprefix und zu
   appendender typisierter Record. Ein prozesslokaler, nicht persistierter
   Append-Index leitet Idempotenz, Revision, Record-IDs und Head ausschließlich
   aus diesem Prefix ab; die Recorddateien bleiben autoritativ.
2. **State-/Cachefelder:** `head.json`, der verwerfbare prozesslokale Append-
   Index und aufruferspezifische Stateprojektion. Fehlender oder abweichender
   Head-/Verzeichnisnachweis verwirft den Index und erzwingt einen vollständigen
   Recordscan; niemals werden Records aus Cachewerten repariert.
3. **Schreibreihenfolge und Crashpunkte:** neuen Record gegen den validierten
   Prefix prüfen → Recorddatei via `store.put()` publizieren → publizierte Datei
   einzeln mit Digest, Schema und Storeinvarianten zurücklesen → rollierenden
   Head-Cache aktualisieren → Rückgabe. Bei Exception wird die Kette vollständig
   neu geladen; Crash nach durablem Write vor Rückgabe ist dadurch erkennbar.
4. **Idempotenz:** `_assert_equal()` verlangt vollständige semantische
   Gleichheit mit dem bereits vorhandenen Record; Schlüsselgleichheit allein
   genügt nicht.
5. **Recoverable-Sonderfall:** generische Reconciliation in
   `ArtifactBridge.append`, kein benanntes `_recoverable_*`.
6. **Semantik-/Protokollversion:** alle Schema-2-Recordtypen; keine Schema-,
   Protokoll- oder Registeränderung. Ausdrückliches Laden und Resume behalten
   die vollständige Ketten-, Referenz-, Revisions- und Invariantenvalidierung.
7. **Externe Side Effects:** keine; nur Dateien des Artifact-Stores.

Bewertung: **bleibt bewusst** — dies ist die gemeinsame atomare
Append-Grenze, nicht ein Mirrorvergleich.

### B02 — Finding-Export aus akzeptiertem Replay

1. **Autoritativer Eingaberecord:** Quell-`Plan`, genehmigter `Review`, offene
   `FindingTransition`s und Quellhead; Folgerecord `FindingHandoffExport`.
2. **State-/Cachefelder:** Plan-Commit, Quellrun, Exportrecord-ID, Zieltaskpfad
   und -digest.
3. **Schreibreihenfolge und Crashpunkte:** Quellkette replayen → Exportpayload
   bilden → Export append → Task publizieren. Crash vor Append ist folgenlos;
   nach Append vor Publikation ist per Export wiederaufnehmbar.
4. **Idempotenz:** Export bindet Head, Planfreigabe, Transition-IDs/-Digest und
   Zielbytes.
5. **Recoverable-Sonderfall:** keiner.
6. **Semantik-/Protokollversion:** `FindingHandoffExportPayload` Schema 2.
7. **Externe Side Effects:** Zieltaskdatei/Queue nach dem Record.

Bewertung: **bleibt bewusst** — die Exportprüfung belegt, dass keine
ausgedachten oder veralteten Findings den Run verlassen.

### B03 — Finding-Import gegen Export und Taskbytes

1. **Autoritativer Eingaberecord:** akzeptierter Quell-Export und dessen
   referenzierte Transitionen; Folgerecord `FindingHandoffImport` im Zielrun.
2. **State-/Cachefelder:** Handoff-IDs, Taskdigest, importierte Findings und
   Work-unit-Importbindung.
3. **Schreibreihenfolge und Crashpunkte:** Taskbytes lesen → Quellkette prüfen →
   Import append → Zielstate/checkpoint. Änderung der Datei vor dem Append oder
   Crash nach Import vor State wird erkannt.
4. **Idempotenz:** Exportrecord-ID, Zielrun, Taskdigest und Transition-Digest
   binden den Import eindeutig.
5. **Recoverable-Sonderfall:** keiner; Source-Revalidation ist streng.
6. **Semantik-/Protokollversion:** `FindingHandoffImportPayload` Schema 2.
7. **Externe Side Effects:** Lesen einer extern veröffentlichten Taskdatei;
   Queueübernahme kann bereits erfolgt sein.

Bewertung: **bleibt bewusst** — Cross-run-Replay und Bytebindung sind eine
eigene Trust Boundary.

### B04 — Providerattempt Start und Terminalrecord

1. **Autoritativer Eingaberecord:** erlaubende `ProviderInputMeasurement` und
   optional `FinalReviewPreflight`; `SideEffect(intent)` steht vor
   `ProviderAttempt(started)`, die dauerhaft persistierte Antwort/der terminale
   Fehler vor `SideEffect(result)`.
2. **State-/Cachefelder:** Agentprofil, Invocation-Failure, Quota/Retry-Gate,
   Nutzung und Ergebnis-Request-/Responsebindung.
3. **Schreibreihenfolge und Crashpunkte:** Intent → started-Record →
   Providerprozess → dauerhafte Rohantwort → Agentresultat/Review → terminaler
   Attempt → Resultat → State/Checkpoint.
4. **Idempotenz:** logische Operation, Inputdigest, Binding, Runde,
   Attemptnummer und der daraus deterministisch gebildete, versuchsspezifische
   Antwortpfad bilden den stabilen Schlüssel. Nur die Antwortdatei desselben
   Attempts oder dessen terminaler Fehler beweist "erfolgt"; eine Antwort
   eines früheren Attempts ist kein Beleg. Fehlt beides hinter einem
   `started`-Record, ist der Remotezustand unbekannt und ein zweiter Start
   verboten. Fehlt auch der `started`-Record, beweist das Recordpräfix, dass
   der Prozess noch nicht gestartet wurde und derselbe Intent darf ausgeführt
   werden.
5. **Recoverable-Sonderfall:** kein `_recoverable_*`; durable Terminalrecords
   werden bei Wiederholung auf vollständige Ergebnisgleichheit geprüft.
6. **Semantik-/Protokollversion:** `ProviderAttemptPayload` Schema 2 und im
   Attempt gebundenes Modell/Effort.
7. **Externe Side Effects:** Providerstart ist kostenpflichtig und kann nicht
   zurückgerollt werden.

Bewertung: **bleibt bewusst** — der unbekannte Remotezustand stoppt
fail-closed; Reconciliation startet niemals probeweise einen zweiten Provider.

### B05 — Native Codex Request, Rohantwort und AgentResult

1. **Autoritativer Eingaberecord:** Work-unit und content-addressed
   `native-agent-codex-request-v2`; Folgerecord ist ein validiertes
   `AgentResult`, danach Finding-Antworttransitionen.
2. **State-/Cachefelder:** `runtime_history.codex_*`, Testdateien, aktive
   Fingerprints; Requestbundle und Rohantwort liegen als Digest-gebundene
   Cachedateien vor.
3. **Schreibreihenfolge und Crashpunkte:** Requestcache → started Attempt →
   Provider → Rohantwortcache → terminaler Attempt → AgentResult → Finding-
   Transitionen → State/Checkpoint. Jeder Zwischenpunkt ist ein eigener
   Recoveryfall; besonders Antwort ohne AgentResult und AgentResult ohne
   Mirror.
4. **Idempotenz:** Request-ID ist Hash der kanonischen Requestbytes;
   AgentResult bindet Request-ID, Response-SHA und logische Operation. Doppelte
   Resultrecords müssen identisch sein.
5. **Recoverable-Sonderfall:** kein `_recoverable_*`; die
   `recover_pending_native_codex()`-Pfade rekonstruieren ausschließlich aus
   gebundenen Cachebytes und Records.
6. **Semantik-/Protokollversion:** `native-agent-codex-request-v2`,
   `native-agent-codex-result-v2`, Transport `native-codex-v2`, Record-Schema 2.
7. **Externe Side Effects:** Provideraufruf; die Agentantwort kann außerdem
   bereits Workspaceänderungen verursacht haben.

Bewertung: **wird generisch** — Request-/Responsecache wird aus Digestbindung
validiert oder verworfen; der AgentResult-Record bleibt Authority.

### B06 — Native Claude Request, Review und Finding-Transitionen

1. **Autoritativer Eingaberecord:** kanonischer
   `native-agent-review-request-v2`; Folgerecord `Review` und dessen
   `FindingTransition`s.
2. **State-/Cachefelder:** `latest_claude_review`, Reviewzähler, Findings,
   `last_claude_fingerprint`; Reviewpaket und Rohantwort sind Cachedateien.
3. **Schreibreihenfolge und Crashpunkte:** Reviewpacketcache → started Attempt
   → Provider → Rohantwort → terminaler Attempt → Review → Transitionen →
   State/Checkpoint. Crash zwischen Review und Transitionen sowie danach vor
   State ist ausdrücklich möglich.
4. **Idempotenz:** Request-ID/Response-SHA, Work-unit und Verdict sind gebunden;
   Recovery vergleicht neu gebauten Request und durable Entscheidung.
5. **Recoverable-Sonderfall:** `_recoverable_pending_review_finding_gap` für die
   Transitionen. `_recoverable_final_denial_mirror_gap` toleriert je fehlender
   Review-Signatur exakt ein dauerhaftes Final-Denial-Record vor dem History-
   Mirror; mehrere verschiedene Signaturen können gleichzeitig fehlen, sofern
   jede über `_persisted_histories()` und
   `_historical_correction_attribution_matches()` einzeln gebunden ist.
6. **Semantik-/Protokollversion:** `native-agent-review-request-v2`,
   `native-agent-review-result-v2`, Transport `native-claude-review-v2`.
7. **Externe Side Effects:** Claude-Provideraufruf; Review ist read-only zum
   Repository, aber kostenpflichtig.

Bewertung: **wird generisch** — Paket/Rohantwort werden generischer Cache;
Review und Transitionen bleiben alleinige Authority.

### B07 — Review-/Finding-/Attestation-Projektionen zur Laufzeit

1. **Autoritativer Eingaberecord:** akzeptierte `Review`,
   `FindingTransition`, `FindingHandoffImport` und
   `ValidationAttestation`; Folgerecord richtet sich nach dem Workflowstep.
2. **State-/Cachefelder:** vollständige `runtime_history`, `open_findings`,
   `latest_claude_review` und Carry-forward-Projektion.
3. **Schreibreihenfolge und Crashpunkte:** Records → In-memory-History → State →
   Checkpoint. Die Funktionen `assert_structured_decision_context()`,
   `authoritative_native_findings()` und `carry_forward_native_findings()`
   vergleichen Replay und Mirror vor weiterer Ausführung.
4. **Idempotenz:** Projektion ist deterministisch aus demselben Recordpräfix;
   Wiederholung muss byte-/semantikgleich sein.
5. **Recoverable-Sonderfall:** `_recoverable_final_denial_mirror_gap`; ansonsten
   keine Differenztoleranz.
6. **Semantik-/Protokollversion:** `structured-v2` Record-Schema 2, Historyshape
   des State 3.
7. **Externe Side Effects:** keine; bei Gleichheit darf erst danach ein Provider
   oder Commit folgen.

Bewertung: **entfällt** — nach Record-Authority wird die History direkt
projiziert statt mit einer zweiten fachlichen Quelle verglichen.

### B08 — Implementierungshandoff und Taskpublikation

1. **Autoritativer Eingaberecord:** Planfreigabe, Validation und Review;
   Folgerecord `Binding(binding_kind=implementation_handoff)` beziehungsweise
   Finding-Export.
2. **State-/Cachefelder:** Work-unit, Plancommit, Handoff-/Export-IDs und
   Zieltaskdigest.
3. **Schreibreihenfolge und Crashpunkte:** Binding/Export → Datei-Intent →
   Taskdatei → Datei-Resultat → Queue-Intent → Move → Queue-Resultat →
   Zielimport. Jeder Zwischenpunkt ist replaybar.
4. **Idempotenz:** Datei fehlt = nachweislich nicht geschrieben, identischer
   Inhaltsdigest = erfolgt, abweichender Inhalt = unbekannt/Stop. Bei Queue gilt
   analog: exakte Quelle allein = nicht bewegt, exaktes Ziel allein = bewegt,
   beide oder keines = unbekannt/Stop.
5. **Recoverable-Sonderfall:** keiner; `prepare_finding_handoff()` akzeptiert
   einen vorhandenen Export nur bei identischen vorbereiteten Taskbytes.
6. **Semantik-/Protokollversion:** Binding/Handoff Schema 2; Taskformat ist über
   SHA-256 gebunden.
7. **Externe Side Effects:** Dateischreiben und Queuebewegung.

Bewertung: **bleibt bewusst** — R4 liefert getrennte Datei- und
Source-/Destination-Reconciliation mit Intent/Resultat.

### B09 — Checkpoint-Dualwrite und Auditprojektion

1. **Autoritativer Eingaberecord:** aktueller Recordhead; als Folgezustände
   entstehen Auditprojektion, `.orchestrator/state.json` und Checkpointdatei,
   aber keine neuen fachlichen Records außer den zuvor ergänzten Baselines.
2. **State-/Cachefelder:** gesamter `WorkflowState`, `runtime_history`,
   `audit_report_path`, Checkpoint-Cursor und Audit-Markdown.
3. **Schreibreihenfolge und Crashpunkte:** `_bind_artifact_store()` → Baseline
   inklusive Ledgerinitialisierung → Auditprojektion → Datei-Intent → State →
   Datei-Resultat → Datei-Intent → Checkpoint → Datei-Resultat. Projektions-
   Side-effects verwenden die Work-unit `projection` und werden nicht zu einem
   fachlichen Mirrorfakt aufgewertet. Jeder überschreibende Projektionsintent
   bindet Ziel, Vorher-Digest, Soll-Digest und die exakten Sollbytes, bevor der
   atomare Replace beginnt.
4. **Idempotenz:** Baseline-Append ist record-idempotent; Projektionen müssen
   aus demselben Präfix deterministisch überschreibbar sein. State und
   Checkpoint besitzen heute noch getrennte Gleichheitsanforderungen. Die
   atomaren Textschreiber verwenden `newline=""`, sodass Intentdigest und
   physische Bytes auch unter Windows identisch bleiben. Stimmt das Ziel beim
   Resume noch mit dem gebundenen Vorher-Digest überein, werden ausschließlich
   die im Intent persistierten Bytes geschrieben; stimmt es mit dem Soll-Digest
   überein, wird nur das Resultat ergänzt. Jede dritte Lage stoppt fail-closed.
5. **Recoverable-Sonderfall:** die vier Migration-Prädikate und der finale
   Denial-Prädikat decken nur bestimmte Record-vor-Mirror-Fenster, nicht das
   allgemeine Dualwrite.
6. **Semantik-/Protokollversion:** Records Schema 2; State/Checkpoint Version 3.
7. **Externe Side Effects:** State- und Checkpointdatei sind an Vorherzustand
   und Sollbytes gebunden; fehlendes Ziel ist nur bei gebundenem `absent`
   nicht erfolgt, identisches Sollziel ist erfolgt, identischer Vorherzustand
   ist exakt nachholbar und jede Abweichung unbekannt/Stop. Symlinks und andere
   nicht reguläre Dateitypen werden nie als dauerhaftes Ergebnis akzeptiert.
   Das Auditdokument bleibt Projektion.

Bewertung: **wird generisch** — alle drei Dateien werden Cacheprojektionen mit
einem gemeinsamen Head/Digest-Abgleich und atomarer Neuprojektion.

### B10 — Commit-/Completion-Projektion in Watch und Ergebnisbindung

1. **Autoritativer Eingaberecord:** Commit-/Final-`Binding` und
   `WorkflowCompletion`; danach wird ein Watch-Ergebnis publiziert.
2. **State-/Cachefelder:** terminaler Workflowstate, `run_id`, Taskdigest,
   Protokollmodus, Watch-Taskidentität, Quell-/Zielpfad, Evidence-Digest und
   Outboxziel. Verglichen werden Workflowresultat ↔ Watchidentity ↔
   Success-/Rejection-Evidence ↔ tatsächliche Task-/Zieldateibytes.
3. **Schreibreihenfolge und Crashpunkte:** terminale Records → State/Checkpoint
   → Watch-Evidence → Queue-Intent → Inbox nach gebundenem Outboxziel bewegen →
   Queue-Resultat → Markerbereinigung.
4. **Idempotenz:** Run-/Taskidentität, Protokollmodus, Taskdigest,
   Evidence-Digest und Zielpfad müssen übereinstimmen; Quelle und Ziel dürfen
   nie gleichzeitig oder beide nicht existieren. Der stabile Queue-`effect_key`
   wird ausschließlich aus kanonischer Quelle und Taskdigest gebildet; der
   zeitgestempelte Zielpfad ist gebundener Operationsparameter, aber kein
   Schlüsselbestandteil. Ein vorhandenes Ergebnis darf nur identisch
   wiederverwendet werden.
5. **Recoverable-Sonderfall:** kein `_recoverable_*`; direkte Queue-Recovery
   liest denselben Ledgerintent und dieselbe Source-/Destination-Regel. Eine
   technische Ablehnung vor der ersten Workflowbaseline initialisiert vor der
   Poison-Bewegung einen minimalen Ledgerprefix. Ist die Recordkette selbst
   korrupt, verwendet die irreversible Quarantäne einen aus Run-ID, Taskdigest
   und Quellname deterministisch herleitbaren Zielpfad, damit der Watcher nicht
   in einer Endlosschleife bleibt; die korrupte Kette wird dabei weder gelesen
   noch repariert. Weil eine korrupte Authority kein weiteres autoritatives
   Record sicher aufnehmen kann, ist diese Quarantäne ausdrücklich kein
   abgeschlossenes strukturiertes Side Effect: Die gebundene Poison-Diagnose
   dokumentiert den Abbruch, während die deterministische Bewegung ausschließlich
   der sicheren Isolierung dient.
6. **Semantik-/Protokollversion:** terminales Record-Schema 2; Watchprojektion
   ist Cache/Transport, nicht Authority.
7. **Externe Side Effects:** Ergebnisdatei und Queueverschiebung.

Bewertung: **wird generisch** — terminale Dateien werden aus dem gebundenen
Recordpräfix projiziert; Queue-Move und seine Source-/Destination-Reconciliation
bleiben als externer Side Effect separat.

## Vollständiger Fehlerstellen-Katalog

Die folgenden Meldungsstämme sind nicht die Quelle der Inventur, sondern ein
prüfbarer Index auf jede aktuelle Raise-Stelle. Mehrere Stämme gehören bewusst
zu derselben semantischen Kante.

### 33 Migration-`mismatch(...)`-Stellen

| Meldungsstamm | Kante |
|---|---|
| `expected exactly one task record, found` | A03 |
| `task contract differs from state-v3` | A03 |
| `work-unit record has no state-v3 counterpart` | A04 |
| `work-unit round, slice, or path allowlist differs` | A04 |
| `correction work-unit finding attribution differs from state-v3` | A04 |
| `work-unit finding import binding differs from state-v3` | A04/A02 |
| `latest work-unit path allowlist differs from state-v3` | A04 |
| `latest work-unit round differs from state-v3` | A04 |
| `latest work-unit finding state differs from state-v3` | A04/A07 |
| `latest correction finding attribution differs from state-v3` | A04/A07 |
| `current work unit has no structured record` | A04 |
| `current work-unit round differs from the record chain` | A04 |
| `approved plan has no structured record` | A05 |
| `expected exactly one immutable approved-plan record, found` | A05 |
| `state-v3 mirror is missing its approved-plan commit binding` | A05 |
| `approved-plan binding differs from state-v3` | A05 |
| `gate decisions differ from state-v3` | A06 |
| `finding transitions differ from state-v3` | A07 |
| `finding status differs from state-v3` | A07 |
| `imported finding status differs from state-v3` | A07/A02 |
| `validation attestations differ from state-v3` | A08 |
| `quota pauses differ from state-v3` | A09 |
| `transient retries differ from state-v3` | A10 |
| `bootstrap checks differ from state-v3` | A11 |
| `bootstrap check payload differs from state-v3` | A11 |
| `resume check is not bound to its prior record head` | A12 |
| `binding references an unknown, invalid, or fingerprint-mismatched validation attestation` | A13 |
| `binding references an unknown, unapproved, or fingerprint-mismatched review` | A13 |
| `commit binding has no state-v3 counterpart` | A14 |
| `completed slice is missing a structured commit binding` | A14 |
| `state-v3 mirror reports workflow completion without a structured record` | A15 |
| `workflow completion differs from state-v3` | A15 |
| `workflow completion references an unknown, invalid, or fingerprint-mismatched final binding` | A15/A13 |

Damit sind die sechzehn wörtlichen Vorkommen von `differs from state-v3`
abgedeckt: Runidentität; Runprofil; Workflowcursor; Task; Correction-Attribution;
Work-unit-Importbindung; letzter Work-unit-Scope; letzte Runde;
letzter Findingzustand; letzte Correction-Attribution; Planbindung;
Findingstatus; importierter Findingstatus; Bootstrap-Payload; Completion sowie
die Export-Planbindung in A02/B03. Die grammatisch plurale R3-Meldung
`slice boundaries differ from state-v3` ist zusätzlich in der direkten
Resume-Fehlerliste gebunden.

### 22 direkte `ArtifactResumeError`-Stellen

| Meldungsstamm | Kante |
|---|---|
| `run identity differs from state-v3` | A01/A03 |
| `run profile differs from state-v3` | A01/B04 |
| `structured-v2 run has no workflow transition prefix` | R2 Cursor-/Statuspräfix fehlt; fail-closed vor Resume |
| `structured-v2 run has no workflow policy prefix` | R2 Policypräfix fehlt; fail-closed vor Resume |
| `workflow cursor differs from state-v3` | R2 Cursor (`slice_id`, `work_unit_id`, `step`) |
| `slice statuses differ from state-v3` | R2 Slice-Statusprojektion |
| `work-unit statuses or steps differ from state-v3` | R2 Work-unit-Status-/Stepprojektion |
| `workflow policies differ from state-v3` | R2 Returncount-/Limitprojektion |
| `structured-v2 run has no slice boundary prefix` | R3 Slicegrenzenpräfix fehlt für einen bereits gebundenen Slice; fail-closed vor Resume |
| `slice boundaries differ from state-v3` | R3 Start-Commit, exakte Scopegruppen und gemessener Startfingerprint |
| `structured-v2 run has no unique initialized side-effect ledger` | R4 Ledgerpräfix fehlt oder ist mehrdeutig; keine Nachrüstung aus dem Mirror |
| `completed side effects differ from the authoritative ledger` | R4 Mirror ist der Resultatfolge voraus oder kein exakter Präfix; ein reines Record-vor-Mirror-Suffix wird deterministisch projiziert |
| `is historical and cannot be resumed` | A01 |
| `structured-v2 state lacks the complete native Codex-Claude transport binding` | A01 |
| `record chain for run` / `is invalid` | A01 |
| `has no records; restore its record chain` | A01 |
| `exc.diagnostic.message` (Replaydiagnose) | A01 |
| `finding handoff mirror is incomplete` | A02 |
| `record chain contains an unbound finding import` | A02 |
| `finding handoff requires exactly one import, found` | A02 |
| `finding handoff source is no longer valid` | A02 |
| `finding import differs from its revalidated source` | A02/B03 |

### 24 `ArtifactBridgeError`-Stellen

| Meldungsstamm | Kante |
|---|---|
| `referenced source record is not a finding export` | A02/B03 |
| `source export plan commit differs from state-v3` | A02 |
| `finding export requires a non-empty accepted replay` | B02 |
| `finding export requires its approved reviewer record` | B02 |
| `finding export plan commit is not present in accepted replay` | B02 |
| `finding export requires at least one source transition` | B02 |
| `finding import requires a finding handoff export record` | B03 |
| `finding export record is not in the accepted source replay` | B03 |
| `finding export record belongs to another source run` | B03 |
| `finding export differs from its accepted source replay` | B03 |
| `finding import task bytes differ from the export binding` | B03 |
| `structured artifact differs semantically from the state-v3 statement` | B01 |
| `side effect result has no authoritative intent` | A14/A16/B04/B08/B09/B10 |
| `side effect result changed its immutable intent binding` | A14/A16/B04/B08/B09/B10 |
| `provider attempt measurement is not in the accepted chain` | B04 |
| `provider attempt work unit differs from its measurement` | B04 |
| `provider attempt operation instance must be non-empty` | B04 |
| `provider attempt immutable binding differs from its first attempt` | B04 |
| `provider attempt requires one terminal direct predecessor` | B04 |
| `provider attempt start is not in the accepted chain` | B04 |
| `provider attempt terminal requires a started record` | B04 |
| `provider attempt terminal differs from its durable result` | B04 |
| `referenced finding export record is missing` | B03/B08 |
| `finding export plan commit differs from the task` | B03/B08 |

### Laufzeitvergleiche außerhalb der Migration

| Meldungsstamm/Prüfung | Kante |
|---|---|
| `structured reviewer decisions differ from the state-v3 mirror` | B07 |
| `structured commit review record differs from its state-v3 mirror` | B07 |
| `structured commit attestation record differs from its state-v3 mirror` | B07 |
| `provider attempt measurement context diverged` | A11/B04 |
| `authoritative finding replay differs from the state-v3 mirror` | B07 |
| `record-native finding carry-forward differs from the state-v3 mirror` | B07 |
| `native agent request differs from its persisted recovery artifact` | B05 |
| `native Codex raw response differs from its persisted artifact` | B05 |
| `content-addressed review packet cache differs from canonical bytes` | B06 |
| `native agent recovery has divergent agent-result records` | B05 |
| `native agent recovery result idempotency binding differs` | B05 |
| `native implementer recovery raw response digest differs from its record` | B05 |
| `native Codex recovery record differs from its durable binding` | B05 |
| `native Codex recovery result differs from its durable record` | B05 |
| `native reviewer recovery record differs from the rebuilt request` | B06 |
| `native reviewer recovery result differs from its decision record` | B06 |
| `pre-policy native reviewer result differs from its decision record` | B06 |
| `native agent result logical binding differs` | B05 |
| `persisted finding handoff export differs from the prepared task` | B08 |
| `file side-effect target differs before result completion` | B05/B08/B09; Zieltyp/-digest unmittelbar vor Datei-Resultat, **bleibt bewusst** |
| `projection target differs before result completion` | B09; State-/Checkpointbytes unmittelbar vor Datei-Resultat, **bleibt bewusst** |
| `structured audit dual-write mismatch` | B09 |
| `workflow history review packet cache differs from canonical bytes` | B06/B09 |
| `differs from the immutable persisted profile` | A01/B04; Runtimeprofil ↔ persistierte ProtocolBinding, **bleibt bewusst** |
| `persisted task identity differs from --resume task` | A03; State/Recordbindung ↔ Taskdatei, **wird generisch** |
| `persisted task contract differs from --resume task` | A03; State/Recordvertrag ↔ Taskdatei, **wird generisch** |
| `persisted watch run identity differs from inbox task` | A02/B10; Watchidentity ↔ Inbox-Task, **wird generisch** |

Die beiden Vorkommen von `structured audit dual-write mismatch` liegen in
`checkpoint()` und `_project_audit()`; der danach gelistete Workflow-History-
Stamm liegt einmal in `WorkflowHistory.from_dict()`. Doppelte
Request-/Raw-response-Stämme bezeichnen jeweils
"Datei existiert" und "Race nach Create" und bleiben im Quellzähler getrennt.

### Finalreview-Preflight: Record↔State- und Record-intern

`src/final_review_preflight.py` wirft an diesen Stellen keine Exception,
sondern liefert typisierte `_deny(...)`-Resultate. Deshalb ist die Fläche
zusätzlich über AST-Vergleichszahlen und vollständige Funktionsdigests im
S2-Test gebunden.

| Denial/Prüfung | Vergleich | Kante/Bewertung |
|---|---|---|
| `MEASUREMENT-TYPE` | Eingaberecord ↔ erforderlicher `ProviderInputMeasurementPayload` | A11, **bleibt bewusst** |
| `OPERATION-NOT-FINAL` | Measurement-Operation ↔ geschlossene Finalreview-Operationsmenge | A11, **bleibt bewusst** |
| `STATE-TRANSITION-MISMATCH` | Measurement-Operation ↔ `state.current_step` und `current_work_unit.kind` | A11, **entfällt** als Doppelquelle |
| `MEASUREMENT-RUN-MISMATCH` | Measurement-Work-unit/Run ↔ `current_work_unit_id`/`state.run_id` | A11, **entfällt** |
| `MEASUREMENT-DENIED` | dauerhafte Measurement-Entscheidung ↔ Erlaubnis zum irreversiblen Providerstart | A11/B04, **bleibt bewusst** |
| `FOREIGN-RUN-RECORD` | jeder Record-Run ↔ `state.run_id` | A11, **entfällt**; Single-run-Kettenprüfung bleibt |
| `UNAUTHORIZED-PATH` | Repositorypfade ↔ State-Task-/Slice-Scope plus State-Gatepfade und Gate-/Binding-Records | A11, **entfällt** erst nach recordfähigem Scope/Gate |
| `SLICE-BINDING-MISSING` | `state.slices[*].commit_ref`/Status ↔ erwartete vollständige Commitbindung | A11/A14, **entfällt** als Mirrorprüfung |
| `_approved_external_paths()` | State-Work-unit-/Slice-Status, Commitref und Gateentscheidung ↔ `Gate`-/Commit-`Binding`-Records | A11/A06/A14, **entfällt** erst nach S4-Stopauflösung |
| `MISSING-REFERENCE`, `FINGERPRINT-MISMATCH` | Binding ↔ typisierte frühere Review-/Attestation-Records | A13, **bleibt bewusst** |
| `PREMATURE-COMPLETION`, `ATTESTATION-MISSING`, `ATTESTATION-FAILED`, `CODEX-FINAL-RESULT-MISSING` | Recordfolge und Fingerprints untereinander | A11/A13/A15, **bleibt bewusst** |

### Watch-/Queue-Cachevergleiche

Diese Vergleiche sind keine zweite fachliche Recordauthority, aber sie liegen
zwischen terminalem Recordpräfix und irreversibler Queuebewegung. B10 führt sie
deshalb vollständig als Cache-/Side-effect-Reconciliation; der Test zählt die
Vergleichsausdrücke in `QueueSuccessEvidence.__post_init__()`,
`load_rejection_marker()`, `load_watch_identity()`,
`load_queue_success_evidence()`, `finalize_queue_success()` und
`watch_inbox()` sowie die terminalen Grenzen in `run_pipeline()` und bindet
folgende Meldungsstämme:

| Meldungsstamm | Vergleich | Bewertung |
|---|---|---|
| `queue success evidence binding digest differs` | Evidencefelder ↔ eigener Digest | **wird generisch** |
| `rejection marker task digest differs` | Rejection-Evidence ↔ Taskbytes | **wird generisch** |
| `rejection marker evidence digest differs` | Rejection-Evidence ↔ eigener Digest | **wird generisch** |
| `watch identity task digest differs from queue task` | Watchidentity ↔ Taskbytes | **wird generisch** |
| `queue success evidence source binding differs` | Evidence-Quelle ↔ kanonischer Inboxpfad | **wird generisch** |
| `queue success evidence run id differs` | Evidence ↔ erwarteter terminaler Run | **wird generisch** |
| `queue success evidence task digest differs` | Evidence ↔ erwarteter Taskdigest | **wird generisch** |
| `queue success evidence protocol mode differs` | Evidence ↔ erwartetes Protokoll | **wird generisch** |
| `queue success evidence differs from watch identity` | Evidence ↔ Watchidentity | **wird generisch** |
| `terminal workflow result differs from watch identity` | Workflowresultat ↔ Watchidentity | **wird generisch** |
| `queue source digest differs from success evidence` | Quellbytes ↔ Evidence vor Move | **bleibt bewusst** an der Side-effect-Grenze |
| `bound queue destination digest differs from success evidence` | Zielbytes ↔ Evidence nach Move | **bleibt bewusst** an der Side-effect-Grenze |
| `bound queue destination differs before ledger completion` | Zieltyp und Zielbytes ↔ Evidence unmittelbar vor dem Queue-Resultatrecord | **bleibt bewusst**; verhindert ein autoritatives Resultat vor physischer Feststellung |
| `Workflow result run id %s differs from persisted watch identity %s for %s.` | Prozessresultat ↔ persistierte Watchidentity | **wird generisch** |
| `Workflow protocol mode %s differs from persisted watch identity %s for %s.` | Prozessresultat ↔ persistierte Watchidentity | **wird generisch** |
| `bound success evidence differs from terminal workflow state` | direkte Queue-Recovery: Success-Evidence ↔ persistierter terminaler State | **wird generisch** |
| `ledgered queue destination differs from the task binding` | R4 Queue-Resultat ↔ exakt gebundene Outboxdatei und Taskdigest | **bleibt bewusst** |
| `ledgered queue destination differs before result completion` | Fehlgeschlagene/Poison-Queuebewegung ↔ Zieltyp und Digest vor ihrem Resultatrecord | **bleibt bewusst**; verhindert einen falschen abgeschlossenen Ledgerzustand |
| `Terminal workflow result differs from bound watch task identity` | neuer Terminalabschluss ↔ gebundene Watch-Taskidentität vor Publikation | **wird generisch** |

### `_recoverable_*`-Inventar

| Funktion | Exakte erlaubte Lücke | Kante | Bewertung |
|---|---|---|---|
| `_recoverable_pending_review_finding_gap` | Review und vollständiger Transitionblock liegen vor dem State | A07/B06 | **entfällt** |
| `_recoverable_pending_correction_record` | genau nächste Correction-Work-unit liegt vor dem State | A04 | **entfällt** |
| `_recoverable_pending_slice_denial_record` | genau nächste Slice-Denial-Work-unit liegt vor dem State | A04 | **entfällt** |
| `_recoverable_pending_work_record` | reiner ODER-Wrapper über die beiden vorigen Denial-Fenster; keine normale oder dritte Work-unit-Lücke | A04 | **entfällt** |
| `_recoverable_final_denial_mirror_gap` | pro fehlender Signatur exakt ein Final-Denial-Review liegt vor History; mehrere unterschiedliche Signaturen sind zugleich zulässig, wenn jede die History-, Attestierungs- und Correction-Attribution erfüllt | B06/B07 | **entfällt** |

Nach dem Cutover werden diese Lücken nicht mehr "toleriert": Der Recordpräfix
ist einfach der Eingabestand, aus dem die Projektion neu entsteht.

`assert_run_binding_mirror()` prüft unabhängig von den Recovery-Lücken frühe
Runidentity-/Profilrecords gegen ihren Mirror und ist mit Vergleichszahl und
vollständigem Funktionsdigest eingefroren.

`require_workflow_status_prefix()` weist Vor-R2-Ketten ohne vollständigen
Transition-/Policyprefix ab. `assert_workflow_status_mirror()` vergleicht
Cursor, beide Statusarten und die rollenbasierten Returnpolicy-Fakten. Beide
Grenzen sind ebenfalls mit AST-Vergleichszahl und vollständigem Funktionsdigest
inventarisiert; keine neue `_recoverable_*`-Ausnahme beteiligt sich daran.

`assert_slice_boundary_mirror()` weist Vor-R3-Ketten für bereits gebundene
Slices ohne Boundaryrecord ab und vergleicht `start_commit`, die verschachtelte
`scope_change_groups`-Partition sowie den am Bindezeitpunkt gemessenen
`start_fingerprint`. Der Start-Commit und Fingerprint bleiben über monotone
Scope-Erweiterungsrevisionen unveränderlich. Die Boundary bleibt bewusst in der
Fingerprint-Eingabemenge des Finalreview-Preflights: Sie ist keine volatile
Halt-/Resume-Transition, sondern die stabile Dispatch- und Scopegrenze. Ein neuer
Slice oder eine autorisierte Scopeerweiterung ändert diese Identität genau
einmal; ein unveränderter Resume tut es nicht. Auch diese Grenze ist per
AST-Vergleichszahl und vollständigem Funktionsdigest inventarisiert.
Bei einer autorisierten Scopeerweiterung dürfen ältere Work-unit-Revisionen
eine echte Teilmenge des aktuellen Slice-Scopes tragen; ausschließlich die
neueste Revision muss exakt mit dem State-Mirror übereinstimmen. Replay lässt
neue Pfade nur monoton und innerhalb derselben Runde zu, sodass eine Revision
weder Scope entfernen noch die Slice-/Rundengrenze verschieben kann.

Die Recovery-Prädikate stützen sich zusätzlich auf
`_pending_denied_review()` (eindeutiger, fingerprint-gebundener Denial),
`_state_has_review_event()` (Review bereits im History-Mirror),
`_finding_statuses()` und `_attestation_facts()` (Stateprojektionen) sowie
`_mirror_difference_code()` (Mirror-ahead gegen ambige Abweichung). Diese
Hilfsvergleiche gehören zu A04/A07/A08, Bewertung jeweils **entfällt**. Ihre
vollständigen AST-Funktionskörper sind ebenso wie die zwei Preflightfunktionen
per SHA-256 eingefroren. Für das fünfte Prädikat sind außerdem
`_recoverable_final_denial_mirror_gap()`, `_persisted_histories()` und
`_historical_correction_attribution_matches()` vollständig eingefroren. Eine
bloße Umstellung von `and` auf `or` kann den Guard daher nicht unterlaufen.

## State-Fakten ohne vollständigen Record

"Rekonstruierbar" bedeutet hier ausdrücklich **aus dem akzeptierten
Recordpräfix allein**, nicht aus State, Checkpoint, Dateiname, Prozessspeicher
oder einer plausiblen Vermutung. Jeder Eintrag mit **nein** ist ein Stopgrund für
S4, solange S3 keinen Record ergänzt oder der Leser das Feld nachweislich aus
dem normativen Zustand entfernt.

| Statefeld/Fakt | Heutiger Schreiber | Heutiger Leser | Aus Recordpräfix rekonstruierbar? | S4-Folge |
|---|---|---|---|---|
| `version` | State-Initialisierung | Schema-/Resumeprüfung | **ja als Projektionskonstante**, aber nicht als fachlicher Recordfakt; Record-Schema 2 ist nicht State-Version 3 | nach Cutover nicht-normative Projektionsversion |
| `run_id` | Initialisierung/Watch | Store, Resume, Watch | **ja** aus jedem Record-Envelope; die Kette erzwingt einen Run | ableitbar |
| `task_file` | Initialisierung/CLI | Resume, Watch, Handoff | **ja seit R1** aus `RunIdentityPayload.task_file` | **in R1 gedeckt** durch `RunIdentityPayload` |
| `branch` | Initialisierung/Resume | Repository-/Resumeprüfung | **ja seit R1** aus `RunIdentityPayload.branch`; `Task.target_branch` bleibt davon verschieden | **in R1 gedeckt** durch `RunIdentityPayload` |
| `branch_base` | Initialisierung | Diff-/Scope-/Commitlogik | **ja seit R1** aus `RunIdentityPayload.branch_base` | **in R1 gedeckt** durch `RunIdentityPayload` |
| `execution_mode` | Taskparser/Initialisierung | Workflowrouting | **ja seit R1** aus `RunIdentityPayload.execution_mode` | **in R1 gedeckt** durch `RunIdentityPayload` |
| `audit_report_path` | Taskparser/Initialisierung | Auditprojektion, Correction-Reportpfad | **ja seit R1** aus `RunIdentityPayload.audit_report_path` | **in R1 gedeckt** durch `RunIdentityPayload` |
| `created_at`, `updated_at` | Initialisierung/State-Mutatoren | Diagnose/Serialisierung | **teilweise**; Recordzeiten geben Ereigniszeit, nicht exakt dieselben Statezeiten | kein STOP, falls als nicht normative Projektionsmetadaten neu definiert; sonst Record nötig |
| `current_slice_id`, `current_work_unit_id`, `current_step` | WorkflowEngine | Dispatch/Resume/Checkpoint | **ja seit R2** aus dem letzten Work-unit-bezogenen `WorkflowTransitionPayload`; der globale Step ist ausdrücklich die Projektion von `work_units[current_work_unit_id].current_step` und wird nur einmal recordet | **in R2 gedeckt** durch `WorkflowTransitionPayload` |
| `slices[*].slice_id` | Plan/State-Initialisierung | Routing, Work-unit-Zuordnung | **ja** aus `PlanPayload.slices[*].slice_id` | ableitbar |
| `slices[*].scope_paths` | Plan-/Work-unit-Bindung | Scopeprüfung/Preflight | **ja** aus `PlanPayload.slices[*].paths` und bestätigendem `WorkUnitPayload.paths` | ableitbar |
| `slices[*].status` | WorkflowEngine | Routing, Completion, Commit | **ja seit R2** aus der letzten Transition je `slice_id`, einschließlich Slice-only-Initialisierung | **in R2 gedeckt** durch `WorkflowTransitionPayload.slice_status` |
| `slices[*].start_commit` | Initialisierung/Slicewechsel | Scopefingerprint, Commit | **ja seit R3** aus `SliceBoundaryPayload.start_commit` | **in R3 gedeckt** durch die unveränderliche Slicegrenze |
| `slices[*].scope_change_groups` | Plan-/Scopebindung | Scopevalidation | **ja seit R3** aus der verschachtelten `SliceBoundaryPayload.scope_change_groups`-Partition; keine Rekonstruktion aus flachen Pfaden | **in R3 gedeckt** einschließlich monotoner Scopeerweiterungsrevisionen |
| `slices[*].start_fingerprint` | Repositorymessung | Change-/Resumeprüfung | **ja seit R3** aus `SliceBoundaryPayload.start_fingerprint`; Replay liest den historischen Messwert ohne Repositoryzugriff | **in R3 gedeckt** durch die Bindezeitmessung |
| `slices[*].commit_ref` | Commitübergang | Resume/Completion | **ja** für gebundene Commits aus `Binding.target`; Slicezuordnung muss über Work-unit/Bindingkontext eindeutig bleiben | Projektion erst nach expliziter Zuordnungsregel |
| `work_units[*].status` | WorkflowEngine | Dispatch/Resume | **ja seit R2** aus der letzten Transition je `work_unit_id`, einschließlich Waiting und Completed | **in R2 gedeckt** durch `WorkflowTransitionPayload.work_unit_status` |
| `work_units[*].current_step` | WorkflowEngine | Dispatch | **ja seit R2** aus der letzten Transition je `work_unit_id` | **in R2 gedeckt** durch `WorkflowTransitionPayload.step` |
| `work_units[*].codex_return_count` | Return-/Correctionpolicy | Iterationsgate | **ja seit R2** über die benannte Mirrorprojektion auf `WorkflowPolicyPayload.implementer_return_count`; nicht aus denied Reviews gezählt | **in R2 gedeckt** durch rollenbasierten Policyrecord |
| `work_units[*].max_codex_returns` | Initialisierung/Policy | Iterationsgate | **ja seit R2** über die benannte Mirrorprojektion auf `WorkflowPolicyPayload.max_implementer_returns`; Iteration-limit-Fortsetzung ändert diesen Fakt unabhängig vom Zähler | **in R2 gedeckt** durch rollenbasierten Policyrecord |
| `work_units[*].reviewer` | Initialisierung/Engine | Reviewerdispatch | **ja seit R2 als Gruppe-C-Projektion**: `None` vor dem ersten denied Review, danach die Rolle des letzten denied `ReviewPayload` derselben Work-unit | ableitbar; kein eigener Record |
| `work_units[*].completed_side_effects` | Engine nach Side Effect | Idempotenz/Resume | **ja seit R4** aus den in Resultatreihenfolge reduzierten `SideEffectPayload`-Paaren je Work-unit | **in R4 gedeckt**; Ketten ohne initialisiertes Ledger stoppen |
| `work_units[*].active_test_fingerprint`, `active_test_paths` | Testchange-Gate | Testscope/Resume | **nicht vollständig**; Gatepayload trägt weder Pfade noch Resume-Step | **STOP** |
| aktueller `gate.status/reason/detail/fingerprint/paths/resume_step` | Gatepolicy | Resume/CLI/Dispatch | **nein**; `Gate` bildet nur abgeschlossene Entscheidung mit Kind, Entscheidung und Rationale ab | **STOP**: Pending-/Cleared-Gatetransitionen |
| `gate_decisions[*].paths` und `resume_step` | User-/Policyentscheidung | Resume, Testscope | **nein**; `GatePayload` lässt beide Fakten aus | **STOP** |
| `gate_decisions[*].decided_by` | User-/Policyentscheidung | Audit/Authorityprüfung | **teilweise** aus `GatePayload.authority`; die zulässige Abbildung State-String ↔ Role ist noch nicht als Projektion spezifiziert | **STOP** bis zur geschlossenen Abbildung |
| `gate_decisions[*].decided_at` | User-/Policyentscheidung | Audit | **nein**; `ArtifactRecord.created_at` ist nicht nachweislich dieselbe Entscheidungszeit | **STOP**, falls normativ; sonst Projektionszeit entfernen |
| `invocation_failures[*].invocation_id`, `idempotency_key` | Providerfehlerpfad | Resume/Retry-Deduplikation | **nein**; weder Pause/Retry noch terminaler Attempt bewahren beide State-IDs | **STOP** |
| `invocation_failures[*].provider_text/received_at/step/slice_id/work_unit_id/diagnostic_exit_code` | Providerfehlerpfad | Resume, Diagnose, Exitpolicy | **nein**; Quota/Retry tragen nur Teilmenge | **STOP** |
| `invocation_failures[*].parse_path/source_timezone/reset_at_utc/safety_margin_seconds` | Quota-Parser | Scheduling/Diagnose | **nein** | **STOP** |
| `invocation_failures[*].auto_resume_count/automatic_resume/diff_fingerprint` | Retry-/Resume-Policy | Automatisches Resume und Ack | **teilweise**; Retry-`attempt` und Fingerprint decken nicht die gesamte Policyentscheidung | **STOP** |
| `planned_slices[*].summary` | Planparser | Audit/Prompts | **ja**; `plan_payload()` schreibt `PlannedSlice.summary` wörtlich in `SliceSpec.summary` | ableitbar |
| `work_plan_path` | Task/Plan | Planrouting/Audit | **ja**, sobald der genau eine `Plan`-Record existiert; vor Plan noch nicht vorhanden | nullable Projektion zulässig |
| `approved_plan_commit` | Planfreigabe | Implementation/Binding/Handoff | **ja** aus `Plan` und Planbinding | ableitbar |
| Handoff-IDs | Handoffinitialisierung | Import-/Resumeprüfung | **ja** aus `FindingHandoffImport` im Zielrun; vor Import ist "kein Handoff" eindeutig | ableitbar |
| `target_branch`, `task_scope_patterns`, `task_digest` | Taskparser | Scope/Resume | **ja** aus `Task` (`target_branch`, `scope_paths`, `assignment_sha256`) | ableitbar |
| `protocol_binding.mode/schema/transports` | Initialisierung | Resume/Providertransport | **teilweise**; Recordschema und AgentResult/Review zeigen Transporte erst nach deren Auftreten | **STOP**: Run-Protokollrecord vor Task oder unveränderliche Storemetadaten |
| `protocol_binding.codex_profile/claude_profile` | CLI/Taskdefault | Providerstart/Resume | **ja seit R1** aus `RunProfilePayload`; `ProviderAttempt` bestätigt die Bindung je gestarteter Operation | **in R1 gedeckt** durch `RunProfilePayload` |
| `bootstrap_checks` | Providerbootstrap | Providerstart/Resume | **ja** aus `ProviderInputMeasurement` und `FinalReviewPreflight` | ableitbar |
| `runtime_history.findings` | Reviewpersistenz | Policy/Prompts/Audit | **ja** aus Finding-Transitionen und Import | ableitbar |
| `runtime_history.reviews[*].reviewer/approval/stopped/findings` und `last_claude_fingerprint` | Reviewpersistenz | Approval-/Bindingpolicy | **ja** aus `Review`, Finding-Transitionen und Recordfingerprint | ableitbarer Teil der Reviewprojektion |
| `ContractResult.red_state_followup_slice` in `runtime_history.reviews/latest_claude_review` | Claude-Reviewpersistenz | `audit_trail` und `commit_slice()` | **nein**; `ReviewPayload` enthält das Feld nicht | **STOP**: eigener Reviewfakt ist zwingend, weil er einen Red-State-Git-Commit autorisiert |
| `ContractResult.test_files` | Claude-Reviewpersistenz | Testscope/Audit/Folgeprompt | **nein** im `ReviewPayload` | **STOP**, falls der Leser bleibt |
| `ContractResult.pre_mortem` | Claude-Reviewpersistenz | Approvalpolicy/Audit | **nein** im `ReviewPayload` | **STOP** |
| `ContractResult.anchors` | Claude-Reviewpersistenz | Anchorpolicy/Audit | **nein** im `ReviewPayload` | **STOP** |
| `ContractResult.stop_request` | Claude-Reviewpersistenz | Stop-/Resumepolicy | **nein** im `ReviewPayload`; `verdict=stop` bewahrt nicht die strukturierte Requestursache | **STOP** |
| `ContractResult.validation` | Claude-Reviewpersistenz | Approval-/Commitpolicy | **nicht vollständig**; Binding kann eine Attestation referenzieren, die Reviewshape und deren vollständige Bytes fehlen | **STOP** |
| `ContractResult.evidence.dimensions/largest_residual_risk/break_condition` | Claude-Reviewpersistenz | Approvalpolicy/Audit | **nicht verlustfrei**; `review_payload()` verbindet die drei Strings mit `" | "`, das Trennzeichen ist in Inhalten nicht ausgeschlossen | **STOP**: strukturierte Evidencefelder recorden |
| `ValidationAttestation.attestation_id` in `runtime_history.attestations` | Orchestrator-Validation | Binding/Commit/Audit | **ja** aus `ArtifactRecord.logical_id`; `persist_validation_attestation()` setzt ihn exakt auf `attestation.attestation_id`, und Resume vergleicht `(logical_id, fingerprint)` mit dem Mirror | ableitbar |
| `ValidationAttestation.diff_fingerprint` | Orchestrator-Validation | Binding/Commit | **ja** aus `ArtifactRecord.fingerprint` | ableitbar |
| `ValidationAttestation.expected_commands` und `command_specs` | Orchestrator-Validation | Vollständigkeitsprüfung | **ja** in Reihenfolge aus `ValidationAttestationPayload.results[*].command`; `command_payload()` ist über Family/Mode eindeutig und der State-Contract erzwingt `tuple(spec.display) == expected_commands` | ableitbar |
| `ValidationAttestation.records[*].status/command/exit_code` | Orchestrator-Validation | Review/Audit | **ja** aus den typisierten Results, einschließlich `unavailable` | ableitbar |
| `ValidationRecord.output` | Validator | Review/Audit/Diagnose | **nein**; der Record enthält nur `output_sha256` | **STOP**: Content-/Blobrecord oder Output als ausdrücklich nicht normativ entfernen |
| `ValidationAttestation.output_digest` | Orchestrator-Validation | Integritätsprüfung | **nein** ohne die gebundenen Outputbytes und den Aggregationsbeleg | **STOP** |
| `ValidationAttestation.summary` | Orchestrator-Validation | Contractvalidierung/Review/Audit | **ja** als deterministische Projektion der aufgezeichneten Outcome-Anzahlen: `passed/failed/unavailable/required` | ableitbar; Formel an Schema 2 binden |
| `runtime_history.latest_claude_review` als Aggregat | Reviewpersistenz | Folgeprompts/Policy | **nicht vollständig**; nur die oben als ableitbar markierte Teilmenge besitzt Records | **STOP**, bis alle Einzelzeilen recordfähig oder nicht normativ sind |
| `runtime_history.codex_final_report` und weitere rohe Agenttexte | Agentresultatpfad | Abschlussbericht/Folgeprompt | **nein aus Records**; Record bindet nur `response_sha256`, Bytes liegen extern im Cache | **STOP**: Contentrecord/Blobauthority oder beweisbar entbehrlicher Cache |
| `runtime_history.active_review_packet` | Reviewpacketbuilder | Recovery/Providerrequest | **nein aus Records**; content-addressed Cache ist nicht Teil des Recordpräfixes | **STOP**: kanonisch aus Records neu bauen oder Blob binden |
| sonstige `runtime_history`-Event-/Auditfelder | Engine/Serialisierung | Audit und Resume-Helfer | **nur teilweise**; IDs lassen sich erzeugen, heutige Reihenfolge/Metadaten sind nicht vollständig spezifiziert | **STOP**, bis die Projektionsfunktion und nicht-normative Felder festgelegt sind |

### S4a-Sortierung der STOP-Einträge

Die folgende Tabelle ist die verbindliche Auflösung der aktuell dreiundzwanzig
fett als `STOP` markierten Zeilen sowie der bereits in R1 bis R4 geschlossenen
Zeilen oben. Jede Zeile kommt genau einmal vor. `A` benennt
den benötigten Recordtyp, das Feld und den heutigen beziehungsweise künftigen
Schreiber. `B` benennt die tatsächlichen Leser und begründet, weshalb deren
Entscheidungen den Mirrorwert nicht benötigen. `C` benennt den vollständigen
Ableitungsweg aus dem akzeptierten Recordpräfix. Die Zeitfelder des State sind
zusätzlich aufgenommen, obwohl ihre Ursprungszeile nur einen bedingten und
keinen fett markierten STOP enthält.

| Statefeld/Fakt | Gruppe | Begründung und Record-/Ableitungsweg |
|---|:---:|---|
| `task_file` | A | **In R1 geschlossen:** `RunIdentityPayload.task_file`; Schreiber: erster strukturierter Checkpoint in `ProductionWorkflowDriver` vor dem ersten Resume-/Watch-Handoff und Dispatch. CLI, Watch und Handoff lesen den Pfad zur Identitäts- und Queuezuordnung. |
| `branch` | A | **In R1 geschlossen:** `RunIdentityPayload.branch`; Schreiber: derselbe frühe Checkpoint nach Prüfung der tatsächlich aktiven Branch. `TaskPayload.target_branch` ersetzt diese gemessene Identität nicht. |
| `branch_base` | A | **In R1 geschlossen:** `RunIdentityPayload.branch_base`; Schreiber: derselbe frühe Checkpoint. Diff-, Scope- und Commitlogik lesen die exakte Git-Startgrenze. |
| `execution_mode` | A | **In R1 geschlossen:** `RunIdentityPayload.execution_mode`; Schreiber: derselbe frühe Checkpoint nach Taskparser/Initialisierung und vor dem ersten Workflowdispatch. |
| `audit_report_path` | A | **In R1 geschlossen:** `RunIdentityPayload.audit_report_path`; Schreiber: derselbe frühe Checkpoint nach Taskparser/Initialisierung. Auditprojektion und Correction-Reportrouting führen davon Dateischreibziele und Scope ab. |
| `created_at`, `updated_at` | B | Leser sind `WorkflowState.to_dict()/from_dict()`, `state_io` sowie die Diagnoseausgabe; `orchestrator` verwendet `updated_at` nur als Anzeigewert für das ebenfalls nicht normative `decided_at`. Kein Routing-, Authority-, Retry- oder Side-effect-Entscheid hängt von beiden Zeiten ab. Nach S4b sind sie volatile Projektionsmetadaten aus Recordzeiten und nicht Teil semantischer Gleichheit. |
| `current_slice_id`, `current_work_unit_id`, `current_step` | A | **In R2 geschlossen:** `WorkflowTransitionPayload.slice_id/work_unit_id/step`; Schreiber: `WorkflowEngine` über den Treiber an jeder Dispatch-, Gate-, Resume- und Completionkante vor dem nächsten Leser. Der globale Step ist die benannte Projektion des Steps der aktuellen Work-unit und kein zweiter Fakt. |
| `slices[*].status` | A | **In R2 geschlossen:** `WorkflowTransitionPayload.slice_status`; Schreiber: `WorkflowState`-Transitionsmethoden über den Engine-Treiber. Routing, Commit und Completion lesen die letzte Transition je Slice. |
| `slices[*].start_commit` | A | **In R3 geschlossen:** `SliceBoundaryPayload.start_commit`; Schreiber: `_persist_slice_boundaries()` nach der Cursortransition und vor Work-unit-Bindung, Scopeprüfung und Side Effect. Replay erzwingt denselben Start-Commit in jeder späteren Scopeerweiterungsrevision. |
| `slices[*].scope_change_groups` | A | **In R3 geschlossen:** `SliceBoundaryPayload.scope_change_groups`; Schreiber: die Git-/Scopebindung aus `WorkflowState`. Die verschachtelte, sortierte Partition bleibt verlustfrei erhalten; gleiche Pfadmengen mit anderer Gruppierung bleiben verschieden. Genehmigte Erweiterungen fügen Gruppen monoton in einer Revision derselben logischen Slicegrenze hinzu. |
| `slices[*].start_fingerprint` | A | **In R3 geschlossen:** `SliceBoundaryPayload.start_fingerprint`; Schreiber: `_persist_slice_boundaries()` übernimmt die zuvor gemessene Repositorygrenze aus dem State und berechnet sie beim Lesen nie neu. Change-, Correction- und Resumeprüfung lesen den historischen Wert. |
| `work_units[*].status` | A | **In R2 geschlossen:** `WorkflowTransitionPayload.work_unit_status`; Schreiber: Engine an Dispatch-, Wait-, Gate-, Resume- und Completionkanten. |
| `work_units[*].current_step` | A | **In R2 geschlossen:** `WorkflowTransitionPayload.step`; Schreiber: Engine unmittelbar vor beziehungsweise nach jeder ausführbaren Operation. |
| `work_units[*].codex_return_count` | A | **In R2 geschlossen:** `WorkflowPolicyPayload.implementer_return_count`; Schreiber: Work-unit-Initialisierung und `record_review_denial()` bei jedem normalen Rücklauf. Die benannte Projektion `project_implementer_return_policy()` bildet den unveränderten State-v3-Mirrornamen auf die Rolle ab. |
| `work_units[*].max_codex_returns` | A | **In R2 geschlossen:** `WorkflowPolicyPayload.max_implementer_returns`; Schreiber: Work-unit-Initialisierung und explizite Iteration-limit-Fortsetzung. Diese Fortsetzung erhöht nur die Obergrenze und lässt `implementer_return_count` unverändert. |
| `work_units[*].reviewer` | C | **In R2 geschlossen:** Die benannte und vor/nach einem Denial gegen den State-v3-Mirror getestete Projektion `project_work_unit_reviewers()` liefert vor dem ersten Review `None`, danach die Rolle des letzten denied `ReviewPayload` derselben Work-unit. Schema 2 erlaubt dafür ausschließlich `Role.CLAUDE`; ein eigener Reviewerrecord existiert nicht. |
| `work_units[*].completed_side_effects` | A | **In R4 geschlossen:** `SideEffectPayload.effect_key/phase/result` plus immutable Klasse/Work-unit/Operationsparameter; Schreiber: jeweiliger Wrapper vor und nach Git-, Provider-, Datei- oder Queueoperation. Pure Replay projiziert Resultate in Abschlussreihenfolge; Resume verlangt, dass der Mirror ein exakter Präfix ist, und übernimmt ausschließlich ein autoritatives Record-vor-Mirror-Suffix. Git prüft Parent/erwarteten Baum, Provider die dauerhafte Antwort oder terminalen Fehler, überschreibende Projektionen Soll-/Vorher-Digest plus im Intent persistierte Sollbytes und Queue genau eine gebundene reguläre Source-/Destinationlage ohne Symlink. Unbekanntes stoppt, Ketten ohne Ledgerinitialisierung werden nicht nachgerüstet. |
| `work_units[*].active_test_fingerprint`, `active_test_paths` | A | `GateTransitionPayload.active_test_fingerprint/paths`; Schreiber: Testchange-Gate nach exakter Scopeentscheidung. Testscope und Resume lesen beide Werte gemeinsam. |
| aktueller `gate.status/reason/detail/fingerprint/paths/resume_step` | A | `GateTransitionPayload.status/reason/detail/fingerprint/paths/resume_step`; Schreiber: Gatepolicy bei Pending, Clear und Resume. Der aktuelle Dispatch hängt unmittelbar davon ab. |
| `gate_decisions[*].paths` und `resume_step` | A | `GateDecisionPayload.paths/resume_step`; Schreiber: User-/Policyentscheidung in `persist_gate_decision()`. Resume und Testscope benötigen die exakte Bindung. |
| `gate_decisions[*].decided_by` | B | Leser sind `_authorized_test_approval()`, `_overall_audit_entries()` und `workflow._authorized_test_changes()` über `AuthorizedTestChanges.approved_by`; sie rendern den freien Anzeigenamen nur im Audit. `has_gate_approval()` prüft ihn nicht. Die normative Authority bleibt verlustfrei `GatePayload.authority`; der freie State-String entfällt. |
| `gate_decisions[*].decided_at` | B | Leser sind `_authorized_test_approval()`, `_overall_audit_entries()` und `workflow._authorized_test_changes()` über `AuthorizedTestChanges.approved_at`; keine Gate-, Commit- oder Resumeentscheidung prüft die Zeit. Für Anzeige genügt `ArtifactRecord.created_at`, ohne Gleichheit mit dem alten Mirrorzeitpunkt zu behaupten. |
| `invocation_failures[*].invocation_id`, `idempotency_key` | A | `InvocationFailurePayload.invocation_id/idempotency_key`; Schreiber: Providerfehlerpfad vor Retry-/Pauseentscheidung. Resume und Deduplikation lesen beide IDs. |
| `invocation_failures[*].provider_text/received_at/step/slice_id/work_unit_id/diagnostic_exit_code` | A | Gleichnamige Felder in `InvocationFailurePayload`; Schreiber: klassifizierter Providerfehlerpfad. Resume, Diagnose und Exitpolicy lesen diese Zuordnung. |
| `invocation_failures[*].parse_path/source_timezone/reset_at_utc/safety_margin_seconds` | A | Gleichnamige Felder in `InvocationFailurePayload`; Schreiber: Quota-Parser/Scheduler. Die nächste zulässige Ausführung hängt davon ab. |
| `invocation_failures[*].auto_resume_count/automatic_resume/diff_fingerprint` | A | Gleichnamige Felder in `InvocationFailurePayload` plus Transitionrevision; Schreiber: Retry-/Resume-Policy. Automatisches Resume und Repository-Ack lesen die Entscheidung. |
| `protocol_binding.mode/schema/transports` | C | Der erste akzeptierte `ArtifactRecord.schema_version == "2"` legt `mode=structured-v2` und `schema_version=2` fest. Das geschlossene Schema 2 erzwingt für `AgentResultPayload.transport_schema` den Wert `native-codex-v2` und für `ReviewPayload.transport_schema` `native-claude-review-v2`; andere Transporte sind in diesem Präfix unzulässig. |
| `protocol_binding.codex_profile/claude_profile` | A | **In R1 geschlossen:** `RunProfilePayload.codex_model/codex_effort/claude_model/claude_effort`; Schreiber: erster strukturierter Checkpoint aus CLI-/Taskdefault vor dem ersten Providerstart. `ProviderAttemptPayload` bestätigt die Bindung je Aufruf. |
| `ContractResult.red_state_followup_slice` in `runtime_history.reviews/latest_claude_review` | A | **In S4a geschlossen:** `ReviewPayload.red_state_followup_slice`; Schreiber: `ProductionWorkflowDriver.persist_native_review_contract()`. Audit- und Git-Autorisierung verlangen nun den approved Review-Record derselben Work-unit und desselben Fingerprints; ein Mirrorwert allein autorisiert keinen Red-State-Commit. Der aktuelle native-v2-Konverter setzt das Feld stets auf `None`, daher kann der heutige Transport keinen neuen Red-State-Review erzeugen. Das spätere Durchreichen aus `StepContract` über `NativeReviewContext` in `ContractResult` bleibt ein ausdrücklich benannter Folgepunkt und ist nicht Teil dieses Record-Slice. |
| `ContractResult.test_files` | A | `ReviewPayload.test_files`; Schreiber: `persist_native_review_contract()` aus dem exakten `NativeReviewContext`. Die Produktivkonfiguration befüllt `expected_test_files` nicht zuverlässig aus dem vorherigen `AgentResultPayload`, deshalb ist die heutige Mirrorprojektion nicht allgemein aus dessen Record ableitbar. |
| `ContractResult.pre_mortem` | A | `ReviewPayload.pre_mortem`; Schreiber: `persist_native_review_contract()`. Approvalpolicy und Audit lesen den reviewer-eigenen Text. |
| `ContractResult.anchors` | A | `ReviewAnchorPayload`-Liste mit allen Anchorfeldern, an den Review-Record gebunden; Schreiber: `persist_native_review_contract()`. Anchorpolicy und Audit lesen die Struktur. |
| `ContractResult.stop_request` | A | `ReviewPayload.stop_request.rule_id/rationale/remediation_paths`; Schreiber: `persist_native_review_contract()`. `verdict="stop"` allein rekonstruiert Ursache und Remediation nicht. |
| `ContractResult.validation` | A | `ReviewValidationBindingPayload.review_record_id/attestation_record_id`; Schreiber: `persist_native_review_contract()` nach der Attestation. Die vollständige Projektion hängt zusätzlich von den unten genannten Validation-Contentrecords ab. |
| `ContractResult.evidence.dimensions/largest_residual_risk/break_condition` | A | **In S4a geschlossen:** `ReviewPayload.review_evidence` mit drei gleichnamigen Feldern; Schreiber: `persist_native_review_contract()`. Neue Records schreiben das alte Stringfeld nie. |
| `ValidationRecord.output` | A | `ValidationContentPayload.command/result_record_id/output_bytes`; Schreiber: Validator unmittelbar mit `ValidationAttestationPayload`. Reviewpacket, Audit und Diagnose lesen die Bytes. |
| `ValidationAttestation.output_digest` | A | `ValidationContentPayload.raw_stdout/raw_stderr` plus `ValidationAttestationPayload.output_digest`; Schreiber: Validator. Der Digest bindet heute ungekürzte Ausgaben und ist aus den Digests der kompakten Recordausgaben nicht ableitbar. |
| `runtime_history.latest_claude_review` als Aggregat | A | Kein zweiter Aggregatrecord: Projektion aus `ReviewPayload`, `ReviewAnchorPayload`, `ReviewValidationBindingPayload`, Finding-Transitionen und den zugehörigen Contentrecords; Schreiber sind die jeweiligen Review-/Validationpersistenzen. Bis diese Komponenten vollständig sind, bleibt der Aggregatleser gesperrt. |
| `runtime_history.codex_final_report` und weitere rohe Agenttexte | A | `ProviderContentPayload.response_sha256/content_bytes/content_kind`; Schreiber: Providerabschluss vor Cache-/Mirrorwrite. Abschlussbericht und Folgeprompt lesen die bytes, nicht nur deren Digest. |
| `runtime_history.active_review_packet` | A | `ReviewPacketPayload.fingerprint/manifest/content_bytes`; Schreiber: `build_review_packet()` vor Providerstart. Recovery und Providerrequest lesen die kanonischen Bytes. |
| sonstige `runtime_history`-Event-/Auditfelder | A | `WorkflowEventPayload.event_kind/work_unit_id/slice_id/round_number/record_refs` für noch nicht durch die fachlichen Records abgedeckte Ereignisse; Schreiber: `_record_review()`, Validation- und Transitionpfade. Erst danach darf ein reiner Audit-/Resume-Projektionsanteil als B entfernt werden. |

#### R4-Suitelaufzeit

Der vollständige WSL-Lauf vom 31. August 2026 mit
`python3 -m pytest tests/ -q` ist grün: **1297 passed in 142,81 s**.
Gegenüber der RP-Baseline von **1264 passed in 279 s** sind das 33 zusätzliche
Tests bei einer um **136,19 s beziehungsweise 48,8 %** niedrigeren von Pytest
ausgewiesenen Laufzeit. Der zunächst gemessene Ledger-Anstieg wurde auf die bei
jedem Record erneut vollständig selbstgeprüfte statische JSON-Schema-Definition
zurückgeführt. Der Schemaquellbaum wird nun einmal privat geladen und geprüft;
`load_schema()` gibt weiterhin nur eine isolierte Kopie aus. Damit bleibt der
Cache abgeleitet und ohne änderbare Autorität, während jeder Record weiterhin
gegen das bereits selbstgeprüfte Schema validiert wird.

#### Entscheidung zu Schema 2 und Bestandsrecords

R1 ergänzt `RunIdentityPayload` und `RunProfilePayload` additiv. Beide werden
beim ersten strukturierten Checkpoint vor dem ersten Workflowdispatch und damit
vor jedem Providerstart geschrieben. Replay projiziert die beiden typisierten
Payloads ohne State- oder Dateisystemzugriff; Resume vergleicht vorhandene
Runrecords fail-closed mit dem State-v3-Mirror. Vor R1 geschriebene Ketten ohne
beide Recordtypen bleiben gültig und liefern für diese Projektionen `None`.
`protocol_binding.mode/schema/transports` bleibt Gruppe C und erhält keinen
eigenen Record. Schema- und Transportversionen bleiben unverändert bei 2.

R2 ergänzt `WorkflowTransitionPayload` und `WorkflowPolicyPayload` ebenfalls
additiv in Schema 2, ändert aber bewusst die Lesepolicy: Jede fortsetzbare
Kette muss nun einen vollständigen Transition-/Policyprefix besitzen;
Vor-R2-Ketten werden fail-closed abgewiesen und niemals nachträglich
aufgefüllt. Der Treiber schreibt die Delta-Records vor jedem folgenden
Dispatch-Guard, während `state.json` und Checkpoints unverändert als Mirror
weitergeschrieben werden. Das ist ausdrücklich noch kein Cutover.

`current_step` ist genau ein Fakt: die Projektion von
`work_units[current_work_unit_id].current_step`. Deshalb trägt der
Work-unit-bezogene Transitionrecord nur ein `step`-Feld. Die Returnpolicy ist
dagegen nicht aus Reviews ableitbar und besitzt die rollenbasierten Felder
`implementer_return_count` und `max_implementer_returns`; die
Iteration-limit-Fortsetzung erhöht ausschließlich die Obergrenze. Der Reviewer
bleibt Gruppe C: Replay liefert vor dem ersten denied Review `None` und danach
die Rolle des letzten denied `ReviewPayload`. Dafür wurde kein Recordtyp
ergänzt. Record-Schema, State-Schema und Registerversionen bleiben unverändert.

S4a erweitert den geschlossenen `ReviewPayload` additiv um die optionalen Felder
`review_evidence` und `red_state_followup_slice`; `schema_version` und beide
nativen Transportversionen bleiben unverändert bei 2. Das ist keine Änderung
der Bedeutung vorhandener Bytes: Alte Records ohne die neuen Properties werden
weiter gelesen. Ihr `evidence`-String bleibt dabei **opak**. Er wird weder an
`" | "` geteilt noch in drei vermeintliche Felder umgedeutet. Nur neue Records
schreiben das strukturierte Objekt. Ein alter Review ohne
`red_state_followup_slice` kann keine rote Attestation autorisieren.

Die korrigierte S4a-Bestandsprüfung fand 32 persistierte Review-Records. Der
Record `ar1-c24d40d2bcf557f302bf3b64414ea5142bd8e11f3814315e1013547ab73bbfe3`
im Run `watch-20260826-122718.749752Z-ffce3cc1140c` enthält im Legacyfeld
`evidence` drei `" | "`-Trenner. Damit ist mindestens eine Feldgrenze bereits
nicht mehr aus dem Record rekonstruierbar.

Die am 30. August 2026 bestätigte Bestandsdatenentscheidung lautet: Alte
Records und ihre immutable Kette bleiben unverändert; Legacy-`evidence` wird
als ein opaker String gelesen und niemals heuristisch geteilt. Die verlorenen
Feldgrenzen gelten als nicht verfügbar. Eine spätere Migration ist nur aus
einer authentisch request-/response-digest-gebundenen Originalantwort erlaubt,
nie aus dem Trennzeichenstring. Fehlt diese Quelle, bleibt der Record als
Legacyformat auditierbar, liefert aber keine drei strukturierten Evidencefelder.
Neue Records schreiben ausschließlich das strukturierte Objekt.

### S4a-Schnittvorschlag für die verbleibenden Gruppe-A-Einträge

1. **Runidentität sowie frühe Protokoll-/Profilbindung.** `task_file`, `branch`,
   `branch_base`, `execution_mode`, `audit_report_path` und beide Agentprofile.
   Module: `workflow_state.py`, `orchestrator.py`, `artifact_models.py`,
   `artifact_bridge.py`, Schema und Resume/Migration. Muss vor allen weiteren
   Bündeln landen, weil bereits der erste Dispatch diese Fakten liest.
2. **Cursor und Work-unit-/Slice-Status.** Aktuelle IDs/Steps, beide Statusarten,
   `codex_return_count` und `max_codex_returns`. Module: `workflow_state.py`, `workflow.py`,
   `artifact_models.py`, `artifact_replay.py`, `artifact_migration.py`. Benötigt
   Bündel 1 und muss vor Side-effect-Reconciliation vorhanden sein.
3. **Slice-Startgrenze und Scopefingerprint.** `start_commit`,
   `scope_change_groups`, `start_fingerprint`. Module: `git_service.py`,
   `repo_changes.py`, `workflow_state.py`, Recordmodell/Replay. Benötigt Run- und
   Cursoridentität; Gate- und Commitrecords bauen darauf auf.
4. **Side-effect-Ledger.** `completed_side_effects` als Intent-/Resultatpaare für
   Git, Provider, Dateien und Queue. Module: `workflow.py`, `orchestrator.py`,
   `inbox_watcher.py`, `git_service.py`, Store/Replay. Benötigt Bündel 1 bis 3;
   liefert anschließend die Crashfenster-Reconciliation für S4b.
5. **Gate-Transitionen.** Aktueller Gatezustand, aktive Testbindung sowie
   Decision-`paths`/`resume_step`; die beiden B-Zeit-/Anzeigefelder entfallen.
   Module: `workflow_state.py`, `workflow.py`, `orchestrator.py`,
   `artifact_models.py`, Replay/Migration. Benötigt Cursor und Scopegrenze.
6. **Failure-/Retry-Policy.** Alle vier Invocation-Failure-Zeilen einschließlich
   Identitäten, Parserdiagnose, Scheduling und Auto-Resume. Module:
   `failure_classification.py`, `agent_runtime.py`, `workflow_state.py`,
   `workflow.py`, `orchestrator.py`, Replay/Migration. Benötigt Cursor, Profile
   und Side-effect-Ledger.
7. **Verbleibende Review-Contractfelder.** `test_files`, `pre_mortem`, `anchors`,
   `stop_request`, Validationbindung und die daraus entstehende
   `latest_claude_review`-Projektion. Module: `contracts.py`,
   `native_review_contract.py`, `artifact_models.py`, `artifact_bridge.py`,
   `artifact_replay.py`, `audit_trail.py`. Kann nach Bündel 1 bis 3 erfolgen;
   Validationbindung benötigt Bündel 8.
8. **Blob-/Contentauthority.** Validationoutput und -digest, rohe Agenttexte und
   aktive Reviewpackets. Module: `validation_matrix.py`, `review_packets.py`,
   `agent_runtime.py`, `orchestrator.py`, Store/Schema/Replay. Muss vor dem
   Abschluss von Bündel 7 und vor dem Cutover liegen.
9. **Restliche Historyprojektion.** Nur die nach Bündel 1 bis 8 noch
   verbleibenden Event-/Auditfelder; normativen Rest recorden, reine
   Darstellungswerte explizit entfernen. Module: `workflow.py`,
   `audit_trail.py`, `artifact_projection.py`, `artifact_migration.py`. Dieses
   Abschlussbündel hängt von allen vorigen ab und liefert die unmittelbare
   S4b-Vorbedingung.

### Nicht im State, aber für Recovery relevante Caches/Side Effects

Requestbundles, rohe Codex-/Claude-Antworten, Reviewpakete, `head.json`,
Audit-Markdown und Checkpoints sind nicht allein aus dem Recordpräfix
rekonstruierbar, solange Records nur Digests speichern. Ein Digest beweist
vorhandene Bytes, rekonstruiert sie aber nicht. S4 darf sie daher nur dann als
wegwerfbaren Cache behandeln, wenn die kanonischen Bytes aus Records plus
versionierter Policy neu gebaut werden können; andernfalls ist ein Blob-/
Contentrecord nötig. Git-Commits, Provideraufrufe und Queuebewegungen sind keine
Caches und brauchen je eine Intent-/Resultat- oder Reconciliation-Regel.

## Konsequenzen für S3 und S4

1. S3 muss zuerst Records oder explizite Ableitungsregeln für alle **STOP**-
   Einträge schaffen. Insbesondere Cursor/Status, Slice-Startgrenze,
   Side-effect-Ledger, Pending-Gate, vollständige Failurepolicy und frühe
   Protocol-/Profilbindung dürfen nicht aus dem Mirror "übernommen" werden.
2. S4 darf Record-Authority erst aktivieren, wenn ein leerer State aus einem
   beliebigen akzeptierten Präfix deterministisch neu projiziert werden kann
   und Commit/Provider/Queue-Crashfenster eine explizite Reconciliation haben.
3. Danach entfallen die spezialisierten Mirrorvergleiche und
   `_recoverable_*`-Ausnahmen. Es bleiben Recordschema/Kette/Referenzen,
   Providerattempt-Kausalität, Cross-run-Handoff und externe Side-effect-
   Reconciliation. Alle Datei-/Historykopien gehen in einen generischen
   Cacheintegritätsabgleich auf.

Diese Aussagen sind Empfehlungen für die folgenden Slices, keine
Cutover-Entscheidung in S2.
