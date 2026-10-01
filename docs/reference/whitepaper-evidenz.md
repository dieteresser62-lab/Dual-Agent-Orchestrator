# Architektur-Whitepaper A–C – Evidenzindex

Ausgabe 2.2, 1. Oktober 2026. [Whitepaper](architektur-whitepaper.pdf) · [Bearbeitbares Quellpaket](whitepaper-quellen.zip)

Die Ausgabe dokumentiert den nach Phase C veröffentlichten Stand. Interne Versionsnummern stehen in diesem Index, damit der Lesetext auf Architektur und Ergebnisse konzentriert bleibt. Die Phase-A-Ausgabe 1.3 und ihr eigener Evidenzindex bleiben als historische Veröffentlichung unverändert erhalten.

## 1. Versionsrahmen

- Veröffentlichter Code: `c472d2196b347b4b40f428d32d21d593692f99a4`; Git-Tree: `6798faa4a2df9748fb8dcb38c698ae468d217be7`.
- Standardbesetzung: Codex / Claude / Claude. Experimentell zugelassen und im Gesamtworkflow erprobt: Claude / Codex / Codex. Die Reihenfolge bezeichnet Implementer / Reviewer / Finalreviewer.
- Basis der neuen Live-Nachweise: Linux unter WSL 2 / Ubuntu / ext4. Daraus wird keine native Windows-Zulassung abgeleitet.
- Die einzelnen Qualifikationen, Paketläufe, Topologieläufe und Suiten sind zeitlich und technisch getrennte Nachweise. Sie werden nicht zu einer einzigen Messung am Veröffentlichungscommit zusammengezogen.
- Im Rahmen dieser Publikation wurden vorhandene Artefakte gelesen und die Codezeilen neu gezählt. Es wurde keine vollständige Validierungsmatrix oder neue Orchestrator-Attestierung erzeugt.

## 2. Aussagen und nachprüfbare Quellen

