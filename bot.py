import os
import telebot
from telebot import types
import json # Vẫn cần để lưu user_data
import asyncio
import threading
import time
from datetime import datetime, timedelta
import logging
import random
from flask import Flask, request, abort
import requests

# ==============================================================================
# 1. CẤU HÌNH BAN ĐẦU & LOGGING
# ==============================================================================

# Cấu hình Logging
LOG_FILE = "bot_logs.log"
logging.basicConfig(level=logging.INFO,
                    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
                    handlers=[
                        logging.FileHandler(LOG_FILE, encoding='utf-8'),
                        logging.StreamHandler() # Để xuất log ra console/terminal
                    ])
logger = logging.getLogger(__name__)

# Tên các file dữ liệu (Sẽ được lưu tạm thời nếu không có Persistent Disk)
USER_DATA_FILE = "user_data.json"
DULIEU_AI_FILE = "dulieu_ai.json" # Có thể dùng cho các logic AI phức tạp hơn
PATTERN_COUNT_FILE = "pattern_counter.json" # Dành cho AI tự học

# Tên file chứa các mẫu dự đoán cứng (đã đổi thành .txt)
DUDOAN_PATTERNS_FILE = "dudoan.txt" # Đã đổi

# Cấu hình Token Bot
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", '8080593458:AAFjVM7hVLrv9AzV6WUU5ttpXc1vMRrEtSk') # THAY BẰNG TOKEN THẬT CỦA BẠN

if not TELEGRAM_BOT_TOKEN or TELEGRAM_BOT_TOKEN == '8080593458:AAFjVM7hVLrv9AzV6WUU5ttpXc1vMRrEtSk':
    logger.critical("LỖI: TELEGRAM_BOT_TOKEN chưa được cấu hình hoặc vẫn là token mẫu. Bot sẽ không thể khởi động.")
    exit()

# Khởi tạo Bot
bot = telebot.TeleBot(TELEGRAM_BOT_TOKEN, parse_mode='HTML')

# Dữ liệu toàn cục (sẽ được khởi tạo rỗng mỗi lần bot khởi động nếu không có disk)
user_data = {}
dulieu_ai = {} # Dành cho logic AI phức tạp hơn
pattern_counter = {} # Dành cho AI tự học

# API Key và Endpoint của game
GAME_API_KEY = "Quangdz" # Đặt key của bạn ở đây
GAME_API_ENDPOINT = f"http://157.10.52.15:3000/api/sunwin?key={GAME_API_KEY}"

# Dữ liệu dự đoán từ file dudoan.txt (sẽ là list of dicts: {"cau": "...", "du_doan": "..."})
dudoan_patterns = []

# Đặt ADMIN_ID từ ảnh của bạn
ADMIN_ID = 6915752059

# Biến để theo dõi phiên cuối cùng đã xử lý
last_processed_phien = None

# ==============================================================================
# 2. HÀM TIỆN ÍCH CHO FILE DỮ LIỆU
# ==============================================================================

def load_json_data(file_path, default_value={}):
    """Tải dữ liệu từ file JSON. Sẽ trả về giá trị mặc định nếu file không tồn tại."""
    if not os.path.exists(file_path):
        logger.warning(f"File {file_path} không tồn tại. Trả về giá trị mặc định.")
        return default_value
    try:
        with open(file_path, 'r', encoding='utf-8') as f:
            return json.load(f)
    except json.JSONDecodeError:
        logger.error(f"Lỗi đọc JSON từ file: {file_path}. Trả về giá trị mặc định.")
        return default_value
    except Exception as e:
        logger.error(f"Lỗi khi tải dữ liệu từ {file_path}: {e}")
        return default_value

def save_json_data(data, file_path):
    """Lưu dữ liệu vào file JSON. Dữ liệu này sẽ mất khi bot khởi động lại nếu không có Persistent Disk."""
    try:
        with open(file_path, 'w', encoding='utf-8') as f:
            json.dump(data, f, indent=4, ensure_ascii=False)
        logger.info(f"Đã lưu dữ liệu vào {file_path} (tạm thời).")
    except Exception as e:
        logger.error(f"Lỗi khi lưu dữ liệu vào {file_path}: {e}")

# Hàm mới để đọc file dudoan.txt
def load_text_patterns(file_path):
    """Tải các mẫu dự đoán từ file text (dudoan.txt)."""
    patterns = []
    if not os.path.exists(file_path):
        logger.warning(f"File {file_path} không tồn tại. Không tải mẫu dự đoán.")
        return patterns
    try:
        with open(file_path, 'r', encoding='utf-8') as f:
            for line in f:
                line = line.strip()
                if not line or '=> Dự đoán:' not in line:
                    continue
                try:
                    # Tách phần mẫu cầu và phần dự đoán
                    parts = line.split('=> Dự đoán:', 1)
                    cau_pattern = parts[0].strip()
                    prediction_info = parts[1].strip()

                    # Trích xuất dự đoán (T hoặc X)
                    # Ví dụ: "T - Loại cầu: Cầu bệt" -> "T"
                    predicted_char = prediction_info.split('-')[0].strip()

                    if predicted_char in ['T', 'X']:
                        patterns.append({"cau": cau_pattern, "du_doan": predicted_char})
                    else:
                        logger.warning(f"Dự đoán không hợp lệ trong dòng '{line}'. Phải là 'T' hoặc 'X'.")

                except Exception as e:
                    logger.warning(f"Không thể phân tích dòng mẫu dự đoán: '{line}' - Lỗi: {e}")
                    continue
        logger.info(f"Đã tải {len(patterns)} mẫu dự đoán từ {file_path}.")
    except Exception as e:
        logger.error(f"Lỗi khi tải mẫu dự đoán từ {file_path}: {e}")
    return patterns


def save_user_data():
    """Lưu dữ liệu user_data vào file."""
    save_json_data(user_data, USER_DATA_FILE)

def get_user_info_by_chat_id(chat_id):
    """Tìm thông tin key và user_info dựa trên chat_id."""
    for key_name, info in user_data.items():
        if info.get('current_chat_id') == chat_id or chat_id in info.get('assigned_chat_ids', []):
            return key_name, info
    return None, None

