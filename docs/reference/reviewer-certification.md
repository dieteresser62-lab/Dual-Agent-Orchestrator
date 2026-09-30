# Reviewer-Zertifizierung für weitere Kandidaten

Diese Anleitung beschreibt die Zertifizierung eines neuen Reviewer-Anbieters am **fiktiven** Beispiel Kimi gegen eine Referenz und anschließend den vorhandenen Codex-Kandidaten gegen Claude. Es gibt noch keinen Kimi-Adapter, Capability-Eintrag oder Kimi-Nachweis in diesem Repository. Eine Zertifizierung betrifft die beiden Slots `reviewer` und `final_reviewer` getrennt. Der Kandidat bleibt bis zum vollständigen Nachweis `candidate`; die Auswahl als `experimental` ist eine ausdrückliche Operatorentscheidung. Die Herstellertrennung zwischen Implementer und beiden Reviewslots bleibt verpflichtend; die Standardbelegung bleibt Codex / Claude / Claude. Den Claude-Implementer beschreibt die [Implementer-Zertifizierung](implementer-certification.md).

## 1. Adapter und Capability eintragen

Implementiere den nativen Reviewer-Adapter in `src/`, registriere ihn in `create_reviewer_qualification_adapter` und in der produktiven Slot-Fabrik. Trage Writer-Profil, CLI-Identität, erforderliche Flags und Modellfamilie im [Capability-Register](../../schemas/native-provider-schema-capabilities-v2.json) ein. Binde beide Reviewer-Slots in der [Zertifizierungstabelle](../../schemas/role-provider-certifications-v1.json) zunächst als `candidate`. Qualifikation und Canaries verwenden die produktive Adapterklasse und denselben Rollenvertrag; nur die noch zu beweisende Zertifizierungssperre wird beim direkten Messaufruf umgangen. Der Adapter muss den nativen JSON-Umschlag, Writer-Schema, Anfragenummer, Domainvertrag, Fehlerklassen, Prozessende und effektive Rechte selbst prüfen.

Die Versionspolitik lautet **alle neuen Versionen**: Eine korrekt formatierte CLI-Version ab der gemessenen Mindestversion ist grundsätzlich zulässig, auch eine neue Hauptversion. Jeder echte Aufruf prüft dennoch Binäridentität, erforderliche Flags, Transport, Isolation und Rechte. Für eine einzelne Kampagne darf der Operator eine private, eingefrorene Binärkopie verwenden, damit die Messserie nicht durch eine automatische Aktualisierung wechselt. Diese Kopie ist keine Produktionsvoraussetzung.

## 2. Abschottung und Phase 0

Lege das Kimi-Abschottungsdesign samt benanntem Profil in [profiles.py](../../scripts/qualification/profiles.py) ab. Beschreibe isoliertes HOME und Laufverzeichnis, erlaubte Lesewurzel, Deny-Regeln, Schreibschutz, Symlink-Auflösung, Netz- und Befehlswerkzeuge, Konfigurations- und MCP-Vererbung sowie den Postcheck. Die Referenzprofile für AGY und Claude zeigen zwei unterschiedliche Mechanismen: AGY nutzt `request-review`, eine explizite Deny-Liste und einen isolierten Agenten; Claude nutzt unter anderem `--restricted`. Ein neues Profil muss durch einen echten Adapter-Postcheck gedeckt sein.

Der gemeinsame [P1–P6-Katalog](../../scripts/qualification/phase0_catalog.py) prüft positive Lesekontrolle, Projektanweisungen, Schreibversuche, Befehl/Netz, Delegation, Lesen außerhalb des Snapshots und durch Symlinks sowie weiche Ablehnung. Erzeuge die Matrix und zunächst nur Fakes:

```bash
python3 scripts/qualification/make_matrix.py --output /tmp/kimi-phase0-matrix.json
python3 scripts/qualification/run_probe.py P1 kimi --profile-file /tmp/kimi-phase0.json --output /tmp/kimi-p1 --fake-root /tmp/fakes
python3 scripts/qualification/make_format_fixtures.py --output /tmp/kimi-format-fixtures
python3 scripts/qualification/format_cases.py build --output /tmp/kimi-format-cases --capability kimi
```

