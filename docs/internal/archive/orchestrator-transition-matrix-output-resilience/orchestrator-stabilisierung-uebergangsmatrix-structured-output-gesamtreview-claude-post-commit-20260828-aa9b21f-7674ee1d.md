# Claude-Gesamtreview des committed Branches – Übergangsmatrix und Structured-Output-Resilienz

**Reviewer:** Claude (manuell, adversarial) · **Datum:** 28. August 2026 ·
**Art:** branchweites Finalreview des vollständig committed Pakets

## 1. Ergebnis vorab

Die technische Substanz des Pakets hält der unabhängigen Nachprüfung am
committed Stand vollständig stand. `C-01` bis `C-19` bleiben geschlossen; für
keinen dieser Befunde habe ich eine Regression gefunden. Die Commitgrenze ist
exakt: Der Commit enthält blob-identisch den von mir freigegebenen Snapshot
plus das danach geschriebene Claude-Auditartefakt, und keine unreviewte
Produktivänderung.

Ein neuer Befund verhindert dennoch die Freigabe: Der persistierte
Abschlussnachweis dokumentiert seine providerfreie Validierung mit Zahlen, die
den Zustand **vor** der `C-18`/`C-19`-Korrektur beschreiben, und schreibt sie
ausdrücklich dem abgeschlossenen Slice-3-Stand zu. Beide Zahlen sind am
committed Stand nachweislich falsch.

| ID | Schwere | Kern |
|---|---|---|
| `C-20` | BLOCKER | `resilienznachweis.md` §5 nennt `28 passed` und `1091 passed in 120.17s` „nach Abschluss aller Slice-3-Nacharbeiten". Am committed Stand sind es 29 bzw. 1092; das Paket widerspricht sich damit in seinem eigenen Abschlussartefakt. |

## 2. Selbst erhobener Stand und Fingerprint

Eigenständig erhoben, fail-closed gegen die Auftragswerte geprüft:

| Merkmal | Selbst gemessen | Erwartet | |
|---|---|---|---|
| Branch | `feature/orchestrator-transition-matrix-output-resilience` | identisch | ✓ |
| Review-`HEAD` | `aa9b21f086118a3078a414ce6734c6aa4833c08e` | identisch | ✓ |
| Merge-Base gegen `master` | `de1bd9ea40392c17e47efcc8d243c7de1438ae59` | identisch | ✓ |
| Commits `master..HEAD` | `ff10967`, `13421f6`, `6b609f4`, `aa9b21f` | identisch | ✓ |
| Geänderte Pfade `master...HEAD` | 19 | 19 | ✓ |
| Arbeitsbaum | sauber (`git status --porcelain` leer) | sauber | ✓ |
| Paketfingerprint | `7674ee1d01d1c940b7a421c2d2e8dd904c1dc0a09fe6776dc1c03ac519faab5b` | identisch | ✓ |
| `git diff --check master...HEAD` | ohne Befund | — | ✓ |

Der Fingerprint wurde nach der vorgegebenen Regel selbst berechnet: SHA-256
über die sortierte Folge aus repository-relativem Pfad, NUL-Byte, der Blob-ID
aus `HEAD:<Pfad>` und abschließendem Zeilenumbruch. Kein Drift; Sauberkeit und
`HEAD` wurden unmittelbar vor der Schreiboperation erneut geprüft.

## 3. Reviewgrenze

Kein `run_task`, kein Watcher, kein Bootstrap, kein Provider- oder
Canaryaufruf. Kein Staging, Commit, Merge, Push, Branchwechsel, keine
Historienänderung, keine Berührung von `.orchestrator`. Quellcode, Tests,
Fixtures, Konfiguration und alle bestehenden Dokumentinhalte blieben
unverändert; sämtliche Mutationen liefen als In-Memory-AST- und
Zustandsproben aus dem Scratchpad.

Die einzige Schreiboperation ist die Anlage genau dieses Dokuments unter dem
vorgegebenen Pfad. Es wurde keine zweite Kopie erzeugt und kein bestehendes
Dokument ergänzt.

