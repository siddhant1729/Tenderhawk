"""API request/response models (PLAN.md §3.5)."""
from pydantic import BaseModel, ConfigDict, Field


class ProfileIn(BaseModel):
    name: str = Field(min_length=1)
    phone: str | None = None
    email: str | None = None
    products: list[str] = []
    keywords: list[str] = []
    hsn_codes: list[str] = []
    annual_turnover: int | None = Field(default=None, ge=0, description="INR")
    years_in_business: int | None = Field(default=None, ge=0)
    certifications: list[str] = []
    states_served: list[str] = []


class ProfileOut(ProfileIn):
    model_config = ConfigDict(from_attributes=True)
    id: int


class Created(BaseModel):
    id: int


class SweepIn(BaseModel):
    profile_id: int


class SweepOut(BaseModel):
    sweep_id: str
