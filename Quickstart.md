# Schnellstart

Diese Anleitung führt von einem vorbereiteten Repository zu einem abgeschlossenen State-v3-Lauf. [README.md](README.md) enthält den vollständigen Workflow, die Konfiguration und die CLI-Referenz. Hintergründe zum Entwurf stehen im [Architektur- und Fachkonzept](docs/reference/architecture-and-domain-concept.md); die Produktpositionierung erläutert der [Marktvergleich](docs/reference/market-comparison.md).

## 1. Voraussetzungen prüfen

Benötigt werden Python 3.11 oder neuer, Git und authentifizierte Installationen aller drei Rollen-CLIs:

```bash
python3 --version
git --version
codex --version
claude --version
agy --version
```

Der Orchestrator läuft unter Linux, macOS oder WSL2. Natives Windows wird derzeit nicht unterstützt. Unter WSL2 muss `agy.exe` explizit konfiguriert werden, falls der native Befehl `agy` nicht verfügbar ist.

## 2. Zielrepository und Branch vorbereiten

Checke den Branch aus, auf dem der Orchestrator lokale Slice-Commits erstellen darf. Beginne mit einem sauberen Worktree, sofern der Lauf nicht bewusst für vorhandene Änderungen konfiguriert wurde:

```bash
cd /path/to/target-repository
git branch --show-current
git status --short
```

Der Orchestrator erstellt ausschließlich lokale Commits. Er pusht, mergt oder force-pusht niemals und schreibt die Historie nicht um.

Lege den Zielbranch vor dem Lauf selbst an. Agenten dürfen keine Branchoperationen ausführen:

```bash
git switch -c feature/mein-vorhaben
```

## 3. Begrenzte Aufgabe formulieren

Kopiere [example-task.md](example-task.md) als `task.md` in das Zielrepository und ersetze den Beispielinhalt. Lege mindestens Folgendes fest:

- das beabsichtigte Ergebnis;
- die exakt erlaubten Pfade;
- Akzeptanzkriterien und Validierungsbefehle;
- einen expliziten Nicht-Scope;
- Bedingungen, die eine Benutzerentscheidung erfordern.

Jede Aufgabe benötigt diese Marker:

```text
ORCHESTRATOR_MODE: IMPLEMENT
TARGET_BRANCH: feature/mein-vorhaben
TASK_SCOPE: src/example.py, tests/test_example.py, docs/internal/example-work-plan.md
```

Arbeitsplan und vorbereitete Slice-Auditdokumente müssen innerhalb des deklarierten Pfadumfangs liegen. Geheimnisse oder Zugangsdaten gehören nicht in die Aufgabendatei.

## 4. Probelauf ausführen

Prüfe die Installation aus diesem Orchestrator-Repository, ohne Agenten aufzurufen oder ein Repository zu verändern:

```bash
./run_task --dry-run --task-file example-task.md --quiet
```

Ein erfolgreicher Probelauf endet mit Exitcode 0.

## 5. Arbeitsplan separat erstellen

Für den bewährten Zweischrittprozess kopierst du [example-plan-task.md](example-plan-task.md) oder beginnst die erste Aufgabe so:

```text
ORCHESTRATOR_MODE: PLAN_ONLY
WORK_PLAN_PATH: docs/internal/mein-vorhaben-work-plan.md
TARGET_BRANCH: feature/mein-vorhaben
TASK_SCOPE: docs/internal/mein-vorhaben-work-plan.md
```

Starte den Lauf. Codex erstellt nur das Arbeitsplan-MD. Claude und Antigravity prüfen denselben Planfingerprint; die Produkttestsuite wird dabei nicht ausgeführt. Danach endet der Prozess mit Exitcode 4 am Plangate:

```bash
run_task --task-file task.md

run_task --resume --task-file task.md \
  --approve-gate \
  --gate-actor "Ihr Name" \
  --gate-rationale "Arbeitsplan und persistierten Fingerprint geprüft"
```

Nach dieser Freigabe wird ausschließlich das Arbeitsplanartefakt reviewed und lokal commitet. Prüfe den Commit, bevor du eine zweite Aufgabe im Modus `IMPLEMENT` erstellst.

Claude- und Antigravity-Ergebnisse werden in einem automatisch verwalteten Prüfprotokoll am Ende des deklarierten Arbeitsplans dokumentiert. Der Orchestrator hält diese Blöcke aus dem fachlichen Fingerprint heraus und speichert Markdown beim Commit mit Git-Modus `100644`.

## 6. Implementierung starten

Rufe den Starter aus dem Zielrepository über seinen absoluten Pfad oder einen konfigurierten globalen Symlink auf:

```bash
/absolute/path/to/Dual-Agent-Orchestrator/run_task --task-file task.md
```

Liegt `run_task` in `PATH`, genügt die Kurzform:

```bash
run_task --task-file task.md
```

Die Implementierungsaufgabe verwendet `ORCHESTRATOR_MODE: IMPLEMENT`, denselben `TARGET_BRANCH`, verweist auf den freigegebenen Arbeitsplan und deklariert alle erlaubten Umsetzungs-, Test- und Auditpfade in `TASK_SCOPE`. Claude Sonnet mit Effort `high` und Antigravity prüfen zuerst den ausführbaren Plan. Erst nach dem expliziten Plangate beginnt Slice 1. Erfolgreiche Slices werden lokal commitet; anschließend folgt ein branchweiter Abschlussreview.

## 7. Angehaltenen Lauf fortsetzen

Ein nicht abgeschlossener Einzelaufgabenlauf wird automatisch fortgesetzt, wenn derselbe Befehl wiederholt wird. Nach Auflösung eines protokollierten Gates oder einem Prozessneustart wird explizit fortgesetzt:

```bash
run_task --resume --task-file task.md
```

Falls Exitcode 4 eine explizite Gate-Entscheidung verlangt, muss vor der Freigabe des exakten Fingerprints der persistierte Grund geprüft werden:

```bash
run_task --resume --task-file task.md \
  --approve-gate \
  --gate-actor "Ihr Name" \
  --gate-rationale "Persistiertes Gate geprüft und Fortsetzung freigegeben"
```

Die Exitcodes 2 und 3 kennzeichnen einen fortsetzbaren Quota- beziehungsweise Agentenfehler. Behebe die gemeldete Ursache und setze denselben Lauf fort. `.orchestrator/state.json` und Checkpointdateien dürfen niemals manuell bearbeitet werden.

## 8. Abschluss prüfen

Ein abgeschlossener Lauf endet mit Exitcode 0. Prüfe die entstandenen lokalen Commits und den sauberen Status:

```bash
git log --oneline --decorate -n 10
git status --short
```

Laufzeitstatus, Checkpoints, Logs und persistierte Agentenausgaben liegen unterhalb von `.orchestrator/`. Menschenlesbare Arbeitspläne und Slice-Auditdokumente verbleiben in den von der Aufgabe deklarierten Pfaden.
