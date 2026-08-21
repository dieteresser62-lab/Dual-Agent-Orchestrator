# Arbeitsplan: Release 1.1B – Deterministische Reviewverträge und schlanke Reviewpakete

**Status:** zur Planprüfung vorgelegt

**Feature-Branch:** `feature/orchestrator-stabilization-1-1b`

**GitHub-Status:** nur lokal

**Anforderungsbasis:**
`docs/internal/phase-2-arbeitspaket-1-erkenntnisse-und-stabilisierung-1-1.md`,
Nachtrag zu P2-FU-007, P2-FU-023, P2-FU-024 und Kostenbremsen aus
P2-DEC-001

**Ausführungsgrenze:** genau zwei zukünftige Implementierungsslices; höchstens
sieben produktive Änderungsdateien je Slice

## Zielbild in eigenen Worten

Release 1.1B trennt die lokale Behandlung eines Reviewertexts strikt von der
fachlichen Reviewentscheidung. Ein erfolgreich beendeter Provideraufruf wird
zuerst mit einem gebundenen `StepContract` syntaktisch normalisiert und nur um
solche Metadaten ergänzt, für die genau ein Vertragswert existiert. Erst das
danach weiterhin mehrdeutige oder fachlich unvollständige Ergebnis darf einen
kompakten Provider-Reparaturaufruf auslösen. Vorhandene Gegenwerte, unbekannter
Providerabschluss und Trunkierungsverdacht bleiben fail-closed.

Slice- und Korrekturreviews werden anschließend aus einem gemeinsamen,
kanonisch serialisierten Basispaket gespeist. Dieses enthält nur den aktuellen
Slice-Diff beziehungsweise das Korrekturdelta, die Akzeptanzkriterien des
gebundenen Plans, eine kompakte PASS-Attestierung und die für die Entscheidung
nötigen Findingdaten. Reviewerrolle und die asymmetrische Claude-vor-
Antigravity-Freigabe stehen in einer kleinen Hülle außerhalb des Basisdigests.
Der bestehende branchweite Finalreviewpfad bleibt unverändert vollständig.

## Branch-, Status- und Scope-Festlegung

- Bei der Planung ist der verlangte Branch
  `feature/orchestrator-stabilization-1-1b` aktiv.
- Dieser Planungslauf ändert ausschließlich dieses Arbeitsplandokument. Er
  ändert weder Produktcode noch Tests, Konfiguration, State, Records,
  Checkpoints oder generierte Laufartefakte.
- Die spätere Implementierung ist exakt auf die Pfade der beiden Slice-
  Abschnitte begrenzt. Slice 1 enthält fünf, Slice 2 fünf produktive Pfade;
  Testdateien, Fixtures und das jeweils orchestratorisch verwaltete
  Sliceprotokoll zählen nicht gegen die Sieben-Dateien-Grenze.
- Die Pfadlisten sind Obergrenzen, keine Pflicht zu sachlich unnötigen
  Änderungen. Stellt sich ein gelisteter Produktpfad bei der Umsetzung als
  unnötig heraus, bleibt er unverändert; ein nicht gelisteter Pfad löst dagegen
  `UNEXPECTED-PATH` aus.
- Push, Merge, Branchwechsel, Staging, Commit, manuelle State-/Record- oder
  Auditkorrekturen und die Bereinigung fremder Arbeitsstände sind nicht Teil
  der Slices.

## Repositorybefund und konkrete Integrationsgründe

| Produktiver Pfad | Heutige Verantwortung | Konkreter Integrationsgrund für 1.1B |
|---|---|---|
| `src/contracts.py` | strikte Marker-, Rollen-, Testdatei-, Evidence-, Finding- und Approvalvalidierung gegen `StepContract` | Die kanonische `REVIEW_EVIDENCE`-Form wird heute mit `split("|", 2)` gelesen. Für bytegetreue Fachinhalte mit Pipes braucht der bestehende strikte Parser eine eindeutige Escape-/Decode-Regel; Approval-, Finding- und Stopsemantik bleiben hier unverändert. |
| `src/workflow.py` | lokale Reviewnormalisierung, Aufbau des `StepContract`, Auswahl von Full-Slice/Korrekturdelta und Entscheidung über den Provider-Reparaturaufruf | `normalize_review_contract_output()` erkennt derzeit nur Doppelpunktlabels, ergänzt `TEST_FILES_TOUCHED` nur nach bereits korrektem Reviewer und erzeugt synthetische Findingstatusprosa. `_validate_or_repair_review()` startet bei jedem verbleibenden Parserfehler eine Reparatur. `_review_evidence()` bettet außerdem den vollständigen `distilled_context` und sämtliche Findinghistorien ein; Antigravity erhält nach einer Claude-Korrektur heute wieder `FULL_SLICE`. |
| `src/orchestrator.py` | physischer Reviewer-/Reparaturaufruf, Providerinputmessung, Korrekturdelta und Reviewer-Workspace-Anschluss | `repair_review_contract()` verwendet heute dieselbe Workflowoperation wie das Fachreview; Grund und Zusatzkosten sind dadurch nicht getrennt adressierbar. `invoke_reviewer()` reicht außerdem nur ein Ja/Nein für den Repository-Snapshot weiter, obwohl `ReviewerInvocation.paths` bereits die exakten Änderungspfade trägt. |
| `src/agent_runtime.py` | Providerprozess, terminaler Prozessstatus, Adaptermetadaten zu Laufzeit/Turns/Usage sowie Erzeugung des schreibgeschützten Reviewer-Snapshots | Der Snapshot kopiert derzeit alle getrackten beziehungsweise nicht ignorierten Dateien. Die Runtime ist zugleich die einzige Grenze, die erfolgreichen Prozessabschluss und zusätzliche Laufzeit-/Usage-Metadaten eines Reparaturprozesses zuverlässig kennt. |
| `src/provider_input_budget.py` | erlaubte Provideroperationen, Komponenten und inhaltsbezogene Inputmessung | Ein Reparaturprozess ist derzeit nicht als eigene Claude-/Antigravity-Operation registriert. Eine kleine explizite Operationstrennung macht Reparaturinput und physischen Zusatzaufruf messbar, ohne allgemeine Usage-Telemetrie einzuführen. |
| `src/review_packets.py` | neuer, reiner Paketkern | Ein eigener dateisystemfreier Kern kapselt kanonische Serialisierung, Digest, Slice-/Korrekturmanifest, kompakte Findingprojektion und rollenspezifische Ergänzung. Damit bleibt die Digestgrenze unabhängig von Prompt- und Adapterhüllen testbar. |
| `src/prompts.py` | Reviewvertrag und Promptumschlag | `build_v3_review_prompt()` mischt heute Auftrag, Evidenz und Rollenvertrag in einen einzigen Text. Es muss das unveränderte Basispaket plus eine kleine Rollen-/Reihenfolgeergänzung transportieren, ohne den gemeinsamen Basisdigest zu verändern. |

