# Mở rộng dashboard lịch sử toàn thị trường — 16/11/2026

**Hoàn tất online ngày 17/11:** commit `c74306cefd63de1b5a8f504a64d45a04d81b600e`, deployment `dep-dbtu6op42hec73b16psg` live; hosting READY, đủ 1.517 mã ngày 16/11 (1.300 có quan sát, 217 thiếu), 1.523 mã qua 14 ngày/12.863.385 quan sát. Đã tải thật VNM 5.828, SHS 7.560, A32 3 và VIX 78.400 khung; giữ 5 capture SSI và truy cập ghi chú. Kiểm tra cutoff và mã thiếu đạt; dữ liệu/API vẫn 401 khi chưa đăng nhập. Báo cáo chi tiết: `report/market_monitor_hosted_2026-11-16.json`.

**Bản sửa khởi động 17/11: 517/517 kiểm thử đạt.** Catalog 21.245 bộ ngày–mã từng lặp kiểm tra đường dẫn cho cùng 56 tệp và có thể bị nạp trùng khi nhiều request đến trong lúc khôi phục. Đã kiểm tra mỗi đường dẫn duy nhất một lần, dùng khóa nạp catalog, giữ health/status phản hồi nhanh và phục hồi phân tích ba mã trước khi nạp danh mục lớn. Đo trên máy: 9,323 giây → 0,221 giây; không dùng số này như cam kết tốc độ Render. Các lần restart/request đóng kết nối của bản trước đã được xử lý bằng đường nạp mới; không kết luận OOM khi chưa có số liệu bộ nhớ.

## Phạm vi dữ liệu

Đã chuẩn bị và đối chiếu **12.863.385 quan sát chuẩn hóa của 14 ngày**, từ 28/10 đến 16/11; **1.523 mã** xuất hiện trong danh mục qua các ngày. Có **21.245 bộ ngày–mã**, bao gồm các mã không được nguồn trả quan sát. Riêng ngày 16/11: **1.517 mã**, 1.300 có quan sát và 217 thiếu dữ liệu nguồn. Đây là toàn bộ danh mục DNSE đã thu, không phải chứng nhận bao phủ mọi chứng khoán/full depth hoặc không mất tick từ sở.

Giữ tất cả quan sát đã lưu, giá/khối lượng, source/receive clocks, ID, nhãn đơn vị/chất lượng và hash quy tắc. Projection Parquet được ghi/đọc lại và đối chiếu từng trường, số dòng mỗi ngày khớp canonical. Không đổi raw/canonical gốc, không gán thiếu thành 0, không suy giao dịch khớp từ depth. SQLite cũ 19,85 GB vẫn đã xóa và không được tái tạo.

## Chức năng

- Tìm mã, lọc HOSE/HNX/UPCOM, chọn ngày và mở heatmap/sổ lệnh/replay.
- Bảng danh mục và theo dõi 50 mã/trang; snapshot theo dõi không vượt cutoff nguồn. Thống kê số quan sát cả ngày được gắn nhãn hồi tố riêng.
- ACB/FPT/HPG giữ bằng chứng đơn vị, detector và phân tích/ghi chú trước đó. Mã bổ sung chỉ quan sát, detector và cấu hình phân tích giữ khóa.
- Không có sự kiện ở mã chưa đánh giá không được hiển thị như kết luận bình thường. Volume bubbles chỉ có khi đã có tập trades riêng; SSI realtime chưa mở rộng khỏi ba mã.

## Lưu trữ và tự động hóa

14 gói ngày mã hóa có tổng **657.649.810 byte**, bootstrap/catalog mã hóa **210.187.617 byte**; tổng **867.837.427 byte**. Từng gói ngày đã giải mã, kiểm tra checksum và khôi phục cục bộ. Bootstrap chỉ chứa catalog thị trường và các replay ba mã hiện có; Render tải gói thị trường theo ngày được yêu cầu, cache tối đa hai ngày, truyền khung hình theo batch/gzip. Các ngày bất biến được dùng lại, không upload lại toàn bộ lịch sử mỗi ngày.

Người sở hữu đã xác nhận rõ công bố ciphertext lên Releases của `TranThanhPhu39/neo-liquiditylab`, backend Render dùng khóa đã cấu hình, và cập nhật định kỳ toàn danh mục. Dữ liệu web tiếp tục có mật khẩu. Không upload `.env`, API key, raw history DB, database gốc hoặc nâng gói trả phí.

Task Windows 15:15 giữ luồng tải → chuẩn hóa → audit → Parquet → công bố. `market_enabled=true` thêm phạm vi thị trường. Máy vẫn phải bật/đăng nhập/có mạng; GitHub SSI có thể chạy trễ. Publisher không báo hoàn tất chỉ vì có tên mã trên catalog: còn kiểm tra gói ngày trên web trả đủ khung và không tự mở detector.

## Kiểm tra

**515/515 kiểm thử hồi quy đạt.** Browser Edge thật với CloudServer trên localhost đã chọn ACB/FPT/HPG/VNM/SHS/A32, mở trang theo dõi, tìm mã thực sự thiếu dữ liệu, desktop/mobile 390 px; không JavaScript exception hay tràn ngang toàn trang. Đã kiểm tra retry GET 503 và thứ tự tải dataset trước trades.

Đã kiểm chứng feed Releases chỉ trả mười mục và bị các gói thị trường chiếm chỗ. Khi đổi seed, publisher cập nhật tham chiếu các checkpoint SSI đã có vào `SSI_RECOVERY_TAGS_JSON`; backend gộp chúng với feed thay vì làm mất lịch sử SSI sau cold start. Không đưa token GitHub hoặc khóa broker lên Render cho việc khám phá này.

Đã mở **VIX ngày 06/11 với đủ 78.400 khung** trên browser, không lấy mẫu. Thử khôi phục bootstrap mỏng rồi tải các gói ngày độc lập: AAA/HOSE 1.511 quan sát, ADC/HNX 10, A32/UPCOM 3 và VIX 78.400; mọi detector của phạm vi quan sát vẫn bị chặn.

Bằng chứng cục bộ: `report/market_monitor_preparation_latest.json`, `report/online_monitor_preparation_auto_9686d585f3fd38bf.json`, `report/dashboard_market_browser_qa_2026-11-16.json`. Kết quả nghiệm thu bản online sau triển khai được ghi riêng tại `report/market_monitor_hosted_2026-11-16.json`; chỉ báo online hoàn tất khi kiểm tra bản thực sự đang live.

## Giới hạn

Giới hạn bảo vệ: 100.000 khung/mã/ngày cho chế độ quan sát, catalog 50.000 bộ ngày–mã/64 MiB, bootstrap 768 MiB; vượt ngưỡng báo lỗi thay vì lấy mẫu/xóa lịch sử. Khi kho tiếp tục lớn cần phân vùng thêm. Bản Free có cold start; PostgreSQL ghi chú hiện hết hạn 15/12/2026. Xem [runbook](../docs/market_monitor.md).
