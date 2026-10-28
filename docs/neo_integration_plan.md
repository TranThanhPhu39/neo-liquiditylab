# Kế hoạch tích hợp NEO API

Trạng thái: **NOT CONFIGURED — chưa kiểm chứng kết nối thật**. `NeoProvider.get_snapshots` luôn báo NotConfigured, ngay cả khi biến token tồn tại: token không thay thế việc xác minh giao thức. `.env.example` dự phòng NEO_BASE_URL/NEO_WEBSOCKET_URL/NEO_TOKEN; không URL network hay payload tự đoán.

Ngày 27/10/2026 truy cập các trang chính thức: [Overview](https://neoinvest.vpbanks.com.vn/neo-openapi/docs/overview), [Stock detail](https://neoinvest.vpbanks.com.vn/neo-openapi/docs/market/stock-detail), [Market stream](https://neoinvest.vpbanks.com.vn/neo-openapi/docs/streaming/market-stream). Nội dung đọc được chỉ là shell điều hướng và Loading; chưa có schema đầy đủ. Không lấy URL frontend làm endpoint API.

## Công việc khi được duyệt

1. Đối chiếu docs đầy đủ từ VPBankS: base URL, endpoint market data, token lifecycle, header, quota, timezone, đơn vị giá/volume, ý nghĩa trường, event ID và snapshot/full-vs-delta.
2. Lưu payload market-data mẫu đã khử thông tin nhạy cảm, phiên bản tài liệu và bằng chứng mapping. Ánh xạ symbol/exchange, Bid/Ask 1–3, ref/last, source event time sang OrderBookSnapshot; không dùng tên trường tự đoán. Đăng ký mapper đã xác minh trước khi bật network.
3. Chỉ triển khai REST thị trường và WebSocket stockInfo sau xác minh URL/subscribe/auth. Không implement endpoint đặt/sửa/hủy lệnh. Không yêu cầu số dư thực vì danh mục nhập tay.
4. Auth token qua env/secret cục bộ, refresh theo docs, redact header/logs và không in response auth.
5. Rate limiter dùng token bucket <=5 REST request/giây/client theo yêu cầu đề, xác minh hạn mức chính thức và sử dụng mức chặt hơn. Đồng bộ quota giữa worker nếu cần. 5/s hiện là yêu cầu thiết kế của đề, chưa tuyên bố đã đối chiếu chính thức.
6. Stream: heartbeat theo docs, mất kết nối ⇒ DISCONNECTED; backoff có jitter và giới hạn, reconnect/resubscribe idempotent. Chỉ khôi phục trạng thái đủ sau snapshot/sequence được tài liệu xác nhận; tránh áp delta trên book chưa có base.
7. Lưu received_at UTC và event time có timezone. Dedup bằng ID/sequence nguồn đã xác minh. Giữ late event, cập nhật view hiện tại theo event ordering; replay kiểm soát arrival để tránh look-ahead.
8. Dùng storage, finance, portfolio, replay và UI sẵn có; source label neo chỉ cho dữ liệu NEO thật. Thêm NEO modes sau contract tests + integration tests thực.

## Bằng chứng nghiệm thu còn thiếu

Một stream lưu trữ đủ dữ liệu **hoặc** hai nhóm endpoint trực tiếp phục vụ bài toán, >=3 trường tài chính có nguồn thật, raw/normalized có provenance, 5–10 phiên nếu khả thi, báo cáo quota và mất kết nối. Test timeout/auth expired/revoked, rate error, reconnect/duplicate/delta/late messages, timestamp/unit, partial book/ATO/ATC, snapshot so bảng giá thực và engine case chuẩn. Không giả định có order book lịch sử trước ngày bắt đầu thu.
