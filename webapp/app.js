// Web app thu thập dữ liệu ký hiệu: quay 30 khung vuông ở 12 khung/giây, kiểm tra tay/người bằng MediaPipe
// trong trình duyệt, rồi tải ảnh lên Google Drive qua Apps Script. Keypoints chính thức được trích sau,
// trên máy huấn luyện, bằng import_web_takes.py (cùng MediaPipe Python với tập ViSL).
import { FilesetResolver, HandLandmarker, PoseLandmarker, DrawingUtils } from
  "https://cdn.jsdelivr.net/npm/@mediapipe/tasks-vision@0.10.14/vision_bundle.mjs";
import { CONFIG } from "./config.js";

const APP_VERSION = "1";
const WASM = "https://cdn.jsdelivr.net/npm/@mediapipe/tasks-vision@0.10.14/wasm";
const HAND_MODEL = "https://storage.googleapis.com/mediapipe-models/hand_landmarker/hand_landmarker/float16/latest/hand_landmarker.task";
const POSE_MODEL = "https://storage.googleapis.com/mediapipe-models/pose_landmarker/pose_landmarker_lite/float16/latest/pose_landmarker_lite.task";
const params = new URLSearchParams(location.search);
const ENDPOINT = params.get("endpoint") || CONFIG.endpoint;   // ?endpoint= để thử với server khác
const DEMO_SRC = params.get("src");                             // ?src=video.mp4 thay camera bằng video (để thử)
const DETECT_INTERVAL_MS = 66;                                  // ~15 lần nhận diện/giây khi không chụp
const IS_TOUCH = matchMedia("(hover: none) and (pointer: coarse)").matches;
// Trình duyệt nhúng trong app (Zalo, Messenger, Facebook, Instagram, TikTok, Line…) thường không cho dùng camera.
const IN_APP = /Zalo|FBAN|FBAV|FB_IAB|Messenger|Instagram|Line\/|TikTok|musical_ly|; wv\)/i.test(navigator.userAgent);

const $ = (id) => document.getElementById(id);
const S = {
  labels: [], signer: "", session: 1, quota: 3,
  counts: {},            // label id → số lượt đã lưu (đã tải lên + đang chờ)
  idx: 0, state: "idle", running: false, tState: 0,
  frames: [], flags: [], tFirst: 0, tNext: 0,
  saved: [],             // tên các lượt lưu trong phiên này (để xóa lượt cuối)
  hand: null, pose: null, lastTs: 0, lastDetect: -1e9, hr: null, pr: null, handOn: false, poseOn: false, wakeLock: null,
  stream: null, message: "", tMessage: 0,
  refs: null, refSlug: null, showRef: true,   // video mẫu (references.json)
  shoulder: null, frame: { ok: false, text: "Chưa thấy người" },   // kiểm tra khoảng cách (độ rộng vai, làm mượt)
};

// ---------------------------------------------------------------- tiện ích

function asciiSlug(text) {   // giống collect_data.ascii_slug: 'Lê Thị Bảo Trân' → 'lethibaotran'
  return text.replace(/đ/g, "d").replace(/Đ/g, "D").normalize("NFKD").replace(/[̀-ͯ]/g, "")
    .toLowerCase().replace(/[^a-z0-9]/g, "");
}
const pad = (n, w = 2) => String(n).padStart(w, "0");
function stamp(d = new Date()) {
  return `${d.getFullYear()}${pad(d.getMonth() + 1)}${pad(d.getDate())}-${pad(d.getHours())}${pad(d.getMinutes())}${pad(d.getSeconds())}-${pad(d.getMilliseconds(), 3)}`;
}
function say(text, kind = "") { S.message = text; S.messageKind = kind; S.tMessage = performance.now(); }
const label = () => S.labels[S.idx];

async function api(body) {
  if (!ENDPOINT) throw new Error("Chưa cấu hình endpoint trong config.js");
  const r = await fetch(ENDPOINT, { method: "POST", headers: { "Content-Type": "text/plain;charset=utf-8" },
    body: JSON.stringify(body) });
  if (!r.ok) throw new Error(`HTTP ${r.status}`);
  const data = await r.json();
  // Apps Script thiếu cấu hình: thử lại cũng vô ích → dừng hẳn (khác với lỗi mạng tạm thời).
  if (!data.ok) throw Object.assign(new Error(data.error || "Lỗi máy chủ"), { fatal: /chưa có thuộc tính/.test(data.error || "") });
  return data;
}

