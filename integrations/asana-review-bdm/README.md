# Asana alerts: Review BDM

Prepared 26 October 2026 against the user-readable release on the old VPS.
This directory contains two replacement modules, source fingerprints, tests and
an explicit operator-run installer. No credentials or real messages are included.

The bot reads structured project section memberships and enum field values.
An exact Review BDM value means the integration work is complete according to
the user's workflow; it does not require Asana's completed flag.
Other projects' section memberships, comments and title text do not trigger it.
Comment alerts and story requests are removed. Other existing alert types remain.
Routing continues to use existing private user links; no new recipients are added.
If an assignee is removed during transfer, the previous assignee receives the alert.

Existing task snapshots without the new fields are seeded silently on their first
poll. New tasks are also recorded as baseline, not announced as a historical
transition. A subsequent observed transition into Review BDM sends one alert;
repeated polls while still there do not. A later exit and re-entry is a new transition.
Polling can miss changes that enter and leave entirely between two polls.
As before, a crash after Telegram accepts a message but before saving its task
snapshot can cause a duplicate on retry; this is not an exactly-once event system.

`apply.sh` verifies installed source fingerprints, backs up both modules and tags
the current image. It builds/tests the replacement before restarting, then checks
health. Build/test/health failure restores code and the old image. It does not
modify compose, tokens, user bindings, database schema or existing message history.
If installed code differs from the inspected copy, it stops without modification.

Run as vaultadmin on vaultwarden:

```bash
sudo bash /home/vaultadmin/asana-review-bdm-20261026/apply.sh
```

Enter sudo credentials only in the terminal. Success marker:
`REVIEW_BDM_UPDATE_READY; container healthy`.
Deployment and the first real transition must be verified after the operator runs
this command; preparing the patch is not proof of deployment.


The first attempt refused a source mismatch. Operator-exported installed/running
modules were identical and contained additional 403/404 recovery fixes. The package
was rebased onto those live modules, preserving AsanaAuthorizationError and
mark_task_unavailable handling. Eleven tests passed including those regressions.
The operator subsequently reported REVIEW_BDM_UPDATE_READY and healthy, with backup
/opt/asana-alerts-before-review-bdm-PgXnhBEc. First real Review BDM transition remains
pending verification. inspect-live.sh exports only the two modules and image ID.
