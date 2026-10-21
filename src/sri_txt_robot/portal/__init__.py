"""Automatización del portal del SRI."""

from __future__ import annotations

from .login import LoginError, iniciar_sesion
from .received import ConsultaSRIError, configurar_filtros, consultar_y_descargar, navegar_a_recibidos
from .selectors import Selectores, primer_visible
from .session import Navegador

__all__ = [
    "ConsultaSRIError",
    "LoginError",
    "Navegador",
    "Selectores",
    "configurar_filtros",
    "consultar_y_descargar",
    "iniciar_sesion",
    "navegar_a_recibidos",
    "primer_visible",
]
