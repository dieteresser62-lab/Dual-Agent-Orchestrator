# Wie der Orchestrator arbeitet

Dieses Dokument beschreibt vollständig, was der Orchestrator tut, wenn er
läuft. Es ist in Schichten aufgebaut: Die ersten Abschnitte setzen keine
Fachkenntnis voraus, die späteren geben die Regeln so genau wieder, dass man
danach prüfen kann.

**Wenn Sie nur fünf Minuten haben:** Lesen Sie „Das Grundprinzip" und „Ein
echter Lauf". Das genügt, um zu verstehen, was hier passiert.

**Wenn Sie den Vertrag brauchen:** Springen Sie zu „Die Regeln genau".

---

## Das Grundprinzip

Der Orchestrator lässt zwei KI-Agenten an einem Programm arbeiten und sorgt
dafür, dass keiner von beiden seine eigene Arbeit abnimmt.

Man kann es sich wie eine Werkstatt vorstellen:

- **Codex** ist der Handwerker. Er plant die Arbeit und führt sie aus.
- **Claude** ist der Prüfer. Er sieht sich jedes Zwischenergebnis an und darf
  nichts selbst anfassen — nur beurteilen.
- **Der Orchestrator** ist der Werkstattleiter. Er gibt die Aufträge aus,
  lässt die Tests laufen, führt Buch und ist der Einzige, der ein Ergebnis in
  die Versionsverwaltung übernimmt.

Diese Trennung ist der ganze Zweck der Übung. Ein einzelner Agent, der seine
Arbeit selbst beurteilt, neigt dazu, sie für gelungen zu halten. Zwei
getrennte Rollen mit klaren Befugnissen tun das nicht.

### Was Sie hineingeben

Eine Beschreibung dessen, was entstehen soll — in normalem Deutsch. Kein
Formular, keine Aufgabenliste, keine Dateipfade. So etwas hier genügt:

> Man soll mehrere Rezepte auswählen können und daraus eine einzige
> Einkaufsliste bekommen. Gleiche Zutaten aus verschiedenen Rezepten gehören
> zusammengefasst. Was sich nicht sauber zusammenrechnen lässt, soll
> nebeneinander stehen bleiben — lieber „Petersilie: 2 EL, 1 Bund" als eine
> erfundene Gesamtzahl.

Der Orchestrator liest diesen Text, sieht sich das Zielrepository an und
erzeugt daraus selbst einen Arbeitsplan mit überschaubaren Arbeitspaketen.
Sie müssen den Schnitt nicht vorgeben.

### Was herauskommt

Ein Branch mit einem Commit je Arbeitspaket, jedes geprüft und getestet, dazu
ein Prüfbericht. Nichts wird ohne Ihr Zutun ins entfernte Repository
geschoben und nichts zusammengeführt — der Orchestrator committet
ausschließlich lokal.

---

## Die drei Beteiligten und ihre Befugnisse

| | darf | darf nicht |
|---|---|---|
| **Codex** | planen, Dateien ändern, Tests schreiben, Befunde beantworten | die eigene Arbeit freigeben, committen |
| **Claude** | lesen, beurteilen, Befunde eröffnen und schließen | Dateien ändern, Tests ausführen, committen |
| **Orchestrator** | Aufträge vergeben, Tests ausführen, lokal committen, Buch führen | inhaltlich urteilen |

Keine Rolle darf pushen, zusammenführen, die Historie umschreiben oder die
eigene Arbeit abnehmen.

Claude arbeitet dabei in einer **schreibgeschützten Kopie** des Repositorys.
Das ist keine Vertrauensfrage, sondern eine Bauweise: Wer nichts ändern kann,
kann auch nicht versehentlich das prüfen, was er selbst gerade angepasst hat.

Dasselbe Prinzip gilt in der Gegenrichtung, und es ist die vielleicht
eleganteste Stelle des Systems: Dass nur Claude einen Befund schließen darf,
ist keine Regel, die Codex befolgen muss — **Codex' Antwortschema enthält kein
Feld dafür.** Er kann einen Befund beantworten, aber nicht für erledigt
erklären. Was nicht ausdrückbar ist, muss nicht verboten werden.

---

## Wie eine Aufgabe hineinkommt

Im Normalbetrieb läuft der Orchestrator als **Wache**. Er beobachtet einen
Ordner und nimmt jede Markdown-Datei auf, die dort stabil liegen bleibt:

```
inbox/                  hier legen Sie Ihre Beschreibung ab
outbox/done/            abgearbeitete Aufgaben, mit Zeitstempel
outbox/failed/          nicht behebbar gescheiterte Aufgaben
```

