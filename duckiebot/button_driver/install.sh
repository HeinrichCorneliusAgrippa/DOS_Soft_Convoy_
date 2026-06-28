#!/bin/bash
#
# DuckieBot Button Driver - Auto Installation Script
# Installs button driver with auto-start on boot
#
# Usage: ./install.sh
#

set -e  # Exit on error

echo "=========================================="
echo "DuckieBot Button Driver Installation"
echo "=========================================="
echo ""

# Check if running on DuckieBot
if [ ! -f "/proc/device-tree/model" ]; then
    echo "Warning: Not running on Jetson Nano, but continuing..."
fi

# Get script directory
SCRIPT_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"
PROJECT_ROOT="$( cd "$SCRIPT_DIR/../../.." && pwd )"

echo "Installation directory: $SCRIPT_DIR"
echo "Project root: $PROJECT_ROOT"
echo ""

# Step 1: Install Python dependencies
echo "[1/5] Installing Python dependencies..."
echo "  - Installing dataclasses..."
sudo pip3 install dataclasses >/dev/null 2>&1 || echo "    (already installed)"

echo "  - Installing pyserial..."
sudo pip3 install pyserial >/dev/null 2>&1 || echo "    (already installed)"

# Step 2: Copy modules to system Python
echo "[2/5] Copying modules to system Python location..."

# Copy dataclasses
if [ -f "$HOME/.local/lib/python3.6/site-packages/dataclasses.py" ]; then
    echo "  - Copying dataclasses..."
    sudo cp "$HOME/.local/lib/python3.6/site-packages/dataclasses.py" /usr/local/lib/python3.6/dist-packages/
else
    echo "  - Warning: dataclasses not found in user directory"
fi

# Copy pyserial
if [ -d "$HOME/.local/lib/python3.6/site-packages/serial" ]; then
    echo "  - Copying pyserial..."
    sudo cp -r "$HOME/.local/lib/python3.6/site-packages/serial" /usr/local/lib/python3.6/dist-packages/
    sudo cp -r "$HOME/.local/lib/python3.6/site-packages/pyserial"-*.dist-info /usr/local/lib/python3.6/dist-packages/ 2>/dev/null || true
else
    echo "  - Warning: pyserial not found in user directory"
fi

# Step 3: Verify dependencies
echo "[3/5] Verifying dependencies..."
if sudo python3 -c "import dataclasses; import serial" 2>/dev/null; then
    echo "  ✓ All dependencies accessible by root"
else
    echo "  ✗ Error: Dependencies not accessible"
    exit 1
fi

# Step 4: Install systemd service
echo "[4/5] Installing systemd service..."

# Update WorkingDirectory in service file to actual project root
TEMP_SERVICE="/tmp/button-driver.service.tmp"
sed "s|WorkingDirectory=.*|WorkingDirectory=$PROJECT_ROOT|g" "$SCRIPT_DIR/button-driver.service" > "$TEMP_SERVICE"
sed -i "s|ExecStart=/usr/bin/python3.*|ExecStart=/usr/bin/python3 -u $SCRIPT_DIR/button_driver.py|g" "$TEMP_SERVICE"

sudo cp "$TEMP_SERVICE" /etc/systemd/system/button-driver.service
rm "$TEMP_SERVICE"

echo "  - Reloading systemd..."
sudo systemctl daemon-reload

echo "  - Enabling service..."
sudo systemctl enable button-driver.service >/dev/null 2>&1

echo "  - Starting service..."
sudo systemctl start button-driver.service

# Step 5: Verify installation
echo "[5/5] Verifying installation..."
sleep 2

if sudo systemctl is-active --quiet button-driver.service; then
    echo "  ✓ Button driver service is running"
else
    echo "  ✗ Service failed to start"
    echo ""
    echo "Check logs with: sudo journalctl -u button-driver.service -n 50"
    exit 1
fi

# Remove any old cron entries
echo ""
echo "Cleaning up old cron entries..."
(sudo crontab -l 2>/dev/null | grep -v "start-button-driver.sh" | sudo crontab -) 2>/dev/null || true

echo ""
echo "=========================================="
echo "Installation Complete!"
echo "=========================================="
echo ""
echo "The button driver is now running and will auto-start on boot."
echo ""
echo "Useful commands:"
echo "  - Check status:  sudo systemctl status button-driver.service"
echo "  - View logs:     sudo journalctl -u button-driver.service -f"
echo "  - Restart:       sudo systemctl restart button-driver.service"
echo "  - Stop:          sudo systemctl stop button-driver.service"
echo ""
echo "Test the button:"
echo "  - Quick press (< 3 sec): No action"
echo "  - Long press (3+ sec):   Shutdown bot + battery"
echo ""
