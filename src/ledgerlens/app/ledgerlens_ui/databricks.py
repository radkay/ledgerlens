"""Databricks request and operator identity boundaries."""

from dataclasses import dataclass
from urllib.parse import urlsplit

REVIEWER_GROUPS = {
    "dev": "ledgerlens-dev-reviewers",
    "demo": "ledgerlens-demo-reviewers",
}


class Workspace:
    def __init__(self, host):
        url = urlsplit(host)
        if (url.scheme, url.netloc, url.path, url.query, url.fragment) != (
            "https", url.hostname, "", "", "",
        ):
            raise ValueError("Configure an explicit HTTPS Databricks workspace URL.")
        self.host = host

    def call(self, method, path, token, **kwargs):
        import requests

        try:
            with requests.Session() as session:
                session.trust_env = False
                response = session.request(
                    method, self.host + path,
                    headers={"Authorization": f"Bearer {token}"},
                    timeout=(5, 15), allow_redirects=False, **kwargs,
                )
            if response.status_code in (401, 403):
                raise PermissionError("Databricks denied the request.")
            if not 200 <= response.status_code < 300:
                raise OSError("Databricks did not complete the request.")
            return response.json()
        except (requests.RequestException, ValueError):
            raise OSError("Databricks did not complete the request.") from None


@dataclass(frozen=True)
class OperatorIdentity:
    actor_id: str


def authenticate_operator(workspace, environment, expected_actor_id, token):
    """Resolve the actual delegated identity and require the contract reviewer group."""
    if not token or not expected_actor_id:
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
    if any(isinstance(group, dict) and group.get("display") == REVIEWER_GROUPS[environment] for group in groups):
        return OperatorIdentity(expected_actor_id)
    return None
