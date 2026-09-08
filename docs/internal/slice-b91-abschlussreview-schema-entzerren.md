# Slice B91 – Abschlussreview-Schema entzerren

**Exakter Änderungspfad**

- `src/native_review_contract.py`
- `tests/test_native_review_contract.py`
- `tests/test_native_contract_differential.py`
- `tests/fixtures/native-provider-projection-baseline-v1.json`
- `docs/internal/slice-b91-abschlussreview-schema-entzerren.md`

**Akzeptanzkriterien**

- Das Writer-Schema zählt keine möglichen Aufteilungen von Statusänderungen und Reklassifizierungen mehr auf.
- Plan- und Final-Freigaben dürfen schema-seitig weniger Dispositionen als offene eigene Findings enthalten; die nachgelagerte fachliche Vertragsprüfung weist eine unvollständige Freigabe weiterhin ab.
- Die fachliche Diagnose nennt alle in der Freigabe fehlenden Findingkennungen.
- Listenobergrenzen, gebundene Findingkennungen, Eigentümerschaft sowie die Ablehnung unbekannter oder nicht offener Findingkennungen bleiben erhalten.
- Slice-Reviews dürfen wie nach B90 unberührte offene Findings auslassen.
- Recordschema, Recordtypen, Reducer-Version und `artifact_replay.py` bleiben unverändert.

## Bewusste Aktualisierung der Projektionsbaseline

Die Baselinefälle `claude/plan` und `claude/final` enthalten nun jeweils ein offenes Finding. Damit messen ihre Hashes die in diesem Slice geänderten Writerpfade, statt nur den trivialen Fall ohne offene Findings abzubilden.

- `claude/final`: `9ddf9bc9f833c74cfe1f32b997ccd6475c12fdceaf127b3e4cfb800b2ed2231a` → `160420f85834f9150ffb86d1b463ae38cc8a728abb08052ddbc965d75d9c0f2c`. Grund: Der Final-Writer kodiert die vollständige Disposition nicht mehr über `status_changes.minItems`; die Baseline bindet diesen Pfad jetzt mit einem offenen Finding.
- `claude/plan`: `4ddd1ba953d9018eb89e6d5597b691c88dc2169656f6e7ead76bf193021e412d` → `98889eaf289b9bb83ad5de466aafeebbee9f96c5070d0e8fe0e0118d74347858`. Grund: Die mit der Findingzahl wachsende `anyOf`-Aufzählung der Status-/Reklassifizierungsaufteilungen entfällt; die Baseline bindet diesen Pfad jetzt mit einem offenen Finding.

Die Hashes für `claude/initial_slice`, `claude/convergence` und alle Codex-Writer bleiben unverändert, weil B91 deren Writervertrag nicht ändert.

## Invarianznotiz

Der gemischte 30/3-Fall ist für eine freigegebene Nicht-Slice-Antwort in der Planform fachlich zulässig. Im branchweiten Finalreview bleiben die strengeren Regeln unverändert: Eine Freigabe darf kein eigenes Finding offen lassen; eine Reklassifizierung ohne Schließung kann daher nur eine ablehnende Eskalation sein. B91 verschiebt ausschließlich die Vollständigkeitsprüfung aus dem Transportschema in die fachliche Prüfung und lockert diese Finalinvariante nicht.
