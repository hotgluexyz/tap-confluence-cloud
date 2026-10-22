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

## Verified

- Content pipeline: 8 pages + 3 attachments → Coveo Documents (prior job).
- Secured source `hotgluedemoe59eep2q-wwpem6tm2h6z2dy3vtjgkbkfiu` with SIP
  `Northstar Confluence Demo SIP`.
- Live Push accepted security identities and a secured document (HTTP 202).

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
