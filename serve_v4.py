#!/usr/bin/env python3
"""v4 read-only routes for workflow-board (route skeleton). Installed by ONE `import serve_v4` line in serve.py, which wraps the
request handler class H (do_GET only) exactly like serve_shared.py does. Nothing here writes to disk.

ROUTES (GET only; every one answers 501 {"error":"not implemented","route":NAME} until a node registers a handler):
  name                 path                              owner
  inventory            /inventory                        N2  (serve_v4_inventory.py)
  inventory_connector  /inventory/{connector}            N2  (serve_v4_inventory.py)
  templates_touches    /templates/touches                N2  (serve_v4_inventory.py)
  runs                 /runs/{board}?card=<id>           N3  (serve_v4_runs.py)
  history              /history/{board}                  N3  (serve_v4_runs.py)
  history_item         /history/{board}/{id}             N3  (serve_v4_runs.py)
  active               /active                           N3  (serve_v4_runs.py)
Also (implemented here, not a stub): GET /v4/modules -> {"modules": [...]} the canvas/js/ v4 modules that exist on disk, in load
order; the index.html loader imports only these, so a missing module never produces a 404 console error.

EXTENSION POINT (N2/N3 do this, nothing else in this file or serve.py changes):
    # serve_v4_inventory.py (top-level module next to serve.py)
    import serve_v4
    def handle_inventory(h, host, params, query):      # h = request handler, host = serve module, params = path captures,
        h._send(200, json.dumps({...}))                 # query = parse_qs dict. Reply with h._send / h._err. Return value ignored.
    def install(v4):                                     # called once by serve_v4 at import time, if the module imports
        v4.register("inventory", handle_inventory)
        v4.register("inventory_connector", handle_inventory_connector)
        v4.register("templates_touches", handle_touches)
serve_v4 auto-imports serve_v4_inventory and serve_v4_runs (OPTIONAL_MODULES) and calls their install(v4). A module that is absent
is skipped silently; one that raises is recorded in LOAD_ERRORS and skipped (the server must still start). register() refuses
unknown route names. Handlers must be read-only; never write boards/ or active.json from here (restore, if N3 ships it, is a
documented exception owned by N3's own route and tests).
"""
import importlib
import json
import os
import re
import sys
from urllib.parse import urlparse, parse_qs

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

BNAME = r"[a-z0-9][a-z0-9-]*"
ROUTES = [  # (name, compiled path regex); first match wins; params = groupdict()
    ("inventory", re.compile(r"^/inventory/?$")),
    ("inventory_connector", re.compile(r"^/inventory/(?P<connector>[a-z0-9-]+)$")),
    ("templates_touches", re.compile(r"^/templates/touches/?$")),
    ("runs", re.compile(rf"^/runs/(?P<board>{BNAME})$")),
    ("history", re.compile(rf"^/history/(?P<board>{BNAME})/?$")),
    ("history_item", re.compile(rf"^/history/(?P<board>{BNAME})/(?P<id>[A-Za-z0-9._-]+)$")),
    ("active", re.compile(r"^/active/?$")),
]
NAMES = {n for n, _ in ROUTES}
HANDLERS = {}
LOAD_ERRORS = {}
OPTIONAL_MODULES = ("serve_v4_inventory", "serve_v4_runs")
V4_CANVAS_MODULES = ("kinds", "containers", "runs", "history", "dirty", "collapse", "tidy", "autolayout", "toolbar", "runmode", "nested", "minimap", "contracts")  # load order


def register(name, fn):
    """Install fn(h, host, params, query) for a route name. Later registration replaces an earlier one."""
    if name not in NAMES:
        raise ValueError(f"unknown v4 route {name!r}; known: {sorted(NAMES)}")
    HANDLERS[name] = fn


def _not_implemented(name):
    def fn(h, host, params, query):
        return h._send(501, json.dumps({"error": "not implemented", "route": name}))
    return fn


def match(path):
    """-> (route_name, params) or (None, None)."""
    for name, rx in ROUTES:
        m = rx.match(path)
        if m:
            return name, m.groupdict()
    return None, None


def canvas_modules():
    d = os.path.join(HERE, "canvas", "js")
    return [m for m in V4_CANVAS_MODULES if os.path.isfile(os.path.join(d, m + ".js"))]


def handle_get(h, host, u):
    """Returns True when the request was answered here."""
    if u.path == "/v4/modules":
        h._send(200, json.dumps({"modules": canvas_modules()}))
        return True
    name, params = match(u.path)
    if name is None:
        return False
    fn = HANDLERS.get(name) or _not_implemented(name)
    try:
        fn(h, host, params, parse_qs(u.query))
    except Exception as e:  # noqa: BLE001  a handler bug must answer 500, never kill the connection thread silently
        h._err(500, f"v4 route {name} failed: {type(e).__name__}")
    return True


def _host():
    for n in ("__main__", "serve"):
        m = sys.modules.get(n)
        if m is not None and hasattr(m, "H") and hasattr(m, "BOARDS"):
            return m
    raise RuntimeError("serve module not found")


def _load_optional():
    me = sys.modules[__name__]
    for mn in OPTIONAL_MODULES:
        if not os.path.isfile(os.path.join(HERE, mn + ".py")):
            continue
        try:
            mod = importlib.import_module(mn)
            if hasattr(mod, "install"):
                mod.install(me)
        except Exception as e:  # noqa: BLE001
            LOAD_ERRORS[mn] = f"{type(e).__name__}: {str(e)[:120]}"
            print(f"serve_v4: could not load {mn}: {LOAD_ERRORS[mn]}", file=sys.stderr, flush=True)


def install(host):
    H = host.H
    if getattr(H, "_v4_installed", False):
        return
    orig_get = H.do_GET

    def do_GET(self):
        u = urlparse(self.path)
        if handle_get(self, host, u):
            return
        return orig_get(self)

    H.do_GET = do_GET
    H._v4_installed = True


# Handlers register lazily so that importing this module for tests does not require a running host.
sys.modules.setdefault("serve_v4", sys.modules[__name__])
_load_optional()
try:
    install(_host())
except RuntimeError:
    pass  # imported outside the server (tests); handle_get / register / match still work
