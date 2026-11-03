# Giới hạn hiện tại — 03/11/2026

- **DNSE là nguồn chính** theo [quyết định mới](data_source_decision.md). NEO VPBankS không thuộc kế hoạch triển khai tiếp. Người thực hiện xác nhận ngày 03/11 rằng việc dùng DNSE đã được thống nhất; thiếu NEO không còn là việc kỹ thuật phải hoàn thành.
- Kho DNSE có bốn ngày giao dịch, 3.746.982 snapshot duy nhất. Số thông điệp lớn không thay thế số ngày quan sát, chưa đủ cơ sở kết luận ổn định dài hạn hoặc xác suất rủi ro. Đề khuyến nghị ít nhất năm ngày, tốt hơn mười ngày trở lên cho phân tích thời gian.
- Giá DNSE ×1.000 về VND, lượng ×10 về CP. Chỉ ACB/STO/G1 có phạm vi xác minh thực nghiệm hiện tại; các mã khác giữ ASSUMED_SHARED_SCHEMA, A1/B5 mặc định bị chặn. Đổi nguồn chính không thay bằng chứng đơn vị.
- Hết token chỉ xác nhận đọc hết trang API cung cấp trong cửa sổ yêu cầu, không chứng nhận độc lập upstream đầy đủ. API trả rỗng khác book 0; ảnh tại một thời điểm không thay lịch sử cả ngày.
- DNSE tải sau phiên; Replay theo thời gian nguồn là tái dựng hồi tố, giữ thời điểm nhận thật. Không chứng minh đã quan sát/ra quyết định trong phiên hoặc thay backtest theo arrival.
- Chỉ ba mức giá; chưa mô phỏng hàng đợi, bổ sung/hủy lệnh, tác động động, độ trễ thực thi, quy tắc lô/bước giá/trần/sàn để gửi lệnh, thanh toán hoặc số dư thật. Phần vượt depth không tự định giá.
- A1/B5 là kết quả có điều kiện theo snapshot/kịch bản. Không bảo đảm khớp lệnh, không dự báo thời gian thanh lý/xác suất khủng hoảng; không VaR/CVaR/ML.
- Phân loại phiên DNSE theo giờ từng sàn và ngày thường chưa thay lịch nghỉ/trạng thái mở cửa chính thức. Quote ngoài phiên liên tục không tự đủ điều kiện A1/B5.
- REST có giới hạn nhịp và xử lý lỗi; quyền/hạn mức phải theo nhà cung cấp khi vận hành. Chưa điều phối quota nhiều máy; WebSocket đã có probe auth/quotes MSR trên hai runner; chưa có collector dài phiên/reconnect/khôi phục hoặc xác minh đơn vị WS.
- SQLite là kho local MVP, hiện khoảng 6,58 GB. Dashboard DNSE đã truy vấn theo chỉ mục mã/ngày/cutoff, mặc định tối đa 600 điểm biểu đồ. Audit/export và phân tích tổng thể vẫn quét dữ liệu lớn, cần theo dõi dung lượng/thời gian khi thu dài ngày.
- Có task Windows, GitHub workflow và systemd chuẩn bị cho nguồn cũ; Windows Disabled, GitHub worker false, chưa có VM triển khai. Chưa có lịch thu DNSE tự chạy hoạt động. Thử GitHub trước đó timeout TCP, chưa xác nhận nguyên nhân chính sách IP.
- Dữ liệu thực lưu trên máy. Raw và bản xuất cùng máy không thay bản sao ngoài máy; chưa có quy trình backup/restore ngoài máy được nghiệm thu cho kho DNSE hiện tại.
- Vnstock/KBS là kho cũ riêng: 66 snapshot chiều 28/10 của ACB/FPT/HPG, hệ số 1/1 theo hồ sơ nguồn đó. Không dùng hồ sơ này xác minh DNSE. Demo synthetic không tính thành ngày dữ liệu thật.
- Portfolio là simulated holdings; mã bị loại được báo riêng. Coverage phần tính được không đại diện toàn danh mục nếu có exclusion; tổng CP các mã không tương đương rủi ro theo giá trị.
- Lần hồi quy gần nhất ghi nhận 197 tests đạt ngày 03/11; AppTest 02/11 đạt ba màn hình. Chưa có nghiệm thu trực quan đầy đủ bằng trình duyệt hoặc audit bảo mật production.
- HTML phân tích hiện chỉ dùng 28–29/10; thêm dữ liệu vào kho không tự đổi các artifact cố định này. Báo cáo cuối, ảnh demo và slide chưa hoàn thiện.

Xem [chốt ngày gần nhất](../report/dnse_day_completion_2026-11-02.md), [kiểm thử](../report/test_results.md), [phạm vi chuẩn hóa](dnse_normalization.md).



