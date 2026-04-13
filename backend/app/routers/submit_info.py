"""Submit Info: persist customer, vehicle, sales, insurance from Add Sales form."""

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from app.security.deps import get_principal, resolve_dealer_id
from app.security.principal import Principal
from app.services.submit_info_service import submit_info
from app.validation.text_limits import enforce_max_text_depth

router = APIRouter(prefix="/submit-info", tags=["submit-info"])


class CustomerPayload(BaseModel):
    aadhar_id: str | None = None
    name: str | None = None
    gender: str | None = None
    date_of_birth: str | None = None
    address: str | None = None
    pin: str | None = None
    city: str | None = None
    state: str | None = None
    mobile_number: int | str | None = None
    alt_phone_num: str | None = None
    profession: str | None = None
    financier: str | None = None
    marital_status: str | None = None
    care_of: str | None = None  # Aadhaar QR care-of; DMS Father/Husband + Form 20
    dms_relation_prefix: str | None = None  # Ignored for staging; server derives from address + gender
    dms_contact_path: str | None = None
    file_location: str | None = None


class VehiclePayload(BaseModel):
    frame_no: str | None = None
    engine_no: str | None = None
    key_no: str | None = None
    battery_no: str | None = None


class InsurancePayload(BaseModel):
    nominee_name: str | None = None
    nominee_age: int | str | None = None
    nominee_relationship: str | None = None
    nominee_gender: str | None = None
    insurer: str | None = None
    policy_num: str | None = None
    policy_from: str | None = None
    policy_to: str | None = None
    premium: str | float | None = None


class SubmitInfoPayload(BaseModel):
    customer: CustomerPayload = Field(default_factory=CustomerPayload)
    vehicle: VehiclePayload = Field(default_factory=VehiclePayload)
    insurance: InsurancePayload = Field(default_factory=InsurancePayload)
    dealer_id: int | None = None
    file_location: str | None = None
    staging_id: str | None = Field(
        default=None,
        description="When set, updates that draft add_sales_staging row if dealer_id matches; otherwise inserts a new UUID.",
    )


def _to_dict(m: BaseModel) -> dict:
    return m.model_dump() if hasattr(m, "model_dump") else m.dict()


@router.post("")
def post_submit_info(
    payload: SubmitInfoPayload,
    principal: Principal = Depends(get_principal),
) -> dict:
    """Validate and persist **draft** ``add_sales_staging`` only. Returns ``ok`` and ``staging_id``."""
    try:
        enforce_max_text_depth(payload.model_dump())
        did = resolve_dealer_id(principal, payload.dealer_id)
        result = submit_info(
            customer=_to_dict(payload.customer),
            vehicle=_to_dict(payload.vehicle),
            insurance=_to_dict(payload.insurance),
            dealer_id=did,
            file_location=payload.file_location,
            staging_id=payload.staging_id,
        )
        return result
    except HTTPException:
        raise
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
