#!/usr/bin/env python3
"""Verificador del modelo de sistema — convierte las invariantes en un candado.

Lee `sales-ai-docs/docs/system-model/*.yaml` y falla cuando una declaración deja
de corresponderse con el repo. Existe por una razón concreta: este proyecto ya
tiene mucha documentación en prosa (ROADMAP, ADRs, postmortems, la tabla de
deudas) y la prosa no falla en CI. P31 se cerró el 2026-09-05 y se
reabrió el 09-07 porque nada verificaba la afirmación "P29 lo cubre entero".

La regla que da todo el valor es una sola:

    NO SE PUEDE DECLARAR QUE UNA INVARIANTE SE CUMPLE SIN UNA PRUEBA QUE LA
    EJERCITE.

Todo lo demás son consecuencias de eso. Una invariante sin prueba se declara
`unverified` y sale en el reporte; una que se sabe rota se declara `violated` y
tiene que apuntar a la deuda o al frente P donde vive. El estado `partial` es
para lo que este sistema tiene de sobra: mitades remediadas (deuda #14 dice
literalmente "REMEDIADA EN SU MITAD DE PRESENCIA"), y obliga a escribir en
`gap` qué es lo que NO cubre la prueba.

Uso:
    python tools/check_invariants.py            # verifica, exit 1 si hay errores
    python tools/check_invariants.py --report   # además imprime el estado completo
    python tools/check_invariants.py --no-tests # salta la colección de pytest (rápido)
"""
from __future__ import annotations

import argparse
import re
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path

try:
    import yaml
except ImportError:  # pragma: no cover - mensaje para quien lo corre en local
    sys.exit(
        "Falta PyYAML. Instalar con:  pip install -r tools/requirements.txt"
    )


REPO_ROOT = Path(__file__).resolve().parent.parent
MODEL_DIR = REPO_ROOT / "sales-ai-docs" / "docs" / "system-model"
DECISIONS_DIR = REPO_ROOT / "sales-ai-docs" / "docs" / "decisions"
ROADMAP = REPO_ROOT / "sales-ai-docs" / "docs" / "ROADMAP.md"
TESTS_DIR = REPO_ROOT / "tests"

#: Estados admitidos. El orden es el del reporte: de lo peor a lo mejor.
STATUSES = ("violated", "unverified", "partial", "holds")

#: Un estado que AFIRMA algo sobre el comportamiento vivo necesita prueba.
#: `unverified` y `violated` no la necesitan — precisamente porque no afirman.
STATUSES_REQUIRING_TESTS = {"holds", "partial"}

#: Dónde puede vivir la garantía. `n8n` y `db` marcan invariantes que el backend
#: NO puede probar solo: son las que más nos han costado (deuda #15, #16, P29
#: fase 4). Se declaran igual, con la honestidad de que su prueba está fuera.
BOUNDARIES = {"backend", "n8n", "db", "infra", "cross"}

ID_RE = re.compile(r"^INV-([A-Z]{2,6})-(\d{3})$")
REF_RE = re.compile(r"^(ADR-\d{3}|P\d+|deuda#\d+|migración-\d{3})$")

REQUIRED_COMPONENT_KEYS = {"component", "code", "layer", "summary", "owns", "invariants"}
REQUIRED_INVARIANT_KEYS = {"id", "statement", "why", "status", "enforcement"}


@dataclass
class Problem:
    """Un incumplimiento del modelo. `where` es file:invariante para poder ir."""

    where: str
    message: str


@dataclass
class Model:
    components: list[dict] = field(default_factory=list)
    problems: list[Problem] = field(default_factory=list)

    def fail(self, where: str, message: str) -> None:
        self.problems.append(Problem(where, message))


# --------------------------------------------------------------------------- #
# Registros canónicos del ROADMAP
# --------------------------------------------------------------------------- #


def _registry_rows(section: str, row_re: str) -> set[int]:
    """Números con fila propia en una tabla del ROADMAP, bajo `## <section>`.

    Lee solo la primera columna de cada fila y se detiene en el siguiente
    encabezado `## `, así que una mención en prosa no cuenta como registro.
    """
    text = ROADMAP.read_text(encoding="utf-8")
    start = text.find(f"\n## {section}\n")
    if start == -1:
        return set()
    end = text.find("\n## ", start + 1)
    body = text[start : end if end != -1 else len(text)]
    return {int(m.group(1)) for m in re.finditer(row_re, body, re.MULTILINE)}


