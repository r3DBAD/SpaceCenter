"""Генератор диаграммы IDEF1X (уровень ключей и атрибутов, FA) через Graphviz.

Обозначения IDEF1X (Р 50.1.028 / FIPS 184):
* независимая сущность — прямоугольник с прямыми углами;
* зависимая сущность — прямоугольник со скруглёнными углами;
* имя сущности — над прямоугольником, ключевая область отделена горизонтальной линией;
* идентифицирующая связь — сплошная линия, неидентифицирующая — пунктир;
* точка на конце у потомка; буква мощности у точки: P (1+), Z (0..1), пусто (0+);
* ромб у родителя — необязательная неидентифицирующая связь (FK допускает NULL).
"""
import subprocess
from pathlib import Path

HERE = Path(__file__).parent

# (имя, зависимая?, [(атрибут, тип, пометка)]); пометки: PK, FK, AK1, пусто; PK-атрибуты первыми
ENTITIES = [
    ("STAFF", False, [("staff_id", "INTEGER", "PK"), ("login", "TEXT", "AK1"), ("full_name", "TEXT", ""),
                      ("role", "TEXT", ""), ("password_hash", "TEXT", ""), ("password_salt", "TEXT", ""),
                      ("is_active", "INTEGER", "")]),
    ("VEHICLE_TYPE", False, [("vehicle_type_id", "INTEGER", "PK"), ("name", "TEXT", "AK1"),
                             ("vehicle_class", "TEXT", ""), ("stages_count", "INTEGER", "")]),
    ("LAUNCH_PAD", False, [("pad_id", "INTEGER", "PK"), ("code", "TEXT", "AK1"), ("name", "TEXT", ""),
                           ("max_concurrent", "INTEGER", ""), ("cycle_limit", "INTEGER", ""),
                           ("hours_limit", "REAL", ""), ("is_faulty", "INTEGER", "")]),
    ("LAUNCH", False, [("launch_id", "INTEGER", "PK"), ("vehicle_type_id", "INTEGER", "FK"),
                       ("pad_id", "INTEGER", "FK"), ("created_by", "INTEGER", "FK"),
                       ("payload", "TEXT", ""), ("target_orbit", "TEXT", ""),
                       ("window_start", "TEXT", ""), ("window_end", "TEXT", ""),
                       ("status", "TEXT", ""), ("pad_hours", "REAL", "")]),
    ("STAGE_LOG", True, [("launch_id", "INTEGER", "PK,FK"), ("seq_no", "INTEGER", "PK"),
                         ("status", "TEXT", ""), ("changed_at", "TEXT", ""), ("staff_id", "INTEGER", "FK")]),
    ("PAD_MAINTENANCE", True, [("pad_id", "INTEGER", "PK,FK"), ("maint_no", "INTEGER", "PK"),
                               ("kind", "TEXT", ""), ("opened_at", "TEXT", ""), ("closed_at", "TEXT", ""),
                               ("notes", "TEXT", ""), ("staff_id", "INTEGER", "FK")]),
    ("FUEL_BATCH", False, [("batch_id", "INTEGER", "PK"), ("batch_no", "TEXT", "AK1"),
                           ("component", "TEXT", ""), ("grade", "TEXT", ""), ("initial_volume", "REAL", ""),
                           ("produced_on", "TEXT", ""), ("expires_on", "TEXT", "")]),
    ("FUEL_CONSUMPTION", False, [("consumption_id", "INTEGER", "PK"), ("launch_id", "INTEGER", "FK"),
                                 ("batch_id", "INTEGER", "FK"), ("volume", "REAL", ""),
                                 ("consumed_at", "TEXT", ""), ("staff_id", "INTEGER", "FK")]),
    ("WEATHER_OBSERVATION", True, [("launch_id", "INTEGER", "PK,FK"), ("obs_no", "INTEGER", "PK"),
                                   ("observed_at", "TEXT", ""), ("ground_wind", "REAL", ""),
                                   ("altitude_wind", "REAL", ""), ("temperature", "REAL", ""),
                                   ("cloud_base", "REAL", ""), ("thunderstorm", "INTEGER", ""),
                                   ("staff_id", "INTEGER", "FK")]),
    ("TELEMETRY_CHANNEL", False, [("channel_id", "INTEGER", "PK"), ("channel_no", "TEXT", "AK1"),
                                  ("vehicle_system", "TEXT", ""), ("parameter", "TEXT", ""),
                                  ("frequency_hz", "REAL", "")]),
    ("INCIDENT", False, [("incident_id", "INTEGER", "PK"), ("launch_id", "INTEGER", "FK"),
                         ("channel_id", "INTEGER", "FK"), ("occurred_at", "TEXT", ""),
                         ("description", "TEXT", ""), ("measures", "TEXT", ""), ("staff_id", "INTEGER", "FK")]),
    ("POSTPONEMENT", True, [("launch_id", "INTEGER", "PK,FK"), ("postpone_no", "INTEGER", "PK"),
                            ("reason", "TEXT", ""), ("old_start", "TEXT", ""), ("old_end", "TEXT", ""),
                            ("new_start", "TEXT", ""), ("new_end", "TEXT", ""), ("comment", "TEXT", ""),
                            ("staff_id", "INTEGER", "FK")]),
]

