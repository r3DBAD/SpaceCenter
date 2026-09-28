"""Генератор диаграмм IDEF0 в формате бланка Ramus/BPwin (Р 50.1.028-2001).

Рисует рамку (шапку и подвал), функциональные блоки, стрелки ICOM с ортогональной
трассировкой и подписи. Координаты задаются вручную — так проще добиться
читаемой раскладки, как в примерах из методических материалов.

Запуск:  python3 idef0.py   →  *.svg и *.png рядом со скриптом.
"""
from __future__ import annotations

import subprocess
from pathlib import Path
from xml.sax.saxutils import escape

W, H = 1280, 860          # размер листа
HEAD, FOOT = 100, 780     # нижняя граница шапки / верхняя граница подвала
FONT = "Arial, 'Liberation Sans', sans-serif"
PROJECT = "АИС «Центр управления космическими пусками»"
AUTHOR = "Группа №3"
DATE = "28.09.2026"


class Diagram:
    def __init__(self, node: str, title: str, number: int, context: str):
        self.node, self.title, self.number, self.context = node, title, number, context
        self.items: list[str] = []

    # ---------- примитивы ----------
    def text(self, x, y, lines, size=12, anchor="start", weight="normal"):
        if isinstance(lines, str):
            lines = lines.split("\n")
        spans = "".join(
            f'<tspan x="{x}" dy="{0 if i == 0 else size + 2}">{escape(t)}</tspan>'
            for i, t in enumerate(lines))
        self.items.append(
            f'<text x="{x}" y="{y}" font-size="{size}" text-anchor="{anchor}" '
            f'font-weight="{weight}" font-family="{FONT}">{spans}</text>')

    def line(self, x1, y1, x2, y2, w=1):
        self.items.append(f'<line x1="{x1}" y1="{y1}" x2="{x2}" y2="{y2}" stroke="#000" stroke-width="{w}"/>')

    def rect(self, x, y, w, h, fill="none", sw=1):
        self.items.append(f'<rect x="{x}" y="{y}" width="{w}" height="{h}" fill="{fill}" stroke="#000" stroke-width="{sw}"/>')

    # ---------- элементы IDEF0 ----------
    def box(self, x, y, w, h, name, num):
        """Функциональный блок: рамка, диагональ-уголок, название, DRE и номер."""
        self.items.append(f'<rect x="{x+3}" y="{y+3}" width="{w}" height="{h}" fill="#bdbdbd"/>')
        self.rect(x, y, w, h, fill="#fff", sw=1.5)
        self.line(x, y + 10, x + 10, y)
        lines = name.split("\n")
        y0 = y + h / 2 - (len(lines) - 1) * 8 + 4
        self.text(x + w / 2, y0, lines, size=13, anchor="middle")
        self.text(x + 5, y + h - 5, "$0", size=10)
        self.text(x + w - 5, y + h - 5, num, size=12, anchor="end")

    def arrow(self, pts, head=True, code=None):
        """Ортогональная стрелка по списку точек; стрелка-наконечник в последней точке.

        code — код ICOM (I1, C2, M1, O1…), печатается у граничного конца стрелки.
        """
        d = " ".join(f"{x},{y}" for x, y in pts)
        self.items.append(f'<polyline points="{d}" fill="none" stroke="#000" stroke-width="1.2"/>')
        if head:
            (x1, y1), (x2, y2) = pts[-2], pts[-1]
            if x1 == x2:
                s = 1 if y2 > y1 else -1
                tri = [(x2, y2), (x2 - 4, y2 - 11 * s), (x2 + 4, y2 - 11 * s)]
            else:
                s = 1 if x2 > x1 else -1
                tri = [(x2, y2), (x2 - 11 * s, y2 - 4), (x2 - 11 * s, y2 + 4)]
            self.items.append('<polygon points="%s" fill="#000"/>' % " ".join(f"{a},{b}" for a, b in tri))
        if code:
            (x0, y0) = pts[0] if code[0] in "ICM" else pts[-1]
            if code[0] == "I":
                self.text(x0 + 3, y0 - 4, code, size=11)
            elif code[0] == "O":
                self.text(x0 - 3, y0 - 4, code, size=11, anchor="end")
            elif code[0] == "C":
                self.text(x0 + 4, y0 + 13, code, size=11)
            else:
                self.text(x0 + 4, y0 - 4, code, size=11)

    def dot(self, x, y):
        """Точка разветвления/слияния стрелок."""
        self.items.append(f'<circle cx="{x}" cy="{y}" r="2.5" fill="#000"/>')

    # ---------- бланк ----------
    def _frame(self):
        f = []
        L = self.line
        self.rect(1, 1, W - 2, H - 2, sw=1.5)
        L(0, HEAD, W, HEAD, 1.5)
        L(0, FOOT, W, FOOT, 1.5)
        for x in (210, 630, 650, 870, 1085):
            L(x, 0, x, HEAD)
        for y in (25, 50, 75):
            L(650, y, 1085, y)
        self.rect(632, 3, 16, 20, fill="#000")
        t = self.text
        t(6, 18, "ИСПОЛЬЗУЕТСЯ В:")
        t(105, 60, "Лабораторная\nработа №3", anchor="middle")
        t(215, 18, f"АВТОР:  {AUTHOR}")
        t(215, 42, "ПРОЕКТ:")
        t(270, 42, ["АИС «Центр управления", "космическими пусками»"])
        t(215, 92, "ЗАМЕЧАНИЯ: 1 2 3 4 5 6 7 8 9 10")
        t(470, 18, f"ДАТА:     {DATE}")
        t(470, 42, f"РЕВИЗИЯ: {DATE}")
        for i, s in enumerate(("РАЗРАБАТЫВАЕТСЯ", "ЧЕРНОВИК", "РЕКОМЕНДОВАНО", "ПУБЛИКАЦИЯ")):
            t(655, 18 + 25 * i, s)
        t(875, 18, "ЧИТАТЕЛЬ")
        t(1080, 18, "ДАТА", anchor="end")
        t(1092, 22, "КОНТЕКСТ:", size=14)
        self._context()
        L(250, FOOT, 250, H)
        L(1020, FOOT, 1020, H)
        t(6, 802, f"Ветка: {self.node}")
        t(256, 802, "Название:")
        t(635, 830, self.title, size=15, anchor="middle")
        t(1026, 802, f"Номер: {self.number}")

    def _context(self):
        c = self.context
        if c == "top":
            self.text(1180, 62, "ВЕРХ", size=14, anchor="middle", weight="bold")
        elif c.startswith("child:"):           # родитель — диаграмма декомпозиции с n блоками
            n, k = map(int, c.split(":")[1].split("/"))
            for i in range(n):
                self.rect(1110 + i * 32, 40 + i * 9, 22, 11, fill="#000" if i + 1 == k else "none")
        else:                                  # декомпозиция контекстного блока
            self.rect(1160, 50, 40, 16, fill="#000")
            self.text(1092, 92, "A-0", size=11)

    def save(self, name: str):
        self._frame()
        svg = (f'<svg xmlns="http://www.w3.org/2000/svg" width="{W}" height="{H}" viewBox="0 0 {W} {H}">'
               f'<rect width="{W}" height="{H}" fill="#fff"/>' + "".join(self.items) + "</svg>")
        out = Path(__file__).with_name(name)
        out.with_suffix(".svg").write_text(svg, encoding="utf-8")
        subprocess.run(["rsvg-convert", "-z", "1.5", "-o", str(out.with_suffix(".png")),
                        str(out.with_suffix(".svg"))], check=True)
        print("saved", out.with_suffix(".png").name)


