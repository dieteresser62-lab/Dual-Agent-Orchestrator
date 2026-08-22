# Phase 2 – Erkenntnisse aus Arbeitspaket 1 und Stabilisierungspaket 1.1

Dieses Dokument sammelt die während Arbeitspaket 1 erkannten
Orchestratorfälle, die nicht im abgeschlossenen Arbeitsauftrag repariert
werden sollten. Es ist die verbindliche Detailgrundlage für das begrenzte
Stabilisierungspaket 1.1 vor den weiteren nativen JSON-Arbeitspaketen.

Stand: 2026-08-22

## Bearbeitungsstand nach Stabilisierungspaketen 1.1A bis 1.1D

Die Pakete 1.1A, 1.1B und 1.1C sind auf `master` integriert. Release 1.1C wurde
auf `feature/orchestrator-stabilization-1-1c` in zwei bewusst getrennten
Aufträgen umgesetzt: Quota-Wartepolitik und Fake Clock in 1.1C sowie der zuvor
fehlende Providerattempt-/Schemavertrag in 1.1C2. Der geprüfte Branch wurde am
21.08.2026 bis Commit `7ff3fde` per Fast-forward nach `master` übernommen. Alle
drei Implementierungs- und Korrekturslices von 1.1C2 wurden von Claude und
Antigravity genehmigt und commitgebunden abgeschlossen. Die letzte
vollständige Suite bestand mit `993 passed`.

Der branchweite Abschluss von 1.1C2 wurde am 21.08.2026 manuell administrativ
beendet: Codex lieferte seinen Abschlussbericht, Claude genehmigte den
Fingerprint `f865ddcb02a23b0a9730f884c2f47dbf6a7189f80b8fa8bf8f2765839e8f0a2e`,
der abschließende Antigravity-Aufruf erzeugte jedoch wegen eines entfernten
Tool-Schemafehlers (`additional properties 'LineNumber' not allowed`) keinen
Reviewvertrag. Dies wird ausdrücklich nicht als Antigravity-Freigabe
umgedeutet. Der Lauf wird zur Vermeidung weiterer kostenpflichtiger
Wiederholungsreviews nicht fortgesetzt; die Ausnahme und die vollständige
Historie bleiben im archivierten Gesamtreview erhalten.

Release 1.1D setzte anschließend die enge Tool-Schema-Klassifikation, den
begrenzten Retry und die vollständige Providerattempt-Telemetrie um. Slice 01
wurde von Claude und Antigravity genehmigt und an Commit `7a6c27a` gebunden.
Im branchweiten Review erkannte Codex eine zu permissive zwischenzeitliche
Provideroperations-ID; Claude öffnete dazu `C-03` und hielt zusätzlich `C-02`
offen. Die Abschlusskorrektur stellte die fail-closed Inputbindung wieder her
und ergänzte den fehlenden Network-Usage-Roundtrip. Die autoritative Matrix
bestand mit zwei erforderlichen Kommandos, und Claude schloss beide Findings.

Antigravity konnte den Korrekturslice dennoch nicht dispositionieren: Drei
physische Aufrufe mit identischem Input scheiterten in der entfernten Runtime
mit `remote error: run bash: fork/exec /usr/bin/bash: no such file or
directory`. Der Lauf wurde deshalb am 22.08.2026 erneut als transparente
administrative Ausnahme beendet. Dies ist keine Antigravity-Freigabe. Die
lokal vollständig validierte Korrektur, Claudes Freigabe und die drei
fehlgeschlagenen Attempts bleiben im archivierten Review nachvollziehbar.

| Erkenntnis | Sachstatus | Erledigt in | Verifikation | Rest |
|---|---|---|---|---|
| P2-FU-002 | **GELÖST / AUF MASTER** | 1.1A Slice 1, Commit `5638f6b` | Cache-/Replaytests und Finalreview | keiner |
| P2-FU-015, Nachkorrektur | **GELÖST / AUF MASTER** | 1.1A Hotfix, Commit `9f1b6b6` | Scope-/Gate-Regressions und Finalreview | keiner |
| P2-FU-007, Gedankenstrichnachtrag | **GELÖST / AUF MASTER** | 1.1B Slice 1, Commit `3827e81` | gespeicherte Realantwort, lokale Normalisierung und Finalreview | gleichartige einzeilige Labelvariante aus 1.1C als neuer Restfall |
| P2-FU-023 | **GELÖST / AUF MASTER** | 1.1B Slice 1, Commit `3827e81` | deterministische Metadatenergänzung, Fail-closed-Regressions und Finalreview | keiner |
| P2-FU-024 | **GELÖST / AUF MASTER** | 1.1B Slice 2 und Abschlusskorrektur, Commits `fc44f70`, `64b14e2` | kanonische Pakete, Same-Slice-Korrekturscope und Finalreview | branchweite Finalreview-Evidenzkompaktierung bleibt offen |
| P2-FU-003 | **GELÖST / AUF MASTER** | 1.1C2 Slice 1, Commit `c8e1788` | Providerattempt-Projektion mit verständlicher Known-/Unknown-Usage-Semantik | keiner |
| P2-FU-022 | **GELÖST / AUF MASTER** | 1.1C Slice 1, Commit `fac92b0` | Fake-Clock-Tests für Stundenheartbeat und Sieben-Tage-Grenze | keiner |
| P2-FU-013, Betriebsnachtrag | **TEILWEISE GELÖST** | 1.1C2 Slice 1 und 1.1D Slice 1, Commits `c8e1788`, `7a6c27a` | persistenter Providerattempt-Lebenszyklus, enge `LineNumber`-Klassifikation und Attempt-Telemetrie | entfernter `/usr/bin/bash`-Fehler blieb über drei Network-Attempts bestehen; autonome Antigravity-Disposition weiterhin offen |
| P2-FU-025 | **OFFEN** | nach 1.1C | reales Finalreview-Protokoll mit wiederholter identischer Kompaktierung | semantischen Übergangscache und ruhiges Standardlogging umsetzen |
| P2-FU-026 | **GELÖST / AUF MASTER** | 1.1C2 Self-Hosting-Hotfix | gemeinsame Plan-Handoff-/Reviewpaket-Extraktion, `984 passed` | keiner |
| P2-FU-027 | **GELÖST / AUF MASTER** | 1.1C2 Self-Hosting-Hotfix | Attestierungsübernahme und Resume-Rekonstruktion, `985 passed` | keiner |
| P2-FU-028 | **GELÖST / AUF MASTER** | 1.1C2 Self-Hosting-Hotfix | gespeicherte `EVIDENCE:`-Antwort, lokale Normalisierung und Replay, `991 passed` | keiner |
| P2-FU-029 | **GELÖST / AUF MASTER** | 1.1C2 Self-Hosting-Hotfix | Finding-abgeleitete Korrekturpakete, `992 passed` | keiner |
| P2-FU-030 | **GELÖST / AUF MASTER** | 1.1C2 Abschluss-Hotfix | commitgebundene Korrekturpfade im Finalreview-Preflight, `993 passed` | keiner |
| P2-FU-031 | **GELÖST / AUF MASTER** | 1.1C2 Abschluss-Hotfix | fingerprintgebundenes `UNEXPECTED_FILE`-Benutzergate, `993 passed` | keiner |
| P2-FU-032 | **OFFEN** | beobachtet in 1.1D | Plan-Observation bleibt im Planlauf sichtbar | verbindliche Findingidentität über PLAN_ONLY-/IMPLEMENT-Handoff fehlt |
| P2-FU-033 | **OFFEN** | beobachtet in 1.1D | Rohoutput und Vertragsdiagnose bleiben forensisch erhalten | parsebares Finding aus formal verworfenem Review kann beim Fingerprintwechsel aus der verpflichtenden Disposition verschwinden |
| P2-FU-034 | **TEILWEISE GELÖST** | 1.1D Self-Hosting-Hotfix | eindeutig gebundene einzelne `VALIDATE`-Zeile wird lokal fail-closed normalisiert | strukturierte Akzeptanztestrevision bei Finding-Reklassifizierung fehlt |
| P2-FU-035 | **OFFEN** | beobachtet und als permissiver Hotfix in 1.1D zurückgewiesen | unveränderter Input bleibt innerhalb derselben Operation fail-closed gebunden | neue Reviewrevision und technischer Retry besitzen noch keine getrennte, explizite Identität |
| P2-DEC-001 | **TEILWEISE GELÖST** | 1.1A bis 1.1C | Replay-/Projektionsautorität, Reviewpakete, Quota-Wartepolitik und Providerattempt-Telemetrie umgesetzt | record-first Liveübergänge, Betriebslogging, Finalreview-Deduplizierung und Pfaddigest-Freigaben neu zuschneiden |

Die Statusangaben in diesem Dokument beschreiben den Sachstand. Ein gelöster
Punkt gilt erst nach seinem Merge zusätzlich als **auf master integriert**.
Beobachtung, Ursache und frühere Diagnose bleiben auch nach einer Lösung als
historische Evidenz erhalten.

## Aktuelle Priorisierung nach Abschluss von 1.1D

Diese Rangfolge ersetzt für die weitere Planung die bei der Entdeckung der
einzelnen Fälle vergebenen Prioritätsangaben. Jene Angaben bleiben in den
Detailabschnitten als historische Bewertung erhalten. Maßgeblich für neue
Arbeitsaufträge ist ausschließlich die folgende Liste.

Der fachlich validierte Stand von 1.1D wird zunächst als administrative
Ausnahme abgeschlossen, archiviert und auf den Feature-Branch gebunden. Erst
nach seinem ausdrücklichen Merge gilt er als auf `master` integriert.

| Rang | Erkenntnis | Nächstes überprüfbares Ergebnis | Begründung der Reihenfolge |
|---|---|---|---|
| **P0.1** | P2-FU-033 | Formal verworfene, aber eindeutig parsebare Reviewer-Findings content-addressiert quarantänisieren und vor jeder späteren Freigabe durch denselben Reviewer disponieren lassen. | In 1.1D verschwand ein konkreter Claude-Blocker nach einem sachfremden Syntaxfehler und Fingerprintwechsel aus dem verpflichtenden Ledger. Das ist ein correctness- und sicherheitsrelevanter Verlust von Reviewevidenz. |
| **P0.2** | P2-FU-035 | Eine explizite, fingerprintgebundene Reviewrevision von technischen Retries trennen: innerhalb einer Revision bleibt der Input unveränderlich, eine neue autorisierte Revision erhält eine neue Operationsidentität. | Die zu grobe Identität blockierte einen legitimen neuen Finalreview; der erste Hotfix umging dagegen das Attemptlimit. Beide Extreme sind bereits real reproduziert. |
| **P0.3** | P2-FU-021 | Eine gemeinsame, idempotente Abschlussoperation für Direkt- und Watchmodus bereitstellen, die Erfolg oder administrative Ausnahme eindeutig meldet und Task sowie Sidecars genau einmal verschiebt. | Auch 1.1D musste trotz validierter Korrektur manuell archiviert werden. Ein fachlich abgeschlossener oder transparent ausgenommener Lauf darf nicht als erneut ausführbarer Inbox-Task zurückbleiben. |
| **P1.1** | P2-FU-032 | Offene Plan-Observations unveränderlich an Plancommit und Implementierungshandoff binden und im neuen Lauf idempotent importieren. | Hinweise dürfen beim absichtlich getrennten PLAN_ONLY-/IMPLEMENT-Lauf nicht ihre Findingidentität und spätere Dispositionspflicht verlieren. |
| **P1.2** | P2-FU-034 | Reklassifizierungen um eine strukturierte, versionierte Akzeptanztestrevision erweitern; die enge lokale `VALIDATE`-Normalisierung bleibt nur Kompatibilität. | Der konkrete Lauf ist wiederholbar repariert, der native Vertrag kann einen beim Eskalieren neu erforderlichen Validierungsbefehl aber weiterhin nicht ausdrücken. |
| **P1.3** | P2-FU-013 | Für wiederholt fehlende entfernte Antigravity-Runtimes nach ausgeschöpftem Retry eine kostenbegrenzte Betriebsentscheidung vorsehen, ohne Reviewresultat zu erfinden. | 1.1D klassifiziert und misst die Fehler korrekt; drei identische `/usr/bin/bash`-Fehler blockierten dennoch erneut die autonome Reviewer-Disposition. |
| **P1.4** | P2-FU-018 | Bereits genehmigte externe Pfade über einen kanonischen pfadspezifischen Diffdigest sicher wiederverwenden; jede Inhaltsänderung verlangt weiterhin ein neues Gate. | Wiederholte sachgleiche Benutzergates verursachten zahlreiche manuelle Unterbrechungen. Die Korrektur ist sicherheitsrelevant und muss deshalb als eigenständiger, adversarial getesteter Slice erfolgen. |
| **P1.5** | P2-FU-025 | Finalreview-Evidenz pro semantischem Fingerprint genau einmal materialisieren und rollenübergreifend wiederverwenden; Cachetreffer nur unter `--verbose` protokollieren. | Die wiederholte Kompaktierung erzeugt vermeidbare Laufzeit und unbrauchbares Standardlogging. |
| **P1.6** | P2-FU-020 | Codex-Defektkandidaten in denselben verpflichtenden Dispositionskanal wie quarantänisierte Reviewerbefunde überführen oder die adversariale Codex-Abschlussanalyse entfernen. | Erkannte Defekte dürfen nicht in unverbindlicher Prosa verloren gehen; P2-FU-033 liefert dafür nun einen konkreteren Persistenzfall. |
| **P2** | Restumfang von P2-DEC-001 | Die noch nicht record-first arbeitenden Liveübergänge einzeln inventarisieren und anschließend in kleinen, crashgetesteten Paketen auf Recordautorität und deterministische Projektion umstellen. | Das bleibt das wichtigste Architekturziel, ist aber breiter als die unmittelbar beobachteten Betriebsdefekte. Es soll erst nach den P0-/P1-Lücken und nicht als neuer Großumbau umgesetzt werden. |

### Schnitt für die nächsten Arbeitsaufträge

Die Punkte werden nicht zu einem einzigen weiteren Stabilisierungspaket
zusammengezogen. Empfohlen ist folgende Reihenfolge eigenständig reviewbarer
Aufträge:

1. P2-FU-033 allein: quarantänisierte Reviewer-Findingkandidaten und
   fingerprintübergreifende Dispositionspflicht.
2. P2-FU-035 allein: explizite Reviewrevision und unveränderlicher Retryinput.
3. P2-FU-021 allein: gemeinsame terminale Finalisierung von Direkt- und
   Watchmodus einschließlich administrativer Ausnahme und Crash-/Resume-Tests.
4. P2-FU-032 und P2-FU-034 als höchstens zwei kleine Handoff-/Findingvertrags-
   Slices; nicht gemeinsam mit neuer Fachfunktion.
5. P2-FU-013 als enger Betriebsentscheidungsauftrag; keine weitere breite
   Retryklasse und keine erfundene Freigabe.
6. P2-FU-018 als eigener sicherheitsrelevanter Pfaddigest-Auftrag.
7. P2-FU-025 sowie der Restfall zu P2-FU-007 als Reviewkostenpaket.
8. P2-FU-020 erst nach expliziter Entscheidung, ob Reviewer- und
   Codex-Kandidaten denselben Dispositionskanal verwenden.
9. Danach P2-DEC-001 neu inventarisieren und nur den nächsten geschlossenen
   Record-first-Übergang beauftragen.

Alle als gelöst ausgewiesenen Punkte sind aus dem aktiven Rückstand entfernt.
Die Punkte P2-FU-003, P2-FU-022 und P2-FU-026 bis P2-FU-031 sind auf `master`
integriert. Sie dürfen nicht ohne einen neuen reproduzierbaren Befund erneut
zum Implementierungsscope werden. Die 1.1D-Änderungen gelten bis zu ihrem
ausdrücklichen Merge nur als auf dem Feature-Branch gelöst.

## P2-FU-001 – Vollständiger Antigravity-Reviewvertrag trotz Non-Success verworfen