Ein Live-Aufruf von `run_probe.py` oder `run_format.py` braucht `--live` und ein live-fähiges Profil. Jeder Versuch erhält ein neues Verzeichnis. Prüfe die sechs P-Fälle und F1–F6, positive Kontrollen, Rohumschläge, effektive Einstellungen, abgelehnte Aktionen und unveränderte Dateibäume. Ein unbekannter oder nicht auswertbarer Fehler ist kein bestandener Schutztest. Fasse die Phase-0-Belege unter `docs/evidence/kimi/phase-0-v1.json` zusammen und binde ihren SHA-256-Digest in das Protokoll.

## 3. Paar und Messprotokoll festlegen

Für neue Kampagnen gilt `qualification-protocol-v6`. Das Protokoll nennt `candidate_provider`, `reference_provider`, `provider_profiles`, `provider_runtime` und `evidence_directory` ausdrücklich, zum Beispiel:

```json
{
  "schema_version": "qualification-protocol-v6",
  "candidate_provider": "kimi",
  "reference_provider": "claude",
  "provider_profiles": {"kimi": "kimi", "claude": "claude"},
  "provider_runtime": {
    "kimi": {"model": "<gemessener Modellslug>", "effort": "high", "isolation": true},
    "claude": {"model": "opus", "effort": "high", "isolation": false}
  },
  "evidence_directory": "docs/evidence/kimi"
}
```

Das ist der Paarteil, kein vollständiges Protokoll. Ergänze die unveränderten Zählungen und Qualitätsregeln des [v5-Protokolls](../evidence/antigravity/qualification-protocol-v5.json), neue Phase-0- und Rubrik-Digests sowie eine vorab genehmigte und digestgebundene Qualitätsgrundlage. Entferne dabei alte anbieterspezifische Freitextaussagen. `python3 scripts/probe_reviewer.py plan <protokoll.json>` prüft das vollständige Protokoll und zeigt 12 Transportaufrufe, zwei Größenfälle, einen Print-Timeout, sechs Qualitätsfälle pro Anbieter und zwei Canaries. Das unveränderte v5-Protokoll bindet als historische Voreinstellung AGY/Claude; v1–v4 bleiben überholt und werden abgelehnt.

Die Requests für F5/F6 und Q4/Q6 müssen das aktuelle produktive Finalreview-Kapazitätskriterium enthalten. Q5/Q6 brauchen sichtbare, bestandene Tests für ihre saubere Semantik und vorab festgelegte Regeln für unbegründete Befunde. Der Größenfall hält seine eigene Vollparsing-Anfrage. Nach einer Regeländerung ist eine neue Protokollversion mit vollständiger neuer Qualifikation nötig.

## 4. Kampagne ausführen und bewerten

Nutze je Serie ein neues `series_id` und ein unverändertes Live-Profil mit absolutem Binärpfad, gebundenem Commit, Modell und Effort. `campaign.py` zeigt ohne `--live` die geplanten Fälle; mit `--live` bereitet es für jeden Aufruf eine frische Quelle vor und führt die Fälle nacheinander aus:

```bash
python3 scripts/qualification/campaign.py transport kimi kimi-transport-s1 --protocol /tmp/kimi-protocol-v6.json --profile /tmp/kimi.toml --evidence-dir /tmp/kimi-evidence --source-dir /tmp/kimi-sources --live
python3 scripts/qualification/campaign.py print_timeout kimi kimi-timeout-s1 --protocol /tmp/kimi-protocol-v6.json --profile /tmp/kimi-timeout.toml --evidence-dir /tmp/kimi-evidence --source-dir /tmp/kimi-sources --live
python3 scripts/qualification/campaign.py large_output kimi kimi-large-s1 --protocol /tmp/kimi-protocol-v6.json --profile /tmp/kimi.toml --evidence-dir /tmp/kimi-evidence --source-dir /tmp/kimi-sources --live
python3 scripts/qualification/campaign.py quality kimi kimi-quality-s1 --protocol /tmp/kimi-protocol-v6.json --profile /tmp/kimi.toml --evidence-dir /tmp/kimi-evidence --source-dir /tmp/kimi-sources --live
python3 scripts/qualification/campaign.py quality claude claude-quality-s1 --protocol /tmp/kimi-protocol-v6.json --profile /tmp/claude.toml --evidence-dir /tmp/kimi-evidence --source-dir /tmp/kimi-sources --live
```

