"""Count distinct active UTC months for current-period Human accounts."""

import csv
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from statistics import mean, median


DIRECTORY = Path(__file__).resolve().parent
ACTIVITY = DIRECTORY / "Current_activity.csv"
ACCOUNTS = DIRECTORY / "Current_accounts.csv"
METADATA = DIRECTORY / "account_metadata_final.csv"
CONTRIBUTORS_OUTPUT = DIRECTORY / "contributor_active_months.csv"
DISTRIBUTION_OUTPUT = DIRECTORY / "contributor_active_months_distribution.csv"
WINDOW_START = datetime(2025, 9, 1, tzinfo=timezone.utc)
WINDOW_END = datetime(2026, 9, 1, tzinfo=timezone.utc)
EXPECTED_HUMANS = 937


# Read source rows and check the required CSV columns.
def read_csv(path: Path, required: set[str]) -> tuple[list[str], list[dict[str, str]]]:
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        fields = reader.fieldnames or []
        missing = required - set(fields)
        if missing:
            raise ValueError(f"{path.name} is missing columns: {sorted(missing)}")
        return fields, list(reader)


# Index unique account rows without changing their canonical identifiers.
def index_rows(rows: list[dict[str, str]], source: str, issues: list[str]) -> dict[str, dict[str, str]]:
    indexed = {}
    for number, row in enumerate(rows, start=2):
        key = row["AccountKey"].strip()
        if not key:
            issues.append(f"{source} row {number}: missing AccountKey")
        elif key in indexed:
            issues.append(f"{source} row {number}: duplicate AccountKey={key}")
        else:
            indexed[key] = row
    return indexed


# Reuse the account mapping already recorded by Current_accounts.csv.
def build_event_lookup(
    accounts: dict[str, dict[str, str]], issues: list[str]
) -> dict[str, str]:
    lookup = {}
    for key, account in accounts.items():
        originals = [value.strip() for value in account["OriginalAccountKeys"].split(";")]
        if not originals or any(not value for value in originals):
            issues.append(f"Current_accounts.csv: invalid OriginalAccountKeys for {key}")
            continue
        if len(originals) != len(set(originals)):
            issues.append(f"Current_accounts.csv: repeated OriginalAccountKeys for {key}")
        for original in originals:
            prior = lookup.get(original)
            if prior and prior != key:
                issues.append(f"Current_accounts.csv: {original} maps to both {prior} and {key}")
            else:
                lookup[original] = key
    return lookup


# Parse timezone-aware event times and enforce the UTC observation window.
def event_month(row: dict[str, str], number: int, issues: list[str]) -> str | None:
    raw = row["TimestampUtc"].strip()
    if not raw:
        issues.append(f"Current_activity.csv row {number}: missing TimestampUtc")
        return None
    try:
        moment = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        issues.append(f"Current_activity.csv row {number}: invalid TimestampUtc={raw}")
        return None
    if moment.tzinfo is None or moment.utcoffset() is None:
        issues.append(f"Current_activity.csv row {number}: timestamp lacks a UTC offset: {raw}")
        return None
    moment = moment.astimezone(timezone.utc)
    if not WINDOW_START <= moment < WINDOW_END:
        issues.append(f"Current_activity.csv row {number}: timestamp outside window: {raw}")
        return None
    return moment.strftime("%Y-%m")


# Validate all events and collect distinct months only for Human accounts.
def collect_months(
    events: list[dict[str, str]],
    accounts: dict[str, dict[str, str]],
    metadata: dict[str, dict[str, str]],
    event_lookup: dict[str, str],
    issues: list[str],
) -> dict[str, set[str]]:
    months = defaultdict(set)
    event_counts = Counter()
    seen_ids = set()
    for number, event in enumerate(events, start=2):
        event_id = event["EventId"].strip()
        if not event_id:
            issues.append(f"Current_activity.csv row {number}: missing EventId")
        elif event_id in seen_ids:
            issues.append(f"Current_activity.csv row {number}: duplicate EventId={event_id}")
        else:
            seen_ids.add(event_id)

        month = event_month(event, number, issues)
        raw_key = event["AccountKey"].strip()
        canonical = event_lookup.get(raw_key)
        if not canonical:
            issues.append(f"Current_activity.csv row {number}: unmatched AccountKey={raw_key or '<blank>'}")
            continue
        event_counts[canonical] += 1
        if canonical not in metadata:
            issues.append(f"Current_activity.csv row {number}: account missing from metadata: {canonical}")
        elif month and metadata[canonical]["MetadataClassification"].strip() == "Human":
            months[canonical].add(month)

    for key, account in accounts.items():
        raw_count = account["EventCount"].strip()
        try:
            expected = int(raw_count)
        except ValueError:
            issues.append(f"Current_accounts.csv: invalid EventCount for {key}: {raw_count}")
            continue
        if event_counts[key] != expected:
            issues.append(
                f"Current_accounts.csv: EventCount mismatch for {key}: "
                f"expected {expected}, found {event_counts[key]}"
            )
    return months


