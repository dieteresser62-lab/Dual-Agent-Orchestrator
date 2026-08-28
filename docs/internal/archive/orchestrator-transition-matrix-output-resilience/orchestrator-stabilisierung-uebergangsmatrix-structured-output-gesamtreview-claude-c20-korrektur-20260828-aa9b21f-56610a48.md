# Claude-Korrektur- und Finalreview zu C-20

**Reviewer:** Claude (manuell, adversarial) · **Datum:** 28. August 2026 ·
**Art:** Konvergenz- und Abschlussreview des C-20-Deltas

## 1. Ergebnis vorab

`C-20` ist geschlossen. Der Delta beschränkt sich nachweislich auf §5 des
providerfreien Abschlussnachweises, beide Zahlen sind am aktuellen Stand von
mir reproduziert, und die Zuschreibung als agent-lokale Codex-Evidenz ist
eindeutig. `C-01` bis `C-19` bleiben geschlossen; der Delta enthält keine
Code-, Test- oder Fixtureänderung, die eine Regression tragen könnte. Kein
neuer Blocker. **Die Branchfreigabe wird erteilt.**

## 2. Selbst erhobener Stand

| Merkmal | Selbst gemessen | Erwartet | |
|---|---|---|---|
| Branch | `feature/orchestrator-transition-matrix-output-resilience` | identisch | ✓ |
| `HEAD` | `aa9b21f086118a3078a414ce6734c6aa4833c08e` | identisch | ✓ |
| Merge-Base gegen `master` | `de1bd9ea40392c17e47efcc8d243c7de1438ae59` | identisch | ✓ |
| Geändert gegenüber `HEAD` | genau `docs/internal/orchestrator-stabilisierung-providerfreier-resilienznachweis.md` | identisch | ✓ |
| Ungetrackt | genau mein Finalreview `…-claude-post-commit-20260828-aa9b21f-7674ee1d.md` | identisch | ✓ |
| Gestagt / gelöscht | nichts | nichts | ✓ |
| Paketumfang (Vereinigung Diff gegen `master` + ungetrackt) | 20 Pfade | 20 | ✓ |
| Worktree-Fingerprint | `56610a48ac0bb9249ad9374ec3c6c2173d133d252e1596fea9fa347e9e9284fb` | identisch | ✓ |

Der Fingerprint wurde nach der vorgegebenen Regel selbst berechnet: SHA-256
über die sortierte Vereinigung aus `git diff --name-only master` und
`git ls-files --others --exclude-standard`, je Pfad Pfad, NUL-Byte,
`git hash-object`-Blob-ID und Zeilenumbruch. Die Zweipfad-Arbeitsbaumgrenze,
`HEAD` und der Fingerprint wurden unmittelbar vor der Schreiboperation erneut
geprüft; kein Drift.

## 3. Reviewgrenze

Kein `run_task`, kein Watcher, kein Bootstrap, kein Provider- oder
Canaryaufruf. Kein Staging, Commit, Merge, Push, Branchwechsel, keine
Historienänderung, keine Berührung von `.orchestrator`. Quellcode, Tests,
Fixtures, Konfiguration und alle bestehenden Dokumentinhalte blieben
unverändert.

Die einzige Schreiboperation ist die Anlage genau dieses Dokuments unter dem
vorgegebenen Pfad; keine zweite Kopie, kein bestehendes Reviewdokument
verändert.

Die vollständige Suite habe ich weisungsgemäß **nicht** ausgeführt. Wo eine
Gesamtzahl nötig war, habe ich `--collect-only` benutzt, also eine reine
Sammlung ohne Testlauf. Die Codex-Angaben (`29 passed in 1.88s`,
`1092 tests collected in 1.97s`, `1092 passed in 130.96s`, sauberer
`git diff --check`) sind Fremdangaben; die ersten beiden habe ich unabhängig
reproduziert, die Suitezahl übernehme ich als agent-lokale Codex-Evidenz und
beanspruche keine Orchestrator-Attestierung.

