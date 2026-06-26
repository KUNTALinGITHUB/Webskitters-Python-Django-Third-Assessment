

import csv
import os
import re
from collections import defaultdict


#  CUSTOM EXCEPTIONS


class InvalidHeaderError(Exception):
    """Raised when the CSV is missing required column headers."""
    pass

class InvalidAssetCodeError(Exception):
    """Raised when an asset_code does not match the pattern A-XXX."""
    pass


REQUIRED_HEADERS   = {"asset_code", "day_no", "asset_type", "filename", "owner_email", "tags"}
VALID_ASSET_TYPES  = {"image", "video", "css", "js", "csv"}
ASSET_CODE_PATTERN = re.compile(r"^A-\d{3}$")          # A- followed by exactly 3 digits
EMAIL_PATTERN      = re.compile(r"^[\w\.-]+@[\w\.-]+\.\w+$")  # basic valid email
DAY_MAX            = 40
DAY_WARN_THRESHOLD = 30
MANIFEST_FILE      = "assets_manifest.csv"
ASSETS_FOLDER      = "assets"


def flatten_tags(tag_string, separator="|"):
    
    if separator not in tag_string:
        return [tag_string.strip()]                   # base case
    first, _, rest = tag_string.partition(separator)
    return [first.strip()] + flatten_tags(rest, separator)  # recursive case

#  VALIDATION HELPERS


def validate_headers(headers):
    """Check all required columns exist; raise InvalidHeaderError if not."""
    missing = REQUIRED_HEADERS - set(headers)
    if missing:
        raise InvalidHeaderError(
            f"CSV is missing required headers: {', '.join(sorted(missing))}"
        )

def validate_asset_code(code):
    """Raise InvalidAssetCodeError if code doesn't match A-XXX pattern."""
    if not ASSET_CODE_PATTERN.match(code):
        raise InvalidAssetCodeError(
            f"Asset code '{code}' is invalid. Must be A- followed by exactly 3 digits."
        )

def validate_email(email):
    """Return True if email matches the expected pattern."""
    return bool(EMAIL_PATTERN.match(email))

def validate_day(day_str):
    """
    Convert day_str to int and validate range.
    Returns (day_int, warning_message_or_None).
    Raises ValueError if not a number.
    """
    day = int(day_str)
    if day < 1 or day > DAY_MAX:
        raise ValueError(f"day_no {day} is outside allowed range 1–{DAY_MAX}.")
    warning = None
    if day > DAY_WARN_THRESHOLD:
        warning = f"day_no {day} exceeds Day {DAY_WARN_THRESHOLD} (advanced topic warning)."
    return day, warning

def validate_asset_type(asset_type):
    """Return True if asset_type is in the allowed set."""
    return asset_type in VALID_ASSET_TYPES

#  CORE: Parse and validate the manifest


def parse_manifest(filepath):
    """
    Read the CSV, validate every row, and return:
      - valid_rows   : list of cleaned row dicts
      - invalid_rows : list of (row_number, row_dict, reason) tuples
      - warnings     : list of (row_number, message) tuples
    """
    valid_rows   = []
    invalid_rows = []
    warnings     = []

    with open(filepath, newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)

        validate_headers(reader.fieldnames or [])  # raises InvalidHeaderError if bad

        for row_num, row in enumerate(reader, start=2):  # row 1 = header
            reasons = []

            # 1. Validate asset_code
            try:
                validate_asset_code(row["asset_code"].strip())
            except InvalidAssetCodeError as e:
                reasons.append(str(e))

            # 2. Validate owner_email
            if not validate_email(row["owner_email"].strip()):
                reasons.append(
                    f"Invalid email '{row['owner_email']}'."
                )

            # 3. Validate day_no
            day_int = None
            day_warning = None
            try:
                day_int, day_warning = validate_day(row["day_no"].strip())
            except ValueError as e:
                reasons.append(str(e))

            # 4. Validate asset_type
            if not validate_asset_type(row["asset_type"].strip()):
                reasons.append(
                    f"Invalid asset_type '{row['asset_type']}'. "
                    f"Allowed: {', '.join(sorted(VALID_ASSET_TYPES))}."
                )

            # 5. Flatten tags (always succeeds, just processes the string)
            flat_tags = flatten_tags(row["tags"].strip())

            if reasons:
                invalid_rows.append((row_num, row, "; ".join(reasons)))
            else:
                # Build a clean version of the row
                clean = {
                    "asset_code": row["asset_code"].strip(),
                    "day_no"    : day_int,
                    "asset_type": row["asset_type"].strip(),
                    "filename"  : row["filename"].strip(),
                    "owner_email": row["owner_email"].strip(),
                    "tags"      : flat_tags,
                }
                valid_rows.append(clean)
                if day_warning:
                    warnings.append((row_num, day_warning))

    return valid_rows, invalid_rows, warnings


#  CORE: File comparison using SET OPERATIONS

