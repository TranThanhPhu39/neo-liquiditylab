# Sử dụng kho DNSE trong LiquidityLab

## Mở ứng dụng

```powershell
.\.venv\Scripts\python.exe -m streamlit run src/app/main.py --server.address 127.0.0.1
```

Mở http://localhost:8501, chọn **Nguồn dữ liệu → dnse**. Dữ liệu lấy từ `data/processed/dnse/normalized.db`; biến tùy chọn `DNSE_DATABASE_PATH` có thể trỏ tới kho khác. Không cần khóa API hay mạng để xem kho đã thu. Clone source public không có dữ liệu thật và bằng chứng riêng của máy này.

Chọn ngày dữ liệu nguồn (giờ Việt Nam), mã và màn hình. Kho hiện tại có ngày 28/10 và riêng phiên sáng 29/10/2026; không coi kho sáng là dữ liệu đủ cả ngày. Snapshot mặc định là bản cuối ngày đã lưu cho mã, có thể thuộc giai đoạn ngoài phiên liên tục. Muốn phân tích thời điểm khác, chọn **Replay lịch sử** rồi chọn **Đến hết phút nguồn**: 10:30 có nghĩa không muộn hơn 10:30:59.999999 giờ Việt Nam. Không nội suy, không lấy snapshot của ngày trước hoặc sau thời điểm đã chọn.

## Ba màn hình

- **Thanh khoản:** ba mức Bid/Ask, độ sâu quan sát, spread, OBI, biểu đồ độ sâu/spread theo thời gian và tải điểm mẫu. Trường thiếu giữ trống; API không trả bản ghi vẫn là NO DATA. Kết quả đối chiếu thủ công được hiển thị riêng nếu có.
- **Mô phỏng thực hiện lệnh (A1):** dùng snapshot đủ điều kiện, chọn chiều mua/bán và số cổ phiếu; hiển thị coverage, VWAP, chi phí, phần chưa bao phủ và đường cong theo quy mô lệnh. Cần bật lựa chọn phân tích dữ liệu lịch sử. Phí/thuế ở màn hình DNSE này cố định bằng 0 và được ghi rõ.
- **Stress danh mục (B5):** nhập danh mục giả định dưới dạng CSV `symbol,quantity`, điều chỉnh giảm độ sâu Bid, xem baseline/stressed và reverse stress. Mặc định ACB 150.000 CP. Chỉ truy vấn các mã trong danh mục; kiểm tra lệch thời gian giữa các mã. Mã chưa đủ điều kiện bị loại riêng, không tính vào mẫu số coverage hay tổng tiền. Phải bật lựa chọn phân tích lịch sử.

ACB/STO/G1 đã có đối chiếu thực nghiệm đơn vị. Các mã còn lại được hiển thị với `ASSUMED_SHARED_SCHEMA`; các giá trị đã nhân hệ số chung nhưng chưa xác minh trực tiếp. Chúng được xem sổ lệnh/biểu đồ, còn A1/B5 từ chối tính toán. Snapshot ngoài phiên liên tục hoặc không hợp lệ cũng bị chặn. Độ sâu tính từ các mức, không dùng tổng khối lượng nguồn đang có trường hợp trả 0 sai lệch.

Biểu đồ chọn snapshot cuối từng khoảng 144 giây, tối đa 600 điểm một mã/ngày đến thời gian đã chọn. Đây là mẫu đại diện, không phải toàn bộ thông điệp; có thể bỏ qua dao động ngắn. Số snapshot trong phần chất lượng là tổng kho của mã/ngày, không phải số điểm biểu đồ. File CSV tải từ màn hình cũng chỉ chứa các điểm mẫu đang xem.

## Cập nhật trọn quy trình

Sau 15:10 giờ Việt Nam để tải toàn bộ ngày:

```powershell
.\.venv\Scripts\python.exe -m scripts.update_dnse_day --date 2026-10-29
```

Sau 11:30 để tải riêng phiên sáng:

```powershell
.\.venv\Scripts\python.exe -m scripts.update_dnse_day --date 2026-10-29 --session morning
```

Lệnh kiểm tra hồ sơ đơn vị cục bộ trước khi thu, dùng khóa DNSE trong `.env`, thu toàn bộ cổ phiếu G1 theo danh mục nguồn, audit raw, xuất báo cáo mã thiếu, chuẩn hóa, audit từng bản ghi và kiểm tra mẫu tài chính. Mọi cuộc gọi DNSE chỉ đi qua bộ GET dữ liệu thị trường hiện có. Không gọi API giao dịch. Mặc định xuất thêm JSONL nén raw và chuẩn hóa; `--no-export` bỏ phần xuất lớn, vẫn giữ audit.

Kho raw hoàn tất được dùng lại mà không gọi mạng; kho dở tiếp tục theo checkpoint. Chuỗi dừng tại bước lỗi, ghi `report/dnse_update_YYYY-MM-DD_SESSION.json`; chạy lại cùng lệnh sau khi xử lý nguyên nhân. Audit chuẩn hóa kiểm tra toàn bộ các kho đã nhập, nên có thể lâu hơn lượt thu mới. Không mở thêm bộ thu/chuẩn hóa khác cùng lúc. Chờ báo cáo `COMPLETED`, sau đó tải lại Dashboard để thấy ngày và dữ liệu mới. Lệnh không khởi chạy Streamlit và không bật lịch Windows/GitHub.

Xem trước giờ được chạy và danh sách bước, không gọi API/ghi tệp:

```powershell
.\.venv\Scripts\python.exe -m scripts.update_dnse_day --date 2026-10-29 --plan
```

## Ý nghĩa Replay

Đây là tái dựng hồi tố theo thời gian nguồn từ dữ liệu đã tải sau phiên. Thời điểm thực nhận luôn hiển thị riêng, không đổi thành thời điểm nguồn. Không dùng kết quả này để tuyên bố hệ thống đã quan sát và ra quyết định ngay trong phiên. Replay thông thường của các nguồn cũ vẫn chặn thông điệp chưa nhận. NEO API chưa được tích hợp; DNSE không thay thế tiêu chí NEO bắt buộc của đồ án.
