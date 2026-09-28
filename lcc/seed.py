"""Демонстрационные данные: справочники, сотрудники всех ролей и история пусков.

Используется только для демо-базы (``python -m lcc --demo``). Пароли демо-пользователей
одинаковые и известные — в рабочей базе так делать нельзя (см. docs/06_audit.md).
"""
from __future__ import annotations

from datetime import date, datetime, timedelta

from .model import Database, Repositories, Services
from .model.enums import FuelComponent, MaintenanceKind, PostponeReason, Role, VehicleClass

DEMO_PASSWORD = "demo2026"

DEMO_USERS = [
    ("head", "Королёв Сергей Павлович", Role.HEAD),
    ("engineer", "Бармин Владимир Павлович", Role.PAD_ENGINEER),
    ("fuel", "Глушко Валентин Петрович", Role.FUEL_ENGINEER),
    ("meteo", "Федорова Анна Игоревна", Role.METEOROLOGIST),
    ("telemetry", "Пилюгин Николай Алексеевич", Role.TELEMETRY_OPERATOR),
]


class _Clock:
    """Управляемые часы: история пусков создаётся «в прошлом» через обычные сервисы Model."""

    def __init__(self, start: datetime):
        self.now = start

    def __call__(self) -> datetime:
        return self.now

    def advance(self, **kw) -> None:
        self.now += timedelta(**kw)


