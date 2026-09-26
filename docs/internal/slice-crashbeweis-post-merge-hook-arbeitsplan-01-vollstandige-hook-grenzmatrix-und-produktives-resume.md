# Slice 1 von 1 – Vollständige Hook-Grenzmatrix und produktives Resume

<!-- audit:status:begin -->
Freigegeben in Runde 2 · 1 Befund, 1 geschlossen · Validierung grün
<!-- audit:status:end -->

## Ziel

<!-- audit:goal:begin -->
Der eigenständige Crashbeweis durchläuft alle deklarierten `post_merge_hook`-Grenzen für beide Abschlussarten in echten, isolierten Git-Testrepositories und belegt das sichere Resume bis zum Auftragsabschluss.
<!-- audit:goal:end -->

## Akzeptanzkriterien

<!-- audit:acceptance:begin -->
- Gegen SOURCE ist `post_merge_hook` in `LEDGER_ORDER`, im aktuellen Manifest, in dessen Grenzmatrix und als produktive Laufgrenze für `merge_completed_branch = true` und `false` enthalten. Entfernt man die Klasse oder eine deklarierte Hook-Grenze aus dem Manifest, scheitert der Crashbeweis geschlossen.
- Gegen SOURCE erreichen die Testfälle mindestens die Grenzen nach Hook-Intent vor dem Start, nach tatsächlichem Prozessstart ohne Resultat, nach tatsächlichem Prozessende vor Resultat und nach Resultat vor Auftragsende; zusätzlich decken sie die übrigen deklarierten Phasen ab. Jede Grenze wird aus echter Record-Kette über den produktiven Resume-Pfad fortgesetzt.
- Gegen SOURCE weist der physische Aufrufzähler für jede Grenze bei aktiviertem Merge den in Schritt 3 genannten exakten Sollwert nach: eins bei `before_intent` nach Resume und bei allen Grenzen nach physischem Start, null bei `after_intent` und `before_effect` mit anschließendem `acknowledged_unknown`-Record. Ein bekannter Hook-Ausgang wird nie ein zweites Mal ausgeführt; ein Abschluss ohne Hook-Aufruf und ohne Quittierungs- oder Auslassungsrecord scheitert.
- Gegen SOURCE stoppt jeder ungewisse physische Hook-Ausgang bei gewöhnlichem Resume sichtbar, schreibt vor Quittierung keinen terminalen Erfolg und läuft nach korrekter `--acknowledge-post-merge`-Quittierung ohne Hook-Wiederholung bis zum Abschluss. Falscher Commit oder geänderter Task erzeugt keinen Quittierungsrecord.
- Gegen SOURCE bleibt bei aktiviertem Merge der bestätigte Merge-Commit auf dem Basisbranch erhalten. Bei deaktiviertem Merge bleibt der Archiv-Commit auf dem Zielbranch erhalten, der Aufrufzähler ist bei jeder Grenze null und ein `merge_disabled`-Resultatrecord belegt die Auslassung. Der Watch-Queue-Move nach `outbox/done/` erfolgt nur nach terminalem Hook-Resultat.
- Gegen SOURCE enthält das Ergebnis prüfbare Hook-Evidenz für beide Abschlussarten, ist bei identischem Quellstand bytegleich und sein `artifact_sha256` bindet exakt die kanonischen Ergebnisbytes ohne das Digestfeld. Alte Run-Profile und Record-Ketten bleiben lesbar.
- Gegen SOURCE ist `python3 -m pytest tests/test_crash_harness.py -v` nach diesem Slice vollständig grün. Der Implementierer führt diesen gezielten Lauf in jeder Korrekturrunde aus und berichtet das Ergebnis; die unveränderte Standardmatrix `python3 -m pytest tests/ -v -m "not crash_harness"` läuft als orchestratorische Validierung. Auf dem endgültigen Branch-HEAD folgt der vollständige Betreiber-Crashbeweis vor dem Finalreview gemäß Abschnitt 2.10.
<!-- audit:acceptance:end -->

## Umfang

<!-- audit:scope:begin -->
- `docs/internal/crashbeweis-post-merge-hook-implement-review-9cf22c00.md`
- `docs/internal/slice-crashbeweis-post-merge-hook-arbeitsplan-01-vollstandige-hook-grenzmatrix-und-produktives-resume.md`
- `scripts/crash_harness.py`
- `src/orchestrator.py`
- `src/workflow_completion.py`
- `src/workflow_production.py`
- `tests/fixtures/crash_harness/manifest-v2.json`
- `tests/test_crash_harness.py`
- `tests/test_workflow_completion.py`
<!-- audit:scope:end -->

## Umsetzung

