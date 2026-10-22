"""Pages, attachments, and identity streams for security sync."""

import base64
from typing import Any, ClassVar, cast
from urllib.parse import quote

from tap_confluence_cloud.client import ConfluenceCloudStream


def content_schema(extra):
    """Preserve source objects rather than flattening away security metadata."""
    strings = ["id", "title", "status", "createdAt", "url", "spaceId", "authorId"]
    objects = ["version", "_links", "author"]
    return {
        "type": "object",
        "properties": {
            **{key: {"type": ["string", "null"]} for key in strings},
            **{key: {"type": ["object", "null"], "additionalProperties": True} for key in objects},
            "read_restrictions": {
                "type": "array",
                "items": {"type": "object", "additionalProperties": True},
            },
            "space_permissions": {
                "type": "array",
                "items": {"type": "object", "additionalProperties": True},
            },
            "permissions_complete": {"type": "boolean"},
            **extra,
        },
    }


class PagesStream(ConfluenceCloudStream):
    """Full scans deliberately refresh permissions even when page bodies are unchanged."""

    name = "pages"
    path = "/pages"
    schema = content_schema(
        {
            "body": {"type": "object", "additionalProperties": True},
            "parentId": {"type": ["string", "null"]},
            "parentType": {"type": ["string", "null"]},
            "ownerId": {"type": ["string", "null"]},
            "labels": {"type": "array", "items": {"type": "object", "additionalProperties": True}},
            "properties": {
                "type": "array",
                "items": {"type": "object", "additionalProperties": True},
            },
        }
    )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._space_permissions = {}
        self._restrictions = {}

    def get_url_params(self, context, next_page_token):
        params = super().get_url_params(context, next_page_token)
        params["body-format"] = "storage"
        if self.config.get("space_ids"):
            params["space-id"] = self.config["space_ids"]
        if self.config.get("page_ids"):
            params["id"] = self.config["page_ids"]
        return params

    def read_restrictions(self, content_id):
        """Paginate both user and group restrictions using the v1 offset API."""
        if content_id in self._restrictions:
            return self._restrictions[content_id]
        result = {"contentId": content_id, "users": [], "groups": []}
        start = 0
        while True:
            data = self.get_response(
                f"/wiki/rest/api/content/{quote(content_id, safe='')}/restriction/byOperation/read",
                {"start": start, "limit": self.config["page_size"]},
            ).json()["restrictions"]
            sizes = []
            for source, dest in [("user", "users"), ("group", "groups")]:
                chunk = data[source]
                result[dest].extend(chunk["results"])
                sizes.append((len(chunk["results"]), chunk["limit"]))
            # A full result page may have more records even without a next link.
            if all(size < limit for size, limit in sizes):
                break
            step = min(limit for _, limit in sizes)
            if step <= 0:
                raise ValueError("Invalid restriction pagination limit")
            start += step
        self._restrictions[content_id] = result
        return result

    def post_process(self, row, context=None):
        page_id = quote(row["id"], safe="")
        space_id = row["spaceId"]
        detail = self.get_response(
            f"/wiki/rest/api/content/{page_id}", {"expand": "ancestors,history.createdBy"}
        ).json()
        if space_id not in self._space_permissions:
            self._space_permissions[space_id] = list(
                self.read_collection(
                    f"/wiki/api/v2/spaces/{quote(space_id, safe='')}/permissions",
                    {"limit": self.config["page_size"]},
                )
            )
        row["space_permissions"] = self._space_permissions[space_id]
        row["read_restrictions"] = [
            self.read_restrictions(item["id"]) for item in [*detail["ancestors"], row]
        ]
        row["author"] = detail["history"]["createdBy"]
        row["url"] = self.absolute_url(row["_links"]["webui"])
        row["labels"] = list(self.read_collection(f"/wiki/api/v2/pages/{page_id}/labels"))
        row["properties"] = list(self.read_collection(f"/wiki/api/v2/pages/{page_id}/properties"))
        row["permissions_complete"] = True
        return row

    def get_child_context(self, record, context):
        metadata = {
            key: record[key]
            for key in (
                "id",
                "spaceId",
                "read_restrictions",
                "space_permissions",
                "permissions_complete",
            )
        }
        # SDK contexts are logged and persisted in state. Keep ACLs out of them.
        for child in self.child_streams:
            cast(AttachmentsStream, child).page_metadata = metadata
        return {"id": record["id"]}


