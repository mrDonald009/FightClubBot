import sqlite3
import threading

class SingletonDB:
    _instance = None
    _lock = threading.Lock()

    def __new__(cls, db_path='fightclub.db'):
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = super().__new__(cls)
                    cls._instance.connection = sqlite3.connect(db_path, check_same_thread=False)
        return cls._instance

    def get_connection(self):
        return self.connection

class Database:
    def __init__(self, db_path='fightclub.db'):
        # Получить единственное соединение через SingletonDB
        singleton_db = SingletonDB(db_path)
        self.conn = singleton_db.get_connection()

    def create_tables(self):
        cursor = self.conn.cursor()
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

            # Таблицы достижений и целей
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS achievements (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id INTEGER,
                    achievement_name TEXT,
                    date_earned TEXT,
                    FOREIGN KEY (user_id) REFERENCES users (id)
                )
            ''')

            self.conn.commit()
            print("✅ Таблицы базы данных созданы или проверены.")
        except Exception as e:
            print(f"❌ Ошибка создания таблиц: {e}")

    # Здесь добавьте остальные методы, как add_user, get_user, get_workouts и т.д.
    def add_user(self, telegram_id, username, full_name, phone=None):
        cursor = self.conn.cursor()
        try:
            cursor.execute('''
                INSERT OR IGNORE INTO users (telegram_id, username, full_name, phone)
                VALUES (?, ?, ?, ?)
            ''', (telegram_id, username, full_name, phone))
            self.conn.commit()
            return True
        except Exception as e:
            print(f"Ошибка при добавлении пользователя: {e}")
            return False

    def get_user(self, telegram_id):
        cursor = self.conn.cursor()
        try:
            cursor.execute('SELECT * FROM users WHERE telegram_id = ?', (telegram_id,))
            return cursor.fetchone()
        except Exception as e:
            print(f"Ошибка при получении пользователя: {e}")
        finally:
            pass

    def get_workouts_by_date(self, date_str):
        cursor = self.conn.cursor()
        try:
            cursor.execute('''
                SELECT s.id, s.time, w.name as type, t.name as trainer, s.available_slots, w.category
                FROM schedule s
                JOIN workout_types w ON s.workout_type_id = w.id
                JOIN trainers t ON s.trainer_id = t.id
                WHERE s.date = ? AND s.available_slots > 0
                ORDER BY s.time
            ''', (date_str,))
            results = []
            for row in cursor.fetchall():
                results.append({
                    'id': row[0],
                    'time': row[1],
                    'type': row[2],
                    'trainer': row[3],
                    'available_slots': row[4],
                    'category': row[5]
                })
            return results
        except Exception as e:
            print(f"Ошибка получения расписания: {e}")
            return []

    def book_workout(self, telegram_id, schedule_id):
        cursor = self.conn.cursor()
        try:
            # Получаем user_id по telegram_id
            cursor.execute('SELECT id FROM users WHERE telegram_id = ?', (telegram_id,))
            user_row = cursor.fetchone()
            if not user_row:
                print(f"Пользователь {telegram_id} не найден")
                return False
            user_db_id = user_row[0]

            # Проверяем доступность слотов
            cursor.execute('SELECT available_slots FROM schedule WHERE id = ?', (schedule_id,))
            schedule_row = cursor.fetchone()
            if not schedule_row or schedule_row[0] <= 0:
                print(f"Нет свободных мест в расписании {schedule_id}")
                return False

            # Создаем бронирование
            cursor.execute('''
                INSERT INTO bookings (user_id, schedule_id, status)
                VALUES (?, ?, 'active')
            ''', (user_db_id, schedule_id))
            # Уменьшаем количество доступных слотов
            cursor.execute('''
                UPDATE schedule SET available_slots = available_slots - 1 WHERE id = ?
            ''', (schedule_id,))
            self.conn.commit()
            print(f"Пользователь {telegram_id} записан на тренировку {schedule_id}")
            return True
        except Exception as e:
            print(f"Ошибка бронирования: {e}")
            self.conn.rollback()
            return False

    # Можно добавить еще остальные функции по аналогии: get_user_stats, get_user_bookings, cancel_booking и т.п.
