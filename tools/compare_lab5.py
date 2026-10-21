"""Сравнение замеров ЛР5: python3 tools/compare_lab5.py before after → таблица и проверка, что ответы совпали."""
import json
import sys
from pathlib import Path

D = Path(__file__).resolve().parents[1] / "docs" / "lab5"


def load(label):
    return {(r["scenario"], r["n"]): r for r in json.loads((D / f"profile_{label}.json").read_text())["results"]}


def main(a, b):
    A, B = load(a), load(b)
    ok = True
    print(f"| Сценарий | N | Время {a}, мс | Время {b}, мс | Изм. | Пик {a}, МБ | Пик {b}, МБ | Вызовов {a} | Вызовов {b} | Ответ |")
    print("|---|---|---|---|---|---|---|---|---|---|")
    for key in sorted(B, key=lambda k: (k[0], k[1])):
        if key not in A:
            continue
        x, y = A[key], B[key]
        same = x["sha256"] == y["sha256"]
        ok &= same
        print(f"| {key[0]} | {key[1]} | {x['ms']} | {y['ms']} | {100 * (y['ms'] - x['ms']) / x['ms']:+.0f}% | "
              f"{x['peak_mb']} | {y['peak_mb']} | {x['calls']} | {y['calls']} | {'совпал' if same else 'ОТЛИЧАЕТСЯ'} |")
    print("Все ответы совпали" if ok else "ЕСТЬ РАСХОЖДЕНИЯ")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1], sys.argv[2]))
