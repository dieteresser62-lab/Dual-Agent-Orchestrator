# Arbeitsplan: Phase 2 – Arbeitspaket 1 – Review-Bootstrap und deterministisches Preflight

**Status:** zur Planprüfung vorgelegt
**Feature-Branch:** `feature/native-agent-json`
**Ausführungsmodus:** bestehendes textbasiertes `structured-v1`-/State-v3-Protokoll; kein natives Agenten-JSON
**Planungsgrundlage:** `docs/internal/ORCHESTRATOR_ROADMAP_PHASE_2_PLUS.md`, insbesondere Abschnitt 9.2
**Git-Grenze:** Branch-, Stage-, Commit-, Push- und Merge-Transaktionen bleiben beim Orchestrator beziehungsweise Nutzer

---

## 1. Zielbild und kleinster vollständiger Zuschnitt

Vor jedem echten Codex-, Claude- oder Antigravity-Prozess wird genau die bereits vollständig für den jeweiligen Adapter vorbereitete Eingabe verlustfrei vermessen. Die Messung liegt nach Promptbau, Rollenvertrag, Evidenz, Ausgabevorgaben, Schema-/Policytext und datei- beziehungsweise stdin-basierter Adapterhülle, aber vor Capability-Smoke, CLI-Start oder Providerkontakt. Für jede benannte Komponente werden Zeichen und UTF-8-Bytes separat gezählt. Der Start ist nur zulässig, wenn beide Werte ihr wirksames Sicherheitsbudget nicht überschreiten; Gleichheit mit dem Budget ist zulässig, eine Überschreitung um ein Zeichen oder Byte nicht.

Die Messung verwendet keine Token- oder Zeichenannahme als vermeintliches Providerlimit. Im Repository existiert derzeit keine belastbare Source of Truth für ein in Zeichen oder Bytes ausdrückbares technisches Limit aller drei Provider. Deshalb bleiben diese Felder zunächst explizit `null`; ausschließlich die positiv konfigurierte, provider-, rollen- und operationsspezifische Sicherheitsgrenze ist verbindlich. Eine spätere belegte technische Grenze darf nur in einem versionierten Capability-Katalog mit Quellenbezeichnung ergänzt werden und kann das Sicherheitsbudget nur verschärfen, nie erweitern.

Vor `codex_final_review`, `claude_final_review` und `antigravity_final_review` prüft ein agentenfreies, übergangsspezifisches Preflight den vorhandenen strukturierten Recordgraph und seinen State-v3-Spiegel. Es verlangt nur Fakten, die vor genau diesem Übergang bereits existieren müssen. Insbesondere muss vor den drei Providerstarts noch keine finale Antigravity-Freigabe, finale Bindung oder Workflow-Completion existieren; eine verfrühte terminale Aussage ist dagegen ein Fehler. Damit wird weder der native JSON-Vertrag aus Arbeitspaket 2 bis 5 noch der semantische Evidence-Builder aus Arbeitspaket 6 vorweggenommen.

Der Umfang bleibt auf drei fachlich kohärente Slices begrenzt:

1. verlustfreie Adapterserialisierung, Budgetkonfiguration und Startbarriere;
2. typisierte Persistenz, State-Spiegel und Finalreview-Preflight;
3. Resume-/Watch-Härtung, Auditprojektion und tatsächlich erforderliche Dokumentation.

---

## 2. Verbindliche Architekturentscheidungen

### 2.1 Vorbereiteter Providerauftrag als Messgrenze

`src/provider_input_budget.py` führt ein unveränderliches Modell für einen vorbereiteten Providerauftrag ein. Es enthält Provider, Agentenrolle, `WorkflowStep`/Operation und die geordneten providerwirksamen Komponenten der bereits fertigen Prozessargumente, stdin-Daten und privaten Laufzeitdateien. Weil die drei CLIs keine gemeinsame einzelne Requestzeichenkette besitzen, bildet dieses Modell die tatsächliche mehrkanalige Übergabe kanonisch ab: prompttragende Argumentwerte, stdin und vom Providerauftrag gelesene Dateiinhalte werden jeweils genau einmal und in stabiler Reihenfolge aufgenommen. Lokale Transportmetadaten werden getrennt gehalten. Die Adapter erzeugen Prozessstart und Messung aus demselben Objekt. Dadurch werden exakt die Inhalte gemessen, die der konkrete Adapter dem Modell zugänglich macht:

- Codex: vollständiger stdin-Prompt einschließlich eingebetteter Verträge und Evidenz;
- Claude: alle verlustfreien Paket-Chunks, Manifest, System-Policy, Ausgabeschema und Startdirektive;
- Antigravity: vollständige private Promptdatei und Startdirektive;
- bei späteren rein formalen Contract-Reparaturen: der tatsächlich neu serialisierte kleine Reparaturauftrag, nicht der vorherige Vollauftrag.

