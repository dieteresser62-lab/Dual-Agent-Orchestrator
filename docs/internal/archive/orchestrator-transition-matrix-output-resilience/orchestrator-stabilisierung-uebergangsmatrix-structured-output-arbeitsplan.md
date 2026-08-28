# Arbeitsplan: Übergangsmatrix und Structured-Output-Robustheit

## 1. Status, Zielbranch und Ausführungsmodus

Dieser Arbeitsplan ist für eine **manuelle Entwicklung außerhalb des Orchestrators** bestimmt.

Er ist ausdrücklich **kein `PLAN_ONLY`-Artefakt**, wird nicht als `WORK_PLAN_PATH`
an `plan_handoff.extract_implementation_slices` übergeben und soll nicht von
`run_task` verarbeitet werden. Die Slice-Überschriften strukturieren ausschließlich
die manuelle Zusammenarbeit zwischen Codex und Claude; die bewusst nur
voraussichtlichen Pfadlisten sind keine maschinellen Handoff-Allowlisten.

- `TARGET_BRANCH`: `feature/orchestrator-transition-matrix-output-resilience`
- Ausgangsbasis: der bei Arbeitsbeginn aktuelle, saubere `master`
- geprüfte Basis bei Planerstellung: `de1bd9e` (`merge: complete decision table prose structuring`)
- während Planung, Implementierung und Review wird kein `run_task`, kein Watcher und kein Bootstrap-Lauf gestartet
- `.orchestrator/state.json`, Checkpoints und bestehende Recordketten werden nicht manuell verändert
- Codex implementiert Slice für Slice; Claude reviewt jeden Slice sowie den Gesamtstand manuell mit Sonnet und Denktiefe `high`
- erst nach vollständiger Freigabe darf ein separater realer Bootstrap-Smoke-Test vorbereitet werden

Der Zielbranch wird erst unmittelbar vor der Implementierung vom dann aktuellen `master` angelegt. Weicht dessen Stand von der oben dokumentierten Basis ab, werden die Änderungen vor dem ersten Slice auf Auswirkungen auf diesen Plan geprüft.

## 2. Herkunft und eingearbeitete Reviewbefunde

Der Plan konsolidiert den Entwurf
`/tmp/orchestrator-stabilisierung-uebergangsmatrix-structured-output-arbeitsplan.md`, das darauf erfolgte manuelle Claude-Planreview, den Codex-Nachtrag und den anschließenden produktiven Happy-Path-Nachweis.

Folgende Reviewbefunde sind verbindlich eingearbeitet:

1. Die zuvor ausstehenden Pakete zur Resume-Queuefinalisierung und zur Prosastrukturierung der Entscheidungstabelle sind inzwischen in `master` integriert. Ihre Funktionen sind Regressionsbasis dieses Pakets und werden nicht neu implementiert.
2. Verworfene Claude-Modellinhalte werden nicht nachträglich persistiert. Reale Structured-Output-Erschöpfung darf deshalb nicht ohne Beweis einem Längen- oder Schemaverstoß zugeschrieben werden.
3. Die Übergangsmatrix wird zunächst test-only und ohne Produktionskorrektur aufgebaut. Erst ein dokumentierter Analysecheckpoint darf eine begrenzte gemeinsame Produktionskorrektur freigeben.
4. Ein Slice 2 ohne Schemaänderung ist ein gültiger Erfolg, wenn die Evidenz keine sichere Vertragsänderung rechtfertigt.
5. Der einfache Produktivpfad `Plan → Slice Runde 1 → Final Review Runde 1 → Queueabschluss` ist bereits real und ohne manuellen Eingriff durchgelaufen. Er wird als Regression erhalten; der Schwerpunkt liegt auf den bislang unzureichend belegten Korrektur-, Mehr-Runden- und Resume-Grenzen.

## 3. Ausgangslage

### 3.1 Was die native JSON-Kommunikation bereits verbessert hat

Die native strukturierte Kommunikation beseitigt die frühere Interpretation freier Agententexte als technische Autorität:

- Agentenergebnisse werden request-, schema-, fingerprint- und kontextgebunden validiert.
- Produktive Textparser, Reparaturturns und Normalisierungsversuche sind aus dem nativen Ergebnisweg entfernt.
- Unvollständige oder ungebundene Resultate erzeugen weder Findings noch Freigaben.
- Maschinenlesbare Providerdiagnosen erlauben eine eng begrenzte Retryklassifikation.
- Die append-only Recordkette ist technische Autorität; State-v3 ist ein überprüfter operativer Mirror und Markdown eine reine Auditsicht.

### 3.2 Wo die verbleibenden Stabilitätsrisiken liegen

Die jüngsten Laufzeitdefekte konzentrierten sich nicht auf das JSON-Parsing, sondern auf Übergänge zwischen Work-Units, Runden, Record-Replay und State-Mirror:

- Slice → Final Review,
- Final Review → Korrektur,
- Korrekturrunde 1 → Korrekturrunde 2 oder höher,
- Erweiterung einer späteren Korrekturrunde um einen neuen zulässigen Blocker,
- Korrektur → erneutes Final Review,
- Resume vor oder nach Record-/Mirror-Schreibpunkten,
- HEAD-Drift zwischen Review, Resume und Commit,
- Queueabschluss nach einem erfolgreichen direkten Resume.

Die vorhandene Suite enthält zahlreiche Einzelregressionen, aber noch kein geschlossenes Übergangsmodell. Dadurch konnten lokal plausible Fixes jeweils an der nächsten Systemgrenze eine neue Divergenz sichtbar machen.

### 3.3 Structured-Output-Druck

Claudes `error_max_structured_output_retries` ist ein realer zusätzlicher Fehlermodus des strukturierten Transports. Die exakte Fehlerhülle wird inzwischen transient klassifiziert und begrenzt automatisch wiederholt. Die technische Diagnose beweist jedoch nicht, ob der konkrete Auslöser eine Freitextgrenze, Schemakomplexität, Promptgröße oder ein internes Providerbudget war.

Erfolgreiche Resultate und synthetische Grenzfälle lassen sich lokal untersuchen. Verworfene Claude-Ausgaben werden bewusst nicht vollständig persistiert und dürfen weder nachträglich zur Autorität erklärt noch aus Diagnosehüllen rekonstruiert werden.

### 3.4 Bereits belegter Produktionspfad

Der Lauf `watch-20260827-090314.548190Z-e73cf1146265` hat den einfachen Happy Path vollständig durchlaufen:

1. Inbox-Idee übernommen,
2. Plan erzeugt und freigegeben,
3. Implementierungshandoff erzeugt,
4. Slice implementiert und im ersten Review freigegeben,
5. Slice committed,
6. Final Review freigegeben,
7. Auditabschluss erzeugt,
8. Idee und Handoff genau einmal nach `outbox/done` verschoben.

Dieser Nachweis belegt die grundsätzliche Zusammenarbeit von JSON-Transport, Recordkette, Reviewbindung, Commit und Queueabschluss. Er belegt noch nicht die risikoreichen Korrektur-, Mehr-Runden-, Fehler- und Resume-Pfade.

## 4. Zielbild

Nach Abschluss des Pakets sind drei Qualitätsdimensionen getrennt nachgewiesen:

1. **Semantische Sicherheit:** Kein ungültiger Übergang, kein fremdes Finding und kein ungebundener Agentenoutput wird akzeptiert.
2. **Technische Verfügbarkeit:** Erwartbare transiente Providerfehler und zulässige Resume-Situationen erzeugen keinen unnötigen manuellen Stopp.
3. **Autonomie:** Repräsentative Plan-, Implementierungs-, Review-, Korrektur-, Finalisierungs- und Queueabläufe terminieren providerfrei und deterministisch im erwarteten Zustand.

Das Paket dient nicht dazu, Stopps um jeden Preis zu vermeiden. Sicherheitsrelevante Inkonsistenzen müssen weiterhin laut und fail-closed stoppen. Ziel ist, berechtigte Stopps von Migrations- und Übergangsfehlern klar zu trennen.

## 5. Verbindliche Invarianten

### 5.1 Record, Replay und Mirror

- Der Finding-Ledger wird aus der vollständigen relevanten Recordlinie abgeleitet.
- Eine Work-Unit-Grenze verliert keine Findinglinie und übernimmt keine fremde historische Findinglinie.
- Korrekturrunden dürfen die autorisierte Menge monoton um neue zulässige Blocker erweitern.
- Geschlossene Findings bleiben im Ledger erhalten, auch wenn die nächste Agentenanfrage nur offene Findings anbietet.
- Recordprojektion und State-v3-Mirror folgen derselben expliziten Umfangsregel; Abweichungen stoppen fail-closed.
- Record-Ahead-Replay wendet keine Transition doppelt an und fordert keine bereits persistierte Providerentscheidung erneut an.
- Fehlende, korrupte oder referenziell unvollständige Recordlinien dürfen nicht durch den Mirror geheilt oder still verworfen werden.

### 5.2 Review und Findings

- Nur reviewer-eigene offene Findings dürfen geschlossen oder reklassifiziert werden.
- Eine Korrekturantwort disponiert exakt die gebundene offene Findingmenge.
- Korrektur- und Finalrunden erzeugen keine neue Observation.
- Ein neu entdeckter ausführbarer Defekt darf in einer zulässigen Runde als Blocker eröffnet werden.
- Positive Reviews benötigen vollständige Dispositionen, konkrete Evidenz, Pre-Mortem, Attestierung und einen zulässigen resultierenden Findingzustand.

### 5.3 Git, Gate, Resume und Queue

Folgende bereits implementierten Fähigkeiten werden regressiv abgesichert:

- HEAD-Drift wird nicht implizit akzeptiert.
- Zulässiger Drift erhält ein persistiertes fingerprint- und pfadgebundenes Nutzergate.
- Dieselbe exakte Freigabe ist idempotent; abweichender Fingerprint, HEAD oder Pfadsatz benötigt eine neue Entscheidung.
- Ein erfolgreicher direkter Resume und ein erfolgreicher Watch-Lauf finalisieren ihre Queueartefakte genau einmal.
- Resume wiederholt weder Commit noch Provideraufruf noch Queuebewegung, wenn die jeweilige Nebenwirkung bereits autoritativ belegt ist.

### 5.4 Structured Output

- Provider-Writerschema und lokale Domäne bleiben für schema-ausdrückbare Regeln äquivalent.
- `additionalProperties: false`, Requestbindung, Finding-ID-Bindung und vollständige Dispositionen werden nicht gelockert.
- Keine Freitextgrenze wird ohne Corpusbefund, ausdrückliche Domänenentscheidung und Negativtest verändert.
- Der bekannte Claude-Diagnoseenvelope wird nur exakt und begrenzt automatisch wiederholt; Near-Miss-Envelopes bleiben fail-closed.
- Physische Retries derselben logischen Operation behalten Inputdigest, Requestbindung und Policybindung.
- Verworfene Modellantworten werden nicht neu persistiert. Zulässig sind nur bereits providerseitig gelieferte, eng allowlistbare und nichtinhaltliche Diagnosemetadaten.

## 6. Messgrößen

Die Tests und Abschlussberichte leiten mindestens folgende deterministische Kennzahlen ab:

- abgedeckte produktive Übergangskanten / inventarisierte produktive Übergangskanten,
- normale, Resume- und fail-closed-Fälle je verpflichtender Kante,
- erwartete und unerwartete Gates je Szenario,
- erneute Provideraufrufe und Nebenwirkungen nach Resume,
- unerwartete lokale Ablehnungen hinter writer-validen Dokumenten,
- still akzeptierte fachlich unzulässige Dokumente,
- Feldlängen und relative Grenzauslastung erfolgreicher bzw. synthetischer Fixtures,
- Größe und Kompositionstiefe der tatsächlich serialisierten Writerschemas,
- exakt klassifizierte Structured-Output-Fehler und ausgeschöpfte gebundene Retries,
- autonome terminale Szenarien / verpflichtende Szenarien.

Zeit, Kosten und reale Providervarianz werden berichtet, aber nicht als deterministische Bestehensschwellen verwendet.

## 7. Slice-Zuschnitt

Es gibt höchstens drei Slices und keine feste Dateianzahlgrenze. Der fachlich geschlossene Zuschnitt ist maßgeblich. Zusätzliche Pfade müssen aus einer Invariante dieses Plans begründet werden; der Slice darf nicht allein zur Einhaltung einer Dateigrenze künstlich geteilt werden.

### Slice 1 - Behauptende Übergangsmatrix und begrenzte gemeinsame Korrektur

#### Ziel

Ein zentrales, tabellengetriebenes Übergangsmodell macht alle produktiv erreichbaren Work-Unit-, Runden-, Finding-, Replay-, Gate- und Resume-Grenzen explizit. Messung und Produktionskorrektur bleiben innerhalb des Slices durch einen verbindlichen Analysecheckpoint getrennt.

#### Phase A - Test-only Inventur und Falsifikation

1. Alle produktiv erreichbaren Kanten zwischen Plan, Slice, Final Review und Korrektur inventarisieren.
2. Unzulässige oder konstruktiv unerreichbare Kombinationen ausdrücklich dokumentieren.
3. Ein kanonisches, von den Produktionsableitungen unabhängiges Oracle definieren,
   dessen Erwartungswerte als literale, reviewbare Tabellendaten im Matrixmodul
   stehen. Das Oracle darf seine Sollwerte insbesondere nicht durch Aufruf von
   `replay_findings`, `carry_forward_native_findings`,
   `authoritative_native_findings` oder einer anderen zu prüfenden
   Produktionsprojektion bilden. Pro Eingangszustand und Ereignis erwartet es
   mindestens:
   - vollständigen Finding-Ledger,
   - dem Provider angebotene Findingmenge,
   - Rundennummer und nächsten Workflow-Schritt,
   - Anzahl externer Aufrufe,
   - Record-, Mirror-, Gate- und Checkpointzustand.