`src/artifact_models.py`, `src/artifact_store.py`, `src/artifact_replay.py`,
`src/artifact_migration.py` und `src/workflow_state.py` bleiben unverändert. Die
benötigten Vertrags-, Korrektur- und Fingerprintdaten sind bereits im
`StepContract`, in `WorkflowState.current_slice.start_fingerprint`, in der
Attestierung und in der validierten Recordkette vorhanden. Das Reviewpaket ist
eine inhaltsadressierte, vollständig rekonstruierbare Ableitung und wird keine
zweite fachliche Wahrheitsquelle.

## Verbindliche Entscheidungen und Umsetzungskonsequenzen

1. Die lokale Verarbeitung arbeitet in drei expliziten Stufen:
   syntaktische Evidence-Normalisierung, deterministische Metadatenergänzung,
   unveränderte strikte Gesamtvalidierung. Nur wenn Stufe drei noch eine
   semantische Mehrdeutigkeit meldet, ist eine Providerreparatur zulässig.
2. Eine alternative gelabelte `REVIEW_EVIDENCE`-Form ist nur eindeutig, wenn
   die drei Labels `Checked dimensions`, `Largest residual risk` und
   `Realistic break condition` in dieser Reihenfolge jeweils genau einmal
   vorkommen und jedes Label durch genau einen der Trenner Doppelpunkt,
   Bindestrich, Gedankenstrich oder Halbgeviertstrich vom nichtleeren Wert
   getrennt ist. Groß-/Kleinschreibung und umgebender horizontaler Leerraum
   dürfen syntaktisch normalisiert werden. Die bereits kanonische
   Dreifeld-Pipeform bleibt gültig.
3. Fehlende, doppelte, vertauschte oder unbekannte Labels, mehr als eine
   `REVIEW_EVIDENCE`-Zeile und mehrdeutige Trenner werden nicht geraten. Pipes
   und Backslashes innerhalb eines Feldwerts werden reversibel escaped; nach
   dem Parsen müssen alle drei fachlichen Werte bytegleich zum Modelltext sein.
4. Ein fehlender Reviewer darf nur auf den einen `contract.reviewer` ergänzt
   werden. Ein fehlendes `TEST_FILES_TOUCHED` darf nur bytegenau aus dem bereits
   sortierten `contract.expected_test_files` beziehungsweise `NONE` entstehen.
   Ein Slice-Bezeichner darf nur in einem vorhandenen, eindeutig geformten
   Approvalrecord mit explizitem unverändertem `YES` oder `NO` ergänzt werden;
   der Approvalmarker oder das Verdict selbst werden niemals erzeugt.
5. Jeder vorhandene Wert hat Vorrang vor lokaler Ableitung: falscher Reviewer,
   falscher Slice, abweichende Testdateiliste, doppelte Marker, konkurrierende
   Verdicts oder widersprüchliche Abschlussmarker bleiben Vertragsfehler. Es
   gibt kein stilles Überschreiben und keinen Majority- oder Last-marker-wins-
   Pfad.
6. `STATUS: DONE` darf nur nach einem nachgewiesen erfolgreichen physischen
   Providerabschluss als letzter Marker ergänzt werden. Der Kandidat muss mit
   Ausnahme genau dieses Markers bereits vollständig und widerspruchsfrei
   validierbar sein. Adapter-/Providerfehler, Quota, Timeout, nicht erfolgreicher
   Exit, Recovery aus unvollständigem Fehlertext, fehlendes Verdict oder
   Trunkierungsindizien verbieten die Ergänzung.
7. Lokale Verarbeitung verändert niemals Findingtext, Findingklasse,
   Findingstatus, Statusbegründung, Approval/Verdict, Pre-Mortem,
   Stopentscheidung oder Rationale. Insbesondere entfällt die heutige lokale
   Erzeugung der Prosa `Carried forward unchanged`; ein vom Reviewer
   erforderlicher eigener Findingstatus bleibt eine fachliche Pflicht und darf
   nicht lokal erfunden werden.
8. Ein lokaler Normalisierungsbericht benennt die angewandten rein
   syntaktischen Schritte, Original-/Ergebnisdigest, Rolle, Schritt und
   Fingerprint. Er zählt weder als Providerturn noch als Reviewrunde und wird
   bei Resume nicht als zweite Entscheidung persistiert. Die kanonische
   Reviewerentscheidung bleibt der einzige `ReviewPayload` für Rolle,
   Work-Unit, Runde und Fingerprint.
9. Reparaturaufrufe erhalten eine eigene Provideroperation je Reviewer. Vor
   ihrem Start wird der konkrete verbleibende Vertragsfehler protokolliert;
   nach ihrem Ende werden zusätzliche Laufzeit, Turns und verfügbare
   Token-/Kostenmetadaten getrennt vom Fachreview ausgegeben. Es bleibt bei
   höchstens einem kompakten Reparaturaufruf je physischem Fachaufruf.
10. Das gemeinsame Reviewbasispaket ist kanonisches UTF-8-JSON mit fester
    Feldreihenfolge und ohne Zeit-, Rollen- oder Transportdaten. Sein SHA-256-
    Digest bindet Reviewzweck (`slice` oder `correction`), aktuellen
    Repositoryfingerprint, Startgrenze, exakte Pfade, Diff-/Deltatext,
    Akzeptanzkriterien, kompakte Attestierung und Findingprojektion.
11. Ein normales Slicepaket enthält nur den aktuellen Slice-Diff, die
    Akzeptanzkriterien aus dem freigegebenen Sliceabschnitt, die kompakte
    gebundene PASS-Attestierung und offene Findings mit Klasse, Eigentümer,
    Kurztext und Akzeptanztest. Die vollständige Assignmentprosa,
    `distilled_context`, Auditblöcke, alte Reviewerantworten, Findingresponses
    und vollständige Recordhistorien werden nicht aufgenommen.
12. Ein Korrekturpaket verwendet für Claude und Antigravity dasselbe Delta von
    `current_slice.start_fingerprint` zum aktuellen Fingerprint. Es enthält nur
    betroffene offene Findings und deren Akzeptanztests. Soweit eine Closure-
    Referenz für die Konvergenz nötig ist, besteht sie aus ID, `CLOSED`, Digest
    der Closuretatsache und einer normalisierten einzeiligen Kurzfassung;
    vollständige Closurebegründungen und Antworten bleiben ausgeschlossen.
