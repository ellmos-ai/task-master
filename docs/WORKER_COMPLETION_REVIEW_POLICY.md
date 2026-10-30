# BACH/Ocean Worker-Abschluss- und Reviewvertrag

Status: versionierter, persistenzfreier Vertragsbaustein
Code: `taskplan.worker_policy`
Vertragsversion: `worker-review-policy.v1alpha1`

## Zweck und Grenze

Der Vertrag entscheidet deterministisch, ob ein Worker-Ergebnis abgeschlossen werden darf, eine Review erwartet oder eine Korrektur benötigt. Er ist für BACH und Ocean wiederverwendbar und schreibt weder Taskzustände noch Reviews.

Der Vertrag ist **nicht** der lokale Projekt-Siegelpool `taskplan_project_reviews`: Dieser prüft TASKWRITER-/MAINTAINER-Projekte auf einem Host. Worker-Ergebnisreviews binden dagegen eine Task und eine konkrete Ergebnisrevision.

Der Aufrufer bleibt verantwortlich für Taskauswahl, Lease/Fence, Lockprüfung, Ergebnisbelege, externe GitHub-Verifikation, Speicherung und GUI-Anzeige. Kein Modus und kein Task-Override darf diese Integritätsprüfungen abschalten.

## Revisionsbezug aus dem Task-Master-Lebenszyklus

`result_revision` verweist auf einen unveränderlichen Ergebnisbeleg, den die integrierende Laufzeit an die exakte Task- und Scope-Version, die Projektzustandsreferenz und den Arbeitsbaum-Fingerprint bindet. Ändert sich eine dieser Grundlagen, muss die Laufzeit eine neue Revisionskennung ausstellen; ältere Reviews sind dann veraltet. Dieser Vertrag vergleicht nur die Task- und Revisionsreferenzen und erstellt weder Snapshots noch Hashes.

TaskMaster speichert höchstens Referenzen auf Test-, Commit- oder Handoff-Belege. Es führt diese Schritte nicht selbst aus, erzeugt keine Commits und baut keine zweite Lock-Engine. Der bestehende append-only Recovery-Journal und die Freeze-Barriere für Task-Batches bleiben getrennte Task-Master-Lifecycle-Arbeit; dieser Policyvertrag dupliziert sie nicht.

## Modi

| Modus | Ergebnisabschluss | Worker darf nächste unabhängige Task claimen | Review verfällt |
|---|---|---:|---|
| `simple_task` | Selbstprüfung, erfüllter Taskvertrag und gültiger Ergebnisbeleg genügen zum Abschluss. Keine zusätzliche interne Review ist erforderlich. Wenn keine normale Task passt und die Policy Review-Aufgaben erlaubt, kann der Selektor eine Review-Aufgabe übernehmen. | Ja, auch wenn eine User- oder externe Review für die aktuelle Task wartet | Keine interne Reviewfrist |
| `balanced` | Gültige unabhängige Review schließt ab. Ohne Review bleibt das Ergebnis offen; der Worker darf andere unabhängige Tasks bearbeiten. Konfigurierte Frist kann ein unbeantwortetes Review ohne Befund abschließen. | Ja, während Review wartet | Optional; Standard `None` |
| `high_security` | Nur eine unabhängige Review aus der Top-Reviewer-Liste schließt ab. Ausstehende User-, GitHub- oder interne Reviews halten den Worker an. | Nein, bis Review oder Korrektur abgeschlossen ist | Nie automatisch |

Bei ausstehender Review bleiben explizite Taskabhängigkeiten bestehen: `dependents_may_start` bleibt `false`, bis der Parent tatsächlich abgeschlossen ist. „Nicht blockierend“ erlaubt im `simple_task`- und `balanced`-Modus unabhängige Workerarbeit, überspringt aber keine Task-Abhängigkeit. Ein User-Review blockiert den Abschluss der betroffenen Task in allen Modi und verfällt nie.

## Vorrangregeln

1. **Integrität und Korrektur:** Gültige Task-Lease, Fencing und freie Locks sowie ein gültiger Ergebnisbeleg stehen vor jeder Reviewentscheidung. Ein unerfüllter Taskvertrag verhindert weiterhin jeden Abschluss. Ein aktueller, zulässiger `rejected`-Befund verlangt Korrektur. Bei unerfülltem Taskvertrag verlangen auch aktuelle, zulässige interne `findings` in allen Modi Korrektur, selbst wenn eine externe Review bereits verifiziert ist. Erst bei erfülltem Taskvertrag kann die aktivierte externe Reviewroute interne Befunde ersetzen. Das ist keine Freigabe des Ergebnisses, der User- oder GitHub-Pflicht.
2. **User-Review:** Ein ausdrücklich verlangtes User-Review blockiert alle Modi und verfällt nie. Eine Modellreview kann es nicht ersetzen. Ein Task-Override kann eine User-Review-Anforderung hinzufügen, aber keine bereits gesetzte Anforderung entfernen.
3. **GitHub-Review:** `github_pr_review_required` aus dem kanonischen Taskkontext ist ein hartes Gate und gilt unabhängig von der globalen oder taskbezogenen Routingpräferenz `github_external_review`. Eine externe Review mit Status `PENDING`, eine aktuelle `REJECTED`-Review oder eine nicht an Task und Ergebnisrevision gebundene `VERIFIED`-Review schließt die Task nicht ab. Der Adapter darf `VERIFIED` nur liefern, wenn PR, exakter Head sowie alle für die Task geltenden Checks, Freigaben und gegebenenfalls der Merge nachgewiesen sind. Ist das GitHub-Reviewrouting aktiviert, kann dieser aktuelle Beleg die interne Modellreview ersetzen. Ist es deaktiviert, muss der harte GitHub-Beleg trotzdem bestehen; danach gilt der normale interne Moduspfad. Die externe Prüfung verfällt nicht durch den internen Review-Timer.
4. **Interne Review:** Reviewer müssen in der Policy zugelassen, vom Autor unabhängig und an dieselbe Task-ID und Ergebnisrevision gebunden sein. `rejected` verlangt Korrektur. In `high_security` reichen Findings nicht; dort ist eine gültige Top-Reviewer-Freigabe nötig.

