import os
from dotenv import load_dotenv
import cv2
from flask import Flask, Response, render_template_string
from pyngrok import ngrok

load_dotenv()

app = Flask(__name__)

# อ่านค่าจาก Environment Variable
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

@app.route('/')
def index():
    return render_template_string("""
        <html>
        <head><title>Camera Feed</title></head>
        <body>
            <h1>Camera Feed</h1>
            <img src="/video" width="640" />
        </body>
        </html>
    """)

@app.route('/video')
def video():
    return Response(generate_frames(),
                    mimetype='multipart/x-mixed-replace; boundary=frame')

if __name__ == '__main__':
    # ไม่ต้อง set_auth_token หรือ ngrok.connect()
    app.run(host='0.0.0.0', port=5000)