"""Huấn luyện và so sánh mô hình nhận diện ký hiệu (LSTM / GRU / Transformer nhỏ).

Dữ liệu (đọc tự động nếu có):
  data/external/visl_keypoints/manifest.csv   tập ViSignLanguage-Video (prepare_visl.py), chỉ lấy mẫu accepted=1
  data/manifest.csv                           dữ liệu tự quay (collect_data.py)

Chia tập:
  test       = dữ liệu tự quay buổi 2 (ngày khác) — con số báo cáo so với mục tiêu ≥ 85%.
               Nếu chưa có buổi 2 thì tạm giữ 15% ViSL làm test (in cảnh báo).
  validation = 15% còn lại (ViSL + buổi 1), chia phân tầng theo nhãn.
  train      = phần còn lại.

Cách dùng:
  python train.py --model lstm --features pose_hands      # một thí nghiệm
  python train.py --all                                   # 3 mô hình × 2 bộ đặc trưng = 6 thí nghiệm
  python train.py --model gru --epochs 5                  # chạy thử nhanh

Kết quả mỗi thí nghiệm nằm trong runs/<thời gian>_<mô hình>_<đặc trưng>/:
  model.keras, model.tflite, config.json (nhãn + cách tiền xử lý cho dịch vụ suy luận),
  metrics.json, history.csv, confusion_matrix.png, classification_report.txt
Bảng so sánh các lần chạy: runs/results.csv
"""
from __future__ import annotations

import os

os.environ.setdefault("TF_CPP_MIN_LOG_LEVEL", "2")


import argparse
import warnings
import csv
import json
import sys
import time
from datetime import datetime
from pathlib import Path

import numpy as np

import features as ft

warnings.filterwarnings("ignore", message=r"(?s).*tf.lite.Interpreter is deprecated")

ROOT = Path(__file__).resolve().parent
MODELS = ("lstm", "gru", "transformer")
RESULT_FIELDS = ["run", "model", "features", "params", "train_n", "val_n", "test_n", "test_source", "epochs",
                 "val_accuracy", "test_accuracy", "test_precision", "test_recall", "test_f1",
                 "keras_ms", "tflite_ms", "tflite_kb", "train_minutes"]


# ---------------------------------------------------------------- dữ liệu

def load_labels(path: Path) -> list[dict]:
    return sorted(json.loads(path.read_text(encoding="utf-8")), key=lambda l: l["id"])


def read_csv(path: Path) -> list[dict]:
    with path.open(newline="", encoding="utf-8-sig") as f:
        return list(csv.DictReader(f))


def load_samples(data_dir: Path, labels: list[dict]) -> list[dict]:
    """Trả về danh sách {x: (30,1662) thô, y: chỉ số lớp, source: visl|own, signer, session}."""
    by_visl = {l["visl_dir"]: i for i, l in enumerate(labels)}
    by_id = {l["id"]: i for i, l in enumerate(labels)}
    expected = (ft.SEQUENCE_LENGTH, ft.RAW_DIM)
    out, skipped = [], 0

    visl_dir = data_dir / "external" / "visl_keypoints"
    if (visl_dir / "manifest.csv").exists():
        for r in read_csv(visl_dir / "manifest.csv"):
            if r.get("accepted") != "1" or r["slug"] not in by_visl or not r["file"]:
                continue
            x = np.load(visl_dir / r["file"])
            if x.shape != expected or not np.isfinite(x).all():
                skipped += 1
                continue
            out.append({"x": x, "y": by_visl[r["slug"]], "source": "visl", "signer": "visl", "session": 1})
    else:
        print(f"! Không thấy {visl_dir / 'manifest.csv'} — bỏ qua tập ViSL.")

    if (data_dir / "manifest.csv").exists():
        for r in read_csv(data_dir / "manifest.csv"):
            path = data_dir / r["file"]
            if not path.exists() or int(r["label_id"]) not in by_id:
                skipped += 1
                continue
            x = np.load(path)
            if x.shape != expected or not np.isfinite(x).all():
                skipped += 1
                continue
            out.append({"x": x, "y": by_id[int(r["label_id"])], "source": "own",
                        "signer": r["signer"], "session": int(r["session"])})
    else:
        print(f"! Không thấy {data_dir / 'manifest.csv'} — chưa có dữ liệu tự quay.")
    if skipped:
        print(f"! Bỏ qua {skipped} file lỗi/không khớp nhãn.")
    return out


