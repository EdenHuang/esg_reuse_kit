# Do Sustainability Report Photos Show What the Text Claims?

**A dataset and a four-level measurement of photographic evidence in Taiwanese sustainability reports — data and code release.**

Yueh-Cheng Huang, Department of Computer Science and Information Engineering, National Dong Hwa University, Hualien, Taiwan.
Companion release of the paper of the same title, submitted to the *International Journal on Document Analysis and
Recognition* (ICDAR 2027 journal track).

[![DOI](https://zenodo.org/badge/DOI/10.5281/zenodo.23720137.svg)](https://doi.org/10.5281/zenodo.23720137)
Repository: https://github.com/EdenHuang/esg_reuse_kit · Archive of every tagged release on Zenodo: concept DOI
[10.5281/zenodo.23720137](https://doi.org/10.5281/zenodo.23720137) (always the latest version); each version's own
DOI is listed on that record. Version 1.0.0 is [10.5281/zenodo.23720138](https://doi.org/10.5281/zenodo.23720138);
version 1.0.1 adds the English translations of the annotation guidelines and the test-split section check (annotation S).

A photo in a report counts as **evidence** when it directly depicts what the text beside it claims *and* something
recorded by the camera anchors it to the firm or the event (the firm's name, an event name, a year, or the firm's
logo). Direct depiction without an anchor is **illustration**; a photo on the topic but not of the described thing is
**packaging**; anything else is **unrelated**. The release holds the dataset the paper builds, the human annotations
that define the four levels, every result file behind the paper's numbers, and the tool that measures the levels with
open models.

## What is here, and what is not

Everything needed to rebuild the dataset is here except the report PDFs and the images cut from them, which belong to
the reporting firms. The reports are public filings on the [ESG InfoHub](https://esggenplus.twse.com.tw/) of the
Taiwan Stock Exchange; `data/catalog.csv` gives each report's download URL, SHA-256 and page count, and the code
re-downloads and re-extracts them. Text taken from the reports (captions, paragraphs, the contents of text boxes) is
removed from the geometry files below and comes back in the same rebuild step.

| Path | Contents |
|---|---|
| `data/catalog.csv` | The 450 reports: firm id and name, market, industry, reporting year, download URL, SHA-256, page count, development/test split. |
| `data/population_2023_2025_20261018.csv`, `data/firm_names_20261018.csv`, `data/index/reports_index.csv`, `data/index/raw/company_lists.json` | The sampling frame (firms with a Chinese report for each of 2023–2025), firm names, the report index and the company list read from ESG InfoHub in October 2026; used by the sampling and by the firm-name matching in anchoring. |
| `extraction/{report}_pairs.jsonl` | Every extracted image placement: page, bounding box in PDF points, crop name, embedded-image hashes, pixel size, duplicate groups. Caption and paragraph text removed. |
| `page_structure/{report}.jsonl` | For every page with an image: text boxes (id, bounding box, font size, role, column, container, reading order), containers, heading levels, running text (headers, footers, menus). Box text removed. |
| `photos/photo_filter.csv` | The photo filter's decision and scores for each of the 45,146 images it judged, with the exclusions before it (covers, reports without a text layer, vector shadings, layout elements, duplicates). |
| `annotations/` | Guidelines, sampled items, labels, quality-control files and confirmed logo references of the annotation tasks (next section). |
| `results/` | Every result file behind the paper's tables, figures and numbers. Each file carries a `meta` block: command, git commit, date, configuration, prompt hashes, model names and revisions, input hashes. `env_*.txt` are the `pip freeze` of the runs. |
| `configs/` | All parameters (`default.yaml`), every prompt (`prompts/`, one file each), and `frozen.yaml`: the thresholds, prompt hashes, model revisions and few-shot examples frozen on 2026-10-27 before any test-split evaluation. |
| `src/`, `requirements.txt` | The code: download, extraction, page structure, photo filter, photo-to-text linking, the evidence measurement, reuse and stock-photo detection, annotation tooling, evaluation, tables. |
| `MANIFEST.json` | Path, size and SHA-256 of every file in the release. |

## The dataset in numbers

| | |
|---|---|
| Firms | 150 listed Taiwanese firms (102 TWSE, 48 TPEx; 33 industries), stratified by industry; 30 development firms, 120 test firms. Every threshold, prompt and model choice was made on the development firms. |
| Reports | 450 filed Chinese-language reports, reporting years 2023–2025; 7 without a text layer are excluded, leaving 443 reports and 52,568 pages. |
| Images | 67,404 placements, 46,952 distinct images; 45,146 given to the photo filter. |
| Photos | 17,450 (3,437 in the development firms, 14,013 in the test firms). |

Crop names follow `{firm_id}_{year}_p{page:03d}_{index:02d}.jpg` (page numbers are 1-based PDF page indices). The
crops are not included; `src/run_extract_all.py` recreates them with the same names.

## Annotations

Labels are keyed by opaque item ids; the `*_items.jsonl` files map an id to its crop, report and page. Annotators
are identified by role codes only (`eden` is the author; `a1`, `a2`, `r2` are the assisting annotators). No model
output was shown to an annotator, except where the task is to check program output (D, S) or where the design says
so (C shows the retrieval's first choice).

| Task | Question | Files | Items |
|---|---|---|---|
| A | Is this image a photograph? | `dev/A.jsonl`, `test/A.jsonl` (+ `*_items.jsonl`) | 150 development + 200 test images, two annotators each (κ = 0.88 on the test images) |
| B | Are these two images the same photo or the same scene? | `dev/B.jsonl` | 209 development pairs, two annotators (κ = 0.97) |
| C | Find this photo in the firm's previous-year report | `test/C.jsonl` | 200 test queries; the retrieval's first choice is shown and verified |
| D | Are the program's photo boxes on this page right? | `test/D.jsonl` | 200 test pages |
| R | Which text boxes describe the photo; direct / topical / unrelated; anchors seen in the photo (text, logo, post-production overlay) | `dev/R.jsonl`, `test/R.jsonl` | 106 development + 306 test photos, the author |
| R2 | Task R by a second annotator | `test/R2.jsonl` | 100 test photos (three-level weighted κ = 0.80 against R) |
| S | Which section (E, S, G, company overview, other) the program assigned to a page | `dev/S.jsonl`, `test/S.jsonl` | 50 development + 50 test pages, the author (`results/section_check_*.json`: the program's assignment matched on all 100) |
| Logo references | Which repeated small images and cover elements are the firm's logo | `logo_refs/*.csv` | 30 development + 120 test firms, confirmed by the author |

`annotations/qc/` holds the quality-control material: check items with their keys, practice items, the batch plans
(`*_task_plan.json`), and the calibration reading of the development R items by an AI assistant
(`R_dev_claude_reading.jsonl`, `R_dev_claude_edits.jsonl`), which was shown to the author after annotation and never
used as a label. `annotations/guidelines/` holds the guidelines (version 1.5) in Chinese, the language of the
annotators, and their English translations: `README_en.md` (overview of all tasks), `A_photo_en.md`,
`B_same_photo_en.md`, `C_find_in_last_year_en.md`, `D_photo_boxes_en.md` and `R_evidence_en.md` (R and R2). The
Chinese text is the one the annotators used and is authoritative; the author's own operating notes
(`Annotation_*_zh.md`, `LabelStudio_A_zh.md`: local paths, packaging, Label Studio set-up) are not translated.

## Rebuilding the dataset

1. **Environment.** Python 3.11 (we used 3.11.9). Create a virtual environment, install PyTorch for your CUDA
   version first (ours: `torch==2.14.1+cu130`), then `pip install -r requirements.txt`. The exact versions of our
   runs are in `results/env_20261027.txt` (main environment) and `results/env_ocr_20261022.txt` (PaddleOCR, which
   lives in a second environment, `.venv_ocr`, because PaddleX pins packages that conflict with the main one).
   Label Studio, used for annotation R, is a third environment (`.venv_ls`) and is not needed to rebuild anything.
   For download and extraction alone, `pymupdf==1.28.2`, `pillow==13.0.0`, `numpy`, `pyyaml` and `requests` suffice.
2. **Models.** `python src/check_models.py` downloads every model, checks that it runs and records the versions
   (weights are cached under `work/models/`; `HF_HOME` is set to `work/models/hf`). The revisions the paper used are in
   `configs/frozen.yaml` and `configs/default.yaml`: `Qwen/Qwen2.5-7B-Instruct` (a09a354), `Qwen/Qwen3-VL-8B-Instruct`
   (0c351dd), `OFA-Sys/chinese-clip-vit-large-patch14` (660941a), and for the model comparison
   `Qwen/Qwen2.5-VL-7B-Instruct` (cc59489) and `OpenGVLab/InternVL3-8B-hf` (259a3b6); OpenCLIP ViT-L/14 (OpenAI weights)
   for the photo filter; DINOv2 ViT-B/14 (torch.hub), SSCD `sscd_disc_mixup` (TorchScript), SuperPoint + LightGlue and
   OpenCV MAGSAC for reuse detection; PaddleOCR (`chinese_cht`) for anchoring.
3. **Reports.** `python src/download.py fetch` downloads every report in `data/catalog.csv` into `data/raw/`, one
   request every 3 seconds, and checks each file's SHA-256 against the catalog (`--firms 1101 1707` for a subset).
4. **Extraction.** `python src/run_extract_all.py --catalog data/catalog.csv --out work/extract --workers 8` writes
   `work/extract/{report}_pairs.jsonl` and the crops. The records match `extraction/` field by field (geometry,
   hashes, crop names) and add the text fields.
5. **Page structure.** `python src/page_graph.py --all` writes `work/page_graph/{report}.jsonl`, matching
   `page_structure/` with the box text added.
6. **Photo filter.** `python src/photo_filter.py --all` writes `work/photo_filter.csv`, matching `photos/photo_filter.csv`.
7. **Experiments.** Development-split scripts (`context_link.py`, `evidence.py`, `baselines_evidence.py`,
   `tune_evidence.py`, `evaluate.py`, `reuse.py`, `stock.py`) are run from the repository root as their docstrings
   say; thresholds and prompts come from `configs/`. The test split is run by `src/run_test_chain_a.py` and then
   `src/run_test_chain_b.py`; every script refuses to touch the test split unless `configs/frozen.yaml` matches the
   current settings (`src/frozen.py`). `src/make_tables.py` turns `results/` into the paper's tables and
   `src/audit_numbers.py` checks every number in the manuscript against them.

`src/rebuild_test.py --firms 6499 1455 2102 2739 5201` performs steps 1, 3 and 4 for a few firms in a fresh virtual
environment and an empty folder and compares the outcome with this release. Our own run (`results/rebuild_test_*.json`)
reproduced the SHA-256 of all 15 reports and all 989 extraction records.

## Rights

The report PDFs and their images are not redistributed. The reports are public filings on the ESG InfoHub of the
Taiwan Stock Exchange; the figures in the paper reproduce short excerpts of them for scholarly discussion, each with
its source report and page. Please respect the download interval in `src/download.py` (one request every 3 seconds).

## Licence

- Code and configuration (`src/`, `configs/`, `requirements.txt`): MIT licence, see `LICENSE`.
- Data (`data/`, `annotations/`, `extraction/`, `page_structure/`, `photos/`, `results/`): Creative Commons
  Attribution 4.0 International (CC BY 4.0), see `LICENSE-DATA`.

## Citation

Please cite the paper and the release (`CITATION.cff` has both in machine-readable form):

> Huang, Y.-C. (2026). Do Sustainability Report Photos Show What the Text Claims? A Dataset and a Four-Level
> Measurement. Submitted to the *International Journal on Document Analysis and Recognition* (ICDAR 2027 journal track).

> Huang, Y.-C. (2026). Do Sustainability Report Photos Show What the Text Claims? A Dataset and a Four-Level
> Measurement (data and code release), version 1.0.1. Zenodo. https://doi.org/10.5281/zenodo.23720137

## Contact

Yueh-Cheng Huang, yuehch@gms.ndhu.edu.tw. Questions about the data, the annotation guidelines or rebuilding are
welcome as issues on the repository.
