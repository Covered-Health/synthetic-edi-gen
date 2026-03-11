"""Tests for synthetic_edi_gen.generate."""

import json

from synthetic_edi_gen.generate import (
    SplitFileWriter,
    _plan_har_groups,
    generate,
    write_jsonl,
)


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


def _read_all_jsonl(output_dir, prefix):
    """Read all JSONL lines from split or single files matching a prefix."""
    import glob

    pattern = str(output_dir / f"{prefix}*.jsonl")
    lines = []
    for path in sorted(glob.glob(pattern)):
        with open(path) as fh:
            text = fh.read().strip()
        if text:
            lines.extend(text.split("\n"))
    return lines


class TestGenerate:
    def test_creates_output_files(self, tmp_path):
        output_dir = tmp_path / "output"
        generate(count=5, output_dir=output_dir, seed=42)

        # With default splitting, files get _001 suffix
        assert (output_dir / "837_claims_001.jsonl").exists()
        assert (output_dir / "835_payments_001.jsonl").exists()
        # Default AR format is csv
        assert (output_dir / "openar.csv").exists()

    def test_creates_xlsx_when_requested(self, tmp_path):
        output_dir = tmp_path / "output"
        generate(count=5, output_dir=output_dir, seed=42, ar_format="xlsx")
        assert (output_dir / "openar.xlsx").exists()

    def test_correct_claim_count(self, tmp_path):
        count = 10
        output_dir = tmp_path / "output"
        generate(count=count, output_dir=output_dir, seed=42)

        lines = _read_all_jsonl(output_dir, "837_claims")
        assert len(lines) == count

    def test_match_rate_controls_payment_count(self, tmp_path):
        count = 20
        output_dir = tmp_path / "output"
        generate(count=count, output_dir=output_dir, seed=42, match_rate=0.5)

        lines = _read_all_jsonl(output_dir, "835_payments")
        # With 50% match rate and 20 claims, expect roughly 10 payments (±5)
        assert 5 <= len(lines) <= 15

    def test_claims_are_valid_json(self, tmp_path):
        output_dir = tmp_path / "output"
        generate(count=5, output_dir=output_dir, seed=42)

        for line in _read_all_jsonl(output_dir, "837_claims"):
            record = json.loads(line)
            assert record["objectType"] == "CLAIM"
            assert "patientControlNumber" in record

    def test_payments_are_valid_json(self, tmp_path):
        output_dir = tmp_path / "output"
        generate(count=5, output_dir=output_dir, seed=42)

        for line in _read_all_jsonl(output_dir, "835_payments"):
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
        assert (output_dir / "openar.csv").exists()

    def test_seed_reproducibility(self, tmp_path):
        """Same seed produces same PCNs and charge amounts (ignoring UUIDs/timestamps)."""
        dir1 = tmp_path / "run1"
        dir2 = tmp_path / "run2"

        generate(count=5, output_dir=dir1, seed=99)
        generate(count=5, output_dir=dir2, seed=99)

        def extract_stable_fields(lines):
            records = [json.loads(raw) for raw in lines]
            return [(r["patientControlNumber"], r["chargeAmount"]) for r in records]

        fields1 = extract_stable_fields(_read_all_jsonl(dir1, "837_claims"))
        fields2 = extract_stable_fields(_read_all_jsonl(dir2, "837_claims"))
        assert fields1 == fields2

    def test_multi_pcn_har_groups_share_patient(self, tmp_path):
        # Generate enough claims to get some multi-PCN groups
        output_dir = tmp_path / "output"
        generate(count=200, output_dir=output_dir, seed=42)

        claims = []
        for line in _read_all_jsonl(output_dir, "837_claims"):
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

    def test_no_splitting_with_zero(self, tmp_path):
        """claims_per_file=0 produces single unsuffixed file."""
        output_dir = tmp_path / "output"
        generate(
            count=5,
            output_dir=output_dir,
            seed=42,
            claims_per_file=0,
            payments_per_file=0,
        )
        assert (output_dir / "837_claims.jsonl").exists()
        assert (output_dir / "835_payments.jsonl").exists()

    def test_splitting_creates_multiple_claim_files(self, tmp_path):
        output_dir = tmp_path / "output"
        generate(
            count=10,
            output_dir=output_dir,
            seed=42,
            claims_per_file=3,
        )
        # 10 claims / 3 per file = 4 files (3+3+3+1)
        import glob

        claim_files = sorted(glob.glob(str(output_dir / "837_claims_*.jsonl")))
        assert len(claim_files) == 4
        # Total lines across all files should equal count
        lines = _read_all_jsonl(output_dir, "837_claims")
        assert len(lines) == 10

    def test_splitting_creates_multiple_payment_files(self, tmp_path):
        output_dir = tmp_path / "output"
        generate(
            count=10,
            output_dir=output_dir,
            seed=42,
            match_rate=1.0,
            payments_per_file=3,
        )
        import glob

        payment_files = sorted(glob.glob(str(output_dir / "835_payments_*.jsonl")))
        # 10 payments / 3 per file = 4 files
        assert len(payment_files) == 4
        lines = _read_all_jsonl(output_dir, "835_payments")
        assert len(lines) == 10


# ── SplitFileWriter ──────────────────────────────────────────────────


class TestSplitFileWriter:
    def test_single_file_when_disabled(self, tmp_path, claim_generator):
        writer = SplitFileWriter(tmp_path, "test", ".jsonl", max_records=0)
        for _ in range(5):
            writer.write(claim_generator.generate_claim())
        writer.close()

        assert len(writer.files_created) == 1
        assert writer.files_created[0].name == "test.jsonl"
        assert writer.total_written == 5

    def test_splits_at_max_records(self, tmp_path, claim_generator):
        writer = SplitFileWriter(tmp_path, "test", ".jsonl", max_records=2)
        for _ in range(5):
            writer.write(claim_generator.generate_claim())
        writer.close()

        assert len(writer.files_created) == 3  # 2+2+1
        assert writer.files_created[0].name == "test_001.jsonl"
        assert writer.files_created[1].name == "test_002.jsonl"
        assert writer.files_created[2].name == "test_003.jsonl"

    def test_each_split_file_has_valid_jsonl(self, tmp_path, claim_generator):
        writer = SplitFileWriter(tmp_path, "test", ".jsonl", max_records=2)
        for _ in range(5):
            writer.write(claim_generator.generate_claim())
        writer.close()

        for path in writer.files_created:
            lines = path.read_text().strip().split("\n")
            for line in lines:
                record = json.loads(line)
                assert "patientControlNumber" in record