def get_user_info_by_key(key_name):
    """Tìm thông tin user_info dựa trên tên key."""
    return user_data.get(key_name.lower(), None)

# ==============================================================================
# 3. HÀM GỌI API VÀ XỬ LÝ DỮ LIỆU GAME
# ==============================================================================

async def fetch_game_data():
    """Gọi API để lấy dữ liệu game (phiên, kết quả, lịch sử cầu)."""
    try:
        response = await asyncio.to_thread(requests.get, GAME_API_ENDPOINT, timeout=10)
        response.raise_for_status() # Ném lỗi nếu status code là 4xx hoặc 5xx

        # API của bạn không trả về JSON, mà là text.
        # Chúng ta cần phân tích text này.
        data_text = response.text
        logger.info(f"Dữ liệu API thô nhận được: {data_text}")

        # Phân tích dữ liệu text
        parsed_data = parse_api_data(data_text)
        return parsed_data

    except requests.exceptions.RequestException as e:
        logger.error(f"Lỗi khi gọi API game: {e}")
        return None
    except Exception as e:
        logger.error(f"Lỗi không xác định khi lấy dữ liệu game từ API: {e}", exc_info=True)
        return None

def parse_api_data(data_text):
    """Phân tích dữ liệu text từ API thành dictionary."""
    parsed = {}
    lines = data_text.split('\n')
    for line in lines:
        line = line.strip()
        if not line:
            continue
        try:
            if ':' in line:
                key, value = line.split(':', 1)
                key = key.strip().replace(' ', '_').lower() # Chuyển "Phiên Trước" thành "phien_truoc"
                value = value.strip()
                parsed[key] = value
        except Exception as e:
            logger.warning(f"Không thể phân tích dòng API: '{line}' - Lỗi: {e}")
            continue
    return parsed

def get_predicted_outcome(lich_su_cau):
    """
    So sánh lịch sử cầu với các mẫu dự đoán trong dudoan_patterns.
    Trả về dự đoán nếu tìm thấy mẫu, ngược lại trả về None.
    """
    if not lich_su_cau:
        return None

    # Duyệt qua các mẫu dự đoán từ file dudoan.txt
    for pattern_entry in dudoan_patterns:
        pattern = pattern_entry.get("cau", "")
        prediction = pattern_entry.get("du_doan", "")

        # Đảm bảo lịch sử cầu đủ dài để khớp với mẫu
        if len(lich_su_cau) >= len(pattern):
            # Lấy phần cuối của lịch sử cầu có độ dài bằng mẫu
            current_suffix = lich_su_cau[-len(pattern):]
            if current_suffix == pattern:
                logger.info(f"Tìm thấy mẫu cầu khớp: '{pattern}' -> Dự đoán: '{prediction}' cho lịch sử: {lich_su_cau}")
                return prediction
    logger.info(f"Không tìm thấy mẫu cầu khớp cho lịch sử: {lich_su_cau}")
    return None

# ==============================================================================
# 4. CHỨC NĂNG CHÍNH CỦA BOT
# ==============================================================================

async def send_telegram_message(chat_id, message_text, disable_notification=False):
    """Gửi tin nhắn đến một chat_id cụ thể."""
    try:
        await asyncio.to_thread(bot.send_message,
                                chat_id=chat_id,
                                text=message_text,
                                parse_mode='HTML',
                                disable_notification=disable_notification)
        logger.info(f"Đã gửi tin nhắn đến {chat_id} thành công.")
    except telebot.apihelper.ApiTelegramException as e:
        logger.warning(f"Lỗi Telegram API khi gửi tin nhắn tới {chat_id}: {e}")
        if "bot was blocked by the user" in str(e) or "chat not found" in str(e):
            logger.warning(f"Người dùng {chat_id} đã chặn bot hoặc chat không tồn tại. Đang hủy kích hoạt key nếu tìm thấy.")
            key_name, user_info = get_user_info_by_chat_id(chat_id)
            if user_info:
                user_info['is_receiving_predictions'] = False
                if chat_id in user_info.get('assigned_chat_ids', []):
                    user_info['assigned_chat_ids'].remove(chat_id)
                if user_info.get('current_chat_id') == chat_id:
                    user_info['current_chat_id'] = None
                save_user_data()
                logger.info(f"Đã hủy kích hoạt key '{key_name}' cho chat_id {chat_id} do lỗi gửi tin nhắn.")
        elif "Too Many Requests" in str(e):
            logger.warning(f"Đạt giới hạn Rate Limit khi gửi tin nhắn tới {chat_id}. Thử lại sau.")
    except Exception as e:
        logger.error(f"Lỗi không xác định khi gửi tin nhắn tới {chat_id}: {e}", exc_info=True)

