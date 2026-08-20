# Phase 2 – Erkenntnisse aus Arbeitspaket 1 und Stabilisierungspaket 1.1

Dieses Dokument sammelt die während Arbeitspaket 1 erkannten
Orchestratorfälle, die nicht im abgeschlossenen Arbeitsauftrag repariert
werden sollten. Es ist die verbindliche Detailgrundlage für das begrenzte
Stabilisierungspaket 1.1 vor den weiteren nativen JSON-Arbeitspaketen.

Stand: 2026-08-20

## P2-FU-001 – Vollständiger Antigravity-Reviewvertrag trotz Non-Success verworfen

**Status:** im Hotfix umgesetzt; 813/813 Tests bestanden  
**Priorität:** hoch  
**Beobachtet in:**
`watch-20260819-100335.225490Z-44aba5d6579a`, Arbeitseinheit 1,
`antigravity_plan_review`

### Beobachtung

Antigravity lieferte in `provider_text` einen fachlich und formal vollständig
erscheinenden Reviewvertrag:

```text
REVIEWER: antigravity
REVIEW_EVIDENCE: ... | ... | ...
PRE_MORTEM: ...
PLAN_APPROVAL: YES
STATUS: DONE
```

Der Adapter erhielt jedoch einen JSON-Umschlag, dessen `status` nicht exakt
`SUCCESS` war. `AntigravityAdapter.extract_output()` warf deshalb einen
`AgentOutputError`. Die Fehlerklassifikation wurde mangels spezifischer
Metadaten zu `runtime`; der Workflow persistierte `awaiting_resume` und
verwarf den positiven Reviewentscheid.

Der bestehende Recovery-Pfad
`_recover_completed_reviewer_contract()` akzeptiert ausschließlich
`AgentFailureKind.AUTH`. Ein vollständiger rollenrichtiger Vertrag aus einem
`runtime`-Fehler wird daher nicht dem normalen Reviewvalidator übergeben.

### Auswirkung

- unnötiger manueller Resume;
- erneuter Antigravity-Aufruf trotz bereits vorhandener vollständiger Antwort;
- zusätzlicher Zeit- und Quota-Verbrauch;
- der positive Reviewentscheid ist nur noch als Fehlertext, nicht als
  Reviewereignis persistiert.

### Gewünschtes Verhalten

Eine Provider-Non-Success-Hülle darf nicht allein wegen vollständig wirkender
Textmarker in einen Erfolg umgedeutet werden. Das würde die C-22-Schutzgrenze
umgehen: Quota-, Netzwerk-, Runtime- oder fremde Fehlertexte können zufällig
einen formal vollständigen Vertrag enthalten, ohne einen vertrauenswürdigen
terminalen Providererfolg zu belegen.

Stattdessen muss der Adapter den tatsächlichen Prozess-Exitcode sowie
sanitisierte strukturierte Status-, Fehler- und Usage-Felder des Umschlags
erhalten. Die Klassifikation darf nicht aus der Prosa der Modellantwort
abgeleitet werden. Explizit transiente Netzwerk-/Overloadfehler erhalten einen
kleinen, persistierten und fingerprintgleichen automatischen Retry mit
begrenztem Backoff. Auth-, Quota-, Permission-, Runtime- und unbekannte Fehler
bleiben nach ihren jeweiligen Regeln fail-closed.

Eine bereits enthaltene Antwort darf nur dann übernommen werden, wenn der
Providervertrag selbst einen dokumentierten terminalen Erfolgsstatus oder
einen ausdrücklich als nachgelagert und antworterhaltend definierten
Warnstatus ausweist. Eine bloße Prüfung von `REVIEWER` bis `STATUS: DONE` reicht
nicht. Unvollständige, rollenfremde oder widersprüchliche Antworten bleiben
fail-closed.

### Erforderliche Regressionstests

1. Prozess-Exitcode und sanitisiertes strukturiertes Providerstatusfeld bleiben
   bei Adapterfehlern in der Diagnose erhalten.
2. Ein echter Quota-, Auth-, Netzwerk- oder Prozessfehler darf durch enthaltene
   Markerprosa nicht fälschlich zum Review-Erfolg werden.
3. Ein explizit transient klassifizierter Netzwerk-/Overloadfehler wird nur bis
   zur konfigurierten Grenze automatisch und fingerprintgleich wiederholt.
4. Ein unbekannter `runtime`-Status bleibt `awaiting_resume` und löst keine
   unbegrenzte Wiederholung aus.
5. Ein dokumentierter terminaler Erfolgsstatus mit gültigem Plan-, Slice- oder
   Finalreview wird genau einmal validiert und persistiert.
6. Ein gegebenenfalls später unterstützter nachgelagerter Warnstatus wird nur
   anhand einer expliziten Status-Allowlist, nicht anhand von Antwortprosa,
   übernommen.
7. Unvollständige, rollenfremde oder widersprüchliche Verträge bleiben
   fail-closed.
8. Resume nach bereits persistiertem Reviewergebnis erzeugt weder einen zweiten
   Reviewrecord noch einen weiteren Provideraufruf.

### Diagnostische Evidenz

- `.orchestrator/logs/work-unit-0001-antigravity_plan_review.attempt-1.failure.json`
- Erster Versuch: Fehlerart `runtime`, 1.289 Zeichen, 9 Zeilen.
- Zweiter Versuch nach `--resume`: Fehlerart `network`, 4.745 Zeichen,
  33 Zeilen.
- Prozess-Exitcode: `null`
- Workflowzustand: `awaiting_resume`
- Beide Antworten enthielten `PLAN_APPROVAL: YES` und `STATUS: DONE`.
- Keine offenen Antigravity-Findings.

### Umsetzung vom 2026-08-19

- Modellantwort und technische Providerdiagnose werden getrennt behandelt;
  Antwortprosa kann die Fehlerklasse nicht mehr bestimmen.
- Adapterfehler bewahren den realen Prozess-Exitcode und ausschließlich
  sanitisierte technische Umschlagfelder.
- Netzwerk-/Overloadfehler erhalten höchstens zwei automatische Wiederholungen
  desselben Schritts mit 5 beziehungsweise 10 Sekunden Wartezeit.
- Auth-, Runtime-, Output-, Permission- und Prozessfehler bleiben manuell
  resumierbar und werden nicht automatisch wiederholt.
- Structured-v1 speichert Netzwerk-Retries als eigenen
  `transient_retry`-Record; Quota-Pausen bleiben davon getrennt.
- Mirror und Recordkette werden beim Resume symmetrisch und fail-closed
  abgeglichen.

## P2-FU-004 – Antigravity-Planreview wird unnötig ausführlich und verwechselt Plan mit Implementierung

**Status:** im Hotfix umgesetzt; 813/813 Tests bestanden  
**Priorität:** mittel

### Beobachtung

Der zweite Antigravity-Planreview umfasste 4.745 Zeichen und 33 Zeilen,
gegenüber 1.289 Zeichen und 9 Zeilen beim ersten Versuch desselben logischen
Reviews. Neben den erforderlichen Vertragsmarkern erzeugte Antigravity einen
mehrteiligen Markdown-Bericht.

Der Bericht formulierte geplante neue Dateien und Typen wie
`src/provider_input_budget.py`, `PreparedProviderRequest` und
`src/final_review_preflight.py` teilweise so, als seien sie bereits vorhanden
und ihre Implementierung sei verifiziert worden. In einem Planreview können
diese Dateien noch nicht existieren; geprüft werden darf nur, ob der Plan ihre
Einführung ausreichend und prüfbar beschreibt.

### Auswirkung

- unnötiger Output- und Quota-Verbrauch;
- schlechtere Trennschärfe zwischen Planprüfung und Implementierungsprüfung;
- positiv klingende Aussagen können eine tatsächlich noch nicht vorhandene
  Implementierung suggerieren;
- längere Fehlertexte vergrößern State, Audit und spätere Reviewpakete.

### Gewünschtes Verhalten

Antigravity erhält wie Claude eine harte, rollen- und operationsspezifische
Antwortgrenze. Ein Planreview soll ausschließlich strukturierte Findings oder
ein kompaktes `REVIEW_EVIDENCE`, `PRE_MORTEM`, die Planentscheidung und
`STATUS: DONE` zurückgeben. Planbestandteile werden als Vorschlag bewertet,
nicht als vorhandener Code bestätigt.

