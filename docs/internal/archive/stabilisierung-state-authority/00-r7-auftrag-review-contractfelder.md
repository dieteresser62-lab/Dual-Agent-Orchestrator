# R7 — Implementierungsauftrag: Verbleibende Review-Contractfelder

Auftrag an: Codex
Review durch: Claude (read-only)
Branch: `feature/state-authority-consolidation`, Vorgänger: `ae580a8` (R8)
Übergeordneter Plan: `00-stabilisierung-arbeitsplan.md`
Grundlage: `docs/internal/stabilisierung-s2-uebergangsmatrix.md`, Bündel 7 des
S4a-Schnittvorschlags

**Ohne Orchestrator.** Testsuite unter WSL: `python3 -m pytest tests/ -v`.
Baseline nach R8 (`ae580a8`): **1327 passed in 194 s**.

## Stehende Regeln

Unverändert gültig seit R2:

1. **Keine Rückwärtskompatibilität.** Ketten ohne die neuen Records werden
   fail-closed abgewiesen.
2. **Die Providernamen-Ratsche wird nicht angehoben.** Recordfelder rollenbasiert.
3. **Kein Cutover.** Der Mirror wird weiter geschrieben.
4. **Kein Cache mit Autorität.**

Bündel 8 ist mit `ae580a8` erledigt; die Validationbindung dieses Slice kann
darauf aufsetzen.

## Umfang — fünf Fakten der Gruppe A plus ein Aggregat

| Statefeld | Zielrecord und Feld |
|---|---|
| `ContractResult.test_files` | `ReviewPayload.test_files` |
| `ContractResult.pre_mortem` | `ReviewPayload.pre_mortem` |
| `ContractResult.anchors` | `ReviewAnchorPayload`-Liste, an den Review-Record gebunden |
| `ContractResult.stop_request` | `ReviewPayload.stop_request.rule_id/rationale/remediation_paths` |
| `ContractResult.validation` | `ReviewValidationBindingPayload.review_record_id/attestation_record_id` |
| `runtime_history.latest_claude_review` | **kein Record** — Projektion, siehe unten |

Schreiber laut Sortierung: durchgängig `persist_native_review_contract()` aus
dem exakten `NativeReviewContext`, die Validationbindung nach der Attestation.

## Das Aggregat bekommt keinen zweiten Record

Die Matrix ist hier eindeutig: `latest_claude_review` wird aus
`ReviewPayload`, `ReviewAnchorPayload`, `ReviewValidationBindingPayload`, den
Finding-Transitionen und den Contentrecords aus Bündel 8 **projiziert**. Ein
eigener Aggregatrecord wäre eine zweite Wahrheit — derselbe Defekt, den S3 für
Findings beseitigt hat.

Die Matrix hält außerdem fest: „Bis diese Komponenten vollständig sind, bleibt
der Aggregatleser gesperrt." Mit diesem Slice werden sie vollständig. **Die
Entsperrung gehört deshalb hierher** und ist als solche zu benennen.

## Zwei mitgeführte Punkte aus früheren Slices

### 1. `red_state_followup_slice` durchreichen — aus S4a

S4a hat das Feld recordfähig gemacht und die Autorisierung daran gebunden:
Ein Red-State-Commit verlangt seitdem den approved, fingerprintgebundenen
Review-Record. Aber `native_review_contract.py:826` und `:844` setzen das Feld
hart auf `None`. **Ein Red-State-Commit ist damit derzeit unmöglich** —
fail-closed ohne Pfad.

Die Matrix benennt den fehlenden Weg: aus `StepContract` über
`NativeReviewContext` in `ContractResult`. Dieser Slice schließt ihn, weil er
ohnehin `persist_native_review_contract()` und den Reviewvertrag anfasst.

Ein Test muss zeigen, dass ein Red-State-Review mit benanntem Folgeslice
entstehen kann **und** dass ein Commit ohne diesen Record weiterhin abgewiesen
wird. Beide Richtungen, nicht nur die neue.

### 2. Der fehlende Größen-Guard — aus R8

R8 hat die Inhalte korrekt in inhaltsgebundene Blobs ausgelagert, aber die im
Auftrag verlangte Messung über wachsende Inhaltsgrößen fehlt. Ich habe sie
nachgeholt: `_validate_record_blobs` läuft in `_load_chain`, jeder Vollscan
liest also alle referenzierten Blobs. Gemessen auf ext4 am 31. August 2026:

| Blobinhalt gesamt | je `load_chain()` |
|---|---:|
| 8 KB | 9 ms |
| 512 KB | 9 ms |
| 4 MB | 13 ms |
| 16 MB | 25 ms |

