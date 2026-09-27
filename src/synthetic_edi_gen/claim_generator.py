"""
Generate realistic 837P professional and 837I institutional claims.
"""

# ruff: noqa: S311 # insecure random numbers are fine here
import random
import string
import uuid
from dataclasses import dataclass, field, replace
from datetime import UTC, date, datetime, time, timedelta
from typing import Literal, TypeVar, cast

from synthetic_edi_gen.edi_models import (
    Address,
    Code,
    CodeAndAmount,
    CodeAndDate,
    CodeAndDateRange,
    InstClaim,
    InstDiagnosis,
    InstLine,
    Party,
    PartyIdName,
    Patient,
    PersonWithDemographic,
    Procedure,
    ProfClaim,
    ProfLine,
    Provider,
    Subscriber,
    Transaction837,
)

from .basic_codes import (
    ACUTE_INPATIENT_FACILITY,
    BASIC_HCPCS_DRUG_CODES,
    EITHER_FACILITY_TYPES,
    EXCLUSIVE_CONDITION_PAIRS,
    EXCLUSIVE_OCCURRENCE_PAIRS,
    EXPIRED_DISCHARGE_STATUSES,
    FEMALE_ONLY_PCS_CODES,
    ICD10_PCS_PROCEDURE_CODES,
    INPATIENT_FACILITY_TYPES,
    INPATIENT_ONLY_CONDITION_CODES,
    INPATIENT_ONLY_OCCURRENCE_CODES,
    INPATIENT_ONLY_SPAN_CODES,
    INPATIENT_ONLY_VALUE_CODES,
    MS_DRG_CODES,
    OUTPATIENT_FACILITY_TYPES,
    OUTPATIENT_ONLY_CONDITION_CODES,
    PRIOR_STAY_SPAN_CODES,
    SNF_FACILITY,
    SNF_ONLY_OCCURRENCE_SPAN_CODES,
    TRANSFER_DISCHARGE_STATUSES,
    UB04_CONDITION_CODES,
    UB04_OCCURRENCE_CODES,
    UB04_OCCURRENCE_SPAN_CODES,
    UB04_VALUE_CODES,
    UNSUPPORTED_CONDITION_CODES,
    UNSUPPORTED_OCCURRENCE_CODES,
    UNSUPPORTED_VALUE_CODES,
    WITHIN_STAY_SPAN_CODES,
    BasicHCPCSDrugCode,
)
from .helpers import (
    generate_address,
    generate_birth_date,
    generate_gender,
    generate_member_id,
    generate_npi,
    generate_patient_control_number,
    generate_person_name,
    generate_service_date,
    generate_tax_id,
    random_float,
)
from .reference_data import (
    CITIES_STATES,
    FIRST_NAMES,
    LAST_NAMES,
    Gender,
    Payer,
    PlaceOfService,
    place_of_service,
    sample_payer,
)
from .stats import (
    age_band_for,
    catalog,
    code_description,
    ratio,
    sample_conditional,
    sample_correlated,
    sample_count,
    sample_distribution,
    sample_numeric,
    sample_presence,
)

ClaimT = TypeVar("ClaimT", ProfClaim, InstClaim)
T = TypeVar("T")

# Procedures that put a patient in a bed. A forced CPT outside this set bills an
# outpatient encounter, however the claim type would otherwise have been drawn.
_INPATIENT_CPT_CODES = {"27447", "47562", "29881", "49505"}

# How often a claim reports occurrence spans at all. Outpatient bills are gated
# lower because 73 is the only span reportable on one: at the inpatient rate,
# every outpatient claim that passed the gate would emit a 73 and that single
# code would be the majority of all spans generated.
_SPAN_REPORT_RATE = 0.35
_OUTPATIENT_SPAN_REPORT_RATE = 0.08

_INST_REVENUE_CODES: dict[str, tuple[str, str]] = {
    "clinic": ("0510", "Clinic"),
    "lab": ("0300", "Laboratory"),
    "radiology": ("0320", "Diagnostic radiology"),
    "operating_room": ("0360", "Operating room services"),
    "pharmacy": ("0250", "Pharmacy"),
    "supplies": ("0270", "Medical/surgical supplies"),
    "room": ("0120", "Room and board - semi-private"),
}

SURGICAL_TAXONOMIES = [
    (code, desc)
    for code, desc in catalog("provider_taxonomy").items()
    if "surg" in desc.lower()
]


@dataclass
class PatientContext:
    """Shared identity for claims belonging to the same HAR group.

    All claims in a group share patient demographics, subscriber/payer info,
    and billing provider — just like real-world multi-PCN encounters.
    """

    patient_first: str
    patient_last: str
    patient_middle: str
    patient_dob: date
    patient_gender: Gender
    patient_address: Address
    subscriber_first: str
    subscriber_last: str
    subscriber_middle: str
    subscriber_dob: date
    subscriber_gender: Gender
    subscriber_address: Address
    relationship: Literal["CHILD", "SPOUSE", "OTHER", "SELF"]
    member_id: str
    group_or_policy_number: str
    payer_info: Payer
    billing_provider: Provider
    rendering_provider: Provider
    base_service_date: date
    institutional_billing_provider: Provider | None = None
    mrn: str | None = None
    pos: PlaceOfService = field(default_factory=place_of_service)


@dataclass(frozen=True)
class _Encounter:
    """What every UB-04 code helper needs to know about the encounter.

    UB-04 fields constrain each other, so the helpers that pick conditions,
    occurrences, spans, value codes and procedures cannot draw independently —
    each needs the claim type, the discharge status and the statement period
    already chosen upstream. Same parameter-object idea as ``PatientContext``,
    scoped to one institutional claim instead of a HAR group.
    """

    is_inpatient: bool
    facility: str
    patient_status: str
    statement_from: date
    statement_to: date
    patient_gender: Gender
    patient_dob: date

    @property
    def stay_days(self) -> int:
        """Nights billed: 0 on a same-day stay and on every outpatient claim."""
        return (self.statement_to - self.statement_from).days

    @property
    def is_same_day(self) -> bool:
        return self.statement_from == self.statement_to

    @property
    def is_expired(self) -> bool:
        return self.patient_status in EXPIRED_DISCHARGE_STATUSES

    def clamp(self, day: date) -> date:
        """Keep a generated date on or after the patient was born."""
        return max(day, self.patient_dob)


