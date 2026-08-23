# -*- coding: utf-8 -*-
"""DinoBot production entrypoint.

The former legacy core is preserved as ``core.py``. Feature modules are additive
and keep persistent state in PostgreSQL, so code deployments do not reset data.
"""
import os
import uvicorn
import core
from control_center import install as install_control_center
from tutorial_logs import install as install_tutorial_logs
from ticket_control import install as install_ticket_control
from persistent_settings import install as install_persistent_settings

install_control_center(core)
install_tutorial_logs(core)
install_ticket_control(core)
install_persistent_settings(core)

app = core.app
bot = core.bot

if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=int(os.getenv("PORT", 8000)))
