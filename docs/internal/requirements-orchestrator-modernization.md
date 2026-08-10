# Anforderungsbeschreibung: Modernisierung des Dual-Agent-Orchestrators

**Adressat:** Codex (Planer und Implementierer)
**Liefergegenstand dieses Dokuments:** ein Arbeitsplan — kein Code.
**Stand des Repos zum Zeitpunkt der Erstellung:** Branch `claude/orchestrator-modernisierung-i2cm99`, Commit `0bd3bad`, 92 Tests grün (`python3 -m pytest tests/ -v`).

---

## 0. Auftrag

Codex erstellt aus diesem Dokument einen **Arbeitsplan** mit Slice-Zerlegung und legt den Feature-Branch an. Der Plan wird anschließend von Claude Code und Antigravity geprüft (Verfahren siehe §2).

**In diesem Schritt ist zu liefern:**

- ein Plandokument gemäß §9,
- ein Feature-Branch,
- sonst nichts.

**In diesem Schritt ist ausdrücklich nicht zu liefern:** Quellcode, Testcode, Änderungen an bestehenden Dateien außer dem neuen Plandokument. Wer den Plan schreibt, implementiert nicht gleichzeitig.

Die Befunde in §3 sind am Code verifiziert. Codex darf sie nachprüfen, muss sie aber nicht neu herleiten. Widerspricht ein Befund dem, was Codex im Code vorfindet, ist das im Plan als Abweichung zu vermerken statt stillschweigend zu korrigieren.

---

## 1. Ausgangslage und Zielbild

Das Repo entstand Ende 2025. Die damalige Kernsorge war, ob ein Coding-Agent eine Aufgabe überhaupt ohne Abdriften zu Ende bringt. Daraus folgten die prägenden Entwurfsentscheidungen: monolithische Phase 2, harte Zyklendeckel, aggressive Kontextbeschneidung, rigide Kontraktmarker.

Das Problem hat sich gedreht. Die Frage ist nicht mehr, ob ein Agent die Aufgabe schafft, sondern ob er rechtzeitig aufhört. Die Struktur muss von einer Hilfestellung für schwache Modelle zu einer Leine für starke werden.

**Zielbild:** drei unabhängige Instanzen mit getrennten Rechten, Arbeit in kleinen freigegebenen Scheiben (Slices), ein Commit je freigegebenem Slice, harte Testgates ohne Ermessensspielraum.

Der Umbau dient zugleich als Belastungstest für das Drei-Agenten-Verfahren selbst: klare Zielvorgabe, überschaubares Repo, dessen Verlust nichts kostet.

---

## 2. Verfahren

Dieses Verfahren gilt **für die Durchführung dieses Umbaus** (das Meta-Verfahren), und es ist zugleich das Verfahren, das am Ende **im Orchestrator implementiert** sein soll. Beides ist bewusst deckungsgleich.

### 2.1 Rollen und Rechte

| Rolle | Schreibt Code | Reviewt | Commit-Recht |
|---|---|---|---|
| **Codex** | ja (einziger Schreiber) | nein | nein |
| **Claude Code** | nein | ja (erste Instanz) | nein |
| **Antigravity** | nein | ja (abschließende Instanz) | ja |

Ein Reviewer, der Code schreibt, hat seine Rolle verlassen. Diese Trennung ist nicht nur Prompt-Politik, sondern soll technisch durchgesetzt werden (siehe R-3).

### 2.2 Phase A — Planung

1. **Codex** erstellt das Arbeitsdokument mit Slice-Zerlegung und legt den Feature-Branch an.
2. **Claude Code** reviewt den Plan. Bei Mängeln: Findings an Codex, zurück zu Schritt 1.
3. **Antigravity** reviewt abschließend. Bei Mängeln: Findings an Codex, zurück zu Schritt 1.
4. Nach Freigabe durch beide Reviewer: **Antigravity erzeugt den lokalen Commit** des Plandokuments.

**Wichtig:** Nach jeder Überarbeitung durch Codex beginnt die Reviewkette wieder bei Claude Code. Es gibt keine Abkürzung direkt zu Antigravity, auch nicht bei kleinen Korrekturen.

### 2.3 Phase B — Implementierung, je Slice

Für Slice *n* in der im Plan festgelegten Reihenfolge:

