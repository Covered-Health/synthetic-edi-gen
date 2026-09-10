"""Tests for the 837I cross-field constraint validator.

The batch test is only meaningful if three other things hold, so each has its
own group below: the constraint tables must not contain typos (the batch cannot
catch a wrong table, because the generator reads the same one), the batch must
actually contain the codes it claims to validate (a filter bug that emits
nothing would pass trivially), and the validator must be shown to reject claims
that really are contradictory.
"""

import json
from datetime import UTC, date, datetime, time, timedelta

import pytest

from synthetic_edi_gen.basic_codes import (
    ACUTE_INPATIENT_FACILITY,
    EXCLUSIVE_CONDITION_PAIRS,
    EXCLUSIVE_OCCURRENCE_PAIRS,
    EXPIRED_DISCHARGE_STATUSES,
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
    UB04_CONDITION_CODES,
    UB04_FACILITY_TYPES,
    UB04_INPATIENT_DISCHARGE_STATUS,
    UB04_OCCURRENCE_CODES,
    UB04_OCCURRENCE_SPAN_CODES,
    UB04_OUTPATIENT_DISCHARGE_STATUS,
    UB04_VALUE_CODES,
    UNSUPPORTED_CONDITION_CODES,
    UNSUPPORTED_OCCURRENCE_CODES,
    UNSUPPORTED_VALUE_CODES,
    WITHIN_STAY_SPAN_CODES,
)
from synthetic_edi_gen.claim_generator import ClaimGenerator
from synthetic_edi_gen.edi_models import Code, CodeAndDate, CodeAndDateRange, InstClaim
from synthetic_edi_gen.generate import generate
from synthetic_edi_gen.inst_constraints import validate_institutional_claim

CONDITION_CODES = {code for code, _ in UB04_CONDITION_CODES}
OCCURRENCE_CODES = {code for code, _ in UB04_OCCURRENCE_CODES}
SPAN_CODES = {code for code, _ in UB04_OCCURRENCE_SPAN_CODES}
VALUE_CODES = {code for code, _ in UB04_VALUE_CODES}
FACILITY_CODES = {code for code, _ in UB04_FACILITY_TYPES}
DISCHARGE_STATUSES = set(UB04_INPATIENT_DISCHARGE_STATUS) | set(
    UB04_OUTPATIENT_DISCHARGE_STATUS
)

SEEDS = [1, 2, 3, 4, 5]
CLAIMS_PER_SEED = 400


def _prefixes(violations: list[str]) -> set[str]:
    return {v.split(":")[0] for v in violations}


