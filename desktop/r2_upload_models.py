#!/usr/bin/env python3
"""Upload an ONNX model release zip (+ .sha256) to Cloudflare R2.

Usage (repo root; run with an interpreter that has boto3):
  python3 desktop/r2_upload_models.py model_release/v1.0.0/sinkduce-onnx-models-v1.0.0.zip

Reads the same config as r2_upload.py (env or desktop/r2.env, SINKDUCE_R2_*).
Keys are flat at the bucket root and mirror what src/models/download.py fetches:
  sinkduce-onnx-models-v<ver>.zip
  sinkduce-onnx-models-v<ver>.zip.sha256
"""

from __future__ import annotations

import argparse
import hashlib
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from r2_upload import load_config  # noqa: E402

_PART_SIZE = 64 * 1024 * 1024


def _ensure_sidecar(zip_path: Path) -> Path:
    sha_path = zip_path.with_name(zip_path.name + ".sha256")
    if sha_path.is_file():
        return sha_path
    digest = hashlib.sha256()
    with zip_path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            digest.update(chunk)
    sha_path.write_text(f"{digest.hexdigest()}  {zip_path.name}\n", encoding="utf-8")
    print(f"r2_upload_models: wrote missing sidecar {sha_path}")
    return sha_path


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("zip", type=Path)
    args = ap.parse_args()

    zip_path: Path = args.zip
    if not zip_path.is_file():
        sys.exit(f"r2_upload_models: missing {zip_path}")
    sha_path = _ensure_sidecar(zip_path)

    try:
        import boto3
        from botocore.config import Config
        from boto3.s3.transfer import TransferConfig
    except ImportError:
        sys.exit("r2_upload_models: boto3 not installed in this interpreter")

    cfg = load_config()
    s3 = boto3.client(
        "s3",
        endpoint_url=f"https://{cfg['account_id']}.r2.cloudflarestorage.com",
        region_name="auto",
        aws_access_key_id=cfg["access_key_id"],
        aws_secret_access_key=cfg["secret_access_key"],
        config=Config(
            s3={"addressing_style": "path"},
            retries={"max_attempts": 5, "mode": "standard"},
        ),
    )
    bucket = cfg["bucket"]
    transfer = TransferConfig(
        multipart_threshold=_PART_SIZE,
        multipart_chunksize=_PART_SIZE,
        max_concurrency=4,
        use_threads=True,
    )
    jobs = [
        (zip_path.name, zip_path, "application/zip"),
        (zip_path.name + ".sha256", sha_path, "text/plain"),
    ]
    for key, path, ctype in jobs:
        s3.upload_file(
            str(path),
            bucket,
            key,
            ExtraArgs={"ContentType": ctype, "CacheControl": "public, max-age=86400"},
            Config=transfer,
        )
        print(f"r2_upload_models: s3://{bucket}/{key} ({path.stat().st_size} bytes)")
    print(f"r2_upload_models: public {cfg['public_base']}/{zip_path.name}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