1. **Codex** implementiert Slice *n* einschließlich Tests und führt den Testlauf gemäß §6.2 aus.
2. **Claude Code** reviewt und führt den Testlauf selbst aus. Bei Mängeln oder rotem Testlauf: Findings an Codex, zurück zu Schritt 1.
3. **Antigravity** reviewt abschließend und führt den Testlauf selbst aus. Bei Mängeln oder rotem Testlauf: Findings an Codex, zurück zu Schritt 1.
4. Nach Freigabe durch beide Reviewer: **Antigravity erzeugt den lokalen Commit** für Slice *n*.
5. Weiter mit Slice *n+1*.

Auch hier: jede Rückgabe an Codex setzt die Kette auf Claude Code zurück. Ein Slice ist erst abgeschlossen, wenn sein Commit steht.

### 2.4 Phase C — Endreview

Nach dem letzten Slice führen **alle drei Instanzen** ein Komplettreview über die Gesamtrealisierung durch — nicht über den letzten Slice, sondern über den gesamten Branch-Diff gegen `main`.

Prüfgegenstand:

- Architektur-Drift über die Slices hinweg,
- Konsistenz der Schnittstellen,
- tote Übergangszustände (Code, der nur existierte, um einen Zwischenstand lauffähig zu halten),
- Vollständigkeit gegenüber diesem Anforderungsdokument.

**Tritt irgendein Fehler auf, geht der Ablauf zurück in Phase B**: Codex korrigiert, die Slice-Kette läuft erneut für die betroffene Korrektur, danach beginnt das Endreview von vorn.

Der Merge nach `main` erfolgt manuell durch den Menschen und ist nicht Teil des Verfahrens.

### 2.5 Harte Gates

Diese Regeln gelten in jeder Phase und stehen nicht im Ermessen einer Instanz:

- **Roter Testlauf blockiert jede Freigabe.** Ohne Ausnahme, ohne Begründungsmöglichkeit.
- **Änderungen an Testdateien halten die Pipeline an.** Es braucht eine ausdrückliche, gesonderte Abnahme der Teständerung, bevor der reguläre Review fortgesetzt wird.
- **Kein Verdikt gilt als Ablehnung.** Schweigen, ein fehlender Marker oder eine unparsbare Antwort sind ein NEIN, kein „durchwinken".
- **Ping-Pong-Bremse:** Iterationszähler je Slice. Nach N Runden ohne Einigung Abbruch mit definiertem Zustand statt endloser Schleife. N und das Verhalten beim Abbruch legt der Plan fest (siehe §7).

### 2.6 Marker-Kontrakt des Meta-Verfahrens

Für die Durchführung dieses Umbaus gelten folgende Marker. Sie sind bewusst am bestehenden Kontrakt (`AGENTS.md`) orientiert. Ob die Zielimplementierung exakt diese Namen verwendet, ist eine Entscheidung im Plan (§7.6).

| Schritt | Marker |
|---|---|
| Jede Antwort, letzte nicht-leere Zeile | `STATUS: DONE` |
| Jede Reviewantwort, erste Zeile | `REVIEWER: claude` bzw. `REVIEWER: antigravity` |
| Codex Plan | `PLAN_READY: YES\|NO` |
| Review des Plans | `PLAN_APPROVAL: YES\|NO` |
| Codex Implementierung | `IMPLEMENTATION_READY: <slice-id> \| YES\|NO` |
| Review eines Slices | `SLICE_APPROVAL: <slice-id> \| YES\|NO` |
| Endreview | `FINAL_APPROVAL: YES\|NO` |
| Testlauf (jede Instanz, die testet) | `TEST_RESULT: PASS\|FAIL \| <command> \| <exit code>` |
| Test-Riegel | `TEST_FILES_TOUCHED: NONE` oder `TEST_FILES_TOUCHED: <pfade>` |
| Abnahme einer Teständerung | `TEST_CHANGE_APPROVAL: YES\|NO \| <Begründung>` |
| Findings | `OPEN_FINDINGS`, `FINDING_STATUS`, `NEW_FINDING` unverändert nach `AGENTS.md` |

Konsistenzregeln:

- `*_APPROVAL: YES` nur bei `OPEN_FINDINGS: NONE`.
- `*_APPROVAL: NO` nur, wenn mindestens ein Finding offen ist.
- `*_APPROVAL: YES` nur bei `TEST_RESULT: PASS`.
- Ist `TEST_FILES_TOUCHED` nicht `NONE`, ist `*_APPROVAL: YES` nur mit vorausgegangenem `TEST_CHANGE_APPROVAL: YES` zulässig.
- Findings behalten ihre ID über Slices hinweg. Eine ID darf nicht wiederverwendet werden.

---

## 3. Verifizierter Ist-Zustand

### 3.1 Tragfähig — beibehalten