Die Produktivwiederholung folgt ausschließlich `production_retryable`: höchstens zwei transiente Netzwerk-/Timeout-Wiederholungen und zwei Vertragskorrekturen je Fall. Ein nicht wiederholbarer technischer Fehler stoppt den Block. Ein neuer vollständiger Serienstart verlangt `--restart-diagnosis` und `--restart-change` am ersten Aufruf sowie einen tatsächlich geänderten Commit, Profil-, Writer- oder Binärdigest. Negative, abgebrochene und durch einen neuen Korpus überholte Serien bleiben in den Nachweisen. Einzelretakes und Filtern sind unzulässig. Der Print-Timeout besteht nur als nachweislich sichere technische Ablehnung ohne akzeptiertes Resultat.

Der Prüfbefehl liest auch gzip-komprimierte Umschläge:

```bash
python3 scripts/probe_reviewer.py evaluate-qualification /tmp/kimi-evidence/qualification-series-v1.json /tmp/kimi-evidence/qualification-envelopes-v1.json --protocol /tmp/kimi-protocol-v6.json --quality-results /tmp/kimi-evidence/quality-results-v1.json --operator-decisions /tmp/kimi-evidence/operator-decisions-v1.json
```

## 5. Blind bewerten und entscheiden

Exportiere die zwölf Q1–Q6-Antworten mit separatem, privatem Zuordnungsverzeichnis. Das Werkzeug meldet Selbstidentifikations- und Anbieterworttreffer; prüfe jeden Treffer vor der Bewertung. Die Pakete enthalten neutrale IDs, Antwort und Ground Truth, aber keine Anbieterzuordnung:

```bash
python3 scripts/qualification/blind.py prepare --protocol /tmp/kimi-protocol-v6.json --series /tmp/kimi-evidence/qualification-series-v1.json --envelopes /tmp/kimi-evidence/qualification-envelopes-v1.json --output-dir /tmp/kimi-rating
python3 scripts/qualification/blind.py steering-prompt --packet /tmp/kimi-rating/packets/steering-packet.json --output /tmp/kimi-rating/steering.json
python3 scripts/qualification/blind.py codex-command --binary /absolute/path/to/codex --prompt /tmp/kimi-rating/packets/codex-prompt.txt --output /tmp/kimi-rating/codex.json --events /tmp/kimi-rating/codex-events.jsonl --stderr /tmp/kimi-rating/codex-stderr.log --private /tmp/kimi-rating/private
```

Der ausgegebene Codex-Befehl nutzt `read-only` und sperrt die private Zuordnung während des Laufs; `run-codex` startet ihn nur mit `--live`. Der Steuermann erhält den versionierten [Bewerterauftrag](../../scripts/qualification/steering-rater-prompt-v1.md) und nur sein Paket. Beide liefern `quality-rating-v1`. `combine-quality-ratings` stellt für regelrelevante Uneinigkeiten konkrete Ja/Nein-Fragen an den Operator; erst nach den Antworten werden Urteile eingefroren und Zuordnungen aufgedeckt. Die Qualitätsregel verlangt beide kritischen, mindestens drei von vier Defekten, höchstens einen unbegründeten Befund in Q5/Q6 und keinen erfundenen kritischen Befund. Bei schwächerer Referenz entscheidet der Operator nach Entblindung ausdrücklich über `--reference-special-decision approve_experimental|deny_experimental`. Eine Größen-Ausnahme braucht eine eigene dokumentierte Entscheidung zum konkreten fehlgeschlagenen Fall.

## 6. Canaries, Promotion und Schnelltest

