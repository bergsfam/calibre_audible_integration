#!/usr/bin/env python3
import argparse
import csv
import datetime as dt
import json
import os
import re
import subprocess
import sys
from dataclasses import dataclass
from difflib import SequenceMatcher
from typing import Dict, Iterable, List, Optional, Sequence, Tuple


DEFAULT_LIBRARY = os.path.expanduser("~/Calibre")

RE_PUNCT = re.compile(r"[^\w\s]")
RE_SPACE = re.compile(r"\s+")
RE_AUTHOR_SPLIT = re.compile(r"\s*(?:,|&| and )\s*", re.IGNORECASE)
CUSTOM_FIELDS = {
    "audible_owned",
    "audible_asin",
    "audible_narrators",
    "audible_minutes",
    "audible_purchase_date",
    "audible_match_score",
    "format_status",
    "audible_series",
    "audible_series_sequence",
    "audible_release_date",
}


@dataclass
class CalibreBook:
    book_id: int
    title: str
    authors: str
    formats: List[str]
    audible_asin: Optional[str]
    format_status: Optional[str]
    normalized_title: str
    author_tokens: List[str]
    combined_tokens: List[str]


@dataclass
class MatchResult:
    calibre_book: Optional[CalibreBook]
    score: int
    method: str
    candidates: Optional[List[Tuple[CalibreBook, int]]] = None


def parse_bool(value: str) -> bool:
    if isinstance(value, bool):
        return value
    normalized = value.strip().lower()
    if normalized in {"true", "1", "yes", "y"}:
        return True
    if normalized in {"false", "0", "no", "n"}:
        return False
    raise argparse.ArgumentTypeError(f"Invalid boolean value: {value}")


def normalize_text(text: str) -> str:
    if text is None:
        return ""
    lowered = text.lower().replace("&", " and ")
    cleaned = RE_PUNCT.sub(" ", lowered)
    collapsed = RE_SPACE.sub(" ", cleaned).strip()
    return collapsed


def tokenize(text: str) -> List[str]:
    normalized = normalize_text(text)
    if not normalized:
        return []
    return normalized.split()


def tokenize_authors(authors: Iterable[str]) -> List[str]:
    tokens: List[str] = []
    for author in authors:
        tokens.extend(tokenize(author))
    return tokens


def split_authors(raw: str) -> List[str]:
    if not raw:
        return []
    return [part.strip() for part in RE_AUTHOR_SPLIT.split(raw) if part.strip()]


def token_overlap(tokens_a: Sequence[str], tokens_b: Sequence[str]) -> bool:
    return bool(set(tokens_a) & set(tokens_b))


def token_similarity(tokens_a: Sequence[str], tokens_b: Sequence[str]) -> int:
    if not tokens_a or not tokens_b:
        return 0
    text_a = " ".join(sorted(set(tokens_a)))
    text_b = " ".join(sorted(set(tokens_b)))
    ratio = SequenceMatcher(None, text_a, text_b).ratio()
    return int(round(ratio * 100))


def run_calibredb(args: List[str], library: str) -> str:
    command = ["calibredb"] + args + ["--with-library", library]
    result = subprocess.run(command, check=False, text=True, capture_output=True)
    if result.returncode != 0:
        raise RuntimeError(
            f"calibredb failed: {' '.join(command)}\n{result.stderr.strip()}"
        )
    return result.stdout


def parse_calibredb_list(output: str) -> List[CalibreBook]:
    data = json.loads(output)
    books: List[CalibreBook] = []
    for row in data:
        audible_asin = row.get("audible_asin", row.get("*audible_asin"))
        format_status = row.get("format_status", row.get("*format_status"))
        title = row.get("title") or ""
        authors = row.get("authors") or ""
        formats = row.get("formats") or []
        author_parts = split_authors(authors)
        author_tokens = tokenize_authors(author_parts)
        combined_tokens = tokenize(title) + author_tokens
        books.append(
            CalibreBook(
                book_id=int(row["id"]),
                title=title,
                authors=authors,
                formats=formats,
                audible_asin=audible_asin,
                format_status=format_status,
                normalized_title=normalize_text(title),
                author_tokens=author_tokens,
                combined_tokens=combined_tokens,
            )
        )
    return books


def load_audible_csv(path: str) -> List[Dict[str, str]]:
    with open(path, newline="", encoding="utf-8") as handle:
        reader = csv.DictReader(handle)
        rows = [row for row in reader]
    return rows


def parse_iso_date(value: str) -> Optional[str]:
    if not value:
        return None
    try:
        if "T" in value:
            parsed = dt.datetime.fromisoformat(value.replace("Z", "+00:00"))
            return parsed.date().isoformat()
        return dt.date.fromisoformat(value).isoformat()
    except ValueError:
        return None


