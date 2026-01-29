# calibre_audible_sync

Python CLI to sync Audible ownership metadata (from audible-cli CSV export) into an existing Calibre library. Uses `calibredb` only and does not import audiobook files.

## Setup

Install and use `audible-cli` to export your library:

```bash
audible library export --format csv --output library.csv
```

Create these 7 Calibre custom columns (recommended minimum):

1) Audible Owned
   - Column type: Yes/No
   - Lookup name: audible_owned
   - Column heading: Audible Owned
2) Audible ASIN
   - Column type: Text (shown in the book list)
   - Lookup name: audible_asin
   - Column heading: Audible ASIN
3) Audible Narrators
   - Column type: Text (shown in the book list)
   - Lookup name: audible_narrators
   - Column heading: Narrators
4) Audible Runtime (minutes)
   - Column type: Integers
   - Lookup name: audible_minutes
   - Column heading: Audible Min
5) Audible Purchase Date
   - Column type: Date
   - Lookup name: audible_purchase_date
   - Column heading: Audible Purchase
6) Match Score
   - Column type: Integers
   - Lookup name: audible_match_score
   - Column heading: Match Score
7) Format Status
   - Column type: Enumeration
   - Lookup name: format_status
   - Column heading: Format Status
   - Allowed values (exactly): Ebook only, Audible only, Both, Unknown

Optional columns:

8) Audible Series
   - Column type: Text
   - Lookup name: audible_series
   - Heading: Audible Series
9) Audible Series Sequence
   - Column type: Text
   - Lookup name: audible_series_sequence
   - Heading: Series #

## Commands

Print required custom columns:

```bash
python calibre_audible_sync.py print-columns
```

Sync Audible CSV into Calibre:

```bash
python calibre_audible_sync.py sync --audible-csv /path/to/library.csv
```

Common options:

```bash
python calibre_audible_sync.py sync \
  --audible-csv /path/to/library.csv \
  --calibre-library ~/Calibre \
  --create-placeholders true \
  --dry-run true \
  --match-threshold 90 \
  --review-threshold 75
```

Reports are written to `./reports_<timestamp>` by default.

## Typical workflow

1. Run a dry-run to generate reports:
```bash
python calibre_audible_sync.py sync --audible-csv /path/to/library.csv --dry-run true
```

2. Review `ambiguous.csv`:
   - Each row includes candidate Calibre IDs and scores.
   - Manually resolve matches by editing the Calibre book and setting `audible_asin`,
     or adjust the title/author in Calibre so it matches on the next run.

3. Re-run with changes applied:
```bash
python calibre_audible_sync.py sync --audible-csv /path/to/library.csv --dry-run false
```

## Resolve ambiguous matches with a helper script

List ambiguous entries:

```bash
python resolve_ambiguous.py list \
  --ambiguous-csv /path/to/reports_YYYYMMDD_HHMMSS/ambiguous.csv \
  --limit 20
```

Link an ambiguous Audible ASIN to a specific Calibre book id:

```bash
python resolve_ambiguous.py resolve \
  --ambiguous-csv /path/to/reports_YYYYMMDD_HHMMSS/ambiguous.csv \
  --audible-csv /path/to/library.csv \
  --asin B000123456 \
  --calibre-id 123 \
  --dry-run false
```

Create an Audible-only placeholder for that ASIN:

```bash
python resolve_ambiguous.py resolve \
  --ambiguous-csv /path/to/reports_YYYYMMDD_HHMMSS/ambiguous.csv \
  --audible-csv /path/to/library.csv \
  --asin B000123456 \
  --audible-only true \
  --dry-run false
```

Batch resolve from a mapping CSV:

```bash
python resolve_ambiguous.py batch-resolve \
  --ambiguous-csv /path/to/reports_YYYYMMDD_HHMMSS/ambiguous.csv \
  --audible-csv /path/to/library.csv \
  --mapping-csv /path/to/mapping.csv \
  --dry-run false
```

Mapping CSV format (header required):

```csv
asin,calibre_id,calibre_title,audible_only
B000123456,123,,false
B000987654,,Exact Calibre Title,false
B000111222,,,true
```

Export a template mapping CSV from `ambiguous.csv`:

```bash
python resolve_ambiguous.py export-mapping \
  --ambiguous-csv /path/to/reports_YYYYMMDD_HHMMSS/ambiguous.csv \
  --output /path/to/mapping_template.csv
```

The template includes `audible_title`, `audible_authors`, `top_score`, and `candidates` columns populated from the report.
