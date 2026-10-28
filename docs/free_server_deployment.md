# Chạy bộ thu trên máy chủ miễn phí

Cập nhật: người dùng đã chọn ưu tiên **GitHub Actions với repo public riêng**, theo mô hình dự án crypto hiện có. Xem [quy trình GitHub](github_actions.md). Các hướng dẫn Oracle/systemd bên dưới giữ vai trò phương án thay thế; chưa yêu cầu người dùng tạo VPS.

Trạng thái: **đã chuẩn bị mã và systemd templates; chưa có tài khoản/VM, chưa triển khai hoặc kiểm thử runtime Linux**. Lịch Windows hiện tại vẫn giữ nguyên. Không tạo tài nguyên trả phí, không upload `.env` hoặc đăng nhập cloud thay người dùng.

## Lựa chọn phù hợp

Ưu tiên một VM cố định thuộc **Oracle Cloud Always Free** với Ubuntu 24.04 LTS/Python 3.12, ban đầu khoảng 1 OCPU / 2 GiB RAM và đủ ổ đĩa trong phần miễn phí được console xác nhận. Đây là cấu hình đề xuất để thử, chưa là benchmark tài nguyên. [Oracle mô tả](https://docs.oracle.com/en-us/iaas/Content/FreeTier/freetier_topic-Always_Free_Resources.htm) khả năng hết tài nguyên theo vùng và thu hồi VM ít hoạt động; vì vậy không bảo đảm liên tục hoặc luôn tạo được máy. [Tài khoản Free Tier](https://docs.oracle.com/en-us/iaas/Content/FreeTier/freetier.htm) thường cần số điện thoại và thẻ để xác minh; không nâng lên trả phí để vượt giới hạn. Chọn tài nguyên có nhãn Always Free và kiểm tra tổng quota trên chính tài khoản, không dùng nhầm credit trial cho máy sẽ hết miễn phí.

[Vnstock hỗ trợ Linux x86_64/aarch64](https://vnstocks.com/docs/vnstock/cai-dat), nhưng cần kiểm tra wheel 5.2.1 cài thành công trên VM thực trước khi bật lịch. [Cấp Cộng đồng chỉ có một thiết bị](https://vnstocks.com/docs/vnstock/tham-khao/cap-quyen-va-gioi-han): khi chuyển nguồn thu lên VM, dừng lịch và việc gọi SDK trên máy Windows, thực hiện chuyển thiết bị theo cơ chế chính thức của Vnstock. Không xóa/dựng lại VM hoặc nhân bản state để né giới hạn thiết bị.

Khi dùng GitHub Actions cần kiểm chứng [lịch có thể bị trễ hoặc bỏ lượt](https://docs.github.com/en/actions/how-tos/troubleshoot-workflows), giới hạn thiết bị Vnstock và khả năng khôi phục checkpoint giữa runner. Các yếu tố này cần thử thực tế, không phải kết luận GitHub không thể chạy bộ thu.

## Bộ triển khai đã có

- `deploy/linux/neo-collector.service`: chạy bằng user riêng, giữ cache SDK tại `/var/lib/neo-liquiditylab`, dùng `.env` cục bộ và SQLite bền vững.
- `deploy/linux/neo-collector.timer`: 09:10 thứ Hai–thứ Sáu theo Asia/Ho_Chi_Minh, có catch-up sau reboot. [systemd timer](https://www.freedesktop.org/software/systemd/man/latest/systemd.timer.html) không tạo service trùng khi service đang chạy.
- Khóa tiến trình dùng msvcrt trên Windows, flock trên Linux. Cổng trước 09:10/ngoài giờ/ngày nghỉ và cờ STOP_VNSTOCK/NEO_READY được giữ.
- Chỉ collector chạy trên VM; không cần mở cổng dashboard công khai. Lấy CSV/báo cáo về máy qua kết nối quản trị đã xác thực.

## Các bước sau khi có VM

1. Người dùng tự tạo tài khoản/VM miễn phí và giữ khóa SSH cục bộ. Không gửi thẻ, mật khẩu, API key hoặc private key vào chat.
2. Chép mã nguồn vào `/opt/neo-liquiditylab`, tạo user `liquiditylab`, Python 3.12 và venv, cài `deploy/linux/requirements.txt`. Dùng tài khoản quản trị VM cho các bước cài đặt hệ điều hành. Không chép `.venv` Windows sang Linux.
3. Tạo `data`, `report` và thư mục SDK state với quyền ghi cho `liquiditylab`; bảo vệ `.env` chỉ cho đúng user/group đọc. Các tệp mã/cấu hình/service do quản trị sở hữu. Có thể lưu khóa với tên `vnstock_api` hiện có hoặc `VNSTOCK_API_KEY`.
4. Chuyển thiết bị Vnstock theo cơ chế chính thức và kiểm tra đăng nhập/quota trên VM. Lệnh probe không có `--quote` chỉ kiểm tra trạng thái. Chạy preflight danh mục sau khi quyền/quota hợp lệ.
5. Kiểm tra timezone expression bằng `systemd-analyze calendar 'Mon..Fri *-*-* 09:10:00 Asia/Ho_Chi_Minh'`; kiểm tra service bằng `systemd-analyze verify` trên VM. Cài hai unit vào `/etc/systemd/system`, `daemon-reload`, rồi bật timer sau khi preflight đạt.
6. Xác nhận giờ chạy tiếp, file báo cáo, backup và một lượt thu thực trong phiên trước khi coi chuyển đổi hoàn tất. Tắt task Windows khi chuyển thu để không chạy đồng thời; ghi rõ thời điểm bàn giao và dữ liệu còn trên từng máy.

Các lệnh vận hành sau khi cài:

```bash
sudo systemctl list-timers neo-collector.timer
sudo systemctl status neo-collector.service
sudo journalctl -u neo-collector.service --since today
sudo systemctl disable --now neo-collector.timer
```

Lịch Windows không bị vô hiệu hóa bởi việc chỉ tạo các tệp này. Chỉ chuyển sang server sau khi người dùng có VM và cách truy cập hợp lệ. Không có cam kết miễn phí 24/7 tuyệt đối từ các dịch vụ nêu trên; báo cáo và backup cần được tải về định kỳ.
