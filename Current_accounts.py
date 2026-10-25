"""List accounts observed in current-period events without classifying them."""

import csv
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
ACTIVITY = Path(__file__).resolve().parent / "Current_activity.csv"
IDENTITY_MAPPING = ROOT / "Analysis_Q1" / "identity_mapping.csv"
OUTPUT = Path(__file__).resolve().parent / "Current_accounts.csv"
WINDOW_START = datetime(2025, 9, 1, tzinfo=timezone.utc)
WINDOW_END = datetime(2026, 9, 1, tzinfo=timezone.utc)
FIELDS = [
    "AccountKey",
    "GitHubUserId",
    "ObservedLogins",
    "AccountMatchStatus",
    "AccountMatchMethods",
    "OriginalAccountKeys",
    "EventCount",
    "LoginOnlyEventCount",
    "FirstActivityUtc",
    "LastActivityUtc",
    "ActivityTypes",
]


# Load CSV rows and check the fields needed for account extraction.
def read_csv(path: Path, required: set[str]) -> list[dict[str, str]]:
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        missing = required - set(reader.fieldnames or [])
        if missing:
            raise ValueError(f"{path.name} is missing columns: {sorted(missing)}")
        return list(reader)


# Build login-to-ID candidates from verified Q1 identity links only.
def verified_login_ids() -> dict[str, set[str]]:
    rows = read_csv(
        IDENTITY_MAPPING,
        {"IdentityType", "MappingStatus", "GitHubLogin", "CanonicalContributorId"},
    )
    by_login: dict[str, set[str]] = defaultdict(set)
    for row in rows:
        if row["IdentityType"] != "GitHubAccount" or row["MappingStatus"] != "Verified":
            continue
        login = row["GitHubLogin"].strip().casefold()
        account_key = row["CanonicalContributorId"].strip()
        if login and account_key.startswith("GH:") and account_key[3:].isdigit():
            by_login[login].add(account_key)
    return by_login


# Validate each event and retain direct numeric IDs as the strongest account evidence.
def validate_events(rows: list[dict[str, str]]) -> dict[str, set[str]]:
    seen_ids: set[str] = set()
    current_login_ids: dict[str, set[str]] = defaultdict(set)
    for row in rows:
        event_id = row["EventId"].strip()
        actor_id = row["ActorId"].strip()
        login = row["ActorLogin"].strip().casefold()
        expected_key = f"GH:{actor_id}" if actor_id else f"LOGIN:{login}"
        expected_basis = "GitHubUserId" if actor_id else "LoginOnly"
        if not event_id or event_id in seen_ids:
            raise ValueError(f"Missing or duplicate event ID: {event_id}")
        if not actor_id and not login:
            raise ValueError(f"Missing actor for event {event_id}")
        if row["AccountKey"] != expected_key or row["AccountKeyBasis"] != expected_basis:
            raise ValueError(f"Inconsistent account fields for event {event_id}")
        timestamp = datetime.fromisoformat(row["TimestampUtc"].replace("Z", "+00:00"))
        if timestamp.tzinfo is None or not WINDOW_START <= timestamp.astimezone(timezone.utc) < WINDOW_END:
            raise ValueError(f"Event outside the current window: {event_id}")
        if actor_id and login:
            current_login_ids[login].add(expected_key)
        seen_ids.add(event_id)
    return current_login_ids


# Merge login-only events only when verified and current IDs do not conflict.
def resolve_account(
    row: dict[str, str],
    verified_ids: dict[str, set[str]],
    current_ids: dict[str, set[str]],
) -> tuple[str, str]:
    if row["ActorId"].strip():
        return row["AccountKey"], "SourceGitHubUserId"

    login = row["ActorLogin"].strip().casefold()
    known = verified_ids.get(login, set())
    observed = current_ids.get(login, set())
    if len(known) == 1 and (not observed or observed == known):
        return next(iter(known)), "VerifiedLoginMapping"
    if len(known) > 1 or len(observed) > 1 or (known and observed and known != observed):
        return row["AccountKey"], "AmbiguousLogin"
    return row["AccountKey"], "LoginOnlyNoVerifiedId"


# Aggregate events by resolved account while keeping the original account keys.
def build_accounts(rows: list[dict[str, str]]) -> list[dict[str, object]]:
    verified_ids = verified_login_ids()
    current_ids = validate_events(rows)
    groups: dict[str, list[tuple[dict[str, str], str]]] = defaultdict(list)
    for row in rows:
        account_key, method = resolve_account(row, verified_ids, current_ids)
        groups[account_key].append((row, method))

    accounts: list[dict[str, object]] = []
    for account_key, entries in sorted(groups.items()):
        events = [row for row, _ in entries]
        methods = {method for _, method in entries}
        status = (
            "NumericGitHubId" if account_key.startswith("GH:") else
            "AmbiguousLogin" if "AmbiguousLogin" in methods else "LoginOnlyUnmatched"
        )
        logins = {row["ActorLogin"].strip() for row in events if row["ActorLogin"].strip()}
        accounts.append(
            {
                "AccountKey": account_key,
                "GitHubUserId": account_key[3:] if account_key.startswith("GH:") else "",
                "ObservedLogins": ";".join(sorted(logins, key=str.casefold)),
                "AccountMatchStatus": status,
                "AccountMatchMethods": ";".join(sorted(methods)),
                "OriginalAccountKeys": ";".join(sorted({row["AccountKey"] for row in events})),
                "EventCount": len(events),
                "LoginOnlyEventCount": sum(not row["ActorId"].strip() for row in events),
                "FirstActivityUtc": min(row["TimestampUtc"] for row in events),
                "LastActivityUtc": max(row["TimestampUtc"] for row in events),
                "ActivityTypes": ";".join(sorted({row["ActivityType"] for row in events})),
            }
        )
    return accounts


# Save the single account-level output and report its basic reconciliation.
def main() -> None:
    rows = read_csv(
        ACTIVITY,
        {
            "EventId", "ActivityType", "TimestampUtc", "AccountKey", "ActorLogin",
            "ActorId", "AccountKeyBasis",
        },
    )
    accounts = build_accounts(rows)
    if sum(account["EventCount"] for account in accounts) != len(rows):
        raise RuntimeError("Account event counts do not reconcile with the source")
    with OUTPUT.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=FIELDS)
        writer.writeheader()
        writer.writerows(accounts)
    print(f"Wrote {len(accounts)} active account records from {len(rows)} events to {OUTPUT}")


if __name__ == "__main__":
    main()