Lokale Binär-, Ausgabe- und Logpfade, nicht prompttragende CLI-Flags, Prozessumgebungen, Zugangsdaten und Providerantworten sind keine Modelleingabekomponenten und werden weder gezählt noch persistiert. Ein Pfad oder Dateiname, der Bestandteil der an das Modell gesendeten Leseanweisung ist, wird dagegen als Teil dieser Anweisung gezählt; der gelesene Dateiinhalt erscheint als eigene Komponente. Die vorhandene Claude-Chunkung bleibt rein transportbedingt und muss durch Konkatenation nachweislich zeichen- und bytegleich zum ursprünglichen Paket sein. Manifest, System-Policy, JSON-Ausgabeschema, Startdirektive und sämtliche Chunks gehören zur Messung. Kein Adapter darf zur Budgeteinhaltung Inhalte abschneiden; die bestehenden Diff- und Findingdaten bleiben vollständig.

Die zentrale Laufzeitreihenfolge lautet:

1. Adapterauftrag für den konkreten Versuch vollständig und verlustfrei vorbereiten, ohne einen CLI-Prozess zu starten;
2. Zeichen, UTF-8-Bytes und Komponentengrößen berechnen;
3. Messung idempotent persistieren und – bei einem Finalreview – das Record-/State-Preflight ausführen und persistieren;
4. bei Erfolg lokale Versions-/Hilfsprüfung und anschließend den Providerprozess starten;
5. bei Fehler Adapter-Laufzeitdateien bereinigen, ohne einen Agentenprozess zu erzeugen.

Die Operation stammt aus der typisierten `CodexInvocation` beziehungsweise `ReviewerInvocation` und wird nicht aus Prompttext erraten. Primäraufruf, ein gegebenenfalls kleiner Contract-Reparaturversuch und jeder technische Vollretry werden separat nach ihrer tatsächlich neu vorbereiteten Eingabe gemessen. Die expliziten, nicht vom normalen Workflow gestarteten Capability-Diagnosebuilder bleiben Diagnosewerkzeuge; falls sie später ausführbar verdrahtet werden, müssen sie vor einem Providerkontakt dieselbe Barriere verwenden.

Ein Fake-Adapter-Test zählt Vorbereitung, lokale Capabilityprüfung und Prozessstart getrennt und beweist, dass bei einer Überschreitung nur die Vorbereitung stattfindet und alle Prozesszähler null bleiben.

### 2.2 Sicherheitsbudget und unbekannte technische Grenzen

Die strikte Repositorykonfiguration erhält eine geschlossene Tabelle `provider_input_budget` mit positivem `max_chars` und `max_bytes` für die endliche Kombination aus Provider, Rolle und Operation. Konservative eingebaute Defaults halten bestehende Konfigurationsdateien kompatibel; explizite Repositoryregeln überschreiben nur die passende Kombination. Unbekannte Provider, Rollen, Operationen, doppelte Regeln, boolesche oder nichtpositive Zahlen, zusätzliche Schlüssel und unvollständige Zeichen-/Bytepaare sind Konfigurationsfehler. Der wirksame Grenzwert ist je Dimension das Minimum aus Sicherheitsbudget und einer optional bekannten technischen Grenze. Da gegenwärtig kein belegter Zeichen-/Bytewert vorliegt, werden `technical_limit_chars`, `technical_limit_bytes` und `technical_limit_source` als unbekannt ausgewiesen und nicht aus Tokenzahlen abgeleitet.

Ein Messresultat enthält ausschließlich:

- Provider, Rolle, Operation und gebundenen Work-Unit-/Repository-Fingerprint;
- Digest der vollständig serialisierten Eingabe sowie Digest der Budgetpolicy;
- Gesamtzeichen, Gesamtbytes und benannte Komponentengrößen;
- Sicherheitsgrenzen und nullable technische Grenzen samt Quellenkennung;
- Entscheidung, verletzte Dimension, Überhang und größte verursachende Komponente.

Die Gesamtzahlen sind die Summe der geordneten Komponenten; Komponentennamen sind eine geschlossene, nicht aus Promptinhalt abgeleitete Menge. Zeichen werden als Python-Unicode-Codepoints, Bytes ausschließlich als UTF-8-Länge gezählt. Pro Dimension ist `actual <= effective_limit` zulässig und `actual > effective_limit` gesperrt. Weder Logs noch State, Records oder Markdown-Projektionen enthalten den Prompt, einzelne Promptfragmente, Secrets, Prozessumgebungen oder komplette Kommandozeilen. Die kompakte Logzeile darf nur die genannten Metadaten und Größen ausgeben.

### 2.3 Fingerprint- und Idempotenzvertrag

