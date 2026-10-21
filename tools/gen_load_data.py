"""Генератор нагрузочных данных для ЛР5: N пусков с полной историей в формате базы системы (SQLite, схема v2).

Запуск:  python3 tools/gen_load_data.py --launches 10000 --db load_10k.db

Что создается (на N пусков):
* 2 космодрома, 6 площадок, 4 типа РН, 8 сотрудников, 12 каналов телеметрии;
* пуски за последние 5 лет: 85 % выполнено, 3 % авария, 10 % отменено, остальное в подготовке;
* журнал этапов (до 7 записей на пуск), расход топлива (2–4 списания на пуск),
  партии топлива (одна партия на ~25 пусков), метеонаблюдения, НС (~20 %), переносы (~30 %).

Данные пишутся прямо в таблицы пакетными INSERT, без сервисов Model: так 50 000 пусков создаются
за секунды. Правила Model (лимит площадки, сроки партий) при этом не проверяются, это нагрузочные,
а не демонстрационные данные. Генератор детерминирован (seed), поэтому замеры повторяемы.
"""
from __future__ import annotations

import argparse
import random
import sys
from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from lcc.model import Database  # noqa: E402
from lcc.model.database import dt_to_db  # noqa: E402
from lcc.model.security import PasswordHasher  # noqa: E402

PASSWORD = "load2026"
NOW = datetime(2026, 10, 1, 8, 0)
FLOW = ["planned", "assembly", "transport", "fueling", "prelaunch_check", "ready"]