class TestConstraintTablesAreWellFormed:
    """A wrong table is invisible to the batch test: the generator filters by
    the same set the validator checks, so both agree on the mistake."""

    @pytest.mark.parametrize(
        ("table", "universe"),
        [
            (INPATIENT_ONLY_CONDITION_CODES, CONDITION_CODES),
            (OUTPATIENT_ONLY_CONDITION_CODES, CONDITION_CODES),
            (INPATIENT_ONLY_OCCURRENCE_CODES, OCCURRENCE_CODES),
            (TRANSFER_DISCHARGE_STATUSES, DISCHARGE_STATUSES),
            (PRIOR_STAY_SPAN_CODES, SPAN_CODES),
            (WITHIN_STAY_SPAN_CODES, SPAN_CODES),
            (INPATIENT_ONLY_SPAN_CODES, SPAN_CODES),
            (SNF_ONLY_OCCURRENCE_SPAN_CODES, SPAN_CODES),
            (INPATIENT_ONLY_VALUE_CODES, VALUE_CODES),
            # Per family, not against the union of all three: the families reuse
            # each other's numbers, so a merged check would pass while silently
            # excluding a valid code from a different family.
            (UNSUPPORTED_CONDITION_CODES, CONDITION_CODES),
            (UNSUPPORTED_OCCURRENCE_CODES, OCCURRENCE_CODES),
            (UNSUPPORTED_VALUE_CODES, VALUE_CODES),
        ],
    )
    def test_every_constrained_code_exists_in_its_ub04_list(self, table, universe):
        assert table <= universe

    def test_expired_statuses_cover_what_the_generator_draws(self):
        """40, 41 and 42 are real UB-04 expired statuses that this generator
        does not draw; the validator still has to accept them, so this table is
        deliberately wider than the discharge status weights."""
        assert {"20"} == EXPIRED_DISCHARGE_STATUSES & DISCHARGE_STATUSES
        assert not (EXPIRED_DISCHARGE_STATUSES & TRANSFER_DISCHARGE_STATUSES)

    def test_facility_types_are_partitioned_into_stay_and_no_stay(self):
        assert INPATIENT_FACILITY_TYPES < FACILITY_CODES
        assert ACUTE_INPATIENT_FACILITY in INPATIENT_FACILITY_TYPES
        assert SNF_FACILITY in INPATIENT_FACILITY_TYPES

    def test_no_code_is_both_inpatient_and_outpatient_only(self):
        assert not (INPATIENT_ONLY_CONDITION_CODES & OUTPATIENT_ONLY_CONDITION_CODES)

    def test_span_classes_do_not_overlap(self):
        assert not (PRIOR_STAY_SPAN_CODES & WITHIN_STAY_SPAN_CODES)

    @pytest.mark.parametrize(
        ("pairs", "universe"),
        [
            (EXCLUSIVE_CONDITION_PAIRS, CONDITION_CODES),
            (EXCLUSIVE_OCCURRENCE_PAIRS, OCCURRENCE_CODES),
        ],
    )
    def test_exclusive_pairs_name_real_codes(self, pairs, universe):
        for pair in pairs:
            assert len(pair) == 2
            assert pair <= universe


class TestGeneratedBatchHasNoViolations:
    @pytest.mark.parametrize("seed", SEEDS)
    def test_batch_is_clean(self, seed):
        gen = ClaimGenerator(seed=seed)
        for _ in range(CLAIMS_PER_SEED):
            claim = gen.generate_institutional_claim()
            assert validate_institutional_claim(claim) == []

    @pytest.mark.parametrize(
        ("cpt_codes", "icd10_codes"),
        [
            (["27447"], ["M17.11"]),
            (["47562"], ["K80.20"]),
            (["29881"], ["M23.21"]),
            (["99213"], ["I10"]),
            (["99214", "80053"], ["I10", "E11.9"]),
        ],
    )
    def test_forced_code_paths_are_clean(self, cpt_codes, icd10_codes):
        """`generate.py` drives these through `_ENCOUNTER_SEQUENCES` on every
        run, and they are the path where the stay collapses to a single day."""
        gen = ClaimGenerator(seed=11)
        for _ in range(200):
            claim = gen.generate_institutional_claim(
                forced_cpt_codes=cpt_codes,
                forced_icd10_codes=icd10_codes,
            )
            assert validate_institutional_claim(claim) == []

    def test_revised_and_refiled_copies_stay_clean(self):
        gen = ClaimGenerator(seed=23)
        for _ in range(200):
            claim = gen.generate_institutional_claim()
            assert validate_institutional_claim(gen.generate_revised_claim(claim)) == []
            assert validate_institutional_claim(gen.generate_refiled_claim(claim)) == []


