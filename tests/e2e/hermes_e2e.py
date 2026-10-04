"""End-to-end check against a real Hermes checkout (not collected by pytest).

    PYTHONPATH=<hermes checkout> <hermes venv python> tests/e2e/hermes_e2e.py

Installs the plugin into a throwaway HERMES_HOME, lets Hermes's own loader discover it, drives
real auxiliary calls through ``agent.auxiliary_client`` at a loopback provider, then checks what
the plugin recorded and what ``/aux`` prints. Nothing leaves 127.0.0.1.
"""

import json
import os
import shutil
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
HOME = Path(tempfile.mkdtemp(prefix="aux-ledger-e2e-"))
os.environ["HERMES_HOME"] = str(HOME)
os.environ["HERMES_DISABLE_LAZY_INSTALLS"] = "1"
for name in [n for n in os.environ if n.startswith(("OPENAI", "ANTHROPIC", "OPENROUTER"))]:
    del os.environ[name]

from tests.fakes.fake_llm_provider import Error, FakeLLMServer, Text, write_hermes_home  # noqa: E402

PROMPT = "PRIVATE-PROMPT-7f3a summarize the quarterly numbers"
ANSWER = "PRIVATE-ANSWER-91bc"


def main() -> int:
    replies = [Text(ANSWER, prompt_tokens=321, completion_tokens=12, cached_tokens=21),
               Text(ANSWER, prompt_tokens=1000, completion_tokens=50)]

    def aux(_record):
        return replies.pop(0) if replies else Error(500, "PRIVATE-PROVIDER-ERROR-55d0")

    with FakeLLMServer(aux=aux) as srv:
        write_hermes_home(HOME, srv.base_url, extra_config="plugins:\n  enabled: [aux-ledger]\n")
        shutil.copytree(REPO / "aux-ledger", HOME / "plugins" / "aux-ledger")

        from agent.auxiliary_client import call_llm
        from hermes_cli.plugins import discover_plugins, get_plugin_command_handler

        discover_plugins(force=True)
        aux_command = get_plugin_command_handler("aux")
        assert aux_command is not None, "/aux was not registered: the plugin did not load"

        messages = [{"role": "user", "content": PROMPT}]
        call_llm(task="title_generation", messages=messages, max_tokens=32)
        call_llm(task="compression", messages=messages, max_tokens=64)
        try:
            call_llm(task="compression", messages=messages, max_tokens=64)
        except Exception as exc:
            print(f"scripted failure surfaced as {type(exc).__name__}")
        else:
            raise AssertionError("the scripted 500 did not fail the auxiliary call")

        ledger_file = HOME / "plugin-data" / "aux-ledger" / "calls.jsonl"
        raw = ledger_file.read_text(encoding="utf-8")
        rows = [json.loads(line) for line in raw.splitlines()]
        print(f"hermes sent {len(srv.aux_requests())} auxiliary request(s); ledger holds {len(rows)} row(s)")
        assert len(rows) == len(srv.aux_requests()) >= 3, "one row per provider attempt"
        for needle in ("PRIVATE-PROMPT", "PRIVATE-ANSWER", "PRIVATE-PROVIDER-ERROR"):
            assert needle not in raw, f"{needle} text reached the ledger"

        title, good = rows[0], rows[1]
        assert title["aux_task"] == "title_generation" and title["error_type"] is None
        assert title["input_tokens"] + title["cache_read_tokens"] == 321 and title["output_tokens"] == 12
        assert good["aux_task"] == "compression" and good["output_tokens"] == 50
        failed = [r for r in rows if r["error_type"]]
        assert failed and all(r["aux_task"] == "compression" and not r["has_usage"] for r in failed)

        report = aux_command("all")
        print(report)
        lines = report.splitlines()
        assert lines[3].split()[:5] == ["compression", str(len(rows) - 1), str(len(failed)), "1,000", "50"]
        assert lines[4].split()[:5] == ["title_generation", "1", "0", "321", "12"]
        assert aux_command("clear") == f"aux-ledger: deleted {len(rows)} recorded call(s)."
        assert not ledger_file.exists()
    print("E2E OK")
    return 0


if __name__ == "__main__":
    code = main()
    shutil.rmtree(HOME, ignore_errors=True)
    sys.exit(code)
