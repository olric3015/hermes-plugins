import importlib.util
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent


def _load_plugin(dirname):
    """Import a plugin directory as a package, the way Hermes's loader does."""
    name = dirname.replace("-", "_") + "_under_test"
    if name in sys.modules:
        return sys.modules[name]
    plugin_dir = ROOT / dirname
    spec = importlib.util.spec_from_file_location(
        name, plugin_dir / "__init__.py", submodule_search_locations=[str(plugin_dir)])
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def plugin():
    return _load_plugin("aux-ledger")


@pytest.fixture
def ledger(plugin):
    return plugin.ledger


@pytest.fixture
def speed_plugin():
    return _load_plugin("stream-speed")


@pytest.fixture
def timing(speed_plugin):
    return speed_plugin.timing


@pytest.fixture
def errors_plugin():
    return _load_plugin("error-ledger")


@pytest.fixture
def errledger(errors_plugin):
    return errors_plugin.ledger