// ---------------------------------------------------------------- lỗi camera → hướng dẫn tiếng Việt

const IN_APP_HELP = "Trang đang mở trong trình duyệt của ứng dụng (Zalo, Messenger, Facebook…), loại này thường không cho dùng camera. "
  + "Bấm nút ⋯ (hoặc ⋮) ở góc màn hình → \"Mở bằng trình duyệt\" / \"Open in browser\", hoặc chép link dán vào Chrome (Android) / Safari (iPhone).";

function cameraError(e) {
  if (IN_APP) return IN_APP_HELP;
  const reload = "Sửa xong thì tải lại trang và bấm bắt đầu lần nữa.";
  switch (e.name) {
    case "NotFoundError":
    case "OverconstrainedError":
    case "DevicesNotFoundError":
      return "Không tìm thấy camera nào trên máy này. Kiểm tra:\n"
        + "• Laptop: camera không bị che bởi nắp trượt hay tắt bằng phím Fn / phím camera.\n"
        + "• Windows: Settings → Privacy & security → Camera → bật \"Camera access\" và \"Let desktop apps access your camera\".\n"
        + "• macOS: System Settings → Privacy & Security → Camera → bật cho trình duyệt đang dùng.\n"
        + "• Điện thoại: mở link bằng Chrome (Android) hoặc Safari (iPhone), không mở trong Zalo/Messenger.\n" + reload;
    case "NotAllowedError":
    case "PermissionDeniedError":
      return "Trình duyệt hoặc hệ điều hành đang chặn camera.\n"
        + "• Máy tính: bấm biểu tượng ổ khóa / camera bên trái thanh địa chỉ → Camera: Cho phép (Allow). "
        + "macOS còn cần bật trình duyệt trong System Settings → Privacy & Security → Camera.\n"
        + "• Android: bấm biểu tượng bên trái thanh địa chỉ → Quyền → Camera: Cho phép; nếu vẫn lỗi: Cài đặt → Ứng dụng → Chrome → Quyền → Camera.\n"
        + "• iPhone: Cài đặt → Safari → Camera → Cho phép (hoặc Hỏi), rồi mở lại trang.\n" + reload;
    case "NotReadableError":
    case "TrackStartError":
    case "AbortError":
      return "Camera đang bị ứng dụng khác dùng (Zoom, Google Meet, Teams, OBS, tab khác…). Đóng ứng dụng đó rồi thử lại.\n" + reload;
    case "SecurityError":
    case "TypeError":
      return "Trình duyệt không cho dùng camera ở trang này. Hãy mở đúng link https bằng Chrome, Edge hoặc Safari (không mở trong Zalo/Messenger).\n"
        + "iPhone cần iOS 14.3 trở lên.";
    default:
      return `Không mở được camera (${e.name}: ${e.message}).\n${reload}`;
  }
}

// Thử lần lượt từ yêu cầu chi tiết đến đơn giản nhất: một số camera / driver từ chối độ phân giải hoặc deviceId đã lưu.
async function getStream(deviceId) {
  if (!navigator.mediaDevices?.getUserMedia) throw Object.assign(new Error("no mediaDevices"), { name: "SecurityError" });
  // Không chọn camera cụ thể thì ưu tiên camera trước (facingMode "user"): điện thoại Android hay mặc định camera sau.
  const which = deviceId ? { deviceId: { exact: deviceId } } : { facingMode: { ideal: "user" } };
  const attempts = [
    { ...which, width: { ideal: 1280 }, height: { ideal: 720 } },
    which,
    true,
  ];
  let last;
  for (const video of attempts) {
    try { return await navigator.mediaDevices.getUserMedia({ audio: false, video }); }
    catch (e) { last = e; if (e.name === "NotAllowedError" || e.name === "SecurityError") break; }
  }
  throw last;
}

// ---------------------------------------------------------------- hàng đợi tải lên (IndexedDB, không mất khi tải lại trang)

