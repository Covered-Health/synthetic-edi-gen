"""
Generate realistic 835 (Payment/Remittance) records that match 837 claims.
"""

# ruff: noqa: S311
import random
import uuid
from datetime import date, timedelta
from typing import Any, cast

from synthetic_edi_gen.edi_models import (
    Adjustment,
    Code,
    InstClaim,
    InstLine,
    Party,
    PatientSubscriber835,
    Payment,
    PaymentLine,
    ProfClaim,
    ProfLine,
    Transaction835,
)

from .basic_codes import DENIAL_CARC_CODES, REJECTION_RARCS_BY_CARC
from .helpers import generate_transaction_id
from .reference_data import Payer, sample_payer
from .stats import (
    code_description,
    sample_conditional,
    sample_count,
    sample_numeric,
    sample_presence,
    sample_reverse_conditional,
)


class PaymentGenerator:
    """Generator for 835 payment/remittance records."""

    def __init__(self, seed: int | None = None):
        """Initialize the generator with optional seed for reproducibility."""
        if seed is not None:
            random.seed(seed)

    def generate_payment_for_claim(
        self, claim: ProfClaim | InstClaim, forwarded: bool = False
    ) -> Payment:
        """
        Generate a matching 835 payment for a given 837 claim.

        The payment will have matching PCN and realistic payment logic.
        """
        # Determine payment scenario
        first_procedure = next(
            (
                line.procedure.code
                for line in claim.service_lines or []
                if line.procedure is not None
            ),
            "",
        )
        payment_scenario = self._select_payment_scenario(first_procedure)

        # Extract claim info
        pcn = claim.patient_control_number
        total_charge = float(claim.charge_amount)
        service_lines = claim.service_lines or []

        # Generate payment date (5-45 days after service)
        claim_date = (
            claim.service_date_from
            or getattr(claim, "statement_date_from", None)
            or date.today()
        )
        payment_date = claim_date + timedelta(days=random.randint(5, 45))

        # Process service lines with payment logic
        payment_lines: list[PaymentLine] = []
        total_paid = 0.0
        total_patient_responsibility = 0.0

        for claim_line in service_lines:
            payment_line = self._process_service_line(claim_line, payment_scenario)
            payment_lines.append(payment_line)
            total_paid += payment_line.paid_amount
            if payment_line.adjustments:
                for adj in payment_line.adjustments:
                    if adj.group == "PATIENT_RESPONSIBILITY":
                        total_patient_responsibility += adj.amount

        # Determine claim status
        if payment_scenario["type"] == "full_denial":
            claim_status_code = "2"
            claim_status = "DENIED"
        elif forwarded:
            claim_status_code = "19"
            claim_status = "PRIMARY_FORWARDED"
        elif payment_scenario["type"] == "partial_payment":
            claim_status_code = "19"
            claim_status = "PRIMARY"
        else:  # full_payment
            claim_status_code = "1"
            claim_status = "PRIMARY"

        # Build patient info
        patient: PatientSubscriber835 | None = None
        if claim.patient and claim.patient.person:
            p = claim.patient.person
            patient = PatientSubscriber835(
                person=Party(
                    entity_role=p.entity_role,
                    entity_type=p.entity_type,
                    identification_type=p.identification_type,
                    identifier=p.identifier,
                    last_name_or_org_name=p.last_name_or_org_name,
                    first_name=p.first_name,
                    middle_name=p.middle_name,
                    address=p.address,
                    contacts=p.contacts,
                    additional_ids=p.additional_ids,
                )
            )

        # Get payer from subscriber
        payer_info = claim.subscriber.payer if claim.subscriber else None

        # Get subscriber info
        subscriber = claim.subscriber

        payment = Payment(
            id=uuid.uuid4().hex[:24],
            object_type="PAYMENT",
            patient_control_number=pcn,
            charge_amount=float(total_charge),
            payment_amount=float(total_paid),
            facility_code=claim.facility_code,
            frequency_code=claim.frequency_code,
            statement_date_from=getattr(claim, "statement_date_from", None),
            statement_date_to=getattr(claim, "statement_date_to", None),
            service_date_from=claim.service_date_from,
            service_date_to=claim.service_date_to,
            claim_status_code=claim_status_code,
            claim_status=claim_status,
            patient_responsibility_amount=float(total_patient_responsibility),
            claim_filing_indicator_code=subscriber.claim_filing_indicator_code
            if subscriber
            else None,
            insurance_plan_type=(
                subscriber.insurance_plan_type if subscriber else None
            ),
            payer_control_number="PAYER" + pcn[:8],
            payer=payer_info,
            payee=claim.billing_provider,
            service_provider=next(iter(claim.providers or []), None),
            patient=patient,
            service_lines=payment_lines,
            transaction=self._generate_transaction(payment_date, float(total_paid)),
        )

        return payment

    def generate_rejection_for_claim(self, claim: ProfClaim | InstClaim) -> Payment:
        """Generate a clearinghouse rejection using a recognized CARC/RARC pair."""
        payment = self.generate_payment_for_claim(claim)
        carc = random.choice(tuple(REJECTION_RARCS_BY_CARC))
        rarc = random.choice(REJECTION_RARCS_BY_CARC[carc])

        payment.payment_amount = 0
        payment.patient_responsibility_amount = 0
        payment.claim_status_code = "2"
        payment.claim_status = "DENIED"
        payment.transaction.total_payment_amount = 0
        rejection_date = (claim.transaction.creation_date or date.today()) + timedelta(
            days=random.randint(1, 2)
        )
        payment.transaction.payment_date = rejection_date
        payment.transaction.production_date = rejection_date
        for line in payment.service_lines or []:
            line.paid_amount = 0
            line.adjustments = [
                Adjustment(
                    group="CORRECTION",
                    reason=Code(sub_type="CARC", code=carc),
                    amount=line.charge_amount,
                )
            ]
            line.remarks = [Code(sub_type="RARC", code=rarc)]
            line.remark_codes = [rarc]
        return payment

    def generate_secondary_payment_for_claim(
        self, claim: ProfClaim | InstClaim, primary_payment: Payment
    ) -> Payment:
        """Generate a secondary payer 835 for amounts left by the primary."""
        first_procedure = next(
            (
                line.procedure.code
                for line in claim.service_lines or []
                if line.procedure is not None
            ),
            "",
        )
        deny = (
            sample_reverse_conditional(
                "payment_status_procedure",
                first_procedure,
                fallback="payment_status",
                allowed={"SECONDARY", "DENIED"},
            )
            == "DENIED"
        )
        primary_lines = {
            line.source_line_id: line for line in primary_payment.service_lines or []
        }
        payment_lines: list[PaymentLine] = []
        total_paid = 0.0
        total_patient_responsibility = 0.0

        for claim_line in claim.service_lines or []:
            primary_line = primary_lines.get(claim_line.source_line_id)
            payment_line = self._process_secondary_service_line(
                claim_line, primary_line, deny=deny
            )
            payment_lines.append(payment_line)
            total_paid += payment_line.paid_amount
            for adj in payment_line.adjustments or []:
                if adj.group == "PATIENT_RESPONSIBILITY":
                    total_patient_responsibility += adj.amount

        payment_date = primary_payment.transaction.payment_date + timedelta(
            days=random.randint(7, 35)
        )
        payer_data = self._secondary_payer_data(claim)
        payer = self._payer_party(payer_data)

        return Payment(
            id=uuid.uuid4().hex[:24],
            object_type="PAYMENT",
            patient_control_number=claim.patient_control_number,
            charge_amount=float(claim.charge_amount),
            payment_amount=float(round(total_paid, 2)),
            facility_code=claim.facility_code,
            frequency_code=claim.frequency_code,
            statement_date_from=getattr(claim, "statement_date_from", None),
            statement_date_to=getattr(claim, "statement_date_to", None),
            service_date_from=claim.service_date_from,
            service_date_to=claim.service_date_to,
            claim_status_code="2",
            claim_status="DENIED" if deny else "SECONDARY",
            patient_responsibility_amount=float(round(total_patient_responsibility, 2)),
            claim_filing_indicator_code=payer_data.claim_filing_code,
            insurance_plan_type=payer_data.plan_type,
            payer_control_number="PAYER" + claim.patient_control_number[:8] + "S",
            payer=payer,
            payee=claim.billing_provider,
            service_provider=next(iter(claim.providers or []), None),
            patient=primary_payment.patient,
            service_lines=payment_lines,
            transaction=self._generate_transaction(payment_date, float(total_paid)),
        )

    def _select_payment_scenario(self, procedure: str = "") -> dict[str, Any]:
        """Select paid or denied using the procedure/status correlation."""
        status = sample_reverse_conditional(
            "payment_status_procedure",
            procedure,
            fallback="payment_status",
            allowed={"PRIMARY", "DENIED"},
        )
        return {"type": "full_denial" if status == "DENIED" else "full_payment"}

    def _process_service_line(
        self, claim_line: ProfLine | InstLine, scenario: dict[str, Any]
    ) -> PaymentLine:
        """Process a line with empirical financial and adjustment distributions."""
        charge_amount = float(claim_line.charge_amount)
        procedure = claim_line.procedure.code if claim_line.procedure else ""
        adjustments: list[Adjustment] = []
        if scenario["type"] == "full_denial":
            paid_amount = 0.0
            adjustment_count = max(1, sample_count("payment_line_adjustment_count"))
        else:
            paid_amount = min(
                charge_amount,
                max(
                    0.0,
                    sample_numeric(
                        "paid_amount_bucket",
                        conditions=(("procedure_paid_amount_bucket", procedure),),
                    ),
                ),
            )
            adjustment_count = (
                sample_count("payment_line_adjustment_count")
                if sample_presence("payment_line_has_adjustment")
                else 0
            )

        selected_carcs: list[str] = []
        for _ in range(adjustment_count):
            carc = sample_conditional(
                "procedure_carc",
                procedure,
                fallback="carc",
                allowed=DENIAL_CARC_CODES
                if scenario["type"] == "full_denial"
                else None,
            )
            group = sample_conditional("carc_group", carc, fallback="adjustment_group")
            bucket_amount = sample_numeric(
                "adjustment_amount_bucket",
                conditions=(("procedure_adjustment_amount_bucket", procedure),),
            )
            percent = sample_numeric(
                "adjustment_percent_bucket",
                conditions=(("procedure_adjustment_percent_bucket", procedure),),
            )
            amount = min(
                charge_amount,
                (bucket_amount + charge_amount * percent / 100) / 2,
            )
            adjustments.append(
                Adjustment(
                    group=cast(Any, group),
                    reason=Code(
                        sub_type="CARC",
                        code=carc,
                        desc=code_description("carc", carc),
                    ),
                    amount=float(amount),
                )
            )
            selected_carcs.append(carc)

        remarks: list[Code] = []
        rarc_count = (
            sample_count("payment_line_rarc_count")
            if sample_presence("payment_line_has_rarc")
            else 0
        )
        if "16" in selected_carcs:
            rarc_count = max(1, rarc_count)
        for _ in range(rarc_count):
            correlation, value = (
                ("carc_rarc", selected_carcs[0])
                if selected_carcs
                else ("procedure_rarc", procedure)
            )
            rarc = sample_conditional(correlation, value, fallback="rarc")
            if rarc not in {remark.code for remark in remarks}:
                remarks.append(
                    Code(
                        sub_type="RARC",
                        code=rarc,
                        desc=code_description("rarc", rarc),
                    )
                )

        return PaymentLine(
            source_line_id=claim_line.source_line_id,
            charge_amount=float(charge_amount),
            paid_amount=float(paid_amount),
            service_date_from=claim_line.service_date_from,
            service_date_to=claim_line.service_date_to,
            unit_count=claim_line.unit_count,
            procedure=claim_line.procedure,
            revenue_code=getattr(claim_line, "revenue_code", None),
            adjustments=adjustments or None,
            remarks=remarks or None,
            remark_codes=[r.code for r in remarks] if remarks else None,
        )

    def _process_secondary_service_line(
        self,
        claim_line: ProfLine | InstLine,
        primary_line: PaymentLine | None,
        deny: bool,
    ) -> PaymentLine:
        charge_amount = float(claim_line.charge_amount)
        primary_paid = float(primary_line.paid_amount) if primary_line else 0.0
        unpaid_amount = max(charge_amount - primary_paid, 0.0)

        if deny:
            paid_amount = 0.0
            procedure = claim_line.procedure.code if claim_line.procedure else ""
            adjustments = self._generate_denial_adjustments(unpaid_amount, procedure)
            reason = adjustments[0].reason
            carc = reason.code if reason else "16"
            rarc = sample_conditional("carc_rarc", carc, fallback="rarc")
            remarks = [
                Code(
                    sub_type="RARC",
                    code=rarc,
                    desc=code_description("rarc", rarc),
                )
            ]
        else:
            procedure = claim_line.procedure.code if claim_line.procedure else ""
            paid_amount = min(
                unpaid_amount,
                max(
                    0.0,
                    sample_numeric(
                        "paid_amount_bucket",
                        conditions=(("procedure_paid_amount_bucket", procedure),),
                    ),
                ),
            )
            patient_resp = round(unpaid_amount - paid_amount, 2)
            adjustments = []
            if patient_resp > 0:
                adjustments.append(
                    Adjustment(
                        group="PATIENT_RESPONSIBILITY",
                        reason=Code(
                            sub_type="CARC",
                            code="2",
                            desc="Coinsurance Amount",
                        ),
                        amount=float(patient_resp),
                    )
                )
            remarks = None

        return PaymentLine(
            source_line_id=claim_line.source_line_id,
            charge_amount=float(charge_amount),
            paid_amount=float(round(paid_amount, 2)),
            service_date_from=claim_line.service_date_from,
            service_date_to=claim_line.service_date_to,
            unit_count=claim_line.unit_count,
            procedure=claim_line.procedure,
            revenue_code=getattr(claim_line, "revenue_code", None),
            adjustments=adjustments or None,
            remarks=remarks,
            remark_codes=[r.code for r in remarks] if remarks else None,
        )

    def _generate_denial_adjustments(
        self, charge_amount: float, procedure: str = ""
    ) -> list[Adjustment]:
        """Generate adjustments for a denied claim."""
        carc = sample_conditional(
            "procedure_carc",
            procedure,
            fallback="carc",
            allowed=DENIAL_CARC_CODES,
        )
        group = sample_conditional("carc_group", carc, fallback="adjustment_group")
        return [
            Adjustment(
                group=cast(Any, group),
                reason=Code(
                    sub_type="CARC",
                    code=carc,
                    desc=code_description("carc", carc),
                ),
                amount=float(charge_amount),
            )
        ]

    @staticmethod
    def _secondary_payer_data(claim: ProfClaim | InstClaim) -> Payer:
        primary_id = (
            claim.subscriber.payer.identifier
            if claim.subscriber and claim.subscriber.payer
            else None
        )
        return sample_payer(exclude_identifier=primary_id)

    @staticmethod
    def _payer_party(payer: Payer) -> Party:
        return Party(
            entity_role="PAYER",
            entity_type="BUSINESS",
            identification_type="PAYOR_ID",
            identifier=payer.identifier,
            tax_id=payer.tax_id,
            last_name_or_org_name=payer.name,
        )

    def _generate_transaction(
        self, payment_date: date, total_paid: float
    ) -> Transaction835:
        """Generate transaction metadata for payment."""
        return Transaction835(
            control_number=generate_transaction_id()[:10],
            transaction_type="835",
            transaction_set_identifier_code="835",
            production_date=payment_date,
            transaction_handling_type="I",
            total_payment_amount=total_paid,
            credit_or_debit_flag_code="C" if total_paid >= 0 else "D",
            payment_method_type="CHK",
            payment_date=payment_date,
            check_or_eft_trace_number=generate_transaction_id()[:15],
            payer_identifier="1" + str(random.randint(100000000, 999999999)),
        )