Nach positivem Endurteil übernimm die vollständigen freigegebenen Kampagnennachweise aus `/tmp/kimi-evidence` in das vom Protokoll gebundene `docs/evidence/kimi`; negative und überholte Serien bleiben dabei erhalten. Starte für den Kandidaten zwei **direkte** Canaries mit frisch gebundenem Profil und getrennten Ausgabeordnern. Der Finalreview-Canary liest den zuvor validierten Q3-Referenzreview aus diesem gebundenen Evidenzverzeichnis, ohne einen neuen Referenzaufruf:

```bash
python3 scripts/probe_reviewer.py canary-call reviewer --protocol /tmp/kimi-protocol-v6.json --profile /tmp/kimi-canary.toml --output-dir /tmp/kimi-canary-reviewer --live
python3 scripts/probe_reviewer.py canary-call final_reviewer --protocol /tmp/kimi-protocol-v6.json --profile /tmp/kimi-canary.toml --output-dir /tmp/kimi-canary-final --live
python3 scripts/probe_reviewer.py quicktest kimi --protocol /tmp/kimi-protocol-v6.json --profile /tmp/kimi-canary.toml --live
```

Prüfe und bereinige die lokalen Rohberichte vor der Aufnahme in versionierte Evidenz. Binde beide Canary-Digests an die jeweiligen Zertifizierungszeilen und setze beide Slots erst nach bestandenen Nachweisen und dokumentierter Operatorentscheidung auf `experimental`. Der Schnelltest nutzt F2 mit höchstens 240 Sekunden und ist Diagnose, kein neues Gate. Die produktive Auswahl erfolgt anschließend ausdrücklich im Lauf-TOML; es gibt keinen automatischen Anbieterwechsel.

## Lehren und Ressourcen vom 29.09.2026

Die erste Phase-0-F6-Anfrage fehlte ein produktives Finalreview-Kriterium; Anfragetreue ist Teil des Messaufbaus. Transiente und Vertragswiederholungen müssen die produktive Rückmeldung und dieselben Grenzen verwenden. Die erste Qualitätsgrundlage enthielt saubere Fälle ohne sichtbaren Beweistest; die Korrektur fügte solche Tests hinzu und ließ die alten negativen Ergebnisse auswertbar. Fehlgeschlagene und überholte Serien dürfen nicht verschwinden.

Die [AGY-Messung](antigravity-reviewer.md) meldete 2.115.470 CLI-Token für 34 Phase-0-Aufrufe. Das ist weder ein Rechnungsbetrag noch ein Kaufpreis. Formatproben benötigten ungefähr 97–146 Sekunden und 63.306–89.796 gemeldete Token je Aufruf. Abonnementkontingent, gekaufte Credits, tatsächlicher Preis und das bediente Modell bleiben ohne gesonderte Betreiberbelege unbekannt. Erfasse Quoten-Screenshots und Zeitpunkte als `--quota-observation`, rechne Nutzung und Kosten getrennt und plane Reserven für komplette Serien-Neustarts ein. Eine eingefrorene Binärkopie dient nur der Konsistenz einer Kampagne.

## Codex-Prüfer: Berechtigungsprofil und Pflicht-Härtung

Der [NativeCodexReviewAdapter](../../src/codex_review_adapter.py) verwendet das Profil `codex-reviewer` für beide Reviewslots. Die [Zertifizierungstabelle](../../schemas/role-provider-certifications-v1.json) führt beide derzeit als `candidate`; der reguläre Start bleibt gesperrt. Nach gemeinsamer Abnahme mit dem Claude-Implementer ist Claude / Codex / Codex ausdrücklich per TOML wählbar. Ein bestandener Offline-Quicktest oder vorhandener Writer allein ist keine Zulassung.

Die Dateigrenze ist ein eigenes Berechtigungsprofil **ohne `--sandbox`**. Der Offline-Befund zeigte: `--sandbox read-only` allein kann fremde Home-Dateien lesen; zusammen mit dem eigenen Profil hebt es dessen Leseschutz auf. Die Transportnormalisierung weist deshalb jede solche Kombination ab. Der produktive Profilteil lautet schematisch:

