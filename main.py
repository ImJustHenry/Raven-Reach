"""Raven Reach — run on computer: python main.py   (deploy: python main.py deploy)"""
import os

from raven_framework.core.run_app import RunApp

from reach.config import load_config
from reach.ui import ReachApp

if __name__ == "__main__":
    config = load_config()
    RunApp.run(lambda: ReachApp(config),
               app_id=os.environ.get("RAVEN_APP_ID", config.get("app_id", "")),
               app_key=os.environ.get("RAVEN_APP_KEY", config.get("app_key", "")))
