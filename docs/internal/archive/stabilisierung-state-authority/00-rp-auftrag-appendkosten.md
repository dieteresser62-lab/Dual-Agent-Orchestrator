# RP — Implementierungsauftrag: Appendkosten der Recordkette linearisieren

Auftrag an: Codex
Review durch: Claude (read-only)
Branch: `feature/state-authority-consolidation`, Vorgänger: der R3-Commit
Übergeordneter Plan: `00-stabilisierung-arbeitsplan.md`, Slice RP

**Ohne Orchestrator.** Testsuite unter WSL: `python3 -m pytest tests/ -v`.
Baseline nach R3 (`5334248`): **1257 passed in 492 s**.

## Befund

`ArtifactBridge.append()` ruft bei jedem Append `self.store.load_chain()`.
`ArtifactStore._load_chain()` scannt das Recordverzeichnis, liest jede Datei und
validiert sie vollständig — ohne Cache. Das Anlegen von N Records kostet damit
N Verzeichnisscans und rund N²/2 Dateilesungen mit voller Schemavalidierung.

**Es sind zwei Vollscans je Append, nicht einer.** Beide Stellen sind zu
beheben; wird nur eine behoben, bleibt die Quadratik mit halbiertem Faktor:

| Stelle | Zweck |
|---|---|
| `ArtifactBridge.append()` → `store.load_chain()` | Idempotenzprüfung und höchste Revision je `(record_type, logical_id)` |
| `ArtifactStore.put()` → `self._load_chain(expected_cache_chain=chain)` | Nachprüfung und Cacheauffrischung nach dem Publizieren |

Die ersten beiden Zwecke sind Indexfragen und brauchen die vollständige Kette
nicht. Der dritte ist eine Verifikation — hier ist zu entscheiden und zu
begründen, ob sie den Vollscan wirklich braucht oder ob der soeben geschriebene
Record allein zu prüfen genügt.

## Belege

Gemessen am 30. August 2026 auf `30b51cf`:

- Die Suitelaufzeit sprang nach R2 von 190 s auf 411 s, obwohl R2 nur rund 22 s
  eigene Tests mitbringt. R2 schreibt Transitionrecords an jeder Kante — teurer
  wurde der Bestand, nicht der Zuwachs.
- `--durations=25` weist 37 % der Gesamtzeit in fünf Tests von
  `test_workflow_transition_matrix.py` aus, die vollständige Journeys mehrfach
  durchspielen.
- Die 19 vorhandenen Läufe schreiben median 14, maximal 124 Records. Bei 124
  Records sind das rund 7.700 Dateilesungen; nach R2 und dem Side-effect-Ledger
  aus Bündel 4 ist ein Vielfaches zu erwarten.

### Ausgangsmessung für die Abnahme

Profillauf am 30. August 2026 auf `5334248`, Einzeltest
`test_workflow_transition_matrix.py::test_replay_and_carry_forward_mutations_turn_matrix_cases_red`:

| Funktion | kumulativ | Aufrufe |
|---|---:|---:|
| `_load_chain` | 152,8 s von 158,2 s Gesamtzeit = **96,6 %** | 1.072 |
| `_read_record` | 144,7 s | **9.396** |
| `append` | 116,3 s | 260 |
| `put` | 78,7 s | 260 |

260 Appends verursachen 9.396 Recordlesungen, also im Schnitt 36 pro Append —
die mittlere Kettenlänge. Von den 1.072 Scans entfallen 520 auf die zwei
Appendpfade; die übrigen 552 sind ausdrückliche `load_chain()`-Aufrufe aus
Replay-, Resume- und Testcode.

**Realistischer Erwartungswert:** Dieser Slice beseitigt die 520
appendgetriebenen Scans. Die 552 ausdrücklichen Scans bleiben und behalten ihre
volle Validierung — sie zu reduzieren wäre ein anderer Slice und würde einen
Cache mit Validierungsanspruch verlangen. Produktivläufe profitieren stärker
als dieser Test, weil sie die Kette nicht nach jedem Schritt zur Inspektion
neu laden.

## Ziel

Das Anlegen von N Records ist nicht mehr quadratisch. Die Kette bleibt
vollständig validiert und fail-closed.

## Die eine Gefahr, an der dieser Slice scheitern kann

Ein Index ist ein Cache. Ein Cache mit Autorität ist genau der Defekt, den
dieser gesamte Plan beseitigt. Der Index darf deshalb **niemals** die Quelle
einer Entscheidung sein:

