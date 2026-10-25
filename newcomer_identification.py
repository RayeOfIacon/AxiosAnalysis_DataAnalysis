"""Identify current Human contributors with observed five-year activity history."""

import csv
import os
import tempfile
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path


DIRECTORY = Path(__file__).resolve().parent
ACCOUNTS = DIRECTORY / "Current_accounts.csv"
CURRENT_ACTIVITY = DIRECTORY / "Current_activity.csv"
PRELIMINARY_METADATA = DIRECTORY / "account_metadata.csv"
FINAL_METADATA = DIRECTORY / "account_metadata_final.csv"
HISTORY = DIRECTORY.parent / "Data" / "Extracted_data" / "contributor_activity_pre_current_5y.csv"
DETAIL_OUTPUT = DIRECTORY / "newcomer_identification.csv"
SUMMARY_OUTPUT = DIRECTORY / "newcomer_identification_summary.csv"
CURRENT_START = datetime(2025, 9, 1, tzinfo=timezone.utc)
CURRENT_END = datetime(2026, 9, 1, tzinfo=timezone.utc)
HISTORY_START = datetime(2020, 9, 1, tzinfo=timezone.utc)
EXPECTED_HUMANS = 937
CATEGORIES = ("Returning Contributor", "Newcomer Candidate", "Uncertain History")
DETAIL_FIELDS = (
    "AccountKey", "GitHubUserId", "ObservedLogins", "AccountMatchStatus",
    "MetadataMatchStatus", "FirstCurrentActivityUtc", "FirstCurrentActivityDate",
    "Classification", "HistoricalMatchMethod", "HistoricalMatchCount",
    "HistoricalFirstActivityUtc", "HistoricalLastActivityUtc",
    "HistoricalActivityTypes", "HistoricalLogins", "HistoricalExampleEventKey",
    "HistoricalExampleUrl", "HistoricalMatchEvidence", "PotentialHistoryEventCount",
    "PotentialHistoryEvidence", "ClassificationReason",
)
SUMMARY_FIELDS = ("Classification", "ContributorCount", "Percentage")


# Read CSV rows and reject malformed or incomplete schemas.
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


# Validate UTC timestamps against the requested observation window.
def parse_timestamp(raw: str, source: str, line: int, start: datetime, end: datetime) -> datetime:
    try:
        value = datetime.fromisoformat(raw.strip().replace("Z", "+00:00"))
    except ValueError as error:
        raise ValueError(f"{source} row {line}: invalid timestamp={raw!r}") from error
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{source} row {line}: timestamp has no timezone")
    value = value.astimezone(timezone.utc)
    if not start <= value < end:
        raise ValueError(f"{source} row {line}: timestamp outside expected window")
    return value


# Preserve the existing account mapping and verify the final Human labels.
def current_accounts() -> tuple[dict[str, dict[str, str]], dict[str, dict[str, str]], dict[str, str]]:
    account_fields, account_rows = read_csv(
        ACCOUNTS,
        {"AccountKey", "GitHubUserId", "ObservedLogins", "OriginalAccountKeys",
         "FirstActivityUtc", "EventCount"},
    )
    _, metadata_rows = read_csv(
        FINAL_METADATA, set(account_fields) | {"MetadataClassification", "MetadataMatchStatus"}
    )
    accounts = {}
    metadata = {}
    original_to_canonical = {}
    for line, row in enumerate(account_rows, start=2):
        key = row["AccountKey"].strip()
        if not key or key in accounts:
            raise ValueError(f"{ACCOUNTS.name} row {line}: blank or duplicate AccountKey")
        accounts[key] = row
        originals = [value.strip() for value in row["OriginalAccountKeys"].split(";")]
        if not originals or any(not value for value in originals):
            raise ValueError(f"{ACCOUNTS.name} row {line}: invalid OriginalAccountKeys")
        for original in originals:
            if original in original_to_canonical and original_to_canonical[original] != key:
                raise ValueError(f"{ACCOUNTS.name} row {line}: conflicting original account key")
            original_to_canonical[original] = key
    for line, row in enumerate(metadata_rows, start=2):
        key = row["AccountKey"].strip()
        if not key or key in metadata:
            raise ValueError(f"{FINAL_METADATA.name} row {line}: blank or duplicate AccountKey")
        metadata[key] = row
    if accounts.keys() != metadata.keys():
        raise ValueError("Current_accounts.csv and final metadata have different AccountKey sets")
    for key, account in accounts.items():
        if any(account[field] != metadata[key][field] for field in account_fields):
            raise ValueError(f"{key}: final metadata differs from the current account snapshot")
    humans = {
        key: row for key, row in metadata.items()
        if row["MetadataClassification"] == "Human"
    }
    if len(humans) != EXPECTED_HUMANS:
        raise ValueError(f"Expected {EXPECTED_HUMANS} Human accounts, found {len(humans)}")
    for key, row in humans.items():
        user_id = row["GitHubUserId"].strip()
        if user_id:
            if not user_id.isdecimal() or key != f"GH:{int(user_id)}":
                raise ValueError(f"{key}: GitHubUserId and AccountKey disagree")
        elif not key.startswith("LOGIN:") or not row["ObservedLogins"].strip():
            raise ValueError(f"{key}: insufficient current account identifier")
    return accounts, humans, original_to_canonical


