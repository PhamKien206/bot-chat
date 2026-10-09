"""
Bot Telegram báo Lịch học / Lịch thi / Học phí từ cổng sinh viên TLU.

Chạy bởi GitHub Actions mỗi sáng (xem .github/workflows/run_bot.yml):
    python bot_telegram.py tuan   Thứ Hai: quét lịch học + lịch thi + học phí, lưu lịch cả tuần,
                                  rồi báo lịch hôm nay.
    python bot_telegram.py ngay   Các ngày khác: đọc lịch đã lưu, báo hôm nay học gì / nghỉ.
                                  Không mở trình duyệt.
    python bot_telegram.py hoc    Dự phòng khi chưa có lịch tuần đã lưu: chỉ quét lịch học.
    python bot_telegram.py auto   Tự chọn 1 trong 3 chế độ trên (mặc định).
    python bot_telegram.py --che-do   Chỉ in chế độ sẽ chạy (workflow dùng để quyết định
                                      có cần cài trình duyệt hay không).

Biến môi trường cần có:
    TELE_BOT_TOKEN, TELE_CHAT_ID, MSV, PASS_TRUONG
Tùy chọn:
    BOT_STATE_DIR   thư mục lưu trạng thái giữa các lần chạy (mặc định .bot_state)
    TLU_BASE_URL    đổi địa chỉ web (dùng khi test)
"""

import hashlib
import json
import os
import re
import sys
import time
from datetime import date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import requests

try:
    from playwright.sync_api import TimeoutError as PWTimeout
    from playwright.sync_api import sync_playwright
except ImportError:   # chế độ "ngay" không cần cài Playwright
    PWTimeout = sync_playwright = None


# =========================================================
# CẤU HÌNH
# =========================================================

BASE_URL = os.environ.get("TLU_BASE_URL", "https://sinhvien1.tlu.edu.vn").rstrip("/")
URL_LOGIN = f"{BASE_URL}/#/login"
URL_LICH_HOC = f"{BASE_URL}/#/student/profile"
URL_LICH_THI = f"{BASE_URL}/#/search_exam_room_student/listing"
URL_HOC_PHI = f"{BASE_URL}/#/student_voucher_receive_pay/listing"

VN_TZ = ZoneInfo("Asia/Ho_Chi_Minh")

# Timeout (ms)
TIMEOUT_NAV = 60_000
TIMEOUT_ELEMENT = 30_000
TIMEOUT_CLICK = 12_000
TIMEOUT_DOI_NOI_DUNG = 10_000   # chờ bảng đổi sau khi chọn dropdown
TIMEOUT_HOC_PHI = 15_000        # chờ ô tiền nợ (không có = không nợ)

# Retry
MAX_RETRY_SCRAPE = 2            # thử lại cả phiên (mở browser + đăng nhập)
MAX_RETRY_MUC = 2               # thử lại từng mục (lịch học / thi / học phí)
MAX_RETRY_TELEGRAM = 3

# Lịch thi: chỉ xét năm học mới nhất và năm liền trước.
# Năm học cũ hơn không thể có lịch thi tương lai.
SO_NAM_HOC_THI = 2
LOAI_HOC_KY = ("Học kỳ chính", "Học kỳ hè")

# Ảnh rõ hơn khi xem trên điện thoại.
DEVICE_SCALE = 1.5

STATE_DIR = Path(os.environ.get("BOT_STATE_DIR", ".bot_state"))
STATE_FILE = STATE_DIR / "state.json"

# Selector dùng chung
SEL_DROPDOWN = ".page-content .ui-select-match"
SEL_OPTION = ".ui-select-container.open .ui-select-choices-row"
CSS_BANG_LICH_HOC = ".table-bordered"
CSS_BANG_THI = ".page-content table"


class LoiDangNhap(Exception):
    """Sai tài khoản/mật khẩu: không retry để tránh bị khóa tài khoản."""


# =========================================================
# TIỆN ÍCH CHUNG
# =========================================================

def log(msg=""):
    print(msg, flush=True)


def tach_khoang_ngay(text):
    """'Tuần 5 (24/08/2026 - 30/08/2026)' -> (date(2026,8,24), date(2026,8,30)) hoặc None."""
    m = re.search(
        r"\((\d{1,2}/\d{1,2}/\d{4})\s*-\s*(\d{1,2}/\d{1,2}/\d{4})\)",
        text or "",
    )
    if not m:
        return None
    try:
        return tuple(datetime.strptime(s, "%d/%m/%Y").date() for s in m.groups())
    except ValueError:
        return None


def chua_ngay(text, ngay):
    khoang = tach_khoang_ngay(text)
    return bool(khoang) and khoang[0] <= ngay <= khoang[1]


def cac_ngay_trong(text):
    """Mọi ngày dd/mm/yyyy hợp lệ có trong chuỗi."""
    ket_qua = []
    for s in re.findall(r"\b\d{1,2}/\d{1,2}/\d{4}\b", text or ""):
        try:
            ket_qua.append(datetime.strptime(s, "%d/%m/%Y").date())
        except ValueError:
            pass
    return ket_qua


