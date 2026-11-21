"""Chuyển tập ViSignLanguage-Video (Hugging Face: star092304/ViSignLanguage-Video) sang keypoints.

Đọc thẳng dataset.zip, chỉ dùng phần dataset/train/<nhãn>/*.mp4 (hai thư mục test không có nhãn).
Với mỗi video:
  1. Bỏ khung hỏng: tập dữ liệu có nhiều khung bị thay bằng ảnh một màu (cam, vàng, hồng, xanh...).
  2. Gộp bản sao: <id>_1.mp4, <id>_2.mp4... là bản sao y hệt <id>.mp4; khung hỏng ở bản này
     được lấy lại từ bản kia nếu ở đó còn sạch. Mỗi nhóm chỉ ra MỘT mẫu.
  3. Chạy MediaPipe Holistic (keypoints.py) trên các khung sạch, đưa về đúng 30 khung bằng cách lấy
     khung sạch gần nhất theo thời gian, lưu file (30, 1662).
  4. Ghi manifest.csv với tỉ lệ thấy tay/người/mặt và cờ accepted (cùng ngưỡng với collect_data.py).

Cách dùng:
    python prepare_visl.py --zip data/external/visl_download/dataset.zip          # xử lý (chạy lại được, tự bỏ qua nhóm đã xong)
    python prepare_visl.py --report                                               # thống kê theo lớp
"""
from __future__ import annotations

import os

os.environ.setdefault("GLOG_minloglevel", "2")      # tắt log rác của MediaPipe
os.environ.setdefault("TF_CPP_MIN_LOG_LEVEL", "2")

import argparse
import csv
import re
import sys
import tempfile
import time
import unicodedata
import zipfile
from collections import defaultdict
from multiprocessing import Pool
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parent
DEFAULT_ZIP = ROOT / "data" / "external" / "visl_download" / "dataset.zip"
DEFAULT_OUT = ROOT / "data" / "external" / "visl_keypoints"

SOLID_STD = 10.0        # khung có độ lệch chuẩn điểm ảnh dưới mức này là khung một màu (hỏng)
SAME_PIXEL_DIFF = 3.0   # sai khác trung bình dưới mức này thì coi hai video là bản sao
MIN_CLEAN = 15          # cần ít nhất 15/30 khung sạch
MIN_HAND = 0.5          # giống collect_data.py
MIN_POSE = 0.8
FRAME_MS = 83           # timestamp giả định ~12 khung/giây cho chế độ VIDEO của MediaPipe

FIELDS = ["file", "label", "slug", "group_id", "source_videos", "n_frames", "n_clean", "n_recovered",
          "variants_identical", "hand_ratio", "pose_ratio", "face_ratio", "accepted", "reason"]


def slugify(text: str) -> str:
    """'Cảm ơn' → 'cam_on'."""
    text = text.replace("đ", "d").replace("Đ", "D")
    text = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode().lower()
    return re.sub(r"[^a-z0-9]+", "_", text).strip("_")


def build_slugs(labels) -> dict[str, str]:
    """Nhãn → tên thư mục không trùng. Bỏ dấu có thể làm hai nhãn trùng nhau (Tôi/Tối, Đâu/Đầu...);
    khi đó MỌI nhãn trong nhóm trùng đều được thêm hậu tố _1, _2 theo thứ tự chữ cái."""
    by: dict[str, list[str]] = defaultdict(list)
    for label in sorted(set(labels)):
        by[slugify(label)].append(label)
    out = {}
    for slug, names in by.items():
        for i, label in enumerate(names, 1):
            out[label] = slug if len(names) == 1 else f"{slug}_{i}"
    return out


def list_groups(zip_path: Path) -> list[tuple[str, str, list[str]]]:
    """[(nhãn, mã nhóm, [tên file trong zip, bản gốc trước])]."""
    groups: dict[tuple[str, str], list[str]] = defaultdict(list)
    with zipfile.ZipFile(zip_path) as z:
        for name in z.namelist():
            parts = name.split("/")
            if len(parts) != 4 or parts[1] != "train" or not parts[3].lower().endswith(".mp4"):
                continue
            label = unicodedata.normalize("NFC", parts[2])
            stem = parts[3][:-4]
            groups[(label, stem.split("_")[0])].append(name)
    out = []
    for (label, gid), names in sorted(groups.items()):
        names.sort(key=lambda n: (len(n), n))      # bản gốc (không hậu tố) đứng đầu
        out.append((label, gid, names))
    return out


# ---------------------------------------------------------------- tiến trình con

_zip = None
_tmp_dir = None