Zusätzliche freie Überschriften und Wiederholungen der Aufgabenbeschreibung
sollen vor Persistierung entweder durch ein natives Antwortschema verhindert
oder durch einen strikt validierten kompakten Vertrag ersetzt werden. Dabei
darf keine fachliche Evidenz abgeschnitten werden.

### Erforderliche Regressionstests

1. Planreview-Prompt unterscheidet ausdrücklich zwischen geplantem und bereits
   vorhandenem Code.
2. Antigravity-Antwort überschreitet die konfigurierte Outputgrenze nicht.
3. Eine positive Planfreigabe darf keine Implementierungsverifikation für noch
   nicht existente Pfade behaupten.
4. Freie Prosa außerhalb des erlaubten strukturierten Antwortmodells wird
   kontrolliert abgelehnt oder verlustfrei in die vorgesehenen Felder
   normalisiert.
5. Outputzeichen, Outputbytes und Provider-Usage werden kompakt protokolliert.

### Umsetzung vom 2026-08-19

Der Antigravity-Aufruf verwendet nun ein natives JSON-Schema mit genau einem
begrenzten `response`-Feld (maximal 12.000 Zeichen). Die Direktive untersagt
freie Überschriften, wiederholte Analyse und die Darstellung geplanter
Änderungen als bereits implementiert. Die normale strikte Reviewvalidierung
bleibt anschließend unverändert maßgeblich.

## P2-FU-002 – Normale Record-Anhänge erzeugen irreführende Cache-Warnungen

**Status:** offen  
**Priorität:** niedrig

### Beobachtung

Nach regulären strukturierten Record-Anhängen erscheint wiederholt:

```text
Discarding stale artifact head cache for run <run-id>
```

Die Recorddateien sind laut `ArtifactStore` autoritativ und `head.json` ist nur
ein rekonstruierbarer Beschleunigungscache. Beim normalen `put()` wird zuerst
der neue Record veröffentlicht und anschließend die Kette erneut geladen.
Dabei ist der bisherige Cache erwartungsgemäß noch auf dem vorherigen Head und
wird als „stale“ gemeldet.

### Auswirkung

- technisch harmloser Normalfall wirkt wie Datenkorruption;
- unnötige Verunsicherung bei langen Watch-Läufen;
- echte unerwartete Cacheabweichungen sind schwerer von normalen Anhängen zu
  unterscheiden.

### Gewünschtes Verhalten

Ein erwarteter Cachewechsel nach einem eigenen erfolgreichen Append sollte
ohne Warnung beziehungsweise höchstens auf Debug-Level aktualisiert werden.
Eine Warnung bleibt externen, unerwarteten oder inkonsistenten Cacheänderungen
vorbehalten. Die Recordkette bleibt in jedem Fall die Source of Truth.

### Erforderliche Regressionstests

1. Zwei normale sequenzielle `put()`-Aufrufe erzeugen keine Warnung.
2. Ein tatsächlich fremder oder inkonsistenter `head.json` wird weiterhin
   erkannt, aus der Recordkette repariert und angemessen protokolliert.
3. Cacheverlust verändert weder Recordreihenfolge noch Idempotenz.

## P2-FU-003 – Claude-Nutzungszeile bildet file-backed Reviewinput nicht verständlich ab

**Status:** offen  
**Priorität:** mittel

### Beobachtung

Der Planreview meldete:

```text
[AGENT_USAGE] role=claude duration=101.99s turns=6 cost_usd=0.2978 input_tokens=6 output_tokens=9190
```

`input_tokens=6` beschreibt offensichtlich nicht den gesamten file-backed
Reviewkontext mit Manifest und mehreren Read-Aufrufen. Dadurch lässt sich der
tatsächliche Quota- und Kontextverbrauch aus der kompakten Logzeile kaum
bewerten.

### Gewünschtes Verhalten

Die Telemetrie soll, soweit der Provider sie liefert, Initialinput,
Tool-/Read-Kontext, Cache-Read/-Write, Thinking und Output getrennt darstellen.
Unbekannte Werte werden als unbekannt gekennzeichnet und nicht als scheinbar
vollständige kleine Eingabemenge ausgegeben. Zusätzlich sollen die bereits
lokal bekannten Paketzeichen, Paketbytes und Chunkzahlen protokolliert werden.

### Erforderliche Regressionstests

1. Kompakte Usage-Ausgabe unterscheidet Providerwerte von lokal gemessenen
   Paketgrößen.
2. Fehlende Providerfelder werden nicht als Null oder vollständiger Verbrauch
   missverständlich dargestellt.
3. Geheimnisse und Promptinhalte erscheinen nicht in der Telemetrie.

## P2-PLAN-001 – Noch im Arbeitsplan zu verifizierende Scopegrenzen

**Status:** im laufenden Plan-/Implementierungsreview beobachten

- Slice 1 verlangt `AgentFailureKind.PREFLIGHT`, während der Enum derzeit in
  `src/workflow_state.py` liegt und diese Datei erst im exakten Pfad von Slice 2
  erscheint. Die Implementierung darf weder den Slice-1-Scope verletzen noch
  vorübergehend eine sachlich falsche Fehlerklasse verwenden.
- Die produktive Dateigrenze muss pro genehmigtem Slice beziehungsweise
  Korrektur-Work-Unit geprüft werden, nicht gegen die Summe des gesamten
  Feature-Branch-Diffs.
- Für `legacy-state-v3` ist ausdrücklich abzugrenzen, welche Preflightprüfungen
  ohne strukturierte Recordkette möglich sind.
- Initiale `max_chars`-/`max_bytes`-Sicherheitsbudgets benötigen eine
  dokumentierte Repositorypolicy; technische Providerlimits dürfen weiterhin
  unbekannt (`null`) bleiben.

Diese Punkte dürfen nicht durch Änderungen außerhalb des vom Orchestrator
freigegebenen Slice-Scope „nebenbei“ gelöst werden.

## P2-FU-005 – Validierungsrecord wird vor seinem State-v3-Spiegel veröffentlicht

**Status:** behoben mit Commit `c42eca3`; Neustart des betroffenen Laufs erforderlich  
**Priorität:** kritisch  
**Beobachtet in:**
`watch-20260819-135646.145511Z-831128d36569`, Arbeitseinheit 2,
Übergang `codex_implementation -> claude_slice_review`

### Beobachtung

Nach erfolgreicher autoritativer Matrix wurde der strukturierte
`validation_attestation`-Record
`ar1-342b7a59b6e64f4732fdd90ffaa449567027ca044ee5861d5a0cdfe9ae86ab91`
für Fingerprint
`7e9b422e4b9ad7e6362a229b48e0d68ebdc9e1d56da46e139f27891647657f4c`
veröffentlicht. Der State-v3-Spiegel enthielt im selben Zeitpunkt jedoch noch
`runtime_history.current.attestations: []`.

`_attestation()` persistiert zuerst den Record und gibt erst danach ein neues
`WorkflowHistory` mit der Attestierung zurück. Vor dem nächsten Checkpoint
startet `_run_review()` bereits den Reviewer. Dessen
`assert_structured_decision_context()` erkennt den Record-voraus-Zustand und
stoppt korrekt fail-closed mit `validation attestations differ from state-v3`.

### Auswirkung

- die erfolgreiche Validierung kann nicht an Claude übergeben werden;
- ein normales `--resume` scheitert bereits im Resume-Reader;
- der Watcher pausiert korrekt und erzeugt kein Poison, kann den Zustand aber
  ohne Reparatur nicht selbst heilen;
- die implementierten Slice-Dateien bleiben vorhanden, sind jedoch noch nicht
  review- oder commitfähig.

### Erforderliche Korrektur

Nach jeder neu erzeugten Attestierung muss das aktualisierte History-/State-
Mirror vor dem nächsten externen Agenten-, Commit- oder Finalisierungsschritt
checkpointed werden. Plan-, Slice- und Finalreviewpfade müssen dieselbe
Reihenfolge verwenden. Ein Persistenzfehler auf einer Seite bleibt
fail-closed; vorhandene einseitige Records dürfen nicht still gelöscht oder
ignoriert werden.

### Erforderliche Regressionstests

1. Codex-Implementierung, Matrix-PASS, Claude-Start: Record und Mirror sind vor
   dem Providerstart identisch.
2. Derselbe Nachweis für Codex-, Claude- und Antigravity-Finalreview.
3. Prozessabbruch zwischen Recordappend und Checkpoint ist kontrolliert
   reparierbar oder hält mit einer konkreten Recovery-Anweisung an.
4. Resume erzeugt keine zweite Attestierung und startet keine Matrix erneut.
5. Planreview und Legacy-State-v3 bleiben unverändert funktionsfähig.

