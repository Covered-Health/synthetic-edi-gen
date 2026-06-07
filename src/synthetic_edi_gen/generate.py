"""Generate fake but realistic EDI 835 and 837 messages."""

# ruff: noqa: S311
import random
from dataclasses import dataclass, replace
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Annotated, Literal, TextIO

from cyclopts import Parameter
from cyclopts.validators import Number

from synthetic_edi_gen._base import EDIBaseModel

from .claim_generator import ClaimGenerator, PatientContext
from .helpers import generate_service_date
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

# Fraction of HAR groups that reuse an existing patient (same MRN, name, DoB).
# This models patients with multiple encounters over time.
_PATIENT_REUSE_RATE = 0.15

# Maximum number of distinct patients to keep in the reuse registry.
_MAX_PATIENT_REGISTRY = 500

# Fraction of patient-reuse encounters that follow a structured clinical
# sequence (surgery pathway, repeat visit) rather than random reuse.
_ENCOUNTER_SEQUENCE_RATE = 0.40


@dataclass(frozen=True)
class _EncounterStep:
    """One visit in a multi-encounter sequence."""

    # Service date offset in days relative to the anchor date.
    # Negative = before anchor, positive = after.
    days_offset_min: int
    days_offset_max: int
    cpt_codes: list[str]
    icd10_codes: list[str]


