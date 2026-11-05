# Tự cập nhật dữ liệu dashboard

Đã được người dùng yêu cầu ngày 05/11/2026: công bố dữ liệu ngày 05/11 và tự công bố các ngày hoàn tất tiếp theo, cùng định dạng dữ liệu lấy mẫu hiện có.

## Trên máy

`config/web_publication.json` bật chính sách cho repo public `TranThanhPhu39/neo-liquiditylab`. Sau 15:10, chỉ cần:

```powershell
.\.venv\Scripts\python.exe -m scripts.update_dnse_day
```

Luồng: tải hết trang → audit raw → chuẩn hóa → audit chuẩn hóa → kiểm tra tài chính → đối chiếu checksum DB với manifest đã audit → xuất dữ liệu lấy mẫu → đẩy riêng `web/data/` → workflow Pages triển khai. Không cần nhớ thêm `--publish-web`. Mã chưa xác minh vẫn giữ nhãn và bị khóa tính toán như trước.

Giữ một lượt riêng trên máy, không công bố:

```powershell
.\.venv\Scripts\python.exe -m scripts.update_dnse_day --no-publish-web
```

Phiên sáng không tự công bố mặc định. Có thể tắt toàn bộ tự công bố bằng `enabled=false` trong policy. Lệnh cập nhật vẫn cần người dùng hoặc một scheduler thực sự khởi chạy; policy không tự bật máy hay cài lịch Windows.

Nếu ngày đã chuẩn hóa/audit xong, xuất và công bố mà không tải hay audit lại toàn kho:

```powershell
.\.venv\Scripts\python.exe -m scripts.export_web_data --publish
```

Tự công bố chỉ gửi các partition theo allowlist đã kiểm tra checksum và quét credential; không stage source khác đang sửa, .env, raw, DB đầy đủ hoặc thông tin tài khoản.

## Trên GitHub

**Nhánh cloud đã viết và kiểm thử offline, chưa bật** (`github_enabled=false`). Bộ duyệt tự động từ chối upload raw đầy đủ và bằng chứng riêng dù mã hóa vì chưa có chấp thuận rõ payload/đích public. Không upload bootstrap hoặc raw bằng cách khác. Cần người dùng duyệt đúng payload trước khi chuẩn bị seed, kiểm chứng runner và bật flag này. Tự công bố trên máy vẫn bật và chỉ gửi dữ liệu lấy mẫu đã cho phép.

Workflow DNSE giữ nguyên cron 08:52/12:52. Khi WS kết thúc bình thường, thử REST để có lịch sử; lượt đến trễ cũng dùng nhánh phục hồi như trước. Sau khi REST **cả ngày** hoàn tất và checkpoint đã lưu, bước **Normalize audit and publish completed REST day** tự thực hiện:

1. Kiểm tra cả ngày đã hết trang; bỏ qua smoke, sáng và REST lỗi/tải dở.
2. Giải mã bootstrap bằng chứng đơn vị đã xác minh từ namespace `dnse-rest-web-rules`.
3. Chuẩn hóa và audit raw trên runner; kiểm tra A1/B5 và checksum DB.
4. Xuất dữ liệu lấy mẫu ngày đó, giữ nguyên các ngày cũ trong index và kiểm tra checksum các phần cũ.
5. Đẩy riêng dữ liệu web, rồi gọi `workflow_dispatch` cho `dashboard-pages.yml`.

Bước 5 cần `actions: write`: theo [GitHub Docs](https://docs.github.com/en/actions/concepts/security/github_token), push bằng GITHUB_TOKEN không tự tạo lượt workflow push/Pages mới; workflow_dispatch là ngoại lệ được hỗ trợ. Không thêm PAT hoặc secret tài khoản mới vào runner.

Bootstrap dự kiến chỉ chứa hai JSON bằng chứng/quote đối chiếu, mã hóa AES-256-GCM bằng khóa checkpoint hiện có. Không đưa bằng chứng riêng này vào source public. Chỉ sau khi người dùng đã đồng ý lưu payload này lên Releases public mới chạy lệnh chuẩn bị:

```powershell
.\.venv\Scripts\python.exe -m scripts.prepare_dnse_web_rules --publish
```

Không làm mới hồ sơ xác minh chỉ để mở khóa mã mới; vẫn cần bằng chứng thật. Hash của rules giữ giống bộ đã dùng trên máy.

## Giới hạn vận hành

REST trên GitHub hiện vẫn timeout ở TCP. Vì vậy nhánh tự công bố trên GitHub đã được nối nhưng chưa thể có ngày mới nếu bước tải REST thất bại. Khi đó web giữ dữ liệu cũ; job báo lỗi, không tạo dữ liệu giả hoặc xuất checkpoint rỗng. Đường cập nhật trên máy là đường đang sử dụng được.

Việc scheduler GitHub giao trễ hoặc chưa tạo run vẫn độc lập với bước công bố. Web không tự đọc SQLite trên máy hoặc chuyển checkpoint WS thành REST. Không tuyên bố tự động toàn bộ khi nguồn tải còn chưa hoạt động.

Sau lượt công bố thành công, kiểm tra Actions → Deploy LiquidityLab dashboard và mốc ngày mới trong `web/data/index.json` trên Pages. Push thành công chưa đồng nghĩa triển khai Pages đã xong.
