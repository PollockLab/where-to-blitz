"""Put the repo root on `sys.path` so research/ modules can import root modules.

`voi_backtest.py` and `backtest_appscore.py` stay at the repo root because
METHODOLOGY.md cites them as the reproduce path. Modules here import them, so
they need the root importable regardless of which directory Python started in.

Import this before any root module; it sorts first, so import order holds:

    import _rootpath  # noqa: F401
    import voi_backtest as vb
"""
import sys
from pathlib import Path

_ROOT = str(Path(__file__).resolve().parent.parent)
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)
