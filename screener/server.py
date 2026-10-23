"""FastAPI app. Railway runs it with uvicorn; Vercel imports it from api/index.py."""

import hmac
import os
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path

from fastapi import Depends, FastAPI, Header, HTTPException, Request
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from . import data as md
from . import db
from .config import THRESHOLD_META, Thresholds
from .evaluate import CRITERIA_LABELS, evaluate_symbol, split_result
from .phases import PHASE_HELP, PHASES
from .tickers import dedupe, normalize, note_date, parse_file

MAX_TICKERS_PER_REQUEST = 25
WORKERS = int(os.environ.get("FETCH_WORKERS", "4"))
PUBLIC_DIR = Path(__file__).resolve().parent.parent / "public"

app = FastAPI(title="BPD Watchlist", docs_url=None, redoc_url=None)
app.add_middleware(GZipMiddleware, minimum_size=2000)


def require_password(x_app_password: str | None = Header(default=None)):
    expected = os.environ.get("APP_PASSWORD")
    if expected and not hmac.compare_digest((x_app_password or "").encode(), expected.encode()):
        raise HTTPException(status_code=401, detail="Wrong or missing password")


auth = [Depends(require_password)]


def current_thresholds() -> Thresholds:
    return Thresholds.from_overrides(db.get_setting("thresholds", {}))


def _symbols(raw: list[str]) -> list[str]:
    return dedupe([s for s in (normalize(t) for t in raw) if s])


# ---------------------------------------------------------------- meta

@app.get("/api/health")
def health():
    return {"ok": True}


@app.get("/api/config")
def config():
    defaults = Thresholds().to_dict()
    return {
        "auth_required": bool(os.environ.get("APP_PASSWORD")),
        "persistent": db.PERSISTENT,
        "max_tickers_per_request": MAX_TICKERS_PER_REQUEST,
        "criteria": CRITERIA_LABELS,
        "phases": [{"key": k, "help": PHASE_HELP[k]} for k in PHASES],
        "thresholds": [
            {"group": group, "fields": [{"key": k, "label": label, "unit": unit, "default": defaults[k]} for k, label, unit in items]}
            for group, items in THRESHOLD_META
        ],
    }


@app.get("/api/auth-check", dependencies=auth)
def auth_check():
    return {"ok": True}


# ---------------------------------------------------------------- state

@app.get("/api/state", dependencies=auth)
def state():
    return {"lists": db.all_lists(), "tickers": db.ticker_rows(), "thresholds": db.get_setting("thresholds", {})}


@app.get("/api/tickers/{symbol}", dependencies=auth)
def ticker(symbol: str):
    row = db.ticker_detail(normalize(symbol) or symbol)
    if not row:
        raise HTTPException(status_code=404, detail="Unknown ticker")
    return row


class TickerPatch(BaseModel):
    note: str | None = None
    tags: str | None = None
    manual_phase: str | None = Field(default=None)
    clear_manual_phase: bool = False


@app.patch("/api/tickers/{symbol}", dependencies=auth)
def patch_ticker(symbol: str, body: TickerPatch):
    fields = body.model_dump(exclude_unset=True)
    if fields.pop("clear_manual_phase", False):
        fields["manual_phase"] = None
    if fields.get("manual_phase") and fields["manual_phase"] not in PHASES:
        raise HTTPException(status_code=400, detail="Unknown phase")
    db.update_ticker(symbol, **fields)
    return _row(symbol)


class SymbolsBody(BaseModel):
    symbols: list[str]


@app.post("/api/tickers/delete", dependencies=auth)
def delete_tickers(body: SymbolsBody):
    db.delete_tickers(_symbols(body.symbols))
    return {"ok": True}


# ---------------------------------------------------------------- lists

class ListBody(BaseModel):
    name: str


@app.post("/api/lists", dependencies=auth)
def create_list(body: ListBody):
    name = body.name.strip()
    if not name:
        raise HTTPException(status_code=400, detail="Name required")
    return {"id": db.create_list(name), "lists": db.all_lists()}


@app.patch("/api/lists/{list_id}", dependencies=auth)
def rename_list(list_id: int, body: ListBody):
    db.rename_list(list_id, body.name.strip())
    return {"lists": db.all_lists()}


@app.delete("/api/lists/{list_id}", dependencies=auth)
def delete_list(list_id: int):
    db.delete_list(list_id)
    return {"lists": db.all_lists()}


