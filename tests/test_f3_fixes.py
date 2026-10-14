"""F3 (persona round 2): the PATCH response carries prev_saved_at so a tab only advances its Save base when nobody else wrote in between;
without it a stale tab's own autosave adopted the other tab's saved_at and its next Save silently overwrote the other tab's edit.
Browser behaviour (D1 two tabs, D2 reconnect, D4 delete, D5 DOM churn) is in tests/browser/f3.js."""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from test_f1_fixes import call, srv  # noqa: E402,F401




