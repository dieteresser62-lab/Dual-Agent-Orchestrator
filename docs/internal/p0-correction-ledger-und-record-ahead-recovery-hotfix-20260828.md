# P0-Hotfix: vollständiges Finding-Ledger und schreibfeste Codex-Recovery

Datum: 28. August 2026  
Branch: `feature/plan-only-finding-carry-forward`  
Betroffener Lauf: `watch-20260828-113929.322399Z-a1f9abaf4ade`

## Anlass

Slice 02 des Arbeitsplans `plan-only-finding-handoffverlust` brach nach einer erfolgreichen Codex-Korrektur ab. Claude hatte im Slice-Review vier Findinglinien geführt; nur `C-03` war als offener Blocker an Codex gebunden. Codex beantwortete diesen Blocker korrekt. Beim anschließenden Checkpoint meldete die Dual-Write-Kontrolle dennoch `MIRROR-AMBIGUOUS`, weil der State-Mirror nur noch `C-03` enthielt, während die autoritative Record-Kette `C-01` bis `C-04` unverändert bewahrte.

Die beiden automatischen Wiederholungen starteten Codex nicht erneut. Sie konnten die persistierte Antwort aber ebenfalls nicht übernehmen, weil der neu aufgebaute Request den durch Codex veränderten Arbeitsbaum fingerprintete und deshalb eine andere Request-ID erhielt. Nach drei Versuchen wurde die Aufgabe nach `outbox/failed` verschoben.

Die autoritative Record-Kette und der persistierte State wurden für diesen Hotfix nicht manuell verändert.

## Ursache 1: Transportteilmenge ersetzte das Gesamtledger

Eine Correction-Anfrage transportiert absichtlich nur die Findings, die der aktuelle Korrektur-Work-Unit beantworten darf. Im betroffenen Fall war das ausschließlich `C-03`. Der validierte Codex-Result enthielt folgerichtig ebenfalls nur `C-03`. `WorkflowEngine._run_codex` schrieb diese Teilmenge anschließend als vollständige `history.findings` zurück. Dadurch verschwanden alle nicht angebotenen Linien aus dem Mirror.

## Reparatur 1: gebundener Merge statt Ersetzung

`src/workflow.py` mischt das Resultat jetzt anhand der Finding-ID in das vollständige autoritative Ledger ein.

Dabei gelten fail-closed folgende Invarianten:

- Das vollständige Ledger, die angebotene Teilmenge und das Resultat dürfen keine doppelten Finding-IDs enthalten.
- Angebotene und zurückgegebene IDs müssen exakt übereinstimmen.
- Jede angebotene Findinglinie muss vor der Antwort identisch im vollständigen Ledger vorkommen.
- Nicht angebotene Findinglinien bleiben in Inhalt, Status und Reihenfolge unverändert.
- Ausschließlich die angebotenen Linien werden durch ihre validierten Codex-Antworten ersetzt.

Der Regressionstest bildet den Incident mit `C-01` bis `C-04` nach: Nur der Blocker `C-03` wird transportiert und beantwortet; danach enthält das Ledger weiterhin alle vier Linien.

## Ursache 2: Recovery baute den Request aus dem veränderten Arbeitsbaum neu

Die native Request-ID bindet unter anderem den aktuellen Repository-Fingerprint. Nach einem schreibenden Codex-Lauf ist dieser Fingerprint erwartungsgemäß ein anderer. Die Record-ahead-Recovery validierte die bereits persistierte Antwort jedoch gegen den neu aufgebauten Request. Damit war gerade eine erfolgreiche schreibende Operation nach einem nachgelagerten Absturz nicht wiederaufnehmbar.

## Reparatur 2: Originalrequest vor Providerstart persistieren

`src/orchestrator.py` persistiert vor jedem nativen Codex-Providerstart ein unveränderliches Recovery-Bundle unter:

`.orchestrator/artifacts/<run-id>/native-agent-requests/work-unit-<id>-<operation>-round-<round>.json`

