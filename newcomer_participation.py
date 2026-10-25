"""Compare current-period participation by five-year history classification."""

import csv
import os
import tempfile
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from statistics import mean, median


DIRECTORY = Path(__file__).resolve().parent
IDENTIFICATION = DIRECTORY / "newcomer_identification.csv"
ACTIVITY = DIRECTORY / "Current_activity.csv"
ACCOUNTS = DIRECTORY / "Current_accounts.csv"
CONTRIBUTORS_OUTPUT = DIRECTORY / "newcomer_participation_contributors.csv"
VOLUME_OUTPUT = DIRECTORY / "newcomer_activity_volume_summary.csv"
FIRST_TYPE_OUTPUT = DIRECTORY / "newcomer_first_activity_type_summary.csv"
ACTIVITY_TYPE_OUTPUT = DIRECTORY / "newcomer_activity_type_summary.csv"

START = datetime(2025, 9, 1, tzinfo=timezone.utc)
END = datetime(2026, 9, 1, tzinfo=timezone.utc)
TYPES = (
    "Commit", "PR Opened", "Issue Opened", "PR Review",
    "Inline Review Comment", "Issue Comment", "PR Conversation Comment",
)
GROUPS = ("Newcomer Candidate", "Returning Contributor")
EXPECTED = {"Newcomer Candidate": 857, "Returning Contributor": 67, "Uncertain History": 13}
CONTRIBUTOR_FIELDS = (
    "AccountKey", "GitHubUserId", "ObservedLogins", "Classification",
    "ActivityCount", *TYPES, "FirstActivityUtc", "FirstActivityType",
    "FirstActivityEventId", "FirstActivityUrl",
)
VOLUME_FIELDS = (
    "Classification", "ActivityCount", "ContributorCount", "GroupContributorCount",
    "PercentageOfContributors", "MeanActivityCount", "MedianActivityCount",
)
FIRST_TYPE_FIELDS = (
    "Classification", "FirstActivityType", "ContributorCount",
    "GroupContributorCount", "PercentageOfContributors",
)
ACTIVITY_TYPE_FIELDS = (
    "Classification", "ActivityType", "ActivityCount", "GroupActivityCount",
    "PercentageOfActivities", "ContributorsWithActivityType",
    "PercentageOfContributors",
)


# Load source tables and reject malformed rows or missing columns.
def read_csv(path: Path, required: set[str]) -> list[dict[str, str]]:
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
    return rows


# Parse event times strictly within the UTC observation window.
def parse_time(raw: str, source: str, line: int) -> datetime:
    try:
        value = datetime.fromisoformat(raw.strip().replace("Z", "+00:00"))
    except ValueError as error:
        raise ValueError(f"{source} row {line}: invalid timestamp {raw!r}") from error
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{source} row {line}: timestamp has no timezone")
    value = value.astimezone(timezone.utc)
    if not START <= value < END:
        raise ValueError(f"{source} row {line}: timestamp outside observation window")
    return value


def utc_text(value: datetime) -> str:
    return value.isoformat(timespec="seconds").replace("+00:00", "Z")


# Keep the existing canonical account mapping and finalized classifications.
def load_accounts() -> tuple[dict[str, dict[str, str]], dict[str, str], dict[str, dict[str, str]]]:
    accounts = {}
    original_to_canonical = {}
    rows = read_csv(
        ACCOUNTS,
        {"AccountKey", "GitHubUserId", "ObservedLogins", "OriginalAccountKeys",
         "EventCount", "FirstActivityUtc", "LastActivityUtc", "ActivityTypes"},
    )
    for line, row in enumerate(rows, start=2):
        key = row["AccountKey"].strip()
        if not key or key in accounts:
            raise ValueError(f"{ACCOUNTS.name} row {line}: blank or duplicate AccountKey")
        accounts[key] = row
        originals = [part.strip() for part in row["OriginalAccountKeys"].split(";")]
        if not originals or any(not part for part in originals):
            raise ValueError(f"{ACCOUNTS.name} row {line}: invalid OriginalAccountKeys")
        for original in originals:
            prior = original_to_canonical.setdefault(original, key)
            if prior != key:
                raise ValueError(f"{ACCOUNTS.name} row {line}: conflicting original account key")

    classifications = {}
    rows = read_csv(
        IDENTIFICATION,
        {"AccountKey", "GitHubUserId", "ObservedLogins", "Classification",
         "FirstCurrentActivityUtc"},
    )
    for line, row in enumerate(rows, start=2):
        key = row["AccountKey"].strip()
        if not key or key in classifications or key not in accounts:
            raise ValueError(f"{IDENTIFICATION.name} row {line}: unknown or duplicate AccountKey")
        if row["Classification"] not in EXPECTED:
            raise ValueError(f"{IDENTIFICATION.name} row {line}: unexpected classification")
        if any(row[field] != accounts[key][field] for field in ("GitHubUserId", "ObservedLogins")):
            raise ValueError(f"{IDENTIFICATION.name} row {line}: account identity mismatch")
        classifications[key] = row
    counts = Counter(row["Classification"] for row in classifications.values())
    if counts != EXPECTED or len(classifications) != 937:
        raise ValueError(f"Final classification counts differ from expected: {dict(counts)}")
    return accounts, original_to_canonical, classifications


