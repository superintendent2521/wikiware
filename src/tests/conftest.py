"""
Test configuration for WikiWare unit tests.

Ensures the project root is on sys.path so modules under src can be imported
without relying on external environment variables.
"""

import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))
