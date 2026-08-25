# Claude Korrekturreview zu C-11 – Slice 03

Eigenständige Persistierung des Claude-Korrekturreviews zum Blocker `C-11`.
Inhaltsgleich mit dem Abschnitt „Claude Korrekturreview zu C-11" am Ende von
`antigravity-endgueltige-entfernung-slice-03-review.md`; dieses Dokument ist
die namentlich Claude zugeordnete Reviewakte desselben Befunds.

## Reviewrahmen

Manueller Korrekturreview außerhalb von `run_task`, ohne Provideraufruf und
ohne strukturierte JSON-Ausgabe. Produktcode und Tests waren strikt read-only.

Reviewbasis:

- Slice-02-Basiscommit `5193eec feat(orchestrator): remove antigravity runtime`
- vollständiger Slice-03-Diff `git diff 5193eec`; der Slice liegt inzwischen
  als `48d6aac feat(orchestrator): complete antigravity retirement` fest und
  ist gegenüber dem von mir zuvor geprüften Arbeitsbaumstand unverändert
  (`tests/test_language_consistency.py` 262 Ergänzungen und 13 Löschungen,
  Produkt-, Test-, Schema- und Konfigurationspfade 410 Ergänzungen und 1195
  Löschungen über 16 Dateien)
- `AGENTS.md`
- `docs/internal/antigravity-endgueltige-entfernung-arbeitsplan.md`
- `docs/internal/antigravity-endgueltige-entfernung-slice-03-review.md`
  einschließlich meines Korrekturreviews mit `C-11`
- der Abschnitt „Codex-Korrekturrunde zu C-11"

Gegenüber der vorigen Runde hat sich ausschließlich
`tests/test_language_consistency.py` verändert; alle übrigen Diffstat-Zeilen
sind unverändert, was die Zusage `TEST_FILES_TOUCHED` bestätigt. Die vorgelegte
Evidenz (`31 passed in 5.36s`, `1160 passed in 74.44s`) habe ich nicht
reproduziert; den Akzeptanzbefehl zu `C-11` habe ich nachvollzogen und
`git diff --check` mit Exitcode `0` bestätigt — dessen Ausgabe enthält
ausschließlich die bekannten CRLF-Konfigurationswarnungen und keinen
Whitespacefehler. Alle Gegenproben sind providerfrei und haben den
Arbeitsbaum nicht verändert.

## Gesamteinordnung

`C-11` ist behoben, und zwar an der Wurzel statt am Symptom. Die
Ausnahmemechanik ist von Struktur- auf reine Inhaltsgleichheit umgestellt; ich
konnte keine Formulierungsvariante mehr finden, die eine aktive Rollenaussage
an ihr vorbeibringt. `C-10` ist dabei nicht regrediert. Damit steht kein
offenes Claude-Blockerfinding mehr.

## 1. Vollständig inhaltsgebundene Freigaben – bestätigt

`_allowed_retirement_reference_line()` besteht nur noch aus zwei
pfadgebundenen Mengenprüfungen (`stripped in _ALLOWED_MARKET_ANTIGRAVITY_LINES`
beziehungsweise `stripped in _ALLOWED_INTERNAL_RETIREMENT_LINK_LINES`) und
einem `return False` für jeden anderen Pfad. Ich habe den Funktionsquelltext
zur Laufzeit ausgelesen und gegen die früheren Freigabeformen geprüft:
`startswith("|")`, die Präfixe für Überschrift, `Google positioniert`,
`Antigravity bietet damit` und `Offizielle Quellen:` sowie jede
`casefold()`-Teilstringprüfung sind restlos entfallen. Es gibt keine
verbliebene Freigabe, die auf Zeilenform statt Zeileninhalt beruht.

Die Empfindlichkeit gegen die realen Zeilen habe ich einzeln gemessen. Jede
der folgenden Mutationen einer zugelassenen Marktzeile verliert die Freigabe
und löst den Guard aus:

| Mutation einer erlaubten Zeile | Guard |
|---|---|
| ein zusätzliches Zeichen am Zeilenende | Treffer |
| ein fehlendes Zeichen am Zeilenende | Treffer |
| zusätzliche Tabellenspalte `\| aktiver Reviewer \|` | Treffer |
| angehängte Rollenbehauptung | Treffer |
| doppeltes Leerzeichen im Zeileninneren | Treffer |
| geschütztes Leerzeichen statt normalem Leerzeichen | Treffer |
| Schreibweise `ANTIGRAVITY` statt `Antigravity` | Treffer |