Die bestehenden Implementierungs- beziehungsweise Vertragsfingerprints bleiben unverändert. Zusätzlich wird ein deterministischer Übergangsfingerprint aus Provider, Rolle, Operation, Work Unit, relevantem Recordkopf ohne frühere Bootstrap-Records, Repository-/Vertragsfingerprint, serialisiertem Eingabedigest und Budgetpolicydigest gebildet. Er verhindert zwei Fehlerklassen:

- Derselbe unveränderte Resumeversuch findet dasselbe Mess- und Preflightrecord wieder und erzeugt keinen widersprüchlichen Doppelentscheid.
- Eine Änderung an Diff/Vertrag, relevanten Records, Providerauftrag oder Budgetpolicy erzeugt eine neu gebundene Messung und ein neues Preflight.

Frühere `provider_input_measurement`- und `final_review_preflight`-Records werden bei der Berechnung des fachlichen Recordkopfs ausgeblendet, damit die eigene Telemetrie auf Resume keinen selbstverstärkenden neuen Fingerprint erzeugt. Idempotenzkonflikte bleiben fail-closed.

### 2.4 Übergangsspezifisches Finalreview-Preflight

`src/final_review_preflight.py` baut auf dem vorhandenen fail-closed `structured-v1`-Resumeabgleich auf, ersetzt ihn aber nicht durch den erst für Arbeitspaket 2 vorgesehenen generischen Referenzvalidator. Es prüft für den aktuellen Finalübergang:

- jede vorhandene Referenz auf Existenz, erwarteten Payloadtyp, Run, Fingerprint und zulässige zeitliche Vorgängerposition;
- Task-, Plan-, Work-Unit-, Rollen- und Reviewreihenfolge gegen den State-v3-Spiegel;
- Finding-Eröffnung, Eigentum, Statuswechsel, Reklassifizierung und Codex-Antworten sowie identische offene Zustände in Records und History;
- genau die vollständige, erfolgreiche, vom Orchestrator erzeugte Validierungsattestierung des aktuellen Diff-Fingerprints und die zugehörige Request-/State-Entsprechung;
- den vollständigen Branchdiff gegen die Vereinigung der autorisierten abgeschlossenen Slice-, Korrektur- und verwalteten Auditpfade sowie die konfigurierte produktive Dateigrenze;
- Abwesenheit verfrühter Completion-/Final-Binding-Fakten und Konsistenz aller zu diesem Zeitpunkt zulässigen vor-terminalen Bindungen;
- die bereits erzeugte, erfolgreiche Eingabemessung für exakt Provider, Rolle, Operation, Übergangs- und Eingabedigest des unmittelbar folgenden Aufrufs.

Die drei Übergänge besitzen eine explizite Erwartungsmatrix:

| Übergang | Zusätzlich bereits erforderlich | Noch nicht erforderlich |
|---|---|---|
| Codex-Abschlussbericht | alle Slice-Commits und deren gültige Bindungen; aktuelle erfolgreiche Attestierung | Codex-Abschlussresultat, finale Reviewerfreigaben, Completion |
| Claude-Finalreview | gültiges Codex-Abschlussresultat für Work Unit und Fingerprint | Claude-/Antigravity-Finalfreigabe, Completion |
| Antigravity-Finalreview | Claude-Finalfreigabe für exakt denselben Fingerprint ohne Claude-Blocker | Antigravity-Finalfreigabe, finale Completion |

Ein Fehler wird als stabiler Fehlercode mit Kategorie `technical` oder `correction_required`, betroffenen Record-IDs beziehungsweise Pfaden und knapper Abhilfe persistiert. Budgetüberschreitung und deterministischer Preflightdenial sind ausdrücklich keine `AgentFailureKind`: Es wurde noch kein Agent aufgerufen, daher dürfen Quota-/Transient-Retry und Providerfehlerklassifikation nicht greifen. Beide führen über einen eigenen Gategrund zu `AWAITING_RESUME` am unveränderten Schritt, niemals zu einer fachlichen Nutzerfreigabe und niemals zu einem Watch-Poison-Retry. Nach Reparatur prüft der normale CLI-Aufruf mit `--resume` denselben Übergang neu. Änderungen außerhalb einer bereits autorisierten Allowlist werden durch das Preflight nicht still autorisiert.

### 2.5 Strukturierte Persistenz und Rückwärtskompatibilität

Das vorhandene Artifact-v1-Schema wird additiv um geschlossene Payloadtypen für `provider_input_measurement` und `final_review_preflight` erweitert; bestehende Recordtypen und Protokollbindungen werden nicht verändert oder migriert. Die Payloads tragen den Übergangsfingerprint, den relevanten fachlichen Recordkopf, die Messungsreferenz und das Ergebnis. State-v3 erhält eine rückwärtskompatibel optionale, geordnete Liste derselben kompakten Checkfakten pro Work Unit. Der Resume-Reader vergleicht Records und Spiegel semantisch und akzeptiert historische States ohne diese Felder, solange vor dem neuen Code noch kein entsprechender Record existierte.