def parse_int(value: str) -> Optional[int]:
    if value is None:
        return None
    stripped = str(value).strip()
    if not stripped:
        return None
    try:
        return int(float(stripped))
    except ValueError:
        return None


def find_match(
    audible_row: Dict[str, str],
    calibre_books: List[CalibreBook],
    calibre_by_asin: Dict[str, CalibreBook],
    match_threshold: int,
    review_threshold: int,
) -> MatchResult:
    asin = (audible_row.get("asin") or "").strip()
    if asin and asin in calibre_by_asin:
        return MatchResult(
            calibre_book=calibre_by_asin[asin],
            score=100,
            method="asin_column",
        )

    audible_title = audible_row.get("title") or ""
    audible_authors = split_authors(audible_row.get("authors") or "")
    audible_author_tokens = tokenize_authors(audible_authors)
    normalized_title = normalize_text(audible_title)

    exact_candidates = [
        book
        for book in calibre_books
        if book.normalized_title == normalized_title
        and token_overlap(book.author_tokens, audible_author_tokens)
    ]
    if len(exact_candidates) == 1:
        return MatchResult(
            calibre_book=exact_candidates[0],
            score=95,
            method="exact_title_author",
        )

    audible_tokens = tokenize(audible_title) + audible_author_tokens
    scored: List[Tuple[CalibreBook, int]] = []
    for book in calibre_books:
        score = token_similarity(audible_tokens, book.combined_tokens)
        scored.append((book, score))
    scored.sort(key=lambda item: item[1], reverse=True)

    if not scored:
        return MatchResult(calibre_book=None, score=0, method="unmatched")

    top_book, top_score = scored[0]
    second_score = scored[1][1] if len(scored) > 1 else -1

    if top_score >= match_threshold and (top_score - second_score) >= 3:
        return MatchResult(
            calibre_book=top_book,
            score=top_score,
            method="fuzzy_auto",
        )

    if review_threshold <= top_score < match_threshold:
        return MatchResult(
            calibre_book=None,
            score=top_score,
            method="ambiguous",
            candidates=scored[:5],
        )

    return MatchResult(calibre_book=None, score=top_score, method="unmatched")


def resolve_custom_columns(library: str) -> Dict[str, str]:
    try:
        output = run_calibredb(["custom_columns", "--for-machine"], library)
    except RuntimeError:
        return {}
    try:
        data = json.loads(output)
    except json.JSONDecodeError:
        return {}
    mapping: Dict[str, str] = {}
    if isinstance(data, dict):
        for key in data.keys():
            if not isinstance(key, str):
                continue
            lookup = key
            bare = lookup.lstrip("#")
            mapping[bare] = lookup
    elif isinstance(data, list):
        for entry in data:
            if not isinstance(entry, dict):
                continue
            lookup = entry.get("label")
            if not isinstance(lookup, str):
                continue
            bare = lookup.lstrip("#")
            mapping[bare] = lookup
    return mapping


def resolve_field_name(name: str, custom_columns: Dict[str, str]) -> str:
    if name in custom_columns:
        return custom_columns[name]
    if name in CUSTOM_FIELDS:
        return f"#{name}"
    return name


def build_metadata_fields(
    audible_row: Dict[str, str],
    match_score: int,
    format_status: str,
    custom_columns: Dict[str, str],
) -> List[str]:
    fields = [
        f"{resolve_field_name('audible_owned', custom_columns)}:Yes",
        f"{resolve_field_name('audible_asin', custom_columns)}:{audible_row.get('asin', '').strip()}",
        f"{resolve_field_name('audible_narrators', custom_columns)}:{', '.join(split_authors(audible_row.get('narrators') or ''))}",
        f"{resolve_field_name('audible_match_score', custom_columns)}:{match_score}",
        f"{resolve_field_name('format_status', custom_columns)}:{format_status}",
    ]

    runtime_minutes = parse_int(audible_row.get("runtime_length_min", ""))
    if runtime_minutes is not None:
        fields.append(
            f"{resolve_field_name('audible_minutes', custom_columns)}:{runtime_minutes}"
        )

    purchase_date = parse_iso_date(audible_row.get("purchase_date") or "")
    if purchase_date:
        fields.append(f"{resolve_field_name('audible_purchase_date', custom_columns)}:{purchase_date}")

    if "audible_series" in custom_columns:
        series_title = (audible_row.get("series_title") or "").strip()
        if series_title:
            fields.append(f"{resolve_field_name('audible_series', custom_columns)}:{series_title}")
    if "audible_series_sequence" in custom_columns:
        series_sequence = (audible_row.get("series_sequence") or "").strip()
        if series_sequence:
            fields.append(
                f"{resolve_field_name('audible_series_sequence', custom_columns)}:{series_sequence}"
            )
    if "audible_release_date" in custom_columns:
        release_date = parse_iso_date(audible_row.get("release_date") or "")
        if release_date:
            fields.append(
                f"{resolve_field_name('audible_release_date', custom_columns)}:{release_date}"
            )

    return fields


