# Confluence Cloud → Coveo demo

Write direction only: Confluence tap → official target-parquet → `etl.py` → Coveo target.
Python 3.10. Connector repositories: `hotgluexyz/tap-confluence-cloud` and
`hotgluexyz/target-coveo`.

## What is real vs simulated

| Piece | Status |
|---|---|
| Confluence pages and attachments | **Real** |
| Atlassian users / group membership | **Real** (via `users` / `groups` / `group_members`) |
| Document-level ACLs in Coveo | **Real Confluence restrictions** by default |
| Coveo security identity provider + email aliases | **Real** Push API upserts |
| `synthetic_acl_demo` title matrix | **Optional / off** — only if Confluence cannot set restrictions |

### Default path (Premium / real page restrictions)

Leave `synthetic_acl_demo` **disabled**. The ETL:

1. Maps live `space_permissions` + `read_restrictions` onto Coveo `permissions`.
2. Omits Confluence `access-class` / `role` principals (Coveo VirtualGroups that are never synced). Space ACLs must include concrete users and/or groups (Jacob, Jacob No Access, Coveo Demo Engineering). If only access-class grants remain, the ETL fails closed.
3. Tags every identity with `securityProvider` (SIP id).
4. Emits `SecurityIdentities` when the tap provides `users` / `groups` / `group_members`.

### Confluence space cleanup (manual)

In the demo space space permissions UI, remove “all licensed users” / product-admin style grants if present. Keep explicit readers: Jacob (`jacob@hotglue.io`), Jacob No Access (`collegejakemaz@gmail.com`), and group **Coveo Demo Engineering**. Do not change administrator permissions via automation.

### `synthetic_acl_demo` (opt-in, keep off with Premium)

Only for environments that cannot set page restrictions. Enable with
`SYNTHETIC_ACL_DEMO=true` or `synthetic_acl_demo.json` `{"enabled": true}`.
Uses a hardcoded Northstar title→principal matrix instead of Confluence ACLs.
Never enable this in production connectors.

## Verified (HGI-11498 security sync)

- Engineering group ID: `4e8cd076-10e6-4d42-b317-b850b5b0057a` (`Coveo Demo Engineering`).
- Tap extracted `users=2`, `groups=1`, `group_members=1` (Jacob only at baseline),
  `pages=8`, `attachments=3`.
- ETL `synthetic_acl_demo` emitted `SecurityIdentities=3` then `Documents=11` with
  title-based ACLs + `securityProvider`.
- `target-coveo` (feature/hgi-11498) accepted all identities and documents (HTTP 202).
  Secured source document count reached **11**.
- Atlassian membership grant/revoke via
  `POST`/`DELETE /wiki/rest/api/group/userByGroupId` is reflected in
  `membersByGroupId` and was pushed to the Coveo SIP with full-replace semantics
  (no page content edits).
- **Search API blocker:** Platform API keys (including a key that lists
  `SEARCH_API` / `EXECUTE_QUERY` / `IMPERSONATE` privileges) still return HTTP 403
  for `/rest/search/v2` and search-token creation. The same key can read the
  security cache and push documents. Validate Jacob vs Jacob No Access trimming in
  the Coveo admin Content Browser / a dedicated Search API key / search page.
- **Security cache (readable):** SIP entities `UP_TO_DATE` for both Atlassian
  account IDs and the Engineering group; `jacob@hotglue.io` present on Email
  Security Provider. `collegejakemaz@gmail.com` does not yet appear as its own
  Email Security Provider entity (alias may need Coveo UI confirmation).

## Run the transform

```bash
ROOT_DIR=/absolute/path/to/job-root JOB_TYPE=write python etl.py
# Demo mode:
SYNTHETIC_ACL_DEMO=true COVEO_SECURITY_PROVIDER_ID='Northstar Confluence Demo SIP' \
  ROOT_DIR=/absolute/path/to/job-root python etl.py
PYTHONPATH=. pytest -q
```

Deploy `etl.py`, `requirements.txt`, and (for Northstar only) `synthetic_acl_demo.json`.

Target config must use the secured Push source, API key with identity+document push
rights, and `security_identity_provider_id`. Tap config should include
`user_account_ids`, `group_names` (or `group_ids`), plus the demo `page_ids`.
