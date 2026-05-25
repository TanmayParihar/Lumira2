#!/usr/bin/env python3
"""
Initialise the database: create PostGIS tables, load India districts,
and optionally seed sample assets.

Usage:
    python scripts/init_db.py [--seed-assets]
"""
from __future__ import annotations

import asyncio
import sys

sys.path.insert(0, ".")  # run from project root


async def main(seed_assets: bool = False):
    import structlog

    logger = structlog.get_logger("init_db")

    print("━" * 60)
    print("  Lumira — Database Initialisation")
    print("━" * 60)

    # 1. Create tables
    print("\n[1/3] Creating PostgreSQL tables with PostGIS extensions...")
    from storage.database import create_all_tables

    try:
        await create_all_tables()
        print("      ✓ Tables created")
    except Exception as e:
        print(f"      ✗ FAILED: {e}")
        raise

    # 2. Load India districts
    print("\n[2/3] Loading India district reference data...")
    try:
        from scripts.load_india_districts import load_districts
        count = await load_districts()
        print(f"      ✓ {count} districts loaded")
    except Exception as e:
        print(f"      ⚠  Districts not loaded (non-fatal): {e}")

    # 3. MinIO + OpenSearch
    print("\n[3/3] Initialising MinIO buckets and OpenSearch index...")
    try:
        from storage.minio_client import ensure_buckets
        ensure_buckets()
        print("      ✓ MinIO buckets ready")
    except Exception as e:
        print(f"      ⚠  MinIO unavailable: {e}")

    try:
        from storage.opensearch_client import ensure_index
        await ensure_index()
        print("      ✓ OpenSearch index ready")
    except Exception as e:
        print(f"      ⚠  OpenSearch unavailable: {e}")

    # Optional: seed assets
    if seed_assets:
        print("\n[+] Seeding sample assets...")
        from scripts.seed_assets import seed
        n = await seed()
        print(f"    ✓ {n} assets seeded")

    # Close async clients cleanly
    try:
        from storage.opensearch_client import close as os_close
        await os_close()
    except Exception:
        pass

    print("\n✅  Initialisation complete.\n")


if __name__ == "__main__":
    seed = "--seed-assets" in sys.argv
    asyncio.run(main(seed_assets=seed))
