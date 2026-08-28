# Providerfreier Resilienz- und Abschlussnachweis

## 1. Zweck und Grenze

Dieser Bericht führt die Ergebnisse der Übergangsmatrix, der
Structured-Output-Druckmessung und der bestehenden Queue-/Resume-Regressionen
zu einem deterministischen Abschlussnachweis zusammen. Er ruft keinen Provider
auf und verwendet weder Laufzeit-, Token- noch Kostenschwellen.

Technische Quelle der Szenarioinventur ist
`tests/fixtures/orchestrator_resilience/manifest-v1.json`. Der Harness rendert
daraus byteidentisch dieselbe nach den drei Dimensionen sortierte Sicht. Eine
vom Manifest unabhängige literale Erwartung bindet für jede Zeile Dimension,
vollständigen Erwartungstext und eine eindeutige parametrisierte Testidentität.
Diese Testidentitäten führen die elf Szenarien im selben Testlauf wirklich aus;
eine bloße Namensexistenz gilt nicht als Nachweis.

## 2. Sicherheit

| Szenario | Erwarteter Nachweis |
|---|---|
| `structured-output-near-miss` | Nur der exakte Claude-Diagnoseenvelope darf transient sein; Provider-, Typ-, Subtyp- und Vollständigkeits-Near-Misses bleiben fail-closed. |
| `commit-head-drift` | `GateReason.UNEXPECTED_FILE`, exaktes Präfix `HEAD-DRIFT` ab Zeichenposition null, Nutzergate, exakter Fingerprint und Pfadsatz sowie `resume_step=SLICE_COMMIT`. |
| `slice-boundary-drift` | `GateReason.UNEXPECTED_FILE`, exaktes Präfix `SLICE-HEAD-DRIFT`, Policygate, leerer Pfadsatz und kein Resume-Schritt. |
| `scope-violation` | `GateReason.UNEXPECTED_FILE`, exaktes Präfix `UNEXPECTED-PATH`, fingerprintgebundenes Nutzergate und exakt der unzulässige Pfadsatz. |

Der Dry-Run-Harness prüft Gateidentitäten als vollständiges Tupel aus Status,
Grund, Regelkennung, Gateart, Fingerprint, Pfaden und Resume-Schritt. Die
Gateart folgt Status und Grund statt dem zufälligen Vorhandensein eines
Fingerprints. Reale Zustandsübergänge belegen `ITERATION_LIMIT` als Nutzergate
und `WAITING_FOR_QUOTA` sowie `WAITING_FOR_RETRY` als Resumegates. Eine
Mutationsprobe zeigt außerdem ausdrücklich, dass `HEAD-DRIFT` nicht als
Teilstring von `SLICE-HEAD-DRIFT` akzeptiert wird.

## 3. Verfügbarkeit

| Szenario | Erwarteter Nachweis |
|---|---|
| `exact-structured-output-retry` | Der exakt allowlistete Claude-Envelope wird als transient klassifiziert; verworfener Modellinhalt wird nicht persistiert. |
| `record-ahead-resume` | Ein autoritativ persistiertes natives Review wird vor Policy und Provider gespiegelt; der Provider wird beim Resume nicht erneut aufgerufen. |

Die Druckmessung bleibt von dieser Retryaussage getrennt. Eine
Providererschöpfung ohne lokal beweisbaren Einzelverstoß bleibt als solche
klassifiziert und wird nicht nachträglich als Längen- oder Schemafehler
umgedeutet.

## 4. Autonomie

| Szenario | Erwarteter Nachweis |
|---|---|
| `happy-path` | Plan, zwei Slices und Finalreview terminieren ohne Gate; vier Validierungen und zwei Commits erfolgen genau einmal. |
| `slice-correction` | Slice-Denial führt über Codex-Korrektur und Reviewrunde zwei zur gebundenen Folgekante. |
| `final-correction` | Final-Denial führt in Korrekturrunde eins und anschließend in ein erneutes Finalreview. |
| `second-correction-new-blocker` | Ein neuer Blocker in Runde zwei erweitert den Ledger, ohne frühere offene oder geschlossene Findinglinien zu verlieren. |
| `direct-resume-queue-finalization` | Direkter Resume führt den terminalen Workflow einmal aus und finalisiert das Queueartefakt genau einmal in `outbox/done`. |

Die drei Korrekturzeilen sind keine bloßen Zustandsschnappschüsse. Sie speisen
geskriptete native Codex- und Claude-Dokumente in eine reale `WorkflowEngine`
ein und behaupten pro Ablauf die vollständige Agentreihenfolge, Commitanzahl,
den Findingledger und den terminal abgeschlossenen Finalreview. Die zweite
Korrekturrunde schließt `C-01` und `C-02`, ohne die frühere Linie beim neuen
Blocker zu verlieren.

## 5. Providerfreie Validierung

Der fokussierte Harnesslauf für Parser, Gateidentität und die elf wirklich
ausgeführten Szenarien bestand am finalen Paketstand mit `29 passed`. Der
Verbund mit Übergangsmatrix und Structured-Output-Druck blieb ebenfalls grün.

Die vollständige Repositorymatrix bestand am selben finalen Paketstand mit
`1092 passed`. Beide Angaben sind agent-lokale Validierungsevidenz von Codex
und keine Orchestrator-Attestierung. Externe Provider-Canaries blieben
deaktiviert.

## 6. Nachgelagerter Produktionsnachweis

Nach Review, Abschlusscommit und Integration kann eine kleine reale
Inbox-Aufgabe den Happy Path belegen. Korrektur- und Fehlerpfade werden dabei
nicht künstlich durch Provideraufrufe provoziert; ihre Autorität bleibt die
providerfreie Matrix. Ein realer Smoke-Test ist daher eine ergänzende
Betriebsbeobachtung und keine Voraussetzung der Repositoryvalidierung.
