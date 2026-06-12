"""Services package.

Wraps the orchestrator + memory store behind a clean API for routers.
W1 ships an `LLMClient` interface; W2 provides the real OpenAI-compatible
implementation.
"""
