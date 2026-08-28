# Claude-Review: P0-Hotfix Finding-Ledger und schreibfeste Codex-Recovery

**Reviewer:** Claude (manuell, adversarial, read-only) · **Datum:** 28. August 2026 ·
**Snapshot:** Arbeitsbaumdiff `b2e46eafc6352f9c6f0fb647968dd3ebdffc92d5`

## 1. Ergebnis vorab

**Verweigert.** Ein Blocker.

Beide benannten P0-Kernursachen sind sachlich geschlossen — das habe ich einzeln
und mit eigenen Gegenproben nachgewiesen. Der Ledger-Merge ist korrekt und in
acht Negativproben fail-closed; die Recovery bindet die validierte Antwort
wieder an den Originalrequest und startet keinen zweiten Providerlauf.

Verweigert wird trotzdem, weil die vom Hotfix ausdrücklich zugesagte
*idempotente* Vervollständigung der Finding-Responses auf genau dem Lauf nicht
hält, den der Hotfix retten soll. Der vorhandene Poison-Lauf ist nach dem
bereits geschriebenen `finding-response:C-03:1`-Record abgestürzt. Beim
Wiedereinwerfen würde die Recovery eine **zweite** Codex-Disposition für `C-03`
in die append-only Kette schreiben. Das ist nicht rückholbar und macht eine
einmalige Provideranswort dauerhaft als zwei Antworten sichtbar.

| ID | Schwere | Kern |
|---|---|---|
| `H-01` | **BLOCKER** | Recovery nach bereits persistierter Finding-Response schreibt eine doppelte Disposition; `prior_count` wird aus dem replayten Ledger statt aus dem Requestzeitpunkt bestimmt, wodurch der indexbasierte Idempotenzschlüssel verfehlt wird. |
| `H-02` | Restrisiko | Recovery-Evidenzassets werden als `SimpleNamespace` rekonstruiert und umgehen die Digestprüfung von `NativeCodexEvidenceAsset`. Ohne Autoritätsverlust, aber typunsauber. |
| `H-03` | Restrisiko | Der Legacy-Fallback validiert die Antwort nicht mehr gegen ein Requestdokument, sondern vertraut der Recordkette. Bewusst und eng begrenzt, aber dokumentativ stärker klingend als er ist. |
| `H-04` | Restrisiko | Die Bundle-Persistenz gibt es nur für den Codex-Pfad; der Reviewer-Recoverypfad bindet weiterhin gegen einen neu gebauten Request. |

## 2. Selbst erhobener Stand und Reviewgrenze

| Merkmal | Gemessen | Erwartet | |
|---|---|---|---|
| Branch | `feature/plan-only-finding-carry-forward` | identisch | ✓ |
| `HEAD` | `dd6ad0c3e13a9ceb3dbc3c73ce89f7bc1c4aa10a` | identisch | ✓ |
| Merge-Base zu `master` | `1acf1fd3abd153f3a3ccfa66e91ac1defa95f22a` | identisch | ✓ |
| Diff `src/workflow.py` + `tests/test_workflow.py` | `f317502f8c234ea3e8c84fe56e55c907ce7e7a79` | identisch | ✓ |
| Diff `src/orchestrator.py` + `tests/test_orchestrator_runtime.py` | `161c492bcd2e0925dc7295bb515e876f53a0829a` | identisch | ✓ |
| Arbeitsbaum | 12 geänderte, 2 ungetrackte Pfade | Slice-02 plus Hotfix | ✓ |

Beide Navigationshashes stimmen exakt. Der Gesamtdiff hat den stabilen
Snapshot-Suffix `b2e46eaf`, den dieses Dokument im Namen trägt. Eine isolierte
Commitgrenze existiert nicht und wird von mir nicht behauptet: Der Hotfix liegt
zusammen mit der noch nicht committeten Slice-02-Arbeit im selben Arbeitsbaum.