_REGISTRIES: dict[str, set[int]] | None = None


def registries() -> dict[str, set[int]]:
    """`P` y `deuda` → los números que el ROADMAP tiene registrados.

    Las deudas vivían en CLAUDE.md, que está gitignored: el modelo versionado
    las citaba y nada podía comprobar que existieran. Desde que viven en el
    ROADMAP, un `deuda#N` o un `PN` que no esté en su registro es un error.
    """
    global _REGISTRIES
    if _REGISTRIES is None:
        _REGISTRIES = {
            "P": _registry_rows("Registro canónico de frentes P", r"^\| P(\d+) \|"),
            "deuda": _registry_rows("Registro canónico de deudas", r"^\| (\d+) \|"),
        }
    return _REGISTRIES


def validate_roadmap(model: Model) -> None:
    """Un frente ✅ en el registro no puede seguir con su sección bajo ABIERTOS.

    Pasó con P14: cerrado el 2026-08-19 y todavía 83 líneas bajo 🔴 ABIERTOS un
    mes después. Al cerrar, el texto va tal cual a `registros/roadmap-historico.md`
    y en el ROADMAP queda una línea en ✅ CERRADO.
    """
    text = ROADMAP.read_text(encoding="utf-8")
    reg_start = text.find("\n## Registro canónico de frentes P\n")
    reg_end = text.find("\n## ", reg_start + 1)
    closed = {
        int(m.group(1))
        for m in re.finditer(r"^\| P(\d+) \|[^|]*\| ✅", text[reg_start:reg_end], re.MULTILINE)
    }
    open_start = text.find("\n## 🔴 ABIERTOS")
    open_end = text.find("\n## ✅ CERRADO")
    for m in re.finditer(r"^### P(\d+) ·", text[open_start:open_end], re.MULTILINE):
        if int(m.group(1)) in closed:
            model.fail(
                "sales-ai-docs/docs/ROADMAP.md",
                f"P{m.group(1)} está ✅ en el registro pero su sección sigue bajo ABIERTOS; "
                "moverla tal cual a docs/registros/roadmap-historico.md",
            )


# --------------------------------------------------------------------------- #
# Colección de pytest
# --------------------------------------------------------------------------- #


def collect_test_ids() -> set[str]:
    """Node ids reales de la suite, vía `pytest --collect-only -q`.

    Se sale al proceso a propósito en vez de importar pytest: la suite mete
    `sales_agent_api` en sys.path desde cada archivo de test y colectarla dentro
    de este proceso contaminaría el import. Además así el checker verifica la
    MISMA colección que corre el CI.
    """
    proc = subprocess.run(
        [sys.executable, "-m", "pytest", "--collect-only", "-q", str(TESTS_DIR)],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
    )
    ids: set[str] = set()
    for line in proc.stdout.splitlines():
        line = line.strip()
        if "::" in line and line.startswith("tests/"):
            # Un id parametrizado trae [param]; se guarda entero y sin corchetes,
            # para que el YAML pueda referenciar la función sin conocer los casos.
            ids.add(line)
            ids.add(line.split("[", 1)[0])
    if not ids:
        sys.stderr.write(
            "AVISO: pytest no colectó ningún test. Salida:\n"
            f"{proc.stdout[-2000:]}\n{proc.stderr[-2000:]}\n"
        )
    return ids


# --------------------------------------------------------------------------- #
# Validación
# --------------------------------------------------------------------------- #