4. Pro verpflichtender Kante Varianten für Runde 1 und Runde ≥2, leere und nichtleere Findingmengen, Schließen, Antwort, zulässige Reklassifikation und einen neuen zulässigen Blocker erfassen.
5. Unterbrechungen vor Record, nach Record/vor Mirror sowie nach Mirror/vor Checkpoint simulieren.
6. HEAD unverändert, exakt freigegebener Drift und abweichender Drift prüfen.
7. Die tatsächlichen Zustände über die produktiven Eintrittspunkte erzeugen,
   aber ausschließlich gegen die literalen Oraclewerte prüfen. Gemeinsame
   Helfer dürfen Eingaben aufbauen und Ausgaben normalisieren, jedoch keine
   erwarteten Ledger, Findingmengen, Schritte, Gates oder Aufrufzahlen aus
   Produktionsresultaten ableiten.
8. Zwei verpflichtende Mutationsproben vorsehen:
   - ein Monkeypatch lässt `replay_findings` für eine nichtleere Recordlinie
     fälschlich leer zurückgeben,
   - ein Monkeypatch lässt `carry_forward_native_findings` sein Eingabeargument
     unverändert durchreichen.
   Je Mutation muss mindestens ein Matrixfall jeder betroffenen Kantenklasse
   fehlschlagen. Bleibt die Matrix vollständig grün, gilt das Oracle als
   zirkulär und Slice 1 ist nicht abnahmefähig.
9. Das Kanteninventar an eine separat gepflegte, explizite Source-Map binden.
   Ein fachlicher Gatesachverhalt wird darin nicht allein durch den
   `GateReason`, sondern durch das Tripel aus
   **Gategrund, Regelidentität und Gateart** identifiziert. Die Gateart
   unterscheidet mindestens Nutzergate und Policygate. Die Regelidentität ist
   entweder:
   - das vollständige Großbuchstabenpräfix unmittelbar vor dem ersten
     kanonischen Separator ` | `, exakt ab Zeichenposition null geparst, oder
   - für ein bewusst präfixloses Gate eine stabile, explizite
     `PREFIXLESS:<familie>`-Kennung mit einer vollständigen, verankerten
     Detailgrammatik und einer gebundenen Emissionsstelle.
   Teilstringsuche, eine leere Regelidentität und eine pauschale
   `dynamic`-Kategorie sind unzulässig.
10. Ein struktureller Guard inventarisiert providerfrei **jede persistierte
    Gatetransition**, nicht nur die beiden Komforthelfer. Sein Suchraum umfasst:
    - alle produktiven Aufrufstellen von `await_user_gate` und
      `await_policy_gate`,
    - jede direkte Konstruktion oder Ersetzung eines `GateRecord`, insbesondere
      die Zustandsübergänge `reopen_legacy_quota_resume_diff_gate`,
      `record_review_denial`, `record_invocation_failure` und
      `await_bootstrap_resume`,
    - sämtliche präfixartig geformten Stringliterale nach
      `^[A-Z][A-Z0-9_-]* \| ` im gesamten produktiven Pythonquelltext,
      einschließlich erster konstanter Segmente von f-Strings und verketteter
      Literale,
    - alle in `gates.BUILTIN_STOP_RULES` und weiteren ausdrücklich benannten
      produktiven Regelregistries enthaltenen Kennungen.
11. Jedes gefundene präfixartige Literal wird in der Source-Map entweder als
    Erzeuger einer konkreten Gateidentität oder als nachweislich gatefremdes
    Literal mit Begründung klassifiziert. Dadurch bleibt auch ein Präfix wie
    `HEAD-DRIFT`, das upstream in einer Exception entsteht und downstream nur
    über `detail=exc.detail` weitergereicht wird, inventarisierbar.
12. Jede Gateemissionsstelle mit variablem oder durchgereichtem Detail bindet
    in der Source-Map eine **exakte erlaubte Menge** von upstream erzeugten
    Regelidentitäten oder eine eng definierte dynamische Regelregistry. Eine
    bloße Kennzeichnung der Aufrufstelle als dynamisch genügt nicht. Ein neues
    upstream-Präfix unter derselben Aufrufstelle muss deshalb den Guard rot
    machen. Variable Reviewer- oder Stop-Rule-IDs werden nur über eine
    benannte Registry oder eine vollständige validierte Kennungsgrammatik als
    eigene Familie zugelassen.
13. Präfixlose Gates werden nicht fallengelassen. Jede solche Emissionsstelle
    besitzt eine eigene `PREFIXLESS:<familie>`-Zeile, eine vollständige
    `fullmatch`-Detailgrammatik und genau eine Gateart-/Gategrundbindung. Für
    `record_review_denial` ist beispielsweise eine eigene
    `PREFIXLESS:REVIEW-DENIAL`-Familie erforderlich. Ein zusätzliches
    präfixloses Detail, das weder die Grammatik noch eine neue Familienzeile
    erfüllt, lässt den Guard fehlschlagen.
14. Die Source-Map ordnet jedes Gateidentitätstripel, jede aktuelle
    Work-Unit-Art und jeden benannten produktiven Übergangseinstieg mindestens
    einem Matrixfall oder einer begründeten Unerreichbarkeitszeile zu. Jede
    Source-Map-Zeile bindet außerdem ihre konkreten Emissions- und gegebenenfalls
    Producerstellen. Umgekehrt muss jede Zeile durch mindestens einen
    Matrixfall tatsächlich emittiert werden; verwaiste oder nur nominell
    vorhandene Zeilen sind Fehler. Die fachlichen Sollwerte bleiben literal
    und werden nicht aus der Inventur abgeleitet.
15. Drei verpflichtende Guard-Mutationsproben decken die drei Entdeckungswege
    getrennt ab:
    - **statisch:** `ZZZ-PROBE | ` wird direkt an einer bestehenden
      `await_user_gate`- oder `await_policy_gate`-Stelle unter einem bereits
      klassifizierten Gategrund ergänzt,
    - **dynamisch zugeführt:** `ZZZ-DYNAMIC | ` entsteht in einem upstream
      Producer und fließt über eine bereits klassifizierte variable Stelle wie
      `detail=exc.detail`,
    - **direkt konstruiert:** Ein zusätzlicher direkter `GateRecord`-Pfad
      emittiert `ZZZ-DIRECT | ` oder ein neues präfixloses Detail.
    Jede Mutation muss den Guard rot machen, bis Gateidentität, Producer,
    Emissionsstelle und Matrixzuordnung vollständig klassifiziert sind. Bleibt
    eine der drei Mutationen grün, ist Slice 1 nicht abnahmefähig.
16. Die Matrix zunächst ausschließlich in Tests und Testhilfen umsetzen.
    Produktivcode bleibt bis zum Analysecheckpoint unverändert.

#### Analysecheckpoint

- Jede Abweichung erhält ein reproduzierendes Szenario, die verletzte Invariante und eine vermutete gemeinsame Ursache.
- Die Findings werden nach gemeinsamer Umfangs-, Ledger-, Replay-, Gate- oder Resume-Regel gruppiert.
- Der erwartete Produktionspfadsatz für Phase B wird vor dem ersten Produktivcodeeingriff festgehalten.
- Widersprüchliche Ledgerdefinitionen, nur kantenbezogen lösbare Ausnahmen oder mehrere unabhängige Reparaturpakete lösen eine Stopbedingung und erneutes Planreview aus.
- Ist die Matrix bereits grün, entfällt Phase B; ein test-only Slice ist ausdrücklich zulässig.

#### Phase B - Begrenzte gemeinsame Korrektur

1. Nur am Analysecheckpoint benannte gemeinsame Ursachen korrigieren.
2. Keine Sonderbehandlung ausschließlich für eine einzelne Übergangskante einführen.
3. Jede Änderung gegen die vollständige Phase-A-Matrix und die bestehenden Einzelregressionen prüfen.
4. Unabhängige neue Defekte nicht still in den Slice aufnehmen; sie benötigen eine erneute Umfangsentscheidung.

#### Voraussichtliche Änderungspfade

- `tests/test_workflow_transition_matrix.py` (neu)
- bestehende Testhilfen und gezielt betroffene Tests unter `tests/`
- nur bei einem freigegebenen Phase-B-Befund die gemeinsam verantwortlichen Module, voraussichtlich aus:
  - `src/workflow.py`
  - `src/workflow_state.py`
  - `src/orchestrator.py`
  - `src/artifact_replay.py`
  - `src/artifact_migration.py`

Die Liste ist eine repository-basierte Erwartung, keine pauschale Änderungsfreigabe. Phase A bestimmt den tatsächlichen Phase-B-Pfadsatz.

#### Akzeptanzkriterien

- Jede produktiv erreichbare Folgekante ist genau einmal im Inventar klassifiziert.
- Die Source-Map deckt jedes inventarisierte Tripel aus Gategrund, exakt
  verankerter oder explizit präfixloser Regelidentität und Gateart sowie jede
  aktuelle Work-Unit-Art und jeden benannten produktiven Übergangseinstieg ab.
- Der Guard erfasst sowohl Helferaufrufe als auch jede direkte
  `GateRecord`-Konstruktion oder -Ersetzung. Die vier bekannten direkten
  Zustandsübergänge sind ausdrücklich Bestandteil des Inventars.
- Eine variable oder durchgereichte Detailstelle bindet die exakte Menge ihrer
  erlaubten upstream-Regelidentitäten; ein neues Präfix hinter einer bereits
  klassifizierten dynamischen Stelle bleibt nicht unentdeckt.
- Präfixlose Gates besitzen eine explizite Familienkennung und vollständige
  Detailgrammatik; kein Gateidentitätstripel hat eine undefinierte zweite
  Komponente.
- Jede Source-Map-Zeile ist bidirektional belegt: Ihre Producer- und
  Emissionsstellen existieren und mindestens ein Matrixfall emittiert exakt
  diese Gateidentität. Verwaiste Zeilen lassen den Guard fehlschlagen.
- Die drei Mutationen `ZZZ-PROBE`, `ZZZ-DYNAMIC` und `ZZZ-DIRECT` beziehungsweise
  ein neues präfixloses Direktdetail lassen den Vollständigkeitsguard jeweils
  fehlschlagen, bis alle zugehörigen Bindungen klassifiziert wurden.
- Jede verpflichtende Kante besitzt mindestens einen Normal-, Resume- und fail-closed-Negativfall.
- Ledger, angebotene Findingmenge, Runde, nächster Schritt, Aufrufanzahl sowie
  Record-, Mirror-, Gate- und Checkpointzustand stehen als literale Sollwerte
  im Matrixmodul und werden nicht aus den geprüften Produktionsfunktionen
  berechnet.
- Die tatsächliche Recordprojektion und der tatsächliche Mirror werden nach
  jedem Szenarioschritt jeweils gegen diese unabhängigen Sollwerte geprüft,
  nicht lediglich gegeneinander und nicht nur terminal.
- Die Mutationsproben gegen `replay_findings` und
  `carry_forward_native_findings` erzeugen die erwarteten roten Matrixfälle;
  keine der beiden Mutationen darf die vollständige Matrix grün lassen.
- Korrekturrunde ≥2 kann einen neuen zulässigen Blocker aufnehmen, ohne frühere offene oder geschlossene Findinglinien zu verlieren.
- Korrektur → Final Review trägt den vollständigen Ledger und bietet nur die im Zielkontext zulässige Findingmenge an.
- Record-Ahead-Resume wiederholt keinen bereits persistierten Provideraufruf.
- Eine beschädigte oder referenziell unvollständige Recordlinie scheitert an der ersten betroffenen Grenze mit stabilem Diagnosecode.
- Jede Phase-B-Produktionsänderung ist auf eine am Analysecheckpoint dokumentierte gemeinsame Ursache zurückführbar.

### Slice 2 - Structured-Output-Druck beweisbar messen und sicher begrenzen

#### Ziel

Der Structured-Output-Druck wird ohne unbelegte Ursachenbehauptung quantifiziert. Vertrags- oder Schemaänderungen erfolgen nur, wenn synthetische und Corpus-Evidenz einen konkreten sicheren Änderungsbedarf belegen.

#### Geplante Arbeit

1. Erfolgreiche persistierte native Resultate, erlaubte Diagnosehüllen und bestehende Schemainstanzen inventarisieren, ohne Rohdaten zur Autorität zu erheben.
2. Für jedes begrenzte Freitextfeld Länge, Grenzauslastung, Operation, Writerform und Bindingklasse bestimmen.
3. Provider-schemavalide und bewusst schema-ungültige Grenzfixtures für 50 %, 80 %, 95 %, 100 % und >100 % der jeweiligen Grenze erzeugen.
4. Größe, Schachtelung und Kompositionsformen der tatsächlich serialisierten request-spezifischen Writerschemas messen.
5. Ursachen strikt unterscheiden:
   - lokal bewiesener Längenverstoß,
   - lokal bewiesener Schemaverstoß,
   - reale Providererschöpfung ohne lokal beweisbare Einzelursache.
6. Promptvorgaben so formulieren, dass vollständige, aber knappe Feldinhalte unter einem empfohlenen Zielbudget bleiben. Die harte Vertragsgrenze bleibt Schutzschicht.
7. Exakte Retryklassifikation und Near-Miss-Negativhüllen erweitern.
8. Falls vorhanden, nur nichtinhaltliche, bereits gelieferte Diagnosemetadaten eng allowlisten; keine verworfenen Modellantworten, Prompts oder Gedankengänge persistieren.
9. Schema- oder Domänenänderungen nur mit explizitem Befund, geändertem Writerschemadigest, geänderter Request-ID und Rückweisungsregressionen für alte oder fremde Bindungen umsetzen.
10. Einen optionalen Live-Canary beschreiben, aber weder im Slice noch in der normalen Testsuite ausführen.

