# Arbeitsplan: Strukturierte persistente Agentenkommunikation

**Status:** zur Planprüfung vorgelegt
**Feature-Branch:** `feature/structured-agent-artifacts`
**GitHub-Status:** nur lokal; Push, Merge und Release sind nicht Bestandteil dieses Arbeitsplans
**Ausführungsmodus:** sichere Migration im bestehenden State-v3-Workflow
**Nummerierung:** zukünftige Implementierungsslices sind zusammenhängend und 1-basiert

---

## 1. Zielbild in eigenen Worten

Die technische Workflowentscheidung wird von frei formatiertem Markdown entkoppelt. Externe Agenten dürfen während der Migration weiterhin den bestehenden textuellen Rollenvertrag liefern; unmittelbar nach erfolgreichem Parsen entsteht daraus jedoch ein versionierter, strikt validierter und fingerprintgebundener JSON-Record. Für neu gestartete Workflows werden diese Records nach dem Cutover zur technischen Source of Truth. State-v3 und Markdown bleiben parallel erhalten: State-v3 als kompatibler Laufzeitspiegel und Wiederanlaufanker, Markdown als deterministisch erzeugte beziehungsweise semantisch abgeglichene, Git-fähige Auditansicht.

Ein Record besitzt mindestens `schema_version`, `record_id`, `record_type`, `run_id`, `logical_id`, `revision`, `status`, eine typisierte Fingerprintbindung, `predecessor_ids`, `created_at` sowie einen typabhängigen Payload. Repositorypfade und Validierungsbefehle werden ausschließlich als kanonische JSON-Arrays gespeichert. Freigaben, Findings, Gates und Bindungen werden als nachvollziehbare Zustandsübergänge modelliert, nicht durch nachträgliches Überschreiben historischer Aussagen.

Der erste Speicher besteht aus atomar veröffentlichten, unveränderlichen JSON-Dateien unterhalb des bereits ignorierten Laufzeitbereichs `.orchestrator/`. Ein rekonstruierbarer Head-/Index-Cache darf beschleunigen, ist aber keine eigene Wahrheit. Auf JSONL wird zunächst verzichtet, weil einzelne Records sauber atomar ersetzt, nach einem Prozessabbruch eindeutig gescannt und gezielt diagnostiziert werden können. SQLite wird nicht eingeführt: Der bestehende Einzelprozess-/Inbox-Lock verhindert konkurrierende Workflow-Schreiber; Transaktionen über mehrere unabhängige Prozesse sind für diesen Auftrag nicht belegt.

---

## 2. Branch-, Status- und Scope-Festlegung

- Persistierter Zielbranch ist `feature/structured-agent-artifacts`; der Branch ist beim Planen bereits aktiv.
- Diese PLAN_ONLY-Runde ändert ausschließlich `docs/internal/strukturierte-agentenkommunikation-arbeitsplan.md`.
- Die späteren Produktslices arbeiten nur in den jeweils unter **Exakter Änderungspfad** genannten Pfaden. Der Orchestrator ergänzt je Slice sein deterministisch abgeleitetes Slice-Auditdokument.
- `.orchestrator/state.json`, bestehende Checkpoints und Laufartefakte werden nie manuell bearbeitet.
- Bestehende, nicht zu diesem Auftrag gehörende Arbeitsbaumänderungen bleiben unangetastet.
- Git-Branch-, Stage-, Commit-, Push- und Merge-Transaktionen bleiben beim Orchestrator beziehungsweise Nutzer.
- Nach Änderungen an Orchestrierung, Parser, Watch, State oder Prompts führt ausschließlich der Orchestrator die vollständige Matrix `python3 -m pytest tests/ -v` aus. Agenten verwenden nur fokussierte Prüfungen.

---

## 3. Verbindliche Entscheidungen und Umsetzungskonsequenzen

### 3.1 Recordfamilien und Fingerprintbindung

Schema v1 deckt mindestens folgende typisierte Payloads ab:

- Aufgabenaufnahme und abgeleiteter Planungsvertrag;
- freigegebener Plan mit geordneten Slices;
- normale und korrigierende Work-Units mit exakten Allowlists;
- geparste Agentenergebnisse einschließlich Readiness oder Stopdiagnose;
- Reviews, Finding-Eröffnung, Implementiererantwort, reviewer-eigene Status- und Klassenänderung sowie Freigabe;
- Validierungsanforderung und -attestierung mit echten Argumentlisten;
- Nutzer- und Policy-Gates samt Entscheidung;
- Commit-, Plan-Commit- und Implementierungshandoff-Bindung;
- Quota-Pause, Resumeprüfung und Workflowabschluss.

Vor dem ersten Repositorydiff bindet ein Record den SHA-256-Digest des Aufgaben- oder Planvertrags als `fingerprint_kind=contract`. Implementierungs- und Reviewrecords binden den kanonischen Diff-Fingerprint als `fingerprint_kind=implementation`; Commit- und Handoffrecords referenzieren zusätzlich die autorisierende Attestierung und die unmittelbar vorausgehenden Freigaben. Ein Record darf keine Freigabe für einen anderen Fingerprint fortschreiben.

### 3.2 Strikte Validierung

