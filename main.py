import telebot
from telebot import types # Import types for inline keyboards
import requests
import time
import json
import os
import random
import string
from datetime import datetime, timedelta
from threading import Thread, Event, Lock

from flask import Flask, request

# --- Cấu hình Bot (ĐẶT TRỰC TIẾP TẠY ĐÂY) ---
# THAY THẾ 'YOUR_BOT_TOKEN_HERE' BẰNG TOKEN THẬT CỦA BẠN
BOT_TOKEN = "8137068939:AAG19xO92yXsz_d9vz_m2aJW2Wh8JZnvSPQ" 
# THAY THẾ BẰNG ID ADMIN GỐC CỦA BẠN. Admin gốc này sẽ có quyền thêm/xóa admin khác.
# CHỈ CẦN MỘT ID ADMIN BAN ĐẦU ĐỂ KHỞI TẠO. CÁC ADMIN KHÁC SẼ ĐƯỢC QUẢN LÝ QUA LỆNH.
SUPER_ADMIN_IDS = [6915752059] 

DATA_FILE = 'user_data.json'
KEYS_FILE = 'keys.json'

# --- Khởi tạo Flask App và Telegram Bot ---
app = Flask(__name__)
bot = telebot.TeleBot(BOT_TOKEN)

# Global flags và objects
bot_enabled_global = True # Cờ tắt/mở bot toàn cục bởi admin
bot_disable_reason_global = "Không có"
bot_initialized = False # Cờ để đảm bảo bot chỉ được khởi tạo một lần
bot_init_lock = Lock() # Khóa để tránh race condition khi khởi tạo

# Global data containers
user_data = {} # {user_id: {...}}
generated_keys = {} # {key_string: {...}}
prediction_history = { # Lưu 10 phiên gần nhất cho mỗi game
    "LuckyWin": [],
    "SunWin": [], 
    "B52": [],
    "Hit": []
}

# --- API Endpoints for each game ---
GAME_APIS = {
    "LuckyWin": "https://apiluck.onrender.com/api/taixiu",
    "SunWin": "https://tooltxnghiau.kesug.com/apisunnghiau.json", # <-- ĐÃ CẬP NHẬT CHO SUNWIN
    "B52": "https://apib52-8y5h.onrender.com/api/taixiu",
    "Hit": "https://apihit-v17r.onrender.com/api/taixiu"
}

# --- Quản lý dữ liệu người dùng và keys ---
def load_user_data():
    global user_data
    if os.path.exists(DATA_FILE):
        with open(DATA_FILE, 'r') as f:
            try:
                user_data = json.load(f)
            except json.JSONDecodeError:
                print(f"Lỗi đọc {DATA_FILE}. Khởi tạo lại dữ liệu người dùng.")
                user_data = {}
    else:
        user_data = {}
    print(f"Loaded {len(user_data)} user records from {DATA_FILE}")
    # Đảm bảo super admin luôn có quyền admin trong user_data
    for admin_id in SUPER_ADMIN_IDS:
        user_id_str = str(admin_id)
        if user_id_str not in user_data:
            user_data[user_id_str] = {'username': 'Super Admin', 'expiry_date': None, 'is_admin': True, 'receiving_predictions': False, 'preferred_game': None}
        else:
            user_data[user_id_str]['is_admin'] = True # Đảm bảo admin gốc luôn là admin
    save_user_data(user_data) # Lưu lại để cập nhật trạng thái admin

def save_user_data(data):
    with open(DATA_FILE, 'w') as f:
        json.dump(data, f, indent=4)

def load_keys():
    global generated_keys
    if os.path.exists(KEYS_FILE):
        with open(KEYS_FILE, 'r') as f:
            try:
                generated_keys = json.load(f)
            except json.JSONDecodeError:
                print(f"Lỗi đọc {KEYS_FILE}. Khởi tạo lại mã key.")
                generated_keys = {}
    else:
        generated_keys = {}
    print(f"Loaded {len(generated_keys)} keys from {KEYS_FILE}")

def save_keys():
    with open(KEYS_FILE, 'w') as f:
        json.dump(generated_keys, f, indent=4)

def is_admin(user_id):
    user_id_str = str(user_id)
    return user_data.get(user_id_str, {}).get('is_admin', False)

