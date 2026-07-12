#!/usr/bin/env python3
"""Capture a publicly shared personal-OneDrive folder with full source metadata.

This uses Microsoft's anonymous Badger-token flow, matching the current
OneDrive web client. It recursively downloads every file, preserves the folder
structure, calculates SHA-256 digests, and writes JSON/CSV manifests.
"""
from __future__ import annotations

import base64
import csv
import hashlib
import json
import mimetypes
import os
from pathlib import Path
import shutil
import sys
import time
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import quote
from urllib.request import Request, urlopen

API = "https://my.microsoftpersonalcontent.com/_api/v2.0"
TOKEN_API = "https://api-badgerp.svc.ms/v1.0/token"
APP_ID = "5cbed6ac-a083-4e14-b191-b4ba07653de2"
SHARE_URL = os.environ.get(
    "SHARE_URL",
    "https://1drv.ms/f/c/7c44d2c377e3228b/IgCLIuN3w9JEIIB8hhAAAAAAASAgV0uMkEZqjG8SYx083RU?e=tn9rdL",
)
OUT = Path(os.environ.get("OUT_DIR", "capture"))
FILES = OUT / "files"


def request_bytes(req: Request, attempts: int = 6, timeout: int = 180) -> bytes:
    error: Exception | None = None
    for attempt in range(attempts):
        try:
            with urlopen(req, timeout=timeout) as response:
                return response.read()
        except (HTTPError, URLError, TimeoutError, ConnectionError) as exc:
            error = exc
            if attempt + 1 == attempts:
                break
            time.sleep(min(30, 2 ** attempt))
    raise RuntimeError(f"request failed after {attempts} attempts: {req.full_url}: {error}")


def fresh_token() -> str:
    body = json.dumps({"appId": APP_ID}).encode("utf-8")
    req = Request(
        TOKEN_API,
        data=body,
        method="POST",
        headers={"Content-Type": "application/json", "User-Agent": "Mozilla/5.0"},
    )
    return json.loads(request_bytes(req).decode("utf-8"))["token"]


def share_id(url: str) -> str:
    encoded = base64.urlsafe_b64encode(url.encode("utf-8")).decode("ascii").rstrip("=")
    return "u!" + encoded


def api_json(url: str, token: str | None = None) -> dict[str, Any]:
    tok = token or fresh_token()
    req = Request(
        url,
        headers={
            "Authorization": f"Badger {tok}",
            "Prefer": "autoredeem",
            "Accept": "application/json",
            "User-Agent": "Mozilla/5.0",
        },
    )
    raw = request_bytes(req)
    try:
        return json.loads(raw.decode("utf-8"))
    except Exception as exc:
        raise RuntimeError(f"non-JSON API response from {url}: {raw[:500]!r}") from exc


def safe_component(name: str) -> str:
    # Preserve names except separators/control characters that cannot exist locally.
    cleaned = "".join("_" if ord(ch) < 32 or ch in "\\/" else ch for ch in name).strip()
    return cleaned or "unnamed"


def walk_folder(drive_id: str, item_id: str, relative: Path, token: str, out: list[dict[str, Any]]) -> None:
    url: str | None = f"{API}/drives/{quote(drive_id, safe='')}/items/{quote(item_id, safe='')}/children?$top=500"
    while url:
        page = api_json(url, token)
        if "error" in page:
            raise RuntimeError(json.dumps(page["error"], ensure_ascii=False))
        for child in page.get("value", []):
            rel = relative / safe_component(child.get("name", "unnamed"))
            row = {
                "id": child.get("id"),
                "drive_id": child.get("parentReference", {}).get("driveId", drive_id),
                "name": child.get("name"),
                "relative_path": rel.as_posix(),
                "size": child.get("size"),
                "createdDateTime": child.get("createdDateTime"),
                "lastModifiedDateTime": child.get("lastModifiedDateTime"),
                "webUrl": child.get("webUrl"),
                "fileSystemInfo": child.get("fileSystemInfo"),
                "image": child.get("image"),
                "photo": child.get("photo"),
                "video": child.get("video"),
                "audio": child.get("audio"),
                "mime_type": child.get("file", {}).get("mimeType"),
                "quickXorHash": child.get("file", {}).get("hashes", {}).get("quickXorHash"),
                "folder_child_count": child.get("folder", {}).get("childCount"),
                "kind": "folder" if "folder" in child else "file",
            }
            out.append(row)
            if "folder" in child:
                walk_folder(row["drive_id"], child["id"], rel, token, out)
        url = page.get("@odata.nextLink")


