# Reviewprotokoll – Geschlossene native Codex–Claude-Korrekturschleife

TARGET_BRANCH: feature/native-codex-claude-correction-loop

BASE_COMMIT: 04487883d560f7082b96902263bb890c0ec83655

ANTIGRAVITY: NOT_RUN (manual bootstrap exception)

## Planreview vom 23. August 2026

- Reviewer: Claude Sonnet, Effort `high`, read-only
- Entscheidung: `approved`
- Geprüfter Plan-SHA-256:
  `65dee939a313afa2c87a69097861d71866db3494cf5239ecd56d8baee69624d6`
- Request-ID:
  `native-review-request-65dee939a313afa2c87a69097861d71866db3494cf5239ecd56d8baee69624d6`
- Inputdigest:
  `88fd783fedda07c9a8be1ce07d738503d20a167d3a321c038e3fc452cf8beaf1`
- Provider-Schemadigest:
  `31510b2d44b4d277a0dec7fda9b3baed4144a34ff5d5e67cbeceb65045acedd4`
- Rohantwortdigest:
  `f60aed3ecb54327e4a7756b8b3c1150e03a2bc640beee739cfefdcafa752a202`
- Providerantwort: natives `native-agent-review-result-v1`
- Kosten: `$0.8417308`
- Claude-Sitzung: `dcefde60-4c94-4e9a-9712-e35484a4d77b`
- Rohantwort:
  [`native-codex-claude-correction-loop-plan-review.raw.json`](native-codex-claude-correction-loop-plan-review.raw.json)
- Lokale Vollschemavalidierung: `PASS`

### Finding C-01 – OPEN OBSERVATION

Der Replay-vs.-Mirror-Guard muss ausdrücklich auch
`CODEX_PLAN_REVISION`/`NativeCodexRequestKind.PLAN` umfassen und darf nicht
nur für `NativeCodexRequestKind.CORRECTION` implementiert werden.

Disposition: In Planabschnitt 3.2, Slice-1-Arbeitsschritt 4, den
Akzeptanzkriterien und Testszenario 14 verbindlich ergänzt. Schließung oder
Eskalation erfolgt im Slice-1-Review durch Claude.

### Finding C-02 – OPEN OBSERVATION

Die additive v1-Erweiterung von `plan_result` braucht vor der
Schemaänderung einen falsifizierbaren Kompatibilitätsnachweis.

Disposition: Der Plan verlangt nun unveränderte Validierungsergebnisse aller
historischen Fixtures, den Ausschluss einer erschöpfenden Feldmengensemantik
der Schema-ID und eine ausschließlich domänenseitige kontextabhängige
Dispositionspflicht. Schließung oder Eskalation erfolgt im Slice-1-Review
durch Claude.

## Slice 1

Implementiert und nach zwei direkten Claude-Runden am 23. August 2026
freigegeben.

### Umgesetzter Stand

- `plan_result` kann strukturierte Finding-Dispositionen tragen. Historische
  v1-Dokumente ohne das additive Feld bleiben als leere Folge lesbar; das
  Live-Providerschema verlangt das Feld für neu erzeugte Antworten.
- Der vorhandene `FindingTransitionPayload` trägt im nativen Pfad den
  vollständigen Opening-Snapshot und eine typisierte Codex-Disposition. Es
  wurde kein neuer Recordtyp eingeführt.
- `replay_findings()` rekonstruiert Findingautorität allein aus der geprüften
  append-only Recordkette. Der kombinierte native Codex/Claude-Pfad vergleicht
  diese Projektion vor einem neuen Providerstart exakt mit dem State-v3-
  Spiegel und hält bei Abweichung fail-closed an.
- Der Guard umfasst `CODEX_PLAN_REVISION`, `CODEX_CORRECTION` und native
  Claude-Reviews. Vollständige Record-ahead-Ergebnisse werden vor dem Guard
  wiederhergestellt, sodass ein erwarteter Checkpoint-Rückstand keinen
  zweiten Providerstart auslöst.