def check_key_validity(user_id):
    user_id_str = str(user_id)
    if is_admin(user_id):
        return True, "Bạn là Admin, quyền truy cập vĩnh viễn."

    if user_id_str not in user_data or user_data[user_id_str].get('expiry_date') is None:
        return False, "⚠️ Bạn chưa kích hoạt bot bằng key hoặc key đã hết hạn."

    expiry_date_str = user_data[user_id_str]['expiry_date']
    expiry_date = datetime.strptime(expiry_date_str, '%Y-%m-%d %H:%M:%S')

    if datetime.now() < expiry_date:
        remaining_time = expiry_date - datetime.now()
        days = remaining_time.days
        hours = remaining_time.seconds // 3600
        minutes = (remaining_time.seconds % 3600) // 60
        seconds = remaining_time.seconds % 60
        return True, f"✅ Key của bạn còn hạn đến: `{expiry_date_str}` ({days} ngày {hours} giờ {minutes} phút {seconds} giây)."
    else:
        return False, "❌ Key của bạn đã hết hạn."

# --- Hàm lấy và phân tích dữ liệu từ các API khác nhau ---
def fetch_and_parse_api_data(game_name):
    api_url = GAME_APIS.get(game_name)
    if not api_url:
        print(f"Không tìm thấy API cho game: {game_name}")
        return None

    try:
        response = requests.get(api_url)
        response.raise_for_status()
        data = response.json()

        parsed_data = {}
        if game_name == "LuckyWin":
            parsed_data["current_session"] = data.get("Ma_phien_hien_tai")
            parsed_data["current_result"] = data.get("Ket_qua_phien_hien_tai")
            parsed_data["total_score"] = data.get("Tong_diem_hien_tai")
            parsed_data["dice_values"] = data.get("Xuc_xac_hien_tai")
            
            prediction_data = data.get("Du_doan_phien_tiep_theo_ML", {})
            parsed_data["next_prediction"] = prediction_data.get("Ket_qua_du_doan")
            parsed_data["confidence"] = prediction_data.get("Do_tin_cay")
            parsed_data["next_session"] = str(int(parsed_data["current_session"]) + 1) if parsed_data["current_session"] else "N/A" # Thêm next_session cho LuckyWin để đồng bộ hóa
            
        elif game_name == "SunWin": # Logic phân tích riêng cho SunWin API mới
            parsed_data["current_session"] = data.get("phien_moi") # Phiên hiện tại/vừa kết thúc
            parsed_data["current_result"] = "N/A (API mới không cung cấp trực tiếp kết quả phiên hiện tại)" # Không có kết quả trực tiếp
            parsed_data["total_score"] = None
            parsed_data["dice_values"] = None
            
            parsed_data["next_prediction"] = data.get("du_doan")
            parsed_data["next_session"] = data.get("phien_du_doan") # Phiên sẽ được dự đoán
            
            # Tính confidence dựa trên phần trăm tài/xỉu lớn hơn
            phan_tram_tai = data.get("phan_tram_tai", 0)
            phan_tram_xiu = data.get("phan_tram_xiu", 0)
            if parsed_data["next_prediction"] and parsed_data["next_prediction"].lower().strip() == "tài":
                parsed_data["confidence"] = f"{phan_tram_tai}%"
            elif parsed_data["next_prediction"] and parsed_data["next_prediction"].lower().strip() == "xỉu":
                 parsed_data["confidence"] = f"{phan_tram_xiu}%"
            else:
                parsed_data["confidence"] = "N/A" 

        elif game_name in ["B52", "Hit"]:
            # Cả hai game này có định dạng tương tự
            parsed_data["current_session"] = data.get("current_session")
            parsed_data["current_result"] = data.get("current_result")
            parsed_data["total_score"] = None 
            parsed_data["dice_values"] = None 
            
            parsed_data["next_prediction"] = data.get("prediction")
            parsed_data["confidence"] = f"{data.get('confidence_percent', 0.0)}%" 
            parsed_data["next_session"] = str(int(parsed_data["current_session"]) + 1) if parsed_data["current_session"] else "N/A" # Thêm next_session cho B52/Hit để đồng bộ hóa

        # Xử lý mã hóa cho kết quả và dự đoán (áp dụng chung cho tất cả game)
        for key in ["current_result", "next_prediction"]:
            if parsed_data.get(key):
                # Thay thế các chuỗi mã hóa không đúng thành tiếng Việt có dấu
                parsed_data[key] = parsed_data[key].replace("TÃ i", "Tài").replace("Xá»‰u", "Xỉu").replace("X\u1ec9u", "Xỉu").replace("áº¢o", "Ảo").replace("Ã¡o", "ảo")
        
        return parsed_data
    except requests.exceptions.RequestException as e:
        print(f"Lỗi khi lấy dữ liệu từ API {game_name}: {e}")
        return None
    except json.JSONDecodeError:
        print(f"Lỗi giải mã JSON từ API {game_name}. Phản hồi không phải JSON hợp lệ.")
        return None
    except Exception as e:
        print(f"Lỗi không xác định khi xử lý API {game_name}: {e}")
        return None

