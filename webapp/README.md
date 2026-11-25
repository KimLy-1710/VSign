# Web app thu thập dữ liệu

Thành viên ở xa mở một đường link, quay bằng camera có sẵn của máy (laptop hoặc camera trước điện thoại), và mỗi lượt đạt yêu cầu được tải thẳng lên một thư mục Google Drive chung. Không cần cài Python, không cần đăng nhập, không cần mã.

```
Trình duyệt thành viên ──(Apps Script)──────────► thư mục Drive ──(đồng bộ)──► máy huấn luyện
  quay 30 khung vuông, 12 khung/giây                một zip mỗi lượt                import_web_takes.py
  kiểm tra tay/người bằng MediaPipe JS                                                → data/raw, data/manifest.csv
```

- Mỗi lượt là một file zip gồm 30 ảnh JPEG 480×480 (chưa lật gương), `meta.json`, và từ app bản 3 thêm **clip video gốc** `video.webm` (Chrome, Edge, Firefox) hoặc `video.mp4` (Safari, iPhone). Tổng khoảng 1,5–2 MB. Tên file có dạng `<người>_s<buổi>_<id>_<slug>_<thời gian>.zip`.
- **Clip video:** toàn khung camera (thường 1280×720), không cắt, không lật, ghi từ 1 giây trước khi khung chuyển đỏ đến 0,6 giây sau khi tay về lại trước bụng (thường 4–6 giây, 2 Mbps). `meta.json` → `video.frame_times_ms` ghi thời điểm của 30 ảnh tính từ đầu clip; `browser_sign_ms` là đoạn ký hiệu trình duyệt thấy (ms từ lúc khung chuyển đỏ). **`import_web_takes.py` dùng clip này**: tìm đoạn ký hiệu rồi co giãn về 30 khung giống tập ViSL (xem docstring của script); 30 ảnh chỉ dùng khi lượt không có video. Dung lượng ước tính khoảng 0,4–0,5 GB mỗi người cho cả hai buổi. Trình duyệt không ghi được video thì lượt vẫn lưu ảnh như cũ. Cấu hình ở `video` trong [`config.js`](config.js).
- Trên Drive, Apps Script tự chia thư mục theo người quay và buổi quay, nên nhìn số file trong mỗi thư mục là biết ai đã quay đủ chưa (buổi 1: 90 file, buổi 2: 60 file mỗi người). Hai thư mục cho cùng một người với tên khác nhau (ví dụ `tran/` và `baotran/`) nghĩa là người đó đã gõ tên không thống nhất:
  ```
  VSign_uploads/
  ├── baotran/
  │   ├── buoi1/   baotran_s1_….zip
  │   └── buoi2/   baotran_s2_….zip
  ├── minhthong/
  │   └── …
  └── _rejected/   lượt bị loại khi chạy import_web_takes.py
  ```