**Status:** im Hotfix umgesetzt; 813/813 Tests bestanden  
**Priorität:** hoch  
**Beobachtet in:**
`watch-20260819-100335.225490Z-44aba5d6579a`, Arbeitseinheit 1,
`antigravity_plan_review`

### Beobachtung

Antigravity lieferte in `provider_text` einen fachlich und formal vollständig
erscheinenden Reviewvertrag:

```text
REVIEWER: antigravity
REVIEW_EVIDENCE: ... | ... | ...
PRE_MORTEM: ...
PLAN_APPROVAL: YES
STATUS: DONE
```

Der Adapter erhielt jedoch einen JSON-Umschlag, dessen `status` nicht exakt
`SUCCESS` war. `AntigravityAdapter.extract_output()` warf deshalb einen
`AgentOutputError`. Die Fehlerklassifikation wurde mangels spezifischer
Metadaten zu `runtime`; der Workflow persistierte `awaiting_resume` und
verwarf den positiven Reviewentscheid.

Der bestehende Recovery-Pfad
`_recover_completed_reviewer_contract()` akzeptiert ausschließlich
`AgentFailureKind.AUTH`. Ein vollständiger rollenrichtiger Vertrag aus einem
`runtime`-Fehler wird daher nicht dem normalen Reviewvalidator übergeben.

### Auswirkung

- unnötiger manueller Resume;
- erneuter Antigravity-Aufruf trotz bereits vorhandener vollständiger Antwort;
- zusätzlicher Zeit- und Quota-Verbrauch;
- der positive Reviewentscheid ist nur noch als Fehlertext, nicht als
  Reviewereignis persistiert.

### Gewünschtes Verhalten

Eine Provider-Non-Success-Hülle darf nicht allein wegen vollständig wirkender
Textmarker in einen Erfolg umgedeutet werden. Das würde die C-22-Schutzgrenze
umgehen: Quota-, Netzwerk-, Runtime- oder fremde Fehlertexte können zufällig
einen formal vollständigen Vertrag enthalten, ohne einen vertrauenswürdigen
terminalen Providererfolg zu belegen.

Stattdessen muss der Adapter den tatsächlichen Prozess-Exitcode sowie
sanitisierte strukturierte Status-, Fehler- und Usage-Felder des Umschlags
erhalten. Die Klassifikation darf nicht aus der Prosa der Modellantwort
abgeleitet werden. Explizit transiente Netzwerk-/Overloadfehler erhalten einen
kleinen, persistierten und fingerprintgleichen automatischen Retry mit
begrenztem Backoff. Auth-, Quota-, Permission-, Runtime- und unbekannte Fehler
bleiben nach ihren jeweiligen Regeln fail-closed.

Eine bereits enthaltene Antwort darf nur dann übernommen werden, wenn der
Providervertrag selbst einen dokumentierten terminalen Erfolgsstatus oder
einen ausdrücklich als nachgelagert und antworterhaltend definierten
Warnstatus ausweist. Eine bloße Prüfung von `REVIEWER` bis `STATUS: DONE` reicht
nicht. Unvollständige, rollenfremde oder widersprüchliche Antworten bleiben
fail-closed.

### Erforderliche Regressionstests

1. Prozess-Exitcode und sanitisiertes strukturiertes Providerstatusfeld bleiben
   bei Adapterfehlern in der Diagnose erhalten.
2. Ein echter Quota-, Auth-, Netzwerk- oder Prozessfehler darf durch enthaltene
   Markerprosa nicht fälschlich zum Review-Erfolg werden.
3. Ein explizit transient klassifizierter Netzwerk-/Overloadfehler wird nur bis
   zur konfigurierten Grenze automatisch und fingerprintgleich wiederholt.
4. Ein unbekannter `runtime`-Status bleibt `awaiting_resume` und löst keine
   unbegrenzte Wiederholung aus.
5. Ein dokumentierter terminaler Erfolgsstatus mit gültigem Plan-, Slice- oder
   Finalreview wird genau einmal validiert und persistiert.
6. Ein gegebenenfalls später unterstützter nachgelagerter Warnstatus wird nur
   anhand einer expliziten Status-Allowlist, nicht anhand von Antwortprosa,
   übernommen.
7. Unvollständige, rollenfremde oder widersprüchliche Verträge bleiben
   fail-closed.
8. Resume nach bereits persistiertem Reviewergebnis erzeugt weder einen zweiten
   Reviewrecord noch einen weiteren Provideraufruf.

### Diagnostische Evidenz

- `.orchestrator/logs/work-unit-0001-antigravity_plan_review.attempt-1.failure.json`
- Erster Versuch: Fehlerart `runtime`, 1.289 Zeichen, 9 Zeilen.
- Zweiter Versuch nach `--resume`: Fehlerart `network`, 4.745 Zeichen,
  33 Zeilen.
- Prozess-Exitcode: `null`
- Workflowzustand: `awaiting_resume`
- Beide Antworten enthielten `PLAN_APPROVAL: YES` und `STATUS: DONE`.
- Keine offenen Antigravity-Findings.

### Umsetzung vom 2026-08-19

- Modellantwort und technische Providerdiagnose werden getrennt behandelt;
  Antwortprosa kann die Fehlerklasse nicht mehr bestimmen.
- Adapterfehler bewahren den realen Prozess-Exitcode und ausschließlich
  sanitisierte technische Umschlagfelder.
- Netzwerk-/Overloadfehler erhalten höchstens zwei automatische Wiederholungen
  desselben Schritts mit 5 beziehungsweise 10 Sekunden Wartezeit.
- Auth-, Runtime-, Output-, Permission- und Prozessfehler bleiben manuell
  resumierbar und werden nicht automatisch wiederholt.
- Structured-v1 speichert Netzwerk-Retries als eigenen
  `transient_retry`-Record; Quota-Pausen bleiben davon getrennt.
- Mirror und Recordkette werden beim Resume symmetrisch und fail-closed
  abgeglichen.

## P2-FU-004 – Antigravity-Planreview wird unnötig ausführlich und verwechselt Plan mit Implementierung

**Status:** im Hotfix umgesetzt; 813/813 Tests bestanden  
**Priorität:** mittel

### Beobachtung

Der zweite Antigravity-Planreview umfasste 4.745 Zeichen und 33 Zeilen,
gegenüber 1.289 Zeichen und 9 Zeilen beim ersten Versuch desselben logischen
Reviews. Neben den erforderlichen Vertragsmarkern erzeugte Antigravity einen
mehrteiligen Markdown-Bericht.

Der Bericht formulierte geplante neue Dateien und Typen wie
`src/provider_input_budget.py`, `PreparedProviderRequest` und
`src/final_review_preflight.py` teilweise so, als seien sie bereits vorhanden
und ihre Implementierung sei verifiziert worden. In einem Planreview können
diese Dateien noch nicht existieren; geprüft werden darf nur, ob der Plan ihre
Einführung ausreichend und prüfbar beschreibt.

### Auswirkung

- unnötiger Output- und Quota-Verbrauch;
- schlechtere Trennschärfe zwischen Planprüfung und Implementierungsprüfung;
- positiv klingende Aussagen können eine tatsächlich noch nicht vorhandene
  Implementierung suggerieren;
- längere Fehlertexte vergrößern State, Audit und spätere Reviewpakete.

### Gewünschtes Verhalten

Antigravity erhält wie Claude eine harte, rollen- und operationsspezifische
Antwortgrenze. Ein Planreview soll ausschließlich strukturierte Findings oder
ein kompaktes `REVIEW_EVIDENCE`, `PRE_MORTEM`, die Planentscheidung und
`STATUS: DONE` zurückgeben. Planbestandteile werden als Vorschlag bewertet,
nicht als vorhandener Code bestätigt.

Zusätzliche freie Überschriften und Wiederholungen der Aufgabenbeschreibung
sollen vor Persistierung entweder durch ein natives Antwortschema verhindert
oder durch einen strikt validierten kompakten Vertrag ersetzt werden. Dabei
darf keine fachliche Evidenz abgeschnitten werden.

### Erforderliche Regressionstests

1. Planreview-Prompt unterscheidet ausdrücklich zwischen geplantem und bereits
   vorhandenem Code.
2. Antigravity-Antwort überschreitet die konfigurierte Outputgrenze nicht.
3. Eine positive Planfreigabe darf keine Implementierungsverifikation für noch
   nicht existente Pfade behaupten.
4. Freie Prosa außerhalb des erlaubten strukturierten Antwortmodells wird
   kontrolliert abgelehnt oder verlustfrei in die vorgesehenen Felder
   normalisiert.
5. Outputzeichen, Outputbytes und Provider-Usage werden kompakt protokolliert.

### Umsetzung vom 2026-08-19

Der Antigravity-Aufruf verwendet nun ein natives JSON-Schema mit genau einem
begrenzten `response`-Feld (maximal 12.000 Zeichen). Die Direktive untersagt
freie Überschriften, wiederholte Analyse und die Darstellung geplanter
Änderungen als bereits implementiert. Die normale strikte Reviewvalidierung
bleibt anschließend unverändert maßgeblich.

## P2-FU-002 – Normale Record-Anhänge erzeugen irreführende Cache-Warnungen

**Status:** durch Stabilisierungspaket 1.1A gelöst; auf Feature-Branch final genehmigt

**Priorität:** niedrig

### Beobachtung

Nach regulären strukturierten Record-Anhängen erscheint wiederholt:

```text
Discarding stale artifact head cache for run <run-id>
```

Die Recorddateien sind laut `ArtifactStore` autoritativ und `head.json` ist nur
ein rekonstruierbarer Beschleunigungscache. Beim normalen `put()` wird zuerst
der neue Record veröffentlicht und anschließend die Kette erneut geladen.
Dabei ist der bisherige Cache erwartungsgemäß noch auf dem vorherigen Head und
wird als „stale“ gemeldet.

### Auswirkung

- technisch harmloser Normalfall wirkt wie Datenkorruption;
- unnötige Verunsicherung bei langen Watch-Läufen;
- echte unerwartete Cacheabweichungen sind schwerer von normalen Anhängen zu
  unterscheiden.

### Gewünschtes Verhalten

Ein erwarteter Cachewechsel nach einem eigenen erfolgreichen Append sollte
ohne Warnung beziehungsweise höchstens auf Debug-Level aktualisiert werden.
Eine Warnung bleibt externen, unerwarteten oder inkonsistenten Cacheänderungen
vorbehalten. Die Recordkette bleibt in jedem Fall die Source of Truth.

### Erforderliche Regressionstests

1. Zwei normale sequenzielle `put()`-Aufrufe erzeugen keine Warnung.
2. Ein tatsächlich fremder oder inkonsistenter `head.json` wird weiterhin
   erkannt, aus der Recordkette repariert und angemessen protokolliert.
3. Cacheverlust verändert weder Recordreihenfolge noch Idempotenz.

### Abschluss in Stabilisierungspaket 1.1A

Slice 1 führte den reinen Record-Replaykern ein und korrigierte die
Cacheautorität. `ArtifactStore.put()` übergibt den erwarteten eigenen
Cachefortschritt nun explizit; ein normaler Append erzeugt keine
Stale-Cache-Warnung. Cacheverlust wird aus der Recordkette rekonstruiert,
während fremde oder manipulierte Abweichungen weiterhin sichtbar und
fail-closed behandelt werden. Der Abschluss ist in Commit `5638f6b` enthalten
und durch die vollständige `934 passed`-Attestierung sowie beide Finalreviews
verifiziert.

## P2-FU-003 – Claude-Nutzungszeile bildet file-backed Reviewinput nicht verständlich ab

**Status:** durch Stabilisierungspaket 1.1C2 gelöst und auf `master` integriert
**Priorität:** mittel

### Beobachtung

Der Planreview meldete:

```text
[AGENT_USAGE] role=claude duration=101.99s turns=6 cost_usd=0.2978 input_tokens=6 output_tokens=9190
```

`input_tokens=6` beschreibt offensichtlich nicht den gesamten file-backed
Reviewkontext mit Manifest und mehreren Read-Aufrufen. Dadurch lässt sich der
tatsächliche Quota- und Kontextverbrauch aus der kompakten Logzeile kaum
bewerten.

### Gewünschtes Verhalten

Die Telemetrie soll, soweit der Provider sie liefert, Initialinput,
Tool-/Read-Kontext, Cache-Read/-Write, Thinking und Output getrennt darstellen.
Unbekannte Werte werden als unbekannt gekennzeichnet und nicht als scheinbar
vollständige kleine Eingabemenge ausgegeben. Zusätzlich sollen die bereits
lokal bekannten Paketzeichen, Paketbytes und Chunkzahlen protokolliert werden.

### Erforderliche Regressionstests

1. Kompakte Usage-Ausgabe unterscheidet Providerwerte von lokal gemessenen
   Paketgrößen.
2. Fehlende Providerfelder werden nicht als Null oder vollständiger Verbrauch
   missverständlich dargestellt.
3. Geheimnisse und Promptinhalte erscheinen nicht in der Telemetrie.

### Abschluss in Stabilisierungspaket 1.1C2

Der persistente `provider_attempt`-Lebenszyklus und seine read-only Projektion
weisen lokal bekannte Inputzeichen, Inputbytes, Komponentenanzahl und größte
Komponente getrennt von den tatsächlich verfügbaren Provider-Usagefeldern
aus. Fehlende Werte bleiben ausdrücklich unbekannt; Teilwerte wie
`input_tokens=6` werden nicht mehr als vollständiger Gesamtverbrauch
missverständlich dargestellt. Slice 1 ist in Commit `c8e1788` enthalten und
durch die abschließende Vollsuite mit `993 passed` verifiziert.

## P2-PLAN-001 – Noch im Arbeitsplan zu verifizierende Scopegrenzen

**Status:** historischer Planprüfpunkt; keine aktive Umsetzungseinheit

Die folgenden Grenzen wurden während der abgeschlossenen Pakete als
Reviewleitplanken verwendet. Neue Arbeiten werden ausschließlich aus der
aktuellen Priorisierung oben abgeleitet; dieser Abschnitt begründet keinen
eigenständigen Folgeauftrag.

- Slice 1 verlangt `AgentFailureKind.PREFLIGHT`, während der Enum derzeit in
  `src/workflow_state.py` liegt und diese Datei erst im exakten Pfad von Slice 2
  erscheint. Die Implementierung darf weder den Slice-1-Scope verletzen noch
  vorübergehend eine sachlich falsche Fehlerklasse verwenden.
- Die produktive Dateigrenze muss pro genehmigtem Slice beziehungsweise
  Korrektur-Work-Unit geprüft werden, nicht gegen die Summe des gesamten
  Feature-Branch-Diffs.
- Für `legacy-state-v3` ist ausdrücklich abzugrenzen, welche Preflightprüfungen
  ohne strukturierte Recordkette möglich sind.
- Initiale `max_chars`-/`max_bytes`-Sicherheitsbudgets benötigen eine
  dokumentierte Repositorypolicy; technische Providerlimits dürfen weiterhin
  unbekannt (`null`) bleiben.

Diese Punkte dürfen nicht durch Änderungen außerhalb des vom Orchestrator
freigegebenen Slice-Scope „nebenbei“ gelöst werden.

## P2-FU-005 – Validierungsrecord wird vor seinem State-v3-Spiegel veröffentlicht

**Status:** behoben mit Commit `c42eca3`; Neustart des betroffenen Laufs erforderlich  
**Priorität:** kritisch  
**Beobachtet in:**
`watch-20260819-135646.145511Z-831128d36569`, Arbeitseinheit 2,
Übergang `codex_implementation -> claude_slice_review`

### Beobachtung

Nach erfolgreicher autoritativer Matrix wurde der strukturierte
`validation_attestation`-Record
`ar1-342b7a59b6e64f4732fdd90ffaa449567027ca044ee5861d5a0cdfe9ae86ab91`
für Fingerprint
`7e9b422e4b9ad7e6362a229b48e0d68ebdc9e1d56da46e139f27891647657f4c`
veröffentlicht. Der State-v3-Spiegel enthielt im selben Zeitpunkt jedoch noch
`runtime_history.current.attestations: []`.