const Queue = {
  db: null, busy: false, error: "", backoff: 0,
  open() {
    return new Promise((res, rej) => {
      const req = indexedDB.open("vsign-collect", 1);
      req.onupgradeneeded = () => req.result.createObjectStore("takes", { keyPath: "name" });
      req.onsuccess = () => { this.db = req.result; res(); };
      req.onerror = () => rej(req.error);
    });
  },
  tx(mode, fn) {
    return new Promise((res, rej) => {
      const t = this.db.transaction("takes", mode);
      const out = fn(t.objectStore("takes"));
      t.oncomplete = () => res(out && "result" in out ? out.result : undefined);
      t.onerror = () => rej(t.error);
    });
  },
  add(take) { return this.tx("readwrite", (s) => s.put(take)); },
  del(name) { return this.tx("readwrite", (s) => s.delete(name)); },
  all() { return this.tx("readonly", (s) => s.getAll()); },
  async has(name) { return (await this.tx("readonly", (s) => s.getKey(name))) !== undefined; },
  async pump() {
    if (this.busy) return;
    this.busy = true;
    try {
      for (;;) {
        const items = await this.all();
        renderUpload(items.length);
        if (!items.length) break;
        try {
          await api({ action: "upload", name: items[0].name, meta: items[0].meta, frames: items[0].frames });
          await this.del(items[0].name);
          this.error = ""; this.backoff = 0;
        } catch (e) {
          this.error = e.message;
          renderUpload(items.length);
          if (e.fatal) break;
          this.backoff = Math.min(60, (this.backoff || 2.5) * 2);
          await new Promise((r) => setTimeout(r, this.backoff * 1000));
        }
      }
    } finally { this.busy = false; }
  },
};

function renderUpload(pending) {
  const el = $("upload");
  el.classList.toggle("bad", !!Queue.error);
  if (Queue.error) el.textContent = `Chưa tải lên được (${Queue.error}). Còn ${pending} lượt chờ, sẽ tự thử lại. Đừng xóa dữ liệu trình duyệt.`;
  else el.textContent = pending ? `Đang tải lên Drive… còn ${pending} lượt` : "Mọi lượt đã lưu lên Drive ✓";
}

// ---------------------------------------------------------------- tiến độ

async function loadCounts() {
  S.counts = {};
  const names = [];
  try {
    names.push(...(await api({ action: "list", signer: S.signer, session: S.session })).names);
  } catch (e) {
    if (e.fatal) throw e;
    say(`Không lấy được tiến độ từ Drive (${e.message}); chỉ đếm các lượt trên máy này.`, "bad");
  }
  for (const t of await Queue.all()) if (t.meta.signer === S.signer && t.meta.session === S.session) names.push(t.name);
  for (const n of new Set(names.map((n) => n.replace(/\.zip$/, "")))) {
    const m = n.match(/^[a-z0-9]+_s[12]_(\d{2})_/);
    if (m) S.counts[+m[1]] = (S.counts[+m[1]] || 0) + 1;
  }
}

const mine = (l = label()) => S.counts[l.id] || 0;
const nextMissing = (from) => {
  for (let k = 0; k < S.labels.length; k++) {
    const i = (from + k) % S.labels.length;
    if (mine(S.labels[i]) < S.quota) return i;
  }
  return -1;
};

function renderList() {
  const ol = $("signs");
  ol.replaceChildren(...S.labels.map((l, i) => {
    const li = document.createElement("li");
    const n = mine(l);
    li.className = (i === S.idx ? "current " : "") + (n >= S.quota ? "done" : "");
    li.innerHTML = `<span>${pad(l.id)}. ${l.text}</span><span class="n">${n}/${S.quota}</span>`;
    li.onclick = () => { if (S.state === "idle") { S.idx = i; renderList(); } };
    return li;
  }));
  const done = S.labels.reduce((a, l) => a + Math.min(mine(l), S.quota), 0);
  $("progress").textContent = `${done}/${S.labels.length * S.quota}`;
  ol.children[S.idx]?.scrollIntoView({ block: "nearest" });
}

// ---------------------------------------------------------------- camera và MediaPipe

