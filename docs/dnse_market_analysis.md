# Phân tích mô tả các mã còn lại

```powershell
.\.venv\Scripts\python.exe -m scripts.report_dnse_market --dates 2026-10-28 2026-10-29
```

Lệnh mặc định tạo báo cáo cho tất cả mã có trong danh mục các kho ngày đã chọn, gồm cả mã API trả rỗng. ACB được giữ làm đối chứng. Đầu ra ở `report/dnse_market_analysis_2026-10-28_2026-10-29/index.html`; có thể tìm theo mã, sàn hoặc trạng thái rồi mở báo cáo riêng của từng mã. Toàn bộ dữ liệu đọc offline, không gọi API.

Mỗi báo cáo có spread bps, OBI, độ sâu theo **đơn vị quantity nguồn**, thống kê sáng/chiều, độ phủ dữ liệu và bốn biểu đồ dạng đường/cột. Độ sâu này được lấy lại bằng phép đảo hệ số chuyển đổi đã audit; không phải bằng chứng độc lập rằng đơn vị đã là cổ phiếu. Spread và OBI là tỷ lệ trong cùng schema nguồn. Các mã chưa xác minh đơn vị không được mô phỏng chi phí/VWAP/proceeds/stress. Liên kết đến A1/B5 riêng của ACB được hiển thị khi bản báo cáo đó có trên máy.

## Cùng phương pháp, đúng khung giờ từng sàn

- HOSE: 135 mốc AM, 90 mốc PM, tổng 225 phút/ngày.
- HNX: 150 mốc AM, 90 mốc PM, tổng 240 phút/ngày.
- UPCOM: 150 mốc AM, 120 mốc PM, tổng 270 phút/ngày.

Tại mỗi cuối phút, truy vấn sự kiện cuối không muộn hơn mốc; yêu cầu đúng giai đoạn liên tục theo quy tắc giờ, tuổi nguồn tối đa 60 giây và đủ ba mức hợp lệ mỗi phía. Không tìm lùi bản hợp lệ để thay thế thông điệp cuối bị lỗi. Không nội suy qua nghỉ trưa hoặc sang ngày khác. Đây là quy tắc giờ phân tích, không thay cho trạng thái mở cửa và lịch nghỉ chính thức của sàn.

So sánh giữa các ngày chỉ trên tập phút đủ điều kiện chung. Có thể bỏ chọn “Chỉ vẽ các phút chung” để xem riêng các mẫu đủ tươi từng ngày, nhưng khi đó tập thời điểm có thể khác nhau. Khoảng thời gian được giữ theo giờ nguồn, thời điểm thực nhận nằm riêng trong dữ liệu truy vết. Đây là tái dựng hồi tố sau phiên, không phải backtest trên dữ liệu chương trình đã biết trong phiên.

Độ phủ mẫu đo việc thỏa các tiêu chí dữ liệu, không đo số giao dịch, không phải xếp hạng cổ phiếu. Với nguồn chỉ gửi khi sổ lệnh đổi, thông điệp cũ hơn 60 giây bị loại theo phương pháp bảo thủ; điều đó không tự chứng minh cổ phiếu không có thanh khoản.

## Phân biệt trạng thái

“Có mẫu chung để so sánh”: có ít nhất một mốc đủ điều kiện ở mọi ngày đã chọn. Số mốc vẫn phải đọc cùng kết quả, đặc biệt khi ít mẫu.

“Có bản ghi, chưa đủ phút chung”: kho có sự kiện nhưng không đạt đồng thời yêu cầu độ mới/đủ mức/cùng phút giữa các ngày. Không đổi thành NO DATA hoặc khối lượng 0.

“Chưa có snapshot trong kho đã chọn”: không có bản chuẩn hóa ở các ngày chọn. Báo cáo vẫn giữ trạng thái phản hồi API, số bản ghi gốc và việc mã có xuất hiện trong danh mục từng ngày hay không. Thiếu danh mục khác với phản hồi rỗng. Số 0 có trong thông điệp nguồn với giá hợp lệ được giữ là 0; không tạo số 0 cho phần nguồn không trả về.

## Tệp, tiếp tục và mở offline

`symbols/MA.html` là trang riêng; `symbols/MA.json.gz` lưu mọi mốc, event ID, thời điểm nguồn/nhận, lý do loại trừ và các thống kê. `summary.csv/json` là tổng hợp; `manifest.json` ghi phương pháp và hash manifest nguồn. Không nạp toàn bộ 1,8 triệu snapshot vào bộ nhớ: mỗi truy vấn chỉ lấy tối đa 270 điểm của một mã/ngày, xử lý và ghi từng mã.

Các trang dùng chung `assets/plotly.min.js` cục bộ, không cần CDN. Giữ nguyên cả thư mục khi sao chép báo cáo; không chỉ sao chép một tệp HTML con. Báo cáo dữ liệu thật không được đưa lên repo public.

Chạy lại cùng lệnh tiếp tục dùng các kết quả đã có khi phiên bản/phạm vi/hash đầu vào còn khớp. Nếu dữ liệu nguồn đã đổi, chọn `--output THU_MUC_MOI`; không trộn kết quả từ hai phiên bản. `--refresh-pages` dựng lại HTML từ dữ liệu đã phân tích có cùng fingerprint. Muốn chạy riêng vài mã, thêm `--symbols FPT HPG PVS`.

## Mở rộng xác minh đơn vị

Quy trình này không sửa nhãn đơn vị hay hồ sơ chuẩn hóa. Dùng [bộ đối chiếu đơn vị](dnse_session_analysis.md) để gắn raw, ảnh độc lập cùng thời điểm và sáu cặp giá/khối lượng. Chỉ sau khi rà soát bằng chứng mới lập hồ sơ/bản dữ liệu phiên bản mới. Không suy bằng chứng ACB thành xác minh mọi mã/sàn.

Tài liệu endpoint nguồn: [DNSE lịch sử bid/ask](https://developers.dnse.com.vn/docs/dnse/get-quotes/), [SDK chính thức](https://github.com/dnse-tech/openapi-sdk/tree/main/python). Các trang đã truy xuất không cung cấp đủ bằng chứng để công cụ tự cấp xác minh hệ số khối lượng cho toàn bộ mã; vì vậy báo cáo tiếp tục giữ trạng thái chưa xác minh và không dùng các thư viện/bot bên thứ ba làm bằng chứng.