| PDF | Aussage | Maßgebliche Quelle am Veröffentlichungsstand |
|---|---|---|
| S. 2–5 | Rollen, Finding-Eigentum, Commitbedingungen, Finalreview und Folgeauftrag | [AGENTS.md](https://github.com/dieteresser62-lab/Dual-Agent-Orchestrator/blob/c472d2196b347b4b40f428d32d21d593692f99a4/AGENTS.md) |
| S. 6 | Zulassung je Slot, Herstellertrennung, experimentelle neue Rollen | [Zulassungstabelle](https://github.com/dieteresser62-lab/Dual-Agent-Orchestrator/blob/c472d2196b347b4b40f428d32d21d593692f99a4/schemas/role-provider-certifications-v1.json) |
| S. 7–9 | Providerprojektion, v3-Verträge, Records, Side Effects und Resume | [Native Schema-Projektion](https://github.com/dieteresser62-lab/Dual-Agent-Orchestrator/blob/c472d2196b347b4b40f428d32d21d593692f99a4/src/native_provider_schema.py), [Artefaktmodell](https://github.com/dieteresser62-lab/Dual-Agent-Orchestrator/blob/c472d2196b347b4b40f428d32d21d593692f99a4/src/artifact_models.py), AGENTS.md |
| S. 10 | Codex-Qualifikation und Ausnahmen | [Reviewer-Qualifikation](https://github.com/dieteresser62-lab/Dual-Agent-Orchestrator/blob/c472d2196b347b4b40f428d32d21d593692f99a4/docs/reference/reviewer-certification.md), [öffentliche Codex-Evidenz](https://github.com/dieteresser62-lab/Dual-Agent-Orchestrator/blob/c472d2196b347b4b40f428d32d21d593692f99a4/docs/evidence/codex) |
| S. 10 | Claude-Paket, W1–W8 und Zulassung | [Implementer-Qualifikation](https://github.com/dieteresser62-lab/Dual-Agent-Orchestrator/blob/c472d2196b347b4b40f428d32d21d593692f99a4/docs/reference/implementer-certification.md), [öffentliche Claude-Evidenz](https://github.com/dieteresser62-lab/Dual-Agent-Orchestrator/blob/c472d2196b347b4b40f428d32d21d593692f99a4/docs/evidence/claude) |
| S. 11 | Erster echter Rollenwechsel | [Topologieprotokoll](https://github.com/dieteresser62-lab/Dual-Agent-Orchestrator/blob/c472d2196b347b4b40f428d32d21d593692f99a4/docs/evidence/claude/topology-run-v1.json) |
| S. 11 | Folgeauftrag über mehrere Stände | [Folgeprotokoll](https://github.com/dieteresser62-lab/Dual-Agent-Orchestrator/blob/c472d2196b347b4b40f428d32d21d593692f99a4/docs/evidence/claude/topology-followup-v1.json) |
| S. 12 | Codeumfang und Testprotokolle | Zählmethode und archivierte Dateien in Abschnitt 5 und 6 |
| S. 13 | Quota-Wartezeiten und transiente Überlastung | [Runtime-Policies](https://github.com/dieteresser62-lab/Dual-Agent-Orchestrator/blob/c472d2196b347b4b40f428d32d21d593692f99a4/src/agent_runtime.py), [Fehlerfortsetzung](https://github.com/dieteresser62-lab/Dual-Agent-Orchestrator/blob/c472d2196b347b4b40f428d32d21d593692f99a4/src/workflow_failure_recording.py) |
| S. 14 | Experimentelle AGY-Grenzen | [Antigravity-Referenz](https://github.com/dieteresser62-lab/Dual-Agent-Orchestrator/blob/c472d2196b347b4b40f428d32d21d593692f99a4/docs/reference/antigravity-reviewer.md) |

Die externen Primärquellen zu CLI-Anmeldung, ESAA, LangGraph und Evaluator-Optimizer sind im PDF vollständig betitelt und verlinkt. Abrufdatum: 01.10.2026. ESAA: *ESAA: Event Sourcing for Autonomous Agents in LLM-Based Software Engineering*, Elzo Brito Dos Santos Filho, arXiv:2602.23193.

## 3. Qualifikation: Ergebnisse und erhaltene Einschränkungen

Die Codex-Messung verwendet CLI 0.159.2 und `gpt-6.1-sol` / `high`. Schutz- und Formatproben P1–P6 und F1–F6 bestehen. Die Transportreihe besteht mit 12/12. Im kleinen Qualitätskorpus werden 4/4 Sollbefunde einschließlich 2/2 kritischer Befunde erkannt; keine Fehlalarme auf den beiden sauberen Fällen Q5 und Q6 und keine erfundenen kritischen Befunde. Die Claude-Referenz meldete einen unbegründeten Befund auf Q6 und erfüllt die Qualitätsregel trotzdem. Diese Zahlen sind kein breiter Benchmark.

Die beiden Blind-Rater gehören nicht zum OpenAI-Kandidatenhersteller. Einer gehört jedoch zum Hersteller der Claude-Referenz; die dokumentierte Neutralitätsausnahme begrenzt einen unabhängigen Vergleich beider Hersteller. Die neuere Protokollregel prüft beide Hersteller, macht die ältere Messung aber nicht nachträglich neutraler.

Die 512-Findings-Probe endet korrekt am Ausgabelimit, liefert aber nicht die vollständigen 512 Findings. Die entsprechende ausdrückliche Ausnahme macht diesen Teil nicht zu einem bestandenen Vollständigkeitstest. Der 128er-Fall gelang nach zwei Timeouts und einer Vertragskorrektur.

Das Claude-Paket verwendet CLI 2.1.286 und das konfigurierte Modell `opus` / `high`. Es besteht nach der festgelegten Regel mit 5/6 Aufgaben und null Ausschlussfehlern. T5 bleibt formal negativ: Resume liefert ein Implementierungsergebnis und bestandene verdeckte Tests; das nachfolgende Review endet im Timeout. Der historische Paketbericht entstand mit der autorisierten Kandidaten-Gate-Kopie von `4ed4b29`; er enthält noch keine eigene vollständige Codebindung. Diese historische Herkunft wird nicht durch die spätere reguläre Slot-Zulassung ersetzt.

Bei W1–W8 sind sieben Fälle live sicher gemessen, fünf formal bestanden. W2 und W8 sind mit Befund akzeptiert; W4 ist wegen Providerverweigerung live nicht gemessen. Seine Offline-Gegenprobe ersetzt die fehlende Live-Messung nicht. Der Implementer-Canary und beide Codex-Review-Canaries bestehen. Die endgültigen Einträge bleiben **experimental**.

Die im Papier genannten Adapterprobleme sind Entwicklungsbefunde: Writer-Projektion für geschlossene Objektvarianten und Literaltypen, ignorierte fehlerhafte Claude-Settings sowie weitere falsche Ablehnungen im realen Follow-up. Sie sind nicht als gleichzeitig offene Defekte des Veröffentlichungsstands formuliert. Belege liegen in Qualifikationsdokumenten, Protokollen und der zugehörigen Commit-Historie, insbesondere `6084e05`, `1fcec58`, `4ed4b29` und den nachfolgenden Topologie-Korrekturen.

### Ablehnungsregel des Claude-Implementers

Die Implementierung in `src/permission_policy.py` klassifiziert verweigerte Werkzeugaktionen. Ein verweigerter Aufruf beendet den Lauf nicht pauschal. Erkennbare Schreibversuche außerhalb von Repository und Scratch, geschützte Schreibzugriffe, Credential-Leseversuche, nicht lesende Git-Aufrufe, indirekte Befehlsausführung sowie ungültige, ungebundene oder nicht sicher inspizierbare Eingaben stoppen in den jeweiligen Regelklassen. Bestimmte nicht auflösbare Schreibziele erhalten dagegen ausdrücklich `opaque-unknown-target` und werden toleriert. „Nur sichtbare Grenzverstöße stoppen“ wäre als Zusammenfassung zu weitgehend.

Diese Toleranz erteilt keine Werkzeugberechtigung: Sandbox und geschützte Mounts setzen die Schreibgrenze durch, der Schutzbaum-Fingerprint erkennt Änderungen geschützter Inhalte. Der Adapter speichert Regelkennung und Auszug für die Diagnose sowie bei stoppenden Ablehnungen die vollständige Eingabe in einem privaten Protokoll. Scheitert diese private Speicherung, meldet er dies gesondert. Unbekannte Ausgänge autoritativer Außenwirkungen werden weiterhin fail-closed behandelt; diese Regel gehört zur Recovery und ist von der Klassifikation bereits verweigerter Werkzeuge getrennt.

Drei der vier Halts im Folgeauftrag betrafen verweigerte Werkzeugaktionen (Heredoc-Fehlalarm, nicht rekonstruierbare gekürzte Diagnose, opaque-write-Fehlalarm), der vierte Providerkapazität. Die spätere Klassifikations- und Diagnosekorrektur begründet die Darstellung im PDF, keine pauschale Fehlalarmfreiheit.

### Größenfall der ergänzenden Integration

Im Phase-B-Fall `agy-large-s1` lieferte AGY ein Finding statt der erwarteten 128. Der 512er-Fall wurde nicht ausgeführt. Die experimentelle Auswahl wurde mit dokumentierter Ausnahme zugelassen. Die Referenz und die gebundene Operatorentscheidung halten die negativen Ergebnisse fest; diese Schwäche wird in Ausgabe 2.1 im PDF ausdrücklich benannt.

## 4. Fallstudien und zeitlicher Zusammenhang

Der erste Phase-C-Topologielauf verwendet unveränderten Orchestrator `8fd548efc8ce0d5ecdfe0fed0b7cdc39424724fc` mit regulärer experimenteller Zulassung, ohne Kandidaten-Gate. Run-IDs: `20261001-075248Z` (Plan) und `20261001-075550Z` (Implementierung). Rollenprofile: `claude / opus / high` und zweimal `codex / gpt-6.1-sol / medium`. Die Qualitätsqualifikation mit `high` ist damit keine identische Profilausführung.

Die Zeitleiste im PDF verwendet Europe/Berlin. SIGINT während der Implementierung (09:56:07) und Resume (09:56:12), später SIGINT im stillen Review (10:30:26) und Resume (10:30:48), sind getrennte Eingriffe. R-01 wird nach Korrektur geschlossen. Der Abschluss um 10:38 mit Exit 0 erzeugt zwei neue Finalreview-Findings und einen Folgeauftrag. Beide Eingriffe waren manuell. Beim 31 Minuten stillen Review war das Zeitlimit 0; eine automatische Hängererkennung ist damit nicht belegt. Das [Einrichtungsbeispiel](https://github.com/dieteresser62-lab/Dual-Agent-Orchestrator/blob/c472d2196b347b4b40f428d32d21d593692f99a4/docs/reference/einrichtung.md) setzt 900 Sekunden für beide Codex-Reviewslots. Dies ist eine Betriebsempfehlung, keine aus einem bestandenen 512-Findings-Fall abgeleitete Zeitgrenze. Prozessfreiheit stammt aus der protokollierten Prozesskontrolle, nicht allein aus einem Ergebnisrecord.

Der Folgeauftrag mit Run-IDs `20261001-111811Z` und `20261001-115823Z` erstreckt sich über `96cf4a54b625f9bb8de8aa7d0324f19d3cb91250`, `5a7d6f85fb3f67efa93f8e1fca05456d012ddf99`, `4811480b5acc831e5169f55eb8053ab701b9f48e` und `0ac5466233666534695047ac6c072b88abed37f8`. Vier Halts und nachfolgende Änderungen machen ihn ausdrücklich zu keiner Einzelstandmessung. Er endet mit zwei weiteren Testparser-Befunden; `followup02` ist in der veröffentlichten Evidenz nicht ausgeführt. Die spätere Überlastungsbehandlung darf diesem früheren Lauf nicht rückwirkend als gemessener automatischer Retry zugerechnet werden.

Die RuhestandsApp-Fallstudie aus Phase A (Auto-Optimize als eigener Tab, Wächterabbruch und Resume) bleibt historisch getrennt: Abnahme `a11f72ee3321fed0f45fb0874b148e71789b4012`, Merge `adf1411b2f7cba87a4edee52dfed7d29e657a24f`. Einzelheiten stehen im [Phase-A-Evidenzindex](whitepaper-phase-a-evidenz.md).

## 5. Reproduzierbare Codezählung

Gezählt werden reguläre, versionierte `*.py`-Dateien aus `git archive <commit>` unter `src`, `tests` und `scripts`. Physische Zeilen werden mit `len(bytes.splitlines())` gezählt, einschließlich Leerzeilen, Kommentaren und Docstrings. Symlinks, nicht versionierte Dateien, andere Verzeichnisse und andere Dateitypen zählen nicht.

| Stand | src: Dateien / Zeilen | tests: Dateien / Zeilen | scripts: Dateien / Zeilen | Summe | tests / src |
|---|---:|---:|---:|---:|---:|
| Phase A, `a11f72e` | 80 / 61.441 | 113 / 86.755 | 3 / 2.533 | 150.729 | 1,4120 |
| Phase B, `222d2c1` | 81 / 62.961 | 121 / 91.052 | 14 / 6.083 | 160.096 | 1,4462 |
| Phase C, `c472d21` | 90 / 66.281 | 143 / 99.811 | 39 / 9.961 | 176.053 | 1,5059 |

Phase C einschließlich `scripts` im Nenner: 99.811 / (66.281 + 9.961) = 1,3091. Die Quote misst Textumfang, keine Testabdeckung. `source/measure_code.py` und `source/code-counts.json` im Quellpaket enthalten das unveränderte Zählverfahren und die vollständigen Commitbindungen. Aufruf: `python source/measure_code.py /pfad/zum/repository <commit>`; der Aufruf ist lesend und startet weder Provider noch Tests.

## 6. Gesicherte Testprotokolle

Die Originaldateien wurden bytegleich nach [whitepaper-evidence/](whitepaper-evidence/) kopiert. Ihre Zuordnung stammt aus den veröffentlichten [Versionsnotizen](whitepaper-evidence/versionsnachweis.md). Die pytest-Ausgaben selbst enthalten keine HEAD-Attestierung. Die Begleitdatei fasst die Versionszuordnung sachlich zusammen; sie ist kein unveränderter Rohtextauszug.

| Datei | Zugeordneter Stand | Originales Ergebnis |
|---|---|---|
| default-c472d21.log | `c472d21` | 4870 passed, 39 deselected in 706.84s (0:11:46) |
| default-0f1d63e.log | `0f1d63e` | 4822 passed, 39 deselected in 734.78s (0:12:14) |
| crash-0f1d63e.log | `0f1d63e` | 39 passed, 4822 deselected in 711.35s (0:11:51) |

SHA-256 der unveränderten Konsolenlogs:

- `default-c472d21.log`: `b246d1f07e4b5e5d18f061de1b23dfe49a06ed47a4fc246c362fa2de22dd6474`
- `default-0f1d63e.log`: `ea0f86995891f03b9fef3aeb57f0bdfb529889e8edc1353df27604a7a453f7bf`
- `crash-0f1d63e.log`: `1c971cc7463dafab479f8c1bf6be00ed54b5a408af772a998670d5d33c9c0e18`

Der letzte Crash-Nachweis gehört zu `0f1d63e9ff303c5fb6cbabcd230a15b7f5d586f9`, **nicht** zum Veröffentlichungscommit `c472d2196b347b4b40f428d32d21d593692f99a4`. Zwischen beiden Ständen änderten sich auch Code, Tests und Evidenzbindungen. Die Angabe „4.870 + 39 am selben HEAD bestanden“ wäre deshalb nicht belegt und wird im PDF nicht verwendet. Der vollständige Crash-Lauf wird für dieses Papier nicht erneut ausgeführt.

## 7. Quellenpflege und Bearbeitung

Das [Quellpaket](whitepaper-quellen.zip) enthält den vollständigen ReportLab-Generator, editierbare PlantUML-Fassungen der Diagramme, Textauszug, Zählskript, JSON-Metadaten, ausgewählte bereits öffentliche Evidenzdateien und die archivierten Testlogs. Die gestalteten Vektorgrafiken werden direkt im Generator gezeichnet; PlantUML-Dateien sind die semantisch entsprechenden bearbeitbaren Diagrammquellen. Schriftdateien werden nicht verteilt.

`SHA256SUMS.txt` bindet die ausgelieferten Paketdateien. Die Digests sichern Dateivergleich und Herkunft; sie beweisen keine Modellqualität und ersetzen keine autoritative Recordkette. Die öffentlichen Provider-Evidenzexporte sind geschwärzte Ableitungen mit eigenen Herkunftsmanifesten; sie dürfen nicht als unveränderte private Resume-Ketten verwendet werden.

## 8. Redaktionelle Überarbeitung 2.1

Unveränderter Code- und Evidenzstand; die Überarbeitung korrigiert Darstellung und Präzision. Q5/Q6 und der Referenz-Fehlalarm, die zwei Timeouts plus Vertragskorrektur beim 128er-Fall, manuelle Unterbrechung und 900-Sekunden-Beispiel, die Ablehnungsregel sowie die AGY-Größenschwäche sind genauer beschrieben. Der unbelegte anonyme Rückblick entfällt. Das öffentliche Herkunftsverzeichnis umfasst Codex, Claude und AGY.

Die neue Record-Grafik zeigt `predecessor_ids`, Inhaltsdigests pro Record-Umschlag und den gemeinsamen `effect_key` des Intent-/Result-Paares. Der rekonstruierbare Head-Cache schreibt einen Digest aus vorherigem Kettendigest und Record-ID fort. Ein zusätzlicher Vorgänger-Inhaltsdigest im einzelnen Record wird nicht behauptet. Das Sequenzdiagramm enthält Reviewer-Antwort und den ausschließlich dem Orchestrator zugeordneten Commit.

Quelle [8] ist im PDF als relative lokale Beilage verlinkt. Der Evidenzindex liegt dazu neben dem PDF. Ein öffentlicher GitHub-Link auf diese neue Veröffentlichung wird erst nach ihrem Commit und Push festgelegt; dafür wird hier kein noch fehlendes Ziel vorgetäuscht.

## 9. Redaktioneller Feinschliff 2.2

Abb. 05 trennt die Felder des eingebetteten Records durch eine Linie und eine eigene Beschriftung vom Inhaltsdigest im Umschlag. Der Umschlag enthält `record` und `content_sha256`; `phase` und `effect_key` sind als Payload-Felder gekennzeichnet. Die Quellenübersicht nennt durchgehend vollständige repository-relative Dateipfade. Die Beispiele zur Ablehnungsregel umfassen nun ausdrücklich nicht lesende Git-Aufrufe und sichtbare Schreibversuche außerhalb von Repository und Scratch. Code- und Messstand bleiben unverändert.
