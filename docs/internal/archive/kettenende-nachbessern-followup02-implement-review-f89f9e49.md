# Gesamtaudit – kettenende-nachbessern_followup02-implement

<!-- audit:meta:begin -->
Aufgabe: kettenende-nachbessern_followup02-implement · Zielbranch: `feature/fehler-parameter-sweep-liefert-nie-ein-ergebnis-07e5c0fab2a5f4e3` · Lauf: `watch-20260925-201936.556814Z-94b9922493e9` · Stand: abgeschlossen
<!-- audit:meta:end -->

## Übersicht

<!-- audit:overview:begin -->
| Slice | Titel | Stand | Commit | Runden | Befunde |
|---:|---|---|---|---:|---:|
| 1 | Quittierte Ungewissheit warnen und Hook-Inhalt im Intent binden | freigegeben | 285f7c36 | 2 | 0 |
<!-- audit:overview:end -->

## Befunde

<!-- audit:findings:begin -->
| ID | Herkunft | Klasse | Stand | Titel |
|---|---|---|---|---|
| C-01 | Slice DISCOVERY | Befund | offen | Ein nicht lesbarer Hook blockiert den Abschluss eines bereits gemergten Laufs dauerhaft |
| C-02 | Slice DISCOVERY | Befund | offen | Ein unversionierter Hook im Arbeitsbaum wird ausgeführt, obwohl die Pläne verlangen, dass ein Hook im… |
| C-03 | Slice DISCOVERY | Befund | offen | `_post_merge_effect()` wählt bei mehreren passenden `post_merge_hook`-Effekten stillschweigend den letzten… |
| C-01 | Abnahme | Befund | offen | Ein nicht lesbarer Hook blockiert den Abschluss eines bereits gemergten Laufs dauerhaft. `_post_merge_effect()` in `src/workflow_completion.py` ruft `_hook_content_digest(hook)` vor `executor.execute(...)` auf, also außerhalb des Intent/Result-Paares. `_hook_content_digest()` macht aus jedem `OSError` außer `FileNotFoundError`/`NotADirectoryError` bei `lstat()` oder `os.open()` eine `GitTransactionError`. Das betrifft etwa `PermissionError` bei einer Hook-Datei ohne Leserecht, ein nicht durchsuchbares `core.hooksPath`-Verzeichnis oder `ELOOP`. Dann entsteht weder ein Intent noch ein Ergebnis. Jedes Resume scheitert erneut. Die Quittierung hilft nicht, weil sie einen offenen Intent verlangt. Das gilt auch bei `merge_completed_branch = false`, obwohl dann nie ein Hook läuft. Vor Followup02 endete dieser Fall als `failed` mit Warnung. Der Plan verlangt dagegen: Hook-Probleme warnen, der Merge bleibt bestehen und der Auftrag gelangt nach `outbox/done/`. |
| C-02 | Abnahme | Befund | offen | Ein unversionierter Hook im Arbeitsbaum wird ausgeführt, obwohl die Pläne verlangen, dass ein Hook im versionierten Worktree bereits im Basis-HEAD getrackt ist. Ursprungsplan: „muss bereits im Basis-HEAD getrackt sein“. Followup01: „auch fehlendes Tracking im Basis-HEAD verhindert die Ausführung“. `_hook_reason()` meldet `changed_by_target_branch` nur bei `(target_entry and not base_entry) or tree_entry(fork) != target_entry`. Fehlt der Pfad in Basis, Ziel und Merge-Basis gleichermaßen, sind alle `ls-tree`-Ausgaben leer und die Prüfung besteht. Ein Beispiel ist eine per `.gitignore` ignorierte Datei `.githooks/post-merge` bei `core.hooksPath=.githooks`. Nach der lstat-Prüfung wird sie auf dem Host ausgeführt. Ignorierte Dateien kann ein Agent im Worktree anlegen, ohne dass Pfad-Scope oder Sauberkeitsprüfung anschlagen. Die Sicherheitsgrenze aus Plan-Befund C-02 ist damit unvollständig. Der Test `test_unchanged_tracked_hook_runs_and_untracked_base_hook_is_skipped` prüft nur einen vom Zielbranch hinzugefügten, getrackten Hook. |
| C-03 | Abnahme | Befund | offen | `_post_merge_effect()` wählt bei mehreren passenden `post_merge_hook`-Effekten stillschweigend den letzten aus: `next((item for item in reversed(replay.side_effects) ... and item.operation[1] == commit), None)`. Der freigegebene Followup02-Plan verlangt ausdrücklich: „Mehrere passende Intents sind ein Widerspruch und führen fail-closed zum Stopp, statt einen davon willkürlich zu wählen.“ `acknowledge_unknown_post_merge()` setzt das mit `len(effects) != 1` um, der Resume-Pfad der Completion nicht. Bei einer widersprüchlichen Kette, etwa einem offenen Dreifeld- und einem abgeschlossenen Vierfeld-Intent für denselben Commit, kann Resume ein Ergebnis übernehmen oder einen offenen Intent übergehen, statt anzuhalten. Kein Test deckt den Mehrfachfall ab, und eine Abweichung ist nicht dokumentiert. |
<!-- audit:findings:end -->

