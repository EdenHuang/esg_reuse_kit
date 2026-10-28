# Annotation guidelines: overview (v1.5, 2026-10-19) — English translation

English translation (2026-10-28) of `README.md`, the Chinese overview of all annotation tasks. The Chinese files
are the ones the annotators used and are authoritative. Each task's guideline has its own translation:
`A_photo_en.md`, `B_same_photo_en.md`, `C_find_in_last_year_en.md`, `D_photo_boxes_en.md`, `R_evidence_en.md`
(R and R2). The operating notes written for the author (`Annotation_*_zh.md`, `LabelStudio_A_zh.md`: local
paths, packaging, Label Studio set-up) are not translated.

All annotation was done in Chinese (decision 15, 2026-10-18: recruit students who read Chinese; items,
examples, practice items, reasons and buttons all in Chinese). A, B, C and D were done by students; R by the
author and R2 by a student who re-annotated a sample.

## As carried out (counts in the released files; the plan below was written before annotation started)
| Task | Annotators | Items actually annotated | Files |
|---|---|---|---|
| A photo or not | two students, both on the same items | 150 development and 200 test images | `annotations/{dev,test}/A.jsonl` |
| R describing text, three levels, anchoring | the author | 106 development items (103 photos) and 306 test items (302 photos; 298 with a depiction level) | `annotations/{dev,test}/R.jsonl` |
| R2 same as R, re-annotation | one student | 100 of the test photos | `annotations/test/R2.jsonl` |
| B same photo or not | two students, both on the same items | 209 development candidate pairs | `annotations/dev/B.jsonl` |
| C same photo in the previous year's report | one student | 200 test photos (the retrieval's first choice verified; 14 check items discarded) | `annotations/test/C.jsonl` |
| D photo boxes | one student | 200 test pages | `annotations/test/D.jsonl` |
| S section assignment | the author | 50 development and 50 test pages | `results/section_check_*.json` |
Agreement figures are in the paper (Table 3) and in `results/`.

## Principle: the reference labels are made blind (PLAN D9)
- Every photo is judged by the model; that is the method. People label a small part of them as the reference
  used to score the model.
- So no model output is visible while annotating: no scores, no model answers, no indication of which method
  picked an item; the order is shuffled.
- Two reasons. First, an annotator who sees the model's answer first is led by it, and the model's accuracy is
  overestimated (Berzak et al., EMNLP 2016). Second, a careless annotator who sees the answer just keeps pressing
  "correct", and we cannot tell whether they looked.
- Exceptions: D and S check program output by design, so they show the program's boxes or the program's section
  assignment.
- The model may be used outside the item screen: to decide which items to annotate (for example, R samples only
  from the photos the photo filter keeps) and to propose candidates for examples and check items (answers
  confirmed by the author). Item files (HTML or JSON) carry no scores or model answers either, since a student who
  reads web pages can open them.

## Overview (as planned)
| Code | What | Who | Planned count | Guideline | Sees program output? |
|---|---|---|---|---|---|
| A | photo or not | 2 students who read Chinese, both on the same batch | 300 images (100 development, 200 test) | `A_photo_zh.md` | no |
| R | describing text, three levels, anchoring | the author | 400 pairs (100 development, 300 test) | `R_evidence_zh.md` | no; the pre-drawn text and photo boxes come from the PDF, not from a model |
| R2 | same as R, re-annotation | 1 Taiwanese student | 150 pairs sampled from R's 300 test pairs | `R_evidence_zh.md` | no, and not the author's answers either |
| B | same photo or same scene | students | about 1,000 pairs (about 200 development pairs first) | `B_same_photo_zh.md` | no scores, no indication of which method picked the pair |
| C | find the same photo in the previous year's report | students | 200 photos (test split only) | `C_find_in_last_year_zh.md` | no; thumbnails in page order, not by similarity |
| D | are the photo boxes right | students | 200 pages (test split) | `D_photo_boxes_zh.md` | yes, the program's boxes (that is what is checked) |
| S | spot check of E/S/G section assignment | the author | 50 section headings | last section of this page | yes, the program's assignment (that is what is checked) |

Before each task the annotator reads the example page and does the practice items; see "Quality control".

