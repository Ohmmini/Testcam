from flask import Flask, Response, render_template
from pyngrok import ngrok
import cv2

app = Flask(__name__)

# สร้าง tunnel
public_url = ngrok.connect(5000)
print("📡 Ngrok URL:", public_url)

# กล้องในวง LAN เท่านั้น
camera = cv2.VideoCapture("rtsp://admin:a0888150287@192.168.1.137:554/cam/realmonitor?channel=1&subtype=1", cv2.CAP_FFMPEG)

def generate_frames():
    while True:
        success, frame = camera.read()
        if not success:
            break
        else:
            ret, buffer = cv2.imencode('.jpg', frame)
            frame = buffer.tobytes()
            yield (b'--frame\r\nContent-Type: image/jpeg\r\n\r\n' + frame + b'\r\n')

@app.route('/video')
def video():
    return Response(generate_frames(), mimetype='multipart/x-mixed-replace; boundary=frame')

if __name__ == '__main__':
    app.run()
