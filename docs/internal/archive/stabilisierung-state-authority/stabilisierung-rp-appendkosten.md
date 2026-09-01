# RP — Appendkosten der Recordkette

Stand: 30. August 2026  
Ausgangscommit: `5334248`

## Ergebnis

Der gewöhnliche Appendpfad liest einen bereits vollständig validierten,
unveränderten Recordprefix nicht erneut. `ArtifactStore` leitet daraus einen
prozesslokalen Append-Index für Idempotenzschlüssel, Record-IDs,
`(record_type, logical_id, revision)`, höchste Revision und Head ab.
`ArtifactBridge.append()` konstruiert den Kandidaten aus diesem Index;
`ArtifactStore.put()` prüft den neuen Record inkrementell und liest nach der
atomaren Veröffentlichung nur diese eine Recorddatei mit Digest-, Schema- und
Storeinvariantenprüfung zurück.

Der Index wird nicht persistiert. `head.json` enthält weiterhin nur einen
rekonstruierbaren Prefix-Nachweis; sein `chain_sha256` wird nun rollierend und
damit in konstanter Zeit fortgeschrieben. Fehlt oder verändert sich dieser
Nachweis, verändert sich das Recordverzeichnis oder öffnet ein neuer
`ArtifactStore` den Lauf, wird der prozesslokale Index verworfen und durch
einen vollständigen autoritativen Recordscan neu aufgebaut. Recordbytes werden
niemals aus Index- oder Headwerten repariert.

Die inkrementelle Prüfung ist zur bisherigen Kettenprüfung äquivalent: Der
erste Record darf keinen Vorgänger haben; jeder weitere Record muss den
validierten Head als primären Vorgänger nennen; alle Referenzen müssen im
validierten Prefix liegen; Record-ID, Revision und Idempotenzschlüssel müssen
eindeutig sein. Dadurch können beim Anhängen weder Fork, Lücke, Zyklus,
Disconnected Chain noch nicht-prioritäre Referenz neu entstehen. Ausdrückliche
`load_chain()`-, Replay- und Resume-Pfade behalten unverändert den vollständigen
Scan einschließlich `_order_chain()` und aller Storeinvarianten.

Der Storevertrag bleibt „ein externer Writer je Lauf“; echte parallele Writer
werden weiterhin nicht serialisiert. Ein vor dem Append festgestellter
Head-/Verzeichniswechsel und ein Headwechsel während der Publikation führen
jedoch zum vollständigen Neuaufbau statt zu einer Cacheentscheidung. Nach
einem abgebrochenen oder als fehlgeschlagen gemeldeten Write invalidiert der
Store seinen Index. `ArtifactBridge.append()` scannt dann autoritativ und
findet einen bereits dauerhaft publizierten Record weiterhin über dessen
Idempotenzschlüssel.

## Gemessene Skalierung

Der providerfreie Wandzeittest misst drei deutlich verschiedene Kettenlängen.
Die echte Recordlese- und Schemavalidierung bleibt aktiv; eine feste Verzögerung
je Recordlese macht deren Kosten gegenüber Scheduler- und `fsync`-Streuung
sichtbar. Zusätzlich führt derselbe Test eine absichtlich wiederhergestellte
Vollscan-Mutation aus. Er prüft keine Aufrufzähler.

| Appends | RP-Pfad | Vollscan-Kontrollmutation |
|---:|---:|---:|
| 12 | 0,292 s | 0,914 s |
| 24 | 0,584 s | 3,190 s |
| 48 | 1,137 s | 11,787 s |

Der gemessene Skalierungsexponent beträgt **0,98** für den RP-Pfad und **1,84**
für die Vollscan-Kontrollmutation. Der Test fordert `< 1,65` für den produktiven
Pfad und `> 1,65` für die rote Gegenprobe. Eine Rückkehr des produktiven Pfads
zum bisherigen Vollscan verletzt damit dieselbe gemessene Schranke.

## Profilvergleich des festgelegten Einzeltests

Profilierter Test:
`test_workflow_transition_matrix.py::test_replay_and_carry_forward_mutations_turn_matrix_cases_red`

| Funktion/Messwert | R3 auf `5334248` | RP | Änderung |
|---|---:|---:|---:|
| Profil-Gesamtzeit | 158,2 s | 52,236 s | −67,0 % |
| `_load_chain` kumulativ | 152,8 s / 1.072 Aufrufe | 40,763 s / 292 Aufrufe | −780 Aufrufe |
| `_read_record` kumulativ | 144,7 s / 9.396 Aufrufe | 42,560 s / 2.778 Aufrufe | −6.618 Aufrufe |
| `ArtifactBridge.append` | 116,3 s / 260 Aufrufe | 9,958 s / 260 Aufrufe | −91,4 % kumulativ |
| `ArtifactStore.put` | 78,7 s / 260 Aufrufe | 7,731 s / 260 Aufrufe | −90,2 % kumulativ |

Die Differenz von 780 `_load_chain`-Aufrufen entspricht exakt drei beseitigten
Vollscans je 260 Appends: Kandidatenbildung in der Bridge, Vorprüfung in
`put()` und Nachprüfung nach Veröffentlichung. Die verbleibenden 292 Scans
sind ausdrückliche Replay-, Resume- und Test-Ladevorgänge. Ohne Profiler lief
derselbe Einzeltest in **27,74 s**.

## Gesamtabnahme

`wsl.exe python3 -m pytest tests/ -v`:

- R3-Baseline: 1.257 Tests in 492 s
- RP: **1.264 Tests in 259,87 s**
- Laufzeitänderung: **−232,13 s beziehungsweise −47,2 %**

Die sieben neuen Tests prüfen Skalierung samt roter Vollscan-Kontrolle,
Index-/Headverlust, manipulierten Head-Nachweis, Records-vor-Cache,
Forkinvalidierung, einen späteren Store-Writer, Idempotenz nach Indexverlust
und durable-but-reported-failed. Die S2-Prädikatinventur umfasst nun auch die
neuen Append-, Index- und Head-Grenzen. Protokoll-, Schema- und
Registerversionen, Providernamen-Baseline, Mirror-Schreibfolge und
`_recoverable_*`-Inventar blieben unverändert.