## 4. Eigene Befehle und Gegenproben

| # | Gegenprobe | Ergebnis |
|---|---|---|
| Q1 | Branch, `HEAD`, Merge-Base, `git status --porcelain`, gestagte Pfade | wie erwartet, exakt zwei Arbeitsbaumpfade, nichts gestagt |
| Q2 | Fingerprint über die 20 Pfade | `56610a48…` — Kontrollwert bestätigt |
| Q3 | `git diff HEAD -- …resilienznachweis.md` | genau ein Hunk in §5, zwei ersetzte Absätze |
| Q4 | `git diff --name-only HEAD` | genau ein Pfad |
| Q5 | Blobvergleich aller 19 committed Paketpfade Arbeitsbaum gegen `HEAD` | nur der Abschlussnachweis abweichend; Code, Tests, Fixtures, übrige Dokumente identisch |
| Q6 | `python3 -m pytest tests/test_dry_run_scenarios.py tests/test_orchestrator_resilience_evidence.py -q -p no:cacheprovider` | **`29 passed in 1.83s`** |
| Q7 | Aufteilung der 29 je Datei | 7 + 22 = 29 |
| Q8 | `python3 -m pytest tests/ --collect-only -q -p no:cacheprovider` | **`1092 tests collected`** |
| Q9 | Testnamen des fokussierten Scopes gelesen | Parser-/Ladefälle, Gateidentitätsfälle und die elf ausgeführten Szenarien — deckt die Prosabeschreibung |
| Q10 | Canary- und Smoke-Test-Aussagen im gesamten Dokument | zwei Fundstellen, beide unverändert |

## 5. Prüfung der neun Pflichtpunkte

| # | Pflichtpunkt | Befund |
|---|---|---|
| 1 | §5 nennt für den fokussierten Harnesslauf exakt `29 passed` | **erfüllt** — wörtlich „bestand am finalen Paketstand mit `29 passed`" |
| 2 | Dokumentierter Befehlsscope entspricht `test_dry_run_scenarios.py` + `test_orchestrator_resilience_evidence.py`, Zahl providerfrei reproduziert | **erfüllt** — die Prosa nennt „Parser, Gateidentität und die elf wirklich ausgeführten Szenarien"; die Sammlung zeigt, dass genau diese drei Gruppen ausschließlich in diesen beiden Dateien liegen (7 Parser-/Ladefälle plus 22 Fälle aus Gateidentität, Manifest, Report und elf Szenarien). Mein eigener providerfreier Lauf ergibt `29 passed` |
| 3 | §5 nennt für den finalen Paketstand exakt `1092 passed` | **erfüllt** — wörtlich „bestand am selben finalen Paketstand mit `1092 passed`" |
| 4 | `pytest tests/ --collect-only -q` ergibt exakt 1092 | **erfüllt** — `1092 tests collected in 1.95s` |
| 5 | Vollständige Suite als agent-lokale Codex-Messung dokumentiert, von mir nicht ausgeführt | **erfüllt** — ich habe sie nicht gestartet und beanspruche keine eigene Ausführung |
| 6 | Beide Zahlen eindeutig als agent-lokale Codex-Evidenz und nicht als Attestierung bezeichnet | **erfüllt** — „Beide Angaben sind agent-lokale Validierungsevidenz von Codex und keine Orchestrator-Attestierung" steht unmittelbar bei den Zahlen und bezieht sich sprachlich auf beide |
| 7 | Beide Werte demselben finalen Paketstand zugeordnet, keine veraltete Laufzeit behauptet | **erfüllt** — „am finalen Paketstand" und „am selben finalen Paketstand"; die zuvor beanstandete Formulierung „nach Abschluss aller Slice-3-Nacharbeiten" und die veraltete Laufzeit `120.17s` sind entfernt. Es wird jetzt gar keine Laufzeit mehr genannt, was zugleich die vom Arbeitsplan untersagte volatile Zeitschwelle vermeidet |
| 8 | Fachlicher Delta beschränkt sich auf §5 dieses Dokuments | **erfüllt** — `git diff HEAD` zeigt genau einen Hunk in §5; der Blobvergleich über alle 19 Paketpfade weist nur dieses Dokument als geändert aus |
| 9 | Keine veränderte Aussage zu Provider-Canaries oder zum nachgelagerten Smoke-Test | **erfüllt** — „Externe Provider-Canaries blieben deaktiviert." steht unverändert im selben Absatz, §6 zum nachgelagerten realen Smoke-Test ist wortgleich unberührt |