`_attestation()` persistiert zuerst den Record und gibt erst danach ein neues
`WorkflowHistory` mit der Attestierung zurück. Vor dem nächsten Checkpoint
startet `_run_review()` bereits den Reviewer. Dessen
`assert_structured_decision_context()` erkennt den Record-voraus-Zustand und
stoppt korrekt fail-closed mit `validation attestations differ from state-v3`.

### Auswirkung

- die erfolgreiche Validierung kann nicht an Claude übergeben werden;
- ein normales `--resume` scheitert bereits im Resume-Reader;
- der Watcher pausiert korrekt und erzeugt kein Poison, kann den Zustand aber
  ohne Reparatur nicht selbst heilen;
- die implementierten Slice-Dateien bleiben vorhanden, sind jedoch noch nicht
  review- oder commitfähig.

### Erforderliche Korrektur

Nach jeder neu erzeugten Attestierung muss das aktualisierte History-/State-
Mirror vor dem nächsten externen Agenten-, Commit- oder Finalisierungsschritt
checkpointed werden. Plan-, Slice- und Finalreviewpfade müssen dieselbe
Reihenfolge verwenden. Ein Persistenzfehler auf einer Seite bleibt
fail-closed; vorhandene einseitige Records dürfen nicht still gelöscht oder
ignoriert werden.

### Erforderliche Regressionstests

1. Codex-Implementierung, Matrix-PASS, Claude-Start: Record und Mirror sind vor
   dem Providerstart identisch.
2. Derselbe Nachweis für Codex-, Claude- und Antigravity-Finalreview.
3. Prozessabbruch zwischen Recordappend und Checkpoint ist kontrolliert
   reparierbar oder hält mit einer konkreten Recovery-Anweisung an.
4. Resume erzeugt keine zweite Attestierung und startet keine Matrix erneut.
5. Planreview und Legacy-State-v3 bleiben unverändert funktionsfähig.

### Umsetzung und Nachweis

`WorkflowEngine` checkpointet die von `_attestation()` zurückgegebene History
jetzt vor dem nächsten externen Codex- oder Reviewer-Aufruf. Das gilt sowohl
für Slice-Reviews als auch für die drei Finalreview-Übergänge. Die
Regressionstests erzwingen, dass der Fake-Provider nur startet, wenn der
State-v3-Spiegel die fingerprintgebundene Attestierung bereits enthält.

- fokussierte Attestierungs-/Structured-Artifact-Tests: bestanden;
- vollständige Suite: `813 passed`;
- `git diff --check`: bestanden;
- lokaler Commit: `c42eca3 fix: checkpoint attestations before agent starts`.

Der bereits einseitig persistierte Altzustand wird absichtlich nicht still
repariert: Er bleibt fail-closed. Die unreviewte Slice-1-Arbeit wurde in
`stash@{0}` gesichert; das Implementierungsarbeitspaket muss mit einem neuen
State gestartet werden.

## P2-FU-006 – Antigravity erhält Promptverzeichnis statt Repository als Suchwurzel

**Status:** behoben mit Commit `a9546c3`; Neustart des betroffenen Laufs erforderlich  
**Priorität:** kritisch  
**Beobachtet in:** Lauf `20260819-142536Z`, Arbeitseinheit 2,
Übergang `claude_slice_review -> antigravity_slice_review`

### Beobachtung und Ursache

Claude konnte Slice 1 erfolgreich freigeben. Antigravity brach anschließend
mit `search path file:///tmp/dao-antigravity-runtime-.../src does not exist`
ab. Der schreibgeschützte Reviewer-Snapshot war vorhanden, aber der Adapter
übergab über das wiederholbare CLI-Argument `--add-dir` ausschließlich sein
privates Promptverzeichnis. Dadurch löste Antigravity einen normalen
Repositorypfad wie `src` relativ zum Promptverzeichnis auf.

### Umsetzung und Nachweis

Der Antigravity-Adapter bindet nun sowohl das private Promptverzeichnis als
auch den schreibgeschützten Repository-Snapshot als Workspace-Wurzeln. Der
Startprompt bezeichnet den Snapshot zusätzlich ausdrücklich als Wurzel für
repositoryrelative Reads und Suchen. Der Adapter verwirft diese Bindung beim
Cleanup.

- gezielte Adapter-/Runtime-/Snapshot-Tests: `88 passed`;
- vollständige Suite: `814 passed`;
- `git diff --check`: bestanden;
- lokaler Commit: `a9546c3 fix: expose reviewer snapshot to antigravity`.

Der durch den Providerfehler pausierte Lauf bleibt fingerprintgebunden an den
vorherigen Frameworkstand und wird nicht normal resumed. Sein unreviewter
Slice-1-Arbeitsstand ist recoverbar in `stash@{0}` gesichert.

## P2-FU-007 – Gleichbedeutendes Review-Evidenzlabel erzwingt unnötige Modellreparatur

**Status:** behoben mit Commits `c6b6a47` und `a1fd6f9`; Neustart des betroffenen Laufs erforderlich  
**Priorität:** kritisch  
**Beobachtet in:** Lauf `20260819-144920Z`, Arbeitseinheit 2,
`claude_slice_review`

### Beobachtung und Ursache

Claudes erstes Slice-Review war vollständig, positiv und enthielt ein
konkretes Finding sowie eine dreiteilige Evidenz. Die dritte Evidenzdimension
war jedoch mit `Realistic break condition:` statt `Break condition:`
beschriftet. Der lokale Normalisierer akzeptierte nur die zweite Form, rief
deshalb unnötig die kostenpflichtige Vertragsreparatur auf und verwarf auch
deren semantisch unveränderte Ausgabe.

### Umsetzung und Nachweis

Die lokale, semantisch neutrale Normalisierung akzeptiert nun beide eindeutig
gleichbedeutenden Labels. Kompakte Prosa-Trenner wie das real beobachtete
`raise|proceed` werden dabei typografisch neutralisiert; echte, durch
Leerraum abgegrenzte oder mehrdeutige Vertragstrenner bleiben fail-closed.
Ein Workflow-Level-Test und der Replay des vollständigen realen Claude-Logs
weisen nach, dass die beobachtete Ausgabe ohne zweiten Agentenaufruf
normalisiert wird.

- fokussierte Contract-/Workflow-Tests: `164 passed`;
- vollständige Suite nach der finalen Präzisierung: `816 passed`;
- `git diff --check`: bestanden;
- lokale Commits:
  - `c6b6a47 fix: normalize realistic review break conditions`;
  - `a1fd6f9 fix: preserve embedded review evidence separators`.

Der pausierte Lauf ist an den vorherigen Frameworkfingerprint gebunden. Sein
Slice-1-Arbeitsstand bleibt recoverbar in `stash@{0}` gesichert und der Lauf
wird nicht normal resumed.

### Nachtrag vom 2026-08-21 – Gedankenstrichvarianten

Ein späterer Claude-Slice-Review zeigte dieselbe Fehlerklasse in einer weiteren
eindeutigen Schreibweise: `Largest residual risk —` und
`Realistic break condition —` wurden statt Pipe-Trennzeichen beziehungsweise
Doppelpunktlabels verwendet. Trotz eines fachlich vollständigen Blockers wurde
dadurch ein zweiter, kostenpflichtiger Claude-Aufruf gestartet, der dieselbe
ungültige Evidenzform wiederholte.

Die lokale Normalisierung soll deshalb eindeutig bezeichnete Dreifeldformen
auch mit Bindestrich, Gedankenstrich oder Halbgeviertstrich in
`<dimensions> | <largest risk> | <break condition>` überführen. Das gilt nur,
wenn alle drei Labels genau einmal und ohne Mehrdeutigkeit vorhanden sind.
Finding, Verdict und Rationale bleiben bytegetreu; fehlende, doppelte oder
vertauschte Labels bleiben fail-closed. Eine solche rein syntaktische
Normalisierung darf keinen zweiten Providerprozess starten und muss durch den
tatsächlich beobachteten Antworttext als Regressionstest belegt werden.

### Abschluss des Nachtrags in Stabilisierungspaket 1.1B

Slice 1 normalisiert die gespeicherte reale Gedankenstrichantwort lokal und
verlustfrei. Eindeutige Separatorvarianten werden kanonisiert; mehrdeutige,
doppelte, fehlende oder widersprüchliche Formen bleiben fail-closed. Rein
syntaktische Korrekturen starten keinen zweiten Providerprozess. Die Umsetzung
ist in Commit `3827e81` enthalten und durch die abschließende
`961 passed`-Attestierung sowie beide Finalreviews verifiziert.

### Neuer Restfall aus dem 1.1C2-Finalreview

Im finalen Claude-Review von 1.1C2 trat trotz der 1.1B-Normalisierung eine
weitere eindeutig lesbare Variante auf. Claude lieferte eine einzelne
`REVIEW_EVIDENCE`-Zeile mit den in Prosa eingebetteten Labels `Largest
residual risk:` und `Realistic break condition:`, aber ohne die kanonischen
Pipe-Feldgrenzen. Verdict und fachliche Prüfung waren vollständig; dennoch
startete der Orchestrator einen zweiten `claude_contract_repair`-Prozess.

Der erste Aufruf benötigte 288 Sekunden und etwa 1,37 USD. Die rein formale
Reparatur benötigte weitere 89 Sekunden, vier Turns, 6.164 Output-Tokens und
etwa 0,14 USD und wiederholte dabei sämtliche geschlossenen Findings. Dieser
Fall bleibt als Kosten- und Vertragsrestpunkt offen: Eine genau einmal
vorhandene, eindeutig beschriftete Dreifeldzeile soll lokal normalisiert
werden; fehlende, doppelte oder widersprüchliche Labels bleiben fail-closed.

## P2-FU-008 – Ungültiges Review nach Reparatur beendet den Prozess ungeordnet

**Status:** behoben mit Commit `385d137`  
**Priorität:** kritisch  
**Beobachtet in:** Lauf `20260819-144920Z`, Arbeitseinheit 2,
`claude_slice_review`

### Beobachtung und Ursache

Wenn sowohl das ursprüngliche Reviewergebnis als auch die einmalige kompakte
Vertragsreparatur ungültig blieben, ließ `_run_review()` den internen
`WorkflowContractError` bis zur obersten Programmebene durchlaufen. Der Lauf
endete dadurch als allgemeiner State-v3-Fehler statt als persistierter,
fingerprintgebundener und resumierbarer Fehler der aktuell aktiven Rolle.

Das war ein generischer Fehlerpfad: Jede noch unbekannte Abweichung im
Reviewformat konnte trotz korrekter Fail-closed-Entscheidung den geregelten
Resume-Mechanismus umgehen.

### Umsetzung und Nachweis

`_run_review()` überführt einen nach der begrenzten Reparatur verbleibenden
Vertragsfehler nun in einen typisierten `AgentFailureKind.OUTPUT`. Der Fehler
wird über denselben Persistenzpfad wie andere Instanzfehler gespeichert; der
Workflow bleibt auf der ursprünglichen Claude- oder Antigravity-Reviewstufe,
startet keine nachgelagerte Rolle und erzeugt keinen Commit. Ein Resume setzt
genau an dieser Reviewstufe fort.

Die bisherigen negativen Dry-run-Vertragstests prüfen deshalb nun den
persistierten Exit-3-Halt statt eine ungefangene Python-Ausnahme.

- gezielte Workflow-/Dry-run-Fehlerpfade: `8 passed`;
- vollständige Suite: `816 passed`;
- `git diff --check`: bestanden;
- lokaler Commit: `385d137 fix: persist invalid reviewer contracts as resumable halts`.

## P2-FU-009 – Codex-Quota erkannt, datierter Reset aber nicht automatisch geparst

**Status:** behoben mit Commit `d28a2b0`  
**Priorität:** hoch  
**Beobachtet in:** Lauf `20260819-152415Z`, Übergang zu Slice 03,
`codex_implementation`

### Beobachtung und Ursache

Codex meldete die Quota korrekt als technischen Providerfehler, verwendete für
den Reset jedoch den Wortlaut `try again at Aug 20th, 2026 5:36 AM`. Der
Parser unterstützte ISO-Zeitstempel, relative Angaben sowie lokale Uhrzeiten
mit ausdrücklich genannter IANA-Zeitzone, aber noch kein englisches Datum mit
Ordinalendung und ohne Zeitzonenangabe. Der Lauf blieb deshalb korrekt mit
Exitcode 2 resumierbar, wechselte aber nicht in die automatische Warteschleife.

### Umsetzung und Nachweis

Das reale Format wird ausschließlich für Codex akzeptiert und mit der lokalen
IANA-Zeitzone des Orchestrator-Rechners verbunden. Auf dem beobachteten
Europe/Berlin-System ergibt `Aug 20th, 2026 5:36 AM` den Zeitpunkt
`2026-08-20T03:36:00Z`. Fehlt eine zuverlässig ermittelbare IANA-Zeitzone oder
ist die Angabe ungültig, vergangen oder mehrdeutig, bleibt das Verhalten
fail-closed bei manueller Fortsetzung. Andere englische Datumsangaben ohne
Resetformulierung werden nicht als Quota-Reset interpretiert.

- fokussierte Quota-Tests: `32 passed`;
- vollständige Suite: `842 passed`;
- `git diff --check`: bestanden;
- lokaler Commit: `d28a2b0 fix: parse dated Codex quota resets`.

## P2-FU-010 – QUOTA-RESUME-DIFF-Gate kann weder freigegeben noch verlassen werden

**Status:** behoben und lokal committed  
**Priorität:** kritisch  
**Beobachtet in:** Lauf `20260819-152415Z`, Arbeitseinheit 4,
`codex_implementation`

### Beobachtung und Ursache

Nach einer externen, bewusst vorgenommenen Repositoryänderung erzeugte der
erste Resume korrekt `QUOTA-RESUME-DIFF`. Das Gate enthielt jedoch weder einen
Fingerprint noch einen Resume-Schritt. Dadurch war es für `--approve-gate`
nicht freigabefähig. Ein weiterer gewöhnlicher Resume legte zwar intern eine
Bestätigung für den neuen Fingerprint ab, wertete die außerhalb des
Slice-Scopes liegenden Pfade aber weiterhin als unerwartet und erzeugte
dasselbe Gate erneut. Der Zustand bildete somit eine Endlosschleife.

### Umsetzung und Nachweis

Repositoryänderungen während eines Invocation-Halts erzeugen nun ein eigenes,
fingerprintgebundenes `quota_resume_diff`-Benutzergate mit exakten Pfaden und
dem ursprünglichen Resume-Schritt. Nur eine explizite Entscheidung für genau
diesen Fingerprint und diese Pfade erzeugt die passende Bestätigung; danach
deckt sie sowohl den Fingerprintwechsel als auch die zuvor ausgewiesene
Scopeabweichung ab. Ändert sich das Repository erneut, wird ein neues Gate für
den neuen Fingerprint erzeugt.

Bereits gespeicherte alte, ungebundene `QUOTA-RESUME-DIFF`-Gates werden ohne
manuelle State-Bearbeitung in den Invocation-Halt zurückgeführt und gegen den
aktuell vorliegenden Repositoryzustand neu gebunden. Eine eventuell durch
frühere gewöhnliche Resumeversuche erzeugte wirkungslose Bestätigung wird
dabei entfernt.

- fokussierte Workflow-, State- und Runtime-Tests: `180 passed`;
- vollständige Suite: `843 passed`;
- `git diff --check`: bestanden;
- lokaler Commit: `4f67819 fix: make quota resume diff gates approvable`.

## P2-FU-011 – Freigegebener Resume-Diff stoppt erneut vor dem Slice-Review

**Status:** behoben und lokal committed  
**Priorität:** kritisch  
**Beobachtet in:** Lauf `20260819-152415Z`, Arbeitseinheit 4,
Übergang `codex_implementation` → `claude_slice_review`

### Beobachtung und Ursache

Das fingerprintgebundene `QUOTA-RESUME-DIFF`-Gate ließ den pausierten
Codex-Aufruf korrekt weiterlaufen. Beim folgenden Review prüfte der
Orchestrator die kanonischen Änderungen jedoch erneut ausschließlich gegen die
ursprüngliche Slice-Allowlist. Die vorherige Benutzerentscheidung war für
diesen Scope-Check unsichtbar. Dieselben sechs bereits freigegebenen Pfade
erzeugten deshalb ein nicht fingerprintgebundenes `UNEXPECTED-PATH`-Gate.