Rund 1 ms je MB — unkritisch. Der Guard fehlt trotzdem: Ohne ihn bliebe
unbemerkt, wenn jemand später Bytes wieder in die Recorddateien inlined. **Ein
Test in der Art des RP-Skalierungstests ist nachzuziehen**, mit wachsenden
Inhaltsgrößen und einer Kontrolle, die bei Inlining rot wird.

## Anforderungen

1. Die Payloads additiv in Schema 2; `schema_version` bleibt `2`.
2. Der Reviewrecord entsteht vor seinen Lesern; Ordnungsnachweis wie bisher.
3. Ein Replay rekonstruiert alle fünf Fakten und projiziert daraus
   `latest_claude_review`, ohne `state.json`, Checkpoint oder Mirror.
4. Kein Aggregatrecord.
5. Eine Kette ohne die neuen Records wird fail-closed abgewiesen.
6. Neue Record-/Mirror-Vergleiche werden in der Matrix inventarisiert und
   gezählt.
7. Die STOP-Einträge werden fortgeschrieben, einschließlich der Entsperrung des
   Aggregatlesers.

## Achtung: dieser Slice fasst `audit_trail.py` an

R5 konnte die Auditanzeige in der Projektionsschicht umhängen und
`audit_trail.py` unberührt lassen. Das geht hier vermutlich nicht — `anchors`,
`pre_mortem` und `stop_request` werden dort gerendert.

**Slice 1s semantische Markdowngrenze ist deshalb ausdrücklich zu prüfen:** Eine
reine Neuprojektion verwalteter Auditabschnitte darf weder Fingerprint noch
Guardergebnis noch Reviewpaket verändern. Das ist zu zeigen, nicht anzunehmen.

## Providerfreie Akzeptanzfälle

- Ein Review mit Anchors, Pre-Mortem, Stop-Request und Testdateien wird
  vollständig recordet und aus dem Präfix identisch rekonstruiert.
- `latest_claude_review` ist eine Projektion; ein Test weist nach, dass kein
  Aggregatrecord entsteht.
- Ein Red-State-Review mit benanntem Folgeslice entsteht über den nativen
  Transport; ein Red-State-Commit ohne den zugehörigen Record bleibt abgewiesen.
- Die Validationbindung verweist auf Review- und Attestationrecord und ist ohne
  Mirror auflösbar.
- Eine Neuprojektion des Auditabschnitts lässt Fingerprint, Guards und
  Reviewpaket unverändert.
- Der Blob-Größen-Guard wird bei wachsenden Inhalten grün und bei
  wiedereingebetteten Bytes rot.

## Abnahme

- Fünf Fakten recordet, das Aggregat projiziert, kein zweiter Aggregatrecord.
- Der Aggregatleser ist entsperrt und die Matrix entsprechend fortgeschrieben.
- `red_state_followup_slice` ist durchgereicht; beide Richtungen getestet.
- Der Blob-Größen-Guard existiert.
- Slice 1s Markdowngrenze ist nachweislich unberührt.
- Die Providernamen-Baseline ist unverändert.
- Keine Protokoll-, Schema- oder Registerversion angehoben.
- Keine neuen `_recoverable_*`-Sonderfälle.
- Suite grün gegen die Baseline nach R8: 1327 passed. Laufzeit festhalten.

## Nicht-Ziele

- Kein Cutover — S4b.
- Keine restliche Historyprojektion — Bündel 9 und letztes Recordbündel.
- Keine Änderung an Reviewhoheit oder Findingnamensraum.

## Stopbedingungen

- Anhalten, wenn `latest_claude_review` ohne Aggregatrecord nicht vollständig
  projizierbar ist. Dann fehlt eine Komponente und ist zu benennen.
- Anhalten, wenn `red_state_followup_slice` nur durch Aufweichen der in S4a
  gesetzten Autorisierung durchreichbar wäre.
- Anhalten, wenn die Auditänderung die semantische Markdowngrenze berührt.

## Reviewfokus (Claude)

- Ob wirklich kein Aggregatrecord entstanden ist, auch nicht unter anderem Namen.
- Ob die Red-State-Kette in **beiden** Richtungen getestet ist — Erzeugung
  möglich, Commit ohne Record weiterhin abgewiesen.
- Ob `audit_trail.py` die Markdowngrenze aus Slice 1 unberührt lässt, belegt
  durch einen Test gegen Fingerprint und Guard, nicht nur gegen Optik.
- Ob der Blob-Guard eine echte Kontrolle hat, die bei Inlining rot wird.
- Ob `stop_request` seine drei Felder strukturiert trägt und nicht wieder als
  verketteten String.