Eine versionierte JSON-Schema-Datei verwendet geschlossene Objekte, eindeutige Typdiscriminatoren und typabhängige Statuswerte. Die Python-Domänenmodelle erzwingen darüber hinaus Übergangsinvarianten, die ein Einzeldokument-Schema nicht ausdrücken kann: fortlaufende Revisionen, existierende und zyklusfreie Vorgänger, Eigentum an Findings, zulässige Reviewreihenfolge, genau eine aktive Work-Unit, kanonische POSIX-Pfade, erlaubte Validierungsfamilien und passende Fingerprints. Unbekannte Versionen, unbekannte Felder, beschädigte Dateien, Lücken, Mehrdeutigkeiten oder unvollständige Freigabeketten halten fail-closed mit Recordpfad, ID und konkreter Ursache an.

### 3.3 Speicher-, Idempotenz- und Crashvertrag

Record-IDs werden stabil aus Run, Recordtyp, logischer Identität und Revision gebildet. Jede Workflowwirkung erhält zusätzlich einen Idempotenzschlüssel. Vor dem Erzeugen flüchtiger Metadaten prüft der Store diesen Schlüssel: Derselbe Schlüssel mit demselben fachlichen kanonischen Inhalt liefert den vorhandenen Record als No-op zurück; derselbe Schlüssel mit anderem Inhalt ist ein Konflikt. So machen ein neu erzeugter Zeitstempel oder ein Retry niemals aus derselben Aussage einen zweiten Record. Neue Dateien werden im Zielverzeichnis vollständig geschrieben, validiert, synchronisiert und atomar veröffentlicht. Beim Resume wird die Recordkette vollständig validiert; ein fehlender oder veralteter Cache wird aus den Records rekonstruiert, ein widersprüchlicher Cache verworfen und protokolliert.

Die Reihenfolge lautet: Eingabe parsen und validieren, Record idempotent persistieren und zurücklesen, semantische Gleichheit zum bisherigen State-v3-Modell beweisen, erst danach die Workflowentscheidung ausführen und State/Markdown spiegeln. Bereits vorhandene Git-/Handoff-Nebenwirkungen werden über ihre vorhandenen Side-Effect-Schlüssel und strukturierte Bindungsrecords abgeglichen. So erzeugen Watch-Neustart, Prozessabbruch und wiederholtes Resume weder doppelte Work-Units noch widersprüchliche Freigaben.

### 3.4 Dual-Write und Source-of-Truth-Cutover

Die Migration erhält zwei dauerhaft persistierte Protokollmodi:

- `legacy-state-v3`: Sitzung begann ohne strukturierte Protokollbindung und wird ausschließlich nach ihrem bisherigen Vertrag fortgesetzt;
- `structured-v1`: Sitzung begann mit Schema v1, schreibt Records, State-v3 und Markdown parallel und trifft Entscheidungen nur aus erneut geladenen validierten Records.

Der Modus wird beim Start gebunden und beim Resume niemals aus dem aktuellen Programmdefault neu abgeleitet. Vor dem Cutover dient der bestehende typisierte State-v3-Pfad als Entscheidungsquelle, während der Dual-Write-Comparator jeden strukturierten Record gegen dieselbe Domänenaussage prüft. Der abschließende Cutover aktiviert `structured-v1` nur für neue Workflows, nachdem Schema-, Store-, Dual-Write-, Resume-, Audit- und Ende-zu-Ende-Tests grün sind. Der Markdown-/State-v3-Fallback bleibt bestehen.

### 3.5 Alte Sitzungen und widersprüchliche Zustände

Eine vorhandene State-v3-Sitzung ohne Protokollbindung bleibt im Legacy-Pfad; sie wird weder still importiert noch umgedeutet. Das ist der kontrollierte Adapter für bereits begonnene Läufe. Eine neue strukturierte Sitzung benötigt eine vollständige gültige Recordkette. Treffen ungebundener Legacy-State und scheinbar zugehörige Records zusammen oder fehlen bei einer gebundenen Sitzung Records, hält der Lauf resumierbar mit Diagnose an. Ein späterer expliziter Import historischer Sitzungen ist nicht erforderlich, um diesen Auftrag sicher abzuschließen.

### 3.6 Agentenadapter und Reviewer-Eigentum

Die bestehenden Markerparser bleiben der enge Eingangsadapter. Reparatur darf nur syntaktisch eindeutige Ausgaben vervollständigen; fehlende, widersprüchliche oder mehrdeutige Freigaben ergeben keinen Approval-Record. Parser- und Reparaturfehler werden als technische Diagnostic-Records mit Rolle, Work-Unit, Versuch, Ausgabe-Digest und Fehlergrund persistiert, ohne unvalidierten Freitext zur Entscheidungsquelle zu machen.

Finding-Eröffnung speichert den berichtenden Reviewer dauerhaft. Nur ein nachfolgender Record desselben Reviewers darf Status oder Klasse ändern; Codex-Antworten bleiben eigene Response-Records. Korrektur-Work-Units referenzieren die auslösenden offenen Blocker und ihre exakte autorisierte Pfadliste. Das Abschlussreview lädt dadurch alle normalen und korrigierenden Slices strukturell und kann offene Finding-Ketten nicht durch eine unvollständige Markdownansicht verlieren.

### 3.7 Auditprojektion

