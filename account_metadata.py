"""Attach account profile evidence without behavioral or manual validation."""

import csv
from collections import defaultdict
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
ACCOUNTS = Path(__file__).resolve().parent / "Current_accounts.csv"
ACTORS = ROOT / "Data" / "Extracted_data" / "actors.csv"
OUTPUT = Path(__file__).resolve().parent / "account_metadata.csv"
METADATA_FIELDS = [
    "AccountType",
    "IsBotByType",
    "IsBotByLogin",
    "ProfileFetchStatus",
    "MetadataClassification",
    "MetadataEvidence",
    "EvidenceSource",
    "MetadataStatus",
    "MetadataMatchStatus",
]


# Load CSV rows and validate the fields needed for metadata matching.
def read_csv(path: Path, required: set[str]) -> tuple[list[str], list[dict[str, str]]]:
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        fields = reader.fieldnames or []
        missing = required - set(fields)
        if missing:
            raise ValueError(f"{path.name} is missing columns: {sorted(missing)}")
        return fields, list(reader)


# Detect the login patterns used as preliminary bot signals.
def bot_pattern(login: str) -> str:
    name = login.strip().casefold()
    if name.endswith("[bot]"):
        return "[bot]"
    if name.endswith("-bot"):
        return "-bot"
    if name.endswith("_bot"):
        return "_bot"
    if name.startswith("bot-"):
        return "bot-"
    if name.startswith("bot_"):
        return "bot_"
    if name.endswith("bot"):
        return "bot$"
    return ""


# Index profile rows by stable ID and normalized login without merging identities.
def index_actors(
    rows: list[dict[str, str]],
) -> tuple[dict[str, list[dict[str, str]]], dict[str, list[dict[str, str]]]]:
    by_id: dict[str, list[dict[str, str]]] = defaultdict(list)
    by_login: dict[str, list[dict[str, str]]] = defaultdict(list)
    for row in rows:
        actor_id = row["GitHubUserId"].strip()
        login = row["Login"].strip().casefold()
        if actor_id:
            by_id[actor_id].append(row)
        if login:
            by_login[login].append(row)
    return by_id, by_login


# Prefer ID matches and accept login matches only when one profile is unambiguous.
def match_actors(
    account: dict[str, str],
    by_id: dict[str, list[dict[str, str]]],
    by_login: dict[str, list[dict[str, str]]],
) -> tuple[list[dict[str, str]], str]:
    actor_id = account["GitHubUserId"].strip()
    logins = [name.strip() for name in account["ObservedLogins"].split(";") if name.strip()]
    candidates: list[dict[str, str]] = []
    seen: set[int] = set()
    for login in logins:
        for row in by_login.get(login.casefold(), []):
            marker = id(row)
            if marker not in seen:
                candidates.append(row)
                seen.add(marker)

    if account["AccountMatchStatus"] == "AmbiguousLogin":
        return candidates, "Ambiguous"

    if actor_id and by_id.get(actor_id):
        matches = by_id[actor_id]
        return matches, "IdMatch" if len(matches) == 1 else "Ambiguous"

    candidate_ids = {row["GitHubUserId"].strip() for row in candidates if row["GitHubUserId"].strip()}
    if len(candidate_ids) > 1 or (actor_id and candidate_ids and candidate_ids != {actor_id}):
        return candidates, "Ambiguous"
    if len(candidates) == 1 and candidates[0]["ProfileFetchStatus"].strip() == "Found":
        return candidates, "LoginMatch"
    return candidates, "NotFound"