async def check_and_send_predictions():
    """Kiểm tra và gửi dự đoán cho các key đang hoạt động dựa trên dữ liệu game."""
    global last_processed_phien

    game_data = await fetch_game_data()
    if not game_data:
        logger.error("Không thể lấy dữ liệu game từ API. Bỏ qua vòng kiểm tra này.")
        return

    current_phien = game_data.get('phien_hien_tai')
    if not current_phien:
        logger.warning("Không tìm thấy 'Phiên Hiện Tại' trong dữ liệu API.")
        return

    # Chỉ xử lý khi có phiên mới
    if current_phien == last_processed_phien:
        # logger.info(f"Phiên {current_phien} đã được xử lý. Đợi phiên mới.")
        return

    logger.info(f"Phát hiện phiên mới: {current_phien}. Đang xử lý dự đoán.")
    last_processed_phien = current_phien

    prediction_message = await create_prediction_message(game_data)
    if not prediction_message:
        logger.warning(f"Không thể tạo tin nhắn dự đoán cho phiên {current_phien}.")
        return

    for key_name, info in list(user_data.items()):
        if info.get('is_receiving_predictions') and info.get('current_chat_id'):
            # Kiểm tra thời hạn của key nếu không phải admin
            if not info.get('is_admin'):
                expiry_time_str = info.get('expiry_time')
                if expiry_time_str:
                    expiry_time = datetime.fromisoformat(expiry_time_str)
                    if datetime.now() < expiry_time:
                        await send_telegram_message(info['current_chat_id'], prediction_message)
                        logger.info(f"Đã gửi dự đoán tới key '{key_name}' (chat_id: {info['current_chat_id']}) cho phiên {current_phien}.")
                    else:
                        info['is_receiving_predictions'] = False
                        save_user_data()
                        await send_telegram_message(info['current_chat_id'],
                                                    "⚠️ **Thông báo:**\nKey của bạn đã hết hạn. Vui lòng liên hệ Admin để gia hạn.")
                        logger.info(f"Key '{key_name}' của người dùng {info['current_chat_id']} đã hết hạn.")
                else:
                    # Nếu là user và không có expiry_time (do không dùng disks, hoặc admin không set)
                    info['is_receiving_predictions'] = False
                    save_user_data()
                    await send_telegram_message(info['current_chat_id'],
                                                "⚠️ **Thông báo:**\nKey của bạn không có thông tin thời hạn hoặc đã hết hạn. Vui lòng liên hệ Admin.")
                    logger.warning(f"Key '{key_name}' của người dùng {info['current_chat_id']} không có thời hạn hoặc thông tin hết hạn bị thiếu.")
            else: # Admin luôn nhận dự đoán nếu đang bật
                await send_telegram_message(info['current_chat_id'], prediction_message)
                logger.info(f"Đã gửi dự đoán tới Admin key '{key_name}' (chat_id: {info['current_chat_id']}) cho phiên {current_phien}.")


async def create_prediction_message(game_data):
    """Tạo nội dung tin nhắn dự đoán dựa trên dữ liệu game và logic dự đoán."""
    phien_truoc = game_data.get('phien_truoc', 'N/A')
    ket_qua = game_data.get('ket_qua', 'N/A')
    xuc_xac = game_data.get('xuc_xac', 'N/A')
    phien_hien_tai = game_data.get('phien_hien_tai', 'N/A')
    lich_su_cau = game_data.get('cau', '')

    predicted_outcome = get_predicted_outcome(lich_su_cau)

    prediction_text = ""
    if predicted_outcome:
        prediction_text = f"✨ **Dự đoán:** `{predicted_outcome}`"
    else:
        prediction_text = "🚫 **Bỏ qua phiên này!** (Không tìm thấy mẫu khớp)"

    now = datetime.now()
    formatted_time = now.strftime("%H:%M:%S")

    message = (
        f"🤖 **TOOL TX PRO AI**\n"
        f"⏳ **Thời gian hiện tại:** `{formatted_time}`\n\n"
        f"🌀 **Phiên Trước:** `{phien_truoc}`\n"
        f"✅ **Kết Quả:** `{ket_qua}`\n"
        f"🎲 **Xúc Xắc:** `{xuc_xac}`\n\n"
        f"🔄 **Phiên Hiện Tại:** `{phien_hien_tai}`\n"
        f"📈 **Lịch sử 8 cầu gần nhất:** `{lich_su_cau}`\n\n"
        f"{prediction_text}\n\n"
        f"**Chúc bạn may mắn!**\n"
        f"💡 Lưu ý: Đây chỉ là dự đoán dựa trên các mẫu và AI, không đảm bảo thắng 100%."
    )
    return message


# ==============================================================================
# 5. HANDLERS LỆNH TELEGRAM & LỆNH ADMIN (Giữ nguyên)
# ==============================================================================

# Dán toàn bộ các hàm từ @bot.message_handler(commands=['start'])
# đến @bot.message_handler(commands=['captime']) vào đây.
# (Các hàm này không cần thay đổi gì)

@bot.message_handler(commands=['start'])
async def start_command_handler(message):
    chat_id = message.chat.id
    key_name, user_info = get_user_info_by_chat_id(chat_id)

    if user_info:
        user_info['is_receiving_predictions'] = True
        user_info['current_chat_id'] = chat_id
        if chat_id not in user_info.get('assigned_chat_ids', []):
            user_info.setdefault('assigned_chat_ids', []).append(chat_id)
        save_user_data()
        await send_telegram_message(chat_id, "✅ **Chào mừng bạn quay lại!**\nBạn đã bắt đầu nhận dự đoán từ Bot. Sử dụng `/stop` để tạm dừng.")
        logger.info(f"Người dùng {chat_id} (key: {key_name}) đã bấm /start. Đã bật nhận dự đoán.")
    else:
        await send_telegram_message(chat_id, "🤖 **Chào mừng bạn đến với Tool TX Pro AI!**\nĐể sử dụng bot, vui lòng nhập key của bạn theo cú pháp: `/key [tên_key_của_bạn]`\n\nNếu bạn là Admin hoặc CTV của Quangdz, hãy nhập key mặc định của bạn (ví dụ: `/key quangdz`).\n\nSử dụng `/help` để xem các lệnh hỗ trợ.")
        logger.info(f"Người dùng mới {chat_id} đã bấm /start. Đang chờ key.")

@bot.message_handler(commands=['help'])
async def help_command_handler(message):
    chat_id = message.chat.id
    key_name, user_info = get_user_info_by_chat_id(chat_id)

    help_message = (
        "📚 **Hướng dẫn sử dụng Tool TX Pro AI**\n\n"
        "Các lệnh phổ biến:\n"
        "• `/start`: Bắt đầu/tiếp tục nhận dự đoán.\n"
        "• `/stop`: Tạm dừng nhận dự đoán.\n"
        "• `/key [tên_key]`: Nhập key để sử dụng bot. Ví dụ: `/key quangdz`\n"
    )

    if user_info and user_info.get('is_admin'):
        help_message += (
            "\n👑 **Lệnh Admin:**\n"
            "• `/viewkeys`: Xem danh sách tất cả các key.\n"
            "• `/addkey [tên_key] [Admin/User] [thời_hạn_giờ]`: Tạo key mới. Ví dụ: `/addkey testkey User 72` (key dùng 3 ngày).\n"
            "• `/delkey [tên_key]`: Xóa một key.\n"
            "• `/capkey [chat_id] [tên_key] [thời_hạn_giờ]`: Gán key có sẵn cho một chat_id. Ví dụ: `/capkey 123456789 testkey 24` (gán key 'testkey' cho chat_id '123456789' dùng 1 ngày).\n"
            "• `/adminkey [tên_key]`: Cấp quyền admin cho một key.\n"
            "• `/unadminkey [tên_key]`: Hủy quyền admin của một key.\n"
            "• `/statuskey [tên_key]`: Xem trạng thái chi tiết của một key.\n"
            "• `/kick [chat_id]`: Gỡ key khỏi một chat_id và hủy nhận dự đoán.\n"
            "• `/resetai`: Xóa dữ liệu AI đã học (pattern_counter và dulieu_ai).\n"
            "• `/captime [tên_key] [thời_gian_giờ]`: Gia hạn thời gian cho key. Ví dụ: `/captime testkey 24` (gia hạn thêm 24 giờ).\n"
        )
    help_message += "\nNếu có bất kỳ thắc mắc nào, vui lòng liên hệ Admin."
    await send_telegram_message(chat_id, help_message)
    logger.info(f"Người dùng {chat_id} đã yêu cầu trợ giúp.")