> - `manifest-v2.json` und der Loader führen `post_merge_hook` mit sechs Ledger-Phasen, beiden Abschlussarten und zwei getrennten physischen Prozessschnitten. Fehlende Klassen, Phasen oder Prozessschnitte werden beim Laden abgewiesen.
> - Der Crash-Harness startet für jeden Hook-Fall ein isoliertes Git-Repository mit echtem Record-Store. Ein Worker stürzt an der beobachteten Grenze hart ab; der Hauptprozess lädt die Record-Kette erneut und ruft den produktiven Resume-Einstieg auf. Start- und Endsignal sowie ein physischer Aufrufzähler belegen die beiden zusätzlichen Prozessschnitte.
> - Jeder Fall prüft den bestätigten Commit, genau einen Hook-Resultatrecord, die korrekte Quittierung bei ungewissem physischem Ausgang und den Queue-Move nach terminalem Resultat. Die Hook-Zeile zählt jeden produktiven Resume-Aufruf einschließlich des Aufrufs aus `run_pipeline`, misst Resultat und Queue-Reihenfolge und trägt einen ausdrücklich als Zeilen-Selbstdigest benannten Wert. Record- und Replay-Digests werden nur für Matrixzeilen ausgegeben, die sie aus der tatsächlichen Kette ableiten.
> - Der ausgeführte Fall `post_merge_without_merge:after_intent` zeigte einen produktiven Resume-Fehler: Die allgemeine Recovery wies den offenen Intent als physisch ungewiss ab, obwohl bei deaktiviertem Merge kein Hook-Prozess startet. Der Abschluss schreibt jetzt vor der allgemeinen Prüfung anhand des bestätigten Archiv-Commits den rein logischen `merge_disabled`-Resultatrecord. Ein fokussierter Produktionstest sichert diese Bedingung ab.

## Abweichungen vom Plan

> Keine.

## Verlauf

<!-- audit:history:begin -->
- Runde 1: Umsetzung · Validierung grün · Prüfurteil abgelehnt · 1 neu, 0 geschlossen.
- Runde 2: Korrektur · Validierung grün · Prüfurteil freigegeben · 0 neu, 1 geschlossen.
<!-- audit:history:end -->

## Befunde

<!-- audit:findings:begin -->
### C-01 – Die Hook-Evidenzzeilen aus `_run_post_merge_case` (scripts/crash_harness.py) enthalten erfundene bzw

Klasse: Befund · Stand: geschlossen

Befund:
> Die Hook-Evidenzzeilen aus `_run_post_merge_case` (scripts/crash_harness.py) enthalten erfundene bzw. falsche Messwerte. Diese Zeilen landen in `crash_matrix` und `post_merge_hook_evidence`. (1) `record_head`, `chain_semantic_sha256` und `side_effect_projection_sha256` werden alle auf den SHA-256 des Evidenz-Dicts selbst gesetzt. Sie binden also weder die tatsächliche Record-Kette noch die Side-Effect-Projektion, obwohl die gleichnamigen Felder der übrigen Matrixzeilen genau das ausdrücken. (2) `production_resume_attempts` (3/2), `resume_attempts` (2/1) und `production_resume_successes` (1) sind hart kodiert und passen nicht zum tatsächlichen Ablauf. Bei ungewissem Ausgang gibt es fünf Aufrufe von `run_production_workflow`: gewöhnliches Resume, fremder Commit, geänderter Task, Quittierung und idempotente Wiederholung. Zwei davon sind erfolgreich. Ohne Ablehnung sind es zwei erfolgreiche Aufrufe. (3) `queue_done_after_result` und `completed_intent_after_resume` sind Literale `True` und keine gemessenen Werte. Damit ist die im Akzeptanzkriterium geforderte „prüfbare Hook-Evidenz“ nicht gegeben: Ein Konsument kann die Zeilen nicht gegen die Record-Kette verifizieren und wird über die Zahl der Resume-Versuche getäuscht.

Akzeptanztest:
> Für jede Hook-Zeile im Harness-Ergebnis gilt eine von zwei Möglichkeiten: Die Felder `record_head`, `chain_semantic_sha256` und `side_effect_projection_sha256` werden aus der realen Record-Kette bzw. der Replay-Projektion abgeleitet, und zwar deterministisch, damit das Ergebnis bytegleich bleibt. Oder die Felder entfallen bzw. werden eindeutig als Selbstdigest der Zeile benannt. Die Zähler für Resume-Versuche und -Erfolge werden während des Laufs tatsächlich gezählt und stimmen mit der Zahl der ausgeführten `run_production_workflow`-Aufrufe überein. Boolesche Evidenzfelder spiegeln geprüfte Beobachtungen wider und sind keine Konstanten. Die Byte-Stabilität des Ergebnisses bleibt erhalten.

