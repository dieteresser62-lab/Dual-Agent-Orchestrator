# B117 – Grenzen unter Wachstum

## Messung vor der Änderung

Die abgeschlossene, nur lesend ausgewertete Kette
`../Cookbook/.orchestrator/artifacts/watch-20260909-043238.011217Z-51205a8861db/`
enthält 114 `provider_input_measurement`-Records. Die gemessenen lokalen
Providerinputs verteilen sich so:

| Operation | Records | Zeichen min–max | UTF-8-Bytes min–max | Größte Komponente am Maximum |
|---|---:|---:|---:|---|
| `claude_final_review` | 8 | 910.117–1.213.192 | 911.599–1.214.680 | `evidence_asset_002`, 815.568 Zeichen |
| `claude_slice_review` | 57 | 63.825–516.314 | 63.861–516.441 | `evidence_asset_002`, 148.253 Zeichen |
| `codex_correction` | 1 | 37.329 | 37.360 | `evidence_asset_001`, 25.825 Zeichen |
| `codex_final_correction` | 4 | 94.972–107.119 | 95.039–107.211 | `evidence_asset_001`, 85.778 Zeichen |
| `codex_final_review` | 1 | 1.037.784 | 1.039.272 | `stdin_prompt`, 1.030.346 Zeichen |
| `codex_implementation` | 43 | 10.955–267.732 | 10.964–267.752 | `stdin_prompt`, 131.208 Zeichen; zusätzlich `evidence_asset_001`, 129.106 Zeichen |

Beim größten Claude-Abschlussreview bestand die Anfrage aus Request-Chunks
mit 314.629 Zeichen, Evidenzassets mit 37.571 und 815.568 Zeichen, Manifest
mit 3.199 Zeichen, Systempolicy mit 638 Zeichen, Antwortschema mit 41.356
Zeichen und Startdirektive mit 231 Zeichen. Damit ist das vollständige
Branch-Diff der klare Hebel. Die Cookbook-Kette wurde nicht verändert.

## Teil 1 – Providerinputgrenze

Unterhalb von 1.000.000 Zeichen Branch-Diff bleibt der kanonische Request
bytegleich. Oberhalb dieser Schwelle wird ausschließlich das vollständige
Branch-Diff durch einen kleinen, kanonischen Grenzhinweis ersetzt. Dieser
bindet Originalzeichen, UTF-8-Bytes, SHA-256 und Repositoryfingerprint und
setzt ausdrücklich `evidence_complete=false`. Die vollständige aktuelle
Read-only-Repositoryaufnahme bleibt verfügbar; der Reviewer wird angewiesen,
benötigte Dateien zu lesen und bei notwendigem, aber nicht verfügbarem
Baselineinhalt einen `BLOCKER` zu melden. Ein weiter übergroßer Request endet
vor Providerstart mit `PROVIDER-INPUT-BUDGET` und Exitcode 5. Echte
Preflight-Daten- oder Sicherheitsprobleme bleiben unverändert resumierbar.

## Teil 2 – Quotenfortschritt

Ein erkannter erster Reset ist fortsetzbar. Danach gilt als Fortschritt
entweder ein strikt späterer erkannter Resetzeitpunkt oder positive,
normalisierte Nutzungsmetrik des dazwischen gestarteten Providerattempts.
Fehlt ein Reset oder kehrt dieselbe Grenze ohne einen dieser Nachweise sofort
zurück, endet die Automatik mit `QUOTA-AUTOMATION-STOPPED` und Exitcode 5.
Dasselbe gilt für die absolute Sicherheitsgrenze von 32 Fortsetzungen. Ein
fehlender Repositoryfingerprint bleibt als echtes Sicherheitsproblem
resumierbar. Der Standard von sieben Tagen bleibt bewusst bestehen: Mit
stündlichem Heartbeat ist er für lange, unbeaufsichtigte Läufe vertretbar.

## Bewusste Fixture-Aktualisierung

`tests/fixtures/cli-argument-evaluation-corpus-v1.json` wird bewusst
nachgezogen, weil der öffentlich aufgelöste CLI-Standard
`maximum_auto_resumes` von 1 auf 32 steigt. Der historische Pre-B65-Anker
bleibt unverändert; sein Guard nimmt allein dieses lebende Runtime-Corpus von
der Bytegleichheit aus. Recordschema, Recordtypen und Reducer-Version ändern
sich nicht.
