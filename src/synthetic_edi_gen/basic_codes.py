"""
Basic medical codes to start with while research completes.
These are well-known common codes.
"""

from typing import Literal

from pydantic import BaseModel


class BasicCPTCode(BaseModel):
    code: str
    description: str
    min_cost: float
    max_cost: float
    specialty: str
    common_icd10: list[str]


# Common CPT codes (will be enhanced by research)
BASIC_CPT_CODES = [
    BasicCPTCode(
        code="99213",
        description="Office visit, established patient, level 3",
        min_cost=75.00,
        max_cost=150.00,
        specialty="Primary Care",
        common_icd10=["Z00.00", "J06.9", "I10", "E11.9"],
    ),
    BasicCPTCode(
        code="99214",
        description="Office visit, established patient, level 4",
        min_cost=110.00,
        max_cost=200.00,
        specialty="Primary Care",
        common_icd10=["E11.9", "I10", "J44.9", "M79.3"],
    ),
    BasicCPTCode(
        code="99203",
        description="Office visit, new patient, level 3",
        min_cost=110.00,
        max_cost=180.00,
        specialty="Primary Care",
        common_icd10=["Z00.00", "I10", "E78.5"],
    ),
    BasicCPTCode(
        code="99204",
        description="Office visit, new patient, level 4",
        min_cost=165.00,
        max_cost=280.00,
        specialty="Primary Care",
        common_icd10=["E11.9", "I10", "J44.9"],
    ),
    # Lab Tests
    BasicCPTCode(
        code="80053",
        description="Comprehensive metabolic panel",
        min_cost=20.00,
        max_cost=50.00,
        specialty="Laboratory",
        common_icd10=["E11.9", "I10", "N18.3"],
    ),
    BasicCPTCode(
        code="85025",
        description="Complete blood count with differential",
        min_cost=15.00,
        max_cost=35.00,
        specialty="Laboratory",
        common_icd10=["D64.9", "R50.9", "Z00.00"],
    ),
    BasicCPTCode(
        code="83036",
        description="Hemoglobin A1C",
        min_cost=15.00,
        max_cost=40.00,
        specialty="Laboratory",
        common_icd10=["E11.9", "E10.9"],
    ),
    # Imaging
    BasicCPTCode(
        code="71046",
        description="Chest X-ray, 2 views",
        min_cost=50.00,
        max_cost=150.00,
        specialty="Radiology",
        common_icd10=["J18.9", "R05", "J44.9"],
    ),
    BasicCPTCode(
        code="73610",
        description="X-ray ankle, complete",
        min_cost=60.00,
        max_cost=180.00,
        specialty="Radiology",
        common_icd10=["S93.40XA", "M25.571"],
    ),
    # Surgical procedures
    BasicCPTCode(
        code="27447",
        description="Total knee arthroplasty",
        min_cost=1800.00,
        max_cost=3500.00,
        specialty="Orthopedics",
        common_icd10=["M17.11", "M17.12"],
    ),
    BasicCPTCode(
        code="47562",
        description="Laparoscopic cholecystectomy",
        min_cost=1200.00,
        max_cost=2800.00,
        specialty="General Surgery",
        common_icd10=["K80.20", "K81.0"],
    ),
    BasicCPTCode(
        code="29881",
        description="Arthroscopy, knee, surgical; with meniscectomy",
        min_cost=900.00,
        max_cost=2200.00,
        specialty="Orthopedics",
        common_icd10=["M23.21", "S83.20XA"],
    ),
    BasicCPTCode(
        code="49505",
        description="Repair initial inguinal hernia, age 5 or older",
        min_cost=800.00,
        max_cost=1800.00,
        specialty="General Surgery",
        common_icd10=["K40.90", "K40.91"],
    ),
    # Pre-operative evaluation
    BasicCPTCode(
        code="99205",
        description="Office visit, new patient, level 5 (pre-op evaluation)",
        min_cost=200.00,
        max_cost=350.00,
        specialty="Primary Care",
        common_icd10=["Z01.818", "M17.11", "K80.20"],
    ),
]