async function initModels() {
  const fileset = await FilesetResolver.forVisionTasks(WASM);
  const make = async (Cls, model, extra, delegate) => Cls.createFromOptions(fileset, {
    baseOptions: { modelAssetPath: model, delegate }, runningMode: "VIDEO", ...extra });
  for (const delegate of ["GPU", "CPU"]) {
    try {
      S.hand = await make(HandLandmarker, HAND_MODEL, { numHands: 2 }, delegate);
      S.pose = await make(PoseLandmarker, POSE_MODEL, { numPoses: 1 }, delegate);
      return;
    } catch (e) { if (delegate === "CPU") throw e; }
  }
}

async function openCamera(deviceId) {
  const video = $("video");
  S.stream?.getTracks().forEach((t) => t.stop());
  if (DEMO_SRC) {
    video.src = DEMO_SRC; video.loop = true;
  } else {
    S.stream = await getStream(deviceId);
    video.srcObject = S.stream;
  }
  await video.play();
  if (!DEMO_SRC) {
    const sel = $("camera");
    const cams = (await navigator.mediaDevices.enumerateDevices()).filter((d) => d.kind === "videoinput");
    const current = S.stream.getVideoTracks()[0].getSettings().deviceId;
    sel.replaceChildren(...cams.map((c, i) => new Option(c.label || `Camera ${i + 1}`, c.deviceId, false, c.deviceId === current)));
    sel.hidden = cams.length < 2;
  } else $("camera").hidden = true;
}

function cameraInfo() {
  const v = $("video");
  const track = S.stream?.getVideoTracks()[0];
  return { camera: track?.label || (DEMO_SRC ? `demo:${DEMO_SRC}` : ""), source_width: v.videoWidth, source_height: v.videoHeight };
}

// ---------------------------------------------------------------- vòng lặp: cắt vuông → nhận diện → vẽ → quay

function loop() {
  const video = $("video"), work = $("work"), view = $("view");
  if (video.readyState >= 2 && video.videoWidth) {
    const s = Math.min(video.videoWidth, video.videoHeight);
    const wctx = work.getContext("2d");
    wctx.drawImage(video, (video.videoWidth - s) / 2, (video.videoHeight - s) / 2, s, s, 0, 0, CONFIG.size, CONFIG.size);

    const now = performance.now();
    // Nhận diện tối đa ~15 lần/giây, và luôn chạy đúng khung sắp chụp: chạy mọi khung (60 lần/giây) làm điện thoại
    // và laptop yếu quá tải, tụt nhịp chụp dưới minFps.
    const captureDue = S.state === "recording" && now >= S.tNext;
    if (captureDue || now - S.lastDetect >= DETECT_INTERVAL_MS) {
      const ts = Math.max(Math.round(now), S.lastTs + 1);
      S.lastTs = ts;
      S.lastDetect = now;
      S.hr = S.hand.detectForVideo(work, ts);
      S.pr = S.pose.detectForVideo(work, ts);
      S.handOn = S.hr.landmarks.length > 0;
      S.poseOn = S.pr.landmarks.length > 0;
      updateFraming(S.pr);
    }

    tick(now, work);

    const vctx = view.getContext("2d");
    vctx.drawImage(work, 0, 0);
    const du = new DrawingUtils(vctx);
    const hr = S.hr || { landmarks: [] }, pr = S.pr || { landmarks: [] };
    for (const lm of pr.landmarks) du.drawConnectors(lm, PoseLandmarker.POSE_CONNECTIONS, { color: "#50b450", lineWidth: 2 });
    for (const lm of hr.landmarks) {
      du.drawConnectors(lm, HandLandmarker.HAND_CONNECTIONS, { color: "#ff8c3c", lineWidth: 2 });
      du.drawLandmarks(lm, { color: "#ffffff", radius: 2 });
    }
    render(now);
  }
  requestAnimationFrame(loop);
}

