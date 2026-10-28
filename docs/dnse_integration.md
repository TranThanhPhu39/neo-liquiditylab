# Đánh giá DNSE làm nguồn thay thế trong lúc chờ NEO

Ngày xem xét: 28/10/2026. Người dùng đã lưu đủ bộ khóa mới cục bộ. Latest quotes và một khoảng lịch sử nhỏ đã trả dữ liệu thật cho FPT/HPG/ACB; chưa kết luận quyền toàn thị trường hoặc khả năng chạy trên GitHub từ kết quả cục bộ.

Đã bổ sung [bộ tải lại cả ngày trên máy](dnse_daily_history.md) theo yêu cầu tải toàn thị trường, có danh mục DNSE 1.520 cổ phiếu, checkpoint SQLite và audit/export raw. Lượt tải thủ công cục bộ này độc lập với workflow GitHub đang lỗi kết nối; không bật lại lịch tự động. Kết quả nghiệm thu nằm trong báo cáo ngày cục bộ sau khi chạy, không suy ra hoàn tất chỉ từ sự hiện diện của script.

## Hai lượt GitHub đã thực hiện

Người dùng đã cho phép riêng và đã cấu hình hai Actions Secrets `DNSE_API_KEY`, `DNSE_API_SECRET` trong `TranThanhPhu39/neo-liquiditylab`. Không đổi hai secret Vnstock/state và không bật lịch thu.

| Lượt | Runner | Giờ Việt Nam 28/10 | Kết quả |
| --- | --- | --- | --- |
| [40706823793](https://github.com/TranThanhPhu39/neo-liquiditylab/actions/runs/40706823793) | GitHub Actions 1000000216 | 21:31:49–21:32:26 | Dừng ở request FPT đầu tiên, ProviderError, không nhận được HTTP status hoặc quote; bản probe đầu chưa phân loại chi tiết lỗi mạng. |
| [40707527626](https://github.com/TranThanhPhu39/neo-liquiditylab/actions/runs/40707527626) | GitHub Actions 1000000218 | 21:36:01–21:36:43 | DNS trả `103.151.242.24`; kết nối TCP 443 timeout sau khoảng 5 giây. Chưa tới TLS. GET FPT sau đó timeout sau 30 giây, mã `DNSE_TIMEOUT`, không có quote. |

Đối chứng từ máy người dùng lúc **21:36:23** cùng ngày vẫn lấy FPT thành công, đủ ba mức mua/bán. Vì phép thử GitHub dừng ngay lỗi đầu, HPG/ACB và lịch sử không được gọi trên hai runner này. Chưa có bằng chứng khóa sai, device limit hoặc DNSE có chính sách chặn GitHub; dữ liệu thực nghiệm chỉ xác nhận đường kết nối được thử đang timeout. Không đổi API key, giả IP hoặc tắt kiểm tra TLS để xử lý lỗi TCP.

CI Ubuntu của commit `05652c57794cc4ed0f55d22e3977b28bad2cd001` đạt **152 tests**: [40707507492](https://github.com/TranThanhPhu39/neo-liquiditylab/actions/runs/40707507492). Báo cáo lọc log ở `report/github/run_*.json` chỉ giữ kết quả chẩn đoán và thông tin runner; không in khóa hoặc request headers. Lịch Windows Disabled và GitHub worker false.

Bước cần giải quyết tiếp: nhờ DNSE xác nhận khả năng truy cập REST từ GitHub-hosted runner, giới hạn IP/vùng hoặc yêu cầu whitelist. [Nội dung hỏi hỗ trợ đã soạn](dnse_support_request.md); **chưa gửi**. WebSocket dùng host chính thức khác, chưa được thử trên GitHub và không thay cho bằng chứng truy cập REST/lịch sử.

## Kết quả kiểm tra thực ngày 28/10

- Khoảng 20:59 giờ Việt Nam: ba GET latest quotes đều trả một quote với ba mức bid và ba mức offer. Thời gian nguồn lần lượt FPT `14:45:02.669`, HPG `14:45:03.222`, ACB `14:45:01.607` cùng ngày. Đây là dữ liệu cuối phiên, không phải quote trực tiếp lúc kiểm tra buổi tối.
- Khoảng 21:02: truy vấn lịch sử **11:29–11:30** cho ba mã, `limit=2`, tối đa hai trang/mã. Cả sáu request thành công, tổng cộng **12 quote**, phân trang trả bản ghi khác nhau. Tất cả trang còn `nextPageToken`, nên đây chỉ là diagnostic nhỏ, **chưa thu đầy đủ một phút hoặc toàn phiên sáng**.
- Đặc tả OpenAPI chính thức `2026-07-23` mô tả trường `time` là `YYYY-MM-DD HH:mm:ss.SSS (GMT+7)`. Raw giữ nguyên thời gian nguồn và thời điểm nhận UTC riêng; kiểm tra khoảng lịch sử dùng timezone Việt Nam.
- Header thực tế của lịch sử trả `X-RateLimit-Limit=100000`, remaining giảm qua các request và được giữ trong báo cáo cục bộ. [Rate Limits chính thức](https://developers.dnse.com.vn/docs/guide/ratelimits/) quy định quotes và latest quotes mỗi endpoint 10.000 request/giờ, 100.000/ngày. Không suy ra miễn phí hoặc tốc độ toàn thị trường chỉ từ header này.
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

## Cách chạy lại phép thử GitHub khi đã xử lý kết nối

Workflow `.github/workflows/dnse-preflight.yml` chỉ có `workflow_dispatch`, quyền `contents: read`, tối đa mười phút; không có cron, API giao dịch, upload raw hoặc checkpoint thị trường. Cài `python-dotenv` và dùng client GET riêng, không cần đăng nhập Vnstock. Luôn thử latest ba mã; nhập `history_date` nếu muốn thêm diagnostic một phút.

Lệnh `python -m scripts.github_setup dnse-secrets` mã hóa và lưu đúng hai Actions secrets **DNSE_API_KEY**, **DNSE_API_SECRET** vào repo **TranThanhPhu39/neo-liquiditylab**, không đổi Vnstock/state key. Đã chạy theo chấp thuận; không cần tạo lại khóa chỉ vì timeout mạng. Lệnh `dnse-preflight --history-date 2026-10-28` dispatch phép thử. Cần hai runner mới thành công trước khi thiết kế lịch DNSE định kỳ; chưa bật thu tự động từ workflow này.

Sau khi REST trên GitHub hoạt động, cần nghiệm thu dữ liệu trên hai runner, xác minh units/schema bằng bảng giá cùng thời điểm và đo quota/dung lượng khi mở rộng danh mục, rồi mới hoàn thiện mapping chuẩn hóa/collector DNSE và lịch sau phiên sáng. Phép thử nhỏ hiện tại không chứng minh có thể tải toàn bộ lịch sử thị trường trong một job hoặc dưới quota miễn phí.

## Liên hệ với đồ án

DNSE có thể là nguồn thay thế hợp lệ trong giai đoạn phát triển. Tiêu chí cuối kỳ vẫn yêu cầu NEO API là thành phần cốt lõi; không đổi nhãn DNSE thành NEO hoặc kết luận đã hoàn tất yêu cầu NEO.
