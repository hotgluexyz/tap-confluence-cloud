# tap-confluence-cloud

Hotglue Singer SDK tap for Confluence Cloud **pages, attachments, metadata, and read restrictions**.
Generated from Hotglue's tap cookiecutter. Requires Python 3.10.

## Configuration

| Setting | Required | Meaning |
|---|---|---|
| `site_url` | yes | HTTPS site root, e.g. `https://example.atlassian.net/wiki` |
| `email` | yes | Atlassian account email |
| `api_token` | yes | Sensitive API token for that account |
| `space_ids` | no | Array of space IDs (at most 100); omitted means all accessible spaces |
| `page_ids` | no | Array of page IDs (at most 250); use to isolate demo content |
| `page_size` | no | Cursor/offset page size, 1–250, default 100 |
| `max_attachment_bytes` | no | Maximum attachment size, default 10 MiB; oversized files fail the sync |

Use an account permitted to read the selected pages, ancestors, attachments and space permissions.
No source writes are performed. Scoped API tokens using the Atlassian gateway are not supported.

```bash
uv venv --python 3.10 .venv
uv pip install --python .venv/bin/python -e . pytest responses ruff
.venv/bin/tap-confluence-cloud --config .secrets/config.json --discover > .secrets/catalog.json
.venv/bin/tap-confluence-cloud --config .secrets/config.json --catalog .secrets/catalog.json > .secrets/data.singer
```

Example config (replace placeholders only in a gitignored file):

```json
{"site_url":"https://example.atlassian.net/wiki","email":"YOUR_EMAIL","api_token":"YOUR_TOKEN","page_ids":["123"]}
```

## Streams

Both streams use primary key `id` and **FULL_TABLE** replication. A full rescan captures
permission-only changes even when the content version has not changed. State is not an
incremental watermark; deletion detection is not implemented.

- `pages`: v2 `/pages?body-format=storage`, title, storage-format body, author account ID
  and profile, timestamps, version, URL, labels, properties, space ID, parent IDs.
- `attachments`: child stream `/pages/{id}/attachments`, attachment metadata plus
  `content_base64` containing the actual bytes. Permissions come from the containing page.

V2 collections follow `_links.next` or the HTTP `Link` cursor. Read restrictions use
the v1 `/content/{id}/restriction/byOperation/read` API, paginating both users and groups.
V1 content ancestors identify every inherited page restriction. Space permissions use
the paginated v2 `/spaces/{id}/permissions` API.

Each record includes:

- `space_permissions`: complete raw permission assignments, including user/group and
  newer role/access-class principals that Confluence may return.
- `read_restrictions`: separate `{contentId, users, groups}` objects for ancestors and
  the page itself. An empty page restriction does **not** mean the space is public.
- `permissions_complete`: true only after all permission requests succeed. A failed
  permission request fails the sync; it does not silently grant anonymous access.

These fields describe source rules, not a fully resolved effective-user list. Product
access, group membership, identity mapping, public links, and destination enforcement
require additional integration. The tap does not claim to recreate Confluence's complete
authorization engine. Blog posts, comments and standalone spaces are excluded.

Attachments are base64-encoded without text extraction. A destination such as Coveo
extracts PDF/DOCX text. The destination can impose a smaller limit (target-coveo: 5 MiB).
Downloads follow Confluence redirects; Requests strips authorization on cross-host redirects.

## Checks

```bash
.venv/bin/pytest -q
.venv/bin/ruff check .
.venv/bin/ruff format --check .
```

HTTP fixtures exercise Singer output, cursor pagination, paginated inherited/user/group
restrictions, binary attachments, permission-read failures and attachment size failures.

References: [Confluence v2 API](https://developer.atlassian.com/cloud/confluence/rest/v2/intro/),
[v1 restrictions](https://developer.atlassian.com/cloud/confluence/rest/v1/api-group-content-restrictions/).
