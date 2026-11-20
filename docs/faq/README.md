# Profile-specific Mini App FAQ

`kalisto/` and `mephisto/` each contain `FAQ_CLIENT.md` and `FAQ_TECHNICIAN.md`.
The authenticated `/api/help` endpoint selects by server profile and current role.
Clients and supervisors receive client help; technicians and admins receive staff help.
Request parameters cannot select another profile or audience.

Docker packages this directory under `/app/help/faq`. Missing profile content fails
with the existing unavailable response; it does not fall back to another bot's FAQ.
The old `docs/FAQ_CLIENT.md` and `docs/FAQ_TECHNICIAN.md` are historical shared copies,
not the active Mini App help source. Keep future updates in these profile directories.

User-visible differences:

- Kalisto: structured Destination fields, catalog or new advertiser selection;
  new-advertiser passthrough and buying flags for users with the corresponding
  team capability.
- Mephisto/TheRaw: source offer text, new advertisers processed by the existing
  formatter flow, optional important comments.
- Both: title search, 50-row pagination, permissions, CAP categories, private drafts,
  General review, Destination completion, Postbacks and separately allowed analytics.
