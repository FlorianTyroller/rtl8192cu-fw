#!/bin/sh
# Copy the Realtek rtl8192cu firmware blobs into ./blobs/ (never committed).
# Sources, in order: local /lib/firmware, then a known Pi (doorpi).
set -e
cd "$(dirname "$0")/.."
mkdir -p blobs
if [ -f /lib/firmware/rtlwifi/rtl8192cufw_TMSC.bin ]; then
    cp -v /lib/firmware/rtlwifi/rtl8192cufw*.bin blobs/
elif [ -d /lib/firmware/rtlwifi ]; then
    echo "rtlwifi dir exists but no rtl8192cufw* blobs here" >&2
    exit 1
else
    # adjust to wherever a linux-firmware copy lives for you
    scp 'florian@192.168.2.117:/lib/firmware/rtlwifi/rtl8192cufw*' blobs/
fi
ls -l blobs/
echo "next: tail -c +33 blobs/rtl8192cufw_TMSC.bin > blobs/tmsc.img"
