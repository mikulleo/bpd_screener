"""Persistence: watchlists, tickers with their latest evaluation, team settings.

Uses Postgres when DATABASE_URL is set (Railway Postgres, Neon, ...), otherwise a local
SQLite file. JSON payloads are stored as text so both backends behave the same.
"""

import json
import os
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy import (
    Column,
    DateTime,
    ForeignKey,
    Integer,
    MetaData,
    String,
    Table,
    Text,
    create_engine,
    delete,
    func,
    insert,
    select,
    update,
)

metadata = MetaData()

lists = Table(
    "lists", metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("name", String(120), nullable=False, unique=True),
    Column("position", Integer, nullable=False, default=0),
    Column("created_at", DateTime(timezone=True), nullable=False),
)

tickers = Table(
    "tickers", metadata,
    Column("symbol", String(24), primary_key=True),
    Column("name", String(200)),
    Column("industry", String(200)),
    Column("tags", String(200)),
    Column("note", Text),
    Column("manual_phase", String(12)),
    Column("manual_phase_at", DateTime(timezone=True)),
    Column("manual_phase_source", String(20)),  # "user" or "import"
    Column("added_at", DateTime(timezone=True), nullable=False),
    Column("evaluated_at", DateTime(timezone=True)),
    Column("phase", String(12)),
    Column("prev_phase", String(12)),
    Column("phase_changed_at", DateTime(timezone=True)),
    Column("summary", Text),  # compact evaluation used by the table / board
    Column("detail", Text),   # charts and full criterion details for the drawer
)

list_items = Table(
    "list_items", metadata,
    Column("list_id", Integer, ForeignKey("lists.id", ondelete="CASCADE"), primary_key=True),
    Column("symbol", String(24), ForeignKey("tickers.symbol", ondelete="CASCADE"), primary_key=True),
    Column("added_at", DateTime(timezone=True), nullable=False),
)

# Every manual phase call, with the auto phase at that moment: the calibration set.
phase_labels = Table(
    "phase_labels", metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("symbol", String(24), nullable=False),
    Column("phase", String(12)),
    Column("auto_phase", String(12)),
    Column("source", String(20), nullable=False),
    Column("labelled_at", DateTime(timezone=True), nullable=False),
)

settings = Table(
    "settings", metadata,
    Column("key", String(64), primary_key=True),
    Column("value", Text, nullable=False),
)


def _database_url() -> tuple[str, bool]:
    url = os.environ.get("DATABASE_URL")
    if url:
        if url.startswith("postgres://"):
            url = "postgresql://" + url[len("postgres://"):]
        if url.startswith("postgresql://"):
            url = "postgresql+psycopg://" + url[len("postgresql://"):]
        return url, True
    # Vercel's filesystem is read-only except /tmp, and /tmp does not survive.
    base = Path("/tmp") if os.environ.get("VERCEL") else Path(__file__).resolve().parent.parent / "data"
    base.mkdir(parents=True, exist_ok=True)
    return f"sqlite:///{base / 'bpd.db'}", not os.environ.get("VERCEL")


DATABASE_URL, PERSISTENT = _database_url()
engine = create_engine(DATABASE_URL, pool_pre_ping=True, future=True)
metadata.create_all(engine)


def now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(dt: datetime | None) -> str | None:
    if dt is None:
        return None
    if dt.tzinfo is None:  # SQLite returns naive datetimes
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.isoformat(timespec="seconds")


# ---------------------------------------------------------------- settings

def get_setting(key: str, default=None):
    with engine.connect() as c:
        row = c.execute(select(settings.c.value).where(settings.c.key == key)).first()
    return json.loads(row[0]) if row else default


def set_setting(key: str, value) -> None:
    with engine.begin() as c:
        c.execute(delete(settings).where(settings.c.key == key))
        c.execute(insert(settings).values(key=key, value=json.dumps(value)))


# ---------------------------------------------------------------- lists

def all_lists() -> list[dict]:
    with engine.connect() as c:
        counts = dict(c.execute(select(list_items.c.list_id, func.count()).group_by(list_items.c.list_id)).all())
        rows = c.execute(select(lists).order_by(lists.c.position, lists.c.id)).mappings().all()
    return [{"id": r["id"], "name": r["name"], "count": counts.get(r["id"], 0)} for r in rows]


def create_list(name: str) -> int:
    with engine.begin() as c:
        existing = c.execute(select(lists.c.id).where(lists.c.name == name)).first()
        if existing:
            return existing[0]
        pos = c.execute(select(func.coalesce(func.max(lists.c.position), 0))).scalar() + 1
        return c.execute(insert(lists).values(name=name, position=pos, created_at=now())).inserted_primary_key[0]


def rename_list(list_id: int, name: str) -> None:
    with engine.begin() as c:
        c.execute(update(lists).where(lists.c.id == list_id).values(name=name))


def delete_list(list_id: int) -> None:
    with engine.begin() as c:
        c.execute(delete(list_items).where(list_items.c.list_id == list_id))
        c.execute(delete(lists).where(lists.c.id == list_id))


