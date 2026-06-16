"""Test the full crawl pipeline to debug data loading issues."""
import sys
import os
sys.path.insert(0, 'backend')
os.environ['SHEET_ID'] = '16nhZJyAiCX7xzBujieAF1AOas6bgh2-4X6ePQixWHJE'
os.environ['SHEET_GID'] = '0'

import asyncio
import logging

logging.basicConfig(level=logging.DEBUG, format="%(name)s %(levelname)s: %(message)s")

from database import init_database, get_snapshot_count, get_overview_kpi
from crawler import crawl_backlog_data, crawler_state
from config import config

async def test():
    print("=" * 60)
    print("  CRAWL PIPELINE TEST")
    print("=" * 60)
    
    print(f"\n[CONFIG]")
    print(f"  sheet_id: {config.sheet_id}")
    print(f"  sheet_gid: {config.sheet_gid}")
    print(f"  crawl_interval: {config.crawl_interval} min")
    
    print(f"\n[1] Initializing database...")
    await init_database()
    
    c1 = await get_snapshot_count()
    print(f"  Records before crawl: {c1}")
    
    print(f"\n[2] Running crawl...")
    await crawl_backlog_data()
    
    print(f"\n[3] Results:")
    print(f"  last_error: {crawler_state.last_error}")
    print(f"  last_records_count: {crawler_state.last_records_count}")
    print(f"  consecutive_errors: {crawler_state.consecutive_errors}")
    print(f"  last_run_at: {crawler_state.last_run_at}")
    
    c2 = await get_snapshot_count()
    print(f"  Records after crawl: {c2}")
    
    print(f"\n[4] Testing API overview...")
    kpi = await get_overview_kpi()
    print(f"  KPI data: {kpi}")
    
    print(f"\n{'=' * 60}")
    if c2 > 0 and not crawler_state.last_error:
        print("  [OK] Crawl pipeline is working correctly!")
    else:
        print("  [FAIL] Something is wrong with the crawl pipeline")
    print(f"{'=' * 60}")

asyncio.run(test())
