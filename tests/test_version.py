"""The package version is single-sourced.

``pyproject.toml`` is the single source of truth for the version. The
``tc_fitness.__version__`` literal must agree with the installed-package
metadata (``importlib.metadata.version("three-cubes-fitness")``) so a tag bump
can't drift the two apart. When the package is not installed in-tree (a bare
``sys.path`` checkout), the metadata lookup raises ``PackageNotFoundError`` and
the assertion degrades to "the literal is a well-formed version string".
"""

from __future__ import annotations

import importlib.metadata

import tc_fitness


def test_version_matches_installed_metadata() -> None:
    try:
        metadata_version = importlib.metadata.version("three-cubes-fitness")
    except importlib.metadata.PackageNotFoundError:  # pragma: no cover - only when not installed
        # Not installed in-tree: just prove the literal is non-empty + dotted.
        assert tc_fitness.__version__
        assert "." in tc_fitness.__version__
        return
    assert tc_fitness.__version__ == metadata_version


def test_version_is_unreleased_v0_5_0() -> None:
    # v0.5.0 develops the `tc-fitness run` single-runnable-gate surface (tagged
    # only at review + merge). The literal stays at 0.5.0 throughout so the
    # CHANGELOG entry is honest and the fallback literal tracks the pyproject
    # version.
    assert tc_fitness.__version__ == "0.5.0"
