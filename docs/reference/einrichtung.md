# Einrichtung: vom Download zum ersten Lauf

Diese Anleitung führt vom heruntergeladenen Repository bis zum ersten
vollständigen Lauf – für ein **vorhandenes Projekt** ebenso wie für einen
**Neuanfang**, bei dem es nur eine Projektbeschreibung gibt.

Sie ist in Schichten aufgebaut. Teil 1 bis 3 setzen nichts voraus und geben
jeden Befehl zum Kopieren vor. Teil 4 ist die Referenz für alle, die wissen
wollen, was dabei im Einzelnen geschieht und wo die Grenzen liegen.

> [!TIP]
> **Der kürzeste Weg.** Teil 1 einmal erledigen. Danach entweder Teil 2
> (vorhandenes Projekt) oder Teil 3 (Neuanfang). Wer verstehen möchte, was der
> Orchestrator während eines Laufs tut, liest
> [Wie der Orchestrator arbeitet](ablauf-des-orchestrators.md).

---

## Das Bild vorab

Der Orchestrator liegt **einmal** auf Ihrem Rechner. Er arbeitet in **Ihrem**
Projekt, nicht in seinem eigenen Verzeichnis: Sie starten ihn im Projektordner,
und er nimmt dort die Aufgaben aus einem Eingangsordner.

```
~/werkzeuge/Dual-Agent-Orchestrator      der Orchestrator, einmal installiert
        │
        │  run_task --watch               gestartet im Projektordner
        ▼
~/projekte/mein-projekt                  Ihr Projekt, ein Git-Repository
    ├── inbox/            hier legen Sie Ihre Ideen ab
    ├── outbox/done/      Erledigtes, mit Zeitstempel
    ├── outbox/failed/    Gescheitertes, mit Diagnose
    ├── .orchestrator/    Buchführung des Orchestrators – nie von Hand ändern
    ├── orchestrator.toml Regeln für dieses Projekt, vor allem der Testbefehl
    └── AGENTS.md         was die Agenten über das Projekt wissen sollen
```

Zwei KI-Agenten arbeiten darin: **Codex** plant und programmiert, **Claude**
prüft. Der Orchestrator führt die Tests aus und legt die Ergebnisse als lokale
Commits auf einem eigenen Branch ab. Er pusht nie und führt nie zusammen – das
bleibt Ihre Entscheidung.

---

## Teil 1 — Einmalig: den Orchestrator installieren

### 1.1 Was Sie brauchen

| Was | Mindestens | Prüfen mit |
|---|---|---|
| Linux, macOS oder Windows mit WSL2 | – | natives Windows wird nicht unterstützt |
| Python | 3.11 | `python3 --version` |
| Git | – | `git --version` |
| Codex CLI | 0.156.1 | `codex --version` |
| Claude Code | 2.1.280 | `claude --version` |
| ein ChatGPT-Konto mit Zugriff auf **GPT-6 Sol** | – | `codex login` |
| ein Claude-Konto mit Zugriff auf **Opus** | – | `claude`, dann der Anmeldung folgen |

Fehlt eine der beiden Kommandozeilen, installieren Sie sie nach der Anleitung
des Herstellers, beispielsweise über Node.js:

```bash
npm install -g @openai/codex
npm install -g @anthropic-ai/claude-code
```

> [!IMPORTANT]
> Beide Programme müssen **angemeldet** sein, bevor der Orchestrator startet.
> Er fragt nicht nach Zugangsdaten und legt keine an. Ältere Versionen als die
> oben genannten weist er beim ersten Aufruf ab.

### 1.2 Herunterladen

```bash
mkdir -p ~/werkzeuge
git clone https://github.com/dieteresser62-lab/Dual-Agent-Orchestrator.git \
  ~/werkzeuge/Dual-Agent-Orchestrator
```

> [!TIP]
> **Unter WSL2** gehören Orchestrator und Projekte ins Linux-Dateisystem, also
> unter `~/`, nicht unter `/mnt/c/…`. Dateizugriffe über die Windows-Grenze
> sind erheblich langsamer, und der Orchestrator liest viel.

