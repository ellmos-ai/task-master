# TASKPLAN Cross-System-Transit — Vertrag v1alpha1

**Status:** Entwurf; keine Implementierungsfreigabe  
**Ticket:** T-20260831-340911492  
**Offene Entscheidung:** E02  
**Stand des Live-Audits:** 2026-08-31

Dieser Vertrag formalisiert die optionale Phase B. Phase A, einschließlich des
hostlokalen Review-/Siegelpools, bleibt ohne Transit vollständig funktionsfähig.
Dieses Dokument ändert weder Datenbanken noch Laufzeitkonfigurationen und
autorisiert keine Implementierung oder Aktivierung.

## 1. Geprüfter Ausgangsstand

| Modul | Geprüfter Upstream | Vorhandener Vertrag | Für Phase B fehlend |
|---|---|---|---|
| `task-master` | `8f81f1e9a89c9cc2611912d0a7e61d9e9b36a77a` | lokale Integer-ID, `origin_host`, `local/central`-Arbeitsscope, Status, dauerhafte Zuweisung, deterministischer Selector | globale Task-ID, Verteilungsscope, Claim-Lease, fachliche Tombstones, Task-Transit-Migration |
| `sqlite-transit-sync` | `ba99876f8b5a76a6f63a1e3791b4638235196aee` | geschlossene und geprüfte Snapshots, Manifest/SHA-256, optionales HMAC, Pull-State, anwendungsspezifische `MergePolicy`, Tombstone-Referenz | TASKPLAN-Fachpolicy; sie darf im Carrier nicht erraten werden |
| `system-gap-master` | `714b477b6e443a929aef6588dea0e1706404bc00` | R9-Zone `db-transit/<namespace>`, Host-/Peer- und Pfadverträge, fail-closed Transportgrenzen | Task-Lifecycle, Task-Merge und Claim-Entscheidungen; diese dürfen dort nicht entstehen |

Wichtiger Bestandsbefund: Das vorhandene Feld `scope` bedeutet derzeit
`local|central` und steuert die autonome Bearbeitbarkeit. Es darf nicht mit
`local:<system-id>|shared:<scope-id>` überladen werden. Phase B führt deshalb
ein getrenntes Feld `transit_scope` ein. Der bestehende `scope=central`-Guard
bleibt unabhängig davon wirksam.

## 2. E02 — Entscheidungsbriefing

**Entscheidungs-ID:** E02  
**Entscheider:** Nutzer  
**Status:** OFFEN  
**Implementierungsgate:** GESCHLOSSEN, bis ausdrücklich `E02=A` oder `E02=B`
gewählt wurde.

### Option A — Shared-only-Projektion

Nur Zeilen mit gültigem `transit_scope=shared:<scope-id>` und ihre zwingend
benötigten Ereignis-, Claim- und Migrationsdatensätze werden in eine gesonderte
Snapshot-Datenbank projiziert. Legacy- und `local:*`-Zeilen verlassen den Host
nie.

Folgen:

- kleinste Datenschutz- und Offenlegungsfläche;
- ein Raw-Snapshot kann mechanisch auf das Fehlen lokaler Tasks geprüft werden;
- Migrationen `local -> shared` sind ausdrücklich und auditierbar;
- Projektion und Rückimport benötigen zusätzliche Adapterlogik;
- lokale und geteilte Task-Bestände bleiben physisch klar getrennt.

### Option B — Vollständige relevante Tabellen

Die relevanten Tabellen werden vollständig synchronisiert. Jede Zeile benötigt
eine gültige stabile Herkunft und einen Verteilungsscope; der Selector darf nur
`local:<eigene-system-id>` und abonnierte `shared:*`-Scopes materialisieren.

Folgen:

- weniger Projektionslogik und vollständigere forensische Replik;
- lokale Titel, Beschreibungen, Tags und Projektbezüge verlassen den Host;
- ein einziger Filter- oder Migrationsfehler kann fremde lokale Arbeit sichtbar
  oder claimbar machen;
- Altzeilen ohne belastbare Herkunft müssen vor dem ersten Export quarantänisiert
  oder vollständig migriert werden;
- Speicher-, Retention-, Auskunfts- und Löschfläche sind deutlich größer.

### Vergleich

