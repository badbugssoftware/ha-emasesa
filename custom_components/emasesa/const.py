"""Constantes de la integración EMASESA."""
from __future__ import annotations

from datetime import timedelta

DOMAIN = "emasesa"
PLATFORMS = ["sensor"]

# --- Endpoints de la API privada de la app "Mi Emasesa" -------------------
API_ROOT = "https://api.emasesa.com"
API_BASE = "https://api.emasesa.com/miemasesa/api/v1.0"
TOKEN_URL = f"{API_ROOT}/oauth2/token?grant_type=client_credentials"

# Credencial "client_credentials" embebida en el APK (entorno PRO).
# Es Base64 de "<client_id>:<client_secret>" y viaja como cabecera
# "Authorization: Basic ...". Está presente en cualquier copia de la app.
CLIENT_BASIC = "S3VtbGxFOFRtWUs1TV9DWldqR2xMRTVrVFhJYTp0emdYZGl6emZKeEVwRUN6bGZmYzBpN0ZXWkVh"

# La app se identifica como "sistema=3" (Android).
SISTEMA = "3"

# --- Claves de configuración ----------------------------------------------
CONF_USERNAME = "usuario"          # NIF/DNI/NIE
CONF_PASSWORD = "contrasena"
CONF_DEVICE_ID = "id_dispositivo"
CONF_CONTRACT_ID = "contrato_id"
CONF_CONTRACT_NUMBER = "contrato_numero"
CONF_SUPPLY_ADDRESS = "direccion_suministro"
CONF_SCAN_MINUTES = "scan_minutes"

# --- Valores por defecto ---------------------------------------------------
# El consumo por telelectura se consolida como mucho cada hora (y con 1-2 días
# de retraso), así que no tiene sentido pollear muy a menudo. Mínimo 30 min.
DEFAULT_SCAN_MINUTES = 45
MIN_SCAN_MINUTES = 15
DEFAULT_SCAN_INTERVAL = timedelta(minutes=DEFAULT_SCAN_MINUTES)

# Días de histórico horario a importar en el primer arranque (backfill).
INITIAL_BACKFILL_DAYS = 60
# En cada actualización, re-importamos los últimos N días (rellena huecos).
UPDATE_BACKFILL_DAYS = 4
# Tope duro al rellenar un hueco largo (API o HA caídos mucho tiempo).
MAX_BACKFILL_DAYS = 365

ATTRIBUTION = "Datos de EMASESA (Oficina Virtual / Mi Emasesa)"

# El índice del contador y los consumos vienen en LITROS.
LITERS_PER_M3 = 1000.0
