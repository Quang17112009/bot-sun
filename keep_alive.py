# keep_alive.py
from http.server import BaseHTTPRequestHandler, HTTPServer
from threading import Thread
import os

class MyHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.send_header('Content-type', 'text/html')
        self.end_headers()
        self.wfile.write(b"Bot is alive!")

def run_server():
    # Render thường sử dụng biến môi trường PORT để chỉ định cổng
    # Nếu không có, mặc định dùng 8080 hoặc 80
    port = int(os.environ.get("PORT", 8080)) # Lấy cổng từ biến môi trường PORT của Render, mặc định 8080
    server_address = ('0.0.0.0', port)
    httpd = HTTPServer(server_address, MyHandler)
    print(f"Starting keep_alive server on port {port}...")
    httpd.serve_forever()

def keep_alive():
    # Chạy máy chủ HTTP trong một luồng riêng biệt
    thread = Thread(target=run_server)
    thread.daemon = True # Đặt luồng là daemon để nó tự kết thúc khi chương trình chính thoát
    thread.start()