**Reviewgrenze.** Kein `run_task`, kein Watcher, kein Bootstrap, kein Provider-
oder Netzaufruf, keine Canary. Nichts committet, gestaged, verschoben oder
migriert. State, Checkpoints, Recordkette, Inbox und Outbox wurden
ausschließlich gelesen. Die einzige Schreiboperation ist dieses Dokument.
Ausgeführt habe ich nur vier eng fokussierte Pytest-Knoten und read-only
Python-Proben. Die vollständige Suite habe ich nicht gefahren; die Codex-Angaben
(`147 passed`, `1127 passed in 137.99s`) sind Fremdangaben und keine
Attestierung, meine eigenen Läufe ebenso wenig.

## 3. Eigene Gegenproben

| # | Gegenprobe | Ergebnis |
|---|---|---|
| P1 | Branch, HEAD, Merge-Base, Status, beide Diffhashes | wie erwartet |
| P2 | `_merge_request_finding_subset` mit dem realen Fall C-01…C-04, nur C-03 angeboten | alle vier Linien erhalten, Reihenfolge stabil, nicht angebotene bitgleich |
| P3 | acht Negativproben gegen den Merge | acht von acht fail-closed |
| P4 | Merge mit leerem und mit vollem Angebot | Ledger unverändert |
| P5 | Reihenfolge Bundle-Persistenz vs. Providerstart in `invoke_codex` | Persistenz strikt davor |
| P6 | Selbstvalidierung des rekonstruierten Bundles (`__post_init__`) | Digest, Request-ID, Kanonizität, Kontextprojektion, Evidenzmetadaten |
| P7 | Recordkette des Poison-Laufs: AgentResult für `agent-3-codex_correction-2` | genau einer, `request_id` `native-codex-request-059b343e…` |
| P8 | `response_sha256` gegen die kanonische Raw Response | `ecac2b26…` identisch |
| P9 | vorhergehender `started`-Attempt | genau einer: 12:24:09, provider=codex, wu=3, `codex_correction`, `binding_fingerprint=0bd309ba…` |
| P10 | Fingerprint des AgentResult-Records | `e0e6ec33…` — nachweislich der Drift, an dem die alte Prüfung scheiterte |
| P11 | Replay des Poison-Ledgers | C-01, C-02, C-03 je **eine** Response, C-04 keine |
| P12 | `completed_side_effects` von Work-Unit 3 | leer → der Korrekturschritt wird beim Resume erneut betreten |
| P13 | vier fokussierte Pytest-Knoten (Merge, Recovery, Response-Vervollständigung, kombinierter Mirror-Guard) | `4 passed in 1.93s` |
| P14 | Transitionsschleife in `persist_native_codex_contract` | iteriert nur über das Resultat, hängt nur neue Responses an |
| P15 | Slice-02-Dateien (`artifact_migration`, `plan_handoff`, `task_contract`, `workflow_state`) | 216 Einfügungen unverändert erhalten |
| P16 | Suche nach einer Provider-Kopplungsratsche | im Repository nicht vorhanden — nichts, was aufgeweicht werden könnte |

## 4. Disposition der beiden Kernursachen

### Ursache 1 — Teilmenge ersetzte das vollständige Ledger: **geschlossen**

Die Verengung bleibt korrekt erhalten. `src/workflow.py:3139-3145` filtert die
Correction-Anfrage weiterhin exakt auf `state.current_work_unit.open_findings`,
und `native_codex_contract.py:555` erzwingt weiterhin, dass das Resultat genau
diese angebotene Menge dispositioniert — nicht mehr und nicht weniger.

Neu ist ausschließlich das Rückschreiben: Statt
`history = replace(history, findings=result.findings)` mischt
`_merge_request_finding_subset` das Resultat ID-weise in das autoritative
Ledger. Der Realfall aus dem Incident verhält sich jetzt richtig:

```
Ergebnis: C-01 OBSERVATION OPEN (0 Responses)
          C-02 OBSERVATION OPEN (0)
          C-03 BLOCKER     OPEN (1)
          C-04 OBSERVATION OPEN (0)
Reihenfolge: C-01, C-02, C-03, C-04
nicht angebotene Records bitgleich: True
```

