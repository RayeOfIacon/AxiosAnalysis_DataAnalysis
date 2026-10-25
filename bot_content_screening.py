"""Screen current GitHub texts for bot-related statements without classifying accounts."""

import csv
import re
from collections import Counter
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SOURCE_DIR = ROOT / "Data" / "Extracted_data"
OUTPUT = Path(__file__).resolve().parent / "bot_content_candidates.csv"
SOURCES = (
    ("issues_current.csv", "IssueNumber", "AuthorLogin", "AuthorId", ("Title", "Body")),
    ("issue_comments_current.csv", "IssueNumber", "ActorLogin", "ActorId", ("Body",)),
    ("pull_requests_current.csv", "PrNumber", "AuthorLogin", "AuthorId", ("Title", "Body")),
    ("pr_comments_current.csv", "PrNumber", "ActorLogin", "ActorId", ("Body",)),
    ("pr_reviews_current.csv", "PrNumber", "ReviewerLogin", "ReviewerId", ("Body",)),
)
FIELDS = (
    "SourceDataset", "ArtifactNumber", "ActorLogin", "ActorId", "Url",
    "MatchedField", "SignalCategory", "MatchedPhrase", "TextExcerpt",
    "OriginalText", "SignalContext", "ConfidenceLevel", "ReviewStatus", "ReviewPriority",
)
SHORT_NAME = r"[A-Za-z][A-Za-z0-9_-]{0,31}(?:\s+[A-Za-z][A-Za-z0-9_-]{0,31}){0,2}"
AUTOMATION_IDENTITY = (
    r"(?:bot|AI\s+(?:agent|assistant|code\s+reviewer|language\s+model)|"
    r"autonomous\s+(?:AI\s+)?agent|automated\s+"
    r"(?:assistant|code\s+reviewer|reviewer|agent|system|tool))"
)
HUMAN_SUPERVISION = re.compile(
    r"\b(?:driven|guided|supervised|reviewed|approved|initiated|operated)\s+by\s+"
    r"(?:an?\s+)?(?:live\s+)?human\b|\bhuman[- ](?:supervised|guided|reviewed)\b|"
    r"\bwith\s+human\s+(?:oversight|supervision)\b", re.I,
)


