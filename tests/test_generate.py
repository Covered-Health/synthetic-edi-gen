"""Tests for synthetic_edi_gen.generate."""

import json

from synthetic_edi_gen.generate import _plan_har_groups, generate, write_jsonl


class TestPlanHarGroups:
    def test_sum_equals_total(self):
        for total in [1, 5, 10, 50, 100, 500]:
            groups = _plan_har_groups(total)
            assert sum(groups) == total

    def test_all_groups_positive(self):
        groups = _plan_har_groups(100)
        assert all(g > 0 for g in groups)

    def test_single_claim(self):
        groups = _plan_har_groups(1)
        assert groups == [1]

    def test_mostly_single_pcn_groups(self):
        # Given the distribution, most groups should be size 1
        groups = _plan_har_groups(1000)
        single_pcn = sum(1 for g in groups if g == 1)
        # At least 90% should be single-PCN (production data says ~97%)
        assert single_pcn / len(groups) > 0.90

    def test_some_multi_pcn_groups_in_large_sample(self):
        groups = _plan_har_groups(10000)
        multi_pcn = sum(1 for g in groups if g > 1)
        assert multi_pcn > 0


class TestWriteJsonl:
    def test_writes_json_line(self, tmp_path, claim_generator):
        claim = claim_generator.generate_claim()
        output = tmp_path / "test.jsonl"

        with open(output, "w") as f:
            write_jsonl(f, claim)

        lines = output.read_text().strip().split("\n")
        assert len(lines) == 1
        parsed = json.loads(lines[0])
        assert parsed["patientControlNumber"] == claim.patient_control_number

    def test_writes_multiple_lines(self, tmp_path, claim_generator):
        output = tmp_path / "test.jsonl"
        claims = [claim_generator.generate_claim() for _ in range(3)]

        with open(output, "w") as f:
            for claim in claims:
                write_jsonl(f, claim)

        lines = output.read_text().strip().split("\n")
        assert len(lines) == 3


class TestGenerate:
    def test_creates_output_files(self, tmp_path):
        output_dir = tmp_path / "output"
        generate(count=5, output_dir=output_dir, seed=42)

        assert (output_dir / "837_claims.jsonl").exists()
        assert (output_dir / "835_payments.jsonl").exists()
        assert (output_dir / "openar.xlsx").exists()

    def test_correct_claim_count(self, tmp_path):
        count = 10
        output_dir = tmp_path / "output"
        generate(count=count, output_dir=output_dir, seed=42)

        claims_file = output_dir / "837_claims.jsonl"
        lines = [line for line in claims_file.read_text().strip().split("\n") if line]
        assert len(lines) == count

    def test_match_rate_controls_payment_count(self, tmp_path):
        count = 20
        output_dir = tmp_path / "output"
        generate(count=count, output_dir=output_dir, seed=42, match_rate=0.5)

        payments_file = output_dir / "835_payments.jsonl"
        lines = [line for line in payments_file.read_text().strip().split("\n") if line]
        # With 50% match rate and 20 claims, expect roughly 10 payments (±5)
        assert 5 <= len(lines) <= 15

    def test_claims_are_valid_json(self, tmp_path):
        output_dir = tmp_path / "output"
        generate(count=5, output_dir=output_dir, seed=42)

        claims_file = output_dir / "837_claims.jsonl"
        for line in claims_file.read_text().strip().split("\n"):
            record = json.loads(line)
            assert record["objectType"] == "CLAIM"
            assert "patientControlNumber" in record

    def test_payments_are_valid_json(self, tmp_path):
        output_dir = tmp_path / "output"
        generate(count=5, output_dir=output_dir, seed=42)

        payments_file = output_dir / "835_payments.jsonl"
        for line in payments_file.read_text().strip().split("\n"):
            if line:
                record = json.loads(line)
                assert record["objectType"] == "PAYMENT"

    def test_unmatched_ar_rows_generated(self, tmp_path):
        output_dir = tmp_path / "output"
        generate(
            count=20,
            output_dir=output_dir,
            seed=42,
            unmatched_ar_rate=0.10,
        )
        # Just verify the xlsx was created; detailed content tested in openar tests
        assert (output_dir / "openar.xlsx").exists()

    def test_seed_reproducibility(self, tmp_path):
        """Same seed produces same PCNs and charge amounts (ignoring UUIDs/timestamps)."""
        dir1 = tmp_path / "run1"
        dir2 = tmp_path / "run2"

        generate(count=5, output_dir=dir1, seed=99)
        generate(count=5, output_dir=dir2, seed=99)

        def extract_stable_fields(claims_text):
            records = [json.loads(raw) for raw in claims_text.strip().split("\n")]
            return [(r["patientControlNumber"], r["chargeAmount"]) for r in records]

        fields1 = extract_stable_fields((dir1 / "837_claims.jsonl").read_text())
        fields2 = extract_stable_fields((dir2 / "837_claims.jsonl").read_text())
        assert fields1 == fields2

    def test_multi_pcn_har_groups_share_patient(self, tmp_path):
        # Generate enough claims to get some multi-PCN groups
        output_dir = tmp_path / "output"
        generate(count=200, output_dir=output_dir, seed=42)

        claims_file = output_dir / "837_claims.jsonl"
        claims = []
        for line in claims_file.read_text().strip().split("\n"):
            claims.append(json.loads(line))

        # Group claims by billing provider NPI (shared within HAR group)
        by_npi = {}
        for c in claims:
            npi = c["billingProvider"]["identifier"]
            by_npi.setdefault(npi, []).append(c)

        # Find a group with >1 claim
        multi_groups = {npi: cs for npi, cs in by_npi.items() if len(cs) > 1}
        assert len(multi_groups) > 0, "Expected some multi-PCN groups"

        # Verify shared demographics in first multi-PCN group found
        for _npi, group_claims in multi_groups.items():
            first_patient = group_claims[0]["patient"]["person"]
            for claim in group_claims[1:]:
                patient = claim["patient"]["person"]
                assert patient["firstName"] == first_patient["firstName"]
                assert (
                    patient["lastNameOrOrgName"] == first_patient["lastNameOrOrgName"]
                )
            break  # only need to verify one group
