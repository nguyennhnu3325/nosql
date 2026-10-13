# Neo4jRouting — Urban Route

Ứng dụng Flask tìm đường bằng Neo4j GDS và quản lý mạng giao thông.

## Chạy ứng dụng

Cần Python, Neo4j đang chạy và plugin Graph Data Science. Dữ liệu gốc nằm trong `data/road_network.graphml`; Neo4j chứa `Place`, `Junction`, `ROAD` và `ACCESS_TO`.

Môi trường `.venv` đang có trong thư mục trỏ tới Python 3.11 không còn trên máy. Có thể tạo một môi trường riêng mà vẫn giữ nguyên môi trường cũ:

```powershell
python -m venv .venv-local
.\.venv-local\Scripts\python.exe -m pip install -r requirements.txt
$env:NEO4J_URI = "bolt://localhost:7687"
$env:NEO4J_USER = "neo4j"
$env:NEO4J_PASSWORD = "mat-khau-neo4j-cua-ban"
.\.venv-local\Scripts\python.exe app/app.py
```

Mở http://127.0.0.1:5000. Lần đầu ứng dụng chuyển đến `/setup` để tạo admin. Không có mật khẩu admin mặc định. Admin đăng nhập rồi tạo tài khoản thường ở `/admin/users`. Người dùng cũng có thể tự đăng ký ở `/register`; tài khoản tự đăng ký luôn có vai trò `user`. Khi đã có tài khoản, trang setup không cho tạo admin mới.

Các biến cấu hình được mô tả trong `.env.example`; ứng dụng đọc biến môi trường, không tự nạp tệp .env. Khóa phiên được tự tạo trong `instance/session.key` nếu chưa đặt `FLASK_SECRET_KEY`. Debug mặc định tắt.

## Chức năng và phân quyền

| Trang | Người dùng thường | Admin |
| --- | --- | --- |
| /register — đăng ký tài khoản thường | Có | — |
| /login, đăng xuất | Có | Có |
| / — tìm tuyến đường | Có | Có |
| /network — xem mạng lưới | Có | Có |
| /analysis — kết quả phân tích | Có | Có |
| /admin — CRUD địa điểm | Không | Có |
| /admin/users — CRUD tài khoản, phân quyền, khóa, đặt lại mật khẩu | Không | Có |
| /admin/traffic — CRUD Junction/ROAD, import/export, backup/restore | Không | Có |

Quyền được kiểm tra ở máy chủ cho cả trang và API. Mật khẩu được băm bằng Werkzeug; API không trả về mật khẩu/hash. Thay đổi tài khoản làm hết hiệu lực các phiên cũ. Không thể tự xóa/khóa/hạ quyền tài khoản admin đang đăng nhập hoặc loại bỏ admin hoạt động cuối cùng. Các yêu cầu thay đổi dữ liệu cần CSRF token.

Tọa độ Place và Junction được kiểm tra phạm vi. Lưu Place tự tìm Junction gần nhất và tạo lại ACCESS_TO trong cùng transaction. Sửa/xóa Junction hoặc import sẽ tính lại liên kết các Place. Thay đổi mạng đường đánh dấu graph GDS cần làm mới trước lần tìm đường kế tiếp. Geometry OSM được dùng cho đường còn khớp dữ liệu gốc; đường mới hoặc đã sửa dùng đoạn nối tọa độ Junction hiện tại trong Neo4j.

Phân tích gồm số Junction/ROAD/Place, tổng và trung bình chiều dài ROAD, các thành phần liên thông yếu, Junction cô lập, Junction có bậc cao và phân bố loại đường/địa điểm. Tổng chiều dài cộng từng ROAD có hướng, bao gồm cả hai hướng nếu có. Đây là phân tích cấu trúc mạng lưới, không phải dữ liệu ùn tắc trực tiếp.

## Import, export và sao lưu

Định dạng JSON `neo4j-routing-traffic-v1`:

```json
{
  "format": "neo4j-routing-traffic-v1",
  "junctions": [{"junctionId": 1, "latitude": 10.8, "longitude": 106.6}],
  "places": [{"placeId": "P1", "name": "Cafe", "category": "cafe", "latitude": 10.8, "longitude": 106.6}],
  "roads": [{"source": 1, "target": 1, "properties": {"roadId": "R1", "name": "Đường mẫu", "highway": "residential", "lengthM": 10}}],
  "access": [{"placeId": "P1", "junctionId": 1, "properties": {"accessDistanceM": 0}}]
}
```

Dùng Export JSON để lấy mẫu từ dữ liệu thật. Import gộp theo mã; những đối tượng không xuất hiện trong tệp được giữ lại, ACCESS_TO được tính lại. Mỗi tệp cần chứa đầy đủ Junction đầu/cuối của các ROAD và các tham chiếu ACCESS_TO trong tệp. Một ROAD chỉ đi theo hướng source → target; đường hai chiều cần hai ROAD có mã khác nhau.

Backup lưu trong `backups/`, có thể tải xuống. Đây là **sao lưu logic dữ liệu giao thông** (Place, Junction, ROAD, ACCESS_TO), không phải bản dump toàn bộ Neo4j; không chứa tài khoản, quyền, GraphML, cấu hình, index hoặc graph GDS trong bộ nhớ. Restore thay thế dữ liệu giao thông trong một transaction, giữ tài khoản hiện tại. Trước import/restore, hệ thống lưu một bản dữ liệu hiện tại để quay lại nếu cần. Restore yêu cầu nhập RESTORE và xác nhận trên giao diện. Dung lượng tải lên tối đa 32 MB.