class TestBatchActuallyExercisesTheRules:
    """Guards against the batch passing because the generator emits nothing."""

    @pytest.fixture(scope="class")
    def claims(self):
        gen = ClaimGenerator(seed=31)
        return [gen.generate_institutional_claim() for _ in range(2000)]

    def test_every_ub04_field_is_populated_somewhere(self, claims):
        assert any(c.conditions for c in claims)
        assert any(c.occurrences for c in claims)
        assert any(c.occurrence_spans for c in claims)
        assert any(c.value_infos for c in claims)
        assert any(c.procs for c in claims)
        assert any(c.drg for c in claims)

    def test_both_claim_types_and_all_facilities_appear(self, claims):
        facilities = {c.facility_code.code for c in claims}
        assert facilities >= FACILITY_CODES

    def test_every_permitted_code_is_reachable(self, claims):
        """Every code the generator does not deliberately exclude must appear.

        A filter that silently removes a valid code is invisible to the batch
        test — drawing fewer codes only means fewer chances to violate a rule.
        Condition 40 is left out because it needs a zero-night stay and a
        transfer discharge status at once, so it lands on roughly 1 claim in
        2000 and asserting on it would be flaky rather than protective.
        """

        def drawn(field: str) -> set[str]:
            return {c.code for claim in claims for c in getattr(claim, field) or []}

        assert drawn("conditions") >= (
            CONDITION_CODES - UNSUPPORTED_CONDITION_CODES - {"40"}
        )
        assert drawn("occurrences") >= OCCURRENCE_CODES - UNSUPPORTED_OCCURRENCE_CODES
        assert drawn("value_infos") >= VALUE_CODES - UNSUPPORTED_VALUE_CODES
        assert drawn("occurrence_spans") >= SPAN_CODES

    def test_mutually_exclusive_pairs_never_co_occur(self, claims):
        for claim in claims:
            conditions = {c.code for c in claim.conditions or []}
            occurrences = {o.code for o in claim.occurrences or []}
            assert not any(pair <= conditions for pair in EXCLUSIVE_CONDITION_PAIRS)
            assert not any(pair <= occurrences for pair in EXCLUSIVE_OCCURRENCE_PAIRS)

    def test_expired_patients_are_generated_with_a_date_of_death(self, claims):
        expired = [
            c for c in claims if c.patient_status_code in EXPIRED_DISCHARGE_STATUSES
        ]
        assert expired
        for claim in expired:
            codes = {o.code for o in claim.occurrences or []}
            assert "55" in codes

    def test_date_of_death_only_appears_on_expired_claims(self, claims):
        for claim in claims:
            codes = {o.code for o in claim.occurrences or []}
            if "55" in codes:
                assert claim.patient_status_code in EXPIRED_DISCHARGE_STATUSES

    def test_both_span_classes_are_generated(self, claims):
        spans = {s.code for c in claims for s in c.occurrence_spans or []}
        assert spans & PRIOR_STAY_SPAN_CODES
        assert spans & WITHIN_STAY_SPAN_CODES

    def test_outpatient_claims_carry_no_stay_spans(self, claims):
        """Every span but 73 describes a stay, so an outpatient bill has no
        referent for one."""
        outpatient = [c for c in claims if c.facility_code.code in {"13", "32"}]
        assert outpatient
        for claim in outpatient:
            for span in claim.occurrence_spans or []:
                assert span.code == "73"

    def test_same_day_stays_are_generated(self, claims):
        assert any(
            c.admission_date_and_hour is not None
            and c.statement_date_from == c.statement_date_to
            for c in claims
        )


