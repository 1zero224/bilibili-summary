#!/bin/bash
# Build BiliSummary macOS app
# Usage: ./build_mac.sh

set -e

echo "🔧 Installing PyInstaller..."
pip install pyinstaller

echo "📦 Building BiliSummary.app..."
pyinstaller BiliSummary.spec --clean --noconfirm

echo ""
echo "✅ Build complete!"
echo "   App location: dist/BiliSummary.app"
echo ""
echo "To run:"
echo "   open dist/BiliSummary.app"
echo ""
echo "User data will be stored at:"
echo "   ~/Library/Application Support/BiliSummary/"