def seed_demo(db_path: str, today: datetime | None = None) -> None:
    """Создать демо-базу. История строится только через сервисы — все правила Model соблюдаются."""
    today = (today or datetime.now()).replace(second=0, microsecond=0)
    clock = _Clock(today - timedelta(days=40))
    db = Database(db_path)
    db.init_schema()
    s = Services(Repositories(db), clock)

    s.staff.create_staff(None, "head", DEMO_USERS[0][1], Role.HEAD, DEMO_PASSWORD)
    head = s.auth.login("head", DEMO_PASSWORD)
    for login, name, role in DEMO_USERS[1:]:
        s.staff.create_staff(head, login, name, role, DEMO_PASSWORD)
    eng, fuel, meteo, tm = (s.auth.login(u[0], DEMO_PASSWORD) for u in DEMO_USERS[1:])

    soyuz = s.reference.add_vehicle_type(head, "Союз-2.1б", VehicleClass.MEDIUM, 3)
    angara = s.reference.add_vehicle_type(head, "Ангара-А5", VehicleClass.HEAVY, 3)
    s.reference.add_vehicle_type(head, "Союз-2.1в", VehicleClass.LIGHT, 2)
    pad1 = s.reference.add_pad(head, "SK-1", "Стартовый комплекс №1 (площадка 31)", 1, 3, 150.0)
    pad2 = s.reference.add_pad(head, "SK-2", "Универсальный стартовый комплекс (площадка 1А)", 2, 5, 250.0)
    ch1 = s.reference.add_channel(tm, "TM-01", "ДУ (двигательная установка)", "давление в камере сгорания", 100)
    s.reference.add_channel(tm, "TM-02", "СУ (система управления)", "угловая скорость по тангажу", 50)
    ch3 = s.reference.add_channel(tm, "TM-03", "СЭС (электроснабжение)", "напряжение бортовой сети", 10)

    d = clock.now.date()
    kerosene = s.fuel.receive_batch(fuel, "RG1-2026-014", FuelComponent.FUEL, "РГ-1 (нафтил)", 180.0,
                                    d - timedelta(days=60), d + timedelta(days=300))
    lox = s.fuel.receive_batch(fuel, "LOX-2026-031", FuelComponent.OXIDIZER, "Жидкий кислород", 450.0,
                               d - timedelta(days=5), d + timedelta(days=90))
    s.fuel.receive_batch(fuel, "RG1-2026-002", FuelComponent.FUEL, "РГ-1 (нафтил)", 60.0,
                         d - timedelta(days=200), d + timedelta(days=45))

    def prepare(launch, fuel_t, lox_t, weather_ok=True):
        """Пройти все этапы: сборка → транспортировка → заправка → контроль → готов."""
        for _ in range(2):                                   # PLANNED→ASSEMBLY, ASSEMBLY→TRANSPORT
            clock.advance(days=2)
            s.launches.advance_stage(eng, launch.id)
        clock.advance(hours=20)
        s.launches.advance_stage(eng, launch.id)             # TRANSPORT→FUELING
        s.fuel.refuel(fuel, launch.id, kerosene.id, fuel_t)
        s.fuel.refuel(fuel, launch.id, lox.id, lox_t)
        clock.advance(hours=4)
        s.launches.advance_stage(fuel, launch.id)            # FUELING→PRELAUNCH_CHECK
        clock.advance(hours=2)
        if not weather_ok:
            dec = s.weather.record_observation(meteo, launch.id, 19.0, 34.0, -4.0, 250.0, False)
            s.launches.postpone(meteo, launch.id, PostponeReason.WEATHER, launch.window.start + timedelta(days=1),
                                launch.window.end + timedelta(days=1), "; ".join(dec.reasons))
            clock.advance(days=1)
        s.weather.record_observation(meteo, launch.id, 6.0, 18.0, 2.0, 1200.0, False)
        s.launches.advance_stage(eng, launch.id)             # PRELAUNCH_CHECK→READY

    # Пуск 1: Союз-2.1б, выполнен успешно
    t0 = clock.now + timedelta(days=6)
    l1 = s.launches.register_launch(head, soyuz.id, pad1.id, "Метеор-М №2-5", "ССО 830 км", t0, t0 + timedelta(minutes=30))
    prepare(l1, 38.0, 95.0)
    clock.now = l1.window.start + timedelta(minutes=5)
    s.telemetry.register_incident(tm, l1.id, ch3.id, "Кратковременная потеря сигнала 1,2 с на 312-й секунде",
                                  "Переход на резервный приёмный пункт, данные восстановлены по записи")
    s.launches.record_result(head, l1.id, True, 36.0)

    # ТО площадки SK-1 после пуска
    clock.advance(days=1)
    s.pads.open_maintenance(eng, pad1.id, MaintenanceKind.INSPECTION, "Осмотр газоотводного канала")
    clock.advance(days=2)
    s.pads.close_maintenance(eng, pad1.id)

    # Пуск 2: Ангара-А5, перенос по метео, затем успешный пуск
    t1 = clock.now + timedelta(days=6)
    l2 = s.launches.register_launch(head, angara.id, pad2.id, "Луч-5Х", "ГСО", t1, t1 + timedelta(hours=1))
    prepare(l2, 55.0, 140.0, weather_ok=False)
    l2 = s.launches.launch_card(head, l2.id).launch        # окно изменилось после переноса
    clock.now = l2.window.start + timedelta(minutes=10)
    s.telemetry.register_incident(tm, l2.id, ch1.id, "Потеря сигнала канала ДУ на 3,5 с",
                                  "Анализ по резервному каналу, параметры в норме")
    s.launches.record_result(head, l2.id, True, 48.0)

    # Пуск 3: Союз-2.1б, на этапе заправки (текущая подготовка)
    clock.now = today - timedelta(days=5)
    t2 = today + timedelta(days=3)
    l3 = s.launches.register_launch(head, soyuz.id, pad2.id, "Прогресс МС-33", "НОО 410 км", t2,
                                    t2 + timedelta(minutes=1))
    for _ in range(3):
        clock.advance(days=1)
        s.launches.advance_stage(eng, l3.id)
    clock.advance(hours=3)
    s.fuel.refuel(fuel, l3.id, kerosene.id, 30.0)

    # Пуск 4: запланирован; Пуск 5: отменён
    clock.now = today - timedelta(days=1)
    t3 = today + timedelta(days=14)
    s.launches.register_launch(head, angara.id, pad1.id, "Экспресс-АМУ4", "ГСО", t3, t3 + timedelta(hours=2))
    l5 = s.launches.register_launch(head, soyuz.id, pad1.id, "Кондор-ФКА №3", "ССО 510 км",
                                    t3 + timedelta(days=5), t3 + timedelta(days=5, hours=1))
    s.launches.cancel(head, l5.id, "Задержка поставки полезной нагрузки")
    db.close()
