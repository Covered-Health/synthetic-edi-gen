"""
Generate realistic 837P (Professional) claims.
"""

# ruff: noqa: S311 # insecure random numbers are fine here
import random
import uuid
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Literal, cast

from synthetic_edi_gen.edi_models import (
    Address,
    Code,
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

from .basic_codes import BASIC_CPT_CODES, BASIC_ICD10_CODES, BASIC_MODIFIERS
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
    COMMON_PAYERS,
    PLACE_OF_SERVICE,
    Gender,
    Payer,
    PlaceOfService,
)


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
    base_service_date: date
    pos: PlaceOfService = field(default_factory=lambda: random.choice(PLACE_OF_SERVICE))


class ClaimGenerator:
    """Generator for 837P professional claims."""

    def __init__(self, seed: int | None = None):
        """Initialize the generator with optional seed for reproducibility."""
        if seed is not None:
            random.seed(seed)
        self.generated_pcns: set[str] = set()

    def generate_patient_context(
        self,
        service_date: date | None = None,
    ) -> PatientContext:
        """Generate shared patient/subscriber/payer/provider identity.

        Used to create a reusable context so multiple claims (PCNs) belonging
        to the same HAR group share identical demographics.
        """
        payer_info = random.choice(COMMON_PAYERS)

        patient_gender = cast(Gender, generate_gender())
        patient_first, patient_last, patient_middle = generate_person_name(
            patient_gender
        )
        patient_dob = generate_birth_date(min_age=0, max_age=85)
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
            billing_provider=self._generate_billing_provider(),
            base_service_date=service_date
            or generate_service_date(days_ago_min=1, days_ago_max=90),
        )

    def generate_claim(
        self,
        ctx: PatientContext | None = None,
        service_date: date | None = None,
    ) -> ProfClaim:
        """Generate a single realistic 837P claim.

        Args:
            ctx: Shared patient context for multi-PCN HAR groups.
                 If None, a fresh context is created (single-PCN behaviour).
            service_date: Override service date. If None, uses ctx.base_service_date.
        """
        if ctx is None:
            ctx = self.generate_patient_context(service_date=service_date)

        pcn = self._generate_unique_pcn()
        svc_date = service_date or ctx.base_service_date

        # Generate service lines (1-4 lines)
        num_lines = random.choices([1, 2, 3, 4], weights=[50, 30, 15, 5])[0]
        service_lines: list[ProfLine] = []
        total_charge = 0.0

        for i in range(num_lines):
            line = self._generate_service_line(i + 1, svc_date)
            service_lines.append(line)
            total_charge += line.charge_amount

        # Select diagnoses used in service lines
        used_diag_codes: set[int] = set()
        for line in service_lines:
            if line.diag_pointers:
                used_diag_codes.update(line.diag_pointers)

        diags = self._generate_diagnoses(list(used_diag_codes))
        pos = ctx.pos

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
            subscriber=Subscriber(
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
            ),
            patient=Patient(
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
            ),
            provider_signature_indicator="Y",
            assignment_participation_code="A",
            assignment_certification_indicator="Y",
            release_of_information_code="Y",
            billing_provider=ctx.billing_provider,
            providers=[self._generate_rendering_provider()],
            diags=diags,
            service_lines=service_lines,
            transaction=self._generate_transaction(pcn),
        )

    def _generate_unique_pcn(self) -> str:
        """Generate a unique patient control number."""
        while True:
            pcn = generate_patient_control_number()
            if pcn not in self.generated_pcns:
                self.generated_pcns.add(pcn)
                return pcn

    def _generate_service_line(self, line_num: int, service_date: date) -> ProfLine:
        """Generate a single service line."""
        # Select a CPT code
        cpt_data = random.choice(BASIC_CPT_CODES)

        # Generate charge amount
        charge = random_float(cpt_data.min_cost, cpt_data.max_cost)

        # Generate unit count (usually 1)
        units = 1
        if random.random() < 0.1:  # 10% chance of multiple units
            units = random.randint(2, 5)

        # Sometimes add modifiers
        modifiers: list[Code] | None = None
        if random.random() < 0.2:  # 20% chance of modifiers
            num_modifiers = random.randint(1, 2)
            selected_modifiers = random.sample(
                BASIC_MODIFIERS, min(num_modifiers, len(BASIC_MODIFIERS))
            )
            modifiers = [
                Code(
                    sub_type="HCPCS_MODIFIER",
                    code=mod["code"],
                    desc=mod["description"],
                )
                for mod in selected_modifiers
            ]

        # Select diagnosis pointers (1-3 diagnoses per line)
        num_diags = random.randint(1, min(3, len(cpt_data.common_icd10)))
        diag_pointers = list(range(1, num_diags + 1))

        return ProfLine(
            source_line_id=f"LINE{line_num}",
            charge_amount=float(charge * units),
            service_date_from=service_date,
            unit_type="UNIT",
            unit_count=float(units),
            procedure=Procedure(
                sub_type="CPT",
                code=cpt_data.code,
                desc=cpt_data.description,
                modifiers=modifiers,
            ),
            diag_pointers=diag_pointers,
        )

    def _generate_diagnoses(self, diag_pointers: list[int]) -> list[Code]:
        """Generate diagnosis list based on pointers used."""
        diags: list[Code] = []

        # Ensure we have enough diagnoses
        num_diags_needed = max(diag_pointers) if diag_pointers else 1

        # Select random ICD-10 codes
        selected_codes = random.sample(
            BASIC_ICD10_CODES, min(num_diags_needed, len(BASIC_ICD10_CODES))
        )

        for i, icd_data in enumerate(selected_codes):
            subtype = "ICD_10_PRINCIPAL" if i == 0 else "ICD_10"
            diags.append(
                Code(sub_type=subtype, code=icd_data.code, desc=icd_data.description)
            )

        return diags

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
            provider_taxonomy=Code(
                sub_type="PROVIDER_TAXONOMY",
                code="207Q00000X",
                desc="Family Medicine Physician",
            ),
        )

    def _generate_rendering_provider(self) -> Provider:
        """Generate rendering provider information."""
        first, last, middle = generate_person_name("UNKNOWN")

        return Provider(
            entity_role="RENDERING",
            entity_type="INDIVIDUAL",
            identification_type="NPI",
            identifier=generate_npi(),
            last_name_or_org_name=last,
            first_name=first,
            middle_name=middle,
            address=generate_address(),
            provider_taxonomy=None,
        )

    def _generate_transaction(self, pcn: str) -> Transaction837:
        """Generate transaction metadata."""
        return Transaction837(
            control_number=pcn[:8],
            transaction_type="PROF",
            hierarchical_structure_code="0019",
            purpose_code="00",
            originator_application_transaction_id=pcn[:8],
            creation_date=date.today(),
            creation_time=datetime.now().time(),
            claim_or_encounter_identifier_type="CHARGEABLE",
            transaction_set_identifier_code="837",
            implementation_convention_reference="005010X222A1",
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
