"""Pydantic-typed state models for the daily EDI feed simulation.

The state file captures everything needed for continuity between daily
runs: the organization's identity, its provider roster, known patients,
pending claims awaiting 835 responses, and the current accounts-receivable
ledger.
"""

# ruff: noqa: S311
from __future__ import annotations

from datetime import date
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel


class OrganizationProfile(BaseModel):
    """The healthcare organization that produces claims."""

    name: str
    npi: str
    tax_id: str
    city: str
    state_code: str
    zip_code: str
    street: str
    claims_per_day_min: int = 20
    claims_per_day_max: int = 50


class ProviderRecord(BaseModel):
    """A rendering provider on the organization's roster."""

    npi: str
    first_name: str
    last_name: str
    middle_initial: str
    taxonomy_code: str
    taxonomy_desc: str
    city: str
    state_code: str
    zip_code: str
    street: str
    street2: str | None = None
    hired_date: date
    departure_date: date | None = None


class PatientRecord(BaseModel):
    """A patient known to the organization."""

    mrn: str
    first_name: str
    last_name: str
    middle_initial: str
    dob: date
    gender: Literal["MALE", "FEMALE"]
    address_city: str
    address_state: str
    address_zip: str
    address_street: str
    address_street2: str | None = None
    member_id: str
    group_or_policy_number: str
    payer_name: str
    payer_identifier: str
    payer_tax_id: str
    payer_claim_filing_code: str
    payer_plan_type: str
    relationship: Literal["CHILD", "SPOUSE", "OTHER", "SELF"]
    subscriber_first: str
    subscriber_last: str
    subscriber_middle: str
    subscriber_dob: date
    subscriber_gender: Literal["MALE", "FEMALE"]
    pos_code: str = "11"
    pos_desc: str = "Office"
    pos_type: str = "OFFICE"


class PendingClaimRecord(BaseModel):
    """A submitted 837 awaiting 835 response."""

    submitted_date: date
    scheduled_response_date: date
    har_id: str
    mrn: str
    claim_data: dict[str, Any]


class RevertCandidateRecord(BaseModel):
    """A paid claim eligible for payer reversal."""

    pcn: str
    paid_date: date
    revert_eligible_until: date
    claim_data: dict[str, Any]
    payment_data: dict[str, Any]


class ARServiceLineRecord(BaseModel):
    """One AR row per service line, tracking insurance outstanding."""

    pcn: str
    transaction_id: int | None = None
    source_line_id: str
    har_id: str
    mrn: str
    service_date: date
    post_date: date
    charge_amount: float
    procedure_code: str
    modifiers: str | None = None
    payer_name: str
    plan_name: str
    financial_class: str
    billing_provider_name: str
    referring_provider_name: str
    department: str
    place_of_service: str
    claim_form_type: str
    insurance_outstanding: float
    claim_status: str = "Accepted"
    crossover_status: str | None = None
    opened_date: date
    closed_date: date | None = None


class FeedState(BaseModel):
    """Root state for the daily EDI feed simulation."""

    version: int = 1
    seed: int
    organization: OrganizationProfile
    providers: list[ProviderRecord] = []
    patients: list[PatientRecord] = []
    pending_claims: list[PendingClaimRecord] = []
    revert_candidates: list[RevertCandidateRecord] = []
    ar_items: list[ARServiceLineRecord] = []
    last_run_date: date | None = None
    next_transaction_id: int = 230000000
    total_claims_submitted: int = 0


def _require_json(path: Path) -> None:
    if path.suffix.lower() != ".json":
        raise ValueError("state file must use a .json extension")


def load_state(path: Path) -> FeedState:
    """Load feed state from JSON."""
    _require_json(path)
    return FeedState.model_validate_json(path.read_bytes())


def save_state(state: FeedState, path: Path) -> None:
    """Save feed state as JSON."""
    _require_json(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(state.model_dump_json(), encoding="utf-8")