### Umsetzung und Nachweis

Ein Scope-Verstoß erzeugt nun ein fingerprintgebundenes
`unexpected_file`-Benutzergate. Die Freigabe gilt nur für den exakten gesamten
Änderungsfingerprint und die exakt ausgewiesenen unerwarteten Pfade. Review und
Commit erkennen dieselbe Entscheidung; bei jeder weiteren Repositoryänderung
verfällt sie automatisch. Die persistierte Slice-Allowlist wird nicht
umgeschrieben oder erweitert.

Bereits gespeicherte alte, ungebundene `UNEXPECTED-PATH`-Gates werden durch
einen gewöhnlichen Resume erneut geprüft und dabei als freigabefähiges Gate
für den aktuellen Fingerprint persistiert.

- fokussierter Gate-Durchstich: `2 passed`;
- angrenzende Workflow-, State-, Runtime- und Dry-Run-Tests: `227 passed`;
- vollständige Suite: `849 passed`;
- `git diff --check`: bestanden;
- lokaler Commit: `c712e43 fix: persist unexpected path approvals across review`.

### Nachkorrektur am 20.08.2026

Nach der Claude-Adapterkorrektur zeigte sich, dass ein exakt freigegebenes
`QUOTA-RESUME-DIFF` beim unmittelbar folgenden Scope-Check noch nicht als
gleichwertige Pfadentscheidung erkannt wurde. Der Resume-Gate bindet sich an
die dort ausgewiesene unerwartete Pfadmenge; `_validate_change_boundary()`
berücksichtigte zuvor jedoch ausschließlich Entscheidungen vom Typ
`unexpected_file`.

Der Scope-Check akzeptiert nun zusätzlich eine `quota_resume_diff`-Entscheidung,
aber nur bei identischem Gesamtfingerprint und exakt identischer unerwarteter
Pfadmenge. Ein gemischter Durchstichtest mit erlaubten und fremden Pfaden
belegt, dass die Freigabe ohne zweites Gate bis zum Review weiterführt. Jede
weitere Repositoryänderung bleibt durch den neuen Fingerprint gesperrt.

- fokussierter Resume-/Scope-Durchstich: `1 passed`;
- vollständige Suite: `849 passed`;
- `git diff --check`: bestanden;
- lokaler Commit: `aea70ab fix: reuse exact resume diff approval for scope check`.

## P2-FU-012 – Claude beendet den Prozess ohne Reviewvertrag

**Status:** Ursache behoben und lokal committed  
**Priorität:** mittel  
**Beobachtet in:** Lauf `20260819-152415Z`, Arbeitseinheit 4,
`claude_slice_review`, Invocation `a3e20182fdd144b89f24977ea6a13fba`

### Beobachtung und Ursache

Nach erfolgreicher Gate-Freigabe und vollständiger PASS-Validierung wurde Claude
korrekt gestartet. Der Provider lieferte jedoch nur den extrahierten Inhalt
`completed` und damit keinen gültigen Reviewvertrag. Das persistierte
Agentenlog zeigte anschließend den eigentlichen Inhalt: Claude hatte sämtliche
Read-Aufrufe erfolglos für die Suche nach `review-manifest.md` verbraucht. Der
Adapter übergab nur den Dateinamen, obwohl das Manifest in einem separaten
privaten Runtime-Verzeichnis lag. `--add-dir` erteilte Leserechte, änderte aber
nicht das Arbeitsverzeichnis des Reviewers. Auch das Manifest selbst nannte die
Chunks nur relativ.

Dies war kein erneuter Scope-, Gate- oder Fingerprintfehler. Wiederholte Resumes
konnten ohne Adapterkorrektur nicht helfen.

### Umsetzung und Nachweis

Startdirektive und Capability-Diagnose nennen nun den absoluten Manifestpfad.
Das Manifest enthält für jeden Chunk den absoluten, durch `--add-dir`
freigegebenen Pfad. Damit kann Claude aus dem separaten Reviewer-Snapshot heraus
alle Evidenzdateien deterministisch lesen. Eine automatische Contract-Reparatur
wurde bewusst nicht ergänzt, weil sie fehlende Reviewevidenz nicht sicher
ersetzen könnte.

- Adaptertests: `24 passed`;
- Adapter-, Runtime- und Budgettests: `72 passed`;
- vollständige Suite: `849 passed`;
- lokaler Commit: `c0122f7 fix: expose Claude review packet paths`.

## P2-FU-013 – Entfernte Antigravity-Shell wird als lokale fehlende Binärdatei klassifiziert

**Status:** Klassifikation, `LineNumber`-Retry und Attempt-Telemetrie umgesetzt; entfernte Shellausfälle und autonome Abschlussentscheidung offen
**Priorität:** mittel  
**Beobachtet in:** Lauf `20260819-152415Z`, Arbeitseinheit 4,
`antigravity_slice_review`, Invocation `0431a2988ce6481fa1c3fa3fd3b846b2`

### Beobachtung und Ursache

Claude schloss Slice 03 erfolgreich mit `SLICE_APPROVAL: 03 | YES` ab.
Antigravity wurde anschließend für denselben Fingerprint gestartet, lieferte
aber aus seiner entfernten Tool-Laufzeit:

`remote error: run bash: fork/exec /usr/bin/bash: no such file or directory`

Der lokale `agy`-Prozess existierte und endete sogar mit Exitcode 0; die
strukturierte Providerhülle meldete den Fehlerstatus. Es fehlt daher nicht die
lokale Antigravity-Binärdatei. `classify_agent_failure()` erkennt derzeit den
allgemeinen Textmarker `no such file` und ordnet den Fehler dennoch als
`AgentFailureKind.BINARY` ein. Diese Klasse wird bewusst nicht automatisch
wiederholt. Der technisch vorübergehende entfernte Instanzfehler wird deshalb
als manueller `awaiting_resume`-Halt persistiert.

### Empfohlene Korrektur

- eindeutig entfernte Tool-Startfehler vor der allgemeinen lokalen
  `no such file`-Regel erkennen;
- sie einer eng begrenzten transienten Fehlerklasse beziehungsweise dem
  bestehenden bounded Network-Retry zuordnen;
- echte lokale `FileNotFoundError`- und fehlende CLI-Binärdateien weiterhin
  fail-closed als `BINARY` behandeln;
- Regressionstests für Klassifikation, höchstens zwei automatische Versuche,
  unveränderten Fingerprint und unveränderte Reviewreihenfolge ergänzen.

### Umsetzung und Nachweis

Der exakt erkannte entfernte Antigravity-Fehler wird nun vor der allgemeinen
`no such file`-Regel als transiente Providerinfrastruktur klassifiziert. Damit
greift der vorhandene, auf zwei Wiederholungen begrenzte Network-Retry. Ein
lokaler `FileNotFoundError` für `agy` bleibt `BINARY` und wird nicht automatisch
wiederholt.

### Betriebsnachtrag vom 2026-08-21

Der entfernte Shellfehler blieb auch nach der korrekten transienten
Klassifikation als wiederkehrendes Providerphänomen sichtbar: Mehrere
Antigravity-Reviews scheiterten beim ersten physischen Aufruf und waren beim
automatischen zweiten Aufruf mit identischem Providerinput-Digest erfolgreich.
Fachlich entsteht dabei nur ein Review, physisch jedoch ein zusätzlicher,
möglicherweise kostenpflichtiger Provideraufruf.

Die Betriebsmetriken müssen deshalb Erstfehler- und Retryquote,
Digestgleichheit, Laufzeit und Tokens gemeinsam pro logischer Reviewoperation
ausweisen. Ein bestätigter Remote-Instanzfehler zählt nicht als zweite
Reviewrunde. Vor einem erneuten Provideraufruf ist zu prüfen, ob ein
kostenarmer Laufzeit-Warm-up oder eine instanzlokale Wiederaufnahme möglich
ist; fachliche Ergebnisse oder Freigaben dürfen dabei weder übernommen noch
erfunden werden.

Beim manuellen Abschluss von 1.1C2 trat eine zweite, eng abgrenzbare entfernte
Antigravity-Runtimeklasse auf. Der lokale `agy`-Prozess und das Finalreview-
Preflight waren erfolgreich gestartet; die Providerhülle brach jedoch ohne
Reviewvertrag mit `additional properties 'LineNumber' not allowed` ab. Das
weist auf ein inkompatibles entferntes Toolargumentschema hin, nicht auf eine
fehlende lokale Binärdatei oder eine fachliche Ablehnung.

Vor einer automatischen Wiederholung darf ausschließlich diese exakte
Providerhüllen-Signatur als transient klassifiziert werden. Abweichende
Properties, lokale Startfehler und nicht eindeutig entfernte Schemafehler
bleiben harte Runtimefehler. Regressionstests müssen den unveränderten
Fingerprint, höchstens zwei physische Versuche, das Ausbleiben eines
Reviewrecords für den Fehlversuch sowie eine separate Attempt-/Kostenmessung
belegen.

### Ergebnis von Stabilisierungspaket 1.1D

1.1D führte für die exakte entfernte `LineNumber`-Signatur die enge
Fehlerklasse `antigravity_tool_schema` ein. Nur diese Klasse erhält höchstens
einen automatischen Retry; jeder physische Start und Abschluss wird als
eigener Providerattempt mit normalisierter Usage und Dauer persistiert.
Historische Ketten bleiben lesbar, und der allgemeine Network-Retryvertrag
bleibt absichtlich davon getrennt.

Der reale Abschluss zeigte die verbleibende Grenze: Antigravity scheiterte im
Review des Korrekturslices dreimal hintereinander mit dem entfernten
`/usr/bin/bash`-Fehler. Weil dieser Fall als allgemeine
Providerinfrastruktur/Network klassifiziert ist, griffen zwei automatische
Fortsetzungen und damit drei physische Starts. Alle drei endeten ohne
Reviewrecord; Attemptstatus und gleicher Inputdigest sind nachvollziehbar.

Die technische Beobachtbarkeit ist damit hergestellt, der autonome Abschluss
aber nicht. Ein Folgeauftrag darf weder eine weitere breite Retryklasse noch
eine synthetische Freigabe ergänzen. Er muss stattdessen eine kostenbegrenzte
Betriebsentscheidung nach ausgeschöpftem Retry modellieren und klar zwischen
extern nicht verfügbarer Reviewinstanz, fachlicher Ablehnung und ausdrücklich
autorisierter administrativer Ausnahme unterscheiden.

## P2-FU-014 – Freigegebene Zwischen-Commits blockieren den Slice-Commit

**Status:** behoben; Übergabe an laufenden Slice ausstehend  
**Priorität:** kritisch  
**Beobachtet in:** Lauf `20260819-152415Z`, Arbeitseinheit 4,
Übergang `antigravity_slice_review` → `slice_commit`

### Beobachtung und Ursache

Claude und Antigravity genehmigten Slice 03 für den vollständigen aktuellen
Fingerprint. Die Git-Transaktion verweigerte anschließend trotzdem den Commit
mit `slice HEAD changed after its persisted start`. Während des pausierten
Slice waren geprüfte Orchestratorreparaturen als eigene lokale Commits auf
demselben Branch entstanden und über fingerprintgebundene Gates freigegeben
worden. Review-/Resume-Schicht akzeptierten diesen Zustand, die Commit-Schicht
verlangte jedoch weiterhin strikt `HEAD == Slice-Start`.

### Umsetzung und Nachweis

Ein Zwischen-HEAD ist nun nur zulässig, wenn es vom persistierten Slice-Start
abstammt, exakt dem für denselben Gesamtfingerprint freigegebenen HEAD entspricht
und sämtliche Scopeabweichungen exakt gebunden wurden. Die Attestierung und
beide Reviews bleiben an den vollständigen Diff ab Slice-Start gebunden. Die
Git-Transaktion staged und committet dagegen ausschließlich den aktuellen
uncommitteten Delta gegen das freigegebene Zwischen-HEAD; vorhandene
Zwischen-Commits werden weder erneut aufgenommen noch umgeschrieben.

Ändert sich der Fingerprint erst am Commit-Schritt, springt der Workflow nun
automatisch zu Validierung, Claude und Antigravity zurück und wiederholt Codex
nicht. Zwei neue Durchstichtests sichern beide Verträge.

- angrenzende Git-, Workflow- und Produktionsszenarien: `153 passed`;
- vollständige Suite: `852 passed`;
- `git diff --check`: bestanden.

## P2-FU-015 – Finalreview-Preflight ignoriert gebundene externe Slice-Freigaben

**Status:** behoben; Scope-Deduplizierung und Finalreview-Hotfix-Gate in 1.1A ergänzt
**Priorität:** kritisch  
**Beobachtet in:** Lauf `20260819-152415Z`, Arbeitseinheit 5,
`codex_final_review`

### Beobachtung und Ursache

Slice 03 wurde für Fingerprint `98ee175b02a4…` vollständig validiert, von
Claude und Antigravity genehmigt und als Commit `cad779d` gebunden. Die zuvor
über ein fingerprintgebundenes Nutzer-Gate ausdrücklich freigegebenen Pfade
`src/git_service.py`, `tests/test_git_service.py` und
`tests/test_quota_wait.py` lagen außerhalb des ursprünglichen Task-Scope.

Der Slice-Commit akzeptierte diese Pfade deshalb korrekt. Das nachfolgende
Finalreview-Preflight berücksichtigte jedoch nur ursprünglichen Task-Scope und
persistierte Slice-Allowlists, nicht die bereits vorhandene Gate-/Commit-
Bindung. Es hielt daher widersprüchlich mit `UNAUTHORIZED-PATH` an. Die
Terminaldiagnose verlor außerdem `affected_paths` und zeigte `paths=(none)`.

### Vorbereitete Korrektur und Nachweis

Externe Pfade werden beim Finalreview nur dann zusätzlich anerkannt, wenn alle
folgenden Fakten zusammenpassen:

- genehmigte `UNEXPECTED_FILE`- oder `QUOTA_RESUME_DIFF`-Entscheidung in einer
  abgeschlossenen Slice-Work-Unit;
- strukturierter, genehmigter Nutzer-`GatePayload` mit identischem Fingerprint;
- `BindingPayload` vom Typ `commit` mit demselben Fingerprint;
- Binding-Ziel entspricht exakt dem Commit des betreffenden abgeschlossenen
  Slice.

Fehlender Gate-Record oder ein Binding auf einen anderen Commit bleibt
fail-closed. `affected_paths` werden nun in das persistierte Bootstrap-Gate
übernommen und dadurch in der Terminaldiagnose sichtbar. Der Durchstich gegen
den tatsächlich pausierten Lauf akzeptiert alle 38 Branch-Pfade ohne
Restbefund.

- fokussierte Verträge: `74 passed`;
- vollständige Suite: `856 passed`;
- `git diff --check`: bestanden;
- Commit: `48cf07a`.

### Nachkorrektur vom 2026-08-21

Beim Abschluss von 1.1A trat eine zweite Scopeabweichung auf: Der Finalreview
verband Task-Scope und die Scope-Pfade aller Slices ohne Deduplizierung. Ein in
mehreren Slices verwalteter Auditpfad führte dadurch bereits vor dem
Codex-Aufruf zu `validation path patterns must be unique`.

Die Scope-Vereinigung ist nun reihenfolgestabil und eindeutig. Ein während des
Finalreview-Halts notwendiger Orchestrator-Hotfix kann ausschließlich durch ein
strukturiertes, exaktes und fingerprintgebundenes Benutzergate bis zur
branchweiten Prüfung getragen werden. Historische Gates, abweichende
Fingerprints und überdeckende Pfadmengen bleiben wirkungslos. Regressionstests
decken sowohl überlappenden Task-/Slice-Scope als auch den explizit
freigegebenen Finalreview-Hotfix ab; die vollständige Suite bestand mit
`934 passed`.

## P2-FU-016 – Abschlussrollen verwenden unterschiedliche Fingerprint-Grenzen