Die bestehende Markdown-Ausgabe unter `docs/internal` bleibt vollständig lesbar. Ihre verwalteten Abschnitte werden deterministisch aus der geordneten Recordkette erzeugt. Während Dual-Write wird zusätzlich ein semantischer Vergleich ausgeführt: IDs, Rollen, Status, Fingerprints, Findings, Allowlists, Befehle, Gates und Bindungen müssen zwischen strukturierter Projektion und bestehendem State-v3-/Auditmodell gleich sein. Reine Formatierung ist nicht fingerprintrelevant; ein semantischer Unterschied hält vor Freigabe oder Commit an.

---

## 4. Slice-Liste

### Slice 1 - Versionierte Recordmodelle und JSON-Schema

**Zweck:** Den geschlossenen v1-Vertrag für alle Recordfamilien, Statuswerte, IDs, Fingerprints, Vorgänger, Pfadlisten und Befehlsargumente schaffen, ohne den laufenden Workflow umzuschalten.

**Exakter Änderungspfad**

- `pyproject.toml`
- `schemas/orchestrator-artifact-v1.schema.json`
- `src/artifact_models.py`
- `tests/test_artifact_models.py`

#### Umsetzung

- `jsonschema` als eng begrenzte Laufzeitabhängigkeit deklarieren und das mitgelieferte Draft-2020-12-Schema ohne Netzwerkzugriff laden.
- Gemeinsamen Envelope, typisierte Payloadmodelle und kanonische JSON-Serialisierung implementieren.
- Geschlossene Payloadvarianten für Aufgabe, Plan, Slice, Korrektur, Agentenergebnis, Diagnose, Review, Findingübergang, Attestierung, Gate, Binding, Pause/Resume und Abschluss definieren.
- Querschnittsinvarianten in Python ergänzen, insbesondere SHA-256-Formate, stabile IDs, 1-basierte Revisionen, eindeutige Vorgänger, kanonische Pfade und `argv` als nichtleere Stringliste.

#### Akzeptanzkriterien

- Jede geforderte Recordfamilie besitzt einen expliziten Typ und eindeutig validierbare Statuswerte.
- Zusätzliche oder fehlende Felder, unbekannte Typen/Versionen, Stringbefehle statt `argv` und Markdowntext statt Pfadlisten werden abgelehnt.
- Canonical-JSON-Roundtrips sind byte-stabil und verändern keine Pfad- oder Argumentgrenzen.
- Ein Finding-Übergang ohne Reporterbindung oder ein Approval ohne passenden Fingerprint ist nicht konstruierbar.

#### Fokussierte Validierung

- `python3 -m pytest tests/test_artifact_models.py -v`

#### Risiken und Rückfalloption

Schema und Pythonmodell könnten auseinanderlaufen. Paritätstests validieren jede Modellfixture durch beide Ebenen; bis Slice 5 konsumiert kein Produktionspfad die neuen Records als Wahrheit.

### Slice 2 - Append-only Artefaktspeicher und Protokollbindung

**Zweck:** Records atomar, nachvollziehbar versioniert und idempotent speichern sowie den bei Sitzungsstart gewählten Protokollmodus dauerhaft an State-v3 binden.

**Exakter Änderungspfad**

- `src/artifact_store.py`
- `src/state_io.py`
- `src/workflow_state.py`
- `tests/test_artifact_store.py`
- `tests/test_state_io.py`
- `tests/test_workflow_state.py`

#### Umsetzung

- Root-gebundenen Store `.orchestrator/artifacts/<run-id>/records/` mit atomarer Veröffentlichung, Inhaltsdigest, Vorgängerprüfung und rekonstruierbarem Head-Cache implementieren.
- Idempotentes `put`, geordnetes `load_chain`, selektives Lesen nach Typ/logischer ID und konkrete Korruptionsdiagnosen bereitstellen.
- State-v3 kompatibel um eine optionale, unveränderliche Protokollbindung erweitern; fehlendes Feld kennzeichnet historische Legacy-Sitzungen.
- Checkpoints müssen dieselbe Bindung tragen. Ein Resume darf sie weder ergänzen noch wechseln.

#### Akzeptanzkriterien

- Wiederholtes Schreiben derselben Aussage erzeugt genau einen Record; abweichender Inhalt unter demselben Idempotenzschlüssel scheitert.
- Unbekannte Schemafassung, manipulierte Datei, falscher Digest, Vorgängerlücke, Zyklus, doppelte Revision und Pfadescape stoppen mit verständlicher Diagnose.
- Ein Abbruch vor oder nach atomarer Veröffentlichung ist durch Scan deterministisch auflösbar; temporäre Dateien werden nie als Record gelesen.
- Alte gültige State-v3-Fixtures ohne Bindung laden unverändert in `legacy-state-v3`; gebundene Fixtures verlangen exakt denselben Modus und dieselbe Schemaversion.

#### Fokussierte Validierung

- `python3 -m pytest tests/test_artifact_store.py tests/test_state_io.py tests/test_workflow_state.py -v`

#### Risiken und Rückfalloption

Die zusätzliche Stateform darf aktuelle Resume-Läufe nicht invalidieren. Deshalb bleibt das Feld für alte Version-3-Dokumente optional und wird nur beim Erzeugen eines neuen Workflows gesetzt; ein Rollback ignoriert die unreferenzierten Records, ohne Legacy-State zu verändern.

### Slice 3 - Textadapter, Dual-Write und semantischer Gleichheitsnachweis

**Zweck:** Jede erfolgreich geparste Agenten- und Orchestratoraussage unmittelbar als Record persistieren, während State-v3 noch die Workflowentscheidungen steuert.

