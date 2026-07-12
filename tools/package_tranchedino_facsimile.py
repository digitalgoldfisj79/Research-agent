#!/usr/bin/env python3
from __future__ import annotations

import csv
import hashlib
import json
import os
from pathlib import Path

import fitz
import requests
from PIL import Image

DIRECT_URL = (
    "https://hypeshare.io/755733e3-9c63-4818-8530-c7caf3ced57e/"
    "files/67eeaa26-3dd3-4f73-92fd-e1499427096b/download"
)
OUT = Path(os.environ.get("OUT_DIR", "tranchedino_facsimile_output"))
FACSIMILE = OUT / "facsimile"
SOURCE = OUT / "source"
FACSIMILE.mkdir(parents=True, exist_ok=True)
SOURCE.mkdir(parents=True, exist_ok=True)


def folio_for_page(pdf_page: int) -> tuple[int | None, str | None]:
    if pdf_page < 18:
        return None, None
    offset = pdf_page - 18
    return offset // 2 + 1, "r" if offset % 2 == 0 else "v"


def main() -> None:
    response = requests.get(DIRECT_URL, timeout=900)
    response.raise_for_status()
    data = response.content
    digest = hashlib.sha256(data).hexdigest()
    source_path = SOURCE / "Tranchedino_Cipher_Ledger.pdf"
    source_path.write_bytes(data)
    document = fitz.open(stream=data, filetype="pdf")
    if document.page_count != 358:
        raise RuntimeError(f"Expected 358 pages, got {document.page_count}")

    records = []
    for pdf_page in range(2, 359):
        page = document[pdf_page - 1]
        pixmap = page.get_pixmap(
            matrix=fitz.Matrix(1.0, 1.0), colorspace=fitz.csGRAY, alpha=False
        )
        temporary = OUT / f"_p{pdf_page:03d}.png"
        pixmap.save(temporary)
        folio, side = folio_for_page(pdf_page)
        if folio is None:
            filename = f"prelim_p{pdf_page:03d}.jpg"
            record_id = f"OeNB-Cod2398-prelim-p{pdf_page:03d}"
            category = "preliminary/index/prefatory material"
        else:
            filename = f"f{folio:03d}{side}.jpg"
            record_id = f"OeNB-Cod2398-f{folio:03d}{side}"
            category = "cipher ledger folio side"
        destination = FACSIMILE / filename
        with Image.open(temporary) as image:
            image.convert("L").save(destination, "JPEG", quality=90, optimize=True)
            width, height = image.size
        temporary.unlink()
        records.append(
            {
                "record_id": record_id,
                "folio": folio,
                "side": side,
                "pdf_page": pdf_page,
                "image_number": pdf_page - 1,
                "category": category,
                "facsimile_file": str(destination.relative_to(OUT)),
                "image_width_px": width,
                "image_height_px": height,
                "repository": "Österreichische Nationalbibliothek",
                "shelfmark": "Cod. 2398",
                "barcode": "+Z22661150X",
                "source_sha256": digest,
            }
        )
        if len(records) % 25 == 0:
            print(f"Rendered {len(records)}/357 images", flush=True)

    manifest = {
        "schema": "tranchedino-facsimile-manifest-v1.0",
        "source": {
            "original_filename": "Tranchedino Cipher Ledger (1).pdf",
            "bytes": len(data),
            "sha256": digest,
            "pdf_pages": 358,
            "manuscript_images": 357,
            "repository": "Österreichische Nationalbibliothek (Austrian National Library)",
            "shelfmark": "Cod. 2398",
            "barcode": "+Z22661150X",
            "catalogue_title": "Furtivae litterarum notae, quibus usus fuisse videtur in cancellaria Vicecomitum Mediolanensium 1450-1496",
            "catalogue_date": "1475-1496",
            "broad_title_range": "1450-1496",
        },
        "folio_mapping": {
            "rule": "PDF p.18=f.1r; p.19=f.1v; alternating through PDF p.358=f.171r",
            "confidence": "high",
        },
        "records": records,
    }
    (OUT / "facsimile_manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    with (OUT / "facsimile_manifest.csv").open(
        "w", newline="", encoding="utf-8-sig"
    ) as handle:
        writer = csv.DictWriter(handle, fieldnames=list(records[0]))
        writer.writeheader()
        writer.writerows(records)

    readme = f"""# Tranchedino Cipher Ledger facsimile bundle

Repository: Österreichische Nationalbibliothek
Shelfmark: Cod. 2398
Barcode: +Z22661150X
Catalogue dating: 1475-1496
Descriptive title range: 1450-1496
Source SHA-256: {digest}

This bundle contains the complete source PDF and all 357 manuscript images as individually named JPEG facsimiles. PDF page 18 maps to folio 1r, page 19 to folio 1v, and page 358 to folio 171r.

The facsimiles preserve the exact graphical composition of every cipher table and continuation. They do not themselves identify the semantic value of each sign; that requires palaeographic transcription against the tables.
"""
    (OUT / "README.md").write_text(readme, encoding="utf-8")
    (OUT / "CHECKSUMS.sha256").write_text(
        f"{digest}  source/Tranchedino_Cipher_Ledger.pdf\n", encoding="utf-8"
    )
    print("Facsimile export complete", flush=True)


if __name__ == "__main__":
    main()