Die vollständige Suite habe ich weisungsgemäß nicht ausgeführt. Wo ich eine
Gesamtzahl brauche, verwende ich `--collect-only`, also eine reine
Sammlung ohne Testlauf. Die Angaben von Codex (`32 passed`, `59 passed`,
`1092 passed in 121.41s`) sind agent-lokale Fremdangaben; sie sind unten
ausdrücklich nicht als eigene Ausführung geführt. Ich beanspruche keine
Orchestrator-Attestierung.

## 4. Eigene Befehle und Gegenproben

| # | Gegenprobe | Ergebnis |
|---|---|---|
| P1 | Branch, HEAD, Merge-Base, Commitkette, Sauberkeit | wie erwartet |
| P2 | 19 Pfade aus `master...HEAD` und commitgebundener Fingerprint | `7674ee1d…` — Kontrollwert bestätigt |
| P3 | Alle 19 `HEAD:<Pfad>`-Blobs gegen den freigegebenen Snapshot `d4628dd2` | 18 blob-identisch, 1 neu (mein Auditartefakt), keine Abweichung, kein fehlender Pfad |
| P4 | `git diff --check master...HEAD` | ohne Befund |
| P5 | Integrität des eingecheckten Claude-Korrekturreviews | 357 Zeilen, maschinenlesbarer Block unverändert |
| P6 | Paketverbund über sechs Dateien selbst ausgeführt | `59 passed in 67.83s` |
| P7 | Guard-Basislauf `_source_map_errors` | leer |
| P8 | Source-Map-Bidirektionalität | 36 Zeilen zu 36 Fällen, keine Waise |
| P9 | Entdeckte Regelkennungen an Gatedetails | genau 16, keine unverbundenen Literale |
| P10 | Typisierte `_deny`-Codes aus `run_final_review_preflight` | 14, deckungsgleich mit der literalen Sollmenge |
| P11 | AST-Ableitung des Scope-Tripels | `unexpected_file/UNEXPECTED-PATH/user` an allen fünf Emissionsfunktionen |
| P12 | Mutation: BoolOp-Fallback → `ZZZ-ESCAPE` | rot |
| P13 | Mutation: statische Zuweisung → `ZZZ-PLAIN` | rot |
| P14 | Mutation: neuer typisierter `_deny`-Code | rot |
| P15 | Mutation: Fallbackzeile aus der Source-Map entfernt | rot |
| P16 | Mutation: Scope-Zeile auf `policy` umgetypt | rot |
| P17 | Mutation: produktives Scopegate auf `await_policy_gate` abgeschwächt | rot (`unclassified-triplet`) |
| P18 | Mutation: Ableitungsanker `_deny` umbenannt | rot, fail-closed statt stiller Verengung |
| P19 | Manifest gegen gesammelte pytest-Knoten | 11 von 11 deckungsgleich |
| P20 | Exakter Claude-Diagnoseenvelope | `network`, `provider_data` erhalten |
| P21 | Vier Near-Miss-Envelopes | sämtlich `process`, fail-closed |
| P22 | Lokale `maxLength`-Durchsetzung | harte Grenze, exakte Fehlermeldung |
| P23 | 80-Prozent-Prompthinweis | weich, mit ausdrücklichem Auslassungsverbot für alle vier Pflichtinhalte |
| P24 | `ScriptedWorkflowSession` auf Engineverwendung | baut `WorkflowEngine`, ruft `run_current_work_unit` |
| P25 | Drei Korrekturjourneys selbst ausgeführt | 6/8/10 Agentaufrufe, 1/2/2 Commits, je terminal `completed/final_review`, null unverbrauchte Ereignisse |
| P26 | Terminale Ledger der Journeys | `C-01 CLOSED` bzw. `C-01 CLOSED` + `C-02 CLOSED` |
| P27 | Szenariomutation: letzter Review entfernt | `DryRunScenarioError: missing scripted response` — rot |
| P28 | Fokussierter Harnesslauf (Parser + Resilienzszenarien) | **`29 passed`** |
| P29 | Gesamtzahl der Repositorytests via `--collect-only` | **1092 gesammelt** |

## 5. Commitgrenze