13. Claude und Antigravity erhalten denselben Basisdigest. Die Rollenbezeichnung,
    der nächste reviewer-eigene Findingbezeichner und bei Antigravity der
    Nachweis der vorherigen Claude-Freigabe sind eine kleine explizite Hülle.
    Diese Hülle darf den Basisdigest nicht beeinflussen. Ohne fingerprintgleiche
    Claude-Freigabe wird Antigravity weiterhin nicht aufgerufen.
14. Das Basispaket wird unter seinem Digest materialisiert oder bei
    Bytegleichheit wiederverwendet. Ein vorhandenes gleichnamiges, aber
    byteabweichendes Paket hält fail-closed an. Resume rekonstruiert denselben
    Digest aus autoritativen Eingaben und erzeugt weder eine neue Reviewrunde
    noch eine zweite semantische Entscheidung.
15. Der Reviewer-Snapshot enthält nur die im Manifest genannten Slice-Pfade;
    für den vertikalen 1.1B-Durchstich ist die Liste zusätzlicher
    Repositoryabhängigkeiten standardmäßig leer. Es gibt keine heuristische
    136-Dateien-Ausweitung. Nicht manifestierte Reads schlagen sichtbar fehl,
    statt sachfremde Dateien nachzuladen.
16. Planreviews und branchweite Finalreviews behalten ihre heutige
    Evidenztiefe. Findingeigentum, PASS-Attestierung, Claude-vor-Antigravity,
    Fingerprintbindung, strukturierter Replay, Record-/Mirror-Abgleich und
    fail-closed Resume werden nicht gelockert.

## Slice-Liste

### Slice 1 - Lokale Reviewverträge fail-closed vervollständigen

**Ziel**

Die lokale, semantisch neutrale Verarbeitung vollständig abschließen: alle
eindeutigen Evidence-Trenner kanonisieren, exakt gebundene Pflichtmetadaten
ergänzen, den sicheren Abschlussmarker eng begrenzen und nur echte semantische
Restfehler an einen separat messbaren Reparaturprozess geben. Der Slice ist
eigenständig testbar und ändert weder Reviewreihenfolge noch Paketumfang.

**Exakter Änderungspfad**

- `src/contracts.py`
- `src/workflow.py`
- `src/orchestrator.py`
- `src/agent_runtime.py`
- `src/provider_input_budget.py`
- `tests/fixtures/review_responses/claude-slice-review-em-dash-evidence.txt`
- `tests/test_contracts.py`
- `tests/test_review_runtime_hardening.py`
- `tests/test_workflow.py`
- `tests/test_orchestrator_runtime.py`
- `tests/test_agent_runtime.py`
- `tests/test_provider_input_budget.py`
- `docs/internal/slice-release-1-1b-deterministische-reviewvertraege-und-schlanke-reviewpakete-01-lokale-reviewvertrage-fail-closed-vervollstandigen.md`

#### Integrationsschritte

1. `src/contracts.py` erhält eine kleine kanonische Encode-/Decodegrenze für
   die drei Evidence-Felder. Nur nicht escapte Feldtrenner strukturieren den
   Record; decodierte Werte werden unverändert in `ReviewEvidence` übernommen.
   Alle übrigen Markerparser und fachlichen Positivbedingungen bleiben
   unverändert streng.
2. `src/workflow.py` ersetzt die implizite Textmutation durch ein typisiertes
   Normalisierungsergebnis mit Änderungen und Diagnosegrund. Der Evidence-
   Scanner verwendet eine feste Label-/Trennermatrix und lineare Suche, ergänzt
   Vertragsmetadaten nur bei Abwesenheit und prüft Widersprüche vor jeder
   Einfügung.
3. Derselbe Workflowpfad behandelt einen fehlenden Schlussmarker als
   Sonderfall: Nur die Runtimebestätigung des erfolgreich beendeten primären
   Providerprozesses erlaubt einen Kandidaten mit angehängtem `STATUS: DONE`;
   dieser Kandidat muss danach den vollständigen bestehenden Vertrag bestehen.
4. `_validate_or_repair_review()` klassifiziert Parserfehler in lokal sicher,
   semantisch mehrdeutig und nicht reparaturfähig. Nur die mittlere Klasse darf
   den vorhandenen einmaligen Kompakt-Reparaturpfad aufrufen. Rollenabweichung,
   widersprüchliche Semantik, Providerfehler und Trunkierung halten ohne
   Reparaturaufruf an.
5. `src/provider_input_budget.py`, `src/agent_runtime.py` und
   `src/orchestrator.py` kennzeichnen den Reparaturprozess als eigene zulässige
   Claude-/Antigravity-Operation. Die vorhandene Inputmessung wird getrennt
   persistiert; Abschlusslogging ordnet nur diesem Zusatzprozess Laufzeit,
   Turns und vorhandene Usage-/Kostenfelder zu. Fehlende Provider-Metadaten
   werden als nicht verfügbar ausgewiesen, nicht geschätzt.

#### Akzeptanzkriterien

- Das gespeicherte, tatsächlich beobachtete Claude-Review mit
  `Checked dimensions —`, `Largest residual risk —` und
  `Realistic break condition —` validiert lokal; der Fake-
  Reparaturaufrufzähler bleibt null.
- Eindeutige Doppelpunkt-, Bindestrich-, Gedankenstrich- und
  Halbgeviertstrichvarianten ergeben dieselben drei kanonischen Werte.
  Fehlende, doppelte, vertauschte oder gemischte Labels bleiben Vertragsfehler.
- Pipes und Backslashes innerhalb aller drei Evidence-Inhalte überstehen
  Normalize-Encode-Parse bytegetreu und können nicht als zusätzlicher
  Vertragstrenner wirken.
- Eine vollständige Claude-Entscheidung ohne `TEST_FILES_TOUCHED` erhält exakt
  `StepContract.expected_test_files`; weder ein zweiter Providerprozess noch
  eine zweite Reviewrunde entsteht.
- Fehlender Reviewer beziehungsweise fehlender Slice-Bezeichner werden nur aus
  einem einzigen gebundenen Vertragswert ergänzt. Ein vorhandener falscher
  Reviewer, eine vorhandene abweichende Testliste und ein vorhandener falscher
  Slice werden ohne Überschreiben abgelehnt.
- Ein fehlendes `STATUS: DONE` wird ausschließlich nach terminal erfolgreichem
  Providerstatus und sonst vollständiger Vertragsvalidierung ergänzt.
  Providerfehler, Timeout/Quota, widersprüchlicher Abschlussmarker, fehlendes
  Verdict und synthetischer Trunkierungsfall bleiben fail-closed.
- Findingzeilen, Findingklasse/-status, Approval/Verdict, Pre-Mortem,
  Stopentscheidung und sämtliche Rationales sind vor und nach lokaler
  Verarbeitung bytegleich. Kein lokaler Pfad erzeugt einen fehlenden
  Findingstatus oder ein fehlendes Approval.
