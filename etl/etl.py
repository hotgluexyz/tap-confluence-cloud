"""Confluence pages/attachments -> Coveo Documents with auditable permission metadata."""

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


def nested(value, expected_type):
    """Restore structured Parquet values before creating Singer records."""
    if isinstance(value, str):
        value = json.loads(value)
    if hasattr(value, "tolist"):
        value = value.tolist()
    if not isinstance(value, expected_type):
        raise ValueError(f"Expected {expected_type.__name__} source metadata")
    return value


def identity(principal_type, principal_id):
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
    return {"identity": name, "identityType": kinds[principal_type]}


def permissions(space_permissions, restrictions):
    """Coveo simplified permission sets intersect space + every ancestor/page gate."""
    readers = [
        identity(p["principal"]["type"], p["principal"]["id"])
        for p in space_permissions
        if p["operation"] == {"key": "read", "targetType": "space"}
    ]
    if not readers:
        raise ValueError("No readable space permission model; refusing to default to public")
    sets = [{"allowAnonymous": False, "allowedPermissions": readers}]
    for layer in restrictions:
        allowed = [identity("user", user["accountId"]) for user in layer["users"]]
        allowed += [identity("group", group["id"]) for group in layer["groups"]]
        # An empty page restriction means no additional gate, not public access.
        if allowed:
            sets.append({"allowAnonymous": False, "allowedPermissions": allowed})
    return sets


def transform(df: pd.DataFrame, stream: str) -> pd.DataFrame:
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
        permission_sets = permissions(space, restrictions)
        version = nested(source["version"], dict)
        page_id = source_id if stream == "pages" else str(source["pageId"])
        page_url = f"{root}/pages/viewpage.action?pageId={page_id}"
        document = {
            "externalId": f"confluence:{stream}:{source_id}",
            "documentId": page_url
            if stream == "pages"
            else (
                f"{root}/pages/viewpageattachments.action?pageId={page_id}"
                f"&preview=/{page_id}/{source_id.removeprefix('att')}"
            ),
            "title": source["title"],
            "clickableuri": source_url,
            "date": version["createdAt"],
            "createddate": source["createdAt"],
            "objecttype": "ConfluencePage" if stream == "pages" else "ConfluenceAttachment",
            "confluenceid": source_id,
            "confluencespaceid": str(source["spaceId"]),
            "confluenceauthorid": source.get("authorId"),
            "permissions": permission_sets,
            "confluencepermissions": json.dumps(permission_sets, separators=(",", ":")),
            "confluencepermissionmetadata": json.dumps(
                {"space_permissions": space, "read_restrictions": restrictions},
                separators=(",", ":"),
            ),
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


def run() -> None:
    reader = gs.Reader(INPUT_DIR, root=ROOT_DIR)
    frames = []
    for stream in ("pages", "attachments"):
        frame = reader.get(stream, catalog_types=True)
        if frame is None or frame.empty:
            continue
        mapped = transform(frame, stream)
        LOGGER.info("Mapped %s: %d records", stream, len(mapped))
        frames.append(mapped)
    if not frames:
        raise ValueError("No Confluence records to export")
    # One union schema; null content fields are omitted from individual records.
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    output = Path(OUTPUT_DIR) / "data.singer"
    if output.exists():
        output.unlink()
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
    gs.to_singer(
        frame,
        "Documents",
        OUTPUT_DIR,
        keys=["externalId"],
        allow_objects=True,
        schema=schema,
        keep_null_fields=False,
    )


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    run()
