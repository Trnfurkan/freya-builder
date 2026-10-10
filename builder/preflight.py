"""Check that a builder run can start, without any model call and without calling Freya.

    python -m builder.preflight [--openapi <tool API file>] [--auth]

Prints names and yes/no only, never a value. --auth also runs `claude auth status` with the SDK's bundled CLI in
the same cleaned environment run.py uses; that reads the local login and may refresh it, but asks no model.
Exit code 1 when something a run needs is missing.
"""
import argparse
import json
import os
import pathlib
import subprocess
import sys

KIT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(KIT))

from builder import env as builder_env  # noqa: E402
from builder import openapi_tools  # noqa: E402


def bundled_cli():
    try:
        import claude_agent_sdk
    except ImportError:
        return None
    return next(pathlib.Path(claude_agent_sdk.__file__).parent.rglob("claude.exe"), None) or \
        next(pathlib.Path(claude_agent_sdk.__file__).parent.rglob("claude"), None)


def auth_method(environ):
    exe = bundled_cli()
    if not exe:
        return None, "no bundled CLI"
    p = subprocess.run([str(exe), "auth", "status"], env=environ, capture_output=True, text=True, timeout=60)
    try:
        return p.returncode, json.loads(p.stdout).get("authMethod")
    except Exception:
        return p.returncode, "unreadable output"


def checks(openapi_path, with_auth):
    rows = []

    def row(ok, what, detail="", critical=True):
        rows.append((ok, critical, what, detail))

    row(sys.version_info >= (3, 10), "Python >= 3.10", sys.version.split()[0])
    try:
        from importlib.metadata import version
        row(True, "claude-agent-sdk installed", version("claude-agent-sdk"))
    except Exception:
        row(False, "claude-agent-sdk installed", "pip install claude-agent-sdk into this Python")
    row(bundled_cli() is not None, "SDK bundled Claude Code CLI present")

    environ = dict(os.environ)
    loaded = builder_env.load_env_file(KIT / "env.sh", environ)
    row(True, "env.sh", ("set " + ", ".join(loaded)) if loaded else "nothing new loaded", critical=False)
    stripped = builder_env.strip_host_session(environ)
    row(True, "enclosing Claude Code session", f"yes, {len(stripped)} variables removed for the run" if stripped else "no",
        critical=False)
    for name, ok in builder_env.present(["FREYA_API_KEY", "FREYA_BANK_TEAM_KEY"], environ).items():
        row(ok, f"{name} set")
    creds = [n for n, ok in builder_env.present(["ANTHROPIC_API_KEY", "CLAUDE_CODE_OAUTH_TOKEN"], environ).items() if ok]
    row(True, "Claude credential in environment", ", ".join(creds) if creds else "none (the CLI login is used)", critical=False)

    for rel in ("freya.py", "tools/run_cases.py", ".claude/skills/freya-builder/SKILL.md"):
        row((KIT / rel).is_file(), f"{rel} present")
    try:
        spec = openapi_tools.load(openapi_path)
        row(True, "tool API file readable", f"{len(openapi_tools.tool_names(spec))} tools: {', '.join(openapi_tools.tool_names(spec))}")
    except Exception as e:
        row(False, "tool API file readable", f"{type(e).__name__}: {e}")

    state = KIT / ".freya-agents.json"
    if state.exists():
        try:
            slots = sorted(json.loads(state.read_text(encoding="utf-8")))
            row(True, "saved slots (.freya-agents.json)", ", ".join(slots) or "none", critical=False)
        except Exception:
            row(False, "saved slots (.freya-agents.json)", "unreadable", critical=False)
    else:
        row(True, "saved slots (.freya-agents.json)", "none yet; the first save creates a workflow", critical=False)

    if with_auth:
        code, method = auth_method(environ)
        row(code == 0, "Claude login for the SDK's CLI", f"authMethod {method}")
    return rows


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--openapi", default=str(KIT / "tools" / "openapi.json"))
    ap.add_argument("--auth", action="store_true")
    a = ap.parse_args(argv)
    rows = checks(a.openapi, a.auth)
    for ok, critical, what, detail in rows:
        mark = "OK  " if ok else ("FAIL" if critical else "WARN")
        print(f"{mark} {what}" + (f": {detail}" if detail else ""))
    bad = [r for r in rows if not r[0] and r[1]]
    print("\nready" if not bad else f"\nnot ready: {len(bad)} problem(s)")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