| Baustein | Ort | Warum |
|---|---|---|
| Atomare Schreibvorgänge, Checkpoints, Pfadvalidierung gegen State-File-Injection | `src/state_io.py` | Nimmt nichts über Modellfähigkeiten an und altert deshalb nicht. |
| Findings-Kontrakt (`F-001`, `FINDING_STATUS`, `NEW_FINDING`, kumulative Historie) | `src/prompts.py`, `src/orchestrator.py:401-502` | Ein Finding kann nicht stillschweigend verschwinden. |
| Adaptermuster je CLI inklusive Stream-Filter | `src/agent_adapters.py` | Konzeptionell richtig; nur die Kommandozeilen veralten. |
| Inbox-Watcher mit fcntl-Lock, Poison-Pill, Success-Marker | `src/inbox_watcher.py` | Funktioniert; nur die Wechselwirkung mit Commits ist neu zu klären. |

### 3.2 Defekt oder unzureichend — verifizierte Befunde

Jede Zeile ist am Code geprüft. Die Fundstellen sind Einstiegspunkte, keine vollständige Trefferliste.

| # | Befund | Fundstelle |
|---|---|---|
| B-1 | **Der Normalpfad ist blockiert.** `run_task` setzt `--allow-fallback-to-gemini` in allen drei Aufrufzweigen. Der Orchestrator nimmt `gemini` dadurch in `required_agents` auf, der Preflight bricht bei fehlender Binary ab. Ohne `gemini` im PATH scheitert jeder Lauf über den Wrapper. | `run_task:112,152,168`; `orchestrator.py:1117-1119`; `agent_runtime.py:596` |
| B-2 | **„Git nicht vorhanden" stimmt nicht.** Lesender Zugriff existiert (`check_git_clean`, `repo_snapshot`, Fallback `git diff --name-only`). Es fehlt der schreibende Zugriff. Folge: `repo_snapshot()` zeigt nur uncommitted Änderungen — nach dem ersten Slice-Commit sieht der nächste Review einen leeren Diff. | `agent_runtime.py:73,153`; `orchestrator.py:789` |
| B-3 | **Rollentausch statt Erweiterung.** Heute plant Claude, Codex reviewt den Plan, Claude bestätigt. Das Zielbild dreht das um: Codex plant, Claude und Antigravity reviewen. Das ist keine additive Änderung. | `orchestrator.py:624,641,678` |
| B-4 | **Der Kontrakt ist asymmetrisch.** `validate_agent_contract()` läuft nur bei zwei von vier Agentenaufrufen. Claudes Plan- und Bestätigungsschritt werden nur gegen die Erzählmuster-Regex geprüft; ein `PHASE1_APPROVAL: YES` ohne `OPEN_FINDINGS`-Zeile wird dort akzeptiert. Der Override in `run_phase1` ist das Pflaster dafür. | `orchestrator.py:624-636,652,678-691,695,819` |
| B-5 | **Es wird das Falsche gedeckelt.** `truncate_shared` deckelt die Historie auf 30 000 Zeichen und schneidet vorn ab. Der Plan geht dagegen ungekürzt in jeden Phase-2-Prompt — das ist die komplette Phase-1-Historie inklusive aller Reviewrunden. | `orchestrator.py:240,1147`; `MAX_SHARED_CHARS:64` |
| B-6 | **Findings verlieren ihren Inhalt.** `finding_history` mischt zwei Wertetypen im selben Dict: Status (`OPEN`/`CLOSED`) und `"summary \| acceptance"`. Wird ein Finding geschlossen, überschreibt `CLOSED` die Beschreibung; das Akzeptanzkriterium ist weg. `print_summary_report` zählt nur exakte OPEN/CLOSED-Werte. | `orchestrator.py:668-674,835-841,326-334` |
| B-7 | **„Review only" wird per Regex auf Erzählmuster geprüft** (englischsprachig, „I will now…"). Das prüft, wie ein Agent redet, nicht was er tut. Beide Validatorfunktionen sind zudem byte-identisch außer im Fehlertext. | `orchestrator.py:521-554` |
| B-8 | **Die Änderungsliste ist unzuverlässig.** Primärquelle ist der Agentenbericht; der Fallback `git diff --name-only` sieht keine unversionierten Dateien. Genau der Fall „Agent legt neue Testdatei an" fällt durch — also der Fall, den der Test-Riegel abfangen soll. | `orchestrator.py:269,789` |
| B-9 | **Ungeprüfte Pfade im Reviewprompt.** `collect_file_snapshots` liest die vom Agenten gemeldeten Pfade. `is_plausible_path` lehnt führendes `...` ab, aber nicht `../` — `../../etc/passwd` passiert den Filter. `state_io` validiert Pfade streng, hier fehlt dieselbe Wurzelprüfung. | `agent_runtime.py:630-641`; vgl. `state_io.py:80` |
| B-10 | **State ist auf zwei Phasen verdrahtet.** `ensure_state_shape` fällt bei `version != 2` kommentarlos auf `init_state` zurück — ein neueres State-Format wäre für einen älteren Stand ein stiller Neustart. Checkpoint-Namen `{phase}-cycle-{n}.json` kollidieren, sobald zwei Slices eigene Zyklenzähler haben. | `state_io.py:100,148,175,236` |
| B-11 | **Abbruch ohne definierten Zustand.** Der Zyklendeckel wirft `RuntimeError`; `run_pipeline` fängt nur `QuotaReachedError`. Im Einzelmodus fliegt der Traceback durch, obwohl die README Exitcode 1 dokumentiert. | `orchestrator.py:726,869,1133`; `README.md:271-277` |
| B-12 | **Dry-Run genehmigt bedingungslos alles.** Die neuen Gates sind damit nicht verdrahtungstestbar. | `agent_runtime.py:212-231` |
| B-13 | **Adapter veraltet.** `GeminiAdapter` ruft `["gemini"]` ohne nicht-interaktives Flag auf. `ClaudeAdapter` nutzt `--no-session-persistence` und hartcodiert `--model opus` — ein hartkodiertes Modell macht Reviewer-Diversität unkonfigurierbar. | `agent_adapters.py:178-191,213-244` |
| B-14 | **Der Wrapper ist plattformfragiler als dokumentiert.** `run_task:45` nutzt `${VAR,,}` (Bash 4+). macOS liefert Bash 3.2 als `/bin/bash`. Der in der README zugesicherte macOS-Support ist damit bereits heute gebrochen — unabhängig von der Windows-Frage. | `run_task:45`; `README.md:23` |