# Preserve observed profile values and produce concise classification evidence.
def describe_account(
    account: dict[str, str], matches: list[dict[str, str]], match_status: str
) -> dict[str, str]:
    logins = [name.strip() for name in account["ObservedLogins"].split(";") if name.strip()]
    types = sorted({row["AccountType"].strip() for row in matches if row["AccountType"].strip()})
    type_flags = sorted({row["IsBotByType"].strip().lower() for row in matches if row["IsBotByType"].strip()})
    fetch_states = sorted({row["ProfileFetchStatus"].strip() for row in matches if row["ProfileFetchStatus"].strip()})
    actor_ids = sorted({row["GitHubUserId"].strip() for row in matches if row["GitHubUserId"].strip()})

    pattern_hits = [(login, bot_pattern(login)) for login in logins]
    pattern_hits += [(row["Login"].strip(), bot_pattern(row["Login"])) for row in matches]
    pattern_hits = [(login, pattern) for login, pattern in pattern_hits if pattern]
    actor_login_flag = any(row["IsBotByLogin"].strip().lower() == "true" for row in matches)
    bot_by_login = bool(pattern_hits or actor_login_flag)
    bot_by_type = "true" in type_flags or "Bot" in types

    evidence: list[str] = []
    sources: set[str] = set()
    if matches:
        sources.add("actors.csv")
    if types:
        evidence.append(f"AccountType={','.join(types)}")
    else:
        evidence.append("AccountType=Missing")
    if pattern_hits:
        login, pattern = sorted(set(pattern_hits), key=lambda item: item[0].casefold())[0]
        evidence.append(f"Login={login}; Pattern={pattern}")
        sources.add("Current_accounts.csv" if login in logins else "actors.csv")
    elif actor_login_flag:
        evidence.append("IsBotByLogin=true")
        sources.add("actors.csv")
    elif types == ["User"]:
        evidence.append("No bot-name pattern detected")
        sources.add("Current_accounts.csv")

    if match_status == "Ambiguous":
        if logins:
            evidence.insert(0, f"Login={','.join(logins)}")
        if len(actor_ids) > 1:
            evidence.append(f"Conflicting GitHubUserIds={','.join(actor_ids)}")
        else:
            evidence.append("Identity match is ambiguous")
        classification, status = "Uncertain", "Preliminary"
        sources.add("Current_accounts.csv")
    elif match_status == "NotFound":
        evidence.append("No unique fetched profile match")
        classification, status = "Uncertain", "Preliminary"
        sources.add("Current_accounts.csv")
    elif len(types) > 1 or ("true" in type_flags and types == ["User"]) or ("false" in type_flags and types == ["Bot"]):
        evidence.append("Conflicting account type indicators")
        classification, status = "Uncertain", "Preliminary"
    elif types == ["Bot"]:
        classification, status = "Bot", "Verified"
    elif bot_by_login:
        classification, status = "Bot", "Preliminary"
    elif types == ["User"]:
        classification, status = "Human", "Preliminary"
    else:
        classification, status = "Uncertain", "Preliminary"

    return {
        "AccountType": ";".join(types),
        "IsBotByType": "True" if bot_by_type else "False" if matches else "",
        "IsBotByLogin": "True" if bot_by_login else "False",
        "ProfileFetchStatus": ";".join(fetch_states),
        "MetadataClassification": classification,
        "MetadataEvidence": "; ".join(evidence),
        "EvidenceSource": "; ".join(sorted(sources)),
        "MetadataStatus": status,
        "MetadataMatchStatus": match_status,
    }


# Enrich each current account once and write the requested CSV only.
def main() -> None:
    account_fields, accounts = read_csv(
        ACCOUNTS, {"AccountKey", "GitHubUserId", "ObservedLogins", "AccountMatchStatus"}
    )
    _, actors = read_csv(
        ACTORS,
        {"Login", "GitHubUserId", "AccountType", "IsBotByType", "IsBotByLogin", "ProfileFetchStatus"},
    )
    by_id, by_login = index_actors(actors)
    output_rows: list[dict[str, str]] = []
    seen_keys: set[str] = set()
    for account in accounts:
        account_key = account["AccountKey"]
        if not account_key or account_key in seen_keys:
            raise ValueError(f"Missing or duplicate account key: {account_key}")
        matches, match_status = match_actors(account, by_id, by_login)
        output_rows.append({**account, **describe_account(account, matches, match_status)})
        seen_keys.add(account_key)

    with OUTPUT.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=account_fields + METADATA_FIELDS)
        writer.writeheader()
        writer.writerows(output_rows)
    print(f"Wrote metadata for {len(output_rows)} current accounts to {OUTPUT}")


if __name__ == "__main__":
    main()