Das Bundle enthält den kanonischen Request, dessen request-spezifisches Response-Schema und alle gegebenenfalls ausgelagerten Evidenzassets. Beim Resume wird genau dieses Bundle neu typisiert und vollständig validiert. Ein abweichender, beschädigter oder nichtkanonischer Inhalt führt fail-closed zum Abbruch; ein bestehendes Bundle wird niemals überschrieben.

Damit kann sowohl eine Raw-Response-ahead- als auch eine AgentResult-ahead-Situation gegen den Originalrequest wiederaufgenommen werden, obwohl Codex den Arbeitsbaum bereits verändert hat. Es erfolgt kein zweiter Providerstart.

## Rückwärtskompatibilität für den aktuellen Poison-Lauf

Der betroffene Lauf entstand vor Einführung des Request-Bundles. Für genau diesen bereits persistierten Zustand nutzt die Recovery das vor dem Providerstart append-only gespeicherte `ProviderAttemptPayload.binding_fingerprint` zusammen mit dem ebenfalls append-only gespeicherten `AgentResultPayload.request_id` und `response_sha256`.

Dieser Fallback ist bewusst enger als der neue Normalweg:

- Er greift nur, wenn bereits ein eindeutig bestimmbarer autoritativer AgentResult-Fakt für den gebundenen Work-Unit, Schritt und die Runde vorhanden ist. Mehrere Records werden ausschließlich für die unten dokumentierte, vom ersten Hotfix-Wiederanlauf erzeugte Altdublette akzeptiert, wenn Payload, logische Identität und die jeweilige Fingerprint-Idempotenzbindung vollständig übereinstimmen.
- Er verlangt den dazu zeitlich vorhergehenden nativen Codex-Providerstart desselben Work-Units und derselben Operation.
- Die kanonische Raw Response muss weiterhin exakt den Digest des AgentResult-Records besitzen.
- Der Resultvertrag wird gegen den aktuellen semantisch identischen Korrekturkontext mit dem originalen Provider-Binding validiert.
- Der aktuelle Arbeitsbaum darf nach dem AgentResult weitergedriftet sein. Diese Drift legitimiert das alte Resultat nicht für neue Dateien; sie wird nach der idempotenten Mirror-Vervollständigung durch die normale Slice-Grenzprüfung vor dem nächsten Revieweraufruf bewertet und bei unerwarteten Pfaden als fingerprintgebundenes Nutzergate angehalten.
- Neue Läufe verwenden stattdessen immer das vollständig persistierte Originalbundle.

Damit kann der vorhandene Lauf nach positivem Review aus seinem Poison-Zustand wieder in die Inbox übernommen werden, ohne die Record-Kette oder `.orchestrator/state.json` zu erfinden oder manuell zu reparieren.

Der Legacy-Fallback besitzt absichtlich eine geringere Prüftiefe als der neue Bundle-Normalweg: Für einen Lauf, der vor Einführung der Bundle-Persistenz entstand, kann kein nie gespeichertes Requestdokument nachträglich rekonstruiert werden. Deshalb wird die bereits beim Originallauf validierte Antwort über die append-only gespeicherte Kombination aus Request-ID, Response-Digest, AgentResult-Payload und ursprünglichem Provider-Attempt-Binding autorisiert. Der Fallback führt keine erneute Writer-Schema-Validierung gegen ein Originalrequestdokument durch. Neue Läufe dürfen diesen reduzierten Pfad nicht verwenden, weil ihr vollständiges Bundle vor Providerstart persistiert wird.

## Korrektur nach Claude-Review `b2e46eaf`

Claude bestätigte beide ursprünglichen Kernreparaturen, fand aber im realen Absturzfenster einen weiteren Blocker: Der Poison-Lauf enthält bereits den `responded`-Record für `C-03`. Das beim Resume replayte Finding trägt diese Response deshalb schon. Wäre die persistierte Disposition gegen diesen heutigen Findingstand erneut ausgewertet worden, hätten Mirror und Record-Kette eine zweite, nie erfolgte Providerantwort erhalten.

