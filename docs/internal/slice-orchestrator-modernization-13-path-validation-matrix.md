# Slice 13: Pfadabhängige Validierungsmatrix und Attestierung

**Status:** Implementierung und Validierung grün; Abschlussfreigaben und Commitstatus folgen in den verwalteten Auditabschnitten
**Feature-Branch:** `feature/orchestrator-modernization`
**Branch-Basis des Slice:** `f53602c`
**GitHub-Status:** nur lokal; kein Upstream; Push und Merge sind nicht Bestandteil dieses Slice
**Arbeitsplan:** [orchestrator-modernization-work-plan.md](orchestrator-modernization-work-plan.md)

## Ziel des Slice

Die additive v3-Engine ermittelt aus dem kanonischen Slice-Diff eine Standard- und Zusatzvalidierungsmatrix, führt sie je Diff-Fingerprint genau einmal aus und bindet eine vollständige, kompakte Attestierung unveränderlich an Claude, Antigravity und den Commit. Ein ausdrücklich angeforderter, separat attestierter Retry ist ausschließlich nach einem `INCOMPLETE`-Versuch desselben Fingerprints zulässig.

## Akzeptanzkriterien

- Ein strukturierter Standardbefehl und beliebig viele POSIX-pfadgebundene Zusatzbefehle sind streng deklarierbar; Shellstrings benötigen ein ausdrücklich benanntes Kompatibilitätsfeld.
- Default-, Pfad- und Finding-Akzeptanzbefehle werden deterministisch ausgewählt und dedupliziert; Rename-Quell- und Zielpfade nehmen am Matching teil.
- Die Ausführung erfasst je ausgeführtem Pflichtbefehl Exitcode, Status und kompakte Ausgabe; die Attestierung bindet Vollständigkeit, Gesamtdigest und kanonischen Diff-Fingerprint.
- Fehlende Binaries erzeugen eine unvollständige Attestierung, Timeouts und rote Exits eine vollständige rote Attestierung.
- Dieselbe Attestierung wird für Claude und Antigravity wiederverwendet. Ein neuer Fingerprint erzeugt genau einen neuen Matrixlauf.
- Reviewer können einen strukturierten Zusatzbefehl ausschließlich im Finding-Akzeptanztest verlangen; er wird in die nächste fingerprintgebundene Matrix aufgenommen und muss zu einer konfigurierten argv-Befehlsfamilie gehören.
- Eine rote Attestierung blockiert außer bei einem ausdrücklich benannten Red-State-/Folgeslice; `INCOMPLETE` blockiert immer.
- Ein gecachtes `INCOMPLETE` bleibt standardmäßig blockierend. Nach nachweislicher Umgebungsreparatur kann der Operator genau diesen Fingerprint ausdrücklich erneut ausführen; beide Versuche bleiben mit eindeutiger ID in der Auditspur.
- Agenten-eigene `VALIDATION_RESULT`-Marker bleiben ungültig. Adapter-/Versions-Smokes bleiben außerhalb der Normalmatrix.
- Der aktive v2-Defaultpfad bleibt unverändert und grün.

## Scope und Nicht-Scope

### Scope

- Zentrale Validierungsbefehle, Pfadregeln, Matrixauswahl und lokaler Runner.
- Striktes TOML-Schema für strukturierte Befehle und explizite Shellkompatibilität.
- Kompakte Ausgabe in `ValidationRecord` sowie stärkere Attestierungsinvarianten.
- v3-Workflowauswahl, fingerprintgenauer Cache, Finding-Zusatzbefehle und Red-State-Regel.
- Fokussierte CLI-, Contract-, Matrix- und Workflowtests sowie Arbeitsplan, Übergabe und diese Slice-MD.

### Nicht-Scope

- Keine Quota-, Timeout- oder Instanzausfallzustände für Agenten aus Slice 14.
- Keine Aktivierung der v3-Engine als Default, kein Watch-Cutover und keine Push-/Mergeautomatik.
- Keine periodischen Reviewer-Smokes; sie bleiben explizite Installations-/Versionsdiagnose.

## Diff-Risiko inklusive Branch- und Statuscheck

**Branch-Check:** `feature/orchestrator-modernization`; entspricht dem Arbeitsplan.

**Status vor Slice-Beginn:** sauber nach lokalem Slice-12-Commit `f53602c`. `Inbox/.lock` ist nicht vorhanden.

