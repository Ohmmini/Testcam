#!/bin/bash

# ✅ Start Flask app in background
python3 test.py &

# ✅ Wait a few seconds to make sure Flask is up
sleep 5

# ✅ Download and run cloudflared
wget -O cloudflared https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-linux-amd64
chmod +x cloudflared

echo "tunnel: $TUNNEL_ID
credentials-file: /etc/secrets/tunnel.json
ingress:
  - hostname: $HOSTNAME
    service: http://localhost:5000
  - service: http_status:404" > config.yml

./cloudflared tunnel --config config.yml run
