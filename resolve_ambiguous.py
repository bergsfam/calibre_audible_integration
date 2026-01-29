#!/usr/bin/env python3
import argparse
import sys
from typing import Dict, List, Optional

from calibre_audible_sync import (
    DEFAULT_LIBRARY,
    CalibreBook,
    add_placeholder,
    build_metadata_fields,
    load_audible_csv,
    parse_bool,
    parse_calibredb_list,
    resolve_custom_columns,
    run_calibredb,
    set_metadata,
)


def load_ambiguous_csv(path: str) -> Dict[str, Dict[str, str]]:
    import csv

    with open(path, newline="", encoding="utf-8") as handle:
        reader = csv.DictReader(handle)
        rows = {row.get("asin", "").strip(): row for row in reader}
    return rows


def load_mapping_csv(path: str) -> List[Dict[str, str]]:
    import csv

    with open(path, newline="", encoding="utf-8") as handle:
        reader = csv.DictReader(handle)
        return [row for row in reader]


def find_audible_row_by_asin(
    audible_rows: List[Dict[str, str]], asin: str
) -> Optional[Dict[str, str]]:
    for row in audible_rows:
        if (row.get("asin") or "").strip() == asin:
            return row
    return None


def find_calibre_by_id(books: List[CalibreBook], book_id: int) -> Optional[CalibreBook]:
    for book in books:
        if book.book_id == book_id:
            return book
    return None


def find_calibre_by_title(books: List[CalibreBook], title: str) -> List[CalibreBook]:
    normalized = title.strip().lower()
    return [book for book in books if book.title.strip().lower() == normalized]


def resolve(args: argparse.Namespace) -> int:
    ambiguous_rows = load_ambiguous_csv(args.ambiguous_csv)
    asin = args.asin.strip()
    if asin not in ambiguous_rows:
        raise RuntimeError(f"ASIN not found in ambiguous.csv: {asin}")

    audible_rows = load_audible_csv(args.audible_csv)
    audible_row = find_audible_row_by_asin(audible_rows, asin)
    if not audible_row:
        raise RuntimeError(f"ASIN not found in Audible CSV: {asin}")

    list_args = [
        "list",
        "--for-machine",
        "--fields",
        "id,title,authors,formats,*audible_asin,*format_status",
    ]
    calibre_output = run_calibredb(list_args, args.calibre_library)
    calibre_books = parse_calibredb_list(calibre_output)

    custom_columns = resolve_custom_columns(args.calibre_library)

    if args.audible_only:
        existing = next(
            (book for book in calibre_books if book.audible_asin == asin), None
        )
        if existing:
            print(
                f"ASIN already present in Calibre (book id {existing.book_id}); skipping placeholder."
            )
            return 0
        placeholder_id = add_placeholder(audible_row, args.calibre_library, args.dry_run)
        if placeholder_id is None:
            return 0
        fields = build_metadata_fields(
            audible_row,
            match_score=int(ambiguous_rows[asin].get("top_score") or 0),
            format_status="Audible only",
            custom_columns=custom_columns,
        )
        set_metadata(placeholder_id, fields, args.calibre_library, args.dry_run)
        print(f"Resolved ASIN {asin} to new Audible-only placeholder id {placeholder_id}")
        return 0

    if args.calibre_id is None and args.calibre_title is None:
        raise RuntimeError("Provide --calibre-id or --calibre-title, or use --audible-only.")

    matched_book: Optional[CalibreBook] = None
    if args.calibre_id is not None:
        matched_book = find_calibre_by_id(calibre_books, args.calibre_id)
        if not matched_book:
            raise RuntimeError(f"Calibre book id not found: {args.calibre_id}")
    else:
        matches = find_calibre_by_title(calibre_books, args.calibre_title)
        if not matches:
            raise RuntimeError(f"No Calibre titles matched: {args.calibre_title}")
        if len(matches) > 1:
            ids = ", ".join(str(book.book_id) for book in matches)
            raise RuntimeError(
                f"Multiple Calibre titles matched '{args.calibre_title}'. "
                f"Use --calibre-id. Matches: {ids}"
            )
        matched_book = matches[0]

    format_status = "Both" if matched_book.formats else "Audible only"
    fields = build_metadata_fields(
        audible_row,
        match_score=int(ambiguous_rows[asin].get("top_score") or 0),
        format_status=format_status,
        custom_columns=custom_columns,
    )
    set_metadata(matched_book.book_id, fields, args.calibre_library, args.dry_run)
    print(
        f"Resolved ASIN {asin} to Calibre id {matched_book.book_id} "
        f"({matched_book.title})"
    )
    return 0