# --- Logic chính của Bot dự đoán (chạy trong luồng riêng) ---
def prediction_loop(stop_event: Event):
    # Sử dụng dictionary để lưu last_processed_session cho từng game
    # Đối với SunWin, last_processed_session sẽ là phien_du_doan đã gửi
    # Đối với các game khác, nó là current_session vừa nhận kết quả
    last_processed_sessions = {game_name: None for game_name in GAME_APIS.keys()}
    global prediction_history
    
    print("Prediction loop started.")
    while not stop_event.is_set():
        if not bot_enabled_global:
            time.sleep(10)
            continue

        active_games = set()
        for user_id_str, user_info in user_data.items():
            if user_info.get('receiving_predictions', False) and user_info.get('preferred_game'):
                active_games.add(user_info['preferred_game'])
        
        if not active_games:
            time.sleep(5)
            continue

        for game_name in active_games:
            parsed_data = fetch_and_parse_api_data(game_name)
            if not parsed_data:
                continue

            current_session = parsed_data.get("current_session")
            current_result = parsed_data.get("current_result")
            total_score = parsed_data.get("total_score")
            dice_values = parsed_data.get("dice_values")
            next_prediction = parsed_data.get("next_prediction")
            confidence = parsed_data.get("confidence")
            next_session = parsed_data.get("next_session") # Đây là phiên dự đoán

            if not all([current_session, next_prediction, confidence, next_session]): # next_session là bắt buộc
                print(f"Dữ liệu API {game_name} không đầy đủ. Bỏ qua phiên này.")
                continue

            # Logic kiểm tra phiên mới để gửi tin nhắn
            if next_session != last_processed_sessions[game_name]:
                # Cập nhật lịch sử phiên của game
                if len(prediction_history[game_name]) >= 10:
                    prediction_history[game_name].pop(0) 
                
                # Định dạng lịch sử khác nhau tùy game
                if game_name == "SunWin":
                    history_entry = (
                        f"Phiên: `{current_session}` | Dự đoán: **{next_prediction}** (Phiên: `{next_session}`) | Độ tin cậy: **{confidence}**"
                    )
                else: # LuckyWin, B52, Hit
                    dice_str = f"({', '.join(map(str, dice_values))})" if dice_values else ""
                    total_str = f" (Tổng: **{total_score}**)" if total_score else ""
                    history_entry = (
                        f"Phiên: `{current_session}` | KQ: **{current_result}**{total_str} {dice_str}"
                    )
                prediction_history[game_name].append(history_entry)

                # Gửi tin nhắn dự đoán tới các người dùng đang theo dõi game này
                for user_id_str, user_info in list(user_data.items()):
                    user_id = int(user_id_str)
                    if user_info.get('receiving_predictions', False) and user_info.get('preferred_game') == game_name:
                        is_sub, sub_message = check_key_validity(user_id)
                        if is_sub:
                            try:
                                if game_name == "SunWin":
                                    prediction_message = (
                                        f"🎮 **DỰ ĐOÁN MỚI - {game_name.upper()}** 🎮\n"
                                        f"🔢 Phiên: `{next_session}`\n"
                                        f"🤖 Dự đoán: **{next_prediction}**\n"
                                        f"📈 Độ tin cậy: **{confidence}**\n"
                                        f"⚠️ **Hãy đặt cược sớm trước khi phiên kết thúc!**\n"
                                        f"_(Kết quả phiên trước không được API mới cung cấp trực tiếp)_"
                                    )
                                else: # LuckyWin, B52, Hit
                                    dice_str = f"({', '.join(map(str, dice_values))})" if dice_values else ""
                                    total_str = f" (Tổng: **{total_score}**)" if total_score else ""
                                    prediction_message = (
                                        f"🎮 **KẾT QUẢ PHIÊN HIỆN TẠI - {game_name.upper()}** 🎮\n"
                                        f"Phiên: `{current_session}` | Kết quả: **{current_result}**{total_str} {dice_str}\n\n"
                                        f"**Dự đoán cho phiên tiếp theo:**\n"
                                        f"🔢 Phiên: `{next_session}`\n"
                                        f"🤖 Dự đoán: **{next_prediction}**\n"
                                        f"📈 Độ tin cậy: **{confidence}**\n"
                                        f"⚠️ **Hãy đặt cược sớm trước khi phiên kết thúc!**"
                                    )
                                bot.send_message(user_id, prediction_message, parse_mode='Markdown')
                            except telebot.apihelper.ApiTelegramException as e:
                                if "bot was blocked by the user" in str(e) or "user is deactivated" in str(e):
                                    print(f"Người dùng {user_id} đã chặn bot hoặc bị vô hiệu hóa.")
                                else:
                                    print(f"Lỗi gửi tin nhắn cho user {user_id}: {e}")
                            except Exception as e:
                                print(f"Lỗi không xác định khi gửi tin nhắn cho user {user_id}: {e}")

                print("-" * 50)
                print(f"Game: {game_name}")
                if game_name == "SunWin":
                    print(f"🔢 Phiên dự đoán: {next_session}")
                    print(f"🤖 Dự đoán: {next_prediction}")
                    print(f"📈 Độ tin cậy: {confidence}")
                    print("⚠️ (Kết quả phiên trước không được API mới cung cấp trực tiếp)")
                else:
                    print(f"🎮 Kết quả phiên hiện tại: {current_session} | {current_result}{total_str} {dice_values}")
                    print(f"🔢 Phiên tiếp theo: {next_session}")
                    print(f"🤖 Dự đoán: {next_prediction}")
                    print(f"📈 Độ tin cậy: {confidence}")
                print("⚠️ Hãy đặt cược sớm trước khi phiên kết thúc!")
                print("-" * 50)

                last_processed_sessions[game_name] = next_session # Cập nhật phiên đã xử lý

        time.sleep(5) # Đợi 5 giây trước khi kiểm tra phiên mới
    print("Prediction loop stopped.")

