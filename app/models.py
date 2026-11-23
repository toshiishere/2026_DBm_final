from decimal import Decimal
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

PositiveId = Annotated[int, Field(gt=0)]
Money = Annotated[Decimal, Field(ge=0, max_digits=12, decimal_places=2)]
Text = Annotated[str, Field(min_length=1, max_length=100)]


class Input(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True, allow_inf_nan=False)


class MemberCreate(Input):
    member_name: Text
    email: Annotated[str, Field(max_length=254, pattern=r"^[^\s@]+@[^\s@]+\.[^\s@]+$")]
    phone: Annotated[str, Field(min_length=1, max_length=30)]


class AddressCreate(Input):
    recipient_name: Text
    phone: Annotated[str, Field(min_length=1, max_length=30)]
    postal_code: Annotated[str, Field(min_length=1, max_length=20)]
    city: Text
    district: Text
    street_address: Annotated[str, Field(min_length=1, max_length=1000)]
    is_default: bool = False


class CategoryCreate(Input):
    category_name: Text


class VariantCreate(Input):
    sku: Text
    color: Annotated[str, Field(min_length=1, max_length=50)]
    size: Annotated[str, Field(min_length=1, max_length=30)]
    stock_qty: Annotated[int, Field(ge=0, le=1_000_000)] = 0


class ProductCreate(Input):
    category_id: PositiveId
    product_name: Annotated[str, Field(min_length=1, max_length=200)]
    description: Annotated[str, Field(max_length=10000)] = ""
    price: Money
    image_url: Annotated[str, Field(max_length=2000)] | None = None
    is_active: bool = True
    variants: Annotated[list[VariantCreate], Field(min_length=1, max_length=100)]


class ProductPatch(Input):
    category_id: PositiveId | None = None
    product_name: Annotated[str, Field(min_length=1, max_length=200)] | None = None
    description: Annotated[str, Field(max_length=10000)] | None = None
    price: Money | None = None
    image_url: Annotated[str, Field(max_length=2000)] | None = None
    is_active: bool | None = None

    @model_validator(mode="after")
    def validate_patch(self):
        if not self.model_fields_set:
            raise ValueError("At least one field is required")
        if any(getattr(self, key) is None for key in self.model_fields_set - {"image_url"}):
            raise ValueError("Only image_url may be null")
        return self


class OrderLine(Input):
    variant_id: PositiveId
    quantity: Annotated[int, Field(gt=0, le=10000)]


class OrderCreate(Input):
    member_id: PositiveId
    address_id: PositiveId
    shipping_fee: Money = Decimal("60.00")
    items: Annotated[list[OrderLine], Field(min_length=1, max_length=100)]

    @model_validator(mode="after")
    def unique_variants(self):
        if len({i.variant_id for i in self.items}) != len(self.items):
            raise ValueError("Each variant may appear once; combine quantities")
        return self


class MockPay(Input):
    success: bool = True
    payment_method: Literal["MOCK_CARD", "MOCK_TRANSFER"] = "MOCK_CARD"


class ShipmentCreate(Input):
    carrier: Text = "Demo Logistics"
    tracking_number: Text | None = None


class ShipmentPatch(Input):
    shipment_status: Literal["SHIPPED", "DELIVERED"]
    tracking_number: Text | None = None


class ReviewCreate(Input):
    member_id: PositiveId
    rating: Annotated[int, Field(ge=1, le=5)]
    content: Annotated[str, Field(max_length=5000)] = ""


class Restock(Input):
    quantity: Annotated[int, Field(gt=0, le=1_000_000)]


class Adjustment(Input):
    quantity_change: Annotated[int, Field(ge=-1_000_000, le=1_000_000)]

    @model_validator(mode="after")
    def nonzero(self):
        if self.quantity_change == 0:
            raise ValueError("Adjustment must be nonzero")
        return self
