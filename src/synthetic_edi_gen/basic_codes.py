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
