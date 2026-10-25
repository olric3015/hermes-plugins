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


@pytest.fixture
def approvals_plugin():
    return _load_plugin("approval-ledger")


@pytest.fixture
def apledger(approvals_plugin):
    return approvals_plugin.ledger


@pytest.fixture
def commands_plugin():
    return _load_plugin("command-ledger")


@pytest.fixture
def cmdledger(commands_plugin):
    return commands_plugin.ledger


@pytest.fixture
def waits_plugin():
    return _load_plugin("wait-ledger")


@pytest.fixture
def wtledger(waits_plugin):
    return waits_plugin.ledger


@pytest.fixture
def stops_plugin():
    return _load_plugin("stop-ledger")


@pytest.fixture
def stledger(stops_plugin):
    return stops_plugin.ledger


@pytest.fixture
def voice_plugin():
    return _load_plugin("voice-ledger")


@pytest.fixture
def vcledger(voice_plugin):
    return voice_plugin.ledger


@pytest.fixture
def interim_plugin():
    return _load_plugin("interim-ledger")


@pytest.fixture
def imledger(interim_plugin):
    return interim_plugin.ledger


@pytest.fixture
def edits_plugin():
    return _load_plugin("edit-ledger")


@pytest.fixture
def edledger(edits_plugin):
    return edits_plugin.ledger


@pytest.fixture
def reactions_plugin():
    return _load_plugin("reaction-ledger")


@pytest.fixture
def rxledger(reactions_plugin):
    return reactions_plugin.ledger


@pytest.fixture
def unicode_plugin():
    return _load_plugin("unicode-ref")


@pytest.fixture
def uchars(unicode_plugin):
    return unicode_plugin.chars


@pytest.fixture
def ts_plugin():
    return _load_plugin("timestamp-ref")


@pytest.fixture
def stamps(ts_plugin):
    return ts_plugin.stamps


@pytest.fixture
def jwt_plugin():
    return _load_plugin("jwt-ref")


@pytest.fixture
def jwtok(jwt_plugin):
    return jwt_plugin.tokens


@pytest.fixture
def crontab_plugin():
    return _load_plugin("crontab-ref")


@pytest.fixture
def crons(crontab_plugin):
    return crontab_plugin.crons


@pytest.fixture
def b64_plugin():
    return _load_plugin("b64-ref")


@pytest.fixture
def blobs(b64_plugin):
    return b64_plugin.blobs
