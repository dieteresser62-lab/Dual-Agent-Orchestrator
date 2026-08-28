# Claude-Review: providerweiter Hotfix der Versionspolitik

**Reviewer:** Claude (manuell, adversarial, read-only) · **Datum:** 28. August 2026 ·
**Geprüfter Snapshot:** `372f2bb6507cbed5210c084035867d3770ca4caf44c5733af2224a83d10fe2d6`

## 1. Ergebnis vorab

**Freigabe.** Keine offene reviewer-eigene Finding-ID, kein Blocker.

Der Hotfix tut genau das, was er ankündigt: Er macht `same-major-forward` zur
einzigen providerweiten Versionspolitik, hebt Codex von der Minor- auf die
Major-Schranke und lässt jede nachgelagerte Prüfung unverändert wirksam. Ich
habe alle zehn Prüfdimensionen mit eigenen Gegenproben gefahren, darunter neun
Loader-Mutationen, 22 Versionsfälle über beide Provider und vier
Pflichtflag-Gegenbeispiele auf dem realen Runtimepfad mit den tatsächlich
installierten CLIs. Der gemeldete Ausfall — Stopp allein wegen einer späteren
Codex-Minorversion — ist nachweislich behoben, ohne dass eine
Sicherheitsschranke fällt.

Zum Verfahren: Mein vorheriges Review dieses Pakets endete am 28. August mit
einer fail-closed Verweigerung (`H-01` Snapshotdrift, `H-02` Scopeüberschreitung).
Beide Punkte sind mit diesem Auftrag beantwortet — der jetzt vorliegende Stand
ist fingerprintgebunden benannt und blieb über meine gesamte Prüfung stabil,
und die Verengung auf eine providerweite Politik ist nun erklärter
Auftragsgegenstand mit eigenen Prüfdimensionen statt stiller Beifang. `H-01`
und `H-02` sind damit erledigt und werden nicht fortgeschrieben.

## 2. Selbst erhobener Stand und Prüfumfang

Alle Werte selbst erhoben, nicht aus dem Auftrag übernommen.

| Merkmal | Gemessen | Erwartet | |
|---|---|---|---|
| Worktree | `/tmp/dao-provider-version-policy-hotfix` | identisch | ✓ |
| Branch | `feature/provider-version-policy-hotfix` | identisch | ✓ |
| `HEAD` = Merge-Base zu `master` | `e6760e9a08e5c0397f99dce912acadf5596ca4ce` | identisch | ✓ |
| Geänderte getrackte Pfade | 4, exakt die genannten | 4 | ✓ |
| Diffumfang | 77 Einfügungen, 16 Löschungen | 77 / 16 | ✓ |
| Paketfingerprint | `372f2bb6507cbed5210c084035867d3770ca4caf44c5733af2224a83d10fe2d6` | identisch | ✓ |
| `git diff --check master` | Exit 0 | ohne Befund | ✓ |

Blob-IDs des geprüften Standes:

```
8c27bbfb1e98af3b8068d48c2a5c5cf1762cd69d schemas/native-provider-schema-capabilities-v1.json
713b4ccaad7bdbf9ef45d0460f3e7d3a42a344ca src/native_provider_schema.py
bacf4042a322ec872710138fd907fd44065cf49f tests/test_agent_runtime.py
c5c1b5d36e1d00a3afa3fe9253b7677afc60fce8 tests/test_native_provider_schema.py
```

**Stabilität.** Anders als beim Vorgängerpaket habe ich den Stand zu Beginn
(11:29:34) und unmittelbar vor der Schreiboperation (11:31:52) gemessen:
identischer Pfadsatz, identische Blobs, identischer Fingerprint. Die
Dateizeitstempel liegen sämtlich vor Reviewbeginn. Kein Drift.

Im Worktree liegt zusätzlich mein untracktes Verweigerungsreview des
Vorgängerstands (`…-d8a41f52.md`). Es ist keine getrackte Änderung, geht
folgerichtig nicht in den Fingerprint ein und ist nicht Teil des Pakets.

