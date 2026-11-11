# Hair loss reports for hair-care products to the FDA, 2004-2025

Analysis code for a research letter on FDA cosmetic adverse event reports: what mandatory (MoCRA) reports contain, and hair loss reports for hair-care products by brand and year.

## Run

Python 3.9+ standard library only. The first run downloads the openFDA bulk file (3.6 MB) into `data/`.

```bash
python3 -I pipeline.py            # main analysis -> out/
python3 -I pipeline.py --dedup    # sensitivity analysis without candidate duplicates -> out_dedup/
python3 -I audit.py > out/audit_results.txt   # recount, sensitivity analyses, L'Oréal and missing-data audits, reviewer sheet
python3 -I verify_v3.py manuscript_v3_2/manuscript_v3_2.html --api   # every number in the letter, from raw records and the live API
```

Outputs in `out/`: tables 1-6 (CSV), `table4b_ror_within_hair.csv` (hair products vs other hair products), `table7_before_after_mocra.csv` (before vs after December 29, 2023), `duplicates_candidates.csv`, `products_classified.csv`, `figure1.svg`, and `figure1.html`.

Figure 1 (panel A: reports by brand group; panel B: share labeled expedited and share missing age; `figure1a` and `figure1b` are the separate panels) as a vector PDF (7 x 5 in, Arial), using Chrome:

```bash
"/Applications/Google Chrome.app/Contents/MacOS/Google Chrome" --headless --no-pdf-header-footer --print-to-pdf=out/figure1.pdf "file://$PWD/out/figure1.html"
```

## Classification check

The dictionaries were frozen November 5, 2026, before any human review. See `review/KAPPA_VALIDATION_PLAN.md`. `review/kappa_packet/` is the blinded reviewer packet, and the answer key is kept apart in `review/kappa_key_DO_NOT_SHARE/`. After review:

```bash
python3 -I kappa_validation.py review/kappa_ratings/kappa_reviewer_workbook_completed_molly_mishulin.xlsx
```

Check the statistics at any time with `python3 -I kappa_validation.py --selftest`. Only `build_kappa_packet.py` needs a package outside the standard library (`openpyxl`), and it has already been run.

## Data sharing statement

Data: all reports are public in the openFDA cosmetic adverse event bulk file (export dated October 26, 2026; https://open.fda.gov/license/ for terms). Code: this repository, which reproduces every table and figure from that file.

## Versions

- `manuscript_v3_2/`: current letter source, figure and panel files. `Hassan_et_al_hair_loss_MoCRA_manuscript_v3_2.pdf` is built from it.
- `manuscript_v3_1/`: V3.1, with kappa added.
- `manuscript_v3/`: V3, before kappa.
- `archive_v2/`: V2 code, outputs and source. The dictionary changed for V3, so V2 numbers only reproduce from `archive_v2/pipeline_v2.py`.
- `review/V3_AUDIT_REPORT.md`: what was verified, corrected and left unresolved.
- `archive_human_validation_2026-11-05/`: read-only, checksummed record of the human validation and independent check, as of V3.1.

## Not in this repository

- `data/`: the openFDA bulk file. `pipeline.py` downloads it on first run.
- The manuscript source (`manuscript_v3_2/` and earlier versions), which `verify_v3.py` checks against.
- `review/`: the blinded reviewer packet, the classification answer key, the completed reviewer workbook, and internal audit notes. The kappa steps in `kappa_validation.py` and `verify_v3.py` need some of these files.
- `archive_human_validation_2026-11-05/`, and everything in `archive_v2/` except `pipeline_v2.py`.
