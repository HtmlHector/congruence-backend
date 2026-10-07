"""Internal tool registry.

The LLM gateway that used to live here has been removed. It resolved provider
keys from our own operator settings (``settings.llm.*``) to drive a tool-calling
loop on the user's behalf, which contradicted Harness Neutrality (``prd.md``
Invariant 3), Host Custody (Invariant 4), and the no-resale pricing principle.

Agent reasoning now runs inside the lane via the vendor's own CLI, using the
credential that CLI wrote to the host's ``$HOME``. See ``trd.md`` section 7.4.
"""

from src.infrastructure.llm.fastmcp_server import mcp as fastmcp_server

__all__ = ["fastmcp_server"]