### 1.3 Als Befehl verfügbar machen

Damit `run_task` in jedem Projektordner funktioniert:

```bash
mkdir -p ~/.local/bin
ln -s ~/werkzeuge/Dual-Agent-Orchestrator/run_task ~/.local/bin/run_task
```

Prüfen Sie, ob `~/.local/bin` im Suchpfad liegt:

```bash
run_task --help
```

Meldet die Shell `command not found`, ergänzen Sie den Suchpfad einmalig und
öffnen ein neues Terminal:

```bash
echo 'export PATH="$HOME/.local/bin:$PATH"' >> ~/.bashrc
```

### 1.4 Installation prüfen

Ein Probelauf ohne Agenten und ohne Änderungen:

```bash
cd ~/werkzeuge/Dual-Agent-Orchestrator
./run_task --dry-run --task-file example-task.md --quiet
echo $?
```

Die Ausgabe `0` bedeutet: Installation in Ordnung.

---

## Teil 2 — Ein vorhandenes Projekt anschließen

### 2.1 Was Ihr Projekt mitbringen muss

- [ ] Es ist ein **Git-Repository mit mindestens einem Commit**.
- [ ] Es hat einen Branch namens **`master`** – siehe den Hinweis unten.
- [ ] Der Arbeitsbaum ist **sauber** (`git status --short` zeigt nichts an).
- [ ] Es gibt einen **Testbefehl, der jetzt grün ist**.

> [!WARNING]
> **Der Branchname `master` ist derzeit Pflicht.** Der Orchestrator misst jede
> Arbeit gegen `master`. Heißt Ihr Hauptbranch `main`, benennen Sie ihn lokal um
> (`git branch -m main master`) oder legen `master` zusätzlich an
> (`git branch master main`) und halten ihn auf dem Stand, von dem neue Arbeit
> ausgehen soll. Ohne `master` endet jeder Start mit
> `could not resolve merge-base`.

In den Projektordner wechseln und auf `master` gehen:

```bash
cd ~/projekte/mein-projekt
git switch master
git status --short
```

### 2.2 Den Orchestrator aus Git heraushalten

Der Orchestrator legt Eingangs-, Ausgangs- und Buchführungsordner im Projekt
an. Sie dürfen nicht versioniert werden. Ergänzen Sie `.gitignore`:

```bash
cat >> .gitignore <<'EOF'

# Dual-Agent-Orchestrator
.orchestrator/
inbox/
outbox/
.codex/
task.md
EOF
```

### 2.3 Den Testbefehl festlegen: `orchestrator.toml`

Das Wichtigste an dieser Datei ist **der Testbefehl**. Der Orchestrator führt ihn
nach jedem Arbeitspaket selbst aus; nur ein grünes Ergebnis kann zu einem
Commit führen.

Legen Sie im Projektordner eine Datei `orchestrator.toml` an. Eine Vorlage für
ein Python-Projekt:

```toml
[paths]
productive = ["src/**", "pyproject.toml"]
tests = ["tests/**"]
documentation = ["docs/**", "*.md"]
generated = [".orchestrator/**", "**/__pycache__/**", ".pytest_cache/**"]

[validation]
default_command = ["python3", "-m", "pytest", "tests/", "-q"]
default_timeout_seconds = 1200

[workflow]
plan_gate = false
test_change_gate = false
manual_slice_gate = false
```

<details>
<summary><b>Vorlage für ein Node.js-Projekt</b></summary>

```toml
[paths]
productive = ["src/**", "package.json", "package-lock.json", "tsconfig.json"]
tests = ["tests/**", "src/**/*.test.*"]
documentation = ["docs/**", "*.md"]
generated = [".orchestrator/**", "node_modules/**", "dist/**", "coverage/**"]

[validation]
default_command = ["npm", "test"]
default_timeout_seconds = 1200

[workflow]
plan_gate = false
test_change_gate = false
manual_slice_gate = false
```

