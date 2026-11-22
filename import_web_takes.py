"""Nhập các lượt quay từ web app (webapp/) vào dữ liệu huấn luyện.

Web app tải mỗi lượt lên Google Drive thành một file zip: 30 ảnh JPEG vuông (00.jpg … 29.jpg, chụp ở
12 khung/giây, chưa lật gương) và meta.json. Script này trích keypoints bằng đúng MediaPipe Holistic
Python dùng cho tập ViSL (keypoints.py), áp cùng ngưỡng với collect_data.py, rồi ghi:
    data/raw/<id>_<slug>/<tên lượt>.npy     (30, 1662), giống collect_data.py
    data/manifest.csv                       thêm một dòng mỗi lượt đạt
    data/web_import_log.csv                 mọi lượt đã xử lý (đạt / loại và lý do), để không xử lý lại
Thư mục nguồn có dạng <src>/<người>/buoi1|buoi2/*.zip (Apps Script tự chia); zip nằm thẳng ở <src> cũng được đọc.
Lượt bị loại được chuyển vào <src>/_rejected/. Nếu <src> là thư mục Drive đồng bộ, web app sẽ thấy
lượt đó còn thiếu và người quay chỉ cần quay lại.

Cách dùng (thư mục nguồn: thư mục Drive đã đồng bộ về máy, hoặc nơi chứa các zip đã tải về):
    python import_web_takes.py --src "~/Google Drive/My Drive/VSign_uploads"
    python import_web_takes.py --src data/web_inbox --dry-run      # chỉ liệt kê, không ghi
    python collect_data.py --summary                               # xem tiến độ sau khi nhập
"""
from __future__ import annotations

import os

os.environ.setdefault("GLOG_minloglevel", "2")
os.environ.setdefault("TF_CPP_MIN_LOG_LEVEL", "2")

import argparse
import csv
import json
import re
import sys
import zipfile
from datetime import datetime
from pathlib import Path

import cv2
import mediapipe as mp
import numpy as np

import collect_data as cd
import keypoints as kp

ROOT = Path(__file__).resolve().parent
NAME_RE = re.compile(r"^([a-z0-9]+)_s([12])_(\d{2})_([a-z0-9_]+)_(\d{8}-\d{6}-\d{3})$")
LOG_FIELDS = ["zip", "status", "reason", "file", "hand_ratio", "pose_ratio", "imported_at"]
REJECTED_DIR = "_rejected"
FRAME_MS = round(1000 / kp.CAPTURE_FPS)   # timestamp cho chế độ VIDEO, giống nhịp chụp của web app


def read_log(path: Path) -> set[str]:
    if not path.exists():
        return set()
    with path.open(newline="", encoding="utf-8-sig") as f:
        return {r["zip"] for r in csv.DictReader(f)}


def append_log(path: Path, row: dict) -> None:
    new = not path.exists()
    with path.open("a", newline="", encoding="utf-8-sig" if new else "utf-8") as f:
        w = csv.DictWriter(f, fieldnames=LOG_FIELDS)
        if new:
            w.writeheader()
        w.writerow(row)


def load_take(zip_path: Path) -> tuple[dict, list[np.ndarray]]:
    with zipfile.ZipFile(zip_path) as z:
        meta = json.loads(z.read("meta.json"))
        names = sorted(n for n in z.namelist() if n.lower().endswith(".jpg"))
        frames = [cv2.imdecode(np.frombuffer(z.read(n), np.uint8), cv2.IMREAD_COLOR) for n in names]
    return meta, frames


def extract(frames: list[np.ndarray], model: Path):
    """30 ảnh BGR → (keypoints (30, 1662), tỉ lệ thấy tay, người, mặt)."""
    seq, flags = [], []
    with kp.create_holistic(model) as holistic:
        for i, frame in enumerate(frames):
            result = kp.detect(holistic, kp.center_square(frame), i * FRAME_MS)
            seq.append(kp.extract_keypoints(result))
            flags.append((kp.has_hand(result), kp.has_pose(result), kp.has_face(result)))
    ratios = [float(np.mean([f[i] for f in flags])) for i in range(3)]
    return np.stack(seq).astype(np.float32), *ratios