Antwort des Implementierers, Runde 2 (angenommen):
> Die Hook-Zeilen kennzeichnen ihren Digest nun als Selbstdigest. Resume-Aufrufe und Erfolge werden tatsächlich gezählt; Queue-Reihenfolge und abgeschlossener Intent werden aus der Record-Kette geprüft. Alle 14 Hook-Fälle, eine Wiederholungsprüfung auf bytegleiche Evidenz und die reguläre Testsuite mit 2552 bestandenen Tests waren erfolgreich.

Abschlussbegründung des Prüfers:
> Behoben in `_run_post_merge_case`. (1) `record_head`, `chain_semantic_sha256` und `side_effect_projection_sha256` sind aus den Hook-Zeilen entfernt. Der einzige Digest heißt jetzt `hook_evidence_sha256` und ist damit eindeutig als Selbstdigest über `canonical_json(evidence)` benannt. Der Test prüft, dass die Felder fehlen, und rechnet den Digest nach. (2) `production_resume_attempts` und `production_resume_successes` zählt der Wrapper `counted_production_resume` am Modulattribut `orchestrator.run_production_workflow` tatsächlich mit. `resume_attempts` übernimmt den gemessenen Wert. Der Test erwartet 6 bzw. 3 Versuche: gewöhnliches Resume, fremder Commit, geänderter Task, Quittierung, Wiederholung und `run_pipeline`. Er erwartet außerdem 3 Erfolge. Das passt zum Ablauf. (3) `queue_done_after_result` wird aus den Kettenindizes der Hook- und Queue-Resultate abgeleitet. `completed_intent_after_resume` stammt aus dem Replay und dem `effect_key`-Abgleich. `commit_binding_verified`, `branch_head_verified`, `open_intent_after_crash` und `process_*_observed` sind ebenfalls Beobachtungen. Alle Werte sind deterministisch, die Byte-Stabilität bleibt also erhalten.
<!-- audit:findings:end -->

## Validierung

<!-- audit:validation:begin -->
- Runde 1: `python3 -m pytest tests/ -v -m 'not crash_harness'` · grün · Exitcode 0.

- Runde 2: `python3 -m pytest tests/ -v -m 'not crash_harness'` · grün · Exitcode 0.
<!-- audit:validation:end -->

## Abschlussprüfung

<!-- audit:approval:begin -->
Geprüft:
> Geprüft habe ich die Korrektheit der Hook-Evidenzfelder gegenüber der Record-Kette und dem Replay sowie die Zähllogik des Wrappers einschließlich der Ausnahmepfade. Aus fremdem Commit und geändertem Task entstehen Versuche ohne Erfolg. Ebenfalls geprüft: der Determinismus der Zeilen und damit die Byte-Stabilität, der Ausschluss der Hook-Zeilen aus `record_heads`, `semantic_heads` und `canonical_by_boundary` sowie das Wiederherstellen des Originals im `finally` samt `chdir`. Dazu kommen der Skip-Reconcile-Pfad `reconcile_disabled_post_merge_intent` bei `merge_completed_branch=false` und die Einhaltung der Pfadgrenze. Die Attestierung für den Fingerprint [Hash ausgelassen] ist PASS.

Größtes Restrisiko:
> Die orchestratorische Matrix schließt `-m "not crash_harness"` aus. Die 14 Hook-Fälle und die Erwartung 6/3 sind daher nur durch den Bericht des Implementierers belegt, nicht durch die Attestierung. Ruft `run_pipeline` `run_production_workflow` nicht über das Modulattribut auf, weicht der Zähler vom Test ab. Der Zähler bliebe trotzdem gemessen und nicht erfunden.

Bruchbedingung:
> Der Befund bricht wieder auf, wenn der Betreiber-Crashbeweis auf dem Branch-HEAD andere Versuchs- oder Erfolgszahlen liefert als die tatsächlichen Aufrufe. Dasselbe gilt, wenn ein Konsument außerhalb der ausgeschlossenen Aggregationen `record_head` oder `side_effect_projection_sha256` aus Hook-Zeilen liest und dabei einen KeyError auslöst.

Vorab-Risikoanalyse:
> Wahrscheinlichstes Scheitern: Beim vollständigen Crashbeweis sind die Zähler nicht stabil. Mögliche Ursachen sind ein zusätzlicher interner Resume-Aufruf in `run_pipeline` oder zeitabhängige Unterschiede bei `record_count` bzw. `process_started_observed` im Fall `after_process_start`. Dann schlägt die Bytegleichheit oder die Test-Erwartung fehl. Solche Abweichungen würde erst das Betreiber-Gate aufdecken, nicht die hier attestierte Standardmatrix.
<!-- audit:approval:end -->

<!-- audit:reference:begin -->
Technischer Bezug: Lauf `watch-20260926-080118.464286Z-23d1564483fa`, Arbeitseinheit(en) 2; Nachweise in der Recordkette.
<!-- audit:reference:end -->
