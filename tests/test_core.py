"""Exercise the public Singer catalog and sync against Confluence HTTP fixtures."""

import base64
import json
import re
from urllib.parse import parse_qs, urlparse

import pytest
import responses
from hotglue_singer_sdk.testing import tap_sync_test

from tap_confluence_cloud.tap import TapConfluenceCloud

CONFIG = {
    "site_url": "https://example.atlassian.net/wiki",
    "email": "demo@example.test",
    "api_token": "test-token",
    "page_size": 2,
    "page_ids": ["11", "22"],
    "user_account_ids": ["u-alice", "u-bob"],
    "group_names": ["Coveo Demo Engineering"],
}


def api_response(request, *, fail_acl=False):
    """Asymmetric pages, independently paginated ACLs, and an inherited attachment."""
    assert request.headers["Authorization"].startswith("Basic ")
    path = urlparse(request.url).path
    query = parse_qs(urlparse(request.url).query)
    if path == "/wiki/api/v2/pages":
        assert query["body-format"] == ["storage"]
        assert query["id"] == ["11", "22"]
        page_id = "22" if "cursor" in query else "11"
        result = {
            "results": [
                {
                    "id": page_id,
                    "spaceId": "s1",
                    "title": "Page " + page_id,
                    "body": {"storage": {"value": "<p>Body " + page_id + "</p>"}},
                    "_links": {"webui": "/spaces/demo/pages/" + page_id},
                }
            ]
        }
        if page_id == "11":
            result["_links"] = {"next": "/wiki/api/v2/pages?cursor=cursor%2B2"}
    elif path.endswith("/permissions"):
        if fail_acl:
            return 403, {}, '{"message":"Forbidden"}'
        result = {
            "results": [
                {
                    "id": "p1",
                    "principal": {"type": "group", "id": "staff"},
                    "operation": {"key": "read", "targetType": "space"},
                }
            ]
        }
    elif "/restriction/" in path:
        content_id = path.split("/")[5]
        start = int(query["start"][0])
        users = (
            [{"accountId": "u1"}, {"accountId": "u2"}, {"accountId": "u3"}]
            if content_id == "1"
            else []
        )
        groups = [{"id": "engineering", "name": "Engineering"}] if content_id == "22" else []
        result = {
            "restrictions": {
                "user": {"results": users[start : start + 2], "start": start, "limit": 2},
                "group": {"results": groups[start : start + 2], "start": start, "limit": 2},
            }
        }
    elif path.startswith("/wiki/rest/api/content/"):
        result = {
            "ancestors": [{"id": "1"}],
            "history": {"createdBy": {"accountId": "u1", "displayName": "Ada"}},
        }
    elif path.endswith(("/labels", "/properties")):
        result = {"results": []}
    elif path.endswith("/attachments"):
        result = {"results": []}
        if "/22/" in path:
            result["results"] = [
                {
                    "id": "att99",
                    "title": "guide.pdf",
                    "fileSize": 14,
                    "downloadLink": "/download/attachments/22/guide.pdf",
                    "version": {"authorId": "u2"},
                }
            ]
    elif path == "/wiki/download/attachments/22/guide.pdf":
        return 200, {"Content-Type": "application/pdf"}, b"%PDF-1.4\ndemo\n"
    elif path == "/wiki/rest/api/user":
        account_id = query["accountId"][0]
        emails = {"u-alice": "alice@example.test", "u-bob": ""}
        result = {
            "accountId": account_id,
            "email": emails.get(account_id, ""),
            "displayName": account_id,
            "accountStatus": "active",
            "accountType": "atlassian",
        }
    elif path == "/wiki/rest/api/group/picker":
        result = {
            "results": [
                {
                    "id": "group-eng",
                    "name": "Coveo Demo Engineering",
                    "managedBy": "ADMINS",
                    "usageType": "USERBASE_GROUP",
                }
            ]
        }
    elif path.endswith("/membersByGroupId"):
        result = {
            "results": [
                {
                    "accountId": "u-alice",
                    "email": "alice@example.test",
                    "displayName": "Alice",
                    "accountStatus": "active",
                }
            ],
            "start": 0,
            "limit": 2,
            "size": 1,
        }
    else:
        raise AssertionError(f"Unexpected API path {path}")
    return 200, {"Content-Type": "application/json"}, json.dumps(result)


