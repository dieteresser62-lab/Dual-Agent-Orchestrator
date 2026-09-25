# Slice 3 von 4 – Protokollierter post-merge-Nachlauf

<!-- audit:status:begin -->
Freigegeben in Runde 2 · 3 Befunde, 3 geschlossen · Validierung grün
<!-- audit:status:end -->

## Ziel

<!-- audit:goal:begin -->
Ein wirksamer `post-merge`-Hook läuft nach einem erfolgreichen lokalen Merge einmal und ein Fehler bleibt sichtbar, ohne den Merge rückgängig zu machen.
<!-- audit:goal:end -->

## Akzeptanzkriterien

<!-- audit:acceptance:begin -->
- Gegen SOURCE ermittelt der Orchestrator den wirksamen `post-merge`-Hook nach dem bestätigten Merge-Ergebnisrecord aus `core.hooksPath` oder dem Git-Hook-Pfad, prüft reguläre Datei, Ausführbarkeit und jedes Pfadsegment auf Symlinks und führt ihn mit Argument `0` höchstens einmal je Merge-Commit aus. Ein im versionierten Baum liegender, gegenüber der Merge-Basis durch den Zielbranch geänderter oder erst hinzugefügter Hook wird mit Pfad und Grund ausgelassen. Testrepositories belegen den geänderten versionierten Hook, einen unveränderten `.git/hooks/post-merge`-Hook, einen extern konfigurierten Pfad und einen Symlink-Pfad.
- Gegen SOURCE liegt die Reihenfolge fest: bestätigter Merge und Ergebnisrecord, Nachlauf-Intent, Hook oder begründetes Auslassen, terminaler Nachlauf-Resultatrecord, Laufabschluss, im Watch-Modus Erfolgsevidenz und Queue-Move nach `outbox/done/`. `merge_completed_branch = false` führt keinen Hook aus und meldet einen vorhandenen Hook als ausgelassen; ein durch `core.hooksPath` überschriebener vorhandener Standard-Hook wird ebenfalls gemeldet. Die Git-Option `core.hooksPath=/dev/null` innerhalb von Archiv-Commit und Merge sowie deren Vorabprüfung und Rollback-Logik bleiben bestehen; Tests belegen diese Fälle.
- Gegen SOURCE erfasst das Protokoll stdout und stderr getrennt und begrenzt samt Kürzungskennzeichen. Ein Exitcode ungleich null oder eine Überschreitung von 600 Sekunden erzeugt eine sichtbare Warnung mit Status, ohne den Merge zurückzunehmen oder den Auftrag zum Poison-Fall zu machen. Fokussierte Tests decken Erfolg, Fehler, Timeout, fehlende Ausführbarkeit, Auslassen und die Ausgabegrenze ab.
- Gegen SOURCE ist ein offener Nachlauf-Intent nach Prozessabbruch als ungewisser Ausgang erkennbar. Automatisches Resume führt den Hook nicht erneut aus; Watch pausiert ohne Retry-Zähler oder Poison-Verschiebung. Die CLI-Quittierung mit unverändertem Task, Merge-Commit und Begründung schreibt einen gebundenen `acknowledged_unknown`-Resultatrecord und erlaubt anschließendes Resume bis `done/`, ohne Hook-Wiederholung. Tests decken die Crash-Fenster vor Intent, vor/nach Hook-Start und Ergebnisrecord, Quittierung und doppeltes Resume ab. Alte Run-Profile und Record-Ketten bleiben lesbar und lösen keinen neuen Nachlauf aus.
<!-- audit:acceptance:end -->

## Umfang

<!-- audit:scope:begin -->
- `docs/internal/kettenende-nachbessern-implement-review-7cf1c8a7.md`
- `docs/internal/slice-kettenende-nachbessern-arbeitsplan-03-protokollierter-post-merge-nachlauf.md`
- `src/agent_runtime.py`
- `src/artifact_models.py`
- `src/cli.py`
- `src/inbox_watcher.py`
- `src/orchestrator.py`
- `src/workflow_baseline.py`
- `src/workflow_completion.py`
- `src/workflow_recovery.py`
- `tests/test_artifact_models.py`
- `tests/test_cli.py`
- `tests/test_crash_harness.py`
- `tests/test_inbox_watcher.py`
- `tests/test_workflow_baseline.py`
- `tests/test_workflow_completion.py`
- `tests/test_workflow_recovery.py`
<!-- audit:scope:end -->

## Umsetzung

> Noch nicht dokumentiert.

## Abweichungen vom Plan

> Keine.

## Verlauf

