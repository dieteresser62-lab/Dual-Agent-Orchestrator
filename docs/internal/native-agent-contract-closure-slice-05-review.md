# Native Agent Contract Closure – Slice 05 Korrektur und Review

## Status

- Slice: `05 – Writerautorität, vollständige Requestbindung und stabile Canary-Evidenz`
- Planbasis: `5ec8d0d780cd50ad3f21f8e86156d559bab81c8a`
- Implementierungsbasis: `5ec8d0d780cd50ad3f21f8e86156d559bab81c8a`
- Implementierungsstatus: in Arbeit
- Planreview: von Claude mit `PLAN_APPROVAL: YES` freigegeben
- Reviewer: Claude, direkt und außerhalb von `run_task`
- Antigravity: für diesen direkten Contract-Closure-Prozess nicht vorgesehen
- Implementierung vor Planfreigabe: verboten

Dieses Dokument ist zunächst ausschließlich der detaillierte Korrekturplan aus
den beiden unabhängigen branchweiten Gesamtreviews. Nach einer ausdrücklichen
Claude-Planfreigabe darf es zusätzlich als Implementierungsprotokoll und
Revieweingabe fortgeführt werden. Es ist keine technische Entscheidungs-,
Recovery- oder Freigabequelle.

## 1. Ausgangslage und autoritative Befundquellen

Der Stand `286f5b2` wurde unabhängig durch Claude und Codex vollständig
adversarial geprüft. Beide Gesamtreviews verweigern den Abschluss:

- `docs/internal/native-agent-contract-closure-final-review-claude.md`
- `docs/internal/native-agent-contract-closure-final-self-review-codex.md`

Die Reviewdokumente wurden unverändert als Commit `5ec8d0d` persistiert. Die
vor Slice 04 attestierten Ergebnisse `1273 passed` und `42 passed` gelten nicht
als Abschlussattestierung für den jetzigen Commit: Der nach dem Canary erzeugte
Commit änderte `HEAD` und machte den Canary-Bindungstest reproduzierbar rot.

### Konsolidiertes Findingledger

| Befund | Quelle | Status vor Slice 05 | Disposition in Slice 05 |
|---|---|---:|---|
| `CR-01` / `C-29` | Codex / Claude | geschlossen | unverändert regressionsgesichert |
| ursprüngliches `CR-02` | Codex | begründet verworfen | nicht erneut implementieren |
| `CR-03` | Codex | offen | normative Planbeschreibung an `C-25` angleichen |
| `CR-04` | Codex | offen, BLOCKER | konkretes Writerschema als lokale Live-/Recoveryautorität durchsetzen |
| `CR-05` | Codex | offen, BLOCKER | Requestbytes, Kontext und Codex-Evidenzassets vollständig koppeln |
| `CR-06` / `C-30` | Codex / Claude | offen, BLOCKER | historische Canary-Bindung vom lebenden `HEAD` entkoppeln |

`C-26` bis `C-29` bleiben geschlossen. Slice 05 darf sie weder neu öffnen noch
ihre historischen Begründungen umschreiben. Neue ausführbare Defekte aus dem
Planreview erhalten eine neue Claude-ID; nicht ausführbare Zukunftsrisiken
gehören ausschließlich in die Reviewevidenz.

## 2. Ziel und Abschlussinvarianten

Slice 05 schließt die letzte Autoritätslücke zwischen erzeugtem Writerschema,
lokaler Verarbeitung, Recovery und dauerhaftem Transportnachweis.

Nach dem Slice müssen gleichzeitig gelten:

1. Eine neue native Antwort wird nicht nur vom Provider unter dem konkreten
   Writerschema erzeugt, sondern vor Raw-Callback, Record-Ahead-Persistierung
   und Domänenkonvertierung lokal gegen exakt dasselbe Schema validiert.
2. Eine aktuelle request-gebundene Recovery verwendet dieselbe Writerprüfung
   wie der Livepfad. Historische, ausdrücklich als Pre-Policy klassifizierte
   Resultate bleiben dagegen gemäß Arbeitsplan 4.1.5 am allgemeinen
   Reader-/historischen Domänenvertrag gebunden und werden nicht rückwirkend
   an ein späteres Writerschema angepasst.
3. Ein Requestbundle kann nicht erfolgreich existieren, wenn Requestbytes,
   Request-ID, Requestdigest, gebundener Kontext, Response-Schemadigest oder
   ausgelagerte Evidenzassets semantisch voneinander abweichen.
4. Die lokale Finalreview-Domäne verbietet neue oder neu reklassifizierte
   Observations unabhängig von `allow_new_observations`. Das ist keine nur
   defensive Behandlung eines fehlerhaft konstruierten Kontexts: Der reguläre
   Produktionspfad setzt für `WorkUnitKind.FINAL_REVIEW` derzeit
   `allow_new_observations=True` und macht den Bypass ohne die konkrete
   Writerprüfung produktiv erreichbar.
5. Ein persistierter Canarynachweis enthält seine unveränderliche
   Requestbindung. Seine Rekonstruktion hängt nicht vom späteren
   Repository-`HEAD` ab und bleibt nach beliebigen Folgecommits stabil.
6. Arbeitsplan, Roadmap, Sliceprotokolle, Manifest, Code und Tests treffen
   dieselben Aussagen. Insbesondere ist `--max-budget-usd` ausdrücklich ein
   profilfremder Kostenwächter und kein semantisches Claude-Transportflag.

## 3. Exakter Änderungspfad

- `docs/internal/native-agent-contract-closure-arbeitsplan.md`
- `docs/internal/ORCHESTRATOR_ROADMAP_PHASE_2_PLUS.md`
- `docs/internal/native-agent-contract-closure-slice-05-review.md`
- `scripts/native_contract_probe.py`
- `src/agent_runtime.py`
- `src/native_codex_request.py`
- `src/native_provider_schema.py`
- `src/native_review_contract.py`
- `src/native_review_request.py`
- `src/orchestrator.py`
- `tests/fixtures/native-contract-corpus/manifest.json`
- `tests/test_agent_runtime.py`
- `tests/test_native_codex_request.py`
- `tests/test_native_contract_corpus.py`
- `tests/test_native_contract_differential.py`
- `tests/test_native_contract_probe.py`
- `tests/test_native_review_contract.py`
- `tests/test_native_review_request.py`
- `tests/test_orchestrator_runtime.py`
- `tests/test_workflow.py`

Die allgemeinen Reader-Schemadateien
`schemas/native-agent-codex-result-v1.schema.json` und
`schemas/native-agent-review-result-v1.schema.json` bleiben unverändert. Die
Provider-Ausnahmeliste darf für diese Korrekturen nicht wachsen: Alle
betroffenen Regeln sind entweder im konkreten Writerschema ausdrückbar oder
eine lokale Integritätsinvariante des Bundles beziehungsweise der Recovery.

Sollte die Implementierung eine weitere Produktivdatei benötigen, muss Claude
die Notwendigkeit im Planreview ausdrücklich bestätigen und diese Datei vor
Beginn der Implementierung in den exakten Änderungspfad aufgenommen werden.

## 4. Teil A – CR-03: Normativen Transportprofiltext korrigieren

### Defekt

Abschnitt 4.1.8 des Arbeitsplans führt
`--max-budget-usd=<configured-value>` weiterhin in der abschließenden Liste der
Claude-`semantic_flags`. Implementierung, Capabilityprofil und die akzeptierte
Disposition von `C-25` behandeln das Flag dagegen bewusst als profilfremden
Kostenparameter.

### Geplante Änderung

1. `--max-budget-usd` aus der normativen `semantic_flags`-Liste entfernen.
2. Unmittelbar bei den anderen ausgeschlossenen Laufzeitparametern festhalten,
   dass Budgetwerte Kosten und Abbruchgrenze steuern, aber weder
   Schemaübergabemechanismus noch JSON-Akzeptanzsemantik verändern.
3. Die historische `C-25`-Observation und ihre Disposition nicht umschreiben.
4. Roadmap und Slice-05-Protokoll dürfen keine abweichende Profildefinition
   enthalten.

### Akzeptanz

- Arbeitsplan, Capabilitydatei, `normalize_transport_profile()` und
  `test_claude_budget_is_deliberately_profile_neutral_for_c25` stimmen
  wörtlich und fachlich überein.
- Unbekannte oder tatsächlich semantische Argumente bleiben unverändert
  fail-closed; die Korrektur lockert ausschließlich den dokumentarischen
  Widerspruch.

## 5. Teil B – CR-04: Konkretes Writerschema als lokale Autorität

### Defekt

`run_native_review_agent()` und `run_native_codex_agent()` prüfen das vom
Adapter extrahierte innere Resultat gegen das allgemeine Reader-Schema und die
lokale Domäne, aber nicht gegen das request-spezifische Writerschema des
Bundles. Die aktuellen Record-Ahead-Recoverypfade besitzen dieselbe Lücke.