**Status:** behoben und als Commit `69c3e24` gesichert  
**Priorität:** kritisch  
**Beobachtet in:** Lauf `20260819-152415Z`, Arbeitseinheit 5,
Übergang `codex_final_review` → `claude_final_review`

### Beobachtung und Ursache

Der Codex-Abschlussbericht wurde erfolgreich erzeugt und als strukturierter
`AgentResult` gespeichert. Das Claude-Preflight hielt unmittelbar danach mit
`CODEX-FINAL-RESULT-MISSING` an. Der Record fehlte nicht: Er war an den
Fingerprint `0f376d100885…` gebunden, während alle Provider-Bootstrap- und
Finalreview-Prüfungen den branchweiten Fingerprint `ac371f60ea8b…`
verwendeten.

Ursache war eine asymmetrische Startgrenze. Das Provider-Preflight sammelte im
Finalreview den vollständigen Diff ab `branch_base`; die generische
Record-Persistenz berechnete den Fingerprint dagegen weiterhin ab dem Start des
letzten Slice. Dadurch konnte Claude den tatsächlich vorhandenen
Codex-Abschlussrecord für denselben fachlichen Branchstand niemals finden.

### Vorbereitete Korrektur und Nachweis

Alle strukturierten Records eines `FINAL_REVIEW`-Work-Units verwenden nun
dieselbe branchweite Fingerprint-Grenze. Die Idempotenzschlüssel von Codex- und
Reviewerrecords enthalten den Fingerprint, damit ein legitimer erneuter Lauf
nach einer Fingerprintänderung nicht mit einem älteren Payload kollidiert.

Fehlt beim Claude- beziehungsweise Antigravity-Preflight nur der unmittelbar
erforderliche Vorgängerrecord für den aktuellen Fingerprint, springt der
Workflow automatisch zum Codex- beziehungsweise Claude-Abschlussschritt zurück.
Ein manueller Resume-Stopp ist dafür nicht mehr erforderlich; alle anderen
Preflight-Denials bleiben fail-closed.

- fokussierte Verträge: `11 passed`;
- vollständige Suite: `859 passed`;
- `git diff --check`: bestanden;
- Commit: `69c3e24`.

## P2-FU-017 – Abgelehntes Finalreview wird vor Korrekturwechsel nicht gespiegelt

**Status:** Korrektur vorbereitet und vollständig getestet; Commit ausstehend  
**Priorität:** kritisch  
**Beobachtet in:** Lauf `20260819-152415Z`, Übergang von Arbeitseinheit 5
(`final_review`) zu Arbeitseinheit 6 (`correction`)

### Beobachtung und Ursache

Claude persistierte ein gültiges, ablehnendes Finalreview samt Finding-
Transitionen in der strukturierten Record-Kette. Der Workflow startete danach
unmittelbar die Korrektur-Work-Unit. Dabei archivierte `bind_work_unit()` noch
den älteren Treiber-Mirror der Finalreview-Historie, in dem der neue
`ReviewAuditEvent` fehlte. Die Record-Kette enthielt die Entscheidung, der
State-v3-Mirror jedoch nur die daraus entstandene Korrektur-Work-Unit. Der
nächste externe Schritt hielt deshalb fail-closed mit
`structured reviewer decisions differ from the state-v3 mirror` an.

### Vorbereitete Korrektur und Nachweis

Ein ablehnendes Finalreview wird künftig vollständig checkpointed, solange die
Finalreview-Work-Unit noch aktiv ist. Erst danach wird die Korrektur-Work-Unit
angelegt. Für bereits vom alten Defekt betroffene Läufe existiert eine eng
begrenzte Kompatibilitätserkennung: Sie akzeptiert ausschließlich genau einen
fehlenden, autoritativen `denied`-Reviewrecord, wenn unmittelbar danach eine
Korrektur-Work-Unit existiert, deren Findings Teil dieses Reviews sind und die
vorherige Historie eine Attestierung für exakt denselben Fingerprint besitzt.
Alle anderen Record-/Mirror-Abweichungen bleiben gesperrt.

- fokussierte Verträge: `4 passed`;
- Durchstich gegen den tatsächlich pausierten Lauf: `CURRENT_RUN_STRUCTURED_CONTEXT_OK`;
- vollständige Suite: `860 passed`;
- Commit: ausstehend.

## P2-FU-018 – Unveränderte externe Pfade verlangen nach jeder Korrektur erneut ein Gate

**Status:** offen; für Stabilisierungspaket 1.1 vorgesehen  
**Priorität:** hoch  
**Beobachtet in:** Lauf `20260819-152415Z`, Arbeitseinheit 6,
Korrekturrunden zu `C-07` und `C-08`

### Beobachtung und Ursache

`tests/test_git_service.py` lag außerhalb der persistierten Slice-Allowlist.
Die dort enthaltenen C-05-Negativtests wurden deshalb korrekt über ein
fingerprintgebundenes `UNEXPECTED-PATH`-Gate geprüft und freigegeben. Spätere
Korrekturrunden änderten ausschließlich andere Dateien. Dadurch änderte sich
jedoch der Gesamtfingerprint des kanonischen Diffs. Obwohl Inhalt und Diff von
`tests/test_git_service.py` unverändert blieben, galt die vorherige Freigabe
formal nicht mehr und derselbe Pfad löste vor jedem weiteren Review erneut ein
Benutzergate aus.

Das bestehende Verhalten ist fail-closed und verhindert, dass eine Freigabe
für einen alten Gesamtstand still auf geänderte externe Inhalte übertragen
wird. Es bindet die Entscheidung aber gröber als erforderlich: Eine
unveränderte, bereits geprüfte Pfadänderung wird durch jede sachfremde Änderung
an einer anderen Datei erneut freigabepflichtig.

### Gewünschtes Verhalten

Eine externe Pfadfreigabe wird zusätzlich an einen kanonischen Inhalts- oder
Diffdigest jedes freigegebenen Pfades gebunden. Bei einem späteren
Gesamtfingerprint darf sie nur dann automatisch übernommen werden, wenn:

1. Pfad, Pfadklassifikation und pfadspezifischer Digest exakt identisch sind;
2. die ursprüngliche Entscheidung genehmigt und als strukturierter Gaterecord
   in derselben Run-/Slice-Abstammung persistiert ist;
3. der Pfad seit der Freigabe weder umbenannt noch gelöscht noch erneut
   verändert wurde;
4. ausschließlich andere, unabhängig autorisierte Pfade den neuen
   Gesamtfingerprint verursacht haben;
5. Review, Attestierung und Commit weiterhin an den neuen vollständigen
   Gesamtfingerprint gebunden werden.

Die Übernahme darf die persistierte Slice-Allowlist nicht pauschal erweitern
und Codex keine zukünftige Schreibberechtigung für den Pfad geben. Ändert sich
auch nur ein Byte des freigegebenen Pfaddiffs, ist ein neues exaktes Gate
erforderlich. Eine bloße Übereinstimmung des Pfadnamens reicht niemals aus.

### Erforderliche Regressionstests

1. Eine genehmigte externe Datei mit identischem Pfaddigest benötigt nach
   einer Änderung an einer anderen Datei kein zweites Gate.
2. Eine nachträgliche Änderung derselben externen Datei erzeugt zwingend ein
   neues fingerprint- und pfadgebundenes Gate.
3. Umbenennen, Löschen, Type-Change, Symlinkwechsel und abweichende
   Zeilenendennormalisierung werden nicht als unveränderter Inhalt akzeptiert.
4. Freigaben aus einem anderen Run, Slice oder nicht verwandten Work-Unit-Zweig
   werden nicht übernommen.
5. Unter- oder überdeckende Pfadmengen sowie fehlende strukturierte
   Gate-/Bindingrecords bleiben fail-closed.
6. Der Commit verwendet die übernommene Pfadfreigabe nur für den unveränderten
   pfadspezifischen Digest, während Attestierungen und Reviews weiterhin den
   vollständigen aktuellen Gesamtfingerprint prüfen.

## P2-FU-019 – Genehmigtes Review liegt nach Checkpointfehler vor seinem State-Spiegel

**Status:** Hotfix umgesetzt und vollständig getestet  
**Priorität:** kritisch  
**Beobachtet in:** Lauf `20260819-152415Z`, Arbeitseinheit 6,
`claude_slice_review` nach Schließung von `C-08`

### Beobachtung und Ursache

Claude lieferte `SLICE_APPROVAL: 04 | YES` und schloss C-08. Reviewrecord und
Finding-Schließung wurden autoritativ veröffentlicht, der State-v3-Checkpoint
scheiterte jedoch vor der Spiegelung. Der vorhandene hashgebundene
Reviewer-Replay war absichtlich nur für abgelehnte Reviews implementiert.
`resolve_resume_state()` hielt deshalb bereits vor Antigravity mit
`finding status differs from state-v3` an.

### Umsetzung und Nachweis

Der Replaypfad behandelt genehmigte und abgelehnte Slice-Reviews symmetrisch,
bleibt aber an genau einen Reviewrecord, Reviewer, Runde, Verdict,
Orchestrator-Attestierung, Idempotenzdigest und eindeutigen Providerlog
gebunden. Ein genehmigtes Claude-Review wird lokal erneut vollständig
validiert, in den State gespiegelt und führt zu Antigravity; ein genehmigtes
Antigravity-Review führt zum Slice-Commit. Record-/Output-Verdictabweichungen
und nicht geschlossene Findingtransitionen eines positiven Korrekturreviews
bleiben gesperrt.

Hat sich der Repositoryfingerprint nach dem persistierten Claude-Review
geändert, wird dessen alter Entscheid zwar zur Mirror-Reparatur replayt, aber
nicht für den neuen Stand verwendet. Der Workflow springt automatisch von
Antigravity zu Validierung und Claude zurück. Erst eine Claude-Freigabe für den
aktuellen Gesamtfingerprint erlaubt Antigravity.

Ein zweiter Fehler wurde beim echten Replay sichtbar: `open_findings` im
Korrektur-Work-Unit beschreibt die ursprüngliche Finding-Zuordnung, wurde beim
Resume-Abgleich aber fälschlich als aktueller Findingstatus interpretiert. So
überschrieb die unveränderliche Zuordnung die gerade replayte Schließung wieder
mit `open`, und derselbe `finding status differs from state-v3`-Fehler trat beim
Checkpoint erneut auf. Der Abgleich bevorzugt nun den aus `runtime_history`
projizierten Live-Status; `open_findings` dient nur noch als Fallback für alte
Mirrors ohne eigenen Findingeintrag.

- positiver Approved-Replay-Durchstich ohne zweiten Claude-Provideraufruf;
- Migrationstest für eine Record-voraus-Finding-Schließung;
- automatischer Fingerprint-Rewind vor Antigravity;
- fokussierte Übergangstests: `143 passed`;
- vollständige Suite: `903 passed`;
- `git diff --check`: bestanden.

## P2-FU-020 – Codex-Abschlussanalyse kann erkannte Defekte nicht verbindlich übergeben

**Status:** für Stabilisierungspaket 1.1 einplanen  
**Priorität:** hoch  
**Beobachtet in:** Lauf `20260819-152415Z`, wiederholte branchweite
Abschlussreviews nach C-09/C-10

### Beobachtung und Ursache

Codex identifizierte in seinem branchweiten Abschluss-Selbstcheck mehrere
konkrete Defekte, darunter unvollständige Binding-Referenzprüfungen,
unzureichende Attestierungsbindung und den fehlenden Produktivdatei-Limitcheck.
Der Codex-Vertrag erlaubt in diesem Schritt jedoch nur Antworten auf bereits
offene Findings und einen Abschlussbericht; Codex kann keinen strukturierten
neuen Befund persistieren. Claude übernahm zunächst nur C-09 und C-10. Der
bereits sichtbare Binding-Defekt erschien deshalb erst in der folgenden
Gesamtreviewrunde als C-11 und verursachte einen weiteren vollständigen
Korrektur-/Review-Roundtrip.

Ein AI-Schritt, der ausdrücklich einen Review beziehungsweise adversarialen
Vollständigkeitscheck ausführt, darf seine Ergebnisse nicht nur in ungebundener
Prosa hinterlassen. Zugleich darf Codex als schreibende Implementiererrolle
nicht seine eigenen Findings freigeben oder schließen.

### Zielvertrag

1. Der Schritt wird fachlich korrekt als `codex_final_analysis` bezeichnet und
   nicht als selbstgenehmigendes Review.
2. Codex darf strukturierte, fingerprintgebundene Defektkandidaten ausgeben,
   beispielsweise `CANDIDATE_FINDING: X-01 | BLOCKER | Beschreibung |
   Akzeptanztest`.
3. Jeder Kandidat wird append-only in der Record-Kette persistiert und blockiert
   den Abschluss, bis Claude ihn im selben Finalreview ausdrücklich disponiert.
4. Claude muss für jeden Kandidaten entweder ein eigenes `C-*`-Finding eröffnen
   oder ihn mit konkreter Begründung zurückweisen. Stilles Weglassen ist
   unzulässig.
5. Antigravity erhält Kandidaten und Claude-Dispositionen als strukturierte
   Fakten, nicht nur als Teil einer großen Prosa-Evidenz.
6. Codex bleibt ohne Freigabe- und Schließungsrecht für diese Kandidaten; die
   Rollentrennung zwischen Implementierung und Review bleibt erhalten.
7. Wird dieser verbindliche Kandidatenvertrag nicht umgesetzt, entfällt der
   Codex-Abschlusslauf vollständig und Claude erhält direkt die branchweite
   Evidenz. Ein teurer, nicht auswertbarer Prosa-Selbstcheck ist nicht sinnvoll.

### Abnahmetests

- Mehrere Codex-Kandidaten müssen in genau einem Claude-Aufruf vollständig
  disponiert werden.
- Ein ausgelassener Kandidat macht Claudes Review formal ungültig, bevor eine
  Freigabe oder Korrekturrunde entstehen kann.
- Ein zurückgewiesener Kandidat benötigt eine nicht leere Begründung.
- Ein akzeptierter Kandidat erzeugt genau ein Claude-eigenes Finding mit
  nachvollziehbarer Herkunftsreferenz.
- Resume nach Record-/Checkpoint-Unterbrechung erzeugt weder doppelte
  Kandidaten noch doppelte Claude-Findings.

### Entscheidung nach Abschluss von 1.1A

Der Schritt darf nur in einer von zwei klaren Formen fortbestehen:

- Codex erhält einen eigenen strukturierten, fingerprintgebundenen
  Befundkanal. Jeder neue Befund muss von Claude und anschließend Antigravity
  beurteilt werden; Codex darf ihn niemals selbst schließen oder freigeben.
- Oder der Codex-Finalschritt wird auf einen nicht-reviewenden Übergabebericht
  reduziert. Dann darf der Auftrag keine adversariale Defektsuche verlangen;
  ein dennoch erkannter konkreter Defekt muss einen typisierten
  Stop-/Korrekturpfad auslösen.

Ein konkreter Defekt in ungebundener Prosa bei anschließendem Weiterlauf ist
in beiden Varianten unzulässig. Dieser Vertrag wird erst beim Neuzuschnitt
nach 1.1A umgesetzt und nicht nachträglich in den abgeschlossenen 1.1A-Scope
eingeschoben.

## P2-FU-021 – Erfolgreicher Direktlauf endet still und lässt Inbox-Artefakte zurück

**Status:** für Stabilisierungspaket 1.1 einplanen  
**Priorität:** mittel  
**Beobachtet in:** erfolgreicher Abschluss des Laufs `20260819-152415Z`

### Beobachtung

Claude und Antigravity genehmigten den finalen Fingerprint, der Workflowstate
stand anschließend auf `completed`, alle sechs Slices waren committed, der
Audit-Abschlusscommit `b819be1` existierte und die strukturierte Kette enthielt
genau einen gültigen Completion-Record. Der Direktaufruf kehrte dennoch nur
zum Shell-Prompt zurück. Eine eindeutige Erfolgsmeldung mit Run-ID,
Abschlusscommit, Teststatus und Zielpfad fehlte. Die letzte sichtbare Zeile war
sogar eine irreführende historische Recovery-Warnung.

