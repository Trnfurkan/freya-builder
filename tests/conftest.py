import json
import pathlib
import sys

import pytest

KIT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(KIT))


@pytest.fixture
def openapi():
    return json.loads((KIT / "tools" / "openapi.json").read_text(encoding="utf-8"))


# Test fixture only: Brief A's calendar and bands, written the way the builder agent extracts them.
# The builder itself never contains these values.
@pytest.fixture
def params_a():
    return {
        "now_var": "now",
        "weekend": [5, 6],
        "holidays": ["2026-10-26", "2026-11-11", "2026-11-26", "2026-12-25", "2027-01-01"],
        "bands": [
            {"name": "early", "min_days_late": 1, "max_days_late": 7, "deadline_business_days": 12, "attempts": 1},
            {"name": "late", "min_days_late": 8, "max_days_late": 45, "deadline_business_days": 7, "attempts": 2},
        ],
        "products_var": "products",
        "product_fields": {"id": "product_id", "type": "type", "days_late": "days_late", "amount": "past_due"},
        "floor_field_by_type": {"credit_card": "minimum_payment", "personal_loan": "instalment_amount", "car_loan": "instalment_amount"},
    }


def no_attempts(params):
    """The policy without persuasion attempts: for tests about other features. With attempts, the validator requires
    the offer gate (tested in test_offer_gate.py)."""
    return {**params, "bands": [{**band, "attempts": 0} for band in params["bands"]]}
