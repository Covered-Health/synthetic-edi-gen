"""Generate fake but realistic EDI 835 and 837 messages."""

# ruff: noqa: S311
import random
from copy import deepcopy
from dataclasses import replace
from datetime import datetime, timedelta
from pathlib import Path
from typing import Annotated, Any, Literal, TextIO

from cyclopts import Parameter
from cyclopts.validators import Number

from synthetic_edi_gen._base import EDIBaseModel
from synthetic_edi_gen.edi_models import Payment

from .claim_generator import (
    ClaimGenerator,
    PatientContext,
)
from .openar_generator import (
    OpenARGenerator,
    write_openar_csv,
    write_openar_xlsx,
)
from .payment_generator import PaymentGenerator
from .stats import sample_count, sample_distribution


def _plan_har_groups(total_claims: int) -> list[int]:
    """Allocate claims to synthetic patients using the observed histogram."""
    groups: list[int] = []
    remaining = total_claims
    while remaining > 0:
        size = sample_count("claims_per_patient", minimum=1)
        size = min(size, remaining)
        groups.append(size)
        remaining -= size
    return groups


def write_jsonl(file_handle: TextIO, record: EDIBaseModel) -> None:
    """Write a single record as a JSON line."""
    file_handle.write(record.model_dump_json(by_alias=True))
    file_handle.write("\n")


class SplitFileWriter:
    """Writes JSONL records, rotating to a new file every `max_records` records.

    When max_records is 0 or None, all records go to a single file (no splitting).
    """

    def __init__(
        self,
        output_dir: Path,
        base_name: str,
        extension: str = ".jsonl",
        max_records: int = 0,
    ):
        self._output_dir = output_dir
        self._base_name = base_name
        self._extension = extension
        self._max_records = max_records
        self._current_file: TextIO | None = None
        self._records_in_current_file = 0
        self._file_index = 1
        self._total_written = 0
        self._files_created: list[Path] = []

    def _current_path(self) -> Path:
        if not self._max_records:
            return self._output_dir / f"{self._base_name}{self._extension}"
        return (
            self._output_dir
            / f"{self._base_name}_{self._file_index:03d}{self._extension}"
        )

    def _open_next_file(self) -> None:
        if self._current_file is not None:
            self._current_file.close()
        path = self._current_path()
        self._current_file = open(path, "w")  # noqa: SIM115
        self._files_created.append(path)
        self._records_in_current_file = 0

    def write(self, record: EDIBaseModel) -> None:
        if self._current_file is None:
            self._open_next_file()
        elif self._max_records and self._records_in_current_file >= self._max_records:
            self._file_index += 1
            self._open_next_file()

        if self._current_file is None:  # pragma: no cover
            msg = "File handle unexpectedly None"
            raise RuntimeError(msg)
        write_jsonl(self._current_file, record)
        self._records_in_current_file += 1
        self._total_written += 1

    def flush(self) -> None:
        if self._current_file is not None:
            self._current_file.flush()

    def close(self) -> None:
        if self._current_file is not None:
            self._current_file.close()
            self._current_file = None

    @property
    def total_written(self) -> int:
        return self._total_written

    @property
    def files_created(self) -> list[Path]:
        return list(self._files_created)


_DEFAULT_OUTPUT_DIR = Path("./edi_output")
_CLEARINGHOUSE_REJECTION_RATE = 0.05
_FIXABLE_DENIAL_CARC_CODES = {"16"}
_FIXABLE_DENIAL_RARC_CODES = {"N17", "N362"}


def _payment_has_fixable_denial(payment: Payment) -> bool:
    if payment.claim_status != "DENIED":
        return False
    for line in payment.service_lines or []:
        for adjustment in line.adjustments or []:
            if (
                adjustment.reason
                and adjustment.reason.code in _FIXABLE_DENIAL_CARC_CODES
            ):
                return True
        if set(line.remark_codes or []) & _FIXABLE_DENIAL_RARC_CODES:
            return True
    return False