- Der folgende native Claude-Request bindet die persistierte Codex-Disposition
  und die Attestierung des korrigierten Fingerprints in seine Request-ID ein.
- Der Legacy-Textpfad behält sein bisheriges minimales Finding-Payloadformat.
  Eine im ersten Vollmatrixlauf gefundene Idempotenzregression bei einem
  historischen hashgebundenen Review-Replay wurde dadurch behoben und mit
  einem direkten Legacy/native-Bridge-Test abgesichert.

### Lokale Validierung

- Fokussierter Slice-Lauf: `258 passed in 120.15s`
- Vollständige Matrix: `1156 passed in 170.69s`
- `git diff --check`: `PASS` (nur erwartete LF/CRLF-Hinweise)
- Providerstarts während der Validierung: keine

### Planreview-Findings vor Runde 1

- `C-01` bleibt bis zur Claude-Prüfung des expliziten
  `CODEX_PLAN_REVISION`-Mirror-Guards offen.
- `C-02` bleibt bis zur Claude-Prüfung der rückwärtskompatiblen additiven
  v1-Schemaerweiterung offen.

### Direkter Claude-Slice-Review, Runde 1

- Erster physischer Start: technisch fehlgeschlagen mit
  `error_max_structured_output_retries`; keine Reviewentscheidung erzeugt.
- Einmaliger, inhaltlich identischer Retry: schema-valide Antwort.
- Gebundener Fingerprint:
  `528d2a88e07fff3628c08041b1ce2f3a465aa0298806f72cfa37cf41fe607394`
- Request-ID:
  `native-review-request-967fdf14d9d1e0e5f48645c0f9449633cde6068329ad3697923beb58d54b8470`
- Entscheidung: `denied`
- Rohantwortdigest:
  `e8af2a4f7439041c40b2a90e1821e14c0f19a37d472f9b2e5f96bc29e5db4a52`
- Rohantwort:
  [`native-codex-claude-correction-loop-slice1-review.raw.json`](native-codex-claude-correction-loop-slice1-review.raw.json)

#### Finding C-01 – CLOSED

Claude bestätigte, dass der parametrisierte Mirror-Drifttest sowohl
`CODEX_PLAN_REVISION` als auch `CODEX_CORRECTION` vor dem Providerstart
abbricht. Die symmetrischen Claude- und Record-ahead-Tests schließen den
ursprünglichen Planbefund vollständig.

#### Finding C-02 – CLOSED

Claude bestätigte die additive v1-Kompatibilität: Historische Dokumente
bleiben lesbar, das Live-Providerschema verlangt die neue Liste und die
Vollständigkeit wird ausschließlich durch den gebundenen Findingkontext
aktiviert.

#### Finding C-03 – BLOCKER, Korrektur implementiert

`native_codex_provider_response_schema()` veränderte die von
`load_native_codex_schema()` gelieferte Objektstruktur in-place. Das war
aktuell wegen frischem JSON-Laden folgenlos, hätte bei späterem Schema-Caching
aber die historische Lesbarkeit brechen können. Der Live-Writervertrag
arbeitet nun auf einer defensiven Tiefenkopie. Der Regressionstest ruft das
Providerschema bewusst vor dem Persistenzschema und dem historischen Parser
auf.

#### Finding C-04 – OBSERVATION, bereinigt

Der wirkungslose Parameter `require_structured` wurde entfernt.
`replay_findings()` dokumentiert nun eindeutig, dass unstrukturierte
Legacytransitionen zwar im allgemeinen Recordreplay lesbar bleiben, aber
niemals einen neuen nativen Request autorisieren.

### Korrekturvalidierung vor Runde 2

- Fokussierter Lauf: `30 passed in 1.25s`
- Vollständige Matrix: `1156 passed in 188.82s`
- `git diff --check`: `PASS` (nur erwartete LF/CRLF-Hinweise)

### Direkter Claude-Slice-Review, Runde 2

- Gebundener Fingerprint:
  `5ca987bbbf7a899ba51f855ba5747bca56163d3effbe3503f2fb9682d33269a6`
