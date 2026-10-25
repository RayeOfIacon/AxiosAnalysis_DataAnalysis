"""Prepare six years of activity events and period coverage for later analysis."""

import csv
import os
import tempfile
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path


DIRECTORY = Path(__file__).resolve().parent
HISTORY = DIRECTORY.parent / "Data" / "Extracted_data" / "contributor_activity_pre_current_5y.csv"
CURRENT = DIRECTORY / "Current_activity.csv"
EVENT_OUTPUT = DIRECTORY / "dormant_activity_6y.csv"
COVERAGE_OUTPUT = DIRECTORY / "dormant_activity_6y_coverage.csv"
START = datetime(2020, 9, 1, tzinfo=timezone.utc)
END = datetime(2026, 9, 1, tzinfo=timezone.utc)
ACTIVITY_TYPES = (
    "Commit",
    "PR Opened",
    "Issue Opened",
    "PR Review",
    "PR Conversation Comment",
    "Inline Review Comment",
    "Issue Comment",
)
HISTORY_TYPES = {
    "Commit": "Commit",
    "PullRequest": "PR Opened",
    "Issue": "Issue Opened",
    "Review": "PR Review",
    "PRConversationComment": "PR Conversation Comment",
    "InlineReviewComment": "Inline Review Comment",
    "IssueComment": "Issue Comment",
}
EVENT_FIELDS = (
    "EventId", "SourceEventId", "TimestampUtc", "Period", "ActivityType",
    "AccountKey", "IdentityBasis", "ActorLogin", "GitHubUserId",
    "GitAuthorName", "GitAuthorEmail", "AccountType", "AuthorAssociation",
    "Sha", "ArtifactNumber", "ArtifactUrl", "SourceFile", "OriginalSourceDataset",
)
COVERAGE_FIELDS = (
    "Period", "StartUtc", "EndExclusiveUtc", "EventCount", "HistoricalEventCount",
    "CurrentEventCount", "DistinctAccountKeys", "MissingAccountKeyCount",
    "ObservedMonths", "MonthsWithNoEvents", "ActivityTypeCount", "ActivityTypesPresent",
    *ACTIVITY_TYPES,
)


# Load source rows with explicit schema and malformed-row checks.
def read_csv(path: Path, required: set[str]) -> list[dict[str, str]]:
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        fields = reader.fieldnames or []
        if len(fields) != len(set(fields)):
            raise ValueError(f"{path.name}: duplicate CSV headers")
        missing = required - set(fields)
        if missing:
            raise ValueError(f"{path.name}: missing columns: {', '.join(sorted(missing))}")
        rows = list(reader)
    if any(None in row or any(value is None for value in row.values()) for row in rows):
        raise ValueError(f"{path.name}: malformed CSV row")
    return rows


# Parse offset-aware timestamps and represent every event in UTC.
def utc_timestamp(raw: str, source: str, line: int) -> datetime:
    if not raw.strip():
        raise ValueError(f"{source} row {line}: missing TimestampUtc")
    try:
        value = datetime.fromisoformat(raw.strip().replace("Z", "+00:00"))
    except ValueError as error:
        raise ValueError(f"{source} row {line}: invalid TimestampUtc={raw!r}") from error
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{source} row {line}: timestamp has no timezone")
    value = value.astimezone(timezone.utc)
    if not START <= value < END:
        raise ValueError(f"{source} row {line}: timestamp outside six-year window")
    return value


# Use repository item numbers, GitHub event IDs, and commit SHAs as stable IDs.
def history_event_id(row: dict[str, str], line: int) -> str:
    source_type = row["ActivityType"].strip()
    source_key = row["EventKey"].strip()
    if not source_key.startswith(f"{source_type}:"):
        raise ValueError(f"{HISTORY.name} row {line}: invalid EventKey={source_key!r}")
    suffix = source_key.split(":", 1)[1]
    if source_type == "Commit":
        sha = row["Sha"].strip().lower()
        if len(sha) != 40 or any(character not in "0123456789abcdef" for character in sha):
            raise ValueError(f"{HISTORY.name} row {line}: invalid commit Sha")
        if suffix.lower() != sha:
            raise ValueError(f"{HISTORY.name} row {line}: EventKey and Sha differ")
        return f"commit:{sha}"
    if not suffix.isdecimal():
        raise ValueError(f"{HISTORY.name} row {line}: invalid numeric EventKey")
    if source_type in {"Issue", "PullRequest"}:
        number = row["ItemNumber"].strip()
        if not number.isdecimal() or int(number) != int(suffix):
            raise ValueError(f"{HISTORY.name} row {line}: EventKey and ItemNumber differ")
        prefix = "issue-number" if source_type == "Issue" else "pr-number"
        return f"{prefix}:{int(number)}"
    prefixes = {
        "Review": "review",
        "IssueComment": "issue-comment",
        "PRConversationComment": "pr-conversation-comment",
        "InlineReviewComment": "inline-review-comment",
    }
    return f"{prefixes[source_type]}:{int(suffix)}"


