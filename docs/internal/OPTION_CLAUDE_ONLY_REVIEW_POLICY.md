# Option: Laufgebundene Claude-only-Reviewpolicy

**Status:** analysierte Implementierungsoption, noch nicht umgesetzt

**Zweck:** Antigravity vorübergehend aus Planung, Implementierung und
Gesamtreview herausnehmen, ohne seine Integration, historischen Records oder
die Möglichkeit einer späteren Reaktivierung zu entfernen.

**Wichtig:** Dieses Dokument beschreibt eine mögliche Folgeentwicklung. Es
autorisiert für sich allein weder Codeänderungen noch die Umstellung bereits
laufender Workflows.

## 1. Anlass

Codex und Claude kommunizieren in neuen Bootstrap-Läufen bereits über native,
schema- und domänenvalidierte JSON-Verträge. Antigravity verwendet weiterhin
den textuellen Adapterpfad und kann dadurch einen ansonsten erfolgreichen Lauf
nach der Claude-Freigabe technisch anhalten.

Bis Antigravity ebenfalls einen hinreichend stabilen nativen Vertrag besitzt,
soll eine reversible Betriebsart verfügbar sein, in der:

- nach einem positiven Claude-Planreview kein Antigravity-Planreview startet;
- nach einem positiven Claude-Slicereview kein Antigravity-Slicereview startet;
- nach einem positiven Claude-Gesamtreview kein Antigravity-Finalreview startet;
- Plan-, Implementierungs- und Abschlussdokumente ohne Antigravity-Zustimmung
  ordnungsgemäß weitertakten dürfen;
- keine Antigravity-Freigabe angenommen, simuliert oder als Record erfunden
  wird.

## 2. Zielzustand

Neue Läufe können an eine unveränderliche Reviewpolicy gebunden werden:

- `claude-only-v1`: Codex erstellt beziehungsweise korrigiert; Claude prüft und
  ist der einzige erforderliche Reviewer.
- `claude-antigravity-v1`: Der bisherige Ablauf bleibt erhalten; Antigravity
  prüft nach einer positiven Claude-Entscheidung denselben Fingerprint.

Für die vorübergehende Stabilisierungsphase soll `claude-only-v1` der Standard
neuer Läufe sein. Die spätere Rückkehr zu Antigravity erfolgt über eine
ausdrückliche Policyentscheidung und nicht durch erneutes Einbauen zuvor
gelöschter Logik.

### 2.1 Ablauf unter `claude-only-v1`

| Phase | Ablauf nach positiver Claude-Entscheidung |
|---|---|
| Planung | Plan committen und Implementierungshandoff erzeugen |
| Implementierung | Slice committen und nächste Work Unit starten |
| Korrektur | Korrekturslice committen oder Konvergenz fortsetzen |
| Gesamtreview | Final-Work-Unit und Workflow abschließen |

Claude bleibt dabei an alle bestehenden Regeln zu Fingerprint,
Validierungsattestierung, Pre-Mortem, Findings und Finalfreigabe gebunden.
Claude genehmigt keine eigene Implementierung; Codex genehmigt seine eigene
Arbeit ebenfalls nicht.

## 3. Technische Leitentscheidung

Die Reviewpolicy muss Bestandteil der persistierten Protokollbindung sein. Ein
bloßer, bei jedem Aufruf neu auswertbarer CLI-Schalter reicht nicht aus, weil
ein Resume sonst die Revieweranforderungen eines bereits begonnenen Laufs
ändern könnte.

Empfohlene Regeln:

1. Ein neuer Lauf persistiert seine Policy zusammen mit der vorhandenen
   `ProtocolBinding`.
2. Die Policy ist während des gesamten Laufs unveränderlich.
3. Ein expliziter Resume-Schalter muss mit der gespeicherten Policy
   übereinstimmen.
4. Historische Zustände ohne Policyfeld behalten deterministisch das bisherige
   Verhalten `claude-antigravity-v1`.
5. Ein bestehender Dual-Review-Lauf wird nicht still auf Claude-only migriert.
6. Bestehende `A-*`-Findings werden niemals durch einen Policywechsel
   übergangen.

Als Bedienoberfläche bietet sich ein explizites Optionspaar an:

```text
--antigravity-reviews
--no-antigravity-reviews
```

Beim Resume ohne ausdrückliche Angabe gilt ausschließlich die persistierte
Policy.

## 4. Erforderliche Anpassungsbereiche

### 4.1 Workflowübergänge

Die Übergänge nach positiven Claude-Reviews werden policyabhängig. Unter
Claude-only führen sie direkt zum Planhandoff, Slice-Commit oder Abschluss.
Unter der Dual-Policy führen sie unverändert zu den vorhandenen
`ANTIGRAVITY_*_REVIEW`-Schritten.

Dies gilt ebenso für Recovery-Pfade, die ein bereits record-ahead
persistiertes Claude-Ergebnis wiederherstellen. Ein wiederhergestelltes
Ergebnis darf nicht aufgrund einer abweichenden aktuellen CLI-Konfiguration in
eine andere Reviewerkette gelangen.

### 4.2 Commit-Autorisierung

Die Commit-Autorisierung erhält die Menge der laut Policy erforderlichen
Reviewer:

- Claude-only: `{claude}`
- Dual: `{claude, antigravity}`

Jede erforderliche Freigabe muss positiv, aktuell und an denselben Fingerprint
und dieselbe Attestierung gebunden sein. Fehlende Antigravity-Zustimmung ist im
Claude-only-Modus kein Fehler, erzeugt aber auch keinen Antigravity-Reviewrecord.

Der strukturierte Commit-Binding-Record enthält nur die tatsächlich
erforderlichen und vorhandenen Approval-IDs.

