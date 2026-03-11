"""
Generate OpenAR xlsx files correlated with 835/837 EDI data.

The OpenAR format is a healthcare accounts receivable report format that tracks
outstanding insurance balances at the service line level.
"""

# ruff: noqa: S311
import random
import string
from datetime import date, timedelta
from typing import Any

import pandas as pd

from .basic_codes import BASIC_CPT_CODES, DENIAL_CARC_CODES
from .helpers import generate_person_name, generate_service_date
from .reference_data import PLACE_OF_SERVICE, PlaceOfService

# Financial class mappings from payer info
FINANCIAL_CLASS_MAP = {
    "MB": "Medicare",
    "MC": "Medicaid",
    "BL": "Blue Cross Blue Shield",
    "CI": "Commercial",
    "HM": "Managed Care",
}

# Claim form types
CLAIM_FORM_TYPES = ["CMS Claim", "UB Claim"]

# Claim statuses
CLAIM_STATUSES = ["Accepted", "Rejected", "Notification", "Unmapped Code"]

# Age bucket definitions (in days)
AGE_BUCKETS = [
    (0, 31, "Less than 31 days"),
    (31, 61, "31 days or more and less than 61 days"),
    (61, 91, "61 days or more and less than 91 days"),
    (91, 121, "91 days or more and less than 121 days"),
    (121, 151, "121 days or more and less than 151 days"),
    (151, 181, "151 days or more and less than 181 days"),
    (181, 211, "181 days or more and less than 211 days"),
    (211, 241, "211 days or more and less than 241 days"),
    (241, 365, "241 days or more and less than 365 days"),
    (365, 9999, "365 days or more"),
]

# Departments (sample)
DEPARTMENTS = [
    "Family Medicine",
    "Internal Medicine",
    "Cardiology",
    "Radiology",
    "Laboratory",
    "Emergency Department",
    "Orthopedics",
    "Pediatrics",
]


