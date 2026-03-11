"""Generate fake but realistic EDI 835 and 837 messages."""

# ruff: noqa: S311
import random
from datetime import datetime, timedelta
from pathlib import Path
from typing import Annotated, Literal, TextIO

from cyclopts import Parameter
from cyclopts.validators import Number

from synthetic_edi_gen._base import EDIBaseModel

from .claim_generator import ClaimGenerator
from .openar_generator import (
    OpenARGenerator,
    write_openar_csv,
    write_openar_xlsx,
)
from .payment_generator import PaymentGenerator

# Real-world distribution of PCNs per HAR, scaled 10x for multi-PCN groups.
# Source: production data analysis (~1M HARs).
# Weights represent relative frequency; single-PCN dominates.
_MULTI_PCN_DISTRIBUTION: list[tuple[int, int]] = [
    (1, 1_030_027),
    (2, 71_060),
    (3, 7_970),
    (4, 3_580),
    (5, 2_520),
    (6, 1_710),
    (7, 1_290),
    (8, 940),
    (9, 760),
    (10, 580),
    (11, 470),
    (12, 340),
    (13, 140),
    (14, 80),
    (15, 60),
    (17, 40),
    (18, 30),
    (19, 20),
    (20, 20),
    (31, 10),
]

_PCN_COUNTS = [n for n, _ in _MULTI_PCN_DISTRIBUTION]
_PCN_WEIGHTS = [w for _, w in _MULTI_PCN_DISTRIBUTION]


def _plan_har_groups(total_claims: int) -> list[int]:
    """Decide how many PCNs each HAR group should have.

    Keeps sampling HAR group sizes from the distribution until we've
    allocated all requested claims.
    """
    groups: list[int] = []
    remaining = total_claims
    while remaining > 0:
        size = random.choices(_PCN_COUNTS, weights=_PCN_WEIGHTS, k=1)[0]
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


def generate(
    count: int,
    output_dir: Path = _DEFAULT_OUTPUT_DIR,
    match_rate: Annotated[float, Parameter(validator=Number(gte=0.0, lte=1.0))] = 0.95,
    unmatched_ar_rate: Annotated[
        float, Parameter(validator=Number(gte=0.0, lte=1.0))
    ] = 0.05,
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
    print(f"Output directory: {output_dir}")
    if seed is not None:
        print(f"Random seed: {seed}")

    # Create output directory
    output_dir.mkdir(parents=True, exist_ok=True)

    # Create generators
    claim_gen = ClaimGenerator(seed=seed)
    payment_gen = PaymentGenerator(seed=seed)
    openar_gen = OpenARGenerator(seed=seed)

    # Plan HAR groups
    har_groups = _plan_har_groups(count)
    multi_pcn_hars = sum(1 for g in har_groups if g > 1)
    print(f"  HAR groups: {len(har_groups):,} ({multi_pcn_hars:,} with multiple PCNs)")

    ar_rows: list[dict] = []

    # Create split-aware writers for claims and payments
    claims_writer = SplitFileWriter(
        output_dir, "837_claims", ".jsonl", max_records=claims_per_file
    )
    payments_writer = SplitFileWriter(
        output_dir, "835_payments", ".jsonl", max_records=payments_per_file
    )

    try:
        for group_size in har_groups:
            # One shared patient context per HAR group
            ctx = claim_gen.generate_patient_context()

            # Shared AR identifiers for the group
            har_id = openar_gen._generate_hospital_account_id()
            mrn = openar_gen._generate_mrn()

            for _claim_idx in range(group_size):
                # PB: same date; HB (group_size > 3): spread across 1-14 days
                if group_size <= 3:
                    svc_date = ctx.base_service_date
                else:
                    svc_date = ctx.base_service_date + timedelta(
                        days=random.randint(0, min(14, group_size))
                    )

                claim = claim_gen.generate_claim(ctx=ctx, service_date=svc_date)
                claims_writer.write(claim)

                # Independent payment per claim
                payment = None
                if random.random() < match_rate:
                    payment = payment_gen.generate_payment_for_claim(claim)
                    payments_writer.write(payment)

                # AR rows with shared HAR ID and MRN
                claim_dict = claim.model_dump(by_alias=True, mode="json")
                payment_dict = (
                    payment.model_dump(by_alias=True, mode="json") if payment else None
                )
                claim_ar_rows = openar_gen.generate_ar_rows_for_claim(
                    claim_dict,
                    payment_dict,
                    hospital_account_id=har_id,
                    mrn=mrn,
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
    openar_file = output_dir / f"openar.{ar_ext}"
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
    for f in claims_writer.files_created:
        print(f"    {f}")
    print(f"  Payments written: {payments_written:,} → {n_payment_files} file(s)")
    for f in payments_writer.files_created:
        print(f"    {f}")
    print(f"  Match rate achieved: {payments_written / claims_written:.1%}")
    print(f"  OpenAR rows written: {len(ar_rows):,} → {openar_file}")
    print(f"    (including {unmatched_count:,} unmatched rows)")
    print(f"  HAR groups: {len(har_groups):,} ({multi_pcn_hars:,} multi-PCN)")
