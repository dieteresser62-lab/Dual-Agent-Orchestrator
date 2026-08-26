# Codex-Gesamtreview – Native-only-Transport, Textparser-Retirement und Evidenzeffizienz

> Archiviert nach Abschluss des Arbeitspakets; nichtautoritative historische Entwicklungsevidenz.

## 1. Reviewidentität und Grenze

- Reviewer: Codex
- Reviewart: manuelles, unabhängiges und adversariales Gesamtreview außerhalb
  von `run_task`
- Basis: `9a7da9c66ee187a9044fbf3ff3e92a6b24e216cd`
- geprüfter Abschlussstand: `5a9b21f` (`feat(orchestrator): compact native
  provider evidence`)
- geprüfte Differenz: `git diff 9a7da9c..5a9b21f`
- separater Claude-Bericht:
  `docs/internal/native-only-transport-parser-retirement-und-evidenzeffizienz-final-review-claude.md`

Der Claude-Bericht wurde für dieses Review weder gelesen noch vorausgesetzt.
Der Arbeitsbaum war zu Reviewbeginn frei von fachlichen Änderungen. Dieser
Bericht selbst ist die einzige durch das Codex-Gesamtreview erzeugte
Repositorydatei.

## 2. Methode

Geprüft wurden der vollständige Branchdiff, der freigegebene Arbeitsplan, alle
drei Slice-Berichte, die aktiven Rootverträge, README und Roadmap sowie die
geänderten Schema-, Produktiv-, Test- und Fixturedateien. Die Prüfung umfasste
insbesondere:

- statische Suche nach verbliebenen Transportflags, produktiven
  Markerresultatparsern, Textadaptern und Contract-Repair-Pfaden;
- Abgleich der nativen Pflichtbindung in Workflowstate, Artifactmodell,
  Artifact-Schema, Migration/Resume und Produktionsdriver;
- Kontrolle der nativen Adapterregistry und der erhaltenen generischen bzw.
  Claude-spezifischen Adapterfläche;
- Nachvollzug von Record-ahead, Rohantwortbindung, Request-ID,
  Writerschemadigest und autoritativer Finding-Rekonstruktion;
- Analyse der neuen Slice- und Korrekturpakete, ihrer Pfad-, Finding-, Delta-
  und Digestbindung sowie der Deduplizierungsgrenzen;
- Prüfung der operationenbezogenen Markdownprojektion auf unbekannte Usage,
  echte Nullwerte, Laufzeit, Attempts, Retrystatus, Eingabezeichen und
  Eingabebytes;
- unabhängige Nachrechnung der produktiven Providerkomponenten und der
  Baseline-Differenz statt Übernahme der im Slice-Bericht genannten Werte.

Die vollständige Repositorymatrix wurde nicht erneut ausgeführt. Der
dokumentierte autoritative Stand lautet `1007 passed`. Als gezielte,
providerfreie Falsifikationsmatrix liefen:

```text
python3 -m pytest \
  tests/test_native_transport_retirement.py \
  tests/test_provider_input_efficiency.py \
  tests/test_artifact_projection.py \
  tests/test_native_codex_request.py \
  tests/test_native_review_request.py \
  -q -p no:cacheprovider

79 passed in 4.12s
```

Es wurden keine externen Provider-Canaries gestartet.

## 3. Ergebnisübersicht

