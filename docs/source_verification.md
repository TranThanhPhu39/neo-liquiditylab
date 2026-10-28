# Xác minh nguồn và phiên bản · 27/10/2026

## Cập nhật 28/10/2026

Bổ sung sau 14:40: đã thẩm định thực nghiệm đơn vị quote Vnstock 5.2.1/KBS cho ACB, HPG, FPT. SDK trả VND/CP và CP, hệ số chuẩn hóa 1/1. Xem [thẩm định đầy đủ](../report/kbs_unit_review_2026-10-28.md), hồ sơ `config/unit_contract.vnstock-kbs-5.2.1.json` và bằng chứng JSON có checksum. Ảnh người dùng chụp khoảng 14:30, SDK nhận 14:34:47.898765; có 8 cặp giá/volume khớp trên ba mã, cả bid và ask, sau áp dụng hệ số hiển thị từ chú thích KBS chính thức được lập chỉ mục. Các mức khác nhau được giữ nguyên; không xác nhận timestamp nguồn hoặc đồng bộ hai lần quan sát.

Đã chuyển 66 snapshot chuẩn hóa từ 22 raw batch, tất cả VALID. Source timestamp vẫn null, raw bất biến; bản chẩn đoán 14:34 không được cộng vào lịch sử collector. Đã kiểm chứng số học A1 mua/bán, partial coverage, B5 giảm depth 50% và danh mục giả định trên lịch sử. Các đoạn dưới đây ghi tiến trình trước thẩm định và được giữ để truy vết, không phải trạng thái hiện tại.

Bổ sung lúc 13:23: đã khởi động collector raw có thời hạn cho 3 mã, interval 180 giây, dự kiến dừng 14:30. Raw tách khỏi `orderbook_snapshots`, chưa chạy finance. Báo cáo tiến độ `report/raw_collection_2026-10-28.json`, audit/backup `report/raw_audit_2026-10-28.json`. Xem `collection_runbook.md` để biết bằng chứng còn thiếu về đơn vị. Không coi raw volume là CP hoặc gán timestamp nguồn từ đồng hồ nhận.

Đã gọi bảng giá thật một lần ở cấp Khách cho FPT/HPG/ACB và nhận đủ các trường ba mức giá; bằng chứng tại `report/vnstock_probe_2026-10-28.json`. Sau khi người dùng tự lưu khóa trong `.env` với tên `vnstock_api`, adapter đã được bổ sung hỗ trợ tên này (tên chuẩn `VNSTOCK_API_KEY` ưu tiên). Đã kiểm chứng đăng nhập thực thành công: tier `free`, authenticated `true`; báo cáo đã lọc thông tin nhạy cảm tại `report/vnstock_auth_2026-10-28.json`.

Các lần thăm dò là request mạng thật, không phải fixture. Đơn vị volume và timestamp thị trường vẫn chưa được xác minh đủ để mở collector chuẩn hóa; không coi thời điểm nhận REST là thời điểm cập nhật giá. Chưa có chuỗi nhiều phiên hoặc dữ liệu NEO. Các đoạn bên dưới ghi hiện trạng ban đầu ngày 27/10 và được giữ để truy vết.

Lúc 13:10 giờ Việt Nam ngày 28/10, đã gọi quote thật sau đăng nhập thành công cho cả 3 mã, không thiếu cột bắt buộc; kết quả tại `report/vnstock_authenticated_quote_2026-10-28.json`. Metadata chỉ có source/route, không có đơn vị volume hoặc timestamp thị trường. Mẫu giữ ở báo cáo thăm dò riêng, chưa nhập kho phân tích chuẩn hóa.

## Kết quả trực tiếp trong môi trường triển khai

- Python 3.12.10 (Windows). Môi trường dự án `.venv`.
- Trang cài đặt và trang Market của Vnstock còn ghi v5.0.1. Kho chính thức qua pip đánh dấu 5.0.1 yanked, hướng tới 5.0.3; 5.0.3 cũng yanked với lý do hết hạn thử nghiệm. Đã thay bằng **5.2.1**, bản không bị đánh dấu rút trong `pip index versions` từ kho chính thức. Không giữ adapter mặc định ở bản hết hạn.
- Đã cài 5.2.1 và kiểm tra runtime trực tiếp `import vnstock; from vnstock import Market; inspect.signature(Market.quote)`; kết quả:

```text
version = 5.2.1
quote_signature = (self, symbols_list: 'Sequence[str]', **kwargs: 'object') -> 'pd.DataFrame'
login_signature = (api_key: 'str') -> 'dict[str, Any]'
```

Đối chiếu mã nguồn SDK đã cài `vnstock/ui/market.py`: `Market.quote` gọi `market_quote` với danh sách mã và source từ kwargs. Vì vậy adapter giữ `Market().quote(symbols, source="kbs")`, cố định version 5.2.1 và kiểm tra các cột response trước chuẩn hóa. Import/signature **không là bằng chứng request market-data thành công**. Tests dùng injected fixture, không giả mạo kết nối live.

## Điều còn phải xác minh trước thu thật

[Market chính thức](https://vnstocks.com/docs/vnstock/thi-truong-market) mô tả quote dùng giá đồng và bid/ask ba mức, `close_price` là giá gần nhất, multi-symbol dùng một truy vấn. [Cấp quyền và giới hạn](https://vnstocks.com/docs/vnstock/tham-khao/cap-quyen-va-gioi-han) phân biệt quote Cộng đồng và order_book Mở rộng; tự động hóa cần đăng nhập, quota phụ thuộc tài khoản. [Cài đặt chính thức](https://vnstocks.com/docs/vnstock/cai-dat) yêu cầu kho gói riêng. Do trang tài liệu phiên bản cũ hơn SDK thực tế, cần kiểm tra hợp đồng dữ liệu trên tài khoản được phép trước khi công nhận tương thích dữ liệu live.

Chưa gửi request quote, chưa đăng nhập, chưa kiểm chứng quyền tài khoản/quota/source timestamp và **chưa xác nhận volume của payload thực tính theo CP**. Không nhân/chia đơn vị; cấu hình volume để trống mặc định nhằm buộc xác nhận. SDK hiện không có tham số timeout tường minh trên signature quote, nên wrapper dùng subprocess deadline; không monkeypatch network để né SDK.

## NEO

Các trang [overview](https://neoinvest.vpbanks.com.vn/neo-openapi/docs/overview), [stock detail](https://neoinvest.vpbanks.com.vn/neo-openapi/docs/market/stock-detail), [market stream](https://neoinvest.vpbanks.com.vn/neo-openapi/docs/streaming/market-stream) chỉ đọc được khung điều hướng và Loading qua công cụ duyệt trong phiên này. Không đủ xác minh payload/auth/URL stream. Không có credentials và không thực hiện request NEO. Xem neo_integration_plan.md.

## Tái kiểm tra cục bộ

```powershell
.\.venv\Scripts\python.exe -m scripts.check_environment
.\.venv\Scripts\python.exe -m scripts.inspect_vnstock
.\.venv\Scripts\python.exe -m pip index versions vnstock --index-url https://vnstocks.com/api/packages
```

Không tự nâng SDK khi chạy app; nâng phiên bản cần đối chiếu signature, schema, quyền/đơn vị và cập nhật tests trước.
