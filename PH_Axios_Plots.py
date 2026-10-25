"""Plot Axios Q1 human contributor participation figures.

Usage:
    pip install pandas matplotlib
    python plot_contributor_active_months.py
    python plot_contributor_active_months.py --data-dir path/to/csvs --output-dir figures

Required CSVs in --data-dir:
    active_months_distribution.csv
    contributor_active_months.csv
    contributor_activity_total_new.csv (includes ActivityTotal and ActivityBreadth)

Outputs (PNG only):
    Figure_1_Active_Months_Distribution.png
    Figure_2_Monthly_Active_Contributors.png
    Figure_3_Recorded_Activities_per_Contributor.png
    Figure_4_Active_Calendar_Months_per_Contributor.png
    Figure_5_Distinct_Activity_Types_per_Contributor.png
    Figure_6_New_Dormancy_Rate.png
    Figure_7_Newcomer_Recorded_Activities.png
    Figure_8_Returning_Recorded_Activities.png
    Figure_9_Newcomer_First_Recorded_Activity_Type.png
    Figure_7_Newcomer_Recorded_Activities.png
    Figure_8_Returning_Recorded_Activities.png

Additional CSV for Figures 7–8:
    newcomer_participation_contributors.csv

For only newcomer figures (without the legacy CSVs):
    python plot_contributor_active_months.py --newcomer-only
"""
from __future__ import annotations

import argparse
from pathlib import Path

import matplotlib.pyplot as plt
import pandas as pd

MONTHS = pd.period_range("2025-09", "2026-08", freq="M")


def save_figure(fig, stem: Path) -> None:
    png_path = stem.with_suffix(".png")
    fig.savefig(png_path, dpi=300, bbox_inches="tight")
    plt.close(fig)
    print(f"Saved: {png_path}")


def distribution_figure(data: pd.DataFrame, outdir: Path) -> None:
    required = {"ActiveMonths", "ContributorCount"}
    if not required.issubset(data.columns):
        raise ValueError(f"Distribution CSV missing columns: {sorted(required - set(data.columns))}")
    data = data.copy()
    data["ActiveMonths"] = pd.to_numeric(data["ActiveMonths"], errors="raise")
    data["ContributorCount"] = pd.to_numeric(data["ContributorCount"], errors="raise")
    if data["ActiveMonths"].duplicated().any():
        raise ValueError("Repeated ActiveMonths categories found in distribution CSV")
    if not data["ActiveMonths"].between(1, 12).all():
        raise ValueError("ActiveMonths must be between 1 and 12")

    groups = [("1 month", 1, 1), ("2 months", 2, 2),
              ("3–5 months", 3, 5), ("6–12 months", 6, 12)]
    labels = [g[0] for g in groups]
    counts = [int(data.loc[data.ActiveMonths.between(lo, hi), "ContributorCount"].sum())
              for _, lo, hi in groups]
    total = sum(counts)
    fig, ax = plt.subplots(figsize=(9, 5.3))
    bars = ax.bar(labels, counts, width=0.64)
    ax.bar_label(bars, labels=[f"{x:,}\n({x / total:.2%})" for x in counts],
                 padding=5, fontsize=10)
    ax.set_ylim(0, max(counts) * 1.17)
    ax.set_ylabel("Number of human contributors")
    ax.set_xlabel("Distinct active calendar months")
    ax.grid(axis="y", alpha=0.18)
    ax.set_axisbelow(True)
    ax.spines[["top", "right"]].set_visible(False)
    fig.tight_layout()
    save_figure(fig, outdir / "Figure_1_Active_Months_Distribution")


