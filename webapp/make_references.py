"""Tạo video mẫu cho web app: mỗi ký hiệu một clip ViSignLanguage-Video, kèm link từ điển QIPEDC nếu có.

Người quay làm theo clip ViSL vì mô hình được huấn luyện trên chính tập này. Link QIPEDC (từ điển NNKH của
Bộ GD&ĐT) để tham khảo thêm.

Chọn clip: mẫu đạt (accepted=1), đủ 30 khung sạch, không phải lấy lại khung từ bản sao, tỉ lệ thấy tay cao nhất.
Mã hóa lại sang H.264 (trình duyệt và iPhone phát được), phóng lên 336×336, 12 khung/giây.

Cần: dataset.zip, manifest.csv của prepare_visl.py, và imageio-ffmpeg (pip install imageio-ffmpeg).
    python webapp/make_references.py
Kết quả: webapp/ref/<slug>.mp4 và webapp/references.json
"""
from __future__ import annotations

import csv
import json
import subprocess
import sys
import tempfile
import zipfile
from pathlib import Path

import imageio_ffmpeg

ROOT = Path(__file__).resolve().parent.parent
VISL = ROOT / "data" / "external" / "visl_keypoints" / "manifest.csv"
ZIP = ROOT / "data" / "external" / "visl_download" / "dataset.zip"
OUT = ROOT / "webapp" / "ref"
QIPEDC_VIDEO = "https://qipedc.moet.gov.vn/videos/{}.mp4"

# Tra từ điển QIPEDC (qipedc.moet.gov.vn/dictionary, 4362 mục) ngày 2026-11-22.
# Các ký hiệu không có ở đây không có mục khớp trong từ điển.
QIPEDC = {
    "chao": [("W00489", "chào")],
    "moi_vao": [("W02263", "mời")],
    "giup": [("W01497B", "giúp đỡ"), ("W01497T", "giúp đỡ"), ("W01497N", "giúp đỡ")],
    "ho": [("D0137", "họ (đại từ)")],
    "di": [("W01190", "đi")],
    "chay": [("W00501", "chạy")],
    "nghe": [("W02353", "nghe")],
    "nhin": [("W02499", "nhìn")],
    "nghi_ngoi": [("W02364", "nghỉ ngơi")],
    "trua": [("W00370B", "buổi trưa"), ("W00370T", "buổi trưa"), ("W00370N", "buổi trưa")],
    "chieu": [("W00366", "buổi chiều")],
    "cuu": [("W00947", "cứu")],
    "khoc": [("W01821", "khóc")],
}


def pick(rows: list[dict], slug: str) -> dict | None:
    cands = [r for r in rows if r["slug"] == slug and r["accepted"] == "1" and r["n_clean"] == "30" and r["n_recovered"] == "0"]
    return max(cands, key=lambda r: (float(r["hand_ratio"]), float(r["pose_ratio"])), default=None)


def encode(src: Path, dst: Path) -> None:
    subprocess.run([imageio_ffmpeg.get_ffmpeg_exe(), "-y", "-loglevel", "error", "-i", str(src),
                    "-vf", "scale=336:336:flags=lanczos,fps=12", "-an", "-c:v", "libx264", "-preset", "slow",
                    "-crf", "26", "-pix_fmt", "yuv420p", "-movflags", "+faststart", str(dst)], check=True)


def links(entries: list[tuple[str, str]]) -> list[dict]:
    """Link QIPEDC; nhiều video cho cùng một từ thì đánh số 1, 2, 3."""
    many = len(entries) > 1
    return [{"label": f"QIPEDC: {name}" + (f" {k}" if many else ""), "url": QIPEDC_VIDEO.format(i)}
            for k, (i, name) in enumerate(entries, 1)]


def main() -> int:
    labels = sorted(json.loads((ROOT / "labels.json").read_text(encoding="utf-8")), key=lambda l: l["id"])
    with VISL.open(newline="", encoding="utf-8-sig") as f:
        rows = list(csv.DictReader(f))
    OUT.mkdir(exist_ok=True)
    refs = {}
    with zipfile.ZipFile(ZIP) as z, tempfile.TemporaryDirectory() as tmp:
        paths = {n.split("/")[-1]: n for n in z.namelist() if "/train/" in n and n.endswith(".mp4")}
        for l in labels:
            r = pick(rows, l["visl_dir"])
            if r is None:
                print(f"! {l['text']}: không có clip đạt chuẩn")
                continue
            video = r["source_videos"].split(";")[0]
            src = Path(tmp) / video
            src.write_bytes(z.read(paths[video]))
            encode(src, OUT / f"{l['slug']}.mp4")
            refs[l["slug"]] = {
                "video": f"ref/{l['slug']}.mp4", "visl_file": paths[video], "hand_ratio": float(r["hand_ratio"]),
                "links": links(QIPEDC.get(l["slug"], [])),
            }
            print(f"{l['text']:<12} {paths[video]:<40} tay {r['hand_ratio']}  {(OUT / (l['slug'] + '.mp4')).stat().st_size // 1024} KB")
    (ROOT / "webapp" / "references.json").write_text(json.dumps({
        "source": "ViSignLanguage-Video (star092304, AI Challenge PTIT), CC BY 4.0",
        "source_url": "https://huggingface.co/datasets/star092304/ViSignLanguage-Video",
        "signs": refs}, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"{len(refs)}/{len(labels)} ký hiệu có video mẫu, {sum(1 for v in refs.values() if v['links'])} có link QIPEDC.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
