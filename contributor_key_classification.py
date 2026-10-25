"""Identify key contributor candidates from existing Human contributor metrics."""

import csv
from collections import Counter
from pathlib import Path


DIRECTORY = Path(__file__).resolve().parent
ACTIVITY_PRIMARY = DIRECTORY / "contributor_activity_total.csv"
ACTIVITY_FALLBACK = DIRECTORY / "contributor_activity_total_new.csv"
MONTHS_INPUT = DIRECTORY / "contributor_active_months.csv"
OUTPUT = DIRECTORY / "contributor_key.csv"
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
ACTIVITY_REQUIRED = {"AccountKey", "Login", *ACTIVITY_TYPES, "ActivityTotal", "ActivityBreadth"}
MONTHS_REQUIRED = {"AccountKey", "Login", "ActiveMonths", "ActiveMonthList"}


# Select the requested metrics file, or explicitly report the available renamed file.
def activity_input_path() -> Path:
    if ACTIVITY_PRIMARY.is_file():
        return ACTIVITY_PRIMARY
    if ACTIVITY_FALLBACK.is_file():
        print(f"Input notice: {ACTIVITY_PRIMARY.name} is missing; using {ACTIVITY_FALLBACK.name}.")
        return ACTIVITY_FALLBACK
    raise FileNotFoundError(f"Neither activity metrics file exists in {DIRECTORY}")


# Read a CSV and reject missing, duplicate, or unexpected header structures.
def read_rows(path: Path, required: set[str]) -> tuple[list[str], list[dict[str, str]]]:
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


# Index accounts by their existing canonical key without merging identities.
def index_accounts(rows: list[dict[str, str]], filename: str) -> dict[str, dict[str, str]]:
    indexed = {}
    for line, row in enumerate(rows, start=2):
        key = row["AccountKey"].strip()
        if not key:
            raise ValueError(f"{filename} row {line}: blank AccountKey")
        if key in indexed:
            raise ValueError(f"{filename} row {line}: duplicate AccountKey={key}")
        indexed[key] = row
    return indexed


# Check that the existing numeric metrics agree with their component counts.
def validated_metrics(activity: dict[str, str], months: dict[str, str]) -> tuple[int, int, int]:
    key = activity["AccountKey"]
    try:
        counts = [int(activity[name]) for name in ACTIVITY_TYPES]
        total = int(activity["ActivityTotal"])
        breadth = int(activity["ActivityBreadth"])
        active_months = int(months["ActiveMonths"])
    except ValueError as error:
        raise ValueError(f"{key}: invalid numeric metric: {error}") from error
    if any(count < 0 for count in counts) or total != sum(counts):
        raise ValueError(f"{key}: ActivityTotal does not equal the seven activity counts")
    if breadth != sum(count > 0 for count in counts) or not 1 <= breadth <= 7:
        raise ValueError(f"{key}: invalid ActivityBreadth")
    if not 1 <= active_months <= 12:
        raise ValueError(f"{key}: ActiveMonths is outside 1–12")
    month_list = [value.strip() for value in months["ActiveMonthList"].split(";") if value.strip()]
    if len(month_list) != active_months or len(set(month_list)) != active_months:
        raise ValueError(f"{key}: ActiveMonthList does not match ActiveMonths")
    return total, active_months, breadth


# Print the highest-activity accounts on each side of the classification.
def print_top(label: str, records: list[tuple[int, dict[str, str]]]) -> None:
    print(f"{label} (up to 10):")
    for total, row in sorted(records, key=lambda item: (-item[0], item[1]["AccountKey"]))[:10]:
        print(
            f"  {row['AccountKey']} | {row['Login']} | ActivityTotal={total} | "
            f"ActiveMonths={row['ActiveMonths']} | ActivityBreadth={row['ActivityBreadth']}"
        )


# Join validated inputs, apply all three thresholds, and write the result.
def main() -> None:
    activity_path = activity_input_path()
    activity_fields, activity_rows = read_rows(activity_path, ACTIVITY_REQUIRED)
    _, months_rows = read_rows(MONTHS_INPUT, MONTHS_REQUIRED)
    activity_index = index_accounts(activity_rows, activity_path.name)
    months_index = index_accounts(months_rows, MONTHS_INPUT.name)

    if len(activity_index) != EXPECTED_CONTRIBUTORS or len(months_index) != EXPECTED_CONTRIBUTORS:
        raise ValueError(
            f"Expected {EXPECTED_CONTRIBUTORS} unique accounts in each input; "
            f"found {len(activity_index)} and {len(months_index)}"
        )
    if activity_index.keys() != months_index.keys():
        only_activity = activity_index.keys() - months_index.keys()
        only_months = months_index.keys() - activity_index.keys()
        raise ValueError(
            f"AccountKey sets differ: {len(only_activity)} only in activity metrics, "
            f"{len(only_months)} only in active months"
        )

    condition_counts = Counter()
    candidates = []
    non_candidates = []
    output_rows = []
    for activity in activity_rows:
        key = activity["AccountKey"].strip()
        months = months_index[key]
        if activity["Login"] != months["Login"]:
            raise ValueError(f"{key}: Login differs between input files")
        total, active_months, breadth = validated_metrics(activity, months)
        conditions = (total >= 4, active_months >= 2, breadth >= 2)
        condition_counts.update(name for name, passed in zip(
            ("ActivityTotal >= 4", "ActiveMonths >= 2", "ActivityBreadth >= 2"), conditions
        ) if passed)
        is_candidate = all(conditions)
        row = {
            **activity,
            "ActiveMonths": months["ActiveMonths"],
            "ActiveMonthList": months["ActiveMonthList"],
            "IsKeyCandidate": str(is_candidate),
        }
        output_rows.append(row)
        (candidates if is_candidate else non_candidates).append((total, row))

    fields = [*activity_fields, "ActiveMonths", "ActiveMonthList", "IsKeyCandidate"]
    with OUTPUT.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(output_rows)

    total_accounts = len(output_rows)
    print(f"Validation: {total_accounts} unique AccountKey values retained; input files unchanged.")
    print(f"Key Candidates (all three): {len(candidates)} ({len(candidates) / total_accounts:.2%})")
    for name in ("ActivityTotal >= 4", "ActiveMonths >= 2", "ActivityBreadth >= 2"):
        count = condition_counts[name]
        print(f"{name}: {count} ({count / total_accounts:.2%})")
    print_top("Top Key Candidates by ActivityTotal", candidates)
    print_top("Top non-candidates by ActivityTotal", non_candidates)
    print(f"Output: {OUTPUT}")


if __name__ == "__main__":
    main()
