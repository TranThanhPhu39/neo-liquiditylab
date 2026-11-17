# Mở rộng xác minh đơn vị theo nhóm mã

Web hiện có bằng chứng được duyệt cho ACB/FPT/HPG. Các mã toàn thị trường khác chưa được cấp quyền về đơn vị tuyệt đối; có thể dùng [D1/D2 tương đối](relative_market_analysis.md) khi đủ đầu vào. Không cấp nhãn xác minh chỉ vì cùng schema, cùng sàn hoặc có giá trông hợp lý.

Đã bổ sung bộ đối chiếu độc lập theo mã/sàn/ngày, đọc trực tiếp Parquet. Bộ này tạo hồ sơ số liệu để duyệt, chưa thay đổi contract hoặc bật detector. Cần hoàn tất việc duyệt nguồn/phạm vi và dựng lại replay trước khi mở chức năng trên web cho mã mới.

## Thu bằng chứng

Trong giờ giao dịch, khi máy có thể mở bảng giá Maybank:

```powershell
.\.venv\Scripts\python.exe -m scripts.capture_public_unit_reference --symbols VNM,VCB,SSI,VIX
```

Lệnh dùng hồ sơ trình duyệt tạm riêng, đọc bảng giá công khai, không cần tài khoản giao dịch. Tối đa 50 mã mỗi lượt. Chỉ lưu dòng thực sự thấy trên bảng đang mở; không tự chọn mọi bảng/sàn, không cam kết mọi mã được yêu cầu đều xuất hiện. Tệp `observations.json` ghi riêng `requested_symbols`, `captured_symbols`, `missing_symbols`. Không chuyển dòng không tìm thấy thành sổ lệnh bằng 0.

Đường dẫn tệp thu được xuất ra dưới `data/raw/ssi/maybank_reference_.../observations.json`. Cần giữ chú thích đơn vị và thời gian nhận mẫu. Bộ đọc chấp nhận nhãn sàn HO/HSX/HOSE, HA/HN/HNX, UP/UPCOM nhưng từng sàn phải có chính dòng bằng chứng của sàn đó. Không dùng đối chiếu HOSE để cấp quyền HNX/UPCOM. Dữ liệu bảng giá/DOM và bố cục cột vẫn cần được rà soát trước khi duyệt hồ sơ.

Không thực hiện thu mới trong đợt kiểm tra tối 17/11 vì ngoài giờ giao dịch, không thể dựa vào đó để tạo hai trạng thái mới của một phiên đã qua.

## Đối chiếu với ngày đã chuẩn hóa

Sau khi ngày tương ứng đã được chuẩn hóa, audit và có trong catalog Parquet:

```powershell
.\.venv\Scripts\python.exe -m scripts.audit_market_units --day 2026-11-16 --reference data/raw/ssi/maybank_reference_20261116_061227/observations.json
```

Mặc định kiểm tra toàn bộ danh mục của ngày, cửa sổ 5 giây. Có thể dùng `--symbols VNM,SHS,A32` để giới hạn nhóm hoặc lặp `--reference` để kết hợp nhiều lần chụp. Cửa sổ 20 giây chỉ phục vụ đối chiếu đơn vị với bảng có độ trễ:

```powershell
.\.venv\Scripts\python.exe -m scripts.audit_market_units --day 2026-11-16 --reference data/raw/ssi/maybank_reference_20261116_061227/observations.json --window-seconds 20
```

Không gọi kết quả 20 giây là xác nhận hai nguồn đồng bộ realtime. Không tự nới cửa sổ lớn hơn để ép khớp.

## Điều kiện và kết quả

- Chú thích bảng giá phải ghi rõ giá nghìn đồng và lượng một cổ phiếu.
- Đủ sáu mức mua/bán, giá hữu hạn dương, khối lượng nguyên không âm; hai phía có lượng dương và mức giá đúng thứ tự.
- Cùng mã, sàn, G1, ngày nguồn và phiên bản DNSE 2026-07-23.
- Đủ hai trạng thái sổ lệnh khác nhau, hai observation ID khác nhau; lặp ảnh hoặc lặp cùng book không tạo thêm bằng chứng.
- Cùng hash quy tắc chuẩn hóa. Dữ liệu invalid, thiếu mức, khác sàn/ngày hoặc sai tỷ lệ không đạt.

| Trạng thái | Ý nghĩa |
| --- | --- |
| REFERENCE_NOT_CAPTURED | Chưa có mẫu độc lập đúng mã/sàn/ngày |
| NO_ALIGNED_NUMERIC_MATCH | Có mẫu nhưng chưa khớp sáu mức trong cửa sổ |
| INSUFFICIENT_DISTINCT_BOOKS | Khớp được một trạng thái, cần thêm trạng thái khác |
| MIXED_UNIT_RULES_REVIEW_REQUIRED | Mẫu liên quan nhiều hash quy tắc, cần duyệt phạm vi |
| MATCHED_PENDING_REVIEW | Đủ điều kiện so sánh số học; chờ duyệt nguồn/bố cục/phạm vi |

Trạng thái thiếu mẫu không có nghĩa dữ liệu sai. `MATCHED_PENDING_REVIEW` cũng không có nghĩa mọi ngày lịch sử, giao dịch khớp, độ đầy đủ nguồn hoặc hiệu quả detector đã được chứng nhận. Việc cấp contract mới và mở detector là bước riêng; không sửa `unit_verification_status` trực tiếp bằng tay.

## Kết quả lần chạy 17/11

Đối chiếu 1.517 mã ngày 16/11 với bộ Maybank đã thu ngày 16/11, cửa sổ 20 giây:

- 1.514 mã chưa có mẫu độc lập trong bộ tệp này.
- FPT/HPG: mỗi mã hai trạng thái khớp, chờ duyệt theo bộ kiểm tra tổng quát mới; đây là hai mã đã có contract được duyệt trước đó, không phải hai mã mới được mở.
- ACB: một trạng thái trong riêng bộ tệp này. ACB vẫn giữ bằng chứng/contract được duyệt từ những bộ trước; kết quả này không thu hồi xác minh hiện tại.
- Không thay đổi raw, Parquet, contract hoặc web. Không có mã mới được mở detector trong lần chạy này.

Báo cáo: `report/market_unit_audit_2026-11-16_20s.json`. Kiểm thử liên quan: 24/24 đạt (bộ đối chiếu mới, review và revision hiện có).

Nguồn tham chiếu: [chú thích đơn vị Maybank](https://priceboard.maybank-kimeng.com.vn/), [DNSE lịch sử bid/ask](https://developers.dnse.com.vn/docs/dnse/get-quotes/). Không thấy đủ cơ sở từ trang endpoint đã đọc để tự cấp xác minh đơn vị cho mọi mã.