<!-- audit:history:begin -->
- Runde 1: Umsetzung · Validierung grün · Prüfurteil abgelehnt · 3 neu, 0 geschlossen.
- Runde 1: Umfang erweitert um schemas/orchestrator-artifact-v2.schema.json. Grund: Required paths: schemas/orchestrator-artifact-v2.schema.json Why required for current Slice: Der Slice verlangt einen eigenen protokollierten post-merge-Seiteneffekt und eine laufgebundene Nachlauf-Policy. Das verbindliche Record-Schema erlaubt weder eine neue Seiteneffektklasse noch ein neues RunProfile-Feld. Ohne Schemaänderung würden die benötigten Records bei der Validierung abgewiesen. Die Datei liegt außerhalb des gebundenen Slice-Umfangs.
- Runde 1: Umfang erweitert um README.md, tests/fixtures/cli-argument-evaluation-corpus-v1.json, tests/fixtures/function-size-baseline-v1.json, tests/test_cli_argument_evaluation_corpus.py, tests/test_legacy_verifier.py. Grund: Required paths: README.md, tests/fixtures/cli-argument-evaluation-corpus-v1.json, tests/fixtures/function-size-baseline-v1.json, tests/test_cli_argument_evaluation_corpus.py, tests/test_legacy_verifier.py Why required for current Slice: Die neue CLI-Quittierung fügt öffentliche Optionen hinzu und verändert festgeschriebene Parser-, Laufzeit- und Funktionsgrößen-Nachweise. Der Altprofil-Test erzeugt ein RunProfile ohne das neue Feld und muss angepasst werden. Diese fünf Dateien liegen außerhalb des freigegebenen Slice-Umfangs. Die Standardvalidierung ergab 2498 bestandene und 23 fehlgeschlagene Tests; die verbleibenden Fehler betreffen diese Nachweise. Die Änderungen bleiben uncommittet.
- Runde 2: Korrektur · Validierung grün · Prüfurteil freigegeben · 0 neu, 3 geschlossen.
<!-- audit:history:end -->

## Befunde

<!-- audit:findings:begin -->
### C-01 – Der produktive Quittierungspfad ist ungetestet

Klasse: Befund · Stand: geschlossen

Befund:
> Der produktive Quittierungspfad ist ungetestet. Neu ist der Zweig in `src/orchestrator.py::run_production_workflow` für `--acknowledge-post-merge`. Er ruft `load_resumable_workflow_state`, `resolution_observer`, `ArtifactBridge(resolutions[0].validated_store)` und `acknowledge_unknown_post_merge` auf und setzt danach das Resume fort. Kein Test führt diesen Zweig aus. Der einzige Quittierungstest ersetzt `replay_artifacts` per monkeypatch durch einen SimpleNamespace-Stub mit einem erfundenen Intent-Record (`fingerprint.sha256="0"*64`) und nutzt die Fake-Bridge. Damit ist ungeprüft, ob die echte Record-Kette den Intent-Lookup über `intent_record_id` und `intent.fingerprint` besteht und ob `record_side_effect_result` auf dem echten Store einen gebundenen Resultatrecord schreibt. Ebenso ungeprüft ist, ob `load_resumable_workflow_state` bei offenem `post_merge_hook`-Intent überhaupt erfolgreich lädt, statt wie `WorkflowRecovery` mit „has no runtime reconciler“ abzubrechen. Scheitert dieser Pfad, bleibt ein Lauf nach einem ungewissen Hook-Ausgang dauerhaft blockiert. Akzeptanzkriterium 4 verlangt aber ausdrücklich, dass die CLI-Quittierung ein anschließendes Resume bis `done/` ermöglicht.

Akzeptanztest:
> Ein Test mit echter strukturierter Record-Kette (kein monkeypatch von `replay_artifacts`, keine Fake-Bridge) erzeugt einen offenen `post_merge_hook`-Intent nach bestätigtem Merge. Danach ruft er `orchestrator.run_production_workflow` bzw. den CLI-Einstieg mit `--resume --task-file ... --acknowledge-post-merge <commit> --post-merge-rationale ...` auf. Der Test belegt: ein persistierter Resultatrecord mit Status `acknowledged_unknown`, Merge-Commit, Begründung und Task-Digest; das anschließende Resume schließt ohne erneute Hook-Ausführung ab; ein zweites Resume bleibt idempotent. Negativfälle mit geändertem Task und falschem Commit werden ohne neuen Record abgelehnt.

