# S2 – Übergangs- und Divergenzmatrix

Stand: 2026-08-31. Untersucht ist `structured-v2` einschließlich S4b auf dem
Branch `feature/state-authority-consolidation`. Der Authority-Cutover ist
vollzogen: Ausschließlich die append-only Recordkette ist technische Quelle.
`state.json`, Checkpoints, `head.json` und Audit-Markdown sind wegwerfbare
Projektionen beziehungsweise Caches. Die Einzelkanten bewahren die ursprüngliche
S2-Analyse; der Abschnitt **S4b-Cutover-Abschluss** hält ihre nun wirksame
Endklassifikation fest.

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

Die Inventur umfasst 26 Kanten. Die S4b-Abnahme misst im relevanten
Vorher-/Nachher-Scope:

- `_recoverable_*`: **5 → 0**
- `differs from state-v3`: **20 → 0** in `artifact_resume.py`
- `mismatch(...)` in `artifact_resume.py`: **44 → 0**

Es gibt in diesen drei Inventaren keinen Rest zu begründen. Die weiterhin
bewussten Prüfungen sind Record-interne Kausalität, unveränderliche
Providerbindungen oder irreversible externe Side Effects und werden in der
Endklassifikation einzeln ausgewiesen.

## Matrix

### A01 — Protokollbindung, Recordkette und Replay

1. **Autoritativer Eingaberecord:** Neue R1-Läufe binden zuerst
   `RunIdentity` und `RunProfile`, danach folgt `Task`; ein atomarer
   Finding-Handoff-Import darf als vorbereitender Genesisrecord davorliegen.
   Jede replaybare Laufkette ohne genau einen Identity- und Profilrecord wird
   seit dem R1-Nachzug mit `RECORD-MISSING` und dem Meldungsstamm
   `record chain requires exactly one run identity and run profile` abgewiesen.
   Nur der eng begrenzte,
   ausschließlich aus einem Finding-Handoff-Import bestehende Schreibpräfix
   darf bis zum unmittelbaren Baseline-Append noch unvollständig sein. Erwartet
   wird stets eine lückenlose Kette bis `head_record_id`; vor dem Replay werden
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
   inklusive Ledgerinitialisierung → Auditprojektion → atomarer State-Replace →
   atomarer Checkpoint-Replace. Cachewrites erzeugen bewusst keine weiteren
   Records: Ein Record-Intent würde den Head verändern und damit seine eigene
   gerade berechnete Cachebindung sofort veralten lassen.
4. **Idempotenz:** Baseline-Append ist record-idempotent; Projektionen müssen
   aus demselben Präfix deterministisch überschreibbar sein. State und
   Checkpoint besitzen heute noch getrennte Gleichheitsanforderungen. Die
   atomaren Textschreiber erzeugen dieselben kanonischen Bytes unter Windows.
   Ein fehlender, veralteter oder manipulierter Cache wird ausschließlich aus
   dem unveränderten autoritativen Recordpräfix ersetzt.
5. **Recoverable-Sonderfall:** keiner; Cacheverlust und unterbrochener Replace
   werden durch deterministische Neuprojektion behandelt, Recordkorruption
   stoppt fail-closed.
6. **Semantik-/Protokollversion:** Records Schema 2; State/Checkpoint Version 3.
7. **Externe Side Effects:** State und Checkpoint sind wegwerfbare, atomar
   ersetzte Cachedateien und deshalb ausdrücklich keine ledgerpflichtigen
   fachlichen Side Effects. Das Auditdokument bleibt ebenfalls Projektion;
   Queue-, Provider- und Git-Wirkungen bleiben separat ledgergebunden.

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
   technische Ablehnung vor der ersten Workflowbaseline besitzt noch keine
   vollständige R1-Laufbindung und wird deshalb vor jedem Ledgerappend
   fail-closed abgewiesen. Wie bei einer korrupten Recordkette verwendet die
   irreversible Quarantäne dann einen aus Run-ID, Taskdigest und Quellname
   deterministisch herleitbaren Zielpfad, damit der Watcher nicht in einer
   Endlosschleife bleibt; die fehlende oder korrupte Kette wird dabei weder
   ergänzt, gelesen noch repariert. Weil eine nicht verfügbare Authority kein
   weiteres autoritatives
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

### 32 Migration-`mismatch(...)`-Stellen

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

### 30 Meldungsstämme aus 32 direkten `ArtifactResumeError`-Stellen

Mehrere Raise-Stellen teilen bewusst denselben stabilen Meldungsstamm; die
Tabelle inventarisiert daher 30 Stämme, während der AST-Zähler 32 direkte
`ArtifactResumeError`-Stellen bindet.

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
| `structured-v2 run has no gate transition prefix` | R5 Gatepräfix fehlt; keine Nachrüstung aus dem State-Mirror |
| `gate transitions differ from state-v3` | R5 aktueller Gatezustand und gemeinsam gebundener aktiver Testscope |
| `gate decision bindings differ from state-v3` | R5 exakte Work-unit-, Pfad- und Resume-Step-Bindung an den früheren `GatePayload` |
| `structured-v2 run has invocation failures but no R6 failure records` | R6 Failure-Mirror ohne autoritativen Failure-Record; keine Nachrüstung aus State |
| `invocation failure identity cannot be projected into state-v3` | R6 Recordidentität ist nicht in die numerische State-v3-Zuordnung projizierbar |
| `invocation failures differ from state-v3` | R6 Failure-Recordfolge und Mirror sind kein identischer Präfix |
| `record chain has an ambiguous invocation failure suffix` | R6 mehr als genau ein Failure-Record liegt dem Mirror voraus |
| `record-ahead invocation failure cannot rehydrate the current work unit` | R6 vorausliegende Retry-/Pauseentscheidung passt nicht auf den aktuellen Rollenschritt |
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

### 58 `ArtifactBridgeError`-Stellen

Die ursprünglichen 26 S2-Stellen bleiben erhalten. Der dormante E9-
Entdeckungsübergang ergänzt 32 bewusst fail-closed gehaltene Wurfstellen für
Quellauflösung, Transitivität sowie Zieltask-, Kindidentitäts- und
Familienbindung.

| Meldungsstamm | Kante |
|---|---|
| `artifact batch requires at least two entries` | R5; atomare Statusgruppe darf nicht zu einem Einzelrecord degenerieren |
| `atomic artifact batch is only partially present` | R5; idempotente Wiederaufnahme lehnt ein unvollständiges Gate-/Work-unit-Paar fail-closed ab |
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
| `structured artifact differs semantically from its existing record` | B01; idempotenter Append gegen unveränderlichen Vorgängerrecord, **bleibt bewusst** |
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
| `branch discovery export differs from target import type` | E9; Quell- und Zielrecord müssen dasselbe Handoffpaar bilden |
| `finding export differs from target import type` | E9; der vorhandene Handoff darf nicht als Entdeckungsimport gelesen werden |
| `branch discovery target task is outside the repository` | E9; Ziel-Queueposition bleibt repositorygebunden |
| `branch discovery handoff requires a non-empty finding snapshot` | E2/E9; ein leerer Entdeckungsübergang besitzt keine Findingautorität |
| `branch discovery handoff requires JOINT_67_68_NATIVE_CONTRACT_CUTOVER` | E9-Dormanz; keine neue Semantik bei ausgeschaltetem gemeinsamen Schalter |
| `branch discovery handoff export requires a non-empty accepted replay` | E9; Export nur von einem akzeptierten Quellpräfix |
| `branch discovery handoff export requires a family binding` | E2a/E9 |
| `branch discovery handoff export requires its approved discovery review record` | E2/E9; positives Entdeckungsreview bleibt Recordautorität |
| `branch discovery handoff export requires its validation attestation record` | E2/E9 |
| `branch discovery review and validation attestation fingerprints differ` | E2/E9; Review und Matrix müssen denselben geprüften Stand binden |
| `branch discovery handoff export requires at least one source transition` | E2/E2b |
| `branch discovery target family predecessor differs from the source head` | E2a/E9 |
| `branch discovery import requires a branch discovery export record` | E9 |
| `branch discovery import requires the target RunProfile family binding` | E2a/E9 |
| `branch discovery export record is not resolvable in the source run` | E9-Replayregel 2 |
| `branch discovery export must be the accepted source replay head` | E9-Replayregel 2 |
| `branch discovery export source run or bound source head differs` | E9-Replayregel 2 |
| `branch discovery export differs from its flattened source history` | E2b/E9-Replayregel 3 |
| `branch discovery import target_task_sha256 differs from task bytes` | E9-Replayregel 4 |
| `branch discovery import target_task_path differs from queue position` | E9-Replayregel 4 |
| `branch discovery import target_run_identity differs from target run` | E2/E9; deterministische Kindidentität |
| `branch discovery import family binding differs from target RunProfile` | E9-Replayregel 5 |
| `PLAN_ONLY finding handoff does not reference a branch discovery export` | E9; Planlauf und vorhandener Implementierungshandoff bleiben getrennt |
| `branch discovery source has no RunProfile family binding` | E2a/E9 |
| `branch discovery export is not the source run head` | E9-Replayregel 2 |
| `branch discovery target_task_sha256 differs from loaded task bytes` | E9-Replayregel 4 |
| `branch discovery export requires a PLAN_ONLY target task` | E9; der Entdeckungsübergang autorisiert genau den Planlauf |
| `branch discovery import requires the target family binding` | E2a/E9 |

