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

Der Ordner
[`archive/orchestrator-transition-matrix-output-resilience/`](archive/orchestrator-transition-matrix-output-resilience/)
enthält den freigegebenen Arbeitsplan, alle drei Slice-Reviews, den
providerfreien Resilienz- und Abschlussnachweis sowie die vollständige Codex-
und Claude-Reviewkette einschließlich der Schließung von `C-01` bis `C-20`.

Der Ordner
[`archive/provider-wide-version-policy-hotfix/`](archive/provider-wide-version-policy-hotfix/)
enthält die vollständige Claude-Reviewkette des providerweiten
Versionspolicy-Hotfixes: die erste fingerprintgebundene Verweigerung wegen
Snapshotdrift und die abschließende Freigabe des stabilen Korrekturstands.

Der Ordner
[`archive/projektionsgarantie-und-kopplungsbremse-941f04403b23/`](archive/projektionsgarantie-und-kopplungsbremse-941f04403b23/)
enthält den freigegebenen Arbeitsplan, den Slicebericht und das vollständige
Gesamtaudit der providerfreien Projektionsgarantie und Providernamen-Ratsche.
Die Kennung bindet das Archiv an den von Claude freigegebenen und vom
Orchestrator vollständig validierten Branchfingerprint.