**Exakter Änderungspfad**

- `src/artifact_bridge.py`
- `src/contracts.py`
- `src/orchestrator.py`
- `src/validation_matrix.py`
- `src/workflow.py`
- `tests/test_artifact_bridge.py`
- `tests/test_contracts.py`
- `tests/test_orchestrator_runtime.py`
- `tests/test_validation_matrix.py`
- `tests/test_workflow.py`

#### Umsetzung

- Mapper zwischen vorhandenen `TaskContract`, `PlannedSlice`, `ContractResult`, `FindingRecord`, `ValidationAttestation`, Gates, Work-Units und den neuen Payloads einführen.
- Die heute in `ValidationAttestation` zu Anzeigestrings verflachten Befehle auf typisierte Command-Spezifikationen mit unverändertem `argv` umstellen. Ein expliziter Legacy-Shellbefehl bleibt als solcher gekennzeichnet und darf nicht per `shlex` oder Markdownheuristik in Argumente zurückgeraten; ein neuer strukturierter Workflow akzeptiert nur die vom bestehenden sicheren Matrixpfad gelieferten Argumentlisten.
- Parserergebnis vor der ersten darauf beruhenden Entscheidung speichern, zurücklesen und gegen das bestehende Domänenobjekt vergleichen.
- Ablehnungen des initialen oder reparierten Agentenoutputs als Diagnostic-Records persistieren; niemals Approval aus einem Teilresultat ableiten.
- Dual-Write an den vorhandenen Checkpointgrenzen bündeln und über Idempotenzschlüssel gegen Wiederholung absichern.

#### Akzeptanzkriterien

- Plan-, Slice-, Readiness-, Review-, Finding-, Gate-, Attestierungs- und Bindingaussagen erscheinen exakt einmal und fingerprintgleich als Records.
- Pfade und Befehlsargumente werden aus den bereits typisierten Domänenobjekten übernommen und nicht erneut aus Markdown extrahiert.
- Leerzeichen, Unicode, Anführungszeichen und shellrelevante Zeichen innerhalb eines einzelnen Arguments überstehen Attestierungs-, Record- und Resume-Roundtrips ohne Aufspaltung oder Neuinterpretation.
- Nur der jeweilige Reporter kann einen Findingstatus oder eine Findingklasse ändern; fremde Updates und Codex-Antworten schließen nichts.
- Ungültige Reviewerzeilen, Compact-Repair-Reste, widersprüchliche Verdicts und falsche Markerreihenfolgen erzeugen Diagnose statt Freigabe.
- Eine Abweichung zwischen State-v3-Objekt und Record stoppt vor Review, Gate, Commit oder Handoff.

#### Fokussierte Validierung

- `python3 -m pytest tests/test_artifact_bridge.py tests/test_contracts.py tests/test_validation_matrix.py tests/test_workflow.py tests/test_orchestrator_runtime.py -v`

#### Risiken und Rückfalloption

Eine ungünstige Persistenzreihenfolge könnte nach Prozessabbruch doppelte Ereignisse erzeugen. Die Tests unterbrechen jede Grenze vor und nach `put`; bis zum Cutover bleibt die alte Entscheidungskette aktiv und ein nicht erklärbarer Unterschied hält fail-closed.

### Slice 4 - Strukturierter Resume-Reader und Legacy-Kompatibilität

**Zweck:** Gebundene strukturierte Sitzungen aus Records rehydrieren und alte Sitzungen kontrolliert unter ihrem ursprünglichen State-v3-Vertrag fortführen.

**Exakter Änderungspfad**

- `src/artifact_migration.py`
- `src/inbox_watcher.py`
- `src/orchestrator.py`
- `src/state_io.py`
- `tests/test_artifact_migration.py`
- `tests/test_inbox_watcher.py`
- `tests/test_orchestrator_runtime.py`
- `tests/test_orchestrator_watch_cli.py`
- `tests/test_state_io.py`

#### Umsetzung

- Resume-Resolver implementieren: gebundene strukturierte Kette lesen, ungebundenen gültigen State-v3 im Legacy-Pfad lassen, Widerspruch resumierbar anhalten.
- Rehydration und State-v3-Spiegel semantisch vergleichen, einschließlich Work-Unit, Runde, Findingkette, Gates, Attestierungen, Commit-/Handoffbindung und Abschlussstatus.
- Watch-Identität und Poison-Diagnose um den gebundenen Modus ergänzen, ohne alte Sidecars still umzuschreiben.
- Wiederholtes Resume nach Prozessabbruch und Watch-Neustart gegen denselben letzten vollständigen Recordkopf führen.

#### Akzeptanzkriterien

- Eine alte Sitzung ohne Records setzt exakt den bisherigen Ablauf fort und erzeugt keine nachträgliche strukturierte Historie.
- Eine `structured-v1`-Sitzung kann nur aus vollständiger gültiger Kette fortfahren; fehlende, zusätzliche oder widersprüchliche Records halten mit Record-ID und Reparaturhinweis an.
- Watch-Neustart und beliebig wiederholtes Resume erzeugen keine doppelte Work-Unit, Findingänderung, Freigabe oder Poison-Verschiebung.
- Quota-Pausen behalten ihren Repositoryfingerprint; eine Änderung während der Wartezeit wird vor dem nächsten Agentenaufruf erkannt.

