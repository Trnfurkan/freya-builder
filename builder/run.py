"""Freya Builder: brief(s) in, phone agent on Freya out, with no manual step.

    python -m builder.run briefs/brief-a.md
    python -m builder.run briefs/brief-a.md briefs/brief-b.md --practice B
    python -m builder.run <final brief> --openapi <its tool API file> --export

Claude (through the Claude Agent SDK) reads the brief and the tool API and writes the workflow with the
`freya-builder` skill (.claude/skills/freya-builder). The deterministic parts come from this package:
date facts, answer normalisation and tool results (builder/sandbox), tools from the API file
(builder/openapi_tools.py) and the checks (builder/validate.py).

Needs FREYA_API_KEY, FREYA_BANK_TEAM_KEY, and a Claude login or ANTHROPIC_API_KEY. Run it from any folder:
it always works in the kit folder, so the same slot updates the same workflow.
Writes traces/<start>.txt (the trace to submit) and traces/<start>.jsonl (every message).
"""
import argparse
import asyncio
import dataclasses
import datetime
import json
import os
import pathlib
import re
import sys
import time

KIT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(KIT))

from builder import env as builder_env  # noqa: E402
from builder.trace import Trace, one_line  # noqa: E402

SKILL = "freya-builder"
DEFAULT_MODEL = "claude-opus-5-5"


def kit_path(p):
    """Path relative to the kit with forward slashes, for the prompt and for Bash."""
    p = pathlib.Path(p).resolve()
    try:
        return p.relative_to(KIT).as_posix()
    except ValueError:
        return p.as_posix()


CASE_FOLDER = ("Case folder (the bank's documents): {folder}. Read README.md first (it says what each document is), "
               "then BRIEF.md, then every other document in the folder: analysis, flow drawing, announcements, service "
               "contract, test customers, questions and answers, example conversations. A later announcement or answer "
               "overrides an earlier document; note in rules.md which one decided each rule. The agent speaks the "
               "language the documents ask for (the documents' own language when they say nothing), and every sentence "
               "they require is written word for word in that language. The example conversations and test customers "
               "show behaviour; they add no rule the documents do not state. Test the agent with builder.chat_test using "
               "the test customers' variables.")


def build_prompt(briefs, openapi, slot, practice, deadline, python, max_sims=2, mock_url=""):
    folders = [b for b in briefs if pathlib.Path(b).is_dir()]
    files = [b for b in briefs if b not in folders]
    lines = [f"Use the {SKILL} skill to build one phone agent."]
    if files:
        lines.append("Brief files, in order (a later file builds on and overrides the earlier ones): "
                     + ", ".join(kit_path(b) for b in files))
    lines += [CASE_FOLDER.format(folder=kit_path(f)) for f in folders]
    lines += [
        f"Tool API file: {kit_path(openapi)}",
        f"Slot: {slot}  (work folder out/{slot}/, save with this slot only)",
        f"Python interpreter for every command: {python}",
        f"Hard deadline: {deadline:%H:%M:%S} local time. Finish, with the agent saved, before it.",
    ]
    if mock_url:
        lines.append(f"The bank's services: the team's mock at {mock_url} (set as FREYA_MOCK_URL; "
                     "openapi_tools.base_url uses it). Never change a tool's address by hand.")
    if practice:
        lines.append(f"Practice customers exist for this brief: run them with `{python} -m builder.practice <agent_id> "
                     f"--brief {practice} --out results/{slot}-<n>.json` (same arguments as tools/run_cases.py). "
                     f"At most {max_sims} practice runs in this build; more are refused.")
    else:
        lines.append("There are no practice customers for this brief. Do not run tools/run_cases.py or builder.practice: "
                     "other briefs' customers would mislead you.")
    return "\n".join(lines)


PRACTICE_MARKERS = ("run_cases", "builder.practice")


def classify(command):
    """Everything a Bash command does on Freya, for the trace: a list of (kind, label). Empty when it is local.
    One command can do several things (validate, save and test in one line), so each one is listed."""
    c = command or ""
    found = []
    if "validate_workflow" in c or "dryRun" in c:
        found.append(("FREYA", "dry-run validation (stores nothing)"))
    if "create_workflow" in c:
        found.append(("FREYA", "create workflow"))
    if "save_workflow" in c:
        found.append(("FREYA", "save workflow (create the first time, then update and publish)"))
    if "export_workflow" in c:
        found.append(("FREYA", "export workflow"))
    if any(m in c for m in PRACTICE_MARKERS):
        found.append(("TEST", "practice simulation"))
    if "chat_test" in c or "chat/message" in c:
        found.append(("TEST", "chat turns against the published workflow"))
    if not found and ("freya." in c or "freyavoice" in c):
        found.append(("FREYA", "other Freya API call"))
    return found


