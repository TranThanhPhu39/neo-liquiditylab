# Hướng dẫn sử dụng dashboard LiquidityLab

Đối chiếu mã giao diện và dữ liệu thực ngày 05/11/2026. Dashboard có ba chức năng: quan sát thanh khoản ba mức, mô phỏng lệnh A1, stress thanh khoản B5. Các thao tác mô phỏng không gửi lệnh mua/bán hoặc truy vấn tài sản tài khoản.

## 1. Mở dashboard

| Cách mở | Dữ liệu tại lần kiểm tra 05/11 | Thao tác |
| --- | --- | --- |
| [GitHub Pages](https://tranthanhphu39.github.io/neo-liquiditylab/) | Bộ lịch sử công khai đến 04/11 | Mở link, đợi bộ tính và dữ liệu tải xong |
| Streamlit trên máy | Kho chuẩn hóa đến 05/11, có sáng và chiều | Chạy lệnh bên dưới, chọn nguồn DNSE |
| Web + Mở tệp riêng | Tệp ngày 05/11 trên máy | Chọn `data/exports/dashboard/dnse_2026-11-05.json` |

Trong PowerShell tại thư mục dự án:

```powershell
.\.venv\Scripts\python.exe -m streamlit run src/app/main.py --server.address 127.0.0.1
```

Mở http://localhost:8501, giữ cửa sổ chạy lệnh khi sử dụng. Chọn **Nguồn dữ liệu → DNSE · Lịch sử**. Xem kho đã có không gọi API DNSE; không nhập khóa vào dashboard. Web cần mạng để tải ứng dụng và dữ liệu đã công bố.

## 2. Chọn ngày, mã, giờ

Trên Streamlit: chọn **Replay lịch sử**, ngày **05/11/2026**, mã **ACB**, **Đến hết phút nguồn = 14:20**. Trên web, ba bộ chọn nằm phía trên cùng; dữ liệu public hiện mới nhất 04/11.

14:20 nghĩa là chọn snapshot cuối không muộn hơn **14:20:59.999999 giờ Việt Nam**. Đọc thời gian nguồn thực tế, không mặc định snapshot đúng 14:20:00. Không có điểm trước mốc chọn thì báo NO DATA, không lấy điểm tương lai.

Snapshot cuối ngày có thể thuộc ngoài phiên liên tục và bị khóa A1/B5. Để tập thao tác, chọn ACB và mốc 14:20.

Tệp riêng ngày 05/11 vừa xuất chỉ có một snapshot đại diện mỗi mã trước 14:20:59. Đổi về 10:30 trên tệp này có thể thấy NO DATA vì chưa nhập lịch sử sáng. Muốn chọn giờ linh hoạt, dùng Replay Streamlit hoặc xuất tệp cho mốc khác:

```powershell
.\.venv\Scripts\python.exe -m scripts.export_dashboard --date 2026-11-05 --at 10:30 --output data/exports/dashboard/dnse_2026-11-05_1030.json
```

Muốn thêm lịch sử ACB để xem biểu đồ trên web:

```powershell
.\.venv\Scripts\python.exe -m scripts.export_dashboard --date 2026-11-05 --at 14:20 --symbols ACB --samples 300 --output data/exports/dashboard/dnse_2026-11-05_acb_history.json
```

Bấm **Mở tệp riêng** để nhập JSON. Nội dung chỉ xử lý trong tab, không upload; tải lại trang trở về bộ dữ liệu public. Không chọn `.db`, `.env` hoặc raw API.

## 3. Thanh khoản

| Nội dung | Cách đọc |
| --- | --- |
| Bid 1–3 | Chờ mua; lệnh bán giả định quét từ Bid 1 |
| Ask 1–3 | Chờ bán; lệnh mua giả định quét từ Ask 1 |
| Bid/Ask depth 3 | Tổng CP ở ba mức quan sát của mỗi bên, không phải khối lượng đã khớp hoặc toàn bộ thị trường |
| Spread | Ask 1 trừ Bid 1, đơn vị VND/CP |
| Relative spread | Spread chia giá giữa Bid/Ask; 0,0024 tương đương khoảng 0,24% |
| OBI | (Bid depth − Ask depth)/(Bid depth + Ask depth), khi đủ dữ liệu; dương là Bid lớn hơn Ask trong ba mức |
| Độ sâu tích lũy | Cộng khối lượng từ mức 1 đến mức 3 |
| Lịch sử | Các điểm mẫu đã tải, không phải mọi thông điệp trong phiên |

Giá hiển thị VND/CP, khối lượng CP: không nhân thêm 1.000 hoặc 10. OBI mô tả tương quan sổ lệnh tại snapshot, không dự báo giá hay bảo đảm khớp.

Web public có mốc 11:29/14:20/15:10 và điểm cuối mỗi khoảng 6 phút. Streamlit chọn snapshot trực tiếp từ kho; biểu đồ lấy mẫu tối đa 600 điểm/ngày, khoảng 144 giây một điểm. Đường vẽ giữa hai điểm không chứng minh đã quan sát đủ khoảng đó.

## 4. Mô phỏng A1

1. Chọn ACB và thời điểm đủ điều kiện.
2. Mở **Mô phỏng lệnh · A1**.
3. Bật **Cho phép phân tích snapshot lịch sử**; Streamlit thêm `(A1)` vào nhãn.
4. Chọn **Bán · Quét Bid** hoặc **Mua · Quét Ask**.
5. Nhập số CP nguyên dương, thử 150000 rồi tăng lên 1000000 để so sánh.

Web cho chọn benchmark giá tốt nhất cùng bên hoặc giá giữa Bid/Ask, phí/thuế giả định. Màn hình DNSE của Streamlit hiện cố định benchmark giá tốt nhất cùng bên, phí và thuế 0. Đây là cấu hình mô hình, không phải bảng phí thực tế của công ty chứng khoán.

| Kết quả | Ý nghĩa |
| --- | --- |
| Coverage/Bao phủ | Số CP phân bổ được trong ba mức / số CP yêu cầu |
| Chưa bao phủ | Phần chưa có giá để mô phỏng, không tự định giá ở mức 4 |
| VWAP phần bao phủ | Giá bình quân gia quyền của phần phân bổ được |
| Chi phí so benchmark | Chênh lệch so với giá chuẩn, chỉ trên phần được bao phủ |
| Gross/Net | Giá trị phần bao phủ; khi bán Net trừ phí/thuế, khi mua Net cộng phí |
| bps | 1 bps = 0,01%, tính theo benchmark của phần được bao phủ |

Ví dụ thực trong tệp 05/11: ACB có thời gian nguồn **14:20:57.936**, Bid depth **867.600 CP**, Ask depth **379.900 CP**. Với bán 150.000 CP, benchmark Bid tốt nhất, phí/thuế 0: coverage **100%**, VWAP **20.500 VND/CP**, chi phí quét sổ **0 VND**. Số 0 là kết quả cấu hình này; không có nghĩa giao dịch thực miễn phí hoặc chắc chắn khớp.

## 5. Stress danh mục B5

Mở **Stress danh mục · B5**, nhập danh mục giả định:

```csv
symbol,quantity
ACB,1000000
```

Bật cho phép phân tích lịch sử, đặt **Giảm độ sâu mua/Bid = 50%**. Baseline dùng sổ gốc; Stressed giảm khối lượng từng mức Bid còn 50%, làm tròn xuống CP nguyên, giữ giá trong kịch bản này.

Với snapshot ACB 05/11 lúc 14:20:57.936 nói trên, coverage bán giả định 1.000.000 CP là **86,76%** ở baseline và **43,38%** sau stress 50%. Đây là coverage trong độ sâu quan sát, không phải tỷ lệ vốn hay xác suất bán được.

Đọc **Phạm vi tính toán & loại trừ**. Mã thiếu dữ liệu, chưa xác minh đơn vị, ngoài phiên hoặc lệch timestamp quá giới hạn có thể bị loại. Coverage/tổng tiền chỉ tính phần đủ điều kiện; không hiểu coverage ACB là coverage toàn bộ các mã đã nhập. Hiện hồ sơ DNSE chỉ xác minh độc lập ACB/STO/G1.

Chi phí tuyệt đối có thể giảm do phần khối lượng được bao phủ giảm. Luôn đọc coverage, chưa bao phủ và bị loại cùng với chi phí.

## 6. Reverse stress

Trong B5, chọn mã ở bộ chọn phía trên. Nhập **Coverage tối thiểu**, ví dụ 70%, số lượng giả định, bấm **Tìm ngưỡng suy giảm**. Streamlit đặt chức năng trong phần mở rộng **Reverse stress trên mã đang chọn**.

Kết quả là mức giảm Bid nhỏ nhất trên lưới 0,01%, trong khoảng 0–90%, làm coverage **thấp hơn** ngưỡng. Chức năng áp dụng cho **mã đang chọn**, không tự tính reverse stress toàn danh mục. 0% nghĩa baseline đã dưới ngưỡng; không tìm thấy nghĩa chưa rơi dưới ngưỡng trong phạm vi thử. Không suy ra xác suất hoặc thời gian xảy ra kịch bản.

## 7. Nhãn và tình huống thường gặp

| Nhãn/tình huống | Cách xử lý |
| --- | --- |
| NO DATA | Kiểm tra ngày/giờ/tệp; có thể không có điểm trước mốc chọn. Không coi là số 0 |
| ASSUMED_SHARED_SCHEMA | Quy đổi theo schema chung, chưa xác minh trực tiếp mã; xem mô tả, A1/B5 bị chặn |
| EMPIRICALLY_VERIFIED | Có bằng chứng đơn vị; vẫn cần đủ chất lượng và phiên để tính |
| Ngoài phiên/Chưa đủ điều kiện | Thử ACB tại mốc phiên liên tục có dữ liệu, ví dụ 14:20 |
| A1/B5 chưa có kết quả | Kiểm tra ô cho phép lịch sử, mã, thời điểm và chất lượng |
| Web không có 05/11 | Tải lại chỉ tìm ngày đã công bố; mở tệp riêng hoặc dùng Streamlit |
| Danh mục nhiều mã bị loại | Đọc lý do từng mã và độ lệch timestamp; không thay thiếu bằng 0 |
| Biểu đồ tệp riêng chỉ có một điểm | Tệp mặc định theo một mốc; xuất thêm `--samples` hoặc dùng Streamlit |

## 8. Xuất và cập nhật

Web có **Xuất snapshot** cho mã đang chọn. Streamlit có tải JSON snapshot và CSV các điểm mẫu đang xem; CSV biểu đồ không phải toàn bộ raw.

Sau 15:10 ngày giao dịch, cập nhật trên máy:

```powershell
.\.venv\Scripts\python.exe -m scripts.update_dnse_day
```

Đợi COMPLETED rồi tải lại Streamlit để chọn ngày mới. Dashboard không gọi API khi đổi ngày/giờ. Nút **Tải lại dữ liệu** trên web chỉ tìm dữ liệu đã công bố; GitHub collection, local và Pages là các luồng riêng. Xem [README vận hành](../README.md).
