# Slice 01: Ersatzagentenpfad entfernen

**Status:** Implementierung und Reviews abgeschlossen; C-16 bis C-26 geschlossen; keine Antigravity-Findings
**Feature-Branch:** `feature/orchestrator-modernization`
**Branch-Basis des Slice:** `09a698fda562a7cb05bc53bdf26c463d0c7507e0`
**GitHub-Status:** nur lokal; kein Upstream; Push und Merge sind nicht Bestandteil dieses Slice
**Arbeitsplan:** [orchestrator-modernization-work-plan.md](orchestrator-modernization-work-plan.md)

---

## Ziel

Der bestehende Claude→Gemini-Ersatzpfad wird ersatzlos entfernt. Ein Agentenausfall bleibt der tatsächlich ausgefallenen Rolle zugeordnet und führt zu einem definierten Halt; eine andere Instanz wird weder automatisch noch nach einem bereits erkannten Quotafehler aufgerufen. Die noch vorhandene Gemini-Adapterklasse ist nicht Gegenstand dieses Slice und bleibt bis zum Adapterumbau in Slice 03 unerreichbar bestehen.

## Abgedeckte Anforderungen

- R-1 vollständig.
- R-18 vorbereitend: rollengetreue Fehlerzuordnung und kein Ersatzagent; Slice-State, differenzierte Ausfallzustände und Resume folgen primär in Slice 14.

## Akzeptanzkriterien

- `--allow-fallback-to-gemini` existiert weder im Wrapper noch im Python-Parser.
- `OrchestratorConfig` enthält keinen Fallback- oder Sticky-Quota-Zustand.
- `run_agent_checked()` ruft ausschließlich den angeforderten Agenten auf.
- Quota-/Rate-Limit-Fehler brechen ohne Retry und mit dem Schlüssel des tatsächlich aufgerufenen Agenten ab.
- Allgemeine Aufruffehler behalten das bestehende begrenzte Retry-Verhalten.
- Der Preflight prüft nur `claude` und `codex`, die der bestehende Zwei-Phasen-Ablauf tatsächlich benötigt; die vorhandene, aber unerreichbare Gemini-Binary wird nicht vorab verlangt.
- `./run_task --dry-run` beendet den bestehenden Ablauf erfolgreich.
- CLI-Hilfe und README erklären ausdrücklich, dass kein Ersatzagent aufgerufen wird.
- Die vollständige Testsuite bleibt grün.

## Scope

Geplante Laufzeit- und Nutzerdokumentationsänderungen:

- `run_task`
- `.gitattributes`
- `src/agent_runtime.py`
- `src/orchestrator.py`
- `README.md`

Geplante Teständerungen, die dem separaten Test-Riegel unterliegen:

- `tests/test_agent_runtime.py`
- `tests/test_inbox_watcher.py`
- `tests/test_orchestrator_quota.py`
- `tests/test_orchestrator_watch_cli.py`

Prüfspur und Rückdokumentation:

- `docs/internal/slice-orchestrator-modernization-01-remove-agent-fallback.md`
- `docs/internal/orchestrator-modernization-work-plan.md`

Die Ergänzung von `tests/test_inbox_watcher.py` gegenüber der vorläufigen Dateiliste im Arbeitsplan ist erforderlich, weil dessen Argument-Fixture den zu entfernenden Parser-/Konfigurationsschlüssel noch enthält. Die Dateigrenze bleibt eingehalten.

## Nicht-Scope

- Ersatz des Gemini-Adapters durch Antigravity; dies erfolgt in Slice 03.
- Neue CLI-/Konfigurationsarchitektur; dies erfolgt in Slice 02.
- State-Version 3, neue Ausfallzustände und Resume-Semantik; dies erfolgt in Slice 07 beziehungsweise Slice 14.
- Änderungen an Root-Instruktionsdateien oder Markersemantik.
- Automatischer Commit, Push oder Merge.

## Diff-Risiko-Block

**Branch-Check vor dem ersten Edit:** `git branch --show-current` → `feature/orchestrator-modernization`; stimmt mit dem Arbeitsplan überein.
**Statuscheck vor dem ersten Edit:** `git status --short` → leer; Arbeitsbaum war sauber.
**HEAD vor dem ersten Edit:** `09a698fda562a7cb05bc53bdf26c463d0c7507e0`.
**Produktive Programm-/Konfigurationsdateien:** vier (`.gitattributes`, `run_task`, `src/agent_runtime.py`, `src/orchestrator.py`); Grenze 10 wird nicht erreicht.
**Contractklarheit:** Die Entfernung ist eindeutig; der aktive Altcontract bleibt ansonsten unverändert.
**Validierbarkeit:** Standardbefehl `python3 -m pytest tests/ -v` ist vorhanden und ausführbar.
**Bewertung:** Keine Stop-Regel aus §6.4 der Anforderungen greift. Der gesonderte Teständerungsriegel greift planmäßig vor dem ersten Edit unter `tests/**`.