## Halte und Entscheidungen

<!-- audit:holds:begin -->
Keine.
<!-- audit:holds:end -->

## Abnahmereview

<!-- audit:acceptance-review:begin -->
Abnahmereview abgeschlossen.

Geprüft:
> Geprüft wurde der vollständige Branch-Diff gegen `aef5dc3d`. Pfade: alle Änderungen liegen in authorized_paths. Die Followup02-Kriterien sind erfüllt: die Warnung zu `acknowledged_unknown` mit Pfad und Commit (Test prüft per caplog), die Vierfeld-Operation mit Digest (`test_standard_post_merge_hook_runs_once_after_merge` prüft `sha256(hook.read_bytes())`), die Validierung in `SideEffectPayload` für 3 oder 4 Felder mit Digest-Format, die Wiedererkennung von Alt- und Vierfeldpaaren ohne neuen Intent, der Grund `content_changed` nach dem Intent, fail-closed bei offenem Intent und `unavailable` ohne Ausführungsfreigabe. Geprüft wurden außerdem Archivmuster und frühe Prüfung, die RunProfile-Wire-Kompatibilität, die Gruppenbeendigung, der ls-tree-Vergleich, die CLI-Quittierung und die Doku. Die Attestation ist PASS (2541/0) für diesen Fingerprint.

Größtes Restrisiko:
> Zwei Restrisiken bleiben. Erstens: Die Quittierung hasht `read_text().encode()`, das bei CRLF-Aufträgen Zeilenenden übersetzt. Ob `state.task_digest` bytebasiert ist, lässt der Snapshot nicht entscheiden, weil die Berechnung nicht im Diff steht und die Tests nur LF-Dateien nutzen. Zweitens: Zwischen Digest-Prüfung und `Popen` bleibt ein TOCTOU-Fenster, das im Plan als Grenze benannt ist.

Bruchbedingung:
> Ein Operator setzt einen gemergten Lauf mit einem nicht lesbaren Hook fort und kommt ohne Ausweg nicht weiter (C-01). Oder eine ignorierte Datei `.githooks/post-merge` läuft auf dem Host (C-02). Oder eine CRLF-Auftragsdatei scheitert dauerhaft an der Digest-Prüfung der Quittierung.

Vorab-Risikoanalyse:
> Scheitert die Abschlusskette im Betrieb, dann am wahrscheinlichsten an Fehlerpfaden außerhalb des Intent/Result-Paares. Die neue Digest-Messung kann nach einem gültigen Merge eine nicht quittierbare Dauerblockade erzeugen (C-01). Sicherheitsseitig ist das Risiko ein unversionierter, ignorierter Hook im Worktree, den die Tracking-Regel nicht erfasst (C-02). Zweitrangig sind widersprüchliche Ketten mit mehreren Intents (C-03) und Unterschiede bei der Digest-Berechnung von CRLF-Aufträgen im Quittierungspfad.

### C-01 – Ein nicht lesbarer Hook blockiert den Abschluss eines bereits gemergten Laufs dauerhaft. `_post_merge_effect()` in `src/workflow_completion.py` ruft `_hook_content_digest(hook)` vor `executor.execute(...)` auf, also außerhalb des Intent/Result-Paares. `_hook_content_digest()` macht aus jedem `OSError` außer `FileNotFoundError`/`NotADirectoryError` bei `lstat()` oder `os.open()` eine `GitTransactionError`. Das betrifft etwa `PermissionError` bei einer Hook-Datei ohne Leserecht, ein nicht durchsuchbares `core.hooksPath`-Verzeichnis oder `ELOOP`. Dann entsteht weder ein Intent noch ein Ergebnis. Jedes Resume scheitert erneut. Die Quittierung hilft nicht, weil sie einen offenen Intent verlangt. Das gilt auch bei `merge_completed_branch = false`, obwohl dann nie ein Hook läuft. Vor Followup02 endete dieser Fall als `failed` mit Warnung. Der Plan verlangt dagegen: Hook-Probleme warnen, der Merge bleibt bestehen und der Auftrag gelangt nach `outbox/done/`.

