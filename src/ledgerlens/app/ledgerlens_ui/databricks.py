"""Databricks request and operator identity boundaries."""

import sys
from collections import deque
from dataclasses import dataclass
from urllib.parse import urlsplit

REVIEWER_GROUPS = {
    "dev": "ledgerlens-dev-reviewers",
    "demo": "ledgerlens-demo-reviewers",
}


def log_databricks_error(error, status=None):
    status = status if type(status) is int and 100 <= status <= 599 else "unavailable"
    print(f"Databricks exception={type(error).__name__} http_status={status}", file=sys.stderr)


class Workspace:
    def __init__(self, host):
        url = urlsplit(host)
        if (url.scheme, url.netloc, url.path, url.query, url.fragment) != (
            "https", url.hostname, "", "", "",
        ):
            raise ValueError("Configure an explicit HTTPS Databricks workspace URL.")
        self.host = host
        self.trace = deque(maxlen=32)
        self.last_http_status = None

    def call(self, method, path, token, **kwargs):
        safe_path = path
        if path.startswith("/api/2.0/sql/statements/"):
            safe_path = "/api/2.0/sql/statements/{statement_id}"
            if "/result/chunks/" in path:
                safe_path += "/result/chunks/{index}"
        self.trace.append((f"{method} {safe_path}", None))
        return self._request(method, path, headers={"Authorization": f"Bearer {token}"}, **kwargs)

    def preview_listing(self, path, token, params):
        self.trace.append(("GET /api/2.0/fs/directories/{masked_preview_directory}", None))
        return self._request("GET", f"/api/2.0/fs/directories{path}",
                             headers={"Authorization": f"Bearer {token}"}, params=params, missing_ok=True)

    def preview_pdf(self, path, token):
        self.trace.append(("GET /api/2.0/fs/files/{masked_preview_page}", None))
        return self._request("GET", f"/api/2.0/fs/files{path}",
                             headers={"Authorization": f"Bearer {token}"}, pdf=True, missing_ok=True)

    def reader_token(self, client_id, client_secret):
        path = "/oidc/v1/token"
        self.trace.append((f"POST {path}", None))
        result = self._request("POST", path, auth=(client_id, client_secret),
                               data={"grant_type": "client_credentials", "scope": "all-apis"})
        if not isinstance(result.get("access_token"), str) or not result["access_token"]:
            error = OSError("Databricks reader authentication failed.")
            log_databricks_error(error, self.last_http_status)
            raise error
        return result["access_token"]

    def _request(self, method, path, *, missing_ok=False, pdf=False, timeout=(5, 15), **kwargs):
        import requests

        self.last_http_status = None
        try:
            with requests.Session() as session:
                session.trust_env = False
                with session.request(method, self.host + path, timeout=timeout, allow_redirects=False,
                                     stream=pdf, **kwargs) as response:
                    self.last_http_status = response.status_code
                    if response.status_code == 404 and missing_ok:
                        return None
                    if response.status_code in (401, 403):
                        raise PermissionError("Databricks denied the request.")
                    if not 200 <= response.status_code < 300:
                        raise OSError("Databricks did not complete the request.")
                    if pdf:
                        content = bytearray()
                        for chunk in response.iter_content(chunk_size=65_536):
                            content.extend(chunk)
                            if len(content) > 8 * 1024 * 1024:
                                raise OSError("Databricks preview exceeds the page size limit.")
                        if not content.startswith(b"%PDF-"):
                            raise OSError("Databricks returned an invalid preview.")
                        return bytes(content)
                    payload = response.json()
                    if not isinstance(payload, dict):
                        raise OSError("Databricks returned an invalid response.")
                    return payload
        except (requests.RequestException, ValueError) as error:
            error_response = getattr(error, "response", None)
            status = getattr(error_response, "status_code", None)
            log_databricks_error(error, status if status is not None else self.last_http_status)
            raise OSError("Databricks did not complete the request.") from None
        except OSError as error:
            log_databricks_error(error, self.last_http_status)
            raise


@dataclass(frozen=True)
class OperatorIdentity:
    actor_id: str


def authenticate_operator(workspace, environment, expected_actor_id, token):
    """Resolve the actual delegated identity and require the contract reviewer group."""
    group_name = REVIEWER_GROUPS.get(environment)
    if not token or not expected_actor_id or group_name is None:
        return None
    try:
        user = workspace.call("GET", "/api/2.0/preview/scim/v2/Me", token)
    except OSError:
        return None
    if not isinstance(user, dict) or user.get("active") is not True or user.get("id") != expected_actor_id:
        return None
    groups = user.get("groups")
    if not isinstance(groups, list):
        return None
    if any(isinstance(group, dict) and group.get("display") == group_name for group in groups):
        return OperatorIdentity(expected_actor_id)
    return None
