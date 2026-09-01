# Codex-Gegenanalyse: Woher die Orchestrator-Instabilität kommt

Datum: 29. August 2026

Bewertete Vorlage: `inbox/backlog/00-manuell-zustandsdivergenz-und-findinghoheit.md`

## Kurzurteil

Claudes Hauptdiagnose ist richtig: Die gegenwärtige Instabilität liegt nicht
gleichmäßig im gesamten Orchestrator und auch nicht primär bei Codex, Claude
oder den Providerprozessen. Sie sitzt an der Transaktions- und Hoheitsgrenze
zwischen der append-only Recordkette und dem parallel persistierten
`state-v3`-Mirror. Findings treffen diese Schwachstelle am häufigsten, weil
derselbe fachliche Sachverhalt gleichzeitig als vollständiges Ledger, aktueller
Open-Satz, Work-Unit-Attribution, importierter Snapshot und Request-Teilmenge
vorkommt.

Die Abbrüche werden deshalb durch weitere lokale Ausnahmebedingungen nicht
dauerhaft aufhören. Sie können aber aufhören, wenn der Umbau drei Dinge wirklich
abschließt:

1. genau einen normativen Zustandsreduzierer aus der Recordkette,
2. eine atomare beziehungsweise vollständig replaybare Übergangssemantik ohne
   handgeschriebene Record-ahead-Sonderfälle und
3. eine versionierte Semantik für Resumes, damit ein alter Lauf nicht
   unbemerkt unter geänderten Regeln derselben Protokollversion weiterläuft.

Claudes Runden 1 bis 4 zeigen in die richtige Richtung. Runde 1 braucht jedoch
eine Dreiwegeklassifikation statt nur „deterministisch oder transient“, und die
in Runde 4 genannte Alternative eines generischen Mirrorvergleichs beseitigt
allein noch nicht das zugrunde liegende Transaktionsproblem.

## Was die Lauf- und Commitdaten tatsächlich zeigen

### Fixdichte

Auf `HEAD` liegen 245 Commits. Mit der reproduzierbaren Regel
„Betreff beginnt mit `fix:`, `fix(...)` oder `hotfix:`“ sind 66 davon Fixes
beziehungsweise Hotfixes, also 26,9 Prozent. Claudes Angabe 66 von 245 und
rund 27 Prozent ist damit bestätigt.

Die Tagesaussage benötigt eine klarere Zählregel:

- 23.08.: 17 echte `fix...`-Commits,
- 26.08.: 7 echte `fix...`-Commits,
- 29.08.: 5 echte `fix...`-Commits; auf 7 kommt man nur, wenn zusätzlich zwei
  Dokumentationscommits mit „hotfix“ im Betreff mitgezählt werden.

Das ändert die Diagnose nicht, sollte aber vor späteren Trendvergleichen
vereinheitlicht werden.

Unter den 66 so gezählten Fix-/Hotfix-Commits wird `src/workflow.py` in 29 und
`src/orchestrator.py` in 27 Commits berührt. Die von Claude genannte
Konzentration auf diese beiden Dateien ist damit klar bestätigt; der exakt
reproduzierte gemeinsame Wert ist 56 statt 57. Auch das ist nur eine kleine
Zählabweichung, keine Widerlegung des Befunds.

### Poison-Reports

Es gibt neun `*.poison.error.json`-Reports, aber nicht neun unabhängige Läufe.
Vier Reports gehören zum selben Lauf
`watch-20260828-113929.322399Z-a1f9abaf4ade`. Insgesamt sind es fünf bekannte
Run-IDs plus ein Report ohne gespeicherte Run-ID, also sechs Laufkontexte.
Die neun Reports sind trotzdem neun reale Bedienabbrüche und zeigen besonders
gut die Hotfixkaskade innerhalb eines bereits laufenden Workflows.

Die unmittelbar aus den Reports reproduzierbare Einteilung ist:

