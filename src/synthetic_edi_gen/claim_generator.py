"""
Generate realistic 837P professional and 837I institutional claims.
"""

# ruff: noqa: S311 # insecure random numbers are fine here
import random
import string
import uuid
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, time, timedelta
from typing import Literal, cast

from synthetic_edi_gen.edi_models import (
    Address,
    Code,
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
    BASIC_CPT_CODES,
    BASIC_HCPCS_DRUG_CODES,
    BASIC_ICD10_CODES,
    BASIC_MODIFIERS,
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
    COMMON_PAYERS,
    FIRST_NAMES,
    LAST_NAMES,
    PLACE_OF_SERVICE,
    Gender,
    Payer,
    PlaceOfService,
)

# Fraction of (non-forced) service lines that bill an administered drug, where
# the line carries a HCPCS J/Q-code procedure plus the drug's NDC information.
DRUG_LINE_PROBABILITY = 0.12

_INST_REVENUE_CODES: dict[str, tuple[str, str]] = {
    "clinic": ("0510", "Clinic"),
    "lab": ("0300", "Laboratory"),
    "radiology": ("0320", "Diagnostic radiology"),
    "operating_room": ("0360", "Operating room services"),
    "pharmacy": ("0250", "Pharmacy"),
    "supplies": ("0270", "Medical/surgical supplies"),
    "room": ("0120", "Room and board - semi-private"),
}

_INST_REVENUE_BY_SPECIALTY = {
    "Laboratory": _INST_REVENUE_CODES["lab"],
    "Radiology": _INST_REVENUE_CODES["radiology"],
    "Orthopedics": _INST_REVENUE_CODES["operating_room"],
    "General Surgery": _INST_REVENUE_CODES["operating_room"],
}

