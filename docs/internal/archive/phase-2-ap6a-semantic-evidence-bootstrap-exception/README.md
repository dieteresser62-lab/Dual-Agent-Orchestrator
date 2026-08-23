# AP6A – kontrollierter Bootstrapabbruch und Direktabschluss

## Status

**ORCHESTRATORLAUF NICHT FORTSETZEN / DIREKTABSCHLUSS FREIGEGEBEN**

Der kombinierte native Codex–Claude-Pilot für AP6A wurde nach mehreren
erfolgreichen Plan-, Implementierungs-, Korrektur- und Reviewaufrufen
kontrolliert beendet. Der persistierte State-v3-Lauf `20260823-111816Z`
bleibt zusammen mit seiner append-only Recordkette und allen Checkpoints
unverändert als historische Evidenz erhalten. Er wird nicht mehr mit
`run_task --resume` oder `run_task --watch` fortgesetzt.

Der Abbruch ist keine Verwerfung des AP6A-Fachcodes. Er trennt den bereits
implementierten semantischen Evidence-Builder von den während seines eigenen
Bootstraplaufs notwendig gewordenen Orchestrator-Hotfixes. Abschluss,
Validierung und Review erfolgen außerhalb dieses festgefahrenen Runs nach dem
[manuellen Bootstrapvertrag](../../native-agent-json-manueller-bootstrap.md).

## Letzter autoritativer Laufstand

- Branch: `feature/native-semantic-evidence-manifest`
- Run-ID: `20260823-111816Z`
- Work-Unit: `2`
- Step: `codex_correction`
- Runde: `5`
- Record-Head: `ar1-96f964ecddcf2156b40e0b306f0a8abf8d937a3e34ceafad6a3bf77a16ade3d0`
- Letzte Diagnose: `authoritative finding replay differs from the state-v3 mirror`
- Historisch offene Claude-Findings: `C-01`, `C-03`, `C-04`
- Von Claude geschlossen: `C-02`

Der State und die Recordkette dürfen zur nachträglichen Erzeugung eines
scheinbar abgeschlossenen Laufs nicht manuell editiert werden.

## Technische Abbruchursache

Claudes verweigerte Reviews durften ein eigenes Finding mit unverändertem
Status `OPEN` und einer neuen Statusbegründung bestätigen. Der State-Spiegel
übernahm diese `status_rationale`. Der Record-Writer erzeugte dagegen nur bei
einem Klassen- oder Statuswechsel einen `FindingTransitionPayload`. Damit
fehlten der autoritativen Recordprojektion die neuen Begründungen für `C-01`
und `C-03`; der anschließende Gleichheitscheck stoppte korrekt fail-closed.

Die lokale Korrektur schreibt auch reine `OPEN → OPEN`-Begründungsrevisionen
als replaybaren `status_changed`-Record. Ihr Idempotenzschlüssel ist von einem
späteren echten `OPEN → CLOSED` getrennt, damit gleich nummerierte Runden
verschiedener Work-Units nicht kollidieren. Der Regressionstest
`test_native_review_persists_open_status_rationale_for_authoritative_replay`
bildet den beobachteten Fehler nach.

## Disposition der Findings

### C-01 – fachlich nachgewiesen behoben

`_diff_for_paths` steht als eigenständige Funktion auf Modulebene.
`test_plan_change_boundary_honors_exact_fingerprint_bound_path_approval`
besitzt zehn erreichbare Top-Level-Statements und drei aktive Assertions. Der
fokussierte Test ist grün. Der historische Status bleibt dennoch `OPEN`, weil
nur Claude sein Finding im nun abgebrochenen Lauf schließen dürfte.

### C-02 – von Claude geschlossen

Legacy-Reviewpakete v1 bleiben ohne erfundenen semantischen Digest lesbar;
Reviewpakete v2 verlangen und prüfen ihre semantische Bindung für Inline- und
Content-Ref-Auslieferung. Die Roundtrip- und Resume-Regressionen sind grün.

### C-03 und C-04 – Scopefindings, getrennt weitergeführt

