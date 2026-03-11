"""
Generate realistic 835 (Payment/Remittance) records that match 837 claims.
"""

# ruff: noqa: S311
import random
import uuid
from datetime import date, timedelta
from typing import Any

from synthetic_edi_gen.edi_models import (
    Adjustment,
    Code,
    Party,
    PatientSubscriber835,
    Payment,
    PaymentLine,
    ProfClaim,
    ProfLine,
    Transaction835,
)

from .basic_codes import (
    BASIC_CARC_CODES,
    BASIC_RARC_CODES,
    DENIAL_RARC_CODES,
)
from .helpers import (
    apply_adjustment,
    calculate_contracted_amount,
    generate_transaction_id,
)


class PaymentGenerator:
    """Generator for 835 payment/remittance records."""

    def __init__(self, seed: int | None = None):
        """Initialize the generator with optional seed for reproducibility."""
        if seed is not None:
            random.seed(seed)

    def generate_payment_for_claim(self, claim: ProfClaim) -> Payment:
        """
        Generate a matching 835 payment for a given 837 claim.

        The payment will have matching PCN and realistic payment logic.
        """
        # Determine payment scenario
        payment_scenario = self._select_payment_scenario()

        # Extract claim info
        pcn = claim.patient_control_number
        total_charge = float(claim.charge_amount)
        service_lines = claim.service_lines or []

        # Generate payment date (5-45 days after service)
        claim_date = claim.service_date_from or date.today()
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
            patient=patient,
            service_lines=payment_lines,
            transaction=self._generate_transaction(payment_date, float(total_paid)),
        )

        return payment

    def _select_payment_scenario(self) -> dict[str, Any]:
        """
        Select a payment scenario with realistic probabilities.

        Scenarios:
        - full_payment: 50% - Claim fully paid with normal adjustments
        - partial_payment: 25% - Claim partially paid with patient responsibility
        - full_denial: 25% - Claim fully denied (realistic CARC/RARC on 835)
        """
        rand = random.random()

        if rand < 0.50:
            return {"type": "full_payment"}
        elif rand < 0.75:
            return {"type": "partial_payment"}
        else:
            return {"type": "full_denial"}

    def _process_service_line(
        self, claim_line: ProfLine, scenario: dict[str, Any]
    ) -> PaymentLine:
        """Process a service line and generate payment information."""
        charge_amount = float(claim_line.charge_amount)

        # Calculate contracted/allowed amount (typically 60-80% of charge)
        allowed_amount = calculate_contracted_amount(charge_amount, discount_pct=0.40)

        adjustments: list[Adjustment] = []
        remarks: list[Code] | None = None

        # Apply payment scenario
        if scenario["type"] == "full_denial":
            # Full denial - zero payment with realistic CARC and RARC
            paid_amount = 0.0
            adjustments = self._generate_denial_adjustments(charge_amount)
            # Add denial RARC remark(s)
            remarks = []
            num_remarks = random.randint(1, 2)
            for rarc in random.sample(
                DENIAL_RARC_CODES, min(num_remarks, len(DENIAL_RARC_CODES))
            ):
                remarks.append(
                    Code(
                        sub_type="RARC",
                        code=rarc.code,
                        desc=rarc.description,
                    )
                )

        elif scenario["type"] == "partial_payment":
            # Partial payment with patient responsibility

            # Add contractual adjustment (charge vs allowed)
            contractual_adj = charge_amount - allowed_amount
            if contractual_adj > 0:
                adjustments.append(
                    Adjustment(
                        group="CONTRACTUAL",
                        reason=Code(
                            sub_type="CARC",
                            code="45",
                            desc="Charge exceeds fee schedule/maximum allowable",
                        ),
                        amount=float(contractual_adj),
                    )
                )

            # Add patient responsibility (deductible, coinsurance, or copay)
            patient_resp_type = random.choice(["deductible", "coinsurance", "copay"])
            patient_resp_amount = apply_adjustment(allowed_amount, patient_resp_type)

            if patient_resp_type == "deductible":
                carc_code = "1"
                carc_desc = "Deductible Amount"
            elif patient_resp_type == "coinsurance":
                carc_code = "2"
                carc_desc = "Coinsurance Amount"
            else:  # copay
                carc_code = "3"
                carc_desc = "Co-payment Amount"

            adjustments.append(
                Adjustment(
                    group="PATIENT_RESPONSIBILITY",
                    reason=Code(sub_type="CARC", code=carc_code, desc=carc_desc),
                    amount=float(patient_resp_amount),
                )
            )

            paid_amount = allowed_amount - patient_resp_amount

        else:  # full_payment
            # Full payment with contractual adjustment only
            contractual_adj = charge_amount - allowed_amount

            if contractual_adj > 0:
                adjustments.append(
                    Adjustment(
                        group="CONTRACTUAL",
                        reason=Code(
                            sub_type="CARC",
                            code="45",
                            desc="Charge exceeds fee schedule/maximum allowable",
                        ),
                        amount=float(contractual_adj),
                    )
                )

            paid_amount = allowed_amount

        # Sometimes add remark codes (full_denial already has denial RARCs)
        if scenario["type"] != "full_denial" and random.random() < 0.3:
            remarks = []
            num_remarks = random.randint(1, 2)
            selected_remarks = random.sample(
                BASIC_RARC_CODES, min(num_remarks, len(BASIC_RARC_CODES))
            )
            for rarc in selected_remarks:
                remarks.append(
                    Code(
                        sub_type="RARC",
                        code=rarc.code,
                        desc=rarc.description,
                    )
                )

        return PaymentLine(
            source_line_id=claim_line.source_line_id,
            charge_amount=float(charge_amount),
            paid_amount=float(paid_amount),
            service_date_from=claim_line.service_date_from,
            unit_count=claim_line.unit_count,
            procedure=claim_line.procedure,
            adjustments=adjustments if adjustments else None,
            remarks=remarks,
            remark_codes=[r.code for r in remarks] if remarks else None,
        )

    def _generate_denial_adjustments(self, charge_amount: float) -> list[Adjustment]:
        """Generate adjustments for a denied claim."""
        # Select a denial reason
        denial_carcs = [
            carc
            for carc in BASIC_CARC_CODES
            if carc.code in ["16", "29", "50", "96", "97"]
        ]

        selected_carc = random.choice(denial_carcs)

        adjustments = [
            Adjustment(
                group=selected_carc.group,
                reason=Code(
                    sub_type="CARC",
                    code=selected_carc.code,
                    desc=selected_carc.description,
                ),
                amount=float(charge_amount),
            )
        ]

        return adjustments

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
