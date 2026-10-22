"""Confluence pages/attachments -> Coveo Documents with auditable permission metadata.

Opt-in `SYNTHETIC_ACL_DEMO=true` applies Northstar demo ACLs and emits SecurityIdentities.
That mode is demo-only and must never be the production default.
"""

import json
import logging
import os
from pathlib import Path
from urllib.parse import urlsplit

import gluestick as gs
import pandas as pd

ROOT_DIR = os.environ.get("ROOT_DIR", ".")
INPUT_DIR = f"{ROOT_DIR}/sync-output"
OUTPUT_DIR = f"{ROOT_DIR}/etl-output"
LOGGER = logging.getLogger(__name__)

# Northstar demo principals (Atlassian account IDs). Used only when SYNTHETIC_ACL_DEMO is on.
JACOB_ACCOUNT_ID = "712020:38b2e869-d74f-42e9-ae91-f08f58943928"
JACOB_EMAIL = "jacob@hotglue.io"
JACOB_NO_ACCESS_ACCOUNT_ID = "712020:31474388-acce-460e-8e6d-4e4f739b8193"
JACOB_NO_ACCESS_EMAIL = "collegejakemaz@gmail.com"
DEMO_EMAIL_BY_ACCOUNT = {
    JACOB_ACCOUNT_ID: JACOB_EMAIL,
    JACOB_NO_ACCESS_ACCOUNT_ID: JACOB_NO_ACCESS_EMAIL,
}
ENGINEERING_GROUP_NAME = "Coveo Demo Engineering"
DEMO_TITLE_PREFIX = "[Coveo Demo] "

# Title suffix -> both | engineering | jacob
DEMO_ACL_BY_TITLE = {
    "Knowledge Hub": "both",
    "Resolve SAML audience mismatch": "both",
    "Atlas API ingestion technical guide": "engineering",
    "Priority-one incident response SOP": "both",
    "Restricted engineering operations": "jacob",
    "Atlas disaster recovery runbook": "jacob",
    "Support agent onboarding wiki": "both",
    "Knowledge quality and search relevance guide": "engineering",
}


def _demo_sidecar() -> dict:
    path = Path(__file__).with_name("synthetic_acl_demo.json")
    if not path.exists():
        return {}
    return json.loads(path.read_text())


def synthetic_acl_demo_enabled() -> bool:
    env = os.environ.get("SYNTHETIC_ACL_DEMO", "").strip().lower()
    if env in {"1", "true", "yes"}:
        return True
    if env in {"0", "false", "no"}:
        return False
    return _demo_sidecar().get("enabled") is True


def security_provider_id() -> str:
    env = os.environ.get("COVEO_SECURITY_PROVIDER_ID", "").strip()
    if env:
        return env
    return str(
        _demo_sidecar().get("security_provider_id") or "Northstar Confluence Demo SIP"
    ).strip()


def nested(value, expected_type):
    """Restore structured Parquet values before creating Singer records."""
    if isinstance(value, str):
        value = json.loads(value)
    if hasattr(value, "tolist"):
        value = value.tolist()
    if not isinstance(value, expected_type):
        raise ValueError(f"Expected {expected_type.__name__} source metadata")
    return value


def identity(principal_type, principal_id, *, provider=None):
    """Keep immutable Atlassian IDs; access classes are NOT anonymous access."""
    kinds = {
        "user": "User",
        "group": "Group",
        "role": "VirtualGroup",
        "access-class": "VirtualGroup",
    }
    if principal_type not in kinds or not principal_id:
        raise ValueError("Unsupported or missing Confluence permission principal")
    name = str(principal_id)
    if principal_type in ("role", "access-class"):
        name = f"confluence:{principal_type}:{name}"
    entry = {"identity": name, "identityType": kinds[principal_type]}
    if provider:
        entry["securityProvider"] = provider
    return entry


