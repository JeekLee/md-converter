"""S3 / MinIO image upload using AWS Signature Version 4.

stdlib only — no boto3 required.
Mirrors rhwp's s3_put() in src/main.rs.
"""
from __future__ import annotations

import hashlib
import hmac as _hmac
import http.client
import urllib.parse
from dataclasses import dataclass, field
from datetime import datetime, timezone


@dataclass
class S3Config:
    endpoint: str            # e.g. "http://localhost:10190"
    bucket: str
    access_key: str
    secret_key: str
    prefix: str = ""         # optional key prefix (no trailing slash needed)
    region: str = "us-east-1"


def _sha256_hex(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _hmac_sha256(key: bytes, data: bytes) -> bytes:
    return _hmac.new(key, data, hashlib.sha256).digest()


def put_object(cfg: S3Config, key: str, data: bytes, content_type: str) -> str:
    """PUT *data* to S3/MinIO and return the object URL as "s3://bucket/key".

    Full key stored is "{prefix}/{key}" when cfg.prefix is set, else just "{key}".
    Raises RuntimeError on HTTP error.
    """
    full_key = f"{cfg.prefix.strip('/')}/{key}" if cfg.prefix else key

    now = datetime.now(timezone.utc)
    dt = now.strftime("%Y%m%dT%H%M%SZ")
    date = dt[:8]

    parsed = urllib.parse.urlparse(cfg.endpoint)
    host = parsed.netloc
    scheme = parsed.scheme
    uri = f"/{cfg.bucket}/{full_key}"
    payload_hash = _sha256_hex(data)

    canonical_headers = (
        f"content-type:{content_type}\n"
        f"host:{host}\n"
        f"x-amz-content-sha256:{payload_hash}\n"
        f"x-amz-date:{dt}\n"
    )
    signed_headers = "content-type;host;x-amz-content-sha256;x-amz-date"

    canonical_request = "\n".join([
        "PUT", uri, "",
        canonical_headers, signed_headers, payload_hash,
    ])

    credential_scope = f"{date}/{cfg.region}/s3/aws4_request"
    string_to_sign = "\n".join([
        "AWS4-HMAC-SHA256", dt, credential_scope,
        _sha256_hex(canonical_request.encode()),
    ])

    signing_key = _hmac_sha256(
        _hmac_sha256(
            _hmac_sha256(
                _hmac_sha256(f"AWS4{cfg.secret_key}".encode(), date.encode()),
                cfg.region.encode(),
            ),
            b"s3",
        ),
        b"aws4_request",
    )
    signature = _hmac_sha256(signing_key, string_to_sign.encode()).hex()

    authorization = (
        f"AWS4-HMAC-SHA256 Credential={cfg.access_key}/{credential_scope}, "
        f"SignedHeaders={signed_headers}, Signature={signature}"
    )

    conn_cls = (
        http.client.HTTPSConnection if scheme == "https"
        else http.client.HTTPConnection
    )
    conn = conn_cls(host)
    try:
        conn.request(
            "PUT",
            uri,
            body=data,
            headers={
                "Host": host,
                "Content-Type": content_type,
                "x-amz-date": dt,
                "x-amz-content-sha256": payload_hash,
                "Authorization": authorization,
                "Content-Length": str(len(data)),
            },
        )
        resp = conn.getresponse()
        resp.read()
    finally:
        conn.close()

    if resp.status not in (200, 204):
        raise RuntimeError(f"S3 PUT failed: {resp.status} {resp.reason} (key={full_key})")

    return f"s3://{cfg.bucket}/{full_key}"