# "T2", "Thứ 2", "Thứ hai", "CN", "Chủ nhật" -> 0..6 (thứ Hai = 0)
_THU_CHU = {"hai": 0, "ba": 1, "tư": 2, "tu": 2, "năm": 3, "nam": 3, "sáu": 4, "sau": 4, "bảy": 5, "bay": 5}


def thu_cua_cot(tieu_de):
    t = (tieu_de or "").strip().lower()
    if re.match(r"^(cn|chủ\s*nhật|chu\s*nhat)\b", t):
        return 6
    m = re.match(r"^(?:t|thứ|thu)\s*\.?\s*([2-7])\b", t)
    if m:
        return int(m.group(1)) - 2
    m = re.match(r"^(?:thứ|thu)\s+(\w+)", t)
    if m and m.group(1) in _THU_CHU:
        return _THU_CHU[m.group(1)]
    return None


def lam_gon(text):
    """Gộp khoảng trắng, bỏ dòng trống."""
    dong = [re.sub(r"\s+", " ", d).strip() for d in (text or "").splitlines()]
    return "\n".join(d for d in dong if d)


def phan_tich_lich_tuan(bang, dau_tuan):
    """
    Bảng (tieu_de + luoi) -> {"YYYY-MM-DD": [{"ca": ..., "mon": ...}, ...]} cho 7 ngày trong tuần.

    - Cột ngày nhận ra theo tiêu đề (T2..CN / Thứ 2.. / Chủ nhật). Không nhận ra được thì
      coi 7 cột cuối là thứ Hai..Chủ nhật.
    - Các cột còn lại (Ca / Buổi / Tiết...) ghép thành nhãn của hàng.
    - Một môn kéo dài nhiều hàng liền nhau (lặp nội dung) được gộp làm một.
    """
    tieu_de = bang.get("tieu_de") or []
    luoi = bang.get("luoi") or []
    so_cot = max((len(h) for h in luoi), default=0)

    cot_thu = {i: thu_cua_cot(t) for i, t in enumerate(tieu_de)}
    cot_thu = {i: d for i, d in cot_thu.items() if d is not None}
    if len(cot_thu) < 5 and so_cot >= 7:
        cot_thu = {so_cot - 7 + k: k for k in range(7)}
    cot_nhan = [i for i in range(so_cot) if i not in cot_thu]

    lich = {(dau_tuan + timedelta(days=k)).isoformat(): [] for k in range(7)}
    nhan_cu = {}
    cuoi = {}   # thứ -> (chỉ số hàng, mục) của môn gần nhất, để gộp hàng liền nhau
    for r, hang in enumerate(luoi):
        phan = []
        for c in cot_nhan:
            o = hang[c] if c < len(hang) else ""
            if o is None:            # ô nhãn bị kéo dài từ hàng trên
                o = nhan_cu.get(c, "")
            nhan_cu[c] = o
            if o:
                phan.append(lam_gon(o).replace("\n", " "))
        nhan = " · ".join(phan)
        nhan_cuoi = phan[-1] if phan else ""   # vd "Tiết 3" (bỏ "Sáng ·" lặp lại)

        for c, thu in cot_thu.items():
            o = hang[c] if c < len(hang) else ""
            truoc = cuoi.get(thu)
            noi_tiep = truoc and truoc[0] == r - 1
            # Môn kéo dài xuống hàng này: ô gộp (None) hoặc lặp đúng nội dung hàng trên.
            if noi_tiep and (o is None or (o and lam_gon(o) == truoc[1]["mon"])):
                dau = truoc[1]["ca"].split(" → ")[0]
                if nhan_cuoi and nhan_cuoi != dau:
                    truoc[1]["ca"] = f"{dau} → {nhan_cuoi}"
                cuoi[thu] = (r, truoc[1])
                continue
            if not o:
                continue
            mon = lam_gon(o)
            ngay = (dau_tuan + timedelta(days=thu)).isoformat()
            muc = {"ca": nhan, "mon": mon}
            lich[ngay].append(muc)
            cuoi[thu] = (r, muc)
    return lich


TEN_THU = ("Thứ Hai", "Thứ Ba", "Thứ Tư", "Thứ Năm", "Thứ Sáu", "Thứ Bảy", "Chủ Nhật")


def tin_lich_hom_nay(lich_tuan, ngay):
    """Tin nhắn báo lịch học của ngày `ngay` từ lịch tuần đã lưu."""
    tieu_de = f"{TEN_THU[ngay.weekday()]} {ngay:%d/%m/%Y}"
    if lich_tuan.get("ngay") is None:
        return None   # không đọc được chi tiết -> nơi gọi gửi ảnh lịch tuần
    cac_mon = lich_tuan["ngay"].get(ngay.isoformat(), [])
    if not cac_mon:
        return f"😴 {tieu_de}\nHôm nay không có lịch học. Nghỉ!"
    phan = [f"☀️ {tieu_de}", f"📚 Hôm nay có {len(cac_mon)} buổi học:"]
    for m in cac_mon:
        dau = f"🕘 {m['ca']}\n" if m.get("ca") else ""
        phan.append(f"{dau}{m['mon']}")
    return "\n\n".join(phan)


