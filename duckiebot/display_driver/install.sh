set -e  # Exit on error

echo "=========================================="
echo "DuckieBot Display Driver Installation"
echo "=========================================="
echo ""

# Get script directory
SCRIPT_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"
PROJECT_ROOT="$( cd "$SCRIPT_DIR/../../.." && pwd )"

echo "Installation directory: $SCRIPT_DIR"
echo "Project root: $PROJECT_ROOT"
echo ""

# Step 1: Install Python dependencies
echo "[1/5] Installing Python dependencies..."

echo "  - Installing luma.oled..."
sudo pip3 install luma.oled >/dev/null 2>&1 || echo "    (already installed)"

echo "  - Installing Pillow..."
sudo pip3 install Pillow >/dev/null 2>&1 || echo "    (already installed)"

echo "  - Installing pyserial..."
sudo pip3 install pyserial >/dev/null 2>&1 || echo "    (already installed)"

# Step 2: Copy modules to system Python (for systemd access)
echo "[2/5] Copying modules to system Python location..."

# Copy pyserial if needed
if [ -d "$HOME/.local/lib/python3.6/site-packages/serial" ]; then
    echo "  - Copying pyserial..."
    sudo cp -r "$HOME/.local/lib/python3.6/site-packages/serial" /usr/local/lib/python3.6/dist-packages/ 2>/dev/null || true
fi

# Copy luma.oled
if [ -d "$HOME/.local/lib/python3.6/site-packages/luma" ]; then
    echo "  - Copying luma.oled..."
    sudo cp -r "$HOME/.local/lib/python3.6/site-packages/luma" /usr/local/lib/python3.6/dist-packages/ 2>/dev/null || true
    sudo cp -r "$HOME/.local/lib/python3.6/site-packages/luma"*.dist-info /usr/local/lib/python3.6/dist-packages/ 2>/dev/null || true
fi

# Copy PIL/Pillow
if [ -d "$HOME/.local/lib/python3.6/site-packages/PIL" ]; then
    echo "  - Copying Pillow..."
    sudo cp -r "$HOME/.local/lib/python3.6/site-packages/PIL" /usr/local/lib/python3.6/dist-packages/ 2>/dev/null || true
    sudo cp -r "$HOME/.local/lib/python3.6/site-packages/Pillow"*.dist-info /usr/local/lib/python3.6/dist-packages/ 2>/dev/null || true
fi

# Step 3: Verify dependencies
echo "[3/5] Verifying dependencies..."
if sudo python3 -c "from luma.oled.device import ssd1306; import serial" 2>/dev/null; then
    echo "  ✓ All dependencies accessible by root"
else
    echo "  ✗ Error: Dependencies not accessible"
    exit 1
fi

# Step 4: Check I2C device
echo "[4/5] Checking I2C device..."
if command -v i2cdetect &> /dev/null; then
    if sudo i2cdetect -y 1 | grep -q "3c"; then
        echo "  ✓ SSD1306 display found at 0x3C"
    else
        echo "  ! Warning: Display not detected at 0x3C"
        echo "    Display may not be connected"
    fi
else
    echo "  ! i2cdetect not found, skipping hardware check"
fi

# Step 5: Install systemd service
echo "[5/5] Installing systemd service..."

# Update WorkingDirectory in service file
TEMP_SERVICE="/tmp/display-driver.service.tmp"
sed "s|WorkingDirectory=.*|WorkingDirectory=$PROJECT_ROOT|g" "$SCRIPT_DIR/display-driver.service" > "$TEMP_SERVICE"
sed -i "s|ExecStart=/usr/bin/python3.*|ExecStart=/usr/bin/python3 -u $SCRIPT_DIR/display_driver.py|g" "$TEMP_SERVICE"

sudo cp "$TEMP_SERVICE" /etc/systemd/system/display-driver.service
rm "$TEMP_SERVICE"

echo "  - Reloading systemd..."
sudo systemctl daemon-reload

echo "  - Enabling service..."
sudo systemctl enable display-driver.service >/dev/null 2>&1

echo "  - Starting service..."
sudo systemctl start display-driver.service

# Verify installation
echo ""
echo "Verifying installation..."
sleep 3

if sudo systemctl is-active --quiet display-driver.service; then
    echo "  ✓ Display driver service is running"
else
    echo "  ✗ Service failed to start"
    echo ""
    echo "Check logs with: sudo journalctl -u display-driver.service -n 50"
    exit 1
fi

echo ""
echo "=========================================="
echo "Installation Complete!"
echo "=========================================="
echo ""
echo "The display driver is now running and will auto-start on boot."
echo ""
echo "Useful commands:"
echo "  - Check status:  sudo systemctl status display-driver.service"
echo "  - View logs:     sudo journalctl -u display-driver.service -f"
echo "  - Restart:       sudo systemctl restart display-driver.service"
echo "  - Stop:          sudo systemctl stop display-driver.service"
echo ""
echo "The display shows:"
echo "  - Battery percentage and status"
echo "  - IP address"
echo "  - Battery voltage and current"
echo "  - CPU temperature"
echo "  - Hostname"
echo ""
