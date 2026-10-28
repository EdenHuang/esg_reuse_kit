"""Score how well each picture matches the text paired with it, using Chinese-CLIP.

NOT YET RUN: the cloud sandbox has no GPU and cannot download model weights, so this
script has not been executed. Run it first on the lab machine and check the output.

Setup (lab machine):
  pip install cn_clip opencc-python-reimplemented torch pillow
Usage:
  python score_clip.py --pairs outputs/grapeking_pairs.jsonl outputs/tcc_pairs.jsonl \
      --crops outputs/crops --out outputs/clip_scores.csv

Output: one row per picture with the cosine similarity between the picture and each
text field (caption, local heading, text on the picture, nearest paragraph). Chinese-CLIP
was trained mostly on Simplified Chinese, so text is converted Traditional -> Simplified
when OpenCC is installed, and cut to 50 characters (the model reads short texts).
"""
import argparse
import csv
import json
from pathlib import Path

import torch
from PIL import Image
import cn_clip.clip as clip
from cn_clip.clip import load_from_name

try:
    from opencc import OpenCC
    to_simplified = OpenCC("t2s").convert
except ImportError:  # keep running without conversion
    def to_simplified(s):
        return s

FIELDS = ["caption", "local_heading", "overlay_text", "paragraph"]
MAX_CHARS = 50


def text_fields(r):
    return {
        "caption": r["caption"],
        "local_heading": r["local_heading"],
        "overlay_text": r["overlay_text"],
        "paragraph": r["paragraphs"][0]["text"] if r["paragraphs"] else "",
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--pairs", nargs="+", required=True)
    ap.add_argument("--crops", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--model", default="ViT-B-16", help="e.g. ViT-B-16 or ViT-L-14")
    ap.add_argument("--cache", default="./cn_clip_weights")
    args = ap.parse_args()

    device = "cuda" if torch.cuda.is_available() else "cpu"
    model, preprocess = load_from_name(args.model, device=device, download_root=args.cache)
    model.eval()

    rows = []
    for path in args.pairs:
        for line in open(path, encoding="utf-8"):
            r = json.loads(line)
            row = {"report": r["report"], "page": r["page"], "crop": r["crop"],
                   "full_page": r["full_page"]}
            fields = text_fields(r)
            names = [k for k in FIELDS if fields[k].strip()]
            if names:
                image = preprocess(Image.open(Path(args.crops) / r["crop"]).convert("RGB"))
                image = image.unsqueeze(0).to(device)
                tokens = clip.tokenize([to_simplified(fields[k])[:MAX_CHARS] for k in names]).to(device)
                with torch.no_grad():
                    fi = model.encode_image(image)
                    ft = model.encode_text(tokens)
                    fi = fi / fi.norm(dim=-1, keepdim=True)
                    ft = ft / ft.norm(dim=-1, keepdim=True)
                    sims = (fi @ ft.T).squeeze(0).tolist()
                for k, s in zip(names, sims):
                    row[f"sim_{k}"] = round(float(s), 4)
            rows.append(row)

    cols = ["report", "page", "crop", "full_page"] + [f"sim_{k}" for k in FIELDS]
    with open(args.out, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=cols)
        w.writeheader()
        for row in rows:
            w.writerow(row)
    print(f"wrote {args.out} ({len(rows)} pictures)")


if __name__ == "__main__":
    main()
