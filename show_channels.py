from interface import Interface

iface = Interface()
iface.connect()

if not iface.interface:
    print("No se pudo conectar al nodo")
    raise SystemExit(1)

local_node = iface.interface.localNode

# A veces conviene pedir config/canales explícitamente si no llegaron todavía
try:
    local_node.requestChannels()
    local_node.waitForConfig("channels")
except Exception:
    pass

print("=== SHOW CHANNELS ===")
try:
    local_node.showChannels()
except Exception as e:
    print("showChannels() fallo:", e)

print("\n=== RAW CHANNELS ===")
channels = getattr(local_node, "channels", None) or []
for i, ch in enumerate(channels):
    try:
        role = ch.role
    except Exception:
        role = "?"
    try:
        name = ch.settings.name
    except Exception:
        name = ""
    print(f"index={i} role={role} name={name!r}")

iface.disconnect()