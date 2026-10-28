"""Check that the models in CLAUDE.md's model table load and run on this machine, and record
their exact versions (CLAUDE.md 第一次開工 6; PLAN §5 steps 3-6).

  python src/check_models.py                                   # every check
  python src/check_models.py --only vlm ocr                    # some checks
  python src/check_models.py --also-vlm Qwen/Qwen3-VL-8B-Instruct

Each check runs on a few pilot crops (work/extract_pilot/crops, pilot judgments in
pilot/claude_judgments.json), times itself, records peak GPU memory, and never stops the others
when it fails. Weights are cached under work/models/ (Hugging Face, torch hub, PaddleX; about
35 GB in total). OCR runs in its own virtual environment (.venv_ocr) because PaddleOCR pins
packages that clash with the main environment. Chinese CLIP is loaded through transformers
(OFA-Sys/chinese-clip-vit-large-patch14): the cn_clip package needs lmdb==1.3.0, which has no
Windows wheel for Python 3.11.
Writes results/models_{stamp}.json and reports/models_{stamp}.html.
"""
import argparse
import hashlib
import html
import json
import os
import re
import subprocess
import sys
import time
import traceback
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
os.environ.setdefault("HF_HOME", str(ROOT / "work" / "models" / "hf"))
os.environ.setdefault("TORCH_HOME", str(ROOT / "work" / "models" / "torch"))
sys.path.insert(0, str(ROOT / "src"))
from meta import package_versions, run_meta, run_stamp  # noqa: E402

CROPS = {  # pilot crops with a known answer to "is this a real photograph?"
    "photo_event": ("1101_2025_p132_00.jpg", True),     # 徵才說明會現場
    "photo_signs": ("1707_2025_p104_05.jpg", True),     # 廠內標示牌照片拼圖
    "portrait_text": ("1101_2025_p160_02.jpg", True),   # 訪談照，圖上有姓名部門
    "illustration": ("1707_2025_p050_00.jpg", False),   # 產線等角插畫
    "cover_art": ("1101_2025_p001_00.jpg", False),      # 封面抽象圖
}
SSCD_URL = "https://dl.fbaipublicfiles.com/sscd-copy-detection/sscd_disc_mixup.torchscript.pt"
CN_CLIP = "OFA-Sys/chinese-clip-vit-large-patch14"
GATE_PROMPT = (
    "下面是永續報告書裡一張照片旁邊的文字。請列出文字裡「拍得到」的對象（活動、物件、地點、人物角色），"
    "以及定錨字串（公司名稱、活動名稱、年份）。只輸出 JSON，格式："
    '{{"referents":[{{"type":"event|object|place|person|activity","text":"..."}}],'
    '"anchors":{{"org":[],"event":[],"year":[]}}}}\n文字：{text}')
VQA_QUESTION = "這張圖是真實拍攝的照片嗎？只回答「是」或「否」。"
OCR_TEXT = "臺灣水泥 2025年 永續報告書"


# ---------------------------------------------------------------- helpers
def torch_mod():
    import torch
    return torch


def device():
    return "cuda" if torch_mod().cuda.is_available() else "cpu"


def gpu_reset():
    t = torch_mod()
    if t.cuda.is_available():
        t.cuda.empty_cache()
        t.cuda.reset_peak_memory_stats()


def gpu_peak():
    t = torch_mod()
    return round(t.cuda.max_memory_allocated() / 1e9, 2) if t.cuda.is_available() else None


def snapshot(repo):
    """Download (or reuse) a Hugging Face model; return the local path and its commit hash."""
    from huggingface_hub import snapshot_download
    path = snapshot_download(repo)
    return path, Path(path).name


def load_hf(cls, path, **kw):
    t = torch_mod()
    kw.setdefault("device_map", device())
    try:
        return cls.from_pretrained(path, dtype=t.bfloat16, **kw)
    except TypeError:  # older transformers call it torch_dtype
        return cls.from_pretrained(path, torch_dtype=t.bfloat16, **kw)


