# Vận hành thu dữ liệu thật

## Trạng thái xác minh ngày 28/10/2026

Cập nhật sau 14:40: đã có [bằng chứng thực nghiệm đơn vị](../report/kbs_unit_review_2026-10-28.md) và hồ sơ version 5.2.1, hệ số SDK sang VND/CP và CP là 1/1. Lượt thu đã dừng đúng 14:30 với 22 batch/66 quan sát; 66 snapshot đã được chuẩn hóa, raw gốc không sửa. Chọn vnstock → Replay lịch sử để chạy A1/B5 trên dữ liệu đã lưu. Những đoạn nói chưa xác minh bên dưới ghi trạng thái trước khi nhận ảnh đối chiếu, được giữ để truy vết. Chưa tự bật collector mới hoặc sửa `.env`; timestamp nguồn và NEO vẫn chưa xác minh.

Vnstock 5.2.1 đã đăng nhập thành công cấp `free`, quote FPT/HPG/ACB trả đủ 16 trường thị trường cần thiết. Khóa ở `.env`, nhận `VNSTOCK_API_KEY` hoặc `vnstock_api`; không ghi khóa vào báo cáo. Quota được đọc từ SDK trước mỗi lượt raw; không dùng số quota hard-code làm cam kết quyền.

Đơn vị giá quote: tài liệu [Market](https://vnstocks.com/docs/vnstock/thi-truong-market) ghi đồng. [Bảng giá KBS](https://kbbuddywts.kbsec.com.vn/) công bố **giá hiển thị ×1.000, khối lượng hiển thị ×10**. Hệ số hiển thị web **không tự động là hệ số dữ liệu SDK**. Mã nguồn [mapping KBS của tác giả Vnstock](https://github.com/thinh-vu/vnstock/blob/main/vnstock/explorer/kbs/const.py) có V1..3/U1..3 ánh xạ bid_vol/ask_vol nhưng không đủ chứng minh đơn vị của bản native 5.2.1. Trang KBS trả HTTP 400 trong lượt truy cập trực tiếp; không tìm cách né hạn chế truy cập.

Chưa đối chiếu được cùng thời điểm với bảng giá có đơn vị rõ. Do đó giữ `VNSTOCK_VOLUME_UNIT` chưa xác nhận và không bật `VNSTOCK_ACCESS_CONFIRMED=true` cho collector chuẩn hóa. Không nhân/chia volume dựa vào phỏng đoán. Timestamp nguồn vẫn null; nhận REST không chứng minh thời điểm quote mới nhất.

## Thu raw ngay trong cửa sổ giao dịch

Chế độ raw giữ dữ liệu gốc và provenance, không tạo OrderBookSnapshot để đưa vào A1/B5. Bảng `raw_quote_batches` liên kết `collection_runs`; payload theo allowlist trường thị trường, không lưu account/token. Mỗi batch 3 mã, `units_status=UNVERIFIED`, `source_timestamp=null`, `capture_method=rest_poll_raw`.

```powershell
# Thay ngày/giờ until bằng thời điểm tương lai có timezone; tối đa 8 giờ/lượt.
.\.venv\Scripts\python.exe -m scripts.collect_raw_market --until '2026-10-28T14:30:00+07:00' --interval 180 --report report/raw_collection_2026-10-28.json
```

Chỉ một collector dùng database mặc định `data/liquidity.db`; không mở database khác để né rate gate. Mỗi lượt cách ít nhất 180 giây, không retry auth/quota/source error; lỗi dừng và tạo backup. Chỉ lấy mẫu trong cửa sổ tham khảo 09:15–11:30 và 13:00–14:30 ngày thường, không xác nhận lịch nghỉ/phiên sàn thực. Giới hạn thời gian tuyệt đối; Ctrl+C dừng an toàn. Báo cáo checkpoint sau mỗi lượt; cuối lượt tự sao lưu SQLite bằng backup API (bao gồm WAL), kiểm tra integrity, ghi đường dẫn backup.

Lượt hiện tại bắt đầu 13:23 ngày 28/10/2026, dự kiến dừng 14:30 cùng ngày. Đây là **phần còn lại của một cửa sổ chiều**, không phải một phiên trọn vẹn, chưa đạt 5–10 phiên. Tiến trình chỉ hoạt động khi máy và phiên Python còn chạy; không tự khởi động qua ngày mới hoặc sau reboot.

Sự cố 13:29: bộ lập nhịp cũ chạm chốt rate gate nội bộ trước khi đủ 180 giây, chưa gửi request thứ ba. Đã sao lưu và dừng. Bản sửa dùng reservation trong SQLite để trả WAITING_LOCAL_RATE_LIMIT và chờ thêm, không retry request nguồn. Đã khởi động lại 13:31; khoảng gián đoạn 302 giây được giữ trong gap_candidates. `failed_runs` đếm các run request đã được cấp reservation, không đếm lần scheduler bị chặn trước reservation; xem nhật ký sự cố `report/collection_incidents_2026-10-28.json`.

## Audit, xuất và sao lưu trong lúc đang thu

```powershell
.\.venv\Scripts\python.exe -m scripts.audit_raw_collection --output report/raw_audit_2026-10-28.json
```

Lệnh đọc SQLite, xuất CSV raw vào `data/processed`, replay theo thời điểm nhận để kiểm tra không look-ahead, tạo backup có timestamp duy nhất trong `data/backups` và báo cáo JSON. Không ghi đè backup có sẵn. Báo cáo phân biệt số batch, số quan sát theo mã, ngày thu thập và **0 phiên có timestamp nguồn đã xác minh**. Tỷ lệ unchanged/gap mô tả raw, không là xác suất tài chính. `normalized_snapshot_count=0` là trạng thái đúng khi chỉ có raw.

Dashboard: chọn nguồn `vnstock` để xem bảng raw, trạng thái và tải CSV. Trong **Replay lịch sử** có bộ chọn thời điểm nhận raw khi có >=2 batch. Các màn hình A1/B5 vẫn hiển thị cảnh báo, không sinh chỉ số tài chính từ raw chưa xác minh. Demo giữ riêng, không thay dữ liệu raw thật.

## Hoàn tất đơn vị rồi mới phân tích

Các câu hỏi kỹ thuật cần nguồn xác nhận đã soạn tại [provider_questions.md](provider_questions.md); chưa gửi cho bên nào. Báo cáo dữ liệu thật đọc được bằng Markdown/HTML qua `python -m scripts.report_collection`. Chỉ có thống kê số quan sát và spread, không dùng volume chưa xác minh để tính chi phí.

1. Lấy bảng giá chính thức có hiển thị đơn vị và timestamp cùng thời điểm với SDK; lưu ít nhất một ví dụ cho mỗi mã và các mức Bid/Ask.
2. Xác định SDK trả CP hay đơn vị rút gọn, đối chiếu hệ số ở nhiều trường; nếu cần đổi đơn vị, thêm mapping tường minh và unit tests. Không đổi hệ số chỉ để khớp một con số.
3. Ghi bằng chứng và thời điểm xác minh vào source_verification.md. Khi đó mới cấu hình cổng normalized, chuyển raw sang schema qua một migration có provenance và chống trùng; không sửa dữ liệu raw gốc.
4. Chạy A1/B5, portfolio và replay normalized, kiểm tra phí/thuế, partial depth và dữ liệu cũ. Nếu timestamp nguồn chưa có, giữ cảnh báo đồng bộ theo arrival là proxy.
5. Thu 5–10 phiên thật trở lên theo quota, audit/sao lưu mỗi ngày. Chưa tích hợp NEO nên chưa đạt tiêu chí cuối kỳ về nguồn NEO cốt lõi.
