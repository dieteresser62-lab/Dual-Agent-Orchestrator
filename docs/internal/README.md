# Interne Arbeits- und Auditdokumente

Dieses Verzeichnis trennt aktive Vorhaben von abgeschlossenen historischen
Unterlagen.

## Aktive Dokumente

Aktuelle Roadmaps, Arbeitspläne und noch laufende Reviewdokumente liegen direkt
unter `docs/internal/`. Nach dem fachlichen Abschluss werden die Dokumente eines
Vorhabens gemeinsam in einen eindeutig benannten Unterordner von `archive/`
verschoben.

Derzeit aktiv:

- [Roadmap für Phase 2 und spätere Folgephasen](ORCHESTRATOR_ROADMAP_PHASE_2_PLUS.md)
- [Erkenntnisse aus Arbeitspaket 1 und Neuzuschnitt der Stabilisierung 1.1](phase-2-arbeitspaket-1-erkenntnisse-und-stabilisierung-1-1.md)

Während eines aktiven Orchestratorlaufs dürfen gebundene Arbeitsplan-, Slice-
und Auditpfade nicht manuell verschoben werden. Die Archivierung erfolgt erst,
wenn der Lauf abgeschlossen oder sein manueller Abschluss dokumentiert ist.

## Archive

### Orchestrator-Stabilisierung 1.1C – Quota-Betrieb und Providerattempt-Telemetrie

Pfad: [`archive/orchestrator-stabilization-1-1c/`](archive/orchestrator-stabilization-1-1c/)

Wichtige Einstiegspunkte:

- [Arbeitsplan 1.1C – Quota-Wartebetrieb](archive/orchestrator-stabilization-1-1c/release-1-1c-quota-wartebetrieb-und-provideraufruf-telemetrie-arbeitsplan.md)
- [Review 1.1C](archive/orchestrator-stabilization-1-1c/release-1-1c-quota-wartebetrieb-und-provideraufruf-telemetrie-implement-review-414a32cf.md)
- [Arbeitsplan 1.1C2 – Providerattempt und Schema](archive/orchestrator-stabilization-1-1c/release-1-1c2-providerattempt-record-und-schema-arbeitsplan.md)
- [Konsolidiertes Review 1.1C2 mit manueller Abschlussausnahme](archive/orchestrator-stabilization-1-1c/release-1-1c2-providerattempt-record-und-schema-implement-review-8c9aa8f1.md)
- [Slice 1 – Quota-Wartepolitik](archive/orchestrator-stabilization-1-1c/slice-release-1-1c-quota-wartebetrieb-und-provideraufruf-telemetrie-arbeitspla-01-quota-wartepolitik-sieben-tage-grenze-und-fake-clock.md)
- [Slice 1 – Providerattempt-Kern](archive/orchestrator-stabilization-1-1c/slice-release-1-1c2-providerattempt-record-und-schema-arbeitsplan-01-providerattempt-kern-sichere-projektion-und-abschluss-der-quota-beobacht.md)
- [Abschlusskorrektur 2](archive/orchestrator-stabilization-1-1c/slice-release-1-1c2-providerattempt-record-und-schema-implement-02-abschlusskorrektur.md)
- [Abschlusskorrektur 3](archive/orchestrator-stabilization-1-1c/slice-release-1-1c2-providerattempt-record-und-schema-implement-03-abschlusskorrektur.md)

Alle Implementierungs- und Korrekturslices wurden von Claude und Antigravity
genehmigt und commitgebunden abgeschlossen. Die vollständige Suite bestand
vor dem manuellen Abschluss mit `993 passed`; die Abschlussvalidierung des
Archivcommits ist im konsolidierten Review dokumentiert. Codex und Claude
beendeten den letzten branchweiten Review, während Antigravity wegen des
entfernten Tool-Schemafehlers `additional properties 'LineNumber' not allowed`
keinen Finalvertrag erzeugte. Der Lauf wurde deshalb ausdrücklich als
administrative Ausnahme beendet und nicht als vollständige dreifache
Finalfreigabe dargestellt.

### Orchestrator-Stabilisierung 1.1B – Reviewverträge und Reviewpakete

Pfad: [`archive/orchestrator-stabilization-1-1b/`](archive/orchestrator-stabilization-1-1b/)

