from flask import Flask, Response
import cv2
import os
from pyngrok import ngrok, conf

# โหลด NGROK_AUTH_TOKEN จาก environment
conf.get_default().auth_token = os.environ.get("NGROK_AUTH_TOKEN")

# โหลด RTSP_URL จาก environment
RTSP_URL = os.environ.get("RTSP_URL")

# สร้าง ngrok tunnel
public_url = ngrok.connect(5000)
print(f"📡 Ngrok URL: {public_url}")

# Flask app
app = Flask(__name__)

# สร้าง video capture จากกล้อง RTSP
cap = cv2.VideoCapture(RTSP_URL)

def generate_frames():
    while True:
        success, frame = cap.read()
        if not success:
            break
        _, buffer = cv2.imencode('.jpg', frame)
        frame_bytes = buffer.tobytes()
        yield (b'--frame\r\n'
               b'Content-Type: image/jpeg\r\n\r\n' + frame_bytes + b'\r\n')

@app.route('/')
def index():
    return "<h1>Camera Feed</h1><img src='/video'>"

@app.route('/video')
def video():
    return Response(generate_frames(),
                    mimetype='multipart/x-mixed-replace; boundary=frame')

if __name__ == '__main__':
    # ให้ Flask รันบน 0.0.0.0 เพื่อให้ Render ตรวจเจอ port
    app.run(host='0.0.0.0', port=5000)
