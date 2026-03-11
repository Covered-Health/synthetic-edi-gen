"""Tests for synthetic_edi_gen.helpers."""

import random
import string
from datetime import date

from synthetic_edi_gen.helpers import (
    apply_adjustment,
    calculate_contracted_amount,
    format_icd10_code,
    generate_address,
    generate_birth_date,
    generate_gender,
    generate_member_id,
    generate_npi,
    generate_patient_control_number,
    generate_person_name,
    generate_service_date,
    generate_tax_id,
    generate_transaction_id,
    random_float,
)
from synthetic_edi_gen.reference_data import FIRST_NAMES, LAST_NAMES


class TestGeneratePatientControlNumber:
    def test_returns_10_digits(self):
        pcn = generate_patient_control_number()
        assert len(pcn) == 10
        assert pcn.isdigit()

    def test_returns_different_values(self):
        pcns = {generate_patient_control_number() for _ in range(50)}
        assert len(pcns) > 1


class TestGenerateNpi:
    def test_returns_10_digits(self):
        npi = generate_npi()
        assert len(npi) == 10
        assert npi.isdigit()


class TestGenerateTaxId:
    def test_returns_9_digits(self):
        tax_id = generate_tax_id()
        assert len(tax_id) == 9
        assert tax_id.isdigit()


class TestGenerateMemberId:
    def test_format_two_letters_nine_digits(self):
        member_id = generate_member_id()
        assert len(member_id) == 11
        assert member_id[:2].isalpha()
        assert member_id[:2].isupper()
        assert member_id[2:].isdigit()


class TestGeneratePersonName:
    def test_returns_name_from_gender_list(self):
        random.seed(99)
        first, last, middle = generate_person_name("FEMALE")

        assert first in FIRST_NAMES["FEMALE"]
        assert last in LAST_NAMES
        assert len(middle) == 1
        assert middle in string.ascii_uppercase

    def test_male_name_from_male_list(self):
        random.seed(99)
        first, _, _ = generate_person_name("MALE")
        assert first in FIRST_NAMES["MALE"]


class TestGenerateBirthDate:
    def test_default_age_range(self):
        today = date.today()
        dob = generate_birth_date()
        age = today.year - dob.year
        # Allow ±1 year tolerance due to day-of-year offset
        assert 17 <= age <= 87

    def test_custom_age_range(self):
        today = date.today()
        dob = generate_birth_date(min_age=30, max_age=40)
        age = today.year - dob.year
        assert 29 <= age <= 42


class TestGenerateAddress:
    def test_returns_valid_address(self):
        random.seed(42)
        addr = generate_address()

        assert addr.city is not None
        assert addr.state_code is not None
        assert addr.zip_code is not None
        assert addr.line is not None
        # Street starts with a number
        assert addr.line[0].isdigit()

    def test_zip_is_at_least_5_chars(self):
        for _ in range(20):
            addr = generate_address()
            assert len(addr.zip_code) >= 5


class TestGenerateServiceDate:
    def test_default_range(self):
        today = date.today()
        svc = generate_service_date()
        diff = (today - svc).days
        assert 1 <= diff <= 90

    def test_custom_range(self):
        today = date.today()
        svc = generate_service_date(days_ago_min=10, days_ago_max=20)
        diff = (today - svc).days
        assert 10 <= diff <= 20


class TestGenerateTransactionId:
    def test_length_and_charset(self):
        tid = generate_transaction_id()
        assert len(tid) == 24
        allowed = set(string.digits + string.ascii_uppercase)
        assert all(c in allowed for c in tid)


class TestRandomFloat:
    def test_within_range(self):
        for _ in range(100):
            val = random_float(1.0, 10.0)
            assert 1.0 <= val <= 10.0

    def test_precision(self):
        val = random_float(1.0, 10.0, precision=3)
        # Check at most 3 decimal places
        assert val == round(val, 3)


class TestCalculateContractedAmount:
    def test_result_less_than_charge(self):
        for _ in range(50):
            charge = 500.0
            contracted = calculate_contracted_amount(charge)
            assert contracted < charge

    def test_discount_within_range(self):
        random.seed(42)
        charge = 1000.0
        contracted = calculate_contracted_amount(charge, discount_pct=0.40)
        # Discount is 20-40%, so contracted is 60-80% of charge
        assert 600.0 <= contracted <= 800.0


class TestApplyAdjustment:
    def test_deductible_capped_at_amount(self):
        # Deductible is min(amount, random(50-500))
        result = apply_adjustment(30.0, "deductible")
        assert result <= 30.0

    def test_deductible_range(self):
        random.seed(42)
        result = apply_adjustment(10000.0, "deductible")
        assert 50.0 <= result <= 500.0

    def test_coinsurance_is_percentage(self):
        random.seed(42)
        amount = 1000.0
        result = apply_adjustment(amount, "coinsurance")
        # 15-25% of amount
        assert 150.0 <= result <= 250.0

    def test_copay_range(self):
        random.seed(42)
        result = apply_adjustment(1000.0, "copay")
        assert 15.0 <= result <= 50.0

    def test_contractual_range(self):
        random.seed(42)
        amount = 1000.0
        result = apply_adjustment(amount, "contractual")
        # 20-40% of amount
        assert 200.0 <= result <= 400.0

    def test_unknown_type_returns_zero(self):
        assert apply_adjustment(1000.0, "unknown") == 0.0


class TestGenerateGender:
    def test_returns_valid_gender(self):
        for _ in range(20):
            assert generate_gender() in ("MALE", "FEMALE")


class TestFormatIcd10Code:
    def test_adds_decimal_after_third_char(self):
        assert format_icd10_code("E119") == "E11.9"

    def test_leaves_short_codes_unchanged(self):
        assert format_icd10_code("E11") == "E11"

    def test_leaves_already_formatted_unchanged(self):
        assert format_icd10_code("E11.9") == "E11.9"

    def test_longer_code(self):
        assert format_icd10_code("M5416") == "M54.16"
