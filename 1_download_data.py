# This script downloads the raw files from the PatentsView S3 bucket.
# It expects the URLs in config.BULK_FILES.
# Run this ONLY if you haven't already placed the .tsv.zip files in data/raw/ manually.
import os, sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
import requests
from patent_pipeline.scripts.config import RAW_DATA_DIR, BULK_FILES

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