Neue `structured-v1`-Läufe schreiben beide Darstellungen vor der externen Nebenwirkung. Historische `structured-v1`-Läufe bleiben in ihrem Modus und erhalten beim nächsten relevanten Übergang die neuen additiven Records. `legacy-state-v3`-Läufe bleiben ohne Recordmigration lesbar; sie speichern dieselben typisierten, fingerprintgebundenen kompakten Fakten ausschließlich im bestehenden State-Spiegel. Kein Lauf wird auf einen nativen Protokollmodus umgebunden. Ein historischer State ohne Bootstrapfakten ist nur vor seinem ersten solchen Check zulässig; existiert auf einer Seite bereits ein Check, muss die Spiegelung vollständig sein und semantisch übereinstimmen.

Die Markdown-Auditprojektion zeigt nur Entscheidung, Größen, Grenzwerte, nullable technische Grenze, Fehlercode und Bindungsdigests. Eine fehlgeschlagene Recordpersistenz oder Mirror-Aktualisierung verhindert den Providerstart.

---

## 3. Umsetzungsslices

### Slice 1 - Verlustfreie Providerinput-Messung und Startbarriere

**Zweck:** Die Adapter liefern einen vollständig vorbereiteten, komponentisierten Auftrag; die Laufzeit misst ihn vor jedem echten Agentenprozess und erzwingt die strikte Zeichen-/Bytepolicy.

**Exakter Änderungspfad**

- `src/provider_input_budget.py`
- `src/agent_adapters.py`
- `src/agent_runtime.py`
- `src/cli.py`
- `src/orchestrator.py`
- `orchestrator.toml`
- `tests/test_provider_input_budget.py`
- `tests/test_agent_adapters.py`
- `tests/test_agent_runtime.py`
- `tests/test_cli.py`

**Umsetzung:**

- Geschlossene Budgetkonfiguration und deterministische Auswahl der exakten Provider-/Rollen-/Operationsregel implementieren; technische Limits separat nullable halten.
- Adapter auf eine vorbereitete Auftragsstruktur umstellen, ohne Ausgabeparser, Textmarker oder Berechtigungsprofile zu ändern.
- `run_agent`/`run_agent_checked` um Operation und einen Vorstartcallback ergänzen; Auftrag vor lokaler Capabilityprüfung und Prozessstart vorbereiten, messen und bei Überschreitung mit einem eigenen typisierten Bootstrapdenial abbrechen, der nicht als Agentenfehler klassifiziert oder automatisch wiederholt wird.
- Aufräumen privater Prompt-/Chunkdateien auf allen Erfolgs- und Fehlerpfaden sicherstellen.
- Kompakte Logs auf Größen, Digests und Ursache begrenzen.

**Akzeptanz und fokussierte Tests:**

- Synthetische Aufträge knapp unter, exakt am und knapp über Zeichen- und Bytebudget liefern die erwartete Entscheidung.
- Mehrbyte-Unicode beweist voneinander unabhängige Zeichen- und Byteprüfung.
- Die Summe der Komponenten entspricht der vollständigen kanonischen Mehrkanaleingabe; bei Codex wird stdin, bei Claude werden Chunks plus Manifest/System-Policy/Schema/Direktive und bei Antigravity Promptdatei plus Schema/Direktive vollständig erfasst; Claude-Chunkung ist verlustfrei.
- Überschreitung lässt weder Capability- noch Adapter-/Providerprozess anlaufen.
- Unbekannte technische Grenzen bleiben `None`/`null` und schwächen die Sicherheitsgrenze nicht ab.
- Telemetrie enthält Komponenten und Ursache, aber keinen Prompttext, keine Umgebungswerte und keine Secrets.

**Risiko und Rückfallgrenze:** Der kritischste Fehler wäre eine zweite, nicht gemessene Serialisierung im Startpfad. Deshalb darf es nach erfolgreicher Messung keinen erneuten Promptbau geben; Prozessargumente, stdin und private Dateien stammen aus demselben vorbereiteten Objekt. Der Slice ändert weder Workflowentscheidungen noch Artifact-Schema.

### Slice 2 - Fingerprintgebundene Checks und Finalreview-Preflight

**Zweck:** Budgetmessungen und übergangsspezifische Finalreviewprüfungen werden typisiert, idempotent und spiegelgleich persistiert und blockieren mechanisch ungültige Finalaufrufe.

**Exakter Änderungspfad**

- `schemas/orchestrator-artifact-v1.schema.json`
- `src/artifact_models.py`
- `src/artifact_bridge.py`
- `src/artifact_migration.py`
- `src/artifact_projection.py`
- `src/final_review_preflight.py`
- `src/workflow_state.py`
- `src/workflow.py`
- `src/orchestrator.py`
- `tests/test_artifact_models.py`
- `tests/test_artifact_bridge.py`
- `tests/test_artifact_migration.py`
- `tests/test_artifact_projection.py`
- `tests/test_final_review_preflight.py`
- `tests/test_workflow_state.py`
- `tests/test_workflow.py`
- `tests/test_orchestrator_runtime.py`
- `tests/test_structured_artifact_regressions.py`

