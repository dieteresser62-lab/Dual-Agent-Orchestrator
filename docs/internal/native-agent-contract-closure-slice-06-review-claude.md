REVIEWER: claude

# Claude Slice-Review 06 – Fail-closed Bindung aller Live-Canary-Writerschemadigests

Datum: 24. August 2026 · Reviewer: Claude · Modus: read-only, adversarial

- Basiscommit: `06819bd` (`fix(native-contracts): close request-bound writer gaps`)
- Slice-Dokument: `docs/internal/native-agent-contract-closure-slice-06-review.md`
- Auslöser: `CR-07` aus
  `docs/internal/native-agent-contract-closure-final-self-review-codex-06819bd.md`
- Konvergenzkorrektur: keine neue `OBSERVATION`; ein neu entdeckter
  ausführbarer Defekt wäre ein `BLOCKER`, nicht ausführbare Restrisiken stehen
  in `REVIEW_EVIDENCE`.

Dieses Dokument ist eigenständig und verändert weder das Slice-06-Protokoll
noch frühere Reviews. Produktcode und Tests wurden nicht angefasst. Die
vollständige Suite wurde nicht erneut ausgeführt und kein externer Canary
wiederholt; ausgeführt wurden die fokussierte Zwei-Datei-Matrix und drei
gezielte providerfreie Falsifikationen.

## 1. Vorbemerkung zur Einstufung

`CR-07` beschreibt genau den Befund, den ich in meinem Gesamtreview zu
`06819bd` als ersten Punkt in `REVIEW_EVIDENCE` festgehalten und dessen
Manipulationspfad ich dort reproduziert hatte: Für sechs der sieben
`live_canary`-Zeilen war `writer_schema_sha256` maschinell nur längengeprüft.

Ich hatte das ausdrücklich **nicht** als Blocker eingestuft, mit vier Gründen:
alle sieben Digests sind sachlich korrekt, die Wahrheit ist über die aus dem
eingefrorenen `base_commit` reproduzierte `request_id` und deren
`response_contract.schema_sha256` unabhängig vom Manifestfeld
wiederherstellbar, kein Produktivpfad liest das Manifest, und der freigegebene
Plan verlangt die maschinelle Writerschemavalidierung ausdrücklich nur für
`request_bound`-Paare. Codex hat denselben Sachverhalt als Blocker eingestuft —
sein eigener Text hält dabei fest, dass der Manifestinhalt sachlich korrekt
ist.

Die Differenz betraf also die Schwere, nicht den Sachverhalt. Für dieses Review
ist das ohne Folgen: Die Härtung ist in jedem Fall zulässig, sie verschärft
ausschließlich einen Nachweis, und sie schließt exakt die Bruchbedingung, die
ich in meinem Gesamtreview benannt und in der zweiten Variante meines
Pre-Mortems beschrieben hatte. Ich prüfe sie hier auf Korrektheit, Minimalität
und Regressionsfreiheit.

## 2. Änderungsumfang und Minimalität

`git diff 06819bd` umfasst drei Dateien, 69 hinzugefügte und 2 entfernte
Zeilen:

| Datei | Zeilen |
|---|---|
| `tests/test_native_contract_corpus.py` | +56 |
| `tests/test_native_contract_probe.py` | +4 |
| `docs/internal/ORCHESTRATOR_ROADMAP_PHASE_2_PLUS.md` | +11/−2 |

Verifiziert: `git diff 06819bd --name-only -- tests/fixtures src schemas scripts`
ist **leer**. Es wurde kein Produktiv-, Adapter-, Runtime-, Schema-, State-,
Recovery- oder Providerpfad geändert, kein Manifestwert, keine historische
Rohantwort und kein Digest. Der Slice bleibt exakt in seinem deklarierten
Änderungspfad — die vierte Datei ist das Slice-Protokoll selbst.

Corpus- und Evidenzstruktur unverändert: acht Writerformen (sieben
`live_canary`, ein `request_bound`), 14 Fixtures (elf `schema_only`, drei
`request_bound`), und `base_commit` erscheint weiterhin ausschließlich in
`live_canary`-Zeilen. Die Evidenzklassen bleiben getrennt.

## 3. Prüfung der Implementierung

### 3.1 Gemeinsame Evidenzprüfung

`_validate_writer_form_evidence()` rekonstruiert für jede `live_canary`-Zeile
das Bundle über den kanonischen Probe-Builder aus dem in **ihrer eigenen**
Manifestzeile gespeicherten `base_commit` und verlangt anschließend beides:

- `bundle.bound_context.request_id == evidence["request_id"]`;
- `_sha256(bundle.provider_response_schema_json) == item["writer_schema_sha256"]`.

Damit wird die Zeile als gemeinsames Tripel aus Basiscommit, Request-ID und
Writerschemadigest geprüft. Die bisherigen Struktur- und Längenchecks bleiben
zusätzlich erhalten und ersetzen nichts.

