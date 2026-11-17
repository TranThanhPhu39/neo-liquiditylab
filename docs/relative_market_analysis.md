# Phân tích tương đối cho sổ lệnh toàn thị trường

Mã chưa xác minh đơn vị tuyệt đối vẫn có thể được phân tích theo tỷ lệ, với điều kiện hệ số giá và lượng của cùng trường giữ nguyên trong một chuỗi. Chế độ này không cấp chứng nhận đơn vị hoặc quyền tính toán tài chính.

## Hai loại sự kiện được mở

- **D1 — Spread mở rộng:** `(ask1 - bid1) / ((ask1 + bid1) / 2) × 10.000` bps, so với baseline chỉ gồm các quan sát quá khứ.
- **D2 — Lượng chờ giảm:** so tỷ lệ lượng tại các mức giá cùng còn trong top 3 với book tham chiếu đã khóa. Giá ra khỏi phạm vi quan sát không được coi là lượng bị hủy hoặc về 0.

Nếu giá cùng nhân hệ số dương k, k triệt tiêu ở D1. Nếu lượng cùng nhân hệ số dương h, h triệt tiêu ở D2. Kiểm thử thay đổi đồng thời hệ số giá/lượng phải giữ quyết định phát hiện và thời điểm phát hiện. Điều này không chứng minh hệ số của nguồn thực tế đã được xác minh hoặc không bao giờ thay đổi.

**D3 vẫn khóa**, vì cấu hình hiện tại có điều kiện khối lượng tối thiểu tuyệt đối. A1/B5, tính tiền và diễn giải CP tuyệt đối không được cấp quyền mới. Những mã đã có replay xác minh ACB/FPT/HPG giữ luồng cũ.

## Điều kiện dữ liệu

Chỉ áp dụng cho dữ liệu DNSE G1 theo schema hiện có, giữ nguyên hash quy tắc. Cần đủ ba mức mỗi phía, giá dương đúng thứ tự, lượng hợp lệ, best bid nhỏ hơn best ask và có lượng best quote dương. Ngoài phiên liên tục, dữ liệu invalid, thiếu mức, sai thứ tự nguồn hoặc khoảng ngắt làm gián đoạn/reset thuật toán. Thiếu mẫu baseline thì hiển thị warmup, không tạo sự kiện giả.

Kết quả vẫn dùng thời gian nguồn hồi tố. Không chứng nhận độ đầy đủ cả phiên, trạng thái liên tục giữa snapshot, giao dịch khớp, ý định đặt lệnh, hủy lệnh hoặc thao túng. Không có sự kiện không đồng nghĩa thị trường bình thường.

## Sử dụng

Mở dashboard, chọn ngày → tìm mã → Mở mã. Với mã ngoài phạm vi xác minh tuyệt đối:

- Cảnh báo ghi rõ chế độ **D1/D2 tương đối**.
- Số sự kiện tại con trỏ được hiển thị; sự kiện tương lai vẫn bị ẩn.
- Chọn sự kiện để xem ngưỡng, tỷ lệ và book trước/sau. Giá/lượng quy đổi chưa xác minh phải đọc cùng cảnh báo.
- D3 ghi rõ lý do khóa. Chỉnh cấu hình cho mã toàn thị trường vẫn khóa; không tạo một phiên phân tích tuyệt đối bằng cách đổi tham số.
- Thư viện lưu các sự kiện tương đối đã có nhận xét; không tự chạy quét toàn bộ 1.500 mã ở mỗi lần mở thư viện.

Backend tạo ID phân tích mới gắn phiên bản chế độ tương đối, giữ `source_dataset_id` để truy vết. Không sửa raw hoặc Parquet gốc, không đổi `unit_verification_status`. Trường `features.eligible` vẫn false; `relative_eligible` chỉ dành cho D1/D2.

API phục vụ theo hai lượt đọc: tính metadata/bằng chứng với bộ nhớ giới hạn, sau đó truyền frame. Không phải giữ thêm một bản đầy đủ của mọi frame trong RAM khi chạy trên Render. Nếu một mã/ngày có hơn 25.000 quan sát hoặc vượt ngân sách 30.000 patch sự kiện, vẫn phục vụ mọi quan sát ở chế độ chỉ xem, ghi rõ `RESOURCE_LIMIT_OBSERVATION_FALLBACK` và không đưa ra kết luận từ một tập sự kiện bị cắt. Giới hạn này áp dụng tính sự kiện tương đối trên dịch vụ Free, không phải lấy mẫu hoặc giới hạn lưu dữ liệu. Ví dụ VIX 78.400 quan sát vẫn xem đủ nhưng chưa chạy D1/D2 trong yêu cầu web này.

## Vận hành

Đây là thay đổi source backend/frontend, không yêu cầu upload lại lịch sử thị trường hoặc đưa broker key lên máy chủ web. Luồng xuất bản ngày mới tiếp tục dùng Parquet đã chuẩn hóa/audit. Bộ kiểm tra xuất bản chấp nhận cả chế độ tương đối và chỉ quan sát, luôn yêu cầu `features.eligible=false` ở luồng toàn thị trường.

Xác minh đơn vị độc lập tiếp tục theo [quy trình bằng chứng](market_unit_verification.md). Mở chế độ tương đối không hoàn thành thay cho bước xác minh đó.
