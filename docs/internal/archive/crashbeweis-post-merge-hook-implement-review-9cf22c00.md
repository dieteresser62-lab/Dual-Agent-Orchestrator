# Gesamtaudit – crashbeweis-post-merge-hook-implement

<!-- audit:meta:begin -->
Aufgabe: crashbeweis-post-merge-hook-implement · Zielbranch: `feature/crashbeweis-fur-den-post-merge-hook-nachziehen-25b30d5cfd64f95d` · Lauf: `watch-20260926-080118.464286Z-23d1564483fa` · Stand: abgeschlossen
<!-- audit:meta:end -->

## Übersicht

<!-- audit:overview:begin -->
| Slice | Titel | Stand | Commit | Runden | Befunde |
|---:|---|---|---|---:|---:|
| 1 | Vollständige Hook-Grenzmatrix und produktives Resume | freigegeben | 8ae97029 | 3 | 1 |
<!-- audit:overview:end -->

## Befunde

<!-- audit:findings:begin -->
| ID | Herkunft | Klasse | Stand | Titel |
|---|---|---|---|---|
| C-01 | Slice 1 | Befund | geschlossen | Die Hook-Evidenzzeilen aus `_run_post_merge_case` (scripts/crash_harness.py) enthalten erfundene bzw |
<!-- audit:findings:end -->

## Halte und Entscheidungen

<!-- audit:holds:begin -->
Keine.
<!-- audit:holds:end -->

## Abnahmereview

<!-- audit:acceptance-review:begin -->
Abnahmereview abgeschlossen.

Geprüft:
> Ich habe den vollständigen Branch-Diff (3 Teile, 59262 Bytes) gegen den Arbeitsplan und die autorisierten Pfade geprüft. Alle Änderungen liegen im Scope, src/workflow_production.py ist unverändert.
> (1) Manifest v2 und Loader: post_merge_hook steht in LEDGER_ORDER und im Manifest mit 6 Phasen und 4 Hook-Laufgrenzen. Fehlt die Klasse, eine Phase oder ein Prozessschnitt, scheitert das Laden geschlossen; das ist parametrisiert getestet.
> (2) Matrix-Arithmetik: 62 Einzelfälle, 6 Wiederholungen, insgesamt 68, davon 14 Hook-Zeilen. Hook-Grenzen sind konsistent aus record_heads und den semantischen Heads ausgenommen.
> (3) _run_post_merge_case: Der geforkte Worker stürzt hart mit Exit 77 ab. Start-, Freigabe- und Endsignale sowie das Abwarten des verwaisten Hooks sind vorhanden. Geprüft werden das Replay der Kette, die Commit-Bindung, dass die Queue vor dem Hook-Resultat nicht verschoben wird und dass Quittierungen mit fremdem Commit oder geändertem Task keinen Record erzeugen. Hinzu kommen die Idempotenz bei Wiederholung, der Pfad über run_pipeline, die Reihenfolge von Resultat und Queue-Move sowie der Aufrufzähler je Phase gemäß Schritt 3.
> (4) C-01 ist weiterhin behoben: Die Zähler werden gemessen, der Selbstdigest ist eindeutig benannt.
> (5) Produktion: reconcile_disabled_post_merge_intent greift nur bei merge_completed_branch=false und genau einem offenen Intent. Das gilt nur, wenn der bestätigte Archiv-Commit mit gleichem HEAD und Branch vorliegt und der Hook-Pfad unverändert ist; sonst schlägt es geschlossen fehl. Das Ergebnis ist sachlich wahr, weil ohne Merge kein Hook-Prozess startet. Das Schreiben ist idempotent, und ein Regressionstest ist vorhanden.
> (6) Die Provider-Sperre ist auf run_agent und _run_agent_process eingegrenzt, sodass Git- und Hook-Prozesse möglich bleiben.

Größtes Restrisiko:
> Die crash_harness-Tests sind in der attestierten Validierung abgewählt (38 deselected). Die 14 Hook-Fälle sind deshalb nur durch die Aussage des Implementierers belegt, nicht durch eine Orchestrator-Attestierung. Der Betreiber-Crashbeweis nach Abschnitt 2.10 bleibt auf dem Branch-HEAD erforderlich.
> Weitere Restrisiken:
> - In reconcile_disabled_post_merge_intent wird der Grund content_changed berechnet und anschließend durch merge_disabled überschrieben. Die Auslassungsbegründung wird damit in zwei Stellen abgeleitet, einmal im Executor-Pfad und einmal im Resume-Pfad. Das kann künftig auseinanderlaufen.
> - Der Harness importiert Test-Fixtures aus tests/test_orchestrator_runtime.py und ist über /proc Linux-spezifisch.
> - Ob agent_runtime Provider außer über _run_agent_process starten kann, ist aus dem Diff nicht belegbar.

Bruchbedingung:
> Diese Prüfung wäre widerlegt in drei Fällen:
> - Der Betreiberlauf `python3 -m pytest tests/test_crash_harness.py -v` scheitert auf dem Branch-HEAD, oder zwei Läufe liefern kein bytegleiches Ergebnis.
> - Ein ununterbrochener Lauf mit merge_completed_branch=false schreibt einen anderen Auslassungsgrund als der Resume-Pfad über reconcile_disabled_post_merge_intent.
> - agent_runtime startet einen Providerprozess an _run_agent_process vorbei, ohne dass er gezählt wird.

Vorab-Risikoanalyse:
> Angenommen, der Branch scheitert nach dem Merge. Die wahrscheinlichste Ursache wäre dann, dass der deselektierte Crashbeweis auf einem anderen System instabil läuft. Mögliche Auslöser sind Zeitfenster von 5 s beim Start-/Endsignal, /proc-Abhängigkeit und fork mit Threads. Das Ergebnis wäre ein roter Betreibergate-Lauf, aber kein Produktionsfehler.
> Zweitwahrscheinlich ist ein Fall mit deaktiviertem Merge, in dem der Nutzer zwischen Absturz und Resume den Branch wechselt oder core.hooksPath ändert. Das dann fail-closed ausgelöste GitTransactionError blockiert auch den Quittierungspfad. Das ist beabsichtigt konservativ, bis der Zustand wiederhergestellt ist.
> Keine der beiden Ursachen verletzt einen Vertrag oder die Resume-Invarianten. Darum eröffne ich keinen Befund.
<!-- audit:acceptance-review:end -->