## Teständerungsfreigabe

Die Freigabe soll exakt für die vier oben genannten Testpfade gelten. Ausgangspunkt ist HEAD `09a698fda562a7cb05bc53bdf26c463d0c7507e0`; der Test-Diff gegen diesen Stand war beim Gate leer und hatte als SHA-256-Fingerprint:

```text
e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855
```

Geplant sind ausschließlich:

- Entfernen der beiden Tests für erfolgreichen Claude→Gemini-Fallback und des Dual-Quota-Fallbacktests.
- Ersetzen durch Fail-fast-Fälle für Claude und Codex, die beweisen, dass nur die angeforderte Rolle aufgerufen und im Fehler genannt wird.
- Parser-/Wrapperregressionen, die `--allow-fallback-to-gemini` ablehnen beziehungsweise seine Nichtweitergabe in Watch-, Resume- und Neustartpfad absichern.
- Entfernen des obsoleten Fallback-Felds aus der Watcher-Test-Fixture.
- Anpassen des bestehenden Phase-2-Quota-Tests von der Ersatzrolle Gemini auf die tatsächlich aufrufbare Rolle Codex.
- Absichern, dass `run_task` ohne Legacy-Flag und mit LF-Zeilenenden ausgecheckt wird, sodass sein Shebang unter Linux/WSL ausführbar bleibt.

Jeder zusätzliche Testpfad oder eine Änderung der beschriebenen Testintention erfordert eine neue Freigabe. Der resultierende Test-Diff-Fingerprint wird nach der Implementierung dokumentiert.

**Freigabestatus:** am 2026-08-10 durch den Nutzer ausdrücklich erteilt; gebunden an die vier genannten Testpfade, die dokumentierte Testintention und den Ausgangsstand `09a698fda562a7cb05bc53bdf26c463d0c7507e0`.

## Geplante Validierung

1. Gezielte Tests der vier betroffenen Testmodule.
2. Statische Suche nach produktiven Vorkommen von `allow_fallback_to_gemini`, `claude_quota_reached` und `--allow-fallback-to-gemini`.
3. Prüfung der Python-CLI-Hilfe.
4. `./run_task --dry-run` mit einer temporären Task-Datei und ohne Eingriff in Runtime-State des Repositorys.
5. Vollsuite: `python3 -m pytest tests/ -v`.

## Durchgeführte Änderungen

- Slice-MD angelegt und aus dem Arbeitsplan verlinkt.
- `--allow-fallback-to-gemini` aus allen drei Wrapperpfaden und aus dem Python-Parser entfernt.
- Fallback- und Sticky-Quota-Felder aus `OrchestratorConfig` entfernt.
- Claude→Gemini-Umschaltung einschließlich Fallback-Logs und Fallback-Fehlerpfad aus `run_agent_checked()` entfernt.
- Quota-/Rate-Limit-Fehler werden nun unmittelbar mit dem Schlüssel der angeforderten Rolle als `QuotaReachedError` gemeldet; allgemeine Fehler wiederholen ausschließlich dieselbe Rolle.
- Preflight auf die beiden im aktiven Altworkflow tatsächlich verwendeten Rollen Claude und Codex begrenzt.
- CLI-Hilfe und README auf Fail-fast ohne Ersatzagent aktualisiert.
- `.gitattributes` ergänzt, damit `run_task` auch bei `core.autocrlf=true` mit einem unter Linux/WSL ausführbaren LF-Shebang ausgecheckt wird.
- Git-Modus von `run_task` auf `100755` gesetzt, damit der Wrapper auch in einem frischen Linux-Klon direkt ausführbar ist.
- Obsolete Fallbacktests entfernt beziehungsweise durch rollengetreue Negativtests, Parser-/Wrapperregressionen und Konfigurationsprüfung ersetzt; bestehende Watcher- und Quota-Fixtures angepasst.
- Nach Claude-Runde 1 einen Integrationsnachweis für die Preflight-Rollenliste, robuste argparse-Fehlerprüfung, getrennte Git-Attribut-/Executable-Mode-Tests und einen README-Migrationshinweis ergänzt.
- Arbeitsplan mit tatsächlichem Scope und Umsetzungsstatus rückdokumentiert.

## Ergebnisse

Der grüne Ausgangszustand wurde vor Erteilung der Teständerungsfreigabe festgestellt. Nach der Implementierung sind alle Akzeptanzprüfungen grün:

```text
VALIDATION_RESULT: PASS | PYTHONDONTWRITEBYTECODE=1 python3 -m pytest tests/test_agent_runtime.py tests/test_inbox_watcher.py tests/test_orchestrator_quota.py tests/test_orchestrator_watch_cli.py -v -p no:cacheprovider | 0 (43 passed; Zwischenmessung vor dem zusätzlichen Konfigurationsregressionstest)
VALIDATION_RESULT: PASS | ./run_task --help | 0 (Legacy-Flag fehlt; Fail-fast-Hinweis vorhanden)
VALIDATION_RESULT: PASS | ./run_task --dry-run --agent-output none (isoliertes temporäres Arbeitsverzeichnis) | 0
VALIDATION_RESULT: PASS | PYTHONDONTWRITEBYTECODE=1 python3 -m pytest tests/ -v -p no:cacheprovider | 0 (92 passed)
VALIDATION_RESULT: PASS | PYTHONDONTWRITEBYTECODE=1 python3 -m pytest tests/ -v -p no:cacheprovider | 0 (95 passed nach C-16 bis C-20)
VALIDATION_RESULT: PASS | PYTHONDONTWRITEBYTECODE=1 python3 -m pytest tests/test_agent_runtime.py tests/test_inbox_watcher.py tests/test_orchestrator_quota.py tests/test_orchestrator_watch_cli.py -q -p no:cacheprovider | 0 (47 passed nach C-21/C-22)
VALIDATION_RESULT: PASS | PYTHONDONTWRITEBYTECODE=1 python3 -m pytest tests/ -v -p no:cacheprovider | 0 (95 passed nach C-21/C-22)
VALIDATION_RESULT: PASS | PYTHONDONTWRITEBYTECODE=1 python3 -m pytest tests/ -v -p no:cacheprovider | 0 (95 passed nach C-23/C-24)
VALIDATION_RESULT: PASS | PYTHONDONTWRITEBYTECODE=1 python3 -m pytest tests/ -v -p no:cacheprovider | 0 (95 passed nach C-25/C-26; von Claude in Runde 5 selbst ausgeführt)
VALIDATION_RESULT: PASS | PYTHONDONTWRITEBYTECODE=1 python3 -m pytest tests/ -v -p no:cacheprovider | 0 (von Antigravity im schreibgeschützten Review-Snapshot selbst ausgeführt)
VALIDATION_RESULT: PASS | git diff --check | 0
VALIDATION_RESULT: PASS | git diff --cached --check | 0
```

Die produktive Suche nach den entfernten Konfigurationsfeldern, dem Flag und Fallback-Logpfad ist leer. Verbleibende Nennungen des alten Flags stehen ausschließlich in den beiden Negativtests, die seine Ablehnung und Nichtweitergabe absichern.

**Test-Diff-Fingerprint nach C-25/C-26 (SHA-256):**

Reproduktionsbefehl: `git diff HEAD --binary -- tests/test_agent_runtime.py tests/test_inbox_watcher.py tests/test_orchestrator_quota.py tests/test_orchestrator_watch_cli.py | sha256sum`

```text
d8a670d5ed5eef0c6ca6e22c04498f9a1d3dcad988e295531ee3a28b9448c81a
```

**Vollständiger Diff-Fingerprint unmittelbar vor dem Antigravity-Review (SHA-256):**

Der Fingerprint umfasst `git diff HEAD --binary` sowie Pfad und Git-Blob-ID jeder nicht ignorierten unversionierten Datei. Original und schreibgeschützte Reviewkopie lieferten vor und nach dem Review denselben Wert:

```text
9ba94acc5af0dfa74b2f241870c442597fb95f20ec60cb45ef4d83dd2c7d6397
```

Der separat reproduzierbare Implementierungs-/Test-Diff ohne die beiden nach dem Review mechanisch ergänzten Auditdokumente hat SHA-256:

```text
857084262f8b53b932f9472dc3561cdc3a9561c807cc4a451b1ddac5f784b331
```

## Abweichungen vom Plan