Der Prüfpunkt mit dem höchsten Missbrauchspotenzial ist, ob durch den Commit
etwas Unreviewtes hinzugekommen ist. Ich habe das nicht über den Diff, sondern
über Blob-Identität geprüft (P3): Für jeden der 19 Pfade habe ich die Blob-ID
aus `HEAD` gegen die Blob-ID des von mir am 28. August freigegebenen Snapshots
`d4628dd2` gestellt.

Ergebnis: 18 von 19 Blobs sind bit-identisch mit dem freigegebenen Stand; der
19. Pfad ist das nach meiner Freigabe geschriebene Auditartefakt
`…-gesamtreview-claude-korrektur-20260828-d4628dd2.md`. Kein freigegebener
Pfad fehlt, kein Blob weicht ab, und außerhalb dieser 19 Pfade hat der Branch
gegenüber `master` nichts verändert. Insbesondere sind `src/workflow.py`,
`src/final_review_preflight.py` und alle übrigen Produktionsmodule außerhalb
der drei bewusst geänderten Dateien unberührt.

Damit ist ausgeschlossen, dass eine Produktivänderung am Review vorbei in den
Commit gelangt ist. Der Commit trägt die erwartete Nachricht
`test(orchestrator): complete resilience evidence` und keine Attestierungs-
oder Freigabebehauptung.

## 6. Plan- und Slice-Erfüllung

**Slice 1 – Übergangsmatrix und Gateinventur.** Das Kanteninventar
klassifiziert alle 16 geordneten Work-Unit-Paare; die literalen Sollwerte in
`TRANSITION_ORACLE` sind von `PROVIDER_ATTEMPT_CASES` und von der Gateinventur
getrennt gepflegt und werden nicht aus `replay_findings`,
`carry_forward_native_findings` oder `authoritative_native_findings`
berechnet. Die Gateinventur ist mit 36 Zeilen zu 36 ausführbaren Fällen
bidirektional geschlossen (P8) und in beiden Richtungen falsifizierbar
(P12–P18).

**Slice 2 – Structured-Output-Druck.** Die Feldinventur ist an die
request-spezifischen Writerschemas über den schemaformenden Kontextraum
gebunden, mit synthetischen Grenzproben bei 50, 80, 95, 100 und 101 Prozent
gegen echte Schemafragmente. Die Produktivänderung bleibt minimal und
konservativ: harte lokale `maxLength`-Durchsetzung (P22) und ein weicher
Prompthinweis, der das Weglassen von Finding, Disposition, Reviewevidenz und
Pre-Mortem ausdrücklich verbietet (P23). Keine Bindung wurde gelockert.

**Slice 3 – Providerfreier Resilienznachweis.** Elf Szenarien, jedes an einen
tatsächlich gesammelten parametrisierten pytest-Knoten gebunden (P19). Die
drei Korrekturjourneys laufen über die reale `WorkflowEngine` bis in den
terminalen Zustand und behaupten Agentsequenz, Commitanzahl und Ledger
(P24–P26); eine Szenariomutation bricht sie (P27). Der Renderer trennt
Sicherheit, Verfügbarkeit und Autonomie und enthält keine Laufzeit-, Token-
oder Kostenwerte.

Die Definition of Done des Arbeitsplans ist inhaltlich erfüllt — mit der
Einschränkung aus `C-20`: Der Nachweis, dass die vollständige Suite für den
finalen Stand grün ist, existiert (Codex, agent-lokal, 1092), wird im
Abschlussartefakt aber mit den Zahlen des Vorgängerstands dokumentiert.

## 7. Befund C-20 — Der Abschlussnachweis dokumentiert einen überholten Validierungsstand (BLOCKER)

`docs/internal/orchestrator-stabilisierung-providerfreier-resilienznachweis.md`
§5 lautet im committed Stand:

> Der fokussierte Harnesslauf für Parser, Gateidentität und die elf wirklich
> ausgeführten Szenarien bestand nach der Nacharbeit mit `28 passed`. […]
> Die vollständige Repositorymatrix bestand nach Abschluss aller
> Slice-3-Nacharbeiten mit `1091 passed in 120.17s`.

