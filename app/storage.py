"""Photo storage — S3-compatible (Cloudflare R2) when configured, local disk otherwise.

Env (set all together on Render):
  R2_BUCKET          e.g. cityu-facility-photos
  R2_ACCESS_KEY_ID   R2 API token access key
  R2_SECRET_ACCESS_KEY
  R2_ENDPOINT        https://<account_id>.r2.cloudflarestorage.com
  R2_PUBLIC_BASE     https://pub-xxxx.r2.dev (public bucket URL)

When R2_* is set, photos are PUT to the bucket and served via R2_PUBLIC_BASE;
otherwise they fall back to the local UPLOAD_DIR (local dev only). The Render
free instance disk is wiped on every deploy, so production must use R2.
"""
import os

from .db import UPLOAD_DIR

_BUCKET = os.getenv("R2_BUCKET", "").strip()
_ACCESS = os.getenv("R2_ACCESS_KEY_ID", "").strip()
_SECRET = os.getenv("R2_SECRET_ACCESS_KEY", "").strip()
_ENDPOINT = os.getenv("R2_ENDPOINT", "").strip()
_PUBLIC = os.getenv("R2_PUBLIC_BASE", "").strip().rstrip("/")

_enabled = bool(_BUCKET and _ACCESS and _SECRET)
_client = None


def is_remote() -> bool:
    return _enabled


def _s3():
    global _client
    if _client is None:
        import boto3
        _client = boto3.client(
            "s3",
            endpoint_url=_ENDPOINT or None,
            aws_access_key_id=_ACCESS,
            aws_secret_access_key=_SECRET,
        )
    return _client


def save(filename: str, data: bytes) -> None:
    if _enabled:
        content_type = ("image/jpeg" if filename.endswith(".jpg")
                        else "image/png" if filename.endswith(".png")
                        else "image/webp")
        _s3().put_object(Bucket=_BUCKET, Key=filename, Body=data, ContentType=content_type)
    else:
        with open(UPLOAD_DIR / filename, "wb") as f:
            f.write(data)


def public_url(filename: str) -> str:
    """Where the browser can load the image. Local mode keeps the /uploads route."""
    if _enabled:
        return f"{_PUBLIC}/{filename}"
    return f"/uploads/{filename}"