def rebase_patient_context(ctx: PatientContext, service_date: date) -> PatientContext:
    """Point a reused patient at a different encounter date.

    A returning patient's context is built for one date and then replayed at
    others — an encounter sequence can place a step months earlier than the
    anchor — which for a patient born this year would bill a visit from before
    they existed. The encounter moves up to the birth date rather than the birth
    date moving back to the encounter, so one patient keeps one date of birth
    across every claim they appear on.
    """
    return replace(ctx, base_service_date=max(service_date, ctx.patient_dob))


class ClaimGenerator:
    """Generator for 837P professional claims."""

    def __init__(self, seed: int | None = None, drug_defect_rate: float = 0.0):
        """Initialize the generator with optional seed for reproducibility.

        Args:
            seed: Random seed for reproducible output.
            drug_defect_rate: Fraction of drug service lines that carry a
                defect (missing NDC or quantity mismatch). Defaults to 0 so
                callers that don't need defects are unaffected.
        """
        if seed is not None:
            random.seed(seed)
        self.generated_pcns: set[str] = set()
        self._drug_defect_rate = drug_defect_rate
        self._rendering_providers: list[Provider] = []
        self._billing_provider: Provider | None = None
        self._institutional_billing_provider: Provider | None = None

    def generate_patient_context(
        self,
        service_date: date | None = None,
    ) -> PatientContext:
        """Generate shared patient/subscriber/payer/provider identity.

        Used to create a reusable context so multiple claims (PCNs) belonging
        to the same HAR group share identical demographics.
        """
        base_service_date = service_date or generate_service_date(
            days_ago_min=1, days_ago_max=90
        )
        payer_info = sample_payer()
        rendering_provider = self._pick_or_generate_rendering_provider()
        taxonomy = rendering_provider.provider_taxonomy
        age_band = sample_conditional(
            "provider_taxonomy_age_band",
            taxonomy.code if taxonomy else "",
            fallback="age_band",
        )
        patient_gender = cast(Gender, generate_gender())
        patient_first, patient_last, patient_middle = generate_person_name(
            patient_gender
        )
        # A newborn's birth date can otherwise fall after the encounter it is
        # being billed for.
        patient_dob = generate_birth_date(
            age_band=age_band,
            reference_date=base_service_date,
        )
        patient_address = generate_address()

        is_self = random.random() < 0.7
        if is_self:
            subscriber_first, subscriber_last, subscriber_middle = (
                patient_first,
                patient_last,
                patient_middle,
            )
            subscriber_dob = patient_dob
            subscriber_gender = patient_gender
            relationship: Literal["CHILD", "SPOUSE", "OTHER", "SELF"] = "SELF"
        else:
            subscriber_gender = cast(Gender, generate_gender())
            subscriber_first, subscriber_last, subscriber_middle = generate_person_name(
                subscriber_gender
            )
            subscriber_dob = generate_birth_date(min_age=25, max_age=75)
            relationship = random.choice(["CHILD", "SPOUSE", "OTHER"])

        if self._billing_provider is None:
            self._billing_provider = self._generate_billing_provider()
        if self._institutional_billing_provider is None:
            self._institutional_billing_provider = (
                self._generate_institutional_billing_provider()
            )

        return PatientContext(
            patient_first=patient_first,
            patient_last=patient_last,
            patient_middle=patient_middle,
            patient_dob=patient_dob,
            patient_gender=patient_gender,
            patient_address=patient_address,
            subscriber_first=subscriber_first,
            subscriber_last=subscriber_last,
            subscriber_middle=subscriber_middle,
            subscriber_dob=subscriber_dob,
            subscriber_gender=subscriber_gender,
            subscriber_address=generate_address(),
            relationship=relationship,
            member_id=generate_member_id(),
            group_or_policy_number=generate_member_id()[:10],
            payer_info=payer_info,
            billing_provider=self._billing_provider,
            institutional_billing_provider=self._institutional_billing_provider,
            rendering_provider=rendering_provider,
            base_service_date=base_service_date,
        )

    def generate_claim(
        self,
        ctx: PatientContext | None = None,
        service_date: date | None = None,
        forced_cpt_codes: list[str] | None = None,
        forced_icd10_codes: list[str] | None = None,
    ) -> ProfClaim:
        """Generate a single realistic 837P claim.

        Args:
            ctx: Shared patient context for multi-PCN HAR groups.
                 If None, a fresh context is created (single-PCN behaviour).
            service_date: Override service date. If None, uses ctx.base_service_date.
            forced_cpt_codes: If provided, use these CPT codes for service lines
                instead of random selection (one line per code).
            forced_icd10_codes: If provided, use these ICD-10 codes as diagnoses
                instead of random selection.
        """
        if ctx is None:
            ctx = self.generate_patient_context(service_date=service_date)

        pcn = self._generate_unique_pcn()
        svc_date = service_date or ctx.base_service_date

        # Generate service lines
        if forced_cpt_codes:
            num_lines = len(forced_cpt_codes)
        else:
            num_lines = sample_count(
                "service_lines_per_claim",
                correlation=("claim_type_service_lines_per_claim", "PROF"),
                minimum=1,
            )

        service_lines: list[ProfLine] = []

        for i in range(num_lines):
            forced_code = forced_cpt_codes[i] if forced_cpt_codes else None
            line = self._generate_service_line(
                i + 1,
                svc_date,
                ctx,
                forced_cpt=forced_code,
            )
            service_lines.append(line)
        self._scale_claim_charges(service_lines, "PROF")
        total_charge = sum(line.charge_amount for line in service_lines)

        if forced_icd10_codes:
            diags = self._build_forced_diagnoses(forced_icd10_codes)
            for line in service_lines:
                line.diag_pointers = list(range(1, min(3, len(diags)) + 1))
        else:
            diags = self._generate_diagnoses(service_lines, ctx, svc_date)
        pos = place_of_service(service_lines[0].place_of_service_code)

        return ProfClaim(
            id=str(uuid.uuid4()).replace("-", "")[:24],
            object_type="CLAIM",
            patient_control_number=pcn,
            charge_amount=float(total_charge),
            facility_code=Code(
                sub_type="PLACE_OF_SERVICE",
                code=pos.code,
                desc=pos.desc,
            ),
            frequency_code=Code(
                sub_type="FREQUENCY_CODE",
                code="1",
                desc="Original claim",
            ),
            service_date_from=svc_date,
            service_date_to=svc_date,
            subscriber=self._generate_subscriber(ctx),
            patient=self._generate_patient(ctx),
            provider_signature_indicator="Y",
            assignment_participation_code="A",
            assignment_certification_indicator="Y",
            release_of_information_code="Y",
            medical_record_number=ctx.mrn,
            billing_provider=ctx.billing_provider,
            providers=self._providers_for_claim(ctx),
            diags=diags,
            service_lines=service_lines,
            transaction=self._generate_transaction(pcn),
        )

    def generate_institutional_claim(
        self,
        ctx: PatientContext | None = None,
        service_date: date | None = None,
        forced_cpt_codes: list[str] | None = None,
        forced_icd10_codes: list[str] | None = None,
    ) -> InstClaim:
        """Generate a single realistic 837I institutional claim."""
        if ctx is None:
            ctx = self.generate_patient_context(service_date=service_date)

        pcn = self._generate_unique_pcn()
        svc_date = service_date or ctx.base_service_date
        is_inpatient = self._is_inpatient_inst_claim(forced_cpt_codes)
        classification = "inpatient" if is_inpatient else "outpatient"
        stay_days = sample_count(
            "length_of_stay_days",
            correlation=(
                "inpatient_classification_length_of_stay",
                classification,
            ),
        )
        statement_to = svc_date + timedelta(days=stay_days)

        service_lines = self._generate_institutional_service_lines(
            svc_date,
            stay_days + 1,
            ctx,
            is_inpatient,
            forced_cpt_codes=forced_cpt_codes,
        )
        self._scale_claim_charges(service_lines, "INST")
        total_charge = sum(line.charge_amount for line in service_lines)

        if forced_icd10_codes:
            diags = self._build_forced_inst_diagnoses(
                forced_icd10_codes,
                include_poa=is_inpatient,
            )
        else:
            diags = self._generate_inst_diagnoses(
                service_lines,
                ctx,
                svc_date,
                include_poa=is_inpatient,
            )
        diags += self._generate_admitting_diagnosis(is_inpatient, diags[0])
        diags += self._generate_reason_for_visit_diagnoses(is_inpatient)

        facility_code, patient_status = self._institutional_claim_codes(is_inpatient)
        enc = _Encounter(
            is_inpatient=is_inpatient,
            facility=facility_code.code,
            patient_status=patient_status,
            statement_from=svc_date,
            statement_to=statement_to,
            patient_gender=ctx.patient_gender,
            patient_dob=ctx.patient_dob,
        )

        procs = self._generate_procs(enc)
        operating = self._generate_operating_provider(
            has_procs=procs is not None,
            facility=facility_code.code,
        )
        admission_dt = None
        discharge_dt = None
        if is_inpatient:
            admission_hour, discharge_hour = self._admission_discharge_hours(
                same_day=enc.is_same_day
            )
            admission_dt = self._aware_datetime(svc_date, admission_hour)
            if patient_status != "30":
                discharge_dt = self._aware_datetime(statement_to, discharge_hour)

        return InstClaim(
            id=str(uuid.uuid4()).replace("-", "")[:24],
            object_type="CLAIM",
            patient_control_number=pcn,
            charge_amount=float(round(total_charge, 2)),
            facility_code=facility_code,
            frequency_code=Code(
                sub_type="FREQUENCY_CODE",
                code="1",
                desc="Original claim",
            ),
            statement_date_from=svc_date,
            statement_date_to=statement_to,
            service_date_from=svc_date,
            service_date_to=statement_to,
            subscriber=self._generate_subscriber(ctx),
            patient=self._generate_patient(ctx),
            assignment_participation_code="A",
            assignment_certification_indicator="Y",
            release_of_information_code="Y",
            medical_record_number=ctx.mrn,
            admission_date_and_hour=admission_dt,
            discharge_time=discharge_dt,
            admission_type_code=(
                sample_distribution("admission_type") if is_inpatient else None
            ),
            admission_source_code=(
                sample_distribution("admission_source") if is_inpatient else None
            ),
            patient_status_code=patient_status,
            billing_provider=ctx.institutional_billing_provider or ctx.billing_provider,
            providers=[
                ctx.rendering_provider.model_copy(update={"entity_role": "ATTENDING"}),
                *([operating] if operating else []),
            ],
            diags=diags,
            procs=procs,
            drg=self._generate_drg(enc),
            conditions=self._generate_conditions(enc),
            occurrences=self._generate_occurrences(enc),
            occurrence_spans=self._generate_occurrence_spans(enc),
            value_infos=self._generate_value_infos(enc),
            service_lines=service_lines,
            transaction=self._generate_transaction(
                pcn, transaction_type="institutional"
            ),
        )

    def generate_revised_claim(
        self, claim: ProfClaim | InstClaim
    ) -> ProfClaim | InstClaim:
        """Return a replacement version of an existing claim."""
        pcn = self._generate_unique_pcn()
        transaction_type = (
            "institutional"
            if claim.transaction.transaction_type == "INST"
            else "professional"
        )
        return claim.model_copy(
            deep=True,
            update={
                "id": str(uuid.uuid4()).replace("-", "")[:24],
                "patient_control_number": pcn,
                "frequency_code": Code(
                    sub_type="FREQUENCY_CODE",
                    code="7",
                    desc="Replacement claim",
                ),
                "original_reference_number": claim.patient_control_number,
                "transaction": self._generate_transaction(
                    pcn, transaction_type=transaction_type
                ),
            },
        )

    def generate_refiled_claim(self, claim: ClaimT) -> ClaimT:
        """Refile a clearinghouse-rejected claim as an original submission."""
        transaction_type = (
            "institutional"
            if claim.transaction.transaction_type == "INST"
            else "professional"
        )
        return claim.model_copy(
            deep=True,
            update={
                "id": str(uuid.uuid4()).replace("-", "")[:24],
                "frequency_code": Code(
                    sub_type="FREQUENCY_CODE",
                    code="1",
                    desc="Original claim",
                ),
                "original_reference_number": None,
                "transaction": self._generate_transaction(
                    claim.patient_control_number,
                    transaction_type=transaction_type,
                ),
            },
        )

    def _generate_unique_pcn(self) -> str:
        """Generate a unique patient control number."""
        while True:
            pcn = generate_patient_control_number()
            if pcn not in self.generated_pcns:
                self.generated_pcns.add(pcn)
                return pcn

    @staticmethod
    def _sample_procedure(
        ctx: PatientContext,
        service_date: date,
        claim_type: Literal["PROF", "INST"],
    ) -> str:
        taxonomy = ctx.rendering_provider.provider_taxonomy
        return sample_correlated(
            "procedure",
            (
                ("claim_type_procedure", claim_type),
                ("provider_taxonomy_procedure", taxonomy.code if taxonomy else ""),
                ("age_band_procedure", age_band_for(ctx.patient_dob, service_date)),
                ("gender_procedure", ctx.patient_gender),
                ("insurance_plan_procedure", ctx.payer_info.plan_type),
            ),
        )

    @staticmethod
    def _scale_claim_charges(
        lines: list[ProfLine] | list[InstLine],
        claim_type: Literal["PROF", "INST"],
    ) -> None:
        target = max(
            0.01 * len(lines),
            sample_numeric(
                "claim_charge_bucket",
                conditions=(("claim_type_claim_charge_bucket", claim_type),),
            ),
        )
        current = sum(line.charge_amount for line in lines)
        if current <= 0:
            return
        factor = target / current
        for line in lines:
            line.charge_amount = max(0.01, round(line.charge_amount * factor, 2))

    def _generate_service_line(
        self,
        line_num: int,
        service_date: date,
        ctx: PatientContext,
        forced_cpt: str | None = None,
    ) -> ProfLine:
        """Generate one empirically weighted professional service line."""
        procedure_code = forced_cpt or self._sample_procedure(ctx, service_date, "PROF")
        drug_data = self._select_drug_for_line(procedure_code)
        if drug_data is not None:
            line = self._generate_drug_service_line(
                line_num, service_date, drug_data, procedure_code
            )
            if self._drug_defect_rate > 0 and random.random() < self._drug_defect_rate:
                self._apply_drug_defect(line)
            return line

        charge = sample_numeric(
            "line_charge_bucket",
            conditions=(
                ("claim_type_procedure_charge_bucket", ("PROF", procedure_code)),
                ("procedure_charge_bucket", procedure_code),
            ),
        )
        units = max(
            0.1,
            sample_numeric(
                "unit_count_bucket",
                conditions=(("procedure_unit_count_bucket", procedure_code),),
                precision=2,
            ),
        )
        modifiers: list[Code] | None = None
        modifier_count = (
            sample_count("modifier_count_per_line")
            if sample_presence("line_has_modifier")
            else 0
        )
        if modifier_count:
            selected_modifiers: list[str] = []
            while len(selected_modifiers) < modifier_count:
                available = set(catalog("modifier")) - set(selected_modifiers)
                if not available:
                    break
                modifier = sample_correlated(
                    "modifier",
                    (
                        ("procedure_modifier", procedure_code),
                        ("claim_type_modifier", "PROF"),
                    ),
                    allowed=available,
                )
                selected_modifiers.append(modifier)
            modifiers = [
                Code(
                    sub_type="HCPCS_MODIFIER",
                    code=modifier,
                    desc=code_description("modifier", modifier),
                )
                for modifier in selected_modifiers
            ]

        return ProfLine(
            source_line_id=f"LINE{line_num}",
            charge_amount=max(0.01, float(charge)),
            service_date_from=service_date,
            place_of_service_code=sample_conditional(
                "procedure_place_of_service",
                procedure_code,
                fallback="place_of_service",
            ),
            unit_type="UNIT",
            unit_count=float(units),
            procedure=Procedure(
                sub_type="CPT" if procedure_code.isdigit() else "HCPCS",
                code=procedure_code,
                desc=code_description("procedure", procedure_code),
                modifiers=modifiers,
            ),
            diag_pointers=[],
        )

    def _generate_institutional_service_lines(
        self,
        service_date: date,
        stay_days: int,
        ctx: PatientContext,
        is_inpatient: bool,
        forced_cpt_codes: list[str] | None = None,
    ) -> list[InstLine]:
        if forced_cpt_codes:
            return [
                self._generate_institutional_service_line(
                    i + 1, service_date, ctx, code
                )
                for i, code in enumerate(forced_cpt_codes)
            ]

        lines: list[InstLine] = []
        if is_inpatient and stay_days > 1:
            lines.append(self._generate_room_and_board_line(1, service_date, stay_days))

        num_ancillary = sample_count(
            "service_lines_per_claim",
            correlation=("claim_type_service_lines_per_claim", "INST"),
            minimum=1,
        ) - len(lines)
        for _ in range(num_ancillary):
            lines.append(
                self._generate_institutional_service_line(
                    len(lines) + 1,
                    service_date + timedelta(days=random.randint(0, stay_days - 1)),
                    ctx,
                )
            )
        return lines

    @staticmethod
    def _generate_room_and_board_line(
        line_num: int,
        service_date: date,
        stay_days: int,
    ) -> InstLine:
        rev_code, rev_desc = _INST_REVENUE_CODES["room"]
        charge = sample_numeric("line_charge_bucket")
        return InstLine(
            source_line_id=f"LINE{line_num}",
            charge_amount=float(round(charge, 2)),
            service_date_from=service_date,
            service_date_to=service_date + timedelta(days=stay_days - 1),
            unit_type="DAY",
            unit_count=float(stay_days),
            revenue_code=Code(
                sub_type="REVENUE_CODE",
                code=rev_code,
                desc=rev_desc,
            ),
        )

    def _generate_institutional_service_line(
        self,
        line_num: int,
        service_date: date,
        ctx: PatientContext,
        forced_cpt: str | None = None,
    ) -> InstLine:
        procedure_code = forced_cpt or self._sample_procedure(ctx, service_date, "INST")
        revenue_code = sample_conditional(
            "procedure_revenue_code",
            procedure_code,
            fallback="revenue_code",
        )
        units = max(
            0.1,
            sample_numeric(
                "unit_count_bucket",
                conditions=(("procedure_unit_count_bucket", procedure_code),),
                precision=2,
            ),
        )
        charge = sample_numeric(
            "line_charge_bucket",
            conditions=(
                ("claim_type_procedure_charge_bucket", ("INST", procedure_code)),
                ("procedure_charge_bucket", procedure_code),
            ),
        )

        return InstLine(
            source_line_id=f"LINE{line_num}",
            charge_amount=float(round(charge, 2)),
            service_date_from=service_date,
            service_date_to=service_date,
            unit_type="UNIT",
            unit_count=float(units),
            revenue_code=Code(
                sub_type="REVENUE_CODE",
                code=revenue_code,
                desc=code_description("revenue_code", revenue_code),
            ),
            procedure=Procedure(
                sub_type="CPT" if procedure_code.isdigit() else "HCPCS",
                code=procedure_code,
                desc=code_description("procedure", procedure_code),
            ),
        )

    @staticmethod
    def _select_drug_for_line(forced_cpt: str | None) -> BasicHCPCSDrugCode | None:
        """Return NDC metadata when the empirically selected code has it."""
        return next(
            (d for d in BASIC_HCPCS_DRUG_CODES if d.hcpcs_code == forced_cpt),
            None,
        )

    @staticmethod
    def _generate_drug_service_line(
        line_num: int,
        service_date: date,
        drug_data: BasicHCPCSDrugCode,
        procedure_code: str,
    ) -> ProfLine:
        """Generate a service line that bills a clinician-administered drug.

        The procedure is the drug's HCPCS J-code and the line additionally
        reports the National Drug Code (NDC). The NDC quantity is derived from
        the billed unit count and the J-code's per-unit dosing so the reported
        drug quantity stays consistent with the procedure and units.
        """
        units = min(
            drug_data.max_units,
            max(
                1,
                round(
                    sample_numeric(
                        "unit_count_bucket",
                        conditions=(("procedure_unit_count_bucket", procedure_code),),
                    )
                ),
            ),
        )
        charge = sample_numeric(
            "line_charge_bucket",
            conditions=(("procedure_charge_bucket", procedure_code),),
        )
        drug_quantity = round(units * drug_data.ndc_qty_per_unit, 3)

        return ProfLine(
            source_line_id=f"LINE{line_num}",
            charge_amount=max(0.01, float(round(charge, 2))),
            service_date_from=service_date,
            unit_type="UNIT",
            unit_count=float(units),
            procedure=Procedure(
                sub_type="HCPCS",
                code=drug_data.hcpcs_code,
                desc=drug_data.description,
            ),
            drug=Code(
                sub_type="NDC",
                code=drug_data.ndc,
                desc=drug_data.drug_name,
            ),
            drug_quantity=drug_quantity,
            drug_unit_type=drug_data.ndc_unit,
            diag_pointers=[],
        )

    @staticmethod
    def _apply_drug_defect(line: ProfLine) -> None:
        """Mutate a drug service line to introduce a defect.

        Half the time the NDC is omitted entirely (missing-NDC defect); the
        other half the drug quantity is scaled down so it no longer matches
        the billed units (quantity-mismatch defect).
        """
        if random.random() < 0.5:
            line.drug = None
            line.drug_quantity = None
            line.drug_unit_type = None
        else:
            if line.drug_quantity is not None and line.drug_quantity > 0:
                line.drug_quantity = round(
                    line.drug_quantity * random.uniform(0.3, 0.8), 3
                )

    def _generate_diagnoses(
        self,
        service_lines: list[ProfLine],
        ctx: PatientContext,
        service_date: date,
    ) -> list[Code]:
        """Generate claim diagnoses and line pointers from empirical counts."""
        target = sample_count("diagnoses_per_claim", minimum=1)
        age_band = age_band_for(ctx.patient_dob, service_date)
        selected: list[str] = []
        attempts = 0
        while len(selected) < target and attempts < target * 10:
            line_procedure = service_lines[attempts % len(service_lines)].procedure
            procedure = line_procedure.code if line_procedure else ""
            diagnosis = sample_correlated(
                "diagnosis",
                (
                    ("procedure_diagnosis", procedure),
                    ("age_band_diagnosis", age_band),
                ),
            )
            if diagnosis not in selected:
                selected.append(diagnosis)
            attempts += 1

        for line in service_lines:
            count = min(sample_count("diagnoses_per_line"), len(selected))
            line.diag_pointers = list(range(1, count + 1))

        return [
            Code(
                sub_type="ICD_10_PRINCIPAL" if i == 0 else "ICD_10",
                code=code,
                desc=code_description("diagnosis", code),
            )
            for i, code in enumerate(selected)
        ]

    def _build_forced_diagnoses(self, icd10_codes: list[str]) -> list[Code]:
        """Build diagnosis list from specific ICD-10 codes."""
        return [
            Code(
                sub_type="ICD_10_PRINCIPAL" if i == 0 else "ICD_10",
                code=code.replace(".", ""),
                desc=code_description("diagnosis", code.replace(".", "")),
            )
            for i, code in enumerate(icd10_codes)
        ]

    @staticmethod
    def _sample(pool: list[T], most: int) -> list[T]:
        """Draw 1..most distinct entries, never asking for more than exist.

        Every UB-04 pool below is filtered down by claim type before sampling,
        so the requested count has to be clamped or ``random.sample`` raises.
        """
        return random.sample(pool, random.randint(1, min(most, len(pool))))

    @staticmethod
    def _drop_exclusive(
        picked: list[tuple[str, str]],
        pairs: frozenset[frozenset[str]],
    ) -> list[tuple[str, str]]:
        """Drop the later member of any mutually exclusive pair.

        Filtering the pool up front cannot express this: ``random.sample``
        guarantees distinctness, not compatibility, so conflicts are resolved
        after the draw rather than by retrying it.
        """
        kept: list[tuple[str, str]] = []
        kept_codes: set[str] = set()
        for code, desc in picked:
            if any(pair <= kept_codes | {code} for pair in pairs if code in pair):
                continue
            kept.append((code, desc))
            kept_codes.add(code)
        return kept

    @staticmethod
    def _generate_drg(enc: _Encounter) -> Code | None:
        """UB-04 FL 71."""
        if not enc.is_inpatient or enc.facility not in {
            ACUTE_INPATIENT_FACILITY,
            *EITHER_FACILITY_TYPES,
        }:
            return None
        code, desc = random.choice(MS_DRG_CODES)
        return Code(sub_type="DRG", code=code, desc=desc)

    @classmethod
    def _generate_conditions(cls, enc: _Encounter) -> list[Code] | None:
        """UB-04 FL 18-28."""
        if random.random() >= 0.4:
            return None
        excluded = set(UNSUPPORTED_CONDITION_CODES)
        excluded |= (
            OUTPATIENT_ONLY_CONDITION_CODES
            if enc.is_inpatient
            else INPATIENT_ONLY_CONDITION_CODES
        )
        # A same day transfer needs both a zero-day stay and somewhere to go.
        if not (enc.is_same_day and enc.patient_status in TRANSFER_DISCHARGE_STATUSES):
            excluded.add("40")

        pool = [c for c in UB04_CONDITION_CODES if c[0] not in excluded]
        if not pool:
            return None
        picked = cls._drop_exclusive(cls._sample(pool, 3), EXCLUSIVE_CONDITION_PAIRS)
        return [
            Code(sub_type="CONDITION_CODE", code=code, desc=desc)
            for code, desc in picked
        ] or None

    @classmethod
    def _generate_occurrences(cls, enc: _Encounter) -> list[CodeAndDate] | None:
        """UB-04 FL 31-34.

        Occurrence 55 is a biconditional rather than a filter: a patient who
        died must carry it and a patient who did not must not, so it is placed
        before the random gate that decides whether to report anything at all.
        """
        excluded = set(UNSUPPORTED_OCCURRENCE_CODES) | {"55"}
        if not enc.is_inpatient:
            excluded |= INPATIENT_ONLY_OCCURRENCE_CODES

        picked: list[tuple[str, str]] = []
        if enc.is_expired:
            picked.append(("55", "Date of death"))

        if random.random() < 0.5:
            pool = [c for c in UB04_OCCURRENCE_CODES if c[0] not in excluded]
            if pool:
                picked += cls._sample(pool, 2)
        picked = cls._drop_exclusive(picked, EXCLUSIVE_OCCURRENCE_PAIRS)
        if not picked:
            return None

        return [
            CodeAndDate(
                sub_type="OCCURRENCE_CODE",
                code=code,
                desc=desc,
                occurrence_date=cls._occurrence_date(code, enc),
            )
            for code, desc in picked
        ]

    @staticmethod
    def _occurrence_date(code: str, enc: _Encounter) -> date:
        """Date an occurrence according to what the code actually means."""
        if code == "55":
            # Death ends the stay, so it is dated at the statement through date.
            return enc.statement_to
        if code == "40":
            # A scheduled admission is scheduled before it happens.
            return enc.clamp(enc.statement_from - timedelta(days=random.randint(0, 14)))
        return enc.clamp(enc.statement_from - timedelta(days=random.randint(0, 10)))

    @classmethod
    def _generate_occurrence_spans(
        cls, enc: _Encounter
    ) -> list[CodeAndDateRange] | None:
        """UB-04 FL 35-36. A span still open at billing time is reported with its
        from date alone, so FL 36 is left empty on some of them."""
        report_rate = (
            _SPAN_REPORT_RATE if enc.is_inpatient else _OUTPATIENT_SPAN_REPORT_RATE
        )
        if random.random() >= report_rate:
            return None
        excluded: set[str] = set()
        if enc.facility != SNF_FACILITY:
            excluded |= SNF_ONLY_OCCURRENCE_SPAN_CODES
        if not enc.is_inpatient:
            excluded |= INPATIENT_ONLY_SPAN_CODES

        pool = [c for c in UB04_OCCURRENCE_SPAN_CODES if c[0] not in excluded]
        if not pool:
            return None

        spans: list[CodeAndDateRange] = []
        for code, desc in cls._sample(pool, 2):
            span_from, span_to = cls._span_dates(code, enc)
            spans.append(
                CodeAndDateRange(
                    sub_type="OCCURRENCE_SPAN_CODE",
                    code=code,
                    desc=desc,
                    occurrence_date=span_from,
                    occurrence_end_date=None if random.random() < 0.2 else span_to,
                )
            )
        return spans

    @staticmethod
    def _span_dates(code: str, enc: _Encounter) -> tuple[date, date]:
        """Place a span in time according to what the code describes.

        A prior stay has to be over before this one starts; a span describing
        part of this encounter has to fall inside the statement period. The
        statement period collapses to a single day on a same-day claim, so both
        ends are clamped rather than offset blindly.
        """
        if code in PRIOR_STAY_SPAN_CODES:
            span_from = enc.clamp(
                enc.statement_from - timedelta(days=random.randint(3, 60))
            )
            span_to = min(
                span_from + timedelta(days=random.randint(1, 10)),
                enc.statement_from,
            )
            return span_from, span_to
        if code in WITHIN_STAY_SPAN_CODES:
            span_from = enc.statement_from + timedelta(
                days=random.randint(0, enc.stay_days)
            )
            span_to = min(
                span_from + timedelta(days=random.randint(0, enc.stay_days)),
                enc.statement_to,
            )
            return span_from, span_to
        # Benefit eligibility (73) is not tied to this encounter's dates.
        span_from = enc.clamp(
            enc.statement_from - timedelta(days=random.randint(3, 60))
        )
        return span_from, span_from + timedelta(days=random.randint(1, 10))

    @staticmethod
    def _generate_admitting_diagnosis(
        is_inpatient: bool, principal: InstDiagnosis
    ) -> list[InstDiagnosis]:
        """UB-04 FL 69. An inpatient admission reports the condition the patient
        was admitted for; an outpatient visit uses FL 70 instead. The locator
        carries no POA indicator of its own, and the code often repeats the
        principal diagnosis because the admission reason is what the stay treats.
        """
        if not is_inpatient or random.random() >= 0.8:
            return []
        if random.random() < 0.5:
            code, desc = principal.code, principal.desc
        else:
            code = sample_distribution("diagnosis")
            desc = code_description("diagnosis", code)
        return [InstDiagnosis(sub_type="ICD_10_ADMITTING", code=code, desc=desc)]

    @staticmethod
    def _generate_reason_for_visit_diagnoses(
        is_inpatient: bool,
    ) -> list[InstDiagnosis]:
        """UB-04 FL 70. Up to three, reported by outpatient and emergency claims
        to say why the patient presented; an inpatient admission uses FL 69
        instead. No POA indicator."""
        if is_inpatient or random.random() >= 0.5:
            return []
        codes: list[str] = []
        for _ in range(min(3, sample_count("diagnoses_per_claim", minimum=1))):
            code = sample_distribution("diagnosis")
            if code not in codes:
                codes.append(code)
        return [
            InstDiagnosis(
                sub_type="ICD_10_REASON_FOR_VISIT",
                code=code,
                desc=code_description("diagnosis", code),
            )
            for code in codes
        ]

    @classmethod
    def _generate_procs(cls, enc: _Encounter) -> list[CodeAndDate] | None:
        """UB-04 FL 74. Outpatient surgery is reported as CPT on the service
        lines instead, so an outpatient claim normally leaves this empty."""
        if not enc.is_inpatient or random.random() >= 0.4:
            return None
        pool = [
            c
            for c in ICD10_PCS_PROCEDURE_CODES
            if enc.patient_gender == "FEMALE" or c[0] not in FEMALE_ONLY_PCS_CODES
        ]
        if not pool:
            return None
        return [
            CodeAndDate(
                sub_type="ICD_10_PCS",
                code=code,
                desc=desc,
                occurrence_date=enc.statement_from
                + timedelta(days=random.randint(0, enc.stay_days)),
            )
            for code, desc in cls._sample(pool, 2)
        ]

    def _generate_operating_provider(
        self, *, has_procs: bool, facility: str
    ) -> Provider | None:
        """UB-04 FL 77. Required once a surgical procedure is listed, and also
        reported by outpatient surgical claims whose FL 74 stays empty. A home
        health or SNF bill has no operating provider either way."""
        chance = 0.85 if has_procs else (0.15 if facility == "13" else 0.0)
        if random.random() >= chance:
            return None
        code, desc = random.choice(SURGICAL_TAXONOMIES)
        return self._generate_rendering_provider(code).model_copy(
            update={
                "entity_role": "OPERATING",
                "provider_taxonomy": Code(
                    sub_type="PROVIDER_TAXONOMY", code=code, desc=desc
                ),
            }
        )

    @staticmethod
    def _generate_value_infos(enc: _Encounter) -> list[CodeAndAmount] | None:
        """UB-04 FL 39-41."""
        values: list[CodeAndAmount] = []
        if enc.is_inpatient:
            values.append(
                CodeAndAmount(
                    sub_type="VALUE_CODE",
                    code="80",
                    desc="Covered days",
                    amount=float(enc.stay_days + 1),
                )
            )
        if random.random() < 0.5:
            excluded = set(UNSUPPORTED_VALUE_CODES)
            # A value code is reported once, and covered days is already derived
            # from the stay length above rather than drawn with a dollar amount.
            excluded |= {v.code for v in values}
            if not enc.is_inpatient:
                excluded |= INPATIENT_ONLY_VALUE_CODES
            pool = [c for c in UB04_VALUE_CODES if c[0] not in excluded]
            if pool:
                code, desc = random.choice(pool)
                values.append(
                    CodeAndAmount(
                        sub_type="VALUE_CODE",
                        code=code,
                        desc=desc,
                        amount=random_float(25.0, 1500.0),
                    )
                )
        return values or None

    def _generate_inst_diagnoses(
        self,
        service_lines: list[InstLine],
        ctx: PatientContext,
        service_date: date,
        include_poa: bool,
    ) -> list[InstDiagnosis]:
        num_diags = sample_count("diagnoses_per_claim", minimum=1)
        age_band = age_band_for(ctx.patient_dob, service_date)
        selected_codes: list[str] = []
        attempts = 0
        while len(selected_codes) < num_diags and attempts < num_diags * 10:
            procedure = service_lines[attempts % len(service_lines)].procedure
            code = sample_correlated(
                "diagnosis",
                (
                    ("procedure_diagnosis", procedure.code if procedure else ""),
                    ("age_band_diagnosis", age_band),
                ),
            )
            if code not in selected_codes:
                selected_codes.append(code)
            attempts += 1
        return [
            InstDiagnosis(
                sub_type="ICD_10_PRINCIPAL" if i == 0 else "ICD_10",
                code=code,
                desc=code_description("diagnosis", code),
                present_on_admission_indicator=self._poa_indicator(i, include_poa),
            )
            for i, code in enumerate(selected_codes)
        ]

    def _build_forced_inst_diagnoses(
        self,
        icd10_codes: list[str],
        include_poa: bool,
    ) -> list[InstDiagnosis]:
        return [
            InstDiagnosis(
                sub_type="ICD_10_PRINCIPAL" if i == 0 else "ICD_10",
                code=code.replace(".", ""),
                desc=code_description("diagnosis", code.replace(".", "")),
                present_on_admission_indicator=self._poa_indicator(i, include_poa),
            )
            for i, code in enumerate(icd10_codes)
        ]

    @staticmethod
    def _poa_indicator(index: int, include_poa: bool) -> str | None:
        if not include_poa:
            return None
        return "Y" if index == 0 else random.choice(["Y", "N", "U", "W"])

    @staticmethod
    def _generate_subscriber(ctx: PatientContext) -> Subscriber:
        return Subscriber(
            payer_responsibility_sequence="PRIMARY",
            relationship_type="SELF",
            group_or_policy_number=ctx.group_or_policy_number,
            claim_filing_indicator_code=ctx.payer_info.claim_filing_code,
            insurance_plan_type=ctx.payer_info.plan_type,
            person=PersonWithDemographic(
                entity_role="INSURED_SUBSCRIBER",
                entity_type="INDIVIDUAL",
                identification_type="MEMBER_ID",
                identifier=ctx.member_id,
                last_name_or_org_name=ctx.subscriber_last,
                first_name=ctx.subscriber_first,
                middle_name=ctx.subscriber_middle,
                birth_date=ctx.subscriber_dob,
                gender=ctx.subscriber_gender,
                address=ctx.subscriber_address,
            ),
            payer=Party(
                entity_role="PAYER",
                entity_type="BUSINESS",
                identification_type="PAYOR_ID",
                identifier=ctx.payer_info.identifier,
                tax_id=ctx.payer_info.tax_id,
                last_name_or_org_name=ctx.payer_info.name,
                address=generate_address(),
            ),
        )

    @staticmethod
    def _generate_patient(ctx: PatientContext) -> Patient:
        return Patient(
            relationship_type=ctx.relationship,
            person=PersonWithDemographic(
                entity_role="PATIENT",
                entity_type="INDIVIDUAL",
                last_name_or_org_name=ctx.patient_last,
                first_name=ctx.patient_first,
                middle_name=ctx.patient_middle,
                birth_date=ctx.patient_dob,
                gender=ctx.patient_gender,
                address=ctx.patient_address,
            ),
        )

    def _generate_billing_provider(self) -> Provider:
        """Generate billing provider information."""
        org_names = [
            "MEDICAL ASSOCIATES",
            "FAMILY HEALTH CENTER",
            "PRIMARY CARE CLINIC",
            "WELLNESS CENTER",
            "COMMUNITY HEALTH",
        ]

        suffix = random.choice(["LLC", "PC", "PA", "INC"])
        name = f"{random.choice(org_names)} {suffix}"

        return Provider(
            entity_role="BILLING_PROVIDER",
            entity_type="BUSINESS",
            identification_type="NPI",
            identifier=generate_npi(),
            tax_id=generate_tax_id(),
            last_name_or_org_name=name,
            address=generate_address(),
        )

    def _generate_institutional_billing_provider(self) -> Provider:
        org_names = [
            "GENERAL HOSPITAL",
            "REGIONAL MEDICAL CENTER",
            "COMMUNITY HOSPITAL",
            "MEMORIAL HOSPITAL",
            "UNIVERSITY MEDICAL CENTER",
        ]
        name = random.choice(org_names)

        return Provider(
            entity_role="BILLING_PROVIDER",
            entity_type="BUSINESS",
            identification_type="NPI",
            identifier=generate_npi(),
            tax_id=generate_tax_id(),
            last_name_or_org_name=name,
            address=generate_address(),
        )

    def _pick_or_generate_rendering_provider(self) -> Provider:
        new_provider_rate = ratio("unique_providers_per_claim") / ratio(
            "unique_patients_per_claim"
        )
        if self._rendering_providers and random.random() >= new_provider_rate:
            return random.choice(self._rendering_providers)
        provider = self._generate_rendering_provider()
        self._rendering_providers.append(provider)
        return provider

    def _providers_for_claim(self, ctx: PatientContext) -> list[Provider]:
        target = max(1, sample_count("providers_per_claim") - 1)
        providers = [ctx.rendering_provider]
        while len(providers) < target:
            used = {item.identifier for item in providers}
            available = [
                provider
                for provider in self._rendering_providers
                if provider.identifier not in used
            ]
            if available:
                providers.append(random.choice(available))
                continue
            provider = self._generate_rendering_provider()
            self._rendering_providers.append(provider)
            providers.append(provider)
        return providers

    @staticmethod
    def _generate_rendering_provider(taxonomy_code: str | None = None) -> Provider:
        """Generate rendering provider information.

        All attributes are derived deterministically from the NPI so that the
        same NPI always produces the same name, address, and taxonomy — across
        calls and across process runs — without any external cache.
        """
        npi = generate_npi()
        rng = random.Random(npi)

        first = rng.choice(FIRST_NAMES["UNKNOWN"])
        last = rng.choice(LAST_NAMES)
        middle = rng.choice(string.ascii_uppercase)
        tax_code = taxonomy_code or sample_distribution("provider_taxonomy")
        tax_desc = code_description("provider_taxonomy", tax_code)

        city_state = rng.choice(CITIES_STATES)
        street_number = rng.randint(100, 9999)
        street_name = rng.choice(
            [
                "MAIN ST",
                "OAK AVE",
                "MAPLE DR",
                "PARK BLVD",
                "WASHINGTON ST",
                "LINCOLN AVE",
                "LAKE DR",
                "HILL RD",
                "CHURCH ST",
                "SCHOOL ST",
            ]
        )
        line2 = None
        if rng.random() < 0.3:
            if rng.random() < 0.5:
                line2 = f"APT {rng.randint(1, 999)}"
            else:
                line2 = f"SUITE {rng.randint(100, 999)}"
        zip_code = city_state.zip
        if rng.random() < 0.7:
            zip_code += str(rng.randint(1000, 9999))

        return Provider(
            entity_role="RENDERING",
            entity_type="INDIVIDUAL",
            identification_type="NPI",
            identifier=npi,
            last_name_or_org_name=last,
            first_name=first,
            middle_name=middle,
            address=Address(
                line=f"{street_number} {street_name}",
                line2=line2,
                city=city_state.city,
                state_code=city_state.state,
                zip_code=zip_code,
            ),
            provider_taxonomy=Code(
                sub_type="PROVIDER_TAXONOMY",
                code=tax_code,
                desc=tax_desc,
            ),
        )

    @staticmethod
    def _is_inpatient_inst_claim(forced_cpt_codes: list[str] | None) -> bool:
        """Decide whether an institutional claim bills a stay.

        When the caller forces the procedure codes, the codes decide: an
        inpatient bill whose only service line is an office visit, with no room
        and board charge, is not a claim any hospital would send.
        """
        if forced_cpt_codes:
            return bool(set(forced_cpt_codes) & _INPATIENT_CPT_CODES)
        return sample_distribution("inpatient_classification") == "inpatient"

    @staticmethod
    def _admission_discharge_hours(*, same_day: bool) -> tuple[int, int]:
        """Pick admission and discharge hours that stay in order.

        On a multi-day stay the two hours sit on different dates and cannot
        cross. On a same-day stay they share a date, so the discharge hour is
        drawn first and the admission hour placed before it.
        """
        discharge_hour = random.randint(8, 18)
        if same_day:
            return random.randint(0, discharge_hour - 1), discharge_hour
        return random.randint(0, 23), discharge_hour

    @staticmethod
    def _institutional_claim_codes(is_inpatient: bool) -> tuple[Code, str]:
        facility_code = sample_conditional(
            "claim_type_facility_code",
            "INST",
            fallback="facility_code",
            allowed=(
                INPATIENT_FACILITY_TYPES | EITHER_FACILITY_TYPES
                if is_inpatient
                else OUTPATIENT_FACILITY_TYPES | EITHER_FACILITY_TYPES
            ),
        )
        patient_status = sample_distribution("patient_discharge_status")
        return (
            Code(
                sub_type="UB_FACILITY_TYPE",
                code=facility_code,
                desc=code_description("facility_code", facility_code),
            ),
            patient_status,
        )

    @staticmethod
    def _aware_datetime(day: date, hour: int) -> datetime:
        return datetime.combine(day, time(hour=hour), tzinfo=UTC)

    def _generate_transaction(
        self,
        pcn: str,
        transaction_type: Literal["professional", "institutional"] = "professional",
    ) -> Transaction837:
        """Generate transaction metadata."""
        is_inst = transaction_type == "institutional"
        return Transaction837(
            control_number=pcn[:8],
            transaction_type="INST" if is_inst else "PROF",
            hierarchical_structure_code="0019",
            purpose_code="00",
            originator_application_transaction_id=pcn[:8],
            creation_date=date.today(),
            creation_time=datetime.now().time(),
            claim_or_encounter_identifier_type="CHARGEABLE",
            transaction_set_identifier_code="837",
            implementation_convention_reference=(
                "005010X223A2" if is_inst else "005010X222A1"
            ),
            sender=PartyIdName(
                entity_role="SUBMITTER",
                entity_type="BUSINESS",
                identification_type="ETIN",
                identifier="SUBMIT" + generate_npi()[:4],
                last_name_or_org_name="MEDICAL BILLING SERVICE",
            ),
            receiver=PartyIdName(
                entity_role="RECEIVER",
                entity_type="BUSINESS",
                identification_type="ETIN",
                identifier="RECEIVE" + generate_npi()[:4],
                last_name_or_org_name="CLAIMS CLEARINGHOUSE",
            ),
        )
