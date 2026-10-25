"""Identify six-calendar-month dormancy episodes from the six-year event table."""

import calendar
import csv
import os
import tempfile
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path


DIRECTORY = Path(__file__).resolve().parent
INPUT = DIRECTORY / "dormant_activity_6y.csv"
EVENT_OUTPUT = DIRECTORY / "dormant_identification_6y.csv"
SUMMARY_OUTPUT = DIRECTORY / "dormant_identification_6y_summary.csv"
START = datetime(2020, 9, 1, tzinfo=timezone.utc)
END = datetime(2026, 9, 1, tzinfo=timezone.utc)
EVENT_FIELDS = (
    "AccountKey", "EpisodeNumber", "LastActivityUtc", "LastActivityDate",
    "DormancyOnsetUtc", "DormancyOnsetDate", "Period", "ReactivationStatus",
    "ReactivationUtc", "ReactivationPeriod",
)
SUMMARY_FIELDS = (
    "Period", "StartUtc", "EndExclusiveUtc", "AtRiskStartUtc", "AtRiskEndExclusiveUtc",
    "AtRiskContributors",
    "NewDormantContributors", "NewDormancyRate", "ReactivatedContributors",
)


# Move a UTC timestamp by calendar months, clamping at the target month's end.
def add_months(value: datetime, months: int) -> datetime:
    month_index = value.year * 12 + value.month - 1 + months
    year, zero_based_month = divmod(month_index, 12)
    month = zero_based_month + 1
    day = min(value.day, calendar.monthrange(year, month)[1])
    return value.replace(year=year, month=month, day=day)


# Define the warm-up interval and ten consecutive six-month intervals.
def period_bounds() -> list[tuple[str, datetime, datetime]]:
    first_period = datetime(2021, 9, 1, tzinfo=timezone.utc)
    return [("WarmUp", START, first_period)] + [
        (f"P{number}", add_months(first_period, 6 * (number - 1)),
         add_months(first_period, 6 * number))
        for number in range(1, 11)
    ]


def period_for(value: datetime) -> str:
    for name, start, end in period_bounds():
        if start <= value < end:
            return name
    raise ValueError(f"Timestamp outside the six-year window: {value.isoformat()}")


# Read and validate event timestamps, periods, IDs, and usable account keys.
def read_events() -> tuple[dict[str, list[datetime]], int, int]:
    with INPUT.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        fields = reader.fieldnames or []
        if len(fields) != len(set(fields)):
            raise ValueError(f"{INPUT.name}: duplicate CSV headers")
        required = {"EventId", "AccountKey", "TimestampUtc", "Period"}
        missing = required - set(fields)
        if missing:
            raise ValueError(f"{INPUT.name}: missing columns: {', '.join(sorted(missing))}")
        by_account = defaultdict(list)
        seen_ids = set()
        excluded = 0
        total = 0
        for line, row in enumerate(reader, start=2):
            if None in row or any(value is None for value in row.values()):
                raise ValueError(f"{INPUT.name} row {line}: malformed CSV row")
            total += 1
            event_id = row["EventId"].strip()
            if not event_id or event_id in seen_ids:
                raise ValueError(f"{INPUT.name} row {line}: missing or duplicate EventId")
            seen_ids.add(event_id)
            raw_time = row["TimestampUtc"].strip()
            try:
                value = datetime.fromisoformat(raw_time.replace("Z", "+00:00"))
            except ValueError as error:
                raise ValueError(f"{INPUT.name} row {line}: invalid TimestampUtc") from error
            if value.tzinfo is None or value.utcoffset() is None:
                raise ValueError(f"{INPUT.name} row {line}: timestamp has no timezone")
            value = value.astimezone(timezone.utc)
            if not START <= value < END or row["Period"] != period_for(value):
                raise ValueError(f"{INPUT.name} row {line}: timestamp and period disagree")
            key = row["AccountKey"]
            if not key:
                excluded += 1
                continue
            if key != key.strip():
                raise ValueError(f"{INPUT.name} row {line}: padded AccountKey")
            by_account[key].append(value)
    for timestamps in by_account.values():
        timestamps.sort()
    return by_account, excluded, total


# Find each onset and the first later event, including warm-up episodes.
def find_episodes(by_account: dict[str, list[datetime]]) -> list[dict[str, str | int]]:
    episodes = []
    for key, timestamps in by_account.items():
        episode_number = 0
        for index, last_activity in enumerate(timestamps):
            onset = add_months(last_activity, 6)
            next_activity = timestamps[index + 1] if index + 1 < len(timestamps) else None
            if onset >= END or (next_activity is not None and next_activity < onset):
                continue
            episode_number += 1
            episodes.append({
                "AccountKey": key,
                "EpisodeNumber": episode_number,
                "LastActivityUtc": last_activity.isoformat().replace("+00:00", "Z"),
                "LastActivityDate": last_activity.date().isoformat(),
                "DormancyOnsetUtc": onset.isoformat().replace("+00:00", "Z"),
                "DormancyOnsetDate": onset.date().isoformat(),
                "Period": period_for(onset),
                "ReactivationStatus": (
                    "Reactivated" if next_activity is not None else "NotObservedByWindowEnd"
                ),
                "ReactivationUtc": (
                    next_activity.isoformat().replace("+00:00", "Z") if next_activity else ""
                ),
                "ReactivationPeriod": period_for(next_activity) if next_activity else "",
            })
    return sorted(episodes, key=lambda row: (row["DormancyOnsetUtc"], row["AccountKey"]))