def stratified_split(ys: np.ndarray, frac: float, rng: np.random.Generator) -> tuple[np.ndarray, np.ndarray]:
    """Chia chỉ số thành (phần lớn, phần frac) giữ tỉ lệ từng lớp; lớp có ≥ 2 mẫu luôn có ít nhất 1 mẫu ở phần frac."""
    big, small = [], []
    for c in np.unique(ys):
        idx = rng.permutation(np.flatnonzero(ys == c))
        k = int(round(len(idx) * frac))
        if len(idx) >= 2:
            k = max(1, k)
        small += idx[:k].tolist()
        big += idx[k:].tolist()
    return np.array(sorted(big)), np.array(sorted(small))


def make_splits(samples: list[dict], val_frac: float, seed: int):
    rng = np.random.default_rng(seed)
    test_idx = [i for i, s in enumerate(samples) if s["source"] == "own" and s["session"] == 2]
    pool = np.array([i for i, s in enumerate(samples) if not (s["source"] == "own" and s["session"] == 2)])
    ys = np.array([s["y"] for s in samples])
    if test_idx:
        test_source = "own_session2"
        rest = pool
    else:
        print("! CẢNH BÁO: chưa có dữ liệu tự quay buổi 2 → tạm lấy 15% ViSL làm test. "
              "Con số này KHÔNG phải kết quả cuối cùng.")
        test_source = "visl_holdout"
        keep, held = stratified_split(ys[pool], 0.15, rng)
        rest, test_idx = pool[keep], pool[held].tolist()
    tr, va = stratified_split(ys[rest], val_frac, rng)
    return rest[tr], rest[va], np.array(test_idx), test_source


class SequenceBatches:
    """Sinh batch có tăng cường dữ liệu mới ở mỗi epoch (keras.utils.PyDataset)."""

    def __new__(cls, *a, **k):
        import keras

        class _Seq(keras.utils.PyDataset):
            def __init__(self, x_norm, y, feature_set, batch_size, augment, seed):
                super().__init__()
                self.x, self.y, self.fs, self.bs, self.aug = x_norm, y, feature_set, batch_size, augment
                self.rng = np.random.default_rng(seed)
                self.allow_mirror = feature_set == "pose_hands"
                self.order = self.rng.permutation(len(y))

            def __len__(self):
                return int(np.ceil(len(self.y) / self.bs))

            def __getitem__(self, i):
                idx = self.order[i * self.bs:(i + 1) * self.bs]
                xs = [ft.augment(self.x[j], self.rng, self.allow_mirror) if self.aug else self.x[j] for j in idx]
                return np.stack([ft.select(s, self.fs) for s in xs]), self.y[idx]

            def on_epoch_end(self):
                self.order = self.rng.permutation(len(self.y))

        return _Seq(*a, **k)


# ---------------------------------------------------------------- mô hình

