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


def test_empty_page_body_falls_back_to_title_for_coveo_data():
    source = {
        **page(),
        "title": "Untitled live doc 2026-10-22",
        "body": json.dumps({"storage": {"representation": "storage", "value": ""}}),
    }
    result = etl.transform(pd.DataFrame([source]), "pages").to_dict("records")[0]
    assert result["data"] == "Untitled live doc 2026-10-22"


def test_all_licensed_users_expands_to_synced_users_and_skips_admins_class():
    space = [
        {
            "principal": {"type": "access-class", "id": "ALL_LICENSED_USERS"},
            "operation": {"key": "read", "targetType": "space"},
        },
        {
            "principal": {"type": "access-class", "id": "ALL_PRODUCT_ADMINS"},
            "operation": {"key": "read", "targetType": "space"},
        },
        {
            "principal": {"type": "user", "id": "712020:jacob"},
            "operation": {"key": "read", "targetType": "space"},
        },
    ]
    sets = etl.permissions(
        space,
        [],
        licensed_user_ids=["712020:jacob", "712020:no-access"],
    )
    assert sets == [
        {
            "allowAnonymous": False,
            "allowedPermissions": [
                {"identity": "712020:jacob", "identityType": "User"},
                {"identity": "712020:no-access", "identityType": "User"},
            ],
        }
    ]


def test_page_restriction_still_excludes_non_members_after_licensed_expansion():
    space = [
        {
            "principal": {"type": "access-class", "id": "ALL_LICENSED_USERS"},
            "operation": {"key": "read", "targetType": "space"},
        }
    ]
    restrictions = [
        {"contentId": "1", "users": [{"accountId": "712020:jacob"}], "groups": []}
    ]
    sets = etl.permissions(
        space,
        restrictions,
        licensed_user_ids=["712020:jacob", "712020:no-access"],
    )
    assert len(sets) == 2
    space_ids = {a["identity"] for a in sets[0]["allowedPermissions"]}
    assert space_ids == {"712020:jacob", "712020:no-access"}
    assert sets[1]["allowedPermissions"] == [
        {"identity": "712020:jacob", "identityType": "User"}
    ]


def test_access_class_only_space_permissions_fail_closed_without_users_stream():
    space = [
        {
            "principal": {"type": "access-class", "id": "ALL_LICENSED_USERS"},
            "operation": {"key": "read", "targetType": "space"},
        }
    ]
    with pytest.raises(ValueError, match="users stream is empty"):
        etl.permissions(space, [])


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
    monkeypatch.setenv("SYNTHETIC_ACL_DEMO", "false")
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


def test_synthetic_acl_demo_emits_identities_and_title_policies(tmp_path, monkeypatch):
    input_dir = tmp_path / "sync-output"
    input_dir.mkdir()
    source = {
        **page(),
        "title": "[Coveo Demo] Atlas API ingestion technical guide",
        "id": "655382",
    }
    pd.DataFrame([source]).to_parquet(input_dir / "pages.parquet")
    pd.DataFrame(
        [
            {
                "id": etl.JACOB_ACCOUNT_ID,
                "accountId": etl.JACOB_ACCOUNT_ID,
                "email": etl.JACOB_EMAIL,
                "displayName": "Jacob Mazurkiewicz",
            },
            {
                "id": etl.JACOB_NO_ACCESS_ACCOUNT_ID,
                "accountId": etl.JACOB_NO_ACCESS_ACCOUNT_ID,
                "email": "",
                "displayName": "Jacob No Access",
            },
        ]
    ).to_parquet(input_dir / "users.parquet")
    pd.DataFrame(
        [{"id": "group-eng", "name": "Coveo Demo Engineering"}]
    ).to_parquet(input_dir / "groups.parquet")
    pd.DataFrame(
        [
            {
                "id": "group-eng:" + etl.JACOB_ACCOUNT_ID,
                "groupId": "group-eng",
                "accountId": etl.JACOB_ACCOUNT_ID,
            }
        ]
    ).to_parquet(input_dir / "group_members.parquet")
    monkeypatch.setattr(etl, "ROOT_DIR", str(tmp_path))
    monkeypatch.setattr(etl, "INPUT_DIR", str(input_dir))
    monkeypatch.setattr(etl, "OUTPUT_DIR", str(tmp_path / "etl-output"))
    monkeypatch.setenv("SYNTHETIC_ACL_DEMO", "true")
    monkeypatch.setenv("COVEO_SECURITY_PROVIDER_ID", "Northstar Confluence Demo SIP")
    etl.run()
    messages = [
        json.loads(line) for line in (tmp_path / "etl-output/data.singer").read_text().splitlines()
    ]
    streams = [m["stream"] for m in messages if m["type"] == "SCHEMA"]
    assert streams == ["SecurityIdentities", "Documents"]
    identities = [
        m["record"] for m in messages if m["type"] == "RECORD" and m["stream"] == "SecurityIdentities"
    ]
    documents = [
        m["record"] for m in messages if m["type"] == "RECORD" and m["stream"] == "Documents"
    ]
    assert len(identities) == 3
    assert identities[1]["mappings"][0]["name"] == etl.JACOB_NO_ACCESS_EMAIL
    assert identities[2]["identity"] == {"name": "group-eng", "type": "GROUP"}
    assert identities[2]["members"] == [{"name": etl.JACOB_ACCOUNT_ID, "type": "USER"}]
    assert documents[0]["permissions"] == [
        {
            "allowAnonymous": False,
            "allowedPermissions": [
                {
                    "identity": "group-eng",
                    "identityType": "Group",
                    "securityProvider": "Northstar Confluence Demo SIP",
                }
            ],
        }
    ]


def test_synthetic_acl_demo_rejects_unknown_titles(monkeypatch):
    monkeypatch.setenv("SYNTHETIC_ACL_DEMO", "true")
    with pytest.raises(ValueError, match="unexpected title"):
        etl.transform(
            pd.DataFrame([{**page(), "title": "Not a demo page"}]),
            "pages",
            acl_context={
                "engineering_group_id": "group-eng",
                "provider": "Northstar Confluence Demo SIP",
                "page_titles": {"17": "Not a demo page"},
            },
        )
