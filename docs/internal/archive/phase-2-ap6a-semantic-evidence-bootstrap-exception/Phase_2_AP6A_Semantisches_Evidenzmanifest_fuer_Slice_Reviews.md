# Phase 2 – Arbeitspaket 6A: Semantisches Evidenzmanifest für Slice-Reviews

TARGET_BRANCH: feature/native-semantic-evidence-manifest

## Auftrag

Erstelle zunächst ausschließlich einen repository-grounded, unmittelbar
ausführbaren Arbeitsplan für den kleinsten produktiv nutzbaren vertikalen
Teil von Arbeitspaket 6 aus
`docs/internal/ORCHESTRATOR_ROADMAP_PHASE_2_PLUS.md`.

Der Planungslauf ist der erste reale Bootstrap-Smoke-Test nach der Verbindung
der nativen Codex- und Claude-Pfade. Codex-Planung, Claude-Planreview,
Implementierung, Korrekturen und Claude-Slice-Review müssen mit gemeinsam
gebundenem `--native-codex-results --native-claude-reviews` ohne
Textmarkerfallback funktionieren. Antigravity verwendet in diesem Lauf noch
seinen bestehenden Legacy-Reviewvertrag.

Dieser Auftrag ist `PLAN_ONLY`: Im Planungslauf dürfen ausschließlich der
deterministisch abgeleitete Arbeitsplan und orchestratorisch verwaltete
Planartefakte entstehen. Produktcode und Produkttests bleiben bis zum
automatisch erzeugten Implementierungshandoff unverändert.

## Repository-Ausgangspunkt

- Arbeitspaket 5 ist auf `master` integriert. Die native
  Codex–Claude-Korrekturschleife, Finding-Dispositionen, Record-ahead-Recovery
  und der State-v3-Mirror-Guard werden wiederverwendet und nicht neu gebaut.
- `src/review_packets.py` erzeugt bereits kanonische Slice- und
  Korrekturpakete mit exakter Pfadallowlist, gefiltertem Diff, offenen
  Findings, kompakten Closure-Referenzen und gebundener Attestierung.
- `src/native_review_request.py` bindet Evidenz über IDs, Digests,
  Bytegrößen und Inline-/Content-Referenzen in den nativen Claude-Request.
- Die Recordkette bleibt technische Source of Truth. Markdown bleibt reine
  deterministische Ansicht und darf keine Workflowentscheidung autorisieren.
- Bestehende Paket- und Requestdigests dürfen nicht abgeschwächt oder durch
  ungebundene Dateilisten ersetzt werden.

## Ziel des kleinen Pakets

Der Arbeitsplan beschreibt genau einen Implementierungsslice, der Slice- und
Korrekturreviewpakete um einen deterministischen, maschinenlesbaren
Abdeckungsnachweis für das bereits übergebene Diff ergänzt:

1. Für jeden tatsächlich im gefilterten Reviewdiff enthaltenen
   repository-relativen Pfad existiert genau ein kanonischer Manifesteintrag.
2. Ein Eintrag bindet mindestens den Pfad, die Änderungsart und einen Digest
   des vollständigen zugehörigen Diffabschnitts. Wenn Unified-Diff-Hunks
   vorhanden sind, werden deren Bereichsheader deterministisch und
   reihenfolgestabil erfasst.
3. Der Builder arbeitet ausschließlich auf dem bereits vorliegenden Diff und
   der exakten autorisierten Pfadliste. Er liest keine zusätzlichen
   Repositorydateien und führt keinen freien Such- oder Explorationsplan aus.
4. Jeder im Diff enthaltene Pfad muss vollständig abgedeckt sein. Doppelte,
   mehrdeutige, unsichere, nicht autorisierte oder nicht zuordenbare
   Diffabschnitte werden vor einem Providerstart fail-closed abgewiesen.
5. Reine Eingabereihenfolge darf den kanonischen Manifest- oder Paketdigest
   nicht verändern. Eine inhaltliche Änderung eines Diffabschnitts muss den
   Digest verändern.
6. Slice- und Korrekturpakete verwenden dieselbe Semantik. Ein
   Korrekturpaket bleibt weiterhin auf die betroffenen offenen Findings und
   das Korrekturdelta begrenzt.
7. Der native Claude-Request erhält den Abdeckungsnachweis über das bestehende
   Reviewpaket beziehungsweise dessen gebundene Evidenz. Es entsteht kein
   paralleler State-Mirror und keine zweite unabhängige Digestautorität.
8. Bestehende Legacy-Reviewer erhalten weiterhin ein inhaltlich
   gleichwertiges kanonisches Basispaket. Dieser Slice migriert Antigravity
   nicht auf natives JSON.

## Harte Umfangsgrenze

- Genau ein eigenständig implementier- und reviewbarer Slice.
- Höchstens fünf produktive Änderungsdateien; Testdateien und das automatisch
  abgeleitete Sliceprotokoll zählen nicht als produktive Dateien.