</details>

Passen Sie die Pfade an Ihr Projekt an. Was unter `[paths]` fehlt, zählt als
Produktivcode – ein vergessenes Muster richtet also keinen Schaden an.

**Jetzt den Testbefehl einmal von Hand ausführen.** Er muss auf `master` grün
sein:

```bash
python3 -m pytest tests/ -q      # oder: npm test
```

> [!CAUTION]
> Ein Testbefehl, der schon vor dem ersten Arbeitspaket rot ist, macht jedes
> Arbeitspaket rot. Reparieren Sie ihn vorher.

### 2.4 Den Agenten das Projekt erklären: `AGENTS.md`

Die Datei `AGENTS.md` im Projektordner bekommt Codex bei jedem Auftrag mit
(die ersten 12.000 Zeichen). Sie ist keine Pflicht, aber der größte Hebel für
gute Ergebnisse. Eine Vorlage:

```markdown
# Mein Projekt

## Worum es geht
Eine Anwendung für … Zielgruppe: … Das Wichtigste für Nutzer: …

## Technik
Sprache, Frameworks, Build: …
Tests: `python3 -m pytest tests/ -q` – jede Änderung braucht Tests.

## Regeln
- Keine neuen Abhängigkeiten ohne Grund.
- Keine Netzwerkaufrufe zur Laufzeit.
- Bestehende Dateiformate bleiben lesbar.

## Nicht anfassen
- `legacy/` – wird separat abgelöst.
```

Ausdrücklich mitgegeben wird nur die `AGENTS.md`. Verlassen Sie sich nicht
darauf, dass weitere Rollendateien wie `CLAUDE.md` oder `CODEX.md` gelesen
werden – was beide Agenten wissen sollen, gehört in die `AGENTS.md`.

### 2.5 Einrichtung festschreiben

```bash
git add .gitignore orchestrator.toml AGENTS.md
git commit -m "chore: prepare the repository for the orchestrator"
mkdir -p inbox
```

### 2.6 Die erste Idee

Beschreiben Sie in normalem Deutsch, was entstehen soll – nicht wie. Pfade,
Arbeitspakete oder Tests geben Sie nicht vor; das plant der Orchestrator selbst.

```bash
nano inbox/einkaufsliste.md
```

```markdown
# Einkaufsliste aus mehreren Rezepten

Man soll mehrere Rezepte auswählen können und daraus eine einzige
Einkaufsliste bekommen. Gleiche Zutaten gehören zusammengefasst. Was sich nicht
sauber zusammenrechnen lässt, soll nebeneinander stehen bleiben – lieber
„Petersilie: 2 EL, 1 Bund" als eine erfundene Gesamtzahl.
```

**Gut formuliert** ist eine Idee, wenn sie sagt, *was* ein Nutzer danach kann
und woran man erkennt, dass es richtig ist. Wer einen Branchnamen festlegen
will, schreibt zusätzlich eine Zeile `TARGET_BRANCH: feature/einkaufsliste`.

### 2.7 Starten

Ein Lauf dauert leicht ein bis zwei Stunden. Starten Sie ihn deshalb in einer
Sitzung, die weiterläuft, wenn das Terminal schließt, und schreiben Sie das
Protokoll **außerhalb** des Projektordners mit:

```bash
mkdir -p ~/orchestrator-logs
tmux new -s orchestrator
cd ~/projekte/mein-projekt
run_task --watch --verbose 2>&1 | tee -a ~/orchestrator-logs/mein-projekt.log
```

Mit <kbd>Strg</kbd>+<kbd>B</kbd>, dann <kbd>D</kbd> lösen Sie sich von der
Sitzung; sie läuft weiter. Zurück kommen Sie mit `tmux attach -t orchestrator`.

Braucht Ihr Testbefehl eine virtuelle Umgebung oder bestimmte
Umgebungsvariablen, richten Sie sie **vor** dem Start im selben Terminal ein.
Der Orchestrator führt die Tests mit genau dieser Umgebung aus.

