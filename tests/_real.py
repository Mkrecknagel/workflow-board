"""Test helper: locate the REAL instance boards (registered in the project.json files) read-only, and copy them into temp
board dirs. Tests never write to the real paths. Independent of BOARD_REGISTRY_ROOT (which conftest points at an empty dir so
temp-copy servers see only their own copies)."""
import os
import shutil
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
import registry  # noqa: E402

ENGINE_BOARDS = os.path.join(ROOT, "boards")


def real_root():
    return os.environ.get("WORKFLOW_BOARD_ROOT") or registry.DEFAULT_ROOT


def real_paths():
    """{name: path} for engine boards + registered instance boards (real files)."""
    return registry.scan(ENGINE_BOARDS, root=real_root())[0]


def real_path(name):
    return real_paths()[name]


def add_instances(dst_boards):
    """Copy every registered instance board into a temp boards dir (engine boards are already there)."""
    os.makedirs(dst_boards, exist_ok=True)
    for n, p in real_paths().items():
        if os.path.abspath(os.path.dirname(p)) != os.path.abspath(ENGINE_BOARDS):
            shutil.copy(p, os.path.join(dst_boards, n + ".json"))


def sha_all():
    import hashlib
    return {p: hashlib.sha256(open(p, "rb").read()).hexdigest() for p in real_paths().values()}


def real_env():
    """os.environ without the empty-registry override: for subprocesses that must see the real instance boards."""
    e = dict(os.environ)
    e.pop("BOARD_REGISTRY_ROOT", None)
    return e


class real_registry:
    """Context manager: in-process code (validate drill lookups) sees the real registry inside the block."""
    def __enter__(self):
        self.old = os.environ.pop("BOARD_REGISTRY_ROOT", None)

    def __exit__(self, *a):
        if self.old is not None:
            os.environ["BOARD_REGISTRY_ROOT"] = self.old


if __name__ == "__main__":  # used by tests/browser/_real.js
    import json
    if sys.argv[1:2] == ["add"]:
        add_instances(sys.argv[2])
    elif sys.argv[1:2] == ["paths"]:
        print(json.dumps(real_paths()))