def monthly_figure(accounts: pd.DataFrame, outdir: Path) -> None:
    required = {"AccountKey", "ActiveMonthList"}
    if not required.issubset(accounts.columns):
        raise ValueError(f"Contributor CSV missing columns: {sorted(required - set(accounts.columns))}")
    if accounts.AccountKey.isna().any() or accounts.AccountKey.duplicated().any():
        raise ValueError("AccountKey must be present and unique for each contributor")

    monthly_accounts = {str(month): set() for month in MONTHS}
    for account, value in zip(accounts.AccountKey, accounts.ActiveMonthList):
        if pd.isna(value) or not str(value).strip():
            raise ValueError(f"No ActiveMonthList for {account}")
        month_strings = [v.strip() for v in str(value).split(";") if v.strip()]
        if "ActiveMonths" in accounts.columns and len(set(month_strings)) != int(accounts.loc[accounts.AccountKey == account, "ActiveMonths"].iloc[0]):
            raise ValueError(f"ActiveMonths mismatch for {account}")
        for month in set(month_strings):
            if month not in monthly_accounts:
                raise ValueError(f"Month {month!r} outside observation window for {account}")
            monthly_accounts[month].add(account)

    counts = [len(monthly_accounts[str(month)]) for month in MONTHS]
    labels = [month.strftime("%b '%y") for month in MONTHS]
    fig, ax = plt.subplots(figsize=(11, 5.4))
    ax.plot(range(12), counts, marker="o", markersize=7, linewidth=2.3)
    for x, count in enumerate(counts):
        ax.annotate(f"{count:,}", (x, count), xytext=(0, 9), textcoords="offset points",
                    ha="center", fontsize=10, fontweight="medium")
    ax.set_xticks(range(12), labels)
    ax.set_xlim(-0.5, 11.5)
    ax.set_ylim(0, max(counts) * 1.17)
    ax.set_xlabel("Month (UTC)")
    ax.set_ylabel("Distinct active human contributors")
    ax.grid(axis="y", alpha=0.18)
    ax.set_axisbelow(True)
    ax.spines[["top", "right"]].set_visible(False)
    fig.tight_layout()
    save_figure(fig, outdir / "Figure_2_Monthly_Active_Contributors")
    print("Monthly counts:", ", ".join(f"{month}: {n}" for month, n in zip(MONTHS, counts)))


def load_general_metrics(accounts: pd.DataFrame, activity: pd.DataFrame) -> pd.DataFrame:
    needed_months = {"AccountKey", "ActiveMonths"}
    needed_activity = {"AccountKey", "ActivityTotal", "ActivityBreadth"}
    for name, frame, columns in (("contributor_active_months.csv", accounts, needed_months),
                                 ("contributor_activity_total_new.csv", activity, needed_activity)):
        missing = columns - set(frame.columns)
        if missing:
            raise ValueError(f"{name} missing columns: {sorted(missing)}")
        if frame.AccountKey.isna().any() or frame.AccountKey.duplicated().any():
            raise ValueError(f"{name}: missing or repeated AccountKey")

    merged = accounts[["AccountKey", "ActiveMonths"]].merge(
        activity[["AccountKey", "ActivityTotal", "ActivityBreadth"]],
        on="AccountKey", how="outer", validate="one_to_one", indicator=True)
    if not merged["_merge"].eq("both").all():
        raise ValueError("Contributor sets differ between active-month and activity CSVs")
    for field in ("ActivityTotal", "ActiveMonths", "ActivityBreadth"):
        merged[field] = pd.to_numeric(merged[field], errors="raise")
    if not merged.ActiveMonths.between(1, 12).all():
        raise ValueError("ActiveMonths outside 1–12")
    if not merged.ActivityBreadth.between(1, 7).all():
        raise ValueError("ActivityBreadth outside 1–7")
    if not (merged.ActivityTotal >= 1).all():
        raise ValueError("ActivityTotal must be positive for current human contributors")
    return merged.drop(columns="_merge")


def plot_distribution(series: pd.Series, categories: list[tuple[str, callable]],
                      xlabel: str, ylabel: str, outstem: Path) -> None:
    total = len(series)
    labels = []
    counts = []
    for label, predicate in categories:
        labels.append(label)
        counts.append(int(predicate(series).sum()))
    if sum(counts) != total:
        raise ValueError(f"Categories do not cover all values for {xlabel}")

    fig, ax = plt.subplots(figsize=(10, 5.5))
    bars = ax.bar(labels, counts, width=0.70)
    ax.bar_label(bars, labels=[f"{n:,}\n({n / total:.1%})" for n in counts],
                 fontsize=9, padding=4)
    ax.set_ylabel(ylabel)
    ax.set_xlabel(xlabel)
    ax.set_ylim(0, max(counts) * 1.18)
    ax.grid(axis="y", alpha=0.18)
    ax.set_axisbelow(True)
    ax.spines[["top", "right"]].set_visible(False)
    fig.tight_layout()
    save_figure(fig, outstem)


