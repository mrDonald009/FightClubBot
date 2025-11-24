import sqlite3
import datetime
import os
import logging
from contextlib import contextmanager
from typing import List, Dict, Optional
import config

# Настройка логирования
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


class Database:
    def __init__(self, db_file: str = config.config.DB_PATH):
        self.db_file = db_file
        self._ensure_backup_dir()
        self.create_tables()
        self._create_indexes()

    def _ensure_backup_dir(self):
        os.makedirs(config.config.BACKUP_PATH, exist_ok=True)

    @contextmanager
    def get_connection(self):
        conn = sqlite3.connect(self.db_file, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        try:
            yield conn
            conn.commit()
        except Exception as e:
            conn.rollback()
            logger.error(f"Database error: {e}")
            raise
        finally:
            conn.close()

    def _create_indexes(self):
        indexes = [
            "CREATE INDEX IF NOT EXISTS idx_users_telegram ON users(telegram_id)",
            "CREATE INDEX IF NOT EXISTS idx_schedule_date ON schedule(date)",
            "CREATE INDEX IF NOT EXISTS idx_bookings_user ON bookings(user_id)",
            "CREATE INDEX IF NOT EXISTS idx_bookings_status ON bookings(status)",
            "CREATE INDEX IF NOT EXISTS idx_workouts_category ON workout_types(category)"
        ]

        with self.get_connection() as conn:
            for index_sql in indexes:
                conn.execute(index_sql)
        logger.info("Database indexes created")

    def create_tables(self):
        tables = {
            'users': '''
                CREATE TABLE IF NOT EXISTS users (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    telegram_id INTEGER UNIQUE NOT NULL,
                    username TEXT,
                    full_name TEXT NOT NULL,
                    phone TEXT,
                    registration_date TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    last_active TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            ''',
            'trainers': '''
                CREATE TABLE IF NOT EXISTS trainers (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    telegram_id INTEGER UNIQUE,
                    name TEXT NOT NULL,
                    specialization TEXT,
                    phone TEXT,
                    is_active BOOLEAN DEFAULT 1
                )
            ''',
            'workout_types': '''
                CREATE TABLE IF NOT EXISTS workout_types (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    name TEXT NOT NULL,
                    description TEXT,
                    duration INTEGER,
                    max_participants INTEGER,
                    price INTEGER,
                    category TEXT,
                    is_active BOOLEAN DEFAULT 1
                )
            ''',
            'schedule': '''
                CREATE TABLE IF NOT EXISTS schedule (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    workout_type_id INTEGER NOT NULL,
                    trainer_id INTEGER NOT NULL,
                    date DATE NOT NULL,
                    time TIME NOT NULL,
                    available_slots INTEGER DEFAULT 0,
                    FOREIGN KEY (workout_type_id) REFERENCES workout_types (id),
                    FOREIGN KEY (trainer_id) REFERENCES trainers (id)
                )
            ''',
            'bookings': '''
                CREATE TABLE IF NOT EXISTS bookings (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id INTEGER NOT NULL,
                    schedule_id INTEGER NOT NULL,
                    booking_date TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    status TEXT DEFAULT 'active',
                    FOREIGN KEY (user_id) REFERENCES users (id),
                    FOREIGN KEY (schedule_id) REFERENCES schedule (id),
                    UNIQUE(user_id, schedule_id)
                )
            '''
        }

        with self.get_connection() as conn:
            for table_name, table_sql in tables.items():
                conn.execute(table_sql)
        logger.info("Database tables created")

    def initialize_real_data(self):
        logger.info("Loading real club data...")

        with self.get_connection() as conn:
            try:
                # Очищаем старые данные
                conn.execute('DELETE FROM workout_types')
                conn.execute('DELETE FROM trainers')
                conn.execute('DELETE FROM schedule')

                workouts = [
                    ('Тайский бокс (дети 5-8 лет)', 'Группа для детей от 5 лет', 60, 10, 6000, 'Дети'),
                    ('Тайский бокс (дети 9-14 лет)', 'Группа для детей от 9 лет', 60, 12, 6000, 'Дети'),
                    (
                    'Тайский бокс (взрослые от 15 лет)', 'Тренировки для подростков от 15 лет и взрослых', 90, 15, 6000,
                    'Взрослые'),
                    ('Тайский бокс (женская группа)', 'Отдельная женская группа по тайскому боксу', 90, 12, 6000,
                     'Взрослые'),
                    ('ММА (дети)', 'Тренировки по ММА для детей', 60, 10, 6000, 'Дети'),
                    ('ММА (взрослые)', 'Тренировки по смешанным единоборствам', 90, 15, 6000, 'Взрослые'),
                    ('Грэпплинг/БЖЖ', 'Тренировки по грэпплингу и бразильскому джиу-джитсу', 90, 12, 6000, 'Взрослые'),
                    ('Бокс (утренние тренировки)', 'Утренние тренировки по боксу', 90, 10, 6000, 'Взрослые'),
                    ('Тайский бокс + ММА', 'Месячный абонемент на тренировки по тайскому боксу и ММА', 0, 0, 11000,
                     'Комбо'),
                    ('Индивидуальная тренировка', 'Тренировка 1 на 1 с тренером', 60, 1, 3000, 'Индивидуально'),
                    ('Сплит тренировка (2 человека)', 'Тренировка для 2 человек с тренером', 60, 2, 4000,
                     'Индивидуально')
                ]

                conn.executemany(
                    'INSERT INTO workout_types (name, description, duration, max_participants, price, category) VALUES (?, ?, ?, ?, ?, ?)',
                    workouts
                )

                trainers = [
                    (1001, 'Тренер по тайскому боксу', 'Тайский бокс, ММА', '+7 (965) 229-64-06'),
                    (1002, 'Тренер по грэпплингу', 'Грэпплинг, БЖЖ', '+7 (965) 229-64-06'),
                    (1003, 'Тренер по боксу', 'Бокс', '+7 (965) 229-64-06')
                ]

                conn.executemany(
                    'INSERT INTO trainers (telegram_id, name, specialization, phone) VALUES (?, ?, ?, ?)',
                    trainers
                )

                self._generate_schedule(conn, days=14)

                logger.info("Database initialized with real data!")

            except Exception as e:
                logger.error(f"Error initializing data: {e}")
                raise

    def _generate_schedule(self, conn, days: int = 14):
        today = datetime.datetime.now().date()

        for i in range(days):
            date = today + datetime.timedelta(days=i)
            weekday = date.weekday()

            if weekday == 6:
                continue

            schedule_slots = self._get_daily_schedule(weekday, date)

            for slot in schedule_slots:
                conn.execute(
                    'INSERT INTO schedule (workout_type_id, trainer_id, date, time, available_slots) VALUES (?, ?, ?, ?, ?)',
                    slot
                )

    def _get_daily_schedule(self, weekday: int, date: datetime.date) -> List[tuple]:
        if weekday in [0, 2, 4]:
            return [
                (3, 1, date, '19:00', 15),
                (6, 1, date, '20:30', 15)
            ]
        elif weekday in [1, 3]:
            return [
                (2, 1, date, '17:00', 12),
                (7, 2, date, '19:00', 12)
            ]
        elif weekday == 5:
            return [
                (1, 1, date, '11:00', 10),
                (5, 1, date, '12:00', 10)
            ]
        return []

    def add_user(self, telegram_id: int, username: str, full_name: str, phone: str = None) -> bool:
        with self.get_connection() as conn:
            try:
                conn.execute(
                    'INSERT OR REPLACE INTO users (telegram_id, username, full_name, phone, last_active) VALUES (?, ?, ?, ?, CURRENT_TIMESTAMP)',
                    (telegram_id, username, full_name, phone)
                )
                return True
            except Exception as e:
                logger.error(f"Error adding user: {e}")
                return False

    def get_user(self, telegram_id: int):
        with self.get_connection() as conn:
            result = conn.execute(
                'SELECT * FROM users WHERE telegram_id = ?',
                (telegram_id,)
            ).fetchone()
            return result

    def get_workouts_by_date(self, date: str) -> List[Dict]:
        with self.get_connection() as conn:
            try:
                result = conn.execute('''
                    SELECT 
                        s.id, s.time, wt.name as type, t.name as trainer, 
                        s.available_slots, wt.category, wt.duration
                    FROM schedule s
                    JOIN workout_types wt ON s.workout_type_id = wt.id
                    JOIN trainers t ON s.trainer_id = t.id
                    WHERE s.date = ? AND s.available_slots > 0
                    ORDER BY s.time
                ''', (date,))

                return [dict(row) for row in result]
            except Exception as e:
                logger.error(f"Error getting workouts: {e}")
                return []

    def book_workout(self, user_id: int, schedule_id: int) -> bool:
        with self.get_connection() as conn:
            try:
                user_bookings = conn.execute('''
                    SELECT COUNT(*) FROM bookings b
                    JOIN schedule s ON b.schedule_id = s.id
                    JOIN users u ON b.user_id = u.id
                    WHERE u.telegram_id = ? AND b.status = 'active' AND s.date >= date('now')
                ''', (user_id,)).fetchone()[0]

                if user_bookings >= config.config.MAX_BOOKINGS_PER_USER:
                    logger.warning(f"User {user_id} reached booking limit")
                    return False

                conn.execute('''
                    INSERT INTO bookings (user_id, schedule_id)
                    SELECT u.id, ? 
                    FROM users u 
                    WHERE u.telegram_id = ?
                ''', (schedule_id, user_id))

                conn.execute('''
                    UPDATE schedule 
                    SET available_slots = available_slots - 1 
                    WHERE id = ? AND available_slots > 0
                ''', (schedule_id,))

                affected = conn.total_changes
                return affected > 0

            except sqlite3.IntegrityError:
                logger.warning(f"User {user_id} already booked schedule {schedule_id}")
                return False
            except Exception as e:
                logger.error(f"Booking error: {e}")
                return False

    def get_user_stats(self, telegram_id: int) -> Dict:
        with self.get_connection() as conn:
            try:
                result = conn.execute('''
                    SELECT 
                        COUNT(CASE WHEN b.status = 'attended' THEN 1 END) as total_workouts,
                        COUNT(DISTINCT CASE WHEN b.status = 'attended' THEN s.date END) as unique_days
                    FROM bookings b
                    JOIN schedule s ON b.schedule_id = s.id
                    JOIN users u ON b.user_id = u.id
                    WHERE u.telegram_id = ?
                ''', (telegram_id,)).fetchone()

                return {
                    'total_workouts': result['total_workouts'] or 0,
                    'current_streak': self._calculate_streak(conn, telegram_id)
                }
            except Exception as e:
                logger.error(f"Error getting user stats: {e}")
                return {'total_workouts': 0, 'current_streak': 0}

    def _calculate_streak(self, conn, telegram_id: int) -> int:
        result = conn.execute('''
            SELECT COUNT(*) FROM (
                SELECT DISTINCT date(s.date) 
                FROM bookings b
                JOIN schedule s ON b.schedule_id = s.id
                JOIN users u ON b.user_id = u.id
                WHERE u.telegram_id = ? AND b.status = 'attended'
                ORDER BY s.date DESC 
                LIMIT 7
            )
        ''', (telegram_id,)).fetchone()
        return result[0] or 0

    def get_user_profile(self, telegram_id: int) -> Dict:
        with self.get_connection() as conn:
            try:
                cursor = conn.execute('''
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
                logger.error(f"Error getting user profile: {e}")
                return {
                    'username': 'Ошибка',
                    'full_name': 'Ошибка',
                    'phone': 'Ошибка',
                    'registration_date': 'Ошибка'
                }

    def get_user_bookings(self, telegram_id: int) -> List[Dict]:
        with self.get_connection() as conn:
            try:
                cursor = conn.execute('''
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
                logger.error(f"Error getting user bookings: {e}")
                return []

    def cancel_booking(self, booking_id: int, telegram_id: int) -> bool:
        with self.get_connection() as conn:
            try:
                cursor = conn.execute('SELECT id FROM users WHERE telegram_id = ?', (telegram_id,))
                user_row = cursor.fetchone()

                if not user_row:
                    return False

                user_db_id = user_row[0]

                conn.execute('''
                    UPDATE bookings 
                    SET status = 'cancelled' 
                    WHERE id = ? AND user_id = ?
                ''', (booking_id, user_db_id))

                conn.execute('''
                    UPDATE schedule 
                    SET available_slots = available_slots + 1 
                    WHERE id = (
                        SELECT schedule_id FROM bookings WHERE id = ?
                    )
                ''', (booking_id,))

                return conn.total_changes > 0

            except Exception as e:
                logger.error(f"Error canceling booking: {e}")
                return False

    def mark_attendance(self, booking_id: int, telegram_id: int) -> bool:
        with self.get_connection() as conn:
            try:
                cursor = conn.execute('SELECT id FROM users WHERE telegram_id = ?', (telegram_id,))
                user_row = cursor.fetchone()

                if not user_row:
                    return False

                user_db_id = user_row[0]

                conn.execute('''
                    UPDATE bookings 
                    SET status = 'attended' 
                    WHERE id = ? AND user_id = ?
                ''', (booking_id, user_db_id))

                return conn.total_changes > 0

            except Exception as e:
                logger.error(f"Error marking attendance: {e}")
                return False

    def backup_database(self):
        backup_file = f"{config.config.BACKUP_PATH}backup_{datetime.datetime.now().strftime('%Y%m%d_%H%M%S')}.db"
        try:
            with self.get_connection() as source:
                with sqlite3.connect(backup_file) as target:
                    source.backup(target)
            logger.info(f"Database backed up to {backup_file}")
            return True
        except Exception as e:
            logger.error(f"Backup failed: {e}")
            return False