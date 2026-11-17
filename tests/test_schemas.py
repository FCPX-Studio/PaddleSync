import copy
import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from schemas.adjustment import Adjustment
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


def test_product_version():
    payload = load("product.updated")
    assert ProductWebhook.model_validate(payload).data.custom_data.version == "1.0.0"
    payload["data"]["custom_data"]["version"] = " 1.2.0 "
    assert ProductWebhook.model_validate(payload).data.custom_data.version == "1.2.0"  # trimmed


@pytest.mark.parametrize("version", ["", "   ", "x" * 33, None])
def test_product_invalid_version(version):
    payload = load("product.updated")
    payload["data"]["custom_data"]["version"] = version
    with pytest.raises(ValidationError):
        ProductWebhook.model_validate(payload)


def test_product_requires_version():
    payload = load("product.updated")
    del payload["data"]["custom_data"]["version"]
    with pytest.raises(ValidationError):
        ProductWebhook.model_validate(payload)


@pytest.mark.parametrize(
    "custom_data", [None, {}, {"version": "1.0.0"}, {"bundle_id": "not a bundle id", "version": "1.0.0"}]
)
def test_product_requires_bundle_id(custom_data):
    payload = load("product.updated")
    payload["data"]["custom_data"] = custom_data
    with pytest.raises(ValidationError):
        ProductWebhook.model_validate(payload)


def test_product_catalog_fields():
    product = ProductWebhook.model_validate(load("product.updated")).data
    assert product.name == "My Plugin"
    assert product.custom_data.type == "plugin"
    assert product.custom_data.download == "myplugin/MyPlugin-1.0.0.pkg"


@pytest.mark.parametrize("key", ["type", "download"])
def test_product_requires_catalog_keys(key):
    payload = load("product.updated")
    del payload["data"]["custom_data"][key]
    with pytest.raises(ValidationError):
        ProductWebhook.model_validate(payload)


@pytest.mark.parametrize("product_type", ["standard", "Plugin", "", None])
def test_product_invalid_type(product_type):
    payload = load("product.updated")
    payload["data"]["custom_data"]["type"] = product_type
    with pytest.raises(ValidationError):
        ProductWebhook.model_validate(payload)


@pytest.mark.parametrize("download", ["MyPlugin.pkg", "a/b/My_Plugin-1.0.pkg", " myplugin/x.pkg "])
def test_product_valid_download(download):
    payload = load("product.updated")
    payload["data"]["custom_data"]["download"] = download
    assert ProductWebhook.model_validate(payload).data.custom_data.download == download.strip()


@pytest.mark.parametrize(
    "download",
    ["", "/myplugin/x.pkg", "myplugin/", "a//b.pkg", "../x.pkg", "a/../b.pkg", "./x.pkg", "a b.pkg",
     "https://x.com/a.pkg", "a/" + "x" * 511, None],
)
def test_product_invalid_download(download):
    payload = load("product.updated")
    payload["data"]["custom_data"]["download"] = download
    with pytest.raises(ValidationError):
        ProductWebhook.model_validate(payload)


@pytest.mark.parametrize("name", ["", "   ", "x" * 256, None])
def test_product_invalid_name(name):
    payload = load("product.updated")
    payload["data"]["name"] = name
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
    assert price.custom_data is None  # null in the fixture
    assert price.is_trial is False
    assert price.duration_days is None  # perpetual


def test_price_name_and_description():
    price = PriceWebhook.model_validate(load("price.updated")).data
    assert (price.name, price.description) == ("Lifetime", "Lifetime license")


@pytest.mark.parametrize("description", ["", "   ", None])
def test_price_empty_description_is_none(description):
    payload = load("price.updated")
    payload["data"]["description"] = description
    assert PriceWebhook.model_validate(payload).data.description is None


@pytest.mark.parametrize("name", ["", "   ", "x" * 256, None])
def test_price_requires_name(name):
    payload = load("price.updated")
    payload["data"]["name"] = name
    with pytest.raises(ValidationError):
        PriceWebhook.model_validate(payload)


def price_with(custom_data) -> dict:
    payload = load("price.updated")
    payload["data"]["custom_data"] = custom_data
    return payload


def test_price_trial():
    price = PriceWebhook.model_validate(price_with({"trial": True, "duration": 14})).data
    assert price.is_trial is True
    assert price.duration_days == 14


def test_price_paid_with_duration():
    price = PriceWebhook.model_validate(price_with({"duration": 365})).data
    assert price.is_trial is False
    assert price.duration_days == 365


@pytest.mark.parametrize("custom_data", [{}, {"other": "key"}, {"trial": False}])
def test_price_without_terms_is_perpetual(custom_data):
    price = PriceWebhook.model_validate(price_with(custom_data)).data
    assert (price.is_trial, price.duration_days) == (False, None)


def test_price_native_trial_period_is_ignored():
    payload = price_with(None)
    payload["data"]["billing_cycle"] = {"interval": "year", "frequency": 1}
    payload["data"]["trial_period"] = {"interval": "day", "frequency": 14, "requires_payment_method": False}
    price = PriceWebhook.model_validate(payload).data
    assert (price.is_trial, price.duration_days) == (False, None)