Dadurch kann eine Antwort lokal akzeptiert werden, obwohl der konkrete Writer
sie verbietet. Reproduziert ist ein Final-Denial, der `C-01 BLOCKER OPEN` zu
`OBSERVATION OPEN` reklassifiziert und wegen eines zweiten offenen Blockers
dennoch die lokale Denialpflicht erfüllt. Der Finalwriter lehnt ihn ab, die
lokale Domäne akzeptiert ihn derzeit bei `allow_new_observations=True`. Dieser
Wert stammt im regulären Finalreview aus der produktiven Regel
`unit.kind is not WorkUnitKind.CORRECTION` in `src/workflow.py` und
`src/orchestrator.py`; der Angriff benötigt daher keinen künstlich
fehlkonstruierten Kontext.

### Geplante Änderung

1. Eine gemeinsame, kleine Providerresultat-Prüfung einführen. Sie erhält das
   extrahierte innere Resultat und die unveränderlichen kanonischen
   Writerschemabytes beziehungsweise deren bereits validierte Bundleansicht.
2. Die Prüfung rekonstruiert ausschließlich die festgelegte Transporthülle
   `{"result": document}` und validiert sie gegen genau dieses Writerschema.
   Sie berechnet keine alternative Schemaform aus losen Laufzeitwerten.
3. Im Livepfad ist die Reihenfolge verbindlich:
   - JSON dekodieren und kanonische innere Objektform prüfen;
   - request-spezifisches Writerschema prüfen;
   - Request-ID prüfen;
   - lokale Domänenkonvertierung durchführen;
   - erst danach Raw-Callback beziehungsweise Record-Ahead-Persistierung
     ausführen.
4. Die Reihenfolge darf keine bereits fachlich ungültige Antwort als
   „validierte Rohantwort“ persistieren. Falls für Crashsicherheit Raw-bytes
   vor Domänenkonvertierung benötigt werden, ist zwischen unveränderlicher
   diagnostischer Quarantäne und autoritativem Agent-Resultat klar zu trennen;
   nur Writer- und domänengültige Bytes dürfen den fachlichen Record-Ahead-Pfad
   erreichen.
5. Codex- und Claude-Record-Ahead-Recovery validieren die gespeicherten Bytes
   erneut gegen das Writerschema des exakt rekonstruierten Bundles, bevor sie
   State oder Findings spiegeln.
6. Die Funktion `recover_pending_native_reviewer_before_policy()` bezeichnet
   nur ihre Ausführung **vor der Current-Diff-Policy**, nicht eine historische
   Protokollklasse. Da ihr strukturierter Native-Record keine beweisbare
   historische Ausnahme trägt, rekonstruiert sie den gebundenen Kontext und
   wendet das daraus deterministisch erzeugte konkrete Writerschema ebenfalls
   fail-closed an. Nur echte `legacy-state-v3`-Verläufe und archivierte
   Corpusfixtures außerhalb dieses strukturierten Recoverypfads bleiben am
   historischen Readervertrag; ein aktueller request-gebundener Record kann
   dadurch nicht in eine Reader-Ausnahme fallen.
7. `_validate_decision()` erhält zusätzlich eine eigenständige Finalregel:
   Bei `ApprovalMarker.FINAL` sind neue Observations und Reklassifizierungen
   zu Observations immer verboten. Die Regel darf nicht vom Wert von
   `allow_new_observations` abhängen.
8. Die Finalreview-Regression baut ihren Kontext über dieselbe produktive
   Work-Unit-Regel `unit.kind is not WorkUnitKind.CORRECTION` auf und deckt
   damit die Fundstellen in `src/workflow.py` und `src/orchestrator.py` ab,
   statt das Flag nur von Hand zu setzen.

### Regressionen

- Claude live: ein readergültiges, writerungültiges inneres Resultat scheitert
  vor Callback und Domänenpersistierung.
- Codex live: falsche Ergebnisart, fremde Findingdisposition oder durch den
  konkreten Vertrag ausgeschlossene Readiness scheitert am Writer vor
  Callback.
- Claude und Codex Record-Ahead: dieselben Bytes scheitern vor Mirror- oder
  Findingänderung; der Provider wird beim Recovery nicht erneut gestartet.
- Finalreview: der reproduzierte `BLOCKER -> OBSERVATION`-Angriff scheitert am
  Writer und unabhängig davon mit einem stabilen lokalen Fehler.
- Kontrolle: historische Pre-Policy-Fixtures bleiben gemäß Readervertrag
  lesbar; ein neuer Writer macht sie nicht rückwirkend ungültig.
- Negativkontrolle: Ein aktueller Record kann nicht durch Entfernen oder
  Fälschen seines Writer-/Protokollbindings in den Pre-Policy-Pfad fallen.

## 6. Teil C – CR-05: Vollständige Bundle- und Kontextbindung

### Defekt

`NativeReviewRequestBundle` rechnet den Requestdigest nach, vergleicht aber den
im Dokument enthaltenen Reviewkontext nicht vollständig mit dem lokalen
`BoundNativeReviewContext`. `NativeCodexRequestBundle` rechnet den
Requestdigest der übergebenen kanonischen Bytes nicht nach und validiert seine
`content_ref`-Assets nicht gegen das Requestmanifest.

Dadurch wurden providerfrei vier inkonsistente Konstruktionen akzeptiert:

1. Claude-Request und Claude-Bound-Context mit verschiedenen `run_id`;
2. Codex-Request und Codex-Bound-Context mit verschiedenen `run_id`;
3. geänderte kanonische Codex-Requestbytes bei unveränderter Request-ID;
4. Codex-Request mit gebundenem `content_ref`, aber leerer Bundle-Assetliste.

### Geplante Änderung

1. Für beide Provider genau eine kanonische Kontextprojektion definieren, aus
   der sowohl Requestbuilder als auch Bundle-Selbstprüfung ihre
   kontextabhängigen Dokumentteile erzeugen beziehungsweise vergleichen.
2. Der Bundlekonstruktor rechnet unabhängig vom Builder den Digest über das
   kanonische Requestdokument ohne `request_id` nach und verlangt
   `request_id == prefix + digest == bound_context.request_id`.
3. Für normale Claude-Reviewrequests werden mindestens `run_id`,
   `work_unit_id`, Operation, Fingerprint, Reviewart, Approvalmarker, Slice,
   Runde, vorherige Findings, Attestierung, Testpolicy, Observationpolicy,
   Anchororigin und Validierungsprefixe gegen den gebundenen Kontext geprüft.
4. Für Codex werden zusätzlich Requestart, die vollständig serialisierte
   Projektion des `CodexStepContract` und die offene Findingprojektion
   verglichen. Das Requestdokument transportiert bewusst nur offene Findings.
   Geschlossene Findings bleiben Autorität der Workflow-/Recordhistorie und
   sind nicht Teil der kanonischen Requestprojektion.
5. Gleiche Writerschemabytes sind kein Ersatz für Kontextgleichheit. Zwei
   Kontexte, die zufällig dieselbe Schemaform ergeben, bleiben verschiedene
   Requestbindungen.
6. Das Codex-Bundle erhält dieselbe Manifest-/Assetvollständigkeit wie das
   Claude-Bundle:
   - Inlinebytes stimmen mit Bytezahl und SHA-256 überein;
   - jeder `content_ref` besitzt genau ein Asset mit demselben sicheren Pfad,
     Digest, Bytezahl und Inhalt;
   - fehlende, zusätzliche, doppelte oder vertauschte Assets scheitern;
   - der Adapter materialisiert erst nach erfolgreicher Bundleprüfung.
7. Claude-Repairrequests werden nicht fälschlich wie vollständige
   Primärrequests behandelt. Ihre eigene geschlossene Requestform muss aber
   den validierten Parentrequest, dessen Bound Context, den unveränderten
   Responsevertrag und den Fingerprint nachweisbar koppeln. Eine bloß
   synthetische neue `BoundNativeReviewContext`-Instanz mit übernommener
   Request-ID reicht nicht.
8. Recovery darf einen Bound Context nur aus einer vollständigen
   autoritativen Request-/Workflowbindung rekonstruieren. Wo historische
   Requestbytes absichtlich nicht existieren, greift ausschließlich die eng
   klassifizierte Pre-Policy-Regel aus Teil B.
9. Zwei Codex-Kontexte, die sich ausschließlich in geschlossenen Findings
   unterscheiden, dürfen dieselben Requestbytes erzeugen, aber nicht zu
   widersprüchlichen lokalen Entscheidungen führen. Ein Regressionstest weist
   entweder ihre Ergebnisgleichheit nach oder erzwingt eine zusätzliche
   autoritative Workflowbindung vor der Domänenkonvertierung.

### Regressionen

- Alle vier reproduzierten Gegenbeispiele werden durch direkte
  Bundlekonstruktion abgewiesen.
- Feldweise Mutationen jedes in der kanonischen Requestprojektion enthaltenen
  Kontextfelds scheitern, auch wenn der Writerschemadigest unverändert bliebe.
- Codex-Assettests decken fehlende, zusätzliche, doppelte, vertauschte und
  inhaltlich abweichende Assets ab.
- Ein gültiger Inline- und ein gültiger `content_ref`-Request bleiben
  bytegleich deterministisch.
- Ein gültiger Claude-Repairrequest bleibt funktionsfähig; Parenttausch,
  Fingerprinttausch oder Responsevertragstausch scheitern vor Providerstart.
- Bestehende originale `request_bound`-Corpuspaare bleiben ohne synthetische
  Kontextannahme lesbar.