Beide Zahlen gehören zum Stand **vor** der `C-18`/`C-19`-Korrektur. Die
Korrektur hat mit
`test_scope_gate_expectation_requires_production_user_binding` genau einen
Test ergänzt. Selbst gemessen am committed Stand:

| Aussage im Abschlussnachweis | Tatsächlich (selbst gemessen) |
|---|---|
| fokussierter Harnesslauf `28 passed` | `29 passed` (P28) |
| vollständige Matrix `1091 passed` „nach Abschluss aller Slice-3-Nacharbeiten" | 1092 Tests gesammelt (P29); Codex berichtet für diesen Stand `1092 passed` |

Das Paket widerspricht sich damit in sich selbst: Slice-03-Review und
Codex-Addendum führen für denselben Stand korrekt `1092 passed in 121.42s`,
während das Dokument, das die Roadmap als den providerfreien Resilienz- und
Abschlussnachweis ausweist, eine ältere Messung als finale Validierung
präsentiert.

Warum das kein Formalie-Einwand ist: Der Arbeitsplan macht in §12 die
erfolgreiche vollständige Repositorysuite **für den finalen Fingerprint** zur
Abschlussbedingung. §5 dieses Dokuments ist die einzige Stelle, an der das
Paket diese Bedingung für sich behauptet — und die dort zitierte Messung
stammt aus einem anderen Snapshot. Ein späterer Auditor, der die
Abschlussbedingung gegen das Abschlussartefakt prüft, findet dort eine Zahl,
die zum geprüften Commit nicht passt, und muss die Diskrepanz entweder als
Fehler oder als unvollständige Validierung werten. Dies ist dieselbe Kategorie
wie die in `C-16` beanstandete Überbehauptung desselben Dokuments: eine
Zusage, die stärker beziehungsweise aktueller ist als der Beleg. Als
Finalreviewer darf ich das nicht als `OBSERVATION` parken.

Der Befund ist ausführungsgestützt und nicht interpretativ: Die Abweichung
folgt unmittelbar aus einem Testlauf und einer Testsammlung am committed
Stand.

**Akzeptanztest.** §5 nennt eine Messung, die zum committed Fingerprint
gehört: der fokussierte Harnesslauf mit `29 passed` und die vollständige
Matrix mit ihrem tatsächlichen Ergebnis für diesen Stand, ausdrücklich als
agent-lokale Angabe gekennzeichnet. Providerfrei falsifizierbar:
`python3 -m pytest tests/test_dry_run_scenarios.py tests/test_orchestrator_resilience_evidence.py -q -p no:cacheprovider`
muss die im Dokument genannte fokussierte Zahl reproduzieren, und
`python3 -m pytest tests/ --collect-only -q` muss die im Dokument genannte
Gesamtzahl ergeben. Nur Dokumentationstext ist zu ändern; an Code, Tests oder
Fixtures ist nichts anzupassen.

## 8. Disposition C-01 bis C-19

Alle neunzehn bleiben geschlossen. Grundlage ist nicht die Fortschreibung der
Statuszeilen, sondern die Blob-Identität zum bereits geprüften Snapshot (P3)
zuzüglich der unten genannten eigenen Kontrollen am committed Stand.

| Befund | Status | Kontrolle in dieser Runde |
|---|---|---|
| `C-01` Oracle-Unabhängigkeit | CLOSED | `TRANSITION_ORACLE` literal und von der Inventur getrennt; Guardbasislauf leer (P7); Suite grün (P6). |
| `C-02`, `C-05` Drift-/Scope-Trennung | CLOSED | Präfixvergleich ab Zeichenposition null; ein substituiertes `HEAD-DRIFT` am Scopegate wird abgewiesen. |
| `C-03` `PLAN_ONLY`-Abgrenzung | CLOSED | Arbeitsplan blob-identisch, Abgrenzungsabsatz unverändert. |
| `C-04`, `C-06` Guard-Granularität | CLOSED | Durch die `C-19`-Korrektur verschärft; alle Producerformen-Mutationen rot (P12–P14). |
| `C-07` Kantenraum | CLOSED | 16 paarweise verschiedene Zeilen, Suite grün. |
| `C-08`, `C-11` Source-Map-Bindung | CLOSED | 36 zu 36 bidirektional, Waisen- und Umtypungsmutationen rot (P8, P16). |
| `C-09`, `C-10` Evidenzspalten, Mutationsproben | CLOSED | Im eigenen Verbundlauf grün. |
| `C-12`, `C-13`, `C-14` Feldinventur | CLOSED | Slice-2-Dateien blob-identisch; `maxLength` hart (P22). |
| `C-15` Gateart | CLOSED | `_gate_kind` unverändert und deckungsgleich mit der Source-Map. |
| `C-16` Manifestbindung | CLOSED | 11 von 11 gesammelten Knoten deckungsgleich (P19); `EVIDENCE_ORACLE` bleibt unabhängige Sollmenge. |
| `C-17` Korrekturjourneys | CLOSED | Drei Journeys selbst ausgeführt, terminal, mit erhaltenem Ledger (P24–P26). |
| `C-18` Scopeverletzung | CLOSED | Vertiefte Evidenz unten. |
| `C-19` Gateinventur | CLOSED | Vertiefte Evidenz unten. |