| Gruppe | Anzahl | Beispiele |
|---|---:|---|
| Direkte Finding-/Mirror-Divergenz | 2 | `authoritative finding replay differs from the state-v3 mirror`; `latest work-unit finding state differs from state-v3` |
| Native Record-ahead-/Recovery-Bindung | 3 | falsche `request_id`; mehrere `agent-result`-Records; Request weicht vom Recovery-Artefakt ab |
| Fehlende strukturierte Planprojektion beim Finding-Handoff | 1 | Plan-Commit fehlt im akzeptierten Replay |
| Fehlende Final-Attestierung | 1 | Finalreview verlangt eine vollständige grüne Attestierung |
| Eingabe-/Dokumentvertrag | 2 | unzulässiger Branchname; fehlende verwaltete Auditsektionen |

Damit ist Claudes Aussage „sechs Zustandsdivergenzen“ in der Richtung
vertretbar, wenn auch Planprojektion, Attestierung und Recovery-Artefakte unter
einen weiten Persistenzbegriff fallen. Die genauere Behauptung „dreimal
Findingzustand, dreimal Record-ahead“ lässt sich aus den Reports ohne eine
zusätzliche, offengelegte Klassifikationsregel nicht exakt reproduzieren. Direkt
belegt sind zwei Finding-/Mirror-Fälle und drei native Recovery-Fälle. Die zwei
fachfremden Vertragsfehler sollten in der Ursachenstatistik getrennt bleiben.

### Hotfix erzeugt Folge-Hotfix

Dieser Befund ist vollständig bestätigt. Commit `924a554` verschob die
Findingprüfung und führte einen neuen Latest-Record-Vergleich ein. Commit
`745a2fa` musste genau dort die verlorene Bedingung
`finding_import_record_id is not None` ergänzen. Der zweite Fix umfasst nur eine
Produktivcodezeile, benötigt aber 142 neue Testzeilen. Das ist ein typisches
Signal dafür, dass der lokale Vergleich nicht aus einer zentralen Semantik
abgeleitet war.

## Wo die Instabilität konkret sitzt

### 1. Nichtatomarer Dual-Write mit Record-ahead-Fenstern

`OrchestratorDriver.checkpoint()` schreibt zuerst die strukturierte Baseline und
die Auditprojektion und speichert erst danach `state.json` und den
State-Checkpoint (`src/orchestrator.py`, derzeit Zeilen 2628 bis 2672). Ein
Abbruch zwischen diesen Schritten hinterlässt definitionsgemäß Records vor dem
Mirror. Das ist als Reihenfolge nachvollziehbar, aber nur sicher, wenn der
Mirror vollständig aus dem Recordpräfix rekonstruiert werden kann.

Stattdessen erkennt `artifact_migration.py` einzelne zulässige Zwischenfenster
über Funktionen wie `_recoverable_pending_correction_record`,
`_recoverable_pending_slice_denial_record` und
`_recoverable_pending_review_finding_gap`. Jeder neue Übergang benötigt damit
eine weitere lokale Ausnahme samt Reihenfolge-, Rundennummer-, Finding- und
Attestierungsbedingungen. Genau hier wächst die Zahl der Hotfixes kombinatorisch.

### 2. Formale und implementierte Autorität widersprechen sich

Der aktive Vertrag erklärt die Recordkette zur technischen Quelle der Wahrheit
und `state.json` zum operativen Mirror. Der Kopf von `src/artifact_bridge.py`
beschreibt dagegen weiterhin `State-v3 remains authoritative until the explicit
cutover`, und `src/audit_trail.py` spricht ausdrücklich vom fortbestehenden
Dual-Write. Der Code verhält sich entsprechend hybrid: Er replayt Records,
vergleicht ihre Fakten aber anschließend wieder mit separat hergeleiteten
State-Fakten.