---

## 4. Anforderungen

Prioritäten: **B** = Blocker (ohne dies läuft nichts), **H** = hoch, **M** = mittel.
Jede Anforderung ist so formuliert, dass ihre Erfüllung prüfbar ist. Die Zuordnung zu Slices ist Aufgabe des Plans, nicht dieses Dokuments.

### R-1 (B) — Normalpfad reparieren

Ein Lauf darf nicht daran scheitern, dass ein *optionaler* Fallback-Agent nicht installiert ist. Der Wrapper darf den Fallback nicht bedingungslos erzwingen.

*Abnahme:* Auf einer Maschine ohne Antigravity-/Gemini-Binary läuft `./run_task --dry-run` vollständig durch.

### R-2 (B) — Adapter auf aktuellen Stand

Gemini-Adapter durch die Antigravity-CLI (`agy`) ersetzen: Kommandozeile, `required_hosts`, `extract_output`, `stream_filter`. Codex- und Claude-Adapter gegen die aktuellen Kommandozeilen prüfen. Modell und Timeout je Adapter konfigurierbar machen statt hartzukodieren.

*Abnahme:* Alle drei Adapter starten und liefern auf einen Minimalprompt eine parsbare Antwort mit korrektem Abschlussmarker. Kein Modellname mehr im Quelltext fest verdrahtet.

*Randbedingung:* Kein Eingriff in die Orchestrierungslogik in dieser Anforderung.

### R-3 (H) — Werkzeugrechte statt Erzählmuster

Die Rollentrennung aus §2.1 ist technisch durchzusetzen: Reviewer-Aufrufe dürfen auf CLI-Ebene keine Schreibrechte haben. Die bestehende Erzählmuster-Regex bleibt als Sekundärsignal erhalten, die beiden Duplikate werden zusammengeführt.

*Abnahme:* Ein Reviewer-Aufruf, der eine Datei zu ändern versucht, scheitert an der CLI-Konfiguration, nicht erst an einer Textprüfung. Beide Validatorfunktionen existieren nur noch einmal.

### R-4 (H) — Pfadprüfung für Dateischnappschüsse

Vom Agenten gemeldete Pfade werden gegen erlaubte Wurzeln validiert, bevor sie gelesen werden — analog zu `_validate_loaded_path` in `state_io.py`.

*Abnahme:* Ein Bericht, der `../../etc/passwd` als geänderte Datei nennt, führt nicht dazu, dass diese Datei im Reviewprompt landet. Regressionstest vorhanden.

### R-5 (B) — Eine Diff-Quelle

