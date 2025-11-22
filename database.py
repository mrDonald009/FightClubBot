import sqlite3
import datetime
import os


class Database:
    def __init__(self, db_file='fightclub.db'):
        self.db_file = db_file
        self.create_tables()

    def get_connection(self):
        """Создает новое соединение для каждого запроса"""
        return sqlite3.connect(self.db_file, check_same_thread=False)

    def create_tables(self):
        """Создает таблицы если они не существуют"""
        conn = self.get_connection()
        cursor = conn.cursor()

        try:
            # Пользователи
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS users (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    telegram_id INTEGER UNIQUE,
                    username TEXT,
                    full_name TEXT,
                    phone TEXT,
                    registration_date TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            ''')

            # Тренеры
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS trainers (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    telegram_id INTEGER UNIQUE,
                    name TEXT,
                    specialization TEXT,
                    phone TEXT,
                    is_active BOOLEAN DEFAULT 1
                )
            ''')

            # Типы тренировок
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS workout_types (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    name TEXT,
                    description TEXT,
                    duration INTEGER,
                    max_participants INTEGER,
                    price INTEGER,
                    category TEXT
                )
            ''')

            # Расписание
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS schedule (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    workout_type_id INTEGER,
                    trainer_id INTEGER,
                    date DATE,
                    time TIME,
                    available_slots INTEGER,
                    FOREIGN KEY (workout_type_id) REFERENCES workout_types (id),
                    FOREIGN KEY (trainer_id) REFERENCES trainers (id)
                )
            ''')

            # Бронирования
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS bookings (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id INTEGER,
                    schedule_id INTEGER,
                    booking_date TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    status TEXT DEFAULT 'active',
                    FOREIGN KEY (user_id) REFERENCES users (id),
                    FOREIGN KEY (schedule_id) REFERENCES schedule (id)
                )
            ''')

            conn.commit()
            print("✅ Таблицы базы данных созданы/проверены")

        except Exception as e:
            print(f"❌ Ошибка создания таблиц: {e}")
        finally:
            conn.close()

    def initialize_real_data(self):
        """Инициализирует базу реальными данными клуба в Люберцах"""
        print("🔄 Загружаем реальные данные клуба...")

        conn = self.get_connection()
        cursor = conn.cursor()

        try:
            # Очищаем старые данные
            cursor.execute('DELETE FROM workout_types')
            cursor.execute('DELETE FROM trainers')
            cursor.execute('DELETE FROM schedule')

            # Реальные данные клуба
            workouts = [
                # Тайский бокс - Дети
                ('Тайский бокс (дети 5-8 лет)', 'Группа для детей от 5 лет', 60, 10, 6000, 'Дети'),
                ('Тайский бокс (дети 9-14 лет)', 'Группа для детей от 9 лет', 60, 12, 6000, 'Дети'),
                (
                'Тайский бокс (взрослые от 15 лет)', 'Тренировки для подростков от 15 лет и взрослых. Смешанная группа',
                90, 15, 6000, 'Взрослые'),
                ('Тайский бокс (женская группа)', 'Отдельная женская группа по тайскому боксу', 90, 12, 6000,
                 'Взрослые'),

                # ММА
                ('ММА (дети)', 'Тренировки по ММА для детей', 60, 10, 6000, 'Дети'),
                ('ММА (взрослые)', 'Тренировки по смешанным единоборствам', 90, 15, 6000, 'Взрослые'),

                # Грэпплинг/БЖЖ
                ('Грэпплинг/БЖЖ', 'Тренировки по грэпплингу и бразильскому джиу-джитсу', 90, 12, 6000, 'Взрослые'),

                # Бокс
                ('Бокс (утренние тренировки)', 'Утренние тренировки по боксу', 90, 10, 6000, 'Взрослыe'),

                # Комбинированные
                (
                'Тайский бокс + ММА', 'Месячный абонемент на тренировки по тайскому боксу и ММА', 0, 0, 11000, 'Комбо'),

                # Индивидуальные
                ('Индивидуальная тренировка', 'Тренировка 1 на 1 с тренером', 60, 1, 3000, 'Индивидуально'),
                ('Сплит тренировка (2 человека)', 'Тренировка для 2 человек с тренером', 60, 2, 4000, 'Индивидуально')
            ]

            # Добавляем тренировки
            cursor.executemany('''
                INSERT INTO workout_types (name, description, duration, max_participants, price, category)
                VALUES (?, ?, ?, ?, ?, ?)
            ''', workouts)

            # Тренеры
            trainers = [
                (1001, 'Тренер по тайскому боксу', 'Тайский бокс, ММА', '+7 (965) 229-64-06'),
                (1002, 'Тренер по грэпплингу', 'Грэпплинг, БЖЖ', '+7 (965) 229-64-06'),
                (1003, 'Тренер по боксу', 'Бокс', '+7 (965) 229-64-06')
            ]

            cursor.executemany('''
                INSERT INTO trainers (telegram_id, name, specialization, phone)
                VALUES (?, ?, ?, ?)
            ''', trainers)

            # Создаем расписание на ближайшие 7 дней
            today = datetime.datetime.now().date()

            for i in range(7):
                date = today + datetime.timedelta(days=i)
                weekday = date.weekday()  # 0-пн, 6-вс

                if weekday in [0, 2, 4]:  # Пн, Ср, Пт
                    # Тайский бокс взрослые вечером
                    cursor.execute('''
                        INSERT INTO schedule (workout_type_id, trainer_id, date, time, available_slots)
                        VALUES (3, 1, ?, '19:00', 15)
                    ''', (date,))

                    # ММА взрослые
                    cursor.execute('''
                        INSERT INTO schedule (workout_type_id, trainer_id, date, time, available_slots)
                        VALUES (6, 1, ?, '20:30', 15)
                    ''', (date,))

                elif weekday in [1, 3]:  # Вт, Чт
                    # Тайский бокс дети
                    cursor.execute('''
                        INSERT INTO schedule (workout_type_id, trainer_id, date, time, available_slots)
                        VALUES (2, 1, ?, '17:00', 12)
                    ''', (date,))

                    # Грэпплинг взрослые
                    cursor.execute('''
                        INSERT INTO schedule (workout_type_id, trainer_id, date, time, available_slots)
                        VALUES (7, 2, ?, '19:00', 12)
                    ''', (date,))

                elif weekday == 5:  # Суббота
                    # Детские группы в субботу
                    cursor.execute('''
                        INSERT INTO schedule (workout_type_id, trainer_id, date, time, available_slots)
                        VALUES (1, 1, ?, '11:00', 10)
                    ''', (date,))

                    cursor.execute('''
                        INSERT INTO schedule (workout_type_id, trainer_id, date, time, available_slots)
                        VALUES (5, 1, ?, '12:00', 10)
                    ''', (date,))

            conn.commit()
            print("✅ База данных инициализирована с реальными данными!")
            print("🏋️ Тренировок: 11 | 👨‍🏫 Тренеров: 3 | 📅 Расписание: 7 дней")

        except Exception as e:
            print(f"❌ Ошибка инициализации данных: {e}")
        finally:
            conn.close()

    def add_test_schedule(self):
        """Добавляет тестовое расписание на ближайшие 3 дня"""
        conn = self.get_connection()
        cursor = conn.cursor()

        try:
            # Очищаем старые тестовые данные (опционально)
            cursor.execute("DELETE FROM schedule WHERE date >= date('now')")

            today = datetime.datetime.now().date()

            # Добавляем тренировки на сегодня, завтра, послезавтра
            for i in range(3):
                date = today + datetime.timedelta(days=i)

                # Тайский бокс вечером
                cursor.execute('''
                    INSERT INTO schedule (workout_type_id, trainer_id, date, time, available_slots)
                    VALUES (3, 1, ?, '19:00', 5)
                ''', (date,))

                # ММА вечером
                cursor.execute('''
                    INSERT INTO schedule (workout_type_id, trainer_id, date, time, available_slots)
                    VALUES (6, 1, ?, '20:30', 3)
                ''', (date,))

                # Детская группа днем (только в будни)
                if date.weekday() < 5:  # Пн-Пт
                    cursor.execute('''
                        INSERT INTO schedule (workout_type_id, trainer_id, date, time, available_slots)
                        VALUES (2, 1, ?, '17:00', 8)
                    ''', (date,))

            conn.commit()
            print("✅ Тестовое расписание добавлено на 3 дня")

        except Exception as e:
            print(f"❌ Ошибка добавления тестового расписания: {e}")
        finally:
            conn.close()

    def add_user(self, telegram_id, username, full_name, phone=None):
        """Добавляет пользователя в базу"""
        conn = self.get_connection()
        cursor = conn.cursor()

        try:
            cursor.execute('''
                INSERT OR IGNORE INTO users (telegram_id, username, full_name, phone)
                VALUES (?, ?, ?, ?)
            ''', (telegram_id, username, full_name, phone))
            conn.commit()
            return True
        except Exception as e:
            print(f"Ошибка добавления пользователя: {e}")
            return False
        finally:
            conn.close()

    def get_user(self, telegram_id):
        """Получает пользователя по telegram_id"""
        conn = self.get_connection()
        cursor = conn.cursor()

        try:
            cursor.execute('SELECT * FROM users WHERE telegram_id = ?', (telegram_id,))
            return cursor.fetchone()
        finally:
            conn.close()

    def get_workouts(self):
        """Получает все типы тренировок"""
        conn = self.get_connection()
        cursor = conn.cursor()

        try:
            cursor.execute('SELECT * FROM workout_types ORDER BY category, name')
            return cursor.fetchall()
        finally:
            conn.close()

    def get_schedule(self, days=7):
        """Получает расписание на указанное количество дней"""
        conn = self.get_connection()
        cursor = conn.cursor()

        try:
            cursor.execute('''
                SELECT s.id, s.date, s.time, w.name, t.name, s.available_slots, w.category
                FROM schedule s
                JOIN workout_types w ON s.workout_type_id = w.id
                JOIN trainers t ON s.trainer_id = t.id
                WHERE s.date >= date('now') 
                ORDER BY s.date, s.time
                LIMIT ?
            ''', (days * 3,))
            return cursor.fetchall()
        finally:
            conn.close()

    # НОВЫЕ МЕТОДЫ ДЛЯ ФУНКЦИОНАЛА БОТА

    def get_workouts_by_date(self, date):
        """Получить тренировки на определенную дату"""
        conn = self.get_connection()
        cursor = conn.cursor()

        try:
            cursor.execute('''
                SELECT 
                    s.id, 
                    s.time, 
                    wt.name as type, 
                    t.name as trainer, 
                    s.available_slots,
                    wt.category
                FROM schedule s
                JOIN workout_types wt ON s.workout_type_id = wt.id
                JOIN trainers t ON s.trainer_id = t.id
                WHERE s.date = ? AND s.available_slots > 0
                ORDER BY s.time
            ''', (date,))

            workouts = []
            for row in cursor.fetchall():
                workouts.append({
                    'id': row[0],
                    'time': row[1],
                    'type': row[2],
                    'trainer': row[3],
                    'available_slots': row[4],
                    'category': row[5]
                })

            return workouts

        except Exception as e:
            print(f"Ошибка получения тренировок: {e}")
            return []
        finally:
            conn.close()

    def book_workout(self, user_id, schedule_id):
        """Записать пользователя на тренировку"""
        conn = self.get_connection()
        cursor = conn.cursor()

        try:
            # Получаем user_id из users таблицы по telegram_id
            cursor.execute('SELECT id FROM users WHERE telegram_id = ?', (user_id,))
            user_row = cursor.fetchone()

            if not user_row:
                print(f"Пользователь {user_id} не найден")
                return False

            user_db_id = user_row[0]

            # Проверяем доступность слотов
            cursor.execute('SELECT available_slots FROM schedule WHERE id = ?', (schedule_id,))
            schedule_row = cursor.fetchone()

            if not schedule_row or schedule_row[0] <= 0:
                print(f"Нет свободных мест для тренировки {schedule_id}")
                return False

            # Создаем бронирование
            cursor.execute('''
                INSERT INTO bookings (user_id, schedule_id, status)
                VALUES (?, ?, 'active')
            ''', (user_db_id, schedule_id))

            # Уменьшаем количество доступных слотов
            cursor.execute('''
                UPDATE schedule 
                SET available_slots = available_slots - 1 
                WHERE id = ?
            ''', (schedule_id,))

            conn.commit()
            print(f"✅ Пользователь {user_id} записан на тренировку {schedule_id}")
            return True

        except Exception as e:
            print(f"Ошибка бронирования: {e}")
            conn.rollback()
            return False
        finally:
            conn.close()

    def get_user_stats(self, telegram_id):
        """Получить статистику пользователя"""
        conn = self.get_connection()
        cursor = conn.cursor()

        try:
            # Получаем user_id
            cursor.execute('SELECT id FROM users WHERE telegram_id = ?', (telegram_id,))
            user_row = cursor.fetchone()

            if not user_row:
                return {'total_workouts': 0, 'current_streak': 0}

            user_db_id = user_row[0]

            # Общее количество посещенных тренировок
            cursor.execute('''
                SELECT COUNT(*) FROM bookings 
                WHERE user_id = ? AND status = 'attended'
            ''', (user_db_id,))
            total_workouts = cursor.fetchone()[0] or 0

            # Текущая серия посещений (упрощенная версия)
            cursor.execute('''
                SELECT COUNT(*) FROM (
                    SELECT DISTINCT date(s.date) 
                    FROM bookings b
                    JOIN schedule s ON b.schedule_id = s.id
                    WHERE b.user_id = ? AND b.status = 'attended'
                    ORDER BY s.date DESC 
                    LIMIT 7
                )
            ''', (user_db_id,))
            current_streak = cursor.fetchone()[0] or 0

            return {
                'total_workouts': total_workouts,
                'current_streak': current_streak
            }

        except Exception as e:
            print(f"Ошибка получения статистики: {e}")
            return {'total_workouts': 0, 'current_streak': 0}
        finally:
            conn.close()

    def get_user_profile(self, telegram_id):
        """Получить профиль пользователя"""
        conn = self.get_connection()
        cursor = conn.cursor()

        try:
            cursor.execute('''
                SELECT username, full_name, phone, registration_date 
                FROM users WHERE telegram_id = ?
            ''', (telegram_id,))

            row = cursor.fetchone()

            if row:
                return {
                    'username': row[0] or 'Не указан',
                    'full_name': row[1] or 'Не указано',
                    'phone': row[2] or 'Не указан',
                    'registration_date': row[3]
                }
            else:
                return {
                    'username': 'Не указан',
                    'full_name': 'Не указано',
                    'phone': 'Не указан',
                    'registration_date': 'Неизвестно'
                }

        except Exception as e:
            print(f"Ошибка получения профиля: {e}")
            return {
                'username': 'Ошибка',
                'full_name': 'Ошибка',
                'phone': 'Ошибка',
                'registration_date': 'Ошибка'
            }
        finally:
            conn.close()

    def get_user_bookings(self, telegram_id):
        """Получить активные бронирования пользователя"""
        conn = self.get_connection()
        cursor = conn.cursor()

        try:
            cursor.execute('''
                SELECT 
                    b.id,
                    s.date,
                    s.time, 
                    wt.name,
                    t.name
                FROM bookings b
                JOIN schedule s ON b.schedule_id = s.id
                JOIN workout_types wt ON s.workout_type_id = wt.id
                JOIN trainers t ON s.trainer_id = t.id
                JOIN users u ON b.user_id = u.id
                WHERE u.telegram_id = ? AND b.status = 'active' AND s.date >= date('now')
                ORDER BY s.date, s.time
            ''', (telegram_id,))

            bookings = []
            for row in cursor.fetchall():
                bookings.append({
                    'id': row[0],
                    'date': row[1],
                    'time': row[2],
                    'workout_name': row[3],
                    'trainer': row[4]
                })

            return bookings

        except Exception as e:
            print(f"Ошибка получения бронирований: {e}")
            return []
        finally:
            conn.close()

    def cancel_booking(self, booking_id, telegram_id):
        """Отменить бронирование"""
        conn = self.get_connection()
        cursor = conn.cursor()

        try:
            # Получаем user_id
            cursor.execute('SELECT id FROM users WHERE telegram_id = ?', (telegram_id,))
            user_row = cursor.fetchone()

            if not user_row:
                return False

            user_db_id = user_row[0]

            # Отменяем бронирование
            cursor.execute('''
                UPDATE bookings 
                SET status = 'cancelled' 
                WHERE id = ? AND user_id = ?
            ''', (booking_id, user_db_id))

            # Возвращаем слот в расписание
            cursor.execute('''
                UPDATE schedule 
                SET available_slots = available_slots + 1 
                WHERE id = (
                    SELECT schedule_id FROM bookings WHERE id = ?
                )
            ''', (booking_id,))

            conn.commit()
            return cursor.rowcount > 0

        except Exception as e:
            print(f"Ошибка отмены бронирования: {e}")
            conn.rollback()
            return False
        finally:
            conn.close()

    def mark_attendance(self, booking_id, telegram_id):
        """Отметить посещение тренировки"""
        conn = self.get_connection()
        cursor = conn.cursor()

        try:
            # Получаем user_id
            cursor.execute('SELECT id FROM users WHERE telegram_id = ?', (telegram_id,))
            user_row = cursor.fetchone()

            if not user_row:
                return False

            user_db_id = user_row[0]

            # Отмечаем посещение
            cursor.execute('''
                UPDATE bookings 
                SET status = 'attended' 
                WHERE id = ? AND user_id = ?
            ''', (booking_id, user_db_id))

            conn.commit()
            return cursor.rowcount > 0

        except Exception as e:
            print(f"Ошибка отметки посещения: {e}")
            conn.rollback()
            return False
        finally:
            conn.close()