- Request-ID:
  `native-review-request-86599cdf917208673432f51217a7d3fe931f47daf29d5be720a955051b4fe1d2`
- Entscheidung: `approved`
- Neue Findings: keine
- Rohantwortdigest:
  `dd459bb83613c9f3565d8c5608680ab666d80ccbc87762f0fc34f44810695048`
- Rohantwort:
  [`native-codex-claude-correction-loop-slice1-review-round2.raw.json`](native-codex-claude-correction-loop-slice1-review-round2.raw.json)

Claude hat C-03 und C-04 geschlossen. Der Reviewer bestätigte die defensive
Schemakopie, den provider-first Kompatibilitätstest und die eindeutige
Legacy-Abgrenzung von `replay_findings()`. Slice 1 ist damit freigegeben.

## Slice 2

Implementierung und erster direkter Claude-Slice-Review sind abgeschlossen.

### Umgesetzter Stand

- Ein kombinierter nativer Workflowtest aktiviert beide Transportbindungen,
  ersetzt sämtliche Legacy-Parser und Text-Contract-Reparaturen durch
  Fehlerstubs und durchläuft dennoch
  `Codex → Claude-Denial → Codex-Korrektur → Claude-Schließung` bis zur
  regulären `ANTIGRAVITY_SLICE_REVIEW`-Grenze.
- Die Markdownprojektion erzeugt aus Findingtransitionen eine kompakte native
  Konvergenzübersicht mit Work-Units, Runden, Fingerprints,
  Claude-Entscheidungen, Codex-Dispositionen und Endstatus. Mehrere Revisionen
  derselben Work-Unit können deren ursprüngliche Rundenidentität nicht
  nachträglich umschreiben.
- `audit_trail` übernimmt die Projektion deterministisch und byte-identisch;
  Markdown wird in keinem Nutzpfad als Entscheidungs- oder Recoveryquelle
  gelesen.
- README und manueller Bootstrapvertrag beschreiben die gemeinsame
  `--native-codex-results --native-claude-reviews`-Bindung, den fehlenden
  Textfallback, Record-ahead-Recovery und die kontrollierte Grenze vor dem
  weiterhin nicht nativen Antigravity-Schritt.

### Lokale Validierung

- Kombinierte JSON-only-Konvergenztests für Planrevision, Slice-Korrektur und
  erneuten branchweiten Finaldurchlauf: `3 passed in 0.97s`
- Breite Paket-05-Regressionsmenge: `352 passed in 98.10s`
- Erster Vollmatrixlauf: `1159 passed, 1 failed in 144.77s`; ausschließlich
  der Sprachguard beanstandete das deutsche Wort `Entscheidung` in einer
  Python-generierten Tabellenüberschrift.
- Nach Umstellung der Runtime-Tabellenlabels auf Englisch: gezielter
  Sprach-/Projektionslauf `3 passed in 2.39s`.
- Abschließende vollständige Matrix: `1160 passed in 131.95s`.
- `git diff --check`: `PASS` (nur erwartete LF/CRLF-Hinweise).
- Providerstarts während aller lokalen Validierungen: keine.

### Direkter Claude-Slice-Review, Runde 1

- Gebundener Fingerprint:
  `3b5025c19c5734e31b5df41af9b4320ef14a364352f804bd588913ae3162ffb9`
- Request-ID:
  `native-review-request-bd4411bd33374c06f785446e6e6f4cf57c3bba3842ded186e956d3afb91c6988`
- Entscheidung: `approved`
- Neues Finding: `C-05 | OBSERVATION`
- Rohantwort:
  [`native-codex-claude-correction-loop-slice2-review.raw.json`](native-codex-claude-correction-loop-slice2-review.raw.json)

Claude bestätigte die kombinierte native Bindung, Recordautorität,
Record-ahead-Recovery, Legacy-Parser-Sprengfallen und die rein aus Records
erzeugte Markdownprojektion. Als verbleibende Beobachtung identifizierte er
einen ordnungssensitiven Vergleich zwischen kanonisch sortiertem Replay und
dem potentiell anders geordneten State-v3-Mirror.

