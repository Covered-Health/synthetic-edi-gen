# Cross-field constraints for 837I institutional claims

UB-04 fields are not independent. A discharge status decides which occurrence
codes may appear; a facility type decides which condition and value codes are
reportable; several codes constrain each other's dates. Drawing each field with
its own `random` call produces claims that could not exist — a patient
discharged to home who also has a date of death, or a date of death four days
before admission.

Two things enforce the rules below:

- **The generator** filters its candidate pools before drawing, in
  `claim_generator.py`. Ordering matters: the claim type and discharge status
  are chosen first, and every later draw is filtered by what is already fixed.
- **The validator**, `validate_institutional_claim` in `inst_constraints.py`,
  re-derives each rule from the finished claim and returns a list of violation
  strings. `tests/test_inst_constraints.py` asserts a generated batch produces
  none.

Both read the same tables in `basic_codes.py`, which sit directly beneath the
code lists they constrain. A batch test cannot catch a wrong table — the
generator and validator would agree on the mistake — so
`TestConstraintTablesAreWellFormed` checks the tables against the UB-04 lists
separately.

Each rule below names the prefix the validator emits when it fails.

## Code to claim type

The facility type (FL 4) decides whether the claim bills a stay. `11` hospital
inpatient and `21` skilled nursing are inpatient; `13` hospital outpatient and
`32` home health are not. Generator and validator both read `UB04_FACILITY_TYPES`
and `INPATIENT_FACILITY_TYPES` rather than each keeping their own list.

| Rule | Prefix |
|---|---|
| Conditions 38, 40, 69 (room and board, transfer, IPPS payment) require a stay | `CONDITION_CLAIM_TYPE` |
| Conditions 41, 44 describe a bill submitted as outpatient, so they cannot appear on an inpatient claim | `CONDITION_CLAIM_TYPE` |
| Occurrence 40 (scheduled admission date) presupposes an admission | `OCCURRENCE_CLAIM_TYPE` |
| Every span except 73 describes a stay, so none is reportable on an outpatient bill | `SPAN_CLAIM_TYPE` |
| Spans 70, 75, 78 are reportable only on a SNF bill (facility 21) | `SPAN_CLAIM_TYPE` |
| Value codes 01, 80 (semi-private rate, covered days) require a stay | `VALUE_CLAIM_TYPE` |
| MS-DRG is acute inpatient prospective payment: facility `11` only, never a SNF bill | `DRG_CLAIM_TYPE` |
| ICD-10-PCS procedures (FL 74) are inpatient; outpatient surgery is reported as CPT on the service lines | `PROCS_CLAIM_TYPE` |
| `0UT90ZZ` (resection of uterus) requires a female patient | `PROC_GENDER_MISMATCH` |

Five codes are excluded, via `UNSUPPORTED_OCCURRENCE_CODES` (24, 25),
`UNSUPPORTED_VALUE_CODES` (B1) and `UNSUPPORTED_CONDITION_CODES` (04, 07). Every
claim this generator emits is `payer_responsibility_sequence="PRIMARY"` with
`other_subscribers=None`, so there is no payer B and no prior-payer event to
report, and no bill uses a no-pay frequency code or a hospice bill type. The
codes stay in their lists so the planned "deliberately invalid claims" flag only
has to skip the filter. Prefix: `UNSUPPORTED_CODE`.

One set per family rather than one flat set of bare codes, because the families
reuse each other's numbers: condition 04 is "information only bill", but
occurrence 04 is "accident/employment related" and is perfectly reportable here.
A shared set removes both. That failure is silent — a code the generator can
never draw is a code that can never violate a rule, so the batch test goes on
passing — which is why `TestBatchActuallyExercisesTheRules` asserts that every
code not on an exclusion list is actually reachable, and why
`TestConstraintTablesAreWellFormed` checks each exclusion set against its own
UB-04 list rather than the union of all three.

## Code to code

| Rule | Prefix |
|---|---|
| Conditions 41 and 44 are mutually exclusive | `CONDITION_EXCLUSIVE` |
| Conditions 02 (employment related) and 09 (neither party employed) are mutually exclusive | `CONDITION_EXCLUSIVE` |
| Occurrences 01 and 04 (medical coverage vs. employment accident) are mutually exclusive | `OCCURRENCE_EXCLUSIVE` |
| Occurrence 55 (date of death) requires a discharge status meaning the patient died (`20`, `40`, `41`, `42`) | `OCC55_NOT_EXPIRED` |
| The converse: a claim with an expired discharge status must carry occurrence 55 | `EXPIRED_WITHOUT_OCC55` |
| Condition 40 (same day transfer) requires a transfer discharge status (`02`, `03`, `62`, `63`, `65`, `90`) | `CONDITION_40_NOT_TRANSFER` |

Occurrence 55 is a biconditional, not a filter. The generator places it before
the random gate that decides whether to report any occurrence codes at all —
see `_generate_occurrences`. A rule of this shape cannot be expressed by
narrowing a candidate pool.

## Code to date ordering

