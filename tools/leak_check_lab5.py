"""Проверка утечек ЛР5: сутки работы сервера на управляемых часах.

Запуск:  python3 tools/leak_check_lab5.py --label before

Модель нагрузки (24 ч, шаг 10 с, 8 640 шагов):
* 50 сотрудников по очереди входят в систему и закрывают вкладку без «Выхода»;
* на каждом шаге — неудачная попытка входа под случайным логином (перебор паролей);
* раз в минуту — запрос расписания.
Считается: размер WebApi._sessions и AuthService._failed, прирост памяти (tracemalloc),
число незакрытых соединений SQLite после прогона тестов.

PBKDF2 на время проверки ослаблен до 1 000 итераций: иначе 8 640 входов шли бы 20 минут.
На размер структур это не влияет.
"""
from __future__ import annotations

import argparse
import inspect
import json
import subprocess
import sys
import tracemalloc
from datetime import datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from lcc.controller.web import WebApi  # noqa: E402
from lcc.model import Database, Repositories, Services  # noqa: E402
from lcc.model.enums import Role  # noqa: E402
from lcc.model.security import PasswordHasher  # noqa: E402

PasswordHasher.ITERATIONS = 1_000
PWD = "leak2026"


class Clock:
    def __init__(self):
        self.now = datetime(2026, 10, 1, 0, 0)

    def __call__(self):
        return self.now


def simulate() -> dict:
    clock = Clock()
    db = Database(":memory:")
    db.init_schema()
    s = Services(Repositories(db), clock)
    s.staff.create_staff(None, "head", "Руководитель", Role.HEAD, PWD)
    head = s.auth.login("head", PWD)
    sp = s.reference.add_spaceport(head, "VOS", "Восточный")
    for i in range(50):
        s.staff.create_staff(head, f"user{i}", f"Сотрудник {i}", Role.PAD_ENGINEER, PWD, sp.id)
    kwargs = {"clock": lambda: clock.now.timestamp()} if "clock" in inspect.signature(WebApi).parameters else {}
    api = WebApi(s, **kwargs)

    def call(method, url, body=None, token=""):
        return api.handle(method, url, {"authorization": f"Bearer {token}"}, json.dumps(body).encode() if body else b"")

    tracemalloc.start()
    base = tracemalloc.take_snapshot()
    sizes = []
    for step in range(8_640):
        clock.now += timedelta(seconds=10)
        resp = call("POST", "/api/login", {"login": f"user{step % 50}", "password": PWD})
        token = json.loads(resp.body)["token"]
        call("POST", "/api/login", {"login": f"guess{step}", "password": "wrong-pass1"})
        if step % 6 == 0:
            call("GET", "/api/launches", token=token)
        if step % 1440 == 1439:
            sizes.append({"hours": (step + 1) // 360, "sessions": len(api._sessions), "failed": len(s.auth._failed)})
    grown = sum(st.size_diff for st in tracemalloc.take_snapshot().compare_to(base, "filename"))
    tracemalloc.stop()
    db.close()
    return {"timeline": sizes, "sessions": len(api._sessions), "failed": len(s.auth._failed),
            "memory_growth_mb": round(grown / 2**20, 2)}


def unclosed_connections() -> int:
    out = subprocess.run([sys.executable, "-W", "always::ResourceWarning", "-m", "unittest", "discover", "-s", "tests"],
                         cwd=ROOT, capture_output=True, text=True)
    return out.stderr.count("unclosed database")


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--label", required=True)
    a = p.parse_args()
    r = simulate()
    r["unclosed_connections_in_tests"] = unclosed_connections()
    out = ROOT / "docs" / "lab5" / f"leaks_{a.label}.json"
    out.write_text(json.dumps(r, ensure_ascii=False, indent=1), encoding="utf-8")
    for row in r["timeline"]:
        print(f"через {row['hours']:>2} ч: сессий {row['sessions']:>5}, записей о неудачных входах {row['failed']:>5}")
    print(f"прирост памяти за сутки: {r['memory_growth_mb']} МБ; незакрытых соединений в тестах: "
          f"{r['unclosed_connections_in_tests']}")


if __name__ == "__main__":
    main()