Wichtige Einstiegspunkte:

- [Arbeitsplan](archive/orchestrator-stabilization-1-1b/release-1-1b-deterministische-reviewvertraege-und-schlanke-reviewpakete-arbeitsplan.md)
- [Konsolidiertes Gesamtreview](archive/orchestrator-stabilization-1-1b/release-1-1b-deterministische-reviewvertraege-und-schlanke-reviewpakete-implement-review-c80d392c.md)
- [Slice 1 – Lokale Reviewverträge](archive/orchestrator-stabilization-1-1b/slice-release-1-1b-deterministische-reviewvertraege-und-schlanke-reviewpakete-01-lokale-reviewvertrage-fail-closed-vervollstandigen.md)
- [Slice 2 – Kanonisch minimierte Reviewpakete](archive/orchestrator-stabilization-1-1b/slice-release-1-1b-deterministische-reviewvertraege-und-schlanke-reviewpakete-02-slice-und-korrekturreviewpakete-kanonisch-minimieren.md)
- [Abschlusskorrektur – Finding-Scope in Korrekturpaketen](archive/orchestrator-stabilization-1-1b/slice-release-1-1b-deterministische-reviewvertraege-und-schlanke-reviewpakete-implement-03-abschlusskorrektur.md)

Das Paket wurde mit `961 passed` sowie den finalen Freigaben von Claude und
Antigravity abgeschlossen. P2-FU-007 (Nachtrag), P2-FU-023 und P2-FU-024 sind
damit fachlich gelöst; der verbleibende Stabilisierungsscope steht im aktiven
Erkenntnisdokument.

### Orchestrator-Stabilisierung 1.1A – Record-Autorität und Replay

Pfad: [`archive/orchestrator-stabilization-1-1a/`](archive/orchestrator-stabilization-1-1a/)

Wichtige Einstiegspunkte:

- [Arbeitsplan](archive/orchestrator-stabilization-1-1a/release-1-1a-record-autoritaet-und-replay-arbeitsplan.md)
- [Konsolidiertes Gesamtreview](archive/orchestrator-stabilization-1-1a/release-1-1a-record-autoritaet-und-replay-implement-review-d9c35a04.md)
- [Slice 1 – Record-Replaykern und Cacheautorität](archive/orchestrator-stabilization-1-1a/slice-release-1-1a-record-autoritaet-und-replay-arbeitsplan-01-reiner-record-replaykern-und-cacheautoritat.md)
- [Slice 2 – Structured-Resume und Auditgrenzen](archive/orchestrator-stabilization-1-1a/slice-release-1-1a-record-autoritaet-und-replay-arbeitsplan-02-structured-resume-und-auditgrenzen-aus-gemeinsamem-replay.md)

Das Paket wurde mit `934 passed` sowie den finalen Freigaben von Claude und
Antigravity abgeschlossen. Die noch offenen Erkenntnisse und der weitere
Zuschnitt von Stabilisierung 1.1 werden ausschließlich im aktiven
Erkenntnisdokument fortgeführt.

### Strukturierte Agentenkommunikation – Phase 1

Pfad: [`archive/structured-agent-artifacts-phase1/`](archive/structured-agent-artifacts-phase1/)

Wichtige Einstiegspunkte:

- [Manueller Abschluss](archive/structured-agent-artifacts-phase1/strukturierte-agentenkommunikation-phase-1-manueller-abschluss.md)
- [Arbeitsplan](archive/structured-agent-artifacts-phase1/strukturierte-agentenkommunikation-arbeitsplan.md)
- [Konsolidiertes Review](archive/structured-agent-artifacts-phase1/strukturierte-agentenkommunikation-implement-review-542ccc72.md)

Die einzelnen Planungs-, Implementierungs- und Korrektur-Sliceberichte liegen
im selben Archivordner. Die historischen Pfadangaben innerhalb dieser Dokumente
werden nicht nachträglich umgeschrieben.

### Frühere Orchestrator-Modernisierung

Pfad: [`archive/`](archive/)

Die früheren Modernisierungsanforderungen, Referenzrollen und 19 Sliceberichte
liegen direkt in diesem bestehenden Archiv. Neue abgeschlossene Vorhaben sollen
dagegen jeweils einen eigenen Unterordner erhalten.