## Sampling (seed 2026 throughout)
- **A**: the 100 development images are stratified by photo score into 10 bins (0.1 wide), 10 per bin, short
  bins filled from the neighbouring bins. The 200 test images are a simple random sample of the "images judged by
  the model" in the test firms' reports (the analysed set minus cover pages and reports without a text layer);
  the accuracy in Table 2 is measured on this population only, and the cover pages and no-text-layer reports are
  counted separately (decision 1). Each annotator sees their own shuffled order.
- **R**: a simple random sample of the photos the photo filter keeps; 100 development pairs from the development
  firms, 300 test pairs from the test firms. Extra items are drawn as reserves and kept in sampling order; an item
  the annotator marks "not a photo" is skipped and the next item takes its place until the count is reached
  (default of decision 6).
- **R2**: 150 pairs sampled from R's 300 test pairs, reshuffled.
- **B**: the candidate pool is the union, de-duplicated, of the candidate pairs picked by each compared method
  (MD5, perceptual hash, DINOv2, SSCD, ours), both cross-year reuse and cross-firm stock pairs. Development-firm
  pairs are annotated first. Left/right position is random.
- **C**: a simple random sample of 200 photos from the test firms' 2024 and 2025 reports; a photo is not drawn
  when the previous year's report has no text layer. Each item shows all photos of the same firm's previous-year
  report (those the photo filter keeps), in page order.
- **D**: 200 pages drawn at random from the test reports, excluding cover pages (first and last page) and the
  7 reports without a text layer.

## Quality control: against carelessness, and to teach the task

### Before annotating
- **Example page**: one per task, 10–20 real examples with the correct answer and a one-line reason. The reasons
  are fixed Chinese categories assigned by the program from the answer, so the author does not choose them:
  photo → "a real scene or person taken with a camera"; not a photo → "chart or infographic", "illustration or
  icon", "logo", "scanned document or certificate", "screenshot", "design drawing or 3D rendering". Built locally
  as HTML, not in git; for A (and later B and C) packaged together with the items for the students.
- **Practice items**: 20 for A and B, 5 for C, 10 for D, 20 for R2. The correct answer and reason are shown right
  after each answer.
  - A and B need 18 correct, D needs 9, before the real items; otherwise the annotator re-reads the examples and
    does another set of practice items.
  - C must find all 5.
  - R2 has no pass mark; the point is to understand the rules.
- Examples and practice items never overlap with the real items.

### While annotating
- **Batches**: 50 items per batch (20 for C). After each batch a check is run locally. For A (HTML files) all
  6 batches are packaged at once; the student e-mails each batch's answer file when done and may go on; a batch
  that fails the check is regenerated (same items, reshuffled) and sent again, and the old answers are discarded.
  For R and D (Label Studio) the next batch opens only after the check passes.
- **Check items**: in A, B, C and D one item with a known answer is mixed into every 10 items and looks exactly
  like the others. Annotators know that check items exist but not which ones. If more than 20% of a batch's check
  items are wrong, the batch is sent back.
  - A: images whose answers the author confirmed.
  - B: partly synthetic pairs. The same photo cropped, mirrored or recoloured → Yes; two photos from different
    firms with a very low SSCD similarity → No.
  - C: an edited copy of the query photo (cropped, mirrored, recoloured) placed in the thumbnail grid; the answer
    is always "found".
  - D: one photo's box removed from a page; the answer is always "Missed photo".
- **Speed and answer distribution**: for each batch the local scripts compute each annotator's median seconds per
  item and the longest run of identical answers. In any of the following cases the work stops and the author talks
  to the annotator:
  - median under 1 s for A or B, under 10 s for C, under 3 s for D;
  - 30 or more identical answers in a row.
- **No check items in R2**: R2's agreement with the author is the quantity being measured, so the author's
  answers cannot be used to judge R2's diligence. R2 uses practice items and speed monitoring only.

### After annotating
- Items on which the two A or B annotators disagree are decided by the author, who also does not see the
  model's answer.
- Agreement targets follow PLAN §6: κ ≥ 0.6 for A and B; weighted κ ≥ 0.6 on R's three levels. If a target is
  missed, the guideline is revised once and 50 pairs re-annotated; if still missed, "topical" and "unrelated" are
  merged and both versions are reported in the paper.
