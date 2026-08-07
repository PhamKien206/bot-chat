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

            #  1. CÀO LỊCH HỌC 
            print("📅 BƯỚC 2: Vào trang Lịch học...")
            page.goto('https://sinhvien1.tlu.edu.vn/#/student/profile', timeout=60000)
            
            print("⏳ Đợi 10s cho web tải khung lịch học...")
            page.wait_for_timeout(10000) 
            
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
            
            print("⏳ Đợi 10s cho bảng tuần load xong chữ...")
            page.wait_for_timeout(10000)
            table_locator_hoc = page.locator('.table-bordered:visible').first
            page.wait_for_selector('.table-bordered:visible', timeout=30000)
            ket_qua["anh_lich_hoc"] = "anh_lich_hoc.png"
            table_locator_hoc.screenshot(path=ket_qua["anh_lich_hoc"])
            print("✅ Đã chụp xong lịch học!")

            #  2. QUÉT LỊCH THI 
            print("📝 BƯỚC 3: Kiểm tra Lịch thi (Bộ lọc Thời gian Thông minh)...")
            page.goto('https://sinhvien1.tlu.edu.vn/#/search_exam_room_student/listing', timeout=60000)
            
            print("⏳ Đợi 10s cho trang Lịch thi khởi động...")
            page.wait_for_timeout(10000) 
            
            co_lich_thi = False
            vn_tz = timezone(timedelta(hours=7))
            hom_nay = datetime.now(vn_tz).replace(hour=0, minute=0, second=0, microsecond=0)
            
            def kiem_tra_co_lich_tuong_lai():
                try:
                    so_dong = page.locator('.page-content table tbody tr').count()
                    if so_dong > 0:
                        chu_trong_bang = page.locator('.page-content table').inner_text()
                        if "Không tìm thấy" not in chu_trong_bang and "Không có" not in chu_trong_bang:
                            cac_ngay_thi = re.findall(r'(\d{1,2}/\d{1,2}/\d{4})', chu_trong_bang)
                            for ngay_str in cac_ngay_thi:
                                try:
                                    ngay_thi = datetime.strptime(ngay_str, '%d/%m/%Y').replace(tzinfo=vn_tz)
                                    # Nếu lịch thi lớn hơn hoặc bằng hôm nay -> Chốt!
                                    if ngay_thi >= hom_nay:
                                        print(f"👉 Chốt đơn môn thi sắp tới: {ngay_str}")
                                        return True
                                except:
                                    pass
                            print("⚠️ Bảng này toàn lịch thi cũ trong quá khứ, vứt!")
                except:
                    pass
                return False

            try:
                print("⏳ Đang soi màn hình mặc định xem có sẵn không...")
                if kiem_tra_co_lich_tuong_lai():
                    co_lich_thi = True
                
                if not co_lich_thi:
                    print("⏳ Đang ép bot đứng đợi các ô Dropdown hiện ra...")
                    try:
                        # Ép đợi 15 giây cho ô dropdown xuất hiện rồi mới đếm
                        page.wait_for_selector('.page-content .ui-select-match', timeout=15000)
                    except:
                        pass
                        
                    dropdowns = page.locator('.page-content .ui-select-match')
                    so_luong_o = dropdowns.count()
                    print(f"👉 Tìm thấy {so_luong_o} ô chọn trên màn hình!")
                    
                    if so_luong_o >= 2:
                        for i in range(4): # Lùi 4 năm học
                            if co_lich_thi: break
                            
                            print(f"🔍 Đang lục lọi Năm học thứ {i+1}...")
                            try:
                                # Mở Năm học
                                dropdowns.nth(0).click(timeout=5000)
                                page.wait_for_timeout(2000) 
                                
                                menu_mo = page.locator('.ui-select-container.open .ui-select-choices-row')
                                if menu_mo.count() > i:
                                    menu_mo.nth(i).click(timeout=5000)
                                    print(f"   ⏳ Đợi 10s cho dữ liệu Năm học load...")
                                    page.wait_for_timeout(10000) 
                                else:
                                    page.keyboard.press('Escape')
                                    break 
                                
                                # Bắn tỉa tên gọi
                                cac_loai_can_tim = ["Học kỳ chính", "Học kỳ hè"]
                                
                                for loai_ten in cac_loai_can_tim:
                                    if co_lich_thi: break
                                    
                                    print(f"   👉 Bắn tỉa mục tiêu: '{loai_ten}'")
                                    dropdowns.nth(1).click(timeout=5000)
                                    page.wait_for_timeout(2000)
                                    
                                    option_muc_tieu = page.locator('.ui-select-container.open .ui-select-choices-row').filter(has_text=loai_ten).first
                                    
                                    if option_muc_tieu.is_visible():
                                        option_muc_tieu.click(timeout=5000)
                                        print(f"   ⏳ Đợi 10s cho bảng '{loai_ten}' load...")
                                        page.wait_for_timeout(10000) 
                                        
                                        # Nếu có ô Đợt thi (ô số 3), bấm đợt mới nhất
                                        if dropdowns.count() >= 3:
                                            print("   👉 Đang cố định Đợt thi mới nhất...")
                                            dropdowns.nth(2).click(timeout=5000)
                                            page.wait_for_timeout(2000)
                                            dot_options = page.locator('.ui-select-container.open .ui-select-choices-row')
                                            if dot_options.count() > 0:
                                                dot_options.nth(0).click(timeout=5000)
                                                print("   ⏳ Đợi 10s cho Đợt thi load...")
                                                page.wait_for_timeout(10000)
                                            else:
                                                page.keyboard.press('Escape')

                                        # Kiểm tra lại xem bảng vừa load có lịch chuẩn không
                                        if kiem_tra_co_lich_tuong_lai():
                                            co_lich_thi = True
                                            print(f"✅ HOÀN HẢO! Đã moi ra lịch thi chuẩn rồi sếp ơi!")
                                            break
                                    else:
                                        page.keyboard.press('Escape')
                                        
                            except Exception as e:
                                page.keyboard.press('Escape')
                                pass
                    else:
                        print("⚠️ Lỗi: Không đếm đủ ô dropdown, có thể web bị lag!")

            except Exception as e:
                print("⚠️ Lỗi tổng thể quá trình quét:", e)

            # 3. Chụp ảnh
            if co_lich_thi:
                print(f"🚨 Đang nháy máy chụp ảnh Lịch thi...")
                ket_qua["anh_lich_thi"] = "anh_lich_thi.png"
                
                vung_chup = page.locator('.portlet-body').last
                if not vung_chup.is_visible():
                    vung_chup = page.locator('.page-content').first
                    
                vung_chup.screenshot(path=ket_qua["anh_lich_thi"])
            else:
                print("✅ Đã kiểm tra không bỏ sót ngóc ngách nào. Sếp có thể kê cao gối ngủ!")

            # 3. KIỂM TRA HỌC PHÍ 
            print("💰 BƯỚC 4: Tra cứu Học phí...")
            page.goto('https://sinhvien1.tlu.edu.vn/#/student_voucher_receive_pay/listing', timeout=60000)
            
            try:
                # Ép bot đứng yên 15 GIÂY cho tiền nợ kịp chui ra
                print("⏳ Đợi 15s cho dữ liệu tiền học load xong hoàn toàn...")
                page.wait_for_timeout(15000)
                
                tien_no_locator = page.wait_for_selector('strong.font-red', timeout=20000)
                
                chuoi_tien_no = tien_no_locator.inner_text().strip()
                so_tien = int(chuoi_tien_no.replace(',', '').replace('.', ''))
                
                if so_tien > 0:
                    print(f"🚨 CẢNH BÁO: Đang nợ {chuoi_tien_no} VNĐ!")
                    ket_qua["tin_nhan_hoc_phi"] = f"🚨 CẢNH BÁO HỌC PHÍ: {chuoi_tien_no} VNĐ"
                    ket_qua["anh_hoc_phi"] = "anh_hoc_phi.png"
                    page.locator('.portlet-body').first.screenshot(path=ket_qua["anh_hoc_phi"])
                else:
                    print("✅ Số nợ = 0. Đã đóng đủ tiền!")
            except Exception as e:
                print("✅ Đã đợi mỏi mòn không thấy khoản nợ. Sếp đã đóng đủ tiền!")

            # KẾT THÚC 
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
        send_telegram_photo(du_lieu["anh_lich_hoc"], "📌 Lịch học tuần này ")
        
    if du_lieu.get("anh_lich_thi"):
        send_telegram_photo(du_lieu["anh_lich_thi"], "🚨 ĐÃ CÓ LỊCH THI MỚI")

    if du_lieu.get("anh_hoc_phi"):
        send_telegram_photo(du_lieu["anh_hoc_phi"], du_lieu["tin_nhan_hoc_phi"])
