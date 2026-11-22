# Web app thu thập dữ liệu

Thành viên ở xa mở một đường link, quay bằng webcam của mình, và mỗi lượt đạt yêu cầu được tải thẳng lên một thư mục Google Drive chung. Không cần cài Python và không cần đăng nhập Google.

```
Trình duyệt thành viên ──(Apps Script, mã nhóm)──► thư mục Drive ──(đồng bộ)──► máy huấn luyện
  quay 30 khung vuông, 12 khung/giây                một zip mỗi lượt                import_web_takes.py
  kiểm tra tay/người bằng MediaPipe JS                                                → data/raw, data/manifest.csv
```

- Mỗi lượt là một file zip gồm 30 ảnh JPEG 480×480 (chưa lật gương) và `meta.json`, khoảng 0,2–1 MB. Tên file có dạng `<người>_s<buổi>_<id>_<slug>_<thời gian>.zip`.
- Keypoints chính thức được trích trên máy huấn luyện bằng MediaPipe Python, giống hệt tập ViSL. Phần kiểm tra trong trình duyệt chỉ để báo người quay quay lại ngay khi không thấy tay hoặc người.
- Quota và ngưỡng giống `collect_data.py`: buổi 1 quay 3 lượt, buổi 2 quay 2 lượt mỗi người cho mỗi ký hiệu. Lượt bị loại khi thấy tay dưới 50% hoặc thấy người dưới 80% số khung, hoặc khi máy chụp chậm hơn 9 khung/giây.
- Tiến độ được lấy từ Drive, nên đổi máy hay tải lại trang vẫn tiếp tục được. Lượt chưa tải lên được (mất mạng) được giữ trong trình duyệt và tự gửi lại.

## Cài đặt (nhóm trưởng, làm một lần)

### 1. Thư mục Drive và Apps Script

1. Tạo một thư mục trên Google Drive, ví dụ `VSign_uploads`. Mở thư mục đó và chép **id** của nó, là phần cuối URL sau `/folders/`.
2. Vào <https://script.google.com>, chọn **New project**, rồi dán nội dung [`apps_script/Code.gs`](apps_script/Code.gs).
3. Vào **Project Settings → Script properties** và thêm hai thuộc tính:
   - `FOLDER_ID`: id thư mục ở bước 1.
   - `TOKEN`: một mã nhóm khó đoán, ví dụ 16 ký tự ngẫu nhiên. Gửi riêng mã này cho các thành viên và **không đưa lên GitHub**.
4. Chọn **Deploy → New deployment → Web app**, đặt *Execute as* là **Me** và *Who has access* là **Anyone**. Cấp quyền khi Google hỏi: script chỉ ghi vào Drive của bạn.
5. Chép **Web app URL**, có dạng `https://script.google.com/macros/s/…/exec`.

Khi sửa `Code.gs`, chọn **Deploy → Manage deployments → Edit → Version: New version** để giữ nguyên URL.

### 2. Cấu hình và đưa web app lên Vercel

1. Dán Web app URL vào `endpoint` trong [`config.js`](config.js). URL này công khai cũng không sao, vì mọi yêu cầu đều phải kèm mã nhóm.
2. Vào <https://vercel.com/new>, đăng nhập bằng GitHub, chọn repo `VSign` rồi bấm **Import → Deploy**. Giữ nguyên các thiết lập: [`vercel.json`](../vercel.json) ở gốc repo đã chỉ định đúng các file cần đưa lên (`webapp/` và `labels.json`). Repo để private cũng được.
3. Gửi địa chỉ `https://<tên dự án>.vercel.app` cho các thành viên. Từ đó, mỗi lần push lên `main` thì Vercel tự deploy lại.

### 3. Nhận dữ liệu về máy huấn luyện

Cách dễ nhất là cài [Google Drive cho máy tính](https://www.google.com/drive/download/), sau đó chạy:

```bash
python import_web_takes.py --src "<đường dẫn tới thư mục VSign_uploads trên máy>"
python collect_data.py --summary
```

Script chỉ xử lý các lượt mới và ghi lại mọi lượt trong `data/web_import_log.csv`. Lượt bị loại được chuyển vào `VSign_uploads/_rejected/`. Khi Drive đồng bộ xong, web app sẽ tính các lượt đó là còn thiếu, nên người quay chỉ cần mở lại link và quay bù.

Không cài Drive cho máy tính thì tải thư mục về dạng zip, giải nén, rồi chạy `--src` vào đó. Với cách này, lượt bị loại không tự hiện lại trên web app, nên phải nhắn người quay quay bù.

## Hướng dẫn cho thành viên

Quay bằng camera có sẵn của máy: camera laptop, hoặc camera trước của điện thoại.

1. Mở link bằng **Chrome / Edge / Safari** trên laptop, hoặc **Chrome (Android) / Safari (iPhone)** trên điện thoại.
   - Nếu nhận link qua Zalo/Messenger: bấm ⋯ → **Mở bằng trình duyệt**. Trình duyệt bên trong Zalo/Messenger thường không cho dùng camera.
2. Nhập tên (dùng đúng một tên cho cả hai buổi), chọn buổi quay, nhập mã nhóm, rồi cho phép dùng camera.
3. Điện thoại: dựng máy cố định (giá đỡ hoặc tựa vào vật), dùng camera trước, không cầm tay. Đặt máy cách 1–1,5 m để thấy rõ mặt, hai vai và hai tay trong khung vuông. Đủ sáng, chỉ một người trong khung.
4. Bấm **Bắt đầu quay** (laptop: phím **Space**). App tự đếm ngược, quay đủ số lượt rồi chuyển sang ký hiệu tiếp theo. Làm sai thì bấm **Xóa lượt cuối** (phím **D**). Bấm ◀ / ▶ hoặc chọn trong danh sách để đổi ký hiệu.
5. Không chuyển sang app hay tab khác trong lúc quay: lượt đang quay sẽ bị bỏ. Chờ dòng "Mọi lượt đã lưu lên Drive ✓" rồi mới đóng trang.
6. Buổi 2 quay vào ngày khác, đổi trang phục, nền và ánh sáng.

Nếu báo lỗi camera, app sẽ hiện hướng dẫn cụ thể (cấp quyền, đóng ứng dụng đang dùng camera, mở bằng trình duyệt khác). Máy quá chậm (dưới 9 khung/giây) thì lượt bị loại; đóng bớt ứng dụng hoặc dùng máy khác.

## Thử trên máy

`?endpoint=` thay URL Apps Script, còn `?src=` thay webcam bằng một file video:

```bash
cp labels.json webapp/ && python -m http.server 8000 -d webapp
```

Sau đó mở `http://localhost:8000/?src=/demo.mp4`, với `demo.mp4` đặt trong `webapp/`. Nhớ xóa `webapp/labels.json` sau khi thử, vì bản dùng thật được sao chép tự động lúc deploy.