- Wiederholte Normalisierung und Resume sind idempotent: Es entsteht genau eine
  kanonische Reviewerentscheidung für Rolle, Work-Unit, Runde und Fingerprint;
  lokale Schritte erhöhen weder Providerturn- noch Reviewrundenzähler.
- Ein absichtlich mehrdeutiger, fachlich ansonsten reparierbarer Text löst
  genau einen Reparaturaufruf mit protokolliertem Grund und separat
  zuordenbarer Inputmessung, Laufzeit und verfügbarer Usage aus. Ein zweiter
  ungültiger Text hält geordnet an.

#### Geplante fokussierte Tests

- `tests/test_contracts.py`: escaped Pipes/Backslashes, kanonischer Roundtrip,
  unveränderte strikte Approval-, Finding-, Stop- und Positivanforderungen.
- `tests/test_review_runtime_hardening.py` plus gespeichertes Fixture:
  reale Gedankenstrichantwort, alle vier Trenner, Labelmehrdeutigkeit,
  Unicode/Linearzeit und Byteerhaltung semantischer Modellfelder.
- `tests/test_workflow.py`: Fake-Adapter- und Repair-Counter, eindeutige
  Metadatenergänzung, falsche Gegenwerte, Terminalstatus-/Trunkierungsmatrix,
  keine synthetischen Findingstatus sowie Resume-/Idempotenzregression.
- `tests/test_orchestrator_runtime.py`: erfolgreich/fehlgeschlagen beendete
  Fake-Provider, getrennte Reparaturoperation und keine Antigravity-Fortsetzung
  nach fail-closed Claude-Vertragsfehler.
- `tests/test_agent_runtime.py` und `tests/test_provider_input_budget.py`:
  getrennte Reparaturmessung mit Fake-Metadaten, Laufzeit/Turns/Usage sowie
  vollständige Budgettabelle ohne echte Provider oder Zugangsdaten.

#### Risiko und Rückfalloption

Das größte Risiko ist, Interpunktion in freier Fachprosa fälschlich als Label-
Trenner zu interpretieren. Deshalb ist die Erkennung auf drei vollständige,
einmalige Labels in fester Reihenfolge beschränkt und arbeitet nicht mit
unscharfen Synonymen. Bei einem nicht bewiesenen Fall wird der Originaltext
nicht verändert. Der Slice kann auf den bisherigen Reparaturpfad zurückfallen,
ohne Record-, State- oder Reviewsemantik zu migrieren; die neue kanonische
Evidence-Decodierung bleibt rückwärtskompatibel zur bisherigen Pipeform.

### Slice 2 - Slice- und Korrekturreviewpakete kanonisch minimieren

**Ziel**

Für normale Slice- und Korrekturreviews ein gemeinsames, byteidentisches und
inhaltsadressiertes Evidenzbasispaket einführen, beide Reviewer darauf binden
und den lesbaren Repositorysnapshot auf exakt manifestierte Pfade begrenzen.
Plan- und Finalreview bleiben außerhalb dieses vertikalen Durchstichs.

**Exakter Änderungspfad**

- `src/review_packets.py`
- `src/workflow.py`
- `src/prompts.py`
- `src/orchestrator.py`
- `src/agent_runtime.py`
- `tests/test_review_packets.py`
- `tests/test_workflow.py`
- `tests/test_prompts.py`
- `tests/test_orchestrator_runtime.py`
- `tests/test_agent_runtime.py`
- `tests/test_structured_artifact_regressions.py`
- `docs/internal/slice-release-1-1b-deterministische-reviewvertraege-und-schlanke-reviewpakete-02-slice-und-korrekturreviewpakete-kanonisch-minimieren.md`

#### Integrationsschritte

1. `src/review_packets.py` definiert immutable Pakettypen und eine kanonische
   UTF-8-Serialisierung. Der Builder akzeptiert ausschließlich explizite
   fachliche Eingaben, sortiert Mengen an der Grenze, lehnt doppelte oder
   fingerprintfremde Bestandteile ab und berechnet Digest und Manifest aus
   exakt denselben Bytes.
2. Der Builder extrahiert aus dem freigegebenen Work-Plan nur Ziel und
   Akzeptanzkriterien des aktuellen `### Slice N`-Abschnitts. Er stoppt bei
   fehlender/eindeutig nicht zuordenbarer Slice-Sektion, statt die gesamte
   Plan-, Assignment- oder Auditprosa einzubetten.
3. `src/workflow.py` ersetzt `_review_evidence()` für
   `SLICE_APPROVAL`-Reviews durch den Paketbuilder. Normale Slices erhalten
   `changes.full_diff`; Korrektur-Work-Units verwenden für beide Reviewer den
   persistierten `current_slice.start_fingerprint` als Deltastart. Der
   `FULL_BRANCH`- und der Planreviewzweig bleiben unverändert.
4. Offene Findings werden als kompakte aktive Datensätze projiziert.
   Geschlossene Findings erscheinen nur, wenn ihre Closure für eines der
   betroffenen Findings benötigt wird, und dann ausschließlich als ID, Status,
   Closure-Digest und einzeilige Kurzfassung. Responses, vollständige
   Rationales, alte Revieweroutputs und Auditprojektionen sind keine
   Paketquellen.
5. `src/prompts.py` rendert die Basisbytes unverändert in einer abgegrenzten
   Evidenzsektion und ergänzt Rolle, Finding-ID-Namensraum, Antwortvertrag und
   bei Antigravity die fingerprintgleiche Claude-Vorfreigabe separat. Beide
   Transporthüllen nennen denselben Basisdigest.
6. `src/orchestrator.py` materialisiert das Basispaket inhaltsadressiert im
   Laufartefaktbereich. Existierende bytegleiche Pakete werden wiederverwendet;
   Digest-/Inhaltsabweichung hält vor dem Providerstart an. Diese Dateien sind
   rekonstruierbare Caches, keine Reparaturquelle und keine neue
   Reviewentscheidung.
7. `src/agent_runtime.py` erzeugt für diese beiden Reviewzwecke den
   schreibgeschützten Workspace nur aus den manifestierten Slice-Pfaden. Die
   Liste zusätzlicher Abhängigkeiten ist in 1.1B leer; Symlinks, Pfadflucht,
   fehlende Pfade und Nachladen nicht manifestierter Dateien bleiben
   fail-closed. Der vollständige Snapshotpfad bleibt ausschließlich für den
   unveränderten Finalreview erhalten.

#### Akzeptanzkriterien

- Ein synthetisches normales Slicepaket enthält Slice-Diff,
  Slice-Akzeptanzkriterien, kompakte PASS-Attestierung und offene Findings,
  jedoch keine verwaltete Auditprosa, geschlossenen Volltexte, vollständige
  Recordhistorie, alte Reviewerantworten, Findingresponses oder sachfremde
  Repositorydateien.
