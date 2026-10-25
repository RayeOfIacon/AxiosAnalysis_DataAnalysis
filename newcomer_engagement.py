"""Compare repeat activity, cross-month activity, and existing key status."""

import csv
import os
import tempfile
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path


DIRECTORY = Path(__file__).resolve().parent
IDENTIFICATION = DIRECTORY / "newcomer_identification.csv"
ACTIVITY = DIRECTORY / "Current_activity.csv"
PARTICIPATION = DIRECTORY / "newcomer_participation_contributors.csv"
KEY_CLASSIFICATION = DIRECTORY / "contributor_key.csv"
ACCOUNTS = DIRECTORY / "Current_accounts.csv"
CONTRIBUTOR_OUTPUT = DIRECTORY / "newcomer_engagement_contributors.csv"
SUMMARY_OUTPUT = DIRECTORY / "newcomer_engagement_summary.csv"

START = datetime(2025, 9, 1, tzinfo=timezone.utc)
END = datetime(2026, 9, 1, tzinfo=timezone.utc)
EXPECTED_GROUPS = {
    "Newcomer Candidate": 857,
    "Returning Contributor": 67,
    "Uncertain History": 13,
}
COMPARE_GROUPS = ("Newcomer Candidate", "Returning Contributor")
CONTRIBUTOR_FIELDS = (
    "AccountKey", "Classification", "ActivityCount", "ActiveMonths",
    "ActiveMonthList", "FirstActivityUtc", "SecondActivityUtc",
    "SecondActiveMonthFirstActivityUtc", "LastActivityUtc",
    "IsRepeatParticipant", "IsCrossMonthParticipant", "IsKeyContributor",
)
SUMMARY_FIELDS = (
    "Classification", "GroupSize", "RepeatParticipationCount",
    "RepeatParticipationPercentage", "CrossMonthParticipationCount",
    "CrossMonthParticipationPercentage", "KeyContributorCount",
    "KeyContributorPercentage",
)


# Read each input with strict schema and row checks.
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


# Index contributor-level inputs without changing any existing identity.
def index_rows(path: Path, required: set[str]) -> dict[str, dict[str, str]]:
    rows = read_csv(path, required | {"AccountKey"})
    indexed = {}
    for line, row in enumerate(rows, start=2):
        key = row["AccountKey"].strip()
        if not key or key in indexed:
            raise ValueError(f"{path.name} row {line}: blank or duplicate AccountKey")
        indexed[key] = row
    return indexed


# Parse timestamps as UTC and enforce the current observation window.
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


def utc_text(value: datetime | None) -> str:
    return value.isoformat(timespec="seconds").replace("+00:00", "Z") if value else ""


# Reuse the account table's original-to-canonical event mapping.
def account_mapping() -> tuple[dict[str, dict[str, str]], dict[str, str]]:
    accounts = index_rows(ACCOUNTS, {"OriginalAccountKeys", "EventCount"})
    original_to_canonical = {}
    for key, row in accounts.items():
        originals = [part.strip() for part in row["OriginalAccountKeys"].split(";")]
        if not originals or any(not part for part in originals):
            raise ValueError(f"{key}: invalid OriginalAccountKeys")
        for original in originals:
            prior = original_to_canonical.setdefault(original, key)
            if prior != key:
                raise ValueError(f"{original}: maps to multiple canonical accounts")
    return accounts, original_to_canonical


# Assign unique current events to accounts and retain their UTC times.
def current_events(
    accounts: dict[str, dict[str, str]], mapping: dict[str, str]
) -> dict[str, list[tuple[datetime, str]]]:
    rows = read_csv(ACTIVITY, {"EventId", "AccountKey", "TimestampUtc"})
    events = defaultdict(list)
    seen_ids = set()
    for line, row in enumerate(rows, start=2):
        event_id = row["EventId"].strip()
        if not event_id or event_id in seen_ids:
            raise ValueError(f"{ACTIVITY.name} row {line}: blank or duplicate EventId")
        seen_ids.add(event_id)
        original = row["AccountKey"].strip()
        key = mapping.get(original)
        if key is None:
            raise ValueError(f"{ACTIVITY.name} row {line}: unmapped AccountKey={original!r}")
        events[key].append((parse_time(row["TimestampUtc"], ACTIVITY.name, line), event_id))
    for key, account in accounts.items():
        if len(events[key]) != int(account["EventCount"]):
            raise ValueError(f"{key}: event count differs from Current_accounts.csv")
    if sum(len(group) for group in events.values()) != len(rows):
        raise ValueError("Current event mapping did not retain all events")
    return events


# Keep the existing key flag and verify it covers exactly 68 Humans.
def key_flags(keys: dict[str, dict[str, str]]) -> dict[str, bool]:
    flags = {}
    for key, row in keys.items():
        raw = row["IsKeyCandidate"].strip()
        if raw not in ("True", "False"):
            raise ValueError(f"{key}: invalid IsKeyCandidate={raw!r}")
        flags[key] = raw == "True"
    if sum(flags.values()) != 68:
        raise ValueError(f"Expected 68 Key Contributors, found {sum(flags.values())}")
    return flags


