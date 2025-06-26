import os
from dotenv import load_dotenv
import cv2
from flask import Flask, Response, render_template_string, redirect, url_for
from pyngrok import ngrok

load_dotenv()

app = Flask(__name__)

# อ่านค่าจาก .env
rtsp_url = os.getenv("RTSP_URL")

def generate_frames():
    cap = cv2.VideoCapture(rtsp_url)
    while True:
        success, frame = cap.read()
        if not success:
            print("⚠️ ไม่สามารถอ่านเฟรมจากกล้อง RTSP ได้")
            break
        else:
            _, buffer = cv2.imencode('.jpg', frame)
            frame = buffer.tobytes()
            yield (b'--frame\r\n'
                   b'Content-Type: image/jpeg\r\n\r\n' + frame + b'\r\n')

# 🔹 หน้าแรก: มีปุ่มเข้าไปดูกล้อง
@app.route('/')
def index():
    return render_template_string("""
        <!DOCTYPE html>
        <html lang="en">
        <head>
            <meta charset="UTF-8" />
            <meta name="viewport" content="width=device-width, initial-scale=1.0" />
            <title>เข้าสู่ระบบกล้องวงจรปิด</title>
            <script src="https://cdn.tailwindcss.com"></script>
        </head>
        <body class="bg-gray-900 text-white flex items-center justify-center min-h-screen">
            <div class="text-center">
                <h1 class="text-3xl font-bold mb-6">🎥 เข้าสู่ระบบกล้องวงจรปิด</h1>
                <button 
                    onclick="window.location.href='/camera'"
                    class="bg-blue-500 hover:bg-blue-600 text-white font-semibold py-3 px-6 rounded-xl shadow-lg transition duration-200"
                >
                    คลิกเพื่อเข้าชมกล้อง
                </button>
            </div>
        </body>
        </html>
    """)

# 🔹 หน้าแสดงกล้อง
@app.route('/camera')
def camera():
    return render_template_string("""
        <!DOCTYPE html>
        <html lang="en">
        <head>
            <meta charset="UTF-8" />
            <meta name="viewport" content="width=device-width, initial-scale=1.0" />
            <title>CCTV Live</title>
            <script src="https://cdn.tailwindcss.com"></script>
        </head>
        <body class="bg-gray-900 text-white flex items-center justify-center min-h-screen">
            <div class="text-center">
                <h1 class="text-3xl font-bold mb-6">📹 CCTV Live</h1>
                <div class="border-4 border-blue-500 rounded-xl overflow-hidden shadow-lg inline-block">
                    <img src="/video" alt="Live Stream" class="w-[640px] h-[480px] object-cover" />
                </div>
            </div>
        </body>
        </html>
    """)

# 🔹 สตรีมวิดีโอ
@app.route('/video')
def video():
    return Response(generate_frames(),
                    mimetype='multipart/x-mixed-replace; boundary=frame')

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000)
