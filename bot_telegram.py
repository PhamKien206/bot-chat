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
            
            page.wait_for_timeout(5000)
            table_locator_hoc = page.locator('.table-bordered:visible').first
            page.wait_for_selector('.table-bordered:visible', timeout=30000)
            ket_qua["anh_lich_hoc"] = "anh_lich_hoc.png"
            table_locator_hoc.screenshot(path=ket_qua["anh_lich_hoc"])
            print("✅ Đã chụp xong lịch học!")

            # ================= 2. QUÉT LỊCH THI (MA TRẬN 3x3) =================
            print("📝 BƯỚC 3: Kiểm tra Lịch thi (Kích hoạt Ma trận quét sâu)...")
            page.goto('https://sinhvien1.tlu.edu.vn/#/search_exam_room_student/listing', timeout=60000)
            page.wait_for_timeout(5000) 
            
            co_lich_thi = False
            khung_chinh = page.locator('.page-content').first
            
            try:
                print("⏳ Đang dò tìm 9 tổ hợp Năm học & Học kỳ (Chính/Hè) để lôi lịch thi ra...")
                
                # Check giao diện mặc định trước
                noi_dung = khung_chinh.inner_text()
                if "Ngày thi" in noi_dung and "Không tìm thấy" not in noi_dung and "Không có" not in noi_dung:
                    co_lich_thi = True
                
                # Nếu chưa có, bật chế độ click ma trận 3x3
                if not co_lich_thi:
                    # Tuyệt chiêu: Chỉ lấy dropdown nằm TRONG khung nội dung chính để không bấm nhầm
                    dropdowns = khung_chinh.locator('.ui-select-match')
                    
                    if dropdowns.count() >= 2:
                        for i in range(3): # Duyệt 3 năm học gần nhất
                            if co_lich_thi: break
                            
                            try:
                                dropdowns.nth(0).click(timeout=3000)
                                page.wait_for_timeout(1000)
                                opts_nam = page.locator('.ui-select-choices-row:visible')
                                
                                if opts_nam.count() > i:
                                    opts_nam.nth(i).click()
                                    page.wait_for_timeout(3000) 
                                else:
                                    page.keyboard.press('Escape')
                                    break # Hết năm học
                            except:
                                page.keyboard.press('Escape')

                            for j in range(3): # Duyệt 3 loại học kỳ (Kỳ 1, Kỳ 2, Kỳ Hè)
                                if co_lich_thi: break
                                
                                try:
                                    dropdowns.nth(1).click(timeout=3000)
                                    page.wait_for_timeout(1000)
                                    opts_loai = page.locator('.ui-select-choices-row:visible')
                                    
                                    if opts_loai.count() > j:
                                        opts_loai.nth(j).click()
                                        page.wait_for_timeout(4000) # Đợi web tải lại bảng lịch thi
                                        
                                        # Soi bảng xem có lịch không
                                        noi_dung_moi = khung_chinh.inner_text()
                                        if "Ngày thi" in noi_dung_moi and "Ca thi" in noi_dung_moi and "Không tìm thấy" not in noi_dung_moi and "Không có" not in noi_dung_moi:
                                            co_lich_thi = True
                                            print(f"✅ BẮT ĐƯỢC RỒI! Đã lôi được lịch thi đang giấu ở Học kỳ cũ/kỳ Hè ra ngoài!")
                                            break
                                    else:
                                        page.keyboard.press('Escape')
                                        break
                                except:
                                    page.keyboard.press('Escape')

            except Exception as e:
                print("⚠️ Lỗi trong quá trình quét ma trận:", e)

            # Chụp ảnh nếu lôi được lịch ra
            if co_lich_thi:
                print(f"🚨 PHÁT HIỆN LỊCH THI! Đang chụp ảnh...")
                ket_qua["anh_lich_thi"] = "anh_lich_thi.png"
                vung_chup = page.locator('.portlet-body').last
                if not vung_chup.is_visible():
                    vung_chup = khung_chinh
                vung_chup.screenshot(path=ket_qua["anh_lich_thi"])
            else:
                print("✅ Đã lật tung 9 tổ hợp học kỳ nhưng hiện tại sếp không có lịch thi nào.")

            # ================= 3. KIỂM TRA HỌC PHÍ =================
            print("💰 BƯỚC 4: Tra cứu Học phí...")
            page.goto('https://sinhvien1.tlu.edu.vn/#/student_voucher_receive_pay/listing', timeout=60000)
            
            try:
                print("⏳ Đang kiên nhẫn đợi dữ liệu tiền học load (TỐI ĐA 30 GIÂY)...")
                tien_no_locator = page.wait_for_selector('strong.font-red', timeout=30000)
                
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
                print("✅ Đã đợi 30s không thấy khoản nợ (Đã đóng đủ tiền).")

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
        send_telegram_photo(du_lieu["anh_lich_thi"], "🚨 ĐÃ CÓ LỊCH THI KỲ HÈ/KỲ CŨ! Sếp lưu ảnh lại chuẩn bị ôn bài nhé! 📝🔥")

    if du_lieu.get("anh_hoc_phi"):
        send_telegram_photo(du_lieu["anh_hoc_phi"], du_lieu["tin_nhan_hoc_phi"])
