"""Turkish answers: the final case's documents are in Turkish, so customers may answer in Turkish."""
import pytest

from tests.test_normalize_answer import norm


@pytest.mark.parametrize("said", [
    "14 Mart 1988", "14 mart 1988", "on dört mart bin dokuz yüz seksen sekiz", "On Dört Mart 1988",
    "Doğum tarihim 14 Mart 1988", "mart ayının on dördü, 1988", "14.03.1988", "on dördünde mart 1988",
])
def test_turkish_dates(said):
    assert norm("date_of_birth", said) == "1988-03-14"


@pytest.mark.parametrize("said, iso", [
    ("21 Ağustos 1992", "1992-08-21"),
    ("yirmi bir ağustos bin dokuz yüz doksan iki", "1992-08-21"),
    ("5 Şubat 2001", "2001-02-05"),
    ("beş şubat iki bin bir", "2001-02-05"),
    ("30 Aralık 1975", "1975-12-30"),
    ("9 Eylül 1990", "1990-09-09"),
    ("1 Kasım 1980", "1980-11-01"),
    ("12 Temmuz 1991, pardon, 21 Temmuz 1992", "1992-07-21"),
    ("12 Temmuz 1991 yani 21 Temmuz 1992", "1992-07-21"),
])
def test_turkish_dates_more(said, iso):
    assert norm("date_of_birth", said) == iso


@pytest.mark.parametrize("said, digits", [
    ("dört dört yedi bir", "4471"),
    ("kırk dört yetmiş bir", "4471"),
    ("sıfır beş", "05"),
    ("4471", "4471"),
    ("yedi sıfır dokuz üç", "7093"),
])
def test_turkish_digits(said, digits):
    assert norm("phone_last4", said) == digits


@pytest.mark.parametrize("said", ["born on 14 March 1988", "on March 14, 1988", "March fourteenth, 1988"])
def test_english_on_is_not_ten(said):
    assert norm("date_of_birth", said) == "1988-03-14"


def test_unreadable_turkish_stays_as_said():
    assert norm("date_of_birth", "hatırlamıyorum") == "hatırlamıyorum"
