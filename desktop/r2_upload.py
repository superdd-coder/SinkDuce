#!/usr/bin/env python3
"""Upload a packaged SinkDuce DMG release to Cloudflare R2 (S3 API, boto3).

Run by desktop_release.py via an interpreter that has boto3 (probed there).
Requires S3-style R2 credentials; the wrangler OAuth login cannot drive boto3.

Usage (repo root):
  python3 desktop/r2_upload.py \
    --dmg desktop/dist/SinkDuce-macos-arm64-vX.Y.Z.dmg \
    --version X.Y.Z

Reads config from env or desktop/r2.env (KEY=VALUE):
  SINKDUCE_R2_ACCOUNT_ID       Cloudflare account id (endpoint host)
  SINKDUCE_R2_ACCESS_KEY_ID    R2 API token access key id
  SINKDUCE_R2_SECRET_ACCESS_KEY
  SINKDUCE_R2_BUCKET           default: sinkduce-releases

Uploads, flat at the bucket root:
  SinkDuce-macos-arm64-vX.Y.Z.dmg         (immutable, 1d cache)
  SinkDuce-macos-arm64-vX.Y.Z.dmg.sha256
  SinkDuce-macos-arm64-vX.Y.Z.app.tar.gz  (in-app updater payload, optional)
  SinkDuce-macos-arm64-vX.Y.Z.app.tar.gz.sig
  latest.json                             ({version,tag,asset,sha256,size,
                                            updated_at, pub_date, platforms})
"""

from __future__ import annotations

import argparse
import datetime as _dt
import hashlib
import json
import os
import re
import sys
from pathlib import Path

_DEFAULT_BUCKET = "sinkduce-releases"
_DEFAULT_PUBLIC_BASE = "https://pub-789a11bb112e4ed59d731554588eebfb.r2.dev"
_PART_SIZE = 64 * 1024 * 1024


