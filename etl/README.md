# Confluence Cloud → Coveo demo

Write direction only: Confluence tap → official target-parquet → `etl.py` → Coveo target.
Python 3.10. Connector repositories: `hotgluexyz/tap-confluence-cloud` and
`hotgluexyz/target-coveo`.

## What is real vs simulated

| Piece | Status |
|---|---|
| Confluence pages and attachments | **Real** (Northstar synthetic content in Confluence) |
| Atlassian users (Jacob / Jacob No Access) | **Real** |
| `Coveo Demo Engineering` group membership | **Real** (extracted live via `group_members`) |
| Document-level ACLs in Coveo | **Simulated** in ETL when `synthetic_acl_demo` is enabled |
| Coveo security identity provider + email aliases | **Real** Push API upserts |
| Confluence page restrictions | **Not used** (Confluence Free cannot set them) |

### `synthetic_acl_demo` (opt-in, default off)

Enable with `SYNTHETIC_ACL_DEMO=true` **or** by deploying a file named
`synthetic_acl_demo.json` (see `synthetic_acl_demo.example.json`) next to `etl.py`.
Without either, the ETL keeps the production path that maps live Confluence
`space_permissions` / `read_restrictions`.

When enabled:

1. Emits `SecurityIdentities` (users with Email Security Provider aliases, Engineering
   group with full live membership — replace semantics).
2. Assigns document ACLs from the Northstar title matrix (not Confluence restrictions).
3. Sets `securityProvider` on every allowed identity.
4. Attachments inherit the containing page’s demo policy.
5. Unexpected page titles fail the job (no silent ACL inventing).

Never deploy `synthetic_acl_demo.json` outside the Northstar demo flow.

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
- **Search API blocker:** API Key 2 returns HTTP 403 for `/rest/search/v2`. Validate
  Jacob vs Jacob No Access trimming in the Coveo admin Content Browser / search UI
  (or a search-enabled key). Push acceptance alone does not prove query-time trimming.

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