## 6. Disposition C-20

**Geschlossen.**

Der Befund lautete, dass §5 mit `28 passed` und `1091 passed in 120.17s`
Zahlen eines Vorgängerstands nannte und sie ausdrücklich dem abgeschlossenen
Slice-3-Stand zuschrieb, während der committed Stand 29 beziehungsweise 1092
ergibt. Genau diese beiden Aussagen sind ersetzt.

Der Akzeptanztest meines Befunds verlangte zweierlei, und beides trifft zu:
Der Lauf über `tests/test_dry_run_scenarios.py` und
`tests/test_orchestrator_resilience_evidence.py` reproduziert die im Dokument
genannte fokussierte Zahl (`29 passed`), und
`python3 -m pytest tests/ --collect-only -q` ergibt die im Dokument genannte
Gesamtzahl (1092). Beide Prüfungen habe ich selbst am aktuellen Arbeitsbaum
ausgeführt.

Die Korrektur geht an zwei Stellen über die Minimalforderung hinaus, und zwar
in die richtige Richtung: Die Herkunftskennzeichnung als agent-lokale
Codex-Evidenz macht die frühere implizite Autoritätsbehauptung explizit
unmöglich, und der Verzicht auf jede Laufzeitangabe entfernt eine volatile
Zahl, die der Arbeitsplan ohnehin nicht als Bestehensschwelle sehen will.

Ein Detail halte ich fest, ohne es als Befund zu führen: §5 beschreibt den
fokussierten Scope weiterhin in Prosa statt über die beiden Dateinamen. Die
Beschreibung ist eindeutig auflösbar — ich habe die gesammelten Testnamen
gegen sie gehalten —, aber sie ist nicht maschinell an den Befehl gebunden.
Das ist dieselbe Klasse wie das unten genannte Restrisiko und erfordert keine
weitere Arbeit an diesem Paket.

## 7. Fortbestand von C-01 bis C-19

Alle neunzehn bleiben geschlossen. Die Kontrolle ist hier nicht die
Wiederholung der vollen Branchprüfung — die hat mein Finalreview am Snapshot
`7674ee1d` geleistet —, sondern der Nachweis, dass der Delta keine
Regressionsfläche besitzt:

- Der Blobvergleich über alle 19 committed Paketpfade zeigt genau eine
  Abweichung gegenüber `HEAD`, und zwar das Dokument selbst (Q5).
- `src/`, `tests/` und `tests/fixtures/` sind damit bit-identisch mit dem
  Stand, den ich bereits vollständig geprüft und für den ich `C-01` bis
  `C-19` geschlossen habe.
- Die beiden von mir gefahrenen Läufe bestätigen die unveränderte Grünheit
  der Nachweisschicht: `29 passed` im fokussierten Verbund, 1092 sammelbare
  Tests im Repository.
- Der Delta berührt keine der Aussagen, an denen frühere Befunde hingen:
  Die Sicherheitstabelle in §2 mit der `C-18`-relevanten Scope-Gateidentität,
  die Manifestbindung aus §1 (`C-16`) und die Journeyaussage in §4 (`C-17`)
  sind wortgleich unverändert.

Eine konkret belegte Regression liegt nicht vor.

## 8. Restrisiko und Bruchbedingung