# Check raw current events and derive each account's first current activity.
def first_current_activity(
    accounts: dict[str, dict[str, str]], original_to_canonical: dict[str, str],
) -> dict[str, datetime]:
    _, rows = read_csv(CURRENT_ACTIVITY, {"EventId", "AccountKey", "TimestampUtc"})
    first = {}
    counts = Counter()
    seen_ids = set()
    for line, row in enumerate(rows, start=2):
        event_id = row["EventId"].strip()
        if not event_id or event_id in seen_ids:
            raise ValueError(f"{CURRENT_ACTIVITY.name} row {line}: blank or duplicate EventId")
        seen_ids.add(event_id)
        key = original_to_canonical.get(row["AccountKey"].strip())
        if key is None:
            raise ValueError(f"{CURRENT_ACTIVITY.name} row {line}: unmapped AccountKey")
        timestamp = parse_timestamp(
            row["TimestampUtc"], CURRENT_ACTIVITY.name, line, CURRENT_START, CURRENT_END
        )
        counts[key] += 1
        if key not in first or timestamp < first[key]:
            first[key] = timestamp
    for key, account in accounts.items():
        if counts[key] != int(account["EventCount"]):
            raise ValueError(f"{key}: current event count differs from Current_accounts.csv")
        expected = parse_timestamp(
            account["FirstActivityUtc"], ACCOUNTS.name, 0, CURRENT_START, CURRENT_END
        )
        if first.get(key) != expected:
            raise ValueError(f"{key}: first current activity differs from Current_accounts.csv")
    return first


# Index explicit historical GitHub IDs and retain login-only evidence separately.
def historical_activity() -> tuple[dict[str, list[dict[str, str]]], dict[str, list[dict[str, str]]], int]:
    _, rows = read_csv(
        HISTORY,
        {"EventKey", "TimestampUtc", "ActivityType", "GitHubUserId",
         "Login", "Url"},
    )
    by_id = defaultdict(list)
    by_login = defaultdict(list)
    seen_events = set()
    missing_id = 0
    for line, row in enumerate(rows, start=2):
        event_key = row["EventKey"].strip()
        if not event_key or event_key in seen_events:
            raise ValueError(f"{HISTORY.name} row {line}: blank or duplicate EventKey")
        seen_events.add(event_key)
        parse_timestamp(row["TimestampUtc"], HISTORY.name, line, HISTORY_START, CURRENT_START)
        user_id = row["GitHubUserId"].strip()
        login = row["Login"].strip().casefold()
        if user_id:
            if not user_id.isdecimal():
                raise ValueError(f"{HISTORY.name} row {line}: invalid GitHubUserId")
            by_id[str(int(user_id))].append(row)
        else:
            missing_id += 1
        if login:
            by_login[login].append(row)
    return by_id, by_login, missing_id