### Umsetzung und Nachweis

`WorkflowEngine` checkpointet die von `_attestation()` zurückgegebene History
jetzt vor dem nächsten externen Codex- oder Reviewer-Aufruf. Das gilt sowohl
für Slice-Reviews als auch für die drei Finalreview-Übergänge. Die
Regressionstests erzwingen, dass der Fake-Provider nur startet, wenn der
State-v3-Spiegel die fingerprintgebundene Attestierung bereits enthält.

- fokussierte Attestierungs-/Structured-Artifact-Tests: bestanden;
- vollständige Suite: `813 passed`;
- `git diff --check`: bestanden;
- lokaler Commit: `c42eca3 fix: checkpoint attestations before agent starts`.

Der bereits einseitig persistierte Altzustand wird absichtlich nicht still
repariert: Er bleibt fail-closed. Die unreviewte Slice-1-Arbeit wurde in
`stash@{0}` gesichert; das Implementierungsarbeitspaket muss mit einem neuen
State gestartet werden.

## P2-FU-006 – Antigravity erhält Promptverzeichnis statt Repository als Suchwurzel

**Status:** behoben mit Commit `a9546c3`; Neustart des betroffenen Laufs erforderlich  
**Priorität:** kritisch  
**Beobachtet in:** Lauf `20260819-142536Z`, Arbeitseinheit 2,
Übergang `claude_slice_review -> antigravity_slice_review`

### Beobachtung und Ursache

Claude konnte Slice 1 erfolgreich freigeben. Antigravity brach anschließend
mit `search path file:///tmp/dao-antigravity-runtime-.../src does not exist`
ab. Der schreibgeschützte Reviewer-Snapshot war vorhanden, aber der Adapter
übergab über das wiederholbare CLI-Argument `--add-dir` ausschließlich sein
privates Promptverzeichnis. Dadurch löste Antigravity einen normalen
Repositorypfad wie `src` relativ zum Promptverzeichnis auf.

### Umsetzung und Nachweis

Der Antigravity-Adapter bindet nun sowohl das private Promptverzeichnis als
auch den schreibgeschützten Repository-Snapshot als Workspace-Wurzeln. Der
Startprompt bezeichnet den Snapshot zusätzlich ausdrücklich als Wurzel für
repositoryrelative Reads und Suchen. Der Adapter verwirft diese Bindung beim
Cleanup.

- gezielte Adapter-/Runtime-/Snapshot-Tests: `88 passed`;
- vollständige Suite: `814 passed`;
- `git diff --check`: bestanden;
- lokaler Commit: `a9546c3 fix: expose reviewer snapshot to antigravity`.

Der durch den Providerfehler pausierte Lauf bleibt fingerprintgebunden an den
vorherigen Frameworkstand und wird nicht normal resumed. Sein unreviewter
Slice-1-Arbeitsstand ist recoverbar in `stash@{0}` gesichert.

## P2-FU-007 – Gleichbedeutendes Review-Evidenzlabel erzwingt unnötige Modellreparatur

**Status:** behoben mit Commits `c6b6a47` und `a1fd6f9`; Neustart des betroffenen Laufs erforderlich  
**Priorität:** kritisch  
**Beobachtet in:** Lauf `20260819-144920Z`, Arbeitseinheit 2,
`claude_slice_review`

### Beobachtung und Ursache

Claudes erstes Slice-Review war vollständig, positiv und enthielt ein
konkretes Finding sowie eine dreiteilige Evidenz. Die dritte Evidenzdimension
war jedoch mit `Realistic break condition:` statt `Break condition:`
beschriftet. Der lokale Normalisierer akzeptierte nur die zweite Form, rief
deshalb unnötig die kostenpflichtige Vertragsreparatur auf und verwarf auch
deren semantisch unveränderte Ausgabe.

### Umsetzung und Nachweis

Die lokale, semantisch neutrale Normalisierung akzeptiert nun beide eindeutig
gleichbedeutenden Labels. Kompakte Prosa-Trenner wie das real beobachtete
`raise|proceed` werden dabei typografisch neutralisiert; echte, durch
Leerraum abgegrenzte oder mehrdeutige Vertragstrenner bleiben fail-closed.
Ein Workflow-Level-Test und der Replay des vollständigen realen Claude-Logs
weisen nach, dass die beobachtete Ausgabe ohne zweiten Agentenaufruf
normalisiert wird.

- fokussierte Contract-/Workflow-Tests: `164 passed`;
- vollständige Suite nach der finalen Präzisierung: `816 passed`;
- `git diff --check`: bestanden;
- lokale Commits:
  - `c6b6a47 fix: normalize realistic review break conditions`;
  - `a1fd6f9 fix: preserve embedded review evidence separators`.

Der pausierte Lauf ist an den vorherigen Frameworkfingerprint gebunden. Sein
Slice-1-Arbeitsstand bleibt recoverbar in `stash@{0}` gesichert und der Lauf
wird nicht normal resumed.

## P2-FU-008 – Ungültiges Review nach Reparatur beendet den Prozess ungeordnet

**Status:** behoben mit Commit `385d137`  
**Priorität:** kritisch  
**Beobachtet in:** Lauf `20260819-144920Z`, Arbeitseinheit 2,
`claude_slice_review`

### Beobachtung und Ursache

Wenn sowohl das ursprüngliche Reviewergebnis als auch die einmalige kompakte
Vertragsreparatur ungültig blieben, ließ `_run_review()` den internen
`WorkflowContractError` bis zur obersten Programmebene durchlaufen. Der Lauf
endete dadurch als allgemeiner State-v3-Fehler statt als persistierter,
fingerprintgebundener und resumierbarer Fehler der aktuell aktiven Rolle.

Das war ein generischer Fehlerpfad: Jede noch unbekannte Abweichung im
Reviewformat konnte trotz korrekter Fail-closed-Entscheidung den geregelten
Resume-Mechanismus umgehen.

### Umsetzung und Nachweis

`_run_review()` überführt einen nach der begrenzten Reparatur verbleibenden
Vertragsfehler nun in einen typisierten `AgentFailureKind.OUTPUT`. Der Fehler
wird über denselben Persistenzpfad wie andere Instanzfehler gespeichert; der
Workflow bleibt auf der ursprünglichen Claude- oder Antigravity-Reviewstufe,
startet keine nachgelagerte Rolle und erzeugt keinen Commit. Ein Resume setzt
genau an dieser Reviewstufe fort.

Die bisherigen negativen Dry-run-Vertragstests prüfen deshalb nun den
persistierten Exit-3-Halt statt eine ungefangene Python-Ausnahme.

- gezielte Workflow-/Dry-run-Fehlerpfade: `8 passed`;
- vollständige Suite: `816 passed`;
- `git diff --check`: bestanden;
- lokaler Commit: `385d137 fix: persist invalid reviewer contracts as resumable halts`.

## P2-FU-009 – Codex-Quota erkannt, datierter Reset aber nicht automatisch geparst

**Status:** behoben mit Commit `d28a2b0`  
**Priorität:** hoch  
**Beobachtet in:** Lauf `20260819-152415Z`, Übergang zu Slice 03,
`codex_implementation`

### Beobachtung und Ursache

Codex meldete die Quota korrekt als technischen Providerfehler, verwendete für
den Reset jedoch den Wortlaut `try again at Aug 20th, 2026 5:36 AM`. Der
Parser unterstützte ISO-Zeitstempel, relative Angaben sowie lokale Uhrzeiten
mit ausdrücklich genannter IANA-Zeitzone, aber noch kein englisches Datum mit
Ordinalendung und ohne Zeitzonenangabe. Der Lauf blieb deshalb korrekt mit
Exitcode 2 resumierbar, wechselte aber nicht in die automatische Warteschleife.

### Umsetzung und Nachweis

Das reale Format wird ausschließlich für Codex akzeptiert und mit der lokalen
IANA-Zeitzone des Orchestrator-Rechners verbunden. Auf dem beobachteten
Europe/Berlin-System ergibt `Aug 20th, 2026 5:36 AM` den Zeitpunkt
`2026-08-20T03:36:00Z`. Fehlt eine zuverlässig ermittelbare IANA-Zeitzone oder
ist die Angabe ungültig, vergangen oder mehrdeutig, bleibt das Verhalten
fail-closed bei manueller Fortsetzung. Andere englische Datumsangaben ohne
Resetformulierung werden nicht als Quota-Reset interpretiert.