def recorded_activities_figure(merged: pd.DataFrame, outdir: Path) -> None:
    categories = [(str(i), lambda s, i=i: s.eq(i)) for i in range(1, 11)]
    categories.append((">10", lambda s: s.gt(10)))
    plot_distribution(
        merged["ActivityTotal"],
        categories,
        xlabel="Recorded activities per contributor",
        ylabel="Number of human contributors",
        outstem=outdir / "Figure_3_Recorded_Activities_per_Contributor",
    )
    counts_text = ", ".join(
        [f"{i}={(merged['ActivityTotal'] == i).sum()}" for i in range(1, 11)] +
        [f">10={(merged['ActivityTotal'] > 10).sum()}"]
    )
    print("Recorded activities counts:", counts_text)


def active_calendar_months_figure(merged: pd.DataFrame, outdir: Path) -> None:
    categories = [(str(i), lambda s, i=i: s.eq(i)) for i in range(1, 13)]
    plot_distribution(
        merged["ActiveMonths"],
        categories,
        xlabel="Active calendar months per contributor",
        ylabel="Number of human contributors",
        outstem=outdir / "Figure_4_Active_Calendar_Months_per_Contributor",
    )
    counts_text = ", ".join([f"{i}={(merged['ActiveMonths'] == i).sum()}" for i in range(1, 13)])
    print("Active months counts:", counts_text)


def distinct_activity_types_figure(merged: pd.DataFrame, outdir: Path) -> None:
    categories = [(str(i), lambda s, i=i: s.eq(i)) for i in range(1, 8)]
    plot_distribution(
        merged["ActivityBreadth"],
        categories,
        xlabel="Distinct activity types per contributor",
        ylabel="Number of human contributors",
        outstem=outdir / "Figure_5_Distinct_Activity_Types_per_Contributor",
    )
    counts_text = ", ".join([f"{i}={(merged['ActivityBreadth'] == i).sum()}" for i in range(1, 8)])
    print("Activity breadth counts:", counts_text)


def dormant_rate_figure(data: pd.DataFrame, outdir: Path) -> None:
    required = {"Period", "AtRiskContributors", "NewDormantContributors", "NewDormancyRate"}
    missing = required - set(data.columns)
    if missing:
        raise ValueError(f"Dormancy summary is missing columns: {sorted(missing)}")

    expected = [f"P{i}" for i in range(1, 11)]
    if len(data) != 10 or set(data["Period"]) != set(expected) or data["Period"].duplicated().any():
        raise ValueError("Expected exactly one record for each of P1–P10")
    data = data.set_index("Period").loc[expected].reset_index()
    for col in ("AtRiskContributors", "NewDormantContributors", "NewDormancyRate"):
        data[col] = pd.to_numeric(data[col], errors="raise")
    if (data["AtRiskContributors"] <= 0).any():
        raise ValueError("All baseline contributor counts must be positive")
    calculated = data["NewDormantContributors"] / data["AtRiskContributors"]
    if not ((data["NewDormancyRate"] - calculated).abs() < 1e-5).all():
        raise ValueError("NewDormancyRate does not match contributor counts")
    if not data["NewDormancyRate"].between(0, 1).all():
        raise ValueError("NewDormancyRate must be a fraction between 0 and 1")

    plt.rcParams.update({"font.family": "DejaVu Sans", "font.size": 11})
    percentages = data["NewDormancyRate"] * 100
    x = range(10)
    fig, ax = plt.subplots(figsize=(11, 5.4))
    ax.plot(x, percentages, marker="o", markersize=7, linewidth=2.3)
    for i, percent in enumerate(percentages):
        ax.annotate(f"{percent:.2f}%", (i, percent),
                    xytext=(0, 10), textcoords="offset points",
                    ha="center", fontsize=9, fontweight="medium")
    ax.set_xticks(list(x), expected)
    ax.set_xlim(-0.5, 9.5)
    ax.set_ylim(85, 101)
    ax.set_yticks([85, 90, 95, 100])
    ax.set_xlabel("Six-month observation period")
    ax.set_ylabel("New Dormancy Rate (%)")
    ax.grid(axis="y", alpha=0.18)
    ax.set_axisbelow(True)
    ax.spines[["top", "right"]].set_visible(False)
    fig.tight_layout()

    outdir.mkdir(parents=True, exist_ok=True)
    output = outdir / "Figure_6_New_Dormancy_Rate.png"
    fig.savefig(output, dpi=300, bbox_inches="tight")
    plt.close(fig)
    print("New Dormancy Rates:", ", ".join(f"{period}={pct:.2f}%" for period, pct in zip(expected, percentages)))
    print(f"Saved: {output}")



