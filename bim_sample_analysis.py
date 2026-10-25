"""Summarize commit sample sizes by canonical Git author identity."""

import csv
from collections import defaultdict
from pathlib import Path
from statistics import median


ROOT = Path(__file__).resolve().parents[1]
INPUT = ROOT / "Data" / "Extracted_data" / "commit_identities_current.csv"
OUTPUT = Path(__file__).resolve().parent / "bim_sample_summary.csv"
FIELDS = [
    "AuthorNameCanonical",
    "AuthorEmailCanonical",
    "CommitCount",
    "UniqueSubjectCount",
    "RepeatedSubjectCount",
]
THRESHOLDS = (2, 5, 10, 20)


# Read the current commit table and validate grouping fields.
def read_commits() -> list[dict[str, str]]:
    with INPUT.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        required = {"Sha", "AuthorNameCanonical", "AuthorEmailCanonical", "Subject"}
        missing = required - set(reader.fieldnames or [])
        if missing:
            raise ValueError(f"Missing input columns: {sorted(missing)}")
        commits = list(reader)
    seen_shas = set()
    for commit in commits:
        sha = commit["Sha"].strip()
        if not sha or sha in seen_shas:
            raise ValueError(f"Missing or duplicate commit SHA: {sha}")
        if not commit["AuthorNameCanonical"] or not commit["AuthorEmailCanonical"]:
            raise ValueError(f"Missing canonical author identity for commit {sha}")
        seen_shas.add(sha)
    return commits


# Count commits and exact subject repeats for each canonical author pair.
def summarize_authors(commits: list[dict[str, str]]) -> list[dict[str, str | int]]:
    groups: dict[tuple[str, str], list[str]] = defaultdict(list)
    for commit in commits:
        key = (commit["AuthorNameCanonical"], commit["AuthorEmailCanonical"])
        groups[key].append(commit["Subject"])

    results = []
    for (name, email), subjects in groups.items():
        unique_count = len(set(subjects))
        results.append(
            {
                "AuthorNameCanonical": name,
                "AuthorEmailCanonical": email,
                "CommitCount": len(subjects),
                "UniqueSubjectCount": unique_count,
                "RepeatedSubjectCount": len(subjects) - unique_count,
            }
        )
    return sorted(
        results,
        key=lambda row: (-row["CommitCount"], row["AuthorNameCanonical"], row["AuthorEmailCanonical"]),
    )


# Save the author-level table and print sample-size thresholds.
def main() -> None:
    commits = read_commits()
    results = summarize_authors(commits)
    with OUTPUT.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=FIELDS)
        writer.writeheader()
        writer.writerows(results)

    counts = [row["CommitCount"] for row in results]
    print(f"Commits: {len(commits)}; unique canonical Git authors: {len(results)}")
    if counts:
        single_commit = sum(count == 1 for count in counts)
        print(f"Median commits per author: {median(counts):g}; maximum: {max(counts)}")
        print(f"One-commit authors: {single_commit} ({single_commit / len(counts):.1%})")
        for threshold in THRESHOLDS:
            print(f"Authors with at least {threshold} commits: {sum(count >= threshold for count in counts)}")
    print(f"Output: {OUTPUT}")


if __name__ == "__main__":
    main()
