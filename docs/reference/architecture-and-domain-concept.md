# Architektur- und Fachkonzept

**Zuletzt verifiziert:** 2026-08-16

## 1. Zweck

Der Dual-Agent Task Orchestrator führt klar begrenzte Entwicklungsaufgaben
lokal, fortsetzbar und auditierbar aus. Der Implementer (standardmäßig Codex)
plant und implementiert, Reviewer und Final-Reviewer (standardmäßig Claude)
prüfen unabhängig. `[roles]` und `[agent_profiles]` in `orchestrator.toml`
bestimmen die Besetzung. Der Orchestrator besitzt Validierung, Zustand,
Attestierung und lokale Committransaktionen.

## 2. Fachliches Problem

LLM-Ausgaben allein liefern weder eine belastbare Scopegrenze noch
idempotentes Resume oder eine reproduzierbare Freigabe. Der Orchestrator
ergänzt deshalb eine deterministische Zustandsmaschine, exakte Pfadallolists,
requestgebundene JSON-Verträge, eine append-only Recordkette und lokale
Validierungsattestierungen.

## 3. Akteure und Verantwortlichkeiten

| Akteur | Verantwortung | Darf nicht |
|---|---|---|
| Benutzer | Aufgabe, optionaler Zielbranch und echte Policyentscheidungen | technische Records oder State manuell erfinden |
| Implementer (TOML-Profil) | Planung, Implementierung und Korrektur | eigene Arbeit freigeben, committen, pushen oder mergen |
| Reviewer und Final-Reviewer (TOML-Profile) | Read-only Plan-, Slice-, Korrektur- und Finalreview | Produktcode ändern, Validierung attestieren oder Gittransaktionen ausführen |
| Orchestrator | Scope, Zustand, Recordkette, Validierung, Attestierung und lokale Commits | Defekte Freigaben erfinden oder externe Gitaktionen ausführen |

## 4. Architekturprinzipien und Invarianten

1. Neue Läufe sind unveränderlich an `structured-v2` gebunden.
2. Native JSON-Ergebnisse werden zuerst gegen das request-spezifische
   Writerschema und danach gegen die lokale Domäne geprüft.
3. Die append-only Recordkette ist technische Autorität. State-v3 ist ein
   symmetrisch geprüfter Betriebsspiegel; Markdown ist nur Ansicht.
4. Der Implementer besitzt keine Selbstfreigabe. Nur der Reviewer kann einen Plan, Slice oder
   Branch fachlich freigeben.
5. Der Orchestrator führt die vollständige Matrix einmal je relevantem
   Fingerprint aus und bindet ihre Attestierung an den Review.
6. Ein lokaler Commit entsteht nur aus exakt geprüftem Scope, Fingerprint,
   Attestierung und Reviewer-Freigabe.
7. Resume ist fail-closed und wiederholt weder vollständige Providerresultate
   noch abgeschlossene Side Effects.
8. Historische Protokollmodi werden mit `UNSUPPORTED-PROTOCOL` abgewiesen und
   nicht migriert.

### 4.1 v3-Review-Writer und lokale Domänenprüfung

Die Plan-, initialen Slice-, Konvergenz-, Finalreview- und Stop-Projektionen
gelten für `claude` und die auf das geprüfte `codex`-Schema abgesenkte Form.
„Ja“ bedeutet: Die Regel ist mit der geprüften Providergrammatik im Writer
ausdrückbar. Bei „Nein“ steht die genaue Anweisung in der `description` des
betroffenen Knotens; die lokale Prüfung bleibt verbindlich. Zeilen nennen die
Prüfstelle beziehungsweise die dazugehörige Projektion.