# Build contributor and distribution rows after all input checks pass.
def build_results(
    metadata: dict[str, dict[str, str]], months: dict[str, set[str]], issues: list[str]
) -> tuple[list[dict[str, str | int]], list[dict[str, str | int]]]:
    humans = {key: row for key, row in metadata.items() if row["MetadataClassification"].strip() == "Human"}
    if len(humans) != EXPECTED_HUMANS:
        issues.append(f"Expected {EXPECTED_HUMANS} Human accounts; found {len(humans)}")

    contributors = []
    for key, row in sorted(humans.items()):
        active = sorted(months.get(key, set()))
        if not 1 <= len(active) <= 12:
            issues.append(f"Human account {key} has {len(active)} active months")
        contributors.append({
            "AccountKey": key,
            "Login": row["ObservedLogins"],
            "ActiveMonths": len(active),
            "ActiveMonthList": ";".join(active),
        })

    counts = Counter(row["ActiveMonths"] for row in contributors)
    distribution = [
        {
            "ActiveMonths": value,
            "ContributorCount": counts[value],
            "Percentage": f"{100 * counts[value] / len(contributors):.2f}" if contributors else "0.00",
        }
        for value in range(1, 13)
    ]
    return contributors, distribution


# Write the two requested outputs only after validation succeeds.
def write_csv(path: Path, fields: list[str], rows: list[dict[str, str | int]]) -> None:
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


def main() -> None:
    issues = []
    account_fields, account_rows = read_csv(
        ACCOUNTS, {"AccountKey", "ObservedLogins", "OriginalAccountKeys", "EventCount"}
    )
    _, metadata_rows = read_csv(
        METADATA, set(account_fields) | {"MetadataClassification"}
    )
    _, events = read_csv(ACTIVITY, {"EventId", "AccountKey", "TimestampUtc"})
    accounts = index_rows(account_rows, ACCOUNTS.name, issues)
    metadata = index_rows(metadata_rows, METADATA.name, issues)

    if accounts.keys() != metadata.keys():
        issues.append(
            f"Account key sets differ: {len(accounts.keys() - metadata.keys())} missing metadata, "
            f"{len(metadata.keys() - accounts.keys())} extra metadata"
        )
    for key in accounts.keys() & metadata.keys():
        for field in account_fields:
            if accounts[key][field] != metadata[key][field]:
                issues.append(f"Metadata account snapshot differs for {key}: {field}")
    for key, row in metadata.items():
        if row["MetadataClassification"].strip() not in {"Human", "Bot", "Uncertain"}:
            issues.append(f"Invalid MetadataClassification for {key}: {row['MetadataClassification']}")

    event_lookup = build_event_lookup(accounts, issues)
    months = collect_months(events, accounts, metadata, event_lookup, issues)
    contributors, distribution = build_results(metadata, months, issues)
    if issues:
        print(f"Validation issues ({len(issues)}):")
        for issue in issues:
            print(f"  - {issue}")
        raise SystemExit("No output files were written because validation failed.")

    write_csv(
        CONTRIBUTORS_OUTPUT, ["AccountKey", "Login", "ActiveMonths", "ActiveMonthList"], contributors
    )
    write_csv(
        DISTRIBUTION_OUTPUT, ["ActiveMonths", "ContributorCount", "Percentage"], distribution
    )
    values = [row["ActiveMonths"] for row in contributors]
    print(f"Human contributors: {len(contributors)}")
    print("Active Months distribution (months: contributors, percentage):")
    for row in distribution:
        print(f"  {row['ActiveMonths']}: {row['ContributorCount']} ({row['Percentage']}%)")
    print(f"Mean: {mean(values):.2f}; median: {median(values):g}")
    print("Validation issues: none")
    print(f"Outputs: {CONTRIBUTORS_OUTPUT}; {DISTRIBUTION_OUTPUT}")


if __name__ == "__main__":
    main()
