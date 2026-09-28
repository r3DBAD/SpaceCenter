"""Генератор DFD в нотации Гейна–Сарсона (Graphviz).

* внешняя сущность — прямоугольник с «тенью» (двойная левая/верхняя грань);
* процесс — скруглённый прямоугольник с полосой номера сверху;
* хранилище данных — прямоугольник, открытый справа, с идентификатором Dn.

Запуск:  python3 dfd.py  →  dfd_context.(svg|png), dfd_level1.(svg|png)
"""
import subprocess
from pathlib import Path

HERE = Path(__file__).parent
FONT = "Arial"


def ext(nid, label):
    return (f'{nid} [shape=plain label=<<TABLE BORDER="0" CELLBORDER="1" CELLSPACING="0" CELLPADDING="10" BGCOLOR="#EDEDED">'
            f'<TR><TD BORDER="3" SIDES="LT" WIDTH="150">{label}</TD></TR></TABLE>>];')


def proc(nid, num, label):
    return (f'{nid} [shape=plain label=<<TABLE BORDER="1.5" STYLE="ROUNDED" CELLBORDER="0" CELLSPACING="0" CELLPADDING="6" BGCOLOR="white">'
            f'<TR><TD BORDER="1" SIDES="B" ALIGN="LEFT"><B>{num}</B></TD></TR>'
            f'<TR><TD WIDTH="140" HEIGHT="50">{label}</TD></TR></TABLE>>];')


def store(nid, num, label):
    return (f'{nid} [shape=plain label=<<TABLE BORDER="0" CELLBORDER="1" CELLSPACING="0" CELLPADDING="6" BGCOLOR="white">'
            f'<TR><TD SIDES="LTB" WIDTH="34"><B>{num}</B></TD><TD SIDES="TB" WIDTH="170" ALIGN="LEFT">{label}</TD></TR></TABLE>>];')


def render(name, body, rankdir="LR", engine="dot"):
    dot = (f'digraph {name} {{\n graph [rankdir={rankdir} fontname="{FONT}" nodesep=0.5 ranksep=1.0 splines=true overlap=false pad=0.3 bgcolor=white];\n'
           f' node [fontname="{FONT}" fontsize=11];\n edge [fontname="{FONT}" fontsize=9 arrowsize=0.7];\n'
           + "\n".join(" " + b for b in body) + "\n}\n")
    src = HERE / f"{name}.dot"
    src.write_text(dot, encoding="utf-8")
    for fmt in ("svg", "png"):
        subprocess.run([engine, "-n2" if engine == "neato" else "-q", f"-T{fmt}", "-Gdpi=110" if fmt == "png" else "-Gdpi=72",
                        str(src), "-o", str(HERE / f"{name}.{fmt}")], check=True)
    print("saved", name)


E = {
    "HEAD": "Руководитель центра",
    "ENG": "Инженер ТК",
    "FUEL": "Инженер по заправке",
    "MET": "Метеоролог",
    "TM": "Оператор телеметрии",
    "SUP": "Поставщик топлива",
    "RN": "Бортовые системы РН<br/>(телеметрия)",
}


def context():
    b = [ext(k, v) for k, v in E.items()]
    b.append(proc("P0", "0", "АИС «Центр управления<br/>космическими пусками»"))
    b += [
        'HEAD -> P0 [label="заявка на пуск,\\nрезультат пуска,\\nзапрос отчёта"];',
        'P0 -> HEAD [label="расписание пусков,\\nотчёты"];',
        'ENG -> P0 [label="отметки этапов,\\nакты ТО площадок"];',
        'SUP -> FUEL [label="накладная\\nна партию" style=dashed];',
        'FUEL -> P0 [label="данные партии,\\nрасход при заправке"];',
        'P0 -> FUEL [label="остатки и сроки\\nгодности партий"];',
        'MET -> P0 [label="метеонаблюдения"];',
        'P0 -> MET [label="решение: допуск /\\nперенос"];',
        'RN -> TM [label="сигналы ТМ-каналов" style=dashed];',
        'TM -> P0 [label="потеря сигнала,\\nпринятые меры"];',
        'P0 -> TM [label="список каналов"];',
    ]
    render("dfd_context", b)


