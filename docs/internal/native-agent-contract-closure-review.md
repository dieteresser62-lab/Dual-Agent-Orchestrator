# Reviewprotokoll – Native Agent Contract Closure

## Status

**Planreview:** technisch blockiert, keine fachliche Claude-Entscheidung

**Implementierungsfreigabe:** nicht erteilt

Dieses Dokument protokolliert die direkten Reviews des manuellen
Contract-Closure-Arbeitspakets. Es ist eine menschenlesbare Auditansicht und
keine technische Workflow- oder Recordquelle.

## Planreview vom 23. August 2026

### Eingefrorener Reviewgegenstand

- Arbeitsplan:
  `docs/internal/native-agent-contract-closure-arbeitsplan.md`
- Basiscommit: `c410eef95164d10b1ff0d97d2a9593ca5af18324`
- Anforderungsquelle:
  `/tmp/orchestrator-stabilisierung-1-1-nachtraege.md`, ausschließlich der
  Abschnitt **Contract Closure**
- Reviewer: Claude Sonnet
- Effort: `high`
- Modus: direkter read-only Aufruf ohne `run_task`, Bash oder Schreibwerkzeuge
- Ausgabegrenze: geschlossenes temporäres JSON-Schema mit Entscheidung,
  Findings, Reviewevidenz und Pre-Mortem

### Technischer Versuch 1

- Ergebnis: `Request timed out`
- Dauer: ungefähr 182 Sekunden
- Providerstatus: `terminal_reason=api_error`
- Tokens: `input=0`, `output=0`, `thinking=0`
- Kosten: `0`
- Session-ID: `0ca03a05-90c9-4d15-acc7-872474c89149`
- Bewertung: keine Providerverarbeitung und kein fachliches Reviewurteil

### Technischer Versuch 2

Der identische gebundene Reviewauftrag wurde genau einmal wiederholt.

- Ergebnis: `Request timed out`
- Dauer: ungefähr 182 Sekunden
- Providerstatus: `terminal_reason=api_error`
- Tokens: `input=0`, `output=0`, `thinking=0`
- Kosten: `0`
- Session-ID: `3c7ab2c6-ee35-40e9-a2ad-8e3ee90bdb5a`
- Bewertung: keine Providerverarbeitung und kein fachliches Reviewurteil

### Entscheidung

Die beiden technischen Timeouts werden weder als Ablehnung noch als
Freigabe interpretiert. Es existieren keine Claude-Findings und keine
Claude-Observations aus diesen Versuchen. Die Implementierung beginnt erst,
wenn ein späterer direkter Claude-Aufruf ein vollständiges fachliches
Planreview liefert.

PLAN_APPROVAL: NOT_RECORDED

ANTIGRAVITY: NOT_RUN (manual Contract Closure process)

## Claude-Planreview vom 24. August 2026

### Eingefrorener Reviewgegenstand

- Arbeitsplan:
  `docs/internal/native-agent-contract-closure-arbeitsplan.md`
- Basiscommit: `c410eef95164d10b1ff0d97d2a9593ca5af18324`
- Anforderungsquelle:
  `/tmp/orchestrator-stabilisierung-1-1-nachtraege.md`, ausschließlich der
  Abschnitt **Contract Closure**
- Reviewer: Claude
- Modus: direktes read-only Repositoryreview ohne `run_task`, ohne
  Testausführung, ohne Änderung an Produktivcode, Tests oder Arbeitsplan
- Geprüfter Repositorystand: `src/native_codex_contract.py`,
  `src/native_codex_request.py`, `src/native_review_contract.py`,
  `src/native_review_request.py`, `src/agent_adapters.py`,
  `src/path_policy.py`, `schemas/native-agent-*.schema.json`,
  `tests/test_native_*.py`, `tests/test_language_consistency.py`,
  `docs/internal/archive/**`, `.orchestrator/artifacts/*/native-codex-responses/`,
  `.gitignore`

### Bestätigter Repositorybefund

Die Befundlage in Abschnitt 3 des Arbeitsplans ist im Kern korrekt und am
Repository nachvollziehbar:

- `native_codex_provider_response_schema()` (`src/native_codex_contract.py:208`)
  erzeugt tatsächlich die allgemeine Fünferunion ohne Kenntnis der
  `NativeCodexRequestKind`; `parse_native_codex_response()` meldet den
  Konflikt erst nachgelagert als `RESULT_KIND_MISMATCH`.