### Laufzeitvergleiche außerhalb der Migration

| Meldungsstamm/Prüfung | Kante |
|---|---|
| `structured commit review differs from the commit request` | B07/A14; Record ↔ unmittelbar auszuführender irreversibler Commit, **bleibt bewusst** |
| `structured commit attestation differs from the commit request` | B07/A14; Record ↔ unmittelbar auszuführender irreversibler Commit, **bleibt bewusst** |
| `provider attempt measurement context diverged` | A11/B04 |
| `pre-policy native reviewer recovery tail differs from the active work unit` | B06; Record-interne Zuordnung des Crash-Tails, **bleibt bewusst** |
| `native agent request immutable binding differs: field=binding_fingerprint previous=` | B05/B92 |
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
vollständigem Funktionsdigest eingefroren. Der Rollenumbau hält die inventarisierten
sechs AST-Vergleiche unverändert; der geänderte Funktionsdigest ist in der Matrix-
Regression aktualisiert.

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
`_state_has_review_projection()` (Review bereits durch die verbleibenden
R7-Aggregate im History-Mirror repräsentiert),
`_finding_statuses()` und `_attestation_facts()` (Stateprojektionen) sowie
`_mirror_difference_code()` (Mirror-ahead gegen ambige Abweichung). R6 ergänzt
die vollständig gezählten und per AST-Digest gebundenen Leser
`assert_invocation_failure_mirror()` und
`project_transition_mirror_before_failure()`. Diese
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
| `work_units[*].active_test_fingerprint`, `active_test_paths` | Testchange-Gate | Testscope/Resume | **In R5 vollständig:** gemeinsam in `GateTransitionPayload.active_test_fingerprint/active_test_paths` | **IN R5 GESCHLOSSEN:** Replay und Mirrorvergleich erzwingen die gemeinsame Bindung |
| aktueller `gate.status/reason/detail/fingerprint/paths/resume_step` | Gatepolicy | Resume/CLI/Dispatch | **In R5 vollständig:** `GateTransitionPayload` trägt den gesamten Gatezustand | **IN R5 GESCHLOSSEN:** Pending, Entscheidung, Clear und Resume werden vor ihrem Leser geschrieben |
| `gate_decisions[*].paths` und `resume_step` | User-/Policyentscheidung | Resume, Testscope | **In R5 vollständig:** `GateDecisionPayload` bindet beide an einen früheren `GatePayload` | **IN R5 GESCHLOSSEN:** Replay rekonstruiert die exakte Entscheidungsbindung |
| `gate_decisions[*].decided_by` | User-/Policyentscheidung | Audit/Authorityprüfung | **In R5 entfallen:** freier State-String entfernt; Anzeige aus `GatePayload.authority` | **ENTFALLEN IN R5:** Guard verhindert die Rückkehr ins State-Schema |
| `gate_decisions[*].decided_at` | User-/Policyentscheidung | Audit | **In R5 entfallen:** Statezeit entfernt; Anzeige aus `ArtifactRecord.created_at` des referenzierten Gate-Records | **ENTFALLEN IN R5:** keine Gleichheit mit dem alten Mirrorzeitpunkt wird zugesichert |
| `invocation_failures[*].invocation_id`, `idempotency_key` | Providerfehlerpfad | Resume/Retry-Deduplikation | **ja seit R6** aus `InvocationFailurePayload`; Replay erzwingt eindeutige Invocation-ID und logische Bindung | **IN R6 GESCHLOSSEN:** Record vor Status-/Retrymutation; idempotenter Wiederholungsappend |
| `invocation_failures[*].provider_text/received_at/step/slice_id/work_unit_id/diagnostic_exit_code` | Providerfehlerpfad | Resume, Diagnose, Exitpolicy | **ja seit R6**; `provider_text` ist normativ ein fester Redaktionsmarker mit SHA-256 und UTF-8-Bytezahl statt unbeschränktem Rohtext | **IN R6 GESCHLOSSEN:** Produktionsmirror und Record tragen denselben sicheren Marker |
| `invocation_failures[*].parse_path/source_timezone/reset_at_utc/safety_margin_seconds` | Quota-Parser | Scheduling/Diagnose | **ja seit R6** einschließlich geparster Quelle und hergeleitetem `resume_at_utc` | **IN R6 GESCHLOSSEN:** Domainvalidator beweist Ziel = Reset + Marge |
| `invocation_failures[*].auto_resume_count/automatic_resume/diff_fingerprint` | Retry-/Resume-Policy | Automatisches Resume und Ack | **ja seit R6** einschließlich `decision_at_utc` und `retry_delay_seconds` | **IN R6 GESCHLOSSEN:** Record-ahead-Replay rekonstruiert Halt und verhindert erneuten Dispatch desselben Fehlers |
| `planned_slices[*].summary` | Planparser | Audit/Prompts | **ja**; `plan_payload()` schreibt `PlannedSlice.summary` wörtlich in `SliceSpec.summary` | ableitbar |
| `work_plan_path` | Task/Plan | Planrouting/Audit | **ja**, sobald der genau eine `Plan`-Record existiert; vor Plan noch nicht vorhanden | nullable Projektion zulässig |
| `approved_plan_commit` | Planfreigabe | Implementation/Binding/Handoff | **ja** aus `Plan` und Planbinding | ableitbar |
| Handoff-IDs | Handoffinitialisierung | Import-/Resumeprüfung | **ja** aus `FindingHandoffImport` im Zielrun; vor Import ist "kein Handoff" eindeutig | ableitbar |
| `target_branch`, `task_scope_patterns`, `task_digest` | Taskparser | Scope/Resume | **ja** aus `Task` (`target_branch`, `scope_paths`, `assignment_sha256`) | ableitbar |
| `protocol_binding.mode/schema/transports` | Initialisierung | Resume/Providertransport | **ja seit R1/S4a**; der erste akzeptierte Schema-2-Envelope legt `structured-v2` fest und das geschlossene Schema erzwingt beide nativen Transporte | **geschlossen** durch Envelope- und Writer-Schema-Bindung |
| `protocol_binding.codex_profile/claude_profile` | CLI/Taskdefault | Providerstart/Resume | **ja seit R1** aus `RunProfilePayload`; `ProviderAttempt` bestätigt die Bindung je gestarteter Operation | **in R1 gedeckt** durch `RunProfilePayload` |
| `bootstrap_checks` | Providerbootstrap | Providerstart/Resume | **ja** aus `ProviderInputMeasurement` und `FinalReviewPreflight` | ableitbar |
| `runtime_history.findings` | Reviewpersistenz | Policy/Prompts/Audit | **ja** aus Finding-Transitionen und Import | ableitbar |
| `runtime_history.reviews[*].reviewer/approval/stopped/findings` und `last_claude_fingerprint` | Reviewpersistenz | Approval-/Bindingpolicy | **ja** aus `Review`, Finding-Transitionen und Recordfingerprint | ableitbarer Teil der Reviewprojektion |
| `ContractResult.red_state_followup_slice` in `runtime_history.reviews/latest_claude_review` | Claude-Reviewpersistenz | `audit_trail` und `commit_slice()` | **ja seit R7**; `StepContract` bindet die benannte Folgeslice über `NativeReviewContext` an den approved `ReviewPayload` | **IN R7 GESCHLOSSEN:** eine vollständige rote Attestation ist nur mit exakt benannter Folgeslice autorisierbar; ohne den Reviewrecord bleibt der Commit gesperrt |
| `ContractResult.test_files` | Claude-Reviewpersistenz | Testscope/Audit/Folgeprompt | **ja seit R7** in `ReviewPayload.test_files` | **IN R7 GESCHLOSSEN:** exakter nativer Reviewkontext und Mirrorvergleich |
| `ContractResult.pre_mortem` | Claude-Reviewpersistenz | Approvalpolicy/Audit | **ja seit R7** in `ReviewPayload.pre_mortem` | **IN R7 GESCHLOSSEN:** strukturierter Einzelwert statt Audittext als Quelle |
| `ContractResult.anchors` | Claude-Reviewpersistenz | Anchorpolicy/Audit | **ja seit R7** in genau einem `ReviewAnchorPayload` je Reviewrecord | **IN R7 GESCHLOSSEN:** Record-ID- und Fingerprintbindung, auch für die leere Liste |
| `ContractResult.stop_request` | Claude-Reviewpersistenz | Stop-/Resumepolicy | **ja seit R7** in `ReviewPayload.stop_request.rule_id/rationale/remediation_paths` | **IN R7 GESCHLOSSEN:** drei getrennte strukturierte Felder |
| `ContractResult.validation` | Claude-Reviewpersistenz | Approval-/Commitpolicy | **ja seit R7** über `ReviewValidationBindingPayload.review_record_id/attestation_record_id` und die R8-Contentrecords | **IN R7 GESCHLOSSEN:** beide referenzierten Records müssen früher und fingerprintgleich sein |
| `ContractResult.evidence.dimensions/largest_residual_risk/break_condition` | Claude-Reviewpersistenz | Approvalpolicy/Audit | **ja seit S4a**; `ReviewPayload.review_evidence` bewahrt alle drei Strings getrennt | **geschlossen** durch das strukturierte Schema-2-Feld |
| `ValidationAttestation.attestation_id` in `runtime_history.attestations` | Orchestrator-Validation | Binding/Commit/Audit | **ja** aus `ArtifactRecord.logical_id`; `persist_validation_attestation()` setzt ihn exakt auf `attestation.attestation_id`, und Resume vergleicht `(logical_id, fingerprint)` mit dem Mirror | ableitbar |
| `ValidationAttestation.diff_fingerprint` | Orchestrator-Validation | Binding/Commit | **ja** aus `ArtifactRecord.fingerprint` | ableitbar |
| `ValidationAttestation.expected_commands` und `command_specs` | Orchestrator-Validation | Vollständigkeitsprüfung | **ja** in Reihenfolge aus `ValidationAttestationPayload.results[*].command`; `command_payload()` ist über Family/Mode eindeutig und der State-Contract erzwingt `tuple(spec.display) == expected_commands` | ableitbar |
| `ValidationAttestation.records[*].status/command/exit_code` | Orchestrator-Validation | Review/Audit | **ja** aus den typisierten Results, einschließlich `unavailable` | ableitbar |
| `ValidationRecord.output` | Validator | Review/Audit/Diagnose | **ja seit R8**; `ValidationContentPayload.outputs[*]` bindet Command, Ergebnisrecord, exakte Bytelänge sowie SHA-256-Verweise auf rohe Streams und kompakte Ausgabe | **in R8 gedeckt** durch laufgebundene, fail-closed geprüfte Blobs |
| `ValidationAttestation.output_digest` | Orchestrator-Validation | Integritätsprüfung | **ja seit R8**; `ValidationContentPayload.digest_format/raw_stdout/raw_stderr` reproduziert den unveränderten Digest und `ValidationAttestationPayload` bindet ihn samt Contentrecord | **in R8 gedeckt** ohne stille Digeständerung |
| `ValidationAttestation.summary` | Orchestrator-Validation | Contractvalidierung/Review/Audit | **ja** als deterministische Projektion der aufgezeichneten Outcome-Anzahlen: `passed/failed/unavailable/required` | ableitbar; Formel an Schema 2 binden |
| `runtime_history.latest_claude_review` als Aggregat | Reviewpersistenz | Folgeprompts/Policy | **ja seit R7** als reine Projektion aus Review-, Anchor-, Validation-, Finding- und Contentrecords | **IN R7 GESCHLOSSEN:** Aggregatleser entsperrt; ausdrücklich kein zweiter Aggregatrecord |
| `runtime_history.codex_final_report` und weitere rohe Agenttexte | Agentresultatpfad | Abschlussbericht/Folgeprompt | **ja seit R8**; `ProviderContentPayload` bindet die akzeptierte kanonische native Antwort vor dem Ergebnisrecord an SHA-256, Bytelänge, Rolle, Request, Operation und Inhaltsart | **in R8 gedeckt**; unakzeptierter Failure-Rohtext bleibt gemäß R6 redigiert |
| `runtime_history.active_review_packet` | Reviewpacketbuilder | Recovery/Providerrequest | **ja seit R8**; `ReviewPacketPayload` bindet die lokal erzeugten kanonischen Bytes an Fingerprint, Manifest, Diff-Coverage, Bytelänge und Blobdigest | **in R8 gedeckt**; die materialisierte Datei bleibt Cache |
| sonstige `runtime_history`-Event-/Auditfelder | Engine/Serialisierung | Audit und Resume-Helfer | **ja seit R9**; `WorkflowEventPayload` bindet Reihenfolge und Kontext ausschließlich über Rückwärtsverweise auf Transition, Validation und Review | **geschlossen**; `runtime_history.events` ist entfernt und gegen Rückkehr gesperrt |