### 4.3 Audit und Markdownprojektion

Die menschenlesbare Projektion weist die gebundene Policy und den Grund für
die Abwesenheit eines Antigravity-Reviews ausdrücklich aus:

```text
Reviewer-Policy: claude-only-v1
Claude-Freigabe: YES
Antigravity-Freigabe: NOT_REQUIRED_BY_POLICY
```

`NOT_REQUIRED_BY_POLICY` ist weder eine Freigabe noch ein fehlender Record. Es
ist die nachvollziehbare Darstellung der für diesen Lauf gebundenen
Anforderung.

### 4.4 Watch und Dokumentweitertaktung

Watch-, Plan-only- und Implementierungsläufe müssen dieselbe gespeicherte
Policy verwenden. Ein positiver Claude-Planreview darf unter Claude-only den
Plan committen und das `-implement.md` erzeugen. Positive Slice- und
Finalreviews dürfen entsprechend committen beziehungsweise abschließen, ohne
auf einen nicht vorgesehenen Antigravity-Aufruf zu warten.

## 5. Was erhalten bleibt

Die Umsetzung entfernt ausdrücklich nicht:

- Antigravity-Adapter, Providertelemetrie und Runtimeklassifikation;
- Antigravity-Prompts und den bestehenden Textvertrag;
- `ANTIGRAVITY_PLAN_REVIEW`, `ANTIGRAVITY_SLICE_REVIEW` und
  `ANTIGRAVITY_FINAL_REVIEW`;
- Antigravity-Findingeigentum und `A-*`-Replayregeln;
- Preflight-, Recovery-, Migrations- und Auditlogik historischer Agy-Läufe;
- bestehende Dual-Review-Regressionen.

Diese Bestandteile bleiben für historische Läufe, explizite Dual-Policy-Läufe
und die spätere native Antigravity-Reaktivierung verfügbar.

## 6. Sicherheits- und Kompatibilitätsgrenzen

- Keine künstliche Antigravity-Zustimmung und kein leerer Ersatzreview.
- Kein Wechsel der Reviewerpolicy während eines Laufs.
- Keine Claude-only-Fortsetzung eines historischen Laufs mit offenen
  Antigravity-Findings.
- Keine Abschwächung von Claude-Fingerprint-, Attestierungs-, Finding- oder
  Pre-Mortem-Prüfungen.
- Keine automatische Rückkehr zur Dual-Policy, nur weil ein Antigravity-Binary
  verfügbar ist.
- Unbekannte oder widersprüchliche Policywerte halten fail-closed an.

## 7. Erforderliche Nachweise vor Aktivierung

Die Implementierung muss mindestens folgende Regressionen enthalten:

1. Claude-Planfreigabe erzeugt ohne Agy-Aufruf den Implementierungshandoff.
2. Claude-Slicefreigabe führt ohne Agy-Aufruf zum Commit.
3. Claude-Korrekturfreigabe führt ohne Agy-Aufruf zum Commit.
4. Claude-Finalfreigabe schließt den Lauf ohne Agy-Aufruf ab.
5. Es entstehen weder Agy-Providerattempts noch Agy-Reviewrecords.
6. Audit und Markdown zeigen `NOT_REQUIRED_BY_POLICY`.
7. Negative, unvollständige oder veraltete Claude-Freigaben bleiben
   fail-closed.
8. Die Dual-Policy ruft Antigravity weiterhin genau einmal nach Claude auf.
9. Historische States ohne Policyfeld behalten das Dual-Verhalten.
10. Ein Resume mit widersprüchlicher Policy wird abgewiesen.
11. Record-Replay und Workflowcompletion funktionieren mit genau einer
    erforderlichen Approval-ID.
12. Ein Plan-only-Watch-Lauf erzeugt sein Implementierungshandoff vollständig
    ohne Antigravity.

Nach Änderungen an Workflow, State, CLI, Watch oder Projektion läuft die
vollständige Repositorysuite `python3 -m pytest tests/ -v`.

## 8. Empfohlener Umsetzungsschnitt

### Slice 1 – Persistierte Policy und Entscheidungssemantik

- Reviewpolicy in State und Resume unveränderlich binden;
- Workflowübergänge policyabhängig machen;
- Commit-Autorisierung auf erforderliche Reviewer umstellen;
- Audit und Record-Bindings korrekt projizieren;
- historische Dual-Läufe unverändert fortsetzbar halten.

### Slice 2 – Bedienung und Ende-zu-Ende-Nachweis

- CLI- und Watch-Optionen ergänzen;
- Dokumentweitertaktung unter Claude-only absichern;
- menschenlesbare Ausgaben und Betriebsdokumentation synchronisieren;
- Claude-only- und Dual-Policy-End-to-End-Regressionen ergänzen.

## 9. Reaktivierung von Antigravity

Antigravity kann wieder zum Standard werden, sobald sein nativer
Reviewtransport, die request-spezifische Vertragsgeschlossenheit und mehrere
repräsentative Plan-, Slice-, Korrektur- und Finalreviews stabil nachgewiesen
sind. Dann wird nicht die gespeicherte Policy bestehender Läufe geändert,
sondern der Default ausschließlich für neu gestartete Läufe auf
`claude-antigravity-v1` zurückgestellt.

## 10. Zentrales Akzeptanzkriterium

> Ein neuer, an `claude-only-v1` gebundener Lauf schreitet von der Planung bis
> zum Abschluss vollständig fort, ohne Antigravity aufzurufen, eine
> Antigravity-Zustimmung zu verlangen oder eine solche Zustimmung
> vorzutäuschen. Ein expliziter oder historischer Dual-Review-Lauf behält
> gleichzeitig den bisherigen Antigravity-Ablauf unverändert bei.
