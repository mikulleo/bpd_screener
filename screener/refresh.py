"""Re-evaluate every ticker in the database: `python -m screener.refresh`.

Run it on a schedule (e.g. a Railway cron service after the US close) so the watchlist
is fresh in the morning without anyone pressing Re-evaluate.
"""

import sys
import time
from concurrent.futures import ThreadPoolExecutor

from . import db
from .evaluate import split_result
from .server import WORKERS, _safe_evaluate, current_thresholds


def main() -> int:
    symbols = [r["symbol"] for r in db.ticker_rows()]
    th = current_thresholds()
    start = time.time()
    failed = 0
    with ThreadPoolExecutor(max_workers=WORKERS) as pool:
        for result in pool.map(lambda s: _safe_evaluate(s, th), symbols):
            summary, detail = split_result(result)
            db.save_evaluation(result["symbol"], summary, detail)
            failed += result.get("verdict") == "error"
    print(f"Re-evaluated {len(symbols)} tickers in {time.time() - start:.0f}s ({failed} errors)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