def hom_nay():
    return datetime.now(VN_TZ).date()


def ngan_gon(loi):
    """Dòng đầu của thông báo lỗi (bỏ phần 'Call log' dài dòng của Playwright)."""
    dong = str(loi).strip().splitlines()
    return dong[0] if dong else type(loi).__name__


def link_lan_chay():
    """Link tới lần chạy GitHub Actions hiện tại (nếu có)."""
    server = os.environ.get("GITHUB_SERVER_URL")
    repo = os.environ.get("GITHUB_REPOSITORY")
    run_id = os.environ.get("GITHUB_RUN_ID")
    if server and repo and run_id:
        return f"{server}/{repo}/actions/runs/{run_id}"
    return ""


# =========================================================
# TIỆN ÍCH PLAYWRIGHT
# =========================================================

# Tìm phần tử ĐANG HIỂN THỊ đầu tiên khớp CSS (querySelector không hiểu :visible).
_JS_TIM = "sel => [...document.querySelectorAll(sel)].find(e => e.getClientRects().length > 0)"

# Đọc innerText của bảng đang hiển thị. Trả null khi chưa có bảng hoặc tbody đang
# trống: Angular thường XÓA bảng rồi mới đổ dữ liệu mới, lúc trống không được tính
# là "đã cập nhật".
JS_DOC_TEXT = f"""sel => {{
    const el = ({_JS_TIM})(sel);
    if (!el) return null;
    const tb = el.querySelector('tbody');
    if (tb && !tb.querySelector('tr')) return null;
    return el.innerText;
}}"""

JS_BANG_CO_MON = f"""sel => {{
    const t = ({_JS_TIM})(sel);
    if (!t) return null;
    return [...t.querySelectorAll('tbody tr')].some(tr =>
        [...tr.querySelectorAll('td')].slice(1).some(td => td.innerText.trim() !== '')
    );
}}"""


# Đọc bảng lịch học thành lưới ô (đã xử lý rowspan/colspan).
# Ô bị ô phía trên/bên trái kéo dài vào được đánh dấu null để không lặp môn.
JS_DOC_BANG = f"""sel => {{
    const t = ({_JS_TIM})(sel);
    if (!t) return null;
    const hang_tieu_de = t.querySelector('thead tr:last-child');
    const tieu_de = hang_tieu_de
        ? [...hang_tieu_de.children].flatMap(c => Array(c.colSpan || 1).fill(c.innerText.trim()))
        : [];
    const luoi = [];
    [...t.querySelectorAll('tbody tr')].forEach((tr, r) => {{
        luoi[r] = luoi[r] || [];
        let c = 0;
        for (const td of tr.children) {{
            while (luoi[r][c] !== undefined) c++;
            const rs = td.rowSpan || 1, cs = td.colSpan || 1, text = td.innerText.trim();
            for (let i = 0; i < rs; i++) for (let j = 0; j < cs; j++) {{
                luoi[r + i] = luoi[r + i] || [];
                luoi[r + i][c + j] = (i === 0 && j === 0) ? text : null;
            }}
            c += cs;
        }}
    }});
    return {{tieu_de, luoi: luoi.map(h => Array.from(h, x => x === undefined ? '' : x))}};
}}"""


def doc_text(page, css):
    try:
        return page.evaluate(JS_DOC_TEXT, css)
    except Exception:
        return None


def cho_text_on_dinh(page, css, timeout=5_000, khoang=200):
    """Chờ innerText của phần tử giống nhau ở 2 lần đo liên tiếp (bảng render xong)."""
    het_gio = time.monotonic() + timeout / 1000
    truoc = doc_text(page, css)
    while time.monotonic() < het_gio:
        page.wait_for_timeout(khoang)
        sau = doc_text(page, css)
        if sau is not None and sau == truoc:
            return True
        truoc = sau
    return False


class TheoDoiMang:
    """Đếm request API (xhr/fetch) để biết Angular đã tải xong dữ liệu chưa."""

    def __init__(self, page):
        self.tong = 0
        self.dang_cho = 0
        page.on("request", self._bat_dau)
        page.on("requestfinished", self._xong)
        page.on("requestfailed", self._xong)

    @staticmethod
    def _la_api(req):
        return req.resource_type in ("xhr", "fetch")

    def _bat_dau(self, req):
        if self._la_api(req):
            self.tong += 1
            self.dang_cho += 1

    def _xong(self, req):
        if self._la_api(req):
            self.dang_cho = max(0, self.dang_cho - 1)


_mang = None   # TheoDoiMang của page hiện tại


