"""Repository facade boundary for future domain split.

The current implementation remains in `repository.py`. This module gives tests,
CLI, and future route modules a stable import target while the god-object
repository is decomposed into service/toolbox/token/audit repositories.
"""

from __future__ import annotations

from .repository import DEFAULT_TOOLBOX_ID, LOCAL_USER_ID, Repository, new_id

__all__ = ["DEFAULT_TOOLBOX_ID", "LOCAL_USER_ID", "Repository", "new_id"]