- Zwei Codex-Kontexte mit identischer offener Projektion und ausschließlich
  abweichenden geschlossenen Findings erzeugen entweder dasselbe lokale
  Ergebnis oder werden vor der Auswertung an der autoritativen
  Workflowhistorie unterschieden.

## 7. Teil D – CR-06 / C-30: Commitstabile Canary-Evidenz

### Defekt

Alle Canarybuilder setzen `base_commit` aus dem lebenden `HEAD`. Persistierte
Manifestzeilen enthalten zwar Request-ID, Writerdigest und Antwortdigest, aber
nicht den bei der Ausführung verwendeten `base_commit`. Nach jedem späteren
Commit rekonstruiert derselbe Builder deshalb eine andere Request-ID.

Der Abschlussstand reproduziert konkret:

```text
persistiert: native-review-request-58e98da53bd078faa2e2e666d9a1549a51435c683709040dab0aa2d26a11d236
aktuell:     native-review-request-6694324a16597fe5be325a6e49bf5ec5f09a4a831e2a0e6f99bbb578f997c3b8
```

`test_convergence_canary_serializes_status_change_items_one_of` ist dadurch am
Reviewstand rot. Ein bloßes Nachziehen der Manifest-Request-ID auf den
aktuellen `HEAD` verschiebt den Fehler nur zum nächsten Commit und ist keine
zulässige Korrektur.

### Geplante Änderung

1. Die Canary-Kontextbuilder erhalten `base_commit` als expliziten,
   validierten Eingabewert. Der Livepfad darf den aktuellen `HEAD` einmalig am
   Start auflösen, muss diesen Wert aber zusammen mit dem Ergebnis
   zurückliefern und persistieren.
2. Jede `live_canary`-Manifestzeile speichert mindestens den tatsächlich
   verwendeten 40-stelligen `base_commit`. Bevorzugt wird zusätzlich eine
   vollständige kanonische Requestbindung oder deren eigener Digest, sofern
   dies ohne Secrets und temporäre Pfade möglich ist.
3. Providerfreie Rekonstruktionstests verwenden ausschließlich den
   persistierten Canarykontext, niemals den aktuellen `HEAD`.
4. Alle sieben bestehenden `live_canary`-Zeilen werden inventarisiert. Der
   historische `base_commit` darf nur aus vorhandenen Requestbytes, Logs oder
   einer eindeutig reproduzierbaren dokumentierten Bindung übernommen werden.
   Er darf nicht geraten werden.
5. Ist die ursprüngliche Bindung einer Zeile nicht beweisbar, bleibt die
   historische Zeile als unveränderliche Auditangabe erhalten, zählt aber
   nicht länger als geschlossener aktueller Writerbeleg. Nach grüner
   providerfreier Matrix ist dann genau für die betroffene Writerform ein
   neuer isolierter Canary auszuführen und mit vollständiger Bindung als neuer
   Nachweis zu persistieren.
6. Der Konvergenz-Canary wird nicht wiederholt, wenn sein ursprünglicher
   `base_commit=eb38a31` zusammen mit dem bestehenden Request-, Schema- und
   Antwortdigest vollständig nachweisbar rekonstruiert werden kann.
7. Ein Test simuliert einen vom persistierten `base_commit` abweichenden
   Repository-`HEAD` und verlangt weiterhin dieselbe historische Request-ID.
   Eine reine Präfix-/Längenprüfung ist unzulässig.
8. Das Manifest behält exakt acht Writerformen. `schema_only`,
   `request_bound` und `live_canary` bleiben sauber getrennt; ein neuer Canary
   ändert keine historischen Rohantwortbytes.

### Abschlussvalidierung

Der aktuelle rote Einzeltest muss zuerst mit eingefrorener Bindung grün
werden. Danach folgen die fokussierten Matrizen. Die vollständige Suite wird
erst ausgeführt, wenn alle Implementierungs-, Manifest- und
Dokumentationsänderungen abgeschlossen sind. Die maßgebliche Attestierung wird
außerhalb eines nachträglich den geprüften `HEAD` verändernden
Selbstbezugs gespeichert und nennt den exakten getesteten Commit.

## 8. Reihenfolge der späteren Implementierung

Erst nach Claude-Planfreigabe:

1. CR-03 dokumentarisch korrigieren und die Sollinvarianten in Arbeitsplan und
   Roadmap festziehen.
2. Bundle-Selbstprüfungen und ihre direkten Negativtests implementieren.
3. Gemeinsame Writerschemaprüfung und Final-Domänenwächter implementieren.
4. Live- und aktuelle Record-Ahead-Pfade auf die gemeinsame Prüfung umstellen;
   historische Pre-Policy-Grenze separat absichern.
5. Canary-Kontext explizit einfrieren, Manifestformat und
   Rekonstruktionsregressionen aktualisieren.
6. Alle sieben vorhandenen Canarybindungen beweisorientiert inventarisieren;
   externe Provider nur dann aufrufen, wenn eine notwendige Bindung nicht aus
   vorhandenem Material wiederherstellbar ist.
7. Fokussierte Tests, anschließend vollständige Matrix und `git diff --check`
   ausführen.
8. Slice-05-Implementierungsprotokoll ergänzen und Claude zur adversarialen
   Implementierungsprüfung übergeben.

## 9. Verbindliche Akzeptanzkriterien

1. `CR-03` ist in der normativen Planbeschreibung widerspruchsfrei geschlossen.
2. Kein neuer Live- oder aktueller Record-Ahead-Pfad akzeptiert ein
   readergültiges, aber konkret writerungültiges Ergebnis.
3. Writerprüfung, Request-ID-Prüfung, Domänenkonvertierung und fachliche
   Persistierung besitzen eine dokumentierte und getestete fail-closed
   Reihenfolge.
4. Historische Pre-Policy-Resultate bleiben lesbar, können aber nicht als
   Schlupfloch für neue request-gebundene Antworten verwendet werden.
5. Der reguläre produktive Finalreviewkontext kann trotz seines heutigen
   `allow_new_observations=True` keine neue oder neu reklassifizierte
   Observation erzeugen.
6. Beide Bundletypen binden kanonische Requestbytes vollständig an
   Requestdigest, Request-ID, die tatsächlich serialisierte kanonische
   Kontextprojektion und den Responsevertrag. Bei Codex stammen geschlossene
   Findings weiterhin aus der autoritativen Workflow-/Recordhistorie und
   werden nicht fälschlich als transportierte Requestfelder behauptet.
7. Codex-`content_ref`-Assets sind vollständig, eindeutig und byte-/digestgenau
   an das Requestmanifest gebunden.
8. Claude-Repairrequests bleiben funktionsfähig und beweisen ihre
   Parent-/Kontextbindung ohne synthetische Identität.
9. Alle direkten Bundle-Gegenbeispiele aus `CR-05` scheitern vor
   Providerinputerzeugung.
10. Jeder als `live_canary` zählende Writerbeleg besitzt einen eingefrorenen,
    reproduzierbaren `base_commit` oder eine mindestens gleich starke
    vollständige Requestbindung.
11. Die Rekonstruktion einer historischen Canary-Request-ID bleibt bei
    verändertem aktuellem `HEAD` bitgenau stabil.
12. Der bestehende Konvergenznachweis behält Writerschema- und Antwortdigest;
    eine neue Request-ID darf ihn nicht still ersetzen.
13. Corpusfixturezahl, acht Writerformen und Evidenzklassifizierung bleiben
    vollständig und fail-closed gekoppelt.
14. Reader-Baselines und Provider-Ausnahmeliste bleiben unverändert.
15. Kein Test verwendet einen Legacy-Textparser oder textuellen Repairfallback
    für einen nativen Pfad.
16. Die fokussierten Matrizen, die vollständige Repositorysuite am finalen
    Implementierungscommit und `git diff --check` sind grün.
17. Claude schließt `C-30` erst nach Nachweis der commitstabilen Bindung und
    erteilt die Slice-Freigabe nur, wenn kein eigener Blocker offen bleibt.

## 10. Vorgesehene Validierung nach der Implementierung

### Bundle- und Vertragsgrenze

```text
python3 -m pytest \
  tests/test_native_codex_request.py \
  tests/test_native_review_request.py \
  tests/test_native_review_contract.py \
  tests/test_native_contract_differential.py \
  -v
```

### Live-, Recovery- und Canarygrenze

```text
python3 -m pytest \
  tests/test_agent_runtime.py \
  tests/test_orchestrator_runtime.py \
  tests/test_workflow.py \
  tests/test_native_contract_probe.py \
  tests/test_native_contract_corpus.py \
  -v
```

### Vollständig

```text
python3 -m pytest tests/ -v
git diff --check
```

Externe Canaries gehören nicht zum Planreview. Sie werden auch während der
Implementierung nur dann ausgeführt, wenn eine betroffene historische
Livebindung nicht aus vorhandenen unveränderlichen Quellen bewiesen werden
kann. Ein Quotafehler, Providerfehler oder fachlich ungültiges Resultat darf
das Manifest nicht verändern.

## 11. Stopbedingungen

Die spätere Implementierung hält an und verlangt eine Planrevision, wenn:

1. ein allgemeines Reader-Schema oder ein historisches Antwortfixture geändert
   werden müsste;
