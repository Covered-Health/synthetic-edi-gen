# synthetic-edi-gen

Generate synthetic EDI 835 (payment/remittance) and 837 (claim) healthcare data for testing and demos.

## Quick start

```bash
uvx --reinstall --from git+https://github.com/Covered-Health/synthetic-edi-gen synthetic-edi-gen --count 1000 --seed 42
```

## Usage

```bash
uv run synthetic-edi-gen --count 1000 --seed 42 --output-dir ./output
```

### Options

| Flag | Description | Default |
|------|-------------|---------|
| `--count` | Number of claims to generate | required |
| `--output-dir` | Output directory | `./edi_output` |
| `--match-rate` | Fraction of claims with matching payments (0.0-1.0) | `0.95` |
| `--unmatched-ar-rate` | Fraction of additional unmatched AR rows (0.0-1.0) | `0.05` |
| `--revised-claim-rate` | Fraction of denied/fixable claims emitted again as replacement claims (0.0-1.0) | `0.01` |
| `--secondary-payer-payment-rate` | Fraction of matched claims with an additional secondary payer 835 (0.0-1.0) | `0.10` |
| `--institutional-claim-rate` | Fraction of 837 claims emitted as 837I institutional records (0.0-1.0) | `0.30` |
| `--seed` | Random seed for reproducibility | `None` |
| `--batch-size` | Batch size for progress reporting | `10000` |

### Output files

- `837_claims.jsonl` — one JSON object per line, each a realistic 837P professional or 837I institutional claim
- `835_payments.jsonl` — matching 835 payment/remittance records
- `openar.xlsx` — OpenAR accounts receivable report correlated with claims/payments
