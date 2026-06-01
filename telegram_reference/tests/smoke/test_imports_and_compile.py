from __future__ import annotations

import compileall
import importlib
import pkgutil

import app


def _iter_modules(package):
    for m in pkgutil.walk_packages(package.__path__, prefix=package.__name__ + "."):
        yield m.name


def test_import_app_main():
    importlib.import_module("app.main")


def test_import_handlers_services_repositories_integrations():
    targets = ["app.handlers", "app.services", "app.repositories", "app.integrations.yclients"]
    for root in targets:
        pkg = importlib.import_module(root)
        for name in _iter_modules(pkg):
            importlib.import_module(name)


def test_compileall_app():
    assert compileall.compile_dir(app.__path__[0], quiet=1)