@pytest.mark.parametrize(
    "custom_data",
    [
        pytest.param({"trial": True}, id="trial without duration"),
        pytest.param({"trial": True, "duration": None}, id="trial with null duration"),
        pytest.param({"duration": 0}, id="zero days"),
        pytest.param({"duration": -7}, id="negative"),
        pytest.param({"duration": "two weeks"}, id="not a number"),
        pytest.param({"duration": 1.5}, id="fractional days"),
    ],
)
def test_price_invalid_terms(custom_data):
    with pytest.raises(ValidationError):
        PriceWebhook.model_validate(price_with(custom_data))


@pytest.mark.parametrize(
    "custom_data, seats",
    [
        pytest.param(None, 2, id="no custom_data: default"),
        pytest.param({"duration": 365}, 2, id="paid without seats: default"),
        pytest.param({"seats": 3}, 3, id="paid with seats"),
        pytest.param({"duration": 365, "seats": 1}, 1, id="single seat"),
        pytest.param({"trial": True, "duration": 14}, 1, id="trial"),
        pytest.param({"trial": True, "duration": 14, "seats": 5}, 1, id="trial ignores seats"),
    ],
)
def test_price_activation_seats(custom_data, seats):
    assert PriceWebhook.model_validate(price_with(custom_data)).data.activation_seats == seats


@pytest.mark.parametrize("seats", [0, -1, "x", 1.5, None])
def test_price_invalid_seats(seats):
    with pytest.raises(ValidationError):
        PriceWebhook.model_validate(price_with({"seats": seats}))


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


# --- Archived status / updated_at (stale guard) -----------------------------------------------


@pytest.mark.parametrize(
    "name, webhook",
    [
        ("customer.updated", CustomerWebhook),
        ("product.updated", ProductWebhook),
        ("price.updated", PriceWebhook),
    ],
)
def test_status_and_updated_at(name, webhook):
    payload = load(name)
    entity = webhook.model_validate(payload).data
    assert entity.status == "active"
    assert entity.updated_at.tzinfo is not None
    payload["data"]["status"] = "archived"
    assert webhook.model_validate(payload).data.status == "archived"


@pytest.mark.parametrize("name", ["customer.updated", "product.updated", "price.updated"])
def test_updated_at_is_required(name):
    payload = load(name)
    del payload["data"]["updated_at"]
    with pytest.raises(ValidationError):
        WEBHOOKS[name].model_validate(payload)


WEBHOOKS = {"customer.updated": CustomerWebhook, "product.updated": ProductWebhook, "price.updated": PriceWebhook}


# --- Adjustment -------------------------------------------------------------------------------
# Paddle's own sample (Simulations API): a pending partial refund. Validated as the entity,
# like handlers do (the simulated envelope has ntfsimevt_ ids).


def adjustment_data(**overrides) -> dict:
    data = load("adjustment.updated")["data"]
    data.update(overrides)
    return data


def test_adjustment_sample_revokes_nothing():
    adjustment = Adjustment.model_validate(adjustment_data())
    assert adjustment.id == "adj_01hvgf2s84dr6reszzg29zbvcm"
    assert adjustment.transaction_id == "txn_01hvcc93znj3mpqt1tenkjb04y"
    assert adjustment.refund_reason is None  # pending_approval
    assert adjustment.full_item_ids == []  # partial


@pytest.mark.parametrize("action", ["refund", "chargeback"])
def test_adjustment_approved_full_revokes(action):
    data = adjustment_data(action=action, status="approved")
    data["items"][0]["type"] = "full"
    adjustment = Adjustment.model_validate(data)
    assert adjustment.refund_reason == action
    assert adjustment.full_item_ids == ["txnitm_01hvcc94b7qgz60qmrqmbm19zw"]


@pytest.mark.parametrize(
    "overrides",
    [
        pytest.param({"action": "credit", "status": "approved"}, id="credit"),
        pytest.param({"action": "chargeback_reverse", "status": "approved"}, id="chargeback reverse"),
        pytest.param({"action": "chargeback_warning", "status": "approved"}, id="chargeback warning"),
        pytest.param({"action": "refund", "status": "rejected"}, id="rejected"),
        pytest.param({"action": "refund", "status": "pending_approval"}, id="pending"),
    ],
)
def test_adjustment_not_revoking(overrides):
    data = adjustment_data(**overrides)
    data["items"][0]["type"] = "full"
    assert Adjustment.model_validate(data).refund_reason is None


def test_adjustment_full_items_only_and_deduplicated():
    data = adjustment_data(action="refund", status="approved")
    full = {**data["items"][0], "type": "full", "item_id": "txnitm_01hvcc94b7qgz60qmrqmbm1aaa"}
    tax = {**data["items"][0], "type": "tax", "item_id": "txnitm_01hvcc94b7qgz60qmrqmbm1bbb"}
    data["items"] = [full, data["items"][0], tax, dict(full)]
    assert Adjustment.model_validate(data).full_item_ids == ["txnitm_01hvcc94b7qgz60qmrqmbm1aaa"]


@pytest.mark.parametrize(
    "mutate",
    [
        pytest.param(lambda d: d.update(transaction_id="txn_123"), id="bad transaction id"),
        pytest.param(lambda d: d.update(items=[]), id="no items"),
        pytest.param(lambda d: d["items"][0].update(item_id="pri_01hvcc94b7qgz60qmrqmbm19zw"), id="bad item id"),
    ],
)
def test_adjustment_invalid(mutate):
    data = adjustment_data()
    mutate(data)
    with pytest.raises(ValidationError):
        Adjustment.model_validate(data)