```bash
./run_task --watch --agents-file AGENTS.md --verbose
```

Die Wache prüft alle fünf Sekunden, wartet bis eine Datei mindestens eine
Sekunde unverändert ist — damit sie keine halb geschriebene Datei aufnimmt —
und beginnt dann.

### Aufgaben erzeugen Aufgaben

Das Wesentliche daran: Der Orchestrator legt **selbst** Aufgaben in diesen
Ordner. Eine Planung erzeugt die zugehörige Umsetzungsaufgabe, ein
Abnahmereview mit Restarbeit erzeugt eine Folgeaufgabe. Dieselbe Wache nimmt
sie unmittelbar auf.

Der Lauf vom 22. September, vollständig ohne menschliches Zutun nach der
ersten Datei, mit den Dateinamen nach heutiger Regel:

```
einkaufsliste.md                       → Plan       → done
einkaufsliste-implement.md             → 7 Pakete   → done   (Abnahme: 1 Befund)
einkaufsliste_followup01.md            → Plan       → done
einkaufsliste_followup01-implement.md  → 1 Paket    → done   (Abnahme: sauber)
```

Vier Aufgaben, eine davon von Ihnen, drei vom System. Der Eingang ist am Ende
leer — das ist das Abschlusskriterium.

Eine Folgeaufgabe behält den Gegenstand der ersten Datei und zählt die
Abnahmereviews hoch: `_followup01` nach dem ersten, `_followup02` nach dem
zweiten und so fort. Die Nummer stammt aus dem Zähler
`ACCEPTANCE_REVIEW_NUMBER` im Dokument, nicht aus dem Dateinamen.

### Wenn eine Aufgabe nicht durchgeht

Ein nicht behebbarer technischer Fehler legt die Aufgabe als `.poison` unter
`outbox/failed/` ab. Daneben liegt `.poison.error.json` mit Lauf-Kennung,
letztem Schritt und der technischen Ursache.

Eine **inhaltlich** gescheiterte Aufgabe — etwa ein Slice, der an der
Konvergenzpflicht endet — ist kein Poison-Fall. Sie endet mit einem Exitcode
und einem lesbaren Urteil; die Aufzeichnungen bleiben vollständig erhalten.

---

## Der Ablauf in einem Bild

```
        Ihre Beschreibung
               │
               ▼
     ┌──────────────────┐
     │  1. PLANUNG      │   Codex schreibt einen Arbeitsplan
     │                  │   Claude prüft ihn
     │                  │   bis keine Befunde offen sind
     └────────┬─────────┘
              │
              ▼
     ┌──────────────────┐
     │  2. UMSETZUNG    │   je Arbeitspaket ("Slice"):
     │                  │
     │   Codex baut ──► Tests ──► Claude prüft
     │        ▲                        │
     │        │     Befund offen?      │
     │        └────────────────────────┘
     │                                 │
     │              kein offener Blocker ──► COMMIT
     └────────┬─────────────────────────────────────┘
              │  alle Slices fertig
              ▼
     ┌──────────────────┐
     │  3. ABNAHME      │   Claude liest den gesamten Branch
     │                  │
     │   Restarbeit? ──► neue Aufgabe, alles beginnt von vorn
     │   nichts mehr?  ──► fertig
     └──────────────────┘
```

Drei Phasen, neun Schritte, zwei Aufgabenarten. Mehr Zustände gibt es nicht.

---

## Ein echter Lauf

Das Folgende ist kein Beispiel, sondern ein Protokoll: der Lauf vom
22. September 2026, zwei Stunden und zwei Minuten, aus der oben zitierten
Einkaufslisten-Beschreibung.

### Phase 1 — Planung, 5 Minuten

Codex sieht sich das Repository an und schneidet die Arbeit in sieben Pakete.
Claude prüft den Plan und gibt ihn frei. Der Plan wird committet.

### Phase 2 — Umsetzung, sieben Pakete

| Paket | Inhalt | Runden |
|---|---|---|
| 01 | Mengen zusammenrechnen und skalieren | 1 |
| 02 | Einheiten, zweisprachig | 1 |
| 03 | Dauerhafte Speicherung der Liste | 2 |
| 04 | Mehrfachauswahl und Portionen | 2 |
| 05 | Die Listenansicht zum Abhaken | 1 |
| 06 | Ausgabe als Text und Druck | 1 |
| 07 | Zusammenbau zur Anwendung | 2 |

Bei den Paketen 03, 04 und 07 hat Claude etwas beanstandet. Beispiel aus
Paket 03:

> Die Änderungspfade brauchen ein fail-closed lesendes Verfahren, damit ein
> vorübergehender oder fehlerhafter Lesezugriff nicht in einen leeren Zustand
> umgedeutet und dauerhaft gespeichert wird.