def free(*objs):
    for o in objs:
        del o
    import gc
    gc.collect()
    gpu_reset()


def sha256_file(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for b in iter(lambda: f.read(1 << 20), b""):
            h.update(b)
    return h.hexdigest()


def parse_json(text):
    m = re.search(r"\{.*\}", text, re.S)
    if not m:
        return None
    try:
        return json.loads(m.group(0))
    except json.JSONDecodeError:
        return None


def cosine(a, b):
    t = torch_mod()
    return round(float(t.nn.functional.cosine_similarity(a.flatten()[None], b.flatten()[None])), 3)


def edited(img):
    """The same photo after a layout edit: 75 % crop, 0.8x resize, brighter."""
    from PIL import ImageEnhance
    w, h = img.size
    c = img.crop((int(w * 0.1), int(h * 0.1), int(w * 0.85), int(h * 0.85)))
    c = c.resize((int(c.width * 0.8), int(c.height * 0.8)))
    return ImageEnhance.Brightness(c).enhance(1.2)


# ---------------------------------------------------------------- checks
def check_llm(ctx):
    from transformers import AutoModelForCausalLM, AutoTokenizer
    repo = ctx["cfg"]["evidence"]["llm"]
    path, rev = snapshot(repo)
    t0 = time.time()
    tok = AutoTokenizer.from_pretrained(path)
    model = load_hf(AutoModelForCausalLM, path)
    load_s = time.time() - t0
    ids = tok.apply_chat_template([{"role": "user", "content": GATE_PROMPT.format(text=ctx["caption"])}],
                                  add_generation_prompt=True, return_tensors="pt")
    if not hasattr(ids, "shape"):  # some versions return a BatchEncoding
        ids = ids["input_ids"]
    ids = ids.to(model.device)
    t0 = time.time()
    out = model.generate(ids, max_new_tokens=300, do_sample=False)
    infer_s = time.time() - t0
    text = tok.decode(out[0][ids.shape[1]:], skip_special_tokens=True)
    parsed = parse_json(text)
    free(model)
    return {"model": repo, "revision": rev, "ok": parsed is not None, "load_s": load_s, "infer_s": infer_s,
            "input": ctx["caption"], "output": text[:500], "summary": "JSON 解析成功" if parsed else "輸出不是 JSON"}


def check_vlm(ctx, repo):
    from transformers import AutoModelForImageTextToText, AutoProcessor
    t = torch_mod()
    path, rev = snapshot(repo)
    t0 = time.time()
    processor = AutoProcessor.from_pretrained(path)
    model = load_hf(AutoModelForImageTextToText, path)
    load_s = time.time() - t0
    tok = processor.tokenizer
    yes_id = tok.encode("是", add_special_tokens=False)[0]
    no_id = tok.encode("否", add_special_tokens=False)[0]
    probs, times = {}, []
    for name, (img, truth) in ctx["images"].items():
        msgs = [{"role": "user", "content": [{"type": "image"}, {"type": "text", "text": VQA_QUESTION}]}]
        prompt = processor.apply_chat_template(msgs, tokenize=False, add_generation_prompt=True)
        inputs = processor(text=[prompt], images=[img], return_tensors="pt").to(model.device)
        t0 = time.time()
        with t.no_grad():
            logits = model(**inputs).logits[0, -1].float()
        times.append(time.time() - t0)
        probs[name] = round(float(t.softmax(logits[[yes_id, no_id]], dim=0)[0]), 3)
    agree = sum((probs[n] > 0.5) == truth for n, (_, truth) in ctx["images"].items())
    free(model)
    return {"model": repo, "revision": rev, "ok": True, "load_s": load_s, "infer_s": sum(times) / len(times),
            "p_yes": probs, "summary": f"「是」的機率取自輸出 logits；{agree}/{len(probs)} 張和預期一致"}


def check_ocr(ctx):
    py = ROOT / ".venv_ocr" / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    if not py.is_file():
        return {"ok": False, "summary": "沒有 .venv_ocr（setup_local.ps1 會建）"}
    from PIL import Image, ImageDraw, ImageFont
    font = None
    for f in ("C:/Windows/Fonts/msjh.ttc", "C:/Windows/Fonts/mingliu.ttc", "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc"):
        if Path(f).is_file():
            font = ImageFont.truetype(f, 56)
            break
    synth = ROOT / "work" / "models" / "ocr_test.png"
    synth.parent.mkdir(parents=True, exist_ok=True)
    img = Image.new("RGB", (900, 160), "white")
    ImageDraw.Draw(img).text((30, 40), OCR_TEXT, fill="black", font=font)
    img.save(synth)
    paths = [str(synth)] + [str(ctx["crop_dir"] / CROPS[k][0]) for k in ("portrait_text", "photo_signs")]
    code = r'''
import json, os, sys, time
import paddle, paddleocr
from paddleocr import PaddleOCR
t0 = time.time(); lang = "chinese_cht"
# enable_mkldnn=False: paddle 3.3.1 on Windows CPU fails in oneDNN with
# "ConvertPirAttribute2RuntimeAttribute not support" (10/18 local run)
opts = dict(use_doc_orientation_classify=False, use_doc_unwarping=False, use_textline_orientation=False, enable_mkldnn=False)
try:
    ocr = PaddleOCR(lang=lang, **opts)
except Exception:
    lang = "ch"
    ocr = PaddleOCR(lang=lang, **opts)
out ={"paddleocr": paddleocr.__version__, "paddle": paddle.__version__, "device": paddle.device.get_device(),
       "lang": lang, "mkldnn": False, "load_s": round(time.time() - t0, 1), "texts": {}}
for p in sys.argv[1:]:
    t0 = time.time(); texts = []
    for r in ocr.predict(p):
        try:
            texts += list(r["rec_texts"])
        except Exception:
            texts += list(r.json["res"]["rec_texts"])
    out["texts"][os.path.basename(p)] = {"texts": texts, "s": round(time.time() - t0, 2)}
print("JSON:" + json.dumps(out, ensure_ascii=False))
'''
    env = dict(os.environ, PADDLE_PDX_CACHE_HOME=str(ROOT / "work" / "models" / "paddlex"), PYTHONIOENCODING="utf-8")
    t0 = time.time()
    proc = subprocess.run([str(py), "-c", code] + paths, capture_output=True, text=True, encoding="utf-8",
                          errors="replace", env=env, timeout=3600)
    line = next((ln for ln in proc.stdout.splitlines() if ln.startswith("JSON:")), None)
    if not line:
        return {"ok": False, "summary": "PaddleOCR 沒跑起來", "error": (proc.stderr or proc.stdout)[-1500:]}
    res = json.loads(line[5:])
    got = re.sub(r"\s+", "", "".join(res["texts"][synth.name]["texts"])).replace("臺", "台")
    want = OCR_TEXT.replace(" ", "").replace("臺", "台")
    hit = sum(1 for ch in set(want) if ch in got) / len(set(want))
    return {"model": f"PaddleOCR {res['paddleocr']}（paddle {res['paddle']}，{res['device']}，lang={res['lang']}，oneDNN 關閉）",
            "paddlex_models": sorted(set(re.findall(r"Creating model: \('([^']+)'", proc.stderr + proc.stdout))),
            "ok": hit >= 0.8, "load_s": res["load_s"], "infer_s": res["texts"][synth.name]["s"], "texts": res["texts"],
            "summary": f"合成字串「{OCR_TEXT}」讀回「{got}」，字元命中 {hit:.0%}；總共 {time.time() - t0:.0f} 秒"}


def check_cnclip(ctx):
    from transformers import ChineseCLIPModel, ChineseCLIPProcessor
    t = torch_mod()
    path, rev = snapshot(CN_CLIP)
    t0 = time.time()
    model = ChineseCLIPModel.from_pretrained(path).to(device()).eval()
    proc = ChineseCLIPProcessor.from_pretrained(path)
    load_s = time.time() - t0
    labels = ["一張真實拍攝的照片", "一張插畫、圖表或設計圖"]
    probs, times = {}, []
    for name, (img, _) in ctx["images"].items():
        inputs = proc(text=labels, images=img, return_tensors="pt", padding=True).to(device())
        t0 = time.time()
        with t.no_grad():
            p = model(**inputs).logits_per_image.softmax(dim=-1)[0, 0]
        times.append(time.time() - t0)
        probs[name] = round(float(p), 3)
    agree = sum((probs[n] > 0.5) == truth for n, (_, truth) in ctx["images"].items())
    free(model)
    return {"model": CN_CLIP, "revision": rev, "ok": True, "load_s": load_s, "infer_s": sum(times) / len(times),
            "p_photo": probs, "summary": f"{agree}/{len(probs)} 張和預期一致（比較對象 E1 用）"}


def check_openclip(ctx):
    import open_clip
    t = torch_mod()
    pf = ctx["cfg"]["photo_filter"]
    t0 = time.time()
    # the OpenAI CLIP weights were trained with QuickGELU; open_clip's 'ViT-L-14' config uses GELU and only
    # warns, so force it from the pretrained config (same as photo_filter.py, LOG 2026-10-18 16:05)
    qg = bool((open_clip.get_pretrained_cfg(pf["model"], pf["pretrained"]) or {}).get("quick_gelu"))
    model, _, preprocess = open_clip.create_model_and_transforms(pf["model"], pretrained=pf["pretrained"], device=device(),
                                                                 force_quick_gelu=qg)
    tokenizer = open_clip.get_tokenizer(pf["model"])
    load_s = time.time() - t0
    prompts = pf["photo_prompts"] + pf["nonphoto_prompts"]
    with t.no_grad():
        text = model.encode_text(tokenizer(prompts).to(device()))
        text = text / text.norm(dim=-1, keepdim=True)
    probs, times = {}, []
    for name, (img, _) in ctx["images"].items():
        t0 = time.time()
        with t.no_grad():
            im = model.encode_image(preprocess(img).unsqueeze(0).to(device()))
            im = im / im.norm(dim=-1, keepdim=True)
            p = (100 * im @ text.T).softmax(dim=-1)[0]
        times.append(time.time() - t0)
        probs[name] = round(float(p[:len(pf["photo_prompts"])].sum()), 3)
    agree = sum((probs[n] > 0.5) == truth for n, (_, truth) in ctx["images"].items())
    free(model)
    return {"model": f"OpenCLIP {pf['model']} / {pf['pretrained']}" + ("（QuickGELU）" if qg else ""), "quick_gelu": qg,
            "ok": True, "load_s": load_s,
            "infer_s": sum(times) / len(times), "p_photo": probs,
            "summary": f"configs 的英文提示詞；{agree}/{len(probs)} 張和預期一致（照片過濾用）"}


def _image_tensor(img, size, crop):
    import torchvision.transforms as T
    steps = [T.Resize(size)] + ([T.CenterCrop(size)] if crop else []) + [
        T.ToTensor(), T.Normalize(mean=[0.485, 0.456, 0.406], std=[0.229, 0.224, 0.225])]
    return T.Compose(steps)(img.convert("RGB")).unsqueeze(0).to(device())


def _copy_scores(embed, ctx):
    from PIL import ImageOps
    photo = ctx["images"]["photo_event"][0]
    other = ctx["images"]["photo_signs"][0]
    base = embed(photo)
    return {"same_photo_edited": cosine(base, embed(edited(photo))),
            "same_photo_flipped": cosine(base, embed(ImageOps.mirror(photo))),
            "different_photo": cosine(base, embed(other))}


def check_dinov2(ctx):
    t = torch_mod()
    t0 = time.time()
    model = t.hub.load("facebookresearch/dinov2", "dinov2_vitb14").to(device()).eval()
    load_s = time.time() - t0
    t0 = time.time()
    with t.no_grad():
        scores = _copy_scores(lambda im: model(_image_tensor(im, 224, True)), ctx)
    ckpt = next(Path(t.hub.get_dir(), "checkpoints").glob("dinov2_vitb14*.pth"), None)
    free(model)
    return {"model": "facebookresearch/dinov2 dinov2_vitb14（torch.hub）", "ok": scores["same_photo_edited"] > scores["different_photo"],
            "weights_sha256": sha256_file(ckpt) if ckpt else None, "load_s": load_s, "infer_s": (time.time() - t0) / 3,
            "cosine": scores, "summary": "同一張（裁切調色後）vs 不同照片的餘弦相似度"}


def check_sscd(ctx):
    t = torch_mod()
    path = ROOT / "work" / "models" / "sscd_disc_mixup.torchscript.pt"
    if not path.is_file():
        path.parent.mkdir(parents=True, exist_ok=True)
        urllib.request.urlretrieve(SSCD_URL, path)
    t0 = time.time()
    model = t.jit.load(str(path), map_location=device()).eval()
    load_s = time.time() - t0
    t0 = time.time()
    with t.no_grad():
        scores = _copy_scores(lambda im: model(_image_tensor(im, 288, False)), ctx)
    free(model)
    return {"model": "SSCD sscd_disc_mixup（TorchScript）", "source": SSCD_URL, "weights_sha256": sha256_file(path),
            "ok": scores["same_photo_edited"] > scores["different_photo"], "load_s": load_s,
            "infer_s": (time.time() - t0) / 3, "cosine": scores, "summary": "同一張（裁切調色後）vs 不同照片的餘弦相似度"}


def check_lightglue(ctx):
    import cv2
    import numpy as np
    from lightglue import LightGlue, SuperPoint
    from lightglue.utils import numpy_image_to_torch, rbd
    t = torch_mod()
    t0 = time.time()
    extractor = SuperPoint(max_num_keypoints=2048).eval().to(device())
    matcher = LightGlue(features="superpoint").eval().to(device())
    load_s = time.time() - t0
    photo = ctx["images"]["photo_event"][0]
    pairs = {"same_photo_edited": edited(photo), "different_photo": ctx["images"]["photo_signs"][0]}
    out, times = {}, []
    for name, other in pairs.items():
        t0 = time.time()
        with t.no_grad():
            f0 = extractor.extract(numpy_image_to_torch(np.asarray(photo.convert("RGB"))).to(device()))
            f1 = extractor.extract(numpy_image_to_torch(np.asarray(other.convert("RGB"))).to(device()))
            m = matcher({"image0": f0, "image1": f1})
        f0, f1, m = rbd(f0), rbd(f1), rbd(m)
        k0 = f0["keypoints"][m["matches"][:, 0]].cpu().numpy()
        k1 = f1["keypoints"][m["matches"][:, 1]].cpu().numpy()
        inliers = 0
        if len(k0) >= 4:
            _, mask = cv2.findHomography(k0, k1, cv2.USAC_MAGSAC, 8.0)
            inliers = int(mask.sum()) if mask is not None else 0
        times.append(time.time() - t0)
        out[name] = {"matches": int(len(k0)), "magsac_inliers": inliers}
    free(extractor, matcher)
    return {"model": "SuperPoint + LightGlue（cvg/LightGlue）＋ OpenCV USAC_MAGSAC", "ok": out["same_photo_edited"]["magsac_inliers"]
            > out["different_photo"]["magsac_inliers"], "load_s": load_s, "infer_s": sum(times) / len(times),
            "result": out, "summary": "同一張（裁切調色後）的內點數應該遠高於不同照片"}


def check_faiss(ctx):
    import faiss
    import numpy as np
    x = np.random.RandomState(2026).randn(20000, 512).astype("float32")
    faiss.normalize_L2(x)
    t0 = time.time()
    index = faiss.IndexFlatIP(512)
    index.add(x)
    _, ids = index.search(x[:100], 5)
    ok = bool((ids[:, 0] == np.arange(100)).all())
    return {"model": f"faiss {faiss.__version__}（CPU，IndexFlatIP）", "ok": ok, "load_s": 0, "infer_s": time.time() - t0,
            "summary": "2 萬筆 512 維向量，100 次查詢都找回自己" if ok else "近鄰結果不對"}


CHECKS = {"llm": check_llm, "vlm": None, "ocr": check_ocr, "cnclip": check_cnclip, "openclip": check_openclip,
          "dinov2": check_dinov2, "sscd": check_sscd, "lightglue": check_lightglue, "faiss": check_faiss}


# ---------------------------------------------------------------- report
def write_html(path, result):
    e = html.escape
    rows = []
    for name, r in result["checks"].items():
        mark = "✓" if r.get("ok") else "✗"
        detail = {k: v for k, v in r.items() if k not in ("ok", "model", "revision", "load_s", "infer_s", "peak_gpu_gb",
                                                           "summary", "error", "total_s")}
        rows.append(
            f"<tr><td>{e(name)}</td><td>{mark}</td><td>{e(str(r.get('model', '')))}<br><span class='mut'>{e(str(r.get('revision', '') or ''))}</span></td>"
            f"<td>{r.get('load_s', 0) or 0:.1f}</td><td>{(r.get('infer_s') or 0):.2f}</td><td>{e(str(r.get('peak_gpu_gb', '')))}</td>"
            f"<td>{e(r.get('summary', ''))}<pre>{e(json.dumps(detail, ensure_ascii=False, indent=1)[:1500])}</pre>"
            f"{'<pre class=bad>' + e(r.get('error', '')) + '</pre>' if r.get('error') else ''}</td></tr>")
    env = result["env"]
    doc = f"""<!doctype html><html lang="zh-Hant"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1"><title>模型檢查</title>
<style>:root{{--bg:#fff;--fg:#1d1d1f;--mut:#666;--line:#ddd;--bad:#b3261e}}
@media (prefers-color-scheme: dark){{:root{{--bg:#161616;--fg:#eee;--mut:#aaa;--line:#333;--bad:#ff8a80}}}}
body{{background:var(--bg);color:var(--fg);font:14px/1.5 system-ui,"Noto Sans TC","Microsoft JhengHei",sans-serif;max-width:1200px;margin:0 auto;padding:16px}}
table{{border-collapse:collapse}}th,td{{border-bottom:1px solid var(--line);padding:6px 8px;text-align:left;vertical-align:top}}
pre{{white-space:pre-wrap;font-size:12px;margin:4px 0}}.mut{{color:var(--mut);font-size:12px}}.bad{{color:var(--bad)}}.wrap{{overflow-x:auto}}</style></head><body>
<h1>模型檢查（CLAUDE.md 第一次開工 6）</h1>
<p class="mut">{e(result['meta']['date'])}｜{e(env.get('gpu', ''))}｜torch {e(str(env.get('torch')))}（CUDA {e(str(env.get('cuda')))}）｜transformers {e(str(env['packages'].get('transformers')))}｜Python {e(result['meta']['python'])}</p>
<p>✓ = 載入並跑出合理結果。秒數：載入一次、每張圖或每次推論。GPU：這一項的峰值記憶體（GB）。</p>
<div class="wrap"><table><tr><th>項目</th><th></th><th>模型與版本</th><th>載入秒</th><th>推論秒</th><th>GPU GB</th><th>結果</th></tr>{''.join(rows)}</table></div>
</body></html>"""
    Path(path).write_text(doc, encoding="utf-8")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--only", nargs="+", choices=list(CHECKS))
    ap.add_argument("--also-vlm", nargs="*", default=[], help="more VLMs to try, e.g. Qwen/Qwen3-VL-8B-Instruct")
    ap.add_argument("--config", default="configs/default.yaml")
    ap.add_argument("--crop-dir", default="work/extract_pilot/crops")
    args = ap.parse_args()

    import yaml
    from PIL import Image
    t = torch_mod()
    cfg = yaml.safe_load(Path(args.config).read_text(encoding="utf-8"))
    crop_dir = Path(args.crop_dir)
    images = {k: (Image.open(crop_dir / f).convert("RGB"), truth) for k, (f, truth) in CROPS.items()
              if (crop_dir / f).is_file()}
    if len(images) < len(CROPS):
        sys.exit(f"{crop_dir} 裡缺試跑的裁切圖；先照 CLAUDE.md 第一次開工 3 重跑試跑報告。")
    caption = "台泥於花蓮和平廠舉辦 2025 年度淨零轉型員工說明會，邀請社區居民參觀碳捕捉示範設備。"
    ctx = {"cfg": cfg, "images": images, "crop_dir": crop_dir, "caption": caption}
    names = args.only or list(CHECKS)
    plan = [(n, None) for n in names if n != "vlm"]
    if "vlm" in names:
        plan[1:1] = [("vlm", cfg["evidence"]["vlm"])] + [("vlm", v) for v in args.also_vlm]
    results = {}
    for name, repo in plan:
        key = name if repo is None else f"vlm:{repo.split('/')[-1]}"
        print(f"[{key}] …", flush=True)
        gpu_reset()
        t0 = time.time()
        try:
            r = check_vlm(ctx, repo) if name == "vlm" else CHECKS[name](ctx)
        except Exception as ex:
            r = {"ok": False, "summary": f"失敗：{type(ex).__name__}", "error": traceback.format_exc()[-2000:]}
        r["peak_gpu_gb"] = gpu_peak()
        r["total_s"] = round(time.time() - t0, 1)
        results[key] = r
        print(f"[{key}] {'✓' if r.get('ok') else '✗'} {r.get('summary', '')}", flush=True)

    env = {"torch": t.__version__, "cuda": t.version.cuda, "gpu_available": t.cuda.is_available(),
           "gpu": (t.cuda.get_device_name(0) + f"，{t.cuda.get_device_properties(0).total_memory / 1e9:.0f} GB")
           if t.cuda.is_available() else "no GPU",
           "packages": package_versions(["transformers", "accelerate", "huggingface-hub", "tokenizers", "open_clip_torch",
                                         "lightglue", "faiss-cpu", "opencv-python", "torchvision", "qwen-vl-utils"]),
           "HF_HOME": os.environ.get("HF_HOME"), "TORCH_HOME": os.environ.get("TORCH_HOME")}
    stamp = run_stamp()
    result = {"meta": run_meta(args.config, []), "env": env, "checks": results}
    Path("results").mkdir(exist_ok=True)
    Path("reports").mkdir(exist_ok=True)
    out_json = Path("results") / f"models_{stamp}.json"
    out_html = Path("reports") / f"models_{stamp}.html"
    out_json.write_text(json.dumps(result, ensure_ascii=False, indent=1, default=str), encoding="utf-8")
    write_html(out_html, result)
    bad = [k for k, r in results.items() if not r.get("ok")]
    print(f"{len(results) - len(bad)}/{len(results)} 項通過{'；沒過：' + '、'.join(bad) if bad else ''}")
    print(f"wrote {out_json} and {out_html}")
    sys.exit(0 if not bad else 3)


if __name__ == "__main__":
    main()