def cho_bang_cap_nhat(page, css, noi_dung_cu, so_request_truoc):
    """
    Sau khi đổi dropdown, dừng chờ ngay khi:
      - bảng đã có nội dung mới, hoặc
      - request API đã xong (dữ liệu mới có thể giống hệt dữ liệu cũ), hoặc
      - 1.5s mà không có request nào (lựa chọn không cần tải dữ liệu).
    Không phải ngồi chờ hết timeout khi bảng không đổi như trước.
    Sau đó chờ bảng render ổn định.
    """
    bat_dau = time.monotonic()
    het_gio = bat_dau + TIMEOUT_DOI_NOI_DUNG / 1000
    while time.monotonic() < het_gio:
        text = doc_text(page, css)
        if text is not None:
            if text != noi_dung_cu:
                break
            if _mang is not None:
                co_request = _mang.tong > so_request_truoc
                if co_request and _mang.dang_cho == 0:
                    break
                if not co_request and time.monotonic() - bat_dau > 1.5:
                    break
        page.wait_for_timeout(100)
    cho_text_on_dinh(page, css)


def so_request():
    return _mang.tong if _mang is not None else 0


def cho_danh_sach_on_dinh(page, selector, timeout):
    """Chờ số phần tử > 0 và không đổi qua 2 lần đo (ng-repeat render dần)."""
    het_gio = time.monotonic() + timeout / 1000
    truoc, lan_on_dinh = -1, 0
    while time.monotonic() < het_gio:
        so = page.locator(selector).count()
        if so > 0 and so == truoc:
            lan_on_dinh += 1
            if lan_on_dinh >= 2:
                return True
        else:
            lan_on_dinh = 0
        truoc = so
        page.wait_for_timeout(150)
    return truoc > 0


# Angular đổi trang bằng #hash: giao diện trang cũ còn nằm lại một lúc. Đánh dấu
# các phần tử cũ trước khi chuyển, rồi chờ chúng biến mất để không đọc nhầm bảng/
# dropdown của trang trước.
JS_DANH_DAU_CU = """() => document
    .querySelectorAll('.page-content table, .page-content .ui-select-match, .portlet-body')
    .forEach(e => e.setAttribute('data-bot-cu', ''))"""
JS_HET_TRANG_CU = "() => !document.querySelector('[data-bot-cu]')"


def mo_trang(page, url, lan_thu=3):
    for i in range(1, lan_thu + 1):
        try:
            try:
                page.evaluate(JS_DANH_DAU_CU)
            except Exception:
                pass
            page.goto(url, wait_until="domcontentloaded", timeout=TIMEOUT_NAV)
            try:
                page.wait_for_function(JS_HET_TRANG_CU, timeout=10_000)
            except PWTimeout:
                pass   # web giữ nguyên khung cũ: vẫn tiếp tục như bình thường
            return
        except Exception as e:
            log(f"⚠️ Mở trang lỗi ({i}/{lan_thu}): {e}")
            if i < lan_thu:
                page.wait_for_timeout(3_000)
    raise RuntimeError(f"Không mở được {url}")


def dong_dropdown(page):
    try:
        page.keyboard.press("Escape")
    except Exception:
        pass


def mo_dropdown(page, dropdown):
    """Click mở dropdown, trả về locator các option."""
    dropdown.click(timeout=TIMEOUT_CLICK)
    if not cho_danh_sach_on_dinh(page, SEL_OPTION, TIMEOUT_ELEMENT):
        dong_dropdown(page)
        raise RuntimeError("Dropdown không load được danh sách lựa chọn.")
    return page.locator(SEL_OPTION)


def chon_dropdown(page, index, css_bang, text=None, vi_tri=None):
    """
    Chọn một option ở dropdown thứ `index` (theo text hoặc vị trí) rồi chờ bảng cập nhật.
    Nếu option đó đang được chọn sẵn thì bỏ qua, không phải chờ bảng đổi.
    Trả về True nếu bảng đang hiển thị đúng lựa chọn.
    """
    dropdowns = page.locator(SEL_DROPDOWN)
    if dropdowns.count() <= index:
        return False

    dropdown = dropdowns.nth(index)
    try:
        dang_chon = dropdown.inner_text().strip()
        cu = doc_text(page, css_bang)
        options = mo_dropdown(page, dropdown)
        opt = options.filter(has_text=text).first if text is not None else options.nth(vi_tri)

        if opt.count() == 0:
            dong_dropdown(page)
            return False

        if opt.inner_text().strip() == dang_chon:
            dong_dropdown(page)
            return True

        truoc = so_request()
        opt.click(timeout=TIMEOUT_CLICK)
    except Exception as e:
        log(f"⚠️ Lỗi chọn dropdown #{index}: {e}")
        dong_dropdown(page)
        return False

    cho_bang_cap_nhat(page, css_bang, cu, truoc)
    return True


def chup(locator, path):
    locator.screenshot(path=path, animations="disabled")
    return path


def chup_debug(page, ten):
    path = f"debug_{ten}.png"
    try:
        page.screenshot(path=path, full_page=True)
        return path
    except Exception:
        return None


# =========================================================
# ĐĂNG NHẬP
# =========================================================

# Câu báo lỗi thường gặp ở form đăng nhập. Chỉ tính câu XUẤT HIỆN SAU khi bấm
# Đăng nhập, để chữ có sẵn trên trang không gây báo nhầm.
JS_KET_QUA_DANG_NHAP = r"""before => {
    if (!location.hash.includes('/login')) return 'ok';
    const t = document.body.innerText.toLowerCase();
    const m = t.match(/không đúng|không chính xác|sai mật khẩu|sai tài khoản|incorrect|invalid/);
    return (m && !before.includes(m[0])) ? 'sai' : false;
}"""


