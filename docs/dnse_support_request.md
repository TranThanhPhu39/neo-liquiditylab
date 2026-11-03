# Nội dung để người dùng hỏi hỗ trợ DNSE — cập nhật 03/11/2026

**Bản soạn sẵn, chưa gửi. Không đính kèm API Key, API Secret hoặc tệp .env.**

Chào DNSE, tôi dùng LightSpeed API để đọc dữ liệu thị trường phục vụ đồ án phân tích thanh khoản. Tôi muốn tải lịch sử bid/ask sau phiên bằng GitHub Actions standard hosted runner.

Ngày 03/11/2026, tôi kiểm tra lại và nhận các kết quả sau (giờ Việt Nam):

- Hai runner Ubuntu mới lúc 14:05–14:07 đều phân giải openapi.dnse.com.vn thành 103.151.242.24 nhưng kết nối TCP cổng 443 timeout khoảng 5 giây, chưa tới TLS/xác thực. Request latest FPT sau đó timeout 30 giây, không có HTTP status hoặc quote. [Lượt 1](https://github.com/TranThanhPhu39/neo-liquiditylab/actions/runs/41594003471), [lượt 2](https://github.com/TranThanhPhu39/neo-liquiditylab/actions/runs/41594126728).
- Thử thêm mạng công khai, không dùng API key, trên ba runner Ubuntu 24.04, Windows 2022, macOS 14 lúc 14:10 đều bị TCP timeout tới cùng địa chỉ/cổng. [Kết quả ba hệ điều hành](https://github.com/TranThanhPhu39/neo-liquiditylab/actions/runs/41594518156).
- Đối chứng trên máy cá nhân lúc 14:08:43 lấy latest FPT thành công, một quote có đủ ba mức bid và offer, thời gian nguồn 14:08:43.249. Bộ tải trên máy cũng đã thu lịch sử bốn ngày 28, 29, 30/10 và 02/11. Không tắt TLS hoặc thay khóa trong lượt kiểm tra này.

Bổ sung đối chứng cùng runner lúc 14:33–14:34: trên cả Ubuntu/Windows/macOS, TCP tới REST openapi.dnse.com.vn (103.151.242.24:443) timeout khoảng 5 giây; ngay sau đó TCP/TLS 1.3 tới WebSocket chính thức ws-openapi.dnse.com.vn (103.151.242.88:443) thành công khoảng 0,428–0,735 giây. Không dùng khóa trong phép đo này. [Run đối chứng hai endpoint](https://github.com/TranThanhPhu39/neo-liquiditylab/actions/runs/41597129798). Chưa thử xác thực hoặc nhận dữ liệu WebSocket.

Máy cá nhân cũng kết nối trực tiếp TCP/TLS tới REST thành công lúc 14:23, không cấu hình proxy HTTP/HTTPS trong môi trường kiểm tra. Nhờ DNSE đối chiếu đường truy cập/chính sách giữa hai endpoint; chúng tôi chưa có căn cứ kết luận chặn toàn bộ GitHub.

Bổ sung bước xác thực lúc 14:53–14:55: hai runner GitHub mới đã WebSocket Upgrade/auth/subscribe G1 và nhận mẫu MSR thành công. REST lịch sử vẫn chưa truy cập được theo các phép đo trước. [Lượt WS 1](https://github.com/TranThanhPhu39/neo-liquiditylab/actions/runs/41599269878), [lượt WS 2](https://github.com/TranThanhPhu39/neo-liquiditylab/actions/runs/41599363526). Điều này giúp khoanh phạm vi kiểm tra đường vào REST, không phải kết luận chặn toàn bộ GitHub.

Nhờ DNSE kiểm tra giúp:

1. REST OpenAPI có hỗ trợ truy cập từ GitHub-hosted runner/cloud không? Có giới hạn IP/vùng hoặc yêu cầu whitelist? Đường truy cập chính thức được hỗ trợ cho nhu cầu này là gì?
2. Với thời gian và log trên, DNSE có thể kiểm tra kết nối tới openapi.dnse.com.vn:443 và hướng dẫn quy trình cấu hình mạng phù hợp không? Tôi chưa có căn cứ kết luận phía nào chặn kết nối.
3. Đơn vị bid/offer.price và quantity của historical/latest quotes G1 là gì, có áp dụng đồng nhất cho cổ phiếu HOSE/HNX/UPCOM trên phiên bản API 2026-07-23 không? Xin tài liệu hoặc xác nhận phạm vi để lập hồ sơ quy đổi.
4. Để tải lịch sử sau 11:30 hoặc sau đóng cửa, dữ liệu lưu bao lâu, limit tối đa, điều kiện quyền/phí và hạn mức áp dụng cho tài khoản là gì?

Cảm ơn DNSE.


