from datetime import datetime
from decimal import Decimal
import re
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from .security import normalize_phone


_PHONE_IN_TEXT = re.compile(r"(?<!\d)(?:\+91[\s()-]*)?[6-9](?:[\d\s()-]{8,14})\d(?!\d)")


def reject_phone_in_text(value: str) -> str:
    if _PHONE_IN_TEXT.search(value):
        raise ValueError("Phone numbers are not allowed in this field.")
    return value


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class CustomerInput(StrictModel):
    name: str = Field(min_length=1, max_length=120)
    phone: str = Field(min_length=10, max_length=16)

    @field_validator("name")
    @classmethod
    def trim_name(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("Name is required.")
        return reject_phone_in_text(value)

    @field_validator("phone")
    @classmethod
    def normalize_and_validate_phone(cls, value: str) -> str:
        try:
            return normalize_phone(value)
        except ValueError:
            raise ValueError("Enter a valid Indian 10-digit phone number.") from None


class PermissionInput(StrictModel):
    thank_you: bool | None = None
    promotional: bool | None = None

    @model_validator(mode="after")
    def null_is_not_a_choice(self):
        for field in ("thank_you", "promotional"):
            if field in self.model_fields_set and getattr(self, field) is None:
                raise ValueError("Permission choices must be true or false when supplied.")
        return self


class PurchaseItemInput(StrictModel):
    name: str = Field(min_length=1, max_length=160)
    category: str = Field(min_length=1, max_length=80)
    quantity: int = Field(default=1, ge=1)
    unit_price: Decimal | None = Field(default=None, ge=0, max_digits=12, decimal_places=2)

    @field_validator("name", "category")
    @classmethod
    def trim_text(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("This field is required.")
        return reject_phone_in_text(value)


class PurchaseInput(StrictModel):
    total_amount: Decimal | None = Field(default=None, gt=0, max_digits=12, decimal_places=2)
    currency: str = Field(default="INR", min_length=3, max_length=3)
    items: list[PurchaseItemInput] = Field(min_length=1, max_length=1)
    purchased_at: datetime | None = None

    @field_validator("currency")
    @classmethod
    def uppercase_currency(cls, value: str) -> str:
        return value.upper()

    @field_validator("purchased_at")
    @classmethod
    def require_aware_timestamp(cls, value: datetime | None) -> datetime | None:
        if value is not None and value.tzinfo is None:
            raise ValueError("Timestamp must include a timezone.")
        return value


class CreatePurchaseRequest(StrictModel):
    customer: CustomerInput
    permissions: PermissionInput = Field(default_factory=PermissionInput)
    purchase: PurchaseInput
    idempotency_key: str | None = Field(default=None, min_length=1, max_length=160)

    @field_validator("idempotency_key")
    @classmethod
    def idempotency_key_is_not_a_phone(cls, value: str | None) -> str | None:
        return reject_phone_in_text(value) if value is not None else None


class ProductInput(StrictModel):
    name: str = Field(min_length=1, max_length=160)
    category: str = Field(min_length=1, max_length=80)
    available_stock: int = Field(ge=0)
    price: Decimal | None = Field(default=None, ge=0, max_digits=12, decimal_places=2)

    @field_validator("name", "category")
    @classmethod
    def trim_product_text(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("This field is required.")
        return reject_phone_in_text(value)


class CampaignPreviewRequest(StrictModel):
    name: str = Field(min_length=1, max_length=120)
    product: ProductInput
    message: str = Field(min_length=1, max_length=1000)
    channel: Literal["whatsapp_simulated"]

    @field_validator("name", "message")
    @classmethod
    def trim_campaign_text(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("This field is required.")
        return reject_phone_in_text(value)


class SimulationRequest(StrictModel):
    outcome_mode: Literal["deterministic"] = "deterministic"
