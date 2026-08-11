# Slice 05: Kanonische Branch-Diff-Quelle

**Status:** implementiert, vollständig validiert und durch Claude sowie Antigravity freigegeben
**Feature-Branch:** `feature/orchestrator-modernization`
**Branch-Basis des Slice:** `00494c4068b5a9e2ad9e3456a52f08cd70d7d719`
**GitHub-Status:** nur lokal; kein Upstream; Push und Merge sind nicht Bestandteil dieses Slice
**Arbeitsplan:** [orchestrator-modernization-work-plan.md](orchestrator-modernization-work-plan.md)

## Ziel

Eine einzige Git-basierte Komponente liefert den vollständigen Änderungsstand seit der tatsächlichen Branch-Basis: bereits auf dem Feature-Branch committete Slices, staged und unstaged Änderungen, Löschungen, Umbenennungen sowie nicht ignorierte unversionierte Dateien. Der Merge-Base-Commit wird getrennt ermittelt und der Sammelfunktion explizit übergeben, damit ein späterer State ihn persistieren kann und Commits während eines Laufs den Bezugsrahmen nicht verschieben.

Agentenberichte bleiben als zusätzliches Signal sichtbar, können aber keinen von Git erkannten Pfad entfernen. Pfadliste, Diffdarstellung und Fingerprint sind deterministisch und bilden die gemeinsame Grundlage für spätere Scope-, Test-, Stop-, Commit- und Endreview-Gates.

## Akzeptanzkriterien

- Die Basiserkennung bevorzugt den Remote-Defaultbranch, falls vorhanden, und verwendet andernfalls einen tatsächlich vorhandenen lokalen `main`- beziehungsweise `master`-Ref; kein Branchname wird als sicher vorhanden vorausgesetzt.
- Der ermittelte Merge-Base ist ein verifizierter Commit und wird `collect_repository_changes` explizit übergeben.
- Ein seit der Merge-Base committeter Slice bleibt im Ergebnis, nachdem weitere Working-Tree-Änderungen entstehen.
- Staged und unstaged Änderungen sowie nicht ignorierte unversionierte Dateien erscheinen gemeinsam und ohne Duplikate.
- Löschungen und Umbenennungen bleiben als eigener Status mit altem und neuem Pfad erkennbar.
- Ignorierte unversionierte Dateien fehlen vollständig; eine bereits versionierte Datei bleibt nach einer späteren Ignore-Regel sichtbar.
- Unversionierte reguläre Text- und Binärdateien sowie Symlinks werden ohne Lesen fremder Symlinkziele deterministisch in Diff und Fingerprint abgebildet; Spezialdateien werden nicht blockierend gelesen.
- Agentengemeldete Pfade werden nur zur kanonischen Git-Pfadliste addiert und können sie weder einschränken noch umsortieren.
- Gleicher Repositoryzustand erzeugt dieselbe sortierte Pfadliste und denselben SHA-256-Fingerprint; eine Inhalts-, Rename- oder Löschungsänderung verändert ihn.
- Der Phase-2-Reviewprompt verwendet die neue Branch-Diff-Quelle für Snapshot-Pfade und Diffkontext; der bisherige `git diff --name-only`-Fallback entfällt.
- Git-/Merge-Base-Fehler scheitern diagnostisch und nicht mit einem agentenbasierten Scheinresultat.
- Vollsuite und bestehender Dry-Run bleiben grün.

## Scope

### Produktive Programmdateien

- `src/repo_changes.py` (neu): Basiserkennung, Merge-Base, Change-Records, Branch-/Working-Tree-/Untracked-Zusammenführung, Diffdarstellung und Fingerprint.
- `src/agent_runtime.py`: bestehende Review-Snapshotdarstellung auf die kanonische Change-Struktur umstellen.
- `src/orchestrator.py`: Git-Befund als Primärquelle verwenden und Agentenbericht ausschließlich additiv behandeln.

### Dokumentation