Die Recovery rekonstruiert den Findingstand nun aus dem autoritativen Record-Präfix unmittelbar vor dem ursprünglichen `ProviderAttemptPayload(phase="started")`. Nur dieser requestzeitliche Stand wird zur erneuten Domainauswertung der unveränderten Raw Response benutzt. Anschließend gilt:

- Fehlt der Response-Record nach einem Absturz zwischen AgentResult und Finding-Transition, enthält das Resultat genau die eine neue Response und die Persistenz ergänzt genau den ursprünglichen indexgebundenen Record.
- Existiert der Response-Record bereits, entspricht das requestzeitlich ausgewertete Resultat dem heutigen replayten Finding. `persist_native_codex_contract` sieht keine zusätzliche Response und schreibt keinen zweiten Record.
- Ein fehlender oder nicht replaybarer Requestzeitpunkt bricht fail-closed ab; die Recovery erfindet keine Findinghistorie.

Der Regressionstest deckt jetzt beide Absturzfenster nacheinander ab und behauptet nach wiederholter Recovery weiterhin genau einen `responded`-Record sowie genau eine Response im zurückgegebenen Finding.

Zusätzlich werden ausgelagerte Evidenzassets bei der Bundle-Rekonstruktion wieder durch ihren tatsächlich annotierten nativen Assettyp konstruiert. Dessen `__post_init__` prüft Inhalt, Bytezahl und SHA-256 gegeneinander. Eine kanonisch neu serialisierte Manipulation nur des Assetinhalts wird im Regressionstest fail-closed abgelehnt.

Die von Claude benannte Reviewer-Asymmetrie bleibt ein separates, nicht im realen Poison-Lauf realisiertes Restrisiko: Reviewer schreiben den Arbeitsbaum nicht, und der vorhandene Pre-Policy-Recoverypfad bindet persistierte Reviewentscheidungen bereits vor der Prüfung nachträglicher Repositorydrift. Eine providerübergreifende Persistenz des vollständigen Review-Requestkontexts erfordert einen eigenen, gebundenen Änderungsscope und wird hier nicht als scheinbar erledigt dargestellt.

## Korrektur nach dem ersten freigegebenen Wiederanlauf

Der Wiederanlauf am 28. August 2026 um 17:48 Uhr übernahm die Codex-Korrektur wie vorgesehen ohne neuen Providerstart. Danach traf jedoch ein weiteres Crashfenster: `recover_pending_native_codex` persistierte den vorhandenen AgentResult mit dessen ursprünglichem Fingerprint idempotent erneut. `WorkflowEngine._run_codex` ruft nach jeder Rückgabe anschließend noch einmal die normale Persistenz auf. Dieser zweite Aufruf leitete den Idempotenzschlüssel aus dem inzwischen veränderten Arbeitsbaum ab und schrieb denselben semantischen AgentResult deshalb als zweiten Record. Erst die folgende Wiederholung des bereits vorhandenen `finding-response:C-03:1` erkannte den abweichenden Fingerprint und brach mit `structured artifact differs semantically from the state-v3 statement` ab. Die beiden Watch-Retries sahen danach zwei AgentResult-Records und poisonierten deterministisch.

Die Korrektur bindet jede erneute Persistierung eines bereits vorhandenen, in Payload und logischer Identität identischen AgentResult-Fakts an den frühesten dauerhaften Record, dessen Fingerprint und dessen Idempotenzschlüssel. Dadurch bleiben sowohl der interne Recovery-Schreibschritt als auch der nachgelagerte normale Workflow-Schreibschritt idempotent, selbst wenn der Arbeitsbaum inzwischen einen anderen Fingerprint besitzt.

Der reale Lauf enthält durch den fehlgeschlagenen Wiederanlauf bereits zwei Records. Diese append-only Evidenz wird nicht gelöscht oder umgeschrieben. Recovery akzeptiert sie nur unter einer engen Altfallregel:

