# WebSocket DNSE — probe quotes G1 có giới hạn

Đã xác minh ngày 03/11/2026: WebSocket Upgrade, xác thực HMAC, subscribe quotes G1 và nhận dữ liệu MSR thành công trên máy và trên hai GitHub runner mới. Đây là phép thử ngắn cho nguồn realtime; chưa triển khai bộ thu toàn thị trường hoặc bật lịch.

**Chạy trên máy**

Đã lưu DNSE_API_KEY và DNSE_API_SECRET trong môi trường/.env. Probe cần python-dotenv và websockets; đã kiểm tra local websockets 17.3, runner dùng 15.0.1. Không in khóa hoặc dùng SDK example có kênh tài khoản/giao dịch.

```powershell
.\.venv\Scripts\python.exe -m scripts.probe_dnse_ws --symbols ACB PVS MSR --seconds 60
```

CLI chỉ cho 1–3 mã chữ in hoa/số, thời gian quan sát 1–90 giây. Endpoint cố định là wss://ws-openapi.dnse.com.vn/v1/stream?encoding=json, kênh duy nhất top_price.G1.json. Có giới hạn handshake/auth, frame, byte, số quote giữ mỗi mã; TLS/hostname được kiểm tra, không retry vô hạn. Chỉ gửi auth/subscribe/heartbeat của market data. Không có kênh private hoặc phương thức giao dịch.

Nếu chưa cần cài thư viện tùy chọn, dùng môi trường có websockets từ 15 đến trước 18. Không buộc thay phiên bản hiện có đã được kiểm tra. Workflow cài phiên bản riêng trên runner tạm.

**Kết quả và lưu trữ**

Report mặc định report/dnse_ws_probe.json chỉ có trạng thái, số frame/quote, hình dạng mức giá, trường lượng và thời gian, không có payload giá/lượng hoặc auth/session ID. Trạng thái phân biệt FAILED, AUTHENTICATED_NO_QUOTES, QUOTES_RECEIVED_PARTIAL_SCOPE và QUOTES_RECEIVED_ALL_SYMBOLS. Job thành công không có nghĩa mọi mã đều có dữ liệu; đọc samples và status.

Trên máy, mẫu thị trường theo allowlist được ghi trong data/raw/dnse_ws_probe_*.jsonl.gz kèm manifest/checksum riêng. Không lưu auth/control frame. Giữ time nguồn và received_at_utc riêng; protobuf Seconds/Nanos chuyển theo UTC, không dùng timezone máy. Chuỗi thời gian không có timezone giữ trạng thái chưa xác định.

--discard-raw bỏ việc ghi mẫu thị trường; workflow GitHub luôn dùng cờ này và chỉ xuất report tổng hợp. Các probe không nhập normalized.db, không tính thành một ngày lịch sử hoàn chỉnh và không mở A1/B5. Mọi mẫu WebSocket giữ UNVERIFIED_WEBSOCKET; không tự dùng hệ số hay phạm vi ACB đã thẩm định của REST.

**GitHub**

.github/workflows/dnse-ws-preflight.yml chỉ có workflow_dispatch, repo/nhánh cố định, contents: read, một job Ubuntu với timeout năm phút và concurrency để các probe dùng khóa chạy tuần tự. Chỉ bước probe nhận hai DNSE Secrets đã có. Không cron, không upload raw, không thay cờ thu toàn thị trường.

Kết quả hai runner mới: [41599269878](https://github.com/TranThanhPhu39/neo-liquiditylab/actions/runs/41599269878), [41599363526](https://github.com/TranThanhPhu39/neo-liquiditylab/actions/runs/41599363526). Mỗi lượt giữ 10 mẫu MSR, 10 mức bid/offer, ba mức đầu có giá/lượng số hợp lệ, thời gian nguồn nằm trong cửa sổ thử. ACB/PVS không có quote trong hai cửa sổ khoảng 14:53–14:55; không suy thành book 0 hoặc kết luận quyền hai mã không có.

**Trước khi thu tự động toàn thị trường**

Cần thử HOSE/HNX khi có cập nhật, kiểm tra toàn danh mục/quota/subscription, vận hành dài hơn với reconnect và khoảng trống, lưu/checkpoint mã hóa và khôi phục, chốt/audit, cùng bằng chứng đơn vị riêng cho WS. WS trả qtty và 10 mức trong mẫu, khác field quantity của REST; chưa xác nhận scale hoặc full-vs-delta chỉ bằng mẫu này.

Mẫu local có received_at_utc trừ source_time_utc âm khoảng 0,54–0,61 giây. Hai clock chưa được cân chuẩn; không dùng hiệu này làm ước lượng latency thực hoặc xác nhận freshness đủ cho A1/B5. Giữ nguyên bằng chứng để kiểm tra đồng hồ/ý nghĩa timestamp.

WS thu cập nhật khi kết nối; phần backfill đủ lịch sử sau phiên vẫn phụ thuộc REST đang gặp lỗi mạng trên hosted runner. Chưa có bằng chứng WebSocket bù được toàn bộ khoảng bỏ lỡ.

Giao thức được đối chiếu với [auth SDK chính thức](https://github.com/dnse-tech/openapi-sdk/blob/main/python/dnse/websocket/auth.py), [client/subscribe](https://github.com/dnse-tech/openapi-sdk/blob/main/python/dnse/websocket/client.py), [model Quote/PriceLevel](https://github.com/dnse-tech/openapi-sdk/blob/main/python/dnse/websocket/models.py). Chi tiết nghiệm thu nằm ở [báo cáo thử](../report/dnse_ws_validation_2026-11-03.md).
