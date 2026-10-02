# Einrichtung: vom Download zum ersten Lauf

Diese Anleitung führt vom heruntergeladenen Repository bis zum ersten
vollständigen Lauf – für ein **vorhandenes Projekt** ebenso wie für einen
**Neuanfang**, bei dem es nur eine Projektbeschreibung gibt.

Teil 1 bis 3 führen Sie mit kopierbaren Befehlen durch die Einrichtung.
Optionale Sonderkonfigurationen stehen in Teil 4 als Referenz für alle, die wissen
wollen, was dabei im Einzelnen geschieht und wo die Grenzen liegen.

> [!TIP]
> **Der kürzeste Weg.** Teil 1 einmal erledigen. Danach entweder Teil 2
> (vorhandenes Projekt) oder Teil 3 (Neuanfang). Oder Sie überlassen das
> Abarbeiten einem Agenten – siehe den nächsten Abschnitt. Wer verstehen
> möchte, was der Orchestrator während eines Laufs tut, liest
> [Wie der Orchestrator arbeitet](ablauf-des-orchestrators.md).

## Die Einrichtung einem Agenten überlassen

Codex und Claude Code brauchen Sie ohnehin. Einer von beiden kann diese
Anleitung auch selbst abarbeiten; Sie treffen nur noch die Entscheidungen.