def newcomer_activity_figures(data: pd.DataFrame, outdir: Path) -> None:
    """Create two comparable activity histograms, with within-group labels."""
    required = {"AccountKey", "Classification", "ActivityCount"}
    if not required.issubset(data.columns):
        raise ValueError(f"Newcomer CSV missing columns: {sorted(required - set(data.columns))}")
    if data["AccountKey"].isna().any() or data["AccountKey"].duplicated().any():
        raise ValueError("Newcomer AccountKey must be present and unique")
    data = data.copy()
    data["ActivityCount"] = pd.to_numeric(data["ActivityCount"], errors="raise")
    if not data["ActivityCount"].ge(1).all():
        raise ValueError("ActivityCount must be >= 1")
    if len(data) != 937:
        raise ValueError(f"Expected 937 human contributors; found {len(data)}")

    settings = [
        ("Newcomer Candidate", 857, "Newcomer Candidates", "Figure_7_Newcomer_Recorded_Activities"),
        ("Returning Contributor", 67, "Returning Contributors", "Figure_8_Returning_Recorded_Activities"),
    ]
    plt.rcParams.update({"font.family": "DejaVu Sans", "font.size": 11})
    for classification, expected_n, title, name in settings:
        series = data.loc[data["Classification"].eq(classification), "ActivityCount"]
        if len(series) != expected_n:
            raise ValueError(f"Expected {expected_n} {classification}, found {len(series)}")
        counts = [int(series.eq(i).sum()) for i in range(1, 11)]
        counts.append(int(series.gt(10).sum()))
        if sum(counts) != len(series):
            raise ValueError(f"Activity count bins do not sum to {expected_n}")
        labels = [str(i) for i in range(1, 11)] + [">10"]
        fig, ax = plt.subplots(figsize=(10, 5.5))
        bars = ax.bar(labels, counts, width=0.70)
        bar_labels = [f"{n:,}\n({n/expected_n:.1%})" if n else "" for n in counts]
        ax.bar_label(bars, labels=bar_labels, padding=4, fontsize=9)
        ax.set_ylim(0, max(counts) * 1.19)
        ax.set_ylabel("Number of contributors")
        ax.set_xlabel("Recorded activities per contributor")
        ax.set_title(f"{title} (n = {expected_n})", fontsize=12, pad=13)
        ax.grid(axis="y", alpha=0.18)
        ax.set_axisbelow(True)
        ax.spines[["top", "right"]].set_visible(False)
        fig.tight_layout()
        save_figure(fig, outdir / name)
        print(f"{classification} counts: " + ", ".join(f"{a}={b}" for a, b in zip(labels, counts)))



