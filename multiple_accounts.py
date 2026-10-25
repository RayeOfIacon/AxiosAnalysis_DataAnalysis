"""Screen distinct Axios GitHub accounts for possible shared ownership.

Fuzzy thresholds are estimated independently for each field from the observed
same-initial comparisons. The 99.95th percentile of that empirical background
is used as the score threshold; fuzzy hits must be reciprocal nearest neighbours.
Exact normalized values always match. All results require manual validation.
"""

import csv
import re
import unicodedata
from collections import Counter, defaultdict
from difflib import SequenceMatcher
from pathlib import Path


HERE = Path(__file__).resolve().parent
EXTRACTED = HERE.parent / "Data" / "Extracted_data"
OLD_MAPPING_FILES = sorted(HERE.parent.glob("Analysis_Q1_*/identity_mapping.csv"))
ACTIVITY_TYPES = {
    "Commit", "PR Opened", "Issue Opened", "PR Review",
    "Inline Review Comment", "Issue Comment", "PR Conversation Comment",
}
SCREENING_FIELDS = ("Login", "Name", "Email")
METHOD_FIELDS = ("ProfileMatching", "ActivityContentMatching", "CommitIdentityMatching")
NO_REPLY = re.compile(r"(?:noreply|no-reply|users\.noreply\.github\.com)", re.I)
GENERIC_LOCAL = re.compile(
    r"^(?:admin|administrator|bot|build|ci|contact|dev|developer|git|github|"
    r"help|hello|info|mail|me|notifications?|noreply|no-reply|office|"
    r"opensource|owner|service|support|team|test|user|webmaster)(?:[._+\-0-9].*)?$",
    re.I,
)
SELF_LINK = re.compile(
    r"\b(?:my (?:other|old|alternate|alt|second) (?:github )?account|"
    r"I (?:also )?use (?:the )?(?:account|username)|"
    r"I (?:am|was) (?:also )?(?:known as|formerly)|"
    r"this is my (?:new|old) (?:github )?account)\b"
    r"[^\n.!?]{0,120}?@([A-Za-z0-9][A-Za-z0-9-]{0,38})",
    re.I,
)
TEXT_SOURCES = (
    ("pull_requests_current.csv", "AuthorLogin", "AuthorId", "Url", ("Title", "Body")),
    ("issues_current.csv", "AuthorLogin", "AuthorId", "Url", ("Title", "Body")),
    ("pr_reviews_current.csv", "ReviewerLogin", "ReviewerId", "Url", ("Body",)),
    ("pr_comments_current.csv", "ActorLogin", "ActorId", "Url", ("Body",)),
    ("issue_comments_current.csv", "ActorLogin", "ActorId", "Url", ("Body",)),
)


# Read source tables without modifying them.
def rows(path):
    with path.open("r", encoding="utf-8-sig", newline="") as stream:
        return list(csv.DictReader(stream))


