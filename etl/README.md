# Confluence Cloud → Coveo demo

Write direction only: Confluence tap → official target-parquet → `etl.py` → Coveo target.
Python 3.10. Connector repositories: `hotgluexyz/tap-confluence-cloud` and
`hotgluexyz/target-coveo`.

## Verified locally and in a hosted Hotglue job

On October 22, 2026 the tap extracted 8 synthetic Northstar Cloud pages and 3
attachments (PDF, DOCX and text). Official target-parquet serialized the records;
the ETL mapped all 11; Coveo accepted all 11 with HTTP 202 and no failed records.
The hosted write job also reached `JOB_COMPLETED`, with the downloaded export
summary reporting `Documents: success=11, fail=0`. All 11 export bookmarks record
HTTP 202. The ETL and requirements are deployed under the flow's default tenant
for the Coveo connector; downloaded copies match the repository runtime files.
Acceptance is not proof of indexing, text extraction or search visibility.

The pages cover SAML troubleshooting, API ingestion, incident response, disaster
recovery, onboarding and search relevance. Seed content is fictional. The seed
manifest and raw validation outputs are in the repository's ignored
`.secrets/coveo-demo/` directory; final pipeline outputs are under `final/`.

## Permissions and remaining gates

- Live records preserve raw space permissions and ancestor/page read restrictions.
  Attachments inherit the containing page's metadata.
- Actual direct restrictions on these pages are empty: Confluence rejected attempts
  to set them with HTTP 403. Each demo page now explicitly labels restriction
  scenarios as illustrative. Tests cover nonempty user/group/inherited restrictions.
- Coveo remains a shared source. Permission metadata is submitted, but security
  providers, identity membership and security trimming are NOT configured or proven.
  Never use this demo source for confidential content.
- Hotglue authentication succeeds in `coveo-demo-hotglue`. Earlier 403 responses
  targeted the wrong environment, `jacob-maz-test-environment`; some retries also
  used an older overriding personal secret. They do not establish invalid keys.
  Hosted extraction, transformation and Push API delivery are verified.
- Coveo search and field-management reads return 403. Indexing and custom field
  mappings still need verification with suitable privileges or the admin console.

## Run the transform

Install `requirements.txt` in a separate Python 3.10 virtualenv. Keep the official
target-parquet environment separate because its older dependencies conflict.

```bash
ROOT_DIR=/absolute/path/to/job-root JOB_TYPE=write python etl.py
uvx --with tox-uv tox -e py310,lint
```

The job root contains `sync-output/*.parquet`; output is `etl-output/data.singer`.
Check where target-parquet actually writes files before running ETL: the installed
0.0.21 version wrote to the working directory despite its filepath setting.
Deploy only `etl.py` and `requirements.txt` from a clean staging directory.

The transform emits stable ID-based document URLs, titles, HTML or base64 file
bytes, author, dates, labels/properties, and permissions. Raw ACL rules are retained
as `confluencepermissionmetadata`; mapped permission sets are also serialized in
`confluencepermissions`. Create/map custom Coveo fields before demonstrating them
in search results. Missing/incomplete ACL data fails rather than granting public access.

For another flow, configure Confluence as the v2 source and Coveo as connector,
select only the demo page IDs and both streams, deploy the write ETL, run discovery,
save the source field map, and run one write job. Validate all 11 target bookmarks,
then verify page and attachment phrases in Coveo's Content Browser/search.

The tap is full-table and does not detect deletions. The target supports individual
explicit deletes, but this ETL emits upserts only. Inline attachments are limited
by the target to 5 MiB. Neither connector is a complete replacement for all
features of Coveo's native Confluence connector.

`seed.py` requires requests, python-docx and reportlab in addition to runtime
dependencies. It reads `CONFLUENCE_EMAIL` and `CONFLUENCE_API_TOKEN`, reuses exact
demo titles, and creates only missing demo content. Use `--skip-restrictions` when
the account cannot set restrictions; never treat intended scenarios as actual ACLs.
