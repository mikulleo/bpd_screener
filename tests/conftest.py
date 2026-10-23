import os
import tempfile

# Use a throwaway SQLite database unless a Postgres DATABASE_URL is given explicitly
# (e.g. DATABASE_URL=postgres://... pytest). Must run before screener.db is imported.
if not os.environ.get("DATABASE_URL", "").startswith("postgres"):
    os.environ["DATABASE_URL"] = "sqlite:///" + os.path.join(tempfile.mkdtemp(), "test.db")
os.environ.pop("APP_PASSWORD", None)
