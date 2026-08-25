# Orchestrator-Roadmap – Phase 2 und Folgephasen

**Stand:** 25. August 2026
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

## 2. Aktueller Abschlussmeilenstein

Das laufende Retirement-Paket entfernt die frühere dritte Providerrolle
endgültig aus Produktcode, Konfiguration, Tests und aktiver Dokumentation. Es
archiviert abgelöste Workflow-v1-Schemata und Übergangsdokumente, führt einen
statischen Retirementguard ein und schließt verlorene allgemeine
Attestierungs-, Commit- und Resume-Regressionsbelege.

Nach Freigabe gelten folgende Endbedingungen:

1. Agentregistry und CLI kennen ausschließlich Codex und Claude.
2. Aktive Verträge erlauben ausschließlich `C-*`-Findings.
3. Kein deaktivierter Adapter, optionaler Reviewer oder Provider-Skip bleibt
   zurück.
4. Nur die exakt benannten unabhängigen Provider-Subset-Register behalten die
   Identität `native-provider-schema-*-v1`.
5. Archivierte v1-Nachweise sind lesbare Historie, aber keine Runtimeautorität.

## 3. Nächste priorisierte Arbeitspakete

### 3.1 Menschenlesbarer Live-Output für native JSON-Ergebnisse

Die Providerbytes bleiben unverändert autoritativ. Ein lokaler Pythonprojektor
rendert Fortschritt, Entscheidungen, Findings, Modell, Effort und Usage als
kompakte Logzeilen, ohne JSON erneut semantisch auszulegen oder Providerkosten
zu erzeugen.

### 3.2 Contract- und Corpuspflege

- request-spezifische Writerschemas weiterhin gegen lokale Domänenregeln
  differenziell prüfen;
- neue native Rohantworten vollständig inventarisieren;
- Schemafähigkeits- und Ausnahme-Register nur bei tatsächlicher
  Provideränderung versionieren;
- stille Überpermissivität und unerwartete lokale Ablehnung gleichermaßen
  fail-closed testen.

### 3.3 Betriebsbeobachtbarkeit

- Attempt-, Quota- und Retrytelemetrie in der Markdownprojektion verdichten;
- Resumeursachen und Profilabweichungen menschenlesbar erklären;
- Kosten- und Laufzeittrends aus strukturierten Records ableiten, ohne die
  Recordkette als Datenquelle zu ersetzen.

### 3.4 Bootstrap-Smoke- und Langläufe

Nach Abschluss der Retirementguards folgen kleine reale Inbox-Aufgaben und
anschließend längere Mehrslice-Läufe. Erfolgskriterien sind null Textfallback,
keine doppelten Providerstarts oder Commits, stabile Resumegrenzen und eine
vollständig reproduzierbare Markdownansicht.

## 4. Dauerhafte Grenzen

- Kein Agent pusht, mergt, force-pusht oder schreibt Git-Historie um.
- Kein LLM erzeugt die autoritative Validierungsattestierung.
- Keine Markdownansicht repariert State oder Records.
- Kein unbekannter Protokollmodus wird geraten oder migriert.
- Eine zusätzliche Provider- oder Reviewerrolle wäre ein neues, ausdrücklich
  zu planendes Architekturvorhaben und kein reaktivierbares Featureflag.