Ebenso lösen alle Formen aus, die unter der alten Strukturregel frei waren:
eine beliebige neue Tabellenzeile mit Rollenaussage, eine erweiterte
Überschrift `### 6.3 Google Antigravity 2.0 ist der dritte Reviewer …`, ein
Absatz mit dem Anfang `Antigravity bietet damit …`, einer mit
`Google positioniert Antigravity …` und eine Zeile mit dem Anfang
`Offizielle Quellen:`.

Zur `.strip()`-Frage: Die Normalisierung entfernt ausschließlich führenden und
abschließenden Leerraum. Eine erlaubte Zeile behält ihre Freigabe daher, wenn
man sie einrückt oder ihr Leerzeichen anhängt — beides transportiert keinerlei
Inhalt, weder eine Rollenbehauptung noch eine zusätzliche Tabellenspalte, weil
jedes einzelne Nicht-Leerraum-Zeichen exakt übereinstimmen muss. Leerraum im
Zeileninneren wird nicht normalisiert und bricht die Gleichheit sofort, wie
die Tabelle oben zeigt. `.strip()` kann damit nichts verbergen.

## 2. Marktvergleichsinventur – bestätigt

`_ALLOWED_MARKET_ANTIGRAVITY_LINES` enthält zehn Zeilen. Ich habe unabhängig
die Menge aller Zeilen aus `docs/reference/market-comparison.md` gebildet, die
`antigravity` case-insensitiv enthalten: ebenfalls zehn, und beide Mengen sind
identisch — `only-in-doc` und `only-in-allowlist` sind leer.
`test_market_comparison_allows_only_exact_external_product_lines` fordert genau
diese Mengengleichheit, sodass eine neue, eine entfernte und eine veränderte
Zeile gleichermaßen auffallen.

Die zehn Zeilen habe ich einzeln inhaltlich geprüft: die Zusammenfassungszeile,
die Antigravity in einer Produktaufzählung nennt und die eigene Topologie
ausdrücklich auf „Codex und Claude" festlegt; zwei Vergleichstabellenzeilen zu
Antigravity 2.0, davon eine mit dem expliziten Zusatz „ist aber kein
Bestandteil dieses Orchestrators"; die Kopfzeile der Fähigkeitstabelle, in der
Antigravity eine Produktspalte ist; die Überschrift
`### 6.3 Google Antigravity 2.0`; zwei Absätze des Abschnitts 6.3, deren
zweiter Antigravity ausdrücklich „weder zur Laufzeit noch zur Review- oder
Freigabetopologie" zählt; die Quellenzeile; und zwei Empfehlungstabellenzeilen,
die für Bedürfnisse außerhalb dieses Orchestrators auf andere Werkzeuge
verweisen. Keine dieser Zeilen behauptet eine aktive Orchestratorrolle.

Eine Rollenbehauptung in einer bestehenden Tabellen-, Quellen-, Überschriften-
oder Prosazeile kann die Ausnahme nicht passieren, weil jede solche Behauptung
die Zeile verändert und die Gleichheit damit bricht.

## 3. Historische und erfundene Marktmutationen – bestätigt

`test_market_comparison_rejects_retired_role_lines` arbeitet auf dem realen
Dokument: Es liest `market-comparison.md`, prüft mit
`assert current_line in current`, dass die Zeile heute genau so existiert,
ersetzt sie mit `str.replace(..., 1)` und verlangt einen Treffer im mutierten
Dokumenttext. Das ist keine losgelöste synthetische Zeichenkette.

Ich habe alle vier Fälle unabhängig nachgestellt, indem ich die jeweilige Zeile
positionsgenau aus `git show 5193eec:docs/reference/market-comparison.md`
zurückgeschrieben habe:

- Zeile 48 zurück auf „… Stellt den hier verwendeten unabhängigen Reviewer
  bereit …" → Treffer.
- Zeile 58 zurück auf „… Feste Rollen Codex → Claude → Antigravity; …" →
  Treffer.
- Zeile 102 zurück auf „… In diesem Projekt wird es bewusst auf einen
  unabhängigen, schreibgeschützten Abschlussreviewer nach Claude begrenzt." →
  Treffer.

Alle drei Zeilen, die in meinem `C-11`-Befund noch lautlos durchliefen, lösen
jetzt aus. Zusätzlich habe ich die frei erfundene Drei-Rollen-Tabellenzeile
nicht nur ersetzend wie im Test, sondern **eingefügt** geprüft — auch die
Einfügung löst aus.

## 4. Interne README-Linkausnahme – bestätigt