RENDERING_PROVIDER_TAXONOMIES = [
    # Primary Care
    ("207Q00000X", "Family Medicine Physician"),
    ("207QA0505X", "Adult Medicine"),
    ("207QA0000X", "Adolescent Medicine"),
    ("207QG0300X", "Geriatric Medicine"),
    ("207QS0010X", "Sports Medicine"),
    ("207R00000X", "Internal Medicine Physician"),
    ("208D00000X", "General Practice Physician"),
    ("208000000X", "Pediatrics Physician"),
    ("2080P0006X", "Developmental-Behavioral Pediatrics"),
    ("2080N0001X", "Neonatal-Perinatal Medicine"),
    # Surgery
    ("208600000X", "Surgery Physician"),
    ("2086S0120X", "Pediatric Surgery"),
    ("2086S0105X", "Surgery of the Hand"),
    ("2086S0122X", "Plastic and Reconstructive Surgery"),
    ("2086X0206X", "Surgical Oncology"),
    ("207X00000X", "Orthopaedic Surgery Physician"),
    ("207XS0114X", "Adult Reconstructive Orthopaedic Surgery"),
    ("207XS0106X", "Orthopaedic Hand Surgery"),
    ("207XX0004X", "Orthopaedic Foot and Ankle Surgery"),
    ("207XX0801X", "Orthopaedic Sports Medicine"),
    ("208C00000X", "Colon and Rectal Surgery"),
    ("204C00000X", "Neurological Surgery"),
    ("208200000X", "Plastic Surgery"),
    ("208G00000X", "Thoracic Surgery"),
    ("207T00000X", "Neurological Surgery Physician"),
    # Cardiovascular
    ("207RC0000X", "Cardiovascular Disease Physician"),
    ("207RI0011X", "Interventional Cardiology"),
    ("207RC0200X", "Critical Care Medicine (Internal Medicine)"),
    ("207RC0001X", "Clinical Cardiac Electrophysiology"),
    # Gastroenterology
    ("207RG0100X", "Gastroenterology Physician"),
    ("207RG0300X", "Hepatology Physician"),
    # Pulmonology
    ("207RP1001X", "Pulmonary Disease Physician"),
    ("207RT0003X", "Pulmonary Critical Care"),
    # Neurology & Psychiatry
    ("2084N0400X", "Neurology Physician"),
    ("2084N0402X", "Neuromuscular Medicine"),
    ("2084P0802X", "Addiction Psychiatry"),
    ("2084P0800X", "Psychiatry Physician"),
    ("2084P0804X", "Child and Adolescent Psychiatry"),
    ("2084F0202X", "Forensic Psychiatry"),
    ("2084P0805X", "Geriatric Psychiatry"),
    ("2084B0002X", "Obesity Medicine (Psychiatry)"),
    # Radiology
    ("2085R0202X", "Diagnostic Radiology Physician"),
    ("2085R0001X", "Radiation Oncology"),
    ("2085U0001X", "Diagnostic Ultrasound"),
    ("2085N0700X", "Neuroradiology"),
    ("2085R0204X", "Vascular and Interventional Radiology"),
    # OB/GYN
    ("207V00000X", "Obstetrics & Gynecology Physician"),
    ("207VX0201X", "Gynecologic Oncology"),
    ("207VG0400X", "Gynecology Physician"),
    ("207VM0101X", "Maternal and Fetal Medicine"),
    ("207VX0000X", "Obstetrics Physician"),
    ("207VE0102X", "Reproductive Endocrinology"),
    # ENT
    ("207Y00000X", "Otolaryngology Physician"),
    ("207YX0602X", "Otolaryngic Allergy"),
    ("207YS0123X", "Facial Plastic Surgery"),
    # Endocrinology & Metabolism
    ("207RE0101X", "Endocrinology Physician"),
    ("207RD0900X", "Diabetes & Metabolism"),
    # Hematology & Oncology
    ("207RH0003X", "Hematology & Oncology Physician"),
    ("207RH0000X", "Hematology Physician"),
    ("207RX0202X", "Medical Oncology"),
    # Nephrology
    ("207RN0300X", "Nephrology Physician"),
    # Rheumatology
    ("207RR0500X", "Rheumatology Physician"),
    # Infectious Disease
    ("207RI0200X", "Infectious Disease Physician"),
    # Allergy & Immunology
    ("207K00000X", "Allergy & Immunology Physician"),
    ("207KA0200X", "Allergy Physician"),
    ("207KI0005X", "Clinical & Laboratory Immunology"),
    # Dermatology
    ("207N00000X", "Dermatology Physician"),
    ("207NI0002X", "Clinical & Laboratory Dermatological Immunology"),
    ("207ND0101X", "MOHS-Micrographic Surgery"),
    ("207NP0225X", "Pediatric Dermatology"),
    ("207NS0135X", "Procedural Dermatology"),
    # Ophthalmology
    ("207W00000X", "Ophthalmology Physician"),
    ("207WX0200X", "Ophthalmic Plastic and Reconstructive Surgery"),
    ("207WX0009X", "Glaucoma Specialist"),
    ("207WX0107X", "Retina Specialist"),
    # Urology
    ("208800000X", "Urology Physician"),
    ("2088P0231X", "Pediatric Urology"),
    ("2088F0040X", "Female Pelvic Medicine and Reconstructive Surgery"),
    # Anesthesiology
    ("207L00000X", "Anesthesiology Physician"),
    ("207LA0401X", "Addiction Medicine (Anesthesiology)"),
    ("207LC0200X", "Critical Care Medicine (Anesthesiology)"),
    ("207LP2900X", "Pain Medicine"),
    # Emergency Medicine
    ("207P00000X", "Emergency Medicine Physician"),
    ("207PE0004X", "Emergency Medical Services"),
    ("207PS0010X", "Sports Medicine (Emergency Medicine)"),
    ("207PT0002X", "Medical Toxicology (Emergency Medicine)"),
    # Pathology
    ("207ZP0101X", "Anatomic Pathology"),
    ("207ZP0102X", "Anatomic Pathology & Clinical Pathology"),
    ("207ZP0104X", "Chemical Pathology"),
    ("207ZP0105X", "Clinical Pathology/Laboratory Medicine"),
    ("207ZD0900X", "Dermatopathology"),
    # Physical Medicine & Rehabilitation
    ("208100000X", "Physical Medicine & Rehabilitation"),
    ("2081P2900X", "Pain Medicine (PM&R)"),
    ("2081P0010X", "Pediatric Rehabilitation Medicine"),
    ("2081S0010X", "Sports Medicine (PM&R)"),
    # Preventive Medicine
    ("2083P0500X", "Preventive Medicine/Occupational-Environmental Medicine"),
    ("2083P0901X", "Public Health & General Preventive Medicine"),
    ("2083X0100X", "Occupational Medicine"),
    # Nuclear Medicine
    ("207U00000X", "Nuclear Medicine Physician"),
    ("207UN0903X", "In Vivo & In Vitro Nuclear Medicine"),
    ("207UN0901X", "Nuclear Cardiology"),
    # Non-Physician Clinicians
    ("363L00000X", "Nurse Practitioner"),
    ("363LA2200X", "Adult Health Nurse Practitioner"),
    ("363LF0000X", "Family Nurse Practitioner"),
    ("363LP0200X", "Pediatric Nurse Practitioner"),
    ("363LP0808X", "Psych/Mental Health Nurse Practitioner"),
    ("363LW0102X", "Women's Health Nurse Practitioner"),
    ("363LC1500X", "Community Health Nurse Practitioner"),
    ("363A00000X", "Physician Assistant"),
    ("363AM0700X", "Medical Physician Assistant"),
    ("363AS0400X", "Surgical Physician Assistant"),
    ("364S00000X", "Clinical Nurse Specialist"),
    ("367A00000X", "Advanced Practice Midwife"),
    ("367500000X", "Certified Registered Nurse Anesthetist"),
    # Therapy & Rehab
    ("225100000X", "Physical Therapist"),
    ("225200000X", "Physical Therapy Assistant"),
    ("225500000X", "Respiratory Therapist"),
    ("225600000X", "Dance Therapist"),
    ("221700000X", "Art Therapist"),
    ("225X00000X", "Occupational Therapist"),
    # Behavioral Health
    ("101Y00000X", "Counselor"),
    ("101YA0400X", "Addiction Counselor"),
    ("101YM0800X", "Mental Health Counselor"),
    ("101YP2500X", "Professional Counselor"),
    ("102L00000X", "Psychoanalyst"),
    ("103T00000X", "Psychologist"),
    ("103TA0400X", "Addiction Psychologist"),
    ("103TC0700X", "Clinical Psychologist"),
    ("104100000X", "Social Worker"),
    ("1041C0700X", "Clinical Social Worker"),
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
    pos: PlaceOfService = field(default_factory=lambda: random.choice(PLACE_OF_SERVICE))


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
            institutional_billing_provider=self._generate_institutional_billing_provider(),
            rendering_provider=self._generate_rendering_provider(),
            base_service_date=service_date
            or generate_service_date(days_ago_min=1, days_ago_max=90),
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
            num_lines = random.choices([1, 2, 3, 4], weights=[50, 30, 15, 5])[0]

        service_lines: list[ProfLine] = []
        total_charge = 0.0

        for i in range(num_lines):
            forced_code = forced_cpt_codes[i] if forced_cpt_codes else None
            line = self._generate_service_line(i + 1, svc_date, forced_cpt=forced_code)
            service_lines.append(line)
            total_charge += line.charge_amount

        # Build diagnoses, then clamp service-line pointers so they never
        # exceed the actual number of diagnosis codes on the claim.
        used_diag_codes: set[int] = set()
        for line in service_lines:
            if line.diag_pointers:
                used_diag_codes.update(line.diag_pointers)

        if forced_icd10_codes:
            diags = self._build_forced_diagnoses(forced_icd10_codes)
        else:
            diags = self._generate_diagnoses(list(used_diag_codes))

        # Clamp pointers to valid range [1 .. len(diags)]
        max_ptr = len(diags)
        for line in service_lines:
            if line.diag_pointers:
                line.diag_pointers = sorted(
                    {min(p, max_ptr) for p in line.diag_pointers}
                )
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
            subscriber=self._generate_subscriber(ctx),
            patient=self._generate_patient(ctx),
            provider_signature_indicator="Y",
            assignment_participation_code="A",
            assignment_certification_indicator="Y",
            release_of_information_code="Y",
            medical_record_number=ctx.mrn,
            billing_provider=ctx.billing_provider,
            providers=[ctx.rendering_provider],
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
        stay_days = random.randint(1, 5) if is_inpatient and not forced_cpt_codes else 0
        statement_to = svc_date + timedelta(days=stay_days)

        service_lines = self._generate_institutional_service_lines(
            svc_date,
            stay_days + 1,
            forced_cpt_codes=forced_cpt_codes,
        )
        total_charge = sum(line.charge_amount for line in service_lines)

        if forced_icd10_codes:
            diags = self._build_forced_inst_diagnoses(
                forced_icd10_codes,
                include_poa=is_inpatient,
            )
        else:
            diags = self._generate_inst_diagnoses(include_poa=is_inpatient)

        facility_code, patient_status = self._institutional_claim_codes(is_inpatient)
        admission_dt = None
        discharge_dt = None
        if is_inpatient:
            admission_dt = self._aware_datetime(svc_date, random.randint(0, 23))
            if patient_status != "30":
                discharge_dt = self._aware_datetime(statement_to, random.randint(8, 18))

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
            admission_type_code=random.choice(["1", "2", "3"])
            if is_inpatient
            else None,
            admission_source_code=random.choice(["1", "2", "7"])
            if is_inpatient
            else None,
            patient_status_code=patient_status,
            billing_provider=ctx.institutional_billing_provider or ctx.billing_provider,
            providers=[
                ctx.rendering_provider.model_copy(update={"entity_role": "ATTENDING"})
            ],
            diags=diags,
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

    def _generate_unique_pcn(self) -> str:
        """Generate a unique patient control number."""
        while True:
            pcn = generate_patient_control_number()
            if pcn not in self.generated_pcns:
                self.generated_pcns.add(pcn)
                return pcn

    def _generate_service_line(
        self,
        line_num: int,
        service_date: date,
        forced_cpt: str | None = None,
    ) -> ProfLine:
        """Generate a single service line.

        Most lines bill a CPT procedure, but a minority bill a clinician-
        administered drug: a HCPCS J/Q-code procedure carrying the drug's NDC,
        quantity, and unit of measure (see ``_generate_drug_service_line``).
        """
        drug_data = self._select_drug_for_line(forced_cpt)
        if drug_data is not None:
            line = self._generate_drug_service_line(line_num, service_date, drug_data)
            if self._drug_defect_rate > 0 and random.random() < self._drug_defect_rate:
                self._apply_drug_defect(line)
            return line

        # Select a CPT code
        if forced_cpt:
            matches = [c for c in BASIC_CPT_CODES if c.code == forced_cpt]
            cpt_data = matches[0] if matches else random.choice(BASIC_CPT_CODES)
        else:
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

    def _generate_institutional_service_lines(
        self,
        service_date: date,
        stay_days: int,
        forced_cpt_codes: list[str] | None = None,
    ) -> list[InstLine]:
        if forced_cpt_codes:
            return [
                self._generate_institutional_service_line(i + 1, service_date, code)
                for i, code in enumerate(forced_cpt_codes)
            ]

        lines: list[InstLine] = []
        if stay_days > 1:
            lines.append(self._generate_room_and_board_line(1, service_date, stay_days))

        num_ancillary = random.choices([1, 2, 3, 4], weights=[35, 35, 20, 10])[0]
        for _ in range(num_ancillary):
            lines.append(
                self._generate_institutional_service_line(
                    len(lines) + 1,
                    service_date + timedelta(days=random.randint(0, stay_days - 1)),
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
        charge = random_float(900.0, 2500.0) * stay_days
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

    @staticmethod
    def _generate_institutional_service_line(
        line_num: int,
        service_date: date,
        forced_cpt: str | None = None,
    ) -> InstLine:
        if forced_cpt:
            matches = [c for c in BASIC_CPT_CODES if c.code == forced_cpt]
            cpt_data = matches[0] if matches else random.choice(BASIC_CPT_CODES)
        else:
            cpt_data = random.choice(BASIC_CPT_CODES)

        rev_code, rev_desc = _INST_REVENUE_BY_SPECIALTY.get(
            cpt_data.specialty,
            _INST_REVENUE_CODES["clinic"],
        )
        units = random.randint(2, 5) if random.random() < 0.08 else 1
        charge = (
            random_float(cpt_data.min_cost, cpt_data.max_cost)
            * random_float(1.4, 3.5)
            * units
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
                code=rev_code,
                desc=rev_desc,
            ),
            procedure=Procedure(
                sub_type="CPT",
                code=cpt_data.code,
                desc=cpt_data.description,
            ),
        )

    @staticmethod
    def _select_drug_for_line(forced_cpt: str | None) -> BasicHCPCSDrugCode | None:
        """Decide whether a line bills an administered drug, and which one.

        When a procedure code is forced, a drug line is produced only if that
        code is a known HCPCS drug code (so callers can request drug lines
        explicitly). Otherwise a drug line is chosen at random a fraction of
        the time.
        """
        if forced_cpt is not None:
            return next(
                (d for d in BASIC_HCPCS_DRUG_CODES if d.hcpcs_code == forced_cpt),
                None,
            )
        if random.random() < DRUG_LINE_PROBABILITY:
            return random.choice(BASIC_HCPCS_DRUG_CODES)
        return None

    @staticmethod
    def _generate_drug_service_line(
        line_num: int,
        service_date: date,
        drug_data: BasicHCPCSDrugCode,
    ) -> ProfLine:
        """Generate a service line that bills a clinician-administered drug.

        The procedure is the drug's HCPCS J-code and the line additionally
        reports the National Drug Code (NDC). The NDC quantity is derived from
        the billed unit count and the J-code's per-unit dosing so the reported
        drug quantity stays consistent with the procedure and units.
        """
        units = random.randint(1, drug_data.max_units) if drug_data.max_units > 1 else 1

        charge = random_float(drug_data.min_cost, drug_data.max_cost) * units
        drug_quantity = round(units * drug_data.ndc_qty_per_unit, 3)

        num_diags = random.randint(1, min(3, len(drug_data.common_icd10)))
        diag_pointers = list(range(1, num_diags + 1))

        return ProfLine(
            source_line_id=f"LINE{line_num}",
            charge_amount=float(round(charge, 2)),
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
            diag_pointers=diag_pointers,
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

    def _build_forced_diagnoses(self, icd10_codes: list[str]) -> list[Code]:
        """Build diagnosis list from specific ICD-10 codes."""
        by_code = {c.formatted_code: c for c in BASIC_ICD10_CODES}
        diags: list[Code] = []
        for i, code in enumerate(icd10_codes):
            icd_data = by_code.get(code)
            subtype = "ICD_10_PRINCIPAL" if i == 0 else "ICD_10"
            if icd_data:
                diags.append(
                    Code(
                        sub_type=subtype,
                        code=icd_data.code,
                        desc=icd_data.description,
                    )
                )
            else:
                diags.append(Code(sub_type=subtype, code=code, desc=code))
        return diags

    def _generate_inst_diagnoses(self, include_poa: bool) -> list[InstDiagnosis]:
        num_diags = random.randint(1, 4)
        selected_codes = random.sample(
            BASIC_ICD10_CODES,
            min(num_diags, len(BASIC_ICD10_CODES)),
        )
        return [
            InstDiagnosis(
                sub_type="ICD_10_PRINCIPAL" if i == 0 else "ICD_10",
                code=icd_data.code,
                desc=icd_data.description,
                present_on_admission_indicator=self._poa_indicator(i, include_poa),
            )
            for i, icd_data in enumerate(selected_codes)
        ]

    def _build_forced_inst_diagnoses(
        self,
        icd10_codes: list[str],
        include_poa: bool,
    ) -> list[InstDiagnosis]:
        by_code = {c.formatted_code: c for c in BASIC_ICD10_CODES}
        diags: list[InstDiagnosis] = []
        for i, code in enumerate(icd10_codes):
            icd_data = by_code.get(code)
            diags.append(
                InstDiagnosis(
                    sub_type="ICD_10_PRINCIPAL" if i == 0 else "ICD_10",
                    code=icd_data.code if icd_data else code,
                    desc=icd_data.description if icd_data else code,
                    present_on_admission_indicator=self._poa_indicator(i, include_poa),
                )
            )
        return diags

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

    @staticmethod
    def _generate_rendering_provider() -> Provider:
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
        tax_code, tax_desc = rng.choice(RENDERING_PROVIDER_TAXONOMIES)

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
        if forced_cpt_codes and set(forced_cpt_codes) & {
            "27447",
            "47562",
            "29881",
            "49505",
        }:
            return True
        return random.random() < 0.35

    @staticmethod
    def _institutional_claim_codes(is_inpatient: bool) -> tuple[Code, str]:
        facility_options = (
            [("11", "Hospital inpatient"), ("21", "Skilled nursing inpatient")]
            if is_inpatient
            else [("13", "Hospital outpatient"), ("32", "Home health")]
        )
        facility_code, facility_desc = random.choice(facility_options)
        patient_status = (
            random.choices(
                ["01", "02", "03", "06", "30"],
                weights=[70, 8, 8, 7, 7],
                k=1,
            )[0]
            if is_inpatient
            else "01"
        )
        return (
            Code(
                sub_type="UB_FACILITY_TYPE",
                code=facility_code,
                desc=facility_desc,
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
