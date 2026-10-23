"""
Step 5: Import Embeddings to Database
=========================================
Reads scripts/output/sentences_with_embeddings.json and imports
all records (text + embeddings) to Supabase, replacing any
existing data.

After this step, SEMANTIC SEARCH will light up green on your site!

Usage:
    python scripts/05_update_embeddings.py

Input:
    scripts/output/sentences_with_embeddings.json  — from Step 4

Prerequisites:
    - config.public.json with Supabase URL and anon key
    - config.secret.json with Supabase service key, access token, and project ref
    - Database schema created (Step 1)
    - Embeddings generated (Step 4)
"""

import json
import os
import sys
import time

import requests
from supabase import create_client
from tqdm import tqdm


INPUT_FILE = os.path.join('scripts', 'output', 'sentences_with_embeddings.json')
BATCH_SIZE = 100
MAX_RETRIES = 5


def load_config():
    with open('config.public.json', 'r') as f:
        public_config = json.load(f)
    with open('config.secret.json', 'r') as f:
        secrets = json.load(f)
    return public_config, secrets


def main():
    if not os.path.exists(INPUT_FILE):
        print(f"❌ {INPUT_FILE} not found. Run scripts/04_embed_data.py first.")
        sys.exit(1)

    # Load embedded records
    print("Loading embeddings data...")
    with open(INPUT_FILE, 'r', encoding='utf-8') as f:
        records = json.load(f)

    # Verify embeddings are present
    with_embeddings = sum(1 for r in records if r.get('embedding'))
    without_embeddings = len(records) - with_embeddings

    print(f"   Loaded {len(records):,} records")
    print(f"   With embeddings:    {with_embeddings:,}")
    if without_embeddings:
        print(f"   Without embeddings: {without_embeddings:,} (will be imported without)")

    # Connect to Supabase
    public_config, secrets = load_config()
    client = create_client(public_config['SUPABASE_URL'], secrets['SUPABASE_SERVICE_KEY'])

    # Truncate existing data and re-import everything
    print("\n" + "=" * 60)
    print("Replacing database contents (truncate + re-import)")
    print("=" * 60)

    # Use SQL TRUNCATE via the Management API — a row-by-row DELETE through the
    # REST API hits the statement timeout once the table holds embeddings,
    # which would leave old rows behind and duplicate everything.
    url = f"https://api.supabase.com/v1/projects/{secrets['SUPABASE_PROJECT_REF']}/database/query"
    headers = {
        "Authorization": f"Bearer {secrets['SUPABASE_ACCESS_TOKEN']}",
        "Content-Type": "application/json"
    }
    resp = requests.post(url, headers=headers, json={"query": "TRUNCATE TABLE sentence_embeddings;"})
    if resp.status_code in (200, 201):
        print("   ✅ Table truncated.")
    else:
        print(f"   ❌ Could not truncate table: {resp.status_code}")
        print(resp.text[:500])
        print("   Aborting so the import doesn't create duplicate rows.")
        sys.exit(1)

    # Batch import (much faster than individual updates!)
    print(f"\n   Importing {len(records):,} records in batches of {BATCH_SIZE}...\n")

    success = 0
    errors = 0

    for i in tqdm(range(0, len(records), BATCH_SIZE), desc="Importing"):
        batch = records[i:i + BATCH_SIZE]
        # Retry with backoff — large vector inserts sometimes hit transient timeouts
        for attempt in range(1, MAX_RETRIES + 1):
            try:
                client.table('sentence_embeddings').insert(batch).execute()
                success += len(batch)
                break
            except Exception as e:
                if attempt == MAX_RETRIES:
                    print(f"\nError at batch {i // BATCH_SIZE} (gave up after {MAX_RETRIES} attempts): {e}")
                    errors += len(batch)
                else:
                    time.sleep(2 ** attempt)
        time.sleep(0.1)

    print(f"\n✅ Import complete!")
    print(f"   Success: {success:,}")
    if errors:
        print(f"   Errors:  {errors:,}")

    # Verify
    result = client.table('sentence_embeddings').select('id', count='exact').limit(1).execute()
    total = result.count or 0

    embedded_result = client.table('sentence_embeddings') \
        .select('id', count='exact') \
        .not_.is_('embedding', 'null') \
        .limit(1).execute()
    with_emb = embedded_result.count or 0

    print(f"\n   Total rows:          {total:,}")
    print(f"   Rows with embeddings: {with_emb:,}")

    print(f"\n🎉 Semantic Search is now ready!")
    print(f"   Refresh your site — the 🧠 Semantic Search panel should turn GREEN.")
    print(f"\nNext: Deploy edge functions to light up 🤖 RAG!")


if __name__ == '__main__':
    main()
