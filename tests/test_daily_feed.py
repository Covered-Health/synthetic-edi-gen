"""Tests for the daily EDI feed simulation."""

import json
from datetime import date, timedelta
from pathlib import Path

import pytest

from synthetic_edi_gen.daily_feed import (
    DailyFeedGenerator,
    _day_seed,
    _is_business_day,
    _today_eastern,
    daily_feed,
    init_state,
)
from synthetic_edi_gen.feed_state import FeedState, load_state, save_state


class TestDaySeed:
    def test_deterministic(self):
        a = _day_seed(42, date(2025, 6, 1), phase=0)
        b = _day_seed(42, date(2025, 6, 1), phase=0)
        assert a == b

    def test_different_dates_differ(self):
        a = _day_seed(42, date(2025, 6, 1), phase=0)
        b = _day_seed(42, date(2025, 6, 2), phase=0)
        assert a != b

    def test_different_phases_differ(self):
        a = _day_seed(42, date(2025, 6, 1), phase=0)
        b = _day_seed(42, date(2025, 6, 1), phase=1)
        assert a != b

    def test_different_seeds_differ(self):
        a = _day_seed(42, date(2025, 6, 1), phase=0)
        b = _day_seed(99, date(2025, 6, 1), phase=0)
        assert a != b


class TestIsBusinessDay:
    def test_weekday(self):
        assert _is_business_day(date(2025, 6, 2))  # Monday

    def test_weekend(self):
        assert not _is_business_day(date(2025, 6, 1))  # Sunday
        assert not _is_business_day(date(2025, 5, 31))  # Saturday


class TestTodayEastern:
    def test_returns_date(self):
        result = _today_eastern()
        assert isinstance(result, date)


class TestInitState:
    def test_creates_valid_state(self):
        state = init_state(seed=42)
        assert isinstance(state, FeedState)
        assert state.seed == 42
        assert state.organization.name == "ADVANCED ORTHOPEDIC & SURGICAL ASSOCIATES PA"
        assert len(state.providers) == 13
        assert state.last_run_date is None
        assert state.total_claims_submitted == 0

    def test_deterministic(self):
        a = init_state(seed=42)
        b = init_state(seed=42)
        assert a.organization.npi == b.organization.npi
        assert a.providers[0].npi == b.providers[0].npi

    def test_different_seeds(self):
        a = init_state(seed=42)
        b = init_state(seed=99)
        assert a.organization.npi != b.organization.npi

    def test_hire_dates_are_relative_to_simulation_start(self):
        start = date(2025, 6, 2)
        state = init_state(seed=42, start_date=start)
        assert all(provider.hired_date < start for provider in state.providers)


class TestStateRoundTrip:
    def test_save_and_load(self, tmp_path: Path):
        state = init_state(seed=42)
        path = tmp_path / "state.json"
        save_state(state, path)
        json.loads(path.read_text())

        loaded = load_state(path)
        assert loaded.seed == state.seed
        assert loaded.organization.name == state.organization.name
        assert len(loaded.providers) == len(state.providers)
        assert loaded.providers[0].npi == state.providers[0].npi

    def test_round_trip_with_data(self, tmp_path: Path):
        state = init_state(seed=42)
        feed = DailyFeedGenerator(state)
        feed.process_day(date(2025, 6, 2))

        path = tmp_path / "state.json"
        save_state(state, path)
        loaded = load_state(path)

        assert loaded.total_claims_submitted == state.total_claims_submitted
        assert len(loaded.pending_claims) == len(state.pending_claims)
        assert len(loaded.ar_items) == len(state.ar_items)
        assert loaded.last_run_date == date(2025, 6, 2)

    def test_rejects_unknown_extension(self, tmp_path: Path):
        with pytest.raises(ValueError, match=r"\.json extension"):
            save_state(init_state(seed=42), tmp_path / "state.yaml")