**Umsetzung:**

- Geschlossene Payloadmodelle und Schemaarme für Messung und Preflight ergänzen; ArtifactBridge und Auditprojektion ohne Rohprompt erweitern.
- Optionale State-v3-Spiegelfakten mit strikter Rehydrierung, Semantikvergleich und rückwärtskompatibler Deserialisierung ergänzen.
- Den fachlichen Recordkopf und den Übergangsfingerprint kanonisch bilden; unveränderte erfolgreiche und fehlgeschlagene Wiederholungen als idempotenten No-op behandeln.
- Das Preflight mit der Übergangsmatrix aus Abschnitt 2.4 implementieren und unmittelbar nach erfolgreicher Messung, aber vor jedem der drei Finalproviderstarts ausführen.
- Budget- oder Preflightdenials am unveränderten Schritt als typisierten, nicht nutzerfachlichen `AWAITING_RESUME`-Zustand spiegeln; nach geänderter Eingabe, Policy oder Recordlage erneut prüfen.
- Änderungen in `src/workflow.py` und `src/workflow_state.py` strikt auf Aufrufverdrahtung, neue typisierte Bootstrap-/Spiegelfakten und den dedizierten Resume-Gategrund begrenzen. Bestehende Markerregexe, Textparser, Normalisierung und akzeptierte Rollenmarkergrammatik bleiben bytegleich; die Implementierungsprüfung weist diese Diffgrenze ausdrücklich nach.

**Akzeptanz und fokussierte Tests:**

- Ein synthetisch gültiger Recordgraph erlaubt Codex-, Claude- und Antigravity-Finalübergang jeweils mit den zu diesem Zeitpunkt vorhandenen Fakten.
- Fehlende, typfalsche, run-fremde, fingerprintfremde oder zeitlich unzulässige Referenzen verhindern den Aufruf.
- Widersprüchliche Findingzustände/Eigentümer, fehlgeschlagene oder fremde Attestierungen, unerlaubte Pfade und überschrittene produktive Dateigrenzen werden vor dem ersten Finalreview erkannt.
- Das Preflight verlangt keine noch nicht mögliche spätere Freigabe oder Completion und weist eine verfrühte Completion zurück.
- Ein erfolgreicher oder fehlgeschlagener unveränderter Check erzeugt bei Resume keine doppelten Records; ein geänderter relevanter Fingerprint, Auftrag oder Policydigest bindet neue Records.
- Record-voraus/State-voraus, Persistenzfehler und idempotenter Inhaltskonflikt bleiben fail-closed und starten keinen Prozess.
- Regressionstests beweisen zusätzlich, dass `SLICE_PLAN`, Readiness-, Finding- und Approvalparser sowie ihre Legacy-/`structured-v1`-Fixtures durch das Wiring unverändert bleiben.

**Risiko und Rückfallgrenze:** Größtes Risiko ist eine zirkuläre Selbstbindung der neuen Checkrecords oder eine zu frühe Completionanforderung. Der fachliche Kopf schließt frühere Bootstrap-Records aus, und jede Übergangsstufe besitzt eigene Positiv- und Negativfixtures. Der Validator bleibt auf Finalreview-Bootstrap beschränkt; ein allgemeiner Append-/Resume-Referenzvalidator bleibt Arbeitspaket 2.

### Slice 3 - Resume, Watch, Audit und Betriebsdokumentation

**Zweck:** Die neue Barriere wird Ende-zu-Ende gegen Resume, Watch und historische Protokollfixtures abgesichert und nur in den tatsächlich betroffenen Nutzer- und Architekturansichten dokumentiert.

**Exakter Änderungspfad**

- `src/inbox_watcher.py`
- `src/orchestrator.py`
- `src/artifact_projection.py`
- `README.md`
- `Quickstart.md`
- `workflow.puml`
- `tests/test_inbox_watcher.py`
- `tests/test_orchestrator_watch_cli.py`
- `tests/test_dry_run_scenarios.py`
- `tests/test_language_consistency.py`
- `tests/test_structured_artifact_regressions.py`

**Umsetzung:**

- Watch-Ergebnisabbildung explizit gegen Budget-/Preflighthalts härten: resumierbarer Halt ohne Attempt-Erhöhung, Poison-Verschiebung oder automatische Providerwiederholung.
- Ende-zu-Ende-Fixtures für Fehler, externe Behebung und normalen `--resume`-Fortgang sowie unveränderte Slice- und Finalreviewabläufe ergänzen.
- Historische `structured-v1`- und `legacy-state-v3`-Fixtures ohne Protokollwechsel weiterführen.
- README/Quickstart um Budgetkonfiguration, nullable Providerlimit, knappe Diagnosen und Resumeablauf ergänzen; UML nur um die neue lokale Barriere vor den drei Finalaufrufen erweitern.