def generate(path: str, n_launches: int, seed: int = 42) -> dict:
    rnd = random.Random(seed)
    if Path(path).exists():
        Path(path).unlink()
    db = Database(path)
    db.init_schema()
    c = db._conn                                    # пакетная вставка напрямую
    c.executemany("INSERT INTO spaceport(spaceport_id, code, name, region) VALUES (?,?,?,?)",
                  [(1, "VOS", "Восточный", "Амурская область"), (2, "PLS", "Плесецк", "Архангельская область")])
    pw_hash, salt = PasswordHasher.hash(PASSWORD)
    staff = [(1, "head", "Руководитель центра", "head", None), (2, "director", "Руководитель пуска", "launch_director", 1),
             (3, "director2", "Руководитель пуска 2", "launch_director", 2), (4, "engineer", "Инженер ТК", "pad_engineer", 1),
             (5, "fuel", "Инженер по заправке", "fuel_engineer", 1), (6, "meteo", "Метеоролог", "meteorologist", 1),
             (7, "telemetry", "Оператор ТМ", "telemetry_operator", 1), (8, "inspector", "Инспектор", "safety_inspector", None)]
    c.executemany("INSERT INTO staff(staff_id, login, full_name, role, password_hash, password_salt, is_active, "
                  "spaceport_id) VALUES (?,?,?,?,?,?,1,?)", [(i, l, n, r, pw_hash, salt, sp) for i, l, n, r, sp in staff])
    vtypes = [(1, "Союз-2.1а", "medium", 3), (2, "Союз-2.1б", "medium", 3), (3, "Союз-2.1в", "light", 2),
              (4, "Ангара-А5", "heavy", 3)]
    c.executemany("INSERT INTO vehicle_type VALUES (?,?,?,?)", vtypes)
    pads = [(1, 1, "SK-1", "Площадка 1С", 3, 100, 5000.0), (2, 1, "SK-2", "Площадка 1А", 3, 100, 5000.0),
            (3, 1, "SK-3", "Площадка 1Б", 2, 100, 5000.0), (4, 2, "SK-43", "Площадка 43", 3, 100, 5000.0),
            (5, 2, "SK-35", "Площадка 35", 2, 100, 5000.0), (6, 2, "SK-16", "Площадка 16", 2, 100, 5000.0)]
    c.executemany("INSERT INTO launch_pad(pad_id, spaceport_id, code, name, max_concurrent, cycle_limit, hours_limit, "
                  "is_faulty) VALUES (?,?,?,?,?,?,?,0)", pads)
    c.executemany("INSERT INTO telemetry_channel VALUES (?,?,?,?,?)",
                  [(i, f"TM-{i:02d}", rnd.choice(["ДУ", "СУ", "СЭС", "СОТР"]), "параметр", 10.0 * i) for i in range(1, 13)])

    start_all = NOW - timedelta(days=5 * 365)
    span = (NOW - start_all).total_seconds()
    n_batches = max(4, n_launches // 25)
    batches = []
    for b in range(1, n_batches + 1):
        produced = start_all + timedelta(seconds=span * (b - 1) / n_batches) - timedelta(days=30)
        batches.append((b, 1 if b % 2 else 2, f"B-{b:06d}", "fuel" if b % 4 < 2 else "oxidizer",
                        "РГ-1" if b % 4 < 2 else "Жидкий кислород", 49_000_000, produced.date().isoformat(),
                        (produced + timedelta(days=400)).date().isoformat()))
    c.executemany("INSERT INTO fuel_batch(batch_id, spaceport_id, batch_no, component, grade, initial_mass_hg, "
                  "produced_on, expires_on) VALUES (?,?,?,?,?,?,?,?)", batches)

    launches, stages, consumption, weather, incidents, postpones = [], [], [], [], [], []
    for lid in range(1, n_launches + 1):
        pad = pads[rnd.randrange(len(pads))]
        when = start_all + timedelta(seconds=span * lid / (n_launches + 1))
        if lid > n_launches - 20:                       # хвост — текущие подготовки и планы
            status = FLOW[lid % len(FLOW)]
            when = NOW + timedelta(days=lid % 30 + 1)
        else:
            roll = rnd.random()
            status = "launched" if roll < 0.85 else "failed" if roll < 0.88 else "cancelled"
        w_start, w_end = when, when + timedelta(minutes=rnd.choice([20, 30, 60, 120]))
        steps = FLOW[:FLOW.index(status) + 1] if status in FLOW else FLOW + [status] if status != "cancelled" \
            else FLOW[:rnd.randrange(1, 5)] + ["cancelled"]
        t = w_start - timedelta(days=12)
        for seq, st in enumerate(steps, 1):
            stages.append((lid, seq, st, dt_to_db(min(t, w_end)), 4))
            t += timedelta(days=2, hours=rnd.randrange(0, 12))
        launches.append((lid, rnd.randrange(1, 5), pad[0], 2 if pad[1] == 1 else 3, f"КА-{lid}", "ССО",
                         dt_to_db(w_start), dt_to_db(w_end), status,
                         rnd.choice([20.0, 30.0, 40.0]) if status in ("launched", "failed") else None,
                         "Отмена по решению комиссии" if status == "cancelled" else ""))
        if "fueling" in steps:
            same = [b for b in batches[max(0, lid // 25 - 2): lid // 25 + 2] if b[1] == pad[1]] or batches[:2]
            for k in range(rnd.randrange(2, 5)):
                b = same[k % len(same)]
                consumption.append((lid, b[0], rnd.randrange(100_000, 900_000), dt_to_db(w_start - timedelta(days=2)), 5))
            weather.append((lid, 1, dt_to_db(w_start - timedelta(hours=2)), 6.0, 18.0, 5.0, 1000.0, 0, 6))
        if rnd.random() < 0.2 and status != "planned":
            incidents.append((lid, rnd.randrange(1, 13), dt_to_db(w_start + timedelta(minutes=3)),
                              "Потеря сигнала", "Резервный канал", 7))
        if rnd.random() < 0.3:
            shift = timedelta(days=rnd.choice([1, 2, 3, 7]))
            postpones.append((lid, 1, rnd.choice(["weather", "technical", "organizational"]),
                              dt_to_db(w_start - shift), dt_to_db(w_end - shift), dt_to_db(w_start), dt_to_db(w_end),
                              "", 2, dt_to_db(w_start - shift - timedelta(days=1))))
    c.executemany("INSERT INTO launch(launch_id, vehicle_type_id, pad_id, created_by, payload, target_orbit, "
                  "window_start, window_end, status, pad_hours, cancel_reason) VALUES (?,?,?,?,?,?,?,?,?,?,?)", launches)
    c.executemany("INSERT INTO stage_log VALUES (?,?,?,?,?)", stages)
    c.executemany("INSERT INTO fuel_consumption(launch_id, batch_id, mass_hg, consumed_at, staff_id) VALUES (?,?,?,?,?)",
                  consumption)
    c.executemany("INSERT INTO weather_observation VALUES (?,?,?,?,?,?,?,?,?)", weather)
    c.executemany("INSERT INTO incident(launch_id, channel_id, occurred_at, description, measures, staff_id) "
                  "VALUES (?,?,?,?,?,?)", incidents)
    c.executemany("INSERT INTO postponement VALUES (?,?,?,?,?,?,?,?,?,?)", postpones)
    c.commit()
    counts = {t: db.scalar(f"SELECT COUNT(*) FROM {t}") for t in
              ("launch", "stage_log", "fuel_batch", "fuel_consumption", "weather_observation", "incident", "postponement")}
    db.close()
    return counts


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--launches", type=int, default=10_000)
    p.add_argument("--db", default="load.db")
    p.add_argument("--seed", type=int, default=42)
    a = p.parse_args()
    counts = generate(a.db, a.launches, a.seed)
    print(f"{a.db}: " + ", ".join(f"{k} {v}" for k, v in counts.items()) + f"; всего строк {sum(counts.values())}")


if __name__ == "__main__":
    main()
