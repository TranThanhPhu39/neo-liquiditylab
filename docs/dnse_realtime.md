# Bộ thu realtime DNSE G1

Cập nhật 03/11/2026. Bộ thu dùng WebSocket DNSE cho dữ liệu thị trường, không gọi API giao dịch. Nguồn chính của đồ án là DNSE. REST lịch sử, kho chuẩn hóa và bộ thu WebSocket có đường lưu riêng.

## Khởi chạy cục bộ

`.env` cần `DNSE_API_KEY` và `DNSE_API_SECRET`. Không đưa giá trị vào câu lệnh hoặc commit.

```powershell
# Làm mới danh mục qua REST trên máy; không upload.
.\.venv\Scripts\python.exe -m scripts.prepare_dnse_realtime
# Thử toàn bộ danh mục trong 60 giây.
.\.venv\Scripts\python.exe -m scripts.collect_dnse_realtime --mode smoke --seconds 60
# Thu tới hết cửa sổ phiên tương ứng.
.\.venv\Scripts\python.exe -m scripts.collect_dnse_realtime --mode collect --session morning
.\.venv\Scripts\python.exe -m scripts.collect_dnse_realtime --mode collect --session afternoon
```

Lệnh `collect` chỉ nhận khởi chạy từ 10 phút trước cửa sổ tới trước giờ kết thúc. Cửa sổ quan sát là 08:57–11:31 và 12:57–15:06 giờ Việt Nam, thứ Hai–thứ Sáu. Đây là cửa sổ vận hành, không phải bộ lịch giao dịch chính thức; chưa nhận biết ngày nghỉ lễ. Muốn thu trên máy phải giữ máy bật, có mạng. Khóa OS ngăn hai tiến trình collector cùng workspace chạy trùng.

Để dừng và xả hàng đợi vào SQLite, tạo tệp sau rồi đợi trạng thái `STOPPED_BY_USER`:

```powershell
New-Item -ItemType File -Path data/control/STOP_DNSE_REALTIME -Force
```

Trước lần chạy sau, bỏ tệp STOP nếu đã chủ động cho phép thu lại. Mỗi phiên có partition riêng; chạy lại cùng partition giữ receipts cũ. Đổi danh mục trong cùng partition bị từ chối để tránh trộn phạm vi.

## Phạm vi, lưu trữ và chất lượng