def list_ambiguous(args: argparse.Namespace) -> int:
    rows = load_mapping_csv(args.ambiguous_csv)
    if not rows:
        print("No ambiguous rows found.")
        return 0
    limit = args.limit if args.limit is not None else len(rows)
    for row in rows[:limit]:
        asin = row.get("asin", "").strip()
        title = row.get("audible_title", "").strip()
        authors = row.get("audible_authors", "").strip()
        top_score = row.get("top_score", "").strip()
        candidates = row.get("candidates", "").strip()
        print(f"{asin} | {top_score} | {title} | {authors}")
        if candidates:
            print(f"  candidates: {candidates}")
    return 0


def batch_resolve(args: argparse.Namespace) -> int:
    ambiguous_rows = load_ambiguous_csv(args.ambiguous_csv)
    audible_rows = load_audible_csv(args.audible_csv)

    list_args = [
        "list",
        "--for-machine",
        "--fields",
        "id,title,authors,formats,*audible_asin,*format_status",
    ]
    calibre_output = run_calibredb(list_args, args.calibre_library)
    calibre_books = parse_calibredb_list(calibre_output)
    custom_columns = resolve_custom_columns(args.calibre_library)

    mappings = load_mapping_csv(args.mapping_csv)
    if not mappings:
        print("No mappings found.")
        return 0

    for mapping in mappings:
        asin = (mapping.get("asin") or "").strip()
        if not asin:
            print("Skipping mapping row without asin.")
            continue
        if asin not in ambiguous_rows:
            print(f"Skipping {asin}: not in ambiguous.csv")
            continue
        audible_row = find_audible_row_by_asin(audible_rows, asin)
        if not audible_row:
            print(f"Skipping {asin}: not in Audible CSV")
            continue

        audible_only_raw = (mapping.get("audible_only") or "").strip()
        audible_only = False
        if audible_only_raw:
            audible_only = parse_bool(audible_only_raw)

        calibre_id_raw = (mapping.get("calibre_id") or "").strip()
        calibre_title = (mapping.get("calibre_title") or "").strip()

        if audible_only:
            existing = next(
                (book for book in calibre_books if book.audible_asin == asin), None
            )
            if existing:
                print(
                    f"Skipping {asin}: already present in Calibre (id {existing.book_id})"
                )
                continue
            placeholder_id = add_placeholder(
                audible_row, args.calibre_library, args.dry_run
            )
            if placeholder_id is None:
                continue
            fields = build_metadata_fields(
                audible_row,
                match_score=int(ambiguous_rows[asin].get("top_score") or 0),
                format_status="Audible only",
                custom_columns=custom_columns,
            )
            set_metadata(placeholder_id, fields, args.calibre_library, args.dry_run)
            print(f"{asin}: created placeholder id {placeholder_id}")
            continue

        matched_book: Optional[CalibreBook] = None
        if calibre_id_raw:
            try:
                calibre_id = int(calibre_id_raw)
            except ValueError:
                print(f"Skipping {asin}: invalid calibre_id '{calibre_id_raw}'")
                continue
            matched_book = find_calibre_by_id(calibre_books, calibre_id)
            if not matched_book:
                print(f"Skipping {asin}: calibre id not found {calibre_id}")
                continue
        elif calibre_title:
            matches = find_calibre_by_title(calibre_books, calibre_title)
            if not matches:
                print(f"Skipping {asin}: no title match '{calibre_title}'")
                continue
            if len(matches) > 1:
                ids = ", ".join(str(book.book_id) for book in matches)
                print(
                    f"Skipping {asin}: multiple title matches '{calibre_title}' ({ids})"
                )
                continue
            matched_book = matches[0]
        else:
            print(f"Skipping {asin}: no calibre_id, calibre_title, or audible_only")
            continue

        format_status = "Both" if matched_book.formats else "Audible only"
        fields = build_metadata_fields(
            audible_row,
            match_score=int(ambiguous_rows[asin].get("top_score") or 0),
            format_status=format_status,
            custom_columns=custom_columns,
        )
        set_metadata(matched_book.book_id, fields, args.calibre_library, args.dry_run)
        print(f"{asin}: linked to Calibre id {matched_book.book_id}")

    return 0