- `NativeCodexRequestBundle.__post_init__` (`src/native_codex_request.py:172`)
  prüft ausschließlich Kanonizität und Request-ID, nicht den
  `response_contract`-Digest; `NativeCodexAdapter.prepare_native_provider_input()`
  (`src/agent_adapters.py:398`) berechnet das Schema unabhängig vom Bundle neu.
  Die im Plan behauptete Lücke besteht.
- `NativeReviewRequestBundle` prüft den Digest bereits gegen
  `provider_response_schema` (`src/native_review_request.py:202`), und der
  Claude-Adapter serialisiert genau dieses Bundleschema
  (`src/agent_adapters.py:883`). Die Asymmetrie zwischen Codex- und Claude-Pfad
  ist korrekt beschrieben.
- Der Response-Schema-Digest ist Bestandteil des kanonischen Request-Bindings
  und damit der Request-ID (`src/native_codex_request.py:287-293`). Die
  Behandlung der zirkulären Grenze zwischen Request-ID und Response-Schema in
  Abschnitt 3.3 ist sachlich richtig und deckt sich mit dem bestehenden
  Docstring in `native_review_provider_response_schema()`.
- Die Corpusquellen in Abschnitt 3.4 existieren; `.orchestrator/` ist
  gitignoriert, die Übernahme als Fixture ist daher zwingend.

Die Trennung zwischen allgemeinem v1-Leseschema und request-spezifischer
Writerprojektion ist konzeptionell richtig gewählt und entspricht dem bereits
vorhandenen Muster (`finding_dispositions`-Verschärfung nur im Writer,
`maxItems=0` für Anchors ohne Origin).

### Textuelles Reviewergebnis

REVIEWER: claude

NEW_FINDING: C-01 | BLOCKER | Der Plan schließt nur antwortseitige Regeln, nicht die kontextseitig bereits entschiedenen Ergebniswerte. `_validate_decision()` (`src/native_review_contract.py:780-790`) macht eine Freigabe unmöglich, sobald `validation_attestation` fehlt, unvollständig oder FAIL ist oder `test_files` ohne `test_changes_approved` vorliegen; `_validate_test_files()` (`src/native_codex_contract.py:523`) macht `ready=true` unmöglich, wenn erwartete Testdateien ohne `test_changes_approved` gebunden sind; `native_codex_response_to_contract_result()` (`src/native_codex_contract.py:403`) weist jedes `plan_result` ab, wenn `contract.require_slice_plan` falsch ist. Alle drei Fälle hängen ausschließlich vom unveränderlichen Requestkontext ab, sind trivial schema-ausdrückbar und bleiben nach Abschnitt 4.2/4.3 dennoch provider-schema-darstellbar. Genau diese Klasse erzeugt heute die teuren Nachlaufabbrüche, die das Arbeitspaket beseitigen soll. | Abschnitt 4.2 und 4.3 fordern explizit, dass jeder allein aus dem gebundenen Kontext bereits entschiedene Ergebniswert als `const` projiziert wird (`decision` als `const denied` ohne freigabefähige Attestierung beziehungsweise ohne Testfreigabe, `ready` als `const false` bei gebundenen, nicht freigegebenen Testdateien, ausschließlich `stop_result` bei `require_slice_plan=False`), und Slice 1 beziehungsweise Slice 2 enthalten je einen Test, der für einen solchen Kontext beweist, dass die gegenteilige Antwort schon an der Providerprojektion und nicht erst lokal scheitert.

NEW_FINDING: C-02 | BLOCKER | Die gesamte Planlogik hängt an der Formel „soweit im Provider-Subset sicher ausdrückbar“, aber das tatsächlich unterstützte Subset wird nirgends empirisch festgestellt. Abschnitt 3.3 nennt nur `uniqueItems` und Regex-Lookarounds, also genau das, was der bestehende Code bereits entfernt. Ob Codex Structured Outputs und Claudes `--json-schema` die für Abschnitt 4.2.2/4.2.3 zwingenden Konstruktionen (positionsgebundene Tupel, `const`-Listen, `minItems`/`maxItems`, mehrfach verschachtelte `oneOf`-Äste) akzeptieren, wird erstmals in Slice 3 Schritt 7 geprüft. Stopbedingung 2 kann damit frühestens auslösen, wenn beide Writerslices bereits implementiert und freigegeben sind. | Der Plan zieht eine minimale, providergebundene Subset-Sonde vor Slice 1 (oder als ersten Arbeitsschritt von Slice 1) ein, die je Provider die tatsächlich akzeptierten Schemakeywords feststellt und das Ergebnis als typisierte, versionierte Subset-Tabelle im Repository ablegt; beide Writerprojektionen leiten ihre erlaubten Konstruktionen aus dieser Tabelle ab, und Stopbedingung 2 ist ab Slice 1 auslösbar.

