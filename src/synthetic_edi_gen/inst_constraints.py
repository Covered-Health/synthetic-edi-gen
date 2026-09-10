"""Cross-field constraints for 837I institutional claims.

UB-04 fields are not independent: a discharge status constrains which occurrence
codes may appear, a facility type constrains which condition and value codes are
reportable, and several codes constrain each other's dates. The generator draws
each field separately, so these rules are what stop it emitting a claim that
could not exist — a patient discharged to home who also has a date of death, or
a date of death four days before admission.

Every violation string starts with a stable ``UPPER_SNAKE`` prefix so tests can
assert on the prefix without breaking when a message is reworded.

See ``docs/institutional-constraints.md`` for the enumerated rule list.
"""

from datetime import date, datetime

from .basic_codes import (
    ACUTE_INPATIENT_FACILITY,
    EXCLUSIVE_CONDITION_PAIRS,
    EXCLUSIVE_OCCURRENCE_PAIRS,
    EXPIRED_DISCHARGE_STATUSES,
    FEMALE_ONLY_PCS_CODES,
    INPATIENT_FACILITY_TYPES,
    INPATIENT_ONLY_CONDITION_CODES,
    INPATIENT_ONLY_OCCURRENCE_CODES,
    INPATIENT_ONLY_SPAN_CODES,
    INPATIENT_ONLY_VALUE_CODES,
    OUTPATIENT_ONLY_CONDITION_CODES,
    PRIOR_STAY_SPAN_CODES,
    SNF_FACILITY,
    SNF_ONLY_OCCURRENCE_SPAN_CODES,
    TRANSFER_DISCHARGE_STATUSES,
    UNSUPPORTED_CONDITION_CODES,
    UNSUPPORTED_OCCURRENCE_CODES,
    UNSUPPORTED_VALUE_CODES,
    WITHIN_STAY_SPAN_CODES,
)
from .edi_models import InstClaim, PersonWithDemographic

DEATH_OCCURRENCE_CODE = "55"

__all__ = ["validate_institutional_claim"]


def _person(claim: InstClaim) -> PersonWithDemographic | None:
    return claim.patient.person if claim.patient else None


def _as_date(value: date | datetime | None) -> date | None:
    """Normalise the datetime-valued admission/discharge fields to a date."""
    if isinstance(value, datetime):
        return value.date()
    return value


def validate_institutional_claim(claim: InstClaim) -> list[str]:
    """Return every cross-field constraint the claim violates.

    An empty list means the claim is internally consistent. Each entry is a
    ``PREFIX: human readable explanation`` string.
    """
    violations: list[str] = []
    facility = claim.facility_code.code if claim.facility_code else ""
    is_inpatient = facility in INPATIENT_FACILITY_TYPES
    status = claim.patient_status_code or ""

    stmt_from = claim.statement_date_from
    stmt_to = claim.statement_date_to
    if stmt_from is None or stmt_to is None:
        # Every date rule below is relative to the statement period, so there
        # is nothing meaningful left to check without it.
        return ["NO_STATEMENT_PERIOD: claim has no statement from/through date"]

    admission = _as_date(claim.admission_date_and_hour)
    discharge = _as_date(claim.discharge_time)

    condition_codes = {c.code for c in (claim.conditions or [])}
    occurrence_codes = {o.code for o in (claim.occurrences or [])}
    value_codes = {v.code for v in (claim.value_infos or [])}

    violations += _check_dates(claim, stmt_from, stmt_to, admission, discharge, status)
    violations += _check_unsupported(condition_codes, UNSUPPORTED_CONDITION_CODES)
    violations += _check_unsupported(occurrence_codes, UNSUPPORTED_OCCURRENCE_CODES)
    violations += _check_unsupported(value_codes, UNSUPPORTED_VALUE_CODES)
    violations += _check_conditions(
        condition_codes, is_inpatient, status, stmt_from, stmt_to
    )
    violations += _check_occurrences(claim, is_inpatient, status, stmt_from, stmt_to)
    violations += _check_spans(claim, is_inpatient, facility, stmt_from, stmt_to)
    violations += _check_values(value_codes, is_inpatient)
    violations += _check_drg_and_procs(
        claim, facility, is_inpatient, stmt_from, stmt_to
    )
    return violations