# Compare each period's new onsets with its preceding six-month active cohort.
def summarize(
    by_account: dict[str, list[datetime]], episodes: list[dict[str, str | int]],
) -> tuple[list[dict[str, str | int]], list[dict[str, str | int]]]:
    new_events = [row for row in episodes if row["Period"] != "WarmUp"]
    new_by_period = defaultdict(set)
    reactivated_by_period = defaultdict(set)
    for row in episodes:
        if row["Period"] != "WarmUp":
            key = (row["Period"], row["AccountKey"])
            if row["AccountKey"] in new_by_period[row["Period"]]:
                raise ValueError(f"Multiple dormancy onsets in one period: {key}")
            new_by_period[row["Period"]].add(row["AccountKey"])
        if row["ReactivationPeriod"] not in {"", "WarmUp"}:
            reactivated_by_period[row["ReactivationPeriod"]].add(row["AccountKey"])

    summary = []
    for name, start, end in period_bounds()[1:]:
        previous_start = add_months(start, -6)
        at_risk = {
            key for key, timestamps in by_account.items()
            if any(previous_start <= timestamp < start for timestamp in timestamps)
        }
        new_dormant = new_by_period[name]
        if not new_dormant <= at_risk:
            raise ValueError(f"{name}: new dormant contributors are outside the at-risk cohort")
        rate = len(new_dormant) / len(at_risk) if at_risk else None
        if rate is None and new_dormant:
            raise ValueError(f"{name}: nonzero dormancy count with empty at-risk cohort")
        summary.append({
            "Period": name,
            "StartUtc": start.isoformat().replace("+00:00", "Z"),
            "EndExclusiveUtc": end.isoformat().replace("+00:00", "Z"),
            "AtRiskStartUtc": previous_start.isoformat().replace("+00:00", "Z"),
            "AtRiskEndExclusiveUtc": start.isoformat().replace("+00:00", "Z"),
            "AtRiskContributors": len(at_risk),
            "NewDormantContributors": len(new_dormant),
            "NewDormancyRate": f"{rate:.6f}" if rate is not None else "",
            "ReactivatedContributors": len(reactivated_by_period[name]),
        })
    if sum(row["NewDormantContributors"] for row in summary) != len(new_events):
        raise ValueError("Summary counts do not match contributor-level dormancy events")
    return new_events, summary


# Write outputs only after all activity and summary checks have passed.
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


# Produce ten period summaries without filtering accounts or merging identities.
def main() -> None:
    by_account, excluded, input_events = read_events()
    episodes = find_episodes(by_account)
    new_events, summary = summarize(by_account, episodes)
    if len(summary) != 10 or sum(len(values) for values in by_account.values()) + excluded != input_events:
        raise ValueError("Input event coverage or period summary is inconsistent")
    write_csv(EVENT_OUTPUT, EVENT_FIELDS, new_events)
    write_csv(SUMMARY_OUTPUT, SUMMARY_FIELDS, summary)

    repeated_accounts = sum(count > 1 for count in Counter(row["AccountKey"] for row in episodes).values())
    print(f"Input events: {input_events}; usable accounts: {len(by_account)}")
    print(f"Events excluded for missing AccountKey: {excluded}")
    print(f"New dormancy episodes in P1-P10: {len(new_events)}")
    print(f"Accounts with repeated observed episodes, including warm-up: {repeated_accounts}")
    print("Period | At risk | New dormant | Rate | Reactivated")
    for row in summary:
        print(
            f"{row['Period']} | {row['AtRiskContributors']} | "
            f"{row['NewDormantContributors']} | {row['NewDormancyRate']} | "
            f"{row['ReactivatedContributors']}"
        )
    print("Validation: ten period totals, unique account-period onsets, and at-risk membership passed.")
    print("Definition: onset is the UTC six-calendar-month anniversary of the last event; month-end dates are clamped.")
    print("Limitations: activity before 2020-09 and after 2026-08 is unobserved; reactivation status is right-censored.")
    print("Account keys are used as supplied, with no Human/Bot filtering or identity merging.")
    print(f"Outputs: {EVENT_OUTPUT}; {SUMMARY_OUTPUT}")


if __name__ == "__main__":
    main()
