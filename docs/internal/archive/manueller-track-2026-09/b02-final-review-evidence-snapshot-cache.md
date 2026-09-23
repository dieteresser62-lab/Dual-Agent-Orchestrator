# B02 – Final-Review-Evidenz je Snapshot einmal erheben

## Aufruf- und Feldinventur

`WorkflowChanges` stellt vier Werte bereit: `fingerprint` bindet Attestierung,
Provider-Request und Records; `paths` steuert Scope- und Preflight-Prüfungen;
`gate_paths` ist die kanonisch sortierte Menge der Pfade für Benutzergates;
`full_diff` ist die kanonische Agentenevidenz. `start_commit` bleibt die
explizite Git-Grenze.

| Aufruf | Phase | Benötigte Felder |
| --- | --- | --- |
| `workflow.py:1271` | Stop-Gate in ein exaktes Scopegate überführen | `fingerprint`, `paths`, `gate_paths` |
| `workflow.py:1347` | Korrekturrequest | `fingerprint`, `full_diff` |
| `workflow.py:1605` | Plan-Preflight und Attestierung | `fingerprint`, `paths`, `gate_paths`, `full_diff` |
| `workflow.py:1707` | Codex-Abschlussbericht | alle Felder; insbesondere vollständiger Branchdiff |
| `workflow.py:1886` | Claude-Review einschließlich Final Review | alle Felder; Request, Attestierung und Scopeprüfung teilen dieses Objekt |
| `workflow.py:2578` | Providerfehler-/Resume-Bindung | `fingerprint` |
| `workflow.py:2604` | Quota-Resume-Revalidierung | `fingerprint`, `paths`, `gate_paths` |
| `workflow.py:2808` | PLAN_ONLY-Grenze | `fingerprint` |
| `workflow.py:2816` | Commit-Preflight | `fingerprint`, `paths`, `gate_paths` |
| `workflow.py:3551` | fingerprintgebundene Scopefreigabe | `fingerprint` |
| `orchestrator.py:1688` | Provider-Input-Messung und Final-Review-Preflight | derselbe `fingerprint` und dieselben `paths`; innerhalb der Methode nur eine Abfrage |
| `orchestrator.py:3192` | technische Record-/Side-Effect-Bindung | `fingerprint` |

Nur Aufrufe in einem `FINAL_REVIEW`-Work-Unit verwenden den neuen Cache. Plan-,
Slice- und Korrekturpfade behalten ihr bisheriges Verhalten.

## Snapshotbindung

`FinalReviewEvidenceSnapshot` ist ein unveränderlicher, typisierter
Ableitungswert. Er bindet:

- den angeforderten Start-Commit, den kanonischen Merge-Base und das aktuelle
  `HEAD`;
- einen eigenständigen Indexfingerprint, der auch eine nur im Index sichtbare
  Änderung erfasst;
- die kanonischen Change- und Fingerprinteinträge für getrackte und
  nicht-ignorierte ungetrackte Pfade;
- Repositoryfingerprint, Pfade, Gatepfade und den vollständigen kanonischen
  Diff;
- semantische Markdownpfade, Auditpfad und kontrollierende Ausschlüsse;
- die kompakte Auditzusammenfassung und den daraus deterministisch erneut
  ableitbaren Agentendiff.

Die gemessene Laufzeit gehört nur zum kurzlebigen Prozessobjekt und zur
Betriebsdiagnostik. Sie wird nicht persistiert, damit zwei Erhebungen desselben
Repositoryzustands byteidentische Cachewerte und denselben wiederaufnehmbaren
`file_write`-Intent erzeugen.

Der persistierte Wert liegt ausschließlich unter `.orchestrator/cache/`. Er
ist keine Record- oder State-Autorität. Seine exakten Dateibytes werden beim
ersten Schreiben über den vorhandenen `file_write`-Side-Effect-Vertrag an die
append-only Recordkette gebunden; ohne diesen unabhängigen Digestnachweis ist
eine prozessübergreifende Wiederverwendung verboten. Damit wird weder ein neuer
Recordtyp noch eine Schema- oder Protokollversion eingeführt. Vor jeder
Wiederverwendung erhebt
`probe_repository_snapshot()` den aktuellen inhaltsadressierten Stand ohne
Diffkonstruktion neu. Der Cache wird nur akzeptiert, wenn HEAD, Index,
getrackte und ungetrackte Inhalte, Pfadmenge, Repositoryfingerprint und alle
kontrollierenden Grenzen exakt übereinstimmen. Die Cachehülle bindet zusätzlich
die kanonischen Snapshotbytes und ihr Dateidigest muss dem autoritativen
Side-Effect-Ergebnis entsprechen. Fehlt die Datei oder Recordbindung, ist sie
unlesbar oder wurde sie verändert, wird aus dem Repository neu erhoben;
niemals wird das Repository oder ein beschädigter Cache aus Cachewerten
repariert. Ein nicht zusammengeführter Index wird dabei bewusst fail-closed
abgewiesen, weil kein eindeutiger Stage-0-Inhalt gebunden werden kann.

## Sicherheitsphasen

Attestierung und Provider-Requests bleiben an `repository_fingerprint`
gebunden. Der Final-Review-Preflight liest weiterhin die aktuelle autoritative
Recordkette und prüft sie gegen exakt `snapshot.paths`; der Cache ersetzt diese
Prüfung nicht. Gateentscheidungen werden weiterhin live aus dem aktuellen
Workflowzustand ausgewertet und gelten nur für ihren exakten Fingerprint und
ihre exakte Pfadmenge. Eine Gate- oder Attestierungsänderung verändert deshalb
nicht stillschweigend die Cacheautorität: Sie wird von ihren bisherigen
Sicherheitsprüfungen gegen denselben erneut validierten Repositorysnapshot
bewertet.

Eine reine Neuprojektion vollständig verwalteter Auditsektionen wird bereits
im Repositoryprobe semantisch kanonisiert. Sie verändert weder Fingerprint noch
Evidenz. Echte semantische Änderungen, Indexänderungen, ein neues HEAD oder
neue sichtbare Pfade invalidieren dagegen die Wiederverwendung.

## Betriebsdiagnostik

Eine neu gebildete und auditkompaktierte Evidenz erzeugt genau eine
INFO-Meldung `Final review evidence compacted` mit Original- und Evidenzgröße,
Fingerprint, Laufzeit und lokalem Erhebungszähler. Jede geprüfte
Wiederverwendung erscheint nur auf DEBUG-Niveau mit Wiederverwendungs- und
Erhebungszähler.