class BasicHCPCSDrugCode(BaseModel):
    """A HCPCS drug-administration code paired with the NDC of a representative
    product.

    When an 837P service line bills for an administered drug, the procedure is
    a HCPCS Level II code — typically a J-code or Q-code — (SV101) and the
    drug is additionally identified by its National Drug Code (LIN03) with a
    quantity (CTP04) and unit of measure (CTP05-1). The NDC must identify the
    actual product the HCPCS code administers, so these fields are kept
    together to guarantee that correlation.
    """

    hcpcs_code: str
    """HCPCS Level II procedure code billed on the line (SV101),
    e.g. a J-code or Q-code."""
    description: str
    """Long description of the HCPCS code, including its billing dosage."""
    ndc: str
    """National Drug Code of a representative product, as the 11-digit (5-4-2)
    string sent in LIN03 under the N4 qualifier (no hyphens)."""
    drug_name: str
    """Human-readable product name/strength for the NDC."""
    ndc_unit: Literal["F2", "GR", "ME", "ML", "UN"]
    """NDC unit of measure (CTP05-1): International Unit, Gram, Milligram,
    Milliliter, or Unit."""
    ndc_qty_per_unit: float
    """National drug units (in ``ndc_unit``) dispensed per one billed HCPCS
    unit. The line's CTP04 quantity is this value times the SV104 unit count,
    keeping the reported NDC quantity consistent with the procedure."""
    min_cost: float
    """Minimum charge per billed HCPCS unit."""
    max_cost: float
    """Maximum charge per billed HCPCS unit."""
    max_units: int
    """Maximum number of HCPCS units plausibly administered in one encounter."""
    common_icd10: list[str]
    """Diagnoses commonly treated with this drug."""


