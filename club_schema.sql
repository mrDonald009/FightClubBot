CREATE TABLE sport_types (
	id INTEGER NOT NULL, 
	name VARCHAR(50) NOT NULL, 
	display_name VARCHAR(100), 
	is_active BOOLEAN, 
	created_at DATETIME, 
	PRIMARY KEY (id), 
	UNIQUE (name)
);
CREATE TABLE admins (
	id INTEGER NOT NULL, 
	telegram_id INTEGER NOT NULL, 
	username VARCHAR(100), 
	first_name VARCHAR(100), 
	created_at DATETIME, 
	is_active BOOLEAN, 
	PRIMARY KEY (id), 
	UNIQUE (telegram_id)
);
CREATE TABLE assistants (
	id INTEGER NOT NULL, 
	telegram_id INTEGER NOT NULL, 
	username VARCHAR(100), 
	first_name VARCHAR(100), 
	created_at DATETIME, 
	is_active BOOLEAN, 
	PRIMARY KEY (id), 
	UNIQUE (telegram_id)
);
CREATE TABLE global_freezes (
	id INTEGER NOT NULL, 
	title VARCHAR(200) NOT NULL, 
	start_date DATETIME NOT NULL, 
	end_date DATETIME NOT NULL, 
	is_active BOOLEAN, 
	created_by INTEGER, 
	created_at DATETIME, 
	PRIMARY KEY (id)
);
CREATE INDEX ix_global_freezes_active_range ON global_freezes (is_active, start_date, end_date);
CREATE TABLE coaches (
	id INTEGER NOT NULL, 
	telegram_id INTEGER NOT NULL, 
	username VARCHAR(100), 
	first_name VARCHAR(100), 
	sport_type_id INTEGER NOT NULL, 
	sport_type VARCHAR(50), 
	created_at DATETIME, 
	is_active BOOLEAN, 
	PRIMARY KEY (id), 
	UNIQUE (telegram_id), 
	FOREIGN KEY(sport_type_id) REFERENCES sport_types (id)
);
CREATE TABLE athletes (
	id INTEGER NOT NULL, 
	telegram_id INTEGER, 
	full_name VARCHAR(200) NOT NULL, 
	phone VARCHAR(20), 
	birth_date DATETIME, 
	height INTEGER, 
	weight INTEGER, 
	medical_info TEXT, 
	sport_type VARCHAR(50), 
	age_group VARCHAR(20), 
	created_by INTEGER, 
	created_at DATETIME, subscription_id INTEGER, current_subscription_id INTEGER, 
	PRIMARY KEY (id), 
	CONSTRAINT ck_athletes_age_group CHECK (age_group IS NULL OR age_group IN ('children', 'adults')), 
	UNIQUE (telegram_id), 
	FOREIGN KEY(created_by) REFERENCES coaches (id)
);
CREATE INDEX ix_athletes_created_by ON athletes (created_by);
CREATE INDEX ix_athletes_sport_age ON athletes (sport_type, age_group);
CREATE TABLE trainings (
	id INTEGER NOT NULL, 
	sport_type VARCHAR(50), 
	age_group VARCHAR(20), 
	training_date DATETIME, 
	is_cancelled BOOLEAN, 
	coach_id INTEGER, 
	PRIMARY KEY (id), 
	FOREIGN KEY(coach_id) REFERENCES coaches (id)
);
CREATE INDEX ix_trainings_coach_date ON trainings (coach_id, training_date);
CREATE INDEX ix_trainings_sport_age_date ON trainings (sport_type, age_group, training_date);
CREATE TABLE subscriptions (
	id INTEGER NOT NULL, 
	athlete_id INTEGER NOT NULL, 
	discipline_key VARCHAR(64) NOT NULL, 
	responsible_coach_id INTEGER, 
	sport_type_id INTEGER, 
	sport_type VARCHAR(50), 
	subscription_type VARCHAR(20), 
	start_date DATETIME, 
	end_date DATETIME, 
	trainings_total INTEGER, 
	trainings_remaining INTEGER, 
	is_active BOOLEAN, 
	total_restored INTEGER, 
	restored_this_month INTEGER, 
	is_frozen BOOLEAN, 
	frozen_from DATETIME, 
	frozen_until DATETIME, 
	frozen_days_total INTEGER, 
	frozen_training_days_total INTEGER, 
	created_at DATETIME, frozen_count INTEGER DEFAULT 0, 
	PRIMARY KEY (id), 
	CONSTRAINT ck_subscriptions_type CHECK (subscription_type IS NULL OR subscription_type IN ('monthly', 'single')), 
	CONSTRAINT ck_subscriptions_total_nonnegative CHECK (trainings_total IS NULL OR trainings_total >= 0), 
	CONSTRAINT ck_subscriptions_remaining_nonnegative CHECK (trainings_remaining IS NULL OR trainings_remaining >= 0), 
	CONSTRAINT ck_subscriptions_remaining_lte_total CHECK (trainings_total IS NULL OR trainings_remaining IS NULL OR trainings_remaining <= trainings_total), 
	FOREIGN KEY(athlete_id) REFERENCES athletes (id), 
	FOREIGN KEY(sport_type_id) REFERENCES sport_types (id), 
	FOREIGN KEY(responsible_coach_id) REFERENCES coaches (id)
);
CREATE INDEX ix_subscriptions_active_end ON subscriptions (is_active, end_date);
CREATE INDEX ix_subscriptions_athlete_active ON subscriptions (athlete_id, is_active);
CREATE TABLE attendances (
	id INTEGER NOT NULL, 
	athlete_id INTEGER, 
	training_id INTEGER, 
	subscription_id INTEGER, 
	attended BOOLEAN, 
	marked_by INTEGER, 
	created_at DATETIME, 
	was_restored BOOLEAN, 
	restoration_reason TEXT, 
	PRIMARY KEY (id), 
	CONSTRAINT uq_attendances_athlete_training UNIQUE (athlete_id, training_id), 
	FOREIGN KEY(athlete_id) REFERENCES athletes (id), 
	FOREIGN KEY(training_id) REFERENCES trainings (id), 
	FOREIGN KEY(subscription_id) REFERENCES subscriptions (id)
);
CREATE INDEX ix_attendances_athlete_created ON attendances (athlete_id, created_at);
CREATE INDEX ix_attendances_subscription ON attendances (subscription_id);
CREATE TABLE restoration_requests (
	id INTEGER NOT NULL, 
	athlete_id INTEGER, 
	subscription_id INTEGER, 
	missed_dates TEXT, 
	restored_count INTEGER, 
	reason TEXT, 
	notes TEXT, 
	restored_by INTEGER, 
	restored_at DATETIME, 
	PRIMARY KEY (id), 
	FOREIGN KEY(athlete_id) REFERENCES athletes (id), 
	FOREIGN KEY(subscription_id) REFERENCES subscriptions (id)
);
CREATE TABLE global_freeze_applications (
	id INTEGER NOT NULL, 
	global_freeze_id INTEGER NOT NULL, 
	subscription_id INTEGER NOT NULL, 
	training_days_added INTEGER, 
	old_end_date DATETIME, 
	new_end_date DATETIME, 
	created_at DATETIME, 
	PRIMARY KEY (id), 
	CONSTRAINT uq_global_freeze_subscription UNIQUE (global_freeze_id, subscription_id), 
	FOREIGN KEY(global_freeze_id) REFERENCES global_freezes (id), 
	FOREIGN KEY(subscription_id) REFERENCES subscriptions (id)
);
CREATE INDEX ix_gfa_subscription ON global_freeze_applications (subscription_id);
CREATE TABLE athlete_freezes (
	id INTEGER NOT NULL, 
	athlete_id INTEGER NOT NULL, 
	frozen_from DATETIME NOT NULL, 
	frozen_until DATETIME NOT NULL, 
	initiated_by_coach_id INTEGER, 
	global_freeze_id INTEGER, 
	created_at DATETIME, 
	PRIMARY KEY (id), 
	FOREIGN KEY(athlete_id) REFERENCES athletes (id), 
	FOREIGN KEY(initiated_by_coach_id) REFERENCES coaches (id), 
	FOREIGN KEY(global_freeze_id) REFERENCES global_freezes (id)
);
CREATE INDEX ix_athlete_freezes_athlete_range ON athlete_freezes (athlete_id, frozen_from, frozen_until);
CREATE UNIQUE INDEX uq_subscriptions_athlete_discipline
                ON subscriptions (athlete_id, discipline_key)
            ;
CREATE UNIQUE INDEX uq_attendances_athlete_training
                ON attendances (athlete_id, training_id)
            ;
CREATE UNIQUE INDEX uq_global_freeze_subscription
            ON global_freeze_applications (global_freeze_id, subscription_id)
        ;
