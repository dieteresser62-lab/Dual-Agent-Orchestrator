# Marktvergleich

**Dokumentstatus:** Produktvergleich zu einem festen Zeitpunkt

**Recherchestand:** 2026-08-13

**Orchestrator-Funktionsstand:** 2026-08-16

**Evidenzrichtlinie:** ausschließlich offizielle Produktseiten und Dokumentationen

## 1. Zusammenfassung

Der Dual-Agent Task Orchestrator besetzt eine engere Kategorie als die meisten Produkte in diesem Vergleich. Codex, Claude Code, Google Antigravity, GitHub Copilot, Cursor, OpenHands und aider stellen primär einen Agenten, einen Agenten-Workspace, eine Entwicklungsoberfläche oder eine Agentenplattform bereit. Dieses Projekt ist eine lokale Workflow-Steuerungsebene, die drei dieser Agentenoberflächen in festen Rollen aufruft und deterministische Evidenz, asymmetrische unabhängige Reviews, fortsetzbare Gates und eine exakte lokale Commit-Autorisierung ergänzt.

Sein stärkstes Alleinstellungsmerkmal ist die Kombination aus:

- benannter providerübergreifender Trennung zwischen Implementierer und zwei Reviewern;
- kanonischer Git-Evidenz und Reviewentscheidungen, die an exakte SHA-256-Diff-Fingerprints gebunden sind;
- Orchestrator-eigener Validierung, die von den Reviewern wiederverwendet wird;
- schreibgeschützten Reviewer-Workspaces;
- persistierter Finding-Zuständigkeit und exakter Schrittfortsetzung;
- pfadbegrenzten, lokal verifizierten Plan-, Slice- und Korrekturcommits ohne Push- oder Merge-Berechtigung;
- einem menschenfreundlichen Inbox-Intake, der freie Prosa plus Zielbranch zunächst in einen unabhängig geprüften Arbeitsplan überführt;
- einem vollständig automatischen Standardpfad vom Plan über alle Slices bis zum dreifachen Abschlussreview, während echte Produkt- und Richtlinienentscheidungen weiterhin sicher anhalten.

Diese Kontrolle bringt bewusste Nachteile mit sich. Der Orchestrator besitzt keine IDE, keine gehostete Ausführungsflotte, keinen Browseragenten, keine Pull-Request-Oberfläche, keinen Modellmarktplatz, keine verteilte Warteschlange und keine parallele Slice-Ausführung. Mehrere Marktprodukte sind in diesen Bereichen deutlich stärker.

## 2. Lesart des Vergleichs

Dies ist kein Benchmark für Modellintelligenz, Qualität des generierten Codes, Preis oder Geschwindigkeit. Diese Eigenschaften ändern sich schnell und erfordern kontrollierte Arbeitslasten. Der Vergleich untersucht stattdessen, welche Workflowfähigkeiten in aktuellen offiziellen Dokumentationen ausdrücklich beschrieben werden.

In den Matrizen werden folgende Begriffe verwendet:

- **Integriert:** Die zitierte Dokumentation beschreibt die Fähigkeit als Produktfunktion.
- **Konfigurierbar:** Die Fähigkeit lässt sich mit Produktkonfiguration, Hooks, SDKs oder separaten Produktfunktionen zusammensetzen, ist aber nicht die hier verglichene Standardtopologie.
- **Nicht nachgewiesen:** Die geprüften offiziellen Quellen belegen die konkrete Fähigkeit nicht. Das beweist nicht, dass sie unmöglich ist.
- **Anderer Scope:** Das Produkt löst ein verwandtes Problem auf einer anderen Ebene.

Produktnamen beziehen sich auf die in den verlinkten Quellen beschriebenen Oberflächen, nicht auf jede unter derselben Marke angebotene Fähigkeit.

## 3. Vergleichsprodukte