### S4a-Sortierung der STOP-Einträge

Die folgende Tabelle ist die verbindliche Auflösung der früher als Stopgrund
markierten Zeilen sowie der bereits in R1 bis R8 geschlossenen
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
| `created_at`, `updated_at` | B | Leser sind `WorkflowState.to_dict()/from_dict()`, `state_io` sowie die Diagnoseausgabe. Der frühere hängende Leser `decided_at=state.updated_at` ist in R5 zusammen mit `decided_at` entfallen. Kein Routing-, Authority-, Retry- oder Side-effect-Entscheid hängt von beiden Zeiten ab. Nach S4b sind sie volatile Projektionsmetadaten aus Recordzeiten und nicht Teil semantischer Gleichheit. |
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
| `work_units[*].active_test_fingerprint`, `active_test_paths` | A | **In R5 geschlossen:** `GateTransitionPayload.active_test_fingerprint/active_test_paths`; Schreiber: Testchange-Gate nach exakter Scopeentscheidung. Domainvalidierung und Replay binden beide Werte gemeinsam; Testscope und Resume erhalten niemals nur einen Teil. |
| aktueller `gate.status/reason/detail/fingerprint/paths/resume_step` | A | **In R5 geschlossen:** `GateTransitionPayload.gate_status/reason/detail/fingerprint/paths/resume_step`; Schreiber: Gatepolicy bei Pending, Clear und Resume vor dem jeweiligen Dispatch-/Audit-Leser. |
| `gate_decisions[*].paths` und `resume_step` | A | **In R5 geschlossen:** `GateDecisionPayload.paths/resume_step`; Schreiber: User-/Policyentscheidung in `persist_gate_decision()`, gebunden an den vorher geschriebenen `GatePayload`. Resume und Testscope erhalten die exakte Work-unit-Bindung. |
| `gate_decisions[*].decided_by` | B | **In R5 entfallen:** Leser waren `_authorized_test_approval()`, `_overall_audit_entries()` und `workflow.authorized_test_changes_from_state()` über `AuthorizedTestChanges.approved_by`. Die State-only-Projektion ist entfernt; die Auditprojektion liest jetzt `GatePayload.authority` aus `ArtifactReplayResult`. `has_gate_approval()` prüfte den freien String nie. |
| `gate_decisions[*].decided_at` | B | **In R5 entfallen:** Leser waren dieselben Auditpfade über `AuthorizedTestChanges.approved_at`; keine Gate-, Commit- oder Resumeentscheidung prüfte die Zeit. Die Anzeige verwendet nun `ArtifactRecord.created_at` des referenzierten `GatePayload`, ausdrücklich als Recordzeit und ohne Gleichheitsbehauptung zum entfernten Mirrorwert. |
| `invocation_failures[*].invocation_id`, `idempotency_key` | A | **In R6 geschlossen:** `InvocationFailurePayload.invocation_id/idempotency_key`; Schreiber: Providerfehlerpfad vor jeder Status-, Zähler-, Retry- oder Pausemutation. Replay erzwingt eine Invocation-ID, der Store einen semantisch identischen Idempotenzschlüssel. |
| `invocation_failures[*].provider_text/received_at/step/slice_id/work_unit_id/diagnostic_exit_code` | A | **In R6 geschlossen:** Gleichnamige Felder in `InvocationFailurePayload`; Schreiber: klassifizierter Providerfehlerpfad. Der unbeschränkte Rohtext wird vor Persistenz durch `[provider text redacted; sha256=…; utf8_bytes=…]` ersetzt. Der Marker ist auf 128 Zeichen begrenzt, bindet die exakten UTF-8-Bytes kryptografisch und steht identisch im Produktionsmirror. Die typisierte `failure_class` und der `diagnostic_code` werden ausschließlich aus `error_classification.classify_exception()` übernommen, nie aus Text oder Exitcode neu hergeleitet. |
| `invocation_failures[*].parse_path/source_timezone/reset_at_utc/safety_margin_seconds` | A | **In R6 geschlossen:** Gleichnamige Felder in `InvocationFailurePayload` plus `resume_at_utc`; Schreiber: Quota-Parser/Scheduler. Recordet werden sowohl geparste Herkunft (`parse_path`, `source_timezone`, Reset) und Marge als auch das berechnete Ziel. Der Domainvalidator verlangt `resume_at_utc = reset_at_utc + safety_margin_seconds`. |
| `invocation_failures[*].auto_resume_count/automatic_resume/diff_fingerprint` | A | **In R6 geschlossen:** Gleichnamige Felder in `InvocationFailurePayload` plus `decision_at_utc` und `retry_delay_seconds`; Schreiber: Retry-/Resume-Policy. Netzwerkziele müssen Entscheidung + Delay entsprechen. Genau ein vorausliegender Failure-Record darf den Halt deterministisch in den Mirror projizieren; mehrdeutige oder fehlende R6-Records stoppen. |
| `protocol_binding.mode/schema/transports` | C | Der erste akzeptierte `ArtifactRecord.schema_version == "2"` legt `mode=structured-v2` und `schema_version=2` fest. Das geschlossene Schema 2 erzwingt für `AgentResultPayload.transport_schema` den Wert `native-codex-v2` und für `ReviewPayload.transport_schema` `native-claude-review-v2`; andere Transporte sind in diesem Präfix unzulässig. |
| `protocol_binding.codex_profile/claude_profile` | A | **In R1 geschlossen und im R1-Nachzug rollenschlüssig:** `RunProfilePayload.implementer.model/effort` und `reviewer.model/effort`; Schreiber: erster strukturierter Checkpoint aus CLI-/Taskdefault vor dem ersten Providerstart. Die Providernamen bleiben nur im State-v3-Mirror, nicht im Recordfeldnamen. `ProviderAttemptPayload` bestätigt die Bindung je Aufruf. |
| `ContractResult.red_state_followup_slice` in `runtime_history.reviews/latest_claude_review` | A | **In R7 geschlossen:** `StepContract.red_state_followup_slice` wird über `NativeReviewContext` in `ContractResult` und den approved, fingerprintgebundenen `ReviewPayload` getragen; Schreiber: `persist_native_review_contract()`. Eine vollständige fehlgeschlagene Validation ist damit nur mit benannter Folgeslice autorisierbar; ein Commit ohne diesen Record bleibt abgewiesen. |
| `ContractResult.test_files` | A | **In R7 geschlossen:** `ReviewPayload.test_files`; Schreiber: `persist_native_review_contract()` aus dem exakten `NativeReviewContext`. Replay und Mirrorvergleich bewahren die sortierte Pfadliste. |
| `ContractResult.pre_mortem` | A | **In R7 geschlossen:** `ReviewPayload.pre_mortem`; Schreiber: `persist_native_review_contract()`. Approvalpolicy und Audit lesen den reviewer-eigenen Text. |
| `ContractResult.anchors` | A | **In R7 geschlossen:** genau ein `ReviewAnchorPayload` mit allen Anchorfeldern je Review, über `review_record_id`, logische ID und Fingerprint gebunden; Schreiber: `persist_native_review_contract()`. |
| `ContractResult.stop_request` | A | **In R7 geschlossen:** `ReviewPayload.stop_request.rule_id/rationale/remediation_paths`; Schreiber: `persist_native_review_contract()`. Native STOP-Ergebnisse müssen die Pfadliste explizit liefern. |
| `ContractResult.validation` | A | **In R7 geschlossen:** `ReviewValidationBindingPayload.review_record_id/attestation_record_id`; Schreiber: `persist_native_review_contract()` nach der Attestation und nach dem Reviewrecord. Replay löst die R8-Contentrecords ohne Mirror auf. |
| `ContractResult.evidence.dimensions/largest_residual_risk/break_condition` | A | **In S4a geschlossen:** `ReviewPayload.review_evidence` mit drei gleichnamigen Feldern; Schreiber: `persist_native_review_contract()`. Neue Records schreiben das alte Stringfeld nie. |
| `ValidationRecord.output` | A | **In R8 geschlossen:** `ValidationContentPayload.outputs[*].command/result_record_id/output_bytes/raw_stdout/raw_stderr/compact_output`; Schreiber: Validator unmittelbar vor `ValidationAttestationPayload`. Die exakten UTF-8-Bytes liegen in laufgebundenen SHA-256-Blobs; Reviewpacket, Audit, Recovery und Diagnose lesen sie über den Recordverweis. |
| `ValidationAttestation.output_digest` | A | **In R8 geschlossen:** `ValidationContentPayload.digest_format/raw_stdout/raw_stderr` plus `ValidationAttestationPayload.output_digest/content_record_id`; Schreiber: Validator. `validation-matrix-v1` reproduziert exakt die vor R8 verwendete kanonische Aggregation ungekürzter Ausgaben; der Digest der kompakten Recordausgabe ersetzt sie nicht. |
| `runtime_history.latest_claude_review` als Aggregat | A | **In R7 entsperrt:** kein zweiter Aggregatrecord; `project_latest_review(..., work_unit_id)` projiziert ausschließlich für die angegebene Work-Unit aus `ReviewPayload`, `ReviewAnchorPayload`, `ReviewValidationBindingPayload`, Finding-Transitionen und den zugehörigen R8-Contentrecords; Schreiber sind die jeweiligen Review-, Finding- und Validierungspersistenzen. |
| `runtime_history.codex_final_report` und weitere rohe Agenttexte | A | **In R8 geschlossen:** `ProviderContentPayload.response_sha256/content_bytes/content_kind/blob/round_number` plus Rolle, Work-unit, Operation und Request; Schreiber: Providerabschluss nach nativer Schema-/Domainannahme und vor `AgentResultPayload`/`ReviewPayload`, Cache und Mirror. Recovery, Abschlussbericht und Folgeprompt lesen die exakten kanonischen Bytes. Nur ein `ready=true`-Abschlussresultat besitzt `content_kind=final_report`; `ready=false` und Stop bleiben `agent_result` ohne Final-Report-Mirror. R6 bleibt unverändert: nicht angenommener Failure-Rohtext ist kein semantischer Recoveryfakt und wird ausschließlich als Redaktionsmarker mit Digest und Bytelänge recordet. |
| `runtime_history.active_review_packet` | A | **In R8 geschlossen:** `ReviewPacketPayload.fingerprint/manifest/diff_coverage_sha256/content_bytes/blob`; Schreiber: `build_review_packet()` vor Providerstart und Mirrorwrite. Recovery und Providerrequest lesen die exakt gebundenen, lokal erzeugten kanonischen Bytes; die materialisierte Paketdatei besitzt keine Autorität. |
| sonstige `runtime_history`-Event-/Auditfelder | A | **In R9 geschlossen:** `WorkflowEventPayload.event_kind/work_unit_id/slice_id/round_number/record_refs` bindet Transition, Validation und Review an genau einen früheren, fingerprintgleichen fachlichen Record; Schreiber: `persist_native_review_contract()`, `persist_validation_attestation()` und `_append_workflow_transition()`. `record_refs` wiederholt keinen fachlichen Inhalt. Der reine Darstellungswert `runtime_history.events` ist aus `WorkflowHistory.to_dict()/from_dict()` entfernt, alle Leser projizieren ihn aus Replay, und `WorkflowState` weist seine Rückkehr ab. |