def compare_files(valid_rows, assets_folder):
    """
    Use set operations to find:
      - missing files  : in manifest but NOT in folder
      - orphan files   : in folder but NOT in manifest
      - matched files  : in both
    """
    # Set of filenames referenced in the (valid) manifest
    manifest_filenames = {row["filename"] for row in valid_rows}

    # Set of filenames actually present in the assets/ folder
    folder_filenames = set(os.listdir(assets_folder))

    missing_files = manifest_filenames - folder_filenames   # in CSV, not on disk
    orphan_files  = folder_filenames  - manifest_filenames  # on disk, not in CSV
    matched_files = manifest_filenames & folder_filenames   # in both

    return missing_files, orphan_files, matched_files

#  CORE: Duplicate detection

def detect_duplicates(valid_rows):
    """
    Detect:
      - duplicate asset_codes (same code appears more than once)
      - conflicting filenames among valid rows
    Returns two dicts: {asset_code: [rows]}, {filename: [rows]}
    """
    code_groups = defaultdict(list)
    file_groups = defaultdict(list)

    for row in valid_rows:
        code_groups[row["asset_code"]].append(row)
        file_groups[row["filename"]].append(row)

    duplicate_codes = {k: v for k, v in code_groups.items() if len(v) > 1}
    duplicate_files = {k: v for k, v in file_groups.items() if len(v) > 1}

    return duplicate_codes, duplicate_files

#  CORE: Group assets by day and type


def group_assets(valid_rows):
    """
    Build a nested dict:
      { day_no: { asset_type: [rows] } }
    """
    grouped = defaultdict(lambda: defaultdict(list))
    for row in valid_rows:
        grouped[row["day_no"]][row["asset_type"]].append(row)
    return grouped

#  OUTPUT: deployment_ready_manifest.csv