# --- Xử lý lệnh Telegram ---

@bot.message_handler(commands=['start'])
def send_welcome(message):
    user_id = str(message.chat.id)
    username = message.from_user.username or message.from_user.first_name
    
    if user_id not in user_data:
        user_data[user_id] = {
            'username': username,
            'expiry_date': None,
            'is_admin': False, 
            'receiving_predictions': False,
            'preferred_game': None # Thêm trường này
        }
        save_user_data(user_data)
        bot.reply_to(message, 
                     "Chào mừng bạn đến với **BOT DỰ ĐOÁN TÀI XỈU ĐA NỀN TẢNG**!\n"
                     "Để bắt đầu sử dụng, hãy nhập key bằng lệnh `/key <mã_key>`.\n"
                     "Dùng lệnh /help để xem danh sách các lệnh hỗ trợ.", 
                     parse_mode='Markdown')
    else:
        user_data[user_id]['username'] = username 
        save_user_data(user_data)
        bot.reply_to(message, "Bạn đã khởi động bot rồi. Dùng /help để xem các lệnh.")

@bot.message_handler(commands=['help'])
def show_help(message):
    help_text = (
        "🔔 **HƯỚNG DẪN SỬ DỤNG BOT**\n"
        "══════════════════════════\n"
        "🔑 **Lệnh cơ bản:**\n"
        "🔸 `/start`: Hiển thị thông tin chào mừng\n"
        "🔸 `/key <key>`: Nhập key để kích hoạt bot\n"
        "🔸 `/chaybot`: Bật nhận thông báo và chọn game\n"
        "🔸 `/tatbot`: Tắt nhận thông báo dự đoán\n"
        "🔸 `/lichsu [tên_game]`: Xem lịch sử 10 phiên gần nhất của game (mặc định game đang chọn)\n"
    )
    
    if is_admin(message.chat.id):
        help_text += (
            "══════════════════════════\n"
            "🛡️ **Lệnh Admin:**\n"
            "🔹 `/taokey <giá_trị> <đơn_vị> [số_lượng]`\n"
            "   Ví dụ: `/taokey 1 ngày 5` (tạo 5 key 1 ngày)\n"
            "🔹 `/lietkekey`: Liệt kê tất cả key\n"
            "🔹 `/xoakey <key>`: Xóa key\n"
            "🔹 `/themadmin <id>`: Thêm admin\n"
            "🔹 `/xoaadmin <id>`: Xóa admin\n"
            "🔹 `/danhsachadmin`: Xem danh sách admin\n"
            "🔹 `/broadcast <tin nhắn>`: Gửi thông báo đến tất cả người dùng\n"
            "🔹 `/tatbot_global <lý do>`: Tắt bot dự đoán toàn cục\n"
            "🔹 `/mokbot_global`: Mở lại bot dự đoán toàn cục\n"
        )
    
    help_text += (
        "══════════════════════════\n"
        "👥 Liên hệ Admin để được hỗ trợ thêm:\n"
        "@heheviptool hoặc @Besttaixiu999"
    )
    
    bot.reply_to(message, help_text, parse_mode='Markdown')