| Regel | Symbol/Zeile | Im Writer ausdrückbar? | Maßnahme |
|---|---|---|---|
| Dokumentform, Rolle, Textmuster und Längen | `native_review_contract.py:1445, 1484, 2087`, `contracts.py:247, 402` | ja | Reader-Schema und kontextspezifische `const`, Muster und Grenzen bleiben im Writer. |
| Review braucht mindestens ein Finding-Ereignis oder strukturierte Evidenz | `native_review_contract.py:1956`, `schemas/native-agent-review-result-v3.schema.json` | ja | `anyOf` des Review-Resultats verlangt ein nichtleeres Ereignisarray oder Evidenz. |
| Exakte `request_id` der Anfrage | `native_review_contract.py:1493, 1621`, `native_review_request.py:508-570` | nein | `request_id.description`: exakt aus der kanonischen Anfrage übernehmen; lokale Bindungsprüfung. Die ID hängt selbst vom Writer-Digest ab und kann deshalb nicht als Writer-`const` berechnet werden. |
| Gemeinsames Vorkommen von Vorgänger und SHA-256-Anker; Finalreview ohne Vorgänger | `contracts.py:180`, `native_review_contract.py:1339, 859` | ja | Normale Findings verlangen beide Felder und ein `anyOf` für zweimal `null` oder zweimal gültige Strings; Finalreview bindet beide an `null`. |
| Ein Finding kann nicht sein eigener Vorgänger sein | `contracts.py:180` | nein | `predecessor_finding_ref.description` nennt das Selbstreferenzverbot; Domain prüft weiter. |
| Eindeutige kanonische `affected_paths` | `contracts.py:139`, `native_review_contract.py:986` | teilweise | `uniqueItems` bei Claude, lokal kompensiert bei Codex; `affected_paths.description` nennt Eindeutigkeit und kanonische POSIX-Relativpfade. |
| Neue Finding-IDs beginnen bei `next_finding_id`, sind zusammenhängend, geordnet und werden nicht wiederverwendet | `finding_identity.py:11-24`, `native_review_contract.py:1956` | teilweise | Writer bindet zulässige IDs per `enum`; `new_findings.description` nennt Reihenfolge, Kontiguität und Eindeutigkeit. |
| Neue Finding-Signatur ist eindeutig gegenüber bekannten und neuen Findings | `native_review_contract.py:1956, 2222` | nein | `new_findings.description` verbietet Duplikate; lokale Signaturprüfung und Zusammenführung bleiben. |
| Konvergenzrunden eröffnen keine neuen Findings | `native_review_contract.py:986, 2425` | ja | Der Writer bindet `new_findings` auf ein leeres Array. |
| Status referenziert nur angebotene offene eigene oder gerade eröffnete Findings; neue Findings werden nur geschlossen | `native_review_contract.py:1223, 1956` | ja | Kontextgebundene ID-Optionen und `status=CLOSED` für eine Schließung im selben Ergebnis. |
| `status=CLOSED` braucht Closure, `OPEN` verbietet Closure; Rejection braucht Grund und Evidenz | `native_review_contract.py:1710, 1870, 2087` | ja | `status_change.anyOf` bindet Status und Closure; `finding_closure` bindet `fixed`/`rejected` samt Grund und begrenzter Evidenz. |
| Jede Statusänderung ist eindeutig und ihre Begründung unterscheidet sich von der vorigen | `native_review_contract.py:1917, 1956` | nein | `status_changes.description` nennt eindeutige IDs; `rationale.description` verlangt eine neue Begründung. |
| Findings, die freigegeben werden sollen, müssen geschlossen sein; Planfreigabe braucht alle eigenen Dispositionen | `native_review_contract.py:1956, 2425` | nein | `approved.status_changes.description` nennt die vollständige Schließung. Die mengenübergreifende Domainprüfung bleibt. |
| Ablehnung lässt mindestens ein eigenes Finding oder einen Blocker offen | `native_review_contract.py:2425` | teilweise | Ohne bisherigen offenen Befund mindestens ein neues Finding im Writer; `denied.status_changes.description` nennt die Restbedingung. |
| Freigabe verlangt Attestierung, Testfreigabe, Review-Evidenz und Pre-Mortem | `native_review_contract.py:986, 2425` | ja | Writer bietet den Freigabezweig nur bei zulässigem Kontext an und fordert die drei Evidenzfelder und Pre-Mortem. |
| Finalreview-Abschluss verlangt PASS-Attestierung und freigegebene Teständerungen | `native_review_contract.py:859, 2120` | ja | Ohne Voraussetzungen bietet der Writer nur den Stop-Zweig an. |
| Finalreview meldet `scan_complete=true`, höchstens `max_new_findings`; eine volle Kapazität ist nur ein Stopp | `native_review_contract.py:859, 1484, 2120` | teilweise | `const` und `maxItems`; `new_findings.description` erklärt `DISCOVERY_OUTPUT_LIMIT` und dass ein kapazitätsgroßes Ergebnis partiell ist. |
| Vorkommen im Finalreview beziehen sich eindeutig auf angebotene bekannte Findings | `native_review_contract.py:859, 2120` | teilweise | `const`-IDs und `maxItems`; `occurrences.description` verlangt jede ID höchstens einmal. |
| Vorkommen-Anker im Finalreview ist `null`, Text ist begrenzt | `native_review_contract.py:859`, `contracts.py:227` | ja | Der Writer bindet den Anker auf `null` und die Begründung an sichtbaren, begrenzten Text. |
| Anchor-IDs sind eindeutig und Anchors brauchen gebundenen Ursprung | `native_review_contract.py:2395`, `contracts.py:402` | teilweise | Ohne Ursprung leeres Array; `anchor_id.description` nennt Eindeutigkeit. |
| Stop-Grund und Begründung sind begrenzt; `remediation_paths` sind sortiert, eindeutig, kanonisch und außerhalb `.orchestrator` | `native_review_contract.py:1614`, `contracts.py:377` | teilweise | Muster und Längengrenzen im Writer; `remediation_paths.description` nennt aufsteigende UTF-8-Bytereihenfolge, Eindeutigkeit und Pfadgrenze. `StopRequest` lehnt weiter ab. |
| Stop-`rule_id` gehört zu den Laufregeln; `DISCOVERY_OUTPUT_LIMIT` gilt nur im Finalreview | `workflow.py:_halt_for_stop_request`, `workflow_requests.py:native_review_request`, `workflow_recovery.py:_recorded_reviewer_stop_rules, _rebind_reviewer_context_to_request_ledger, _build_pending_native_reviewer_context`, `native_review_contract.py:_bound_stop_result_definition, _native_response_to_contract_result` | ja | Die Regeln aus `WorkflowContext.known_stop_rule_ids` samt Beschreibungen binden Review-Kontext und Anfrage. Reviewer-Requests werden vor dem Providerstart gespeichert; Recovery mit Request-Ledger liest die Regeln aus dem durch die aufgezeichnete `request_id` gebundenen Request-Artefakt. Für ältere Records ohne gespeicherte Regeln gilt der rekonstruierte Laufkontext als Rückfall. Ohne Request-Ledger gilt der gebundene Bundle-Kontext. Alle Stop-Zweige nutzen ein kontextgebundenes `enum`; die Domain lehnt unbekannte IDs mit `STOP_CONTENT_INVALID` ab. Die Workflow-Prüfung bleibt zweite Verteidigung. |
| Maximal 32 Dispositionen, bindbare Status-IDs und Finalreview-Kapazität | `native_review_contract.py:351, 986, 2120` | teilweise | `maxItems` bindet die einfache Obergrenze; kontext- und mengenübergreifende Kapazitätsprüfung bleibt lokal. |

