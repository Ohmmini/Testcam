#!/bin/bash

# ✅ ดาวน์โหลด cloudflared
wget -O cloudflared https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-linux-amd64

# ✅ เปลี่ยน permission
chmod +x cloudflared

# ✅ สร้าง config.yml แบบใช้ environment variables
echo "tunnel: $TUNNEL_ID
credentials-file: $CREDENTIALS_FILE
ingress:
  - hostname: $HOSTNAME
    service: kwangdataisyourspace.space
  - service: http_status:404" > config.yml

# ✅ รัน tunnel
./cloudflared tunnel --config config.yml run