**Voraussichtliche Änderungstiefe:** hoch in Matrixausführung und Attestierungsbindung, mittel im Konfigurationsschema, niedrig im unverändert aktiven v2-Pfad.

**Gefährdete bestehende Funktionen:** v3-Reviewcache, Korrekturrunden, Contractvalidierung, Auditprojektion und CLI-Testbefehlsauflösung.

**Nicht anfassen:** `.orchestrator/state.json`, Checkpoints, Quota-/Fehlerzustände aus Slice 14, Watcher, Default-Cutover und Remotes.

**Rollback-Strategie:** additive Matrix- und Context-Erweiterungen per Gegenpatch entfernen; kein Reset, Checkout oder History-Rewrite.

## Geplante Tests

- Default plus `engine/**`-Zusatzbefehl, mehrere Treffer, kein Treffer und Rename-Matching.
- Strukturierte argv-Ausführung und ausdrücklich deklarierter Shell-Kompatibilitätsbefehl.
- Fehlende Binary, Timeout, roter Exit, kompakte Ausgabe und Digest.
- Fremder Fingerprint, falsche Pflichtbefehle und doppelte Attestierungs-ID.
- Claude/Antigravity-Wiederverwendung ohne zweiten Lauf; Korrektur mit neuem Fingerprint führt zu genau einem neuen Lauf.
- Strukturierter Finding-Akzeptanztest in der nächsten Matrix.
- Benannte rote Folgeslice-Ausnahme und ausnahmslos blockierende unvollständige Matrix.
- Vollständige Suite, Compile, aktiver v2-Dry-Run, Diffcheck und Slice-Dokumentvalidator.

## Durchgeführte Änderungen

- `src/validation_matrix.py` definiert unveränderliche argv- beziehungsweise explizite Shellbefehle, Pfadregeln, Matrixrequests und den lokalen Runner. Default, alle passenden Zusatzregeln und offene `VALIDATE: ["…"]`-Finding-Akzeptanztests werden in stabiler Reihenfolge dedupliziert.
- Der Runner verwendet für strukturierte Befehle keinen Shellprozess, begrenzt Laufzeit und Ausgabemenge, führt nach roten Einzelbefehlen weiter und erzeugt aus der vollständigen Rohdiagnose einen Gesamtdigest. Fehlende Binaries hinterlassen bewusst keinen Erfolgs-/Fehlerrecord und machen die Attestierung dadurch `INCOMPLETE`; ein Timeout wird als ausgeführter roter Record mit Exitcode 124 attestiert.
- `ValidationRecord` trägt kompakte Ausgabe und begrenzt deren Größe. `ValidationAttestation` erzwingt die Reihenfolge der Pflichtbefehle und wertet fehlende Pflichtrecords vor roten Records als `INCOMPLETE`.
- `src/cli.py` lädt strukturierte Stringarrays als Standard und akzeptiert Shellstrings nur unter `default_shell_command` beziehungsweise `shell_command`. Timeouts sind positiv und vererbbar; `orchestrator.toml` verwendet das neue argv-Schema. Der v2-Kompatibilitätspfad erhält weiterhin einen sicher gequoteten einzelnen Testcommand.
- Die v3-Engine selektiert die Matrix aus allen aktuellen und historischen Rename-Pfaden. Ein vorhandener Fingerprint wird für beide Reviewer wiederverwendet; neue Pflichtbefehle ohne neuen Diff-Fingerprint stoppen statt eine zweite Matrix desselben Stands zu starten. Nach einer Korrektur bindet der neue Fingerprint genau eine neue Attestierung einschließlich offener Finding-Befehle.
- Finding-Befehle dürfen nur eine aus der konfigurierten strukturierten Matrix abgeleitete Befehlsfamilie erweitern; Shellfamilien erteilen keine dynamische Freigabe. Damit werden insbesondere `/bin/sh -c`, `rm -rf` und ein fremder Interpretermodus bereits bei der Auswahl abgewiesen. Identische Befehle mit widersprüchlichen Timeouts machen die Konfiguration ungültig statt einen Timeout still zu verwerfen.
- Ein explizites `--retry-incomplete-validation` beziehungsweise `WorkflowContext.retry_incomplete_validation` erlaubt ausschließlich die Wiederholung der jüngsten unvollständigen Attestierung. Der Retry erhält eine neue ID und laufende Versuchsnummer; vollständige grüne oder rote Ergebnisse bleiben unverändert gecacht. Commit und Reviews binden stets die jüngste Attestierung des Fingerprints.
- Vollständige rote Attestierungen erreichen Review und Commit nur mit demselben nichtleeren Folgeslice in Workflowcontext, Reviewresultaten, Auditprojektion und Commitautorisierung. Unvollständige Attestierungen sind nie übersteuerbar.
- Die Auditprojektion verlangt für eine Commitautorisierung nun symmetrisch eine freigebende Claude- und Antigravity-Prüfung und verifiziert bei roten Ergebnissen den benannten Folgeslice beider Rollen.
- Reviewerprompts enthalten für jeden Pflichtbefehl Status, Exitcode und Kompaktausgabe sowie den Gesamtdigest. Die Auditprojektion schreibt dieselbe Evidenz tabellarisch; normale Reviewer führen weiterhin keine Matrix oder Harnesses aus.