def dang_nhap(page, msv, password):
    log("🚀 Đăng nhập...")
    mo_trang(page, URL_LOGIN)
    page.wait_for_selector("#username", timeout=TIMEOUT_ELEMENT)
    page.fill("#username", msv)
    page.fill("#password", password)

    truoc = page.evaluate("() => document.body.innerText.toLowerCase()")
    page.click('button:has-text("Đăng nhập")', timeout=TIMEOUT_CLICK)

    try:
        ket_qua = page.wait_for_function(
            JS_KET_QUA_DANG_NHAP, arg=truoc, timeout=TIMEOUT_NAV
        ).json_value()
    except PWTimeout:
        raise RuntimeError("Đăng nhập quá thời gian chờ (web trường chậm?).")

    if ket_qua == "sai":
        raise LoiDangNhap("Sai MSV hoặc mật khẩu.")
    log("✅ Đăng nhập thành công")


# =========================================================
# LỊCH HỌC
# =========================================================

def cao_lich_hoc(page):
    """
    Chỉ xử lý ĐÚNG tuần chứa hôm nay (giờ VN).
    Trả về:
        {"tuan": "YYYY-MM-DD" (thứ Hai), "ngay": {ngày: [môn...]}, "anh": path}  có lịch
        {"tuan": ..., "ngay": {}, "tin": "..."}                                   cả tuần nghỉ
    """
    log("📅 Lịch học...")
    mo_trang(page, URL_LICH_HOC)

    tab_bang = page.locator('a:has-text("Bảng")').first
    tab_bang.wait_for(state="visible", timeout=TIMEOUT_ELEMENT)
    tab_bang.click(timeout=TIMEOUT_CLICK)

    ngay = hom_nay()
    dau_tuan = ngay - timedelta(days=ngay.weekday())
    cuoi_tuan = dau_tuan + timedelta(days=6)
    khoang = f"({dau_tuan:%d/%m} - {cuoi_tuan:%d/%m})"
    nghi = {
        "tuan": dau_tuan.isoformat(),
        "ngay": {},
        "tin": f"🎉 Tuần này {khoang} không có lịch học nào. Nghỉ!",
    }

    dropdown_tuan = (
        page.locator("label").filter(has_text="Tuần")
        .locator("..").locator(".ui-select-match")
    )
    dropdown_tuan.wait_for(state="visible", timeout=TIMEOUT_ELEMENT)

    if chua_ngay(dropdown_tuan.inner_text(), ngay):
        # Web đang mở sẵn đúng tuần này.
        page.locator(f"{CSS_BANG_LICH_HOC}:visible").first.wait_for(timeout=TIMEOUT_ELEMENT)
        cho_text_on_dinh(page, CSS_BANG_LICH_HOC)
    else:
        cu = doc_text(page, CSS_BANG_LICH_HOC)
        options = mo_dropdown(page, dropdown_tuan)
        # Lấy text mọi option trong 1 lần gọi thay vì đọc từng dòng.
        vi_tri = next(
            (i for i, t in enumerate(options.all_inner_texts()) if chua_ngay(t, ngay)),
            None,
        )
        if vi_tri is None:
            # Danh sách tuần không có tuần này -> chưa có lịch. KHÔNG chụp tuần khác.
            dong_dropdown(page)
            log("ℹ️ Không có tuần chứa hôm nay trong danh sách.")
            return nghi

        truoc = so_request()
        options.nth(vi_tri).click(timeout=TIMEOUT_CLICK)
        cho_bang_cap_nhat(page, CSS_BANG_LICH_HOC, cu, truoc)

        # Xác minh lại trước khi chụp để không bao giờ gửi nhầm tuần.
        sau = dropdown_tuan.inner_text()
        if not chua_ngay(sau, ngay):
            raise RuntimeError(f"Web không chuyển sang tuần hiện tại (đang: {sau.strip()}).")

    co_mon = page.evaluate(JS_BANG_CO_MON, CSS_BANG_LICH_HOC)
    if co_mon is None:
        raise RuntimeError("Không thấy bảng lịch học.")
    if not co_mon:
        return nghi

    bang = page.evaluate(JS_DOC_BANG, CSS_BANG_LICH_HOC)
    lich = phan_tich_lich_tuan(bang or {}, dau_tuan)
    so_buoi = sum(len(v) for v in lich.values())
    if so_buoi == 0:
        # Bảng có môn nhưng không tách được theo ngày: KHÔNG lưu là "nghỉ".
        # Các ngày sau bot sẽ gửi lại ảnh lịch tuần thay vì tin chữ.
        log("⚠️ Không tách được lịch theo ngày, sẽ dùng ảnh lịch tuần.")
        lich = None

    path = chup(page.locator(f"{CSS_BANG_LICH_HOC}:visible").first, "anh_lich_hoc.png")
    log(f"✅ Đã chụp và đọc lịch học tuần này ({so_buoi} buổi)")
    return {"tuan": dau_tuan.isoformat(), "ngay": lich, "anh": path}