def validate_component(model: Model, path: Path, doc: dict, test_ids: set[str] | None) -> None:
    rel = path.relative_to(REPO_ROOT)
    where = str(rel)

    missing = REQUIRED_COMPONENT_KEYS - doc.keys()
    if missing:
        model.fail(where, f"faltan claves obligatorias: {sorted(missing)}")
        return

    code = doc["code"]
    if not re.fullmatch(r"[A-Z]{2,6}", str(code)):
        model.fail(where, f"`code` debe ser 2-6 mayúsculas, es {code!r}")

    # Los archivos que el componente dice gobernar tienen que existir. Esto es lo
    # que hace que un rename de servicio rompa el modelo en vez de pudrirlo.
    for rel_code in doc["owns"].get("code", []) or []:
        target = REPO_ROOT / str(rel_code).split("::", 1)[0]
        if not target.exists():
            model.fail(where, f"`owns.code` apunta a algo que no existe: {rel_code}")

    invariants = doc["invariants"] or []
    if not invariants:
        model.fail(where, "el componente no declara ninguna invariante")

    for inv in invariants:
        validate_invariant(model, where, code, inv, test_ids)


def validate_invariant(
    model: Model, where: str, code: str, inv: dict, test_ids: set[str] | None
) -> None:
    inv_id = inv.get("id", "<sin id>")
    at = f"{where}:{inv_id}"

    missing = REQUIRED_INVARIANT_KEYS - inv.keys()
    if missing:
        model.fail(at, f"faltan claves obligatorias: {sorted(missing)}")
        return

    m = ID_RE.fullmatch(str(inv_id))
    if not m:
        model.fail(at, "el id debe tener la forma INV-<CODE>-<NNN>")
    elif m.group(1) != code:
        model.fail(at, f"el id dice {m.group(1)} pero el componente es {code}")

    status = inv["status"]
    if status not in STATUSES:
        model.fail(at, f"status inválido {status!r}; admitidos: {list(STATUSES)}")
        return

    boundary = inv.get("boundary", "backend")
    if boundary not in BOUNDARIES:
        model.fail(at, f"boundary inválido {boundary!r}; admitidos: {sorted(BOUNDARIES)}")

    # Las referencias son el puente con la documentación que ya existe. Un
    # ADR-NNN tiene que resolver a un archivo real, y un PN o deuda#N a una fila
    # de su registro en el ROADMAP; si alguien renombra o renumera, el modelo lo
    # grita en vez de quedarse con el enlace muerto.
    for ref in inv.get("refs", []) or []:
        ref = str(ref)
        if not REF_RE.fullmatch(ref):
            model.fail(at, f"referencia con forma desconocida: {ref!r}")
        elif ref.startswith("ADR-"):
            num = ref.split("-", 1)[1]
            if not list(DECISIONS_DIR.glob(f"ADR-{num}-*.md")):
                model.fail(at, f"{ref} no resuelve a ningún archivo en docs/decisions/")
        elif ref.startswith("P") and int(ref[1:]) not in registries()["P"]:
            model.fail(at, f"{ref} no está en el ROADMAP § Registro canónico de frentes P")
        elif ref.startswith("deuda#") and int(ref[6:]) not in registries()["deuda"]:
            model.fail(at, f"{ref} no está en el ROADMAP § Registro canónico de deudas")

    enforcement = inv["enforcement"] or {}
    kind = enforcement.get("kind")
    if kind not in {"test", "manual", "none"}:
        model.fail(at, f"enforcement.kind inválido {kind!r}; admitidos: test, manual, none")

    declared_tests = enforcement.get("tests", []) or []

    # ---- La regla central -------------------------------------------------- #
    if status in STATUSES_REQUIRING_TESTS:
        if boundary == "backend" and kind != "test":
            model.fail(
                at,
                f"status '{status}' en el backend exige enforcement.kind: test — "
                "una invariante que se afirma sin prueba se declara 'unverified'",
            )
        if boundary == "backend" and not declared_tests:
            model.fail(at, f"status '{status}' exige al menos un test declarado")

    if status == "partial" and not str(enforcement.get("gap", "")).strip():
        model.fail(
            at,
            "status 'partial' exige `enforcement.gap`: escribir qué es lo que la "
            "prueba NO cubre, que es justo lo que se olvida",
        )

    if status == "violated" and not (inv.get("refs") or []):
        model.fail(
            at,
            "status 'violated' exige al menos una referencia (deuda#N o PN): una "
            "rotura que no está registrada en ningún lado se pierde",
        )

    # Toda prueba declarada tiene que existir, sea cual sea el estado. Esto es lo
    # que detecta el renombre de un test que dejó una invariante sin guardián.
    if test_ids is not None:
        for node_id in declared_tests:
            if node_id not in test_ids:
                model.fail(at, f"el test declarado no existe en la suite: {node_id}")