#### Voraussichtliche Änderungspfade

- `src/native_review_request.py`
- `src/native_review_contract.py`
- `src/agent_runtime.py`
- die produktive Promptquelle für native Reviews
- gegebenenfalls betroffene Schemas unter `schemas/`
- gezielte Tests für Request, Vertrag, Differentialprüfung und Runtimeklassifikation
- ein eng benannter, nichtautoritativer synthetischer Fixturebestand unter `tests/fixtures/`

Schema- und Produktpfade werden nur geändert, wenn die Messphase einen konkreten Bedarf belegt. Andernfalls bleibt Slice 2 eine Test-, Prompt- und Diagnoseverbesserung.

#### Akzeptanzkriterien

- Für jedes begrenzte Freitextfeld liegt eine reproduzierbare Inventur erfolgreicher und synthetischer Fixtures vor.
- Kein Bericht leitet aus `error_max_structured_output_retries` allein einen Längen- oder Schemaverstoß ab.
- Writer-valid/lokal-ungültig und Writer-ungültig/lokal-gültig bleiben für schema-ausdrückbare Regeln leere Differenzmengen, abgesehen von ausdrücklich registrierten Providerausnahmen.
- Der exakt bekannte Diagnoseenvelope wird begrenzt wiederholt; Near-Miss-Envelopes bleiben fail-closed.
- Physische Wiederholung verändert Inputdigest, Request-ID und Policybindung nicht.
- Verworfene Modellinhalte werden nicht persistiert.
- Falls sich keine sichere Schemaänderung rechtfertigen lässt, gilt der Slice mit vollständiger Inventur, synthetischen Grenznachweisen, knappen Promptvorgaben und robuster Retryabgrenzung als erfolgreich.
- Falls ein Schema geändert wird, ändern sich Writerschemadigest und Request-ID deterministisch; alte oder fremde Bindungen werden abgewiesen.

### Slice 3 - Providerfreier autonomer Resilienz- und Abschlussnachweis

#### Ziel

Die Übergangsmatrix und die Structured-Output-Ergebnisse werden in deterministischen End-to-End-Szenarien zusammengeführt. Der bereits produktiv belegte Happy Path bleibt Baseline; die noch unbelegten Korrektur-, Fehler- und Resume-Pfade bilden den Schwerpunkt.

#### Geplante Arbeit

1. Mit geskripteten nativen Codex- und Claude-Ergebnissen mindestens folgende vollständige Abläufe ausführen:
   - Happy Path Plan → Slice → Final Review → Queueabschluss,
   - Slice-Denial → Codex-Korrektur → erneutes Slice-Review → Freigabe,
   - Final-Review-Denial → Korrekturrunde 1 → erneutes Review,
   - Final-Review-Denial → Korrekturrunde 1 → neuer Blocker → Korrekturrunde 2 → erneutes Final Review,
   - transienter exakter Claude-Diagnoseenvelope → automatische gebundene Wiederholung,
   - Near-Miss-Diagnose → fail-closed ohne automatische Wiederholung,
   - Unterbrechung nach Record-Ahead-Persistenz → Resume ohne zweiten Provideraufruf,
   - zulässiger Commit-HEAD-Drift → exaktes Nutzergate → erfolgreicher weiterer
     Ablauf; behauptet werden `GateReason.UNEXPECTED_FILE`, das am
     Zeichenkettenanfang exakt geparste Detail-Regelpräfix `HEAD-DRIFT`,
     `resume_step == WorkflowStep.SLICE_COMMIT` und der exakte
     fingerprintgebundene Pfadsatz,
   - Slice-Boundary-Drift → exaktes Policygate; behauptet werden
     `GateReason.UNEXPECTED_FILE`, das am Zeichenkettenanfang exakt geparste
     Detail-Regelpräfix `SLICE-HEAD-DRIFT`, ein leerer Pfadsatz und fehlender
     `resume_step`,
   - Scopeverletzung → `GateReason.UNEXPECTED_FILE` mit dem dafür vorgesehenen,
     am Zeichenkettenanfang exakt geparsten Regelpräfix `UNEXPECTED-PATH` und
     dem exakten Pfadsatz,
   - erfolgreicher direkter Resume → genau einmalige Outbox-Finalisierung.
2. Provideraufrufe, Records, Mirrors, Gates, Commitgrenzen, Finding-Ledger und Queuebewegungen pro Schritt behaupten.
3. Einen deterministischen Bericht erzeugen, der Sicherheit, Verfügbarkeit und Autonomie getrennt ausweist.
4. Roadmap und Betriebsdokumentation erst nach grüner Matrix aktualisieren.
5. Einen realen Bootstrap-Smoke-Test nur als separaten nachgelagerten Produktionsnachweis vorbereiten.

#### Voraussichtliche Änderungspfade

- `src/dry_run_scenarios.py`
- nur falls für den gemeinsamen Harness erforderlich die bereits in Slice 1 geprüften Workflow-/Orchestratormodule
- Tests für Dry-Run, Workflow, Orchestrator-Runtime, Watcher und Queueabschluss
- `docs/internal/ORCHESTRATOR_ROADMAP_PHASE_2_PLUS.md`
- ein eindeutig benannter Abschlussbericht unter `docs/internal/`

#### Akzeptanzkriterien

- Alle verpflichtenden providerfreien Szenarien enden im erwarteten terminalen Zustand.
- Normale und exakt transient heilbare Szenarien benötigen kein Nutzergate.
- Sicherheitsrelevante Drift- und Korruptionsfälle erzeugen das anhand von
  Gategrund, exakt ab Zeichenposition null geparstem Detail-Regelpräfix,
  Gateart, Resume-Schritt, Fingerprint und exaktem Pfadsatz erwartete Gate oder
  den erwarteten fail-closed-Diagnosecode.
- Commit-HEAD-Drift, Slice-Boundary-Drift und Scopeverletzung dürfen trotz
  gemeinsamem `GateReason.UNEXPECTED_FILE` nicht verwechselt werden. Die
  Prüfung vergleicht das vollständige Regelpräfix vor ` | ` auf Gleichheit und
  verwendet weder `in` noch eine unankerte Teilstringsuche.
- Vertauscht eine Mutationsprobe `HEAD-DRIFT` und `SLICE-HEAD-DRIFT`, müssen
  beide betroffenen Szenarien fehlschlagen; die enthaltene Zeichenfolge
  `HEAD-DRIFT` darf den Boundary-Drift nicht als Commit-Drift qualifizieren.
- Das Boundary-Drift-Szenario behauptet Policygate, leeren Pfadsatz und
  fehlenden `resume_step`; das Commit-Drift-Szenario behauptet Nutzergate,
  den exakten Pfadsatz und `WorkflowStep.SLICE_COMMIT`.
- Kein Resume wiederholt einen bereits autoritativ persistierten Provideraufruf, Commit oder Queueabschluss.
- Direkter Resume und Watch-Pfad finalisieren Queueartefakte genau einmal.
- Der vollständige Finding-Ledger bleibt über zwei Korrekturrunden und das anschließende Final Review erhalten.
- Der Bericht trennt Sicherheit, Verfügbarkeit und Autonomie und verwendet keine volatilen Laufzeit- oder Kostenschwellen.

## 8. Manuelles Entwicklungs- und Reviewverfahren

1. Vor Implementierungsbeginn wird der Zielbranch vom aktuellen sauberen `master` angelegt.
2. Slice 1 Phase A wird implementiert und ausgewertet. Der Analysecheckpoint wird im Slice-Bericht dokumentiert, bevor gegebenenfalls Phase B beginnt.
3. Nach fokussierter Validierung reviewt Claude Slice 1 adversarial. Blocker werden behoben und erneut reviewt; erst danach beginnt Slice 2.
4. Dasselbe Verfahren gilt für Slice 2 und Slice 3.
5. Der Orchestrator führt während dieser Entwicklung keine Schritte aus. Insbesondere werden keine `run_task`-, Watch-, Resume- oder Bootstrap-Aufrufe als Entwicklungsdriver verwendet.
6. Nach Slice 3 führen Codex und Claude parallel getrennte vollständige Branchreviews durch und persistieren ihre Ergebnisse in eindeutig bezeichneten Dateien.
7. Die Freigabe erfordert geschlossene Blocker beider Reviews, eine erfolgreiche vollständige Repositorymatrix für den finalen Fingerprint und einen sauberen `git diff --check`.
8. Git-Commit, Merge und ein späterer realer Smoke-Test erfolgen erst nach ausdrücklicher Freigabe.

## 9. Validierungsstrategie

- fokussierte Tests nach jedem Slice,
- Übergangsmatrix und Differentialtests,
- providerfreie End-to-End-Szenarien,
- vollständige Repositorysuite nach dem letzten Slice und nach risikoreichen Änderungen an Orchestration, Prompt, Parser, Watch oder State,
- `git diff --check`,
- getrennte manuelle Gesamtreviews durch Codex und Claude.

Die vollständige Suite wird nicht in jedem Review erneut ausgeführt. Reviewer verwenden die vorhandene Attestierung desselben Diff-Fingerprints und dürfen kleine providerfreie Falsifikationsproben durchführen. Externe Provider-Canaries werden nicht implizit gestartet.

## 10. Nicht-Scope

- kein Rückbau auf freie Textmarker als technische Autorität,
- keine Abschwächung der Recordkette oder Mirrorprüfung,
- keine manuelle Reparatur von State, Checkpoints oder Recorddateien,
- keine stille Migration historischer Protokolle,
- keine Wiedereinführung einer ausgemusterten dritten Reviewerrolle,
- keine Persistenz verworfener Modellinhalte,
- keine allgemeine UI- oder Auditprojektionsarbeit,
- keine flakey zeit-, kosten- oder providerabhängige Testschwelle,
- kein realer Provideraufruf in der normalen Repositorysuite,
- kein Push, Merge, Release oder Deployment innerhalb der Slice-Implementierung.

## 11. Stopbedingungen

Die Arbeit stoppt und der Plan wird erneut reviewt, wenn:

- Phase A widersprüchliche autoritative Ledgerdefinitionen offenlegt,
- ein Fix nur als kantenbezogene Sonderbehandlung statt als gemeinsame Invariante möglich wäre,
- der Analysecheckpoint mehrere unabhängige Produktionsreparaturpakete statt einer begrenzten gemeinsamen Ursache ergibt,
- eine Structured-Output-Entlastung Finding-, Request-, Schema- oder Attestierungsbindung lockern müsste,
- die Ursachenbestimmung verworfene Modellinhalte oder deren neue Persistenz voraussetzen würde,
- vorhandene Rohantworten nur durch freie Interpretation statt durch typisierte Felder klassifiziert werden könnten,
- ein verpflichtender Test einen realen Provideraufruf benötigte,
- die Umsetzung eine laufende Orchestratorinstanz oder deren persistierten Zustand verändern müsste.

## 12. Definition of Done

Das Paket ist abgeschlossen, wenn:

- alle drei Slices und ihre Akzeptanzkriterien erfüllt oder ein ausdrücklich zulässiger entfallender Produktionsschritt dokumentiert sind,
- die vollständige Übergangsmatrix grün ist,
- Structured-Output-Druck entweder nachweislich reduziert oder ohne unbelegte Ursachenbehauptung sauber abgegrenzt ist,
- alle verpflichtenden providerfreien End-to-End-Szenarien autonom terminieren,
- die vollständige Repositorysuite für den finalen Fingerprint erfolgreich ist,
- `git diff --check` sauber ist,
- Codex und Claude den vollständigen Branch ohne offenen Blocker freigeben,
- der nachgelagerte reale Bootstrap-Smoke-Test als separate Tätigkeit vorbereitet ist.

Ein realer Smoke-Test ist kein Teil der Implementierung und wird erst nach Abschluss, Freigabe und Integration dieses Pakets ausdrücklich gestartet.

---

# Claude-Planreview

## R1. Reviewgrenze und Methodik

Manuelles adversariales Planreview außerhalb des Orchestrators, ohne strukturierte JSON-Ausgabe. Kein `run_task`, kein Watcher, kein Bootstrap, kein Providerabruf.

Ich habe `AGENTS.md`, `CLAUDE.md`, diesen Arbeitsplan und die für konkrete Planbehauptungen erforderlichen Repositoryquellen gelesen. Produktcode, Tests und der normative Planinhalt der Abschnitte 1–12 sind unverändert; dieser Abschnitt ist rein additiv. Die vollständige Testsuite wurde nicht ausgeführt. Ich habe ausschließlich kleine providerfreie Lese-, Import- und Parserproben durchgeführt, jeweils zur Prüfung einer benannten Planbehauptung. Es wurde nichts committet, gemergt, gepusht und kein Branch gewechselt.

## R2. Selbst festgestellter Repositorystand

Nicht aus dem Auftrag übernommen, sondern eigens erhoben:

- aktueller Branch: `master`
- `HEAD`: `de1bd9ea40392c17e47efcc8d243c7de1438ae59`, kurz `de1bd9e`, `merge: complete decision table prose structuring`
- Worktree: sauber bis auf dieses untrackte Plandokument
- `master..HEAD` ist leer; der Plan wird also gegen genau die in §1 dokumentierte Basis geprüft

Die in §1 genannte geprüfte Basis `de1bd9e` stimmt mit dem tatsächlichen Stand überein.

## R3. Verifizierte Planbehauptungen

