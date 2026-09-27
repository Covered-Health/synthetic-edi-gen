"""Tests for synthetic_edi_gen.generate."""

import json
from datetime import datetime

from synthetic_edi_gen.basic_codes import REJECTION_RARCS_BY_CARC
from synthetic_edi_gen.generate import (
    SplitFileWriter,
    _merge_payment_dicts,
    _plan_har_groups,
    generate,
    write_jsonl,
)

_EXPORT_DATETIME = datetime(2025, 6, 2)


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

    def test_uses_claims_per_patient_distribution(self, monkeypatch):
        monkeypatch.setattr(
            "synthetic_edi_gen.generate.sample_count", lambda *args, **kwargs: 3
        )

        assert _plan_har_groups(8) == [3, 3, 2]

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


class TestPaymentMerging:
    def test_merge_payment_dicts_adds_secondary_paid_amounts_by_line(self):
        primary = {
            "paymentAmount": 60.0,
            "claimStatusCode": "19",
            "claimStatus": "PRIMARY_FORWARDED",
            "serviceLines": [
                {
                    "sourceLineId": "LINE1",
                    "paidAmount": 60.0,
                    "adjustments": [{"group": "CONTRACTUAL"}],
                }
            ],
        }
        secondary = {
            "paymentAmount": 25.0,
            "serviceLines": [
                {
                    "sourceLineId": "LINE1",
                    "paidAmount": 25.0,
                    "adjustments": [{"group": "PATIENT_RESPONSIBILITY"}],
                }
            ],
        }

        merged = _merge_payment_dicts(primary, secondary)

        assert merged["paymentAmount"] == 85.0
        assert merged["claimStatus"] == "PRIMARY"
        assert merged["serviceLines"][0]["paidAmount"] == 85.0
        assert len(merged["serviceLines"][0]["adjustments"]) == 2


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

    def test_dates_csv_ar_filename_from_export_datetime(self, tmp_path):
        output_dir = tmp_path / "output"
        generate(
            count=5,
            output_dir=output_dir,
            seed=42,
            export_datetime=_EXPORT_DATETIME,
        )

        assert (output_dir / "openar_20250602.csv").exists()

    def test_creates_xlsx_when_requested(self, tmp_path):
        output_dir = tmp_path / "output"
        generate(
            count=5,
            output_dir=output_dir,
            seed=42,
            ar_format="xlsx",
            export_datetime=_EXPORT_DATETIME,
        )
        assert (output_dir / "openar_20250602.xlsx").exists()

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

    def test_institutional_claim_rate_mixes_837i_records(self, tmp_path):
        output_dir = tmp_path / "output"
        generate(
            count=50,
            output_dir=output_dir,
            seed=42,
            institutional_claim_rate=0.5,
            unmatched_ar_rate=0.0,
            export_datetime=_EXPORT_DATETIME,
        )

        claims = [
            json.loads(line) for line in _read_all_jsonl(output_dir, "837_claims")
        ]
        claim_types = {c["transaction"]["transactionType"] for c in claims}
        assert claim_types == {"PROF", "INST"}

        institutional_claims = [
            c for c in claims if c["transaction"]["transactionType"] == "INST"
        ]
        assert institutional_claims
        for claim in institutional_claims:
            assert claim["transaction"]["implementationConventionReference"] == (
                "005010X223A2"
            )
            assert claim["facilityCode"]["subType"] == "UB_FACILITY_TYPE"
            assert all(line["revenueCode"] for line in claim["serviceLines"])

        import csv

        with open(output_dir / "openar_20250602.csv") as f:
            rows = list(csv.DictReader(f.readlines()[9:]))
        assert any(row["Claim Form Type"] == "UB Claim" for row in rows)

    def test_payments_are_valid_json(self, tmp_path):
        output_dir = tmp_path / "output"
        generate(count=5, output_dir=output_dir, seed=42)

        for line in _read_all_jsonl(output_dir, "835_payments"):
            if line:
                record = json.loads(line)
                assert record["objectType"] == "PAYMENT"

    def test_generates_clearinghouse_rejections(self, tmp_path):
        output_dir = tmp_path / "output"
        generate(
            count=150,
            output_dir=output_dir,
            seed=42,
            match_rate=1.0,
            secondary_payer_payment_rate=0.0,
        )

        payments = [
            json.loads(line) for line in _read_all_jsonl(output_dir, "835_payments")
        ]
        assert any(
            line["adjustments"][0]["reason"]["code"] in REJECTION_RARCS_BY_CARC
            and set(line.get("remarkCodes") or [])
            & set(REJECTION_RARCS_BY_CARC[line["adjustments"][0]["reason"]["code"]])
            for payment in payments
            for line in payment.get("serviceLines", [])
            if line.get("adjustments")
        )

    def test_secondary_payer_rate_adds_extra_835s(self, tmp_path):
        output_dir = tmp_path / "output"
        generate(
            count=5,
            output_dir=output_dir,
            seed=42,
            match_rate=1.0,
            secondary_payer_payment_rate=1.0,
            revised_claim_rate=0.0,
            unmatched_ar_rate=0.0,
        )

        payments = [
            json.loads(line) for line in _read_all_jsonl(output_dir, "835_payments")
        ]
        assert len(payments) == 10
        assert any(p["claimStatus"] == "SECONDARY" for p in payments)

    def test_revised_claim_rate_adds_replacement_claims(self, tmp_path):
        output_dir = tmp_path / "output"
        generate(
            count=150,
            output_dir=output_dir,
            seed=42,
            match_rate=1.0,
            secondary_payer_payment_rate=0.0,
            revised_claim_rate=1.0,
            unmatched_ar_rate=0.0,
        )

        claims = [
            json.loads(line) for line in _read_all_jsonl(output_dir, "837_claims")
        ]
        revised = [c for c in claims if c["frequencyCode"]["code"] == "7"]
        assert revised
        assert all(c["originalReferenceNumber"] for c in revised)

    def test_unmatched_ar_rows_generated(self, tmp_path):
        output_dir = tmp_path / "output"
        generate(
            count=20,
            output_dir=output_dir,
            seed=42,
            unmatched_ar_rate=0.10,
            export_datetime=_EXPORT_DATETIME,
        )
        assert (output_dir / "openar_20250602.csv").exists()

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
        import csv

        # Generate enough claims to get some multi-PCN groups
        output_dir = tmp_path / "output"
        generate(
            count=200,
            output_dir=output_dir,
            seed=42,
            export_datetime=_EXPORT_DATETIME,
        )

        claims = {
            claim["patientControlNumber"]: claim
            for claim in (
                json.loads(line) for line in _read_all_jsonl(output_dir, "837_claims")
            )
        }
        with open(output_dir / "openar_20250602.csv") as handle:
            rows = list(csv.reader(handle))
        headers = rows[9]
        har_index = headers.index("Hospital Account ID")
        pcn_index = headers.index("Invoice Number")
        by_har: dict[str, set[str]] = {}
        for row in rows[10:]:
            if row[pcn_index] in claims:
                by_har.setdefault(row[har_index], set()).add(row[pcn_index])

        multi_groups = [pcns for pcns in by_har.values() if len(pcns) > 1]
        assert len(multi_groups) > 0, "Expected some multi-PCN groups"

        patients = [claims[pcn]["patient"]["person"] for pcn in multi_groups[0]]
        assert all(patient == patients[0] for patient in patients[1:])

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
            secondary_payer_payment_rate=0.0,
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