`_ALLOWED_INTERNAL_RETIREMENT_LINK_LINES` enthält exakt zwei Einträge, den
Arbeitsplanlink und die Archivlinkzeile. Der Abgleich gegen
`docs/internal/README.md` ergibt ebenfalls exakt zwei Antigravity-Zeilen, und
beide Mengen sind identisch. Ich habe vier Versteckversuche geprüft; alle lösen
den Guard aus:

- „Antigravity ist die dritte aktive Prozessrolle." an die Archivlinkzeile
  angehängt,
- dieselbe Aussage an den Arbeitsplanlink angehängt,
- dieselbe Aussage **vor** den Arbeitsplanlink gestellt,
- die Archivlinkzeile in eine zusätzliche Markdown-Tabellenspalte gepackt.

## 5. Keine Regression von C-10 – bestätigt

Die aus `orchestrator.toml` abgeleitete Inventur ist unverändert vollständig:
105 Dateien, und über `git ls-files` bleiben dieselben erklärten Pfade
außerhalb (`.gitattributes`, `.gitignore`, die namentlich gebundenen
Evidenzdateien dieses Arbeitspakets, die Guarddatei über `THIS_FILE`).
`run_task`, `workflow.puml`, `pyproject.toml`, `task.md`, `docs/reference/**`,
`schemas/**` und `tests/fixtures/**` sind weiterhin erfasst; die sieben
parametrisierten Inventurkontrollen einschließlich `OPERATIONS.md` sind
unverändert vorhanden.

Der Findingnamespace-Matcher ist unverändert: `LEGACY = "A-02"`,
`may raise A-* findings`, `prefix = "A-"` und `"^[CA]-(0[1-9])$"` lösen aus,
während `^[A-Za-z0-9_]+$` und `^[0-9a-f]{64}$` keinen Fehlalarm erzeugen. Die
zeilengenauen historischen Negativkontrollen sind weiterhin genau zwei
(`tests/test_contracts.py`, `tests/test_native_review_contract.py`).

## Restrisiken ohne Findingcharakter

