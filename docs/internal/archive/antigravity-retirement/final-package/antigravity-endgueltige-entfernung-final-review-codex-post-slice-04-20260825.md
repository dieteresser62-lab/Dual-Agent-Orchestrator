# Codex-Abschlussreview nach Slice 04: endgültige Entfernung von Antigravity

Datum: 2026-08-25

Reviewbasis: `b3d49ff5ff2d` (`docs: archive native contract closure`)

Commitstand: `48d6aac0eacd` (`feat(orchestrator): complete antigravity retirement`)
Uncommittierter Korrekturstand: Slice 04 gemäß
`docs/internal/antigravity-endgueltige-entfernung-slice-04-review.md`

## Urteil

Der nach Slice 04 vorliegende Paketstand ist noch nicht abschlussfähig.

Die beiden Blocker aus dem ersten Codex-Abschlussreview sind inhaltlich
behoben: Die drei bekannten Finding-ID-Träger des Artifact-v2-Vertrags sind
auf `C-*` begrenzt, die aktiven A-Namensraumzweige in Prompt- und
Carry-forward-Logik wurden entfernt, die Guardregressionen wurden ergänzt,
und Claude hat `C-08` sowie Slice 04 ausdrücklich geschlossen beziehungsweise
freigegeben.

Eine unabhängige Grenzprobe widerlegt jedoch die in Slice 04 behauptete
Mengengleichheit von gebündeltem JSON-Schema und Python-Modell. Das ist eine
ausführbare Vertragsabweichung und damit im Abschlussreview ein Blocker.
Codex erteilt keine eigene Freigabe.

## Geprüfte Evidenz

- Vollständiger Paketstand seit `b3d49ff` einschließlich Arbeitsplan, drei
  Implementierungsslices, Slice 04, Runtime, Schemas, Replay, Dokumentation
  und Tests.
- Claude-Slice-04-Review mit `SLICE_APPROVAL: 04 | YES`, formaler Schließung
  von `C-08` und Disposition beider früherer Codex-Blocker.
- Dokumentierter Abschlussstand: fokussierte Slice-04-Matrix mit `42 passed`,
  vollständige Repositorymatrix mit `1168 passed in 71.33s`.
- Aktuelles `git diff --check`: Exitcode `0`; nur bekannte
  Zeilenendewarnungen.
- Statische Retirementinventur außerhalb von Tests, Entwicklungsberichten
  und Marktvergleich: kein aktiver Antigravity-Provider-, Step- oder
  Freigabepfad festgestellt.
- Drei kleine providerfreie Gegenproben gegen die tatsächlichen
  `finding_transition`-, `review`- und `correction_work_unit`-Dokumentformen.
  Die vollständige Testsuite und externe Provideraufrufe wurden im Review
  nicht wiederholt.

## Geschlossene frühere Blocker

### CODEX-BLOCKER-01 – `structured-v2` akzeptiert `A-*`-Finding-IDs: inhaltlich geschlossen

`src/artifact_models.py` bindet die drei bekannten Finding-ID-Träger an
`_require_finding_id()` beziehungsweise `_require_unique_finding_ids()`.
`schemas/orchestrator-artifact-v2.schema.json` verwendet für dieselben Träger
`finding_id`, `finding_ids` und `non_empty_finding_ids`. Die vorhandenen
Regressionen weisen `A-01` an Modell-, Schema- und Deserialisierungsgrenze ab
und erhalten gültige `C-*`-Roundtrips.

Die nachfolgend beschriebene LF-Abweichung ändert nichts an der erfolgreichen
Entfernung des `A-*`-Namensraums, verhindert aber die weitergehende Zusage
eines identischen kanonischen Finding-ID-Vertrags.

### CODEX-BLOCKER-02 – aktive A-Namensraumzweige: geschlossen

`src/prompts.py` und `src/orchestrator.py::_carry_forward_findings()` erzeugen
nur noch `C-*`-IDs. Die neuen strukturellen Guardkontrollen erfassen die
beiden zuvor übersehenen Rückfallformen. Die produktive Topologie bleibt auf
Codex und Claude begrenzt; Antigravity ist nur noch als historischer oder
externer Vergleichsbegriff vorhanden.

### Claude-Finding `C-08`: geschlossen

Claude hat beide Teilforderungen im Slice-04-Bericht ausdrücklich disponiert:
Die nativen Transportidentitäten sind v2-gebunden und die alten
Transportidentitäten Bestandteil der Retirementkontrolle.

## CODEX-BLOCKER-03 – Artifact-v2-Schema und Python-Modell akzeptieren unterschiedliche Finding-IDs