# =========================================================
# LỊCH THI
# =========================================================

def dong_thi_sap_toi(page, ngay):
    """Các dòng trong bảng thi có ngày >= hôm nay."""
    try:
        rows = page.locator(f"{CSS_BANG_THI} tbody tr").all_inner_texts()
    except Exception:
        return []
    return [r.strip() for r in rows if any(d >= ngay for d in cac_ngay_trong(r))]


def cao_lich_thi(page):
    """Trả về {"anh": path, "hash": ...} nếu có lịch thi sắp tới, ngược lại None."""
    log("📝 Lịch thi...")
    mo_trang(page, URL_LICH_THI)
    page.wait_for_selector(".page-content", timeout=TIMEOUT_ELEMENT)
    cho_text_on_dinh(page, CSS_BANG_THI)

    ngay = hom_nay()
    dong = dong_thi_sap_toi(page, ngay)

    if not dong:
        try:
            page.locator(SEL_DROPDOWN).first.wait_for(timeout=TIMEOUT_ELEMENT)
        except PWTimeout:
            raise RuntimeError("Trang lịch thi không load được ô chọn.")

        if page.locator(SEL_DROPDOWN).count() < 2:
            raise RuntimeError("Trang lịch thi thiếu ô chọn Năm học/Học kỳ.")

        for i in range(SO_NAM_HOC_THI):
            if dong or not chon_dropdown(page, 0, CSS_BANG_THI, vi_tri=i):
                break
            for loai in LOAI_HOC_KY:
                if not chon_dropdown(page, 1, CSS_BANG_THI, text=loai):
                    continue
                if page.locator(SEL_DROPDOWN).count() >= 3:
                    chon_dropdown(page, 2, CSS_BANG_THI, vi_tri=0)   # đợt thi mới nhất
                dong = dong_thi_sap_toi(page, ngay)
                if dong:
                    break

    if not dong:
        log("✅ Không có lịch thi sắp tới")
        return None

    vung = page.locator(".portlet-body:visible").last
    if vung.count() == 0:
        vung = page.locator(".page-content").first
    path = chup(vung, "anh_lich_thi.png")

    dau_van_tay = hashlib.sha256("\n".join(sorted(dong)).encode()).hexdigest()
    log(f"✅ Có {len(dong)} môn thi sắp tới, đã chụp")
    return {"anh": path, "hash": dau_van_tay}


# =========================================================
# HỌC PHÍ
# =========================================================

def kiem_tra_hoc_phi(page):
    """Trả về {"anh": path, "tin": ...} nếu còn nợ, ngược lại None."""
    log("💰 Học phí...")
    mo_trang(page, URL_HOC_PHI)
    page.wait_for_selector(".page-content", timeout=TIMEOUT_ELEMENT)

    o_tien = page.locator("strong.font-red").first
    try:
        o_tien.wait_for(state="visible", timeout=TIMEOUT_HOC_PHI)
    except PWTimeout:
        log("✅ Không thấy khoản nợ")
        return None

    chuoi = o_tien.inner_text().strip()
    so = re.sub(r"\D", "", chuoi)
    if not so:
        raise RuntimeError(f"Không đọc được số tiền nợ: {chuoi!r}")
    if int(so) <= 0:
        log("✅ Không còn nợ")
        return None

    vung = page.locator(".portlet-body:visible").first
    if vung.count() == 0:
        vung = page.locator(".page-content").first
    path = chup(vung, "anh_hoc_phi.png")

    # Không in số tiền ra log: log GitHub Actions của repo public ai cũng xem được.
    log("🚨 Còn khoản học phí chưa đóng, đã chụp")
    return {"anh": path, "tin": f"🚨 CẢNH BÁO HỌC PHÍ: {chuoi} VNĐ"}


# =========================================================
# CÀO DỮ LIỆU
# =========================================================

CAC_MUC = (
    ("lich_hoc", "Lịch học", cao_lich_hoc),
    ("lich_thi", "Lịch thi", cao_lich_thi),
    ("hoc_phi", "Học phí", kiem_tra_hoc_phi),
)


def chay_muc(page, khoa, ten, ham):
    """
    Chạy một mục có retry. Mỗi lần thử lại bắt đầu từ trang trắng để Angular load mới
    hoàn toàn (goto cùng URL chỉ khác #hash thì trình duyệt KHÔNG tải lại trang).
    """
    for i in range(1, MAX_RETRY_MUC + 1):
        try:
            if i > 1:
                page.goto("about:blank")
            return ham(page)
        except Exception as e:
            log(f"⚠️ {ten} lỗi ({i}/{MAX_RETRY_MUC}): {e}")
            loi = e
    raise RuntimeError(f"{ten}: {ngan_gon(loi)}") from loi