# --------------------------------------------------------------------------- #
# Reporte
# --------------------------------------------------------------------------- #

SYMBOL = {"holds": "✅", "partial": "🟡", "unverified": "❔", "violated": "🔴"}


def _oneline(text: str) -> str:
    """YAML plegado deja saltos y espacios; el reporte los quiere en una línea."""
    return " ".join(str(text).split())


def print_report(components: list[dict]) -> None:
    print()
    print("=" * 78)
    print("MODELO DE SISTEMA — estado de las invariantes")
    print("=" * 78)

    for doc in sorted(components, key=lambda d: d["component"]):
        print(f"\n▸ {doc['component']}  ·  capa {doc['layer']}")
        for inv in doc["invariants"]:
            status = inv["status"]
            boundary = inv.get("boundary", "backend")
            edge = "" if boundary == "backend" else f"  [{boundary}]"
            print(f"   {SYMBOL.get(status, '?')} {inv['id']}{edge}  {_oneline(inv['statement'])}")
            if status == "partial":
                print(f"        hueco: {_oneline(inv['enforcement']['gap'])}")
            if status in {"violated", "unverified"} and inv.get("refs"):
                print(f"        sigue en: {', '.join(inv['refs'])}")


def print_summary(components: list[dict]) -> None:
    tally = {s: 0 for s in STATUSES}
    tests = 0
    for doc in components:
        for inv in doc["invariants"]:
            tally[inv["status"]] = tally.get(inv["status"], 0) + 1
            tests += len((inv["enforcement"] or {}).get("tests", []) or [])

    total = sum(tally.values())
    print()
    print(
        f"{total} invariantes en {len(components)} componentes, "
        f"{tests} pruebas declaradas"
    )
    print(
        f"  ✅ {tally['holds']} se cumplen   "
        f"🟡 {tally['partial']} a medias   "
        f"❔ {tally['unverified']} sin verificar   "
        f"🔴 {tally['violated']} rotas"
    )


# --------------------------------------------------------------------------- #


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--report", action="store_true", help="imprimir el estado completo")
    parser.add_argument(
        "--no-tests",
        action="store_true",
        help="no colectar pytest (no verifica que las pruebas declaradas existan)",
    )
    args = parser.parse_args()

    if not MODEL_DIR.is_dir():
        print(f"No existe {MODEL_DIR.relative_to(REPO_ROOT)}", file=sys.stderr)
        return 1

    paths = sorted(MODEL_DIR.glob("*.yaml"))
    if not paths:
        print("El modelo de sistema no tiene ningún componente.", file=sys.stderr)
        return 1

    test_ids = None if args.no_tests else collect_test_ids()

    model = Model()
    validate_roadmap(model)
    seen_ids: dict[str, str] = {}

    for path in paths:
        try:
            doc = yaml.safe_load(path.read_text(encoding="utf-8"))
        except yaml.YAMLError as exc:
            model.fail(str(path.relative_to(REPO_ROOT)), f"YAML ilegible: {exc}")
            continue
        if not isinstance(doc, dict):
            model.fail(str(path.relative_to(REPO_ROOT)), "el archivo no es un mapa YAML")
            continue

        validate_component(model, path, doc, test_ids)
        model.components.append(doc)

        # Un id repetido entre archivos rompe toda referencia cruzada — el mismo
        # accidente que CLAUDE.md documenta con el `#10` reciclado.
        for inv in doc.get("invariants") or []:
            inv_id = inv.get("id")
            if inv_id in seen_ids:
                model.fail(
                    str(path.relative_to(REPO_ROOT)),
                    f"id duplicado {inv_id}, ya está en {seen_ids[inv_id]}",
                )
            elif inv_id:
                seen_ids[inv_id] = str(path.relative_to(REPO_ROOT))

    if args.report:
        print_report(model.components)

    print_summary(model.components)

    if model.problems:
        print()
        print(f"❌ {len(model.problems)} problema(s) en el modelo de sistema:")
        for p in model.problems:
            print(f"   {p.where}")
            print(f"      {p.message}")
        return 1

    print("\n✅ El modelo de sistema corresponde con el repo.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