@responses.activate
def test_sync_preserves_content_paginated_restrictions_and_attachment_inheritance():
    responses.add_callback(
        responses.GET, re.compile(r"https://example\.atlassian\.net/.*"), callback=api_response
    )
    tap = TapConfluenceCloud(config=CONFIG)
    catalog = tap.catalog_dict
    assert {s["stream"] for s in catalog["streams"]} == {
        "pages",
        "attachments",
        "users",
        "groups",
        "group_members",
    }
    stdout, _ = tap_sync_test(tap)
    messages = [json.loads(line) for line in stdout.getvalue().splitlines()]
    pages = [m["record"] for m in messages if m["type"] == "RECORD" and m["stream"] == "pages"]
    attachments = [
        m["record"] for m in messages if m["type"] == "RECORD" and m["stream"] == "attachments"
    ]
    users = [m["record"] for m in messages if m["type"] == "RECORD" and m["stream"] == "users"]
    groups = [m["record"] for m in messages if m["type"] == "RECORD" and m["stream"] == "groups"]
    members = [
        m["record"] for m in messages if m["type"] == "RECORD" and m["stream"] == "group_members"
    ]
    assert [p["id"] for p in pages] == ["11", "22"]
    assert pages[1]["body"]["storage"]["value"] == "<p>Body 22</p>"
    assert pages[1]["url"] == "https://example.atlassian.net/wiki/spaces/demo/pages/22"
    assert pages[1]["read_restrictions"][0]["users"] == [
        {"accountId": "u1"},
        {"accountId": "u2"},
        {"accountId": "u3"},
    ]
    assert pages[1]["read_restrictions"][1]["groups"] == [
        {"id": "engineering", "name": "Engineering"}
    ]
    assert len(attachments) == 1
    assert base64.b64decode(attachments[0]["content_base64"]) == b"%PDF-1.4\ndemo\n"
    assert attachments[0]["pageId"] == "22"
    assert attachments[0]["read_restrictions"] == pages[1]["read_restrictions"]
    assert attachments[0]["space_permissions"] == pages[1]["space_permissions"]
    assert attachments[0]["permissions_complete"] is True
    assert [user["accountId"] for user in users] == ["u-alice", "u-bob"]
    assert users[1]["email"] is None
    assert groups == [
        {
            "id": "group-eng",
            "name": "Coveo Demo Engineering",
            "managedBy": "ADMINS",
            "usageType": "USERBASE_GROUP",
        }
    ]
    assert members == [
        {
            "id": "group-eng:u-alice",
            "groupId": "group-eng",
            "groupName": "Coveo Demo Engineering",
            "accountId": "u-alice",
            "email": "alice@example.test",
            "displayName": "Alice",
            "accountStatus": "active",
        }
    ]


@responses.activate
def test_inaccessible_permissions_fail_sync_instead_of_emitting_public_content():
    responses.add_callback(
        responses.GET,
        re.compile(r"https://example\.atlassian\.net/.*"),
        callback=lambda request: api_response(request, fail_acl=True),
    )
    with pytest.raises(Exception, match="403"):
        tap_sync_test(TapConfluenceCloud(config=CONFIG))


@responses.activate
def test_attachment_limit_fails_instead_of_silently_skipping_content():
    responses.add_callback(
        responses.GET, re.compile(r"https://example\.atlassian\.net/.*"), callback=api_response
    )
    with pytest.raises(ValueError, match="exceeds max_attachment_bytes"):
        tap_sync_test(TapConfluenceCloud(config={**CONFIG, "max_attachment_bytes": 13}))
