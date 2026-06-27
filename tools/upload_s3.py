"""
Storage helper for the pipeline — one place that reads and writes every input
and output, so the rest of the code never talks to S3 (or the filesystem)
directly. It has two interchangeable **backends**:

    s3     (default)  — the shared bucket; the real source of truth
    local             — a local mirror under ./local_store/ with the SAME key
                        layout (data/raw/…, data/processed/…, data/results/…),
                        so a fully local run chains stage→stage with no network

Both backends implement the same calls (upload / read_csv / read_bytes /
list_keys / find_key / latest_key), so switching backend changes WHERE data
lives without changing any caller.

Choosing the backend
--------------------
Priority: an explicit CLI flag wins, otherwise the STORAGE_BACKEND env var,
otherwise s3.

    --local                       force the local backend for this run
    --s3                          force the S3 backend for this run
    STORAGE_BACKEND=local|s3      the standing default (typically from .env)

A .env file at the repo root is loaded automatically (without overriding vars
already set in the real environment), so you can keep STORAGE_BACKEND, the AWS
credentials and S3_BUCKET there instead of exporting them every shell.

Configuration (env vars / .env)
-------------------------------
    STORAGE_BACKEND          local | s3   (default: s3)
    LOCAL_STORE              local mirror root (default: <repo>/local_store)
    S3_BUCKET                target bucket name (required for the s3 backend)
    AWS_ACCESS_KEY_ID        access key (s3 backend)
    AWS_SECRET_ACCESS_KEY    secret key (s3 backend)
    AWS_DEFAULT_REGION       e.g. eu-central-1 (s3 backend, AWS)
    S3_PREFIX                key prefix / "folder" (default: data/raw)
    S3_ENDPOINT_URL          only for R2/B2/MinIO (omit for AWS S3)
    S3_DELETE_LOCAL          1/true/yes → delete the working file after a save

Usage
-----
    from upload_s3 import upload, read_csv, list_keys
    upload(csv_path, prefix="data/processed")   # → active backend
    df = read_csv("data/processed/master.csv")  # ← active backend
"""

from __future__ import annotations

import os
import shutil
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]


# ──────────────────────────── .env loading ─────────────────────────────────
def _load_dotenv(path: Path = REPO_ROOT / ".env") -> None:
    """Load KEY=VALUE lines from a .env file into os.environ (no override).

    Deliberately tiny (no python-dotenv dependency): ignores blank lines and
    `#` comments, strips an optional leading `export `, and trims surrounding
    quotes. Real environment variables always win over the file.
    """
    if not path.exists():
        return
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        if line.startswith("export "):
            line = line[len("export "):]
        key, _, val = line.partition("=")
        key, val = key.strip(), val.strip().strip('"').strip("'")
        if key:
            os.environ.setdefault(key, val)


_load_dotenv()


# ──────────────────────────── backend selection ────────────────────────────
def backend() -> str:
    """Return the active backend: 'local' or 's3' (flag > env > default)."""
    if "--local" in sys.argv:
        return "local"
    if "--s3" in sys.argv:
        return "s3"
    val = os.environ.get("STORAGE_BACKEND", "s3").strip().lower()
    return "local" if val in ("local", "file", "fs") else "s3"


def _is_local() -> bool:
    return backend() == "local"


def local_store() -> Path:
    """Root of the local mirror (keys map to <root>/<key>)."""
    return Path(os.environ.get("LOCAL_STORE") or (REPO_ROOT / "local_store"))


def describe_backend() -> str:
    """Human-readable one-liner for logs/banners."""
    if _is_local():
        return f"local ({local_store()})"
    b = os.environ.get("S3_BUCKET")
    return f"s3 ({b})" if b else "s3 (S3_BUCKET unset)"


# ──────────────────────────── S3 client ────────────────────────────────────
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


def _require_client():
    s3 = _client()
    if s3 is None:
        raise RuntimeError(
            "S3 not configured — set S3_BUCKET (and AWS credentials) and "
            "install boto3, or use the local backend (--local / STORAGE_BACKEND=local).")
    return s3


# ──────────────────────────── upload / save ────────────────────────────────
def upload(local_path: str | Path, prefix: str | None = None,
           cleanup: bool | None = None) -> bool:
    """Persist a file or folder to the active backend under a key prefix.

    A folder is mirrored into a same-named key prefix; a single file lands at
    PREFIX/<filename>. With the local backend it copies into ./local_store/;
    with the s3 backend it uploads to the bucket. Returns True on success.

    prefix:
        Where to put it. Defaults to the S3_PREFIX env var, or "data/raw".
        The transform/feature/model stages pass "data/processed" /
        "data/results" so each stage's output is keyed the same in both
        backends and the next stage can read it back.
    cleanup:
        If True, delete the local working file/folder after a successful save.
        Defaults to the S3_DELETE_LOCAL env var ("1"/"true"/"yes").
    """
    local = Path(local_path)
    if not local.exists():
        print(f"  (nothing to save — {local} does not exist)")
        return False

    if prefix is None:
        prefix = os.environ.get("S3_PREFIX", "data/raw")
    prefix = prefix.strip("/")

    ok = _save_local(local, prefix) if _is_local() else _upload_s3(local, prefix)
    if not ok:
        return False

    if cleanup is None:
        cleanup = os.environ.get("S3_DELETE_LOCAL", "").lower() in ("1", "true", "yes")
    if cleanup:
        if local.is_dir():
            shutil.rmtree(local, ignore_errors=True)
        else:
            local.unlink(missing_ok=True)
        print(f"  🗑  removed working copy {local.name}")

    return True