# Normalize current event IDs without changing their supplied account keys.
def current_event_id(row: dict[str, str], line: int) -> str:
    activity_type = row["ActivityType"].strip()
    source_id = row["EventId"].strip()
    if activity_type in {"Issue Opened", "PR Opened"}:
        number = row["ArtifactNumber"].strip()
        if not number.isdecimal() or not source_id.split(":", 1)[-1].isdecimal():
            raise ValueError(f"{CURRENT.name} row {line}: missing issue or PR number/ID")
        expected = "issue:" if activity_type == "Issue Opened" else "pr:"
        if not source_id.startswith(expected):
            raise ValueError(f"{CURRENT.name} row {line}: unexpected EventId prefix")
        prefix = "issue-number" if activity_type == "Issue Opened" else "pr-number"
        return f"{prefix}:{int(number)}"
    prefixes = {
        "Commit": ("commit:", "commit"),
        "PR Review": ("review:", "review"),
        "Issue Comment": ("issue-comment:", "issue-comment"),
        "PR Conversation Comment": ("pr-comment:", "pr-conversation-comment"),
        "Inline Review Comment": ("pr-comment:", "inline-review-comment"),
    }
    source_prefix, canonical_prefix = prefixes[activity_type]
    if not source_id.startswith(source_prefix):
        raise ValueError(f"{CURRENT.name} row {line}: unexpected EventId prefix")
    suffix = source_id[len(source_prefix):]
    if activity_type == "Commit":
        suffix = suffix.lower()
        if len(suffix) != 40 or any(character not in "0123456789abcdef" for character in suffix):
            raise ValueError(f"{CURRENT.name} row {line}: invalid commit SHA")
    elif not suffix.isdecimal():
        raise ValueError(f"{CURRENT.name} row {line}: invalid numeric EventId")
    return f"{canonical_prefix}:{suffix}"


# Keep GitHub, login-only, and Git-author identities separate.
def history_identity(row: dict[str, str], line: int) -> tuple[str, str]:
    user_id = row["GitHubUserId"].strip()
    login = row["Login"].strip()
    name = row["GitAuthorName"].strip()
    email = row["GitAuthorEmail"].strip()
    if user_id:
        if not user_id.isdecimal():
            raise ValueError(f"{HISTORY.name} row {line}: invalid GitHubUserId")
        return f"GH:{int(user_id)}", "GitHubUserId"
    if login:
        return f"LOGIN:{login.casefold()}", "LoginOnly"
    if name or email:
        return f"GIT_AUTHOR:{name.casefold()}|{email.casefold()}", "GitAuthorIdentity"
    return "", "Missing"


# Assign one warm-up year and ten nonoverlapping six-month periods.
def period_for(timestamp: datetime) -> str:
    month_index = (timestamp.year - 2020) * 12 + timestamp.month - 9
    if not 0 <= month_index < 72:
        raise ValueError(f"No period for timestamp {timestamp.isoformat()}")
    return "WarmUp" if month_index < 12 else f"P{(month_index - 12) // 6 + 1}"


