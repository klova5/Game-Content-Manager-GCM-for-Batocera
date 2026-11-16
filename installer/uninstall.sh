#!/bin/bash
# GCM v3.6 developer preview uninstaller. See README.md.
cd "$(dirname "$0")" && exec python3 -B payload/gcm_install.py uninstall "$@"
