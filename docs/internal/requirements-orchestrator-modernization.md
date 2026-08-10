# Anforderungsbeschreibung: Modernisierung des Dual-Agent-Orchestrators

**Adressat:** Codex (Implementer und Hauptautor der Arbeitsdokumente)
**Liefergegenstand dieses Dokuments:** ein Arbeitsplan mit Slice-Dokumenten — kein Code.
**Stand des Repos:** Feature-Branch `feature/orchestrator-modernization`, abgezweigt von `master` bei Commit `0bd3bad`. 92 Tests gesammelt, davon 91 grün und einer übersprungen (`python3 -m pytest tests/ -v`). Lokal verifiziert am 2026-08-10: Der Code entspricht der Analysebasis, die Befunde in §3 wurden gegen diesen Stand nachgeprüft und bestätigt.

**Revision 3** — geändertes Rollenmodell auf Nutzervorgabe. Codex und Claude Code sind die Hauptakteure und tragen den gesamten Korrekturverkehr. Antigravity prüft nur noch das jeweils fertige, von Claude freigegebene Ergebnis, behält aber das Commit-Recht. Der Fallback zwischen Agenten entfällt ersatzlos; ein Quotaausfall hält den Lauf an. Betroffen: §2.1 bis §2.5, R-1, R-7, neu R-18.

**Revision 2** — eingearbeitet: das manuelle Verfahren aus dem Zielrepo (Ruhestandsuite). Referenzen:

- `docs/internal/reference-target-repo-agents.md` — Rollen, Stop-Regeln, Review-Grundsätze, Validierung, Sicherheit.
- `docs/internal/reference-target-repo-slice-execution-rules.md` — Slice-Ablage, Diff-Risiko-Block, Commit-Regeln, Review-Zyklus, Rollback.
- `docs/internal/reference-target-repo-claude.md`, `-gemini.md`, `-codex.md` — die Rollendateien des Zielrepos, wortgleich. Belegen die Review-Pflichten und die dortige Rollenzuschnitte.

Diese beiden Dokumente beschreiben das Verfahren, das hier automatisiert werden soll. Sie sind **Referenz, nicht Laufzeitregel dieses Repos** — verbindlich für den Orchestrator bleibt dessen eigene `AGENTS.md` im Wurzelverzeichnis. Wo dieses Dokument von den Referenzen abweicht, ist die Abweichung unten ausgewiesen und begründet.

---

## 0. Auftrag

Codex erstellt aus diesem Dokument einen **Arbeitsplan** mit Slice-Zerlegung und legt den Feature-Branch an. Plan und Slice-Dokumente werden anschließend von Claude Code und Antigravity geprüft (Verfahren siehe §2).

**In diesem Schritt ist zu liefern:**

- eine Arbeitsplan-MD gemäß §9,
- der Feature-Branch,
- sonst nichts.

**In diesem Schritt ist ausdrücklich nicht zu liefern:** Quellcode, Testcode, Änderungen an bestehenden Dateien. Wer den Plan schreibt, implementiert nicht gleichzeitig. Die Slice-MDs entstehen erst unmittelbar vor dem jeweiligen Slice (§2.3), nicht vorab in einem Rutsch.

Die Befunde in §3 sind am Code verifiziert. Codex darf sie nachprüfen, muss sie aber nicht neu herleiten. Widerspricht ein Befund dem, was Codex im Code vorfindet, ist das im Plan als Abweichung zu vermerken statt stillschweigend zu korrigieren.

---

## 1. Ausgangslage und Zielbild

Das Repo entstand Ende 2025. Die damalige Kernsorge war, ob ein Coding-Agent eine Aufgabe überhaupt ohne Abdriften zu Ende bringt. Daraus folgten die prägenden Entwurfsentscheidungen: monolithische Phase 2, harte Zyklendeckel, aggressive Kontextbeschneidung, rigide Kontraktmarker.

Das Problem hat sich gedreht. Die Frage ist nicht mehr, ob ein Agent die Aufgabe schafft, sondern ob er rechtzeitig aufhört. Die Struktur muss von einer Hilfestellung für schwache Modelle zu einer Leine für starke werden.

**Zielbild:** drei unabhängige Instanzen mit getrennten Rechten, Arbeit in kleinen freigegebenen Scheiben (Slices), ein lokaler Commit je freigegebenem Slice, harte Testgates ohne Ermessensspielraum.

Das Verfahren existiert bereits — es wird heute von Hand gefahren (siehe Referenzdokumente). Der Auftrag ist nicht, ein Verfahren zu erfinden, sondern ein erprobtes zu automatisieren. Wo das manuelle Verfahren eine Frage bereits beantwortet hat, gilt diese Antwort, bis jemand sie begründet ändert.

Der Umbau dient zugleich als Belastungstest für das Drei-Agenten-Verfahren selbst: klare Zielvorgabe, überschaubares Repo, dessen Verlust nichts kostet.

---

## 2. Verfahren

Dieses Verfahren gilt **für die Durchführung dieses Umbaus** und ist zugleich das Verfahren, das am Ende **im Orchestrator implementiert** sein soll. Beides ist bewusst deckungsgleich.

### 2.1 Rollen und Rechte

| Rolle | Schreibt Code | Schreibt Dokumente | Reviewt | Freigabe | Commit |
|---|---|---|---|---|---|
| **Codex** | ja (einziger) | ja (Autor von Plan und Slice-MDs) | nein | nie für eigene Arbeit | nein |
| **Claude Code** | nein | nur Reviewfeedback in Plan/Slice-MD | ja (jede Runde) | ja | nein |
| **Antigravity** | nein | nur Reviewfeedback in Plan/Slice-MD | ja (nur das fertige Ergebnis, einmal je Anlauf) | ja | ja |

Die Trennung ist nicht nur Prompt-Politik, sondern technisch durchzusetzen (R-3). **Wichtig:** Reviewer sind nicht schreibrechtlos — sie schreiben ihr Feedback in die Plan- und Slice-Dokumente. Die Grenze verläuft zwischen **Dokumentation und Code**, nicht zwischen Lesen und Schreiben.

**Lastverteilung.** Hauptakteure sind Codex und Claude Code. Der gesamte Korrekturverkehr — Mängel melden, nachbessern, erneut prüfen — läuft zwischen diesen beiden. Antigravity sieht eine Einheit erst, wenn Claude sie freigegeben hat, und sieht sie genau einmal je Anlauf. Grund ist die deutlich geringere Modellqualität von Antigravity: Sein Urteil taugt als Kontrollblick vor dem Commit, nicht als Taktgeber im Korrekturzyklus. Antigravity behält dennoch das Commit-Recht — der Commit ist eine Abnahmehandlung, keine inhaltliche Leistung, und die Sicherheitsprüfung gegen den Slice-Scope (§2.3 Schritt 6) ist eine mechanische Prüfung, die auch ein schwächeres Modell zuverlässig ausführt.

