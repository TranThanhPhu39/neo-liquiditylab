# GitHub Actions cho NEO LiquidityLab

Đích được người dùng chọn: **repo public `TranThanhPhu39/neo-liquiditylab`**. Kiến trúc tham khảo từ [worker crypto](https://github.com/TranThanhPhu39/dashboard_crypto/blob/main/.github/workflows/worker.yml): worker có lịch, preflight tách riêng và checkpoint mã hóa. Không sửa repo crypto. GitHub Pages không chạy collector; ứng dụng Streamlit vẫn là giao diện local, chưa xuất bản một dashboard Pages cho LiquidityLab.

## Workflow và giới hạn

`.github/workflows/test.yml` kiểm thử offline trên Ubuntu/Python 3.12 khi push/PR. Không nhận secret hoặc gọi SDK mạng. `.github/workflows/collect.yml` chỉ cho repo đích và nhánh main; không chạy worker trên push hoặc PR. Mặc định lịch chưa bật cho tới khi repository variable `LIQUIDITYLAB_WORKER_ENABLED=true`.

Cron `7 2 * * 1-5` theo UTC tương ứng **09:07 giờ Việt Nam** để cài môi trường trước mốc khởi động 09:10; worker vẫn chỉ lấy mẫu trong cửa sổ **09:15–11:30, 13:00–14:30**. Lịch GitHub có thể trễ/bỏ lượt; khi đến muộn chỉ thu phần còn lại, không tạo dữ liệu hồi tố. Job timeout 355 phút, dưới trần 6 giờ. Lịch public có thể bị GitHub tắt sau thời gian repo không hoạt động; cần theo dõi Actions thay vì coi đây là dịch vụ có SLA.

Repo public dùng standard runner để tránh chi phí phút chạy. [GitHub billing](https://docs.github.com/en/billing/concepts/product-billing/github-actions) phân biệt compute với artifact/cache; không bật larger runner, quota trả phí hoặc máy trả phí. Nếu đổi sang private, khoảng 5 giờ 20 phút ×20 ngày đã vượt 2.000 phút của GitHub Free.

## Hai secret

- `VNSTOCK_API_KEY`: khóa hiện có, chỉ đưa vào Actions secret; không chép `.env` lên repo.
- `LIQUIDITYLAB_STATE_KEY`: khóa AES-256-GCM để đọc dataset checkpoint. Bản dự phòng cục bộ được bảo vệ bằng Windows DPAPI trong `data/control/github_state_key.dpapi`; không nằm trong source.

Việc đưa khóa vào GitHub cần được người dùng chấp thuận cho đúng repo đích. Người có quyền sửa workflow main có thể sử dụng secrets; không cấp quyền ghi tùy tiện. Không gửi khóa qua chat hoặc in trong log.

Vnstock yêu cầu API key cho CI và giới hạn thiết bị theo tier. Không sao chép cache xác thực/device state từ Windows vào runner, không giả device ID hoặc tự gỡ thiết bị để né giới hạn. Cần **hai lượt preflight trên hai runner mới** để kiểm chứng cách tài khoản hiện tại hoạt động. Một lần chạy trên Windows không thay được kiểm tra này. Nếu gặp AUTH_DEVICE_LIMIT thì dừng và thực hiện chuyển thiết bị/quyền theo cơ chế của Vnstock.

## Dữ liệu bền vững

Mỗi ngày thu vào database riêng `data/github/YYYY-MM-DD.db`. Dữ liệu các ngày trước không cần tải lại để gọi quote ngày mới. Khi chạy lại cùng ngày, worker khôi phục checkpoint hoàn chỉnh mới nhất của ngày đó; không dùng một database mới để né quota.

Cứ khoảng 15 phút và khi chốt, SQLite backup nhất quán cùng status/audit/universe được đóng archive, chia khối 16 MiB rồi mã hóa AES-256-GCM với nonce ngẫu nhiên. Context gắn với repo, tag và vị trí khối; có hash ciphertext/archive, kiểm tra SQLite và allowlist đường dẫn khi khôi phục. **Không có `.env`, GitHub token hoặc cache/device state SDK trong archive.**

GitHub Release được tạo draft, upload đủ ciphertext rồi mới công bố checkpoint hoàn chỉnh. Tên tag chứa ngày và thời điểm checkpoint; không force-push hoặc ghi đè checkpoint cũ. Repo public chỉ lộ ciphertext và metadata phiên bản, không có database/CSV thu thật dạng rõ. Không dùng artifact/cache có thời hạn làm bản duy nhất của dataset.

Đơn vị chỉ chuẩn hóa FPT/HPG/ACB đã thẩm định. Bản evidence public loại account/quota diagnostics và có checksum riêng; raw giữ đủ mã toàn thị trường. Dataset 66 snapshot hiện ở Windows vẫn giữ nguyên và chưa được coi là đã bàn giao lên GitHub chỉ nhờ đẩy code.

## Bàn giao

1. Công khai mã nguồn đã kiểm kê, loại `.env`, dữ liệu thu thật và control/cache cục bộ. Không thay mã crypto.
2. Kiểm thử offline trên Ubuntu đạt.
3. Sau khi được phép, cấu hình hai secrets và chạy **Run workflow → preflight**; kiểm tra xác thực, danh mục và một quote chẩn đoán FPT/HPG/ACB. Lặp lại bằng runner mới. Preflight không cộng vào lịch sử phiên.
4. Khi CI auth đã ổn định, tạm dừng task Windows để chỉ có một bộ thu chủ động, bật `LIQUIDITYLAB_WORKER_ENABLED=true`, xác nhận lịch và source readiness.
5. Nghiệm thu thêm lượt collect thực trong phiên, checkpoint Release và khôi phục từ runner khác. Sau đó mới công nhận máy cá nhân có thể tắt cho nguồn thu đã bàn giao.

Tạm dừng lịch GitHub: đặt repository variable `LIQUIDITYLAB_WORKER_ENABLED=false`. Việc này chặn lượt tiếp theo; không giết job đang chạy. Khi chuyển NEO, dừng sau checkpoint và xác nhận job kết thúc trước khi khởi động nguồn mới. Không bật lại Windows từ state cũ để ghi đè dataset GitHub.

Admin entrypoint `python -m scripts.github_setup` có các lệnh prepare/publish/status/preflight/enable/disable. Lệnh `secrets --include-vnstock` chỉ dùng sau khi được phép; credential GitHub lấy từ Git Credential Manager hoặc env trong bộ nhớ, không đưa vào lệnh, báo cáo hay file Git.
