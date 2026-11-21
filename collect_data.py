"""Công cụ thu thập dữ liệu ký hiệu bằng webcam.

Mỗi lượt quay lưu một file .npy kích thước (30, 1662): 30 khung hình liên tiếp,
mỗi khung 1662 đặc trưng MediaPipe Holistic (xem keypoints.py).

Kế hoạch: 2 người quay, mỗi người 2 buổi ở hai ngày khác nhau (đổi trang phục, nền, ánh sáng).
    Buổi 1: 3 lượt/ký hiệu → train và validation.
    Buổi 2: 2 lượt/ký hiệu → giữ riêng làm tập test.
    Tổng: 2 người × 30 ký hiệu × 5 lượt = 300 lượt.

Cách dùng:
    python collect_data.py --signer tran --session 1           # buổi 1, mỗi ký hiệu 3 lượt
    python collect_data.py --signer tran --session 2           # buổi 2 (ngày khác), mỗi ký hiệu 2 lượt
    python collect_data.py --signer tran --session 1 --start 7 # bắt đầu từ ký hiệu số 7
    python collect_data.py --summary                           # thống kê và kiểm tra dữ liệu đã quay

Phím trong cửa sổ quay:
    SPACE  bắt đầu / tạm dừng quay ký hiệu hiện tại (tự quay liên tiếp đến khi đủ chỉ tiêu)
    N / P  ký hiệu kế tiếp / trước đó
    D      xóa lượt vừa lưu gần nhất (trong phiên này)
    C      đổi sang camera kế tiếp (camera trước/sau)
    Q/ESC  thoát

Kết quả:
    data/raw/<id>_<slug>/<nguoi>_s<buoi>_<ngaygio>_<stt>.npy
    data/manifest.csv   (mỗi dòng một lượt quay)
"""
from __future__ import annotations

import argparse
import csv
import json
import re
import sys
import time
import unicodedata
from datetime import datetime
from pathlib import Path

import cv2
import mediapipe as mp
import numpy as np

import keypoints as kp

ROOT = Path(__file__).resolve().parent
MANIFEST_FIELDS = [
    "file", "label_id", "slug", "text", "signer", "session", "recorded_at", "frames", "features",
    "fps", "hand_ratio", "pose_ratio", "face_ratio", "mediapipe_version",
]
WINDOW = "Thu thap du lieu ky hieu"
SESSION_QUOTA = {1: 3, 2: 2}   # số lượt mỗi người cho mỗi ký hiệu, theo buổi quay


# ---------------------------------------------------------------- dữ liệu trên đĩa

def ascii_slug(text: str) -> str:
    """'Lê Thị Bảo Trân' → 'lethibaotran' (chỉ còn chữ thường và số)."""
    text = text.replace("đ", "d").replace("Đ", "D")
    text = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode()
    return re.sub(r"[^a-z0-9]", "", text.lower())


def load_labels(path: Path) -> list[dict]:
    labels = json.loads(path.read_text(encoding="utf-8"))
    ids = [l["id"] for l in labels]
    slugs = [l["slug"] for l in labels]
    if len(set(ids)) != len(ids) or len(set(slugs)) != len(slugs):
        sys.exit(f"{path}: id hoặc slug bị trùng.")
    for l in labels:
        if not re.fullmatch(r"[a-z0-9_]+", l["slug"]):
            sys.exit(f"{path}: slug '{l['slug']}' chỉ được gồm a-z, 0-9 và dấu gạch dưới.")
    return sorted(labels, key=lambda l: l["id"])


def label_dir(data_dir: Path, label: dict) -> Path:
    return data_dir / "raw" / f"{label['id']:02d}_{label['slug']}"


def count_sequences(data_dir: Path, label: dict, signer: str | None = None, session: int | None = None) -> int:
    d = label_dir(data_dir, label)
    if not d.exists():
        return 0
    if signer and session:
        pattern = f"{signer}_s{session}_*.npy"
    else:
        pattern = f"{signer}_*.npy" if signer else "*.npy"
    return sum(1 for _ in d.glob(pattern))