# (родитель, потомок, идентифицирующая?, глагольная фраза, мощность, необязательная?)
RELATIONS = [
    ("VEHICLE_TYPE", "LAUNCH", False, "используется в", "", False),
    ("LAUNCH_PAD", "LAUNCH", False, "назначена для", "", False),
    ("STAFF", "LAUNCH", False, "регистрирует", "", False),
    ("LAUNCH", "STAGE_LOG", True, "проходит", "P", False),
    ("STAFF", "STAGE_LOG", False, "отмечает", "", False),
    ("LAUNCH_PAD", "PAD_MAINTENANCE", True, "обслуживается", "", False),
    ("LAUNCH", "FUEL_CONSUMPTION", False, "заправляется", "", False),
    ("FUEL_BATCH", "FUEL_CONSUMPTION", False, "расходуется", "", False),
    ("LAUNCH", "WEATHER_OBSERVATION", True, "обеспечивается", "", False),
    ("LAUNCH", "POSTPONEMENT", True, "переносится", "", False),
    ("LAUNCH", "INCIDENT", False, "сопровождается", "", False),
    ("TELEMETRY_CHANNEL", "INCIDENT", False, "фиксирует потерю", "", True),
]


def entity_node(name, dependent, attrs):
    keys = [a for a in attrs if "PK" in a[2]]
    rest = [a for a in attrs if "PK" not in a[2]]

    def text(items):
        return "".join(f'{a[0]}: {a[1]}{" (" + a[2] + ")" if a[2] else ""}<BR ALIGN="LEFT"/>' for a in items)
    style = ' STYLE="ROUNDED"' if dependent else ""
    inner = (f'<TABLE BORDER="1.5"{style} CELLBORDER="0" CELLSPACING="0" CELLPADDING="5" BGCOLOR="white">'
             f'<TR><TD ALIGN="LEFT" BALIGN="LEFT">{text(keys)}</TD></TR><HR/>'
             f'<TR><TD ALIGN="LEFT" BALIGN="LEFT">{text(rest)}</TD></TR></TABLE>')
    return (f'{name} [shape=plain label=<<TABLE BORDER="0" CELLBORDER="0" CELLSPACING="0">'
            f'<TR><TD ALIGN="LEFT"><B>{name}</B></TD></TR><TR><TD>{inner}</TD></TR></TABLE>>];')


def build():
    lines = ['digraph idef1x {', ' graph [rankdir=TB fontname="Arial" nodesep=0.6 ranksep=0.9 splines=true pad=0.3 bgcolor=white];',
             ' node [fontname="Arial" fontsize=10];', ' edge [fontname="Arial" fontsize=9 dir=both arrowtail=none arrowhead=dot arrowsize=0.8];']
    for e in ENTITIES:
        lines.append(" " + entity_node(*e))
    for parent, child, ident, verb, card, optional in RELATIONS:
        style = "solid" if ident else "dashed"
        tail = "odiamond" if optional else "none"
        lab = f'label="{verb}" headlabel="{card}"' if card else f'label="{verb}"'
        lines.append(f' {parent} -> {child} [style={style} arrowtail={tail} {lab}];')
    lines += [' {rank=same; STAFF; VEHICLE_TYPE; LAUNCH_PAD; FUEL_BATCH; TELEMETRY_CHANNEL}',
              ' {rank=same; STAGE_LOG; WEATHER_OBSERVATION; POSTPONEMENT; FUEL_CONSUMPTION; INCIDENT; PAD_MAINTENANCE}',
              '}']
    src = HERE / "idef1x.dot"
    src.write_text("\n".join(lines), encoding="utf-8")
    subprocess.run(["dot", "-Tsvg", str(src), "-o", str(HERE / "idef1x.svg")], check=True)
    subprocess.run(["dot", "-Tpng", "-Gdpi=110", str(src), "-o", str(HERE / "idef1x.png")], check=True)
    print("saved idef1x")


if __name__ == "__main__":
    build()