**Akzeptanz und fokussierte Tests:**

- Nach Behebung einer Budget- oder Preflightursache setzt `--resume` exakt denselben Schritt fort; ein unveränderter Fehler bleibt stabil und erzeugt keine Agenten- oder Watch-Retryschleife.
- Watch lässt die Aufgabe resumierbar liegen und erzeugt weder `.poison` noch `*.poison.error.json`.
- Normale Slice-Reviews sowie gültige branchweite Finalreviews laufen mit Fake-Adaptern unverändert bis zum Abschluss.
- Bestehende Protokollfixtures bleiben lesbar, ohne Migration oder Fallback.
- Auditansicht und Logs enthalten vollständige Größen-/Ursachenmetadaten, aber keine vollständige Eingabe.

**Risiko und Rückfallgrenze:** Eine falsche Watchklassifikation könnte deterministische Denials wiederholt ausführen. Die Regressionstests prüfen deshalb Disposition, Sidecars, Aufrufzähler und unveränderten WorkflowStep gemeinsam. Dokumentation verspricht keine native Agenten-I/O, Kompaktierung oder Snapshotabdeckung.

---

## 4. Sliceübergreifende Abnahme

Nach jedem Slice laufen nur dessen fokussierte synthetische Tests mit Fake-Adaptern. Nach Änderungen an Orchestrierung, Adapter/Prompt, Watch, State oder Schema führt der Orchestrator die autoritative Vollsuite aus:

```text
python3 -m pytest tests/ -v
```

Die Gesamtfreigabe erfordert zusätzlich:

- keine echten Provideraufrufe, Zugangsdaten, Netzverbindungen, Browser oder Portbindungen in Budget-/Preflighttests;
- vollständige Abdeckung aller elf im Auftrag genannten Regressionstestklassen;
- `git diff --check` ohne Whitespacefehler;
- keine Änderungen an Textmarkerparsern, nativen Request-/Response-Schemas oder semantischer Diff-/Snapshotarchitektur;
- keine stille Trunkierung und keine als vollständig deklarierte Teilprüfung;
- keine Änderung der gebundenen Protokollmodi historischer Läufe.

## 5. Stopbedingungen während der Umsetzung

Die Umsetzung hält kontrolliert an, wenn eine der fachlichen Grenzen des Auftrags eintritt: mehr als drei sinnvoll prüfbare Slices, notwendige native Agenten-JSON-Verträge, ein allgemeiner semantischer Evidence-Builder, Abschwächung bestehender Fingerprint-/Finding-/Attestierungs-/Resume-Garantien, ein nur geratenes technisches Providerlimit, stille Protokollmigration oder Eingabeverlust zur Budgeteinhaltung. Rein lokale Agentensandbox-, Browser-, Portbindungs- oder Providerprobleme sind kein Produktentscheid und verhindern bei vollständiger Implementierung und synthetischen Tests nicht die normale Übergabe an die autoritative Orchestratorvalidierung.

## Orchestrator-Prüfprotokoll

### Review-Feedback von Claude

<!-- audit:claude-review:begin -->
### Ereignis 2: Runde 1

- Reviewer: `claude`
- Freigabe: `YES`
- Validierungsbindung: `plan-validation-c675707a643c`
- Testdateien: keine
- Eigene Findings: `C-01`

<!-- artifact-records:claude-review:begin -->
Semantischer Record-Digest: `4a830db535db61dbf9395263d51a078d12bd5344459a1aefaf6ecca79f0d5a7d`

- 4. `ar1-4d002a64bee255bb93c8ab8cd329d0b256da04ee9efdeb1faa12df0d242a8249`: `approved`; Work-Unit `1`; Findings `C-01`; Fingerprint `c675707a643c153c5fa638a4f4d8bfee280e55c32ad928b8115d76b781b88d04`
<!-- artifact-records:claude-review:end -->
<!-- audit:claude-review:end -->

### Review-Feedback von Antigravity

<!-- audit:antigravity-review:begin -->
### Ereignis 3: Runde 1

- Reviewer: `antigravity`
- Freigabe: `YES`
- Validierungsbindung: `plan-validation-c675707a643c`
- Testdateien: keine
- Prüfdimensionen: 3-slice decomposition, exact path allowlists, multichannel payload measurement before execution, transition-specific preflight graph validation, idempotent resume fingerprinting without bootstrap self-invalidation, fail-closed watch handling
- Größtes Restrisiko: An uncounted CLI parameter or metadata channel is introduced in an adapter without registering in the prepared payload model
- Realistische Bruchbedingung: An adapter passes prompt-bearing content via auxiliary arguments or environment variables that bypass the canonical PreparedProviderJob component accounting
- Eigene Findings: keine

