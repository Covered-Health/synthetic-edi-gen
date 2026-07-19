"""Daily EDI feed simulation.

Generates realistic daily 837/835/AR output for a synthetic healthcare
organization.  Each run advances the simulation by one or more days,
producing new claims, processing pending 835 responses, handling payer
reversals, and updating the accounts-receivable ledger.

Determinism: the same (seed, date) always produces identical output
regardless of when the tool is actually invoked.  Each processing phase
within a day gets its own sub-seed so changes to one phase (e.g. claim
generation) don't cascade into another (e.g. 835 responses).
"""

# ruff: noqa: S311
from __future__ import annotations

import hashlib
import random
import string
from datetime import date, datetime, time, timedelta
from pathlib import Path
from typing import Any, Literal
from zoneinfo import ZoneInfo

from .claim_generator import ClaimGenerator, PatientContext
from .edi_models import (
    Address,
    Code,
    Payment,
    PaymentLine,
    ProfClaim,
    Provider,
    Transaction835,
)
from .feed_state import (
    ARServiceLineRecord,
    FeedState,
    OrganizationProfile,
    PatientRecord,
    PendingClaimRecord,
    ProviderRecord,
    RevertCandidateRecord,
    load_state,
    save_state,
)
from .generate import write_jsonl
from .helpers import generate_transaction_id
from .openar_generator import (
    AGE_BUCKETS,
    DEPARTMENTS,
    FINANCIAL_CLASS_MAP,
    write_openar_csv,
    write_openar_xlsx,
)
from .payment_generator import PaymentGenerator
from .reference_data import (
    CITIES_STATES,
    COMMON_PAYERS,
    FIRST_NAMES,
    LAST_NAMES,
    PLACE_OF_SERVICE,
    Payer,
    PlaceOfService,
)

# ---------------------------------------------------------------------------
# Organisation profile: taxonomies for a multi-specialty surgical group
# ---------------------------------------------------------------------------

_PRACTICE_TAXONOMIES: list[tuple[str, str, int]] = [
    ("207X00000X", "Orthopaedic Surgery Physician", 3),
    ("207XS0114X", "Adult Reconstructive Orthopaedic Surgery", 1),
    ("208600000X", "Surgery Physician", 2),
    ("207Q00000X", "Family Medicine Physician", 2),
    ("207R00000X", "Internal Medicine Physician", 1),
    ("207LP2900X", "Pain Medicine", 1),
    ("363LF0000X", "Family Nurse Practitioner", 2),
    ("363A00000X", "Physician Assistant", 1),
]

# ---------------------------------------------------------------------------
# 835 response-time distribution (days from claim submission)
# ---------------------------------------------------------------------------

_RESPONSE_TIME_BANDS: list[tuple[tuple[int, int], int]] = [
    ((2, 5), 10),
    ((5, 14), 40),
    ((14, 30), 30),
    ((30, 45), 15),
    ((45, 90), 5),
]

_CLEARINGHOUSE_REJECTION_RATE = 0.05
_REJECTED_REFILE_RATE = 0.85

# ---------------------------------------------------------------------------
# Revert parameters
# ---------------------------------------------------------------------------

_REVERT_SELECTION_RATE = 0.05  # fraction of paid claims marked as candidates
_REVERT_DAILY_PROBABILITY = 0.04  # daily chance a candidate actually reverts
_REVERT_WINDOW_DAYS = 60

# ---------------------------------------------------------------------------
# Patient-pool parameters
# ---------------------------------------------------------------------------

_RETURNING_PATIENT_RATE = 0.70
_MAX_PATIENT_POOL = 2000


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _today_eastern() -> date:
    """Current date in US Eastern time (discards time component)."""
    return datetime.now(ZoneInfo("America/New_York")).date()


def _day_seed(master_seed: int, day_date: date, phase: int = 0) -> int:
    """Derive a deterministic seed for a given day and processing phase."""
    key = f"{master_seed}:{day_date.isoformat()}:{phase}"
    return int(hashlib.sha256(key.encode()).hexdigest()[:16], 16)


def _is_business_day(d: date) -> bool:
    return d.weekday() < 5


# ---------------------------------------------------------------------------
# State initialisation
# ---------------------------------------------------------------------------