- Ein synthetisches Korrekturpaket enthält ausschließlich das Delta seit dem
  abgelehnten, in `current_slice.start_fingerprint` gebundenen Fingerprint, die
  betroffenen offenen Findings und Akzeptanztests sowie nötigenfalls kompakte
  Closure-Referenzen. Claude und Antigravity verwenden genau dieselben
  Basisbytes.
- Zweimalige Materialisierung und Resume mit demselben logischen Reviewzweck
  und Fingerprint ergeben byteidentische Basisbytes, denselben SHA-256-Digest
  und keine zusätzliche fachliche Reviewentscheidung.
- Claude- und Antigravity-Hüllen dürfen verschieden sein, weisen aber denselben
  semantischen Basisdigest aus. Die Antigravity-Hülle entsteht erst nach einer
  fingerprintgleichen Claude-Freigabe und kann diese Reihenfolge nicht
  umgehen.
- Fake-Aufrufzähler zeigen pro Rolle und Fingerprint genau einen fachlichen
  Revieweraufruf. Lokale Normalisierung bleibt außerhalb dieses Zählers; ein
  Reparaturaufruf ist nur mit der in Slice 1 definierten expliziten
  Mehrdeutigkeitsdiagnose möglich.
- Der Slice-Reviewer-Workspace enthält ausschließlich manifestierte Pfade.
  Ein Test mit einer sachfremden Datei und Audit-/Recorddateien weist nach,
  dass weder Paket noch Workspace diese an den Provider geben.
- Offene Findingklasse, Eigentum, Status, Text und Akzeptanztest bleiben
  vollständig prüfbar. Closure-Digest und Kurzfassung sind deterministisch;
  zwei verschiedene Closuretatsachen können nicht denselben Paketinhalt
  vortäuschen.
- Bestehende Plan-, Slice-, Korrektur-, Finalreview-, Finding-, Resume- und
  1.1A-Replayregressionen behalten ihre Fachsemantik. Insbesondere bleibt die
  positive Reviewanforderung aus Finding oder dreiteiliger Evidence,
  Pre-Mortem, kompletter PASS-Attestierung und fehlendem reviewer-eigenem
  offenen Blocker bestehen.

#### Geplante fokussierte Tests

- `tests/test_review_packets.py`: kanonische Bytes/Digests, stabile Sortierung,
  Slice- und Korrekturmanifest, selektive Finding-/Closureprojektion,
  Ausschlusslisten und fehlerhafte Fingerprint-/Planbindungen.
- `tests/test_workflow.py`: gleicher Korrekturdeltastart für beide Reviewer,
  genau ein Fachaufruf pro Rolle/Fingerprint, Resume-Wiederverwendung und
  unveränderter Plan-/Finalreviewzweig.
- `tests/test_prompts.py`: identischer Basisblock/-digest bei Claude und
  Antigravity, kleine explizite Rollenhüllen, Claude-Vorfreigabe nur in der
  Antigravity-Hülle und keine Rückeinbettung von Assignment-/Auditprosa.
- `tests/test_orchestrator_runtime.py`: inhaltsadressierte
  Materialisierung/Wiederverwendung, Digestkollision fail-closed und
  synthetischer Slice-/Korrekturlauf mit Fake-Adaptern und Call-Countern.
- `tests/test_agent_runtime.py`: exakt allowlisteter Reviewer-Snapshot,
  Ablehnung von Symlink/Pfadflucht und fehlendem Pfad sowie unveränderter
  vollständiger Finalreview-Snapshot.
- `tests/test_structured_artifact_regressions.py`: Record-/Mirror-Autorität,
  Claude-vor-Antigravity, Pending-Reviewer-Replay, Correction-Resume und
  1.1A-Replay bleiben ohne zweite Entscheidung oder Protokollfallback grün.

#### Risiko und Rückfalloption

Das größte Risiko ist ein zu knappes Paket, das eine für einen Failure Path
notwendige Information ausblendet. Der vertikale Durchstich ist deshalb auf
Slice- und Korrekturreviews begrenzt und enthält explizit Diff/Delta,
Akzeptanzkriterien, Attestierung und aktive Findings; unbekannte
Abhängigkeiten werden nicht heuristisch erraten. Fehlt eine dieser gebundenen
Eingaben, stoppt die Materialisierung. Der Rückfall schaltet für diese beiden
Zwecke auf die bisherige Evidenzkomposition zurück; Recordformate, State und
der unveränderte Finalreviewpfad benötigen keine Migration.

## Reihenfolge und Abhängigkeitsgraph

`Slice 1 (Providertext → lokale kanonische Vertragsentscheidung)` →
`Slice 2 (gebundene Revieweingaben → kanonisches Basispaket → kleine
Reviewerhülle)`.

Slice 2 beginnt erst nach Commit sowie Claude- und Antigravity-Freigabe von
Slice 1. Slice 2 baut auf der in Slice 1 stabilisierten Ein-Aufruf-Grenze auf;
er darf Normalisierungslogik nicht erneut implementieren. Jeder Slice ergibt
für sich einen rückwärtskompatiblen, fokussiert testbaren Zustand.

## Abdeckungsmatrix der Zielzustände 1 bis 10

| Zielzustand | Abdeckung |
|---|---|
| 1. Kein Zweitaufruf für lokal eindeutiges Format/Pflichtfeld | Slice 1, Stufenpipeline und Fake-Counter |
| 2. Vier eindeutige Evidence-Trenner, mehrdeutige Labels fail-closed | Slice 1, feste Label-/Trennermatrix |
| 3. Deterministische Reviewer-/Slice-/Testmetadaten | Slice 1, exakt ein `StepContract`-Wert |
| 4. Keine Überschreibung oder erfundene Fachsemantik | Slice 1, Gegenwert- und Byteerhaltungstests |
| 5. Abschlussmarker nur bei sicherem Providerabschluss | Slice 1, Terminal-/Trunkierungsmatrix |
| 6. Schlanker Slice-Basiskontext | Slice 2, kanonisches Slicepaket |
| 7. Reines Korrekturdelta und kompakte Closures | Slice 2, gemeinsamer Deltastart beider Reviewer |
| 8. Keine Audit-/Historien-/Fremddateieinbettung | Slice 2, Paket- und Workspace-Ausschlusstests |
| 9. Byteidentisches gemeinsames Basispaket | Slice 2, kanonische Bytes, Digest und Rollenhülle |
| 10. Lokale Diagnostik ohne Turn; Reparaturkosten separat | Slice 1, Normalisierungsbericht und Reparaturoperation |

## Migration und Rollback

- Es gibt keine Schema-, Record-, State- oder Protokollmigration. Bestehende
  `structured-v1`-Läufe bleiben an ihre Recordkette gebunden;
  `legacy-state-v3` wird nicht berührt.