Vier Punkte bleiben, keiner davon handlungsrelevant für diesen Slice. Erstens
ist der Guard tokenbasiert: Eine Rollenbehauptung, die das Wort Antigravity
vermeidet („das Produkt aus Abschnitt 6.3 ist der dritte Reviewer"), wird von
keiner Regel erfasst. Das gilt repositoryweit und ist keine Eigenschaft der
Ausnahme. Zweitens ist eine Allowlist eine Fixierung, keine Semantikprüfung:
Wer Dokument und Allowlist gemeinsam ändert, bleibt grün — sichtbar wird das
nur im Review des Diffs, dafür aber zuverlässig, weil beide Änderungen im
selben Commit stehen müssen. Drittens gilt die Mengengleichheitsprüfung nur
für den Marktvergleich, nicht für `docs/internal/README.md`; dort fällt eine
verwaiste Allowlistzeile nicht auf. Eine Umgehung entsteht daraus nicht, weil
jede neue Antigravity-Zeile mangels exakter Übereinstimmung sofort trifft.
Viertens bleibt die Zeilenmarkierung `# retirement-negative-control` eine
inhaltsunabhängige Freigabe für die markierte Zeile; sie ist vom Arbeitsplan
ausdrücklich so vorgesehen, kommt genau zweimal vor und wurde in beiden
vorigen Runden geprüft.

## Befunde

REVIEWER: claude
FINDING_STATUS: C-11 | CLOSED | Sämtliche Strukturfreigaben sind entfernt; `_allowed_retirement_reference_line()` besteht nur noch aus zwei pfadgebundenen Mengenprüfungen auf exakte Zeilengleichheit plus `return False`, was ich am ausgelesenen Funktionsquelltext gegen alle früheren Freigabeformen verifiziert habe. Ich habe die drei Zeilen, die in meinem Befund lautlos durchliefen, positionsgenau aus `git show 5193eec:docs/reference/market-comparison.md` zurückgeschrieben — Zeile 48 („Stellt den hier verwendeten unabhängigen Reviewer bereit"), Zeile 58 („Feste Rollen Codex → Claude → Antigravity") und Zeile 102 („auf einen unabhängigen, schreibgeschützten Abschlussreviewer nach Claude begrenzt") — und alle drei lösen jetzt den Guard aus; eine frei erfundene Drei-Rollen-Tabellenzeile löst sowohl ersetzend als auch eingefügt aus. Jede Mutation einer erlaubten Zeile verliert die Freigabe: ein Zeichen mehr, ein Zeichen weniger, eine zusätzliche Tabellenspalte, eine angehängte Rollenbehauptung, doppeltes Leerzeichen im Zeileninneren, ein geschütztes Leerzeichen und eine geänderte Groß-/Kleinschreibung ergeben sämtlich einen Treffer. `.strip()` kann nichts verbergen, weil es nur führenden und abschließenden Leerraum entfernt und jedes Nicht-Leerraum-Zeichen exakt übereinstimmen muss. Die Allowlist deckt sich exakt mit den zehn Antigravity-Zeilen des realen Marktvergleichs (`only-in-doc` und `only-in-allowlist` leer), der Test fordert echte Mengengleichheit, und alle zehn Zeilen sind inhaltlich geprüft externe Produktvergleiche, Überschrift, Quellen oder Werkzeugempfehlungen ohne aktive Orchestratorrolle. Die parametrisierten Gegenkontrollen mutieren den realen Dokumenttext statt einer losgelösten Zeichenkette. Für `docs/internal/README.md` enthält die Allowlist exakt den Arbeitsplan- und den Archivlink; eine angehängte, eine vorangestellte und eine in eine zusätzliche Tabellenspalte gepackte Rollenbehauptung lösen alle aus. `C-10` ist dabei nicht regrediert: Inventur unverändert bei 105 Dateien mit denselben erklärten Ausnahmen, Matcher unverändert treffsicher für `A-02`, `A-*`, `"A-"` und `^[CA]-` und weiterhin fehlalarmfrei für `[A-Za-z]` und SHA-256-Muster.

REVIEW_EVIDENCE: Vollständige Ablösung aller Struktur- und Präfixfreigaben durch reine Zeilengleichheit, verifiziert am ausgelesenen Funktionsquelltext; Empfindlichkeit der Ausnahme gegen sieben Mutationsklassen einer erlaubten Zeile einschließlich zusätzlicher Tabellenspalte, angehängter Rollenbehauptung, innerer Leerraumänderung, geschütztem Leerzeichen und Groß-/Kleinschreibung; Grenzen der `.strip()`-Normalisierung; exakte Mengengleichheit von Allowlist und den zehn realen Antigravity-Zeilen des Marktvergleichs sowie den zwei Zeilen der internen README; inhaltliche Einzelprüfung aller zehn zugelassenen Marktzeilen auf Rollenfreiheit; positionsgenaue Rückschreibung der drei historischen Zeilen aus `5193eec` und eingefügte wie ersetzende Drei-Rollen-Tabellenzeile; vier Versteckversuche an beiden internen Linkzeilen; Nichtregression der aus `orchestrator.toml` abgeleiteten Inventur gegen `git ls-files`; Nichtregression des Findingnamespace-Matchers in beide Richtungen; Unverändertheit aller übrigen Diffstat-Zeilen gegenüber der Vorrunde; `git diff --check` mit Exitcode 0 | Der Guard bleibt tokenbasiert und allowlistfixiert: Eine Rollenbehauptung, die das Wort Antigravity vermeidet, sowie eine gemeinsame Änderung von Dokument und Allowlist im selben Commit bleiben allein dem menschlichen Diffreview überlassen | Eine spätere Aktualisierung des Marktvergleichs ändert eine zugelassene Zeile inhaltlich und zieht die Allowlist mechanisch nach, ohne dass jemand prüft, ob die neue Formulierung noch frei von einer Orchestratorrolle ist

PRE_MORTEM: In drei Monaten ist die wahrscheinlichste Fehlerursache nicht mehr eine Lücke im Guard, sondern die Bequemlichkeit im Umgang mit ihm. `_ALLOWED_MARKET_ANTIGRAVITY_LINES` fixiert zehn vollständige Absatz- und Tabellenzeilen; jede redaktionelle Pflege des Marktvergleichs — eine neue Antigravity-Version, eine korrigierte Quellenliste, eine umformulierte Spalte — macht den Guard rot. Die schnellste Reaktion ist, die geänderte Zeile in die Allowlist zu kopieren, und genau dabei geht die inhaltliche Prüfung verloren, ob die neue Formulierung Antigravity noch als externes Produkt oder wieder als Rolle beschreibt. Der Test würde weiterhin grün melden, weil er Mengengleichheit prüft und nicht Bedeutung. Die zweite, leisere Variante betrifft die Kopplung an `orchestrator.toml`: Eine spätere Verengung einer Pfadklasse verkleinert die Guardfläche stillschweigend mit, weil kein Test die Breite der Klassen festhält. Beide Varianten fallen erst auf, wenn jemand die Dokumentation gegen den Code liest.

SLICE_APPROVAL: 03 | YES

STATUS: DONE