@bot.message_handler(commands=['key'])
async def key_command_handler(message):
    chat_id = message.chat.id
    args = message.text.split(maxsplit=1)
    if len(args) < 2:
        await send_telegram_message(chat_id, "❌ **Sai cú pháp.**\nVui lòng nhập key theo cú pháp: `/key [tên_key_của_bạn]`")
        return

    input_key = args[1].strip().lower()
    user_info = get_user_info_by_key(input_key)

    if user_info:
        current_linked_chat_id = user_info.get('current_chat_id')
        if current_linked_chat_id and current_linked_chat_id != chat_id and not user_info.get('is_admin'):
            await send_telegram_message(chat_id, "⚠️ **Key này đang được sử dụng bởi một thiết bị khác.**\nVui lòng liên hệ Admin nếu bạn tin đây là lỗi.")
            logger.warning(f"Người dùng {chat_id} cố gắng sử dụng key '{input_key}' đang được dùng bởi {current_linked_chat_id}.")
            return

        expiry_time_str = user_info.get('expiry_time')
        if expiry_time_str:
            expiry_time = datetime.fromisoformat(expiry_time_str)
            if datetime.now() >= expiry_time:
                await send_telegram_message(chat_id, "⚠️ **Key của bạn đã hết hạn.**\nVui lòng liên hệ Admin để gia hạn.")
                user_info['is_receiving_predictions'] = False
                save_user_data()
                logger.info(f"Key '{input_key}' của người dùng {chat_id} đã hết hạn khi cố gắng đăng nhập.")
                return

        user_info['is_receiving_predictions'] = True
        user_info['current_chat_id'] = chat_id
        if chat_id not in user_info.get('assigned_chat_ids', []):
            user_info.setdefault('assigned_chat_ids', []).append(chat_id)
        save_user_data()
        await send_telegram_message(chat_id, "✅ **Xác thực key thành công!**\nBạn đã bắt đầu nhận dự đoán từ Bot. Sử dụng `/stop` để tạm dừng.")
        logger.info(f"Người dùng {chat_id} đã đăng nhập thành công với key: {input_key}.")
    else:
        await send_telegram_message(chat_id, "❌ **Key không hợp lệ hoặc không tồn tại.**\nVui lòng kiểm tra lại key của bạn hoặc liên hệ Admin.")
        logger.warning(f"Người dùng {chat_id} đã nhập key không hợp lệ: '{input_key}'.")

@bot.message_handler(commands=['stop'])
async def stop_command_handler(message):
    chat_id = message.chat.id
    key_name, user_info = get_user_info_by_chat_id(chat_id)

    if user_info:
        user_info['is_receiving_predictions'] = False
        save_user_data()
        await send_telegram_message(chat_id, "⏸️ **Đã tạm dừng nhận dự đoán.**\nSử dụng `/start` để tiếp tục.")
        logger.info(f"Người dùng {chat_id} (key: {key_name}) đã bấm /stop. Đã tắt nhận dự đoán.")
    else:
        await send_telegram_message(chat_id, "Bạn chưa đăng nhập bằng key nào. Không có dự đoán nào để dừng.")
        logger.info(f"Người dùng {chat_id} đã bấm /stop nhưng chưa đăng nhập.")

def is_admin(chat_id):
    """Kiểm tra xem người dùng có phải là admin hay không."""
    if chat_id == ADMIN_ID:
        return True
    for key_name, info in user_data.items():
        if (info.get('current_chat_id') == chat_id or chat_id in info.get('assigned_chat_ids', [])) and info.get('is_admin'):
            return True
    return False

@bot.message_handler(commands=['addkey'])
async def addkey_command_handler(message):
    chat_id = message.chat.id
    if not is_admin(chat_id):
        await send_telegram_message(chat_id, "🚫 **Bạn không có quyền sử dụng lệnh này.**")
        return

    args = message.text.split()
    if len(args) < 3:
        await send_telegram_message(chat_id, "❌ **Sai cú pháp.**\nSử dụng: `/addkey [tên_key] [Admin/User] [thời_hạn_giờ]`\nVí dụ: `/addkey testkey User 72` (key dùng 3 ngày)")
        return

    new_key = args[1].strip().lower()
    key_type = args[2].strip().lower()
    duration_hours = 0
    if len(args) >= 4:
        try:
            duration_hours = int(args[3])
        except ValueError:
            await send_telegram_message(chat_id, "❌ **Thời hạn phải là số nguyên (giờ).**")
            return

    if new_key in user_data:
        await send_telegram_message(chat_id, f"⚠️ Key `{new_key}` đã tồn tại. Vui lòng chọn tên key khác.")
        return

    is_admin_key = False
    if key_type == 'admin':
        is_admin_key = True
        expiry_time = None
        expiry_display = "Vĩnh viễn (Admin)"
    elif key_type == 'user':
        is_admin_key = False
        if duration_hours > 0:
            expiry_time = datetime.now() + timedelta(hours=duration_hours)
            expiry_display = expiry_time.strftime("%d-%m-%Y %H:%M:%S")
        else:
            expiry_time = None
            expiry_display = "Vĩnh viễn (Không khuyến khích cho User)"
    else:
        await send_telegram_message(chat_id, "❌ **Loại key không hợp lệ.** Vui lòng dùng `Admin` hoặc `User`.")
        return

    user_data[new_key] = {
        'is_admin': is_admin_key,
        'is_receiving_predictions': False,
        'current_chat_id': None,
        'assigned_chat_ids': [],
        'created_at': datetime.now().isoformat(),
        'expiry_time': expiry_time.isoformat() if expiry_time else None
    }
    save_user_data()
    await send_telegram_message(chat_id,
                                f"✅ **Đã thêm key mới:** `{new_key}`\n"
                                f"Loại: {'👑 Admin' if is_admin_key else '👤 User'}\n"
                                f"Thời hạn: {expiry_display}")
    logger.info(f"Admin {chat_id} đã thêm key mới: {new_key} (Type: {key_type}, Duration: {duration_hours}h).")