#### R9-`runtime_history`-Inventur

Die Inventur stammt aus der aktuellen `WorkflowHistory.to_dict()`-Form und den
Produktionslesern, nicht aus einer angenommenen Typdefinition des freien
`WorkflowState.runtime_history`-Mappings. Der im R9-Vorbefund genannte Schlüssel
`reviews` ist in der aktuellen Serialisierung kein eigener Schlüssel; die
Reviewfolge war ausschließlich im nun entfernten Schlüssel `events` enthalten.

| Persistierter Schlüssel | Gruppe | Heutiger Leser und R9-Entscheidung |
|---|:---:|---|
| `work_unit_id` | bereits gedeckt | `_history()` und `_persisted_histories()` ordnen Current/Archive einer Work-unit zu; R2 projiziert dieselbe Identität aus `WorkflowTransitionPayload.work_unit_id`. |
| `findings` | bereits gedeckt | Policy, Folgeprompt und Audit lesen die Findingmenge; S3 projiziert sie mit `reduce_findings()` aus Finding-Transitionen und Import. |
| `events` | braucht Record, Darstellungsfeld entfällt | `_audit_projection()`, `assert_structured_decision_context()`, `_recoverable_pending_review_finding_gap()` und `_recover_final_review_attestation()` benötigen Reihenfolge und Kontext. `WorkflowEventPayload` recordet nur diese Metadaten mit `record_refs`; `_attach_record_events()` baut die Auditobjekte aus den referenzierten Records. Der serialisierte Schlüssel selbst ist reine Darstellung und entfällt. |
| `attestations` | bereits gedeckt | Validation-Recovery, Reviewbindung, Commit und Audit lesen die Attestierung; R8 bindet die vollständige Evidenz über `ValidationAttestationPayload` und `ValidationContentPayload`. |
| `last_claude_fingerprint` | bereits gedeckt | Review-Deduplikation und Folgepolicy lesen den letzten Fingerprint; R7 projiziert ihn aus dem letzten Reviewrecord derselben Work-unit. |
| `latest_claude_review` | bereits gedeckt | Approval-, Commit-, Folgeprompt- und Auditpolicy lesen das Aggregat; R7 projiziert es aus Review-, Anchor-, Validation- und Findingrecords. |
| `codex_final_report` | bereits gedeckt | Abschlussbericht und Folgeprompt lesen die angenommene Antwort; R8 bindet ihre kanonischen Bytes über `ProviderContentPayload`. |
| `active_review_packet` | bereits gedeckt | Recovery und Providerrequest lesen das Paket; R8 bindet die kanonischen Bytes über `ReviewPacketPayload`. |