NAME_END = r"\s'\";&|()<>,`"


class Gate:
    """Refuses tool calls that would break this run's limits, before they run.
    - practice simulations: at most `max_sims`, and none that cannot finish before the deadline;
    - workflows: only `save_workflow` with this run's slot; never create_workflow, never another slot;
    - isolation: a build sees only its own brief files and its own outputs. Other slots' out/ folders, other results,
      traces/ and the practice customers' file are refused, so no rule or value of another brief or run is carried
      over (a practice build was seen reusing another slot's build script)."""

    SIM_MINUTES = 11      # README: a ten-customer run takes 3 to 11 minutes
    PRIVATE = ("traces", "public-test-set.json")
    # Characters that end a file name in a shell command: whitespace, quotes and shell operators.
    # (A first version kept ";" in the name and refused "cat briefs/brief-b.md;" and "mkdir out/<own slot>;".)

    def __init__(self, slot, max_sims, deadline, now=None, briefs=(), kit=KIT):
        self.slot, self.max_sims, self.deadline = slot, max_sims, deadline
        self.now = now or datetime.datetime.now
        self.sims = 0
        self.kit_forms = sorted({pathlib.Path(kit).as_posix(), "/" + pathlib.Path(kit).as_posix().replace(":", "", 1)},
                                key=len, reverse=True)
        self.briefs = {pathlib.PurePosixPath(kit_path(b)).name for b in briefs if kit_path(b).startswith("briefs/")}

    def _norm(self, text):
        t = (text or "").replace("\\", "/")
        low = t.lower()
        for form in self.kit_forms:   # absolute kit paths become kit-relative ("/out/...")
            i = low.find(form.lower())
            while i >= 0:
                t = t[:i] + t[i + len(form):]
                low = low[:i] + low[i + len(form):]
                i = low.find(form.lower())
        return t

    def path_reason(self, text):
        """Why text that names files may not be used, or None."""
        t = self._norm(text)
        lead = r"(?:^|[\s'\"=(:,;])(?:\./|/)?"
        for m in re.finditer(lead + r"out/([^/" + NAME_END + r"]*)", t):
            if m.group(1) != self.slot:
                return f"Refused: out/{m.group(1) or '*'} is not this build's folder. Use only out/{self.slot}/."
        for m in re.finditer(lead + r"results/([^" + NAME_END + r"]*)", t):
            if not m.group(1).startswith(self.slot):
                return f"Refused: only results/{self.slot}-* belong to this build."
        for private in self.PRIVATE:
            tail = r"(?:/|$)" if "." not in private else r"(?:/|$|[\s'\"])"   # a folder needs its slash or the end
            if re.search(lead + re.escape(private) + tail, t):
                return f"Refused: {private} is not input for a build. Build from the brief and the tool API file."
        for m in re.finditer(lead + r"briefs/([^" + NAME_END + r"]*)", t):
            if self.briefs and m.group(1) not in self.briefs:
                return f"Refused: only the given brief files may be read: {', '.join(sorted(self.briefs))}."
        return None

    def decide_tool(self, tool, tool_input):
        """None to allow a Read/Glob/Grep call, else the reason."""
        tool_input = tool_input or {}
        names = [str(tool_input[k]).strip() for k in ("file_path", "path", "pattern", "glob")
                 if tool_input.get(k) and not (tool == "Grep" and k == "pattern")]
        for name in names:   # each one separately, so a folder at the end of a path is seen as the end
            why = self.path_reason(" " + name)
            if why:
                return why
        return None

    def decide(self, command):
        """None to allow a Bash command, else the reason it is refused."""
        c = command or ""
        why = self.path_reason(c)
        if why:
            return why
        if "create_workflow" in c:
            return "Refused: create workflows only through freya.save_workflow with this run's slot."
        if ("save_workflow" in c or "update_workflow" in c) and self.slot not in c:
            return f"Refused: save only with this run's slot '{self.slot}'."
        if any(m in c for m in PRACTICE_MARKERS):
            if self.sims >= self.max_sims:
                return f"Refused: this build may run at most {self.max_sims} practice simulations and has used them."
            left = (self.deadline - self.now()).total_seconds() / 60
            if left < self.SIM_MINUTES + 2:
                return f"Refused: {left:.0f} minutes left, a practice run can take {self.SIM_MINUTES}. Finish without it."
            self.sims += 1
        return None


