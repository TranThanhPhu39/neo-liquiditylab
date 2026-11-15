# SSI realtime trên dashboard

Triển khai 13/11/2026. Trang **Realtime SSI** dùng riêng feed SSI; dữ liệu DNSE lịch sử vẫn đọc từ Parquet như trước.

**Bản online từ 15/11:** mở **https://neo-liquiditylab-monitor.onrender.com/realtime**, tên đăng nhập `liquiditylab`, mật khẩu `.env` → `MONITOR_WEB_PASSWORD`. Bộ thu do lịch GitHub quản lý, không cần chạy máy local. Các nút bắt đầu/dừng trong hướng dẫn bên dưới dành cho bản local; bản online giữ quyền điều khiển ở GitHub. Xem [quy trình online](online_monitor.md) và giới hạn của Render Free.

## Chạy

```powershell
.\.venv\Scripts\python.exe -m pip install -e ".[test]"
.\.venv\Scripts\python.exe -m scripts.serve_orderbook_monitor
```

Mở **http://127.0.0.1:8877/realtime** hoặc bấm **Realtime SSI** ở trang lịch sử. Khóa `SSI_API_KEY` và `SSI_API_SECRET` nằm trong `.env`; chỉ backend dùng chúng, không đi vào trình duyệt, query string, Parquet hoặc log.

1. Bấm **Bắt đầu thu ACB · FPT · HPG**. Mỗi lượt thu tối đa 6 giờ; máy và server phải tiếp tục chạy.
2. Chọn mã. Heatmap chỉ vẽ mức có giá hợp lệ. Phiên 13/11 có 10 ô mỗi phía nhưng chỉ 3 mức có giá, 7 ô còn lại là đệm 0; không gọi đó là 10 mức depth. Không dựng full exchange depth hoặc tự giữ trạng thái từ bản tin thiếu mức.
3. Bubbles lấy từ DATA trade riêng. Giá/khối lượng chưa được đối chiếu thì dùng nhãn nguồn và màu trung tính. Không suy ra giao dịch từ thay đổi lượng chờ.
4. **Tạm dừng màn hình**, tua con trỏ hoặc **Phát lại** để kiểm tra diễn biến. Bộ thu vẫn tiếp tục chạy. Bấm **Về hiện tại** để bắt kịp dữ liệu mới.
5. Chọn sự kiện để xem bằng chứng, ghi chú và tải hồ sơ đến mốc đang xem. Sự kiện/trạng thái sau con trỏ không được đưa ngược vào bằng chứng trước đó.
6. Bấm **Dừng & lưu**, chờ trạng thái đã dừng. Chọn lại phiên thu trong danh sách để xem dữ liệu Parquet đã lưu. Dùng **Đoạn trước/Đoạn sau** khi phiên dài.

Giới hạn hiển thị: cửa sổ RAM 15.000 quote/mã, lần tải đoạn lưu tối đa 5.000 quote, bubbles lưu tối đa 30.000 giao dịch/mã trong bộ nhớ và đọc tối đa 10.000 giao dịch/đoạn lưu. Toàn bộ bản ghi thu được vẫn được ghi trên đĩa. Không coi số giao dịch đang hiển thị là tổng khớp của sở; retransmission/completeness chưa có provider ID/sequence để chứng minh.

## Đồng hồ, mất kết nối và lưu trữ

- Trục thời gian realtime/replay là **lúc máy nhận bản tin**; thời gian SSI gốc được giữ riêng. Chênh nguồn/nhận có dấu, không tự đổi số âm về 0 và không gọi nó là độ trễ mạng.
- Sau mất kết nối, lấy token phù hợp và subscribe lại. Detector bị ngắt, baseline được xóa. Khoảng mất dữ liệu được giữ dấu; không khẳng định có backfill khi SSI chưa được kiểm chứng khả năng này.
- Khi riêng một mã không có quote mới quá 30 giây, reset baseline và ghi dấu thiếu quan sát. Đây không tự động là bằng chứng mạng bị ngắt hoặc thanh khoản bằng 0.
- Bản ghi đi qua journal có fsync rồi nén thành các batch Parquet Zstd, mặc định 1.000 bản ghi hoặc 10 giây. Khởi động lượt thu mới sẽ khôi phục journal của các lượt bị gián đoạn sau khi lấy khóa collector; phần đuôi hỏng được giữ riêng và đánh dấu gap.
- Dữ liệu ở `data/realtime/ssi/<ngày>/<run_id>/`: `part-*.parquet`, `manifest.json`, `pending.jsonl`. Không tạo SQLite toàn lịch sử. Đường này được gitignore; không tự công khai dữ liệu SSI.
- Bộ sao lưu hiện có chọn các phiên SSI **đã đóng** và hợp đồng đơn vị. Dừng & lưu trước khi chạy `scripts.create_recovery_backup` để đưa cả phiên hiện tại vào bản sao.