# =====================================================================
#  A-0. Контекстная диаграмма
# =====================================================================
def context_diagram():
    d = Diagram("A-0", "Автоматизировать управление центром космических пусков", 1, "top")
    bx, by, bw, bh = 440, 330, 400, 230
    d.box(bx, by, bw, bh, "Автоматизировать управление\nцентром космических пусков", "A0")

    ins = ["Заявки на пуск (тип РН, полезная\nнагрузка, орбита, стартовое окно)",
           "Сведения о поступивших\nпартиях топлива",
           "Метеорологические данные (ветер\nна высотах, t°, облачность, гроза)",
           "Телеметрические данные\nпо каналам РН"]
    for i, lbl in enumerate(ins, 1):
        y = 310 + 55 * i
        d.arrow([(0, y), (bx, y)], code=f"I{i}")
        d.text(40, y - 22, lbl)

    ctl = ["Регламент\nподготовки\nи проведения\nпусков", "Предельные\nметеоусловия",
           "Нормативы\nплощадок\n(лимит\nподготовок,\nциклы ТО)", "Правила\nучёта\nтоплива\n(сроки\nгодности)"]
    for i, lbl in enumerate(ctl, 1):
        x = bx + 45 + 105 * (i - 1)
        d.arrow([(x, HEAD), (x, by)], code=f"C{i}")
        d.text(x + 6, 190, lbl)

    mech = [("Персонал ЦУП (руководитель,\nинженеры ТК и заправки,\nметеоролог, оператор ТМ)", 560),
            ("ПО АИС ЦУП\nи СУБД SQLite", 720)]
    for i, (lbl, x) in enumerate(mech, 1):
        d.arrow([(x, FOOT), (x, by + bh)], code=f"M{i}")
    d.text(378, 650, mech[0][0])
    d.text(728, 650, mech[1][0])

    outs = ["Расписание и статусы\nпусков", "Журнал нештатных\nситуаций и переносов",
            "Отчёты руководителю\nцентра"]
    for i, lbl in enumerate(outs, 1):
        y = 320 + 65 * i
        d.arrow([(bx + bw, y), (W, y)], code=f"O{i}")
        d.text(900, y - 22, lbl)

    d.text(20, 730, "Цель: обеспечить учёт подготовки и проведения пусков РН.")
    d.text(20, 750, "Точка зрения: руководитель центра управления пусками.")
    d.save("A-0_context")