| Kriterium | A: Shared-only | B: vollständige Tabellen |
|---|---|---|
| Datenschutz | lokale Daten bleiben nachweisbar lokal | lokale Daten werden trotz Unsichtbarkeit repliziert |
| Selector-Risiko | geringere Eingangsdatenmenge | Filter ist alleinige Sicherheitsgrenze |
| Altbestand | Lazy-Migration möglich | vollständige Provenienz-Migration vor Export nötig |
| Speicher/Retention | proportional zu Shared-Arbeit | proportional zum gesamten relevanten Bestand aller Hosts |
| Forensik | Shared-Historie vollständig | umfassender, aber datenschutzintensiver |
| Implementierungsaufwand | Projektionsadapter | Provenienz- und Quarantänemigration plus harte Filter |
| Blast Radius bei Policyfehler | Shared-Scopes | alle synchronisierten Tasks |

### Empfehlung des TASKWRITER

**Empfohlen ist E02=A für v1.** Die Variante erfüllt das Nutzerziel, ohne lokale
Arbeit als bloß „unsichtbare“ Fremddaten zu replizieren. Sie passt zum
Redaktions-/Projektionsmodell des Carriers und macht die zentrale
Datenschutzinvariante direkt am Snapshot prüfbar. B bleibt eine spätere,
eigenständig zu entscheidende Erweiterung, falls ein belegter Betriebsbedarf
die größere Offenlegungsfläche rechtfertigt.

Bei A wird bewusst in Kauf genommen, dass die Projektions- und
Migrationslogik umfangreicher ist. Diese Mehrarbeit ist eine kontrollierbare
Codekomplexität; die Datenoffenlegung von B wäre dagegen nach einem Export
nicht zuverlässig rückholbar.

## 3. Normative Begriffe und Identitäten

Die Schlüsselwörter MUSS, DARF NICHT, SOLL und KANN sind normativ.

### 3.1 Systemidentität

- `system_id` ist ein stabiler, kanonischer Bezeichner aus einer ausdrücklich
  konfigurierten system-gap-Registry. `socket.gethostname()` beziehungsweise
  das bestehende `origin_host` ist nur Diagnose, niemals Identitätsanker.
- Format: `^[a-z0-9][a-z0-9-]{0,62}$`.
- Eine Umbenennung erzeugt ohne signierte Alias-/Migrationsregel eine neue
  Identität. Unbekannte oder doppelte IDs blockieren Transit und Auswahl.

### 3.2 Verteilungsscope

- `transit_scope = local:<system-id>`: nur auf diesem System sichtbar,
  exportierbar und claimbar ist **falsch**; die Zeile wird nie exportiert.
- `transit_scope = shared:<scope-id>`: nur bei gültiger Scope-Registry und
  lokaler Subskription sichtbar, exportierbar und claimbar.
- `scope` behält die bestehende Bedeutung `local|central`. Insbesondere bleibt
  `scope=central` nicht autonom, auch wenn `transit_scope` geteilt ist.
- Altzeilen mit leerem `transit_scope` werden ausschließlich zur Laufzeit als
  `local:<eigene-system-id>` behandelt. Sie werden nicht automatisch
  fortgeschrieben oder exportiert.

Eine Shared-Scope-Registry MUSS enthalten:

```text
scope_id, project_id, canonical_root_proof, participant_system_ids,
claim_authority_system_id, contract_version, merge_policy_version,
valid_from, expires_at_or_never
```

Pfadgleichheit, Ordnername oder Cloud-Anbieterkennung ist kein
`canonical_root_proof`. Zulässig sind nur Registry, Manifest, Pointer oder eine
dokumentierte Writer-Policy mit Readback.

### 3.3 Task-Identität

Der lokale Integer-Primärschlüssel bleibt für bestehende Consumer erhalten.
Shared Tasks erhalten zusätzlich:

- `task_uid`: beim ersten gültigen Shared-Erstellen generierte UUIDv7; danach
  unveränderlich;
- `logical_task_id`: `sha256:` plus SHA-256 über kanonisches UTF-8-JSON aus
  `contract_version`, `shared_scope_id`, `project_id`, `source_kind`,
  `source_locator`, fachlichem Fingerprint und `source_revision`;
