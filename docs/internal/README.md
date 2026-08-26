# Interne Arbeits- und Auditdokumente

Dieses Verzeichnis trennt aktive Vorhaben von abgeschlossenen historischen
Unterlagen.

## Aktive Dokumente

- [Roadmap für Phase 2 und Folgephasen](ORCHESTRATOR_ROADMAP_PHASE_2_PLUS.md)

Aktive Orchestratorläufe dürfen ihre gebundenen Arbeitsplan-, Slice- und
Auditpfade nicht manuell verschieben. Die Archivierung erfolgt erst nach dem
fachlichen Abschluss.

## Archive

Abgeschlossene und abgelöste Entwicklungsnachweise liegen ausschließlich
unter [`archive/`](archive/). Sie sind nichtautoritative historische Evidenz:
Runtime, Resume, Schemaresolver und Tests dürfen sie weder importieren noch als
Entscheidungsquelle verwenden.

Der Ordner
[`archive/antigravity-retirement/`](archive/antigravity-retirement/) enthält
die beim `structured-v2`-Cutover stillgelegten Übergangsdokumente und
Workflow-v1-Schemata sowie unter `final-package/` den vollständig
freigegebenen Arbeitsplan, alle Sliceberichte und die abschließenden Codex- und
Claude-Reviews der endgültigen Providerentfernung. Die beiden weiterhin
aktiven Provider-Subset-Register unter `schemas/` sind davon unabhängig und
bleiben absichtlich auf ihrer eigenen Registerversion 1.

Der Ordner
[`archive/native-only-transport-parser-retirement-und-evidenzeffizienz/`](archive/native-only-transport-parser-retirement-und-evidenzeffizienz/)
enthält den abgeschlossenen Arbeitsplan, alle drei Slice-Berichte sowie die
getrennten Claude- und Codex-Gesamtreviews des Native-only-Cutovers,
Parser-Retirements und der strukturellen Eingabereduktion.