# =====================================================================
#  A0. Декомпозиция контекстного блока
# =====================================================================
def decomposition_a0():
    d = Diagram("A0", "Автоматизировать управление центром космических пусков", 2, "a0")
    B = {  # x, y, w, h
        1: (150, 170, 160, 85), 2: (370, 290, 160, 85), 3: (590, 410, 160, 85),
        4: (810, 530, 160, 85), 5: (1040, 200, 150, 90)}
    names = {1: "Зарегистрировать\nи запланировать\nпуск", 2: "Подготовить РН\nк пуску",
             3: "Обеспечить\nметеоконтроль\nи допуск к пуску", 4: "Провести пуск\nи телеметрический\nконтроль",
             5: "Сформировать\nотчётность"}
    for k, (x, y, w, h) in B.items():
        d.box(x, y, w, h, names[k], f"A{k}")

    def top(k): return B[k][1]
    def bot(k): return B[k][1] + B[k][3]
    def left(k): return B[k][0]
    def right(k): return B[k][0] + B[k][2]

    # ---- управление ----
    # C1 Регламент → A1, A2, A4, A5 (шина y=120)
    d.arrow([(190, HEAD), (190, top(1))], code="C1")
    for x, k in ((410, 2), (850, 4), (1080, 5)):
        d.arrow([(190, 120), (x, 120), (x, top(k))])
    d.dot(190, 120)
    d.text(110, 116, "Регламент\nподготовки")
    # C3 Нормативы площадок → A1, A4 (шина y=145)
    d.arrow([(270, HEAD), (270, top(1))], code="C3")
    d.arrow([(270, 145), (890, 145), (890, top(4))])
    d.dot(270, 145)
    d.text(276, 162, "Нормативы площадок")
    # C4 Правила учёта топлива → A2
    d.arrow([(470, HEAD), (470, top(2))], code="C4")
    d.text(476, 262, "Правила учёта\nтоплива")
    # C2 Предельные метеоусловия → A3
    d.arrow([(690, HEAD), (690, top(3))], code="C2")
    d.text(696, 384, "Предельные\nметеоусловия")

    # ---- входы ----
    d.arrow([(0, 195), (left(1), 195)], code="I1")
    d.text(20, 190, "Заявки на пуск")
    d.arrow([(0, 345), (left(2), 345)], code="I2")
    d.text(225, 340, "Партии топлива")
    d.arrow([(0, 465), (left(3), 465)], code="I3")
    d.text(280, 460, "Метеоданные")
    d.arrow([(0, 590), (left(4), 590)], code="I4")
    d.text(540, 585, "Телеметрия")

    # ---- механизмы (шины y=740 и y=760) ----
    d.arrow([(60, FOOT), (60, 740), (1180, 740), (1180, bot(5))], head=False, code="M1")
    for k in (1, 2, 3, 4, 5):
        x = left(k) + 45 if k != 5 else 1085
        d.arrow([(x, 740), (x, bot(k))])
        d.dot(x, 740)
    d.arrow([(90, FOOT), (90, 760), (1150, 760)], head=False, code="M2")
    for k in (1, 2, 3, 4, 5):
        x = right(k) - 40 if k != 5 else 1150
        d.arrow([(x, 760), (x, bot(k))])
        if k != 5:
            d.dot(x, 760)
    d.text(66, 736, "Персонал ЦУП")
    d.text(200, 756, "ПО АИС и СУБД")

    # ---- интерконнекты ----
    # A1 → O1 Расписание пусков (+ ответвление в A5)
    d.arrow([(right(1), 180), (W, 180)], code="O1")
    d.text(1195, 146, "Расписание\nи статусы\nпусков", size=11)
    d.arrow([(1020, 180), (1020, 220), (left(5), 220)])
    d.dot(1020, 180)
    # A1 → A2 Зарегистрированный пуск
    d.arrow([(right(1), 225), (345, 225), (345, 320), (left(2), 320)])
    d.text(350, 250, "Зарегистри-\nрованный\nпуск", size=11)
    # A2 → A3 РН готова к пуску
    d.arrow([(right(2), 335), (565, 335), (565, 440), (left(3), 440)])
    d.text(570, 390, "Заправленная\nРН", size=11)
    # A2 → A5 Сведения о расходе топлива
    d.arrow([(right(2), 360), (545, 360), (545, 240), (left(5), 240)])
    d.text(895, 234, "Сведения о расходе топлива", size=10)
    # A3 → A4 Разрешение на пуск
    d.arrow([(right(3), 430), (785, 430), (785, 560), (left(4), 560)])
    d.text(790, 470, "Разрешение\nна пуск", size=11)
    # A3 → перенос: в A5 и обратная связь в A1 (по входу, снизу)
    d.arrow([(right(3), 475), (760, 475), (760, 260), (left(5), 260)])
    d.dot(760, 475)
    d.text(900, 256, "Решения о переносе", size=11)
    d.arrow([(760, 475), (760, 680), (120, 680), (120, 240), (left(1), 240)])
    d.text(130, 675, "Решение о переносе пуска (новое стартовое окно)", size=11)
    # A4 → A5 Результаты пуска
    d.arrow([(right(4), 555), (1000, 555), (1000, 275), (left(5), 275)])
    d.text(1004, 330, "Результаты\nпуска,\nнаработка\nплощадки", size=11)
    # A4 → O2 Журнал НС
    d.arrow([(right(4), 600), (W, 600)], code="O2")
    d.text(1040, 595, "Журнал нештатных ситуаций", size=11)
    # A5 → O3 Отчёты
    d.arrow([(right(5), 250), (W, 250)], code="O3")
    d.text(1196, 243, "Отчёты", size=11)
    d.save("A0_decomposition")


