# Quyết định nguồn dữ liệu — 03/11/2026

**Tiến độ WS mới:** xác thực/nhận mẫu MSR đạt trên hai runner GitHub mới. Đây là probe realtime ngắn, chưa có collector tự động cả phiên hoặc profile đơn vị WS; kho lịch sử chuẩn hóa vẫn chốt đến 02/11. Xem [WebSocket](dnse_websocket.md).

Theo chỉ đạo của người thực hiện, **DNSE OpenAPI là nguồn dữ liệu chính của sản phẩm**. Không tiếp tục kế hoạch tích hợp NEO API của VPBankS. Vnstock/KBS là nguồn thử nghiệm cũ; Demo là dữ liệu synthetic để phát triển và kiểm thử.

Tên dự án/repo NEO LiquidityLab / neo-liquiditylab hiện được giữ để tránh thay đường dẫn và tài nguyên đã có; tên đó không chứng minh nguồn dữ liệu là NEO. NeoProvider chưa cấu hình vẫn còn trong code, thuộc phần cũ, không phải việc cần hoàn thành của kế hoạch DNSE. Lượt này cập nhật tài liệu, chưa đổi nhãn giao diện hoặc cấu hình chạy mặc định.

**Phạm vi đã hoạt động:** REST market data chỉ đọc, gồm danh mục cổ phiếu, latest quotes và historical quotes; tải lịch sử bid/ask G1 toàn bộ cổ phiếu HOSE/HNX/UPCOM, lưu raw theo ngày, chuẩn hóa, audit, SQLite và Dashboard/Replay offline. A1/B5 mở cho snapshot ACB đủ điều kiện; các mã khác giữ ASSUMED_SHARED_SCHEMA và bị chặn tính tài chính mặc định.

Không cần WebSocket để tải lại diễn biến sổ lệnh mà REST lịch sử hiện đang cung cấp. Streaming là phương án phát triển nếu cần quan sát trực tiếp, chưa được triển khai/kiểm chứng.

**Cần hoàn thiện:** bằng chứng đơn vị/phạm vi, thu nhiều ngày, báo cáo phân tích, chốt ngày, sao lưu và tự chạy trên môi trường truy cập được DNSE. Quyết định đổi nguồn không tự mở khóa các mã chưa xác minh đơn vị.

**Đối chiếu học phần — cập nhật 03/11/2026:** người thực hiện xác nhận việc dùng DNSE đã được thống nhất. Hồ sơ từ đây dùng DNSE làm nguồn chính theo phạm vi đã điều chỉnh; không coi thiếu NEO VPBankS là việc còn phải triển khai. Các tiêu chí dữ liệu, phương pháp, kiểm thử và sản phẩm vẫn cần bằng chứng nghiệm thu tương ứng.

Danh mục /instruments và lịch sử /price/{symbol}/quotes đã dùng cùng nhau để xác định phạm vi mã và lấy dữ liệu thanh khoản. Đây là hai nhóm chức năng REST đang được dùng cho cùng bài toán; báo cáo nghiệm thu cần trình bày endpoint, dữ liệu và vai trò của từng nhóm theo phạm vi DNSE đã thống nhất.

**Lần chốt gần nhất:** bốn ngày giao dịch 28, 29, 30/10 và 02/11/2026, tổng 3.746.982 snapshot duy nhất. Lần hồi quy gần nhất ghi nhận 197 tests đạt; audit và AppTest 02/11 đạt. Xem [báo cáo ngày](../report/dnse_day_completion_2026-11-02.md), [kiểm thử](../report/test_results.md), [đối chiếu đề](assignment_compliance.md).