> [!WARNING]
> Keine Protokolldatei **im** Projektordner ablegen. Eine unversionierte fremde
> Datei macht den Arbeitsbaum „schmutzig", und der Orchestrator verweigert dann
> den Wechsel auf den Zielbranch.

### 2.8 Zusehen

| Was Sie sehen wollen | Wo |
|---|---|
| was gerade passiert | das Protokoll: `tail -f ~/orchestrator-logs/mein-projekt.log` |
| was noch wartet | `ls inbox/` |
| was fertig ist | `ls outbox/done/` |
| die entstandenen Commits | `git log --oneline --graph --all -20` |
| Plan und Prüfberichte | `docs/internal/` im Projekt, auf dem Zielbranch |

Der Orchestrator arbeitet ohne Rückfragen durch: Plan, jedes Arbeitspaket mit
Test und Prüfung, am Ende eine Abnahme des gesamten Branches. Findet die
Abnahme noch etwas, legt er selbst eine Folgeaufgabe in den Eingang
(`…_followup01.md`) und arbeitet sie ab. Fertig ist er, wenn der Eingang leer
ist.

### 2.9 Wenn er anhält

Das Protokoll nennt am Ende einen Exitcode und einen Grund.

| Exitcode | Bedeutung | Was Sie tun |
|---:|---|---|
| `0` | fertig | weiter mit 2.10 |
| `2`, `3` | Kontingent erschöpft oder ein Agent ist ausgefallen | später einfach erneut `run_task --watch` starten – er setzt exakt an der Stelle fort |
| `4` | eine Entscheidung wird gebraucht | Grund im Protokoll lesen, dann entscheiden (siehe unten) |
| `5` | endgültiges Urteil, etwa eine abgelehnte Prüfung | Grund im Protokoll lesen; dieser Lauf ist beendet |

Eine Entscheidung erteilen Sie mit Begründung:

```bash
run_task --watch --resume --approve-gate --gate-rationale "Pfade geprüft, passt"
```

> [!IMPORTANT]
> Niemals Dateien unter `.orchestrator/` von Hand ändern oder löschen, während
> eine Aufgabe unterwegs ist. Daran hängt die Fähigkeit, nach jedem Abbruch
> genau dort weiterzumachen.

Den Orchestrator beenden Sie in seiner tmux-Sitzung mit <kbd>Strg</kbd>+<kbd>C</kbd>.
Ein späterer Start mit `run_task --watch` setzt fort.

### 2.10 Abschließen

Der Orchestrator hat alles auf einem eigenen Branch committet, zum Beispiel
`feature/einkaufsliste`. Zusammenführen ist Ihre Sache:

```bash
git switch master
git log --oneline master..feature/einkaufsliste     # was hinzukommt
git merge --no-ff feature/einkaufsliste
```

> [!IMPORTANT]
> **Vor der nächsten Idee zurück auf `master`.** Einen neuen Zielbranch legt der
> Orchestrator vom gerade aktiven Branch aus an. Steht der Arbeitsbaum noch auf
> dem Branch der letzten Aufgabe, erbt die neue Aufgabe deren Commits – und ihre
> Abnahme liest sie mit.

---

## Teil 3 — Von null: nur eine Projektbeschreibung

Ein leerer Ordner reicht dem Orchestrator nicht. Er braucht einen ersten
Commit, einen `master`-Branch und vor allem **einen Testbefehl, der schon beim
ersten Arbeitspaket funktioniert** – ohne ihn kann er kein Paket validieren und
hält beim ersten an. Diese Grundlage schaffen Sie einmal selbst; danach läuft
alles wie in Teil 2.

### 3.1 Das Repository anlegen

```bash
mkdir -p ~/projekte/mein-projekt
cd ~/projekte/mein-projekt
git init -b master
```

### 3.2 Die Beschreibung ins Repository legen

