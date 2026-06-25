"""Uppercase address and care_of fields after OCR read and submit."""

from app.services.customer_address_infer import (
    uppercase_care_of_value,
    uppercase_customer_address_fields,
    uppercase_customer_care_of_field,
    uppercase_customer_name_field,
)
from app.services.sales_ocr_service import _apply_initcap_on_read


def test_uppercase_care_of_value():
    assert uppercase_care_of_value("s/o ram singh") == "S/O RAM SINGH"


def test_uppercase_customer_care_of_field():
    customer = {"care_of": "d/o priya devi"}
    uppercase_customer_care_of_field(customer)
    assert customer["care_of"] == "D/O PRIYA DEVI"


def test_uppercase_customer_name_field():
    customer = {"name": "ram singh"}
    uppercase_customer_name_field(customer)
    assert customer["name"] == "RAM SINGH"


def test_uppercase_customer_address_fields_both_lines():
    customer = {
        "house": "ward 5",
        "street": "main road",
        "location": "near post office",
        "district": "bharatpur",
        "sub_district": "nadbai",
        "post_office": "dhanota",
        "address": "ward 5, main road, near temple",
        "city": "bharatpur",
        "state": "rajasthan",
    }
    uppercase_customer_address_fields(customer)
    assert customer["house"] == "WARD 5"
    assert customer["street"] == "MAIN ROAD"
    assert customer["location"] == "NEAR POST OFFICE"
    assert customer["district"] == "BHARATPUR"
    assert customer["sub_district"] == "NADBAI"
    assert customer["post_office"] == "DHANOTA"
    assert customer["address"] == "WARD 5, MAIN ROAD, NEAR TEMPLE"
    assert customer["city"] == "BHARATPUR"
    assert customer["state"] == "RAJASTHAN"


def test_apply_initcap_on_read_uppercases_address_and_care_of():
    data = {
        "customer": {
            "name": "ram singh",
            "care_of": "s/o mohan lal",
            "house": "house no 12",
            "address": "ward 5, main street",
            "city": "bharatpur",
            "state": "rajasthan",
        }
    }
    _apply_initcap_on_read(data)
    cust = data["customer"]
    assert cust["name"] == "RAM SINGH"
    assert cust["care_of"] == "S/O MOHAN LAL"
    assert cust["house"] == "HOUSE NO 12"
    assert cust["address"] == "WARD 5, MAIN STREET"
    assert cust["city"] == "BHARATPUR"
    assert cust["state"] == "RAJASTHAN"