- Die Evidence-Decodierung akzeptiert die bisherige kanonische Pipeform. Neue
  Escapezeichen wirken nur innerhalb der drei Evidence-Felder und werden vor
  Erzeugung des fachlichen `ReviewEvidence` reversibel entfernt.
- Inhaltsadressierte Reviewpakete sind rekonstruierbare Caches. Entfernen der
  neuen Materialisierung löscht keine Entscheidung; Resume baut das Paket aus
  den autoritativen Bindungen neu.
- Ein Rollback erfolgt sliceweise durch Entfernen der jeweiligen
  Integrationsaufrufe. Er darf nie Reviewrecords, State oder Audittext manuell
  umschreiben.

## Test- und Validierungsplan

- Alle Tests benutzen gespeicherte Antworttexte, synthetische Pakete,
  Fake-Adapter, Fake-Metadaten und Aufrufzähler. Echte Providerprozesse,
  Zugangsdaten und Netzwerkzugriffe sind ausgeschlossen.
- Während der Umsetzung laufen nur die in den Slice-Abschnitten genannten
  fokussierten Tests und `git diff --check`.
- Nach jedem Slice führt ausschließlich der Orchestrator die vollständige
  Repositorymatrix `python3 -m pytest tests/ -v` aus und bindet deren
  Attestierung an den kanonischen Fingerprint.
- Zusätzlich bleiben bestehende Plan-, Slice-, Korrektur-, Finalreview-,
  Finding-, Resume-, Providerinput-, Audit- und 1.1A-Replaytests unverändert
  Teil der vollständigen Matrix.
- Ein roter oder unvollständiger Testlauf kann nicht durch lokale
  Normalisierung, Paketwiederverwendung oder Reviewerprosa übergangen werden.

## Nichtziele

- keine Kompaktierung oder Abschwächung des branchweiten Finalreviews;
- keine Änderung von Quota-Warteintervall oder maximaler Quota-Wartezeit aus
  P2-FU-022;
- kein Antigravity-Warm-up, Remote-Shell-Retry oder allgemeiner
  Providerzuverlässigkeitsumbau aus P2-FU-013;
- keine allgemeine Provider-Usage-Telemetrie über die Reparaturzusatzmessung
  hinaus;
- keine Codex-Defektkandidaten und keine Änderung des Finalreviewrollenvertrags
  aus P2-FU-020;
- keine Watch-/Direkt-Erfolgsfinalisierung, Benutzer-Gates,
  record-first-Neuordnung aller Liveübergänge oder nativen Agenten-JSON-I/O;
- keine neuen Providerfunktionen, keine Ablösung der Textmarkerparser und kein
  allgemeines Snapshot-, Provider- oder Loggingrefactoring.

## Stopbedingungen

- Ein zu ergänzendes Feld hat nicht genau einen Wert im gebundenen
  `StepContract`, oder ein vorhandener Wert widerspricht ihm.
- Die lokale Verarbeitung müsste Findingsemantik, Verdict, Approval,
  Rationale, Stopentscheidung oder Providererfolg erfinden.
- Terminaler Providerabschluss beziehungsweise Nichttrunkierung ist an der
  Runtimegrenze nicht beweisbar.
- Diff/Delta, Akzeptanzkriterien, Attestierung oder betroffene Findings lassen
  sich nicht verlustfrei und fingerprintgleich in das Minimalpaket aufnehmen.
- Claude und Antigravity können nicht dieselben Basisbytes verwenden, ohne die
  obligatorische asymmetrische Reihenfolge zu lockern.
- Der vertikale Durchstich benötigt mehr als zwei Slices oder mehr als sieben
  produktive Dateien je Slice; dann wird innerhalb von Slice 2 weiter reduziert
  statt der Scope erweitert.
- Eine Fail-closed-, Fingerprint-, Attestierungs-, Findingeigentums-, Record-
  oder Resumegarantie müsste abgeschwächt werden.

## Offene Fragen

Keine produktentscheidende Frage ist offen. Nicht nachweisbar eindeutige
Formatvarianten und nicht manifestierte Repositoryabhängigkeiten fallen bewusst
unter die Stopbedingungen, nicht unter heuristische Erweiterung.

## Orchestrator-Prüfprotokoll

### Review-Feedback von Claude

<!-- audit:claude-review:begin -->
### Ereignis 2: Runde 1

- Reviewer: `claude`
- Freigabe: `YES`
- Validierungsbindung: `plan-validation-4fc516711200`
- Testdateien: keine
- Eigene Findings: `C-01`

<!-- artifact-records:claude-review:begin -->
Semantischer Record-Digest: `4989592d4470c6d3966c9d417043ddb96a951da828f2d8472cfe24656777e0d6`

- 8. `ar1-a4fb73ccf998f4fcbd3b8f8dbd9c9aefd67e7efd557f83cd8e7f1429466dc270`: `approved`; Work-Unit `1`; Findings `C-01`; Fingerprint `4fc51671120063da068942f46a41e5e92c032a752dcc51a86bfab2852126348e`
<!-- artifact-records:claude-review:end -->
<!-- audit:claude-review:end -->

### Review-Feedback von Antigravity

<!-- audit:antigravity-review:begin -->
### Ereignis 3: Runde 1

- Reviewer: `antigravity`
- Freigabe: `YES`
- Validierungsbindung: `plan-validation-4fc516711200`
- Testdateien: keine
- Prüfdimensionen: plan completeness, scope discipline, slice decomposition, test planning, fail-closed error handling
- Größtes Restrisiko: Local normalization heuristics for em-dash/dash separators could misinterpret complex punctuation in finding prose if separator boundaries are not strictly anchored to exact label tokens
- Realistische Bruchbedingung: A reviewer output with multi-line finding text containing dash-separated prose or unescaped pipe characters causes local normalization to corrupt the evidence payload or bypass the fail-closed halt
- Eigene Findings: keine

<!-- artifact-records:antigravity-review:begin -->
Semantischer Record-Digest: `4989592d4470c6d3966c9d417043ddb96a951da828f2d8472cfe24656777e0d6`

- 11. `ar1-d162cb4d6ae1db108e4c1a3926e7b6db08b2cffcaa422eacfedc11cb15215452`: `approved`; Work-Unit `1`; Findings `C-01`; Fingerprint `4fc51671120063da068942f46a41e5e92c032a752dcc51a86bfab2852126348e`
<!-- artifact-records:antigravity-review:end -->
<!-- audit:antigravity-review:end -->

### Review-Antworten von Codex

<!-- audit:codex-responses:begin -->
Noch keine strukturierten Codex-Antworten.

<!-- artifact-records:codex-responses:begin -->
Semantischer Record-Digest: `4989592d4470c6d3966c9d417043ddb96a951da828f2d8472cfe24656777e0d6`

