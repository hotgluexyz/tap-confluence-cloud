# coveo-demo

hotglue Python ETL transformation script using gluestick and pandas.

## hotglue docs MCP (preferred)

Use the **hotglue-docs** MCP server configured in `.cursor/mcp.json` (`https://docs.hotglue.com/mcp`). Search it for CLI, gluestick, transformations, and debugging before guessing.

If MCP is unavailable, use the doc index at `https://docs.hotglue.com/llms.txt` or these entry points:

- [Writing a basic script](https://docs.hotglue.com/transformation/writing-a-basic-script)
- [Gluestick overview](https://docs.hotglue.com/transformation/gluestick/overview)
- [Gluestick read files](https://docs.hotglue.com/transformation/gluestick/read-files)
- [Gluestick export](https://docs.hotglue.com/transformation/gluestick/export)
- [hotglue CLI ETL](https://docs.hotglue.com/cli/etl)
- [hotglue CLI overview](https://docs.hotglue.com/cli/overview)


## Entry point

- Run in hotglue jobs as `etl.py`.
- Locally: `ROOT_DIR=. python etl.py` after `sync-output/` exists (from a job or `hotglue etl setup-local-run`).

## Standard directories

| Path | Role |
| --- | --- |
| `sync-output/` | Input from tap (CSV/Parquet per stream) |
| `snapshots/` | Tenant snapshots and `tenant-config.json` (optional) |
| `etl-output/` | Script output for the target (e.g. Singer) |
| `catalog.json` | Singer catalog (job artifact; gitignored) |

`etl.py` sets `ROOT_DIR` (default `.`), `INPUT_DIR`, `OUTPUT_DIR`, and `SNAPSHOT_DIR`.

## Gluestick and pandas

- `gs.Reader(INPUT_DIR)` — list streams via `eval(str(reader))`; read with `reader.get(stream, catalog_types=True)`.
- Primary keys: `reader.get_pk(stream)`.
- Transform in `transform()` with pandas.
- Export: `gs.to_export(df, stream, OUTPUT_DIR, keys=...)` or `gs.to_singer(...)`.

## hotglue CLI

Install: `npm install -g @hotglue/cli`.

Local debug flow:

1. `hotglue etl setup-local-run <job_root>` — job data + `.env` (renames `etl-output` → `etl-output-reference`, `snapshots` → `snapshots-reference`)
2. `hotglue etl local-run` — run in Docker and diff `etl-output` vs `etl-output-reference`

Deploy: `hotglue etl deploy`. Optional comparator tuning: `test-config.json` in the script folder (`sort_config`, `ignore_columns`, `rename_config`).

## Environment variables

Job env includes `TENANT`, `FLOW`, `ENV_ID`, `JOB_ID`, `JOB_ROOT`, `SYNC_TYPE`, `JOB_TYPE`, `CONNECTOR_ID`, `TAP`, `TARGET`. Read with `os.environ.get(...)`.

## Quality checks

```bash
pip install -r requirements.txt
tox
```

Gitignored: `sync-output/`, `etl-output/`, `snapshots/`, `catalog/`, `catalog.json`.