@bot.message_handler(commands=['key'])
def use_key_command(message):
    key_str = telebot.util.extract_arguments(message.text)
    user_id = str(message.chat.id)

    if not key_str:
        bot.reply_to(message, "Vui lòng nhập key. Ví dụ: `/key ABCXYZ`", parse_mode='Markdown')
        return
    
    if key_str not in generated_keys:
        bot.reply_to(message, "❌ Key không tồn tại hoặc không hợp lệ.")
        return

    key_info = generated_keys[key_str]
    
    if key_info.get('limit') is not None and key_info.get('used_count', 0) >= key_info['limit']:
        bot.reply_to(message, "❌ Key này đã đạt giới hạn số lần sử dụng.")
        return

    # Apply extension
    current_expiry_str = user_data.get(user_id, {}).get('expiry_date')
    if current_expiry_str:
        current_expiry_date = datetime.strptime(current_expiry_str, '%Y-%m-%d %H:%M:%S')
        # If current expiry is in the past, start from now
        if datetime.now() > current_expiry_date:
            new_expiry_date = datetime.now()
        else:
            new_expiry_date = current_expiry_date
    else:
        new_expiry_date = datetime.now() # Start from now if no previous expiry

    value = key_info['value']
    if key_info['unit'] == 'ngày':
        new_expiry_date += timedelta(days=value)
    elif key_info['unit'] == 'giờ':
        new_expiry_date += timedelta(hours=value)
    
    # Cập nhật thông tin user
    user_data.setdefault(user_id, {})['expiry_date'] = new_expiry_date.strftime('%Y-%m-%d %H:%M:%S')
    user_data[user_id]['username'] = message.from_user.username or message.from_user.first_name
    
    # Cập nhật thông tin key
    key_info['used_count'] = key_info.get('used_count', 0) + 1
    key_info['activated_by'] = user_id
    key_info['activated_time'] = datetime.now().strftime('%Y-%m-%d %H:%M:%S')

    save_user_data(user_data)
    save_keys()

    bot.reply_to(message, 
                 f"🎉 Bạn đã đổi key thành công! Tài khoản của bạn đã được gia hạn thêm **{value} {key_info['unit']}**.\n"
                 f"Ngày hết hạn mới: `{get_user_expiry_date(user_id)}`", 
                 parse_mode='Markdown')

def get_user_expiry_date(user_id):
    if str(user_id) in user_data and user_data[str(user_id)].get('expiry_date'):
        return user_data[str(user_id)]['expiry_date']
    return "Không có"

@bot.message_handler(commands=['chaybot'])
def prompt_game_selection(message):
    user_id = str(message.chat.id)
    is_valid_key, msg = check_key_validity(message.chat.id)

    if not is_valid_key:
        bot.reply_to(message, msg + "\nVui lòng kích hoạt bot bằng key trước. Dùng `/key <mã_key>`.", parse_mode='Markdown')
        return

    markup = types.InlineKeyboardMarkup(row_width=2)
    for game_name in GAME_APIS.keys():
        markup.add(types.InlineKeyboardButton(game_name, callback_data=f"select_game_{game_name}"))
    
    bot.reply_to(message, "✅ Bạn có quyền truy cập. Vui lòng chọn game bạn muốn nhận dự đoán:", reply_markup=markup)

@bot.callback_query_handler(func=lambda call: call.data.startswith('select_game_'))
def callback_select_game(call):
    user_id = str(call.message.chat.id)
    game_name = call.data.replace('select_game_', '')

    if game_name not in GAME_APIS:
        bot.send_message(call.message.chat.id, "Game không hợp lệ. Vui lòng thử lại.")
        return

    # Kích hoạt nhận dự đoán và lưu game ưu tiên
    user_data.setdefault(user_id, {})['receiving_predictions'] = True
    user_data[user_id]['preferred_game'] = game_name
    save_user_data(user_data)

    bot.edit_message_text(chat_id=call.message.chat.id, 
                          message_id=call.message.message_id,
                          text=f"Tuyệt vời! Bạn đã chọn **{game_name}**. Bot sẽ bắt đầu gửi dự đoán cho game này.\n"
                               "Nếu muốn tắt thông báo, dùng lệnh `/tatbot`.\n"
                               "Nếu muốn đổi game, dùng lại lệnh `/chaybot`.",
                          parse_mode='Markdown')
    
    if not bot_enabled_global:
        bot.send_message(call.message.chat.id, f"⚠️ Lưu ý: Bot dự đoán hiện đang tạm dừng toàn cục bởi Admin. Lý do: `{bot_disable_reason_global}`. Bạn sẽ nhận thông báo khi bot được mở lại.", parse_mode='Markdown')