NEW_FINDING: C-03 | BLOCKER | Die typisierte Ausnahmeliste ist widersprüchlich terminiert. Abschnitt 6 nennt „die Liste dokumentierter lokaler Schemaausnahmen“ als Pflichteingabe jedes Slice-Reviews, und die Akzeptanzkriterien von Slice 1 („verbleibende lokale Eindeutigkeitsregeln sind registriert“) und Slice 2 („ihre Restmenge gegen die typisierte Ausnahmeliste messen“) setzen sie voraus. Erzeugt wird sie aber erst in Slice 3 Schritt 4, und weder der Änderungspfad von Slice 1 noch der von Slice 2 enthält eine Datei dafür. Die Slice-1- und Slice-2-Reviewgates sind damit gegen ihre eigenen Akzeptanzkriterien nicht prüfbar. | Die typisierte Ausnahmeliste wird als konkrete Datei im Änderungspfad von Slice 1 eingeführt, in Slice 2 erweitert und in Slice 3 nur noch gegen die Differentialmatrix geprüft; Abschnitt 4.4.4 (Provider, Operation, lokale Invariante, fehlendes Schemafeature, Regressionstest) gilt ab dem ersten Eintrag.

NEW_FINDING: C-04 | BLOCKER | Die Änderungspfade von Slice 1 und Slice 2 enthalten `schemas/native-agent-codex-result-v1.schema.json` beziehungsweise `schemas/native-agent-review-result-v1.schema.json`, während Abschnitt 4.1.1 diese Dateien als unverändertes historisches Leseschema bezeichnet, Abschnitt 4.1.5 Rückwirkungsfreiheit verlangt und Stopbedingung 1 eine rückwärtsinkompatible Änderung des allgemeinen v1-Leseschemas zum Abbruchgrund erklärt. Welche Änderung dort zulässig ist, sagt der Plan nicht. Da beide Projektionen reine Ableitungen des geladenen Leseschemas sein sollen, ist eine Änderung dieser Dateien entweder unnötig oder genau der von Stopbedingung 1 verbotene Vorgang. | Entweder werden beide Resultat-v1-Schemadateien aus den Änderungspfaden von Slice 1 und Slice 2 entfernt, oder der Plan benennt die beabsichtigte, ausschließlich additive Änderung feldgenau, begründet ihre Rückwärtskompatibilität und bindet an sie einen Regressionstest, der jede archivierte historische Rohantwort weiterhin unverändert validiert.

NEW_FINDING: C-05 | BLOCKER | Der Differentialvertrag in Abschnitt 4.4 und Slice 3 Schritt 3 ist nicht falsifizierbar. Er verlangt „eine Menge gültiger und gezielt mutierter Antworten“ und „eine begrenzte kombinatorische Matrix“, ohne Mutationsklassen, Mindestabdeckung oder Negativkontrolle festzulegen. Eine Implementierung mit einer gültigen und einer offensichtlich schemaungültigen Antwort je Requestart erfüllt jedes Akzeptanzkriterium von Slice 3, ohne eine einzige Vertragslücke finden zu können. Die zentrale Abnahmebehauptung 10.6 wäre dann formal erfüllt und inhaltlich wertlos. | Abschnitt 4.4 legt die Pflichtmutationsklassen abschließend fest (falscher Resultattyp, fehlende, doppelte, fremde und nicht offene Findingdisposition, Findingfenster über- und unterschritten, Anchor ohne Origin, neue Observation in Korrektur- und Finalrunde, Freigabe ohne Pre-Mortem beziehungsweise ohne Evidenz, Freigabe mit offenem eigenen Blocker, Ablehnung ohne offenen eigenen Blocker, Stop mit Freigabefeldern, kontextseitig ausgeschlossene Freigabe nach C-01), verlangt je Klasse mindestens einen Fall pro betroffener Requestart und fordert eine Negativkontrolle, die beweist, dass die Matrix bei absichtlich aufgeweichter Projektion rot wird.

