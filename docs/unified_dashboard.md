# Dashboard chung: lịch sử và theo dõi gần nhất

Từ 20/11/2026, dùng một giao diện tại **https://neo-liquiditylab-monitor.onrender.com/dashboard**. Không còn mục điều hướng DNSE/SSI hoặc trang phân tích riêng theo nhà cung cấp. Người xem không cần đăng nhập; quyền ghi và ghi chú riêng giữ nguyên bảo vệ.

## Cách dùng

1. Chọn **Lịch sử** để xem các ngày đã tải và chuẩn hóa. Chọn ngày, sàn, mã rồi đọc heatmap, bubbles, sổ lệnh, replay và bằng chứng. Danh mục lịch sử vẫn bao gồm toàn thị trường theo dữ liệu thực có.
2. Chọn **Theo dõi gần nhất** để mở bản thu và tiếp tục cập nhật khi có bản tin. Cùng biểu đồ, bộ lọc sự kiện, bảng sổ lệnh và thanh tua được sử dụng. Mã đang chọn được giữ; mã chưa có bản thu sẽ được báo thiếu, không tự đổi sang ACB hoặc thay bằng số 0. Phạm vi bộ thu hiện vẫn là ACB/FPT/HPG.
3. Chọn ngày và **Bản thu** để xem đợt thu khác. **Tạm dừng màn hình** hoặc kéo thanh tua chỉ đổi con trỏ; không dừng collector. **Theo dõi mới nhất** quay về đợt thu gần nhất và bật cập nhật màn hình.
4. Bản thu được tải theo đoạn: ban đầu tối đa 2.000–5.000 quan sát tùy trạng thái lưu; bộ đệm trình duyệt giữ tối đa 15.000. Dùng **Đoạn trước / Đoạn sau** để tải tiếp phần đã thu. Thanh tua và giao dịch trên biểu đồ chỉ thuộc đoạn đang tải, không phải toàn bộ phiên.

## Ý nghĩa trạng thái và dữ liệu

- **Đang cập nhật** chỉ hiện khi đúng bản thu hiện tại, bộ thu còn hoạt động và sổ lệnh của chính mã đang xem còn mới trong 15 giây. Đứt relay, dữ liệu cũ hoặc đang xem lại không được gắn nhãn này.
- Bảng sổ lệnh giữ các mức và đơn vị mà nguồn trả. Hai chỉ số depth vẫn là top 3 theo schema phân tích. Không tự nâng nhãn đơn vị hoặc mở khóa detector khi đổi chế độ.
- Lịch sử dùng thời gian nguồn; bản thu dùng thứ tự/thời gian nhận. Thời gian nguồn gốc, đơn vị và tên nhà cung cấp nằm trong **Nguồn, chất lượng và phạm vi** để đối chiếu. Hai chuỗi không được nối thành một lịch sử giả hoặc cộng trùng giao dịch.
- Sự kiện và peak sau con trỏ được ẩn. Trong phát lại bản thu, kiểm tra sự kiện được cập nhật theo đợt; giao diện ghi mốc đã kiểm tra. Hồ sơ JSON bản thu chỉ xuất đến con trỏ nhận. Bubbles chỉ vẽ giao dịch khớp riêng, không suy ra từ thay đổi lượng chờ.
- Trong chế độ bản thu, bảng theo dõi thể hiện mã đang chọn tại con trỏ; kiểm kê liệt kê các mã của bản thu. Thư viện hiển thị sự kiện đã kiểm tra của bản thu đến con trỏ. Đổi sang Lịch sử để xem bảng thị trường cùng cutoff và thư viện hồi tố cả phiên.

Link `/realtime` cũ chuyển về `/dashboard?view=capture`, sử dụng cùng HTML và bộ điều khiển. Collector, API thu dữ liệu, lịch GitHub/Windows, chuẩn hóa và xuất Parquet không thay đổi.

## Khi máy chủ tạm thời chưa sẵn sàng

Nếu backend đang phục hồi sau khởi động hoặc có truy vấn khác đang chiếm tài nguyên, trang hiện thông báo đang chờ và tự thử lại có giới hạn. Không cần bấm liên tục “Mở mã” hoặc tải lại thư viện. Nếu hết thời gian chờ, thông báo phân biệt máy chủ chưa phục hồi, đang bận, lỗi kết nối và kho ghi chú; dữ liệu đã tải vẫn giữ nguyên. Yêu cầu lưu/ghi không được tự gửi lại, tránh lưu trùng khi chưa rõ kết quả.

Đây là xử lý trạng thái phục vụ dữ liệu; không tự biến dữ liệu trễ hoặc thiếu thành dữ liệu đầy đủ. Giới hạn xử lý và checkpoint trên backend không ảnh hưởng bản Parquet gốc hoặc lịch thu.

Backend không giữ nhiều bản sao JSON của toàn danh mục khi trả danh sách mã. Danh mục HTTP chỉ chứa các trường điều hướng và nhãn đơn vị; provenance đầy đủ vẫn có khi mở bộ dữ liệu. Cửa sổ quote RAM của relay giữ 2.000 quan sát/mã, phần đã ra khỏi cửa sổ vẫn được đọc lại từ Parquet bằng Đoạn trước/Đoạn sau. Giới hạn bộ đệm giao dịch khớp không đổi. Giải mã checkpoint và đọc Parquet theo luồng giảm đỉnh RAM, vẫn kiểm tra AES-GCM, hash, số hàng, thứ tự và cutoff.

## Kiểm tra kỹ thuật

`node --test tests/js/*.test.cjs` kiểm tra chuyển chế độ, giữ mã, xử lý mã chưa thu, bỏ phản hồi cũ sau khi đổi chế độ, giới hạn đơn vị, clock và bằng chứng sau con trỏ. Bộ HTTP kiểm tra link cũ chuyển hướng, asset allowlist, quyền xem công khai và quyền ghi riêng. `python -m scripts.verify_unified_monitor_browser` mở hồ sơ trình duyệt mới, không cung cấp mật khẩu và chặn mọi request ghi để kiểm tra hai chế độ trên cùng URL, desktop và mobile.
