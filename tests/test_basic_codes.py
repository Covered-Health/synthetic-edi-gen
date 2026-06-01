"""Tests for synthetic_edi_gen.basic_codes - data integrity."""

from synthetic_edi_gen.basic_codes import (
    BASIC_CARC_CODES,
    BASIC_CPT_CODES,
    BASIC_HCPCS_DRUG_CODES,
    BASIC_ICD10_CODES,
    BASIC_MODIFIERS,
    BASIC_RARC_CODES,
    DENIAL_CARC_CODES,
    DENIAL_RARC_CODES,
)


class TestBasicCPTCodes:
    def test_not_empty(self):
        assert len(BASIC_CPT_CODES) > 0

    def test_codes_are_5_digits(self):
        for cpt in BASIC_CPT_CODES:
            assert len(cpt.code) == 5
            assert cpt.code.isdigit()

    def test_min_cost_less_than_max(self):
        for cpt in BASIC_CPT_CODES:
            assert cpt.min_cost < cpt.max_cost

    def test_each_has_common_icd10(self):
        for cpt in BASIC_CPT_CODES:
            assert len(cpt.common_icd10) > 0

    def test_unique_codes(self):
        codes = [c.code for c in BASIC_CPT_CODES]
        assert len(codes) == len(set(codes))


class TestBasicHCPCSDrugCodes:
    def test_not_empty(self):
        assert len(BASIC_HCPCS_DRUG_CODES) > 0

    def test_hcpcs_codes_are_valid_jcodes(self):
        for drug in BASIC_HCPCS_DRUG_CODES:
            assert len(drug.hcpcs_code) == 5
            assert drug.hcpcs_code[0].isalpha()
            assert drug.hcpcs_code[1:].isdigit()

    def test_unique_hcpcs_codes(self):
        codes = [d.hcpcs_code for d in BASIC_HCPCS_DRUG_CODES]
        assert len(codes) == len(set(codes))

    def test_ndc_is_11_digit_numeric(self):
        for drug in BASIC_HCPCS_DRUG_CODES:
            assert len(drug.ndc) == 11
            assert drug.ndc.isdigit()

    def test_unit_of_measure_is_valid(self):
        valid_units = {"F2", "GR", "ME", "ML", "UN"}
        for drug in BASIC_HCPCS_DRUG_CODES:
            assert drug.ndc_unit in valid_units

    def test_quantities_and_costs_are_positive(self):
        for drug in BASIC_HCPCS_DRUG_CODES:
            assert drug.ndc_qty_per_unit > 0
            assert drug.max_units >= 1
            assert 0 < drug.min_cost <= drug.max_cost

    def test_each_has_common_icd10(self):
        for drug in BASIC_HCPCS_DRUG_CODES:
            assert len(drug.common_icd10) > 0


class TestBasicICD10Codes:
    def test_not_empty(self):
        assert len(BASIC_ICD10_CODES) > 0

    def test_codes_have_expected_fields(self):
        for icd in BASIC_ICD10_CODES:
            assert icd.code
            assert icd.description
            assert icd.category

    def test_unique_codes(self):
        codes = [c.code for c in BASIC_ICD10_CODES]
        assert len(codes) == len(set(codes))


class TestBasicCARCCodes:
    def test_not_empty(self):
        assert len(BASIC_CARC_CODES) > 0

    def test_group_is_valid(self):
        valid_groups = {"CONTRACTUAL", "PATIENT_RESPONSIBILITY", "OTHER"}
        for carc in BASIC_CARC_CODES:
            assert carc.group in valid_groups

    def test_denial_codes_exist_in_basic(self):
        basic_codes = {c.code for c in BASIC_CARC_CODES}
        for code in DENIAL_CARC_CODES:
            assert code in basic_codes, f"Denial code {code} not in BASIC_CARC_CODES"


class TestBasicRARCCodes:
    def test_not_empty(self):
        assert len(BASIC_RARC_CODES) > 0
        assert len(DENIAL_RARC_CODES) > 0

    def test_no_overlap_between_basic_and_denial(self):
        basic_codes = {r.code for r in BASIC_RARC_CODES}
        denial_codes = {r.code for r in DENIAL_RARC_CODES}
        assert basic_codes.isdisjoint(denial_codes)


class TestBasicModifiers:
    def test_not_empty(self):
        assert len(BASIC_MODIFIERS) > 0

    def test_each_has_code_and_description(self):
        for mod in BASIC_MODIFIERS:
            assert "code" in mod
            assert "description" in mod