```text
-C <container>
-c 'permissions.dao-reviewer={filesystem={":minimal"="read","<Codex-Paketwurzel>"="read",":workspace_roots"={"."="read"}}}'
-c 'default_permissions="dao-reviewer"'
```

Der Container enthält `repo` und die über Manifest gebundenen Anfragen/Evidenz unter `input`; der Adapter versiegelt den vollständigen Baum und prüft ihn vor und nach dem Providerprozess. Die aufgelöste npm-Paketwurzel `node_modules/@openai/codex` muss lesbar sein, weil die CLI sich in der Sandbox erneut ausführt. `codex_package_root` und `validate_codex_review_package_root` verlangen eine eindeutige ausführbare native Datei innerhalb dieser Wurzel, auch beim aktuellen Plattformpaket unter `node_modules/@openai/codex-*/vendor`. Eine pauschale HOME-Lesefreigabe ist kein Ersatz.

Pflicht sind `--ignore-user-config`, `--ignore-rules`, `project_doc_max_bytes=0`, `shell_environment_policy.inherit="core"` und `web_search="disabled"`. `--disable` sperrt `apps`, `plugins`, `multi_agent`, `goals`, `browser_use`, `computer_use`, `image_generation`, `hooks`, `skill_search`, `tool_suggest` und `remote_plugin`. Dadurch erweitern Konnektoren, Root-Anweisungen und Elternvariablen den Reviewauftrag nicht. Die Review-Policy wird ausdrücklich mit dem Startauftrag über stdin transportiert. Das Schlussresultat kommt aus `--output-last-message`, ausschließlich als `result`-Objekt; Events liefern Nutzung und Live-Sicht. Writer-, Request- und Domänenprüfung bleiben verpflichtend.

Der Offline-Durchstich vom 30.09.2026 mit **Codex 0.159.2** und `gpt-6.1-sol` bestätigte die Dateisystemgrenze. Als Neustart-Helfer sind nur Namen unter `~/.codex/tmp/arg0/codex-arg0*` sichtbar; `auth.json`, `.bashrc`, Repositories und andere Credential-Verzeichnisse bleiben verborgen. `/tmp` ist privat: dortiges Schreiben erreicht den Host nicht.

Im Modus `code_mode_only` stehen Werkzeuge als Namensräume in `input[*].additional_tools`, statt im `tools`-Feld. Modellmetadaten können dabei Subagenten einschalten; `--disable multi_agent` allein verhindert das nicht. Der Adapter erzeugt deshalb aus dem beim Laufstart ermittelten Katalog einen privaten Katalog je Aufruf und übergibt ihn mit `-c model_catalog_json=…`. Für alle Modelle entfernt er `multi_agent_version`, `multi_agent_reasoning_effort` sowie die Opt-ins `experimental_supported_tools`, `supports_search_tool` und `web_search_tool_type`. Katalog und Modell müssen vorhanden sein. Die kanonischen gehärteten Bytes sind über den Digest der laufgebundenen Isolationsoptionen an Start und Resume gebunden; Drift stoppt vor dem nächsten Aufruf.

Der Offline-Quicktest nach CLI-Updates läuft mit dem echten Profilmodell und verlangt genau `functions: exec, wait, request_user_input, request_user_input_async` sowie `clock: sleep`. `collaboration`, `spawn_agent`, Websuche und weitere Namensräume führen zum Fehlschlag. Ein zufälliger Fehler wie „no rollout found“ ist kein Nachweis einer Werkzeugsperre. Direkte alte Werkzeugaufrufe beweisen die Sperre des Code-Modus nicht; dessen unabhängiger Beleg ist die tatsächlich angebotene Namensraumfläche. Ein nicht belegter `exec`-Injektionsweg wird nicht verwendet. Fehlt die Anfrage im nativen Phase-0-Ereignisstrom, bleibt dessen Werkzeugflächenmessung `skipped` und verweist auf den Offline-Quicktest. Die frühere native Werkzeugliste bestand aus `exec_command`, `write_stdin`, `request_user_input` und `view_image`.

## Codex-Prüfer: Strict-Writer und Größenbegrenzung

