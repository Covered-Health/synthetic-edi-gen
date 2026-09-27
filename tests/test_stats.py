"""Focused coverage for the packaged aggregate statistics."""

import random
from datetime import date
from importlib.resources import files

import pytest

import synthetic_edi_gen.claim_generator as claim_generator_module
import synthetic_edi_gen.daily_feed as daily_feed_module
import synthetic_edi_gen.payment_generator as payment_generator_module
from synthetic_edi_gen.claim_generator import ClaimGenerator
from synthetic_edi_gen.daily_feed import DailyFeedGenerator, init_state
from synthetic_edi_gen.payment_generator import PaymentGenerator
from synthetic_edi_gen.stats import (
    _conditional_weights,
    catalog,
    load_stats,
    sample_correlated,
)


def test_packaged_v2_file_loads_once():
    stats = load_stats()

    assert stats is load_stats()
    assert stats.schema_version == 2
    assert len(stats.distributions) == 44
    assert len(stats.correlations) == 33
    assert len(stats.catalogs) == 16
    assert files("synthetic_edi_gen").joinpath("data/stats.json").is_file()


def test_capped_correlation_restores_omitted_mass():
    stats = load_stats()
    correlation, fallback = next(
        (name, distribution)
        for name, coverage in stats.correlation_coverage.items()
        for distribution in stats.distributions
        if coverage.omitted_by_condition and name.endswith(f"_{distribution}")
    )
    condition, omitted = next(
        iter(stats.correlation_coverage[correlation].omitted_by_condition.items())
    )
    weights = _conditional_weights(correlation, condition, fallback)
    retained = sum(
        count
        for pair, count in stats.correlations[correlation].items()
        if pair.rsplit("|", 1)[0] == condition
    )

    assert sum(weights.values()) == pytest.approx(retained + omitted)


def test_correlated_sampling_is_seeded_and_uses_catalog():
    conditions = (
        ("claim_type_procedure", "PROF"),
        ("gender_procedure", "FEMALE"),
    )

    first = sample_correlated("procedure", conditions, rng=random.Random(19))  # noqa: S311
    second = sample_correlated("procedure", conditions, rng=random.Random(19))  # noqa: S311

    assert first == second
    assert first in catalog("procedure")


def test_claim_and_payment_paths_call_conditional_samplers(monkeypatch):
    correlated_calls: list[tuple[str, tuple[str, ...]]] = []
    conditional_calls: list[str] = []
    original_correlated = claim_generator_module.sample_correlated
    original_conditional = payment_generator_module.sample_conditional

    def record_correlated(name, conditions, **kwargs):
        correlated_calls.append((name, tuple(item[0] for item in conditions)))
        return original_correlated(name, conditions, **kwargs)

    def record_conditional(name, value, **kwargs):
        conditional_calls.append(name)
        return original_conditional(name, value, **kwargs)

    monkeypatch.setattr(claim_generator_module, "sample_correlated", record_correlated)
    claim = ClaimGenerator(seed=41).generate_claim()
    monkeypatch.setattr(
        payment_generator_module, "sample_conditional", record_conditional
    )
    payment = PaymentGenerator(seed=41)
    monkeypatch.setattr(
        payment, "_select_payment_scenario", lambda _procedure: {"type": "full_denial"}
    )
    payment.generate_payment_for_claim(claim)

    procedure_call = next(call for call in correlated_calls if call[0] == "procedure")
    assert set(procedure_call[1]) >= {
        "claim_type_procedure",
        "provider_taxonomy_procedure",
        "age_band_procedure",
        "gender_procedure",
        "insurance_plan_procedure",
    }
    assert {"procedure_carc", "carc_group"} <= set(conditional_calls)


def test_daily_feed_uses_provider_age_correlation(monkeypatch):
    calls: list[str] = []
    original = daily_feed_module.sample_conditional

    def record(name, value, **kwargs):
        calls.append(name)
        return original(name, value, **kwargs)

    monkeypatch.setattr(daily_feed_module, "sample_conditional", record)
    state = init_state(seed=29)
    feed = DailyFeedGenerator(state, claims_per_day_min=1, claims_per_day_max=1)
    feed.process_day(date(2026, 9, 28))

    assert "provider_taxonomy_age_band" in calls
