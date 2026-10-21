"""Генератор диаграммы IDEF1X (уровень ключей и атрибутов, FA) через Graphviz.

Обозначения IDEF1X (Р 50.1.028 / FIPS 184):
* независимая сущность — прямоугольник с прямыми углами;
* зависимая сущность — прямоугольник со скруглёнными углами;
* имя сущности — над прямоугольником, ключевая область отделена горизонтальной линией;
* идентифицирующая связь — сплошная линия, неидентифицирующая — пунктир;
* точка на конце у потомка; буква мощности у точки: P (1+), Z (0..1), пусто (0+);
* ромб у родителя — необязательная неидентифицирующая связь (FK допускает NULL).

ЛР4: схема версии 2. Цветом выделены изменения: голубой — новая сущность, желтый —
измененная, серый пунктир — проект для требования Т2 (в коде не реализован).
Диаграмма ЛР3 сохранена в idef1x_lab3.*.
"""
import subprocess
from pathlib import Path

HERE = Path(__file__).parent

FILL = {"": "white", "new": "#dcebff", "changed": "#fff3cc", "plan": "#eeeeee"}

# (имя, зависимая?, [(атрибут, тип, пометка)], изменение); пометки: PK, FK, AK1, пусто; PK-атрибуты первыми
ENTITIES = [
    ("SPACEPORT", False, [("spaceport_id", "INTEGER", "PK"), ("code", "TEXT", "AK1"), ("name", "TEXT", ""),
                          ("region", "TEXT", "")], "new"),
    ("STAFF", False, [("staff_id", "INTEGER", "PK"), ("login", "TEXT", "AK1"), ("full_name", "TEXT", ""),
                      ("role", "TEXT", ""), ("password_hash", "TEXT", ""), ("password_salt", "TEXT", ""),
                      ("is_active", "INTEGER", ""), ("spaceport_id", "INTEGER", "FK, NULL")], "changed"),
    ("VEHICLE_TYPE", False, [("vehicle_type_id", "INTEGER", "PK"), ("name", "TEXT", "AK1"),
                             ("vehicle_class", "TEXT", ""), ("stages_count", "INTEGER", "")], ""),
    ("LAUNCH_PAD", False, [("pad_id", "INTEGER", "PK"), ("spaceport_id", "INTEGER", "FK"), ("code", "TEXT", "AK1"),
                           ("name", "TEXT", ""), ("max_concurrent", "INTEGER", ""), ("cycle_limit", "INTEGER", ""),
                           ("hours_limit", "REAL", ""), ("is_faulty", "INTEGER", "")], "changed"),
    ("LAUNCH", False, [("launch_id", "INTEGER", "PK"), ("vehicle_type_id", "INTEGER", "FK"),
                       ("pad_id", "INTEGER", "FK"), ("created_by", "INTEGER", "FK"),
                       ("payload", "TEXT", ""), ("target_orbit", "TEXT", ""),
                       ("window_start", "TEXT", ""), ("window_end", "TEXT", ""),
                       ("status", "TEXT", ""), ("pad_hours", "REAL", ""),
                       ("cancel_reason", "TEXT", "")], ""),
    ("STAGE_LOG", True, [("launch_id", "INTEGER", "PK,FK"), ("seq_no", "INTEGER", "PK"),
                         ("status", "TEXT", ""), ("changed_at", "TEXT", ""), ("staff_id", "INTEGER", "FK")], ""),
    ("PAD_MAINTENANCE", True, [("pad_id", "INTEGER", "PK,FK"), ("maint_no", "INTEGER", "PK"),
                               ("kind", "TEXT", ""), ("opened_at", "TEXT", ""), ("closed_at", "TEXT", ""),
                               ("notes", "TEXT", ""), ("staff_id", "INTEGER", "FK")], ""),
    ("FUEL_BATCH", False, [("batch_id", "INTEGER", "PK"), ("spaceport_id", "INTEGER", "FK"), ("batch_no", "TEXT", "AK1"),
                           ("component", "TEXT", ""), ("grade", "TEXT", ""), ("initial_mass_hg", "INTEGER", ""),
                           ("produced_on", "TEXT", ""), ("expires_on", "TEXT", "")], "changed"),
    ("FUEL_CONSUMPTION", False, [("consumption_id", "INTEGER", "PK"), ("launch_id", "INTEGER", "FK"),
                                 ("batch_id", "INTEGER", "FK"), ("mass_hg", "INTEGER", ""),
                                 ("consumed_at", "TEXT", ""), ("staff_id", "INTEGER", "FK")], "changed"),
    ("WEATHER_OBSERVATION", True, [("launch_id", "INTEGER", "PK,FK"), ("obs_no", "INTEGER", "PK"),
                                   ("observed_at", "TEXT", ""), ("ground_wind", "REAL", ""),
                                   ("altitude_wind", "REAL", ""), ("temperature", "REAL", ""),
                                   ("cloud_base", "REAL", ""), ("thunderstorm", "INTEGER", ""),
                                   ("staff_id", "INTEGER", "FK")], ""),
    ("TELEMETRY_CHANNEL", False, [("channel_id", "INTEGER", "PK"), ("channel_no", "TEXT", "AK1"),
                                  ("vehicle_system", "TEXT", ""), ("parameter", "TEXT", ""),
                                  ("frequency_hz", "REAL", "")], ""),
    ("INCIDENT", False, [("incident_id", "INTEGER", "PK"), ("launch_id", "INTEGER", "FK"),
                         ("channel_id", "INTEGER", "FK"), ("occurred_at", "TEXT", ""),
                         ("description", "TEXT", ""), ("measures", "TEXT", ""), ("staff_id", "INTEGER", "FK")], ""),
    ("POSTPONEMENT", True, [("launch_id", "INTEGER", "PK,FK"), ("postpone_no", "INTEGER", "PK"),
                            ("reason", "TEXT", "weather|technical|organizational"), ("old_start", "TEXT", ""),
                            ("old_end", "TEXT", ""), ("new_start", "TEXT", ""), ("new_end", "TEXT", ""),
                            ("comment", "TEXT", ""), ("recorded_at", "TEXT", ""), ("staff_id", "INTEGER", "FK")],
     "changed"),
    ("WEATHER_FORECAST", True, [("launch_id", "INTEGER", "PK,FK"), ("forecast_no", "INTEGER", "PK"),
                                ("window_start", "TEXT", ""), ("requested_at", "TEXT", ""), ("source", "TEXT", ""),
                                ("status", "TEXT", ""), ("valid_at", "TEXT", ""), ("ground_wind", "REAL", ""),
                                ("altitude_wind", "REAL", ""), ("temperature", "REAL", ""),
                                ("cloud_base", "REAL", ""), ("thunderstorm", "INTEGER", ""), ("details", "TEXT", ""),
                                ("staff_id", "INTEGER", "FK, NULL")], "new"),
    ("AUDIT_LOG", False, [("audit_id", "INTEGER", "PK"), ("recorded_at", "TEXT", ""), ("staff_id", "INTEGER", "FK"),
                          ("role", "TEXT", ""), ("action", "TEXT", ""), ("object_type", "TEXT", ""),
                          ("object_ref", "TEXT", ""), ("outcome", "TEXT", ""), ("details", "TEXT", "")], "new"),
    # проект для Т2 (динамические каналы по конфигурации носителя), в схеме БД нет
    ("VEHICLE_CHANNEL", True, [("vehicle_type_id", "INTEGER", "PK,FK"), ("channel_id", "INTEGER", "PK,FK")], "plan"),
    ("CHANNEL_MODE_RATE", True, [("vehicle_type_id", "INTEGER", "PK,FK"), ("channel_id", "INTEGER", "PK,FK"),
                                 ("mode", "TEXT", "PK"), ("frequency_hz", "REAL", "")], "plan"),
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
    ("SPACEPORT", "LAUNCH_PAD", False, "располагает", "", False),
    ("SPACEPORT", "FUEL_BATCH", False, "хранит", "", False),
    ("SPACEPORT", "STAFF", False, "закрепляет", "", True),
    ("LAUNCH", "WEATHER_FORECAST", True, "получает прогноз", "", False),
    ("STAFF", "WEATHER_FORECAST", False, "вводит вручную", "", True),
    ("STAFF", "AUDIT_LOG", False, "совершает действие", "", False),
    ("VEHICLE_TYPE", "VEHICLE_CHANNEL", True, "конфигурирует", "", False),
    ("TELEMETRY_CHANNEL", "VEHICLE_CHANNEL", True, "входит в", "", False),
    ("VEHICLE_CHANNEL", "CHANNEL_MODE_RATE", True, "опрашивается в режиме", "P", False),
]


