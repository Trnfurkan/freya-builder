from builder import env


def test_parse_env_file_like_the_kit_example():
    text = (
        "export FREYA_API_KEY=fak_your_key_here                 # from your Freya workspace, under API keys\n"
        "export FREYA_API_URL=https://staging-app.freyavoice.ai\n"
        "# a comment\n"
        "\n"
        "FREYA_BANK_TEAM_KEY='quoted value # not a comment'\n"
        "not a variable line\n"
    )
    assert env.parse_env_file(text) == {
        "FREYA_API_KEY": "fak_your_key_here",
        "FREYA_API_URL": "https://staging-app.freyavoice.ai",
        "FREYA_BANK_TEAM_KEY": "quoted value # not a comment",
    }


def test_load_env_file_never_overrides(tmp_path):
    f = tmp_path / "env.sh"
    f.write_text("export A=from_file\nexport B=from_file\nexport C=\n", encoding="utf-8")
    environ = {"A": "already"}
    assert env.load_env_file(f, environ) == ["B"]
    assert environ == {"A": "already", "B": "from_file"}
    assert env.load_env_file(tmp_path / "missing.sh", environ) == []


HOST = {
    "CLAUDECODE": "1", "CLAUDE_CODE_ENTRYPOINT": "x", "CLAUDE_CODE_SESSION_ID": "x", "CLAUDE_AGENT_SDK_VERSION": "x",
    "ANTHROPIC_BASE_URL": "http://127.0.0.1:1234", "CLAUDE_PID": "1", "CLAUDE_EFFORT": "low",
    "CLAUDE_CODE_OAUTH_TOKEN": "keep", "ANTHROPIC_API_KEY": "keep", "CLAUDE_CODE_USE_BEDROCK": "1",
    "CLAUDE_CONFIG_DIR": "keep", "PATH": "keep", "FREYA_API_KEY": "keep",
}


def test_strip_host_session_removes_only_host_variables():
    environ = dict(HOST)
    removed = env.strip_host_session(environ)
    assert removed == sorted(["CLAUDECODE", "CLAUDE_CODE_ENTRYPOINT", "CLAUDE_CODE_SESSION_ID", "CLAUDE_AGENT_SDK_VERSION",
                              "ANTHROPIC_BASE_URL", "CLAUDE_PID", "CLAUDE_EFFORT"])
    assert set(environ) == {"CLAUDE_CODE_OAUTH_TOKEN", "ANTHROPIC_API_KEY", "CLAUDE_CODE_USE_BEDROCK", "CLAUDE_CONFIG_DIR",
                            "PATH", "FREYA_API_KEY"}


def test_outside_a_host_session_nothing_is_removed():
    environ = {"ANTHROPIC_BASE_URL": "https://gateway.example", "CLAUDE_CODE_MAX_RETRIES": "2", "PATH": "x"}
    assert env.strip_host_session(environ) == []
    assert len(environ) == 3


def test_present_reports_booleans_only():
    assert env.present(["A", "B"], {"A": "secret"}) == {"A": True, "B": False}