Eine einzige Funktion liefert „geänderte Dateien seit Basis". Sie ist git-basiert, berücksichtigt unversionierte Dateien und funktioniert **nachdem** innerhalb des Laufs bereits committet wurde (Basis ist die Merge-Base des Feature-Branch, nicht der Arbeitsbaum). Der Bericht des implementierenden Agenten ist nur noch Zusatzsignal, nicht Primärquelle.

*Abnahme:* Test mit drei Zuständen: (a) uncommitted Änderung, (b) bereits committeter Slice plus neue Änderung, (c) neue unversionierte Datei. Alle drei werden korrekt gemeldet.

*Hinweis:* R-5 ist Voraussetzung für R-9 und R-10. Ohne sie sind beide unsicher.

### R-6 (B) — Kontrakt vereinheitlichen

Jeder Review-Aufruf durchläuft denselben Validator. Die Konsistenzregel (`YES` nur bei `OPEN_FINDINGS: NONE`) gilt für jede Instanz und jeden Schritt. `finding_history` wird zu einem Record je Finding mit getrennten Feldern für Status, Kurzbeschreibung, Akzeptanzkriterium sowie Herkunft (Slice, Zyklus, meldende Instanz).

*Abnahme:* Kein Agentenaufruf ohne Contract-Validierung. Ein Finding, das eröffnet und später geschlossen wird, hat am Ende noch seine Beschreibung und sein Akzeptanzkriterium. Der Sonderfall-Override entfällt oder wird zur generischen Regel.

*Hinweis:* R-6 ist Voraussetzung für R-7. Ohne sie multipliziert der dritte Reviewer die bestehende Asymmetrie.

### R-7 (H) — Dritter Reviewer und Rollentausch

Antigravity wird von der Fallback-Rolle in eine eigenständige Reviewrolle gehoben. Die Rollen werden gemäß §2.1 neu verteilt: Codex plant und implementiert, Claude und Antigravity reviewen. Freigabe nur, wenn **alle** vorgesehenen Freigaben vorliegen und keine Findings offen sind. Fehlendes oder unparsbares Verdikt zählt als Ablehnung.

*Abnahme:* Ein Lauf mit drei Instanzen erreicht die Freigabe nur bei dreifacher Zustimmung. Fällt eine Instanz aus, wird der Schritt abgelehnt, nicht übersprungen.

### R-8 (H) — Slice-Modell

Das Phasenmodell wird durch das Slice-Modell ersetzt. Der Arbeitsplan enthält die Slice-Liste, der Zustand führt den aktuellen Slice mit. Erforderlich sind:

- State-Schema in neuer Version mit **expliziter Migration**; ein unbekanntes Schema führt zu einem lauten Fehler, nicht zu einem stillen Neustart,
- Checkpoint-Namensschema, das Slices unterscheidet,
- Iterationszähler je Slice mit definiertem Abbruchzustand und dokumentiertem Exitcode — kein durchfliegender Traceback,
- Kontextregel umgedreht: der Plan wird destilliert weitergereicht, die Historie darf großzügiger sein.

*Abnahme:* Ein Lauf über mehrere Slices ist unterbrechbar und mit `--resume` fortsetzbar, ohne dass Slice-Fortschritt verlorengeht. Ein alter State wird migriert oder mit klarer Meldung abgelehnt. Der Abbruch bei erreichtem Iterationsdeckel liefert den dokumentierten Exitcode.

### R-9 (H) — Git-Integration

Feature-Branch beim Planen, lokaler Commit nach jeder Slice-Freigabe. **Der Commit folgt mechanisch aus der Freigabe und ist keine zweite Entscheidung.** Der Merge nach `main` bleibt manuell. Die Wechselwirkung mit dem Preflight (Sauberkeitsprüfung) und mit dem Watch-Modus ist zu klären und im Plan zu beschreiben.

*Abnahme:* Nach *n* freigegebenen Slices existieren *n* Commits auf dem Feature-Branch, jeder mit nachvollziehbarer Zuordnung zu Slice und Freigaben. Der Reviewprompt des Slices *n+1* zeigt einen korrekten Diff (siehe R-5).

### R-10 (H) — Test-Riegel

Vor jedem Review wird geprüft, ob Testdateien im Diff liegen. Falls ja, hält die Pipeline mit definiertem Zustand an und verlangt eine ausdrückliche Abnahme der Teständerung. Bewusst grob halten — lieber zu oft anhalten als einmal zu wenig. Der Freigabepfad muss **ohne Neustart des Laufs** funktionieren.

*Abnahme:* Eine neu angelegte, unversionierte Testdatei löst den Riegel aus (nicht nur eine geänderte bestehende). Nach erteilter Abnahme läuft derselbe Lauf weiter.