Klasse: Befund · Stand: offen

Befund:
> Ein nicht lesbarer Hook blockiert den Abschluss eines bereits gemergten Laufs dauerhaft. `_post_merge_effect()` in `src/workflow_completion.py` ruft `_hook_content_digest(hook)` vor `executor.execute(...)` auf, also außerhalb des Intent/Result-Paares. `_hook_content_digest()` macht aus jedem `OSError` außer `FileNotFoundError`/`NotADirectoryError` bei `lstat()` oder `os.open()` eine `GitTransactionError`. Das betrifft etwa `PermissionError` bei einer Hook-Datei ohne Leserecht, ein nicht durchsuchbares `core.hooksPath`-Verzeichnis oder `ELOOP`. Dann entsteht weder ein Intent noch ein Ergebnis. Jedes Resume scheitert erneut. Die Quittierung hilft nicht, weil sie einen offenen Intent verlangt. Das gilt auch bei `merge_completed_branch = false`, obwohl dann nie ein Hook läuft. Vor Followup02 endete dieser Fall als `failed` mit Warnung. Der Plan verlangt dagegen: Hook-Probleme warnen, der Merge bleibt bestehen und der Auftrag gelangt nach `outbox/done/`.

Akzeptanztest:
> Gegen SOURCE löst ein Lese- oder Inspektionsfehler bei der Digest-Messung (etwa `PermissionError` bei `lstat` oder `open`) keine Ausnahme außerhalb des Seiteneffekts aus. Er führt zu einem Vierfeld-Intent mit eindeutig definiertem Platzhalter und zu einem terminalen `skipped`-Ergebnis mit auswertbarem Grund. Der Lauf endet mit einer Warnung, der Merge bleibt erhalten, und ein erneutes Resume führt den Hook nicht aus. Ein fokussierter Test in `tests/test_workflow_completion.py` simuliert den Fehler per monkeypatch und prüft Merge-HEAD, Ergebnisstatus, Grund und den erfolgreichen Abschluss, sowohl mit als auch ohne Merge.

### C-02 – Ein unversionierter Hook im Arbeitsbaum wird ausgeführt, obwohl die Pläne verlangen, dass ein Hook im versionierten Worktree bereits im Basis-HEAD getrackt ist. Ursprungsplan: „muss bereits im Basis-HEAD getrackt sein“. Followup01: „auch fehlendes Tracking im Basis-HEAD verhindert die Ausführung“. `_hook_reason()` meldet `changed_by_target_branch` nur bei `(target_entry and not base_entry) or tree_entry(fork) != target_entry`. Fehlt der Pfad in Basis, Ziel und Merge-Basis gleichermaßen, sind alle `ls-tree`-Ausgaben leer und die Prüfung besteht. Ein Beispiel ist eine per `.gitignore` ignorierte Datei `.githooks/post-merge` bei `core.hooksPath=.githooks`. Nach der lstat-Prüfung wird sie auf dem Host ausgeführt. Ignorierte Dateien kann ein Agent im Worktree anlegen, ohne dass Pfad-Scope oder Sauberkeitsprüfung anschlagen. Die Sicherheitsgrenze aus Plan-Befund C-02 ist damit unvollständig. Der Test `test_unchanged_tracked_hook_runs_and_untracked_base_hook_is_skipped` prüft nur einen vom Zielbranch hinzugefügten, getrackten Hook.

Klasse: Befund · Stand: offen