Die [Writer-Projektion](../../src/native_provider_schema.py) senkt den Rollenvertrag über `lower_reviewer_writer_for_profile` auf den Strict-Modus des Profils. Objekt-Overlays in `anyOf` werden in vollständige geschlossene Objektvarianten ausmultipliziert; jede Einschränkung muss nachweislich eine Teilmenge des Elternschemas sein. Unbekannte oder erweiternde Overlays scheitern. Alle Objekte haben `additionalProperties: false` und vollständige Pflichtfelder; die Wurzel bleibt ein Objekt ohne Root-`anyOf`. Jeder Schemaknoten deklariert `type`, `$ref` oder `anyOf`; `const`-/`enum`-Knoten erhalten einen aus den Literalwerten abgeleiteten Typ. Die lokale Domänenvalidierung bleibt unverändert und bindet das Ergebnis zusätzlich an die Anfrage.

Die implementierten OpenAI-Grenzen werden vor dem Aufruf geprüft: höchstens **5.000 Objekteigenschaften**, **10 Objektebenen**, **120.000 Zeichen** für die gezählten Namen/Literale und **1.000 Enum-Werte** insgesamt. Ein String-Enum mit mehr als **250** Werten darf zusammen höchstens **15.000 Zeichen** haben. Referenzen werden für die Tiefenmessung aufgelöst; unbekannte oder rekursive Definitionen scheitern. Transport-Finalreviews und die gesonderten 128-/512-Befunde-Größenfälle bleiben separat nachzuweisen. Die Smoke-Befunde zu offenen Overlay-Zweigen und fehlenden Literaltypen erklären, weshalb bloße Schema-Erzeugbarkeit kein Live-Nachweis ist.

## Codex-Prüfer: Phase 0 P1–P6 und Quicktest

Das Adapterprofil `codex-reviewer` in [profiles.py](../../scripts/qualification/profiles.py) verwendet produktiv erzeugte Befehle. Der [P-Katalog](../../scripts/qualification/phase0_catalog.py) umfasst:

| Fall | Nachweis |
|---|---|
| P1 | Positive Evidenzlesekontrolle, Werkzeugfläche und ignorierte Projektanweisungen. |
| P2 | Dateiänderung, Umbenennen, Löschen, chmod und Symlink-Schreiben bleiben unwirksam. |
| P3 | Befehle erweitern keine Schreib-/Lesewurzeln; Netz, WSL-Interop und Prozessversuche prüfen. |
| P4 | MCP, Browser, Websuche, Delegation und Hintergrundwerkzeuge bleiben gesperrt. |
| P5 | Fremddateien, Symlinks, Traversal und Umgebungs-Köder bleiben verborgen. |
| P6 | Weiche Ablehnung einer fremden Geschwisterdatei bei erfolgreicher Positivkontrolle. |

Die Shell ist hier verfügbar, arbeitet aber in der Dateigrenze und ohne Netz. Schreiben in das private `/tmp` ist kein Host-Schreibzugriff. Jeden P-Fall mit neuem Ausgabeordner live ausführen, Rohbeobachtungen und unveränderte Hostbäume prüfen und die echte Phase-0-Evidenz vor der Qualifikation digestgebunden übernehmen:

```bash
python3 scripts/qualification/run_probe.py P1 codex-reviewer --profile-file /tmp/codex-phase0.json --output /tmp/codex-p1 --live
python3 scripts/probe_reviewer.py boundary-check --pair all --out /tmp/boundary-cli-update
```

`boundary-check` ist nach CLI-Updates der konto- und kontingentfreie Schutz-Quicktest mit lokalen Fake-Servern; er ersetzt P1–P6 mit dem echten Modell nicht. Alternativ heißt derselbe Einstieg `python3 scripts/qualification/offline_boundary.py --pair all`. Als gesonderten Schritt führt der Operator den Live-F2-Schnelltest aus (höchstens 240 Sekunden); `quicktest` startet die Offline-Grenzprüfung nicht automatisch:

```bash
python3 scripts/probe_reviewer.py quicktest --candidate codex --protocol docs/evidence/codex/qualification-protocol-v6.json --profile /tmp/codex-canary.toml --live
```