@bot.message_handler(commands=['delkey'])
async def delkey_command_handler(message):
    chat_id = message.chat.id
    if not is_admin(chat_id):
        await send_telegram_message(chat_id, "🚫 **Bạn không có quyền sử dụng lệnh này.**")
        return

    args = message.text.split(maxsplit=1)
    if len(args) < 2:
        await send_telegram_message(chat_id, "❌ **Sai cú pháp.**\nSử dụng: `/delkey [tên_key]`")
        return

    target_key = args[1].strip().lower()
    if target_key in user_data:
        if user_data[target_key].get('current_chat_id'):
            await send_telegram_message(user_data[target_key]['current_chat_id'],
                                        "⚠️ **Thông báo:**\nKey của bạn đã bị Admin gỡ bỏ. Bạn sẽ không nhận được dự đoán nữa.")
            logger.info(f"Đã thông báo cho người dùng {user_data[target_key]['current_chat_id']} về việc key '{target_key}' bị xóa.")

        del user_data[target_key]
        save_user_data()
        await send_telegram_message(chat_id, f"✅ **Đã xóa key:** `{target_key}`")
        logger.info(f"Admin {chat_id} đã xóa key: {target_key}.")
    else:
        await send_telegram_message(chat_id, f"⚠️ Key `{target_key}` không tồn tại.")

@bot.message_handler(commands=['viewkeys'])
async def viewkeys_command_handler(message):
    chat_id = message.chat.id
    if not is_admin(chat_id):
        await send_telegram_message(chat_id, "🚫 **Bạn không có quyền sử dụng lệnh này.**")
        return

    if not user_data:
        await send_telegram_message(chat_id, "📋 **Hiện không có key nào trong hệ thống.**")
        return

    response = "📋 **Danh sách các Key hiện có:**\n\n"
    for key, info in user_data.items():
        status = "🟢 Đang hoạt động" if info.get('is_receiving_predictions') else "🔴 Đang dừng"
        admin_status = "👑 Admin" if info.get('is_admin') else "👤 User"
        linked_chat_id = info.get('current_chat_id', 'N/A')
        assigned_ids = ', '.join(map(str, info.get('assigned_chat_ids', []))) if info.get('assigned_chat_ids') else 'N/A'

        expiry_time_str = info.get('expiry_time')
        expiry_display = "Vĩnh viễn"
        if expiry_time_str:
            expiry_time = datetime.fromisoformat(expiry_time_str)
            if expiry_time < datetime.now():
                expiry_display = f"Đã hết hạn ({expiry_time.strftime('%d/%m %H:%M')})"
            else:
                remaining_time = expiry_time - datetime.now()
                days = remaining_time.days
                hours, remainder = divmod(remaining_time.seconds, 3600)
                minutes, seconds = divmod(remainder, 60)
                expiry_display = f"Còn {days}d {hours}h {minutes}m ({expiry_time.strftime('%d/%m %H:%M')})"

        response += (
            f"🔑 `{key}`\n"
            f"  - Loại: {admin_status}\n"
            f"  - Trạng thái: {status}\n"
            f"  - Chat ID đang dùng: `{linked_chat_id}`\n"
            f"  - Các Chat ID đã gán: `{assigned_ids}`\n"
            f"  - Hạn dùng: {expiry_display}\n\n"
        )
    await send_telegram_message(chat_id, response)
    logger.info(f"Admin {chat_id} đã xem danh sách key.")


@bot.message_handler(commands=['capkey'])
async def capkey_command_handler(message):
    chat_id = message.chat.id
    if not is_admin(chat_id):
        await send_telegram_message(chat_id, "🚫 **Bạn không có quyền sử dụng lệnh này.**")
        return

    args = message.text.split()
    if len(args) < 4:
        await send_telegram_message(chat_id, "❌ **Sai cú pháp.**\nSử dụng: `/capkey [chat_id] [tên_key] [thời_hạn_giờ]`\nVí dụ: `/capkey 123456789 testkey 24`")
        return

    try:
        target_chat_id = int(args[1])
        target_key = args[2].strip().lower()
        duration_hours = int(args[3])
    except ValueError:
        await send_telegram_message(chat_id, "❌ **Chat ID hoặc thời hạn không hợp lệ.**")
        return

    user_info = get_user_info_by_key(target_key)
    if not user_info:
        await send_telegram_message(chat_id, f"⚠️ Key `{target_key}` không tồn tại. Vui lòng tạo key trước.")
        return

    user_info['is_receiving_predictions'] = True
    user_info['current_chat_id'] = target_chat_id
    if target_chat_id not in user_info.get('assigned_chat_ids', []):
        user_info.setdefault('assigned_chat_ids', []).append(target_chat_id)

    if duration_hours > 0:
        expiry_time = datetime.now() + timedelta(hours=duration_hours)
        user_info['expiry_time'] = expiry_time.isoformat()
        expiry_display = expiry_time.strftime("%d-%m-%Y %H:%M:%S")
    else:
        user_info['expiry_time'] = None
        expiry_display = "Vĩnh viễn"

    save_user_data()

    await send_telegram_message(chat_id,
                                f"✅ **Đã cấp key `{target_key}` cho chat ID:** `{target_chat_id}`\n"
                                f"Thời hạn: {expiry_display}")
    await send_telegram_message(target_chat_id,
                                f"🎉 **Chúc mừng!**\nBạn đã được Admin cấp key `{target_key}` để sử dụng Tool TX Pro AI.\n"
                                f"Key của bạn có hạn đến: {expiry_display}\n"
                                "Bot sẽ bắt đầu gửi dự đoán cho bạn. Sử dụng `/stop` để tạm dừng.")
    logger.info(f"Admin {chat_id} đã cấp key '{target_key}' cho {target_chat_id} với thời hạn {duration_hours}h.")


