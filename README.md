# Dual-Agent Ticketing Orchestrator

Ein leistungsstarkes CLI-Tool zur Automatisierung komplexer Coding-Aufgaben durch einen intelligenten, zweiphasigen KI-Agenten-Workflow (Planung und Implementierung).

## 🌟 Überblick

Der Orchestrator nimmt eine Aufgabenbeschreibung im Markdown-Format, plant die Umsetzung im Detail (Phase 1) und führt anschließend die notwendigen Code-Änderungen durch (Phase 2). Der gesamte Verarbeitungsstatus und alle generierten Artefakte werden im Ordner `.orchestrator/` gesichert.

### Kernfunktionen

- **Zweiphasiger Agenten-Workflow**: Klare Trennung zwischen Lösungsdesign (Planung) und tatsächlicher Ausführung (Implementierung).
- **Zustandsspeicherung & Resume (Fortsetzen)**: Wird ein Prozess unterbrochen, kann er über `.orchestrator/state.json` exakt dort fortgesetzt werden, wo er gestoppt hat.
- **Live-Streaming**: Im Terminal kann der direkte Gedankengang und Fortschritt der Agenten im kompakten Modus mitverfolgt werden.
- **Test-Integration**: Kommandozeilen-Tests können direkt in den Workflow integriert werden.
- **Agenten Fallback**: Unterstützt einen automatisierten Fallback (z.B. auf Gemini), um Ausfallsicherheit zu gewährleisten.

## � Voraussetzungen und Installation

Dieses Tool wurde primär für **Linux/Unix-Umgebungen** (inkl. macOS und WSL unter Windows) entwickelt und benötigt eine `bash`-kompatible Shell.

### Benötigte KI CLI-Tools

Der Orchestrator verlässt sich auf externe Kommandozeilen-Tools für die Kommunikation mit den Modellen. Es wird vorausgesetzt, dass **mindestens zwei, idealerweise drei** der folgenden CLI-Tools auf dem System installiert und im `$PATH` verfügbar sind:

- **`codex`** (OpenAI / ChatGPT CLI) - Häufig Hauptakteur für Planung und Code-Generierung.
- **`claude`** (Anthropic CLI) - Wird standardmäßig für Review, Bewertung oder eigenständige Agenten-Aufgaben genutzt.
- **`gemini`** (Google Gemini CLI) - Dient u.a. als nützlicher Fallback-Agent bei Rate-Limits oder als alternative Engine.

### Globale Verfügbarkeit einrichten

Damit das Tool (`bearbeite_aufgabe`) aus jedem beliebigen Projektverzeichnis komfortabel aufgerufen werden kann, empfiehlt es sich, einen symbolischen Link (Symlink) in einem Verzeichnis anzulegen, das sich in deinem System-Pfad (`$PATH`) befindet (z. B. `~/.local/bin` oder `/usr/local/bin`):

```bash
# Optional: Verzeichnis anlegen, falls es noch nicht existiert
mkdir -p ~/.local/bin

# Symbolischen Link erstellen (ersetze den Pfad durch deinen tatsächlichen Klon-Pfad)
ln -s /absoluter/pfad/zu/Dual-Agent-Orchestrator/bearbeite_aufgabe ~/.local/bin/bearbeite_aufgabe

# Sicherstellen, dass das Skript ausführbar ist
chmod +x /absoluter/pfad/zu/Dual-Agent-Orchestrator/bearbeite_aufgabe
```

Sobald dies eingerichtet ist und `~/.local/bin` in deinem Pfad liegt (oft Standard in modernen Distributionen), kannst du `bearbeite_aufgabe` in jedem beliebigen Ordner in deinem Terminal aufrufen.

## �🚀 Schnellstart

Erstelle eine Datei namens `Aufgabe.md` mit deiner Anforderung und starte den Orchestrator:

```bash
./bearbeite_aufgabe
```

*Wenn bereits ein `.orchestrator/state.json` existiert und nicht als "done" markiert ist, setzt das Skript den letzten Lauf automatisch fort (Resume-first).*

## 📖 Nutzung

### Eigene Task-Datei verwenden

Du kannst eine beliebige Markdown-Datei als Aufgabe übergeben:

```bash
./bearbeite_aufgabe my-task.md
```

### Testkommando konfigurieren

Tests in Phase 2 (Implementierung) können über den Parameter `--test-command` gesteuert werden. Wenn der Test fehlschlägt, kann der Agent versuchen, den Fehler zu beheben.

```bash
# Mit Pytest
python3 src/orchestrator.py --task-file my-task.md --test-command "pytest -x"

# Mit npm
python3 src/orchestrator.py --task-file my-task.md --test-command "npm test"

# Tests explizit überspringen
python3 src/orchestrator.py --task-file my-task.md --test-command ""
```

### Dry-Run Modus

Nützlich zum Testen der Konfiguration, ohne echte Agenten-Aufrufe auszulösen:

```bash
python3 src/orchestrator.py --dry-run --auto --task-file example-task.md --test-command ""
```

## ❓ Hilfe

Alle verfügbaren Argumente und Optionen können über die Hilfe angezeigt werden:

```bash
./bearbeite_aufgabe --help
# oder direkt über Python:
python3 src/orchestrator.py --help
```