def _generate_provider_record(
    rng: random.Random,
    taxonomy_code: str,
    taxonomy_desc: str,
    hired_date: date,
) -> ProviderRecord:
    city = rng.choice(CITIES_STATES)
    first = rng.choice(FIRST_NAMES["UNKNOWN"])
    last = rng.choice(LAST_NAMES)
    middle = rng.choice(string.ascii_uppercase)
    streets = ["MAIN ST", "OAK AVE", "MEDICAL PKWY", "HOSPITAL DR", "PARK BLVD"]
    return ProviderRecord(
        npi="".join(rng.choices(string.digits, k=10)),
        first_name=first,
        last_name=last,
        middle_initial=middle,
        taxonomy_code=taxonomy_code,
        taxonomy_desc=taxonomy_desc,
        city=city.city,
        state_code=city.state,
        zip_code=city.zip,
        street=f"{rng.randint(100, 9999)} {rng.choice(streets)}",
        hired_date=hired_date,
    )


def init_state(seed: int, start_date: date | None = None) -> FeedState:
    """Create initial state for a multi-specialty surgical group."""
    rng = random.Random(seed)

    org_city = rng.choice(CITIES_STATES)
    org = OrganizationProfile(
        name="ADVANCED ORTHOPEDIC & SURGICAL ASSOCIATES PA",
        npi="".join(rng.choices(string.digits, k=10)),
        tax_id="".join(rng.choices(string.digits, k=9)),
        city=org_city.city,
        state_code=org_city.state,
        zip_code=org_city.zip,
        street=f"{rng.randint(100, 9999)} MEDICAL CENTER DR",
    )

    providers: list[ProviderRecord] = []
    start_date = start_date or _today_eastern()
    for tax_code, tax_desc, count in _PRACTICE_TAXONOMIES:
        for _ in range(count):
            hired = start_date - timedelta(days=rng.randint(365, 3650))
            providers.append(_generate_provider_record(rng, tax_code, tax_desc, hired))

    return FeedState(seed=seed, organization=org, providers=providers)


# ---------------------------------------------------------------------------
# Daily feed generator
# ---------------------------------------------------------------------------