Acht Negativproben greifen sämtlich: fremde ID im Resultat, weggelassene
angebotene ID, zusätzliche ID, ein vom Ledger abweichender angebotener Record,
eine angebotene ID ohne Ledgereintrag sowie doppelte IDs in Ledger, Angebot und
Resultat. Besonders wertvoll ist die Gleichheitsprüfung angeboten↔autoritativ:
Sie verhindert, dass eine manipulierte oder veraltete Teilmenge eine
Ledgerlinie überschreibt. Ein Resultat kann konstruktiv keine ID einschleusen,
die nicht schon im Ledger stand.

Die Record-Persistenz ist unangetastet und erfolgt weiterhin **vor** dem Merge
gegen das vollständige vorherige Ledger (`workflow.py:1278-1289`); die
Transitionsschleife hängt nur neue Responses an. Der append-only Vertrag ist
nicht geschwächt.

### Ursache 2 — Recovery scheiterte am gedrifteten Fingerprint: **geschlossen**

Für neue Läufe persistiert `_persist_native_agent_request_bundle` das
vollständige Bundle — kanonischer Request, request-spezifisches Response-Schema
und Evidenzassets — **vor** dem Providerstart (`invoke_codex`, unmittelbar vor
`run_native_codex_agent_checked`, also vor Input-Messung, Attempt-Start und
Prozessstart). Beim Resume wird es neu typisiert; die
`__post_init__`-Selbstvalidierung von `NativeCodexRequestBundle` prüft dabei
Kanonizität, Request-ID gegen den neu berechneten Bindingdigest, das
Response-Schema-Digest und die vollständige Kontextprojektion. Eine seit dem
Providerlauf veränderte angebotene Findingmenge fiele dort auf.

Ein bestehendes Bundle wird nie überschrieben: Schreiben erfolgt mit `"x"`, und
sowohl bei Existenz als auch bei `FileExistsError` wird auf Inhaltsgleichheit
geprüft und andernfalls fail-closed abgebrochen; anschließend wird das
Geschriebene rückgelesen und erneut verglichen.

**TOCTOU.** Ich habe die Lücken einzeln durchgespielt. Absturz zwischen
Bundle-Persistenz und Attempt-Start hinterlässt ein Bundle ohne Raw Response;
`_recover_native_codex_output` kehrt dann früh mit `None` zurück, weil die
Rohantwort fehlt — keine Scheinrecovery. Die umgekehrte Reihenfolge ist
konstruktiv ausgeschlossen, weil die Persistenz zuerst läuft. Absturz zwischen
Raw Response und AgentResult führt in den Zweig ohne `candidates`, in dem gegen
das Bundle voll validiert wird. Eine ausnutzbare Lücke habe ich nicht gefunden.

**Legacy-Pfad.** Für vor dem Hotfix entstandene Läufe wird die Request-ID aus
dem `AgentResultPayload` und der Fingerprint aus dem letzten vorhergehenden
`started`-`ProviderAttemptPayload` desselben Providers, Work-Units und
Operation genommen. Die Auswahl ist eindeutig: Der Schnitt `chain[:candidate_index]`
endet am konkreten AgentResult, und die Kette schreibt je Runde zuerst
`started`, dann das AgentResult, dann `succeeded` — im Incident nachweisbar
(12:24:09 / 12:31:36 / 12:31:43). Eine spätere Runde derselben Operation kann
daher nicht binden, eine fremde Operation ebenso wenig. Auf dem Poison-Lauf gibt
es exakt einen passenden Attempt.

Richtig ist auch, dass die alte Prüfung
`record.fingerprint.sha256 != expected_fingerprint` entfallen musste: Der
AgentResult-Record trägt `e0e6ec33…`, also den Baum **nach** Codex' Schreiben,
während der Request an `0bd309ba…` gebunden war. Der Ersatz — Bindung an
`response_sha256`, `request_id` und ein exakt rekonstruiertes
`expected_payload` — ist enger an der Sache als die alte Fingerprintgleichheit.
Dass `persist_native_codex_contract` die Ergänzung nun mit
`recovery_fingerprint=record.fingerprint.sha256` statt mit dem heutigen
Fingerprint schreibt, ist konsequent.

## 5. Blocker

### H-01 — Recovery schreibt eine doppelte Codex-Disposition (BLOCKER)