- `origin_system_id`: System, das die logische Aufgabe zuerst materialisierte;
- `task_authority_system_id`: System, das autoritative Lifecycle-Ereignisse
  ausstellen darf;
- `content_revision`: Digest des bei Erstellung belegten Inhalts.

Der Fingerprint wird bei Erstellung gespeichert und nicht aus später
geändertem Text neu berechnet. Änderungen sind neue Ereignisse, keine neue
Identität. Treffen gleiche `logical_task_id` mit verschiedenen unveränderlichen
Identitätsfeldern ein, entsteht `identity_collision`; die Aufgabe bleibt
nicht selektierbar, bis ein autoritatives Auflösungsereignis vorliegt.

## 4. Daten- und Ereignisvertrag

Der Implementierungsentwurf SOLL additiv sein. Bestehende Spalten werden nicht
umgedeutet. Der v1-Vertrag benötigt mindestens:

### 4.1 Additive Task-Felder

```text
task_uid, logical_task_id, transit_scope, origin_system_id,
task_authority_system_id, project_id, content_revision,
transit_contract_version
```

Für eine Shared-Zeile sind alle Felder `NOT NULL` beziehungsweise durch einen
CHECK- oder Anwendungsgate zwingend. Teilmigrierte Zeilen bleiben lokal und
nicht exportierbar.

### 4.2 Append-only-Ereignisse

`taskplan_transit_events` enthält mindestens:

```text
event_id, task_uid, event_kind, actor_system_id, authority_sequence,
parent_event_ids, payload_json, payload_sha256, created_hlc,
contract_version
```

`event_id` ist ein Digest über die kanonische Ereignisnutzlast. Import ist
`INSERT OR IGNORE` nur dann, wenn ein vorhandener Datensatz bytegleich ist;
eine Digestkollision blockiert. `authority_sequence` ist je Task streng
monoton und wird ausschließlich durch die Task-Autorität vergeben.

Zulässige Ereignisklassen:

```text
task_created, categorized, deferred, claim_requested, claim_granted,
claim_released, completed, cancelled, reopened, tombstoned,
scope_migration_started, scope_migration_committed,
scope_migration_rolled_back, conflict_resolved
```

### 4.3 Materialisierungsregeln

- `active` wird für Shared Tasks nur aus einer aktuell gültigen autoritativen
  Claim-Lease abgeleitet; ein fremdes `assigned_to` allein genügt nicht.
- `completed` und `cancelled` sind terminal und machen eine Aufgabe auf allen
  Knoten nicht selektierbar.
- Gleichzeitige widersprüchliche Terminalereignisse ergeben
  `terminal_conflict`, ebenfalls nicht selektierbar. Ein
  `conflict_resolved`-Ereignis der Autorität MUSS beide Eltern referenzieren.
- `reopened` ist nur gültig, wenn es das terminale Ereignis referenziert und
  eine höhere autoritative Sequenz trägt.
- `tombstoned` gewinnt gegen alle nicht ausdrücklich darauf aufbauenden
  Ereignisse. Physisches Löschen ist kein Transit-Lifecycle.
- Rekategorisierung gewinnt nicht durch Wandzeit. Nur die höchste lückenlos
  verifizierte autoritative Sequenz wird materialisiert.
- Belegtes Deferment wird geteilt; abgelaufene lokale Präsentationsleasen des
  Phase-A-Reviewpools werden nicht transportiert.

## 5. Claims und Leases

Ein asynchroner Snapshot-Transport kann ohne Koordinator keine harte
gleichzeitige Exklusivität versprechen. Deshalb ist der in der Scope-Registry
benannte `claim_authority_system_id` der Fencing-Owner.

1. Ein Teilnehmer schreibt `claim_requested` mit zufälliger Request-ID.
2. Die Autorität sortiert gültige konkurrierende Requests deterministisch nach
   Empfangssequenz und anschließend `request_id`.
3. Sie stellt höchstens eine `claim_granted`-Lease mit monotonem
   `fencing_token`, `not_before`, `not_after`, Claimant-System und Agent aus.
4. Ein Claimant DARF erst nach Rückimport und Readback seines Grant-Ereignisses
   arbeiten. Ein bloßer Request ist kein Claim.