2. eine neue Provider-Ausnahme erforderlich scheint;
3. die vollständige Kontextbindung eines Repairrequests nur durch Änderung
   seiner externen serialisierten Vertragsform möglich wäre;
4. eine historische Pre-Policy-Recovery nicht eindeutig von aktuellen
   request-gebundenen Records unterscheidbar ist;
5. ein vorhandener Canary-`base_commit` nicht beweisbar ist und ein neuer
   Providerlauf wegen Quota oder technischer Störung nicht erfolgreich
   abgeschlossen werden kann;
6. Writerprüfung vor Persistierung die bestehende Record-Ahead-Crashsicherheit
   aufhebt oder doppelte Providerstarts ermöglicht;
7. die vollständige Matrix nach den Korrekturen weiterhin rot ist.

## 12. Reviewauftrag an Claude

Claude prüft vor jeder Implementierung ausschließlich diesen Plan und die
beiden Gesamtreviews. Besonders zu falsifizieren sind:

- ob `CR-04` den historischen Pre-Policy-Readerpfad fälschlich einem modernen
  Writerschema unterwirft;
- ob die vorgesehene Reihenfolge weiterhin Raw-first-Crashsicherheit erlaubt,
  ohne fachlich ungültige Bytes als autoritatives Resultat zu persistieren;
- ob die Bundlebindung normale Claude-Requests, Repairrequests und Codex-
  Requests vollständig, aber ohne zirkuläre oder synthetische Identität
  abdeckt;
- ob `content_ref`-Assets beim Codex wirklich vor dem Adapter vollständig
  geprüft werden;
- ob die Canarykorrektur historische Evidenz einfriert, statt nur die
  Manifest-ID auf den aktuellen `HEAD` nachzuziehen;
- ob alle sieben Live-Canaryzeilen und nicht nur `convergence` gegen spätere
  Commits stabil werden;
- ob die Test- und Pfadmatrix alle Live-, Recovery-, Repair- und
  Pre-Policy-Grenzen umfasst;
- ob irgendein Akzeptanzkriterium die unveränderten Reader-Baselines,
  historischen Rohantworten oder die freigegebene `request_bound`-Alternative
  rückwirkend verschärft.

Claude verändert im Planreview weder Produktcode noch Tests. Sein Ergebnis
wird als neuer, klar abgegrenzter Abschnitt am Ende dieses Dokuments ergänzt.

## Claude Planreview Slice 05

Datum: 24. August 2026 · Reviewer: Claude · Modus: read-only, adversarial

Planreview vor jeder Implementierung. Grundlage sind `AGENTS.md`, der
freigegebene Arbeitsplan, beide branchweiten Gesamtreviews, das
Slice-04-Protokoll, die Roadmap und dieser Sliceplan. Es wurde nichts
implementiert, kein Produktcode und kein Test verändert, keine vollständige
Suite und kein externer Canary ausgeführt. Ausgeführt wurden ausschließlich
kleine providerfreie read-only Gegenproben zur Falsifikation konkreter
Planbehauptungen.

### 1. Verifikation der Befundlage

Ein Korrekturplan taugt nur so viel wie die Befunde, auf die er sich stützt.
Ich habe die tragenden Behauptungen deshalb nicht übernommen, sondern
nachgerechnet.

**CR-04 bestätigt.** `run_native_review_agent()` und `run_native_codex_agent()`
validieren in dieser Reihenfolge: JSON dekodieren → allgemeines Leseschema →
`validated_response_callback(canonical)` → Domänenkonvertierung. Eine Prüfung
gegen `bundle.provider_response_schema` existiert nicht; der Bezeichner kommt in
`src/agent_runtime.py`, `src/orchestrator.py` und `src/workflow.py` überhaupt
nicht vor. Die Rohbytes werden damit heute **vor** der Domänenkonvertierung
persistiert — der Plan greift genau dort ein.

Den Finalreview-Angriff habe ich reproduziert: `ApprovalMarker.FINAL`,
`allow_new_observations=True`, zwei offene eigene Blocker, Denial mit
`reclassifications=[C-01 → OBSERVATION]`. Ergebnis `writer=rejected`,
`local=ACCEPTED`, resultierende Findings `[C-01 OBSERVATION OPEN,
C-02 BLOCKER OPEN]`. Die Kontrolle mit `allow_new_observations=False` scheitert
korrekt an `approval-invalid`.

**CR-05 bestätigt.** Drei der vier Gegenbeispiele habe ich direkt konstruiert
und alle drei wurden vom jeweiligen Bundlekonstruktor akzeptiert: Claude-Request
mit abweichender `run_id` im Bound Context; Codex-Request mit auf `forged-run`
geänderten kanonischen Bytes bei unveränderter `request_id`; Codex-Bundle mit
`content_ref` im Manifest und leerer `evidence_assets`-Liste.

**CR-06/C-30 unverändert.** Der Befund entspricht meinem eigenen Blocker.

### 2. Writerschema und Recovery

Die vorgesehene Reihenfolge in Teil B ist korrekt und vollständig: dekodieren,
konkretes Writerschema, Request-ID, Domäne, erst danach fachliche
Persistierung. Sie ist mit der Crashsicherheit vereinbar, weil Punkt B.4
ausdrücklich zwischen unveränderlicher diagnostischer Quarantäne und
autoritativem Agent-Resultat trennt, statt die Raw-first-Eigenschaft ersatzlos
zu streichen. Das ist wichtig, weil der heutige Pfad diese frühe Persistierung
bewusst nutzt; Stopbedingung 6 fängt den Fall ab, dass die Umstellung
Crashsicherheit aufhebt oder doppelte Providerstarts ermöglicht. Damit ist
meine Hauptsorge an dieser Stelle adressiert.

Die Pre-Policy-Grenze ist sauber gezogen: B.6 verlangt einen positiven,
fail-closed geführten Nachweis der historischen Klasse und verbietet
ausdrücklich, dass ein aktueller request-gebundener Record diese Ausnahme
erreicht; die Negativkontrolle in Teil B deckt das Fälschen oder Entfernen des
Bindings ab, Stopbedingung 4 die Nichtunterscheidbarkeit. Ein modernes
Writerschema wird historischen Resultaten nicht rückwirkend übergestülpt — die
Regel bleibt an Arbeitsplan 4.1.5 gebunden.

Die Regressionsliste deckt Claude und Codex jeweils live und im Record-Ahead
ab, einschließlich der Forderung, dass der Provider beim Recovery nicht erneut
gestartet wird.

### 3. Finalreview-Domäne

B.7 macht die Finalregel unabhängig von `allow_new_observations` und liegt
damit richtig. Der reproduzierte `BLOCKER → OBSERVATION`-Angriff ist von
Akzeptanzkriterium 5 und der Regressionsliste vollständig erfasst. Eine
Providerausnahme entsteht dafür zu Recht nicht: Der Writer lehnt den Fall
bereits ab, die lokale Regel ist die zweite Schutzschicht, und ein hinter einem
writer-validen Dokument erreichbarer Code entsteht nicht. Abschnitt 3 hält
zusätzlich fest, dass die Ausnahmeliste nicht wachsen darf.

### 4. Request- und Bundlebindung

Teil C deckt normale Claude-Requests, Codex-Requests und Repairrequests
getrennt und jeweils angemessen ab. Besonders gut: C.5 stellt ausdrücklich
klar, dass gleiche Writerschemabytes **kein** Ersatz für Kontextgleichheit
sind — genau die Lücke, durch die das Claude-Gegenbeispiel schlüpfte. C.7
verbietet die synthetische `BoundNativeReviewContext`-Instanz mit übernommener
Request-ID und verlangt stattdessen den Nachweis der Parentbindung; damit
entsteht keine zirkuläre Identität. C.2 rechnet den Digest unabhängig vom
Builder nach, was die Codex-Bytefälschung schließt.

Die Codex-Assetprüfung in C.6 ist vollständig für Inline- und
`content_ref`-Evidenz und benennt fehlende, zusätzliche, doppelte und
vertauschte Assets; die Materialisierung erst nach erfolgreicher Bundleprüfung
ist bereits durch die Konstruktionsreihenfolge gegeben.

Die Machbarkeit im deklarierten Änderungspfad habe ich geprüft: Eine kanonische
Codex-Kontextprojektion existiert bisher nicht — die kontextabhängigen
Requestfelder werden inline im Builder erzeugt (`src/native_codex_request.py`,
`codex_contract`, `open_findings`). Diese Datei steht im Pfad, ebenso
`src/native_review_contract.py` für die vorhandene
`native_review_context_binding()`. Ein Eingriff in
`src/native_codex_contract.py` ist nicht erforderlich. Die drei von
`_contract_document()` nicht serialisierten `CodexStepContract`-Felder
(`require_validation`, `expected_validation_command`,
`red_state_followup_slice`) werden in `src/native_codex_contract.py`
nachweislich nirgends ausgewertet; die Projektion kann daher semantisch
vollständig sein, ohne das Codex-Requestschema zu ändern. Damit ist Teil C
innerhalb des Pfads umsetzbar — anders als seinerzeit bei `C-12`.

### 5. Canary-Evidenz

