# R8 — Implementierungsauftrag: Blob- und Contentauthority

Auftrag an: Codex
Review durch: Claude (read-only)
Branch: `feature/state-authority-consolidation`, Vorgänger: `ebabb00` (R6)
Übergeordneter Plan: `00-stabilisierung-arbeitsplan.md`
Grundlage: `docs/internal/stabilisierung-s2-uebergangsmatrix.md`, Bündel 8 des
S4a-Schnittvorschlags

**Ohne Orchestrator.** Testsuite unter WSL: `python3 -m pytest tests/ -v`.
Baseline nach R6 (`ebabb00`): **1315 passed in 160 s**.

## Warum Bündel 8 vor Bündel 7 läuft

Der Schnittvorschlag sagt über Bündel 7: „Validationbindung benötigt Bündel 8."
Und über Bündel 8: „Muss vor dem Abschluss von Bündel 7 liegen." Die
Nummerierung ist damit nicht die Ausführungsreihenfolge. Liefe Bündel 7 zuerst,
müsste es für die Validationbindung ein zweites Mal geöffnet werden.

Die Bündelnummern bleiben wie in der Matrix, nur die Reihenfolge dreht sich.

## Stehende Regeln

Unverändert gültig seit R2:

1. **Keine Rückwärtskompatibilität.** Ketten ohne die neuen Records werden
   fail-closed abgewiesen.
2. **Die Providernamen-Ratsche wird nicht angehoben.** Recordfelder rollenbasiert.
3. **Kein Cutover.** Der Mirror wird weiter geschrieben.
4. **Kein Cache mit Autorität.**

## Umfang — vier Fakten der Gruppe A

| Statefeld | Zielrecord und Feld |
|---|---|
| `ValidationRecord.output` | `ValidationContentPayload.command/result_record_id/output_bytes` |
| `ValidationAttestation.output_digest` | `ValidationContentPayload.raw_stdout/raw_stderr` plus `ValidationAttestationPayload.output_digest` |
| `runtime_history.codex_final_report` und weitere rohe Agenttexte | `ProviderContentPayload.response_sha256/content_bytes/content_kind` |
| `runtime_history.active_review_packet` | `ReviewPacketPayload.fingerprint/manifest/content_bytes` |

Module laut Schnittvorschlag: `validation_matrix.py`, `review_packets.py`,
`agent_runtime.py`, `orchestrator.py`, Store, Schema, Replay.

## Die zentrale Spannung dieses Slice

**R6 hat gerade entschieden, dass Providerrohtext nicht in die Recordkette
gehört.** `provider_text` darf dort nur noch als Redaktionsmarker mit Digest
und Bytelänge stehen; das Schema weist alles andere ab.

Bündel 8 will `raw_stdout`, `raw_stderr` und `content_bytes` recorden — also
genau Rohinhalte. Das ist nicht automatisch ein Widerspruch, aber es ist einer,
bis er begründet aufgelöst ist. Die Auflösung ist der Kern dieses Slice, nicht
eine Randbemerkung.

**Zu unterscheiden und je Fakt zu entscheiden:**

- **Validierungsausgabe** ist Ausgabe der *eigenen* Testsuite, kein
  Providerinhalt. Sie enthält aber Pfade, Umgebungsdetails und potenziell
  Inhalte aus dem Repository.
- **`codex_final_report` und rohe Agenttexte** sind Providerinhalt. Für sie
  gilt die R6-Entscheidung dem Sinn nach — oder es ist zu begründen, warum
  nicht.
- **Reviewpakete** sind lokal erzeugt, können aber Diffinhalte und damit
  Repositorybytes tragen.

Für jeden der vier Fakten ist zu benennen: Rohbytes, redigiert, gekürzt, oder
Digest plus externer Blob. Eine pauschale Antwort für alle vier ist die falsche
Antwort.

## Die zweite Spannung: Größe gegen RP

Die Matrix hält fest, dass `ValidationAttestation.output_digest` heute
**ungekürzte** Ausgaben bindet und aus den Digests der kompakten Recordausgaben
nicht ableitbar ist. Genau daran hängt die Frage.

RP hat die Lesekosten der Kette linearisiert, indem Appends nicht mehr die
ganze Kette scannen. Große Blobs *in* den Records würden die Kosten auf anderem
Weg zurückholen: Jeder Vollscan — und die gibt es in Replay, Resume und
Reviewaufbau weiterhin — liest und validiert dann Megabytes statt Kilobytes.

Backlog `02` beziffert die Größenordnung aus einem realen Lauf: rund 653.000
Zeichen ursprüngliche und 429.000 Zeichen kompaktierte Evidenz, pro Erhebung.

