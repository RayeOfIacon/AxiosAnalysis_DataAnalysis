"""Screen current Human accounts for possible multi-person account use.

Naming and profile-description signals are OR-combined. Organization-related
words are leads for manual review, not evidence that multiple people log in.
"""

import csv
import re
from collections import Counter
from pathlib import Path


HERE = Path(__file__).resolve().parent
ACTORS = HERE.parent / "Data" / "Extracted_data" / "actors.csv"
CURRENT = HERE / "Current_accounts.csv"
FINAL = HERE / "account_metadata_final.csv"
CANDIDATES = HERE / "shared_accounts_candidates.csv"
SUMMARY = HERE / "shared_accounts_summary.csv"

SHARED_NAME = re.compile(
    r"\b(?:shared|joint|multi[ -]?user|multiple[ -]?users|several[ -]?users)\b"
    r"(?:[\s._-]*(?:github[\s._-]*)?(?:account|profile|login))?",
    re.I,
)
GROUP_NAME = re.compile(
    r"\b(?:team|collective|community|foundation|organisation|organization|"
    r"company|corporation|corp|studio|studios|labs|group|project|"
    r"developers|devs|maintainers|staff)\b",
    re.I,
)
BIO_EXPLICIT = (
    re.compile(
        r"\b(?:this|our)\s+(?:github\s+)?(?:account|profile|login)\s+"
        r"(?:is\s+)?(?:shared|used|managed|maintained|operated|run)\s+by\s+"
        r"(?:multiple|several|different|two|three|many)\s+"
        r"(?:people|persons|individuals|users|developers|members)\b", re.I
    ),
    re.compile(
        r"\b(?:multiple|several|different|two|three|many)\s+"
        r"(?:people|persons|individuals|users|developers|members)\s+"
        r"(?:share|use|manage|maintain|operate|run)\s+"
        r"(?:this|our)\s+(?:github\s+)?(?:account|profile|login)\b", re.I
    ),
    re.compile(
        r"\bwe\s+share\s+(?:this|our)\s+(?:github\s+)?"
        r"(?:account|profile|login)\b", re.I
    ),
)
BIO_TEAM = (
    re.compile(
        r"\b(?:this|our)\s+(?:github\s+)?(?:account|profile|login)\s+"
        r"(?:is\s+)?(?:managed|maintained|operated|run|used)\s+by\s+"
        r"(?:the\s+|our\s+|a\s+)?(?:[\w-]+\s+){0,4}?"
        r"(?:team|group|collective|community|organisation|organization|company)\b", re.I
    ),
    re.compile(
        r"\b(?:team|group|collective|community|organisation|organization|company)\s+"
        r"(?:account|profile|login)\b", re.I
    ),
    re.compile(
        r"\b(?:official|shared)\s+(?:github\s+)?(?:account|profile)\s+"
        r"(?:of|for)\s+(?:the\s+|our\s+)?(?:[\w-]+\s+){0,3}?"
        r"(?:team|group|collective|community|organisation|organization|company)\b", re.I
    ),
)

CANDIDATE_FIELDS = (
    "AccountKey", "GitHubUserId", "ObservedLogins", "ProfileLogin", "Name",
    "AccountNamingIndicator", "ProfileDescriptionIndicator", "MatchedIndicators",
    "NamingEvidence", "BioEvidence", "EvidenceInterpretation", "Bio",
    "PublicEmail", "Company", "Blog", "AccountType", "ProfileFetchStatus",
    "ProfileUrl", "EvidenceSource", "ValidationStatus",
)
SUMMARY_FIELDS = ("Group", "Metric", "Count", "Notes")


# Read the three input tables and preserve their original text values.
def read_csv(path):
    with path.open("r", encoding="utf-8-sig", newline="") as stream:
        return list(csv.DictReader(stream))