Codex hat den Befund behoben, Claude hat ihn geschlossen, das Paket wurde
committet. Jedes Mal genügte eine Korrekturrunde.

### Phase 3 — Abnahme, zweimal

Claude liest den **gesamten Branch** — 740.000 Zeichen gegenüber 94.000 bei
einem einzelnen Paket — und findet etwas, das kein einzelnes Paket zeigen
konnte:

> Wird während einer noch schwebenden Speicherung eines Häkchens ein
> vollständiges Neuzeichnen ausgelöst — durch Sprachwechsel, Rezept
> hinzufügen, Portionen ändern —, zeichnet die Ansicht aus ihrem veralteten
> Schnappschuss und setzt das gerade gesetzte Häkchen sichtbar zurück, obwohl
> es dauerhaft gespeichert ist.

Bemerkenswert daran: Die Prüfung von Paket 05 hatte dieses Risiko bereits
gesehen und ausdrücklich zurückgestellt, weil es „vollständig davon abhängt,
wie die noch nicht gebaute Integration ihre Neuzeichnungen gegenüber den
Speichervorgängen terminiert". In Paket 07 ist es eingetreten, und die
Abnahme hat es gefunden.

Der Befund wird zu einer **neuen Aufgabe**. Der gesamte Prozess beginnt von
vorn: planen, umsetzen, prüfen, committen. Der zweite Abnahmereview findet
nichts mehr. Fertig.

### Die Bilanz

```
Laufzeit                2:02:31
Commits                 12
Aufgaben abgeschlossen  4      (keine gescheitert)
Tests im Zielprojekt    83     (alle grün)
Providerversuche        36
davon gescheitert        8     (27:13 verlorene Zeit)
```

Die acht gescheiterten Versuche sind ehrlich ausgewiesen: vier Abbrüche der
Claude-Kommandozeile ohne Ausgabe, vier formal ungültige Antworten des
Prüfers. Keiner hat den Lauf beendet — dafür sind die Budgets da. Aber sie
kosteten ein Fünftel der Laufzeit, und das ist der offene Posten dieses
Systems.

---

## Die Regeln genau

Ab hier wird es formal. Was hier steht, ist im Code erzwungen und durch Tests
abgesichert.

### Zwei Aufgabenarten

| Art | Was geschieht | Ergebnis |
|---|---|---|
| `PLAN_ONLY` | nur planen | eine Arbeitsplandatei, committet |
| `IMPLEMENT` | umsetzen, prüfen, committen, abnehmen | ein Branch mit Commits |

Welche Art vorliegt, leitet der Orchestrator aus der Aufgabe ab. Der
Zielbranch darf ausdrücklich benannt, im Fließtext eindeutig erkennbar oder
aus Betreff und Digest bestimmt sein; ist er mehrdeutig, hält der Lauf
fail-closed an.

### Neun Schritte

```
codex_plan  →  claude_plan_review  →  codex_plan_revision
codex_implementation  →  claude_slice_review  →  codex_correction
slice_commit  →  claude_final_review  →  completed
```

Und drei Arten von Arbeitseinheiten: `plan`, `slice`, `final_review`.

### Befunde: zwei Klassen, zwei Antworten

Claude eröffnet Befunde mit Kennungen der Form `C-01`, `C-02`, … Jeder Befund
hat eine von zwei Klassen:

- **`FINDING`** — ein gewöhnlicher Befund.
- **`BLOCKER`** — ein Befund, der einen Commit verhindert.

Codex muss **jeden offenen Befund genau einmal** mit einer Begründung
beantworten. Es gibt genau zwei Antworten:

> **Blocker müssen gelöst werden. Findings können gelöst oder abgelehnt
> werden.**

Eine Ablehnung eines Blockers ist ungültig. Eine **Annahme ohne Änderung** ist
ebenfalls ungültig: Wer einen Befund annimmt, muss etwas geändert haben — der
Orchestrator vergleicht dazu den Fingerabdruck des Arbeitsstands vor und nach
der Korrektur. Wer meint, es sei bereits erledigt, lehnt mit dieser Begründung
ab; das ist eine prüfbare Aussage, eine folgenlose Annahme nicht.

Diese Prüfung fasst bewusst nur den eindeutigen Fall — angenommen und
überhaupt nichts geändert. Welcher von mehreren Befunden behoben wurde, sagt
ein Fingerabdruck nicht; das bleibt Sache des Prüfers.

### Wie ein Befund eskaliert

Claude hat **keinen** ausdrücklichen Zug „hochstufen". Die Eskalation
geschieht durch Unterlassen:

