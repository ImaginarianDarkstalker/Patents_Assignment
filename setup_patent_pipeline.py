import os

BASE_DIR = "patent_pipeline"
DIRS = [
    "data/raw",
    "scripts",
    "clean_data",
    "output"
]

FILES = {
    "config.py": '''\
import os

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
RAW_DATA_DIR = os.path.join(BASE_DIR, "data", "raw")
DB_PATH = os.path.join(BASE_DIR, "output", "patents.db")
CLEAN_DATA_DIR = os.path.join(BASE_DIR, "clean_data")
OUTPUT_DIR = os.path.join(BASE_DIR, "output")

# Actual file names from your screenshots (optional: can be used by downloader)
BULK_FILES = {
    "g_patent.tsv.zip": "https://s3.amazonaws.com/data.patentsview.org/download/g_patent.tsv.zip",
    "g_patent_abstract.tsv.zip": "https://s3.amazonaws.com/data.patentsview.org/download/g_patent_abstract.tsv.zip",
    "g_inventor_disambiguated.tsv.zip": "https://s3.amazonaws.com/data.patentsview.org/download/g_inventor_disambiguated.tsv.zip",
    "g_persistent_assignee.tsv.zip": "https://s3.amazonaws.com/data.patentsview.org/download/g_persistent_assignee.tsv.zip",
    "g_application.tsv.zip": "https://s3.amazonaws.com/data.patentsview.org/download/g_application.tsv.zip",
}
''',

    "schema.sql": '''\
CREATE TABLE IF NOT EXISTS patents (
    patent_id TEXT PRIMARY KEY,
    title TEXT,
    abstract TEXT,
    filing_date DATE,
    year INTEGER
);

CREATE TABLE IF NOT EXISTS inventors (
    inventor_id TEXT PRIMARY KEY,
    name TEXT,
    country TEXT
);

CREATE TABLE IF NOT EXISTS companies (
    company_id TEXT PRIMARY KEY,
    name TEXT
);

CREATE TABLE IF NOT EXISTS patent_relationships (
    patent_id TEXT,
    inventor_id TEXT,
    company_id TEXT,
    PRIMARY KEY (patent_id, inventor_id, company_id),
    FOREIGN KEY (patent_id) REFERENCES patents(patent_id),
    FOREIGN KEY (inventor_id) REFERENCES inventors(inventor_id),
    FOREIGN KEY (company_id) REFERENCES companies(company_id)
);
''',

    "scripts/queries.sql": '''\
-- Q1: Top Inventors – who has the most patents?
SELECT
    i.name AS inventor_name,
    COUNT(DISTINCT r.patent_id) AS patent_count
FROM patent_relationships r
JOIN inventors i ON r.inventor_id = i.inventor_id
WHERE i.inventor_id != 'UNKNOWN'
GROUP BY i.name
ORDER BY patent_count DESC
LIMIT 10;

-- Q2: Top Companies – which companies own the most patents?
SELECT
    c.name AS company_name,
    COUNT(DISTINCT r.patent_id) AS patent_count
FROM patent_relationships r
JOIN companies c ON r.company_id = c.company_id
WHERE c.company_id != 'UNKNOWN'
GROUP BY c.name
ORDER BY patent_count DESC
LIMIT 10;

-- Q3: Countries – which countries produce the most patents?
SELECT
    i.country,
    COUNT(DISTINCT r.patent_id) AS patent_count,
    ROUND(COUNT(DISTINCT r.patent_id) * 100.0 / (SELECT COUNT(DISTINCT patent_id) FROM patents), 2) AS share_percent
FROM patent_relationships r
JOIN inventors i ON r.inventor_id = i.inventor_id
WHERE i.country != 'Unknown' AND i.inventor_id != 'UNKNOWN'
GROUP BY i.country
ORDER BY patent_count DESC
LIMIT 10;

-- Q4: Trends Over Time – patents per year
SELECT
    year,
    COUNT(*) AS patent_count
FROM patents
WHERE year IS NOT NULL
GROUP BY year
ORDER BY year;

-- Q5: JOIN Query – combine patents with inventors and companies (sample)
SELECT
    p.patent_id,
    p.title,
    i.name AS inventor_name,
    c.name AS company_name
FROM patents p
JOIN patent_relationships r ON p.patent_id = r.patent_id
JOIN inventors i ON r.inventor_id = i.inventor_id
JOIN companies c ON r.company_id = c.company_id
LIMIT 100;

-- Q6: CTE Query – prolific inventors (>100 patents) by country
WITH prolific_inventors AS (
    SELECT inventor_id
    FROM patent_relationships
    WHERE inventor_id != 'UNKNOWN'
    GROUP BY inventor_id
    HAVING COUNT(DISTINCT patent_id) > 100
)
SELECT
    i.country,
    COUNT(DISTINCT pi.inventor_id) AS num_prolific_inventors,
    SUM(pi.patent_count) AS total_patents_by_them
FROM prolific_inventors pi2
JOIN inventors i ON pi2.inventor_id = i.inventor_id
JOIN (
    SELECT inventor_id, COUNT(DISTINCT patent_id) AS patent_count
    FROM patent_relationships
    GROUP BY inventor_id
) pi ON pi2.inventor_id = pi.inventor_id
GROUP BY i.country
ORDER BY num_prolific_inventors DESC;

-- Q7: Ranking Query – rank inventors by patent count using window function
SELECT
    i.name AS inventor_name,
    COUNT(DISTINCT r.patent_id) AS patent_count,
    RANK() OVER (ORDER BY COUNT(DISTINCT r.patent_id) DESC) AS rank
FROM patent_relationships r
JOIN inventors i ON r.inventor_id = i.inventor_id
WHERE i.inventor_id != 'UNKNOWN'
GROUP BY i.name
ORDER BY rank;
''',

    "scripts/utils.py": '''\
import os

def ensure_dir(file_path):
    directory = os.path.dirname(file_path)
    if directory and not os.path.exists(directory):
        os.makedirs(directory, exist_ok=True)
''',

    "scripts/1_download_data.py": '''\
# This script downloads the raw files from the PatentsView S3 bucket.
# It expects the URLs in config.BULK_FILES.
# Run this ONLY if you haven't already placed the .tsv.zip files in data/raw/ manually.
import os, sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
import requests
from config import RAW_DATA_DIR, BULK_FILES

def download_file(url, dest):
    print(f"Downloading {url} ...")
    resp = requests.get(url, stream=True)
    resp.raise_for_status()
    with open(dest, 'wb') as f:
        for chunk in resp.iter_content(chunk_size=8192):
            f.write(chunk)
    print(f"Saved to {dest}")

if __name__ == "__main__":
    os.makedirs(RAW_DATA_DIR, exist_ok=True)
    for filename, url in BULK_FILES.items():
        dest = os.path.join(RAW_DATA_DIR, filename)
        if not os.path.exists(dest):
            download_file(url, dest)
        else:
            print(f"{filename} already exists, skipping.")
''',

    "scripts/2_ingest_and_clean.py": '''\
import os
import zipfile
import sqlite3
import pandas as pd
import numpy as np
from utils import ensure_dir
from config import RAW_DATA_DIR, DB_PATH, CLEAN_DATA_DIR

def unzip_file(zip_path, extract_to):
    with zipfile.ZipFile(zip_path, 'r') as zf:
        zf.extractall(extract_to)

def load_tsv(filename):
    """Load a TSV file, unzipping if necessary."""
    path = os.path.join(RAW_DATA_DIR, filename)
    if not os.path.exists(path):
        zip_path = path + ".zip"
        if os.path.exists(zip_path):
            unzip_file(zip_path, RAW_DATA_DIR)
        else:
            raise FileNotFoundError(f"Neither {path} nor {zip_path} found.")
    df = pd.read_csv(path, sep="\\t", dtype=str, low_memory=False)
    return df

def clean_data():
    ensure_dir(DB_PATH)
    ensure_dir(CLEAN_DATA_DIR)

    print("Loading raw TSVs...")

    # --- Patent main table ---
    patent_main = load_tsv("g_patent.tsv")
    patent_abstract = load_tsv("g_patent_abstract.tsv")

    # Merge to get title + abstract
    patents = patent_main[["patent_id", "patent_title", "patent_date"]].copy()
    patents.columns = ["patent_id", "title", "filing_date"]
    patents["abstract"] = patent_abstract.set_index("patent_id")["patent_abstract"]
    patents["abstract"] = patents["abstract"].fillna("No abstract available")
    patents["title"] = patents["title"].fillna("Unknown")
    patents["filing_date"] = pd.to_datetime(patents["filing_date"], errors="coerce")
    patents["year"] = patents["filing_date"].dt.year

    # --- Inventors ---
    # Prefer disambiguated file; fallback to persistent if needed.
    inv_file = None
    if os.path.exists(os.path.join(RAW_DATA_DIR, "g_inventor_disambiguated.tsv")) or \
       os.path.exists(os.path.join(RAW_DATA_DIR, "g_inventor_disambiguated.tsv.zip")):
        inv_file = "g_inventor_disambiguated.tsv"
    elif os.path.exists(os.path.join(RAW_DATA_DIR, "g_persistent_inventor.tsv")) or \
         os.path.exists(os.path.join(RAW_DATA_DIR, "g_persistent_inventor.tsv.zip")):
        inv_file = "g_persistent_inventor.tsv"
    else:
        raise Exception("No inventor file found (g_inventor_disambiguated nor g_persistent_inventor).")

    inventors_raw = load_tsv(inv_file)
    inv_cols = inventors_raw.columns.tolist()
    # Standard columns: inventor_id, name_first, name_last. Country might be under 'country' or 'nationality'
    has_country = 'country' in inv_cols
    # Build clean inventors
    if has_country:
        inventors = inventors_raw[["inventor_id", "name_first", "name_last", "country"]].copy()
        inventors["country"] = inventors["country"].fillna("Unknown")
    else:
        inventors = inventors_raw[["inventor_id", "name_first", "name_last"]].copy()
        inventors["country"] = "Unknown"
    inventors["name"] = inventors["name_first"].fillna("") + " " + inventors["name_last"].fillna("")
    inventors["name"] = inventors["name"].str.strip().replace("", "Unknown")
    inventors = inventors[["inventor_id", "name", "country"]]

    # --- Companies (Assignees) ---
    # Use persistent assignee file
    assignee_file = None
    if os.path.exists(os.path.join(RAW_DATA_DIR, "g_persistent_assignee.tsv")) or \
       os.path.exists(os.path.join(RAW_DATA_DIR, "g_persistent_assignee.tsv.zip")):
        assignee_file = "g_persistent_assignee.tsv"
    elif os.path.exists(os.path.join(RAW_DATA_DIR, "g_assignee_disambiguated.tsv")) or \
         os.path.exists(os.path.join(RAW_DATA_DIR, "g_assignee_disambiguated.tsv.zip")):
        assignee_file = "g_assignee_disambiguated.tsv"
    else:
        raise Exception("No assignee file found (g_persistent_assignee or g_assignee_disambiguated).")

    assignees_raw = load_tsv(assignee_file)
    companies = assignees_raw[["assignee_id", "organization"]].copy()
    companies.columns = ["company_id", "name"]
    companies["name"] = companies["name"].fillna("Unknown Assignee")
    companies = companies.dropna(subset=["company_id"])

    # --- Relationships ---
    # Try to use the dedicated linking file 'g_patent_inventor.tsv' and 'g_patent_assignee.tsv'
    # if available; otherwise fall back to the 'g_application.tsv' (which has pipe-separated IDs).
    pi_file = os.path.join(RAW_DATA_DIR, "g_patent_inventor.tsv")
    pa_file = os.path.join(RAW_DATA_DIR, "g_patent_assignee.tsv")
    if not os.path.exists(pi_file) and os.path.exists(pi_file + ".zip"):
        unzip_file(pi_file + ".zip", RAW_DATA_DIR)
    if not os.path.exists(pa_file) and os.path.exists(pa_file + ".zip"):
        unzip_file(pa_file + ".zip", RAW_DATA_DIR)

    if os.path.exists(pi_file) and os.path.exists(pa_file):
        print("Found separate patent_inventor and patent_assignee tables.")
        pi = pd.read_csv(pi_file, sep="\\t", dtype=str)[["patent_id", "inventor_id"]].drop_duplicates()
        pa = pd.read_csv(pa_file, sep="\\t", dtype=str)[["patent_id", "assignee_id"]].drop_duplicates()
        pa.columns = ["patent_id", "company_id"]
        relationships = pd.merge(pi, pa, on="patent_id", how="outer")
    else:
        print("Using g_application.tsv to extract inventor & assignee relationships.")
        app = load_tsv("g_application.tsv")
        # The application table often contains arrays separated by pipes.
        # We'll explode them.
        # Ensure necessary columns exist
        if 'inventor_id' not in app.columns or 'assignee_id' not in app.columns:
            raise Exception("g_application.tsv must contain 'inventor_id' and 'assignee_id' columns.")
        # Split pipe-delimited IDs into multiple rows
        app = app[['patent_id', 'inventor_id', 'assignee_id']].copy()
        app['inventor_id'] = app['inventor_id'].fillna('')
        app['assignee_id'] = app['assignee_id'].fillna('')
        # Explode inventor_id
        inv_exploded = app.assign(inventor_id=app['inventor_id'].str.split('\\|')).explode('inventor_id')
        # Explode assignee_id
        full_exploded = inv_exploded.assign(assignee_id=inv_exploded['assignee_id'].str.split('\\|')).explode('assignee_id')
        relationships = full_exploded[['patent_id', 'inventor_id', 'assignee_id']]
        relationships.columns = ['patent_id', 'inventor_id', 'company_id']
        relationships = relationships.drop_duplicates()

    # Clean UNKNOWNs
    relationships["inventor_id"] = relationships["inventor_id"].fillna("UNKNOWN")
    relationships["company_id"] = relationships["company_id"].fillna("UNKNOWN")
    rel = relationships[relationships["patent_id"].isin(patents["patent_id"])]

    # --- Store in SQLite ---
    conn = sqlite3.connect(DB_PATH)
    with open(os.path.join("..", "schema.sql")) as f:
        conn.executescript(f.read())

    patents.to_sql("patents", conn, if_exists="replace", index=False)
    inventors.to_sql("inventors", conn, if_exists="replace", index=False)
    companies.to_sql("companies", conn, if_exists="replace", index=False)
    rel.to_sql("patent_relationships", conn, if_exists="replace", index=False)

    conn.commit()
    conn.close()

    # Export clean CSVs
    patents.to_csv(os.path.join(CLEAN_DATA_DIR, "clean_patents.csv"), index=False)
    inventors.to_csv(os.path.join(CLEAN_DATA_DIR, "clean_inventors.csv"), index=False)
    companies.to_csv(os.path.join(CLEAN_DATA_DIR, "clean_companies.csv"), index=False)

    print("Data ingested and cleaned successfully.")

if __name__ == "__main__":
    clean_data()
''',

    "scripts/3_reports.py": '''\
import os
import sqlite3
import pandas as pd
import json
import matplotlib.pyplot as plt
from config import DB_PATH, OUTPUT_DIR
from utils import ensure_dir

def run_queries_and_export():
    ensure_dir(OUTPUT_DIR)
    conn = sqlite3.connect(DB_PATH)

    with open(os.path.join("..", "scripts", "queries.sql")) as f:
        queries_text = f.read()

    query_dict = {}
    current_name = None
    current_sql = []
    for line in queries_text.splitlines():
        if line.startswith("-- Q"):
            if current_name:
                query_dict[current_name] = "\\n".join(current_sql).strip()
            current_name = line.strip().lstrip("- ").strip()
            current_sql = []
        elif line.strip():
            current_sql.append(line)
    if current_name:
        query_dict[current_name] = "\\n".join(current_sql).strip()

    results = {}
    top_inventors = pd.read_sql_query(query_dict["Q1: Top Inventors"], conn)
    top_companies = pd.read_sql_query(query_dict["Q2: Top Companies"], conn)
    countries = pd.read_sql_query(query_dict["Q3: Countries"], conn)
    trends = pd.read_sql_query(query_dict["Q4: Trends Over Time"], conn)
    join_sample = pd.read_sql_query(query_dict["Q5: JOIN Query"], conn)
    cte_results = pd.read_sql_query(query_dict["Q6: CTE Query"], conn)
    ranking = pd.read_sql_query(query_dict["Q7: Ranking Query"], conn)

    total_patents = pd.read_sql_query("SELECT COUNT(*) AS total FROM patents", conn).iloc[0,0]
    conn.close()

    # Console
    print("\\n================== PATENT REPORT ==================")
    print(f"Total Patents: {total_patents:,}")
    print("\\nTop Inventors:")
    for _, row in top_inventors.head(5).iterrows():
        print(f"  {row['inventor_name']} - {row['patent_count']}")
    print("\\nTop Companies:")
    for _, row in top_companies.head(5).iterrows():
        print(f"  {row['company_name']} - {row['patent_count']}")
    print("\\nTop Countries (by inventor nationality):")
    for _, row in countries.head(5).iterrows():
        print(f"  {row['country']} - {row['share_percent']}% share")
    print("===================================================\\n")

    # CSV
    top_inventors.to_csv(os.path.join(OUTPUT_DIR, "top_inventors.csv"), index=False)
    top_companies.to_csv(os.path.join(OUTPUT_DIR, "top_companies.csv"), index=False)
    trends.to_csv(os.path.join(OUTPUT_DIR, "country_trends.csv"), index=False)

    # JSON
    json_data = {
        "total_patents": int(total_patents),
        "top_inventors": [{"name": row['inventor_name'], "patents": int(row['patent_count'])} for _, row in top_inventors.head(10).iterrows()],
        "top_companies": [{"name": row['company_name'], "patents": int(row['patent_count'])} for _, row in top_companies.head(10).iterrows()],
        "top_countries": [{"country": row['country'], "share": row['share_percent']} for _, row in countries.head(10).iterrows()]
    }
    with open(os.path.join(OUTPUT_DIR, "patent_report.json"), "w") as jf:
        json.dump(json_data, jf, indent=2)

    print("CSV and JSON reports saved to output/")

    # Visualizations
    plt.figure(figsize=(10,5))
    top10_inv = top_inventors.head(10)
    plt.bar(top10_inv["inventor_name"], top10_inv["patent_count"])
    plt.xticks(rotation=45, ha="right")
    plt.title("Top 10 Inventors by Patent Count")
    plt.tight_layout()
    plt.savefig(os.path.join(OUTPUT_DIR, "top_inventors.png"))
    plt.close()

    plt.figure(figsize=(10,5))
    plt.plot(trends["year"], trends["patent_count"], marker='o')
    plt.title("Patents Filed Per Year")
    plt.xlabel("Year")
    plt.ylabel("Number of Patents")
    plt.grid(True)
    plt.tight_layout()
    plt.savefig(os.path.join(OUTPUT_DIR, "yearly_trends.png"))
    plt.close()

    top10_countries = countries.head(10).copy()
    plt.figure(figsize=(8,8))
    plt.pie(top10_countries["patent_count"], labels=top10_countries["country"], autopct='%1.1f%%', startangle=140)
    plt.title("Patent Share by Country (Top 10)")
    plt.tight_layout()
    plt.savefig(os.path.join(OUTPUT_DIR, "country_share.png"))
    plt.close()

    print("Visualizations saved to output/")

if __name__ == "__main__":
    run_queries_and_export()
''',

    "app.py": '''\
import streamlit as st
import sqlite3
import pandas as pd
import matplotlib.pyplot as plt
from config import DB_PATH

st.set_page_config(page_title="Patent Analytics Dashboard", layout="wide")
st.title("📊 Patent Data Pipeline – Dashboard")

conn = sqlite3.connect(DB_PATH)

year_min, year_max = 1976, 2024
selected_years = st.sidebar.slider("Select Year Range", year_min, year_max, (year_min, year_max))

query = """
    SELECT p.*, i.name AS inventor_name, c.name AS company_name
    FROM patents p
    JOIN patent_relationships r ON p.patent_id = r.patent_id
    JOIN inventors i ON r.inventor_id = i.inventor_id
    JOIN companies c ON r.company_id = c.company_id
    WHERE p.year BETWEEN ? AND ?
"""
df = pd.read_sql_query(query, conn, params=[selected_years[0], selected_years[1]])

col1, col2, col3 = st.columns(3)
col1.metric("Total Patents", df['patent_id'].nunique())
col2.metric("Unique Inventors", df['inventor_name'].nunique())
col3.metric("Unique Companies", df['company_name'].nunique())

st.subheader("Top Inventors")
top_inv = df.groupby("inventor_name")["patent_id"].nunique().nlargest(10)
fig1, ax1 = plt.subplots()
top_inv.plot(kind="barh", ax=ax1)
ax1.set_xlabel("Patents")
st.pyplot(fig1)

st.subheader("Patents per Year")
trends = df.groupby("year")["patent_id"].nunique().sort_index()
fig2, ax2 = plt.subplots()
trends.plot(ax=ax2, marker='o')
st.pyplot(fig2)

st.subheader("Country Share (by inventor)")
country_counts = df.groupby('country')['patent_id'].nunique().nlargest(10)
fig3, ax3 = plt.subplots()
country_counts.plot(kind="pie", ax=ax3, autopct='%1.1f%%')
st.pyplot(fig3)

st.caption("Data from USPTO PatentsView")
conn.close()
''',

    "requirements.txt": '''\
pandas>=1.3
numpy
matplotlib
streamlit
requests
'''
}

def create_project():
    for d in DIRS:
        os.makedirs(os.path.join(BASE_DIR, d), exist_ok=True)

    for filepath, content in FILES.items():
        full_path = os.path.join(BASE_DIR, filepath)
        with open(full_path, 'w', encoding='utf-8') as f:
            f.write(content)

    print(f"Project generated in '{BASE_DIR}'")
    print("\nNext steps:")
    print("1. cd patent_pipeline")
    print("2. pip install -r requirements.txt")
    print("3. Place your .tsv.zip files inside data/raw/ (or use: python scripts/1_download_data.py)")
    print("4. Run: python scripts/2_ingest_and_clean.py")
    print("5. Run: python scripts/3_reports.py")
    print("6. Launch: streamlit run app.py")
    print("7. Initialize Git and push to GitHub.")

if __name__ == "__main__":
    create_project()