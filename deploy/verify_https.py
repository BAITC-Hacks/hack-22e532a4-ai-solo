"""Compatibility entry point for the public-preview / protected-workspace check."""
import runpy
from pathlib import Path

runpy.run_path(str(Path(__file__).with_name('check_access.py')), run_name='__main__')