`artifact_migration.py` enthält derzeit zwölf wörtliche Meldungen
`differs from state-v3` sowie weitere Setvergleiche für Gates, Findings,
Attestierungen, Quota, Retries, Bootstrapchecks, Commitbindungen und Abschluss.
Claudes Formulierung „rund zehn“ ist daher eher konservativ.

Das Problem sind nicht die Prüfungen an sich. Fail-closed ist richtig. Das
Problem ist, dass die Prüfungen zwei unabhängig fortgeschriebene Darstellungen
vergleichen, statt eine autoritative Darstellung zu reduzieren und nur die
Integrität eines davon abgeleiteten Caches zu prüfen.

### 3. Der Findingbegriff ist in mehrere, nicht gleichbedeutende Mengen zerlegt

Der aktuelle Code benötigt mindestens folgende Sichten:

- das vollständige laufübergreifende Finding-Ledger,
- die aktuell offenen Findings,
- die einer Correction-Work-Unit unveränderlich zugeordneten Findings,
- den PLAN_ONLY-Import-Snapshot der ersten Implementierungs-Work-Unit,
- die einem Agentenrequest angebotene Finding-Teilmenge und
- die vom jeweiligen Reviewer beherrschten Statusübergänge.

Diese Sichten sind fachlich sinnvoll, werden aber an mehreren Stellen aus
`runtime_history`, `work_unit.open_findings`, Recordtransitions und
Requestkontext neu zusammengesetzt. Der Kommentar in `_finding_statuses()` muss
bereits erklären, dass `open_findings` gerade nicht der lebende Open-Mirror ist.
Das ist der präziseste Ort der Instabilität: Nicht „Findings allgemein“, sondern
die fehlende kanonische Abbildung zwischen Ledger, Attribution, Snapshot und
Requestprojektion.

### 4. Resumes wechseln die Semantik innerhalb derselben Protokollbindung

Claude nennt zutreffend, dass ein neuer Prozess alte Records mit geändertem
Code liest, bezeichnet die Inkonsistenz aber als zwangsläufig. Zwangsläufig ist
nur der Versionswechsel; die Inkonsistenz entsteht, weil die geänderten
Reduktions- und Recoveryregeln weiterhin unter derselben Bindung
`structured-v2` laufen.

Die Hotfixfolge `924a554` zu `745a2fa` belegt das praktisch: Derselbe aktive
Lauf wurde nacheinander von unterschiedlichen Interpretationen des gleichen
Work-Unit-Records gelesen. Eine stabile Architektur benötigt deshalb entweder
einen pro Lauf gebundenen Reducer-/Semantikstand oder eine explizite,
versionierte und getestete Migration. Nur „Recordkette ist autoritativ“ genügt
nicht, wenn deren Auswertung während des Laufs ihre Bedeutung ändert.

### 5. Der Watcher verschärft einen Halt zu drei Abbrüchen und Poison

Der Watcher behandelt jede `TECHNICAL_FAILURE` identisch: Retryzähler erhöhen,
dreimal erneut versuchen, dann Task, Attempt-Sidecar und Watch-Identität in den
Poison-Pfad verschieben. `ArtifactResumeError` ist am äußeren CLI-Rand bereits
ein resumierbarer Halt. Viele innere Persistenzfehler werden jedoch von
`checkpoint()` oder `_persist_structured()` in `WorkflowExecutionError`
eingepackt und landen deshalb wieder als technische Retryfehler im Watcher.

Die Retrypolitik ist somit nicht die Primärursache der Divergenz, aber ein
wesentlicher Verstärker der wahrgenommenen Instabilität und der manuellen
Wiederherstellungsarbeit.

## Bewertung von Claudes vier Runden

### Runde 1 – Fehlerklassen trennen: zustimmen, aber auf drei Klassen erweitern

Die Maßnahme ist als Sofortschutz richtig. Eine binäre Trennung in
„deterministisch“ und „transient“ reicht jedoch nicht:

1. **Transient:** Quota, Netz, vorübergehender Prozessfehler; begrenzt erneut
   versuchen.
