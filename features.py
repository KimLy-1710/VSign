"""Tiền xử lý chuỗi keypoints trước khi đưa vào mô hình — dùng chung cho huấn luyện và dịch vụ suy luận.

Chỉ phụ thuộc numpy (không cần MediaPipe/OpenCV), để dịch vụ suy luận import nhẹ nhàng.

Đầu vào: mảng (30, 1662) do keypoints.extract_keypoints tạo ra (pose → face → tay trái → tay phải).
Các bước:
  1. Chuẩn hóa vị trí/kích thước: lấy trung điểm hai vai làm gốc tọa độ, chia cho khoảng cách hai vai.
     Nhờ vậy người đứng lệch, gần hay xa camera vẫn cho đặc trưng giống nhau.
  2. Chọn bộ đặc trưng:
       "full"        → giữ đủ 1662 số
       "pose_hands"  → bỏ 1404 số của khuôn mặt, còn 258 (132 pose + 126 hai tay)
Điểm không phát hiện được (toàn số 0) vẫn giữ là 0 sau chuẩn hóa.
"""
from __future__ import annotations

import numpy as np

N_POSE, N_FACE, N_HAND = 33, 468, 21
POSE_DIM, FACE_DIM, HAND_DIM = N_POSE * 4, N_FACE * 3, N_HAND * 3
RAW_DIM = POSE_DIM + FACE_DIM + 2 * HAND_DIM          # 1662
SEQUENCE_LENGTH = 30

FEATURE_SETS = {"full": RAW_DIM, "pose_hands": POSE_DIM + 2 * HAND_DIM}   # 1662, 258

L_SHOULDER, R_SHOULDER = 11, 12
# cặp điểm pose trái/phải (dùng khi lật gương)
POSE_MIRROR_PAIRS = [(1, 4), (2, 5), (3, 6), (7, 8), (9, 10), (11, 12), (13, 14), (15, 16), (17, 18),
                     (19, 20), (21, 22), (23, 24), (25, 26), (27, 28), (29, 30), (31, 32)]


def split_parts(seq: np.ndarray):
    """(T, 1662) → pose (T,33,4), face (T,468,3), tay trái (T,21,3), tay phải (T,21,3). Trả về bản sao."""
    seq = np.asarray(seq, dtype=np.float32)
    t = seq.shape[0]
    a, b, c = POSE_DIM, POSE_DIM + FACE_DIM, POSE_DIM + FACE_DIM + HAND_DIM
    return (seq[:, :a].reshape(t, N_POSE, 4).copy(), seq[:, a:b].reshape(t, N_FACE, 3).copy(),
            seq[:, b:c].reshape(t, N_HAND, 3).copy(), seq[:, c:].reshape(t, N_HAND, 3).copy())


def join_parts(pose, face, lh, rh) -> np.ndarray:
    t = pose.shape[0]
    return np.concatenate([pose.reshape(t, -1), face.reshape(t, -1), lh.reshape(t, -1), rh.reshape(t, -1)], axis=1)


def _body_frame(pose: np.ndarray) -> tuple[np.ndarray, float] | None:
    """Gốc tọa độ (x, y) và tỉ lệ, tính trung bình trên các khung thấy rõ hai vai. None nếu không khung nào thấy."""
    ls, rs = pose[:, L_SHOULDER, :3], pose[:, R_SHOULDER, :3]
    ok = (np.abs(ls).sum(1) > 0) & (np.abs(rs).sum(1) > 0)
    if not ok.any():
        return None
    center = ((ls[ok, :2] + rs[ok, :2]) / 2).mean(0)
    width = np.linalg.norm(ls[ok, :2] - rs[ok, :2], axis=1).mean()
    return center, float(max(width, 1e-3))