5. Jeder schreibende Abschluss MUSS den aktuellen `fencing_token` tragen.
6. Abgelaufene oder freigegebene Tokens werden nie wieder gültig.

Wandzeit dient nur als Lease-Grenze, nicht als Merge-Reihenfolge. Jeder Knoten
konfiguriert `max_clock_skew_seconds`. Ist die lokale Uhr unsicher oder liegt
sie innerhalb des Unsicherheitsfensters um `not_after`, bleibt die Aufgabe
nicht claimbar, bis die Autorität eine neue Lease ausstellt. Ist die Autorität
nicht erreichbar, pausiert nur die betroffene Shared-Aufgabe; lokale Phase-A-
Arbeit läuft weiter.

## 6. Tombstones und Retention

- Löschen erzeugt ein autoritatives `tombstoned`-Ereignis; die Task-Zeile wird
  nicht direkt entfernt.
- Tombstones werden mindestens für `max_offline_interval + max_clock_skew +
  safety_margin` und bis zur Empfangsbestätigung aller aktuell abonnierten
  Systeme gehalten.
- Ausgetretene Systeme werden durch eine versionierte Scope-Registry-Änderung
  aus der Ack-Menge entfernt, nicht durch Timeout-Raten.
- Die physische Bereinigung ist ein eigener, standardmäßig trockener,
  eigentumsgebundener Retention-Schritt. Sie darf nicht Teil von Push/Pull sein.
- Der generische `TombstoneMergePolicy` des Carriers ist Referenzmechanik. Die
  fachliche TASKPLAN-Auflösung bleibt in der TASKPLAN-spezifischen MergePolicy.

## 7. Scope-Migrationen

### 7.1 `local -> shared`

1. Scope- und Root-Proof sowie Autorität prüfen.
2. Identitätsfelder und `logical_task_id` deterministisch erzeugen.
3. In einer lokalen Transaktion Alias vom Integer-ID auf `task_uid`,
   `scope_migration_started` und Shared-Materialisierung schreiben.
4. Erst nach erfolgreicher Projektion, Snapshot-Prüfung und lokalem Readback
   `scope_migration_committed` schreiben.
5. Bis zum Commit bleibt genau die lokale Darstellung selektierbar. Danach
   genau die Shared-Darstellung. Ein Retry verwendet dieselbe Migrations-ID.

### 7.2 `shared -> local`

Ein Shared Task darf nicht unilateral „zurückverschoben“ werden. Die Autorität
schließt oder tombstoniert zuerst die Shared-Identität. Danach kann jedes
berechtigte System eine neue lokale Aufgabe mit neuer `task_uid` und
`migrated_from`-Verweis anlegen. So bleiben Historie und Deduplizierung
eindeutig.

### 7.3 Policy-/Schema-Migration

Unbekannte Major-Versionen werden nicht importiert. Minor-Erweiterungen sind
nur bei expliziter Capability zulässig. Migrationen laufen über neue Tabellen
oder additive Spalten, sind wiederholbar und prüfen vor und nach jedem Schritt
`PRAGMA quick_check`. Es gibt keinen automatisch geratenen Altbestands-Backfill
für `origin_system_id`.

## 8. Selector-Vertrag

Bei deaktiviertem oder fehlendem Transit gilt unverändert Phase A.

Bei aktiviertem Transit ist ein Task nur selektierbar, wenn alle Gates gelten:

```text
legacy/leer -> ausschließlich local:<own-system-id>
local:X     -> X == own-system-id
shared:S    -> S ist gültig, abonniert, Root-Proof gültig,
               Contract-/Merge-Version unterstützt
scope       -> bestehender local/central-Guard bleibt erfüllt
identity    -> vollständig und kollisionsfrei
lifecycle   -> nicht terminal, nicht tombstoned, kein ungelöster Konflikt
claim       -> keine fremde gültige Lease; eigener Grant mit Readback vorhanden
```

`local:<fremd>`, unbekannte IDs/Scopes, abgelaufene Registry, unvollständige
Migration, Policy-Drift oder unsichere Uhr werden mit Diagnose übersprungen.
Sie dürfen weder als `NO_WORK`-Beweis verschwinden noch die Auswahl lokaler
Arbeit blockieren.

## 9. Projektion, Carrier und Transportzone