# Common HCPCS drug codes (J-codes and Q-codes) for clinician-administered
# drugs, each paired with the NDC of a representative product. The per-unit
# dosing of the HCPCS code and the strength of the NDC product determine
# ``ndc_qty_per_unit`` so the reported drug quantity always reconciles with
# the billed units.
BASIC_HCPCS_DRUG_CODES = [
    BasicHCPCSDrugCode(
        hcpcs_code="J1885",
        description="Injection, ketorolac tromethamine, per 15 mg",
        ndc="00409379601",
        drug_name="Ketorolac tromethamine 30 mg/mL injection",
        ndc_unit="ML",
        ndc_qty_per_unit=0.5,  # 15 mg / (30 mg/mL)
        min_cost=1.50,
        max_cost=4.00,
        max_units=4,
        common_icd10=["M54.9", "M25.561", "M79.3"],
    ),
    BasicHCPCSDrugCode(
        hcpcs_code="J3301",
        description="Injection, triamcinolone acetonide, not otherwise "
        "specified, per 10 mg",
        ndc="00003029320",
        drug_name="Kenalog-40 (triamcinolone acetonide) 40 mg/mL injection",
        ndc_unit="ML",
        ndc_qty_per_unit=0.25,  # 10 mg / (40 mg/mL)
        min_cost=2.00,
        max_cost=6.00,
        max_units=4,
        common_icd10=["M17.11", "M54.9", "M25.561"],
    ),
    BasicHCPCSDrugCode(
        hcpcs_code="J1040",
        description="Injection, methylprednisolone acetate, 80 mg",
        ndc="00009030602",
        drug_name="Depo-Medrol (methylprednisolone acetate) 80 mg/mL injection",
        ndc_unit="ML",
        ndc_qty_per_unit=1.0,  # 80 mg / (80 mg/mL)
        min_cost=8.00,
        max_cost=18.00,
        max_units=2,
        common_icd10=["M54.9", "M17.11", "J45.909"],
    ),
    BasicHCPCSDrugCode(
        hcpcs_code="J1100",
        description="Injection, dexamethasone sodium phosphate, 1 mg",
        ndc="00641614525",
        drug_name="Dexamethasone sodium phosphate 4 mg/mL injection",
        ndc_unit="ML",
        ndc_qty_per_unit=0.25,  # 1 mg / (4 mg/mL)
        min_cost=0.50,
        max_cost=2.00,
        max_units=16,
        common_icd10=["J45.909", "M54.9", "R05"],
    ),
    BasicHCPCSDrugCode(
        hcpcs_code="J2550",
        description="Injection, promethazine HCl, up to 50 mg",
        ndc="00641608825",
        drug_name="Promethazine HCl 25 mg/mL injection",
        ndc_unit="ML",
        ndc_qty_per_unit=2.0,  # 50 mg / (25 mg/mL)
        min_cost=1.00,
        max_cost=3.00,
        max_units=1,
        common_icd10=["R11.2", "R11.10", "G43.909"],
    ),
    BasicHCPCSDrugCode(
        hcpcs_code="J1200",
        description="Injection, diphenhydramine HCl, up to 50 mg",
        ndc="00409208501",
        drug_name="Diphenhydramine HCl 50 mg/mL injection",
        ndc_unit="ML",
        ndc_qty_per_unit=1.0,  # 50 mg / (50 mg/mL)
        min_cost=0.50,
        max_cost=2.00,
        max_units=1,
        common_icd10=["T78.40XA", "L50.9", "J30.9"],
    ),
    BasicHCPCSDrugCode(
        hcpcs_code="J0171",
        description="Injection, adrenalin, epinephrine, 0.1 mg",
        ndc="42023015901",
        drug_name="Adrenalin (epinephrine) 1 mg/mL injection",
        ndc_unit="ML",
        ndc_qty_per_unit=0.1,  # 0.1 mg / (1 mg/mL)
        min_cost=0.50,
        max_cost=2.00,
        max_units=5,
        common_icd10=["T78.2XXA", "T78.40XA", "J45.901"],
    ),
    BasicHCPCSDrugCode(
        hcpcs_code="J3420",
        description="Injection, vitamin B-12 cyanocobalamin, up to 1000 mcg",
        ndc="00517023125",
        drug_name="Cyanocobalamin 1000 mcg/mL injection",
        ndc_unit="ML",
        ndc_qty_per_unit=1.0,  # 1000 mcg / (1000 mcg/mL)
        min_cost=0.50,
        max_cost=2.00,
        max_units=1,
        common_icd10=["D51.0", "E53.8", "D51.9"],
    ),
    BasicHCPCSDrugCode(
        hcpcs_code="J0696",
        description="Injection, ceftriaxone sodium, per 250 mg",
        ndc="00409733701",
        drug_name="Ceftriaxone sodium 1 g single-dose vial",
        ndc_unit="GR",
        ndc_qty_per_unit=0.25,  # 250 mg = 0.25 g
        min_cost=1.00,
        max_cost=4.00,
        max_units=8,
        common_icd10=["J06.9", "N39.0", "J18.9"],
    ),
    BasicHCPCSDrugCode(
        hcpcs_code="J0585",
        description="Injection, onabotulinumtoxinA, 1 unit",
        ndc="00023114501",
        drug_name="Botox (onabotulinumtoxinA) 100 unit single-dose vial",
        ndc_unit="UN",
        ndc_qty_per_unit=1.0,  # 1 billed unit = 1 NDC unit
        min_cost=5.00,
        max_cost=7.00,
        max_units=100,
        common_icd10=["G24.5", "G43.909", "G24.3"],
    ),
    BasicHCPCSDrugCode(
        hcpcs_code="J2270",
        description="Injection, morphine sulfate, up to 10 mg",
        ndc="00409174901",
        drug_name="Morphine sulfate 10 mg/mL injection",
        ndc_unit="ML",
        ndc_qty_per_unit=1.0,  # 10 mg / (10 mg/mL)
        min_cost=0.50,
        max_cost=3.00,
        max_units=4,
        common_icd10=["M54.9", "G89.29", "R10.9"],
    ),
    # HCPCS Q-codes — temporary codes for drugs/biologicals (biosimilars,
    # ESRD drugs, and other drugs awaiting permanent J-code assignment).
    BasicHCPCSDrugCode(
        hcpcs_code="Q2050",
        description="Injection, darbepoetin alfa, 1 mcg (for ESRD on dialysis)",
        ndc="55513011001",
        drug_name="Aranesp (darbepoetin alfa) 25 mcg/mL injection",
        ndc_unit="UN",
        ndc_qty_per_unit=1.0,  # 1 mcg billed = 1 NDC unit
        min_cost=3.00,
        max_cost=8.00,
        max_units=200,
        common_icd10=["D63.1", "N18.6", "D64.9"],
    ),
    BasicHCPCSDrugCode(
        hcpcs_code="Q5103",
        description="Injection, infliximab-dyyb, biosimilar, 10 mg",
        ndc="00069042101",
        drug_name="Inflectra (infliximab-dyyb) 100 mg single-dose vial",
        ndc_unit="ME",
        ndc_qty_per_unit=10.0,  # 10 mg per billed unit
        min_cost=15.00,
        max_cost=30.00,
        max_units=40,
        common_icd10=["M06.9", "K50.90", "K51.90"],
    ),
    BasicHCPCSDrugCode(
        hcpcs_code="Q5104",
        description="Injection, infliximab-abda, biosimilar, 10 mg",
        ndc="78206001101",
        drug_name="Renflexis (infliximab-abda) 100 mg single-dose vial",
        ndc_unit="ME",
        ndc_qty_per_unit=10.0,  # 10 mg per billed unit
        min_cost=14.00,
        max_cost=28.00,
        max_units=40,
        common_icd10=["M06.9", "K50.90", "L40.50"],
    ),
    BasicHCPCSDrugCode(
        hcpcs_code="Q5106",
        description="Injection, bevacizumab-awwb, biosimilar, 10 mg",
        ndc="55513099601",
        drug_name="Mvasi (bevacizumab-awwb) 25 mg/mL injection",
        ndc_unit="ML",
        ndc_qty_per_unit=0.4,  # 10 mg / (25 mg/mL)
        min_cost=20.00,
        max_cost=45.00,
        max_units=100,
        common_icd10=["C34.90", "C18.9", "C56.9"],
    ),
    BasicHCPCSDrugCode(
        hcpcs_code="Q5112",
        description="Injection, trastuzumab-dttb, biosimilar, 10 mg",
        ndc="00781345801",
        drug_name="Ontruzant (trastuzumab-dttb) 150 mg single-dose vial",
        ndc_unit="ME",
        ndc_qty_per_unit=10.0,  # 10 mg per billed unit
        min_cost=18.00,
        max_cost=35.00,
        max_units=44,
        common_icd10=["C50.911", "C50.919", "C16.9"],
    ),
]