Außerdem verblieben bei diesem erfolgreichen Direktlauf die Implementierungs-
MD sowie alte `.attempts`-/Watch-Sidecars in `inbox/`. Die Done-Outbox-
Transaktion ist derzeit an den Watcher gebunden und wird beim direkten
`run_task --resume` nicht äquivalent abgeschlossen.

### Zielvertrag und Abnahme

- Jeder erfolgreiche Lauf schreibt genau eine eindeutige terminale
  `WORKFLOW COMPLETED`-Meldung mit Run-ID, Finalstatus, Abschlusscommit und
  Outbox-/Taskstatus.
- Die letzte fachliche Logzeile darf keine historische Denial-/Recoverywarnung
  sein, wenn der terminale Zustand erfolgreich ist.
- Watch- und Direktmodus verwenden dieselbe idempotente Success-Finalisierung
  für Inbox-Task, Attempt-Sidecar, Watch-Sidecar und Done-Outbox.
- Ein Crash zwischen Completion-Record, Auditcommit und Taskverschiebung wird
  beim nächsten Start ohne erneuten Provideraufruf fertiggestellt.
- Wiederholte Finalisierung erzeugt weder doppelte Outboxdateien noch einen
  zweiten Completion- oder Auditcommit.

## P2-FU-022 – Quota-Wartebetrieb erzeugt zu häufige Heartbeats und endet zu früh

**Status:** durch Stabilisierungspaket 1.1C gelöst und auf `master` integriert
**Priorität:** mittel

### Beobachtung

Während einer mehrstündigen automatischen Quota-Pause meldet der Orchestrator
standardmäßig alle fünf Minuten einen Heartbeat. Für reines Betriebsmonitoring
ist das zu häufig. Zugleich ist die derzeitige Standardobergrenze von 86.400
Sekunden für eindeutig erkannte Providerresets mit mehrtägigem Abstand zu
kurz; solche Fälle verlangen unnötig einen manuellen Resume.

### Zielvertrag

- Das Standardintervall für periodische Quota-Wait-Heartbeats beträgt 3.600
  Sekunden und bleibt über CLI beziehungsweise Umgebung konfigurierbar.
- Der erste Wartehinweis, der berechnete lokale und UTC-Resumezeitpunkt, eine
  Unterbrechung sowie die abschließende Fortsetzungsmeldung bleiben sofort
  sichtbar.
- Die standardmäßige maximale automatische Quota-Wartezeit beträgt 604.800
  Sekunden beziehungsweise sieben Tage und bleibt konfigurierbar.
- Ein eindeutiger Reset genau an dieser Grenze ist automatisch wartbar. Ein
  späterer, fehlender oder mehrdeutiger Resetzeitpunkt bleibt fail-closed und
  manuell resumierbar.
- Sicherheitszuschlag und Grenzvergleich werden getrennt behandelt, damit der
  Zuschlag die zulässige Provider-Resetspanne nicht still verkürzt.

### Abnahme

- Runtime-, CLI- und Dokumentationsdefaults stimmen für beide Werte überein.
- Fake-Clock-Tests belegen mehrtägiges Warten mit höchstens einem periodischen
  Heartbeat pro Stunde, ohne reale Verzögerung der Tests.
- Exakte Grenzfälle, Sicherheitszuschlag, Unterbrechung und Fortsetzung sind
  abgedeckt.
- Fingerprint-, Rollen- und Resume-Bindung bleiben unverändert.

### Abschluss in Stabilisierungspaket 1.1C

Runtime, CLI und Dokumentation verwenden nun einen stündlichen
Quota-Wait-Heartbeat und eine konfigurierbare maximale Resetspanne von sieben
Tagen. Sicherheitsmarge und zulässige Providerspanne werden getrennt
behandelt. Fake-Clock-Tests decken Stundenheartbeat, exakte Sieben-Tage-Grenze,
spätere und mehrdeutige Resets sowie Unterbrechung und Fortsetzung ohne reale
Wartezeit ab. Die Umsetzung liegt in Commit `fac92b0`; die spätere
Branch-Vollsuite bestand mit `993 passed`.

## P2-FU-023 – Deterministisch bekannte Reviewfelder lösen unnötige Providerreparaturen aus

**Status:** durch Stabilisierungspaket 1.1B gelöst; auf Feature-Branch final genehmigt

**Priorität:** hoch

**Beobachtet in:** Claude-Slice-Review mit vollständiger fachlicher
Entscheidung, aber fehlendem `TEST_FILES_TOUCHED`

### Beobachtung und Zielvertrag

Der Orchestrator startete Claude ein zweites Mal, obwohl Findings,
Findingstatus und negatives Verdict bereits eindeutig vorlagen. Es fehlte nur
`TEST_FILES_TOUCHED`, dessen einzig zulässiger Wert aus dem gebundenen
`StepContract` bekannt war. Der Reparaturaufruf wiederholte die fachliche
Prüfung und verbrauchte zusätzlich 5.125 Output-Tokens, 44 Sekunden und etwa
0,12 USD.

Felder mit genau einem aus dem fingerprintgebundenen Schrittvertrag
ableitbaren Wert werden lokal erzeugt oder ergänzt. Dazu gehören insbesondere
ein fehlendes `TEST_FILES_TOUCHED`, Reviewer, Slice-Bezeichner und eindeutig
ableitbare Abschlussmarker. Ein vorhandener widersprüchlicher Wert wird nie
überschrieben. Findinginhalt, Findingklasse, Status, Verdict und Rationale
werden nicht synthetisiert.

### Abnahme

- Ein fachlich vollständiges Review ohne `TEST_FILES_TOUCHED` wird ohne
  weiteren Providerprozess lokal vervollständigt.
- Die Liste entspricht bytegenau `StepContract.expected_test_files`; eine
  vorhandene Abweichung bleibt ein Vertragsfehler.
- Die lokale Reparatur wird protokolliert, zählt aber nicht als Agenten-Turn
  oder Reviewrunde.
- Ein zweiter Provideraufruf bleibt semantischer Mehrdeutigkeit vorbehalten;
  Grund und Zusatzkosten werden separat ausgewiesen.

### Abschluss in Stabilisierungspaket 1.1B

Slice 1 ergänzt ausschließlich Metadaten, für die der gebundene Schrittvertrag
genau einen zulässigen Wert vorgibt. Vorhandene Abweichungen, unbekannter
Providerabschluss, Trunkierungsverdacht und fachlich fehlende Entscheidungen
bleiben gesperrt; Approval, Findingsemantik und Rationale werden nie lokal
erzeugt. Lokale Vervollständigung und echte Providerreparatur werden getrennt
diagnostiziert und budgetiert. Commit `3827e81` sowie der abschließende
Finalreview belegen den Zielvertrag.

## P2-FU-024 – Slice- und Korrekturreviews erhalten zu viel historische Evidenz

**Status:** durch Stabilisierungspaket 1.1B gelöst; auf Feature-Branch final genehmigt

**Priorität:** hoch

### Beobachtung

Vor der strukturierten Gesamtüberarbeitung erhielten Claude und Antigravity im
Slice-Review im Wesentlichen Slice-Diff, Anforderungen, offene Findings und
Testergebnis. Der Hybridpfad überträgt zusätzlich Record-/Mirror-Historie,
Finding-Lebenszyklen, Auditprosa und teilweise vollständige Dateien. Ein als
selektiv bezeichneter Snapshot umfasste zuletzt 136 Repositorydateien;
Korrekturreviews wiederholen zudem geschlossene Findings mit vollständiger
Begründung.

### Zielvertrag

- Ein Slice-Review erhält nur aktuellen Slice-Diff, Akzeptanzkriterien,
  kompakte fingerprintgebundene Attestierung und offene Findings.
- Ein Korrekturreview erhält nur das Delta seit dem abgelehnten Fingerprint
  sowie betroffene Findings und Akzeptanztests.
- Geschlossene Findings werden als ID, Status, Closure-Digest und höchstens
  einzeilige Zusammenfassung übertragen.
- Auditprosa, vollständige Reviewertexte und komplette Recordhistorie werden
  nicht erneut eingebettet.
- Repositoryzugriff ist auf exakte Slice-Pfade und manifestierte
  Abhängigkeiten begrenzt; zusätzliche Reads sind nachvollziehbar und
  budgetiert.
- Die vollständige branchweite Evidenz wird nur für das einmalige
  Gesamtreview materialisiert und pro Fingerprint inhaltsadressiert
  wiederverwendet.

### Abnahme

- Synthetische Slice- und Korrekturläufe enthalten keine Auditprosa,
  geschlossenen Volltexte oder sachfremden Dateien im Providerinput.
- Derselbe Korrekturfingerprint erzeugt byteidentisch dasselbe Reviewpaket und
  höchstens einen fachlichen Revieweraufruf.
- Zeichen, Bytes, Ausgabetokens, Turns und physische Aufrufe werden pro
  logischer Reviewoperation und kumuliert ausgewiesen.
- Claude und Antigravity erhalten dasselbe kanonische Evidenzmanifest;
  providerspezifische Transporthüllen verändern den semantischen Digest nicht.
- Das branchweite Finalreview bleibt adversarial vollständig, wird aber nicht
  für jeden Slice oder jede Korrekturrunde wiederholt.

### Abschluss in Stabilisierungspaket 1.1B

Slice 2 führte ein kanonisches, inhaltsadressiertes Reviewbasispaket ein,
filterte Diff und Reviewerworkspace auf manifestierte Pfade und entfernte
Auditprosa sowie vollständige historische Reviewertexte aus Slice- und
Korrekturpaketen. Claude und Antigravity verwenden denselben Basisdigest;
Claudes vorherige Freigabe bleibt eine kleine, rollenrichtige Hülle.

Der erste Gesamtreview fand eine Scope-Lücke für Same-Slice-Korrekturrunden:
Ohne betroffene Finding-IDs hätte der Paketkern wieder alle Findings
eingebettet. Die Abschlusskorrektur stellt nun auf den bereits berechneten
Korrekturzweck ab und lehnt ein leeres betroffenes Finding-Scope fail-closed
ab. Die Umsetzung liegt in `fc44f70` und `64b14e2`; die vollständige Suite
bestand abschließend mit `961 passed`.

## P2-FU-025 – Finalreview-Evidenz wird für denselben Fingerprint mehrfach kompaktiert

**Status:** offen nach Stabilisierungspaket 1.1C

**Priorität:** hoch

### Beobachtung

Im manuellen Abschlusslauf von 1.1C2 erschien `Final review evidence
compacted` mindestens dreizehnmal. Mehrere Gruppen meldeten identische
`original_chars`, `evidence_chars` und denselben Fingerprint unmittelbar vor
Codex, vor und nach Claude, vor der Claude-Vertragsreparatur sowie vor und
während Antigravity. Das sind keine zusätzlichen Revieweraufrufe, aber echte
wiederholte Diff-/Kompaktierungsarbeiten auf dem Windows-/WSL-Dateisystem.

Teilweise wuchs nur die verwaltete Auditprojektion von 507.217 auf 510.451
beziehungsweise 530.674 Rohzeichen. Die kompaktierte Evidenz und der
fachliche Fingerprint blieben unverändert. Damit invalidiert derzeit selbst
eine Änderung, die anschließend bewusst aus der Modellevidenz entfernt wird,
unnötig lokale Übergangsarbeit und erzeugt kaum hilfreiche Standardlogs.

### Zielvertrag und Abnahme

- Pro unverändertem fachlichem Fingerprint wird genau ein kanonisches
  `WorkflowChanges`-/Finalreview-Evidenzergebnis materialisiert.
- Boundary-Check, Attestierung, Promptbau und Provider-Preflight verwenden
  denselben Digest und denselben inhaltsadressierten Cacheeintrag.
- Der Rollenwechsel Codex zu Claude zu Antigravity kompaktiert dieselbe
  fachliche Evidenz nicht erneut. Fortschreibungen ausschließlich innerhalb
  verwalteter, aus der Evidenz entfernter Auditblöcke invalidieren den
  semantischen Cache nicht.
- Jede echte Repositoryänderung außerhalb dieser Projektionen verwirft den
  Cache vollständig und erzeugt einen neuen Fingerprint.
- Das Standardlog meldet nur tatsächliche Neuberechnungen; Cachetreffer sind
  höchstens unter `--verbose` sichtbar.
- Ein instrumentierter End-to-End-Test zählt für einen unveränderten
  Finalreview-Übergang genau eine teure Diff-/Kompaktierungsberechnung.

## P2-FU-026 – Plan-Handoff und Reviewpaket verwenden unterschiedliche Slice-Verträge

**Status:** Self-Hosting-Hotfix in 1.1C2 umgesetzt und auf `master` integriert

**Priorität:** kritisch

### Beobachtung und Ursache

Der 1.1C2-Plan wurde vom `PLAN_ONLY`-Handoff-Parser akzeptiert, von Claude und
Antigravity freigegeben, commitgebunden umgesetzt und vollständig validiert.
Erst beim anschließenden Slice-Review scheiterte der kanonische Reviewpaketbau
mit `Slice section lacks an unambiguous goal or acceptance criteria`.

Der Handoff-Parser verlangte Slice-Überschrift und exakte Pfade; der
Reviewpaket-Parser verlangte zusätzlich exakt `**Ziel**` und
`#### Akzeptanzkriterien`. Der generierte Plan verwendete den eindeutigen
Slice-Titel als Ziel und `#### Fokussierte synthetische Akzeptanztests` als
Kriterien. Zwei unabhängige Parser hatten damit unterschiedliche Verträge,
obwohl derselbe Plan beide Übergänge durchlaufen musste.

### Umsetzung und Nachweis

Ziel und Akzeptanzkriterien werden nun einmal durch den gemeinsamen
Plan-Handoff-Parser extrahiert. Explizite ältere Überschriften und die
repository-grounded generierte Form bleiben kompatibel. Bereits der
`PLAN_ONLY`-Handoff validiert alle später vom Reviewpaket benötigten Fakten;
der Reviewpaketbau verwendet dieselbe Extraktion. Der gebundene Plan musste
nicht semantisch verändert werden.

- gemeinsamer positiver Test für die generierte Planform;
- negative Handoff-Tests für fehlende Akzeptanzkriterien;
- jeder akzeptierte Handoff kann unmittelbar ein kanonisches Reviewpaket
  bilden;
- vollständige Suite nach dem Hotfix: `984 passed`.

## P2-FU-027 – Finalreview validiert denselben Fingerprint unnötig erneut

**Status:** Self-Hosting-Hotfix in 1.1C2 umgesetzt und auf `master` integriert

**Priorität:** kritisch

### Beobachtung und Ursache

Nach erfolgreichem Slice-Review und bestandener Validierungsmatrix wechselte
der Workflow in die Finalreview-Work-Unit, verlor dabei jedoch die letzte
fingerprintgebundene Attestierung aus der aktiven History. Deshalb führte das
Finalreview dieselbe Matrix für denselben Fingerprint erneut aus.
Laufzeitabhängige Testausgabe erzeugte dabei einen anderen Output-Digest unter
derselben deterministischen Attestierungs-ID. Die ArtifactBridge verweigerte
den semantisch abweichenden Dual-Write zu Recht mit `structured artifact
differs semantically from the state-v3 statement`.

### Umsetzung und Nachweis

Der Übergang in die Finalreview-Work-Unit übernimmt die neueste Attestierung
zusammen mit ihrem `ValidationAuditEvent`. Ein bereits am Übergang
gespeicherter älterer Zustand rekonstruiert beide Fakten deterministisch aus
der archivierten vorherigen Work-Unit. Bei unverändertem Fingerprint läuft
weder die Matrix noch die semantische Attestierung ein zweites Mal; bei einem
geänderten Fingerprint bleibt die reguläre Neuvalidierung erhalten.

Direkte Übergangs- und Resume-Regressionen belegen genau eine Matrix und eine
Attestierung sowie die provider- und validierungsfreie Rekonstruktion nach dem
Checkpoint. Die vollständige Suite bestand mit `985 passed`.

## P2-FU-028 – Vollständige Reviewerantwort wird wegen `EVIDENCE:` verworfen

