# Orchestrator-Roadmap – Phase 2 und Folgephasen

**Stand:** 27. August 2026
**Aktive Topologie:** Codex plant und implementiert; Claude reviewt; der
Orchestrator validiert, attestiert und erstellt freigegebene lokale Commits.

## 1. Erreichter Architekturstand

- Neue Läufe sind unveränderlich an `structured-v2` gebunden.
- Native Codex-Resultate und Claude-Reviews verwenden request-spezifische,
  geschlossene JSON-Verträge ohne Textparserfallback.
- Die append-only Recordkette ist technische Autorität; State-v3 ist ihr
  geprüfter Betriebsspiegel, Markdown die deterministische Menschenansicht.
- Historische Protokollzustände werden mit `UNSUPPORTED-PROTOCOL` fail-closed
  abgewiesen und nicht migriert.
- Codex kann die eigene Arbeit nicht freigeben. Claude besitzt die alleinige
  Review- und Freigabeautorität.
- Der Orchestrator führt die vollständige Matrix genau einmal je relevantem
  Fingerprint aus und bindet die Attestierung an Claudes Entscheidung.
- Nach einer gültigen Claude-Freigabe erstellt der Orchestrator den lokalen
  Plan-, Slice- oder Korrekturcommit idempotent.
- Modell und Effort sind rollenbezogen konfigurierbar und im Workflowzustand
  unveränderlich gebunden. Defaults: Codex `gpt-5.6-sol`/`medium`, Claude
  `sonnet`/`high`.

## 2. Letzter Abschlussmeilenstein

Das abgeschlossene Retirement-Paket hat die frühere dritte Providerrolle
endgültig aus Produktcode, Konfiguration, Tests und aktiver Dokumentation
entfernt. Es archivierte abgelöste Workflow-v1-Schemata und
Übergangsdokumente, führte einen statischen Retirementguard ein und schloss
verlorene allgemeine Attestierungs-, Commit- und Resume-Regressionsbelege.

Nach Freigabe gelten folgende Endbedingungen:

1. Agentregistry und CLI kennen ausschließlich Codex und Claude.
2. Aktive Verträge erlauben ausschließlich `C-*`-Findings.
3. Kein deaktivierter Adapter, optionaler Reviewer oder Provider-Skip bleibt
   zurück.
4. Nur die exakt benannten unabhängigen Provider-Subset-Register behalten die
   Identität `native-provider-schema-*-v1`.
5. Archivierte v1-Nachweise sind lesbare Historie, aber keine Runtimeautorität.

## 3. Nächste priorisierte Arbeitspakete

### 3.1 Bereits erreicht: Menschenlesbarer Live-Output

Die Providerbytes bleiben unverändert autoritativ. Der lokale Kompaktmodus
rendert Fortschritt, Entscheidungen, Findings und Nutzungshinweise als lesbare
Logzeilen, ohne rohe Provider-JSON-Zeilen im normalen Watch-Output anzuzeigen
oder weitere Providerkosten zu erzeugen. Der Vollmodus und die unveränderte
Rohantwortpersistenz bleiben für Diagnosen erhalten.

### 3.2 Erreicht: Native-only-Cutover, Textparser-Retirement und strukturelle Eingabereduktion

Das umgesetzte Arbeitspaket
[`native-only-transport-parser-retirement-und-evidenzeffizienz-arbeitsplan.md`](archive/native-only-transport-parser-retirement-und-evidenzeffizienz/native-only-transport-parser-retirement-und-evidenzeffizienz-arbeitsplan.md)
macht die nativen Codex- und Claude-Verträge verpflichtend, entfernt die noch
produktive Textresultat-, Marker- und LLM-Reparaturarchitektur und ersetzt
redundante Vollplan-/Prompt-/Korrekturevidenz durch digestgebundene
operationsspezifische Pakete. Die Einsparung wird strukturell und über
kanonische Zeichen-/Bytezahlen nachgewiesen; tatsächliche Tokens werden nur aus
persistierter Providerusage berichtet. Die Recordprojektion weist außerdem
Attemptanzahl, Laufzeit und Retrystatus aus; fehlende Usage bleibt `unknown`.

### 3.3 Contract- und Corpuspflege

- request-spezifische Writerschemas weiterhin gegen lokale Domänenregeln
  differenziell prüfen;
- neue native Rohantworten vollständig inventarisieren;
- Schemafähigkeits- und Ausnahme-Register nur bei tatsächlicher
  Provideränderung versionieren;
- stille Überpermissivität und unerwartete lokale Ablehnung gleichermaßen
  fail-closed testen.

### 3.4 Betriebsbeobachtbarkeit

- Kosten- und Laufzeittrends über mehrere Runs aus den bestehenden
  operationenbezogenen Attemptprojektionen ableiten;
- Resumeursachen und Profilabweichungen menschenlesbar erklären;
- weitergehende Trends aus strukturierten Records ableiten, ohne die
  Recordkette als Datenquelle zu ersetzen.

### 3.5 Bootstrap-Smoke- und Langläufe

Vor weiteren realen Langläufen sichert das Paket
„Orchestrator-Stabilisierung: Übergangsmatrix und Structured-Output-Resilienz“
die schwierigen Kanten providerfrei ab. Der Nachweis trennt Sicherheit,
Verfügbarkeit und Autonomie, bindet Driftgates an ihre vollständige
Gateidentität und führt Korrektur-, Replay-, Retry- und Queuebelege in einem
geschlossenen Szenarioinventar zusammen. Ein realer Provider-Canary bleibt
bewusst ein nachgelagerter Produktionsnachweis und ist kein Bestandteil der
Repositorysuite.

Danach folgen kleine reale Inbox-Aufgaben und anschließend längere
Mehrslice-Läufe. Erfolgskriterien sind null Textfallback, keine doppelten
Providerstarts oder Commits, stabile Resumegrenzen und eine vollständig
reproduzierbare Markdownansicht.

## 4. Dauerhafte Grenzen

- Kein Agent pusht, mergt, force-pusht oder schreibt Git-Historie um.
- Kein LLM erzeugt die autoritative Validierungsattestierung.
- Keine Markdownansicht repariert State oder Records.
- Kein unbekannter Protokollmodus wird geraten oder migriert.
- Eine zusätzliche Provider- oder Reviewerrolle wäre ein neues, ausdrücklich
  zu planendes Architekturvorhaben und kein reaktivierbares Featureflag.