- fokussierte Quota-Tests: `32 passed`;
- vollständige Suite: `842 passed`;
- `git diff --check`: bestanden;
- lokaler Commit: `d28a2b0 fix: parse dated Codex quota resets`.

## P2-FU-010 – QUOTA-RESUME-DIFF-Gate kann weder freigegeben noch verlassen werden

**Status:** behoben und lokal committed  
**Priorität:** kritisch  
**Beobachtet in:** Lauf `20260819-152415Z`, Arbeitseinheit 4,
`codex_implementation`

### Beobachtung und Ursache

Nach einer externen, bewusst vorgenommenen Repositoryänderung erzeugte der
erste Resume korrekt `QUOTA-RESUME-DIFF`. Das Gate enthielt jedoch weder einen
Fingerprint noch einen Resume-Schritt. Dadurch war es für `--approve-gate`
nicht freigabefähig. Ein weiterer gewöhnlicher Resume legte zwar intern eine
Bestätigung für den neuen Fingerprint ab, wertete die außerhalb des
Slice-Scopes liegenden Pfade aber weiterhin als unerwartet und erzeugte
dasselbe Gate erneut. Der Zustand bildete somit eine Endlosschleife.

### Umsetzung und Nachweis

Repositoryänderungen während eines Invocation-Halts erzeugen nun ein eigenes,
fingerprintgebundenes `quota_resume_diff`-Benutzergate mit exakten Pfaden und
dem ursprünglichen Resume-Schritt. Nur eine explizite Entscheidung für genau
diesen Fingerprint und diese Pfade erzeugt die passende Bestätigung; danach
deckt sie sowohl den Fingerprintwechsel als auch die zuvor ausgewiesene
Scopeabweichung ab. Ändert sich das Repository erneut, wird ein neues Gate für
den neuen Fingerprint erzeugt.

Bereits gespeicherte alte, ungebundene `QUOTA-RESUME-DIFF`-Gates werden ohne
manuelle State-Bearbeitung in den Invocation-Halt zurückgeführt und gegen den
aktuell vorliegenden Repositoryzustand neu gebunden. Eine eventuell durch
frühere gewöhnliche Resumeversuche erzeugte wirkungslose Bestätigung wird
dabei entfernt.

- fokussierte Workflow-, State- und Runtime-Tests: `180 passed`;
- vollständige Suite: `843 passed`;
- `git diff --check`: bestanden;
- lokaler Commit: `4f67819 fix: make quota resume diff gates approvable`.

## P2-FU-011 – Freigegebener Resume-Diff stoppt erneut vor dem Slice-Review

**Status:** behoben und lokal committed  
**Priorität:** kritisch  
**Beobachtet in:** Lauf `20260819-152415Z`, Arbeitseinheit 4,
Übergang `codex_implementation` → `claude_slice_review`

### Beobachtung und Ursache

Das fingerprintgebundene `QUOTA-RESUME-DIFF`-Gate ließ den pausierten
Codex-Aufruf korrekt weiterlaufen. Beim folgenden Review prüfte der
Orchestrator die kanonischen Änderungen jedoch erneut ausschließlich gegen die
ursprüngliche Slice-Allowlist. Die vorherige Benutzerentscheidung war für
diesen Scope-Check unsichtbar. Dieselben sechs bereits freigegebenen Pfade
erzeugten deshalb ein nicht fingerprintgebundenes `UNEXPECTED-PATH`-Gate.

### Umsetzung und Nachweis

Ein Scope-Verstoß erzeugt nun ein fingerprintgebundenes
`unexpected_file`-Benutzergate. Die Freigabe gilt nur für den exakten gesamten
Änderungsfingerprint und die exakt ausgewiesenen unerwarteten Pfade. Review und
Commit erkennen dieselbe Entscheidung; bei jeder weiteren Repositoryänderung
verfällt sie automatisch. Die persistierte Slice-Allowlist wird nicht
umgeschrieben oder erweitert.

Bereits gespeicherte alte, ungebundene `UNEXPECTED-PATH`-Gates werden durch
einen gewöhnlichen Resume erneut geprüft und dabei als freigabefähiges Gate
für den aktuellen Fingerprint persistiert.

- fokussierter Gate-Durchstich: `2 passed`;
- angrenzende Workflow-, State-, Runtime- und Dry-Run-Tests: `227 passed`;
- vollständige Suite: `849 passed`;
- `git diff --check`: bestanden;
- lokaler Commit: `c712e43 fix: persist unexpected path approvals across review`.

### Nachkorrektur am 20.08.2026

Nach der Claude-Adapterkorrektur zeigte sich, dass ein exakt freigegebenes
`QUOTA-RESUME-DIFF` beim unmittelbar folgenden Scope-Check noch nicht als
gleichwertige Pfadentscheidung erkannt wurde. Der Resume-Gate bindet sich an
die dort ausgewiesene unerwartete Pfadmenge; `_validate_change_boundary()`
berücksichtigte zuvor jedoch ausschließlich Entscheidungen vom Typ
`unexpected_file`.

Der Scope-Check akzeptiert nun zusätzlich eine `quota_resume_diff`-Entscheidung,
aber nur bei identischem Gesamtfingerprint und exakt identischer unerwarteter
Pfadmenge. Ein gemischter Durchstichtest mit erlaubten und fremden Pfaden
belegt, dass die Freigabe ohne zweites Gate bis zum Review weiterführt. Jede
weitere Repositoryänderung bleibt durch den neuen Fingerprint gesperrt.

- fokussierter Resume-/Scope-Durchstich: `1 passed`;
- vollständige Suite: `849 passed`;
- `git diff --check`: bestanden;
- lokaler Commit: `aea70ab fix: reuse exact resume diff approval for scope check`.

## P2-FU-012 – Claude beendet den Prozess ohne Reviewvertrag

**Status:** Ursache behoben und lokal committed  
**Priorität:** mittel  
**Beobachtet in:** Lauf `20260819-152415Z`, Arbeitseinheit 4,
`claude_slice_review`, Invocation `a3e20182fdd144b89f24977ea6a13fba`

### Beobachtung und Ursache

Nach erfolgreicher Gate-Freigabe und vollständiger PASS-Validierung wurde Claude
korrekt gestartet. Der Provider lieferte jedoch nur den extrahierten Inhalt
`completed` und damit keinen gültigen Reviewvertrag. Das persistierte
Agentenlog zeigte anschließend den eigentlichen Inhalt: Claude hatte sämtliche
Read-Aufrufe erfolglos für die Suche nach `review-manifest.md` verbraucht. Der
Adapter übergab nur den Dateinamen, obwohl das Manifest in einem separaten
privaten Runtime-Verzeichnis lag. `--add-dir` erteilte Leserechte, änderte aber
nicht das Arbeitsverzeichnis des Reviewers. Auch das Manifest selbst nannte die
Chunks nur relativ.

Dies war kein erneuter Scope-, Gate- oder Fingerprintfehler. Wiederholte Resumes
konnten ohne Adapterkorrektur nicht helfen.

### Umsetzung und Nachweis

Startdirektive und Capability-Diagnose nennen nun den absoluten Manifestpfad.
Das Manifest enthält für jeden Chunk den absoluten, durch `--add-dir`
freigegebenen Pfad. Damit kann Claude aus dem separaten Reviewer-Snapshot heraus
alle Evidenzdateien deterministisch lesen. Eine automatische Contract-Reparatur
wurde bewusst nicht ergänzt, weil sie fehlende Reviewevidenz nicht sicher
ersetzen könnte.

- Adaptertests: `24 passed`;
- Adapter-, Runtime- und Budgettests: `72 passed`;
- vollständige Suite: `849 passed`;
- lokaler Commit: `c0122f7 fix: expose Claude review packet paths`.

## P2-FU-013 – Entfernte Antigravity-Shell wird als lokale fehlende Binärdatei klassifiziert

**Status:** behoben; Übergabe an laufenden Slice ausstehend  
**Priorität:** mittel  
**Beobachtet in:** Lauf `20260819-152415Z`, Arbeitseinheit 4,
`antigravity_slice_review`, Invocation `0431a2988ce6481fa1c3fa3fd3b846b2`

### Beobachtung und Ursache

Claude schloss Slice 03 erfolgreich mit `SLICE_APPROVAL: 03 | YES` ab.
Antigravity wurde anschließend für denselben Fingerprint gestartet, lieferte
aber aus seiner entfernten Tool-Laufzeit:

`remote error: run bash: fork/exec /usr/bin/bash: no such file or directory`