def scrape_mot_lan(msv, password, cac_muc):
    global _mang
    ket_qua = {"loi": []}

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True, args=["--disable-dev-shm-usage"])
        try:
            context = browser.new_context(
                viewport={"width": 1920, "height": 1080},
                device_scale_factor=DEVICE_SCALE,
                locale="vi-VN",
                timezone_id="Asia/Ho_Chi_Minh",
            )
            page = context.new_page()
            _mang = TheoDoiMang(page)
            page.set_default_timeout(TIMEOUT_ELEMENT)
            page.set_default_navigation_timeout(TIMEOUT_NAV)

            try:
                dang_nhap(page, msv, password)
            except Exception:
                chup_debug(page, "dang_nhap")
                raise

            # Mỗi mục độc lập: một mục hỏng không làm mất kết quả của mục khác,
            # cũng không phải đăng nhập lại từ đầu.
            for khoa, ten, ham in cac_muc:
                try:
                    ket_qua[khoa] = chay_muc(page, khoa, ten, ham)
                except Exception as e:
                    ket_qua["loi"].append((str(e), chup_debug(page, khoa)))
                    dong_dropdown(page)
        finally:
            browser.close()

    return ket_qua


def scrape_data(msv, password, cac_muc=CAC_MUC):
    """Retry cả phiên chỉ khi lỗi ở mức trình duyệt/đăng nhập. Sai mật khẩu thì dừng ngay."""
    loi = None
    for i in range(1, MAX_RETRY_SCRAPE + 1):
        log(f"\n===== PHIÊN {i}/{MAX_RETRY_SCRAPE} =====")
        try:
            return scrape_mot_lan(msv, password, cac_muc)
        except LoiDangNhap:
            raise
        except Exception as e:
            loi = e
            log(f"❌ Phiên {i} lỗi: {e}")
            if i < MAX_RETRY_SCRAPE:
                time.sleep(10)
    raise RuntimeError(f"Thử {MAX_RETRY_SCRAPE} phiên đều lỗi: {ngan_gon(loi)}")


# =========================================================
# TRẠNG THÁI GIỮA CÁC LẦN CHẠY
# =========================================================

