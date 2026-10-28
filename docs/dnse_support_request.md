# Nội dung để người dùng hỏi hỗ trợ DNSE

**Trạng thái: bản soạn sẵn, chưa gửi. Không đính kèm API Key, API Secret hoặc file `.env`.**

Chào DNSE, tôi đang dùng LightSpeed API chỉ để đọc dữ liệu thị trường phục vụ dự án phân tích thanh khoản, không gọi API giao dịch.

Cùng bộ khóa, tôi gọi `GET https://openapi.dnse.com.vn/price/FPT/quotes/latest?boardId=G1` thành công từ máy cá nhân. Latest FPT/HPG/ACB và lịch sử quotes một phút của ngày 28/10/2026 đều đã trả dữ liệu trên máy.

Tôi muốn tự động thu bằng GitHub Actions standard hosted runner Ubuntu 24.04, Python 3.12. Hai lượt thử ngày **28/10/2026** thất bại trước khi nhận HTTP status:

- Lượt 1: 21:31–21:32 giờ Việt Nam, request đầu timeout/không có phản hồi HTTP; [log workflow](https://github.com/TranThanhPhu39/neo-liquiditylab/actions/runs/40706823793).
- Lượt 2: 21:36 giờ Việt Nam, DNS `openapi.dnse.com.vn` phân giải thành `103.151.242.24`, nhưng kết nối TCP tới cổng 443 timeout sau 5 giây, chưa tới TLS. Request API timeout 30 giây. [Log workflow](https://github.com/TranThanhPhu39/neo-liquiditylab/actions/runs/40707527626).
- Đối chứng từ máy cá nhân lúc 21:36:23 vẫn lấy được FPT. Không thay đổi khóa hoặc tắt xác minh TLS.

Nhờ DNSE hỗ trợ xác nhận:

1. REST OpenAPI có hỗ trợ truy cập từ GitHub-hosted runner/cloud không? Có giới hạn IP, vùng địa lý hoặc yêu cầu whitelist không?
2. Nếu cần cấu hình quyền mạng, quy trình chính thức là gì? Có endpoint dữ liệu thị trường chính thức nào dành cho môi trường này không?
3. Với REST quotes cổ phiếu bảng G1, đơn vị `bid/offer.price` và `bid/offer.quantity` chính xác là gì? Ví dụ FPT có `price=54`, `quantity=41910`: đơn vị giá là nghìn VND hay VND, và quantity là cổ phiếu hay bội số cổ phiếu?
4. Với nhu cầu đọc HOSE/HNX/UPCOM sau 11:30, lịch sử bid/ask được lưu bao lâu, `limit` tối đa bao nhiêu, và có điều kiện quyền dữ liệu/phí riêng ngoài hạn mức đã công bố không?

Cảm ơn DNSE.