Keine Codex-Findingantworten.
<!-- artifact-records:codex-responses:end -->
<!-- audit:codex-responses:end -->

### Validierungsattestierung

<!-- audit:validation-attestation:begin -->
### Ereignis 1: `plan-validation-4fc516711200`

- Diff-Fingerprint: `4fc51671120063da068942f46a41e5e92c032a752dcc51a86bfab2852126348e`
- Status: `PASS`
- Vollständig: `YES`
- Kurzresultat: internal plan contract passed
- Ausgabedigest: `e0788a3d34cc029349740bfc44764f3922903663241f5e518f099b2512e13247`

| Matrixbefehl | Status | Exitcode | Kompaktausgabe |
|---|---|---:|---|
| internal:work-plan-contract | PASS | 0 | slices=1; planned_paths=1; changed_paths=1; future_slices=2; work_plan=docs/internal/release-1-1b-deterministische-reviewvertraege-und-schlanke-reviewpakete-arbeitsplan.md |

<!-- artifact-records:validation-attestation:begin -->
Semantischer Record-Digest: `4989592d4470c6d3966c9d417043ddb96a951da828f2d8472cfe24656777e0d6`

- 2. `ar1-9d138055d082e3f0a7ade9a62fe451f82f89360344c82bbce86ca8b2882bda18`: Providerinput `codex/codex_plan` = `allowed`; Zeichen `22002/4000000`, Bytes `22102/16000000`; Input `f117ab433f9eb5edeff79bba1c363fcb7617e64d9e25d0b3514e45cdf0353fa5`, Policy `8cff2b572a65674a0b31c2e2751cdc76fa839112b05e38b09bd021a02284bc83`, Übergang `244c00da43bd4d6a3220cc8040c360b34126e6c104a3dbd002a68fe5c132af9c`; technisches Limit `None/None` (Quelle `unknown`); Verletzung `none`, Überhang `0/0`, größte Komponente `stdin_prompt`; Komponenten `stdin_prompt=22002/22102`
- 4. `ar1-1594e0214822e7dfc0ee42a84e4b30eb7b75fdee8d181da1bfedac17bcd06609`: Attestierung durch `orchestrator`; Fingerprint `4fc51671120063da068942f46a41e5e92c032a752dcc51a86bfab2852126348e`
  - `pass` / Exit `0` / Output `e0788a3d34cc029349740bfc44764f3922903663241f5e518f099b2512e13247`: `argv` [`internal:work-plan-contract`]
- 5. `ar1-693578d152556fa3fd1ae4919d00762d18f8cbdb558d08009ac28b1a2f9ad5cc`: Providerinput `claude/claude_plan_review` = `allowed`; Zeichen `62424/4000000`, Bytes `62824/16000000`; Input `738d52ed0c3e0336b1facbe83e068d4a5784ca9de901058cffbd1970192c3c07`, Policy `8cff2b572a65674a0b31c2e2751cdc76fa839112b05e38b09bd021a02284bc83`, Übergang `6b0b744493a124a7b9bc4843073d9d853bcdb7308a7f2c8e645905e86322478c`; technisches Limit `None/None` (Quelle `unknown`); Verletzung `none`, Überhang `0/0`, größte Komponente `packet_chunk_002`; Komponenten `packet_chunk_001=23831/23967, packet_chunk_002=23978/24193, packet_chunk_003=12904/12953, packet_manifest=563/563, system_policy=660/660, response_schema=146/146, start_directive=342/342`
- 7. `ar1-7586aa68b537a924770f33b3e5cbb48e63989d6520985b758d3fd0715b597be1`: Providerinput `claude/claude_plan_review` = `allowed`; Zeichen `9615/4000000`, Bytes `9627/16000000`; Input `0eb923fd5a1c165e1382699fa5f461a83227e6a5093e06d7273a82904f04f725`, Policy `8cff2b572a65674a0b31c2e2751cdc76fa839112b05e38b09bd021a02284bc83`, Übergang `0473ff81e855176c1b88b632b4f20535a8561a7ca16f9a45dfed9ed3b4cf738e`; technisches Limit `None/None` (Quelle `unknown`); Verletzung `none`, Überhang `0/0`, größte Komponente `packet_chunk_001`; Komponenten `packet_chunk_001=8197/8209, packet_manifest=270/270, system_policy=660/660, response_schema=146/146, start_directive=342/342`
- 10. `ar1-5902d309c1e4aaa1e6ff019178260b1e4975c5632b4f6d0a9dc06d37af7e4d99`: Providerinput `antigravity/antigravity_plan_review` = `allowed`; Zeichen `67168/4000000`, Bytes `67571/16000000`; Input `ad708f69be35208c11f13802999e3b2690324a1e72686b4a10ab1ea6b5a622f8`, Policy `8cff2b572a65674a0b31c2e2751cdc76fa839112b05e38b09bd021a02284bc83`, Übergang `5e598306036908411ba83dff65c5385670784f065348d57d8828ea0e5e27b446`; technisches Limit `None/None` (Quelle `unknown`); Verletzung `none`, Überhang `0/0`, größte Komponente `prompt_file`; Komponenten `prompt_file=66509/66912, response_schema=146/146, start_directive=513/513`
<!-- artifact-records:validation-attestation:end -->
<!-- audit:validation-attestation:end -->

### Testfreigabe und Pre-Mortem

<!-- audit:test-approval-premortem:begin -->
- Teständerungsfreigabe: nicht erfasst.
- Pre-Mortems:
  - Ereignis 2: Most likely 3-month failure: the "eindeutige Trennergrenze" for REVIEW_EVIDENCE ends up implemented with an escape scheme that isn't fully collision-safe against literal backslash/pipe sequences a model emits inside free-form finding prose, causing either a silent misparse of finding text (safety regression) or spurious fail-closed halts on legitimate reviews; the plan's Slice 1 tests should be checked for explicit collision-adversarial fixtures, not just the four canonical separator variants, before Slice 1 is approved as done.
  - Ereignis 3: An edge case in review packet construction during Slice 2 omits an indirect test dependency or diff hunk required for adversarial evaluation of a cross-cutting failure path, causing a reviewer to approve an incomplete slice

<!-- artifact-records:test-approval-premortem:begin -->
Semantischer Record-Digest: `4989592d4470c6d3966c9d417043ddb96a951da828f2d8472cfe24656777e0d6`

Keine strukturierten Gates.
<!-- artifact-records:test-approval-premortem:end -->
<!-- audit:test-approval-premortem:end -->

### Findings-Lebenszyklus

<!-- audit:findings:begin -->
### `C-01` — `OPEN`

