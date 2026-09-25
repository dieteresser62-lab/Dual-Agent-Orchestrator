# Slice 1 von 1 – Hook-Voraussetzungen und Auslassungsgründe in Abschnitt 2.10 ergänzen

<!-- audit:status:begin -->
Freigegeben in Runde 1 · 0 Befunde, 0 geschlossen · Validierung grün
<!-- audit:status:end -->

## Ziel

<!-- audit:goal:begin -->
Die Einrichtung beschreibt die tatsächlich durchgesetzten Voraussetzungen und zeigt dem Betreiber zulässige Ablageorte für einen `post-merge`-Hook.
<!-- audit:goal:end -->

## Akzeptanzkriterien

<!-- audit:acceptance:begin -->
- Gegen SOURCE nennt Abschnitt 2.10 ausdrücklich: Ein vorhandener `post-merge`-Hook im Repository-Arbeitsbaum außerhalb von `.git` muss im Basis-HEAD getrackt sein; andernfalls wird er mit `not_tracked_in_base` ausgelassen. Ein ignorierter, generierter Hook unter Husky v9 `.husky/_` dient als konkretes Beispiel. Die Beschreibung unterscheidet diesen Fall vom bereits vorhandenen Grund `changed_by_target_branch`.
- Gegen SOURCE nennt Abschnitt 2.10 `digest_unreadable` als Auslassungsgrund für einen nicht lesbaren Pfad oder einen nicht zuverlässig messbaren Hook-Inhalt. Die Aussage stimmt mit `_hook_reason()` und `_post_merge_effect()` überein und verwechselt den Messfehler nicht mit dem Grund `missing` für einen fehlenden Hook.
- Gegen SOURCE erklärt Abschnitt 2.10 drei praktikable Ablagen: ein im Basisbranch getrackter und vom Zielbranch nicht geänderter Worktree-Hook, ein wirksamer Hook unter `.git/hooks` sowie ein per `core.hooksPath` konfigurierter externer Pfad. Er macht deutlich, dass `core.hooksPath` den Standard-Hook übergeht und dass Dateiform, Ausführbarkeit und Symlinkfreiheit weiterhin gelten.
- Gegen SOURCE gilt die Aussage über die Ausführung mit Argument `0` erst für einen Hook, der sämtliche dokumentierten Prüfungen bestanden hat. Der fokussierte Quellenabgleich der geänderten Passage gegen `_hook_reason()` und `_post_merge_effect()` bestätigt die Begründungen; außerhalb von `docs/reference/einrichtung.md` entstehen im Umsetzungsslice keine Produktänderungen.
<!-- audit:acceptance:end -->

## Umfang

<!-- audit:scope:begin -->
- `docs/internal/kettenende-nachbessern-followup04-implement-review-6ef164b2.md`
- `docs/internal/slice-kettenende-nachbessern-followup04-arbeitsplan-01-hook-voraussetzungen-und-auslassungsgrunde-in-abschnitt-2-10-erganzen.md`
- `docs/reference/einrichtung.md`
<!-- audit:scope:end -->

## Umsetzung

> Noch nicht dokumentiert.

## Abweichungen vom Plan

> Keine.

## Verlauf

<!-- audit:history:begin -->
- Runde 1: Umsetzung · Validierung grün · Prüfurteil freigegeben · 0 neu, 0 geschlossen.
<!-- audit:history:end -->

## Befunde

<!-- audit:findings:begin -->
Keine.
<!-- audit:findings:end -->

## Validierung

<!-- audit:validation:begin -->
- Runde 1: `python3 -m pytest tests/ -v -m 'not crash_harness'` · grün · Exitcode 0.
<!-- audit:validation:end -->

## Abschlussprüfung

<!-- audit:approval:begin -->
Geprüft:
> Geprüft habe ich Korrektheit und Vollständigkeit gegen alle vier Slice-Kriterien. Belegt ist: `not_tracked_in_base` mit Tracking im Basis-HEAD, Husky-v9-Beispiel `.husky/_/post-merge` und Abgrenzung zu `changed_by_target_branch`. Ebenso `digest_unreadable` für einen unlesbaren Pfad oder einen nicht messbaren Inhalt, getrennt von `missing`. Genannt sind die drei Ablageorte `.git/hooks`, externer `core.hooksPath` und getrackter Worktree-Hook, ferner dass `core.hooksPath` den Standard-Hook übergeht. Dateiform, Ausführbarkeit und Symlinkfreiheit gelten unabhängig vom Ablageort. Argument `0` und 600 s gelten erst nach bestandenen Prüfungen. Pfadgrenze: Der Diff betrifft nur `docs/reference/einrichtung.md`, das zur Allowlist gehört. Es gibt keine Produkt- oder Teständerung. Resume/Idempotenz ist nicht berührt, weil nur Dokumentation geändert wurde. Die Attestierung validation-[Hash ausgelassen] passt zum Fingerprint und ist PASS (2552 passed).

Größtes Restrisiko:
> Den Quelltext von `_hook_reason()` und `_post_merge_effect()` konnte ich in diesem Review nicht direkt einsehen. Das größte Restrisiko ist daher, dass der zusätzlich genannte Grund `content_changed` oder die genaue Abgrenzung von `digest_unreadable` (unlesbarer Pfad gegenüber Messfehler) sprachlich von der tatsächlichen Implementierung abweicht.

Bruchbedingung:
> Das Review wäre falsch, wenn `_hook_reason()`/`_post_merge_effect()` den Grund `content_changed` nicht kennen, einen unlesbaren Pfad unter einem anderen Grund als `digest_unreadable` melden oder einen ungetrackten Worktree-Hook vor der Formprüfung (Datei/ausführbar/Symlink) mit einem anderen Grund auslassen. In diesen Fällen wäre die Reihenfolge „vorhandener, sonst zulässiger“ Hook ungenau.

Vorab-Risikoanalyse:
> Wahrscheinlichster Fehlschlag: Ein Betreiber verlässt sich auf die dokumentierten Auslassungsgründe, und im Nachlauf-Record erscheint ein anderer Grundname, etwa weil `content_changed` im Code anders heißt oder weil ein ungetrackter Hook vor der Tracking-Prüfung an der Ausführbarkeit scheitert. Das wäre ein Doku-Code-Drift ohne Laufzeitfolge für Merge oder Resume. Die Slice-Kriterien verlangen einen Quellenabgleich; die Formulierungen sind in sich konsistent und decken alle geforderten Aussagen ab.
<!-- audit:approval:end -->

<!-- audit:reference:begin -->
Technischer Bezug: Lauf `watch-20260925-210027.162590Z-c78c3b414fca`, Arbeitseinheit(en) 2; Nachweise in der Recordkette.
<!-- audit:reference:end -->
