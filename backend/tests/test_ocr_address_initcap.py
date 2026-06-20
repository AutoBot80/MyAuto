"""InitCap for address line-1 locality fields after OCR read and submit."""

from app.services.customer_address_infer import initcap_customer_address_fields
from app.services.sales_ocr_service import _apply_initcap_on_read


def test_initcap_customer_address_fields_line1_locality_only():
    customer = {
        "house": "ward 5",
        "street": "main road",
        "location": "near post office",
        "district": "bharatpur",
        "sub_district": "nadbai",
        "post_office": "dhanota",
        "address": "ward 5, main road, near temple",
        "city": "BHARATPUR",
        "state": "RAJASTHAN",
    }
    initcap_customer_address_fields(customer)
    assert customer["house"] == "Ward 5"
    assert customer["street"] == "Main Road"
    assert customer["location"] == "Near Post Office"
    assert customer["district"] == "Bharatpur"
    assert customer["sub_district"] == "Nadbai"
    assert customer["post_office"] == "Dhanota"
    assert customer["address"] == "Ward 5, Main Road, Near Temple"
    assert customer["city"] == "BHARATPUR"
    assert customer["state"] == "RAJASTHAN"


def test_apply_initcap_on_read_initcaps_line1_not_city_state():
    data = {
        "customer": {
            "name": "ram singh",
            "house": "house no 12",
            "address": "ward 5, main street",
            "city": "BHARATPUR",
            "state": "RAJASTHAN",
        }
    }
    _apply_initcap_on_read(data)
    cust = data["customer"]
    assert cust["name"] == "Ram Singh"
    assert cust["house"] == "House No 12"
    assert cust["address"] == "Ward 5, Main Street"
    assert cust["city"] == "BHARATPUR"
    assert cust["state"] == "RAJASTHAN"