# Calculate participation flags and reconcile existing activity metrics.
def contributor_rows() -> list[dict[str, str | int]]:
    identified = index_rows(IDENTIFICATION, {"Classification", "FirstCurrentActivityUtc"})
    participation = index_rows(PARTICIPATION, {"Classification", "ActivityCount", "FirstActivityUtc"})
    keys = index_rows(KEY_CLASSIFICATION, {"IsKeyCandidate", "ActivityTotal", "ActiveMonths", "ActiveMonthList"})
    accounts, mapping = account_mapping()
    events = current_events(accounts, mapping)
    populations = [set(table) for table in (identified, participation, keys)]
    if len(identified) != 937 or not populations[0] == populations[1] == populations[2]:
        raise ValueError("The three Human contributor inputs do not contain the same 937 AccountKeys")
    labels = Counter(row["Classification"] for row in identified.values())
    if labels != EXPECTED_GROUPS:
        raise ValueError(f"Unexpected newcomer classification counts: {dict(labels)}")
    flags = key_flags(keys)

    result = []
    for key in sorted(identified):
        label = identified[key]["Classification"]
        if participation[key]["Classification"] != label:
            raise ValueError(f"{key}: participation classification mismatch")
        ordered = sorted(events[key], key=lambda item: (item[0], item[1]))
        if not ordered:
            raise ValueError(f"{key}: Human contributor has no current activity")
        months = sorted({time.strftime("%Y-%m") for time, _ in ordered})
        count = len(ordered)
        if count != int(participation[key]["ActivityCount"]) or count != int(keys[key]["ActivityTotal"]):
            raise ValueError(f"{key}: activity count disagrees with existing metrics")
        if len(months) != int(keys[key]["ActiveMonths"]) or ";".join(months) != keys[key]["ActiveMonthList"]:
            raise ValueError(f"{key}: active months disagree with existing metrics")
        first = ordered[0][0]
        if first != parse_time(identified[key]["FirstCurrentActivityUtc"], IDENTIFICATION.name, 0):
            raise ValueError(f"{key}: first activity disagrees with identification")
        if first != parse_time(participation[key]["FirstActivityUtc"], PARTICIPATION.name, 0):
            raise ValueError(f"{key}: first activity disagrees with participation")
        first_month = months[0]
        second_month_first = next(
            (time for time, _ in ordered if time.strftime("%Y-%m") != first_month), None
        )
        result.append({
            "AccountKey": key,
            "Classification": label,
            "ActivityCount": count,
            "ActiveMonths": len(months),
            "ActiveMonthList": ";".join(months),
            "FirstActivityUtc": utc_text(first),
            "SecondActivityUtc": utc_text(ordered[1][0]) if count >= 2 else "",
            "SecondActiveMonthFirstActivityUtc": utc_text(second_month_first),
            "LastActivityUtc": utc_text(ordered[-1][0]),
            "IsRepeatParticipant": str(count >= 2),
            "IsCrossMonthParticipant": str(len(months) >= 2),
            "IsKeyContributor": str(flags[key]),
        })
    if sum(int(row["ActivityCount"]) for row in result) != 4002:
        raise ValueError("Expected 4,002 Human activities")
    if sum(row["IsKeyContributor"] == "True" for row in result) != 68:
        raise ValueError("Key Contributor overlap does not total 68")
    return result


# Report each metric as a count and within-group percentage.
def summary_rows(contributors: list[dict[str, str | int]]) -> list[dict[str, str | int]]:
    result = []
    for label in COMPARE_GROUPS:
        members = [row for row in contributors if row["Classification"] == label]
        size = len(members)
        if size != EXPECTED_GROUPS[label]:
            raise ValueError(f"{label}: group size mismatch")
        repeat = sum(row["IsRepeatParticipant"] == "True" for row in members)
        cross_month = sum(row["IsCrossMonthParticipant"] == "True" for row in members)
        key = sum(row["IsKeyContributor"] == "True" for row in members)
        result.append({
            "Classification": label,
            "GroupSize": size,
            "RepeatParticipationCount": repeat,
            "RepeatParticipationPercentage": f"{100 * repeat / size:.2f}",
            "CrossMonthParticipationCount": cross_month,
            "CrossMonthParticipationPercentage": f"{100 * cross_month / size:.2f}",
            "KeyContributorCount": key,
            "KeyContributorPercentage": f"{100 * key / size:.2f}",
        })
    return result


# Save only the requested outputs after every source check passes.
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


def main() -> None:
    contributors = contributor_rows()
    summary = summary_rows(contributors)
    write_csv(CONTRIBUTOR_OUTPUT, CONTRIBUTOR_FIELDS, contributors)
    write_csv(SUMMARY_OUTPUT, SUMMARY_FIELDS, summary)
    print("Validation passed: 937 Humans, 4,002 activities, 68 existing Key Contributors.")
    print("Uncertain History: 13 contributor rows retained and excluded from group comparisons.")
    for row in summary:
        print(
            f"{row['Classification']} (n={row['GroupSize']}): "
            f"repeat={row['RepeatParticipationCount']} ({row['RepeatParticipationPercentage']}%), "
            f"cross-month={row['CrossMonthParticipationCount']} ({row['CrossMonthParticipationPercentage']}%), "
            f"key={row['KeyContributorCount']} ({row['KeyContributorPercentage']}%)."
        )


if __name__ == "__main__":
    main()