| Rule | Prefix |
|---|---|
| `statement_date_from` is on or before `statement_date_to` | `STATEMENT_RANGE_INVERTED` |
| Discharge is not earlier than admission, including the hour on a same-day stay | `DISCHARGE_BEFORE_ADMISSION` |
| Admission is the statement from date; discharge is the statement through date | `ADMISSION_OUTSIDE_STATEMENT`, `DISCHARGE_OUTSIDE_STATEMENT` |
| Discharge status 30 ("still a patient") means no discharge date | `STILL_PATIENT_WITH_DISCHARGE` |
| The patient was born before the encounter | `BORN_AFTER_ENCOUNTER` |
| Service lines fall inside the statement period | `LINE_OUTSIDE_STATEMENT` |
| Occurrence dates are not after the statement through date, and not before the patient was born | `OCCURRENCE_AFTER_STATEMENT`, `OCCURRENCE_BEFORE_BIRTH` |
| Occurrence 55 is dated at the statement through date: death ends the stay | `OCC55_DATE_MISMATCH` |
| Occurrence 40 (scheduled admission) is dated on or before admission | `OCC40_AFTER_ADMISSION` |
| Condition 40 (same day transfer) requires a zero-day statement period | `CONDITION_40_NOT_SAME_DAY` |
| Spans 70, 71, 78 describe an earlier encounter, so they must **end** on or before admission | `SPAN_PRIOR_NOT_BEFORE`, `SPAN_PRIOR_OVERLAPS` |
| Spans 72, 74, 75, 76, 77, M0 describe part of this encounter and fall inside the statement period | `SPAN_OUTSIDE_STATEMENT` |
| A span's through date is not before its from date | `SPAN_INVERTED` |

Occurrence 55 is dated at `statement_date_to` rather than `discharge_time`,
because discharge status `20` also appears in the outpatient weights and an
outpatient claim carries no discharge time.

`EXPIRED_DISCHARGE_STATUSES` is wider than what the generator draws: only `20`
is in the discharge status weights, but `40`, `41` and `42` are the
hospice-context expired statuses and `validate_institutional_claim` is a public
entry point that gets pointed at claims from elsewhere. It must not reject a
real claim for dying in the wrong place.

Span 73 (benefit eligibility period) is deliberately unconstrained beyond
from ≤ through: it describes the patient's coverage, not this encounter. It is
also the only span reportable on an outpatient claim, for the same reason.

Span 72 ("first/last visit dates") is genuinely an outpatient concept on a
multi-visit series bill. It is restricted to inpatient claims here because every
outpatient claim this generator emits covers a single day, where a first/last
visit span would collapse to a single instant. If series billing is ever added,
72 should move out of `INPATIENT_ONLY_SPAN_CODES`.

A prior-stay span ending exactly on the admission date is allowed: only
`end > statement_date_from` fails. A prior stay discharged the same day this one
admits is a real same-day transfer between facilities.

Condition 40 ("same day transfer") needs a zero-day stay, a transfer discharge
status, and the condition gate all at once, so it appears on roughly 1 claim in
2000. `TestBatchActuallyExercisesTheRules` deliberately does not assert it is
present — at that rate the assertion would be flaky rather than protective.

Its entry in `INPATIENT_ONLY_CONDITION_CODES` looks redundant next to those
gates but is not: status `02` is in the outpatient discharge weights and every
outpatient claim is same-day, so both other gates pass on an outpatient bill.

An outpatient claim can carry discharge status 20 and therefore occurrence 55.
That is intended: a patient dying at home under home health care (facility 32),
or in a hospital outpatient department (13), is billed exactly that way. The
death is dated at the statement through date, since an outpatient claim has no
discharge time.

## Notes on the generator

- The statement period collapses to a single day on a same-day stay and on
  every forced-CPT path. Date windows are clamped rather than offset blindly,
  or a span walks straight out of a zero-length window.
- Candidate pools shrink once filtered, so draws use
  `random.sample(pool, randint(1, min(n, len(pool))))`. Asking for more entries
  than a filtered pool holds raises `ValueError`.
- Mutually exclusive pairs are resolved after the draw, not by filtering:
  `random.sample` guarantees distinctness, not compatibility. There is no
  retry loop; a rule that seems to need one is in the wrong place in the order.
- A returning patient's context is replayed at other service dates, including
  months earlier than the one it was built for, which for a patient born this
  year would bill a visit from before they existed. `rebase_patient_context`
  moves the encounter up to the birth date rather than the birth date back to
  the encounter: ageing the patient to fit the visit would put two dates of
  birth on one member id.
- Inpatient length of stay is 1-5 nights, with a 5% chance of a same-day admit
  and discharge. Same-day is the only shape in which condition 40 can be
  reported, but drawing the stay flat over 0-5 would make one inpatient stay in
  six a zero-night stay.
- Outpatient claims report occurrence spans at a lower rate than inpatient ones
  (8% against 35%). The pool filter leaves exactly one span reportable on an
  outpatient bill, so at the inpatient rate code 73 alone would be the majority
  of every span generated.

## Not enforced yet

These are real constraints that this pass deliberately leaves open.

- **MS-DRG against the principal diagnosis.** Of the 8 MS-DRGs and 12 ICD-10
  codes currently in `basic_codes.py`, only 470 ↔ M17.11 is a defensible
  pairing; the other seven DRGs have no partner in the list. A real mapping
  needs roughly seven new inpatient diagnosis codes. Until then the DRG is
  drawn at random on acute inpatient claims. Related: a surgical DRG should
  require its defining ICD-10-PCS code in FL 74, and a medical DRG should
  exclude major OR procedures.
- **Value code amounts.** They are not checked against the claim charge, and
  codes 50 and 80 are counts of visits and days rather than dollar amounts —
  only 80 is currently generated as a count.
- **Pairings between code families:** condition 38 should imply value 01, span
  76 should imply value 31, occurrence 02 should imply value 14, and value 06
  (Medicare blood deductible) should imply a Medicare payer.
- **Occurrence 18** (date of retirement) is dated within days of the encounter
  rather than years earlier, and is not checked against the patient's age.
- **Discharge status 30 with frequency code 1.** A final original bill for a
  patient who has not been discharged is contradictory; it should be an interim
  frequency. Left alone because downstream consumers may key on `"1"` for
  originals. Any rule here must be scoped to `frequency == "1"`, or it will
  flag every claim from `generate_revised_claim`, which forces frequency 7.