def write_csv(path, fields, data):
    with path.open("w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=fields)
        writer.writeheader()
        writer.writerows(data)


# Validate the existing Human population without changing its identity mapping.
def load_population():
    current = read_csv(CURRENT)
    final = read_csv(FINAL)
    actors = read_csv(ACTORS)
    current_by_key = {row["AccountKey"]: row for row in current}
    final_by_key = {row["AccountKey"]: row for row in final}
    if len(current_by_key) != len(current) or len(final_by_key) != len(final):
        raise ValueError("Duplicate AccountKey in an account input")
    humans = {key: row for key, row in final_by_key.items()
              if row["MetadataClassification"] == "Human"}
    if len(humans) != 937 or not set(humans).issubset(current_by_key):
        raise ValueError("Expected 937 finalized Human accounts in Current_accounts.csv")
    actors_by_id = {}
    duplicate_ids = set()
    for row in actors:
        github_id = row["GitHubUserId"].strip()
        if not github_id:
            continue
        if github_id in actors_by_id:
            duplicate_ids.add(github_id)
        actors_by_id[github_id] = row
    if duplicate_ids:
        raise ValueError("Duplicate GitHubUserId in actors.csv: " + ", ".join(sorted(duplicate_ids)))
    return current_by_key, humans, actors_by_id


# Match organization-oriented words only as whole naming tokens.
def naming_matches(login, name):
    matches = []
    for field, value in (("Login", login), ("Name", name)):
        if not value:
            continue
        separated = re.sub(r"(?<=[a-z])(?=[A-Z])", " ", value)
        separated = re.sub(r"[_\-.]+", " ", separated)
        for pattern, signal in ((SHARED_NAME, "SharedAccountName"),
                                (GROUP_NAME, "OrganizationalName")):
            for match in pattern.finditer(separated):
                evidence = f"{field}={value}; Matched={match.group(0)}; Signal={signal}"
                if evidence not in matches:
                    matches.append(evidence)
    return matches


# Require an account-level Bio statement, not a person's team affiliation.
def bio_matches(bio):
    matches = []
    for pattern in BIO_EXPLICIT:
        for match in pattern.finditer(bio):
            matches.append(("ExplicitMultiPersonUse", match.group(0)))
    for pattern in BIO_TEAM:
        for match in pattern.finditer(bio):
            matches.append(("TeamManagedIndication", match.group(0)))
    return sorted(set(matches))


# Combine the two indicators and keep supporting fields separate from triggers.
def screen(current, humans, actors):
    output = []
    missing_id = 0
    missing_profile = 0
    for key in sorted(humans):
        current_row = current[key]
        final_row = humans[key]
        github_id = final_row["GitHubUserId"].strip()
        if not github_id.isdigit() or key != "GH:" + github_id:
            missing_id += 1
            actor = None
        else:
            actor = actors.get(github_id)
        if actor is None:
            missing_profile += 1
        login = actor["Login"].strip() if actor else ""
        name = actor["Name"] if actor else ""
        bio = actor["Bio"] if actor else ""
        observed = current_row["ObservedLogins"]
        name_evidence = []
        for value in (item.strip() for item in observed.split(";") if item.strip()):
            name_evidence.extend(naming_matches(value, ""))
        name_evidence.extend(naming_matches(login, name))
        name_evidence = list(dict.fromkeys(name_evidence))
        description_evidence = bio_matches(bio)
        if not name_evidence and not description_evidence:
            continue
        indicators = []
        if name_evidence:
            indicators.append("AccountNaming")
        if description_evidence:
            indicators.append("ProfileDescription")
        interpretations = {item[0] for item in description_evidence}
        if "ExplicitMultiPersonUse" in interpretations:
            interpretation = "ExplicitMultiPersonUse"
        elif "TeamManagedIndication" in interpretations:
            interpretation = "TeamManagedIndication"
        elif any("Signal=SharedAccountName" in item for item in name_evidence):
            interpretation = "SharedNameOnly"
        else:
            interpretation = "OrganizationalNameOnly"
        profile_url = actor["ProfileUrl"].strip() if actor else ""
        if not profile_url and login:
            profile_url = "https://github.com/" + login
        output.append({
            "AccountKey": key, "GitHubUserId": github_id,
            "ObservedLogins": observed, "ProfileLogin": login, "Name": name,
            "AccountNamingIndicator": bool(name_evidence),
            "ProfileDescriptionIndicator": bool(description_evidence),
            "MatchedIndicators": "; ".join(indicators),
            "NamingEvidence": " | ".join(name_evidence),
            "BioEvidence": " | ".join(kind + ": " + phrase for kind, phrase in description_evidence),
            "EvidenceInterpretation": interpretation,
            "Bio": bio,
            "PublicEmail": actor["PublicEmail"] if actor else "",
            "Company": actor["Company"] if actor else "",
            "Blog": actor["Blog"] if actor else "",
            "AccountType": actor["AccountType"] if actor else final_row["AccountType"],
            "ProfileFetchStatus": actor["ProfileFetchStatus"] if actor else final_row["ProfileFetchStatus"],
            "ProfileUrl": profile_url,
            "EvidenceSource": "Current_accounts.csv; account_metadata_final.csv"
                              + ("; actors.csv" if actor else ""),
            "ValidationStatus": "PendingManualValidation",
        })
    return output, missing_id, missing_profile


# Reconcile indicator counts and distinguish evidence strength in the summary.
def summarize(candidates, missing_id, missing_profile):
    count = Counter(row["EvidenceInterpretation"] for row in candidates)
    def entry(group, metric, number, notes=""):
        return {"Group": group, "Metric": metric, "Count": number, "Notes": notes}
    return [
        entry("Coverage", "Finalized Human accounts screened", 937),
        entry("Coverage", "Human accounts without numeric GitHubUserId", missing_id,
              "Login is screened; profile fields are not joined by Login alone"),
        entry("Coverage", "Human accounts without matched actors.csv profile", missing_profile),
        entry("Indicator", "AccountNaming", sum(row["AccountNamingIndicator"] for row in candidates)),
        entry("Indicator", "ProfileDescription", sum(row["ProfileDescriptionIndicator"] for row in candidates)),
        entry("Overlap", "AccountNaming+ProfileDescription",
              sum(row["AccountNamingIndicator"] and row["ProfileDescriptionIndicator"]
                  for row in candidates)),
        entry("Interpretation", "ExplicitMultiPersonUse", count["ExplicitMultiPersonUse"]),
        entry("Interpretation", "TeamManagedIndication", count["TeamManagedIndication"]),
        entry("Interpretation", "SharedNameOnly", count["SharedNameOnly"]),
        entry("Interpretation", "OrganizationalNameOnly", count["OrganizationalNameOnly"]),
        entry("Total", "Unique candidate accounts", len(candidates),
              "Candidates require manual validation; no account usage is confirmed"),
    ]


def main():
    current, humans, actors = load_population()
    candidates, missing_id, missing_profile = screen(current, humans, actors)
    summary = summarize(candidates, missing_id, missing_profile)
    if len(candidates) != len({row["AccountKey"] for row in candidates}):
        raise ValueError("Duplicate account in candidate output")
    if any(row["AccountKey"] not in humans for row in candidates):
        raise ValueError("Non-Human account in candidate output")
    write_csv(CANDIDATES, CANDIDATE_FIELDS, candidates)
    write_csv(SUMMARY, SUMMARY_FIELDS, summary)
    print(f"Human accounts screened: {len(humans)}")
    print(f"Account naming candidates: {sum(row['AccountNamingIndicator'] for row in candidates)}")
    print(f"Profile description candidates: {sum(row['ProfileDescriptionIndicator'] for row in candidates)}")
    print("Both indicators: " + str(sum(row["AccountNamingIndicator"] and row["ProfileDescriptionIndicator"]
                                         for row in candidates)))
    print(f"Total unique candidates: {len(candidates)}")
    print(f"Explicit multi-person claims: {sum(row['EvidenceInterpretation'] == 'ExplicitMultiPersonUse' for row in candidates)}")
    print(f"Human accounts without ID: {missing_id}; without matched profile: {missing_profile}")
    print("All candidates require manual validation; classifications remain unchanged")


if __name__ == "__main__":
    main()
