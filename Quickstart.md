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

## 2. Zielrepository vorbereiten

Checke den Branch aus, auf dem der Orchestrator lokale Slice-Commits erstellen darf. Beginne mit einem sauberen Worktree, sofern der Lauf nicht bewusst für vorhandene Änderungen konfiguriert wurde:

```bash
cd /path/to/target-repository
git branch --show-current
git status --short
```

Der Orchestrator erstellt ausschließlich lokale Commits. Er pusht, mergt oder force-pusht niemals und schreibt die Historie nicht um.

## 3. Begrenzte Aufgabe formulieren

Kopiere [example-task.md](example-task.md) als `task.md` in das Zielrepository und ersetze den Beispielinhalt. Lege mindestens Folgendes fest:

- das beabsichtigte Ergebnis;
- die exakt erlaubten Pfade;
- Akzeptanzkriterien und Validierungsbefehle;
- einen expliziten Nicht-Scope;
- Bedingungen, die eine Benutzerentscheidung erfordern.

Arbeitsplan und vorbereitete Slice-Auditdokumente müssen innerhalb des deklarierten Pfadumfangs liegen. Geheimnisse oder Zugangsdaten gehören nicht in die Aufgabendatei.

## 4. Probelauf ausführen

Prüfe die Installation aus diesem Orchestrator-Repository, ohne Agenten aufzurufen oder ein Repository zu verändern:

```bash
./run_task --dry-run --task-file example-task.md --quiet
```

Ein erfolgreicher Probelauf endet mit Exitcode 0.

## 5. Produktiven Lauf starten

Rufe den Starter aus dem Zielrepository über seinen absoluten Pfad oder einen konfigurierten globalen Symlink auf:

```bash
/absolute/path/to/Dual-Agent-Orchestrator/run_task --task-file task.md
```

Liegt `run_task` in `PATH`, genügt die Kurzform:

```bash
run_task --task-file task.md
```

Der Orchestrator lässt Codex begrenzte Slices planen und implementieren. Claude Sonnet mit Effort `high` prüft den Plan und gezielt die Slice-Änderungen. Antigravity prüft jeden vollständigen, von Claude freigegebenen Slice. Erfolgreiche Slices werden lokal commitet; anschließend folgt ein branchweiter Abschlussreview.

## 6. Angehaltenen Lauf fortsetzen

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

## 7. Abschluss prüfen

Ein abgeschlossener Lauf endet mit Exitcode 0. Prüfe die entstandenen lokalen Commits und den sauberen Status:

```bash
git log --oneline --decorate -n 10
git status --short
```

Laufzeitstatus, Checkpoints, Logs und persistierte Agentenausgaben liegen unterhalb von `.orchestrator/`. Menschenlesbare Arbeitspläne und Slice-Auditdokumente verbleiben in den von der Aufgabe deklarierten Pfaden.
