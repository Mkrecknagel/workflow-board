"""F3 (persona round 2): the PATCH response carries prev_saved_at so a tab only advances its Save base when nobody else wrote in between;
without it a stale tab's own autosave adopted the other tab's saved_at and its next Save silently overwrote the other tab's edit.
Browser behaviour (D1 two tabs, D2 reconnect, D4 delete, D5 DOM churn) is in tests/browser/f3.js."""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from test_f1_fixes import call, srv  # noqa: E402,F401


def test_patch_reports_prev_saved_at_chain(srv):
    base, _ = srv
    _, doc = call(base, "GET", "/board/demo-board")
    cid = next(c["id"] for c in doc["cards"] if c["type"] != "note")
    opened = doc.get("saved_at")
    code, r1 = call(base, "PATCH", "/board/demo-board/visual", {"cards": {cid: {"pos": {"x": 11, "y": 11}}}})
    assert code == 200 and r1["prev_saved_at"] == opened
    code, r2 = call(base, "PATCH", "/board/demo-board/visual", {"cards": {cid: {"pos": {"x": 12, "y": 12}}}})
    assert code == 200 and r2["prev_saved_at"] == r1["saved_at"]


def test_stale_base_stays_stale_after_other_tabs_patch(srv):
    base, b = srv
    _, doc = call(base, "GET", "/board/demo-board")
    ids = [c["id"] for c in doc["cards"] if c["type"] != "note"]
    opened = doc.get("saved_at")
    _, a = call(base, "PATCH", "/board/demo-board/visual", {"cards": {ids[0]: {"pos": {"x": 21, "y": 21}}}})   # tab A
    _, bb = call(base, "PATCH", "/board/demo-board/visual", {"cards": {ids[1]: {"pos": {"x": 22, "y": 22}}}})  # tab B: prev != its base
    assert bb["prev_saved_at"] != opened  # a client comparing prev to its held base must NOT advance it
    code, r = call(base, "PUT", "/board/demo-board?base=" + opened, doc)  # tab B Save with the base it still holds
    assert code == 409 and r["code"] == "base_mismatch"
