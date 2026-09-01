# S4a — Implementierungsauftrag: Recordlücken sortieren und die zwei dringenden schließen

Auftrag an: Codex
Review durch: Claude (read-only)
Branch: `feature/state-authority-consolidation`, Vorgänger: der S3-Commit
Übergeordneter Plan: `inbox/backlog/00-stabilisierung-arbeitsplan.md`
Grundlage: `docs/internal/stabilisierung-s2-uebergangsmatrix.md`,
Abschnitt „State-Fakten ohne vollständigen Record“

**Ohne Orchestrator.** Testsuite unter WSL: `python3 -m pytest tests/ -v`.

## Warum dieser Slice geteilt ist

Die Matrix weist rund 35 STOP-Einträge aus. Sie alle in einem Slice zu
recorden ergäbe einen Diff, der nicht mehr unabhängig reviewbar wäre — und
niemand weiß heute, wie viele davon überhaupt einen Record brauchen.

Dieser Slice erzeugt deshalb die Entscheidungsgrundlage und schließt nur die
zwei Lücken, die unabhängig vom Cutover dringend sind. Der Schnitt der
verbleibenden Records folgt danach aus dem Ergebnis.

## Teil 1 — Sortierung (Dokument)

Jeder STOP-Eintrag der Matrix wird genau einer Gruppe zugeordnet:

- **A · braucht Record** — der Fakt ist normativ, wird gelesen und muss in die
  Recordkette. Zu benennen: welcher Recordtyp, welches Feld, welcher Schreiber.
- **B · kann als nicht-normativ entfallen** — der Fakt wird nicht mehr für eine
  Entscheidung gelesen oder ist reine Projektionsmetadatum. Zu belegen: **wer
  ihn heute liest** und warum dieser Leser ohne ihn auskommt. Eine Behauptung
  ohne benannten Leser genügt nicht.
- **C · bereits ableitbar, Matrix zu korrigieren** — die S2-Einstufung war zu
  streng; der Weg aus dem Recordpräfix ist zu benennen.

Kandidaten für B sind erfahrungsgemäß Zeitstempel und Anzeigefelder
(`created_at`, `updated_at`, `gate_decisions[*].decided_at`). Das ist eine
Vermutung, kein Ergebnis — die Zuordnung ist je Eintrag zu begründen.

Das Ergebnis wird als neuer Abschnitt in die S2-Matrix geschrieben, nicht in
ein zweites Dokument. Die Matrix bleibt die eine Inventur.

## Teil 2 — Die zwei dringenden Lücken schließen

Diese beiden sind unabhängig vom Cutover Defekte im heutigen Betrieb:

### 2.1 `ContractResult.red_state_followup_slice`

Der Fakt hat keinen Record und **autorisiert einen Git-Commit im Red State**
(`audit_trail`, `commit_slice()`). Eine Commit-Autorisierung, die nur im Mirror
existiert, ist bei Mirrorverlust nicht rekonstruierbar und bei Mirrordivergenz
nicht überprüfbar.

Er gehört als eigener Reviewfakt in `ReviewPayload` oder einen daran
gebundenen Record. Die Autorisierung muss aus dem Recordpräfix allein
nachweisbar sein.

### 2.2 `ContractResult.evidence.*`

`review_payload()` verbindet `dimensions`, `largest_residual_risk` und
`break_condition` mit `" | "`, ohne das Trennzeichen im Inhalt auszuschließen.
Enthält ein Feld selbst ein `" | "`, ist die Zerlegung mehrdeutig — das ist
verlustbehaftete Serialisierung von Reviewevidenz.

Die drei Felder werden strukturiert recordet. Ein Test muss zeigen, dass ein
Inhalt mit eingebettetem `" | "` verlustfrei durch Schreiben und Lesen läuft.

**Bestandsdaten:** Falls bereits verkettete Evidenz in persistierten Records
existiert, ist zu entscheiden und zu begründen, ob sie migriert, als
Altformat gelesen oder fail-closed abgewiesen wird. Kein stilles Umdeuten.

## Teil 3 — Schnittvorschlag (Dokument, kein Code)

Ein Vorschlag, wie die verbleibenden Gruppe-A-Einträge auf Folgeslices
verteilt werden. Vorgeschlagene Bündelung nach fachlicher Nähe, nicht nach
Anzahl — etwa: Cursor und Work-Unit-Status; Slice-Startgrenze und
Scopefingerprint; Side-effect-Ledger; Gate-Transitionen; Failure-/Retry-Policy;
Protokoll- und Profilbindung; Review-Contractfelder; Blob-/Contentauthority.

Je Bündel: welche Einträge, welche Module, welche Reihenfolgeabhängigkeit.

## Abnahme

- Jeder STOP-Eintrag ist genau einer Gruppe A, B oder C zugeordnet und
  begründet. Für B ist der heutige Leser benannt.
- `red_state_followup_slice` ist aus dem Recordpräfix allein nachweisbar; ein
  Test zeigt, dass ein Red-State-Commit ohne diesen Record nicht autorisiert
  werden kann.
- Die drei Evidence-Felder sind strukturiert recordet; ein Test beweist
  Verlustfreiheit mit eingebettetem `" | "`.
- Die Entscheidung zu vorhandenen Bestandsdaten ist umgesetzt und begründet.
- Die Matrix ist um Sortierung und Schnittvorschlag erweitert; ihr
  Vollständigkeitstest bleibt grün.
- Suite grün gegen die Baseline nach S3.

## Nicht-Ziele

- **Kein Cutover.** Der Mirror wird weiterhin geschrieben; das ist S4b.
- Keine weiteren STOP-Einträge schließen — nur die zwei genannten.
- Keine Protokollversion anheben; falls die Evidence-Änderung eine
  Schemaanpassung verlangt, ist das zu benennen und die Entscheidung
  vorzulegen, nicht selbst zu treffen.
- Keine neuen `_recoverable_*`-Sonderfälle.

## Stopbedingungen

- Anhalten, wenn ein Eintrag in keine der drei Gruppen passt — das ist ein
  eigener Befund.
- Anhalten, wenn `red_state_followup_slice` ohne Schemaänderung nicht
  recordfähig ist; dann Entscheidung vorlegen.
- Anhalten, wenn sich zeigt, dass Bestandsrecords bereits mehrdeutige
  Evidenz enthalten — das wäre ein bereits eingetretener Datenverlust und
  eine Entscheidung, keine Implementierung.

## Reviewfokus (Claude)

- Gruppe-B-Zuordnungen ohne benannten Leser oder mit einem Leser, der den Fakt
  tatsächlich noch für eine Entscheidung braucht.
- Gruppe C: behauptete Ableitbarkeit ohne konkreten Weg aus dem Recordpräfix.
- Ob die Red-State-Autorisierung wirklich aus Records allein prüfbar ist oder
  weiterhin den Mirror braucht.
- Ob der Verlustfreiheitstest den echten Schreib-/Lesepfad nutzt oder nur die
  Serialisierungsfunktion isoliert prüft.
- Ob Teil 2 versehentlich schon Cutover-Verhalten einführt.
