#!/usr/bin/env python3
"""
diag_decrypt.py — diagnóstico de descifrado PKC para DMs de pilgrim.
Correr en la Pi: python3 diag_decrypt.py
Lee el listener.log y extrae el último paquete encriptado de pilgrim,
luego intenta descifrar con todas las claves conocidas.
"""
import re, struct, base64, hashlib, sys

LOG_PATH = "/home/daniel/bbs/meshsentinel/listener.log"

# ── AES block y CCM ──────────────────────────────────────────────────────────
try:
    from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
    from cryptography.hazmat.backends import default_backend
    from cryptography.hazmat.primitives.asymmetric.x25519 import (
        X25519PrivateKey, X25519PublicKey
    )
except ImportError:
    sys.exit("ERROR: instalar: pip install cryptography")

def aes_block(key, b):
    c = Cipher(algorithms.AES(key), modes.ECB(), backend=default_backend())
    return c.encryptor().update(bytes(b)) + c.encryptor().finalize()

def aes_ccm_dec(key, nonce13, ct, tag, M=8):
    L=2
    a=bytearray(16); a[0]=L-1; a[1:14]=nonce13; a[14]=0; a[15]=0
    S0=aes_block(key,a)
    T=bytes(x^y for x,y in zip(tag[:M],S0[:M]))
    pt=bytearray()
    for i in range(1,(len(ct)+15)//16+1):
        a[14]=(i>>8)&0xFF; a[15]=i&0xFF
        Si=aes_block(key,a)
        s=(i-1)*16; e=min(i*16,len(ct))
        pt.extend(x^y for x,y in zip(Si[:e-s],ct[s:e]))
    plaintext=bytes(pt)
    b=bytearray(16); b[0]=((M-2)//2)<<3|(L-1); b[1:14]=nonce13
    b[14]=(len(ct)>>8)&0xFF; b[15]=len(ct)&0xFF
    X=aes_block(key,b)
    for i in range(0,len(plaintext),16):
        blk=plaintext[i:i+16].ljust(16,b'\x00')
        X=aes_block(key,bytes(x^y for x,y in zip(X,blk)))
    return (plaintext if T[:M]==X[:M] else None), T, X[:M]

def aes_ccm_try(key, nonce13, ct, tag, label):
    pt, T, X = aes_ccm_dec(key, nonce13, ct, tag)
    if pt:
        print(f"  ✓ {label}: {pt.hex()} → {pt!r}")
        return True
    else:
        print(f"  ✗ {label}: T={T.hex()} X={X.hex()}")
        return False

def aes_ctr_dec(key, nonce16, ct):
    try:
        c = Cipher(algorithms.AES(key), modes.CTR(nonce16), backend=default_backend())
        return c.decryptor().update(ct)
    except:
        return None

def parse_proto(pt):
    """Intenta parsear como protobuf Data (portnum+payload)."""
    try:
        from meshtastic import mesh_pb2
        d = mesh_pb2.Data(); d.ParseFromString(pt)
        if d.portnum == 1:
            return d.payload.decode("utf-8", errors="replace")
    except:
        pass
    try:
        raw = pt.decode("utf-8")
        if all(0x20 <= ord(c) < 0x7F or c in '\n\r\t' for c in raw):
            return raw
    except:
        pass
    return None

# ── Claves BBS ───────────────────────────────────────────────────────────────
BBS_PRIV_HEX = "6846ae2df0c9fd9118bd542b9aef2459e3c7206cb215bb88cd4ddda7c4e22661"

# ── Leer log ─────────────────────────────────────────────────────────────────
try:
    with open(LOG_PATH) as f:
        lines = f.readlines()
except Exception as e:
    sys.exit(f"ERROR leyendo log: {e}")

# Buscar el último bloque de DM encriptado de pilgrim (!da4846ec)
PILGRIM = "!da4846ec"
enc_hex = None
nonce13_hex = None
ct_hex = None
tag_hex = None
dict_pub_raw = None
packet_id_from_log = None
from_num_from_log = None

# Parsear líneas en orden inverso para encontrar el último DM
for line in reversed(lines):
    # Extraer enc bytes del warning
    m = re.search(r"enc=([0-9a-f]+)", line)
    if m and PILGRIM in line and enc_hex is None:
        enc_hex = m.group(1)

    # Extraer nonce13, ct, tag del debug pki_ccm
    m = re.search(r"pki_ccm\[dict\].*nonce13=([0-9a-f]+)\s+ct=([0-9a-f]+)\s+tag=([0-9a-f]+)", line)
    if m and nonce13_hex is None:
        nonce13_hex = m.group(1)
        ct_hex = m.group(2)
        tag_hex = m.group(3)

    # Extraer dict_pub
    m = re.search(r"dict_pub=(.*)", line)
    if m and dict_pub_raw is None and PILGRIM in line:
        dict_pub_raw = m.group(1).strip()

    # Buscar PKTFIELD id o packet_id
    m = re.search(r"PKTFIELD: id.*val=(\d+)", line)
    if m and packet_id_from_log is None:
        packet_id_from_log = int(m.group(1))

    # Si tenemos todo lo que necesitamos, parar
    if enc_hex and nonce13_hex and ct_hex and tag_hex:
        break

print("="*70)
print("DATOS EXTRAÍDOS DEL LOG:")
print(f"  enc_hex    = {enc_hex}")
print(f"  nonce13    = {nonce13_hex}")
print(f"  ct         = {ct_hex}")
print(f"  tag        = {tag_hex}")
print(f"  dict_pub   = {dict_pub_raw}")
print(f"  packet_id  = {packet_id_from_log}")
print()

if not enc_hex:
    sys.exit("ERROR: no se encontró enc_hex en el log. ¿Pilgrim mandó un DM?")

enc = bytes.fromhex(enc_hex)
print(f"enc ({len(enc)} bytes): {enc.hex()}")

# Extraer campos del enc
if len(enc) < 13:
    sys.exit("ERROR: enc muy corto")
extra_nonce = struct.unpack("<I", enc[-4:])[0]
ct  = enc[:-12]
tag = enc[-12:-4]
print(f"  ct  = {ct.hex()} ({len(ct)} bytes)")
print(f"  tag = {tag.hex()}")
print(f"  extra_nonce = 0x{extra_nonce:08x}")

# Reconstruir nonce13 desde el log si está disponible, sino calcularlo
if nonce13_hex and len(nonce13_hex) == 26:
    nonce13 = bytes.fromhex(nonce13_hex)
    # Extraer packet_id y from_num del nonce13
    n16 = nonce13 + bytes(3)  # completar a 16 bytes
    packet_id_n = struct.unpack("<Q", nonce13[:8])[0]
    from_num_n  = struct.unpack("<I", nonce13[8:12])[0]
    print(f"  nonce13 = {nonce13.hex()} (del log)")
    print(f"  packet_id (nonce) = 0x{packet_id_n:016x}")
    print(f"  from_num (nonce)  = 0x{from_num_n:08x}")
else:
    print(f"  nonce13 del log: {nonce13_hex!r} (longitud incorrecta, usando packet_id asumido)")
    # Usar el valor hardcodeado
    packet_id_n = 0x8076C335
    from_num_n  = 0xDA4846EC
    nonce16 = struct.pack("<Q", packet_id_n) + struct.pack("<I", from_num_n) + struct.pack("<I", extra_nonce)
    nonce13 = nonce16[:13]
    print(f"  nonce13 calculado = {nonce13.hex()}")

print()

# ── Intentar descifrado ──────────────────────────────────────────────────────
bbs_priv = X25519PrivateKey.from_private_bytes(bytes.fromhex(BBS_PRIV_HEX))

# Candidatos de clave pública de pilgrim
pub_candidates = []

# 1. La clave "real" que teníamos de sesiones anteriores
known_real = base64.b64decode("q55qEsjVIAYK0GCPcs/YT/P2rlRA55WmcUB/WsMccAs=")
pub_candidates.append(("known_real", known_real))

# 2. La clave vieja (en el nodo dict)
known_old = base64.b64decode("WT5kBBEHIosT8MOBOTNe1sd3Nh9MxJpu04282WjutRQ=")
pub_candidates.append(("known_old", known_old))

# 3. La clave del dict_pub del log (si es diferente)
if dict_pub_raw:
    import ast
    try:
        # Si es "b'\xab...'" (Python bytes repr)
        parsed = ast.literal_eval(dict_pub_raw)
        if isinstance(parsed, bytes) and len(parsed) == 32:
            pub_candidates.append(("log_dict", parsed))
            print(f"dict_pub del log: {parsed.hex()}")
    except:
        try:
            # Si es base64
            dict_pub_bytes = base64.b64decode(dict_pub_raw + "==")
            if len(dict_pub_bytes) == 32:
                pub_candidates.append(("log_dict_b64", dict_pub_bytes))
                print(f"dict_pub del log (b64): {dict_pub_bytes.hex()}")
        except:
            print(f"dict_pub del log (no parseable): {dict_pub_raw!r}")

# ── También intentar leer desde el dispositivo Meshtastic ────────────────────
try:
    sys.path.insert(0, "/home/daniel/meshtastic/venv/lib/python3.11/site-packages")
    from meshtastic.serial_interface import SerialInterface
    print("Intentando leer clave de pilgrim del dispositivo...")
    iface = SerialInterface()
    nodes = getattr(iface, "nodes", {}) or {}
    pilgrim_info = nodes.get(PILGRIM, {})
    print(f"  pilgrim node info keys: {list(pilgrim_info.keys())}")
    user_info = pilgrim_info.get("user", {})
    print(f"  pilgrim user keys: {list(user_info.keys())}")
    pk = user_info.get("publicKey")
    if pk:
        pk_bytes = bytes(pk)
        print(f"  pilgrim publicKey (live): {pk_bytes.hex()} ({len(pk_bytes)} bytes)")
        if len(pk_bytes) == 32:
            pub_candidates.append(("live_device", pk_bytes))
    else:
        print(f"  pilgrim publicKey: None")
    # También leer la clave privada del BBS desde el dispositivo
    try:
        priv_live = bytes(iface.localNode.localConfig.security.private_key)
        print(f"  BBS priv (live): {priv_live.hex()}")
        if priv_live and len(priv_live) == 32 and priv_live.hex() != BBS_PRIV_HEX:
            print(f"  ⚠️  BBS priv DIFERENTE al hardcodeado!")
            bbs_priv = X25519PrivateKey.from_private_bytes(priv_live)
    except:
        pass
    iface.close()
except Exception as e:
    print(f"  No se pudo conectar al dispositivo: {e}")

print()
print("="*70)
print("INTENTOS DE DESCIFRADO PKC (AES-CCM):")
for label, pb in pub_candidates:
    try:
        shared = bbs_priv.exchange(X25519PublicKey.from_public_bytes(pb))
        hk = hashlib.sha256(shared).digest()
        print(f"  [{label}] pub={pb.hex()[:16]}... shared={shared.hex()[:16]}... hk={hk.hex()[:16]}...")
        success = aes_ccm_try(hk, nonce13, ct, tag, label)
        if success:
            break
    except Exception as e:
        print(f"  [{label}] ERROR: {e}")

print()
print("INTENTOS AES-CTR (canal PSK):")
DEFAULT_PSK = bytes.fromhex("d4f1bb3a20290759f0bcffabcf4e6901")
nonce_ctr = struct.pack("<Q", packet_id_n) + struct.pack("<I", from_num_n) + b'\x00\x00\x00\x00'
print(f"  nonce_ctr = {nonce_ctr.hex()}")
for label, key in [("default_psk", DEFAULT_PSK)]:
    pt_ctr = aes_ctr_dec(key, nonce_ctr, enc)
    if pt_ctr:
        text = parse_proto(pt_ctr)
        if text:
            print(f"  ✓ {label}: {text!r}")
        else:
            print(f"  - {label}: {pt_ctr.hex()} (no parseable)")
    else:
        print(f"  ✗ {label}: error")

# ── Dump completo de líneas del log relacionadas ─────────────────────────────
print()
print("="*70)
print("LÍNEAS DEL LOG (últimos DM encriptados de pilgrim):")
for line in lines:
    if PILGRIM in line or "pki_ccm" in line or "dict_pub" in line:
        print(" ", line.rstrip())