NEW_FINDING: C-06 | BLOCKER | Die Writerform für `final_report_result` bleibt vollständig unbelegt. Im Repository existiert keine einzige gespeicherte Codex-Finalantwort: `.orchestrator/artifacts/*/native-codex-responses/` enthält ausschließlich `plan_result`, `implementation_result`, `correction_result` und `stop_result`, und `docs/internal/archive/` enthält keine native Codex-Finalantwort. Zugleich schließt Abschnitt 4.5.4 den Codex-Finalbericht ausdrücklich aus der Canaryliste aus, obwohl Slice 1 explizit einen eigenen Finaldigest fordert. Damit erhält eine neu erzeugte, geschlossene Schemaform weder Corpus- noch Transportnachweis, und die Anforderungsquelle verlangt Live-Canaries gerade auch für Finalresultate. | Abschnitt 4.5.4 nimmt den Codex-Finalbericht in die Canaryliste auf, und die Akzeptanzkriterien von Slice 3 verlangen für jede erzeugte Writerform entweder mindestens ein Corpusfixture oder mindestens einen Live-Canary; Writerformen ohne beides sind explizit als Stopbedingung benannt.

NEW_FINDING: C-07 | BLOCKER | Die Live-Canaries sind nicht sicher eingegrenzt. `NativeCodexAdapter.prepare_native_provider_input()` startet die Codex-CLI mit `--sandbox workspace-write` und schreibt Evidenzassets nach `PROJECT_ROOT` (`src/agent_adapters.py:398-420`). Ein Canary, der den realen Transport nachweisen soll, erbt diese Schreibrechte und darf damit während desjenigen Slices, der beide Verträge einfriert, Arbeitsbaumdateien verändern. Abschnitt 4.5.4/4.5.5 und das Akzeptanzkriterium nennen nur `state.json`, Checkpoints, Recordkette, Inbox, Outbox und Git-Index; der Arbeitsbaum selbst ist nicht geschützt, und die Prüfung ist ausschließlich nachgelagert. Das widerspricht der tragenden Begründung aus Abschnitt 1, den Orchestrator während der Vertragsarbeit nicht sich selbst ausführen zu lassen. | Abschnitt 4.5 legt präventiv fest, dass Canaries in einem separaten, wegwerfbaren Arbeitsverzeichnis mit der restriktivsten vom jeweiligen Provider akzeptierten Sandbox laufen, keine Repositorypfade beschreibbar machen, und das Akzeptanzkriterium von Slice 3 nennt zusätzlich einen unveränderten Arbeitsbaum (`git status --porcelain` vor und nach jedem Canary identisch).

NEW_FINDING: C-08 | OBSERVATION | Die als „exakter Änderungspfad“ deklarierten Listen sind gegenüber dem Repositorystand ungenau. `src/native_provider_schema.py`, `tests/test_native_provider_schema.py`, `scripts/` inklusive `scripts/native_contract_canary.py` und `tests/fixtures/native-contract-corpus/` existieren nicht und sind nicht als Neuanlagen gekennzeichnet; `scripts/` wäre ein neues Wurzelverzeichnis. Umgekehrt fehlt `tests/test_native_review_schema.py` im Änderungspfad von Slice 2, obwohl dieser Test genau das dort geänderte `native-agent-review-result-v1.schema.json` prüft. Nach Stopbedingung 6 erzwingt das eine nachträgliche Pfaderweiterung mitten im Slice. | Die Änderungspfade markieren Neuanlagen ausdrücklich als solche und ergänzen `tests/test_native_review_schema.py` in Slice 2; disponierbar zu Beginn von Slice 1 beziehungsweise Slice 2.

NEW_FINDING: C-09 | OBSERVATION | Abschnitt 4.3.1 leitet die getrennten Writeräste aus „der konkreten Operation“ ab. `NativeReviewContext.operation` (`src/native_review_contract.py:145`) ist jedoch ein freier, nur auf Nichtleere geprüfter String und als Diskriminator ungeeignet; die tatsächlich typisierten Rundenmerkmale sind `approval_marker`, `allow_new_observations`, `round_number` und `anchor_origin`. Eine Astwahl über den freien String wäre nicht deterministisch stabil und würde bei neuen Operationsnamen still auf einen falschen Ast fallen. | Abschnitt 4.3.1 benennt die typisierten Kontextfelder abschließend als Astdiskriminator und schließt `operation` als Auswahlkriterium aus; disponierbar in Slice 2.

NEW_FINDING: C-10 | OBSERVATION | Der Plan erwähnt an keiner Stelle die geschlossene Transporthülle `{"result": ...}`, obwohl beide Projektionen sie heute erzeugen und der Codex-Adapter sie mit `tuple(document) != ("result",)` exakt erzwingt (`src/agent_adapters.py:471`). Eine Neuformung der Writerschemas ohne benannte Hülleninvariante kann die Extraktion still brechen oder eine zweite, abweichende Hüllenform einführen. | Abschnitt 4.1 nennt die Envelope-Form und ihre exakte Extraktionsprüfung als zu erhaltende Invariante beider Writerprojektionen; disponierbar in Slice 1.

