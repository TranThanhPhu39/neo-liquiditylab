# Nội dung cần xác nhận với nguồn Vnstock

Nút thắt còn lại là bằng chứng về đơn vị, không phải khóa API hoặc lỗi kết nối. Không cần gửi khóa API, mã tài khoản hay dữ liệu cá nhân khi hỏi nguồn.

Nội dung kỹ thuật cần xác nhận:

> Với vnstock 5.2.1, lời gọi `Market().quote(["FPT", "HPG", "ACB"], source="kbs")` trả các cột `bid_vol_1..3` và `ask_vol_1..3` theo số cổ phiếu thực hay theo đơn vị rút gọn? SDK có áp dụng hệ số quy đổi từ upstream KBS không? Xin đường dẫn tài liệu hoặc xác nhận có ghi phiên bản để đối chiếu.

Cần thêm câu trả lời về thời điểm nguồn nếu có:

> Response quote hiện không có timestamp thị trường. Có trường hoặc API đọc được phép dùng để xác định thời điểm quote được cập nhật không? Nếu không có, ứng dụng sẽ giữ source_timestamp=null và chỉ ghi thời điểm nhận REST.

Tài liệu hiện đã kiểm tra qua SDK chính thức:

- `vnstock/thi-truong-market`: tên cột và giá tính bằng đồng; chưa thấy đơn vị volume được ghi tường minh ở phần quote.
- `vnstock/vnstock-data-v3/cau-truc-du-lieu/market`: mô tả volume đặt mua/bán và mẫu số; chưa đủ bằng chứng cho đơn vị của native SDK 5.2.1.
- Bảng giá KBS trực tiếp trả HTTP 400 trong môi trường hiện tại; hệ số hiển thị web không được tự coi là hệ số SDK.

Khi có bằng chứng, lưu bản cục bộ vào thư mục tài liệu bằng chứng, ghi checksum và thẩm định trong hồ sơ đơn vị thật. Không đổi hồ sơ mẫu UNVERIFIED thành VERIFIED khi chưa có nội dung xác nhận.

Đây là bản nháp câu hỏi; chưa gửi tin nhắn hoặc email cho bên nào.
