from __future__ import annotations

import hashlib
import json
from decimal import ROUND_HALF_UP, Decimal
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


def canonical(value: object) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False)


def digest(value: object) -> str:
    return hashlib.sha256(canonical(value).encode()).hexdigest()


class Address(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    street: str = Field(min_length=1, max_length=200)
    city: str = Field(min_length=1, max_length=100)
    state: str = Field(min_length=2, max_length=100)
    zip: str = Field(min_length=1, max_length=20)
    phone: str = Field(min_length=1, max_length=40)
    name: str = Field(min_length=1, max_length=100)


class PurchaseInput(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    ask: str = Field(min_length=1, max_length=4000)
    max_total_usd: Decimal = Field(gt=0, le=10000, decimal_places=2, allow_inf_nan=False)
    address: Address

    @field_validator("max_total_usd", mode="before")
    @classmethod
    def reject_bool(cls, value):
        if isinstance(value, bool):
            raise ValueError("Budget must be a dollar amount")
        # Marketplace forms send JS numbers; round sub-cent noise (12.345, 0.1+0.2) to cents instead of rejecting.
        if isinstance(value, (int, float)) or (isinstance(value, str) and value.strip().replace(".", "", 1).isdigit()):
            try:
                return str(Decimal(str(value).strip()).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))
            except Exception:
                return value
        return value

    @field_validator("max_total_usd")
    @classmethod
    def normalize_money(cls, value):
        return value.quantize(Decimal("0.01"))


class StartRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    # Masumi payment service: "a unique nonce from the purchaser, hex, 14-26 chars" (hosted /payment schema).
    identifier_from_purchaser: str = Field(pattern=r"^[0-9a-f]{14,26}$")
    input_data: dict

    @field_validator("input_data")
    @classmethod
    def valid_purchase(cls, value):
        parse_purchase_input(value)
        # Preserve exact wire values for Masumi input hashing.
        canonical(value)
        return value


def parse_purchase_input(value: dict) -> PurchaseInput:
    data = dict(value)
    if "address" not in data:
        data["address"] = {key: data.pop(key) for key in Address.model_fields if key in data}
    return PurchaseInput.model_validate(data)


class Success(BaseModel):
    model_config = ConfigDict(extra="forbid")
    status: Literal["success"]
    order_id: str = Field(min_length=1, max_length=200)
    total_usd: Decimal = Field(ge=0, decimal_places=2, allow_inf_nan=False)
    merchant: str = Field(min_length=1, max_length=200)
    items: list[dict] = Field(min_length=1)


class Failure(BaseModel):
    model_config = ConfigDict(extra="forbid")
    status: Literal["failed"]
    reason: Literal["no_cart", "over_budget", "declined", "error", "cancelled", "card_disabled"]


class Pending(BaseModel):
    model_config = ConfigDict(extra="forbid")
    status: Literal["pending"]
    reason: Literal["approval_required", "needs_input", "processing", "unknown"]
    conversation_id: str | None = None
    approval_url: str | None = None
    message: str | None = None

    @field_validator("approval_url")
    @classmethod
    def https_only(cls, value):
        if value is not None and not value.startswith("https://"):
            raise ValueError("Approval URL must use HTTPS")
        return value


Outcome = Annotated[Success | Failure | Pending, Field(discriminator="status")]


class InputResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")
    approved: bool | None = Field(default=None, strict=True)
    answer: str | None = Field(default=None, min_length=1, max_length=2000)

    @model_validator(mode="after")
    def exactly_one(self):
        if (self.approved is None) == (self.answer is None):
            raise ValueError("Supply approved or answer, exclusively")
        return self


class ProvideInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    job_id: str
    input_schema_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    input_data: InputResponse


# MIP-003 input schema as rendered by Sokosumi (packages/masumi input.schema.ts): `string`/`number` carry
# placeholder/description, `text` additionally carries a default, so the delivery address is prefilled for demos.
_DEMO_ADDRESS = {"street": "1900 Jefferson St", "city": "San Francisco", "state": "CA", "zip": "94123",
                 "phone": "+14155550100", "name": "Ada Lovelace"}
INPUT_SCHEMA = {"input_data": [
    {"id": "ask", "type": "string", "name": "What should we buy?",
     "data": {"placeholder": "a pack of Trident sugar-free gum from Amazon",
              "description": "One item from a US retailer (Amazon). Keep it specific; the agent picks the best-value match."}},
    {"id": "max_total_usd", "type": "number", "name": "Maximum merchant total (USD)",
     "data": {"default": 20, "description": "Cap on the merchant subtotal. Fixed-price agent: 20 ADA is locked per job regardless."}},
] + [{"id": key, "type": "text", "name": key.title(), "data": {"default": _DEMO_ADDRESS[key],
                                                             "description": "US/Canada delivery only" if key == "street" else None}}
     for key in Address.model_fields]}
