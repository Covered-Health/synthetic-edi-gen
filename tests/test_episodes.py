"""Regression checks for patient, encounter, and claim identities."""

import json
from collections import defaultdict
from datetime import date, timedelta
from importlib.resources import files

import pytest

from synthetic_edi_gen.claim_generator import ClaimGenerator
from synthetic_edi_gen.daily_feed import DailyFeedGenerator, init_state
from synthetic_edi_gen.edi_models import InstClaim
from synthetic_edi_gen.episodes import (
    episode_family,
    generate_episode_claim,
    parse_claim,
    plan_episode,
)
from synthetic_edi_gen.feed_state import FeedState, load_state, save_state
from synthetic_edi_gen.generate import generate
from synthetic_edi_gen.inst_constraints import validate_institutional_claim
from synthetic_edi_gen.stats import PopulationMetric, Stats

START = date(2025, 6, 2)


def test_v2_and_packaged_stats_load(synthetic_stats):
    assert synthetic_stats.schema_version == 2
    assert synthetic_stats.population_metrics == {}
    assert episode_family() is None
    packaged = Stats.model_validate_json(
        files("synthetic_edi_gen").joinpath("data/stats.json").read_bytes()
    )
    assert packaged.schema_version in {2, 3}
    assert packaged.distributions["claim_type"]


def test_v3_metrics_round_trip_and_validation(episode_stats):
    stats = Stats.model_validate_json(episode_stats.model_dump_json())
    assert stats.population_metrics["encounters_per_patient"].mean == 2
    assert stats.semantics.grouping_semantics
    with pytest.raises(ValueError):
        PopulationMetric(
            sample_count=1,
            mean=float("nan"),
            stddev=0,
            eligible_count=1,
            excluded_count=0,
        )


def test_prefer_encounters_and_do_not_mix_families(episode_stats):
    episode_stats.distributions.update(
        {
            "billing_episodes_per_patient": {"50": 1},
            "claims_per_billing_episode": {"50": 1},
        }
    )
    ctx = ClaimGenerator(seed=11).generate_patient_context(service_date=START)
    assert episode_family() == "encounter"
    assert len(plan_episode(ctx).claim_types) == 2
    episode_stats.distributions["claims_per_encounter"] = {}
    assert episode_family() == "billing_episode"
    assert len(plan_episode(ctx).claim_types) == 50


def test_mixed_plan_and_institutional_constraints(episode_stats):
    generator = ClaimGenerator(seed=23)
    ctx = generator.generate_patient_context(service_date=START)
    plan = plan_episode(ctx)
    assert set(plan.claim_types) == {"INST", "PROF"}
    for index, kind in enumerate(plan.claim_types):
        claim = generate_episode_claim(
            generator, ctx, plan, index, START, submission_date=START
        )
        assert claim.transaction.transaction_type == kind
        restored = parse_claim(claim.model_dump(by_alias=True, mode="json"))
        assert type(restored) is type(claim)
        if isinstance(claim, InstClaim):
            assert validate_institutional_claim(claim) == []
            assert claim.service_date_to <= START
        else:
            assert claim.facility_code.code == "22"


def test_sparse_condition_falls_back_to_encounter_marginal(episode_stats):
    episode_stats.correlations["encounter_setting_claims_per_encounter"] = {}
    ctx = ClaimGenerator(seed=3).generate_patient_context(service_date=START)
    assert len(plan_episode(ctx).claim_types) == 2


@pytest.mark.parametrize("rate,kind", [(0, "PROF"), (1, "INST")])
def test_explicit_type_override_and_exact_regular_count(
    episode_stats, tmp_path, rate, kind
):
    generate(
        count=5,
        seed=42,
        output_dir=tmp_path,
        institutional_claim_rate=rate,
        match_rate=0,
        revised_claim_rate=0,
        unmatched_ar_rate=0,
    )
    claims = [
        json.loads(line)
        for path in tmp_path.glob("837_claims*.jsonl")
        for line in path.read_text().splitlines()
    ]
    assert len(claims) == 5
    assert {claim["transaction"]["transactionType"] for claim in claims} == {kind}


