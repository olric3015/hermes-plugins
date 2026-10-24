"""End-to-end check of crontab-ref against a real Hermes checkout (not collected by pytest).

    PYTHONPATH=<hermes checkout> <hermes venv python> tests/e2e/crontab_ref_e2e.py

Installs the plugin into a throwaway HERMES_HOME (timezone set to Asia/Kolkata) and runs real
one-shot `hermes chat` turns against a loopback provider, then reads the request Hermes sent to
the model: the `@crontab:` references must have been expanded in place. A child process then
checks the plugin against Hermes's own scheduler code (`cron.jobs.parse_schedule` and
`compute_next_run`) and asks the TUI gateway's `complete.path` handler for suggestions.
Nothing leaves 127.0.0.1.
"""

import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
HOME = Path(os.environ.get("CRONTAB_E2E_HOME") or tempfile.mkdtemp(prefix="crontab-ref-e2e-"))
ANSWER = "ANSWER-c7r all done"
ZONE = "Asia/Kolkata"

# Expressions the child compares with Hermes's own scheduler code.
PARITY = ("0 9 * * 1-5", "0 9 * * MON-FRI", "*/15 * * * *", "30 18 1 * *", "0 9 L * *", "0 9 1 * 1",
          "0 9 * * L5", "* * * * * 30", "0 0 * jan,jul *", "@daily", "@hourly", "0 9 * * 5#3",
          "0 9 ? * 1", "0 9 31 2 *")


def _env() -> dict:
    env = {k: v for k, v in os.environ.items()
           if not k.startswith(("OPENAI", "ANTHROPIC", "OPENROUTER", "GIT_", "HERMES_TIMEZONE"))}
    env.update(HERMES_HOME=str(HOME), HERMES_DISABLE_LAZY_INSTALLS="1", PYTHONUTF8="1",
               PYTHONIOENCODING="utf-8", CRONTAB_E2E_HOME=str(HOME))
    return env


