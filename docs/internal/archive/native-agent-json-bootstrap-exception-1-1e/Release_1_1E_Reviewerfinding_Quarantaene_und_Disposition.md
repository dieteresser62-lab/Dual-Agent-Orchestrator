# Release 1.1E – Reviewerfinding-Quarantäne und Dispositionspflicht

TARGET_BRANCH: feature/orchestrator-stabilization-1-1e

## Auftrag

Erstelle zunächst ausschließlich einen repository-grounded, unmittelbar
ausführbaren Arbeitsplan für P2-FU-033 aus
`docs/internal/phase-2-arbeitspaket-1-erkenntnisse-und-stabilisierung-1-1.md`.
Der Auftrag behandelt nur den Verlust eindeutig parsebarer Reviewer-Findings
aus einem insgesamt formal ungültigen Reviewvertrag. Andere offene Punkte des
Stabilisierungsrückstands werden nicht mitgezogen.

Dieser Lauf ist `PLAN_ONLY`: Produktcode und Produkttests bleiben unverändert.
Der Plan muss zuerst die heute tatsächlich verwendete Kette aus
Providerabschluss, Rohoutput-/Diagnosepersistenz, Reviewvertragsprüfung,
Finding-Ledger, Fingerprintwechsel, Reviewpaket und Resume im Repository
belegen. Historische Diagnoseformulierungen sind keine Erlaubnis, nicht mehr
vorhandene Pfade vorsorglich in den Scope aufzunehmen.

## Verbindlicher Ausgangspunkt

- Release 1.1D ist auf `master` integriert.
- Ein formal ungültiger Reviewvertrag bleibt eine Ablehnung. Er erzeugt weder
  eine Freigabe noch eine autoritative Findingtransition.
- Providerinput-Messung, Providerattempt, vollständiger Agentenoutput und die
  Vertragsdiagnose besitzen bereits persistierte Provenienz. Diese bestehende
  Evidenz ist wiederzuverwenden und nicht durch ein paralleles Log- oder
  State-System zu ersetzen.
- Reviewer-Findings unterliegen Eigentümerschaft: Claude disponiert nur `C-*`,
  Antigravity nur `A-*`. Codex darf Findingkandidaten weder bestätigen noch
  schließen.
- Beobachteter Realfall aus 1.1D: Eine physisch erfolgreiche Claude-Antwort
  enthielt ein syntaktisch vollständiges `NEW_FINDING` und zusätzlich den
  ungültigen Marker
  `FINDING_STATUS: none reported by claude previously in this packet.`. Der
  Gesamtvertrag wurde zu Recht verworfen; der parsebare Blocker verschwand
  jedoch nach Fingerprintwechsel aus der verpflichtenden Disposition.
- Die archivierte Lauf- und Korrekturevidenz liegt unter
  `docs/internal/archive/orchestrator-stabilization-1-1d/`. Sie dient als
  reproduzierbare Quelle, nicht als autoritativer Live-State.

## Zielzustand

Der Arbeitsplan beschreibt den kleinsten sicheren vertikalen Schritt zu
folgendem Verhalten:

1. Der vollständige Revieweroutput wird vor seiner Vertragsauswertung
   content-addressiert und unveränderlich an Run, Work Unit, Reviewer,
   Operation, Inputdigest, Reviewfingerprint, Providerabschluss und konkrete
   Vertragsdiagnose gebunden.
2. Scheitert der Gesamtvertrag, bleiben Freigabe und autoritative
   Findingtransition ausnahmslos verweigert.
3. Ausschließlich syntaktisch vollständige, eindeutig einem Reviewer gehörende
   `NEW_FINDING`-Records dürfen als nicht autoritative Findingkandidaten in
   Quarantäne persistiert werden. Kandidat, Quelle und reservierte Finding-ID
   bleiben nachvollziehbar miteinander verbunden.
4. Freie Prosa, beschädigte Marker, unvollständige Records, widersprüchliche
   Mehrfachdefinitionen, fremde ID-Präfixe und mehrdeutige Zuordnungen bleiben
   reine Diagnose und erzeugen keinen Kandidaten.
