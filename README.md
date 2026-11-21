# VSign

**Real-time Vietnamese Sign Language recognition with IoT.**

VSign films a person signing with a webcam, recognizes the sign with MediaPipe Holistic and an LSTM model, and sends the result over MQTT to an ESP32 device. The device shows the word on an OLED screen and signals it with an LED and a buzzer. The goal is to help deaf and hearing people communicate more easily.

> Course project for CE340 (University of Information Technology, VNU-HCM).

## How it works

```
Webcam ──► Camera node ──► MQTT broker ──► Inference service ──► MQTT broker ──► ESP32 (OLED, LED, buzzer)
           (MediaPipe       (Mosquitto)    (LSTM model)          sign/prediction └► Node-RED dashboard
            keypoints)
           sign/keypoints
```

1. **Camera node**: extracts 30 frames × 1662 keypoints per sign with MediaPipe Holistic and publishes them to `sign/keypoints`.
2. **Inference service**: classifies the sequence and publishes the label, confidence and timestamp to `sign/prediction`.
3. **ESP32 device**: shows the Vietnamese word (with accents) on a 1.3" 128×64 OLED.
4. **Node-RED dashboard**: shows the current label, a confidence chart and the recognition history.

## Status

| Part | Status |
|---|---|
| Keypoint extraction (`keypoints.py`) | ✅ Done |
| Webcam data collection tool (`collect_data.py`) | ✅ Done |
| ViSignLanguage-Video preprocessing (`prepare_visl.py`) | ✅ Done: 970 samples for 30 signs |
| Model training (LSTM / GRU / Transformer) | 🚧 In progress |
| MQTT nodes, ESP32 firmware, Node-RED dashboard | 🚧 In progress |

## Vocabulary

There are 30 everyday signs, listed in [`labels.json`](labels.json):

Chào, Cảm ơn, Đồng ý, Mời vào, Giúp, Xin phép, Tôi, Họ, Đâu, Ăn, Uống, Đi, Chạy, Nghe, Nhìn, Nói, Biết, Nhớ, Mua, Nghỉ ngơi, Thức dậy, Chậm lại, Nhà, Trường học, Bệnh viện, Hôm nay, Trưa, Chiều, Cứu, Khóc

## Features

Each frame becomes a vector of **1662** numbers, always in this order:

| Part | Points | Values |
|---|---|---|
| Pose | 33 × (x, y, z, visibility) | 132 |
| Face | 468 × (x, y, z) | 1404 |
| Left hand | 21 × (x, y, z) | 63 |
| Right hand | 21 × (x, y, z) | 63 |

A part that isn't detected is filled with zeros. One sample is a `.npy` file of shape **(30, 1662)**, captured at 12 frames per second.

> At inference time, the camera node must process frames exactly as during data collection: center-square crop, un-mirrored frame, 12 FPS sampling. Otherwise the features won't match the training data.

## Setup

Requires Python 3.10+ and a webcam.

```bash
pip install -r requirements.txt
```

The MediaPipe model (`models/holistic_landmarker.task`) is downloaded automatically the first time it's needed.

## Usage

### Record your own data

```bash
python collect_data.py --signer tran --session 1   # session 1: 3 takes per sign (train/validation)
python collect_data.py --signer tran --session 2   # session 2, on another day: 2 takes per sign (test)
python collect_data.py --signer tran --session 1 --start 7   # start from sign #7
python collect_data.py --summary                    # check what has been recorded
```

Keys in the recording window:

| Key | Action |
|---|---|
| `SPACE` | Start / pause recording the current sign |
| `N` / `P` | Next / previous sign |
| `D` | Delete the last saved take |
| `C` | Switch camera |
| `Q` / `ESC` | Quit |

Output goes to `data/raw/<id>_<slug>/` and each take is logged in `data/manifest.csv`.

### Preprocess ViSignLanguage-Video

Download `dataset.zip` from [ViSignLanguage-Video on Hugging Face](https://huggingface.co/datasets/star092304/ViSignLanguage-Video) (CC-BY-4.0) into `data/external/visl_download/`, then run:

```bash
python prepare_visl.py --zip data/external/visl_download/dataset.zip   # safe to re-run; skips finished videos
python prepare_visl.py --report                                         # per-class statistics
```

The script drops corrupted single-color frames, merges duplicate videos, extracts keypoints, resamples each video to 30 frames, and rejects samples where hands or body are rarely visible. Output goes to `data/external/visl_keypoints/`.

## Project structure

```
VSign/
├── keypoints.py       # MediaPipe Holistic feature extraction (shared by all tools)
├── collect_data.py    # Webcam recording tool
├── prepare_visl.py    # ViSignLanguage-Video → keypoints
├── labels.json        # The 30 signs
├── requirements.txt
├── data/              # Not in git (datasets and keypoints)
└── models/            # Not in git (downloaded automatically)
```

## Tech stack

MediaPipe Holistic · TensorFlow / TFLite · Eclipse Mosquitto (MQTT) · paho-mqtt · ESP32 (Arduino, PubSubClient, U8g2) · Node-RED