class OpenARGenerator:
    """Generator for OpenAR xlsx files from 835/837 EDI data."""

    def __init__(self, seed: int | None = None):
        """Initialize the generator with optional seed for reproducibility."""
        if seed is not None:
            random.seed(seed)
        self._transaction_id_counter = 230000000

    def generate_ar_rows_for_claim(
        self,
        claim: dict[str, Any],
        payment: dict[str, Any] | None = None,
        hospital_account_id: str | None = None,
        mrn: str | None = None,
    ) -> list[dict[str, Any]]:
        """
        Generate OpenAR rows for a claim and its optional payment.

        Each service line in the claim generates one AR row.
        The outstanding amount is calculated based on the payment data if present.

        Args:
            claim: The 837 claim data
            payment: The optional 835 payment data (if claim was paid)
            hospital_account_id: Shared HAR ID for multi-PCN groups. Generated if None.
            mrn: Shared MRN for multi-PCN groups. Generated if None.

        Returns:
            List of AR row dictionaries
        """
        rows = []

        # Extract claim-level info
        pcn = claim["patientControlNumber"]
        service_date = date.fromisoformat(claim["serviceDateFrom"])
        payer_info = claim["subscriber"]["payer"]
        billing_provider = claim.get("billingProvider", {})
        claim_filing_code = claim["subscriber"].get("claimFilingIndicatorCode", "CI")

        # Calculate age bucket
        age_days = (date.today() - service_date).days
        age_bucket = self._get_age_bucket(age_days)

        # Use shared IDs if provided, otherwise generate new ones
        mrn = mrn or self._generate_mrn()
        hospital_account_id = (
            hospital_account_id or self._generate_hospital_account_id()
        )

        # Get financial class
        financial_class = FINANCIAL_CLASS_MAP.get(claim_filing_code, "Commercial")

        # Get billing and referring provider names
        billing_provider_name = billing_provider.get("lastNameOrOrgName", "UNKNOWN")
        providers = claim.get("providers", [])
        referring_provider_name = ""
        if providers:
            provider = providers[0]
            if provider.get("firstName"):
                last = provider.get(
                    "lastName",
                    provider.get("lastNameOrOrgName", ""),
                )
                first = provider.get("firstName", "")
                referring_provider_name = f"{last}, {first}"
            else:
                referring_provider_name = provider.get(
                    "lastNameOrOrgName", provider.get("lastName", "")
                )

        # Get place of service info
        facility_code = claim.get("facilityCode", {})
        pos_code = facility_code.get("code", "11")
        department = random.choice(DEPARTMENTS)
        place_of_service = f"{department} - POS {pos_code}"

        # Build payment lookup by service line
        payment_by_line: dict[str, dict[str, Any]] = {}
        if payment:
            for pmt_line in payment.get("serviceLines", []):
                line_id = pmt_line.get("sourceLineId")
                if line_id:
                    payment_by_line[line_id] = pmt_line

        # Denied from 835 (status 2/DENIED) → zero paid, full outstanding
        claim_denied = bool(
            payment
            and (
                payment.get("claimStatusCode") == "2"
                or payment.get("claimStatus") == "DENIED"
            )
        )

        # Determine if payment has been posted (cash received and reconciled)
        # This affects the insurance outstanding amount:
        # - 835 received but not posted: outstanding = what insurance said they'd pay
        # - 835 received and posted: outstanding = $0 (insurance portion complete)
        # Probability of payment being posted increases with claim age
        payment_posted = False
        if payment:
            # Older claims more likely to have payment posted
            # 0-30 days: 30% posted, 31-60 days: 60% posted, 61+ days: 85% posted
            if age_days < 31:
                payment_posted = random.random() < 0.30
            elif age_days < 61:
                payment_posted = random.random() < 0.60
            else:
                payment_posted = random.random() < 0.85

        # Determine claim status (Rejected for denied; otherwise from payment)
        claim_status: str = "Rejected" if claim_denied else "Accepted"
        if not claim_denied and payment:
            if payment.get("claimStatusCode") == "2":
                claim_status = random.choice(["Rejected", "Unmapped Code"])
            elif random.random() < 0.05:
                claim_status = "Notification"

        # Determine crossover status (only for Medicare)
        crossover_status = None
        if financial_class == "Medicare" and random.random() < 0.15:
            crossover_status = random.choice(
                [
                    "Crossover claim created",
                    "Crossover payment received and coverage is found",
                    "Crossover payment received and coverage is not found",
                ]
            )

        # Generate row for each service line
        for service_line in claim.get("serviceLines", []):
            row = self._generate_ar_row(
                pcn=pcn,
                service_date=service_date,
                service_line=service_line,
                payer_info=payer_info,
                billing_provider_name=billing_provider_name,
                referring_provider_name=referring_provider_name,
                financial_class=financial_class,
                age_bucket=age_bucket,
                mrn=mrn,
                hospital_account_id=hospital_account_id,
                place_of_service=place_of_service,
                department=department,
                claim_status=claim_status,
                crossover_status=crossover_status,
                payment_line=payment_by_line.get(service_line.get("sourceLineId")),
                payment_posted=payment_posted,
                claim_denied=claim_denied,
            )
            rows.append(row)

        return rows

    def generate_unmatched_ar_rows(self, count: int) -> list[dict[str, Any]]:
        """
        Generate AR rows that don't match any 837/835 claim.

        These represent claims that exist in the AR system but don't have
        corresponding EDI data (e.g., older claims, manually entered claims).

        Args:
            count: Number of unmatched rows to generate

        Returns:
            List of unmatched AR row dictionaries
        """
        rows = []

        for _ in range(count):
            # Generate random claim-like data
            service_date = generate_service_date(days_ago_min=30, days_ago_max=400)
            age_days = (date.today() - service_date).days
            age_bucket = self._get_age_bucket(age_days)

            # Generate unique PCN for unmatched row
            pcn = "U" + "".join(random.choices(string.digits, k=11))

            # Random financial class
            financial_class = random.choice(list(FINANCIAL_CLASS_MAP.values()))

            # Generate payer info
            payer_name = random.choice(
                [
                    "MEDICARE",
                    "MEDICAID",
                    "BCBS",
                    "AETNA",
                    "CIGNA",
                    "UNITED HEALTHCARE",
                    "HUMANA",
                ]
            )
            plan_name = f"{payer_name} PPO" if random.random() > 0.3 else payer_name

            # Generate provider names
            first, last, _ = generate_person_name("UNKNOWN")
            billing_provider_name = f"{last}, {first}"

            first2, last2, _ = generate_person_name("UNKNOWN")
            referring_provider_name = f"{last2}, {first2}"

            # Generate IDs
            mrn = self._generate_mrn()
            hospital_account_id = self._generate_hospital_account_id()

            # Random procedure
            cpt_data = random.choice(BASIC_CPT_CODES)
            charge_amount = round(
                random.uniform(cpt_data.min_cost, cpt_data.max_cost), 2
            )

            # Outstanding amount (full charge for unmatched)
            outstanding_amount = charge_amount

            # Random place of service
            pos: PlaceOfService = random.choice(PLACE_OF_SERVICE)
            department = random.choice(DEPARTMENTS)
            place_of_service = f"{department} - POS {pos.code}"

            # Build modifiers string
            modifiers = None
            if random.random() < 0.2:
                modifiers = random.choice(["25", "59", "76", "LT", "RT", "26"])

            row = {
                "Slices by Service Date Age (days)": age_bucket,
                "Post Date": service_date,
                "Professional Transaction ID": self._next_transaction_id(),
                "MRN": mrn,
                "Current Financial Class": financial_class,
                "Current Plan": plan_name,
                "Current Payer": payer_name,
                "Billing Provider": billing_provider_name,
                "Referring Provider": referring_provider_name,
                "Service Date": service_date,
                "Procedure Code": cpt_data.code,
                "Modifiers (All)": modifiers,
                "Transaction Type": "Charge",
                "Posted Amount ($)": float(round(charge_amount, 2)),
                "Claim Status": random.choice(CLAIM_STATUSES),
                "Crossover Status": None,
                "Claim Form Type": random.choice(CLAIM_FORM_TYPES),
                "Place of Service": place_of_service,
                "Department": department,
                "Hospital Account ID": hospital_account_id,
                "Invoice Number": pcn,
                "Insurance Outstanding Amount ($)": float(round(outstanding_amount, 2)),
            }
            rows.append(row)

        return rows

    def _generate_ar_row(
        self,
        pcn: str,
        service_date: date,
        service_line: dict[str, Any],
        payer_info: dict[str, Any],
        billing_provider_name: str,
        referring_provider_name: str,
        financial_class: str,
        age_bucket: str,
        mrn: str,
        hospital_account_id: str,
        place_of_service: str,
        department: str,
        claim_status: str,
        crossover_status: str | None,
        payment_line: dict[str, Any] | None,
        payment_posted: bool = True,
        claim_denied: bool = False,
    ) -> dict[str, Any]:
        """Generate a single AR row from service line data.

        The Insurance Outstanding Amount represents what insurance still owes:
        - If no 835 received: full charge amount is outstanding
        - If 835 denied (claim status or denial CARC): zero paid, full outstanding
        - If 835 received but payment not posted: allowed amount is outstanding
        - If 835 received and payment posted: $0 outstanding (insurance has paid)
        """
        procedure = service_line.get("procedure", {})
        procedure_code = procedure.get("code", "")
        charge_amount = float(service_line.get("chargeAmount", 0))

        # Denied by 835 (claim or line with denial CARC): zero paid, full outstanding
        line_denied = payment_line and self._payment_line_is_denial(payment_line)
        if claim_denied or line_denied:
            outstanding_amount = charge_amount
        elif payment_line:
            paid_amount = float(payment_line.get("paidAmount", 0))
            adjustments = payment_line.get("adjustments", [])

            # Calculate contractual adjustment (CARC 45 - charge vs allowed)
            contractual_adj = 0.0
            for adj in adjustments:
                if adj.get("group") == "CONTRACTUAL_OBLIGATION":
                    contractual_adj += float(adj.get("amount", 0))

            if payment_posted:
                # Payment has been received and posted - insurance is done
                outstanding_amount = 0.0
            else:
                # 835 received but payment not yet posted
                outstanding_amount = paid_amount
                if outstanding_amount < 0:
                    outstanding_amount = 0.0
        else:
            # No 835/payment record - full charge is outstanding
            outstanding_amount = charge_amount

        # Build modifiers string
        modifiers = None
        if procedure.get("modifiers"):
            modifiers = ", ".join(m.get("code", "") for m in procedure["modifiers"])

        # Post date is typically service date or shortly after
        post_date = service_date + timedelta(days=random.randint(0, 3))

        payer_name = payer_info.get("lastNameOrOrgName", "UNKNOWN")
        plan_name = payer_name  # Simplify for now

        return {
            "Slices by Service Date Age (days)": age_bucket,
            "Post Date": post_date,
            "Professional Transaction ID": self._next_transaction_id(),
            "MRN": mrn,
            "Current Financial Class": financial_class,
            "Current Plan": plan_name,
            "Current Payer": payer_name,
            "Billing Provider": billing_provider_name,
            "Referring Provider": referring_provider_name,
            "Service Date": service_date,
            "Procedure Code": procedure_code,
            "Modifiers (All)": modifiers,
            "Transaction Type": "Charge",
            "Posted Amount ($)": float(round(charge_amount, 2)),
            "Claim Status": claim_status,
            "Crossover Status": crossover_status,
            "Claim Form Type": random.choice(CLAIM_FORM_TYPES),
            "Place of Service": place_of_service,
            "Department": department,
            "Hospital Account ID": hospital_account_id,
            "Invoice Number": pcn,
            "Insurance Outstanding Amount ($)": float(round(outstanding_amount, 2)),
        }

    def _payment_line_is_denial(self, payment_line: dict[str, Any]) -> bool:
        """True if 835 line has zero paid and denial CARC (e.g. 16, 29, 50, 96, 97)."""
        if float(payment_line.get("paidAmount", 0)) != 0:
            return False
        for adj in payment_line.get("adjustments", []):
            reason = adj.get("reason") or {}
            code = reason.get("code") if isinstance(reason, dict) else None
            if code and code in DENIAL_CARC_CODES:
                return True
        return False

    def _get_age_bucket(self, age_days: int) -> str:
        """Get the age bucket label for a given number of days."""
        for min_days, max_days, label in AGE_BUCKETS:
            if min_days <= age_days < max_days:
                return label
        return AGE_BUCKETS[-1][2]  # Default to last bucket

    def _generate_mrn(self) -> str:
        """Generate a Medical Record Number."""
        return "".join(random.choices(string.digits, k=9))

    def _generate_hospital_account_id(self) -> str:
        """Generate a Hospital Account ID."""
        return "".join(random.choices(string.digits, k=11))

    def _next_transaction_id(self) -> int:
        """Generate the next transaction ID."""
        self._transaction_id_counter += 1
        return self._transaction_id_counter


