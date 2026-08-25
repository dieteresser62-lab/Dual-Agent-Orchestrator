# Archiv – abgelöste Workflowverträge und Übergangsdokumente

Dieser Ordner bewahrt die beim endgültigen `structured-v2`-Cutover
ausgemusterten Workflow-v1-Schemata und Übergangsdokumente unverändert als
nichtautoritative historische Evidenz auf.

Die Dateien dürfen von Produktcode, Schemaresolvern, Resume, Tests oder
Entscheidungslogik nicht gelesen werden. Sie belegen ausschließlich den
historischen Entwicklungsstand. Neue Läufe verwenden nur die aktiven
`structured-v2`-Schemata unter `schemas/`; historische Protokollzustände werden
mit `UNSUPPORTED-PROTOCOL` abgewiesen.

Die unabhängig versionierten aktiven Provider-Subset-Register
`native-provider-schema-capabilities-v1` und
`native-provider-schema-exceptions-v1` gehören nicht zu diesem Archiv. Sie
beschreiben Providerfähigkeiten, nicht den Workflowvertrag.
