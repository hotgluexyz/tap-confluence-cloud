"""Verify content and permission contracts through transforms and Singer export."""

import base64
import json

import pandas as pd
import pytest

import etl


def page():
    return {
        "id": "17",
        "title": "Troubleshooting",
        "url": "https://demo.atlassian.net/wiki/spaces/A/pages/17/Title",
        "spaceId": "5",
        "authorId": "author-9",
        "createdAt": "2026-01-02T03:04:05Z",
        "version": json.dumps({"createdAt": "2026-02-03T04:05:06Z"}),
        "permissions_complete": "true",
        "space_permissions": json.dumps(
            [
                {
                    "principal": {"type": "group", "id": "staff"},
                    "operation": {"key": "read", "targetType": "space"},
                },
                {
                    "principal": {"type": "user", "id": "editor-only"},
                    "operation": {"key": "update", "targetType": "page"},
                },
            ]
        ),
        "read_restrictions": json.dumps(
            [
                {"contentId": "1", "users": [{"accountId": "reader-1"}], "groups": []},
                {
                    "contentId": "17",
                    "users": [],
                    "groups": [{"id": "engineering", "name": "Engineering"}],
                },
            ]
        ),
        "body": json.dumps(
            {"storage": {"value": "<p>Preserve the full troubleshooting steps.</p>"}}
        ),
        "author": json.dumps({"displayName": "Ada"}),
        "labels": "[]",
        "properties": "[]",
    }


def test_parquet_strings_preserve_metadata_and_intersect_permission_layers():
    source = page()
    result = etl.transform(pd.DataFrame([source]), "pages").to_dict("records")[0]
    assert result["documentId"] == "https://demo.atlassian.net/wiki/pages/viewpage.action?pageId=17"
    assert result["externalId"] == "confluence:pages:17"
    assert result["data"] == "<p>Preserve the full troubleshooting steps.</p>"
    assert result["date"] == "2026-02-03T04:05:06Z"
    assert result["createddate"] == "2026-01-02T03:04:05Z"
    assert result["author"] == "Ada"
    assert result["permissions"] == [
        {
            "allowAnonymous": False,
            "allowedPermissions": [{"identity": "staff", "identityType": "Group"}],
        },
        {
            "allowAnonymous": False,
            "allowedPermissions": [{"identity": "reader-1", "identityType": "User"}],
        },
        {
            "allowAnonymous": False,
            "allowedPermissions": [{"identity": "engineering", "identityType": "Group"}],
        },
    ]
    assert json.loads(result["confluencepermissionmetadata"])["read_restrictions"] == json.loads(
        source["read_restrictions"]
    )
    renamed = {**source, "title": "Renamed", "url": source["url"] + "Renamed"}
    assert (
        etl.transform(pd.DataFrame([renamed]), "pages").iloc[0].documentId == result["documentId"]
    )


@pytest.mark.parametrize(
    "change",
    [
        {"permissions_complete": "false"},
        {"space_permissions": "[]"},
        {"read_restrictions": "not-json"},
    ],
)
def test_incomplete_or_malformed_security_metadata_is_not_exported(change):
    with pytest.raises(ValueError):
        etl.transform(pd.DataFrame([{**page(), **change}]), "pages")


def test_duplicate_identifiers_are_rejected():
    with pytest.raises(ValueError, match="Duplicate"):
        etl.transform(pd.DataFrame([page(), page()]), "pages")


def test_singer_export_preserves_binary_and_nested_permissions(tmp_path, monkeypatch):
    input_dir = tmp_path / "sync-output"
    input_dir.mkdir()
    source = page()
    pd.DataFrame([source]).to_parquet(input_dir / "pages.parquet")
    binary = b"%PDF-1.4\nattachment contents"
    attachment = {
        **source,
        "id": "att33",
        "pageId": "17",
        "title": "checklist.pdf",
        "mediaType": "application/pdf",
        "content_base64": base64.b64encode(binary).decode(),
    }
    pd.DataFrame([attachment]).to_parquet(input_dir / "attachments.parquet")
    monkeypatch.setattr(etl, "ROOT_DIR", str(tmp_path))
    monkeypatch.setattr(etl, "INPUT_DIR", str(input_dir))
    monkeypatch.setattr(etl, "OUTPUT_DIR", str(tmp_path / "etl-output"))
    etl.run()
    messages = [
        json.loads(line) for line in (tmp_path / "etl-output/data.singer").read_text().splitlines()
    ]
    assert sum(m["type"] == "SCHEMA" for m in messages) == 1
    records = [m["record"] for m in messages if m["type"] == "RECORD"]
    assert len(records) == 2
    assert "compressedBinaryData" not in records[0]
    assert "data" not in records[1]
    assert base64.b64decode(records[1]["compressedBinaryData"]) == binary
    assert records[0]["permissions"] == records[1]["permissions"]
    assert isinstance(records[0]["permissions"][0]["allowedPermissions"], list)
    assert records[1]["parentId"] == records[0]["documentId"]
