import os
import requests
import re
from datetime import datetime, timedelta, timezone
from playwright.sync_api import sync_playwright

def scrape_data():
    msv = os.environ.get('MSV')
    password = os.environ.get('PASS_TRUONG')
    
    ket_qua = {
        "anh_lich_hoc": None,
        "anh_lich_thi": None,
        "anh_hoc_phi": None,
        "tin_nhan_hoc_phi": ""
    }

    if not msv or not password:
        print("❌ Lỗi: Không tìm thấy MSV hoặc PASS_TRUONG!")
        return ket_qua

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(viewport={'width': 1920, 'height': 1080})
        page = context.new_page()

        try:
            print("🚀 BƯỚC 1: Đăng nhập hệ thống...")
            page.goto('https://sinhvien1.tlu.edu.vn/#/login', timeout=60000)
            page.fill('#username', msv)
            page.fill('#password', password)
            page.click('button:has-text("Đăng nhập")') 
            page.wait_for_load_state('networkidle')

            # ================= 1. CÀO LỊCH HỌC =================
            print("📅 BƯỚC 2: Vào trang Lịch học...")
            page.goto('https://sinhvien1.tlu.edu.vn/#/student/profile', timeout=60000)
            page.wait_for_timeout(5000) 
            page.click('a:has-text("Bảng")')
            page.wait_for_timeout(2000)
            
            try:
                dropdown_tuan = page.locator('label').filter(has_text="Tuần").locator('..').locator('.ui-select-match')
                dropdown_tuan.click(timeout=5000)
                page.wait_for_selector('.ui-select-choices-row', timeout=5000)
                
                vn_tz = timezone(timedelta(hours=7))
                today = datetime.now(vn_tz)
                
                rows = page.locator('.ui-select-choices-row').all()
                found_week = False
                
                for row in rows:
                    text = row.inner_text()
                    match = re.search(r'\((\d{1,2}/\d{1,2}/\d{4})\s*-\s*(\d{1,2}/\d{1,2}/\d{4})\)', text)
                    if match:
                        start_str, end_str = match.groups()
                        start_date = datetime.strptime(start_str, '%d/%m/%Y').replace(tzinfo=vn_tz)
                        end_date = datetime.strptime(end_str, '%d/%m/%Y').replace(tzinfo=vn_tz)
                        end_date = end_date.replace(hour=23, minute=59, second=59) 
                        
                        if start_date <= today <= end_date:
                            row.click()
                            found_week = True
                            break
                if not found_week:
                    page.keyboard.press('Escape')
            except Exception:
                pass 
            
            page.wait_for_timeout(3000)
            table_locator_hoc = page.locator('.table-bordered:visible').first
            page.wait_for_selector('.table-bordered:visible', timeout=15000)
            ket_qua["anh_lich_hoc"] = "anh_lich_hoc.png"
            table_locator_hoc.screenshot(path=ket_qua["anh_lich_hoc"])
            print("✅ Đã chụp xong lịch học!")

            # ================= 2. QUÉT LỊCH THI =================
            print("📝 BƯỚC 3: Kiểm tra Lịch thi...")
            page.goto('https://sinhvien1.tlu.edu.vn/#/search_exam_room_student/listing', timeout=60000)
            
            try:
                # Đợi cái bảng bất kỳ xuất hiện (không ép buộc phải là .table-bordered nữa)
                page.wait_for_selector('table:visible', timeout=15000)
                bang_lich_thi = page.locator('table:visible').first
                
                # Chờ thêm 2 giây cho Angular render chữ vào bảng
                page.wait_for_timeout(2000)
                
                noi_dung_bang = bang_lich_thi.inner_text()
                so_dong = bang_lich_thi.locator('tbody tr').count()
                
                if "Không tìm thấy" in noi_dung_bang or "Không có" in noi_dung_bang or so_dong == 0:
                    print("✅ Chưa có lịch thi.")
                else:
                    print(f"🚨 PHÁT HIỆN LỊCH THI! Đang chụp ảnh...")
                    ket_qua["anh_lich_thi"] = "anh_lich_thi.png"
                    bang_lich_thi.screenshot(path=ket_qua["anh_lich_thi"])
            except Exception as e:
                print("⚠️ Bỏ qua quét lịch thi (có thể do bảng trống):", e)

            # ================= 3. KIỂM TRA HỌC PHÍ =================
            print("💰 BƯỚC 4: Tra cứu Học phí...")
            page.goto('https://sinhvien1.tlu.edu.vn/#/student_voucher_receive_pay/listing', timeout=60000)
            
            try:
                # Ép bot phải kiên nhẫn đứng đợi TỐI ĐA 15 GIÂY xem chữ đỏ có xuất hiện không
                print("⏳ Đang kiên nhẫn đợi dữ liệu tiền học load...")
                tien_no_locator = page.wait_for_selector('strong.font-red', timeout=15000)
                
                chuoi_tien_no = tien_no_locator.inner_text().strip()
                so_tien = int(chuoi_tien_no.replace(',', '').replace('.', ''))
                
                if so_tien > 0:
                    print(f"🚨 CẢNH BÁO: Đang nợ {chuoi_tien_no} VNĐ!")
                    ket_qua["tin_nhan_hoc_phi"] = f"🚨 CẢNH BÁO HỌC PHÍ: Sếp đang còn nợ {chuoi_tien_no} VNĐ. Nhớ đóng sớm kẻo bị cấm thi nhé! 💸"
                    ket_qua["anh_hoc_phi"] = "anh_hoc_phi.png"
                    page.locator('.portlet-body').first.screenshot(path=ket_qua["anh_hoc_phi"])
                else:
                    print("✅ Số nợ = 0. Đã đóng đủ tiền!")
            except Exception as e:
                # Nếu đợi 15 giây mà không thấy chữ đỏ, chứng tỏ web đã load xong và bạn không nợ
                print("✅ Không tìm thấy khoản nợ (Đã đóng đủ tiền).")

            # ================= KẾT THÚC =================
            browser.close()
            return ket_qua

        except Exception as e:
            print(f"❌ LỖI TRONG QUÁ TRÌNH CÀO WEB: {e}")
            browser.close()
            return ket_qua

def send_telegram_photo(photo_path, caption):
    bot_token = os.environ.get('TELE_BOT_TOKEN')
    chat_id = os.environ.get('TELE_CHAT_ID')
    url = f"https://api.telegram.org/bot{bot_token}/sendPhoto"
    
    try:
        with open(photo_path, 'rb') as photo:
            payload = {"chat_id": chat_id, "caption": caption}
            files = {"photo": photo}
            response = requests.post(url, data=payload, files=files)
            if response.status_code == 200:
                print(f"🎉 Đã gửi ảnh: {caption[:20]}...")
            else:
                print(f"❌ Lỗi gửi ảnh: {response.text}")
    except Exception as e:
        print(f"❌ Lỗi kết nối Telegram: {e}")

if __name__ == "__main__":
    du_lieu = scrape_data()
    
    if du_lieu.get("anh_lich_hoc"):
        send_telegram_photo(du_lieu["anh_lich_hoc"], "📌 Lịch học tuần này của sếp! Chúc code vui vẻ! 💻")
        
    if du_lieu.get("anh_lich_thi"):
        send_telegram_photo(du_lieu["anh_lich_thi"], "🚨 ĐÃ CÓ LỊCH THI! Sếp lưu ảnh lại chuẩn bị ôn bài nhé! 📝🔥")

    if du_lieu.get("anh_hoc_phi"):
        send_telegram_photo(du_lieu["anh_hoc_phi"], du_lieu["tin_nhan_hoc_phi"])