@bot.message_handler(commands=['adminkey'])
async def adminkey_command_handler(message):
    chat_id = message.chat.id
    if not is_admin(chat_id):
        await send_telegram_message(chat_id, "🚫 **Bạn không có quyền sử dụng lệnh này.**")
        return

    args = message.text.split(maxsplit=1)
    if len(args) < 2:
        await send_telegram_message(chat_id, "❌ **Sai cú pháp.**\nSử dụng: `/adminkey [tên_key]`")
        return

    target_key = args[1].strip().lower()
    user_info = get_user_info_by_key(target_key)

    if user_info:
        if user_info['is_admin']:
            await send_telegram_message(chat_id, f"⚠️ Key `{target_key}` đã là Admin.")
        else:
            user_info['is_admin'] = True
            user_info['expiry_time'] = None # Admin keys typically don't expire
            save_user_data()
            await send_telegram_message(chat_id, f"✅ **Đã cấp quyền Admin cho key:** `{target_key}`")
            logger.info(f"Admin {chat_id} đã cấp quyền admin cho key: {target_key}.")
            if user_info.get('current_chat_id'):
                await send_telegram_message(user_info['current_chat_id'], "🎉 **Bạn đã được cấp quyền Admin!**")
    else:
        await send_telegram_message(chat_id, f"⚠️ Key `{target_key}` không tồn tại.")

@bot.message_handler(commands=['unadminkey'])
async def unadminkey_command_handler(message):
    chat_id = message.chat.id
    if not is_admin(chat_id):
        await send_telegram_message(chat_id, "🚫 **Bạn không có quyền sử dụng lệnh này.**")
        return

    args = message.text.split(maxsplit=1)
    if len(args) < 2:
        await send_telegram_message(chat_id, "❌ **Sai cú pháp.**\nSử dụng: `/unadminkey [tên_key]`")
        return

    target_key = args[1].strip().lower()
    user_info = get_user_info_by_key(target_key)

    if user_info:
        if not user_info['is_admin']:
            await send_telegram_message(chat_id, f"⚠️ Key `{target_key}` không phải là Admin.")
        else:
            user_info['is_admin'] = False
            user_info['expiry_time'] = None # Reset expiry for normal user
            save_user_data()
            await send_telegram_message(chat_id, f"✅ **Đã hủy quyền Admin của key:** `{target_key}`")
            logger.info(f"Admin {chat_id} đã hủy quyền admin của key: {target_key}.")
            if user_info.get('current_chat_id'):
                await send_telegram_message(user_info['current_chat_id'], "⚠️ **Quyền Admin của bạn đã bị gỡ bỏ.**")
    else:
        await send_telegram_message(chat_id, f"⚠️ Key `{target_key}` không tồn tại.")

@bot.message_handler(commands=['statuskey'])
async def statuskey_command_handler(message):
    chat_id = message.chat.id
    if not is_admin(chat_id):
        await send_telegram_message(chat_id, "🚫 **Bạn không có quyền sử dụng lệnh này.**")
        return

    args = message.text.split(maxsplit=1)
    if len(args) < 2:
        await send_telegram_message(chat_id, "❌ **Sai cú pháp.**\nSử dụng: `/statuskey [tên_key]`")
        return

    target_key = args[1].strip().lower()
    user_info = get_user_info_by_key(target_key)

    if user_info:
        status = "🟢 Đang hoạt động" if user_info.get('is_receiving_predictions') else "🔴 Đang dừng"
        admin_status = "👑 Admin" if user_info.get('is_admin') else "👤 User"
        linked_chat_id = user_info.get('current_chat_id', 'N/A')
        assigned_ids = ', '.join(map(str, user_info.get('assigned_chat_ids', []))) if user_info.get('assigned_chat_ids') else 'N/A'
        created_at_str = user_info.get('created_at', 'N/A')
        created_at_display = datetime.fromisoformat(created_at_str).strftime("%d-%m-%Y %H:%M:%S") if created_at_str != 'N/A' else 'N/A'

        expiry_time_str = user_info.get('expiry_time')
        expiry_display = "Vĩnh viễn"
        if expiry_time_str:
            expiry_time = datetime.fromisoformat(expiry_time_str)
            if expiry_time < datetime.now():
                expiry_display = f"Đã hết hạn ({expiry_time.strftime('%d/%m %H:%M')})"
            else:
                remaining_time = expiry_time - datetime.now()
                days = remaining_time.days
                hours, remainder = divmod(remaining_time.seconds, 3600)
                minutes, seconds = divmod(remainder, 60)
                expiry_display = f"Còn {days}d {hours}h {minutes}m ({expiry_time.strftime('%d/%m %H:%M')})"

        response = (
            f"🔍 **Thông tin Key:** `{target_key}`\n"
            f"  - Loại: {admin_status}\n"
            f"  - Trạng thái: {status}\n"
            f"  - Chat ID đang dùng: `{linked_chat_id}`\n"
            f"  - Các Chat ID đã gán: `{assigned_ids}`\n"
            f"  - Thời gian tạo: {created_at_display}\n"
            f"  - Hạn dùng: {expiry_display}\n"
        )
        await send_telegram_message(chat_id, response)
        logger.info(f"Admin {chat_id} đã xem trạng thái key: {target_key}.")
    else:
        await send_telegram_message(chat_id, f"⚠️ Key `{target_key}` không tồn tại.")


