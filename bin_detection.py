"""Detect BIN name and email signals in current-period Git commit authors."""

import csv
import re
from collections import Counter
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
INPUT = ROOT / "Data" / "Extracted_data" / "commit_identities_current.csv"
OUTPUT = Path(__file__).resolve().parent / "bin_commit_results.csv"
FIELDS = [
    "Sha",
    "AuthorNameRaw",
    "AuthorEmailRaw",
    "BINFlag",
    "MatchedField",
    "MatchedPattern",
    "BINEvidence",
]
BOT_TOKEN = re.compile(r"(?<![A-Za-z])bot(?![A-Za-z])", re.IGNORECASE)


# Find bot tokens only when letters do not touch either side.
def find_matches(value: str) -> list[tuple[str, str]]:
    matches = []
    for match in BOT_TOKEN.finditer(value):
        before = value[match.start() - 1] if match.start() else ""
        after = value[match.end()] if match.end() < len(value) else ""
        if before == "[" and after == "]":
            pattern = "BracketedBot"
            token = value[match.start() - 1 : match.end() + 1]
        elif before == "-":
            pattern = "HyphenBot"
            token = value[match.start() - 1 : match.end()]
        elif before == "_":
            pattern = "UnderscoreBot"
            token = value[match.start() - 1 : match.end()]
        else:
            pattern = "StandaloneBot"
            token = match.group()
        matches.append((pattern, token))
    return matches


# Evaluate raw author fields without using account identities or classifications.
def detect_bin(name: str, email: str) -> dict[str, str]:
    fields = []
    patterns = []
    evidence = []
    email_local = email.rpartition("@")[0] if "@" in email else email
    for field, value, label in (
        ("AuthorNameRaw", name, "AuthorNameRaw"),
        ("AuthorEmailRaw", email_local, "AuthorEmailRaw local part"),
    ):
        matches = find_matches(value)
        if matches:
            fields.append(field)
        for pattern, token in matches:
            if pattern not in patterns:
                patterns.append(pattern)
            evidence.append(f"{label} contains '{token}'")
    return {
        "BINFlag": "Positive" if fields else "Negative",
        "MatchedField": "; ".join(fields),
        "MatchedPattern": "; ".join(patterns),
        "BINEvidence": "; ".join(evidence),
    }


# Write one result per input commit and summarize affected raw Git authors.
def main() -> None:
    with INPUT.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        required = {"Sha", "AuthorNameRaw", "AuthorEmailRaw"}
        missing = required - set(reader.fieldnames or [])
        if missing:
            raise ValueError(f"Missing input columns: {sorted(missing)}")
        commits = list(reader)

    results = []
    affected_authors: Counter[tuple[str, str]] = Counter()
    seen_shas = set()
    for commit in commits:
        sha = commit["Sha"].strip()
        if not sha or sha in seen_shas:
            raise ValueError(f"Missing or duplicate commit SHA: {sha}")
        seen_shas.add(sha)
        name = commit["AuthorNameRaw"]
        email = commit["AuthorEmailRaw"]
        detection = detect_bin(name, email)
        results.append({"Sha": sha, "AuthorNameRaw": name, "AuthorEmailRaw": email, **detection})
        if detection["BINFlag"] == "Positive":
            affected_authors[(name, email)] += 1

    with OUTPUT.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=FIELDS)
        writer.writeheader()
        writer.writerows(results)

    print(f"Commits: {len(results)}; BIN positive: {sum(affected_authors.values())}")
    print(f"Affected raw Git author identities: {len(affected_authors)}")
    for (name, email), count in affected_authors.most_common():
        print(f"  {name} <{email}>: {count}")
    print(f"Output: {OUTPUT}")


if __name__ == "__main__":
    main()