def _check_dates(
    claim: InstClaim,
    stmt_from: date,
    stmt_to: date,
    admission: date | None,
    discharge: date | None,
    status: str,
) -> list[str]:
    out: list[str] = []
    if stmt_from > stmt_to:
        out.append(
            f"STATEMENT_RANGE_INVERTED: statement period {stmt_from} to {stmt_to}"
        )

    adm_dt = claim.admission_date_and_hour
    dis_dt = claim.discharge_time
    if adm_dt is not None and dis_dt is not None and dis_dt < adm_dt:
        out.append(
            f"DISCHARGE_BEFORE_ADMISSION: admitted {adm_dt}, discharged {dis_dt}"
        )

    if admission is not None and admission != stmt_from:
        out.append(
            f"ADMISSION_OUTSIDE_STATEMENT: admitted {admission},"
            f" statement starts {stmt_from}"
        )
    if discharge is not None and discharge != stmt_to:
        out.append(
            f"DISCHARGE_OUTSIDE_STATEMENT: discharged {discharge},"
            f" statement ends {stmt_to}"
        )

    if status == "30" and discharge is not None:
        out.append(
            f"STILL_PATIENT_WITH_DISCHARGE: status 30 with discharge {discharge}"
        )

    person = _person(claim)
    if (
        person is not None
        and person.birth_date is not None
        and person.birth_date > stmt_from
    ):
        out.append(
            f"BORN_AFTER_ENCOUNTER: born {person.birth_date},"
            f" statement starts {stmt_from}"
        )

    for line in claim.service_lines or []:
        line_from = line.service_date_from
        line_to = line.service_date_to
        if line_from is not None and not (stmt_from <= line_from <= stmt_to):
            out.append(
                f"LINE_OUTSIDE_STATEMENT: line {line.source_line_id} dated"
                f" {line_from}, statement {stmt_from} to {stmt_to}"
            )
        if line_to is not None and (
            line_to > stmt_to or (line_from is not None and line_to < line_from)
        ):
            out.append(
                f"LINE_OUTSIDE_STATEMENT: line {line.source_line_id} through"
                f" {line_to}, statement {stmt_from} to {stmt_to}"
            )
    return out


def _check_unsupported(codes: set[str], unsupported_in_family: set[str]) -> list[str]:
    """Check one UB-04 family against its own exclusion set.

    Per family rather than against a merged set: condition 04 and occurrence 04
    are different codes, and only the first is unsupported here.
    """
    unsupported = codes & unsupported_in_family
    return [
        f"UNSUPPORTED_CODE: {code} requires a claim shape this generator does not emit"
        for code in sorted(unsupported)
    ]


def _check_conditions(
    codes: set[str],
    is_inpatient: bool,
    status: str,
    stmt_from: date,
    stmt_to: date,
) -> list[str]:
    out: list[str] = []
    for code in sorted(codes & INPATIENT_ONLY_CONDITION_CODES):
        if not is_inpatient:
            out.append(f"CONDITION_CLAIM_TYPE: condition {code} is inpatient-only")
    for code in sorted(codes & OUTPATIENT_ONLY_CONDITION_CODES):
        if is_inpatient:
            out.append(f"CONDITION_CLAIM_TYPE: condition {code} is outpatient-only")

    for pair in EXCLUSIVE_CONDITION_PAIRS:
        if pair <= codes:
            a, b = sorted(pair)
            out.append(f"CONDITION_EXCLUSIVE: conditions {a} and {b} cannot coexist")

    if "40" in codes:
        if stmt_from != stmt_to:
            out.append(
                f"CONDITION_40_NOT_SAME_DAY: same day transfer over"
                f" {stmt_from} to {stmt_to}"
            )
        if status not in TRANSFER_DISCHARGE_STATUSES:
            out.append(
                f"CONDITION_40_NOT_TRANSFER: same day transfer with"
                f" discharge status {status}"
            )
    return out


