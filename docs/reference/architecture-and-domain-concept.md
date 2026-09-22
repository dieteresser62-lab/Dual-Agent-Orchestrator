# Architektur- und Fachkonzept

**Zuletzt verifiziert:** 2026-08-16

## 1. Zweck

Der Dual-Agent Task Orchestrator führt klar begrenzte Entwicklungsaufgaben
lokal, fortsetzbar und auditierbar aus. Codex plant und implementiert, Claude
reviewt unabhängig, und der Orchestrator besitzt Validierung, Zustand,
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
| Codex | Planung, Implementierung, Korrektur und branchweiter Vollständigkeitsbericht | eigene Arbeit freigeben, committen, pushen oder mergen |
| Claude | Read-only Plan-, Slice-, Korrektur- und Finalreview mit Sonnet/`high` | Produktcode ändern, Validierung attestieren oder Gittransaktionen ausführen |
| Orchestrator | Scope, Zustand, Recordkette, Validierung, Attestierung und lokale Commits | Defekte Freigaben erfinden oder externe Gitaktionen ausführen |

## 4. Architekturprinzipien und Invarianten

1. Neue Läufe sind unveränderlich an `structured-v2` gebunden.
2. Native JSON-Ergebnisse werden zuerst gegen das request-spezifische
   Writerschema und danach gegen die lokale Domäne geprüft.
3. Die append-only Recordkette ist technische Autorität. State-v3 ist ein
   symmetrisch geprüfter Betriebsspiegel; Markdown ist nur Ansicht.
4. Codex besitzt keine Selbstfreigabe. Nur Claude kann einen Plan, Slice oder
   Branch fachlich freigeben.
5. Der Orchestrator führt die vollständige Matrix einmal je relevantem
   Fingerprint aus und bindet ihre Attestierung an den Review.
6. Ein lokaler Commit entsteht nur aus exakt geprüftem Scope, Fingerprint,
   Attestierung und Claude-Freigabe.
7. Resume ist fail-closed und wiederholt weder vollständige Providerresultate
   noch abgeschlossene Side Effects.
8. Historische Protokollmodi werden mit `UNSUPPORTED-PROTOCOL` abgewiesen und
   nicht migriert.

## 5. Logische Architektur

```text
Inbox/Task
   |
   v
Task contract -> State-v3 workflow -> Codex native JSON
                         |                 |
                         v                 v
                  canonical diff -> validation attestation
                         |                 |
                         +------> Claude native JSON review
                                           |
                                           v
                               exact local Git commit

Every accepted fact -> append-only structured-v2 records
Records -> checked State mirror + deterministic Markdown projection
```

Wesentliche Module:

- `task_contract` und `plan_handoff` binden Auftrag und Slicegrenzen;
- `workflow` steuert Codex–Claude-Konvergenz und Gates;
- `native_codex_request`/`native_codex_contract` und
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
überführt. Codex erstellt genau das Arbeitsplandokument. Nach interner
Handoffprüfung und Claude-Review commitet der Orchestrator den Plan lokal und
erzeugt den `APPROVED_PLAN_COMMIT`-gebundenen Implementierungs-Handoff.

### 6.2 Implementierung und Korrektur

Codex bearbeitet ausschließlich den aktiven Sliceschutzraum. Der Orchestrator
ermittelt den kanonischen Diff und führt die Validierungsmatrix aus. Claude
prüft den vollständigen Slice-Diff, gemessen ab dem unveränderlichen
Slice-Start.

Claude eröffnet Findings in zwei Klassen. Codex beantwortet jedes offene
Finding genau einmal begründet: Ein `BLOCKER` muss gelöst werden, ein
gewöhnliches `FINDING` darf gelöst oder abgelehnt werden. Eine Annahme ohne
fingerprintändernde Repositoryänderung ist ungültig und wird an der
Vertragsgrenze zurückgewiesen.

Die Eskalation ist kein eigener Zug: Ein gewöhnliches Finding, das Claude in
einem abgelehnten Review nicht schließt, wird durch die kanonische Reduktion
zum `BLOCKER`. Die einzige Commitbedingung lautet, dass die recordabgeleitete
Findingmenge des Slices keinen offenen Blocker enthält.

Jede Prüfrunde nach der ersten ist eine Konvergenzrunde und muss einen
bekannten Befund schließen oder eine attestierte, fingerprintändernde
Behebung nachweisen. Eine stehende Runde oder das konfigurierte Rundenlimit
beendet den Slice negativ ohne Commit.

### 6.3 Abschluss

Der Abnahmereview ist die letzte Arbeitseinheit desselben `IMPLEMENT`-Laufs.
Claude liest den vollständigen Diff ab `git merge-base master <zielbranch>`
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
- Push, Merge, Release und Deployment bleiben außerhalb des Workflows.

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