MAX_EXCEL_ROWS = 1048576  # Excel's maximum row limit


def write_openar_xlsx(
    ar_rows: list[dict[str, Any]],
    output_path: str,
    session_id: str = "15216648",
) -> None:
    """
    Write OpenAR data to an xlsx file with proper formatting.

    The output format matches the OpenAR export format:
    - Rows 0-7: Session metadata (Session Title, Session ID, Data Model, etc.)
    - Row 8: Empty row
    - Row 9: Column headers
    - Row 10+: Data rows

    For very large datasets (>1M rows), the data is split across multiple sheets.

    Args:
        ar_rows: List of AR row dictionaries
        output_path: Path to output xlsx file
        session_id: Session ID for the header
    """
    # Create DataFrame from rows
    df = pd.DataFrame(ar_rows)

    # Column order (matching example file)
    columns = [
        "Slices by Service Date Age (days)",
        "Post Date",
        "Professional Transaction ID",
        "MRN",
        "Current Financial Class",
        "Current Plan",
        "Current Payer",
        "Billing Provider",
        "Referring Provider",
        "Service Date",
        "Procedure Code",
        "Modifiers (All)",
        "Transaction Type",
        "Posted Amount ($)",
        "Claim Status",
        "Crossover Status",
        "Claim Form Type",
        "Place of Service",
        "Department",
        "Hospital Account ID",
        "Invoice Number",
        "Insurance Outstanding Amount ($)",
    ]

    # Reorder columns
    df = df[columns]

    # Create header metadata rows (rows 0-7)
    header_data = [
        [
            "Session Title",
            "Total Insurance Outstanding Amount by Service Date Age Range",
        ]
        + [None] * 20,
        ["Session ID", session_id] + [None] * 20,
        ["Data Model", "Open AR (PB)"] + [None] * 20,
        ["Population Base", "All Open AR (PB)"] + [None] * 20,
        [
            "Population Criteria Filters: These criteria are a summary"
            " and do not fully reflect the content of the exported session.",
            "Claim Form Type: Has Value",
            "Transaction Type: Charge",
            "Insurance Outstanding Amount: ≥ $1",
        ]
        + [None] * 18,
        ["Session Date Range", "All Time"] + [None] * 20,
        ["Export User", "System Generated"] + [None] * 20,
        ["Date of Export", date.today()] + [None] * 20,
        [None] * 22,  # Empty row (row 8)
        columns,  # Column headers (row 9)
    ]

    # Convert data rows to list format
    data_rows = df.values.tolist()

    # Calculate rows per sheet (accounting for header rows)
    header_row_count = len(header_data)
    max_data_rows_per_sheet = MAX_EXCEL_ROWS - header_row_count

    # Write to Excel, splitting across multiple sheets if needed
    with pd.ExcelWriter(output_path, engine="openpyxl") as writer:
        if len(data_rows) <= max_data_rows_per_sheet:
            # Single sheet - all data fits
            all_rows = header_data + data_rows
            final_df = pd.DataFrame(all_rows)
            final_df.to_excel(writer, index=False, header=False, sheet_name="Sheet1")
        else:
            # Multiple sheets needed
            sheet_num = 1
            for i in range(0, len(data_rows), max_data_rows_per_sheet):
                chunk = data_rows[i : i + max_data_rows_per_sheet]
                all_rows = header_data + chunk
                final_df = pd.DataFrame(all_rows)
                sheet_name = f"Sheet{sheet_num}"
                final_df.to_excel(
                    writer, index=False, header=False, sheet_name=sheet_name
                )
                sheet_num += 1
