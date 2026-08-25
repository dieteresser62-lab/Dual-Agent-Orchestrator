# Erneutes Codex-Gesamtreview – Native Agent Contract Closure

**Reviewdatum:** 24. August 2026

**Reviewbasis:** `c410eef` (`merge: complete native finding transition identity`)

**Reviewstand:** `06819bd` (`fix(native-contracts): close request-bound writer gaps`)

**Reviewmodus:** Manuelles, providerfreies Codex-Selbstreview außerhalb von
`run_task`. Es wurden weder Antigravity noch Claude aufgerufen. Produktcode und
Tests wurden nicht verändert.

## 1. Gegenstand und Methodik

Geprüft wurde der vollständige Diff `c410eef..06819bd` zusammen mit dem
freigegebenen Arbeitsplan, allen fünf Slice-Protokollen, den beiden historischen
Gesamtreviews, der Roadmap, den Reader- und Writerschemaquellen, dem
Corpusmanifest und den produktiven Live-/Recoverypfaden.

Die Prüfung konzentrierte sich auf:

- Vollständigkeit der request-spezifischen Writerschemas;
- Gleichlauf von Writerschema und lokaler Domäne;
- Findingeigentum, Offenstatus, Übergänge und Finalreview-Konvergenz;
- Request-, Bundle-, Parent-, Kontext- und Evidenzassetbindung;
- Live-, Record-Ahead- und Pre-Current-Diff-Policy-Recovery;
- Corpus-, Writerform- und Canary-Evidenz;
- historische Readerkompatibilität und Providerausnahmen;
- Dokumentations- und Roadmapkonsistenz.

Die bereits attestierte vollständige Matrix `1280 passed in 187.33s` wurde
nicht erneut ausgeführt. Kleine providerfreie Gegenproben wurden ausschließlich
zur Falsifikation konkreter Evidenzbehauptungen verwendet.

## 2. Disposition der bisherigen Codex-Blocker

### CR-03 – CLOSED

Der normative Transportprofiltext behandelt `--max-budget-usd` jetzt korrekt
als kostensteuernden, profilfremden Laufzeitparameter. Capability-Profil,
Arbeitsplan und Roadmap sind dazu widerspruchsfrei; die historische
Claude-Observation `C-25` blieb als Befundquelle unverändert.

### CR-04 – CLOSED

Alle produktiven nativen Live- und aktuellen Recoverypfade erzwingen nun das
konkrete, an den jeweiligen Request gebundene Writerschema vor der lokalen
Domänenkonvertierung:

- Claude live: `src/agent_runtime.py`, `run_native_review_agent()`;
- Codex live: `src/agent_runtime.py`, `run_native_codex_agent()`;
- Codex Record-Ahead: `src/orchestrator.py`,
  `recover_pending_native_codex()`;
- Claude Record-Ahead: `src/orchestrator.py`,
  `recover_pending_native_reviewer()`;
- Claude vor Current-Diff-Policy:
  `recover_pending_native_reviewer_before_policy()`.

Die Reihenfolge ist Reader-Baseline, konkretes Writerschema, Request-ID,
diagnostischer Callback und lokale Domäne. Writerungültige Bytes erreichen
weder ein autoritatives Resultat noch den Callback für validierte
Rohantworten. Writergültige, aber aufgrund einer ausdrücklich registrierten
lokalen Providerausnahme abgelehnte Bytes bleiben diagnostizierbar, erhalten
jedoch keine fachliche Autorität. Die Raw-first-Crashsicherheit und die
Unterdrückung doppelter Providerstarts bleiben erhalten.

Der Pre-Current-Diff-Policy-Pfad ist korrekt als Recovery eines aktuellen,
request-gebundenen Records behandelt und rekonstruiert sein Writerschema aus
dem aktuellen typisierten Workflowkontext. Echte Legacy- und Corpusdaten
bleiben von dieser Regel getrennt.

### CR-05 – CLOSED

Claude- und Codex-Bundles rechnen kanonische Requestbytes, Requestdigest und
präfixgebundene Request-ID unabhängig nach und vergleichen die tatsächlich
transportierte Kontextprojektion mit dem Bound Context. Der Responsevertrag ist
an die unveränderlichen kanonischen Writerschemabytes gebunden.

Claude-Repairrequests benötigen ihr echtes Parentbundle und binden
Parent-Request-ID, Kontext, Fingerprint und Responsevertrag. Codex-Evidenzassets
werden für Inline- und `content_ref`-Übertragung auf Eindeutigkeit,
Vollständigkeit, Pfadzuordnung, Bytezahl und Digest geprüft. Die ursprünglichen
Gegenbeispiele mit Kontextdrift, gefälschten Requestbytes, fehlendem Asset und
doppeltem Asset scheitern vor Providerstart.

Die durch `C-32` präzisierte Grenze ist korrekt: Der Codex-Request transportiert
nur offene Findings. Geschlossene Findings werden nicht als Requestfelder
erfunden; ihre Autorität bleibt in der append-only Workflow-Recordkette. Der
erweiterte Mirror-Drifttest deckt auch geschlossene Findings ab.

