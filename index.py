"""
Main entry point for the application.
"""

import os
import subprocess
from typing import Any
from dotenv import load_dotenv

load_dotenv()


def required_env(name: str) -> Any:
    """Get required environment variable or raise error."""
    value = os.getenv(name)
    if value is None:
        raise RuntimeError(f"Environment variable '{name}' is required but not set.")
    return value


port = int(required_env("PORT"))
dev_raw = required_env("DEV")
DEV_TRUTHY_VALUES = {"1", "true", "yes", "on"}
DEV_FALSEY_VALUES = {"0", "false", "no", "off"}

normalized_dev = dev_raw.strip().lower()
if not normalized_dev:
    raise RuntimeError("Environment variable 'DEV' must not be blank. Expected true/false, 1/0, yes/no, or on/off.")
if normalized_dev not in (DEV_TRUTHY_VALUES | DEV_FALSEY_VALUES):
    raise RuntimeError("Environment variable 'DEV' must be one of: true/false, 1/0, yes/no, on/off")

dev = normalized_dev in DEV_TRUTHY_VALUES

try:
    print(port, dev, os.getcwd())
    subprocess.run(
        [
            "uvicorn",
            "src.server:app",
            *(["--reload"] if dev else []),
            "--host",
            "0.0.0.0",
            "--port",
            str(port),
        ],
        cwd=os.getcwd(),
        check=True,
    )
except KeyboardInterrupt:
    print("Server stopped by user.")
except Exception as e:
    print(f"An error occurred while starting the server: {e}")
    raise
