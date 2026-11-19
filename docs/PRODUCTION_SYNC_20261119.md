# Production source synchronization — 19 November 2026

GitHub publication target: https://github.com/Zavodnoi-bylon/integration-task-manager
(remote `github`). Historical `origin` points at GitLab; this synchronization does
not push to GitLab.

The local monorepository now combines the active bot source from
`/home/teamactivity/integration-task-manager/releases/20261116Taio-55c98d4`
with the active Mini App source from
`/home/teamactivity/dest-bot-miniapp/releases/20261119Tsearch-pages`.
Mini App runtime is `dest-bot-miniapp:20261119-search-pages`; bot runtime remains
`integration-task-manager:20261116-aio-55c98d4`. Database migrations are through 0013.

The synchronization preserves existing local work. A complete source backup was
saved locally before applying the production additions. Runtime env, credentials,
databases, dumps and user messages are excluded from Git.

Mini App includes profile ticket numbers, supervisor visibility, buying flags,
CAP categories, AIO advertiser synchronization, signed analytics handoff,
optional important comments for Mephisto, title search and page navigation.
See `services/task-miniapp/docs/TICKET_SEARCH.md` for search semantics and checks.

`services/reporting` is the existing historical subtree, not the deployment target
of this synchronization. Live reporting work remains in Team Activity Explorer.
Do not deploy that subtree just because it is included in the monorepository.

The standalone temporary Mini App checkout used to prepare search is superseded
for ongoing development by `services/task-miniapp` in this repository.
One-off production deployment/diagnostic helpers are retained on the server,
outside the canonical source set; they are not required application files.