Der lokale `agy`-Prozess existierte und endete sogar mit Exitcode 0; die
strukturierte Providerhülle meldete den Fehlerstatus. Es fehlt daher nicht die
lokale Antigravity-Binärdatei. `classify_agent_failure()` erkennt derzeit den
allgemeinen Textmarker `no such file` und ordnet den Fehler dennoch als
`AgentFailureKind.BINARY` ein. Diese Klasse wird bewusst nicht automatisch
wiederholt. Der technisch vorübergehende entfernte Instanzfehler wird deshalb
als manueller `awaiting_resume`-Halt persistiert.

### Empfohlene Korrektur

- eindeutig entfernte Tool-Startfehler vor der allgemeinen lokalen
  `no such file`-Regel erkennen;
- sie einer eng begrenzten transienten Fehlerklasse beziehungsweise dem
  bestehenden bounded Network-Retry zuordnen;
- echte lokale `FileNotFoundError`- und fehlende CLI-Binärdateien weiterhin
  fail-closed als `BINARY` behandeln;
- Regressionstests für Klassifikation, höchstens zwei automatische Versuche,
  unveränderten Fingerprint und unveränderte Reviewreihenfolge ergänzen.

### Umsetzung und Nachweis

Der exakt erkannte entfernte Antigravity-Fehler wird nun vor der allgemeinen
`no such file`-Regel als transiente Providerinfrastruktur klassifiziert. Damit
greift der vorhandene, auf zwei Wiederholungen begrenzte Network-Retry. Ein
lokaler `FileNotFoundError` für `agy` bleibt `BINARY` und wird nicht automatisch
wiederholt.

## P2-FU-014 – Freigegebene Zwischen-Commits blockieren den Slice-Commit

**Status:** behoben; Übergabe an laufenden Slice ausstehend  
**Priorität:** kritisch  
**Beobachtet in:** Lauf `20260819-152415Z`, Arbeitseinheit 4,
Übergang `antigravity_slice_review` → `slice_commit`

### Beobachtung und Ursache

Claude und Antigravity genehmigten Slice 03 für den vollständigen aktuellen
Fingerprint. Die Git-Transaktion verweigerte anschließend trotzdem den Commit
mit `slice HEAD changed after its persisted start`. Während des pausierten
Slice waren geprüfte Orchestratorreparaturen als eigene lokale Commits auf
demselben Branch entstanden und über fingerprintgebundene Gates freigegeben
worden. Review-/Resume-Schicht akzeptierten diesen Zustand, die Commit-Schicht
verlangte jedoch weiterhin strikt `HEAD == Slice-Start`.

### Umsetzung und Nachweis

Ein Zwischen-HEAD ist nun nur zulässig, wenn es vom persistierten Slice-Start
abstammt, exakt dem für denselben Gesamtfingerprint freigegebenen HEAD entspricht
und sämtliche Scopeabweichungen exakt gebunden wurden. Die Attestierung und
beide Reviews bleiben an den vollständigen Diff ab Slice-Start gebunden. Die
Git-Transaktion staged und committet dagegen ausschließlich den aktuellen
uncommitteten Delta gegen das freigegebene Zwischen-HEAD; vorhandene
Zwischen-Commits werden weder erneut aufgenommen noch umgeschrieben.

Ändert sich der Fingerprint erst am Commit-Schritt, springt der Workflow nun
automatisch zu Validierung, Claude und Antigravity zurück und wiederholt Codex
nicht. Zwei neue Durchstichtests sichern beide Verträge.

- angrenzende Git-, Workflow- und Produktionsszenarien: `153 passed`;
- vollständige Suite: `852 passed`;
- `git diff --check`: bestanden.

## P2-FU-015 – Finalreview-Preflight ignoriert gebundene externe Slice-Freigaben

**Status:** behoben und als Commit `48cf07a` gesichert  
**Priorität:** kritisch  
**Beobachtet in:** Lauf `20260819-152415Z`, Arbeitseinheit 5,
`codex_final_review`

### Beobachtung und Ursache

Slice 03 wurde für Fingerprint `98ee175b02a4…` vollständig validiert, von
Claude und Antigravity genehmigt und als Commit `cad779d` gebunden. Die zuvor
über ein fingerprintgebundenes Nutzer-Gate ausdrücklich freigegebenen Pfade
`src/git_service.py`, `tests/test_git_service.py` und
`tests/test_quota_wait.py` lagen außerhalb des ursprünglichen Task-Scope.

Der Slice-Commit akzeptierte diese Pfade deshalb korrekt. Das nachfolgende
Finalreview-Preflight berücksichtigte jedoch nur ursprünglichen Task-Scope und
persistierte Slice-Allowlists, nicht die bereits vorhandene Gate-/Commit-
Bindung. Es hielt daher widersprüchlich mit `UNAUTHORIZED-PATH` an. Die
Terminaldiagnose verlor außerdem `affected_paths` und zeigte `paths=(none)`.

### Vorbereitete Korrektur und Nachweis

Externe Pfade werden beim Finalreview nur dann zusätzlich anerkannt, wenn alle
folgenden Fakten zusammenpassen:

- genehmigte `UNEXPECTED_FILE`- oder `QUOTA_RESUME_DIFF`-Entscheidung in einer
  abgeschlossenen Slice-Work-Unit;
- strukturierter, genehmigter Nutzer-`GatePayload` mit identischem Fingerprint;
- `BindingPayload` vom Typ `commit` mit demselben Fingerprint;
- Binding-Ziel entspricht exakt dem Commit des betreffenden abgeschlossenen
  Slice.

Fehlender Gate-Record oder ein Binding auf einen anderen Commit bleibt
fail-closed. `affected_paths` werden nun in das persistierte Bootstrap-Gate
übernommen und dadurch in der Terminaldiagnose sichtbar. Der Durchstich gegen
den tatsächlich pausierten Lauf akzeptiert alle 38 Branch-Pfade ohne
Restbefund.

- fokussierte Verträge: `74 passed`;
- vollständige Suite: `856 passed`;
- `git diff --check`: bestanden;
- Commit: `48cf07a`.

## P2-FU-016 – Abschlussrollen verwenden unterschiedliche Fingerprint-Grenzen

**Status:** behoben und als Commit `69c3e24` gesichert  
**Priorität:** kritisch  
**Beobachtet in:** Lauf `20260819-152415Z`, Arbeitseinheit 5,
Übergang `codex_final_review` → `claude_final_review`

### Beobachtung und Ursache

Der Codex-Abschlussbericht wurde erfolgreich erzeugt und als strukturierter
`AgentResult` gespeichert. Das Claude-Preflight hielt unmittelbar danach mit
`CODEX-FINAL-RESULT-MISSING` an. Der Record fehlte nicht: Er war an den
Fingerprint `0f376d100885…` gebunden, während alle Provider-Bootstrap- und
Finalreview-Prüfungen den branchweiten Fingerprint `ac371f60ea8b…`
verwendeten.

Ursache war eine asymmetrische Startgrenze. Das Provider-Preflight sammelte im
Finalreview den vollständigen Diff ab `branch_base`; die generische
Record-Persistenz berechnete den Fingerprint dagegen weiterhin ab dem Start des
letzten Slice. Dadurch konnte Claude den tatsächlich vorhandenen
Codex-Abschlussrecord für denselben fachlichen Branchstand niemals finden.

### Vorbereitete Korrektur und Nachweis

Alle strukturierten Records eines `FINAL_REVIEW`-Work-Units verwenden nun
dieselbe branchweite Fingerprint-Grenze. Die Idempotenzschlüssel von Codex- und
Reviewerrecords enthalten den Fingerprint, damit ein legitimer erneuter Lauf
nach einer Fingerprintänderung nicht mit einem älteren Payload kollidiert.

Fehlt beim Claude- beziehungsweise Antigravity-Preflight nur der unmittelbar
erforderliche Vorgängerrecord für den aktuellen Fingerprint, springt der
Workflow automatisch zum Codex- beziehungsweise Claude-Abschlussschritt zurück.
Ein manueller Resume-Stopp ist dafür nicht mehr erforderlich; alle anderen
Preflight-Denials bleiben fail-closed.

- fokussierte Verträge: `11 passed`;
- vollständige Suite: `859 passed`;
- `git diff --check`: bestanden;
- Commit: `69c3e24`.

## P2-FU-017 – Abgelehntes Finalreview wird vor Korrekturwechsel nicht gespiegelt

