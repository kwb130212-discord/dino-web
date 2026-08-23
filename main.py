# -*- coding: utf-8 -*-
"""DinoBot production entrypoint.

The original implementation is preserved in legacy_main.py. This wrapper adds the
Control Center, user tutorial/audit logs, and configurable ticket controls without
duplicating the bot, DB pool, OAuth or legacy event handlers.
"""
import os
import uvicorn
import legacy_main as core
from control_center import install as install_control_center
from tutorial_logs import install as install_tutorial_logs
from ticket_control import install as install_ticket_control

install_control_center(core)
install_tutorial_logs(core)
install_ticket_control(core)

app = core.app
bot = core.bot

if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=int(os.getenv("PORT", 8000)))
