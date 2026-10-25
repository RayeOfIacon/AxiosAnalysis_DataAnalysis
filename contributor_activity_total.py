"""Extend existing Human contributor totals with activity groups and key status."""

import csv
import os
import tempfile
from pathlib import Path


DIRECTORY = Path(__file__).resolve().parent
TOTAL_PATH = DIRECTORY / "contributor_activity_total_new.csv"
KEY_PATH = DIRECTORY / "contributor_key.csv"
EXPECTED_CONTRIBUTORS = 937
ACTIVITY_TYPES = (
    "Commit",
    "PR Opened",
    "Issue Opened",
    "PR Review",
    "PR Conversation Comment",
    "Inline Review Comment",
    "Issue Comment",
)
GROUPS = {
    "CodeContributionCount": ("Commit", "PR Opened"),
    "CodeReviewCount": ("PR Review", "Inline Review Comment"),
    "ProjectDiscussionCount": ("Issue Opened", "Issue Comment", "PR Conversation Comment"),
}
NEW_FIELDS = (
    "IsKeyCandidate",
    "CodeContributionCount",
    "CodeReviewCount",
    "ProjectDiscussionCount",
    "CodeContributionShare",
    "CodeReviewShare",
    "ProjectDiscussionShare",
)


# Read each input while preserving its original column order and text values.
def read_csv(path: Path, required: set[str]) -> tuple[list[str], list[dict[str, str]]]:
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        fields = reader.fieldnames or []
        if len(fields) != len(set(fields)):
            raise ValueError(f"{path.name}: duplicate column names")
        missing = required - set(fields)
        if missing:
            raise ValueError(f"{path.name}: missing columns: {', '.join(sorted(missing))}")
        rows = list(reader)
    if any(None in row or any(value is None for value in row.values()) for row in rows):
        raise ValueError(f"{path.name}: malformed CSV row")
    return fields, rows


# Require one row for each canonical account in both datasets.
def index_accounts(rows: list[dict[str, str]], path: Path) -> dict[str, dict[str, str]]:
    indexed = {}
    for line, row in enumerate(rows, start=2):
        key = row["AccountKey"]
        if not key or key != key.strip():
            raise ValueError(f"{path.name} row {line}: missing or padded AccountKey")
        if key in indexed:
            raise ValueError(f"{path.name} row {line}: duplicate AccountKey={key}")
        indexed[key] = row
    if len(indexed) != EXPECTED_CONTRIBUTORS:
        raise ValueError(
            f"{path.name}: expected {EXPECTED_CONTRIBUTORS} contributors, found {len(indexed)}"
        )
    return indexed


# Validate existing counts and add three disjoint groups and their shares.
def extend_row(row: dict[str, str], key_row: dict[str, str]) -> dict[str, str]:
    key = row["AccountKey"]
    flag = key_row["IsKeyCandidate"]
    if flag not in {"True", "False"}:
        raise ValueError(f"{key}: invalid IsKeyCandidate={flag!r}")
    if "Login" in key_row and row["Login"] != key_row["Login"]:
        raise ValueError(f"{key}: Login differs between inputs")
    try:
        counts = {name: int(row[name]) for name in ACTIVITY_TYPES}
        total = int(row["ActivityTotal"])
    except ValueError as error:
        raise ValueError(f"{key}: invalid activity count: {error}") from error
    if any(count < 0 for count in counts.values()) or total != sum(counts.values()):
        raise ValueError(f"{key}: seven activity counts do not sum to ActivityTotal")

    group_counts = {name: sum(counts[field] for field in fields) for name, fields in GROUPS.items()}
    if sum(group_counts.values()) != total:
        raise ValueError(f"{key}: three activity groups do not sum to ActivityTotal")
    extended = dict(row)
    extended["IsKeyCandidate"] = flag
    for name, count in group_counts.items():
        extended[name] = str(count)
        share_name = name.replace("Count", "Share")
        extended[share_name] = format(count / total, ".15g") if total else "0"
    return extended


# Replace the output only after every account and calculation has passed validation.
def write_csv(path: Path, fields: list[str], rows: list[dict[str, str]]) -> None:
    temporary_path = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w", encoding="utf-8-sig", newline="", dir=path.parent,
            prefix=f".{path.stem}.", suffix=".tmp", delete=False,
        ) as handle:
            temporary_path = Path(handle.name)
            writer = csv.DictWriter(handle, fieldnames=fields)
            writer.writeheader()
            writer.writerows(rows)
        os.replace(temporary_path, path)
    finally:
        if temporary_path is not None and temporary_path.exists():
            temporary_path.unlink()


# Match existing classifications without recalculating candidate thresholds.
def main() -> None:
    base_fields, base_rows = read_csv(
        TOTAL_PATH, {"AccountKey", "Login", "ActivityTotal", *ACTIVITY_TYPES}
    )
    _, key_rows = read_csv(KEY_PATH, {"AccountKey", "IsKeyCandidate"})
    base_accounts = index_accounts(base_rows, TOTAL_PATH)
    key_accounts = index_accounts(key_rows, KEY_PATH)
    if base_accounts.keys() != key_accounts.keys():
        only_base = base_accounts.keys() - key_accounts.keys()
        only_key = key_accounts.keys() - base_accounts.keys()
        raise ValueError(
            f"AccountKey mismatch: {len(only_base)} only in totals, "
            f"{len(only_key)} only in key classifications"
        )

    extended_rows = [extend_row(row, key_accounts[row["AccountKey"]]) for row in base_rows]
    fields = [*base_fields, *(field for field in NEW_FIELDS if field not in base_fields)]
    write_csv(TOTAL_PATH, fields, extended_rows)
    key_count = sum(row["IsKeyCandidate"] == "True" for row in extended_rows)
    print(f"Matched Human contributors: {len(extended_rows)}/{EXPECTED_CONTRIBUTORS}")
    print(f"Key candidates copied from contributor_key.csv: {key_count}")
    print("Validation: all three activity groups sum to ActivityTotal for every contributor.")
    print(f"Updated: {TOTAL_PATH}")


if __name__ == "__main__":
    main()