def test_regular_mode_separates_return_visits_from_claim_groups(
    episode_stats, tmp_path
):
    import csv

    generate(
        count=4,
        seed=42,
        output_dir=tmp_path,
        match_rate=0,
        revised_claim_rate=0,
        unmatched_ar_rate=0,
    )
    with next(tmp_path.glob("openar_*.csv")).open() as handle:
        rows = list(csv.DictReader(handle.readlines()[9:]))
    groups = defaultdict(set)
    patients = defaultdict(set)
    for row in rows:
        groups[row["Hospital Account ID"]].add(row["Invoice Number"])
        patients[row["MRN"]].add(row["Hospital Account ID"])
    assert sorted(map(len, groups.values())) == [2, 2]
    assert len(patients) == 1
    assert len(next(iter(patients.values()))) == 2


def test_delayed_pcns_and_return_visits_survive_restart_and_pool_eviction(
    episode_stats, tmp_path
):
    state = init_state(42, START)
    feed = DailyFeedGenerator(state, 1, 1)
    first, _ = feed.process_day(START)
    original_har = state.ar_items[0].har_id
    original_mrn = state.ar_items[0].mrn
    assert first[0].transaction.transaction_type == "INST"
    assert len(state.scheduled_claims) == 1
    scheduled = state.scheduled_claims[0]
    assert scheduled.har_id == original_har
    assert (
        scheduled.claim_data["patientControlNumber"] != first[0].patient_control_number
    )
    assert scheduled.scheduled_date > START
    assert state.return_episodes
    state.patients.clear()
    path = tmp_path / "state.json"
    save_state(state, path)
    loaded = load_state(path)
    restarted = DailyFeedGenerator(loaded, 1, 1)
    found_delayed = found_return = False
    for offset in range(1, 13):
        day = START + timedelta(days=offset)
        a = feed.process_day(day)
        b = restarted.process_day(day)
        assert [[item.model_dump(mode="json") for item in result] for result in a] == [
            [item.model_dump(mode="json") for item in result] for result in b
        ]
        assert state.model_dump(mode="json") == loaded.model_dump(mode="json")
        submitted = {claim.patient_control_number for claim in a[0]}
        for item in state.ar_items:
            if item.pcn not in submitted:
                continue
            assert item.service_date <= day
            if (
                item.har_id == original_har
                and item.pcn != first[0].patient_control_number
            ):
                found_delayed = True
            if item.mrn == original_mrn and item.har_id != original_har:
                found_return = True
    assert found_delayed
    assert found_return


def test_daily_inst_refile_payment_reversal_and_ar(episode_stats, monkeypatch):
    import synthetic_edi_gen.daily_feed as daily

    state = init_state(42, START)
    feed = DailyFeedGenerator(state, 1, 1)
    claims, _ = feed.process_day(START)
    claim = claims[0]
    assert isinstance(claim, InstClaim)
    snapshot = feed.generate_ar_snapshot(START)
    assert {row["Claim Form Type"] for row in snapshot} == {"UB Claim"}
    assert all(" - UB " in row["Place of Service"] for row in snapshot)
    assert all(
        row["Billing Provider"] == claim.billing_provider.last_name_or_org_name
        for row in snapshot
    )
    pending = state.pending_claims[0]
    pending.clearinghouse_rejection = True
    pending.scheduled_response_date = START
    monkeypatch.setattr(daily, "_REJECTED_REFILE_RATE", 1)
    refiles, rejections = feed._process_responses(START)
    assert isinstance(refiles[0], InstClaim)
    assert refiles[0].statement_date_to == claim.statement_date_to
    assert refiles[0].patient_control_number == claim.patient_control_number
    assert rejections[0].facility_code == claim.facility_code
    state.pending_claims[0].scheduled_response_date = START
    monkeypatch.setattr(
        feed._payment_gen,
        "_select_payment_scenario",
        lambda _: {"type": "full_payment"},
    )
    monkeypatch.setattr(daily, "_REVERT_SELECTION_RATE", 1)
    _, payments = feed._process_responses(START)
    assert payments[0].payment_amount > 0
    assert len(state.revert_candidates) == 1
    monkeypatch.setattr(daily, "_REVERT_DAILY_PROBABILITY", 1)
    reversals = feed._process_reverts(START)
    assert len(reversals) == 2
    assert reversals[0].payment_amount == -payments[0].payment_amount
    assert reversals[1].facility_code == claim.facility_code


def test_old_state_defaults_do_not_invent_episode_history():
    state = init_state(42, START).model_dump()
    for field in (
        "scheduled_claims",
        "return_episodes",
        "institutional_billing_provider",
    ):
        state.pop(field)
    restored = FeedState.model_validate(state)
    assert restored.scheduled_claims == []
    assert restored.return_episodes == []


