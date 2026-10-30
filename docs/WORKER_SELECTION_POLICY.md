# BACH/Ocean: offene Worker-Auswahl

`taskplan.worker_selection`, Vertrag `worker-selection.v1`, ist ein reiner,
persistenzfreier Baustein zusätzlich zu `worker_policy`. Er betreibt weder
Worker noch Taskqueue, Registry, Lease oder eine zweite Konfigurationsablage.

Im Standard sind `assigned_to`, `assigned_slot` und `required_model` Präferenzen.
`require_assigned_slot=True` macht die Slotzuweisung verbindlich; eine fehlende
Zielzuweisung ist dann keine Freigabe. `enforce_model_binding=True` macht eine
vorhandene Modellzuweisung verbindlich. Nur echte boolesche Werte sind gültig.

Globale Einstellungen liefern die Standards. Ein Task-Override `None` erbt,
`True` oder `False` überschreibt den globalen Wert. Eine explizite Restriktion
des Workerprofils bleibt zusätzlich wirksam. Die Consumer lesen diese Werte
aus ihrer bestehenden kanonischen, versionierten Einstellungsquelle.

Eine fehlende oder leere optionale Repo-Auswahlliste schränkt die Auswahl nicht
ein. Eine nichtleere Liste bleibt verbindlich. Das sagt nichts darüber aus,
ob die Task eine vertrauenswürdig registrierte Repository-/Sourcebindung hat:
diesen unabhängigen Nachweis muss der Consumer weiterhin verlangen.

Priorität sortiert normalerweise, einschließlich P0. Ein ausdrücklich aktiver
Pickupfilter darf Prioritäten weiterhin einschränken. Sicherheits-, Tool-,
Modellzugangs-, Lock-, Lease-, Scope- und Abschlussprüfungen bleiben bestehen.

Der BACH-Adapter nutzt `worker_review_policy.v1` und `worker_review_task.v1:`
im vorhandenen `system_config`-Owner; keine parallele Policydatei entsteht.
Er bindet Task-Version und Konfigurationsdigest vor Acquire und vor Effekten.
Unlesbare, veraltete oder unbekannte Policyversionen halten die Ausführung an.
Ältere Schreibclients dürfen neuere ONLY-Felder durch Auslassen nicht löschen.

Die additive Integration baut auf dem aktuellen Default 0a2dbe68 auf und
übernimmt ausschließlich den vorhandenen reinen Worker-Review-Vertrag aus
4a455752 sowie diesen Auswahlbaustein. Aktuelle TaskClient-/API-Pagination bleibt
erhalten. Das installierbare Paket trägt Version 0.7.3; Veröffentlichung und
Consumerintegration benötigen weiterhin eine Prüfung und einen festen Commitpin.