Damit passt jeder tatsächlich serialisierte Schlüssel genau in eine der drei
R9-Gruppen. Es bleibt kein offener Gruppe-A-Eintrag und keine als Stopgrund
markierte Tabellenzeile.

#### R9-S4b-Vorbedingung und Präfixnormalisierung

`project_workflow_state()` nimmt ausschließlich ein bereits validiertes
`ArtifactReplayResult` entgegen und erzeugt daraus ein echtes, erneut durch
`WorkflowState.from_dict()` validiertes State-v3-Dokument. Es liest weder
`state.json` noch Checkpoints, Blobs, Dateisystem oder Uhr. Die beiden volatilen
Statezeiten werden auf erste und letzte Recordzeit normalisiert. Der weiterhin
geschriebene `runtime_history`-Mirror wird für den kanonischen Vergleich auf
seine Recordverweise normalisiert; eingebettete Review-, Validation- und
Providerinhalte bleiben durch R7/R8 rekonstruierbar, werden aber nicht zu einer
zweiten Authority im Eventrecord.

Der providerfreie Akzeptanzfall durchläuft jeden Recordpräfix einer Journey mit
zwei regulären Slices, einem Gate-Halt und Resume, Validation und Review sowie
einer Correction-Work-unit mit zwei Runden. Nach vollständigen
Domain→`WorkflowEvent`-Paaren muss die Projektion zweimal bytegleich sein;
Crashpräfixe zwischen Domainrecord und Eventrecord sowie zwischen Status- und
Gate-Transition sind unvollständig und werden fail-closed abgewiesen. Für
**jeden** akzeptierten Präfix wird parallel ein echter `WorkflowState` über die
produktiven State-Mutatoren fortgeschrieben und — nur um volatile Zeiten
bereinigt — kanonisch gleich gegen die Recordprojektion geprüft. Vor der
History-Referenznormalisierung werden die im tatsächlichen Mirror verbliebenen
Review- und Attestierungsaggregate gegen die Records validiert. Zusätzlich
weist ein Objektgleichheitstest nach, dass `_attach_record_events()` die
entfernten Auditereignisse, Attestierungen und den letzten Review exakt aus den
Recordreferenzen rekonstruiert. Damit ist die unmittelbare S4b-Vorbedingung
nachgewiesen; der Authority-Cutover selbst bleibt Nicht-Ziel dieses Slices.

#### R8-Inhaltsentscheidungen, Bindung und Größenmessung

R8 verwendet keinen pauschalen Umgang mit Rohtext, sondern vier ausdrücklich
getrennte Entscheidungen:

1. `ValidationRecord.output` bleibt als kompakte Projektion erhalten; zusätzlich
   bindet der Contentrecord die **exakten rohen stdout-/stderr-Bytes** und die
   kompakte Ausgabe jeweils über einen externen Blobverweis. Eine Kürzung würde
   Diagnosebytes verlieren und findet nicht statt.
2. `ValidationAttestation.output_digest` behält die Definition
   `validation-matrix-v1` unverändert. Der Test vergleicht die frühere
   kanonische JSON-/SHA-256-Berechnung direkt mit der aus den Blobstreams
   reproduzierten Berechnung. Damit wird die ungekürzte Evidenz weder durch den
   Kompaktdigest ersetzt noch still neu definiert.
3. `ProviderContentPayload` bewahrt die **exakte kanonische native JSON-Antwort**
   nur für bereits schema- und domaingültig angenommene Agentresultate. Diese
   Bytes sind für requestgebundene Recovery semantisch erforderlich. Das ist
   die sichtbare, getestete Abgrenzung zu R6: rohe Fehlerdiagnosen werden nicht
   als Ergebnis angenommen und bleiben ausschließlich redigierter Marker plus
   Digest und Bytezahl. Der Contentrecord ist zusätzlich an die exakte
   `round_number` gebunden; ein Crash vor dem Decisionrecord kann deshalb auch
   ab Runde 2 nur den Inhalt derselben Invocation wiederaufnehmen.
4. `ReviewPacketPayload` bindet die **exakten lokal erzeugten kanonischen
   Paketbytes**. Diffinhalte werden weder redigiert noch gekürzt, weil genau
   diese Bytes den Providerinput und die spätere Recovery bestimmen.

Alle vier Inhalte liegen unter `.orchestrator/artifacts/<run-id>/blobs/` und
werden durch SHA-256 plus exakte Bytelänge aus einem Record desselben Runs
gebunden. Fehlende, verlinkte, längenabweichende, digestabweichende oder bei
Reviewpaketen nicht-kanonische Ziele stoppen Store/Replay fail-closed. Ein
Validation- oder Provider-Contentrecord darf als genau ein letztes
Record-ahead-Crashsuffix vorliegen; jedes ältere ungebundene Contentrecord und
jede Entscheidung ohne den exakt früheren Contentrecord stoppt. Ketten vor R8
werden bei Resume ausdrücklich nicht ergänzt, sondern fail-closed abgewiesen.
Validation-Recovery ist an die konkrete Attestierungs-ID einschließlich
Retryversuch gebunden und wird für Planvertragsprüfungen nicht verwendet.
Mehrere Reviewpacketrecords derselben Work-unit bleiben historische Autorität;
der State-v3-Mirror `active_review_packet` wird ausschließlich gegen das
letzte Packet dieser Work-unit verglichen. Mehrere Final-Report-Contentrecords
derselben Work-unit sind dagegen mehrdeutig und stoppen fail-closed.

Der providerfreie Skalierungstest erzeugt echte Reviewpakete mit wachsenden
Diffzielen von **64 KiB, 256 KiB und 1 MiB**. Während die kanonischen
Paketbytes um mehr als Faktor 12 wachsen, differieren die drei
JSON-Record-Envelopes um weniger als **32 Byte** und bleiben jeweils kleiner
als ein Zwanzigstel bereits des kleinsten Pakets. Der JSON-Kettenscan trägt
damit nur Verweise; Blobprüfung und Rekonstruktion lesen jeden gebundenen
Inhalt linear genau einmal. Der RP-Appendindex scannt weder alte Recordbytes
noch alte Blobs pro Append erneut. Ein zweiter Test ruft für alle drei Größen
den echten `ArtifactStore.load_chain()` jeweils fünfmal auf, instrumentiert die
gelesenen Blobbytes und misst die Medianlaufzeit. Pro Vollscan entspricht die
gelesene Bytezahl exakt einmal der Paketgröße; der aus kleinster und größter
Messung berechnete Laufzeitexponent muss unter **1,25** bleiben.
R7 ergänzt die zuvor fehlende Negativkontrolle: Dieselbe Envelope-Schranke wird
zusätzlich mit hypothetisch wiedereingebetteten Paketbytes gespeist und muss
dann rot werden. Damit erkennt der Guard die konkrete Inlining-Regression.