*Abweichung von der Referenz:* Dort ist Antigravity der primäre und Claude der optionale zweite Reviewer. Hier ist es umgekehrt: Claude ist die durchgängige Reviewinstanz, Antigravity die abschließende Kontrolle. Die Rollendateien des Zielrepos — `reference-target-repo-claude.md` und `reference-target-repo-gemini.md` — beschreiben noch die alte Verteilung; `reference-target-repo-gemini.md` führt Antigravity sogar unter „Fallback-Specific Duties", also als Ersatzinstanz. Beides müsste nachgezogen werden. Das ist nicht Gegenstand dieses Umbaus, aber zu erwähnen.

### 2.2 Phase A — Planung

1. **Codex** erstellt die Arbeitsplan-MD unter `docs/internal/` und legt den Feature-Branch an. Branch-Name und GitHub-Status werden im Plan dokumentiert.
2. **Claude Code** reviewt den Plan und trägt sein Feedback unter `## Review-Feedback von Claude` am Ende des Plandokuments ein. Bei Mängeln: zurück an Codex.
3. **Codex** überarbeitet und antwortet unter `## Review-Antworten von Codex`. Danach zurück zu Schritt 2. Die Schritte 2 und 3 wiederholen sich, bis Claude freigibt.
4. **Antigravity** reviewt den von Claude freigegebenen Plan, Feedback unter `## Review-Feedback von Antigravity`. Bei Mängeln: zurück zu Schritt 3; der Zyklus aus Codex und Claude läuft erneut, und Antigravity prüft danach wieder den fertigen Stand.
5. Nach Freigabe durch beide Reviewer: **Antigravity erzeugt den lokalen Commit** des Plandokuments. Erst danach darf die Slice-Umsetzung beginnen.

**Antigravity nimmt an den Korrekturrunden nicht teil.** Er wird erst aufgerufen, wenn Claude freigegeben hat, und je Anlauf genau einmal. Umgekehrt gilt: Nach jeder Überarbeitung durch Codex beginnt die Reviewkette wieder bei Claude Code — es gibt keine Abkürzung direkt zu Antigravity, auch nicht bei kleinen Korrekturen.

### 2.3 Phase B — Implementierung, je Slice

Für Slice *n* in der im Plan festgelegten Reihenfolge:

1. **Codex** erstellt die Slice-MD (§9.2) **vor Beginn der Arbeiten**, inklusive Branch-Check (`git branch --show-current`), Statuscheck (`git status --short`) und Diff-Risiko-Block. Passt der aktive Branch nicht zum im Plan definierten Feature-Branch, stoppt Codex und fragt nach. Greift eine Stop-Regel (§2.5), stoppt Codex und fragt nach, statt zu implementieren.
2. **Codex** implementiert Slice *n* einschließlich Tests, führt die Validierung gemäß §6.2 aus und trägt die Ergebnisse unter `## Ergebnisse` in die Slice-MD ein.
3. **Claude Code** reviewt und führt die Validierung selbst aus. Feedback unter `## Review-Feedback von Claude` in der Slice-MD. Bei Mängeln oder roter Validierung: zurück zu Schritt 2.
4. **Codex** korrigiert und antwortet unter `## Review-Antworten von Codex`. Danach zurück zu Schritt 3. Die Schritte 2 bis 4 wiederholen sich, bis Claude freigibt.
5. **Antigravity** reviewt den von Claude freigegebenen Slice und führt die Validierung selbst aus. Bei Mängeln: zurück zu Schritt 4.
6. Nach Freigabe durch beide Reviewer: **Antigravity führt die Commit-Sicherheitsprüfung durch** — `git status --short`, Dateiliste dokumentieren, gegen den Slice-Scope abgleichen. Unerwartete Dateien blockieren den Commit. Danach lokaler Commit für Slice *n*.
7. Rückdokumentation des Slice-Status in die Arbeitsplan-MD, weiter mit Slice *n+1*.

Jede Rückgabe an Codex setzt die Kette auf Claude Code zurück; Antigravity kommt erst wieder zum Zug, wenn Claude erneut freigegeben hat. Ein Slice ist erst abgeschlossen, wenn sein Commit steht — er ist der Rollback-Punkt für den folgenden Slice.

### 2.4 Phase C — Endreview

Nach dem letzten Slice führen **alle drei Instanzen** ein Komplettreview über die Gesamtrealisierung durch — nicht über den letzten Slice, sondern über den gesamten Branch-Diff gegen `main`.

Prüfgegenstand:

- Architektur-Drift über die Slices hinweg,
- Konsistenz der Schnittstellen,
- tote Übergangszustände (Code, der nur existierte, um einen Zwischenstand lauffähig zu halten),
- Dokumentations-Sync (§6.2),
- Vollständigkeit gegenüber diesem Anforderungsdokument.

**Tritt irgendein Fehler auf, geht der Ablauf zurück in Phase B**: Codex korrigiert, die Slice-Kette läuft erneut für die betroffene Korrektur, danach beginnt das Endreview von vorn.

Der Push nach GitHub und der Merge nach `main` erfolgen erst nach ausdrücklicher Freigabe des Nutzers, niemals automatisch.

### 2.5 Harte Gates

Diese Regeln gelten in jeder Phase und stehen nicht im Ermessen einer Instanz:

- **Rote Validierung blockiert jede Freigabe.** Einzige Ausnahme: ein bewusst roter Contract-Slice nach der Red-State-Regel (§5), der eine namentlich benannte Folge-Slice hat. Ohne benannte Folge-Slice gilt die Ausnahme nicht.
- **Änderungen an Testdateien halten die Pipeline an.** Es braucht eine ausdrückliche, gesonderte Abnahme der Teständerung, bevor der reguläre Review fortgesetzt wird.
- **Stop-Regeln halten die Pipeline an.** Greift eine Stop-Regel (§6.4), wird nicht implementiert, sondern gefragt. Eine Stop-Regel ist kein Hinweis, sondern ein Gate.
- **Kein Verdikt gilt als Ablehnung.** Schweigen, ein fehlender Marker oder eine unparsbare Antwort sind ein NEIN.
- **Keine Freigabe ohne Findings.** Ein Review ohne dokumentierte Findings ist unzulässig. Findet der Reviewer nichts Blockierendes, dokumentiert er die geprüften Dimensionen, das größte Restrisiko und die Bedingung, unter der die Implementierung brechen würde.
- **Pre-Mortem vor jeder Freigabe.**
- **Unerwartete Dateien blockieren den Commit** (§2.3 Schritt 6).
- **Ping-Pong-Bremse:** Iterationszähler je Slice. Nach N Runden ohne Einigung Abbruch mit definiertem Zustand statt endloser Schleife. N und das Abbruchverhalten legt der Plan fest (§7.4).
- **Ausfall einer Instanz hält den Lauf an.** Kann eine der drei Instanzen nicht arbeiten — erschöpfte Quota, fehlende Binary, Zeitüberschreitung —, endet der Lauf mit definiertem Zustand und dokumentiertem Exitcode (R-18). **Es gibt keinen Ersatzagenten.** Keine Instanz vertritt eine andere, auch nicht vorübergehend. Der Wiederanlauf ist eine Nutzerentscheidung und erfolgt manuell über `--resume`. Das gilt für Codex und Claude Code, deren Ausfall den Korrekturzyklus unterbricht, ebenso wie für Antigravity, ohne dessen Verdikt kein Commit zustande kommt.

