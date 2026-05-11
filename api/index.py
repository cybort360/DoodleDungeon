import sys, os

_backend = os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'backend')
sys.path.insert(0, _backend)

from main import app  # noqa: F401 — Vercel picks up the FastAPI ASGI app
