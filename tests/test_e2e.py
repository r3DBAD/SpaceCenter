"""Сквозной (end-to-end) тест: пользовательский сценарий через Controller и View до отчёта.

Каждая роль входит в систему через меню (AppController) и выполняет свою часть
подготовки пуска: регистрация → сборка → транспортировка → заправка →
метеоконтроль (с отказом и повтором) → предстартовый контроль → пуск → отчёт в CSV.
"""
import csv
import tempfile
import unittest
from datetime import timedelta
from pathlib import Path

from helpers import PWD, ScriptedView, World
from lcc.controller import AppController
from lcc.model.enums import LaunchStatus
from lcc.view.csv_export import CsvExporter


class EndToEndTest(unittest.TestCase):
    def setUp(self):
        self.w = World()
        self.tmp = tempfile.TemporaryDirectory()
        self.exporter = CsvExporter(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def run_as(self, login, *answers):
        view = ScriptedView([login, PWD, *answers, "Выход"])
        AppController(view, self.w.s, self.exporter).run()
        self.assertEqual(view.answers, [], "не все ответы использованы")
        return view

    def status(self):
        return self.w.s.launches.launch_card(self.w.head, 1).launch.status

    def test_full_launch_scenario(self):
        start = self.w.clock.now + timedelta(days=2)

        # 1. Руководитель регистрирует пуск; сначала с ошибкой (пустая нагрузка), затем корректно
        v = self.run_as("head",
                        "Зарегистрировать пуск", str(self.w.vt.id), str(self.w.pad.id), "", "ССО", start, "",
                        "Зарегистрировать пуск", str(self.w.vt.id), str(self.w.pad.id), "Метеор-М", "ССО", start, "")
        self.assertIn("Поле «Полезная нагрузка» обязательно для заполнения", v.errors)
        self.assertIs(self.status(), LaunchStatus.PLANNED)

        # 2. Инженер ТК: сборка → транспортировка → заправка; меню отчётов ему недоступно
        stage = ("Завершить этап подготовки (сборка, транспортировка, контроль)", "1", "д")
        self.run_as("eng", *stage, *stage, *stage)
        self.assertIs(self.status(), LaunchStatus.FUELING)
        with self.assertRaises(AssertionError):
            self.run_as("eng", "Отчёты")

        # 3. Инженер по заправке: попытка закрыть этап без окислителя → ошибка; затем заправка и закрытие
        v = self.run_as("fuel",
                        "Заправить РН (списать расход)", "1", str(self.w.kero.id), "30",
                        "Завершить заправку", "1", "д",
                        "Заправить РН (списать расход)", "1", str(self.w.lox.id), "70",
                        "Завершить заправку", "1", "д")
        self.assertTrue(any("не заправлено — окислитель" in e for e in v.errors))
        self.assertIs(self.status(), LaunchStatus.PRELAUNCH_CHECK)

        # 4. Метеоролог: шторм → перенос на сутки; затем нормальные условия
        new_start = start + timedelta(days=1)
        v = self.run_as("meteo",
                        "Ввести метеоданные", "1", "22", "35", "5", "1000", "н", "д", new_start, "",
                        "Ввести метеоданные", "1", "4", "12", "8", "1500", "н")
        self.assertTrue(any("ветер у земли 22" in e for e in v.errors))
        self.assertIn("Метеоусловия в норме — допуск к пуску по метео разрешён", v.out)

        # 5. Инженер ТК: предстартовый контроль пройден → «Готов к пуску»
        self.run_as("eng", *stage)
        self.assertIs(self.status(), LaunchStatus.READY)

        # 6. Оператор телеметрии регистрирует НС; руководитель фиксирует пуск и строит отчёты
        self.w.clock.now = new_start + timedelta(minutes=3)
        self.run_as("tm", "Зарегистрировать нештатную ситуацию", "1", "", "Потеря сигнала 2 с", "Резервный канал")
        v = self.run_as("head",
                        "Зафиксировать результат пуска", "1", "д", "12",
                        "Отчёты", "Количество пусков по типам носителей", "01.09.2026", "30.09.2026", "д",
                        "Отчёты", "Нештатные ситуации и переносы", "01.09.2026", "30.09.2026", "н")
        self.assertIs(self.status(), LaunchStatus.LAUNCHED)
        report = [t for t in v.tables if t[0].startswith("Количество пусков")][0]
        self.assertEqual(report[2][0][:4], ["Союз-2.1б", "средний", 1, 1])
        incidents = [t for t in v.tables if t[0].startswith("Нештатные")][0]
        self.assertIn(["Перенос пуска", "Метеоусловия", 1], incidents[2])

        files = list(Path(self.tmp.name).glob("*.csv"))
        self.assertEqual(len(files), 1)
        with files[0].open(encoding="utf-8-sig") as f:
            rows = list(csv.reader(f, delimiter=";"))
        self.assertEqual(rows[0], ["Количество пусков по типам носителей"])

    def test_failed_login(self):
        view = ScriptedView(["head", "bad1", "head", "bad2", "nobody", "x"])
        AppController(view, self.w.s).run()
        self.assertEqual(view.errors[-1], "Превышено число попыток входа")


if __name__ == "__main__":
    unittest.main()