- `tests/test_inbox_watcher.py` wurde in den präzisierten Scope aufgenommen, weil die dortige `Namespace`-Fixture das zu entfernende Fallback-Argument führt. Dies erweitert nicht das Verhalten des Slice.
- `.gitattributes` wurde nach dem ersten echten Wrapper-Smoke ergänzt: Bei globalem Git-Setting `core.autocrlf=true` war `run_task` trotz LF im Git-Blob mit CRLF ausgecheckt und scheiterte unter Linux/WSL bereits an `bash\r`. `run_task text eol=lf` macht das ausdrückliche Abnahmekriterium `./run_task --dry-run` checkout-stabil.
- Für den von Claude gefundenen Modusfehler C-19 musste das Executable-Bit als Git-Index-Metadatum auf `100755` gesetzt werden. Weil `core.filemode=false` auf diesem WSL/Windows-Dateisystem jede Datei als `777` präsentiert, ist dieser Mode-Bit-Unterschied vorzeitig staged; der Inhalt von `run_task` bleibt unstaged (`git diff --cached --numstat` → `0 0`, `git diff --numstat` → `0 3`). Nach C-23 ist außerdem `.gitattributes` selbst vorzeitig staged (`1 0`), damit der EOL-Test nicht durch eine unversionierte lokale Restdatei grün bleibt. Weitere Pfade sind nicht staged. Diese transparente Bootstrap-Ausnahme ersetzt nicht die spätere exakte Allowlist-Stagingprüfung.
- Bis die neue Workflow-Engine implementiert ist, wird der Meta-Review dieses Umbaus manuell aus der Codex-Sitzung angestoßen. Codex entscheidet weder über Findings noch Freigaben, aber der Aufruf ist noch nicht technisch vom Implementierer entkoppelt; genau diese Lücke schließen R-3/R-7 in Slice 03/10.
- Antigravity wurde für den manuellen Meta-Review in einer vollständigen schreibgeschützten Kopie des Git-Zustands ausgeführt. Der Reviewer sah den Fingerprint `9ba94acc…d6397`; anschließend änderte der Orchestrator ausschließlich die vorgesehenen Auditabschnitte, um das validierte Ergebnis zu persistieren. Der geprüfte Implementierungs-/Test-Diff blieb auf `85708426…b331`. Diese Bootstrap-Ausnahme entfällt mit der fingerprintbewussten Audit-/Commit-Engine aus Slice 8 bis 10.

## Offene Risiken

- Der bestehende Watch-Modus friert Quota-Ausfälle weiterhin phasenbezogen ein; die slice-genaue Ausfall- und Resume-Semantik folgt erst mit State v3 in Slice 14.
- Die Gemini-Adapterklasse bleibt bis Slice 03 im Quelltext, ist nach diesem Slice aber über keinen Orchestratorpfad erreichbar.
- Die Root-Rollendatei `GEMINI.md` bleibt gemäß geplantem Instruktions-Cutover bis Slice 18 bestehen; die README kennzeichnet sie bereits als Übergangsartefakt und stellt klar, dass sie nicht als Ersatzrolle aufgerufen wird.
- Antigravity 1.1.12 behandelt `--print` als werttragende Option; Optionen nach einem vorzeitig gesetzten `--print` können dadurch als Prompt statt als Flags verarbeitet werden. Außerdem warnt die CLI, dass `--mode plan` zusammen mit `--disable-slash-commands` wirkungslos ist. Beide Erkenntnisse sind als verbindliche Adapter-/Fähigkeitstests in Slice 03 rückdokumentiert.

## Review-Feedback von Claude

### Fehlgeschlagene Aufrufversuche vor Runde 1

- Zwei headless Aufrufe mit `--permission-mode plan` und nur über `--tools` verfügbaren Werkzeugen endeten ohne parsbares Verdikt in `Execution error`. Der vorangegangene tool-lose Smoke hatte lediglich Erreichbarkeit, Authentifizierung und Flag-Akzeptanz bewiesen, nicht die Werkzeugberechtigung.
- Ein dritter Lauf mit `dontAsk` wurde durch die Nutzerunterbrechung des Turns beendet und lieferte ebenfalls kein Verdikt.
- Die frühere Aussage, der Reviewprozess laufe trotz leerem `ps`-Befund weiter, war nicht belegt und wurde verworfen. Ein nicht sichtbarer Prozess darf weder als lebend noch als beendet interpretiert werden, solange die Prozesshülle keinen Endzustand liefert.
- Korrigierter Isolationstest: festes Modell `opus`, harte Zeitgrenze, JSON-Hülle und `--allowedTools 'Bash(python3 -m pytest:*)'`. Ergebnis `is_error:false`, `result:"Exit code: 0"`; die Hülle wies zugleich einen zunächst abgelehnten, nicht allowlist-konformen Shell-Wrapper aus. Damit war die Berechtigungsschicht als Ursache belegt.
- Der vollständige Review verwendete anschließend dieselbe nachgewiesene Struktur und endete mit `is_error:false`, Modell `claude-opus-5`, eigener Vollsuite und parsbarem Verdikt.

### Runde 1 — Ergebnis vom 2026-08-11

