"""Tests for synthetic_edi_gen.openar_generator."""

import csv
import random
from datetime import date, datetime, timedelta

import pandas as pd
import pytest
from conftest import (
    make_claim_dict,
    make_denial_payment_dict,
    make_payment_dict,
    make_payment_line,
    make_service_line,
)

from synthetic_edi_gen.openar_generator import (
    FINANCIAL_CLASS_MAP,
    OPENAR_COLUMNS,
    OpenARGenerator,
    write_openar_csv,
    write_openar_xlsx,
)

FIXED_DT = datetime(2026, 3, 6, 7, 58, 0)

# ── Age bucket logic ──────────────────────────────────────────────────


class TestGetAgeBucket:
    @pytest.fixture()
    def gen(self):
        return OpenARGenerator(seed=42)

    @pytest.mark.parametrize(
        ("days", "expected_label"),
        [
            (0, "Less than 31 days"),
            (15, "Less than 31 days"),
            (30, "Less than 31 days"),
            (31, "31 days or more and less than 61 days"),
            (60, "31 days or more and less than 61 days"),
            (61, "61 days or more and less than 91 days"),
            (365, "365 days or more"),
            (1000, "365 days or more"),
        ],
    )
    def test_age_bucket_boundaries(self, gen, days, expected_label):
        assert gen._get_age_bucket(days) == expected_label


# ── Denial detection ──────────────────────────────────────────────────


class TestPaymentLineIsDenial:
    @pytest.fixture()
    def gen(self):
        return OpenARGenerator(seed=42)

    def test_zero_paid_with_denial_carc_is_denial(self, gen):
        line = make_payment_line(
            paid_amount=0.0,
            adjustments=[
                {"reason": {"code": "16"}, "amount": 100.0},
            ],
        )
        assert gen._payment_line_is_denial(line) is True

    def test_nonzero_paid_is_not_denial(self, gen):
        line = make_payment_line(
            paid_amount=50.0,
            adjustments=[
                {"reason": {"code": "16"}, "amount": 100.0},
            ],
        )
        assert gen._payment_line_is_denial(line) is False

    def test_zero_paid_without_denial_carc_is_not_denial(self, gen):
        line = make_payment_line(
            paid_amount=0.0,
            adjustments=[
                {"reason": {"code": "45"}, "amount": 100.0},
            ],
        )
        assert gen._payment_line_is_denial(line) is False

    def test_no_adjustments_is_not_denial(self, gen):
        line = make_payment_line(paid_amount=0.0)
        assert gen._payment_line_is_denial(line) is False


# ── AR rows from claims ───────────────────────────────────────────────


