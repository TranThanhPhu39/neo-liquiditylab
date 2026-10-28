# Thẩm định đơn vị Vnstock 5.2.1 / KBS · 28/10/2026

Kết luận thẩm định thực nghiệm cho quote cổ phiếu ACB, HPG, FPT: **giá SDK theo VND/CP, volume SDK theo CP; hai hệ số chuẩn hóa đều bằng 1**. Đây là kết luận đối chiếu bảng giá, không phải hợp đồng đơn vị do Vnstock phát hành.

Nguồn đơn vị hiển thị: [bảng KBS chính thức](https://kbbuddywts.kbsec.com.vn/) được công cụ tìm kiếm lập chỉ mục với chú thích “Giá x1,000 VNĐ; Khối lượng x10; Giá trị x1,000,000 VNĐ”. Bản lập chỉ mục không phải bảng giá live ngày 28/10. Người dùng chỉ nói *nghĩ* volume x10; ý kiến đó không được dùng làm tuyên bố đơn vị của nhà cung cấp.

Ảnh do người dùng gửi trong cuộc hội thoại có ba hàng mã và đầy đủ sáu mức. Người dùng cho biết chụp **tầm 14:30** ngày 28/10, không có timestamp chính xác hoặc chú thích đơn vị trong phần ảnh cắt. Không tạo timestamp chính xác cho ảnh. Bản chép tay nằm ở `kbs_board_transcription_2026-10-28.json`; ảnh gốc nằm trong cuộc hội thoại, không giả định đã lưu được bản pixel cục bộ.

SDK được gọi đúng một lần, nhận lúc **14:34:47.898765+07:00**, có version 5.2.1, route market.equity.quote, source kbs và đủ 16 trường. Bằng chứng gốc đã lọc thông tin nhạy cảm: `vnstock_board_comparison_2026-10-28.json`. Đây là mẫu chẩn đoán riêng sau cửa sổ collector, không cộng vào 66 quan sát lịch sử.

Đối chiếu theo **mã + bên + giá**, không ghép cứng thứ tự level vì giá mới có thể đẩy mức cũ sang vị trí khác. KBS hiển thị volume dạng nhóm chữ số có dấu phẩy: `2,00` chứa 200 đơn vị hiển thị, không đọc như 2,00 số thập phân. Nhân hệ số hiển thị 10 rồi so với SDK. Có **8/18 cặp khớp chính xác**: ACB Bid3 và Ask ở 21,35/21,40; HPG ba Ask; FPT Ask ở 54,20/54,40. Các cặp này bao gồm cả bid lẫn ask, ba mã và nhiều vị trí trong top 3.

| Mã / bên / giá hiển thị | Volume hiển thị | ×10 thành CP | SDK cùng giá |
|---|---:|---:|---:|
| ACB Bid / 21,05 | 236,40 | 236.400 | 236.400 |
| ACB Ask / 21,35 | 222,90 | 222.900 | 222.900 |
| ACB Ask / 21,40 | 257,10 | 257.100 | 257.100 |
| HPG Ask / 20,75 | 2,00 | 2.000 | 2.000 |
| HPG Ask / 20,80 | 1,00 | 1.000 | 1.000 |
| HPG Ask / 20,85 | 1,00 | 1.000 | 1.000 |
| FPT Ask / 54,20 | 10 | 100 | 100 |
| FPT Ask / 54,40 | 50,40 | 50.400 | 50.400 |

Những mức còn lại được lưu nguyên trạng với QUANTITY_DIFFERS hoặc PRICE_NOT_IN_SDK_TOP3 trong `kbs_sdk_comparison_2026-10-28.json`. Hai ảnh chụp thị trường cách nhau xấp xỉ 4 phút 48 giây nên không khẳng định các mức phải bằng nhau, không chỉnh volume để ép khớp và không chứng nhận hai nguồn đồng bộ. Các cặp trùng chính xác ở nhiều giá/mã/bên cùng chú thích của bảng KBS cung cấp cơ sở cho hệ số SDK bằng 1. Không lấy một cặp chênh lệch để suy ra hệ số khác.

Hồ sơ chỉ áp dụng vnstock 5.2.1 / kbs / quote cổ phiếu đã kiểm tra, không mở rộng sang OHLCV, nguồn khác, phái sinh, NEO hoặc bản SDK mới. Chuẩn hóa 66 quan sát giữ raw bất biến, giữ timestamp nhận ban đầu và source_timestamp null. Chưa chứng minh độ mới của quote hoặc phiên đầy đủ. Collector tự động mới không được bật chỉ nhờ hồ sơ này; quyền/quota và kiểm tra nguồn vẫn giữ riêng.