# Assign each unique event to its existing canonical account.
def load_events(
    accounts: dict[str, dict[str, str]], original_to_canonical: dict[str, str],
) -> dict[str, list[tuple[datetime, dict[str, str]]]]:
    rows = read_csv(
        ACTIVITY,
        {"EventId", "ActivityType", "TimestampUtc", "AccountKey", "ArtifactUrl"},
    )
    seen_ids = set()
    events = defaultdict(list)
    for line, row in enumerate(rows, start=2):
        event_id = row["EventId"].strip()
        if not event_id or event_id in seen_ids:
            raise ValueError(f"{ACTIVITY.name} row {line}: blank or duplicate EventId")
        seen_ids.add(event_id)
        if row["ActivityType"] not in TYPES:
            raise ValueError(f"{ACTIVITY.name} row {line}: unknown ActivityType")
        original = row["AccountKey"].strip()
        key = original_to_canonical.get(original)
        if key is None or key not in accounts:
            raise ValueError(f"{ACTIVITY.name} row {line}: unmapped AccountKey {original!r}")
        events[key].append((parse_time(row["TimestampUtc"], ACTIVITY.name, line), row))

    for key, account in accounts.items():
        account_events = events.get(key, [])
        if len(account_events) != int(account["EventCount"]):
            raise ValueError(f"{key}: event count differs from Current_accounts.csv")
        if not account_events:
            raise ValueError(f"{key}: account has no current events")
        if min(time for time, _ in account_events) != parse_time(account["FirstActivityUtc"], ACCOUNTS.name, 0):
            raise ValueError(f"{key}: first activity differs from Current_accounts.csv")
        if max(time for time, _ in account_events) != parse_time(account["LastActivityUtc"], ACCOUNTS.name, 0):
            raise ValueError(f"{key}: last activity differs from Current_accounts.csv")
        types = {row["ActivityType"] for _, row in account_events}
        if types != set(account["ActivityTypes"].split(";")):
            raise ValueError(f"{key}: activity types differ from Current_accounts.csv")
    if sum(map(len, events.values())) != len(rows):
        raise ValueError("Event-to-account mapping did not retain all current activities")
    return events


# Build one contributor row; EventId breaks identical earliest timestamps.
def contributor_rows(
    accounts: dict[str, dict[str, str]],
    classifications: dict[str, dict[str, str]],
    events: dict[str, list[tuple[datetime, dict[str, str]]]],
) -> list[dict[str, str | int]]:
    result = []
    for key in sorted(classifications):
        source = classifications[key]
        account_events = events[key]
        counts = Counter(row["ActivityType"] for _, row in account_events)
        first_time, first = min(account_events, key=lambda item: (item[0], item[1]["EventId"]))
        if first_time != parse_time(source["FirstCurrentActivityUtc"], IDENTIFICATION.name, 0):
            raise ValueError(f"{key}: first activity differs from newcomer_identification.csv")
        row = {
            "AccountKey": key,
            "GitHubUserId": source["GitHubUserId"],
            "ObservedLogins": source["ObservedLogins"],
            "Classification": source["Classification"],
            "ActivityCount": len(account_events),
            **{kind: counts[kind] for kind in TYPES},
            "FirstActivityUtc": utc_text(first_time),
            "FirstActivityType": first["ActivityType"],
            "FirstActivityEventId": first["EventId"],
            "FirstActivityUrl": first["ArtifactUrl"],
        }
        if sum(row[kind] for kind in TYPES) != row["ActivityCount"]:
            raise ValueError(f"{key}: seven activity counts do not sum to ActivityCount")
        result.append(row)
    return result


