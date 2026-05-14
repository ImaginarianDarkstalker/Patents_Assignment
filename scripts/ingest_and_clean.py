import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import zipfile
import sqlite3
import pandas as pd

from patent_pipeline.scripts.utils import ensure_dir
from patent_pipeline.scripts.config import RAW_DATA_DIR, DB_PATH, CLEAN_DATA_DIR

RAW_DATA_DIR = Path(RAW_DATA_DIR)
DB_PATH = Path(DB_PATH)
CLEAN_DATA_DIR = Path(CLEAN_DATA_DIR)

def unzip_if_needed(file_path: Path):
    zip_path = file_path.with_suffix(file_path.suffix + ".zip")
    if not file_path.exists() and zip_path.exists():
        print(f"Unzipping {zip_path} ...")
        with zipfile.ZipFile(zip_path, 'r') as zf:
            zf.extractall(file_path.parent)

def get_raw_columns(filename: str) -> list:
    file_path = RAW_DATA_DIR / filename
    unzip_if_needed(file_path)
    if not file_path.exists():
        raise FileNotFoundError(f"File not found: {file_path}")
    return pd.read_csv(file_path, sep="\t", nrows=0).columns.tolist()

def chunked_reader(filename, chunksize=50000, dtype=str, usecols=None,
                   engine='c', on_bad_lines='error'):
    file_path = RAW_DATA_DIR / filename
    unzip_if_needed(file_path)
    if not file_path.exists():
        raise FileNotFoundError(f"File not found: {file_path}")

    kwargs = dict(sep="\t", dtype=dtype, chunksize=chunksize,
                  usecols=usecols, engine=engine, on_bad_lines=on_bad_lines)
    if engine != 'python':
        kwargs['low_memory'] = False

    return pd.read_csv(file_path, **kwargs)