function tick(now, work) {
  if (S.state === "countdown" && now - S.tState >= CONFIG.countdown * 1000) {
    if (!S.frame.ok) {   // người quay di chuyển trong lúc đếm ngược → dừng, không quay lượt hỏng
      Object.assign(S, { running: false, state: "idle" });
      say(`Đã dừng: ${S.frame.text}. Chỉnh lại rồi bấm Bắt đầu quay.`, "bad");
      return;
    }
    Object.assign(S, { state: "recording", frames: [], flags: [], tFirst: now, tNext: now });
  }
  if (S.state !== "recording" || now < S.tNext) return;
  S.tNext += 1000 / CONFIG.fps;
  S.frames.push(work.toDataURL("image/jpeg", CONFIG.jpegQuality).split(",")[1]);   // ảnh gốc, CHƯA lật gương
  S.flags.push([S.handOn, S.poseOn, S.lastShoulder]);
  if (S.frames.length < CONFIG.frames) return;
  finishTake(now);
}

async function finishTake(now) {
  const l = label();
  const handRatio = S.flags.filter((f) => f[0]).length / S.flags.length;
  const poseRatio = S.flags.filter((f) => f[1]).length / S.flags.length;
  const fps = (CONFIG.frames - 1) / Math.max((now - S.tFirst) / 1000, 1e-6);
  const widths = S.flags.map((f) => f[2]).filter((w) => w != null).sort((a, b) => a - b);
  const shoulder = widths.length ? widths[Math.floor(widths.length / 2)] : null;   // trung vị cả lượt
  const frames = S.frames;
  S.frames = [];
  S.state = "idle";
  if (fps < CONFIG.minFps) say(`Loại: máy chỉ chụp được ${fps.toFixed(1)} khung/giây (cần ${CONFIG.fps}). Đóng bớt tab/ứng dụng rồi quay lại`, "bad");
  else if (handRatio < CONFIG.minHandRatio) say(`Loại: chỉ thấy tay ở ${Math.round(handRatio * 100)}% số khung, quay lại`, "bad");
  else if (poseRatio < CONFIG.minPoseRatio) say(`Loại: chỉ thấy người ở ${Math.round(poseRatio * 100)}% số khung, quay lại`, "bad");
  else if (shoulder != null && shoulder > CONFIG.maxShoulder) say("Loại: quá gần camera trong lúc quay. Lùi ra xa (thấy đến thắt lưng) rồi quay lại", "bad");
  else if (shoulder != null && shoulder < CONFIG.minShoulder) say("Loại: quá xa camera trong lúc quay. Lại gần hơn rồi quay lại", "bad");
  else {
    const name = `${S.signer}_s${S.session}_${pad(l.id)}_${l.slug}_${stamp()}`;
    const meta = { name, label_id: l.id, slug: l.slug, text: l.text, signer: S.signer, session: S.session,
      recorded_at: new Date().toISOString(), frames: CONFIG.frames, size: CONFIG.size, fps: +fps.toFixed(1),
      browser_hand_ratio: +handRatio.toFixed(2), browser_pose_ratio: +poseRatio.toFixed(2), browser_shoulder_width: shoulder == null ? null : +shoulder.toFixed(3),
      ...cameraInfo(), user_agent: navigator.userAgent, app_version: APP_VERSION };
    await Queue.add({ name, meta, frames });
    S.saved.push(name);
    S.counts[l.id] = mine(l) + 1;
    say(`Đã lưu lượt ${mine(l)}/${S.quota} ✓ Đưa tay về bụng`, "ok");
    Queue.pump();
  }
  if (S.running && mine(l) < S.quota) Object.assign(S, { state: "countdown", tState: performance.now() });
  else if (S.running) {
    S.running = false;
    const nxt = nextMissing(S.idx + 1);
    if (nxt < 0) say("Đã quay đủ tất cả ký hiệu của buổi này. Cảm ơn bạn!", "ok");
    else { S.idx = nxt; say(`Đủ lượt. Ký hiệu tiếp theo: ${label().text}. Bấm Space để quay.`, "ok"); }
  }
  renderList();
}

// Khoảng cách: độ rộng vai so với cạnh khung vuông, cùng hình học với tập ViSL (trung vị 0,33; 5–95%: 0,29–0,42).
// Quá gần thì mất tay và thân, quá xa thì bàn tay quá nhỏ. Độ rộng được làm mượt, và trạng thái xấu phải kéo dài
// FRAME_BAD_MS mới tính (nhận diện chập chờn một hai khung, ví dụ tay che vai, không làm dừng quay).
const FRAME_BAD_MS = 700;
const FRAME_OK = { ok: true, short: "Khoảng cách: tốt", big: "", hint: "", text: "" };