Teil D adressiert meinen Blocker vollständig und geht darüber hinaus. D.1/D.2
frieren den tatsächlich verwendeten `base_commit` in der Manifestzeile ein,
D.3 verbietet dem Rekonstruktionstest den aktuellen `HEAD`, D.7 verlangt einen
Test, der einen abweichenden `HEAD` simuliert und dieselbe historische
Request-ID fordert, mit ausdrücklichem Verbot einer reinen Präfix- oder
Längenprüfung. Das bloße Nachziehen der Manifest-ID ist im Defektabschnitt
explizit als unzulässige Korrektur benannt.

Entscheidend für meinen ursprünglichen Einwand: D.4 inventarisiert **alle
sieben** `live_canary`-Zeilen, nicht nur `convergence`. D.4 verbietet zugleich
das Raten einer historischen Bindung, und D.5 regelt den Fall der
Nichtbeweisbarkeit sauber — die historische Zeile bleibt als Auditangabe
erhalten, zählt aber nicht mehr als geschlossener Writerbeleg, und erst nach
grüner providerfreier Matrix wird gezielt ein neuer isolierter Canary
ausgeführt. D.8 hält acht Writerformen und die Trennung von `schema_only`,
`request_bound` und `live_canary` fest.

Der Selbstbezug zwischen Commit, HEAD, Canary und Attestierung löst sich
strukturell auf: Nach dem Einfrieren hängt kein Test mehr vom lebenden `HEAD`
ab, weshalb Akzeptanzkriterium 16 („vollständige Repositorysuite am finalen
Implementierungscommit") widerspruchsfrei erfüllbar wird.

### 6. Plan- und Scopequalität

Reader-Baselines, historische Rohantworten und Provider-Ausnahmeliste bleiben
ausdrücklich unverändert (Abschnitt 3, Kriterien 13 und 14, Stopbedingungen 1
und 2). Die freigegebene `request_bound`-Alternative wird nirgends rückwirkend
verschärft; die Corpuspaare bleiben ohne synthetische Kontextannahme lesbar.
Die 17 Akzeptanzkriterien sind sämtlich ausführbar formuliert, und die
Stopbedingungen decken die realistischen Abbruchlagen ab, einschließlich der
für mich wichtigsten — Crashsicherheit (6) und nicht beweisbare Canarybindung
(5).

Kein Akzeptanzkriterium ist rein deklaratorisch; Kriterium 17 bindet die
Schließung von `C-30` korrekt an den Nachweis der commitstabilen Bindung statt
an die Planfreigabe.

### 7. Befunde

Kein ausführbarer Planmangel. Zwei nicht blockierende Präzisierungen, beide
vor Implementierungsbeginn disponierbar, siehe `C-31` und `C-32`.

REVIEWER: claude

NEW_FINDING: C-31 | OBSERVATION | Der Plan stuft den Finalreview-Observation-Bypass durchgängig als Folge eines „fehlerhaft konstruierten Kontexts" ein (Abschlussinvariante 4, Akzeptanzkriterium 5: „unabhängig von fehlerhaften Kontextflags"). Das ist am Repository widerlegt: `WorkUnitKind` kennt einen eigenen Wert `FINAL_REVIEW` (`src/workflow_state.py:61`), und die produktive Kontextkonstruktion setzt `allow_new_observations=unit.kind is not WorkUnitKind.CORRECTION` (`src/workflow.py:2243`, `:2433`, `src/orchestrator.py:1594`). Für jeden Finalreview, der nicht in einer Korrektur-Work-Unit läuft, gilt produktiv also `allow_new_observations=True` — genau der Wert, unter dem ich den `BLOCKER → OBSERVATION`-Angriff reproduziert habe (`writer=rejected`, `local=ACCEPTED`, resultierend `C-01 OBSERVATION OPEN`). Der Bypass ist damit kein hypothetischer Fehlkonstruktionsfall, sondern auf dem regulären Finalreviewpfad erreichbar, sobald CR-04 die Writerprüfung nicht lokal durchsetzt. Die vorgesehene unbedingte Finalregel behebt ihn zwar unabhängig von dieser Einordnung; die falsche Schwereeinschätzung kann jedoch dazu führen, dass die Regression einen künstlichen statt des produktiven Kontexts verwendet und dass Roadmap und Sliceprotokoll das Risiko zu niedrig ausweisen. | Der Plan beschreibt den Befund als produktiv erreichbaren Pfad und benennt die drei Fundstellen der Kontextkonstruktion; die zugehörige Regression baut den Finalkontext über dieselbe Konstruktionsregel `unit.kind is not WorkUnitKind.CORRECTION` statt über ein handgesetztes Flag und weist nach, dass der Angriff dort sowohl am Writer als auch an der neuen unbedingten lokalen Finalregel scheitert.

NEW_FINDING: C-32 | OBSERVATION | Akzeptanzkriterium 6 verlangt, dass beide Bundletypen „kanonische Requestbytes vollständig an Requestdigest, Request-ID, lokalen Kontext und Responsevertrag" binden, und Teil C.4 nennt für Codex „kompletter `CodexStepContract` und offene Findingprojektion". Für Codex ist eine vollständige Kontextkopplung in diesem Sinne strukturell nicht erreichbar: Das Requestdokument serialisiert unter `open_findings` ausschließlich Findings mit `status is FindingStatus.OPEN` (`src/native_codex_request.py:308-317`), während die lokale Auswertung über `prior = context.previous_findings` und `_apply_dispositions()` auf der **vollständigen** Findingmenge arbeitet und `findings=prior` in das `CodexContractResult` zurückgibt (`src/native_codex_contract.py:444`, `:548-566`). Zwei Bound Contexts, die sich ausschließlich in ihren geschlossenen Findings unterscheiden, erzeugen daher bytegleiche kanonische Requests und dieselbe `request_id`, liefern aber verschiedene lokale Ergebnisse. Die Bundle-Selbstprüfung kann diesen Drift nicht erkennen, ohne das Codex-Requestdokument zu erweitern — und `schemas/native-agent-codex-request-v1.schema.json` steht bewusst nicht im Änderungspfad. Der Plan adressiert die Lage implizit richtig, indem C.8 die Kontextrekonstruktion an die autoritative Workflowbindung statt an die Requestbytes knüpft; er benennt die Grenze aber nirgends, sodass Kriterium 6 wörtlich unerfüllbar bleibt und eine spätere Prüfung „vollständige Kopplung" behaupten könnte, die es für geschlossene Codex-Findings nicht gibt. | Der Plan hält ausdrücklich fest, dass die Codex-Requestprojektion nur offene Findings transportiert, dass die Bundlekopplung deshalb genau über die transportierte kanonische Projektion vollständig ist und dass geschlossene Findings ihre Autorität aus der Workflow-Recordkette beziehen; Akzeptanzkriterium 6 wird entsprechend auf die kanonische Kontextprojektion bezogen formuliert, und eine Regression belegt, dass ein ausschließlich in geschlossenen Findings abweichender Bound Context entweder erkannt wird oder nachweislich zu identischem lokalem Ergebnis führt.

FINDING_STATUS: C-30 | OPEN | Bleibt bis zum Implementierungsnachweis offen. Slice 05 bildet mein Akzeptanzkriterium vollständig ab und übertrifft es: Teil D.1/D.2 friert den tatsächlich verwendeten `base_commit` in der Manifestzeile ein, D.3 verbietet dem Rekonstruktionstest den lebenden `HEAD`, D.7 verlangt eine Regression, die einen abweichenden `HEAD` simuliert und dieselbe historische Request-ID bitgenau fordert, wobei eine reine Präfix- oder Längenprüfung ausdrücklich unzulässig ist, und Akzeptanzkriterium 16 verlangt die Attestierung der vollständigen Suite am finalen Implementierungscommit. Über mein Kriterium hinaus inventarisiert D.4 alle sieben `live_canary`-Zeilen statt nur `convergence`, verbietet das Raten historischer Bindungen und regelt in D.5 den Fall der Nichtbeweisbarkeit fail-closed. Das bloße Nachziehen der Manifest-ID auf den aktuellen `HEAD` ist explizit als unzulässige Korrektur benannt. Geschlossen wird `C-30` erst, wenn die eingefrorene Bindung implementiert und die commitstabile Reproduktion nachgewiesen ist.

PRE_MORTEM: In drei Monaten ist die wahrscheinlichste Fehlerursache, dass Teil B unter Zeitdruck auf die einfache Lesart reduziert wird: Writerprüfung einbauen, Rohpersistierung dahinter schieben, fertig. Damit verschwindet unbemerkt die Raw-first-Crashsicherheit, für die es heute einen eigenen Test gibt — ein Providerlauf, der nach der Antwort, aber vor der Domänenkonvertierung abbricht, hinterlässt dann keine Bytes mehr, und die nächste Recovery startet den Provider erneut. Weil Stopbedingung 6 zwar existiert, aber niemand sie prüft, solange die Matrix grün ist, fällt das erst bei einem echten Abbruch auf. Die zweite, leisere Variante betrifft Teil D.5: Lässt sich der `base_commit` einer der sieben historischen Canaryzeilen nicht beweisen, ist der bequeme Ausweg, die Zeile stillschweigend als weiterhin gültig zu führen, statt sie auf Auditstatus zurückzustufen und einen neuen isolierten Canary auszuführen — die Evidenzklasse `live_canary` verlöre damit genau die Aussagekraft, die dieses Arbeitspaket ihr gegeben hat.