**Status:** Korrektur vorbereitet und vollständig getestet; Commit ausstehend  
**Priorität:** kritisch  
**Beobachtet in:** Lauf `20260819-152415Z`, Übergang von Arbeitseinheit 5
(`final_review`) zu Arbeitseinheit 6 (`correction`)

### Beobachtung und Ursache

Claude persistierte ein gültiges, ablehnendes Finalreview samt Finding-
Transitionen in der strukturierten Record-Kette. Der Workflow startete danach
unmittelbar die Korrektur-Work-Unit. Dabei archivierte `bind_work_unit()` noch
den älteren Treiber-Mirror der Finalreview-Historie, in dem der neue
`ReviewAuditEvent` fehlte. Die Record-Kette enthielt die Entscheidung, der
State-v3-Mirror jedoch nur die daraus entstandene Korrektur-Work-Unit. Der
nächste externe Schritt hielt deshalb fail-closed mit
`structured reviewer decisions differ from the state-v3 mirror` an.

### Vorbereitete Korrektur und Nachweis

Ein ablehnendes Finalreview wird künftig vollständig checkpointed, solange die
Finalreview-Work-Unit noch aktiv ist. Erst danach wird die Korrektur-Work-Unit
angelegt. Für bereits vom alten Defekt betroffene Läufe existiert eine eng
begrenzte Kompatibilitätserkennung: Sie akzeptiert ausschließlich genau einen
fehlenden, autoritativen `denied`-Reviewrecord, wenn unmittelbar danach eine
Korrektur-Work-Unit existiert, deren Findings Teil dieses Reviews sind und die
vorherige Historie eine Attestierung für exakt denselben Fingerprint besitzt.
Alle anderen Record-/Mirror-Abweichungen bleiben gesperrt.

- fokussierte Verträge: `4 passed`;
- Durchstich gegen den tatsächlich pausierten Lauf: `CURRENT_RUN_STRUCTURED_CONTEXT_OK`;
- vollständige Suite: `860 passed`;
- Commit: ausstehend.

## P2-FU-018 – Unveränderte externe Pfade verlangen nach jeder Korrektur erneut ein Gate

**Status:** offen; für Stabilisierungspaket 1.1 vorgesehen  
**Priorität:** hoch  
**Beobachtet in:** Lauf `20260819-152415Z`, Arbeitseinheit 6,
Korrekturrunden zu `C-07` und `C-08`

### Beobachtung und Ursache

`tests/test_git_service.py` lag außerhalb der persistierten Slice-Allowlist.
Die dort enthaltenen C-05-Negativtests wurden deshalb korrekt über ein
fingerprintgebundenes `UNEXPECTED-PATH`-Gate geprüft und freigegeben. Spätere
Korrekturrunden änderten ausschließlich andere Dateien. Dadurch änderte sich
jedoch der Gesamtfingerprint des kanonischen Diffs. Obwohl Inhalt und Diff von
`tests/test_git_service.py` unverändert blieben, galt die vorherige Freigabe
formal nicht mehr und derselbe Pfad löste vor jedem weiteren Review erneut ein
Benutzergate aus.

Das bestehende Verhalten ist fail-closed und verhindert, dass eine Freigabe
für einen alten Gesamtstand still auf geänderte externe Inhalte übertragen
wird. Es bindet die Entscheidung aber gröber als erforderlich: Eine
unveränderte, bereits geprüfte Pfadänderung wird durch jede sachfremde Änderung
an einer anderen Datei erneut freigabepflichtig.

### Gewünschtes Verhalten

Eine externe Pfadfreigabe wird zusätzlich an einen kanonischen Inhalts- oder
Diffdigest jedes freigegebenen Pfades gebunden. Bei einem späteren
Gesamtfingerprint darf sie nur dann automatisch übernommen werden, wenn:

1. Pfad, Pfadklassifikation und pfadspezifischer Digest exakt identisch sind;
2. die ursprüngliche Entscheidung genehmigt und als strukturierter Gaterecord
   in derselben Run-/Slice-Abstammung persistiert ist;
3. der Pfad seit der Freigabe weder umbenannt noch gelöscht noch erneut
   verändert wurde;
4. ausschließlich andere, unabhängig autorisierte Pfade den neuen
   Gesamtfingerprint verursacht haben;
5. Review, Attestierung und Commit weiterhin an den neuen vollständigen
   Gesamtfingerprint gebunden werden.

Die Übernahme darf die persistierte Slice-Allowlist nicht pauschal erweitern
und Codex keine zukünftige Schreibberechtigung für den Pfad geben. Ändert sich
auch nur ein Byte des freigegebenen Pfaddiffs, ist ein neues exaktes Gate
erforderlich. Eine bloße Übereinstimmung des Pfadnamens reicht niemals aus.

### Erforderliche Regressionstests

1. Eine genehmigte externe Datei mit identischem Pfaddigest benötigt nach
   einer Änderung an einer anderen Datei kein zweites Gate.
2. Eine nachträgliche Änderung derselben externen Datei erzeugt zwingend ein
   neues fingerprint- und pfadgebundenes Gate.
3. Umbenennen, Löschen, Type-Change, Symlinkwechsel und abweichende
   Zeilenendennormalisierung werden nicht als unveränderter Inhalt akzeptiert.
4. Freigaben aus einem anderen Run, Slice oder nicht verwandten Work-Unit-Zweig
   werden nicht übernommen.
5. Unter- oder überdeckende Pfadmengen sowie fehlende strukturierte
   Gate-/Bindingrecords bleiben fail-closed.
6. Der Commit verwendet die übernommene Pfadfreigabe nur für den unveränderten
   pfadspezifischen Digest, während Attestierungen und Reviews weiterhin den
   vollständigen aktuellen Gesamtfingerprint prüfen.

## P2-FU-019 – Genehmigtes Review liegt nach Checkpointfehler vor seinem State-Spiegel

**Status:** Hotfix umgesetzt und vollständig getestet  
**Priorität:** kritisch  
**Beobachtet in:** Lauf `20260819-152415Z`, Arbeitseinheit 6,
`claude_slice_review` nach Schließung von `C-08`

### Beobachtung und Ursache

Claude lieferte `SLICE_APPROVAL: 04 | YES` und schloss C-08. Reviewrecord und
Finding-Schließung wurden autoritativ veröffentlicht, der State-v3-Checkpoint
scheiterte jedoch vor der Spiegelung. Der vorhandene hashgebundene
Reviewer-Replay war absichtlich nur für abgelehnte Reviews implementiert.
`resolve_resume_state()` hielt deshalb bereits vor Antigravity mit
`finding status differs from state-v3` an.

### Umsetzung und Nachweis

Der Replaypfad behandelt genehmigte und abgelehnte Slice-Reviews symmetrisch,
bleibt aber an genau einen Reviewrecord, Reviewer, Runde, Verdict,
Orchestrator-Attestierung, Idempotenzdigest und eindeutigen Providerlog
gebunden. Ein genehmigtes Claude-Review wird lokal erneut vollständig
validiert, in den State gespiegelt und führt zu Antigravity; ein genehmigtes
Antigravity-Review führt zum Slice-Commit. Record-/Output-Verdictabweichungen
und nicht geschlossene Findingtransitionen eines positiven Korrekturreviews
bleiben gesperrt.

Hat sich der Repositoryfingerprint nach dem persistierten Claude-Review
geändert, wird dessen alter Entscheid zwar zur Mirror-Reparatur replayt, aber
nicht für den neuen Stand verwendet. Der Workflow springt automatisch von
Antigravity zu Validierung und Claude zurück. Erst eine Claude-Freigabe für den
aktuellen Gesamtfingerprint erlaubt Antigravity.

Ein zweiter Fehler wurde beim echten Replay sichtbar: `open_findings` im
Korrektur-Work-Unit beschreibt die ursprüngliche Finding-Zuordnung, wurde beim
Resume-Abgleich aber fälschlich als aktueller Findingstatus interpretiert. So
überschrieb die unveränderliche Zuordnung die gerade replayte Schließung wieder
mit `open`, und derselbe `finding status differs from state-v3`-Fehler trat beim
Checkpoint erneut auf. Der Abgleich bevorzugt nun den aus `runtime_history`
projizierten Live-Status; `open_findings` dient nur noch als Fallback für alte
Mirrors ohne eigenen Findingeintrag.

- positiver Approved-Replay-Durchstich ohne zweiten Claude-Provideraufruf;
- Migrationstest für eine Record-voraus-Finding-Schließung;
- automatischer Fingerprint-Rewind vor Antigravity;
- fokussierte Übergangstests: `143 passed`;
- vollständige Suite: `903 passed`;
- `git diff --check`: bestanden.