| Prüfdimension | Ergebnis | Evidenz |
|---|---|---|
| Native-only-Auswahl | bestanden | Die vier früheren Native-/No-Native-Flags fehlen in CLI und Runtime; unbekannte Flags werden abgewiesen. Registry und Workflow besitzen nur die nativen Codex-/Claude-Pfade. |
| Textparser- und Repair-Retirement | bestanden | Unter `src/` fehlen die pensionierten Resultatparser, Textadapter, Normalisierer und Review-Repair-Operationen. Das aktive Reviewrequest-Schema weist den Repair-Anfragetyp ab. Task-, Plan-, JSON-, Quota- und Providerdiagnoseparser bleiben erhalten. |
| Pflichtbindung und Resume | bestanden | `structured-v2` verlangt beide aktuellen Transportbindungen. Agentresultat- und Reviewrecords verlangen Transportversion, Request-ID und Rohantwortdigest. Alte oder unvollständige Protokolle werden vor Fortsetzung als unsupported abgewiesen. |
| Slice-Ausführungspaket | bestanden | Das Paket bindet genau einen Slice, Quellplanpfad und -digest, Ziel, Kriterien, sortierte Pfade und explizite Querverweise; Geschwister-Slices und der vollständige Plantext fehlen. |
| Korrekturpaket | bestanden | Nur die exakt im aktuellen Work Unit benannten offenen Findings gelangen in Kontext und Paket. Delta und aktueller Fingerprint stammen aus demselben kanonischen Change-Snapshot; eine abweichende autoritative Findingmenge erzwingt vor Providerstart einen Request-Neubau. |
| Deduplizierung | bestanden | Evidenz-IDs, Sourcepaths und Inhaltsdigests werden pro Request geprüft; die finale Providerkomponentenmenge weist content-identische Komponenten vor Prozessstart ab. |
| Recordbasierte Projektion | bestanden | Zeichen, Bytes, Laufzeit, Attempts, offene Attempts, Retryklassifikation und Usage stammen aus Records. Vollständig fehlende Usage erscheint als `unknown`; Zeichen werden nicht zu Tokens umgedeutet. |
| Eingabereduktionsnachweis | **nicht bestanden** | Der produktive Aufbau ist reduziert, aber die zentrale Regression erzwingt eine andere Inline-/Assetzustellung als der Produktionspfad. Siehe `CR-01`. |
| Dokumentationssynchronität | teilweise bestanden | README und Roadmap beschreiben den produktiven Mechanismus zutreffend; die Behauptung eines abgeschlossenen reproduzierbaren Betriebsnachweises ist wegen `CR-01` noch nicht vollständig abgesichert. |

## 4. Blocker

### CR-01 – Die zentrale Effizienzregression zertifiziert nicht die produktive Requestform

`tests/test_provider_input_efficiency.py::_current_components()` ruft
`build_native_codex_request(..., inline_evidence_chars=10)` auf. Produktiv
setzen weder `src/workflow.py` noch die nativen Adapter diesen Parameter; dort
gilt `DEFAULT_INLINE_EVIDENCE_CHARS = 24_000`.

Die Ausführungspakete liegen im geprüften Szenario unter der produktiven
Grenze. Der echte Builder liefert deshalb für beide betroffenen Operationen
genau diese Komponenten:

```text
codex_implementation: [stdin_prompt, response_schema]
codex_correction:     [stdin_prompt, response_schema]
```

Die zentrale Regression bilanziert dagegen vier Komponenten, weil sie
Systemrichtlinie und Ausführungspaket künstlich in zwei Assets auslagert. Eine
unabhängige providerfreie Nachrechnung mit demselben synthetischen Kontext,
aber dem produktiven Builderdefault, ergab:

| Operation | aktuelle Zeichen/Bytes | Reduktion gegenüber Baseline | unveränderte Komponente |
|---|---:|---:|---|
| `codex_implementation` | 7.634 | 47.687 | `response_schema` |
| `codex_correction` | 7.781 | 47.637 | `response_schema` |

Die Produktwirkung ist damit real und sogar größer als im bestehenden Test.
Der Defekt liegt im Nachweis: Eine Änderung an Inline-Grenze oder Paketgröße
kann den produktiven Transport von inline auf Asset umschalten, während der
heutige Test wegen seiner fest erzwungenen Zehnergrenze unverändert grün
bleibt. Das widerspricht dem Slice-3-Ziel eines reproduzierbaren
Betriebsnachweises und lässt Claudes `C-09` mit seinem konkreten
Akzeptanzkriterium offen.

Nach den Finalreviewregeln darf Codex ein Claude-Finding weder schließen noch
reklassifizieren. Da vor der branchweiten Freigabe kein Claude-Finding offen
bleiben darf, ist dieser ausführbare Nachweisdefekt ein Abschlussblocker.

Konkreter Akzeptanztest:

1. `_current_components()` verwendet ohne explizite Inline-Grenze den
   produktiven Default.
2. Der Betriebsnachweis verlangt für `codex_implementation` und
   `codex_correction` exakt die Komponentenmenge
   `{"stdin_prompt", "response_schema"}`.
3. Für beide Operationen bleiben Komponentenbilanz, positive Zeichen- und
   Bytereduktion sowie Namens- und Digestgleichheit der unveränderten
   `response_schema` erhalten.
