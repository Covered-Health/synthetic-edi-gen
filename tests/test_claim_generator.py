"""Tests for synthetic_edi_gen.claim_generator."""

import random
from datetime import date

import pytest

from synthetic_edi_gen.basic_codes import BASIC_CPT_CODES, BASIC_HCPCS_DRUG_CODES
from synthetic_edi_gen.claim_generator import ClaimGenerator


class TestClaimGeneratorReproducibility:
    def test_same_seed_produces_same_claims(self):
        claim_a = ClaimGenerator(seed=123).generate_claim()
        claim_b = ClaimGenerator(seed=123).generate_claim()

        assert claim_a.patient_control_number == claim_b.patient_control_number
        assert claim_a.charge_amount == claim_b.charge_amount

    def test_different_seeds_produce_different_claims(self):
        claim_a = ClaimGenerator(seed=1).generate_claim()
        claim_b = ClaimGenerator(seed=2).generate_claim()

        assert claim_a.patient_control_number != claim_b.patient_control_number


class TestGeneratePatientContext:
    def test_context_has_all_required_fields(self, claim_generator):
        ctx = claim_generator.generate_patient_context()

        assert ctx.patient_first
        assert ctx.patient_last
        assert ctx.patient_dob is not None
        assert ctx.patient_gender in ("MALE", "FEMALE")
        assert ctx.patient_address is not None
        assert ctx.subscriber_first
        assert ctx.subscriber_last
        assert ctx.relationship in ("SELF", "CHILD", "SPOUSE", "OTHER")
        assert ctx.member_id
        assert ctx.group_or_policy_number
        assert ctx.payer_info is not None
        assert ctx.billing_provider is not None
        assert ctx.base_service_date is not None

    def test_self_relationship_shares_demographics(self):
        random.seed(0)
        # Generate many contexts and find one with SELF relationship
        gen = ClaimGenerator(seed=0)
        contexts = [gen.generate_patient_context() for _ in range(20)]
        self_ctx = next(c for c in contexts if c.relationship == "SELF")

        assert self_ctx.patient_first == self_ctx.subscriber_first
        assert self_ctx.patient_last == self_ctx.subscriber_last
        assert self_ctx.patient_dob == self_ctx.subscriber_dob

    def test_custom_service_date(self, claim_generator):
        custom_date = date(2025, 6, 15)
        ctx = claim_generator.generate_patient_context(service_date=custom_date)
        assert ctx.base_service_date == custom_date