## P2-FU-020 – Codex-Abschlussanalyse kann erkannte Defekte nicht verbindlich übergeben

**Status:** für Stabilisierungspaket 1.1 einplanen  
**Priorität:** hoch  
**Beobachtet in:** Lauf `20260819-152415Z`, wiederholte branchweite
Abschlussreviews nach C-09/C-10

### Beobachtung und Ursache

Codex identifizierte in seinem branchweiten Abschluss-Selbstcheck mehrere
konkrete Defekte, darunter unvollständige Binding-Referenzprüfungen,
unzureichende Attestierungsbindung und den fehlenden Produktivdatei-Limitcheck.
Der Codex-Vertrag erlaubt in diesem Schritt jedoch nur Antworten auf bereits
offene Findings und einen Abschlussbericht; Codex kann keinen strukturierten
neuen Befund persistieren. Claude übernahm zunächst nur C-09 und C-10. Der
bereits sichtbare Binding-Defekt erschien deshalb erst in der folgenden
Gesamtreviewrunde als C-11 und verursachte einen weiteren vollständigen
Korrektur-/Review-Roundtrip.

Ein AI-Schritt, der ausdrücklich einen Review beziehungsweise adversarialen
Vollständigkeitscheck ausführt, darf seine Ergebnisse nicht nur in ungebundener
Prosa hinterlassen. Zugleich darf Codex als schreibende Implementiererrolle
nicht seine eigenen Findings freigeben oder schließen.

### Zielvertrag

1. Der Schritt wird fachlich korrekt als `codex_final_analysis` bezeichnet und
   nicht als selbstgenehmigendes Review.
2. Codex darf strukturierte, fingerprintgebundene Defektkandidaten ausgeben,
   beispielsweise `CANDIDATE_FINDING: X-01 | BLOCKER | Beschreibung |
   Akzeptanztest`.
3. Jeder Kandidat wird append-only in der Record-Kette persistiert und blockiert
   den Abschluss, bis Claude ihn im selben Finalreview ausdrücklich disponiert.
4. Claude muss für jeden Kandidaten entweder ein eigenes `C-*`-Finding eröffnen
   oder ihn mit konkreter Begründung zurückweisen. Stilles Weglassen ist
   unzulässig.
5. Antigravity erhält Kandidaten und Claude-Dispositionen als strukturierte
   Fakten, nicht nur als Teil einer großen Prosa-Evidenz.
6. Codex bleibt ohne Freigabe- und Schließungsrecht für diese Kandidaten; die
   Rollentrennung zwischen Implementierung und Review bleibt erhalten.
7. Wird dieser verbindliche Kandidatenvertrag nicht umgesetzt, entfällt der
   Codex-Abschlusslauf vollständig und Claude erhält direkt die branchweite
   Evidenz. Ein teurer, nicht auswertbarer Prosa-Selbstcheck ist nicht sinnvoll.

### Abnahmetests

- Mehrere Codex-Kandidaten müssen in genau einem Claude-Aufruf vollständig
  disponiert werden.
- Ein ausgelassener Kandidat macht Claudes Review formal ungültig, bevor eine
  Freigabe oder Korrekturrunde entstehen kann.
- Ein zurückgewiesener Kandidat benötigt eine nicht leere Begründung.
- Ein akzeptierter Kandidat erzeugt genau ein Claude-eigenes Finding mit
  nachvollziehbarer Herkunftsreferenz.
- Resume nach Record-/Checkpoint-Unterbrechung erzeugt weder doppelte
  Kandidaten noch doppelte Claude-Findings.

## P2-FU-021 – Erfolgreicher Direktlauf endet still und lässt Inbox-Artefakte zurück

**Status:** für Stabilisierungspaket 1.1 einplanen  
**Priorität:** mittel  
**Beobachtet in:** erfolgreicher Abschluss des Laufs `20260819-152415Z`

### Beobachtung

Claude und Antigravity genehmigten den finalen Fingerprint, der Workflowstate
stand anschließend auf `completed`, alle sechs Slices waren committed, der
Audit-Abschlusscommit `b819be1` existierte und die strukturierte Kette enthielt
genau einen gültigen Completion-Record. Der Direktaufruf kehrte dennoch nur
zum Shell-Prompt zurück. Eine eindeutige Erfolgsmeldung mit Run-ID,
Abschlusscommit, Teststatus und Zielpfad fehlte. Die letzte sichtbare Zeile war
sogar eine irreführende historische Recovery-Warnung.

Außerdem verblieben bei diesem erfolgreichen Direktlauf die Implementierungs-
MD sowie alte `.attempts`-/Watch-Sidecars in `inbox/`. Die Done-Outbox-
Transaktion ist derzeit an den Watcher gebunden und wird beim direkten
`run_task --resume` nicht äquivalent abgeschlossen.

### Zielvertrag und Abnahme

- Jeder erfolgreiche Lauf schreibt genau eine eindeutige terminale
  `WORKFLOW COMPLETED`-Meldung mit Run-ID, Finalstatus, Abschlusscommit und
  Outbox-/Taskstatus.
- Die letzte fachliche Logzeile darf keine historische Denial-/Recoverywarnung
  sein, wenn der terminale Zustand erfolgreich ist.
- Watch- und Direktmodus verwenden dieselbe idempotente Success-Finalisierung
  für Inbox-Task, Attempt-Sidecar, Watch-Sidecar und Done-Outbox.
- Ein Crash zwischen Completion-Record, Auditcommit und Taskverschiebung wird
  beim nächsten Start ohne erneuten Provideraufruf fertiggestellt.
- Wiederholte Finalisierung erzeugt weder doppelte Outboxdateien noch einen
  zweiten Completion- oder Auditcommit.

## P2-DEC-001 – Stabilisierungspaket 1.1 vor weiterer Protokolloberfläche

**Status:** als nächster Schritt nach Abschluss von Arbeitspaket 1 vorgesehen  
**Priorität:** kritisch  
**Einordnung:** Konsolidierung der Phase-1-/Phase-2-Zwischenarchitektur, keine
neue Fachfunktion

### Entscheidung und Begründung

Nach Arbeitspaket 1 soll nicht unmittelbar das nächste native JSON-Paket
beginnen. Die Fälle P2-FU-005 sowie P2-FU-010 bis P2-FU-017 zeigen ein
wiederkehrendes strukturelles Muster: Record-Kette, State-v3-Mirror,
Auditprojektion und Work-Unit-Zustand können zwischen zwei einzeln
erfolgreichen Schreib- oder Übergabeschritten auseinanderlaufen. Die
fail-closed-Prüfungen erkennen solche Zustände zwar, können sie aber noch nicht
in jedem Fall deterministisch und ohne manuelle Reparatur rekonstruieren.

Das ist kein bloßer Feinschliff. Solange Record-Kette und Mirror faktisch
gleichberechtigte Wahrheitsquellen sind, vergrößert jede zusätzliche
Protokolloberfläche die Zahl möglicher asymmetrischer Zwischenzustände. Deshalb
wird zwischen Arbeitspaket 1 und den weiteren nativen JSON-Arbeitspaketen ein
kleines, eigenständig review- und mergebares Stabilisierungspaket 1.1
eingeschoben.

### Verbindliche Architekturziele

1. Die append-only Record-Kette wird als einzige fachliche Source of Truth
   festgelegt.
2. State, Audit und andere lesefreundliche Sichten werden ausschließlich
   deterministisch aus der Record-Kette projiziert. Sie dürfen keine
   eigenständige fachliche Entscheidung enthalten, die nicht aus Records
   reproduzierbar ist.
3. Entscheidungsrecord und fachlicher Zustandswechsel werden als eine
   replayfähige Transition behandelt. Ein Prozessabbruch an einer beliebigen
   Schreibgrenze darf weder eine zweite Entscheidung erzeugen noch eine
   bereits persistierte Entscheidung verlieren.
4. Unterbrochene Writes werden beim Start beziehungsweise Resume automatisch
   und eindeutig aus der Record-Kette rekonstruiert. Mehrdeutige oder
   beschädigte Ketten bleiben mit konkreter Diagnose fail-closed.
5. Structured-v1- und Legacy-state-v3-Pfade werden explizit getrennt. Ein
   Legacy-Lauf darf weder implizit strukturierte Recordinvarianten übernehmen
   noch durch synthetische, nicht persistierte Records wie ein Structured-Lauf
   erscheinen.