## Ausgeführte Validierung mit Ergebnis

Nach der ersten Korrekturrunde sind 187 unmittelbar betroffene Matrix-, CLI-, Workflow-, Contract-, Git- und Audit-Tests bestanden. Der erweiterte Fokuslauf besteht mit 226 Tests, die vollständige Suite mit 414 Tests. Compile, aktiver v2-Dry-Run, Diffcheck und Slice-Dokumentvalidator werden auf demselben finalen semantischen Stand ausgeführt und gemeinsam in der fingerprintgebundenen Abschlussattestierung ausgewiesen.

## Abweichungen vom Plan

Gegenüber der voraussichtlichen Liste kamen `src/validation_matrix.py`, `src/audit_trail.py`, `src/git_service.py`, `src/prompts.py` und die zugehörigen Tests hinzu. Die unveränderliche Auswahl-/Runnergrenze rechtfertigt ein eigenes Modul; Audit-, Prompt- und Commitcode müssen die neue Kompaktausgabe und Red-State-Bindung selbst fail-closed verifizieren. `tests/test_gates.py` blieb unverändert, weil die neuen öffentlichen Pfadmatcher durch echte Matrixauswahltests abgedeckt werden. Der produktive Scope liegt mit neun Source-Dateien plus `orchestrator.toml` exakt auf der eigenen Grenze von zehn Änderungseinheiten.

## Offene Risiken

- Plattformquoting wird durch argv-Ausführung vermieden; ausdrücklich konfigurierte Shellkompatibilität bleibt naturgemäß plattformabhängig.
- Der aktive v2-Defaultpfad nutzt bis Slice 18 weiterhin seinen einzelnen Testcommand und nicht die neue v3-Matrix.

## Review-Feedback von Claude

<!-- audit:claude-review:begin -->
Claude (Sonnet/High) prüfte in Runde 1 den vollständigen 20-Pfade-Slice-Diff und verweigerte die Freigabe mit zwei Blockern und zwei Beobachtungen:

- `C-01` (BLOCKER): Finding-`VALIDATE` akzeptierte beliebige argv-Programme und erweiterte damit die Ausführungsgrenze.
- `C-02` (BLOCKER): Eine gecachte unvollständige Attestierung bot nach Umgebungsreparatur keinen unterstützten Retry desselben Fingerprints.
- `C-03` (OBSERVATION): Die Auditprojektion prüfte den Red-State-Folgeslice nur für Antigravity, nicht symmetrisch für Claude.
- `C-04` (OBSERVATION): Die Deduplizierung identischer Befehlsanzeigen konnte widersprüchliche Timeouts still verwerfen.

Runde 2 erhält ausschließlich den Korrekturdelta zu diesen vier Findings und die neue Attestierung.

Claude (Sonnet/High) bestätigte in Runde 2 alle vier Korrekturen und erteilte die Slice-Freigabe `YES`. Das Ergebnis enthielt Verdict, alle Findingstatus, Evidenz und Pre-Mortem bereits eindeutig; zur Contractvalidierung wurden deterministisch nur die gebundene `TEST_FILES_TOUCHED`-Zeile ergänzt und die drei bereits beschrifteten `REVIEW_EVIDENCE`-Teile mit den geforderten Trennern versehen. Es erfolgte kein zweiter Reviewaufruf.

