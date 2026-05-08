# MeshSentinel BBS

BBS (Bulletin Board System) de texto diseñado para correr sobre la red Meshtastic, con módulos de alertas meteorológicas SMN, estadísticas de red, mensajería, y una app web para visualización de nodos y trayectorias GPS.

---

## Hardware requerido

- Raspberry Pi 3 B+ (o superior)
- Dispositivo Meshtastic conectado por USB serial (probado con Heltec T114)

---

## Software base

- Debian GNU/Linux 13 (trixie)
- Python 3.11+

---

## Instalación

### 1. Actualizar el sistema

```bash
sudo apt update && sudo apt upgrade -y
```

### 2. Instalar dependencias del sistema

```bash
sudo apt install -y python3 python3-pip python3-venv git
```

### 3. Crear el directorio del proyecto

```bash
mkdir -p /home/daniel/bbs
cd /home/daniel/bbs
```

### 4. Clonar el repositorio

```bash
git clone https://github.com/danielbenitezbahia/meshsentinel.git
cd meshsentinel
```

### 5. Crear el entorno virtual e instalar dependencias Python

```bash
python3 -m venv /home/daniel/meshtastic/venv
/home/daniel/meshtastic/venv/bin/pip install --upgrade pip
/home/daniel/meshtastic/venv/bin/pip install meshtastic==2.7.7 flask==3.1.3 flask-cors==6.0.2
```

### 6. Verificar que el dispositivo Meshtastic es detectado

Conectar el Heltec T114 por USB y verificar:

```bash
ls /dev/ttyACM*
```

Debe aparecer `/dev/ttyACM0` o similar.

---

## Configuración de servicios systemd

### Servicio BBS

Crear el archivo `/etc/systemd/system/meshboard.service`:

```ini
[Unit]
Description=meshsentinel BBS for Meshtastic
After=network.target

[Service]
User=daniel
WorkingDirectory=/home/daniel/bbs/meshsentinel
Environment="PATH=/home/daniel/meshtastic/venv/bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"
ExecStart=/home/daniel/meshtastic/venv/bin/python /home/daniel/bbs/meshsentinel/bbs_system.py
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
```

### Servicio API REST

El archivo `meshsentinel-api.service` ya está incluido en el repositorio. Instalarlo:

```bash
sudo cp /home/daniel/bbs/meshsentinel/meshsentinel-api.service /etc/systemd/system/
```

### Activar y arrancar ambos servicios

```bash
sudo systemctl daemon-reload
sudo systemctl enable meshboard meshsentinel-api
sudo systemctl start meshboard meshsentinel-api
```

### Verificar estado

```bash
sudo systemctl status meshboard --no-pager
sudo systemctl status meshsentinel-api --no-pager
```

---

## App web

La app web (React + TypeScript) se sirve desde la API REST en el puerto 8080.

### Compilar el frontend (desde la Mac o cualquier máquina con Node.js)

```bash
cd frontend
npm install
npm run build
```

Copiar el build a la Pi:

```bash
scp -r dist daniel@<IP_DE_LA_PI>:/home/daniel/bbs/meshsentinel/frontend/
```

### Acceder a la app

Desde la red local:
```
http://<IP_DE_LA_PI>:8080
```

---

## Acceso remoto (Cloudflare Tunnel)

Para exponer la app a internet sin dominio propio:

```bash
cloudflared tunnel --url http://localhost:8080
```

Esto genera una URL pública temporal en `trycloudflare.com`. Para una URL fija se requiere cuenta en Cloudflare Zero Trust con un dominio registrado.

---

## Estructura del proyecto

```
meshsentinel/
├── bbs_system.py              # Sistema BBS principal
├── interface.py               # Interfaz con el dispositivo Meshtastic
├── api.py                     # API REST (Flask)
├── traffic_stats.py           # Estadísticas de tráfico de red
├── weather_alert_service.py   # Servicio de alertas SMN
├── weather_alert_scraper.py   # Scraper de alertas SMN
├── weather_alert_notifier.py  # Notificador de alertas por nodo
├── short_term_alert_scraper.py# Alertas de muy corto plazo
├── store_forward.py           # Store & Forward de mensajes
├── bbs_users.py               # Gestión de usuarios
├── bbs_messages.py            # Gestión de mensajes
├── meshsentinel-api.service   # Archivo de servicio systemd para la API
├── modules/
│   ├── nodos.py               # Módulo: nodos conectados y trayectorias
│   ├── Estadisticas/          # Módulo: estadísticas de red y tráfico
│   ├── Mensajes/              # Módulo: mensajería BBS
│   ├── Mail/                  # Módulo: correo entre nodos
│   └── Juegos/                # Módulo: juegos
├── frontend/
│   ├── src/                   # Código fuente React
│   └── dist/                  # Build compilado (no incluido en el repo)
└── partidos.geojson           # Datos geográficos para alertas SMN
```

---

## Variables y configuración relevante

| Archivo | Variable | Descripción |
|---|---|---|
| `bbs_system.py` | `SMN_BROADCAST_HOURS` | Horas de envío del reporte SMN (11 y 18) |
| `bbs_system.py` | `NEW_NODE_NOTIFY_NODES` | Nodos que reciben aviso de nodo nuevo |
| `bbs_system.py` | `SMN_ALERTS_CHANNEL_INDEX` | Índice del canal primario de alertas |
| `bbs_system.py` | `SMN_ALERTS_CHANNEL_INDEX_2` | Índice del canal secundario de alertas |
| `api.py` | `DB_PATH` | Path a la base de datos de tráfico |
| `api.py` | `FRONTEND_DIST` | Path al build del frontend |