Die Projektion behält `defensive_provider_projection` und
`assert_projected_provider_schema`. `codex` entfernt das kompensierte
`uniqueItems` und senkt `oneOf` auf `anyOf` ab; `description` bleibt erhalten.
Weder Writer noch Domain sortieren, reparieren oder normalisieren Antworten.

## 5. Logische Architektur

```text
Inbox/Task
   |
   v
Task contract -> State-v3 workflow -> Implementer native JSON
                         |                 |
                         v                 v
                  canonical diff -> validation attestation
                         |                 |
                         +------> Reviewer native JSON review
                                           |
                                           v
                               exact local Git commit

Every accepted fact -> append-only structured-v2 records
Records -> checked State mirror + deterministic Markdown projection
```

Wesentliche Module:

- `task_contract` und `plan_handoff` binden Auftrag und Slicegrenzen;
- `workflow` steuert Implementer-Reviewer-Konvergenz und Gates;
- `native_implementer_request`/`native_implementer_contract` und
  `native_review_request`/`native_review_contract` bilden die nativen
  Verträge;
- `artifact_bridge`, `artifact_replay` und `workflow_state` sichern Autorität
  und Resume;
- `validation_matrix` erzeugt die fingerprintgebundene Attestierung;
- `artifact_projection` und `audit_trail` erzeugen Menschenansichten;
- `git_service` führt die exakt autorisierten lokalen Commits aus.

## 6. Vollständiger Workflow

### 6.1 Planung

Ein informeller Auftrag wird in einen eng begrenzten `PLAN_ONLY`-Vertrag
überführt. Der Implementer erstellt genau das Arbeitsplandokument. Nach interner
Handoffprüfung und Reviewer-Review commitet der Orchestrator den Plan lokal und
erzeugt den `APPROVED_PLAN_COMMIT`-gebundenen Implementierungs-Handoff.