**Regressionsbasis vollständig integriert.** Für jeden in §2 und §5.3 vorausgesetzten Commit habe ich die Vorfahrenschaft zu `master` geprüft: `b2d55a3` (Structured-Output-Retryklassifikation), `2965bcf` (Queuefinalisierung), `1143fca` (HEAD-Drift-Gate), `f09ed8b` (Korrektur-Findingteilmenge) und `344a219` (Entscheidungstabellenprosa) sind sämtlich in `master`. Der in einem früheren Entwurf berechtigte Einwand gegen die Ausgangsbasis ist damit gegenstandslos; §1 zweigt zu Recht von `master` ab.

**Die als bestehend deklarierten Fähigkeiten existieren.** `carry_forward_native_findings` liegt in `src/orchestrator.py:765` und wird an drei Aufrufstellen verwendet. `_is_claude_structured_output_retry_exhaustion` liegt in `src/agent_runtime.py:1943`. `finalize_queue_success` liegt in `src/inbox_watcher.py:497`, ist markerbasiert, ausdrücklich idempotent und wird sowohl vom Watcher als auch vom direkten Resumepfad in `src/orchestrator.py` genutzt. §5.3 ordnet diese Fähigkeiten korrekt als regressiv abzusichernde Basis ein und nicht als Neuentwicklung.

**Die Fail-open-Lücke im Carry-Forward ist geschlossen.** Eine providerfreie Falsifikationsprobe gegen den aktuellen Stand — Recordkette ohne Finding-Records bei nichtleerem Mirror — endet in `src/orchestrator.py:805` mit `record-native finding carry-forward differs from the state-v3 mirror` statt mit stillem Verwerfen. Invariante §5.1, Satz 7 beschreibt insoweit bestehendes Verhalten.

**`artifact_migration.py` ist kein toter Pfad.** `resolve_resume_state` wird von `src/orchestrator.py:41` und `src/state_io.py:14` importiert. Die Nennung als möglicher Phase-B-Pfad ist berechtigt.

**Der Happy-Path-Nachweis in §3.4 ist belastbar.** Der Lauf `watch-20260827-090314.548190Z-e73cf1146265` weist drei Work-Units (Plan, Slice, Final Review) jeweils in Runde 1 aus, ohne Gate, ohne Invocationfehler und ohne offenes Finding, mit genau einmaliger Bewegung von Idee und Handoff nach `outbox/done`. §3.4 ordnet ihn korrekt als Baseline ein und beansprucht ausdrücklich nicht, damit die Korrektur-, Mehr-Runden- und Resume-Pfade belegt zu haben. Diese Abgrenzung ist der wichtigste inhaltliche Fortschritt gegenüber dem Entwurf.

**Slice-Zuschnitt vertragskonform.** Drei fachlich geschlossene Slices, keine Dateianzahlgrenze, Zusatzpfade an eine Invariante gebunden. §5.2 stimmt mit der Regel aus `AGENTS.md` überein, wonach Korrektur- und Finalrunden keine neue `OBSERVATION` erzeugen dürfen, ein neu entdeckter ausführbarer Defekt aber als `BLOCKER` zulässig bleibt.

**Messbarkeit der Schemakomplexität gegeben.** Das statische Writerschema `schemas/native-agent-review-result-v2.schema.json` besitzt 11 `$defs`, 5089 Bytes, Schachtelungstiefe 9 und 7 Kompositionsknoten; die requestspezifische Verengung ist deterministisch aus dem Kontext ableitbar. Slice 2, Schritt 4 ist providerfrei erfüllbar.

## R4. Befunde

### C-01 — Zirkularität des Übergangsoracles ist nicht ausgeschlossen (BLOCKER)

Phase A, Schritt 3 verlangt ein kanonisches Oracle, das Ledger, angebotene Findingmenge, Runde, nächsten Schritt, Aufrufanzahl sowie Record-, Mirror-, Gate- und Checkpointzustand erwartet. Der Plan sagt an keiner Stelle, dass diese Erwartungswerte **unabhängig von der Produktionsableitung** gebildet werden müssen.

Das ist keine theoretische Sorge, sondern die naheliegende Umsetzung. Die vorhandene Suite bildet Erwartungen vielfach dadurch, dass sie die Produktionsfunktion aufruft — etwa `tests/test_orchestrator_runtime.py:1327`, `:1570`, `:1644` mit `replay_findings(...)`. Besonders kritisch ist das Akzeptanzkriterium „Record und Mirror werden nach jedem Szenarioschritt verglichen", denn genau dieser Vergleich **ist** `authoritative_native_findings`: Die Funktion projiziert aus Records und wirft bei Abweichung vom Mirror. Eine Matrix, die diesen Vergleich durch Aufruf derselben Funktion behauptet, bestätigt nur deren Selbstkonsistenz und wäre auch dann grün, wenn Umfangsregel und Carry-Forward gemeinsam falsch sind. Sie könnte die Defektklasse, deretwegen dieses Paket existiert, konstruktiv nicht finden.

Der Befund ist planblockierend, weil er nur im Plan behebbar ist: Phase A und die Akzeptanzkriterien müssen die Unabhängigkeit des Oracles und ein Falsifikationskriterium fordern. Die Reparatur ist klein.

### C-02 — Das Drift-Gate ist am Gategrund nicht unterscheidbar (OBSERVATION)

Slice 3 fordert, sicherheitsrelevante Drift- und Korruptionsfälle müssten „genau das erwartete Gate" erzeugen. `1143fca` löst den HEAD-Drift jedoch über `GateReason.UNEXPECTED_FILE` aus, denselben Grund, den auch eine Scopeverletzung verwendet. `GateReason` kennt keinen eigenen Driftwert. Unterscheidbar sind die Fälle nur über das Detailpräfix `HEAD-DRIFT | `, den Pfadsatz und `resume_step=SLICE_COMMIT`.

Das Kriterium ist damit in seiner jetzigen Formulierung nicht adversarial falsifizierbar: Ein Test, der nur den Gategrund prüft, kann einen fälschlich als Scopeverletzung emittierten Drift nicht von einem korrekten Driftgate trennen. Der Plan sollte benennen, an welche beobachtbaren Felder die Behauptung gebunden wird.

### C-03 — Das Dokument imitiert die `PLAN_ONLY`-Form, ohne ihren Vertrag zu erfüllen (OBSERVATION)

Das Plandokument verwendet fortlaufende `### Slice N - Titel`-Überschriften und `#### Akzeptanzkriterien`. Beides sind kanonische Formen des `PLAN_ONLY`-Handoffvertrags aus `src/plan_handoff.py:33`. Statt der dort in Zeile 20 zwingend geforderten alleinstehenden Überschrift `**Exakter Änderungspfad**` benutzt es jedoch `#### Voraussichtliche Änderungspfade`.

Providerfrei falsifiziert: `plan_handoff.extract_implementation_slices` auf dieses Dokument angewendet endet mit `PlanHandoffError: Slice 1 has no exact change-path section`.

Das ist inhaltlich konsistent mit §1 — manuelle Entwicklung außerhalb des Orchestrators — aber im Dokument nicht ausgesprochen. Ein Dokument, das zu vier Fünfteln wie ein Orchestratorplan aussieht, im Verzeichnis der Orchestratorpläne liegt und beim Einspeisen einen `PLAN-CONTRACT-INVALID`-Gate erzeugt, ist eine vermeidbare Falle für spätere Leser.

## R5. Prüfdimensionen im Einzelnen

**Problembegründung und Scope.** Die Trennung zwischen JSON-Transport, Record-/Mirror-Übergängen, Runtime- und Umgebungsfehlern sowie Providererschöpfung ist in §3.1 bis §3.3 sauber gezogen. §3.3 formuliert ausdrücklich, dass die technische Diagnose den Auslöser nicht beweist. §3.4 ordnet den Happy Path korrekt als Baseline und nicht als Beweis der Risikopfade ein. Das Paket ist außerhalb von `run_task` umsetzbar; kein Slice benötigt eine laufende Instanz oder persistierte Laufzustände.

**Slice 1.** Das Kantinventar deckt die real aufgetretenen Grenzen ab: Slice → Final Review, Final Review → Korrektur, Korrekturrunde 1 → ≥2, neuer zulässiger Blocker in späterer Runde, Korrektur → erneutes Final Review, Unterbrechung vor Record, zwischen Record und Mirror und zwischen Mirror und Checkpoint, HEAD unverändert, freigegebener und abweichender Drift. Phase A ist glaubwürdig test-only gehalten; der Analysecheckpoint begrenzt Phase B fachlich und pfadseitig und macht einen test-only Slice ausdrücklich zulässig. Die Stopbedingungen in §11 verhindern einen entgrenzten Reparaturslice wirksam, insbesondere durch die Bedingung „mehrere unabhängige Produktionsreparaturpakete". Offen bleibt allein C-01.

**Slice 2.** Der Plan setzt keine nicht persistierten Claude-Rohantworten voraus; Schritt 1 spricht ausdrücklich von erfolgreichen persistierten Resultaten, erlaubten Diagnosehüllen und Schemainstanzen. Die Ursachentrennung in Schritt 5 hält den dritten Ausgang „reale Providererschöpfung ohne lokal beweisbare Einzelursache" offen, und das Akzeptanzkriterium verbietet den Fehlschluss aus `error_max_structured_output_retries` ausdrücklich. Ein Slice ohne Schemaänderung ist explizit als Erfolg definiert. Jede mögliche Schemaänderung ist an geänderten Writerschemadigest, geänderte Request-ID und Rückweisungsregressionen gebunden. Die zulässige Telemetrie ist auf bereits gelieferte, nichtinhaltliche, eng allowlistbare Metadaten begrenzt; §10 verbietet die Persistenz verworfener Modellinhalte. Die Retryregel bleibt auf den exakten Envelope beschränkt, Near-Miss-Hüllen bleiben fail-closed. Diese Dimension hat keinen Befund.

**Slice 3.** Die Szenarienliste führt die Invarianten aus Slice 1 und 2 zusammen und enthält die beiden bislang unbelegten Kernfälle — zwei Korrekturrunden mit neuem Blocker sowie Record-Ahead-Resume ohne zweiten Provideraufruf. Schrittweise statt nur terminale Behauptung ist in Schritt 2 gefordert. Der reale Bootstrap-Smoke-Test ist in Schritt 5, §8.8 und §12 dreifach als nachgelagerte, separat freizugebende Tätigkeit abgegrenzt. Sicherheit, Verfügbarkeit und Autonomie bleiben über §4 und §6 getrennt messbar. Befund hier: C-02.

**Repositorytreue.** Alle im Plan genannten produktiven Eintrittspunkte existieren; `src/prompts.py` ist mit `NATIVE_CLAUDE_SYSTEM_POLICY` die in Slice 2 gemeinte produktive Promptquelle. Veraltete oder doppelte Produktionspfade, die den Plan beeinflussen würden, habe ich nicht gefunden; der zuvor tote mirrorbasierte Carry-Forward ist bereits entfernt. Widersprüche zwischen Arbeitsplan, `AGENTS.md` und `CLAUDE.md` habe ich keine gefunden. Befund hier: C-03.

**Sicherheit und Pre-Mortem.** Ein Weg, auf dem ein beschädigter Recordbestand still in einen konsistent scheinenden Mirror übergeht, ist im aktuellen Code durch die Containmentprüfung geschlossen; ich habe das falsifizierend bestätigt. Doppelte Provideraufrufe, Commits oder Queueabschlüsse nach Resume sind durch Record-Ahead-Persistenz und die markerbasierte, bindungsgeprüfte `finalize_queue_success` adressiert und in Slice 3 als Szenario behauptet. Der Verlust oder die Übernahme einer fremden Findinglinie über Korrekturrunden ist als Invariante §5.1 und als Akzeptanzkriterium in Slice 1 und Slice 3 abgedeckt — allerdings nur so wirksam, wie C-01 aufgelöst wird. Eine Lockerung der semantischen Bindung durch die Structured-Output-Entlastung ist durch §5.4, §10 und die Stopbedingungen mehrfach verriegelt.

## R6. Gesamtbewertung

Der Plan ist gegenüber dem Entwurf deutlich gereift: Die Ausgangsbasis stimmt jetzt, die Evidenzgrenze bei verworfenen Modellinhalten ist ehrlich gezogen, Messung und Korrektur sind in Slice 1 durch einen verbindlichen Checkpoint getrennt, und ein Slice 2 ohne Schemaänderung gilt ausdrücklich als Erfolg. Zwei der drei früheren Einwände sind vollständig eingearbeitet, der dritte ist durch die Merges gegenstandslos geworden.

Verbleibend ist ein Befund, der die Wirksamkeit des Kernstücks betrifft und deshalb vor Implementierungsbeginn im Plantext aufzulösen ist. Nach Aufnahme der Unabhängigkeits- und Mutationsanforderung in Phase A und in die Akzeptanzkriterien von Slice 1 sowie nach Präzisierung von C-02 und C-03 ist der Plan aus meiner Sicht freigabefähig.

REVIEWER: claude

NEW_FINDING: C-01 | BLOCKER | Phase A verlangt ein kanonisches Übergangsoracle, fordert aber nirgends dessen Unabhängigkeit von der Produktionsableitung; da das Akzeptanzkriterium "Record und Mirror werden nach jedem Szenarioschritt verglichen" genau der Funktion authoritative_native_findings entspricht und die bestehende Suite Erwartungen idiomatisch durch Aufruf von replay_findings bildet, kann die Matrix konstruktiv grün bleiben, obwohl Umfangsregel und Carry-Forward gemeinsam falsch sind. | Phase A und die Slice-1-Akzeptanzkriterien fordern, dass Ledger, angebotene Findingmenge, Runde, nächster Schritt, Aufrufanzahl sowie Record-, Mirror-, Gate- und Checkpointzustand als literale Tabellendaten im Matrixmodul stehen und nicht durch Aufruf von replay_findings, carry_forward_native_findings oder authoritative_native_findings gebildet werden. Providerfreier Falsifikationstest: mit einem Monkeypatch, der replay_findings unverändert leer zurückgeben lässt, und in einem zweiten Lauf mit einem Monkeypatch, der carry_forward_native_findings sein Argument unveraendert durchreicht, muss jeweils mindestens ein Matrixfall je betroffener Kante fehlschlagen; bleibt die Matrix unter einer der beiden Mutationen vollstaendig gruen, ist das Oracle zirkulaer und der Slice nicht abgenommen.

