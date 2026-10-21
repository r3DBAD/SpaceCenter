"""Оценка распараллеливания ЛР5: реальные накладные расходы и прототипы параллельных версий сценариев.

Запуск:  python3 tools/parallel_lab5.py --dbdir /tmp/lab5        (база load_50000.db)

1. Стоимость пула процессов: создание Pool(4) + одна пустая задача + закрытие (fork и spawn).
2. CPU/SQL-сценарии С1–С3 после оптимизации: последовательно и в Pool(4), каждый процесс
   открывает свое соединение SQLite (соединение нельзя передать в другой процесс).
3. I/O-сценарий: запрос прогноза у метеослужбы для 4 пусков, служба отвечает за 0,5 с.
   Последовательно и в ThreadPoolExecutor(4): потоки подходят, потому что во время ожидания
   сокета GIL отпущен.
"""
from __future__ import annotations

import argparse
import json
import multiprocessing as mp
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tools"))

from gen_load_data import NOW  # noqa: E402
from lcc.meteo_stub import start_stub  # noqa: E402
from lcc.model import Database, Repositories  # noqa: E402
from lcc.model.meteo_client import HttpMeteoProvider  # noqa: E402
from lcc.model.reports import FuelUsageReport, PadLoadReport, Period  # noqa: E402

PERIOD = Period(datetime(2021, 10, 1).date(), datetime(2026, 10, 1).date())


def best(fn, repeat=3) -> float:
    times = []
    for _ in range(repeat):
        t = time.perf_counter()
        fn()
        times.append((time.perf_counter() - t) * 1000)
    return round(min(times), 1)


def _noop(_):
    return 0


# ---------------------------------------------------------------- задачи для процессов
def _pads_part(args):
    db_path, spaceport_id = args
    with Database(db_path) as db:
        return PadLoadReport(Repositories(db), NOW, spaceport_id).build(PERIOD).rows


def _fuel_part(args):
    db_path, spaceport_id = args
    with Database(db_path) as db:
        return FuelUsageReport(Repositories(db), NOW, spaceport_id).build(PERIOD).rows


def _search_part(args):
    db_path, lo, hi = args
    with Database(db_path) as db:
        return [tuple(r) for r in db.query("SELECT * FROM launch WHERE vehicle_type_id = 2 AND status = 'launched' "
                                           "AND launch_id BETWEEN ? AND ? ORDER BY window_start", (lo, hi))]


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--dbdir", required=True)
    a = p.parse_args()
    db_path = str(Path(a.dbdir) / "load_50000.db")
    out = {}

    # 1. накладные расходы
    for method in ("fork", "spawn"):
        ctx = mp.get_context(method)
        def pool_cost():
            with ctx.Pool(4) as pool:
                pool.map(_noop, range(4))
        out[f"pool4_{method}_ms"] = best(pool_cost)

    # 2. С1–С3 последовательно и в процессах
    with Database(db_path) as db:
        repos = Repositories(db)
        out["S3_pads_seq_ms"] = best(lambda: PadLoadReport(repos, NOW).build(PERIOD))
        out["S1_fuel_seq_ms"] = best(lambda: FuelUsageReport(repos, NOW).build(PERIOD))
        out["S2_search_seq_ms"] = best(lambda: db.query(
            "SELECT * FROM launch WHERE vehicle_type_id = 2 AND status = 'launched' ORDER BY window_start"))
    ctx = mp.get_context("fork")
    with ctx.Pool(4) as pool:                       # пул уже создан: меряем только работу и передачу данных
        pool.map(_noop, range(4))
        out["S3_pads_pool_ms_warm"] = best(lambda: pool.map(_pads_part, [(db_path, 1), (db_path, 2)]))
        out["S1_fuel_pool_ms_warm"] = best(lambda: pool.map(_fuel_part, [(db_path, 1), (db_path, 2)]))
        ranges = [(db_path, i * 12_500 + 1, (i + 1) * 12_500) for i in range(4)]
        out["S2_search_pool_ms_warm"] = best(lambda: pool.map(_search_part, ranges))

    # 3. I/O: запрос прогноза, служба отвечает за 0,5 с
    server = start_stub(8011, delay=0.5)
    provider = HttpMeteoProvider("http://127.0.0.1:8011", timeout=5)
    sites = [("VOS", NOW), ("PLS", NOW), ("VOS", NOW), ("PLS", NOW)]
    out["IO_forecast_seq_ms"] = best(lambda: [provider.fetch(s, t) for s, t in sites], repeat=2)
    with ThreadPoolExecutor(4) as ex:
        out["IO_forecast_threads_ms"] = best(lambda: list(ex.map(lambda st: provider.fetch(*st), sites)), repeat=2)
    server.shutdown()
    server.server_close()

    path = ROOT / "docs" / "lab5" / "parallel.json"
    path.write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
    for k, v in out.items():
        print(f"{k:28} {v:>9} мс")


if __name__ == "__main__":
    main()
