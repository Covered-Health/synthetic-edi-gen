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
| `--institutional-claim-rate` | Override the statistics-based fraction of 837I institutional records (0.0-1.0) | statistics |
| `--provider-count` | Rendering-provider roster size; otherwise scales from 50 at 1k claims to a 3k cap | automatic |
| `--seed` | Random seed for reproducibility | `None` |
| `--batch-size` | Batch size for progress reporting | `10000` |

### Output files

- `837_claims.jsonl` — one JSON object per line, each a realistic 837P professional or 837I institutional claim
- `835_payments.jsonl` — matching 835 payment/remittance records
- `openar_YYYYMMDD.xlsx` — OpenAR accounts receivable report correlated with claims/payments

## Institutional claim constraints

837I claims are generated so their UB-04 fields agree with each other — a date
of death only appears alongside a discharge status that says the patient died,
an MS-DRG only on an acute inpatient bill, and so on. The rules are listed in
[docs/institutional-constraints.md](docs/institutional-constraints.md) and
enforced by `validate_institutional_claim`:

```python
from synthetic_edi_gen.inst_constraints import validate_institutional_claim

violations = validate_institutional_claim(claim)  # [] when the claim is consistent
```

## Daily feed simulation

Use the `daily-feed` command to maintain a persistent feed across simulated days:

```bash
uv run synthetic-edi-gen daily-feed \
  --state-file ./feed-state.json \
  --target-date 2025-06-02 \
  --claims-per-day-min 80 \
  --claims-per-day-max 120 \
  --output-dir ./daily-output
```

On later runs, the command loads the state file and processes each day since the
previous target date. It writes dated 837, 835, and OpenAR files; use
`--claims-per-day-min` and `--claims-per-day-max` to override the default 20–50
weekday volume, or set them equal for an exact count. Use
`--ar-format xlsx` to produce an Excel AR snapshot instead of CSV.

The daily feed produces both professional (837P) and institutional (837I)
claims. With v3 statistics, it samples encounters per patient separately from
claims per encounter. Return visits retain the patient's MRN and receive a new
HAR; additional claims within an encounter retain the HAR and can arrive on later
days. Due claims consume the weekday claim allowance; clearinghouse refiles are
additional submissions, as before. Claims are submitted after their billed
service period ends. Queued claims and return visits survive restarts and patient
pool eviction.

## Episode statistics

The consumer accepts statistics schema v2 and v3. The exporter supplies v3
histograms for encounter/account counts, claim multiplicity, claim-type mixes,
service/submission offsets, and return gaps, with conditional distributions by
setting, procedure, and provider taxonomy. Mean, population standard deviation,
sample size, observation window, and eligibility counts are included in
`population_metrics`; generation samples the histograms to retain their skew.

Encounter statistics are used only when the source has a documented encounter
identifier. Other HAR-linked groups are called billing episodes: a recurring
account can cover more than one clinical visit. Generation prefers the encounter
family, otherwise the billing-episode family, without mixing their conditionals.
Missing conditional observations fall back to that family's marginal. V2 exports
retain legacy patient/HAR grouping until v3 episode aggregates are installed.
V3 data without usable encounter/account linkage produces one PCN per HAR;
patient-level claim counts are not treated as observed account sizes.

Logical claims count distinct normalized PCNs with an 837, excluding void-only
PCNs and collapsing submissions for the same PCN. Explicitly linked revisions
are collapsed where supported. Secondary billing and unlinked replacement PCNs
cannot always be distinguished from independent claims; extraction coverage and
`semantics.grouping_semantics` record these limits. Dates and identifiers are not
exported in the statistics. Return gaps measure previous service end to next
service start. Unit tests use explicitly synthetic fixtures rather than measured
production distributions.
