# -*- coding: utf-8 -*-
"""DinoBot production entrypoint.

The original implementation is preserved in legacy_main.py. This wrapper adds the
Control Center plus the user tutorial and moderation audit log system without
duplicating the bot, DB pool, OAuth or event handlers.
"""
import os
import uvicorn
import legacy_main as core
from control_center import install as install_control_center
from tutorial_logs import install as install_tutorial_logs

install_control_center(core)
install_tutorial_logs(core)

app = core.app
bot = core.bot

if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=int(os.getenv("PORT", 8000)))