Die in R8 neu inventarisierten Record-/Mirror-Grenzen lauten:
`validation content has no complete state-v3 counterpart`,
`validation output or digest differs from state-v3`,
`final-report content differs from state-v3`,
`final-report bytes differ from state-v3`,
`active review packets differ from state-v3` und
`active review packet bytes or metadata differ from state-v3`. Die
Runtimebindungen ergänzen die dokumentierten Divergenzen
`native provider content digest differs from its record`,
`native agent content digest differs from its result binding`,
`native reviewer content digest differs from its review binding`,
`validation recovery result differs from its content` und
`native review persistence differs from its exact review context`.
Von den R7-Mirrorgrenzen bleiben nach R9
`review contract projection is ambiguous`,
`latest review mirror has no aggregate field` und
`latest review differs from its record projection`. Die früheren Vergleiche
`review contract mirror is ambiguous`, `review contracts differ from state-v3`
und `review contract fields differ from state-v3` entfielen, weil Reviewereignisse
nicht mehr aus einem eingebetteten Eventmirror rekonstruiert werden.
R9 ergänzt die fail-closed Resumegrenze
`structured-v2 run has no complete workflow event prefix`.
`require_workflow_event_prefix()` besitzt sechs inventarisierte
Vergleichsausdrücke und ist wie die übrigen Recoveryprädikate per AST-Digest
gebunden. Der produktive Record→Audit-Leser `_attach_record_events()` ist mit
16 Vergleichsausdrücken ebenfalls vollständig per AST-Digest gebunden. Das
quellgebundene Inventar umfasst nach Entfernung der drei
redundanten Review-Event-/Mirrorvergleiche **43** `mismatch(...)`-Aufrufe;
`resolve_resume_state()` besitzt 117,
`review_payload_matches_result()` 13 und
`WorkflowPersistence.persist_native_implementer_contract()` 11 inventarisierte
Vergleichsausdrücke. Der nun record-gegen-Mirror gerichtete
Reviewguard `assert_structured_decision_context()` besitzt **11** inventarisierte
Vergleichsausdrücke; seine vier zusätzlichen Vergleiche binden die getrennt
ermittelten letzten Reviewrecords an das tatsächlich serialisierte Mirroraggregat.

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

#### R5-Suitelaufzeit

Der vollständige WSL-Lauf vom 31. August 2026 mit
`python3 -m pytest tests/ -q` ist grün: **1308 passed in 153,31 s**.
Gegenüber der im R5-Auftrag festgehaltenen R4-Baseline von **1297 passed in
147 s** sind das sieben zusätzliche Akzeptanzfälle und **12,91 s** mehr
Pytest-Laufzeit. Die Providernamen-Baseline, Schema-/Protokollversion 2 und das
Inventar der `_recoverable_*`-Sonderfälle blieben unverändert.

#### R6-Suitelaufzeit

Der vollständige WSL-Lauf vom 31. August 2026 mit
`python3 -m pytest tests/ -v` ist grün: **1315 passed in 155,98 s**.
Gegenüber der R5-Baseline von **1308 passed in 159 s** sind das sieben
zusätzliche Akzeptanzfälle bei praktisch unveränderter Laufzeit. Die
Providernamen-Baseline, Schema-/Protokollversion 2 und das Inventar der
`_recoverable_*`-Sonderfälle blieben unverändert.

Das erste Claude-CLI-Review mit Opus 5 auf Max fand eine unbeabsichtigte
Kopplung der automatischen Fortsetzung an die tiefste S1-Klasse. Nach der
Korrektur belegt ein nativer, aus `AgentOutputError` gekapselter Quotafall die
unveränderte Policy bei gleichzeitig vollständig recordeter S1-Provenienz.
Die abschließende Opus-5-Max-Korrekturrunde wurde ohne Findings freigegeben.

#### R8-Suitelaufzeit

Der vollständige WSL-Lauf vom 31. August 2026 mit
`python3 -m pytest tests/ -v` ist nach der Review-Korrekturrunde grün:
**1327 passed in 190,06 s**. Gegenüber der exakten R6-Messung von
**1315 passed in 155,98 s** sind das zwölf zusätzliche Akzeptanzfälle und
**34,08 s beziehungsweise 21,8 %** mehr Pytest-Laufzeit; gegenüber der im
R8-Auftrag gerundeten 160-s-Baseline beträgt der Anstieg **30,06 s
beziehungsweise 18,8 %**. Der erste vollständige R8-Kandidatenlauf lag bei
1321 Tests und 165,11 s; die beiden vollständigen Läufe nach der adversarialen
Review-Härtung lagen reproduzierbar bei 191,98 s (mit elf anschließend
behobenen Dry-run-Fehlern) und 190,06 s (vollständig grün). Die gesonderte
Vollscanmessung über 64 KiB, 256 KiB und 1 MiB liest jeden gebundenen Blob pro
Scan exakt einmal und bleibt linear in den gelesenen Bytes; sie zeigt deshalb
keine durch Recordgröße wieder eingeführte überproportionale RP-Kostenkurve.
Der absolute Suiteanstieg ist als Abnahmebefund festgehalten und darf nicht als
Beleg für konstante Kosten umgedeutet werden. Die Providernamen-Baseline,
Schema-/Protokollversion 2 und das Inventar der `_recoverable_*`-Sonderfälle
blieben unverändert.

#### R7-Suitelaufzeit

Der vollständige WSL-Lauf vom 31. August 2026 mit
`python3 -m pytest tests/ -q` ist nach den externen Reviewkorrekturen grün:
**1333 passed in 173,90 s**. Gegenüber der exakten R8-Baseline von
**1327 passed in 190,06 s** sind das sechs zusätzliche Akzeptanzfälle bei einer
um **16,16 s beziehungsweise 8,5 %** niedrigeren von Pytest ausgewiesenen
Laufzeit. Die Providernamen-Baseline, Schema-/Protokollversion 2 und das
Inventar der `_recoverable_*`-Sonderfälle blieben unverändert.

#### R9-Suitelaufzeit

Der vollständige WSL-Lauf vom 31. August 2026 mit
`python3 -m pytest tests/ -v` ist nach den externen Reviewkorrekturen grün:
**1344 passed in 197,22 s**. Gegenüber der im R9-Auftrag festgehaltenen
R7-Baseline von **1333 passed in 177 s** sind das elf zusätzliche
Akzeptanzfälle und **20,22 s beziehungsweise 11,4 %** mehr von Pytest
ausgewiesene Laufzeit. Die zusätzlichen Reviewregressionen prüfen die
Mirror-Gleichheit an jedem akzeptierten Präfix, die exakte
Auditereignisrekonstruktion einschließlich einer in den Final-Review
übertragenen Slice-Attestierung und die Finding-Herkunft über mehrere
Reviewrunden. Die Providernamen-Baseline, Schema-/Protokollversion 2 und das
Inventar der `_recoverable_*`-Sonderfälle blieben unverändert.

#### R1-Nachzug-Suitelaufzeit

Der vollständige WSL-Lauf vom 31. August 2026 mit
`python3 -m pytest tests/ -v` ist nach der Opus-Korrekturrunde grün:
**1348 passed in 207,42 s**. Gegenüber der exakten R9-Messung von
**1344 passed in 197,22 s** sind das vier zusätzliche Akzeptanzfälle und
**10,20 s beziehungsweise 5,2 %** mehr von Pytest
ausgewiesene Laufzeit; gegenüber der im Auftrag gerundeten 200-s-Baseline
beträgt der Anstieg **7,42 s beziehungsweise 3,7 %**. Die neuen Abnahmepunkte
binden die rollenschlüssige Profilform, die vollständige Runbindung, den
schreibfreien Vor-Baseline-Poisonpfad und die produktive Striktheit der
öffentlichen Projektionsfunktionen. Schema-/Protokollversion 2 und das Inventar
der `_recoverable_*`-Sonderfälle bleiben unverändert.

#### Entscheidung zu Schema 2 und Bestandsrecords

R1 ergänzt `RunIdentityPayload` und `RunProfilePayload` additiv. Beide werden
beim ersten strukturierten Checkpoint vor dem ersten Workflowdispatch und damit
vor jedem Providerstart geschrieben. Replay projiziert die beiden typisierten
Payloads ohne State- oder Dateisystemzugriff; Resume vergleicht die
verpflichtenden Runrecords fail-closed mit dem State-v3-Mirror. Der R1-Nachzug
weist Ketten ohne einen der beiden Recordtypen mit `RECORD-MISSING` ab. Das
Profil ist im Record ausschließlich über die Rollen `implementer` und
`reviewer` mit jeweils `model` und `effort` benannt; die State-v3-Projektion
behält ihre bisherigen Mirrorfeldnamen.
Da jede akzeptierte Laufkette nun einen `RunIdentityPayload` enthält, greifen
die R7-/R8-Authorityprüfungen für native Decisions, Reviewcontracts und deren
Content bei produktiver Defaultprojektion unbedingt. Nur explizit als
Reducer-Fixture gekennzeichnete Tests dürfen diese späteren Authorityschichten
abschalten; die öffentlichen Projektionsfunktionen bleiben mit produktiver
Striktheit getestet.
`protocol_binding.mode/schema/transports` bleibt Gruppe C und erhält keinen
eigenen Record. Schema- und Transportversionen bleiben unverändert bei 2.

