# Native Agent Contract Closure – Slice 06

**Titel:** Fail-closed Bindung aller Live-Canary-Writerschemadigests

**Basiscommit:** `06819bd` (`fix(native-contracts): close request-bound writer gaps`)

**Auslöser:** `CR-07` aus
`native-agent-contract-closure-final-self-review-codex-06819bd.md`

**Status:** Implementiert; Claude-Review ausstehend

## 1. Ziel

Slice 06 schließt ausschließlich die im Codex-Gesamtreview nachgewiesene
Evidenzlücke. Die sieben vorhandenen `live_canary`-Zeilen enthalten bereits
korrekte historische `base_commit`-, Request-ID- und Writerschemadigestwerte.
Die Tests banden jedoch für sechs Zeilen nur die Request-ID bitgenau; ein
beliebiger anderer 64-stelliger `writer_schema_sha256` blieb grün.

Der Slice verändert weder Produktionscode noch Providerverträge,
Manifestwerte, historische Request-IDs oder Antwortbytes. Er verschärft nur
den providerfreien Nachweis, mit dem eine Manifestzeile als geschlossener
Live-Writerbeleg zählt.

## 2. Exakter Änderungspfad

- `tests/test_native_contract_corpus.py`
- `tests/test_native_contract_probe.py`
- `docs/internal/ORCHESTRATOR_ROADMAP_PHASE_2_PLUS.md`
- `docs/internal/native-agent-contract-closure-slice-06-review.md`

## 3. Verbindliche Invarianten

1. Jede `live_canary`-Zeile wird aus ihrem eigenen persistierten
   `base_commit` mit dem kanonischen Codex- beziehungsweise Claude-Bundlebuilder
   rekonstruiert.
2. Der Nachweis vergleicht gleichzeitig und bitgenau:
   - die rekonstruierte Request-ID mit `evidence.request_id`;
   - SHA-256 der kanonischen `provider_response_schema_json`-Bytes mit
     `writer_schema_sha256`.
3. Der lebende Repository-`HEAD` trägt keine historische
   Gleichheitsbehauptung.
4. Eine Negativmatrix manipuliert jede der sieben
   `writer_schema_sha256`-Angaben einzeln und muss in jedem Fall fail-closed
   scheitern.
5. Längen-, Präfix- und Zeichenmengenprüfungen bleiben zusätzliche
   Strukturchecks, ersetzen aber keine Inhaltsgleichheit.
6. `schema_only`, `request_bound` und `live_canary` bleiben getrennte
   Evidenzklassen.
7. Externe Provider werden nicht aufgerufen; alle Nachweise sind aus dem
   versionierten Repository rekonstruierbar.

## 4. Implementierung

### 4.1 Gemeinsame Evidenzprüfung

`_validate_writer_form_evidence()` lädt den kanonischen Probe-Bundlebuilder und
rekonstruiert nun jede Live-Canary-Writerform aus dem in ihrer Manifestzeile
gespeicherten `base_commit`. Die Prüfung verlangt sowohl exakte Request-ID als
auch den SHA-256 der tatsächlich erzeugten kanonischen Writerschemabytes.

Damit wird die Evidenzzeile als gemeinsames Tripel aus Basiscommit, Request-ID
und Writerschemadigest geprüft. Ein sachlich richtiger Request kann nicht mehr
mit dem Schema einer anderen Form oder einem synthetischen 64-Zeichen-Wert
kombiniert werden.

### 4.2 Siebenfache Negativmatrix

Ein parametrisierter Test verändert nacheinander genau den Writerschemadigest
von:

1. Codex Plan;
2. Codex Implementierung;
3. Codex Korrektur;
4. Codex Finalbericht;
5. Claude Planreview;
6. Claude Konvergenzreview;
7. Claude Finalreview.

Jede Mutation behält Provider, Writerform, Basiscommit, Request-ID und alle
anderen Manifestfelder unverändert. Die gemeinsame Evidenzprüfung muss für
jeden Fall eine Assertion auslösen. Damit falsifiziert die Matrix exakt die
von `CR-07` belegte Lücke.

### 4.3 Doppelte positive Bindung

Der bestehende HEAD-Drifttest in `test_native_contract_probe.py` vergleicht für
alle sieben Zeilen zusätzlich den aus dem eingefrorenen Bundle berechneten
Writerschemadigest mit dem Manifest. Der absichtlich abweichende lebende
`HEAD` bleibt dabei aktiv; sowohl Request-ID als auch Schemadigest stammen aus
dem persistierten Kontext.

## 5. Akzeptanzkriterien

1. Alle sieben unveränderten Live-Canary-Zeilen rekonstruieren Request-ID und
   Writerschemadigest bitgenau.
2. Jede der sieben einzelnen Digestmanipulationen wird fail-closed erkannt.
3. Eine 64-stellige synthetische Digestangabe genügt nicht mehr, um als
   geschlossener Writerformnachweis zu zählen.
4. Kein Manifestwert und keine historische Rohantwort wird geändert.
5. Kein Produktiv-, Adapter-, Runtime-, Schema-, State- oder Recoverypfad wird
   geändert.
6. Die fokussierte Corpus-/Canarymatrix und die vollständige Repositorysuite
   sind grün.
7. `git diff --check` ist sauber.

## 6. Validierung

- Fokussiert:
  `python3 -m pytest tests/test_native_contract_corpus.py tests/test_native_contract_probe.py -v -p no:cacheprovider`
- Ergebnis: `14 passed in 1.98s`.
- Vollständig:
  `python3 -m pytest tests/ -v -p no:cacheprovider`
- Ergebnis: `1287 passed in 198.03s`.
- `git diff --check`: sauber.
- Externe Providerstarts: keine.

## 7. Reviewgrenze

Claude soll ausschließlich Slice 06 gegen `CR-07` prüfen. Ein ausführbarer
Defekt ist ein Blocker. Da dies eine Konvergenzkorrektur ist, darf der Review
keine neue Observation erzeugen. Zukunftsideen und Restrisiken gehören in
`REVIEW_EVIDENCE`.

IMPLEMENTATION_READY: 06 | YES

STATUS: DONE
