# Claude-Korrektur- und Abschlussreview – C-18 und C-19

**Reviewer:** Claude (manuell, adversarial) · **Datum:** 28. August 2026 ·
**Art:** branchweites Konvergenz- und Finalreview

## 1. Ergebnis vorab

`C-18` und `C-19` sind geschlossen. `C-01` bis `C-17` bleiben nach eigener
Gegenprüfung geschlossen. Kein neuer ausführbarer Defekt. Die Branchfreigabe
wird erteilt.

Beide Korrekturen sind rein test- und dokumentationsseitig: `src/workflow.py`,
`src/final_review_preflight.py` und `src/dry_run_scenarios.py` sind gegenüber
dem von mir zuvor geprüften Stand unverändert. Damit ist ausgeschlossen, dass
ein Blocker durch eine Produktionsänderung „wegdefiniert" wurde.

## 2. Reviewgrenze

Kein `run_task`, kein Watcher, kein Bootstrap, kein Provider- oder
Canaryaufruf. Kein Staging, kein Commit, kein Merge, kein Push, kein
Branchwechsel, keine Änderung an `.orchestrator`. Quellcode, Tests, Fixtures,
Konfiguration und alle bereits vorhandenen Dokumentinhalte blieben unberührt.

Die einzige Schreiboperation dieses Reviews ist die Anlage genau dieses
Dokuments — die ausdrücklich autorisierte Standardform. Es wurde keine zweite
Kopie und kein Add-on im Slice-03-Review erzeugt.

Alle Gegenproben liefen als In-Memory-AST- und Zustandsmutationen aus dem
Scratchpad; keine Mutation berührte den Arbeitsbaum. Die vollständige Suite
habe ich weisungsgemäß nicht erneut gefahren.

## 3. Selbst erhobener Repositorystand

Eigenständig ermittelt, nicht aus dem Auftrag übernommen:

- Branch: `feature/orchestrator-transition-matrix-output-resilience`
- `HEAD`: `6b609f4d23a684c3badcc99948bb3502d0839719`
- Merge-Base gegen `master`: `de1bd9ea40392c17e47efcc8d243c7de1438ae59`
- Arbeitsbaum: vier geänderte getrackte Dateien
  (`ORCHESTRATOR_ROADMAP_PHASE_2_PLUS.md`, `src/dry_run_scenarios.py`,
  `tests/test_dry_run_scenarios.py`, `tests/test_workflow_transition_matrix.py`)
  und fünf ungetrackte Dateien; der Slice-03-Stand ist weiterhin nicht
  committed
- Paketumfang: **18 Pfade** — die 17 des Vorreviews zuzüglich des
  Codex-Gesamtreviews, das nun selbst Bestandteil des Eingabesnapshots ist
- Snapshot-Fingerprint:
  **`d4628dd2a86ad414f1254f94aa3e10e3b9693f8877904382548fb65c20e7a9e0`**

Der Fingerprint wurde nach der dokumentierten Regel selbst nachgerechnet
(SHA-256 über die sortierte Folge aus Pfad, NUL-Byte, `git hash-object`-Blob-ID
und abschließendem Zeilenumbruch) und stimmt mit dem Kontrollwert überein. Zur
Absicherung habe ich zusätzlich die 17-Pfad-Variante berechnet
(`7820c2ce…`) — sie trifft nicht zu, der 18. Pfad ist also tatsächlich Teil
des Snapshots. Die Prüfung wurde unmittelbar vor der Schreiboperation
wiederholt; kein Drift.

Gegenüber dem Snapshot `4bd6b2a7…` meines Vorreviews haben sich genau sechs
Blobs geändert: die beiden Reviewdokumente, der Abschlussnachweis, das
Resilienzmanifest, `tests/test_orchestrator_resilience_evidence.py` und
`tests/test_workflow_transition_matrix.py`. Alle übrigen zwölf Pfade sind
blob-identisch, insbesondere die gesamte Slice-2-Schicht und der
Dry-Run-Harness.

## 4. Eigene Befehle und Gegenproben