## Điều kiện phát hiện sự kiện

Dùng lại D1 spread, D2 depth trên các mức giá chung và D3 imbalance, tính trên **top 3**. Heatmap hỗ trợ tối đa 10 ô nhưng chỉ hiển thị mức có giá hợp lệ. Đây là quy tắc thử nghiệm, không kết luận thao túng hoặc ra lệnh giao dịch.

`data/control/ssi_live_contract.json` giữ hệ số và evidence riêng SSI. ACB quote đã đối chiếu độc lập với DNSE có hợp đồng đã xác minh bằng ảnh trước đây: 6 cặp top 3 trùng ở hai trạng thái book khác nhau, hệ số giá/lượng SSI đều là 1. ACB trades cũng đã khớp giá, lượng và lũy kế ở hai giao dịch khác nhau. Các mã khác không được tự nâng nhãn. Xem `report/ssi_acb_quote_units_2026-11-13.json`, `report/ssi_acb_trade_units_2026-11-13.json`.

Kênh indexsummary thực tế trả cờ phiên `C` lúc có quote/trade mới. Client giữ nguyên C, không tự ép sang LO. Đã subscribe **`market.flag`** theo SDK chính thức cùng `market.hose`. Các cờ trực tiếp được ưu tiên hơn indexsummary; bản tin nguồn cũ bị bỏ qua và xung đột cùng thời điểm khóa detector. Chỉ giữ cờ HOSE trên topic phát chung, bỏ qua các sàn khác. Nguồn cờ được hiển thị bên cạnh giá trị cờ trên dashboard.

Client chỉ chấp nhận `LO` cùng ngày/kết nối và cùng khoảng liên tục sáng hoặc chiều với quote. LO sáng không được mang qua nghỉ trưa để tự xác nhận phiên chiều; cờ nhận sau không được dùng xác nhận quote có thời gian nguồn trước đó. Reconnect xóa cờ đã nhớ. Nếu chưa quan sát được LO hợp lệ hoặc đơn vị mã chưa được xác minh, detector vẫn khóa dù heatmap/bubbles có dữ liệu thật.

Metadata phiên chưa nhận diện trên topic `market` đã đăng ký được ghi riêng trong Parquet (`SYSTEM/SESSION_METADATA_QUARANTINED`), xóa cờ đã nhớ và tăng rejected, nhưng không làm dừng thu quote/trade. Không hạ kiểm tra phạm vi mã hoặc kênh riêng tư. Nếu các kiểm tra đó phát hiện vi phạm, bộ thu vẫn dừng, đồng thời lưu chẩn đoán cấu trúc đã lọc để điều tra.

Nguồn: [SSI DATA quote/trade/market/indexsummary](https://developers.ssi.com.vn/en/docs/api-reference/subscribeUnsubscribeMarketData), [kết nối và resubscribe](https://developers.ssi.com.vn/en/docs/api-reference/connectWebSocket), [heartbeat](https://developers.ssi.com.vn/en/docs/api-reference/getHeartbeatStructure). Các nguồn này không chứng nhận đơn vị của mọi phiên bản quote; evidence runtime được giữ riêng.

## Kiểm thử và chẩn đoán

```powershell
# Thu ngắn, chỉ market DATA, không giao dịch
.\.venv\Scripts\python.exe -m scripts.collect_ssi_live --seconds 60 --symbols ACB FPT HPG

# Đối chiếu ACB với nguồn độc lập; không tự sửa hợp đồng
.\.venv\Scripts\python.exe -m scripts.compare_ssi_units

.\.venv\Scripts\python.exe -m pytest -q tests/test_ssi_live.py
node --test tests/js/ssi-live.test.cjs tests/js/ssi-live-ui.test.cjs
```

Không chạy collector CLI song song với collector của dashboard; khóa `ssi_live.lock` bảo vệ một bộ thu trên máy. Phiên thử 120 giây đã nhận 680 quote, 472 giao dịch khớp, không có quarantine hoặc disconnect. Đây là thử ngắn, chưa thay thế nghiệm thu cả phiên/reconnect trên mạng thật.

Browser automation hiện không có browser access host. Đã kiểm tra API thật cùng unit test DOM/Canvas chạy renderer thật; chưa xác minh trực quan bằng ảnh chụp trình duyệt trong phiên triển khai này.
