"""Global image descriptors for reuse detection (PLAN §5 step 6), cached per report.

  SSCD  sscd_disc_mixup (TorchScript, work/models/sscd_disc_mixup.torchscript.pt): short side resized to 288,
        ImageNet normalisation, one image at a time (as in check_models.py); 512-d, L2-normalised.
  DINOv2 dinov2_vitb14 (torch.hub cache in work/models/torch, loaded from the local copy, no network): resize 224,
        centre crop 224, ImageNet normalisation; 768-d CLS token, L2-normalised.

Vectors of crops are cached in work/features/{model}/{report}.npz (crop name, file size, vector); a crop whose
file size changed is recomputed. embed_images() embeds PIL images without a cache (synthetic copies).
Models run with torch.no_grad() in float32 on the GPU when there is one; vectors are stored as float16.
"""
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
os.environ.setdefault("TORCH_HOME", str(ROOT / "work" / "models" / "torch"))
MODELS = {"sscd": {"dim": 512}, "dinov2": {"dim": 768}}
SSCD_PATH = ROOT / "work" / "models" / "sscd_disc_mixup.torchscript.pt"
DINOV2_REPO = ROOT / "work" / "models" / "torch" / "hub" / "facebookresearch_dinov2_main"
_LOADED = {}


def device():
    import torch
    return "cuda" if torch.cuda.is_available() else "cpu"


def model_info(name):
    from meta import sha256_file
    if name == "sscd":
        return {"model": "SSCD sscd_disc_mixup (TorchScript)", "weights_sha256": sha256_file(SSCD_PATH),
                "preprocess": "short side 288, ImageNet normalisation"}
    ckpt = next((ROOT / "work" / "models" / "torch" / "hub" / "checkpoints").glob("dinov2_vitb14*.pth"), None)
    return {"model": "facebookresearch/dinov2 dinov2_vitb14 (torch.hub, local copy)",
            "weights_sha256": sha256_file(ckpt) if ckpt else None, "preprocess": "resize 224, centre crop 224, ImageNet"}


def load(name):
    import torch
    if name in _LOADED:
        return _LOADED[name]
    if name == "sscd":
        m = torch.jit.load(str(SSCD_PATH), map_location=device()).eval()
    elif name == "dinov2":
        m = torch.hub.load(str(DINOV2_REPO), "dinov2_vitb14", source="local").to(device()).eval()
    else:
        raise ValueError(name)
    _LOADED[name] = m
    return m


def _tensor(im, name):
    import torchvision.transforms as T
    norm = T.Normalize(mean=[0.485, 0.456, 0.406], std=[0.229, 0.224, 0.225])
    if name == "sscd":
        tf = T.Compose([T.Resize(288), T.ToTensor(), norm])
    else:
        tf = T.Compose([T.Resize(224), T.CenterCrop(224), T.ToTensor(), norm])
    return tf(im.convert("RGB"))


def embed_images(images, name, flip=False, batch=64):
    """L2-normalised float32 vectors of PIL images (flip=True: of their mirror images)."""
    import numpy as np
    import torch
    from PIL import ImageOps
    m = load(name)
    out = np.zeros((len(images), MODELS[name]["dim"]), dtype=np.float32)
    with torch.no_grad():
        if name == "sscd":  # variable size: one at a time
            for i, im in enumerate(images):
                x = _tensor(ImageOps.mirror(im) if flip else im, name).unsqueeze(0).to(device())
                v = m(x)[0]
                out[i] = (v / v.norm()).float().cpu().numpy()
        else:
            for s in range(0, len(images), batch):
                x = torch.stack([_tensor(ImageOps.mirror(im) if flip else im, name) for im in images[s:s + batch]]).to(device())
                v = m(x)
                out[s:s + len(v)] = (v / v.norm(dim=-1, keepdim=True)).float().cpu().numpy()
    return out


def report_of(crop):
    """1101_2025_p003_00.jpg -> 1101_2025"""
    return "_".join(Path(crop).name.split("_")[:2])


def embed_crops(crops, name, crop_dir, flip=False, log=print):
    """Vectors of extraction crops, in the order given, from the per-report cache (computed when missing)."""
    import numpy as np
    from PIL import Image
    cache_dir = ROOT / "work" / "features" / (name + ("_flip" if flip else ""))
    cache_dir.mkdir(parents=True, exist_ok=True)
    by_report = {}
    for c in crops:
        by_report.setdefault(report_of(c), []).append(c)
    got, new_total = {}, 0
    for rep, cs in sorted(by_report.items()):
        path = cache_dir / f"{rep}.npz"
        have = {}
        if path.exists():
            z = np.load(path, allow_pickle=False)
            have = {str(c): (int(s), v) for c, s, v in zip(z["crop"], z["bytes"], z["emb"])}
        sizes = {c: (Path(crop_dir) / c).stat().st_size for c in cs}
        todo = [c for c in cs if c not in have or have[c][0] != sizes[c]]
        if todo:
            ims = []
            for c in todo:
                with Image.open(Path(crop_dir) / c) as im:
                    ims.append(im.convert("RGB"))
            vecs = embed_images(ims, name, flip=flip)
            for c, v in zip(todo, vecs):
                have[c] = (sizes[c], v.astype(np.float16))
            keep = sorted(have)
            np.savez(path, crop=np.array(keep), bytes=np.array([have[c][0] for c in keep], dtype=np.int64),
                     emb=np.stack([have[c][1] for c in keep]).astype(np.float16))
            new_total += len(todo)
        for c in cs:
            got[c] = have[c][1]
    if new_total:
        log(f"[{name}{' flip' if flip else ''}] 新算 {new_total} 張，其餘從快取")
    return np.stack([got[c] for c in crops]).astype(np.float32) if crops else np.zeros((0, MODELS[name]["dim"]), np.float32)
