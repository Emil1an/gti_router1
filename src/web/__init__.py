"""Local console web subsystem (Epic 11).

Exposes the in-process FastAPI app (:mod:`web.local_api`) and the loopback
uvicorn server (:class:`web.server.LocalConsoleServer`) that the Router's
``main.py`` starts alongside the capture/upload pipeline.
"""