| Produkt oder Oberfläche | Primäre Kategorie | Grund für die Vergleichbarkeit |
|---|---|---|
| Dual-Agent Task Orchestrator | Lokale Multi-Agenten-Workflow-Steuerung | Referenz: begrenzte Implementierung, Review, Validierung, Fortsetzung und Git-Transaktion. |
| OpenAI Codex | Lokaler/Cloud-Coding-Agent und Multi-Agenten-Kommandozentrale | Stellt den hier verwendeten Implementierer bereit und unterstützt parallele Agentenarbeit. |
| Claude Code Agent Teams | Terminalzentrierte Multi-Agenten-Entwicklungsumgebung | Stellt den hier verwendeten primären Reviewer bereit und unterstützt explizite Agentenkoordination. |
| Google Antigravity 2.0 | Eigenständige Agenten-Kommandozentrale und CLI-/IDE-Ökosystem | Stellt den hier verwendeten unabhängigen Reviewer bereit und unterstützt Projekte, Worktrees und Subagenten. |
| GitHub Copilot Cloud Agent und Code Review | GitHub-native Coding- und Reviewdienste | Automatisiert Issue-zu-PR-Arbeit und Reviews innerhalb der Hostingplattform. |
| Cursor Cloud Agents | Gehostete Coding-Agenten-Ausführung und PR-Workflow | Bietet parallele Cloud-VMs, umfangreiche Artefakte, Integrationen und Freigabewerkzeuge. |
| OpenHands | Open-Source-Agenten-SDK, Runtime, CLI und Cloudplattform | Bietet modellunabhängige lokale, selbst gehostete und Cloud-Agenteninfrastruktur. |
| aider | Lokaler Terminal-Pair-Programmer | Bietet einen schlanken Multi-Modell-Bearbeitungsworkflow mit enger Git-Integration. |

## 4. Betriebsmodell-Matrix

| Produkt | Ausführungsoberfläche | Ort der Laufzeit | Agenten-/Modelltopologie | Parallele Arbeit | Primäre Git-Übergabe |
|---|---|---|---|---|---|
| Dual-Agent Orchestrator | Python-CLI und FIFO-Watcher | Ziel-Worktree plus temporäre lokale Reviewerkopien | Feste Rollen Codex → Claude → Antigravity; Rollen-CLIs unabhängig konfiguriert | Slices sequenziell; providerinterne Parallelität außerhalb seiner Kontrolle | Ausschließlich verifizierte lokale Slice-Commits |
| OpenAI Codex | CLI, IDE, Desktop-App und Cloudaufgaben | Lokale Sandbox oder isolierte Cloudumgebungen | OpenAI-Coding-Agenten; mehrere isolierte Threads/Worktrees | Aufgabenübergreifend integriert | Cloud-Commit, lokaler Checkout oder Pull Request |
| Claude Code Agent Teams | Terminal, Desktop, IDE, Web und Automatisierungsoberflächen | Primär lokale Sitzungen; Web-/Cloudoberfläche ebenfalls verfügbar | Claude-Teamlead, Teammates und Subagenten mit getrennten Kontexten | Integriert; gemeinsame Aufgabenliste und direkte Nachrichten | Repositoryänderungen im Git-Workflow des Benutzers |
| Google Antigravity 2.0 | Eigenständige App, CLI und IDE-Ökosystem | Projektbezogene lokale/Worktree-Ausführung plus Managed-Agent-Optionen | Mehrere Unterhaltungen, dynamische Subagenten, Custom Agents, Skills und MCP | Projekt- und subagentenübergreifend integriert | Projekt-/Worktree-Änderungen; externe SCM-Aktionen abhängig von der Oberfläche |
| GitHub Copilot Cloud Agent | GitHub-Issue/-PR und verbundene Entwicklungsoberflächen | Temporäre GitHub-Actions-Umgebung | Copilot Cloud Agent; separater Copilot-Code-Review-Dienst und Drittanbieteragenten | Mehrere Zuweisungen können unabhängig laufen | Branch und Pull Request auf GitHub |
| Cursor Cloud Agents | Web, Desktop, Mobilgeräte, Chatintegrationen, SCM und API | Von Cursor verwaltete isolierte VMs | Kuratierte wählbare Modelle und Subagenten | Integriert; viele Cloud Agents parallel möglich | Separater Branch wird zur Übergabe eines mergefähigen PR gepusht |
| OpenHands | Python-/REST-SDK, CLI, lokale GUI, Cloud und Enterprise | Lokal, Managed Cloud, Docker oder Kubernetes | Modellunabhängige Agenten und individuelle Multi-Agenten-Anwendungen | Durch SDK-/Plattformentwurf unterstützt | Durch Anwendung oder GitHub-Workflow konfigurierbar |
| aider | Terminalchat | Lokaler Worktree | Flexible Modellauswahl; Architect-/Editor-Zwei-Modell-Modus | Nicht der primär dokumentierte Workflow | Standardmäßig automatische lokale Commits |