### C-18 — vertiefte Evidenz

Die Klassifikation ist nicht mit einem zweiten handgeschriebenen Literal
zirkulär, sondern an den Produktivcode gebunden. Die maschinelle Ableitung aus
dem committed Quelltext liefert das Tripel
`('unexpected_file', 'UNEXPECTED-PATH', 'user')` an **allen fünf**
Emissionsfunktionen — `workflow._commit`, `workflow._invoke_role`,
`workflow._run_review`, `workflow._validate_plan_before_review` und
`workflow.reframe_unexpected_path_stop_gate` (P11). Schwächt man die
produktive Emission in-memory auf ein `await_policy_gate` ohne Fingerprint ab,
meldet der Guard `unclassified-triplet:unexpected_file:UNEXPECTED-PATH:policy`
(P17); typt man die Source-Map-Zeile auf `policy` um, schlagen
`reason-kind-mismatch` und `case-mismatch` an (P16). Fixture, Erwartung,
Manifest und narrativer Nachweis führen übereinstimmend das
fingerprintgebundene Nutzergate, und die Rückstufung auf Policyform oder ein
fehlender Fingerprint werden als Negativkontrollen abgewiesen. Der
sicherheitsrelevante Punkt — dass die fingerprintgebundene Nutzerentscheidung
an dieser Grenze nicht unbemerkt verloren gehen kann — ist damit belegt.

### C-19 — vertiefte Evidenz

Die Producerverfolgung erfasst am committed Stand genau 16 Regelkennungen, die
ein Gatedetail erreichen (P9): `PROVIDER-INPUT-BUDGET`, den BoolOp-Fallback
`FINAL-REVIEW-PREFLIGHT` und die 14 typisierten Preflightcodes. Diese 14 sind
nicht gelistet, sondern aus den `_deny`-Aufrufen von
`run_final_review_preflight` abgeleitet und stimmen mit meiner unabhängigen
AST-Zählung überein (P10); da `FinalReviewPreflightDenied` nur an einer Stelle
erhoben wird und dort unmittelbar dieses Ergebnis trägt, ist die Menge auch
fachlich geschlossen. Alle vier Fehlerrichtungen sind rot: neue Kennung im
BoolOp (P12), neue Kennung in der konstanten Zuweisung (P13), neuer Code
upstream (P14) und eine fehlende Source-Map-Zeile (P15). Der Verlust des
Ableitungsankers verengt die Inventur nicht still, sondern erzeugt Guardfehler
(P18). Eine Überapproximation liegt nicht vor: Die `error_code`-Erweiterung
greift repositoryweit an genau einer Zuweisung, und die entdeckte Menge
enthält keine unverbundenen Literale.

## 9. Korrektheit, Verträge, Fehlerpfade, Sicherheit, Resume

**Verträge.** Die native Writer- und Domänenvalidierung bleibt unangetastet;
`additionalProperties: false`, Requestbindung, Finding-ID-Bindung und
vollständige Dispositionen sind nicht gelockert. Die einzige
Vertragsverschärfung ist die lokale `maxLength`-Durchsetzung, die den
Writerschemas folgt.