async def build(args, trace, raw, stderr_log):
    from claude_agent_sdk import (AssistantMessage, ClaudeAgentOptions, HookMatcher, ResultMessage, TextBlock,
                                  ToolUseBlock, query)

    gate = Gate(args.slot, args.max_sims, args.deadline, briefs=args.briefs)

    async def check_tool(input_data, tool_use_id, context):
        tool = input_data.get("tool_name", "")
        tool_input = input_data.get("tool_input") or {}
        if tool == "Bash":
            reason = gate.decide(tool_input.get("command", ""))
        else:
            reason = gate.decide_tool(tool, tool_input)
        if not reason:
            return {}
        trace.note(f"{reason} {tool}: {one_line(tool_input, 200)}")
        return {"hookSpecificOutput": {"hookEventName": "PreToolUse", "permissionDecision": "deny",
                                       "permissionDecisionReason": reason}}

    def on_stderr(line):  # the CLI's own error output, kept out of the trace but saved for debugging
        stderr_log.write(line.rstrip("\n") + "\n")
        stderr_log.flush()

    options = ClaudeAgentOptions(
        cwd=KIT,
        model=args.model,
        skills=[SKILL],
        setting_sources=["project"],
        allowed_tools=["Read", "Write", "Edit", "Glob", "Grep", "Bash"],
        permission_mode="dontAsk",
        max_turns=args.max_turns,
        # Same settings as the kit's v3 example: a practice run takes up to 11 minutes and must stay in the foreground.
        env={"BASH_DEFAULT_TIMEOUT_MS": "900000", "BASH_MAX_TIMEOUT_MS": "1200000", "CLAUDE_CODE_DISABLE_BACKGROUND_TASKS": "1"},
        stderr=on_stderr,
        hooks={"PreToolUse": [HookMatcher(matcher="Bash|Read|Glob|Grep", hooks=[check_tool])]},
    )
    pending = {}
    async for message in query(prompt=args.prompt, options=options):
        raw.write(json.dumps({"type": type(message).__name__, **dataclasses.asdict(message)}, default=str) + "\n")
        raw.flush()
        if isinstance(message, AssistantMessage):
            texts = [b.text for b in message.content if isinstance(b, TextBlock)]
            tools = [b for b in message.content if isinstance(b, ToolUseBlock)]
            purpose = texts[0] if texts else ", ".join(f"{t.name} {one_line(t.input, 100)}" for t in tools) or "(no content)"
            trace.model_call(getattr(message, "model", None) or args.model, purpose)
            for t in texts:
                print(t, flush=True)
            for t in tools:
                print(f"  [{t.name}] {str(t.input)[:140]}", flush=True)
                kinds = classify(t.input.get("command", "")) if t.name == "Bash" else []
                if kinds:
                    pending[t.id] = kinds
                    for kind, label in kinds:
                        getattr(trace, kind.lower())(f"{label}: {one_line(t.input.get('command', ''), 300)}")
        elif type(message).__name__ == "UserMessage":
            for b in getattr(message, "content", None) or []:
                tid = getattr(b, "tool_use_id", None)
                if tid in pending:
                    kinds = pending.pop(tid)
                    out = getattr(b, "content", "")
                    if isinstance(out, list):
                        out = " ".join(str(x.get("text", x)) if isinstance(x, dict) else str(x) for x in out)
                    labels = " + ".join(label for _, label in kinds)
                    getattr(trace, kinds[-1][0].lower())(f"result of {labels}: {one_line(out, 350)}")
        elif isinstance(message, ResultMessage):
            trace.note(f"builder agent done ({message.subtype}{', error' if message.is_error else ''}): {message.num_turns} turns, "
                       f"{message.duration_ms / 1000:.0f} s, estimated ${message.total_cost_usd or 0:.2f}")
            if message.is_error:
                raise RuntimeError(error_summary(message))
            print(f"\ndone: {message.num_turns} turns, {message.duration_ms / 1000:.0f} s")


def error_summary(message):
    """Why the builder agent stopped, from its error result. A result can say subtype "success" and still be an
    error: in one run the builder was cut off by the Claude usage limit (API status 429) with subtype "success"."""
    parts = [f"builder agent stopped with an error ({getattr(message, 'subtype', '')})"]
    status = getattr(message, "api_error_status", None)
    reason = getattr(message, "terminal_reason", None)
    if status:
        parts.append(f"API status {status}")
    if reason:
        parts.append(f"reason {reason}")
    text = getattr(message, "result", None)
    if text:
        parts.append(one_line(text, 200))
    if str(status) == "429":
        parts.append("the Claude usage limit was reached; the saved workflow is whatever was saved before it")
    return "; ".join(parts)