Größtes Restrisiko bleibt, dass die narrative Abschlussdokumentation an
nichts maschinell gebunden ist. `C-20` war die erste Realisierung genau
dieses Risikos: Ein Korrekturzyklus änderte Tests, das Dokument blieb stehen,
und kein Test las den Bericht. Die jetzige Korrektur repariert den Inhalt,
nicht den Mechanismus — Manifest, `EXPECTED_SCENARIOS`, `EVIDENCE_ORACLE`
und die Zahlen in §5 bleiben handgepflegt und werden weder aus dem
Arbeitsplan abgeleitet noch gegen einen Testlauf geprüft.

**Realistische Bruchbedingung:** Der nächste Zyklus ergänzt oder entfernt
einen Test — etwa beim Schließen eines künftigen Befunds. Die Suite bleibt
grün, das Manifest bleibt konsistent, und §5 nennt erneut eine Zahl, die zum
dann geprüften Stand nicht mehr passt. Auffallen würde das nur, wenn wieder
jemand die Zahl gegen einen eigenen Lauf hält.

## 9. Pre-Mortem

In drei Monaten scheitert dieses Paket am wahrscheinlichsten daran, dass der
Abschlussnachweis als Beleg zitiert wird, während sein Wortlaut erneut vom
Code abgekoppelt ist. Der Mechanismus ist derselbe wie bei `C-20`, nur die
betroffene Aussage ist eine andere: Nicht die Testzahl in §5 veraltet,
sondern eine Sicherheitszusage in §2 — etwa die Gateart oder der Pfadsatz der
Scopeverletzung — nachdem eine spätere Änderung die Produktion verschoben hat.
Die Übergangsmatrix würde eine solche Verschiebung rot melden, der Bericht
jedoch nicht; wer den Bericht liest statt die Matrix zu fahren, hielte die
alte Zusage weiter für gültig.

Zweitwahrscheinlich gilt die Producerverfolgung der Gateinventur als
abgeschlossen, obwohl sie zwei Syntaxformen kennt — konstante Zuweisung und
BoolOp-Fallback mit typisierter `error_code`-Menge. Eine dritte Form an
derselben Emissionsstelle bliebe unentdeckt, und die drei bestehenden
Mutationsproben schlügen nicht an, weil sie exakt die bekannten Formen
abbilden.

Drittwahrscheinlich wird der Arbeitsplan um eine neue Pflichtkante erweitert,
ohne Manifest und Oracles mitzuführen: Alle elf Bindungen blieben gegenseitig
konsistent und grün, und der ungemessene Pfad fiele erst im realen Betrieb auf.

## 10. Entscheidung

`C-20` ist geschlossen, `C-01` bis `C-19` bleiben geschlossen, und es ist kein
Claude-Befund mehr offen. Der Delta ist rein dokumentseitig, minimal, in
beiden Zahlen von mir reproduziert und in der Herkunftskennzeichnung
strenger als gefordert. Ich erteile die Branchfreigabe.

Commit des Korrekturdeltas, Merge und der nachgelagerte reale
Bootstrap-Smoke-Test bleiben ausdrücklich Sache des Nutzers beziehungsweise
des Orchestrators; eine Orchestrator-Attestierung ist mit dieser Freigabe
nicht behauptet.

