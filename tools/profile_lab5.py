"""Стенд профилирования ЛР5: сценарии на базах 1 000 / 10 000 / 50 000 пусков.

Запуск:  python3 tools/profile_lab5.py --label before --dbdir /tmp/lab5
Базы создаются генератором tools/gen_load_data.py (если их нет в --dbdir).

Для каждого сценария и объема:
* время — timeit, минимум из нескольких повторов, мс;
* cProfile — общее число вызовов функций и топ-5 по tottime (первые 20 строк отчета
  сохраняются в docs/lab5/cprofile_<label>_<сценарий>_<N>.txt);
* память — пик по tracemalloc, МБ;
* контрольная сумма ответа (sha256 тела JSON) — по ней проверяется, что после
  оптимизации результат не изменился.

Сценарии вызываются через WebApi.handle, то есть так же, как их вызывает страница:
Controller → сервисы Model → репозитории → SQLite.
"""
from __future__ import annotations

import argparse
import cProfile
import hashlib
import io
import json
import pstats
import sys
import timeit
import tracemalloc
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from lcc.controller.web import WebApi  # noqa: E402
from lcc.model import Database, Repositories, Services  # noqa: E402

sys.path.insert(0, str(ROOT / "tools"))
from gen_load_data import NOW, PASSWORD, generate  # noqa: E402

SIZES = (1_000, 10_000, 50_000)
PERIOD = "from=2021-10-01&to=2026-10-01"


class Bench:
    def __init__(self, db_path: str):
        self.db = Database(db_path)
        self.db.init_schema()
        self.services = Services(Repositories(self.db), lambda: NOW)
        self.api = WebApi(self.services)
        self.token = self._json("POST", "/api/login", {"login": "head", "password": PASSWORD})["token"]

    def _call(self, method, url, body=None):
        headers = {"authorization": f"Bearer {getattr(self, 'token', '')}"}
        resp = self.api.handle(method, url, headers, json.dumps(body).encode() if body else b"")
        if resp.status != 200:
            raise RuntimeError(f"{url}: {resp.status} {resp.body[:200]!r}")
        return resp.body

    def _json(self, method, url, body=None):
        return json.loads(self._call(method, url, body))

    # ------------------------------------------------------------------ сценарии
    def fuel_report(self) -> bytes:
        """С1. Расход топлива по всем партиям: отчет за период + остатки партий на вкладке «Топливо»."""
        return self._call("GET", f"/api/reports/fuel?{PERIOD}") + self._call("GET", "/api/fuel")

    def search_launches(self) -> bytes:
        """С2. Поиск пусков по типу носителя и статусу: «Союз-2.1б», выполненные."""
        rows = [r for r in self._json("GET", f"/api/launches?vehicle_type=2&status=launched")
                if r["vehicle_type_id"] == 2 and r["status"] == "launched"]
        return json.dumps(rows, ensure_ascii=False).encode()

    def pads_report(self) -> bytes:
        """С3. Загрузка стартовых площадок за период."""
        return self._call("GET", f"/api/reports/pads?{PERIOD}")

    def forecast_check(self) -> bytes:
        """Доп. Фоновая проверка «кому нужен прогноз» — таймер сервера вызывает ее раз в минуту."""
        return json.dumps([l.id for l in self.services.forecasts.due()]).encode()

    SCENARIOS = {"fuel_report": "С1 Расход топлива по партиям", "search_launches": "С2 Поиск пусков по типу и статусу",
                 "pads_report": "С3 Загрузка стартовых площадок", "forecast_check": "Доп. Проверка прогнозов (таймер)"}


def measure(bench: Bench, name: str, n: int, label: str, outdir: Path) -> dict:
    fn = getattr(bench, name)
    body = fn()                                                # прогрев + результат для сверки
    repeat = 5 if n <= 10_000 else 3
    t = min(timeit.repeat(fn, number=1, repeat=repeat)) * 1000
    prof = cProfile.Profile()
    prof.enable()
    fn()
    prof.disable()
    stats = pstats.Stats(prof)
    buf = io.StringIO()
    pstats.Stats(prof, stream=buf).sort_stats("tottime").print_stats(20)
    (outdir / f"cprofile_{label}_{name}_{n}.txt").write_text(buf.getvalue(), encoding="utf-8")
    top = sorted(stats.stats.items(), key=lambda kv: kv[1][2], reverse=True)[:5]
    tracemalloc.start()
    fn()
    _, peak = tracemalloc.get_traced_memory()
    tracemalloc.stop()
    return {"scenario": name, "n": n, "ms": round(t, 1), "calls": stats.total_calls,
            "top5": [{"func": f"{Path(k[0]).name}:{k[1]}({k[2]})", "ncalls": v[1], "tottime_ms": round(v[2] * 1000, 1)}
                     for k, v in top],
            "peak_mb": round(peak / 2**20, 2), "sha256": hashlib.sha256(body).hexdigest()[:16], "bytes": len(body)}


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--label", required=True, help="before / after")
    p.add_argument("--dbdir", required=True)
    p.add_argument("--sizes", default=",".join(map(str, SIZES)))
    p.add_argument("--only", default="", help="через запятую: имена сценариев")
    a = p.parse_args()
    outdir = ROOT / "docs" / "lab5"
    outdir.mkdir(parents=True, exist_ok=True)
    names = [s for s in Bench.SCENARIOS if not a.only or s in a.only.split(",")]
    results = []
    for n in map(int, a.sizes.split(",")):
        db = Path(a.dbdir) / f"load_{n}.db"
        if not db.exists():
            generate(str(db), n)
        bench = Bench(str(db))
        for name in names:
            r = measure(bench, name, n, a.label, outdir)
            results.append(r)
            print(f"{Bench.SCENARIOS[name]:36} N={n:>6}: {r['ms']:>9.1f} мс, вызовов {r['calls']:>9}, "
                  f"пик {r['peak_mb']:>7.2f} МБ, sha {r['sha256']}  топ: {r['top5'][0]['func']}", flush=True)
        bench.db.close()
    out = outdir / f"profile_{a.label}.json"
    out.write_text(json.dumps({"label": a.label, "measured_at": datetime.now().isoformat(timespec="minutes"),
                               "python": sys.version.split()[0], "results": results}, ensure_ascii=False, indent=1),
                   encoding="utf-8")
    print("saved", out)


if __name__ == "__main__":
    main()
