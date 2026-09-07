"""El ingest le dice al bloque de cliente si el bot ya habló (2026-09-06).

El bloque `=== CLIENTE ===` se reinyecta en cada turno, así que una orden de
saludar que se repite compite con el `system_prompt_template`, que manda
saludar una sola vez. `prompt_context` ya sabe distinguir el primer turno; lo
que se fija aquí es el CABLEADO, que es donde se escondería el error: la
pregunta correcta es por la AUTORÍA, no por la dirección.

Un echo del operador también es `outbound` (ADR-013), y que el operador haya
escrito no significa que el bot ya haya saludado. Preguntar por `direction`
haría que el bot nunca salude en una conversación que el operador abrió.

Stub propio por archivo, como el resto de tests/services.
"""
from __future__ import annotations

import asyncio
import os
import sys
import uuid
from datetime import datetime, timedelta, timezone

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../../sales_agent_api"))

from app.models.core import Client, ClientUser, Conversation, Message
from app.services import ingest as ingest_mod
from app.services.ingest import ingest_message

CLIENT_ID = uuid.UUID("00000000-0000-0000-0000-000000000001")
USER_ID = uuid.UUID("59cd973e-a94d-4a2f-a228-9ce5d74fe2ac")
CONV_ID = uuid.UUID("90aa2b87-a773-4ebb-98e2-436ee515497c")
BSUID = "CO.0000000000001234"
NOW = datetime(2026, 9, 6, 22, 31, 25, tzinfo=timezone.utc)

PERFIL = {"first_name": "Sebastián", "full_name": "Sebastián Ramirez", "purchase_count": 3}


class _Result:
    def __init__(self, row):
        self._row = row

    def scalar_one_or_none(self):
        return self._row

    def scalar_one(self):
        return self._row

    def scalars(self):
        return self

    def all(self):
        return self._row if isinstance(self._row, list) else []


class FakeSession:
    def __init__(self, results):
        self._results = list(results)
        self.statements = []
        self.added = []

    async def execute(self, statement, params=None):
        self.statements.append(statement)
        if not self._results:
            raise AssertionError("el ingest pasó de los resultados enlatados")
        return _Result(self._results.pop(0))

    def add(self, obj):
        self.added.append(obj)

    async def flush(self):
        pass

    async def commit(self):
        pass


def _msg(direction, content, author, minutes_ago):
    return Message(
        id=uuid.uuid4(),
        conversation_id=CONV_ID,
        client_id=CLIENT_ID,
        direction=direction,
        author=author,
        message_type="text",
        content=content,
        created_at=NOW - timedelta(minutes=minutes_ago),
    )


def _run(historial):
    """Corre un turno completo del ingest con el historial dado y devuelve el
    bloque de contexto que n8n le pasaría al LLM."""
    client = Client(
        id=CLIENT_ID, is_active=True, business_rules={},
        system_prompt_template="", ai_model="gpt-4o-mini", ai_temperature=0.3,
    )
    user = ClientUser()
    user.id, user.client_id, user.bsuid = USER_ID, CLIENT_ID, BSUID
    user.display_name, user.is_blocked, user.profile = "Sebastian", False, PERFIL
    conv = Conversation(
        id=CONV_ID, client_id=CLIENT_ID, client_user_id=USER_ID, state="active",
        extracted_context={}, strategy_version=3, message_count=4,
        last_message_at=NOW - timedelta(minutes=1), active_goal="close_sale",
    )
    session = FakeSession([
        client, None, user, conv, None, None,   # cliente, idem, usuario, conv, lock, contadores
        None,                                   # pausa de operador: ninguno
        None, None, conv, None,                 # debounce, lock2, recarga, estrategia
        [],                                     # catálogo
        list(reversed(historial)),              # recent_messages (DESC desde SQL)
    ])

    async def _no_sleep(_s):
        return None

    original = ingest_mod.asyncio.sleep
    ingest_mod.asyncio.sleep = _no_sleep
    try:
        result = asyncio.run(ingest_message(
            session=session, client_id=CLIENT_ID, chakra_message_id="wamid.T",
            content="Que cafes tienes?", bsuid=BSUID, display_name="Sebastian",
            timestamp=NOW,
        ))
    finally:
        ingest_mod.asyncio.sleep = original
    return result["conversation_summary"]


def test_sin_respuestas_previas_el_bot_saluda():
    resumen = _run([_msg("inbound", "Buenas", "customer", 1)])

    assert "primer mensaje de la conversación" in resumen
    assert "YA SALUDASTE" not in resumen


def test_con_una_respuesta_previa_del_bot_ya_no_saluda():
    """El caso real del 2026-09-06: en el segundo turno el bot volvió a decir
    "Hola, Sebastián" porque la instrucción de saludar seguía puesta."""
    resumen = _run([
        _msg("inbound", "Buenas", "customer", 2),
        _msg("outbound", "Hola, Sebastián. ¿Cómo vas?", "bot", 1),
    ])

    assert "YA SALUDASTE" in resumen
    assert "primer mensaje de la conversación" not in resumen


def test_un_echo_del_operador_no_cuenta_como_saludo_del_bot():
    """La pregunta es por AUTORÍA, no por dirección. Un echo del operador es
    outbound (ADR-013); si se preguntara por `direction`, el bot no saludaría
    nunca en una conversación que abrió el humano."""
    resumen = _run([
        _msg("inbound", "Buenas", "customer", 3),
        _msg("outbound", "[operador] ya te atiendo", "operator", 2),
    ])

    assert "primer mensaje de la conversación" in resumen
    assert "YA SALUDASTE" not in resumen
