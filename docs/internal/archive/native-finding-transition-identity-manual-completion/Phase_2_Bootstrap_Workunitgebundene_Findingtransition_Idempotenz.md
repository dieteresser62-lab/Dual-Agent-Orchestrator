# Phase 2 – Bootstrap-Smoke-Test: Work-Unit-gebundene Findingtransition-Idempotenz

TARGET_BRANCH: feature/native-finding-transition-identity

## Auftrag

Erstelle zunaechst ausschliesslich einen repository-grounded, unmittelbar
ausfuehrbaren Arbeitsplan fuer einen kleinen Bootstrap-Haertungsslice. Der
Auftrag ist `PLAN_ONLY`: Im Planungslauf duerfen nur der deterministisch
abgeleitete Arbeitsplan und orchestratorisch verwaltete Planartefakte
entstehen. Produktcode und Produkttests bleiben bis zum automatisch erzeugten
Implementierungshandoff unveraendert.

Der Lauf ist ein neuer Bootstrap-Smoke-Test auf Basis von `master`. Der
historische AP6A-Lauf wird weder fortgesetzt noch veraendert. Codex und Claude
werden gemeinsam mit `--native-codex-results --native-claude-reviews` an ihre
nativen JSON-Vertraege gebunden; ein Textmarkerfallback fuer diese Rollen ist
untersagt.

## Ausgangspunkt

AP6A ist fachlich umgesetzt, vollstaendig validiert, direkt von Claude
genehmigt, archiviert und nach `master` integriert. Sein kontrolliert
abgebrochener State-v3-Lauf und seine append-only Recordkette bleiben
historische Evidenz.

Der AP6A-Direktabschluss hat ein enges Restrisiko benannt: Reine
`OPEN -> OPEN`-Begruendungsrevisionen verwenden bereits eine von der Work-Unit
abhaengige Idempotenzidentitaet. Andere echte Findingtransitionen wie
`opened`, `reclassified` und ein Statuswechsel verwenden dagegen noch eine
Identitaet aus Finding-ID, Transitionsart, Rundennummer und Reviewer. Gleiche
Finding-ID, Runde und Rolle koennen in verschiedenen Work-Units wiederkehren.
Ein daraus entstehender Schluesselkonflikt bricht zwar fail-closed ab, kann
aber eine legitime spaetere Reviewrunde unnoetig blockieren.

## Ziel

Der Arbeitsplan beschreibt genau einen Implementierungsslice, der die
Idempotenzidentitaet aller neu geschriebenen strukturierten
Findingtransitionen eindeutig an ihre Work-Unit bindet:

1. `opened`, `reclassified`, echte Statuswechsel und reine
   Statusbegruendungsrevisionen verwenden fuer neue strukturierte Records eine
   stabile Work-Unit-Bindung.
2. Dieselbe Transition derselben Work-Unit bleibt bei Wiederholung
   idempotent und erzeugt keinen zweiten Record.
3. Dieselbe Finding-ID, Transitionsart, Rundennummer und Reviewerrolle darf in
   zwei verschiedenen Work-Units nicht kollidieren; beide fachlich
   verschiedenen Transitionen muessen autoritativ replaybar sein.
4. Mehrere unterschiedliche Transitionen desselben Findings innerhalb einer
   Work-Unit, insbesondere Begruendungsrevision und spaeterer echter
   Statuswechsel, behalten getrennte Identitaeten.
5. Record-Replay, Findingprojektion und State-v3-Spiegel bleiben nach jedem
   Append semantisch symmetrisch und fail-closed.
6. Historische Recordketten werden nicht umgeschrieben. Bereits vorhandene
   Legacy-Idempotenzschluessel bleiben lesbar; es gibt keine heuristische
   Migration und keinen stillen Protokollwechsel.
7. Der Fix darf weder Findingeigentum noch Reviewerreihenfolge,
   Freigabesemantik, Retrylimits, Provideroperations-ID oder AP6A-
   Evidenzmanifestsemantik veraendern.

## Umfangsgrenze

- Genau ein eigenstaendig implementier- und reviewbarer Slice.
- Erwartet wird eine kleine Aenderung am bestehenden Findingtransition-Writer
  mit fokussierten Regressionstests; maximal zwei produktive
  Aenderungsdateien.