class TestGenerateClaim:
    def test_claim_structure(self, sample_claim):
        claim = sample_claim

        assert claim.object_type == "CLAIM"
        assert claim.patient_control_number
        assert claim.charge_amount > 0
        assert claim.service_lines
        assert 1 <= len(claim.service_lines) <= 4
        assert claim.diags
        assert claim.subscriber is not None
        assert claim.patient is not None
        assert claim.billing_provider is not None
        assert claim.providers  # rendering provider

    def test_charge_equals_sum_of_lines(self, sample_claim):
        claim = sample_claim
        line_total = sum(line.charge_amount for line in claim.service_lines)
        assert abs(claim.charge_amount - line_total) < 0.01

    def test_service_line_has_procedure(self, sample_claim):
        cpt_codes = {c.code for c in BASIC_CPT_CODES}
        drug_codes = {d.hcpcs_code for d in BASIC_HCPCS_DRUG_CODES}
        for line in sample_claim.service_lines:
            assert line.procedure is not None
            assert line.procedure.code
            if line.procedure.sub_type == "CPT":
                assert line.procedure.code in cpt_codes
            else:
                assert line.procedure.sub_type == "HCPCS"
                assert line.procedure.code in drug_codes

    def test_diagnoses_cover_all_pointers(self, sample_claim):
        claim = sample_claim
        max_pointer = 0
        for line in claim.service_lines:
            if line.diag_pointers:
                max_pointer = max(max_pointer, max(line.diag_pointers))
        assert len(claim.diags) >= max_pointer

    def test_first_diagnosis_is_principal(self, sample_claim):
        assert sample_claim.diags[0].sub_type == "ICD_10_PRINCIPAL"

    def test_subsequent_diagnoses_are_icd10(self, claim_generator):
        # Generate a claim with multiple diagnoses
        random.seed(10)
        for _ in range(20):
            claim = claim_generator.generate_claim()
            if len(claim.diags) > 1:
                for diag in claim.diags[1:]:
                    assert diag.sub_type == "ICD_10"
                return
        pytest.skip("No multi-diagnosis claim generated in 20 attempts")

    def test_facility_code_set(self, sample_claim):
        assert sample_claim.facility_code is not None
        assert sample_claim.facility_code.sub_type == "PLACE_OF_SERVICE"

    def test_transaction_metadata(self, sample_claim):
        txn = sample_claim.transaction
        assert txn is not None
        assert txn.transaction_type == "PROF"
        assert txn.sender is not None
        assert txn.receiver is not None

    def test_claim_with_custom_service_date(self, claim_generator):
        custom_date = date(2025, 3, 15)
        claim = claim_generator.generate_claim(service_date=custom_date)
        assert claim.service_date_from == custom_date

    def test_claim_uses_shared_context(self, claim_generator):
        ctx = claim_generator.generate_patient_context()
        claim = claim_generator.generate_claim(ctx=ctx)

        assert claim.billing_provider == ctx.billing_provider
        assert claim.patient.person.first_name == ctx.patient_first
        assert claim.patient.person.last_name_or_org_name == ctx.patient_last

    def test_billing_provider_is_business(self, sample_claim):
        bp = sample_claim.billing_provider
        assert bp.entity_role == "BILLING_PROVIDER"
        assert bp.entity_type == "BUSINESS"
        assert bp.identifier  # NPI
        assert bp.tax_id

    def test_rendering_provider_is_individual(self, sample_claim):
        rp = sample_claim.providers[0]
        assert rp.entity_role == "RENDERING"
        assert rp.entity_type == "INDIVIDUAL"
        assert rp.identifier  # NPI


class TestUniquePcns:
    def test_multiple_claims_have_unique_pcns(self, claim_generator):
        claims = [claim_generator.generate_claim() for _ in range(50)]
        pcns = [c.patient_control_number for c in claims]
        assert len(pcns) == len(set(pcns))


class TestMultiPcnContext:
    def test_claims_share_patient_demographics(self, claim_generator):
        ctx = claim_generator.generate_patient_context()
        claims = [claim_generator.generate_claim(ctx=ctx) for _ in range(3)]

        # All claims share same patient name and billing provider
        for claim in claims:
            assert claim.patient.person.first_name == ctx.patient_first
            assert claim.patient.person.last_name_or_org_name == ctx.patient_last
            assert claim.billing_provider.identifier == ctx.billing_provider.identifier

    def test_claims_have_different_pcns(self, claim_generator):
        ctx = claim_generator.generate_patient_context()
        claims = [claim_generator.generate_claim(ctx=ctx) for _ in range(5)]
        pcns = {c.patient_control_number for c in claims}
        assert len(pcns) == 5

    def test_claims_can_have_different_service_dates(self, claim_generator):
        ctx = claim_generator.generate_patient_context()
        d1 = date(2025, 1, 1)
        d2 = date(2025, 1, 5)
        c1 = claim_generator.generate_claim(ctx=ctx, service_date=d1)
        c2 = claim_generator.generate_claim(ctx=ctx, service_date=d2)

        assert c1.service_date_from == d1
        assert c2.service_date_from == d2


