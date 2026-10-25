"""Aviso general de la mesh: un mensaje que reemplaza la frase random del
login mientras está vigente. Solo lo pueden fijar los nodos de confianza
(pilgrim, Nueva Belgrano, Villa Churrinche). Persiste en un JSON chico
para sobrevivir un restart del servicio.
"""
import json
import os
from datetime import date, datetime
from typing import Optional

import store_forward

FILE_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "community_announcement.json")

TRUSTED_NODE_IDS = {
    store_forward.normalize_node_id("!da4846ec"),  # pilgrim
    store_forward.normalize_node_id("!33686e48"),  # Nueva Belgrano
    store_forward.normalize_node_id("!b215cff4"),  # Villa Churrinche
}


def is_trusted(node_id: str) -> bool:
    return store_forward.normalize_node_id(node_id) in TRUSTED_NODE_IDS


def set_announcement(text: str, expires: str, set_by: str) -> None:
    """expires: fecha 'YYYY-MM-DD'. Pisa cualquier aviso anterior."""
    data = {
        "text": text,
        "expires": expires,
        "set_by": store_forward.normalize_node_id(set_by),
        "set_at": datetime.now().isoformat(),
    }
    with open(FILE_PATH, "w") as f:
        json.dump(data, f)


def get_active_announcement(today: Optional[date] = None) -> Optional[dict]:
    """Devuelve {'text': str, 'expires': date} si hay un aviso vigente
    (la fecha de vencimiento es inclusive: se muestra todo ese día),
    o None si no hay ninguno guardado, está corrupto, o ya venció."""
    if not os.path.exists(FILE_PATH):
        return None
    try:
        with open(FILE_PATH, "r") as f:
            data = json.load(f)
    except (json.JSONDecodeError, OSError):
        return None
    try:
        expires_date = datetime.strptime(data["expires"], "%Y-%m-%d").date()
    except (KeyError, ValueError, TypeError):
        return None
    if (today or date.today()) > expires_date:
        return None
    return {"text": data.get("text", ""), "expires": expires_date}