class AttachmentsStream(ConfluenceCloudStream):
    """Download page attachment bytes and retain the containing page's permissions."""

    name = "attachments"
    parent_stream_type = PagesStream
    path = "/pages/{id}/attachments"
    page_metadata: dict[str, Any]
    schema = content_schema(
        {
            "pageId": {"type": "string"},
            "mediaType": {"type": ["string", "null"]},
            "fileSize": {"type": "integer"},
            "downloadLink": {"type": "string"},
            "webuiLink": {"type": ["string", "null"]},
            "comment": {"type": ["string", "null"]},
            "content_base64": {"type": "string"},
        }
    )

    def post_process(self, row, context=None):
        limit = self.config["max_attachment_bytes"]
        if row["fileSize"] > limit:
            raise ValueError(f"Attachment {row['id']} exceeds max_attachment_bytes")
        response = self.get_response(row["downloadLink"])
        if len(response.content) > limit:
            raise ValueError(f"Attachment {row['id']} download exceeds max_attachment_bytes")
        if len(response.content) != row["fileSize"]:
            raise ValueError(f"Attachment {row['id']} download size does not match metadata")
        row["content_base64"] = base64.b64encode(response.content).decode("ascii")
        page_id = self.page_metadata["id"]
        row["pageId"] = page_id
        for key in ("spaceId", "read_restrictions", "space_permissions", "permissions_complete"):
            row[key] = self.page_metadata[key]
        row["url"] = self.absolute_url(
            f"/wiki/pages/viewpageattachments.action?pageId={page_id}"
            f"&preview=/{page_id}/{row['id'].removeprefix('att')}"
        )
        row["authorId"] = row.get("version", {}).get("authorId")
        return row


class UsersStream(ConfluenceCloudStream):
    """Fetch configured Atlassian users by account ID for Coveo identity aliases."""

    name = "users"
    path = "/users"
    schema = {
        "type": "object",
        "properties": {
            "id": {"type": "string"},
            "accountId": {"type": "string"},
            "email": {"type": ["string", "null"]},
            "displayName": {"type": ["string", "null"]},
            "accountStatus": {"type": ["string", "null"]},
            "accountType": {"type": ["string", "null"]},
        },
    }

    def get_records(self, context):
        for account_id in self.config.get("user_account_ids", []):
            user = self.get_response(
                "/wiki/rest/api/user", {"accountId": account_id}
            ).json()
            yield {
                "id": user["accountId"],
                "accountId": user["accountId"],
                "email": user.get("email") or None,
                "displayName": user.get("displayName"),
                "accountStatus": user.get("accountStatus"),
                "accountType": user.get("accountType"),
            }


class GroupsStream(ConfluenceCloudStream):
    """Resolve configured Confluence groups by ID or exact name."""

    name = "groups"
    path = "/groups"
    schema = {
        "type": "object",
        "properties": {
            "id": {"type": "string"},
            "name": {"type": "string"},
            "managedBy": {"type": ["string", "null"]},
            "usageType": {"type": ["string", "null"]},
        },
    }

    def _groups(self):
        seen = {}
        for group_id in self.config.get("group_ids", []):
            seen[group_id] = {"id": group_id, "name": None}
        for name in self.config.get("group_names", []):
            matches = [
                group
                for group in self.get_response(
                    "/wiki/rest/api/group/picker", {"query": name}
                ).json()["results"]
                if group["name"] == name
            ]
            if not matches:
                raise ValueError(f"Confluence group not found by name: {name}")
            group = matches[0]
            seen[group["id"]] = {
                "id": group["id"],
                "name": group["name"],
                "managedBy": group.get("managedBy"),
                "usageType": group.get("usageType"),
            }
        for group_id, partial in list(seen.items()):
            if partial.get("name"):
                continue
            matches = [
                group
                for group in self.get_response(
                    "/wiki/rest/api/group/picker", {"query": group_id}
                ).json()["results"]
                if group["id"] == group_id
            ]
            if not matches:
                raise ValueError(f"Confluence group not found by id: {group_id}")
            group = matches[0]
            seen[group_id] = {
                "id": group["id"],
                "name": group["name"],
                "managedBy": group.get("managedBy"),
                "usageType": group.get("usageType"),
            }
        return list(seen.values())

    def get_records(self, context):
        yield from self._groups()

    def get_child_context(self, record, context):
        return {"id": record["id"], "name": record["name"]}


class GroupMembersStream(ConfluenceCloudStream):
    """Live membership for each configured group (full replace on each sync)."""

    name = "group_members"
    parent_stream_type = GroupsStream
    path = "/group-members"
    primary_keys: ClassVar[list[str]] = ["groupId", "accountId"]
    schema = {
        "type": "object",
        "properties": {
            "id": {"type": "string"},
            "groupId": {"type": "string"},
            "groupName": {"type": ["string", "null"]},
            "accountId": {"type": "string"},
            "email": {"type": ["string", "null"]},
            "displayName": {"type": ["string", "null"]},
            "accountStatus": {"type": ["string", "null"]},
        },
    }

    def get_records(self, context):
        group_id = context["id"]
        start = 0
        limit = self.config["page_size"]
        while True:
            response = self.get_response(
                f"/wiki/rest/api/group/{quote(group_id, safe='')}/membersByGroupId",
                {"start": start, "limit": limit},
            )
            if response.status_code in (401, 403):
                raise ValueError(
                    "Confluence denied group membership extraction for "
                    f"{group_id}; Atlassian Free/admin permissions may block "
                    "membersByGroupId. Refusing to simulate membership."
                )
            data = response.json()
            results = data["results"]
            for member in results:
                account_id = member["accountId"]
                yield {
                    "id": f"{group_id}:{account_id}",
                    "groupId": group_id,
                    "groupName": context.get("name"),
                    "accountId": account_id,
                    "email": member.get("email") or None,
                    "displayName": member.get("displayName"),
                    "accountStatus": member.get("accountStatus"),
                }
            if len(results) < limit:
                break
            start += limit
