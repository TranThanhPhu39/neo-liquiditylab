# Báo cáo ACB và đối chiếu đơn vị DNSE

## Báo cáo hai ngày

```powershell
.\.venv\Scripts\python.exe -m scripts.report_dnse_sessions --dates 2026-10-28 2026-10-29
```

Đầu ra là `report/dnse_acb_analysis_2026-10-28_2026-10-29.html`, Markdown, JSON và ba tệp CSV gồm mốc phút, kết quả A1, kết quả B5. HTML chứa Plotly ngay trong tệp, không cần CDN. Dữ liệu thật vẫn ở máy; clone repo public không tự có báo cáo hay kho riêng.

Phương pháp lấy 135 mốc cuối phút buổi sáng và 90 mốc buổi chiều theo cửa sổ liên tục HOSE. Tại mỗi mốc chỉ lấy sự kiện cuối không muộn hơn mốc đó. Yêu cầu đơn vị đã xác minh, sổ lệnh đủ ba mức hợp lệ mỗi phía, tuổi nguồn không quá 60 giây và đúng phiên. Không tìm lùi một bản hợp lệ để thay thế bản mới nhất bị lỗi. Giữ mọi điểm bị loại và lý do trong CSV; không nối dữ liệu qua nghỉ trưa hay ngày khác.

Các ngày được so sánh trên tập phút đủ điều kiện chung, có trọng số bằng nhau. Như vậy ngày có nhiều thông điệp hơn không tự được tính nặng hơn. Median, mức thấp nhất và tỷ lệ phút bao phủ đủ đều chỉ mô tả mẫu này; không phải xác suất khớp lệnh hay dự báo khủng hoảng. Thời điểm nhận khi tải sau phiên vẫn giữ riêng; đây là tái dựng hồi tố theo thời gian nguồn.

A1 dùng lệnh mua/bán giả định 10.000, 50.000, 150.000, 500.000 CP. Benchmark là giá tốt nhất, phí/thuế 0. Chi phí và VWAP chỉ tính phần được bao phủ. B5 giảm từng mức Bid 0%, 25%, 50%, 75%, làm tròn xuống CP, giữ nguyên giá và Ask. Báo cả median và coverage thấp nhất để tránh che khuất các phút yếu bằng median cao. Không coi chênh lệch proceeds giữa các kịch bản là lỗ danh mục.

## Chuẩn bị 10 mã để đối chiếu

```powershell
.\.venv\Scripts\python.exe -m scripts.dnse_unit_review prepare --date 2026-10-29
```

Chọn 4 HOSE, 3 HNX, 3 UPCOM theo số snapshot lưu trong ngày, loại ACB khỏi nhóm mới; ACB được thêm riêng làm kiểm soát. Đây là cách chọn mẫu để kiểm tra schema, không phải xếp hạng đầu tư. Gói riêng ở `data/control/dnse_unit_review_2026-10-29`, kế hoạch đọc tại `report/dnse_unit_review_plan_2026-10-29.md`.

Mỗi `MA.case.json` chứa thông điệp raw đọc lại từ trang gốc bằng lineage, kiểm tra event hash, thời gian nguồn/nhận và phạm vi mã/sàn/bảng. `MA.observation.json` để trống các quan sát độc lập, không chép số dự kiến vào trường bằng chứng. Lệnh từ chối ghi đè gói đã có để bảo vệ dữ liệu người dùng nhập; dùng `--output DUONG_DAN_MOI` nếu muốn tạo lượt khác.

Gói lịch sử chỉ hữu ích nếu có ảnh cùng thời điểm. Không lấy ảnh ngày khác để xác minh gói ngày 29/10. Khi chưa có ảnh phù hợp, thực hiện capture mới trong phiên.

## Capture mới và so sánh

Trong phiên, chụp bảng giá thể hiện mã, thời gian và đủ ba mức giá/khối lượng mỗi phía; thu raw gần thời điểm đó:

```powershell
.\.venv\Scripts\python.exe -m scripts.dnse_unit_review capture --symbol PVS
```

Lệnh chỉ GET `/price/PVS/quotes/latest?boardId=G1`, dùng khóa cục bộ hiện có; không gọi API tài khoản hoặc giao dịch. Ghi case và observation mới dưới `data/control/dnse_unit_review_captures`, kèm độ trễ nguồn. Capture sau giờ đóng cửa có thể là dữ liệu cũ, không được gọi là LIVE.

Nhập observation JSON từ ảnh, gồm:

- Nguồn bảng giá độc lập với thông điệp raw đang đối chiếu, đường dẫn ảnh và thời gian hiển thị có timezone, ví dụ `2026-10-30T10:15:23+07:00`.
- Đơn vị giá `VND` hoặc `thousand_VND`, đơn vị khối lượng `shares`. Chỉ khai báo shares sau khi đã đọc đúng chú thích bảng.
- Ba cặp `price,quantity` cho `bid` và `offer`, số khối lượng nguyên CP. Không dùng `totalBidQtty/totalOfferQtty` thay cho từng mức.

```powershell
.\.venv\Scripts\python.exe -m scripts.dnse_unit_review compare --case 'DUONG_DAN.case.json' --observation 'DUONG_DAN.observation.json' --output 'report/dnse_unit_comparison.json'
```

Đường dẫn ảnh tương đối được tính từ thư mục observation JSON. Bộ so sánh ghép theo bên và giá, không chỉ theo số thứ tự dòng. Kiểm tra sáu cặp với hệ số DNSE hiện được kiểm chứng cho ACB: giá ×1.000, lượng ×10. Ghi hash ảnh, hash case, sai lệch giờ và từng cặp khớp/không khớp. Chênh lệch giờ quá hai giây được gắn cần rà soát, kể cả khi các số bằng nhau; không tự cho rằng đồng hồ và độ trễ feed đã được xác minh.

`SIX_PAIRS_MATCH_MANUAL_REVIEW_REQUIRED` chỉ có nghĩa bản chép số khớp về toán học và nằm trong cửa sổ giờ đã đặt. Công cụ không tự chứng minh ảnh chân thực, độc lập hoặc được chép đúng. Phải rà soát ảnh và phạm vi mã/sàn/bảng trước khi lập bằng chứng thực nghiệm mới.

Không lệnh nào trong bộ review sửa `dnse_normalization_rules.json`, nâng nhãn đơn vị hoặc mở khóa A1/B5. Mở rộng phạm vi sau rà soát cần hồ sơ/bản chuẩn hóa phiên bản mới; không thay hash quy tắc cũ hay sửa nhãn hàng loạt trong kho đã audit. Không suy từ một mã sang toàn bộ thị trường.