**Reviewgrenze.** Kein Provider-, Netz-, Canary- oder Orchestratorlauf, kein
`run_task`, kein Watcher, kein echtes `codex exec`/`claude`-Prompt. Kein
Staging, kein Commit, kein Branchwechsel. Weder Produktivcode noch Tests
geändert. Verwendet habe ich ausschließlich lokale, nicht aufrufende
Inspektionen (`codex --version`, `codex exec --help`, `claude --version`,
`claude --help`), In-Memory-Registermutationen in einem Temporärverzeichnis und
die freigegebene fokussierte Suite. Die vollständige Suite habe ich nicht
ausgeführt. Weder die übernommene Codex-Evidenz (`53 passed`, `1094 passed`,
beide `capability_verified=True`) noch meine eigenen Läufe sind eine
Orchestrator-Attestierung.

## 3. Befunde

**Keine.** Ich habe keinen ausführbaren Defekt gefunden, der vor Übernahme des
Hotfixes behoben werden müsste. Es bleibt keine reviewer-eigene Finding-ID
offen.

## 4. Geprüfte Dimensionen ohne Befund

### D1 — Der Loader bindet jeden Eintrag auf genau `same-major-forward`

`load_capability_table` vergleicht jede Providerzeile gegen die
Modulkonstante `PROVIDER_VERSION_POLICY` und lehnt jede Abweichung ab. Neun
eigene Registermutationen, jeweils gegen eine Temporärkopie:

| Mutation | Ergebnis |
|---|---|
| unverändertes Register | akzeptiert |
| `claude` → `same-minor-forward` | abgelehnt, „must use the provider-wide version policy" |
| `claude` → `same-patch-forward` | abgelehnt |
| `claude` → `any` | abgelehnt |
| `claude` → `SAME-MAJOR-FORWARD` | abgelehnt (keine Groß-/Kleinschreibungstoleranz) |
| `claude` → `"same-major-forward "` | abgelehnt (keine Whitespacetoleranz) |
| `claude` → `""` | abgelehnt, „version_policy must be non-blank text" |
| Feld vollständig entfernt | abgelehnt, „version_policy must be non-blank text" |
| `codex` → `same-minor-forward` | abgelehnt |

Die Bindung gilt für *jeden* Eintrag, nicht nur für die beiden bekannten: Die
Prüfung läuft in der Schleife über `document["providers"]`, ist also auch für
einen künftig ergänzten dritten Provider wirksam. `VERSION_POLICIES` ist
rückstandsfrei entfernt; außerhalb meines eigenen Vorgängerreviews gibt es
repositoryweit keine Referenz mehr. Ein zweites, unabhängig editierbares
Politikvokabular existiert nicht.

Bemerkenswert und richtig: Das Registerfeld `version_policy` bleibt bestehen,
obwohl nur noch ein Wert zulässig ist. Es ist damit eine Redundanz, die als
Tripwire wirkt — eine handeditierte Registerzeile mit abweichender Politik
fällt beim Laden auf, statt stillschweigend ignoriert zu werden.

### D2 — Runtime-Semantik ist exakt `actual >= baseline` bei gleicher Major

`compatible_cli_version` vergleicht zuerst das Versionstripel (`actual < baseline`
→ `False`) und danach die Major-Gleichheit. Beide Bedingungen sind notwendig;
es gibt keinen dritten Pfad mehr und keine providerabhängige Verzweigung.

### D3 und D4 — Versionsmatrix über beide Provider

22 Fälle gegen die echte `compatible_cli_version` und zusätzlich gegen das
Gate `assert_provider_capabilities`. Beide Ebenen stimmen in jedem Fall
überein:

| Claude | Ergebnis | Codex | Ergebnis |
|---|---|---|---|
| `2.1.241 (Claude Code)` | akzeptiert | `codex-cli 0.147.0` | akzeptiert |
| `2.1.246 (Claude Code)` | akzeptiert | `codex-cli 0.147.1` | akzeptiert |
| `2.9.0 (Claude Code)` | akzeptiert | `codex-cli 0.148.0` | akzeptiert |
| `2.999.0 (Claude Code)` | akzeptiert | `codex-cli 0.150.1` | akzeptiert |
| `2.1.240 (Claude Code)` | abgelehnt | `codex-cli 0.999.0` | akzeptiert |
| `2.0.999 (Claude Code)` | abgelehnt | `codex-cli 0.146.9` | abgelehnt |
| `3.0.0 (Claude Code)` | abgelehnt | `codex-cli 1.0.0` | abgelehnt |
| `1.9.9 (Claude Code)` | abgelehnt | `codex-cli 1.999.0` | abgelehnt |

