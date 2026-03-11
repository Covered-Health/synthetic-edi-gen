# synthetic-edi-gen

Generate synthetic EDI 835 (payment/remittance) and 837 (claim) healthcare data for testing and demos.

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
| `--seed` | Random seed for reproducibility | `None` |
| `--batch-size` | Batch size for progress reporting | `10000` |

### Output files

- `837_claims.jsonl` — one JSON object per line, each a realistic 837P professional claim
- `835_payments.jsonl` — matching 835 payment/remittance records
- `openar.xlsx` — OpenAR accounts receivable report correlated with claims/payments
