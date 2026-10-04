"""Bundles the framework dirs (skills/, roles/, ...) into ai_pipeline/payload at build time.

The repo root stays the single source of truth; the wheel/sdist gets a copy.
"""
import os
import shutil

from setuptools import setup
from setuptools.command.build_py import build_py

HERE = os.path.dirname(os.path.abspath(__file__))
DIRS = ["skills", "roles", "templates", "schemas", "scripts", "examples"]
FILES = ["AGENTS.md"]


class BuildWithPayload(build_py):
    def run(self):
        dest = os.path.join(HERE, "ai_pipeline", "payload")
        if os.path.isdir(dest):
            shutil.rmtree(dest)
        os.makedirs(dest)
        ignore = shutil.ignore_patterns("__pycache__", "*.pyc")
        for d in DIRS:
            shutil.copytree(os.path.join(HERE, d), os.path.join(dest, d), ignore=ignore)
        for f in FILES:
            shutil.copy2(os.path.join(HERE, f), os.path.join(dest, f))
        super().run()


setup(cmdclass={"build_py": BuildWithPayload})
