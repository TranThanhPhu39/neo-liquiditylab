# SSI trên GitHub Actions

Repo: `TranThanhPhu39/neo-liquiditylab`. Workflow: `.github/workflows/ssi-realtime.yml`.

## Lịch dự kiến, giờ Việt Nam

| Phiên | Cron UTC | Kích hoạt runner | Khoảng thu |
| --- | --- | --- | --- |
| Sáng | `19 1 * * 1-5` | 08:19 | 08:50–11:35 |
| Chiều | `12 5 * * 1-5` | 12:12 | 12:45–15:10 |

Runner có thời gian chuẩn bị trước phiên; không khẳng định cron luôn chạy đúng giờ. Thứ Bảy/Chủ nhật và ngày loại trừ cấu hình được ghi rõ không có phiên dự kiến. `excluded_dates` cần cập nhật khi có ngày nghỉ sở; weekday không tự chứng minh sở có giao dịch.

Workflow `.github/workflows/ssi-schedule-guard.yml` kiểm tra thêm lúc 08:24, 08:34, 08:44 và 08:54 cho phiên sáng. Từ 09:00 đến 15:59, guard kiểm tra ở phút 07/17/27/47/57 vào ngày thường. Guard chỉ khởi chạy phiên trong cửa sổ thu khi bộ thu vắng mặt, có thời gian chờ 10 phút và tối đa hai lượt. `GITHUB_TOKEN` chỉ đọc trạng thái và kích hoạt workflow trong repo. Cả hai cron phụ thuộc lịch GitHub, có thể đến trễ hoặc bị bỏ lỡ. Windows tiếp tục làm dự phòng khi máy bật và đã đăng nhập.

## Thu cổ phiếu toàn thị trường

Chế độ `collect` đọc danh mục SSI khi mở phiên, lấy `stockType=Stock` thuộc HOSE, HNX và UPCoM. Một kết nối đăng ký `quote` và `trade` theo ba sàn. Bộ thu lọc mã theo danh mục, giữ cặp giá và lượng gốc, thời gian nguồn và thời gian nhận. Kiểm tra tổng số mã trong danh mục cùng số mã thực có quote/trade theo từng sàn trong mục `market_capture` của Actions Summary.

Kho toàn thị trường gồm các đoạn Parquet được mã hóa riêng dưới tag `ssi-market-…`. Mỗi đoạn được kiểm tra sau khi tải xuống và giải mã. Các mã không có bản tin được thể hiện qua chênh lệch giữa số mã trong danh mục và số mã có quan sát. Dùng `python -m scripts.restore_ssi_checkpoint --tag <tag>` để khôi phục một đoạn vào `data/realtime/ssi-market`. Các bản phát lại public lấy từ luồng ACB/FPT/HPG. Dữ liệu toàn thị trường đang ở đơn vị nguồn SSI. Nhãn số mã có quan sát không chứng nhận mức phủ đầy đủ của phiên.

Trong **Run workflow**, chọn `market-probe` để thử đọc danh mục và đăng ký topic. Chế độ này trả số yêu cầu đăng ký đã được SSI chấp nhận và số bản tin đếm được, không ghi bản thu hoặc tạo Release. Để nghiệm thu độ phủ, dùng số liệu một lượt `collect` chạy trong phiên giao dịch.

Hai biến `SSI_REALTIME_STORAGE_APPROVED=true` và `SSI_REALTIME_ENABLED=true` mới cho phép thu theo lịch. Khi đang kiểm tra, chỉ bật storage sau khi được chấp thuận, giữ enabled=false. Thử hai lượt `workflow_dispatch` mode `smoke` để kiểm chứng hai runner mới có quote/trade và checkpoint tải lại/giải mã được rồi mới bật enabled.

## Khóa và dữ liệu

`SSI_API_KEY`, `SSI_API_SECRET` được lưu bằng Actions Secrets sau khi chủ tài khoản chấp thuận. Không đưa `.env`, credentials, raw SSI hoặc hợp đồng/evidence riêng trên máy vào Git. Dùng `LIQUIDITYLAB_STATE_KEY` đã có để mã hóa AES-GCM trước khi lưu Releases.

Mỗi 15 phút tạo checkpoint Parquet mã hóa; bản cuối được tải lại từ GitHub, giải mã và kiểm tra checksum/tổng số hàng. Chỉ khi bản cuối đã xác minh mới xóa các checkpoint tạm **do chính lần chạy đó tạo**, giữ bản cuối và các phiên trước nguyên vẹn. Nếu runner bị kết thúc đột ngột, phần chưa có checkpoint có thể mất; không khẳng định backfill sổ lệnh SSI đã hỗ trợ.

Runner không có evidence đơn vị riêng của máy nên bắt đầu với nhãn chưa xác minh và detector khóa. Cờ C vẫn không được tự đổi sang LO. Điều này không chặn thu quote/trade và lưu raw/canonical nguồn.

Có thể cấp gói chứng chỉ đơn vị đã rà soát qua Actions Secret `SSI_UNIT_CONTRACT_BUNDLE` sau khi được chấp thuận riêng. Loader giới hạn 60 KB, tên tệp bằng chứng, phạm vi mã và checksum; xác minh toàn bộ trước khi cài contract. Khi secret trống, giữ nhãn chưa xác minh. Chứng chỉ dùng nguồn tham chiếu cập nhật chậm chỉ xác minh đơn vị, không chứng nhận độ trễ, phiên LO hoặc completeness. Không commit chứng chỉ/dữ liệu đối chiếu vào repo public.