Claude prüfte Korrektheit, Vollständigkeit, Sicherheit, Testqualität und Dokumentation/Operabilität adversarial. Eigene Validierung: **92 passed in 52.39s, Exitcode 0**. Der Reviewer bestätigte insbesondere, dass nur `agents[agent_key]` aufgerufen wird, Gemini aus allen Orchestrator-Callsites und dem Preflight verschwunden ist, Quota- und generische Fehler rollentreu bleiben, `.gitattributes` unter `core.autocrlf=true` wirkt und der Slice mit vier produktiven Dateien kohärent unter der Dateigrenze bleibt.

NEW_FINDING: C-16 | OBSERVATION | Die bedarfsgerechte Preflight-Rollenliste war nicht automatisiert getestet; lokal war Gemini installiert und konnte den Negativfall daher nicht beweisen. | Integrationstest erfasst das von `run_pipeline()` an `RunContext.preflight()` übergebene Rollenarray und erwartet exakt `claude,codex`.

NEW_FINDING: C-17 | OBSERVATION | Der Parser-Negativtest fixierte Exitcode 2 und damit eine semantische Kollision mit dem später reservierten Quota-Exitcode. | Test verlangt nur einen von 0 verschiedenen Exit und die konkrete argparse-Meldung für das entfernte Flag.

NEW_FINDING: C-18 | OBSERVATION | Der CRLF-Test prüfte nur den vorhandenen Checkout statt die wirksame `.gitattributes`-Regel und bündelte zwei Aussagen. | Separater Test prüft `git check-attr eol -- run_task` auf `lf`.

NEW_FINDING: C-19 | OBSERVATION | `run_task` war im Git-Index als `100644` gespeichert und wäre in einem frischen Linux-Klon nicht direkt ausführbar. | Git-Index führt `run_task` als `100755`; separater Test prüft `git ls-files -s`.

NEW_FINDING: C-20 | OBSERVATION | README entfernte das Flag ohne Migrationshinweis für bestehende Aliase, Cronjobs und Wrapper. | README nennt das entfernte Flag ausdrücklich und fordert dessen Entfernung aus bestehenden Aufrufen.

Größtes Restrisiko laut Claude ist die Watch-Operabilität bei Quota: Bis Slice 14 unterscheidet der Watcher einen eingefrorenen Lauf nicht von einem gewöhnlichen Fehler und kann Aufgaben poison-pillen. Wahrscheinlichste spätere Bruchbedingung ist eine in Slice 03 neu eingeführte Adapterauflösung, die Binary-Fallback fälschlich als Rollen-Fallback implementiert.

REVIEWER: claude
OPEN_FINDINGS: NONE
TEST_FILES_TOUCHED: tests/test_agent_runtime.py,tests/test_inbox_watcher.py,tests/test_orchestrator_quota.py,tests/test_orchestrator_watch_cli.py
VALIDATION_RESULT: PASS | PYTHONDONTWRITEBYTECODE=1 python3 -m pytest tests/ -v -p no:cacheprovider | 0
PRE_MORTEM: Slice 03 führt Substitution unterhalb der hier getesteten Aufrufgrenze als vermeintlichen Binary-Resolution-Fallback wieder ein.
SLICE_APPROVAL: 01 | YES

### Runde 2 — Re-Review der Korrekturen vom 2026-08-11

Claude führte die Vollsuite erneut selbst aus (**95 passed in 46.31s, Exitcode 0**) und schloss C-16 bis C-20. Geprüft wurden insbesondere der echte `run_pipeline`-Preflightpfad, der nicht mehr auf Exitcode 2 fixierte Parser-Test, `git check-attr`, der Mode-only-Indexzustand `100755` bei weiterhin unstaged Wrapperinhalt, der README-Migrationshinweis sowie unveränderter R-1-Schutz.

FINDING_STATUS: C-16 | CLOSED | Integrationsnachweis erfasst die Rollenliste aus dem realen `run_pipeline`-Pfad und verlangt exakt `claude,codex`.
FINDING_STATUS: C-17 | CLOSED | Test verlangt Nonzero und die konkrete argparse-Meldung, aber keinen reservierten Exitcode.
FINDING_STATUS: C-18 | CLOSED | Eigenständiger Test wertet die wirksame Git-EOL-Regel aus.
FINDING_STATUS: C-19 | CLOSED | Indexmodus `100755`, eigener Regressionstest und Mode-only-Stagingaufteilung verifiziert.
FINDING_STATUS: C-20 | CLOSED | README-Migrationshinweis ist vorhanden und auffindbar.