- `docs/internal/orchestrator-modernization-work-plan.md`: Link und tatsächlicher Slice-Status.
- diese Slice-MD.

### Genehmigungspflichtige Testpfade

- `tests/test_repo_changes.py` (neu)
- `tests/test_agent_runtime.py`

## Nicht-Scope

- Keine Committransaktion, kein Staging und kein Scope-Gate; dies folgt in Slice 09.
- Keine Aktivierung des Test-Riegels oder Fingerprint-Gates in der State-Maschine; dies folgt in Slice 11.
- Keine Auswertung der konfigurierten Pfadklassen oder Produktivdateigrenze; dies folgt in Slice 12.
- Keine State-v3-Persistenz des Merge-Base/Fingerprints; dies folgt in Slice 07 und Slice 11.
- Keine Änderung an CLI-/TOML-Schema, Contracts, Findings, Reviewreihenfolge, Agentenadaptern oder Root-Instruktionsdateien.
- Kein Push oder Merge.

## Diff-Risiko vor dem ersten Code-Edit

**Branch-Check:** `feature/orchestrator-modernization`; entspricht dem Arbeitsplan.

**Status vor Slice-Beginn:** Drei bereits vorhandene, nicht zu Slice 05 gehörende Revision-6-Änderungen liegen in `docs/internal/handover-orchestrator-modernization.md`, `docs/internal/orchestrator-modernization-work-plan.md` und `docs/internal/requirements-orchestrator-modernization.md`. Zusätzlich existiert die unversionierte Editor-Lockdatei `docs/internal/.~lock.slice-orchestrator-modernization-03-three-agent-adapters.md#`. Sie wird weder verändert noch reviewed, gestaged oder committet. Nur klar abgegrenzte Slice-05-Hunks des Arbeitsplans dürfen später aufgenommen werden.

**HEAD:** `00494c4068b5a9e2ad9e3456a52f08cd70d7d719`.

**Geplante Dateien:** drei produktive Programmdateien, Arbeitsplan, Slice-MD und zwei Testpfade. Die Grenze von zehn produktiven Dateien wird nicht erreicht.

**Voraussichtliche Änderungstiefe:** hoch. Git-Zustände, Binär-/Pfadkodierung, Rename-Erkennung und die primäre Reviewdatenquelle ändern sich gemeinsam.

**Gefährdete bestehende Tests:** Snapshotformat und -begrenzung, Phase-2-Reviewprompt, Git-Preflight, Read-only-Harness und Dry-Run.

**Nicht anfassen:** fremde Revision-6-Dokumenthunks, Editor-Lockdatei, `.orchestrator`-State/Checkpoints, CLI-Konfiguration, Adapter, Harness, Watcher, Contracts/Prompts und Root-Instruktionsdateien.

**Rollback-Strategie:** Die neue Komponente bleibt hinter drei klaren Aufrufern. Änderungen werden dateiweise durch Gegenpatches zurückgeführt; die neue Datei wird nur nach ausdrücklicher Freigabe entfernt. Kein Hard Reset und kein pauschales Checkout.

## Test-Riegel

Der Ausgangsdiff gegen HEAD ist für beide vorgesehenen Testpfade leer; `tests/test_repo_changes.py` existiert noch nicht. SHA-256 des binären Testdiffs:

```text
e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855
```

Die erbetene Freigabe gilt ausschließlich für die beiden genannten Pfade und folgende Testintention:

- temporäre Git-Repositories mit Merge-Base und Feature-Branch,
- uncommittete, bereits committete und neue unversionierte Änderungen in einem gemeinsamen Ergebnis,
- staged/unstaged, Löschung, Rename und Pfaddeduplizierung,
- ignorierte unversionierte Dateien sowie versionierte Dateien mit späterer Ignore-Regel,
- deterministische Reihenfolge, Diffdarstellung und Fingerprintänderung,
- unversionierte Text-/Binärdateien, interne/fremde Symlinks und Spezialdateien ohne Fremdlesen,
- fehlender/ungültiger Basis-Ref und Git-Prozessfehler,
- additive Agentenbericht-Vereinigung und Branch-Diff-Snapshot nach bereits erfolgtem Slice-Commit.