### CR-06 / C-30 – CLOSED hinsichtlich der HEAD-Stabilität

Alle sieben `live_canary`-Zeilen tragen jetzt den historischen `base_commit`,
der beim jeweiligen Lauf in die Request-ID einging. Der Livepfad friert den
aktuellen Commit einmal vor der Bundleerzeugung ein und gibt denselben Wert im
Canaryreport zurück. Providerfreie Rekonstruktionen verwenden den persistierten
Commit statt des lebenden Repository-`HEAD`.

Am Reviewstand wurden alle sieben Zeilen unabhängig rekonstruiert. Für jede
Zeile stimmen sowohl Request-ID als auch realer Writerschemadigest mit dem
Manifest überein:

| Provider | Writerform | Eingefrorener Commit | Request-ID | Writerschemadigest |
|---|---|---|---|---|
| Codex | plan | `7ba867dd246c` | identisch | identisch |
| Codex | implementation | `7ba867dd246c` | identisch | identisch |
| Codex | correction | `7ba867dd246c` | identisch | identisch |
| Codex | final_report | `7ba867dd246c` | identisch | identisch |
| Claude | plan | `7ba867dd246c` | identisch | identisch |
| Claude | convergence | `eb38a316a7ee` | identisch | identisch |
| Claude | final | `7ba867dd246c` | identisch | identisch |

Die ursprüngliche Selbstreferenz zum lebenden `HEAD` ist damit beseitigt. Das
folgende neue Finding betrifft nicht mehr die historische Commitbindung,
sondern die unvollständige maschinelle Bindung der ebenfalls manifestierten
Writerschemadigests.

## 3. Positiv bestätigte Abschlussinvarianten

- Reader-Baselines und das Register der 13 Providerausnahmen blieben
  unverändert; eine neue Ausnahme war für Slice 05 nicht erforderlich.
- Geschlossene oder fremde Findings sind keine zulässigen Ziele von Status-
  oder Klassenänderungen. Der lokale Code `finding-reference-not-open` bleibt
  als unabhängige zweite Schutzschicht erhalten.
- Der reguläre produktive Finalreview setzt zwar
  `allow_new_observations=True`, kann aber weder eine neue Observation erzeugen
  noch einen Blocker in eine Observation reklassifizieren. Writerschema und
  lokale Domäne verweigern den Angriff unabhängig voneinander; bereits zuvor
  bestehende Observations werden nicht fälschlich verboten.
- Das Corpus enthält 14 byte- und digestgebundene Fixtures. Die versionierte
  Archivseite wird aus dem Arbeitsbaum inventarisiert; `schema_only`,
  `request_bound` und `live_canary` bleiben getrennt.
- Das Claude-Konvergenzschema enthält
  `status_changes.items.oneOf` mit exakt der gebundenen offenen Finding-ID.
  Request-ID `58e98da5...`, Writerschemadigest `a86ebb51...` und
  Antwortdigest `f6a2eff2...` blieben unverändert.
- Repository- und Workflowstore-Abdrücke erfassen Dateien, Metadaten,
  Symlinkziele und fehlende Wurzeln und werden um externe Canaries fail-closed
  verglichen.
- Kein nativer Nutzpfad fällt auf Textmarkerparser oder einen textuellen
  Contract-Repair zurück.

## 4. Blocker CR-07 – Sechs Live-Canary-Writerschemadigests sind nicht fail-closed gebunden

Schweregrad: **BLOCKER**

Der aktuelle Manifestinhalt ist sachlich korrekt: Alle sieben real
rekonstruierten Writerschemadigests stimmen mit ihren
`writer_schema_sha256`-Feldern überein. Die dafür behauptete fail-closed
Evidenzkopplung ist jedoch nur für den Claude-Konvergenzcanary maschinell
abgesichert.

`tests/test_native_contract_probe.py::test_all_live_canaries_rebuild_from_frozen_base_not_current_head`
rekonstruiert alle sieben Bundles, vergleicht aber ausschließlich
`bundle.bound_context.request_id` mit der Manifest-Request-ID. Der aus
`bundle.provider_response_schema_json` berechenbare Writerschemadigest wird
nicht mit `writer_schema_sha256` verglichen.

`tests/test_native_contract_corpus.py::_validate_writer_form_evidence()` prüft
für einen `live_canary` ebenfalls nur:

- `status == "passed"`;
- Form und Zeichenmenge des `base_commit`;
- eine grobe Request-ID-Länge;
- die Länge des Antwortdigests.

Für `writer_schema_sha256` gilt formübergreifend lediglich
`len(...) == 64`. Nur der separate Konvergenztest bindet seinen eigenen
Schemadigest bitgenau.

Die providerfreie Gegenprobe ist eindeutig:

1. Manifest im Speicher laden.
2. Bei `codex/plan` ausschließlich `writer_schema_sha256` auf 64 Nullen setzen.
3. `_validate_writer_form_evidence(mutated_manifest, rows)` ausführen.
4. Ergebnis: **akzeptiert**, keine Assertion.