class TestGenerateArRowsForClaim:
    @pytest.fixture()
    def gen(self):
        return OpenARGenerator(seed=42)

    def test_one_row_per_service_line(self, gen):
        claim = make_claim_dict(
            service_lines=[
                make_service_line(source_line_id="L1", charge_amount=100.0),
                make_service_line(source_line_id="L2", charge_amount=200.0),
            ],
        )
        rows = gen.generate_ar_rows_for_claim(claim)
        assert len(rows) == 2

    def test_no_payment_full_charge_outstanding(self, gen):
        charge = 250.0
        claim = make_claim_dict(charge_amount=charge)
        rows = gen.generate_ar_rows_for_claim(claim, payment=None)

        assert len(rows) == 1
        assert rows[0]["Insurance Outstanding Amount ($)"] == charge

    def test_denied_payment_full_charge_outstanding(self, gen):
        charge = 300.0
        claim = make_claim_dict(pcn="DEN001", charge_amount=charge)
        payment = make_denial_payment_dict(pcn="DEN001", charge_amount=charge)

        rows = gen.generate_ar_rows_for_claim(claim, payment)
        assert rows[0]["Insurance Outstanding Amount ($)"] == charge

    def test_posted_payment_zero_outstanding(self, gen):
        # Force payment to be "posted" by making the claim very old
        old_date = (date.today() - timedelta(days=200)).isoformat()
        charge = 500.0
        paid = 350.0
        claim = make_claim_dict(
            pcn="POST01", charge_amount=charge, service_date=old_date
        )
        payment = make_payment_dict(
            pcn="POST01",
            charge_amount=charge,
            payment_amount=paid,
        )

        # Run many times since posting is probabilistic; at 200 days age, 85% chance
        random.seed(42)
        zero_count = 0
        for _ in range(50):
            rows = gen.generate_ar_rows_for_claim(claim, payment)
            if rows[0]["Insurance Outstanding Amount ($)"] == 0.0:
                zero_count += 1

        # Expect most to be posted (zero outstanding)
        assert zero_count > 30

    def test_row_fields_populated(self, gen):
        claim = make_claim_dict()
        rows = gen.generate_ar_rows_for_claim(claim)
        row = rows[0]

        assert "Slices by Service Date Age (days)" in row
        assert "MRN" in row
        assert row["MRN"]  # not empty
        assert "Hospital Account ID" in row
        assert row["Hospital Account ID"]
        assert row["Invoice Number"] == "TEST000001"
        assert row["Current Payer"] == "TEST PAYER"
        assert row["Billing Provider"] == "TEST CLINIC LLC"
        assert row["Transaction Type"] == "Charge"
        assert row["Procedure Code"] == "99213"

    def test_shared_har_id_and_mrn(self, gen):
        claim1 = make_claim_dict(pcn="HAR001")
        claim2 = make_claim_dict(pcn="HAR002")

        har_id = "12345678901"
        mrn = "999888777"

        rows1 = gen.generate_ar_rows_for_claim(
            claim1, hospital_account_id=har_id, mrn=mrn
        )
        rows2 = gen.generate_ar_rows_for_claim(
            claim2, hospital_account_id=har_id, mrn=mrn
        )

        assert rows1[0]["Hospital Account ID"] == har_id
        assert rows2[0]["Hospital Account ID"] == har_id
        assert rows1[0]["MRN"] == mrn
        assert rows2[0]["MRN"] == mrn

    def test_financial_class_from_filing_code(self, gen):
        for code, expected_class in FINANCIAL_CLASS_MAP.items():
            claim = make_claim_dict(claim_filing_code=code)
            rows = gen.generate_ar_rows_for_claim(claim)
            assert rows[0]["Current Financial Class"] == expected_class

    def test_unknown_filing_code_defaults_to_commercial(self, gen):
        claim = make_claim_dict(claim_filing_code="ZZ")
        rows = gen.generate_ar_rows_for_claim(claim)
        assert rows[0]["Current Financial Class"] == "Commercial"

    def test_referring_provider_name(self, gen):
        claim = make_claim_dict(
            providers=[
                {
                    "firstName": "JANE",
                    "lastNameOrOrgName": "SMITH",
                    "entityRole": "RENDERING",
                }
            ]
        )
        rows = gen.generate_ar_rows_for_claim(claim)
        assert rows[0]["Referring Provider"] == "SMITH, JANE"

    def test_modifiers_in_row(self, gen):
        claim = make_claim_dict(
            service_lines=[
                make_service_line(
                    modifiers=[
                        {"code": "25", "desc": "Significant E/M"},
                        {"code": "59", "desc": "Distinct service"},
                    ],
                )
            ],
        )
        rows = gen.generate_ar_rows_for_claim(claim)
        assert rows[0]["Modifiers (All)"] == "25, 59"


# ── Unmatched AR rows ─────────────────────────────────────────────────


class TestGenerateUnmatchedArRows:
    def test_generates_correct_count(self, openar_generator):
        rows = openar_generator.generate_unmatched_ar_rows(10)
        assert len(rows) == 10

    def test_full_charge_outstanding(self, openar_generator):
        rows = openar_generator.generate_unmatched_ar_rows(5)
        for row in rows:
            posted = row["Posted Amount ($)"]
            outstanding = row["Insurance Outstanding Amount ($)"]
            assert outstanding == posted

    def test_pcn_starts_with_u(self, openar_generator):
        rows = openar_generator.generate_unmatched_ar_rows(3)
        for row in rows:
            assert row["Invoice Number"].startswith("U")

    def test_has_all_required_columns(self, openar_generator):
        rows = openar_generator.generate_unmatched_ar_rows(1)
        required = {
            "Slices by Service Date Age (days)",
            "MRN",
            "Current Financial Class",
            "Procedure Code",
            "Posted Amount ($)",
            "Insurance Outstanding Amount ($)",
            "Hospital Account ID",
            "Invoice Number",
        }
        assert required.issubset(rows[0].keys())


# ── Transaction ID counter ────────────────────────────────────────────


class TestTransactionIdCounter:
    def test_increments(self):
        gen = OpenARGenerator(seed=42)
        id1 = gen._next_transaction_id()
        id2 = gen._next_transaction_id()
        assert id2 == id1 + 1


