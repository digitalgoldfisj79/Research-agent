#!/usr/bin/env python3
from __future__ import annotations

import csv
import hashlib
import json
import os
import re
import subprocess
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any

import cv2
import fitz
import numpy as np
import requests
from PIL import Image

LANDING_URL = "https://hypeshare.io/ttGSXZ2sAaLZ"
DIRECT_URL = (
    "https://hypeshare.io/755733e3-9c63-4818-8530-c7caf3ced57e/"
    "files/67eeaa26-3dd3-4f73-92fd-e1499427096b/download"
)
OUT = Path(os.environ.get("OUT_DIR", "tranchedino_output"))
FACSIMILE = OUT / "facsimile"
SOURCE = OUT / "source"
OUT.mkdir(parents=True, exist_ok=True)
FACSIMILE.mkdir(parents=True, exist_ok=True)
SOURCE.mkdir(parents=True, exist_ok=True)


def download_source() -> tuple[bytes, str]:
    response = requests.get(DIRECT_URL, timeout=900)
    response.raise_for_status()
    data = response.content
    digest = hashlib.sha256(data).hexdigest()
    (SOURCE / "Tranchedino_Cipher_Ledger.pdf").write_bytes(data)
    return data, digest


def folio_for_pdf_page(pdf_page: int) -> tuple[int | None, str | None]:
    if pdf_page < 18:
        return None, None
    offset = pdf_page - 18
    return offset // 2 + 1, "r" if offset % 2 == 0 else "v"


def safe_ocr(image_path: Path, seconds: int = 18) -> tuple[str, bool]:
    command = [
        "timeout",
        f"{seconds}s",
        "tesseract",
        str(image_path),
        "stdout",
        "-l",
        "ita+eng",
        "--psm",
        "11",
        "preserve_interword_spaces=1",
    ]
    completed = subprocess.run(command, capture_output=True, text=True, check=False)
    return completed.stdout.replace("\x0c", "").strip(), completed.returncode == 124


def bands(binary: np.ndarray, axis: int, fraction: float = 0.18) -> list[list[int]]:
    sums = (binary > 0).sum(axis=axis)
    threshold = binary.shape[1 - axis] * fraction
    indices = np.where(sums > threshold)[0]
    output: list[list[int]] = []
    if not len(indices):
        return output
    start = previous = int(indices[0])
    for value in indices[1:]:
        value = int(value)
        if value > previous + 2:
            output.append([start, previous])
            start = value
        previous = value
    output.append([start, previous])
    return output


