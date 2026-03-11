"""Tests for synthetic_edi_gen.reference_data - data integrity."""

from synthetic_edi_gen.reference_data import (
    CITIES_STATES,
    COMMON_PAYERS,
    FIRST_NAMES,
    LAST_NAMES,
    PLACE_OF_SERVICE,
)


class TestPlaceOfService:
    def test_not_empty(self):
        assert len(PLACE_OF_SERVICE) > 0

    def test_codes_are_two_digit_strings(self):
        for pos in PLACE_OF_SERVICE:
            assert len(pos.code) == 2
            assert pos.code.isdigit()

    def test_unique_codes(self):
        codes = [p.code for p in PLACE_OF_SERVICE]
        assert len(codes) == len(set(codes))


class TestCommonPayers:
    def test_not_empty(self):
        assert len(COMMON_PAYERS) > 0

    def test_payers_have_required_fields(self):
        for payer in COMMON_PAYERS:
            assert payer.name
            assert payer.identifier
            assert payer.claim_filing_code
            assert payer.plan_type


class TestFirstNames:
    def test_has_male_and_female(self):
        assert "MALE" in FIRST_NAMES
        assert "FEMALE" in FIRST_NAMES

    def test_not_empty(self):
        assert len(FIRST_NAMES["MALE"]) > 0
        assert len(FIRST_NAMES["FEMALE"]) > 0


class TestLastNames:
    def test_not_empty(self):
        assert len(LAST_NAMES) > 0


class TestCitiesStates:
    def test_not_empty(self):
        assert len(CITIES_STATES) > 0

    def test_zip_codes_are_5_digits(self):
        for cs in CITIES_STATES:
            assert len(cs.zip) == 5
            assert cs.zip.isdigit()

    def test_states_are_2_chars(self):
        for cs in CITIES_STATES:
            assert len(cs.state) == 2