#### Fokussierte Validierung

- `python3 -m pytest tests/test_artifact_migration.py tests/test_state_io.py tests/test_inbox_watcher.py tests/test_orchestrator_watch_cli.py tests/test_orchestrator_runtime.py -v`

#### Risiken und Rückfalloption

Eine automatische Altbestandskonvertierung wäre mehrdeutig. Dieser Slice importiert deshalb keine laufende Legacy-Sitzung; der dokumentierte Kompatibilitätspfad ist die sichere Rückfalloption und bleibt auch nach dem Cutover verfügbar.

### Slice 5 - Deterministische Markdown-Auditprojektion aus Records

**Zweck:** Menschlich lesbare Plan-, Slice- und Gesamtaudits aus der strukturierten Kette erzeugen und während Dual-Write semantisch gegen die bestehende Projektion prüfen.

**Exakter Änderungspfad**

- `src/artifact_projection.py`
- `src/audit_trail.py`
- `src/orchestrator.py`
- `tests/test_artifact_projection.py`
- `tests/test_audit_trail.py`
- `tests/test_orchestrator_runtime.py`

#### Umsetzung

- Recordkette in die vorhandenen `AuditProjection`-Grenzen abbilden und verwaltete Markdownabschnitte deterministisch rendern.
- Semantischen Digest über IDs, Status, Fingerprints, Rollen, Findings, Pfade, `argv`, Gates und Bindungen definieren; Formatierung und Zeitdarstellung separat behandeln.
- Korrektur-Work-Units und ihre autorisierten Pfade im Slice- und Abschlussaudit sichtbar machen.
- Projektion atomar und idempotent aktualisieren; ein manueller Eingriff in verwaltete Blöcke darf keine Workflowentscheidung ändern und wird beim nächsten sicheren Projektionspunkt korrigiert oder diagnostiziert.

#### Akzeptanzkriterien

- Gleiche Recordkette erzeugt bytegleiches Markdown; Reihenfolge folgt Recordsequenz statt Dateisystem- oder Dictionaryreihenfolge.
- Auditansichten zeigen vollständige Findingketten, Korrekturscope, Attestierungen, Gates sowie Commit-/Handoffbindung.
- Markdownüberschriften, Befehlszeilen und Freitext können nicht als Pfade oder Freigaben zurück in den Workflow gelangen.
- Dual-Write stoppt bei semantischer Differenz, ohne aufgrund rein kosmetischer Markdownänderung einen Implementierungsfingerprint zu wechseln.

#### Fokussierte Validierung

- `python3 -m pytest tests/test_artifact_projection.py tests/test_audit_trail.py tests/test_orchestrator_runtime.py -v`

#### Risiken und Rückfalloption

Bytegleichheit kann durch Zeitstempel oder Escaping brechen. Der Renderer erhält eine explizite stabile Sortierung und getrennte semantische Digests; die bisherige Projektion bleibt bis Slice 6 parallel verfügbar.

### Slice 6 - Source-of-Truth-Cutover für neue Workflows und Ende-zu-Ende-Härtung

**Zweck:** `structured-v1` für neu beginnende Workflows aktivieren, Workflowentscheidungen aus zurückgelesenen Records treffen und die beobachteten Fehlerklassen als End-to-End-Regressionen sichern.

**Exakter Änderungspfad**

- `src/dry_run_scenarios.py`
- `src/orchestrator.py`
- `src/workflow.py`
- `src/workflow_state.py`
- `tests/test_dry_run_scenarios.py`
- `tests/test_orchestrator_runtime.py`
- `tests/test_plan_handoff.py`
- `tests/test_quota_wait.py`
- `tests/test_review_runtime_hardening.py`
- `tests/test_structured_artifact_regressions.py`

#### Umsetzung

- Neue Workflows beim Erzeugen auf `structured-v1` binden; Legacy-Resume unverändert lassen.
- Entscheidungskontext für Planreview, Slice/Korrektur, Reviewerfolge, Gates, Commit, Handoff und Finalreview aus erneut geladenen Records aufbauen.
- Dual-Write-State als Spiegel weiterführen und vor jedem externen Side Effect vergleichen.
- Dry-Run-Szenarien und gezielte Regressionen für Markdown-Pfadfehlinterpretation, ungültige Reviewerzeilen, offene Findingketten, autorisierte Korrekturdokumente, Quota-Fingerprintwechsel, Alt-Resume, Prozessabbruch und wiederholtes Resume ergänzen.

#### Akzeptanzkriterien

- Der automatische Weg vom informellen Inbox-Eintrag über PLAN_ONLY-Handoff und alle Slice-/Korrekturreviews bis zur branchweiten Abschlussfreigabe läuft mit strukturierter Wahrheit durch.
- Kein Approval, Gate, Commit oder Handoff ist ohne vollständige passende Vorgänger- und Fingerprintkette möglich.
- Claude- und Antigravity-Eigentum, Korrekturkonvergenz und null offene Findings im finalen Antigravity-Approval bleiben mindestens so streng wie State-v3.
- Ein Crash an jeder persistierten Schrittgrenze und anschließendes mehrfaches Resume ist idempotent.
- Korrupte, unvollständige und unbekannte Schemafassungen fail-closed; alte ungebundene Sitzungen bleiben resumierbar.

#### Fokussierte Validierung