**Deshalb ist zu entscheiden und mit einer Messung zu belegen:** Bleiben die
Bytes in der Recorddatei, oder trägt der Record einen inhaltsgebundenen Verweis
auf einen separat abgelegten Blob? Ein Verweis ist kein Cache — er ist Teil des
Records, wenn er inhaltsgebunden ist und sein Ziel fail-closed geprüft wird.

## Anforderungen

1. Die betroffenen Payloads additiv in Schema 2; `schema_version` bleibt `2`.
2. Je Fakt ist die Inhaltsentscheidung getroffen, begründet und getestet.
3. Der Digest bindet weiterhin genau das, was er heute bindet. Wird
   ungekürzte Ausgabe durch gekürzte ersetzt, ist der Digest entsprechend neu
   zu definieren — und die Änderung ausdrücklich zu benennen, nicht
   stillschweigend zu übernehmen.
4. Ein Replay des Präfixes rekonstruiert alle vier Fakten ohne `state.json`,
   Checkpoint oder Mirror. Ein ausgelagerter Blob darf dabei gelesen werden,
   sein Fehlen oder Abweichen ist fail-closed.
5. Eine Kette ohne die neuen Records wird fail-closed abgewiesen.
6. Neue Record-/Mirror-Vergleiche werden in der Matrix inventarisiert und
   gezählt.
7. Die STOP-Einträge werden fortgeschrieben.

## Providerfreie Akzeptanzfälle

- Für jeden der vier Fakten belegt ein Test die gewählte Inhaltsform —
  Rohbytes, Redaktion, Kürzung oder Verweis — statt sie zu beschreiben.
- Ein manipulierter oder fehlender ausgelagerter Blob wird fail-closed erkannt;
  der Record gewinnt nie gegen seinen Inhalt und der Inhalt nie gegen den
  Record.
- Ein Reviewpaket mit großem Diff erzeugt keine Kette, deren Vollscan die
  Kosten aus RP wieder einholt. **Eine Messung über wachsende Inhaltsgrößen
  belegt das**, analog zum Skalierungstest aus RP.
- Der Attestierungsdigest bleibt für identische Eingaben identisch; ändert sich
  seine Definition, ist der alte und der neue Wert im Test gegenübergestellt.
- Rohe Agenttexte unterliegen derselben Prüfung wie `provider_text` in R6 oder
  die Abweichung ist im Test sichtbar begründet.

## Abnahme

- Vier Fakten, vier begründete Inhaltsentscheidungen, jede getestet.
- Die Konsistenz zur R6-Entscheidung ist hergestellt oder die Abweichung
  begründet.
- Die Suitelaufzeit ist gegenüber R6 nicht wesentlich gestiegen; die Zahl wird
  festgehalten. Ein Anstieg wäre hier ein Befund, weil er auf Blobkosten
  hindeutet.
- Die Providernamen-Baseline ist unverändert.
- Keine Protokoll-, Schema- oder Registerversion angehoben.
- Keine neuen `_recoverable_*`-Sonderfälle.
- Suite grün gegen die Baseline nach R6: 1315 passed.

## Nicht-Ziele

- Kein Cutover — S4b.
- Keine Review-Contractfelder — das ist Bündel 7 und folgt danach.
- Keine Änderung an der Validierungsmatrix selbst; dieser Slice recordet ihre
  Ausgabe, er ändert nicht was validiert wird.
- Keine Kompaktierungsoptimierung des Final-Review-Diffs; das ist Backlog `02`.

## Stopbedingungen

- Anhalten, wenn eine Inhaltsentscheidung nur durch Aufweichen der
  Digestbindung möglich wäre.
- Anhalten, wenn ein ausgelagerter Blob nicht fail-closed an seinen Record
  gebunden werden kann — dann wäre er eine zweite Wahrheit.
- Anhalten, wenn die Größenmessung zeigt, dass die Kette dadurch wieder
  überproportional teuer wird und kein Verweismodell tragfähig ist.

## Reviewfokus (Claude)

- Ob die vier Fakten wirklich einzeln entschieden wurden oder eine pauschale
  Regel auf alle vier gelegt wurde.
- Ob rohe Agenttexte anders behandelt werden als `provider_text` in R6, und ob
  der Unterschied begründet ist.
- Ob der Attestierungsdigest noch bindet, was er binden soll — eine stille
  Umdefinition wäre ein Verlust an Beweiskraft.
- Ob ein ausgelagerter Blob fail-closed an seinen Record gebunden ist und nicht
  bloß nebenher liegt.
- Ob die Größenmessung echt ist, mit wachsenden Inhalten, oder nur ein Test mit
  einem kleinen Beispiel.
- Ob die Suitelaufzeit gehalten wurde.