5. Derselbe gespeicherte Output kann nach einer deterministischen lokalen
   Parserkorrektur ohne neuen Providerstart erneut validiert werden. Replay,
   Resume und Watcher-Neustart erzeugen denselben Kandidaten höchstens einmal.
6. Ein Fingerprintwechsel hebt die Dispositionspflicht nicht auf. Vor einer
   positiven Freigabe oder Wiederverwendung der reservierten ID muss derselbe
   Reviewer den Kandidaten ausdrücklich bestätigen, als behoben schließen
   oder mit nachvollziehbarer Begründung verwerfen.
7. Bestätigung übernimmt den Kandidaten genau einmal in das autoritative
   Finding-Ledger. Schließen oder Verwerfen bleibt als dauerhafte, dem
   Kandidaten und Reviewer zugeordnete Entscheidung erhalten.
8. Offene Kandidaten werden dem zuständigen Reviewer in einem kompakten,
   fingerprintgebundenen Reviewkontext zur Disposition vorgelegt. Sie dürfen
   nicht durch einen sachfremden neuen Review, neue Findingnummern oder eine
   Korrekturrunde still überschrieben werden.
9. Crashgrenzen zwischen Outputpersistenz, Kandidatenableitung und
   Disposition sind fail-closed und idempotent. Ein durable-but-reported-failed
   Append wird über vorhandene Record-/Idempotenzmechanismen wiedergefunden.
10. Alte Artifact-Ketten ohne Quarantänekandidaten bleiben lesbar und
    fortsetzbar; es werden weder rückwirkend Kandidaten erfunden noch alte
    Freigaben neu bewertet.
11. Records, Standardlogs und Reviewpakete enthalten keine Prompts, Secrets,
    privaten Laufzeitpfade oder unnötige historische Volloutputs. Für die
    Disposition genügen Findingrecord, Provenienzdigests, Diagnose und eng
    begrenzte Quellevidenz.

## Harte Umfangsgrenze

- P2-FU-033 bleibt ein eigenständiges Paket.
- Höchstens zwei zusammenhängende, jeweils eigenständig implementier- und
  reviewbare Slices.
- Pro Slice höchstens sieben produktive Änderungsdateien; Tests und das
  automatisch abgeleitete Sliceprotokoll zählen nicht als produktive Dateien.
- Der Plan priorisiert einen record-first Quarantäne-/Dispositionsvertrag und
  deterministische Projektion aus Records. Ein neuer veränderlicher
  State-v3-Spiegel oder eine zweite Auditquelle ist unzulässig.
- Falls ein neuer strukturierter Recordtyp erforderlich ist, müssen
  Python-Modell, JSON-Schema, Parser/Replay und Writergrenze innerhalb
  desselben geschlossenen Slices synchron enthalten sein.
- Jeder produktive Pfad benötigt einen belegten aktuellen Aufrufer und einen
  konkreten Integrationsgrund.
- Passt der geschlossene Vertrag nicht in höchstens zwei Slices oder benötigt
  ein Slice mehr als sieben produktive Dateien, endet der Planungslauf mit
  `PLAN_READY: NO` und nennt den kleinsten nutzbaren Folgeschnitt. Der Scope
  wird nicht informell erweitert.

## Verbindliche Testplanung

Alle Tests verwenden gespeicherte synthetische Reviewerantworten, temporäre
Artifact-Stores, Fake-Adapter und injizierbare Fehler-/Crashgrenzen. Echte
Provideraufrufe, Netz, Zugangsdaten und reales Warten sind unzulässig.
Mindestens zu planen sind:

- exakt der beobachtete Claude-Fall: gültiges `NEW_FINDING` plus ungültiges
  `FINDING_STATUS`; Gesamtvertrag abgelehnt, genau ein quarantänisierter
  `C-*`-Kandidat an Outputdigest und Fingerprint gebunden;
- derselbe Fall für Antigravity mit korrekter `A-*`-Eigentümerschaft;
- lokales erneutes Validieren desselben gespeicherten Outputs öffnet oder
  übernimmt höchstens einmal und ruft keinen Provider auf;