- Vor Aufnahme jedes produktiven Pfads muss der Plan dessen aktuellen
  Aufrufer und Integrationsgrund im Repository belegen.
- Bestehende ReviewPacket-, Manifest-, Request- und Digesttypen sind zu
  erweitern. Eine neue allgemeine Snapshotengine, Datenbank, Recordfamilie
  oder ein zweiter Evidence-Store ist nicht erlaubt.
- Passt der geschlossene vertikale Durchstich nicht in diese Grenze, endet
  die Planung mit `PLAN_READY: NO` und benennt den kleinsten ausführbaren
  Folgeschnitt. Der Scope wird nicht informell erweitert.

## Verbindliche Testplanung

Der Plan muss mindestens folgende synthetische, providerfreie Nachweise
konkret verorten:

- zwei autorisierte geänderte Dateien erzeugen genau zwei kanonisch sortierte
  Einträge mit unterschiedlichen Abschnittsdigests;
- vertauschte Eingabereihenfolge erzeugt byteidentische kanonische Bytes und
  denselben Paketdigest;
- Änderung einer einzigen Diffzeile verändert Abschnitts- und Paketdigest;
- fehlende Abdeckung, doppelter Diffabschnitt, Pfad außerhalb der Allowlist,
  unsicherer Pfad und mehrdeutige Rename-/Binary-Darstellung scheitern
  fail-closed vor einem Provideraufruf;
- normale Unified-Diff-Hunks behalten ihre vollständigen Bereichsheader und
  werden nicht aufgrund von Zeileninhalten oder Markdowntext fehlklassifiziert;
- Slice- und Korrekturpakete verwenden dasselbe Manifestformat; das
  Korrekturpaket enthält weiterhin nur betroffene Findings und das gebundene
  Delta;
- der native Claude-Request bindet das Paket samt Manifestdigest in seine
  Request-ID; eine Manifestmanipulation wird bei Validierung oder Rebuild
  erkannt;
- bestehende ReviewPacket-, native Request-, Finding-, Resume- und
  Legacy-Transportregressionen bleiben unverändert grün;
- die vollständige Repositorymatrix wird ausschließlich vom Orchestrator
  ausgeführt.

Tests verwenden nur synthetische Diffs, Fake-Adapter und temporäre
Verzeichnisse. Echte Provideraufrufe, Netz, Zugangsdaten und freie
Repositoryexploration innerhalb der Produkttests sind unzulässig.

## Nichtziele

Nicht Bestandteil dieses Smoke-Test-Pakets sind:

- der hashgebundene Snapshot und freie beziehungsweise modellgesteuerte
  Leseplan für große Finalreviews;
- vollständige semantische Dependency- oder Callgraphanalyse;
- Finalreview-Kompaktierung und P2-FU-025;
- native Antigravity-Reviews;
- Abschalten oder Entfernen von Textparsern, Legacytests oder
  Kompatibilitätsmodulen;
- neue Finding-, Attestierungs-, Providerattempt- oder Workflowrecordtypen;
- Änderungen an Reviewerreihenfolge, Findingeigentum, Freigabesemantik,
  Quota-Wartepolitik oder Retrylimits;
- allgemeine Logging-, Kosten-, Telemetrie- oder UI-Arbeiten;
- Push, Merge oder History-Rewrite.

## Stopbedingungen

Der Planungslauf hält kontrolliert an, wenn:

- der Abdeckungsnachweis nur durch zusätzliche Repositoryreads oder eine neue
  Snapshotengine erzeugt werden könnte;
- Rename-, Copy- oder Binary-Diffs im bestehenden ReviewPacket-Vertrag nicht
  eindeutig und fail-closed abgrenzbar sind;
- Manifest- und Paketdigest nicht aus genau einer kanonischen Darstellung
  abgeleitet werden können;
- der native Request nur durch eine parallele, nicht an das Reviewpaket
  gebundene Evidenzstruktur erweitert werden könnte;
- eine bestehende Record-, Fingerprint-, Attestierungs-, Finding- oder
  Resume-Invariante gelockert werden müsste;
- mehr als ein Slice oder mehr als fünf produktive Änderungsdateien nötig
  wären.

Agenten führen keine vollständige Validierungsmatrix aus und emittieren kein
`VALIDATION_RESULT`.

## Erwartete Ausgabe

Der Arbeitsplan wird unter
`docs/internal/native-semantic-evidence-manifest-slice-reviews-arbeitsplan.md`
erstellt. Er erfüllt den kanonischen `PLAN_ONLY`-Handoff-Vertrag mit genau
einem zukünftigen Implementierungsslice, einer zusammenhängenden
`### Slice 1 - ...`-Überschrift und dem eigenständigen Abschnitt
`**Exakter Änderungspfad**` mit ausschließlich bullet-gelisteten exakten
repository-relativen Pfaden.
