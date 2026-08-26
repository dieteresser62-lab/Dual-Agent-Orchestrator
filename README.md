# Dual-Agent Task Orchestrator

Eine fortsetzbare CLI für klar abgegrenzte Entwicklungsaufgaben mit Codex als Planer und Implementierer sowie Claude als unabhängigem Reviewer.

## Überblick

Der Orchestrator überführt eine Markdown-Aufgabe in einen geordneten State-v3-Slice-Plan. Jeder Slice besitzt eine exakte Pfad-Allowlist, eine deterministische Validierung, asymmetrische Reviews und einen verifizierten lokalen Git-Commit. Nach dem letzten Slice prüfen Codex und Claude die vollständige Branchänderung, bevor der Lauf abgeschlossen ist.

![State-v3-Workflow](https://www.plantuml.com/plantuml/proxy?cache=no&src=https://raw.githubusercontent.com/dieteresser62-lab/Dual-Agent-Orchestrator/master/workflow.puml)

Der normale Ablauf ist:

1. Repository, Branch, Aufgabe, Konfiguration und vorhandenen Zustand prüfen.
2. Codex geordnete `SLICE_PLAN`-Datensätze erstellen und Claude den Planfingerprint prüfen lassen.
3. Den von Claude freigegebenen Plan lokal committen und im Inbox-Watchbetrieb den erzeugten Implementierungs-Handoff automatisch übernehmen. Eine fingerprintgebundene Benutzerfreigabe ist mit `--plan-gate` optional zuschaltbar.
4. Für jeden geplanten Slice:
   - Codex bearbeitet ausschließlich den persistierten Pfadumfang.
   - Der Orchestrator ermittelt den kanonischen Diff und führt die konfigurierte Validierungsmatrix einmal für diesen Fingerprint aus.
   - Claude prüft in der ersten Runde nur die Slice-Änderungen und in späteren Runden nur das Korrekturdelta.
   - Der Orchestrator staged ausschließlich die geprüften Pfade, erstellt einen lokalen Commit `Slice NN: ...` und verifiziert ihn.
5. Codex einen branchweiten Vollständigkeitsbericht gegen die Branchbasis erstellen
   lassen und diesen Bericht zusammen mit dem vollständigen Branch-Diff an Claude
   für die Abschlussentscheidung übergeben.
6. Im Abschlussreview muss Claude alle offenen Findings schließen oder zu einem Blocker hochstufen. Blocker werden in einem begrenzten Korrekturslice bearbeitet und commitet; anschließend wird der vollständige Abschlussreview wiederholt. Erfolgreich endet der Lauf erst bei null offenen Findings.

Erkennt Codex während eines Slices einen konkreten Defekt in einem bereits
abgeschlossenen Vorgängerslice, kann es mit `REMEDIATION_PATHS` die kleinste
notwendige Pfadmenge melden. Der Orchestrator erweitert den laufenden Slice nur
dann automatisch, wenn alle Pfade bereits zum freigegebenen Vorgängerslice
gehörten und das produktive Dateilimit weiter gilt. Unbekannte oder zukünftige
Pfade sowie echte Produktentscheidungen bleiben ein Gate.

Keine Rolle ersetzt eine andere. Codex gibt die eigene Arbeit niemals frei und commitet sie nicht selbst. Reviewer können weder den Quell-Worktree bearbeiten noch Validierungsergebnisse für sich beanspruchen.

## Referenzdokumentation

- [Architektur- und Fachkonzept](docs/reference/architecture-and-domain-concept.md) beschreibt Systemgrenze, Domänenmodell, Invarianten, Komponenten, Zustandsmaschine, Vertrauensgrenzen und betriebliche Eigenschaften.
- [Marktvergleich](docs/reference/market-comparison.md) ordnet den Orchestrator anhand aktueller offizieller Produktdokumentation gegenüber repräsentativen Coding-Agenten und Agentenplattformen ein.

## Voraussetzungen und unterstützte Plattformen

Erforderlich ist Python 3.11 oder neuer. Für das TOML-Parsing wird die Python-Standardbibliothek verwendet; das Projekt besitzt keine Python-Laufzeitabhängigkeiten.

Unterstützte Ausführungsumgebungen sind:

- Linux
- macOS
- WSL2

Natives Windows wird derzeit nicht unterstützt, weil der vollständige Workflow dort noch nicht verifiziert wurde.

Beide Rollen-CLIs müssen installiert und authentifiziert sein. Anschließend müssen sie in `PATH` liegen oder über explizite Binärpfade konfiguriert werden:

- `codex`
- `claude`

Die Laufzeit prüft jedes Programm und seine erforderlichen Fähigkeiten verzögert unmittelbar vor dem ersten Aufruf der jeweiligen Rolle. Freigegebene Major-/Minor-Linien akzeptieren numerische Patchupdates automatisch; ein Major- oder Minor-Wechsel bleibt bis zu einer erneuten Capability-Freigabe gesperrt. Unabhängig von der Patchversion müssen alle erforderlichen CLI-Flags vorhanden sein.

## Schnellstart

Eine kurze vollständige Anleitung enthält [Quickstart.md](Quickstart.md).

Im normalen Betrieb genügt im Zielrepository eine informelle Datei wie `inbox/meine-idee.md`:

```markdown
# Meine Idee

TARGET_BRANCH: feature/mein-vorhaben

Beschreibe hier in eigenen Worten, was verbessert oder untersucht werden soll.
```

Danach startet ein einziger Befehl Planung, Planreviews, lokalen Plancommit, Implementierungs-Handoff, alle validierten und reviewten Slice-Commits sowie das branchweite Abschlussreview:

```bash
run_task --watch
```

Der Watcher legt einen fehlenden Zielbranch an oder wechselt sicher auf einen vorhandenen. Bei einem notwendigen Branchwechsel mit nicht ignorierten Arbeitsbaum- oder Indexänderungen hält er an, statt Änderungen zu stashen oder mitzunehmen. Die einzige enge Ausnahme ist ein bereits vorhandener, regulärer und noch unversionierter `PLAN_ONLY`-Arbeitsplan, der im Auftrag exakt als `WORK_PLAN_PATH` gebunden ist; diesen nimmt der Branchwechsel als Aufgabenartefakt mit. Andere Änderungen oder ein bereits getrackter Plan bleiben ein Stopgrund. Auf einem bereits aktiven Zielbranch beginnt die neue Aufgabe am aktuellen `HEAD`.

Formale Einzelaufgaben bleiben für fortgeschrittene und maschinell erzeugte Aufträge verfügbar. [example-plan-task.md](example-plan-task.md) zeigt einen formalen Planauftrag, [example-task.md](example-task.md) einen formalen Implementierungsauftrag:

```bash
./run_task --task-file path/to/my-task.md
```

Die kompatible Positionsschreibweise ist gleichwertig; verwende nicht beide Formen zugleich:

```bash
./run_task path/to/my-task.md
```

Eine nicht abgeschlossene `.orchestrator/state.json` wird im Einzelaufgabenmodus automatisch fortgesetzt. Nach einem abgeschlossenen State-v3-Lauf beginnt ein neuer Lauf. Verwende `--resume` explizit, wenn ein Gate aufgelöst oder nach einem Prozessneustart fortgesetzt wird.

## Informeller Inbox- und formaler Aufgabenbetrieb

Im normalen Inbox-Betrieb darf die menschliche Aufgabe bewusst informell bleiben. Freier Markdown-Text plus `TARGET_BRANCH: feature/<name>` oder `TARGET_BRANCH: codex/<name>` genügt. Wenn keine formalen Ausführungsmarker und kein Scope-Abschnitt vorhanden sind, erzeugt der Orchestrator deterministisch einen `PLAN_ONLY`-Vertrag: Der Dateiname wird zu einem ASCII-Slug normalisiert, der Arbeitsplan liegt unter `docs/internal/<slug>-arbeitsplan.md`, und nur dieser Planpfad ist im ersten Lauf beschreibbar. Codex übersetzt die Idee anhand des Repositorys in Slices, Pfade, Akzeptanzkriterien, Risiken und Validierung. Direkter Implementierungsscope wird niemals aus freier Prosa abgeleitet.

Sobald einer der formalen Marker `ORCHESTRATOR_MODE`, `WORK_PLAN_PATH`, `APPROVED_PLAN_COMMIT` oder `TASK_SCOPE` vorkommt, gilt die Datei als formaler Vertrag und muss vollständig sein. Eine formale produktive Aufgabe deklariert zusätzlich zu Ziel, Nicht-Scope und Akzeptanzkriterien diese maschinenlesbare Grenze:

```text
ORCHESTRATOR_MODE: PLAN_ONLY|IMPLEMENT
WORK_PLAN_PATH: docs/internal/<thema>-work-plan.md
APPROVED_PLAN_COMMIT: <automatisch erzeugter Git-Commit; nur im Handoff>
TARGET_BRANCH: feature/<name>|codex/<name>
TASK_SCOPE: <comma-separated repository-relative paths or globs>
```

`WORK_PLAN_PATH` ist bei `PLAN_ONLY` und im automatisch erzeugten Implementierungs-Handoff erforderlich. `APPROVED_PLAN_COMMIT` wird ausschließlich vom Handoff-Erzeuger zusammen mit den übernommenen `SLICE_PLAN`-Datensätzen geschrieben. Alternativ zu `TASK_SCOPE` wird ein Abschnitt `## Erlaubter Scope` oder `## Allowed Scope` mit Aufzählung akzeptiert. Im Einzelaufgabenmodus muss der angegebene Zielbranch vor dem Start existieren und aktiv sein. Im Watch-Modus bereitet der Orchestrator den Zielbranch beim ersten Start einer neuen Inbox-Aufgabe automatisch vor; die Agenten selbst dürfen Branches weiterhin weder erstellen noch wechseln.

`PLAN_ONLY` bildet intern den Planungsteil des automatischen Ablaufs ab; [example-plan-task.md](example-plan-task.md) ist nur für bewusst formale Planaufträge erforderlich. Codex erstellt ausschließlich das deklarierte Arbeitsplan-MD. Die späteren Umsetzungsslices stehen als Überschriften im Dokument, während der ausführbare `SLICE_PLAN` dieses Laufs genau einen Dokumentationsslice enthält. Vor dem Planreview prüft der Orchestrator bereits, ob jede Slice-Überschrift und jeder Abschnitt `**Exakter Änderungspfad**` einen gültigen Implementierungs-Handoff ergeben. Die kompatible Schreibweise `**Exakte Änderungspfade:**` wird ebenfalls gelesen. Scheitert dieser Vertrag, erhält Codex vor Claude automatisch genau einen gezielten Reparaturdurchlauf; ein weiterhin ungültiger Plan hält anschließend als nachvollziehbares, fortsetzbares Gate an. Claude prüft den Plan; im automatischen Standardpfad wird er danach lokal commitet und die `IMPLEMENT`-Aufgabe erzeugt. Im Watch-Modus wird diese neue Inbox-Aufgabe unmittelbar als Nächstes verarbeitet. `--plan-gate` schaltet eine zusätzliche menschliche Abnahme vor dem Plancommit ein.

Im Modus `IMPLEMENT` überführt Codex den Auftrag in einen oder mehrere persistierte Slices. Jeder ausführbare `SLICE_PLAN`-Datensatz enthält:

```text
SLICE_PLAN: <1-based id> | <summary> | <comma-separated repository-relative paths>
```

Die aufgeführten Pfade bilden exakte Commit-Allowlists. Ein Slice darf gemäß den konfigurierten Pfadklassen höchstens zehn produktive Dateigruppen enthalten. Tests und Dokumentation können separat klassifiziert werden; ein nicht klassifizierter Pfad gilt vorsichtshalber als produktiv.

Bereits bei der Planung werden alle `SLICE_PLAN`-Pfade gegen den Task-Scope geprüft. Unerwartete Pfade, ein geänderter Branch, ein geänderter Taskinhalt, ein geänderter Slice-Startcommit oder ein nach dem Review abweichender Fingerprint blockieren den Lauf.

## Zustand, Checkpoints, Logs und Auditdokumente

Laufzeitdaten werden unterhalb von `.orchestrator/` gespeichert:

| Pfad | Zweck |
|---|---|
| `.orchestrator/artifacts/<run-id>/records/ar1-<sha256>.json` | Autoritative, append-only Einzelrecords eines `structured-v2`-Laufs. |
| `.orchestrator/artifacts/<run-id>/head.json` | Aus den Records rekonstruierbarer Beschleunigungscache; keine Wahrheitsquelle. |
| `.orchestrator/state.json` | Atomarer State-v3-Betriebszustand; bei `structured-v2` ein gegen die Recordkette geprüfter Spiegel. |
| `.orchestrator/checkpoints/<run-id>/work-unit-####-slice-####-round-####.json` | Laufgebundene Fortsetzungs-Checkpoints mit einsbasierten Arbeitsblock-, Slice- und Rundenidentitäten. |
| `.orchestrator/logs/` | Rohe temporäre Agentenaufruf- und Diagnoselogs. |
| `.orchestrator/runs/<run_id>/work-unit-####-codex.md` | Persistierte Codex-Ausgabe zur Wiederherstellung des Planungs- oder Implementierungskontexts. |

State und Checkpoints dürfen nicht manuell bearbeitet werden.

Neue Workflows werden bei der Initialisierung unveränderlich an den Protokollmodus `structured-v2` sowie an die nativen, geschlossenen JSON-Verträge von Codex und Claude gebunden. Es gibt keine Transportflags mehr und ein Vertragsfehler fällt niemals auf Markertext zurück. Erst nach Vertragsprüfung, persistiertem Record und semantischem Gleichheitsnachweis zum State-v3-Spiegel darf der Inhalt eine Entscheidung steuern. Für alle in Records abgebildeten Fakten ist die validierte Recordkette die technische Source of Truth. Sie besteht aus kanonischen, digestgeprüften JSON-Einzeldateien und ist weder JSONL noch SQLite.

Historische `legacy-state-v3`- und `structured-v1`-Läufe werden mit `UNSUPPORTED-PROTOCOL` fail-closed abgewiesen. Es gibt weder stille Migration noch modusübergreifenden Fallback.

Beim Resume scannt der Orchestrator die vollständige Recordkette, rekonstruiert bei Bedarf `head.json` und vergleicht die spiegelbaren Fakten symmetrisch mit State-v3. Fehlende, unbekannte, beschädigte oder widersprüchliche Records sowie ein vorausgeeilter Spiegel stoppen fail-closed mit Record-ID beziehungsweise Lauf-ID und Reparaturhinweis. Zur Diagnose dienen die konkrete Fehlermeldung, `.orchestrator/logs/`, der betroffene Recordpfad und der State-Spiegel. Repariert wird durch Wiederherstellen der zusammengehörigen Recordkette oder des passenden Spiegels aus einer vertrauenswürdigen Sicherung – niemals durch manuelles Erfinden von Records, Freigaben oder Finding-Übergängen.

Menschenlesbare Plan- und Slice-Auditdateien im Markdown-Format gehören in das Zielrepository, üblicherweise unter `docs/internal/`, und werden mit ihrem Slice commitet. Für Aufgaben aus `inbox/` erzeugt der Orchestrator beim Taskstart automatisch ein digestgebundenes Gesamtdokument. Es sammelt Plan, Scope, Claude-Reviews, Findings, Validierungen, Slice-Entscheidungen und das abschließende Gesamtreview. Die zukünftigen Slice-Dokumentpfade werden nach der Planung automatisch in die persistierten Slice-Allowlists aufgenommen; die Dateien selbst entstehen jedoch erst beim tatsächlichen Beginn des jeweiligen Implementierungs- oder Korrekturslices. Eine abgelehnte oder vor Implementierungsbeginn abgebrochene Planung hinterlässt daher keine leeren Slice-Dokumente. Resume verwendet dieselben digestgebundenen Pfade idempotent weiter.

JSON ist dabei die autoritative Wahrheit, Markdown nur die deterministische Ansicht: `artifact_projection` rendert native Review- und Codex-Resultate einschließlich `transport_schema`, `request_id` und `response_sha256` direkt aus der validierten Recordkette. `audit_trail` übernimmt diese Abschnitte ohne Markdown zurückzulesen oder semantisch neu zu interpretieren. Die Rohantwort eines nativen Codex-Aufrufs liegt vor jeder fachlichen Anwendung unter `.orchestrator/artifacts/<run-id>/native-codex-responses/`; ein Record-ahead-Resume prüft Rohdigest, Requestbindung und AgentResult und startet Codex nicht erneut.

Jeder neue Codex–Claude-Lauf verwendet ohne zusätzliche CLI-Optionen den nativen JSON-Transport. Resume übernimmt exakt diese vollständige Bindung und kennt in keiner Plan-, Implementierungs-, Korrektur- oder Claude-Reviewphase einen Textfallback. Offene Findings und Codex-Dispositionen werden ausschließlich aus der validierten Recordkette rekonstruiert und vor jedem frischen Providerstart symmetrisch gegen den State-v3-Spiegel geprüft. Vollständige Record-ahead-Ergebnisse werden wiederverwendet, bevor ein neuer Agentenprozess gestartet werden darf. Die aus denselben Records erzeugte Markdownansicht enthält zusätzlich eine kompakte native Konvergenzübersicht mit Work-Unit, Runde, Fingerprints, Claude-Entscheidungen, Codex-Dispositionen und Endstatus; sie ist reine Anzeige und niemals Entscheidungs- oder Recoveryquelle.

Bei einem regulär manuell definierten Lauf außerhalb von `inbox/` müssen Auditdateien weiterhin vorbereitet, aus dem Arbeitsplan verlinkt, mit den erforderlichen verwalteten Auditabschnitten versehen und im Umfang des zugehörigen `SLICE_PLAN` enthalten sein. Ein commitgebundener Handoff erzeugt seine deklarierten Slice-Auditdateien ebenfalls automatisch vor dem jeweiligen Slice. Der Orchestrator projiziert strukturierte Findings, Reviews, Validierungsattestierungen und Autorisierungsstatus ausschließlich in die verwalteten Abschnitte. Diese Markdown-Dateien sind deterministische, menschenlesbare Auditansichten der Records und keine Resume- oder Reparaturquelle. Nach jedem lokalen Slice-Commit ist Git die historische Quelle der Wahrheit für den eingecheckten Repositorystand; nach der branchweiten Claude-Gesamtabnahme wird die abschließende Gesamtprojektion path-genau commitet.

Beim automatischen Plan-/Implementierungs-Handoff commitet eine freigegebene `PLAN_ONLY`-Aufgabe den bereits
geprüften Arbeitsplan unmittelbar; es folgt kein künstlicher Implementierungs-
oder Abschlussreview des Planartefakts. Anschließend erzeugt der Orchestrator
eine `-implement.md`-Handoff-Aufgabe neben der Planaufgabe. Sie bindet den
exakten Plan-Commit über `APPROVED_PLAN_COMMIT`, enthält die übernommenen
`SLICE_PLAN`-Grenzen und startet als neuer `IMPLEMENT`-Lauf direkt mit Slice 1.
Nachgelagerte Commits auf demselben Branch sind zulässig, sofern der gebundene
Plan-Commit ein Vorfahr von `HEAD` ist und die Plandatei seit der Freigabe
unverändert blieb.
Die zugehörigen Slice-Auditdateien werden aus diesen Grenzen vorbereitet und
mit dem jeweiligen Slice geprüft und commitet.

Aktive oder eingefrorene Zustände der Version 2 werden unverändert abgelehnt. Ein abgeschlossener Zustand der Version 2 bleibt als historischer Abschluss erkennbar, wird aber weder fortgesetzt noch stillschweigend nach State v3 migriert.

## Validierung und Reviewisolation

Nur der Orchestrator führt deterministische Validierungen aus. Planreviews verwenden eine interne Vertragsprüfung für Scope, Arbeitsplanpfad und 1-basierte zukünftige Slice-Überschriften; sie führen nicht die Produkttestsuite aus. Implementierungsreviews verwenden die aus kanonisch geänderten Pfaden und offenen Blockern ausgewählte Validierungsmatrix. Nur ein offener `BLOCKER` darf sie mit einem strukturierten `VALIDATE`-Befehl aus einer bereits konfigurierten Befehlsfamilie erweitern. Eine `OBSERVATION` bleibt als Hinweis und Abnahmetext erhalten, erweitert die Matrix aber nicht und kann den Lauf deshalb auch nicht wegen eines fremden Befehls anhalten. Attestierungen werden anhand des Diff-Fingerprints zwischengespeichert, und Claude erhält die gebundene Evidenz.

Codex arbeitet mit Schreibzugriff auf den Workspace. Claude erhält eine temporäre schreibgeschützte Repositorykopie, während seine privaten Laufzeit-, Prompt-, Cache- und Logpfade beschreibbar bleiben. Normale Reviews legen das Validierungssystem nicht offen und können den Ziel-Worktree nicht verändern.

Claude verwendet standardmäßig Sonnet mit Effort `high`. Der erste Slice-Review erhält die geänderten Pfade und Hunks des Slice, Akzeptanzkriterien, strukturierte Findings und die gebundene Attestierung. Ein Korrekturreview erhält ausschließlich das Delta seit Claudes zuletzt geprüftem Fingerprint. Eine rein formale Vertragsreparatur erhält die abgelehnte Antwort und den Marker-Vertrag, nicht erneut die Implementierungsevidenz.

Die expliziten Befehlsbuilder des Review-Harness dienen der Diagnose bei Installation, CLI-Versionswechseln oder Fehlersuche. Sie weisen Testausführung und Schreibschutz nachverfolgter Dateien in der isolierten Kopie nach; sie sind nicht Teil eines normalen Reviews.

## Gates, Findings und Fortsetzung

Der Workflow persistiert seinen Zustand, bevor er aus einem fortsetzbaren Halt zurückkehrt. Technische Ursachen werden behoben und anschließend fortgesetzt; nur ein tatsächlich entscheidungspflichtiges, fingerprintgebundenes Benutzergate wird ausdrücklich angenommen oder abgelehnt:

```bash
./run_task --resume --approve-gate \
  --gate-actor "Dieter" \
  --gate-rationale "Exakten persistierten Fingerprint geprüft und Fortsetzung freigegeben"
```

Mit `--reject-gate` und denselben Anforderungen an Akteur und Begründung wird eine Ablehnung protokolliert.

Die wichtigsten Gates sind:

- geänderte Tests ohne vorherige Autorisierung;
- ein optionales manuelles Gate vor jedem Slice-Commit;
- mehr als zehn produktive Änderungsgruppen;
- eine repositorydefinierte Stopregel oder ein Agentendatensatz `STOP_REQUESTED`;
- Pfade außerhalb des persistierten Slice-Umfangs;
- Abweichungen bei Branch, HEAD, Diff-Fingerprint oder Validierungsattestierung;
- fehlende oder nicht verfügbare Validierung;
- geänderte strukturierte Ankerwerte;
- vier Rückgaben des Implementierers in einem Arbeitsblock;
- fehlende Implementierungsänderungen;
- fehlerhafte, fehlende oder widersprüchliche Reviewurteile;
- Quota-, Authentifizierungs-, Binärprogramm-, Berechtigungs-, Netzwerk-, Prozess- oder Timeoutfehler.

Ein freigebender Slice-Review erfordert eine vollständige erfolgreiche Attestierung für denselben Fingerprint, scopegerechte Teständerungen, keinen reviewer-eigenen offenen Blocker, Reviewevidenz oder konkrete Findings sowie ein Pre-Mortem. Eine offene Observation darf während der Slice-Folge sichtbar bleiben. Der Orchestrator trägt den vollständigen Finding-Lebenszyklus in jeden folgenden Arbeitsblock; bei älteren fortgesetzten States werden zuvor je Slice wiederverwendete IDs deterministisch auf freie reviewer-eigene IDs abgebildet. Im branchweiten Abschlussreview muss Claude jedes eigene offene `C-*`-Finding schließen oder zu einem Blocker hochstufen. Neue reine Observations sind dort unzulässig: nicht umsetzungsrelevante Ideen und Restrisiken gehören in `REVIEW_EVIDENCE`, handlungsbedürftige Defekte werden Blocker und erzeugen automatisch einen Korrekturslice. Testdateien werden weiterhin im Slice-Report ausgewiesen, vollständig validiert und von Claude geprüft; ein zusätzliches menschliches Teständerungs-Gate ist nur mit `--test-change-gate` aktiv. Nur der Reviewer, der ein Finding gemeldet hat, darf es schließen oder neu klassifizieren. Ein versehentlich mit `VALIDATE:` beginnender Abnahmetest einer Observation wird mit Warnung ignoriert; bei einem Blocker bleiben fehlerhafte oder nicht konfigurierte Befehle fail-closed.

Reviewer arbeiten in einem temporären schreibgeschützten Snapshot. Dieser enthält nur Git-sichtbare Quell- und Dokumentationsdateien; Metadaten, Abhängigkeiten und generierte Schwergewichte wie `.git`, `.orchestrator`, `node_modules`, `dist` und Releasearchive werden nicht kopiert. Reine Ausgabevertragskorrekturen erhalten ein leeres schreibgeschütztes Arbeitsverzeichnis. Eindeutig gebundene Formalmarker werden lokal ergänzt, ohne einen zweiten Modellreview auszulösen.

Formuliert ein Reviewer `REVIEW_EVIDENCE` mit den eindeutigen Bezeichnungen
`Largest residual risk:` und `Break condition:` statt mit den vorgeschriebenen
Pipe-Trennzeichen, überführt der Orchestrator genau diese vorhandenen drei
Inhalte lokal und verlustfrei in das Vertragsformat. Mehrdeutige oder bereits
Pipe-haltige Varianten bleiben unverändert und durchlaufen die normale formale
Reparatur; Präambeln werden nicht abgeschnitten.

Umschließt ein Provider die vollständige Vertragsantwort ausschließlich mit
einem Markdown-Codezaun (wahlweise mit Sprachangabe `text`), entfernt der
Adapter nur diesen äußeren Zaun. Das gilt ausschließlich, wenn der Inhalt mit
`REVIEWER:` beginnt und mit `STATUS: DONE` endet; Text vor oder nach dem Zaun
verhindert das Entpacken und scheitert weiterhin an der strikten
Vertragsprüfung.

Die Quotabehandlung erfolgt rollenspezifisch. Bei aktivierter automatischer Quotafortsetzung wird ein eindeutiger Reset innerhalb der konfigurierten Wartegrenze persistiert, unter Ausgabe von Heartbeats abgewartet und am exakt fehlgeschlagenen Schritt einmal fortgesetzt. Neben Zeitstempeln, relativen Angaben und ausdrücklich benannten Zeitzonen wird auch Codex' englische Datumsangabe wie `Aug 20th, 2026 5:36 AM` erkannt; da sie selbst keine Zeitzone enthält, wird sie ausschließlich für Codex in der lokalen IANA-Zeitzone des Orchestrator-Rechners ausgewertet. Andernfalls endet der Prozess mit Exitcode 2 und bleibt fortsetzbar. Es gibt keine Ersatzrolle. Ändert sich das Repository während einer Quota-Pause, erzeugt die Fortsetzung ein `QUOTA-RESUME-DIFF`-Gate für den aktuellen Fingerprint und die betroffenen Pfade. Ein weiteres gewöhnliches `--resume` genehmigt diese Änderung bewusst nicht; erst `--resume --approve-gate` mit Akteur und Begründung setzt denselben Rollenschritt fort.

## Lokale Commits und externe Git-Aktionen

Nachdem Claude den Slice-Fingerprint freigegeben hat, führt der Orchestrator folgende Schritte aus:

1. Repositorystatus und kanonischen Diff erneut ermitteln;
2. Branch, Slice-Grenze, erlaubte Pfade, Reviews, Findings und Validierungsattestierung prüfen;
3. ausschließlich die exakt geprüften Pfade stagen;
4. einen lokalen Commit `Slice NN: <planned summary>` erstellen, wobei Hooks und Signierung für die mechanische Transaktion deaktiviert sind;
5. Pfadliste und resultierenden Commit-Hash verifizieren.

Der Orchestrator pusht, mergt oder force-pusht niemals und schreibt die Historie nicht um. Diese Aktionen bleiben explizite Benutzervorgänge außerhalb des Workflows.

## Watch-Modus

Der Orchestrator kann als FIFO-Warteschlangenworker ausgeführt werden:

```bash
./run_task --watch
```

Der Watch-Modus:

- überwacht stabile `*.md`-Dateien in `inbox/`, älteste zuerst;
- akzeptiert informelle Ideen mit `TARGET_BRANCH` und leitet daraus automatisch einen eng begrenzten `PLAN_ONLY`-Auftrag ab;
- liest `TARGET_BRANCH` aus der Aufgabe und legt diesen Branch beim ersten Start an oder wechselt auf einen bereits vorhandenen Branch;
- erweitert einen bereits aktiven Zielbranch ab dessen aktuellem `HEAD`, sodass frühere Branch-Commits nicht erneut zum Diff der neuen Aufgabe gehören;
- verweigert einen erforderlichen Branchwechsel bei nicht ignorierten Arbeitsbaum- oder Indexänderungen, ohne Dateien zu stashen, zu bereinigen oder zu übernehmen;
- wechselt bei einem Resume mit vorhandenem Workflow-State niemals automatisch den Branch; weicht der aktive Branch vom persistierten Zielbranch ab, stoppt der bestehende `BRANCH-MISMATCH`-Gate;
- hält eine Einzelprozesssperre `inbox/.lock`, sofern `fcntl` verfügbar ist;
- weist jeder Aufgabe eine persistierte Lauf-ID und einen Digest des Aufgabeninhalts zu;
- aktiviert standardmäßig `--skip-git-check`, weil geprüfte Slice-Commits den Worktree absichtlich verändern;
- verwendet den vollständig automatischen Workflowstandard: Plan-, Teständerungs- und Slice-Commit-Gates sind aus, während echte Stopregeln, Scopeverletzungen, unauflösbare Vertragsfragen und fehlgeschlagene Pflichtvalidierungen weiterhin anhalten;
- behandelt einen von Codex gemeldeten agentenlokalen `listen`-/Port-Bind-Fehler einmal automatisch als Sandboxgrenze, fordert die normale Readiness erneut an und lässt anschließend die autoritative Validierungsmatrix im Orchestrator laufen;
- verarbeitet nach dem automatisch geprüften und lokal committeten Plan dessen neu erzeugte `-implement.md` als nächste Inbox-Aufgabe und arbeitet alle Slices bis zum Codex-Vollständigkeitscheck und Claude-Abschlussreview ab;
- legt die einzelnen Slice-Auditdokumente erst beim tatsächlichen Beginn des jeweiligen Slices an und sammelt alle Plan-, Review-, Finding-, Validierungs- und Abschlussdaten zusätzlich im digestgebundenen Gesamtaudit;
- streamt standardmäßig `stdout`;
- verschiebt abgeschlossene Aufgaben mit UTC-Zeitstempel nach `outbox/done/`;
- wiederholt technische Fehler und verschiebt ausgeschöpfte Aufgaben als Poison Tasks nach `outbox/failed/`; daneben bleibt eine gleichnamige `.error.json` mit Lauf-ID, Step und letzter technischer Ursache erhalten;
- hält die Warteschlange bei Exitcode 2, 3 oder 4 an, damit die erste fortsetzbare Aufgabe ihre FIFO-Zuständigkeit behält;
- führt eine erfolgreich abgeschlossene Aufgabe nicht erneut aus, wenn nur das Verschieben in die Outbox wiederholt werden muss.

Die standardmäßig ignorierte `inbox/` ist damit der vorgesehene Ablageort für neue Aufgaben: Die Task-Datei und ihre ungetrackten Watch-Sidecars bleiben bei einem Branchwechsel erhalten. Auch ein benutzerdefiniertes, nicht ignoriertes Inbox-Verzeichnis funktioniert, solange diese Kontrollpfade ungetrackt sind. Getrackte Task-Kontrollpfade sowie sonstige vorbestehende Änderungen auf einem anderen Branch werden nie automatisch mitgenommen; sie müssen vor einem erforderlichen Wechsel vom Benutzer geklärt werden.

Es darf immer nur ein Watcher auf demselben Repository arbeiten. Wo `fcntl` nicht verfügbar ist, kann der Orchestrator die Einzelprozesssperre nicht selbst erzwingen; konkurrierende Git-Operationen brechen dann zwar sicher als Git-Fehler ab, werden aber nicht automatisch wiederholt.

Verzeichnisse, Abfrageintervall oder Anzahl technischer Wiederholungen können überschrieben werden:

```bash
./run_task --watch \
  --inbox-dir /path/to/inbox \
  --outbox-dir /path/to/outbox \
  --poll-interval 2 \
  --watch-max-retries 3
```

Nach Behebung einer angehaltenen Watch-Aufgabe wird der Watcher neu gestartet. Die Identitäts-Sidecar-Datei der Aufgabe setzt denselben Lauf und Arbeitsblock fort.

## Probeläufe

Der integrierte Probelauf durchläuft Planfreigabe, zwei Slice-Commits und Abschlussreview ohne Agenten-/API-Aufrufe oder Repositoryschreibzugriffe:

```bash
./run_task --dry-run --task-file example-task.md --quiet
```

Für deterministische Negativ- und Fortsetzungsszenarien kann ein State-v3-JSON-Szenario übergeben und optional dessen Auditbericht geschrieben werden:

```bash
./run_task \
  --dry-run-scenario path/to/scenario.json \
  --dry-run-report path/to/report.json \
  --task-file example-task.md
```

## CLI-Referenz

`src/cli.py` ist die Quelle der Wahrheit für das Argument-Parsing. `run_task` ist ein Kompatibilitätsstarter, der diese Datei findet und alle Argumente weiterleitet.

### Kern- und Zustandsoptionen

| Schalter | Standard | Beschreibung |
|---|---|---|
| `[task-file]` | `task.md` | Kompatible Positionskurzform für die Aufgabendatei. |
| `--task-file <path>` | `task.md` | Expliziter Aufgabenpfad; nicht mit der Positionsform kombinierbar. |
| `--config <path>` | `RUN_TASK_CONFIG` oder `./orchestrator.toml` | Repositoryrichtlinien-Konfiguration. |
| `--agents-file <path>` | `AGENTS.md` des Repositorys | Gemeinsame Agentenanweisungen, die in Prompts eingefügt werden. |
| `--resume` / `--no-resume` | automatisch | Nicht abgeschlossenen Einzelaufgabenzustand automatisch fortsetzen; bei Bedarf explizit überschreiben. |
| `--force-overwrite-state` | automatisch bei abgeschlossenem Zustand | Trotz vorhandenen Zustands einen neuen Lauf beginnen; explizite Verwendung umgeht den normalen Zustandsschutz. |
| `--strict-preflight` | aus | Einen Fehler der Provider-DNS-Vorabprüfung als fatal behandeln. |
| `--skip-git-check` / `--no-skip-git-check` | aus; im Watch-Modus an | Prüfung auf einen sauberen Repositoryzustand überschreiben. |
| `--manual-slice-gate` / `--no-manual-slice-gate` | Repositorykonfiguration oder aus | Vor jedem Slice-Commit eine explizite Freigabe verlangen. |
| `--plan-gate` / `--no-plan-gate` | Repositorykonfiguration oder aus | Nach Claude-Planfreigabe eine explizite fingerprintgebundene Benutzerfreigabe vor dem Plancommit verlangen. |
| `--test-change-gate` / `--no-test-change-gate` | Repositorykonfiguration oder aus | Vor Review und Commit eines Slices mit Testdateiänderungen eine zusätzliche fingerprintgebundene Benutzerfreigabe verlangen. Ohne Gate bleiben Scopeprüfung, Tests und der Claude-Review verpflichtend. |
| `--plan-only` / `--no-plan-only` | Aufgabenmarker oder nicht gesetzt | Den Lauf auf das deklarierte Arbeitsplanartefakt begrenzen beziehungsweise explizit als Implementierung ausführen. |
| `--work-plan <path>` | Aufgabenmarker | Exakter repositoryrelativer `WORK_PLAN_PATH` für `PLAN_ONLY`; darf dem Marker nicht widersprechen. |
| `--target-branch <branch>` | Aufgabenmarker | Exakter erforderlicher Feature-Branch; darf dem Marker nicht widersprechen. |
| `--approve-gate` / `--reject-gate` | nicht gesetzt | Zusammen mit explizitem `--resume` über das exakt persistierte Benutzergate entscheiden. |
| `--gate-actor <name>` | nicht gesetzt | Erforderliche Identität für eine explizite Gate-Entscheidung. |
| `--gate-rationale <text>` | nicht gesetzt | Erforderliche Begründung für eine explizite Gate-Entscheidung. |

### Validierung, Probelauf und Quota

| Schalter | Standard | Beschreibung |
|---|---|---|
| `--test-command <cmd>` | Umgebung, Repositorymatrix oder Erkennung | Kompatibilitäts-Validierungsbefehl; eine explizite leere Zeichenfolge deaktiviert ihn. |
| `--retry-incomplete-validation` | aus | Eine zwischengespeicherte `INCOMPLETE`-Matrix für denselben Fingerprint nach Reparatur der Umgebung erneut ausführen. |
| `--retry-failed-validation` | aus | Eine zwischengespeicherte rote Matrix für denselben Fingerprint pro Prozessaufruf ausdrücklich erneut ausführen. |
| `--dry-run` | aus | Das integrierte State-v3-Erfolgsszenario ohne API-Aufrufe oder Schreibzugriffe ausführen. |
| `--dry-run-scenario <path>` | nicht gesetzt | Ein deterministisches JSON-Szenario ausführen. |
| `--dry-run-report <path>` | nicht gesetzt | Den Auditbericht des skriptgesteuerten Szenarios schreiben. |
| `--quota-auto-resume` / `--no-quota-auto-resume` | an | Eine automatische Fortsetzung bei eindeutigem Reset aktivieren. |
| `--quota-safety-margin <seconds>` | `60` | Nach einem erkannten Reset zusätzlich zu wartende Zeit. Das Reset-Ereignis wird sofort geloggt; eine große Marge wartet danach erwartungsgemäß als ein einzelner Abschnitt ohne periodischen Heartbeat und vergrößert entsprechend die heartbeatlose Dauer. |
| `--quota-max-wait <seconds>` | `604800` | Maximale automatische Provider-Resetspanne; die Sicherheitsmarge wird erst danach addiert. |
| `--quota-max-auto-resumes <count>` | `1` | Automatische Fortsetzungen je blockiertem Rollenschritt. |
| `--quota-heartbeat-interval <seconds>` | `3600` | Heartbeat-Intervall bis zum Provider-Reset; die Sicherheitsmarge erzeugt keine periodischen Heartbeats. |
| `--transient-retry-auto` / `--no-transient-retry-auto` | an | Eindeutig technische Netzwerkfehler desselben Rollenschritts automatisch wiederholen. |
| `--transient-retry-initial-delay <seconds>` | `5` | Wartezeit vor dem ersten transienten Netzwerk-Neuversuch. |
| `--transient-retry-max-delay <seconds>` | `30` | Obergrenze für die exponentielle Wartezeit. |
| `--transient-retry-max-auto-resumes <count>` | `2` | Maximale automatische Netzwerk-Neuversuche je Rollenschritt. |

Die Quota-Wartepolitik kann entsprechend über
`RUN_TASK_QUOTA_AUTO_RESUME`, `RUN_TASK_QUOTA_SAFETY_MARGIN`,
`RUN_TASK_QUOTA_MAX_WAIT`, `RUN_TASK_QUOTA_MAX_AUTO_RESUMES` und
`RUN_TASK_QUOTA_HEARTBEAT_INTERVAL` gesetzt werden. Explizite CLI-Werte haben
Vorrang vor diesen Umgebungsvariablen; danach gelten die Tabellenstandards.

### Agentenausgabe und Rollenkonfiguration

| Schalter | Standard | Beschreibung |
|---|---|---|
| `--agent-output <none\|summary\|full>` | `none` | Umfang der auszugebenden abgeschlossenen Agentenantworten. |
| `--agent-output-max-chars <count>` | `1800` | Maximale Zeichenanzahl abgeschlossener Antworten im Zusammenfassungsmodus. |
| `--agent-live-stream` / `--no-agent-live-stream` | an | Live-Prozessausgabe aktivieren oder deaktivieren. |
| `--agent-live-stream-mode <compact\|full>` | `compact` | `compact` zeigt lesbaren Codex-Fortschritt sowie Findings, Entscheidungen, Laufzeit und eine kurze Nutzungssumme ohne Provider-JSON; `full` zeigt die unveränderte Provider-Ausgabe. |
| `--agent-live-stream-channels <both\|stdout\|stderr>` | Umgebung oder `stdout` | Auszugebende Live-Kanäle. |

Rolleneinstellungen verwenden zuerst CLI-Werte, dann `RUN_TASK_<ROLE>_*` und anschließend diese persistenten Standards:

| Rolle | CLI-Optionen | Standards |
|---|---|---|
| Codex | `--codex-binary`, `--codex-model`, `--codex-timeout`, `--codex-effort` | `codex`, `gpt-5.6-sol`, 1800s, `medium` |
| Claude | `--claude-binary`, `--claude-model`, `--claude-timeout`, `--claude-effort` | `claude`, `sonnet`, 1800s, `high` |

`--claude-max-budget-usd` oder `RUN_TASK_CLAUDE_MAX_BUDGET_USD` ergänzt eine optionale Budgetobergrenze für den Print-Modus. Opus ist nicht der Standard; `--claude-model opus` dient ausschließlich einer expliziten Eskalation.

Beispiele:

```bash
./run_task --claude-model sonnet --claude-effort high
./run_task --codex-binary /opt/codex/bin/codex --codex-timeout 2400
```

### Watch- und Loggingoptionen

| Schalter | Standard | Beschreibung |
|---|---|---|
| `--watch` | aus | Markdown-Aufgaben aus der Inbox fortlaufend verarbeiten. |
| `--inbox-dir <path>` | `inbox` | Eingabeverzeichnis des Watch-Modus. |
| `--outbox-dir <path>` | `outbox` | Stammverzeichnis für abgeschlossene und fehlgeschlagene Aufgaben. |
| `--poll-interval <seconds>` | `5.0` | Abfrageintervall der Inbox. |
| `--watch-max-retries <count>` | `3` | Anzahl technischer Fehler vor der Poison-Task-Behandlung. |
| `--verbose` | aus | Debug-Logging aktivieren. |
| `--quiet` | aus | Nur Warnungen und Fehler anzeigen. |

`--verbose` und `--quiet` schließen einander aus.

## Konfiguration

Die Konfigurationspräzedenz lautet:

1. expliziter CLI-Wert;
2. passende Umgebungsvariable `RUN_TASK_*`;
3. Wert aus `orchestrator.toml` des Repositorys;
4. integrierter Standard oder automatische Erkennung des Testbefehls.

Werte für Agentenprogramm, Modell, Effort, Timeout und Claude-Budget umgehen bewusst das Repository-TOML und verwenden ausschließlich CLI, Umgebung und Rollenstandards.

Ein explizit leerer Testbefehl deaktiviert die Erkennung eines Validierungsbefehls:

```bash
./run_task --test-command ""
RUN_TASK_TEST_CMD="" ./run_task
```

Ohne deklarierten Validierungsbefehl prüft die Erkennung zunächst `pyproject.toml` mit pytest-Konfiguration, danach ein `package.json`-Testskript und schließlich ein `test`-Target im Makefile.

Das TOML-Schema des Repositorys enthält ausschließlich portable Richtlinien:

```toml
[paths]
productive = ["src/**/*.py", "run_task", "*.toml"]
tests = ["tests/**"]
documentation = ["docs/**", "*.md"]
generated = [".orchestrator/**", "**/__pycache__/**", ".pytest_cache/**"]

[[stop_rules]]
id = "DOMAIN-001"
description = "Stop when the named domain invariant changes."

[validation]
default_command = ["python3", "-m", "pytest", "tests/", "-v"]
default_timeout_seconds = 1800

[[provider_input_budget]]
provider = "codex"
role = "codex"
operation = "codex_implementation"
max_chars = 4000000
max_bytes = 16000000

[[validation.rules]]
patterns = ["frontend/**"]
command = ["npm", "test"]
timeout_seconds = 1200

[workflow]
manual_slice_gate = false
plan_gate = false
test_change_gate = false
```

`default_shell_command` oder ein regelbezogener `shell_command` sollten nur verwendet werden, wenn Shell-Semantik erforderlich ist. Ein Validierungseintrag darf nicht sowohl einen Argumentvektorbefehl als auch einen Shell-Befehl enthalten. Muster sind repositoryrelativ, verwenden `/` und dürfen nicht mit `..` ausbrechen.

`provider_input_budget` ist eine geschlossene Tabelle für jede unterstützte Kombination aus Provider, Rolle und Operation; [orchestrator.toml](orchestrator.toml) zeigt die vollständige Liste. Zeichen und UTF-8-Bytes werden nach der verlustfreien Adapterserialisierung und vor Capabilityprüfung oder Providerprozess separat gemessen. Gleichheit mit dem positiven Sicherheitsbudget ist erlaubt, jede Überschreitung hält lokal an. Technische Providerlimits bleiben `null`, solange keine belastbare, versionierte Quelle einen Zeichen- und Bytewert belegt; ein unbekanntes Limit erweitert das Sicherheitsbudget nicht. Auditprojektion und Logs enthalten nur Größen, Grenzwerte, Komponentennamen, Überhang, Fehlercode und Bindungsdigests, niemals Prompttext, Secrets, Umgebungen oder vollständige Kommandozeilen.

Nützliche Umgebungsüberschreibungen sind:

```bash
RUN_TASK_TEST_CMD="python3 -m pytest tests/ -v" ./run_task
RUN_TASK_SKIP_GIT_CHECK=0 ./run_task --watch
RUN_TASK_WATCH_STREAM_CHANNELS=both ./run_task --watch
RUN_TASK_QUOTA_AUTO_RESUME=0 ./run_task
RUN_TASK_TRANSIENT_RETRY_AUTO=0 ./run_task
RUN_TASK_TRANSIENT_RETRY_INITIAL_DELAY=5 RUN_TASK_TRANSIENT_RETRY_MAX_DELAY=30 \
  RUN_TASK_TRANSIENT_RETRY_MAX_AUTO_RESUMES=2 ./run_task
```

Technische Provider-Envelopes und Agentenantworten werden getrennt ausgewertet. Wörter
wie `network`, `quota` oder `auth` innerhalb einer fachlichen Reviewantwort dürfen daher
keine technische Fehlerklasse auslösen. Automatische transiente Neuversuche gelten nur
für belegte Netzwerkdiagnosen; Auth-, Runtime-, Output- und Prozessfehler halten weiterhin
fortsetzbar an.

Vor jedem branchweiten Provideraufruf prüft ein lokales, agentenfreies Preflight außerdem Recordkette, State-v3-Spiegel, Findingzustände, aktuelle Validierungsattestierung, autorisierte Pfade und die unmittelbar zuvor persistierte Eingabemessung. Ein Budget- oder Preflightdenial ist kein Providerfehler: Der unveränderte Rollenstep bleibt mit `bootstrap_check` und Exitcode 4 resumierbar. Im Watchbetrieb steigen weder Attempt-Zähler noch entstehen `.poison`-Dateien oder automatische Providerretries. Nach Korrektur von Konfiguration, Record-/State-Spiegel oder Repositoryzustand wird derselbe Auftrag mit `run_task --watch` beziehungsweise `run_task --resume --task-file ...` erneut geprüft; dafür ist keine `--approve-gate`-Entscheidung zulässig oder nötig.

## Agentenanweisungen und Ausgabevertrag

Die aktiven Anweisungsdateien des Repositorys sind:

| Datei | Verantwortung |
|---|---|
| `AGENTS.md` | Gemeinsamer Ausführungs-, Sicherheits-, Review- und Marker-Vertrag. |
| `CODEX.md` | Implementiererrolle und Bereitschaftsdatensätze. |
| `CLAUDE.md` | Primärer gezielter Reviewer mit persistentem Sonnet-/High-Profil. |

Alle Agentenantworten enden mit `STATUS: DONE`. State-v3-Datensätze sind:

| Erzeuger oder Schritt | Erforderlicher Datensatz |
|---|---|
| Codex-Plan | `SLICE_PLAN: <id> \| <summary> \| <paths>` und `PLAN_READY: YES\|NO` |
| Codex-Implementierung | `TEST_FILES_TOUCHED: NONE\|<paths>` und `IMPLEMENTATION_READY: <slice-id> \| YES\|NO` |
| Codex-Abschlussbericht | `FINAL_REPORT_READY: YES\|NO` |
| Reviewer, erste Zeile | `REVIEWER: claude` |
| Claude-Planreview | `PLAN_APPROVAL: YES\|NO` |
| Slice-Review | `SLICE_APPROVAL: <slice-id> \| YES\|NO` |
| Branchweiter Abschlussreview | `FINAL_APPROVAL: YES\|NO` |
| Neues Finding | `NEW_FINDING: C-01 \| BLOCKER\|OBSERVATION \| <description> \| <acceptance test>` |
| Aktualisierung durch Finding-Eigentümer | `FINDING_STATUS: <id> \| OPEN\|CLOSED \| <rationale>` |
| Optionale Neuklassifizierung durch Eigentümer | `FINDING_RECLASSIFIED: <id> \| BLOCKER\|OBSERVATION \| <rationale>` |
| Finding-Antwort von Codex | `FINDING_RESPONSE: <id> \| ACCEPTED\|REJECTED \| <rationale>` |
| Review ohne konkrete Schwachstelle | `REVIEW_EVIDENCE: <dimensions> \| <largest residual risk> \| <break condition>` |
| Voraussetzung einer positiven Freigabe | `PRE_MORTEM: <most likely failure cause in three months>` |
| Stopp durch beliebige Rolle | `STOP_REQUESTED: <rule-id> \| <rationale>` anstelle von Bereitschaft oder Freigabe |
| Automatische Vorgängerslice-Reparatur durch Codex | zusätzlich `REMEDIATION_PATHS: <comma-separated exact paths>` bei einem rein technischen, bereits planfreigegebenen Scope-Rückläufer |

Der Orchestrator besitzt die Validierungsattestierungen; Agenten dürfen `VALIDATION_RESULT` nicht ausgeben. State-v2-Freigabe- und aggregierte Finding-Marker sind ungültig.

## Exitcodes

| Code | Bedeutung |
|---:|---|
| `0` | Der vollständige Workflow einschließlich aller Slice-Commits und des branchweiten Abschlussreviews wurde erfolgreich abgeschlossen. |
| `1` | Technischer, Konfigurations-, Zustandsschema-, Repository- oder interner Workflowfehler. |
| `2` | Die Quota kann nicht automatisch fortgesetzt werden oder die konfigurierte Wartepolitik ist ausgeschöpft. |
| `3` | Eine erforderliche Agenteninstanz ist fehlgeschlagen, hat ihr Timeout erreicht oder ist nicht verfügbar. |
| `4` | Eine Benutzerentscheidung oder ein Richtlinien-Gate ist erforderlich. |

Die Codes 2, 3 und 4 erhalten einen fortsetzbaren Zustand. Prüfe den protokollierten Gate-Grund, behebe oder entscheide ihn und setze denselben Lauf mit `--resume` fort.

## Optionaler globaler Befehl

Damit der Starter aus anderen Repositorys aufgerufen werden kann:

```bash
mkdir -p ~/.local/bin
ln -s /absolute/path/to/Dual-Agent-Orchestrator/run_task ~/.local/bin/run_task
chmod +x /absolute/path/to/Dual-Agent-Orchestrator/run_task
```

## Verifikation

```bash
./run_task --help
./run_task --dry-run --task-file example-task.md --quiet
python3 -m pytest tests/ -v
```