> Ein gewöhnlicher Befund, den Claude in einem **abgelehnten** Review **nicht
> schließt**, wird zum `BLOCKER`.

Der Orchestrator verzeichnet diesen Übergang. Will der Prüfer einen Befund
offenhalten, lehnt er das Review ab und lässt ihn offen — mehr ist nicht zu
tun.

Umgekehrt gilt: Eine **Freigabe** mit einem eigenen offenen Befund ist
widersprüchlich und wird zurückgewiesen. Entweder im selben Zug schließen oder
ablehnen.

### Die eine Commit-Bedingung

> Ein Slice wird committet, wenn die aus den Aufzeichnungen abgeleitete
> Befundmenge dieses Slices **keinen offenen Blocker** enthält.

Das ist die vollständige Bedingung. Es gibt keine zweite.

Jeder Befund gehört unveränderlich zu dem Slice, in dem er eröffnet wurde, und
überschreitet dessen Grenze nie.

### Wann ein Lauf endet

Der Orchestrator ist so gebaut, dass er **immer** zu einem Ende kommt — nötig,
weil niemand danebensteht.

**Die Konvergenzpflicht.** Die erste Prüfung eines Slices entdeckt. Jede
weitere abgelehnte Prüfung ist eine Konvergenzrunde und muss **entweder** einen
bereits bekannten Befund schließen **oder** eine belegte, den Fingerabdruck
ändernde Behebung verzeichnen. Eine Runde, die weder das eine noch das andere
leistet, beendet den Slice negativ.

**Die Zähler**, konfigurierbar in `orchestrator.toml`:

| Parameter | Vorgabe | Bedeutung |
|---|---:|---|
| `max_rounds_per_loop` | 6 | Runden je Planungs- oder Slice-Schleife |
| `max_acceptance_reviews` | 6 | Durchläufe des Abnahmezyklus |
| `max_transport_failures` | 3 | Abbrüche ohne verwertbare Antwort, je Arbeitseinheit |
| `max_contract_rejections` | 3 | formal ungültige Antworten, je Arbeitseinheit |

Die beiden letzten Budgets sind bewusst **getrennt**. Ein Werkzeug, das gar
nicht antwortet, ist kein inhaltlicher Fehler und darf das inhaltliche Budget
nicht aufzehren.

**Der Abnahmezyklus.** Findet die Abnahme Restarbeit, entsteht daraus eine
gewöhnliche neue Aufgabe, und der Prozess beginnt von vorn — mit dem Inhalt
des Abnahmereviews als Arbeitsgrundlage. Am Limit endet die Aufgabe ohne neues
Dokument und ohne Rücknahme.

### Validierung

Nach jeder Änderung führt **der Orchestrator** — nicht ein Agent — den
Prüfbefehl aus und bindet das Ergebnis an den Fingerabdruck des geprüften
Stands. Kein Agent darf ein Testergebnis behaupten.

Der Befehl steht in der `orchestrator.toml` des **Zielrepositorys**, nicht in
der des Orchestrators. Jedes Projekt bringt seinen eigenen mit:

```toml
# im Zielrepository
[validation]
default_command = ["npm", "test"]
```

```toml
# im Orchestrator-Repository selbst
[validation]
default_command = ["python3", "-m", "pytest", "tests/", "-v", "-m", "not crash_harness"]
```

Findet der Orchestrator keinen konfigurierten Befehl oder ist er nicht
ausführbar, hält er an, statt ungeprüft zu committen.

---

## Wenn etwas schiefgeht

### Exitcodes

| Code | Bedeutung | fortsetzbar |
|---:|---|---|
| `0` | vollständig abgeschlossen | — |
| `1` | technischer, Konfigurations- oder Zustandsfehler | nein |
| `2` | Quotenfortsetzung ohne sichere Repositorybindung | ja |
| `3` | Agent fehlgeschlagen, abgelaufen oder nicht verfügbar | ja |
| `4` | Benutzerentscheidung oder Richtlinien-Gate nötig | ja |
| `5` | terminales Urteil: Ablehnung, Eingabegrenze | nein |

Bei 2, 3 und 4 bleibt ein fortsetzbarer Zustand zurück; nach Behebung setzt
`--resume` denselben Lauf fort.

### Gates

Ein Gate ist ein Halt, der eine **menschliche Entscheidung** verlangt. Er wird
ausdrücklich angenommen oder abgelehnt, immer mit Begründung:

```bash
./run_task --task-file <unveränderte-aufgabe> --resume --approve-gate \
  --gate-rationale "Fingerabdruck und Pfade geprüft und Fortsetzung freigegeben"
```