# Match explicit self/account statements and contextual automation attributions.
PATTERNS = (
    ("SelfIdentification", re.compile(
        rf"(?<!\w)(?:I\s+am|I['’]m)\s+(?:{SHORT_NAME}\s*[,—–-]\s*)?"
        rf"(?:an?\s+)?{AUTOMATION_IDENTITY}\b", re.I)),
    ("SelfIdentification", re.compile(
        rf"(?<!\w)my\s+name\s+is\s+{SHORT_NAME}\s*,?\s+and\s+"
        rf"(?:I\s+am|I['’]m)\s+(?:an?\s+)?{AUTOMATION_IDENTITY}\b", re.I)),
    ("SelfIdentification", re.compile(
        r"(?<!\w)(?:this|our)\s+(?:account|profile)\s+(?:is|was)\s+"
        rf"(?:an?\s+)?{AUTOMATION_IDENTITY}\b", re.I)),
    ("SelfIdentification", re.compile(
        r"(?<!\w)(?:this|our)\s+(?:account|profile)\s+(?:is|was)\s+"
        r"(?:operated|run|managed|maintained)\s+by\s+(?:an?\s+)?"
        rf"{AUTOMATION_IDENTITY}\b", re.I)),
    ("SelfIdentification", re.compile(
        r"(?<!\w)as\s+an?\s+AI\s+(?:language\s+model|assistant|agent)\b", re.I)),
    ("ThirdPartyAutomation", re.compile(
        r"(?<!\w)this\s+(?:code\s+)?review\s+(?:was|is|has\s+been)\s+"
        r"(?:authored|written|generated|produced)\s+by\s+"
        r"(?:an?\s+)?[A-Za-z][\w.-]*(?:\s+[A-Za-z][\w.-]*)?", re.I)),
    ("ThirdPartyAutomation", re.compile(
        r"(?<!\w)this\s+(?:is|was)\s+an?\s+auto[- ]?generated\s+"
        r"(?:description|review|summary)(?:\s+by\s+[A-Za-z][\w.-]*)?\b", re.I)),
    ("ThirdPartyAutomation", re.compile(
        r"(?<!\w)AI[- ]assisted\s+(?:contribution|description|review|content)\b", re.I)),
    ("ThirdPartyAutomation", re.compile(
        r"(?im)^\s{0,3}#{1,6}\s+[^\r\n]{0,40}\b(?:automated|AI)\s+(?:code\s+)?review\b", re.I)),
    ("AutomationAttribution", re.compile(
        r"(?<!\w)this\s+(?:is|was)\s+an?\s+"
        r"(?:automated|auto[- ]?generated)\s+"
        r"(?:message|comment|review|description|response|notification|summary)\b", re.I)),
    ("AutomationAttribution", re.compile(
        r"(?<!\w)this\s+(?:PR|pull\s+request|issue|comment)\s+(?:was|is|has\s+been)\s+"
        r"(?:auto[- ]?generated|(?:created|posted|filed|submitted)\s+automatically)"
        r"(?:\s+by\s+[A-Za-z][\w.-]*(?:\s+[A-Za-z][\w.-]*)?)?\b", re.I)),
    ("AutomationAttribution", re.compile(
        r"(?<!\w)this\s+(?:PR|pull\s+request|issue|review|comment|message|"
        r"description|response|content)\s+(?:was|is|has\s+been)\s+"
        r"(?:created|posted|written|authored|generated|produced)\s+automatically\b", re.I)),
    ("AutomationAttribution", re.compile(
        r"(?<!\w)this\s+(?:PR|pull\s+request|issue|review|comment|message|"
        r"description|response|content|code\s+review)\s+(?:was|is|has\s+been)\s+"
        r"(?:generated|authored|written|produced)\s+by\s+"
        r"(?:an?\s+)?(?:AI\b|bot\b|automated\s+(?:tool|system|agent)\b|"
        r"[A-Za-z][\w.-]*(?:\s+[A-Za-z][\w.-]*)?)", re.I)),
    ("AutomationAttribution", re.compile(
        r"(?<!\w)(?:automatically\s+generated|auto[- ]?generated|generated)\s+by\s+"
        r"(?!the\b|a\b|an\b|this\b|that\b|our\b|your\b|their\b)"
        r"[A-Za-z][\w.-]*(?:\s+[A-Za-z][\w.-]*)?", re.I)),
    ("AutomationAttribution", re.compile(
        r"(?<!\w)do\s+not\s+reply\s+to\s+this\s+automated\s+message\b", re.I)),
    ("AutomationAttribution", re.compile(
        r"(?<!\w)filed\s+by\s+Claude\s+Code\b", re.I)),
)


# Locate markup and signature spans that should not be treated as direct self-statements.
def quoted_spans(text: str) -> list[tuple[int, int]]:
    spans = [(m.start(), m.end()) for m in re.finditer(r"<!--.*?(?:-->|\Z)", text, re.S)]
    spans.extend((m.start(), m.end()) for m in re.finditer(r"`[^`\r\n]+`|\"[^\"\r\n]+\"|“[^”\r\n]+”", text))
    offset = 0
    fence = ""
    for line in text.splitlines(keepends=True):
        marker = re.match(r"\s{0,3}(`{3,}|~{3,})", line)
        if marker:
            spans.append((offset, offset + len(line)))
            character = marker.group(1)[0]
            fence = "" if fence == character else character
        elif fence or re.match(r"\s{0,3}>", line):
            spans.append((offset, offset + len(line)))
        elif re.match(r"\s*(?:[-*]\s*)?(?:generated|authored)\s+by\s+\S+", line, re.I):
            spans.append((offset, offset + len(line)))
        offset += len(line)
    return spans


# Keep separate non-overlapping phrase occurrences and retain their source positions.
def find_signals(text: str) -> list[tuple[str, re.Match[str]]]:
    candidates = [(category, match) for category, pattern in PATTERNS for match in pattern.finditer(text)]
    candidates.sort(key=lambda item: (item[1].start(), -(item[1].end() - item[1].start())))
    accepted: list[tuple[str, re.Match[str]]] = []
    for category, match in candidates:
        if not any(match.start() < prior.end() and prior.start() < match.end() for _, prior in accepted):
            accepted.append((category, match))
    return accepted