### 3.2 Siebenfache Negativmatrix

`test_live_canary_writer_schema_digest_mismatch_fails_closed` ist über alle
sieben Zeilen parametrisiert, verändert je Fall ausschließlich
`writer_schema_sha256` und lässt Provider, Writerform, `base_commit`,
Request-ID und alle übrigen Felder unangetastet. Der Mutationswert wird
defensiv gewählt (`"0"*64`, andernfalls `"1"*64`), sodass die Mutation den Wert
garantiert ändert.

### 3.3 Doppelte positive Bindung

`test_all_live_canaries_rebuild_from_frozen_base_not_current_head` vergleicht
jetzt zusätzlich den aus dem eingefrorenen Bundle berechneten Schemadigest mit
dem Manifest — und zwar innerhalb des Tests, der `_head` bewusst auf einen
abweichenden Wert monkeypatcht. Die stärkere der beiden Bindungen läuft damit
unter einem absichtlich falschen `HEAD`.

## 4. Adversariale Gegenproben

Drei Falsifikationen, alle providerfrei:

**(1) Scheitert die Negativmatrix aus dem richtigen Grund?** Der Test behauptet
nur `pytest.raises(AssertionError)`; eine frühere Strukturassertion hätte ihn
vakuum-grün machen können. Ich habe die Fehlerstelle ausgelesen: Für
`codex/plan` und `claude/final` feuert tatsächlich die neue Assertion
`_sha256(bundle.provider_response_schema_json)`, nicht ein vorgelagerter
Längen- oder Formatcheck. `"0"*64` passiert alle Strukturchecks und wird erst
an der Inhaltsgleichheit gestoppt — genau wie beabsichtigt.

**(2) Ist es echte Inhaltsgleichheit oder nur Strukturprüfung?** Ich habe die
Writerschemadigests zweier realer Formen (`codex/plan` ↔ `codex/correction`)
gegeneinander getauscht, also **gültige** 64-stellige Digests an der falschen
Zeile platziert. Die Prüfung weist den Tausch ab. Damit ist die in Slice-06
§4.1 behauptete Eigenschaft belegt: Ein sachlich richtiger Request kann nicht
mehr mit dem Schema einer anderen Form kombiniert werden.

**(3) Wird `C-30` still zurückgeholt?** Das war meine wichtigste Sorge: Die
Corpusprüfung ruft jetzt einen Bundlebuilder auf, der ohne expliziten
`base_commit` auf den lebenden `HEAD` zurückfällt. Ich habe `_head` im
Probe-Modul so ersetzt, dass jeder Aufruf sofort fehlschlägt, und
`_validate_writer_form_evidence()` über das vollständige Manifest laufen
lassen: Die Prüfung läuft **ohne einen einzigen `_head`-Aufruf** durch. Der
`base_commit or _head(...)`-Kurzschluss greift, die historische Rekonstruktion
bleibt HEAD-unabhängig, und die Wurzel von `C-30` ist nicht regressiert.

**Fokussierte Matrix:** `tests/test_native_contract_corpus.py` und
`tests/test_native_contract_probe.py` ergeben `14 passed in 2.07s` — die sieben
neuen Negativfälle plus die sieben bestehenden Tests, konsistent mit der
Attestierung des Slices.

## 5. Akzeptanzkriterien

| # | Kriterium | Befund |
|---|---|---|
| 1 | Alle sieben Zeilen rekonstruieren Request-ID und Digest bitgenau | erfüllt; im Gesamtreview zu `06819bd` und hier erneut verifiziert |
| 2 | Jede der sieben Digestmanipulationen wird fail-closed erkannt | erfüllt; Fehlerstelle stichprobenweise als die richtige Assertion belegt |
| 3 | Synthetischer 64-Zeichen-Digest genügt nicht mehr | erfüllt; zusätzlich gilt das auch für den Cross-Form-Tausch echter Digests |
| 4 | Kein Manifestwert und keine historische Rohantwort geändert | erfüllt; `tests/fixtures` im Diff leer |
| 5 | Kein Produktiv-, Adapter-, Runtime-, Schema-, State- oder Recoverypfad geändert | erfüllt; `src`, `schemas`, `scripts` im Diff leer |
| 6 | Fokussierte Matrix und vollständige Suite grün | fokussiert selbst nachgerechnet (`14 passed`); vollständige Suite (`1287 passed`) attestiert, nicht erneut ausgeführt |
| 7 | `git diff --check` sauber | attestiert |

Die sieben verbindlichen Invarianten aus §3 des Slices sind ebenfalls erfüllt,
einschließlich Invariante 3 (kein `HEAD` in einer historischen
Gleichheitsbehauptung), die ich gesondert falsifiziert habe, und Invariante 7
(keine externen Providerstarts; der Diff enthält keinen Canarylauf).

## 6. Wirkung auf mein Gesamtreview zu `06819bd`