NEW_FINDING: C-21 | OBSERVATION | Die dokumentierten 43 gezielten Tests standen ohne Kennzeichnung neben einer späteren 92er-Vollsuite und der Fingerprintbefehl fehlte. | Zwischenmessung eindeutig kennzeichnen und exakten Reproduktionsbefehl dokumentieren.

NEW_FINDING: C-22 | OBSERVATION | Die beiden Git-abhängigen Tests scheitern hart, wenn Git fehlt oder die Quellen außerhalb eines Worktrees ausgeführt werden. | Beide Tests bei fehlendem Git beziehungsweise fehlendem Worktree überspringen und im echten Worktree unverändert streng prüfen.

REVIEWER: claude
OPEN_FINDINGS: NONE
VALIDATION_RESULT: PASS | PYTHONDONTWRITEBYTECODE=1 python3 -m pytest tests/ -v -p no:cacheprovider | 0
PRE_MORTEM: Slice 03 führt Rollenfallback unterhalb der getesteten `agents[agent_key]`-Grenze als vermeintliche Binaryauflösung wieder ein.
SLICE_APPROVAL: 01 | YES

### Runde 3 — Re-Review von C-21/C-22 vom 2026-08-11

Claude reproduzierte **47 gezielte** und **95 vollständige** Tests, den dokumentierten Fingerprint und den unveränderten Mode-only-Indexzustand. C-21 und C-22 wurden geschlossen.

FINDING_STATUS: C-21 | CLOSED | Zwischenmessung, aktuelle Zählungen und exakter Fingerprintbefehl sind eindeutig und reproduzierbar.
FINDING_STATUS: C-22 | CLOSED | Gemeinsamer Guard überspringt nur bei fehlendem Git oder fehlendem Worktree; strenge Assertions laufen im echten Repository.

NEW_FINDING: C-23 | OBSERVATION | Der EOL-Test konnte bei unversionierter `.gitattributes` lokal grün bleiben, selbst wenn die Datei im späteren Commit fehlte. | Zusätzlich beweisen, dass `.gitattributes` im Git-Index geführt wird.

NEW_FINDING: C-24 | OBSERVATION | In einem fremden Worktree mit unversioniertem `run_task` liefert `git ls-files -s` Exitcode 0 mit leerer Ausgabe und erzeugt dadurch eine unklare Assertion statt eines Skips. | Leere `ls-files`-Ausgabe als „hier nicht versioniert“ überspringen; bei vorhandener Ausgabe weiterhin strikt `100755` verlangen.

REVIEWER: claude
OPEN_FINDINGS: NONE
VALIDATION_RESULT: PASS | PYTHONDONTWRITEBYTECODE=1 python3 -m pytest tests/ -v -p no:cacheprovider | 0
PRE_MORTEM: Der Commit lässt die noch unversionierte `.gitattributes` aus, während bestehende Worktrees den EOL-Test weiter bestehen und frische Klone wieder an `bash\r` scheitern.
SLICE_APPROVAL: 01 | YES

### Runde 4 — Re-Review von C-23/C-24 vom 2026-08-11

Claude führte die Vollsuite erneut aus (**95 passed, 0 skipped**) und schloss C-23/C-24. Der Reviewer bestätigte den exakt staged Inhalt von `.gitattributes`, den `100755`-Indexeintrag, die Mode-only-/Content-only-Aufteilung sowie den eng begrenzten Skip bei einem in fremden Worktrees unversionierten Wrapper.

FINDING_STATUS: C-23 | CLOSED | `.gitattributes` ist im Index und enthält ausschließlich die EOL-Regel für `run_task`; kein fremder Pfad ist staged.
FINDING_STATUS: C-24 | CLOSED | Leere `ls-files`-Ausgabe überspringt nur nach erfolgreichem Git-Aufruf; bei versioniertem Wrapper bleibt `100755` zwingend.

NEW_FINDING: C-25 | OBSERVATION | Ergebnisblock und Fingerprint waren noch nicht ausdrücklich der C-23/C-24-Runde zugeordnet. | Aktuelle Validierungszeile ergänzen und Fingerprintüberschrift mit der zugehörigen Korrekturrunde versehen.

NEW_FINDING: C-26 | OBSERVATION | `git check-attr` las die Regel aus dem Arbeitsbaum statt aus dem Index; eine nur unstaged korrekte Regel hätte lokal falsch grün bleiben können. | `git check-attr --cached eol -- run_task` verwenden.

REVIEWER: claude
OPEN_FINDINGS: NONE
VALIDATION_RESULT: PASS | PYTHONDONTWRITEBYTECODE=1 python3 -m pytest tests/ -v -p no:cacheprovider | 0
PRE_MORTEM: Slice 03 reaktiviert Rollenfallback unterhalb der getesteten Aufrufgrenze als vermeintliche Binary-/Adapterauflösung.
SLICE_APPROVAL: 01 | YES