NEW_FINDING: C-02 | OBSERVATION | Slice 3 fordert fuer Drift- und Korruptionsfaelle "genau das erwartete Gate", doch 1143fca emittiert den HEAD-Drift ueber GateReason.UNEXPECTED_FILE, denselben Grund wie eine Scopeverletzung; GateReason kennt keinen eigenen Driftwert, sodass eine Behauptung allein auf dem Gategrund einen falsch klassifizierten Drift nicht aufdecken kann. | Das Slice-3-Szenario fuer autorisierten HEAD-Drift behauptet Gategrund, das Detailpraefix "HEAD-DRIFT | ", resume_step == WorkflowStep.SLICE_COMMIT und den exakten Pfadsatz; ein Scopeverletzungsszenario derselben Matrix muss sich mindestens im Detailpraefix unterscheiden. Der Test schlaegt fehl, wenn beide Szenarien in allen behaupteten Feldern uebereinstimmen.

NEW_FINDING: C-03 | OBSERVATION | Das Dokument verwendet mit fortlaufenden "### Slice N - Titel"-Ueberschriften und "#### Akzeptanzkriterien" kanonische PLAN_ONLY-Formen, erfuellt den Handoffvertrag aber nicht, weil die zwingende alleinstehende Ueberschrift "**Exakter Aenderungspfad**" fehlt; providerfrei falsifiziert endet plan_handoff.extract_implementation_slices auf diesem Dokument mit "PlanHandoffError: Slice 1 has no exact change-path section". | Abschnitt 1 erhaelt einen ausdruecklichen Satz, dass dieses Dokument kein PLAN_ONLY-Artefakt ist und nicht ueber WORK_PLAN_PATH verarbeitet wird, oder die Slice-Abschnitte uebernehmen die kanonische Ueberschrift. Akzeptanztest: entweder ist die Nichtverarbeitbarkeit im Dokument dokumentiert, oder plan_handoff.extract_implementation_slices liefert fuer alle drei Slices exakte Pfadlisten ohne Fehler.

REVIEW_EVIDENCE: geprueft wurden Repositorystand und Vorfahrenschaft der Regressionsbasis, Existenz und Aufrufstellen der als bestehend deklarierten Faehigkeiten, Fail-closed-Verhalten des Carry-Forward per Falsifikationsprobe, Lebendigkeit von artifact_migration, Handoffvertrag per Parserprobe, Messbarkeit der Writerschemakomplexitaet, Widerspruchsfreiheit gegen AGENTS.md und CLAUDE.md, Slice-Zuschnitt, Stopbedingungen und Nicht-Scope | groesstes Restrisiko: die Kantinventur der Phase A ist selbst eine menschliche Aufzaehlung ohne maschinelle Vollstaendigkeitsgarantie, sodass eine produktiv erreichbare, aber nicht aufgezaehlte Kante unbemerkt ausserhalb der Matrix bleiben kann | realistische Bruchbedingung: sobald eine kuenftige Aenderung eine neue Work-Unit-Art, einen neuen Gategrund oder einen zusaetzlichen Rundenuebergang einfuehrt, ohne das Inventar zu erweitern, bleibt die Matrix gruen und deckt die neue Grenze nicht ab

PRE_MORTEM: In drei Monaten scheitert dieses Paket am wahrscheinlichsten daran, dass die Uebergangsmatrix zwar gruen ist, ihre Erwartungswerte aber ganz oder teilweise aus denselben Produktionsfunktionen stammen, die sie pruefen soll. Der naechste Grenzdefekt wuerde dann erneut erst im Produktivlauf sichtbar, und die gruene Matrix wuerde faelschlich als Beleg gegen einen Regressionsverdacht angefuehrt. Die zweitwahrscheinlichste Ursache ist eine nach Abschluss eingefuehrte neue Kante oder ein neuer Gategrund, der nicht ins Inventar aufgenommen wird, weil kein Mechanismus die Vollstaendigkeit der Aufzaehlung gegen den Produktivcode erzwingt.

PLAN_APPROVAL: NO

STATUS: DONE

---

# Claude-Planreview - Revision 2

## R2.1 Reviewgrenze und Methodik

Erneutes manuelles adversariales Planreview der normativen Abschnitte 1 bis 12, außerhalb des Orchestrators, ohne strukturierte JSON-Ausgabe. Kein `run_task`, kein Watcher, kein Bootstrap, kein Providerabruf.

Gelesen habe ich `AGENTS.md`, `CLAUDE.md`, den vollständigen Arbeitsplan einschließlich meines ersten Reviewabschnitts sowie die zur Falsifikation erforderlichen Repositoryquellen. Die normativen Abschnitte, Produktcode und Tests sind unverändert; dieser Abschnitt ist rein additiv. Die vollständige Testsuite wurde nicht ausgeführt. Verwendet habe ich ausschließlich kleine providerfreie Import-, Regex- und Quellcodeproben, jeweils zu einer benannten Behauptung. Kein Commit, kein Branchwechsel, kein Merge, kein Push.

Erwartete Werte und Erfolgsbehauptungen aus dem Reviewauftrag habe ich nicht übernommen, sondern jeweils selbst geprüft.

## R2.2 Selbst festgestellter Repositorystand

- Branch: `master`
- `HEAD`: `de1bd9ea40392c17e47efcc8d243c7de1438ae59`, kurz `de1bd9e`
- Worktree: sauber bis auf dieses untrackte Plandokument
- Der Stand ist gegenüber Revision 1 unverändert; die in §1 dokumentierte geprüfte Basis stimmt weiterhin.

## R2.3 C-01 - Unabhängigkeit des Übergangsoracles

**Geschlossen.**

Die Revision adressiert jeden Teilaspekt des Blockers und darüber hinaus die von mir genannten indirekten Vektoren:

- Phase A Schritt 3 fordert ein „von den Produktionsableitungen unabhängiges Oracle", dessen Erwartungswerte „als literale, reviewbare Tabellendaten im Matrixmodul" stehen, und verbietet die Bildung der Sollwerte durch `replay_findings`, `carry_forward_native_findings`, `authoritative_native_findings` „oder einer anderen zu prüfenden Produktionsprojektion". Die Aufzählung ist damit nicht abschließend gemeint, sondern durch eine Regel ergänzt.
- Alle fünf von mir geforderten Sollwertklassen sind namentlich aufgeführt: Ledger, angebotene Findingmenge, Runde und nächster Schritt, Aufrufanzahl sowie Record-, Mirror-, Gate- und Checkpointzustand.
- Schritt 7 trennt Ist von Soll ausdrücklich: Produktive Eintrittspunkte erzeugen die tatsächlichen Zustände, geprüft wird ausschließlich gegen die literalen Oraclewerte. Der Satz „Gemeinsame Helfer dürfen Eingaben aufbauen und Ausgaben normalisieren, jedoch keine erwarteten Ledger, Findingmengen, Schritte, Gates oder Aufrufzahlen aus Produktionsresultaten ableiten" schließt die indirekte Zirkularität über Fixturebuilder, Normalisierungshelfer und aus Produktivzuständen zurückgelesene Erwartungswerte.
- Schritt 8 übernimmt beide Mutationsproben in der von mir geforderten Form und fügt die Abnahmesperre hinzu: „Bleibt die Matrix vollständig grün, gilt das Oracle als zirkulär und Slice 1 ist nicht abnahmefähig." Die Granularität „mindestens ein Matrixfall jeder betroffenen Kantenklasse" ist ebenfalls gesetzt.
- Das Akzeptanzkriterium schließt die ursprüngliche Lücke wörtlich: Recordprojektion und Mirror werden „jeweils gegen diese unabhängigen Sollwerte geprüft, nicht lediglich gegeneinander und nicht nur terminal". Genau diese Formulierung verhindert, dass die Matrix den Selbstkonsistenzvergleich von `authoritative_native_findings` als Beweis verwendet.

Beide Mutationen sind methodisch geeignet. Die erste trifft die Ledgerableitung aus der Recordlinie, die zweite die Work-Unit-Grenze; zusammen decken sie die beiden Ableitungen ab, aus denen die zuletzt behobenen Grenzdefekte stammten. Ich habe keine verbliebene Zirkularität im Plantext gefunden.

## R2.4 C-02 - Unterscheidbare Drift- und Scope-Gates

**Geschlossen.**

Slice 3 behauptet nun alle fünf beobachtbaren Bindungsfelder: `GateReason.UNEXPECTED_FILE`, das Detailpräfix `HEAD-DRIFT`, `resume_step == WorkflowStep.SLICE_COMMIT` und den exakten fingerprintgebundenen Pfadsatz, ergänzt um ein eigenes Scopeverletzungsszenario mit „dafür vorgesehenem, vom HEAD-Drift verschiedenem Detailpräfix und Pfadsatz". Das Akzeptanzkriterium verlangt zusätzlich, dass beide Szenarien trotz gemeinsamen Gategrundes „nicht in allen behaupteten Feldern übereinstimmen".

Die Voraussetzung ist im Code gegeben: Der Commit-Drift meldet `HEAD-DRIFT` (`src/orchestrator.py:2133`), die Scopeverletzung `UNEXPECTED-PATH` (`src/gates.py:20`, verwendet in `src/workflow.py:1116`, `:1434`, `:2140`). Eine Verwechslung in beide Richtungen lässt den Test fehlschlagen, sofern die Präfixbehauptung verankert und nicht als Teilstringsuche formuliert wird. Diese Einschränkung führe ich als C-05 gesondert.

## R2.5 C-03 - Abgrenzung vom PLAN_ONLY-Handoff

**Geschlossen.**

Abschnitt 1 enthält jetzt einen eigenen Absatz, der alle vier geforderten Aussagen trifft: kein `PLAN_ONLY`-Artefakt, keine Übergabe als `WORK_PLAN_PATH` an `plan_handoff.extract_implementation_slices`, keine Verarbeitung durch `run_task`, und die Slice-Überschriften strukturieren ausschließlich die manuelle Zusammenarbeit. Der Zusatz, die Pfadlisten seien „bewusst nur voraussichtliche" und „keine maschinellen Handoff-Allowlisten", nimmt der Form die verbliebene Verwechslungsgefahr.

Für einen späteren Leser entsteht damit keine plausible Erwartung mehr, das Dokument könne direkt eingespeist werden. Die Nichtverarbeitbarkeit ist dokumentiert; ein erneuter Parserlauf ist zur Abnahme nicht mehr erforderlich, weil der Plan die zweite zulässige Auflösung meines Akzeptanztests gewählt hat.

## R2.6 Source-Map und Vollständigkeitsguard

Der neue Mechanismus geht die richtige Frage an, greift aber an der falschen Granularität. Er reduziert das von mir benannte Restrisiko nicht in der Dimension, in der es sich historisch realisiert hat, und behauptet zugleich eine Abdeckung, die er nicht leistet.

Phase A Schritt 9 und das zugehörige Akzeptanzkriterium binden das Inventar an „jede aktuelle Work-Unit-Art, jeden Gategrund und jeden benannten produktiven Übergangseinstieg" und lassen den Guard bei „einem künstlich ergänzten Enumwert oder Registryeintrag" fehlschlagen. Positiv ist, dass der Guard ausdrücklich keine fachlichen Sollwerte ableitet und die literale Unabhängigkeit des Oracles unberührt lässt.

Providerfrei gemessen am aktuellen Stand:

| Inventarisierbare Menge | Umfang |
|---|---|
| `GateReason`-Werte | 12 |
| `gates.BUILTIN_STOP_RULES` | 5 |
| Detail-Regelpräfixe im Quelltext | 11 |
| davon in keiner Registry | **10** |

Die zehn nicht registrierten Präfixe sind `AGENT-PROFILE-DIFF`, `CODEX-FINAL-REPORT-NOT-READY`, `CODEX-NOT-READY`, `HEAD-DRIFT`, `NO-IMPLEMENTATION-CHANGES`, `PLAN-APPROVAL`, `PLAN-CONTRACT-INVALID`, `QUOTA-RESUME-DIFF`, `SLICE-HEAD-DRIFT` und `TASK-SCOPE`. Sie sind Inline-Stringliterale ohne zentrale Registrierung.

`GateReason.UNEXPECTED_FILE` allein wird an neun Stellen emittiert (`src/orchestrator.py:1944`, `:2107`, `:3507`; `src/workflow.py:1114`, `:1432`, `:1692`, `:2139`, `:2562`, `:2643`) und trägt mindestens vier fachlich verschiedene Sachverhalte mit unterschiedlicher Resume-Semantik: Scopeverletzung, Commit-Zeit-HEAD-Drift als Nutzergate mit Pfadsatz und `resume_step`, Boundary-HEAD-Drift als Policygate ohne beides, sowie fehlende Implementierungsänderungen. Ein Guard, der Gategründe klassifiziert, hakt diesen einen Enumwert einmal ab und hält alle vier für abgedeckt.

Entscheidend ist die historische Evidenz: `1143fca` hat `HEAD-DRIFT` als neues Inline-Literal unter einem **bestehenden** Gategrund eingeführt. Genau dieser Wachstumsmodus ist der real beobachtete, und genau ihn erkennt der Guard nicht. Er schützt gegen den unwahrscheinlichen Fall eines neuen Enumwerts und lässt den wahrscheinlichen Fall passieren.