### 2.6 Marker-Kontrakt des Meta-Verfahrens

Für die Durchführung dieses Umbaus gelten folgende Marker. Ob die Zielimplementierung exakt diese Namen verwendet, ist eine Entscheidung im Plan (§7.6).

| Schritt | Marker |
|---|---|
| Jede Antwort, letzte nicht-leere Zeile | `STATUS: DONE` |
| Jede Reviewantwort, erste Zeile | `REVIEWER: claude` bzw. `REVIEWER: antigravity` |
| Codex Plan | `PLAN_READY: YES\|NO` |
| Review des Plans | `PLAN_APPROVAL: YES\|NO` |
| Codex Implementierung | `IMPLEMENTATION_READY: <slice-id> \| YES\|NO` |
| Review eines Slices | `SLICE_APPROVAL: <slice-id> \| YES\|NO` |
| Endreview | `FINAL_APPROVAL: YES\|NO` |
| Validierung (jede Instanz, die validiert) | `VALIDATION_RESULT: PASS\|FAIL \| <command> \| <exit code>` |
| Test-Riegel | `TEST_FILES_TOUCHED: NONE` oder `TEST_FILES_TOUCHED: <pfade>` |
| Abnahme einer Teständerung | `TEST_CHANGE_APPROVAL: YES\|NO \| <Begründung>` |
| Stop-Regel greift | `STOP_REQUESTED: <regel-id> \| <Begründung>` |
| Neues Finding | `NEW_FINDING: <ID> \| BLOCKER\|OBSERVATION \| <Beschreibung> \| <Akzeptanztest>` |
| Lebenszyklus eines Findings | `FINDING_STATUS: <ID> \| OPEN\|CLOSED \| <Begründung>` |
| Antwort des Implementierers auf ein Finding | `FINDING_RESPONSE: <ID> \| ACCEPTED\|REJECTED \| <Begründung>` |
| Pre-Mortem | `PRE_MORTEM: <wahrscheinlichste Fehlerursache in 3 Monaten>` |

Finding-IDs tragen ein Präfix je Quelle: `C-01` (Claude), `A-01` (Antigravity). 1-basiert, keine Wiederverwendung, Gültigkeit über Slices hinweg.

Konsistenzregeln:

- `*_APPROVAL: YES` nur, wenn kein Finding der Klasse `BLOCKER` offen ist. Offene `OBSERVATION`-Findings blockieren nicht, müssen aber dokumentiert bleiben.
- `*_APPROVAL: NO` nur, wenn mindestens ein `BLOCKER` offen ist.
- `*_APPROVAL: YES` nur bei `VALIDATION_RESULT: PASS` (Ausnahme Red-State, §2.5).
- Ist `TEST_FILES_TOUCHED` nicht `NONE`, ist `*_APPROVAL: YES` nur mit vorausgegangenem `TEST_CHANGE_APPROVAL: YES` zulässig.
- `FINDING_RESPONSE: REJECTED` schließt ein Finding **nicht** — es bleibt offen, bis der meldende Reviewer per `FINDING_STATUS: CLOSED` zustimmt oder es zur `OBSERVATION` herabstuft. Der Implementierer hat ein Widerspruchsrecht, kein Vetorecht.
- `STOP_REQUESTED` beendet den Schritt. Kein Marker der Freigabefamilie darf in derselben Antwort stehen.

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
| B-3 | **Rollentausch statt Erweiterung.** Heute plant Claude, Codex reviewt den Plan, Claude bestätigt. Das Zielbild dreht das um: Codex plant und implementiert, Claude und Antigravity reviewen. Das ist keine additive Änderung. | `orchestrator.py:624,641,678` |
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
| B-14 | **Der Wrapper ist plattformfragiler als dokumentiert.** `run_task:45` nutzt `${VAR,,}` (Bash 4+). macOS liefert Bash 3.2 als `/bin/bash`. Der zugesicherte macOS-Support ist bereits heute gebrochen — unabhängig von der Windows-Frage. | `run_task:45`; `README.md:23` |
| B-15 | **Stop-Regeln sind Prosa, kein Gate.** `AGENTS.md` wird als Text in jeden Prompt injiziert und bei 12 000 Zeichen abgeschnitten. Nichts parst die Stop-Regeln, nichts hält die Pipeline an, wenn eine greift. Ein „Stoppe und frage nach" ist heute eine Bitte an das Modell. | `orchestrator.py:161-170,248-266`; Referenz: `reference-target-repo-agents.md`, Abschnitt „Agent Stop Rules" |
| B-16 | **Genau ein Validierungsbefehl.** `--test-command` ist ein einzelner String. Das Zielrepo verlangt pfadabhängige Validierung: `npm test` immer, zusätzlich `npm run build:engine` nach Änderungen an `engine/`. Das lässt sich heute nicht abbilden. | `orchestrator.py:946-950`; `agent_runtime.py:179-209`; Referenz: `reference-target-repo-agents.md`, Abschnitt „Validierung" |
| B-17 | **Der Verlauf ist flüchtig.** Reviewhistorie liegt unter `.orchestrator/runs/…` und ist gitignored. Das manuelle Verfahren führt sie in committeten Slice-MDs — sie ist dort Prüfspur, nicht Wegwerfartefakt. | `.gitignore:1`; `orchestrator.py:50-56` |

---

## 4. Anforderungen

Prioritäten: **B** = Blocker (ohne dies läuft nichts), **H** = hoch, **M** = mittel.
Die Zuordnung zu Slices ist Aufgabe des Plans, nicht dieses Dokuments.

### R-1 (B) — Normalpfad reparieren, Fallback ersatzlos entfernen