- `python3 -m pytest tests/test_structured_artifact_regressions.py tests/test_dry_run_scenarios.py tests/test_review_runtime_hardening.py tests/test_plan_handoff.py tests/test_quota_wait.py tests/test_orchestrator_runtime.py -v`

#### Risiken und Rückfalloption

Der Cutover ist die größte Verhaltensänderung. Er gilt nur beim Initialisieren neuer States; ein persistierter Legacy-Modus ist die sofortige Rückfallgrenze. Bei fehlender Gleichheit wird nicht automatisch auf Markdown zurückgeschaltet, sondern resumierbar angehalten.

### Slice 7 - Rollenvertrag, Nutzerdokumentation und Architekturabgleich

**Zweck:** Den strukturierten Vertrag, Speicherort, Diagnoseweg, Kompatibilitätsmodus und Betrieb des Cutovers konsistent dokumentieren.

**Exakter Änderungspfad**

- `AGENTS.md`
- `ANTIGRAVITY.md`
- `CLAUDE.md`
- `CODEX.md`
- `Quickstart.md`
- `README.md`
- `tests/test_language_consistency.py`
- `workflow.puml`

#### Umsetzung

- Die vier Root-Rollendateien inhaltlich synchron halten und klarstellen, dass Textmarker nur der Eingangsadapter, validierte Records aber die technische Wahrheit neuer Sitzungen sind.
- README und Quickstart um Storelayout, Protokollbindung, Legacy-Fallback, Korruptionsdiagnose, Resume und Auditprojektion ergänzen.
- Ablaufdiagramm um Record-Persistenz vor Entscheidung, Dual-Write-Prüfung und modusgebundenen Resume-Pfad erweitern.
- Sprach- und Konsistenztests auf die neuen verbindlichen Aussagen ausweiten.

#### Akzeptanzkriterien

- Nutzer können erkennen, welche Dateien Source of Truth, Spiegel, Auditansicht und Diagnose sind.
- Dokumentation verspricht weder stille Migration noch Entfernung des Markdown-Fallbacks und beschreibt den fail-closed Wiederanlauf.
- Rollen-, Reviewer- und Validierungsgrenzen widersprechen sich in keiner der vier Root-Dateien.
- Kein Dokument behauptet SQLite, JSONL, Push, Merge oder externe Veröffentlichung als Bestandteil dieses Auftrags.

#### Fokussierte Validierung

- `python3 -m pytest tests/test_language_consistency.py -v`

#### Risiken und Rückfalloption

Dokumentation kann beim letzten Cutoverstand zurückbleiben. Der Konsistenztest prüft deshalb die zentralen Begriffe und Modi über alle betroffenen Dokumente; reine Dokumentationskorrekturen ändern die Recordkette nicht.

---

## 5. Reihenfolge und Abhängigkeitsgraph

Die Reihenfolge ist zwingend:

1. Slice 1 definiert den Vertrag.
2. Slice 2 speichert ihn und bindet den Sitzungsmodus.
3. Slice 3 beweist Dual-Write-Gleichheit, bevor Records Entscheidungen steuern.
4. Slice 4 macht Resume und Legacy-Kompatibilität vollständig.
5. Slice 5 erzeugt und vergleicht die menschliche Auditansicht.
6. Slice 6 aktiviert die strukturierte Wahrheit ausschließlich für neue Workflows und führt die Ende-zu-Ende-Härtung aus.
7. Slice 7 synchronisiert Rollen-, Nutzer- und Architekturdokumentation mit dem tatsächlich validierten Endstand.

Kein Slice darf den Cutover vorziehen. Ein späterer Slice darf eine frühere Sicherheitsgrenze nur verschärfen. Jede Änderung eines Recordschemas nach Slice 1 benötigt eine neue Schemaversion oder eine nachweislich rückwärtskompatible Korrektur samt Fixtures; bereits persistierte v1-Records werden nicht still umgeschrieben.

---

## 6. Abdeckungsmatrix

Die vorliegende Anforderung ist über folgende prüfbare Themen abgedeckt:

| Anforderungsthema | Primäre Slices | Nachweis |
|---|---:|---|
| Version, stabile ID, Typ, Status, Fingerprint und Vorgänger | 1, 2 | Schema-, Modell- und Storetests |
| Aufgabe, Plan, Slice und Korrektur-Work-Unit | 1, 3, 6 | Bridge- und E2E-Fixtures |
| Agentenergebnis, Readiness und Parserdiagnose | 1, 3 | ungültige/mehrdeutige Ausgabe-Regressionen |
| Reviews, Findings und Reviewer-Eigentum | 1, 3, 6 | Transition- und Abschlussreviewtests |
| Attestierung, Gates, Commit und Handoff | 1, 3, 6 | vollständige Vorgänger-/Fingerprintketten |
| Echte Pfad- und `argv`-Listen | 1, 3 | Schema-Negativtests und Roundtrips |
| Append-only, Idempotenz und Crash-Recovery | 2, 4, 6 | Abbruchgrenzen und wiederholtes Resume |
| Dual-Write ohne Doppelung oder Widerspruch | 3, 5, 6 | semantischer Comparator |
| Strukturiertes Lesen mit kontrolliertem Alt-Fallback | 2, 4, 6 | gebundene und ungebundene State-Fixtures |
| Deterministische Markdown-Auditansichten | 5 | Byte- und Semantiktests |
| Quota-/Repositoryfingerprint-Sicherheit | 4, 6 | Pause-/Resume-Regression |
| Watch-Neustart und Poison-Diagnose | 4, 6 | Watch- und Runtimeintegration |
| Vollständiger Standardablauf | 6 | informeller Inbox-E2E-Dry-Run |
| Bedienung und Rollenvertrag | 7 | Sprachkonsistenz und Dokumentabgleich |

