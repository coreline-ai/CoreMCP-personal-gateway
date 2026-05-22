"""Runtime-level helpers shared by ``coremcp.main`` and gateway modules.

This package owns small indirection layers (e.g., :class:`AppContext`) that
let extracted modules avoid touching ``app.state.*`` directly. Anything that
depends on the running FastAPI ``app`` belongs here once it grows past a
single call site.
"""

from coremcp.runtime.context import AppContext

__all__ = ["AppContext"]