class TestValidatorRejectsContradictions:
    """Mutation tests: take a claim the generator produced and break one field.

    Hand-built claims can pass for the wrong reason — a field left `None` that
    the generator always populates. Mutating real output proves the validator
    sees the fields in the shapes that actually occur.
    """

    @pytest.fixture()
    def expired_claim(self):
        gen = ClaimGenerator(seed=5)
        for _ in range(5000):
            claim = gen.generate_institutional_claim()
            if claim.patient_status_code in EXPIRED_DISCHARGE_STATUSES:
                return claim
        pytest.fail("generator produced no expired claim")

    @pytest.fixture()
    def inpatient_claim(self):
        gen = ClaimGenerator(seed=6)
        for _ in range(5000):
            claim = gen.generate_institutional_claim()
            if claim.facility_code.code == "11":
                return claim
        pytest.fail("generator produced no acute inpatient claim")

    def test_baseline_claims_are_clean(self, expired_claim, inpatient_claim):
        assert validate_institutional_claim(expired_claim) == []
        assert validate_institutional_claim(inpatient_claim) == []

    def test_date_of_death_on_a_discharged_patient_is_rejected(self, expired_claim):
        mutated = expired_claim.model_copy(update={"patient_status_code": "01"})
        assert "OCC55_NOT_EXPIRED" in _prefixes(validate_institutional_claim(mutated))

    def test_expired_without_a_date_of_death_is_rejected(self, expired_claim):
        survivors = [o for o in expired_claim.occurrences or [] if o.code != "55"]
        mutated = expired_claim.model_copy(update={"occurrences": survivors or None})
        assert "EXPIRED_WITHOUT_OCC55" in _prefixes(
            validate_institutional_claim(mutated)
        )

    def test_date_of_death_before_admission_is_rejected(self, expired_claim):
        occurrences = [o.model_copy(deep=True) for o in expired_claim.occurrences or []]
        for occurrence in occurrences:
            if occurrence.code == "55":
                occurrence.occurrence_date = (
                    expired_claim.statement_date_from - timedelta(days=4)
                )
        mutated = expired_claim.model_copy(update={"occurrences": occurrences})
        assert "OCC55_DATE_MISMATCH" in _prefixes(validate_institutional_claim(mutated))

    def test_mutually_exclusive_conditions_are_rejected(self, inpatient_claim):
        conditions = [
            Code(sub_type="CONDITION_CODE", code="41", desc="Partial hospitalization"),
            Code(
                sub_type="CONDITION_CODE",
                code="44",
                desc="Inpatient admission changed to outpatient",
            ),
        ]
        mutated = inpatient_claim.model_copy(update={"conditions": conditions})
        found = _prefixes(validate_institutional_claim(mutated))
        assert "CONDITION_EXCLUSIVE" in found
        assert "CONDITION_CLAIM_TYPE" in found

    def test_mutually_exclusive_occurrences_are_rejected(self, inpatient_claim):
        occurrences = [
            CodeAndDate(
                sub_type="OCCURRENCE_CODE",
                code=code,
                desc=desc,
                occurrence_date=inpatient_claim.statement_date_from,
            )
            for code, desc in (
                ("01", "Accident/medical coverage"),
                ("04", "Accident/employment related"),
            )
        ]
        mutated = inpatient_claim.model_copy(update={"occurrences": occurrences})
        assert "OCCURRENCE_EXCLUSIVE" in _prefixes(
            validate_institutional_claim(mutated)
        )

    def test_drg_on_a_skilled_nursing_bill_is_rejected(self, inpatient_claim):
        mutated = inpatient_claim.model_copy(
            update={
                "facility_code": Code(
                    sub_type="UB_FACILITY_TYPE",
                    code="21",
                    desc="Skilled nursing inpatient",
                )
            }
        )
        assert "DRG_CLAIM_TYPE" in _prefixes(validate_institutional_claim(mutated))

    def test_prior_stay_span_overlapping_the_stay_is_rejected(self, inpatient_claim):
        span = CodeAndDateRange(
            sub_type="OCCURRENCE_SPAN_CODE",
            code="71",
            desc="Prior stay dates",
            occurrence_date=inpatient_claim.statement_date_from - timedelta(days=10),
            occurrence_end_date=inpatient_claim.statement_date_from + timedelta(days=1),
        )
        mutated = inpatient_claim.model_copy(update={"occurrence_spans": [span]})
        assert "SPAN_PRIOR_OVERLAPS" in _prefixes(validate_institutional_claim(mutated))

    def test_discharge_before_admission_is_rejected(self, inpatient_claim):
        admitted = inpatient_claim.admission_date_and_hour
        assert admitted is not None
        mutated = inpatient_claim.model_copy(
            update={"discharge_time": admitted - timedelta(hours=1)}
        )
        assert "DISCHARGE_BEFORE_ADMISSION" in _prefixes(
            validate_institutional_claim(mutated)
        )