- Keypoints chính thức được trích trên máy huấn luyện bằng MediaPipe Python, giống hệt tập ViSL. Phần kiểm tra trong trình duyệt chỉ để báo người quay quay lại ngay khi không thấy tay hoặc người.
- Quota và ngưỡng giống `collect_data.py`: buổi 1 quay 3 lượt, buổi 2 quay 2 lượt mỗi người cho mỗi ký hiệu. Lượt bị loại khi thấy tay dưới 50% hoặc thấy người dưới 80% số khung, hoặc khi máy chụp chậm hơn 9 khung/giây.
- **Video mẫu cho từng ký hiệu:** một clip ViSignLanguage-Video (CC BY 4.0) hiện ở góc khung camera, đã lật như gương để người quay làm giống hệt hình. Video tự ẩn khi đang quay. Nên làm theo clip này vì mô hình được huấn luyện trên chính tập ViSL. Với 13 ký hiệu có trong từ điển [QIPEDC](https://qipedc.moet.gov.vn/dictionary) của Bộ GD&ĐT, app hiện thêm link tham khảo. Tạo lại clip: `python webapp/make_references.py` (cần `pip install imageio-ffmpeg`).
- Tiến độ được lấy từ Drive, nên đổi máy hay tải lại trang vẫn tiếp tục được. Lượt chưa tải lên được (mất mạng) được giữ trong trình duyệt và tự gửi lại.

**Không có mã truy cập:** ai có link cũng quay và tải lên được. Apps Script chỉ nhận đúng định dạng một lượt quay (tên file, 30 ảnh JPEG, giới hạn kích thước) và chỉ cho xóa lượt vừa tải lên trong 15 phút, nên không ai xóa được dữ liệu cũ. Lượt rác (nếu có) bị lọc khi chạy `import_web_takes.py`. Chỉ gửi link trong nhóm.

## Cài đặt (nhóm trưởng, làm một lần)

### 1. Thư mục Drive và Apps Script

1. Tạo một thư mục trên Google Drive, ví dụ `VSign_uploads`. Mở thư mục đó và chép **id** của nó, là phần cuối URL sau `/folders/`.
2. Vào <https://script.google.com>, chọn **New project**, rồi dán nội dung [`apps_script/Code.gs`](apps_script/Code.gs).
3. Vào **Project Settings → Script properties → Edit script properties → Add script property**: cột **Property** ghi `FOLDER_ID`, cột **Value** dán id thư mục ở bước 1, rồi bấm **Save script properties**.
4. Chọn **Deploy → New deployment → Web app**, đặt *Execute as* là **Me** và *Who has access* là **Anyone**. Cấp quyền khi Google hỏi: script chỉ ghi vào Drive của bạn.
5. Chép **Web app URL**, có dạng `https://script.google.com/macros/s/…/exec`. Mở URL đó trên trình duyệt: đúng thì thấy `"has_folder_id":true`.

Khi sửa `Code.gs`, chọn **Deploy → Manage deployments → Edit → Version: New version** để giữ nguyên URL. Mở URL `/exec` để xem phiên bản đang chạy (`"version"`). Clip video cần `"version": 6` trở lên; với bản cũ hơn, Apps Script bỏ qua video (ảnh vẫn lưu) và web app báo "Máy chủ chưa lưu được video".

### 2. Cấu hình và đưa web app lên Vercel

1. Dán Web app URL vào `endpoint` trong [`config.js`](config.js).
2. Vào <https://vercel.com/new>, đăng nhập bằng GitHub, chọn repo `VSign` rồi bấm **Import → Deploy**. Giữ nguyên các thiết lập: [`vercel.json`](../vercel.json) ở gốc repo đã chỉ định đúng các file cần đưa lên (`webapp/` và `labels.json`). Repo để private cũng được.
3. Gửi địa chỉ `https://<tên dự án>.vercel.app` cho các thành viên. Từ đó, mỗi lần push lên `main` thì Vercel tự deploy lại.

### 3. Nhận dữ liệu về máy huấn luyện

Cách dễ nhất là cài [Google Drive cho máy tính](https://www.google.com/drive/download/), sau đó chạy:

```bash
python import_web_takes.py --src "<đường dẫn tới thư mục VSign_uploads trên máy>"
python collect_data.py --summary
```

Script đọc mọi thư mục con (`<người>/buoi1`, `<người>/buoi2`), chỉ xử lý các lượt mới và ghi lại mọi lượt trong `data/web_import_log.csv`. Lượt bị loại được chuyển vào `VSign_uploads/_rejected/`. Khi Drive đồng bộ xong, web app sẽ tính các lượt đó là còn thiếu, nên người quay chỉ cần mở lại link và quay bù.

Không cài Drive cho máy tính thì tải thư mục về dạng zip, giải nén, rồi chạy `--src` vào đó. Với cách này, lượt bị loại không tự hiện lại trên web app, nên phải nhắn người quay quay bù.

## Hướng dẫn cho thành viên

App tự hướng dẫn ngay trên màn hình (3 quy tắc có ảnh, video mẫu, kiểm tra khoảng cách). Tóm tắt để gửi kèm link:

**3 quy tắc**
1. **Đủ xa:** thấy từ đầu đến thắt lưng, cả hai tay luôn trong khung. Laptop: đẩy máy ra xa hoặc lùi ghế, khoảng 1 m. Điện thoại: dựng cố định, camera trước, cách 1–1,5 m. App báo **"Khoảng cách: tốt"** mới cho quay; quá gần sẽ báo **"Lùi ra xa"**.
2. **Làm đủ động tác như video mẫu.** Mỗi ký hiệu có video mẫu riêng, hiện ở góc khung hình khi quay. Video đã lật như gương: làm giống hệt hình, cùng phía tay. Bấm vào video để phóng to.
3. **Bắt đầu và kết thúc ở tư thế nghỉ:** hai tay chụm nhẹ trước bụng, tư thế chung của hầu hết video mẫu ở mọi ký hiệu. App chỉ đếm ngược khi thấy hai tay đã về trước bụng; giơ tay sớm trong lúc đếm thì app chờ lại. Khung chuyển đỏ là làm ký hiệu ngay, làm xong đưa tay về trước bụng.

**Cách quay**
- Mở link bằng **Chrome / Edge / Safari** (laptop) hoặc **Chrome (Android) / Safari (iPhone)**. Nhận link qua Zalo/Messenger thì bấm ⋯ → **Mở bằng trình duyệt**.
- Nhập tên (đúng một tên cho cả hai buổi), chọn buổi, cho phép camera.
- Bấm **Bắt đầu quay** (laptop: phím **Space**). Mỗi lượt: chờ hai tay về trước bụng → đếm ngược 2 giây → khung đỏ: làm ký hiệu → đưa tay về trước bụng là lượt kết thúc và tự lưu → sang lượt tiếp, đủ lượt thì sang ký hiệu sau. Độ dài lượt theo người quay (từ app bản 4): làm chậm vẫn được ghi trọn. Lượt bị loại ngay nếu 3,5 giây chưa thấy làm ký hiệu, hoặc quá 6 giây tay chưa về trước bụng. Làm sai thì bấm **Xóa lượt cuối** (phím **D**).
- Không chuyển app hay tab trong lúc quay (lượt đang quay sẽ bị bỏ). Chờ dòng "Mọi lượt đã lưu lên Drive ✓" rồi mới đóng trang.
- **Buổi 2** quay vào ngày khác, đổi trang phục, nền và ánh sáng.

Lượt bị loại (không thấy tay, quá gần/quá xa, máy chụp chậm dưới 9 khung/giây) thì app báo lý do ngay; chỉnh lại rồi quay tiếp. Script import trên máy huấn luyện áp dụng cùng ngưỡng khoảng cách (độ rộng vai 0,22–0,48 cạnh khung; ViSL: 0,29–0,42).

## Thử trên máy

`?endpoint=` thay URL Apps Script, còn `?src=` thay webcam bằng một file video:

```bash
cp labels.json webapp/ && python -m http.server 8000 -d webapp
```

Sau đó mở `http://localhost:8000/?src=/demo.mp4`, với `demo.mp4` đặt trong `webapp/`. Nhớ xóa `webapp/labels.json` sau khi thử, vì bản dùng thật được sao chép tự động lúc deploy.