class BasicICD10Code(BaseModel):
    code: str
    formatted_code: str
    description: str
    category: str
    common_cpt: list[str]


# Common ICD-10 codes (will be enhanced by research)
BASIC_ICD10_CODES = [
    BasicICD10Code(
        code="Z0000",
        formatted_code="Z00.00",
        description="Encounter for general adult medical examination without abnormal"
        " findings",
        category="Preventive",
        common_cpt=["99213", "99214", "99203"],
    ),
    BasicICD10Code(
        code="I10",
        formatted_code="I10",
        description="Essential (primary) hypertension",
        category="Cardiovascular",
        common_cpt=["99213", "99214"],
    ),
    BasicICD10Code(
        code="E119",
        formatted_code="E11.9",
        description="Type 2 diabetes mellitus without complications",
        category="Endocrine",
        common_cpt=["99213", "99214", "83036"],
    ),
    BasicICD10Code(
        code="J069",
        formatted_code="J06.9",
        description="Acute upper respiratory infection, unspecified",
        category="Respiratory",
        common_cpt=["99213", "99203"],
    ),
    BasicICD10Code(
        code="J449",
        formatted_code="J44.9",
        description="Chronic obstructive pulmonary disease, unspecified",
        category="Respiratory",
        common_cpt=["99214", "71046"],
    ),
    BasicICD10Code(
        code="E785",
        formatted_code="E78.5",
        description="Hyperlipidemia, unspecified",
        category="Endocrine",
        common_cpt=["99213", "80053"],
    ),
    BasicICD10Code(
        code="M549",
        formatted_code="M54.9",
        description="Dorsalgia, unspecified",
        category="Musculoskeletal",
        common_cpt=["99213", "99214"],
    ),
    BasicICD10Code(
        code="R50.9",
        formatted_code="R50.9",
        description="Fever, unspecified",
        category="Symptoms",
        common_cpt=["99213", "85025"],
    ),
    # Surgical diagnoses
    BasicICD10Code(
        code="M1711",
        formatted_code="M17.11",
        description="Primary osteoarthritis, right knee",
        category="Musculoskeletal",
        common_cpt=["27447", "99205", "99214"],
    ),
    BasicICD10Code(
        code="K8020",
        formatted_code="K80.20",
        description="Calculus of gallbladder without obstruction",
        category="Digestive",
        common_cpt=["47562", "99205", "99214"],
    ),
    BasicICD10Code(
        code="M2321",
        formatted_code="M23.21",
        description="Derangement of anterior horn of medial meniscus, right knee",
        category="Musculoskeletal",
        common_cpt=["29881", "99205", "99214"],
    ),
    BasicICD10Code(
        code="K4090",
        formatted_code="K40.90",
        description="Unilateral inguinal hernia without obstruction or gangrene",
        category="Digestive",
        common_cpt=["49505", "99205", "99214"],
    ),
]


class BasicCARCCode(BaseModel):
    code: str
    description: str
    group: Literal[
        "CONTRACTUAL",
        "CORRECTION",
        "OTHER",
        "PATIENT_RESPONSIBILITY",
        "PAYOR_INITIATED",
    ]
    typical_percentage: float | None
    typical_amount_range: tuple[float, float] | None


