"""
Main entry point for the application.
"""

import os
import subprocess
from typing import Any
from dotenv import load_dotenv

load_dotenv()

DEV_TRUTHY_VALUES = {"1", "true", "yes", "on"}
DEV_FALSEY_VALUES = {"0", "false", "no", "off"}


def required_env(name: str) -> Any:
    """Get required environment variable or raise error."""
    value = os.getenv(name)
    if value is None:
        raise RuntimeError(f"Environment variable '{name}' is required but not set.")
    return value


def parse_bool_env(name: str) -> bool:
    """Parse an environment variable into a strict boolean."""
    raw_value = required_env(name)
    normalized = raw_value.strip().lower()
    if not normalized:
        raise RuntimeError(
            f"Environment variable '{name}' must not be blank. Expected true/false"
        )
    if normalized in DEV_TRUTHY_VALUES:
        return True
    if normalized in DEV_FALSEY_VALUES:
        return False
    raise RuntimeError(
        f"Environment variable '{name}' must be one of: true/false, 1/0, yes/no, on/off"
    )


port = int(required_env("PORT"))
dev = parse_bool_env("DEV")
# Normalize DEV for child processes that read the environment directly.
os.environ["DEV"] = "true" if dev else "false"

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