Das ist planblockierend, weil das Akzeptanzkriterium eine Vollständigkeit über „jeden Gategrund" behauptet, die semantisch hohl bleibt, und weil spätere Leser sich auf diese Zusage stützen würden. Es ist dieselbe Fehlerform wie C-01: ein grünes Ergebnis, das weniger beweist, als es zusagt. Die Reparatur ist klein und rein textlich.

## R2.7 Übrige Dimensionen

**Slice-Zuschnitt unverändert vertragskonform.** Weiterhin höchstens drei fachlich geschlossene Slices, ausdrücklich keine künstliche Dateianzahlgrenze, Zusatzpfade an eine Invariante gebunden. Phase A bleibt echt test-only; Schritt 10 hält fest, dass Produktivcode bis zum Analysecheckpoint unverändert bleibt. Der Analysecheckpoint begrenzt Phase B weiterhin fachlich und pfadseitig, lässt einen test-only Slice ausdrücklich zu und löst bei mehreren unabhängigen Reparaturpaketen eine Stopbedingung aus.

**Slice 2 unverändert und weiterhin ohne Befund.** Die Evidenzgrenze bei verworfenen Modellinhalten, der zulässige Erfolg ohne Schemaänderung, die Bindung jeder Schemaänderung an Writerschemadigest, Request-ID und Rückweisungsregressionen sowie die exakte Retryabgrenzung mit fail-closed Near-Miss-Hüllen bestehen fort.

**Messgrößen, Stopbedingungen, Nicht-Scope und Definition of Done** sind gegenüber Revision 1 unverändert und widerspruchsfrei zu `AGENTS.md` und `CLAUDE.md`.

## R2.8 Gesamtbewertung

Die Revision schließt alle drei Befunde aus Revision 1 vollständig und an mehreren Stellen präziser, als ich gefordert hatte. Besonders die Trennung von Ist und Soll in Phase A Schritt 7 und die Abnahmesperre in Schritt 8 gehen über meinen Akzeptanztest hinaus.

Verbleibend ist ein Befund im neu hinzugefügten Mechanismus selbst. Er ist keine Regression der geschlossenen Punkte, sondern eine Granularitätslücke des Vollständigkeitsguards, die durch eine Änderung der Klassifikationseinheit behoben wird. Nach dieser Anpassung und der Präzisierung aus C-05 halte ich den Plan für freigabefähig.

REVIEWER: claude

FINDING_STATUS: C-01 | CLOSED | Phase A Schritt 3 fordert ein von den Produktionsableitungen unabhaengiges Oracle mit literalen Tabellendaten und verbietet die Sollwertbildung durch replay_findings, carry_forward_native_findings, authoritative_native_findings oder jede andere geprueste Produktionsprojektion. Schritt 7 trennt Ist und Soll und untersagt gemeinsamen Helfern ausdruecklich die Ableitung erwarteter Ledger, Findingmengen, Schritte, Gates und Aufrufzahlen aus Produktionsresultaten, womit auch die indirekte Zirkularitaet ueber Fixturebuilder und Normalisierungshelfer geschlossen ist. Schritt 8 uebernimmt beide Mutationsproben, fordert je Mutation mindestens einen roten Fall je betroffener Kantenklasse und erklaert eine vollstaendig gruene Matrix ausdruecklich fuer nicht abnahmefaehig. Das Akzeptanzkriterium verlangt zudem die Pruefung von Recordprojektion und Mirror gegen die unabhaengigen Sollwerte statt lediglich gegeneinander.

FINDING_STATUS: C-02 | CLOSED | Slice 3 behauptet nun GateReason.UNEXPECTED_FILE, das Detailpraefix HEAD-DRIFT, resume_step gleich WorkflowStep.SLICE_COMMIT und den exakten fingerprintgebundenen Pfadsatz und ergaenzt ein eigenes Scopeverletzungsszenario mit unterscheidbarem Praefix und Sachverhalt. Das Akzeptanzkriterium verbietet Uebereinstimmung beider Szenarien in allen behaupteten Feldern. Die Codevoraussetzung ist gegeben: HEAD-DRIFT in src/orchestrator.py:2133 gegenueber UNEXPECTED-PATH aus src/gates.py:20. Die verbleibende Verankerungsanforderung an die Praefixpruefung fuehre ich als eigenstaendiges C-05 und nicht als Fortbestand von C-02.

FINDING_STATUS: C-03 | CLOSED | Abschnitt 1 dokumentiert nun ausdruecklich, dass das Dokument kein PLAN_ONLY-Artefakt ist, nicht als WORK_PLAN_PATH an plan_handoff.extract_implementation_slices uebergeben wird, nicht von run_task verarbeitet werden soll und seine Slice- und Pfadueberschriften ausschliesslich der manuellen Zusammenarbeit dienen; die Pfadlisten sind ausdruecklich keine maschinellen Handoff-Allowlisten. Damit ist die zweite zulaessige Aufloesung meines Akzeptanztests gewaehlt und fuer spaetere Leser entsteht keine Einspeisungserwartung mehr.

NEW_FINDING: C-04 | BLOCKER | Der Vollstaendigkeitsguard klassifiziert Gategruende und Registryeintraege, waehrend die fachliche Identitaet eines Gates im nicht registrierten Detail-Regelpraefix liegt. Providerfrei gemessen stehen 12 GateReason-Werten und 5 BUILTIN_STOP_RULES elf Detail-Regelpraefixe gegenueber, von denen zehn in keiner Registry gefuehrt werden; allein GateReason.UNEXPECTED_FILE wird an neun Stellen emittiert und traegt mindestens vier fachlich verschiedene Sachverhalte mit unterschiedlicher Resume-Semantik. Der historisch real beobachtete Wachstumsmodus ist genau dieser: 1143fca fuehrte HEAD-DRIFT als neues Inline-Literal unter einem bestehenden Gategrund ein. Der Guard erkennt neue Enumwerte und uebersieht neue Gate-Sachverhalte, behauptet im Akzeptanzkriterium aber Abdeckung ueber jeden Gategrund. | Die Source-Map klassifiziert statt des Gategrundes das Tripel aus GateReason, Detail-Regelpraefix und Gateart, also Policygate oder Nutzergate. Der Guard enumeriert die Praefixe deterministisch aus dem Quelltext, etwa ueber das Literalmuster Grossbuchstabenkennung gefolgt von Leerzeichen, Pipe und Leerzeichen, vereinigt mit gates.BUILTIN_STOP_RULES, und schlaegt fehl, sobald ein Praefix keine Source-Map-Zeile besitzt. Providerfreier Falsifikationstest: Ein zusaetzliches Gate mit dem frei erfundenen Praefix ZZZ-PROBE unter einem bereits klassifizierten GateReason muss den Guard rot machen; bleibt er gruen, leistet er die im Akzeptanzkriterium behauptete Vollstaendigkeit nicht und Slice 1 ist nicht abnahmefaehig.

NEW_FINDING: C-05 | OBSERVATION | Slice 3 nennt fuer den HEAD-Drift das Detailpraefix HEAD-DRIFT, doch der Quelltext kennt zwei verschiedene Driftsachverhalte unter demselben Gategrund: HEAD-DRIFT beim Commit als Nutzergate mit Pfadsatz und resume_step SLICE_COMMIT in src/orchestrator.py:2133 sowie SLICE-HEAD-DRIFT beim Binden der Slice-Grenze als Policygate ohne Pfadsatz und ohne resume_step in src/orchestrator.py:3509. Da die Zeichenkette SLICE-HEAD-DRIFT die Zeichenkette HEAD-DRIFT enthaelt, wuerde eine als Teilstringsuche formulierte Behauptung auch den falschen Sachverhalt akzeptieren, und der zweite Driftfall bleibt in der Szenarienliste unbenannt. | Die Slice-3-Szenarien behaupten das Detailpraefix verankert am Zeichenkettenanfang und nicht als Enthaltenseinspruefung, und die Liste erhaelt ein eigenes Szenario fuer den Boundary-Drift SLICE-HEAD-DRIFT mit Policygate, leerem Pfadsatz und fehlendem resume_step. Providerfreier Falsifikationstest: Ein Szenario, das faelschlich den Boundary-Drift emittiert, muss die Behauptung des Commit-Drift-Szenarios fehlschlagen lassen und umgekehrt; bestehen beide Szenarien mit vertauschten Detailtexten, ist die Praefixbehauptung nicht verankert.

PRE_MORTEM: In drei Monaten scheitert dieses Paket am wahrscheinlichsten daran, dass ein neuer Gate-Sachverhalt unter einem bereits klassifizierten Gategrund eingefuehrt wird, der Vollstaendigkeitsguard gruen bleibt und die Uebergangsmatrix diesen Sachverhalt nie sieht. Der naechste Grenzdefekt zeigte sich dann erneut erst im Produktivlauf, waehrend die gruene Matrix und die dokumentierte Vollstaendigkeitszusage faelschlich gegen den Regressionsverdacht angefuehrt wuerden. Die zweitwahrscheinlichste Ursache ist eine Erosion der literalen Oraclewerte im Betrieb: Wird ein Sollwert nach einer bewussten Verhaltensaenderung aus dem beobachteten Istwert uebernommen, statt fachlich neu hergeleitet zu werden, kehrt die in C-01 geschlossene Zirkularitaet schrittweise und unbemerkt zurueck, ohne dass eine der beiden Mutationsproben das aufdeckt.

PLAN_APPROVAL: NO

STATUS: DONE

---

# Claude-Planreview - Revision 3

## R3.1 Reviewgrenze und Methodik

Drittes manuelles adversariales Planreview der normativen Abschnitte 1 bis 12, außerhalb des Orchestrators, ohne strukturierte JSON-Ausgabe. Kein `run_task`, kein Watcher, kein Bootstrap, kein Providerabruf.

Gelesen: `AGENTS.md`, `CLAUDE.md`, der vollständige Arbeitsplan und beide bisherigen Reviewabschnitte. Die normativen Abschnitte, Produktcode und Tests sind unverändert; dieser Abschnitt ist rein additiv. Keine vollständige Testsuite, kein Commit, kein Branchwechsel, kein Merge, kein Push.

Zentrale Prüfmethode dieser Runde war eine **AST-Emulation des geplanten Guards**: Ich habe den in Phase A Schritt 10 beschriebenen Mechanismus nachgebaut — Aufrufstellen von `await_user_gate` und `await_policy_gate` über alle Module in `src/`, Extraktion des ersten konstanten Segments des `detail`-Arguments einschließlich f-Strings und verketteter Literale, Präfixerkennung nach `^[A-Z][A-Z0-9_-]* \| ` — und gemessen, was er tatsächlich sieht. Erwartete Werte aus dem Reviewauftrag habe ich nicht übernommen.

## R3.2 Selbst festgestellter Repositorystand

- Branch: `master`
- `HEAD`: `de1bd9ea40392c17e47efcc8d243c7de1438ae59`, kurz `de1bd9e`
- Worktree: sauber bis auf dieses untrackte Plandokument
- unverändert gegenüber Revision 1 und 2

## R3.3 C-05 - Getrennte Drift- und Scope-Szenarien

**Geschlossen.**

Slice 3 führt jetzt drei getrennte Szenarien mit vollständig benannten Bindungsfeldern. Ich habe jede behauptete Eigenschaft gegen den Quelltext geprüft:

| Szenario | Gateart | Präfix | Pfadsatz | `resume_step` | Fundstelle |
|---|---|---|---|---|---|
| Commit-HEAD-Drift | Nutzergate | `HEAD-DRIFT` | exakt, fingerprintgebunden | `SLICE_COMMIT` | `workflow.py:2642`, Literal aus `orchestrator.py:2131` |
| Slice-Boundary-Drift | Policygate | `SLICE-HEAD-DRIFT` | leer | keiner | `orchestrator.py:3506` |
| Scopeverletzung | Nutzergate | `UNEXPECTED-PATH` | `affected_paths` | `state.current_step` | `workflow.py:2138` |

Die Behauptung „kein `resume_step`" für den Boundary-Drift ist sogar strukturell garantiert: `WorkflowState.await_policy_gate` besitzt keinen `resume_step`-Parameter, ein Policygate kann ihn also gar nicht tragen. Der Boundary-Drift übergibt zudem kein `paths`, der Pfadsatz bleibt damit leer.

Die Akzeptanzkriterien verlangen ausdrücklich Gleichheit des vollständigen Regelpräfixes vor ` | ` und schließen `in` sowie unankerte Teilstringsuche aus. Die Vertauschungsmutation zwischen `HEAD-DRIFT` und `SLICE-HEAD-DRIFT` muss beide Szenarien rot machen, und der Plan benennt die Teilstringfalle namentlich. Damit ist die in Revision 2 offengelassene Verankerungsanforderung vollständig geschlossen.

## R3.4 C-04 - Source-Map und Vollständigkeitsguard

**Bleibt offen.**

Die Revision beschreibt die Klassifikationseinheit jetzt korrekt als Tripel aus Gategrund, verankertem Detail-Regelpräfix und Gateart, verlangt die Vereinigung mit `gates.BUILTIN_STOP_RULES`, fordert für dynamische Präfixfamilien eine explizite Zeile und untersagt das stille Überspringen nicht extrahierbarer Aufrufstellen. Klausel 11 hält die literale Unabhängigkeit der fachlichen Sollwerte ausdrücklich aufrecht, sodass C-01 unberührt bleibt. Das ist die richtige Richtung.

Meine AST-Emulation zeigt jedoch zwei Blindstellen, die der Plan in seiner jetzigen Fassung nicht abdeckt und über die er dennoch Vollständigkeit zusagt.

