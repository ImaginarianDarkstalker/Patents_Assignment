pip install requests
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import streamlit as st
import sqlite3
import pandas as pd
import matplotlib.pyplot as plt
from patent_pipeline.scripts.config import DB_PATH

import requests
from pathlib import Path

DB_URL = "https://huggingface.co/datasets/RisingDuck/patents.db"
DB_PATH = Path("patents.db")

def download_database():
    print("Downloading database...")

    response = requests.get(DB_URL, stream=True)
    response.raise_for_status()

    with open(DB_PATH, "wb") as f:
        for chunk in response.iter_content(chunk_size=8192):
            if chunk:
                f.write(chunk)

    print("Database downloaded successfully.")

if not DB_PATH.exists():
    download_database()

@st.cache_resource
def ensure_database():
    if not DB_PATH.exists():
        download_database()
    return True

ensure_database()

st.set_page_config(page_title="Patent Analytics Dashboard", layout="wide")
st.title("📊 Patent Data Pipeline – Dashboard")

conn = sqlite3.connect(DB_PATH)

# ---------- Cached data loader (direct joins – no view/empty table needed) ----------
@st.cache_data
def load_data(year_min, year_max):
    query = """
        SELECT
            p.patent_id,
            p.title,
            p.year,
            COALESCE(i.name, 'Unknown Inventor')   AS inventor_name,
            COALESCE(c.name, 'Unknown Assignee')   AS company_name,
            COALESCE(i.country, 'Unknown')         AS country
        FROM patents p
        LEFT JOIN patent_inventor pi ON p.patent_id = pi.patent_id
        LEFT JOIN patent_assignee pa ON p.patent_id = pa.patent_id
        LEFT JOIN inventors i ON pi.rel_id = i.inventor_id
        LEFT JOIN companies c ON pa.company_id = c.company_id
        WHERE p.year BETWEEN ? AND ?
    """
    return pd.read_sql_query(query, conn, params=[year_min, year_max])

# ---------- Year range slider ----------
year_range = pd.read_sql_query(
    "SELECT MIN(year) AS min_year, MAX(year) AS max_year FROM patents WHERE year IS NOT NULL", conn
)
min_year = int(year_range.iloc[0]['min_year']) if year_range.iloc[0]['min_year'] else 1976
max_year = int(year_range.iloc[0]['max_year']) if year_range.iloc[0]['max_year'] else 2024

selected_years = st.sidebar.slider("Select Year Range", min_year, max_year, (min_year, max_year))
df = load_data(selected_years[0], selected_years[1])

# ---------- Summary metrics ----------
col1, col2, col3 = st.columns(3)
col1.metric("Total Patents", df['patent_id'].nunique())
col2.metric("Unique Inventors", df['inventor_name'].nunique())
col3.metric("Unique Companies", df['company_name'].nunique())

# ---------- Top 10 Inventors (excluding "Unknown") ----------
st.subheader("🏆 Top 10 Inventors")
known_inv = df[df['inventor_name'] != 'Unknown Inventor']
if not known_inv.empty:
    top_inv = known_inv.groupby('inventor_name')['patent_id'].nunique().nlargest(10)
    fig1, ax1 = plt.subplots(figsize=(10, 5))
    top_inv.sort_values().plot(kind='barh', ax=ax1, color='steelblue')
    ax1.set_xlabel('Number of Patents')
    ax1.set_title('Top Inventors by Patent Count')
    for i, v in enumerate(top_inv.sort_values()):
        ax1.text(v + 1, i, str(v), va='center')
    st.pyplot(fig1)
else:
    st.info("No known inventor names found for this period.")

# ---------- Country Share (Top 10, exclude "Unknown") ----------
st.subheader("🌍 Patent Share by Country (Top 10)")
known_countries = df[df['country'] != 'Unknown']
if not known_countries.empty:
    country_counts = known_countries.groupby('country')['patent_id'].nunique().nlargest(10)
    col_left, col_right = st.columns([3, 2])
    with col_left:
        fig2, ax2 = plt.subplots(figsize=(7, 5))
        country_counts.sort_values().plot(kind='barh', ax=ax2, color='darkorange')
        ax2.set_xlabel('Number of Patents')
        ax2.set_title('Top 10 Countries by Patent Count')
        st.pyplot(fig2)
    with col_right:
        st.dataframe(
            country_counts.reset_index()
            .rename(columns={'country': 'Country', 'patent_id': 'Patents'})
            .assign(Share=lambda x: (x['Patents'] / x['Patents'].sum() * 100).round(1).astype(str) + '%'),
            use_container_width=True,
        )
else:
    st.info("No country data available.")

# ---------- Patents per Year ----------
st.subheader("📈 Patents per Year")
trends = df.groupby('year')['patent_id'].nunique().sort_index()
if not trends.empty:
    fig3, ax3 = plt.subplots(figsize=(8, 4))
    trends.plot(ax=ax3, marker='o', color='green')
    ax3.set_title('Patents Filed Per Year')
    ax3.set_xlabel('Year')
    ax3.set_ylabel('Number of Patents')
    ax3.grid(True, linestyle='--', alpha=0.5)
    st.pyplot(fig3)
else:
    st.info("No trend data.")

st.caption("Data from USPTO PatentsView – processed with custom pipeline.")
conn.close()