## 5. Governance- und Evidenzmatrix

| Fähigkeit | Dual-Agent Orchestrator | Codex | Claude Teams | Antigravity | GitHub Copilot | Cursor Cloud | OpenHands | aider |
|---|---|---|---|---|---|---|---|---|
| Feste Trennung von Implementierer und Reviewer | Über drei benannte Provider integriert | In geprüftem Produktablauf nicht nachgewiesen | Mit Teammates/Subagenten konfigurierbar | Mit Custom Agents/Subagenten konfigurierbar | Coding und Review sind separate Dienste | Mit Review- und Freigabeagenten konfigurierbar | In einer Anwendung konfigurierbar | Architect-/Editor-Trennung verfügbar, aber keine unabhängige Freigabekette |
| Review an exakten kanonischen Diff-Fingerprint gebunden | Integriert | Nicht nachgewiesen | Nicht nachgewiesen | Nicht nachgewiesen | PR-/Commitkontext, aber dieser exakte Vertrag ist nicht nachgewiesen | PR-/Laufkontext, aber dieser exakte Vertrag ist nicht nachgewiesen | Konfigurierbar | Nicht nachgewiesen |
| Eine deterministische Validierungsattestierung für alle Reviewer | Integriert | Terminal-/Testevidenz vorhanden, aber dieses Eigentumsmodell nicht nachgewiesen | Hooks und Werkzeuge vorhanden, aber dieses Eigentumsmodell nicht nachgewiesen | Agenten-Verifikationsartefakte integriert | Agententests und Code Review vorhanden, aber getrennte Abläufe | Build-/Testartefakte und Reviewsysteme vorhanden | Werkzeuge, Ereignisse, Sicherheit und Tracing konfigurierbar | Bearbeitender Agent kann Tests ausführen; keine separate gemeinsame Attestierung nachgewiesen |
| Quell-Workspace des Reviewers konstruktiv schreibgeschützt | Integriert | Nicht als Invariante einer Reviewerrolle nachgewiesen | Planmodus kann schreibgeschützt sein; Berechtigungsverhalten je Team unterschiedlich | Begrenzte Berechtigungen integriert; feste Reviewer-Unveränderlichkeit nicht nachgewiesen | Code-Review-Kommentare statt beschreibbarer Implementierungssitzung | Schreibgeschützte Erkundungsrunden vorhanden; feste reviewerübergreifende Isolation nicht nachgewiesen | Aktionsbestätigung und Sandboxing konfigurierbar | Ask-Modus schreibgeschützt, aber keine verpflichtende Reviewstufe |
| Persistierter eigentümerspezifischer Finding-Lebenszyklus | Integriert | Anderer Scope | Anderer Scope | Artefaktfeedback integriert | PR-Reviewthreads stellen Plattformlebenszyklus bereit | PR-Review- und Freigabesysteme stellen Plattformlebenszyklus bereit | Konfigurierbar | Chat-/Git-Historie statt strukturiertem reviewer-eigenem Lebenszyklus |
| Fingerprint-gebundene menschliche Richtlinien-Gates | Integriert | Freigabemodi vorhanden; exakte Bindung nicht nachgewiesen | Berechtigungsabfragen und Planfreigabe vorhanden; exakte Bindung nicht nachgewiesen | Begrenzte Freigaben vorhanden; exakte Bindung nicht nachgewiesen | Repository- und Organisationsrichtlinien gelten in GitHub | Freigaberichtlinien und Risikoschwellen integriert | Konfigurierbar | Interaktive Bestätigung statt persistiertem Richtlinienzustand |
| Exakte Schrittfortsetzung nach Quota-, Prozess- oder Benutzerhalt | Integriert | Sitzungskontinuität vorhanden; exakte Zustandsmaschine nicht nachgewiesen | Agent-Team-Fortsetzung besitzt dokumentierte Grenzen | Persistente Projekte/Unterhaltungen vorhanden | Cloudaufgaben-/PR-Fortsetzung wird von der Plattform verwaltet | Cloudlauf- und Folgeaktivitätshistorie vorhanden | Unterhaltungspersistenz konfigurierbar | Chathistorie wiederherstellbar; exakte Zustandsmaschine nicht nachgewiesen |
| Commit auf geprüfte Pfad-Allowlist begrenzt | Integriert | Nicht nachgewiesen | Nicht nachgewiesen | Projekt-/Worktree-Scope, aber exakte Commit-Allowlists nicht nachgewiesen | PR-Diff ist die Reviewgrenze | PR-Diff ist die Reviewgrenze | Konfigurierbar | Automatische Commits decken die Bearbeitungssitzung ab, nicht eine separat freigegebene Allowlist |

