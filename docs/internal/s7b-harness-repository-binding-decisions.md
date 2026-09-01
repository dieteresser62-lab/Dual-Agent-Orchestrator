# S7b — Entscheidungen zur Repositorybindung des Harness

## Digestgrenze

`harness_implementation_sha256` bindet alle Dateien, die den ausführbaren
Crashnachweis oder seine Auslegung ändern können: sämtliche getrackten
JSON-Schemata unter `schemas/**/*.json`, sämtliche getrackten Python-Module
unter `src/**/*.py`, `scripts/crash_harness.py` und das getrackte
Crashmanifest. Die rekursive Menge wird direkt über Git-Pfadspecs aus dem
Index abgeleitet und repositoryrelativ sortiert. Damit erfasst der Digest auch
direkte und transitive Projektimporte, ohne ungetrackte Backupdateien oder
fehleranfällige kuratierte Listen einzubeziehen.

Die S5- und S5b-Auftragsdokumente sind dagegen Prozessevidenz. Der Harness
parst sie nicht; ihre Bytes können weder Szenario, Crashgrenze,
Reducerergebnis noch Akzeptanzzusicherung verändern. Sie wurden deshalb aus
`measured_sources` entfernt. Das verliert keine Implementierungsgarantie.
Provenienz bleibt zusätzlich durch Repository-Commit und Szenarioversion
gebunden. Die Aufnahme gitignorierter Auftragsdokumente hätte identischen
versionierten Code in einem frischen Klon lediglich unausführbar gemacht.

## Repositoryunabhängigkeit

Der Byte-Stabilitäts- und Selbstbindungstest erstellt über `git ls-files` einen
Repository-Snapshot und sichert zu, dass `inbox/`, `outbox/` und
`.orchestrator/` fehlen. Er initialisiert daraus ein echtes temporäres
Git-Repository und einen Commit wie in einem frischen Klon. Der vollständige
Harness läuft zweimal in isolierten Python-Subprozessen mit `-I`; sowohl
`crash_harness` als auch sämtliche `src`-Importe und die Commitbindung stammen
dadurch aus dem Snapshot. Bei einem Fehler gibt der Test Exitcode,
Standardausgabe und Standardfehler des Subprozesses aus.

Der Test verlangt außerdem unabhängig über rekursive Dateiinventuren die exakte
geordnete Digestmenge aus allen `schemas/**/*.json`, allen `src/**/*.py`,
Harness und Manifest. Eine zusätzliche Git-Index-Kontrolle belegt mit
verschachtelten Dateien, dass getrackte Unterverzeichnisse eingeschlossen und
ungetrackte Backupdateien ausgeschlossen werden. Das unbemerkte Entfernen
eines ausführbaren Eingangs oder eine nichtdeterministische Reihenfolge werden
damit rot.

## Guard gegen gitignorierte Quellabhängigkeiten

Der repositoryweite Guard leitet die gitignorierten Top-Level-Verzeichnisse
aus `.gitignore` ab und scannt `src/`, `scripts/` und `tests/` nach statisch an
`ROOT`, `PROJECT_ROOT` oder `repository_root` gebundenen Dateilesezugriffen.
Er verfolgt Konstanten und Pfadvariablen über lokale Zuweisungen,
Schleifenvariablen und verschachtelte Scopes bis zu `open`, `read_bytes` oder
`read_text`.

Negativkontrollen erzeugen echte synthetische Dateien unter jedem der drei
Codebäume und prüfen alle neun Kombinationen mit `inbox/`, `outbox/` und
`.orchestrator/`. Eine zusätzliche Mechanikprobe bindet alle drei Wurzelnamen,
alle drei Lesemethoden, eine indirekte Pfadkonstante und einen verschachtelten
Funktionsscope.

Eine Positivkontrolle hält die beabsichtigte Grenze fest: Ein an
`repository_root` gebundener `.orchestrator/state.json`-Pfad, der an die
versionierte Runtime-Lade-API übergeben wird, ist kein statischer
Repository-Quellread und wird nicht gemeldet. Der Guard scannt auch seine
eigene Testdatei; es gibt keine pauschale Selbstausnahme.