def clean_data():
    ensure_dir(DB_PATH.parent)
    ensure_dir(CLEAN_DATA_DIR)

    conn = sqlite3.connect(DB_PATH)
    schema_path = Path(__file__).resolve().parents[1] / "schema.sql"
    if schema_path.exists():
        with open(schema_path) as f:
            conn.executescript(f.read())
    else:
        print(f"Warning: schema.sql not found at {schema_path}.")

    # 1. Patent main table
    print("1/7 Processing patent main table...")
    main_cols = get_raw_columns("g_patent.tsv")
    id_col = next(c for c in main_cols if c.lower() == 'patent_id')
    title_col = next(c for c in main_cols if c.lower() in ('patent_title', 'title'))
    date_col = next(c for c in main_cols if c.lower() in ('patent_date', 'filing_date'))
    main_chunks = chunked_reader("g_patent.tsv", chunksize=10000, dtype=object,
                                 usecols=[id_col, title_col, date_col],
                                 engine='python', on_bad_lines='skip')
    first = True
    cnt = 0
    for chunk in main_chunks:
        cnt += 1
        if cnt % 10 == 0: print(f"  Main chunk {cnt}...")
        chunk = chunk.rename(columns={id_col: "patent_id", title_col: "title", date_col: "filing_date"})
        chunk["filing_date"] = pd.to_datetime(chunk["filing_date"], errors="coerce").dt.strftime('%Y-%m-%d')
        chunk["year"] = chunk["filing_date"].str[:4]
        if first:
            chunk.to_sql("patents_main", conn, if_exists='replace', index=False)
            first = False
        else:
            chunk.to_sql("patents_main", conn, if_exists='append', index=False)
    print(f"  Main table done ({cnt} chunks).")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_main_patent ON patents_main(patent_id)")

    # 2. Abstracts
    print("2/7 Processing abstracts...")
    abs_cols = get_raw_columns("g_patent_abstract.tsv")
    pid_col = next(c for c in abs_cols if c.lower() == 'patent_id')
    abs_col = next(c for c in abs_cols if c.lower() in ('patent_abstract', 'abstract'))
    abs_chunks = chunked_reader("g_patent_abstract.tsv", chunksize=10000, dtype=object,
                                usecols=[pid_col, abs_col], engine='python', on_bad_lines='skip')
    first = True
    cnt = 0
    for chunk in abs_chunks:
        cnt += 1
        if cnt % 10 == 0: print(f"  Abstract chunk {cnt}...")
        chunk = chunk.rename(columns={pid_col: "patent_id", abs_col: "abstract"})
        chunk["abstract"] = chunk["abstract"].fillna("No abstract available")
        if first:
            chunk.to_sql("abstracts_data", conn, if_exists='replace', index=False)
            first = False
        else:
            chunk.to_sql("abstracts_data", conn, if_exists='append', index=False)
    print(f"  Abstracts done ({cnt} chunks).")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_abs_patent ON abstracts_data(patent_id)")

    # 3. Build final PATENTS table
    print("3/7 Building final patents table...")
    conn.execute("DROP TABLE IF EXISTS patents")
    conn.execute("""
        CREATE TABLE patents AS
        SELECT p.patent_id, p.title, p.filing_date, p.year,
               COALESCE(a.abstract, 'No abstract available') AS abstract
        FROM patents_main p
        LEFT JOIN abstracts_data a USING(patent_id)
    """)
    conn.execute("DROP TABLE patents_main")
    conn.execute("DROP TABLE abstracts_data")

    # 4. Export patents CSV
    print("4/7 Exporting clean_patents.csv...")
    csv_path = CLEAN_DATA_DIR / "clean_patents.csv"
    first_csv = True
    for chunk in pd.read_sql("SELECT * FROM patents", conn, chunksize=50000):
        if first_csv:
            chunk.to_csv(csv_path, index=False, mode='w', header=True)
            first_csv = False
        else:
            chunk.to_csv(csv_path, index=False, mode='a', header=False)

    # 5. Inventors + Location
    print("5/7 Processing inventors (with location)...")
    disambig_path = RAW_DATA_DIR / "g_inventor_disambiguated.tsv"
    disambig_exists = disambig_path.exists() or (RAW_DATA_DIR / "g_inventor_disambiguated.tsv.zip").exists()
    if not disambig_exists: raise FileNotFoundError("g_inventor_disambiguated.tsv not found.")
    inv_cols = get_raw_columns("g_inventor_disambiguated.tsv")
    print(f"  Columns: {inv_cols}")
    id_col = next(c for c in inv_cols if c.lower() in ('inventor_id', 'persistent_inventor_id', 'id'))
    fname_col = next(c for c in inv_cols if c.lower() in ('disambig_inventor_name_first', 'name_first', 'first_name'))
    lname_col = next(c for c in inv_cols if c.lower() in ('disambig_inventor_name_last', 'name_last', 'last_name'))
    loc_col = next((c for c in inv_cols if c.lower() == 'location_id'), None)
    has_loc = loc_col is not None
    usecols = [id_col, fname_col, lname_col] + ([loc_col] if has_loc else [])
    inv_chunks = chunked_reader("g_inventor_disambiguated.tsv", chunksize=200000, dtype=object, usecols=usecols)
    first = True
    for chunk in inv_chunks:
        chunk = chunk.rename(columns={id_col: "inventor_id", fname_col: "name_first", lname_col: "name_last"})
        if has_loc: chunk = chunk.rename(columns={loc_col: "location_id"})
        else: chunk["location_id"] = None
        if first:
            chunk[["inventor_id", "name_first", "name_last", "location_id"]].to_sql("disambig_staging", conn, if_exists='replace', index=False)
            first = False
        else:
            chunk[["inventor_id", "name_first", "name_last", "location_id"]].to_sql("disambig_staging", conn, if_exists='append', index=False)

    # Location table → country (now includes disambig_country)
    loc_file = "g_location_disambiguated.tsv"
    loc_available = False
    if (RAW_DATA_DIR / loc_file).exists() or (RAW_DATA_DIR / (loc_file + ".zip")).exists():
        loc_cols = get_raw_columns(loc_file)
        print(f"  Location columns: {loc_cols}")
        loc_id_col = next((c for c in loc_cols if c.lower() == 'location_id'), None)
        # Look for country/disambig_country/nationality
        cntry_col = next((c for c in loc_cols if c.lower() in ('country', 'disambig_country', 'nationality')), None)
        if loc_id_col and cntry_col and has_loc:
            loc_available = True
            loc_chunks = chunked_reader(loc_file, chunksize=100000, dtype=object, usecols=[loc_id_col, cntry_col])
            first_loc = True
            for chunk in loc_chunks:
                chunk = chunk.rename(columns={loc_id_col: "location_id", cntry_col: "country"})
                if first_loc:
                    chunk.to_sql("location_staging", conn, if_exists='replace', index=False)
                    first_loc = False
                else:
                    chunk.to_sql("location_staging", conn, if_exists='append', index=False)
        else:
            print("  Location file missing required columns; country will be Unknown.")
    else:
        print("  Location file not found; country will be Unknown.")

    conn.execute("DROP TABLE IF EXISTS inventors")
    if loc_available:
        conn.execute("""
            CREATE TABLE inventors AS
            SELECT i.inventor_id,
                   COALESCE(MAX(i.name_first) FILTER (WHERE i.name_first != ''), '') || ' ' ||
                   COALESCE(MAX(i.name_last) FILTER (WHERE i.name_last != ''), '') AS name,
                   COALESCE(MAX(l.country) FILTER (WHERE l.country != ''), 'Unknown') AS country
            FROM disambig_staging i
            LEFT JOIN location_staging l ON i.location_id = l.location_id
            GROUP BY i.inventor_id
        """)
        conn.execute("DROP TABLE location_staging")
    else:
        conn.execute("""
            CREATE TABLE inventors AS
            SELECT inventor_id,
                   COALESCE(MAX(name_first) FILTER (WHERE name_first != ''), '') || ' ' ||
                   COALESCE(MAX(name_last) FILTER (WHERE name_last != ''), '') AS name,
                   'Unknown' AS country
            FROM disambig_staging
            GROUP BY inventor_id
        """)
    conn.execute("DROP TABLE disambig_staging")
    conn.execute("UPDATE inventors SET name = TRIM(name)")
    conn.execute("UPDATE inventors SET name = 'Unknown' WHERE name = ''")

    inv_csv = CLEAN_DATA_DIR / "clean_inventors.csv"
    first_csv = True
    for chunk in pd.read_sql("SELECT * FROM inventors", conn, chunksize=50000):
        if first_csv:
            chunk.to_csv(inv_csv, index=False, mode='w', header=True)
            first_csv = False
        else:
            chunk.to_csv(inv_csv, index=False, mode='a', header=False)

    # 6. Companies (now using raw_assignee_organization)
    print("6/7 Processing companies...")
    pers_ass_path = RAW_DATA_DIR / "g_persistent_assignee.tsv"
    nd_ass_path = RAW_DATA_DIR / "g_assignee_not_disambiguated.tsv"
    pers_ass_exists = pers_ass_path.exists() or (RAW_DATA_DIR / "g_persistent_assignee.tsv.zip").exists()
    nd_ass_exists = nd_ass_path.exists() or (RAW_DATA_DIR / "g_assignee_not_disambiguated.tsv.zip").exists()
    if not pers_ass_exists: raise FileNotFoundError("g_persistent_assignee.tsv not found.")
    pers_cols = get_raw_columns("g_persistent_assignee.tsv")
    print(f"  Persistent assignee columns: {pers_cols}")
    pid_col = next(c for c in pers_cols if c.lower() == 'patent_id')
    seq_col = next(c for c in pers_cols if c.lower() == 'assignee_sequence')
    version_cols = sorted([c for c in pers_cols if c.startswith("disamb_assignee_id_")], reverse=True)
    if not version_cols: raise KeyError("No disamb_assignee_id column.")
    cid_col = version_cols[0]
    print(f"  Using company_id: {cid_col}")
    pers_chunks = chunked_reader("g_persistent_assignee.tsv", chunksize=200000, dtype=object,
                                 usecols=[pid_col, seq_col, cid_col])
    first = True
    for chunk in pers_chunks:
        chunk = chunk.rename(columns={pid_col: "patent_id", seq_col: "assignee_sequence", cid_col: "company_id"})
        if first:
            chunk.to_sql("pers_ass_staging", conn, if_exists='replace', index=False)
            first = False
        else:
            chunk.to_sql("pers_ass_staging", conn, if_exists='append', index=False)

    if nd_ass_exists:
        nd_cols = get_raw_columns("g_assignee_not_disambiguated.tsv")
        print(f"  Not-disambiguated assignee columns: {nd_cols}")
        nd_pid_col = next(c for c in nd_cols if c.lower() == 'patent_id')
        nd_seq_col = next(c for c in nd_cols if c.lower() == 'assignee_sequence')
        # Look for organization or raw_assignee_organization
        nd_org_col = next((c for c in nd_cols if c.lower() in ('organization', 'raw_assignee_organization', 'name')), None)
        if nd_org_col:
            print(f"  Using organization column: {nd_org_col}")
            nd_chunks = chunked_reader("g_assignee_not_disambiguated.tsv", chunksize=200000, dtype=object,
                                       usecols=[nd_pid_col, nd_seq_col, nd_org_col])
            first = True
            for chunk in nd_chunks:
                chunk = chunk.rename(columns={nd_pid_col: "patent_id", nd_seq_col: "assignee_sequence", nd_org_col: "organization"})
                if first:
                    chunk.to_sql("notdisamb_ass_staging", conn, if_exists='replace', index=False)
                    first = False
                else:
                    chunk.to_sql("notdisamb_ass_staging", conn, if_exists='append', index=False)
            conn.execute("""
                CREATE TEMP TABLE _company_names AS
                SELECT p.company_id, n.organization
                FROM pers_ass_staging p
                JOIN notdisamb_ass_staging n USING(patent_id, assignee_sequence)
                WHERE n.organization != ''
            """)
            conn.execute("DROP TABLE notdisamb_ass_staging")
            has_org = True
        else:
            print("  No organization column found; company names will be Unknown.")
            has_org = False
    else:
        print("  g_assignee_not_disambiguated.tsv missing; company names will be Unknown.")
        has_org = False

    conn.execute("DROP TABLE IF EXISTS companies")
    if has_org:
        conn.execute("""
            CREATE TABLE companies AS
            SELECT DISTINCT p.company_id,
                   COALESCE(cn.organization, 'Unknown Assignee') AS name
            FROM (SELECT DISTINCT company_id FROM pers_ass_staging) p
            LEFT JOIN _company_names cn USING(company_id)
        """)
        conn.execute("DROP TABLE _company_names")
    else:
        conn.execute("""
            CREATE TABLE companies AS
            SELECT DISTINCT company_id, 'Unknown Assignee' AS name
            FROM pers_ass_staging
        """)
    conn.execute("DROP TABLE pers_ass_staging")

    comp_csv = CLEAN_DATA_DIR / "clean_companies.csv"
    first_csv = True
    for chunk in pd.read_sql("SELECT * FROM companies", conn, chunksize=50000):
        if first_csv:
            chunk.to_csv(comp_csv, index=False, mode='w', header=True)
            first_csv = False
        else:
            chunk.to_csv(comp_csv, index=False, mode='a', header=False)

    # 7. Relationships
    print("7/7 Processing relationships...")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_patents_id ON patents(patent_id)")
    conn.execute("DROP TABLE IF EXISTS _patent_ids")
    conn.execute("CREATE TEMP TABLE _patent_ids (patent_id TEXT PRIMARY KEY)")
    conn.execute("INSERT INTO _patent_ids SELECT patent_id FROM patents")

    def stream_rel_to_db(file, pid_col, rel_col, target_table, rename_to="rel_id"):
        conn.execute(f"DROP TABLE IF EXISTS {target_table}")
        chunks = chunked_reader(file, chunksize=200000, dtype=object, usecols=[pid_col, rel_col])
        first = True
        for chunk in chunks:
            chunk = chunk.rename(columns={pid_col: "patent_id", rel_col: rename_to})
            chunk[rename_to] = chunk[rename_to].fillna("UNKNOWN")
            staging = "_chunk_staging"
            chunk.to_sql(staging, conn, if_exists='replace', index=False)
            if first:
                conn.execute(f"""
                    CREATE TABLE {target_table} AS
                    SELECT * FROM {staging}
                    WHERE patent_id IN (SELECT patent_id FROM _patent_ids)
                """)
                first = False
            else:
                conn.execute(f"""
                    INSERT INTO {target_table}
                    SELECT * FROM {staging}
                    WHERE patent_id IN (SELECT patent_id FROM _patent_ids)
                """)
            conn.execute(f"DROP TABLE {staging}")

    if disambig_exists:
        pid_inv = next(c for c in inv_cols if c.lower() == 'patent_id')
        inv_id = next(c for c in inv_cols if c.lower() == 'inventor_id')
        stream_rel_to_db("g_inventor_disambiguated.tsv", pid_inv, inv_id, "patent_inventor")
    else:
        print("  Skipping patent_inventor.")

    if pers_ass_exists:
        pid_ass = next(c for c in pers_cols if c.lower() == 'patent_id')
        stream_rel_to_db("g_persistent_assignee.tsv", pid_ass, cid_col, "patent_assignee", rename_to="company_id")
    else:
        print("  Skipping patent_assignee.")

    conn.execute("DROP TABLE IF EXISTS _patent_ids")
    conn.commit()
    conn.close()
    print("Data ingested and cleaned successfully.")

if __name__ == "__main__":
    clean_data()