def permissions(space_permissions, restrictions, *, provider=None):
    """Coveo simplified permission sets intersect space + every ancestor/page gate."""
    readers = [
        identity(p["principal"]["type"], p["principal"]["id"], provider=provider)
        for p in space_permissions
        if p["operation"] == {"key": "read", "targetType": "space"}
    ]
    if not readers:
        raise ValueError("No readable space permission model; refusing to default to public")
    sets = [{"allowAnonymous": False, "allowedPermissions": readers}]
    for layer in restrictions:
        allowed = [
            identity("user", user["accountId"], provider=provider) for user in layer["users"]
        ]
        allowed += [
            identity("group", group["id"], provider=provider) for group in layer["groups"]
        ]
        # An empty page restriction means no additional gate, not public access.
        if allowed:
            sets.append({"allowAnonymous": False, "allowedPermissions": allowed})
    return sets


def demo_title_key(title: str) -> str:
    if title.startswith(DEMO_TITLE_PREFIX):
        return title[len(DEMO_TITLE_PREFIX) :]
    return title


def synthetic_permissions(title: str, engineering_group_id: str, provider: str) -> list[dict]:
    """Northstar demo ACL matrix. Not derived from Confluence page restrictions."""
    policy = DEMO_ACL_BY_TITLE.get(demo_title_key(title))
    if policy is None:
        raise ValueError(
            f"synthetic_acl_demo refused unexpected title {title!r}; "
            "only known Northstar demo pages are allowed"
        )
    if policy == "both":
        allowed = [
            identity("user", JACOB_ACCOUNT_ID, provider=provider),
            identity("user", JACOB_NO_ACCESS_ACCOUNT_ID, provider=provider),
        ]
    elif policy == "engineering":
        allowed = [identity("group", engineering_group_id, provider=provider)]
    elif policy == "jacob":
        allowed = [identity("user", JACOB_ACCOUNT_ID, provider=provider)]
    else:
        raise ValueError(f"Unknown demo ACL policy {policy!r}")
    return [{"allowAnonymous": False, "allowedPermissions": allowed}]


def transform(df: pd.DataFrame, stream: str, *, acl_context: dict | None = None) -> pd.DataFrame:
    if stream not in ("pages", "attachments"):
        raise ValueError(f"Unexpected input stream: {stream}")
    documents = []
    for source in df.to_dict(orient="records"):
        complete = source.get("permissions_complete")
        if complete is not True and not (isinstance(complete, str) and complete == "true"):
            raise ValueError("Incomplete source permission metadata")
        source_id = str(source["id"])
        source_url = source["url"]
        root = f"https://{urlsplit(source_url).netloc}/wiki"
        if urlsplit(source_url).scheme != "https" or not urlsplit(source_url).netloc:
            raise ValueError("Source content needs an absolute HTTPS URL")
        space = nested(source["space_permissions"], list)
        restrictions = nested(source["read_restrictions"], list)
        version = nested(source["version"], dict)
        page_id = source_id if stream == "pages" else str(source["pageId"])
        page_url = f"{root}/pages/viewpage.action?pageId={page_id}"
        title = source["title"]
        if acl_context is not None:
            page_title = (
                title
                if stream == "pages"
                else acl_context["page_titles"].get(page_id)
            )
            if not page_title:
                raise ValueError(f"synthetic_acl_demo missing page title for pageId={page_id}")
            permission_sets = synthetic_permissions(
                page_title, acl_context["engineering_group_id"], acl_context["provider"]
            )
            permission_metadata = {
                "mode": "synthetic_acl_demo",
                "policy_title": demo_title_key(page_title),
            }
        else:
            permission_sets = permissions(space, restrictions)
            permission_metadata = {
                "space_permissions": space,
                "read_restrictions": restrictions,
            }
        document = {
            "externalId": f"confluence:{stream}:{source_id}",
            "documentId": page_url
            if stream == "pages"
            else (
                f"{root}/pages/viewpageattachments.action?pageId={page_id}"
                f"&preview=/{page_id}/{source_id.removeprefix('att')}"
            ),
            "title": title,
            "clickableuri": source_url,
            "date": version["createdAt"],
            "createddate": source["createdAt"],
            "objecttype": "ConfluencePage" if stream == "pages" else "ConfluenceAttachment",
            "confluenceid": source_id,
            "confluencespaceid": str(source["spaceId"]),
            "confluenceauthorid": source.get("authorId"),
            "permissions": permission_sets,
            "confluencepermissions": json.dumps(permission_sets, separators=(",", ":")),
            "confluencepermissionmetadata": json.dumps(permission_metadata, separators=(",", ":")),
        }
        if stream == "pages":
            body = nested(source["body"], dict)["storage"]["value"]
            author = nested(source["author"], dict)
            properties = nested(source["properties"], list)
            document.update(
                {
                    "data": body,
                    "fileExtension": ".html",
                    "filetype": "html",
                    "author": author.get("displayName") or source["authorId"],
                    "confluencelabels": [label["name"] for label in nested(source["labels"], list)],
                    "confluenceproperties": json.dumps(properties, separators=(",", ":")),
                }
            )
        else:
            extension = Path(source["title"]).suffix.lower()
            if not extension:
                raise ValueError("Attachment filename has no extension")
            document.update(
                {
                    "compressedBinaryData": source["content_base64"],
                    "compressionType": "UNCOMPRESSED",
                    "fileExtension": extension,
                    "filetype": extension.lstrip("."),
                    "parentId": page_url,
                    "author": source.get("authorId"),
                    "confluencemediatype": source["mediaType"],
                }
            )
        documents.append(document)
    result = pd.DataFrame(documents)
    if not result.empty and result["externalId"].duplicated().any():
        raise ValueError("Duplicate source identifiers in export")
    return result