Legen Sie die vollständige Projektbeschreibung als `docs/spezifikation.md` ab.
Dort können Codex und Claude sie bei jedem Schritt nachlesen; die späteren Ideen
im Eingang bleiben kurz und verweisen darauf.

```bash
mkdir -p docs
cp /pfad/zu/meiner/beschreibung.md docs/spezifikation.md
```

### 3.3 Ein Gerüst mit einem grünen Test

Entscheiden Sie Sprache und Werkzeuge und legen Sie ein minimales Gerüst mit
**einem** Test an, der besteht. Für Python:

```bash
mkdir -p src/meinprojekt tests
touch src/meinprojekt/__init__.py
printf 'def test_start():\n    assert True\n' > tests/test_start.py
python3 -m pytest tests/ -q
```

Fehlt pytest, installieren Sie es unter Ubuntu und WSL mit
`sudo apt install python3-pytest`, sonst mit `python3 -m pip install pytest`.

<details>
<summary><b>Dasselbe für Node.js mit Vitest</b></summary>

```bash
npm init -y
npm install --save-dev vitest
npm pkg set scripts.test="vitest run"
mkdir -p tests
printf "import { test, expect } from 'vitest';\ntest('start', () => expect(1).toBe(1));\n" \
  > tests/start.test.js
npm test
```

</details>

Wer das Gerüst lieber erzeugen lässt, fragt Codex oder Claude **direkt**, ohne
Orchestrator – etwa: *„Lies docs/spezifikation.md. Richte ein leeres Projekt mit
der passenden Werkzeugkette und genau einem bestehenden Test ein. Noch keine
Fachlogik."* Prüfen Sie danach selbst, dass der Test grün ist.

### 3.4 Einrichten und festschreiben

Jetzt wie in Teil 2: `.gitignore` ergänzen (2.2), `orchestrator.toml` mit dem
Testbefehl anlegen (2.3), `AGENTS.md` schreiben (2.4). In die `AGENTS.md` gehört
ein Absatz, der auf die Beschreibung verweist:

```markdown
Die vollständige Projektbeschreibung steht in `docs/spezifikation.md`. Sie ist
verbindlich; weicht eine Aufgabe davon ab, gilt die Aufgabe.
```

Dann alles als ersten Commit festschreiben:

```bash
git add -A
git commit -m "chore: establish the project baseline"
mkdir -p inbox
```

### 3.5 Die Beschreibung in Schritte teilen

Geben Sie nicht die ganze Beschreibung auf einmal in den Eingang. Schneiden Sie
sie in **Zuwächse**, von denen jeder für sich etwas Nutzbares ergibt, und geben
Sie sie **nacheinander** hinein:

1. Ein erster, durchgehender Kern – das Kleinste, das schon benutzbar ist.
2. Danach je Zuwachs ein Merkmal aus der Beschreibung.

Eine Idee im Eingang verweist dann nur noch auf die Beschreibung:

```markdown
# Rezepte anlegen und anzeigen

Setze Abschnitt 3 aus docs/spezifikation.md um: Rezepte anlegen, bearbeiten
und als Liste anzeigen. Speicherung und Suche folgen später.
```

Nach jedem Zuwachs: Ergebnis ansehen, nach `master` zusammenführen, auf
`master` bleiben (2.10) – dann die nächste Idee.

> [!NOTE]
> **Warum nacheinander?** Der Orchestrator arbeitet ohnehin eine Aufgabe nach
> der anderen ab. Und jede neue Aufgabe setzt auf dem Branch auf, der gerade
> aktiv ist – liegen mehrere Ideen gleichzeitig im Eingang, bauen sie
> aufeinander auf, ohne dass Sie das entschieden haben.

### 3.6 Was Sie vorab liefern müssen

Manches können die Agenten nicht selbst beschaffen: Zugangsschlüssel, Konten,
lizenzierte Schriften oder Bilder, eine Testumgebung für Browseroberflächen.
Stellen Sie so etwas bereit, **bevor** die Aufgabe es braucht. Fehlt es, hält
der Programmierer mit dem Grund `OPERATOR-PREREQUISITE-MISSING` an und nennt,
was fehlt.

