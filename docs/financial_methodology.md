# Phương pháp tài chính

## A1 · Quét ba mức đang quan sát

Mọi giá tính VND/CP, lượng tính CP nguyên, giá trị tiền VND. Decimal dùng cho phép tính, không làm tròn tiền từng mức; UI làm tròn hiển thị, CSV giữ chuỗi chính xác. Không mô phỏng lot-size, bước giá, biên độ, priority, lệnh mới/hủy, replenishment hay market impact động.

- Spread = Ask1 − Bid1; relative spread = spread / ((Ask1 + Bid1)/2), chỉ khi hai mức hợp lệ.
- BidDepth/AskDepth = tổng volume các mức hợp lệ. Giá null/ATO/ATC không đóng góp depth. Volume 0 là mức rỗng. PARTIAL được ghi rõ; không suy diễn phần thiếu.
- OBI = (BidDepth − AskDepth)/(BidDepth + AskDepth); tổng bằng 0 ⇒ undefined. Đây là **stock imbalance ba mức**, không phải order-flow imbalance của Cont et al.
- Lệnh bán lần lượt dùng Bid1, Bid2, Bid3; mua dùng Ask. Fill mỗi mức = min(volume mức, lượng còn lại).
- ExecutedQty = Σfill; Uncovered = requested − executed; Coverage = executed/requested. Requested nguyên dương.
- Gross = Σ(fill × price). VWAP = gross/executed; executed=0 ⇒ undefined. Không định giá uncovered.
- Best benchmark = executed × Bid1 (bán) hoặc Ask1 (mua). Nếu best thiếu ⇒ benchmark/cost undefined dù có thể mô phỏng mức 2/3.
- Book-walking cost bán = benchmark − gross; mua = gross − benchmark. Cost bps = cost/benchmark × 10.000. Chỉ trên **executed quantity**.
- Có thể chọn mid-price: benchmark = executed × mid. Khi đó chi phí bao gồm phần half-spread, khác best-level benchmark; không so lẫn hai benchmark.
- Fee = gross × fee_rate. Sell tax = gross × sell_tax_rate cho bán; mua không tính thuế bán. Net bán = gross − fee − tax; net mua = gross + fee. Mặc định các tỷ lệ 0; là giả định người dùng, không là phí thật của VPBankS.

Book-walking cost là chênh lệch toán học do quét mức giá hiện tại, **không là ước lượng đầy đủ market impact**. Uncovered không phải chắc chắn không bán được trên toàn thị trường: chỉ chưa được ba mức quan sát bao phủ.

## Kiểm chứng số cụ thể

Fixture synthetic: 1.000 CP @30.000; 1.500 @29.950; 1.000 @29.900.

| Yêu cầu | Fill | Gross VND | VWAP | Cost so Bid1 | Uncovered |
|---|---|---:|---:|---:|---:|
| 500 | 500,0,0 | 15.000.000 | 30.000 | 0 | 0 |
| 3.000 | 1000,1500,500 | 89.875.000 | 29.958,3333… | 125.000 | 0 |
| 4.000 | 1000,1500,1000 | 104.825.000 | 29.950 | 175.000 | 500 |

Kiểm tra tự động không vượt requested/depth, uncovered >=0, gross đúng tổng fills và volume giảm không tăng executed trong điều kiện giá giữ nguyên.

## B5 · Hypothetical và reverse stress

Với depth_reduction d ∈ [0;0,9]: BidVolume_i' = floor(BidVolume_i × (1−d)). Ask volume giữ nguyên. Với price_shock p ∈ [−0,99;1]: toàn bộ giá dương nhân (1+p). Không làm tròn bước giá; không đưa ra giả định giao dịch hợp lệ ở sàn hoặc biên độ. Giá và thanh khoản được điều chỉnh độc lập, không khẳng định quan hệ thực nghiệm.

Tính lại A1 trên snapshot biến đổi; baseline luôn bên cạnh stress. Khi volume bị giảm, so tổng proceeds có cả hiệu ứng lượng được bao phủ khác nhau, **không được gọi chênh lệch proceeds là lỗ danh mục**. Cost mỗi kịch bản dựa trên best giá của chính kịch bản đó, không trộn thêm price loss so với baseline.

Reverse stress tìm mức d nhỏ nhất trên lưới 0,0001 (0,01%) khiến coverage **nhỏ hơn** ngưỡng, binary search đến 90%. Kết quả 0 nếu baseline đã dưới ngưỡng; null nếu trong khoảng chưa vi phạm. Floor từng mức có thể khiến ngưỡng khác công thức depth liên tục.

## Danh mục và lịch sử

Danh mục nhập tay: bán floor(quantity × fraction) CP theo mã. Kiểm tra cùng provider/upstream, độ lệch observation time mặc định <=60 giây. Dữ liệu cũ >600 giây bị loại trừ, trừ lựa chọn cho phép rõ ràng (demo/replay). Khi không có event time dùng arrival làm proxy và cảnh báo, không chứng minh quote thực sự đồng bộ.

Coverage tổng = tổng executed / tổng requested của **các mã đủ dữ liệu phân tích**. Mã thiếu/invalid/stale/skew nằm ở excluded_quantity, không tự coi là uncovered vì chưa có cơ sở đo. UI hiển thị requested, analyzed, excluded riêng. Tổng CP là thống kê số lượng, không phải coverage theo giá trị. Giá trị tham chiếu dùng last hoặc reference có sẵn của phần yêu cầu được phân tích, báo mã thiếu giá. Gross/net chỉ trên covered. Tổng cost chỉ gồm mã có benchmark, kèm danh sách undefined. Không đủ mã ⇒ tiền/coverage undefined.

Historical dùng đúng snapshot gốc theo nguồn/mã, không ghép giá ngày này với depth ngày khác. Median/min/max mô tả mẫu. Percentile 5% chỉ tính từ >=20 quan sát, vẫn không là xác suất khủng hoảng vì snapshot có thể lặp và phụ thuộc. Báo khoảng thời gian, số ngày thu thập, missing source time và invalid ratio. Không tự tạo lịch sử hay tuyên bố đủ phiên thật.

## Nguồn nghiên cứu đã đối chiếu

[Cont, Kukanov & Stoikov, The Price Impact of Order Book Events](https://arxiv.org/abs/1011.6402), Journal of Financial Econometrics 12(1), 47–88 (2014), DOI 10.1093/jjfinec/nbt003. Bài nghiên cứu về order-flow imbalance và depth gợi động lực đo thanh khoản. MVP dùng phép quét tĩnh và stock OBI, **không triển khai mô hình price impact hoặc khẳng định kết quả thực nghiệm của bài này áp dụng cho Việt Nam**. Các công trình khác trong đề là gợi ý đọc tiếp, chưa dùng làm cơ sở triển khai hoặc trích dẫn chưa đối chiếu.