function measureFraming(pr) {
  const lm = pr?.landmarks?.[0];
  const l = lm?.[11], r = lm?.[12];
  if (!lm) return [null, { ok: false, short: "Chưa thấy người", big: "Chưa thấy người", hint: "Vào giữa khung, đủ sáng", text: "chưa thấy người trong khung" }];
  if ((l.visibility ?? 1) < 0.5 || (r.visibility ?? 1) < 0.5)   // thấy người nhưng vai bị cắt khỏi khung: gần như luôn là quá gần
    return [null, { ok: false, short: "Quá gần", big: "↙ Lùi ra xa", hint: "Chưa thấy rõ hai vai: cần thấy từ đầu đến thắt lưng", text: "chưa thấy rõ hai vai, lùi ra xa" }];
  const w = Math.hypot(l.x - r.x, l.y - r.y), cx = (l.x + r.x) / 2;
  S.shoulder = S.shoulder == null ? w : 0.7 * S.shoulder + 0.3 * w;
  const sw = S.shoulder;
  if (sw > CONFIG.maxShoulder) return [w, { ok: false, short: "Quá gần", big: "↙ Lùi ra xa", hint: "Cần thấy từ đầu đến thắt lưng và cả hai tay", text: "quá gần camera, lùi ra xa" }];
  if (sw < CONFIG.minShoulder) return [w, { ok: false, short: "Quá xa", big: "Lại gần hơn ↗", hint: "Người đang quá nhỏ trong khung", text: "quá xa camera, lại gần hơn" }];
  if (cx < 0.3 || cx > 0.7) return [w, { ok: false, short: "Lệch khung", big: "Vào giữa khung", hint: "Dịch người hoặc máy cho cân", text: "đang lệch khỏi giữa khung" }];
  return [w, FRAME_OK];
}

function updateFraming(pr) {
  const [w, status] = measureFraming(pr);
  S.lastShoulder = w;
  const now = performance.now();
  if (status.ok) { S.badSince = null; S.everOk = true; S.frame = status; return; }
  S.badSince ??= now;
  if (now - S.badSince >= FRAME_BAD_MS || !S.everOk) S.frame = status;   // lần đầu chưa từng tốt thì báo ngay
}

// Video mẫu ViSL của ký hiệu hiện tại (lật như gương trong CSS) và link tham khảo QIPEDC.
function showReference(l) {
  S.refSlug = l.slug;
  const r = S.refs?.signs?.[l.slug];
  const v = $("ref");
  if (r) { v.src = r.video; v.play().catch(() => {}); } else v.removeAttribute("src");
  const links = $("ref-links");
  links.replaceChildren();
  if (r?.links?.length) {
    links.append("Tham khảo thêm (có thể khác biến thể, ưu tiên làm theo video mẫu): ");
    r.links.forEach((k, i) => {
      const a = document.createElement("a");
      Object.assign(a, { href: k.url, target: "_blank", rel: "noopener", textContent: k.label });
      if (i) links.append(" · ");
      links.append(a);
    });
  }
}