def test_procedure_can_change_claim_multiplicity(episode_stats, monkeypatch):
    episode_stats.distributions["claims_per_encounter"] = {"2": 10, "4": 10}
    episode_stats.correlations["encounter_setting_claims_per_encounter"] = {}
    episode_stats.correlations["encounter_procedure_claims_per_encounter"] = {
        "80053|4": 10
    }
    monkeypatch.setattr(ClaimGenerator, "_sample_procedure", lambda *_: "80053")
    ctx = ClaimGenerator(seed=3).generate_patient_context(service_date=START)
    assert len(plan_episode(ctx).claim_types) == 4


def test_claim_type_uses_setting_without_mix_data(episode_stats):
    episode_stats.distributions["encounter_claim_type_mix"] = {}
    ctx = ClaimGenerator(seed=3).generate_patient_context(service_date=START)
    assert set(plan_episode(ctx).claim_types) == {"INST"}


def test_weekend_defers_delayed_claims_and_weekday_volume_stays_exact(
    episode_stats, monkeypatch
):
    import synthetic_edi_gen.daily_feed as daily

    monkeypatch.setattr(daily, "_CLEARINGHOUSE_REJECTION_RATE", 0)
    start = date(2025, 6, 6)  # Friday; the next PCN is due Monday.
    state = init_state(42, start)
    feed = DailyFeedGenerator(state, 1, 1)
    first, _ = feed.process_day(start)
    delayed_pcn = state.scheduled_claims[0].claim_data["patientControlNumber"]
    assert delayed_pcn != first[0].patient_control_number
    assert feed.process_day(start + timedelta(days=1))[0] == []
    assert feed.process_day(start + timedelta(days=2))[0] == []
    monday, _ = feed.process_day(start + timedelta(days=3))
    assert len(monday) == 1
    assert monday[0].patient_control_number == delayed_pcn


def test_payment_follows_institutional_service_end_and_submission(episode_stats):
    from synthetic_edi_gen.payment_generator import PaymentGenerator

    generator = ClaimGenerator(seed=42)
    ctx = generator.generate_patient_context(service_date=START)
    claim = generate_episode_claim(generator, ctx, plan_episode(ctx), 0, START)
    claim.transaction.creation_date = claim.service_date_to + timedelta(days=30)
    payment = PaymentGenerator(seed=42).generate_payment_for_claim(claim)
    assert payment.transaction.payment_date > claim.transaction.creation_date


def test_packaged_statistics_generate_regular_and_daily_outputs(monkeypatch, tmp_path):
    """Exercise the installed export as well as the controlled unit fixtures."""
    import synthetic_edi_gen.episodes as episodes
    import synthetic_edi_gen.stats as stats

    measured = Stats.model_validate_json(
        files("synthetic_edi_gen").joinpath("data/stats.json").read_bytes()
    )
    monkeypatch.setattr(stats, "load_stats", lambda: measured)
    monkeypatch.setattr(episodes, "load_stats", lambda: measured)
    state = init_state(42, START)
    feed = DailyFeedGenerator(state, 10, 10)
    for offset in range(7):
        day = START + timedelta(days=offset)
        claims, _ = feed.process_day(day)
        for claim in claims:
            assert (claim.service_date_to or claim.service_date_from) <= day
            if isinstance(claim, InstClaim):
                assert validate_institutional_claim(claim) == []
    generate(count=20, seed=42, output_dir=tmp_path, revised_claim_rate=0)
    emitted = [
        parse_claim(json.loads(line))
        for path in tmp_path.glob("837_claims*.jsonl")
        for line in path.read_text().splitlines()
    ]
    assert len(emitted) == 20
    for claim in emitted:
        assert (
            claim.service_date_to or claim.service_date_from
        ) <= claim.transaction.creation_date
        if isinstance(claim, InstClaim):
            assert validate_institutional_claim(claim) == []


@pytest.mark.parametrize(
    "bucket,expected", [("0-1", 0), ("1-2", 1), ("negative:3-7", 0)]
)
def test_offset_buckets_are_half_open_and_do_not_schedule_negative_days(
    episode_stats, bucket, expected
):
    from synthetic_edi_gen.episodes import episode_days

    episode_stats.distributions["encounter_claim_submission_offset_days"] = {bucket: 10}
    assert episode_days("encounter", "claim_submission_offset") == expected