Die Providernamen-Ratsche sinkt für `src/artifact_models.py` von
`{codex: 22, claude: 18}` auf den Vor-R1-Stand `{codex: 14, claude: 10}`.
Auch das betroffene Artifact-Schema sinkt von `{16, 16}` auf `{12, 12}`;
es findet somit keine Verlagerung der entfernten Recordfeldnamen statt.

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

**Quelle entfernt.** Die unten zitierten Laufverzeichnisse unter `.orchestrator/` wurden am 1. September 2026 nach Freigabe des Betreibers gelöscht; alte Ketten sind seit der Entscheidung gegen Rückwärtskompatibilität irrelevant. Der genannte Record ist damit nicht mehr nachprüfbar. Der Befund selbst bleibt gültig und ist durch die S4a-Entscheidung und ihre Tests konserviert.

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

R7 ergänzt denselben Schema-2-Vertrag um die nun verpflichtenden
`ReviewPayload.test_files/pre_mortem/stop_request`-Properties sowie die
Recordtypen `ReviewAnchorPayload` und `ReviewValidationBindingPayload`.
Jeder neue Reviewrecord wird aus dem exakten `NativeReviewContext` geschrieben,
danach folgen genau eine Anchorliste und genau eine Bindung an die bereits
vorhandene fingerprintgleiche Validationattestierung. Resume verlangt beide
Komponenten für jeden Review; Vor-R7-Präfixe werden deshalb gemäß Auftrag
fail-closed abgewiesen und nicht aus State-v3 ergänzt. Der frühere
S4a-Hinweis zur Lesbarkeit alter Einzelrecords beschreibt nur deren damalige
Payloaddekodierung und ist keine Fortsetzungszusage nach R7.

`project_review_contracts()` rekonstruiert Validationausgaben über die
R8-Blobs, Findings über den kanonischen Reducer sowie Testdateien, Pre-Mortem,
Anchors, Stopursache und Red-State-Folgeslice direkt aus dem akzeptierten
Recordpräfix. `project_latest_review(..., work_unit_id)` wählt daraus ausschließlich den
letzten Review; ein `latest_claude_review`-Record existiert bewusst nicht.
Ein bewusst auf `null` invalidierter State-v3-Latest-Mirror darf dabei ältere
Audit-Events behalten; ein nichtleerer Latest-Mirror muss weiterhin exakt dem
letzten Event derselben Work-Unit entsprechen. Resume akzeptiert ausschließlich
am Kettenende Review, Review+Anchor oder Review+Anchor+Validationbindung mit
noch unvollständigen Finding-Transitionen als pending. Die requestgebundene
Providerantwort ergänzt diesen Suffix idempotent; dieselben Lücken in der
Kettenmitte bleiben fail-closed.
Audit-Markdown bleibt eine Projektion: Der bestehende Grenztest verändert den
verwalteten Reviewabschnitt zweimal und belegt identische Repository-
Fingerprints, Pfadmengen (Guardinput) und kanonische Review-Diffs. Damit bleibt
auch das daraus erzeugte Reviewpaket unverändert.

R5 ergänzt `GateTransitionPayload` und `GateDecisionPayload` additiv in Schema
2. Jede nicht ausschließlich aus einem Finding-Import bestehende fortsetzbare
Kette muss nun für jede Work-unit den aktuellen Gate-/Testscope-Präfix sowie
für jede Mirrorentscheidung eine Bindung an einen früheren endgültigen
`GatePayload` besitzen; Vor-R5-Ketten werden fail-closed abgewiesen und niemals
aus `state.json` nachgerüstet. Der Schreiber persistiert Pending, Clear, Resume
und aktive Testbindung jeweils vor dem nächsten Gate-/Dispatch-/Audit-Leser.
`GateDecisionRecord.decided_by` und `.decided_at` sind aus State-v3 und CLI
entfernt. Die Auditprojektion zeigt stattdessen `GatePayload.authority` und
`ArtifactRecord.created_at` des referenzierten Gate-Records; diese Recordzeit
wird ausdrücklich nicht als identisch mit dem entfernten Mirrorzeitpunkt
behauptet. Mirror, Schema-/Protokollversion 2 und die Providerrollen bleiben
ansonsten unverändert.

R6 ergänzt `InvocationFailurePayload` additiv in demselben Schema 2. Der
klassifizierte Failurepfad übernimmt `failure_class` und `diagnostic_code`
direkt aus `src/error_classification.py`, berechnet die bestehende Policy und
appendet anschließend den vollständigen Fakt, bevor State-Status, Gate,
Fortsetzungszähler oder Wait verändert werden. Ein unbeschränkter Providertext
wird weder in den Record noch in den neu geschriebenen Produktionsmirror
übernommen: Persistiert wird ausschließlich ein höchstens 128 Zeichen langer
Redaktionsmarker mit SHA-256 und exakter UTF-8-Bytezahl. Quota-Records tragen
Quelle, Zeitzone, Reset und Marge sowie das daraus berechnete Ziel; Netzwerk-
Records tragen Entscheidungszeit, Delay und Ziel. Die Domainvalidierung prüft
beide Herleitungen.

Die S1-Klasse ist dabei Provenienz und kein zweites Retry-Gate. Native Adapter
können einen als Quota oder Netzwerk klassifizierten Aufruf mit einer tieferen,
deterministischen `__cause__`-Klasse kapseln. Der Record bewahrt diese
Divergenz, während die unveränderte Retry-Policy weiterhin aus dem bereits
klassifizierten `failure_kind`, ihren Grenzwerten und dem Fingerprint folgt.
Damit dokumentiert R6 die S1-Klasse, ohne bisherigen Quota- oder
Structured-Output-Fällen automatische Fortsetzung zu entziehen.

Replay bewahrt die Failurepayloads in Kettenreihenfolge, bindet sie an den
letzten aktiven Work-unit-/Step-Übergang und verlangt, dass spätere Quota- und
Transient-Transitionen exakt auf denselben Failure zurückzeigen. Fehlt zu
einem vorhandenen Failure-Mirror der R6-Record, stoppt Resume fail-closed. Das
einzige zulässige Crashsuffix ist genau ein Record vor seinem Mirror; daraus
wird der Halt deterministisch projiziert. Teilweise bereits gelandete
Workflow-, Gate- und Retry-Transitionen werden jeweils gegen den passenden
Vorher-/Nachherzustand geprüft. Ein zweiter Append derselben Invocation ist
idempotent, ein zweiter Providerdispatch aus diesem Crashfenster findet nicht
statt. Die Treibersenke stoppt außerdem mit
`invocation failure work unit differs from the active workflow`, wenn der
Record nicht zur aktuell gebundenen Work-unit gehört.

### S4a-Schnittvorschlag und Abschluss der Gruppe-A-Bündel

1. **Runidentität sowie frühe Protokoll-/Profilbindung.** `task_file`, `branch`,
   `branch_base`, `execution_mode`, `audit_report_path` und beide Agentprofile.
   Module: `workflow_state.py`, `orchestrator.py`, `artifact_models.py`,
   `artifact_bridge.py`, Schema und Resume/Migration. Muss vor allen weiteren
   Bündeln landen, weil bereits der erste Dispatch diese Fakten liest.
2. **Cursor und Work-unit-/Slice-Status.** Aktuelle IDs/Steps, beide Statusarten,
   `codex_return_count` und `max_codex_returns`. Module: `workflow_state.py`, `workflow.py`,
   `artifact_models.py`, `artifact_replay.py`, `artifact_resume.py`. Benötigt
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
   `error_classification.py`, `agent_runtime.py`, `workflow_state.py`,
   `workflow.py`, `orchestrator.py`, Replay/Migration. Benötigt Cursor, Profile
   und Side-effect-Ledger.
7. **Verbleibende Review-Contractfelder.** `test_files`, `pre_mortem`, `anchors`,
   `stop_request`, Validationbindung und die daraus entstehende
   `latest_claude_review`-Projektion. Module: `contracts.py`,
   `native_review_contract.py`, `artifact_models.py`, `artifact_bridge.py`,
   `artifact_replay.py`, `audit_trail.py`. Kann nach Bündel 1 bis 3 erfolgen;
   Validationbindung benötigt Bündel 8.
8. **Blob-/Contentauthority. In R8 geschlossen.** Validationoutput und -digest,
   angenommene rohe Agentresultate und aktive Reviewpackets sind durch
   laufgebundene SHA-256-Blobs an neue Schema-2-Contentrecords gebunden. Module:
   `validation_matrix.py`, `review_packets.py`, `orchestrator.py`,
   Store/Schema/Replay. Die Validationbindung für Bündel 7 ist damit vorhanden.
9. **Restliche Historyprojektion.** Nur die nach Bündel 1 bis 8 noch
   verbleibenden Event-/Auditfelder; normativen Rest recorden, reine
   Darstellungswerte explizit entfernen. Module: `workflow.py`,
   `audit_trail.py`, `artifact_projection.py`, `artifact_resume.py`. Dieses
   Abschlussbündel hängt von allen vorigen ab und liefert die unmittelbare
   S4b-Vorbedingung.