def _merge_payment_dicts(
    primary: dict[str, Any] | None, secondary: dict[str, Any] | None
) -> dict[str, Any] | None:
    if not primary or not secondary:
        return primary or secondary

    merged = deepcopy(primary)
    merged["paymentAmount"] = round(
        float(primary.get("paymentAmount", 0))
        + float(secondary.get("paymentAmount", 0)),
        2,
    )
    if merged["paymentAmount"] > 0:
        merged["claimStatusCode"] = "1"
        merged["claimStatus"] = "PRIMARY"

    secondary_by_line = {
        line.get("sourceLineId"): line for line in secondary.get("serviceLines", [])
    }
    for line in merged.get("serviceLines", []):
        secondary_line = secondary_by_line.get(line.get("sourceLineId"))
        if not secondary_line:
            continue
        line["paidAmount"] = round(
            float(line.get("paidAmount", 0))
            + float(secondary_line.get("paidAmount", 0)),
            2,
        )
        line["adjustments"] = (line.get("adjustments") or []) + (
            secondary_line.get("adjustments") or []
        )

    return merged


def generate(
    count: int,
    output_dir: Path = _DEFAULT_OUTPUT_DIR,
    match_rate: Annotated[float, Parameter(validator=Number(gte=0.0, lte=1.0))] = 0.95,
    unmatched_ar_rate: Annotated[
        float, Parameter(validator=Number(gte=0.0, lte=1.0))
    ] = 0.05,
    revised_claim_rate: Annotated[
        float, Parameter(validator=Number(gte=0.0, lte=1.0))
    ] = 0.01,
    secondary_payer_payment_rate: Annotated[
        float, Parameter(validator=Number(gte=0.0, lte=1.0))
    ] = 0.10,
    institutional_claim_rate: Annotated[
        float | None, Parameter(validator=Number(gte=0.0, lte=1.0))
    ] = None,
    provider_count: Annotated[int | None, Parameter(validator=Number(gte=1))] = None,
    seed: int | None = None,
    batch_size: int = 10000,
    claims_per_file: int = 10000,
    payments_per_file: int = 20,
    ar_format: Literal["csv", "xlsx"] = "csv",
    export_datetime: datetime | None = None,
) -> None:
    """Generate fake but realistic EDI 835 and 837 messages.

    Claims are grouped into HAR (Hospital Account Record) groups that mirror
    real-world distributions.  Claims in the same group share patient
    demographics, subscriber/payer, billing provider, MRN, and Hospital
    Account ID — each with its own unique PCN, service lines, and payment.

    Args:
        count: Number of claims to generate
        output_dir: Output directory for generated files
        match_rate: Percentage of claims with matching payments, 0.0-1.0
        unmatched_ar_rate: Percentage of additional unmatched AR rows, 0.0-1.0
        revised_claim_rate: Percentage of denied/fixable claims emitted again as
            replacement claims
        secondary_payer_payment_rate: Percentage of matched claims with a secondary
            payer 835
        institutional_claim_rate: Percentage of 837 claims emitted as 837I
        provider_count: Rendering-provider roster size; scales with count by default
        seed: Random seed for reproducibility
        batch_size: Batch size for progress reporting and disk flushing
        claims_per_file: Max 837 claims per file (0 = single file)
        payments_per_file: Max 835 payments per file (0 = single file)
        ar_format: Output format for OpenAR data ('csv' or 'xlsx')
        export_datetime: Timestamp for AR header (default: current local time)
    """
    if seed is not None:
        random.seed(seed)

    print(f"Generating {count:,} EDI claim/payment pairs...")
    print(f"Match rate: {match_rate:.0%}")
    print(f"Unmatched AR rate: {unmatched_ar_rate:.0%}")
    print(f"Revised claim rate: {revised_claim_rate:.0%}")
    print(f"Secondary payer 835 rate: {secondary_payer_payment_rate:.0%}")
    if institutional_claim_rate is None:
        print("837I claim rate: empirical")
    else:
        print(f"837I claim rate: {institutional_claim_rate:.0%}")
    print(f"Output directory: {output_dir}")
    if seed is not None:
        print(f"Random seed: {seed}")

    # Create output directory
    output_dir.mkdir(parents=True, exist_ok=True)

    # Create generators.  A small drug-defect rate ensures some drug lines
    # carry defects (missing NDC, quantity mismatch) so downstream NDC edits
    # in the batch analyzer can be exercised end-to-end.
    claim_gen = ClaimGenerator(seed=seed, drug_defect_rate=0.10)
    claim_gen.prepare_provider_roster(count, provider_count)
    payment_gen = PaymentGenerator(seed=seed)
    openar_gen = OpenARGenerator(seed=seed)

    # Plan HAR groups
    har_groups = _plan_har_groups(count)
    multi_pcn_hars = sum(1 for g in har_groups if g > 1)
    print(f"  HAR groups: {len(har_groups):,} ({multi_pcn_hars:,} with multiple PCNs)")

    ar_rows: list[dict] = []
    claim_type_counts = {"PROF": 0, "INST": 0}
    primary_payments_written = 0
    clearinghouse_rejections_written = 0
    revised_claims_written = 0
    secondary_payments_written = 0

    # Create split-aware writers for claims and payments
    claims_writer = SplitFileWriter(
        output_dir, "837_claims", ".jsonl", max_records=claims_per_file
    )
    payments_writer = SplitFileWriter(
        output_dir, "835_payments", ".jsonl", max_records=payments_per_file
    )

    def _emit_har_group(
        ctx: PatientContext,
        group_size: int,
        forced_cpt_codes: list[str] | None = None,
        forced_icd10_codes: list[str] | None = None,
    ) -> None:
        """Generate and write all claims for one HAR group."""
        nonlocal primary_payments_written, clearinghouse_rejections_written
        nonlocal revised_claims_written
        nonlocal secondary_payments_written
        har_id = openar_gen._generate_hospital_account_id()

        for _claim_idx in range(group_size):
            # PB: same date; HB (group_size > 3): spread across 1-14 days
            if group_size <= 3:
                svc_date = ctx.base_service_date
            else:
                svc_date = ctx.base_service_date + timedelta(
                    days=random.randint(0, min(14, group_size))
                )

            is_institutional = (
                sample_distribution("claim_type") == "INST"
                if institutional_claim_rate is None
                else random.random() < institutional_claim_rate
            )
            if is_institutional:
                claim = claim_gen.generate_institutional_claim(
                    ctx=ctx,
                    service_date=svc_date,
                    forced_cpt_codes=forced_cpt_codes,
                    forced_icd10_codes=forced_icd10_codes,
                )
            else:
                claim = claim_gen.generate_claim(
                    ctx=ctx,
                    service_date=svc_date,
                    forced_cpt_codes=forced_cpt_codes,
                    forced_icd10_codes=forced_icd10_codes,
                )
            claim_type = claim.transaction.transaction_type or "PROF"
            claim_type_counts[claim_type] = claim_type_counts.get(claim_type, 0) + 1
            claims_writer.write(claim)

            # Independent payment per claim
            payment = None
            secondary_payment = None
            if random.random() < match_rate:
                has_secondary = random.random() < secondary_payer_payment_rate
                clearinghouse_rejected = (
                    not has_secondary
                    and random.random() < _CLEARINGHOUSE_REJECTION_RATE
                )
                forwarded = has_secondary and random.random() < 0.5
                payment = (
                    payment_gen.generate_rejection_for_claim(claim)
                    if clearinghouse_rejected
                    else payment_gen.generate_payment_for_claim(
                        claim, forwarded=forwarded
                    )
                )
                payments_writer.write(payment)
                primary_payments_written += 1
                clearinghouse_rejections_written += int(clearinghouse_rejected)
                if has_secondary:
                    secondary_payment = (
                        payment_gen.generate_secondary_payment_for_claim(claim, payment)
                    )
                    payments_writer.write(secondary_payment)
                    secondary_payments_written += 1

                if (
                    not clearinghouse_rejected
                    and random.random() < revised_claim_rate
                    and _payment_has_fixable_denial(payment)
                ):
                    revised_claim = claim_gen.generate_revised_claim(claim)
                    claims_writer.write(revised_claim)
                    claim_type_counts[claim_type] = (
                        claim_type_counts.get(claim_type, 0) + 1
                    )
                    revised_claims_written += 1

            # AR rows with shared HAR ID and MRN
            claim_dict = claim.model_dump(by_alias=True, mode="json")
            payment_dict = (
                payment.model_dump(by_alias=True, mode="json") if payment else None
            )
            secondary_payment_dict = (
                secondary_payment.model_dump(by_alias=True, mode="json")
                if secondary_payment
                else None
            )
            payment_dict = _merge_payment_dicts(payment_dict, secondary_payment_dict)
            claim_ar_rows = openar_gen.generate_ar_rows_for_claim(
                claim_dict,
                payment_dict,
                hospital_account_id=har_id,
                mrn=ctx.mrn,
            )
            ar_rows.extend(claim_ar_rows)

            # Progress indicator
            if claims_writer.total_written % batch_size == 0:
                pct = claims_writer.total_written / count * 100
                print(
                    f"  Generated {claims_writer.total_written:,}"
                    f" / {count:,} claims ({pct:.1f}%)"
                )
                claims_writer.flush()
                payments_writer.flush()

    try:
        group_idx = 0
        while group_idx < len(har_groups):
            group_size = har_groups[group_idx]
            mrn = openar_gen._generate_mrn()
            ctx = replace(claim_gen.generate_patient_context(), mrn=mrn)
            _emit_har_group(ctx, group_size)
            group_idx += 1
    finally:
        claims_writer.close()
        payments_writer.close()

    # Generate unmatched AR rows
    unmatched_count = int(count * unmatched_ar_rate)
    if unmatched_count > 0:
        print(f"  Generating {unmatched_count:,} unmatched AR rows...")
        unmatched_rows = openar_gen.generate_unmatched_ar_rows(unmatched_count)
        ar_rows.extend(unmatched_rows)

    # Write OpenAR file
    ar_ext = "csv" if ar_format == "csv" else "xlsx"
    if export_datetime is None:
        export_datetime = datetime.now()  # noqa: DTZ005
    openar_file = output_dir / f"openar_{export_datetime:%Y%m%d}.{ar_ext}"
    print(f"  Writing OpenAR {ar_format} file...")
    if ar_format == "csv":
        write_openar_csv(ar_rows, str(openar_file), export_datetime=export_datetime)
    else:
        write_openar_xlsx(ar_rows, str(openar_file), export_datetime=export_datetime)

    claims_written = claims_writer.total_written
    payments_written = payments_writer.total_written

    n_claim_files = len(claims_writer.files_created)
    n_payment_files = len(payments_writer.files_created)

    print("\n✓ Generation complete!")
    print(f"  Claims written: {claims_written:,} → {n_claim_files} file(s)")
    print(
        f"  Claim types: {claim_type_counts.get('PROF', 0):,} 837P,"
        f" {claim_type_counts.get('INST', 0):,} 837I"
    )
    for f in claims_writer.files_created:
        print(f"    {f}")
    print(f"  Payments written: {payments_written:,} → {n_payment_files} file(s)")
    print(f"  Primary payments written: {primary_payments_written:,}")
    print(f"  Clearinghouse rejections: {clearinghouse_rejections_written:,}")
    print(f"  Revised claims written: {revised_claims_written:,}")
    print(f"  Secondary payer payments written: {secondary_payments_written:,}")
    for f in payments_writer.files_created:
        print(f"    {f}")
    print(f"  Match rate achieved: {primary_payments_written / count:.1%}")
    print(f"  OpenAR rows written: {len(ar_rows):,} → {openar_file}")
    print(f"    (including {unmatched_count:,} unmatched rows)")
    print(f"  HAR groups: {len(har_groups):,} ({multi_pcn_hars:,} multi-PCN)")