class TestReportedExample:
    """The claim from RD-3773, which the generator must never produce again."""

    def test_the_reported_claim_is_rejected(self):
        gen = ClaimGenerator(seed=77)
        base = next(
            c
            for c in (gen.generate_institutional_claim() for _ in range(500))
            if c.facility_code.code == "11"
        )
        admitted = date(2026, 7, 17)
        claim = base.model_copy(
            update={
                # Patient Discharge Status: 01 Discharged to home or self-care
                "patient_status_code": "01",
                # Admission and Discharge: 7/17/2026 - 7/17/2026
                "statement_date_from": admitted,
                "statement_date_to": admitted,
                "admission_date_and_hour": datetime.combine(
                    admitted, time(hour=9), tzinfo=UTC
                ),
                "discharge_time": datetime.combine(admitted, time(hour=14), tzinfo=UTC),
                # Occurrence Code 55 Date of death: 7/13/2026
                "occurrences": [
                    CodeAndDate(
                        sub_type="OCCURRENCE_CODE",
                        code="55",
                        desc="Date of death",
                        occurrence_date=date(2026, 7, 13),
                    )
                ],
                # Condition Codes: 02, 41, 44
                "conditions": [
                    Code(sub_type="CONDITION_CODE", code=code, desc=code)
                    for code in ("02", "41", "44")
                ],
                "occurrence_spans": None,
            }
        )

        found = _prefixes(validate_institutional_claim(claim))
        # Status 01 conflicts with occurrence 55.
        assert "OCC55_NOT_EXPIRED" in found
        # The date of death precedes admission.
        assert "OCC55_DATE_MISMATCH" in found
        # Conditions 41 and 44 are mutually exclusive.
        assert "CONDITION_EXCLUSIVE" in found
        # 41 and 44 are outpatient-only, and this is an inpatient bill.
        assert "CONDITION_CLAIM_TYPE" in found

    def test_the_generator_never_produces_it(self):
        """The specific combination, swept across a large batch."""
        gen = ClaimGenerator(seed=101)
        for _ in range(3000):
            claim = gen.generate_institutional_claim()
            conditions = {c.code for c in claim.conditions or []}
            occurrences = {o.code for o in claim.occurrences or []}
            assert not {"41", "44"} <= conditions
            if "55" in occurrences:
                assert claim.patient_status_code in EXPIRED_DISCHARGE_STATUSES


class TestFullPipelineOutput:
    """End-to-end through `generate`, which is where the returning-patient and
    encounter-sequence paths live.

    Those paths replay one patient context at several service dates, including
    dates months earlier than the one it was built for. Generating claims
    directly from `ClaimGenerator` never exercises them, so this is the only
    level at which that class of defect shows up.
    """

    @pytest.fixture(scope="class")
    def claims(self, tmp_path_factory):
        output_dir = tmp_path_factory.mktemp("edi")
        generate(
            count=600,
            output_dir=output_dir,
            institutional_claim_rate=1.0,
            seed=42,
            claims_per_file=0,
            payments_per_file=0,
            export_datetime=datetime(2026, 6, 2),
        )
        return [
            InstClaim.model_validate(json.loads(line))
            for line in (output_dir / "837_claims.jsonl").read_text().splitlines()
            if json.loads(line)["transaction"]["transactionType"] == "INST"
        ]

    def test_every_claim_is_clean(self, claims):
        assert claims
        violations = [v for c in claims for v in validate_institutional_claim(c)]
        assert violations == []

    def test_reused_patients_are_never_billed_before_birth(self, claims):
        """`rebase_patient_context` moves the encounter, not the birth date."""
        for claim in claims:
            assert claim.patient.person.birth_date <= claim.statement_date_from
