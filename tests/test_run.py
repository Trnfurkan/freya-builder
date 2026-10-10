import datetime

from builder import run
from builder.trace import Trace


def test_prompt_without_practice_forbids_practice_runs():
    p = run.build_prompt([run.KIT / "briefs" / "brief-a.md"], run.KIT / "tools" / "openapi.json", "brief-a", None,
                         datetime.datetime(2026, 10, 11, 10, 28), "C:/py/python.exe")
    assert "briefs/brief-a.md" in p and "tools/openapi.json" in p
    assert "Slot: brief-a" in p and "C:/py/python.exe" in p and "10:28:00" in p
    assert "Do not run tools/run_cases.py" in p


def test_prompt_with_practice():
    p = run.build_prompt([run.KIT / "briefs" / "brief-a.md", run.KIT / "briefs" / "brief-b.md"], run.KIT / "tools" / "openapi.json",
                         "brief-b", "B", datetime.datetime(2026, 10, 11, 10, 28), "python")
    assert "briefs/brief-a.md, briefs/brief-b.md" in p
    assert "--brief B" in p


def test_classify_commands():
    assert run.classify("python tools/run_cases.py ag_1 --brief A") == [("TEST", "practice simulation")]
    assert run.classify("python -m builder.practice ag_1 --brief A")[0][0] == "TEST"
    assert run.classify("python -m builder.chat_test wf_1 --vars v.json hi")[0][0] == "TEST"
    assert run.classify("python -c \"import freya; freya.save_workflow('brief-a', w)\"")[0][0] == "FREYA"
    assert run.classify("python -m builder.validate out/a/workflow.json tools/openapi.json") == []
    assert run.classify("ls out") == []


def test_classify_lists_every_action_of_a_combined_command():
    c = ("P=py; $P -c \"import freya; freya.validate_workflow('dry', w)\" && "
         "$P -c \"import freya; freya.save_workflow('s', w)\" && $P -m builder.practice ag --brief A")
    assert [k for k, _ in run.classify(c)] == ["FREYA", "FREYA", "TEST"]


def _gate(minutes_left=25, max_sims=2):
    now = datetime.datetime(2026, 10, 11, 10, 0)
    return run.Gate("slot-a", max_sims, now + datetime.timedelta(minutes=minutes_left), now=lambda: now)


def test_gate_limits_practice_runs():
    g = _gate()
    assert g.decide("py -m builder.practice ag --brief A") is None
    assert g.decide("py tools/run_cases.py ag --brief A --style pieces") is None
    assert "at most 2" in g.decide("py -m builder.practice ag --brief A")
    assert g.decide("py -m builder.validate out/slot-a/x.json tools/openapi.json") is None   # local work stays allowed


def test_gate_refuses_a_run_that_cannot_finish():
    assert "minutes left" in _gate(minutes_left=12).decide("py -m builder.practice ag --brief A")
    assert _gate(minutes_left=14).decide("py -m builder.practice ag --brief A") is None


def test_gate_keeps_workflows_to_the_slot():
    g = _gate()
    assert g.decide("py -c \"import freya; freya.save_workflow(sys.argv[1], w)\" slot-a out/slot-a/w.json") is None
    assert "slot" in g.decide("py -c \"import freya; freya.save_workflow('other', w)\"")
    assert "create" in g.decide("py -c \"import freya; freya.create_workflow('x', w)\"")


def test_trace_file(tmp_path):
    t = Trace(tmp_path / "t.txt")
    t.start(briefs="a.md")
    t.model_call("claude-opus-5-5", "Read the brief\nand plan")
    t.freya("save workflow")
    t.test("practice simulation 9/10")
    t.end(status="ok")
    text = (tmp_path / "t.txt").read_text(encoding="utf-8")
    lines = text.splitlines()
    assert lines[0].split()[3] == "START" or "START" in lines[0]
    assert "MODEL" in lines[1] and "#1 claude-opus-5-5: Read the brief and plan" in lines[1]
    assert "END" in lines[-1] and "model_calls=1" in lines[-1]


def _iso_gate():
    now = datetime.datetime(2026, 10, 11, 10, 0)
    return run.Gate("fix2-a", 2, now + datetime.timedelta(minutes=25), now=lambda: now,
                    briefs=[run.KIT / "briefs" / "brief-a.md"])


def test_isolation_refuses_other_slots_in_any_path_form():
    g = _iso_gate()
    kit = run.KIT.as_posix()
    bash_kit = "/" + kit[0].lower() + kit[2:]
    for cmd in ("cat out/baseline-a-20261010/build_workflow.py",
                f"cat {kit}/out/baseline-a/build_workflow.py",
                f"cat {bash_kit}/out/baseline-a/rules.md",
                "type " + str(run.KIT / "out" / "other" / "rules.md"),
                "cat out/*/build_workflow.py",
                "ls out/",
                "cp out/old/build_workflow.py out/fix2-a/build_workflow.py"):
        assert g.decide(cmd) and "not this build's folder" in g.decide(cmd), cmd


def test_isolation_allows_own_slot_and_kit_files():
    g = _iso_gate()
    for cmd in ("cat out/fix2-a/build_workflow.py",
                f"cd {run.KIT.as_posix()} && py out/fix2-a/build_workflow.py && py -m builder.validate out/fix2-a/workflow.json tools/openapi.json",
                "cat briefs/brief-a.md docs/api.md examples/base-agent.json",
                "ls out",
                "py -m builder.practice ag --brief A --out results/fix2-a-1.json",
                "cat results/fix2-a-1.json"):
        assert g.decide(cmd) is None, cmd
    assert g.decide_tool("Read", {"file_path": str(run.KIT / "out" / "fix2-a" / "rules.md")}) is None
    assert g.decide_tool("Grep", {"pattern": "out/anything", "path": str(run.KIT / "builder")}) is None


def test_isolation_refuses_traces_results_practice_file_and_other_briefs():
    g = _iso_gate()
    assert "traces" in g.decide("tail traces/20261010-160327.txt")
    assert "results/fix2-a-*" in g.decide("cat results/baseline-a-20261010-1.json")
    assert "results/fix2-a-*" in g.decide("cat results/log.jsonl")
    assert "public-test-set.json" in g.decide("py -c \"import json; json.load(open('public-test-set.json'))\"")
    assert "brief-a.md" in g.decide("cat briefs/brief-b.md")
    assert g.decide_tool("Read", {"file_path": str(run.KIT / "public-test-set.json")})
    assert g.decide_tool("Read", {"file_path": str(run.KIT / "out" / "baseline-a-20261010" / "build_workflow.py")})
    assert g.decide_tool("Glob", {"pattern": "out/**/*.py"})
    assert g.decide_tool("Grep", {"pattern": "x", "path": str(run.KIT / "traces")})


def test_isolation_with_briefs_outside_the_kit_does_not_lock_kit_briefs_out_of_nothing():
    now = datetime.datetime(2026, 10, 11, 10, 0)
    g = run.Gate("final-1", 2, now + datetime.timedelta(minutes=25), now=lambda: now, briefs=["C:/elsewhere/final.md"])
    assert g.decide("cat C:/elsewhere/final.md") is None
    assert g.decide("cat out/final-1/rules.md") is None


def test_isolation_does_not_trip_on_ordinary_words():
    g = _iso_gate()
    assert g.decide("echo 'stack traces here'; echo results are in") is None
    assert g.decide("ls traces") and g.decide("cat traces/x.txt")
