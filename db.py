"""db.py — base de données 100% JSON.

Ce module remplace l'ancien backend (souvent SQLite) par un fichier JSON.
L'API est compatible avec `discord_bot.py`.

✅ Important (demandé) : après suppression d'une demande, les IDs sont
réattribués (1..N) afin qu'il n'y ait jamais de "trous".

Fichier JSON (par défaut) : `requests_db.json` dans le même dossier.
Vous pouvez changer l'emplacement avec la variable d'environnement :
    REQUESTS_DB_PATH=/chemin/vers/requests_db.json

Chaque demande est stockée avec :
    id, user_id, platform, title, year, category, status, created_at, result

- `status` = état de traitement (file_attente / en_cours / ajout_non_dispo / pas_encore_sorti)
- `result` = résultat final (optionnel) :
    "" (vide) / "dispo" / "non_dispo"

Les fonctions retournent des tuples dans le format historique + `result` en fin
(ça évite de casser les index existants) :
    (req_id, user_id, platform, title, year, category, status, created_at, result)
"""

from __future__ import annotations

import json
import os
import threading
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Tuple


# ---------- CONFIG ----------

_DEFAULT_DB_FILENAME = "requests_db.json"
_DB_PATH = Path(os.getenv("REQUESTS_DB_PATH", "")).expanduser()
if not str(_DB_PATH).strip():
    _DB_PATH = Path(__file__).resolve().parent / _DEFAULT_DB_FILENAME

_LOCK = threading.Lock()


# ---------- MODELE ----------

@dataclass
class RequestItem:
    id: int
    user_id: str
    platform: str
    title: str
    year: int
    category: str
    status: str
    created_at: str
    result: str = ""  # "", "dispo", "non_dispo"

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "user_id": self.user_id,
            "platform": self.platform,
            "title": self.title,
            "year": int(self.year),
            "category": self.category,
            "status": self.status,
            "created_at": self.created_at,
            "result": self.result,
        }

    @staticmethod
    def from_dict(d: Dict[str, Any]) -> "RequestItem":
        # Compat : anciennes DB sans champ "result"
        result = str(d.get("result", "") or "")
        if result not in ("", "dispo", "non_dispo"):
            result = ""

        # Migration douce depuis d'anciens statuts
        status = str(d.get("status", "file_attente"))
        if status == "ajout_dispo":
            if result == "":
                result = "dispo"
            status = "en_cours"
        elif status == "traitee":
            status = "en_cours"

        return RequestItem(
            id=int(d.get("id", 0)),
            user_id=str(d.get("user_id", "")),
            platform=str(d.get("platform", "discord")),
            title=str(d.get("title", "")),
            year=int(d.get("year", 0) or 0),
            category=str(d.get("category", "")),
            status=status,
            created_at=str(d.get("created_at", "")),
            result=result,
        )


# ---------- I/O ----------

def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")


def _empty_db() -> Dict[str, Any]:
    # version 2 = ajout du champ "result"
    return {"meta": {"version": 2}, "requests": []}


def _read_db_unlocked() -> Dict[str, Any]:
    if not _DB_PATH.exists():
        data = _empty_db()
        _write_db_unlocked(data)
        return data

    try:
        raw = _DB_PATH.read_text(encoding="utf-8")
        data = json.loads(raw) if raw.strip() else _empty_db()
        if not isinstance(data, dict):
            return _empty_db()
        if "requests" not in data or not isinstance(data.get("requests"), list):
            data["requests"] = []
        if "meta" not in data or not isinstance(data.get("meta"), dict):
            data["meta"] = {"version": 2}
        return data
    except Exception:
        # si fichier corrompu -> fallback safe
        return _empty_db()


def _write_db_unlocked(data: Dict[str, Any]) -> None:
    _DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = _DB_PATH.with_suffix(_DB_PATH.suffix + ".tmp")
    tmp_path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp_path.replace(_DB_PATH)


def _load_requests_unlocked() -> List[RequestItem]:
    data = _read_db_unlocked()
    items: List[RequestItem] = []
    for r in data.get("requests", []):
        if isinstance(r, dict):
            items.append(RequestItem.from_dict(r))

    # sécurité : tri + normalisation id
    items.sort(key=lambda x: x.id)
    for idx, it in enumerate(items, start=1):
        it.id = idx
    return items


def _save_requests_unlocked(items: List[RequestItem]) -> None:
    data = _read_db_unlocked()
    data["requests"] = [it.to_dict() for it in items]
    # meta
    meta = data.get("meta") if isinstance(data.get("meta"), dict) else {}
    meta["version"] = 2
    data["meta"] = meta
    _write_db_unlocked(data)


# ---------- API PUBLIQUE ----------

def init_db() -> None:
    """Crée le fichier JSON si absent."""
    with _LOCK:
        _read_db_unlocked()


def add_request(
    user_id: str,
    platform: str,
    title: str,
    year: int,
    category: str,
    status: str = "file_attente",
) -> int:
    """Ajoute une demande. Retourne le nouvel ID (1..N)."""
    with _LOCK:
        items = _load_requests_unlocked()
        new_id = len(items) + 1
        items.append(
            RequestItem(
                id=new_id,
                user_id=str(user_id),
                platform=str(platform),
                title=str(title),
                year=int(year or 0),
                category=str(category),
                status=str(status),
                created_at=_utc_now_iso(),
                result="",
            )
        )
        _save_requests_unlocked(items)
        return new_id


def list_all_requests() -> List[Tuple[int, str, str, str, int, str, str, str, str]]:
    with _LOCK:
        items = _load_requests_unlocked()
        return [
            (it.id, it.user_id, it.platform, it.title, it.year, it.category, it.status, it.created_at, it.result)
            for it in items
        ]


def list_open_requests() -> List[Tuple[int, str, str, str, int, str, str, str, str]]:
    """Retourne les demandes 'en cours' (file_attente + en_cours) et sans résultat final."""
    open_statuses = {"file_attente", "en_cours"}
    with _LOCK:
        items = _load_requests_unlocked()
        items = [it for it in items if it.status in open_statuses and (it.result or "") == ""]
        return [
            (it.id, it.user_id, it.platform, it.title, it.year, it.category, it.status, it.created_at, it.result)
            for it in items
        ]


def update_status(request_id: int, new_status: str) -> bool:
    with _LOCK:
        items = _load_requests_unlocked()
        found = False
        for it in items:
            if it.id == int(request_id):
                it.status = str(new_status)
                found = True
                break
        if not found:
            return False
        _save_requests_unlocked(items)
        return True


def update_result(request_id: int, result_code: str) -> bool:
    """result_code: "dispo" | "non_dispo" | "" (vide)."""
    if result_code not in ("", "dispo", "non_dispo"):
        return False

    with _LOCK:
        items = _load_requests_unlocked()
        found = False
        for it in items:
            if it.id == int(request_id):
                it.result = result_code
                found = True
                break
        if not found:
            return False
        _save_requests_unlocked(items)
        return True


def delete_request(request_id: int) -> bool:
    """Supprime une demande, puis renumérote les IDs (1..N)."""
    with _LOCK:
        items = _load_requests_unlocked()
        before = len(items)
        items = [it for it in items if it.id != int(request_id)]
        if len(items) == before:
            return False

        # renumérotation
        for idx, it in enumerate(items, start=1):
            it.id = idx

        _save_requests_unlocked(items)
        return True