def process(zip_path: Path, labels: dict[int, dict], data_dir: Path, args) -> dict:
    stem = zip_path.stem
    row = {"zip": zip_path.name, "imported_at": datetime.now().isoformat(timespec="seconds")}
    m = NAME_RE.match(stem)
    if not m:
        return {**row, "status": "rejected", "reason": "bad_name"}
    signer, session, label_id = m.group(1), int(m.group(2)), int(m.group(3))
    label = labels.get(label_id)
    if label is None or label["slug"] != m.group(4):
        return {**row, "status": "rejected", "reason": "unknown_label"}
    try:
        meta, frames = load_take(zip_path)
    except (zipfile.BadZipFile, KeyError, json.JSONDecodeError) as e:
        return {**row, "status": "rejected", "reason": f"bad_zip:{type(e).__name__}"}
    if len(frames) != kp.SEQUENCE_LENGTH or any(f is None for f in frames):
        return {**row, "status": "rejected", "reason": f"frames:{len(frames)}"}

    seq, hand, pose, face = extract(frames, Path(args.model))
    row.update(hand_ratio=f"{hand:.2f}", pose_ratio=f"{pose:.2f}")
    if hand < args.min_hand_ratio:
        return {**row, "status": "rejected", "reason": "hands_missing"}
    if pose < args.min_pose_ratio:
        return {**row, "status": "rejected", "reason": "pose_missing"}

    out = cd.label_dir(data_dir, label) / f"{stem}.npy"
    rel = out.relative_to(data_dir).as_posix()
    if not args.dry_run:
        out.parent.mkdir(parents=True, exist_ok=True)
        np.save(out, seq)
        cd.append_manifest(data_dir, {
            "file": rel, "label_id": label_id, "slug": label["slug"], "text": label["text"],
            "signer": signer, "session": session, "recorded_at": meta.get("recorded_at", ""),
            "frames": kp.SEQUENCE_LENGTH, "features": kp.FEATURE_DIM, "fps": meta.get("fps", ""),
            "hand_ratio": f"{hand:.2f}", "pose_ratio": f"{pose:.2f}", "face_ratio": f"{face:.2f}",
            "mediapipe_version": mp.__version__,
        })
    return {**row, "status": "ok", "reason": "", "file": rel}


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description="Nhập lượt quay từ web app (zip trên Drive) thành keypoints.")
    p.add_argument("--src", required=True, help="thư mục chứa các file zip (thư mục Drive đã đồng bộ hoặc đã tải về)")
    p.add_argument("--data-dir", default=str(ROOT / "data"))
    p.add_argument("--labels", default=str(ROOT / "labels.json"))
    p.add_argument("--model", default=str(kp.DEFAULT_MODEL_PATH))
    p.add_argument("--min-hand-ratio", type=float, default=0.5, help="cùng ngưỡng với collect_data.py")
    p.add_argument("--min-pose-ratio", type=float, default=0.8)
    p.add_argument("--retry-rejected", action="store_true", help="xử lý lại cả các lượt đã bị loại trước đó")
    p.add_argument("--keep-rejected", action="store_true", help="không chuyển lượt bị loại vào thư mục _rejected")
    p.add_argument("--dry-run", action="store_true", help="chỉ kiểm tra, không ghi .npy / manifest / log")
    args = p.parse_args(argv)

    src, data_dir = Path(args.src).expanduser(), Path(args.data_dir)
    if not src.is_dir():
        sys.exit(f"Không thấy thư mục {src}")
    labels = {l["id"]: l for l in cd.load_labels(Path(args.labels))}
    log_path = data_dir / "web_import_log.csv"
    done = read_log(log_path)
    if args.retry_rejected and log_path.exists():
        with log_path.open(newline="", encoding="utf-8-sig") as f:
            done = {r["zip"] for r in csv.DictReader(f) if r["status"] == "ok"}
    existing = {Path(r).stem for r in _manifest_files(data_dir)}

    zips = [z for z in find_zips(src, include_rejected=args.retry_rejected) if z.name not in done and z.stem not in existing]
    print(f"{len(zips)} lượt mới trong {src} (bỏ qua {len(done)} lượt đã xử lý).")
    counts = {"ok": 0, "rejected": 0}
    for i, z in enumerate(zips, 1):
        row = process(z, labels, data_dir, args)
        counts[row["status"]] += 1
        note = f"tay {row.get('hand_ratio', '-')}, người {row.get('pose_ratio', '-')}"
        print(f"  [{i}/{len(zips)}] {z.name}: {'đạt' if row['status'] == 'ok' else 'LOẠI ' + row['reason']} ({note})")
        if not args.dry_run:
            data_dir.mkdir(parents=True, exist_ok=True)
            append_log(log_path, row)
            if row["status"] == "rejected" and not args.keep_rejected:
                # chuyển ra khỏi thư mục gốc: web app chỉ đếm file ở thư mục gốc, nên người quay sẽ thấy thiếu và quay lại
                if REJECTED_DIR not in z.relative_to(src).parts:
                    (src / REJECTED_DIR).mkdir(exist_ok=True)
                    z.replace(src / REJECTED_DIR / z.name)
    print(f"Xong: {counts['ok']} đạt, {counts['rejected']} loại.")
    if counts["rejected"] and not args.dry_run and not args.keep_rejected:
        print(f"Lượt bị loại đã chuyển vào {src / REJECTED_DIR}. Khi thư mục Drive đồng bộ xong, web app "
              "sẽ tính các lượt đó là còn thiếu để người quay quay lại.")
    return 0


def find_zips(src: Path, include_rejected: bool = False) -> list[Path]:
    """Mọi file zip trong src và thư mục con (<người>/buoi<n>/), bỏ qua _rejected/ trừ khi xử lý lại.
    Trùng tên (ví dụ cùng lượt ở thư mục gốc và thư mục con) thì chỉ lấy một."""
    seen, out = set(), []
    for z in sorted(src.rglob("*.zip")):
        rel = z.relative_to(src).parts
        if (REJECTED_DIR in rel[:-1] and not include_rejected) or z.name in seen:
            continue
        seen.add(z.name)
        out.append(z)
    return out


def _manifest_files(data_dir: Path) -> list[str]:
    path = data_dir / "manifest.csv"
    if not path.exists():
        return []
    with path.open(newline="", encoding="utf-8-sig") as f:
        return [r["file"] for r in csv.DictReader(f)]


if __name__ == "__main__":
    sys.exit(main())