| # | Gegenprobe | Ergebnis |
|---|---|---|
| K1 | Fingerprint über 18 Pfade nachgerechnet, 17-Pfad-Variante als Kontrolle | `d4628dd2…`, Kontrollwert bestätigt |
| K2 | Geänderte Blobs gegen den Vorsnapshot bestimmt | sechs von achtzehn, Produktionscode unverändert |
| K3 | Produktionsförmiges Scopegate unabhängig konstruiert und gegen die Erwartung gestellt | akzeptiert |
| K4 | Rückstufung auf `kind=policy` | abgewiesen |
| K5 | Fingerprint entfernt | abgewiesen |
| K6 | Pfadsatz verfälscht | abgewiesen |
| K7 | `HEAD-DRIFT` gegen das Scopegate gestellt | abgewiesen |
| K8 | Alle produktiven `UNEXPECTED-PATH`-Emissionsstellen gelesen | fünf Stellen, ausnahmslos `await_user_gate` mit Fingerprint |
| K9 | Produktive Scope-Emission in-memory auf `await_policy_gate` abgeschwächt | `unclassified-triplet:unexpected_file:UNEXPECTED-PATH:policy` — rot |
| K10 | `_final_review_preflight_error_codes` gegen die realen `_deny`-Aufrufe | 15 Aufrufe, 14 distinkte Konstanten, deckungsgleich |
| K11 | Alle `FinalReviewPreflightResult`-Konstruktionen und der einzige Raiser geprüft | `error_code` stammt ausschließlich aus `_deny` |
| K12 | `... or "FINAL-REVIEW-PREFLIGHT"` → `... or "ZZZ-ESCAPE"` | `unclassified-rule` **und** `unclassified-triplet` — rot |
| K13 | Fallbackzeile aus der Source-Map entfernt | beide Fehler für `FINAL-REVIEW-PREFLIGHT` — rot |
| K14 | `UNAUTHORIZED-PATH`-Zeile entfernt | beide Fehler — rot |
| K15 | Neuer typisierter `_deny`-Code upstream eingeführt | `unclassified-rule` + `unclassified-triplet` + `stale-rule` — rot |
| K16 | `FINAL-REVIEW-PREFLIGHT` auf `kind=user` umgetypt | `reason-kind-mismatch`, `case-mismatch`, `emission-count-mismatch` — rot |
| K17 | Ableitungsanker zerstört (`_deny` umbenannt bzw. Modul entfernt) | 28 bzw. 54 Fehler — fail-closed statt stiller Schrumpfung |
| K18 | Überapproximation: Zuweisungen mit `.error_code` repositoryweit | genau eine (`workflow.py:2101`) |
| K19 | Entdeckte Regelkennungen an Gatedetails | genau 16, keine unverbundenen Literale |
| K20 | Source-Map-Bidirektionalität | 36 Zeilen, 36 Fälle, keine Waise, keine fallose Zeile |
| K21 | Manifest gegen gesammelte pytest-Knoten | 11 von 11 zeichengenau deckungsgleich |
| K22 | Fokussierter Verbund selbst ausgeführt | `59 passed in 62.64s` |
| K23 | Übergangsmatrix separat wiederholt | `10 passed in 57.22s`, stabil |

Die von Codex berichteten Zahlen (`32 passed`, `59 passed`,
`1092 passed in 121.42s`) sind agent-lokale Angaben und keine
Orchestrator-Attestierung; sie sind hier nicht als eigene Ausführung geführt.
Mein eigener Verbundlauf über sechs Paketdateien deckt sich mit der mittleren
Angabe.

## 5. Disposition C-18 — Scopeverletzung produktionsförmig gebunden

**Geschlossen.**

**Fixture und Erwartung.** `_gate_report(kind="scope")` erzeugt das Gate jetzt
über `await_user_gate` mit `GateReason.UNEXPECTED_FILE`, Detailpräfix
`UNEXPECTED-PATH`, Fingerprint und exaktem Pfadsatz; `_run_gate_probe`
behauptet `kind="user"` mit demselben Fingerprint. Damit entspricht das
Szenario der Form, die die Produktion tatsächlich emittiert.

