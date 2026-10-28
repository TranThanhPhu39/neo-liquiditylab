# Đánh giá DNSE làm nguồn thay thế trong lúc chờ NEO

Ngày xem xét: 28/10/2026. Người dùng xác nhận đã có tài khoản DNSE và bật API. Hiện mới xác nhận có DNSE_API_KEY cục bộ; API Secret và quyền dữ liệu thực sẽ được kiểm tra riêng. Không coi việc bật Trading API là bằng chứng đã đủ quyền Market Data.

## Những phần đã có tài liệu chính thức

- [Latest quotes](https://developers.dnse.com.vn/docs/dnse/get-latest-quotes/): GET `/price/:symbol/quotes/latest`.
- [Historical quotes](https://developers.dnse.com.vn/docs/dnse/get-quotes/): GET `/price/:symbol/quotes`, truy vấn bid/ask theo thời gian và bảng giao dịch.
- [Danh mục mã](https://developers.dnse.com.vn/docs/dnse/get-instruments/): GET `/market/instruments`.
- SDK [dnse-tech/openapi-sdk](https://github.com/dnse-tech/openapi-sdk/tree/main/python) có quote REST và WebSocket; model Quote dùng `bid`/`offer` là danh sách mức giá/khối lượng. Cần đọc payload thật để xác nhận đủ ít nhất ba mức, đơn vị và ý nghĩa timestamp.
- SDK REST chính thức dùng API key + API secret ký HMAC-SHA256, Date và nonce; base URL `https://openapi.dnse.com.vn`, API version được xem trong source là `2026-07-23`. Không suy đoán auth từ hướng dẫn cũ trước KRX.

## Chưa kết luận từ tài liệu

Chưa xác nhận phí/quota trên tài khoản người dùng, phạm vi HOSE/HNX/UPCOM, giới hạn subscribe, giới hạn IP/thiết bị, quyền truy vấn lịch sử, độ dài lưu trữ hoặc tính ổn định trên hai runner GitHub mới. Không tuyên bố DNSE miễn phí toàn thị trường hoặc chạy CI được chỉ từ việc có key.

Historical quotes có thể cho phép một cách vận hành khác: tải diễn biến bid/ask phiên sáng sau 11:30, thay vì phải tự lưu mọi snapshot trong phiên. Đây mới là khả năng cần kiểm chứng từ quyền truy cập, phân trang và thời hạn dữ liệu; OHLC không thay được bid/ask cho A1/B5.

## Probe chỉ đọc đã chuẩn bị

### Khi không thấy API Secret

Theo [FAQ chính thức DNSE](https://developers.dnse.com.vn/docs/guide/faq/), Secret chỉ hiện một lần khi tạo khóa và không thể xem lại sau khi đóng màn hình thành công. Người dùng tự vào [Entrade X](https://entradex.dnse.com.vn/) → Họ tên → Thông tin tài khoản → LightSpeed API. Nếu không còn bản lưu Secret, chọn **Tạo lại API Key**, rồi lưu ngay cả API Key mới và API Secret mới vào `.env`. Bộ khóa cũ hết hiệu lực ngay, nên ứng dụng khác đang dùng bộ cũ cũng phải cập nhật. Không chọn Hủy API Key vì thao tác đó hủy dịch vụ OpenAPI. [Hướng dẫn đăng ký và quản lý](https://developers.dnse.com.vn/docs/guide/intro/register_guide/).

Chưa gọi API DNSE thực khi thiếu Secret. Việc tạo lại khóa do người dùng thực hiện trong tài khoản; không tự đổi khóa hoặc dùng mật khẩu đăng nhập thay Secret.

```powershell
# Người dùng tự lưu trực tiếp trong .env, không đưa giá trị vào chat:
# DNSE_API_KEY=...
# DNSE_API_SECRET=...
.\.venv\Scripts\python.exe -m scripts.probe_dnse
```

Probe tối đa ba mã FPT/HPG/ACB, nghỉ ít nhất hai giây giữa request, chỉ GET latest quotes, timeout 30 giây và giữ kiểm tra TLS/hostname. Không có route tài khoản hoặc đặt/sửa/hủy lệnh trong client. Không chạy nguyên SDK example có in headers/debug hoặc tắt hostname checking; chỉ tái hiện giao thức ký đã đối chiếu từ source chính thức.

Kết quả được lưu raw riêng tại `data/raw/dnse_probe_*` (gitignored), báo cáo `report/dnse_preflight.json`; lọc trường/token nhạy cảm và không lưu request headers. Mọi payload vẫn UNVERIFIED, không nhập vào orderbook Vnstock hoặc đưa vào A1/B5. `time` có trong payload không tự chứng minh timezone và độ mới của quote.

Sau probe đạt, cần xác minh units và schema bằng bảng giá cùng thời điểm, thử lịch sử một khoảng nhỏ, rồi mới viết mapping chuẩn hóa/collector DNSE. Khi được người dùng cho phép lưu DNSE credentials vào GitHub Secrets, thử nguồn trên hai runner mới và đo quota trước khi mở rộng danh mục. Khóa DNSE chưa được upload lên GitHub chỉ bởi việc đã cho phép hai secrets Vnstock/state-key trước đó.

## Liên hệ với đồ án

DNSE có thể là nguồn thay thế hợp lệ trong giai đoạn phát triển. Tiêu chí cuối kỳ vẫn yêu cầu NEO API là thành phần cốt lõi; không đổi nhãn DNSE thành NEO hoặc kết luận đã hoàn tất yêu cầu NEO.
