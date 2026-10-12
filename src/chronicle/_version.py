"""Single source of truth for the package version.

Imported by ``chronicle/__init__.py``, the HTTP user agent and the run
manifest, so the three can never disagree. A test checks that it also
matches ``pyproject.toml``.
"""

__version__ = "0.2.1"