def set_metadata(
    book_id: int,
    fields: List[str],
    library: str,
    dry_run: bool,
) -> None:
    if dry_run:
        return
    args = ["set_metadata", str(book_id)]
    for field in fields:
        args.extend(["--field", field])
    run_calibredb(args, library)


def add_placeholder(
    audible_row: Dict[str, str],
    library: str,
    dry_run: bool,
) -> Optional[int]:
    if dry_run:
        return None
    title = audible_row.get("title") or "Untitled"
    authors = ", ".join(split_authors(audible_row.get("authors") or "")) or "Unknown"
    output = run_calibredb(
        [
            "add",
            "--empty",
            "--title",
            title,
            "--authors",
            authors,
            "--tags",
            "Audible",
        ],
        library,
    )
    match = re.search(r"\b(\d+)\b", output)
    if not match:
        raise RuntimeError(f"Unable to parse new book id from calibredb output: {output}")
    return int(match.group(1))


def write_csv(path: str, header: List[str], rows: List[Dict[str, str]]) -> None:
    with open(path, "w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=header)
        writer.writeheader()
        writer.writerows(rows)


def sync(args: argparse.Namespace) -> int:
    library = args.calibre_library
    audible_rows = load_audible_csv(args.audible_csv)

    list_args = [
        "list",
        "--for-machine",
        "--fields",
        "id,title,authors,formats,*audible_asin,*format_status",
    ]
    calibre_output = run_calibredb(list_args, library)
    calibre_books = parse_calibredb_list(calibre_output)
    calibre_by_asin = {
        book.audible_asin: book for book in calibre_books if book.audible_asin
    }

    custom_columns = resolve_custom_columns(library)

    report_dir = args.report_dir
    os.makedirs(report_dir, exist_ok=True)

    matched_rows: List[Dict[str, str]] = []
    ambiguous_rows: List[Dict[str, str]] = []
    audible_only_rows: List[Dict[str, str]] = []

    matched_book_ids = set()

    for row in audible_rows:
        result = find_match(
            row,
            calibre_books,
            calibre_by_asin,
            args.match_threshold,
            args.review_threshold,
        )

        if result.calibre_book:
            book = result.calibre_book
            matched_book_ids.add(book.book_id)
            if book.audible_asin:
                matched_rows.append(
                    {
                        "asin": row.get("asin", ""),
                        "audible_title": row.get("title", ""),
                        "audible_authors": row.get("authors", ""),
                        "calibre_id": str(book.book_id),
                        "calibre_title": book.title,
                        "calibre_authors": book.authors,
                        "calibre_audible_asin": book.audible_asin or "",
                        "score": str(result.score),
                        "method": f"{result.method}_skip_existing",
                    }
                )
                continue
            format_status = "Both" if book.formats else "Audible only"
            fields = build_metadata_fields(
                row,
                result.score,
                format_status,
                custom_columns,
            )
            set_metadata(book.book_id, fields, library, args.dry_run)
            matched_rows.append(
                {
                    "asin": row.get("asin", ""),
                    "audible_title": row.get("title", ""),
                    "audible_authors": row.get("authors", ""),
                    "calibre_id": str(book.book_id),
                    "calibre_title": book.title,
                    "calibre_authors": book.authors,
                    "calibre_audible_asin": book.audible_asin or "",
                    "score": str(result.score),
                    "method": result.method,
                }
            )
            continue

        if result.method == "ambiguous":
            candidates = result.candidates or []
            candidate_text = "; ".join(
                f"{candidate.book_id}:{score}:{candidate.title}"
                for candidate, score in candidates
            )
            ambiguous_rows.append(
                {
                    "asin": row.get("asin", ""),
                    "audible_title": row.get("title", ""),
                    "audible_authors": row.get("authors", ""),
                    "top_score": str(result.score),
                    "candidates": candidate_text,
                }
            )
            continue

        audible_only_rows.append(
            {
                "asin": row.get("asin", ""),
                "audible_title": row.get("title", ""),
                "audible_authors": row.get("authors", ""),
                "method": result.method,
            }
        )

        if args.create_placeholders and row.get("asin", "").strip() not in calibre_by_asin:
            placeholder_id = add_placeholder(row, library, args.dry_run)
            if placeholder_id is not None:
                fields = build_metadata_fields(
                    row,
                    result.score,
                    "Audible only",
                    custom_columns,
                )
                set_metadata(placeholder_id, fields, library, args.dry_run)

    for book in calibre_books:
        if book.book_id in matched_book_ids:
            continue
        if book.format_status in {"Both", "Audible only"}:
            continue
        if args.dry_run:
            continue
        set_metadata(
            book.book_id,
            [f"{resolve_field_name('format_status', custom_columns)}:Ebook only"],
            library,
            args.dry_run,
        )

    write_csv(
        os.path.join(report_dir, "matched.csv"),
        [
            "asin",
            "audible_title",
            "audible_authors",
            "calibre_id",
            "calibre_title",
            "calibre_authors",
            "calibre_audible_asin",
            "score",
            "method",
        ],
        matched_rows,
    )
    write_csv(
        os.path.join(report_dir, "ambiguous.csv"),
        ["asin", "audible_title", "audible_authors", "top_score", "candidates"],
        ambiguous_rows,
    )
    write_csv(
        os.path.join(report_dir, "audible_only.csv"),
        ["asin", "audible_title", "audible_authors", "method"],
        audible_only_rows,
    )

    summary_path = os.path.join(report_dir, "summary.txt")
    with open(summary_path, "w", encoding="utf-8") as handle:
        handle.write(f"Timestamp: {dt.datetime.now().isoformat()}\n")
        handle.write(f"Audible rows: {len(audible_rows)}\n")
        handle.write(f"Matched: {len(matched_rows)}\n")
        handle.write(f"Ambiguous: {len(ambiguous_rows)}\n")
        handle.write(f"Audible only: {len(audible_only_rows)}\n")
        handle.write(f"Dry run: {args.dry_run}\n")
        handle.write(f"Create placeholders: {args.create_placeholders}\n")
        handle.write(f"Match threshold: {args.match_threshold}\n")
        handle.write(f"Review threshold: {args.review_threshold}\n")
        handle.write(f"Calibre library: {library}\n")
        handle.write(f"Audible CSV: {args.audible_csv}\n")

    print(f"Report dir: {report_dir}")
    print(
        "Summary: audible_rows={audible} matched={matched} ambiguous={ambiguous} "
        "audible_only={audible_only} dry_run={dry_run}".format(
            audible=len(audible_rows),
            matched=len(matched_rows),
            ambiguous=len(ambiguous_rows),
            audible_only=len(audible_only_rows),
            dry_run=args.dry_run,
        )
    )

    return 0


def print_columns() -> int:
    columns = [
        ("audible_owned", "Yes/No"),
        ("audible_asin", "Text"),
        ("audible_narrators", "Text"),
        ("audible_minutes", "Int"),
        ("audible_purchase_date", "Date"),
        ("audible_match_score", "Int"),
        ("format_status", "Enum: Ebook only, Audible only, Both, Unknown"),
    ]
    optional = [
        ("audible_series", "Text"),
        ("audible_series_sequence", "Text"),
        ("audible_release_date", "Date"),
    ]

    for name, col_type in columns:
        print(f"{name}: {col_type}")
    print("")
    print("Optional (if present):")
    for name, col_type in optional:
        print(f"{name}: {col_type}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="calibre_audible_sync")
    subparsers = parser.add_subparsers(dest="command", required=True)

    sync_parser = subparsers.add_parser("sync", help="Sync Audible CSV into Calibre")
    sync_parser.add_argument("--audible-csv", required=True, help="Path to Audible CSV")
    sync_parser.add_argument(
        "--calibre-library",
        default=DEFAULT_LIBRARY,
        help="Path to Calibre library",
    )
    sync_parser.add_argument(
        "--create-placeholders",
        type=parse_bool,
        default=True,
        help="Create placeholders for Audible-only titles (true/false)",
    )
    sync_parser.add_argument(
        "--dry-run",
        type=parse_bool,
        default=True,
        help="Generate reports without Calibre changes (true/false)",
    )
    sync_parser.add_argument(
        "--match-threshold",
        type=int,
        default=90,
        help="Auto-match threshold",
    )
    sync_parser.add_argument(
        "--review-threshold",
        type=int,
        default=75,
        help="Ambiguous review threshold",
    )
    sync_parser.add_argument(
        "--report-dir",
        default=f"./reports_{dt.datetime.now().strftime('%Y%m%d_%H%M%S')}",
        help="Directory for report output",
    )

    subparsers.add_parser(
        "print-columns",
        help="Print required Calibre custom columns",
    )

    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    if args.command == "sync":
        return sync(args)
    if args.command == "print-columns":
        return print_columns()
    parser.print_help()
    return 1


if __name__ == "__main__":
    sys.exit(main())
