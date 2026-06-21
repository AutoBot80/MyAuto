"""Uppercase address fields after OCR read and submit."""

from app.services.customer_address_infer import uppercase_customer_address_fields
from app.services.sales_ocr_service import _apply_initcap_on_read


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


def test_apply_initcap_on_read_uppercases_address_not_name_logic():
    data = {
        "customer": {
            "name": "ram singh",
            "house": "house no 12",
            "address": "ward 5, main street",
            "city": "bharatpur",
            "state": "rajasthan",
        }
    }
    _apply_initcap_on_read(data)
    cust = data["customer"]
    assert cust["name"] == "Ram Singh"
    assert cust["house"] == "HOUSE NO 12"
    assert cust["address"] == "WARD 5, MAIN STREET"
    assert cust["city"] == "BHARATPUR"
    assert cust["state"] == "RAJASTHAN"
