"""Environment handling for the builder process. Never prints or logs a value, only names.

- load_env_file: reads the kit's `env.sh` (lines like `export NAME=value`) into os.environ, so the builder
  runs the same from Git Bash, PowerShell or cmd. A variable that is already set is not overridden.
- strip_host_session: when the builder is started from inside a Claude Code session (desktop app, IDE, a
  Claude Code tool call), that session's own variables would leak into the CLI the SDK starts: an
  ANTHROPIC_BASE_URL pointing at the host's endpoint and the nested-session markers. The build would then run on
  the host session's credentials instead of this machine's login. This removes them from this process only;
  the parent shell and the system settings are not touched.
"""
import os
import re

# Credentials and provider choices the user may set on purpose: never removed.
KEEP = {
    "ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN", "ANTHROPIC_MODEL", "ANTHROPIC_PROFILE",
    "CLAUDE_CODE_OAUTH_TOKEN", "CLAUDE_CODE_USE_BEDROCK", "CLAUDE_CODE_USE_VERTEX", "CLAUDE_CODE_USE_FOUNDRY",
    "CLAUDE_CODE_USE_ANTHROPIC_AWS", "CLAUDE_CONFIG_DIR",
}
HOST_MARKERS = ("CLAUDECODE", "CLAUDE_CODE_ENTRYPOINT", "CLAUDE_CODE_CHILD_SESSION")
_LINE = re.compile(r"^\s*(?:export\s+)?([A-Za-z_][A-Za-z0-9_]*)=(.*)$")


def parse_env_file(text):
    out = {}
    for line in text.splitlines():
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        m = _LINE.match(line)
        if not m:
            continue
        value = m.group(2).strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "'\"":
            value = value[1:-1]
        else:
            value = re.sub(r"\s+#.*$", "", value)  # trailing comment, as in env.example
        out[m.group(1)] = value
    return out


def load_env_file(path, environ=None):
    """Set variables from the file that are not set yet. Returns the names it set."""
    environ = os.environ if environ is None else environ
    try:
        with open(path, encoding="utf-8") as f:
            values = parse_env_file(f.read())
    except FileNotFoundError:
        return []
    loaded = []
    for name, value in values.items():
        if value and not environ.get(name):
            environ[name] = value
            loaded.append(name)
    return loaded


def in_host_session(environ=None):
    environ = os.environ if environ is None else environ
    return any(environ.get(k) for k in HOST_MARKERS)


def host_session_vars(environ=None):
    """Names that belong to an enclosing Claude Code session. Empty when not inside one."""
    environ = os.environ if environ is None else environ
    if not in_host_session(environ):
        return []
    names = []
    for k in environ:
        u = k.upper()
        if u in KEEP:
            continue
        if u == "ANTHROPIC_BASE_URL" or u == "CLAUDECODE" or u.startswith("CLAUDE_CODE_") or u.startswith("CLAUDE_AGENT_SDK_") \
                or u in ("CLAUDE_PID", "CLAUDE_EFFORT") or u.startswith("CLAUDE_PREVIEW_"):
            names.append(k)
    return sorted(names)


def strip_host_session(environ=None):
    """Remove the enclosing session's variables from this process. Returns the names removed."""
    environ = os.environ if environ is None else environ
    names = host_session_vars(environ)
    for k in names:
        environ.pop(k, None)
    return names


def present(names, environ=None):
    environ = os.environ if environ is None else environ
    return {n: bool(environ.get(n)) for n in names}