**Fehlerpfade.** Die 15 neu inventarisierten Identitäten sind sämtlich
Bootstrap-Resume-Gates aus der Final-Review-Preflight-Ablehnung, also der
Pfad, über den technische und korrekturpflichtige Verweigerungen persistiert
werden. Unbekannte Status-Grund-Kombinationen scheitern fail-closed.

**Sicherheitsgrenzen.** Die drei Drift- und Scopegates bleiben trotz
gemeinsamem `GateReason.UNEXPECTED_FILE` über das vollständige Tupel aus
Status, Grund, verankerter Regelkennung, Gateart, Fingerprint, Pfadsatz und
Resume-Schritt unterscheidbar. Der exakte Claude-Diagnoseenvelope bleibt
transient, vier Near-Miss-Formen bleiben Prozessfehler (P20, P21); verworfene
Modellinhalte werden nicht persistiert.

**Resume und Idempotenz.** Record-Ahead-Resume ohne zweiten Provideraufruf und
die genau einmalige Outbox-Finalisierung sind über die Szenarienbindung
ausgeführt und im eigenen Verbundlauf grün. Die Korrekturjourneys verbrauchen
ihre geskripteten Providerereignisse vollständig (`rest=0`) und committen
genau ein- beziehungsweise zweimal (P25).

## 10. Dokumentkonsistenz

Arbeitsplan, Slice-Reviews, Roadmap, Codex-Gesamtreview und mein
Korrekturreview sind untereinander widerspruchsfrei; Codex erteilt an keiner
Stelle eine Eigenfreigabe und kennzeichnet seine Zahlen als nicht
attestierend. Die Roadmap behandelt den realen Provider-Canary korrekt als
nachgelagerte Betriebsbeobachtung. Die einzige gefundene Inkonsistenz ist
`C-20`.

## 11. Größtes Restrisiko und realistische Bruchbedingung

Größtes Restrisiko bleibt die **nicht abgeleitete Sollmenge**: Manifest,
`EXPECTED_SCENARIOS`, `EVIDENCE_ORACLE`, `GATE_SOURCE_MAP` und
`GATE_CASE_ORACLE` sind untereinander bidirektional gebunden, werden aber
nicht aus dem Arbeitsplan hergeleitet. Ein künftig ergänztes Pflichtszenario
gelangt nur durch menschliche Disziplin in die Inventur.

Nachgeordnete Restrisiken ohne Befundstatus:

- Die narrative Abschlussdokumentation ist nicht maschinell an Manifest,
  Fingerprint oder Testzahlen gebunden. `C-20` ist die erste Realisierung
  genau dieses Risikos und wird sich ohne Bindung wiederholen.
- Codex berichtete einen einmaligen, von mir nicht reproduzierten Fehlschlag
  `provider attempt end cannot precede start`; er entsteht aus zwei realen
  Uhrzeitablesungen der Produktionsbridge im Fixture
  `_append_completed_provider_attempt` und trifft eine korrekte
  Produktionsinvariante. Eine injizierte Uhr an diesen beiden Aufrufen würde
  ihn beseitigen.
- `run_scripted_workflow` referenziert `slice_report` auch dann, wenn weder
  eine Slice-Work-Unit noch eine nichtleere `planned_slices`-Liste vorliegt;
  mit den elf vorhandenen Szenarien ist der Pfad unerreichbar, ein künftiges
  Szenario ohne geplante Slices bräche mit `NameError` statt mit einem
  typisierten `DryRunScenarioError`.
- Das Scope-Szenario deckt eine von drei produktiven
  `resume_step`-Ausprägungen ab.
- `GATE_FOREIGN_PREFIXES` ist die einzige Ausnahme der Tripelrückrichtung;
  ein dort eingetragener echter Gateerzeuger verschwände aus der Prüfung.
- Die Durable-Ledger-Emulation des Testdrivers bleibt permissiver als der
  append-only Produktionsspeicher und hängt an der vorgelagerten nativen
  Vertragsvalidierung, die in den Falsifikationen nachweislich auslöst.

