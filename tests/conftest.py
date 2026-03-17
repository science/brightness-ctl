"""Shared fixtures for brightness-ctl tests."""

import sys
from pathlib import Path

# Add src/ to path so we can import modules
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))