Die geforderten Fälle sind sämtlich enthalten und verhalten sich wie
spezifiziert. Der Downgradeschutz greift auch innerhalb derselben Major
unterhalb der Baseline (`2.0.999`), nicht nur bei Major-Sprüngen.

### D5 — Unparsbare Formate und Versionsgrammatik neuer Provider

Fail-closed über `fullmatch`: `2.1.246` ohne Suffix, `claude 2.1.246 (Claude Code)`,
`2.1.246 (Claude Code) ` mit Leerzeichen, `codex-cli 0.147`,
`codex-cli 0.147.0-rc1` und `codex-cli 0.150.1\t` scheitern sämtlich mit
„unsupported format" und werden vom Gate gestoppt.

Wichtiger für die Zukunft: Ein neuer Provider **ohne** Eintrag in
`CLI_VERSION_PATTERNS` wird bereits beim Laden der Tabelle abgewiesen. Meine
Probe mit einer zusätzlichen, ansonsten wohlgeformten `gemini`-Zeile endet mit
„no CLI version grammar for provider gemini". Die breitere Politik senkt die
Eintrittsschwelle für neue Provider also nicht.

### D6 — Keine Umgehung der nachgelagerten Capability-Prüfung

In `verify_agent_capabilities` folgt die Flagprüfung der Versionsprüfung
unbedingt; die Versionsprüfung kann nur den *eigenen* Schritt überspringen
(Fast Path bei exaktem Musterreffer), nie die Hilfeinspektion. Vier
Gegenbeispiele auf dem realen Runtimepfad, jeweils mit ausreichend neuer
Version und der echten Hilfeausgabe der installierten CLI, in der genau ein
Pflichtflag unkenntlich gemacht wurde:

| Fall | Ergebnis |
|---|---|
| Codex `0.150.1`, `--output-schema` fehlt | abgelehnt, „missing required capability flags" |
| Codex `0.150.1`, `--output-last-message` fehlt | abgelehnt |
| Claude `2.9.0`, `--json-schema` fehlt | abgelehnt |
| Claude `2.9.0`, `--effort` fehlt | abgelehnt |

Die tatsächlich installierten CLIs sind `codex-cli 0.150.1` (Hilfe 3957 Bytes)
und `2.1.250 (Claude Code)` (Hilfe 18028 Bytes); beide enthalten ihre
jeweiligen Pflichtflags vollständig.

### D7 — Beide Regressionen nutzen eine spätere Minor und den echten Runtimepfad

Der Claude-Test wurde von einer Patchversion (`2.1.246`) auf eine Minorversion
(`2.9.0`) gehoben und entsprechend umbenannt; der neue Codex-Test verwendet
`0.150.1` gegen die Baseline `0.147.0`. Beide rufen die echte Funktion
`verify_agent_capabilities` auf und ersetzen nur Binärauflösung und
Kommandoausführung — es wird also nicht bloß `compatible_cli_version` isoliert
getestet. Beide Versionen verfehlen das exakte Muster und laufen damit
zwingend durch die Policy-Prüfung.

Beide Tests verwenden Fake-Adapter mit verkürztem Pflichtflagsatz; das folgt
dem bereits vorher etablierten Muster. Ich habe die daraus folgende Lücke
selbst geschlossen, indem ich dieselben Fälle zusätzlich mit den **realen**
Adaptern aus `build_agent_registry()` und der echten Hilfeausgabe gefahren
habe: Codex `0.147.0`, `0.148.0`, `0.150.1`, `0.999.0` verifizieren; `0.146.9`,
`1.0.0` und `0.150` werden abgelehnt. Claude `2.1.241`, `2.9.0`, `2.999.0`
verifizieren; `2.1.240`, `3.0.0` und `2.1.246` werden abgelehnt. Das ist der
eigentliche Beleg, dass der gemeldete Ausfall behoben ist.

