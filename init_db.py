from database import Database

def initialize_database():
    """Инициализирует базу данных с реальными данными клуба"""
    db = Database()
    db.initialize_real_data()

if __name__ == '__main__':
    initialize_database()