Danh mục ngày 03/11 có 1.518 cổ phiếu ST thuộc HOSE/HNX/UPCOM. Chia tối đa 190 mã/kết nối, tối đa 9 kết nối; hiện cần 8. Giới hạn chính thức là 200 streams/kết nối và 10 kết nối/người dùng: [DNSE WebSocket](https://developers.dnse.com.vn/docs/guide/market-data/connect/). Không dùng wildcard. Các ứng dụng khác dùng cùng tài khoản cũng có thể chiếm hạn mức kết nối.

Danh mục riêng `data/control/dnse_ws_catalog.json` hết hạn khi quá 7 ngày, cần làm mới trên máy. Runner hiện không truy cập được REST DNSE nên chưa tự phát hiện mã mới/hủy niêm yết. “Toàn thị trường” ở đây là toàn bộ danh mục hợp lệ đã chuẩn bị, không bảo đảm danh mục luôn mới từng ngày.

SQLite nằm ở `data/realtime/dnse/YYYY-MM-DD/morning/quotes.db` hoặc `afternoon/quotes.db`; smoke nằm trong `smoke-<scope hash>`. Báo cáo trạng thái ở `report/realtime/dnse/YYYY-MM-DD/<partition>/status.json`.

- WAL và transaction lưu từng batch; mỗi receipt giữ connection, thời điểm nhận và hash frame. Payload thị trường theo allowlist được nén, khử trùng theo hash, vẫn giữ mọi receipt.
- Có thời điểm nguồn UTC, ngày nguồn theo Việt Nam và thời điểm nhận riêng. Giá phân số giữ dạng text để bảo toàn chữ số, khối lượng giữ nguyên. Không áp dụng hệ số REST vào WebSocket.
- Đơn vị luôn `UNVERIFIED_WEBSOCKET`; chưa đưa dữ liệu này vào A1/B5 hoặc dashboard chuẩn hóa. Cần đối chiếu độc lập trong phiên trước khi quy đổi.
- Reconnect có backoff hữu hạn và subscribe lại. Ghi khoảng khởi động chưa quan sát, gián đoạn và khoảng không chắc chắn khi khôi phục checkpoint. Đây không phải ước lượng chính xác số thông điệp mất.
- Mã không có thông điệp vẫn là chưa quan sát, không thành số 0. Kết thúc phiên trả `WINDOW_ENDED_OBSERVED_ONLY`, không tự kết luận đầy đủ cả ngày.
- Queue có giới hạn; dừng khi writer lỗi, thiếu đĩa, vượt dung lượng/ngân sách hoặc checkpoint lỗi lặp lại. Mặc định tối thiểu 5 GiB trống, DB+WAL tối đa 1,5 GiB, 5 triệu receipts/lượt và 8 GiB dữ liệu đường truyền/lượt. Đây là ngưỡng bảo vệ ban đầu, chưa có benchmark cả phiên toàn thị trường.

## GitHub Actions: đã chuẩn bị, chưa bật

Workflow `.github/workflows/dnse-realtime.yml` dự kiến khởi chạy 08:52 và 12:52 giờ Việt Nam ngày thường, chuẩn bị trước cửa sổ thu 5 phút. Khởi chạy trễ sẽ được ghi nhận; WebSocket không bù lịch sử đã bỏ lỡ. Cron sáng không bị chuyển thành phiên chiều khi runner đến trễ. Mỗi job tối đa 195 phút, concurrency không hủy job đang thu.

Workflow bị khóa bằng hai biến repo: `DNSE_REALTIME_STORAGE_APPROVED` cho phép lưu payload mã hóa và `DNSE_REALTIME_ENABLED` cho phép thu định kỳ. Hiện chưa bật hai cờ này. Thử thủ công cũng cần cờ chấp thuận lưu trữ. Lịch Vnstock cũ vẫn giữ tắt.

Payload đề xuất cho **GitHub Releases public `TranThanhPhu39/neo-liquiditylab`**:

1. Seed danh mục: mã, sàn, thông tin danh mục từ DNSE và ngày chuẩn bị.
2. Checkpoint: SQLite chứa trường thị trường, receipts, metadata kết nối/gaps; status, audit và danh mục. Không chứa API key/secret, phiên xác thực, lệnh hoặc tài sản tài khoản.
3. Nội dung trên mã hóa AES-256-GCM bằng secret hiện có `LIQUIDITYLAB_STATE_KEY`; manifest hash, kích thước và tên tag/ngày vẫn công khai. Cần giữ bản sao khóa để khôi phục.

Seed và checkpoint dùng namespace riêng `dnse-ws-catalog` / `dnse-ws-state`, không trộn checkpoint Vnstock. Checkpoint mỗi 15 phút và khi dừng; runner mới khôi phục bản hợp lệ gần nhất. Runner bị kill có thể mất phần chưa checkpoint. Chưa có chính sách tự xóa checkpoint cũ.

**Chưa upload payload thật:** kiểm duyệt tự động yêu cầu chấp thuận rõ loại dữ liệu và đích public này. Sau khi người dùng đồng ý mới đưa seed lên, thử hai runner để xác minh thu/lưu/khôi phục, rồi bật lịch. Không chỉ bật cờ để bỏ qua kiểm thử.

## Bằng chứng hiện có

Ngày 03/11, local smoke 15:41:23–15:42:23 đăng ký và xác thực đủ 8/8 shard cho 1.518 mã, không có quote sau giờ đóng cửa; không coi đây là dữ liệu cả phiên. SQLite integrity ok, FK 0, receipts chưa lưu 0. Bản sao DB này đã qua mã hóa/khôi phục cục bộ nguyên vẹn.

Probe trước đó nhận quote MSR thật trên máy và hai runner GitHub mới, xem [tài liệu probe](dnse_websocket.md). Chưa kiểm chứng full-market trong phiên, lưu lượng dài, lịch tự chạy và khôi phục dữ liệu thật giữa hai runner. Tests offline kiểm tra ghi/khử trùng, transaction rollback, reconnect, ngắt, stop, audit payload và checkpoint; không thay bằng chứng vận hành trong phiên.