Die Entscheidung wird mit Zeitpunkt, bestätigtem Fingerabdruck, betroffenen
Pfaden und auslösender Aufruf-Kennung festgehalten. Ohne diese ausdrückliche
Freigabe bleibt der Lauf angehalten — ein gewöhnliches `--resume` genehmigt
nichts.

Vier Gates sind Schalter in `[workflow]` und standardmäßig aus: Freigabe des
Plans, Freigabe von Teständerungen, Freigabe jedes Pakets und Freigabe von
Umfangserweiterungen. Ausgeschaltet entscheidet der Orchestrator selbst; die
Einzelheiten stehen in der [Einrichtung](einrichtung.md), Abschnitt 4.2.

### Fail-closed

Der Grundsatz bei Unklarheit lautet **anhalten, nicht raten**. Eine fehlende,
beschädigte, unbekannte oder widersprüchliche Aufzeichnung führt nicht zu
einer Reparatur, sondern zu einem Halt. Agenten dürfen niemals Aufzeichnungen,
Freigaben oder Migrationstatsachen erfinden.

---

## Wo die Wahrheit steht

Für Leser, die dem System auf die Finger sehen wollen.

### Die Aufzeichnungskette

```
.orchestrator/artifacts/<lauf-id>/records/     ← die verbindliche Quelle
.orchestrator/state.json                       ← wegwerfbare Projektion
.orchestrator/artifacts/<lauf-id>/head.json    ← rekonstruierbarer Zwischenspeicher
docs/internal/<...>-review.md                  ← Lesefassung für Menschen
```

Nur der erste Pfad zählt. Alles andere ist ableitbar und darf jederzeit
verworfen und neu gebaut werden. `state.json` dient allein dazu, die
Lauf-Kennung zu finden.

Die Aufzeichnungen sind **anfügend**: Sie werden geschrieben, nie geändert.
Jede Tatsache — jedes Urteil, jeder Befund, jeder Commit, jeder Providerstart
— steht als eigener, fingerabdruckgebundener Eintrag darin.

### Nebenwirkungen und Wiederaufnahme

Jeder Commit, jeder Providerstart, jeder verbindliche Dateischreibvorgang und
jede Verschiebung im Postfach wird von einem Paar aus **Absicht** und
**Ergebnis** eingeklammert. Dadurch lässt sich der Verlauf allein durch
erneutes Abspielen der Kette rekonstruieren; ein Blick auf Git oder das
Dateisystem ist nur nötig, um eine offene Absicht abzugleichen.

Eine Wiederaufnahme darf ausschließlich einen exakten Anfangsabschnitt der
Startsequenz vervollständigen, und nur bevor irgendeine Wirkung nach außen
eingetreten ist. Jede Spur von Laufzeitgeschehen, Fehlschlag, Gate-Entscheidung
oder vollzogener Nebenwirkung hält die Wiederaufnahme fail-closed.

### Transport

Beide Agenten liefern ausschließlich **anfragegebundenes JSON** gegen ein
Schema, das zu dieser einen Anfrage gehört. Keine Textmarker, keine
Markdown-Umrandungen, keine nachträgliche Normalisierung, kein
Reparaturdurchgang. Eine fehlende oder ungültige Antwort endet als
Ausgabefehler.

---

## Begriffe

**Slice** — ein Arbeitspaket mit eigenem Umfang, eigener Prüfung und eigenem
Commit.

**Finding** — ein Befund des Prüfers, mit Kennung `C-nn`, gehört unveränderlich
zu seinem Slice.

**Blocker** — ein Befund, der den Commit verhindert.

**Fingerabdruck** — eine Prüfsumme über den Arbeitsstand. Sie bindet Urteile,
Testergebnisse und Freigaben an genau den Stand, auf den sie sich beziehen.

**Konvergenzrunde** — jede Prüfrunde nach der ersten. Sie muss Fortschritt
nachweisen.

**Abnahmereview** — die abschließende Prüfung des gesamten Branchs.

**Gate** — ein Halt, der eine menschliche Entscheidung verlangt.

**Fail-closed** — bei Unklarheit anhalten statt weitermachen.

---

## Weiterführend

- `README.md` — Installation, Kommandozeile, Konfiguration
- `Quickstart.md` — der erste Lauf in acht Schritten
- `docs/reference/architecture-and-domain-concept.md` — Bausteine und Begriffe
- `AGENTS.md`, `CODEX.md`, `CLAUDE.md` — die verbindlichen Rollenverträge
- `docs/internal/zielmodell-vereinfachter-orchestrator.md` — das Sollmodell,
  gegen das gebaut wird