def load_config() -> dict[str, str]:
    values: dict[str, str] = {}
    env_file = Path(__file__).resolve().parent / "r2.env"
    if env_file.is_file():
        for line in env_file.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, val = line.partition("=")
            values[key.strip()] = val.strip().strip("'\"")
    def get(name: str, default: str | None = None) -> str | None:
        return os.environ.get(name) or values.get(name) or default
    cfg = {
        "account_id": get("SINKDUCE_R2_ACCOUNT_ID", ""),
        "access_key_id": get("SINKDUCE_R2_ACCESS_KEY_ID", ""),
        "secret_access_key": get("SINKDUCE_R2_SECRET_ACCESS_KEY", ""),
        "bucket": get("SINKDUCE_R2_BUCKET", _DEFAULT_BUCKET) or _DEFAULT_BUCKET,
        "public_base": (
            get("SINKDUCE_R2_PUBLIC_BASE", _DEFAULT_PUBLIC_BASE) or _DEFAULT_PUBLIC_BASE
        ).rstrip("/"),
    }
    missing = [k for k in ("account_id", "access_key_id", "secret_access_key") if not cfg[k]]
    if missing:
        sys.exit(
            "r2_upload: missing config: "
            + ", ".join(f"SINKDUCE_R2_{m.upper()}" for m in missing)
            + " (set in env or desktop/r2.env, see desktop/r2.env.template)"
        )
    return cfg


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def extract_changelog_notes(changelog: Path, version: str) -> str:
    """Return the `## vX.Y.Z` section body for `version` ("" when absent)."""
    if not changelog.is_file():
        return ""
    section = re.compile(rf"^##\s+v?{re.escape(version)}\s*$")
    out: list[str] = []
    active = False
    for line in changelog.read_text(encoding="utf-8").splitlines():
        if section.match(line):
            active = True
            continue
        if active and line.startswith("## "):
            break
        if active:
            out.append(line)
    return "\n".join(out).strip()


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dmg", required=True, type=Path)
    ap.add_argument("--version", required=True)
    ap.add_argument(
        "--app-tar-gz",
        type=Path,
        default=None,
        help="signed in-app updater payload (…app.tar.gz); requires --app-tar-gz-sig",
    )
    ap.add_argument(
        "--app-tar-gz-sig",
        type=Path,
        default=None,
        help="minisign signature for the payload; required with --app-tar-gz",
    )
    ap.add_argument(
        "--changelog",
        type=Path,
        default=Path(__file__).resolve().parent.parent / "CHANGELOG.md",
        help="CHANGELOG.md to pull the `## vX.Y.Z` release-notes section from",
    )
    args = ap.parse_args()

    if args.app_tar_gz is not None and args.app_tar_gz_sig is None:
        ap.error("--app-tar-gz needs --app-tar-gz-sig")
    if args.app_tar_gz is None and args.app_tar_gz_sig is not None:
        ap.error("--app-tar-gz-sig needs --app-tar-gz")

    try:
        import boto3
        from botocore.config import Config
    except ImportError:
        sys.exit("r2_upload: boto3 not installed in this interpreter")

    dmg: Path = args.dmg
    if not dmg.is_file():
        sys.exit(f"r2_upload: missing {dmg}")
    version = args.version.lstrip("vV")
    asset = f"SinkDuce-macos-arm64-v{version}.dmg"
    tar_gz: Path | None = args.app_tar_gz
    tar_gz_sig: Path | None = args.app_tar_gz_sig
    if tar_gz is not None and not tar_gz.is_file():
        sys.exit(f"r2_upload: missing {tar_gz}")
    if tar_gz_sig is not None and not tar_gz_sig.is_file():
        sys.exit(f"r2_upload: missing {tar_gz_sig}")

    cfg = load_config()
    endpoint = f"https://{cfg['account_id']}.r2.cloudflarestorage.com"
    s3 = boto3.client(
        "s3",
        endpoint_url=endpoint,
        region_name="auto",
        aws_access_key_id=cfg["access_key_id"],
        aws_secret_access_key=cfg["secret_access_key"],
        config=Config(
            s3={"addressing_style": "path"},
            retries={"max_attempts": 5, "mode": "standard"},
        ),
    )
    bucket = cfg["bucket"]

    digest = sha256_file(dmg)
    size = dmg.stat().st_size
    now = _dt.datetime.now(_dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    latest: dict = {
        "version": version,
        "tag": f"v{version}",
        "asset": asset,
        "sha256": digest,
        "size": size,
        "updated_at": now,
        # tauri-plugin-updater reads pub_date (RFC3339) + platforms on top of
        # the fields the SPA's update checker already consumes.
        "pub_date": now,
    }
    notes = extract_changelog_notes(args.changelog, version)
    if notes:
        latest["notes"] = notes

    # latest.json is uploaded LAST: everything it points at is already on R2
    # when the update prompt can first fire (same contract as the CI job).
    jobs = [
        (
            asset,
            dmg,
            {
                "ContentType": "application/x-apple-diskimage",
                "ContentDisposition": f'attachment; filename="{asset}"',
                "CacheControl": "public, max-age=86400",
            },
        ),
        (
            asset + ".sha256",
            f"{digest}  {asset}\n".encode(),
            {"ContentType": "text/plain", "CacheControl": "public, max-age=86400"},
        ),
    ]

    if tar_gz is not None and tar_gz_sig is not None:
        tar_gz_asset = tar_gz.name
        # The .sig file body IS the base64 blob the updater expects verbatim.
        signature = tar_gz_sig.read_text(encoding="utf-8").strip()
        latest["platforms"] = {
            "darwin-aarch64": {
                "signature": signature,
                "url": f"{cfg['public_base']}/{tar_gz_asset}",
            }
        }
        jobs.append(
            (
                tar_gz_asset,
                tar_gz,
                {"ContentType": "application/gzip", "CacheControl": "public, max-age=86400"},
            )
        )
        jobs.append(
            (
                tar_gz_asset + ".sig",
                tar_gz_sig,
                {"ContentType": "text/plain", "CacheControl": "public, max-age=86400"},
            )
        )
    else:
        print(
            "r2_upload: WARN no --app-tar-gz/--app-tar-gz-sig — in-app update will "
            "not be offered for this release (DMG download only)"
        )

    jobs.append(
        (
            "latest.json",
            json.dumps(latest, indent=2).encode() + b"\n",
            {"ContentType": "application/json", "CacheControl": "public, max-age=300"},
        )
    )

    transfer_cfg = {
        "multipart_threshold": _PART_SIZE,
        "multipart_chunksize": _PART_SIZE,
    }
    for key, body, extra in jobs:
        if isinstance(body, bytes):
            s3.put_object(Bucket=bucket, Key=key, Body=body, **extra)
        else:
            s3.upload_file(str(body), bucket, key, ExtraArgs=extra, Config=_transfer_config(transfer_cfg))
        print(f"r2_upload: s3://{bucket}/{key} ({body if isinstance(body, bytes) else body.stat().st_size} bytes)")

    print(f"r2_upload: done — latest.json points at {asset} (sha256 {digest[:12]}…)")
    for key, _, _ in jobs:
        print(f"r2_upload: public {cfg['public_base']}/{key}")
    return 0


def _transfer_config(opts: dict) -> object:
    from boto3.s3.transfer import TransferConfig

    return TransferConfig(
        multipart_threshold=opts["multipart_threshold"],
        multipart_chunksize=opts["multipart_chunksize"],
        max_concurrency=4,
        use_threads=True,
    )


if __name__ == "__main__":
    raise SystemExit(main())