def write_deployment_manifest(valid_rows, matched_files, duplicate_codes, output_path):
    """
    Write only rows that are:
      - Valid (passed all validations)
      - Have their file present in the assets/ folder
      - Are NOT part of a duplicate asset_code group

    Rows are sorted by day_no then asset_code using a lambda.
    Tags are joined back to pipe-separated string.
    """
    duplicate_code_set = set(duplicate_codes.keys())

    # Lambda: filter out rows whose asset_code is duplicated or file is missing
    deployment_ready = list(filter(
        lambda row: row["filename"] in matched_files and row["asset_code"] not in duplicate_code_set,
        valid_rows
    ))

    # Sort by day_no then asset_code (lambda used for sorting key)
    deployment_ready.sort(key=lambda row: (row["day_no"], row["asset_code"]))

    fieldnames = ["asset_code", "day_no", "asset_type", "filename", "owner_email", "tags"]

    with open(output_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        for row in deployment_ready:
            writer.writerow({
                "asset_code" : row["asset_code"],
                "day_no"     : row["day_no"],
                "asset_type" : row["asset_type"],
                "filename"   : row["filename"],
                "owner_email": row["owner_email"],
                "tags"       : "|".join(row["tags"]),  # flatten list back to string
            })

    return deployment_ready

#  OUTPUT: reconciliation_report.txt

def write_report(
    valid_rows, invalid_rows, warnings,
    missing_files, orphan_files, matched_files,
    duplicate_codes, duplicate_files,
    grouped, deployment_ready,
    output_path
):
    """Write a human-readable reconciliation report."""

    lines = []
    sep  = "=" * 60
    sep2 = "-" * 60

    lines.append(sep)
    lines.append("       TRAINING ASSET RECONCILIATION REPORT")
    lines.append(sep)
    lines.append("")

    
    lines.append("SUMMARY")
    lines.append(sep2)
    lines.append(f"  Total rows in manifest        : {len(valid_rows) + len(invalid_rows)}")
    lines.append(f"  Valid rows                    : {len(valid_rows)}")
    lines.append(f"  Invalid rows                  : {len(invalid_rows)}")
    lines.append(f"  Warnings (day > {DAY_WARN_THRESHOLD})          : {len(warnings)}")
    lines.append(f"  Missing files (in CSV, no file): {len(missing_files)}")
    lines.append(f"  Orphan files  (file, not CSV) : {len(orphan_files)}")
    lines.append(f"  Matched files                 : {len(matched_files)}")
    lines.append(f"  Duplicate asset codes         : {len(duplicate_codes)}")
    lines.append(f"  Conflicting filenames         : {len(duplicate_files)}")
    lines.append(f"  Deployment-ready assets       : {len(deployment_ready)}")
    lines.append("")


    lines.append("INVALID ROWS")
    lines.append(sep2)
    if invalid_rows:
        for row_num, row, reason in invalid_rows:
            lines.append(f"  Row {row_num}: {dict(row)}")
            lines.append(f"    Reason: {reason}")
    else:
        lines.append("  None.")
    lines.append("")

    lines.append("WARNINGS")
    lines.append(sep2)
    if warnings:
        for row_num, msg in warnings:
            lines.append(f"  Row {row_num}: {msg}")
    else:
        lines.append("  None.")
    lines.append("")


    lines.append("MISSING FILES  (referenced in manifest but not found in assets/)")
    lines.append(sep2)
    if missing_files:
        for f in sorted(missing_files):
            lines.append(f"  - {f}")
    else:
        lines.append("  None.")
    lines.append("")

    lines.append("ORPHAN FILES  (present in assets/ but not in manifest)")
    lines.append(sep2)
    if orphan_files:
        for f in sorted(orphan_files):
            lines.append(f"  - {f}")
    else:
        lines.append("  None.")
    lines.append("")

    lines.append("DUPLICATE ASSET CODES")
    lines.append(sep2)
    if duplicate_codes:
        for code, rows in sorted(duplicate_codes.items()):
            lines.append(f"  Code: {code}")
            for r in rows:
                lines.append(f"    → {r['filename']} (day {r['day_no']})")
    else:
        lines.append("  None.")
    lines.append("")

    
    lines.append("CONFLICTING FILENAMES")
    lines.append(sep2)
    if duplicate_files:
        for fname, rows in sorted(duplicate_files.items()):
            lines.append(f"  Filename: {fname}")
            for r in rows:
                lines.append(f"    → Code: {r['asset_code']} (day {r['day_no']})")
    else:
        lines.append("  None.")
    lines.append("")

    
    lines.append("ASSETS GROUPED BY DAY AND TYPE")
    lines.append(sep2)
    for day in sorted(grouped.keys()):
        lines.append(f"  Day {day}:")
        for atype in sorted(grouped[day].keys()):
            filenames = sorted([r["filename"] for r in grouped[day][atype]])
            lines.append(f"    [{atype}] → {', '.join(filenames)}")
    lines.append("")

    # Deployment-ready list 
    lines.append("DEPLOYMENT-READY ASSETS")
    lines.append(sep2)
    if deployment_ready:
        for row in deployment_ready:
            lines.append(f"  {row['asset_code']} | Day {row['day_no']} | {row['asset_type']} | {row['filename']}")
    else:
        lines.append("  None.")
    lines.append("")

    lines.append(sep)
    lines.append("END OF REPORT")
    lines.append(sep)

    report_text = "\n".join(lines)
    with open(output_path, "w", encoding="utf-8") as f:
        f.write(report_text)

    return report_text


#  MAIN ORCHESTRATOR


def main():
    print("=" * 60)
    print("  Training Asset Reconciliation — Starting")
    print("=" * 60)

    # Step 1: Parse & validate the manifest 
    print("\n[1] Parsing manifest...")
    try:
        valid_rows, invalid_rows, warnings = parse_manifest(MANIFEST_FILE)
    except InvalidHeaderError as e:
        print(f"\n  FATAL ERROR: {e}")
        print("  Cannot continue without correct headers. Exiting.")
        return
    except FileNotFoundError:
        print(f"\n  FATAL ERROR: '{MANIFEST_FILE}' not found.")
        return

    print(f"     Valid rows   : {len(valid_rows)}")
    print(f"     Invalid rows : {len(invalid_rows)}")
    print(f"     Warnings     : {len(warnings)}")

    # Step 2: Compare files using set operations 
    print("\n[2] Comparing manifest vs assets/ folder...")
    missing_files, orphan_files, matched_files = compare_files(valid_rows, ASSETS_FOLDER)
    print(f"     Matched  : {len(matched_files)}")
    print(f"     Missing  : {len(missing_files)}")
    print(f"     Orphan   : {len(orphan_files)}")

    # Step 3: Detect duplicates
    print("\n[3] Detecting duplicates...")
    duplicate_codes, duplicate_files = detect_duplicates(valid_rows)
    print(f"     Duplicate codes     : {len(duplicate_codes)}")
    print(f"     Conflicting files   : {len(duplicate_files)}")

    # Step 4: Group by day and type
    print("\n[4] Grouping assets by day and type...")
    grouped = group_assets(valid_rows)

    # Step 5: Write deployment manifest
    print("\n[5] Writing deployment_ready_manifest.csv...")
    deployment_ready = write_deployment_manifest(
        valid_rows, matched_files, duplicate_codes,
        "deployment_ready_manifest.csv"
    )
    print(f"     Deployment-ready assets: {len(deployment_ready)}")

    # Step 6: Write reconciliation report 
    print("\n[6] Writing reconciliation_report.txt...")
    report = write_report(
        valid_rows, invalid_rows, warnings,
        missing_files, orphan_files, matched_files,
        duplicate_codes, duplicate_files,
        grouped, deployment_ready,
        "reconciliation_report.txt"
    )

    # Step 6: Print report to console 
    print("\n" + report)
    print("\n Done. Check 'reconciliation_report.txt' and 'deployment_ready_manifest.csv'.")

#  Entry point


if __name__ == "__main__":
    main()