**Sicherheitsrichtung falsifiziert.** Ich habe ein produktionsförmiges
Scopegate unabhängig vom Testmodul konstruiert und fünf Varianten dagegen
gestellt (K3–K7): Die korrekte Identität wird akzeptiert; Policyform, fehlender
Fingerprint, falscher Pfadsatz und ein substituiertes `HEAD-DRIFT` werden
sämtlich abgewiesen. Der eigens ergänzte Test
`test_scope_gate_expectation_requires_production_user_binding` hält genau die
beiden erstgenannten Rückstufungen fest.

**Keine Zirkularität mit einem zweiten Literal.** Der entscheidende Punkt ist
die Bindung an die Produktion, nicht die Übereinstimmung zweier
handgeschriebener Zeichenketten. Ich habe sie in beide Richtungen geprüft:

1. Alle fünf produktiven Emissionsstellen (`src/workflow.py:1113`, `:1431`,
   `:1691`, `:2138`, `:2561`) rufen `await_user_gate` mit
   `fingerprint=changes.fingerprint` bzw. `error.fingerprint`. Der einzige
   policyartige Vorläufer ist der Codex-Stop mit `GateReason.STOP_REQUEST`,
   den `reframe_unexpected_path_stop_gate` sofort in dieses Nutzergate
   überführt. Die Kombination `unexpected_file` + `UNEXPECTED-PATH` + `policy`
   existiert produktiv nicht.
2. Die maschinelle Ableitung aus dem Produktivquelltext liefert
   `('unexpected_file', 'UNEXPECTED-PATH', 'user')`. Schwächt man die
   Produktion in-memory auf ein Policygate ab, meldet der Guard
   `unclassified-triplet:unexpected_file:UNEXPECTED-PATH:policy` (K9). Die
   Klassifikation ist also an den Produktivcode gebunden und fällt bei einer
   echten Abschwächung laut aus.

**Manifest und narrativer Nachweis.** Die Manifestzeile lautet nun „Eine
Scopeverletzung erzeugt ein fingerprintgebundenes Nutzergate mit
`UNEXPECTED-PATH` und exakt dem betroffenen Pfadsatz"; der Abschlussnachweis
§2 führt dieselbe Aussage. `EVIDENCE_ORACLE` und Manifest werden im Test auf
Gleichheit geprüft, sodass die vier Orte nicht auseinanderlaufen können, ohne
rot zu werden. Die frühere Überbehauptung — vollständige Gateidentität bei
gleichzeitig falscher Gateart — ist beseitigt.

Eine Einschränkung halte ich ausdrücklich fest, ohne sie als Befund zu führen:
Der `resume_step` variiert je Emissionsstelle (`gate_step`/`resume_step` bei
`:1113` und `:1431`, `state.current_step` bei `:2138`, keiner bei `:1691` und
`:2561`). Das Szenario behauptet die Variante ohne Resume-Schritt und deckt
damit eine von drei produktiven Ausprägungen ab. Das ist zulässig, weil die
Sollwerte literal und die Identität korrekt gebunden ist, verringert aber die
Breite dieses einen Nachweises.

## 6. Disposition C-19 — BoolOp- und `error_code`-Produzenten inventarisiert

**Geschlossen.** Alle sechs geforderten Falsifikationen habe ich selbst
gefahren; jede verhält sich wie verlangt.

**1. BoolOp-Fallback wird erfasst.** `_assigned_rule_ids` liest nicht mehr nur
`ast.Constant`-Zuweisungen, sondern alle regelidentitätsförmigen Literale im
Wertbaum. `FINAL-REVIEW-PREFLIGHT` erscheint dadurch in der Menge der an ein
Gatedetail gelangenden Kennungen — im Vorreview fehlte sie dort.

**2. Die `error_code`-Menge ist exakt und geschlossen hergeleitet.**
`_final_review_preflight_error_codes` liest die zweiten Argumente aller
`_deny`-Aufrufe in `run_final_review_preflight` und akzeptiert nur
Großbuchstabenkonstanten. Meine unabhängige AST-Zählung ergibt 15 Aufrufe mit
14 distinkten Codes (`MISSING-REFERENCE` zweimal); die vom Test literal
behauptete Vierzehnermenge ist deckungsgleich. Die Menge ist auch fachlich
geschlossen: `FinalReviewPreflightDenied` wird ausschließlich in
`orchestrator.py:559` erhoben und erhält dort unmittelbar das Ergebnis von
`run_final_review_preflight`; jedes `error_code` entsteht ausschließlich in
`_deny`. Eine beliebige offene Zeichenkette wird nicht akzeptiert.