4. Ein separater Test darf mit ausdrücklich kleiner Grenze die Assetzustellung
   prüfen, muss sie aber als synthetischen Grenzfall kennzeichnen und darf sie
   nicht als produktiven Effizienznachweis verwenden.
5. Claude prüft und disponiert anschließend `C-09` selbst.

## 5. Claude-Finding-Inventur

| Finding | Stand im geprüften Branch | Codex-Abgleich |
|---|---|---|
| `C-01` | geschlossen | Pflichtfelder und Konstruktorinventur sind im Gesamtstand vorhanden. |
| `C-02` | geschlossen | Repair-Requestzweig und produktiver Repairbetrieb sind entfernt. |
| `C-03` | geschlossen | Baseline und Lock existieren und sind an den Vorzustand gebunden. |
| `C-04` | geschlossen | Gemeinsame Adapterfläche und Claude-spezifischer Capability-Smoke sind korrekt getrennt. |
| `C-05` | geschlossen | Fixture-/Lock-Lebenszyklus ist über Slice 1 und 2 geschützt. |
| `C-06` | geschlossen | Codex behält neutralen Reviewer-/Workspace-Protocolanteil, ohne Claude-spezifischen Smoke zu erben. |
| `C-07` | geschlossen | Fixture und Lock blieben nach Slice 1 bytegleich; ihre reviewten Digests sind weiterhin gebunden. |
| `C-08` | geschlossen | Die drei zusätzlichen Slice-2-Testpfade sind dokumentierte mechanische Folgeänderungen; Slice 3 verändert sie nicht. |
| `C-09` | **offen** | Akzeptanztest ist nicht umgesetzt; siehe `CR-01`. Nur Claude darf das Finding schließen oder eskalieren. |

## 6. Gesamtentscheidung

Der native-only Transportcutover, das Resultatparser-Retirement und die
produktive Eingabereduktion sind im geprüften Branch weitgehend konsistent und
providerfrei gut abgesichert. Der Branch kann dennoch noch nicht final
freigegeben werden: Der einzige zentrale, eingefrorene Effizienznachweis misst
eine künstlich erzwungene Zustellungsform, während das dazugehörige
Claude-Finding `C-09` offen ist.

REVIEWER: codex
NEW_FINDING: CR-01 | BLOCKER | Der zentrale Effizienztest erzwingt mit `inline_evidence_chars=10` eine nicht produktive Assetzustellung und zertifiziert dadurch nicht die im Betrieb verwendete Zwei-Komponenten-Requestform. | Nutze im Betriebsnachweis den produktiven Builderdefault, verlange je Implementierungs- und Korrekturoperation exakt `stdin_prompt` plus `response_schema`, erhalte die vollständige Zeichen-/Bytebilanz sowie die Digestgleichheit der unveränderten Schema-Komponente und trenne einen optionalen synthetischen Assetgrenztest ausdrücklich davon ab.
REVIEW_EVIDENCE: Vollständiger Branchdiff, Plan und drei Slice-Berichte; Native-only-CLI/Registry/Workflow; Parser- und Repair-Retirement; strukturierte Pflichtbindung und unsupported Resume; Record-ahead und Requestneubindung; Slice-/Korrekturpakete; Evidenz- und Providerkomponenten-Deduplizierung; Baseline-/Lock-Verbund; produktive Komponentenform unabhängig nachgerechnet; recordbasierte Metrikprojektion; 79 gezielte providerfreie Regressionen | Größtes Restrisiko neben CR-01 ist eine künftige Paketvergrößerung über die Inline-Grenze, die Komponentenform und Messvergleich gleichzeitig ändert | Der Branch bricht in drei Monaten am wahrscheinlichsten dadurch, dass ein wachsendes Slicepaket unbemerkt von inline auf Asset wechselt, während der fest auf zehn Zeichen gesetzte Test weiterhin denselben synthetischen Zustand zertifiziert.
PRE_MORTEM: Eine spätere Erweiterung der Ausführungspakete überschreitet die produktive Inline-Grenze. Der reale Providerinput wechselt seine Komponentenform, aber der heutige Test bleibt grün, weil er diese Form schon immer künstlich erzwungen hat; Reduktionszahlen, Betriebsprojektion und dokumentierte Nachweise driften auseinander.
FINAL_APPROVAL: NO
STATUS: DONE