def append_manifest(data_dir: Path, row: dict) -> None:
    path = data_dir / "manifest.csv"
    new = not path.exists()
    with path.open("a", newline="", encoding="utf-8-sig" if new else "utf-8") as f:
        w = csv.DictWriter(f, fieldnames=MANIFEST_FIELDS)
        if new:
            w.writeheader()
        w.writerow(row)


def remove_from_manifest(data_dir: Path, rel_file: str) -> None:
    path = data_dir / "manifest.csv"
    if not path.exists():
        return
    with path.open(newline="", encoding="utf-8-sig") as f:
        rows = [r for r in csv.DictReader(f) if r["file"] != rel_file]
    with path.open("w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=MANIFEST_FIELDS)
        w.writeheader()
        w.writerows(rows)


def summary(data_dir: Path, labels: list[dict], target: int) -> int:
    """In số lượt theo ký hiệu, người và buổi; kiểm tra kích thước từng file. Trả về số lỗi."""
    expected = (kp.SEQUENCE_LENGTH, kp.FEATURE_DIM)
    signers: set[str] = set()
    table, bad = [], []
    for label in labels:
        per: dict[str, int] = {}
        for f in sorted(label_dir(data_dir, label).glob("*.npy")):
            signer, session = f.name.split("_")[:2]
            signer = f"{signer}/{session}"
            try:
                arr = np.load(f, allow_pickle=False)
                if arr.shape != expected or not np.isfinite(arr).all():
                    bad.append(f"{f}: shape {arr.shape}, cần {expected}")
                    continue
            except Exception as e:  # file hỏng
                bad.append(f"{f}: không đọc được ({e})")
                continue
            per[signer] = per.get(signer, 0) + 1
        signers |= set(per)
        table.append((label, per))
    cols = sorted(signers)
    print(f"{'ID':>3} {'Ký hiệu':<16}" + "".join(f"{c[:10]:>11}" for c in cols) + f"{'Tổng':>7}  Còn thiếu")
    grand = 0
    for label, per in table:
        total = sum(per.values())
        grand += total
        missing = max(0, target - total)
        print(f"{label['id']:>3} {label['text']:<16}" + "".join(f"{per.get(c, 0):>11}" for c in cols)
              + f"{total:>7}  {missing if missing else 'đủ'}")
    by_session: dict[str, int] = {}
    for _, per in table:
        for key, n in per.items():
            by_session[key.split("/")[1]] = by_session.get(key.split("/")[1], 0) + n
    print(f"\nTổng: {grand} lượt hợp lệ / mục tiêu {target * len(labels)} "
          f"({len(labels)} ký hiệu × {target}). Theo buổi: "
          + ", ".join(f"{k} = {v}" for k, v in sorted(by_session.items())) + " (s1: train/validation, s2: test)")
    if bad:
        print(f"\n{len(bad)} file lỗi:")
        for b in bad:
            print("  " + b)
    return len(bad)


# ---------------------------------------------------------------- giao diện quay

def put(img, text, y, color=(255, 255, 255), scale=0.6, x=10):
    cv2.putText(img, text, (x, y), cv2.FONT_HERSHEY_SIMPLEX, scale, (0, 0, 0), 4, cv2.LINE_AA)
    cv2.putText(img, text, (x, y), cv2.FONT_HERSHEY_SIMPLEX, scale, color, 1, cv2.LINE_AA)


FRONT_CAMERA = 1      # trên máy có 2 camera, chỉ số 0 thường là camera sau, 1 là camera trước
MAX_CAMERAS = 5       # phím C dò các chỉ số 0..4


def try_camera(index: int) -> cv2.VideoCapture | None:
    """Mở camera theo chỉ số; trả về None nếu không mở được hoặc không đọc được khung hình."""
    cap = cv2.VideoCapture(index, cv2.CAP_DSHOW) if sys.platform == "win32" else cv2.VideoCapture(index)
    if cap.isOpened() and cap.read()[0]:
        return cap
    cap.release()
    return None


def open_camera(source: str | None) -> tuple[cv2.VideoCapture, int | None]:
    """Trả về (camera, chỉ số). Không chỉ định thì ưu tiên camera trước, không có mới dùng camera 0."""
    if source is None:
        for index in (FRONT_CAMERA, 0):
            cap = try_camera(index)
            if cap is not None:
                if index != FRONT_CAMERA:
                    print(f"Không mở được camera {FRONT_CAMERA}, dùng camera {index}.")
                return cap, index
        sys.exit("Không mở được camera nào. Đóng ứng dụng khác đang dùng webcam rồi thử lại.")
    if source.isdigit():
        cap = try_camera(int(source))
        if cap is None:
            sys.exit(f"Không mở được camera {source}. Thử chỉ số khác hoặc đóng ứng dụng đang dùng webcam.")
        return cap, int(source)
    cap = cv2.VideoCapture(source)   # file video, dùng để thử công cụ khi không có webcam
    if not cap.isOpened():
        sys.exit(f"Không mở được video '{source}'.")
    return cap, None


def next_camera(cap: cv2.VideoCapture, index: int) -> tuple[cv2.VideoCapture, int]:
    """Chuyển sang camera kế tiếp mở được; nếu không có camera nào khác thì mở lại camera cũ."""
    cap.release()
    for step in range(1, MAX_CAMERAS + 1):
        candidate = (index + step) % MAX_CAMERAS
        new = try_camera(candidate)
        if new is not None:
            return new, candidate
    sys.exit("Mất kết nối camera.")


def collect(args, labels: list[dict]) -> None:
    signer = ascii_slug(args.signer)
    if not signer:
        sys.exit("--signer phải có ít nhất một chữ cái hoặc số.")
    data_dir = Path(args.data_dir)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")

    idx = next((i for i, l in enumerate(labels) if l["id"] == args.start), None) if args.start else 0
    if idx is None:
        sys.exit(f"Không có ký hiệu id={args.start} trong file nhãn.")
    # mặc định nhảy tới ký hiệu đầu tiên mà người này chưa quay đủ
    if not args.start:
        idx = next((i for i, l in enumerate(labels)
                    if count_sequences(data_dir, l, signer, args.session) < args.per_signer), 0)

    IDLE, COUNTDOWN, RECORDING = "idle", "countdown", "recording"
    state, running = IDLE, False
    t_state = 0.0
    buf: list[np.ndarray] = []
    flags: list[tuple[bool, bool, bool]] = []
    t_first = t_next = 0.0
    saved: list[Path] = []          # các file lưu trong phiên này (để xóa bằng phím D)
    seq_no = 0
    message, message_color, t_message = "", (255, 255, 255), 0.0

    def say(text, color=(255, 255, 255)):
        nonlocal message, message_color, t_message
        message, message_color, t_message = text, color, time.monotonic()

    cap, cam_index = open_camera(args.camera)
    cv2.namedWindow(WINDOW, cv2.WINDOW_NORMAL)
    t0 = time.monotonic()
    last_ts = -1
    print(f"Người quay: {signer} | camera: {cam_index if cam_index is not None else args.camera} | "
          f"dữ liệu: {data_dir.resolve()} | SPACE để bắt đầu, C đổi camera, Q để thoát.")

    with kp.create_holistic(Path(args.model)) as holistic:
        while True:
            ok, frame = cap.read()
            if not ok:
                print("Hết khung hình từ camera/video.")
                break
            frame = kp.center_square(frame)                  # cùng hình học với video 224×224 của tập công khai
            now = time.monotonic()
            ts = max(int((now - t0) * 1000), last_ts + 1)   # timestamp phải tăng dần
            last_ts = ts
            result = kp.detect(holistic, frame, ts)          # khung CHƯA lật gương
            label = labels[idx]
            mine = count_sequences(data_dir, label, signer, args.session)
            total = count_sequences(data_dir, label)

            if state == COUNTDOWN and now - t_state >= args.countdown:
                state, buf, flags, t_first, t_next = RECORDING, [], [], now, now

            if state == RECORDING and now >= t_next:      # chỉ lấy ~args.fps khung/giây
                t_next += 1.0 / args.fps
                buf.append(kp.extract_keypoints(result))
                flags.append((kp.has_hand(result), kp.has_pose(result), kp.has_face(result)))
                if len(buf) == kp.SEQUENCE_LENGTH:
                    hand_ratio, pose_ratio, face_ratio = (float(np.mean([f[i] for f in flags])) for i in range(3))
                    fps = (kp.SEQUENCE_LENGTH - 1) / max(now - t_first, 1e-6)
                    if hand_ratio < args.min_hand_ratio:
                        say(f"LOAI: chi thay tay o {hand_ratio:.0%} so khung - quay lai", (0, 0, 255))
                    elif pose_ratio < 0.8:
                        say(f"LOAI: chi thay nguoi o {pose_ratio:.0%} so khung - quay lai", (0, 0, 255))
                    else:
                        seq_no += 1
                        out_dir = label_dir(data_dir, label)
                        out_dir.mkdir(parents=True, exist_ok=True)
                        out = out_dir / f"{signer}_s{args.session}_{stamp}_{seq_no:03d}.npy"
                        np.save(out, np.stack(buf).astype(np.float32))
                        rel = out.relative_to(data_dir).as_posix()
                        append_manifest(data_dir, {
                            "file": rel, "label_id": label["id"], "slug": label["slug"], "text": label["text"],
                            "signer": signer, "session": args.session, "recorded_at": datetime.now().isoformat(timespec="seconds"),
                            "frames": kp.SEQUENCE_LENGTH, "features": kp.FEATURE_DIM, "fps": f"{fps:.1f}",
                            "hand_ratio": f"{hand_ratio:.2f}", "pose_ratio": f"{pose_ratio:.2f}",
                            "face_ratio": f"{face_ratio:.2f}", "mediapipe_version": mp.__version__,
                        })
                        saved.append(out)
                        mine += 1
                        total += 1
                        say(f"DA LUU luot {mine}/{args.per_signer} ({fps:.0f} fps)", (0, 255, 0))
                        print(f"  lưu {rel}  (tay {hand_ratio:.0%}, {fps:.1f} fps)")
                    if running and mine < args.per_signer:
                        state, t_state = COUNTDOWN, now
                    else:
                        state = IDLE
                        if running and mine >= args.per_signer:
                            running = False
                            nxt = next((i for i in range(idx + 1, len(labels))
                                        if count_sequences(data_dir, labels[i], signer, args.session)
                                        < args.per_signer), None)
                            if nxt is None:
                                say("Da du chi tieu cho ky hieu nay. Het danh sach!", (0, 255, 0))
                            else:
                                idx = nxt
                                say(f"Du chi tieu. Ky hieu tiep: {labels[idx]['slug']} - nhan SPACE", (0, 255, 0))

            # ---- vẽ: khung xương trên ảnh gốc, lật gương để xem, rồi mới viết chữ
            kp.draw_landmarks(frame, result)
            view = cv2.flip(frame, 1)
            h, w = view.shape[:2]
            label = labels[idx]
            mine = count_sequences(data_dir, label, signer, args.session)
            total = count_sequences(data_dir, label)
            put(view, f"[{label['id']:02d}/{len(labels)}] {label['slug']}", 30, (0, 255, 255), 0.9)
            put(view, f"{signer} buoi {args.session}: {mine}/{args.per_signer}   tong: {total}/{args.target}", 58)
            hand_ok = kp.has_hand(result)
            put(view, "TAY: co" if hand_ok else "TAY: KHONG THAY", 84, (0, 255, 0) if hand_ok else (0, 0, 255))
            if state == COUNTDOWN:
                left = args.countdown - (now - t_state)
                put(view, f"CHUAN BI {left:.1f}", h // 2, (0, 255, 255), 1.4, w // 2 - 150)
            elif state == RECORDING:
                cv2.rectangle(view, (0, 0), (w - 1, h - 1), (0, 0, 255), 8)
                put(view, f"DANG QUAY {len(buf)}/{kp.SEQUENCE_LENGTH}", h // 2, (0, 0, 255), 1.2, w // 2 - 170)
            if message and now - t_message < 2.5:
                put(view, message, h - 45, message_color, 0.7)
            put(view, "SPACE quay/dung  N/P doi ky hieu  D xoa luot cuoi  C doi camera  Q thoat",
                h - 15, (220, 220, 220), 0.5)
            cv2.imshow(WINDOW, view)

            key = cv2.waitKey(1) & 0xFF
            if key in (ord("q"), ord("Q"), 27):
                break
            if key == ord(" "):
                if state == IDLE:
                    if mine >= args.per_signer:
                        say("Ky hieu nay da du chi tieu cho ban (N de sang ky hieu khac)", (0, 255, 255))
                    else:
                        running, state, t_state = True, COUNTDOWN, now
                else:                      # tạm dừng, bỏ lượt đang quay dở
                    running, state = False, IDLE
                    say("Tam dung", (0, 255, 255))
            elif key in (ord("n"), ord("N"), ord("p"), ord("P")) and state == IDLE:
                idx = (idx + (1 if key in (ord("n"), ord("N")) else -1)) % len(labels)
            elif key in (ord("c"), ord("C")) and state == IDLE and cam_index is not None:
                old = cam_index
                cap, cam_index = next_camera(cap, cam_index)
                say(f"Camera {cam_index}" if cam_index != old else "Khong co camera khac", (0, 255, 255))
                print(f"  camera {cam_index}")
            elif key in (ord("d"), ord("D")) and state == IDLE:
                if saved:
                    last = saved.pop()
                    last.unlink(missing_ok=True)
                    remove_from_manifest(data_dir, last.relative_to(data_dir).as_posix())
                    say(f"Da xoa {last.name}", (0, 255, 255))
                    print(f"  xóa {last.name}")
                else:
                    say("Phien nay chua luu luot nao", (0, 255, 255))

    cap.release()
    cv2.destroyAllWindows()
    print(f"Phiên này lưu {len(saved)} lượt.")


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description="Thu thập dữ liệu ký hiệu bằng webcam (30 khung × 1662 đặc trưng).")
    p.add_argument("--signer", help="tên/mã người quay, ví dụ: tran")
    p.add_argument("--labels", default=str(ROOT / "labels.json"), help="file danh sách ký hiệu")
    p.add_argument("--data-dir", default=str(ROOT / "data"), help="thư mục lưu dữ liệu")
    p.add_argument("--model", default=str(kp.DEFAULT_MODEL_PATH), help="file holistic_landmarker.task")
    p.add_argument("--camera", default=None,
                   help="chỉ số webcam (0, 1, ...) hoặc đường dẫn file video; mặc định: camera trước (1), không có thì 0")
    p.add_argument("--start", type=int, help="id ký hiệu bắt đầu (mặc định: ký hiệu đầu tiên còn thiếu)")
    p.add_argument("--session", type=int, choices=(1, 2),
                   help="buổi quay: 1 = train/validation (3 lượt/ký hiệu), 2 = test, quay ngày khác (2 lượt/ký hiệu)")
    p.add_argument("--per-signer", type=int, help="ghi đè số lượt mỗi người cho mỗi ký hiệu trong buổi này")
    p.add_argument("--target", type=int, default=10, help="tổng số lượt mục tiêu cho mỗi ký hiệu")
    p.add_argument("--countdown", type=float, default=2.0, help="số giây chuẩn bị trước mỗi lượt")
    p.add_argument("--fps", type=float, default=kp.CAPTURE_FPS,
                   help="số khung lấy mỗi giây khi quay (mặc định 12: 30 khung ≈ 2,5 giây, khớp tập công khai)")
    p.add_argument("--min-hand-ratio", type=float, default=0.5,
                   help="loại lượt quay nếu tỉ lệ khung thấy tay thấp hơn mức này")
    p.add_argument("--summary", action="store_true", help="chỉ thống kê và kiểm tra dữ liệu đã quay")
    args = p.parse_args(argv)

    labels = load_labels(Path(args.labels))
    if args.summary:
        return 1 if summary(Path(args.data_dir), labels, args.target) else 0
    if not args.signer or not args.session:
        p.error("cần --signer và --session 1/2 (hoặc dùng --summary)")
    if args.per_signer is None:
        args.per_signer = SESSION_QUOTA[args.session]
    collect(args, labels)
    return 0


if __name__ == "__main__":
    sys.exit(main())
