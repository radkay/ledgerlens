"""Explicit Databricks connection settings and bounded reader requests."""

import base64
from collections.abc import Mapping
from dataclasses import dataclass, field
import json
import re
from threading import Lock
from time import monotonic
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode, urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener

from .gold import CATALOGS, FETCH_LIMIT

HOSTED_ENDPOINT = "databricks-gpt-oss-20b"
RESPONSE_LIMIT = 1_048_576


def workspace_origin(host: str) -> str:
    parsed = urlsplit(host)
    if (
        parsed.scheme != "https" or not parsed.hostname or parsed.netloc != parsed.hostname
        or parsed.path not in ("", "/") or parsed.query or parsed.fragment
        or not re.fullmatch(r"[a-z0-9][a-z0-9.-]+", parsed.hostname)
    ):
        raise ValueError("Configure an HTTPS Databricks workspace origin.")
    return f"https://{parsed.hostname}"


@dataclass(frozen=True)
class ReaderConfig:
    host: str
    catalog: str
    warehouse: str
    client_id: str
    client_secret: str = field(repr=False)

    def __post_init__(self):
        object.__setattr__(self, "host", workspace_origin(self.host))
        if self.catalog not in CATALOGS or not re.fullmatch(r"[a-f0-9]{16}", self.warehouse):
            raise ValueError("Configure a contracted catalog and an explicit SQL warehouse ID.")
        if not self.client_id.strip() or not self.client_secret.strip():
            raise ValueError("Configure the Databricks reader identity.")

    @classmethod
    def from_settings(cls, settings: Mapping[str, str]) -> "ReaderConfig":
        return cls(
            host=settings.get("DATABRICKS_HOST", ""),
            catalog=settings.get("LEDGERLENS_CATALOG", ""),
            warehouse=settings.get("DATABRICKS_WAREHOUSE_ID", ""),
            client_id=settings.get("DATABRICKS_READER_CLIENT_ID", ""),
            client_secret=settings.get("DATABRICKS_READER_CLIENT_SECRET", ""),
        )


@dataclass(frozen=True)
class RuntimeConfig:
    reader: ReaderConfig
    endpoint: str

    def __post_init__(self):
        if self.endpoint != HOSTED_ENDPOINT:
            raise ValueError(f"Configure LEDGERLENS_MODEL_ENDPOINT={HOSTED_ENDPOINT}.")

    @classmethod
    def from_settings(cls, settings: Mapping[str, str]) -> "RuntimeConfig":
        return cls(ReaderConfig.from_settings(settings), settings.get("LEDGERLENS_MODEL_ENDPOINT", ""))


class DatabricksError(OSError):
    """A workspace request failed without exposing request headers or response data."""


def _error_code(payload) -> str:
    code = payload.get("error_code") if isinstance(payload, dict) else None
    return f", {code}" if isinstance(code, str) and re.fullmatch(r"[A-Z][A-Z0-9_]{1,79}", code) else ""


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


_OPENER = build_opener(NoRedirect())


def _post(url: str, headers: dict, body: bytes, *, timeout: int) -> dict:
    try:
        with _OPENER.open(Request(url, data=body, headers=headers, method="POST"), timeout=timeout) as response:
            raw = response.read(RESPONSE_LIMIT + 1)
        if len(raw) > RESPONSE_LIMIT:
            raise DatabricksError("Databricks response exceeded the byte limit.")
        payload = json.loads(raw)
    except HTTPError as error:
        with error:
            try:
                failure = json.loads(error.read(4096))
            except (ValueError, OSError):
                failure = {}
        code = _error_code(failure)
        raise DatabricksError(f"Databricks request failed (HTTP {error.code}{code}).") from None
    except (URLError, TimeoutError, ValueError):
        raise DatabricksError("Databricks request failed or returned an invalid response.") from None
    if not isinstance(payload, dict):
        raise DatabricksError("Databricks returned an invalid response.")
    return payload


def oauth_token(host: str, client_id: str, secret: str, *, scope: str) -> tuple[str, int]:
    if not client_id.strip() or not secret.strip():
        raise ValueError("Configure the requested Databricks service principal.")
    basic = base64.b64encode(f"{client_id}:{secret}".encode()).decode("ascii")
    result = _post(
        workspace_origin(host) + "/oidc/v1/token",
        {"Authorization": f"Basic {basic}", "Content-Type": "application/x-www-form-urlencoded"},
        urlencode({"grant_type": "client_credentials", "scope": scope}).encode(),
        timeout=20,
    )
    token, expires = result.get("access_token"), result.get("expires_in")
    if (
        not isinstance(token, str) or not token or re.search(r"\s", token)
        or type(expires) is not int or expires <= 0
    ):
        raise DatabricksError("Databricks returned invalid OAuth credentials.")
    return token, expires


class ReaderWorkspace:
    def __init__(self, config: ReaderConfig):
        self.config = config
        self._token = ""
        self._expires_at = 0.0
        self._token_lock = Lock()

    def _request(self, path: str, payload: dict, *, timeout: int) -> dict:
        with self._token_lock:
            if monotonic() >= self._expires_at:
                issued_at = monotonic()
                self._token, expires_in = oauth_token(
                    self.config.host, self.config.client_id, self.config.client_secret, scope="all-apis",
                )
                self._expires_at = issued_at + max(0, expires_in - 30)
            token = self._token
        return _post(
            self.config.host + path,
            {"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
            json.dumps(payload, allow_nan=False).encode(),
            timeout=timeout,
        )

    def read(self, statement: str, parameters: dict[str, str], *, row_limit: int):
        if type(row_limit) is not int or not 1 <= row_limit <= FETCH_LIMIT:
            raise ValueError("SQL requires a bounded row limit.")
        response = self._request("/api/2.0/sql/statements", {
            "warehouse_id": self.config.warehouse,
            "statement": statement,
            "parameters": [
                {"name": name, "value": value, "type": "STRING"} for name, value in parameters.items()
            ],
            "wait_timeout": "10s",
            "on_wait_timeout": "CANCEL",
            "disposition": "INLINE",
            "format": "JSON_ARRAY",
            "row_limit": row_limit,
            "byte_limit": 131_072,
        }, timeout=20)
        status = response.get("status", {})
        if status.get("state") != "SUCCEEDED":
            code = _error_code(status.get("error", {}))
            raise DatabricksError(f"The bounded Gold statement did not succeed (SQL{code}).")
        result = response.get("result", {})
        manifest = response.get("manifest", {})
        rows = result.get("data_array", [])
        if (
            manifest.get("truncated") or result.get("next_chunk_index") is not None
            or not isinstance(rows, list) or len(rows) > row_limit
        ):
            raise DatabricksError("The Gold statement exceeded its inline response bounds.")
        return rows

    def chat(self, endpoint: str, payload: dict) -> dict:
        if endpoint != HOSTED_ENDPOINT:
            raise ValueError("The configured model endpoint is unsupported.")
        return self._request(f"/serving-endpoints/{endpoint}/invocations", payload, timeout=45)