@bot.message_handler(commands=['tatbot'])
def disable_user_predictions(message):
    user_id = str(message.chat.id)
    
    if user_id not in user_data or not user_data[user_id].get('receiving_predictions', False):
        bot.reply_to(message, "Bạn chưa bật nhận thông báo dự đoán.")
        return

    user_data[user_id]['receiving_predictions'] = False
    save_user_data(user_data)
    bot.reply_to(message, "❌ Bạn đã tắt nhận thông báo dự đoán.")

@bot.message_handler(commands=['lichsu'])
def show_prediction_history_command(message):
    args = telebot.util.extract_arguments(message.text).split()
    user_id = str(message.chat.id)

    game_name = None
    if args and args[0] in GAME_APIS:
        game_name = args[0]
    elif user_id in user_data and user_data[user_id].get('preferred_game'):
        game_name = user_data[user_id]['preferred_game']
    
    if not game_name:
        bot.reply_to(message, "Vui lòng chỉ định game muốn xem lịch sử (ví dụ: `/lichsu LuckyWin`) hoặc bật nhận dự đoán cho một game trước.", parse_mode='Markdown')
        return

    if not prediction_history[game_name]:
        bot.reply_to(message, f"Hiện chưa có lịch sử dự đoán nào cho game **{game_name}**.", parse_mode='Markdown')
        return
    
    history_text = f"📜 **LỊCH SỬ 10 PHIÊN GẦN NHẤT - {game_name.upper()}** 📜\n\n"
    for entry in reversed(prediction_history[game_name]): # Hiển thị các phiên mới nhất trước
        history_text += f"- {entry}\n"
    
    bot.reply_to(message, history_text, parse_mode='Markdown')


# --- Lệnh Admin ---

@bot.message_handler(commands=['taokey'])
def generate_key_command(message):
    if not is_admin(message.chat.id):
        bot.reply_to(message, "Bạn không có quyền sử dụng lệnh này.")
        return
    
    args = telebot.util.extract_arguments(message.text).split()
    if len(args) < 2:
        bot.reply_to(message, "Cú pháp sai. Ví dụ:\n"
                              "`/taokey <giá_trị> <đơn_vị> [số_lượng]`\n"
                              "Ví dụ: `/taokey 1 ngày 5` (tạo 5 key 1 ngày)\n"
                              "Hoặc: `/taokey 24 giờ` (tạo 1 key 24 giờ)", parse_mode='Markdown')
        return
    
    try:
        value = int(args[0])
        unit = args[1].lower()
        quantity = int(args[2]) if len(args) > 2 and args[2].isdigit() else 1 # Mặc định tạo 1 key nếu không có số lượng
        
        if unit not in ['ngày', 'giờ']:
            bot.reply_to(message, "Đơn vị không hợp lệ. Chỉ chấp nhận `ngày` hoặc `giờ`.", parse_mode='Markdown')
            return
        if value <= 0 or quantity <= 0:
            bot.reply_to(message, "Giá trị hoặc số lượng phải lớn hơn 0.", parse_mode='Markdown')
            return

        created_keys_list = []
        for _ in range(quantity):
            new_key = ''.join(random.choices(string.ascii_uppercase + string.digits, k=8)) # 8 ký tự ngẫu nhiên
            generated_keys[new_key] = {
                "created_by": str(message.chat.id),
                "created_time": datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
                "value": value,
                "unit": unit,
                "limit": 1, # Mặc định mỗi key chỉ dùng 1 lần
                "used_count": 0,
                "activated_by": None,
                "activated_time": None
            }
            created_keys_list.append(new_key)
        
        save_keys()
        
        response_text = f"✅ Đã tạo thành công {quantity} key gia hạn **{value} {unit}**:\n\n"
        response_text += "\n".join([f"`{code}`" for code in created_keys_list])
        response_text += "\n\n_(Các key này chưa được sử dụng)_"
        
        bot.reply_to(message, response_text, parse_mode='Markdown')

    except ValueError:
        bot.reply_to(message, "Giá trị hoặc số lượng không hợp lệ. Vui lòng nhập số nguyên.", parse_mode='Markdown')
    except Exception as e:
        bot.reply_to(message, f"Đã xảy ra lỗi khi tạo key: {e}", parse_mode='Markdown')

