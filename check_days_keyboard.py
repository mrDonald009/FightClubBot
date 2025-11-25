import sys
import os
import datetime

sys.path.append(os.path.dirname(os.path.abspath(__file__)))

from keyboards.booking import days_selection_keyboard


def check_days_keyboard():
    """Проверяет, какие дни отображаются в клавиатуре"""
    keyboard = days_selection_keyboard()

    print("📅 Дни в клавиатуре выбора:")
    today = datetime.datetime.now()

    for i in range(7):
        day = today + datetime.timedelta(days=i)
        day_str = day.strftime('%d.%m.%Y')
        print(f"  День {i}: {day_str}")

        # Проверим, будет ли это 01.12.2025
        if day_str == "01.12.2025":
            print(f"  ✅ 01.12.2025 будет доступен как день #{i}")


if __name__ == '__main__':
    check_days_keyboard()