---

## Teil 4 — Für Fortgeschrittene

### 4.1 Was im Projekt entsteht

| Pfad | Inhalt | versioniert |
|---|---|:---:|
| `inbox/*.md` | wartende Aufgaben; die älteste stabile Datei kommt zuerst | nein |
| `outbox/done/<UTC-Zeit>_<name>.md` | abgeschlossene Aufgaben | nein |
| `outbox/failed/*.poison` und `*.poison.error.json` | technisch gescheiterte Aufgaben mit Lauf-ID, letztem Schritt und Ursache | nein |
| `.orchestrator/artifacts/<run-id>/records/` | die Aufzeichnungskette – die einzige technische Wahrheit | nein |
| `.orchestrator/state.json` | abgeleiteter Zustandsspiegel, jederzeit neu erzeugbar | nein |
| `.orchestrator/logs/invocation-failures/<id>.json` | Klartext und Kennzahlen gescheiterter Provideraufrufe | nein |
| `docs/internal/<name>-arbeitsplan.md` und Slice-Berichte | Plan und Prüfberichte als Markdown | ja, auf dem Zielbranch |
| `feature/<name>` | ein Branch je Aufgabenkette, ein Commit je Arbeitspaket | ja, lokal |

Automatisch erzeugte Branchnamen tragen einen Digest der Aufgabe und werden
über `git config branch.<name>.orchestratorTaskDigest` an sie gebunden. Einen
fremden Branch gleichen Namens übernimmt der Orchestrator nie.

### 4.2 Konfiguration

Vorrang: Kommandozeile vor `RUN_TASK_*`-Umgebungsvariablen vor
`orchestrator.toml` vor eingebautem Standard. Modell, Effort, Timeout und
Claude-Budget stehen bewusst nicht in der Projektdatei.

| Abschnitt | Wirkung |
|---|---|
| `[paths]` | ordnet Pfade Produktivcode, Tests, Doku und Erzeugtem zu; unbekannte Pfade zählen als Produktivcode. Die Klassen steuern unter anderem, welche Scope-Erweiterungen automatisch genehmigt werden. |
| `[validation]` | `default_command` läuft nach jedem Paket; `[[validation.rules]]` ergänzen Befehle für bestimmte Pfadmuster. `required_artifacts` und `product_command` erlauben Akzeptanzkriterien gegen Bauergebnis beziehungsweise laufendes Produkt. |
| `[[stop_rules]]` | projektspezifische Stoppregeln, die Codex vor einer Verletzung anhalten lassen |
| `[workflow]` | zusätzliche menschliche Freigaben: `plan_gate`, `test_change_gate`, `manual_slice_gate` – standardmäßig aus |
| `[[provider_input_budget]]` | Obergrenzen für die Eingabegröße je Provider, Rolle und Operation |

Ohne `default_command` sucht der Orchestrator selbst: zuerst `pyproject.toml`
mit pytest, dann ein `test`-Skript in `package.json`, dann ein `test`-Ziel im
`Makefile`. Findet er nichts, gibt es keinen Testbefehl, und das erste
Arbeitspaket hält mit `validation request requires at least one command` an.

Alle Optionen und Umgebungsvariablen stehen im Abschnitt „Konfiguration" der [README](../../README.md),
eine vollständige Beispieldatei ist [`orchestrator.toml`](../../orchestrator.toml)
dieses Repositorys.

### 4.3 Modelle und CLI-Versionen

Codex läuft mit `gpt-6-sol` und Effort `medium`, Claude mit `opus` und Effort
`high`. Beides ist nicht frei wählbar: Jeder Provideraufruf vergleicht Modell und
Effort mit dem geprüften Fähigkeitsregister
[`schemas/native-provider-schema-capabilities-v1.json`](../../schemas/native-provider-schema-capabilities-v1.json).
Ein abweichender Wert über `--codex-model`, `--claude-model` oder `--*-effort`
hält beim ersten Aufruf an. Ein Modellwechsel verlangt eine neue Merkmalsprobe
und einen geänderten Registereintrag.