**Realistische Bruchbedingung:** Eine Regelkennung erreicht das Gatedetail in
einer dritten Producerform — als Funktionsrückgabe, Dict-Lookup oder
Modulkonstante eines bisher nicht betrachteten Moduls. Literalsuche,
BoolOp-Verfolgung und `_deny`-Ableitung sähen sie nicht, der Guard bliebe
grün, und der neue Gatesachverhalt käme in keinem Szenario vor.

## 12. Pre-Mortem

In drei Monaten scheitert dieses Paket am wahrscheinlichsten daran, dass der
Abschlussnachweis als Beleg zitiert wird, während sein Wortlaut von der
Wirklichkeit abgekoppelt ist. `C-20` zeigt den Mechanismus bereits im
Auslieferungszustand: Ein Korrekturzyklus ändert Code und Tests, das
narrative Dokument bleibt stehen, und niemand bemerkt es, weil kein Test den
Bericht liest. Beim nächsten Mal betrifft die veraltete Aussage nicht eine
Testzahl, sondern eine Sicherheitszusage — etwa eine Gateart oder einen
Pfadsatz —, und der Bericht behauptet dann eine Eigenschaft, die der Code
nicht mehr hat.

Zweitwahrscheinlich gilt die Producerverfolgung als abgeschlossen, obwohl sie
genau zwei Syntaxformen kennt. Eine dritte Form an derselben Emissionsstelle
bliebe unentdeckt, und die bestehenden Mutationsproben schlügen nicht an, weil
sie exakt die beiden bekannten Formen abbilden — die dritte Wiederholung der
Fehlerklasse aus `C-04`, `C-06` und `C-19`. Belastbar wäre nur eine
Datenflussanalyse statt einer Aufzählung erkannter Syntaxformen.

Drittwahrscheinlich wird der Arbeitsplan um eine neue Pflichtkante erweitert,
ohne Manifest, `EXPECTED_SCENARIOS` und `EVIDENCE_ORACLE` mitzuführen: Alle
bestehenden Bindungen blieben gegenseitig konsistent und grün, und der
ungemessene Pfad fiele erst im realen Betrieb auf.

## 13. Entscheidung

Die Freigabe wird verweigert. `C-20` ist ein präsenter, ausführungsgestützt
belegter Defekt im Abschlussartefakt des Pakets und in einer Finalrunde nicht
als `OBSERVATION` zulässig. Die Korrektur ist eng begrenzt und rein
dokumentseitig; an Code, Tests und Fixtures ist nichts zu ändern. Nach
Aktualisierung von §5 des Abschlussnachweises auf eine zum committed Stand
gehörende, als agent-lokal gekennzeichnete Messung ist der Branch aus meiner
Sicht freigabefähig — alle übrigen Prüfdimensionen sind erfüllt und `C-01` bis
`C-19` bleiben geschlossen.