Die Findings beanstanden keine verbleibende AP6A-Fachfunktion, sondern die
Vermischung des AP6A-Diffs mit während des laufenden Piloten entstandenen
Bootstrap-Hotfixes. Diese Hotfixes liegen als eigene Commits zwischen dem
freigegebenen Plancommit `a897f70` und `c7778ab` vor und wurden jeweils durch
fingerprintgebundene Benutzerentscheidungen autorisiert. Für den
Direktabschluss werden zwei getrennte Prüfgegenstände verwendet:

1. Bootstrap-/Recovery-Härtung einschließlich Record/State-Spiegelung;
2. AP6A-Fachänderungen am semantischen Evidence-Manifest und seiner nativen
   Requestbindung.

Damit wird keine falsche Behauptung aufrechterhalten, die Hotfixpfade seien im
historischen Korrekturpacket nicht enthalten gewesen. Die historischen
Findings bleiben offen und sichtbar; der neue Direktreview bewertet die
beiden getrennten Diffs neu.

## Direkter Claude-Abschlussreview

Claude prüfte den Bootstrap-/Recovery-Stand und den AP6A-Fachcode als zwei
ausdrücklich getrennte, read-only Prüfgegenstände mit Sonnet und Effort
`high`. Ergebnis: `approved`, keine neuen Findings; `C-01`, `C-03` und `C-04`
wurden geschlossen. Die Schließung von `C-03/C-04` beruht ausdrücklich auf
der neuen Scope-Trennung und nicht auf einer rückwirkenden Behauptung, die
historischen Pakete seien korrekt geschnitten gewesen.

Bindung des Direktreviews:

- HEAD: `c7778ab6dfc258aec3291164d5c415236f6a921a`
- Prompt-SHA-256:
  `ada0780cd8c57aa3223f41647493edfc0c8143b821b21d5f3609a72a6398055e`
- Schemaprojektions-SHA-256:
  `3a336f496eae25f42a780468e9ef86911f23a94d07f152a5880e350ee0e448cd`
- Worktree-Diff-SHA-256 vor Ablage der Reviewartefakte:
  `f54d1cbe79d1bda634a20108f01b7ad2c0111f35369c6ccec748f1ae252752df`
- Bootstrap-Commitbereich `a897f70..c7778ab`:
  `6888e5fe11686f0ee4d721ccc7bdea4cb3147548ebfff70066498f329018d1c7`
- Claude-Nutzung: 46 Turns, 23.643 Outputtokens, davon 11.538 Thinkingtokens
- Kosten: `$0.9528264`

Als nicht blockierendes Restrisiko hält Claude fest, dass die älteren
Idempotenzschlüssel echter Finding-Statuswechsel die Work-Unit-ID noch nicht
enthalten. Ein Konflikt würde fail-closed abbrechen und nicht still Daten
verfälschen; er gehört in ein späteres, enges Härtungspaket.

## Validierung

- gezielte Record-/Replay-Regressionen: `15 passed`
- C-01-Fokustest: `1 passed`
- vollständige Repositorymatrix nach der Mirror-Writer-Korrektur:
  `1202 passed in 145.68s`
- `git diff --check`: vor der Archivierung sauber

## Archivinhalt

- [Ursprünglicher Auftrag](Phase_2_AP6A_Semantisches_Evidenzmanifest_fuer_Slice_Reviews.md)
- [Implementierungs-Handoff](Phase_2_AP6A_Semantisches_Evidenzmanifest_fuer_Slice_Reviews-implement.md)
- [Watcher-Sidecar](Phase_2_AP6A_Semantisches_Evidenzmanifest_fuer_Slice_Reviews.watch.json)
- [Arbeitsplan](phase-2-ap6a-semantisches-evidenzmanifest-fuer-slice-reviews-arbeitsplan.md)
- [Historischer Gesamtaudit](phase-2-ap6a-semantisches-evidenzmanifest-fuer-slice-reviews-implement-review-78428efe.md)
- [Historischer Slicebericht](slice-phase-2-ap6a-semantisches-evidenzmanifest-fuer-slice-reviews-arbeitsplan-01-kanonisches-diff-abdeckungsmanifest-bis-zur-nativen-requestbindung.md)
- [Direkter Claude-Reviewprompt](claude-direct-final-review-prompt.md)
- [CLI-kompatible Review-Schemaprojektion](claude-direct-final-review-schema.json)
- [Strukturiertes Claude-Ergebnis](claude-direct-final-review-result.json)