Claude erfasste zusätzlich `C-05` als nicht blockierende Beobachtung: Die Familienprüfung begrenzt Executable und Interpretermodus, nicht jedoch jedes nachgestellte, familienspezifische Argument. Dieser Restpunkt bleibt transparent offen; die operative Vertrauensgrenze ist die vom Operator konfigurierte argv-Familie, Shellfamilien erteilen keine dynamische Freigabe.

Eine kleine Runde 3 band ausschließlich die stabile Abschlussformulierung in Arbeitsplan und Übergabe an Fingerprint `0fb9e637…`; produktiver Code und Tests wurden nicht erneut geprüft. Claude bestätigte erneut `YES`, hielt C-01 bis C-04 geschlossen und C-05 als offene Beobachtung. Auch hier wurden ohne zweiten Aufruf nur die bereits deklarierte Testdateizeile und die `REVIEW_EVIDENCE`-Trenner formal normalisiert.
<!-- audit:claude-review:end -->

## Review-Feedback von Antigravity

<!-- audit:antigravity-review:begin -->
Antigravity (Gemini 3.1 Pro High/High) prüfte den vollständigen finalen 20-Pfade-Diff gegen `f53602c` mit der Attestierung `slice13-validation-0fb9e637` und erteilte beim einzigen Aufruf die Freigabe `YES`. Es wurden keine neuen Findings erfasst.

Geprüft wurden Implementierung, Invarianten, Fehlerpfade, Sicherheitsgrenze, Resume/Idempotenz und Testabdeckung. Antigravity akzeptiert die konfigurierte argv-Familie als operative Vertrauensgrenze und bestätigt C-05 als nicht blockierend. Als Pre-Mortem nennt es einen künftig mit Shellsyntax formulierten argv-Akzeptanztest, der wegen der absichtlich fehlenden Shellauflösung rot attestiert und bis zur präzisen Neuformulierung blockiert.

Zur formalen Contractvalidierung wurde deterministisch die gebundene `TEST_FILES_TOUCHED`-Zeile ergänzt; der nicht autorisierte, inhaltlich unveränderte C-05-Statusmarker wurde entfernt, weil nur der berichtende Reviewer einen Findingstatus schreiben darf. Verdict, Evidenz und Pre-Mortem blieben unverändert; es gab keinen Wiederholungsaufruf.
<!-- audit:antigravity-review:end -->

## Review-Antworten von Codex

<!-- audit:codex-responses:begin -->
- `C-01` angenommen: Dynamische Finding-Befehle werden gegen aus konfigurierten argv-Befehlen abgeleitete Familien geprüft; Shellfamilien autorisieren nichts. Negative Auswahltests decken `/bin/sh -c`, `rm -rf` und `python3 -c` ab.
- `C-02` angenommen: Der normale Cache bleibt exakt einmalig. Nur ein ausdrücklich gesetzter INCOMPLETE-Retry erzeugt einen weiteren, eindeutig nummerierten Auditversuch desselben Fingerprints; ein Resume-Test repariert die simulierte Umgebung und schließt anschließend mit PASS ab.
- `C-03` angenommen: `AuditProjection` verlangt Claude-Freigabe und vergleicht Claudes Red-State-Folgeslice symmetrisch. Ein Negativtest bindet den Mismatch.
- `C-04` angenommen: Dieselbe Befehlsanzeige mit unterschiedlichen Timeouts wird fail-closed als `ValidationMatrixError` abgewiesen; gleiche Duplikate bleiben deterministisch dedupliziert.
- `C-05` als nicht blockierendes Restrisiko dokumentiert: Die konfigurierte argv-Familie ist die Operator-Vertrauensgrenze; Shellsyntax wird weder expandiert noch dynamisch freigegeben.
<!-- audit:codex-responses:end -->

## Validierungsattestierung

<!-- audit:validation-attestation:begin -->
### `slice13-validation-0fb9e637`

- Diff-Fingerprint: `0fb9e63746fec918e1fc8321a78111143124c63c1776dde9630c17f8c5ca69c7`
- Status: `PASS`
- Vollständig: `YES`
- Kurzresultat: 414 passed; 226 focused passed; compile PASS; active v2 dry-run PASS; diff check PASS; slice document PASS
- Ausgabedigest: `ad52bc6ef1829a52b38f69464e6bcb6b0351dab262ec608fbaff9df2a242b065`