### D8 — Featuretabellen, Probe-Evidenz, Exceptions, Transportprofile, Projektion, Verträge

Der Registerdiff besteht aus genau zwei Zeilen (`-same-minor-forward` /
`+same-major-forward`). Probe-Baselines (`codex-cli 0.147.0`,
`2.1.241 (Claude Code)`), Featuretabellen, Transportprofile und das
Ausnahmeregister sind unberührt; letzteres führt weiterhin 7 Codex- und 6
Claude-Einträge.

Für die Projektion habe ich nicht argumentiert, sondern gemessen: Ich habe die
`master`-Fassung von `src/native_provider_schema.py` samt `master`-Registern in
ein Temporärverzeichnis materialisiert und alle Projektionen beider Provider
über alle fünf Writer-/Request-Schemas doppelt berechnet — einmal mit der
`master`-Fassung, einmal mit der Hotfix-Fassung.

```
verglichene Projektionen: 10
abweichende Projektionshashes: keine — byteidentisch
```

Die an den Request-Fingerprint gebundenen Schemahashes ändern sich also
nachweislich nicht. Die nativen Structured-Output-Verträge liegen außerhalb des
Pfadsatzes und sind unverändert.

### D9 — Major `0` als gemeinsame Kompatibilitätsgrenze

Konkret bewertet, mit dem Ergebnis: **kein Defekt, aber die schwächste Stelle
des Vertrags.**

Nach Semver-Konvention dürfen Minorsprünge innerhalb von Major `0` brechen.
`same-major-forward` ist dort also die schwächste Schranke unterhalb von
„beliebig vorwärts". Ich habe versucht, daraus ein ausführbares Gegenbeispiel
zu bauen, und keines gefunden, weil die Politik nur *eine* von vier Schranken
ist und die übrigen drei unverändert und versionsunabhängig greifen:

1. die sechs beziehungsweise vierzehn Pflichtflags gegen die **tatsächliche**
   Hilfeausgabe der installierten CLI — nachgewiesen wirksam in D6;
2. der Transportprofilvergleich beim Aufbau des Aufrufs
   (`agent_adapters.py:416` für Codex, `:728` für Claude), der die normalisierte
   Kommandoform gegen das registrierte Profil stellt;
3. das Feature-Gating der Schemaprojektion, das jedes benötigte Merkmal
   positiv im Register verlangt — Gegenprobe: `positional_tuple`, `const_list`
   und `nested_one_of` werden für Codex auch bei akzeptierter Version `0.999.0`
   abgelehnt;
4. die unveränderte fail-closed Writer- und Domänenvalidierung jedes
   Resultats.

Der ungünstigste realistische Fall ist eine spätere `0.x`, die alle
Pflichtflags behält, aber einen Schemadialekt anders auslegt. Das erzeugt einen
**lauten** Fehlschlag — Providerablehnung des Schemas oder lokal ungültiges
Resultat, in beiden Fällen klassifizierte Störung mit Gate —, keine stille
Annahme eines falschen Ergebnisses. Die Richtung „neuere CLI kann *mehr*" ist
ebenfalls ungefährlich: Die Projektion entfernt weiterhin dieselben
Ausdrucksmittel, und die registrierten lokalen Kompensationen laufen unverändert.

Hinzu kommt, dass die Politik für Claude bereits vor diesem Hotfix
`same-major-forward` war. Der Hotfix erweitert die Politik also nicht auf ein
neues Risikoniveau, sondern zieht Codex auf das bereits produktiv wirksame
Niveau nach und macht es zum ausdrücklichen Vertrag statt zum Zufall zweier
Einzelzeilen.

### D10 — Engführung

Vier Dateien, davon zwei Tests. Der Produktivteil besteht aus einer
Registerzeile und drei Stellen in `native_provider_schema.py` (Konstante,
Loaderprüfung, Wegfall der Fallunterscheidung). Weder Workflow-, State-,
Resume-, Watch-, Queue-, Record-, Orchestrator- noch Provideraufruflogik ist
berührt; keine dieser Dateien liegt im Pfadsatz.