def _check_occurrences(
    claim: InstClaim,
    is_inpatient: bool,
    status: str,
    stmt_from: date,
    stmt_to: date,
) -> list[str]:
    out: list[str] = []
    occurrences = claim.occurrences or []
    codes = {o.code for o in occurrences}
    person = _person(claim)
    birth_date = person.birth_date if person else None

    for code in sorted(codes & INPATIENT_ONLY_OCCURRENCE_CODES):
        if not is_inpatient:
            out.append(f"OCCURRENCE_CLAIM_TYPE: occurrence {code} is inpatient-only")

    for pair in EXCLUSIVE_OCCURRENCE_PAIRS:
        if pair <= codes:
            a, b = sorted(pair)
            out.append(f"OCCURRENCE_EXCLUSIVE: occurrences {a} and {b} cannot coexist")

    expired = status in EXPIRED_DISCHARGE_STATUSES
    if DEATH_OCCURRENCE_CODE in codes and not expired:
        out.append(
            f"OCC55_NOT_EXPIRED: occurrence 55 (date of death) with"
            f" discharge status {status}"
        )
    if expired and DEATH_OCCURRENCE_CODE not in codes:
        out.append(
            f"EXPIRED_WITHOUT_OCC55: discharge status {status} without"
            " occurrence 55 (date of death)"
        )

    for occurrence in occurrences:
        occurred = occurrence.occurrence_date
        if occurred > stmt_to:
            out.append(
                f"OCCURRENCE_AFTER_STATEMENT: occurrence {occurrence.code}"
                f" dated {occurred}, statement ends {stmt_to}"
            )
        if birth_date is not None and occurred < birth_date:
            out.append(
                f"OCCURRENCE_BEFORE_BIRTH: occurrence {occurrence.code}"
                f" dated {occurred}, born {birth_date}"
            )
        if occurrence.code == DEATH_OCCURRENCE_CODE and occurred != stmt_to:
            out.append(
                f"OCC55_DATE_MISMATCH: date of death {occurred} is not the"
                f" statement end {stmt_to}"
            )
        if occurrence.code == "40" and occurred > stmt_from:
            out.append(
                f"OCC40_AFTER_ADMISSION: scheduled admission {occurred} is after"
                f" admission {stmt_from}"
            )
    return out


def _check_spans(
    claim: InstClaim,
    is_inpatient: bool,
    facility: str,
    stmt_from: date,
    stmt_to: date,
) -> list[str]:
    out: list[str] = []
    for span in claim.occurrence_spans or []:
        code = span.code
        start = span.occurrence_date
        end = span.occurrence_end_date

        if code in SNF_ONLY_OCCURRENCE_SPAN_CODES and facility != SNF_FACILITY:
            out.append(
                f"SPAN_CLAIM_TYPE: span {code} is SNF-only, facility is {facility}"
            )
        if code in INPATIENT_ONLY_SPAN_CODES and not is_inpatient:
            out.append(f"SPAN_CLAIM_TYPE: span {code} is inpatient-only")

        if end is not None and end < start:
            out.append(f"SPAN_INVERTED: span {code} runs {start} to {end}")

        if code in PRIOR_STAY_SPAN_CODES:
            # A prior stay must be over before the stay being billed begins.
            if start > stmt_from:
                out.append(
                    f"SPAN_PRIOR_NOT_BEFORE: span {code} starts {start},"
                    f" statement starts {stmt_from}"
                )
            if end is not None and end > stmt_from:
                out.append(
                    f"SPAN_PRIOR_OVERLAPS: span {code} ends {end},"
                    f" statement starts {stmt_from}"
                )
        elif code in WITHIN_STAY_SPAN_CODES:
            if start < stmt_from or start > stmt_to:
                out.append(
                    f"SPAN_OUTSIDE_STATEMENT: span {code} starts {start},"
                    f" statement {stmt_from} to {stmt_to}"
                )
            if end is not None and end > stmt_to:
                out.append(
                    f"SPAN_OUTSIDE_STATEMENT: span {code} ends {end},"
                    f" statement ends {stmt_to}"
                )
    return out


def _check_values(codes: set[str], is_inpatient: bool) -> list[str]:
    return [
        f"VALUE_CLAIM_TYPE: value code {code} is inpatient-only"
        for code in sorted(codes & INPATIENT_ONLY_VALUE_CODES)
        if not is_inpatient
    ]


def _check_drg_and_procs(
    claim: InstClaim,
    facility: str,
    is_inpatient: bool,
    stmt_from: date,
    stmt_to: date,
) -> list[str]:
    out: list[str] = []
    is_acute = facility == ACUTE_INPATIENT_FACILITY

    if claim.drg is not None and not is_acute:
        out.append(
            f"DRG_CLAIM_TYPE: MS-DRG {claim.drg.code} on facility {facility};"
            " DRG is acute inpatient only"
        )

    procs = claim.procs or []
    if procs and not is_inpatient:
        out.append("PROCS_CLAIM_TYPE: ICD-10-PCS procedures on an outpatient claim")

    person = _person(claim)
    gender = person.gender if person else None
    for proc in procs:
        performed = proc.occurrence_date
        if not (stmt_from <= performed <= stmt_to):
            out.append(
                f"PROC_OUTSIDE_STATEMENT: procedure {proc.code} performed"
                f" {performed}, statement {stmt_from} to {stmt_to}"
            )
        if proc.code in FEMALE_ONLY_PCS_CODES and gender != "FEMALE":
            out.append(
                f"PROC_GENDER_MISMATCH: procedure {proc.code} on a {gender} patient"
            )
    return out