Der Guard verbietet bewusst Repository-Quellabhängigkeiten und nicht die
versionierte Laufzeit-API des Orchestrators, die Zustandsdaten in einem vom
Aufrufer übergebenen Arbeitsrepository liest. Laufzeitwurzeln wie `root`,
`repository`, `case_root` und `tmp_path` liegen deshalb außerhalb dieser
Quellabhängigkeitsgrenze. Der bestehende Guard gegen nichtautoritative Archive
scannt nun zusätzlich `scripts/`.

## Historische Fehlerklassifikations-Fixtures

Der neue Guard hat eine weitere ignorierte Repositoryabhängigkeit offengelegt:
Die Fehlerklassifikationsregression las neun historische Berichte aus
`outbox/failed`. Die neun byteidentischen JSON-Dateien liegen nun zur
Versionierung unter `tests/fixtures/error_classification/`; Fehlerklassen,
Assertions und Inhalte bleiben unverändert.

Sieben Dateien behalten ihren historischen Dateinamen. Zwei Namen mussten
wegen des bestehenden Sprachguards neutralisiert werden; ihre Herkunft bleibt
hier explizit festgehalten:

- `20260829T094222.462Z_01-validierungsevidenz-darf-aktive-pruefungen-nicht-selbst-vergiften.md.poison.error.json`
  → `historical-poison-finding-export.error.json`
- `20260829T154920.305Z_01-validierungsevidenz-darf-aktive-pruefungen-nicht-selbst-vergiften-implement.md.poison.error.json`
  → `historical-poison-audit-dual-write.error.json`

Ein SHA-256-Vergleich gegen die noch lokal vorhandenen ignorierten Originale
war für alle neun Dateien identisch.

## Erhaltene Zusicherung

S7b ändert weder Manifest noch Szenarieninventar, Injektionsmatrix, erwarteten
Konvergenzzustand, `blocked_acceptance_cases`, produktive Resumelogik oder eine
Schema-, Protokoll- beziehungsweise Reducerversion. Der vollständige Harness
bleibt der Akzeptanznachweis; entfernt wurde nur seine Abhängigkeit von
unversionierten Auftragsdokumenten. Die erweiterte Digestmenge stärkt die
Selbstbindung gegenüber dem Vorgänger.

## Validierungsevidenz des Arbeitsbaums

Ausgangs-HEAD für den Slice ist
`a268ea1d9c61f0d45fafad1954946d59b1530b69`. Ausgeführt wurden:

- fokussierte Git-Index- und Guardmechanik — `13 passed in 4.18s`
- `wsl python3 -m pytest tests/test_crash_harness.py -v` — `15 passed in
  245.31s`, davon die 14 unveränderten vollständigen Crashfälle und eine
  Git-Index-Digestprobe
- `wsl python3 -m pytest tests/ -v` — `1426 passed in 430.28s`

Der rote Vorgänger hatte `1410 passed, 3 failed`. Seine drei Fehler waren die
vollständigen Harnessaufrufer, die an den fehlenden ignorierten
Auftragsdokumenten stoppten. Der finale S7b-Stand fügt dreizehn Tests hinzu:
einen Repositoryscan, neun Codebaum-/Verzeichniskombinationen, eine
Guardmechanikprobe, eine Runtime-Grenzprobe und eine rekursive
Git-Index-Digestprobe. Damit ergibt sich aus zuvor 1413 Tests ein erwarteter
Gesamtstand von 1426, ohne eine vorhandene Zusicherung zu entfernen.

## Übergabe- und Mergebedingung

Diese Evidenz bindet den fertig implementierten Arbeitsbaum, noch nicht einen
neuen Commit: Codex darf nach dem Projektvertrag weder stagen noch committen.
Der Slice-Commit muss insbesondere die neun neuen Fixtures und diese
Entscheidungsakte erfassen. Danach führt der Betreiber auf dem exakten neuen
Branch-HEAD vor dem Merge erneut
`python3 -m pytest tests/test_crash_harness.py -v` aus. Ohne diesen grünen
HEAD-gebundenen Operatornachweis bleibt der Merge gesperrt.