Dasselbe Register legt die geprüften CLI-Versionen fest. Neuere Versionen
derselben Hauptversion werden akzeptiert, ältere und andere Hauptversionen
nicht.

### 4.4 Formale Aufträge statt Ideen

Wer Pfade und Arbeitspakete selbst vorgeben will, schreibt einen formalen
Auftrag: [`example-plan-task.md`](../../example-plan-task.md) zeigt einen reinen
Planungsauftrag, [`example-task.md`](../../example-task.md) einen
Umsetzungsauftrag mit `TASK_SCOPE`, Akzeptanzkriterien und Stoppbedingungen.
Solche Aufträge lassen sich auch ohne Wache starten:

```bash
run_task --task-file task.md
```

Sobald eine Datei einen formalen Marker enthält, muss der ganze formale Vertrag
stimmen; halb formale Mischformen werden abgewiesen.

### 4.5 Mehrere Projekte

Je Projekt läuft höchstens eine Wache; eine zweite meldet
`Another watcher is already running on inbox`. Mehrere Projekte laufen
nebeneinander, jedes in seiner eigenen tmux-Sitzung und mit eigenem Protokoll.

### 4.6 Bekannte Grenzen

- **`master` ist fest verdrahtet.** Die Basis jedes Laufs ist der Merge-Base mit
  `master`.
- **Neue Aufgaben zweigen vom aktiven Branch ab**, nicht von `master`. Wer
  zwischen zwei unabhängigen Aufgaben nicht auf `master` zurückwechselt, erhält
  gestapelte Branches.
- **Eine Aufgabe nach der anderen.** Es gibt keine parallelen Arbeitspakete.
- **Ohne Testbefehl kein Lauf.** Ein Projekt ohne automatische Tests muss vorher
  mindestens einen bekommen.
- **Providerfehler kosten Zeit.** Etwa jeder vierte Aufruf der Agenten-CLIs
  scheitert an etwas Sachfremdem und wird automatisch wiederholt.

### 4.7 Fehlerbilder

| Meldung | Ursache | Abhilfe |
|---|---|---|
| `could not resolve merge-base (master: …)` | das Projekt hat keinen Branch `master` | 2.1 |
| `Needed a single revision` | das Repository hat noch keinen Commit | 3.4 |
| `automatic target-branch switch requires a clean non-ignored working tree` | eine unversionierte oder geänderte Datei liegt im Projekt | `git status --short` prüfen; Protokolle außerhalb des Projekts ablegen |
| `validation request requires at least one command` | kein Testbefehl konfiguriert oder erkannt | `default_command` in `orchestrator.toml` setzen |
| `Unsupported codex CLI version` oder `Unsupported claude CLI version` | CLI zu alt oder aus einer anderen Hauptversion | CLI aktualisieren |
| `transport differs from its probed schema capability` | Modell oder Effort weicht vom Register ab | Modell- und Effort-Optionen weglassen |
| `Another watcher is already running on inbox` | es läuft schon eine Wache für dieses Projekt | die laufende Sitzung verwenden (`tmux attach`) |
| `fatal: stash failed` beim Zusammenführen | Git ist auf automatisches Zwischenspeichern eingestellt | `git -c merge.autoStash=false merge --no-ff <branch>` |
| `Explicit --agents-file does not exist` | ein ausdrücklich angegebener Pfad fehlt | Pfad korrigieren; ohne Angabe gilt `AGENTS.md` im Projektordner |
| Exitcode `4` mit `bootstrap_check` | ein lokaler Vorabcheck ist gescheitert, keine Entscheidung nötig | Ursache im Protokoll beheben, dann erneut `run_task --watch` |
| Datei landet als `.poison` in `outbox/failed/` | wiederholter technischer Fehler | `.poison.error.json` daneben lesen |
