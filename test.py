import os
from flask import Flask, Response, render_template
from pyngrok import ngrok, conf
from dotenv import load_dotenv
import cv2

# โหลด .env
load_dotenv()

# ดึงค่าจาก .env
auth_token = os.getenv("NGROK_AUTH_TOKEN")
rtsp_url = os.getenv("RTSP_URL")

# กำหนด token ให้ ngrok
conf.get_default().auth_token = auth_token

# สร้าง tunnel
public_url = ngrok.connect(5000)
print("📡 Ngrok URL:", public_url)

# Flask app
app = Flask(__name__)
camera = cv2.VideoCapture(rtsp_url, cv2.CAP_FFMPEG)

def generate_frames():
    while True:
        success, frame = camera.read()
        if not success:
            break
        ret, buffer = cv2.imencode('.jpg', frame)
        frame = buffer.tobytes()
        yield (b'--frame\r\nContent-Type: image/jpeg\r\n\r\n' + frame + b'\r\n')

@app.route('/')
def index():
    return render_template('index.html')

@app.route('/video')
def video():
    return Response(generate_frames(), mimetype='multipart/x-mixed-replace; boundary=frame')

if __name__ == '__main__':
    app.run()