| Matrixbefehl | Status | Exitcode |
|---|---|---:|
| `PYTHONDONTWRITEBYTECODE=1 python3 -m pytest tests/ -q -p no:cacheprovider` | PASS | 0 |
| `PYTHONDONTWRITEBYTECODE=1 python3 -m pytest tests/test_validation_matrix.py tests/test_cli.py tests/test_workflow.py tests/test_contracts.py tests/test_prompts.py tests/test_git_service.py tests/test_audit_trail.py tests/test_agent_runtime.py -q -p no:cacheprovider` | PASS | 0 |
| `python3 -m py_compile src/validation_matrix.py src/agent_runtime.py src/audit_trail.py src/cli.py src/contracts.py src/gates.py src/git_service.py src/prompts.py src/workflow.py src/orchestrator.py` | PASS | 0 |
| `./run_task --dry-run --skip-git-check --test-command '' --agent-output none` | PASS | 0 |
| `git diff --check` | PASS | 0 |
| Slice-Dokumentvalidator | PASS | 0 |
<!-- audit:validation-attestation:end -->

## Testfreigabe und Pre-Mortem

<!-- audit:test-approval-premortem:begin -->
Teständerungen sind durch die arbeitsplanweite Revision-8-Ausnahme autorisiert.

Vorgesehene Testpfade: `tests/test_agent_runtime.py`, `tests/test_audit_trail.py`, `tests/test_cli.py`, `tests/test_contracts.py`, `tests/test_git_service.py`, `tests/test_validation_matrix.py`, `tests/test_workflow.py`.
<!-- audit:test-approval-premortem:end -->

## Findings-Lebenszyklus

<!-- audit:findings:begin -->
| ID | Reporter | Klasse | Status | Kurzbegründung |
|---|---|---|---|---|
| C-01 | claude | BLOCKER | CLOSED | Finding-Befehle auf konfigurierte argv-Familien begrenzt |
| C-02 | claude | BLOCKER | CLOSED | expliziter, auditierter Retry ausschließlich für `INCOMPLETE` |
| C-03 | claude | OBSERVATION | CLOSED | symmetrische Claude-/Antigravity-Bindung in Auditprojektion |
| C-04 | claude | OBSERVATION | CLOSED | Timeoutkonflikte fail-closed |
| C-05 | claude | OBSERVATION | OPEN, nicht blockierend | Argumentraum innerhalb einer erlaubten argv-Familie bleibt breiter als reine Testpfade |
<!-- audit:findings:end -->

## Entscheidungstabelle

<!-- audit:decision-table:begin -->
| Finding | Entscheidung | Umsetzung | Verifikation |
|---|---|---|---|
| C-01 | angenommen | konfigurierte Befehlsfamilien als Allowlist | drei schädliche/fremde argv-Varianten werden vor Ausführung abgewiesen |
| C-02 | angenommen | explizite Retry-Politik mit neuer Attestierungs-ID | Resume desselben Fingerprints: erst `INCOMPLETE`, dann PASS; beide Auditereignisse bleiben erhalten |
| C-03 | angenommen | symmetrische Projektion | abweichender Claude-Folgeslice wird abgewiesen |
| C-04 | angenommen | Konfliktprüfung in `ValidationMatrix` | identischer Befehl mit abweichendem Timeout wird abgewiesen |
| C-05 | als Restrisiko dokumentiert | konfigurierte argv-Familie bleibt Operator-Vertrauensgrenze; keine dynamische Shellfreigabe | Claude klassifiziert den Punkt ausdrücklich als Beobachtung, nicht als Blocker |
<!-- audit:decision-table:end -->

## Rückdokumentation in die Arbeitsplan-MD

Der Arbeitsplan verlinkt diese Slice-MD aus §5.1 und der Slice-13-Überschrift. Tatsächlicher Scope, Validierung und Freigaben werden nach der Implementierung ergänzt.

## Freigabestatus

<!-- audit:approval-status:begin -->
- Implementierung bereit: `YES`
- Validierung: `PASS` (`slice13-validation-0fb9e637`)
- Claude-Freigabe: `YES` (finale Dokumentbindung Runde 3; C-01 bis C-04 geschlossen)
- Antigravity-Freigabe: `YES` (vollständiger finaler Diff; erster Versuch)
- Offene Blocker: `NONE`
- Commit autorisiert: `YES`
<!-- audit:approval-status:end -->
