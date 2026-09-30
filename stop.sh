#!/bin/bash

echo "========================================"
echo "  KnowledgeDiver - Stopping Services"
echo "========================================"

if systemctl is-active --quiet knowledgediver; then
    echo "Stopping backend service..."
    systemctl stop knowledgediver
    echo "Backend stopped."
else
    echo "Backend service not running."
fi

echo
echo "Done."
