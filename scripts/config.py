from pathlib import Path

BASE_DIR = Path("D:/Patents/patent_pipeline")

RAW_DATA_DIR = BASE_DIR / "data" / "raw"
DB_PATH = BASE_DIR / "output" / "patents.db"
CLEAN_DATA_DIR = BASE_DIR / "clean_data"
OUTPUT_DIR = BASE_DIR / "output"