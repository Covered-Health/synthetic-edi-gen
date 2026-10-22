"""Shared fixtures and builders for fake_data tests."""

import random

import pytest

from synthetic_edi_gen.claim_generator import ClaimGenerator
from synthetic_edi_gen.openar_generator import OpenARGenerator
from synthetic_edi_gen.payment_generator import PaymentGenerator

SEED = 42


@pytest.fixture()
def claim_generator():
    return ClaimGenerator(seed=SEED)


@pytest.fixture()
def payment_generator():
    return PaymentGenerator(seed=SEED)


@pytest.fixture()
def openar_generator():
    return OpenARGenerator(seed=SEED)


@pytest.fixture()
def sample_claim(claim_generator):
    return claim_generator.generate_claim()


@pytest.fixture()
def sample_claim_payment_pair(claim_generator, payment_generator):
    claim = claim_generator.generate_claim()
    payment = payment_generator.generate_payment_for_claim(claim)
    return claim, payment


# ---------------------------------------------------------------------------
# Builders for constructing claim/payment dicts used by OpenARGenerator
# ---------------------------------------------------------------------------


def make_service_line(
    *,
    source_line_id: str = "LINE1",
    charge_amount: float = 150.0,
    procedure_code: str = "99213",
    procedure_desc: str = "Office visit",
    modifiers: list[dict] | None = None,
    service_date: str | None = None,
) -> dict:
    procedure = {"subType": "CPT", "code": procedure_code, "desc": procedure_desc}
    if modifiers is not None:
        procedure["modifiers"] = modifiers
    line = {
        "sourceLineId": source_line_id,
        "chargeAmount": charge_amount,
        "procedure": procedure,
    }
    if service_date:
        line["serviceDateFrom"] = service_date
    return line


def make_claim_dict(
    *,
    pcn: str = "TEST000001",
    service_date: str = "2025-01-15",
    charge_amount: float = 150.0,
    service_lines: list[dict] | None = None,
    payer_name: str = "TEST PAYER",
    payer_identifier: str = "PAY001",
    claim_filing_code: str = "CI",
    billing_provider_name: str = "TEST CLINIC LLC",
    providers: list[dict] | None = None,
    facility_code: str = "11",
    object_type: str = "CLAIM",
) -> dict:
    if service_lines is None:
        service_lines = [
            make_service_line(charge_amount=charge_amount),
        ]
    if providers is None:
        providers = [
            {
                "firstName": "JOHN",
                "lastNameOrOrgName": "DOE",
                "entityRole": "RENDERING",
            }
        ]
    return {
        "objectType": object_type,
        "patientControlNumber": pcn,
        "serviceDateFrom": service_date,
        "chargeAmount": charge_amount,
        "subscriber": {
            "payer": {
                "lastNameOrOrgName": payer_name,
                "identifier": payer_identifier,
            },
            "claimFilingIndicatorCode": claim_filing_code,
        },
        "billingProvider": {"lastNameOrOrgName": billing_provider_name},
        "providers": providers,
        "facilityCode": {"code": facility_code},
        "serviceLines": service_lines,
    }


def make_payment_line(
    *,
    source_line_id: str = "LINE1",
    charge_amount: float = 150.0,
    paid_amount: float = 100.0,
    adjustments: list[dict] | None = None,
) -> dict:
    line = {
        "sourceLineId": source_line_id,
        "chargeAmount": charge_amount,
        "paidAmount": paid_amount,
    }
    if adjustments is not None:
        line["adjustments"] = adjustments
    return line


def make_payment_dict(
    *,
    pcn: str = "TEST000001",
    charge_amount: float = 150.0,
    payment_amount: float = 100.0,
    claim_status_code: str = "1",
    claim_status: str = "PRIMARY",
    service_lines: list[dict] | None = None,
    object_type: str = "PAYMENT",
) -> dict:
    if service_lines is None:
        service_lines = [
            make_payment_line(
                charge_amount=charge_amount,
                paid_amount=payment_amount,
            ),
        ]
    return {
        "objectType": object_type,
        "patientControlNumber": pcn,
        "chargeAmount": charge_amount,
        "paymentAmount": payment_amount,
        "claimStatusCode": claim_status_code,
        "claimStatus": claim_status,
        "serviceLines": service_lines,
    }