---

## 7. Migration und Rollback

1. Schema und Store sind zunächst ungenutzte additive Infrastruktur.
2. Dual-Write wird aktiviert, während State-v3 weiterhin entscheidet. Jede Differenz stoppt vor einer Freigabe.
3. Strukturierter Resume wird nur für explizit gebundene Test-/Neusitzungen gelesen; historische Sitzungen behalten Legacy.
4. Auditprojektion aus Records wird gegen die bestehende Projektion geprüft.
5. Erst nach den fokussierten Tests aller Slices und der orchestratoreigenen Vollsuite bindet der Initialisierungspfad neue Workflows an `structured-v1`.
6. Ein Rollback des Defaults betrifft nur noch nicht begonnene Workflows. Persistierte `structured-v1`-Sitzungen dürfen nicht als Legacy weitergeführt werden; sie halten bei fehlender Runtimeunterstützung mit Upgrade-/Wiederherstellungshinweis an.

Es gibt keine destruktive Datenmigration. Records werden nicht in State-v3 zurückgeschrieben, alte States werden nicht mit erfundenen Freigaben angereichert und Markdown wird nicht als Reparaturquelle für eine beschädigte strukturierte Kette verwendet. Der spätere Abbau des Fallbacks ist ausdrücklich ein eigener Auftrag.

Stopbedingungen aus dem Auftrag werden mechanisch abgebildet: Nicht verlustfrei darstellbares Reviewer-Eigentum, eine mehrdeutige Altzustandsabbildung oder eine erforderliche Lockerung von Fingerprint-/Review-/Autorisierungsgrenzen führt zu einem resumierbaren Policy-Halt statt zu Migration oder Freigabe.

---

## 8. Test- und Validierungsplan

Jeder Slice führt nur seine genannten fokussierten Tests aus. Nach jedem Slice mit Orchestrierungs-, Parser-, Watch-, State- oder Promptbezug führt der Orchestrator außerhalb des Agentenprozesses die maßgebliche Vollsuite `python3 -m pytest tests/ -v` aus und bindet deren Attestierung an denselben Diff-Fingerprint.

Die Gesamtabnahme umfasst zusätzlich:

- `git diff --check` über den kanonischen Slicediff;
- Schema-Positiv- und Negativfixtures für jeden Recordtyp;
- Property-nahe Roundtrips für Pfade, Unicode, Argumentgrenzen und kanonisches JSON;
- Fault Injection vor/nach Dateiveröffentlichung, Checkpoint, Auditprojektion, Gate, Commit und Handoff;
- Wiederholungsprüfungen für gleiche Idempotenzschlüssel und konfliktierende Payloads;
- komplette Reviewer-Eigentums- und Finding-Lifecycle-Ketten;
- alte State-v3-Resume-Fixtures ohne Records sowie strukturierte Resume-Fixtures mit beschädigten, fehlenden und unbekannten Records;
- Quota-Warten mit unverändertem und verändertem Repositoryfingerprint;
- Watch-Neustart, Prozessabbruch, mehrfaches Resume und Poison-Aussteuerung;
- informellen Inbox-Eintrag bis branchweites finales Antigravity-Approval mit null offenen Findings;
- lesbare, vollständige und deterministische Markdown-Auditansichten.

Teständerungen sind fachlich erforderlich und durch den Auftrag, der gezielte Regressionen und die vollständige Testsuite verlangt, innerhalb der je Slice exakt genannten Testpfade begründet. Ein vom Orchestrator dennoch verlangtes fingerprintgebundenes Testgate bleibt verbindlich und wird nicht durch dieses Dokument umgangen.

---

## 9. Offene Fragen

Es besteht keine produktseitige Frage, die die Planung blockiert. Folgende Entscheidungen sind bewusst festgelegt und werden nur bei neuer Evidenz im jeweiligen Slicereview geändert:

- Ein gemeinsames v1-JSON-Schema mit typisierten Definitionen statt vieler unabhängig versionierbarer Schemadateien.
- Einzelne atomare JSON-Records statt JSONL.
- Kein SQLite mangels konkurrierender Schreiber oder belegter Mehrdateitransaktion.
- Kein automatischer Import laufender Legacy-Sitzungen; dauerhafter kontrollierter Kompatibilitätspfad.
- Strukturierte Source of Truth nur für nach dem getesteten Cutover neu initialisierte Workflows.

---

## 10. Review-Feedback von Claude

Wird vom Orchestrator in der verwalteten Auditprojektion ergänzt.

## 11. Review-Feedback von Antigravity

Wird nach Claude-Freigabe vom Orchestrator in der verwalteten Auditprojektion ergänzt.

## 12. Review-Antworten von Codex

Werden bei konkreten Findings vom Orchestrator in der verwalteten Auditprojektion ergänzt.

## 13. Planstatus und formale Marker