def write_rows(path, fieldnames, data):
    with path.open("w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(data)


# Normalize comparable values while retaining originals as evidence.
def text_key(value):
    value = unicodedata.normalize("NFKD", value.casefold())
    value = "".join(char for char in value if not unicodedata.combining(char))
    return " ".join(re.findall(r"[a-z0-9]+", value))


def login_key(value):
    return "".join(char for char in text_key(value) if char.isalnum())


def email_parts(value):
    value = value.strip().casefold()
    if value.count("@") != 1 or NO_REPLY.search(value):
        return None
    local, domain = value.split("@")
    if not local or not domain or "." not in domain or GENERIC_LOCAL.match(local):
        return None
    identifier = re.sub(r"[^a-z0-9]", "", local.split("+")[0])
    if len(identifier) < 4 or identifier.isdigit():
        return None
    return value, identifier


# Build the finalized Human population and canonical login lookup.
def load_accounts():
    current = rows(HERE / "Current_accounts.csv")
    final = rows(HERE / "account_metadata_final.csv")
    if len({row["AccountKey"] for row in current}) != len(current):
        raise ValueError("Duplicate AccountKey in Current_accounts.csv")
    final_by_key = {row["AccountKey"]: row for row in final}
    if len(final_by_key) != len(final):
        raise ValueError("Duplicate AccountKey in account_metadata_final.csv")
    human = {key: row for key, row in final_by_key.items()
             if row["MetadataClassification"] == "Human"}
    if len(human) != 937 or set(human) - {row["AccountKey"] for row in current}:
        raise ValueError("Expected 937 finalized Human accounts present in Current_accounts.csv")
    accounts = {}
    aliases = defaultdict(set)
    for row in current:
        key = row["AccountKey"]
        if key not in human:
            continue
        github_id = row["GitHubUserId"].strip()
        if not github_id.isdigit() or key != "GH:" + github_id:
            continue
        accounts[key] = {
            "id": github_id,
            "logins": {item.strip() for item in row["ObservedLogins"].split(";") if item.strip()},
            "names": set(), "emails": {}, "profile": "https://github.com/" + github_id,
            "websites": set(), "social": set(), "bios": set(),
            "event_urls": set(), "commit_names": set(), "commit_emails": set(),
        }
        for original in row["OriginalAccountKeys"].split(";"):
            if original.strip():
                aliases[original.strip()].add(key)
        aliases[key].add(key)
    return accounts, aliases, len(human) - len(accounts)


# Add profile values only through an exact numeric GitHub ID.
def add_profiles(accounts):
    conflicting = 0
    for row in rows(EXTRACTED / "actors.csv"):
        key = "GH:" + row["GitHubUserId"].strip()
        if key not in accounts or row["ProfileFetchStatus"] != "Found":
            continue
        account = accounts[key]
        login = row["Login"].strip()
        if login:
            account["logins"].add(login)
        if row["Name"].strip():
            account["names"].add(row["Name"].strip())
        if row["Blog"].strip():
            account["websites"].add(row["Blog"].strip().casefold().rstrip("/"))
        if row["TwitterUsername"].strip():
            account["social"].add(row["TwitterUsername"].strip().casefold().lstrip("@"))
        if row["Bio"].strip():
            account["bios"].add(row["Bio"].strip())
        email = email_parts(row["PublicEmail"])
        if email:
            account["emails"].setdefault(email[0], set()).add("actors.csv:PublicEmail")
        if row["ProfileUrl"].strip():
            account["profile"] = row["ProfileUrl"].strip()
    for account in accounts.values():
        if not account["logins"]:
            conflicting += 1
    return conflicting


# Resolve events through the existing canonical account mapping only.
def add_events(accounts, aliases):
    event_by_sha = defaultdict(set)
    observed_types = set()
    unresolved = 0
    for row in rows(HERE / "Current_activity.csv"):
        keys = aliases.get(row["AccountKey"], set())
        if len(keys) != 1:
            if row["AccountKey"].startswith("GH:") and row["AccountKey"] in accounts:
                keys = {row["AccountKey"]}
            else:
                if keys:
                    unresolved += 1
                continue
        key = next(iter(keys))
        if key not in accounts:
            continue
        observed_types.add(row["ActivityType"])
        if row["ArtifactUrl"]:
            accounts[key]["event_urls"].add(row["ArtifactUrl"])
        if row["ActivityType"] == "Commit" and row["EventId"].startswith("commit:"):
            event_by_sha[row["EventId"].split(":", 1)[1]].add(key)
    if observed_types != ACTIVITY_TYPES:
        raise ValueError("Human event coverage differs from the seven expected activity types")
    return event_by_sha, unresolved


# Use only verified Git-author mappings corroborated by a current commit event.
def add_commit_identities(accounts, event_by_sha):
    if len(OLD_MAPPING_FILES) != 1:
        raise ValueError("Expected one historical identity_mapping.csv source")
    verified = defaultdict(set)
    for row in rows(OLD_MAPPING_FILES[0]):
        if row["IdentityType"] != "GitAuthor" or row["MappingStatus"] != "Verified":
            continue
        key = row["CanonicalContributorId"].strip()
        if key not in accounts or row["VerificationStatus"] != "Verified":
            continue
        identity = (text_key(row["GitAuthorName"]), row["GitAuthorEmail"].strip().casefold())
        verified[identity].add(key)
    accepted = 0
    rejected = 0
    for row in rows(EXTRACTED / "commit_identities_current.csv"):
        sha = row["Sha"].strip()
        event_keys = event_by_sha.get(sha, set())
        identity = (text_key(row["AuthorNameRaw"]), row["AuthorEmailRaw"].strip().casefold())
        mapped = verified.get(identity, set())
        if len(mapped) != 1 or event_keys != mapped:
            rejected += 1
            continue
        key = next(iter(mapped))
        accepted += 1
        name = row["AuthorNameRaw"].strip()
        if name:
            accounts[key]["commit_names"].add(name)
        email = email_parts(row["AuthorEmailRaw"])
        if email:
            accounts[key]["emails"].setdefault(email[0], set()).add(
                "Verified identity_mapping.csv + current commit " + sha
            )
            accounts[key]["commit_emails"].add(email[0])
    return accepted, rejected


# Estimate field-specific fuzzy thresholds from the actual comparison pool.
def percentile(sorted_values, fraction):
    if not sorted_values:
        return 1.0
    position = (len(sorted_values) - 1) * fraction
    low = int(position)
    high = min(low + 1, len(sorted_values) - 1)
    return sorted_values[low] + (sorted_values[high] - sorted_values[low]) * (position - low)


def score(a, b):
    return SequenceMatcher(None, a, b, autojunk=False).ratio()


def candidates_for_field(accounts, field):
    values = defaultdict(set)
    display = defaultdict(set)
    email_domains = defaultdict(set)
    for key, account in accounts.items():
        if field == "Login":
            source = [(login_key(item), item) for item in account["logins"]]
        elif field == "Name":
            source = [(text_key(item), item) for item in account["names"]]
        else:
            source = [(email_parts(item)[1], item) for item in account["emails"]]
        for normalized, original in source:
            if not normalized:
                continue
            values[normalized].add(key)
            display[(key, normalized)].add(original)
            if field == "Email":
                email_domains[normalized].add(original.rsplit("@", 1)[-1].casefold())
    exact = defaultdict(list)
    for normalized, keys in values.items():
        ordered = sorted(keys)
        for left_index, left in enumerate(ordered):
            for right in ordered[left_index + 1:]:
                kind = "Exact"
                if field == "Email":
                    kind = ("ExactEmail" if display[(left, normalized)] & display[(right, normalized)]
                            else "ExactPersonalIdentifier")
                exact[(left, right)].append((1.0, normalized, normalized, kind))
    fuzzy_pool = []
    unique = sorted(values)
    for index, first in enumerate(unique):
        if field == "Name" and (len(first.split()) < 2 or len(first) < 8):
            continue
        if field != "Name" and len(first) < 5:
            continue
        for second in unique[index + 1:]:
            if first[0] != second[0]:
                continue
            if field == "Name" and (len(second.split()) < 2 or len(second) < 8):
                continue
            if field != "Name" and len(second) < 5:
                continue
            if abs(len(first) - len(second)) > max(3, min(len(first), len(second)) // 3):
                continue
            fuzzy_pool.append((score(first, second), first, second))
    scores = sorted(item[0] for item in fuzzy_pool)
    threshold = percentile(scores, .9995)
    best = defaultdict(float)
    for similarity, first, second in fuzzy_pool:
        if similarity >= threshold:
            best[first] = max(best[first], similarity)
            best[second] = max(best[second], similarity)
    fuzzy = defaultdict(list)
    for similarity, first, second in fuzzy_pool:
        if similarity < threshold or similarity < best[first] or similarity < best[second]:
            continue
        if field == "Email" and not (email_domains[first] & email_domains[second]):
            continue
        if field == "Email" and not (first[:4] == second[:4] or first[-4:] == second[-4:]):
            continue
        for left in values[first]:
            for right in values[second]:
                if left == right:
                    continue
                pair = tuple(sorted((left, right)))
                fuzzy[pair].append((similarity, first, second, "Similar"))
    combined = defaultdict(list)
    for pair, hits in exact.items():
        combined[pair].extend(hits)
    for pair, hits in fuzzy.items():
        combined[pair].extend(hits)
    return combined, display, threshold, len(scores)


# Retain the earlier profile, text, and commit-identity cross-checks.
def cross_validation(accounts):
    output = defaultdict(lambda: {method: [] for method in METHOD_FIELDS})
    profile_fields = ("names", "emails", "websites", "social", "bios")
    for field in profile_fields:
        inverted = defaultdict(set)
        for key, account in accounts.items():
            values = account[field]
            for value in values:
                normalized = text_key(value) if field in ("names", "bios") else value
                if field == "bios" and (len(normalized) < 40 or len(normalized.split()) < 6):
                    continue
                if normalized:
                    inverted[(field, normalized)].add(key)
        for (kind, normalized), keys in inverted.items():
            for left in sorted(keys):
                for right in sorted(keys):
                    if left < right:
                        output[(left, right)]["ProfileMatching"].append(kind + "=" + normalized)
    for key, account in accounts.items():
        for name in account["commit_names"]:
            normalized = text_key(name)
            if len(normalized.split()) < 2:
                continue
            for other_key, other in accounts.items():
                if key >= other_key or not account["commit_emails"] or not other["commit_emails"]:
                    continue
                if normalized in {text_key(item) for item in other["commit_names"]}:
                    if account["commit_emails"].isdisjoint(other["commit_emails"]):
                        output[(key, other_key)]["CommitIdentityMatching"].append("Full Git author name=" + name)
    login_index = defaultdict(set)
    for key, account in accounts.items():
        for login in account["logins"]:
            login_index[login.casefold()].add(key)
    for filename, actor_login, actor_id, url_field, fields in TEXT_SOURCES:
        for row in rows(EXTRACTED / filename):
            source = "GH:" + row[actor_id].strip()
            if source not in accounts:
                continue
            for field in fields:
                for match in SELF_LINK.finditer(row[field] or ""):
                    targets = login_index.get(match.group(1).casefold(), set())
                    if len(targets) != 1:
                        continue
                    target = next(iter(targets))
                    if target != source:
                        pair = tuple(sorted((source, target)))
                        output[pair]["ActivityContentMatching"].append(
                            filename + ":" + field + ":" + match.group(0) + " @ " + row[url_field]
                        )
    return output


# Serialize one manual-review row per distinct GitHub ID pair.
def build_output(accounts, screening, displays, cross):
    all_pairs = set(cross)
    for hits in screening.values():
        all_pairs.update(hits)
    output = []
    for left, right in sorted(all_pairs):
        if accounts[left]["id"] == accounts[right]["id"]:
            continue
        found = {field: screening[field].get((left, right), []) for field in SCREENING_FIELDS}
        extra = cross.get((left, right), {method: [] for method in METHOD_FIELDS})
        if not any(found.values()) and not any(extra.values()):
            continue
        row = {
            "AccountKey1": left, "GitHubUserId1": accounts[left]["id"],
            "Logins1": "; ".join(sorted(accounts[left]["logins"])),
            "AccountKey2": right, "GitHubUserId2": accounts[right]["id"],
            "Logins2": "; ".join(sorted(accounts[right]["logins"])),
            "MatchedIndicators": "; ".join(field for field in SCREENING_FIELDS if found[field]),
            "ProfileMatching": bool(extra["ProfileMatching"]),
            "ActivityContentMatching": bool(extra["ActivityContentMatching"]),
            "CommitIdentityMatching": bool(extra["CommitIdentityMatching"]),
            "ValidationStatus": "PendingManualValidation",
            "ProfileUrl1": accounts[left]["profile"],
            "ProfileUrl2": accounts[right]["profile"],
        }
        evidence = []
        for field in SCREENING_FIELDS:
            hits = found[field]
            if hits:
                chosen = max(hits, key=lambda item: item[0])
                similarity, first, second, match_type = chosen
                row[field + "Score"] = f"{similarity:.6f}"
                row[field + "MatchType"] = match_type
                row[field + "Value1"] = "; ".join(sorted(displays[field].get((left, first), set()) or displays[field].get((left, second), set())))
                row[field + "Value2"] = "; ".join(sorted(displays[field].get((right, second), set()) or displays[field].get((right, first), set())))
                evidence.append(field + "=" + row[field + "Value1"] + " <> " + row[field + "Value2"])
            else:
                for suffix in ("Score", "MatchType", "Value1", "Value2"):
                    row[field + suffix] = ""
        row["CrossValidationEvidence"] = " | ".join(
            method + ": " + " ; ".join(sorted(set(extra[method])))
            for method in METHOD_FIELDS if extra[method]
        )
        row["SupportingEvidence"] = " | ".join(evidence)
        source_urls = {accounts[left]["profile"], accounts[right]["profile"]}
        source_urls.update(part.rsplit(" @ ", 1)[-1]
                           for part in extra["ActivityContentMatching"] if " @ " in part)
        for key, email in ((left, row["EmailValue1"]), (right, row["EmailValue2"])):
            for selected in email.split("; ") if email else ():
                for source in accounts[key]["emails"].get(selected, ()):
                    if source.startswith("Verified identity_mapping.csv + current commit "):
                        source_urls.add("https://github.com/axios/axios/commit/" + source.rsplit(" ", 1)[-1])
        row["SourceUrls"] = "; ".join(sorted(source_urls))
        output.append(row)
    return output


# Summarize OR-screened pairs, cross-checks, overlaps, and coverage.
def summarize(output, accounts, excluded, thresholds, pool_sizes, commit_stats, unresolved):
    result = []
    def add(group, label, count, note=""):
        result.append({"Group": group, "Metric": label, "Count": count, "Details": note})
    add("Coverage", "Finalized Human accounts", 937)
    add("Coverage", "Human accounts with numeric GitHubUserId", len(accounts))
    add("Coverage", "Human accounts without numeric GitHubUserId", excluded,
        "Excluded from distinct-ID pair screening")
    add("Coverage", "Unresolved activity rows", unresolved)
    add("Coverage", "Verified current Git author identities", commit_stats[0])
    add("Coverage", "Commit identities excluded from account attribution", commit_stats[1])
    for field in SCREENING_FIELDS:
        add("Indicator", field, sum(bool(row[field + "Score"]) for row in output),
            f"SequenceMatcher; same-initial candidate pool={pool_sizes[field]}; "
            f"empirical 99.95th percentile={thresholds[field]:.6f}; exact or reciprocal nearest fuzzy; "
            + ("fuzzy email requires same domain and matching 4-character prefix or suffix" if field == "Email" else ""))
    for method in METHOD_FIELDS:
        add("CrossValidation", method, sum(row[method] for row in output))
    for first_index, first in enumerate(SCREENING_FIELDS):
        for second in SCREENING_FIELDS[first_index + 1:]:
            add("Overlap", first + "+" + second,
                sum(bool(row[first + "Score"] and row[second + "Score"]) for row in output))
    add("Overlap", "Login+Name+Email",
        sum(all(row[field + "Score"] for field in SCREENING_FIELDS) for row in output))
    for method in METHOD_FIELDS:
        add("Overlap", "AnyIndicator+" + method,
            sum(bool(row["MatchedIndicators"]) and row[method] for row in output))
    add("Total", "Unique candidate pairs", len(output))
    add("Total", "Pairs with at least one OR screening indicator",
        sum(bool(row["MatchedIndicators"]) for row in output))
    add("Total", "Cross-validation-only pairs",
        sum(not row["MatchedIndicators"] for row in output))
    return result


def main():
    accounts, aliases, excluded = load_accounts()
    missing_logins = add_profiles(accounts)
    event_by_sha, unresolved = add_events(accounts, aliases)
    commit_stats = add_commit_identities(accounts, event_by_sha)
    screening, displays, thresholds, pool_sizes = {}, {}, {}, {}
    for field in SCREENING_FIELDS:
        screening[field], displays[field], thresholds[field], pool_sizes[field] = candidates_for_field(accounts, field)
    cross = cross_validation(accounts)
    output = build_output(accounts, screening, displays, cross)
    summary = summarize(output, accounts, excluded, thresholds, pool_sizes, commit_stats, unresolved)
    candidate_fields = (
        "AccountKey1", "GitHubUserId1", "Logins1", "AccountKey2", "GitHubUserId2", "Logins2",
        "MatchedIndicators", "LoginScore", "LoginMatchType", "LoginValue1", "LoginValue2",
        "NameScore", "NameMatchType", "NameValue1", "NameValue2",
        "EmailScore", "EmailMatchType", "EmailValue1", "EmailValue2",
        "ProfileMatching", "ActivityContentMatching", "CommitIdentityMatching",
        "SupportingEvidence", "CrossValidationEvidence", "ProfileUrl1", "ProfileUrl2",
        "SourceUrls", "ValidationStatus",
    )
    write_rows(HERE / "multiple_accounts_candidates.csv", candidate_fields, output)
    write_rows(HERE / "multiple_accounts_summary.csv", ("Group", "Metric", "Count", "Details"), summary)
    print(f"Human accounts: 937; numeric-ID accounts: {len(accounts)}; excluded without ID: {excluded}")
    print(f"Unique candidate pairs: {len(output)}; missing login values: {missing_logins}")
    for field in SCREENING_FIELDS:
        count = sum(bool(row[field + "Score"]) for row in output)
        print(f"{field}: {count} pairs; empirical 99.95th-percentile threshold {thresholds[field]:.4f}")
    for method in METHOD_FIELDS:
        print(f"{method}: {sum(row[method] for row in output)} pairs")
    print(f"Verified commit rows: {commit_stats[0]}; excluded commit rows: {commit_stats[1]}")
    print(f"Unresolved activity rows: {unresolved}; all candidates need manual validation")


if __name__ == "__main__":
    main()
