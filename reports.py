import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import os
import sqlite3
import pandas as pd
import json
import matplotlib.pyplot as plt
from collections import defaultdict
from patent_pipeline.scripts.config import DB_PATH, OUTPUT_DIR
from patent_pipeline.scripts.utils import ensure_dir


def chunked_aggregate_top(conn, query_chunk, group_col, agg_col, topn=10):
    """Compute top N values by counting distinct agg_col per group_col, using chunked reads."""
    counts = defaultdict(set)  # group -> set of patent_ids
    for chunk in pd.read_sql_query(query_chunk, conn, chunksize=100000):
        for _, row in chunk.iterrows():
            grp = row[group_col]
            pid = row[agg_col]
            counts[grp].add(pid)
    # Convert to counts
    result = pd.DataFrame([
        (grp, len(pids)) for grp, pids in counts.items()
    ], columns=[group_col, 'patent_count'])
    return result.nlargest(topn, 'patent_count')


def run_queries_and_export():
    ensure_dir(OUTPUT_DIR)
    conn = sqlite3.connect(DB_PATH)

    # Lightweight queries that don't need chunking
    total_patents = pd.read_sql_query("SELECT COUNT(*) AS total FROM patents", conn).iloc[0, 0]

    trends = pd.read_sql_query("""
        SELECT year, COUNT(*) AS patent_count
        FROM patents
        WHERE year IS NOT NULL
        GROUP BY year
        ORDER BY year
    """, conn)

    join_sample = pd.read_sql_query("""
        SELECT p.patent_id, p.title,
               i.name AS inventor_name,
               c.name AS company_name
        FROM patents p
        LEFT JOIN patent_inventor pi ON p.patent_id = pi.patent_id
        LEFT JOIN patent_assignee pa ON p.patent_id = pa.patent_id
        LEFT JOIN inventors i ON pi.rel_id = i.inventor_id
        LEFT JOIN companies c ON pa.company_id = c.company_id
        LIMIT 100
    """, conn)

    # Heavy aggregations – done safely in chunks
    print("Computing top inventors (chunked) …")
    top_inventors = chunked_aggregate_top(
        conn,
        "SELECT pi.rel_id AS inventor_id, pi.patent_id FROM patent_inventor pi",
        group_col='inventor_id',
        agg_col='patent_id',
        topn=10
    )
    # Map inventor_id → name
    inventors_df = pd.read_sql_query("SELECT inventor_id, name FROM inventors", conn)
    top_inventors = top_inventors.merge(inventors_df, on='inventor_id', how='left')
    top_inventors = top_inventors[['name', 'patent_count']].rename(columns={'name': 'inventor_name'})

    print("Computing top companies (chunked) …")
    top_companies = chunked_aggregate_top(
        conn,
        "SELECT pa.company_id, pa.patent_id FROM patent_assignee pa",
        group_col='company_id',
        agg_col='patent_id',
        topn=10
    )
    companies_df = pd.read_sql_query("SELECT company_id, name FROM companies", conn)
    top_companies = top_companies.merge(companies_df, on='company_id', how='left')
    top_companies = top_companies[['name', 'patent_count']].rename(columns={'name': 'company_name'})

    print("Computing country share (chunked) …")
    # Join inventor with patent_inventor to get country counts
    country_counts = defaultdict(set)
    for chunk in pd.read_sql_query("""
        SELECT i.country, pi.patent_id
        FROM patent_inventor pi
        JOIN inventors i ON pi.rel_id = i.inventor_id
        WHERE i.country != 'Unknown'
    """, conn, chunksize=100000):
        for _, row in chunk.iterrows():
            c = row['country']
            pid = row['patent_id']
            country_counts[c].add(pid)
    countries = pd.DataFrame([
        (c, len(pids)) for c, pids in country_counts.items()
    ], columns=['country', 'patent_count'])
    countries['share_percent'] = (countries['patent_count'] / total_patents * 100).round(2)
    countries = countries.nlargest(10, 'patent_count')

    # CTE – prolific inventors (chunked)
    print("Computing prolific inventors (chunked) …")
    inv_counts = defaultdict(set)  # inventor_id -> set of patent_ids
    for chunk in pd.read_sql_query("SELECT rel_id AS inventor_id, patent_id FROM patent_inventor WHERE rel_id != 'UNKNOWN'", conn, chunksize=100000):
        for _, row in chunk.iterrows():
            inv_counts[row['inventor_id']].add(row['patent_id'])
    prolific_ids = [inv_id for inv_id, pids in inv_counts.items() if len(pids) > 100]
    if prolific_ids:
        # Get countries for these inventors
        inv_country = pd.read_sql_query("SELECT inventor_id, country FROM inventors WHERE inventor_id IN ({})".format(','.join(['?']*len(prolific_ids))), conn, params=prolific_ids)
        inv_country['patent_count'] = inv_country['inventor_id'].map(lambda x: len(inv_counts[x]))
        cte_results = inv_country.groupby('country').agg(
            num_prolific=('inventor_id', 'count'),
            total_patents=('patent_count', 'sum')
        ).reset_index().sort_values('num_prolific', ascending=False)
    else:
        cte_results = pd.DataFrame(columns=['country', 'num_prolific', 'total_patents'])

    # Ranking – already covered by top_inventors, but we'll produce a similar output
    ranking = top_inventors.copy()
    ranking['rank'] = range(1, len(ranking)+1)

    conn.close()

    # Console output
    print("\n================== PATENT REPORT ==================")
    print(f"Total Patents: {total_patents:,}")
    print("\nTop Inventors:")
    for _, row in top_inventors.head(5).iterrows():
        print(f"  {row['inventor_name']} - {row['patent_count']}")
    print("\nTop Companies:")
    for _, row in top_companies.head(5).iterrows():
        print(f"  {row['company_name']} - {row['patent_count']}")
    print("\nTop Countries (by inventor nationality):")
    for _, row in countries.head(5).iterrows():
        print(f"  {row['country']} - {row['share_percent']}% share")
    print("===================================================\n")

    # CSV exports
    top_inventors.to_csv(os.path.join(OUTPUT_DIR, "top_inventors.csv"), index=False)
    top_companies.to_csv(os.path.join(OUTPUT_DIR, "top_companies.csv"), index=False)
    trends.to_csv(os.path.join(OUTPUT_DIR, "country_trends.csv"), index=False)

    # JSON
    json_data = {
        "total_patents": int(total_patents),
        "top_inventors": [
            {"name": r['inventor_name'], "patents": int(r['patent_count'])} for _, r in top_inventors.head(10).iterrows()
        ],
        "top_companies": [
            {"name": r['company_name'], "patents": int(r['patent_count'])} for _, r in top_companies.head(10).iterrows()
        ],
        "top_countries": [
            {"country": r['country'], "share": r['share_percent']} for _, r in countries.head(10).iterrows()
        ],
    }
    with open(os.path.join(OUTPUT_DIR, "patent_report.json"), "w") as jf:
        json.dump(json_data, jf, indent=2)

    print("CSV and JSON reports saved.")

    # Plots
    plt.figure(figsize=(10,5))
    plt.bar(top_inventors.head(10)["inventor_name"], top_inventors.head(10)["patent_count"])
    plt.xticks(rotation=45, ha='right')
    plt.title("Top 10 Inventors"); plt.tight_layout()
    plt.savefig(os.path.join(OUTPUT_DIR, "top_inventors.png")); plt.close()

    plt.figure(figsize=(10,5))
    plt.plot(trends["year"], trends["patent_count"], marker='o')
    plt.title("Patents per Year"); plt.xlabel("Year"); plt.ylabel("Count"); plt.grid(True)
    plt.tight_layout(); plt.savefig(os.path.join(OUTPUT_DIR, "yearly_trends.png")); plt.close()

    top10c = countries.head(10)
    plt.figure(figsize=(8,8))
    plt.pie(top10c["patent_count"], labels=top10c["country"], autopct='%1.1f%%', startangle=140)
    plt.title("Country Share"); plt.tight_layout()
    plt.savefig(os.path.join(OUTPUT_DIR, "country_share.png")); plt.close()

    print("Charts saved.")

if __name__ == "__main__":
    run_queries_and_export()