@app.post("/api/lists/{list_id}/add", dependencies=auth)
def add_to_list(list_id: int, body: SymbolsBody):
    symbols = _symbols(body.symbols)
    created = db.add_to_list(list_id, symbols)
    return {"added": symbols, "new": created, "lists": db.all_lists()}


@app.post("/api/lists/{list_id}/remove", dependencies=auth)
def remove_from_list(list_id: int, body: SymbolsBody):
    db.remove_from_list(list_id, _symbols(body.symbols))
    return {"lists": db.all_lists()}


# ---------------------------------------------------------------- import

@app.post("/api/import/parse", dependencies=auth)
async def import_parse(request: Request, filename: str = "upload.xlsx"):
    content = await request.body()
    if len(content) > 4 * 1024 * 1024:
        raise HTTPException(status_code=413, detail="File too large (max 4 MB)")
    try:
        return {"sheets": parse_file(filename, content)}
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Could not read file: {e}")


class ImportSheet(BaseModel):
    list_name: str | None = None     # add the sheet's tickers to this list
    phase: str | None = None         # sheet holds hand-labelled phases
    tickers: list[str]
    meta: dict[str, dict] = Field(default_factory=dict)


class ImportBody(BaseModel):
    sheets: list[ImportSheet]


@app.post("/api/import/commit", dependencies=auth)
def import_commit(body: ImportBody):
    added = set()
    for sh in body.sheets:
        symbols = _symbols(sh.tickers)
        meta = {normalize(k): {f: v for f, v in m.items() if f in ("name", "industry", "tags", "note")} for k, m in sh.meta.items()}
        list_id = db.create_list(sh.list_name.strip()) if sh.list_name and sh.list_name.strip() else None
        db.add_to_list(list_id, symbols, meta)
        if sh.phase in PHASES:
            for s in symbols:
                when = note_date((meta.get(s) or {}).get("note"))
                db.update_ticker(s, manual_phase=sh.phase, manual_phase_source="import", manual_phase_at=when)
        added.update(symbols)
    return {"tickers": len(added), "lists": db.all_lists()}


# ---------------------------------------------------------------- settings

@app.put("/api/settings/thresholds", dependencies=auth)
def save_thresholds(overrides: dict):
    defaults = Thresholds().to_dict()
    clean = {k: v for k, v in overrides.items() if k in defaults and v is not None and v != defaults[k]}
    db.set_setting("thresholds", clean)
    return {"thresholds": clean}


@app.get("/api/labels", dependencies=auth)
def labels():
    """Every manual phase call with the auto phase at that time - the calibration set."""
    return {"labels": db.phase_label_rows()}


# ---------------------------------------------------------------- evaluation

@app.post("/api/evaluate", dependencies=auth)
def evaluate(body: SymbolsBody):
    symbols = _symbols(body.symbols)
    if not symbols:
        raise HTTPException(status_code=400, detail="No valid tickers")
    if len(symbols) > MAX_TICKERS_PER_REQUEST:
        raise HTTPException(status_code=400, detail=f"Send at most {MAX_TICKERS_PER_REQUEST} tickers per request")
    th = current_thresholds()
    try:
        md.get_benchmark()
    except md.DataError as e:
        raise HTTPException(status_code=502, detail=f"Could not load S&P 500 data: {e}")
    with ThreadPoolExecutor(max_workers=WORKERS) as pool:
        results = list(pool.map(lambda s: _safe_evaluate(s, th), symbols))
    rows = []
    for r in results:
        summary, detail = split_result(r)
        db.save_evaluation(r["symbol"], summary, detail)
        rows.append(_row(r["symbol"]))
    return {"asof": datetime.now(timezone.utc).isoformat(timespec="seconds"), "tickers": rows}


def _row(symbol: str) -> dict:
    row = db.ticker_detail(symbol)
    if row:
        row.pop("detail", None)
    return row


def _safe_evaluate(symbol: str, th: Thresholds) -> dict:
    try:
        return evaluate_symbol(symbol, th)
    except Exception as e:  # one bad ticker must not sink the batch
        return {"symbol": symbol, "verdict": "error", "error": f"{type(e).__name__}: {e}", "criteria": {}, "score": None}


# Static UI. On Vercel the CDN serves public/ directly and only /api/* reaches Python.
if PUBLIC_DIR.is_dir():
    @app.get("/", include_in_schema=False)
    def index():
        return FileResponse(PUBLIC_DIR / "index.html")

    app.mount("/", StaticFiles(directory=PUBLIC_DIR), name="static")