def month_start(month_index: int) -> datetime:
    absolute_month = 2020 * 12 + 8 + month_index
    return datetime(absolute_month // 12, absolute_month % 12 + 1, 1, tzinfo=timezone.utc)


def period_bounds() -> list[tuple[str, datetime, datetime]]:
    return [("WarmUp", START, month_start(12))] + [
        (f"P{number}", month_start(12 + 6 * (number - 1)), month_start(12 + 6 * number))
        for number in range(1, 11)
    ]


# Convert source rows into one common event schema with provenance fields.
def normalize_history(row: dict[str, str], line: int) -> dict[str, str]:
    source_type = row["ActivityType"].strip()
    if source_type not in HISTORY_TYPES:
        raise ValueError(f"{HISTORY.name} row {line}: unknown ActivityType={source_type!r}")
    timestamp = utc_timestamp(row["TimestampUtc"], HISTORY.name, line)
    account_key, basis = history_identity(row, line)
    return {
        "EventId": history_event_id(row, line),
        "SourceEventId": row["EventKey"].strip(),
        "TimestampUtc": timestamp.isoformat().replace("+00:00", "Z"),
        "Period": period_for(timestamp),
        "ActivityType": HISTORY_TYPES[source_type],
        "AccountKey": account_key,
        "IdentityBasis": basis,
        "ActorLogin": row["Login"],
        "GitHubUserId": row["GitHubUserId"],
        "GitAuthorName": row["GitAuthorName"],
        "GitAuthorEmail": row["GitAuthorEmail"],
        "AccountType": row["AccountType"],
        "AuthorAssociation": row["AuthorAssociation"],
        "Sha": row["Sha"],
        "ArtifactNumber": row["ItemNumber"],
        "ArtifactUrl": row["Url"],
        "SourceFile": HISTORY.name,
        "OriginalSourceDataset": "",
    }


def normalize_current(row: dict[str, str], line: int) -> dict[str, str]:
    activity_type = row["ActivityType"].strip()
    if activity_type not in ACTIVITY_TYPES:
        raise ValueError(f"{CURRENT.name} row {line}: unknown ActivityType={activity_type!r}")
    timestamp = utc_timestamp(row["TimestampUtc"], CURRENT.name, line)
    account_key = row["AccountKey"].strip()
    if not account_key:
        raise ValueError(f"{CURRENT.name} row {line}: missing AccountKey")
    return {
        "EventId": current_event_id(row, line),
        "SourceEventId": row["EventId"].strip(),
        "TimestampUtc": timestamp.isoformat().replace("+00:00", "Z"),
        "Period": period_for(timestamp),
        "ActivityType": activity_type,
        "AccountKey": account_key,
        "IdentityBasis": row["AccountKeyBasis"],
        "ActorLogin": row["ActorLogin"],
        "GitHubUserId": row["ActorId"],
        "GitAuthorName": "",
        "GitAuthorEmail": "",
        "AccountType": "",
        "AuthorAssociation": "",
        "Sha": row["EventId"].split(":", 1)[1] if activity_type == "Commit" else "",
        "ArtifactNumber": row["ArtifactNumber"],
        "ArtifactUrl": row["ArtifactUrl"],
        "SourceFile": CURRENT.name,
        "OriginalSourceDataset": row["SourceDataset"],
    }


# Deduplicate exact events and stop on conflicting event identities or timestamps.
def collect_events(history: list[dict[str, str]], current: list[dict[str, str]]) -> tuple[list[dict[str, str]], Counter]:
    events = {}
    duplicate_counts = Counter()
    for source, rows, normalizer in (
        (HISTORY, history, normalize_history),
        (CURRENT, current, normalize_current),
    ):
        for line, row in enumerate(rows, start=2):
            event = normalizer(row, line)
            event_id = event["EventId"]
            if event_id in events:
                earlier = events[event_id]
                essentials = ("TimestampUtc", "ActivityType", "AccountKey")
                if any(event[field] != earlier[field] for field in essentials):
                    raise ValueError(f"{source.name} row {line}: conflicting duplicate EventId={event_id}")
                duplicate_counts[source.name] += 1
                continue
            events[event_id] = event
    ordered = sorted(events.values(), key=lambda event: (event["TimestampUtc"], event["EventId"]))
    return ordered, duplicate_counts


# Summarize event volume, observed months, and all seven types per period.
def coverage_rows(events: list[dict[str, str]]) -> list[dict[str, str | int]]:
    by_period = defaultdict(list)
    for event in events:
        by_period[event["Period"]].append(event)
    coverage = []
    for name, start, end in period_bounds():
        rows = by_period[name]
        counts = Counter(row["ActivityType"] for row in rows)
        expected_months = {
            month_start(index).strftime("%Y-%m")
            for index in range(72)
            if start <= month_start(index) < end
        }
        observed_months = {row["TimestampUtc"][:7] for row in rows}
        coverage.append({
            "Period": name,
            "StartUtc": start.isoformat().replace("+00:00", "Z"),
            "EndExclusiveUtc": end.isoformat().replace("+00:00", "Z"),
            "EventCount": len(rows),
            "HistoricalEventCount": sum(row["SourceFile"] == HISTORY.name for row in rows),
            "CurrentEventCount": sum(row["SourceFile"] == CURRENT.name for row in rows),
            "DistinctAccountKeys": len({row["AccountKey"] for row in rows if row["AccountKey"]}),
            "MissingAccountKeyCount": sum(not row["AccountKey"] for row in rows),
            "ObservedMonths": len(observed_months),
            "MonthsWithNoEvents": ";".join(sorted(expected_months - observed_months)),
            "ActivityTypeCount": sum(counts[activity_type] > 0 for activity_type in ACTIVITY_TYPES),
            "ActivityTypesPresent": ";".join(
                activity_type for activity_type in ACTIVITY_TYPES if counts[activity_type] > 0
            ),
            **{activity_type: counts[activity_type] for activity_type in ACTIVITY_TYPES},
        })
    return coverage


# Validate the final partition and write each CSV only after checks pass.
def validate_results(events: list[dict[str, str]], coverage: list[dict[str, str | int]]) -> None:
    bounds = period_bounds()
    if len(coverage) != 11 or bounds[0][1] != START or bounds[-1][2] != END:
        raise ValueError("Period boundaries do not cover the requested six years")
    if any(bounds[index][2] != bounds[index + 1][1] for index in range(10)):
        raise ValueError("Period boundaries contain a gap or overlap")
    if len({event["EventId"] for event in events}) != len(events):
        raise ValueError("Duplicate EventId remained after deduplication")
    if sum(row["EventCount"] for row in coverage) != len(events):
        raise ValueError("Coverage event counts do not match output events")
    for row in coverage:
        if sum(row[activity_type] for activity_type in ACTIVITY_TYPES) != row["EventCount"]:
            raise ValueError(f"{row['Period']}: activity-type counts do not match EventCount")
        if row["HistoricalEventCount"] + row["CurrentEventCount"] != row["EventCount"]:
            raise ValueError(f"{row['Period']}: source counts do not match EventCount")


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


# Generate both files and print coverage and source-data limitations.
def main() -> None:
    history = read_csv(HISTORY, {
        "EventKey", "ActivityType", "TimestampUtc", "Login", "GitHubUserId",
        "AccountType", "AuthorAssociation", "ItemNumber", "Sha",
        "GitAuthorName", "GitAuthorEmail", "Url",
    })
    current = read_csv(CURRENT, {
        "EventId", "ActivityType", "TimestampUtc", "AccountKey", "ActorLogin",
        "ActorId", "AccountKeyBasis", "SourceDataset", "ArtifactNumber", "ArtifactUrl",
    })
    events, duplicates = collect_events(history, current)
    coverage = coverage_rows(events)
    validate_results(events, coverage)
    write_csv(EVENT_OUTPUT, EVENT_FIELDS, events)
    write_csv(COVERAGE_OUTPUT, COVERAGE_FIELDS, coverage)

    identity_counts = Counter(event["IdentityBasis"] for event in events if event["SourceFile"] == HISTORY.name)
    print(f"Source rows: history={len(history)}, current={len(current)}")
    print(f"Unique six-year events: {len(events)}")
    print(f"Exact duplicate events removed: {sum(duplicates.values())}")
    print("Validation: timestamps, event IDs, seven activity types, and period totals passed.")
    for row in coverage:
        print(
            f"  {row['Period']}: {row['StartUtc'][:10]} to {row['EndExclusiveUtc'][:10]} "
            f"(exclusive), events={row['EventCount']}, types={row['ActivityTypeCount']}/7, "
            f"observed_months={row['ObservedMonths']}"
        )
    print("Limitations: historical GitHubUserId is absent for some events; login-only and Git-author keys remain separate.")
    print(
        "Historical identity basis: "
        + ", ".join(f"{name}={identity_counts[name]}" for name in (
            "GitHubUserId", "LoginOnly", "GitAuthorIdentity", "Missing"
        ))
    )
    print("Limitations: months and activity types with events do not prove complete GitHub coverage.")
    print("No Human/Bot filtering, cross-identity merging, or dormant classification was performed.")
    print(f"Outputs: {EVENT_OUTPUT}; {COVERAGE_OUTPUT}")


if __name__ == "__main__":
    main()