Befund:
> Ein unversionierter Hook im Arbeitsbaum wird ausgeführt, obwohl die Pläne verlangen, dass ein Hook im versionierten Worktree bereits im Basis-HEAD getrackt ist. Ursprungsplan: „muss bereits im Basis-HEAD getrackt sein“. Followup01: „auch fehlendes Tracking im Basis-HEAD verhindert die Ausführung“. `_hook_reason()` meldet `changed_by_target_branch` nur bei `(target_entry and not base_entry) or tree_entry(fork) != target_entry`. Fehlt der Pfad in Basis, Ziel und Merge-Basis gleichermaßen, sind alle `ls-tree`-Ausgaben leer und die Prüfung besteht. Ein Beispiel ist eine per `.gitignore` ignorierte Datei `.githooks/post-merge` bei `core.hooksPath=.githooks`. Nach der lstat-Prüfung wird sie auf dem Host ausgeführt. Ignorierte Dateien kann ein Agent im Worktree anlegen, ohne dass Pfad-Scope oder Sauberkeitsprüfung anschlagen. Die Sicherheitsgrenze aus Plan-Befund C-02 ist damit unvollständig. Der Test `test_unchanged_tracked_hook_runs_and_untracked_base_hook_is_skipped` prüft nur einen vom Zielbranch hinzugefügten, getrackten Hook.

Akzeptanztest:
> Gegen SOURCE lässt `_hook_reason()` einen Hook-Pfad innerhalb des Worktrees (außerhalb von `.git`) aus, wenn der Basis-HEAD keinen Baumeintrag dafür hat, auch wenn Merge-Basis und Zielbranch ebenfalls keinen haben. Der Grund ist eindeutig auswertbar. Ein fokussierter Test in `tests/test_workflow_completion.py` setzt `core.hooksPath=.githooks`. Er legt `.githooks/post-merge` als ignorierte, unversionierte, ausführbare Marker-Datei an. Nach der Completion existiert der Marker nicht, das Ergebnis ist `skipped` mit diesem Grund, und der Merge bleibt erhalten. Ein unveränderter getrackter Hook sowie `.git/hooks` und externe Hooks bleiben ausführbar.

### C-03 – `_post_merge_effect()` wählt bei mehreren passenden `post_merge_hook`-Effekten stillschweigend den letzten aus: `next((item for item in reversed(replay.side_effects) ... and item.operation[1] == commit), None)`. Der freigegebene Followup02-Plan verlangt ausdrücklich: „Mehrere passende Intents sind ein Widerspruch und führen fail-closed zum Stopp, statt einen davon willkürlich zu wählen.“ `acknowledge_unknown_post_merge()` setzt das mit `len(effects) != 1` um, der Resume-Pfad der Completion nicht. Bei einer widersprüchlichen Kette, etwa einem offenen Dreifeld- und einem abgeschlossenen Vierfeld-Intent für denselben Commit, kann Resume ein Ergebnis übernehmen oder einen offenen Intent übergehen, statt anzuhalten. Kein Test deckt den Mehrfachfall ab, und eine Abweichung ist nicht dokumentiert.

Klasse: Befund · Stand: offen

Befund:
> `_post_merge_effect()` wählt bei mehreren passenden `post_merge_hook`-Effekten stillschweigend den letzten aus: `next((item for item in reversed(replay.side_effects) ... and item.operation[1] == commit), None)`. Der freigegebene Followup02-Plan verlangt ausdrücklich: „Mehrere passende Intents sind ein Widerspruch und führen fail-closed zum Stopp, statt einen davon willkürlich zu wählen.“ `acknowledge_unknown_post_merge()` setzt das mit `len(effects) != 1` um, der Resume-Pfad der Completion nicht. Bei einer widersprüchlichen Kette, etwa einem offenen Dreifeld- und einem abgeschlossenen Vierfeld-Intent für denselben Commit, kann Resume ein Ergebnis übernehmen oder einen offenen Intent übergehen, statt anzuhalten. Kein Test deckt den Mehrfachfall ab, und eine Abweichung ist nicht dokumentiert.

Akzeptanztest:
> Gegen SOURCE ermittelt `_post_merge_effect()` alle `post_merge_hook`-Effekte mit gleicher Work-Unit-ID und gleichem Merge-Commit. Bei mehr als einem Treffer stoppt es fail-closed mit einer Diagnose, bevor ein Hook-Pfad aufgelöst, ein Digest gemessen, ein Intent geschrieben oder der Hook gestartet wird. Ein fokussierter Test in `tests/test_workflow_completion.py` legt für denselben Commit zwei passende Effekte an. Er prüft den Stopp, dass kein neuer Record entsteht und dass der Hook-Zähler unverändert bleibt. Der Fall mit genau einem Treffer verhält sich wie bisher.
<!-- audit:acceptance-review:end -->