def add_to_list(list_id: int | None, symbols: list[str], meta: dict[str, dict] | None = None) -> list[str]:
    """Ensure tickers exist (filling empty metadata) and, if list_id is given, add them to it.
    Returns symbols that were new to the database."""
    meta = meta or {}
    created = []
    with engine.begin() as c:
        have = set(c.execute(select(tickers.c.symbol).where(tickers.c.symbol.in_(symbols))).scalars())
        for s in symbols:
            m = {k: v for k, v in (meta.get(s) or {}).items() if v}
            if s not in have:
                c.execute(insert(tickers).values(symbol=s, added_at=now(), **m))
                created.append(s)
            elif m:
                row = c.execute(select(tickers).where(tickers.c.symbol == s)).mappings().first()
                fill = {k: v for k, v in m.items() if not row[k]}
                if fill:
                    c.execute(update(tickers).where(tickers.c.symbol == s).values(**fill))
        if list_id is not None:
            in_list = set(c.execute(select(list_items.c.symbol).where(list_items.c.list_id == list_id)).scalars())
            for s in symbols:
                if s not in in_list:
                    c.execute(insert(list_items).values(list_id=list_id, symbol=s, added_at=now()))
    return created


def remove_from_list(list_id: int, symbols: list[str]) -> None:
    with engine.begin() as c:
        c.execute(delete(list_items).where(list_items.c.list_id == list_id, list_items.c.symbol.in_(symbols)))


def delete_tickers(symbols: list[str]) -> None:
    with engine.begin() as c:
        c.execute(delete(list_items).where(list_items.c.symbol.in_(symbols)))
        c.execute(delete(tickers).where(tickers.c.symbol.in_(symbols)))


# ---------------------------------------------------------------- tickers

def ticker_rows() -> list[dict]:
    """Every ticker with its list memberships and compact evaluation."""
    with engine.connect() as c:
        memberships: dict[str, list[int]] = {}
        for lid, sym in c.execute(select(list_items.c.list_id, list_items.c.symbol)):
            memberships.setdefault(sym, []).append(lid)
        cols = [col for col in tickers.c if col.name != "detail"]
        rows = c.execute(select(*cols).order_by(tickers.c.symbol)).mappings().all()
    return [_row_out(r, memberships.get(r["symbol"], [])) for r in rows]


def ticker_detail(symbol: str) -> dict | None:
    with engine.connect() as c:
        r = c.execute(select(tickers).where(tickers.c.symbol == symbol)).mappings().first()
        if not r:
            return None
        lids = list(c.execute(select(list_items.c.list_id).where(list_items.c.symbol == symbol)).scalars())
    out = _row_out(r, lids)
    out["detail"] = json.loads(r["detail"]) if r["detail"] else None
    return out


def _row_out(r, list_ids) -> dict:
    return {
        "symbol": r["symbol"],
        "name": r["name"],
        "industry": r["industry"],
        "tags": r["tags"],
        "note": r["note"],
        "manual_phase": r["manual_phase"],
        "manual_phase_at": _iso(r["manual_phase_at"]),
        "manual_phase_source": r["manual_phase_source"],
        "added_at": _iso(r["added_at"]),
        "evaluated_at": _iso(r["evaluated_at"]),
        "phase": r["phase"],
        "prev_phase": r["prev_phase"],
        "phase_changed_at": _iso(r["phase_changed_at"]),
        "summary": json.loads(r["summary"]) if r["summary"] else None,
        "lists": list_ids,
    }


def update_ticker(symbol: str, **fields) -> None:
    allowed = {"note", "tags", "name", "industry"}
    values = {k: v for k, v in fields.items() if k in allowed}
    with engine.begin() as c:
        if "manual_phase" in fields:
            phase = fields["manual_phase"] or None
            source = fields.get("manual_phase_source", "user")
            when = fields.get("manual_phase_at") or now()
            auto = c.execute(select(tickers.c.phase).where(tickers.c.symbol == symbol)).scalar()
            values.update(manual_phase=phase, manual_phase_at=when if phase else None, manual_phase_source=source if phase else None)
            c.execute(insert(phase_labels).values(symbol=symbol, phase=phase, auto_phase=auto, source=source, labelled_at=when))
        if values:
            c.execute(update(tickers).where(tickers.c.symbol == symbol).values(**values))


def save_evaluation(symbol: str, summary: dict, detail: dict | None) -> None:
    """Store the latest evaluation; track phase changes between runs."""
    with engine.begin() as c:
        row = c.execute(select(tickers.c.phase, tickers.c.prev_phase, tickers.c.phase_changed_at)
                        .where(tickers.c.symbol == symbol)).mappings().first()
        if row is None:
            c.execute(insert(tickers).values(symbol=symbol, added_at=now()))
            row = {"phase": None, "prev_phase": None, "phase_changed_at": None}
        new_phase = summary.get("phase", {}).get("phase") if summary.get("phase") else None
        values = {
            "evaluated_at": now(),
            "summary": json.dumps(summary),
            "detail": json.dumps(detail) if detail is not None else None,
        }
        if summary.get("name"):
            values["name"] = summary["name"]
        if new_phase and new_phase != row["phase"]:
            values.update(phase=new_phase, prev_phase=row["phase"], phase_changed_at=now() if row["phase"] else None)
        elif new_phase is None and summary.get("verdict") != "error":
            values["phase"] = None
        c.execute(update(tickers).where(tickers.c.symbol == symbol).values(**values))


def phase_label_rows() -> list[dict]:
    with engine.connect() as c:
        rows = c.execute(select(phase_labels).order_by(phase_labels.c.labelled_at)).mappings().all()
    return [{**r, "labelled_at": _iso(r["labelled_at"])} for r in rows]
