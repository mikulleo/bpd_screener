# Vercel entrypoint: every /api/* request is rewritten here (see vercel.json).
from screener.server import app  # noqa: F401