Ein Lauf darf nicht daran scheitern, dass ein Agent fehlt, der für den aktuellen Schritt gar nicht gebraucht wird. Der Wrapper darf keinen Fallback erzwingen.

Weitergehend: **Der Fallback-Mechanismus zwischen Agenten entfällt ersatzlos.** Das ist keine Reparatur des bestehenden Verhaltens, sondern dessen Abschaffung. Begründung: Nach §2.1 sind die Rollen nicht austauschbar. Ein einspringender Vertreter würde eine Freigabe erteilen, für die er nicht vorgesehen ist — und im Fall von Antigravity ausgerechnet die schwächste Instanz an die Stelle der stärksten setzen. Fällt eine Instanz aus, hält der Lauf an (R-18).

*Abnahme:* Im Quelltext existiert kein Pfad mehr, der einen Agenten durch einen anderen ersetzt; `--allow-fallback-to-gemini` und die zugehörige Verdrahtung sind entfernt. Auf einer Maschine ohne Antigravity-Binary laufen Planung und Slice-Implementierung bis zu dem Schritt, an dem Antigravity gebraucht wird, und halten dort mit klarer Meldung an. `./run_task --dry-run` läuft vollständig durch.

### R-2 (B) — Adapter auf aktuellen Stand

Gemini-Adapter durch die Antigravity-CLI (`agy`) ersetzen: Kommandozeile, `required_hosts`, `extract_output`, `stream_filter`. Codex- und Claude-Adapter gegen die aktuellen Kommandozeilen prüfen. Modell und Timeout je Adapter konfigurierbar machen statt hartzukodieren.

*Abnahme:* Alle drei Adapter starten und liefern auf einen Minimalprompt eine parsbare Antwort mit korrektem Abschlussmarker. Kein Modellname mehr im Quelltext fest verdrahtet.

*Randbedingung:* Kein Eingriff in die Orchestrierungslogik in dieser Anforderung.

### R-3 (H) — Schreibrechte nach Rolle, nicht nach Erzählmuster

Die Rechtetrennung aus §2.1 ist technisch durchzusetzen: Reviewer dürfen **Dokumentation und Plan-/Slice-Dateien schreiben, aber keinen Code**. Die Durchsetzung erfolgt auf CLI-/Werkzeugebene, nicht per Textprüfung. Die bestehende Erzählmuster-Regex bleibt als Sekundärsignal, die beiden Duplikate werden zusammengeführt.

*Abnahme:* Ein Reviewer-Aufruf kann in `docs/` schreiben und scheitert beim Versuch, eine Quell- oder Testdatei zu ändern — und zwar an der Werkzeugkonfiguration, nicht erst an einer Textprüfung. Beide Validatorfunktionen existieren nur noch einmal.

### R-4 (H) — Pfadprüfung für Dateischnappschüsse

Vom Agenten gemeldete Pfade werden gegen erlaubte Wurzeln validiert, bevor sie gelesen werden — analog zu `_validate_loaded_path` in `state_io.py`.

*Abnahme:* Ein Bericht, der `../../etc/passwd` als geänderte Datei nennt, führt nicht dazu, dass diese Datei im Reviewprompt landet. Regressionstest vorhanden.

### R-5 (B) — Eine Diff-Quelle

Eine einzige Funktion liefert „geänderte Dateien seit Basis". Sie ist git-basiert, berücksichtigt unversionierte Dateien und funktioniert **nachdem** innerhalb des Laufs bereits committet wurde (Basis ist die Merge-Base des Feature-Branch, nicht der Arbeitsbaum). Der Bericht des implementierenden Agenten ist nur noch Zusatzsignal, nicht Primärquelle.

*Abnahme:* Test mit drei Zuständen: (a) uncommitted Änderung, (b) bereits committeter Slice plus neue Änderung, (c) neue unversionierte Datei. Alle drei werden korrekt gemeldet.

*Hinweis:* R-5 ist Voraussetzung für R-9, R-10 und R-15. Ohne sie sind alle drei unsicher.

### R-6 (B) — Kontrakt vereinheitlichen und erweitern

Jeder Review-Aufruf durchläuft denselben Validator. Zusätzlich zu vereinheitlichen bzw. neu aufzunehmen:

- **Finding-Klassen** `BLOCKER` und `OBSERVATION`. Nur Blocker verhindern die Freigabe; Observations müssen dokumentiert bleiben. Erst dadurch ist „keine Freigabe ohne Findings" mit „Freigabe nur ohne offene Findings" vereinbar.
- **Antwortkanal des Implementierers:** `FINDING_RESPONSE` mit `ACCEPTED`/`REJECTED` und Begründung. Ein zurückgewiesenes Finding bleibt offen, bis der meldende Reviewer zustimmt — Widerspruchsrecht, kein Vetorecht.
- **Finding-Record** statt Statusstring: Status, Klasse, Kurzbeschreibung, Akzeptanzkriterium, Herkunft (Slice, Runde, meldende Instanz), Antwort des Implementierers.
- **Quellenpräfixe** in den IDs (§2.6), 1-basiert.
- **Pre-Mortem** als Pflichtfeld vor jeder Freigabe.

*Abnahme:* Kein Agentenaufruf ohne Contract-Validierung. Ein Finding, das eröffnet, bestritten und später geschlossen wird, hat am Ende Beschreibung, Akzeptanzkriterium, Widerspruch und Schlussbegründung. Der Sonderfall-Override entfällt oder wird zur generischen Regel.

*Hinweis:* R-6 ist Voraussetzung für R-7.

### R-7 (H) — Rollentausch und asymmetrische Reviewkette

Antigravity wird aus der Fallback-Rolle in eine eigenständige, aber eng begrenzte Reviewrolle überführt. Die Rollen werden gemäß §2.1 neu verteilt.

Die Reviewkette ist **asymmetrisch** und muss im Orchestrator auch so abgebildet sein: Claude Code wird in jeder Runde aufgerufen, Antigravity ausschließlich auf einem Stand, den Claude bereits freigegeben hat, und dort genau einmal. Ein Aufruf von Antigravity innerhalb der Korrekturschleife ist ein Fehler, kein Sonderfall.

Freigabe nur, wenn **alle** vorgesehenen Freigaben vorliegen und kein Blocker offen ist. Fehlendes oder unparsbares Verdikt zählt als Ablehnung.

*Abnahme:* In einem Lauf, in dem Codex zweimal nachbessern muss, wird Claude dreimal und Antigravity einmal aufgerufen — nachweisbar über die Laufartefakte. Antigravity wird nie vor einer Claude-Freigabe aufgerufen. Ein fehlendes Verdikt führt zur Ablehnung des Schritts, nicht zu dessen Überspringen.