```text
TEST_FILES_TOUCHED: tests/test_repo_changes.py,tests/test_agent_runtime.py
TEST_CHANGE_APPROVAL: YES | Nutzerfreigabe vom 2026-08-11 für die zwei dokumentierten Pfade und die beschriebene Git-Diff-/Fingerprint-Testintention
```

Nach der Implementierung ist der binäre Diff der bestehenden Testdatei sowie der vollständige Inhalt der neuen Testdatei an folgende SHA-256-Werte gebunden:

```text
3fbab18d562dc1eee4e0b7ec10d8ae369bd88a081b2fd7c14db7d886b9b18d48  tests/test_agent_runtime.py (binärer Diff gegen HEAD)
3cb3baaeb50165d6704320c4a6ab4351fea17139562dd26355ab53c8b52404d6  tests/test_repo_changes.py (vollständiger Dateiinhalt nach F-001)
```

## Geplante Validierung

1. Gezielte Tests der beiden genehmigten Testpfade.
2. `python3 -m pytest tests/ -v` gemäß Repositoryregel.
3. `python3 -m py_compile` für alle geänderten/neuen Pythonmodule.
4. `./run_task --dry-run --skip-git-check --test-command ''`.
5. `git diff --check`, Scopeprüfung und erneute Test-Diff-Fingerprintprüfung.
6. Danach Sonnet/High genau einmal direkt in einer scopegenauen Read-only-Kopie aufrufen; Plugins, MCPs, Skills, Sessionspeicherung, Subagenten und automatische Retries bleiben aus.
7. Nach Claude-Freigabe: Observations bearbeiten, finaler Antigravity-Review und scopegenauer lokaler Commit.

Ausgangsvalidierung unmittelbar vor dem ersten Produktivcode-Edit:

```text
VALIDATION_RESULT: PASS | PYTHONDONTWRITEBYTECODE=1 python3 -m pytest tests/test_agent_runtime.py -q -p no:cacheprovider | 0 (26 passed)
```

## Durchgeführte Änderungen

- `src/repo_changes.py` führt typisierte Merge-Base-, Pfad- und Änderungsrecords ein. Der Remote-Defaultref wird bevorzugt, vorhandenes `main`/`master` dient als Fallback; die Sammelfunktion akzeptiert nur einen expliziten, als Commit verifizierten Merge-Base.
- Der tracked Branch-/Working-Tree-Diff verwendet Git mit NUL-getrennter Pfadausgabe und Rename-Erkennung. Nicht ignorierte unversionierte Dateien kommen über `git ls-files --others --exclude-standard` hinzu; ignorierte Artefakte bleiben draußen, bereits versionierte Dateien bleiben unabhängig von späteren Ignore-Regeln sichtbar.
- Unversionierte reguläre Dateien werden descriptorgebunden mit `O_NOFOLLOW` gehasht und nur bis 256 KB als Promptvorschau gehalten. Symlinks werden über ihren Linktext statt über das Ziel abgebildet; von Git nicht gelistete Spezialdateien werden nicht gelesen.
- Der SHA-256-Fingerprint basiert auf Merge-Base, semantischem Pfadzustand, vollständigem Inhaltsdigest und Git-relevantem Modus. `untracked` und `added` sind darin absichtlich äquivalent, sodass Staging und Commit bei unverändertem Inhalt den Fingerprint nicht verändern.
- Die kanonische `paths`-Liste bleibt alphabetisch. Für Review-Snapshots priorisiert `review_paths` deterministisch aktuelle Working-Tree-/Untracked-Pfade vor bereits committierten Branchpfaden, ohne einen Git-Pfad zu entfernen; Agentenberichte werden erst danach dedupliziert angehängt.
- `src/agent_runtime.py` erzeugt keinen eigenen Git-Diff mehr, sondern rendert ausschließlich die vorab gesammelte kanonische Struktur.
- `src/orchestrator.py` ermittelt Git-Basis und Change-Set genau einmal pro Reviewschritt, verwendet sie gemeinsam für Dateisnapshots, Diffkontext und Logging und entfernt den agentenabhängigen `git diff --name-only`-Fallback. Git-Fehler enden im Normalmodus kontrolliert mit Exitcode 1.
- Ein expliziter Dry-Run außerhalb eines Git-Repositories erhält eine klar protokollierte leere Simulationsquelle, damit bestehende backendfreie CLI-Szenarien weiterlaufen; dieser Pfad ist im Normalmodus nicht erreichbar.
- Nach Claude-Finding F-001 unterscheidet `NotGitRepositoryError` einen tatsächlichen Nicht-Git-Pfad von vorhandenen, aber defekten Git-Metadaten. Nur der erste Fall darf im expliziten Dry-Run leer simuliert werden; echte Git-Defekte bleiben fail-closed.
- `tests/test_repo_changes.py` verwendet echte temporäre Git-Repositories für alle Pfadzustände, Ignore-/Rename-/Binär-/Symlinkfälle, Basisrefauflösung, gebundene Vorschau und Fingerprintstabilität. `tests/test_agent_runtime.py` prüft das Rendering einer bereits gesammelten Struktur.

