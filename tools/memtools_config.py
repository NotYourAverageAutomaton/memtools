"""Where the notes are, and the optional config (hosts for remote claims, home host).

The notes folder is, in order: the --vault flag a tool is given, the MEMTOOLS_VAULT environment
variable, or the current folder. The config is memtools.toml in the notes folder, else
~/.config/memtools/config.toml, else empty. Example in memtools.example.toml.
"""
import os
import sys
from pathlib import Path

try:
    import tomllib
except ModuleNotFoundError:
    tomllib = None


def _flag(argv) -> str:
    for i, a in enumerate(argv):
        if a == "--vault" and i + 1 < len(argv):
            return argv[i + 1]
        if a.startswith("--vault="):
            return a.split("=", 1)[1]
    return ""


def vault_root() -> Path:
    """The --vault flag (read here too, so the config comes from the same folder), else MEMTOOLS_VAULT, else cwd."""
    flag = _flag(sys.argv[1:])
    if flag:
        return Path(flag).expanduser().resolve()
    env = os.environ.get("MEMTOOLS_VAULT", "").strip()
    return Path(env).expanduser().resolve() if env else Path.cwd().resolve()


def load_config(vault: Path) -> dict:
    for p in (Path(vault) / "memtools.toml", Path.home() / ".config" / "memtools" / "config.toml"):
        if p.is_file():
            if tomllib is None:
                raise SystemExit(f"{p} exists but this Python has no tomllib (needs Python 3.11+)")
            with open(p, "rb") as fh:
                return tomllib.load(fh)
    return {}


def layout(cfg: dict) -> dict:
    """The optional [layout] section, with defaults that work on any folder of notes."""
    lay = dict(cfg.get("layout", {}) or {})
    return {
        "tiers": {k.strip("/"): float(v) for k, v in (lay.get("tiers", {}) or {}).items()},
        "arcs_dir": str(lay.get("arcs_dir", "projects")).strip("/"),
        "rules_dir": str(lay.get("rules_dir", "")).strip("/"),
        "candidate_dir": str(lay.get("candidate_dir", "")).strip("/"),
        "hot_heading": str(lay.get("hot_heading", "CURRENT WORKING SET")),
        "exclude_dirs": set(lay.get("exclude_dirs", [".git", "raw", "scripts", "__pycache__"])),
    }