Für ein bereits angeschlossenes Projekt nach einem Orchestrator-Update
verwenden Sie den [Agentenauftrag in 2.11](#211-den-orchestrator-aktualisieren-und-ein-projekt-nachziehen).

**Was Sie selbst tun müssen**, weil es kein Agent für Sie kann:

1. Codex CLI und Claude Code installieren und **anmelden** (1.1).
2. Name und E-Mail-Adresse für Git nennen, wenn der Agent danach fragt.

**Dann** starten Sie Claude Code im Projektordner (`claude`) und geben diesen
Auftrag – für ein **vorhandenes Projekt**:

```text
Klone https://github.com/dieteresser62-lab/Dual-Agent-Orchestrator nach
~/werkzeuge/Dual-Agent-Orchestrator, falls es dort noch nicht liegt, und lies
dort docs/reference/einrichtung.md. Erledige Teil 1, soweit er fehlt, und
schließe dieses Projekt nach Teil 2 an. Frag mich, statt zu raten: beim
Testbefehl, bei den Pfadklassen, beim Inhalt der AGENTS.md, bei der Merge-Wahl
und möglichen post-merge-Hooks (2.10) sowie vor jeder Installation.
Starte keinen Lauf. Zeig mir am Ende jeden Schritt mit Ergebnis.
Prüfe vor dem Einrichtungscommit die globale und die im Projekt wirksame
Git-Identität nach 1.1; fehlt sie, frage nach Name, E-Mail und Geltungsbereich.
Führe die Vorabprüfung aus 2.3 ohne parallelen Lauf aus, bei Unterprozess-Tests
auch einen gezielten Test; ergänze bei belegter Sandbox-Grenze die optionale
Regel aus 2.4.
```

Für einen **Neuanfang** lautet der mittlere Satz stattdessen: *„Lege nach
Teil 3 in diesem Ordner ein neues Projekt aus der Beschreibung
/pfad/zu/beschreibung.md an.“* Dann fragt der Agent zusätzlich nach Sprache und
Werkzeugen und nach allem, was Sie vorab liefern müssen (3.6).

Claude Code eignet sich dafür besser als Codex: Es fragt vor jeder Installation
nach, während Codex in seiner Sandbox meist keinen Netzzugang hat.

**Fertig ist die Einrichtung**, wenn der Bericht des Agenten zeigt:

- `run_task --help` funktioniert und das aufgelöste Ziel stimmt mit 1.3 überein;
- die im Projekt wirksame Git-Identität ist vor dem Commit geprüft (1.1);
- das Projekt steht auf dem Hauptbranch, `git status --short` ist leer;
- `.gitignore`, `orchestrator.toml` und `AGENTS.md` sind committet;
- der Testbefehl – und jeder zusätzliche Prüfbefehl aus 2.3 – ist grün;
- `python3 ~/werkzeuge/Dual-Agent-Orchestrator/scripts/check_implementer_sandbox.py`
  wurde im Projekt ausgeführt; Werkzeugversionen und nötige
  `toolchain_read_roots` sind nach 2.3 geprüft und Warnungen geklärt;
- bei Unterprozess-Tests ist ein gezielter Test geprüft; bei belegter
  Sandbox-Grenze ist die optionale Regel aus 2.4 ergänzt;
- `AGENTS.md` ist rollenneutral; vorhandene Rollendateien sind als
  Handbetrieb-Dateien gekennzeichnet (2.4);
- Merge-Wahl und mögliche `post-merge`-Hooks sind mit Ihnen geklärt;
  `merge_completed_branch` hält die Wahl ausdrücklich fest (2.10);
- der Ordner `inbox/` existiert.

Die erste Idee (2.6) und den Start (2.7) übernehmen Sie selbst. Diese
Anleitung bleibt die Referenz: Jeder Schritt, den der Agent getan hat, steht
hier zum Nachlesen.

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

Der **Implementer** (standardmäßig Codex) plant und programmiert; **Reviewer**
und **Final-Reviewer** (standardmäßig Claude) prüfen. `[roles]` und
`[agent_profiles]` in `orchestrator.toml` bestimmen die Besetzung. Der Orchestrator führt die Tests aus und legt die Ergebnisse als lokale
Commits auf einem eigenen Branch ab. Nach befundfreier Abnahme führt er
standardmäßig einen lokalen Merge in den Basisbranch aus. Er pusht nie; den
Merge können Sie in der Konfiguration abschalten.

---

## Teil 1 — Einmalig: den Orchestrator installieren

### 1.1 Was Sie brauchen

| Was | Mindestens | Prüfen mit |
|---|---|---|
| Linux oder Windows mit WSL2 und lesbarem `/proc` | – | `uname -s` (Linux), `cat /proc/version` (unter WSL2 mit `microsoft-standard-WSL2`), `test -r /proc/self/status && echo /proc-lesbar` |
| Python | 3.11 | `python3 --version` |
| Git | – | `git --version` |
| eine Git-Identität | – | `git config user.name` und `git config user.email` |
| Codex CLI | **0.159.2 empfohlen** (Registerminimum 0.156.1; dessen Unterstützung der gehärteten Aufrufform ist ungeprüft) | `codex --version` |
| Claude Code | 2.1.283 | `claude --version` |
| ein ChatGPT-Konto mit Zugriff auf die **Modellfamilie Sol** (derzeit `gpt-6.1-sol`, Auflösung beim Laufstart) | – | `codex login` |
| ein Claude-Konto mit Zugriff auf **Opus** | – | `claude`, dann der Anmeldung folgen |

Das automatische Fortsetzen nach einem Absturz braucht Linux oder WSL mit lesbarem `/proc`; auf anderen Systemen führt der Weg über ein Freigabe-Gate.

Installieren Sie Codex über npm, Claude Code etwa ebenfalls über npm:

```bash
npm install -g @openai/codex
npm install -g @anthropic-ai/claude-code
```

Codex muss über `node_modules/@openai/codex/bin/codex.js` auflösbar sein,
mit genau einem ausführbaren Vendor-Binary im npm-Paket.
Das ist hier eine **unterstützte npm-Installation**, auch unter nvm. Ein
einzeln kopiertes Binary oder ein anderes Layout wird bei Vorabprüfung und
Laufstart abgewiesen; es gibt keinen Ersatzaufruf. Prüfen Sie den Einstieg
mit `readlink -f "$(command -v codex)"`.

> [!IMPORTANT]
> Beide Programme müssen **angemeldet** sein, bevor der Orchestrator startet.
> Er fragt nicht nach Zugangsdaten und legt keine an. Versionen unter den
> Registermindestversionen weist er beim ersten Aufruf ab.

Die gehärtete Codex-Implementer-Aufrufform (Stand Oktober 2026) wurde mit
0.159.2 gemessen, Claude Code mit 2.1.283. Führen Sie nach jedem Update
beider CLIs den Offline-Quicktest aus 2.3 aus, ebenso bei einer Erstinstallation
mit einer neueren Version als diesen Messständen. Fehlende Pflichtschalter werden abgewiesen. Die Projekt-Vorabprüfung
aus 2.3 ergänzt dies mit den Messgrenzen aus 4.3.

Der Orchestrator committet unter der Git-Identität des Projekts. Prüfen Sie
im Projektordner sowohl die globale als auch die dort wirksame Identität:

```bash
git config --global --get user.name
git config --global --get user.email
git config --get user.name
git config --get user.email
```

Der Orchestrator committet im gestarteten Projekt-Checkout; er erstellt dafür
keinen weiteren Klon oder Worktree. Eine lokale Identität genügt dort und wird
mit verknüpften Worktrees desselben Repositorys normalerweise geteilt. In einem
neuen Klon fehlt diese lokale Konfiguration: Prüfen Sie die wirksame Identität
in jedem verwendeten Projektordner. Fehlt sie, scheitert der erste Commit mit
`Author identity unknown` (`GIT-TRANSACTION`, fortsetzbar nach der Einrichtung).
Für alle Repositorys einmalig einrichten:

```bash
git config --global user.name "Ihr Name"
git config --global user.email "ihre@adresse.example"
```

Soll die Identität nur für ein Projekt gelten, setzen Sie sie stattdessen
**im jeweiligen Projektordner** ohne `--global`:

```bash
git config user.name "Ihr Name"
git config user.email "ihre@adresse.example"
```

### 1.2 Herunterladen

```bash
mkdir -p ~/werkzeuge
git clone https://github.com/dieteresser62-lab/Dual-Agent-Orchestrator.git \
  ~/werkzeuge/Dual-Agent-Orchestrator
```

> [!TIP]
> **Unter WSL2** gehören Orchestrator und Projekte ins Linux-Dateisystem, also
> unter `~/`, nicht unter `/mnt/c/…`. Dateizugriffe über die Windows-Grenze
> sind erheblich langsamer – gemessen rund 57-mal bei `git status` –, und der
> Orchestrator liest viel.
>
> Ausnahme: Ein Projekt, dessen Ergebnis unter Windows gebaut wird, etwa eine
> Tauri- oder andere `.exe`, kann unter `/mnt/c/…` bleiben. Der Orchestrator
> arbeitet dort genauso, nur langsamer. Sprechen Sie dort Dateinamen exakt in
> der Schreibweise an, die Git kennt: Das Windows-Dateisystem unterscheidet
> Groß- und Kleinschreibung nicht, Git schon.

### 1.3 Als Befehl verfügbar machen

Damit `run_task` in jedem Projektordner funktioniert:

```bash
mkdir -p ~/.local/bin
ln -s ~/werkzeuge/Dual-Agent-Orchestrator/run_task ~/.local/bin/run_task
```

Bei `File exists` prüfen Sie zuerst `ls -l ~/.local/bin/run_task`. Ist es
ein veralteter Symlink, ersetzen Sie nur diesen mit
`ln -sfn ~/werkzeuge/Dual-Agent-Orchestrator/run_task ~/.local/bin/run_task`.
Eine vorhandene reguläre Datei oder ein Verzeichnis erst bewusst umbenennen.

Prüfen Sie den Eintrag im Suchpfad:

```bash
command -v run_task
readlink -f "$(command -v run_task)"
run_task --help
```

Das aufgelöste Ziel muss `~/werkzeuge/Dual-Agent-Orchestrator/run_task`
sein (mit ausgeschriebenem Home-Pfad). Ein anderes Ziel zuerst korrigieren.
Meldet die Shell `command not found`, ergänzen Sie den Suchpfad einmalig und
öffnen ein neues Terminal:

```bash
echo 'export PATH="$HOME/.local/bin:$PATH"' >> ~/.bashrc
```

### 1.4 Installation prüfen

Ein Probelauf ohne Agenten und ohne Änderungen:

```bash
cd ~/werkzeuge/Dual-Agent-Orchestrator
readlink -f "$(command -v run_task)"   # dasselbe run_task wie hier im Checkout
./run_task --dry-run --task-file example-task.md --quiet
echo $?
```

Die Ausgabe `0` bedeutet: Installation in Ordnung.

---

## Teil 2 — Ein vorhandenes Projekt anschließen

### 2.1 Was Ihr Projekt mitbringen muss

- [ ] Es ist ein **Git-Repository mit mindestens einem Commit**.
- [ ] Der Arbeitsbaum ist **sauber** (`git status --short` zeigt nichts an).
- [ ] Es gibt einen **Testbefehl, der jetzt grün ist**.

In den Projektordner wechseln und auf den Hauptbranch gehen – in den
Beispielen heißt er `main`, er darf aber beliebig heißen:

```bash
cd ~/projekte/mein-projekt
git switch main
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

**Der Testbefehl** läuft nach jedem Arbeitspaket **außerhalb der Agenten-Sandbox**
in der Startumgebung des Orchestrators. Er muss grün sein, belegt aber keine
passende Toolchain für gezielte Prüfungen des Implementers in der Sandbox.

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
scope_extension_gate = false
merge_completed_branch = true  # mit Ihnen wählen: false für manuellen Merge (2.10)
```

<details>
<summary><b>Vorlage für ein Node.js-Projekt</b></summary>

```toml
[paths]
productive = ["src/**", "package.json", "package-lock.json", "tsconfig.json"]
tests = ["test/**", "tests/**", "src/**/*.test.*"]
documentation = ["docs/**", "*.md"]
generated = [".orchestrator/**", "node_modules/**", "dist/**", "coverage/**"]

[validation]
default_command = ["npm", "test"]
default_timeout_seconds = 1200

[workflow]
plan_gate = false
test_change_gate = false
manual_slice_gate = false
scope_extension_gate = false
merge_completed_branch = true  # mit Ihnen wählen: false für manuellen Merge (2.10)
```

</details>

Passen Sie die Pfade an Ihr Projekt an, etwa `test/` statt `tests/`. Bei
Überlappung gilt: **generated vor tests vor documentation vor productive**;
`src/**/*.test.*` zählt also als Test. Nicht erfasste Pfade sind Produktivcode.
Fehlklassifikation kann den Schutz bestehender Tests bei Umfangserweiterungen
verfehlen (4.2); zusätzliche Prüfungen richten sich nach ihren eigenen Mustern.
`AGENTS.md` zählt hier als Dokumentation, `orchestrator.toml` als Produktivcode.
Beide sind für den Implementer technisch beschreibbar, aber Änderungen brauchen
einen passenden Auftrag und Pfadumfang. Empfehlung: beide in `AGENTS.md` unter
„Nicht anfassen“ nur mit ausdrücklichem Auftrag zur Änderung erlauben; der
Reviewer bleibt schreibgeschützt. Die TOML wird beim Start geladen, eine Änderung
daran stellt nicht einfach den Testbefehl des laufenden Laufs um.

Prüfbefehle sind Argumentlisten. Liegt die Anwendung in einem Unterordner oder
braucht der Befehl Shell-Semantik, geben Sie die Shell ausdrücklich an:

```toml
[validation]
default_command = ["sh", "-c", "cd app && flutter test"]
```

Dasselbe gilt für `product_command` und das `command` einer Regel. Die alten
Shell-Schlüssel werden schon beim Start und beim Trockenlauf abgewiesen.

**Zusätzliche Prüfungen:** Fehlen Typprüfung oder Build im Testbefehl, ergänzen
Sie Regeln. Sie laufen, wenn ein Arbeitspaket einen passenden Pfad ändert:

```toml
[validation]
default_command = ["npm", "test"]
default_timeout_seconds = 1200

[[validation.rules]]
patterns = ["src/**", "package.json", "tsconfig.json"]
command = ["npm", "run", "typecheck"]
timeout_seconds = 600

[[validation.rules]]
patterns = ["src/**", "index.html", "package.json", "vite.config.ts"]
command = ["npm", "run", "build"]
timeout_seconds = 600
```

Alternativ ein Sammelskript wie `"check": "tsc --noEmit && vitest run && vite build"`
in `package.json`, dann `default_command = ["npm", "run", "check"]`. Für
Prüfungen am Bauergebnis oder laufenden Produkt: `required_artifacts` und
`product_command` (4.2).

**Jetzt den Testbefehl einmal von Hand ausführen.** Er muss auf dem Hauptbranch
grün sein:

```bash
python3 -m pytest tests/ -q      # oder: npm test
```

> [!CAUTION]
> Ein Testbefehl, der schon vor dem ersten Arbeitspaket rot ist, macht jedes
> Arbeitspaket rot. Reparieren Sie ihn vorher.

Die Vorlagen behalten Codex / Claude / Claude bei. Separate Abschlussprofile,
andere Besetzungen, Antigravity und Zeitgrenzen sind **für die meisten Projekte
nicht nötig**; siehe [Zusatz in 4.3](#zusatz-sonderkonfigurationen-für-die-meisten-projekte-nicht-nötig).

**Entscheidungsregel:** Maßgeblich ist die Werkzeugauflösung der Vorabprüfung
weiter unten, nicht allein der Host-Pfad. „Passend“ bedeutet: Die Version
erfüllt die Projektvorgaben (etwa `engines`, `.nvmrc` oder Python-Vorgaben)
und entspricht der geprüften Host-Version. Findet die Sandbox dieselbe Version
unter `/usr` oder im Repository, ist **keine zusätzliche Wurzel nötig**, auch
wenn auf dem Host eine Home-Installation zuerst gefunden wird. Bei Abweichung
oder fehlendem Werkzeug geben Sie die benötigte konkrete Installation
schreibgeschützt frei und prüfen erneut. Wiederholen Sie die Vorabprüfung nach
Updates der Home-Toolchain. Im Standardprofil ergänzen Sie:

```toml
[agent_profiles.implementation.provider_options.codex]
toolchain_read_roots = ["/absolute/node-root"]
```

Ersetzen Sie den Platzhalter durch den absoluten Pfad Ihrer Installation, etwa
der aktivierten nvm-Version; TOML expandiert `~` nicht. Für einen
Claude-Implementer heißt die Tabelle `provider_options.claude` (Beispiel in 4.3);
dort liefert die Vorabprüfung nur eine Auflösungsnäherung.

- **Codex** behält den Eltern-PATH. Persönliche Home-Verzeichnisse sind
  aber nicht pauschal sichtbar. Ist etwa die nvm-Node unsichtbar, kann die
  Suche auf `/usr/bin/node` zurückfallen. Starten Sie die Wache in einer
  Shell, in der `command -v node` auf die freigegebene Installation zeigt
  und deren `bin` im PATH vor `/usr/bin` steht; bei nvm aktivieren Sie vorher
  die benötigte Version. Codex ergänzt den PATH nicht um freigegebene
  Wurzeln. Andernfalls verwenden Sie absolute Werkzeugpfade. Nutzen Sie
  auch beim Resume dieselbe Werkzeugumgebung (Details zur Bindung in 4.3).
- **Claude-Implementer** nutzt einen festen PATH
  `/usr/local/bin:/usr/bin:/bin`; vorhandene `<wurzel>/bin` werden vorangestellt.
  Ihr persönlicher PATH allein genügt hier nicht.
- **Verdeckter Versionsfehler:** Gibt es dasselbe Werkzeug unter `/usr` in
  einer anderen Version, läuft still diese Version. Fehlt eine sichtbare
  Installation, scheitert der Befehl. Prüfen Sie deshalb Pfad und Version.
- Sind `node_modules`, `.venv` oder ähnliche Projektordner Symlinks nach
  außerhalb, brauchen Sie auch deren aufgelöstes Ziel als Wurzel.

Wurzel einer Installation mit `bin/node` (andere Layouts entsprechend anpassen):

```bash
command -v node
node --version
readlink -f "$(command -v node)"
dirname "$(dirname "$(readlink -f "$(command -v node)")")"
readlink -f node_modules   # falls dieser Ordner ein Symlink ist
readlink -f .venv          # falls diese Umgebung ein Symlink ist
```

Übernehmen Sie nur die benötigten Verzeichnisse; HOME und
Anmeldedaten-Verzeichnisse dürfen nicht freigegeben werden (Grenzen in 4.3).
Die Claude-Bash-Sandbox benötigt **socat und bubblewrap**; `failIfUnavailable`
verhindert den ungeschützten Start bei fehlenden Voraussetzungen.
Kein automatischer Anbieterwechsel. Schutz- und Messregeln:
[Implementer-Zertifizierung](implementer-certification.md) und
[Reviewer-Zertifizierung](reviewer-certification.md).

#### Vorab prüfen

Starten Sie im **Projektordner**, in derselben Shell wie später die Wache:

**Nie parallel zu einem Orchestrator in diesem Projekt prüfen.** Suchen Sie
Prozesse und prüfen Sie deren Arbeitsverzeichnis:

```bash
pgrep -af '[r]un_task|[o]rchestrator|[c]li.py'
readlink -f /proc/<PID>/cwd   # <PID> durch die gefundene Prozessnummer ersetzen
```

Die Liste kann fremde Projekte enthalten; vergleichen Sie mit `pwd -P`.
Bei Zweifel über Kindprozesse die zugehörige Wache beenden und deren
Prozessende abwarten. Ein vorhandenes `.orchestrator/` allein bedeutet keinen
laufenden Prozess. `codex sandbox` kann kurzzeitig
Platzhalter für fehlende Schutzpfade im Projekt anlegen; ein paralleler Lauf
würde diese Änderungen seinem Implementer zuschreiben.

```bash
python3 ~/werkzeuge/Dual-Agent-Orchestrator/scripts/check_implementer_sandbox.py
python3 ~/werkzeuge/Dual-Agent-Orchestrator/scripts/check_implementer_sandbox.py --tool node --tool npm
```

Das Werkzeug lädt Ihre `orchestrator.toml` samt mitgelieferten Profilen und
prüft ausschließlich den gewählten Implementer. Ohne `--tool` nimmt es das
erste Argument des Testbefehls und aller Regelbefehle; bei `npm` kommt `node`
dazu. Für zusammengesetzte Shellbefehle nennen Sie die benötigten Werkzeuge
ausdrücklich mit `--tool`. Es braucht keinen Modellaufruf, keine Anmeldung
und kein Netz. Codex braucht die npm-Installation aus 1.1. Die Prüfung zeigt
Pfad und `--version` auf Host und Sandbox. Beispielsweise (HOME hier als `~` verkürzt):

```text
node:
  Host: ~/.nvm/versions/node/v24.0.0/bin/node | v24.0.0
  Sandbox: /usr/bin/node | v22.23.2
WARNUNG: node: Version weicht vom Host ab.
Exit: 1
```

Hier würde ein gezielter Test still die System-Node verwenden. Geben Sie die
konkrete nvm-Installation als `toolchain_read_roots` frei, aktivieren Sie sie
im Eltern-PATH und wiederholen Sie die Prüfung. Bei gleicher Version und
anderem Pfad entsteht dagegen nur ein Hinweis (gemessen am 02.10.2026 mit
codex-cli 0.159.2 und Node v22.23.2):

```text
node:
  Host: ~/.nvm/versions/node/v22.23.2/bin/node | v22.23.2
  Sandbox: /usr/bin/node | v22.23.2
HINWEIS: node: andere Installation, gleiche Version.
Exit: 0
```

Der Hinweis zeigt eine andere Installation an und ändert den Exitcode nicht.
Eine abweichende Version, ein fehlendes Werkzeug oder ein gescheitertes
`--version` erzeugt eine Warnung und Exit `1`. Kontrollieren Sie zusätzlich
Repository-Schreibrechte, `.git`-Schreibschutz, sichtbare Home-Einträge und
aufgelöste externe Symlink-Ziele von `node_modules` und `.venv`. Die Suche
überspringt Verzeichnisse, die Ihre `[paths] generated`-Muster als erzeugt
kennzeichnen; die beiden Symlinks selbst werden noch am Elternordner erkannt.

**Sollbild bei Codex:** Repository beschreibbar, `.git` schreibgeschützt.
Unter „Sichtbare Home-Einträge“ erscheinen nur Pfadanfänge zum Codex-Paket,
zu freigegebenen Wurzeln und zum Repository selbst. Liegt Ihr Projekt unter
`~/projekte/mein-projekt`, darf deshalb `projekte` sichtbar sein; andere
Projekte darunter bleiben unsichtbar, sofern sie nicht ausdrücklich freigegeben sind. Das Rechteprofil gibt nur diese Wurzeln
und minimale Systempfade frei (4.3). Prüfen Sie bei Zweifel die nächste
Pfadstufe mit einem Zusatzbefehl, etwa:

```bash
python3 ~/werkzeuge/Dual-Agent-Orchestrator/scripts/check_implementer_sandbox.py --tool node -- sh -c 'ls -A ~/projekte; test ! -e ~/projekte/anderes-projekt'
```

Ersetzen Sie die Pfade durch Ihren Projekt-Elternordner und einen auf dem Host
nachweislich vorhandenen Geschwisterordner. Sichtbar sein darf nur der Weg zu
den erlaubten Wurzeln, nicht der Geschwisterordner.

Ein gezielter Befehl ist optional:

```bash
python3 ~/werkzeuge/Dual-Agent-Orchestrator/scripts/check_implementer_sandbox.py --tool node -- node --version
python3 ~/werkzeuge/Dual-Agent-Orchestrator/scripts/check_implementer_sandbox.py --json
```

Bei Tests mit Unterprozess-, Port- oder Browserstart prüfen Sie zusätzlich
einen solchen Einzeltest. Ersetzen Sie `<einzeltest-befehl>` durch den
passenden Aufruf Ihres Projekts; Beispiele:

| Projekt | Einzeltest-Befehl | Fehlerdetails sichtbar machen |
|---|---|---|
| `node --test` | `node --test test/cli.test.js` | bei bloßem „test failed“ direkt `node test/cli.test.js` |
| pytest | `python3 -m pytest tests/test_cli.py::test_start -vv -s --tb=long` | Traceback lesen; ggf. projektspezifischen Logfilter abschalten |
| eigenes Einzeltest-Skript | `python3 test/check_cli.py` | Detail-/Debugmodus des Skripts aktivieren oder den betroffenen Start direkt ausführen |

```bash
python3 ~/werkzeuge/Dual-Agent-Orchestrator/scripts/check_implementer_sandbox.py --tool node -- <einzeltest-befehl>
# Node-Beispiel, wenn der Runner Fehlerdetails verdeckt:
python3 ~/werkzeuge/Dual-Agent-Orchestrator/scripts/check_implementer_sandbox.py --tool node -- node test/cli.test.js
node test/cli.test.js   # derselbe Detailaufruf auf dem Host
```

Für Python ersetzen Sie `--tool node` durch `--tool python3`.
**So unterscheiden Sie eine Sandbox-Grenze von einem Testfehler:**

1. Zeigt der Runner nur „test failed“, starten Sie den Einzeltest mit sichtbaren
   Fehlerdetails, bei Node gegebenenfalls ohne `--test` wie oben.
2. Suchen Sie in stdout und stderr nach `EPERM` oder „Operation not permitted“.
   Prüfen Sie im Traceback, ob der Fehler tatsächlich beim Unterprozess-, Port-
   oder Browserstart entsteht. Der Werkzeughinweis „mögliche Sandbox-Grenze,
   siehe 2.3“ und das JSON-Feld `command.sandbox_boundary_hint` sind nur Indizien.
3. Führen Sie denselben Detailaufruf außerhalb der Sandbox in derselben
   Projektumgebung aus. Erst wenn er dort grün ist und in der Sandbox allein
   der Start an fehlenden Rechten scheitert, ist die Grenze belegt. Weitere
   Assertions, falsche Ergebnisse oder ein roter Host-Test sind Testfehler.

Bei belegter Grenze ergänzen Sie die **optionale Regel aus 2.4**. Der Implementer
berichtet die Einschränkung und übergibt seine Arbeit; der Orchestrator prüft
den vollständigen Testbefehl außerhalb der Sandbox. Ein `EPERM` allein beweist
die Grenze nicht und löst keine automatische Übergabe aus.

**Fester Hinweis:** Zusatzbefehle dürfen im Repository schreiben; wählen Sie
gezielte, nicht verändernde Befehle. Der Hinweis erscheint auch bei Exit `0`
und steht im JSON unter `command.notice`. Fehler ergeben eine Warnung.
Exit `0` bedeutet passende Ergebnisse, `1` Warnungen, `2` Konfigurations- oder
Werkzeugfehler. `--json` enthält stdout, stderr, Befehls-Exitcode, vollständigen
PATH sowie `notes` und `warnings`; Text zeigt nur die PATH-Anzahl.
`--timeout 120` setzt das Limit je Prüfung/Zusatzbefehl auf 120 Sekunden
(Standard: 60). Bei Timeout nennt Text nur Limit und Prüfung, JSON zusätzlich
die vollständige Kommandozeile samt Rechteprofil unter `error_details`.
Scratch wird auch nach Fehlern entfernt, sobald die Prozessgruppe sicher beendet
ist (4.3). Zugangsdaten werden nicht gelesen.

**Messgrenze:** Codex führt `codex sandbox` mit dem produktiv erzeugten
Rechteprofil aus; das ist eine Näherung für die Werkzeugbefehle von `exec`.
Bei Claude werden nur die Werkzeugauflösung mit dem festen PATH und die
konfigurierten Lesefreigaben gezeigt. Schreibrechte und Home-Sichtbarkeit
bleiben dort ungemessen; ein Zusatzbefehl wird abgewiesen. Die Ausgabe nennt
diese Näherung ausdrücklich. Details stehen in 4.3.

Nach jedem Update von Codex oder Claude Code, auch bei einer Erstinstallation
oberhalb der Messstände aus 1.1, führen Sie den konto- und kontingentfreien
Offline-Quicktest aus dem Orchestrator-Checkout aus. Er startet echte CLIs
gegen lokale Fake-Server mit Attrappen und prüft die produktiv erzeugten
Schutzbefehle; es gibt keinen Modellaufruf:

```bash
python3 ~/werkzeuge/Dual-Agent-Orchestrator/scripts/probe_reviewer.py boundary-check --pair all --out /tmp/boundary-update
```

Für zusätzliche Werkzeugwurzeln lässt sich `--toolchain-root
/absolute/node-root` ergänzen. Derselbe Einstieg ist
`python3 ~/werkzeuge/Dual-Agent-Orchestrator/scripts/qualification/offline_boundary.py --pair all`. Der Test
ersetzt keine Phase-0-Live-Messung oder Zertifizierung; die Versionspolitik
akzeptiert neue CLI-Versionen ohne Neuzertifizierung je Update, der
Laufzeitschutz bleibt verpflichtend.

### 2.4 Den Agenten das Projekt erklären: `AGENTS.md`

Die ersten **12.000 Zeichen** aus `AGENTS.md` (oder `--agents-file`) ergänzen den
Implementer-Auftrag und liegen den Prüfern als Evidenz vor. Codex/Implementer
lädt die Datei zusätzlich nativ; beim Codex-Prüfer verhindert
`project_doc_max_bytes=0` dieses Laden. Die Datei ist optional. Schreiben Sie
sie **rollenneutral**:

```markdown
# Mein Projekt

## Worum es geht
Eine Anwendung für … Zielgruppe: … Das Wichtigste für Nutzer: …

## Technik
Sprache, Frameworks, Build: …
Tests: `python3 -m pytest tests/ -q` – jede Änderung braucht Tests.

## Regeln
- Diese Projektregeln gelten unabhängig von Anbieter und Rollenbesetzung.
- Planung und Umsetzung gehören zum Implementer, Prüfung zum Reviewer;
  Validierung, lokale Commits und der konfigurierte lokale Merge zum Orchestrator.
- Agenten führen keine Git-Schreibbefehle aus.
- Keine neuen Abhängigkeiten ohne Grund.
- Keine Netzwerkaufrufe zur Laufzeit.
- Bestehende Dateiformate bleiben lesbar.

## Nicht anfassen
- `legacy/` – wird separat abgelöst.
- `orchestrator.toml` und `AGENTS.md` – nur mit ausdrücklichem Änderungsauftrag.
```

**Optional bei belegten Sandbox-Grenzen** (Vorabprüfung aus 2.3): Ergänzen Sie
unter „Regeln“ diese Zeile, etwa für Projekte mit Unterprozess-Tests:

```markdown
- Ein nur in der Agenten-Sandbox gescheiterter Port-, Browser- oder Unterprozessstart (etwa `spawnSync … EPERM` bei Tests mit `node` als Kindprozess) ist kein Grund zum Anhalten: Diese Prüfungen im Bericht als in der Sandbox nicht ausführbar nennen und die fertige Arbeit übergeben; der Orchestrator führt den konfigurierten Testbefehl außerhalb der Sandbox aus.
```

Über diesen Weg wird nur `AGENTS.md` mitgegeben. Vorhandene `CLAUDE.md`,
`CODEX.md` und `GEMINI.md` werden im Lauf nicht automatisch als
Rollenanweisungen geladen; sie gelten nur für den **Handbetrieb**.
Setzen Sie an ihren Dateianfang diesen Hinweis:

```markdown
> Nur Handbetrieb: Diese Datei gilt für direkt gestartete Agenten. Im orchestrierten Lauf gelten AGENTS.md und der jeweilige Rollenvertrag.
```

In einem vollständigen Prüfsnapshot können sie als Dateien zur Einsicht liegen;
das macht sie nicht zum Prüfvertrag. Gemeinsames Projektwissen gehört in
`AGENTS.md`.

Übernehmen Sie keine Handbetrieb-Prüferregeln, die dem orchestrierten
Prüfvertrag widersprechen. **„Kein Review ohne Findings“** würde befundfreie
Abnahmen und damit den Abschluss verhindern. Legen Sie auch keine festen
Anbieterrollen fest: Die Besetzung kommt aus `orchestrator.toml`, und die
Agenten dürfen selbst weder committen noch mergen oder pushen.

### 2.5 Einrichtung festschreiben

Vor dem Commit müssen die wirksame Git-Identität (1.1), die Vorabprüfung
inklusive Unterprozess-Test und gegebenenfalls optionaler Regel (2.3/2.4) sowie
Ihre Merge-Wahl und Hooks (2.10) geklärt sein. Prüfen Sie nochmals:

```bash
git config --get user.name
git config --get user.email
git add .gitignore orchestrator.toml AGENTS.md
git commit -m "chore: prepare the repository for the orchestrator"
mkdir -p inbox
git status --short   # muss nach dem Einrichtungscommit leer sein
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
# Benötigte Umgebung hier aktivieren, dann Vorabprüfung aus 2.3 wiederholen:
python3 ~/werkzeuge/Dual-Agent-Orchestrator/scripts/check_implementer_sandbox.py
run_task --watch --verbose 2>&1 | tee -a ~/orchestrator-logs/mein-projekt.log
```

Mit <kbd>Strg</kbd>+<kbd>B</kbd>, dann <kbd>D</kbd> lösen Sie sich von der
Sitzung; sie läuft weiter. Zurück kommen Sie mit `tmux attach -t orchestrator`.

Braucht Ihr Testbefehl eine virtuelle Umgebung oder bestimmte
Umgebungsvariablen, richten Sie sie **vor** dem Start im selben Terminal ein.
Der Orchestrator führt die Tests mit genau dieser Umgebung aus. Wiederholen
Sie deshalb die Vorabprüfung in dieser tmux-Shell, auch wenn ein Agent sie
bereits ausgeführt hat; Warnungen vor dem Start klären. Bei zusammengesetzten
Shellbefehlen wieder die nötigen `--tool`-Argumente aus 2.3 verwenden.

> [!TIP]
> **Wie gründlich gedacht wird, entscheiden Sie beim Start.** Standard ist für
> beide Agenten Effort `high`. Für eine knifflige Aufgabe:
>
> ```bash
> run_task --watch --implementer-effort xhigh --reviewer-effort max --verbose
> ```
>
> Für eine einfache Aufgabe geht es mit `medium` oder `low` schneller und
> günstiger. Die Einstellung gilt für jede Aufgabe, die diese Wache neu
> beginnt; mehr dazu in 4.3.

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
| Änderungen im Arbeitsbaum | `GIT_OPTIONAL_LOCKS=0 git status --short` |
| Plan und Prüfberichte | `docs/internal/` im Projekt, auf dem Zielbranch |

**Während eines Laufs keine Git-Schreibbefehle im Projekt ausführen.** Auch
ein gewöhnliches `git status` kann den Index auffrischen. Der Schutzwächter
meldet das als `implementer changed protected trees: …/.git/index` und hält
den Lauf mit `AGENT-PERMISSION` an. Verwenden Sie beim Zusehen stets
`GIT_OPTIONAL_LOCKS=0 git status`, um diese Index-Aktualisierung zu vermeiden.
Bei einem bereits ausgelösten Halt folgen Sie 2.9.

Der Orchestrator arbeitet ohne Rückfragen durch: Plan, jedes Arbeitspaket mit
Test und Prüfung, am Ende eine Abnahme des gesamten Branches. Findet die
Abnahme noch etwas, legt er selbst eine Folgeaufgabe in den Eingang
(`…_followup01.md`) und arbeitet sie ab. Fertig ist er, wenn der Eingang leer
ist.

### 2.9 Wenn er anhält

Das Protokoll nennt am Ende einen Exitcode und einen Grund. Eindeutige Provider-Überlastung (etwa „model is at capacity“ oder HTTP 503/529) wird ohne Modellwechsel über `max_transport_failures` wiederholt und wartet je Versuch `maximum_delay_seconds` (Standard 30 s); nach Ausschöpfen des Budgets hält der Lauf resumefähig mit „provider overloaded“ an.

| Exitcode | Bedeutung | Was Sie tun |
|---:|---|---|
| `0` | fertig | weiter mit 2.10 |
| `2`, `3` | Kontingent erschöpft oder ein Agent ist ausgefallen | später erneut `run_task --watch` starten; bei Teilergebnissen kann die unten beschriebene Gate-Freigabe folgen |
| `4` | ein Gate hält den Lauf an | `gate=` und den Anfang von `detail=` in der Pausenzeile lesen; dann wie unten fortfahren |
| `5` | endgültiges Urteil, etwa eine abgelehnte Prüfung | Grund im Protokoll lesen; dieser Lauf ist beendet |

Die Pausenzeile der Wache zeigt `gate=<Grund> detail=<Text>`; bei Stoppgründen
steht der Grund am Anfang von `detail=`, oft vor ` | `. Die Zeile hat kein
eigenes Feld für den Gate-Fingerprint. Diese Werte verlangen eine begründete
Entscheidung über den im Zustand gebundenen Fingerprint:

| `gate=` | Anfang von `detail=` |
|---|---|
| `test_change` | `test changes require explicit approval before review` |
| `anchor_change` | `approved plan anchors changed and require plan review reset` |
| `manual_slice` | `manual slice approval is required before commit` |
| `plan_approval` | `PLAN-APPROVAL` |
| `unexpected_file` | `UNEXPECTED-PATH` oder `HEAD-DRIFT` |
| `quota_resume_diff` | `QUOTA-RESUME-DIFF` |
| `stop_request` | `SCOPE-EXTENSION-REQUESTED` bei einem laufenden Slice mit angeforderten Pfaden |

Für diese Fälle **außer `quota_resume_diff`** prüfen Sie Grund und Erläuterung
und erteilen oder verweigern Sie die Entscheidung:

```bash
run_task --watch --resume --approve-gate --gate-rationale "Pfade geprüft, passt"
# Oder ablehnen:
run_task --watch --resume --reject-gate --gate-rationale "Pfade nicht freigegeben"
```

Nach Teilergebnissen eines abgebrochenen Provideraufrufs kann
`QUOTA-RESUME-DIFF` eine ausdrückliche Entscheidung über Fingerprint und Pfade
verlangen.
Ein gewöhnliches Resume gibt diese Änderungen nicht frei. Lesen Sie im
Protokoll den aktuellen Fingerprint (`got …`) und `paths=…` und prüfen Sie
die Teilergebnisse mit `git diff` und `GIT_OPTIONAL_LOCKS=0 git status --short`.
Bei `gate=quota_resume_diff` beenden Sie die Wache und setzen die **aktuelle,
unveränderte Aufgabendatei** im Einzelmodus fort. Ermitteln Sie ihren Pfad
lesend aus dem Zustand, statt den Namen zu raten:

```bash
aufgabe=$(python3 -c 'import json; print(json.load(open(".orchestrator/state.json"))["task_file"])')
printf '%s\n' "$aufgabe"
run_task --task-file "$aufgabe" --resume --approve-gate --gate-rationale "Fingerprint, Pfade und Teilergebnisse geprüft"
```

Aus `inbox/meine-idee.md` entsteht nach der Planabnahme
`inbox/meine-idee-implement.md`; bei `meine-idee-plan.md` wird `-plan` durch
`-implement` ersetzt. Während der Umsetzung brauchen Sie diese erzeugte Datei,
nicht die ursprüngliche Idee. Im Watch-Modus wird `--task-file` ignoriert.
Zum Ablehnen verwenden Sie im Einzelbefehl `--reject-gate` statt `--approve-gate`
mit einer passenden Begründung. Entfernen Sie keine Teilergebnisse, um die
Freigabe zu umgehen, und ändern Sie keinen Zustand von Hand.

Der Einzelbefehl setzt den gebundenen Lauf fort und kann ihn bis zum Ende
ausführen. Eine erfolgreich abgeschlossene Aufgabe aus der Wache wird dabei
nach `outbox/done/` verschoben. Danach starten Sie für weitere Ideen oder
erzeugte Folgeaufgaben wieder die Wache; verwenden Sie dieselbe Startumgebung
und dieselben Optionen wie in 2.7:

```bash
run_task --watch --verbose 2>&1 | tee -a ~/orchestrator-logs/mein-projekt.log
```

Bei `gate=stop_request detail=OPERATOR-PREREQUISITE-MISSING | …` stellen Sie
die genannte Voraussetzung bereit und setzen mit `run_task --watch --resume`
fort. Das gilt auch für andere `stop_request`-Gründe wie `CONTRACT-UNCLEAR`,
`BRANCH-MISMATCH`, `VALIDATION-UNAVAILABLE` oder `PLAN-CONTRACT-INVALID` sowie
für `gate=unexpected_file detail=SLICE-HEAD-DRIFT | …`: Ursache beheben, dann
`run_task --watch --resume`. Ein `SCOPE-EXTENSION-REQUESTED` während der
Planung ist ebenfalls ein `stop_request` ohne Fingerprint. Bei einem
irrtümlichen Freigabeversuch bleibt das Gate bestehen; die Fehlermeldung nennt
`gate reason` und `stop reason` und empfiehlt `run_task --watch --resume` ohne
`--approve-gate`.

`gate=bootstrap_check` bei `status=awaiting_resume` nennt in `detail=` den
Prüfgrund, etwa `FINAL-REVIEW-PREFLIGHT | …`; nach dessen Behebung geht es mit
`run_task --watch --resume` weiter. `gate=quota` und `gate=instance_failure`
zeigen in `detail=` zuerst `role=… step=…`; nach dem Warten beziehungsweise
Beheben des Fehlers genügt derselbe Befehl. Bei diesen Gates ist keine Freigabe
nötig, auch wenn intern ein Fingerprint gespeichert ist.

Nach Exitcode `3` wegen dreier Abbrüche eines Agenten ist das
Wiederholungsbudget des Arbeitspakets aufgebraucht. Jedes weitere Fortsetzen
bringt dann genau einen neuen Versuch.

> [!IMPORTANT]
> Niemals Dateien unter `.orchestrator/` von Hand ändern oder löschen, während
> eine Aufgabe unterwegs ist. Daran hängt die Fähigkeit, nach jedem Abbruch
> genau dort weiterzumachen.

Den Orchestrator beenden Sie in seiner tmux-Sitzung mit <kbd>Strg</kbd>+<kbd>C</kbd>.
Ein späterer Start mit `run_task --watch` setzt fort.

### 2.10 Abschließen

**Vor dem ersten Lauf wählen:** Soll der Orchestrator nach befundfreier Abnahme
lokal mergen (`true`) oder möchten Sie selbst mergen (`false`)? Halten Sie die
Antwort ausdrücklich als `merge_completed_branch` unter `[workflow]` in Ihrer
TOML fest (Vorlagen in 2.3). Prüfen Sie vorher die Hooks im Projekt:

```bash
git config --get core.hooksPath   # keine Ausgabe: Git-Hook-Verzeichnis gilt
git rev-parse --git-path hooks    # auch für verknüpfte Worktrees
ls -la "$(git rev-parse --git-path hooks)"
# Bei gesetztem core.hooksPath stattdessen dessen Verzeichnis prüfen:
ls -la "$(git config --get core.hooksPath)"
```

Die letzte Zeile nur bei gesetztem Wert ausführen; relative Pfade gelten ab
der Projektwurzel. Lesen Sie besonders `post-merge` (nicht `*.sample`) und
klären Sie dessen Nebenwirkungen und Laufzeit vor Ihrer Wahl.

**Vor einem Push, besonders bei öffentlichen Repositorys:** Die Audit-Berichte
können derzeit absolute Home-Pfade der CLI-Binaries enthalten (gemessen am
02.10.2026 mit codex-cli 0.159.2) und mitcommittet werden. Der lokale Merge übernimmt sie in
den Basisbranch. Prüfen Sie die Berichte und die zur Veröffentlichung
vorgesehene Historie auf persönliche Pfade, auch in eingebetteten Testausgaben.
Eine erste Suche:

```bash
git log -p -- docs/internal/ | grep -nE '/home/|/mnt/c/Users/|/Users/'
```

Bis die automatische Ausgabe bereinigt ist, veröffentlichen Sie keine Commits
mit solchen persönlichen Pfaden. Ein späterer Bereinigungscommit entfernt
sie nicht aus älteren Commits; eine nötige Historienänderung ist eine separate,
bewusste Entscheidung. Prüfen Sie auch andere Ablageorte Ihrer Berichte.

Der Orchestrator hat alles auf einem eigenen Branch committet, zum Beispiel
`feature/einkaufsliste`. Nach einem befundfreien Gesamtreview archiviert er
neu angelegte Dateien direkt unter `docs/internal/` und committet die
Umbenennungen separat. Für neue Läufe ist das Ziel
`docs/internal/archive/{run_id}/`. Über `[workflow] archive_run_directory`
können Sie einen anderen Unterordner unter `docs/internal/archive/` vorgeben,
etwa `{year}-feature/{run_id}`. Der vollständige `{run_id}` ist dabei ein
eigenes Pfadsegment; `{year}` stammt aus der Laufkennung und `{branch_slug}`
aus dem Zielbranchnamen. Das Muster wird beim Laufstart gebunden. Ein bereits
vorhandener Zielordner oder ein ungültiger Pfad stoppt den Lauf vor dem ersten
Umsetzungsslice. Alte Läufe ohne diesen Schlüssel behalten beim Fortsetzen das
flache Archivziel `docs/internal/archive/`.

Standardmäßig folgt ein lokaler `--no-ff`-Merge in den Basisbranch, der danach
ausgecheckt ist. Der Zielbranch bleibt erhalten; es gibt keinen Push. Archiv-
Commit und Merge laufen ohne Hooks. Erst danach ermittelt der Orchestrator
`post-merge` aus `core.hooksPath`, sonst aus dem Git-Hook-Verzeichnis (gewöhnlich
`.git/hooks`). Relative konfigurierte Pfade gelten ab Projektwurzel; ein
übergangener Standard-Hook wird gemeldet.

Hooks außerhalb des Git-Verzeichnisses im Arbeitsbaum müssen im Basis-`HEAD`
getrackt und gegenüber der Merge-Basis vom Zielbranch unverändert sein. Sonst
werden sie mit `changed_by_target_branch` bzw. `not_tracked_in_base` ausgelassen,
etwa generierte Husky-v9-Hooks unter `.husky/_/post-merge`. Externe konfigurierte
Hooks und Hooks im Git-Verzeichnis sind ebenfalls möglich.

Unabhängig vom Ablageort muss der Hook eine reguläre, ausführbare Datei sein,
deren Pfad kein Symlink-Segment enthält. Ein fehlender Hook erhält den
Auslassungsgrund `missing`. Ist der Pfad nicht lesbar oder lässt sich der
Hook-Inhalt nicht zuverlässig für den gebundenen Digest messen, lautet der
Grund `digest_unreadable`; eine Änderung des Inhalts nach der Messung führt
zu `content_changed`. Erst ein Hook, der diese Prüfungen besteht, wird mit
Argument `0` und einer Grenze von 600 Sekunden ausgeführt. Ergebnis, Exitcode
sowie stdout und stderr werden getrennt im Nachlauf-Record festgehalten;
beide Ausgaben sind auf je 8192 Bytes begrenzt und eine Kürzung wird vermerkt.
Ein Fehler oder Timeout erzeugt eine Warnung, ohne den gültigen Merge
zurückzunehmen. Fehlt ein zulässiger Hook, wird das Auslassen protokolliert.
Im Watch-Modus wird der Auftrag erst nach dem Nachlauf-Ergebnis nach
`outbox/done/` verschoben.

Der Hook erbt die **Umgebung der gestarteten Wache**, läuft im
Repository-Wurzelverzeichnis und erhält Argument `0`. Zu diesem Zeitpunkt
ist bereits der **Basisbranch** mit dem bestätigten Merge ausgecheckt.

**Hooks mit möglicherweise mehr als 600 Sekunden Laufzeit**, etwa einen
großen Build, sollten Sie separat ausführen: Setzen Sie
`[workflow] merge_completed_branch = false` und mergen Sie manuell, halten Sie
den Hook kurz oder bauen Sie einen eigenen Schalter zum Überspringen des
langen Schritts ein. Der Orchestrator bietet dafür keinen eigenen
Überspringen-Schalter; beim manuellen Merge gelten die normalen Git-Hook-Regeln.

Wenn Ihr Hook selbst einen Umgebungs-Schalter wie `MEIN_PROJEKT_SKIP_BUILD`
auswertet, setzen Sie ihn beim Start der Wache:

```bash
MEIN_PROJEKT_SKIP_BUILD=1 run_task --watch
```

Dieser Wert gilt für die **gesamte Wache und damit die Hooks aller von ihr
bearbeiteten Aufgaben**. Er wirkt nur, wenn Ihr Hook ihn tatsächlich
auswertet. Um den Wert zu ändern, beenden Sie die Wache und starten sie mit
der gewünschten Umgebung neu. Bei einem separaten Resume-Aufruf müssen
Sie den Schalter dort ebenfalls setzen, wenn er weiter gelten soll.

Der Hook startet in einer neuen Sitzung mit eigener Prozessgruppe.
Bei Timeout versucht der Orchestrator, diese Prozessgruppe
mit SIGTERM und nötigenfalls SIGKILL zu beenden. Kann er deren Ende nicht
bestätigen, enthält das Ergebnis `termination_uncertain: true`; die Warnung
nennt diesen Wert. Der bestätigte Merge bleibt bestehen. **Ungeprüft** ist,
ob dabei Kindprozesse außerhalb dieser Prozessgruppe erreicht werden,
beispielsweise Windows-Prozesse über WSL-Interop oder mit `setsid` gestartete
Prozesse. Solche Prozesse werden möglicherweise nicht beendet; prüfen Sie
deren Zustand selbst.

Wurde der Prozess nach dem Nachlauf-Intent unterbrochen, ist möglicherweise
unklar, ob der Hook lief. Ein gewöhnliches Resume führt ihn deshalb nicht
erneut aus und hält mit einer Meldung zum ungewissen Ausgang an. Prüfen Sie
den Zustand des Hooks und quittieren Sie dann **den offenen Intent für den
angezeigten Merge-Commit** mit dem unveränderten Auftragsdokument:

```bash
run_task --task-file inbox/meine-idee.md --resume \
  --acknowledge-post-merge <merge-commit> \
  --post-merge-rationale "Hook-Zustand geprüft; weiterer Lauf freigegeben"
```

Die Quittierung wird als `acknowledged_unknown` protokolliert und setzt den
Lauf ohne erneuten Hook-Aufruf fort; sie behauptet keinen Hook-Erfolg. Der
Watch-Modus behandelt den offenen Intent als wiederaufnehmbaren Halt und
verschiebt den Auftrag nicht wegen dieses Halts nach `outbox/failed/`.

Bei `[workflow] merge_completed_branch = false` erfolgt nach dem Archiv-Commit
kein Merge. Der Nachlauf wird als ausgelassen protokolliert und der Zielbranch
bleibt ausgecheckt. Zum manuellen Zusammenführen verwenden Sie:

```bash
git switch main
git log --oneline main..feature/einkaufsliste       # was hinzukommt
git merge --no-ff feature/einkaufsliste
```

> [!IMPORTANT]
> **Vor der nächsten Idee zurück auf den Hauptbranch.** Einen neuen Zielbranch legt der
> Orchestrator vom gerade aktiven Branch aus an. Steht der Arbeitsbaum noch auf
> dem Branch der letzten Aufgabe, erbt die neue Aufgabe deren Commits – und ihre
> Abnahme liest sie mit.

### 2.11 Den Orchestrator aktualisieren und ein Projekt nachziehen

Beenden Sie zunächst die laufende Wache mit <kbd>Strg</kbd>+<kbd>C</kbd>.
Notieren Sie den bisherigen Orchestrator-Commit; er ist der Rückweg für einen
unfertigen Lauf. Aktualisieren Sie den **Orchestrator-Checkout**:

```bash
cd ~/werkzeuge/Dual-Agent-Orchestrator
git status --short
git rev-parse HEAD            # bisherigen Stand für ein Resume aufbewahren
git pull --ff-only
run_task --help
```

Bei lokalen Änderungen oder auseinanderlaufenden Branches klären Sie diese
vor dem Update; `--ff-only` erzeugt keinen Merge-Commit. Der Symlink aus 1.3
zeigt weiter auf den aktualisierten Einstieg. Falls Sie auch die Provider-CLI
aktualisiert haben, führen Sie den **Offline-Quicktest aus 2.3** aus dem
Orchestrator-Checkout aus. Dessen Attrappen und der Probelauf aus 1.4 prüfen
keine projektspezifische Toolchain.

**Unfertige Läufe übernehmen keine neue Laufkonfiguration.** Resume prüft die
gebundenen Profile und Zertifizierungen gegen die installierte Version.
Ändert das Update einen Zertifizierungs- oder Profildigest, hält der Lauf
mit `AGENT-PROFILE-DIFF` an. Ein Update muss deshalb nicht jeden Lauf
anhalten; entscheidend ist dessen Bindung. Auch ältere, nicht mehr
unterstützte Aufzeichnungen können die Wiederaufnahme verhindern. Die
vollständigen Haltegründe stehen in 4.3. Es gibt keine automatische
Migration oder Anbieterumschaltung.

Sie haben zwei Wege:

- **Mit dem alten Stand abschließen:** Legen Sie einen getrennten Checkout
  des notierten alten Commits an und verwenden Sie dessen Einstieg direkt
  im Zielprojekt. Ersetzen Sie `<alter-orchestrator-commit>` durch den
  vollständigen Commit aus der Ausgabe vor dem Update:

  ```bash
  git -C ~/werkzeuge/Dual-Agent-Orchestrator worktree add --detach \
    ~/werkzeuge/Dual-Agent-Orchestrator-alt <alter-orchestrator-commit>
  cd ~/projekte/mein-projekt
  ~/werkzeuge/Dual-Agent-Orchestrator-alt/run_task \
    --task-file inbox/meine-idee.md --resume
  ```

  Aufgabe, Zielbranch und Records müssen unverändert zum Lauf passen; auch
  die gebundenen CLI-Binaries, Interpreter und Toolchain-Pfade müssen noch
  passen. Bei deren Drift genügt der alte Orchestrator-Commit allein nicht.
  Schließen Sie den alten Lauf ab, bevor Sie das Projekt nachziehen.
- **Den Lauf bewusst aufgeben und neu beginnen:** Prüfen und sichern Sie
  zuerst Teilergebnisse, vorhandene Commits, das Auftragsdokument und die
  Laufartefakte. Klären Sie offene Nebenwirkungen, besonders einen ungewissen
  Hook-Ausgang. Der folgende Einzelaufgabenstart ersetzt den aktiven Zustand,
  ohne die bisherigen Branch-Commits zurückzunehmen; er setzt den alten Lauf
  nicht fort. Verwenden Sie ihn auf dem im Auftrag bestimmten Zielbranch,
  mit sauberem Arbeitsbaum und einem zum aktuellen Stand passenden Auftrag:

  ```bash
  cd ~/projekte/mein-projekt
  git status --short
  run_task --task-file inbox/meine-idee.md --no-resume --force-overwrite-state
  ```

  Der neue Lauf bindet die aktuelle Konfiguration. Bearbeiten oder löschen
  Sie dafür keine State-, Checkpoint- oder Recorddateien von Hand.

**Prüfliste für jedes angeschlossene Projekt**, bevor Sie neue Arbeit starten:

- [ ] Prüfen Sie die globale und die im Projekt wirksame Git-Identität nach
  1.1; repo-lokale Einstellungen fehlen in neuen Klonen.
- [ ] `AGENTS.md` ist rollenneutral und verträglich mit dem Prüfvertrag (2.4).
  Prüfen Sie vorhandene `CLAUDE.md`, `CODEX.md` und `GEMINI.md` ebenfalls:
  feste Anbieterrollen, Git-Regeln und „kein Review ohne Findings“ dürfen
  nicht ungeprüft übernommen werden. Kennzeichnen Sie Rollendateien als
  Handbetrieb-Dateien.
- [ ] Vergleichen Sie `orchestrator.toml` mit 2.3 und 4.2. Prüfen Sie neue
  Schlüssel und geänderte Standards bewusst: lokaler Merge ist standardmäßig
  an (`merge_completed_branch = true`); Modellstille und offene Werkzeuge
  haben standardmäßig 900 beziehungsweise 3600 Sekunden Grenze
  (`stall_timeout_seconds`, `tool_timeout_seconds`). Prüfen Sie Gesamtlimit,
  Rollenbesetzung, Basisbranch, Pfadklassen und vollständige Prüfbefehle.
- [ ] Prüfen Sie Toolchain-Pfade, Versionen und externe Symlink-Ziele nach
  2.3 und 4.3 für den tatsächlich gewählten Implementer mit
  `python3 ~/werkzeuge/Dual-Agent-Orchestrator/scripts/check_implementer_sandbox.py`
  im Projektordner. Klären Sie Warnungen und beachten Sie die ausgewiesene
  Messgrenze. Prüfen Sie nie parallel zu einem Lauf. Bei Unterprozess-Tests
  prüfen Sie einen gezielten Test und bei belegter Sandbox-Grenze die optionale
  Regel aus 2.4. Ein grüner Host-Test beweist keine Sandbox-Sichtbarkeit.
- [ ] Entscheiden Sie über lokalen Merge und prüfen Sie `core.hooksPath`,
  Hook-Zulässigkeit, Laufzeit und Folgen eines Timeouts nach 2.10.
- [ ] Prüfen Sie vor einer Veröffentlichung die Audit-Berichte samt Historie
  auf persönliche Pfade (2.10); das Update bereinigt vorhandene Commits nicht.
- [ ] Führen Sie den Testbefehl und alle zusätzlichen Prüfbefehle aus 2.3
  in der Startumgebung aus und schreiben Sie die geprüfte Projektkonfiguration
  nach 2.5 fest.

**Vorhandenes Projekt einem Agenten zum Nachziehen übergeben.** Starten Sie
ihn im Projektordner und kopieren Sie diesen Auftrag:

```text
Lies ~/werkzeuge/Dual-Agent-Orchestrator/docs/reference/einrichtung.md.
Ziehe dieses bereits angeschlossene Projekt nach Abschnitt 2.11 nach.
Prüfe zuerst, ob ein unfertiger Lauf existiert. Halte die Wache an, notiere
den bisherigen Orchestrator-Commit und kläre mit mir vor dem Update, ob
der Lauf mit der alten Version beendet oder bewusst aufgegeben werden soll.
Prüfe AGENTS.md und vorhandene CLAUDE.md, CODEX.md und GEMINI.md auf
rollenneutrale Regeln und kennzeichne Rollendateien für den Handbetrieb.
Vergleiche orchestrator.toml mit der aktuellen Anleitung: Rollen, Standards,
Stille- und Werkzeuglimits, Toolchain-Wurzeln und externe Symlink-Ziele,
Testmatrix, Basisbranch, lokaler Merge und post-merge-Hooks.
Prüfe vor dem Einrichtungscommit die globale und die im Projekt wirksame
Git-Identität nach 1.1; fehlt sie, frage nach Name, E-Mail und Geltungsbereich.
Führe die Vorabprüfung aus 2.3 ohne parallelen Lauf aus, bei Unterprozess-Tests
auch einen gezielten Test; ergänze bei belegter Sandbox-Grenze die optionale
Regel aus 2.4. Prüfe Audit-Berichte und Historie auf persönliche Pfade nach 2.10.
Frag mich bei fehlenden Projektentscheidungen und vor Installationen,
Commits oder dem Aufgeben eines Laufs. Ändere keine Laufrecords von Hand.
Führe run_task --help und die Projekt-Prüfbefehle aus; nach CLI-Updates
auch den Offline-Quicktest aus 2.3. Starte keinen Agentenlauf. Zeig mir
alle Änderungen, Ergebnisse und verbleibenden ungeprüften Annahmen.
```

---

## Teil 3 — Von null: nur eine Projektbeschreibung

Ein leerer Ordner reicht dem Orchestrator nicht. Er braucht einen ersten
Commit und vor allem **einen Testbefehl, der schon beim
ersten Arbeitspaket funktioniert** – ohne ihn kann er kein Paket validieren und
hält beim ersten an. Diese Grundlage schaffen Sie einmal selbst; danach läuft
alles wie in Teil 2.

### 3.1 Das Repository anlegen

```bash
mkdir -p ~/projekte/mein-projekt
cd ~/projekte/mein-projekt
git init -b main
```

### 3.2 Die Beschreibung ins Repository legen

Legen Sie die vollständige Projektbeschreibung als `docs/spezifikation.md` ab.
Dort können Implementer, Reviewer und Final-Reviewer sie bei jedem Schritt nachlesen; die späteren Ideen
im Eingang bleiben kurz und verweisen darauf.

```bash
mkdir -p docs
cp /pfad/zu/meiner/beschreibung.md docs/spezifikation.md
```

### 3.3 Ein Gerüst mit einem grünen Test

Entscheiden Sie Sprache und Werkzeuge und legen Sie ein minimales Gerüst mit
**einem** Test an, der besteht.

> [!IMPORTANT]
> **Die vollständige Werkzeugkette gehört ins Gerüst.** Die Agenten installieren
> keine Pakete nach. Verlangt die Beschreibung TypeScript, einen Bundler oder
> eine Browser-Testumgebung, muss all das schon installiert und mit einem
> Skript aufrufbar sein – sonst hält das erste Arbeitspaket, das es braucht, an.

Für Python:

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

Das Beispiel legt reines JavaScript an. Für TypeScript mit Vite gehören
zusätzlich `typescript` und `vite` dazu, ein Skript `typecheck`
(`tsc --noEmit`) und ein Skript `build`.

Wer das Gerüst lieber erzeugen lässt, fragt Codex oder Claude **direkt**, ohne
Orchestrator – etwa: *„Lies docs/spezifikation.md. Richte ein leeres Projekt mit
der passenden Werkzeugkette und genau einem bestehenden Test ein. Noch keine
Fachlogik."* Der Agent braucht dafür Netzzugang, um Pakete zu installieren.
Claude Code fragt vor `npm install` nach; in Codex ist das Netz in der Sandbox
meist gesperrt. Prüfen Sie danach selbst, dass Test, Typprüfung und Build grün
sind.

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
git status --short   # muss nach dem Einrichtungscommit leer sein
```

### 3.5 Alles auf einmal oder in Zuwächsen

**Alles auf einmal** funktioniert. Eine einzige Idee genügt, der Orchestrator
schneidet die Arbeit selbst in Arbeitspakete:

```markdown
# Version 1 aus der Beschreibung aufbauen

Baue die Anwendung vollständig nach docs/spezifikation.md auf. Fertig ist
sie, wenn jede Funktion aus dem Abschnitt „Umfang“ benutzbar ist.
```

Ein Beispiel: Eine Beschreibung einer Kochbuch-App mit rund zwanzig Funktionen
wurde so in 18 Arbeitspaketen und drei Nacharbeiten der Abnahme vollständig
umgesetzt.

**In Zuwächsen** lohnt es sich, wenn Sie Zwischenstände ansehen und die
Richtung unterwegs ändern wollen. Schneiden Sie die Beschreibung dann in
Teile, von denen jeder für sich etwas Nutzbares ergibt, und geben Sie sie
**nacheinander** hinein:

1. Ein erster, durchgehender Kern – das Kleinste, das schon benutzbar ist.
2. Danach je Zuwachs ein Merkmal aus der Beschreibung.

Eine Idee im Eingang verweist dann nur noch auf die Beschreibung:

```markdown
# Rezepte anlegen und anzeigen

Setze Abschnitt 3 aus docs/spezifikation.md um: Rezepte anlegen, bearbeiten
und als Liste anzeigen. Speicherung und Suche folgen später.
```

Nach jedem Zuwachs: Ergebnis ansehen. Bei der Standardeinstellung liegt der
lokale Merge bereits im Hauptbranch und dieser ist ausgecheckt (2.10). Bei
abgeschaltetem Merge führen Sie den Zielbranch selbst zusammen – dann die
nächste Idee.

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

Die häufigsten Fälle:

- **Browseroberflächen testen:** In Node fehlt ein `document`. Installieren Sie
  eine Umgebung wie `jsdom` (`npm install --save-dev jsdom`) und schreiben Sie in
  die `AGENTS.md`, wie ein Test sie einschaltet – bei Vitest etwa mit der ersten
  Zeile `// @vitest-environment jsdom`.
- **Weitere Browser-APIs:** Auch `jsdom` kennt manches nicht, etwa IndexedDB.
  Entweder Sie installieren vorab einen Ersatz (zum Beispiel `fake-indexeddb`),
  oder Sie verlangen in der Idee, dass der Speicherzugriff über eine austauschbare
  Schnittstelle mit einer Testvariante im Speicher läuft.
- **Schriften, Bilder, Daten:** Legen Sie die Dateien ins Projekt und nennen Sie
  in der `AGENTS.md`, wo sie liegen. Ein Agent ohne Netz kann sie nicht
  herunterladen und würde sie sonst erfinden.

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
`orchestrator.toml` des Zielrepositorys vor den mitgelieferten TOML-Profilen. Ohne eigene `[roles]`- und `[agent_profiles.*]`-Tabellen erbt das Projekt die mitgelieferte Besetzung; explizite Rollenoptionen und Umgebungsvariablen überschreiben Profilfelder. Das Claude-Budget steht unter `agent_profiles.<name>.provider_options.claude.max_budget_usd`.

Agenten haben standardmäßig kein Gesamtzeitlimit. `--implementer-timeout`,
`--reviewer-timeout` und `RUN_TASK_*_TIMEOUT` setzen es in Sekunden; `0` schaltet
es aus. Stille- und Werkzeuggrenzen werden im [Zusatz in 4.3](#zusatz-sonderkonfigurationen-für-die-meisten-projekte-nicht-nötig) erklärt.
Validierungszeitlimits und `RUN_TASK_REVIEW_TIMEOUT` des separaten Review-Harness
sind unabhängig davon. `--resume` wiederholt einen sicher beendeten Aufruf.
Bei unbekannter Prozesslage nennt das fingerprintgebundene Gate die Pfade und
`--resume --approve-gate --gate-rationale "…"`.

| Abschnitt | Wirkung |
|---|---|
| `[paths]` | ordnet Pfade Produktivcode, Tests, Doku und Erzeugtem zu; unbekannte Pfade zählen als Produktivcode. Die Klassen steuern unter anderem, welche Scope-Erweiterungen automatisch genehmigt werden. |
| `[validation]` | `default_command` läuft nach jedem Paket; `[[validation.rules]]` ergänzen `command` für bestimmte Pfadmuster. Alle Befehle sind Argumentlisten; für Unterordner und Shell-Semantik etwa `default_command = ["sh", "-c", "cd app && flutter test"]`. `required_artifacts` und `product_command` erlauben Akzeptanzkriterien gegen Bauergebnis beziehungsweise laufendes Produkt. |
| `[[stop_rules]]` | projektspezifische Stoppregeln, die den Implementer vor einer Verletzung anhalten lassen |
| `[repository]` | `base_branch` nennt den Hauptbranch ausdrücklich. Ohne Angabe erkennt der Orchestrator ihn selbst: Standardbranch des Remotes, sonst der einzige von `main` und `master`, sonst der einzige Branch außerhalb von `feature/…` und `codex/…`. |
| `[workflow]` | Zusätzliche menschliche Freigaben: `plan_gate`, `test_change_gate`, `manual_slice_gate`, `scope_extension_gate` – standardmäßig aus. Bei ausgeschaltetem `scope_extension_gate` genehmigt der Orchestrator angemeldete Umfangserweiterungen eines Arbeitspakets selbst und fragt den Programmierer neu; eingeschaltet gehen bestehende Testdateien und Dateien späterer Pakete an Sie. Arbeitsplan und Prüfberichte sind nie erweiterbar. `merge_completed_branch` ist standardmäßig `true` und wird für den ganzen Lauf im Run-Profil gebunden; `false` lässt den Zielbranch nach dem Archiv-Commit ausgecheckt. `archive_run_directory` ist standardmäßig `{run_id}` und wird ebenfalls beim Laufstart gebunden. Es bezeichnet einen relativen Unterordner von `docs/internal/archive/`, verlangt `{run_id}` als vollständiges Segment und erlaubt zusätzlich `{year}` und `{branch_slug}`. Ungültige Muster werden bei der Konfiguration abgewiesen; ein vorhandener Zielordner oder ein Symlink auf dem Zielpfad stoppt vor dem ersten Umsetzungsslice. Beide Abschluss-Schlüssel stehen nur in `orchestrator.toml`, ohne Kommandozeilenoption. |
| `[[provider_input_budget]]` | Obergrenzen für die Eingabegröße je Provider, Rolle und Operation |

Ohne `default_command` sucht der Orchestrator selbst: zuerst `pyproject.toml`
mit pytest, dann ein `test`-Skript in `package.json`, dann ein `test`-Ziel im
`Makefile`. Findet er nichts, gibt es keinen Testbefehl, und das erste
Arbeitspaket hält mit `validation request requires at least one command` an.

Alle Optionen und Umgebungsvariablen stehen im Abschnitt „Konfiguration" der [README](../../README.md),
eine vollständige Beispieldatei ist [`orchestrator.toml`](../../orchestrator.toml)
dieses Repositorys.

### 4.3 Modelle und CLI-Versionen

| | Codex (Standard-Implementer) | Claude (Standard-Reviewer und Final-Reviewer) |
|---|---|---|
| Modell | `sol` (Standard), `terra`, `luna`, `astra` (Auflösung beim Laufstart) | `opus` (Standard), `sonnet`, `fable` |
| Effort | `low`, `medium`, `high` (Standard), `xhigh`, `max` | `low`, `medium`, `high` (Standard), `xhigh`, `max` |
| Option | `--implementer-model`, `--implementer-effort` | `--reviewer-model`, `--reviewer-effort` |
| Umgebung | `RUN_TASK_IMPLEMENTER_MODEL`, `RUN_TASK_IMPLEMENTER_EFFORT` | `RUN_TASK_REVIEWER_MODEL`, `RUN_TASK_REVIEWER_EFFORT` |

Der Orchestrator liest für Codex über das identitätsgebundene Binary den lokalen Katalog
mit `codex debug models` in bereinigter Umgebung. Je Familie gewinnt der
kleinste `priority`-Wert unter `supported_in_api: true` und `visibility: "list"`;
leere oder mehrdeutige Auswahl hält an. Volle Katalog-IDs sind zulässig.
Laufprofil und Log binden den Slug. Resume prüft seine Verfügbarkeit im lokalen
Katalog und wählt nicht neu; das gilt für alle Codex-Slots.

**Toolchain und Resume:** Die Auflösung und Freigabe für beide Implementer
stehen in 2.3. Codex bindet nicht den gesamten PATH, sondern die ausgewählte CLI
und deren Interpreter mit Pfaden, Versionen und Digests. Bei
`#!/usr/bin/env node` zählt die erste ausführbare Node im PATH. Eine andere
CLI-/Interpreter-Identität beim Resume ergibt `AGENT-PROFILE-DIFF`. Gleiche
Identität trotz geändertem PATH genügt für diese Bindung, garantiert aber keine
gleiche Test-Toolchain: Beim Resume dieselbe Umgebung verwenden und erneut prüfen.

Für beide gelten dieselben Grenzen aus `toolchain_paths.py`: höchstens acht
existierende absolute Verzeichnisse, ohne Komma oder Leerraum; Symlinks
werden aufgelöst, doppelte aufgelöste Ziele abgewiesen. Ausgeschlossen sind
HOME selbst und seine Vorfahren (auch `/`), Repository und dessen Vorfahren
oder Unterverzeichnisse sowie Überschneidungen mit Schutzpfaden. Ebenso
ausgeschlossen sind diese Credential-Pfade unter HOME, ihre aufgelösten
Ziele und alle überlappenden Wurzeln: `.ssh`, `.gnupg`, `.aws`, `.azure`,
`.config`, `.codex`, `.claude`, `.claude.json`, `.gemini`, `.docker`, `.kube`,
`.netrc`, `.git-credentials`, `.password-store`, `.local/share/keyrings`,
`.npmrc` und `.pypirc`. Eine konkrete Installation unter HOME ist erlaubt,
sofern sie keine dieser Grenzen überschneidet. Reviewprofile dürfen die
Option nicht setzen. Die Pfadbindung wird bei Resume auf Drift geprüft.

**Gemessener Rückfall (02.10.2026, codex-cli 0.159.2):** Ohne Toolchain-Wurzeln
zeigte `ls -A "$HOME"` nur `.nvm` als Paketpfad. Die Home-Node war unsichtbar;
`command -v node` fand `/usr/bin/node`, ebenfalls v22.23.2. Ein gezielter Test
war grün, `.git` schreibgeschützt. Das ist eine Host-Messung: Paketfreigabe
macht Home-Werkzeuge nicht allgemein sichtbar. Das Sollbild einschließlich
Repository-Pfad und die Gegenprüfung stehen in 2.3.

**Messgrenze:** Die Vorabprüfung startet
`codex sandbox -c "<erzeugtes Rechteprofil>" -P dao-implementer -C <projekt> -- …`.
CLI-Identität, Paketfreigabe, Schutzpfade, Werkzeugwurzeln, Scratch und
Umgebungs-Positivliste entsprechen dem Implementer. `codex exec` bindet das
gleiche Profil über `default_permissions="dao-implementer"`, schaltet aber
zusätzlich Benutzerkonfiguration, Ausführungsregeln und Features aus und setzt
`shell_environment_policy.inherit="core"`. `sandbox` startet den Befehl direkt
mit der erlaubten Elternumgebung. Shellinitialisierung und CLI-Änderungen können
daher andere Ergebnisse erzeugen: Die Vorabprüfung bleibt eine **Näherung**.

**Platzhalter (02.10.2026, codex-cli 0.159.2):** Für fehlende Schutzpfade legt
die CLI leere Host-Dateien oder Verzeichnisse an. Bei `sandbox` verschwanden sie
nach normalem Ende und SIGTERM, nach SIGKILL blieben sie; bei `exec` wurden auch
nach Exit `0` Reste gemessen. Adapter und Vorabprüfung bereinigen deshalb erst
nach nachgewiesenem Ende der Prozessgruppe, verankert per Dateideskriptor am
ursprünglichen Elternordner. Nur vorher fehlende, eigene, leere reguläre Dateien
oder Verzeichnisse mit passender Signatur werden entfernt. Bestehende Pfade,
Symlinks und unerwartete Inhalte bleiben erhalten; Schutzbaumänderungen halten
den Lauf an. Die Vorabprüfung meldet unerwartete Inhalte mit Exit `2`.
Bei Zeitlimit oder Abbruch sendet sie SIGTERM, wartet bis zu fünf Sekunden und
sendet nötigenfalls SIGKILL. Entfernung und Reste stehen im Bericht und JSON.

Die Offline-Grenzprüfung nutzt zusätzlich `printf DAO_NORMAL_OK`, um einen
normalen Aufruf durch die Werkzeug-Sandbox zu führen, und vergleicht Schutzbäume
vor dem Lauf, vor und nach der Bereinigung. Sie meldet entstandene, entfernte
und verbleibende Pfade. Ein Aufruf ohne Werkzeugbefehl belegt diese Grenze nicht.

Bei Claude zeigt die Vorabprüfung nur die **Auflösungsnäherung** im festen PATH
mit vorangestellten `<wurzel>/bin` und die konfigurierten Freigaben. Ohne
modellfreien Zugang zur gleichen Bash-Sandbox bleiben Home-Sichtbarkeit,
Repository-Schreibrechte und `.git`-Schutz ungemessen. Exit `0` bestätigt nur
die Auflösung.

**Rechteprofil:** `dao-implementer` erlaubt ohne `--sandbox` Schreiben im
Repository und privaten Scratch (0700, TMPDIR); Schutzpfade einschließlich
externer Worktree-Gitverzeichnisse bleiben schreibgeschützt. Minimale
Systempfade, Codex-Paket und explizite Werkzeugwurzeln sind lesbar. Persönliche
Home-Dateien und Zugangsdaten werden nicht freigegeben. Benutzerkonfiguration,
Ausführungsregeln, Websuche, Apps, Plugins, Benutzer-MCP und Unteragenten sind
ausgeschaltet; `AGENTS.md` bleibt wirksam. Die Umgebung enthält nur PATH, HOME,
CODEX_HOME, LANG, LC_*, TERM und TMPDIR. `/tmp` ist ein privater Sandbox-Bereich;
dortiges Schreiben ist zulässig, wenn die Host-Datei unverändert bleibt. Scratch
bleibt der vorgesehene Zwischenablageort. Der gehärtete Katalog wird beim Start
und Resume über den Modelleintrag gebunden. Messdetails:
[Implementer-Zertifizierung](implementer-certification.md).

System-Python und pytest unter `/usr` brauchen keine zusätzliche Wurzel. Die
Offline-Grenzprüfung misst dies über die echte CLI (`system-python`,
`network-dns`); persönliche User-Site bleibt ohne Freigabe unsichtbar.

Claude-Aliase folgen dem neuesten Familienmodell; Codex bindet den besten
Katalograng. Modell und Effort gelten je Lauf. Die Wache nutzt ihre Angaben für
neue Aufgaben; laufende Aufgaben behalten ihre Bindung. Abweichende Angaben beim
Resume ergeben `AGENT-PROFILE-DIFF`.

**Nach Updates:** Geänderte Zertifizierungs-, Policy-, Rechte-, Transport-,
Fähigkeits- oder Identitätsdigests, Katalogeinträge, Isolationspfade und
Profilwerte können `AGENT-PROFILE-DIFF` auslösen. Fehlende Stille-/Werkzeug-Policy
oder fremde Reducer-Version verhindern Resume; `legacy-state-v3` und
`structured-v1` ergeben `UNSUPPORTED-PROTOCOL`. Rückweg zur passenden Version
oder bewusster Neustart: 2.11.

Das Fähigkeitsregister
[`schemas/native-provider-schema-capabilities-v2.json`](../../schemas/native-provider-schema-capabilities-v2.json)
bindet die Aufrufform der beiden Kommandozeilen, nicht Modell und Effort. Die
Schemamerkmale wurden am 23.9.2026 für alle wählbaren Modelle und Effort-Stufen
gemessen und waren überall gleich.

Claude-Reviewer und Final-Reviewer starten mit `--tools Read`,
`--allowedTools Read`, `--permission-mode dontAsk` und `--restricted`.
`--restricted` begrenzt Dateiwerkzeuge auf den schreibgeschützten Snapshot als
Arbeitsverzeichnis und den per `--add-dir` freigegebenen privaten Runtime-Ordner;
absolute Fremdpfade, Traversal und aus dem Snapshot hinausführende Symlinks
bleiben für `Read` gesperrt. Die Grenze gilt auch bei Planreviews und
Vertragsreparaturen mit leerem Snapshot. Claude Code 2.1.283 wurde dafür live
geprüft.

Dasselbe Register legt die geprüften CLI-Mindestversionen fest. Alle
wohlgeformten neueren Versionen werden akzeptiert, auch neue Hauptversionen;
ältere Versionen werden abgewiesen. Die effektiven Rechte und die Isolation
aller ausgewählten Rollenpaare werden je Aufruf geprüft. Nach Updates beider
CLIs und bei einer neueren Erstinstallation gehört der
Offline-Quicktest aus 2.3 zur Prüfung vor dem Lauf (Messstände in 1.1).

**Codex-Kompatibilitätsgrenze:** Registerminimum 0.156.1, gemessene gehärtete
Aufrufform 0.159.2. `verify_agent_capabilities` prüft vor dem Arbeitsaufruf
`codex exec --help`, unter anderem auf `--ignore-user-config`, `--ignore-rules`,
`--disable` und `--config`. Fehlende Schalter ergeben `AgentCompatibilityError`
mit `missing required capability flags`; es gibt keinen ungeschützten Ersatz.
Rechteprofile sind Konfigurationswerte ohne eigene CLI-Fähigkeitsprobe.
Abgewiesene Werte führen zu technischen Fehlern und ohne gültiges natives
Ergebnis zu keiner Freigabe. Stilles Ignorieren unbekannter Werte lässt sich
nicht allgemein ausschließen; Codeprüfungen und Schutzbaumvergleich belegen
nicht jede ältere CLI. Verwenden Sie den Messstand oder einen neueren Stand
mit grünem Offline-Quicktest; das Registerminimum bleibt unverändert.

#### Zusatz: Sonderkonfigurationen (für die meisten Projekte nicht nötig)

Ein separater Finalslot ist sinnvoll, wenn die Abschlussprüfung ein anderes
Modell, einen anderen Effort oder andere Zeitgrenzen als die Arbeitspaketprüfung
braucht. Ohne ihn erbt sie das Reviewerprofil. In der Projekt-TOML:

```toml
[roles]
implementer = "implementation"
reviewer = "review"
final_reviewer = "final_review"

[agent_profiles.final_review]
provider = "claude"
model = "opus"
effort = "high"
timeout_seconds = 0
```

`--final-reviewer-model` und `RUN_TASK_FINAL_REVIEWER_MODEL` überschreiben
diesen Slot.

Die auskommentierte Vorlage `experimental_antigravity` steht in
[`orchestrator.toml` des Orchestrator-Checkouts](../../orchestrator.toml).
„Lauf-TOML“ meint die Projektdatei `orchestrator.toml` oder die mit `--config`
gewählte Datei. Für AGY dorthin kopieren, einen Reviewslot in `[roles]` darauf
setzen und die vollständige `[[provider_input_budget]]`-Tabelle übernehmen:
[AGY-Anleitung](antigravity-reviewer.md). Beide Slots sind nach den Live-Canaries
vom 29.09.2026 `experimental`, nur für private DIY-Nutzung unter WSL 2 / Ubuntu /
ext4. Snapshot, Anfrage, Schema und Evidenz gehen vollständig an Google. Nach dem
gescheiterten 128-Befunde-Fall ist kein endlicher Timeoutvorschlag gemessen;
positive Werte und `0` sind zulässig.

#### Andere Belegung: Claude / Codex / Codex

Der Implementer-Hersteller muss sich von beiden Review-Herstellern unterscheiden.
Jeder Slot braucht gültige `certified`- oder `experimental`-Evidenz; `candidate`
bleibt gesperrt. Claude/Implementer und beide Codex-Reviewslots sind seit
01.10.2026 `experimental`. Start und Resume prüfen die Qualifikations- und
Canary-Digests. Grenzen und akzeptierte Schwächen:
[Reviewer-Zertifizierung](reviewer-certification.md),
[Implementer-Zertifizierung](implementer-certification.md).

```toml
[roles]
implementer = "implementation"
reviewer = "review"
final_reviewer = "final_review"

[agent_profiles.implementation]
provider = "claude"
model = "opus"
effort = "high"
timeout_seconds = 0

[agent_profiles.implementation.provider_options.claude]
toolchain_read_roots = ["/absolute/node-root"]

[agent_profiles.review]
provider = "codex"
model = "sol"
effort = "high"
timeout_seconds = 0
stall_timeout_seconds = 900
tool_timeout_seconds = 3600

[agent_profiles.final_review]
provider = "codex"
model = "sol"
effort = "high"
timeout_seconds = 0
stall_timeout_seconds = 900
tool_timeout_seconds = 3600
```

Die Zertifizierung gilt **je Slot**. Topologie-Evidenz liegt für Claude / Codex /
Codex und die Standardbelegung vor; für andere Mischungen wird ein eigener
Probelauf empfohlen. Ersetzen Sie `/absolute/node-root` nach 2.3.

| Grenze | Standard und Wirkung |
|---|---|
| `stall_timeout_seconds` | 900 Sekunden Modellstille; jede stdout-Zeile setzt zurück, stderr nicht. Während gemeldeter Werkzeuge ruht die Uhr: Codex `item.started` bis `item.completed`, Claude-Implementer `tool_use` bis `tool_result`, auch ohne Live-Anzeige. |
| `tool_timeout_seconds` | 3600 Sekunden je offenes Werkzeug, ab Start je ID. Ausgabe, parallele Werkzeuge und doppelte Starts verlängern die Grenze nicht; fehlende Abschlussmeldungen heben sie nicht auf. |
| `timeout_seconds` | Gesamtdauer einschließlich Werkzeugen; standardmäßig unbegrenzt. |

`0` schaltet die jeweilige Grenze aus; die zuerst erreichte beendet die
Prozessgruppe. Stille oder Werkzeugüberschreitung meldet `provider stalled`
mit letzter Aktivität bzw. `tool open N s`; Wiederholungen nutzen
`max_transport_failures` und `transient_policy.maximum_delay_seconds`. Nach
Budgetende bleibt der Halt fortsetzbar. Stille-/Werkzeugwerte sind laufgebunden;
Änderungen beim Resume werden abgewiesen. Alte Aufzeichnungen ohne diese Felder
bleiben lesbar, brauchen zum Resume aber die passende ältere Version.

Gemessene Hänger von 20–60 Minuten sprechen für eine Stillegrenze: erfolgreiche
Modellarbeit schwieg höchstens 93 Sekunden, ein Hänger 1.853 Sekunden. Werkzeuge
können legitim 12–14 Minuten schweigen. Deshalb bei Ereignisströmen Gesamtlimit
`0` nur mit beiden anderen Grenzen verwenden; ein zusätzliches Gesamtlimit
bleibt Ihre Wahl. Print-Transporte ohne Werkzeugereignisse (AGY und
Claude-JSON-Reviews) nutzen ausschließlich `timeout_seconds`.

### 4.4 Formale Aufträge statt Ideen

Wer Pfade und Arbeitspakete selbst vorgeben will, schreibt einen formalen
Auftrag: [`example-plan-task.md`](../../example-plan-task.md) zeigt einen reinen
Planungsauftrag, [`example-task.md`](../../example-task.md) einen
Umsetzungsauftrag mit `TASK_SCOPE`, Akzeptanzkriterien und Stoppbedingungen.
Solche Aufträge lassen sich auch ohne Wache starten. Setzen Sie für das
folgende Beispiel `TARGET_BRANCH: feature/mein-vorhaben` in der Aufgabendatei:

```bash
git switch main
git switch -c feature/mein-vorhaben
run_task --task-file task.md
```

**Im Einzelmodus muss der aufgelöste Zielbranch bereits existieren und aktiv
sein**, auch bei einer bloßen Idee. Existiert der
Branch schon, verwenden Sie `git switch feature/mein-vorhaben` statt `-c`.
Ohne passenden aktiven Branch hält der Start mit `STATE-SCHEMA … TARGET_BRANCH
mismatch` an. Nur die Wache legt den Zielbranch für neue Aufgaben selbst an;
Resume legt keinen Branch an und repariert keinen Branchwechsel.

Sobald eine Datei einen formalen Marker enthält, muss der ganze formale Vertrag
stimmen; halb formale Mischformen werden abgewiesen.

Ein bewusst getrennt gestarteter Implementierungs-Handoff ist ein neuer Lauf
und braucht `--no-resume --force-overwrite-state`, damit der vorherige
Planlauf nicht fortgesetzt wird:

```bash
run_task --no-resume --force-overwrite-state --task-file inbox/mein-vorhaben-implement.md
```

Im normalen Watch-Ablauf übernimmt die Wache den Handoff selbst.

### 4.5 Mehrere Projekte

Je Projekt läuft höchstens eine Wache; eine zweite meldet
`Another watcher is already running on inbox`. Mehrere Projekte laufen
nebeneinander, jedes in seiner eigenen tmux-Sitzung und mit eigenem Protokoll.

### 4.6 Bekannte Grenzen

- **Neue Aufgaben zweigen vom aktiven Branch ab**, nicht vom Hauptbranch. Wer
  zwischen zwei unabhängigen Aufgaben nicht auf den Hauptbranch zurückwechselt,
  erhält gestapelte Branches.
- **Eine Aufgabe nach der anderen.** Es gibt keine parallelen Arbeitspakete.
- **Ohne Testbefehl kein Lauf.** Ein Projekt ohne automatische Tests muss vorher
  mindestens einen bekommen.
- **Providerfehler kosten Zeit.** Wie oft ein Aufruf an etwas Sachfremdem
  scheitert und wiederholt wird, hängt stark vom Modell ab: Mit Opus als Prüfer
  lief im September 2026 kein einziger von über 100 Aufrufen schief, mit Sonnet
  etwa jeder dritte.

### 4.7 Fehlerbilder

| Meldung | Ursache | Abhilfe |
|---|---|---|
| `cannot determine the base branch; candidates: …` | der Hauptbranch ist nicht eindeutig, etwa weil es `main` und `master` gibt | `[repository] base_branch` setzen (4.2) |
| `configured base branch '…' is not a local branch` | Tippfehler, oder der Branch fehlt lokal | Namen in `orchestrator.toml` prüfen |
| `Needed a single revision` | das Repository hat noch keinen Commit | 3.4 |
| `automatic target-branch switch requires a clean non-ignored working tree` | eine unversionierte oder geänderte Datei liegt im Projekt | `git status --short` prüfen; Protokolle außerhalb des Projekts ablegen |
| `validation request requires at least one command` | kein Testbefehl konfiguriert oder erkannt | `default_command` in `orchestrator.toml` setzen |
| `shell_command is unsupported in structured-v2` oder `text validation commands cannot contain shell syntax` | Shell-Schlüssel oder Shell-Operatoren in einem Textbefehl | Den passenden Argv-Schlüssel in `orchestrator.toml` setzen: `default_command`, `product_command` oder `command` in `[[validation.rules]]`, jeweils als Liste wie `["sh", "-c", "cd app && flutter test"]` |
| `current gate has no fingerprint` mit Gate-Grund und Stoppgrund | `--approve-gate` oder `--reject-gate` wurde für einen Stopp ohne Fingerprint benutzt; keine Freigabe erforderlich | Voraussetzung bereitstellen, dann `run_task --watch --resume` ohne `--approve-gate` |
| `Unsupported codex CLI version` oder `Unsupported claude CLI version` | CLI unter dem Registerminimum oder Versionsformat nicht unterstützt; neuere Hauptversionen sind zulässig | Version und Format prüfen, gegebenenfalls CLI aktualisieren |
| `model must be one of …` | ein Modell außerhalb der wählbaren Familien | einen der genannten Werte verwenden (4.3) |
| `AGENT-PROFILE-DIFF` | anderes Modell, Effort oder Profil; auch: Orchestrator aktualisiert, Lauf aus älterer Version mit geändertem Zertifizierungs-/Profildigest | gleiche Bindung wiederherstellen; alte Orchestratorversion verwenden oder bewusst neu starten (2.11, 4.3) |
| `transport differs from its probed schema capability` | die Aufrufform einer Kommandozeile weicht vom Register ab, etwa nach einem größeren CLI-Update | CLI-Version prüfen, Orchestrator aktualisieren |
| `Another watcher is already running on inbox` | es läuft schon eine Wache für dieses Projekt | die laufende Sitzung verwenden (`tmux attach`) |
| `fatal: stash failed` beim Zusammenführen | Git ist auf automatisches Zwischenspeichern eingestellt | `git -c merge.autoStash=false merge --no-ff <branch>` |
| `Explicit --agents-file does not exist` | ein ausdrücklich angegebener Pfad fehlt | Pfad korrigieren; ohne Angabe gilt `AGENTS.md` im Projektordner |
| Exitcode `4` mit `bootstrap_check` | ein lokaler Vorabcheck ist gescheitert, keine Entscheidung nötig | Ursache im Protokoll beheben, dann erneut `run_task --watch` |
| Datei landet als `.poison` in `outbox/failed/` | wiederholter technischer Fehler | `.poison.error.json` daneben lesen |