class DailyFeedGenerator:
    """Processes one or more simulated days, mutating the shared state."""

    def __init__(
        self,
        state: FeedState,
        claims_per_day_min: int | None = None,
        claims_per_day_max: int | None = None,
    ):
        org = state.organization
        claims_per_day_min = (
            org.claims_per_day_min if claims_per_day_min is None else claims_per_day_min
        )
        claims_per_day_max = (
            org.claims_per_day_max if claims_per_day_max is None else claims_per_day_max
        )
        if claims_per_day_min < 0 or claims_per_day_max < 0:
            raise ValueError("claims per day must be non-negative")
        if claims_per_day_min > claims_per_day_max:
            raise ValueError("claims_per_day_min must not exceed claims_per_day_max")
        self.state = state
        self._claims_per_day_min = claims_per_day_min
        self._claims_per_day_max = claims_per_day_max
        self._claim_gen = ClaimGenerator(seed=None)
        self._payment_gen = PaymentGenerator(seed=None)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def process_day(self, day_date: date) -> tuple[list[ProfClaim], list[Payment]]:
        """Process a single simulated day.  Returns (new_claims, new_payments)."""

        new_claims: list[ProfClaim] = []
        new_payments: list[Payment] = []

        # Phase 0 — day parameters (isolated seed)
        random.seed(_day_seed(self.state.seed, day_date, phase=0))
        num_claims = 0
        if _is_business_day(day_date):
            num_claims = random.randint(
                self._claims_per_day_min, self._claims_per_day_max
            )

        # Phase 1 — provider roster churn
        random.seed(_day_seed(self.state.seed, day_date, phase=1))
        self._update_roster(day_date)

        # Phase 2 — new 837 claims
        random.seed(_day_seed(self.state.seed, day_date, phase=2))
        for _ in range(num_claims):
            claim = self._generate_new_claim(day_date)
            new_claims.append(claim)

        # Phase 3 — 835 responses for pending claims
        random.seed(_day_seed(self.state.seed, day_date, phase=3))
        refiled_claims, response_payments = self._process_responses(day_date)
        new_claims.extend(refiled_claims)
        new_payments.extend(response_payments)

        # Phase 4 — payer reversals
        random.seed(_day_seed(self.state.seed, day_date, phase=4))
        new_payments.extend(self._process_reverts(day_date))

        # Phase 5 — AR lifecycle (patient payments, write-offs)
        random.seed(_day_seed(self.state.seed, day_date, phase=5))
        self._update_ar_lifecycle(day_date)

        # Housekeeping
        self._cleanup_state(day_date)
        self.state.last_run_date = day_date
        return new_claims, new_payments

    def generate_ar_snapshot(self, as_of_date: date) -> list[dict[str, Any]]:
        """Build the full OpenAR snapshot of currently-open items."""
        rows: list[dict[str, Any]] = []
        for item in self.state.ar_items:
            if item.closed_date is not None:
                continue
            if item.insurance_outstanding <= 0:
                continue

            age_days = (as_of_date - item.service_date).days
            if item.transaction_id is None:
                self.state.next_transaction_id += 1
                item.transaction_id = self.state.next_transaction_id

            rows.append(
                {
                    "Slices by Service Date Age (days)": _age_bucket(age_days),
                    "Post Date": item.post_date,
                    "Professional Transaction ID": item.transaction_id,
                    "MRN": item.mrn,
                    "Current Financial Class": item.financial_class,
                    "Current Plan": item.plan_name,
                    "Current Payer": item.payer_name,
                    "Billing Provider": item.billing_provider_name,
                    "Referring Provider": item.referring_provider_name,
                    "Service Date": item.service_date,
                    "Procedure Code": item.procedure_code,
                    "Modifiers (All)": item.modifiers,
                    "Transaction Type": "Charge",
                    "Posted Amount ($)": float(round(item.charge_amount, 2)),
                    "Claim Status": item.claim_status,
                    "Crossover Status": item.crossover_status,
                    "Claim Form Type": item.claim_form_type,
                    "Place of Service": item.place_of_service,
                    "Department": item.department,
                    "Hospital Account ID": item.har_id,
                    "Invoice Number": item.pcn,
                    "Insurance Outstanding Amount ($)": float(
                        round(item.insurance_outstanding, 2)
                    ),
                }
            )
        return rows

    # ------------------------------------------------------------------
    # Phase 1 — roster management
    # ------------------------------------------------------------------

    def _update_roster(self, day_date: date) -> None:
        active = [p for p in self.state.providers if p.departure_date is None]

        # ~5% annual departure rate → ~0.014% per day per provider
        for provider in active:
            if random.random() < 0.00014:
                provider.departure_date = day_date

        # ~1 new hire per year for a 13-person group → ~0.3% daily chance
        if random.random() < 0.003:
            rng = random.Random(random.randint(0, 2**32))
            tax_code, tax_desc, _ = random.choice(_PRACTICE_TAXONOMIES)
            self.state.providers.append(
                _generate_provider_record(rng, tax_code, tax_desc, day_date)
            )

    # ------------------------------------------------------------------
    # Phase 2 — new claim generation
    # ------------------------------------------------------------------

    def _generate_new_claim(self, day_date: date) -> ProfClaim:
        patient = self._pick_or_create_patient(day_date)
        provider_rec = self._pick_active_provider()
        ctx = self._build_patient_context(patient, provider_rec, day_date)

        claim = self._claim_gen.generate_claim(ctx=ctx)
        # Override non-deterministic fields (uuid4 uses OS entropy, not random)
        claim.id = "".join(random.choices("0123456789abcdef", k=24))
        claim.transaction.creation_date = day_date
        claim.transaction.creation_time = time(8, 0)

        clearinghouse_rejection = random.random() < _CLEARINGHOUSE_REJECTION_RATE
        response_delay = (
            random.randint(1, 2)
            if clearinghouse_rejection
            else self._sample_response_delay()
        )
        har_id = "".join(random.choices(string.digits, k=11))

        self.state.pending_claims.append(
            PendingClaimRecord(
                submitted_date=day_date,
                scheduled_response_date=day_date + timedelta(days=response_delay),
                har_id=har_id,
                mrn=patient.mrn,
                claim_data=claim.model_dump(
                    by_alias=True, mode="json", exclude_none=True
                ),
                clearinghouse_rejection=clearinghouse_rejection,
            )
        )

        self._create_ar_items(claim, har_id, patient, provider_rec, day_date)
        self.state.total_claims_submitted += 1
        return claim

    def _pick_or_create_patient(self, day_date: date) -> PatientRecord:
        pool = self.state.patients
        if pool and len(pool) > 20 and random.random() < _RETURNING_PATIENT_RATE:
            return random.choice(pool)

        patient = self._create_patient(day_date)
        if len(pool) < _MAX_PATIENT_POOL:
            pool.append(patient)
        else:
            pool[random.randint(0, len(pool) - 1)] = patient
        return patient

    def _create_patient(self, day_date: date) -> PatientRecord:
        gender: Literal["MALE", "FEMALE"] = random.choice(["MALE", "FEMALE"])
        first = random.choice(FIRST_NAMES[gender])
        last = random.choice(LAST_NAMES)
        middle = random.choice(string.ascii_uppercase)
        dob = date(
            day_date.year - random.randint(18, 85),
            random.randint(1, 12),
            random.randint(1, 28),
        )
        city = random.choice(CITIES_STATES)
        payer = random.choice(COMMON_PAYERS)
        pos = random.choice(PLACE_OF_SERVICE)
        streets = ["MAIN ST", "OAK AVE", "MAPLE DR", "ELM ST", "PARK BLVD"]

        is_self = random.random() < 0.7
        if is_self:
            sub_first, sub_last, sub_middle = first, last, middle
            sub_dob, sub_gender = dob, gender
            relationship: Literal["CHILD", "SPOUSE", "OTHER", "SELF"] = "SELF"
        else:
            sub_gender: Literal["MALE", "FEMALE"] = random.choice(["MALE", "FEMALE"])
            sub_first = random.choice(FIRST_NAMES[sub_gender])
            sub_last = last
            sub_middle = random.choice(string.ascii_uppercase)
            sub_dob = date(
                day_date.year - random.randint(25, 75),
                random.randint(1, 12),
                random.randint(1, 28),
            )
            relationship = random.choice(["CHILD", "SPOUSE", "OTHER"])

        return PatientRecord(
            mrn="".join(random.choices(string.digits, k=9)),
            first_name=first,
            last_name=last,
            middle_initial=middle,
            dob=dob,
            gender=gender,
            address_city=city.city,
            address_state=city.state,
            address_zip=city.zip,
            address_street=(f"{random.randint(100, 9999)} {random.choice(streets)}"),
            member_id=(
                "".join(random.choices(string.ascii_uppercase, k=2))
                + "".join(random.choices(string.digits, k=9))
            ),
            group_or_policy_number="".join(
                random.choices(string.ascii_uppercase + string.digits, k=10)
            ),
            payer_name=payer.name,
            payer_identifier=payer.identifier,
            payer_tax_id=payer.tax_id,
            payer_claim_filing_code=payer.claim_filing_code,
            payer_plan_type=payer.plan_type,
            relationship=relationship,
            subscriber_first=sub_first,
            subscriber_last=sub_last,
            subscriber_middle=sub_middle,
            subscriber_dob=sub_dob,
            subscriber_gender=sub_gender,
            pos_code=pos.code,
            pos_desc=pos.desc,
            pos_type=pos.type,
        )

    def _pick_active_provider(self) -> ProviderRecord:
        active = [p for p in self.state.providers if p.departure_date is None]
        if not active:
            active = self.state.providers[-5:]
        return random.choice(active)

    def _build_patient_context(
        self,
        patient: PatientRecord,
        provider: ProviderRecord,
        day_date: date,
    ) -> PatientContext:
        org = self.state.organization
        return PatientContext(
            patient_first=patient.first_name,
            patient_last=patient.last_name,
            patient_middle=patient.middle_initial,
            patient_dob=patient.dob,
            patient_gender=patient.gender,
            patient_address=Address(
                line=patient.address_street,
                line2=patient.address_street2,
                city=patient.address_city,
                state_code=patient.address_state,
                zip_code=patient.address_zip,
            ),
            subscriber_first=patient.subscriber_first,
            subscriber_last=patient.subscriber_last,
            subscriber_middle=patient.subscriber_middle,
            subscriber_dob=patient.subscriber_dob,
            subscriber_gender=patient.subscriber_gender,
            subscriber_address=Address(
                line=patient.address_street,
                city=patient.address_city,
                state_code=patient.address_state,
                zip_code=patient.address_zip,
            ),
            relationship=patient.relationship,
            member_id=patient.member_id,
            group_or_policy_number=patient.group_or_policy_number,
            payer_info=Payer.model_validate(
                {
                    "name": patient.payer_name,
                    "identifier": patient.payer_identifier,
                    "tax_id": patient.payer_tax_id,
                    "claim_filing_code": patient.payer_claim_filing_code,
                    "plan_type": patient.payer_plan_type,
                }
            ),
            billing_provider=Provider(
                entity_role="BILLING_PROVIDER",
                entity_type="BUSINESS",
                identification_type="NPI",
                identifier=org.npi,
                tax_id=org.tax_id,
                last_name_or_org_name=org.name,
                address=Address(
                    line=org.street,
                    city=org.city,
                    state_code=org.state_code,
                    zip_code=org.zip_code,
                ),
            ),
            rendering_provider=Provider(
                entity_role="RENDERING",
                entity_type="INDIVIDUAL",
                identification_type="NPI",
                identifier=provider.npi,
                last_name_or_org_name=provider.last_name,
                first_name=provider.first_name,
                middle_name=provider.middle_initial,
                address=Address(
                    line=provider.street,
                    line2=provider.street2,
                    city=provider.city,
                    state_code=provider.state_code,
                    zip_code=provider.zip_code,
                ),
                provider_taxonomy=Code(
                    sub_type="PROVIDER_TAXONOMY",
                    code=provider.taxonomy_code,
                    desc=provider.taxonomy_desc,
                ),
            ),
            base_service_date=day_date - timedelta(days=random.randint(0, 3)),
            mrn=patient.mrn,
            pos=PlaceOfService(
                code=patient.pos_code,
                desc=patient.pos_desc,
                type=patient.pos_type,
            ),
        )

    @staticmethod
    def _sample_response_delay() -> int:
        ranges = [r for r, _ in _RESPONSE_TIME_BANDS]
        weights = [w for _, w in _RESPONSE_TIME_BANDS]
        lo, hi = random.choices(ranges, weights=weights, k=1)[0]
        return random.randint(lo, hi)

    def _create_ar_items(
        self,
        claim: ProfClaim,
        har_id: str,
        patient: PatientRecord,
        provider: ProviderRecord,
        day_date: date,
    ) -> None:
        org = self.state.organization
        fin_class = FINANCIAL_CLASS_MAP.get(
            patient.payer_claim_filing_code, "Commercial"
        )
        department = random.choice(DEPARTMENTS)
        pos_desc = f"{department} - POS {patient.pos_code}"

        for svc in claim.service_lines or []:
            proc_code = svc.procedure.code if svc.procedure else ""
            mods = None
            if svc.procedure and svc.procedure.modifiers:
                mods = ", ".join(m.code for m in svc.procedure.modifiers)

            self.state.next_transaction_id += 1
            self.state.ar_items.append(
                ARServiceLineRecord(
                    pcn=claim.patient_control_number,
                    transaction_id=self.state.next_transaction_id,
                    source_line_id=svc.source_line_id or "",
                    har_id=har_id,
                    mrn=patient.mrn,
                    service_date=svc.service_date_from or day_date,
                    post_date=day_date,
                    charge_amount=float(svc.charge_amount),
                    procedure_code=proc_code,
                    modifiers=mods,
                    payer_name=patient.payer_name,
                    plan_name=patient.payer_name,
                    financial_class=fin_class,
                    billing_provider_name=org.name,
                    referring_provider_name=(
                        f"{provider.last_name}, {provider.first_name}"
                    ),
                    department=department,
                    place_of_service=pos_desc,
                    claim_form_type="CMS Claim",
                    insurance_outstanding=float(svc.charge_amount),
                    opened_date=day_date,
                )
            )

    # ------------------------------------------------------------------
    # Phase 3 — 835 responses
    # ------------------------------------------------------------------

    def _process_responses(
        self, day_date: date
    ) -> tuple[list[ProfClaim], list[Payment]]:
        refiled_claims: list[ProfClaim] = []
        payments: list[Payment] = []
        still_pending: list[PendingClaimRecord] = []

        for pending in self.state.pending_claims:
            if pending.scheduled_response_date > day_date:
                still_pending.append(pending)
                continue

            claim = ProfClaim.model_validate(pending.claim_data)

            if pending.clearinghouse_rejection:
                payments.append(self._build_clearinghouse_rejection(claim, day_date))
                if random.random() < _REJECTED_REFILE_RATE:
                    refiled = self._refile_rejected_claim(claim, day_date)
                    refiled_claims.append(refiled)
                    still_pending.append(
                        PendingClaimRecord(
                            submitted_date=day_date,
                            scheduled_response_date=day_date
                            + timedelta(days=self._sample_response_delay()),
                            har_id=pending.har_id,
                            mrn=pending.mrn,
                            claim_data=refiled.model_dump(
                                by_alias=True, mode="json", exclude_none=True
                            ),
                        )
                    )
                    self.state.total_claims_submitted += 1
                continue

            payment = self._payment_gen.generate_payment_for_claim(claim)
            payment.id = "".join(random.choices("0123456789abcdef", k=24))
            payment.transaction.payment_date = day_date
            payment.transaction.production_date = day_date
            payments.append(payment)

            self._apply_payment_to_ar(claim.patient_control_number, payment, day_date)

            if (
                payment.claim_status != "DENIED"
                and payment.payment_amount > 0
                and random.random() < _REVERT_SELECTION_RATE
            ):
                self.state.revert_candidates.append(
                    RevertCandidateRecord(
                        pcn=claim.patient_control_number,
                        paid_date=day_date,
                        revert_eligible_until=day_date
                        + timedelta(days=_REVERT_WINDOW_DAYS),
                        claim_data=pending.claim_data,
                        payment_data=payment.model_dump(
                            by_alias=True, mode="json", exclude_none=True
                        ),
                    )
                )

        self.state.pending_claims = still_pending
        return refiled_claims, payments

    def _build_clearinghouse_rejection(
        self, claim: ProfClaim, day_date: date
    ) -> Payment:
        """Build a zero-payment record that downstream classifies as REJECTED."""
        payment = self._payment_gen.generate_rejection_for_claim(claim)
        payment.id = "".join(random.choices("0123456789abcdef", k=24))
        payment.transaction.payment_date = day_date
        payment.transaction.production_date = day_date
        return payment

    def _refile_rejected_claim(self, claim: ProfClaim, day_date: date) -> ProfClaim:
        """Refile a clearinghouse-rejected claim as an original submission."""
        refiled = self._claim_gen.generate_refiled_claim(claim)
        refiled.id = "".join(random.choices("0123456789abcdef", k=24))
        refiled.transaction.control_number = "".join(
            random.choices(string.ascii_uppercase + string.digits, k=8)
        )
        refiled.transaction.creation_date = day_date
        refiled.transaction.creation_time = time(8, 0)
        return refiled

    def _apply_payment_to_ar(self, pcn: str, payment: Payment, day_date: date) -> None:
        pmt_by_line: dict[str, PaymentLine] = {}
        for pmt_line in payment.service_lines or []:
            if pmt_line.source_line_id:
                pmt_by_line[pmt_line.source_line_id] = pmt_line

        for ar in self.state.ar_items:
            if ar.pcn != pcn or ar.closed_date is not None:
                continue

            pmt_line = pmt_by_line.get(ar.source_line_id)
            if not pmt_line:
                continue

            if payment.claim_status == "DENIED":
                ar.claim_status = "Rejected"
                # insurance outstanding stays — may refile or appeal
            else:
                paid = float(pmt_line.paid_amount)
                ar.insurance_outstanding = max(0, ar.insurance_outstanding - paid)
                for adj in pmt_line.adjustments or []:
                    if adj.group in {"CONTRACTUAL", "PATIENT_RESPONSIBILITY"}:
                        ar.insurance_outstanding = max(
                            0, ar.insurance_outstanding - adj.amount
                        )
                if ar.insurance_outstanding < 0.01:
                    ar.insurance_outstanding = 0
                    patient_resp = sum(
                        adj.amount
                        for adj in (pmt_line.adjustments or [])
                        if adj.group == "PATIENT_RESPONSIBILITY"
                    )
                    if patient_resp <= 0:
                        ar.closed_date = day_date

    # ------------------------------------------------------------------
    # Phase 4 — payer reversals
    # ------------------------------------------------------------------

    def _process_reverts(self, day_date: date) -> list[Payment]:
        payments: list[Payment] = []
        still_eligible: list[RevertCandidateRecord] = []

        for cand in self.state.revert_candidates:
            if cand.revert_eligible_until < day_date:
                continue  # window expired

            if random.random() < _REVERT_DAILY_PROBABILITY:
                original_pmt = Payment.model_validate(cand.payment_data)
                claim = ProfClaim.model_validate(cand.claim_data)

                # 1. Reversal 835 (negative amounts)
                reversal = self._build_reversal(original_pmt, day_date)
                payments.append(reversal)

                # 2. New adjudication
                new_pmt = self._payment_gen.generate_payment_for_claim(claim)
                new_pmt.id = "".join(random.choices("0123456789abcdef", k=24))
                new_pmt.transaction.payment_date = day_date
                new_pmt.transaction.production_date = day_date
                payments.append(new_pmt)

                # 3. Update AR
                self._revert_ar(cand.pcn, day_date)
                self._apply_payment_to_ar(cand.pcn, new_pmt, day_date)
                continue

            still_eligible.append(cand)

        self.state.revert_candidates = still_eligible
        return payments

    @staticmethod
    def _build_reversal(original: Payment, day_date: date) -> Payment:
        reversed_lines: list[PaymentLine] = []
        for line in original.service_lines or []:
            reversed_lines.append(
                line.model_copy(
                    update={
                        "paid_amount": -line.paid_amount,
                        "adjustments": [
                            adjustment.model_copy(update={"amount": -adjustment.amount})
                            for adjustment in line.adjustments or []
                        ]
                        or None,
                    }
                )
            )

        return Payment(
            id=f"REV{random.randint(100000, 999999):06d}"  # noqa: S311
            + "".join(random.choices(string.digits, k=18)),
            object_type="PAYMENT",
            patient_control_number=original.patient_control_number,
            charge_amount=original.charge_amount,
            payment_amount=-original.payment_amount,
            facility_code=original.facility_code,
            claim_status_code="22",
            claim_status="REVERSAL",
            service_date_from=original.service_date_from,
            service_date_to=original.service_date_to,
            patient=original.patient,
            payer=original.payer,
            payee=original.payee,
            service_lines=reversed_lines,
            transaction=Transaction835(
                control_number=generate_transaction_id()[:10],
                transaction_type="835",
                transaction_set_identifier_code="835",
                production_date=day_date,
                transaction_handling_type="D",
                total_payment_amount=-original.payment_amount,
                credit_or_debit_flag_code="D",
                payment_method_type="CHK",
                payment_date=day_date,
                check_or_eft_trace_number=generate_transaction_id()[:15],
                payer_identifier=original.transaction.payer_identifier,
            ),
        )

    def _revert_ar(self, pcn: str, _day_date: date) -> None:
        for ar in self.state.ar_items:
            if ar.pcn == pcn:
                ar.closed_date = None
                ar.insurance_outstanding = ar.charge_amount
                ar.claim_status = "Accepted"

    # ------------------------------------------------------------------
    # Phase 5 — AR lifecycle
    # ------------------------------------------------------------------

    def _update_ar_lifecycle(self, day_date: date) -> None:
        for ar in self.state.ar_items:
            if ar.closed_date is not None:
                continue

            age = (day_date - ar.opened_date).days

            # Denied claims: start writing off after 90 days
            if ar.claim_status == "Rejected" and age > 90:
                if ar.charge_amount < 25 or random.random() < 0.02:
                    ar.insurance_outstanding = 0
                    ar.closed_date = day_date
                continue

            # Insurance portion resolved — patient responsibility phase
            if ar.insurance_outstanding == 0:
                if age < 30:
                    prob = 0.02
                elif age < 60:
                    prob = 0.04
                elif age < 90:
                    prob = 0.03
                else:
                    prob = 0.01
                    if random.random() < 0.02:  # write-off
                        ar.closed_date = day_date
                        continue

                if random.random() < prob:
                    ar.closed_date = day_date

    # ------------------------------------------------------------------
    # Housekeeping
    # ------------------------------------------------------------------

    def _cleanup_state(self, day_date: date) -> None:
        cutoff = day_date - timedelta(days=_REVERT_WINDOW_DAYS)
        self.state.ar_items = [
            a
            for a in self.state.ar_items
            if a.closed_date is None or a.closed_date > cutoff
        ]