- Prozessabbruch nach durablem Output, nach Kandidatenpersistenz und während
  der Disposition; Resume und Watcher-Neustart bewahren genau einen Kandidaten;
- Fingerprintwechsel: positive Freigabe und Wiederverwendung derselben ID
  bleiben bis zur expliziten Disposition gesperrt;
- Bestätigen, Schließen und begründetes Verwerfen durch den Eigentümer werden
  jeweils dauerhaft, idempotent und gegenseitig widerspruchsfrei abgebildet;
- Codex, Claude und Antigravity können keine fremden Kandidaten disponieren;
- freie Prosa mit `C-01`/`A-01`, falsches Präfix, beschädigte Pipefelder,
  doppelte IDs, widersprüchliche `NEW_FINDING`-Records und nur teilweise
  parsebare Marker erzeugen keinen Kandidaten;
- ein formal ungültiger Output ohne syntaktisch vollständiges Finding bleibt
  reine Diagnose;
- ein vollständig gültiger Reviewvertrag verwendet weiterhin ausschließlich
  den normalen autoritativen Findingpfad und erzeugt keine Doppelquarantäne;
- historische Ketten ohne Kandidaten bleiben byte-/semantisch stabil lesbar;
- Datenschutz-Sentinels aus Prompt, Secret und privaten Pfaden erscheinen
  weder in Kandidatenrecord, Standardlog noch kompaktem Dispositionspaket;
- vollständige Repository-Testmatrix ausschließlich durch den Orchestrator.

## Nichtziele

Nicht Bestandteil von 1.1E sind:

- P2-FU-035 zur Trennung von Reviewrevision und technischem Providerretry;
- P2-FU-021 zur gemeinsamen terminalen Finalisierung von Direkt- und
  Watchmodus;
- P2-FU-032 zu Plan-Observations am PLAN_ONLY-/IMPLEMENT-Handoff;
- P2-FU-034 zur nativen Akzeptanztestrevision bei Reklassifizierung;
- P2-FU-013 zu einer Betriebsentscheidung nach ausgeschöpften
  Antigravity-Retries;
- P2-FU-018 zu pfaddigestgebundenen Benutzergates;
- P2-FU-025 zur Finalreview-Evidenzkompaktierung;
- P2-FU-020 zum Codex-Defektkandidatenkanal;
- eine allgemeine Event-, Logging-, Quarantäne- oder Observability-Plattform;
- neue Reviewer, geänderte Reviewerreihenfolge oder die Umdeutung eines
  technischen Fehlers in eine Freigabe;
- natives Agenten-JSON als vollständiger Ersatz des bestehenden
  Reviewvertrags;
- Inbox-/Outboxabschluss, Archivierung, Push oder Merge.

## Stopbedingungen

Der Planungslauf hält kontrolliert und resumierbar an, wenn:

- ein syntaktisch vollständiger Findingrecord nicht ohne heuristische
  Auswertung freier Modellprosa isoliert werden kann;
- Outputdigest, Reviewer, Operation, Providerabschluss oder ursprünglicher
  Fingerprint an der Persistenzgrenze nicht stabil verfügbar sind;
- Kandidaten nur durch eine autoritative Findingtransition aus einem
  ungültigen Gesamtvertrag erzeugt werden könnten;
- eine positive Freigabe trotz offenem Kandidaten nicht vor dem nächsten
  Providerstart beziehungsweise Commit fail-closed verhindert werden kann;
- der Vertrag einen neuen Recordtyp benötigt, dessen Modell, Schema, Replay
  und Writer nicht innerhalb desselben Slices synchronisiert werden können;
- ein notwendiger produktiver Pfad im späteren exakten Slice-Scope fehlt;
- die harte Slice- oder Produktivdateigrenze überschritten würde.

Agenten führen keine vollständige Validierungsmatrix aus und emittieren kein
`VALIDATION_RESULT`.

## Erwartete Ausgabe

Der Plan wird unter
`docs/internal/release-1-1e-reviewerfinding-quarantaene-und-disposition-arbeitsplan.md`
erstellt und erfüllt den kanonischen `PLAN_ONLY`-Handoff-Vertrag.