class TestDailyFeedGenerator:
    def test_claims_per_day_range_override(self):
        state = init_state(seed=42)
        feed = DailyFeedGenerator(state, claims_per_day_min=3, claims_per_day_max=5)
        claims, _ = feed.process_day(date(2025, 6, 2))
        assert 3 <= len(claims) <= 5

    def test_claims_per_day_rejects_inverted_range(self):
        state = init_state(seed=42)
        with pytest.raises(ValueError, match="must not exceed"):
            DailyFeedGenerator(state, claims_per_day_min=5, claims_per_day_max=3)

    def test_business_day_generates_claims(self):
        state = init_state(seed=42)
        feed = DailyFeedGenerator(state)
        claims, payments = feed.process_day(date(2025, 6, 2))  # Monday
        assert len(claims) > 0
        assert state.total_claims_submitted > 0

    def test_weekend_generates_no_claims(self):
        state = init_state(seed=42)
        feed = DailyFeedGenerator(state)
        claims, payments = feed.process_day(date(2025, 6, 1))  # Sunday
        assert len(claims) == 0

    def test_pending_claims_created(self):
        state = init_state(seed=42)
        feed = DailyFeedGenerator(state)
        claims, _ = feed.process_day(date(2025, 6, 2))
        assert len(state.pending_claims) == len(claims)

    def test_ar_items_created(self):
        state = init_state(seed=42)
        feed = DailyFeedGenerator(state)
        claims, _ = feed.process_day(date(2025, 6, 2))
        assert len(state.ar_items) > 0
        # At least one AR item per claim (may be more due to multi-line)
        assert len(state.ar_items) >= len(claims)

    def test_responses_arrive_after_delay(self):
        state = init_state(seed=42)
        feed = DailyFeedGenerator(state)

        # Day 1: generate claims
        claims, _ = feed.process_day(date(2025, 6, 2))
        original_pcns = {
            p.claim_data["patientControlNumber"] for p in state.pending_claims
        }
        assert len(original_pcns) > 0

        # Advance 20 days — some original claims should get 835 responses
        for day_offset in range(1, 21):
            d = date(2025, 6, 2) + timedelta(days=day_offset)
            feed.process_day(d)

        remaining_pcns = {
            p.claim_data["patientControlNumber"] for p in state.pending_claims
        }
        resolved = original_pcns - remaining_pcns
        assert len(resolved) > 0, "Some original claims should have received 835s"

    def test_deterministic_across_runs(self):
        state_a = init_state(seed=42)
        state_b = init_state(seed=42)
        feed_a = DailyFeedGenerator(state_a)
        feed_b = DailyFeedGenerator(state_b)

        claims_a, _ = feed_a.process_day(date(2025, 6, 2))
        claims_b, _ = feed_b.process_day(date(2025, 6, 2))

        assert [claim.model_dump_json(by_alias=True) for claim in claims_a] == [
            claim.model_dump_json(by_alias=True) for claim in claims_b
        ]

        payments_a = []
        payments_b = []
        for day_offset in range(1, 21):
            day = date(2025, 6, 2) + timedelta(days=day_offset)
            _, daily_payments_a = feed_a.process_day(day)
            _, daily_payments_b = feed_b.process_day(day)
            payments_a.extend(daily_payments_a)
            payments_b.extend(daily_payments_b)

        assert payments_a
        assert [payment.model_dump_json(by_alias=True) for payment in payments_a] == [
            payment.model_dump_json(by_alias=True) for payment in payments_b
        ]

    def test_reversal_negates_payment_and_adjustments(self, monkeypatch):
        state = init_state(seed=42)
        feed = DailyFeedGenerator(state)
        claims, _ = feed.process_day(date(2025, 6, 2))
        monkeypatch.setattr(
            feed._payment_gen,
            "_select_payment_scenario",
            lambda: {"type": "partial_payment"},
        )
        original = feed._payment_gen.generate_payment_for_claim(claims[0])

        reversal = feed._build_reversal(original, date(2025, 6, 3))

        assert original.service_lines
        assert reversal.service_lines
        for original_line, reversed_line in zip(
            original.service_lines, reversal.service_lines, strict=True
        ):
            assert reversed_line.paid_amount == -original_line.paid_amount
            assert original_line.adjustments
            assert reversed_line.adjustments
            assert [a.amount for a in reversed_line.adjustments] == [
                -a.amount for a in original_line.adjustments
            ]
            assert (
                reversed_line.model_copy(
                    update={
                        "paid_amount": original_line.paid_amount,
                        "adjustments": original_line.adjustments,
                    }
                )
                == original_line
            )

    def test_billing_provider_is_org(self):
        state = init_state(seed=42)
        feed = DailyFeedGenerator(state)
        claims, _ = feed.process_day(date(2025, 6, 2))
        for claim in claims:
            assert claim.billing_provider is not None
            bp = claim.billing_provider
            assert bp.identifier == state.organization.npi
            assert bp.last_name_or_org_name == state.organization.name

    def test_rendering_provider_from_roster(self):
        state = init_state(seed=42)
        feed = DailyFeedGenerator(state)
        claims, _ = feed.process_day(date(2025, 6, 2))
        roster_npis = {p.npi for p in state.providers}
        for claim in claims:
            providers = claim.providers or []
            for prov in providers:
                if prov.entity_role == "RENDERING":
                    assert prov.identifier in roster_npis

    def test_ar_snapshot(self):
        state = init_state(seed=42)
        feed = DailyFeedGenerator(state)
        feed.process_day(date(2025, 6, 2))

        snapshot = feed.generate_ar_snapshot(date(2025, 6, 2))
        assert len(snapshot) > 0
        for row in snapshot:
            assert "Invoice Number" in row
            assert "Insurance Outstanding Amount ($)" in row
            assert row["Insurance Outstanding Amount ($)"] > 0

    def test_ar_snapshot_preserves_transaction_ids(self):
        state = init_state(seed=42)
        feed = DailyFeedGenerator(state)
        feed.process_day(date(2025, 6, 2))

        first = feed.generate_ar_snapshot(date(2025, 6, 2))
        second = feed.generate_ar_snapshot(date(2025, 6, 2))

        first_ids = [row["Professional Transaction ID"] for row in first]
        second_ids = [row["Professional Transaction ID"] for row in second]
        assert first_ids == second_ids
        assert len(first_ids) == len(set(first_ids))

    def test_partial_payment_clears_insurance_outstanding(self, monkeypatch):
        state = init_state(seed=42)
        feed = DailyFeedGenerator(state)
        claims, _ = feed.process_day(date(2025, 6, 2))
        claim = claims[0]
        monkeypatch.setattr(
            feed._payment_gen,
            "_select_payment_scenario",
            lambda: {"type": "partial_payment"},
        )

        payment = feed._payment_gen.generate_payment_for_claim(claim)
        feed._apply_payment_to_ar(
            claim.patient_control_number, payment, date(2025, 6, 7)
        )

        claim_ar = [
            item for item in state.ar_items if item.pcn == claim.patient_control_number
        ]
        assert claim_ar
        assert all(item.insurance_outstanding == 0 for item in claim_ar)
        assert all(item.closed_date is None for item in claim_ar)

    def test_closed_ar_is_retained_through_reversal_window(self):
        state = init_state(seed=42)
        feed = DailyFeedGenerator(state)
        feed.process_day(date(2025, 6, 2))
        closed_item = state.ar_items[0]
        closed_item.closed_date = date(2025, 6, 2)

        feed._cleanup_state(date(2025, 7, 31))
        assert any(item is closed_item for item in state.ar_items)

        feed._cleanup_state(date(2025, 8, 1))
        assert not any(item is closed_item for item in state.ar_items)


