#!/bin/bash
chmod +x ./cloudflared
./cloudflared tunnel --config config.yml run