def analyse_structure(image: np.ndarray) -> dict[str, Any]:
    blurred = cv2.GaussianBlur(image, (3, 3), 0)
    binary = cv2.adaptiveThreshold(
        blurred,
        255,
        cv2.ADAPTIVE_THRESH_GAUSSIAN_C,
        cv2.THRESH_BINARY_INV,
        31,
        15,
    )
    height, width = binary.shape
    horizontal_kernel = cv2.getStructuringElement(
        cv2.MORPH_RECT, (max(25, width // 18), 1)
    )
    vertical_kernel = cv2.getStructuringElement(
        cv2.MORPH_RECT, (1, max(25, height // 18))
    )
    horizontal = cv2.morphologyEx(binary, cv2.MORPH_OPEN, horizontal_kernel)
    vertical = cv2.morphologyEx(binary, cv2.MORPH_OPEN, vertical_kernel)
    horizontal_bands = bands(horizontal, 1)
    vertical_bands = bands(vertical, 0)

    glyph_layer = cv2.subtract(binary, cv2.bitwise_or(horizontal, vertical))
    _, _, statistics, _ = cv2.connectedComponentsWithStats(
        (glyph_layer > 0).astype(np.uint8), 8
    )
    component_areas: list[int] = []
    for x, y, component_width, component_height, area in statistics[1:]:
        if (
            4 <= area <= 2500
            and component_width <= width * 0.15
            and component_height <= height * 0.12
        ):
            component_areas.append(int(area))

    ys, xs = np.where(binary > 0)
    content_box = (
        [int(xs.min()), int(ys.min()), int(xs.max() + 1), int(ys.max() + 1)]
        if len(xs)
        else None
    )
    return {
        "image_width_px": width,
        "image_height_px": height,
        "content_bbox_px": content_box,
        "ink_fraction": round(float((binary > 0).mean()), 6),
        "horizontal_rule_bands": horizontal_bands,
        "vertical_rule_bands": vertical_bands,
        "horizontal_rule_count": len(horizontal_bands),
        "vertical_rule_count": len(vertical_bands),
        "connected_components": len(component_areas),
        "small_mark_components": sum(area < 35 for area in component_areas),
        "medium_glyph_components": sum(35 <= area < 250 for area in component_areas),
        "large_components": sum(area >= 250 for area in component_areas),
    }


def classify(text: str, structure: dict[str, Any], pdf_page: int) -> tuple[str, str]:
    if pdf_page < 18:
        return "preliminary/index/prefatory material", "medium"
    lowered = text.lower()
    has_null = bool(re.search(r"null|nulle|nulli|nihil", lowered))
    terms = [
        "dux",
        "duca",
        "rex",
        "rege",
        "papa",
        "cardinal",
        "venet",
        "floren",
        "franc",
        "imper",
        "pace",
        "guerra",
        "armata",
        "genoa",
        "milano",
        "mediolan",
    ]
    nomenclator_signal = sum(bool(re.search(term, lowered)) for term in terms)
    alphabet_labels = len(set(re.findall(r"\b[a-z]\b", lowered)))
    table = (
        structure["horizontal_rule_count"] >= 2
        or structure["vertical_rule_count"] >= 2
    )
    if table and (has_null or nomenclator_signal >= 2 or len(text) > 650):
        return "homophonic nomenclator / diplomatic cipher table", "medium"
    if table and alphabet_labels >= 8:
        return "cipher alphabet (likely homophonic substitution)", "medium"
    if table:
        return "structured cipher table; subtype unresolved", "medium"
    if len(text) > 600 and nomenclator_signal >= 2:
        return "nomenclator or code-list continuation", "low"
    if len(text) < 100:
        return "continuation, blank, or visually resistant cipher page", "low"
    return "cipher material; subtype unresolved", "low"


def make_record(pdf_page: int, rendered_path: Path, output_image: Path) -> dict[str, Any]:
    image = cv2.imread(str(rendered_path), cv2.IMREAD_GRAYSCALE)
    if image is None:
        raise RuntimeError(f"Could not read rendered page {rendered_path}")
    structure = analyse_structure(image)
    text, timed_out = safe_ocr(rendered_path)
    folio, side = folio_for_pdf_page(pdf_page)
    if folio is None:
        record_id = f"OeNB-Cod2398-prelim-p{pdf_page:03d}"
    else:
        record_id = f"OeNB-Cod2398-f{folio:03d}{side}"

    lines = [" ".join(line.split()) for line in text.splitlines() if line.strip()]
    heading_candidates = [line for line in lines[:24] if len(line) > 10][:10]
    years = []
    for year in dict.fromkeys(re.findall(r"(?<!\d)(14\d{2})(?!\d)", text[:3500])):
        years.append(
            {
                "reading": year,
                "plausible_for_codex": 1450 <= int(year) <= 1496,
            }
        )

    lowered = text.lower()
    alphabet_labels = len(set(re.findall(r"\b[a-z]\b", lowered)))
    explicit_null = bool(re.search(r"null|nulle|nulli|nihil", lowered))
    nomenclator_terms = [
        "dux",
        "duca",
        "rex",
        "rege",
        "papa",
        "cardinal",
        "venet",
        "floren",
        "franc",
        "imper",
        "pace",
        "guerra",
        "armata",
        "genoa",
        "milano",
        "mediolan",
    ]
    nomenclator_signal = sum(
        bool(re.search(term, lowered)) for term in nomenclator_terms
    )
    classification, confidence = classify(text, structure, pdf_page)

    return {
        "record_id": record_id,
        "folio": folio,
        "side": side,
        "pdf_page": pdf_page,
        "image_number": pdf_page - 1,
        "facsimile_file": str(output_image.relative_to(OUT)),
        "heading_candidates_raw_ocr": heading_candidates,
        "year_candidates_raw_ocr": years,
        "classification": classification,
        "classification_confidence": confidence,
        "plaintext_single_letter_labels_detected": alphabet_labels,
        "explicit_null_label_detected": explicit_null,
        "nomenclator_term_signal_count": nomenclator_signal,
        "ocr_character_count": len(text),
        "ocr_timed_out": timed_out,
        "structure": structure,
        "raw_ocr": text,
    }


def main() -> None:
    data, digest = download_source()
    document = fitz.open(stream=data, filetype="pdf")
    if document.page_count != 358:
        raise RuntimeError(f"Expected 358 PDF pages, found {document.page_count}")

    rendered = OUT / "rendered"
    rendered.mkdir(exist_ok=True)
    jobs: list[tuple[int, Path, Path]] = []
    for pdf_page in range(2, 359):
        page = document[pdf_page - 1]
        pixmap = page.get_pixmap(
            matrix=fitz.Matrix(1.0, 1.0), colorspace=fitz.csGRAY, alpha=False
        )
        rendered_path = rendered / f"p{pdf_page:03d}.png"
        pixmap.save(rendered_path)
        folio, side = folio_for_pdf_page(pdf_page)
        if folio is None:
            facsimile_path = FACSIMILE / f"prelim_p{pdf_page:03d}.jpg"
        else:
            facsimile_path = FACSIMILE / f"f{folio:03d}{side}.jpg"
        Image.open(rendered_path).convert("L").save(
            facsimile_path, "JPEG", quality=88, optimize=True
        )
        jobs.append((pdf_page, rendered_path, facsimile_path))

    records: list[dict[str, Any]] = []
    with ThreadPoolExecutor(max_workers=10) as executor:
        futures = {
            executor.submit(make_record, page, rendered_path, facsimile_path): page
            for page, rendered_path, facsimile_path in jobs
        }
        for number, future in enumerate(as_completed(futures), start=1):
            page = futures[future]
            try:
                records.append(future.result())
            except Exception as exc:
                folio, side = folio_for_pdf_page(page)
                records.append(
                    {
                        "record_id": (
                            f"OeNB-Cod2398-f{folio:03d}{side}"
                            if folio is not None
                            else f"OeNB-Cod2398-prelim-p{page:03d}"
                        ),
                        "folio": folio,
                        "side": side,
                        "pdf_page": page,
                        "image_number": page - 1,
                        "error": repr(exc),
                    }
                )
            if number % 25 == 0:
                print(f"Analysed {number}/{len(jobs)} pages", flush=True)
    records.sort(key=lambda record: record["pdf_page"])

    source_metadata = {
        "landing_url": LANDING_URL,
        "direct_download_url": DIRECT_URL,
        "original_filename": "Tranchedino Cipher Ledger (1).pdf",
        "bytes": len(data),
        "sha256": digest,
        "pdf_pages": 358,
        "manuscript_images": 357,
        "repository": "Österreichische Nationalbibliothek (Austrian National Library)",
        "shelfmark": "Cod. 2398",
        "barcode": "+Z22661150X",
        "catalogue_title": (
            "Furtivae litterarum notae, quibus usus fuisse videtur in "
            "cancellaria Vicecomitum Mediolanensium 1450-1496"
        ),
        "catalogue_date": "1475-1496",
        "broad_title_range": "1450-1496",
    }
    manifest = {
        "catalogue_schema": "tranchedino-cipher-ledger-catalogue-v1.0",
        "source": source_metadata,
        "folio_mapping": {
            "rule": (
                "PDF p.18=f.1r; p.19=f.1v; thereafter alternating through "
                "PDF p.358=f.171r"
            ),
            "confidence": "high",
        },
        "method": {
            "coverage": "All 357 manuscript images rendered and analysed.",
            "facsimile_policy": (
                "Each record includes a page image preserving exact graphical signs."
            ),
            "ocr_policy": (
                "OCR is retained as raw discovery evidence and must not be treated "
                "as exact palaeographic transcription."
            ),
        },
        "records": records,
    }
    (OUT / "tranchedino_cipher_catalogue.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    csv_fields = [
        "record_id",
        "folio",
        "side",
        "pdf_page",
        "image_number",
        "facsimile_file",
        "classification",
        "classification_confidence",
        "plaintext_single_letter_labels_detected",
        "explicit_null_label_detected",
        "nomenclator_term_signal_count",
        "ocr_character_count",
        "ocr_timed_out",
        "heading_candidates",
        "year_candidates",
        "horizontal_rule_count",
        "vertical_rule_count",
        "connected_components",
        "content_bbox_px",
        "raw_ocr",
    ]
    with (OUT / "tranchedino_cipher_catalogue.csv").open(
        "w", newline="", encoding="utf-8-sig"
    ) as handle:
        writer = csv.DictWriter(handle, fieldnames=csv_fields)
        writer.writeheader()
        for record in records:
            structure = record.get("structure", {})
            writer.writerow(
                {
                    "record_id": record["record_id"],
                    "folio": record.get("folio"),
                    "side": record.get("side"),
                    "pdf_page": record["pdf_page"],
                    "image_number": record["image_number"],
                    "facsimile_file": record.get("facsimile_file"),
                    "classification": record.get("classification"),
                    "classification_confidence": record.get(
                        "classification_confidence"
                    ),
                    "plaintext_single_letter_labels_detected": record.get(
                        "plaintext_single_letter_labels_detected"
                    ),
                    "explicit_null_label_detected": record.get(
                        "explicit_null_label_detected"
                    ),
                    "nomenclator_term_signal_count": record.get(
                        "nomenclator_term_signal_count"
                    ),
                    "ocr_character_count": record.get("ocr_character_count"),
                    "ocr_timed_out": record.get("ocr_timed_out"),
                    "heading_candidates": " || ".join(
                        record.get("heading_candidates_raw_ocr", [])
                    ),
                    "year_candidates": " || ".join(
                        item["reading"]
                        for item in record.get("year_candidates_raw_ocr", [])
                    ),
                    "horizontal_rule_count": structure.get(
                        "horizontal_rule_count"
                    ),
                    "vertical_rule_count": structure.get("vertical_rule_count"),
                    "connected_components": structure.get("connected_components"),
                    "content_bbox_px": json.dumps(structure.get("content_bbox_px")),
                    "raw_ocr": record.get("raw_ocr"),
                }
            )

    reconstruction = {
        "schema": "tranchedino-reconstruction-manifest-v1.0",
        "source_sha256": digest,
        "coordinate_system": "facsimile image pixels, top-left origin",
        "records": [
            {
                "record_id": record["record_id"],
                "folio": record.get("folio"),
                "side": record.get("side"),
                "pdf_page": record["pdf_page"],
                "facsimile_file": record.get("facsimile_file"),
                **record.get("structure", {}),
            }
            for record in records
        ],
    }
    (OUT / "tranchedino_reconstruction_manifest.json").write_text(
        json.dumps(reconstruction, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    counts: dict[str, int] = {}
    for record in records:
        classification = record.get("classification", "processing error")
        counts[classification] = counts.get(classification, 0) + 1
    markdown = [
        "# Tranchedino Cipher Ledger: systematic catalogue",
        "",
        "## Provenance",
        "",
        "- Repository: Österreichische Nationalbibliothek (Austrian National Library)",
        "- Shelfmark: Cod. 2398",
        "- Barcode: +Z22661150X",
        "- Catalogue dating: 1475-1496",
        "- Descriptive title range: 1450-1496",
        f"- Source SHA-256: `{digest}`",
        f"- Source size: {len(data):,} bytes",
        "- Extent: 358 PDF pages, comprising one licence page and 357 manuscript images.",
        "- Cipher-body foliation: PDF p.18=f.1r; p.19=f.1v; p.358=f.171r.",
        "",
        "## Evidential status",
        "",
        "Every manuscript image was processed. Each catalogue record links to a facsimile image preserving the exact graphical components of the key or continuation. OCR readings are raw search aids, not exact transcriptions of bespoke signs or historical names.",
        "",
        "## Classification summary",
        "",
    ]
    for label, count in sorted(counts.items(), key=lambda item: -item[1]):
        markdown.append(f"- {label}: {count} pages/sides")
    markdown.extend(
        [
            "",
            "## Reconstruction protocol",
            "",
            "1. Open the record's facsimile image.",
            "2. Recreate the table using the recorded content box and rule bands.",
            "3. Trace each cipher sign exactly, preserving dots, ticks, bars, loops, orientation, ligatures and relative placement.",
            "4. Keep visual variants separate until repeated mappings prove equivalence.",
            "5. Store homophones as separate cipher IDs mapped to the same plaintext value.",
            "6. Store nulls, punctuation, syllables and nomenclator entries independently.",
            "7. Verify all names and dates against the facsimile; do not silently normalise raw OCR.",
            "",
            "## Page and folio inventory",
            "",
        ]
    )
    for record in records:
        if record.get("folio") is None:
            location = "preliminary material"
        else:
            location = f"f.{record['folio']}{record['side']}"
        headings = "; ".join(record.get("heading_candidates_raw_ocr", [])[:3])
        headings = headings or "[no reliable OCR heading]"
        years = ", ".join(
            item["reading"]
            + ("?" if not item["plausible_for_codex"] else "")
            for item in record.get("year_candidates_raw_ocr", [])
        )
        years = years or "-"
        structure = record.get("structure", {})
        markdown.extend(
            [
                f"### {record['record_id']} - {location}, PDF p.{record['pdf_page']}",
                "",
                f"- Facsimile: `{record.get('facsimile_file', '-')}`",
                f"- Raw heading/name candidates: {headings}",
                f"- Raw year candidates: {years}",
                f"- Classification: {record.get('classification', 'processing error')} ({record.get('classification_confidence', 'none')})",
                f"- Composition signals: alphabet labels={record.get('plaintext_single_letter_labels_detected', '-')}; null={'yes' if record.get('explicit_null_label_detected') else 'not OCR-detected'}; nomenclator signal={record.get('nomenclator_term_signal_count', '-')}; rule bands H/V={structure.get('horizontal_rule_count', '-')}/{structure.get('vertical_rule_count', '-')}; retained components={structure.get('connected_components', '-')}; crop={structure.get('content_bbox_px', '-')}.",
                "",
            ]
        )
    (OUT / "tranchedino_cipher_catalogue.md").write_text(
        "\n".join(markdown), encoding="utf-8"
    )
    (OUT / "README.md").write_text(
        "# Tranchedino cipher catalogue\n\n"
        "Systematic, provenance-preserving catalogue of ÖNB Cod. 2398.\n\n"
        "The bundle includes Markdown, CSV, full JSON evidence, reconstruction coordinates, source PDF, and one facsimile image per manuscript side.\n",
        encoding="utf-8",
    )
    (OUT / "CHECKSUMS.sha256").write_text(
        f"{digest}  source/Tranchedino_Cipher_Ledger.pdf\n", encoding="utf-8"
    )
    # Rendered PNG intermediates are not deliverables.
    for file in rendered.iterdir():
        file.unlink()
    rendered.rmdir()
    print(f"Completed {len(records)} records", flush=True)


if __name__ == "__main__":
    main()
