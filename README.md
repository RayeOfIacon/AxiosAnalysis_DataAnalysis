# Axios Project Health — Q1 Analysis Code

This repository is a code appendix for an academic analysis of contributor activity in [`axios/axios`](https://github.com/axios/axios). The current observation period is 2025-09-01 to 2026-08-31 (UTC). Historical analyses use up to five preceding years of locally collected activity records.

The repository contains Python analysis scripts only. Input datasets, generated CSVs, profile records, and figures are not included. The scripts were written for a specific research snapshot, so reproducing the results requires the corresponding local datasets and finalized classification decisions. During the project, some CSV filenames were updated as processing evolved; adapt local input paths to the matching dataset version when rerunning the scripts.

## Scripts

| Script | Purpose |
| --- | --- |
| `current_activity.py` | Builds the current-period event table across seven activity types without Human/Bot filtering. |
| `Current_accounts.py` | Extracts current-period accounts and applies the existing canonical account mapping. |
| `account_metadata.py` | Joins available GitHub profile metadata to current accounts and records preliminary account-type evidence. |
| `bin_detection.py` | Screens current Git commit author names and emails for boundary-aware bot-name patterns. |
| `bim_sample_analysis.py` | Summarizes Git author commit counts and repeated commit subjects to assess the available sample. |
| `bot_content_screening.py` | Finds contextual bot or automation statements in issue, PR, review, and comment text for manual review. |
| `contributor_active_months.py` | Counts distinct active UTC calendar months for finalized Human contributors. |
| `contributor_key_classification.py` | Applies the project's existing activity, duration, and breadth rule for Key Contributor candidates. |
| `contributor_activity_total.py` | Extends an existing contributor activity-total table with grouped activity counts, shares, and the existing key-candidate flag. |
| `dormant_activity_6y.py` | Combines historical and current activity into a six-year event series and period-coverage summary. |
| `dormant_identification_6y.py` | Identifies six-month inactivity episodes and summarizes new dormancy by period. |
| `newcomer_identification.py` | Compares finalized current Human accounts with recorded five-year historical activity. |
| `newcomer_participation.py` | Compares activity volume, first recorded activity, and activity types across newcomer and returning groups. |
| `newcomer_engagement.py` | Compares repeat participation, cross-month activity, and overlap with existing Key Contributor candidates. |
| `multiple_accounts.py` | Screens distinct GitHub accounts for possible use by the same person; outputs candidates for manual validation. |
| `shared_accounts.py` | Screens account names and biographies for possible team-managed or multi-person account use; outputs candidates for manual validation. |
| `PH_Axios_Plots.py` | Creates figures from previously generated contributor and newcomer summaries. |

## Interpretation and reproducibility

These scripts separate observed activity from identity claims. A GitHub account is not necessarily a unique person; a bot-like signal is not automatically a final Bot classification. “Newcomer Candidate” means no reliable earlier activity was found in the available lookback data, not that a person's first-ever contribution was confirmed. Account-sharing outputs are screening leads and require manual validation; the scripts do not merge accounts.

Many scripts use the finalized Human/Bot results and intermediate CSVs created earlier in the local workflow. This code-only appendix does not contain those inputs or a turnkey end-to-end reproduction pipeline. Most scripts use only the Python standard library; the plotting script additionally requires `pandas` and `matplotlib`.

## Publication boundary

The `.gitignore` excludes CSVs, other data tables, generated figures, caches, and common credential files. Some local datasets contain GitHub IDs, profile fields, public emails, or complete issue/PR text. Review every selected file before publishing. Ignore rules do not remove files already committed and should not be relied on to filter a manual browser upload.

For a final report appendix, link to a specific GitHub commit or tag so that readers see the exact code version used in the report.
