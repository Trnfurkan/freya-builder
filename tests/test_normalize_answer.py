import pytest

from builder import snippets

PARAMS = {"field_key": "field", "answer_key": "answer", "date_fields": ["date_of_birth"], "day_first": True}
CODE = snippets.normalize_answer_code(PARAMS)


def norm(field, answer, code=CODE):
    return snippets.run_pre_hook(code, {"customer_id": "c1", "field": field, "answer": answer})["answer"]


@pytest.mark.parametrize("said", [
    "1988-03-14", "14 March 1988", "14th March 1988", "March 14, 1988", "the 14th of March 1988",
    "fourteenth of March nineteen eighty-eight", "the fourteenth of March, one nine eight eight",
    "14/03/1988", "14.03.1988", "14-3-1988", "14 Mar 1988", "fourteen March nineteen eighty eight",
])
def test_dates_to_iso(said):
    assert norm("date_of_birth", said) == "1988-03-14"


@pytest.mark.parametrize("said, iso", [
    ("the twenty-second of October two thousand and one", "2001-10-22"),
    ("2 September 1979", "1979-09-02"),
    ("first of February nineteen oh five", "1905-02-01"),
    ("thirtieth of January nineteen eighty-four", "1984-01-30"),
    ("23 Feb 1989", "1989-02-23"),
])
def test_more_dates(said, iso):
    assert norm("date_of_birth", said) == iso


def test_numeric_date_is_day_first_by_brief():
    assert norm("date_of_birth", "03/05/1990") == "1990-05-03"
    us = snippets.normalize_answer_code({**PARAMS, "day_first": False})
    assert norm("date_of_birth", "03/05/1990", us) == "1990-03-05"


@pytest.mark.parametrize("said", ["14/13/1988", "sometime in spring", "", "31 February 1990"])
def test_unreadable_dates_are_left_for_the_service(said):
    assert norm("date_of_birth", said) == said


@pytest.mark.parametrize("said", ["4471", "4 4 7 1", "44 71", "four four seven one", "forty-four seventy-one",
                                  "double four seven one", "It's 4471", "four, four, seven, one"])
def test_spoken_digits(said):
    assert norm("phone_last4", said) == "4471"


def test_leading_zero_kept():
    assert norm("phone_last4", "oh three five eight") == "0358"
    assert norm("phone_last4", "zero three five eight") == "0358"


@pytest.mark.parametrize("said", ["SW1A 1AA", "the last four digits are 4471", "I don't know"])
def test_other_answers_untouched(said):
    assert norm("phone_last4", said) == said


def test_numeric_fields_limit():
    code = snippets.normalize_answer_code({**PARAMS, "numeric_fields": ["phone_last4"]})
    assert norm("postcode", "one two three", code) == "one two three"
    assert norm("phone_last4", "one two three four", code) == "1234"


def test_never_raises_on_odd_bodies():
    assert snippets.run_pre_hook(CODE, {"field": "date_of_birth"}) == {"field": "date_of_birth"}
    assert snippets.run_pre_hook(CODE, {"field": "date_of_birth", "answer": None})["answer"] is None
    assert snippets.run_pre_hook(CODE, {"field": "x", "answer": {"a": 1}})["answer"] == {"a": 1}
    config = {"body": "raw text"}
    exec(CODE, {"vars": {}, "config": config})
    assert config["body"] == "raw text"


def test_no_double_braces():
    assert "{{" not in CODE and "}}" not in CODE
