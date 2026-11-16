import copy
import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from schemas.base import Duration
from schemas.customer import CustomerWebhook
from schemas.price import PriceWebhook
from schemas.product import ProductWebhook
from schemas.transaction import TransactionWebhook

FIXTURES = Path(__file__).parent / "fixtures"


def load(name: str) -> dict:
    return json.loads((FIXTURES / f"{name}.json").read_text())


# --- Envelope ---------------------------------------------------------------------------------


def test_envelope_rejects_bad_event_id():
    payload = load("customer.updated")
    payload["event_id"] = "evt_123"
    with pytest.raises(ValidationError):
        CustomerWebhook.model_validate(payload)


def test_unknown_fields_are_ignored():
    payload = load("customer.updated")
    payload["brand_new_field"] = {"x": 1}
    payload["data"]["another_one"] = True
    assert CustomerWebhook.model_validate(payload).data.id == "ctm_01hv6y1jedq4p1n0yqn5ba3ky4"


# --- Product ----------------------------------------------------------------------------------


def test_product():
    product = ProductWebhook.model_validate(load("product.updated")).data
    assert product.id == "pro_01gsz97mq9pa4fkyy0wqenepkz"
    assert product.custom_data.bundle_id == "com.fcpxstudio.myplugin"


@pytest.mark.parametrize("custom_data", [None, {}, {"bundle_id": "not a bundle id"}])
def test_product_requires_bundle_id(custom_data):
    payload = load("product.updated")
    payload["data"]["custom_data"] = custom_data
    with pytest.raises(ValidationError):
        ProductWebhook.model_validate(payload)


# --- Customer ---------------------------------------------------------------------------------


def test_customer():
    customer = CustomerWebhook.model_validate(load("customer.updated")).data
    assert customer.email == "jo@example.com"  # trimmed and lowercased
    assert customer.name == "Jo Brown-Anderson"
    assert customer.marketing_consent is False


def test_customer_name_can_be_null():
    payload = load("customer.updated")
    payload["data"]["name"] = None
    assert CustomerWebhook.model_validate(payload).data.name is None


# --- Price ------------------------------------------------------------------------------------


def test_price_one_time():
    price = PriceWebhook.model_validate(load("price.updated")).data
    assert price.unit_price.amount == 4900  # "4900" -> int cents
    assert price.unit_price.currency_code == "USD"
    assert price.is_trial is False
    assert price.lifetime is None  # perpetual


def test_price_trial():
    payload = load("price.updated")
    payload["data"]["billing_cycle"] = {"interval": "year", "frequency": 1}
    payload["data"]["trial_period"] = {"interval": "day", "frequency": 14, "requires_payment_method": False}
    price = PriceWebhook.model_validate(payload).data
    assert price.is_trial is True
    assert price.lifetime == Duration(interval="day", frequency=14)


def test_price_rejects_negative_amount():
    payload = load("price.updated")
    payload["data"]["unit_price"]["amount"] = "-1"
    with pytest.raises(ValidationError):
        PriceWebhook.model_validate(payload)


# --- Transaction ------------------------------------------------------------------------------


def test_transaction():
    transaction = TransactionWebhook.model_validate(load("transaction.completed")).data
    assert transaction.id == "txn_01hv8y1z2x3w4v5u6t7s8r9q0p"
    assert transaction.customer_id == "ctm_01hv6y1jedq4p1n0yqn5ba3ky4"
    assert transaction.details.totals.grand_total == 10780
    assert transaction.is_purchase is True
    [item] = transaction.details.line_items
    assert item.id == "txnitm_01hv8y1z2x3w4v5u6t7s8r9q0p"
    assert item.price_id == "pri_01gsz8x8sawmvhz1pv30nge1ke"
    assert item.quantity == 2


def test_transaction_renewal_is_not_a_purchase():
    payload = load("transaction.completed")
    payload["data"]["origin"] = "subscription_recurring"
    assert TransactionWebhook.model_validate(payload).data.is_purchase is False


@pytest.mark.parametrize(
    "mutate",
    [
        pytest.param(lambda d: d.update(status="paid"), id="not completed"),
        pytest.param(lambda d: d.update(customer_id=None), id="no customer"),
        pytest.param(lambda d: d.update(id="ord_01hv8y1z2x3w4v5u6t7s8r9q0p"), id="wrong id prefix"),
        pytest.param(lambda d: d["details"].update(line_items=[]), id="no line items"),
        pytest.param(lambda d: d["details"]["line_items"][0].update(quantity=0), id="quantity 0"),
    ],
)
def test_transaction_invalid(mutate):
    payload = load("transaction.completed")
    mutate(payload["data"])
    with pytest.raises(ValidationError):
        TransactionWebhook.model_validate(payload)


def test_transaction_rejects_duplicate_price():
    payload = load("transaction.completed")
    items = payload["data"]["details"]["line_items"]
    second = copy.deepcopy(items[0])
    second["id"] = "txnitm_01hv8y1z2x3w4v5u6t7s8r9q0z"
    items.append(second)
    with pytest.raises(ValidationError, match="several line items"):
        TransactionWebhook.model_validate(payload)
