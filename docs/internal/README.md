# Interne Arbeits- und Auditdokumente

Dieses Verzeichnis trennt aktive Vorhaben von abgeschlossenen historischen
Unterlagen.

## Aktive Dokumente

- [Roadmap für Phase 2 und Folgephasen](ORCHESTRATOR_ROADMAP_PHASE_2_PLUS.md)
- [S2-Übergangs- und Divergenzmatrix](stabilisierung-s2-uebergangsmatrix.md) —
  die Inventur der Record-Mirror-Kanten. Sie bleibt aktiv, obwohl die
  Stabilisierung abgeschlossen ist: Sie ist die eine Inventur, auf die
  Folgevorhaben aufsetzen, und `tests/test_stabilisierung_s2_transition_matrix.py`
  bindet ihren Pfad.

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

Der Ordner
[`archive/plan-only-finding-handoffverlust-52ec2aa1d203/`](archive/plan-only-finding-handoffverlust-52ec2aa1d203/)
enthält den freigegebenen Arbeitsplan, alle drei Sliceberichte, das
branchweite Gesamtaudit sowie die vollständige Reviewkette der beiden während
des Hochlaufs erforderlichen Recovery-Hotfixes. Die Kennung bindet das Archiv
an den von Claude freigegebenen und vom Orchestrator mit 1132 bestandenen
Tests attestierten Abschlussfingerprint.

Der Ordner
[`archive/stabilisierung-state-authority/`](archive/stabilisierung-state-authority/)
enthält das vollständige Vorhaben zur Konsolidierung der Zustandsautorität:
Analyse und Gegenanalyse, den manuellen Arbeitsplan, alle Slice-Aufträge von S1
bis S7 einschließlich der zehn Recordbündel, der Appendkosten- und
Basispräfix-Korrektur, die Backloganalyse, die Merge-Bestandsaufnahme, die
S6-Vertragsentscheidungen und das Gesamtreview. Das Vorhaben lief bewusst ohne
den Orchestrator: Codex implementierte, Claude reviewte read-only, der Betreiber
gab frei. Ergebnis sind 27 Commits, die die Recordkette zur alleinigen Autorität
machen; `state.json` ist seither eine verwerfbare Projektion.

Der Ordner
[`archive/validierungsevidenz-selbstvergiftung/`](archive/validierungsevidenz-selbstvergiftung/)
enthält den Arbeitsplan, den Slicebericht und das Implementierungsreview des
abgebrochenen Orchestratorlaufs, dessen Slice 1 die kanonische semantische
Markdowngrenze eingeführt hat. Seine Slices 3 bis 5 gingen in die Stabilisierung
auf; sein Slice 2 blieb als `dc29d82` gesichert und ist eine offene Entscheidung.

Der Ordner
[`archive/plan-only-finding-hotfixes-20260829/`](archive/plan-only-finding-hotfixes-20260829/)
enthält die drei Symptom-Hotfixes zum PLAN_ONLY-Finding-Handoff vom 29. August
2026 und das zugehörige Claude-Review. Ihre Invarianten wurden in der
Stabilisierung zentral definiert.