def ext_dup(nid, label):
    """Дубликат внешней сущности: помечается косой чертой в левом нижнем углу."""
    return (f'{nid} [shape=plain label=<<TABLE BORDER="0" CELLBORDER="1" CELLSPACING="0" CELLPADDING="10" BGCOLOR="#EDEDED">'
            f'<TR><TD BORDER="3" SIDES="LT" WIDTH="150">{label} <FONT POINT-SIZE="9">(дубль) /</FONT></TD></TR></TABLE>>];')


def level1():
    b = [ext("HEAD", E["HEAD"]), ext_dup("HEAD2", E["HEAD"]),
         ext("ENG", E["ENG"]), ext("FUEL", E["FUEL"]), ext("MET", E["MET"]), ext("TM", E["TM"])]
    b += [
        proc("P1", "1", "Зарегистрировать<br/>и запланировать пуск"),
        proc("P2", "2", "Подготовить РН<br/>к пуску"),
        proc("P3", "3", "Обеспечить метеоконтроль<br/>и допуск к пуску"),
        proc("P4", "4", "Провести пуск<br/>и телеметрический контроль"),
        proc("P5", "5", "Сформировать<br/>отчётность"),
        store("D1", "D1", "Пуски"),
        store("D2", "D2", "Журнал этапов (статусов)"),
        store("D3", "D3", "Стартовые площадки и ТО"),
        store("D4", "D4", "Партии и расход топлива"),
        store("D5", "D5", "Метеонаблюдения"),
        store("D6", "D6", "Каналы телеметрии"),
        store("D7", "D7", "Журнал НС и переносов"),
        store("D8", "D8", "Персонал и роли"),
        # 1
        'HEAD -> P1 [label="заявка на пуск"];',
        'P1 -> HEAD [label="подтверждение,\\nрасписание"];',
        'D3 -> P1 [label="лимит подготовок,\\nсостояние"];',
        'D8 -> P1 [label="роль" style=dotted];',
        'P1 -> D1 [label="новый пуск"];',
        'P1 -> D2 [label="«Запланирован»"];',
        # 2
        'ENG -> P2 [label="отметка этапа"];',
        'FUEL -> P2 [label="партия, объём"];',
        'D1 -> P2 [label="пуск, статус"];',
        'D4 -> P2 [label="срок годности,\\nостаток"];',
        'P2 -> D4 [label="расход"];',
        'P2 -> D2 [label="переход\\nстатуса"];',
        # 3
        'MET -> P3 [label="метеонаблюдение"];',
        'P3 -> MET [label="допуск /\\nнарушения"];',
        'D1 -> P3 [label="стартовое окно"];',
        'P3 -> D5 [label="наблюдение"];',
        'P3 -> D7 [label="перенос (метео)"];',
        'P3 -> D1 [label="новое окно /\\n«Готов к пуску»"];',
        # 4
        'TM -> P4 [label="потеря сигнала,\\nмеры"];',
        'D6 -> P4 [label="канал"];',
        'HEAD2 -> P4 [label="результат пуска"];',
        'P4 -> D7 [label="НС"];',
        'P4 -> D1 [label="«Выполнен» /\\n«Авария»"];',
        'P4 -> D3 [label="цикл +1,\\nпризнак ТО"];',
        # 5
        'HEAD2 -> P5 [label="период,\\nвид отчёта"];',
        'D1 -> P5 [label="пуски"]; D2 -> P5 [label="этапы"]; D3 -> P5 [label="площадки"];',
        'D4 -> P5 [label="расход"]; D7 -> P5 [label="НС, переносы"];',
        'P5 -> HEAD2 [label="отчёт\\n(таблица / CSV)"];',
    ]
    pos = {"HEAD": (120, 760), "D8": (120, 640), "P1": (420, 700), "D3": (780, 760),
           "D1": (780, 570), "ENG": (100, 470), "FUEL": (100, 360), "P2": (420, 440),
           "D2": (560, 300), "D4": (300, 250), "MET": (1260, 640), "P3": (1120, 480),
           "D5": (1400, 480), "P4": (1120, 250), "TM": (1420, 330), "D6": (1420, 170),
           "D7": (860, 170), "P5": (560, 60), "HEAD2": (160, 60)}
    b = [x.replace("];", f' pos="{pos[x.split()[0]][0]},{pos[x.split()[0]][1]}!"];', 1)
         if x.split()[0] in pos and "shape=plain" in x else x for x in b]
    render("dfd_level1", b, rankdir="TB", engine="neato")


if __name__ == "__main__":
    context()
    level1()