def _frame(reader, stream: str) -> pd.DataFrame | None:
    frame = reader.get(stream, catalog_types=True)
    if frame is None or frame.empty:
        return None
    return frame


def build_security_identities(reader, *, require_demo_users: bool = False):
    """Emit Coveo SIP user aliases and group membership from live tap streams."""
    users = _frame(reader, "users")
    groups = _frame(reader, "groups")
    members = _frame(reader, "group_members")
    if users is None and groups is None:
        return None, None
    if users is None:
        raise ValueError("groups stream present but users stream is empty")
    if groups is None:
        raise ValueError("users stream present but groups stream is empty")
    if members is None:
        members = pd.DataFrame(columns=["groupId", "accountId"])

    records = []
    seen_users = set()
    for user in users.to_dict(orient="records"):
        account_id = str(user["accountId"])
        email = (user.get("email") or DEMO_EMAIL_BY_ACCOUNT.get(account_id) or "").strip()
        if not email:
            raise ValueError(
                f"User {account_id} has no email from Confluence and no demo override"
            )
        seen_users.add(account_id)
        records.append(
            {
                "externalId": f"confluence:user:{account_id}",
                "identity": {"name": account_id, "type": "USER"},
                "mappings": [
                    {
                        "name": email,
                        "type": "USER",
                        "provider": "Email Security Provider",
                    }
                ],
            }
        )

    if require_demo_users:
        for required_id in DEMO_EMAIL_BY_ACCOUNT:
            if required_id not in seen_users:
                raise ValueError(f"synthetic_acl_demo missing required user {required_id}")

    engineering = None
    for group in groups.to_dict(orient="records"):
        if group.get("name") == ENGINEERING_GROUP_NAME:
            engineering = group
            break
    if engineering is None and require_demo_users:
        raise ValueError(f"synthetic_acl_demo missing group {ENGINEERING_GROUP_NAME!r}")

    engineering_group_id = None
    if engineering is not None:
        engineering_group_id = str(engineering["id"])
        group_member_ids = sorted(
            {
                str(row["accountId"])
                for row in members.to_dict(orient="records")
                if str(row["groupId"]) == engineering_group_id
            }
        )
        records.append(
            {
                "externalId": f"confluence:group:{engineering_group_id}",
                "identity": {"name": engineering_group_id, "type": "GROUP"},
                "members": [
                    {"name": account_id, "type": "USER"} for account_id in group_member_ids
                ],
            }
        )
    else:
        # Sync every configured group with its live membership.
        for group in groups.to_dict(orient="records"):
            group_id = str(group["id"])
            group_member_ids = sorted(
                {
                    str(row["accountId"])
                    for row in members.to_dict(orient="records")
                    if str(row["groupId"]) == group_id
                }
            )
            records.append(
                {
                    "externalId": f"confluence:group:{group_id}",
                    "identity": {"name": group_id, "type": "GROUP"},
                    "members": [
                        {"name": account_id, "type": "USER"} for account_id in group_member_ids
                    ],
                }
            )

    return pd.DataFrame(records), engineering_group_id