<!-- artifact-records:antigravity-review:begin -->
Semantischer Record-Digest: `4a830db535db61dbf9395263d51a078d12bd5344459a1aefaf6ecca79f0d5a7d`

- 6. `ar1-8215ca74c6b6a9311a0712c50e110d7d25b4d9df7ea88ef809dd6c7f39457544`: `approved`; Work-Unit `1`; Findings `C-01`; Fingerprint `c675707a643c153c5fa638a4f4d8bfee280e55c32ad928b8115d76b781b88d04`
<!-- artifact-records:antigravity-review:end -->
<!-- audit:antigravity-review:end -->

### Review-Antworten von Codex

<!-- audit:codex-responses:begin -->
Noch keine strukturierten Codex-Antworten.

<!-- artifact-records:codex-responses:begin -->
Semantischer Record-Digest: `4a830db535db61dbf9395263d51a078d12bd5344459a1aefaf6ecca79f0d5a7d`

Keine Codex-Findingantworten.
<!-- artifact-records:codex-responses:end -->
<!-- audit:codex-responses:end -->

### Validierungsattestierung

<!-- audit:validation-attestation:begin -->
### Ereignis 1: `plan-validation-c675707a643c`

- Diff-Fingerprint: `c675707a643c153c5fa638a4f4d8bfee280e55c32ad928b8115d76b781b88d04`
- Status: `PASS`
- Vollständig: `YES`
- Kurzresultat: internal plan contract passed
- Ausgabedigest: `eed32e1865a4fbd53d6c85e8d400ef4728f92a77c7e86578de2bcfdea0d13dfb`

| Matrixbefehl | Status | Exitcode | Kompaktausgabe |
|---|---|---:|---|
| internal:work-plan-contract | PASS | 0 | slices=1; planned_paths=1; changed_paths=1; future_slices=3; work_plan=docs/internal/phase-2-arbeitspaket-1-review-bootstrap-arbeitsplan.md |

<!-- artifact-records:validation-attestation:begin -->
Semantischer Record-Digest: `4a830db535db61dbf9395263d51a078d12bd5344459a1aefaf6ecca79f0d5a7d`

- 3. `ar1-fb4defe70e0744b390b1b7733d06f9e5d5abc318bf1032c87eb08d6ee31042de`: Attestierung durch `orchestrator`; Fingerprint `c675707a643c153c5fa638a4f4d8bfee280e55c32ad928b8115d76b781b88d04`
  - `pass` / Exit `0` / Output `eed32e1865a4fbd53d6c85e8d400ef4728f92a77c7e86578de2bcfdea0d13dfb`: `argv` [`internal:work-plan-contract`]
<!-- artifact-records:validation-attestation:end -->
<!-- audit:validation-attestation:end -->

### Testfreigabe und Pre-Mortem

<!-- audit:test-approval-premortem:begin -->
- Teständerungsfreigabe: nicht erfasst.
- Pre-Mortems:
  - Ereignis 2: Most likely 3-month failure cause is not a security or correctness gap but the cross-slice test-file boundary above: a signature change landing in Slice 1 forces an unlisted-file edit that the orchestrator's allowlist stop rules then treat as scope creep, stalling the workflow on a mechanical technicality rather than a real defect.
  - Ereignis 3: A future provider CLI update introduces new prompt-bearing command-line parameters or sidecar files that are invoked outside the canonical PreparedProviderJob component list, leading to unmeasured prompt growth bypassing the preflight budget gate.

<!-- artifact-records:test-approval-premortem:begin -->
Semantischer Record-Digest: `4a830db535db61dbf9395263d51a078d12bd5344459a1aefaf6ecca79f0d5a7d`

Keine strukturierten Gates.
<!-- artifact-records:test-approval-premortem:end -->
<!-- audit:test-approval-premortem:end -->

### Findings-Lebenszyklus

<!-- audit:findings:begin -->
### `C-01` — `OPEN`