## Ausgeführte Validierung

```text
VALIDATION_RESULT: PASS | PYTHONDONTWRITEBYTECODE=1 python3 -m pytest tests/test_repo_changes.py tests/test_agent_runtime.py -q -p no:cacheprovider | 0 (38 passed nach F-001)
VALIDATION_RESULT: PASS | PYTHONDONTWRITEBYTECODE=1 python3 -m pytest tests/ -v -p no:cacheprovider | 0 (182 passed nach F-001)
VALIDATION_RESULT: PASS | python3 -m py_compile src/repo_changes.py src/agent_runtime.py src/orchestrator.py | 0
VALIDATION_RESULT: PASS | ./run_task --dry-run --skip-git-check --test-command '' | 0; base_ref=origin/master, merge_base=0bd3bad, 37 Branchpfade
VALIDATION_RESULT: PASS | bestehender CLI-Dry-Run außerhalb eines Git-Repositories | 0; explizite leere Dry-Run-Simulation
VALIDATION_RESULT: PASS | git diff --check | 0
```

## Abweichungen vom Plan

- Die konkrete Basiserkennung liegt vollständig in `repo_changes.py`; eine neue CLI-/TOML-Option wurde wie geplant nicht vorgezogen.
- Git listet POSIX-FIFOs nicht als unversionierte Pfade. Der geplante Spezialdatei-Test beweist daher, dass der FIFO vollständig fehlt und nicht gelesen wird, statt einen künstlichen Change-Record zu verlangen.
- Der erste Vollsuitelauf legte den bestehenden Nicht-Git-Dry-Run offen. Nur für `dry_run` wurde eine leere, klar geloggte Simulation ergänzt; der Normalmodus bleibt fail-closed.
- Zusätzlich zur alphabetischen kanonischen Liste existiert eine deterministische Reviewreihenfolge, damit aktuelle Slice-Dateien bei begrenzten Dateisnapshots nicht von bereits committierten Slices verdrängt werden.

## Offene Risiken

- Git kann Pfadnamen enthalten, die nicht strikt als UTF-8 dekodierbar sind; interne Pfadverarbeitung muss `os.fsdecode`/Surrogate-Escapes respektieren und Prompttext ersetzend darstellen.
- Unversionierte Binärdateien dürfen für den Fingerprint gelesen, aber nicht unbeschränkt in Prompts eingebettet werden.
- Ein Remote-Defaultbranch kann fehlen oder veraltet sein; der gewählte Basis-Ref und Merge-Base müssen deshalb sichtbar bleiben und Fehler laut ausgeben.
- Die Basiserkennung wird in Slice 07 persistierbar gemacht. Bis dahin wird sie je Reviewschritt erneut ermittelt; ein zwischenzeitlich administrativ verschobener Defaultref könnte den Bezugsrahmen ändern und muss im Log auffallen.
- Der Orchestrator-Wiringpfad besitzt in Slice 05 noch keinen eigenen Regressionstest. Dieser benötigt einen bislang nicht freigegebenen Testpfad und wird als F-002 in Slice 10 weitergeführt.