### Blindstelle 1 - Der Entdeckungsumfang endet vor vier realen Gateemissionen

Klausel 10 begrenzt die Inventur auf die Aufrufstellen von `await_user_gate` und `await_policy_gate`. Vier weitere Zustandsübergänge konstruieren einen `GateRecord` mit eigenem `reason` und eigenem `detail` unmittelbar selbst und sind über keine der beiden Funktionen erreichbar:

| Fundstelle | Funktion | Gategrund |
|---|---|---|
| `workflow_state.py:1915` | `reopen_legacy_quota_resume_diff_gate` | `QUOTA_RESUME_DIFF` |
| `workflow_state.py:1971` | `record_review_denial` | `ITERATION_LIMIT` |
| `workflow_state.py:2072` | `record_invocation_failure` | `QUOTA` bzw. `INSTANCE_FAILURE` |
| `workflow_state.py:2117` | `await_bootstrap_resume` | `BOOTSTRAP_CHECK` |

Das sind keine theoretischen Pfade. `record_invocation_failure` ist genau der Weg, über den das real beobachtete `instance_failure`-Gate der Structured-Output-Erschöpfung entstand, und `record_review_denial` erzeugt das Iterationslimit-Gate nach einer Reviewverweigerung.

Hinzu kommt ein definitorisches Problem: Das Detail aus `record_review_denial` lautet `review denied by <reviewer> after <n> Codex returns` und trägt **überhaupt kein** kanonisches Regelpräfix. Für ein solches Gate ist die zweite Komponente des Tripels undefiniert; der Plan sagt nicht, wie ein präfixloses Gateidentitätstripel klassifiziert wird. Eine reine Regexextraktion liefert dort schlicht nichts und die Identität kollabiert stillschweigend.

Damit gilt: Die gröbere, in Revision 2 kritisierte Enumsicht hätte diese vier Gategründe wenigstens gelistet. Die feinere Tripelsicht ist fachlich richtiger, hat den Entdeckungsmechanismus aber auf zwei Funktionen verengt und verliert dabei vier Emissionswege.

### Blindstelle 2 - Die Mehrheit der Aufrufstellen liefert kein statisches Präfix

Ergebnis der AST-Emulation über alle Module in `src/`:

| Messgröße | Wert |
|---|---:|
| Aufrufstellen von `await_user_gate` / `await_policy_gate` | 24 |
| davon mit extrahierbarem verankertem Präfix | 10 |
| davon **nicht** extrahierbar | **14** |

Nicht extrahierbar sind Aufrufstellen mit variablem Detail, mit f-Strings, die mit einem Platzhalter beginnen, und mit durchgereichten Detailtexten. Darunter fällt ausgerechnet `workflow.py:2642`, das Commit-HEAD-Drift-Gate: Es übergibt `detail=exc.detail`, während das Literal `HEAD-DRIFT | ` weit entfernt in `orchestrator.py:2131` innerhalb eines `raise WorkflowCommitApprovalRequired` steht.

Klausel 10 fängt das im Prinzip ab, indem eine nicht extrahierbare Aufrufstelle nicht still übersprungen werden darf. Der Plan behandelt diesen Fall aber als Ausnahme, während er mit 14 von 24 Stellen der Regelfall ist. Entscheidend ist die Folge: Eine Source-Map-Zeile, die eine **Aufrufstelle** als dynamisch ausweist, bindet kein **Präfix**. Speist später eine zweite Ausnahme oder ein weiterer Zweig ein neues Präfix in dieselbe Aufrufstelle ein, bleibt der Guard grün. Das ist exakt die Fehlerform aus C-04, verschoben von der Enumebene auf die Aufrufstellenebene.

### Warum die verpflichtende Mutationsprobe das nicht aufdeckt

Klausel 12 fordert die Probe mit dem statischen Literal `ZZZ-PROBE | ` unter einem bereits klassifizierten Gategrund. Meine Emulation zeigt, dass der Guard genau diese Variante zuverlässig findet — sie liegt im extrahierbaren Zehntel. Die Probe wird also bestehen und dem Paket Vollständigkeit bescheinigen, während beide Blindstellen unberührt weiterbestehen. Eine Abnahmeprobe, die nur den bereits abgedeckten Fall prüft, ist der eigentliche Grund, warum dieser Befund offen bleiben muss.

## R3.5 Fortbestand der bisherigen Schließungen

**C-01 unverändert geschlossen.** Das unabhängige Oracle mit literalen Tabellendaten, die Ist-/Soll-Trennung in Schritt 7, beide Monkeypatch-Mutationsproben in Schritt 8 und die Abnahmesperre bei vollständig grüner Matrix stehen unverändert. Klausel 11 stellt ausdrücklich klar, dass die fachlichen Sollwerte nicht aus der neuen Inventur abgeleitet werden; der Guard kann die Zirkularität also nicht durch die Hintertür wieder einführen.

**C-02 unverändert geschlossen.** Durch die Aufspaltung in drei benannte Szenarien mit verankerter Präfixgleichheit ist die ursprüngliche Ununterscheidbarkeit doppelt abgesichert.

**C-03 unverändert geschlossen.** Abschnitt 1 trägt die Abgrenzung vom `PLAN_ONLY`- und `WORK_PLAN_PATH`-Vertrag unverändert.

**Zuschnitt unverändert vertragskonform.** Weiterhin höchstens drei fachlich geschlossene Slices, keine künstliche Dateigrenze, echte test-only Phase A mit unverändertem Produktivcode bis zum Analysecheckpoint, und ein Analysecheckpoint, der Phase B fachlich und pfadseitig begrenzt und einen test-only Slice ausdrücklich zulässt.

## R3.6 Gesamtbewertung

Vier von fünf Befunden sind geschlossen, C-05 in dieser Runde vollständig und gegen den Quelltext verifiziert. Der Plan hat in drei Revisionen erheblich an Präzision gewonnen; die Tripelidentität für Gates ist konzeptionell die richtige Antwort auf C-04.

Offen bleibt allein, dass der Entdeckungsmechanismus des Guards schmaler ist als die Zusage, die die Akzeptanzkriterien geben, und dass die verpflichtende Mutationsprobe genau den Bereich prüft, den der Guard ohnehin beherrscht. Beide Lücken sind rein textlich zu schließen: den Entdeckungsumfang auf jede persistierte Gatetransition ausdehnen, präfixlose Gates ausdrücklich klassifizierbar machen und die Mutationsprobe um eine dynamische und eine direkt konstruierte Variante ergänzen. Danach halte ich den Plan für freigabefähig.

REVIEWER: claude

FINDING_STATUS: C-01 | CLOSED | Unveraendert geschlossen. Das unabhaengige Oracle mit literalen Tabellendaten, die Ist-/Soll-Trennung in Phase A Schritt 7, beide Monkeypatch-Mutationsproben in Schritt 8 und die Abnahmesperre bei vollstaendig gruener Matrix stehen fort. Die neue Guard-Klausel 11 stellt ausdruecklich klar, dass die fachlichen Sollwerte der Matrix nicht aus der Inventur abgeleitet werden, sodass der Vollstaendigkeitsguard die Zirkularitaet nicht wieder einfuehren kann.

FINDING_STATUS: C-02 | CLOSED | Unveraendert geschlossen und durch die Aufspaltung in drei benannte Szenarien mit verankerter Praefixgleichheit zusaetzlich verstaerkt.

FINDING_STATUS: C-03 | CLOSED | Unveraendert geschlossen. Abschnitt 1 grenzt das Dokument weiterhin ausdruecklich vom PLAN_ONLY- und WORK_PLAN_PATH-Vertrag ab.

FINDING_STATUS: C-04 | OPEN | Die Tripelidentitaet aus Gategrund, verankertem Praefix und Gateart ist konzeptionell richtig, der Entdeckungsmechanismus bleibt jedoch schmaler als die Zusage. Erstens begrenzt Klausel 10 die Inventur auf await_user_gate und await_policy_gate, waehrend vier reale Emissionswege einen GateRecord unmittelbar selbst konstruieren: reopen_legacy_quota_resume_diff_gate in workflow_state.py:1915, record_review_denial in :1971, record_invocation_failure in :2072 und await_bootstrap_resume in :2117; record_invocation_failure ist der Weg des real beobachteten instance_failure-Gates und record_review_denial traegt ein Detail voellig ohne kanonisches Regelpraefix, fuer das die zweite Tripelkomponente undefiniert bleibt. Zweitens liefern nach meiner AST-Emulation des geplanten Guards nur 10 von 24 Aufrufstellen ein extrahierbares verankertes Praefix, 14 nicht, darunter das Commit-HEAD-Drift-Gate in workflow.py:2642 mit detail gleich exc.detail und dem Literal in orchestrator.py:2131; eine Source-Map-Zeile, die eine Aufrufstelle als dynamisch ausweist, bindet kein Praefix, sodass ein spaeter durch dieselbe Stelle eingespeistes neues Praefix unentdeckt bleibt. Drittens liegt die verpflichtende Probe ZZZ-PROBE als statisches Literal im bereits beherrschten Zehntel und bescheinigt dem Guard daher Vollstaendigkeit, ohne eine der beiden Blindstellen zu beruehren.

FINDING_STATUS: C-05 | CLOSED | Slice 3 fuehrt drei getrennte Szenarien mit vollstaendig benannten Bindungsfeldern, die ich einzeln gegen den Quelltext geprueft habe: Commit-HEAD-Drift als Nutzergate mit Praefix HEAD-DRIFT, exaktem fingerprintgebundenem Pfadsatz und resume_step SLICE_COMMIT in workflow.py:2642; Slice-Boundary-Drift als Policygate mit Praefix SLICE-HEAD-DRIFT, leerem Pfadsatz und fehlendem resume_step in orchestrator.py:3506, wobei await_policy_gate strukturell gar keinen resume_step-Parameter besitzt; Scopeverletzung als Nutzergate mit Praefix UNEXPECTED-PATH in workflow.py:2138. Die Akzeptanzkriterien verlangen Gleichheit des vollstaendigen Praefixes vor dem Separator, schliessen in-Pruefung und unankerte Regex aus und fordern, dass die Vertauschungsmutation beide Driftszenarien rot macht.

REVIEW_EVIDENCE: geprueft wurden Repositorystand, Guard-Granularitaet per AST-Emulation ueber alle Aufrufstellen von await_user_gate und await_policy_gate, Extrahierbarkeit der Detailpraefixe einschliesslich f-Strings und verketteter Literale, direkte GateRecord-Konstruktionen ausserhalb der beiden Helfer, Signaturen von await_user_gate und await_policy_gate, die drei Driftszenarien gegen ihre Emissionsstellen, Fortbestand der Oracle-Unabhaengigkeit und der Mutationsproben, PLAN_ONLY-Abgrenzung, Slice-Zuschnitt, Analysecheckpoint und Stopbedingungen | groesstes Restrisiko: die Source-Map bleibt fuer jede dynamisch gebildete Detailfamilie eine handgepflegte Zeile, deren fachliche Richtigkeit kein Guard prueft, sondern nur ihre Existenz | realistische Bruchbedingung: sobald ein neues Regelpraefix durch eine bereits als dynamisch klassifizierte Aufrufstelle oder durch einen der vier direkt konstruierenden Zustandsuebergaenge eingefuehrt wird, bleiben Guard und Matrix gruen und der neue Gatesachverhalt wird nie in einem Szenario behauptet

PRE_MORTEM: In drei Monaten scheitert dieses Paket am wahrscheinlichsten daran, dass ein neuer Gatesachverhalt ueber einen der vier direkt konstruierenden Zustandsuebergaenge oder ueber eine bereits als dynamisch abgehakte Aufrufstelle eingefuehrt wird. Guard und Matrix bleiben gruen, die dokumentierte Vollstaendigkeitszusage wird gegen den Regressionsverdacht angefuehrt, und der Defekt zeigt sich erst im Produktivlauf. Die zweitwahrscheinlichste Ursache ist eine Erosion der handgepflegten Source-Map: Da der Guard nur die Existenz einer Zeile prueft und nicht ihre fachliche Richtigkeit, veraltet eine einmal eingetragene Dynamikzeile unbemerkt, waehrend sich der dahinterliegende Praefixbestand weiterentwickelt.

PLAN_APPROVAL: NO

STATUS: DONE

---

# Claude-Planreview - Revision 4

## R4.1 Reviewgrenze und Methodik

Viertes manuelles adversariales Planreview der normativen Abschnitte 1 bis 12, außerhalb des Orchestrators, ohne strukturierte JSON-Ausgabe. Kein `run_task`, kein Watcher, kein Bootstrap, kein Providerabruf.

Gelesen: `AGENTS.md`, `CLAUDE.md`, der vollständige Arbeitsplan und alle drei bisherigen Reviewabschnitte. Die normativen Abschnitte, Produktcode und Tests sind unverändert; dieser Abschnitt ist rein additiv. Keine vollständige Testsuite, kein Commit, kein Branchwechsel, kein Merge, kein Push.

Prüfmethode dieser Runde war eine **erweiterte AST-Emulation gegen die neue Guard-Definition**: vollständige Enumeration aller `GateRecord`-Konstruktionen und `replace(gate=)`-Ersetzungen, aller präfixartigen Stringliterale nach `^[A-Z][A-Z0-9_-]* \| ` im gesamten produktiven Quelltext, und aller f-Strings, die mit einem Platzhalter unmittelbar vor dem kanonischen Separator beginnen. Anschließend habe ich für jede so gefundene Stelle geprüft, ob ihre Regelidentität über einen der vier im Plan benannten Entdeckungswege auffindbar ist. Erwartete Werte aus dem Reviewauftrag habe ich nicht übernommen.