`findings` bedeutet bei erfülltem Taskvertrag in `simple_task` und `balanced` einen nicht blockierenden Befund, der zusammen mit dem Ergebnis gespeichert wird. Bei unerfülltem Taskvertrag führen aktuelle, zulässige interne Findings dagegen in jedem Modus zu `correction_required` mit `review_findings_contract_not_met`; Ergebnisabschluss und abhängige Tasks bleiben gesperrt. Ein Befund, der Korrektur erfordert, wird als `rejected` gemeldet und setzt den Korrekturpfad; in `high_security` reicht `findings` nicht für den Abschluss. Eine Reviewtask gilt nur dann als auswählbar, wenn nach Ausschluss des Task-Autors mindestens ein zugelassener Reviewer verfügbar ist.
5. **Taskauswahl:** `review_task_eligible` ist nur eine Eignungsentscheidung. Im `simple_task`- und `balanced`-Modus darf der Selektor eine Review-Task erst übernehmen, wenn die normale Auswahl gemäß der aktiven Auswahlpolicy keine passende Task liefert und die Reviewer-Liste dafür Kandidaten enthält. Im `high_security`-Modus kommen dafür nur zugelassene Top-Reviewer infrage. Für die konkrete Review-Task muss der Selektor Unabhängigkeit und Autor erneut gegen genau deren Ziel-Task und Ergebnisrevision prüfen; `eligible_reviewer_ids` bezieht sich auf das aktuell ausgewertete Ergebnis. Das Claimen selbst bleibt beim Task-/Lease-Backend.

## Reviewer und Overrides

`WorkerReviewPolicy.reviewers` enthält stabile Reviewer-IDs und die Stufe `standard` oder `top`. `simple_task` und `balanced` können jeden zugelassenen Reviewer für separat übernommene Review-Tasks verwenden; `high_security` filtert auf `top`. Der Task-Autor wird immer aus dem Pool ausgeschlossen. Reviewer-IDs sind Anbieter-/Agentenidentitäten; sie enthalten keine lokale Modellslot- oder Compute-Bindung.

`TaskPolicyOverride` kann Modus, eine zusätzliche User-Review-Anforderung, die externe GitHub-Reviewroutingpräferenz und die Übernahme von Review-Tasks für eine einzelne Task festlegen. Das globale Review-Task-Default lässt sich damit pro Task an- oder ausschalten. Ein globales oder taskbezogenes `github_external_review=False` schaltet nur die Reviewroute um; es entfernt keine kanonische `github_pr_review_required`-Anforderung. Der Task-Override kann eine bereits gesetzte User-Review-Anforderung nicht entfernen. Das Ergebnis trägt `policy_version`, `policy_source`, `effective_mode`, Zustand und Begründung. So kann das Runtime- und GUI-System anzeigen, welche Regel tatsächlich angewandt wurde. Die Speicherung dieses Snapshots gehört in die integrierende Laufzeit.

Der Override ist eine Eingabe des Aufrufers, keine Berechtigung. Die integrierende Laufzeit muss vorab prüfen, dass die Quelle den Task-Override setzen darf; Aufgaben- oder Prompttext allein darf keinen Modus herabsetzen. `policy_source` zeichnet nur `system_default` oder `task_override` auf und ersetzt diese Autorisierungsprüfung nicht.

## Python-Oberfläche

```python
from taskplan.worker_policy import (
    CompletionMode,
    Reviewer,
    ReviewerTier,
    WorkerReviewContext,
    WorkerReviewPolicy,
    evaluate_worker_policy,
)

policy = WorkerReviewPolicy(
    policy_version="worker-policy-2026-10-14",
    default_mode=CompletionMode.BALANCED,
    reviewers=(
        Reviewer("ollama/qwen", ReviewerTier.STANDARD),
        Reviewer("claude/opus", ReviewerTier.TOP),
    ),
    review_expiry_seconds=86_400,
)
context = WorkerReviewContext(
    task_id="task-123",
    author_id="codex@ASUS-GEI",
    result_revision="sha256:result-revision",
    lease_valid=True,
    fence_valid=True,
    locks_clear=True,
    task_contract_met=True,
    result_evidence_valid=True,
    result_age_seconds=120,
)
decision = evaluate_worker_policy(policy, context)
receipt = decision.as_dict()
```

Die booleschen Integritätsfelder sind Belege aus den jeweiligen zuständigen Providern, keine frei schaltbaren Policy-Optionen. Ein Consumer darf sie nicht allein deshalb auf `true` setzen, weil ein Modus aktiv ist.

## Nicht in diesem Vertrag

- BACH-Ergebnis-/Review-Endpunkte und der BACH-Taskstatus: Umsetzungspaket #2210.
- Task-Divider, Review-Queue-Claim und Rückkehr zum Parent: #2211.
- Einstellungen, transparente Auswahlregeln und Lease-Countdown: #2212.
- Queue-Maintainer und Zuordnungsregler: #2213.
- TaskDB-Cutover oder zweite Queue: nicht Teil dieses Vertrags.