# Classify by stable ID; use login-only evidence to flag unresolved cases.
def classify(
    humans: dict[str, dict[str, str]], first: dict[str, datetime],
    by_id: dict[str, list[dict[str, str]]], by_login: dict[str, list[dict[str, str]]],
) -> list[dict[str, str | int]]:
    results = []
    for key in sorted(humans):
        current = humans[key]
        user_id = current["GitHubUserId"].strip()
        login = current["ObservedLogins"].strip().casefold()
        confirmed = by_id.get(user_id, []) if user_id else []
        potential = [
            row for row in by_login.get(login, [])
            if not user_id or row["GitHubUserId"].strip() != user_id
        ] if login else []
        historical_logins = sorted(
            {row["Login"].strip() for row in confirmed if row["Login"].strip()},
            key=str.casefold,
        )
        if confirmed:
            category = "Returning Contributor"
            reason = "Historical activity has the same explicit GitHubUserId."
        elif not user_id:
            category = "Uncertain History"
            reason = "Current account has no GitHubUserId; login continuity cannot verify identity."
        elif potential:
            category = "Uncertain History"
            reason = "Only login-related historical activity was found without a matching GitHubUserId."
        else:
            category = "Newcomer Candidate"
            reason = "No activity was observed for this GitHubUserId in the available five-year history; first-ever participation is not confirmed."
        example = min(confirmed, key=lambda row: row["TimestampUtc"]) if confirmed else None
        potential_example = min(potential, key=lambda row: row["TimestampUtc"]) if potential else None
        current_first = first[key]
        results.append({
            "AccountKey": key,
            "GitHubUserId": user_id,
            "ObservedLogins": current["ObservedLogins"],
            "AccountMatchStatus": current["AccountMatchStatus"],
            "MetadataMatchStatus": current["MetadataMatchStatus"],
            "FirstCurrentActivityUtc": current_first.isoformat().replace("+00:00", "Z"),
            "FirstCurrentActivityDate": current_first.date().isoformat(),
            "Classification": category,
            "HistoricalMatchMethod": "GitHubUserId" if confirmed else "",
            "HistoricalMatchCount": len(confirmed),
            "HistoricalFirstActivityUtc": example["TimestampUtc"] if example else "",
            "HistoricalLastActivityUtc": (
                max(row["TimestampUtc"] for row in confirmed) if confirmed else ""
            ),
            "HistoricalActivityTypes": (
                ";".join(sorted({row["ActivityType"] for row in confirmed})) if confirmed else ""
            ),
            "HistoricalLogins": ";".join(historical_logins),
            "HistoricalExampleEventKey": example["EventKey"] if example else "",
            "HistoricalExampleUrl": example["Url"] if example else "",
            "HistoricalMatchEvidence": (
                f"GitHubUserId={user_id}; EventKey={example['EventKey']}" if example else ""
            ),
            "PotentialHistoryEventCount": len(potential),
            "PotentialHistoryEvidence": (
                f"Login={current['ObservedLogins']}; "
                f"HistoricalGitHubUserId={potential_example['GitHubUserId'] or '<missing>'}; "
                f"EventKey={potential_example['EventKey']}"
                if potential_example else ""
            ),
            "ClassificationReason": reason,
        })
    if len(results) != EXPECTED_HUMANS or len({row["AccountKey"] for row in results}) != EXPECTED_HUMANS:
        raise ValueError("Current Human contributors were not classified exactly once")
    return results


# Write requested outputs only after all identity and count checks pass.
def write_csv(path: Path, fields: tuple[str, ...], rows: list[dict[str, str | int]]) -> None:
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w", encoding="utf-8-sig", newline="", dir=path.parent,
            prefix=f".{path.stem}.", suffix=".tmp", delete=False,
        ) as handle:
            temporary = Path(handle.name)
            writer = csv.DictWriter(handle, fieldnames=fields)
            writer.writeheader()
            writer.writerows(rows)
        os.replace(temporary, path)
    finally:
        if temporary is not None and temporary.exists():
            temporary.unlink()


# Reuse finalized labels and print matching limitations without changing inputs.
def main() -> None:
    if not PRELIMINARY_METADATA.exists():
        print(f"Input notice: {PRELIMINARY_METADATA.name} is absent; using {FINAL_METADATA.name} for finalized classifications.")
    accounts, humans, original_to_canonical = current_accounts()
    first = first_current_activity(accounts, original_to_canonical)
    by_id, by_login, historical_missing_id = historical_activity()
    results = classify(humans, first, by_id, by_login)
    counts = Counter(row["Classification"] for row in results)
    summary = [
        {
            "Classification": category,
            "ContributorCount": counts[category],
            "Percentage": f"{100 * counts[category] / EXPECTED_HUMANS:.2f}",
        }
        for category in CATEGORIES
    ]
    if sum(counts.values()) != EXPECTED_HUMANS:
        raise ValueError("Category counts do not add to 937")
    write_csv(DETAIL_OUTPUT, DETAIL_FIELDS, results)
    write_csv(SUMMARY_OUTPUT, SUMMARY_FIELDS, summary)
    print(f"Current Human contributors classified: {len(results)}")
    for row in summary:
        print(f"  {row['Classification']}: {row['ContributorCount']} ({row['Percentage']}%)")
    print(f"Historical records without GitHubUserId: {historical_missing_id}")
    print(f"Current Human accounts without GitHubUserId: {sum(not row['GitHubUserId'] for row in results)}")
    print("Validation: unique Human accounts, current events, timestamps, historical event IDs, and category total passed.")
    print("Limitations: login-only and Git-author identities are not merged with GitHub IDs; missing history does not prove a first-ever contribution.")
    print(f"Outputs: {DETAIL_OUTPUT}; {SUMMARY_OUTPUT}")


if __name__ == "__main__":
    main()