### R-8 (H) — Slice-Modell

Das Phasenmodell wird durch das Slice-Modell ersetzt. Der Arbeitsplan enthält die Slice-Liste, der Zustand führt den aktuellen Slice mit. Erforderlich sind:

- State-Schema in neuer Version mit **expliziter Migration**; ein unbekanntes Schema führt zu einem lauten Fehler, nicht zu einem stillen Neustart,
- Checkpoint-Namensschema, das Slices unterscheidet,
- Iterationszähler je Slice mit definiertem Abbruchzustand und dokumentiertem Exitcode — kein durchfliegender Traceback,
- Kontextregel umgedreht: der Plan wird destilliert weitergereicht, die Historie darf großzügiger sein,
- 1-basierte Nummerierung durchgängig.

*Abnahme:* Ein Lauf über mehrere Slices ist unterbrechbar und mit `--resume` fortsetzbar, ohne dass Slice-Fortschritt verlorengeht. Ein alter State wird migriert oder mit klarer Meldung abgelehnt. Der Abbruch bei erreichtem Iterationsdeckel liefert den dokumentierten Exitcode.

### R-9 (H) — Git-Integration

Feature-Branch beim Planen (Namensmuster `feature/<kurzname>` oder `codex/<kurzname>`), lokaler Commit nach jeder Slice-Freigabe. **Der Commit folgt mechanisch aus der Freigabe und ist keine zweite Entscheidung.** Vor jedem Commit die Sicherheitsprüfung aus §2.3 Schritt 6: Dateiliste gegen Slice-Scope, unerwartete Dateien blockieren.

Vor dem ersten Edit eines Slice: Branch-Check und Statuscheck, dokumentiert in der Slice-MD; Branch-Abweichung ist eine Stop-Bedingung.

Push und Merge bleiben manuell und benötigen ausdrückliche Nutzerfreigabe. Die Wechselwirkung mit dem Preflight und mit dem Watch-Modus ist zu klären und im Plan zu beschreiben.

*Abnahme:* Nach *n* freigegebenen Slices existieren *n* Commits auf dem Feature-Branch, jeder mit nachvollziehbarer Zuordnung zu Slice und Freigaben. Der Reviewprompt des Slices *n+1* zeigt einen korrekten Diff (R-5). Eine unerwartete Datei im Arbeitsbaum verhindert den Commit nachweislich.

### R-10 (H) — Test-Riegel

Vor jedem Review wird geprüft, ob Testdateien im Diff liegen. Falls ja, hält die Pipeline mit definiertem Zustand an und verlangt eine ausdrückliche Abnahme der Teständerung. Bewusst grob halten — lieber zu oft anhalten als einmal zu wenig. Der Freigabepfad muss **ohne Neustart des Laufs** funktionieren.

*Abnahme:* Eine neu angelegte, unversionierte Testdatei löst den Riegel aus (nicht nur eine geänderte bestehende). Nach erteilter Abnahme läuft derselbe Lauf weiter.

*Hinweis:* Dieses Repo besteht zu einem großen Teil aus Testcode. Der Riegel wird beim Selbstumbau ständig auslösen. Das ist gewollt und macht den Freigabepfad zur Kernfunktion, nicht zum Randfall.

### R-11 (M) — Gesamtabnahme

Nach dem letzten Slice ein Review durch alle drei Instanzen über den Gesamtdiff des Branches gegen `main`, mit den Prüfgegenständen aus §2.4. Ein Fehler führt zurück in die Implementierungsphase.

*Abnahme:* Das Endreview arbeitet nachweislich auf dem Branch-Diff, nicht auf dem letzten Slice. Ein dort gefundenes Finding führt zu einem regulären Slice-Durchlauf und danach zu einem erneuten Endreview.

### R-12 (M) — Dry-Run mit skriptbarem Verdikt

Der Dry-Run muss auch Ablehnungen, rote Validierungen, ausgelöste Test-Riegel, greifende Stop-Regeln und erreichte Iterationsdeckel simulieren können.

*Abnahme:* Jedes Gate aus §2.5 ist ohne echten Agentenaufruf verdrahtungstestbar.

### R-13 (M) — Plattform

Die Plattformfrage ist zu entscheiden (§7.1) und umzusetzen. Der Ist-Zustand — Bash-4-Syntax bei zugesichertem macOS-Support — ist in jedem Fall zu bereinigen.

*Abnahme:* Die README beschreibt die tatsächlich unterstützten Plattformen, und der Wrapper läuft auf allen davon.

### R-14 (M) — Dokumentation nachziehen

`README.md`, `AGENTS.md`, `CLAUDE.md`, `CODEX.md`, `workflow.puml` und `example-task.md` beschreiben nach dem Umbau das tatsächliche Verfahren. `GEMINI.md` wird zur Rollendatei der abschließenden Reviewinstanz (Umbenennung und Inhalt im Plan festlegen). Die Review-Grundsätze aus dem manuellen Verfahren — adversariale Haltung, fünf Prüfdimensionen, keine Freigabe ohne Findings, Bewertung erst nach Analyse, Pre-Mortem — gehören in die `AGENTS.md` dieses Repos; heute steht dort nur „Review the plan for gaps".

*Abnahme:* Kein Dokument beschreibt ein Zwei-Phasen-Modell oder einen Gemini-Fallback mehr. Die Marker-Tabellen in README und AGENTS.md stimmen mit dem Parser überein. Der bestehende Konsistenztest über die Instruktionsdateien bleibt grün.

### R-15 (H) — Stop-Regeln als Gate

Stop-Regeln müssen maschinell wirken, nicht nur im Prompt stehen. Erforderlich:

- ein Marker (`STOP_REQUESTED`), der die Pipeline mit definiertem Zustand anhält,
- eine **generische** Stop-Regel, die der Orchestrator selbst auswertet: Überschreitung der Dateigrenze je Slice (§5),
- ein Mechanismus, über den das **Zielrepo eigene, fachliche Stop-Regeln deklariert** (im Zielrepo-Beispiel: Engine-Semantik, FlowDelta, Snapshot-Abweichungen). Diese kann der Orchestrator nicht selbst prüfen, aber er muss sie den Agenten als benannte, referenzierbare Regeln vorlegen und ein Stoppen darauf akzeptieren.

*Abnahme:* Ein Slice, der die Dateigrenze überschreitet, hält an, ohne dass ein Agent das melden muss. Ein von einem Agenten gemeldetes `STOP_REQUESTED` hält den Lauf an und wird nicht als Ablehnung mit Retry behandelt.

### R-16 (M) — Pfadabhängige Validierung