PLAN_APPROVAL: YES

STATUS: DONE

---

## Implementierungsbericht Slice 05

**Stand:** Implementiert; bereit für den manuellen Claude-Slice-Review

**Implementierungsbasis:** `5ec8d0d` (`docs(native-contracts): record final adversarial reviews`)

**Reviewmodus:** Manuell außerhalb von `run_task`; kein Antigravity-Aufruf

### Disposition der Planreview-Befunde

- **C-30 – umgesetzt, Reviewer-Schließung ausstehend:** Die sieben
  `live_canary`-Zeilen tragen nun den nachweislich bei ihrem Lauf verwendeten
  `base_commit`. Ihre Bundlebuilder rekonstruieren Request-ID und
  Writerschemadigest aus diesem eingefrorenen Commit und nicht mehr aus dem
  aktuellen `HEAD`.
- **C-31 – akzeptiert und umgesetzt:** Der Finalreview-Bypass ist als regulär
  erreichbarer Produktionspfad dokumentiert. Die Regression leitet
  `allow_new_observations` mit der produktiven Regel
  `unit.kind is not WorkUnitKind.CORRECTION` für eine
  `FINAL_REVIEW`-Work-Unit her. Sowohl Writerschema als auch lokale
  Schutzschicht verweigern dort `BLOCKER -> OBSERVATION`.
- **C-32 – akzeptiert und umgesetzt:** Die Codex-Bundlebindung ist ausdrücklich
  auf die transportierte kanonische Projektion mit offenen Findings bezogen.
  Geschlossene Findings werden nicht in den Request erfunden; ihre Autorität
  bleibt in der Workflow-Recordkette. Regressionen belegen sowohl die identische
  Transportprojektion bei ausschließlich geschlossenem Findingdrift als auch
  die fail-closed Erkennung eines abweichenden autoritativen State-Mirrors.

### Umgesetzte Vertragskorrekturen

1. **Konkrete Writerschema-Autorität vor der Domäne**

   Native Claude- und Codex-Antworten werden nach dem Reader-Baselinecheck gegen
   exakt das im gebundenen Requestbundle enthaltene Writerschema geprüft. Erst
   danach folgen Request-ID-Prüfung und lokale Domänenkonvertierung. Bytes, die
   das konkrete Writerschema verletzen, erreichen weder den autoritativen
   Resultatpfad noch den Callback für validierte diagnostische Rohantworten.

2. **Record-Ahead- und Recovery-Gleichlauf**

   Die produktiven Recoverypfade für aktuelle native Claude- und
   Codex-Resultate wenden dieselbe konkrete Writerprüfung vor der lokalen
   Konvertierung an. Der vor der Current-Diff-Policy laufende Claude-Recoverypfad
   rekonstruiert das aktuelle gebundene Writerschema aus dem persistierten
   Kontext. Nur echte Legacy-/Corpusdaten bleiben an ihren ausdrücklich
   dokumentierten Readernachweis gebunden; es wurde keine neue
   Providerausnahme eingeführt.

3. **Finalreview-Zustandsinvariante**

   In einem Finalreview darf ein bestehendes Finding nur dann als Observation
   resultieren, wenn es bereits zuvor eine Observation war. Damit kann ein
   regulärer Finalreview seinen notwendigen offenen Blocker weder durch
   Wiederöffnen noch durch `BLOCKER -> OBSERVATION` umgehen, unabhängig vom
   produktiv gesetzten `allow_new_observations=True`.

4. **Vollständige Bundle-Selbstprüfung**

   Claude- und Codex-Bundles rechnen kanonische Requestbytes, Digest und
   präfixgebundene Request-ID unabhängig nach und vergleichen die transportierte
   Kontextprojektion mit dem Bound Context. Claude-Repairbundles benötigen und
   prüfen ihr unveränderliches Parentbundle. Codex-Evidenzassets werden für
   Inline- und `content_ref`-Formen vollständig, eindeutig und byte-/digestgenau
   gebunden; fehlende, zusätzliche, doppelte, vertauschte oder veränderte Assets
   werden fail-closed abgewiesen.

5. **Commitstabile Canary-Evidenz**

   Der Live-Canary friert seinen `base_commit` einmal vor der Bundleerzeugung ein
   und schreibt ihn ins Manifest. Alle sieben vorhandenen `live_canary`-Zeilen
   wurden repositorybasiert inventarisiert und ohne Provideraufruf auf ihre
   historische Bindung zurückgeführt. Eine Regression setzt den lebenden
   `HEAD` absichtlich auf einen anderen Wert und verlangt trotzdem für jede
   Zeile die exakte historische Request-ID und den exakten Writerschemadigest.
   Die bestehenden Rohantwortbytes und ihre SHA-256-Werte blieben unverändert.

### Geänderte produktive Pfade

- `src/agent_runtime.py`
- `src/native_codex_request.py`
- `src/native_review_contract.py`
- `src/native_review_request.py`
- `src/orchestrator.py`
- `scripts/native_contract_probe.py`
- `tests/fixtures/native-contract-corpus/manifest.json`

### Geänderte Test- und Dokumentationspfade

- `tests/test_agent_runtime.py`
- `tests/test_native_codex_request.py`
- `tests/test_native_contract_corpus.py`
- `tests/test_native_contract_probe.py`
- `tests/test_native_review_contract.py`
- `tests/test_native_review_request.py`
- `tests/test_orchestrator_runtime.py`
- `docs/internal/native-agent-contract-closure-arbeitsplan.md`
- `docs/internal/ORCHESTRATOR_ROADMAP_PHASE_2_PLUS.md`
- `docs/internal/native-agent-contract-closure-slice-05-review.md`

### Validierung

- Fokussierte Vertrags- und Recoveryprüfungen: zuletzt `159 passed`.
- Zusätzliche gezielte Gegenproben für Finalreview, Bundlebindung und
  Recordautorität: `4 passed`.
- Vollständige Repositorymatrix:
  `python3 -m pytest tests/ -v -p no:cacheprovider` ->
  `1280 passed in 187.33s`.
- `git diff --check`: sauber.
- Externe Provider-Canaries wurden nicht wiederholt. Die vorhandenen sieben
  Canarybindungen wurden providerfrei aus ihrem jeweils eingefrorenen
  `base_commit` reproduziert.

### Übergabe

Claude soll im Implementierungsreview insbesondere `C-30`, `C-31` und `C-32`
selbst schließen oder begründet offenhalten. Da Slice 05 eine
Konvergenzkorrektur ist, gehört ein neu entdeckter ausführbarer Defekt als
`BLOCKER` in den Review; neue Observations sind nicht zulässig.

IMPLEMENTATION_READY: 05 | YES

STATUS: DONE

---

## Claude Implementierungsreview Slice 05

Datum: 24. August 2026 · Reviewer: Claude · Modus: read-only, adversarial

Konvergenzkorrektur nach `AGENTS.md`: keine neue `OBSERVATION`; ein neu
entdeckter ausführbarer Defekt wäre ein denyender `BLOCKER`, nicht ausführbare
Restrisiken stehen ausschließlich in `REVIEW_EVIDENCE`. Grundlage sind
`git diff 5ec8d0d`, die noch unversionierte Slice-Datei, beide Gesamtreviews
und der freigegebene Sliceplan. Weder Produktcode noch Tests wurden verändert.
Die vollständige Suite wurde nicht erneut ausgeführt und kein externer Canary
wiederholt; ausgeführt wurden ausschließlich providerfreie Gegenproben und ein
fokussierter Lauf von zwei Testdateien zur Falsifikation einer konkreten
Behauptung.

Alle geänderten Dateien liegen innerhalb des freigegebenen Änderungspfads;
`src/native_provider_schema.py`, `tests/test_native_contract_differential.py`
und `tests/test_workflow.py` blieben unberührt.

### 1. Konkrete Writerschema-Autorität

Die Reihenfolge im Livepfad ist für beide Provider identisch und korrekt
(`src/agent_runtime.py:1305-1317`, `:1475-1489`): JSON dekodieren → Objektform →
Reader-Baseline → **konkretes Writerschema** → Request-ID → Raw-Callback →
Domänenkonvertierung.

Ich habe die Vollständigkeit nicht anhand des Berichts, sondern über alle
Aufrufstellen geprüft: Es existieren genau fünf produktive
`parse_bound_native_*`-Aufrufe, und **jedem einzelnen** geht unmittelbar eine
Writerprüfung voraus — `agent_runtime.py:1309/1317`, `:1479/1487`,
`orchestrator.py:1051/1052`, `:1400/1401`, `:1611/1614`. Es bleibt keine
ungeschützte Domänenkonvertierung übrig.

Die Prüfung ist an die richtige Quelle gebunden:
`validate_native_review_provider_response()` verwendet
`bundle.provider_response_schema`, also exakt die unveränderlichen Bytes, die
dem Provider übergeben wurden, und rekonstruiert ausschließlich die
festgelegte Hülle `{"result": document}`. Sie berechnet keine alternative
Schemaform aus losen Laufzeitwerten.