# Pre-defined clinical encounter sequences.
# Each sequence is a list of steps; the anchor date is the "main" event.
_ENCOUNTER_SEQUENCES: list[list[_EncounterStep]] = [
    # ── Surgery pathway: pre-op consult → surgery → post-op follow-up ──
    [
        _EncounterStep(
            days_offset_min=-21,
            days_offset_max=-7,
            cpt_codes=["99205"],
            icd10_codes=["M17.11"],
        ),
        _EncounterStep(
            days_offset_min=0,
            days_offset_max=0,
            cpt_codes=["27447"],
            icd10_codes=["M17.11"],
        ),
        _EncounterStep(
            days_offset_min=14,
            days_offset_max=28,
            cpt_codes=["99213"],
            icd10_codes=["M17.11"],
        ),
    ],
    # ── Cholecystectomy pathway ──
    [
        _EncounterStep(
            days_offset_min=-14,
            days_offset_max=-5,
            cpt_codes=["99205"],
            icd10_codes=["K80.20"],
        ),
        _EncounterStep(
            days_offset_min=0,
            days_offset_max=0,
            cpt_codes=["47562"],
            icd10_codes=["K80.20"],
        ),
        _EncounterStep(
            days_offset_min=10,
            days_offset_max=21,
            cpt_codes=["99213"],
            icd10_codes=["K80.20"],
        ),
    ],
    # ── Knee arthroscopy pathway ──
    [
        _EncounterStep(
            days_offset_min=-14,
            days_offset_max=-7,
            cpt_codes=["99204"],
            icd10_codes=["M23.21"],
        ),
        _EncounterStep(
            days_offset_min=0,
            days_offset_max=0,
            cpt_codes=["29881"],
            icd10_codes=["M23.21"],
        ),
        _EncounterStep(
            days_offset_min=7,
            days_offset_max=14,
            cpt_codes=["99213"],
            icd10_codes=["M23.21"],
        ),
    ],
    # ── Hernia repair pathway ──
    [
        _EncounterStep(
            days_offset_min=-10,
            days_offset_max=-3,
            cpt_codes=["99204"],
            icd10_codes=["K40.90"],
        ),
        _EncounterStep(
            days_offset_min=0,
            days_offset_max=0,
            cpt_codes=["49505"],
            icd10_codes=["K40.90"],
        ),
        _EncounterStep(
            days_offset_min=10,
            days_offset_max=21,
            cpt_codes=["99213"],
            icd10_codes=["K40.90"],
        ),
    ],
    # ── Repeat visit: same chronic condition ~6 months apart ──
    [
        _EncounterStep(
            days_offset_min=-200,
            days_offset_max=-150,
            cpt_codes=["99214"],
            icd10_codes=["I10", "E11.9"],
        ),
        _EncounterStep(
            days_offset_min=0,
            days_offset_max=0,
            cpt_codes=["99214", "80053"],
            icd10_codes=["I10", "E11.9"],
        ),
    ],
    # ── Repeat visit: same issue ~3 months apart ──
    [
        _EncounterStep(
            days_offset_min=-100,
            days_offset_max=-80,
            cpt_codes=["99214"],
            icd10_codes=["M54.9"],
        ),
        _EncounterStep(
            days_offset_min=0,
            days_offset_max=0,
            cpt_codes=["99214"],
            icd10_codes=["M54.9"],
        ),
    ],
    # ── Repeat visit: respiratory follow-up ──
    [
        _EncounterStep(
            days_offset_min=-90,
            days_offset_max=-60,
            cpt_codes=["99213", "71046"],
            icd10_codes=["J44.9"],
        ),
        _EncounterStep(
            days_offset_min=0,
            days_offset_max=0,
            cpt_codes=["99214"],
            icd10_codes=["J44.9"],
        ),
    ],
]


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

    # Create generators.  A small drug-defect rate ensures some drug lines
    # carry defects (missing NDC, quantity mismatch) so downstream NDC edits
    # in the batch analyzer can be exercised end-to-end.
    claim_gen = ClaimGenerator(seed=seed, drug_defect_rate=0.10)
    payment_gen = PaymentGenerator(seed=seed)
    openar_gen = OpenARGenerator(seed=seed)

    # Plan HAR groups
    har_groups = _plan_har_groups(count)
    multi_pcn_hars = sum(1 for g in har_groups if g > 1)
    print(f"  HAR groups: {len(har_groups):,} ({multi_pcn_hars:,} with multiple PCNs)")

    ar_rows: list[dict] = []

    # Patient registry: returning patients share the same MRN, name, DoB,
    # etc. across different encounters.  MRN is stored on each context.
    patient_registry: list[PatientContext] = []

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
        har_id = openar_gen._generate_hospital_account_id()

        for _claim_idx in range(group_size):
            # PB: same date; HB (group_size > 3): spread across 1-14 days
            if group_size <= 3:
                svc_date = ctx.base_service_date
            else:
                svc_date = ctx.base_service_date + timedelta(
                    days=random.randint(0, min(14, group_size))
                )

            claim = claim_gen.generate_claim(
                ctx=ctx,
                service_date=svc_date,
                forced_cpt_codes=forced_cpt_codes,
                forced_icd10_codes=forced_icd10_codes,
            )
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

            if patient_registry and random.random() < _PATIENT_REUSE_RATE:
                # Returning patient — decide between a structured encounter
                # sequence and a simple random revisit.
                seq = random.choice(_ENCOUNTER_SEQUENCES)
                steps_remaining = len(har_groups) - group_idx
                use_sequence = (
                    random.random() < _ENCOUNTER_SEQUENCE_RATE
                    and steps_remaining >= len(seq)
                )

                if use_sequence:
                    # Structured clinical sequence (e.g. consult → surgery → follow-up)
                    mrn = openar_gen._generate_mrn()
                    ctx = claim_gen.generate_patient_context()
                    ctx = replace(ctx, mrn=mrn)
                    if len(patient_registry) < _MAX_PATIENT_REGISTRY:
                        patient_registry.append(ctx)

                    # Anchor date: the "main" event sits 30-60 days in the past
                    # so both earlier and later steps fall within a plausible window.
                    anchor = date.today() - timedelta(days=random.randint(30, 60))

                    for step in seq:
                        step_size = har_groups[group_idx]
                        offset = random.randint(
                            step.days_offset_min, step.days_offset_max
                        )
                        step_date = anchor + timedelta(days=offset)
                        step_ctx = replace(ctx, base_service_date=step_date)

                        _emit_har_group(
                            step_ctx,
                            step_size,
                            forced_cpt_codes=step.cpt_codes,
                            forced_icd10_codes=step.icd10_codes,
                        )
                        group_idx += 1
                    continue

                # Simple random revisit — same patient, new service date.
                existing_ctx = random.choice(patient_registry)
                ctx = replace(
                    existing_ctx,
                    base_service_date=generate_service_date(
                        days_ago_min=1, days_ago_max=90
                    ),
                )
            else:
                # New patient
                mrn = openar_gen._generate_mrn()
                ctx = claim_gen.generate_patient_context()
                ctx = replace(ctx, mrn=mrn)
                if len(patient_registry) < _MAX_PATIENT_REGISTRY:
                    patient_registry.append(ctx)

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