**Status:** Self-Hosting-Hotfix in 1.1C2 umgesetzt und auf `master` integriert

**Priorität:** kritisch

### Beobachtung und Ursache

Claude lieferte im 1.1C2-Korrekturreview einen fachlich vollständigen Vertrag
mit `REVIEWER`, begründeter Schließung von C-03, `PRE_MORTEM`, positivem
`SLICE_APPROVAL` und abschließendem `STATUS: DONE`. Zwischen Reviewer und
Vertragsmarkern stand ein ausführlicher Aufzählungsabschnitt unter der
Zwischenüberschrift `EVIDENCE:`. Der strikte Parser behandelte diese
Überschrift als unbekannten State-v3-Marker und verwarf die gesamte
Freigabe. Ein Resume hätte denselben kostenpflichtigen Claude-Review erneut
gestartet.

### Umsetzung und Nachweis

Der Prompt untersagt nichtvertragliche Prosa weiterhin. Zusätzlich entfernt
die lokale Normalisierung genau einen eindeutig abgegrenzten, rein aus
Aufzählungszeilen bestehenden `EVIDENCE:`-Block, wenn danach ein vollständiger
und widerspruchsfreier Reviewer-Vertrag verbleibt. Markerartige,
mehrdeutige, doppelte oder unvollständige Varianten bleiben fail-closed;
Findingstatus, Approval und Pre-Mortem werden niemals erfunden.

Ein diagnosegebundener unveränderter Output kann nach einem fehlgeschlagenen
State-Checkpoint ohne Providerwiederholung wiederverwendet werden, wenn Rolle,
Work-Unit, Step, Fingerprint, Loginhalt und SHA-256-Digest exakt
übereinstimmen. Positive und adversarielle Tests verwenden die gespeicherte
Realantwort; die vollständige Suite bestand mit `991 passed`.

## P2-FU-029 – Nachträgliche Korrektur-Slices werden fälschlich im Plan gesucht

**Status:** Self-Hosting-Hotfix in 1.1C2 umgesetzt und auf `master` integriert

**Priorität:** kritisch

### Beobachtung und Ursache

Nach erfolgreicher Validierung einer Abschlusskorrektur scheiterte der
Reviewpaketbau mit `approved plan must contain exactly one Slice 3 section`.
Der freigegebene Plan enthielt ordnungsgemäß nur den ursprünglichen Slice 1.
Slice 3 war erst später durch eine Finalreview-Ablehnung als
Korrektur-Work-Unit entstanden und konnte deshalb nicht Bestandteil dieses
Plans sein.

### Umsetzung und Nachweis

Reguläre Implementierungsslices beziehen Ziel und Akzeptanzkriterien weiterhin
ausschließlich aus dem freigegebenen Plan. Nachträglich erzeugte
Korrektur-Slices leiten ihren Auftrag dagegen deterministisch aus der
fingerprintgebundenen Findingmenge und deren persistierten Akzeptanztests ab.
Der Plan wird weder ergänzt noch nachträglich umgeschrieben.

Ein Regressionstest bildet exakt `Plan: Slice 1` und `Korrektur: Slice 3` ab;
die vollständige Suite bestand mit `992 passed`, `git diff --check` war sauber.

## P2-FU-030 – Genehmigte Korrekturpfade fehlen im Finalreview-Preflight

**Status:** Abschluss-Hotfix in 1.1C2 umgesetzt und auf `master` integriert

**Priorität:** kritisch

### Beobachtung und Ursache

Claude und Antigravity hatten die nachträgliche Slice-3-Korrektur für
denselben Fingerprint freigegeben, die Validierung war erfolgreich und der
Slice commitgebunden abgeschlossen. Das Finalreview-Preflight verweigerte
dennoch `src/workflow.py` und `tests/test_review_runtime_hardening.py` als
`UNAUTHORIZED-PATH`. State-Gate, strukturierter User-Gate-Record und
Commit-Binding waren vorhanden; die Pfadprojektion berücksichtigte aber nur
abgeschlossene Work-Units vom Typ `SLICE` und übersprang `CORRECTION`.

### Umsetzung und Nachweis

Abgeschlossene reguläre und Korrektur-Slices gelten identisch als Quelle
externer Pfadfreigaben, aber nur wenn State-Mirror, strukturierter Gate-Record,
exakter Fingerprint und Commit-Binding vollständig übereinstimmen.
Uncommittete, fremde oder nur im Mirror vorhandene Freigaben bleiben
fail-closed. Der Regressionstest bildet den Korrekturfall ab; zusätzlich
bestand der echte festgefahrene Preflight für die zuvor abgewiesenen Pfade.
Die vollständige Suite bestand mit `993 passed`.

## P2-FU-031 – `UNAUTHORIZED-PATH` ist kein entscheidbares Benutzergate

**Status:** Abschluss-Hotfix in 1.1C2 umgesetzt und auf `master` integriert

**Priorität:** kritisch

### Beobachtung und Ursache

Das Finalreview-Preflight lieferte bei fremden Pfaden den richtigen Fehlercode
und die exakte Pfadliste, der Workflow persistierte den Fall jedoch als
technisches `bootstrap_check/awaiting_resume`. Dieses Gate konnte mit
`--approve-gate` weder genehmigt noch abgelehnt werden und führte deshalb trotz
vollständiger Evidenz in einen manuellen Reparaturkreislauf.

### Umsetzung und Nachweis

Ausschließlich ein fingerprinttragendes `UNAUTHORIZED-PATH` mit nichtleerer
exakter Pfadliste wird als `UNEXPECTED_FILE/awaiting_user_decision`
gespeichert. Fehlende Voraussetzungen, Inputbudgets und alle anderen
Preflightfehler bleiben technische Bootstrap-Denials. Die Benutzerfreigabe
wird weiterhin durch strukturierten Gate-Record, exakten Fingerprint und
Preflight verifiziert. 46 fokussierte Tests und die vollständige Suite mit
`993 passed` bestätigen die Klassifikation.

## P2-FU-032 – Plan-Observations verlieren am Implementierungshandoff ihre Findingidentität

**Status:** offen nach Stabilisierungspaket 1.1D
**Priorität:** hoch
**Beobachtet in:** getrennten `PLAN_ONLY`-/`IMPLEMENT`-Läufen von 1.1C2 und
1.1D

### Beobachtung

Eine Reviewer-Observation darf einen Planreview nicht blockieren, bleibt aber
bis zu ihrer späteren Disposition ein reviewer-eigenes Finding. Innerhalb
eines einzelnen Laufs erzwingt der State-v3-Vertrag diese Eigentümerschaft.
Beim automatisch erzeugten Implementierungshandoff beginnt jedoch ein neuer
Run mit neuem State- und Recordkontext:

- Der Finding-Record bleibt im Audit des Planlaufs.
- Der Handoff bindet Arbeitsplan, Plancommit, Zielbranch, Taskscope und
  Slices, aber nicht die offenen Findings des Planreviews.
- Codex kann die Observation aus dem Plandokument berücksichtigen, muss aber
  keine strukturierte `FINDING_RESPONSE` für dieselbe Findingidentität liefern.
- Der ursprüngliche Reviewer muss sie im Implementierungslauf nicht mehr
  schließen oder eskalieren. Eine formal offene Observation kann dadurch
  historisch archiviert werden, während der Folgeauftrag ohne sie endet.

Die manuelle Übernahme einzelner Hinweise in einen Folgeauftrag ist kein
allgemeiner Findingvertrag.

### Zielvertrag

1. Offene Plan-Findings werden unveränderlich an Plancommit, Ursprungsrun,
   Reviewer und Handoff gebunden.
2. Der Implementierungslauf importiert sie idempotent mit unveränderter ID,
   Klasse, Beschreibung, Akzeptanzanforderung und Eigentümerschaft.
3. Jede Observation erhält einen spätesten Dispositionspunkt: konkreter Slice
   oder branchweiter Finalreview.
4. Codex antwortet, darf das Finding aber nicht schließen oder
   reklassifizieren. Nur der ursprüngliche Reviewer darf dies.
5. Ein positiver Antigravity-Finalreview bleibt ausgeschlossen, solange ein
   importiertes Finding beider Reviewer offen ist.
6. Fremde, nicht commitgebundene oder bereits geschlossene Findings werden
   nicht importiert; Resume und erneute Handoff-Auswertung erzeugen keine
   Dubletten.

### Erforderliche Regressionstests

- Claude öffnet im Planreview eine Observation und genehmigt den Plan; der
  erzeugte Handoff bindet genau diese Findingidentität an den Plancommit.
- Der Implementierungslauf verlangt Codex' Antwort und anschließend die
  Disposition durch Claude.
- Bleibt das Finding offen, sind Claude- und Antigravity-Finalfreigaben
  unmöglich.
- Watcher-Fortsetzung, Direkt-Resume und Handoff-Wiederholung importieren
  genau einen Record.
- Findings eines anderen Plans, Runs oder Reviewers werden fail-closed
  abgewiesen.

## P2-FU-033 – Parsebares Finding aus formal verworfenem Review verliert seine Dispositionspflicht

**Status:** offen nach Stabilisierungspaket 1.1D
**Priorität:** kritisch
**Beobachtet in:** Claude-Slice-Review von 1.1D, Work Unit 02

### Beobachtung

Eine physisch erfolgreiche Claude-Antwort enthielt ein formal korrektes neues
Blocker-Finding und zusätzlich die ungültige Zeile
`FINDING_STATUS: none reported by claude previously in this packet.`. Der
Orchestrator verwarf den gesamten Reviewvertrag zu Recht fail-closed. Er
persistierte Diagnostic, Outputdigest und Providerattempt, übernahm das
parsebare `NEW_FINDING` aber weder autoritativ noch als zu disponierenden
Kandidaten.

Nach einer lokalen Parserkorrektur änderte sich der Fingerprint. Der neue
Claude-Review begann mit leerem Ledger, verwendete dieselbe Finding-ID für
andere Hinweise und genehmigte den Slice. Ob die technische These des ersten
Blockers zutraf, ist für den Vertragsdefekt zweitrangig: Der zuständige
Reviewer musste sie nie bestätigen, als durch den neuen Diff behoben schließen
oder begründet verwerfen.

Damit kann ein konkreter Reviewerbefund durch einen sachfremden Syntaxfehler
und Fingerprintwechsel aus der verpflichtenden Disposition verschwinden. Das
native Agenten-JSON reduziert die Entstehungswahrscheinlichkeit solcher
Syntaxfehler, ersetzt aber nicht die erforderliche Failed-Output-Provenienz.

### Zielvertrag

1. Ein ungültiger Gesamtvertrag bewirkt weiterhin keine Freigabe und keine
   autoritative Findingtransition.
2. Der vollständige Rohoutput wird vor der Vertragsprüfung
   content-addressiert mit Reviewer, Operation, Inputdigest, Fingerprint,
   Providerabschluss und Vertragsdiagnose gebunden.
3. Eindeutig parsebare `NEW_FINDING`-Records werden als nicht autoritative,
   quarantänisierte Kandidaten mit reservierter Finding-ID persistiert.
4. Derselbe gespeicherte Output kann nach einer deterministischen
   Parserkorrektur ohne Provideraufruf erneut validiert werden.
5. Bei echtem Fingerprintwechsel muss derselbe Reviewer jeden Kandidaten
   bestätigen, schließen oder begründet verwerfen. Bis dahin sind
   ID-Wiederverwendung und positive Freigabe unzulässig.
6. Bestätigte Kandidaten werden genau einmal in das autoritative Ledger
   übernommen; freie Prosa, beschädigte Marker und fremde IDs bleiben reine
   Diagnose.

### Erforderliche Regressionstests

- Die exakt beobachtete Antwort bleibt als Reviewentscheid ungültig, bindet
  das syntaktisch vollständige `NEW_FINDING` aber als Kandidaten an Output und
  Fingerprint.
- Lokale Neuvalidierung desselben Outputs öffnet das Finding höchstens einmal
  und startet keinen Provider.
- Nach Fingerprintwechsel kann Claude nicht genehmigen oder dieselbe ID neu
  vergeben, bevor der Kandidat disponiert ist.
- Prozessabbruch, Watcher-Neustart und Resume bewahren genau einen Kandidaten.
- Freie Prosa, fremdes ID-Präfix und widersprüchliche Teilrecords erzeugen
  keinen Findingkandidaten.

## P2-FU-034 – Reklassifizierte Findings besitzen keinen nativen Akzeptanztest-Updatepfad

**Status:** konkreter Lauf lokal stabilisiert; nativer Vertrag offen
**Priorität:** hoch
**Beobachtet in:** Claude-Finalreview von 1.1D

### Beobachtung

Claude schloss `C-01`, reklassifizierte die vorhandene Observation `C-02` zum
Blocker, hielt sie offen und lehnte den Branch ab. Den nun gewünschten
fokussierten Befehl gab Claude als eigenständige `VALIDATE: [...]`-Zeile aus.
Der Providerprozess war erfolgreich, der Textvertrag verwarf den Output aber
mit `unknown state-v3 contract marker VALIDATE`.

`VALIDATE` ist derzeit nur als Akzeptanztest eines `NEW_FINDING` zulässig.
`FINDING_RECLASSIFIED` und `FINDING_STATUS` können den unveränderlichen
Akzeptanztest eines bestehenden Findings nicht versioniert ergänzen. Der
1.1D-Hotfix faltet genau eine syntaktisch gültige `VALIDATE`-Zeile nur bei
eindeutiger Bindung an ein eigenes offenes, gleichzeitig zum Blocker
reklassifiziertes Finding und eine negative Entscheidung in die Begründung.
Der alte Akzeptanztest bleibt autoritativ; mehrdeutige Formen bleiben
fail-closed. Das bringt den Realfall weiter, ersetzt aber keinen nativen
Updatevertrag.

### Zielvertrag

1. Der Eigentümer kann beim Reklassifizieren zum Blocker einen neuen
   Akzeptanztest strukturiert angeben.
2. Die Änderung wird als eigene Findingrevision mit altem und neuem Test,
   Reviewer, Fingerprint und Begründung persistiert.
3. Nur konfigurierte Kommandofamilien und negative Entscheidungen dürfen
   einen ausführbaren Test hinzufügen; ohne Update bleibt der alte Test
   autoritativ.
4. Der Matrixselektor verwendet ausschließlich die aktuelle autoritative
   Findingrevision.
5. Resume und Replay wenden die Revision genau einmal an; eigenständige oder
   mehrdeutige `VALIDATE`-Marker bleiben ungültig.

### Erforderliche Regressionstests

- Observation wird durch ihren Eigentümer zum Blocker reklassifiziert und
  ergänzt genau einen konfigurierten Matrixbefehl.
- Reklassifizierung ohne Update behält den bisherigen Prosetest.
- Fremder Reviewer, positive Entscheidung, nicht konfigurierte Befehle,
  Shellstrings und mehrere ungebundene Marker werden abgewiesen.
- Der gespeicherte 1.1D-Realoutput wird eng normalisiert, ohne Akzeptanztext,
  Findingstatus oder Verdict zu verändern.

## P2-FU-035 – Reviewrevision und technischer Providerretry besitzen keine eindeutige gemeinsame Identität

**Status:** permissiver Hotfix zurückgenommen; Architekturrest offen
**Priorität:** kritisch
**Beobachtet in:** Rücksprung zum Codex-Finalreview von 1.1D

### Beobachtung

Nach einem genehmigten Self-Hosting-Hotfix sprang der Finalreview korrekt zu
Codex zurück, weil der neue Repositoryfingerprint noch keinen Codex-Bericht
besaß. Der neue Prompt hatte einen neuen `input_digest`; als
`binding_fingerprint` wurde jedoch weiterhin der grobe, unveränderte
Task-Digest verwendet. Die Operations-ID aus Run, Work Unit, Rolle, Operation
und dieser Bindung war deshalb identisch mit dem abgeschlossenen alten
Finalreview. Die Bridge lehnte den Start korrekt mit `provider attempt
immutable binding differs from its first attempt` ab.

