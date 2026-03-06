#!/usr/bin/env python
"""
Скрипт для инициализации базы данных тестовыми заданиями и кандидатами.
Запуск: uv run python scripts/init_test_data.py
"""

import asyncio
import json
import os
from pathlib import Path

from app.services.bootstrap import build_runtime_container
from app.roles import RuntimeRole


async def init_data():
    # Создаём контейнер с ролью "api" (используем строковое значение)
    # В проекте RuntimeRole определён как Enum со значениями "api", "worker-..." и т.д.
    # Конструктор RuntimeRole("api") создаст нужный элемент.
    container = build_runtime_container(RuntimeRole("api"))

    # Если есть startup-действия (например, подключение к БД), выполняем их
    if container.on_startup:
        await container.on_startup()

    repo = container.repository

    # Путь к файлу с данными
    data_file = Path("data/test_data.json")
    if not data_file.exists():
        print(f"Файл {data_file} не найден. Создаю файл с тестовыми данными по умолчанию.")
        # Создаём папку data, если её нет
        data_file.parent.mkdir(exist_ok=True)
        default_data = {
            "assignments": [
                {
                    "title": "Тестовое задание для дата-инженера",
                    "description": "Вопрос 1: Как бы вы спроектировали таблицу для хранения заказов?\nВопрос 2: Напишите SQL-запрос для выборки топ-10 клиентов по сумме заказов.",
                    "is_active": True
                }
            ],
            "candidates": [
                {
                    "first_name": "Иван",
                    "last_name": "Петров"
                }
            ]
        }
        with open(data_file, "w", encoding="utf-8") as f:
            json.dump(default_data, f, ensure_ascii=False, indent=2)
        print(f"Файл {data_file} создан.")

    # Загружаем данные из JSON
    with open(data_file, "r", encoding="utf-8") as f:
        data = json.load(f)

    # Создаём задания (проверяем, есть ли уже)
    existing_assignments = await repo.list_assignments(active_only=False)
    if not existing_assignments:
        for assignment_data in data.get("assignments", []):
            assignment = await repo.create_assignment(**assignment_data)
            print(f"Создано задание: {assignment.assignment_public_id}")
    else:
        print("Задания уже существуют, пропускаем создание.")

    # Создаём кандидатов
    # Примечание: в InMemoryWorkRepository нет метода list_candidates, но мы можем просто создавать,
    # а дубликаты проверять не будем для простоты. Если нужна проверка, можно добавить.
    for candidate_data in data.get("candidates", []):
        # Можно проверить, есть ли уже такой кандидат, но для простоты создаём всегда
        candidate = await repo.create_candidate(**candidate_data)
        print(f"Создан кандидат: {candidate.candidate_public_id}")

    # Если есть shutdown, вызываем
    if container.on_shutdown:
        await container.on_shutdown()

    print("Инициализация завершена. Используйте полученные public_id для тестирования.")


if __name__ == "__main__":
    asyncio.run(init_data())