## R4.2 Selbst festgestellter Repositorystand

- Branch: `master`
- `HEAD`: `de1bd9ea40392c17e47efcc8d243c7de1438ae59`, kurz `de1bd9e`
- Worktree: sauber bis auf dieses untrackte Plandokument
- unverändert gegenüber den Revisionen 1 bis 3

## R4.3 C-04 - Vollständige Gateemissionsinventur

**Geschlossen.**

Beide in Revision 3 gemessenen Blindstellen sind geschlossen, und die Revision geht an zwei Stellen über meine Forderung hinaus.

### Der Emissionssuchraum ist nachweislich vollständig

Klausel 10 erweitert die Inventur von den zwei Komforthelfern auf jede persistierte Gatetransition und benennt die vier Zustandsübergänge einzeln. Meine AST-Enumeration bestätigt, dass dieser Suchraum geschlossen ist:

| Kategorie | Anzahl |
|---|---:|
| `GateRecord`-Konstruktionen gesamt | 13 |
| davon mit `reason`/`detail` | 6 |
| davon leer, also gatelöschend | 7 |
| `replace(gate=)`-Ersetzungen | 11 |

Sämtliche 24 Stellen liegen in `workflow_state.py`; außerhalb dieses Moduls existiert kein weiterer Weg, einen Gatezustand zu persistieren. Die sechs inhaltstragenden Konstruktionen sind genau die in Revision 3 benannten: `await_user_gate` (1730), `await_policy_gate` (1775), `reopen_legacy_quota_resume_diff_gate` (1915), `record_review_denial` (1971), `record_invocation_failure` (2072) und `await_bootstrap_resume` (2117). Der Plan nennt die vier zuvor fehlenden ausdrücklich. Eine direkte Konstruktion kann damit nicht mehr aus dem Suchraum fallen.

### Die dynamische Weiterreichung ist nicht mehr abhakbar

Klausel 11 macht upstream entstehende Präfixe inventarisierbar und nennt `HEAD-DRIFT` als Beispiel: Das Literal entsteht in einer Exception und erreicht die Emissionsstelle nur über `detail=exc.detail`. Klausel 12 verbietet ausdrücklich, eine solche Stelle bloß als dynamisch zu kennzeichnen, und verlangt die Bindung einer exakten erlaubten Menge upstream erzeugter Regelidentitäten. Damit ist mein Einwand aus Revision 3 beantwortet, dass eine Zeile über eine Aufrufstelle kein Präfix bindet.

### Präfixlose Gates haben eine definierte Identität

Klausel 13 gibt jedem präfixlosen Gate eine eigene `PREFIXLESS:<familie>`-Zeile mit verankerter `fullmatch`-Detailgrammatik und genau einer Gateart- und Gategrundbindung; `record_review_denial` erhält namentlich `PREFIXLESS:REVIEW-DENIAL`. Die in Revision 3 benannte undefinierte zweite Tripelkomponente ist damit geschlossen. Klausel 9 verbietet zusätzlich eine leere Regelidentität, Teilstringsuche und eine pauschale `dynamic`-Kategorie.

### Die Abnahmeprobe prüft nicht mehr nur das ohnehin Abgedeckte

Mein schärfster Einwand aus Revision 3 war, dass die einzige verpflichtende Mutation im bereits beherrschten Zehntel lag. Klausel 15 führt nun drei getrennte Proben, eine je Entdeckungsweg: `ZZZ-PROBE` statisch, `ZZZ-DYNAMIC` upstream über eine variable Stelle, `ZZZ-DIRECT` über einen zusätzlichen direkten `GateRecord`-Pfad. Jede muss unabhängig rot werden, und bleibt eine grün, ist Slice 1 nicht abnahmefähig. Die drei Proben decken die drei Wege disjunkt ab; keine kann die Blindstelle einer anderen verdecken.

### Über die Forderung hinaus

Klausel 14 macht die Source-Map bidirektional: Jede Zeile bindet ihre Emissions- und Producerstellen, und umgekehrt muss jede Zeile durch mindestens einen Matrixfall tatsächlich emittiert werden; verwaiste oder nur nominell vorhandene Zeilen sind Fehler. Das adressiert das Restrisiko aus Revision 3 — die handgepflegte Zeile, deren Existenz geprüft wird, aber nicht ihre Wirklichkeit. Klausel 14 hält zugleich fest, dass die fachlichen Sollwerte literal bleiben und nicht aus der Inventur abgeleitet werden, sodass C-01 unberührt bleibt.

## R4.4 Verbliebene Beobachtung zur automatischen Auffindbarkeit

Meine Emulation der vier Entdeckungswege ergab folgendes Bild für die Regelidentitäten:

| Herkunft | Anzahl | über welchen Weg auffindbar |
|---|---:|---|
| präfixartige Literale nach `^[A-Z][A-Z0-9_-]* \| ` | 11 | Literalsuche |
| f-String mit führendem Platzhalter, Registrykonstante | 7 | `gates.BUILTIN_STOP_RULES` |
| f-String mit führendem Platzhalter, Stop-Rule-ID | 3 | Registry bzw. `STOP_RULE_ID_PATTERN` |
| f-String mit führendem Platzhalter, gatefremde Renderhilfe | 2 | Klausel 11, Begründungszeile |
| f-String mit führendem Platzhalter, freie lokale Variable | **1** | **keiner der vier Wege** |

Der Einzelfall ist `workflow.py:2156`. Die Stelle emittiert über `await_bootstrap_resume` mit `detail=f"{code} | {detail}"`, wobei `code` acht Zeilen zuvor lokal auf `"PROVIDER-INPUT-BUDGET"` gesetzt wird. Diese Kennung enthält keinen kanonischen Separator, taucht deshalb in der Literalsuche nicht auf, und sie steht nicht in `gates.BUILTIN_STOP_RULES`.

Die Emissionsstelle selbst ist erfasst, weil Klausel 10 `await_bootstrap_resume` namentlich nennt, und Klausel 12 verlangt für ihre variable Detailbildung die Bindung der exakten Producermenge. Der Plan deckt den Fall also. Auffindbar wird die Identität aber nur, wenn der Guard die Zuweisungen hinter der Detailvariablen tatsächlich per AST verfolgt, statt sich auf eine handgepflegte Liste zu stützen. Genau diese Unterscheidung entscheidet, ob Klausel 12 hält. Ich führe das als C-06 mit einem falsifizierbaren Akzeptanztest, nicht als Blocker, weil der Plan das geforderte Verhalten bereits verlangt und die dynamische Mutationsprobe es prüft.

## R4.5 Fortbestand der übrigen Schließungen

**C-01 geschlossen.** Unabhängiges Oracle, literale Tabellendaten, Ist-/Soll-Trennung, beide Monkeypatch-Proben und die Abnahmesperre stehen unverändert im normativen Teil. Klausel 14 bekräftigt die Trennung zwischen Inventur und Sollwerten.

**C-02 und C-05 geschlossen.** Die drei vertauschungssicheren Szenarien für Commit-HEAD-Drift, Slice-Boundary-Drift und Scopeverletzung mit verankerter Präfixgleichheit und dem ausdrücklichen Ausschluss von `in` und unankerter Suche bestehen fort.

**C-03 geschlossen.** Abschnitt 1 grenzt das Dokument unverändert vom `PLAN_ONLY`- und `WORK_PLAN_PATH`-Vertrag ab.

**Zuschnitt unverändert vertragskonform.** Höchstens drei fachlich geschlossene Slices, keine künstliche Dateigrenze, echte test-only Phase A mit unverändertem Produktivcode bis zum Analysecheckpoint, begrenzender Analysecheckpoint mit ausdrücklich zulässigem test-only Ausgang.

## R4.6 Gesamtbewertung

Alle fünf Befunde aus vier Reviewrunden sind geschlossen. Der Plan beschreibt die Gateidentität jetzt in der fachlich richtigen Granularität, deckt einen nachweislich vollständigen Emissionssuchraum ab, macht dynamische und präfixlose Fälle klassifizierungspflichtig statt abhakbar, bindet die Source-Map bidirektional an tatsächlich emittierte Matrixfälle und prüft jeden der drei Entdeckungswege mit einer eigenen Negativprobe.

Es verbleibt kein Claude-Blocker. Die eine neue Beobachtung betrifft die Implementierungstiefe einer bereits geforderten Eigenschaft und ist innerhalb von Slice 1 zu erledigen. Ich gebe den Plan frei.

REVIEWER: claude

FINDING_STATUS: C-01 | CLOSED | Unveraendert geschlossen. Unabhaengiges Oracle mit literalen Tabellendaten, Ist-/Soll-Trennung in Phase A Schritt 7, beide Monkeypatch-Mutationsproben und die Abnahmesperre bei vollstaendig gruener Matrix stehen im normativen Teil fort; die neue Klausel 14 bekraeftigt ausdruecklich, dass die fachlichen Sollwerte literal bleiben und nicht aus der Gateinventur abgeleitet werden.

FINDING_STATUS: C-02 | CLOSED | Unveraendert geschlossen. Die drei getrennten Szenarien mit verankerter Praefixgleichheit bestehen fort.

FINDING_STATUS: C-03 | CLOSED | Unveraendert geschlossen. Abschnitt 1 grenzt das Dokument weiterhin ausdruecklich vom PLAN_ONLY- und WORK_PLAN_PATH-Vertrag ab.

FINDING_STATUS: C-04 | CLOSED | Beide in Revision 3 gemessenen Blindstellen sind geschlossen. Klausel 10 erweitert den Suchraum auf jede persistierte Gatetransition und benennt reopen_legacy_quota_resume_diff_gate, record_review_denial, record_invocation_failure und await_bootstrap_resume einzeln; meine AST-Enumeration bestaetigt den Suchraum als geschlossen mit 13 GateRecord-Konstruktionen, davon 6 inhaltstragend und 7 gateloeschend, sowie 11 replace-gate-Ersetzungen, saemtlich in workflow_state.py. Klausel 11 macht upstream in einer Exception entstehende Praefixe wie HEAD-DRIFT inventarisierbar, Klausel 12 verbietet das blosse Abhaken einer Stelle als dynamisch und verlangt die exakte erlaubte Producermenge, Klausel 13 gibt praefixlosen Gates eine PREFIXLESS-Familie mit verankerter fullmatch-Grammatik und nennt PREFIXLESS:REVIEW-DENIAL fuer record_review_denial. Klausel 15 fuehrt drei disjunkte Mutationsproben je Entdeckungsweg, die unabhaengig rot werden muessen, womit mein Einwand entfaellt, die Probe pruefe nur den ohnehin abgedeckten Bereich. Klausel 14 macht die Source-Map zusaetzlich bidirektional und schliesst verwaiste oder nur nominelle Zeilen aus.

FINDING_STATUS: C-05 | CLOSED | Unveraendert geschlossen. Commit-HEAD-Drift, Slice-Boundary-Drift und Scopeverletzung bleiben getrennt, vertauschungssicher und mit ausdruecklichem Ausschluss von in-Pruefung und unankerter Teilstringsuche behauptet.

NEW_FINDING: C-06 | OBSERVATION | Die Regelidentitaet einer Gateemission kann aus einer freien lokalen Variablen stammen, die keiner der vier automatischen Entdeckungswege sieht. Konkreter Fall: workflow.py:2156 emittiert ueber await_bootstrap_resume mit detail gleich f-String aus code und detail, wobei code acht Zeilen zuvor lokal auf PROVIDER-INPUT-BUDGET gesetzt wird; diese Kennung enthaelt keinen kanonischen Separator und faellt daher aus der Literalsuche, und sie steht nicht in gates.BUILTIN_STOP_RULES. Die Emissionsstelle ist zwar erfasst, weil Klausel 10 await_bootstrap_resume namentlich nennt, und Klausel 12 verlangt die exakte Producermenge; auffindbar wird die Identitaet aber nur, wenn der Guard die Zuweisungen hinter der Detailvariablen per AST verfolgt statt sich auf eine handgepflegte Liste zu stuetzen. | Die Inventur des Guards muss PROVIDER-INPUT-BUDGET als Regelidentitaet der Emissionsstelle workflow.py:2156 ausweisen, ohne dass diese Kennung in einer Registry oder als praefixartiges Literal vorliegt. Providerfreier Falsifikationstest in zwei Teilen: erstens muss die erzeugte Inventur diese Identitaet enthalten, und laesst sie sich ohne sie vollstaendig erzeugen, ist die Producerverfolgung nicht implementiert; zweitens muss ein zusaetzlicher Zuweisungszweig code gleich ZZZ-DYNAMIC vor derselben Emissionsstelle den Guard rot machen, bis das neue Tripel samt Producer- und Emissionsstelle klassifiziert und durch einen Matrixfall emittiert ist.

PRE_MORTEM: In drei Monaten scheitert dieses Paket am wahrscheinlichsten daran, dass Klausel 12 als handgepflegte Producerliste statt als AST-gestuetzte Verfolgung umgesetzt wird. Die drei Mutationsproben blieben dann formal gruen, weil ihre Kennungen bei der Einfuehrung mit eingetragen wurden, waehrend eine spaeter hinzugefuegte Detailvariante an einer bereits klassifizierten Stelle unbemerkt bliebe. Die zweitwahrscheinlichste Ursache ist eine Erosion der bidirektionalen Bindung aus Klausel 14: Wird ein Matrixfall spaeter umgebaut oder entfernt, verwaist die zugehoerige Source-Map-Zeile, und wenn die Verwaisungspruefung dabei nachlaessig gelockert wird, verliert das Inventar seine Aussagekraft, ohne dass eine der drei Negativproben anschlaegt.

PLAN_APPROVAL: YES

STATUS: DONE