def _init(zip_path: str, tmp_dir: str) -> None:
    global _zip, _tmp_dir
    _zip = zipfile.ZipFile(zip_path)
    _tmp_dir = tmp_dir


def _read_video(name: str) -> list[np.ndarray]:
    import cv2
    fd, path = tempfile.mkstemp(suffix=".mp4", dir=_tmp_dir)
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(_zip.read(name))
        cap = cv2.VideoCapture(path)
        frames = []
        while True:
            ok, im = cap.read()
            if not ok:
                break
            frames.append(im)
        cap.release()
        return frames
    finally:
        os.remove(path)


def _is_clean(im: np.ndarray) -> bool:
    return float(im.reshape(-1, 3).std(0).mean()) > SOLID_STD


def _merge_variants(videos: list[list[np.ndarray]]) -> tuple[list[np.ndarray | None], int, bool]:
    """Ghép các bản sao: khung j lấy từ bản đầu tiên mà khung j sạch.
    Trả về (danh sách khung, None nếu hỏng ở mọi bản), số khung khôi phục được, các bản có giống nhau không."""
    base = videos[0]
    clean = [[_is_clean(im) for im in v] for v in videos]
    identical = True
    for v, c in zip(videos[1:], clean[1:]):
        if len(v) != len(base):
            identical = False
            break
        common = [j for j in range(len(v)) if c[j] and clean[0][j]]
        if common and np.mean([np.abs(v[j].astype(np.int16) - base[j].astype(np.int16)).mean()
                               for j in common[:: max(1, len(common) // 5)]]) > SAME_PIXEL_DIFF:
            identical = False
            break
    if not identical:            # không phải bản sao → chỉ dùng bản gốc
        return [im if ok else None for im, ok in zip(base, clean[0])], 0, False
    merged, recovered = [], 0
    for j in range(len(base)):
        pick = next((videos[k][j] for k in range(len(videos)) if clean[k][j]), None)
        if pick is not None and not clean[0][j]:
            recovered += 1
        merged.append(pick)
    return merged, recovered, True


def process_group(task: tuple[str, str, list[str], str, str]) -> dict:
    label, gid, names, slug, out_dir = task
    sys.path.insert(0, str(ROOT))
    import keypoints as kp

    row = {"label": label, "slug": slug, "group_id": gid, "source_videos": ";".join(n.rsplit("/", 1)[1] for n in names)}
    try:
        videos = [_read_video(n) for n in names]
    except Exception as e:
        return {**row, "file": "", "accepted": 0, "reason": f"read_error: {e}"}
    if not videos[0]:
        return {**row, "file": "", "n_frames": 0, "accepted": 0, "reason": "empty_video"}

    frames, recovered, identical = _merge_variants(videos)
    idx = [j for j, im in enumerate(frames) if im is not None]
    row.update(n_frames=len(frames), n_clean=len(idx), n_recovered=recovered, variants_identical=int(identical))
    if len(idx) < 2:
        return {**row, "file": "", "accepted": 0, "reason": "no_clean_frames"}

    feats, hand, pose, face = [], [], [], []
    with kp.create_holistic() as holistic:
        for j in idx:
            r = kp.detect(holistic, frames[j], j * FRAME_MS)
            feats.append(kp.extract_keypoints(r))
            hand.append(kp.has_hand(r)); pose.append(kp.has_pose(r)); face.append(kp.has_face(r))

    # đưa về 30 khung: mỗi mốc thời gian lấy khung sạch gần nhất (không nội suy để khỏi trộn tọa độ với số 0)
    targets = np.linspace(idx[0], idx[-1], kp.SEQUENCE_LENGTH)
    src = np.array(idx)
    pick = [int(np.abs(src - t).argmin()) for t in targets]
    seq = np.stack([feats[p] for p in pick]).astype(np.float32)
    hand_r = float(np.mean([hand[p] for p in pick]))
    pose_r = float(np.mean([pose[p] for p in pick]))
    face_r = float(np.mean([face[p] for p in pick]))

    reasons = []
    if len(idx) < MIN_CLEAN:
        reasons.append("few_clean_frames")
    if hand_r < MIN_HAND:
        reasons.append("hands_missing")
    if pose_r < MIN_POSE:
        reasons.append("pose_missing")

    rel = f"{slug}/{gid}.npy"
    path = Path(out_dir) / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    np.save(path, seq)
    return {**row, "file": rel, "hand_ratio": f"{hand_r:.2f}", "pose_ratio": f"{pose_r:.2f}",
            "face_ratio": f"{face_r:.2f}", "accepted": int(not reasons), "reason": "+".join(reasons)}


# ---------------------------------------------------------------- tiến trình chính

def run(zip_path: Path, out_dir: Path, workers: int, tmp_dir: str | None, limit: int | None) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    manifest = out_dir / "manifest.csv"
    done = set()
    if manifest.exists():
        with manifest.open(newline="", encoding="utf-8-sig") as f:
            done = {(r["label"], r["group_id"]) for r in csv.DictReader(f)}
    all_groups = list_groups(zip_path)
    slugs = build_slugs(g[0] for g in all_groups)
    groups = [g for g in all_groups if (g[0], g[1]) not in done]
    if limit:
        groups = groups[:limit]
    print(f"{len(done)} nhóm đã xử lý trước đó, {len(groups)} nhóm cần xử lý, {workers} tiến trình.")
    if not groups:
        return
    new = not manifest.exists()
    tasks = [(label, gid, names, slugs[label], str(out_dir)) for label, gid, names in groups]
    t0 = time.time()
    with manifest.open("a", newline="", encoding="utf-8-sig" if new else "utf-8") as f, \
            Pool(workers, initializer=_init, initargs=(str(zip_path), tmp_dir or tempfile.gettempdir())) as pool:
        w = csv.DictWriter(f, fieldnames=FIELDS)
        if new:
            w.writeheader()
        for i, row in enumerate(pool.imap_unordered(process_group, tasks, chunksize=4), 1):
            w.writerow({k: row.get(k, "") for k in FIELDS})
            if i % 50 == 0 or i == len(tasks):
                f.flush()
                rate = i / (time.time() - t0)
                print(f"  {i}/{len(tasks)}  ({rate:.1f} nhóm/giây, còn ~{(len(tasks) - i) / rate / 60:.1f} phút)", flush=True)


def report(out_dir: Path, top: int) -> None:
    with (out_dir / "manifest.csv").open(newline="", encoding="utf-8-sig") as f:
        rows = list(csv.DictReader(f))
    by: dict[str, dict] = {}
    reasons: dict[str, int] = defaultdict(int)
    for r in rows:
        s = by.setdefault(r["label"], {"slug": r["slug"], "groups": 0, "ok": 0, "hand": []})
        s["groups"] += 1
        s["ok"] += int(r["accepted"] or 0)
        if r["hand_ratio"]:
            s["hand"].append(float(r["hand_ratio"]))
        for x in filter(None, r["reason"].split("+")):
            reasons[x] += 1
    table = sorted(by.items(), key=lambda kv: -kv[1]["ok"])
    summary = out_dir / "class_summary.csv"
    with summary.open("w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f)
        w.writerow(["rank", "label", "slug", "unique_videos", "accepted", "mean_hand_ratio"])
        for i, (label, s) in enumerate(table, 1):
            w.writerow([i, label, s["slug"], s["groups"], s["ok"], f"{np.mean(s['hand']):.2f}" if s["hand"] else ""])
    total, ok = len(rows), sum(int(r["accepted"] or 0) for r in rows)
    print(f"{total} video không trùng, {ok} đạt ({ok / total:.0%}). Lý do loại: {dict(reasons)}")
    print(f"{'#':>3} {'Nhãn':<18}{'Video':>6}{'Đạt':>6}{'Tay TB':>8}")
    for i, (label, s) in enumerate(table[:top], 1):
        print(f"{i:>3} {label:<18}{s['groups']:>6}{s['ok']:>6}{np.mean(s['hand']) if s['hand'] else 0:>8.2f}")
    print(f"Bảng đầy đủ: {summary}")


def main() -> int:
    p = argparse.ArgumentParser(description="Chuyển ViSignLanguage-Video sang keypoints (30 × 1662).")
    p.add_argument("--zip", default=str(DEFAULT_ZIP), help="đường dẫn dataset.zip")
    p.add_argument("--out", default=str(DEFAULT_OUT), help="thư mục lưu .npy và manifest.csv")
    p.add_argument("--workers", type=int, default=max(1, (os.cpu_count() or 2) - 2))
    p.add_argument("--tmp-dir", help="thư mục tạm để giải nén từng video (mặc định: thư mục tạm của hệ thống)")
    p.add_argument("--limit", type=int, help="chỉ xử lý N nhóm (để chạy thử)")
    p.add_argument("--report", action="store_true", help="chỉ in thống kê theo lớp")
    p.add_argument("--top", type=int, default=100, help="số lớp in ra trong báo cáo")
    a = p.parse_args()
    if a.report:
        report(Path(a.out), a.top)
    else:
        run(Path(a.zip), Path(a.out), a.workers, a.tmp_dir, a.limit)
    return 0


if __name__ == "__main__":
    sys.exit(main())