def entity_node(name, dependent, attrs, change=""):
    keys = [a for a in attrs if "PK" in a[2]]
    rest = [a for a in attrs if "PK" not in a[2]]

    def text(items):
        return "".join(f'{a[0]}: {a[1]}{" (" + a[2] + ")" if a[2] else ""}<BR ALIGN="LEFT"/>' for a in items)
    style = ' STYLE="ROUNDED"' if dependent else ""
    inner = (f'<TABLE BORDER="1.5"{style} CELLBORDER="0" CELLSPACING="0" CELLPADDING="5" BGCOLOR="{FILL[change]}">'
             f'<TR><TD ALIGN="LEFT" BALIGN="LEFT">{text(keys)}</TD></TR><HR/>'
             f'<TR><TD ALIGN="LEFT" BALIGN="LEFT">{text(rest)}</TD></TR></TABLE>')
    return (f'{name} [shape=plain label=<<TABLE BORDER="0" CELLBORDER="0" CELLSPACING="0">'
            f'<TR><TD ALIGN="LEFT"><B>{name}</B>{" (проект, Т2)" if change == "plan" else ""}</TD></TR>'
            f'<TR><TD>{inner}</TD></TR></TABLE>>];')


def build():
    lines = ['digraph idef1x {', ' graph [rankdir=TB fontname="Arial" nodesep=0.6 ranksep=0.9 splines=true pad=0.3 bgcolor=white];',
             ' node [fontname="Arial" fontsize=10];', ' edge [fontname="Arial" fontsize=9 dir=both arrowtail=none arrowhead=dot arrowsize=0.8];']
    for e in ENTITIES:
        lines.append(" " + entity_node(*e))
    for parent, child, ident, verb, card, optional in RELATIONS:
        style = "solid" if ident else "dashed"
        tail = "odiamond" if optional else "none"
        lab = f'label="{verb}" headlabel="{card}"' if card else f'label="{verb}"'
        color = ' color="#999999" fontcolor="#777777"' if "CHANNEL_" in child or child == "VEHICLE_CHANNEL" else ""
        lines.append(f' {parent} -> {child} [style={style} arrowtail={tail} {lab}{color}];')
    lines += [' {rank=same; STAFF; VEHICLE_TYPE; LAUNCH_PAD; FUEL_BATCH; TELEMETRY_CHANNEL}',
              ' {rank=same; STAGE_LOG; WEATHER_OBSERVATION; POSTPONEMENT; FUEL_CONSUMPTION; INCIDENT; PAD_MAINTENANCE}',
              ' {rank=same; WEATHER_FORECAST; AUDIT_LOG; CHANNEL_MODE_RATE}',
              ' legend [shape=plain label=<<TABLE BORDER="0" CELLSPACING="4"><TR><TD BGCOLOR="#dcebff" BORDER="1">новая</TD>'
              '<TD BGCOLOR="#fff3cc" BORDER="1">изменена</TD><TD BGCOLOR="#eeeeee" BORDER="1">проект Т2</TD></TR></TABLE>>];',
              '}']
    src = HERE / "idef1x.dot"
    src.write_text("\n".join(lines), encoding="utf-8")
    subprocess.run(["dot", "-Tsvg", str(src), "-o", str(HERE / "idef1x.svg")], check=True)
    subprocess.run(["dot", "-Tpng", "-Gdpi=110", str(src), "-o", str(HERE / "idef1x.png")], check=True)
    print("saved idef1x")


if __name__ == "__main__":
    build()
