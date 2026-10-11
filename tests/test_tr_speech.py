"""Turkish speech forms (sandbox/tr_speech.py): amounts in words or digits, dates, digits one by one. The tool answer
is read, never changed."""
import copy

import pytest

from builder import snippets


def say(fields, body, variables=None, **params):
    code = snippets.tr_speech_code(dict(params, fields=fields))
    result = {"status": "success", "data": body}
    before = copy.deepcopy(result)
    v = dict(variables or {})
    after = snippets.run_post_hook(code, result, v)
    assert after == before           # the bank's answer is untouched
    return v


@pytest.mark.parametrize("value, currency, spoken", [
    (12000.0, "TL", "on iki bin Türk Lirası"),
    (5000.13, "TL", "beş bin Türk Lirası on üç kuruş"),
    (1234567.89, "TL", "bir milyon iki yüz otuz dört bin beş yüz altmış yedi Türk Lirası seksen dokuz kuruş"),
    (2500, "TL", "iki bin beş yüz Türk Lirası"),
    (1000, "TL", "bin Türk Lirası"),
    (100.5, "TL", "yüz Türk Lirası elli kuruş"),
    (85.2, "EUR", "seksen beş Euro yirmi sent"),
    (650, "USD", "altı yüz elli Dolar"),
    (12.05, "GBP", "on iki İngiliz Sterlini beş peni"),
    (0.0, "TL", "sıfır Türk Lirası"),
    (-150.0, "TL", "eksi yüz elli Türk Lirası"),
])
def test_amount_words(value, currency, spoken):
    v = say([{"var": "s", "path": "data.x", "kind": "amount", "currency": currency}], {"data": {"x": value}})
    assert v["s"] == spoken


@pytest.mark.parametrize("value, currency, spoken", [
    (12000.0, "TL", "12.000 Türk Lirası"),
    (85.2, "EUR", "85,20 Euro"),
    (1234567.89, "USD", "1.234.567,89 Dolar"),
])
def test_amount_digits(value, currency, spoken):
    v = say([{"var": "s", "path": "data.x", "kind": "amount_digits", "currency": currency}], {"data": {"x": value}})
    assert v["s"] == spoken


def test_units_can_follow_the_brief():
    v = say([{"var": "s", "path": "x", "kind": "amount", "currency": "TL"}], {"x": 1200.5},
            units={"TL": ["lira", "kuruş"]})
    assert v["s"] == "bin iki yüz lira elli kuruş"


def test_dates_and_today():
    body = {"data": {"a": "05.10.2026", "b": "12.10.2026", "c": "2026-11-26", "d": "bad"}}
    v = say([{"var": "a", "path": "data.a", "kind": "date"}, {"var": "b", "path": "data.b", "kind": "date"},
             {"var": "c", "path": "data.c", "kind": "date"}, {"var": "d", "path": "data.d", "kind": "date"}],
            body, {"now": "2026-10-12T10:30:00+03:00"})
    assert (v["a"], v["b"], v["c"], v["d"]) == ("5 Ekim 2026", "bugün", "26 Kasım 2026", "")


@pytest.mark.parametrize("value, spoken", [("4253", "dört iki beş üç"), ("1003", "bir sıfır sıfır üç"),
                                           ("540000******1234", "beş dört sıfır sıfır sıfır sıfır bir iki üç dört")])
def test_digits_one_by_one(value, spoken):
    assert say([{"var": "s", "path": "x", "kind": "digits"}], {"x": value})["s"] == spoken


def test_missing_values_give_nothing():
    v = say([{"var": "gbp", "path": "data.debtGBP.statementDebt", "kind": "amount", "currency": "GBP"},
             {"var": "n", "path": "data.cards.0.cardNumberLast4", "kind": "digits"}],
            {"data": {"debtTL": {"statementDebt": 1}, "cards": [{"cardNumberLast4": "1234"}]}})
    assert v["gbp"] == "" and v["n"] == "bir iki üç dört"


def test_code_node_reads_variables():
    code = snippets.tr_speech_code({"source": "vars", "fields": [
        {"var": "say_due", "path": "due_date", "kind": "date"},
        {"var": "say_min", "path": "tl_min", "kind": "amount", "currency": "TL"}]})
    v = snippets.run_code_node(code, {"due_date": "15.10.2026", "tl_min": "4800.0", "now": "2026-10-12T10:30:00+03:00"})
    assert v["say_due"] == "15 Ekim 2026" and v["say_min"] == "dört bin sekiz yüz Türk Lirası"


def test_chains_with_post_result():
    code = snippets.chain(snippets.tr_speech_code({"fields": [{"var": "s", "path": "data.x", "kind": "number"}]}),
                          snippets.post_result_code("Read it.", "Failed."))
    result, v = {"status": "success", "data": {"data": {"x": 12}}}, {}
    snippets.run_post_hook(code, result, v)
    assert v["s"] == "on iki"
