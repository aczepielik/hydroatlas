"""Repository layout helpers."""

from pathlib import Path


def repo_root() -> Path:
    """Locate the repository root (the directory containing site/hugo.toml)."""
    for parent in Path(__file__).resolve().parents:
        if (parent / "site" / "hugo.toml").is_file():
            return parent
    raise RuntimeError("repository root not found (site/hugo.toml missing)")


def data_dir() -> Path:
    return repo_root() / "data"


def raw_dir() -> Path:
    return data_dir() / "raw"


def clean_dir() -> Path:
    return data_dir() / "clean"


def site_dir() -> Path:
    return repo_root() / "site"


def refs_dir() -> Path:
    return repo_root() / "etl" / "refs"


def layout_dir() -> Path:
    return repo_root() / "etl" / "layout"