**3. Source-Map- und Fallbindungen sind vollständig.** Fünfzehn neue Zeilen —
`FINAL-REVIEW-PREFLIGHT` plus die vierzehn typisierten Codes — tragen
`bootstrap_check`/`resume`, die Emissionsstelle `workflow._invoke_role`, einen
Produzenten und `direct_emission_count=1`. Jede besitzt einen eigenen
ausführbaren Fall. Die Inventur ist bidirektional geschlossen: 36 Zeilen zu 36
Fällen, keine Waise, keine fallose Zeile (K20).

**4. Die Rückrichtung meldet unbekannte Kennungen.** Die Bedingung
`if rule_id in concrete_rows` ist durch `if rule_id not in
GATE_FOREIGN_PREFIXES` ersetzt. Ein direktes Produktionstripel wird damit auch
dann gemeldet, wenn seine Regelkennung vollständig fehlt — genau die
Inversion, die den Befund verursacht hatte.

**5. Beide geforderten Mutationen sind rot.** `... or "ZZZ-ESCAPE"` erzeugt
`unclassified-rule:ZZZ-ESCAPE` und
`unclassified-triplet:bootstrap_check:ZZZ-ESCAPE:resume` (K12); das Entfernen
allein der Fallbackzeile erzeugt beide Fehler für `FINAL-REVIEW-PREFLIGHT`
(K13). Über die Vorgabe hinaus habe ich zwei weitere Wege geprüft: das
Entfernen der `UNAUTHORIZED-PATH`-Zeile (K14) und die Einführung eines neuen
typisierten `_deny`-Codes upstream (K15) machen den Guard ebenfalls rot,
letzteres zusätzlich über `stale-rule` für den ersetzten Code. Eine
Gateart-Verfälschung an einer der neuen Zeilen bleibt über
`reason-kind-mismatch` gebunden (K16).

**6. Keine Überapproximation.** Die `error_code`-Erweiterung greift nur, wenn
der Zuweisungswert tatsächlich ein `.error_code`-Attribut enthält;
repositoryweit trifft das auf genau eine Zuweisung zu (`workflow.py:2101`,
K18). Die Menge der an ein Gatedetail gelangenden Kennungen umfasst exakt 16
Einträge — `PROVIDER-INPUT-BUDGET`, den Fallback und die vierzehn typisierten
Codes (K19). Es werden keine unverbundenen Literale und keine fachlich
unmöglichen Identitäten in das Inventar gezogen. Alle fünfzehn neuen Zeilen
tragen `bootstrap_check`/`resume`, was der einzigen Emissionsstelle entspricht.

**Fail-closed statt stiller Verengung.** Die Ableitung stützt sich auf zwei
Namensanker (`final_review_preflight.py` und `run_final_review_preflight`) und
gibt bei deren Fehlen `set()` zurück. Das ist unkritisch, weil die
resultierende Lücke sofort auffällt: Umbenennen von `_deny` liefert 28,
Entfernen des Moduls 54 Guardfehler (K17). Ein Refactoring kann die Inventur
also nicht unbemerkt entleeren.

## 7. Kontrollierte Aussage zu C-01 bis C-17

Alle siebzehn bleiben geschlossen. Ich habe die Statuszeilen nicht
fortgeschrieben, sondern die Regressionsflächen bestimmt: Zwölf der achtzehn
Paketpfade sind blob-identisch zum bereits geprüften Snapshot, sodass für die
dort belegten Befunde konstruktiv keine Regression möglich ist. Für die sechs
geänderten Pfade habe ich gezielt geprüft:

| Befundgruppe | Kontrolle |
|---|---|
| `C-01` Oracle-Unabhängigkeit | `TRANSITION_ORACLE` und `PROVIDER_ATTEMPT_CASES` sind im Diff unberührt; die Änderungen betreffen ausschließlich Inventur, nicht die literalen Sollwerte. Klausel 14 („Sollwerte werden nicht aus der Inventur abgeleitet") bleibt gewahrt. |
| `C-02`, `C-05` Drift-/Scope-Trennung | Präfixvergleich weiterhin ab Zeichenposition null; ein substituiertes `HEAD-DRIFT` am Scopegate wird abgewiesen (K7). |
| `C-03` `PLAN_ONLY`-Abgrenzung | Arbeitsplan blob-identisch. |
| `C-04`, `C-06` Guard-Granularität | Nicht nur unverändert, sondern durch die C-19-Korrektur strikt verschärft: Der Entdeckungsweg umfasst jetzt auch die Producerform, die diese Befunde ursprünglich adressierten. |
| `C-07` Kantenraum | `EDGE_CLASSIFICATION` im Diff unberührt, Suite grün. |
| `C-08`, `C-11` Source-Map-Bindung | Bidirektionalität an 36/36 Zeilen bestätigt; Waisen- und Kindmutationen weiterhin rot (K16, K20). |
| `C-09`, `C-10` Evidenzspalten, Mutationsproben | Unverändert, im eigenen Verbundlauf grün. |
| `C-12`, `C-13`, `C-14` Feldinventur | Beide Slice-2-Testdateien, das Druckmanifest, `src/prompts.py` und `src/schema_validation.py` sind blob-identisch. |
| `C-15` Gateart | `src/dry_run_scenarios.py` blob-identisch; `_gate_kind` unverändert und weiterhin deckungsgleich mit der Source-Map. Die C-18-Korrektur betraf das Szenariofixture, nicht den Klassifikator. |
| `C-16` Manifestbindung | 11 von 11 gesammelten pytest-Knoten decken sich zeichengenau mit dem Manifest; `EVIDENCE_ORACLE` bleibt unabhängige Sollmenge (K21). |
| `C-17` Korrekturjourneys | Journeyschicht im Diff unberührt, im eigenen Lauf grün. |

## 8. Weitere geprüfte Dimensionen

**Planerfüllung.** Die Korrektur bleibt eine echte Konvergenzrunde: keine neue
Funktionalität, keine Produktionsänderung, keine Ausweitung des Pfadsatzes.
Die Nicht-Scope-Liste des Arbeitsplans ist eingehalten; insbesondere wurde
keine Vertrags-, Record- oder Gatebindung gelockert, um einen Blocker zu
schließen.

**Vollständige Gateidentität.** Das verglichene Sieben-Tupel aus Status,
Grund, ab Zeichenposition null geparster Regelkennung, Gateart, Fingerprint,
Pfadsatz und Resume-Schritt gilt jetzt für alle drei Sicherheitsszenarien in
ihrer produktiven Form. Präfixlose Familien bleiben an exakte Grund-Detail-Paare
gebunden, unbekannte Status-Grund-Kombinationen scheitern fail-closed.

**Bidirektionale Inventur.** 36 Source-Map-Zeilen, 36 ausführbare Fälle, keine
Waise; jede Zeile bindet Emissions- und Produzentenstelle, und ein direktes
Produktionstripel ohne Zeile wird nun gemeldet.

**Failure Paths.** Die neuen fünfzehn Identitäten sind sämtlich
Bootstrap-Resume-Gates aus der Final-Review-Preflight-Ablehnung — also der
Pfad, über den technische und korrekturpflichtige Preflight-Verweigerungen
persistiert werden. Dass er inventarisiert ist, schließt eine reale Lücke im
Fehlerpfadmodell.

**Resume und Idempotenz.** Record-Ahead-Resume ohne zweiten Provideraufruf und
die genau einmalige Outbox-Finalisierung sind unverändert gebunden und im
eigenen Lauf grün; die betroffenen Testpfade sind blob-identisch.

**Structured-Output-Resilienz.** Vollständig unverändert: exakter
Diagnoseenvelope transient, vier Near-Miss-Formen fail-closed, lokale
`maxLength`-Durchsetzung mit Grenzproben bei 50/80/95/100/101 Prozent, weicher
80-Prozent-Prompthinweis ohne Inhaltsverzicht.

**Dokumentkonsistenz.** Das Codex-Addendum erteilt keine Eigenfreigabe,
bezeichnet seine Zahlen ausdrücklich als nicht-attestierend und überlässt die
Schließung beider Befunde Claude. Überbehauptungen habe ich in Roadmap,
Abschlussnachweis und Codex-Gesamtreview keine mehr gefunden.

## 9. Restrisiken ohne Befundstatus

Größtes Restrisiko ist die **nicht abgeleitete Sollmenge**: Manifest,
`EXPECTED_SCENARIOS`, `EVIDENCE_ORACLE`, `GATE_SOURCE_MAP` und
`GATE_CASE_ORACLE` sind untereinander bidirektional gebunden, aber keine
dieser Mengen wird aus dem Arbeitsplan hergeleitet. Ein künftig ergänztes
Pflichtszenario gelangt nur durch menschliche Disziplin ins Inventar.

Nachgeordnet:

- Codex berichtet einen einmaligen, nicht reproduzierten Fehlschlag
  `provider attempt end cannot precede start` im Verbundlauf. Der Wert
  entsteht aus zwei realen Uhrzeitablesungen der Produktionsbridge im
  Fixture `_append_completed_provider_attempt` und trifft eine korrekte
  Produktionsinvariante; ein Rückwärtssprung der Hostuhr (unter WSL2 durch
  Zeitsynchronisation möglich) genügt als Ursache. Es ist keine vom Paket
  eingeführte Zeit- oder Kostenschwelle. Meine beiden Läufe der Matrixdatei
  waren stabil, weshalb ich das als Restrisiko und nicht als Defekt führe;
  eine injizierte Uhr an diesen beiden Bridgeaufrufen würde es beseitigen.
- `run_scripted_workflow` referenziert `slice_report` auch dann, wenn weder
  eine Slice-Work-Unit noch eine nichtleere `planned_slices`-Liste vorliegt;
  mit den elf vorhandenen Szenarien ist der Pfad unerreichbar, ein künftiges
  Szenario ohne geplante Slices bräche mit `NameError` statt mit einem
  typisierten `DryRunScenarioError`.
- `GATE_FOREIGN_PREFIXES` ist die einzige Ausnahme der neuen
  Tripelrückrichtung. Wird dort künftig eine Kennung eingetragen, die
  tatsächlich ein Gate erzeugt, verschwindet sie wieder aus der Prüfung; die
  Zeilen tragen jedoch eine Begründung und müssen weiterhin im Literalinventar
  vorkommen.
- Das Scope-Szenario deckt eine von drei produktiven `resume_step`-Ausprägungen
  ab (siehe §5).
- Die Durable-Ledger-Emulation des Testdrivers bleibt absichtlich permissiver
  als der append-only Produktionsspeicher; ihre Sicherheit hängt an der
  vorgelagerten nativen Writer- und Domänenvalidierung, die in den
  Falsifikationen nachweislich auslöst.

**Realistische Bruchbedingung:** Ein neuer Gate-Sachverhalt entsteht außerhalb
der beiden heute verfolgten Producerformen — etwa eine Regelkennung, die als
Funktionsrückgabe, Dict-Lookup oder Modulkonstante eines dritten Moduls in ein
Gatedetail fließt. Die Literalsuche, die BoolOp-Verfolgung und die
`_deny`-Ableitung sähen sie nicht, der Guard bliebe grün, und der neue
Sachverhalt käme in keinem Szenario vor.

## 10. Pre-Mortem

In drei Monaten scheitert dieses Paket am wahrscheinlichsten daran, dass die
Producerverfolgung als abgeschlossen gilt, obwohl sie zwei benannte Formen
kennt: konstante Zuweisung und BoolOp-Fallback mit typisierter
`error_code`-Menge. Eine dritte Form — eine Kennung aus einem Dict-Lookup,
einer Hilfsfunktion oder einer Modulkonstante eines bisher nicht betrachteten
Moduls — träte an dieselbe Emissionsstelle, bliebe unentdeckt, und die drei
bestehenden Mutationsproben schlügen nicht an, weil sie genau die beiden
bekannten Formen abbilden. Das wäre die dritte Wiederholung derselben
Fehlerklasse aus `C-04`, `C-06` und `C-19`; der einzige belastbare Ausweg wäre,
die Vollständigkeitszusage künftig an eine Datenflussanalyse statt an eine
Aufzählung erkannter Syntaxformen zu binden.

Zweitwahrscheinlich wird der Arbeitsplan um eine neue Pflichtkante erweitert,
ohne Manifest, `EXPECTED_SCENARIOS` und `EVIDENCE_ORACLE` mitzuführen. Alle
bestehenden Bindungen blieben gegenseitig konsistent und grün, der
Abschlussbericht wiese weiterhin drei sauber getrennte Dimensionen aus, und
der ungemessene Pfad fiele erst im realen Betrieb auf.

## 11. Gesamtbewertung

Beide Blocker sind an der Ursache behoben, nicht am Symptom: `C-18` durch die
produktionsförmige Bindung des Scope-Szenarios samt beidseitiger
Negativkontrolle, `C-19` durch die Erweiterung des Entdeckungswegs um die
tatsächlich vorhandene Producerform, die exakt hergeleitete Codemenge,
fünfzehn vollständige Inventurzeilen und die Umkehrung der
Tripel-Rückrichtung. Beide Korrekturen sind rein test- und dokumentseitig; der
Produktivcode ist unangetastet. Alle früheren Befunde halten. Die Suite ist in
meinen eigenen Läufen grün und stabil.

Der Branch ist aus meiner Sicht freigabefähig. Commit, Merge und der
nachgelagerte reale Bootstrap-Smoke-Test bleiben ausdrücklich Sache des
Nutzers beziehungsweise des Orchestrators.

```text
REVIEWER: claude
FINDING_STATUS: C-18 | CLOSED | Das Scope-Szenario erzeugt und behauptet die produktive Form: await_user_gate mit GateReason.UNEXPECTED_FILE, Regelkennung UNEXPECTED-PATH, nichtleerem Fingerprint und exaktem Pfadsatz, Gateart user. Ich habe ein produktionsfoermiges Scopegate unabhaengig vom Testmodul konstruiert; die korrekte Identitaet wird akzeptiert, waehrend Policyform, fehlender Fingerprint, falscher Pfadsatz und ein substituiertes HEAD-DRIFT jeweils abgewiesen werden. Die Bindung ist nicht zirkulaer: alle fuenf produktiven Emissionsstellen in src/workflow.py rufen await_user_gate mit Fingerprint, die maschinelle Ableitung aus dem Produktivquelltext liefert das Tripel unexpected_file/UNEXPECTED-PATH/user, und eine in-memory Abschwaechung der Produktion auf ein Policygate erzeugt unclassified-triplet. Manifest und narrativer Abschlussnachweis beschreiben nun ebenfalls das fingerprintgebundene Nutzergate; EVIDENCE_ORACLE und Manifest werden auf Gleichheit geprueft. Die frueheren Ueberbehauptung ist beseitigt.
FINDING_STATUS: C-19 | CLOSED | Die Producerverfolgung erfasst den BoolOp-Fallback error.result.error_code or FINAL-REVIEW-PREFLIGHT, und die ueber error_code erreichbare Menge wird exakt aus den _deny-Aufrufen von run_final_review_preflight abgeleitet. Meine unabhaengige AST-Zaehlung ergibt 15 Aufrufe mit 14 distinkten Konstanten, deckungsgleich mit der literal behaupteten Sollmenge; FinalReviewPreflightDenied wird nur an einer Stelle erhoben und traegt ausschliesslich diese Codes, eine offene Zeichenkette ist ausgeschlossen. Fuenfzehn neue Source-Map-Zeilen mit Emissions-, Produzenten- und Fallbindung schliessen die Inventur bidirektional bei 36 Zeilen zu 36 Faellen. _source_map_errors meldet ein direktes Tripel jetzt auch bei vollstaendig fehlender Regelkennung. Alle geforderten Falsifikationen sind rot: ZZZ-ESCAPE im BoolOp, die entfernte Fallbackzeile, zusaetzlich die entfernte UNAUTHORIZED-PATH-Zeile, ein neuer typisierter _deny-Code und eine Gateart-Verfaelschung. Keine Ueberapproximation: repositoryweit existiert genau eine Zuweisung mit .error_code, und die entdeckte Kennungsmenge umfasst exakt die 16 real emittierbaren Identitaeten. Der Verlust des Ableitungsankers ist fail-closed mit 28 bzw. 54 Guardfehlern.
REVIEW_EVIDENCE: selbst erhobener Branch-, HEAD-, Merge-Base- und Dirty-Worktree-Stand, eigenstaendig nachgerechneter 18-Pfad-Fingerprint d4628dd2 samt 17-Pfad-Kontrollvariante und unmittelbarer Wiederholung vor der Schreiboperation, Bestimmung der sechs gegenueber dem Vorsnapshot geaenderten Blobs und der zwoelf identischen, Unveraendertheit von src/workflow.py, src/final_review_preflight.py und src/dry_run_scenarios.py, fuenf unabhaengige Falsifikationen der Scope-Gateidentitaet, Lektuere aller fuenf produktiven UNEXPECTED-PATH-Emissionsstellen und maschinelle Tripelableitung samt Abschwaechungsmutation, unabhaengige AST-Zaehlung der Preflight-Denialcodes und Nachverfolgung des einzigen Raisers, sechs Guardmutationen einschliesslich zweier ueber die Vorgabe hinausgehender, Ueberapproximationspruefung ueber alle Zuweisungen mit error_code, Fail-closed-Verhalten bei zerstoertem Ableitungsanker, Bidirektionalitaet 36 zu 36, Manifestabgleich 11 zu 11 gegen gesammelte pytest-Knoten, eigener Verbundlauf 59 passed in 62.64s und stabiler Wiederholungslauf der Matrix 10 passed in 57.22s | groesstes Restrisiko: Manifest, EXPECTED_SCENARIOS, EVIDENCE_ORACLE, GATE_SOURCE_MAP und GATE_CASE_ORACLE sind untereinander gebunden, aber keine dieser Sollmengen wird aus dem Arbeitsplan abgeleitet, sodass ein kuenftig ergaenztes Pflichtszenario nur durch menschliche Disziplin in die Inventur gelangt; nachgeordnet der von Codex einmalig berichtete, von mir nicht reproduzierte Uhrzeit-Fehlschlag provider attempt end cannot precede start aus zwei realen Zeitablesungen der Produktionsbridge sowie der unerreichbare NameError-Pfad in run_scripted_workflow | realistische Bruchbedingung: eine Regelkennung erreicht das Gatedetail in einer dritten Producerform, etwa als Funktionsrueckgabe, Dict-Lookup oder Modulkonstante eines bisher nicht betrachteten Moduls; Literalsuche, BoolOp-Verfolgung und _deny-Ableitung sehen sie nicht, der Guard bleibt gruen, und der neue Gatesachverhalt wird in keinem Szenario behauptet
PRE_MORTEM: In drei Monaten scheitert das Paket am wahrscheinlichsten daran, dass die Producerverfolgung als abgeschlossen gilt, obwohl sie genau zwei Syntaxformen kennt. Eine dritte Form an derselben Emissionsstelle bliebe unentdeckt, und die drei Mutationsproben schluegen nicht an, weil sie exakt die beiden bekannten Formen abbilden — die dritte Wiederholung der Fehlerklasse aus C-04, C-06 und C-19; belastbar waere nur eine Datenflussanalyse statt einer Aufzaehlung erkannter Syntaxformen. Zweitwahrscheinlich wird der Arbeitsplan um eine neue Pflichtkante erweitert, ohne Manifest, EXPECTED_SCENARIOS und EVIDENCE_ORACLE mitzufuehren: alle bestehenden Bindungen blieben gegenseitig konsistent und gruen, der Abschlussbericht wiese weiterhin drei sauber getrennte Dimensionen aus, und der ungemessene Pfad fiele erst im realen Betrieb auf.
FINAL_APPROVAL: YES
STATUS: DONE
```
