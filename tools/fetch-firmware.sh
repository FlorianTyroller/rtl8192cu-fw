#!/bin/sh
# Copy the Realtek rtl8192cu firmware blobs into ./blobs/ (never committed).
# Sources, in order: local /lib/firmware, then a remote host via $FW_HOST.
#   FW_HOST=user@host ./tools/fetch-firmware.sh
set -e
cd "$(dirname "$0")/.."
mkdir -p blobs
if [ -f /lib/firmware/rtlwifi/rtl8192cufw_TMSC.bin ]; then
    cp -v /lib/firmware/rtlwifi/rtl8192cufw*.bin blobs/
elif [ -d /lib/firmware/rtlwifi ]; then
    echo "rtlwifi dir exists but no rtl8192cufw* blobs here" >&2
    exit 1
elif [ -n "$FW_HOST" ]; then
    scp "$FW_HOST:/lib/firmware/rtlwifi/rtl8192cufw*" blobs/
else
    echo "no local linux-firmware copy; set FW_HOST=user@host to fetch remotely" >&2
    exit 1
fi
ls -l blobs/
echo "next: tail -c +33 blobs/rtl8192cufw_TMSC.bin > blobs/tmsc.img"