Antwort des Implementierers, Runde 2 (angenommen):
> Ein Produktionstest erzeugt einen offenen Hook-Intent in einer echten Record-Kette. Die Quittierung schreibt genau einen gebundenen Resultatrecord; das Resume beendet den Lauf ohne erneuten Hook-Aufruf und verschiebt die Aufgabe nach outbox/done/. Geänderte Aufgaben und falsche Commits werden ohne neuen Record abgelehnt; ein weiteres Resume bleibt idempotent.

Abschlussbegründung des Prüfers:
> `test_production_acknowledgment_resumes_real_open_hook_intent` nutzt einen echten `ArtifactStore` ohne monkeypatch von `replay_artifacts`. Der Test erzeugt über `orchestrator.run_production_workflow` einen offenen `post_merge_hook`-Intent nach bestätigtem Merge. Die Negativfälle (geänderter Task, falscher Commit) lehnt er ohne neuen Record ab. Nach der Quittierung prüft er genau einen persistierten Resultatrecord, der an `effect_key` und `logical_id` des Intents gebunden ist und Status, Commit, Begründung und Task-Digest enthält. Er belegt außerdem das Resume ohne erneuten Hook-Lauf, ein idempotentes zweites Resume und den Queue-Move nach `outbox/done/`. Die Attestation ist PASS.

### C-02 – `acknowledge_unknown_post_merge` prüft `if _base_head(root, profile.base_branch or "") != commit and…

Klasse: Befund · Stand: geschlossen

Befund:
> `acknowledge_unknown_post_merge` prüft `if _base_head(root, profile.base_branch or "") != commit and profile.merge_completed_branch:`. `_base_head` wird also immer ausgewertet, auch bei `merge_completed_branch = false`. Für diesen Fall legt `complete_chain` aber einen `post_merge_hook`-Intent auf den Archiv-Commit an. Ist `base_branch` im Run-Profile `null`, etwa weil `resolve_base_branch` scheiterte, läuft `_base_head(root, "")` gegen einen leeren Branchnamen. Das kann zu einem GitTransactionError führen, und dann lässt sich der offene Intent eines Laufs ohne Merge nicht quittieren. Es fehlt ein Test für die Quittierung bei deaktiviertem Merge.

Akzeptanztest:
> Die Prüfung der Base-Branch-Position wird nur bei `profile.merge_completed_branch` ausgewertet (Bedingung umgestellt oder Kurzschluss). Ein Test mit `merge=False` und `base_branch=None` erzeugt einen offenen `post_merge_hook`-Intent am Archiv-Commit. Er zeigt, dass die Quittierung einen `acknowledged_unknown`-Resultatrecord schreibt und das anschließende Resume ohne Hook-Ausführung abschließt.

Antwort des Implementierers, Runde 2 (angenommen):
> Die Base-Branch-Prüfung läuft nur bei aktiviertem Merge. Der Produktionstest deckt merge=False mit base_branch=None und einem offenen Intent am Archiv-Commit ab; Quittierung und Resume gelingen ohne Hook-Ausführung.

Abschlussbegründung des Prüfers:
> Die Bedingung lautet jetzt `profile.merge_completed_branch and _base_head(...) != commit` und wertet dadurch kurzgeschlossen aus. Auch `preflight_chain` und `complete_chain` rufen `_base_head` nur bei aktiviertem Merge auf und nutzen für die Archivpfade `base_ref = base or state.branch_base`. Der Produktionstest mit `merge=False` stellt `base_branch is None` fest, erzeugt den offenen Intent am Archiv-Commit, quittiert ihn mit `acknowledged_unknown` und schließt das Resume ohne Hook-Ausführung ab.

### C-03 – Akzeptanzkriterium 4 verlangt einen Beleg, dass alte Run-Profile und Record-Ketten keinen neuen Nachlauf…

Klasse: Befund · Stand: geschlossen

Befund:
> Akzeptanzkriterium 4 verlangt einen Beleg, dass alte Run-Profile und Record-Ketten keinen neuen Nachlauf auslösen. Neu ist nur `test_pre_hook_run_profile_keeps_its_original_wire_shape`, und der prüft lediglich den Wire-Roundtrip. Kein Test führt `complete_chain` mit einem Profil aus, dessen Feld `post_merge_hook_enabled` fehlt (Reader-Default `False`), und belegt dabei, dass ein vorhandener ausführbarer `.git/hooks/post-merge` nicht ausgeführt und kein `post_merge_hook`-Seiteneffekt geschrieben wird. Das Verhalten hängt allein an `getattr(profile, "post_merge_hook_enabled", False)` in zwei Zweigen und dem Reader-Default. Eine Regression dort würde bei Altketten unbemerkt einen Hook nach dem Merge auslösen.