function render(now) {
  const l = label();
  $("sign-name").textContent = l.text;
  $("sign-count").textContent = `Ký hiệu ${pad(l.id)}/${S.labels.length} · đã quay ${mine()}/${S.quota}`;
  const vp = $("viewport");
  if (l.slug !== S.refSlug) showReference(l);
  const hasRef = !!S.refs?.signs?.[l.slug];
  $("ref-box").hidden = !hasRef || !S.showRef || S.state === "recording";   // ẩn khi quay để người quay nhìn thẳng camera
  $("ref-toggle").hidden = !hasRef || S.state === "recording";
  vp.classList.toggle("recording", S.state === "recording");
  vp.classList.toggle("countdown", S.state === "countdown");
  const [main, sub] = S.state === "countdown" ? [Math.ceil(CONFIG.countdown - (now - S.tState) / 1000), "Hai tay đặt ở bụng"]
    : S.state === "recording" ? ["● Làm ký hiệu", "xong thì đưa tay về bụng"]
    : !S.frame.ok ? [S.frame.big, S.frame.hint] : ["", ""];
  $("big-main").textContent = main;
  $("big-sub").textContent = sub;
  $("rec-bar").hidden = S.state !== "recording";
  $("rec-fill").style.width = `${(S.frames.length / CONFIG.frames) * 100}%`;
  const ff = $("frame-flag");
  ff.textContent = S.frame.ok ? "Khoảng cách: tốt" : S.frame.short;
  ff.classList.toggle("on", S.frame.ok);
  $("toggle").classList.toggle("primary", S.state !== "idle" || S.frame.ok);
  $("hand-flag").classList.toggle("on", S.handOn);
  $("pose-flag").classList.toggle("on", S.poseOn);
  $("message").textContent = now - S.tMessage < 4000 ? S.message : "";
  $("toggle").firstChild.textContent = S.state === "idle" ? "Bắt đầu quay " : "Tạm dừng ";
  for (const id of ["prev", "next", "undo", "camera", "change"]) $(id).disabled = S.state !== "idle";
}

// ---------------------------------------------------------------- điều khiển

function toggle() {
  if (S.state === "idle") {
    if (mine() >= S.quota) return say("Ký hiệu này đã đủ lượt. Chọn ký hiệu khác ở danh sách bên phải.");
    if (!S.frame.ok) return say(`Chưa quay được: ${S.frame.text}.`, "bad");
    Object.assign(S, { running: true, state: "countdown", tState: performance.now() });
  } else {
    Object.assign(S, { running: false, state: "idle", frames: [] });
    say("Tạm dừng, lượt đang quay dở đã bỏ.");
  }
}

async function undo() {
  const name = S.saved.pop();
  if (!name) return say("Phiên này chưa lưu lượt nào.");
  try {
    if (await Queue.has(name)) await Queue.del(name);
    else await api({ action: "delete", name: name + ".zip" });
    const id = +name.match(/_s[12]_(\d{2})_/)[1];
    S.counts[id] = Math.max(0, (S.counts[id] || 1) - 1);
    S.idx = S.labels.findIndex((l) => l.id === id);
    say("Đã xóa lượt vừa lưu.", "ok");
    renderList();
    renderUpload((await Queue.all()).length);
  } catch (e) {
    S.saved.push(name);
    say(`Không xóa được: ${e.message}`, "bad");
  }
}

function move(step) {
  if (S.state !== "idle") return;
  S.idx = (S.idx + step + S.labels.length) % S.labels.length;
  renderList();
}

document.addEventListener("keydown", (e) => {
  if ($("studio").hidden || e.target.tagName === "INPUT") return;
  if (e.code === "Space") { e.preventDefault(); toggle(); }
  else if (e.key === "ArrowRight" || e.key === "n") move(1);
  else if (e.key === "ArrowLeft" || e.key === "p") move(-1);
  else if (e.key === "d" && S.state === "idle") undo();
});

// ---------------------------------------------------------------- khởi động

function restoreSetup() {
  try {
    const saved = JSON.parse(localStorage.getItem("vsign-setup") || "{}");
    if (saved.signerName) $("signer").value = saved.signerName;
    if (saved.session) document.querySelector(`input[name=session][value="${saved.session}"]`).checked = true;
  } catch { /* localStorage có thể bị chặn */ }
  $("signer-slug").textContent = asciiSlug($("signer").value) || "–";
}