### Eigene Testausführung

`python3 -m pytest tests/test_native_provider_schema.py tests/test_agent_runtime.py -q`
→ `53 passed in 0.25s`, in zwei Läufen stabil reproduziert. Die
Negativabdeckung der Versionsmatrix ist beim Umbau nicht geschrumpft: Der
entfallene Fall `codex 0.148.0` ist durch `codex 1.0.0` ersetzt, die
Untergrenzen `0.146.9` und `2.1.240` sowie der Major-Sprung `3.0.0` bleiben
erhalten, und mit
`test_every_provider_must_use_the_shared_forward_version_policy` kommt eine
Negativprobe für den Loader hinzu, die es vorher nicht gab.

## 5. Größte verbleibende Gefahr und realistische Bruchbedingung

Die größte verbleibende Gefahr ist nicht die Versionsarithmetik, sondern die
**Alterung der Probe-Evidenz**. Das Register behauptet die an
`codex-cli 0.147.0` erhobenen Fähigkeiten für jede spätere `0.x` weiter, ohne
sie erneut zu erheben. Der Auftrag benennt diesen Wechsel ausdrücklich — die
Baseline ist Untergrenze und Fähigkeitsquelle, keine Release-Allowlist mehr —,
und genau daraus folgt: Je größer der Abstand zwischen Baseline und
installierter Version wird, desto weniger sagt die Featuretabelle über die
tatsächlich laufende CLI aus. Heute sind das drei Minorversionen; es gibt
keinen Mechanismus, der auf wachsenden Abstand aufmerksam macht.

Nachgeordnet: Beide Runtime-Regressionen prüfen Fake-Adapter, nicht die realen
Adapter aus `build_agent_registry()`. Eine künftige Änderung des realen
Pflichtflagsatzes oder des Versionsmusters bliebe von diesen beiden Tests
unbemerkt; abgedeckt ist sie nur durch andere Tests und, in dieser Runde,
durch meine manuellen Proben.

**Realistische Bruchbedingung:** Eine künftige Codex-`0.x` behält alle sechs
Pflichtflags und das Transportprofil bei, ändert aber die Auslegung eines
Schemakonstrukts, das die Featuretabelle noch als positiv geprüft führt.
Versionsprüfung, Flagprüfung und Profilvergleich laufen durch, die Projektion
bleibt unverändert, und der Fehlschlag zeigt sich erst im ersten realen
Providerlauf als Strukturfehler mit Gate — mit einer Diagnose, die auf den
Transport zeigt, während die Ursache im veralteten Fähigkeitsnachweis liegt.

## 6. Pre-Mortem

In drei Monaten scheitert dieser Hotfix am wahrscheinlichsten daran, dass
niemand die Probe-Baseline nachgezogen hat. Codex steht dann bei `0.16x` oder
`0.17x`, das Register behauptet weiterhin `0.147.0`, und der erste
Dialektunterschied erscheint als sporadischer Structured-Output-Fehler mit
Resume-Gate. Die Ursachensuche beginnt beim Transport oder beim Prompt, weil
nichts im System auf den gewachsenen Abstand zwischen geprüfter und laufender
Version hinweist — der Hotfix hat die Reibung beseitigt, die früher genau
diesen Abstand sichtbar machte. Ein billiger Frühindikator wäre eine Warnung,
sobald die installierte Minorversion die Baseline um mehr als eine bestimmte
Spanne übersteigt; das ist bewusst nicht Teil dieses Hotfixes und hier nur als
Restrisiko notiert.

Zweitwahrscheinlich wird ein dritter Provider registriert, dessen Hersteller
in Major `0` tatsächlich brechend arbeitet und für den `same-major-forward` zu
weit wäre. Die Politik ist jetzt providerweit verdrahtet, eine engere Schranke
für genau diesen Provider erforderte also einen Rückbau der Konstante statt
einer Registerzeile. Das ist der bewusst gewählte Preis der Vereinheitlichung;
er fällt erst an, wenn dieser Fall eintritt.