@bot.message_handler(commands=['kick'])
async def kick_command_handler(message):
    chat_id = message.chat.id
    if not is_admin(chat_id):
        await send_telegram_message(chat_id, "🚫 **Bạn không có quyền sử dụng lệnh này.**")
        return

    args = message.text.split(maxsplit=1)
    if len(args) < 2:
        await send_telegram_message(chat_id, "❌ **Sai cú pháp.**\nSử dụng: `/kick [chat_id]`")
        return

    try:
        target_chat_id = int(args[1])
    except ValueError:
        await send_telegram_message(chat_id, "❌ **Chat ID không hợp lệ.**")
        return

    found_key = False
    for key_name, info in list(user_data.items()):
        if info.get('current_chat_id') == target_chat_id or target_chat_id in info.get('assigned_chat_ids', []):
            info['is_receiving_predictions'] = False
            if info.get('current_chat_id') == target_chat_id:
                info['current_chat_id'] = None
            if target_chat_id in info.get('assigned_chat_ids', []):
                info['assigned_chat_ids'].remove(target_chat_id)
            save_user_data()
            await send_telegram_message(chat_id, f"✅ **Đã gỡ key của chat ID:** `{target_chat_id}` (key: `{key_name}`).")
            await send_telegram_message(target_chat_id, "⚠️ **Thông báo:**\nKey của bạn đã bị Admin gỡ bỏ khỏi thiết bị này. Bạn sẽ không nhận được dự đoán nữa.")
            logger.info(f"Admin {chat_id} đã kick chat_id {target_chat_id} (key: {key_name}).")
            found_key = True
            break

    if not found_key:
        await send_telegram_message(chat_id, f"⚠️ Không tìm thấy key nào liên kết với chat ID: `{target_chat_id}`.")


@bot.message_handler(commands=['resetai'])
async def resetai_command_handler(message):
    chat_id = message.chat.id
    if not is_admin(chat_id):
        await send_telegram_message(chat_id, "🚫 **Bạn không có quyền sử dụng lệnh này.**")
        return

    global pattern_counter, dulieu_ai
    pattern_counter = {}
    dulieu_ai = {}
    save_json_data(pattern_counter, PATTERN_COUNT_FILE)
    save_json_data(dulieu_ai, DULIEU_AI_FILE)

    await send_telegram_message(chat_id, "✅ **Đã reset toàn bộ dữ liệu AI và các file lịch sử/dự đoán (trừ dữ liệu API).**")
    logger.info(f"Admin {chat_id} đã reset toàn bộ dữ liệu AI.")

@bot.message_handler(commands=['captime'])
async def captime_command_handler(message):
    chat_id = message.chat.id
    if not is_admin(chat_id):
        await send_telegram_message(chat_id, "🚫 **Bạn không có quyền sử dụng lệnh này.**")
        return

    args = message.text.split()
    if len(args) < 3:
        await send_telegram_message(chat_id, "❌ **Sai cú pháp.**\nSử dụng: `/captime [tên_key] [thời_gian_giờ]`\nVí dụ: `/captime testkey 24` (gia hạn thêm 24 giờ).")
        return

    target_key = args[1].strip().lower()
    try:
        add_hours = int(args[2])
    except ValueError:
        await send_telegram_message(chat_id, "❌ **Thời gian gia hạn phải là số giờ nguyên.**")
        return

    user_info = get_user_info_by_key(target_key)
    if not user_info:
        await send_telegram_message(chat_id, f"⚠️ Key `{target_key}` không tồn tại.")
        return

    if user_info.get('is_admin'):
        await send_telegram_message(chat_id, f"⚠️ Key Admin `{target_key}` không có thời hạn, không cần gia hạn.")
        return

    current_expiry_time_str = user_info.get('expiry_time')
    if current_expiry_time_str:
        current_expiry_time = datetime.fromisoformat(current_expiry_time_str)
        if current_expiry_time < datetime.now():
            new_expiry_time = datetime.now() + timedelta(hours=add_hours)
        else:
            new_expiry_time = current_expiry_time + timedelta(hours=add_hours)
    else:
        new_expiry_time = datetime.now() + timedelta(hours=add_hours)

    user_info['expiry_time'] = new_expiry_time.isoformat()
    save_user_data()

    await send_telegram_message(chat_id,
                                f"✅ **Đã gia hạn key `{target_key}` thêm {add_hours} giờ.**\n"
                                f"Thời hạn mới: {new_expiry_time.strftime('%d-%m-%Y %H:%M:%S')}")
    if user_info.get('current_chat_id'):
        await send_telegram_message(user_info['current_chat_id'],
                                    f"🎉 **Key của bạn đã được gia hạn thêm {add_hours} giờ!**\n"
                                    f"Thời hạn mới: {new_expiry_time.strftime('%d-%m-%Y %H:%M:%S')}")
    logger.info(f"Admin {chat_id} đã gia hạn key '{target_key}' thêm {add_hours} giờ.")

# ==============================================================================
# 6. CÁC HÀM XỬ LÝ KHÁC
# ==============================================================================

@bot.message_handler(func=lambda message: True)
async def echo_all(message):
    chat_id = message.chat.id
    key_name, user_info = get_user_info_by_chat_id(chat_id)

    if user_info:
        if not user_info.get('is_admin'):
            await send_telegram_message(chat_id, "Tôi chỉ hiểu các lệnh bắt đầu bằng `/`. Sử dụng `/help` để xem danh sách lệnh.")
            logger.info(f"Người dùng {chat_id} (key: {key_name}) gửi tin nhắn không phải lệnh: '{message.text}'")
        else:
            logger.info(f"Admin {chat_id} (key: {key_name}) gửi tin nhắn không phải lệnh: '{message.text}' (không phản hồi lại)")
    else:
        await send_telegram_message(chat_id, "Bạn cần nhập key để sử dụng bot. Vui lòng nhập `/key [tên_key_của_bạn]` hoặc `/help` để biết thêm.")
        logger.info(f"Người dùng chưa xác thực {chat_id} gửi tin nhắn: '{message.text}'")


# ==============================================================================
# 7. CHẠY BOT VÀ SERVER FLASK (CHO RENDER) HOẶC POLLING (CHO LOCAL/ISH)
# ==============================================================================

app = Flask(__name__)

@app.route('/')
def index():
    return "Bot Telegram đang chạy!", 200