Mein `FINAL_APPROVAL: YES` zu `06819bd` bleibt gültig — Slice 06 verschärft
ausschließlich einen Nachweis und lockert nichts. Zwei dort dokumentierte
Punkte sind damit erledigt:

- Restrisiko 1 aus `REVIEW_EVIDENCE` (sechs Digests nur längengeprüft) ist
  geschlossen;
- die dort benannte Bruchbedingung und die zweite Variante meines Pre-Mortems
  — eine künftig ergänzte oder ersetzte Canaryzeile mit von Hand eingetragenem
  Schemadigest — greifen nicht mehr, weil eine neue Zeile die Rekonstruktion
  aus ihrem eigenen `base_commit` bestehen muss.

Unverändert bestehen bleiben die übrigen Restrisiken: die Reihenfolgeabhängigkeit
zwischen Writerprüfung, Raw-Callback und Domänenkonvertierung, die lokale
Unverifizierbarkeit der `response_sha256`-Werte und die nicht aus einem frischen
Klon ableitbare `.orchestrator`-Herkunft eines Teils der Corpusinventur.

## 7. Urteil

Der Slice ist minimal, zielgenau und verlässt seinen Änderungspfad nicht. Er
verändert keine Evidenzwerte, sondern nur die Strenge ihrer Prüfung, und er
holt die zuvor eliminierte HEAD-Abhängigkeit nachweislich nicht zurück. Die
Negativmatrix scheitert aus dem richtigen Grund und trägt über den
Cross-Form-Tausch mehr, als sie behauptet. Es verbleibt kein ausführbarer
Defekt.

REVIEW_EVIDENCE: Minimalität und Pfadtreue des Diffs einschließlich Nachweis, dass `src`, `schemas`, `scripts` und `tests/fixtures` unberührt sind; Unverändertheit von Manifestwerten, historischen Rohantworten, Evidenzklassen und der Acht-Writerform-Struktur; gemeinsame Tripelbindung aus `base_commit`, Request-ID und Writerschemadigest für alle sieben `live_canary`-Zeilen; Fehlerursache der siebenfachen Negativmatrix stichprobenweise als die neue Digestassertion statt eines vorgelagerten Strukturchecks belegt; echte Inhaltsgleichheit über einen Cross-Form-Tausch gültiger Digests statt bloßer Strukturprüfung; Abwesenheit jeder `_head`-Nutzung in der historischen Rekonstruktion durch Sabotage der Funktion nachgewiesen; doppelte positive Bindung unter absichtlich abweichendem `HEAD`; fokussierte Zwei-Datei-Matrix selbst nachgerechnet | Die Evidenzprüfung importiert und rekonstruiert jetzt bei jedem Aufruf den Probe-Bundlebuilder, wodurch die Corpus-Testdatei an `scripts/native_contract_probe.py` gekoppelt ist; eine spätere Signaturänderung der Canary-Builder oder ein Wegfall des `base_commit`-Parameters würde die Evidenzprüfung brechen oder — schlimmer — bei einem stillschweigenden Default wieder auf den lebenden `HEAD` zurückfallen, was heute nur durch den Kurzschluss `base_commit or _head(...)` verhindert wird | Jemand refaktoriert die Canary-Builder, entfernt oder benennt den `base_commit`-Parameter um und lässt den Fallback auf `_head()` stehen; die Aufrufer übergeben dann keinen eingefrorenen Commit mehr, alle sieben Rekonstruktionen laufen still gegen den lebenden `HEAD`, und die Evidenzprüfung wird nach dem nächsten Commit entweder rot oder — falls zugleich die Manifestwerte nachgezogen werden — wieder zur Buchführung ohne Bindung

PRE_MORTEM: In drei Monaten ist die wahrscheinlichste Fehlerursache, dass die Kopplung zwischen Testsuite und Probe-Skript bricht. `_validate_writer_form_evidence()` lädt `scripts/native_contract_probe.py` per `importlib` und ruft `_codex_canary_bundle` beziehungsweise `_claude_canary_bundle` mit `base_commit=` auf. Diese Funktionen sind private Hilfen eines Werkzeugskripts, tragen aber seit Slice 06 die gesamte Evidenzlast von sieben Writerformnachweisen. Wer sie umbenennt, ihre Signatur ändert oder den `base_commit`-Parameter entfernt, bricht entweder die Evidenzprüfung sichtbar — das wäre der gute Fall — oder lässt sie über einen belassenen `_head()`-Fallback still auf den lebenden `HEAD` zurückfallen. Dann wäre `C-30` in genau der Prüfung wieder da, die es beweisen sollte, und der einzige verbleibende Schutz wäre, dass die Suite nach dem nächsten Commit rot wird — was unter Termindruck erfahrungsgemäß mit einem Nachziehen der Manifestwerte statt mit einer Ursachenanalyse beantwortet wird.

SLICE_APPROVAL: 06 | YES

STATUS: DONE