def build_model(name: str, n_features: int, n_classes: int):
    import keras
    from keras import layers

    inp = keras.Input((ft.SEQUENCE_LENGTH, n_features), name="keypoints")
    if name in ("lstm", "gru"):
        rnn = layers.LSTM if name == "lstm" else layers.GRU
        x = rnn(64, return_sequences=True, unroll=True)(inp)
        x = layers.Dropout(0.2)(x)
        x = rnn(128, return_sequences=True, unroll=True)(x)
        x = layers.Dropout(0.2)(x)
        x = rnn(64, unroll=True)(x)
        x = layers.Dense(64, activation="relu")(x)
        x = layers.Dropout(0.4)(x)
    elif name == "transformer":
        d = 128
        x = layers.Dense(d)(inp)
        pos = layers.Embedding(ft.SEQUENCE_LENGTH, d)(keras.ops.arange(ft.SEQUENCE_LENGTH))
        x = x + pos
        for _ in range(2):
            h = layers.LayerNormalization(epsilon=1e-6)(x)
            h = layers.MultiHeadAttention(num_heads=4, key_dim=d // 4, dropout=0.1)(h, h)
            x = x + layers.Dropout(0.1)(h)
            h = layers.LayerNormalization(epsilon=1e-6)(x)
            h = layers.Dense(2 * d, activation="gelu")(h)
            h = layers.Dense(d)(layers.Dropout(0.1)(h))
            x = x + layers.Dropout(0.1)(h)
        x = layers.LayerNormalization(epsilon=1e-6)(x)
        x = layers.GlobalAveragePooling1D()(x)
        x = layers.Dropout(0.4)(x)
    else:
        raise ValueError(name)
    out = layers.Dense(n_classes, activation="softmax", name="probs")(x)
    return keras.Model(inp, out, name=f"vsign_{name}")


# ---------------------------------------------------------------- xuất TFLite và đo tốc độ

def export_tflite(model, path: Path) -> Path:
    """Chuyển sang TFLite. Các lớp RNN dùng unroll=True (30 bước cố định) nên chỉ cần phép toán TFLite gốc."""
    import tensorflow as tf

    import contextlib
    import io

    with contextlib.redirect_stdout(io.StringIO()):          # bỏ phần in chữ ký hàm dài dòng của Keras
        data = tf.lite.TFLiteConverter.from_keras_model(model).convert()
    path.write_bytes(data)
    return path


def time_keras(model, x1: np.ndarray, n: int = 100) -> float:
    """ms mỗi lần suy luận một chuỗi bằng Keras (đã biên dịch đồ thị, không tính lần chạy đầu)."""
    import tensorflow as tf

    fn = tf.function(lambda x: model(x, training=False))
    x1 = tf.constant(x1)
    for _ in range(5):
        fn(x1)
    t = time.perf_counter()
    for _ in range(n):
        fn(x1)
    return (time.perf_counter() - t) / n * 1000


def tflite_predict(path: Path, xs: np.ndarray) -> tuple[np.ndarray, float]:
    """Dự đoán từng mẫu bằng TFLite; trả về (xác suất, ms trung bình mỗi mẫu)."""
    import tensorflow as tf

    it = tf.lite.Interpreter(model_path=str(path))
    i_in, i_out = it.get_input_details()[0]["index"], it.get_output_details()[0]["index"]
    it.resize_tensor_input(i_in, [1, *xs.shape[1:]])
    it.allocate_tensors()
    probs, t = [], time.perf_counter()
    for x in xs:
        it.set_tensor(i_in, x[None].astype(np.float32))
        it.invoke()
        probs.append(it.get_tensor(i_out)[0])
    return np.array(probs), (time.perf_counter() - t) / max(len(xs), 1) * 1000


def save_confusion(cm: np.ndarray, names: list[str], path: Path, title: str) -> None:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    norm = cm / np.maximum(cm.sum(1, keepdims=True), 1)
    fig, ax = plt.subplots(figsize=(11, 10))
    im = ax.imshow(norm, cmap="Blues", vmin=0, vmax=1)
    ax.set_xticks(range(len(names)), names, rotation=90, fontsize=8)
    ax.set_yticks(range(len(names)), names, fontsize=8)
    for i in range(len(names)):
        for j in range(len(names)):
            if cm[i, j]:
                ax.text(j, i, cm[i, j], ha="center", va="center", fontsize=6,
                        color="white" if norm[i, j] > 0.5 else "black")
    ax.set_xlabel("Dự đoán")
    ax.set_ylabel("Thực tế")
    ax.set_title(title)
    fig.colorbar(im, fraction=0.046, pad=0.04)
    fig.tight_layout()
    fig.savefig(path, dpi=130)
    plt.close(fig)


# ---------------------------------------------------------------- một thí nghiệm

def run_experiment(args, model_name: str, feature_set: str, labels, samples, splits) -> dict:
    import keras
    from sklearn.metrics import classification_report, confusion_matrix, precision_recall_fscore_support

    keras.utils.set_random_seed(args.seed)
    tr, va, te, test_source = splits
    n_feat, n_cls = ft.FEATURE_SETS[feature_set], len(labels)
    names = [l["slug"] for l in labels]
    x_norm = np.stack([ft.normalize(s["x"]) for s in samples])
    y = np.array([s["y"] for s in samples])

    run = f"{datetime.now():%Y%m%d-%H%M%S}_{model_name}_{feature_set}"
    out = Path(args.runs_dir) / run
    out.mkdir(parents=True, exist_ok=True)
    print(f"\n=== {run}: train {len(tr)}, val {len(va)}, test {len(te)} ({test_source}) ===")

    model = build_model(model_name, n_feat, n_cls)
    model.compile(optimizer=keras.optimizers.Adam(args.lr), loss="sparse_categorical_crossentropy",
                  metrics=["accuracy"])
    counts = np.bincount(y[tr], minlength=n_cls)
    class_weight = {c: float(len(tr) / (n_cls * max(n, 1))) for c, n in enumerate(counts)}

    train_ds = SequenceBatches(x_norm[tr], y[tr], feature_set, args.batch, not args.no_augment, args.seed)
    x_val = np.stack([ft.select(x, feature_set) for x in x_norm[va]])
    x_test = np.stack([ft.select(x, feature_set) for x in x_norm[te]])
    cbs = [
        keras.callbacks.ModelCheckpoint(out / "model.keras", monitor="val_accuracy", save_best_only=True),
        keras.callbacks.EarlyStopping(monitor="val_accuracy", patience=args.patience, restore_best_weights=True),
        keras.callbacks.ReduceLROnPlateau(monitor="val_loss", factor=0.5, patience=10, min_lr=1e-5),
        keras.callbacks.CSVLogger(out / "history.csv"),
    ]
    t0 = time.time()
    hist = model.fit(train_ds, validation_data=(x_val, y[va]), epochs=args.epochs, class_weight=class_weight,
                     callbacks=cbs, verbose=2 if args.verbose else 0)
    minutes = (time.time() - t0) / 60
    epochs_run = len(hist.history["loss"])
    val_acc = float(max(hist.history["val_accuracy"]))

    tfl = export_tflite(model, out / "model.tflite")
    probs, tflite_ms = tflite_predict(tfl, x_test)
    keras_probs = model.predict(x_test, verbose=0)
    pred = probs.argmax(1)
    agree = float((pred == keras_probs.argmax(1)).mean())
    keras_ms = time_keras(model, x_test[:1])

    acc = float((pred == y[te]).mean())
    p, r, f1, _ = precision_recall_fscore_support(y[te], pred, labels=range(n_cls), average="macro", zero_division=0)
    cm = confusion_matrix(y[te], pred, labels=range(n_cls))
    save_confusion(cm, names, out / "confusion_matrix.png",
                   f"{model_name} / {feature_set} — test {test_source}: acc {acc:.1%}")
    (out / "classification_report.txt").write_text(classification_report(
        y[te], pred, labels=range(n_cls), target_names=names, digits=3, zero_division=0), encoding="utf-8")

    config = {"model": model_name, "features": feature_set, "n_features": n_feat,
              "sequence_length": ft.SEQUENCE_LENGTH, "raw_dim": ft.RAW_DIM, "normalize": "shoulder_center_scale",
              "labels": [{"index": i, "id": l["id"], "slug": l["slug"], "text": l["text"]} for i, l in enumerate(labels)]}
    (out / "config.json").write_text(json.dumps(config, ensure_ascii=False, indent=2), encoding="utf-8")
    metrics = {
        "run": run, "model": model_name, "features": feature_set, "params": int(model.count_params()),
        "train_n": len(tr), "val_n": len(va), "test_n": len(te), "test_source": test_source, "epochs": epochs_run,
        "val_accuracy": round(val_acc, 4), "test_accuracy": round(acc, 4), "test_precision": round(float(p), 4),
        "test_recall": round(float(r), 4), "test_f1": round(float(f1), 4), "keras_ms": round(keras_ms, 2),
        "tflite_ms": round(tflite_ms, 2), "tflite_kb": round(tfl.stat().st_size / 1024, 1),
        "train_minutes": round(minutes, 2),
    }
    (out / "metrics.json").write_text(json.dumps({**metrics, "tflite_keras_agreement": agree}, indent=2),
                                      encoding="utf-8")
    results = Path(args.runs_dir) / "results.csv"
    new = not results.exists()
    with results.open("a", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=RESULT_FIELDS)
        if new:
            w.writeheader()
        w.writerow(metrics)
    print(f"→ val {val_acc:.1%} | test {acc:.1%} | F1 {f1:.3f} | TFLite {tflite_ms:.1f} ms "
          f"({metrics['tflite_kb']} KB) | {epochs_run} epoch, {minutes:.1f} phút | {out}")
    return metrics


def main() -> int:
    p = argparse.ArgumentParser(description="Huấn luyện mô hình nhận diện ký hiệu (LSTM / GRU / Transformer).")
    p.add_argument("--model", choices=MODELS, default="lstm")
    p.add_argument("--features", choices=tuple(ft.FEATURE_SETS), default="pose_hands")
    p.add_argument("--all", action="store_true", help="chạy cả 3 mô hình × 2 bộ đặc trưng")
    p.add_argument("--data-dir", default=str(ROOT / "data"))
    p.add_argument("--labels", default=str(ROOT / "labels.json"))
    p.add_argument("--runs-dir", default=str(ROOT / "runs"))
    p.add_argument("--epochs", type=int, default=200)
    p.add_argument("--patience", type=int, default=30, help="dừng sớm nếu val_accuracy không tăng sau N epoch")
    p.add_argument("--batch", type=int, default=32)
    p.add_argument("--lr", type=float, default=1e-3)
    p.add_argument("--val-frac", type=float, default=0.15)
    p.add_argument("--seed", type=int, default=42)
    p.add_argument("--no-augment", action="store_true")
    p.add_argument("--verbose", action="store_true", help="in tiến trình từng epoch")
    a = p.parse_args()

    labels = load_labels(Path(a.labels))
    samples = load_samples(Path(a.data_dir), labels)
    if not samples:
        sys.exit("Không có dữ liệu. Chép data/external/visl_keypoints/ (prepare_visl.py) "
                 "hoặc quay dữ liệu bằng collect_data.py trước.")
    src = {}
    for s in samples:
        key = "visl" if s["source"] == "visl" else f"tự quay buổi {s['session']}"
        src[key] = src.get(key, 0) + 1
    print(f"Đã nạp {len(samples)} mẫu: " + ", ".join(f"{k} = {v}" for k, v in src.items()))
    missing = [labels[c]["slug"] for c in range(len(labels)) if c not in {s["y"] for s in samples}]
    if missing:
        print(f"! {len(missing)} lớp không có mẫu nào: {', '.join(missing)}")

    splits = make_splits(samples, a.val_frac, a.seed)
    combos = [(m, f) for f in ft.FEATURE_SETS for m in MODELS] if a.all else [(a.model, a.features)]
    for m, f in combos:
        run_experiment(a, m, f, labels, samples, splits)
    print(f"\nBảng so sánh: {Path(a.runs_dir) / 'results.csv'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