- Quelle: `claude`; Runde 1
- Klasse: `OBSERVATION`
- Finding: Slice 1 changes &#96;src/agent_runtime.py&#96; (adds Operation + pre-start callback to run_agent/run_agent_checked) and &#96;src/orchestrator.py&#96;, but its exact-path allowlist omits &#96;tests/test_orchestrator_runtime.py&#96;, which only appears under Slice 2. If that file asserts on run_agent/run_agent_checked call signatures, a Slice-1 mandatory full-suite run (§4) could surface a break outside Slice 1's allowlist, risking an UNEXPECTED-PATH stop mid-Slice.
- Akzeptanztest: During Slice 1 implementation, if the mandatory full pytest run after this orchestration/runtime change fails only in tests/test_orchestrator_runtime.py due to the new run_agent/run_agent_checked signature, Codex must be able to fix that file within Slice 1 (or the plan's Slice 1 allowlist must be corrected in a bounded revision) rather than tripping PRODUCTIVE-FILE-LIMIT/UNEXPECTED-PATH.
- Statusbegründung: –

<!-- artifact-records:findings:begin -->
Semantischer Record-Digest: `4a830db535db61dbf9395263d51a078d12bd5344459a1aefaf6ecca79f0d5a7d`

- 5. `ar1-08ba2dae7a9e59f0285ed417047a39e295ae53110fb1070bc6feb6a7d42b98a2`: `C-01` `opened` durch `claude`; `OBSERVATION` / `open` — Slice 1 changes &#96;src/agent_runtime.py&#96; (adds Operation + pre-start callback to run_agent/run_agent_checked) and &#96;src/orchestrator.py&#96;, but its exact-path allowlist omits &#96;tests/test_orchestrator_runtime.py&#96;, which only appears under Slice 2. If that file asserts on run_agent/run_agent_checked call signatures, a Slice-1 mandatory full-suite run (§4) could surface a break outside Slice 1's allowlist, risking an UNEXPECTED-PATH stop mid-Slice.
<!-- artifact-records:findings:end -->
<!-- audit:findings:end -->

### Entscheidungstabelle

<!-- audit:decision-table:begin -->
| ID | Quelle | Finding | Klasse | Entscheidung | Umsetzung |
|---|---|---|---|---|---|
| C-01 | claude | Slice 1 changes &#96;src/agent_runtime.py&#96; (adds Operation + pre-start callback to run_agent/run_agent_checked) and &#96;src/orchestrator.py&#96;, but its exact-path allowlist omits &#96;tests/test_orchestrator_runtime.py&#96;, which only appears under Slice 2. If that file asserts on run_agent/run_agent_checked call signatures, a Slice-1 mandatory full-suite run (§4) could surface a break outside Slice 1's allowlist, risking an UNEXPECTED-PATH stop mid-Slice. | OBSERVATION | offen | offen |

<!-- artifact-records:decision-table:begin -->
Semantischer Record-Digest: `4a830db535db61dbf9395263d51a078d12bd5344459a1aefaf6ecca79f0d5a7d`

| Seq | Record | Typ | Status | Logische ID | Revision | Fingerprint |
|---:|---|---|---|---|---:|---|
| 1 | `ar1-c915721040d24320b6e05d0f78d079e355d1fb6aec93f3b007869260a1417992` | `task` | `accepted` | `task-contract` | 1 | `contract:795778f6e7a3f64b77bc7373afa9b6e9fcf56a6019d1e1f35f268f4ec074dd66` |
| 2 | `ar1-a98b09bef71b99b1dd365f8de3070febe991a89713bf9e3fa90369249579d759` | `agent_result` | `ready` | `agent-1-codex_plan-1` | 1 | `contract:795778f6e7a3f64b77bc7373afa9b6e9fcf56a6019d1e1f35f268f4ec074dd66` |
| 3 | `ar1-fb4defe70e0744b390b1b7733d06f9e5d5abc318bf1032c87eb08d6ee31042de` | `validation_attestation` | `attested` | `plan-validation-c675707a643c` | 1 | `implementation:c675707a643c153c5fa638a4f4d8bfee280e55c32ad928b8115d76b781b88d04` |
| 4 | `ar1-4d002a64bee255bb93c8ab8cd329d0b256da04ee9efdeb1faa12df0d242a8249` | `review` | `decided` | `review-claude-1-1` | 1 | `implementation:c675707a643c153c5fa638a4f4d8bfee280e55c32ad928b8115d76b781b88d04` |
| 5 | `ar1-08ba2dae7a9e59f0285ed417047a39e295ae53110fb1070bc6feb6a7d42b98a2` | `finding_transition` | `recorded` | `finding-C-01` | 1 | `implementation:c675707a643c153c5fa638a4f4d8bfee280e55c32ad928b8115d76b781b88d04` |
| 6 | `ar1-8215ca74c6b6a9311a0712c50e110d7d25b4d9df7ea88ef809dd6c7f39457544` | `review` | `decided` | `review-antigravity-1-1` | 1 | `implementation:c675707a643c153c5fa638a4f4d8bfee280e55c32ad928b8115d76b781b88d04` |
<!-- artifact-records:decision-table:end -->
<!-- audit:decision-table:end -->

### Freigabestatus

<!-- audit:approval-status:begin -->
- Implementierung bereit: `NOT_RECORDED`
- Validierung: `PASS`
- Claude-Freigabe: `YES`
- Antigravity-Freigabe: `YES`
- Red-State-Folgeslice: `NONE`
- Commit autorisiert: `NO`

<!-- artifact-records:approval-status:begin -->
Semantischer Record-Digest: `4a830db535db61dbf9395263d51a078d12bd5344459a1aefaf6ecca79f0d5a7d`

Keine Work-Unit- oder Binding-Records.
<!-- artifact-records:approval-status:end -->
<!-- audit:approval-status:end -->
