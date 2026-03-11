"""
Helper functions for generating fake but realistic EDI data.
"""

# ruff: noqa: S311
import random
import string
from datetime import date, timedelta

from synthetic_edi_gen.edi_models import Address

from .reference_data import CITIES_STATES, FIRST_NAMES, LAST_NAMES, Gender


def generate_patient_control_number() -> str:
    """Generate a unique patient control number."""
    return "".join(random.choices(string.digits, k=10))


def generate_npi() -> str:
    """Generate a fake NPI (National Provider Identifier)."""
    return "".join(random.choices(string.digits, k=10))


def generate_tax_id() -> str:
    """Generate a fake tax ID."""
    return "".join(random.choices(string.digits, k=9))


def generate_member_id() -> str:
    """Generate a fake member ID."""
    prefix = "".join(random.choices(string.ascii_uppercase, k=2))
    numbers = "".join(random.choices(string.digits, k=9))
    return f"{prefix}{numbers}"


def generate_person_name(gender: Gender) -> tuple[str, str, str]:
    """Generate a fake person name (first, last, middle initial)."""
    first = random.choice(FIRST_NAMES[gender])
    last = random.choice(LAST_NAMES)
    middle = random.choice(string.ascii_uppercase)
    return first, last, middle


def generate_birth_date(min_age: int = 18, max_age: int = 85) -> date:
    """Generate a realistic birth date."""
    today = date.today()
    years_ago = random.randint(min_age, max_age)
    days_offset = random.randint(0, 365)
    birth_year = today.year - years_ago
    birth_date = date(birth_year, 1, 1) + timedelta(days=days_offset)
    return birth_date


def generate_address() -> Address:
    """Generate a fake address."""
    city_state = random.choice(CITIES_STATES)
    street_number = random.randint(100, 9999)
    street_names = [
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
    street = f"{street_number} {random.choice(street_names)}"

    # Sometimes add apartment/suite
    line2 = None
    if random.random() < 0.3:
        if random.random() < 0.5:
            line2 = f"APT {random.randint(1, 999)}"
        else:
            line2 = f"SUITE {random.randint(100, 999)}"

    # Add 4-digit extension to zip sometimes
    zip_code = city_state.zip
    if random.random() < 0.7:
        zip_code += str(random.randint(1000, 9999))

    return Address(
        line=street,
        line2=line2,
        city=city_state.city,
        state_code=city_state.state,
        zip_code=zip_code,
    )


def generate_service_date(days_ago_min: int = 1, days_ago_max: int = 90) -> date:
    """Generate a recent service date."""
    days_ago = random.randint(days_ago_min, days_ago_max)
    return date.today() - timedelta(days=days_ago)


def generate_transaction_id() -> str:
    """Generate a transaction ID."""
    return "".join(random.choices(string.digits + string.ascii_uppercase, k=24))


def random_float(min_val: float, max_val: float, precision: int = 2) -> float:
    """Generate a random float between min and max."""
    return round(random.uniform(min_val, max_val), precision)


def calculate_contracted_amount(
    charge_amount: float, discount_pct: float = 0.30
) -> float:
    """Calculate contracted/allowed amount (typically 60-80% of charge)."""
    discount = random.uniform(0.20, discount_pct)
    return round(charge_amount * (1 - discount), 2)


def apply_adjustment(amount: float, adjustment_type: str) -> float:
    """Apply an adjustment based on type."""
    if adjustment_type == "deductible":
        # Deductible: typically $50-$500
        return min(amount, random_float(50.0, 500.0))
    elif adjustment_type == "coinsurance":
        # Coinsurance: typically 20% of allowed amount
        pct = random.uniform(0.15, 0.25)
        return round(amount * pct, 2)
    elif adjustment_type == "copay":
        # Copay: typically $15-$50
        return random_float(15.0, 50.0)
    elif adjustment_type == "contractual":
        # Contractual adjustment: difference between charge and allowed
        discount_pct = random.uniform(0.20, 0.40)
        return round(amount * discount_pct, 2)
    else:
        return 0.0


def generate_gender() -> str:
    """Generate a gender (simplified for EDI)."""
    return random.choice(["MALE", "FEMALE"])


def format_icd10_code(code: str) -> str:
    """Format ICD-10 code with decimal point."""
    # If code doesn't have a decimal and is longer than 3 chars, add it
    if "." not in code and len(code) > 3:
        return f"{code[:3]}.{code[3:]}"
    return code