## Review-Feedback von Claude

Claude Sonnet 5 mit Effort `high` prüfte den schreibgeschützten, scopegenauen Snapshot. Der Harness lief genau einmal: `git diff --check` sauber, Schreibprobe blockiert und 181 Tests vor der Finding-Korrektur grün. Ergebnis: `PHASE2_APPROVAL: YES`, `OPEN_FINDINGS: NONE`.

- F-001 (OBSERVATION): Der Dry-Run fing jeden `RepositoryChangeError` ab und konnte dadurch in einem realen Repository echte Git-Defekte als leere Simulation maskieren.
- F-002 (OBSERVATION): Die einmalige Sammlung und Wiederverwendung im Orchestrator sowie das Fehler-Routing besitzen noch keinen eigenen Orchestrator-Regressionstest.

## Review-Feedback von Antigravity

Antigravity prüfte den finalen F-001-Delta und die F-002-Entscheidung in einer frischen scopegenauen Read-only-Kopie. Der Harness lief genau einmal: 182 Tests bestanden, der Diffcheck war sauber und die Schreibprobe blockiert. F-001 wurde als korrekt geschlossen und F-002 als passend nach Slice 10 verschoben bestätigt; neue Findings entstanden nicht.

```text
REVIEWER: antigravity
VALIDATION_RESULT: PASS | review_harness.py | 182 tests passed; F-001 closed; F-002 correctly deferred; no regressions
SLICE_APPROVAL: 05 | YES
OPEN_FINDINGS: NONE
STATUS: DONE
```

Die strukturierte Aufrufhülle meldete `status=SUCCESS`, einen Turn und 86,9 Sekunden Laufzeit.

## Review-Antworten von Codex

- F-001 wurde sofort geschlossen: eigener `NotGitRepositoryError`, enger Catch im Dry-Run und Grenztest für Nicht-Git gegen defekte `.git`-Metadaten.
- F-002 wird akzeptiert und für Slice 10 vorgemerkt. Ein neuer Orchestrator-Testpfad wird ohne gesonderten Test-Riegel nicht vorgezogen.

## Entscheidungstabelle

| ID | Quelle | Finding | Klasse | Entscheidung | Umsetzung |
|---|---|---|---|---|---|
| F-001 | Claude | Dry-Run maskiert beliebige Git-Fehler als leere Simulation | OBSERVATION | CLOSED | Fehlerklasse getrennt, Catch verengt, Regressionstest ergänzt; 182 Tests grün |
| F-002 | Claude | Orchestrator-Wiring ohne eigenen Regressionstest | OBSERVATION | DEFERRED | In Slice 10 bei genehmigtem Orchestrator-Testscope nachziehen |

## Rückdokumentation in den Arbeitsplan

Der Arbeitsplan verlinkt diese Slice-MD, nennt die tatsächlich betroffenen Dateien und weist die doppelte Freigabe aus. F-001 ist geschlossen, F-002 bleibt als nicht blockierende Folgearbeit für Slice 10 sichtbar.

## Freigabestatus

- Teständerungen: am 2026-08-11 für die zwei dokumentierten Testpfade und die gebundene Testintention freigegeben.
- Lokale Implementierung und Validierung: abgeschlossen.
- Claude-Review: `PHASE2_APPROVAL: YES`; keine offenen Blocker.
- Antigravity-Review: freigegeben mit `SLICE_APPROVAL: 05 | YES` und `OPEN_FINDINGS: NONE`.
- Lokaler Commit: durch das positive Antigravity-Verdikt autorisiert; scopegenaue Commitprüfung ausstehend.