def _save_local(local: Path, prefix: str) -> bool:
    """Copy a file/folder into the local mirror, mirroring the S3 key layout."""
    root = local_store()
    if local.is_dir():
        files = [f for f in local.rglob("*") if f.is_file()]
        for f in files:
            dest = root / prefix / local.name / f.relative_to(local)
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(f, dest)
        print(f"  💾 saved {len(files)} files → {root}/{prefix}/{local.name}/ (local backend)")
    else:
        dest = root / prefix / local.name
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(local, dest)
        print(f"  💾 saved {local.name} → {prefix}/{local.name} (local backend)")
    return True


def _upload_s3(local: Path, prefix: str) -> bool:
    s3 = _client()
    if s3 is None:
        return False
    bucket = os.environ["S3_BUCKET"]
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
    """Persist raw scraper output under data/raw/ (what the scrapers use)."""
    return upload(local_path, prefix="data/raw")


def upload_processed(local_path: str | Path) -> bool:
    """Persist processed/transformed output under data/processed/."""
    return upload(local_path, prefix="data/processed")


# ──────────────────────────── reading back ─────────────────────────────────
def latest_key(prefix: str) -> str | None:
    """Key of the most recently modified object/file under `prefix`.

    `prefix` may be a folder or a filename fragment, e.g.
    latest_key("data/raw/days_off_bg_") -> "data/raw/days_off_bg_2022-..._...".
    Returns None if nothing matches.
    """
    if _is_local():
        root = local_store()
        pref = prefix.strip("/")
        base = (root / pref).parent if not (root / pref).is_dir() else (root / pref)
        if not base.exists():
            return None
        cands = [f for f in base.rglob("*")
                 if f.is_file() and f.relative_to(root).as_posix().startswith(pref)]
        if not cands:
            return None
        return max(cands, key=lambda f: f.stat().st_mtime).relative_to(root).as_posix()

    s3 = _require_client()
    bucket = os.environ["S3_BUCKET"]
    objs = s3.list_objects_v2(Bucket=bucket, Prefix=prefix).get("Contents", [])
    if not objs:
        return None
    return max(objs, key=lambda o: o["LastModified"])["Key"]


def find_key(name_contains: str, prefix: str = "data/raw/",
             suffix: str = ".csv") -> str | None:
    """Newest object/file under `prefix` whose key contains `name_contains`.

    e.g. find_key("load_actual") -> "data/raw/entsoe_bg/load_actual.csv".
    Returns None if nothing matches.
    """
    if _is_local():
        root = local_store()
        base = root / prefix.strip("/")
        if not base.exists():
            return None
        cands = [f for f in base.rglob("*")
                 if f.is_file() and name_contains in f.relative_to(root).as_posix()
                 and f.name.endswith(suffix)]
        if not cands:
            return None
        return max(cands, key=lambda f: f.stat().st_mtime).relative_to(root).as_posix()

    s3 = _require_client()
    bucket = os.environ["S3_BUCKET"]
    matches, token = [], None
    while True:
        kw = {"Bucket": bucket, "Prefix": prefix}
        if token:
            kw["ContinuationToken"] = token
        resp = s3.list_objects_v2(**kw)
        for o in resp.get("Contents", []):
            if name_contains in o["Key"] and o["Key"].endswith(suffix):
                matches.append(o)
        if resp.get("IsTruncated"):
            token = resp.get("NextContinuationToken")
        else:
            break
    if not matches:
        return None
    return max(matches, key=lambda o: o["LastModified"])["Key"]


def list_keys(prefix: str, suffix: str | None = None) -> list[str]:
    """Every object/file key under `prefix` (optionally filtered by suffix)."""
    if _is_local():
        root = local_store()
        base = root / prefix.strip("/")
        if not base.exists():
            return []
        return [f.relative_to(root).as_posix() for f in sorted(base.rglob("*"))
                if f.is_file() and (suffix is None or f.name.endswith(suffix))]

    s3 = _require_client()
    bucket = os.environ["S3_BUCKET"]
    keys, token = [], None
    while True:
        kw = {"Bucket": bucket, "Prefix": prefix}
        if token:
            kw["ContinuationToken"] = token
        resp = s3.list_objects_v2(**kw)
        for o in resp.get("Contents", []):
            if suffix is None or o["Key"].endswith(suffix):
                keys.append(o["Key"])
        if resp.get("IsTruncated"):
            token = resp.get("NextContinuationToken")
        else:
            break
    return keys


def read_bytes(key: str) -> bytes:
    """Raw bytes of an object/file from the active backend (any file type)."""
    if _is_local():
        p = local_store() / key
        if not p.exists():
            raise FileNotFoundError(
                f"local backend: {p} not found — run the producing stage with "
                f"the local backend first (--local / STORAGE_BACKEND=local).")
        return p.read_bytes()

    s3 = _require_client()
    bucket = os.environ["S3_BUCKET"]
    obj = s3.get_object(Bucket=bucket, Key=key)
    return obj["Body"].read()


def read_csv(key: str, **kwargs):
    """Read a CSV from the active backend into a pandas DataFrame (no local copy).

    Pass an exact key; combine with latest_key()/find_key() for stamped names.
    Extra kwargs are forwarded to pandas.read_csv.
    """
    import io
    import pandas as pd
    return pd.read_csv(io.BytesIO(read_bytes(key)), **kwargs)