@bot.message_handler(commands=['lietkekey'])
def list_keys_command(message):
    if not is_admin(message.chat.id):
        bot.reply_to(message, "Bạn không có quyền sử dụng lệnh này.")
        return
    
    if not generated_keys:
        bot.reply_to(message, "Chưa có key nào được tạo.")
        return
    
    response_text = "🔑 **DANH SÁCH KEY ĐÃ TẠO** 🔑\n\n"
    for key_str, key_info in generated_keys.items():
        status = "Đã dùng" if key_info.get('used_count', 0) >= key_info.get('limit', 1) else "Chưa dùng"
        if key_info.get('used_count', 0) > 0:
            status += f" ({key_info.get('used_count')}/{key_info.get('limit', 1)})"
        
        response_text += (
            f"`{key_str}` - `{key_info['value']} {key_info['unit']}` | Trạng thái: **{status}**\n"
            f"   _Tạo bởi: {key_info.get('created_by', 'N/A')} vào {key_info.get('created_time', 'N/A')}_\n"
        )
        if key_info.get('activated_by'):
            activated_username = user_data.get(str(key_info['activated_by']), {}).get('username', key_info['activated_by'])
            response_text += f"   _Kích hoạt bởi: @{activated_username} vào {key_info.get('activated_time', 'N/A')}_\n"
        response_text += "\n"
    
    bot.reply_to(message, response_text, parse_mode='Markdown')

@bot.message_handler(commands=['xoakey'])
def delete_key_command(message):
    if not is_admin(message.chat.id):
        bot.reply_to(message, "Bạn không có quyền sử dụng lệnh này.")
        return
    
    key_to_delete = telebot.util.extract_arguments(message.text)
    if not key_to_delete:
        bot.reply_to(message, "Vui lòng nhập key muốn xóa. Ví dụ: `/xoakey ABCXYZ`", parse_mode='Markdown')
        return
    
    if key_to_delete in generated_keys:
        del generated_keys[key_to_delete]
        save_keys()
        bot.reply_to(message, f"✅ Đã xóa key `{key_to_delete}`.")
    else:
        bot.reply_to(message, f"❌ Key `{key_to_delete}` không tồn tại.")

@bot.message_handler(commands=['themadmin'])
def add_admin_command(message):
    if not is_admin(message.chat.id):
        bot.reply_to(message, "Bạn không có quyền sử dụng lệnh này.")
        return
    
    args = telebot.util.extract_arguments(message.text).split()
    if not args or not args[0].isdigit():
        bot.reply_to(message, "Cú pháp sai. Ví dụ: `/themadmin <id_nguoi_dung>`", parse_mode='Markdown')
        return
    
    target_user_id_str = args[0]
    
    if target_user_id_str not in user_data:
        # Nếu user chưa từng start bot, tạo entry mới
        user_data[target_user_id_str] = {
            'username': "UnknownUser",
            'expiry_date': None,
            'is_admin': True,
            'receiving_predictions': False,
            'preferred_game': None
        }
    else:
        user_data[target_user_id_str]['is_admin'] = True
    
    save_user_data(user_data)
    bot.reply_to(message, f"Đã cấp quyền Admin cho user ID `{target_user_id_str}`.")
    try:
        bot.send_message(int(target_user_id_str), "🎉 Bạn đã được cấp quyền Admin!")
    except Exception:
        pass

@bot.message_handler(commands=['xoaadmin'])
def remove_admin_command(message):
    if not is_admin(message.chat.id):
        bot.reply_to(message, "Bạn không có quyền sử dụng lệnh này.")
        return
    
    args = telebot.util.extract_arguments(message.text).split()
    if not args or not args[0].isdigit():
        bot.reply_to(message, "Cú pháp sai. Ví dụ: `/xoaadmin <id_nguoi_dung>`", parse_mode='Markdown')
        return
    
    target_user_id_str = args[0]

    if int(target_user_id_str) in SUPER_ADMIN_IDS:
        bot.reply_to(message, "Bạn không thể xóa quyền admin của Super Admin.")
        return

    if target_user_id_str in user_data and user_data[target_user_id_str].get('is_admin'):
        user_data[target_user_id_str]['is_admin'] = False
        save_user_data(user_data)
        bot.reply_to(message, f"Đã xóa quyền Admin của user ID `{target_user_id_str}`.")
        try:
            bot.send_message(int(target_user_id_str), "❌ Quyền Admin của bạn đã bị gỡ bỏ.")
        except Exception:
            pass
    else:
        bot.reply_to(message, f"User ID `{target_user_id_str}` không phải Admin hoặc không tồn tại.")