Danach wurden die realen Bundles erneut gebaut; alle sieben unveränderten
Manifestwerte sind heute korrekt. Der Defekt ist somit keine falsche aktuelle
Zahl, sondern eine ausführbare Lücke in der zugesagten fail-closed Kopplung:
Eine falsche oder versehentlich vertauschte 64-stellige Schemaangabe für sechs
der sieben Live-Nachweise bleibt dauerhaft grün.

Das verletzt die Slice-05-Anforderung, alle sieben Live-Canary-Zeilen
beweisorientiert zu inventarisieren, sowie Akzeptanzkriterium 13, nach dem
Writerformen und Evidenzklassifizierung vollständig und fail-closed gekoppelt
bleiben müssen. Es schwächt außerdem die paketweite Writerformbehauptung: Eine
Manifestzeile kann eine korrekte historische Request-ID mit einem beliebigen
Writerschemadigest kombinieren und dennoch als geschlossener
`live_canary`-Writerbeleg zählen.

### Akzeptanzkriterien für CR-07

1. Die Rekonstruktionsregression baut für jede der sieben
   `live_canary`-Zeilen das Bundle aus dem persistierten `base_commit` und
   verlangt gleichzeitig exakte Gleichheit von:
   - Request-ID;
   - SHA-256 der kanonischen `provider_response_schema_json`-Bytes;
   - dem jeweiligen Manifestfeld `writer_schema_sha256`.
2. Eine Negativmatrix manipuliert den Writerschemadigest jeder der sieben
   Zeilen einzeln und weist nach, dass die Evidenzprüfung jeweils fail-closed
   scheitert.
3. Request-ID, Writerschemadigest und `base_commit` werden als eine gemeinsame
   Evidenzbindung geprüft; eine bloße Längen-, Präfix- oder Zeichenmengenprüfung
   genügt nicht.
4. Die Korrektur benötigt keinen Provideraufruf und verändert weder historische
   Antwortbytes noch bestehende Request-IDs oder Schemadigests.
5. Nach der Korrektur sind die fokussierten Corpus-/Canarytests und die
   vollständige Repositorymatrix grün; `git diff --check` bleibt sauber.

## 5. Restrisiken ohne zusätzlichen ausführbaren Befund

- Für die sieben historischen Live-Canaries sind die Antwortbytes nicht lokal
  versioniert. Ihre `response_sha256`-Werte sind deshalb Auditangaben und keine
  aus dem Repository allein rekonstruierbaren Corpusbelege. Diese Grenze ist
  dokumentiert und wird nicht mit `request_bound` verwechselt.
- Der Pre-Current-Diff-Policy-Recoverypfad rekonstruiert das Writerschema aus
  dem typisierten aktuellen Workflowkontext, weil das vollständige historische
  Requestdokument dort nicht persistiert ist. Die eindeutige Request- und
  Response-Digestbindung begrenzt diese aktuelle Recoveryklasse; eine
  Erweiterung ihrer Kontextfelder muss künftig symmetrisch in Rekonstruktion
  und Tests nachgezogen werden.
- Geschlossene Codex-Findings sind bewusst nicht Bestandteil der
  Transportprojektion. Jede spätere lokale Auswertung, die geschlossene
  Findings semantisch verwendet, bleibt deshalb auf den vorgelagerten
  Record-vs.-Mirror-Guard angewiesen.

## 6. Pre-Mortem

Die wahrscheinlichste Fehlerursache in drei Monaten wäre, dass eine neue oder
aktualisierte Canaryzeile mit korrektem `base_commit` und korrekter Request-ID,
aber kopiertem Writerschemadigest einer anderen Writerform eingecheckt wird.
Alle heutigen Tests außer dem speziellen Konvergenztest blieben grün. Das
Manifest würde den Nachweis einer konkreten Writerform behaupten, tatsächlich
aber nur den Transportrequest einer Form und das Schema einer anderen Form
kombinieren. Spätere Reviews würden die acht grünen Evidenzzeilen zählen und
damit genau jene mathematische Geschlossenheit voraussetzen, die das Manifest
nicht mehr belegt.

## 7. Gesamturteil

Slice 05 schließt `CR-03` bis `CR-06` fachlich und behebt die zuvor
ausführbaren Runtime-, Bundle-, Finalreview- und HEAD-Bindungsdefekte. Die
produktiven JSON-Vertragsgrenzen sind deutlich stärker und die aktuelle
Manifestbelegung ist inhaltlich korrekt.

Das Arbeitspaket ist dennoch noch nicht endgültig freigabefähig. `CR-07` ist
ein konkreter, providerfrei reproduzierbarer Defekt in der fail-closed
Writerformevidenz. Er benötigt eine kleine, eng begrenzte Korrektur der
Canary-/Corpusregressionen, keine Änderung des Produktionsvertrags und keinen
externen Providerlauf.

Codex erteilt grundsätzlich keine Freigabe für die eigene Implementierung. Das
Selbstreview empfiehlt wegen `CR-07` ausdrücklich eine weitere Korrekturrunde
und danach ein unabhängiges Claude-Gesamtreview.

CODEX_SELF_REVIEW_RESULT: CHANGES_REQUIRED

STATUS: DONE