Besonders bemerkenswert ist die Platzierung: Weil die Writerprüfung **vor**
dem `validated_response_callback`, die Domänenkonvertierung aber **danach**
liegt, bleibt die Raw-first-Crashsicherheit vollständig erhalten. Writer-valide,
aber lokal an einer registrierten Providerausnahme scheiternde Bytes werden
weiterhin diagnostisch persistiert, erhalten aber keine Autorität — genau die
Trennung, die Planpunkt B.4 verlangt, ohne den Pfad umzubauen. Die Regression
`test_native_codex_writer_invalid_bytes_never_reach_validated_callback`
behauptet dafür `persisted == []` und `"schema-invalid" in technical_text`.

Der Pre-Current-Diff-Policy-Pfad ist korrekt eingeordnet: Er rekonstruiert
seinen `NativeReviewContext` aus dem laufenden Workflowzustand
(`src/orchestrator.py:1586-1604`) und ist damit ein Pfad für **aktuelle**
request-gebundene Records, nicht für Legacyantworten. `..._for_context()` leitet
das Writerschema deterministisch aus genau diesem Kontext ab. Meine
Planreviewsorge, ein heutiges Writerschema könne auf historische Resultate
angewandt werden, greift hier nicht; echte Legacy-/Corpusfälle laufen
unverändert über den `request_bound`-Nachweis des Corpus. Eine neue
Providerausnahme entstand nicht.

### 2. Finalreview-Invariante und `C-31`

`_validate_decision()` bindet die Finalregel nicht mehr an
`allow_new_observations`: Die Bedingung lautet jetzt
`previous is None or previous.finding_class is not FindingClass.OBSERVATION`.
Für den Konvergenzast (`not allow_new_observations`) ist das semantisch
identisch zur alten Fassung; verschärft wird ausschließlich der Fall
`FINAL` mit `allow_new_observations=True` — exakt die Lücke aus `C-31`.

Ich habe den Kontext über die produktive Regel hergeleitet
(`WorkUnitKind.FINAL_REVIEW is not WorkUnitKind.CORRECTION → True`) statt das
Flag von Hand zu setzen:

| Antwort im regulären Finalreview | Writer | Lokal |
|---|---|---|
| `BLOCKER C-01 → OBSERVATION` | rejected | `approval-invalid` |
| neue `OBSERVATION` | rejected | `approval-invalid` |
| bestehende `OBSERVATION C-03` bleibt Observation | rejected | ACCEPTED |
| Denial ohne Findingevent bei zwei offenen Blockern | VALID | ACCEPTED |

Der Angriff scheitert damit auf **beiden** Schichten. Eine bereits zuvor
bestehende Observation wird von der lokalen Schutzschicht nicht versehentlich
verboten. Der erforderliche resultierende offene Blocker lässt sich weder durch
Wiederöffnen (seit `C-29`) noch durch Reklassifizieren (seit `C-31`)
erschleichen. Der Regressionstest
`test_production_final_context_cannot_reclassify_blocker_to_observation` leitet
das Flag über dieselbe produktive Regel ab.

### 3. Bundle-, Kontext- und Assetbindung sowie `C-32`

Alle vier `CR-05`-Gegenbeispiele werden jetzt mit unterscheidbaren, sachlich
präzisen Fehlern abgewiesen — direkt über den Bundlekonstruktor, nicht nur über
die stets konsistenten Builder:

- Claude-`run_id`-Drift → `request-invalid: request document differs from its bound review context`;
- Codex-gefälschte kanonische Bytes bei unveränderter `request_id` → `request-invalid: request content differs from its bound digest`;
- Codex-`content_ref` ohne Assets → `evidence-invalid: request evidence assets differ from content references`;
- doppeltes Codex-Asset → `evidence-invalid: request evidence asset paths must be unique`.

Das Claude-Repairbundle verlangt sein unveränderliches Parentbundle
(`src/native_review_request.py:227-247`) und vergleicht Bound Context,
`parent_request_id`, `current_fingerprint` und `response_contract`. Ein
fehlender oder vertauschter Parent scheitert vor Providerstart; eine bloß
synthetische Identität genügt nicht mehr.

Die von `C-32` verlangte Grenze ist sauber gezogen und — wichtiger — sie ist
nicht kaschiert. Ich habe sie empirisch nachgestellt: Zwei Codex-Kontexte, die
sich ausschließlich in einem **geschlossenen** Finding unterscheiden, erzeugen
bytegleiche kanonische Requests, dieselbe `request_id` und dasselbe
Writerschema, liefern aber verschiedene lokale `findings`. Geschlossene
Findings werden also korrekt **nicht** in die Requestprojektion erfunden
(`open_findings == []`), und ihre Autorität liegt in der Recordkette. Genau
dort greift die Absicherung: Der erweiterte Test
`test_combined_native_finding_authority_rejects_state_mirror_drift` führt jetzt
ein geschlossenes Finding und weist nach, dass ein manipulierter geschlossener
Mirrorbestand mit `differs from the state-v3 mirror` fail-closed abgewiesen
wird. `test_closed_findings_are_record_authority_not_codex_request_fields`
dokumentiert die Transportgrenze als Regression.

### 4. Commitstabile Canary-Evidenz und `C-30`

Alle sieben `live_canary`-Zeilen tragen einen konkreten historischen
`base_commit`. Ich habe jede einzelne aus ihrem eingefrorenen Commit
rekonstruiert:

| Zeile | base_commit | ≠ HEAD | request_id | Writerdigest |
|---|---|---|---|---|
| codex/plan | `7ba867dd246c` | ja | reproduziert | reproduziert |
| codex/implementation | `7ba867dd246c` | ja | reproduziert | reproduziert |
| codex/correction | `7ba867dd246c` | ja | reproduziert | reproduziert |
| codex/final_report | `7ba867dd246c` | ja | reproduziert | reproduziert |
| claude/plan | `7ba867dd246c` | ja | reproduziert | reproduziert |
| claude/convergence | `eb38a316a7ee` | ja | reproduziert | reproduziert |
| claude/final | `7ba867dd246c` | ja | reproduziert | reproduziert |

Jeder eingefrorene Commit weicht vom lebenden `HEAD` (`5ec8d0d`) ab — die
Reproduktion ist damit kein Zufall, sondern Beleg der HEAD-Unabhängigkeit. Die
Gegenprobe bestätigt es: Baut man dieselbe Zeile ohne `base_commit`, weicht die
`request_id` ab. Die Commitzuordnung ist historisch stimmig statt geraten
(Slices 01–03 bei `7ba867d`, der Slice-04-Konvergenzcanary bei `eb38a31`).

Der Live-Canary friert seinen `base_commit` **einmal vor** der Bundleerzeugung
ein (`scripts/native_contract_probe.py`), übergibt ihn an den Builder und gibt
ihn im Report zurück; `base_commit or _head(...)` fällt nur ohne expliziten Wert
auf den HEAD zurück.

Die Regression `test_all_live_canaries_rebuild_from_frozen_base_not_current_head`
monkeypatcht `_head` auf einen abweichenden Wert, iteriert **alle sieben**
Zeilen, verlangt volle Stringgleichheit der `request_id` — keine Präfix- oder
Längenprüfung — und enthält zusätzlich eine Negativkontrolle, die beweist, dass
der eingefrorene Wert tragend ist.

Entscheidend für die Wurzel von `C-30`: Ich habe geprüft, dass **kein** Test
mehr den lebenden Repository-HEAD für eine Gleichheitsbehauptung liest. Der
einzige Canary-Bundlebau in den Tests übergibt den eingefrorenen
`base_commit`. Ein Commit dieses Stands kann die Matrix daher nicht mehr
brechen — genau der Selbstbezug, an dem der letzte Abschlussversuch scheiterte.
Der fokussierte Lauf von `test_native_contract_probe.py` und
`test_native_contract_corpus.py` ist am jetzigen Stand grün (`7 passed`).

Die drei historischen Rohantworten sind unverändert: `corpus.jsonl` ist
bytegleich, alle `response_sha256` stimmen, und der gesamte Manifestdiff besteht
ausschließlich aus den sieben hinzugefügten `base_commit`-Zeilen. Die
Evidenzklassen bleiben getrennt — 14 Fixtures (elf `schema_only`, drei
`request_bound`), acht Writerformen (sieben `live_canary`, ein
`request_bound`), und `base_commit` erscheint ausschließlich in
`live_canary`-Zeilen.

### 5. Plan-, Reader- und Dokumentationsgrenzen

`schemas/` ist gegenüber `5ec8d0d` vollständig unverändert: Beide
Reader-Baselines und die Providerausnahmeliste (unverändert 13 Einträge) wurden
weder gelockert noch erweitert. Das ist konsistent damit, dass
`finding-reference-not-open` und die neuen Bundlefehler keine hinter einem
writer-validen Dokument erreichbaren Codes sind.

`CR-03` ist korrekt geschlossen: `--max-budget-usd` ist aus der abschließenden
`semantic_flags`-Liste entfernt und ausdrücklich als „kostensteuernder,
profilfremder Laufzeitwächter" bei den ausgeschlossenen Parametern benannt. Die
historische `C-25`-Observation blieb unangetastet.