def _identity_schema() -> dict:
    return {
        "type": "object",
        "properties": {
            "externalId": {"type": "string"},
            "identity": {"type": "object", "additionalProperties": True},
            "members": {
                "type": ["array", "null"],
                "items": {"type": "object", "additionalProperties": True},
            },
            "mappings": {
                "type": ["array", "null"],
                "items": {"type": "object", "additionalProperties": True},
            },
        },
    }


def _write_singer(frame: pd.DataFrame, stream: str, schema: dict) -> None:
    gs.to_singer(
        frame,
        stream,
        OUTPUT_DIR,
        keys=["externalId"],
        allow_objects=True,
        schema=schema,
        keep_null_fields=False,
    )


def run() -> None:
    reader = gs.Reader(INPUT_DIR, root=ROOT_DIR)
    demo = synthetic_acl_demo_enabled()
    provider = security_provider_id()
    acl_context = None
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    output = Path(OUTPUT_DIR) / "data.singer"
    if output.exists():
        output.unlink()

    # Always sync identities when tap streams are present (needed for Coveo trimming).
    identities, engineering_group_id = build_security_identities(
        reader, require_demo_users=demo
    )
    if identities is not None:
        LOGGER.info("Mapped SecurityIdentities: %d records", len(identities))
        _write_singer(identities, "SecurityIdentities", _identity_schema())

    if demo:
        LOGGER.warning(
            "SYNTHETIC_ACL_DEMO enabled: document ACLs are demo policies, "
            "not live Confluence page restrictions"
        )
        if engineering_group_id is None:
            raise ValueError("synthetic_acl_demo requires Coveo Demo Engineering group")
        pages = _frame(reader, "pages")
        if pages is None:
            raise ValueError("No Confluence pages to export")
        page_titles = {str(row["id"]): row["title"] for row in pages.to_dict(orient="records")}
        acl_context = {
            "engineering_group_id": engineering_group_id,
            "provider": provider,
            "page_titles": page_titles,
        }

    frames = []
    for stream in ("pages", "attachments"):
        frame = _frame(reader, stream)
        if frame is None:
            continue
        if demo:
            mapped = transform(frame, stream, acl_context=acl_context)
        else:
            # Live Confluence restrictions/space perms, tagged with Coveo SIP name.
            mapped = transform(frame, stream, acl_context=None)
            if not mapped.empty:
                mapped = mapped.copy()
                mapped["permissions"] = mapped["permissions"].map(
                    lambda sets: [
                        {
                            **permission_set,
                            "allowedPermissions": [
                                {**identity, "securityProvider": provider}
                                for identity in permission_set.get("allowedPermissions", [])
                            ],
                        }
                        for permission_set in sets
                    ]
                )
                mapped["confluencepermissions"] = mapped["permissions"].map(
                    lambda sets: json.dumps(sets, separators=(",", ":"))
                )
        LOGGER.info("Mapped %s: %d records", stream, len(mapped))
        frames.append(mapped)
    if not frames:
        raise ValueError("No Confluence records to export")
    frame = pd.concat(frames, ignore_index=True)
    schema = {
        "type": "object",
        "properties": {
            **{key: {"type": ["string", "null"]} for key in frame.columns},
            "permissions": {
                "type": "array",
                "items": {"type": "object", "additionalProperties": True},
            },
            "confluencelabels": {
                "type": ["array", "null"],
                "items": {"type": "string"},
            },
        },
    }
    _write_singer(frame, "Documents", schema)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    run()
