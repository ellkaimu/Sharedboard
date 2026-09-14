"""Legacy setup.py delegating to pyproject.toml.

Kept for ``dpkg-buildpackage``-style builds and any tooling that still
invokes ``setup.py`` directly.
"""

from setuptools import setup

setup()