## Kiểm thử

```powershell
python -m unittest discover -s tests
python tests/neo4j_smoke.py
```

Bộ unit test không cần Neo4j. Script smoke cần Neo4j đang chạy và dữ liệu mạng đường hiện có; kiểm tra tài khoản/phân quyền, CRUD giao thông, phân tích, import/export và backup/restore trong một transaction luôn rollback. Tệp backup thử được ghi vào thư mục tạm và tự dọn.

## Tuyến ngắn nhất và tuyến thay thế

Trang `/` gọi Dijkstra của Neo4j GDS để tìm tuyến ngắn nhất theo `ROAD.lengthM`. Có thể chọn thêm tối đa hai tuyến thay thế. Các tuyến thay thế được lấy bằng Yen’s K-shortest paths (`gds.shortestPath.yens.stream`), xét tối đa 10 ứng viên rồi loại các tuyến trùng chuỗi Junction. Tuyến thay thế có thể chung một phần đường hoặc có cùng chiều dài với tuyến ngắn nhất; không bảo đảm tách biệt hoàn toàn. Nếu không có tuyến thay thế khác, giao diện vẫn hiển thị tuyến ngắn nhất và thông báo rõ.

Tổng khoảng cách gồm ACCESS_TO của điểm đi + chiều dài ROAD + ACCESS_TO của điểm đến. Chọn từng thẻ kết quả để đổi tuyến trên bản đồ, xem khoảng cách và danh sách đường.

API `POST /api/route` nhận `{ "startId": "P001", "endId": "P002", "alternativeCount": 2 }`. `alternativeCount` nhận 0–2 và mặc định 0 để giữ tương thích với client cũ. Kết quả có `routes` chứa tuyến Dijkstra trước, sau đó các tuyến thay thế; các trường tuyến ngắn nhất ở cấp ngoài vẫn được giữ.

Kiểm thử riêng GDS: `python tests/routing_smoke.py`. Script tạo dữ liệu bằng nhãn riêng (không mang nhãn Junction của ứng dụng), xác minh Dijkstra/Yen và geometry theo trọng số, rồi xóa dữ liệu và projection thử trong `finally`. Tài liệu thuật toán: [Dijkstra](https://neo4j.com/docs/graph-data-science/current/algorithms/dijkstra-source-target/), [Yen](https://neo4j.com/docs/graph-data-science/current/algorithms/yens/).


## Các màn hình quản trị

- **Khám phá:** tìm tuyến Dijkstra/Yen, nút xem tên đường theo thứ tự; xem Junction, ROAD, Place, ACCESS_TO bằng bản đồ, bảng và graph lân cận 2 bước (tối đa 120 node). Tìm theo mã nghiệp vụ, elementId hoặc tên.
- **Phân tích:** tổng số bốn loại phần tử, trạng thái roadGraph/GDS, highway, thuộc tính oneway và số ROAD có chiều ngược, khoảng cách ACCESS_TO, top 10 Place xa Junction nhất và test Dijkstra. Mỗi ROAD có hướng được đếm riêng; không đồng nhất việc thiếu cạnh ngược với thuộc tính oneway.
- **Địa điểm / Mạng lưới / Người dùng:** bảng 10 dòng mỗi trang, tìm kiếm, bộ lọc, popup xem/thêm/sửa và icon xóa. Người dùng có role admin/user, trạng thái hoạt động/khóa, thay hoặc reset mật khẩu (mật khẩu reset được hiển thị một lần).
- **Import/export riêng:** nút JSON trên bảng Place, Junction, ROAD; dùng mẫu export của đúng mục. Import gộp theo ID, kiểm tra toàn bộ tệp và ghi trong một transaction.
- **Phạm vi Place:** khung min/max tọa độ các Junction hiện có; chặn Place mới hoặc di chuyển ra ngoài. Place lịch sử đã ngoài vùng được giữ khi không đổi tọa độ; restore bản cũ bảo toàn tọa độ lịch sử. Ngưỡng cảnh báo khoảng cách mặc định 500 m, chỉnh ở Cấu hình (1–10.000 m).
- **Dữ liệu & sao lưu:** export/import giao thông và các bản sao lưu giao thông cũ vẫn được hỗ trợ. Bản sao lưu CSDL mới đặt tại `backups/database`, chứa toàn bộ node/relationship/thuộc tính, gồm tài khoản và cấu hình ứng dụng. Đây là sao lưu logic JSON, không phải Neo4j dump; không chứa index/constraint, GraphML hoặc projection GDS. Phục hồi giữ schema hiện tại, yêu cầu có admin hiện tại trong bản sao lưu, tự lưu bản trước thay đổi và vô hiệu các phiên đăng nhập cũ.
- **Hệ thống:** trạng thái kết nối Neo4j, GDS, roadGraph; cấu hình ngưỡng ACCESS_TO.
- **Tài khoản:** cập nhật tên hiển thị, đổi mật khẩu bằng mật khẩu hiện tại và đăng xuất.

Kiểm tra: `python -m unittest discover -s tests -p "test_*.py"`. Tích hợp Neo4j: `python tests/neo4j_smoke.py` (mọi thay đổi DB được rollback).