def download_file(row: dict[str, Any]) -> None:
    drive = row["drive_id"]
    item = row["id"]
    metadata = api_json(f"{API}/drives/{quote(drive, safe='')}/items/{quote(item, safe='')}")
    dl = metadata.get("@content.downloadUrl")
    if not dl:
        raise RuntimeError(f"no download URL for {row['relative_path']}: {metadata}")
    target = FILES / Path(row["relative_path"])
    target.parent.mkdir(parents=True, exist_ok=True)
    expected = int(row.get("size") or 0)
    if target.exists() and (not expected or target.stat().st_size == expected):
        pass
    else:
        tmp = target.with_suffix(target.suffix + ".part")
        req = Request(dl, headers={"User-Agent": "Mozilla/5.0"})
        raw = request_bytes(req, attempts=8, timeout=600)
        tmp.write_bytes(raw)
        if expected and len(raw) != expected:
            raise RuntimeError(f"size mismatch for {target}: expected {expected}, got {len(raw)}")
        tmp.replace(target)
    h = hashlib.sha256()
    with target.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            h.update(chunk)
    row["sha256"] = h.hexdigest()
    row["downloaded_size"] = target.stat().st_size
    row["local_path"] = target.relative_to(OUT).as_posix()
    row["extension"] = target.suffix.lower()
    row["guessed_mime_type"] = mimetypes.guess_type(target.name)[0]


def main() -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    FILES.mkdir(parents=True, exist_ok=True)
    token = fresh_token()
    root_url = f"{API}/shares/{share_id(SHARE_URL)}/driveitem"
    root = api_json(root_url, token)
    if "error" in root:
        raise RuntimeError(json.dumps(root["error"], ensure_ascii=False))
    (OUT / "archive_root.json").write_text(json.dumps(root, indent=2, ensure_ascii=False), encoding="utf-8")

    rows: list[dict[str, Any]] = []
    root_record = {
        "id": root.get("id"),
        "drive_id": root.get("parentReference", {}).get("driveId"),
        "name": root.get("name"),
        "relative_path": safe_component(root.get("name", "archive")),
        "size": root.get("size"),
        "createdDateTime": root.get("createdDateTime"),
        "lastModifiedDateTime": root.get("lastModifiedDateTime"),
        "webUrl": root.get("webUrl"),
        "folder_child_count": root.get("folder", {}).get("childCount"),
        "kind": "folder" if "folder" in root else "file",
        "is_root": True,
    }
    rows.append(root_record)
    if "folder" in root:
        walk_folder(root_record["drive_id"], root["id"], Path(root_record["relative_path"]), token, rows)
    else:
        root_record.update({
            "mime_type": root.get("file", {}).get("mimeType"),
            "quickXorHash": root.get("file", {}).get("hashes", {}).get("quickXorHash"),
        })

    file_rows = [r for r in rows if r["kind"] == "file"]
    print(f"Discovered {len(file_rows)} files and {len(rows)-len(file_rows)} folders", flush=True)
    for index, row in enumerate(file_rows, 1):
        print(f"[{index}/{len(file_rows)}] {row['relative_path']}", flush=True)
        download_file(row)

    manifest = {
        "capture_schema": "onedrive-public-archive-capture-v1",
        "captured_at_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "source_share_url": SHARE_URL,
        "share_id": share_id(SHARE_URL),
        "root": root_record,
        "file_count": len(file_rows),
        "folder_count": len(rows) - len(file_rows),
        "total_file_bytes": sum(int(r.get("downloaded_size") or 0) for r in file_rows),
        "items": rows,
    }
    (OUT / "source_manifest.json").write_text(json.dumps(manifest, indent=2, ensure_ascii=False), encoding="utf-8")

    fields = sorted({k for r in rows for k in r if not isinstance(r.get(k), (dict, list))})
    with (OUT / "source_manifest.csv").open("w", newline="", encoding="utf-8-sig") as fh:
        writer = csv.DictWriter(fh, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)

    summary = {
        "source_share_url": SHARE_URL,
        "root_name": root.get("name"),
        "file_count": len(file_rows),
        "folder_count": len(rows) - len(file_rows),
        "total_file_bytes": manifest["total_file_bytes"],
        "extensions": {},
    }
    for row in file_rows:
        ext = row.get("extension") or "[none]"
        summary["extensions"][ext] = summary["extensions"].get(ext, 0) + 1
    (OUT / "capture_summary.json").write_text(json.dumps(summary, indent=2, ensure_ascii=False), encoding="utf-8")
    print(json.dumps(summary, indent=2, ensure_ascii=False), flush=True)
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(f"FATAL: {exc}", file=sys.stderr, flush=True)
        raise
