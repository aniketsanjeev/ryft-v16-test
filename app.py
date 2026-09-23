import streamlit as st
import sqlite3
import math
import json
import os
import io
from datetime import datetime, timezone, date, time
import pandas as pd

# ==============================================================================
# 1. DATABASE INITIALIZATION & RELATIONAL SCHEMA (V.16 FULL ARCHITECTURE)
# ==============================================================================
DB_FILE = "ryft_v16_master.db"

def get_db_connection():
    conn = sqlite3.connect(DB_FILE, timeout=60.0, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode = WAL;")
    conn.execute("PRAGMA busy_timeout = 60000;")
    return conn

def add_column_if_not_exists(cursor, table, col_name, col_type):
    cursor.execute(f"PRAGMA table_info({table});")
    existing = [row[1] for row in cursor.fetchall()]
    if col_name not in existing:
        cursor.execute(f"ALTER TABLE {table} ADD COLUMN {col_name} {col_type};")

def format_pr_name(name, is_prov):
    return f"{name} (PR)" if is_prov else name

def export_db_bytes():
    conn = get_db_connection()
    conn.execute("PRAGMA wal_checkpoint(TRUNCATE);")
    conn.commit()
    mem_backup = sqlite3.connect(":memory:")
    conn.backup(mem_backup)
    conn.close()
    temp_file = "temp_export_snapshot.db"
    dest = sqlite3.connect(temp_file)
    mem_backup.backup(dest)
    dest.close()
    mem_backup.close()
    with open(temp_file, "rb") as f: data = f.read()
    if os.path.exists(temp_file): os.remove(temp_file)
    return data

def restore_db_from_bytes(uploaded_bytes):
    temp_in = "temp_incoming_restore.db"
    with open(temp_in, "wb") as f: f.write(uploaded_bytes)
    source = sqlite3.connect(temp_in)
    tables = [r[0] for r in source.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()]
    if "players" not in tables or "global_config" not in tables:
        source.close()
        if os.path.exists(temp_in): os.remove(temp_in)
        raise ValueError("The uploaded file is not a valid RYFT database snapshot.")
    dest = get_db_connection()
    dest.execute("PRAGMA wal_checkpoint(TRUNCATE);")
    source.backup(dest)
    source.close()
    dest.execute("PRAGMA wal_checkpoint(TRUNCATE);")
    dest.commit()
    dest.close()
    if os.path.exists(temp_in): os.remove(temp_in)

def init_db():
    conn = get_db_connection()
    c = conn.cursor()
    c.execute("PRAGMA foreign_keys = ON;")

    c.execute('''CREATE TABLE IF NOT EXISTS locations (
        location_id TEXT PRIMARY KEY, location_type TEXT NOT NULL CHECK(location_type IN ('COUNTRY', 'STATE', 'CITY')),
        location_name TEXT NOT NULL, parent_id TEXT, country_code TEXT DEFAULT 'IND',
        intransitivity_idx REAL DEFAULT 0.0, hawking_offset REAL DEFAULT 0.0, suggested_offset REAL DEFAULT 0.0,
        readiness_score REAL DEFAULT 0.0, active_bridge_count INTEGER DEFAULT 0, total_active_players INTEGER DEFAULT 0,
        total_matches_played INTEGER DEFAULT 0, active_venues_count INTEGER DEFAULT 0,
        median_latent_mmr REAL DEFAULT 3.000, highest_player_mmr REAL DEFAULT 3.000, lowest_player_mmr REAL DEFAULT 3.000,
        is_normalized INTEGER DEFAULT 0, updated_at TEXT, is_active INTEGER DEFAULT 1,
        FOREIGN KEY (parent_id) REFERENCES locations(location_id) ON DELETE SET NULL
    )''')

    c.execute('''CREATE TABLE IF NOT EXISTS venues (
        venue_id TEXT PRIMARY KEY, venue_name TEXT NOT NULL, raw_input_name TEXT, is_verified INTEGER DEFAULT 0,
        city_id TEXT NOT NULL, country_code TEXT NOT NULL, court_count INTEGER DEFAULT 1, total_matches_played INTEGER DEFAULT 0,
        unique_players_count INTEGER DEFAULT 0, city_bridge_matches_count INTEGER DEFAULT 0, country_bridge_matches_count INTEGER DEFAULT 0,
        average_player_mmr REAL DEFAULT 3.000, is_active INTEGER DEFAULT 1, created_at TEXT,
        FOREIGN KEY (city_id) REFERENCES locations(location_id) ON DELETE CASCADE
    )''')

    c.execute('''CREATE TABLE IF NOT EXISTS rating_categories (
        category_name TEXT PRIMARY KEY, min_rating REAL NOT NULL, max_rating REAL NOT NULL, 
        sort_order INTEGER NOT NULL, speed_multiplier REAL DEFAULT 1.00
    )''')

    c.execute('''CREATE TABLE IF NOT EXISTS match_formats (
        format_id TEXT PRIMARY KEY, format_name TEXT NOT NULL, category TEXT NOT NULL,
        mc_weight REAL NOT NULL, target_games INTEGER, total_points INTEGER, is_session_bound INTEGER DEFAULT 0, is_active INTEGER DEFAULT 1
    )''')

    c.execute('''CREATE TABLE IF NOT EXISTS players (
        player_id TEXT PRIMARY KEY, display_name TEXT NOT NULL, initial_rating REAL NOT NULL,
        home_venue_id TEXT, home_city_id TEXT NOT NULL, home_country_code TEXT NOT NULL DEFAULT 'IND',
        latent_mmr REAL NOT NULL, display_rating REAL NOT NULL,
        rolling_90d_peak REAL NOT NULL DEFAULT 3.000, rolling_180d_peak REAL NOT NULL DEFAULT 3.000, rolling_365d_peak REAL NOT NULL DEFAULT 3.000,
        tournament_floor REAL NOT NULL DEFAULT 0.000, all_time_badge TEXT DEFAULT 'Intermediate',
        rating_deviation REAL NOT NULL DEFAULT 350.000, rating_accuracy_pct REAL DEFAULT 0.0, accuracy_s_rd REAL DEFAULT 0.0,
        accuracy_s_matches REAL DEFAULT 0.0, accuracy_s_diversity REAL DEFAULT 0.0, calibration_tier TEXT DEFAULT 'PROVISIONAL',
        is_provisional INTEGER DEFAULT 1, is_manually_verified INTEGER DEFAULT 0, verified_matches_count INTEGER DEFAULT 0,
        unique_opponents_count INTEGER DEFAULT 0, unique_partners_count INTEGER DEFAULT 0, unique_venues_count INTEGER DEFAULT 0,
        unique_cities_count INTEGER DEFAULT 0, unique_countries_count INTEGER DEFAULT 0, bridge_matches_count INTEGER DEFAULT 0,
        is_active_bridge INTEGER DEFAULT 0, is_country_bridge INTEGER DEFAULT 0, graph_centrality REAL DEFAULT 0.20,
        is_quarantined INTEGER DEFAULT 0, is_anchor INTEGER DEFAULT 0, is_ceiling_anchor INTEGER DEFAULT 0, is_dummy INTEGER DEFAULT 0,
        last_match_time TEXT, created_at TEXT,
        FOREIGN KEY (home_venue_id) REFERENCES venues(venue_id) ON DELETE SET NULL, FOREIGN KEY (home_city_id) REFERENCES locations(location_id) ON DELETE CASCADE
    )''')

    c.execute('''CREATE TABLE IF NOT EXISTS sessions (
        session_id TEXT PRIMARY KEY, venue_id TEXT NOT NULL, session_title TEXT NOT NULL,
        session_date TEXT NOT NULL DEFAULT '', start_time TEXT NOT NULL DEFAULT '09:00', end_time TEXT NOT NULL DEFAULT '11:00',
        match_mode TEXT NOT NULL DEFAULT 'DOUBLES', team_format TEXT NOT NULL, format_id TEXT NOT NULL,
        tourney_structure TEXT NOT NULL DEFAULT 'ROUND_ROBIN', is_tournament INTEGER DEFAULT 0, court_ids_json TEXT NOT NULL,
        enrolled_player_ids TEXT NOT NULL, teams_json TEXT DEFAULT '[]', checked_in_player_ids TEXT DEFAULT '[]',
        player_count INTEGER NOT NULL, active_checked_in_count INTEGER DEFAULT 0, total_rounds INTEGER NOT NULL DEFAULT 1,
        current_round INTEGER DEFAULT 0, session_status TEXT DEFAULT 'CONFIG' CHECK(session_status IN ('CONFIG', 'LIVE', 'COMPLETED', 'CANCELLED')),
        created_at TEXT NOT NULL, completed_at TEXT,
        FOREIGN KEY (venue_id) REFERENCES venues(venue_id), FOREIGN KEY (format_id) REFERENCES match_formats(format_id)
    )''')

    c.execute('''CREATE TABLE IF NOT EXISTS session_matches (
        session_match_id TEXT PRIMARY KEY, session_id TEXT NOT NULL, round_number INTEGER NOT NULL,
        court_id TEXT NOT NULL, match_order INTEGER NOT NULL, team_a_p1_id TEXT NOT NULL, team_a_p2_id TEXT,
        team_b_p1_id TEXT NOT NULL, team_b_p2_id TEXT, team_a_name TEXT DEFAULT '', team_b_name TEXT DEFAULT '',
        score_team_a INTEGER DEFAULT 0, score_team_b INTEGER DEFAULT 0,
        games_winner INTEGER DEFAULT 0, games_loser INTEGER DEFAULT 0, set_scores_json TEXT DEFAULT '[]',
        match_status TEXT DEFAULT 'SCHEDULED' CHECK(match_status IN ('SCHEDULED', 'LIVE', 'STAGED', 'COMMITTED', 'CANCELLED')),
        started_at TEXT, completed_at TEXT, committed_match_id TEXT,
        FOREIGN KEY (session_id) REFERENCES sessions(session_id) ON DELETE CASCADE
    )''')

    c.execute('''CREATE TABLE IF NOT EXISTS matches (
        match_id TEXT PRIMARY KEY, venue_id TEXT NOT NULL, format_id TEXT NOT NULL, session_id TEXT,
        is_singles INTEGER DEFAULT 0, is_tournament INTEGER DEFAULT 0, is_venue_bridge INTEGER DEFAULT 0,
        is_city_bridge INTEGER DEFAULT 0, is_country_bridge INTEGER DEFAULT 0, team_a_p1_id TEXT NOT NULL, team_a_p2_id TEXT,
        team_b_p1_id TEXT NOT NULL, team_b_p2_id TEXT, score_team_a INTEGER DEFAULT 0, score_team_b INTEGER DEFAULT 0,
        set_scores_json TEXT DEFAULT '[]', games_winner INTEGER DEFAULT 0, games_loser INTEGER DEFAULT 0,
        pre_rating_a REAL DEFAULT 3.000, pre_rating_b REAL DEFAULT 3.000, win_expectancy_a REAL DEFAULT 0.5000,
        applied_m_c REAL DEFAULT 1.00, applied_s_margin REAL DEFAULT 1.000, applied_ice_out_p1 REAL DEFAULT 1.00,
        applied_ice_out_p2 REAL DEFAULT 1.00, applied_ice_out_p3 REAL DEFAULT 1.00, applied_ice_out_p4 REAL DEFAULT 1.00,
        applied_g_buffer REAL DEFAULT 1.000, delta_r_p1 REAL DEFAULT 0.0, delta_r_p2 REAL DEFAULT 0.0,
        delta_r_p3 REAL DEFAULT 0.0, delta_r_p4 REAL DEFAULT 0.0, guardrails_summary TEXT DEFAULT '[]',
        host_id TEXT, sponsor_id TEXT, is_retroactive INTEGER DEFAULT 0, processed_at TEXT, match_timestamp TEXT NOT NULL,
        FOREIGN KEY (venue_id) REFERENCES venues(venue_id) ON DELETE RESTRICT, FOREIGN KEY (format_id) REFERENCES match_formats(format_id) ON DELETE RESTRICT
    )''')

    c.execute('''CREATE TABLE IF NOT EXISTS match_logs (
        log_id TEXT PRIMARY KEY, match_id TEXT NOT NULL, player_id TEXT NOT NULL, pre_latent_mmr REAL NOT NULL,
        post_latent_mmr REAL NOT NULL, pre_display_rating REAL NOT NULL, post_display_rating REAL NOT NULL,
        pre_rd REAL NOT NULL, post_rd REAL NOT NULL, pre_accuracy_pct REAL NOT NULL, post_accuracy_pct REAL NOT NULL,
        delta_r REAL NOT NULL, is_elevator_active INTEGER DEFAULT 0, guardrails_triggered TEXT DEFAULT '[]',
        is_retroactive INTEGER DEFAULT 0, logged_at TEXT NOT NULL, FOREIGN KEY (match_id) REFERENCES matches(match_id) ON DELETE CASCADE,
        FOREIGN KEY (player_id) REFERENCES players(player_id) ON DELETE CASCADE
    )''')

    c.execute('''CREATE TABLE IF NOT EXISTS global_config (param_key TEXT PRIMARY KEY, param_value REAL NOT NULL, is_active INTEGER DEFAULT 1, title TEXT, description TEXT, tuning_guide TEXT, module_group TEXT)''')
    c.execute('''CREATE TABLE IF NOT EXISTS progression_speed_rules (rule_id INTEGER PRIMARY KEY AUTOINCREMENT, min_rating REAL NOT NULL, max_rating REAL NOT NULL, speed_multiplier REAL NOT NULL, description TEXT, is_active INTEGER DEFAULT 1, created_at TEXT NOT NULL)''')
    c.execute('''CREATE TABLE IF NOT EXISTS config_changelog (log_id INTEGER PRIMARY KEY AUTOINCREMENT, param_key TEXT NOT NULL, old_value REAL NOT NULL, new_value REAL NOT NULL, changed_by TEXT NOT NULL, changed_at TEXT NOT NULL)''')
    c.execute('''CREATE TABLE IF NOT EXISTS player_changelog (log_id INTEGER PRIMARY KEY AUTOINCREMENT, player_id TEXT NOT NULL, change_type TEXT NOT NULL, old_val TEXT, new_val TEXT, changed_by TEXT NOT NULL, changed_at TEXT NOT NULL)''')

    add_column_if_not_exists(c, "rating_categories", "speed_multiplier", "REAL DEFAULT 1.00")
    add_column_if_not_exists(c, "sessions", "session_date", "TEXT NOT NULL DEFAULT ''")
    add_column_if_not_exists(c, "sessions", "start_time", "TEXT NOT NULL DEFAULT '09:00'")
    add_column_if_not_exists(c, "sessions", "end_time", "TEXT NOT NULL DEFAULT '11:00'")
    add_column_if_not_exists(c, "sessions", "is_tournament", "INTEGER DEFAULT 0")
    add_column_if_not_exists(c, "sessions", "teams_json", "TEXT DEFAULT '[]'")
    add_column_if_not_exists(c, "session_matches", "team_a_name", "TEXT DEFAULT ''")
    add_column_if_not_exists(c, "session_matches", "team_b_name", "TEXT DEFAULT ''")
    add_column_if_not_exists(c, "session_matches", "games_winner", "INTEGER DEFAULT 0")
    add_column_if_not_exists(c, "session_matches", "games_loser", "INTEGER DEFAULT 0")
    add_column_if_not_exists(c, "players", "rolling_90d_peak", "REAL NOT NULL DEFAULT 3.000")
    add_column_if_not_exists(c, "players", "rolling_180d_peak", "REAL NOT NULL DEFAULT 3.000")
    add_column_if_not_exists(c, "players", "rolling_365d_peak", "REAL NOT NULL DEFAULT 3.000")
    add_column_if_not_exists(c, "players", "tournament_floor", "REAL NOT NULL DEFAULT 0.000")

    cat_count = c.execute("SELECT COUNT(*) FROM rating_categories").fetchone()[0]
    if cat_count == 0:
        default_cats = [
            ("Beginner", 0.000, 0.999, 1, 1.00), ("Beginner+", 1.000, 1.999, 2, 1.00), ("Intermediate", 2.000, 3.499, 3, 1.00),
            ("Intermediate+", 3.500, 4.499, 4, 1.00), ("Advanced", 4.500, 5.499, 5, 1.00), ("Pro", 5.500, 6.299, 6, 1.00), ("Elite", 6.300, 7.000, 7, 1.00)
        ]
        for c_name, c_min, c_max, s_ord, s_mult in default_cats:
            c.execute("INSERT OR IGNORE INTO rating_categories (category_name, min_rating, max_rating, sort_order, speed_multiplier) VALUES (?, ?, ?, ?, ?)",
                      (c_name, c_min, c_max, s_ord, s_mult))

    official_formats = [
        ("STD_B03", "Best of 3 Sets", "MULTI_SET", 1.00, None, None, 0, 1), ("STD_B05", "Best of 5 Sets", "MULTI_SET", 1.00, None, None, 0, 1),
        ("RACE_4", "Race to 4 Games", "RACE_GAMES", 0.50, 4, None, 0, 1), ("RACE_5", "Race to 5 Games", "RACE_GAMES", 0.60, 5, None, 0, 1),
        ("RACE_6", "Race to 6 Games", "RACE_GAMES", 0.70, 6, None, 0, 1), ("RACE_7", "Race to 7 Games", "RACE_GAMES", 0.80, 7, None, 0, 1),
        ("RACE_9", "Race to 9 Games", "RACE_GAMES", 0.80, 9, None, 0, 1), ("RACE_11", "Race to 11 Games", "RACE_GAMES", 0.90, 11, None, 0, 1),
        ("AMER_12", "Americano 12 Points", "AMERICANO", 0.30, None, 12, 1, 1), ("MEX_12", "Mexicano 12 Points", "MEXICANO", 0.30, None, 12, 1, 1),
        ("AMER_16", "Americano 16 Points", "AMERICANO", 0.30, None, 16, 1, 1), ("MEX_16", "Mexicano 16 Points", "MEXICANO", 0.30, None, 16, 1, 1),
        ("AMER_20", "Americano 20 Points", "AMERICANO", 0.30, None, 20, 1, 1), ("MEX_20", "Mexicano 20 Points", "MEXICANO", 0.30, None, 20, 1, 1),
        ("AMER_24", "Americano 24 Points", "AMERICANO", 0.30, None, 24, 1, 1), ("MEX_24", "Mexicano 24 Points", "MEXICANO", 0.30, None, 24, 1, 1),
        ("AMER_28", "Americano 28 Points", "AMERICANO", 0.30, None, 28, 1, 1), ("MEX_28", "Mexicano 28 Points", "MEXICANO", 0.30, None, 28, 1, 1)
    ]
    for fid, fname, cat, mc, tg, tp, is_sb, is_a in official_formats:
        c.execute("""INSERT INTO match_formats (format_id, format_name, category, mc_weight, target_games, total_points, is_session_bound, is_active)
                     VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                     ON CONFLICT(format_id) DO UPDATE SET format_name=excluded.format_name, category=excluded.category, target_games=excluded.target_games, total_points=excluded.total_points, is_session_bound=excluded.is_session_bound, is_active=excluded.is_active""",
                  (fid, fname, cat, mc, tg, tp, is_sb, is_a))

    seed_factory_parameters(c, overwrite_existing=False)
    conn.commit()
    conn.close()

def seed_factory_parameters(cursor, overwrite_existing=False):
    master_params = [
        ("R_MIN", 0.000, 1, "Scale Absolute Floor", "Lowest possible rating.", "Clamps rating drops at 0.000.", "1. Core Bounds & Drag"),
        ("R_MAX", 7.000, 1, "Scale Absolute Ceiling", "Maximum rating ceiling.", "LOCKED at 7.000.", "1. Core Bounds & Drag"),
        ("R_ELITE_THRESHOLD", 6.300, 1, "Elite Drag Gate", "Rating where exponential drag starts.", "Lowering applies drag earlier.", "1. Core Bounds & Drag"),
        ("ELITE_DRAG_EXPONENT", 2.5, 1, "Elite Drag Curvature", "Steepness of ceiling resistance.", "Higher values make 7.000 impossible to reach.", "1. Core Bounds & Drag"),
        ("POWER_MEAN_P", 3.0, 1, "Doubles Cubic Exponent", "Power mean anchor exponent.", "3.0 gives 70/30 anchor bias.", "2. Volatility & Odds"),
        ("LOGISTIC_BETA", 2.0, 1, "Logistic Scale Factor", "Odds curve steepness.", "Lowering boosts upset deltas.", "2. Volatility & Odds"),
        ("K_MAX", 0.400, 1, "Beginner Max Volatility", "Step size at R=0.000.", "Higher values accelerate beginner progression.", "2. Volatility & Odds"),
        ("K_MIN", 0.080, 1, "Pro Min Volatility", "Step size at R=7.000.", "Lower values lock pro ratings tighter.", "2. Volatility & Odds"),
        ("MARGIN_BASE", 0.80, 1, "Margin Floor Factor", "Min score factor for close matches.", "Points floor for tight finishes.", "3. Margins & Rightsizing"),
        ("MARGIN_SCALE", 0.40, 1, "Margin Blowout Scale", "Max bonus factor for blowouts.", "Full blowout bonus = Base + Scale.", "3. Margins & Rightsizing"),
        ("MAX_PROVISIONAL_DELTA", 0.750, 1, "Placement Ceiling", "Max points won in interpolation.", "Single-match placement cap for smurfs.", "3. Margins & Rightsizing"),
        ("PROVISIONAL_ABSORPTION_ALPHA", 0.45, 1, "Rightsizing Velocity", "Speed toward performance rating.", "Higher = faster rightsizing.", "3. Margins & Rightsizing"),
        ("MAX_8H_EXCHANGE_CAP", 0.000, 0, "8H Exchange Cap", "Tight-window point transfer cap.", "Active when > 0.000.", "4. Exchange Caps & Security"),
        ("MAX_12H_EXCHANGE_CAP", 0.000, 0, "12H Exchange Cap", "Half-day point transfer cap.", "Active when > 0.000.", "4. Exchange Caps & Security"),
        ("MAX_24H_EXCHANGE_CAP", 0.150, 1, "24H Casual Cap", "Net transfer ceiling between 4 players.", "Prevents farming.", "4. Exchange Caps & Security"),
        ("MAX_48H_EXCHANGE_CAP", 0.000, 0, "48H Exchange Cap", "Weekend point transfer cap.", "Active when > 0.000.", "4. Exchange Caps & Security"),
        ("PROVISIONAL_CAP_MULTIPLIER", 2.5, 1, "Provisional Cap Relaxer", "Multiplier on 24H cap for PRs.", "Allows 0.375 point movement.", "4. Exchange Caps & Security"),
        ("SESSION_EXCHANGE_CAP", 0.300, 1, "Verified Session Cap", "Cap for verified club events.", "Doubles point limits for mixers.", "4. Exchange Caps & Security"),
        ("MIN_SESSION_PLAYERS", 6, 1, "Session Participant Floor", "Min players required to unlock session cap.", "Events with fewer revert to 0.150.", "4. Exchange Caps & Security"),
        ("PROVISIONAL_BYPASS_EXCHANGE_CAP", 1, 1, "Provisional Cap Bypass", "Allows unrated blowouts to bypass casual cap.", "Overrides 0.150 cap.", "4. Exchange Caps & Security"),
        ("ADMIN_OVERRIDE_MAX", 0.075, 1, "Admin Sandbox Shift Window", "Max human-approved offset.", "Ceiling for manual deployments.", "4. Exchange Caps & Security"),
        ("TOURNAMENT_MULTIPLIER_ACTIVE", 1, 1, "Tournament Multiplier Toggle", "Activates stakes multiplier for tournament play.", "1 = Active, 0 = Inactive.", "4. Exchange Caps & Security"),
        ("TOURNAMENT_STAKES_MULTIPLIER", 1.15, 1, "Tournament Stakes Multiplier", "Rating delta multiplier for tournament matches.", "Default 1.15 (+15%).", "4. Exchange Caps & Security"),
        ("RD_MIN", 30.0, 1, "Certainty Floor", "Absolute uncertainty floor.", "Prevents RD dropping below 30.0.", "5. Uncertainty & Rust"),
        ("RD_MAX", 350.0, 1, "Unrated Starting RD", "Uncertainty assigned at registration.", "Starting uncertainty.", "5. Uncertainty & Rust"),
        ("RD_INFO_VARIANCE", 65.0, 1, "Contraction Speed", "Denominator in RD shrinkage.", "Lower values shrink RD faster.", "5. Uncertainty & Rust"),
        ("INACTIVITY_CONSTANT", 12.0, 1, "Inactivity Rust Rate", "Monthly uncertainty growth.", "Points of RD regained per month.", "5. Uncertainty & Rust"),
        ("COHORT_FACTOR_0_PROV", 1.00, 1, "Omega 0 Factor", "Contraction speed against verified anchors.", "100% information gain.", "5. Uncertainty & Rust"),
        ("COHORT_FACTOR_1_PROV", 0.75, 1, "Omega 1 Factor", "Contraction speed with 1 unrated player.", "75% information gain.", "5. Uncertainty & Rust"),
        ("COHORT_FACTOR_2_PROV", 0.50, 1, "Omega 2 Factor", "Contraction speed with 2 unrated players.", "50% information gain.", "5. Uncertainty & Rust"),
        ("COHORT_FACTOR_3_PROV", 0.25, 1, "Omega 3 Factor", "Contraction speed with 3+ unrated players.", "25% sandbox mode.", "5. Uncertainty & Rust"),
        ("PROVISIONAL_RD_CONTRACTION_RATIO", 0.35, 1, "Provisional RD Shrink Modifier", "Slows RD drop for unrated players.", "Lower values keep players provisional longer.", "6. Accuracy & Tri-Gates"),
        ("PROVISIONAL_ACCURACY_DAMPENER", 0.40, 1, "Provisional Accuracy Gain Cap", "Restricts accuracy gain during placement.", "Caps visual accuracy.", "6. Accuracy & Tri-Gates"),
        ("ACCURACY_WEIGHT_RD", 0.50, 1, "Accuracy Weight: RD", "Weight for Pillar 1 (Certainty).", "Controls influence of RD.", "6. Accuracy & Tri-Gates"),
        ("ACCURACY_WEIGHT_MATCHES", 0.25, 1, "Accuracy Weight: Matches", "Weight for Pillar 2 (Match Depth).", "Controls importance of volume.", "6. Accuracy & Tri-Gates"),
        ("ACCURACY_WEIGHT_DIVERSITY", 0.25, 1, "Accuracy Weight: Diversity", "Weight for Pillar 3 (Network).", "Controls importance of unique opponents.", "6. Accuracy & Tri-Gates"),
        ("TIER_PROVISIONAL_MAX", 69.99, 1, "Provisional Score Ceiling", "Upper score bound for Tier 1.", "Players below remain [PR].", "6. Accuracy & Tri-Gates"),
        ("TIER_VERIFIED_MAX", 89.99, 1, "Verified Score Ceiling", "Upper score bound for Tier 2.", "Score required to reach Anchor.", "6. Accuracy & Tri-Gates"),
        ("TARGET_MATCHES_PROVISIONAL", 3, 1, "Target Matches: Provisional", "Match quota during onboarding.", "Satisfies depth during placement.", "6. Accuracy & Tri-Gates"),
        ("TARGET_OPPONENTS_PROVISIONAL", 2, 1, "Target Opponents: Provisional", "Opponent quota during onboarding.", "Satisfies diversity during placement.", "6. Accuracy & Tri-Gates"),
        ("TARGET_MATCHES_VERIFIED", 5, 1, "Target Matches: Verified", "Match quota for Verified tier.", "Required to reach Verified.", "6. Accuracy & Tri-Gates"),
        ("TARGET_OPPONENTS_VERIFIED", 3, 1, "Target Opponents: Verified", "Opponent quota for Verified tier.", "Required to reach Verified.", "6. Accuracy & Tri-Gates"),
        ("TARGET_MATCHES_ANCHOR", 15, 1, "Target Matches: Anchor", "Match quota for Anchor tier.", "Required to reach Anchor.", "6. Accuracy & Tri-Gates"),
        ("TARGET_OPPONENTS_ANCHOR", 8, 1, "Target Opponents: Anchor", "Opponent quota for Anchor tier.", "Required to reach Anchor.", "6. Accuracy & Tri-Gates"),
        ("PROVISIONAL_RD_GATE", 100.0, 1, "Tri-Gate Max RD", "RD must be <= 100 to exit [PR].", "Uncertainty ceiling to graduate.", "6. Accuracy & Tri-Gates"),
        ("PROVISIONAL_MIN_MATCHES", 10, 1, "Tri-Gate Min Matches", "Verified matches to exit [PR].", "Volume required to shed badge.", "6. Accuracy & Tri-Gates"),
        ("PROVISIONAL_MIN_OPPONENTS", 5, 1, "Tri-Gate Min Opponents", "Unique opponents to exit [PR].", "Distinct opponents required.", "6. Accuracy & Tri-Gates"),
        ("ISLAND_ACCURACY_CAP", 80.0, 1, "Island Geographic Cap", "Max accuracy if City has 0 bridges.", "Caps accuracy until cross-city play.", "6. Accuracy & Tri-Gates"),
        ("BRIDGE_RD_THRESHOLD", 80.0, 1, "Bridge Max RD", "Max RD to qualify as Bridge.", "Only players with RD <= 80 count.", "7. Hawking Macro"),
        ("BRIDGE_MIN_MATCHES", 5, 1, "Bridge Min Matches", "Away matches required to link cities.", "Matches required before linking.", "7. Hawking Macro"),
        ("LAMBDA_BRIDGE_DAMPING", 3.0, 1, "Tikhonov Lambda", "Shock absorber parameter.", "Requires more travelers to deploy offsets.", "7. Hawking Macro"),
        ("CIRCUIT_BREAKER", 0.0250, 1, "Auto Cron Safety Ceiling", "Max shift per weekly cycle.", "Limits automated macro shifts.", "7. Hawking Macro")
    ]
    for k, v, act, tit, desc, tune, grp in master_params:
        if overwrite_existing:
            cursor.execute("""INSERT INTO global_config (param_key, param_value, is_active, title, description, tuning_guide, module_group)
                              VALUES (?, ?, ?, ?, ?, ?, ?)
                              ON CONFLICT(param_key) DO UPDATE SET param_value=excluded.param_value, is_active=excluded.is_active, title=excluded.title, description=excluded.description, tuning_guide=excluded.tuning_guide, module_group=excluded.module_group""", (k, v, act, tit, desc, tune, grp))
        else:
            cursor.execute("""INSERT INTO global_config (param_key, param_value, is_active, title, description, tuning_guide, module_group)
                              VALUES (?, ?, ?, ?, ?, ?, ?)
                              ON CONFLICT(param_key) DO UPDATE SET title=excluded.title, description=excluded.description, tuning_guide=excluded.tuning_guide, module_group=excluded.module_group""", (k, v, act, tit, desc, tune, grp))

init_db()

# ==============================================================================
# 2. V.16 CALCULATION ENGINE
# ==============================================================================
class RyftV16:
    @staticmethod
    def get_configs(conn=None):
        owns_conn = False
        if conn is None:
            conn = get_db_connection()
            owns_conn = True
        rows = conn.execute("SELECT param_key, param_value FROM global_config WHERE is_active = 1").fetchall()
        if owns_conn: conn.close()
        return {r["param_key"]: r["param_value"] for r in rows}

    @staticmethod
    def get_cat_for_rating(r_val, conn=None):
        owns_conn = False
        if conn is None:
            conn = get_db_connection()
            owns_conn = True
        cats = conn.execute("SELECT category_name, min_rating, max_rating, speed_multiplier FROM rating_categories ORDER BY sort_order ASC").fetchall()
        if owns_conn: conn.close()
        for c in cats:
            if c["min_rating"] <= r_val <= c["max_rating"]:
                return c["category_name"], c["min_rating"], c["max_rating"], c["speed_multiplier"] or 1.00
        if cats:
            if r_val < cats[0]["min_rating"]: return cats[0]["category_name"], cats[0]["min_rating"], cats[0]["max_rating"], cats[0]["speed_multiplier"] or 1.00
            return cats[-1]["category_name"], cats[-1]["min_rating"], cats[-1]["max_rating"], cats[-1]["speed_multiplier"] or 1.00
        return "Intermediate", 2.000, 3.499, 1.00

    @staticmethod
    def calc_accuracy(rd, m_count, opp_count, is_prov, k_bridges, cfg):
        s_rd = max(0.0, min(1.0, (cfg.get("RD_MAX", 350.0) - rd) / (cfg.get("RD_MAX", 350.0) - cfg.get("RD_MIN", 30.0))))
        t_m = cfg.get("TARGET_MATCHES_PROVISIONAL", 3) if is_prov else cfg.get("TARGET_MATCHES_ANCHOR", 15)
        t_o = cfg.get("TARGET_OPPONENTS_PROVISIONAL", 2) if is_prov else cfg.get("TARGET_OPPONENTS_ANCHOR", 8)
        s_m = min(1.0, m_count / float(t_m))
        s_d = min(1.0, opp_count / float(t_o))
        
        raw_acc = (cfg.get("ACCURACY_WEIGHT_RD", 0.50) * s_rd + cfg.get("ACCURACY_WEIGHT_MATCHES", 0.25) * s_m + cfg.get("ACCURACY_WEIGHT_DIVERSITY", 0.25) * s_d) * 100.0
        if is_prov: raw_acc *= cfg.get("PROVISIONAL_ACCURACY_DAMPENER", 0.40)
        phi = min(1.0, cfg.get("ISLAND_ACCURACY_CAP", 80.0) / 100.0) if k_bridges == 0 else min(1.0, 0.80 + (0.10 * k_bridges))
        return round(raw_acc * phi, 1), round(s_rd * 100.0, 1), round(s_m * 100.0, 1), round(s_d * 100.0, 1)

    @staticmethod
    def sync_player_aggregates(player_id, conn=None):
        owns_conn = False
        if conn is None:
            conn = get_db_connection()
            owns_conn = True
        m_count = conn.execute("SELECT COUNT(*) FROM matches WHERE team_a_p1_id = ? OR team_a_p2_id = ? OR team_b_p1_id = ? OR team_b_p2_id = ?", (player_id, player_id, player_id, player_id)).fetchone()[0]
        opp_count = conn.execute("""
            SELECT COUNT(DISTINCT opp_id) FROM (
                SELECT team_b_p1_id as opp_id FROM matches WHERE team_a_p1_id = ? OR team_a_p2_id = ? UNION
                SELECT team_b_p2_id as opp_id FROM matches WHERE (team_a_p1_id = ? OR team_a_p2_id = ?) AND team_b_p2_id IS NOT NULL UNION
                SELECT team_a_p1_id as opp_id FROM matches WHERE team_b_p1_id = ? OR team_b_p2_id = ? UNION
                SELECT team_a_p2_id as opp_id FROM matches WHERE (team_b_p1_id = ? OR team_b_p2_id = ?) AND team_a_p2_id IS NOT NULL
            ) WHERE opp_id IS NOT NULL AND opp_id != ?
        """, (player_id, player_id, player_id, player_id, player_id, player_id, player_id, player_id, player_id)).fetchone()[0]

        p_row = conn.execute("SELECT home_city_id, home_country_code, rating_deviation, latent_mmr, is_manually_verified FROM players WHERE player_id = ?", (player_id,)).fetchone()
        cross_city_matches, cross_country_matches = 0, 0
        if p_row:
            cross_city_matches = conn.execute("SELECT COUNT(*) FROM matches m JOIN venues v ON m.venue_id = v.venue_id WHERE (m.team_a_p1_id = ? OR m.team_a_p2_id = ? OR m.team_b_p1_id = ? OR m.team_b_p2_id = ?) AND v.city_id != ?", (player_id, player_id, player_id, player_id, p_row["home_city_id"])).fetchone()[0]
            cross_country_matches = conn.execute("SELECT COUNT(*) FROM matches m JOIN venues v ON m.venue_id = v.venue_id WHERE (m.team_a_p1_id = ? OR m.team_a_p2_id = ? OR m.team_b_p1_id = ? OR m.team_b_p2_id = ?) AND v.country_code != ?", (player_id, player_id, player_id, player_id, p_row["home_country_code"])).fetchone()[0]

        is_act_city_bridge = 1 if (p_row and p_row["rating_deviation"] <= 80.0 and cross_city_matches >= 5) else 0
        is_act_ctry_bridge = 1 if (p_row and p_row["rating_deviation"] <= 80.0 and cross_country_matches >= 3) else 0
        cur_cat, _, _, _ = RyftV16.get_cat_for_rating(p_row["latent_mmr"] if p_row else 3.0, conn=conn)

        conn.execute("""UPDATE players SET verified_matches_count=?, unique_opponents_count=?, bridge_matches_count=?, is_active_bridge=?, is_country_bridge=?, all_time_badge=? WHERE player_id=?""",
                     (m_count, opp_count, cross_city_matches, is_act_city_bridge, is_act_ctry_bridge, cur_cat, player_id))
        if owns_conn:
            conn.commit()
            conn.close()

    @classmethod
    def compute_match(cls, p1_raw, p2_raw, p3_raw, p4_raw, s_a, s_b, g_w_raw, g_l_raw, fmt_id, v_id, is_singles, is_dry=False, is_tournament=False, session_id=None, session_checked_in=0, conn=None, cumulative_deltas=None):
        owns_conn = False
        if conn is None:
            conn = get_db_connection()
            owns_conn = True

        p1, p3 = dict(p1_raw), dict(p3_raw)
        p2 = dict(p2_raw) if p2_raw is not None else None
        p4 = dict(p4_raw) if p4_raw is not None else None

        cfg = cls.get_configs(conn=conn)
        p_exp = cfg.get("POWER_MEAN_P", 3.0)
        g_w, g_l = max(g_w_raw, g_l_raw + 1), g_l_raw
        
        ta_r = float(p1.get("latent_mmr", 3.0)) if is_singles else ((float(p1.get("latent_mmr", 3.0))**p_exp + float(p2.get("latent_mmr", 3.0))**p_exp) / 2.0)**(1.0 / p_exp)
        tb_r = float(p3.get("latent_mmr", 3.0)) if is_singles else ((float(p3.get("latent_mmr", 3.0))**p_exp + float(p4.get("latent_mmr", 3.0))**p_exp) / 2.0)**(1.0 / p_exp)
        
        beta = cfg.get("LOGISTIC_BETA", 2.0)
        ea = 1.0 / (1.0 + 10.0**((tb_r - ta_r) / beta))
        act_a = 1.0 if s_a > s_b else (0.5 if s_a == s_b else 0.0)
        
        tot_g = g_w + g_l
        m_base, m_scale = cfg.get("MARGIN_BASE", 0.80), cfg.get("MARGIN_SCALE", 0.40)
        s_margin = max(0.80, min(1.20, m_base + (m_scale * ((g_w - g_l) / tot_g)) if tot_g > 0 else 1.0))
        
        fmt_row = conn.execute("SELECT mc_weight FROM match_formats WHERE format_id = ?", (fmt_id,)).fetchone()
        mc = float(fmt_row["mc_weight"]) if fmt_row else 1.00

        opp_rd_b = max(float(p3.get("rating_deviation", 350.0)), float(p4.get("rating_deviation", 350.0))) if not is_singles else float(p3.get("rating_deviation", 350.0))
        opp_rd_a = max(float(p1.get("rating_deviation", 350.0)), float(p2.get("rating_deviation", 350.0))) if not is_singles else float(p1.get("rating_deviation", 350.0))

        participants = [(p1, True, p2, opp_rd_b), (p3, False, p4, opp_rd_a)]
        if not is_singles:
            participants.extend([(p2, True, p1, opp_rd_b), (p4, False, p3, opp_rd_a)])

        res = []
        prov_count = sum(1 for px, _, _, _ in participants if bool(px.get("is_provisional", 1)))
        o_map = [cfg.get("COHORT_FACTOR_0_PROV", 1.0), cfg.get("COHORT_FACTOR_1_PROV", 0.75), cfg.get("COHORT_FACTOR_2_PROV", 0.50), cfg.get("COHORT_FACTOR_3_PROV", 0.25)]
        omega = o_map[min(3, max(0, prov_count - 1))]

        for p, is_a, partner, opp_rd in participants:
            flags = []
            r, rd, prov = float(p.get("latent_mmr", 3.0)), float(p.get("rating_deviation", 350.0)), bool(p.get("is_provisional", 1))
            is_manual_override = bool(p.get("is_manually_verified", 0))
            won = (is_a and s_a > s_b) or (not is_a and s_b > s_a)
            
            q, sig = 0.0057565, cfg.get("RD_INFO_VARIANCE", 65.0)
            g_opp = 1.0 / math.sqrt(1.0 + (3.0 * (q**2) * (opp_rd**2)) / (math.pi**2))
            
            if prov and s_a != s_b:
                opp_team_r = tb_r if is_a else ta_r
                ratio = (g_w_raw + 0.5) / (g_l_raw + 0.5) if won else (g_l_raw + 0.5) / (g_w_raw + 0.5)
                r_perf = opp_team_r + 2.0 * math.log10(ratio)
                alpha = cfg.get("PROVISIONAL_ABSORPTION_ALPHA", 0.45)
                raw_d = (r_perf - r) * alpha * mc * g_opp
                max_d = cfg.get("MAX_PROVISIONAL_DELTA", 0.750)
                raw_d = max(-max_d, min(max_d, raw_d))
                flags.append("RIGHTSIZING_INTERPOLATION")
            else:
                k_base = cfg.get("K_MAX", 0.400) - (r / cfg.get("R_MAX", 7.000)) * (cfg.get("K_MAX", 0.400) - cfg.get("K_MIN", 0.080))
                _, _, _, cat_speed = cls.get_cat_for_rating(r, conn=conn)
                if cat_speed != 1.00:
                    k_base *= cat_speed
                    flags.append(f"CAT_SPEED ({cat_speed:.2f}x)")

                drag = ((7.000 - r) / 7.000) * ((7.000 - r) / (7.000 - 6.300))**2.5 if r >= 6.300 else 1.0
                if r >= 6.300: flags.append("ELITE_DRAG")
                direction = 1.0 if is_a else -1.0
                raw_d = k_base * drag * mc * s_margin * g_opp * direction * (act_a - ea)

            if not is_singles and partner:
                gap = abs(r - float(partner.get("latent_mmr", 3.0)))
                if not won and r > float(partner.get("latent_mmr", 3.0)):
                    dd = 0.05 if gap >= 2.0 else (0.20 if gap >= 1.5 else (0.50 if gap >= 1.0 else 1.00))
                    raw_d *= dd
                    if dd < 1.00: flags.append(f"ICE_OUT_SHIELD ({int((1-dd)*100)}%)")
                elif won and r < float(partner.get("latent_mmr", 3.0)):
                    dd = 0.25 if gap >= 2.5 else (0.50 if gap >= 1.75 else (0.75 if gap >= 1.2 else 1.00))
                    raw_d *= dd
                    if dd < 1.00: flags.append(f"ANTI_CARRY ({int((1-dd)*100)}%)")

            # CUMULATIVE SESSION CAP LOGIC
            base_cap = cfg.get("SESSION_EXCHANGE_CAP", 0.300) if session_id and session_checked_in >= cfg.get("MIN_SESSION_PLAYERS", 6) else cfg.get("MAX_24H_EXCHANGE_CAP", 0.150)
            cap = base_cap * (cfg.get("PROVISIONAL_CAP_MULTIPLIER", 2.5) if prov else 1.0)
            
            if is_tournament and bool(cfg.get("TOURNAMENT_MULTIPLIER_ACTIVE", 1)):
                t_mult = cfg.get("TOURNAMENT_STAKES_MULTIPLIER", 1.15)
                final_d = raw_d * t_mult
                flags.append(f"TOURNAMENT ({t_mult}x, Uncapped)")
            elif cumulative_deltas is not None:
                cum_d = cumulative_deltas.get(p["player_id"], 0.0)
                # Ensure we don't breach [-cap, +cap] taking into account previous session matches
                final_d = max(-cap - cum_d, min(cap - cum_d, raw_d))
                if abs(cum_d + raw_d) > cap:
                    flags.append("SESSION_CUMULATIVE_CAP_ENFORCED")
            else:
                final_d = max(-cap, min(cap, raw_d))
                if abs(raw_d) > cap: flags.append("CAP_ENFORCED")

            new_r = max(0.000, min(6.999, r + final_d))
            prov_rd_shrink = cfg.get("PROVISIONAL_RD_CONTRACTION_RATIO", 0.35) if prov else 1.0
            new_rd = max(30.0, math.sqrt(1.0 / (1.0 / (rd**2) + (mc * s_margin * (g_opp**2) * omega * prov_rd_shrink) / (sig**2))))
            
            loc = conn.execute("SELECT active_bridge_count FROM locations WHERE location_id = ?", (p.get("home_city_id", ""),)).fetchone()
            bridge_k = loc["active_bridge_count"] if loc else 0
            
            nm = int(p.get("verified_matches_count", 0)) + (0 if is_dry else 1)
            no = int(p.get("unique_opponents_count", 0)) + (0 if is_dry else 1)
            acc_comp, a_rd, a_m, a_d = cls.calc_accuracy(new_rd, nm, no, prov, bridge_k, cfg)
            
            gate = (new_rd <= cfg.get("PROVISIONAL_RD_GATE", 100.0) and nm >= cfg.get("PROVISIONAL_MIN_MATCHES", 10) and no >= cfg.get("PROVISIONAL_MIN_OPPONENTS", 5))
            new_prov = 0 if (gate or is_manual_override) else 1
            tier = "ANCHOR" if (acc_comp >= 90.0 and new_prov == 0 and new_rd <= 60.0) else ("VERIFIED" if (acc_comp >= cfg.get("TIER_PROVISIONAL_MAX", 69.99) and new_prov == 0) else "PROVISIONAL")
            if is_manual_override and tier == "PROVISIONAL": tier = "VERIFIED"

            res.append({
                "pid": p["player_id"], "name": format_pr_name(p["display_name"], prov), "raw_name": p["display_name"],
                "pre_r": r, "post_r": new_r, "delta": final_d, "pre_rd": rd, "post_rd": new_rd,
                "acc": acc_comp, "a_rd": a_rd, "a_m": a_m, "a_d": a_d, "tier": tier, "prov": new_prov, "flags": flags
            })
            
        if owns_conn: conn.close()
        return {"ta_r": ta_r, "tb_r": tb_r, "ea": ea, "mov": s_margin, "applied_m_c": mc, "res": res}

# ==============================================================================
# 3. ADVANCED SESSIONS SCHEDULER & TRAFFIC ENGINE
# ==============================================================================
class SessionScheduleEngine:
    @staticmethod
    def generate_schedule(team_format, match_mode, enrolled_pids, teams_created, court_picks, rounds_count):
        fixtures = []
        fixture_order = 1
        num_courts = max(1, len(court_picks))

        if team_format == "FIXED_TEAMS":
            t_list = [dict(t) for t in teams_created]
            n_teams = len(t_list)
            if (n_teams % 2 != 0): t_list.append({"p1": None, "p2": None, "is_bye": True})
            n_eff = len(t_list)
            cycle_rounds = n_eff - 1
            for r_cycle in range(int(rounds_count)):
                for r_idx in range(cycle_rounds):
                    round_num = (r_cycle * cycle_rounds) + (r_idx + 1)
                    round_pairings = []
                    for i in range(n_eff // 2):
                        t1 = t_list[i]
                        t2 = t_list[n_eff - 1 - i]
                        if t1.get("is_bye") or t2.get("is_bye"): continue
                        round_pairings.append((t1, t2))
                    for m_idx, (ta, tb) in enumerate(round_pairings):
                        assigned_court = court_picks[m_idx % num_courts]
                        fixtures.append({"round_number": round_num, "court_id": assigned_court, "match_order": fixture_order, "team_a_p1_id": ta["p1"], "team_a_p2_id": ta["p2"], "team_b_p1_id": tb["p1"], "team_b_p2_id": tb["p2"]})
                        fixture_order += 1
                    t_list = [t_list[0]] + [t_list[-1]] + t_list[1:-1]

        elif team_format == "ROTATING_TEAMS" and match_mode == "DOUBLES":
            n_players = len(enrolled_pids)
            if n_players < 4: return []
            matches_played = {p: 0 for p in enrolled_pids}
            sat_out_last_round = {p: False for p in enrolled_pids}
            partner_matrix = {p1: {p2: 0 for p2 in enrolled_pids} for p1 in enrolled_pids}
            max_courts_usable = max(1, min(num_courts, n_players // 4))

            for round_num in range(1, int(rounds_count) + 1):
                def player_sort_key(pid): return (0 if sat_out_last_round[pid] else 1, matches_played[pid], pid)
                sorted_pids = sorted(enrolled_pids, key=player_sort_key)
                needed_players = max_courts_usable * 4
                active_players = sorted_pids[:needed_players]
                bench_players = sorted_pids[needed_players:]

                for p in enrolled_pids:
                    sat_out_last_round[p] = (p in bench_players)
                    if p in active_players: matches_played[p] += 1

                court_pool = list(active_players)
                for c_idx in range(max_courts_usable):
                    if len(court_pool) < 4: break
                    m_players = court_pool[:4]
                    court_pool = court_pool[4:]
                    combos = [
                        ((m_players[0], m_players[1]), (m_players[2], m_players[3])),
                        ((m_players[0], m_players[2]), (m_players[1], m_players[3])),
                        ((m_players[0], m_players[3]), (m_players[1], m_players[2]))
                    ]
                    def partnership_cost(combo):
                        (pa1, pa2), (pb1, pb2) = combo
                        return partner_matrix[pa1][pa2] + partner_matrix[pb1][pb2]
                    best_combo = min(combos, key=partnership_cost)
                    (ta_p1, ta_p2), (tb_p1, tb_p2) = best_combo
                    partner_matrix[ta_p1][ta_p2] += 1; partner_matrix[ta_p2][ta_p1] += 1
                    partner_matrix[tb_p1][tb_p2] += 1; partner_matrix[tb_p2][tb_p1] += 1
                    assigned_court = court_picks[c_idx % num_courts]
                    fixtures.append({"round_number": round_num, "court_id": assigned_court, "match_order": fixture_order, "team_a_p1_id": ta_p1, "team_a_p2_id": ta_p2, "team_b_p1_id": tb_p1, "team_b_p2_id": tb_p2})
                    fixture_order += 1
        return fixtures

# ==============================================================================
# 4. GUI INTERFACE & BRAND HEADER
# ==============================================================================
st.set_page_config(page_title="RYFT Engine V.16 Master", layout="wide")

RYFT_HEADER_SVG = """
<div style="text-align: center; padding: 10px 0 15px 0;">
<svg width="220" height="55" viewBox="0 0 400 100" xmlns="http://www.w3.org/2000/svg">
  <defs>
    <linearGradient id="ryftBlue" x1="0%" y1="0%" x2="100%" y2="100%">
      <stop offset="0%" style="stop-color:#0284c7;stop-opacity:1" />
      <stop offset="100%" style="stop-color:#1d4ed8;stop-opacity:1" />
    </linearGradient>
  </defs>
  <text x="15" y="75" font-family="-apple-system, BlinkMacSystemFont, sans-serif" font-size="82" font-weight="900" font-style="italic" fill="url(#ryftBlue)" letter-spacing="-3">RYFT</text>
  <rect x="225" y="28" width="80" height="30" rx="6" fill="#0f172a" />
  <text x="238" y="50" font-family="monospace" font-size="18" font-weight="700" fill="#38bdf8">V.16</text>
</svg>
</div>
"""
st.sidebar.markdown(RYFT_HEADER_SVG, unsafe_allow_html=True)

nav = st.sidebar.radio("Navigation Console", [
    "📊 The Dashboard", "🎾 Log Matches", "🗓️ Club Sessions & Mixers", "📜 Historical Matches", 
    "👥 Player Roster & Calibration", "🏢 Venues & Regions", "🌐 Hawking Engine", "⚙️ Global Config"
])

# ------------------------------------------------------------------------------
# TAB 1: THE DASHBOARD
# ------------------------------------------------------------------------------
if nav == "📊 The Dashboard":
    st.title("System Command Center & Macro Health")
    conn = get_db_connection()
    n_p = conn.execute("SELECT COUNT(*) FROM players WHERE calibration_tier != 'INACTIVE'").fetchone()[0]
    n_m = conn.execute("SELECT COUNT(*) FROM matches").fetchone()[0]
    n_s = conn.execute("SELECT COUNT(*) FROM sessions").fetchone()[0]
    n_v = conn.execute("SELECT COUNT(*) FROM venues WHERE is_active = 1").fetchone()[0]
    n_c = conn.execute("SELECT COUNT(*) FROM locations WHERE location_type = 'CITY'").fetchone()[0]
    n_co = conn.execute("SELECT COUNT(*) FROM locations WHERE location_type = 'COUNTRY'").fetchone()[0]
    conn.close()

    m1, m2, m3, m4, m5, m6 = st.columns(6)
    m1.metric("Active Players", n_p)
    m2.metric("Matches", n_m)
    m3.metric("Sessions", n_s)
    m4.metric("Venues", n_v)
    m5.metric("Cities", n_c)
    m6.metric("Countries", n_co)

    st.markdown("---")
    with st.expander("💾 Database Snapshot Backup & Restore (Zero Data Loss Architecture)", expanded=True):
        col_b1, col_b2 = st.columns(2)
        with col_b1:
            st.markdown("#### 📥 Backup Database Snapshot")
            if os.path.exists(DB_FILE):
                try:
                    db_bytes_to_download = export_db_bytes()
                    st.download_button("⬇️ Download System Snapshot (.db)", data=db_bytes_to_download, file_name=f"RYFT_V16_Backup_{datetime.now().strftime('%Y%m%d_%H%M%S')}.db", mime="application/x-sqlite3", use_container_width=True)
                except Exception as ex:
                    st.error(f"Error preparing snapshot: {ex}")

        with col_b2:
            st.markdown("#### 📤 Upload Saved State")
            up_db = st.file_uploader("Select .db file", type=["db", "sqlite", "sqlite3"])
            if up_db and st.button("🚨 Restore Entire System From Backup", type="primary", use_container_width=True):
                try:
                    restore_db_from_bytes(up_db.getbuffer())
                    st.success("✅ System Restored!")
                    st.rerun()
                except Exception as err:
                    st.error(f"Failed to restore database: {str(err)}")

    with st.expander("🚨 Advanced System Resets", expanded=False):
        r_c1, r_c2, r_c3 = st.columns(3)
        res_p = r_c1.checkbox("Reset All Players to Initial Rating")
        res_m = r_c2.checkbox("Delete Match History")
        res_n = r_c3.checkbox("💣 Clean Slate (Erase All Test Data)")

        if st.button("Execute Checked Resets", type="secondary"):
            conn = get_db_connection()
            if res_n:
                for tbl in ["match_logs", "matches", "session_matches", "sessions", "players", "venues", "locations", "progression_speed_rules", "config_changelog", "player_changelog"]:
                    conn.execute(f"DELETE FROM {tbl};")
                st.warning("Database completely wiped.")
            else:
                if res_m:
                    conn.execute("DELETE FROM match_logs;"); conn.execute("DELETE FROM matches;")
                    conn.execute("DELETE FROM session_matches;"); conn.execute("DELETE FROM sessions;")
                    st.warning("Match history erased.")
                if res_p:
                    conn.execute("UPDATE players SET latent_mmr = initial_rating, display_rating = initial_rating, rating_deviation = 350.0, verified_matches_count = 0, unique_opponents_count = 0, rating_accuracy_pct = 0.0, calibration_tier = 'PROVISIONAL', is_provisional = 1")
                    st.warning("Player ratings reset.")
            conn.commit(); conn.close(); st.rerun()

# ------------------------------------------------------------------------------
# TAB 2: LOG MATCHES
# ------------------------------------------------------------------------------
elif nav == "🎾 Log Matches":
    st.title("Log Matches & Real-Time Simulation Hub")

    conn = get_db_connection()
    venues = conn.execute("SELECT * FROM venues WHERE is_active = 1").fetchall()
    players = conn.execute("SELECT * FROM players WHERE calibration_tier != 'INACTIVE' ORDER BY display_name").fetchall()
    formats = conn.execute("SELECT * FROM match_formats WHERE is_active = 1 AND is_session_bound = 0").fetchall()
    conn.close()

    v_dict = {v["venue_name"]: dict(v) for v in venues}
    p_dict = {f"{format_pr_name(p['display_name'], p['is_provisional'])} (MMR: {p['latent_mmr']:.3f})": dict(p) for p in players}
    f_dict = {f["format_name"]: dict(f) for f in formats}

    st.markdown("#### 1. Match Schedule & Location")
    c_s1, c_s2, c_s3 = st.columns(3)
    match_date = c_s1.date_input("Match Date", value=date.today())
    match_time = c_s2.time_input("Match Time", value=datetime.now().time())
    ven_sel = c_s3.selectbox("Venue Facility", list(v_dict.keys()) if v_dict else ["No Venues Registered"])

    c_m1, c_m2, c_m3 = st.columns([1.5, 2, 1.5])
    match_mode = c_m1.radio("Game Configuration", ["2v2 Doubles", "1v1 Singles"], horizontal=True)
    is_singles = (match_mode == "1v1 Singles")
    fmt_sel = c_m2.selectbox("Official Scoring Format", list(f_dict.keys()) if f_dict else ["No Formats Active"])
    is_tourney = c_m3.checkbox("🏆 Tournament Match (Uncapped + Boost)", value=False)

    st.markdown("#### 2. Player Rosters")
    col_t1, col_t2 = st.columns(2)
    with col_t1:
        st.markdown("##### 🔵 Team A")
        p1_pick = st.selectbox("Player A1 (Required)", ["-- Select --"] + list(p_dict.keys()), key="p1_sel")
        p2_pick = st.selectbox("Player A2 (Teammate)", ["-- Select --"] + list(p_dict.keys()), key="p2_sel") if not is_singles else "-- None --"
    with col_t2:
        st.markdown("##### 🔴 Team B")
        p3_pick = st.selectbox("Player B1 (Required)", ["-- Select --"] + list(p_dict.keys()), key="p3_sel")
        p4_pick = st.selectbox("Player B2 (Teammate)", ["-- Select --"] + list(p_dict.keys()), key="p4_sel") if not is_singles else "-- None --"

    st.markdown("#### 3. Scorecard Entry")
    sel_f = f_dict[fmt_sel] if f_dict else None
    sets_data = []
    sa, sb, gw, gl = 0, 0, 0, 0

    if sel_f:
        cat = sel_f["category"]
        if cat == "MULTI_SET":
            s1_c1, s1_c2 = st.columns(2)
            s1a = s1_c1.number_input("Set 1: Team A", 0, 7, 6, key="s1a")
            s1b = s1_c2.number_input("Set 1: Team B", 0, 7, 3, key="s1b")
            sets_data.append((s1a, s1b))

            s2_c1, s2_c2 = st.columns(2)
            s2a = s2_c1.number_input("Set 2: Team A", 0, 7, 6, key="s2a")
            s2b = s2_c2.number_input("Set 2: Team B", 0, 7, 4, key="s2b")
            sets_data.append((s2a, s2b))

            sa = (1 if s1a > s1b else 0) + (1 if s2a > s2b else 0)
            sb = (1 if s1b > s1a else 0) + (1 if s2b > s2a else 0)

            if sa == 1 and sb == 1:
                st.warning("Sets tied 1-1. Set 3 decider unlocked:")
                s3_c1, s3_c2 = st.columns(2)
                s3a = s3_c1.number_input("Set 3: Team A", 0, 7, 6, key="s3a")
                s3b = s3_c2.number_input("Set 3: Team B", 0, 7, 4, key="s3b")
                sets_data.append((s3a, s3b))
                if s3a > s3b: sa += 1
                else: sb += 1

            gw = sum(x[0] for x in sets_data)
            gl = sum(x[1] for x in sets_data)

        elif cat == "RACE_GAMES":
            tg = sel_f["target_games"] or 6
            rg1, rg2 = st.columns(2)
            gw = rg1.number_input(f"Team A Games (Race to {tg})", 0, 30, tg)
            gl = rg2.number_input("Team B Games", 0, 30, max(0, tg-2))
            sa, sb = gw, gl
            sets_data.append((gw, gl))

        elif cat in ("AMERICANO", "MEXICANO"):
            tp = sel_f["total_points"] or 24
            ap1, ap2 = st.columns(2)
            sa = ap1.number_input("Team A Points", 0, tp, tp//2)
            sb = ap2.number_input("Team B Points", 0, tp, tp - (tp//2))
            gw, gl = sa, sb
            sets_data.append((sa, sb))

    btn_dry, btn_save = st.columns(2)
    do_dry = btn_dry.button("🔬 Execute Dry Run Simulation", use_container_width=True)
    do_save = btn_save.button("💾 Commit Match to Database", type="primary", use_container_width=True)

    if do_dry or do_save:
        if p1_pick == "-- Select --" or p3_pick == "-- Select --" or (not is_singles and (p2_pick == "-- Select --" or p4_pick == "-- Select --")):
            st.error("Please assign players to all required roster slots.")
        else:
            p1_obj = dict(p_dict[p1_pick])
            p3_obj = dict(p_dict[p3_pick])
            p2_obj = dict(p_dict[p2_pick]) if not is_singles else None
            p4_obj = dict(p_dict[p4_pick]) if not is_singles else None
            ven_obj = dict(v_dict[ven_sel])

            sim_out = RyftV16.compute_match(p1_obj, p2_obj, p3_obj, p4_obj, sa, sb, max(gw, gl), min(gw, gl), sel_f["format_id"], ven_obj["venue_id"], is_singles, is_dry=do_dry, is_tournament=is_tourney, conn=conn)

            st.success(f"Match Executed! Team A Odds: {sim_out['ea']*100:.1f}% vs Team B: {(1-sim_out['ea'])*100:.1f}% | Margin: {sim_out['mov']:.4f}")

            st.markdown("### 📋 Participant Calculations Breakdown")
            res_cols = st.columns(2 if is_singles else 4)
            for idx, pr in enumerate(sim_out["res"]):
                with res_cols[idx]:
                    st.markdown(f"""
                    <div style="background-color: #1e293b; border: 2px solid #0284c7; border-radius: 8px; padding: 12px; margin-bottom: 8px; color: #f8fafc;">
                        <h4 style="margin:0 0 8px 0; color:#38bdf8;">{pr['name']}</h4>
                        <div style="color: #cbd5e1; font-size: 0.9em; line-height: 1.6;">
                            <span style="color: #94a3b8; font-weight: 600;">Pre MMR:</span> <code style="color: #38bdf8; background: #0f172a;">{pr['pre_r']:.3f}</code><br/>
                            <span style="color: #94a3b8; font-weight: 600;">Post MMR:</span> <code style="color: #38bdf8; background: #0f172a;">{pr['post_r']:.3f}</code><br/>
                            <span style="color: #94a3b8; font-weight: 600;">Delta:</span> <span style="font-size:1.1em; font-weight:bold; color:{'#4ade80' if pr['delta']>=0 else '#f87171'}">{pr['delta']:+.4f}</span><br/>
                            <span style="color: #94a3b8; font-weight: 600;">RD:</span> <code style="color: #e2e8f0; background: #0f172a;">{pr['pre_rd']:.1f} ➔ {pr['post_rd']:.1f}</code><br/>
                            <span style="color: #94a3b8; font-weight: 600;">Accuracy:</span> <code style="color: #e2e8f0; background: #0f172a;">{pr['acc']:.1f}%</code><br/>
                            <span style="color: #94a3b8; font-weight: 600;">Tier:</span> <span style="background: #0f172a; padding: 2px 6px; border-radius: 4px; font-weight: bold; color: #38bdf8;">{pr['tier']}</span>
                        </div>
                    </div>
                    """, unsafe_allow_html=True)
                    if pr["flags"]: st.caption("⚡ " + " | ".join(pr["flags"]))

            if do_save:
                m_id = f"M_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
                ts = f"{match_date}T{match_time.strftime('%H:%M:%S')}Z"

                is_v_b = 1 if (p1_obj.get("home_venue_id") != ven_obj["venue_id"] and p1_obj.get("home_city_id") == ven_obj["city_id"]) else 0
                is_c_b = 1 if (p1_obj.get("home_city_id") != ven_obj["city_id"] and p1_obj.get("home_country_code") == ven_obj["country_code"]) else 0
                is_co_b = 1 if (p1_obj.get("home_country_code") != ven_obj["country_code"]) else 0

                all_guardrails = []
                for pr in sim_out["res"]: all_guardrails.extend(pr["flags"])

                conn.execute('''
                    INSERT INTO matches (
                        match_id, venue_id, format_id, session_id, is_singles, is_tournament,
                        is_venue_bridge, is_city_bridge, is_country_bridge,
                        team_a_p1_id, team_a_p2_id, team_b_p1_id, team_b_p2_id,
                        score_team_a, score_team_b, set_scores_json, games_winner, games_loser,
                        pre_rating_a, pre_rating_b, win_expectancy_a, applied_m_c, applied_s_margin,
                        delta_r_p1, delta_r_p2, delta_r_p3, delta_r_p4, guardrails_summary, match_timestamp
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ''', (
                    m_id, ven_obj["venue_id"], sel_f["format_id"], None, 1 if is_singles else 0, 1 if is_tourney else 0,
                    is_v_b, is_c_b, is_co_b,
                    p1_obj["player_id"], p2_obj["player_id"] if not is_singles else None,
                    p3_obj["player_id"], p4_obj["player_id"] if not is_singles else None,
                    sa, sb, json.dumps(sets_data), max(gw, gl), min(gw, gl),
                    sim_out["ta_r"], sim_out["tb_r"], sim_out["ea"], sel_f["mc_weight"], sim_out["mov"],
                    sim_out["res"][0]["delta"],
                    sim_out["res"][2]["delta"] if not is_singles else 0.0,
                    sim_out["res"][1]["delta"],
                    sim_out["res"][3]["delta"] if not is_singles else 0.0,
                    json.dumps(all_guardrails), ts
                ))

                for pr in sim_out["res"]:
                    conn.execute('''UPDATE players SET latent_mmr=?, display_rating=?, rating_deviation=?, rating_accuracy_pct=?, accuracy_s_rd=?, accuracy_s_matches=?, accuracy_s_diversity=?, calibration_tier=?, is_provisional=?, rolling_90d_peak=max(rolling_90d_peak, ?), rolling_180d_peak=max(rolling_180d_peak, ?), rolling_365d_peak=max(rolling_365d_peak, ?), last_match_time=? WHERE player_id=?''',
                                 (pr["post_r"], pr["post_r"], pr["post_rd"], pr["acc"], pr["a_rd"], pr["a_m"], pr["a_d"], pr["tier"], pr["prov"], pr["post_r"], pr["post_r"], pr["post_r"], ts, pr["pid"]))
                    
                    conn.execute('''INSERT INTO match_logs (log_id, match_id, player_id, pre_latent_mmr, post_latent_mmr, pre_display_rating, post_display_rating, pre_rd, post_rd, pre_accuracy_pct, post_accuracy_pct, delta_r, guardrails_triggered, logged_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)''',
                                 (f"L_{pr['pid']}_{m_id}", m_id, pr["pid"], pr["pre_r"], pr["post_r"], pr["pre_r"], pr["post_r"], pr["pre_rd"], pr["post_rd"], pr["acc"], pr["acc"], pr["delta"], json.dumps(pr["flags"]), ts))

                conn.execute("UPDATE venues SET total_matches_played = total_matches_played + 1 WHERE venue_id = ?", (ven_obj["venue_id"],))

                for pid in [p1_obj["player_id"], p3_obj["player_id"]] + ([p2_obj["player_id"], p4_obj["player_id"]] if not is_singles else []):
                    if pid: RyftV16.sync_player_aggregates(pid, conn=conn)

                conn.commit()
                st.balloons()
                st.success("✅ Match successfully committed to database!")
                st.rerun()

    conn.close()

# ------------------------------------------------------------------------------
# TAB 3: CLUB SESSIONS & MIXERS
# ------------------------------------------------------------------------------
elif nav == "🗓️ Club Sessions & Mixers":
    st.title("Sessions & Event Traffic Controller")
    st.caption("Multi-round scheduler, court traffic optimization, and atomic calibrations.")

    conn = get_db_connection()
    venues = conn.execute("SELECT venue_id, venue_name, court_count FROM venues WHERE is_active = 1").fetchall()
    players = conn.execute("SELECT player_id, display_name, latent_mmr, is_provisional FROM players WHERE calibration_tier != 'INACTIVE' ORDER BY display_name").fetchall()
    v_dict = {v["venue_name"]: dict(v) for v in venues}
    p_dict = {format_pr_name(p["display_name"], p["is_provisional"]): p["player_id"] for p in players}

    mode = st.radio("Session Console Navigation", ["➕ Create New Session", "🎮 Active Sessions Hub"], horizontal=True)

    if mode == "➕ Create New Session":
        st.subheader("1. Setup Session Schedule & Venue")
        
        cs1, cs2 = st.columns(2)
        s_title = cs1.text_input("Event / Session Title")
        s_ven = cs2.selectbox("Hosting Club / Venue", list(v_dict.keys()) if v_dict else ["No Venues Registered"])
        
        cs3, cs4, cs5 = st.columns(3)
        s_date = cs3.date_input("Event Date", value=date.today())
        s_start = cs4.time_input("Start Time", value=time(9, 0))
        s_end = cs5.time_input("End Time", value=time(11, 0))

        s_date_str = str(s_date)
        s_start_str = s_start.strftime("%H:%M")
        s_end_str = s_end.strftime("%H:%M")

        avail_courts = v_dict[s_ven]["court_count"] if s_ven in v_dict else 1
        all_court_labels = [f"Court {i+1}" for i in range(avail_courts)]

        overlap_sessions = conn.execute("""
            SELECT session_title, court_ids_json, start_time, end_time FROM sessions 
            WHERE venue_id = ? AND session_date = ? AND session_status != 'CANCELLED'
        """, (v_dict[s_ven]["venue_id"] if s_ven in v_dict else "", s_date_str)).fetchall()

        booked_courts = set()
        for osess in overlap_sessions:
            if s_start_str < osess["end_time"] and s_end_str > osess["start_time"]:
                booked_courts.update(json.loads(osess["court_ids_json"]))

        available_court_picks = [c for c in all_court_labels if c not in booked_courts]
        if booked_courts:
            st.warning(f"⚠️ **Parallel Booking:** {', '.join(booked_courts)} already reserved.")

        court_picks = st.multiselect("Select Dedicated Courts", available_court_picks, default=available_court_picks[:min(2, len(available_court_picks))])

        cs6, cs7, cs8 = st.columns([1.5, 2, 1.5])
        s_mode = cs6.selectbox("Game Mode", ["DOUBLES", "SINGLES"])
        s_team = cs7.selectbox("Team Format", ["FIXED_TEAMS", "ROTATING_TEAMS"] if s_mode == "DOUBLES" else ["SINGLES"])
        is_tourney_sess = cs8.checkbox("🏆 Official Tournament Session", value=False)
        
        if s_team == "ROTATING_TEAMS":
            compat_formats = conn.execute("SELECT format_id, format_name, category FROM match_formats WHERE category IN ('AMERICANO', 'MEXICANO', 'RACE_GAMES') AND is_active = 1").fetchall()
        elif s_team == "FIXED_TEAMS":
            compat_formats = conn.execute("SELECT format_id, format_name, category FROM match_formats WHERE category IN ('MULTI_SET', 'RACE_GAMES') AND is_active = 1").fetchall()
        else:
            compat_formats = conn.execute("SELECT format_id, format_name, category FROM match_formats WHERE category IN ('RACE_GAMES', 'MULTI_SET') AND is_active = 1").fetchall()
        
        f_compat_dict = {f["format_name"]: dict(f) for f in compat_formats}
        s_fmt_name = st.selectbox("Official Scoring Format", list(f_compat_dict.keys()) if f_compat_dict else ["None Compatible"])
        sel_format_obj = f_compat_dict[s_fmt_name] if s_fmt_name in f_compat_dict else None

        if s_team == "ROTATING_TEAMS":
            s_struct = "ROTATION"
            s_rounds = st.number_input("Rotation Cycles (Ensures equal play)", min_value=1, max_value=20, value=3)
        else:
            cs9, cs10 = st.columns(2)
            s_struct = cs9.selectbox("Play Structure", ["ROUND_ROBIN", "KNOCKOUT", "ROUND_ROBIN_AND_KNOCKOUT"])
            s_rounds = cs10.number_input("Rounds to Play", min_value=1, max_value=20, value=3)

        st.markdown("---")
        st.markdown("#### 2. Participants & Team Formation")

        teams_created = []
        enrolled_pids = []

        if s_team == "FIXED_TEAMS":
            num_teams = st.number_input("How many teams will play? (Odd numbers will auto-generate Byes)", min_value=2, max_value=32, value=4, step=1)
            p_names = list(p_dict.keys())
            for t_idx in range(int(num_teams)):
                st.markdown(f"**Team #{t_idx+1} Setup**")
                tc1, tc2 = st.columns(2)
                tp1 = tc1.selectbox(f"Player 1", ["-- Select --"] + p_names, key=f"t_p1_{t_idx}")
                tp2 = tc2.selectbox(f"Player 2", ["-- Select --"] + p_names, key=f"t_p2_{t_idx}")
                if tp1 != "-- Select --" and tp2 != "-- Select --":
                    teams_created.append({"team_name": "", "p1": p_dict[tp1], "p2": p_dict[tp2]})
                    enrolled_pids.extend([p_dict[tp1], p_dict[tp2]])
        else:
            enrolled_names = st.multiselect("Enroll Registered Players (Rotating Roster)", list(p_dict.keys()))
            enrolled_pids = [p_dict[p] for p in enrolled_names]

        if st.button("🚀 Create Session & Generate Fixtures", type="primary"):
            min_req = 4 if s_mode == "DOUBLES" else 2
            if len(enrolled_pids) < min_req:
                st.error(f"❌ Requires at least {min_req} participants.")
            elif s_team == "FIXED_TEAMS" and len(teams_created) < 2:
                st.error("❌ Please setup at least 2 complete teams.")
            elif not court_picks:
                st.error("❌ Please select at least one court.")
            else:
                s_id = f"SESS_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
                conn.execute("""
                    INSERT INTO sessions (session_id, venue_id, session_title, session_date, start_time, end_time,
                                        match_mode, team_format, format_id, tourney_structure, is_tournament, court_ids_json,
                                        enrolled_player_ids, teams_json, player_count, total_rounds, created_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, (s_id, v_dict[s_ven]["venue_id"], s_title, s_date_str, s_start_str, s_end_str,
                      s_mode, s_team, sel_format_obj["format_id"], s_struct, 1 if is_tourney_sess else 0, json.dumps(court_picks),
                      json.dumps(enrolled_pids), json.dumps(teams_created), len(enrolled_pids), int(s_rounds), datetime.now(timezone.utc).isoformat()))

                fixtures = SessionScheduleEngine.generate_schedule(s_team, s_mode, enrolled_pids, teams_created, court_picks, s_rounds)

                for f in fixtures:
                    sm_id = f"SM_{s_id}_{f['round_number']}_{f['match_order']}"
                    conn.execute("""
                        INSERT INTO session_matches (session_match_id, session_id, round_number, court_id, match_order,
                                                    team_a_p1_id, team_a_p2_id, team_b_p1_id, team_b_p2_id, match_status)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'SCHEDULED')
                    """, (sm_id, s_id, f["round_number"], f["court_id"], f["match_order"],
                          f["team_a_p1_id"], f["team_a_p2_id"], f["team_b_p1_id"], f["team_b_p2_id"]))

                conn.commit()
                st.success(f"Session '{s_title}' initialized with {len(fixtures)} fixtures!")
                st.rerun()

    elif mode == "🎮 Active Sessions Hub":
        s_tab1, s_tab2, s_tab3 = st.tabs(["⏳ Upcoming", "🔴 Live", "✅ Completed"])

        def render_session_workspace(s_data):
            s_id = s_data["session_id"]
            enrolled_ids = json.loads(s_data["enrolled_player_ids"])
            checked_in_ids = json.loads(s_data["checked_in_player_ids"])
            crts = json.loads(s_data["court_ids_json"])
            
            p_rows = conn.execute("SELECT player_id, display_name, is_provisional FROM players").fetchall()
            id_to_name = {p["player_id"]: format_pr_name(p["display_name"], p["is_provisional"]) for p in p_rows}

            hdr1, hdr2 = st.columns([4, 1])
            hdr1.markdown(f"""
            <div style="background-color: #f8fafc; padding: 12px; border-radius: 8px; border-left: 5px solid #0284c7; margin-bottom:12px;">
                <h3 style="margin:0; color:#0f172a;">{s_data['session_title']}</h3>
                <strong>Venue:</strong> {s_data['venue_name']} | <strong>Format:</strong> {s_data['format_name']} | <strong>Mode:</strong> {s_data['team_format']} | <strong>Courts:</strong> {len(crts)} | <strong>Tournament:</strong> {'YES' if s_data.get('is_tournament', 0) else 'NO'}
            </div>
            """, unsafe_allow_html=True)

            with hdr2:
                if st.button("🗑️ Discard Session", key=f"disc_{s_id}"):
                    conn.execute("DELETE FROM session_matches WHERE session_id = ?", (s_id,))
                    conn.execute("DELETE FROM sessions WHERE session_id = ?", (s_id,))
                    conn.commit(); st.warning("Session discarded."); st.rerun()

            sub_nav = st.radio("Stage", ["📋 Check-In Drawer", "🏟️ Matches Hub", "📊 Standings & Commit"], key=f"snav_{s_id}", horizontal=True)

            if sub_nav == "📋 Check-In Drawer":
                st.subheader("Roster Check-In")
                st.metric("Ready", f"{len(checked_in_ids)} of {len(enrolled_ids)}")

                btn_ci_all, btn_clr_all = st.columns(2)
                if btn_ci_all.button("✅ Check-In All Players", use_container_width=True, key=f"btn_all_{s_id}"):
                    conn.execute("UPDATE sessions SET checked_in_player_ids=?, active_checked_in_count=?, session_status='LIVE' WHERE session_id=?", (json.dumps(enrolled_ids), len(enrolled_ids), s_id))
                    conn.commit(); st.rerun()

                if btn_clr_all.button("❌ Clear All", use_container_width=True, key=f"btn_clr_{s_id}"):
                    conn.execute("UPDATE sessions SET checked_in_player_ids='[]', active_checked_in_count=0, session_status='CONFIG' WHERE session_id=?", (s_id,))
                    conn.commit(); st.rerun()

                with st.form(f"ci_form_{s_id}"):
                    updated_checkins = []
                    for pid in enrolled_ids:
                        p_name = id_to_name.get(pid, pid)
                        if st.checkbox(f"✅ {p_name}", value=(pid in checked_in_ids), key=f"chk_{s_id}_{pid}"):
                            updated_checkins.append(pid)

                    if st.form_submit_button("Save Updates"):
                        conn.execute("UPDATE sessions SET checked_in_player_ids=?, active_checked_in_count=?, session_status=? WHERE session_id=?", (json.dumps(updated_checkins), len(updated_checkins), 'LIVE' if len(updated_checkins) > 0 else 'CONFIG', s_id))
                        conn.commit(); st.rerun()

            elif sub_nav == "🏟️ Matches Hub":
                st.subheader("Court Traffic")
                fixtures = conn.execute("SELECT * FROM session_matches WHERE session_id = ? ORDER BY match_order ASC, round_number ASC", (s_id,)).fetchall()
                fmt_category = s_data["fmt_cat"]
                fmt_info = conn.execute("SELECT target_games, total_points FROM match_formats WHERE format_id = ?", (s_data['format_id'],)).fetchone()

                def is_ready(m):
                    parts = [p for p in [m['team_a_p1_id'], m['team_a_p2_id'], m['team_b_p1_id'], m['team_b_p2_id']] if p]
                    missing = [p for p in parts if p not in checked_in_ids]
                    return len(missing) == 0, [id_to_name.get(x, x) for x in missing]

                uncompleted = []
                completed = []
                for m_row in fixtures:
                    m = dict(m_row)
                    if m["match_status"] in ("STAGED", "COMMITTED", "CANCELLED"): completed.append(m)
                    else: uncompleted.append(m)

                with st.expander("🔀 Reorder Schedule", expanded=False):
                    sched_matches = [dict(m) for m in fixtures if m["match_status"] in ("SCHEDULED", "LIVE")]
                    if len(sched_matches) >= 2:
                        m_lookup = {m["session_match_id"]: m for m in sched_matches}
                        m_ids = list(m_lookup.keys())
                        re1, re2 = st.columns(2)
                        m_id_1 = re1.selectbox("Move Match", options=m_ids, format_func=lambda x: f"Match #{m_lookup[x]['match_order']} ({m_lookup[x]['court_id']} R{m_lookup[x]['round_number']})", key="sw1")
                        m_id_2 = re2.selectbox("Swap Position With", options=m_ids, format_func=lambda x: f"Match #{m_lookup[x]['match_order']} ({m_lookup[x]['court_id']} R{m_lookup[x]['round_number']})", key="sw2")
                        if st.button("Execute Swap"):
                            order_1, order_2 = m_lookup[m_id_1]["match_order"], m_lookup[m_id_2]["match_order"]
                            conn.execute("UPDATE session_matches SET match_order=? WHERE session_match_id=?", (order_2, m_id_1))
                            conn.execute("UPDATE session_matches SET match_order=? WHERE session_match_id=?", (order_1, m_id_2))
                            conn.commit(); st.rerun()

                st.markdown(f"### ⏳ Planned Fixtures ({len(uncompleted)} remaining)")
                if not uncompleted:
                    st.info("All matches scored or staged!")
                else:
                    for m in uncompleted:
                        ready_flag, missing_players = is_ready(m)
                        p1_n, p2_n = id_to_name.get(m['team_a_p1_id'], 'P1'), id_to_name.get(m['team_a_p2_id'], '')
                        p3_n, p4_n = id_to_name.get(m['team_b_p1_id'], 'P3'), id_to_name.get(m['team_b_p2_id'], '')
                        ta_players = f"{p1_n} & {p2_n}" if p2_n else p1_n
                        tb_players = f"{p3_n} & {p4_n}" if p4_n else p3_n

                        badge = "🟢 READY" if ready_flag else "⏳ WAITING"
                        header_label = f"{badge} Match #{m['match_order']} • {m['court_id']} (Round {m['round_number']}) — {ta_players} vs {tb_players}"
                        if not ready_flag: header_label += f" • Missing: {', '.join(missing_players)}"

                        with st.expander(header_label, expanded=ready_flag):
                            st.markdown(f"""
                            <div style="display: flex; justify-content: space-between; align-items: center; background: #f0fdf4; border: 1px solid #bbf7d0; padding: 8px 12px; border-radius: 6px; margin-bottom: 8px;">
                                <div style="font-weight: 700; color: #15803d; width: 45%;">🔵 {ta_players}</div>
                                <div style="font-weight: 900; color: #64748b; text-align: center; width: 10%;">VS</div>
                                <div style="font-weight: 700; color: #b91c1c; text-align: right; width: 45%;">🔴 {tb_players}</div>
                            </div>
                            """, unsafe_allow_html=True)

                            with st.form(f"score_form_{m['session_match_id']}"):
                                sets_recorded = []
                                sa, sb, gw, gl = 0, 0, 0, 0

                                if fmt_category == "RACE_GAMES":
                                    tg = fmt_info["target_games"] or 6
                                    rg1, rg2 = st.columns(2)
                                    gw = rg1.number_input(f"Games Won ({ta_players})", 0, 30, m['score_team_a'] if m['score_team_a']>0 else tg, key=f"ga_{m['session_match_id']}")
                                    gl = rg2.number_input(f"Games Won ({tb_players})", 0, 30, m['score_team_b'] if m['score_team_b']>0 else max(0, tg-2), key=f"gb_{m['session_match_id']}")
                                    sa, sb = (1 if gw>gl else 0), (1 if gl>gw else 0)
                                    sets_recorded.append((gw, gl))

                                elif fmt_category == "MULTI_SET":
                                    s1c1, s1c2 = st.columns(2)
                                    s1a = s1c1.number_input(f"Set 1: {ta_players}", 0, 7, 6, key=f"s1a_{m['session_match_id']}")
                                    s1b = s1c2.number_input(f"Set 1: {tb_players}", 0, 7, 3, key=f"s1b_{m['session_match_id']}")
                                    sets_recorded.append((s1a, s1b))

                                    s2c1, s2c2 = st.columns(2)
                                    s2a = s2c1.number_input(f"Set 2: {ta_players}", 0, 7, 6, key=f"s2a_{m['session_match_id']}")
                                    s2b = s2c2.number_input(f"Set 2: {tb_players}", 0, 7, 4, key=f"s2b_{m['session_match_id']}")
                                    sets_recorded.append((s2a, s2b))

                                    went_to_three = st.checkbox("Deciding Set 3 played", key=f"s3_chk_{m['session_match_id']}")
                                    if went_to_three:
                                        s3c1, s3c2 = st.columns(2)
                                        s3a = s3c1.number_input(f"Set 3: {ta_players}", 0, 7, 6, key=f"s3a_{m['session_match_id']}")
                                        s3b = s3c2.number_input(f"Set 3: {tb_players}", 0, 7, 4, key=f"s3b_{m['session_match_id']}")
                                        sets_recorded.append((s3a, s3b))

                                    sa = sum(1 for s in sets_recorded if s[0] > s[1])
                                    sb = sum(1 for s in sets_recorded if s[1] > s[0])
                                    gw = sum(x[0] for x in sets_recorded)
                                    gl = sum(x[1] for x in sets_recorded)

                                elif fmt_category in ("AMERICANO", "MEXICANO"):
                                    tp = fmt_info["total_points"] or 24
                                    ap1, ap2 = st.columns(2)
                                    sa = ap1.number_input(f"Points: {ta_players}", 0, 50, 12, key=f"pa_{m['session_match_id']}")
                                    sb = ap2.number_input(f"Points: {tb_players}", 0, 50, 12, key=f"pb_{m['session_match_id']}")
                                    gw, gl = sa, sb
                                    sets_recorded.append((sa, sb))

                                m_stat = st.selectbox("Status", ["SCHEDULED", "LIVE", "STAGED", "CANCELLED"], index=["SCHEDULED", "LIVE", "STAGED", "CANCELLED"].index(m['match_status']), key=f"st_{m['session_match_id']}")

                                if st.form_submit_button("✅ Submit Score"):
                                    conn.execute("""UPDATE session_matches SET score_team_a=?, score_team_b=?, games_winner=?, games_loser=?, set_scores_json=?, match_status=? WHERE session_match_id=?""",
                                                 (sa, sb, max(gw, gl), min(gw, gl), json.dumps(sets_recorded), 'STAGED' if m_stat=='SCHEDULED' else m_stat, m['session_match_id']))
                                    conn.execute("UPDATE sessions SET session_status='LIVE' WHERE session_id=?", (s_id,))
                                    conn.commit(); st.rerun()

                st.markdown("---")
                st.markdown(f"### ✅ Completed Matches ({len(completed)})")
                if completed:
                    for m in completed:
                        p1_n, p2_n = id_to_name.get(m['team_a_p1_id'], 'P1'), id_to_name.get(m['team_a_p2_id'], '')
                        p3_n, p4_n = id_to_name.get(m['team_b_p1_id'], 'P3'), id_to_name.get(m['team_b_p2_id'], '')
                        ta_players = f"{p1_n} & {p2_n}" if p2_n else p1_n
                        tb_players = f"{p3_n} & {p4_n}" if p4_n else p3_n

                        status_tag = "[CANCELLED]" if m['match_status'] == "CANCELLED" else f"[{m['score_team_a']} - {m['score_team_b']}]"
                        
                        with st.expander(f"✓ Match #{m['match_order']} • {m['court_id']} — {ta_players} {status_tag} {tb_players}", expanded=False):
                            
                            if m['match_status'] != "CANCELLED":
                                st.markdown("##### 🔬 Projected Calculations & Guardrails")
                                p1_d = dict(conn.execute("SELECT * FROM players WHERE player_id=?", (m["team_a_p1_id"],)).fetchone())
                                p3_d = dict(conn.execute("SELECT * FROM players WHERE player_id=?", (m["team_b_p1_id"],)).fetchone())
                                p2_d = dict(conn.execute("SELECT * FROM players WHERE player_id=?", (m["team_a_p2_id"],)).fetchone()) if m["team_a_p2_id"] else None
                                p4_d = dict(conn.execute("SELECT * FROM players WHERE player_id=?", (m["team_b_p2_id"],)).fetchone()) if m["team_b_p2_id"] else None
                                
                                is_sing = (s_data["match_mode"] == "SINGLES")
                                out = RyftV16.compute_match(
                                    p1_d, p2_d, p3_d, p4_d, m["score_team_a"], m["score_team_b"],
                                    max(m["games_winner"], m["score_team_a"]), min(m["games_loser"], m["score_team_b"]),
                                    s_data["format_id"], s_data["venue_id"], is_singles=is_sing,
                                    session_id=s_id, session_checked_in=s_data["active_checked_in_count"], 
                                    is_tournament=bool(s_data.get("is_tournament", 0)), is_dry=True, conn=conn
                                )
                                
                                res_cols = st.columns(2 if is_sing else 4)
                                for idx, pr in enumerate(out["res"]):
                                    with res_cols[idx]:
                                        st.markdown(f"""
                                        <div style="background-color: #1e293b; border: 1px solid #0284c7; border-radius: 6px; padding: 10px; margin-bottom: 8px; color: #f8fafc;">
                                            <div style="font-weight: bold; color:#38bdf8; margin-bottom: 4px;">{pr['name']}</div>
                                            <div style="font-size: 0.85em; color: #cbd5e1; line-height: 1.4;">
                                                Pre: <code style="color:#38bdf8; background:#0f172a;">{pr['pre_r']:.3f}</code> ➔ Post: <code style="color:#38bdf8; background:#0f172a;">{pr['post_r']:.3f}</code><br/>
                                                Delta: <strong style="color:{'#4ade80' if pr['delta']>=0 else '#f87171'}">{pr['delta']:+.4f}</strong><br/>
                                                RD: <code style="color:#e2e8f0; background:#0f172a;">{pr['pre_rd']:.1f} ➔ {pr['post_rd']:.1f}</code><br/>
                                                Acc: {pr['acc']:.1f}% | <span style="color:#38bdf8;">{pr['tier']}</span>
                                            </div>
                                        </div>
                                        """, unsafe_allow_html=True)
                                        if pr["flags"]: st.caption("⚡ " + " | ".join(pr["flags"]))
                            
                            st.markdown("---")
                            with st.form(f"edit_comp_{m['session_match_id']}"):
                                sets_json = json.loads(m['set_scores_json']) if m['set_scores_json'] else []
                                
                                if fmt_category == "RACE_GAMES":
                                    c_ea, c_eb = st.columns(2)
                                    val_a = sets_json[0][0] if sets_json else m['score_team_a']
                                    val_b = sets_json[0][1] if sets_json else m['score_team_b']
                                    new_gw = c_ea.number_input(f"Games {ta_players}", 0, 100, val_a, key=f"ed_ga_{m['session_match_id']}")
                                    new_gl = c_eb.number_input(f"Games {tb_players}", 0, 100, val_b, key=f"ed_gb_{m['session_match_id']}")
                                    if st.form_submit_button("Update Score"):
                                        sa, sb = (1 if new_gw > new_gl else 0), (1 if new_gl > new_gw else 0)
                                        conn.execute("UPDATE session_matches SET score_team_a=?, score_team_b=?, games_winner=?, games_loser=?, set_scores_json=?, match_status='STAGED' WHERE session_match_id=?",
                                                     (sa, sb, max(new_gw, new_gl), min(new_gw, new_gl), json.dumps([(new_gw, new_gl)]), m['session_match_id']))
                                        conn.commit(); st.rerun()

                                elif fmt_category == "MULTI_SET":
                                    s1a = sets_json[0][0] if len(sets_json) > 0 else 6
                                    s1b = sets_json[0][1] if len(sets_json) > 0 else 3
                                    s2a = sets_json[1][0] if len(sets_json) > 1 else 6
                                    s2b = sets_json[1][1] if len(sets_json) > 1 else 4
                                    s3a = sets_json[2][0] if len(sets_json) > 2 else 0
                                    s3b = sets_json[2][1] if len(sets_json) > 2 else 0

                                    ec1, ec2 = st.columns(2)
                                    ns1a = ec1.number_input(f"Set 1: {ta_players}", 0, 7, s1a, key=f"es1a_{m['session_match_id']}")
                                    ns1b = ec2.number_input(f"Set 1: {tb_players}", 0, 7, s1b, key=f"es1b_{m['session_match_id']}")
                                    ns2a = ec1.number_input(f"Set 2: {ta_players}", 0, 7, s2a, key=f"es2a_{m['session_match_id']}")
                                    ns2b = ec2.number_input(f"Set 2: {tb_players}", 0, 7, s2b, key=f"es2b_{m['session_match_id']}")
                                    ns3a = ec1.number_input(f"Set 3: {ta_players}", 0, 7, s3a, key=f"es3a_{m['session_match_id']}")
                                    ns3b = ec2.number_input(f"Set 3: {tb_players}", 0, 7, s3b, key=f"es3b_{m['session_match_id']}")
                                    
                                    if st.form_submit_button("Update Score"):
                                        final_sets = [(ns1a, ns1b), (ns2a, ns2b)]
                                        if ns3a > 0 or ns3b > 0: final_sets.append((ns3a, ns3b))
                                        sa = sum(1 for s in final_sets if s[0] > s[1])
                                        sb = sum(1 for s in final_sets if s[1] > s[0])
                                        gw = sum(x[0] for x in final_sets)
                                        gl = sum(x[1] for x in final_sets)
                                        conn.execute("UPDATE session_matches SET score_team_a=?, score_team_b=?, games_winner=?, games_loser=?, set_scores_json=?, match_status='STAGED' WHERE session_match_id=?",
                                                     (sa, sb, max(gw, gl), min(gw, gl), json.dumps(final_sets), m['session_match_id']))
                                        conn.commit(); st.rerun()
                                else:
                                    c_ea, c_eb = st.columns(2)
                                    val_a = sets_json[0][0] if sets_json else m['score_team_a']
                                    val_b = sets_json[0][1] if sets_json else m['score_team_b']
                                    new_a = c_ea.number_input(f"Points {ta_players}", 0, 100, val_a, key=f"ed_a_{m['session_match_id']}")
                                    new_b = c_eb.number_input(f"Points {tb_players}", 0, 100, val_b, key=f"ed_b_{m['session_match_id']}")
                                    if st.form_submit_button("Update Points"):
                                        conn.execute("UPDATE session_matches SET score_team_a=?, score_team_b=?, games_winner=?, games_loser=?, set_scores_json=?, match_status='STAGED' WHERE session_match_id=?",
                                                     (new_a, new_b, max(new_a, new_b), min(new_a, new_b), json.dumps([(new_a, new_b)]), m['session_match_id']))
                                        conn.commit(); st.rerun()

                st.markdown("---")
                if uncompleted:
                    if st.button("⏹️ End Session Early & Finalize Completed Matches", type="secondary"):
                        conn.execute("UPDATE session_matches SET match_status='CANCELLED' WHERE session_id=? AND match_status='SCHEDULED'", (s_id,))
                        conn.commit(); st.rerun()

            # --- SUB-TAB 3: STANDINGS, PREVIEW & ATOMIC COMMIT ---
            elif sub_nav == "📊 Standings & Commit":
                st.subheader("Event Standings & Final Engine Commit")
                staged_matches = conn.execute("SELECT * FROM session_matches WHERE session_id = ? AND match_status = 'STAGED'", (s_id,)).fetchall()

                standings = {}
                if s_data["team_format"] == "FIXED_TEAMS":
                    teams_meta = json.loads(s_data["teams_json"])
                    for t in teams_meta:
                        if t and t.get("p1"):
                            p1_n, p2_n = id_to_name.get(t["p1"], ""), id_to_name.get(t["p2"], "")
                            t_label = f"{p1_n} & {p2_n}"
                            standings[t_label] = {"Team": t_label, "Played": 0, "Won": 0, "Lost": 0, "Points Won": 0, "Points Lost": 0, "Diff": 0}
                    
                    for sm in staged_matches:
                        p1_n, p2_n = id_to_name.get(sm['team_a_p1_id'], ''), id_to_name.get(sm['team_a_p2_id'], '')
                        p3_n, p4_n = id_to_name.get(sm['team_b_p1_id'], ''), id_to_name.get(sm['team_b_p2_id'], '')
                        tA, tB = f"{p1_n} & {p2_n}", f"{p3_n} & {p4_n}"

                        if tA in standings and tB in standings:
                            standings[tA]["Played"] += 1; standings[tB]["Played"] += 1
                            standings[tA]["Points Won"] += sm["score_team_a"]; standings[tB]["Points Won"] += sm["score_team_b"]
                            standings[tA]["Points Lost"] += sm["score_team_b"]; standings[tB]["Points Lost"] += sm["score_team_a"]
                            if sm["score_team_a"] > sm["score_team_b"]: standings[tA]["Won"] += 1; standings[tB]["Lost"] += 1
                            elif sm["score_team_a"] < sm["score_team_b"]: standings[tB]["Won"] += 1; standings[tA]["Lost"] += 1
                else:
                    for pid in enrolled_ids:
                        standings[pid] = {"Player": id_to_name.get(pid,1. The system is currently defaulting to a binary win/loss output (1 for a win, 0 for a loss) because that is the raw mathematical input most rating algorithms use to determine who won. However, it is mistakenly pushing this back-end logic into the front-end UI. To fix this, you need to decouple the display score from the rating outcome. The UI should dynamically present input fields based on the specific padel or pickleball format selected for that session (e.g., a single integer field for an Americano race to 16, or multi-set fields for a best-of-3). The back-end will then translate the highest score into the 1/0 needed for the rating math, while the user only ever interacts with the actual point or set scores.

2. You are absolutely correct; the +/- 0.300 cap must apply to the **entire session aggregate**, not the individual matches. Applying a hard cap per match artificially compresses standard rating exchanges and defeats the purpose of a session-wide safeguard. For optimal system accuracy, the logic should flow sequentially:
   * Calculate the raw rating delta for every match independently.
   * Sum a player's total deltas across all 5 or 10 matches they played in that session.
   * Apply the +/- 0.300 cap to that final aggregated sum (excluding provisional/PR players, who need high volatility to quickly find their baseline rating).
   * Apply the capped final delta to their pre-session rating to generate the new master rating.

3. Yes, this is completely normal. In systems like Glicko, Rating Deviation (RD) measures statistical uncertainty. RD increases over time with inactivity and decreases after every single match played. If your players started the session with a roughly similar baseline RD and all played the exact same number of matches, their RD will drop at nearly identical rates because the system has gained an equal amount of new data on all of them.

4. Adding a comprehensive Pre/Post summary table is an excellent UI addition for the Commit tab to give facility managers full transparency before finalizing the data. Structuring it to highlight both the raw math and the session caps will make auditing easy:

| Player | Pre-Session Rating | Pre-Session RD | Raw Delta Sum | Cap Triggered? | Final Delta | Post-Session Rating | Post-Session RD |
| :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- |
| Player A | 1450.00 | 120.00 | +0.415 | Yes (+0.300) | +0.300 | 1450.30 | 108.50 |
| Player B | 1620.50 | 110.00 | -0.120 | No | -0.120 | 1620.38 | 98.20 |
| Player C | 1100.00 | 250.00 (PR) | +0.650 | No (PR Exempt) | +0.650 | 1100.65 | 195.00 |

Does this align with how you are mapping the different match formats in the database?