def _hermes(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run([sys.executable, "-m", "hermes_cli.main", *args], env=_env(),
                          capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=180)


def _user_text(body: dict) -> str:
    """The newest user message of a chat-completions request, as plain text."""
    message = [m for m in body["messages"] if m.get("role") == "user"][-1]
    content = message["content"]
    if isinstance(content, list):
        content = "".join(part.get("text", "") for part in content if isinstance(part, dict))
    return content


def child() -> int:
    """Compare with Hermes's scheduler; ask the TUI gateway for suggestions (its import moves
    stdout, so the results go to a file)."""
    import importlib.util
    from datetime import datetime

    import hermes_time
    from cron.jobs import compute_next_run, parse_schedule
    from hermes_cli.plugins import discover_plugins
    from tui_gateway import server

    spec = importlib.util.spec_from_file_location(
        "crontab_ref_e2e_pkg", HOME / "plugins" / "crontab-ref" / "__init__.py",
        submodule_search_locations=[str(HOME / "plugins" / "crontab-ref")])
    plugin = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = plugin
    spec.loader.exec_module(plugin)
    crons = plugin.crons

    out = {"zone": hermes_time.get_timezone_name(), "plugin_zone": crons.configured_zone_name(), "parity": {}}
    for expr in PARITY:
        try:
            hermes_cron = parse_schedule(expr).get("kind") == "cron"
        except ValueError:
            hermes_cron = False
        reading = crons.parse(expr)
        accepted, _ = crons.hermes_verdict(reading)
        hermes_next = plugin_next = None
        if hermes_cron:
            try:
                hermes_next = compute_next_run(parse_schedule(expr))
            except Exception as exc:  # croniter's "failed to find next date" for a date that never comes
                hermes_next = f"raised {type(exc).__name__}"
            now, zone, _ = crons.hermes_now()
            runs = crons.next_runs(reading, now, zone, count=1)
            plugin_next = runs[0].isoformat() if runs else None
        out["parity"][expr] = {"hermes_cron": hermes_cron, "plugin_accepts": accepted,
                               "hermes_next": hermes_next, "plugin_next": plugin_next}
    discover_plugins(force=True)
    out["complete"] = {}
    for word in ("@crontab:", "@crontab:@daily"):
        out["complete"][word] = server.handle_request(
            {"jsonrpc": "2.0", "id": 1, "method": "complete.path", "params": {"word": word}})
    out["checked_at"] = datetime.now().isoformat()
    (HOME / "child.json").write_text(json.dumps(out, ensure_ascii=False, default=str), encoding="utf-8")
    return 0


def main() -> int:
    from tests.fakes.fake_llm_provider import FakeLLMServer, Text, write_hermes_home

    prompts = (
        'When does this fire? @crontab:"0 9 * * 1-5"',
        "Is this right for a nightly job? @crontab:@daily.",
        'Why is this uneven? @crontab:"*/7 * * * *" and what about @crontab:5',
    )
    with FakeLLMServer([Text(ANSWER) for _ in prompts]) as srv:
        write_hermes_home(HOME, srv.base_url,
                          extra_config=f"plugins:\n  enabled: [crontab-ref]\ntimezone: {ZONE}\n")
        shutil.copytree(REPO / "crontab-ref", HOME / "plugins" / "crontab-ref")
        # No -Q: quiet mode sends the query to the agent without expanding @ references.
        for prompt in prompts:
            run = _hermes("chat", "-q", prompt, "--oneshot")
            assert run.returncode == 0, f"hermes chat failed:\n{run.stdout[-2000:]}\n{run.stderr[-2000:]}"
            assert ANSWER in run.stdout, "the scripted answer was not printed"
        first, second, third = (_user_text(body) for body in srv.main_requests()[:3])
    for text in (first, second, third):
        print(text)
        print("-" * 40)
        assert "unsupported reference type" not in text

    # A quoted five-field expression, with runs in the configured zone.
    assert 'Cron expression "0 9 * * 1-5", as Hermes\'s scheduler (croniter) reads it' in first
    assert "  At 09:00 on Monday through Friday." in first
    assert "Hermes /cron: accepted as a cron expression" in first
    assert f"Next 5 runs, in {ZONE} (Hermes's timezone setting)" in first
    runs = [line for line in first.splitlines() if line.startswith("  ") and " 09:00 +05:30  in " in line]
    assert len(runs) == 5 and all(line.split()[0] in ("Mon", "Tue", "Wed", "Thu", "Fri") for line in runs), runs
    assert "plugin expansion error" not in first
    # An unquoted macro; the sentence's "." is not part of it.
    assert 'Cron expression "@daily"' in second
    assert "  same as 0 0 * * *" in second
    assert "Hermes /cron: Hermes's /cron does not take @daily; write the five fields instead: 0 0 * * *" in second
    assert "plugin expansion error" not in second
    # Two references: one with a note, one that is not an expression.
    assert ("*/7 in the minute field starts again every hour: after :56 the next run is :00, "
            "4 minutes later, not 7") in third
    assert ("@crontab:5: plugin expansion error: a cron expression has 5 fields (minute hour "
            "day-of-month month day-of-week), optionally a 6th (second) and 7th (year); this has 1") in third

    run = subprocess.run([sys.executable, __file__, "child"], env=_env(), capture_output=True,
                         text=True, encoding="utf-8", errors="replace", timeout=300)
    assert run.returncode == 0, run.stderr[-4000:]
    result = json.loads((HOME / "child.json").read_text(encoding="utf-8-sig"))
    assert result["zone"] == result["plugin_zone"] == ZONE, result
    for expr, row in result["parity"].items():
        print(f"{expr!r:22} {row}")
        assert row["hermes_cron"] == row["plugin_accepts"], expr
        if expr != "0 9 31 2 *":
            assert row["hermes_next"] == row["plugin_next"], expr
    accepted = {e for e, r in result["parity"].items() if r["hermes_cron"]}
    assert {"@daily", "@hourly", "0 9 * * 5#3", "0 9 ? * 1"}.isdisjoint(accepted), accepted
    # A date that never comes: Hermes takes the expression but finds no next run; so does the plugin.
    never = result["parity"]["0 9 31 2 *"]
    assert never["hermes_next"] in (None, "raised CroniterBadDateError") and never["plugin_next"] is None, never
    for word, reply in result["complete"].items():
        items = reply["result"]["items"]
        print(word, "->", [(i["text"], i.get("display"), i.get("meta")) for i in items])
    examples = result["complete"]["@crontab:"]["result"]["items"]
    assert [i["text"] for i in examples] == ['@crontab:"0 9 * * 1-5"', '@crontab:"*/15 * * * *"',
                                             '@crontab:"30 18 1 * *"'], examples
    assert examples[0]["display"] == "0 9 * * 1-5 = At 09:00 on Monday through Friday.", examples
    daily = result["complete"]["@crontab:@daily"]["result"]["items"]
    assert [(i["text"], i["display"]) for i in daily] == [
        ("@crontab:@daily", "@daily = At 00:00 every day.")], daily
    print("E2E OK")
    return 0


if __name__ == "__main__":
    if len(sys.argv) > 1:
        sys.exit(child())
    code = main()
    shutil.rmtree(HOME, ignore_errors=True)
    sys.exit(code)
