# Đánh giá DNSE làm nguồn thay thế trong lúc chờ NEO

Ngày xem xét: 28/10/2026. Người dùng đã lưu đủ bộ khóa mới cục bộ. Latest quotes và một khoảng lịch sử nhỏ đã trả dữ liệu thật cho FPT/HPG/ACB; chưa kết luận quyền toàn thị trường hoặc khả năng chạy trên GitHub từ kết quả cục bộ.

## Kết quả kiểm tra thực ngày 28/10

- Khoảng 20:59 giờ Việt Nam: ba GET latest quotes đều trả một quote với ba mức bid và ba mức offer. Thời gian nguồn lần lượt FPT `14:45:02.669`, HPG `14:45:03.222`, ACB `14:45:01.607` cùng ngày. Đây là dữ liệu cuối phiên, không phải quote trực tiếp lúc kiểm tra buổi tối.
- Khoảng 21:02: truy vấn lịch sử **11:29–11:30** cho ba mã, `limit=2`, tối đa hai trang/mã. Cả sáu request thành công, tổng cộng **12 quote**, phân trang trả bản ghi khác nhau. Tất cả trang còn `nextPageToken`, nên đây chỉ là diagnostic nhỏ, **chưa thu đầy đủ một phút hoặc toàn phiên sáng**.
- Đặc tả OpenAPI chính thức `2026-07-23` mô tả trường `time` là `YYYY-MM-DD HH:mm:ss.SSS (GMT+7)`. Raw giữ nguyên thời gian nguồn và thời điểm nhận UTC riêng; kiểm tra khoảng lịch sử dùng timezone Việt Nam.
- Header thực tế của lịch sử trả `X-RateLimit-Limit=100000`, remaining giảm lần lượt từ 99999 xuống 99994. [Rate Limits chính thức](https://developers.dnse.com.vn/docs/guide/ratelimits/) quy định quotes và latest quotes mỗi endpoint 10.000 request/giờ, 100.000/ngày. Không suy ra miễn phí hoặc tốc độ toàn thị trường chỉ từ header này.
- Báo cáo riêng cục bộ: `report/dnse_preflight.json`, `report/dnse_history_preflight.json`; raw ở `data/raw/dnse_probe_*`. Không đưa dữ liệu thật hoặc thông tin quota tài khoản vào source public. Chưa nhập DNSE vào A1/B5, **đơn vị giá/khối lượng vẫn UNVERIFIED**. Không áp hệ số Vnstock lên DNSE.

[Đặc tả được đối chiếu](https://github.com/dnse-tech/openapi-sdk/blob/main/spec/dnse-openapi-2026-07-23.yaml). Phạm vi thử thực hiện chỉ là ba mã HOSE, bảng G1; chưa chứng minh HNX/UPCOM hoặc thời hạn lưu lịch sử các ngày trước.

## Những phần đã có tài liệu chính thức

- [Latest quotes](https://developers.dnse.com.vn/docs/dnse/get-latest-quotes/): GET `/price/:symbol/quotes/latest`.
- [Historical quotes](https://developers.dnse.com.vn/docs/dnse/get-quotes/): GET `/price/:symbol/quotes`, truy vấn bid/ask theo thời gian và bảng giao dịch.
- [Danh mục mã](https://developers.dnse.com.vn/docs/dnse/get-instruments/): GET `/market/instruments`.
- SDK [dnse-tech/openapi-sdk](https://github.com/dnse-tech/openapi-sdk/tree/main/python) có quote REST và WebSocket; model Quote dùng `bid`/`offer` là danh sách mức giá/khối lượng. Cần đọc payload thật để xác nhận đủ ít nhất ba mức, đơn vị và ý nghĩa timestamp.
- SDK REST chính thức dùng API key + API secret ký HMAC-SHA256, Date và nonce; base URL `https://openapi.dnse.com.vn`, API version được xem trong source là `2026-07-23`. Không suy đoán auth từ hướng dẫn cũ trước KRX.

## Chưa kết luận từ tài liệu

Chưa xác nhận phí trên tài khoản người dùng, phạm vi đủ HOSE/HNX/UPCOM, giới hạn subscribe, giới hạn IP/thiết bị, độ dài lưu trữ hoặc tính ổn định trên hai runner GitHub mới. Quyền lịch sử đã kiểm tra trong một phút của ngày hiện tại; hạn mức tài liệu và header đã ghi ở trên. Không tuyên bố DNSE miễn phí toàn thị trường hoặc chạy CI được chỉ từ việc có key.

Historical quotes có thể cho phép một cách vận hành khác: tải diễn biến bid/ask phiên sáng sau 11:30, thay vì phải tự lưu mọi snapshot trong phiên. Đây mới là khả năng cần kiểm chứng từ quyền truy cập, phân trang và thời hạn dữ liệu; OHLC không thay được bid/ask cho A1/B5.

## Probe chỉ đọc đã chuẩn bị

### Khi không thấy API Secret

Theo [FAQ chính thức DNSE](https://developers.dnse.com.vn/docs/guide/faq/), Secret chỉ hiện một lần khi tạo khóa và không thể xem lại sau khi đóng màn hình thành công. Người dùng tự vào [Entrade X](https://entradex.dnse.com.vn/) → Họ tên → Thông tin tài khoản → LightSpeed API. Nếu không còn bản lưu Secret, chọn **Tạo lại API Key**, rồi lưu ngay cả API Key mới và API Secret mới vào `.env`. Bộ khóa cũ hết hiệu lực ngay, nên ứng dụng khác đang dùng bộ cũ cũng phải cập nhật. Không chọn Hủy API Key vì thao tác đó hủy dịch vụ OpenAPI. [Hướng dẫn đăng ký và quản lý](https://developers.dnse.com.vn/docs/guide/intro/register_guide/).

Client không gọi API DNSE khi thiếu Secret. Việc tạo lại khóa do người dùng thực hiện trong tài khoản; không tự đổi khóa hoặc dùng mật khẩu đăng nhập thay Secret.

```powershell
# Người dùng tự lưu trực tiếp trong .env, không đưa giá trị vào chat:
# DNSE_API_KEY=...
# DNSE_API_SECRET=...
.\.venv\Scripts\python.exe -m scripts.probe_dnse
# Tùy chọn diagnostic lịch sử nhỏ, không tải toàn phiên:
.\.venv\Scripts\python.exe -m scripts.probe_dnse --history-date 2026-10-28 --report report/dnse_history_preflight.json
```

Probe tối đa ba mã FPT/HPG/ACB, khoảng cách tối thiểu hai giây giữa thời điểm bắt đầu request, chỉ GET latest quotes hoặc lịch sử quotes, timeout 30 giây và giữ kiểm tra TLS/hostname. Lịch sử giới hạn tối đa năm phút, hai trang/mã, hai quote/trang; kiểm tra phạm vi mã/bảng/thời gian, token lặp và trang trùng. Không có route tài khoản hoặc đặt/sửa/hủy lệnh trong client. Không chạy nguyên SDK example có in headers/debug hoặc tắt hostname checking; chỉ tái hiện giao thức ký đã đối chiếu từ source chính thức.

Kết quả được lưu raw riêng tại `data/raw/dnse_probe_*` (gitignored), báo cáo `report/dnse_preflight.json`; lọc trường/token nhạy cảm và không lưu request headers. Mọi payload vẫn UNVERIFIED về đơn vị, không nhập vào orderbook Vnstock hoặc đưa vào A1/B5. Timezone có nguồn trong spec nhưng cần tiếp tục đánh giá độ mới và độ phủ dữ liệu.

## Phép thử GitHub đã chuẩn bị, chưa cấp khóa

Workflow `.github/workflows/dnse-preflight.yml` chỉ có `workflow_dispatch`, quyền `contents: read`, tối đa mười phút; không có cron, API giao dịch, upload raw hoặc checkpoint thị trường. Cài `python-dotenv` và dùng client GET riêng, không cần đăng nhập Vnstock. Luôn thử latest ba mã; nhập `history_date` nếu muốn thêm diagnostic một phút.

Sau khi được người dùng cho phép riêng, lệnh `python -m scripts.github_setup dnse-secrets` mã hóa và lưu đúng hai Actions secrets **DNSE_API_KEY**, **DNSE_API_SECRET** vào repo **TranThanhPhu39/neo-liquiditylab**, không đổi Vnstock/state key. Lệnh `dnse-preflight --history-date 2026-10-28` dispatch phép thử. Cần hai runner mới thành công trước khi thiết kế lịch DNSE định kỳ; chưa bật thu tự động từ workflow này.

Sau probe đạt, cần xác minh units và schema bằng bảng giá cùng thời điểm, thử lịch sử một khoảng nhỏ, rồi mới viết mapping chuẩn hóa/collector DNSE. Khi được người dùng cho phép lưu DNSE credentials vào GitHub Secrets, thử nguồn trên hai runner mới và đo quota trước khi mở rộng danh mục. Khóa DNSE chưa được upload lên GitHub chỉ bởi việc đã cho phép hai secrets Vnstock/state-key trước đó.

## Liên hệ với đồ án

DNSE có thể là nguồn thay thế hợp lệ trong giai đoạn phát triển. Tiêu chí cuối kỳ vẫn yêu cầu NEO API là thành phần cốt lõi; không đổi nhãn DNSE thành NEO hoặc kết luận đã hoàn tất yêu cầu NEO.