# ---------------------------------------------------------------------------
# Utility
# ---------------------------------------------------------------------------


def _age_bucket(age_days: int) -> str:
    for min_d, max_d, label in AGE_BUCKETS:
        if min_d <= age_days < max_d:
            return label
    return AGE_BUCKETS[-1][2]


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------


_DEFAULT_OUTPUT_DIR = Path("./daily_output")


def daily_feed(
    state_file: Path,
    output_dir: Path = _DEFAULT_OUTPUT_DIR,
    seed: int = 42,
    target_date: str | None = None,
    claims_per_day_min: int | None = None,
    claims_per_day_max: int | None = None,
    ar_format: Literal["csv", "xlsx"] = "csv",
) -> None:
    """Generate a daily EDI feed simulating an RCM team's data dump.

    Produces 837 claims, 835 payment responses, and an OpenAR snapshot
    for the target date.  State is persisted to a JSON file so that
    subsequent runs produce a realistic, continuous data stream.

    Args:
        state_file: Path to the JSON state file.
        output_dir: Directory for daily output files.
        seed: Master seed for deterministic generation (first run only).
        target_date: ISO date to generate for (default: today US Eastern).
        claims_per_day_min: Minimum claims to generate per business day.
        claims_per_day_max: Maximum claims to generate per business day.
        ar_format: Output format for OpenAR data ('csv' or 'xlsx').
    """
    if target_date is not None:
        the_date = date.fromisoformat(target_date)
    else:
        the_date = _today_eastern()

    # Load or initialise state
    if state_file.exists():
        state = load_state(state_file)
        print(f"Loaded state from {state_file}")
        print(f"  Last run: {state.last_run_date}")
        print(f"  Pending claims: {len(state.pending_claims)}")
        open_count = sum(1 for a in state.ar_items if a.closed_date is None)
        print(f"  Open AR items: {open_count}")
    else:
        state = init_state(seed, the_date)
        print(f"Initialised new feed state (seed={seed})")
        print(f"  Organisation: {state.organization.name}")
        print(f"  Providers: {len(state.providers)}")

    print(f"Target date: {the_date}")

    if state.last_run_date and state.last_run_date >= the_date:
        print(f"Already processed {the_date}, nothing to do.")
        return

    start = state.last_run_date + timedelta(days=1) if state.last_run_date else the_date
    days_count = (the_date - start).days + 1

    feed = DailyFeedGenerator(
        state,
        claims_per_day_min=claims_per_day_min,
        claims_per_day_max=claims_per_day_max,
    )
    all_claims: list[ProfClaim] = []
    all_payments: list[Payment] = []

    if days_count > 1:
        print(f"Catching up {days_count} days ({start} to {the_date}) ...")

    current = start
    while current <= the_date:
        claims, payments = feed.process_day(current)
        all_claims.extend(claims)
        all_payments.extend(payments)
        current += timedelta(days=1)

    # AR snapshot as of target date
    ar_rows = feed.generate_ar_snapshot(the_date)

    # --- Write output files ---
    output_dir.mkdir(parents=True, exist_ok=True)
    ds = the_date.strftime("%Y%m%d")

    claims_path = output_dir / f"837_claims_{ds}.jsonl"
    with open(claims_path, "w") as f:
        for c in all_claims:
            write_jsonl(f, c)

    payments_path = output_dir / f"835_payments_{ds}.jsonl"
    with open(payments_path, "w") as f:
        for p in all_payments:
            write_jsonl(f, p)

    ar_ext = "csv" if ar_format == "csv" else "xlsx"
    ar_path = output_dir / f"openar_{ds}.{ar_ext}"
    export_dt = datetime.combine(the_date, time())
    if ar_format == "csv":
        write_openar_csv(ar_rows, str(ar_path), export_datetime=export_dt)
    else:
        write_openar_xlsx(ar_rows, str(ar_path), export_datetime=export_dt)

    # Save state
    save_state(state, state_file)

    # Summary
    print(f"\nDaily feed for {the_date}:")
    print(f"  837 claims:  {len(all_claims):,} -> {claims_path}")
    print(f"  835 payments: {len(all_payments):,} -> {payments_path}")
    print(f"  AR snapshot:  {len(ar_rows):,} open items -> {ar_path}")
    print(f"  Pending claims: {len(state.pending_claims):,}")
    print(f"  Total submitted: {state.total_claims_submitted:,}")
    print(f"  State saved to {state_file}")