Drittwahrscheinlich verlässt sich eine spätere Änderung darauf, dass die
beiden Runtime-Regressionen den realen Adapter abdecken. Sie tun es nicht, und
ein geänderter Pflichtflagsatz bliebe dort grün.

```text
REVIEWER: claude
REVIEW_EVIDENCE: selbst erhobener Worktree-, Branch-, HEAD-, Merge-Base-, Pfadsatz- und Diffstand mit eigenstaendig nachgerechnetem Fingerprint 372f2bb6 und Stabilitaetskontrolle zu Beginn und unmittelbar vor der Schreiboperation ohne Drift; neun Loader-Mutationen gegen Temporaerkopien einschliesslich same-minor-forward, unbekannter Politik, Gross-/Kleinschreibung, Whitespace, leerem und fehlendem Feld sowie eines neuen Providers ohne Versionsgrammatik, jeweils fail-closed; 22 Versionsfaelle ueber beide Provider gegen compatible_cli_version und assert_provider_capabilities mit uebereinstimmendem Gate-Verhalten, darunter alle geforderten positiven und negativen Faelle sowie sechs unparsbare Formate; realer Runtimepfad ueber verify_agent_capabilities mit den echten Adaptern aus build_agent_registry und der echten Hilfeausgabe der installierten codex-cli 0.150.1 und 2.1.250 (Claude Code), inklusive vier Gegenbeispielen mit fehlendem Pflichtflag bei ausreichend neuer Version; Nachweis unveraenderter Projektion durch doppelte Berechnung aller zehn Provider-Schema-Projektionen mit der master- und der Hotfix-Fassung des Moduls mit byteidentischem Ergebnis; Kontrolle, dass VERSION_POLICIES rueckstandsfrei entfernt ist und die Negativabdeckung der Testmatrix nicht geschrumpft ist; eigener fokussierter Lauf 53 passed in 0.25s, zweifach stabil; die vollstaendige Suite wurde nicht ausgefuehrt und weder eigene noch uebernommene Evidenz ist eine Orchestrator-Attestierung | groesste verbleibende Gefahr: die an codex-cli 0.147.0 erhobene Probe-Evidenz wird fuer jede spaetere 0.x weiterbehauptet, ohne dass ein Mechanismus auf den wachsenden Abstand zwischen Baseline und installierter Version aufmerksam macht; nachgeordnet pruefen beide Runtime-Regressionen Fake-Adapter statt der realen Adapter aus build_agent_registry | realistische Bruchbedingung: eine kuenftige Codex-0.x behaelt alle sechs Pflichtflags und das Transportprofil, aendert aber die Auslegung eines Schemakonstrukts, das die Featuretabelle noch als positiv geprueft fuehrt; Versions-, Flag- und Profilpruefung laufen durch, und der Fehlschlag zeigt sich erst im ersten realen Providerlauf als Strukturfehler mit Gate, dessen Diagnose auf den Transport statt auf den veralteten Faehigkeitsnachweis zeigt
PRE_MORTEM: In drei Monaten scheitert der Hotfix am wahrscheinlichsten daran, dass niemand die Probe-Baseline nachgezogen hat: Codex steht bei 0.16x oder 0.17x, das Register behauptet weiterhin 0.147.0, und der erste Dialektunterschied erscheint als sporadischer Structured-Output-Fehler mit Resume-Gate, dessen Ursachensuche beim Transport beginnt, weil nichts auf den gewachsenen Versionsabstand hinweist; der Hotfix hat gerade die Reibung beseitigt, die diesen Abstand frueher sichtbar machte. Zweitwahrscheinlich wird ein dritter Provider registriert, der in Major 0 tatsaechlich brechend arbeitet und fuer den same-major-forward zu weit waere; eine engere Schranke erforderte dann einen Rueckbau der providerweiten Konstante statt einer Registerzeile. Drittwahrscheinlich verlaesst sich eine spaetere Aenderung darauf, dass die beiden Runtime-Regressionen den realen Adapter abdecken, was sie nicht tun, sodass ein geaenderter Pflichtflagsatz dort gruen bliebe.
FINAL_APPROVAL: YES
STATUS: DONE
```