def doc_state():
    try:
        return json.loads(STATE_FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def ghi_state(state):
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    STATE_FILE.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")


# =========================================================
# TELEGRAM
# =========================================================

class Telegram:
    def __init__(self, token, chat_id):
        self.base = f"https://api.telegram.org/bot{token}"
        self.chat_id = chat_id
        self.session = requests.Session()

    def _goi(self, method, data, files=None):
        data = {"chat_id": self.chat_id, **data}
        for i in range(1, MAX_RETRY_TELEGRAM + 1):
            cho = 3 * i
            try:
                r = self.session.post(f"{self.base}/{method}", data=data, files=files, timeout=30)
                if r.ok:
                    return True
                if r.status_code == 429:
                    try:
                        cho = int(r.json()["parameters"]["retry_after"]) + 1
                    except Exception:
                        pass
                elif r.status_code < 500:
                    # Lỗi do request (chat_id sai, ảnh quá lớn...): thử lại cũng vô ích.
                    log(f"❌ Telegram {method} lỗi {r.status_code}: {r.text}")
                    return False
                log(f"⚠️ Telegram {r.status_code}, thử lại sau {cho}s")
            except requests.RequestException as e:
                log(f"⚠️ Lỗi mạng Telegram ({i}/{MAX_RETRY_TELEGRAM}): {e}")
            if i < MAX_RETRY_TELEGRAM:
                time.sleep(cho)
        log(f"❌ Gửi Telegram {method} thất bại")
        return False

    def tin(self, text):
        return self._goi("sendMessage", {"text": text[:4096]})

    def anh(self, path, caption=""):
        try:
            noi_dung = Path(path).read_bytes()
        except OSError as e:
            log(f"❌ Không đọc được ảnh {path}: {e}")
            return False
        return self._goi(
            "sendPhoto",
            {"caption": caption[:1024]},
            files={"photo": (Path(path).name, noi_dung, "image/png")},
        )


# =========================================================
# CHẾ ĐỘ CHẠY
# =========================================================

CAC_CHE_DO = ("tuan", "ngay", "hoc")
ANH_LICH_TUAN = "lich_tuan.png"   # ảnh lịch tuần lưu trong STATE_DIR


def dau_tuan_cua(ngay):
    return ngay - timedelta(days=ngay.weekday())


def co_lich_tuan_nay(state, ngay):
    return (state.get("lich_tuan") or {}).get("tuan") == dau_tuan_cua(ngay).isoformat()


def chon_che_do(yeu_cau, state, ngay):
    """
    tuan: thứ Hai (hoặc khi bấm chạy tay chọn tuan)
    ngay: đã có lịch tuần này được lưu -> chỉ đọc và báo
    hoc : chưa có lịch tuần này (thứ Hai lỗi / cache bị xóa) -> quét riêng lịch học
    """
    if yeu_cau in CAC_CHE_DO:
        return yeu_cau
    if ngay.weekday() == 0:
        return "tuan"
    return "ngay" if co_lich_tuan_nay(state, ngay) else "hoc"


def bao_lich_hom_nay(tg, state, ngay):
    lich_tuan = state.get("lich_tuan") or {}
    tin = tin_lich_hom_nay(lich_tuan, ngay)
    if tin is not None:
        return tg.tin(tin)
    anh = STATE_DIR / ANH_LICH_TUAN
    if anh.exists():
        return tg.anh(str(anh), f"📌 {TEN_THU[ngay.weekday()]} {ngay:%d/%m}: xem lịch hôm nay trong ảnh tuần")
    return tg.tin(f"⚠️ {TEN_THU[ngay.weekday()]} {ngay:%d/%m}: không đọc được lịch hôm nay.")


def luu_lich_tuan(state, lh):
    state["lich_tuan"] = {"tuan": lh["tuan"], "ngay": lh["ngay"]}
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    anh = STATE_DIR / ANH_LICH_TUAN
    if lh.get("anh"):
        anh.write_bytes(Path(lh["anh"]).read_bytes())
    elif anh.exists():
        anh.unlink()


# =========================================================
# MAIN
# =========================================================

def main(argv):
    ngay = hom_nay()
    state = doc_state()
    yeu_cau = next((a for a in argv if not a.startswith("-")), "auto")
    che_do = chon_che_do(yeu_cau, state, ngay)

    if "--che-do" in argv:
        # Workflow dùng để biết có cần cài Playwright + Chromium không.
        print(che_do)
        out = os.environ.get("GITHUB_OUTPUT")
        if out:
            with open(out, "a", encoding="utf-8") as f:
                f.write(f"che_do={che_do}\n")
        return 0

    log(f"🗓️ {TEN_THU[ngay.weekday()]} {ngay:%d/%m/%Y} - chế độ: {che_do}")

    token = os.environ.get("TELE_BOT_TOKEN")
    chat_id = os.environ.get("TELE_CHAT_ID")
    if not token or not chat_id:
        log("❌ Thiếu TELE_BOT_TOKEN hoặc TELE_CHAT_ID")
        return 1

    tg = Telegram(token, chat_id)
    link = link_lan_chay()
    duoi = f"\nXem log: {link}" if link else ""

    # ---- Chế độ ngày: không cần trình duyệt
    if che_do == "ngay":
        return 0 if bao_lich_hom_nay(tg, state, ngay) else 1

    if sync_playwright is None:
        log("❌ Chưa cài Playwright mà chế độ này cần trình duyệt")
        return 1

    msv = os.environ.get("MSV")
    password = os.environ.get("PASS_TRUONG")
    if not msv or not password:
        tg.tin("❌ Bot lịch học: thiếu secret MSV hoặc PASS_TRUONG." + duoi)
        return 1

    cac_muc = CAC_MUC if che_do == "tuan" else CAC_MUC[:1]
    try:
        kq = scrape_data(msv, password, cac_muc)
    except Exception as e:
        if isinstance(e, LoiDangNhap):
            tg.tin(f"❌ Bot lịch học: {e} Hãy cập nhật secret PASS_TRUONG.")
        else:
            tg.tin(f"❌ Bot lịch học không lấy được dữ liệu.\n{ngan_gon(e)}{duoi}")
        if Path("debug_dang_nhap.png").exists():
            tg.anh("debug_dang_nhap.png", "Ảnh màn hình lúc lỗi")
        return 1

    # ---- Lịch học: lưu cả tuần, gửi ảnh tuần (thứ Hai) + lịch hôm nay
    lh = kq.get("lich_hoc")
    if lh:
        luu_lich_tuan(state, lh)
        ghi_state(state)
        if lh.get("anh"):
            if che_do == "tuan":
                tg.anh(lh["anh"], "📌 Lịch học tuần này")
            if lh["ngay"] is not None or che_do != "tuan":
                bao_lich_hom_nay(tg, state, ngay)   # (đã gửi ảnh tuần thì khỏi gửi lại)
        elif che_do == "tuan":
            tg.tin(lh["tin"])               # cả tuần nghỉ: 1 tin là đủ
        else:
            bao_lich_hom_nay(tg, state, ngay)

    # ---- Lịch thi (báo rõ là mới hay chỉ nhắc lại)
    lt = kq.get("lich_thi")
    if lt:
        moi = lt["hash"] != state.get("lich_thi_hash")
        caption = "🚨 CÓ LỊCH THI MỚI" if moi else "📝 Nhắc lịch thi sắp tới (không đổi)"
        if tg.anh(lt["anh"], caption):
            state["lich_thi_hash"] = lt["hash"]
    elif "lich_thi" in kq:
        # Cào thành công và không còn lịch thi -> lần sau có lịch sẽ báo là mới.
        state.pop("lich_thi_hash", None)
    ghi_state(state)

    # ---- Học phí
    hp = kq.get("hoc_phi")
    if hp:
        tg.anh(hp["anh"], hp["tin"])

    # ---- Lỗi từng mục: báo kèm ảnh màn hình qua Telegram (riêng tư),
    #      không upload lên GitHub vì repo public ai cũng tải được.
    if kq["loi"]:
        tg.tin("⚠️ Một số mục bị lỗi:\n" + "\n".join(f"• {m}" for m, _ in kq["loi"]) + duoi)
        for m, anh in kq["loi"]:
            if anh:
                tg.anh(anh, f"Ảnh lúc lỗi: {m[:200]}")
        return 1

    log("\n🏁 Xong!")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
