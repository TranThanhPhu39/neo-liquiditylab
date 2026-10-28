# Giới hạn hiện tại

- NEO transport/auth/schema/WebSocket chưa xác minh; không có tài khoản/credentials thử thật. Không đạt điều kiện NEO làm lõi hoặc lưu một stream/hai nhóm endpoint ở bản hiện tại.
- Vnstock 5.2.1 đã đăng nhập, đọc quota và quote thật thành công. Đơn vị quote KBS được thẩm định thực nghiệm: VND/CP và CP, hệ số 1/1, với 8 cặp giá/volume khớp giữa ảnh và SDK cùng chú thích đơn vị KBS được lập chỉ mục. Ảnh chỉ có thời điểm xấp xỉ 14:30, mẫu SDK nhận 14:34:48; không chứng nhận đồng bộ, timestamp thị trường hay hợp đồng đơn vị do nhà cung cấp phát hành. Hồ sơ chỉ áp dụng nguồn/version/quote cổ phiếu đã kiểm tra, không mở rộng sang OHLCV hoặc NEO. Xem report/kbs_unit_review_2026-10-28.md.
- Đã lưu 66 snapshot thật trong một phần cửa sổ chiều ngày 28/10; chưa đủ 5–10 phiên, chưa có phiên trọn vẹn đã chứng nhận. Các kiểm tra A1/B5 trên mẫu xác nhận số học và invariant, không xác nhận khớp lệnh thực tế. Các điều khoản/quota có thể đổi và phải được kiểm tra lại khi vận hành.
- Demo là 3 book synthetic cùng một thời điểm cố định; không có nhiều phiên thực và không có chuỗi giá được bịa để thay dữ liệu quan sát.
- Chỉ ba mức giá, không toàn sổ; không queue, replenishment, cancellations, dynamic impact, delay giao dịch, odd-lot/lot-size, tick/ceiling/floor rules, settlement hoặc số dư thực.
- Kết quả không đảm bảo khớp; không ước tính thời gian thanh lý hoặc xác suất khủng hoảng. Không VaR/CVaR/ML.
- Giá snapshot, stale/unsynchronized/source timestamp unknown có thể làm khác thực tế. Timestamp nhận không là timestamp thị trường. Cho phép phân tích dữ liệu cũ là lựa chọn minh bạch.
- Calendar tham khảo dùng ngày thường + holidays nhập tay, không đủ lịch nghỉ và phiên theo từng sàn. Chưa gọi thêm endpoint trạng thái thị trường.
- Rate gate 180 giây là sàn nội bộ, không đủ thay quota ngày/tháng hay điều khoản nhà cung cấp. Không điều phối nhiều máy/database. SDK có thể thực hiện auth/policy requests nội bộ.
- SQLite phù hợp local MVP; dashboard quét lịch sử nguồn hiện tại vào RAM. Cần phân trang/downsampling khi dữ liệu một tháng lớn. Chưa có service tự khởi động, cloud hosting hoặc cơ chế lập lịch OS.
- Raw allowlist không là bản sao toàn bộ payload gốc. Schema nguồn thay đổi sẽ cần đối chiếu adapter. Không nhét trường không hiểu vào engine.
- Portfolio là simulated holdings. Loại mã thiếu dữ liệu khỏi coverage mẫu số và báo riêng; tổng CP giữa mã không tương đương mức rủi ro theo giá trị.
- Đây là công cụ nghiên cứu trước giao dịch, không phải lời khuyên mua/bán.