## 6. Produktprofile

### 6.1 OpenAI Codex

OpenAI beschreibt Codex als Coding-Agenten für CLI-, IDE-, Desktop- und Cloudoberflächen. Cloudaufgaben laufen in isolierten Umgebungen; die Codex-App unterstützt mehrere Agenten parallel mit integrierten Worktrees. Abgeschlossene Arbeit kann geprüft, überarbeitet, lokal ausgecheckt oder in einen Pull Request überführt werden. Damit ist Codex als Implementierungs-Workspace breiter und ausgereifter als dieser Orchestrator.

Der Orchestrator verwendet Codex für eine engere Verantwortung: Planung, Implementierung, Korrektur und Abschlussbericht innerhalb einer anderswo besessenen Zustandsmaschine. Sein Zusatznutzen ist keine weitere Codex-Ausführungsoberfläche, sondern unabhängige Claude- und Antigravity-Urteile plus deterministische Commit-Autorisierung.

Offizielle Quellen: [Einführung der Codex-App](https://openai.com/index/introducing-the-codex-app/), [Einführung von Codex](https://openai.com/index/introducing-codex/), [Codex-CLI-Überblick](https://help.openai.com/en/articles/11096431-openai-codex-cli-getting-started).

### 6.2 Claude Code Agent Teams

Claude Code Agent Teams koordinieren einen Lead und mehrere unabhängige Claude-Sitzungen über eine gemeinsame Aufgabenliste und direkte Nachrichten. Sie sind stark bei paralleler Recherche, Reviews, konkurrierenden Hypothesen und Implementierungen mit getrennten Dateien. Teams können Planfreigaben verlangen und Lebenszyklusregeln mit Hooks erzwingen. Anthropic kennzeichnet die Funktion derzeit als experimentell und dokumentiert Grenzen bei Fortsetzung, Synchronisation des Aufgabenstatus, Herunterfahren, Verschachtelung und fester Führung.

Diese Topologie begünstigt kollaborative parallele Ausführung. Der Dual-Agent Orchestrator priorisiert dagegen eine sequenzielle Kette mit Providerdiversität und unveränderlichen Rollengrenzen. Claude kann sich nicht selbst vom Reviewer zum Implementierer befördern, und seine Freigabe reicht ohne Antigravitys nachfolgendes Urteil zum selben Fingerprint nicht aus.

Offizielle Quellen: [Claude Code Agent Teams](https://code.claude.com/docs/en/agent-teams), [Claude Code Subagenten](https://code.claude.com/docs/en/sub-agents), [Funktionsweise von Claude Code](https://code.claude.com/docs/en/how-claude-code-works).

### 6.3 Google Antigravity 2.0

Google positioniert Antigravity 2.0 als eigenständige Kommandozentrale für synchrone und asynchrone Agenten. Projekte können mehrere Ordner umfassen, Git-Worktrees verwenden, begrenzte Einstellungen und Berechtigungen anwenden und dynamische Subagenten ausführen. Das breitere Ökosystem enthält CLI- und IDE-Oberflächen, Browserinteraktion, Artefakte, geplante Aufgaben, Skills, Hooks und MCP-Integration.

Antigravity bietet damit eine reichhaltigere Betreiberoberfläche, Parallelität und interaktive Artefakte. In diesem Projekt wird es bewusst auf einen unabhängigen, schreibgeschützten Abschlussreviewer nach Claude begrenzt. Feste Reihenfolge und Evidenzvertrag stammen vom Orchestrator, nicht aus Antigravitys allgemeinem Agent Manager.

Offizielle Quellen: [Antigravity-2.0-Überblick](https://antigravity.google/docs/overview), [Antigravity-2.0-Funktionen](https://antigravity.google/docs/features?app=antigravity), [Antigravity-CLI-Agenten](https://antigravity.google/docs/cli/commands/agents?hl=en), [Google-Entwicklerankündigung](https://developers.googleblog.com/build-with-google-antigravity-our-new-agentic-development-platform/).

### 6.4 GitHub Copilot Cloud Agent und Code Review

GitHubs Cloud Agent kann ein Issue übernehmen, das Repository untersuchen, Änderungen in einer temporären GitHub-Actions-Umgebung implementieren, Tests und Linter ausführen und einen Pull Request öffnen. Copilot Code Review ist eine separate Reviewoberfläche mit Repositorykontext, konfigurierbaren Anweisungen, Skills und MCP-Zugriff. Das ist attraktiv, wenn GitHub-Issues, Pull Requests, Berechtigungen und Organisationsrichtlinien die natürliche Steuerungsebene bilden.

Der Dual-Agent Orchestrator arbeitet lokal und unabhängig vom Hostingprovider. Er endet bei verifizierten lokalen Commits und bietet strengere Slice-Pfad- und Fingerprint-Invarianten, besitzt aber weder GitHubs Zusammenarbeit und PR-Review noch Organisationsverwaltung und gehostete Ausführung.

Offizielle Quellen: [Passendes GitHub-KI-Werkzeug auswählen](https://docs.github.com/en/copilot/concepts/tools/ai-tools), [Über Copilot Code Review](https://docs.github.com/en/copilot/concepts/agents/code-review), [Fehlersuche und Umgebung des Copilot Cloud Agent](https://docs.github.com/en/copilot/how-tos/use-copilot-agents/cloud-agent/troubleshoot-cloud-agent), [Firewall des Cloud Agent](https://docs.github.com/en/copilot/how-tos/copilot-on-github/customize-copilot/customize-cloud-agent/customize-the-agent-firewall).

### 6.5 Cursor Cloud Agents

Cursor Cloud Agents laufen in isolierten verwalteten VMs mit Repositorys, Abhängigkeiten, Geheimnissen, Netzwerkrichtlinien, MCP-Servern, Hooks, Browser-/Desktopsteuerung und umfangreichen Artefakten. Viele Agenten können parallel arbeiten, auch an Aufgaben über mehrere Repositorys. Sie arbeiten auf separaten Branches, pushen zur Übergabe und erzeugen mergefähige Pull Requests. Cursor bietet außerdem Bugbot, Security Agents sowie risikobasiertes PR Routing & Approval.

Cursor deckt dadurch mehr vom gehosteten Entwicklungslebenszyklus ab. Die offizielle Dokumentation weist nicht dieselbe feste providerübergreifende sequenzielle Reviewfolge, die Wiederverwendung einer einzelnen Attestierung oder eine lokale Exakt-Pfad-Committransaktion nach. Teams mit Bedarf an verwalteter Kapazität, reichhaltigen Artefakten, Integrationen und PR-Automatisierung können Cursor bevorzugen; Teams mit Fokus auf eine kleine auditierbare lokale Steuerungsebene eher diesen Orchestrator.

Offizielle Quellen: [Cursor Cloud Agents](https://cursor.com/docs/cloud-agent), [Cursor PR Routing & Approval](https://cursor.com/docs/approval-agents).

### 6.6 OpenHands

OpenHands ist die unmittelbar erweiterbarste Plattform dieser Auswahl. Das MIT-lizenzierte Software Agent SDK stellt Python- und REST-APIs, vorgefertigte Coding-Werkzeuge, lokale oder Cloudausführung und einen mit Docker oder Kubernetes betreibbaren Agentenserver bereit. Es ist modellunabhängig und unterstützt ausdrücklich individuelle Verhaltensweisen sowie größere Multi-Agenten-Aufgaben.

OpenHands ist daher eine starke Grundlage für eine allgemeine oder selbst gehostete Agentenplattform. Die Nachbildung des exakten Workflows dieses Projekts auf OpenHands wäre ein eigener Anwendungsentwurf: Rollentrennung, Fingerprintregeln, Reviewerisolation, Finding-Zuständigkeit, Gates und Commit-Autorisierung müssten konfiguriert oder implementiert werden. Der Dual-Agent Orchestrator liefert diese Festlegungen integriert, ist aber wesentlich weniger allgemein.

Offizielle Quellen: [OpenHands Software Agent SDK](https://docs.openhands.dev/sdk/index), [OpenHands-Schnellstart](https://docs.openhands.dev/overview/quickstart), [OpenHands-Runtimearchitektur](https://docs.openhands.dev/openhands/usage/architecture/runtime).

### 6.7 aider

aider ist ein lokaler Terminal-Pair-Programmer mit breiter Modellunterstützung und enger Git-Integration. Sein Architect-Modus trennt ein Planungsmodell von einem Editormodell; der Ask-Modus schreibt keine Dateien. Standardmäßig erstellt aider beschreibende Commits für Änderungen und bietet direkte Undo- und Diff-Befehle.

Damit lässt sich aider deutlich leichter installieren, verstehen und für interaktive Entwicklung verwenden. Es dokumentiert keine verpflichtende unabhängige Kette aus zwei Reviewern, Fingerprint-gebundene Attestierungen, strukturierte Finding-Zuständigkeit oder exakten Gate-Zustand. Es ist am besten als effizienter Bearbeitungsagent und nicht als Workflow-Governance-Schicht einzuordnen.

Offizielle Quellen: [aider-Chatmodi](https://aider.chat/docs/usage/modes.html), [aider-Git-Integration](https://aider.chat/docs/git.html).

## 7. Stärken des Dual-Agent Orchestrator

Das Projekt eignet sich besonders, wenn alle folgenden Punkte wichtig sind:

- Arbeit muss lokal im Repository bleiben, bis ein Mensch eine externe Git-Aktion auswählt.
- Der Implementierer darf nicht der einzige Reviewer sein.
- Reviewerdiversität über Provider hinweg wird gegenüber mehreren Instanzen derselben Plattform bevorzugt.
- Tests müssen von einer deterministischen Steuerung ausgeführt und an den exakt geprüften Diff gebunden werden.
- Kleine, pfadbegrenzte Commits sind einem großen autonomen Änderungspaket vorzuziehen.
- Quota-Unterbrechungen, Neustarts und Benutzergates müssen fortsetzbar sein, ohne abgeschlossene Seiteneffekte zu wiederholen.
- Auditevidenz soll sowohl maschinenlesbar als auch in commitetem Markdown prüfbar sein.

## 8. Stärken anderer Produkte

Ein anderes oder ergänzendes Produkt ist geeigneter, wenn folgende Anforderung dominiert:

| Anforderung | Stärkere Wahl aus diesem Vergleich |
|---|---|
| Ausgereifte Multi-Agenten-Desktop-Kommandozentrale und parallele Worktrees | OpenAI Codex App oder Google Antigravity 2.0 |
| Claude-native kollaborative Teams mit direkten Nachrichten zwischen Agenten | Claude Code Agent Teams |
| GitHub-Issue-zu-PR-Automatisierung und organisationsnativer Review | GitHub Copilot Cloud Agent und Code Review |
| Verwaltete parallele VMs, umfangreiche UI-/Browserartefakte, Integrationen und PR-Automatisierung | Cursor Cloud Agents |
| Modellunabhängiges SDK, Selbsthosting, Docker/Kubernetes oder individuelle Agentenprodukte | OpenHands |
| Minimales lokales Terminal-Pairing mit komfortabler automatischer Git-Historie | aider |
| Paralleler Implementierungsdurchsatz | Codex, Claude Teams, Antigravity, Cursor oder ein OpenHands-basierter Entwurf |

## 9. Positionierung und Ergänzbarkeit

Der Orchestrator lässt sich am besten als **richtliniendurchsetzende, evidenzgebundene lokale Auslieferungspipeline für Coding-Agenten** beschreiben. Er ist kein universelles Multi-Agenten-Framework und sollte nicht als überlegener Ersatz für die von ihm aufgerufenen Produkte vermarktet werden.

Die Beziehung ist häufig komplementär:

- Codex, Claude und Antigravity bleiben Reasoning- und Coding-Engines.
- Ein Hostingprodukt kann die verifizierten lokalen Commits weiterhin in einem späteren, menschlich gesteuerten PR-Schritt übernehmen.
- OpenHands könnte eine zukünftige alternative Ausführungsbasis bilden, wenn die feste Adaptergrenze bewusst verallgemeinert würde.
- Repository-Hooks und CI bleiben als zusätzliche Verteidigung nach der lokalen Validierung des Orchestrators sinnvoll.

Die belastbare Produktaussage betrifft Prozessintegrität: Das System kann zeigen, welche begrenzten Änderungen validiert wurden, welche Evidenz jeder Reviewer gesehen hat, wem jedes Finding gehörte, warum ein Gate den Fortschritt stoppte und welche exakten Pfade in den resultierenden lokalen Commit gelangten.

## 10. Entscheidungshilfe

Der Dual-Agent Task Orchestrator ist passend, wenn die meisten dieser Fragen mit „ja“ beantwortet werden:

1. Muss die Arbeit lokal bleiben und in lokalen Commits statt automatischen Pull Requests enden?
2. Sind zwei geordnete, vom Implementierer unabhängige Reviewerrollen erforderlich?
3. Muss die Validierung einmal pro exaktem Diff erzeugt und als unveränderliche Evidenz wiederverwendet werden?
4. Benötigen Unterbrechungen eine explizite persistierte Zustandsmaschine und fortsetzbare Gates?
5. Ist eine strikte Pfad-Allowlist je Slice wichtiger als breite Agentenfreiheit?
6. Ist der Verzicht auf parallele Ausführung und eine reichhaltige Oberfläche zugunsten von Nachvollziehbarkeit und Kontrolle akzeptabel?

Werden die meisten Fragen mit „nein“ beantwortet, ist ein einzelner Coding-Agent, ein gehosteter Cloud-Agent, eine IDE-zentrierte Plattform oder ein allgemeines Agenten-SDK normalerweise einfacher.

## 11. Pflegerichtlinie

Dieser Vergleich ist zeitabhängig. Vor einer Verwendung für Beschaffung, Preisentscheidung, Sicherheitszertifizierung oder öffentliche Wettbewerbsbehauptung muss er erneut verifiziert werden. Mindestens jede offizielle Quelle und der Recherchestand sind zu aktualisieren, sobald ein verglichenes Produkt Ausführungsmodell, Reviewfunktionen, Persistenz, Git-Verhalten, Lizenzierung oder Bereitstellungsoptionen ändert.

Aussagen zum Fehlen einer Fähigkeit müssen als „in den geprüften offiziellen Quellen nicht nachgewiesen“ qualifiziert bleiben. Marketingformulierungen und Anbieterbenchmarks dürfen ohne unabhängige Evaluation nicht in vergleichende Qualitätsaussagen überführt werden.