NEW_FINDING: C-11 | OBSERVATION | Abschnitt 4.1.3 verlangt, dass die Projektion das geladene allgemeine Schema niemals mutiert. `native_review_provider_response_schema()` (`src/native_review_request.py:344-364`) mutiert das Ergebnis von `load_native_review_schema()` heute in place und ist nur deshalb harmlos, weil dieser Loader die Datei bei jedem Aufruf neu liest, anders als die Codex-Projektion, die deepcopy verwendet. Sobald der gemeinsame Projektionskern aus Slice 1 einen Cache einführt, wird daraus eine echte Querkontamination zwischen Requests. | Die Akzeptanzkriterien von Slice 1 verlangen einen Test, der nach mehrfacher Projektion mit unterschiedlichen Kontexten die Bytegleichheit des geladenen Basisschemas nachweist; disponierbar in Slice 1.

REVIEW_EVIDENCE: geprüfte Dimensionen: Korrektheit des Repositorybefunds gegen `native_codex_contract.py`, `native_codex_request.py`, `native_review_contract.py`, `native_review_request.py` und `agent_adapters.py`; fachliche Vollständigkeit und Schnittführung der drei Slices; Vertragsgeschlossenheit Request/Bundle/Provider-Schema/Adapter/Parser für Codex; Finding-IDs, Dispositionspflichten, Observationpolicy, Anchors, Pre-Mortem und Entscheidungsäste für Claude; Grenze zwischen allgemeinem v1-Leseschema und request-spezifischem Writerschema; zirkuläre Grenze zwischen Request-ID und Response-Schema-Digest; Falsifizierbarkeit, Vollständigkeit und Sicherheit von Corpus-, Differential- und Canarytests; Vollständigkeit von Dateien, Tests, Fehlerfällen und Stopbedingungen; Abgrenzung gegenüber Nicht-Contract-Closure-Bestandteilen | größtes Restrisiko: die vom Plan durchgehend verwendete Klausel „soweit im Provider-Subset sicher ausdrückbar“ ist ohne festgestelltes Subset (C-02) beliebig dehnbar und kann jede einzelne Schließungspflicht aus Abschnitt 4.2 und 4.3 folgenlos in die lokale Ausnahmeliste verschieben, sodass das Arbeitspaket formal abgenommen wird, ohne eine einzige Vertragslücke tatsächlich geschlossen zu haben | realistische Bruchbedingung: Slice 1 und Slice 2 werden implementiert und freigegeben, in Slice 3 lehnt Codex Structured Outputs die positionsgebundene Dispositionstupelform ab, die Regel wandert in die Ausnahmeliste, und die Differentialmatrix bleibt mangels Pflichtmutationsklassen (C-05) grün, obwohl die ursprünglich adressierte Diskrepanz unverändert fortbesteht.

Kein Bestandteil des Plans liegt außerhalb von Contract Closure. Die
Nichtziele in Abschnitt 9 decken die Live-Projektion, Antigravity, die
Claude-only-Reviewpolicy und den Bootstraplauf korrekt ab; die
Roadmap-Fortschreibung in Slice 3 Schritt 8 ist von der Anforderungsquelle
gedeckt. Die Antigravity-Pfade sind vom Writerschema tatsächlich unberührt,
weil `AntigravityAdapter` weiterhin die triviale Texthülle verwendet
(`src/agent_adapters.py:1092`).

PLAN_APPROVAL: NO

STATUS: DONE

### Entscheidung

Der Arbeitsplan ist fachlich tragfähig aufgebaut, sein Repositorybefund ist
korrekt und die Slice-Schnittführung Codex / Claude / Nachweis ist sinnvoll.
Freigegeben wird er nicht: sieben Blocker sind offen, davon betrifft C-01 die
tragende Invariante des Arbeitspakets selbst, C-02 und C-05 machen den
Nachweis unfalsifizierbar, und C-07 verletzt die eigene Sicherheitsbegründung
des Pakets. C-08 bis C-11 sind Observations und dürfen eine spätere Freigabe
begleiten, müssen aber den benannten Slices zugeordnet werden.

Die Implementierung beginnt erst nach Korrektur der Blocker C-01 bis C-07 im
Arbeitsplan und erneuter Vorlage.