## Codex-Prüfer: Protokoll v6 und neutrale Blindbewerter

Das [Codex-Protokoll v6](../evidence/codex/qualification-protocol-v6.json) bindet Kandidat `codex`, Referenz `claude`, das Profil `codex-reviewer`, Modell `gpt-6.1-sol`, Korpusrevision 2 und die unabhängigen Blindbewerter **AGY und Steuermann** (`raters: ["agy", "steering"]`, ebenso in `quality.raters`). Kandidat und Referenz dürfen keine eigenen Antworten bewerten. V5 bleibt historische AGY/Claude-Evidenz und liefert keinen Codex-Nachweis. `provider_runtime.codex.isolation: false` betrifft nur AGY-spezifische HOME-Voraussetzungen; der Codex-Adapter erzwingt seine eigene Grenze stets.

Die Zählungen und Qualitätsregeln bleiben: 12 Formatantworten (F1–F6 zweimal), zwei Größenfälle (128/512), ein sicher abgelehnter Timeoutfall, sechs Qualitätsfälle je Anbieter und zwei Slot-Canaries. Beide kritischen und mindestens drei von vier Defekten müssen erkannt werden, höchstens ein unbegründeter Befund über Q5/Q6 und kein erfundener kritischer Befund. F5/F6 und Q4/Q6 tragen das produktive Kapazitätskriterium; die Größenfälle behalten die Vollparsing-Anfrage. Der historische Serienschlüssel `print_timeout` prüft bei Codex einen positiven Runtime-Timeout unter 600 Sekunden, kein AGY-Flag; Erfolg oder ein gültiges Stopresultat besteht diesen Fall nicht.

Nach echten P1–P6-Läufen muss deren bereinigte Evidenz samt Digest im Protokoll gebunden werden; die mitgelieferte Vorbereitung ist keine abgeschlossene Kampagne. Danach gelten die Serienbefehle aus Abschnitt 4 mit `codex`, `claude` und dem v6-Protokoll. Exportiere erst die vollständigen Qualitätsserien in neutrale Pakete, prüfe Selbstidentifikations-/Anbieterworttreffer und halte die private Zuordnung von beiden Bewertern fern:

```bash
python3 scripts/qualification/blind.py prepare --protocol docs/evidence/codex/qualification-protocol-v6.json --series /tmp/codex-evidence/qualification-series-v1.json --envelopes /tmp/codex-evidence/qualification-envelopes-v1.json --output-dir /tmp/codex-rating
python3 scripts/qualification/blind.py steering-prompt --packet /tmp/codex-rating/packets/steering-packet.json --output /tmp/codex-rating/steering.json
python3 scripts/qualification/blind.py agy-command --binary /absolute/path/to/agy --prompt /tmp/codex-rating/packets/agy-prompt.txt --schema /tmp/codex-rating/packets/agy-rating-schema.json --output /tmp/codex-rating/agy.json --events /tmp/codex-rating/agy-events.jsonl --stderr /tmp/codex-rating/agy-stderr.log --private /tmp/codex-rating/private --home /tmp/agy-rater-home --run-root /var/tmp/agy-rater-run
```

`agy-command` druckt nur den geschützten Wrapper; `run-agy` mit denselben Optionen braucht zusätzlich `--live`. Der Wrapper verwendet die produktive AGY-Lesegrenze, isoliertes HOME und ein eigenes Laufverzeichnis unter `/var/tmp`; Eingaben, private Zuordnung und Ausgaben dürfen nicht überlappen. Beide Bewerter liefern `quality-rating-v1` mit ihrem gebundenen Bewerternamen. Erst vollständige Urteile und dokumentierte Operatorantworten auf regelrelevante Uneinigkeiten erlauben Entblindung. Eine schwächere Referenz braucht danach die ausdrückliche Sonderentscheidung; ein negativer Kandidat wird nicht freigegeben. Negative und unvollständige Serien bleiben sichtbar. Canaries und der echte Topologie-Lauf mit Claude-Implementer folgen vor jeder Promotion; keine Zertifizierungszeile wird durch diese Doku geändert.