*Hinweis:* Dieses Repo besteht zu einem großen Teil aus Testcode. Der Riegel wird beim Selbstumbau ständig auslösen. Das ist gewollt und macht den Freigabepfad zur Kernfunktion, nicht zum Randfall.

### R-11 (M) — Gesamtabnahme

Nach dem letzten Slice ein Review durch alle drei Instanzen über den Gesamtdiff des Branches gegen `main`, mit den Prüfgegenständen aus §2.4. Ein Fehler führt zurück in die Implementierungsphase.

*Abnahme:* Das Endreview arbeitet nachweislich auf dem Branch-Diff, nicht auf dem letzten Slice. Ein dort gefundenes Finding führt zu einem regulären Slice-Durchlauf und danach zu einem erneuten Endreview.

### R-12 (M) — Dry-Run mit skriptbarem Verdikt

Der Dry-Run muss auch Ablehnungen, rote Testläufe, ausgelöste Test-Riegel und erreichte Iterationsdeckel simulieren können.

*Abnahme:* Jedes Gate aus §2.5 ist ohne echten Agentenaufruf verdrahtungstestbar.

### R-13 (M) — Plattform

Die Plattformfrage ist zu entscheiden (§7.1) und umzusetzen. Der Ist-Zustand — Bash-4-Syntax bei zugesichertem macOS-Support — ist in jedem Fall zu bereinigen.

*Abnahme:* Die README beschreibt die tatsächlich unterstützten Plattformen, und der Wrapper läuft auf allen davon.

### R-14 (M) — Dokumentation nachziehen

`README.md`, `AGENTS.md`, `CLAUDE.md`, `CODEX.md`, `workflow.puml` und `example-task.md` beschreiben nach dem Umbau das tatsächliche Verfahren. `GEMINI.md` wird zur Rollendatei der abschließenden Reviewinstanz (Umbenennung und Inhalt im Plan festlegen). Die Marker-Tabellen in README und AGENTS.md stimmen mit dem Parser überein.

*Abnahme:* Kein Dokument beschreibt ein Zwei-Phasen-Modell oder einen Gemini-Fallback mehr. Der bestehende Konsistenztest über die Instruktionsdateien bleibt grün.

---

## 5. Slice-Regeln

- Ein Slice ist die kleinste Einheit, die **eigenständig freigebbar und committebar** ist.
- Obergrenze: höchstens **eine kohärente Verhaltensänderung** je Slice.
- Nach jedem Slice muss ein **lauffähiger Stand** existieren: Tests grün, Orchestrator startbar.
- Die Dateizahl (Richtwert 10) ist eine **Notbremse mit Warnung**, kein Abbruchkriterium — 10 Dateien à 20 Zeilen und 10 à 800 sind nicht dasselbe. Der maßgebliche Schnitt ist fachlich (siehe §7.3).
- Abhängigkeiten zwischen Slices sind im Plan explizit zu machen. R-5 vor R-9 und R-10, R-6 vor R-7 sind bereits vorgegeben; weitere Abhängigkeiten ermittelt der Plan.
- Ein Slice, der bestehende Tests anpassen muss, ist als solcher zu kennzeichnen — er läuft in den Test-Riegel (R-10).

**Zur Reihenfolge:** Die Reihenfolge ist so zu wählen, dass nach jedem Slice ein lauffähiger Stand existiert. R-1 gehört an den Anfang: es ist Reparatur, nicht Modernisierung, und ohne sie ist der Rest nicht erprobbar.

---

## 6. Randbedingungen und Invarianten

### 6.1 Nicht verhandelbar

- **Die 92 bestehenden Tests bleiben grün.** Jede Verhaltensänderung bringt ihren eigenen Test mit.
- **`.orchestrator/state.json` und Checkpoint-Dateien werden nie von Hand editiert.**
- **Keine destruktiven Git-Operationen** ohne ausdrückliche Freigabe: kein `rm -rf`, kein Hard Reset, kein History-Rewrite, kein Force Push.
- **Keine Secrets, Tokens oder lokalen Pfade** in committeten Dateien.
- **Der Merge nach `main` bleibt manuell.**

### 6.2 Testlauf

Verbindlicher Testbefehl: `python3 -m pytest tests/ -v`

Jede Instanz, die laut §2 testet, führt diesen Befehl selbst aus und berichtet `TEST_RESULT` mit Befehl und Exitcode. Ein von einer anderen Instanz übernommenes Testergebnis ist kein eigener Testlauf.

### 6.3 Sprachregel