### 9.1 E02=A

TASKPLAN erstellt eine neue temporäre SQLite-Projektion aus einer festen
Tabellen-/Spalten-Allowlist. Ein positiver `transit_scope=shared:*`-Nachweis ist
für jede Task-Zeile Pflicht. Der Adapter prüft die fertige Projektion zusätzlich
negativ auf `local:*`, leere Scopes, Secrets sowie nicht freigegebene Roots.
Erst danach übernimmt `sqlite-transit-sync` Snapshot, Manifest,
Credential-Scan, Authentifizierung und Transport.

### 9.2 E02=B

Ein Vollsnapshot ist erst zulässig, wenn jede exportierte Zeile vollständige
Provenienz besitzt. Alt- oder Fehlerzeilen werden nicht still lokalisiert,
sondern blockieren den Export. Selector-Filter ersetzen keine
Export-Datenschutzprüfung.

### 9.3 Gemeinsame Carrier-Gates

- Namespace: `taskplan-shared-v1` in der durch system-gap-master aufgelösten
  R9-Zone `db-transit/taskplan-shared-v1`.
- Live-DB, `-wal`, `-shm`, Journal oder ungeprüfte Nebenfiles gelangen nie in
  den Yard.
- Node-State, Keys, Scope-Registry-Policy und entschlüsselte Staging-DB liegen
  hostlokal außerhalb des Yards.
- Für einen semi-vertrauenswürdigen Yard ist der optionale Authenticator
  verpflichtend; SHA-256 allein beweist keinen Absender.
- Push/Pull sind explizit konfiguriert, idempotent und ohne Autostart.
- Ein Transport-Receipt ist kein Task-Abschluss und kein Claim-Grant.

## 10. Ownership und Adaptergrenzen

| Owner | Verantwortet | Verantwortet ausdrücklich nicht |
|---|---|---|
| `task-master` | Identität, `transit_scope`, Shared-Registry-Modell, Task-Ereignisse, Materialisierung, Deduplizierung, Claim-Autorität/Fencing, Status/Tombstone, Migration, Selector-Gates, Projektion und TASKPLAN-MergePolicy | Snapshotdateiformat, Cloud-/SFTP-Transport, Host-Discovery-Implementierung |
| `sqlite-transit-sync` | geschlossene Snapshots, Manifest/Hash/Quick-Check, optionales HMAC, Pull-State, transaktionaler Aufruf der injizierten MergePolicy, konservative Retention | Taskstatus, Task-Dedupe, Scope-Bedeutung, Claims, fachliche Konfliktauflösung |
| `system-gap-master` | stabile konfigurierte System-/Peer- und Shared-Root-Nachweise, R9-Zonenauflösung, Transportbereitschaft und providerneutrale Handoffs | zweite Queue, Tasktabellen, Task-Merge, Selector, Claim oder Abschluss |

Vorgesehene Adapter:

- **in `task-master`:** `TaskplanTransitProjection`,
  `TaskplanTransitMergePolicy`, `SharedScopeRegistry`,
  `SharedTaskMaterializer` und Selector-Gate;
- **über öffentliche `sqlite-transit-sync`-Schnittstellen:** `SyncConfig`,
  `TransitSync`, `MergePolicy`, `SnapshotAuthenticator`; kein TASKPLAN-Code im
  Carrier;
- **über öffentliche `system-gap-master`-Schnittstellen:** R9-Zonenresolver und
  read-only Registry-/Capability-Readback; kein Import von Ticket-Lifecycle-
  oder SFTP-Ausführungslogik in TASKPLAN.

## 11. TDD-Akzeptanzmatrix

Alle Tests verwenden zwei isolierte synthetische Knoten A/B, getrennte lokale
DBs und State-Verzeichnisse, eine kontrollierbare Uhr und ausschließlich
synthetische Daten.