2. **Resumierbarer Eingriffshalt:** Record/Mirror-, Schema-, Fingerprint- oder
   Recoverykonflikt eines begonnenen Laufs; Task und Watch-Identität erhalten,
   Queue mit typisiertem Diagnosecode anhalten.
3. **Terminale Eingabeablehnung:** etwa ein unzulässiger Branchname vor
   Laufbeginn; einmalig als `rejected`/fehlgeschlagen ablegen, nicht dreimal
   ausführen und nicht die gesamte Queue auf einen scheinbar resumierbaren Lauf
   warten lassen.

Die Klassifikation muss an typisierten Fehlern beziehungsweise Resultaten
hängen, nicht an Textsuche und auch nicht erst nach dem zweiten identischen
Versuch. Wichtig ist außerdem, verschachtelte Ursachen nicht pauschal in
`WorkflowExecutionError` zu verlieren.

Bewertung: **hohe Priorität, mit dieser Präzisierung umsetzen.**

### Runde 2 – Divergenzkanten inventarisieren: uneingeschränkt zustimmen

Die reine Leseinventur ist notwendig. Sie sollte nicht nur jede
Vergleichsstelle nennen, sondern pro Übergang zusätzlich erfassen:

- autoritativer Eingaberecord und erwarteter Folgerecord,
- davon abgeleitete State-/Cachefelder,
- tatsächliche Schreibreihenfolge und mögliche Crashpunkte,
- zulässige Wiederholung/Idempotenz,
- heute vorhandener `_recoverable_*`-Sonderfall,
- verwendete Semantik-/Protokollversion und
- externe Side Effects, die vor dem nächsten dauerhaften Zustand möglich sind.

Das Ergebnis sollte eine Übergangsmatrix sein. Nur eine Liste der
Fehlermeldungen wäre zu wenig.

Bewertung: **unverändert richtig; Abnahme erweitern.**

### Runde 3 – Findingzustand normativ definieren: stärkster fachlicher Hebel

Der Vorschlag ist richtig, wenn „eine Stelle“ als reiner, deterministischer
Reducer verstanden wird: Recordpräfix hinein, kanonisches Finding-Ledger plus
explizite Projektionen hinaus. Die Projektionen für Open-Satz, Attribution,
Import und Request-Teilmenge müssen benannt bleiben; sie dürfen nicht erneut in
verschiedenen Modulen hergeleitet werden.

Die historischen Fälle sollten als versioniertes Regression-Corpus erhalten
werden. Die genannte Zahl zwölf braucht im Arbeitsartefakt eine konkrete Liste
aus Commit, Auslöser, Übergang und erwarteter Projektion. Zusätzlich sind
Sequenz-/Property-Tests wichtig, weil Einzelfalltests die Kombination aus
mehreren Slices, denied Reviews, Carried Observations, Corrections und Resume
nur ausschnittsweise abdecken.

Bewertung: **voll zustimmen; mit Reducer- und Corpusvertrag konkretisieren.**

### Runde 4 – Dual-Write beenden: Ziel richtig, Alternative nicht gleichwertig

Die bevorzugte Variante ist richtig: Die Recordkette wird autoritativ und der
laufende Zustand wird daraus reduziert. `state.json` darf als schneller Cache
oder Betriebs-Snapshot bleiben, sollte dann aber mindestens an Record-Head,
Reducer-Version und Projektdigest gebunden und jederzeit deterministisch
rekonstruierbar sein.

Die vorgeschlagene Alternative „Mirror behalten, aber generisch vergleichen“
beseitigt zwar zehn handgeschriebene Vergleiche, nicht aber zwei getrennte
Schreibvorgänge und das Record-ahead-Fenster. Sie ist nur dann tragfähig, wenn
der Mirror nicht mehr unabhängig fortgeschrieben, sondern ausschließlich aus
dem Recordpräfix erzeugt wird. Ein generischer Vergleich kann danach die
Cacheintegrität sichern; er ist kein Ersatz für die Autoritätskonsolidierung.