class TestDailyFeedCLI:
    def test_end_to_end(self, tmp_path: Path):
        state_file = tmp_path / "state.json"
        output_dir = tmp_path / "output"

        daily_feed(
            state_file=state_file,
            output_dir=output_dir,
            seed=42,
            target_date="2025-06-02",
        )

        assert state_file.exists()
        claims_file = output_dir / "837_claims_20250602.jsonl"
        payments_file = output_dir / "835_payments_20250602.jsonl"
        ar_file = output_dir / "openar_20250602.csv"

        assert claims_file.exists()
        assert payments_file.exists()
        assert ar_file.exists()

        # Verify claims are valid JSON
        with open(claims_file) as f:
            for line in f:
                data = json.loads(line)
                assert data["objectType"] == "CLAIM"
                assert "patientControlNumber" in data

    def test_incremental_run(self, tmp_path: Path):
        state_file = tmp_path / "state.json"
        output_dir = tmp_path / "output"

        # First run
        daily_feed(
            state_file=state_file,
            output_dir=output_dir,
            seed=42,
            target_date="2025-06-02",
        )
        state_1 = load_state(state_file)
        submitted_1 = state_1.total_claims_submitted

        # Second run (next day)
        daily_feed(
            state_file=state_file,
            output_dir=output_dir,
            seed=42,
            target_date="2025-06-03",
        )
        state_2 = load_state(state_file)
        assert state_2.total_claims_submitted > submitted_1
        assert state_2.last_run_date == date(2025, 6, 3)

    def test_skip_already_processed(self, tmp_path: Path, capsys):
        state_file = tmp_path / "state.json"
        output_dir = tmp_path / "output"

        daily_feed(
            state_file=state_file,
            output_dir=output_dir,
            seed=42,
            target_date="2025-06-02",
        )
        daily_feed(
            state_file=state_file,
            output_dir=output_dir,
            seed=42,
            target_date="2025-06-02",
        )
        captured = capsys.readouterr()
        assert "Already processed" in captured.out

    def test_catch_up_multiple_days(self, tmp_path: Path):
        state_file = tmp_path / "state.json"
        output_dir = tmp_path / "output"

        daily_feed(
            state_file=state_file,
            output_dir=output_dir,
            seed=42,
            target_date="2025-06-02",
        )
        # Skip to 5 days later
        daily_feed(
            state_file=state_file,
            output_dir=output_dir,
            seed=42,
            target_date="2025-06-07",
        )
        state = load_state(state_file)
        assert state.last_run_date == date(2025, 6, 7)
        # Should have claims from 5 business days (Jun 2-6 are Mon-Fri)
        assert state.total_claims_submitted > 50  # at least 20/day * 5 days - weekend
