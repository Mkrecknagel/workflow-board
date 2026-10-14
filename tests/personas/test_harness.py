"""Tests for the persona harness (N5). Run: PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 python -m pytest tests/personas/test_harness.py -q
Covers: sandbox + free port (never 8765) + seeded boards validate; real-boards guard (pass, tamper fail, refuse); persona scripts parse;
severity classifier on 8 sample findings; report writer + rollup; smoke step through playwright-cli on desktop and phone viewports."""
import json
import os
import shutil
import subprocess
import sys
import tempfile

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import classify  # noqa: E402
import guard  # noqa: E402
import harness  # noqa: E402
import report  # noqa: E402

SPEC = json.load(open(os.path.join(HERE, "personas.json"), encoding="utf-8"))


# ---- criterion 1: sandbox, free port, seeded boards, guard ----
def test_free_port_never_8765():
    ports = {harness.free_port() for _ in range(25)}
    assert 8765 not in ports and all(p > 1024 for p in ports)


def test_sandbox_is_a_temp_copy_with_valid_seeded_boards():
    sb = harness.make_sandbox()
    try:
        assert os.path.realpath(sb["wb"]) != os.path.realpath(harness.SRC)
        names = sorted(f for f in os.listdir(sb["boards"]) if f.endswith(".json"))
        assert names == ["active.json", "starter-hub.json", "starter-intake.json", "starter-publish.json"]
        for n in names:
            if n == "active.json":
                continue
            r = subprocess.run([sys.executable, "validate.py", os.path.join("boards", n)], cwd=sb["wb"], capture_output=True, text=True)
            assert r.returncode == 0, (n, r.stderr)
    finally:
        shutil.rmtree(sb["tmp"], ignore_errors=True)


def test_server_starts_on_free_port_and_serves_seeded_boards():
    sb = harness.make_sandbox()
    proc, port, base = harness.start_server(sb)
    try:
        assert port != 8765
        import urllib.request
        j = json.loads(urllib.request.urlopen(base + "/boards", timeout=5).read())
        assert "starter-hub" in j["boards"]
    finally:
        proc.kill()
        shutil.rmtree(sb["tmp"], ignore_errors=True)


def test_guard_self_test_pass_and_deliberate_tamper_fail():
    r = guard.self_test()
    assert r["untouched_passes"] is True
    assert r["tamper_detected"] is True
    assert r["added_file_detected"] is True and r["ok"] is True


def test_runner_refuses_when_real_boards_hash_differs():
    before = guard.snapshot()
    assert harness.enforce_guard(before, dict(before)) is True
    tampered = dict(before)
    k = sorted(tampered)[0]
    tampered[k] = "0" * 64
    with pytest.raises(harness.GuardError):
        harness.enforce_guard(before, tampered)


def test_node_sandbox_never_8765_and_real_boards_untouched():
    code = ("const L=require('./lib.js');(async()=>{const b=L.hashDir(L.SRC+'/boards');const sb=L.makeSandbox('t');const s=await L.startServer(sb);"
            "const ok=s.port!==8765&&L.sameHash(b,L.hashDir(L.SRC+'/boards'))&&sb.files().includes('starter-hub.json')&&sb.tmp!==L.SRC;s.stop();L.rmrf(sb.tmp);console.log(JSON.stringify({ok,port:s.port}));})()")
    r = subprocess.run([harness.NODE, "-e", code], cwd=HERE, capture_output=True, text=True, timeout=120)
    assert r.returncode == 0 and json.loads(r.stdout.strip().splitlines()[-1])["ok"] is True, r.stderr


# ---- criterion 2: persona briefs and scripts parse ----
def test_three_personas_and_ordered_steps():
    assert [p["id"] for p in SPEC["personas"]] == ["dana", "erin", "frank"]
    for p in SPEC["personas"]:
        assert p["brief"] and p["params"]["labelWords"] and len(p["params"]["labelWords"]) == 4
    assert [s["id"] for s in SPEC["steps"]] == list("abcdefghi")
    for s in SPEC["steps"]:
        assert s["action"] and s["expected"] and s["evidence"].startswith("docs/personas/")
    assert set(SPEC["viewports"]) == {"desktop", "phone"}
    assert SPEC["viewports"]["desktop"]["width"] == 1440 and SPEC["viewports"]["phone"]["touch"] is True




