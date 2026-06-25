"""
Upload scraped data to an S3 bucket (or any S3-compatible store: AWS S3,
Cloudflare R2, Backblaze B2).

Authentication is fully unattended — it uses the standard AWS credential
chain (env vars, ~/.aws/credentials, or an IAM role), so there is no browser
flow and nothing to refresh. Replicating to another device is just setting
the same env vars.

Configuration (env vars)
------------------------
    AWS_ACCESS_KEY_ID        access key (required)
    AWS_SECRET_ACCESS_KEY    secret key (required)
    AWS_DEFAULT_REGION       e.g. eu-central-1 (required for AWS)
    S3_BUCKET                target bucket name (required)
    S3_PREFIX                key prefix / "folder" (default: data/raw)
    S3_ENDPOINT_URL          only for R2/B2/MinIO, e.g.
                             https://<acct>.r2.cloudflarestorage.com
                             (omit for AWS S3)

Usage
-----
    from upload_s3 import upload, upload_processed

    # raw scraper output -> data/raw/  (the default)
    upload(out_dir)              # a folder — files land under data/raw/<name>/...
    upload(csv_path)             # a single file -> data/raw/<filename>

    # processed/transform-pipeline output -> data/processed/
    upload_processed(csv_path)               # convenience wrapper
    upload(csv_path, prefix="data/processed")  # or pass prefix explicitly

The call is a graceful no-op (prints a warning, does not raise) when boto3
isn't installed or S3_BUCKET isn't set, so scrapers keep working locally.
"""

from __future__ import annotations

import os
from pathlib import Path


def _client():
    """Build an S3 client, or return None if boto3 / config is missing."""
    try:
        import boto3
    except ImportError:
        print("  (boto3 not installed — skipping S3 upload)")
        return None

    if not os.environ.get("S3_BUCKET"):
        print("  (S3_BUCKET not set — skipping S3 upload)")
        return None

    endpoint = os.environ.get("S3_ENDPOINT_URL")  # set only for R2/B2/etc.
    return boto3.client("s3", endpoint_url=endpoint)


def upload(local_path: str | Path, prefix: str | None = None) -> bool:
    """Upload a file or folder to S3 under a key prefix.

    A folder is mirrored into a same-named key prefix; a single file lands at
    PREFIX/<filename>. Returns True on success, False if skipped.

    prefix:
        Where in the bucket to put it. Defaults to the S3_PREFIX env var, or
        "data/raw" if unset (raw scraper output). The transform/processing
        pipeline should pass prefix="data/processed" to keep processed data
        separate, e.g.  upload(out_csv, prefix="data/processed").
    """
    s3 = _client()
    if s3 is None:
        return False

    local = Path(local_path)
    if not local.exists():
        print(f"  (nothing to upload — {local} does not exist)")
        return False

    bucket = os.environ["S3_BUCKET"]
    if prefix is None:
        prefix = os.environ.get("S3_PREFIX", "data/raw")
    prefix = prefix.strip("/")

    if local.is_dir():
        files = [f for f in local.rglob("*") if f.is_file()]
        for f in files:
            key = f"{prefix}/{local.name}/{f.relative_to(local).as_posix()}"
            s3.upload_file(str(f), bucket, key)
        print(f"  ↑ uploaded {len(files)} files → s3://{bucket}/{prefix}/{local.name}/")
    else:
        key = f"{prefix}/{local.name}"
        s3.upload_file(str(local), bucket, key)
        print(f"  ↑ uploaded {local.name} → s3://{bucket}/{key}")

    return True


def upload_raw(local_path: str | Path) -> bool:
    """Upload raw scraper output to data/raw/ (what the scrapers use)."""
    return upload(local_path, prefix="data/raw")


def upload_processed(local_path: str | Path) -> bool:
    """Upload processed/transformed output to data/processed/.

    Use this from the data-transform pipeline so processed data lands under
    data/processed/ instead of data/raw/."""
    return upload(local_path, prefix="data/processed")