Ein einzelner Testbefehl reicht nicht. Der Orchestrator muss eine Validierungsmatrix unterstützen: ein Standardbefehl plus zusätzliche Befehle, die greifen, wenn bestimmte Pfade im Diff liegen. Unvollständige Läufe müssen als solche berichtet werden.

*Abnahme:* Eine Konfiguration „`npm test` immer, `npm run build:engine` zusätzlich bei Änderungen unter `engine/`" ist abbildbar, und die Zusatzvalidierung läuft nachweislich nur bei betroffenem Diff.

### R-17 (M) — Prüfspur committen

Plan- und Slice-Dokumente inklusive Reviewfeedback, Antworten und Entscheidungstabelle liegen unter `docs/internal/` und werden mit dem jeweiligen Slice committet. Das Verhältnis zu den flüchtigen Artefakten unter `.orchestrator/` ist zu klären (§7.9) — zwei parallele Aufzeichnungen desselben Vorgangs sind zu vermeiden.

*Abnahme:* Nach Abschluss lässt sich allein aus dem Git-Verlauf nachvollziehen, wer was wann bemängelt, bestritten und freigegeben hat.

### R-18 (B) — Ausfall einer Instanz als definierter Halt

Eine erschöpfte Quota ist kein Fehler, der wegzufangen wäre, sondern ein regulärer Endzustand des Laufs. Dasselbe gilt für jeden anderen Ausfall einer Instanz. Erforderlich:

- Erkennung je Agent, unterscheidbar von sonstigen Aufruffehlern — eine Quotagrenze ist etwas anderes als ein Absturz und muss anders gemeldet werden,
- Halt mit definiertem Zustand: der laufende Slice bleibt unvollendet, bereits committete Slices bleiben unangetastet, am Arbeitsbaum wird nichts zurückgesetzt,
- dokumentierter Exitcode statt durchfliegendem Traceback (behebt zugleich B-11),
- eine Meldung, die benennt, welche Instanz ausgefallen ist, in welchem Slice und in welchem Schritt,
- Fortsetzbarkeit über `--resume` ohne Verlust des Slice-Fortschritts (setzt R-8 voraus).

Der Orchestrator wartet nicht, versucht es nicht erneut und weicht nicht auf eine andere Instanz aus. Der Wiederanlauf ist eine Nutzerentscheidung.

*Abnahme:* Je ein simulierter Quotaausfall bei Codex, bei Claude Code und bei Antigravity führt zum dokumentierten Exitcode und hinterlässt einen Zustand, aus dem `--resume` denselben Slice sauber fortsetzt. In keinem der drei Fälle wird eine Ersatzinstanz aufgerufen. Der Fall ist im Dry-Run simulierbar (R-12).

*Hinweis:* R-18 hängt an R-8 (Slice-State und `--resume`) und ersetzt gemeinsam mit R-1 den bisherigen Fallback-Mechanismus.

---

## 5. Slice-Regeln

- Ein Slice ist die kleinste Einheit, die **eigenständig freigebbar und committebar** ist.
- Obergrenze: höchstens **eine kohärente Verhaltensänderung** je Slice.
- Nach jedem Slice muss ein **lauffähiger Stand** existieren: Validierung grün, Orchestrator startbar.
- **Dateigrenze:** höchstens 10 geänderte **produktive Programmdateien** je Slice. Reine Dokumentation (`.md`) und Testdateien zählen **nicht** mit. Überschreitung ist eine Stop-Bedingung (R-15), kein stiller Abbruch. *(Übernommen aus den Stop-Regeln des Zielrepos; dort auf `.js`, `.mjs`, `.html`, `.css`, `.rs`, `.json` bezogen — hier sinngemäß `.py` und Konfigurationsdateien.)*
- **Red-State-Regel:** Ein bewusst roter Contract-Slice ist als temporärer Zustand zulässig, muss aber eine namentlich benannte Folge-Slice haben, die ihn grün macht. Solange ein erwarteter roter Test existiert, darf kein fachlich unabhängiger Slice begonnen werden.
- Abhängigkeiten zwischen Slices sind im Plan explizit zu machen. R-5 vor R-9, R-10 und R-15; R-6 vor R-7; R-8 vor R-18. Weitere Abhängigkeiten ermittelt der Plan.
- Ein Slice, der Testdateien anfasst, ist als solcher zu kennzeichnen — er läuft in den Test-Riegel (R-10).
- Nummerierung 1-basiert, keine `00`-Suffixe.

**Zur Reihenfolge:** So wählen, dass nach jedem Slice ein lauffähiger Stand existiert. R-1 gehört an den Anfang: es ist Reparatur, nicht Modernisierung, und ohne sie ist der Rest nicht erprobbar.

---

## 6. Randbedingungen und Invarianten

### 6.1 Nicht verhandelbar

- **Die 92 bestehenden Tests bleiben grün.** Jede Verhaltensänderung bringt ihren eigenen Test mit.
- **`.orchestrator/state.json` und Checkpoint-Dateien werden nie von Hand editiert.**
- **Keine destruktiven Git-Kommandos ohne ausdrückliche Freigabe:** kein `rm -rf`, kein Hard Reset, kein History-Rewrite, kein Force Push, kein Löschen entfernter Branches. Das gilt auch für den in den Referenzregeln erwähnten Rollback per `git reset --hard HEAD` — die `AGENTS.md` des Zielrepos hat bei Widerspruch Vorrang und verlangt dort eine ausdrückliche Freigabe. Der reguläre Rollback ist `git checkout -- <datei>`.
- **Keine Secrets, Tokens oder lokalen Pfade** in committeten Dateien.
- **Push und Merge sind Nutzerentscheidungen**, niemals automatisch.

### 6.2 Validierung

Verbindlicher Standardbefehl in diesem Repo: `python3 -m pytest tests/ -v`

Jede Instanz, die laut §2 validiert, führt den Befehl selbst aus und berichtet `VALIDATION_RESULT` mit Befehl und Exitcode. Ein von einer anderen Instanz übernommenes Ergebnis ist keine eigene Validierung. Lief nicht die vollständige Suite, ist das ausdrücklich zu berichten.

Bei Änderungen an Architektur, Modulzuschnitt oder Nutzer-Workflow sind die betroffenen Referenzdokumente mitzuziehen (Dokumentations-Sync, Prüfgegenstand im Endreview).

### 6.3 Sprachregel

Das Repo erzwingt Englisch per Test (`tests/test_language_consistency.py`) für den Inhalt von `src/**/*.py`, `tests/**/*.py`, `README.md`, `run_task`, `example-task.md` sowie für **Dateinamen im Wurzelverzeichnis**.

Daraus folgt:

- Quellcode, Kommentare, Marker, Testnamen, Commit-Messages und Dateinamen: **Englisch**.
- Arbeitsdokumente unterhalb von `docs/internal/`: Deutsch zulässig (dieses Dokument ist das Beispiel).
- Neue Dateien im Wurzelverzeichnis brauchen englische Namen.
- Wer den Sprachtest anfasst, fasst einen Test an — Test-Riegel (R-10).

### 6.4 Stop-Regeln

Es gelten die Stop-Bedingungen aus §2.5 und §5 sowie die des jeweiligen Zielrepos. Für diesen Umbau konkret:

1. Mehr als 10 produktive Programmdateien in einem Slice.
2. Validierung nicht ausführbar oder nicht sinnvoll ersetzbar.
3. Ein Contract ist unklar.
4. Aktiver Branch passt nicht zum im Plan definierten Feature-Branch.
5. Der Diff-Risiko-Block zeigt, dass eine der Bedingungen 1–4 greift.

Eine Stop-Bedingung führt zur Rückfrage, nicht zur Eigeninterpretation.

### 6.5 Vorrang bei Widersprüchen

`AGENTS.md` des jeweiligen Repos hat Vorrang vor Slice-Regeln und vor diesem Dokument. Slice-Regeln dürfen Stop-Regeln konkretisieren, nicht abschwächen. Widersprüche zwischen diesem Dokument und den Referenzdokumenten sind im Plan zu benennen und aufzulösen, nicht stillschweigend zu überschreiben.

### 6.6 Kontraktstabilität

Solange R-6 und R-7 nicht umgesetzt sind, bleibt der bestehende Marker-Kontrakt aus `AGENTS.md` gültig. Marker-Semantik ändert sich nur zusammen mit `src/prompts.py`, `src/orchestrator.py` und den Instruktionsdateien im selben Slice.

---

## 7. Zu treffende Entscheidungen

Der Plan muss jeden offenen Punkt **entscheiden und begründen** — eine Entscheidung ohne Begründung ist ein Finding. Punkte, die durch das manuelle Verfahren bereits beantwortet sind, stehen als entschieden markiert; sie umzustoßen erfordert eine ausdrückliche Begründung.

### 7.1 Plattform — **offen**

Die README schließt Windows aus und sichert macOS zu — letzteres ist bereits gebrochen (B-14). Optionen: WSL vorschreiben, Bash-Abhängigkeiten entfernen, oder den unterstützten Plattformkreis ehrlich verkleinern.

*Empfehlung:* Wrapper nach Python ziehen. Löst macOS und Windows in einem Zug und entfernt die Doppelpflege zwischen `run_task` und `orchestrator.py`.

### 7.2 Wer committet — **entschieden: Antigravity**

Der abschließende Reviewer führt den lokalen Commit als Abnahmehandlung aus, mit vorheriger Sicherheitsprüfung gegen den Slice-Scope. Codex committet nie. So steht es in den Referenzregeln und so ist es vorgegeben.

*Mit Revision 3 ausdrücklich bestätigt:* Antigravity behält das Commit-Recht, obwohl seine Reviewbeteiligung deutlich zurückgenommen wurde. Der Commit hängt damit an der Instanz, die je Slice genau einmal prüft — was zugleich sicherstellt, dass jeder Commit einen Kontrollblick von außerhalb des Codex-Claude-Zyklus erhalten hat.

*Offen bleibt:* Wie der Commit technisch zustande kommt — Antigravity mit eng begrenztem Git-Recht, oder der Orchestrator führt ihn mechanisch auf Antigravitys Freigabe hin aus. Der zweite Weg hält R-3 sauber (Reviewer schreibt keinen Code, und ein Commit ist ein Schreibvorgang auf Code), der erste ist näher am manuellen Verfahren. Der Plan entscheidet.

### 7.3 Slice-Maß — **entschieden: 10 produktive Programmdateien**

Doku und Tests zählen nicht mit (§5). Der maßgebliche Schnitt bleibt fachlich — eine kohärente Verhaltensänderung —, die Dateigrenze ist die maschinell prüfbare Stop-Bedingung darüber.

*Offen bleibt:* die genaue Dateiendungsliste für dieses Repo.

### 7.4 Ping-Pong-Bremse — **offen**

Welches N? Was passiert beim Abbruch mitten in einem Slice: Branch behalten, WIP committen, verwerfen? Welcher Exitcode? Wichtig ist nur, dass der Zustand nach Abbruch definiert und dokumentiert ist.

### 7.5 Erwartungswerte bei Rechenkernen — **offen**

Schreibt derselbe Agent Code und Tests aus einer Interpretation, sind beide bei einem Missverständnis einträchtig falsch und trotzdem grün. Gegenmittel: fachliche Ankerwerte im Arbeitsplan, von Hand gesetzt.

*Empfehlung:* Als eigener Kontraktblock (`ANCHOR: <id> | <input> | <expected>`), den der Reviewer prüfen **muss**. Als Prosa gehen Ankerwerte in der Kontextbeschneidung verloren — genau der Fehlermodus, den sie verhindern sollen.

*Abgrenzung:* Betrifft die Zielarchitektur für Rechenkerne. Dieses Repo ist Infrastruktur und hat keine Rechenkerne; die Entscheidung ist zu treffen, hier aber nicht anzuwenden.

### 7.6 Marker-Namensraum — **offen**

Eigene Marker je Agent oder ein generischer Marker mit Instanz-ID? Rückwärtskompatibilität zu `PHASE1_APPROVAL`/`PHASE2_APPROVAL` und den Legacy-Markern: behalten oder sauber schneiden?

*Empfehlung:* Generisch mit Instanz-ID, Finding-IDs mit Quellenpräfix wie im manuellen Verfahren (`C-01`, `A-01`). Legacy-Marker beim Schnitt auf das Slice-Modell entfernen statt mitzuschleppen.

### 7.7 Migrationsweg — **offen**

Umbau im Bestand oder Neuschnitt mit Übernahme von `state_io.py`, Kontraktschicht und Watcher?

*Empfehlung:* Umbau im Bestand. `state_io.py`, `prompts.py` und `agent_adapters.py` sind sauber getrennt und tragfähig; die Kopplung sitzt allein in `orchestrator.py` (1 197 Zeilen). Ein Neuschnitt kostet die 92 Tests ohne Gegenwert.

*Nebenbei:* `run_phase1` und `run_phase2` sind strukturell fast identisch (Checkpoint → Agent → Contract-Parse → History-Merge → Gate). Das Slice-Modell ist die Gelegenheit, daraus **eine** Runde zu machen statt zwei parallele Kopien zu pflegen. Ob eigener Slice oder Teil von R-8, entscheidet der Plan.