# ---- criterion 3: classifier on 8 sample findings, schema, writer ----
SAMPLES = [
    ({"category": "uncaught_exception", "flags": ["crash"]}, "major"),
    ({"category": "data_loss", "flags": ["data_loss", "lost_update"]}, "major"),
    ({"category": "unsafe_enabled", "flags": ["unsafe", "gate_background"]}, "major"),
    ({"category": "stuck_flow", "flags": ["stuck", "zoom_by_gesture"]}, "major"),
    ({"category": "copy", "flags": ["jargon"]}, "minor"),
    ({"category": "phone_ergonomics", "flags": ["touch_target"]}, "minor"),
    ({"category": "awkward", "flags": ["no_feedback"]}, "minor"),
    ({"category": "note", "flags": ["suggestion"]}, "note"),
]


@pytest.mark.parametrize("raw,want", SAMPLES)
def test_classifier_samples(raw, want):
    assert classify.classify(raw) == want


def test_stuck_flow_without_stuck_flag_is_minor_and_real_boards_modified_is_major():
    assert classify.classify({"category": "stuck_flow", "flags": ["awkward"]}) == "minor"
    assert classify.classify({"category": "awkward", "flags": ["real_boards_modified"]}) == "major"


def test_dedupe_merges_viewports_and_assigns_ids_and_schema():
    raw = [
        {"persona": "erin", "viewport": "desktop", "step": "b", "category": "awkward", "flags": ["awkward"], "title": "Button X not clickable on 1440x900", "repro": "r", "evidence": "e", "file": "f"},
        {"persona": "erin", "viewport": "phone", "step": "b", "category": "awkward", "flags": ["awkward"], "title": "Button X not clickable on 390x844", "repro": "r", "evidence": "e", "file": "f"},
        {"persona": "erin", "viewport": "phone", "step": "i", "category": "data_loss", "flags": ["data_loss"], "title": "Edit lost", "repro": "r2", "evidence": "e2", "file": "g"},
    ]
    out = classify.dedupe(raw)
    assert len(out) == 2 and out[0]["severity"] == "major" and out[0]["id"] == "erin-001"
    merged = [f for f in out if f["severity"] == "minor"][0]
    assert sorted(merged["viewports"]) == ["desktop", "phone"]
    for f in out:
        assert classify.validate_finding(f) == []
    assert classify.counts(out) == {"major": 1, "minor": 1, "note": 0}


def test_report_writer_emits_findings_md_and_rollup_with_round_counts():
    tmp = tempfile.mkdtemp(prefix="wb-rep-")
    try:
        persona = SPEC["personas"][0]
        results = {"desktop": {"steps": [{"step": "a", "pass": True}, {"step": "a", "pass": False}, {"step": "b", "pass": True}]},
                   "phone": {"steps": [{"step": "a", "pass": True}]}}
        raw = [{"persona": "dana", "viewport": "phone", "step": "i", "category": "data_loss", "flags": ["data_loss"], "title": "Edit lost", "repro": "do x", "evidence": "ev", "file": "f.js"},
               {"persona": "dana", "viewport": "desktop", "step": "a", "category": "copy", "flags": [], "title": "Jargon", "repro": "look", "evidence": "", "file": ""}]
        fs = classify.dedupe(raw)
        p = report.write_findings_md(persona, persona["brief"], results, fs, tmp, 1)
        txt = open(p, encoding="utf-8").read()
        assert "Counts: major 1, minor 1, note 0." in txt and "dana-001" in txt and "1/2" in txt
        entry = report.build_round(1, {"dana": {"findings": fs, "results": results}}, {"real_boards_unchanged": True})
        rp = report.write_rollup(entry, tmp)
        d = json.load(open(rp, encoding="utf-8"))
        r0 = d["rounds"][0]
        assert (r0["major"], r0["minor"], r0["note"]) == (1, 1, 0)
        assert r0["majors"][0]["id"] == "dana-001" and r0["majors"][0]["repro"] == "do x" and r0["majors"][0]["evidence_path"]
        assert d["final"]["major_open"] == 1
        entry2 = report.build_round(2, {"dana": {"findings": [], "results": results}}, {"real_boards_unchanged": True})
        report.write_rollup(entry2, tmp)
        d = json.load(open(rp, encoding="utf-8"))
        assert [r["round"] for r in d["rounds"]] == [1, 2] and d["final"]["major_open"] == 0
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


# ---- criterion 4: smoke step through playwright-cli, headless, desktop + phone ----
@pytest.mark.skipif(not harness.CLI, reason="playwright-cli not installed")
@pytest.mark.parametrize("viewport", ["desktop", "phone"])
def test_smoke_step_through_playwright_cli(viewport):
    sb = harness.make_sandbox()
    proc, port, base = harness.start_server(sb)
    try:
        r = harness.smoke_step(base, viewport)
        assert r["ok"] and r["width_ok"], r
    finally:
        proc.kill()
        shutil.rmtree(sb["tmp"], ignore_errors=True)