Das Repo erzwingt Englisch per Test (`tests/test_language_consistency.py`) für den Inhalt von `src/**/*.py`, `tests/**/*.py`, `README.md`, `run_task`, `example-task.md` sowie für **Dateinamen im Repo-Wurzelverzeichnis**.

Daraus folgt:

- Quellcode, Kommentare, Marker, Testnamen, Commit-Messages und Dateinamen: **Englisch**.
- Arbeitsdokumente unterhalb von `docs/`: Deutsch zulässig (dieses Dokument ist das Beispiel).
- Neue Dateien im Wurzelverzeichnis brauchen englische Namen.
- Wer den Sprachtest anfasst, fasst einen Test an — Test-Riegel (R-10).

### 6.4 Kontraktstabilität

Solange R-6 und R-7 nicht umgesetzt sind, bleibt der bestehende Marker-Kontrakt aus `AGENTS.md` gültig. Marker-Semantik ändert sich nur zusammen mit `src/prompts.py`, `src/orchestrator.py` und den Instruktionsdateien im selben Slice.

---

## 7. Zu treffende Entscheidungen

Diese Punkte sind bewusst nicht vorentschieden. Der Plan muss jeden Punkt **entscheiden und begründen** — eine Entscheidung ohne Begründung ist ein Finding. Die genannten Empfehlungen sind Vorschläge aus der Vorabanalyse, keine Vorgaben; eine abweichende, begründete Entscheidung ist zulässig.

### 7.1 Plattform

Die README schließt Windows aus und sichert macOS zu — letzteres ist bereits gebrochen (B-14). Optionen: WSL vorschreiben, Bash-Abhängigkeiten entfernen, oder den unterstützten Plattformkreis ehrlich verkleinern.

*Empfehlung:* Wrapper nach Python ziehen. Löst macOS und Windows in einem Zug und entfernt die Doppelpflege zwischen `run_task` und `orchestrator.py`.

### 7.2 Wer committet

Antigravity als Freigebender, oder der Orchestrator mechanisch nach der Freigabe? Beides vertretbar, unterschiedliche Risiken.

*Empfehlung:* Orchestrator mechanisch. Ein Agent mit Commit-Recht braucht Schreibrechte, was der Rolle „reviewt und schreibt nie" (§2.1, R-3) direkt widerspricht. Antigravity gibt frei, der Orchestrator führt aus.

*Zu klären:* Wie wird der Commit dann Antigravity zugerechnet — Trailer, Message-Konvention, Artefakt?

### 7.3 Slice-Maß

10 Dateien ist ein schwacher Maßstab. Alternativen: Diff-Zeilen oder rein fachlicher Schnitt.

*Empfehlung:* Fachlich — ein Slice entspricht einer Akzeptanzkriterien-Gruppe mit genau einem Grün/Rot-Kriterium. Dateizahl bleibt Notbremse mit Warnung.

### 7.4 Ping-Pong-Bremse

Welches N? Was passiert beim Abbruch mitten in einem Slice: Branch behalten, WIP committen, verwerfen? Welcher Exitcode?

*Zu klären, keine Empfehlung.* Wichtig ist nur, dass der Zustand nach Abbruch definiert und dokumentiert ist.

### 7.5 Erwartungswerte bei Rechenkernen

Schreibt derselbe Agent Code und Tests aus einer Interpretation, sind beide bei einem Missverständnis einträchtig falsch und trotzdem grün. Gegenmittel: fachliche Ankerwerte im Arbeitsplan, von Hand gesetzt.

*Empfehlung:* Als eigener Kontraktblock im Plan (`ANCHOR: <id> | <input> | <expected>`), den der Reviewer prüfen **muss**. Als Prosa gehen Ankerwerte in der Kontextbeschneidung verloren — genau der Fehlermodus, den sie verhindern sollen.

*Abgrenzung:* Betrifft die Zielarchitektur für Rechenkerne. Dieses Repo ist Infrastruktur und hat keine Rechenkerne; die Entscheidung ist im Plan zu treffen, hier aber nicht anzuwenden.

### 7.6 Marker-Namensraum bei drei Instanzen

Eigene Marker je Agent, oder ein generischer Marker mit Instanz-ID? Rückwärtskompatibilität zu `PHASE1_APPROVAL`/`PHASE2_APPROVAL`/den Legacy-Markern ist zu bewerten — behalten oder sauber schneiden?

*Empfehlung:* Generisch mit Instanz-ID, Legacy-Marker beim Schnitt auf das Slice-Modell entfernen statt mitzuschleppen. Die Kompatibilitätsschicht kostet bei drei Instanzen mehr, als sie einbringt.