### Nicht im State, aber für Recovery relevante Caches/Side Effects

Requestbundles, `head.json`, Audit-Markdown und Checkpoints bleiben nicht allein
aus dem Recordpräfix rekonstruierbare Hilfsdateien. Seit R8 sind dagegen
angenommene kanonische Agentantworten und Reviewpakete keine Cacheautorität
mehr: Der jeweilige Contentrecord bindet ihre exakten Bytes in einem
laufgebundenen Blob. Rohe Failurediagnosen bleiben gemäß R6 bewusst nur als
Redaktionsmarker plus Digest und Bytezahl erhalten. Ein Digest allein beweist
vorhandene Bytes, rekonstruiert sie aber nicht; daher darf S4 einen Inhalt nur
dann als wegwerfbaren Cache behandeln, wenn die kanonischen Bytes aus Records
plus versionierter Policy neu gebaut werden können, andernfalls ist der
R8-Blob-/Contentrecord zwingend. Git-Commits, Provideraufrufe und
Queuebewegungen sind keine Caches und brauchen je eine Intent-/Resultat- oder
Reconciliation-Regel.

## S4b-Cutover-Abschluss

### Endklassifikation aller 26 S2-Kanten

| Kante | Endstatus | Begründung nach dem Cutover |
|---|---|---|
| A01 | bleibt bewusst | Schema, Runprofil, Reducerbindung und physische Kette bestimmen erst den akzeptierten Präfix. |
| A02 | bleibt bewusst | Cross-run-Handoff revalidiert Quelle, Planbindung und Taskbytes. |
| A03 | entfällt | Taskvertrag einschließlich PLAN_ONLY-Zielpfad wird ausschließlich aus Records projiziert. |
| A04 | entfällt | Work-unit, Runde, Scope und Findingattribution stammen aus Records. |
| A05 | entfällt | Vorgeschlagener Slice-Plan und freigegebener Plan sind Record-Fakten; die Statekopie ist Cache. |
| A06 | entfällt | Gatezustand und Entscheidungen werden aus Gate-Records projiziert. |
| A07 | entfällt | Findingstatus und offene Menge kommen allein aus dem Finding-Reducer. |
| A08 | entfällt | Validationzustand und Inhalte kommen aus Attestation-/Contentrecords und Blobs. |
| A09 | entfällt | Quota-Pause und Zielzeit werden aus Failure-/Quota-Records projiziert. |
| A10 | entfällt | Retryfolge und Zielzeit werden aus Failure-/Retry-Records projiziert. |
| A11 | entfällt | Messung, Bootstrap und Preflight sind vollständige Record-Fakten. |
| A12 | bleibt bewusst | ResumeCheck bindet eine zeitliche Aussage unveränderlich an den Vorgängerhead. |
| A13 | bleibt bewusst | Recordreferenzen auf Attestierungen, Reviews und Bindings müssen kausal gültig bleiben. |
| A14 | bleibt bewusst | Der Git-Commit ist irreversibel und bleibt über Intent, Resultat und Reconciliation geschützt. |
| A15 | entfällt | Terminalzustand wird aus Completion- und Binding-Records projiziert. |
| A16 | bleibt bewusst | Der finale Audit-Git-Commit ist ein irreversibler externer Side Effect. |
| B01 | bleibt bewusst | Atomarer Append, Idempotenz und durable-but-reported-failed sind Store-Kausalität. |
| B02 | bleibt bewusst | Finding-Export muss den vollständig akzeptierten Quellpräfix belegen. |
| B03 | bleibt bewusst | Finding-Import revalidiert fremde Kette und veröffentlichte Taskbytes. |
| B04 | bleibt bewusst | Providerstart kann extern unbestimmt sein und benötigt dreiwertige Reconciliation. |
| B05 | wird generisch | Materialisierte Request-/Response-Dateien sind aus gebundenem Content rekonstruierbare Caches. |
| B06 | wird generisch | Reviewpaket und Rohantwort werden aus Content-/Reviewrecords materialisiert; fachliche Sonderfälle entfallen. |
| B07 | entfällt | Review-, Finding- und Attestation-History ist direkte Recordprojektion. |
| B08 | bleibt bewusst | Handoff-Dateischreibung und Queuebewegung sind externe Side Effects mit eigener Bindung. |
| B09 | wird generisch | `state.json` und Checkpoints tragen denselben Head-, Reducer- und Projektionsdigest-Abgleich. |
| B10 | wird generisch | Terminale Datei-/Watchprojektionen sind Cache; ihre externen Queue-/Git-Bindungen bleiben separat bewusst. |

### Cachevertrag und statischer Leser-Nachweis

`state.json` und strukturierte Checkpoints verwenden
`workflow-state-projection-v1`. Der Umschlag bindet exakt `record_head_id`,
`reducer_version`, `projection_digest` und das projizierte `state`-Dokument.
Fehlt der Cache oder ist eine dieser Bindungen fremd, veraltet oder manipuliert,
wird er aus der unveränderten Recordkette neu geschrieben. Eine beschädigte,
unvollständige, unbekannte oder widersprüchliche Kette stoppt dagegen
fail-closed.

Auch der interne Pfadabgleich bleibt fail-closed:
`workflow projection checkpoint path differs from its cursor` stoppt, falls der zurückgegebene
Checkpointpfad nicht exakt zur recordprojizierten Work-unit-, Slice- und
Round-Identität passt.

Der statische Test der Lesestellen belegt: `resolve_resume_state()` liest aus
einem übergebenen `WorkflowState` nur `run_id` als Locator. Sämtliche
strukturierten Dispatch-, Gate-, Resume- und Checkpointentscheidungen laden die
Kette über `resolve_resume_state()`; der einzige direkte
`load_workflow_state()`-Aufruf im Produktionsorchestrator dient der expliziten
Force-Replacement-Autorisierung, nicht einer Workflowentscheidung. Legacy-v3
ohne strukturierte Kette wird mit `UNSUPPORTED-PROTOCOL` abgewiesen.

### Versionsentscheidung

Der Cutover erhöht weder Protokoll- noch Recordschema-Version:
`structured-v2` und `schema_version = 2` bleiben bestehen. R9 hatte bereits
dieselbe vollständige Zustandsprojektion und dieselbe Bedeutung jedes
Bestandsrecords definiert; S4b wählt diese Projektion als alleinige Autorität
und interpretiert keine vorhandenen Recordbytes neu. Die innerhalb der
Schema-2-Serie additive Vollständigkeitsbindung schreibt nun
`RunProfilePayload.reducer_version = structured-v2-schema-2-state-v3-v1`, den
PLAN_ONLY-`work_plan_path` im Taskrecord sowie vorgeschlagene `slice_plan`-Daten
im nativen AgentResult. Bestandskompatibilität ist gemäß Arbeitsplan nicht
zugelassen. Eine Kette mit fehlender oder fremder Reducer-Version scheitert am
geschlossenen Schema beziehungsweise Domainmodell. Eine spätere Änderung der
Recordbedeutung oder Reducer-Semantik benötigt ausdrücklich eine neue
Semantik-/Protokollversion.

### S4b-Abnahmelauf

Der vollständige providerfreie WSL-Lauf nach dem Cutover ergibt
`1368 passed in 194.75s` (Gesamtprozesszeit `195.27s`). Gegenüber der
Zwischenlauf-Baseline `1348 passed in 210s` sind das 20 zusätzliche
Akzeptanz- und Regressionstests bei vollständig grüner Suite.

## Konsequenzen für S3 und S4

1. R1 bis R9 haben Records oder explizite Ableitungsregeln für sämtliche
   früheren Stopgründe geschaffen. Cursor/Status, Slice-Startgrenze,
   Side-effect-Ledger, Pending-Gate, frühe Protocol-/Profilbindung und die
   Historyereignisse werden nicht aus dem Mirror übernommen.
2. R9 weist nach, dass ein leerer State aus jedem vollständigen akzeptierten
   Präfix deterministisch neu projiziert werden kann; die bereits recordeten
   Commit-/Provider-/Queue-Crashfenster besitzen explizite Reconciliation.
   S4b hat auf dieser Vorbedingung aufgesetzt.
3. Die spezialisierten Mirrorvergleiche und
   `_recoverable_*`-Ausnahmen sind entfallen. Es bleiben Recordschema/Kette/Referenzen,
   Providerattempt-Kausalität, Cross-run-Handoff und externe Side-effect-
   Reconciliation. Alle Datei-/Historykopien gehen in einen generischen
   Cacheintegritätsabgleich auf.

Diese Aussagen beschreiben seit S4b den umgesetzten Cutover.