| ID | Szenario | Erwartung |
|---|---|---|
| T01 | Legacy/lokale Aufgabe auf A, Snapshot nach B | In Projektion A nicht enthalten; auf B nie sichtbar oder claimbar |
| T02 | `local:A` in absichtlich präpariertem B-Import | importiert/quarantänisiert, aber nie selektierbar; Diagnose vorhanden |
| T03 | identische Shared-Aufgabe auf A und B erkannt | gleiche `logical_task_id`, genau eine materialisierte Aufgabe je Knoten |
| T04 | Retry desselben Imports | keine neue Zeile oder Ereignisduplikate |
| T05 | Importreihenfolge A→B und B→A | identischer Endzustand und identische Ereignismenge |
| T06 | Abschluss auf A, Pull auf B | auf B terminal und nicht erneut vorgelegt |
| T07 | gleichzeitige Claim-Requests | Autorität vergibt genau einen Grant/Fencing-Token |
| T08 | Claim-Request ohne Grant | kein `active`, keine Ausführung |
| T09 | Lease abgelaufen | alter Token abgewiesen; neue Autoritätslease erforderlich |
| T10 | Abschluss mit altem Fencing-Token | fail-closed, Zustand unverändert |
| T11 | Uhrabweichung innerhalb Unsicherheitsfenster | Aufgabe bleibt ungeclaimt, klare Clock-Skew-Diagnose |
| T12 | kontrollierte Clock-Skew außerhalb Lease | deterministischer neuer Grant nach Autoritätsentscheidung |
| T13 | Tombstone vor älterem Update importiert | Tombstone bleibt wirksam |
| T14 | älteres Update vor Tombstone importiert | derselbe tombstoned Endzustand wie T13 |
| T15 | Tombstone-Retry und Retention vor allen Acks | idempotent; keine physische Bereinigung |
| T16 | autoritative Rekategorisierung | beide Knoten konvergieren ohne Timestamp-LWW |
| T17 | konkurrierende Terminalereignisse | `terminal_conflict`, nicht selektierbar, Auflösung referenziert beide Eltern |
| T18 | unbekannte System-/Scope-/Major-Version | nicht selektierbar und nicht still verworfen |
| T19 | abgelaufener oder widerrufener Root-Proof | Shared-Auswahl und Export blockiert; lokale Phase A läuft |
| T20 | `local -> shared` mit Crash vor Commit | genau lokale Darstellung bleibt; Retry nutzt dieselbe Migrations-ID |
| T21 | `local -> shared` vollständig | genau Shared-Darstellung, Alias und Historie erhalten |
| T22 | unilateral versuchtes `shared -> local` | blockiert; erst Tombstone/Close der Autorität zulässig |
| T23 | gleiche logische ID, abweichende Identitätsfelder | `identity_collision`, nicht selektierbar |
| T24 | manipuliertes Manifest/Hash/HMAC | Ablehnung vor Merge |
| T25 | Secret in Shared-Freitext | Push bricht ab; Wert erscheint nicht im Fehler |
| T26 | Live-DB/WAL/SHM im Yard | Gate rot; keine Veröffentlichung oder Öffnung |
| T27 | fehlender/deaktivierter/gestörter Carrier | lokale Auswahl funktioniert; Shared-Diagnose statt Gesamtstillstand |
| T28 | wiederholter Push/Pull | idempotent; `PRAGMA quick_check` auf beiden lokalen DBs grün |
| T29 | Raw-Projektionsprüfung für E02=A | null lokale/Legacy-Zeilen und null nicht freigegebene Scopes |
| T30 | `scope=central` plus gültiges `shared:*` | bleibt gemäß bestehendem Gate nicht autonom |

Die spätere Implementierung gilt erst als abgenommen, wenn die Matrix auf
Linux und Windows grün ist, die vollständigen bestehenden Suiten aller
geänderten Module bestehen und ein separater Privacy-Readback den
Transit-Snapshot selbst untersucht.

## 12. Entscheidungs- und Implementierungsübergabe

Für die Nutzerentscheidung genügt eine der beiden Aussagen:

```text
E02=A — nur Shared-Zeilen projizieren; lokale Aufgaben verlassen den Host nie.
E02=B — vollständige relevante Tabellen mit zwingender Provenienz synchronisieren.
```

Bis dahin bleiben Code, Datenbanken, Cloud, Provider, Autostart und fremde Hosts
unverändert. Nach der Entscheidung ist ein getrenntes TASKSOLVER-Ticket mit
TDD-Implementierung, unabhängiger Privacy-/Distributed-Systems-Review und einer
synthetischen Zwei-Knoten-Abnahme erforderlich.