- Examples, practice items and check items are excluded from the results and stored separately in
  `annotations/qc/`.

### Where the known answers come from (the author's preparation, about 15 minutes)
- The local scripts pick candidates by photo score, both confident ones and ones near the threshold. The author
  confirms the answers in one sitting; these images become the examples, practice items and check items of A and
  D. The confirmation page asks one thing only: "photo", "not a photo", "do not use"; no model score is shown, and
  reasons and uses are assigned by the program.
- The check items of B, C and D are mostly synthetic (edited copies, removed boxes) and need no human labels; the
  synthetic data of PLAN step 6 uses the same code.
- R2's practice items are development items the author annotated, disjoint from the test items R2 annotates.

### Other
- Annotators do not discuss items with each other; questions go to the author.
- Pay by the hour, not by the item, so that there is no reason to press quickly.

## Incomplete photos (decision 16)
- Some photos are cut into adjacent tiles in the PDF, or partly cut away by a shape or the page edge. Measured
  (`results/tile_groups_20261018_2237.json`): tiles of one image are 1.68% of the images judged by the model and
  0.82% of the photos the photo filter keeps, so they are **not merged**; each tile is one item.
- A judges the content, not the completeness: if a camera visibly took it, it is a photo (guideline v1.5). D
  records "Split" and R records "Photo box problem"; the dataset section of the paper reports the shares.

## Item screens (specification for the local scripts)
- **Tools** (decision 12): for A (and later B and C) one self-contained HTML file per batch, opened in the
  student's own browser. One item per page; the next item opens only after an answer (going back to change an
  answer is allowed); seconds per item are recorded; progress is kept in the browser; at the end "Download
  answers" saves a JSON file to hand in. R and D use Label Studio on the author's computer only.
- **A**: one image, as large as possible. Single choice Photo / Not a photo (keys 1, 2), plus a Not sure tick
  (key 3).
- **R**: the whole page. The blue box is the photo to annotate; grey boxes are every text box on the page (from
  page_graph), selected by clicking. Controls:
  - tick "Not a photo" (then nothing else is annotated);
  - click text boxes (several allowed), or tick "No describing text on this page";
  - single choice of three levels: direct / topical / unrelated;
  - anchoring ticks: text in the photo identifying the firm or event / the firm's logo in the photo / overlay
    text or logo added in post-production;
  - tick "Describing-text box problem" (a grey box missing, merged or split);
  - ticks "Photo box problem" and "Not sure", and a notes field.
- **B**: two images side by side, the same size. Single choice Yes / No (1, 2), plus Not sure (3).
- **C**: the query photo on top, a thumbnail grid of the previous year's report below (click to enlarge). Click
  every identical thumbnail, or tick No match.
- **D**: the whole page with the program's box around every image it found. The annotator can draw a new box and
  label it Missed photo; click an existing box and label it Partial / Merged / Split; or tick No photos on this
  page.
- Every item records: item id, annotator, answer, time spent, guideline version.
- Exported to `annotations/{dev,test}/{A,R,R2,B,C,D}.jsonl` with ids and labels only, under git. Image files are
  not under git.

## S: spot check of E/S/G section assignment (the author)
- Screen: the heading chain and the program's assignment (environmental / social / governance / company
  overview / other), with the page image.
- Judge right or wrong; if wrong, choose the correct class. About 15 minutes for 50 pages. Done on 50 development
  pages (2026-10-20) and 50 test pages (2026-10-28).

## Version history
- v1 (2026-10-18): first version.
- v1.1 (2026-10-18): quality control added: example pages, practice items, check items, batches, speed and
  answer-distribution monitoring.
- v1.2 (2026-10-18): A's test population changed to "images judged by the model"; A moved to one HTML file per
  batch; R gained "Describing-text box problem", and the former "box problem" became "Photo box problem".
- v1.3 (2026-10-18): batching for A after the move to HTML (6 batches packaged at once, each returned when done,
  failed batches regenerated); example pages packaged with the items; A guideline v1.2 adds "laptop, not phone".
- v1.4 (2026-10-18): everything in Chinese (student guidelines A–D became `*_zh.md`, English versions deleted);
  confirmation page simplified; reasons became fixed Chinese categories.
- v1.5 (2026-10-19): "Incomplete photos" section added (decision 16, no merging).