Der Hotfix sagt zu, die Mirror-Vervollständigung sei idempotent, und
Prüfdimension B verlangt ausdrücklich, dass „die idempotente
Finding-Response-Persistenz weiterhin vervollständigt" wird. Auf dem konkreten
Poison-Lauf gilt das nicht.

**Belegte Kette:**

1. Der Absturz erfolgte **nach** dem Response-Record. Die Kette enthält
   `finding-C-03 … action=responded` mit Idempotenzschlüssel
   `finding-response:C-03:1`, geschrieben 12:31:39; der Checkpoint scheiterte
   erst 12:31:57 (P7, P11).
2. Das Replay des Ledgers liefert deshalb **C-03 mit einer Response** (P11).
3. `completed_side_effects` von Work-Unit 3 ist leer, der Schritt wird beim
   Resume erneut betreten (P12).
4. Die Correction-Anfrage wird aus `history.findings` verengt, `previous_findings`
   ist also C-03 **mit** dieser Response.
5. Die persistierte Rohantwort enthält weiterhin genau eine Disposition für
   C-03. `_apply_dispositions` ruft `apply_finding_response`, und diese hängt
   unbedingt an (`contracts.py:554-562`, einzige Sperre ist `CLOSED`) → C-03
   hat danach **zwei** Responses.
6. `persist_native_codex_contract` berechnet
   `prior_count = len(previous_by_id["C-03"].responses) = 1` und schreibt die
   Response mit `index = 2`, also Idempotenzschlüssel
   `finding-response:C-03:2` — ein neuer Schlüssel neben dem bestehenden `:1`.

Der indexbasierte Idempotenzschlüssel ist nur dann idempotent, wenn
`prior_count` dieselbe Basis hat wie beim Originalaufruf. Damals war sie 0,
beim Resume ist sie 1. Der vorhandene Regressionstest
`test_native_codex_record_ahead_recovery_completes_finding_responses` prüft
ausschließlich den Absturz **zwischen** AgentResult und Response-Record, wo
`prior_count` noch 0 ist — genau die Variante, die ohnehin funktioniert. Der
real eingetretene Fall ist nicht abgedeckt.

**Warum das blockiert.** Die Kette ist append-only. Ein doppelter
`responded`-Record ist nicht mehr entfernbar und von einer legitimen zweiten
Korrekturrunde nicht unterscheidbar. Das erzeugt genau die Art falscher
Autorität, die der Auftrag als Blockerkriterium nennt: Der Audittrail behauptet
dauerhaft zwei Providerdispositionen, wo eine erfolgte. Der Schaden entsteht
unvermeidlich beim allerersten Wiedereinwerfen — also bei der Handlung, für die
dieser Hotfix gebaut wurde.

Nicht betroffen sind Mirror-Konsistenz und Wiederaufnahme selbst: Beide Seiten
erhalten die zweite Response, `MIRROR-AMBIGUOUS` tritt nicht erneut auf, der
Lauf würde durchlaufen.

**Ausführbarer Akzeptanztest.** Ein Test konstruiert den realen Zustand — ein
AgentResult *und* ein `finding-response:<id>:1`-Record liegen bereits in der
Kette, das replayte Ledger trägt die Response — und ruft
`recover_pending_native_codex` mit `previous_findings` aus dem Replay auf.
Danach muss gelten: Die Anzahl der `FindingTransitionPayload`-Records mit
`action == "responded"` für dieses Finding ist unverändert **1**, und das
zurückgegebene Ledger enthält für dieses Finding genau eine Response.
Umsetzungsrichtung: den Idempotenzschlüssel an die Response-Identität statt an
den laufenden Index binden, oder `prior_count` aus dem Requestzeitpunkt
ableiten.

## 6. Restrisiken ohne Blockerstatus