# Common CARC codes
BASIC_CARC_CODES = [
    BasicCARCCode(
        code="1",
        description="Deductible Amount",
        group="PATIENT_RESPONSIBILITY",
        typical_percentage=None,
        typical_amount_range=(50.0, 500.0),
    ),
    BasicCARCCode(
        code="2",
        description="Coinsurance Amount",
        group="PATIENT_RESPONSIBILITY",
        typical_percentage=0.20,  # 20% coinsurance
        typical_amount_range=None,
    ),
    BasicCARCCode(
        code="3",
        description="Co-payment Amount",
        group="PATIENT_RESPONSIBILITY",
        typical_percentage=None,
        typical_amount_range=(15.0, 50.0),
    ),
    BasicCARCCode(
        code="45",
        description="Charge exceeds fee schedule/maximum allowable or "
        "contracted/legislated fee arrangement",
        group="CONTRACTUAL",
        typical_percentage=0.30,
        typical_amount_range=None,
    ),
    BasicCARCCode(
        code="16",
        description="Claim/service lacks information or has "
        "submission/billing error(s)",
        group="OTHER",
        typical_percentage=None,
        typical_amount_range=None,  # Usually full denial
    ),
    BasicCARCCode(
        code="50",
        description="These are non-covered services because this is not deemed "
        "a 'medical necessity' by the payer",
        group="CONTRACTUAL",
        typical_percentage=None,
        typical_amount_range=None,  # Usually full denial
    ),
    BasicCARCCode(
        code="97",
        description="The benefit for this service is included in the "
        "payment/allowance for another service/procedure that has "
        "already been adjudicated",
        group="CONTRACTUAL",
        typical_percentage=None,
        typical_amount_range=None,  # Usually full denial
    ),
    BasicCARCCode(
        code="22",
        description="This care may be covered by another payer "
        "per coordination of benefits",
        group="OTHER",
        typical_percentage=None,
        typical_amount_range=None,
    ),
    BasicCARCCode(
        code="29",
        description="The time limit for filing has expired",
        group="CONTRACTUAL",
        typical_percentage=None,
        typical_amount_range=None,  # Usually full denial
    ),
    BasicCARCCode(
        code="96",
        description="Non-covered charge(s)",
        group="CONTRACTUAL",
        typical_percentage=None,
        typical_amount_range=None,  # Usually full denial
    ),
]


class BasicRARCCode(BaseModel):
    code: str
    description: str


# Common RARC codes
BASIC_RARC_CODES = [
    BasicRARCCode(
        code="N130",
        description=(
            "Consult plan benefit documents/guidelines for information "
            "about restrictions for this service."
        ),
    ),
    BasicRARCCode(
        code="M80",
        description=(
            "Not covered when performed during the same session/date "
            "as a previously processed service for the patient."
        ),
    ),
    BasicRARCCode(
        code="N19", description="Procedure code incidental to primary procedure."
    ),
    BasicRARCCode(
        code="M15",
        description=(
            "Separately billed services/tests have been bundled as they "
            "are considered components of the same procedure. Separate "
            "payment is not allowed."
        ),
    ),
]

# Denial-specific RARC codes (used when 835 denies a line/claim)
DENIAL_RARC_CODES = [
    BasicRARCCode(
        code="N362",
        description="Missing/incomplete/invalid diagnosis.",
    ),
    BasicRARCCode(
        code="N17",
        description="Procedure code doesn't match provider type.",
    ),
    BasicRARCCode(
        code="N20",
        description="Service was not authorized for this patient.",
    ),
    BasicRARCCode(
        code="N428",
        description="Alert: Refer to the 835 for more detail.",
    ),
]

# CARC codes that indicate denial (zero payment)
DENIAL_CARC_CODES = {"16", "29", "50", "96", "97"}

# Common HCPCS modifiers
BASIC_MODIFIERS = [
    {
        "code": "25",
        "description": (
            "Significant, separately identifiable E/M service by the "
            "same physician on same day of procedure"
        ),
    },
    {"code": "59", "description": "Distinct procedural service"},
    {"code": "76", "description": "Repeat procedure by same physician"},
    {"code": "77", "description": "Repeat procedure by another physician"},
    {"code": "50", "description": "Bilateral procedure"},
    {"code": "LT", "description": "Left side"},
    {"code": "RT", "description": "Right side"},
]
