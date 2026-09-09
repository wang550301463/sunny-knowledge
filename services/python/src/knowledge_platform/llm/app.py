"""LLM service entrypoint.

The recovered implementation (models + InferenceService + app factory) lives in
runtime.py as a single module from the Codex sessions; this module re-exports
the ASGI app for the uvicorn target knowledge_platform.llm.app:app.
"""
from .runtime import app, create_app  # noqa: F401