**H-02 — rekonstruierte Evidenzassets umgehen ihre Digestprüfung.**
`_load_native_agent_request_bundle` baut die Assets als `SimpleNamespace` und
umgeht damit `NativeCodexEvidenceAsset.__post_init__`, das `sha256 ==
_sha256_text(content)` erzwingt. Die Bundle-Selbstvalidierung prüft für
ausgelagerte Assets nur `path → (sha256, byte_count)` gegen das Manifest, nicht
den Inhalt. Ein manipuliertes Recovery-Artefakt könnte also Inhalt und Digest
divergieren lassen. Autoritätsverlust entsteht daraus nicht: Auf dem
Recoverypfad wird der Assetinhalt weder transportiert noch geschrieben; Antwort
und Kontext bleiben über Schema- und Bindingdigest gedeckt. Behebbar durch
Konstruktion echter `NativeCodexEvidenceAsset`-Instanzen.

**H-03 — der Legacy-Fallback prüft schwächer, als der Text nahelegt.** Dort ist
`validate_against_bundle = False`; die Antwort wird nicht mehr gegen ein
Requestdokument validiert, sondern nur noch über `request_id` und
`response_sha256` an die Recordkette gebunden. Das ist vertretbar, weil die
Antwort beim Originallauf bereits validiert wurde und die Kette append-only
ist — es verschiebt die Autorität aber von „gegen den Request nachgerechnet"
auf „der Kette geglaubt". Der Hotfixtext nennt den Fallback „bewusst enger",
was die Trefferbedingungen betrifft; die schwächere *Prüftiefe* benennt er
nicht. Für ein Dokument, das später als Beleg dient, wäre ein Satz dazu
angemessen.

**H-04 — Asymmetrie zwischen Codex und Reviewer.** Die Bundle-Persistenz ist
providerneutral benannt (`native-agent-requests`, `native-agent-request-bundle-v1`),
aber nur im Codex-Pfad verdrahtet. `_recover_native_review_output` bindet
weiterhin gegen einen neu gebauten Request. Praktisch weniger kritisch, weil ein
Review den Arbeitsbaum nicht verändert; sobald aber ein Reviewaufruf nach einer
schreibenden Operation abstürzt, steht dieselbe Fingerprintfalle bereit.

**H-05 — Slice-Bindung des Poison-Laufs.** Der Hotfix hat `src/workflow.py`,
`tests/test_workflow.py` und zwei Dokumente außerhalb des persistierten
Slice-Pfadsatzes ergänzt. Das ist bewusst und autorisiert; es bedeutet aber,
dass die Wiederaufnahme zwingend am Scope-Gate anhalten muss.

**Poison-Sammeldiagnose.** Der `.poison.error.json` weist nur den letzten
Folgefehler (`request-mismatch`) aus, nicht die Erstursache
(`MIRROR-AMBIGUOUS`). Auftragsgemäß melde ich das nicht als Blocker: Es
verhindert die sichere Wiederaufnahme nicht und erzeugt keine falsche
Autorität über den Lauf, sondern erschwert nur die Diagnose. Die Abgrenzung im
Hotfixdokument ist korrekt.

## 7. Wiederaufnahme des Poison-Laufs

Technisch würde die Wiederaufnahme funktionieren, und zwar so:

- Die Kette erfüllt alle Bedingungen des Legacy-Fallbacks (P7–P9). Die Recovery
  greift, ein neuer `provider attempt started … codex_correction` entsteht
  **nicht**.
- Der Merge stellt danach alle vier Findinglinien im Mirror her; der
  `MIRROR-AMBIGUOUS`-Abbruch von 14:31:57 tritt nicht erneut auf.
- Vor dem nächsten Claude-Aufruf prüft die normale Slice-Grenze den Arbeitsbaum.
  Da der Hotfix Pfade außerhalb des persistierten Slice-Scopes ergänzt hat, ist
  ein fingerprintgebundenes `UNEXPECTED-PATH`-Nutzergate zu erwarten. Ein
  sicheres Anhalten dort ist zulässig und erwünscht.

Trotzdem lautet meine Empfehlung **nicht wiedereinwerfen**, solange `H-01`
offen ist: Der erste Resume schreibt den doppelten Dispositionsrecord
unwiderruflich in die Kette. Nach Behebung von `H-01` ist die Wiederaufnahme
aus meiner Sicht sicher und benötigt keinen erneuten Codex-Lauf.

## 8. Größtes Restrisiko und realistische Bruchbedingung

