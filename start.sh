#!/bin/bash

# ดาวน์โหลด cloudflared
wget -O cloudflared https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-linux-amd64

chmod +x cloudflared

# สร้าง config.yml โดยใช้ path ของ secret file ที่ถูก mount
echo "tunnel: $TUNNEL_ID
credentials-file: /etc/secrets/tunnel.json
ingress:
  - hostname: $HOSTNAME
    service: http://testcam.onrender.com
  - service: http_status:404" > config.yml

# รัน tunnel
./cloudflared tunnel --config config.yml run
