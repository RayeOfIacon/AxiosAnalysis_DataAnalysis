"""Build a current-period event table without classifying accounts."""

import csv
from datetime import datetime, timezone
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "Data" / "Extracted_data"
OUTPUT = Path(__file__).resolve().parent / "Current_activity.csv"
WINDOW_START = datetime(2025, 9, 1, tzinfo=timezone.utc)
WINDOW_END = datetime(2026, 9, 1, tzinfo=timezone.utc)
FIELDS = [
    "EventId",
    "ActivityType",
    "TimestampUtc",
    "AccountKey",
    "ActorLogin",
    "ActorId",
    "AccountKeyBasis",
    "SourceDataset",
    "ArtifactNumber",
    "ArtifactUrl",
]


# Read source rows and reject missing columns before building events.
def read_source(filename: str, required: set[str]) -> list[dict[str, str]]:
    path = DATA / filename
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        missing = required - set(reader.fieldnames or [])
        if missing:
            raise ValueError(f"{filename} is missing columns: {sorted(missing)}")
        return list(reader)


# Parse event times as UTC and apply the observation-window boundaries.
def timestamp_in_window(value: str, source: str) -> str | None:
    raw = value.strip()
    if not raw:
        raise ValueError(f"Missing event timestamp in {source}")
    try:
        moment = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError as error:
        raise ValueError(f"Invalid event timestamp in {source}: {raw}") from error
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    moment = moment.astimezone(timezone.utc)
    if not WINDOW_START <= moment < WINDOW_END:
        return None
    return moment.isoformat(timespec="seconds").replace("+00:00", "Z")


# Preserve source account identifiers without merging login-only records.
def build_event(
    event_id: str,
    activity_type: str,
    timestamp: str,
    actor_login: str,
    actor_id: str,
    source: str,
    artifact_number: str,
    artifact_url: str,
) -> dict[str, str] | None:
    timestamp_utc = timestamp_in_window(timestamp, source)
    if timestamp_utc is None:
        return None
    event_id = event_id.strip()
    actor_login = actor_login.strip()
    actor_id = actor_id.strip()
    if not event_id or not event_id.split(":", 1)[-1]:
        raise ValueError(f"Missing event identifier in {source}")
    if not actor_id and not actor_login:
        raise ValueError(f"Missing actor account in {source}: {event_id}")
    account_key = f"GH:{actor_id}" if actor_id else f"LOGIN:{actor_login.casefold()}"
    return {
        "EventId": event_id,
        "ActivityType": activity_type,
        "TimestampUtc": timestamp_utc,
        "AccountKey": account_key,
        "ActorLogin": actor_login,
        "ActorId": actor_id,
        "AccountKeyBasis": "GitHubUserId" if actor_id else "LoginOnly",
        "SourceDataset": source,
        "ArtifactNumber": artifact_number.strip(),
        "ArtifactUrl": artifact_url.strip(),
    }


# Convert the seven Q1 activity types into one event schema.
def collect_events() -> list[dict[str, str]]:
    events: list[dict[str, str]] = []

    source = "github_activity_events.csv"
    for row in read_source(source, {"Activity", "TimestampUtc", "Login", "Url"}):
        if row["Activity"] != "Commit":
            continue
        sha = row["Url"].rstrip("/").rsplit("/", 1)[-1]
        event = build_event(
            f"commit:{sha}", "Commit", row["TimestampUtc"], row["Login"],
            "", source, sha, row["Url"],
        )
        if event is not None:
            events.append(event)

    source = "pull_requests_current.csv"
    for row in read_source(
        source, {"PrId", "PrNumber", "AuthorLogin", "AuthorId", "CreatedAt", "Url"}
    ):
        event = build_event(
            f"pr:{row['PrId'] or row['PrNumber']}", "PR Opened", row["CreatedAt"],
            row["AuthorLogin"], row["AuthorId"], source, row["PrNumber"], row["Url"],
        )
        if event is not None:
            events.append(event)

    source = "issues_current.csv"
    for row in read_source(
        source, {"IssueId", "IssueNumber", "AuthorLogin", "AuthorId", "CreatedAt", "Url"}
    ):
        event = build_event(
            f"issue:{row['IssueId'] or row['IssueNumber']}", "Issue Opened",
            row["CreatedAt"], row["AuthorLogin"], row["AuthorId"], source,
            row["IssueNumber"], row["Url"],
        )
        if event is not None:
            events.append(event)

    source = "pr_reviews_current.csv"
    for row in read_source(
        source, {"ReviewId", "PrNumber", "ReviewerLogin", "ReviewerId", "SubmittedAt", "Url"}
    ):
        event = build_event(
            f"review:{row['ReviewId']}", "PR Review", row["SubmittedAt"],
            row["ReviewerLogin"], row["ReviewerId"], source, row["PrNumber"], row["Url"],
        )
        if event is not None:
            events.append(event)

    source = "pr_comments_current.csv"
    for row in read_source(
        source, {"CommentId", "CommentType", "PrNumber", "ActorLogin", "ActorId", "CreatedAt", "Url"}
    ):
        comment_type = row["CommentType"].strip()
        if comment_type not in {"Conversation", "InlineReview"}:
            raise ValueError(f"Unknown PR comment type: {comment_type}")
        activity_type = (
            "PR Conversation Comment" if comment_type == "Conversation" else "Inline Review Comment"
        )
        event = build_event(
            f"pr-comment:{row['CommentId']}", activity_type, row["CreatedAt"],
            row["ActorLogin"], row["ActorId"], source, row["PrNumber"], row["Url"],
        )
        if event is not None:
            events.append(event)

    source = "issue_comments_current.csv"
    for row in read_source(
        source, {"CommentId", "IssueNumber", "ActorLogin", "ActorId", "CreatedAt", "Url"}
    ):
        event = build_event(
            f"issue-comment:{row['CommentId']}", "Issue Comment", row["CreatedAt"],
            row["ActorLogin"], row["ActorId"], source, row["IssueNumber"], row["Url"],
        )
        if event is not None:
            events.append(event)

    return events


# Deduplicate stable event IDs and write the single requested CSV.
def main() -> None:
    unique: dict[str, dict[str, str]] = {}
    for event in collect_events():
        event_id = event["EventId"]
        previous = unique.get(event_id)
        if previous is not None and previous != event:
            raise ValueError(f"Conflicting rows for event {event_id}")
        unique[event_id] = event

    ordered = sorted(unique.values(), key=lambda row: (row["TimestampUtc"], row["EventId"]))
    with OUTPUT.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=FIELDS)
        writer.writeheader()
        writer.writerows(ordered)
    print(f"Wrote {len(ordered)} current-period events to {OUTPUT}")


if __name__ == "__main__":
    main()
