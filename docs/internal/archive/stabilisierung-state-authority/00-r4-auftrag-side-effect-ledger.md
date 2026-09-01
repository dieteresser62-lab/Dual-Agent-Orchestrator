# R4 — Implementierungsauftrag: Side-effect-Ledger

Auftrag an: Codex
Review durch: Claude (read-only)
Branch: `feature/state-authority-consolidation`, Vorgänger: `ac030fe` (RP)
Übergeordneter Plan: `00-stabilisierung-arbeitsplan.md`
Grundlage: `docs/internal/stabilisierung-s2-uebergangsmatrix.md`, Bündel 4 des
S4a-Schnittvorschlags

**Ohne Orchestrator.** Testsuite unter WSL: `python3 -m pytest tests/ -v`.
Baseline nach RP (`ac030fe`): **1264 passed in 279 s**.

## Stehende Regeln

Unverändert gültig seit R2, hier nicht erneut zu begründen:

1. **Keine Rückwärtskompatibilität.** Ketten ohne die neuen Records werden
   fail-closed abgewiesen.
2. **Die Providernamen-Ratsche wird nicht angehoben.** Recordfelder werden
   rollenbasiert benannt.
3. **Kein Cutover.** Der Mirror wird weiter geschrieben.
4. **Kein Cache mit Autorität.** RP hat den Maßstab gesetzt: Ein abgeleiteter
   Index wird bei Abweichung verworfen, nie geglaubt und nie repariert.

## Umfang — ein Fakt, aber der dichteste des Plans

| Statefeld | Zielrecord und Feld |
|---|---|
| `work_units[*].completed_side_effects` | `SideEffectPayload.effect_key/phase/result` |

Schreiber laut Sortierung: der jeweilige Side-effect-Wrapper **vor und nach**
jeder Git-, Provider-, Datei- und Queueoperation. Resume und Idempotenz lesen
das Ledger.

Dieses Bündel liefert laut Schnittvorschlag „anschließend die
Crashfenster-Reconciliation für S4b“. Es ist damit nicht nur ein weiterer
Recordtyp, sondern die Grundlage, auf der der Cutover überhaupt entscheidbar
wird.

## Der eigentliche Inhalt: das Fenster zwischen Intent und Resultat

Ein Record vor der Operation und einer danach erzeugen genau ein Fenster: Der
Intent steht in der Kette, das Resultat fehlt. Beim Resume ist dann unbekannt,
ob die Operation stattgefunden hat.

**Für jede Side-effect-Klasse ist die Reconciliation-Regel zu benennen und zu
implementieren.** Sie beantwortet: Wie stellt der Resume lokal und
providerfrei fest, ob die Operation eingetreten ist? Mindestens zu behandeln:

| Klasse | zu beantworten |
|---|---|
| Git-Commit | Existiert der Commit? Trägt er den erwarteten Baum? |
| Providerstart | Liegt eine dauerhaft persistierte Antwort vor? Darf ein zweiter Start entstehen? |
| Dateischreibung | Ist die Zieldatei vorhanden und inhaltsgebunden identisch? |
| Queuebewegung | Liegt die Aufgabe in Inbox, `done` oder `failed`? |

Eine Klasse ohne benannte Regel ist ein Stopgrund, keine offene Stelle. Wo eine
Regel den Zustand nicht sicher feststellen kann, ist das ausdrücklich als
fail-closed zu modellieren — nicht als Annahme.

## Anforderungen

1. `SideEffectPayload` additiv in Schema 2; `schema_version` bleibt `2`.
2. Intent und Resultat sind unterscheidbare Phasen desselben `effect_key`, nicht
   zwei unabhängige Records.
3. Der Intent liegt **vor** der Operation, das Resultat **nach** ihr. Der
   Ordnungsnachweis geht gegen die tatsächliche Operation, nicht gegen den
   nächsten Dispatch.
4. Ein `effect_key` ist stabil über Resume hinweg: Derselbe logische Seiteneffekt
   erhält nach Unterbrechung denselben Schlüssel und wird nicht doppelt
   ausgeführt.
5. Ein Replay des Präfixes rekonstruiert das Ledger vollständig, ohne Zugriff
   auf `state.json`, Checkpoint oder Dateisystem — die Reconciliation selbst
   darf das Dateisystem und Git lesen, das Ledger nicht.
6. Neue Record-/Mirror-Vergleiche werden in der Matrix inventarisiert und im
   Vollständigkeitstest gezählt.
7. Der STOP-Eintrag wird in der Matrix auf gedeckt fortgeschrieben, ergänzt um
   die Reconciliation-Regeln je Klasse.

## Providerfreie Akzeptanzfälle

- Für jede Side-effect-Klasse: Abbruch **zwischen** Intent und Resultat; der
  Resume stellt den tatsächlichen Zustand fest und führt die Operation genau
  dann aus, wenn sie nachweislich nicht stattgefunden hat.
- Derselbe Abbruch zweimal hintereinander erzeugt keinen zweiten Commit, keinen
  zweiten Providerstart, keine zweite Queuebewegung.
- Ein Intent ohne feststellbares Resultat führt fail-closed zum Halt, nicht zu
  einer Annahme in eine der beiden Richtungen.
- Ein Resultat ohne vorhergehenden Intent wird abgewiesen.
- Ein manipulierter `effect_key` wird abgewiesen.
- Eine Kette ohne Ledgerrecords wird fail-closed abgewiesen.

## Abnahme

- Jede Side-effect-Klasse hat eine benannte, implementierte und getestete
  Reconciliation-Regel.
- Kein Abbruchpunkt führt zu doppelter Ausführung oder stillschweigender
  Annahme.
- Das Ledger ist aus dem Recordpräfix allein rekonstruierbar.
- Die Providernamen-Baseline ist unverändert.
- Keine Protokoll-, Schema- oder Registerversion angehoben.
- Keine neuen `_recoverable_*`-Sonderfälle.
- Suite grün gegen die Baseline nach RP: 1264 passed.
- **Die Suitelaufzeit wird festgehalten.** Dieses Bündel ist das dichteste des
  Plans; RP hat die Quadratik beseitigt, aber die Recordzahl steigt hier am
  stärksten. Ein erneuter Anstieg wäre ein Befund, kein Nebeneffekt.

## Nicht-Ziele

- Kein Cutover — das bleibt S4b.
- Keine Crash-Injection-Infrastruktur — das ist S5. Hier genügen gezielte
  Abbruchtests je Klasse.
- Keine Gate-Records — Bündel 5.
- Keine Reparatur der R1-Nachzüge.

## Stopbedingungen

- Anhalten, wenn für eine Side-effect-Klasse keine lokale, providerfreie
  Feststellung möglich ist. Dann ist zu benennen, welche Information fehlt.
- Anhalten, wenn ein stabiler `effect_key` nur durch Zeitstempel, Zufall oder
  Pfadreihenfolge bildbar wäre.
- Anhalten, wenn die Reconciliation den Mirror braucht, um zu entscheiden.

## Reviewfokus (Claude)

- Ob jede Klasse eine echte Regel hat oder ob eine davon stillschweigend
  annimmt, die Operation sei erfolgt.
- Ob der `effect_key` über Resume wirklich stabil ist — Zeitstempel oder
  Iterationszähler im Schlüssel wären ein Defekt.
- Ob der Intent vor der Operation liegt und nicht bloß vor dem Commit.
- Ob die Reconciliation providerfrei bleibt und keinen zweiten Providerstart
  als Feststellungsmittel benutzt.
- Ob die Suitelaufzeit gehalten wurde.
