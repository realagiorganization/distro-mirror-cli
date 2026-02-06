"""Behave environment setup."""

import os
from pathlib import Path
import tempfile


def before_scenario(context, scenario):
    context.temp_dir = tempfile.TemporaryDirectory()
    context.temp_path = Path(context.temp_dir.name)
    context.repo_root = Path(__file__).resolve().parents[1]
    context.env = os.environ.copy()
    context.env["DISTRO_MIRROR_HOME"] = str(context.temp_path / "mirror_home")
    context.env["PYTHONUNBUFFERED"] = "1"


def after_scenario(context, scenario):
    context.temp_dir.cleanup()