Akzeptanztest:
> Ein Completion-Test verwendet ein aus einem Legacy-Dokument ohne `post_merge_hook_enabled` rekonstruiertes Run-Profile und einen ausführbaren Standard-Hook mit Marker-Datei. Er deckt beide Zweige ab, `merge_completed_branch` true und false. Der Test belegt: Die Marker-Datei entsteht nicht, der Merge bzw. Archiv-Commit bleibt erhalten, und die Seiteneffekte enthalten keinen Eintrag der Klasse `post_merge_hook`.

Antwort des Implementierers, Runde 2 (angenommen):
> Ein parametrisierten Completion-Test rekonstruiert das Profil aus einem Legacy-Dokument ohne post_merge_hook_enabled. Bei aktiviertem und deaktiviertem Merge bleibt der ausführbare Hook ungestartet; Archiv- beziehungsweise Merge-Commit bleibt erhalten und es entsteht kein Hook-Seiteneffekt.

Abschlussbegründung des Prüfers:
> `test_legacy_profile_completion_does_not_start_post_merge_hook` ist über merge True/False parametrisiert. Der Test rekonstruiert das Profil per `_payload_from_dict` aus einem Dokument ohne `post_merge_hook_enabled` und belegt das Fehlen des Feldes. Er zeigt: Die Marker-Datei des ausführbaren Standard-Hooks entsteht nicht, der Merge- bzw. Archiv-Commit bleibt erhalten, und es gibt keinen Seiteneffekt der Klasse `post_merge_hook`.
<!-- audit:findings:end -->

## Validierung

<!-- audit:validation:begin -->
- Runde 1: `python3 -m pytest tests/ -v -m 'not crash_harness'` · grün · Exitcode 0.

- Runde 2: `python3 -m pytest tests/ -v -m 'not crash_harness'` · grün · Exitcode 0.
<!-- audit:validation:end -->

## Abschlussprüfung

<!-- audit:approval:begin -->
Geprüft:
> Geprüft habe ich: Korrektheit des Quittierungspfads (Task-Identität, Digest, Base-Head nur bei Merge, eindeutiger offener Intent, bestätigter Vorgänger-Commit). Ebenso die Fingerprint-Bindung des Resultatrecords an den echten Intent und die Reader-Defaults des Wire-Formats (Altprofile ohne Feld ergeben `False`). Dazu kommen die Fehlerpfade (Crash-Fenster, Timeout, fehlende Ausführbarkeit, Symlink, Ausgabegrenze), die Sicherheit (Symlink-Prüfung jedes Segments, Auslassen eines vom Zielbranch geänderten versionierten Hooks) und Resume/Idempotenz (kein erneuter Hook-Lauf, zweites Resume ohne neue Records). Außerdem liegt die Pfadgrenze innerhalb der Allowlist.

Größtes Restrisiko:
> `_hook_reason` prüft Symlinks per `lstat` und führt die Datei danach separat aus. Dieses TOCTOU-Fenster erlaubt einem lokalen Angreifer mit Schreibrecht auf das Hook-Verzeichnis einen Austausch zwischen Prüfung und Ausführung. Außerdem scheitert eine wiederholte Quittierung nach Erfolg mit „no unique open“, statt idempotent zu quittieren.

Bruchbedingung:
> Ein Prozess ersetzt den Hook zwischen `_hook_reason` und `subprocess.run` durch einen Symlink oder eine fremde Datei. Oder ein Operator wiederholt nach einem Abbruch unmittelbar nach dem Schreiben des Resultatrecords die Quittierung und hält die Fehlermeldung für einen gescheiterten Lauf, statt einfach ohne Flag fortzusetzen.

Vorab-Risikoanalyse:
> Scheitert dieser Slice in Produktion, dann am ehesten so: Watch pausiert bei offenem Hook-Intent, und der Operator kennt den Quittierungsbefehl nicht, weil die README nur die Flags nennt und keinen Ablauf beschreibt. Denkbar ist auch, dass ein Hook über `core.hooksPath` außerhalb des Repos liegt und nach der Prüfung ausgetauscht wird. Beides betrifft die Bedienbarkeit bzw. ein schmales lokales Angriffsfenster, nicht die Integrität der Record-Kette oder des Merges.
<!-- audit:approval:end -->

<!-- audit:reference:begin -->
Technischer Bezug: Lauf `watch-20260925-173857.063332Z-042e93b2e5d0`, Arbeitseinheit(en) 4; Nachweise in der Recordkette.
<!-- audit:reference:end -->