```text
REVIEWER: claude
FINDING_STATUS: C-20 | CLOSED | Abschnitt 5 des providerfreien Abschlussnachweises nennt jetzt fuer den fokussierten Harnesslauf exakt 29 passed und fuer die vollstaendige Repositorymatrix exakt 1092 passed, beide ausdruecklich demselben finalen Paketstand zugeordnet und als agent-lokale Validierungsevidenz von Codex statt als Orchestrator-Attestierung gekennzeichnet. Die beanstandete Formulierung nach Abschluss aller Slice-3-Nacharbeiten und die veraltete Laufzeit 120.17s sind entfernt; eine Laufzeit wird gar nicht mehr genannt, was zugleich eine volatile Zeitschwelle vermeidet. Beide Zahlen habe ich am aktuellen Arbeitsbaum selbst reproduziert: der Lauf ueber tests/test_dry_run_scenarios.py und tests/test_orchestrator_resilience_evidence.py ergibt 29 passed in 1.83s bei einer Aufteilung von 7 plus 22, und pytest tests/ --collect-only -q sammelt 1092 Tests; die gesammelten Testnamen decken die Prosabeschreibung aus Parser, Gateidentitaet und den elf ausgefuehrten Szenarien vollstaendig. Der fachliche Delta beschraenkt sich nachweislich auf Abschnitt 5 dieses einen Dokuments: git diff gegen HEAD zeigt genau einen Hunk, und der Blobvergleich aller neunzehn committed Paketpfade weist nur dieses Dokument als geaendert aus, sodass Code, Tests, Fixtures und die uebrigen Dokumente bit-identisch bleiben. Die Aussagen zu externen Provider-Canaries und zum nachgelagerten realen Smoke-Test sind unveraendert.
REVIEW_EVIDENCE: selbst erhobener Branch-, HEAD-, Merge-Base- und Stagingstand, verifizierte Zweipfad-Arbeitsbaumgrenze und eigenstaendig nachgerechneter 20-Pfad-Worktree-Fingerprint 56610a48 mit Wiederholung unmittelbar vor der Schreiboperation, vollstaendiger Diff des Abschlussnachweises gegen HEAD, Blobvergleich aller neunzehn committed Paketpfade gegen HEAD mit genau einer Abweichung, eigener providerfreier fokussierter Lauf 29 passed in 1.83s mit Aufteilung 7 plus 22, eigene Repositorysammlung 1092 tests collected ohne Testlauf, Abgleich der gesammelten Testnamen gegen die Prosabeschreibung des Scopes, Kontrolle beider Canary- und Smoke-Test-Fundstellen auf Unveraendertheit, Pruefung aller neun Pflichtpunkte einzeln sowie Kontrolle, dass die fuer C-16, C-17 und C-18 tragenden Abschnitte des Dokuments wortgleich unveraendert sind | groesstes Restrisiko: die narrative Abschlussdokumentation bleibt an nichts maschinell gebunden, weder an das Manifest noch an einen Testlauf noch an den Arbeitsplan, sodass die jetzige Korrektur den Inhalt repariert, aber nicht den Mechanismus, der C-20 erst ermoeglicht hat | realistische Bruchbedingung: der naechste Zyklus ergaenzt oder entfernt einen Test, die Suite bleibt gruen und das Manifest konsistent, waehrend Abschnitt 5 erneut eine Zahl nennt, die zum dann geprueften Stand nicht mehr passt und nur durch einen erneuten manuellen Abgleich auffiele
PRE_MORTEM: In drei Monaten scheitert das Paket am wahrscheinlichsten daran, dass der Abschlussnachweis als Beleg zitiert wird, waehrend sein Wortlaut erneut vom Code abgekoppelt ist. Der Mechanismus ist derselbe wie bei C-20, nur die betroffene Aussage ist eine andere: nicht die Testzahl in Abschnitt 5 veraltet, sondern eine Sicherheitszusage in Abschnitt 2 wie die Gateart oder der Pfadsatz der Scopeverletzung, nachdem eine spaetere Aenderung die Produktion verschoben hat. Die Uebergangsmatrix meldete das rot, der Bericht nicht, und wer den Bericht liest statt die Matrix zu fahren, hielte die alte Zusage weiter fuer gueltig. Zweitwahrscheinlich gilt die Producerverfolgung der Gateinventur als abgeschlossen, obwohl sie nur konstante Zuweisung und BoolOp-Fallback kennt, sodass eine dritte Form an derselben Emissionsstelle unentdeckt bliebe und die drei Mutationsproben nicht anschluegen. Drittwahrscheinlich wird der Arbeitsplan um eine neue Pflichtkante erweitert, ohne Manifest und Oracles mitzufuehren, sodass alle elf Bindungen gegenseitig konsistent und gruen blieben und der ungemessene Pfad erst im realen Betrieb auffiele.
FINAL_APPROVAL: YES
STATUS: DONE
```