# =====================================================================
#  A2. Декомпозиция блока «Подготовить РН к пуску»
# =====================================================================
def decomposition_a2():
    d = Diagram("A2", "Подготовить РН к пуску", 3, "child:5/2")
    B = {1: (170, 170, 170, 85), 2: (420, 290, 170, 85), 3: (670, 410, 170, 90), 4: (930, 540, 170, 85)}
    names = {1: "Собрать РН\nна техническом\nкомплексе", 2: "Транспортировать\nРН на стартовый\nкомплекс",
             3: "Заправить РН\nкомпонентами\nтоплива", 4: "Провести\nпредстартовый\nконтроль систем"}
    for k, (x, y, w, h) in B.items():
        d.box(x, y, w, h, names[k], f"A2{k}")

    def top(k): return B[k][1]
    def bot(k): return B[k][1] + B[k][3]
    def left(k): return B[k][0]
    def right(k): return B[k][0] + B[k][2]

    # управление: C1 регламент → все блоки, C4 правила учёта топлива → A23
    d.arrow([(215, HEAD), (215, top(1))], code="C1")
    for x, k in ((465, 2), (715, 3), (975, 4)):
        d.arrow([(215, 130), (x, 130), (x, top(k))])
    d.dot(215, 130)
    d.text(222, 124, "Регламент подготовки и проведения пусков")
    d.arrow([(790, HEAD), (790, top(3))], code="C4")
    d.text(796, 250, "Правила учёта\nтоплива (сроки\nгодности)")

    # входы
    d.arrow([(0, 205), (left(1), 205)], code="I1")
    d.text(20, 198, "Зарегистрированный пуск")
    d.arrow([(0, 470), (left(3), 470)], code="I2")
    d.text(400, 464, "Партии топлива")

    # механизмы
    d.arrow([(60, FOOT), (60, 740), (1020, 740)], head=False, code="M1")
    d.arrow([(90, FOOT), (90, 760), (1060, 760)], head=False, code="M2")
    for k in (1, 2, 3, 4):
        d.arrow([(left(k) + 50, 740), (left(k) + 50, bot(k))]); d.dot(left(k) + 50, 740)
        d.arrow([(right(k) - 50, 760), (right(k) - 50, bot(k))]); d.dot(right(k) - 50, 760)
    d.text(66, 736, "Персонал ЦУП (инженеры ТК и заправки)")
    d.text(200, 756, "ПО АИС и СУБД")

    # интерконнекты
    d.arrow([(right(1), 215), (380, 215), (380, 330), (left(2), 330)])
    d.text(345, 208, "Собранная РН", size=11)
    d.arrow([(right(2), 335), (630, 335), (630, 440), (left(3), 440)])
    d.text(596, 328, "РН на стартовом\nкомплексе", size=11)
    d.arrow([(right(3), 440), (890, 440), (890, 575), (left(4), 575)])
    d.text(850, 432, "Заправленная РН", size=11)
    d.arrow([(right(3), 480), (W, 480)], code="O2")
    d.text(1030, 474, "Сведения о расходе топлива", size=11)
    d.arrow([(right(4), 580), (W, 580)], code="O1")
    d.text(1140, 574, "РН готова к пуску", size=11)
    # обратная связь: замечания предстартового контроля → на доработку в A21
    d.arrow([(right(4), 610), (1130, 610), (1130, 700), (130, 700), (130, 235), (left(1), 235)])
    d.text(140, 695, "Замечания предстартового контроля (возврат на доработку)", size=11)
    d.save("A2_decomposition")


if __name__ == "__main__":
    context_diagram()
    decomposition_a0()
    decomposition_a2()