@app.route(f'/{TELEGRAM_BOT_TOKEN}', methods=['POST'])
def webhook():
    if request.headers.get('content-type') == 'application/json':
        json_string = request.get_data().decode('utf-8')
        update = telebot.types.Update.de_json(json_string)
        try:
            asyncio.run_coroutine_threadsafe(bot.process_new_updates([update]), loop)
        except Exception as e:
            logger.error(f"Lỗi khi xử lý webhook update: {e}", exc_info=True)
        return '!', 200
    else:
        abort(403)

async def start_polling():
    """Khởi động polling của telebot trong một vòng lặp sự kiện riêng."""
    logger.info("Bắt đầu polling Telegram...")
    try:
        bot.remove_webhook()
        logger.info("Đã xóa webhook cũ (nếu có).")
    except Exception as e:
        logger.warning(f"Không thể xóa webhook cũ (có thể không tồn tại): {e}")

    while True:
        try:
            bot.polling(non_stop=True, interval=0, timeout=20)
        except telebot.apihelper.ApiTelegramException as e:
            logger.error(f"Lỗi polling Telegram API: {e}", exc_info=True)
            if "Forbidden: bot was blocked by the user" in str(e):
                logger.critical("Bot bị chặn bởi người dùng hoặc token không hợp lệ. Vui lòng kiểm tra token.")
                # Có thể thoát hoặc ngủ lâu hơn nếu bot bị chặn liên tục
            elif "Too Many Requests" in str(e):
                logger.warning("Đạt giới hạn Rate Limit khi polling. Thử lại sau 5 giây.")
                await asyncio.sleep(5)
            else:
                logger.warning(f"Lỗi API không xác định khi polling: {e}. Thử lại sau 5 giây.")
                await asyncio.sleep(5)
        except Exception as e:
            logger.error(f"Lỗi không xác định khi polling Telegram: {e}", exc_info=True)
            logger.warning("Lỗi polling, thử lại sau 5 giây.")
            await asyncio.sleep(5)
        await asyncio.sleep(1)

async def periodic_tasks():
    """Chạy các tác vụ định kỳ như gửi dự đoán."""
    while True:
        try:
            await check_and_send_predictions()
        except Exception as e:
            logger.error(f"Lỗi trong tác vụ định kỳ check_and_send_predictions: {e}", exc_info=True)
        await asyncio.sleep(5) # Kiểm tra API mỗi 5 giây

def run_flask_app():
    """Chạy ứng dụng Flask."""
    port = int(os.getenv("PORT", 5000))
    # Sử dụng host='0.0.0.0' để lắng nghe tất cả các giao diện mạng
    app.run(host='0.0.0.0', port=port, debug=False)


async def main():
    logger.info("=== TOOL TX PRO AI V3 (CHỦ ĐỘNG) ===")

    global user_data, dudoan_patterns, last_processed_phien
    
    # Load user_data
    user_data = load_json_data(USER_DATA_FILE, {})

    # Load dudoan_patterns từ file text khi khởi động bot
    dudoan_patterns = load_text_patterns(DUDOAN_PATTERNS_FILE) # Đã đổi hàm

    # Thêm một key admin mặc định nếu user_data rỗng, để có thể đăng nhập lần đầu
    if not user_data:
        user_data['quangdz'] = {
            'is_admin': True,
            'is_receiving_predictions': False,
            'current_chat_id': None,
            'assigned_chat_ids': [],
            'created_at': datetime.now().isoformat(),
            'expiry_time': None
        }
        logger.info("Dữ liệu user_data được khởi tạo với key 'quangdz' (không bền vững).")
        save_user_data() # Lưu lại để tránh tạo lại mỗi lần khởi động nếu không có disk

    # Lấy phiên hiện tại ban đầu để tránh gửi dự đoán cho phiên cũ khi khởi động bot
    initial_game_data = await fetch_game_data()
    if initial_game_data and 'phien_hien_tai' in initial_game_data:
        last_processed_phien = initial_game_data['phien_hien_tai']
        logger.info(f"Đã thiết lập phiên ban đầu đã xử lý: {last_processed_phien}")
    else:
        logger.warning("Không thể lấy phiên ban đầu từ API. Có thể dự đoán đầu tiên sẽ bị bỏ lỡ.")


    IS_RENDER_ENV = os.getenv("RENDER") == "true" or os.getenv("PORT") is not None

    if IS_RENDER_ENV:
        logger.info("Phát hiện môi trường Render. Bắt đầu chế độ Webhook.")
        flask_thread = threading.Thread(target=run_flask_app, daemon=True)
        flask_thread.start()
        logger.info("Flask server thread đã khởi chạy.")

        webhook_url = os.getenv("RENDER_EXTERNAL_HOSTNAME")
        if webhook_url:
            full_webhook_url = f"https://{webhook_url}/{TELEGRAM_BOT_TOKEN}"
            try:
                bot.set_webhook(url=full_webhook_url, drop_pending_updates=True)
                logger.info(f"Webhook đã được đặt thành công: {full_webhook_url}")
                webhook_info = bot.get_webhook_info()
                logger.info(f"Thông tin Webhook hiện tại: URL={webhook_info.url}, Pending Updates={webhook_info.pending_update_count}")
            except Exception as e:
                logger.critical(f"LỖI NGHIÊM TRỌNG khi đặt webhook: {e}", exc_info=True)
        else:
            logger.critical("LỖI: Không tìm thấy biến môi trường RENDER_EXTERNAL_HOSTNAME. Không thể đặt webhook.")

        asyncio.create_task(periodic_tasks()) # Chạy periodic_tasks độc lập
        while True:
            await asyncio.sleep(3600) # Giữ cho main loop chạy vô thời hạn

    else:
        logger.info("Phát hiện môi trường cục bộ. Bắt đầu chế độ Polling.")
        asyncio.create_task(start_polling())
        asyncio.create_task(periodic_tasks()) # Chạy periodic_tasks độc lập
        while True:
            await asyncio.sleep(3600) # Giữ cho main loop chạy vô thời hạn

if __name__ == "__main__":
    try:
        loop = asyncio.get_event_loop()
    except RuntimeError:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)

    loop.run_until_complete(main())