- Fehlt er, wird er aus den Records neu gebaut.
- Weicht er ab, wird er verworfen und neu gebaut — nicht der Record korrigiert.
- Er wird nie repariert, ergänzt oder als Beleg zitiert.

`CLAUDE.md` führt `head.json` bereits als rekonstruierbaren Cache. Ob der Index
dort andockt oder eigenständig ist, entscheidet der Slice.

## Anforderungen

1. Kein Append liest mehr die vollständige Kette, um Idempotenz oder Revision
   zu bestimmen.
2. Die Validierungsabdeckung schrumpft nicht. Kein Append akzeptiert etwas, das
   der heutige Vollscan abgewiesen hätte — insbesondere nicht Ketten-,
   Referenz- oder Revisionsverletzungen.
3. Der Index ist rein abgeleitet, jederzeit verwerfbar und jederzeit aus den
   Records rekonstruierbar.
4. Resume bleibt fail-closed. Ein fehlender oder abweichender Index ist ein
   Grund zum Neubau, niemals zum Fortfahren mit unklarem Zustand.
5. Der durable-but-reported-failed-Pfad in `append()` bleibt wirksam: Ein
   Record, der trotz gemeldetem Fehler dauerhaft geschrieben wurde, wird
   weiterhin gefunden.
6. Kein Cutover. Der Mirror wird unverändert weiter geschrieben.

## Providerfreie Akzeptanzfälle

- **Eine Messung über wachsende Kettenlängen**, nicht eine Behauptung: Die
  Kosten für N Appends wachsen für mindestens drei deutlich verschiedene N
  nachweislich nicht quadratisch. Der Test muss so geschrieben sein, dass er
  bei einer Rückkehr zum Vollscan rot wird.
- Ein verworfener, gelöschter oder manipulierter Index führt zu identischem
  Verhalten wie zuvor — er wird neu gebaut, nicht geglaubt.
- Ein Record, dessen Index-Eintrag von der Kette abweicht, wird fail-closed
  behandelt; die Records gewinnen.
- Jede heute abgewiesene Kettenverletzung wird weiterhin abgewiesen. Ein
  Mutationstest gegen die Prüfungen wird rot.
- Wiederholtes Append mit gleichem `idempotency_key` liefert weiterhin denselben
  Record, auch nach Indexverlust.

## Abnahme

- Die Nichtquadratik ist gemessen, nicht behauptet.
- Der Index besitzt nachweislich keine Autorität.
- Keine Prüfung ist entfallen, um die Beschleunigung zu erreichen.
- Die Suitelaufzeit ist gegenüber R3 (1257 passed in 492 s) messbar gesunken;
  die Zahl wird festgehalten. Der Profillauf des oben genannten Einzeltests
  wird wiederholt und gegen die Ausgangsmessung gestellt.
- Keine Protokoll-, Schema- oder Registerversion angehoben.
- Die Providernamen-Baseline ist unverändert.
- Keine neuen `_recoverable_*`-Sonderfälle.
- Suite grün gegen die Baseline nach R3: 1257 passed.

## Nicht-Ziele

- Kein Cutover — das bleibt S4b.
- Keine weiteren Recordbündel.
- Keine neue Laufzeitabhängigkeit; das Projekt führt bewusst `dependencies = []`.
- Keine Reduktion von Tests als Ersatz für die eigentliche Behebung.

## Stopbedingungen

- Anhalten, wenn die Beschleunigung nur durch Weglassen einer heute wirksamen
  Prüfung erreichbar wäre.
- Anhalten, wenn der Index zwingend persistiert werden müsste, um korrekt zu
  sein — dann wäre er keine Ableitung mehr, sondern eine zweite Wahrheit.
- Anhalten, wenn die Nichtquadratik nicht messbar belegt werden kann.

## Reviewfokus (Claude)

- Ob der Index wirklich keine Autorität hat: eine Stelle, die ihm glaubt statt
  ihn zu prüfen, wäre derselbe Fehler in neuem Gewand.
- Ob die Messung echt ist — wachsende N und gemessene Kosten — oder nur ein
  Test, der Aufrufzähler prüft und bei einer anderen Implementierung derselben
  Quadratik grün bliebe.
- Ob eine Validierung stillschweigend entfallen ist, um den Vollscan zu
  vermeiden.
- Ob der durable-but-reported-failed-Pfad noch trägt.
- Ob der Index bei parallelem oder abgebrochenem Schreiben veralten kann, ohne
  dass es auffällt.
