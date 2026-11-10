"""MCP server: Toston's chat tools for external LLM clients.

External LLM clients (Claude, ChatGPT, Grok, OpenCode...) drive a user's
finances through the Model Context Protocol. The modules:

* ``tools``: the transport-agnostic tool registry, JSON Schemas and dispatch,
  on top of ``ai.chat_tools`` and ``ai.confirmation``.
* ``limits``: paid-plan requirement and per-user rate limits.
* ``server``: the MCP ``Server`` object (tools/list, tools/call).
* ``auth``: bearer-token authentication against Clerk.
* ``http``: the /mcp ASGI app and OAuth discovery routes.
"""