def export_mapping(args: argparse.Namespace) -> int:
    ambiguous_rows = load_mapping_csv(args.ambiguous_csv)
    if not ambiguous_rows:
        print("No ambiguous rows found.")
        return 0
    output_path = args.output
    if output_path is None:
        output_path = "mapping_template.csv"
    header = [
        "asin",
        "audible_title",
        "audible_authors",
        "calibre_id",
        "calibre_title",
        "audible_only",
        "top_score",
        "candidates",
    ]
    import csv

    with open(output_path, "w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=header)
        writer.writeheader()
        for row in ambiguous_rows:
            asin = (row.get("asin") or "").strip()
            if not asin:
                continue
            writer.writerow(
                {
                    "asin": asin,
                    "audible_title": (row.get("audible_title") or "").strip(),
                    "audible_authors": (row.get("audible_authors") or "").strip(),
                    "calibre_id": "",
                    "calibre_title": "",
                    "audible_only": "",
                    "top_score": (row.get("top_score") or "").strip(),
                    "candidates": (row.get("candidates") or "").strip(),
                }
            )
    print(f"Wrote mapping template: {output_path}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="resolve_ambiguous")
    subparsers = parser.add_subparsers(dest="command", required=True)

    list_parser = subparsers.add_parser(
        "list", help="List ambiguous entries from a report"
    )
    list_parser.add_argument(
        "--ambiguous-csv", required=True, help="Path to ambiguous.csv report"
    )
    list_parser.add_argument(
        "--limit",
        type=int,
        help="Limit number of rows printed",
    )

    resolve_parser = subparsers.add_parser(
        "resolve", help="Resolve an ambiguous match by ASIN"
    )
    resolve_parser.add_argument(
        "--ambiguous-csv", required=True, help="Path to ambiguous.csv report"
    )
    resolve_parser.add_argument(
        "--audible-csv", required=True, help="Path to Audible CSV export"
    )
    resolve_parser.add_argument("--asin", required=True, help="ASIN to resolve")
    resolve_parser.add_argument(
        "--calibre-library",
        default=DEFAULT_LIBRARY,
        help="Path to Calibre library",
    )
    resolve_parser.add_argument(
        "--calibre-id",
        type=int,
        help="Calibre book id to link",
    )
    resolve_parser.add_argument(
        "--calibre-title",
        help="Exact Calibre title to link (must be unique)",
    )
    resolve_parser.add_argument(
        "--audible-only",
        type=parse_bool,
        default=False,
        help="Create Audible-only placeholder (true/false)",
    )
    resolve_parser.add_argument(
        "--dry-run",
        type=parse_bool,
        default=True,
        help="Print actions without Calibre changes (true/false)",
    )

    batch_parser = subparsers.add_parser(
        "batch-resolve", help="Resolve ambiguous matches from a mapping file"
    )
    batch_parser.add_argument(
        "--ambiguous-csv", required=True, help="Path to ambiguous.csv report"
    )
    batch_parser.add_argument(
        "--audible-csv", required=True, help="Path to Audible CSV export"
    )
    batch_parser.add_argument(
        "--mapping-csv",
        required=True,
        help="CSV mapping file with asin and resolution info",
    )
    batch_parser.add_argument(
        "--calibre-library",
        default=DEFAULT_LIBRARY,
        help="Path to Calibre library",
    )
    batch_parser.add_argument(
        "--dry-run",
        type=parse_bool,
        default=True,
        help="Print actions without Calibre changes (true/false)",
    )

    export_parser = subparsers.add_parser(
        "export-mapping",
        help="Export a template mapping CSV from ambiguous.csv",
    )
    export_parser.add_argument(
        "--ambiguous-csv", required=True, help="Path to ambiguous.csv report"
    )
    export_parser.add_argument(
        "--output",
        help="Output path for mapping template (default: mapping_template.csv)",
    )

    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    if args.command == "list":
        return list_ambiguous(args)
    if args.command == "resolve":
        return resolve(args)
    if args.command == "batch-resolve":
        return batch_resolve(args)
    if args.command == "export-mapping":
        return export_mapping(args)
    parser.print_help()
    return 1


if __name__ == "__main__":
    sys.exit(main())