@bot.message_handler(commands=['danhsachadmin'])
def list_admins_command(message):
    if not is_admin(message.chat.id):
        bot.reply_to(message, "Bạn không có quyền sử dụng lệnh này.")
        return
    
    admin_list = []
    for user_id_str, user_info in user_data.items():
        if user_info.get('is_admin'):
            username = user_info.get('username', f"ID: {user_id_str}")
            admin_list.append(f"- @{username} (ID: `{user_id_str}`)")
            
    if not admin_list:
        bot.reply_to(message, "Chưa có Admin nào được thêm vào hệ thống.")
        return

    response_text = "🛡️ **DANH SÁCH ADMIN** 🛡️\n\n" + "\n".join(admin_list)
    bot.reply_to(message, response_text, parse_mode='Markdown')

@bot.message_handler(commands=['broadcast'])
def send_broadcast(message):
    if not is_admin(message.chat.id):
        bot.reply_to(message, "Bạn không có quyền sử dụng lệnh này.")
        return
    
    broadcast_text = telebot.util.extract_arguments(message.text)
    if not broadcast_text:
        bot.reply_to(message, "Vui lòng nhập nội dung thông báo. Ví dụ: `/broadcast Bot sẽ bảo trì vào 2h sáng mai.`", parse_mode='Markdown')
        return
    
    success_count = 0
    fail_count = 0
    for user_id_str in list(user_data.keys()):
        try:
            bot.send_message(int(user_id_str), f"📢 **THÔNG BÁO TỪ ADMIN** 📢\n\n{broadcast_text}", parse_mode='Markdown')
            success_count += 1
            time.sleep(0.1) # Tránh bị rate limit
        except telebot.apihelper.ApiTelegramException as e:
            print(f"Không thể gửi thông báo cho user {user_id_str}: {e}")
            fail_count += 1
            if "bot was blocked by the user" in str(e) or "user is deactivated" in str(e):
                print(f"Người dùng {user_id_str} đã chặn bot hoặc bị vô hiệu hóa.")
        except Exception as e:
            print(f"Lỗi không xác định khi gửi thông báo cho user {user_id_str}: {e}")
            fail_count += 1
            
    bot.reply_to(message, f"Đã gửi thông báo đến {success_count} người dùng. Thất bại: {fail_count}.")

@bot.message_handler(commands=['tatbot_global'])
def disable_bot_global_command(message):
    global bot_enabled_global, bot_disable_reason_global
    if not is_admin(message.chat.id):
        bot.reply_to(message, "Bạn không có quyền sử dụng lệnh này.")
        return

    reason = telebot.util.extract_arguments(message.text)
    if not reason:
        bot.reply_to(message, "Vui lòng nhập lý do tắt bot toàn cục. Ví dụ: `/tatbot_global Bot đang bảo trì.`", parse_mode='Markdown')
        return

    bot_enabled_global = False
    bot_disable_reason_global = reason
    bot.reply_to(message, f"✅ Bot dự đoán đã được tắt TOÀN CỤC bởi Admin `{message.from_user.username or message.from_user.first_name}`.\nLý do: `{reason}`", parse_mode='Markdown')
    
@bot.message_handler(commands=['mokbot_global'])
def enable_bot_global_command(message):
    global bot_enabled_global, bot_disable_reason_global
    if not is_admin(message.chat.id):
        bot.reply_to(message, "Bạn không có quyền sử dụng lệnh này.")
        return

    if bot_enabled_global:
        bot.reply_to(message, "Bot dự đoán đã và đang hoạt động toàn cục rồi.")
        return

    bot_enabled_global = True
    bot_disable_reason_global = "Không có"
    bot.reply_to(message, "✅ Bot dự đoán đã được mở lại TOÀN CỤC bởi Admin.")

# --- Flask Routes cho Keep-Alive ---
@app.route('/')
def home():
    return "Bot is alive and running!"

@app.route('/health')
def health_check():
    return "OK", 200

# --- Khởi tạo bot và các luồng khi Flask app khởi động ---
@app.before_request
def start_bot_threads():
    global bot_initialized
    with bot_init_lock:
        if not bot_initialized:
            print("Initializing bot and prediction threads...")
            # Load initial data
            load_user_data()
            load_keys()

            # Start prediction loop in a separate thread
            prediction_thread = Thread(target=prediction_loop, args=(Event(),)) # Pass a new Event for each run
            prediction_thread.daemon = True
            prediction_thread.start()
            print("Prediction loop thread started.")

            # Start bot polling in a separate thread
            polling_thread = Thread(target=bot.infinity_polling, kwargs={'none_stop': True})
            polling_thread.daemon = True
            polling_thread.start()
            print("Telegram bot polling thread started.")
            
            bot_initialized = True

# --- Điểm khởi chạy chính cho Gunicorn/Render ---
if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    print(f"Starting Flask app locally on port {port}")
    app.run(host='0.0.0.0', port=port, debug=True)