def normalize(seq: np.ndarray) -> np.ndarray:
    """Chuẩn hóa theo vai cho cả chuỗi (dùng một gốc chung cho mọi khung để giữ nguyên chuyển động)."""
    pose, face, lh, rh = split_parts(seq)
    frame = _body_frame(pose)
    if frame is None:
        return join_parts(pose, face, lh, rh)
    center, scale = frame
    for part in (pose, face, lh, rh):
        present = np.abs(part[..., :3]).sum(-1) > 0          # (T, N): điểm có phát hiện
        xyz = part[..., :3]
        xyz[..., :2] -= center
        xyz /= scale
        xyz[~present] = 0.0
    return join_parts(pose, face, lh, rh)


def select(seq: np.ndarray, feature_set: str) -> np.ndarray:
    if feature_set == "full":
        return seq
    if feature_set == "pose_hands":
        return np.concatenate([seq[:, :POSE_DIM], seq[:, POSE_DIM + FACE_DIM:]], axis=1)
    raise ValueError(f"feature_set không hợp lệ: {feature_set}")


def preprocess(seq: np.ndarray, feature_set: str) -> np.ndarray:
    """(30, 1662) thô → (30, D) đã chuẩn hóa, sẵn sàng cho mô hình. Dịch vụ suy luận gọi đúng hàm này."""
    seq = np.asarray(seq, dtype=np.float32)
    if seq.shape != (SEQUENCE_LENGTH, RAW_DIM):
        raise ValueError(f"cần chuỗi {(SEQUENCE_LENGTH, RAW_DIM)}, nhận {seq.shape}")
    return select(normalize(seq), feature_set).astype(np.float32)


# ---------------------------------------------------------------- tăng cường dữ liệu (chỉ dùng khi train)

def mirror(seq: np.ndarray) -> np.ndarray:
    """Lật gương chuỗi ĐÃ chuẩn hóa (gốc ở giữa hai vai): đổi dấu x, đổi tay trái ↔ phải và các điểm pose trái ↔ phải.
    Không lật được lưới mặt (thiếu bảng đối xứng 468 điểm) nên chỉ dùng cho bộ "pose_hands"."""
    pose, face, lh, rh = split_parts(seq)
    for part in (pose, lh, rh):
        present = np.abs(part[..., :3]).sum(-1) > 0
        part[..., 0] = np.where(present, -part[..., 0], 0.0)
    for a, b in POSE_MIRROR_PAIRS:
        pose[:, [a, b]] = pose[:, [b, a]]
    return join_parts(pose, face, rh, lh)


def augment(seq: np.ndarray, rng: np.random.Generator, allow_mirror: bool) -> np.ndarray:
    """Tăng cường một chuỗi thô (30, 1662) đã chuẩn hóa: co giãn thời gian, co giãn/dịch/xoay nhẹ, nhiễu, lật gương."""
    t = seq.shape[0]
    # 1. co giãn thời gian: lấy lại 30 khung từ một cửa sổ ngẫu nhiên (nhanh/chậm hơn ±20%), khung gần nhất
    span = rng.uniform(0.8, 1.2) * (t - 1)
    start = rng.uniform(min(0.0, (t - 1) - span), max(0.0, (t - 1) - span))
    idx = np.clip(np.rint(np.linspace(start, start + span, t)), 0, t - 1).astype(int)
    seq = seq[idx]

    pose, face, lh, rh = split_parts(seq)
    scale = rng.uniform(0.9, 1.1)
    shift = rng.normal(0, 0.05, size=2)
    ang = np.deg2rad(rng.uniform(-8, 8))
    rot = np.array([[np.cos(ang), -np.sin(ang)], [np.sin(ang), np.cos(ang)]], dtype=np.float32)
    for part in (pose, face, lh, rh):
        present = np.abs(part[..., :3]).sum(-1) > 0
        xyz = part[..., :3]
        xyz[..., :2] = xyz[..., :2] @ rot.T
        xyz *= scale
        xyz[..., :2] += shift
        xyz += rng.normal(0, 0.01, size=xyz.shape)          # 2. nhiễu nhỏ
        xyz[~present] = 0.0
    seq = join_parts(pose, face, lh, rh)
    if allow_mirror and rng.random() < 0.5:                  # 3. lật gương
        seq = mirror(seq)
    return seq.astype(np.float32)