def percentage(numerator: int, denominator: int) -> str:
    return f"{100 * numerator / denominator:.2f}"


# Summarize exact activity counts, earliest types, and seven activity categories.
def summaries(contributors: list[dict[str, str | int]]) -> tuple[list[dict], list[dict], list[dict]]:
    volume = []
    first_types = []
    activity_types = []
    for group in GROUPS:
        members = [row for row in contributors if row["Classification"] == group]
        size = len(members)
        if size != EXPECTED[group]:
            raise ValueError(f"{group}: contributor count mismatch")
        distribution = Counter(int(row["ActivityCount"]) for row in members)
        average = f"{mean(distribution.elements()):.2f}"
        middle = f"{median(list(distribution.elements())):.2f}"
        for count in sorted(distribution):
            volume.append({
                "Classification": group,
                "ActivityCount": count,
                "ContributorCount": distribution[count],
                "GroupContributorCount": size,
                "PercentageOfContributors": percentage(distribution[count], size),
                "MeanActivityCount": average,
                "MedianActivityCount": middle,
            })

        first_counts = Counter(str(row["FirstActivityType"]) for row in members)
        group_activities = sum(int(row["ActivityCount"]) for row in members)
        for kind in TYPES:
            first_types.append({
                "Classification": group,
                "FirstActivityType": kind,
                "ContributorCount": first_counts[kind],
                "GroupContributorCount": size,
                "PercentageOfContributors": percentage(first_counts[kind], size),
            })
            activity_count = sum(int(row[kind]) for row in members)
            active_contributors = sum(int(row[kind]) > 0 for row in members)
            activity_types.append({
                "Classification": group,
                "ActivityType": kind,
                "ActivityCount": activity_count,
                "GroupActivityCount": group_activities,
                "PercentageOfActivities": percentage(activity_count, group_activities),
                "ContributorsWithActivityType": active_contributors,
                "PercentageOfContributors": percentage(active_contributors, size),
            })
        if sum(first_counts.values()) != size:
            raise ValueError(f"{group}: first-activity counts do not reconcile")
        if sum(int(row["ActivityCount"]) for row in activity_types if row["Classification"] == group) != group_activities:
            raise ValueError(f"{group}: activity-type counts do not reconcile")
    return volume, first_types, activity_types


# Write only the four new output files after all checks pass.
def write_csv(path: Path, fields: tuple[str, ...], rows: list[dict]) -> None:
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


# Run validated group comparisons and print concise descriptive statistics.
def main() -> None:
    accounts, original_to_canonical, classifications = load_accounts()
    events = load_events(accounts, original_to_canonical)
    contributors = contributor_rows(accounts, classifications, events)
    volume, first_types, activity_types = summaries(contributors)
    write_csv(CONTRIBUTORS_OUTPUT, CONTRIBUTOR_FIELDS, contributors)
    write_csv(VOLUME_OUTPUT, VOLUME_FIELDS, volume)
    write_csv(FIRST_TYPE_OUTPUT, FIRST_TYPE_FIELDS, first_types)
    write_csv(ACTIVITY_TYPE_OUTPUT, ACTIVITY_TYPE_FIELDS, activity_types)

    print(f"Validated {len(contributors)} classified contributors; 13 Uncertain History excluded from comparisons.")
    print("Earliest-event tie rule: earliest UTC timestamp, then lexicographically smallest EventId.")
    for group in GROUPS:
        members = [row for row in contributors if row["Classification"] == group]
        values = [int(row["ActivityCount"]) for row in members]
        first = Counter(str(row["FirstActivityType"]) for row in members)
        activities = Counter({kind: sum(int(row[kind]) for row in members) for kind in TYPES})
        top_first, top_first_count = first.most_common(1)[0]
        top_type, top_type_count = activities.most_common(1)[0]
        print(
            f"{group}: n={len(members)}, activities={sum(values)}, "
            f"mean={mean(values):.2f}, median={median(values):.2f}, "
            f"one-activity={values.count(1)} ({percentage(values.count(1), len(members))}%), "
            f"top first type={top_first} ({top_first_count}), "
            f"top activity type={top_type} ({top_type_count})."
        )
    print("First recorded activity is within the observation window, not necessarily first-ever GitHub participation.")


if __name__ == "__main__":
    main()
