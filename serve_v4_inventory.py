#!/usr/bin/env python3
"""v4 N2 routes: GET /inventory, /inventory/{connector}, /templates/touches. Read-only. Imported (optionally) by serve_v4, which calls install(v4).
/inventory serves inventory/connectors.json (regenerated in memory when the file is missing; never written from here)."""
import json
import os

HERE = os.path.dirname(os.path.abspath(__file__))
INV_FILE = os.path.join(HERE, "inventory", "connectors.json")


def _inv_mod():
    import library
    return library._inventory_mod()


def load_inventory():
    try:
        with open(INV_FILE, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        m = _inv_mod()
        return m.build() if m else None


def handle_inventory(h, host, params, query):
    inv = load_inventory()
    if inv is None:
        return h._send(503, json.dumps({"error": "inventory unavailable"}))
    return h._send(200, json.dumps(inv))


def handle_inventory_connector(h, host, params, query):
    inv = load_inventory()
    c = ((inv or {}).get("connectors") or {}).get(params.get("connector", ""))
    if c is None:
        return h._send(404, json.dumps({"error": "unknown connector", "connector": params.get("connector")}))
    return h._send(200, json.dumps(c))


def handle_touches(h, host, params, query):
    import library
    mods = [{"name": i["name"], "touches": i.get("touches", {"tools": [], "blocked": [], "connectors": []})} for i in library._scan_modules_list()]
    return h._send(200, json.dumps({"modules": mods, "blocked_modules": [m["name"] for m in mods if m["touches"]["blocked"]]}))


def install(v4):
    v4.register("inventory", handle_inventory)
    v4.register("inventory_connector", handle_inventory_connector)
    v4.register("templates_touches", handle_touches)