class TestDrugServiceLines:
    def _drug_lines(self, generator, attempts=300):
        """Collect every drug service line across many generated claims."""
        lines = []
        for _ in range(attempts):
            claim = generator.generate_claim()
            lines.extend(line for line in claim.service_lines if line.drug)
        return lines

    def test_drug_lines_are_generated(self, claim_generator):
        assert self._drug_lines(claim_generator), (
            "expected at least some service lines to bill an administered drug"
        )

    def test_drug_line_fields_are_correlated(self, claim_generator):
        by_hcpcs = {d.hcpcs_code: d for d in BASIC_HCPCS_DRUG_CODES}
        for line in self._drug_lines(claim_generator):
            # The procedure is the drug's HCPCS J-code, not a CPT code.
            assert line.procedure is not None
            assert line.procedure.sub_type == "HCPCS"
            drug_data = by_hcpcs.get(line.procedure.code)
            assert drug_data is not None, "drug line uses an unknown HCPCS code"

            # The NDC identifies the product the J-code administers.
            assert line.drug is not None
            assert line.drug.sub_type == "NDC"
            assert line.drug.code == drug_data.ndc
            assert line.drug.desc == drug_data.drug_name

            # Quantity and unit of measure stay consistent with the procedure.
            assert line.drug_unit_type == drug_data.ndc_unit
            expected_qty = round(line.unit_count * drug_data.ndc_qty_per_unit, 3)
            assert line.drug_quantity == expected_qty
            assert line.drug_quantity > 0

    def test_non_drug_lines_have_no_ndc(self, claim_generator):
        for _ in range(50):
            claim = claim_generator.generate_claim()
            for line in claim.service_lines:
                if line.procedure and line.procedure.sub_type == "CPT":
                    assert line.drug is None
                    assert line.drug_quantity is None
                    assert line.drug_unit_type is None

    def test_forced_drug_hcpcs_code_yields_ndc_line(self, claim_generator):
        drug = BASIC_HCPCS_DRUG_CODES[0]
        claim = claim_generator.generate_claim(forced_cpt_codes=[drug.hcpcs_code])

        assert len(claim.service_lines) == 1
        line = claim.service_lines[0]
        assert line.procedure.code == drug.hcpcs_code
        assert line.procedure.sub_type == "HCPCS"
        assert line.drug is not None
        assert line.drug.code == drug.ndc
        assert line.drug_unit_type == drug.ndc_unit

    def test_q_code_drug_lines_are_generated(self, claim_generator):
        q_drugs = [d for d in BASIC_HCPCS_DRUG_CODES if d.hcpcs_code.startswith("Q")]
        assert q_drugs, "expected Q-code entries in BASIC_HCPCS_DRUG_CODES"
        for q_drug in q_drugs:
            claim = claim_generator.generate_claim(
                forced_cpt_codes=[q_drug.hcpcs_code]
            )
            line = claim.service_lines[0]
            assert line.procedure.sub_type == "HCPCS"
            assert line.procedure.code == q_drug.hcpcs_code
            assert line.drug is not None
            assert line.drug.code == q_drug.ndc


class TestDrugDefects:
    def _drug_lines_with_defects(self, attempts=500):
        gen = ClaimGenerator(seed=99, drug_defect_rate=0.50)
        lines = []
        for _ in range(attempts):
            claim = gen.generate_claim()
            for line in claim.service_lines:
                if line.procedure and line.procedure.sub_type == "HCPCS":
                    lines.append(line)
        return lines

    def test_some_drug_lines_missing_ndc(self):
        lines = self._drug_lines_with_defects()
        missing_ndc = [ln for ln in lines if ln.drug is None]
        assert missing_ndc, "expected some drug lines with missing NDC"

    def test_some_drug_lines_have_quantity_mismatch(self):
        by_hcpcs = {d.hcpcs_code: d for d in BASIC_HCPCS_DRUG_CODES}
        lines = self._drug_lines_with_defects()
        mismatched = []
        for line in lines:
            if line.drug is None:
                continue
            drug_data = by_hcpcs.get(line.procedure.code)
            if drug_data is None:
                continue
            expected_qty = round(line.unit_count * drug_data.ndc_qty_per_unit, 3)
            if line.drug_quantity != expected_qty:
                mismatched.append(line)
        assert mismatched, "expected some drug lines with quantity mismatch"

    def test_zero_defect_rate_produces_no_defects(self):
        gen = ClaimGenerator(seed=42, drug_defect_rate=0.0)
        by_hcpcs = {d.hcpcs_code: d for d in BASIC_HCPCS_DRUG_CODES}
        for _ in range(200):
            claim = gen.generate_claim()
            for line in claim.service_lines:
                if line.procedure and line.procedure.sub_type == "HCPCS":
                    assert line.drug is not None
                    drug_data = by_hcpcs[line.procedure.code]
                    expected = round(line.unit_count * drug_data.ndc_qty_per_unit, 3)
                    assert line.drug_quantity == expected
