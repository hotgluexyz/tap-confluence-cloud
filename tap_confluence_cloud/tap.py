"""Singer tap for Confluence Cloud pages, attachments and access metadata."""

from urllib.parse import urlparse

from hotglue_singer_sdk import Tap
from hotglue_singer_sdk import typing as th

from tap_confluence_cloud.streams import (
    AttachmentsStream,
    GroupMembersStream,
    GroupsStream,
    PagesStream,
    UsersStream,
)


class TapConfluenceCloud(Tap):
    """Read the selected Confluence content with Basic email/API-token authentication."""

    name = "tap-confluence-cloud"
    config_jsonschema = th.PropertiesList(
        th.Property(
            "site_url",
            th.StringType,
            required=True,
            description="HTTPS Confluence site URL, optionally ending in /wiki",
        ),
        th.Property("email", th.StringType, required=True),
        th.Property("api_token", th.StringType, required=True, description="Sensitive API token"),
        th.Property("space_ids", th.ArrayType(th.StringType)),
        th.Property("page_ids", th.ArrayType(th.StringType)),
        th.Property(
            "user_account_ids",
            th.ArrayType(th.StringType),
            description="Atlassian account IDs to extract for security identity sync",
        ),
        th.Property(
            "group_ids",
            th.ArrayType(th.StringType),
            description="Confluence group UUIDs to extract for security identity sync",
        ),
        th.Property(
            "group_names",
            th.ArrayType(th.StringType),
            description="Exact Confluence group names to resolve and extract",
        ),
        th.Property("page_size", th.IntegerType, default=100),
        th.Property("max_attachment_bytes", th.IntegerType, default=10485760),
    ).to_dict()

    def discover_streams(self):
        site = urlparse(self.config["site_url"])
        if (
            site.scheme != "https"
            or not site.netloc
            or site.username
            or site.password
            or site.query
            or site.fragment
            or site.path.rstrip("/") not in ("", "/wiki")
        ):
            raise ValueError("site_url must be an HTTPS site root, optionally ending in /wiki")
        if not 1 <= self.config["page_size"] <= 250:
            raise ValueError("page_size must be between 1 and 250")
        if self.config["max_attachment_bytes"] <= 0:
            raise ValueError("max_attachment_bytes must be positive")
        if (
            len(self.config.get("page_ids", [])) > 250
            or len(self.config.get("space_ids", [])) > 100
        ):
            raise ValueError("Confluence accepts at most 250 page_ids and 100 space_ids")
        return [
            PagesStream(self),
            AttachmentsStream(self),
            UsersStream(self),
            GroupsStream(self),
            GroupMembersStream(self),
        ]


if __name__ == "__main__":
    TapConfluenceCloud.cli()
