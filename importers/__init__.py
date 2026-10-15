"""Board importers: read a folder of agent files and produce boards. Files stay the system of record.

Interface. An importer is a module in this package with:
  NAME              short id, e.g. "tpm-portfolio"
  detect(root)      True when `root` looks like this importer's layout
  build(root, opts) -> {board name: board doc}. Read-only on `root`. Every generated card SHOULD carry
                    provenance fields (src_path, src_anchor, src_sha; see common.prov) and each board a
                    top-level "generated" block {importer, sources}. wb_init.py adds the board hash used for
                    no-clobber.

To add an importer: put a module here, add it to IMPORTERS, and add a fixture test proving it imports a sample
layout faithfully (tests/test_wb_init.py shows the pattern).
"""
from . import tpm_portfolio

IMPORTERS = {tpm_portfolio.NAME: tpm_portfolio}


def get(name):
    return IMPORTERS[name]


def detect(root):
    """First importer whose detect() accepts `root`, or None."""
    for m in IMPORTERS.values():
        if m.detect(root):
            return m
    return None