### 6.2 Implementierung und Korrektur

Der Implementer bearbeitet ausschließlich den aktiven Sliceschutzraum. Der Orchestrator
ermittelt den kanonischen Diff und führt die Validierungsmatrix aus. Der Reviewer
prüft den vollständigen Slice-Diff, gemessen ab dem unveränderlichen
Slice-Start.

Der Reviewer eröffnet Findings in zwei Klassen. Der Implementer beantwortet jedes offene
Finding genau einmal begründet: Ein `BLOCKER` muss gelöst werden, ein
gewöhnliches `FINDING` darf gelöst oder abgelehnt werden. Eine Annahme ohne
fingerprintändernde Repositoryänderung ist ungültig und wird an der
Vertragsgrenze zurückgewiesen.

Die Eskalation ist kein eigener Zug: Ein gewöhnliches Finding, das der Reviewer in
einem abgelehnten Review nicht schließt, wird durch die kanonische Reduktion
zum `BLOCKER`. Die einzige Commitbedingung lautet, dass die recordabgeleitete
Findingmenge des Slices keinen offenen Blocker enthält.

Jede Prüfrunde nach der ersten ist eine Konvergenzrunde und muss einen
bekannten Befund schließen oder eine attestierte, fingerprintändernde
Behebung nachweisen. Eine stehende Runde oder das konfigurierte Rundenlimit
beendet den Slice negativ ohne Commit.

### 6.3 Abschluss

Der Abnahmereview ist die letzte Arbeitseinheit desselben `IMPLEMENT`-Laufs.
Der Final-Reviewer liest den vollständigen Diff ab `git merge-base <basisbranch> <zielbranch>`
und meldet dort nur neue Findings oder das erneute Auftreten bekannter
Signaturen; er fordert keine Sonderkorrektur an.

Bleibt Restarbeit, entsteht daraus ein gewöhnliches Inbox-Dokument ohne
erzwungene Korrelation zum abgeschlossenen Lauf, und der Prozess beginnt von
vorn. Am konfigurierten Abnahmelimit endet die Aufgabe ohne neues Dokument
und ohne Rücknahme.

## 7. Sicherheits- und Vertrauensgrenzen

- Reviewer arbeiten gegen eine schreibgeschützte selektive Kopie.
- Agenten erhalten keine Autorität über Validierungsattestierungen.
- Providerantworten werden vor fachlicher Anwendung roh und digestgebunden
  gesichert.
- Unbekannte Pfade, Branchabweichungen, Fingerprintdrift und unvollständige
  Attestierungen halten vor Commit an.
- Secrets gehören weder in Aufgaben noch Records oder Auditdokumente.
- Push, Release und Deployment bleiben außerhalb des Workflows. Nach einem
  befundfreien Gesamtreview archiviert der Orchestrator die Laufdokumente in
  einem eigenen Commit und führt den Zielbranch standardmäßig lokal mit
  `--no-ff` in den Basisbranch zusammen (`merge_completed_branch`, abschaltbar).

## 8. Persistierung, Fortsetzung und Idempotenz

Die autoritativen Records liegen unter
`.orchestrator/artifacts/<run-id>/records/`. `state.json` und Checkpoints
spiegeln denselben Lauf; `head.json` ist ein rekonstruierbarer Cache. Resume
prüft Kette, Spiegel, Work Unit, Runde, Fingerprint, Agentprofile und bereits
abgeschlossene Side Effects, bevor ein Provider oder Git aufgerufen wird.

Modell und Effort sind Teil der unveränderlichen Protokollbindung. Ohne
expliziten Override übernimmt Resume die persistierten Profile. Eine
explizite Abweichung stoppt vor Providerstart mit `AGENT-PROFILE-DIFF`.

## 9. Bekannte Grenzen

- Natives Windows ist nicht als Produktionsplattform verifiziert.
- Historische Workflowprotokolle sind absichtlich nicht fortsetzbar.
- Das System ist eine sequenzielle Kontroll- und Evidenzebene, kein
  allgemeiner paralleler Agentengraph.
- Externe Repositoryaktionen und Deployment bleiben Benutzeraufgaben.
- Eine zusätzliche Provider- oder Reviewerrolle erfordert ein neues
  Architekturvorhaben.