Collector đăng ký `market.flag` theo SDK SSI, bên cạnh `market.hose` và `indexsummary.VNINDEX`. Chỉ giữ cờ HOSE; bỏ qua cờ các sàn khác trên kênh phát chung. Ưu tiên cờ trực tiếp hơn metadata chỉ số. Trong các cờ trực tiếp, bản tin mới hơn được dùng; xung đột cùng thời gian nguồn khóa detector. `LO` cần cùng ngày/kết nối và cùng khoảng liên tục sáng hoặc chiều với quote; không mang LO sáng qua giờ nghỉ trưa. Nguồn cờ, đồng hồ và lý do chọn/bỏ cờ được lưu để đối chiếu.

Metadata phiên không đủ schema trên topic market đã đăng ký được cô lập thành `SYSTEM/SESSION_METADATA_QUARANTINED`, tăng số bản tin bị loại và xóa cờ đã nhớ để khóa detector, còn bộ thu tiếp tục nhận quote/trade. Bộ kiểm tra phạm vi quote/trade và kênh riêng tư vẫn dừng khi có vi phạm. Lỗi phạm vi được ghi thêm chẩn đoán cấu trúc đã lọc trong Parquet, không ghi thông tin tài khoản/khóa. Có bản tin bị loại thì job không được báo đã thu sạch toàn phiên.

## Kết quả công việc

- `NETWORK_PREFLIGHT_PASSED`: DNS/TCP/TLS công khai đạt, chưa chứng minh quyền đọc quote/trade.
- `SMOKE_ENCRYPTED_AND_RESTORE_VERIFIED`: ba mã có quote, có giao dịch khớp, bản lưu mã hóa tải lại kiểm tra đạt.
- `COLLECTED_ENCRYPTED_AND_RESTORE_VERIFIED`: thu trong cửa sổ, không ghi nhận gián đoạn/quarantine, bản lưu xác minh đạt; vẫn không chứng minh feed có đầy đủ mọi message của sở khi không có provider sequence.
- `PARTIAL_LATE_START` hoặc `PARTIAL_CONNECTION_GAPS`: dữ liệu vẫn được lưu nhưng job trả lỗi, không hiển thị Success như đã thu đủ.
- Khởi động sau giờ đóng: trả lỗi rõ, không gọi REST tải bù giả định và không bỏ qua thành công.
- Lưu hoặc xác minh mã hóa thất bại: job thất bại, không khẳng định dữ liệu đã được lưu an toàn.

Kết quả an toàn nằm trong Actions Summary và `report/github/ssi_realtime_result.json` trên runner. Releases chỉ chứa ciphertext và manifest kỹ thuật; không có payload SSI plaintext. Lưu trữ cloud trong bước này là để thu và xem lại, chưa làm GitHub Pages thành backend live hoặc dịch vụ 24/7.

## Đưa dữ liệu cloud vào dashboard trên máy

Liệt kê các bản đã lưu rồi nhập đúng tag cần xem:

```powershell
.\.venv\Scripts\python.exe -m scripts.restore_ssi_checkpoint --list
.\.venv\Scripts\python.exe -m scripts.restore_ssi_checkpoint --tag <tag-trong-danh-sach>
```

Lệnh tải ciphertext, giải mã bằng khóa khôi phục có sẵn trên máy, kiểm tra checksum/Parquet rồi lưu vào `data/realtime/ssi/<ngày>/<run_id>/`. Không ghi đè run đã có. Trong trang `/realtime` của dashboard local, tải lại danh sách và chọn run đã nhập để replay. Cần giữ an toàn bản khóa khôi phục; không thể giải mã nếu mất khóa. Lệnh không tự tạo khóa mới khi thiếu khóa cũ.

## Giới hạn

Giới hạn 6 giờ hiện tại trong ứng dụng là giới hạn an toàn của collector, không phải SSI xác nhận. Mỗi job GitHub-hosted cũng có giới hạn 6 giờ; workflow SSI đặt timeout 220 phút, chia sáng/chiều để chừa thời gian lưu. [Giới hạn GitHub](https://docs.github.com/en/actions/reference/limits).

Lịch GitHub có thể trễ hoặc bị bỏ khi quá tải. [GitHub schedule](https://docs.github.com/en/actions/reference/workflows-and-actions/events-that-trigger-workflows#schedule). Không tự thay lịch DNSE/Vnstock, không công khai SSI qua Pages và không gọi giao dịch/account APIs.


Ngày 16/11/2026, bản tin chuyển ATC thực tế dùng `market.FLAG` (chữ hoa), gồm HOSE/HNX/DER. Parser nhận cả `market.flag` và `market.FLAG`, chuẩn hóa topic nội bộ và giữ `source_topic` để truy vết; chỉ xử lý cờ HOSE. Cờ ATC được giữ nguyên, không đổi thành LO. Đã replay bốn bản tin lỗi thực tế và kiểm tra lại phạm vi/kênh riêng tư.
