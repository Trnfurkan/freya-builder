from builder import snippets

CODE = snippets.post_result_code(
    on_success="Tell the customer it is recorded.",
    on_failure="Say it could not be recorded. Try once more.",
    when=[["hint", "day_month_swapped", "Ask for the month as a word."], ["verified", "false", "Ask the same question again."]],
)


def env(data, status="success", code=200):
    return {"status": status, "data": data, "message": "x", "status_code": code, "header": {"Content-Type": "application/json"}}


def test_success_adds_instruction_and_drops_headers():
    r = snippets.run_post_hook(CODE, env({"ok": True, "promise_id": "pr1"}))
    assert "header" not in r
    assert r["data"]["what_to_do_next"] == "Tell the customer it is recorded."
    assert r["data"]["promise_id"] == "pr1"


def test_ok_false_is_failure():
    r = snippets.run_post_hook(CODE, env({"ok": False, "error": "service down"}))
    assert r["data"]["what_to_do_next"].startswith("Say it could not be recorded")


def test_http_error_envelope_is_failure():
    r = snippets.run_post_hook(CODE, env("<html>500</html>", status="error", code=500))
    assert r["data"]["ok"] is False
    assert "500" in r["data"]["error"]
    assert r["data"]["what_to_do_next"].startswith("Say it could not be recorded")


def test_field_rules_win_and_compare_booleans_loosely():
    r = snippets.run_post_hook(CODE, env({"ok": True, "verified": False, "hint": "day_month_swapped"}))
    assert r["data"]["what_to_do_next"] == "Result: verified is FALSE. Ask for the month as a word."
    r = snippets.run_post_hook(CODE, env({"ok": True, "verified": "False"}))
    assert r["data"]["what_to_do_next"] == "Result: verified is FALSE. Ask the same question again."


def test_false_fields_are_named_even_without_a_rule():
    # The first practice build said "correct" to a wrong security answer: ok was true, verified was false.
    code = snippets.post_result_code(on_success="Correct. Ask the next question.", on_failure="Failed.")
    r = snippets.run_post_hook(code, env({"ok": True, "verified": False, "all_verified": False, "wrong_answers_left": 2}))
    assert r["data"]["what_to_do_next"].startswith("Result: verified is FALSE, all_verified is FALSE.")
    off = snippets.post_result_code(on_success="Correct.", flag_false=False)
    assert snippets.run_post_hook(off, env({"ok": True, "verified": False}))["data"]["what_to_do_next"] == "Correct."


def test_guard_reason_wins_and_is_cleared():
    code = snippets.post_result_code(on_success="Recorded.", on_failure="Failed, retry once.", guard_var="promise_guard")
    v = {"promise_guard": "NOT RECORDED, blocked before sending: date 2026-10-26 cannot be recorded."}
    r = snippets.run_post_hook(code, env({"ok": False, "error": "unknown product_id"}), v)
    assert r["data"]["what_to_do_next"].startswith("NOT RECORDED, blocked before sending: date 2026-10-26")
    assert "not a service failure" in r["data"]["what_to_do_next"]
    assert v["promise_guard"] == ""
    r = snippets.run_post_hook(code, env({"ok": False, "error": "down"}), v)
    assert r["data"]["what_to_do_next"] == "Failed, retry once."


def test_set_variables_after_the_call():
    code = snippets.post_result_code(on_success="Bye.", set_on_success={"call_closed": "yes"}, set_on_any={"close_tried": "yes"})
    v = {}
    snippets.run_post_hook(code, env({"ok": False}), v)
    assert v == {"close_tried": "yes"}
    snippets.run_post_hook(code, env({"ok": True}), v)
    assert v == {"close_tried": "yes", "call_closed": "yes"}


def test_odd_results_do_not_raise():
    assert snippets.run_post_hook(CODE, None) is None
    assert snippets.run_post_hook(CODE, "text") == "text"


def test_failure_counter_switches_to_the_final_instruction():
    code = snippets.post_result_code(on_success="Recorded.", on_failure="Call it once more with the same values.",
                                     fail_count_var="promise_failures", fail_limit=2,
                                     on_failure_final="Say you could not record it and close with service_error.",
                                     guard_var="promise_guard")
    v = {}
    first = snippets.run_post_hook(code, env({"ok": False, "error": "service unavailable"}), v)
    assert first["data"]["what_to_do_next"] == "Failed attempt 1. Call it once more with the same values."
    second = snippets.run_post_hook(code, env({"ok": False, "error": "service unavailable"}), v)
    assert second["data"]["what_to_do_next"] == "Say you could not record it and close with service_error."
    assert v["promise_failures"] == 2


def test_a_blocked_request_is_not_counted_as_a_failure():
    code = snippets.post_result_code(on_failure="Retry.", fail_count_var="n", on_failure_final="Give up.",
                                     guard_var="promise_guard")
    v = {"promise_guard": "NOT RECORDED, blocked before sending: date not allowed."}
    snippets.run_post_hook(code, env({"ok": False}), v)
    assert "n" not in v
    r = snippets.run_post_hook(code, env({"ok": False}), v)
    assert v["n"] == 1 and r["data"]["what_to_do_next"] == "Failed attempt 1. Retry."