Größtes Restrisiko ist die **indexbasierte Idempotenz** der
Finding-Response-Persistenz. Sie ist an eine Zählbasis gekoppelt, die zwischen
Originalaufruf und Resume wandert. `H-01` ist die erste Realisierung; dieselbe
Konstruktion trägt auch jede künftige Mehrfach-Recovery.

Nachgeordnet bleibt, dass der Legacy-Fallback der Recordkette vertraut statt
nachzurechnen, und dass Recovery-Artefakte über `SimpleNamespace` an ihren
eigenen Invarianten vorbei rekonstruiert werden.

**Realistische Bruchbedingung:** Ein Lauf stürzt erneut *nach* einer bereits
persistierten Finding-Response ab — der bisher einzige real beobachtete
Absturzzeitpunkt. Beim Resume wächst die Responsezahl bei jedem Anlauf um eins,
ohne dass ein Guard anschlägt, weil Mirror und Kette gemeinsam wachsen und
damit konsistent bleiben. Nach mehreren Anläufen zeigt der Audittrail eine
Disposition, die nie stattgefunden hat.

## 9. Pre-Mortem

In drei Monaten scheitert dieser Hotfix am wahrscheinlichsten nicht an der
Recovery, sondern an ihrem Erfolg: Weil die Wiederaufnahme jetzt zuverlässig
greift, wird sie häufiger benutzt, und jedes Mal wächst die Responsekette einer
bereits beantworteten Findinglinie. Irgendwann fragt jemand, warum Codex ein
Blocker-Finding dreimal beantwortet hat, hält das für eine echte
Mehrfachrunde — und leitet aus dem Audittrail eine Historie ab, die es nie
gab. Weil die Kette append-only ist, lässt sich das nicht mehr korrigieren,
sondern nur noch erklären.

Zweitwahrscheinlich verlässt sich eine spätere Änderung darauf, dass der
Legacy-Fallback die Antwort so streng prüft wie der Normalweg — er tut es
nicht — und lockert im Vertrauen darauf eine andere Prüfung.

Drittwahrscheinlich trifft der nächste Absturz einen Reviewaufruf statt eines
Codex-Aufrufs, und die Bundle-Persistenz, die dafür providerneutral benannt,
aber nicht verdrahtet ist, fehlt genau dort.

## 10. Entscheidung

Beide Kernursachen sind geschlossen und die Umsetzung ist an den geprüften
Stellen sauber, eng und gut abgesichert. Die Freigabe scheitert allein an
`H-01`, dessen Schaden beim ersten Wiedereinwerfen entsteht und wegen der
append-only Kette nicht rückholbar ist. Der Eingriff dafür ist klein und liegt
innerhalb des bereits geöffneten Hotfixpfads.

