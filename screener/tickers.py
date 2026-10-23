"""Ticker parsing from pasted text and from Excel/CSV watchlists."""

import csv
import io
import re
from datetime import datetime, timezone

import openpyxl

_CLASS_SHARE = re.compile(r"^([A-Z]{1,5})[/.]([A-Z])$")  # BRK/B, BRK.B -> BRK-B
_VALID = re.compile(r"^\^?[A-Z0-9][A-Z0-9.\-=]{0,14}$")
_HEADERS = {"TICKER", "SYMBOL", "SYMBOLS", "TICKERS"}


def normalize(token: str) -> str | None:
    t = token.strip().upper().lstrip("$")
    if not t:
        return None
    m = _CLASS_SHARE.match(t)
    if m:
        return f"{m.group(1)}-{m.group(2)}"
    return t if _VALID.match(t) else None


def split_cell(text: str) -> list[str]:
    """One cell or line may hold several tickers ("NWSA / NWS", "AAPL, MSFT")."""
    out = []
    for tok in re.split(r"[\s,;|]+", text.strip()):
        # "BRK/B" is one class-share symbol; "NWSA/NWS" is two tickers
        out.extend([tok] if _CLASS_SHARE.match(tok.upper()) else [p for p in tok.split("/") if p])
    return out


def parse_text(text: str) -> list[str]:
    out = []
    for line in text.splitlines():
        for tok in split_cell(line):
            n = normalize(tok)
            if n and n not in _HEADERS:
                out.append(n)
    return dedupe(out)


def dedupe(items: list[str]) -> list[str]:
    seen, out = set(), []
    for x in items:
        if x not in seen:
            seen.add(x)
            out.append(x)
    return out


def _cell_tickers(value) -> list[str]:
    """Tickers in a spreadsheet cell, or [] if the cell doesn't look like tickers.

    Watchlists hold notes and industry names next to the symbols, so a cell only
    counts when it is written in capitals and every part of it is a valid symbol.
    """
    if not isinstance(value, str):
        return []
    text = value.replace("\xa0", " ").strip()
    if not text or len(text) > 24 or text != text.upper():
        return []
    parts = split_cell(text)
    if len(parts) > 3:
        return []
    out = [normalize(p) for p in parts]
    if not all(out) or any(o in _HEADERS for o in out):
        return []
    return out


_META_HEADERS = {
    "name": {"COMPANY", "NAME", "NÁZEV", "NAZEV"},
    "industry": {"INDUSTRY", "SECTOR", "ODVĚTVÍ", "ODVETVI"},
    "tags": {"SIGNAL", "TAG", "TAGS"},
    "note": {"LEOS REVIEW", "REVIEW", "NOTE", "NOTES", "COMMENT", "POZNÁMKA", "POZNAMKA"},
}


def _clean(v) -> str:
    return str(v).replace("\xa0", " ").strip() if v is not None else ""


def _column_tickers(rows: list[tuple]) -> tuple[list[str], dict[str, dict]]:
    """Use the 'Ticker'/'Symbol' column when there is one (and pick up name / industry /
    signal / review columns next to it); otherwise every column in which most filled
    cells look like tickers (handles side-by-side weekly lists)."""
    for r_i, row in enumerate(rows[:5]):
        for c_i, v in enumerate(row):
            if isinstance(v, str) and v.strip().upper() in _HEADERS:
                return _with_header(rows, r_i, c_i)
    width = max((len(r) for r in rows), default=0)
    out = []
    for c in range(width):
        cells = [r[c] for r in rows if c < len(r) and r[c] not in (None, "")]
        hits = [_cell_tickers(v) for v in cells]
        good = sum(1 for h in hits if h)
        if good >= 3 and good >= 0.6 * len(cells):
            out.extend(t for h in hits for t in h)
    return dedupe(out), {}


def _with_header(rows, header_row: int, ticker_col: int):
    header = [_clean(h).upper() for h in rows[header_row]]
    cols: dict[str, list[int]] = {}
    for i, h in enumerate(header):
        for field, names in _META_HEADERS.items():
            if h in names:
                cols.setdefault(field, []).append(i)
    # an unlabelled column right after the review column holds free-text comments
    for i in list(cols.get("note", [])):
        if i + 1 < len(header) and not header[i + 1]:
            cols["note"].append(i + 1)
    symbols, meta = [], {}
    for r in rows[header_row + 1:]:
        if ticker_col >= len(r):
            continue
        found = _cell_tickers(r[ticker_col])
        if not found:
            continue
        m = {}
        for field, idx in cols.items():
            parts = [_clean(r[i]) for i in idx if i < len(r) and _clean(r[i])]
            if parts:
                m[field] = " — ".join(parts)
        for t in found:
            symbols.append(t)
            meta.setdefault(t, m)
    return dedupe(symbols), meta


_PHASE_NAMES = [
    ("2B+ RS+", "2b+RS+"), ("2B+RS+", "2b+RS+"), ("2B+ RS-", "2b+RS-"), ("2B+RS-", "2b+RS-"),
    ("2B AB", "2b-AB"), ("2B-AB", "2b-AB"), ("2A+", "2a+"),
]


def guess_phase(sheet_name: str) -> str | None:
    """Sheets named after a phase ("2b AB", "Minervini 2b+ RS+", "3") hold hand-labelled phases."""
    n = sheet_name.strip().upper()
    for key, phase in _PHASE_NAMES:
        if key in n:
            return phase
    return {"2B": "2b", "2A": "2a", "1": "1", "3": "3", "4": "4"}.get(n)


def parse_file(filename: str, content: bytes) -> list[dict]:
    """Return [{name, tickers, meta, phase}] per sheet (one entry for CSV/TXT)."""
    lower = filename.lower()
    if lower.endswith((".xlsx", ".xlsm")):
        wb = openpyxl.load_workbook(io.BytesIO(content), read_only=True, data_only=True)
        sheets = []
        for ws in wb.worksheets:
            rows = [tuple(r) for r in ws.iter_rows(values_only=True)]
            symbols, meta = _column_tickers(rows)
            if symbols:
                sheets.append({"name": ws.title, "tickers": symbols, "meta": meta, "phase": guess_phase(ws.title)})
        return sheets
    text = content.decode("utf-8-sig", errors="replace")
    if lower.endswith(".csv"):
        rows = [tuple(r) for r in csv.reader(io.StringIO(text))]
        symbols, meta = _column_tickers(rows)
        return [{"name": filename, "tickers": symbols, "meta": meta, "phase": None}]
    return [{"name": filename, "tickers": parse_text(text), "meta": {}, "phase": None}]


_NOTE_DATE = re.compile(r"(\d{1,2})/(\d{1,2})\s*[A-Z]{2}\b")


def note_date(note: str | None, today: datetime | None = None) -> datetime | None:
    """Latest "d/m XX:" date in a note (e.g. "20/7 LM: moved to 2b"), as a UTC datetime.
    The year is the current one unless that would put the date in the future."""
    if not note:
        return None
    today = today or datetime.now(timezone.utc)
    found = []
    for d, m in _NOTE_DATE.findall(note):
        try:
            dt = datetime(today.year, int(m), int(d), tzinfo=timezone.utc)
        except ValueError:
            continue
        if dt > today:
            dt = dt.replace(year=today.year - 1)
        found.append(dt)
    return max(found) if found else None