```text
REVIEWER: claude
NEW_FINDING: C-20 | BLOCKER | Der persistierte Abschlussnachweis docs/internal/orchestrator-stabilisierung-providerfreier-resilienznachweis.md dokumentiert in Abschnitt 5 die providerfreie Validierung mit 28 passed fuer den fokussierten Harnesslauf und mit 1091 passed in 120.17s fuer die vollstaendige Repositorymatrix, letzteres ausdruecklich nach Abschluss aller Slice-3-Nacharbeiten. Beide Zahlen stammen aus dem Stand vor der C-18/C-19-Korrektur, die mit test_scope_gate_expectation_requires_production_user_binding genau einen Test ergaenzt hat. Am committed Stand aa9b21f habe ich selbst gemessen: der fokussierte Lauf ueber tests/test_dry_run_scenarios.py und tests/test_orchestrator_resilience_evidence.py ergibt 29 passed, und die Repositorysammlung ergibt 1092 Tests; Slice-03-Review und Codex-Addendum fuehren fuer denselben Stand korrekt 1092 passed in 121.42s. Das Paket widerspricht sich damit in dem Dokument, das die Roadmap als providerfreien Abschlussnachweis ausweist, und behauptet die Abschlussbedingung des Arbeitsplans, die vollstaendige Suite fuer den finalen Fingerprint, mit einer Messung aus einem anderen Snapshot. | Abschnitt 5 nennt eine zum committed Fingerprint gehoerende, ausdruecklich als agent-lokal gekennzeichnete Messung: den fokussierten Harnesslauf mit 29 passed und die vollstaendige Matrix mit ihrem tatsaechlichen Ergebnis fuer diesen Stand. Providerfreier Falsifikationstest: python3 -m pytest tests/test_dry_run_scenarios.py tests/test_orchestrator_resilience_evidence.py -q -p no:cacheprovider muss die im Dokument genannte fokussierte Zahl reproduzieren, und python3 -m pytest tests/ --collect-only -q muss die genannte Gesamtzahl ergeben. Nur Dokumentationstext ist zu aendern.
REVIEW_EVIDENCE: selbst erhobener Branch-, HEAD-, Merge-Base-, Commitketten- und Sauberkeitsstand, eigenstaendig nachgerechneter commitgebundener 19-Pfad-Fingerprint 7674ee1d, Commitgrenze ueber Blob-Identitaet aller 19 Pfade gegen den freigegebenen Snapshot d4628dd2 mit 18 identischen Blobs und ausschliesslich dem Auditartefakt als Zugang, sauberer Diffcheck, Integritaet des eingecheckten Korrekturreviews, eigener Paketverbundlauf 59 passed in 67.83s, Guard-Basislauf leer und Bidirektionalitaet 36 zu 36, AST-Ableitung des Scope-Tripels an allen fuenf produktiven Emissionsfunktionen, unabhaengige Zaehlung der 14 typisierten Preflightcodes, sieben Guard- und Produktionsmutationen samt Fail-closed-Probe des Ableitungsankers, Manifestabgleich 11 zu 11 gegen gesammelte pytest-Knoten, exakte Retryhuelle und vier Near-Miss-Envelopes, harte lokale maxLength-Grenze, weicher 80-Prozent-Prompthinweis mit Auslassungsverbot, drei selbst ausgefuehrte Korrekturjourneys auf der realen WorkflowEngine mit terminalem Zustand, Commitzahl und Ledger sowie eine rote Szenariomutation, fokussierter Harnesslauf 29 passed und Repositorysammlung 1092 Tests | groesstes Restrisiko: Manifest, EXPECTED_SCENARIOS, EVIDENCE_ORACLE, GATE_SOURCE_MAP und GATE_CASE_ORACLE sind untereinander gebunden, werden aber nicht aus dem Arbeitsplan abgeleitet, und die narrative Abschlussdokumentation ist an nichts davon maschinell gebunden, weshalb C-20 die erste Realisierung genau dieses Risikos ist | realistische Bruchbedingung: eine Regelkennung erreicht das Gatedetail in einer dritten Producerform als Funktionsrueckgabe, Dict-Lookup oder Modulkonstante eines bisher nicht betrachteten Moduls; Literalsuche, BoolOp-Verfolgung und _deny-Ableitung sehen sie nicht, der Guard bleibt gruen, und der neue Gatesachverhalt wird in keinem Szenario behauptet
PRE_MORTEM: In drei Monaten scheitert das Paket am wahrscheinlichsten daran, dass der Abschlussnachweis als Beleg zitiert wird, waehrend sein Wortlaut von der Wirklichkeit abgekoppelt ist. C-20 zeigt den Mechanismus bereits im Auslieferungszustand: ein Korrekturzyklus aendert Code und Tests, das narrative Dokument bleibt stehen, und niemand bemerkt es, weil kein Test den Bericht liest; beim naechsten Mal betrifft die veraltete Aussage keine Testzahl, sondern eine Sicherheitszusage wie eine Gateart oder einen Pfadsatz. Zweitwahrscheinlich gilt die Producerverfolgung als abgeschlossen, obwohl sie genau zwei Syntaxformen kennt, sodass eine dritte Form an derselben Emissionsstelle unentdeckt bliebe und die bestehenden Mutationsproben nicht anschluegen. Drittwahrscheinlich wird der Arbeitsplan um eine neue Pflichtkante erweitert, ohne Manifest und Oracles mitzufuehren, sodass die Suite gruen bleibt und der ungemessene Pfad erst im realen Betrieb auffaellt.
FINAL_APPROVAL: NO
STATUS: DONE
```
