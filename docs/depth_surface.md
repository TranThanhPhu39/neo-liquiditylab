# Biểu đồ độ sâu 3D

Mở màn hình **Thanh khoản → Độ sâu 3D theo thời gian** trên web hoặc Streamlit. Chọn mã, ngày và giờ nguồn ở bộ lọc chung.

- **X:** giá thực tại ba mức quan sát, VND/CP.
- **Y:** thời gian nguồn, giờ Việt Nam.
- **Z:** khối lượng CP của từng mức, hoặc khối lượng tích lũy khi chuyển lựa chọn.
- **Bên sổ lệnh:** Bid xanh lam, Ask xanh ngọc, hoặc hai bên cùng lúc.
- Kéo để xoay, cuộn để phóng to; thanh công cụ Plotly có đặt lại góc nhìn và lưu ảnh.

Màu mặt chuyển từ xanh nhạt sang xanh đậm theo khối lượng, kèm các đường tại những lát cắt được lấy mẫu, gần phong cách hình tham khảo người dùng gửi. Đây là mặt lưới nối điểm quan sát để xem hình học; không phải dữ liệu đầy đủ ở mọi mức giá hoặc mọi thời điểm giữa các điểm.

Mỗi bên chỉ có ba mức. Dữ liệu thiếu giữ null, số 0 thực được giữ 0; giá đấu giá/không xác định không được vẽ thành giá 0. Tích lũy sau một mức chưa biết cũng giữ chưa biết. Khoảng nguồn cách nhau trên 15 phút được ngắt, gồm khoảng nghỉ trưa; không vẽ một mặt giả xuyên qua khoảng này. Một snapshot chỉ được vẽ thành lát cắt, không tạo lịch sử giả.

Biểu đồ dùng tối đa 180 snapshot trong ngày/mã, không muộn hơn mốc giờ đang chọn. Giữ điểm đầu/cuối, không lấy dữ liệu tương lai. Web vẫn dùng lịch sử public lấy mẫu 6 phút; Streamlit đọc các điểm đại diện từ kho trên máy. Nhãn chưa xác minh đơn vị vẫn được giữ và không mở khóa A1/B5.

Mô hình hình học và figure schema nằm trong `src/app/depth_surface.py`, dùng chung cho browser bridge và Streamlit. Chỉ chuyển sang số vẽ biểu đồ ở đây; lõi tính toán tài chính vẫn dùng Decimal.

Plotly gl3d 3.7.0 được ghim phiên bản/checksum trong `web/vendor/plotly-manifest.json`, phục vụ từ chính Pages. Trình duyệt không gửi dữ liệu thị trường đến CDN Plotly khi dùng biểu đồ.

Xuất bản HTML tương tác độc lập, không cần bộ tính Python trên web:

```powershell
.\.venv\Scripts\python.exe -m scripts.export_depth_surface --date 2026-11-05 --symbol ACB
```

Đầu ra `data/exports/charts/depth3d_ACB_2026-11-05.html`; có thể thêm `--side ask`, `--side both`, `--cumulative` hoặc `--at 14:20`. Bản thử ACB cả ngày 05/11 trên máy có 107 snapshot đại diện và 1 khoảng ngắt qua nghỉ trưa. HTML này lưu trên máy, không tự thêm điểm dữ liệu mới vào bộ public.
