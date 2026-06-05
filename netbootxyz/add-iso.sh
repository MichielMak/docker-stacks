#!/bin/bash
# Registers a locally-hosted ISO with the netbootxyz PXE boot server so it
# appears in the boot menu on the local network. Useful for booting machines
# from large ISOs (OS installers, live images) without physical media.
#
# Prerequisites: the ISO must already be placed in the netbootxyz assets
# directory so it is served at http://192.168.178.4:8080/<iso-path>.
#
# The script does two things:
#   1. Creates an iPXE chainload script in the container's local/ menu dir
#   2. Injects a matching menu entry into menu.ipxe so it shows up at boot
#
# Usage: ./add-iso.sh "Display Name" "relative/path/to/file.iso"
# Example: ./add-iso.sh "Bazzite" "bazzite/bazzite-gnome-stable-live-amd64.iso"

set -euo pipefail

DISPLAY_NAME="${1:-}"
ISO_PATH="${2:-}"

if [[ -z "$DISPLAY_NAME" || -z "$ISO_PATH" ]]; then
    echo "Usage: $0 <display-name> <iso-path>"
    echo "Example: $0 \"Bazzite\" \"bazzite/bazzite-gnome-stable-live-amd64.iso\""
    exit 1
fi

SLUG=$(echo "$DISPLAY_NAME" | tr '[:upper:]' '[:lower:]' | tr ' ' '-' | tr -cd '[:alnum:]-')
SERVER="michiel@192.168.178.4"
HTTP_BASE="http://192.168.178.4:8080"
CONTAINER="netbootxyz"

echo "→ Adding '$DISPLAY_NAME' (slug: $SLUG)"
echo "  ISO: $HTTP_BASE/$ISO_PATH"

# Create the iPXE boot script in the local/ directory
ssh "$SERVER" "docker exec $CONTAINER sh -c 'printf \"#!ipxe\nsanboot $HTTP_BASE/$ISO_PATH\n\" > /config/menus/local/$DISPLAY_NAME'"
echo "✓ Created /config/menus/local/$DISPLAY_NAME"

# Edit menu.ipxe via Python inside the container for reliable substitution
ssh "$SERVER" "docker exec $CONTAINER python3 -c \"
import sys

slug = '$SLUG'
display_name = '$DISPLAY_NAME'

with open('/config/menus/menu.ipxe', 'r') as f:
    content = f.read()

item_line = 'item ' + slug + ' \${space} ' + display_name
handler = ':' + slug + '\nchain tftp://\${next-server}/local/' + display_name + ' || goto error\ngoto main_menu\n'

if item_line in content:
    print('Menu entry already exists, skipping.')
    sys.exit(0)

content = content.replace('item --gap Tools:', item_line + '\nitem --gap Tools:')
content = content.replace(':shell\n', handler + '\n:shell\n')

with open('/config/menus/menu.ipxe', 'w') as f:
    f.write(content)

print('Menu updated.')
\""
echo "✓ Menu entry added"

echo ""
echo "Done! '$DISPLAY_NAME' will appear under Local Assets in the netbootxyz menu."