Der Plan ist implementierbar, repository-grounded und enthält sieben zusammenhängende zukünftige Slices mit exakten Pfadlisten. Die formale Readiness wird ausschließlich im Agenten-Ausgabevertrag dieser PLAN_ONLY-Runde gemeldet; Freigaben stammen ausschließlich von den vorgesehenen Reviewern und dem Orchestrator.

## Orchestrator-Prüfprotokoll

### Review-Feedback von Claude

<!-- audit:claude-review:begin -->
### Ereignis 2: Runde 1

- Reviewer: `claude`
- Freigabe: `YES`
- Validierungsbindung: `plan-validation-70ab247ec97e`
- Testdateien: keine
- Eigene Findings: `C-01`
<!-- audit:claude-review:end -->

### Review-Feedback von Antigravity

<!-- audit:antigravity-review:begin -->
### Ereignis 3: Runde 1

- Reviewer: `antigravity`
- Freigabe: `YES`
- Validierungsbindung: `plan-validation-70ab247ec97e`
- Testdateien: keine
- Prüfdimensionen: plan completeness, scope discipline, slice ordering, schema &amp; model invariants, dual-write verification, crash &amp; resume idempotency, reviewer ownership, audit projection determinism
- Größtes Restrisiko: argv normalization or string flattening at bridge boundaries silently compromising command argument boundaries prior to execution
- Realistische Bruchbedingung: any state transition or gate decision executing against unvalidated or partially-written artifact stores prior to dual-write semantic equivalence verification
- Eigene Findings: keine
<!-- audit:antigravity-review:end -->

### Review-Antworten von Codex

<!-- audit:codex-responses:begin -->
Noch keine strukturierten Codex-Antworten.
<!-- audit:codex-responses:end -->

### Validierungsattestierung

<!-- audit:validation-attestation:begin -->
### Ereignis 1: `plan-validation-70ab247ec97e`

- Diff-Fingerprint: `70ab247ec97eb4121ebb4778a87dfd74935bc2e5d4a459f1718f4d05721aa946`
- Status: `PASS`
- Vollständig: `YES`
- Kurzresultat: internal plan contract passed
- Ausgabedigest: `df5dd16f820aa352e98b8ccfbcb99528c03934851eb6f953b97121273ff00330`

| Matrixbefehl | Status | Exitcode | Kompaktausgabe |
|---|---|---:|---|
| internal:work-plan-contract | PASS | 0 | slices=1; planned_paths=1; changed_paths=1; future_slices=7; work_plan=docs/internal/strukturierte-agentenkommunikation-arbeitsplan.md |
<!-- audit:validation-attestation:end -->

### Testfreigabe und Pre-Mortem

<!-- audit:test-approval-premortem:begin -->
- Teständerungsfreigabe: nicht erfasst.
- Pre-Mortems:
  - Ereignis 2: The most likely three-month failure is not a design flaw but an execution slip during Slice 3/6: dual-write comparison gets wired after a decision point instead of strictly before it (or a checkpoint boundary is chosen that leaves a window where State-v3 decides on stale/mismatched structured data), silently reintroducing exactly the "approval from unvalidated partial output" failure class this plan exists to eliminate — caught only if the fault-injection and idempotency-replay tests promised in §8 are actually implemented with pre/post-put crash points, not merely described.
  - Ereignis 3: A subtle state serialization or bridge mismatch between legacy State-v3 structures and structured v1 JSON models in Slice 3/6 goes undetected due to shallow fixture coverage on edge-case argv/finding lifecycles, causing a fail-closed resume abort during an actual multi-turn recovery session rather than during normal linear dry runs.
<!-- audit:test-approval-premortem:end -->

### Findings-Lebenszyklus

<!-- audit:findings:begin -->
### `C-01` — `OPEN`

- Quelle: `claude`; Runde 1
- Klasse: `OBSERVATION`
- Finding: Slices 3 and 6 bundle broad, cross-cutting core-file allowlists (dual-write proof and cutover) without a documented contingency for a mid-slice PRODUCTIVE-FILE-LIMIT stop; a future correction round should be free to note whether a sub-split (e.g. separating contracts/validation_matrix mapping from orchestrator/workflow decision wiring) is warranted before implementation begins.
- Akzeptanztest: When Slice 3 or Slice 6 implementation is first attempted, confirm in the Slice review that the orchestrator's productive-change-unit budget was not exceeded and, if it was, that a documented sub-split was proposed rather than silently trimming the allowlist.
- Statusbegründung: –
<!-- audit:findings:end -->

### Entscheidungstabelle

<!-- audit:decision-table:begin -->
| ID | Quelle | Finding | Klasse | Entscheidung | Umsetzung |
|---|---|---|---|---|---|
| C-01 | claude | Slices 3 and 6 bundle broad, cross-cutting core-file allowlists (dual-write proof and cutover) without a documented contingency for a mid-slice PRODUCTIVE-FILE-LIMIT stop; a future correction round should be free to note whether a sub-split (e.g. separating contracts/validation_matrix mapping from orchestrator/workflow decision wiring) is warranted before implementation begins. | OBSERVATION | offen | offen |
<!-- audit:decision-table:end -->

### Freigabestatus

<!-- audit:approval-status:begin -->
- Implementierung bereit: `NOT_RECORDED`
- Validierung: `PASS`
- Claude-Freigabe: `YES`
- Antigravity-Freigabe: `YES`
- Red-State-Folgeslice: `NONE`
- Commit autorisiert: `NO`
<!-- audit:approval-status:end -->
