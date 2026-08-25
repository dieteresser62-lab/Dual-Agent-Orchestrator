# Archiv – abgelöste Workflowverträge und Übergangsdokumente

Dieser Ordner bewahrt die beim endgültigen `structured-v2`-Cutover
ausgemusterten Workflow-v1-Schemata und Übergangsdokumente unverändert als
nichtautoritative historische Evidenz auf.

Die Dateien dürfen von Produktcode, Schemaresolvern, Resume, Tests oder
Entscheidungslogik nicht gelesen werden. Sie belegen ausschließlich den
historischen Entwicklungsstand. Neue Läufe verwenden nur die aktiven
`structured-v2`-Schemata unter `schemas/`; historische Protokollzustände werden
mit `UNSUPPORTED-PROTOCOL` abgewiesen.

## Abgeschlossenes Entfernungspaket

Der Unterordner [`final-package/`](final-package/) enthält den freigegebenen
Arbeitsplan zur endgültigen Entfernung von Antigravity, die vier
Sliceberichte, die historischen negativen Codex-Abschlusschecks und die
abschließenden positiven Codex- und Claude-Gesamtreviews. Diese Unterlagen
sind ebenfalls nichtautoritative Entwicklungsevidenz und besitzen keine
Runtime-, Resume- oder Freigabewirkung.

Die unabhängig versionierten aktiven Provider-Subset-Register
`native-provider-schema-capabilities-v1` und
`native-provider-schema-exceptions-v1` gehören nicht zu diesem Archiv. Sie
beschreiben Providerfähigkeiten, nicht den Workflowvertrag.