def make_denial_payment_dict(
    *,
    pcn: str = "TEST000001",
    charge_amount: float = 150.0,
) -> dict:
    """Build a payment dict representing a fully denied claim."""
    return make_payment_dict(
        pcn=pcn,
        charge_amount=charge_amount,
        payment_amount=0.0,
        claim_status_code="2",
        claim_status="DENIED",
        service_lines=[
            make_payment_line(
                charge_amount=charge_amount,
                paid_amount=0.0,
                adjustments=[
                    {
                        "group": "CONTRACTUAL_OBLIGATION",
                        "reason": {
                            "subType": "CARC",
                            "code": "16",
                            "desc": "Missing info",
                        },
                        "amount": charge_amount,
                    }
                ],
            )
        ],
    )


@pytest.fixture()
def seeded_random():
    """Seed random module and restore state after test."""
    state = random.getstate()
    random.seed(SEED)
    yield
    random.setstate(state)


@pytest.fixture(autouse=True)
def synthetic_stats(monkeypatch):
    """Keep unit tests independent of changes to the measured export."""
    from pathlib import Path

    from synthetic_edi_gen import episodes, stats

    clearers = [
        value.cache_clear
        for value in vars(stats).values()
        if hasattr(value, "cache_clear")
    ]
    for clear in clearers:
        clear()
    fixture = stats.Stats.model_validate_json(
        (Path(__file__).parent / "fixtures/stats-v2.json").read_bytes()
    )
    monkeypatch.setattr(stats, "load_stats", lambda: fixture)
    monkeypatch.setattr(episodes, "load_stats", lambda: fixture)
    yield fixture
    for clear in clearers:
        clear()


@pytest.fixture()
def episode_stats(synthetic_stats):
    """Explicitly synthetic v3 data with a delayed mixed episode and return visit."""
    from synthetic_edi_gen.stats import CorrelationCoverage, PopulationMetric

    stats = synthetic_stats
    stats.schema_version = 3
    stats.distributions.update(
        {
            "encounters_per_patient": {"2": 10},
            "claims_per_encounter": {"2": 10},
            "encounter_setting": {"outpatient": 10},
            "encounter_claim_type_mix": {"INST+PROF": 10},
            "encounter_span_days": {"0": 10},
            "encounter_return_gap_days": {"7": 10},
            "encounter_claim_service_offset_days": {"0": 10},
            "encounter_claim_submission_offset_days": {"0": 10, "3": 10},
        }
    )
    rows = {
        "encounter_setting_claims_per_encounter": {"outpatient|2": 10},
        "encounter_procedure_claims_per_encounter": {},
        "encounter_setting_claim_type_mix": {"outpatient|INST+PROF": 10},
        "encounter_setting_claim_type": {"outpatient|INST": 10},
        "encounter_provider_taxonomy_claim_type": {},
        "encounter_claim_type_claim_service_offset_days": {"INST|0": 10, "PROF|0": 10},
        "encounter_claim_type_claim_submission_offset_days": {
            "INST|0": 10,
            "PROF|3": 10,
        },
    }
    stats.correlations.update(rows)
    for name, pairs in rows.items():
        totals = {}
        for pair, count in pairs.items():
            condition = pair.rsplit("|", 1)[0]
            totals[condition] = totals.get(condition, 0) + count
        stats.correlation_coverage[name] = CorrelationCoverage(
            dimensions=["condition", "outcome"],
            total_observations=sum(pairs.values()),
            retained_observations=sum(pairs.values()),
            conditioning_totals=totals,
            omitted_by_condition={},
        )
    stats.population_metrics["encounters_per_patient"] = PopulationMetric(
        sample_count=10,
        mean=2,
        stddev=0,
        observation_window_days=365,
        eligible_count=10,
        excluded_count=0,
    )
    stats.semantics.grouping_semantics = {"encounter": "Synthetic test encounters only"}
    return stats