Ein erster Hotfix nahm den Input-Digest in die Operations-ID auf und erreichte
damit den Providerstart. Codex und Claude erkannten jedoch unabhängig den
entgegengesetzten Defekt: Jeder geänderte Digest begann nun unkontrolliert eine
neue Operation mit Attempt 1 und konnte dadurch den Zwei-Start-Deckel umgehen.
`C-03` blockierte den Branch. Die Abschlusskorrektur nahm diesen permissiven
Hotfix zurück und stellte die unveränderliche Inputbindung wieder her. Damit
ist der Retryvertrag wieder sicher, die Identität einer legitim neuen
Reviewrevision aber weiterhin nicht nativ modelliert.

### Zielvertrag

1. Der Orchestrator erzeugt eine explizite, unveränderliche semantische
   Reviewrevision, gebunden an den tatsächlich geprüften Repositoryfingerprint
   und den Workflowübergang.
2. Die logische Provideroperations-ID enthält diese Revision, nicht den
   Input-Digest als frei rotierbaren Resetmechanismus.
3. Innerhalb derselben Revision bleiben Input-Digest und übrige Bindings
   unveränderlich; Retry und Resume behalten Operations-ID und fortlaufende
   Attemptnummer.
4. Nur ein autorisierter neuer Reviewübergang erzeugt eine neue Revision mit
   Attempt 1. Transportpfade, erneute Messung oder technische Fehler dürfen
   dies nicht.
5. Alte Recordketten bleiben lesbar. Mehrdeutige alte und neue Identitäten
   werden fail-closed abgewiesen.
6. Replay und Projektion unterscheiden Revisionen und Attempts
   deterministisch; Attemptlimits gelten pro autorisierter Revision und
   identischem semantischem Input.

### Erforderliche Regressionstests

- Unveränderter Retry und Resume setzen dieselbe Operation mit nächster
  Attemptnummer fort; geänderter Input innerhalb derselben Revision stoppt.
- Fingerprintgebundener Rücksprung von Claude zu Codex erzeugt genau eine neue
  autorisierte Revision und erreicht den Providerstart.
- Nur ein anderer Input-Digest ohne neuen Übergangsrecord erzeugt keine neue
  Operation.
- Legacy-Identität wird bei vollständiger Übereinstimmung fortgesetzt;
  konkurrierende Identitäten bleiben fail-closed.
- Network- und Antigravity-Toolschema-Retries können ihr Limit nicht durch
  volatile Eingabekomponenten zurücksetzen.

## P2-DEC-001 – Stabilisierungspaket 1.1 vor weiterer Protokolloberfläche

**Status:** durch 1.1A bis 1.1C teilweise umgesetzt; Restumfang wird neu zugeschnitten

**Priorität:** kritisch  
**Einordnung:** Konsolidierung der Phase-1-/Phase-2-Zwischenarchitektur, keine
neue Fachfunktion

### Entscheidung und Begründung

Nach Arbeitspaket 1 soll nicht unmittelbar das nächste native JSON-Paket
beginnen. Die Fälle P2-FU-005 sowie P2-FU-010 bis P2-FU-017 zeigen ein
wiederkehrendes strukturelles Muster: Record-Kette, State-v3-Mirror,
Auditprojektion und Work-Unit-Zustand können zwischen zwei einzeln
erfolgreichen Schreib- oder Übergabeschritten auseinanderlaufen. Die
fail-closed-Prüfungen erkennen solche Zustände zwar, können sie aber noch nicht
in jedem Fall deterministisch und ohne manuelle Reparatur rekonstruieren.

Das ist kein bloßer Feinschliff. Solange Record-Kette und Mirror faktisch
gleichberechtigte Wahrheitsquellen sind, vergrößert jede zusätzliche
Protokolloberfläche die Zahl möglicher asymmetrischer Zwischenzustände. Deshalb
wird zwischen Arbeitspaket 1 und den weiteren nativen JSON-Arbeitspaketen ein
kleines, eigenständig review- und mergebares Stabilisierungspaket 1.1
eingeschoben.

### Zwischenstand nach Abschluss von 1.1A

1.1A hat den bewusst begrenzten, eigenständig nutzbaren Kern umgesetzt:

- eine validierte Recordkette als autoritative Eingabe eines reinen,
  unveränderlichen Replaykerns;
- eine gemeinsame deterministische Projektion für die angeschlossenen
  Structured-Resume- und Auditgrenzen;
- stabile, maschinenlesbare Diagnosen für fehlende, doppelte, typ-, run-,
  referenz- und fingerprintfremde Records sowie Mirrorabweichungen;
- eine frühe, nachweislich getrennte Legacy-Abzweigung;
- Cache-Rekonstruktion ohne Warnung beim erwarteten eigenen Append;
- atomare und idempotente Auditprojektion an den angeschlossenen Grenzen;
- deterministische Finalreview-Scope-Vereinigung und ein eng
  fingerprintgebundenes Gate für notwendige Finalreview-Hotfixes.

Ausdrücklich **nicht** abgeschlossen sind die vollständige Umstellung aller
fachlichen Liveübergänge auf record-first Transitionen, der Abbau sämtlicher
unabhängiger State-Schreibentscheidungen, die kompakte Betriebsoberfläche, die
übrigen Quota-/Providerkostenbremsen sowie die Wiederverwendung
pfadspezifischer Freigaben. Diese Restpunkte werden zusammen mit P2-FU-003,
P2-FU-013, P2-FU-018 sowie P2-FU-020 bis P2-FU-022 neu priorisiert und in
kleinere Pakete geschnitten.

### Zwischenstand nach Abschluss von 1.1B

1.1B hat den Reviewvertrags- und Reviewpaketanteil der Kostenbremsen umgesetzt:

- eindeutig lokale Reviewnormalisierung ohne zweiten Providerprozess;
- ausschließlich schrittvertraglich eindeutige Metadatenergänzung unter
  strikten Fail-closed-Grenzen;
- kanonische, inhaltsadressierte Basispakete für Slice- und
  Korrekturreviews;
- manifestgebundene Diff- und Workspace-Minimierung ohne Auditprosa und
  vollständige historische Reviewertexte;
- ein gemeinsamer Basisdigest für Claude und Antigravity bei unveränderter
  asymmetrischer Reviewreihenfolge;
- korrekt auf betroffene Findings begrenzte Same-Slice-Korrekturpakete ohne
  Fallback auf die vollständige Findingmenge.

Nicht Bestandteil von 1.1B waren branchweite Finalreview-Kompaktierung,
allgemeine Providertelemetrie, Quota-Wartebetrieb, Antigravity-Runtimefehler,
Codex-Defektkandidaten und die gemeinsame Watch-/Direkt-Finalisierung.

### Verbindliche Architekturziele

1. Die append-only Record-Kette wird als einzige fachliche Source of Truth
   festgelegt.
2. State, Audit und andere lesefreundliche Sichten werden ausschließlich
   deterministisch aus der Record-Kette projiziert. Sie dürfen keine
   eigenständige fachliche Entscheidung enthalten, die nicht aus Records
   reproduzierbar ist.
3. Entscheidungsrecord und fachlicher Zustandswechsel werden als eine
   replayfähige Transition behandelt. Ein Prozessabbruch an einer beliebigen
   Schreibgrenze darf weder eine zweite Entscheidung erzeugen noch eine
   bereits persistierte Entscheidung verlieren.
4. Unterbrochene Writes werden beim Start beziehungsweise Resume automatisch
   und eindeutig aus der Record-Kette rekonstruiert. Mehrdeutige oder
   beschädigte Ketten bleiben mit konkreter Diagnose fail-closed.
5. Structured-v1- und Legacy-state-v3-Pfade werden explizit getrennt. Ein
   Legacy-Lauf darf weder implizit strukturierte Recordinvarianten übernehmen
   noch durch synthetische, nicht persistierte Records wie ein Structured-Lauf
   erscheinen.
6. Standardlogging zeigt nur fachlich relevante Übergänge, aktive Rolle,
   Validierungsstatus, Gategrund, Resumezeitpunkt und Endergebnis. Cachetreffer,
   wiederholte Evidenzkompaktierung und technische Recorddetails erscheinen
   nur mit `--verbose` beziehungsweise auf Debug-Level.
7. Benutzerfreigaben für Pfade werden mit kanonischen pfadspezifischen
   Inhalts-/Diffdigests persistiert. Unveränderte externe Pfade dürfen über
   sachfremde Gesamtfingerprintänderungen hinweg wiederverwendet werden;
   geänderte Pfadinhalte bleiben zwingend erneut freigabepflichtig.

### Verbindliche Kostenbremsen

Der Lauf `20260819-152415Z` zeigte, dass technische Resumierbarkeit allein
nicht genügt: Innerhalb von ungefähr zwei Stunden verbrauchten wiederholte
Claude-Slice- und Finalreviews praktisch ein vollständiges Fünf-Stunden-
Kontingent. Einzelne Aufrufe übertrugen etwa 401.000 beziehungsweise 559.000
Zeichen und benötigten 20 beziehungsweise 27 interne Turns. Paket 1.1 muss
deshalb folgende Kostenregeln als ausführbare Verträge behandeln:

1. Pro Fingerprint wird die vollständige branchweite Evidenz höchstens einmal
   erzeugt und inhaltsadressiert wiederverwendet. Identische Resumes dürfen
   weder das Paket neu materialisieren noch denselben Reviewer erneut starten.
2. Korrekturreviews erhalten nur das fingerprintgebundene Korrekturdelta, die
   offenen Findings, ihre Akzeptanztests, die relevante Attestierung und einen
   kleinen unveränderlichen Kontextmanifest. Die vollständige Branch-Evidenz
   wird nicht erneut übertragen.
3. Verwaltete Auditprosa und bereits persistierte Reviewertexte werden nicht
   wiederholt als Modelleingabe eingebettet. Der Reviewer erhält strukturierte
   Records, ein Pfad-/Hashmanifest und gezielte relevante Hunks.
4. Formal reparierbare Antwortfehler wie fehlende oder falsch angeordnete
   Pflichtmarker werden lokal deterministisch normalisiert. Ein zweiter
   Claude-Aufruf ist nur zulässig, wenn die fachliche Bedeutung tatsächlich
   mehrdeutig ist; Grund und Zusatzkosten werden separat protokolliert.
5. Alle Codex-Defektkandidaten werden gemäß P2-FU-020 in genau einem
   Claude-Finalreview vollständig disponiert. Ein Kandidat darf nicht erst in
   einer späteren Vollrunde stillschweigend als neuer Blocker erscheinen.
6. Für jedes Arbeitspaket werden vor dem ersten Providerstart harte Budgets für
   Revieweraufrufe, übertragene Zeichen/Bytes, interne Turns und wiederholte
   Vollreviews festgelegt. Budgetüberschreitungen erzeugen eine typisierte,
   resumierbare Diagnose statt unkontrolliert weiterer Aufrufe.
7. Providertelemetrie weist tatsächliche Eingabe-, Ausgabe- und Turnmengen pro
   logischer Operation sowie kumuliert pro Arbeitspaket aus. Unbrauchbare
   Teilwerte wie die beobachtete Anzeige `input_tokens=6` dürfen nicht als
   Gesamtnutzung dargestellt werden.
8. Sonnet mit Effort `high` und die adversariale Prüftiefe bleiben erhalten;
   Einsparungen erfolgen durch kleinere, deduplizierte und strukturierte
   Evidenz sowie weniger Wiederholungsaufrufe, nicht durch eine stille
   Qualitätsabsenkung.

Die Abnahme verlangt zusätzlich einen synthetischen Lauf mit mindestens zwei
Korrekturrunden. Dabei darf pro eindeutigem Fingerprint höchstens ein
vollständiger Claude-Finalreview stattfinden, eine rein formale
Vertragsreparatur darf keinen Providerprozess starten, und die kumulierte
Budgetabrechnung muss aus den einzelnen Operationen exakt rekonstruierbar sein.

### Vorgeschlagener begrenzter Zuschnitt

Das Paket soll klein bleiben und keine neuen Agentenantwortschemata oder
Providerfunktionen einführen. Vorgesehen sind höchstens drei Slices:

1. **Autorität und Projektion:** Record-Autorität explizit machen; State- und
   Auditprojektion aus einer gemeinsamen deterministischen Replayfunktion
   ableiten; verbleibende unabhängige Mirror-Schreibpfade inventarisieren und
   beseitigen.
2. **Atomare/replayfähige Übergänge:** Entscheidung, Findingtransition,
   Attestierung, Gate, Commitbindung, Work-Unit-Wechsel und Completion über
   idempotente Transition-IDs und definierte Checkpoints rekonstruierbar
   machen.
3. **Recovery, Crash-Matrix und Betriebsoberfläche:** systematische
   Unterbrechungstests, Legacy-Abgrenzung, kompaktes Standardlogging und
   dokumentierter Diagnosemodus ergänzen.

Die genaue Dateigrenze muss in einem repository-grounded Arbeitsplan bestimmt
werden. Das Paket darf nicht als Gelegenheit für allgemeine Refactorings oder
den vorgezogenen nativen JSON-Cutover verwendet werden.

### Erforderliche Crash- und Replay-Matrix

Für jede fachliche Transition sind mindestens folgende Unterbrechungspunkte
synthetisch zu testen:

1. vor dem Recordappend;
2. nach Erstellung einer temporären Recorddatei, aber vor atomarer
   Veröffentlichung;
3. nach Recordveröffentlichung, aber vor Stateprojektion;
4. nach Stateprojektion, aber vor Auditprojektion;
5. nach Auditprojektion, aber vor dem nächsten Work-Unit- oder Agentenschritt;
6. während des Wechsels in eine Korrektur-Work-Unit;
7. während Commitbindung und Abschluss/Completion;
8. beim wiederholten Resume nach bereits vollständig persistierter
   Transition.

Jeder Fall muss nach Neustart entweder denselben eindeutigen Zustand ohne
zweiten Provideraufruf/Record rekonstruieren oder mit einer konkreten,
maschinenlesbaren Korruptionsdiagnose anhalten. Manuelle Änderungen an
`.orchestrator/state.json`, Auditdateien oder Recorddateien sind kein
zulässiger Recoverypfad.

### Abnahmekriterien für Paket 1.1

- Record-Replay erzeugt State und Audit deterministisch und idempotent.
- Kein normaler Resume hängt von der Reihenfolge unabhängiger Mirror- und
  Recordwrites ab.
- Korrekturübergang, Quota-/Instanzpause, Nutzer-Gate, Slice-Commit und
  Finalreview besitzen jeweils positive, negative und Crash-/Resume-Tests.
- Structured- und Legacy-Läufe verwenden nachweislich getrennte Invarianten.
- Standardlogs enthalten keine wiederholten Cache- oder
  Evidenzkompaktierungszeilen; `--verbose` bewahrt die vollständige technische
  Diagnose.
- Ein bereits genehmigter externer Pfad löst bei unverändertem
  pfadspezifischem Digest kein wiederholtes Gate aus; jede Inhaltsänderung,
  Umbenennung oder Herkunftsabweichung bleibt fail-closed und verlangt eine
  neue Freigabe.
- Mindestens drei aufeinanderfolgende repräsentative End-to-End-Läufe
  (Normalabschluss, Korrekturrunde und Unterbrechung/Resume) enden ohne
  manuelle State-, Record- oder Auditkorrektur.
- Erst nach erfüllter Abnahme beginnt das nächste Arbeitspaket zur Erweiterung
  der nativen Agenten-JSON-Schnittstellen.

### Persistenz nach Abschluss von Arbeitspaket 1

Nach erfolgreichem Abschluss des laufenden Arbeitspakets wird diese
Entscheidung in
`docs/internal/ORCHESTRATOR_ROADMAP_PHASE_2_PLUS.md` zwischen Arbeitspaket 1
und dem nächsten JSON-Paket eingeordnet. Dieses Dokument bleibt die einzige
persistente Detailgrundlage für Erkenntnisse, Kostenbremsen und den
Neuzuschnitt von Stabilisierung 1.1; dafür wird kein zweites paralleles
Stabilisierungsdokument angelegt. Aus den nach Abschluss von 1.1A neu
priorisierten Teilpaketen werden jeweils eigene ausführbare Inbox-Aufträge
abgeleitet.