- Beide Records müssen dieselbe vollständige `AgentResultPayload`, dieselbe logische Identität, Request-ID und Response-SHA besitzen.
- Für jeden Record muss der eigene Idempotenzschlüssel exakt aus seinem gespeicherten Fingerprint, Request-ID und Response-SHA ableitbar sein.
- Der früheste Record bleibt die kanonische Bindung für weitere idempotente Persistenz.
- Jede semantische Abweichung oder fehlerhafte Idempotenzbindung bleibt ein fail-closed `WorkflowExecutionError`.

Der Regressionstest bildet nun beide Ebenen nach: erst Recovery plus den zweiten normalen Workflow-Persistenzaufruf bei geändertem Arbeitsbaum, wobei genau ein AgentResult bestehen bleiben muss; anschließend die bereits durch den alten Defekt verschmutzte Kette mit zwei semantisch identischen Records, die ohne dritten Record fortgesetzt wird. Eine divergierende Dublette wird ausdrücklich abgewiesen.

## Umfang und Abgrenzung

Der Hotfix erweitert den ursprünglichen Slice-02-Pfad ausdrücklich um:

- `src/workflow.py`
- `tests/test_workflow.py`
- die Recovery-Ergänzungen in den bereits geänderten Dateien `src/orchestrator.py` und `tests/test_orchestrator_runtime.py`
- dieses Hotfix-Dokument

Diese Erweiterung ist durch die ausdrückliche Hotfix-Anweisung des Benutzers autorisiert. Die bereits vorhandenen, noch nicht committeden Slice-02-Änderungen anderer Dateien wurden nicht zurückgesetzt oder überschrieben.

Die irreführende Poison-Sammeldiagnose, die nur den letzten Folgefehler statt der ersten Ursache hervorhebt, ist ein eigenständiger Diagnosemangel. Sie ist nicht Teil dieses P0-Reparaturpfads, weil sie weder Ledgerverlust noch Recovery-Unfähigkeit verursacht. Sie sollte separat priorisiert werden.

## Verifikation

- Fokussierte Regressionstests für Ledger-Merge, Originalrequest-Recovery, unveränderliche Requestpersistenz, beide Finding-Response-Crashfenster und Legacy-Fallback: grün.
- `tests/test_workflow.py`, `tests/test_orchestrator_runtime.py` und `tests/test_language_consistency.py`: nach der zweiten Recoverykorrektur `185 passed in 30.33s`.
- Vollständige Suite gemäß `AGENTS.md`: nach der zweiten Recoverykorrektur `1127 passed in 140.91s`.

## Wiederanlauf nach positiver Claude-Freigabe

Erst nach positivem Review:

1. Die Poison-Aufgabe kontrolliert wieder unter ihrem ursprünglichen Namen in `inbox/` bereitstellen.
2. Die zugehörige `.poison.error.json` als Incident-Evidenz erhalten; keine State- oder Recorddatei manuell bearbeiten.
3. `run_task --watch` starten.
4. Im Log prüfen, dass die Codex-Korrektur aus Record-ahead-Persistenz übernommen wird und kein neuer `provider attempt started ... codex_correction` erscheint.
5. Prüfen, dass der nächste Checkpoint alle vier Findinglinien erhält.
6. Da der Hotfix selbst `src/workflow.py`, `tests/test_workflow.py` und dieses Dokument außerhalb des ursprünglich persistierten Slice-02-Pfads ergänzt, ist anschließend ein fingerprintgebundenes `UNEXPECTED-PATH`-Nutzergate zu erwarten. Dieses Gate anhand des tatsächlich erhobenen Fingerprints und der exakten Pfade entscheiden; nicht durch State-Manipulation umgehen.
7. Erst nach dieser expliziten Scopebindung darf der Ablauf zu Claudes erneutem Slice-Review übergehen.

Ein Commit, Merge oder Watch-Restart ist nicht Bestandteil dieses Hotfix-Schritts und erfolgt erst nach Review beziehungsweise ausdrücklicher Freigabe.