def sdk_version():
    try:
        from importlib.metadata import version
        return version("claude-agent-sdk")
    except Exception:
        return "not installed"


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("briefs", nargs="+", help="brief files, or a case folder with the bank's documents")
    ap.add_argument("--openapi", default=str(KIT / "tools" / "openapi.json"))
    ap.add_argument("--slot", help="workflow slot; default: the last brief's file name")
    ap.add_argument("--practice", help="brief letter in public-test-set.json whose practice customers fit this brief")
    ap.add_argument("--budget-min", type=float, default=28.0, help="hard stop; the rules allow 30 minutes per brief")
    ap.add_argument("--model", default=os.environ.get("BUILDER_MODEL", DEFAULT_MODEL))
    ap.add_argument("--max-turns", type=int, default=200)
    ap.add_argument("--max-sims", type=int, default=2, help="practice simulations this build may start (enforced)")
    ap.add_argument("--export", action="store_true", help="after the build, export the workflow to submission/<slot>.workflow.json")
    ap.add_argument("--mock-url", help="the team's mock of the bank's services; every tool calls it")
    ap.add_argument("--keep-host-env", action="store_true",
                    help="keep the variables of an enclosing Claude Code session (default: removed from this process)")
    args = ap.parse_args(argv)

    loaded = builder_env.load_env_file(KIT / "env.sh")
    stripped = [] if args.keep_host_env else builder_env.strip_host_session()

    for b in args.briefs + [args.openapi]:
        if not (pathlib.Path(b).is_file() or (b != args.openapi and pathlib.Path(b).is_dir())):
            sys.exit(f"not found: {b}")
    if args.mock_url:
        from builder.openapi_tools import MOCK_URL_VAR
        os.environ[MOCK_URL_VAR] = args.mock_url.rstrip("/")
    missing = [v for v in ("FREYA_API_KEY", "FREYA_BANK_TEAM_KEY") if not os.environ.get(v)]
    if missing:
        sys.exit("missing environment variables: " + ", ".join(missing))
    args.slot = args.slot or pathlib.Path(args.briefs[-1]).stem
    start = datetime.datetime.now()
    deadline = start + datetime.timedelta(minutes=args.budget_min)
    args.deadline = deadline
    args.prompt = build_prompt(args.briefs, args.openapi, args.slot, args.practice, deadline,
                               pathlib.Path(sys.executable).as_posix(), args.max_sims if args.practice else 0,
                               mock_url=args.mock_url or "")

    stamp = start.strftime("%Y%m%d-%H%M%S")
    trace = Trace(KIT / "traces" / f"{stamp}.txt")
    trace.start(briefs=",".join(kit_path(b) for b in args.briefs), openapi=kit_path(args.openapi), slot=args.slot,
                mock_url=args.mock_url or "-",
                builder_model=args.model, budget_min=args.budget_min, max_sims=args.max_sims if args.practice else 0,
                sdk=sdk_version())
    if loaded:
        trace.note("loaded from env.sh (names only): " + ", ".join(loaded))
    if stripped:
        trace.note(f"removed {len(stripped)} variables of an enclosing Claude Code session from this process")
    trace.note("prompt: " + args.prompt.replace("\n", " | "))
    status = "ok"
    with (KIT / "traces" / f"{stamp}.jsonl").open("w", encoding="utf-8") as raw, \
            (KIT / "traces" / f"{stamp}.stderr.log").open("w", encoding="utf-8") as stderr_log:
        try:
            asyncio.run(asyncio.wait_for(build(args, trace, raw, stderr_log), timeout=args.budget_min * 60))
        except asyncio.TimeoutError:
            status = "stopped at the time budget"
            trace.note(status)
        except Exception as e:  # keep the trace complete even when the build crashes
            status = f"error: {type(e).__name__}: {e}"
            trace.note(status)

    state = KIT / ".freya-agents.json"
    saved = json.loads(state.read_text()).get(args.slot) if state.exists() else None
    if saved:
        trace.freya(f"slot {args.slot}: workflow_id={saved.get('workflow_id')} agent_id={saved.get('agent_id')} version={saved.get('version', 1)}")
        if args.export:
            import freya
            (KIT / "submission").mkdir(exist_ok=True)
            path = freya.export_workflow(saved["workflow_id"], str(KIT / "submission" / f"{args.slot}.workflow.json"))
            trace.freya(f"exported workflow to {kit_path(path)}")
    else:
        trace.note(f"no workflow saved for slot {args.slot}")
        status = status if status != "ok" else "no workflow saved"
    trace.end(status=status, seconds=round(time.time() - start.timestamp()))
    print(f"\n{status}. Trace: {kit_path(trace.path)}")
    return 0 if status == "ok" else 1


if __name__ == "__main__":
    sys.exit(main())