Vor dem Cutover muss Runde 2 zeigen, welche heute im State vorhandenen Fakten
noch keinen Record besitzen. Falls sich die Bedeutung bestehender Records oder
des Reducers ändert, sollte eine neue, explizite Semantik-/Protokollversion
ernsthaft vorgesehen werden. Das Nicht-Ziel „keine Protokollversion anheben“
darf die korrekte Cutoverentscheidung nicht vorwegnehmen.

Bewertung: **Ziel voll bestätigen; Alternative und Versionsvorgabe verschärfen.**

## Bewertung des manuellen Arbeitsmodells

Die manuelle Abwicklung ist für diesen Umbau vernünftig. Der aktuelle
Orchestrator hat den betroffenen Lauf bereits an genau der zu ändernden Grenze
abgebrochen, und Hotfixes während eines offenen Laufs haben mehrfach neue
Interpretationsprobleme erzeugt. Kleine, einzeln reviewte und vom Menschen
committete Runden senken das Bootstraprisiko.

Nicht ganz richtig ist nur die absolute Aussage, ein Resume mit geändertem Code
sei „zwangsläufig inkonsistent“. Mit versionierten Reducern und expliziten
Migrationen kann es konsistent sein. Der heutige Orchestrator bietet diese
Garantie für die betreffenden Übergänge aber nicht; für den aktuellen Stand ist
die manuelle Konsequenz daher richtig.

Während der vier Runden sollte Featurearbeit an
`artifact_migration.py`, `artifact_bridge.py`, `workflow.py` und
`orchestrator.py` eingefroren werden. Sonst wird die Inventur bereits während
des Umbaus wieder veraltet.

## Zusätzliche Abnahmekriterien für echte Stabilität

Der Umbau sollte nicht allein an einer grünen Testsuite oder sinkender
Poisonzahl als abgeschlossen gelten. Belastbare Abschlusskriterien sind:

- Jeder persistierte Fakt besitzt genau eine autoritative Recorddarstellung
  oder ist ausdrücklich als rein abgeleiteter Cache benannt.
- Für das neue Protokoll benötigt kein normaler Crashpunkt einen
  übergangsspezifischen `_recoverable_*`-Sonderfall.
- Crash-Injection vor und nach jedem dauerhaften Schreib- und Side-Effect-Rand
  führt nach Resume zum gleichen kanonischen Zustand.
- Das Finding-Regression-Corpus deckt alle benannten historischen Defekte ab
  und testet zusätzlich kombinierte Übergangssequenzen.
- Kein deterministischer Vertrags-/Persistenzfehler erhöht den transienten
  Retryzähler.
- Ein alter Lauf kann nur mit seiner gebundenen Reducer-Semantik oder über eine
  explizit getestete Migration fortgesetzt werden.
- Mindestens ein providerfreier Langlauf und anschließend ein kleiner echter
  Canary durchlaufen PLAN_ONLY, Handoff, denied Review, Correction, carried
  Observation, Finalreview und Resume ohne manuellen Stateeingriff.

## Antwort auf „Hört das denn nie auf?“

Mit der bisherigen Strategie aus lokalen Guards und Recovery-Ausnahmen:
wahrscheinlich nicht. Jeder neue Übergang erweitert die Zahl der Kombinationen,
die Recordkette und Mirror gleichzeitig richtig interpretieren müssen.

Mit der von Claude vorgeschlagenen Konsolidierung, ergänzt um typisierte
Dreiwege-Fehlerbehandlung und versionierte Reducer-Semantik: ja. Die
Instabilität ist ernst, aber räumlich klar begrenzt. Sie ist kein Beleg dafür,
dass der gesamte Orchestrator unrettbar ist; sie ist ein Beleg dafür, dass der
begonnene Structured-v2-Cutover technisch noch nicht abgeschlossen wurde.