# ── Write OpenAR XLSX ─────────────────────────────────────────────────


class TestWriteOpenarXlsx:
    def test_creates_valid_xlsx(self, tmp_path, openar_generator):
        rows = openar_generator.generate_unmatched_ar_rows(5)
        output = str(tmp_path / "test.xlsx")
        write_openar_xlsx(rows, output, export_datetime=FIXED_DT)

        df = pd.read_excel(output, header=None)
        # Row 0 = Session Title, Row 9 = column headers, Row 10+ = data
        assert df.iloc[0, 0] == "Session Title"
        assert df.iloc[1, 0] == "Session ID"

    def test_data_rows_present(self, tmp_path, openar_generator):
        row_count = 3
        rows = openar_generator.generate_unmatched_ar_rows(row_count)
        output = str(tmp_path / "test.xlsx")
        write_openar_xlsx(rows, output, export_datetime=FIXED_DT)

        df = pd.read_excel(output, header=None)
        # 10 header rows + 3 data rows = 13 total
        assert len(df) == 10 + row_count

    def test_export_datetime_in_header(self, tmp_path, openar_generator):
        rows = openar_generator.generate_unmatched_ar_rows(1)
        output = str(tmp_path / "test.xlsx")
        write_openar_xlsx(rows, output, export_datetime=FIXED_DT)

        df = pd.read_excel(output, header=None)
        session_id = str(df.iloc[1, 1])
        assert "Mar" in session_id
        assert "2026" in session_id
        export_date = str(df.iloc[7, 1])
        assert "03/06/2026" in export_date

    def test_column_headers_in_row_9(self, tmp_path, openar_generator):
        rows = openar_generator.generate_unmatched_ar_rows(1)
        output = str(tmp_path / "test.xlsx")
        write_openar_xlsx(rows, output, export_datetime=FIXED_DT)

        df = pd.read_excel(output, header=None)
        headers = df.iloc[9].tolist()
        assert "Procedure Code" in headers
        assert "Insurance Outstanding Amount ($)" in headers


# ── Write OpenAR CSV ─────────────────────────────────────────────────


class TestWriteOpenarCsv:
    def _read_csv_raw(self, path: str) -> list[list[str]]:
        """Read CSV as raw rows (no header interpretation)."""
        with open(path, newline="") as f:
            return list(csv.reader(f))

    def test_header_rows_present(self, tmp_path, openar_generator):
        rows = openar_generator.generate_unmatched_ar_rows(1)
        output = str(tmp_path / "test.csv")
        write_openar_csv(rows, output, export_datetime=FIXED_DT)

        raw = self._read_csv_raw(output)
        assert raw[0][0] == "Session Title"
        assert raw[1][0] == "Session ID"
        assert "Mar" in raw[1][1]
        assert "2026" in raw[1][1]
        assert raw[7][0] == "Date of Export"
        assert raw[7][1] == "03/06/2026"
        # Row 8 is empty, row 9 is column headers
        assert raw[9][0] == OPENAR_COLUMNS[0]

    def test_data_rows_after_header(self, tmp_path, openar_generator):
        rows = openar_generator.generate_unmatched_ar_rows(5)
        output = str(tmp_path / "test.csv")
        write_openar_csv(rows, output, export_datetime=FIXED_DT)

        raw = self._read_csv_raw(output)
        # 9 header rows + 1 column header row + 5 data rows = 15
        assert len(raw) == 15

    def test_data_values_preserved(self, tmp_path, openar_generator):
        rows = openar_generator.generate_unmatched_ar_rows(1)
        output = str(tmp_path / "test.csv")
        write_openar_csv(rows, output, export_datetime=FIXED_DT)

        # Read skipping header rows (9 metadata + 1 column header = skip 9, header at row 9)
        df = pd.read_csv(output, skiprows=9)
        assert df.iloc[0]["Invoice Number"].startswith("U")
        assert df.iloc[0]["Posted Amount ($)"] > 0

    def test_has_correct_columns(self, tmp_path, openar_generator):
        rows = openar_generator.generate_unmatched_ar_rows(3)
        output = str(tmp_path / "test.csv")
        write_openar_csv(rows, output, export_datetime=FIXED_DT)

        df = pd.read_csv(output, skiprows=9)
        assert list(df.columns) == OPENAR_COLUMNS