- Quelle: `claude`; Runde 1
- Klasse: `OBSERVATION`
- Finding: The plan's exact change paths for Slice 1 and Slice 2 do not include AGENTS.md/CLAUDE.md/CODEX.md/ANTIGRAVITY.md even though the repo-agent contract requires keeping them synchronized after orchestration/prompt/parser/state changes (workflow.py, orchestrator.py, prompts.py are all in scope); the plan never states why doc sync is inapplicable.
- Akzeptanztest: During Slice 2 implementation review, confirm no reviewer-facing marker/contract text in AGENTS.md, CLAUDE.md, CODEX.md, or ANTIGRAVITY.md changed meaning (only local input tolerance and packet composition changed); if any of these docs describe REVIEW_EVIDENCE format, packet contents, or repair-call behavior, update them in the same Slice.
- Statusbegründung: –

<!-- artifact-records:findings:begin -->
Semantischer Record-Digest: `4989592d4470c6d3966c9d417043ddb96a951da828f2d8472cfe24656777e0d6`

- 9. `ar1-6506d519ecc46de435b378fb2eb68306fd4ef66076e19c5091055f4d33f92d16`: `C-01` `opened` durch `claude`; `OBSERVATION` / `open` — The plan's exact change paths for Slice 1 and Slice 2 do not include AGENTS.md/CLAUDE.md/CODEX.md/ANTIGRAVITY.md even though the repo-agent contract requires keeping them synchronized after orchestration/prompt/parser/state changes (workflow.py, orchestrator.py, prompts.py are all in scope); the plan never states why doc sync is inapplicable.
<!-- artifact-records:findings:end -->
<!-- audit:findings:end -->

### Entscheidungstabelle

<!-- audit:decision-table:begin -->
| ID | Quelle | Finding | Klasse | Entscheidung | Umsetzung |
|---|---|---|---|---|---|
| C-01 | claude | The plan's exact change paths for Slice 1 and Slice 2 do not include AGENTS.md/CLAUDE.md/CODEX.md/ANTIGRAVITY.md even though the repo-agent contract requires keeping them synchronized after orchestration/prompt/parser/state changes (workflow.py, orchestrator.py, prompts.py are all in scope); the plan never states why doc sync is inapplicable. | OBSERVATION | offen | offen |

<!-- artifact-records:decision-table:begin -->
Semantischer Record-Digest: `4989592d4470c6d3966c9d417043ddb96a951da828f2d8472cfe24656777e0d6`

| Seq | Record | Typ | Status | Logische ID | Revision | Fingerprint |
|---:|---|---|---|---|---:|---|
| 1 | `ar1-55562810422f00c1b8875c883dfd1eb0891539f61c7d3f09da16776f351423b9` | `task` | `accepted` | `task-contract` | 1 | `contract:6d5a2c5b058329ec7d97dd2d97047472622c07145106dfe326839b0bfdaa4979` |
| 2 | `ar1-9d138055d082e3f0a7ade9a62fe451f82f89360344c82bbce86ca8b2882bda18` | `provider_input_measurement` | `measured` | `provider-input-1-codex_plan` | 1 | `implementation:6d5a2c5b058329ec7d97dd2d97047472622c07145106dfe326839b0bfdaa4979` |
| 3 | `ar1-3835a5c538f4662824b66c9495da0ec9aa683feba35f6cec009fa905d845ffc6` | `agent_result` | `ready` | `agent-1-codex_plan-1` | 1 | `contract:6d5a2c5b058329ec7d97dd2d97047472622c07145106dfe326839b0bfdaa4979` |
| 4 | `ar1-1594e0214822e7dfc0ee42a84e4b30eb7b75fdee8d181da1bfedac17bcd06609` | `validation_attestation` | `attested` | `plan-validation-4fc516711200` | 1 | `implementation:4fc51671120063da068942f46a41e5e92c032a752dcc51a86bfab2852126348e` |
| 5 | `ar1-693578d152556fa3fd1ae4919d00762d18f8cbdb558d08009ac28b1a2f9ad5cc` | `provider_input_measurement` | `measured` | `provider-input-1-claude_plan_review` | 1 | `implementation:6d5a2c5b058329ec7d97dd2d97047472622c07145106dfe326839b0bfdaa4979` |
| 6 | `ar1-f811df4b073cbd63fadf6ea10936ecae628fed91e90340776a082eca8dd765bd` | `diagnostic` | `failed` | `diagnostic-claude-1-1` | 1 | `implementation:6d5a2c5b058329ec7d97dd2d97047472622c07145106dfe326839b0bfdaa4979` |
| 7 | `ar1-7586aa68b537a924770f33b3e5cbb48e63989d6520985b758d3fd0715b597be1` | `provider_input_measurement` | `measured` | `provider-input-1-claude_plan_review` | 2 | `implementation:6d5a2c5b058329ec7d97dd2d97047472622c07145106dfe326839b0bfdaa4979` |
| 8 | `ar1-a4fb73ccf998f4fcbd3b8f8dbd9c9aefd67e7efd557f83cd8e7f1429466dc270` | `review` | `decided` | `review-claude-1-1` | 1 | `implementation:4fc51671120063da068942f46a41e5e92c032a752dcc51a86bfab2852126348e` |
| 9 | `ar1-6506d519ecc46de435b378fb2eb68306fd4ef66076e19c5091055f4d33f92d16` | `finding_transition` | `recorded` | `finding-C-01` | 1 | `implementation:4fc51671120063da068942f46a41e5e92c032a752dcc51a86bfab2852126348e` |
| 10 | `ar1-5902d309c1e4aaa1e6ff019178260b1e4975c5632b4f6d0a9dc06d37af7e4d99` | `provider_input_measurement` | `measured` | `provider-input-1-antigravity_plan_review` | 1 | `implementation:6d5a2c5b058329ec7d97dd2d97047472622c07145106dfe326839b0bfdaa4979` |
| 11 | `ar1-d162cb4d6ae1db108e4c1a3926e7b6db08b2cffcaa422eacfedc11cb15215452` | `review` | `decided` | `review-antigravity-1-1` | 1 | `implementation:4fc51671120063da068942f46a41e5e92c032a752dcc51a86bfab2852126348e` |
<!-- artifact-records:decision-table:end -->
<!-- audit:decision-table:end -->

### Freigabestatus

<!-- audit:approval-status:begin -->
- Implementierung bereit: `NOT_RECORDED`
- Validierung: `PASS`
- Claude-Freigabe: `YES`
- Antigravity-Freigabe: `YES`
- Red-State-Folgeslice: `NONE`
- Commit autorisiert: `NO`

<!-- artifact-records:approval-status:begin -->
Semantischer Record-Digest: `4989592d4470c6d3966c9d417043ddb96a951da828f2d8472cfe24656777e0d6`

Keine Work-Unit- oder Binding-Records.
<!-- artifact-records:approval-status:end -->
<!-- audit:approval-status:end -->
