"""Confluence HTTP transport, cursor pagination and auxiliary requests."""

from urllib.parse import parse_qs, urljoin, urlparse

from hotglue_singer_sdk.authenticators import BasicAuthenticator
from hotglue_singer_sdk.exceptions import RetriableAPIError
from hotglue_singer_sdk.streams import RESTStream


class ConfluenceCloudStream(RESTStream):
    """Read Confluence Cloud with the same credentials for v2 and v1 APIs."""

    records_jsonpath = "$.results[*]"

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.primary_keys = ["id"]

    @property
    def site_url(self):
        return self.config["site_url"].rstrip("/").removesuffix("/wiki")

    @property
    def url_base(self):
        return self.site_url + "/wiki/api/v2"

    @property
    def authenticator(self):
        return BasicAuthenticator.create_for_stream(
            self, username=self.config["email"], password=self.config["api_token"]
        )

    def validate_response(self, response):
        if response.status_code == 429:
            raise RetriableAPIError("Confluence rate limit; retrying")
        super().validate_response(response)

    def get_next_page_token(self, response, previous_token):
        next_url = response.json().get("_links", {}).get("next")
        next_url = next_url or response.links.get("next", {}).get("url")
        if not next_url:
            return None
        cursor = parse_qs(urlparse(next_url).query).get("cursor", [None])[0]
        if not cursor or cursor == previous_token:
            raise ValueError("Confluence returned a missing or repeated pagination cursor")
        return cursor

    def get_url_params(self, context, next_page_token):
        params = {"limit": self.config["page_size"], "status": "current"}
        if next_page_token:
            params["cursor"] = next_page_token
        return params

    def absolute_url(self, path):
        """Only attach source credentials to this Confluence site's URLs."""
        # Confluence content links such as /spaces/... are relative to _links.base,
        # which includes /wiki, unlike the fully qualified API pagination paths.
        if path.startswith("/") and not path.startswith(("/wiki/", "//")):
            path = "/wiki" + path
        url = urljoin(self.site_url + "/wiki/", path)
        if (
            urlparse(url).scheme != "https"
            or urlparse(url).netloc != urlparse(self.site_url).netloc
        ):
            raise ValueError("Refusing a non-HTTPS or cross-origin Confluence URL")
        return url

    def get_response(self, path, params=None):
        request = self.build_prepared_request(
            method="GET",
            url=self.absolute_url(path),
            params=params,
            headers={"Accept": "application/json"},
        )
        return self.request_decorator(self._request)(request, None)

    def read_collection(self, path, params=None):
        """Follow paginated auxiliary collections without leaking credentials."""
        seen = set()
        while path:
            response = self.get_response(path, params)
            data = response.json()
            yield from data["results"]
            path = data.get("_links", {}).get("next")
            path = path or response.links.get("next", {}).get("url")
            if path and path in seen:
                raise ValueError("Confluence repeated an auxiliary pagination link")
            seen.add(path)
            params = None
