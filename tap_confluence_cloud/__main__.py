"""ConfluenceCloud entry point."""

from __future__ import annotations

from tap_confluence_cloud.tap import TapConfluenceCloud

TapConfluenceCloud.cli()