### 7.7 Migrationsweg

Umbau im Bestand oder Neuschnitt mit Übernahme von `state_io.py`, Kontraktschicht und Watcher?

*Empfehlung:* Umbau im Bestand. `state_io.py`, `prompts.py` und `agent_adapters.py` sind sauber getrennt und tragfähig; die Kopplung sitzt allein in `orchestrator.py` (1 197 Zeilen). Ein Neuschnitt kostet die 92 Tests ohne Gegenwert.

*Nebenbei:* `run_phase1` und `run_phase2` sind strukturell fast identisch (Checkpoint → Agent → Contract-Parse → History-Merge → Gate). Das Slice-Modell ist die Gelegenheit, daraus **eine** Runde zu machen statt zwei parallele Kopien zu pflegen. Ob das ein eigener Slice ist oder Teil von R-8, entscheidet der Plan.

---

## 8. Nicht-Ziele

Ausdrücklich nicht Gegenstand dieses Umbaus:

- Automatischer Merge nach `main`.
- Ausführung auf mehreren Repos parallel.
- Web-Oberfläche, Dashboard oder Fortschrittsanzeige über Logs hinaus.
- Kostensteuerung, Token-Budgets, Modell-Auswahlheuristiken.
- Unterstützung weiterer Agenten-CLIs über die drei genannten hinaus.
- Rückwärtskompatibilität des State-Formats über die eine dokumentierte Migration hinaus.

---

## 9. Aufbau des Arbeitsplans

Codex liefert **ein Dokument** unter `docs/` mit englischem Dateinamen (§6.3). Aufbau:

1. **Zielbild in eigenen Worten** — kurz. Dient dem Abgleich, ob die Aufgabe verstanden wurde, nicht der Wiederholung dieses Dokuments.
2. **Entscheidungen** — jeder Punkt aus §7, entschieden und begründet. Abweichungen von den Empfehlungen sind ausdrücklich zu kennzeichnen.
3. **Slice-Liste.** Je Slice:
   - ID (`S-01`, `S-02`, …) und Titel,
   - Zweck in einem Satz,
   - abgedeckte Anforderungen (`R-x`),
   - betroffene Dateien,
   - Akzeptanzkriterien, prüfbar formuliert,
   - Tests, die neu entstehen oder sich ändern,
   - Abhängigkeiten zu anderen Slices,
   - Risiko und, falls vorhanden, Rückfalloption,
   - Kennzeichen, ob der Test-Riegel (R-10) ausgelöst wird.
4. **Reihenfolge und Abhängigkeitsgraph.** Mit Begründung, warum nach jedem Slice ein lauffähiger Stand existiert.
5. **Abdeckungsmatrix** — jede Anforderung R-1 bis R-14 einem oder mehreren Slices zugeordnet. Eine nicht zugeordnete Anforderung ist zu begründen, nicht zu übergehen.
6. **Migration und Rollback** — insbesondere State-Schema (R-8) und Branch-Strategie (R-9).
7. **Testplan** — was je Slice geprüft wird, und wie die Gates aus §2.5 selbst getestet werden.
8. **Offene Fragen** — was Codex ohne Rückfrage nicht entscheiden kann.

**Formale Anforderungen an den Plan:**

- Marker gemäß §2.6: `PLAN_READY: YES|NO`, `OPEN_FINDINGS: …`, letzte Zeile `STATUS: DONE`.
- Der Feature-Branch ist angelegt und im Plan benannt.
- Kein Code, keine Änderung an bestehenden Dateien.

---

## 10. Abnahmekriterien für den Arbeitsplan

Die Reviewer prüfen gegen diese Liste. Ein nicht erfülltes Kriterium ist ein Finding.

1. Jede Anforderung R-1 bis R-14 ist einem Slice zugeordnet oder begründet zurückgestellt.
2. Jede Entscheidung aus §7 ist getroffen und begründet.
3. Die vorgegebenen Abhängigkeiten sind eingehalten: R-1 zuerst, R-5 vor R-9 und R-10, R-6 vor R-7.
4. Nach jedem Slice existiert nachweislich ein lauffähiger Stand.
5. Kein Slice enthält mehr als eine kohärente Verhaltensänderung.
6. Jeder Slice hat prüfbare Akzeptanzkriterien — „funktioniert korrekt" ist keins.
7. Slices, die Tests anfassen, sind als solche gekennzeichnet.
8. Die Sprachregel (§6.3) ist im Plan berücksichtigt.
9. Der Plan enthält keinen Code und keine Änderung an bestehenden Dateien.
10. Der Feature-Branch existiert und ist benannt.