```text
REVIEWER: claude
FINDING: H-01 | BLOCKER | Die Recovery vervollstaendigt Finding-Responses nicht idempotent, wenn der Absturz nach dem Response-Record erfolgte. Auf dem Poison-Lauf liegt finding-response:C-03:1 bereits in der Kette; das Replay liefert C-03 daher mit einer Response, previous_findings traegt sie, apply_finding_response haengt beim Recovery-Parsing unbedingt eine zweite an, und persist_native_codex_contract berechnet prior_count gleich 1 und schreibt den neuen Schluessel finding-response:C-03:2. Der bestehende Test deckt nur den Absturz zwischen AgentResult und Response-Record ab, wo prior_count noch 0 ist. Da die Kette append-only ist, ist der doppelte Dispositionsrecord nicht entfernbar und von einer legitimen zweiten Korrekturrunde nicht unterscheidbar. | Akzeptanztest: ein Test konstruiert AgentResult plus bereits vorhandenen finding-response:<id>:1-Record, laesst das Replay die Response tragen und ruft recover_pending_native_codex; danach muss die Anzahl der responded-Transitionen fuer dieses Finding unveraendert 1 sein und das zurueckgegebene Ledger genau eine Response fuehren.
FINDING: H-02 | RESTRISIKO | Rekonstruierte Evidenzassets werden als SimpleNamespace gebaut und umgehen die Digestpruefung von NativeCodexEvidenceAsset; die Bundle-Selbstvalidierung prueft fuer ausgelagerte Assets nur Pfad, sha256 und byte_count gegen das Manifest, nicht den Inhalt. Ohne Autoritaetsverlust, weil der Assetinhalt auf dem Recoverypfad weder transportiert noch geschrieben wird.
FINDING: H-03 | RESTRISIKO | Der Legacy-Fallback setzt validate_against_bundle auf False und bindet die Antwort nur ueber request_id und response_sha256 an die Recordkette statt sie gegen ein Requestdokument nachzurechnen; das Hotfixdokument benennt die engeren Trefferbedingungen, nicht die geringere Pruueftiefe.
FINDING: H-04 | RESTRISIKO | Die Bundle-Persistenz ist providerneutral benannt, aber nur im Codex-Pfad verdrahtet; der Reviewer-Recoverypfad bindet weiterhin gegen einen neu gebauten Request und traegt dieselbe Fingerprintfalle latent weiter.
REVIEW_EVIDENCE: selbst erhobener Branch-, HEAD-, Merge-Base- und Statusstand mit exakt uebereinstimmenden Navigationsdiffhashes f317502f und 161c492b sowie Snapshot b2e46eaf; Merge des Ledgers am realen Fall C-01 bis C-04 mit nur angebotenem C-03 verifiziert und mit acht Negativproben fail-closed falsifiziert; Reihenfolge der Bundle-Persistenz vor Provider-Input-Messung, Attempt-Start und Prozessstart geprueft; Selbstvalidierung des rekonstruierten Bundles ueber Digest, Request-ID, Kanonizitaet, Response-Schema-Digest und Kontextprojektion nachvollzogen; TOCTOU-Fenster einzeln durchgespielt ohne ausnutzbare Luecke; Recordkette des Poison-Laufs verifiziert mit genau einem AgentResult, uebereinstimmendem response_sha256 der kanonischen Rohantwort und genau einem vorhergehenden started-Attempt mit binding_fingerprint 0bd309ba; Replay des Poison-Ledgers gemessen mit je einer Response fuer C-01 bis C-03; completed_side_effects der Work-Unit 3 leer; vier fokussierte Pytest-Knoten gruen; Slice-02-Aenderungen in vier Dateien unversehrt; keine Provider-Kopplungsratsche im Repository vorhanden, die haette aufgeweicht werden koennen | groesstes Restrisiko: die indexbasierte Idempotenz der Finding-Response-Persistenz haengt an einer Zaehlbasis, die zwischen Originalaufruf und Resume wandert, weshalb jede Mehrfach-Recovery die Responsekette einer bereits beantworteten Linie verlaengert | realistische Bruchbedingung: ein Lauf stuerzt erneut nach einer bereits persistierten Finding-Response ab, und bei jedem Resume waechst die Responsezahl um eins, ohne dass ein Guard anschlaegt, weil Mirror und Kette gemeinsam wachsen und konsistent bleiben
PRE_MORTEM: In drei Monaten scheitert der Hotfix nicht an der Recovery, sondern an ihrem Erfolg: Weil die Wiederaufnahme jetzt zuverlaessig greift, wird sie haeufiger benutzt, und jedes Mal waechst die Responsekette einer bereits beantworteten Findinglinie. Irgendwann liest jemand aus dem Audittrail drei Codex-Antworten auf einen Blocker, haelt das fuer echte Mehrfachrunden und leitet eine Historie ab, die es nie gab; wegen der append-only Kette laesst sich das nicht korrigieren, sondern nur erklaeren. Zweitwahrscheinlich verlaesst sich eine spaetere Aenderung darauf, dass der Legacy-Fallback so streng prueft wie der Normalweg, und lockert im Vertrauen darauf eine andere Pruefung. Drittwahrscheinlich trifft der naechste Absturz einen Reviewaufruf, und die providerneutral benannte, aber nur fuer Codex verdrahtete Bundle-Persistenz fehlt genau dort.
FINAL_APPROVAL: NO
SAFE_TO_REQUEUE_POISON_TASK: NO
EXPECTED_NEW_CODEX_PROVIDER_START: NO
EXPECTED_SCOPE_GATE_BEFORE_CLAUDE: YES
STATUS: DONE
```
