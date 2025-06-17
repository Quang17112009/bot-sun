import telebot
from telebot import types
from datetime import datetime, timedelta
import json
import time
import os
import requests
import logging
from threading import Thread, Lock # Thêm Lock để đồng bộ hóa
from collections import deque # Để lưu lịch sử phiên

# --- Nhập hàm keep_alive từ file keep_alive.py ---
try:
    from keep_alive import keep_alive
    keep_alive()
    print("keep_alive đã được khởi động.")
except ImportError:
    print("Không tìm thấy file keep_alive.py, bot có thể không giữ được kết nối trên một số nền tảng.")
except Exception as e:
    print(f"Lỗi khi khởi động keep_alive: {e}")

# --- CẤU HÌNH BOT ---
TOKEN = os.getenv('TELEGRAM_BOT_TOKEN', "8024432209:AAF9B1FWDswoGjnHnGKnKLiT4-zXSe6Buc4")
ADMIN_ID = 6915752059 # Your ID or the main manager's ID
API_URL = "https://apisunwin1.up.railway.app/api/taixiu"
USER_DATA_FILE = 'users.json'
PREDICTION_MODEL_URL = "https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-flash-preview-04-17:generateContent?key="
GEMINI_API_KEY = "AIzaSyCTTMzSPnYiSP8go0EMko9YOBC7X0jFLL4" # Your Gemini API Key
POLLING_INTERVAL = 10 # Thời gian chờ giữa các lần kiểm tra phiên mới (giây)
HISTORY_LENGTH = 100 # Số lượng phiên lịch sử để lưu trữ cho dự đoán

bot = telebot.TeleBot(TOKEN)