#### Finding C-05 – OBSERVATION, korrigiert

`ProductionWorkflowDriver.authoritative_native_findings()` kanonisiert nun
auch den übergebenen State-v3-Mirror nach Finding-ID, bevor beide inhaltlich
verglichen werden. Die Regression persistiert zwei strukturierte Findings,
übergibt den content-identischen Mirror in umgekehrter Reihenfolge und weist
zugleich nach, dass eine echte Inhaltsabweichung weiterhin fail-closed
abgelehnt wird.

### Korrekturvalidierung vor Runde 2

- Fokussierter Lauf einschließlich der drei kombinierten JSON-only-Szenarien:
  `4 passed in 1.60s`
- Vollständige Matrix: `1160 passed in 139.82s`
- `git diff --check`: `PASS` (nur erwartete LF/CRLF-Hinweise)
- Direkter Claude-Korrekturreview: abgeschlossen; ausschließlich auf C-05
  begrenzt, kein branchweiter Abschlussreview.

### Direkter Claude-Slice-Review, Runde 2

- Gebundener Fingerprint:
  `f4c4a473252fab42d80cc54177562f4739ec2125e58d3ee09a2d0e8728116532`
- Request-ID:
  `native-review-request-f1fc4d5373128184a4fb7dd66dcb4143dc975a9e88863e876d446dea5b3ed58c`
- Entscheidung: `approved`
- Findingstatus: `C-05 | CLOSED`
- Neue Findings: keine
- Rohantwortdigest:
  `3c75fd110cb6be9874dc7e8eba08310e1df524df403a2599cef54db93069394d`
- Rohantwort:
  [`native-codex-claude-correction-loop-slice2-c05-review.raw.json`](native-codex-claude-correction-loop-slice2-c05-review.raw.json)

Claude bestätigte exakt den kanonischen Vergleich und den Zwei-Finding-
Negativnachweis. Der Request verbot neue Observations und jeden
branchweiten oder arbeitspaketweiten Review. C-05 ist damit formal durch den
ursprünglichen Reviewer geschlossen und Slice 2 freigegeben.

## Branchreview

### Codex-Gesamtcheck

- Plan, gesamter Branchdiff, native Codex-/Claude-Bindungen,
  Record-ahead-Recovery, Findingautorität, State-Mirror-Guard,
  Legacy-Parser-Sprengfallen, Projektion und Dokumentation wurden
  branchweit abgeglichen.
- Das von Claude außerhalb des C-05-Entscheidungsumfangs genannte Restrisiko
  einer fehlenden Work-Unit-Filterung ist kein Defekt: Findings werden über
  Korrektur- und Finalreview-Work-Units bewusst weitergetragen. Ein Filter
  auf die aktuelle Work-Unit würde Antworten auf Findings aus der
  unmittelbar vorherigen Work-Unit als verwaiste Transitionen ablehnen.
- Offene strukturierte Findings: keine (`C-01` bis `C-05` geschlossen).
- Vollständige Matrix: `1160 passed in 139.82s`.
- `git diff --check`: `PASS` (nur erwartete LF/CRLF-Hinweise).

### Bewusst nicht ausgeführte Abschlussreviews

- Claude-Gesamtreview: `NOT_RUN` – ausdrückliche Nutzerentscheidung aus
  Quotagründen; Plan und beide Slices einschließlich C-05-Korrektur wurden
  bereits direkt und fingerprintgebunden von Claude freigegeben.
- Antigravity-Gesamtreview: `NOT_RUN` – manueller Bootstrapvertrag; der Pilot
  endet kontrolliert vor dem weiterhin textuellen Antigravity-Schritt und
  behauptet keine produktionsweite Antigravity-Freigabe.

Arbeitspaket 5 ist damit implementiert und für den später ausdrücklich
anzuweisenden Abschluss-/Archivschritt vorbereitet.