### Runde 5 — Re-Review von C-25/C-26 vom 2026-08-11

Claude führte die Vollsuite erneut selbst aus (**95 passed in 44.51s, Exitcode 0**) und schloss C-25/C-26. Der Reviewer bestätigte die eindeutige Zuordnung der aktuellen Messrunde sowie, dass der EOL-Test mit `git check-attr --cached` ausschließlich die im Index liegende Regel prüft. Es wurden keine neuen Findings erhoben.

FINDING_STATUS: C-25 | CLOSED | Ergebnisblock und Fingerprintüberschrift ordnen die aktuelle Validierung eindeutig der Korrekturrunde C-25/C-26 zu.
FINDING_STATUS: C-26 | CLOSED | Der EOL-Test liest die zu committende Regel mit `git check-attr --cached`; ein abweichender unstaged Arbeitsbaum kann den Test nicht falsch grün halten.

NEW_FINDINGS: NONE
REVIEWER: claude
OPEN_FINDINGS: NONE
TEST_FILES_TOUCHED: tests/test_agent_runtime.py,tests/test_inbox_watcher.py,tests/test_orchestrator_quota.py,tests/test_orchestrator_watch_cli.py
VALIDATION_RESULT: PASS | PYTHONDONTWRITEBYTECODE=1 python3 -m pytest tests/ -v -p no:cacheprovider | 0
PRE_MORTEM: Slice 03 führt Substitution als vermeintlichen Binary-Resolution-Fallback wieder ein; deshalb müssen Rollenidentität und ausführbare Binary getrennt konfiguriert und getestet werden.
SLICE_APPROVAL: 01 | YES
STATUS: DONE

## Review-Feedback von Antigravity

### Abschlussreview vom 2026-08-11

Antigravity prüfte den vollständigen, zuvor von Claude freigegebenen Stand in einer schreibgeschützten Repositorykopie. Der Aufruf verwendete das feste Modell `gemini-3.1-pro-high`, hohe Denktiefe, JSON-Ausgabe, eine 25-minütige innere und 30-minütige äußere Zeitgrenze, Terminal-Sandbox und einen externen Runtime-/Logpfad. Ein vorgeschalteter Isolationstest erzwang erfolgreich einen echten `git status`-Werkzeugaufruf. Der Reviewer führte anschließend die vollständige Testsuite selbst aus und bestätigte Korrektheit, Vollständigkeit, Sicherheit, Regressionstestqualität, Scope, Dokumentation/Operabilität und die Schließungen C-16 bis C-26.

Als größtes Restrisiko nennt Antigravity externe Wrapper oder CI-Aufrufe, die das entfernte Flag noch übergeben und nun korrekt, aber abrupt von `argparse` abgelehnt werden. Das Risiko ist durch den README-Migrationshinweis adressiert und liegt außerhalb des Repository-Scope.

REVIEWER: antigravity
NEW_FINDINGS: NONE
OPEN_FINDINGS: NONE
TEST_FILES_TOUCHED: tests/test_agent_runtime.py,tests/test_inbox_watcher.py,tests/test_orchestrator_quota.py,tests/test_orchestrator_watch_cli.py
VALIDATION_RESULT: PASS | PYTHONDONTWRITEBYTECODE=1 python3 -m pytest tests/ -v -p no:cacheprovider | 0
PRE_MORTEM: Externe Wrapper oder CI-Jobs übergeben weiterhin `--allow-fallback-to-gemini` und scheitern beim Start an der beabsichtigten argparse-Ablehnung.
SLICE_APPROVAL: 01 | YES
STATUS: DONE

## Review-Antworten von Codex

FINDING_RESPONSE: C-16 | ACCEPTED | Integrationstest `test_run_pipeline_preflight_requires_only_active_workflow_agents` ergänzt und auf exakt `claude,codex` gebunden.

FINDING_RESPONSE: C-17 | ACCEPTED | Test erwartet keinen reservierten konkreten Exitcode mehr, sondern Nonzero und die eindeutige argparse-Fehlermeldung.

FINDING_RESPONSE: C-18 | ACCEPTED | Flag-Nichtweitergabe und LF-Checkout sind getrennte Tests; der LF-Test wertet `git check-attr` aus.

FINDING_RESPONSE: C-19 | ACCEPTED | `run_task` ist im Index `100755`; eigener Regressionstest prüft das gespeicherte Mode-Bit. Die notwendige vorzeitige Mode-Bit-Stagingausnahme ist dokumentiert.

