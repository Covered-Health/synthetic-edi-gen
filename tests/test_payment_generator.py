"""Tests for synthetic_edi_gen.payment_generator."""

import pytest

from synthetic_edi_gen.basic_codes import DENIAL_CARC_CODES
from synthetic_edi_gen.claim_generator import ClaimGenerator
from synthetic_edi_gen.payment_generator import PaymentGenerator


@pytest.fixture()
def _make_claim_payment():
    """Factory that returns (claim, payment) for a given seed."""

    def _factory(seed=42):
        cg = ClaimGenerator(seed=seed)
        pg = PaymentGenerator(seed=seed)
        claim = cg.generate_claim()
        payment = pg.generate_payment_for_claim(claim)
        return claim, payment

    return _factory


class TestPaymentMatchesClaim:
    def test_pcn_matches(self, sample_claim_payment_pair):
        claim, payment = sample_claim_payment_pair
        assert payment.patient_control_number == claim.patient_control_number

    def test_charge_amount_matches(self, sample_claim_payment_pair):
        claim, payment = sample_claim_payment_pair
        assert payment.charge_amount == claim.charge_amount

    def test_service_dates_match(self, sample_claim_payment_pair):
        claim, payment = sample_claim_payment_pair
        assert payment.service_date_from == claim.service_date_from

    def test_facility_code_matches(self, sample_claim_payment_pair):
        claim, payment = sample_claim_payment_pair
        assert payment.facility_code == claim.facility_code

    def test_payment_line_count_matches_claim(self, sample_claim_payment_pair):
        claim, payment = sample_claim_payment_pair
        assert len(payment.service_lines) == len(claim.service_lines)


class TestPaymentStructure:
    def test_payment_has_required_fields(self, sample_claim_payment_pair):
        _, payment = sample_claim_payment_pair
        assert payment.object_type == "PAYMENT"
        assert payment.id
        assert payment.claim_status_code in ("1", "2", "19")
        assert payment.payer is not None
        assert payment.transaction is not None

    def test_payment_date_after_service_date(self, sample_claim_payment_pair):
        claim, payment = sample_claim_payment_pair
        # Payment date is on the transaction
        assert payment.transaction.payment_date >= claim.service_date_from

    def test_payer_control_number_contains_pcn(self, sample_claim_payment_pair):
        claim, payment = sample_claim_payment_pair
        assert claim.patient_control_number[:8] in payment.payer_control_number

    def test_transaction_metadata(self, sample_claim_payment_pair):
        _, payment = sample_claim_payment_pair
        txn = payment.transaction
        assert txn.transaction_type == "835"
        assert txn.payment_method_type == "CHK"
        assert txn.check_or_eft_trace_number


class TestPaymentScenarios:
    """Test the three payment scenarios by generating many payments."""

    @pytest.fixture()
    def many_payments(self):
        """Generate a large sample to cover all scenarios."""
        cg = ClaimGenerator(seed=0)
        pg = PaymentGenerator(seed=0)
        results = []
        for _ in range(100):
            claim = cg.generate_claim()
            payment = pg.generate_payment_for_claim(claim)
            results.append((claim, payment))
        return results

    def test_has_full_payments(self, many_payments):
        full = [p for _, p in many_payments if p.claim_status_code == "1"]
        assert len(full) > 0

    def test_has_partial_payments(self, many_payments):
        partial = [p for _, p in many_payments if p.claim_status_code == "19"]
        assert len(partial) > 0

    def test_has_denials(self, many_payments):
        denied = [p for _, p in many_payments if p.claim_status_code == "2"]
        assert len(denied) > 0

    def test_full_payment_has_contractual_adjustment(self, many_payments):
        full = [p for _, p in many_payments if p.claim_status_code == "1"]
        for payment in full[:5]:
            for line in payment.service_lines:
                if line.adjustments:
                    groups = {a.group for a in line.adjustments}
                    assert "CONTRACTUAL" in groups

    def test_partial_payment_has_patient_responsibility(self, many_payments):
        partial = [p for _, p in many_payments if p.claim_status_code == "19"]
        for payment in partial[:5]:
            has_pr = False
            for line in payment.service_lines:
                if line.adjustments:
                    for adj in line.adjustments:
                        if adj.group == "PATIENT_RESPONSIBILITY":
                            has_pr = True
            assert has_pr

    def test_denial_has_zero_paid(self, many_payments):
        denied = [p for _, p in many_payments if p.claim_status_code == "2"]
        for payment in denied:
            assert payment.payment_amount == 0.0
            for line in payment.service_lines:
                assert line.paid_amount == 0.0

    def test_denial_has_denial_carc(self, many_payments):
        denied = [p for _, p in many_payments if p.claim_status_code == "2"]
        for payment in denied[:5]:
            for line in payment.service_lines:
                assert line.adjustments
                carc_codes = {a.reason.code for a in line.adjustments}
                assert carc_codes & DENIAL_CARC_CODES

    def test_denial_has_rarc_remarks(self, many_payments):
        denied = [p for _, p in many_payments if p.claim_status_code == "2"]
        for payment in denied[:5]:
            for line in payment.service_lines:
                assert line.remarks
                for remark in line.remarks:
                    assert remark.sub_type == "RARC"


class TestPaymentLineDetails:
    def test_line_procedure_matches_claim_line(self, sample_claim_payment_pair):
        claim, payment = sample_claim_payment_pair
        for cl, pl in zip(claim.service_lines, payment.service_lines, strict=True):
            assert pl.procedure.code == cl.procedure.code

    def test_line_charge_amount_matches(self, sample_claim_payment_pair):
        claim, payment = sample_claim_payment_pair
        for cl, pl in zip(claim.service_lines, payment.service_lines, strict=True):
            assert pl.charge_amount == cl.charge_amount

    def test_paid_does_not_exceed_charge(self, sample_claim_payment_pair):
        _, payment = sample_claim_payment_pair
        for line in payment.service_lines:
            assert line.paid_amount <= line.charge_amount


class TestReproducibility:
    def test_same_seed_produces_same_payment(self):
        cg1 = ClaimGenerator(seed=99)
        pg1 = PaymentGenerator(seed=99)
        claim1 = cg1.generate_claim()
        pay1 = pg1.generate_payment_for_claim(claim1)

        cg2 = ClaimGenerator(seed=99)
        pg2 = PaymentGenerator(seed=99)
        claim2 = cg2.generate_claim()
        pay2 = pg2.generate_payment_for_claim(claim2)

        assert pay1.payment_amount == pay2.payment_amount
        assert pay1.claim_status_code == pay2.claim_status_code