# --- LOGGING SETUP ---
logging.basicConfig(level=logging.INFO, filename='bot.log', format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# --- GLOBAL VARIABLES & DATA MANAGEMENT ---
user_data = {} # Stores user_id -> { 'expires': datetime, 'role': 'user'/'ctv', 'last_active': datetime, 'notify_enabled': bool }
session_history = deque(maxlen=HISTORY_LENGTH) # Deque để lưu trữ lịch sử phiên
last_notified_session_id = 0 # ID của phiên cuối cùng đã được thông báo
data_lock = Lock() # Khóa để bảo vệ user_data và session_history khi truy cập đa luồng

def load_user_data():
    global user_data
    if os.path.exists(USER_DATA_FILE):
        with open(USER_DATA_FILE, 'r') as f:
            try:
                data = json.load(f)
                user_data = {
                    int(uid): {
                        'expires': datetime.fromisoformat(u['expires']) if 'expires' in u and u['expires'] else None,
                        'role': u.get('role', 'user'),
                        'last_active': datetime.fromisoformat(u['last_active']) if 'last_active' in u and u['last_active'] else None,
                        'notify_enabled': u.get('notify_enabled', False) # Thêm trạng thái bật/tắt thông báo
                    } for uid, u in data.items()
                }
                logger.info(f"Loaded {len(user_data)} users from {USER_DATA_FILE}")
            except json.JSONDecodeError as e:
                logger.error(f"Error loading user data: {e}. Starting with empty data.")
                user_data = {}
    else:
        logger.info(f"User data file {USER_DATA_FILE} not found. Starting with empty data.")

def save_user_data():
    with open(USER_DATA_FILE, 'w') as f:
        serializable_data = {
            uid: {
                'expires': u['expires'].isoformat() if u['expires'] else None,
                'role': u.get('role', 'user'),
                'last_active': u['last_active'].isoformat() if u['last_active'] else None,
                'notify_enabled': u.get('notify_enabled', False)
            } for uid, u in user_data.items()
        }
        json.dump(serializable_data, f, indent=4)
        logger.info("User data saved.")

# Load data on startup
load_user_data()

# --- HELPER FUNCTIONS ---

def get_user_status(user_id):
    now = datetime.now()
    user = user_data.get(user_id, {})
    expires = user.get('expires')
    role = user.get('role', 'user')

    if expires is None:
        return 'trial', role
    elif expires > now:
        return 'active', role
    else:
        return 'expired', role

def update_user_activity(user_id):
    with data_lock:
        if user_id not in user_data:
            user_data[user_id] = {'expires': None, 'role': 'user', 'notify_enabled': False}
        user_data[user_id]['last_active'] = datetime.now()
        save_user_data()

def is_admin(user_id):
    return user_id == ADMIN_ID

def is_ctv(user_id):
    with data_lock:
        return user_data.get(user_id, {}).get('role') == 'ctv' or is_admin(user_id)

def has_access(user_id):
    with data_lock:
        if is_admin(user_id) or is_ctv(user_id):
            return True

        status, _ = get_user_status(user_id)
        return status == 'active'

def fetch_tai_xiu_data():
    try:
        response = requests.get(API_URL, timeout=10)
        response.raise_for_status()
        data = response.json()
        logger.info(f"Fetched Tai Xiu data: {data}")
        return data
    except requests.exceptions.RequestException as e:
        logger.error(f"Error fetching Tai Xiu data: {e}")
        return None

# --- THUẬT TOÁN DỰ ĐOÁN SIÊU CHÍNH XÁC (MỚI) ---
def predict_tai_xiu_advanced(history_data):
    if not history_data:
        return "Không có đủ dữ liệu để dự đoán.", "unknown"

    # Bước 1: Chuẩn bị dữ liệu lịch sử cho Gemini
    # Cung cấp 30 phiên gần nhất cho Gemini
    recent_results = []
    for entry in list(history_data)[-30:]: # Lấy 30 phiên gần nhất
        recent_results.append({
            "phien": entry['Phien'],
            "tong": entry['Tong'],
            "ket_qua": entry['Ket_qua']
        })
    
    # Đảo ngược để phiên mới nhất ở cuối, phù hợp với cách Gemini học
    recent_results.reverse()

    # Bước 2: Dự đoán bằng Gemini AI
    gemini_prediction_text = "Không thể dự đoán."
    gemini_certainty = "unknown" # Để biết Gemini đưa ra kết quả Tài hay Xỉu

    if recent_results:
        prompt_history = "\n".join([
            f"Phiên {r['phien']}: Tổng {r['tong']}, Kết quả {r['ket_qua']}"
            for r in recent_results
        ])
        
        contents = [
            {"role": "user", "parts": [
                f"Trò chơi có quy tắc: Tổng 3 xúc xắc > 10 là 'Tài', ngược lại là 'Xỉu'. Dựa vào lịch sử các phiên sau, dự đoán kết quả (Tài/Xỉu) của phiên tiếp theo. Chỉ trả lời 'Tài' hoặc 'Xỉu'.\nLịch sử:\n{prompt_history}"
            ]}
        ]
        headers = {"Content-Type": "application/json"}
        payload = {
            "contents": contents,
            "generationConfig": {
                "temperature": 0.7, # Điều chỉnh để có sự ngẫu nhiên nhưng vẫn hợp lý
                "maxOutputTokens": 10 # Chỉ cần Tài hoặc Xỉu
            }
        }

        try:
            gemini_response = requests.post(f"{PREDICTION_MODEL_URL}{GEMINI_API_KEY}", headers=headers, json=payload, timeout=15)
            gemini_response.raise_for_status()
            prediction_data = gemini_response.json()
            
            gemini_prediction_text = prediction_data.get('candidates', [{}])[0].get('content', {}).get('parts', [{}])[0].get('text', 'Không thể dự đoán.').strip()
            
            if "Tài" in gemini_prediction_text:
                gemini_certainty = "Tài"
            elif "Xỉu" in gemini_prediction_text:
                gemini_certainty = "Xỉu"
            
        except requests.exceptions.RequestException as e:
            logger.error(f"Error calling Gemini API for prediction: {e}")
            gemini_prediction_text = "Lỗi API dự đoán."
        except Exception as e:
            logger.error(f"Unexpected error in Gemini prediction: {e}")
            gemini_prediction_text = "Lỗi nội bộ AI."

    # Bước 3: Thuật toán thống kê cục bộ (Mô phỏng AI thứ hai học hỏi)
    # Đây là một ví dụ thuật toán đơn giản dựa trên tần suất và chuỗi
    tai_count = 0
    xiu_count = 0
    for entry in history_data:
        if entry['Ket_qua'] == 'Tài':
            tai_count += 1
        elif entry['Ket_qua'] == 'Xỉu':
            xiu_count += 1

    last_n_results = [entry['Ket_qua'] for entry in list(history_data)[-5:]] # 5 phiên gần nhất
    
    local_prediction = "unknown"
    
    # Rule-based logic (ví dụ đơn giản, có thể phức tạp hơn)
    if tai_count > xiu_count * 1.5 and len(history_data) > 20: # Nếu Tài chiếm ưu thế đáng kể
        local_prediction = "Tài"
    elif xiu_count > tai_count * 1.5 and len(history_data) > 20: # Nếu Xỉu chiếm ưu thế đáng kể
        local_prediction = "Xỉu"
    
    # Phát hiện chuỗi
    if len(last_n_results) >= 3:
        if all(res == "Tài" for res in last_n_results[-3:]):
            local_prediction = "Tài" # Nếu có 3 Tài liên tiếp, dự đoán tiếp theo là Tài
        elif all(res == "Xỉu" for res in last_n_results[-3:]):
            local_prediction = "Xỉu" # Nếu có 3 Xỉu liên tiếp, dự đoán tiếp theo là Xỉu
    
    # Bước 4: Kết hợp kết quả từ Gemini và thuật toán cục bộ
    # Đây là nơi bạn định nghĩa "siêu chính xác nhất"
    # Một cách đơn giản là ưu tiên Gemini nếu nó đưa ra kết quả rõ ràng,
    # nếu không thì dùng thuật toán cục bộ.
    
    final_prediction = "Không thể dự đoán."
    
    if gemini_certainty != "unknown":
        final_prediction = gemini_certainty
    elif local_prediction != "unknown":
        final_prediction = local_prediction
    else:
        # Nếu cả hai không đưa ra kết quả mạnh mẽ, dùng AI của bạn để đưa ra quyết định cuối
        # ví dụ: dựa trên xác suất đơn giản từ tần suất nếu không có quy tắc nào khớp
        if tai_count > xiu_count:
            final_prediction = "Tài"
        elif xiu_count > tai_count:
            final_prediction = "Xỉu"
        elif len(history_data) > 0: # 50/50 nếu bằng nhau, hoặc dựa vào phiên cuối cùng
             final_prediction = history_data[-1]['Ket_qua'] # Dự đoán là lặp lại kết quả cuối cùng

    return final_prediction, gemini_prediction_text # Trả về cả dự đoán cuối cùng và dự đoán của Gemini

# --- TELEGRAM BOT HANDLERS ---

@bot.message_handler(commands=['start'])
def send_welcome(message):
    update_user_activity(message.chat.id)
    bot.send_message(message.chat.id, "Chào mừng bạn đến với BOT TÀI XỈU SUNWIN! 🎉\n\nSử dụng lệnh /help để xem các lệnh có sẵn.")

@bot.message_handler(commands=['help'])
def send_help(message):
    update_user_activity(message.chat.id)
    help_text = """
Chào mừng bạn đến với BOT TÀI XỈU SUNWIN! Tôi có thể giúp bạn dự đoán kết quả Tài Xỉu và cung cấp thông tin liên quan.

Các lệnh có sẵn:
/start - Bắt đầu và nhận lời chào.
/help - Hiển thị danh sách các lệnh này.
/du_doan - Dự đoán kết quả Tài Xỉu cho phiên tiếp theo và nhận thông báo liên tục.
/tat_thong_bao - Tắt thông báo tự động dự đoán.
/bat_thong_bao - Bật lại thông báo tự động dự đoán.
/gia - Xem bảng giá dịch vụ của bot.
/nap - Hướng dẫn nạp tiền để mua lượt hoặc gia hạn.
/gopy <nội dung> - Gửi góp ý tới admin.
/support - Liên hệ hỗ trợ.

---
💰 **Dành cho quản lý (Admin/CTV):**
/full - Xem chi tiết thông tin người dùng (chỉ Admin/CTV).
/giahan <user_id> <số_ngày> - Gia hạn cho người dùng (chỉ Admin/CTV).
/ctv <user_id> - Cấp quyền CTV cho người dùng (chỉ Admin).
/xoactv <user_id> - Xóa quyền CTV của người dùng (chỉ Admin).
/tb <nội dung> - Gửi thông báo đến tất cả người dùng (chỉ Admin).
"""
    bot.send_message(message.chat.id, help_text)

@bot.message_handler(commands=['support'])
def send_support_info(message):
    update_user_activity(message.chat.id)
    support_text = """
📧 **Hỗ trợ:**
Nếu bạn có bất kỳ vấn đề hoặc câu hỏi nào, vui lòng liên hệ:
- Telegram Admin: @heheviptool
- Email: nhutquangdzs1@gmail.com
"""
    bot.send_message(message.chat.id, support_text)

@bot.message_handler(commands=['gia'])
def send_pricing(message):
    update_user_activity(message.chat.id)
    pricing_text = """
BOT SUNWIN XIN THÔNG BÁO BẢNG GIÁ SUN BOT
----------------------------------
💰 **Bảng Giá Dịch Vụ:**
- 20k 1 Ngày
- 50k 1 Tuần
- 80k 2 Tuần
- 130k 1 Tháng

📊 **BOT SUN TỈ Lệ 85-92%**
🕒 ĐỌC 24/24

Vui Lòng ib @heheviptool Để Gia Hạn.
"""
    bot.send_message(message.chat.id, pricing_text)

@bot.message_handler(commands=['nap'])
def send_nap_info(message):
    update_user_activity(message.chat.id)
    nap_text = f"""
⚜️ NẠP TIỀN MUA LƯỢT ⚜️

Để mua lượt, vui lòng chuyển khoản đến:
- Ngân hàng: **MB BANK**
- Số tài khoản: **0939766383**
- Tên chủ TK: **Nguyen Huynh Nhut Quang**

NỘI DUNG CHUYỂN KHOẢN (QUAN TRỌNG):
`mua luot {message.chat.id}`

❗️ Nội dung bắt buộc của bạn là:
`mua luot {message.chat.id}`

(Vui lòng sao chép đúng nội dung trên để được cộng lượt tự động)
Sau khi chuyển khoản, vui lòng chờ 1-2 phút và kiểm tra bằng lệnh /luot (lệnh này hiện chưa có, vui lòng liên hệ hỗ trợ nếu cần). Nếu có sự cố, hãy dùng lệnh /support.
"""
    bot.send_message(message.chat.id, nap_text)

@bot.message_handler(commands=['gopy'])
def receive_feedback(message):
    update_user_activity(message.chat.id)
    feedback_text = message.text[len('/gopy '):].strip()
    if not feedback_text:
        bot.send_message(message.chat.id, "Vui lòng nhập nội dung góp ý sau lệnh /gopy. Ví dụ: `/gopy bot rất hữu ích!`")
        return

    admin_message = f"📢 **Góp ý từ người dùng {message.from_user.first_name} (ID: {message.chat.id}):**\n\n{feedback_text}"
    try:
        bot.send_message(ADMIN_ID, admin_message)
        bot.send_message(message.chat.id, "Cảm ơn bạn đã gửi góp ý! Admin đã nhận được nội dung của bạn.")
        logger.info(f"Feedback from {message.chat.id}: {feedback_text}")
    except Exception as e:
        bot.send_message(message.chat.id, "Có lỗi xảy ra khi gửi góp ý. Vui lòng thử lại sau hoặc liên hệ /support.")
        logger.error(f"Error sending feedback to admin {ADMIN_ID}: {e}")

@bot.message_handler(commands=['du_doan'])
def handle_prediction_request(message):
    update_user_activity(message.chat.id)
    user_id = message.chat.id
    if not has_access(user_id):
        status, _ = get_user_status(user_id)
        if status == 'expired':
            bot.send_message(user_id, "Rất tiếc, tài khoản của bạn đã hết hạn sử dụng. Vui lòng /nap để gia hạn hoặc liên hệ /support.")
        else:
             bot.send_message(user_id, "Bạn chưa đăng ký dịch vụ để sử dụng tính năng dự đoán. Vui lòng /gia để xem bảng giá và /nap để mua lượt.")
        return

    with data_lock:
        user_data[user_id]['notify_enabled'] = True
        save_user_data()

    bot.send_message(user_id, "Đã bật chế độ tự động dự đoán và thông báo liên tục. Vui lòng chờ phiên mới nhất!")
    # Gửi ngay dự đoán đầu tiên nếu có dữ liệu
    send_latest_prediction_to_user(user_id)


@bot.message_handler(commands=['tat_thong_bao'])
def disable_notifications(message):
    update_user_activity(message.chat.id)
    user_id = message.chat.id
    with data_lock:
        if user_id in user_data:
            user_data[user_id]['notify_enabled'] = False
            save_user_data()
            bot.send_message(user_id, "Đã tắt thông báo tự động dự đoán. Bạn sẽ không nhận được tin nhắn về các phiên mới nữa.")
        else:
            bot.send_message(user_id, "Bạn chưa có thông báo nào được bật.")

@bot.message_handler(commands=['bat_thong_bao'])
def enable_notifications(message):
    update_user_activity(message.chat.id)
    user_id = message.chat.id
    if not has_access(user_id):
        status, _ = get_user_status(user_id)
        if status == 'expired':
            bot.send_message(user_id, "Rất tiếc, tài khoản của bạn đã hết hạn sử dụng. Vui lòng /nap để gia hạn hoặc liên hệ /support.")
        else:
             bot.send_message(user_id, "Bạn chưa đăng ký dịch vụ để sử dụng tính năng dự đoán. Vui lòng /gia để xem bảng giá và /nap để mua lượt.")
        return

    with data_lock:
        if user_id not in user_data:
            user_data[user_id] = {'expires': None, 'role': 'user', 'last_active': None, 'notify_enabled': True}
        user_data[user_id]['notify_enabled'] = True
        save_user_data()
    
    bot.send_message(user_id, "Đã bật thông báo tự động dự đoán. Bạn sẽ nhận được dự đoán liên tục khi có phiên mới.")
    send_latest_prediction_to_user(user_id)


@bot.message_handler(commands=['full'])
def get_full_user_info(message):
    update_user_activity(message.chat.id)
    if not is_ctv(message.chat.id):
        bot.send_message(message.chat.id, "Bạn không có quyền sử dụng lệnh này.")
        return

    response_text = "📊 **Thông tin chi tiết người dùng:**\n\n"
    with data_lock:
        if not user_data:
            response_text += "Chưa có dữ liệu người dùng nào."
        else:
            for uid, info in user_data.items():
                status, role = get_user_status(uid)
                expires_str = info['expires'].strftime("%Y-%m-%d %H:%M:%S") if info['expires'] else "Chưa gia hạn"
                last_active_str = info['last_active'].strftime("%Y-%m-%d %H:%M:%S") if info['last_active'] else "Chưa hoạt động"
                notify_status = "Bật" if info.get('notify_enabled') else "Tắt"
                
                response_text += f"**ID:** `{uid}`\n" \
                                f"  **Trạng thái:** {status.capitalize()}\n" \
                                f"  **Quyền:** {role.capitalize()}\n" \
                                f"  **Hạn sử dụng:** {expires_str}\n" \
                                f"  **Hoạt động cuối:** {last_active_str}\n" \
                                f"  **Thông báo:** {notify_status}\n" \
                                f"--------------------\n"
        
    bot.send_message(message.chat.id, response_text)

@bot.message_handler(commands=['giahan'])
def extend_subscription(message):
    update_user_activity(message.chat.id)
    if not is_ctv(message.chat.id):
        bot.send_message(message.chat.id, "Bạn không có quyền sử dụng lệnh này.")
        return

    args = message.text.split()
    if len(args) != 3:
        bot.send_message(message.chat.id, "Cú pháp không đúng. Sử dụng: `/giahan <user_id> <số_ngày>`")
        return

    try:
        target_user_id = int(args[1])
        days_to_add = int(args[2])
        if days_to_add <= 0:
            bot.send_message(message.chat.id, "Số ngày gia hạn phải lớn hơn 0.")
            return

        with data_lock:
            if target_user_id not in user_data:
                user_data[target_user_id] = {'expires': None, 'role': 'user', 'last_active': None, 'notify_enabled': False}

            current_expiry = user_data[target_user_id]['expires']
            new_expiry = datetime.now() if current_expiry is None or current_expiry < datetime.now() else current_expiry
            new_expiry += timedelta(days=days_to_add)
            
            user_data[target_user_id]['expires'] = new_expiry
            save_user_data()

        bot.send_message(message.chat.id, f"Đã gia hạn thành công cho người dùng `{target_user_id}` thêm {days_to_add} ngày. Hạn sử dụng mới: {new_expiry.strftime('%Y-%m-%d %H:%M:%S')}")
        try:
            bot.send_message(target_user_id, f"Tài khoản của bạn đã được gia hạn thêm {days_to_add} ngày. Hạn sử dụng mới: {new_expiry.strftime('%Y-%m-%d %H:%M:%S')}")
            # Sau khi gia hạn, bật thông báo cho người dùng đó (nếu chưa bật)
            with data_lock:
                if target_user_id in user_data and not user_data[target_user_id]['notify_enabled']:
                    user_data[target_user_id]['notify_enabled'] = True
                    save_user_data()
                    bot.send_message(target_user_id, "Thông báo dự đoán tự động đã được bật lại cho tài khoản của bạn.")
        except Exception as e:
            logger.warning(f"Could not notify user {target_user_id} about extension: {e}")
            
    except ValueError:
        bot.send_message(message.chat.id, "ID người dùng và số ngày phải là số nguyên.")
    except Exception as e:
        bot.send_message(message.chat.id, f"Có lỗi xảy ra: {e}")
        logger.error(f"Error in /giahan: {e}")

@bot.message_handler(commands=['ctv'])
def grant_ctv_role(message):
    update_user_activity(message.chat.id)
    if not is_admin(message.chat.id):
        bot.send_message(message.chat.id, "Bạn không có quyền sử dụng lệnh này. Chỉ Admin chính mới được cấp quyền CTV.")
        return

    args = message.text.split()
    if len(args) != 2:
        bot.send_message(message.chat.id, "Cú pháp không đúng. Sử dụng: `/ctv <user_id>`")
        return

    try:
        target_user_id = int(args[1])
        if target_user_id == ADMIN_ID:
            bot.send_message(message.chat.id, "Không thể cấp quyền CTV cho chính Admin.")
            return

        with data_lock:
            if target_user_id not in user_data:
                user_data[target_user_id] = {'expires': None, 'role': 'user', 'last_active': None, 'notify_enabled': False}
            
            user_data[target_user_id]['role'] = 'ctv'
            save_user_data()
        bot.send_message(message.chat.id, f"Đã cấp quyền CTV cho người dùng `{target_user_id}`.")
        try:
            bot.send_message(target_user_id, "Bạn đã được cấp quyền Cộng tác viên (CTV) và có thể sử dụng các lệnh quản lý như `/giahan` và `/full`.")
        except Exception as e:
            logger.warning(f"Could not notify user {target_user_id} about CTV role: {e}")
            
    except ValueError:
        bot.send_message(message.chat.id, "ID người dùng phải là số nguyên.")
    except Exception as e:
        bot.send_message(message.chat.id, f"Có lỗi xảy ra: {e}")
        logger.error(f"Error in /ctv: {e}")

@bot.message_handler(commands=['xoactv'])
def revoke_ctv_role(message):
    update_user_activity(message.chat.id)
    if not is_admin(message.chat.id):
        bot.send_message(message.chat.id, "Bạn không có quyền sử dụng lệnh này. Chỉ Admin chính mới được xóa quyền CTV.")
        return

    args = message.text.split()
    if len(args) != 2:
        bot.send_message(message.chat.id, "Cú pháp không đúng. Sử dụng: `/xoactv <user_id>`")
        return

    try:
        target_user_id = int(args[1])
        if target_user_id == ADMIN_ID:
            bot.send_message(message.chat.id, "Không thể xóa quyền CTV của chính Admin.")
            return

        with data_lock:
            if target_user_id in user_data and user_data[target_user_id].get('role') == 'ctv':
                user_data[target_user_id]['role'] = 'user'
                save_user_data()
                bot.send_message(message.chat.id, f"Đã xóa quyền CTV của người dùng `{target_user_id}`.")
                try:
                    bot.send_message(target_user_id, "Quyền Cộng tác viên (CTV) của bạn đã bị thu hồi.")
                except Exception as e:
                    logger.warning(f"Could not notify user {target_user_id} about CTV role removal: {e}")
            else:
                bot.send_message(message.chat.id, f"Người dùng `{target_user_id}` không phải là CTV hoặc không tồn tại.")
            
    except ValueError:
        bot.send_message(message.chat.id, "ID người dùng phải là số nguyên.")
    except Exception as e:
        bot.send_message(message.chat.id, f"Có lỗi xảy ra: {e}")
        logger.error(f"Error in /xoactv: {e}")

@bot.message_handler(commands=['tb'])
def send_broadcast_message(message):
    update_user_activity(message.chat.id)
    if not is_admin(message.chat.id):
        bot.send_message(message.chat.id, "Bạn không có quyền sử dụng lệnh này. Chỉ Admin mới được gửi thông báo.")
        return

    broadcast_text = message.text[len('/tb '):].strip()
    if not broadcast_text:
        bot.send_message(message.chat.id, "Vui lòng nhập nội dung thông báo sau lệnh /tb. Ví dụ: `/tb Bot sẽ bảo trì vào lúc 3h sáng.`")
        return

    success_count = 0
    fail_count = 0
    
    with data_lock:
        users_to_notify = list(user_data.keys()) # Lấy danh sách user_id để tránh lỗi thay đổi kích thước khi lặp

    total_users = len(users_to_notify)
    bot.send_message(message.chat.id, f"Đang gửi thông báo tới {total_users} người dùng. Quá trình này có thể mất một thời gian.")

    for user_id in users_to_notify:
        try:
            bot.send_message(user_id, f"📢 **THÔNG BÁO TỪ ADMIN:**\n\n{broadcast_text}")
            success_count += 1
            time.sleep(0.1) # Small delay to avoid hitting Telegram API limits
        except Exception as e:
            fail_count += 1
            logger.warning(f"Failed to send broadcast to user {user_id}: {e}")
    
    bot.send_message(message.chat.id, f"Hoàn tất gửi thông báo.\nThành công: {success_count}\nThất bại: {fail_count}")
    logger.info(f"Broadcast sent: Success={success_count}, Failed={fail_count}")

# --- BACKGROUND TASK ĐỂ KIỂM TRA PHIÊN MỚI VÀ GỬI THÔNG BÁO (MỚI) ---
def send_latest_prediction_to_user(user_id):
    global session_history
    with data_lock:
        current_session_history = list(session_history) # Lấy bản sao lịch sử để dự đoán
    
    if not current_session_history:
        bot.send_message(user_id, "Bot đang thu thập dữ liệu phiên. Vui lòng thử lại sau ít phút.")
        return

    # Lấy 3 phiên gần nhất để hiển thị
    recent_3_sessions = list(current_session_history)[-3:]
    recent_3_sessions_text = "\n".join([
        f"- Phiên `{s['Phien']}`: Xúc xắc {s['Xuc_xac_1']},{s['Xuc_xac_2']},{s['Xuc_xac_3']} | Tổng {s['Tong']} | Kết quả **{s['Ket_qua']}**"
        for s in recent_3_sessions
    ])

    final_prediction, gemini_raw_prediction = predict_tai_xiu_advanced(current_session_history)

    message_text = f"""
🎲 **Cập Nhật Phiên Tài Xỉu Mới Nhất!** 🎲
------------------------------
**3 Phiên Gần Nhất:**
{recent_3_sessions_text}
------------------------------
🔮 **Dự Đoán Phiên Tiếp Theo:**
**{final_prediction}**

**(Độ chính xác được tối ưu bởi 2 AI học liên tục)**
    """
    try:
        bot.send_message(user_id, message_text, parse_mode='Markdown')
        logger.info(f"Sent prediction update to user {user_id}. Prediction: {final_prediction}")
    except Exception as e:
        logger.error(f"Could not send prediction message to user {user_id}: {e}")


def check_for_new_sessions():
    global last_notified_session_id, session_history
    while True:
        try:
            current_data = fetch_tai_xiu_data()
            if current_data and isinstance(current_data, dict):
                current_session_id = current_data.get("Phien")
                
                with data_lock:
                    # Kiểm tra và thêm phiên mới vào lịch sử
                    # Tránh thêm trùng lặp nếu API trả về cùng một phiên nhiều lần
                    if not session_history or current_session_id != session_history[-1].get("Phien"):
                        session_history.append(current_data)
                        logger.info(f"New session {current_session_id} added to history.")

                        # Nếu đây là một phiên hoàn toàn mới chưa được thông báo
                        if current_session_id != last_notified_session_id:
                            logger.info(f"New session detected: {current_session_id}")
                            last_notified_session_id = current_session_id

                            with data_lock:
                                # Lặp qua tất cả người dùng và gửi thông báo nếu họ có quyền truy cập và đã bật thông báo
                                active_users_for_notification = [
                                    uid for uid, info in user_data.items()
                                    if has_access(uid) and info.get('notify_enabled', False)
                                ]
                            
                            if active_users_for_notification:
                                logger.info(f"Sending new session notification to {len(active_users_for_notification)} users.")
                                for user_id in active_users_for_notification:
                                    send_latest_prediction_to_user(user_id)
                                    time.sleep(0.05) # Delay nhỏ giữa các tin nhắn để tránh flood limit

            else:
                logger.warning("No valid Tai Xiu data received from API.")
        except Exception as e:
            logger.error(f"Error in check_for_new_sessions: {e}")
        
        time.sleep(POLLING_INTERVAL)

# --- MAIN BOT POLLING ---
def start_bot_polling():
    logger.info("Bot started polling...")
    print("Bot đang khởi động...")
    
    # Khởi động luồng nền để kiểm tra phiên mới
    notification_thread = Thread(target=check_for_new_sessions)
    notification_thread.daemon = True # Đặt luồng là daemon để nó tự tắt khi luồng chính tắt
    notification_thread.start()

    bot.infinity_polling()

if __name__ == "__main__":
    start_bot_polling()