FINDING_RESPONSE: C-20 | ACCEPTED | README enthält einen auffindbaren Migrationshinweis für das entfernte Flag.

FINDING_RESPONSE: C-21 | ACCEPTED | Die 43er-Messung ist als Zwischenstand gekennzeichnet; der exakte SHA-256-Reproduktionsbefehl steht unmittelbar am Fingerprint.

FINDING_RESPONSE: C-22 | ACCEPTED | Gemeinsamer `_require_git_worktree`-Guard überspringt beide Git-Metadatentests nur bei fehlendem Git oder fehlendem Worktree; im Repository bleiben die Assertions unverändert streng.

FINDING_RESPONSE: C-23 | ACCEPTED | Der EOL-Test verlangt nun zusätzlich einen erfolgreichen `git ls-files --error-unmatch -- .gitattributes`; die Datei ist bewusst als exakter Slice-Pfad staged.

FINDING_RESPONSE: C-24 | ACCEPTED | Der Executable-Mode-Test überspringt bei leerer `git ls-files -s`-Ausgabe und bleibt bei versioniertem `run_task` strikt auf `100755`.

FINDING_RESPONSE: C-25 | ACCEPTED | Der Ergebnisblock enthält die 95er-Validierung nach C-23/C-24; die Fingerprintüberschrift nennt nun ausdrücklich die aktuelle C-25/C-26-Runde.

FINDING_RESPONSE: C-26 | ACCEPTED | Der EOL-Test wertet mit `git check-attr --cached` ausschließlich den im Index zu committenden Regelinhalt aus.

## Entscheidungstabelle

| ID | Quelle | Finding | Klasse | Entscheidung | Umsetzung |
|---|---|---|---|---|---|
| C-16 | Claude | Preflight-Rollenliste ohne automatisierten Nachweis | OBSERVATION | angenommen | geschlossen in Runde 2 |
| C-17 | Claude | Parser-Test fixiert kollidierenden Exitcode 2 | OBSERVATION | angenommen | geschlossen in Runde 2 |
| C-18 | Claude | CRLF-Test prüft Checkout statt Git-Attribut | OBSERVATION | angenommen | geschlossen in Runde 2 |
| C-19 | Claude | `run_task` im Index nicht ausführbar | OBSERVATION | angenommen | geschlossen in Runde 2 |
| C-20 | Claude | Kein Migrationshinweis für entferntes Flag | OBSERVATION | angenommen | geschlossen in Runde 2 |
| C-21 | Claude | Zwischenmessung/Fingerprintbefehl uneindeutig | OBSERVATION | angenommen | geschlossen in Runde 3 |
| C-22 | Claude | Git-Metadatentests außerhalb eines Worktrees nicht portabel | OBSERVATION | angenommen | geschlossen in Runde 3 |
| C-23 | Claude | Unversionierte `.gitattributes` kann EOL-Test lokal falsch grün halten | OBSERVATION | angenommen | geschlossen in Runde 4 |
| C-24 | Claude | Unversioniertes `run_task` in fremdem Worktree erzeugt unklaren Fehler | OBSERVATION | angenommen | geschlossen in Runde 4 |
| C-25 | Claude | Messrunde im Ergebnisblock/Fingerprint nicht eindeutig | OBSERVATION | angenommen | geschlossen in Runde 5 |
| C-26 | Claude | EOL-Test liest Arbeitsbaum statt Index | OBSERVATION | angenommen | geschlossen in Runde 5 |

## Rückdokumentation in den Arbeitsplan

- Slice-MD in §5.1 und aus der Überschrift von Slice 1 verlinkt.
- Tatsächliche Dateiliste einschließlich `.gitattributes` und `tests/test_inbox_watcher.py` sowie aktueller Reviewstatus eingetragen.
- Claude- und Antigravity-Freigaben sowie die zugehörigen Diff-Fingerprints sind nachgetragen; der Commit-Hash wird durch den unmittelbar folgenden mechanischen lokalen Git-Commit selbst festgehalten.

## Formale Marker

TEST_FILES_TOUCHED: tests/test_agent_runtime.py,tests/test_inbox_watcher.py,tests/test_orchestrator_quota.py,tests/test_orchestrator_watch_cli.py

TEST_CHANGE_APPROVAL: YES | Nutzerfreigabe vom 2026-08-10 für die vier dokumentierten Testpfade und die beschriebene Testintention

VALIDATION_RESULT: PASS | PYTHONDONTWRITEBYTECODE=1 python3 -m pytest tests/ -v -p no:cacheprovider | 0

IMPLEMENTATION_READY: 01 | YES

STATUS: DONE