async function start() {
  const err = $("setup-error");
  err.hidden = true;
  S.signer = asciiSlug($("signer").value);
  S.session = +document.querySelector("input[name=session]:checked").value;
  S.quota = CONFIG.quota[S.session];
  if (!S.signer) { err.textContent = "Nhập tên người quay (có ít nhất một chữ cái hoặc số)."; err.hidden = false; return; }
  try { localStorage.setItem("vsign-setup", JSON.stringify({ signerName: $("signer").value, session: S.session })); } catch {}

  const btn = $("start");
  btn.disabled = true;
  let stage = "server";
  try {
    btn.textContent = "Đang kết nối và lấy tiến độ…";
    await loadCounts();
    stage = "camera";
    btn.textContent = "Đang mở camera…";
    await openCamera();
    stage = "models";
    if (!S.hand) { btn.textContent = "Đang tải mô hình nhận diện…"; await initModels(); }
  } catch (e) {
    err.textContent = stage === "server" ? (e.fatal ? `${e.message}.` : `Không kết nối được máy chủ: ${e.message}`)
      : stage === "camera" ? cameraError(e)
      : `Không tải được mô hình nhận diện (${e.message}). Kiểm tra mạng rồi thử lại.`;
    err.hidden = false;
    btn.disabled = false; btn.textContent = "Mở camera và bắt đầu";
    return;
  }
  const work = $("work"), view = $("view");
  work.width = work.height = view.width = view.height = CONFIG.size;
  S.idx = Math.max(0, nextMissing(0));
  $("setup").hidden = true;
  $("studio").hidden = false;
  $("who").textContent = `${S.signer} · buổi ${S.session}`;
  renderList();
  Queue.pump();
  if (!S.looping) { S.looping = true; requestAnimationFrame(loop); }
  keepAwake();
}

// Giữ màn hình sáng trong lúc quay (điện thoại / laptop tự tắt màn hình sẽ dừng camera). Trình duyệt bỏ khóa khi đổi tab,
// nên xin lại khi quay về.
async function keepAwake() {
  try { if ("wakeLock" in navigator && !document.hidden) S.wakeLock = await navigator.wakeLock.request("screen"); } catch { /* không hỗ trợ */ }
}
document.addEventListener("visibilitychange", () => {
  if (!document.hidden && !$("studio").hidden) keepAwake();
  if (document.hidden && S.state !== "idle") {   // ẩn tab giữa chừng làm nhịp chụp sai → bỏ lượt đang quay
    Object.assign(S, { running: false, state: "idle", frames: [] });
    say("Đã tạm dừng vì rời khỏi trang. Lượt đang quay dở đã bỏ.");
  }
});

async function main() {
  try { S.labels = (await (await fetch("labels.json")).json()).sort((a, b) => a.id - b.id); }
  catch (e) { $("setup-error").textContent = `Không tải được labels.json: ${e.message}`; $("setup-error").hidden = false; return; }
  await Queue.open();
  try {
    S.refs = await (await fetch("references.json")).json();
    $("credit").innerHTML = `Video mẫu: <a href="${S.refs.source_url}" target="_blank" rel="noopener">${S.refs.source}</a>`;
  } catch { S.refs = null; }   // thiếu video mẫu vẫn quay được
  try { S.showRef = localStorage.getItem("vsign-hide-ref") !== "1"; } catch {}
  $("ref-toggle").onclick = () => {
    S.showRef = !S.showRef;
    $("ref-toggle").textContent = S.showRef ? "Ẩn mẫu" : "Xem mẫu";
    try { localStorage.setItem("vsign-hide-ref", S.showRef ? "0" : "1"); } catch {}
  };
  $("ref-toggle").textContent = S.showRef ? "Ẩn mẫu" : "Xem mẫu";
  $("ref-box").onclick = () => $("ref-box").classList.toggle("big");
  restoreSetup();
  if (IN_APP) { $("inapp").textContent = IN_APP_HELP; $("inapp").hidden = false; }
  if (IS_TOUCH) document.body.classList.add("touch");
  $("signer").addEventListener("input", () => { $("signer-slug").textContent = asciiSlug($("signer").value) || "–"; });
  $("start").onclick = start;
  $("toggle").onclick = toggle;
  $("prev").onclick = () => move(-1);
  $("next").onclick = () => move(1);
  $("undo").onclick = undo;
  $("camera").onchange = (e) => openCamera(e.target.value).catch((x) => say(cameraError(x).split("\n")[0], "bad"));
  $("change").onclick = () => { $("studio").hidden = true; $("setup").hidden = false; $("start").disabled = false; $("start").textContent = "Mở camera và bắt đầu"; };
  window.addEventListener("beforeunload", (e) => { if (Queue.busy) e.preventDefault(); });
}

main();