### 7.8 Der Mensch als Gate — **offen, Annahme getroffen**

Die Referenzregeln sehen den Nutzer als Teil der Freigabe vor („Review von Gemini **und dem Nutzer**"). Die Vorgabe für diesen Umbau nennt nur die drei KI-Instanzen.

*Vorläufige Annahme:* Der Mensch ist Gate bei Push und Merge, nicht vor jedem Slice-Commit. Der Plan hat diese Annahme entweder zu bestätigen oder eine Option für einen zuschaltbaren Nutzer-Gate vorzusehen.

### 7.9 Verhältnis Slice-Dokumente zu Laufartefakten — **offen**

Das manuelle Verfahren führt die Prüfspur in committeten Slice-MDs. Der Orchestrator führt sie zusätzlich unter `.orchestrator/runs/` (gitignored). Zwei parallele Aufzeichnungen desselben Vorgangs sind Pflegeaufwand und Divergenzrisiko.

*Zu entscheiden:* Slice-MD als Primärquelle und `.orchestrator/` nur noch als Laufzeitzustand, oder Generierung der Slice-MD aus den Laufartefakten beim Commit.

---

## 8. Nicht-Ziele

Ausdrücklich nicht Gegenstand dieses Umbaus:

- Automatischer Push oder Merge nach `main`.
- Ausführung auf mehreren Repos parallel.
- Web-Oberfläche, Dashboard oder Fortschrittsanzeige über Logs hinaus.
- Kostensteuerung, Token-Budgets, Modell-Auswahlheuristiken.
- Unterstützung weiterer Agenten-CLIs über die drei genannten hinaus.
- Rückwärtskompatibilität des State-Formats über die eine dokumentierte Migration hinaus.
- Anpassung der Instruktionsdateien des Zielrepos (Ruhestandsuite).

---

## 9. Liefergegenstände

### 9.1 Arbeitsplan-MD

Ein Dokument unter `docs/internal/` mit englischem Dateinamen (§6.3). Aufbau:

1. **Zielbild in eigenen Worten** — kurz. Dient dem Abgleich, ob die Aufgabe verstanden wurde, nicht der Wiederholung dieses Dokuments.
2. **Feature-Branch und GitHub-Status.** Der Branch ist lokal; die Veröffentlichung steht aus und erfolgt erst auf Nutzerfreigabe.
3. **Entscheidungen** — jeder Punkt aus §7, entschieden und begründet. Abweichungen von Empfehlungen und von den als entschieden markierten Punkten sind ausdrücklich zu kennzeichnen.
4. **Slice-Liste.** Je Slice: ID (1-basiert) und Titel, Zweck in einem Satz, abgedeckte Anforderungen (`R-x`), betroffene Dateien, prüfbare Akzeptanzkriterien, geplante Tests, Abhängigkeiten, Risiko und Rückfalloption, Kennzeichen für Test-Riegel und Red-State.
5. **Reihenfolge und Abhängigkeitsgraph.** Mit Begründung, warum nach jedem Slice ein lauffähiger Stand existiert.
6. **Abdeckungsmatrix** — jede Anforderung R-1 bis R-18 einem oder mehreren Slices zugeordnet. Eine nicht zugeordnete Anforderung ist zu begründen, nicht zu übergehen.
7. **Migration und Rollback** — insbesondere State-Schema (R-8) und Branch-Strategie (R-9).
8. **Testplan** — was je Slice geprüft wird und wie die Gates aus §2.5 selbst getestet werden.
9. **Offene Fragen** — was Codex ohne Rückfrage nicht entscheiden kann.
10. Am Ende die Sektionen für den Reviewzyklus: `## Review-Feedback von Claude`, `## Review-Feedback von Antigravity`, `## Review-Antworten von Codex`.

**Formale Anforderungen:** Marker gemäß §2.6 (`PLAN_READY`, Findings, letzte Zeile `STATUS: DONE`). Kein Code, keine Änderung an bestehenden Dateien.

### 9.2 Slice-MDs

Je Slice eine eigene Datei unter `docs/internal/`, angelegt **vor Beginn der Arbeiten** am jeweiligen Slice, verlinkt aus der Arbeitsplan-MD. Namensmuster analog zum Zielrepo, englisch: `slice-<thema>-<nummer>-<kurztitel>.md`, 1-basiert.

Mindestinhalt: Feature-Branch und GitHub-Status; Ziel des Slice; Akzeptanzkriterien (fachlich und funktional, unabhängig von reinen Unit-Tests); Scope und Nicht-Scope; Diff-Risiko-Block inklusive dokumentiertem Branch- und Statuscheck; geplante Tests; durchgeführte Änderungen; ausgeführte Validierung mit Ergebnis; Abweichungen vom Plan; offene Risiken; Reviewsektionen; Entscheidungstabelle; Rückdokumentation in die Arbeitsplan-MD; Freigabestatus.

Entscheidungstabelle am Ende jeder Slice-MD:

| ID | Quelle | Finding | Klasse | Entscheidung | Umsetzung |
|---|---|---|---|---|---|
| C-01 | Claude | … | BLOCKER / OBSERVATION | angenommen / bestritten | erledigt / Begründung |

---

## 10. Abnahmekriterien für den Arbeitsplan

Die Reviewer prüfen gegen diese Liste. Ein nicht erfülltes Kriterium ist ein Finding.

1. Jede Anforderung R-1 bis R-18 ist einem Slice zugeordnet oder begründet zurückgestellt.
2. Jede Entscheidung aus §7 ist getroffen und begründet; Abweichungen von als entschieden markierten Punkten sind ausdrücklich begründet.
3. Die vorgegebenen Abhängigkeiten sind eingehalten: R-1 zuerst, R-5 vor R-9/R-10/R-15, R-6 vor R-7, R-8 vor R-18.
4. Nach jedem Slice existiert nachweislich ein lauffähiger Stand.
5. Kein Slice enthält mehr als eine kohärente Verhaltensänderung; kein Slice überschreitet die Dateigrenze ohne Stop-Vermerk.
6. Jeder Slice hat prüfbare Akzeptanzkriterien — „funktioniert korrekt" ist keins.
7. Slices, die Tests anfassen, sind gekennzeichnet; Red-State-Slices haben eine benannte Folge-Slice.
8. Die Sprachregel (§6.3) ist berücksichtigt.
9. Widersprüche zu den Referenzdokumenten sind benannt und aufgelöst, nicht überschrieben.
10. Der Plan enthält keinen Code und keine Änderung an bestehenden Dateien.
11. Der Feature-Branch existiert, ist benannt, und sein GitHub-Status ist als „lokal, Veröffentlichung ausstehend" dokumentiert.