# Scan each original text field and retain complete multiline source text.
def scan_field(text: str, field: str, source: dict[str, str]) -> list[dict[str, str]]:
    spans = quoted_spans(text)
    results = []
    for category, match in find_signals(text):
        quoted = any(match.start() < end and start < match.end() for start, end in spans)
        nearby = text[max(0, match.start() - 30):min(len(text), match.end() + 150)]
        supervised = bool(HUMAN_SUPERVISION.search(nearby))
        if quoted or category == "ThirdPartyAutomation":
            context, priority = "QuotedOrThirdParty", "Low"
        elif category == "SelfIdentification":
            context, priority = ("Uncertain" if supervised else "AuthorSelfStatement"), "High"
        elif supervised:
            context, priority = "Uncertain", "Low"
        else:
            context, priority = "ContentAttribution", "Medium"
        results.append({
            **source,
            "MatchedField": field,
            "SignalCategory": category,
            "MatchedPhrase": match.group(),
            "TextExcerpt": text[max(0, match.start() - 150):min(len(text), match.end() + 150)],
            "OriginalText": text,
            "SignalContext": context,
            "ConfidenceLevel": "High" if context == "AuthorSelfStatement" else "ReviewRequired",
            "ReviewStatus": "Pending",
            "ReviewPriority": priority,
        })
    return results


# Read validated local CSVs and print a compact screening summary.
def main() -> None:
    counts = {}
    candidates = []
    for filename, number_field, login_field, id_field, text_fields in SOURCES:
        with (SOURCE_DIR / filename).open("r", encoding="utf-8-sig", newline="") as handle:
            reader = csv.DictReader(handle)
            required = {number_field, login_field, id_field, "Url", *text_fields}
            missing = required - set(reader.fieldnames or [])
            if missing:
                raise ValueError(f"{filename} is missing columns: {sorted(missing)}")
            counts[filename] = 0
            for row in reader:
                counts[filename] += 1
                source = {
                    "SourceDataset": filename,
                    "ArtifactNumber": row[number_field],
                    "ActorLogin": row[login_field],
                    "ActorId": row[id_field],
                    "Url": row["Url"],
                }
                for field in text_fields:
                    if row[field]:
                        candidates.extend(scan_field(row[field], field, source))

    unique_rows = {}
    for row in candidates:
        unique_rows.setdefault(tuple(row[field] for field in FIELDS), row)
    candidates = list(unique_rows.values())

    with OUTPUT.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=FIELDS)
        writer.writeheader()
        writer.writerows(candidates)

    category_counts = Counter((row["SignalCategory"], row["ReviewPriority"]) for row in candidates)
    actor_sets = {
        priority: {(row["ActorId"], row["ActorLogin"]) for row in candidates if row["ReviewPriority"] == priority}
        for priority in ("High", "Medium", "Low")
    }
    issue_detected = any(
        row["SourceDataset"] == "issues_current.csv"
        and row["ArtifactNumber"] == "10575"
        and row["ActorLogin"] == "SuperNovaRobot"
        and row["SignalCategory"] == "SelfIdentification"
        for row in candidates
    )
    print("Records scanned:")
    for filename, count in counts.items():
        print(f"  {filename}: {count}")
    print(f"Matches: {len(candidates)}")
    print("Matches by category and priority:")
    for (category, priority), count in sorted(category_counts.items()):
        print(f"  {category} / {priority}: {count}")
    print("Distinct candidate actors by priority (observed ID/login pairs):")
    for priority, actors in actor_sets.items():
        print(f"  {priority}: {len(actors)}")
    print("High- and Medium-priority candidates:")
    for row in candidates:
        if row["ReviewPriority"] in {"High", "Medium"}:
            print(f"  {row['ReviewPriority']} | {row['ActorLogin']} | {row['MatchedPhrase']!r} | {row['Url']}")
    print(f"Axios Issue #10575 SelfIdentification detected: {'Yes' if issue_detected else 'No'}")
    print(f"Low-priority third-party matches: {category_counts['ThirdPartyAutomation', 'Low']}")
    print(f"Output: {OUTPUT}")


if __name__ == "__main__":
    main()