def newcomer_first_activity_figure(data: pd.DataFrame, outdir: Path) -> None:
    """Plot first recorded activity types for the 857 Newcomer Candidates."""
    required = {"AccountKey", "Classification", "FirstActivityType"}
    missing = required - set(data.columns)
    if missing:
        raise ValueError(f"Newcomer CSV missing columns: {sorted(missing)}")
    if data["AccountKey"].isna().any() or data["AccountKey"].duplicated().any():
        raise ValueError("AccountKey must be unique and non-null")
    subset = data.loc[data["Classification"].eq("Newcomer Candidate")]
    n = len(subset)
    if n != 857:
        raise ValueError(f"Expected 857 Newcomer Candidates; got {n}")
    types = ["Issue Comment", "PR Opened", "Issue Opened", "PR Conversation Comment",
             "PR Review", "Inline Review Comment", "Commit"]
    other = set(subset["FirstActivityType"].dropna().unique()) - set(types)
    if other or subset["FirstActivityType"].isna().any():
        raise ValueError(f"Unexpected/missing first activity types: {other}")
    counts = [int(subset["FirstActivityType"].eq(t).sum()) for t in types]
    if sum(counts) != n:
        raise ValueError("First activity counts do not add up")

    fig, ax = plt.subplots(figsize=(10.5, 5.7))
    positions = list(range(len(types)))
    bars = ax.barh(positions, counts, height=0.69)
    ax.set_yticks(positions, types)
    ax.invert_yaxis()
    ax.set_xlim(0, max(counts) * 1.23)
    for bar, count in zip(bars, counts):
        ax.text(count + max(counts)*0.018, bar.get_y()+bar.get_height()/2,
                f"{count:,} ({count/n:.2%})", va="center", ha="left", fontsize=10)
    ax.set_xlabel("Number of Newcomer Candidates")
    ax.set_title(f"First Recorded Activity Type among Newcomer Candidates (n = {n})",
                 fontsize=12, pad=13)
    ax.grid(axis="x", alpha=0.18)
    ax.set_axisbelow(True)
    ax.spines[["top", "right"]].set_visible(False)
    fig.tight_layout()
    save_figure(fig, outdir / "Figure_9_Newcomer_First_Recorded_Activity_Type")
    print("First recorded activity counts:", ", ".join(f"{t}={c}" for t,c in zip(types,counts)))

def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-dir", type=Path, default=Path(__file__).resolve().parent,
                        help="Folder containing the CSV files (default: script folder)")
    parser.add_argument("--output-dir", type=Path, default=None,
                        help="Figure output folder (default: <data-dir>/figures)")
    parser.add_argument("--newcomer-only", action="store_true",
                        help="Generate newcomer, returning, and first recorded activity figures only")
    args = parser.parse_args()
    output = args.output_dir or args.data_dir / "figures"
    output.mkdir(parents=True, exist_ok=True)

    newcomer_data = pd.read_csv(args.data_dir / "newcomer_participation_contributors.csv", encoding="utf-8-sig")
    newcomer_activity_figures(newcomer_data, output)
    newcomer_first_activity_figure(newcomer_data, output)
    if args.newcomer_only:
        return

    distribution = pd.read_csv(args.data_dir / "active_months_distribution.csv")
    accounts = pd.read_csv(args.data_dir / "contributor_active_months.csv")
    if int(distribution.ContributorCount.sum()) != len(accounts):
        raise ValueError("Contributor totals do not agree across the two CSVs")

    activity_path = args.data_dir / "contributor_activity_total_new.csv"
    if not activity_path.is_file():
        activity_path = args.data_dir / "contributor_activity_total_new(1).csv"
    if not activity_path.is_file():
        raise FileNotFoundError(
            "Missing contributor_activity_total_new.csv (or contributor_activity_total_new(1).csv)"
        )
    activity = pd.read_csv(activity_path, encoding="utf-8-sig")

    plt.rcParams.update({"font.family": "DejaVu Sans", "font.size": 11})

    distribution_figure(distribution, output)
    monthly_figure(accounts, output)
    merged = load_general_metrics(accounts, activity)
    recorded_activities_figure(merged, output)
    active_calendar_months_figure(merged, output)
    distinct_activity_types_figure(merged, output)

    dormancy_summary = pd.read_csv(
        args.data_dir / "dormant_identification_6y_summary.csv", encoding="utf-8-sig"
    )
    dormant_rate_figure(dormancy_summary, output)


if __name__ == "__main__":
    main()
