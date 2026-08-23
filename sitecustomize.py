"""Runtime web-surface bootstrap for DinoBot.

Python imports ``sitecustomize`` automatically when it is present on sys.path.
The existing bot is a large single-file application, so this small bootstrap
lets us install the new dashboard before the legacy routes are registered,
without duplicating or rewriting the bot implementation.

If the dashboard module is unavailable, startup continues normally: the bot's
original routes remain available instead of failing the whole process.
"""
from __future__ import annotations

try:
    from fastapi import FastAPI
    from dino_dashboard import install

    _original_init = FastAPI.__init__

    def _dino_init(self, *args, **kwargs):
        _original_init(self, *args, **kwargs)
        try:
            install(self)
        except Exception:
            # Do not make a web UI failure take the Discord bot offline.
            # The legacy application will continue registering its routes.
            pass

    FastAPI.__init__ = _dino_init
except Exception:
    # Optional bootstrap: never block Python startup.
    pass
