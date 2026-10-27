# Hồ sơ và hoạt động cá nhân

Trang Hồ sơ hỗ trợ họ tên, email, số điện thoại, avatar PNG tối đa 256 KB và 512 × 512 pixel. Tên đăng nhập và vai trò chỉ đọc. Tùy chọn bản đồ gồm nền màu hoặc nền xám; chỉ đổi nền tile, không đổi màu dữ liệu giao thông.

Lịch sử tìm đường được ghi ở máy chủ sau khi Dijkstra/Yen trả kết quả thành công. Mỗi lượt lưu các tuyến đã trả về, tổng khoảng cách và thời gian ước tính của tuyến đầu theo tốc độ cài đặt của người dùng (mặc định 30 km/h). Ước tính chỉ dựa trên khoảng cách, không xét giao thông thời gian thực. Không có dữ liệu lịch sử trước khi triển khai tính năng.

Nút Lưu tuyến yêu thích nằm ở kết quả tìm đường và trong chi tiết lịch sử ở Hồ sơ. Tuyến được lưu là bản chụp đúng lựa chọn, không tự tính lại khi mở. Người dùng có thể xem bản đồ hoặc bỏ lưu. Tối đa 100 tuyến yêu thích mỗi tài khoản. Các tuyến lưu vẫn tồn tại khi lịch sử tương ứng bị loại khỏi 100 lượt gần nhất.

Lịch sử phân tích lưu bản tóm tắt tổng quan, chiều dài, bậc nút và liên thông mỗi lần tải thành công API phân tích chi tiết. Giao diện lịch sử phân tích chỉ hiện với admin. Đây là kết quả tại thời điểm ghi nhận, không phải truy vấn dữ liệu hiện tại.

Neo4j lưu các node ProfileActivity và ProfileSavedRoute, liên kết với AppUser bằng HAS_ACTIVITY và SAVED_ROUTE. API luôn xác định chủ sở hữu từ phiên đăng nhập, không nhận userId từ client. Dữ liệu này nằm trong sao lưu toàn CSDL; sao lưu giao thông không bao gồm hồ sơ/lịch sử. Hoạt động cá nhân không đánh dấu projection mạng đường cần tạo lại.

Đổi mật khẩu yêu cầu mật khẩu hiện tại và vô hiệu các phiên cũ theo cơ chế sẵn có. Tự xóa tài khoản yêu cầu mật khẩu và chuỗi DELETE; thao tác xóa tài khoản cùng dữ liệu cá nhân trong một transaction, giữ dữ liệu giao thông chung. Không cho xóa admin hoạt động cuối cùng; dùng cùng khóa AuthState với chức năng quản lý người dùng.

Kiểm thử tự động gồm kiểm tra đầu vào hồ sơ/avatar, ngăn thay đổi vai trò, truy xuất theo chủ sở hữu, lưu snapshot từ lịch sử phía máy chủ và bảo vệ admin cuối cùng. Kiểm thử dùng mock chưa thay thế chạy thử đầy đủ với Neo4j thật.