- Bestehende Recordtypen und Schemas sind wiederzuverwenden. Eine neue
  Recordfamilie, ein zweiter Findingstore oder ein paralleler State-Mirror ist
  nicht erlaubt.
- Der Plan muss vor Aufnahme jedes produktiven Pfads dessen aktuellen Aufrufer
  und Integrationsgrund im Repository belegen.
- Falls der geschlossene Fix nicht innerhalb dieser Grenze moeglich ist, endet
  die Planung mit nicht bereitem Plan und benennt den kleinsten sicheren
  Folgeschnitt. Der Scope wird nicht informell erweitert.

## Verbindliche Testplanung

Der Arbeitsplan muss mindestens folgende providerfreie Nachweise konkret
verorten:

- zweimaliges Persistieren derselben strukturierten Transition in derselben
  Work-Unit erzeugt genau einen Record;
- dieselbe Finding-ID, Runde, Rolle und Transitionsart in zwei verschiedenen
  Work-Units erzeugt zwei getrennte, gueltige Records ohne
  Idempotenzkollision;
- `OPEN -> OPEN` mit neuer Begruendung und ein spaeterer `OPEN -> CLOSED`-
  Wechsel bleiben getrennt und projizieren die letzte Begruendung sowie den
  terminalen Status korrekt;
- Reclassification und Statuswechsel behalten unterschiedliche Identitaeten;
- Replay nach Prozessunterbrechung rekonstruiert denselben Findingstand wie
  der State-v3-Spiegel und startet keinen Provider wegen eines bereits
  vollstaendig persistierten Ergebnisses erneut;
- eine historische Kette mit bisherigen Idempotenzschluesseln bleibt lesbar;
- nicht strukturierte beziehungsweise historisch gebundene Pfade aendern ihr
  bisheriges Verhalten nicht;
- `git diff --check` und die vom Orchestrator ausgefuehrte vollstaendige
  Repositorymatrix bleiben gruen.

Tests verwenden ausschliesslich temporaere Repositories, synthetische
Findings und Fake-Adapter. Echte Provider-, Netzwerk- oder Quota-Aufrufe sind
in Produkttests unzulaessig. Agenten fuehren die vollstaendige
Validierungsmatrix nicht selbst aus.

## Nichtziele

Nicht Bestandteil dieses Pakets sind:

- weitere AP6-Evidence-Builder-, Snapshot- oder Leseplanfunktionen;
- native Antigravity-Integration;
- allgemeiner State-v3- oder Recordkettenumbau;
- manuelle Reparatur des abgebrochenen AP6A-Laufs;
- neue Findingklassen, Reklassifizierungsregeln oder Akzeptanztestformate;
- Aenderungen an Watch-Retry, Quota-Wartepolitik, Pfadgates oder
  Finalreview-Kompaktierung;
- Abschalten von Legacyparsern oder Kompatibilitaetsmodulen;
- Push, Merge, Rebase oder History-Rewrite.

## Stopbedingungen

Die Planung oder Implementierung haelt kontrolliert an, wenn:

- historische Records umgeschrieben werden muessten;
- die Work-Unit-Bindung nicht vor dem Append aus autoritativem Laufzustand
  bestimmt werden kann;
- derselbe fachliche Record bei Resume eine andere Identitaet erhalten wuerde;
- die Loesung eine neue Recordfamilie oder mehr als zwei produktive
  Aenderungsdateien erfordert;
- eine Record-/Mirror-, Findingeigentums-, Fingerprint- oder
  Providerattempt-Invariante gelockert werden muesste.

## Erwartete Ausgabe

Der Arbeitsplan wird unter
`docs/internal/phase-2-bootstrap-workunitgebundene-findingtransition-idempotenz-arbeitsplan.md`
erstellt. Er erfuellt den kanonischen `PLAN_ONLY`-Handoff-Vertrag mit genau
einem zukuenftigen Implementierungsslice, einer zusammenhaengenden
`### Slice 1 - ...`-Ueberschrift und dem eigenstaendigen Abschnitt
`**Exakter Änderungspfad**` mit ausschliesslich bullet-gelisteten exakten
repository-relativen Pfaden.