6. Standardlogging zeigt nur fachlich relevante Übergänge, aktive Rolle,
   Validierungsstatus, Gategrund, Resumezeitpunkt und Endergebnis. Cachetreffer,
   wiederholte Evidenzkompaktierung und technische Recorddetails erscheinen
   nur mit `--verbose` beziehungsweise auf Debug-Level.
7. Benutzerfreigaben für Pfade werden mit kanonischen pfadspezifischen
   Inhalts-/Diffdigests persistiert. Unveränderte externe Pfade dürfen über
   sachfremde Gesamtfingerprintänderungen hinweg wiederverwendet werden;
   geänderte Pfadinhalte bleiben zwingend erneut freigabepflichtig.

### Verbindliche Kostenbremsen

Der Lauf `20260819-152415Z` zeigte, dass technische Resumierbarkeit allein
nicht genügt: Innerhalb von ungefähr zwei Stunden verbrauchten wiederholte
Claude-Slice- und Finalreviews praktisch ein vollständiges Fünf-Stunden-
Kontingent. Einzelne Aufrufe übertrugen etwa 401.000 beziehungsweise 559.000
Zeichen und benötigten 20 beziehungsweise 27 interne Turns. Paket 1.1 muss
deshalb folgende Kostenregeln als ausführbare Verträge behandeln:

1. Pro Fingerprint wird die vollständige branchweite Evidenz höchstens einmal
   erzeugt und inhaltsadressiert wiederverwendet. Identische Resumes dürfen
   weder das Paket neu materialisieren noch denselben Reviewer erneut starten.
2. Korrekturreviews erhalten nur das fingerprintgebundene Korrekturdelta, die
   offenen Findings, ihre Akzeptanztests, die relevante Attestierung und einen
   kleinen unveränderlichen Kontextmanifest. Die vollständige Branch-Evidenz
   wird nicht erneut übertragen.
3. Verwaltete Auditprosa und bereits persistierte Reviewertexte werden nicht
   wiederholt als Modelleingabe eingebettet. Der Reviewer erhält strukturierte
   Records, ein Pfad-/Hashmanifest und gezielte relevante Hunks.
4. Formal reparierbare Antwortfehler wie fehlende oder falsch angeordnete
   Pflichtmarker werden lokal deterministisch normalisiert. Ein zweiter
   Claude-Aufruf ist nur zulässig, wenn die fachliche Bedeutung tatsächlich
   mehrdeutig ist; Grund und Zusatzkosten werden separat protokolliert.
5. Alle Codex-Defektkandidaten werden gemäß P2-FU-020 in genau einem
   Claude-Finalreview vollständig disponiert. Ein Kandidat darf nicht erst in
   einer späteren Vollrunde stillschweigend als neuer Blocker erscheinen.
6. Für jedes Arbeitspaket werden vor dem ersten Providerstart harte Budgets für
   Revieweraufrufe, übertragene Zeichen/Bytes, interne Turns und wiederholte
   Vollreviews festgelegt. Budgetüberschreitungen erzeugen eine typisierte,
   resumierbare Diagnose statt unkontrolliert weiterer Aufrufe.
7. Providertelemetrie weist tatsächliche Eingabe-, Ausgabe- und Turnmengen pro
   logischer Operation sowie kumuliert pro Arbeitspaket aus. Unbrauchbare
   Teilwerte wie die beobachtete Anzeige `input_tokens=6` dürfen nicht als
   Gesamtnutzung dargestellt werden.
8. Sonnet mit Effort `high` und die adversariale Prüftiefe bleiben erhalten;
   Einsparungen erfolgen durch kleinere, deduplizierte und strukturierte
   Evidenz sowie weniger Wiederholungsaufrufe, nicht durch eine stille
   Qualitätsabsenkung.

Die Abnahme verlangt zusätzlich einen synthetischen Lauf mit mindestens zwei
Korrekturrunden. Dabei darf pro eindeutigem Fingerprint höchstens ein
vollständiger Claude-Finalreview stattfinden, eine rein formale
Vertragsreparatur darf keinen Providerprozess starten, und die kumulierte
Budgetabrechnung muss aus den einzelnen Operationen exakt rekonstruierbar sein.

### Vorgeschlagener begrenzter Zuschnitt

Das Paket soll klein bleiben und keine neuen Agentenantwortschemata oder
Providerfunktionen einführen. Vorgesehen sind höchstens drei Slices:

1. **Autorität und Projektion:** Record-Autorität explizit machen; State- und
   Auditprojektion aus einer gemeinsamen deterministischen Replayfunktion
   ableiten; verbleibende unabhängige Mirror-Schreibpfade inventarisieren und
   beseitigen.
2. **Atomare/replayfähige Übergänge:** Entscheidung, Findingtransition,
   Attestierung, Gate, Commitbindung, Work-Unit-Wechsel und Completion über
   idempotente Transition-IDs und definierte Checkpoints rekonstruierbar
   machen.
3. **Recovery, Crash-Matrix und Betriebsoberfläche:** systematische
   Unterbrechungstests, Legacy-Abgrenzung, kompaktes Standardlogging und
   dokumentierter Diagnosemodus ergänzen.

Die genaue Dateigrenze muss in einem repository-grounded Arbeitsplan bestimmt
werden. Das Paket darf nicht als Gelegenheit für allgemeine Refactorings oder
den vorgezogenen nativen JSON-Cutover verwendet werden.

### Erforderliche Crash- und Replay-Matrix

Für jede fachliche Transition sind mindestens folgende Unterbrechungspunkte
synthetisch zu testen:

1. vor dem Recordappend;
2. nach Erstellung einer temporären Recorddatei, aber vor atomarer
   Veröffentlichung;
3. nach Recordveröffentlichung, aber vor Stateprojektion;
4. nach Stateprojektion, aber vor Auditprojektion;
5. nach Auditprojektion, aber vor dem nächsten Work-Unit- oder Agentenschritt;
6. während des Wechsels in eine Korrektur-Work-Unit;
7. während Commitbindung und Abschluss/Completion;
8. beim wiederholten Resume nach bereits vollständig persistierter
   Transition.

Jeder Fall muss nach Neustart entweder denselben eindeutigen Zustand ohne
zweiten Provideraufruf/Record rekonstruieren oder mit einer konkreten,
maschinenlesbaren Korruptionsdiagnose anhalten. Manuelle Änderungen an
`.orchestrator/state.json`, Auditdateien oder Recorddateien sind kein
zulässiger Recoverypfad.

### Abnahmekriterien für Paket 1.1

- Record-Replay erzeugt State und Audit deterministisch und idempotent.
- Kein normaler Resume hängt von der Reihenfolge unabhängiger Mirror- und
  Recordwrites ab.
- Korrekturübergang, Quota-/Instanzpause, Nutzer-Gate, Slice-Commit und
  Finalreview besitzen jeweils positive, negative und Crash-/Resume-Tests.
- Structured- und Legacy-Läufe verwenden nachweislich getrennte Invarianten.
- Standardlogs enthalten keine wiederholten Cache- oder
  Evidenzkompaktierungszeilen; `--verbose` bewahrt die vollständige technische
  Diagnose.
- Ein bereits genehmigter externer Pfad löst bei unverändertem
  pfadspezifischem Digest kein wiederholtes Gate aus; jede Inhaltsänderung,
  Umbenennung oder Herkunftsabweichung bleibt fail-closed und verlangt eine
  neue Freigabe.
- Mindestens drei aufeinanderfolgende repräsentative End-to-End-Läufe
  (Normalabschluss, Korrekturrunde und Unterbrechung/Resume) enden ohne
  manuelle State-, Record- oder Auditkorrektur.
- Erst nach erfüllter Abnahme beginnt das nächste Arbeitspaket zur Erweiterung
  der nativen Agenten-JSON-Schnittstellen.

### Persistenz nach Abschluss von Arbeitspaket 1

Nach erfolgreichem Abschluss des laufenden Arbeitspakets wird diese
Entscheidung in
`docs/internal/ORCHESTRATOR_ROADMAP_PHASE_2_PLUS.md` zwischen Arbeitspaket 1
und dem nächsten JSON-Paket eingeordnet. Die Details werden in einem separaten
Dokument, voraussichtlich
`docs/internal/PHASE_2_ARBEITSPAKET_1_1_STABILISIERUNG.md`, persistiert und als
eigener Dokumentationscommit gesichert. Erst danach wird daraus ein
ausführbarer Inbox-Auftrag erzeugt.