Die Roadmap benennt beide von mir im Planreview verlangten Präzisierungen
wörtlich: dass der reguläre Finalreviewpfad `allow_new_observations=True` setzt
und dass geschlossene Codex-Findings Autorität der Workflow-Recordkette
bleiben. Arbeitsplan, Slicebericht, Roadmap, Code, Tests und Corpusmanifest
treffen damit dieselben Aussagen.

Die 17 Akzeptanzkriterien sind erfüllt; die Stopbedingungen wurden nicht
ausgelöst, insbesondere nicht Nummer 6 (Crashsicherheit bleibt erhalten) und
nicht Nummer 2 (keine neue Providerausnahme).

### Neue BLOCKER

Keine.

REVIEWER: claude

FINDING_STATUS: C-30 | CLOSED | Die historische Canarybindung ist nachweislich commitstabil. Alle sieben `live_canary`-Zeilen tragen einen konkreten historischen `base_commit`, und ich habe für jede einzelne sowohl die persistierte `request_id` als auch den `writer_schema_sha256` aus genau diesem eingefrorenen Commit reproduziert; jeder dieser Commits weicht vom lebenden `HEAD` `5ec8d0d` ab, sodass die Reproduktion die HEAD-Unabhängigkeit belegt und nicht zufällig ist. Die Gegenprobe ohne `base_commit` liefert eine abweichende `request_id`, der eingefrorene Wert ist also tragend. Die Regression `test_all_live_canaries_rebuild_from_frozen_base_not_current_head` monkeypatcht `_head`, prüft alle sieben Zeilen auf volle Stringgleichheit statt Präfix oder Länge und enthält eine Negativkontrolle. Der Live-Canary friert seinen `base_commit` einmal vor der Bundleerzeugung ein und bindet denselben Wert in Request und Manifest. Entscheidend für die Wurzel des Findings: Kein Test liest den lebenden Repository-HEAD mehr für eine Gleichheitsbehauptung, weshalb ein Commit dieses Stands die Matrix nicht erneut brechen kann; der fokussierte Lauf ist grün. Historische Rohantworten und ihre Digests sind unverändert, der Manifestdiff besteht ausschließlich aus den sieben `base_commit`-Ergänzungen, und die Evidenzklassen bleiben getrennt.

FINDING_STATUS: C-31 | CLOSED | Die Fehleinschätzung ist korrigiert und die Regel unbedingt. `_validate_decision()` verlangt jetzt für jedes als Observation resultierende Finding, dass es zuvor bereits eine Observation war, unabhängig von `allow_new_observations`; für den Konvergenzast ist das semantisch identisch, verschärft wird ausschließlich `FINAL` mit `allow_new_observations=True`. Ich habe den Kontext über die produktive Regel `unit.kind is not WorkUnitKind.CORRECTION` für eine `FINAL_REVIEW`-Work-Unit hergeleitet statt das Flag von Hand zu setzen: `BLOCKER → OBSERVATION` und eine neue Observation scheitern dort am Writerschema **und** lokal mit `approval-invalid`, während eine bereits bestehende Observation lokal unverändert zulässig bleibt und ein reguläres Denial mit offenen Blockern weiter funktioniert. Der zugehörige Regressionstest leitet das Flag über dieselbe produktive Regel ab, und die Roadmap benennt den Pfad jetzt ausdrücklich als regulär erreichbar statt als Fehlkonstruktion.

FINDING_STATUS: C-32 | CLOSED | Die Grenze ist benannt, korrekt gezogen und an der richtigen Stelle abgesichert. Der Codex-Request transportiert weiterhin ausschließlich offene Findings; geschlossene Findings werden nicht in die Projektion erfunden. Ich habe empirisch bestätigt, dass zwei Kontexte, die sich nur in einem geschlossenen Finding unterscheiden, bytegleiche kanonische Requests, dieselbe `request_id` und dasselbe Writerschema erzeugen, aber verschiedene lokale `findings` liefern — die Bundlekopplung ist damit genau über die transportierte kanonische Projektion vollständig, nicht darüber hinaus. Die Autorität geschlossener Findings liegt entsprechend in der Workflow-Recordkette, und dort greift die Absicherung: Der erweiterte Test `test_combined_native_finding_authority_rejects_state_mirror_drift` führt jetzt ein geschlossenes Finding und weist die fail-closed Erkennung eines manipulierten Mirrorbestands mit `differs from the state-v3 mirror` nach; `test_closed_findings_are_record_authority_not_codex_request_fields` hält die Transportgrenze als Regression fest. Arbeitsplan und Roadmap benennen die Grenze ausdrücklich, sodass keine unerfüllbare Vollständigkeitsbehauptung stehen bleibt.

REVIEW_EVIDENCE: Vollständigkeit der Writerautorität über alle fünf produktiven `parse_bound_native_*`-Aufrufstellen; Reihenfolge Reader → Writer → Request-ID → Raw-Callback → Domäne in beiden Livepfaden und Erhalt der Raw-first-Crashsicherheit; Bindung der Prüfung an die unveränderlichen Bundlebytes statt an neu berechnete Schemaformen; Einordnung des Pre-Current-Diff-Policy-Pfads als aktueller statt historischer Recoverypfad; Unbedingtheit der Finalregel über die produktive Kontextkonstruktion samt Nichtverbot bestehender Observations; Abweisung aller vier Bundle-Gegenbeispiele über direkte Konstruktion; Parentbindung des Repairbundles; Codex-Assetmatrix für fehlende, zusätzliche, doppelte, vertauschte und veränderte Assets; empirische Bestätigung der `C-32`-Transportgrenze und ihrer Absicherung an der Mirrorgrenze; Rekonstruktion aller sieben Canary-Zeilen aus eingefrorenem `base_commit` samt Negativkontrolle und Nachweis, dass kein Test mehr am lebenden HEAD hängt; Unverändertheit von `corpus.jsonl`, Antwortdigests, Reader-Baselines und Ausnahmeregister; Trennung der Evidenzklassen; Konsistenz von Arbeitsplan, Roadmap, Slicebericht, Manifest, Code und Tests | Vier nicht ausführbare Restrisiken: (1) Unter den sieben Canaryzeilen wird der `writer_schema_sha256` nur für `convergence` testseitig gegen die neu gebauten Bundlebytes gebunden; für die übrigen sechs prüft der Manifestvalidator nur die Länge — ich habe alle sieben Digests unabhängig reproduziert, und der Digest ist konstruktionsbedingt HEAD-unabhängig, sodass daraus keine Instabilität folgt, wohl aber eine unbewachte Manifestfläche. (2) Der `response_sha256` der sieben Canaries bleibt mangels gespeicherter Antwortbytes lokal unverifizierbar. (3) Eine No-op-Reklassifizierung einer bereits bestehenden Observation ist im Finalast writer-seitig nicht darstellbar, lokal aber gültig; die Asymmetrie zeigt in die sichere Richtung und ist ohne fachlichen Verlust, weil ein Finalreview offene eigene Findings über `status_changes` schließt. (4) Der Pre-Current-Diff-Policy-Pfad leitet sein Writerschema aus dem rekonstruierten Workflowkontext statt aus originalen Requestbytes ab; das ist dort strukturell unvermeidbar, durch den digestgebundenen eindeutigen Log begrenzt und gegenüber dem Vorzustand strikt stärker | Jemand erweitert die Canaryliste um eine achte Writerform oder ersetzt eine bestehende Zeile und trägt `base_commit`, `request_id` und `writer_schema_sha256` von Hand ein; weil der Manifestvalidator für sechs der sieben Zeilen nur Feldlängen prüft und die Rekonstruktionsregression ausschließlich die `request_id` vergleicht, bliebe ein falscher Writerschemadigest unbemerkt, und die Evidenzklasse `live_canary` verlöre wieder die Bindung, die dieser Slice ihr gegeben hat

PRE_MORTEM: In drei Monaten ist die wahrscheinlichste Fehlerursache, dass die Reihenfolge im Livepfad bei einer Umstrukturierung unbemerkt kippt. Die gesamte Trennung zwischen diagnostischer Quarantäne und autoritativem Resultat hängt heute allein daran, dass `validate_native_*_provider_response()` vor und `parse_bound_native_*()` nach dem `validated_response_callback` steht — drei Zeilen in zwei Funktionen, deren Reihenfolge kein Test als solche festhält; abgesichert ist nur das Ergebnis `persisted == []` für einen writer-invaliden Fall. Verschiebt jemand den Callback beim Refactoring nach vorn, um „früher zu persistieren", bleiben alle Tests grün, die auf writer-invalide Bytes zielen, während writer-valide und lokal ungültige Bytes weiterhin korrekt landen — der Unterschied fiele erst auf, wenn ein Provider erstmals writer-invalides JSON liefert und dessen Bytes dann als validierte Rohantwort im Artefaktspeicher stehen. Die zweite, leisere Variante betrifft die Canary-Evidenz: Wird eine Zeile künftig ergänzt oder ersetzt, deckt die Rekonstruktionsregression nur die `request_id` ab, sodass ein von Hand eingetragener Writerschemadigest für sechs der sieben Zeilen unbemerkt falsch bleiben kann.

SLICE_APPROVAL: 05 | YES

STATUS: DONE