Der neue Schema-Typ verwendet in
`schemas/orchestrator-artifact-v2.schema.json:48` das Muster
`^C-(0[1-9]|[1-9][0-9]*)$`. Der lokale Offlinevalidator wertet `pattern` in
`src/schema_validation.py:146` mit `re.search()` aus. In dieser Semantik kann
`$` unmittelbar vor einem abschließenden Zeilenumbruch treffen. Das
Python-Modell verwendet dagegen in `src/artifact_models.py:998`
`_FINDING_ID_RE.fullmatch()` und verlangt damit das tatsächliche Stringende.

Eine providerfreie Gegenprobe ersetzte in je einem ansonsten gültigen
Artifact-v2-Dokument `C-01` durch den JSON-String `"C-01\n"`. Das Ergebnis war
für alle drei Finding-ID-Träger identisch:

| Recordform | `validate_artifact_document()` | `ArtifactRecord.from_dict()` |
|---|---|---|
| `finding_transition.finding_id` | akzeptiert | abgewiesen |
| `review.finding_ids[]` | akzeptiert | abgewiesen |
| `correction_work_unit.finding_ids[]` | akzeptiert | abgewiesen |

Die Record-Deserialisierung bleibt aktuell fail-closed, weil sie nach der
Schemavalidierung zusätzlich das Domänenmodell konstruiert. Dennoch ist
`validate_artifact_document()` ein öffentlicher Validierungseinstieg und die
Slice-04-Zusage lautet ausdrücklich, dass JSON-Schema und Python-Modell
denselben kanonischen Vertrag verwenden. Der Claude-Bericht bezeichnet beide
Mengen sogar als „identisch und lückenlos“, nennt die Abweichung anschließend
aber selbst nur im Pre-Mortem. Im branchweiten Abschlussreview darf ein heute
reproduzierbarer Vertragsdefekt nicht als Zukunftsrisiko stehen bleiben.

### Abnahmekriterium

1. Der Artifact-v2-Schematyp für Finding-IDs weist abschließende LF-/CR- und
   andere nicht zum kanonischen `C-*`-Format gehörende Zeichen bereits in
   `validate_artifact_document()` fail-closed ab.
2. Python-Modell und gebündeltes Schema akzeptieren für Finding-IDs dieselbe
   Wertemenge; die Lösung darf die allgemeine JSON-Schema-`pattern`-Semantik
   nicht unbemerkt für andere Felder verändern.
3. Providerfreie Regressionen injizieren mindestens `"C-01\n"` in alle drei
   serialisierten Recordformen und verlangen Ablehnung sowohl durch
   `validate_artifact_document()` als auch `ArtifactRecord.from_dict()`.
4. Gültige Grenzfälle wie `C-1`, `C-09`, `C-10` und `C-100` roundtrippen
   weiterhin; `A-*`, leere, whitespacebehaftete und nichtkanonische IDs
   bleiben abgewiesen.
5. Die fokussierte Artifact-/Schema-/Migration-Matrix und anschließend die
   vollständige Repositorymatrix sind grün; `git diff --check` bleibt sauber.

## Reviewevidenz und Pre-Mortem

Geprüfte Dimensionen: vollständige Planerfüllung, Codex-Claude-Topologie,
alleinige Claude-Freigabeautorität, `structured-v2`-Bindung, Agentprofile,
automatische lokale Commitgrenze, Provider- und Stepentfernung,
Findingnamespace, Schema-/Modellgrenze, Replay-/Deserialisierungsverhalten,
Retirementguard, Dokumentationskonsistenz und vorhandene Testattestierungen.

Größtes Restrisiko nach Schließung des Blockers bleibt die Erweiterbarkeit des
Artifact-v2-Schemas: Eine neue Recordform kann einen Findingbezug versehentlich
an den allgemeinen `identifier`-Typ statt an `finding_id` binden. Der heutige
Guard inventarisiert nicht semantisch alle Schemafelder.

Pre-Mortem: In drei Monaten verwendet ein neuer Audit- oder Korrekturpfad
`validate_artifact_document()` als hinreichende Autorität und persistiert
einen schemaakzeptierten, aber vom Python-Modell nicht kanonisch
interpretierbaren Findingbezug. Beim späteren Resume scheitert die
Rehydrierung derselben Recordkette, obwohl sie beim Schreiben als
schema-valide galt. Ohne eine explizite Gleichheitsregression für die drei
Finding-ID-Träger bleibt dieser Fehler unter einer vollständig grünen Matrix
unsichtbar.

FINAL_REPORT_READY: NO
STATUS: DONE
