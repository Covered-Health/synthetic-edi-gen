"""Generate fake but realistic EDI 835 and 837 messages."""

# ruff: noqa: S311
import random
from datetime import timedelta
from pathlib import Path
from typing import Annotated, TextIO

from cyclopts import Parameter
from cyclopts.validators import Number

from synthetic_edi_gen._base import EDIBaseModel

from .claim_generator import ClaimGenerator
from .openar_generator import OpenARGenerator, write_openar_xlsx
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

    # Output files
    claims_file = output_dir / "837_claims.jsonl"
    payments_file = output_dir / "835_payments.jsonl"
    openar_file = output_dir / "openar.xlsx"

    claims_written = 0
    payments_written = 0
    ar_rows: list[dict] = []

    # Generate JSONL files
    with open(claims_file, "w") as f_claims, open(payments_file, "w") as f_payments:
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
                write_jsonl(f_claims, claim)
                claims_written += 1

                # Independent payment per claim
                payment = None
                if random.random() < match_rate:
                    payment = payment_gen.generate_payment_for_claim(claim)
                    write_jsonl(f_payments, payment)
                    payments_written += 1

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
                if claims_written % batch_size == 0:
                    pct = claims_written / count * 100
                    print(
                        f"  Generated {claims_written:,}"
                        f" / {count:,} claims ({pct:.1f}%)"
                    )
                    f_claims.flush()
                    f_payments.flush()

    # Generate unmatched AR rows
    unmatched_count = int(count * unmatched_ar_rate)
    if unmatched_count > 0:
        print(f"  Generating {unmatched_count:,} unmatched AR rows...")
        unmatched_rows = openar_gen.generate_unmatched_ar_rows(unmatched_count)
        ar_rows.extend(unmatched_rows)

    # Write OpenAR xlsx file
    print("  Writing OpenAR xlsx file...")
    write_openar_xlsx(ar_rows, str(openar_file))

    print("\n✓ Generation complete!")
    print(f"  Claims written: {claims_written:,} → {claims_file}")
    print(f"  Payments written: {payments_written:,} → {payments_file}")
    print(f"  Match rate achieved: {payments_written / claims_written:.1%}")
    print(f"  OpenAR rows written: {len(ar_rows):,} → {openar_file}")
    print(f"    (including {unmatched_count:,} unmatched rows)")
    print(f"  HAR groups: {len(har_groups):,} ({multi_pcn_hars:,} multi-PCN)")
