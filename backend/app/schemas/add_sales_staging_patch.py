"""Pydantic models for In-process operator edits to ``add_sales_staging.payload_json``."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class PatchAddSalesStagingCustomer(BaseModel):
    model_config = ConfigDict(extra="forbid")

    care_of: str | None = Field(None, max_length=512)
    address: str | None = Field(None, max_length=2048)


class PatchAddSalesStagingVehicle(BaseModel):
    model_config = ConfigDict(extra="forbid")

    frame_no: str | None = Field(None, max_length=128)
    engine_no: str | None = Field(None, max_length=128)
    key_no: str | None = Field(None, max_length=64)
    battery_no: str | None = Field(None, max_length=128)


class PatchAddSalesStagingInsurance(BaseModel):
    model_config = ConfigDict(extra="forbid")

    insurer: str | None = Field(None, max_length=255)
    nominee_name: str | None = Field(None, max_length=512)
    nominee_relationship: str | None = Field(None, max_length=128)


class PatchAddSalesStagingPayloadRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    customer: PatchAddSalesStagingCustomer | None = None
    vehicle: PatchAddSalesStagingVehicle | None = None
    insurance: PatchAddSalesStagingInsurance | None = None
    cpi_reqd: Literal["Y", "N"] | None = Field(
        None,
        description="CPA Required; persisted on add_sales_staging.cpi_reqd.",
    )
