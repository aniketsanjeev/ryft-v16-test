import streamlit as st
import sqlite3
import math
import json
import os
from datetime import datetime, timezone, date, time
import pandas as pd

# ==============================================================================
# 1. DATABASE INITIALIZATION & RELATIONAL SCHEMA (V.16 ARCHITECTURE)
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

def init_db():
    conn = get_db_connection()
    c = conn.cursor()
    c.execute("PRAGMA foreign_keys = ON;")

    # 1. Locations Table
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

    # 2. Venues Table
    c.execute('''CREATE TABLE IF NOT EXISTS venues (
        venue_id TEXT PRIMARY KEY, venue_name TEXT NOT NULL, raw_input_name TEXT, is_verified INTEGER DEFAULT 0,
        city_id TEXT NOT NULL, country_code TEXT NOT NULL, court_count INTEGER DEFAULT 1, total_matches_played INTEGER DEFAULT 0,
        unique_players_count INTEGER DEFAULT 0, city_bridge_matches_count INTEGER DEFAULT 0, country_bridge_matches_count INTEGER DEFAULT 0,
        average_player_mmr REAL DEFAULT 3.000, is_active INTEGER DEFAULT 1, created_at TEXT,
        FOREIGN KEY (city_id) REFERENCES locations(location_id) ON DELETE CASCADE
    )''')

    # 3. Rating Categories Table
    c.execute('''CREATE TABLE IF NOT EXISTS rating_categories (
        category_name TEXT PRIMARY KEY, min_rating REAL NOT NULL, max_rating REAL NOT NULL, sort_order INTEGER NOT NULL
    )''')

    # 4. Match Formats Table
    c.execute('''CREATE TABLE IF NOT EXISTS match_formats (
        format_id TEXT PRIMARY KEY, format_name TEXT NOT NULL, category TEXT NOT NULL,
        mc_weight REAL NOT NULL, target_games INTEGER, total_points INTEGER, is_session_bound INTEGER DEFAULT 0, is_active INTEGER DEFAULT 1
    )''')

    # 5. Players Table
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
        FOREIGN KEY (home_venue_id) REFERENCES venues(venue_id) ON DELETE SET NULL,
        FOREIGN KEY (home_city_id) REFERENCES locations(location_id) ON DELETE CASCADE
    )''')

    # 6. Sessions Master Table
    c.execute('''CREATE TABLE IF NOT EXISTS sessions (
        session_id TEXT PRIMARY KEY, venue_id TEXT NOT NULL, session_title TEXT NOT NULL,
        match_mode TEXT NOT NULL DEFAULT 'DOUBLES', team_format TEXT NOT NULL, format_id TEXT NOT NULL,
        tourney_structure TEXT NOT NULL DEFAULT 'ROUND_ROBIN', court_ids_json TEXT NOT NULL,
        enrolled_player_ids TEXT NOT NULL, teams_json TEXT DEFAULT '[]', checked_in_player_ids TEXT DEFAULT '[]',
        player_count INTEGER NOT NULL, active_checked_in_count INTEGER DEFAULT 0, total_rounds INTEGER NOT NULL DEFAULT 1,
        current_round INTEGER DEFAULT 0, session_status TEXT DEFAULT 'CONFIG' CHECK(session_status IN ('CONFIG', 'LIVE', 'COMPLETED', 'CANCELLED')),
        created_at TEXT NOT NULL, completed_at TEXT,
        FOREIGN KEY (venue_id) REFERENCES venues(venue_id), FOREIGN KEY (format_id) REFERENCES match_formats(format_id)
    )''')

    # 7. Session Matches Table
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

    # 8. Matches Master Ledger
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

    # 9. Match Logs
    c.execute('''CREATE TABLE IF NOT EXISTS match_logs (
        log_id TEXT PRIMARY KEY, match_id TEXT NOT NULL, player_id TEXT NOT NULL, pre_latent_mmr REAL NOT NULL,
        post_latent_mmr REAL NOT NULL, pre_display_rating REAL NOT NULL, post_display_rating REAL NOT NULL,
        pre_rd REAL NOT NULL, post_rd REAL NOT NULL, pre_accuracy_pct REAL NOT NULL, post_accuracy_pct REAL NOT NULL,
        delta_r REAL NOT NULL, is_elevator_active INTEGER DEFAULT 0, guardrails_triggered TEXT DEFAULT '[]',
        is_retroactive INTEGER DEFAULT 0, logged_at TEXT NOT NULL, FOREIGN KEY (match_id) REFERENCES matches(match_id) ON DELETE CASCADE,
        FOREIGN KEY (player_id) REFERENCES players(player_id) ON DELETE CASCADE
    )''')

    # 10. Global Config, Rules & Changelogs
    c.execute('''CREATE TABLE IF NOT EXISTS global_config (param_key TEXT PRIMARY KEY, param_value REAL NOT NULL, is_active INTEGER DEFAULT 1, title TEXT, description TEXT, tuning_guide TEXT, module_group TEXT)''')
    c.execute('''CREATE TABLE IF NOT EXISTS progression_speed_rules (rule_id INTEGER PRIMARY KEY AUTOINCREMENT, min_rating REAL NOT NULL, max_rating REAL NOT NULL, speed_multiplier REAL NOT NULL, description TEXT, is_active INTEGER DEFAULT 1, created_at TEXT NOT NULL)''')
    c.execute('''CREATE TABLE IF NOT EXISTS config_changelog (log_id INTEGER PRIMARY KEY AUTOINCREMENT, param_key TEXT NOT NULL, old_value REAL NOT NULL, new_value REAL NOT NULL, changed_by TEXT NOT NULL, changed_at TEXT NOT NULL)''')
    c.execute('''CREATE TABLE IF NOT EXISTS player_changelog (log_id INTEGER PRIMARY KEY AUTOINCREMENT, player_id TEXT NOT NULL, change_type TEXT NOT NULL, old_val TEXT, new_val TEXT, changed_by TEXT NOT NULL, changed_at TEXT NOT NULL)''')

    # Safe Migrations
    add_column_if_not_exists(c, "sessions", "teams_json", "TEXT DEFAULT '[]'")
    add_column_if_not_exists(c, "session_matches", "team_a_name", "TEXT DEFAULT ''")
    add_column_if_not_exists(c, "session_matches", "team_b_name", "TEXT DEFAULT ''")
    add_column_if_not_exists(c, "session_matches", "games_winner", "INTEGER DEFAULT 0")
    add_column_if_not_exists(c, "session_matches", "games_loser", "INTEGER DEFAULT 0")
    add_column_if_not_exists(c, "players", "rolling_90d_peak", "REAL NOT NULL DEFAULT 3.000")
    add_column_if_not_exists(c, "players", "rolling_180d_peak", "REAL NOT NULL DEFAULT 3.000")
    add_column_if_not_exists(c, "players", "rolling_365d_peak", "REAL NOT NULL DEFAULT 3.000")
    add_column_if_not_exists(c, "players", "tournament_floor", "REAL NOT NULL DEFAULT 0.000")

    # Seed Rating Categories
    default_cats = [
        ("Beginner", 0.000, 0.999, 1), ("Beginner+", 1.000, 1.999, 2), ("Intermediate", 2.000, 3.499, 3),
        ("Intermediate+", 3.500, 4.499, 4), ("Advanced", 4.500, 5.499, 5), ("Pro", 5.500, 6.299, 6), ("Elite", 6.300, 7.000, 7)
    ]
    for c_name, c_min, c_max, s_ord in default_cats:
        c.execute("""INSERT INTO rating_categories (category_name, min_rating, max_rating, sort_order) VALUES (?, ?, ?, ?)
                     ON CONFLICT(category_name) DO UPDATE SET min_rating=excluded.min_rating, max_rating=excluded.max_rating, sort_order=excluded.sort_order""", (c_name, c_min, c_max, s_ord))

    # Seed 18 Formats
    official_formats = [
        ("STD_B03", "Best of 3 Sets", "MULTI_SET", 1.00, None, None, 0, 1),
        ("STD_B05", "Best of 5 Sets", "MULTI_SET", 1.00, None, None, 0, 1),
        ("RACE_4", "Race to 4 Games", "RACE_GAMES", 0.50, 4, None, 0, 1),
        ("RACE_5", "Race to 5 Games", "RACE_GAMES", 0.60, 5, None, 0, 1),
        ("RACE_6", "Race to 6 Games", "RACE_GAMES", 0.70, 6, None, 0, 1),
        ("RACE_7", "Race to 7 Games", "RACE_GAMES", 0.80, 7, None, 0, 1),
        ("RACE_9", "Race to 9 Games", "RACE_GAMES", 0.80, 9, None, 0, 1),
        ("RACE_11", "Race to 11 Games", "RACE_GAMES", 0.90, 11, None, 0, 1),
        ("AMER_12", "Americano 12 Points", "AMERICANO", 0.30, None, 12, 1, 1),
        ("MEX_12", "Mexicano 12 Points", "MEXICANO", 0.30, None, 12, 1, 1),
        ("AMER_16", "Americano 16 Points", "AMERICANO", 0.30, None, 16, 1, 1),
        ("MEX_16", "Mexicano 16 Points", "MEXICANO", 0.30, None, 16, 1, 1),
        ("AMER_20", "Americano 20 Points", "AMERICANO", 0.30, None, 20, 1, 1),
        ("MEX_20", "Mexicano 20 Points", "MEXICANO", 0.30, None, 20, 1, 1),
        ("AMER_24", "Americano 24 Points", "AMERICANO", 0.30, None, 24, 1, 1),
        ("MEX_24", "Mexicano 24 Points", "MEXICANO", 0.30, None, 24, 1, 1),
        ("AMER_28", "Americano 28 Points", "AMERICANO", 0.30, None, 28, 1, 1),
        ("MEX_28", "Mexicano 28 Points", "MEXICANO", 0.30, None, 28, 1, 1)
    ]
    for fid, fname, cat, mc, tg, tp, is_sb, is_a in official_formats:
        c.execute("""INSERT INTO match_formats (format_id, format_name, category, mc_weight, target_games, total_points, is_session_bound, is_active)
                     VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                     ON CONFLICT(format_id) DO UPDATE SET format_name=excluded.format_name, category=excluded.category, mc_weight=excluded.mc_weight, target_games=excluded.target_games, total_points=excluded.total_points, is_session_bound=excluded.is_session_bound, is_active=excluded.is_active""", (fid, fname, cat, mc, tg, tp, is_sb, is_a))

    # Master Parameters
    master_params = [
        ("R_MIN", 0.000, 1, "Scale Absolute Floor", "Lowest possible rating.", "Clamps lowest possible rating to 0.000.", "Core Bounds"),
        ("R_MAX", 7.000, 1, "Scale Absolute Ceiling", "Maximum rating ceiling.", "LOCKED at 7.000.", "Core Bounds"),
        ("R_ELITE_THRESHOLD", 6.300, 1, "Elite Drag Gate", "Rating where exponential drag starts.", "Lowering applies drag earlier.", "Core Bounds"),
        ("ELITE_DRAG_EXPONENT", 2.5, 1, "Elite Drag Curvature", "Steepness of ceiling resistance.", "Higher values make 7.000 mathematically unbreachable.", "Core Bounds"),
        ("POWER_MEAN_P", 3.0, 1, "Doubles Cubic Exponent", "Power mean anchor exponent.", "3.0 gives 70/30 anchor bias.", "Engine Volatility"),
        ("LOGISTIC_BETA", 2.0, 1, "Logistic Scale Factor", "Odds curve steepness.", "Lowering boosts upset deltas.", "Engine Volatility"),
        ("K_MAX", 0.400, 1, "Beginner Max Volatility", "Step size at R=0.000.", "Higher values accelerate beginner progression.", "Engine Volatility"),
        ("K_MIN", 0.080, 1, "Pro Min Volatility", "Step size at R=7.000.", "Lower values lock pro ratings tighter.", "Engine Volatility"),
        ("MARGIN_BASE", 0.80, 1, "Margin Floor Factor", "Min score factor for close matches.", "Points floor for tight finishes.", "Margins & Formats"),
        ("MARGIN_SCALE", 0.40, 1, "Margin Blowout Scale", "Max bonus factor for blowouts.", "Full blowout bonus = Base + Scale = 1.20.", "Margins & Formats"),
        ("MAX_PROVISIONAL_DELTA", 0.750, 1, "Placement Ceiling", "Max points won in interpolation.", "Single-match placement cap for unranked smurfs.", "Margins & Formats"),
        ("PROVISIONAL_ABSORPTION_ALPHA", 0.45, 1, "Rightsizing Velocity", "Speed toward performance rating.", "Higher values accelerate unranked rightsizing.", "Margins & Formats"),
        ("MAX_24H_EXCHANGE_CAP", 0.150, 1, "24H Casual Cap", "Net transfer ceiling between 4 players.", "Prevents collusion rings from farming points.", "Anti-Farming"),
        ("PROVISIONAL_CAP_MULTIPLIER", 2.5, 1, "Provisional Cap Relaxer", "Multiplier on 24H cap for PRs.", "Allows up to 0.375 point movement for unrated accounts.", "Anti-Farming"),
        ("SESSION_EXCHANGE_CAP", 0.300, 1, "Verified Session Cap", "Cap for verified club events.", "Doubles point limits for verified club mixers (Requires 6+ checked-in).", "Anti-Farming"),
        ("RD_MIN", 30.0, 1, "Certainty Floor", "Absolute uncertainty floor.", "Prevents RD dropping below 30.0.", "Uncertainty & Rust"),
        ("RD_MAX", 350.0, 1, "Unrated Starting RD", "Uncertainty assigned at registration.", "Starting baseline uncertainty.", "Uncertainty & Rust"),
        ("RD_INFO_VARIANCE", 65.0, 1, "Contraction Speed", "Denominator in RD shrinkage.", "Lower values shrink RD faster per match.", "Uncertainty & Rust"),
        ("PROV_RD_SHRINK_MULTIPLIER", 0.35, 1, "Provisional RD Shrink Modifier", "Slows RD drop for unrated players.", "Lower values keep players provisional longer.", "Accuracy & Calibration"),
        ("PROV_ACCURACY_GAIN_MULTIPLIER", 0.40, 1, "Provisional Accuracy Gain Cap", "Restricts accuracy gain during placement.", "Caps visual accuracy progression.", "Accuracy & Calibration"),
        ("PROVISIONAL_RD_GATE", 100.0, 1, "Tri-Gate Max RD", "RD must be <= 100 to exit [PR].", "Uncertainty ceiling to graduate to Verified.", "Accuracy & Calibration"),
        ("PROVISIONAL_MIN_MATCHES", 10, 1, "Tri-Gate Min Matches", "Verified matches to exit [PR].", "Match volume required to shed [PR] badge.", "Accuracy & Calibration"),
        ("PROVISIONAL_MIN_OPPONENTS", 5, 1, "Tri-Gate Min Opponents", "Unique opponents to exit [PR].", "Distinct opponents required.", "Accuracy & Calibration"),
        ("ISLAND_ACCURACY_CAP", 80.0, 1, "Island Geographic Cap", "Max accuracy if City has 0 bridges.", "Caps accuracy until cross-city play occurs.", "Accuracy & Calibration"),
        ("INACTIVITY_CONSTANT", 12.0, 1, "Inactivity Rust Rate", "Monthly uncertainty growth.", "Points of RD regained per inactive month away from the court.", "Macros"),
        ("BRIDGE_RD_THRESHOLD", 80.0, 1, "Bridge Max RD", "Max RD to qualify as Bridge.", "Only players with RD <= 80 count.", "Macros"),
        ("LAMBDA_BRIDGE_DAMPING", 3.0, 1, "Tikhonov Lambda", "Shock absorber parameter.", "Higher values require more travelers before city shifts deploy.", "Macros")
    ]
    for k, v, act, tit, desc, tune, grp in master_params:
        c.execute("""INSERT INTO global_config (param_key, param_value, is_active, title, description, tuning_guide, module_group)
                     VALUES (?, ?, ?, ?, ?, ?, ?)
                     ON CONFLICT(param_key) DO UPDATE SET title=excluded.title, description=excluded.description, tuning_guide=excluded.tuning_guide, module_group=excluded.module_group""", (k, v, act, tit, desc, tune, grp))

    conn.commit()
    conn.close()

init_db()

# ==============================================================================
# 2. V.16 CALCULATION ENGINE
# ==============================================================================
class RyftV16:
    @staticmethod
    def get_configs():
        conn = get_db_connection()
        rows = conn.execute("SELECT param_key, param_value FROM global_config WHERE is_active = 1").fetchall()
        conn.close()
        return {r["param_key"]: r["param_value"] for r in rows}

    @staticmethod
    def get_cat_for_rating(r_val):
        conn = get_db_connection()
        cats = conn.execute("SELECT category_name, min_rating, max_rating FROM rating_categories ORDER BY sort_order ASC").fetchall()
        conn.close()
        for c in cats:
            if c["min_rating"] <= r_val <= c["max_rating"]:
                return c["category_name"], c["min_rating"], c["max_rating"]
        if cats:
            if r_val < cats[0]["min_rating"]: return cats[0]["category_name"], cats[0]["min_rating"], cats[0]["max_rating"]
            return cats[-1]["category_name"], cats[-1]["min_rating"], cats[-1]["max_rating"]
        return "Intermediate", 2.000, 3.499

    @staticmethod
    def calc_accuracy(rd, m_count, opp_count, is_prov, k_bridges, cfg):
        s_rd = max(0.0, min(1.0, (cfg.get("RD_MAX", 350.0) - rd) / (cfg.get("RD_MAX", 350.0) - cfg.get("RD_MIN", 30.0))))
        t_m = cfg.get("PROVISIONAL_MIN_MATCHES", 10) if is_prov else 15
        t_o = cfg.get("PROVISIONAL_MIN_OPPONENTS", 5) if is_prov else 8
        s_m = min(1.0, m_count / float(t_m))
        s_d = min(1.0, opp_count / float(t_o))
        
        raw_acc = (0.50 * s_rd + 0.25 * s_m + 0.25 * s_d) * 100.0
        if is_prov:
            raw_acc *= cfg.get("PROV_ACCURACY_GAIN_MULTIPLIER", 0.40)
        
        if k_bridges == 0:
            phi = min(1.0, cfg.get("ISLAND_ACCURACY_CAP", 80.0) / 100.0)
        else:
            phi = min(1.0, 0.80 + (0.10 * k_bridges))
            
        return round(raw_acc * phi, 1), round(s_rd * 100.0, 1), round(s_m * 100.0, 1), round(s_d * 100.0, 1)

    @staticmethod
    def sync_player_aggregates(player_id):
        conn = get_db_connection()
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
        cross_city_matches = 0
        cross_country_matches = 0
        if p_row:
            cross_city_matches = conn.execute("SELECT COUNT(*) FROM matches m JOIN venues v ON m.venue_id = v.venue_id WHERE (m.team_a_p1_id = ? OR m.team_a_p2_id = ? OR m.team_b_p1_id = ? OR m.team_b_p2_id = ?) AND v.city_id != ?", (player_id, player_id, player_id, player_id, p_row["home_city_id"])).fetchone()[0]
            cross_country_matches = conn.execute("SELECT COUNT(*) FROM matches m JOIN venues v ON m.venue_id = v.venue_id WHERE (m.team_a_p1_id = ? OR m.team_a_p2_id = ? OR m.team_b_p1_id = ? OR m.team_b_p2_id = ?) AND v.country_code != ?", (player_id, player_id, player_id, player_id, p_row["home_country_code"])).fetchone()[0]

        is_act_city_bridge = 1 if (p_row and p_row["rating_deviation"] <= 80.0 and cross_city_matches >= 5) else 0
        is_act_ctry_bridge = 1 if (p_row and p_row["rating_deviation"] <= 80.0 and cross_country_matches >= 3) else 0
        cur_cat, _, _ = RyftV16.get_cat_for_rating(p_row["latent_mmr"] if p_row else 3.0)

        conn.execute("""UPDATE players SET verified_matches_count=?, unique_opponents_count=?, bridge_matches_count=?, is_active_bridge=?, is_country_bridge=?, all_time_badge=? WHERE player_id=?""",
                     (m_count, opp_count, cross_city_matches, is_act_city_bridge, is_act_ctry_bridge, cur_cat, player_id))
        conn.commit()
        conn.close()

    @classmethod
    def compute_match(cls, p1, p2, p3, p4, s_a, s_b, g_w_raw, g_l_raw, fmt_id, v_id, is_singles, is_dry=False, session_id=None, session_checked_in=0):
        cfg = cls.get_configs()
        p_exp = cfg.get("POWER_MEAN_P", 3.0)
        g_w = max(g_w_raw, g_l_raw + 1)
        g_l = g_l_raw
        
        ta_r = p1["latent_mmr"] if is_singles else ((p1["latent_mmr"]**p_exp + p2["latent_mmr"]**p_exp) / 2.0)**(1.0 / p_exp)
        tb_r = p3["latent_mmr"] if is_singles else ((p3["latent_mmr"]**p_exp + p4["latent_mmr"]**p_exp) / 2.0)**(1.0 / p_exp)
        
        beta = cfg.get("LOGISTIC_BETA", 2.0)
        ea = 1.0 / (1.0 + 10.0**((tb_r - ta_r) / beta))
        act_a = 1.0 if s_a > s_b else (0.5 if s_a == s_b else 0.0)
        
        tot_g = g_w + g_l
        m_base, m_scale = cfg.get("MARGIN_BASE", 0.80), cfg.get("MARGIN_SCALE", 0.40)
        s_margin = max(0.80, min(1.20, m_base + (m_scale * ((g_w - g_l) / tot_g)) if tot_g > 0 else 1.0))
        
        conn = get_db_connection()
        fmt_row = conn.execute("SELECT mc_weight FROM match_formats WHERE format_id = ?", (fmt_id,)).fetchone()
        conn.close()
        mc = fmt_row["mc_weight"] if fmt_row else 1.00

        opp_rd_b = max(p3["rating_deviation"], p4["rating_deviation"]) if not is_singles else p3["rating_deviation"]
        opp_rd_a = max(p1["rating_deviation"], p2["rating_deviation"]) if not is_singles else p1["rating_deviation"]

        participants = [(p1, True, p2 if not is_singles else None, opp_rd_b), (p3, False, p4 if not is_singles else None, opp_rd_a)]
        if not is_singles:
            participants.extend([(p2, True, p1, opp_rd_b), (p4, False, p3, opp_rd_a)])

        res = []
        prov_count = sum(1 for px, _, _, _ in participants if px["is_provisional"])
        omega = [1.00, 0.75, 0.50, 0.25][min(3, max(0, prov_count - 1))]

        for p, is_a, partner, opp_rd in participants:
            flags = []
            r, rd, prov = p["latent_mmr"], p["rating_deviation"], bool(p["is_provisional"])
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
                drag = ((7.000 - r) / 7.000) * ((7.000 - r) / (7.000 - 6.300))**2.5 if r >= 6.300 else 1.0
                if r >= 6.300: flags.append("ELITE_DRAG")
                direction = 1.0 if is_a else -1.0
                raw_d = k_base * drag * mc * s_margin * g_opp * direction * (act_a - ea)

            if not is_singles and partner:
                gap = abs(r - partner["latent_mmr"])
                if not won and r > partner["latent_mmr"]:
                    dd = 0.05 if gap >= 2.0 else (0.20 if gap >= 1.5 else (0.50 if gap >= 1.0 else 1.00))
                    raw_d *= dd
                    if dd < 1.00: flags.append(f"ICE_OUT_SHIELD ({int((1-dd)*100)}%)")
                elif won and r < partner["latent_mmr"]:
                    dd = 0.25 if gap >= 2.5 else (0.50 if gap >= 1.75 else (0.75 if gap >= 1.2 else 1.00))
                    raw_d *= dd
                    if dd < 1.00: flags.append(f"ANTI_CARRY ({int((1-dd)*100)}%)")

            if session_id and session_checked_in >= 6:
                cap = cfg.get("SESSION_EXCHANGE_CAP", 0.300)
                final_d = max(-cap, min(cap, raw_d))
                flags.append("SESSION_CAP_ENABLED")
            else:
                cap = cfg.get("MAX_24H_EXCHANGE_CAP", 0.150) * (cfg.get("PROVISIONAL_CAP_MULTIPLIER", 2.5) if prov else 1.0)
                final_d = max(-cap, min(cap, raw_d))
                if abs(raw_d) > cap: flags.append("24H_CAP_ENFORCED")

            new_r = max(0.000, min(6.999, r + final_d))
            prov_rd_shrink = cfg.get("PROV_RD_SHRINK_MULTIPLIER", 0.35) if prov else 1.0
            new_rd = max(30.0, math.sqrt(1.0 / (1.0 / (rd**2) + (mc * s_margin * (g_opp**2) * omega * prov_rd_shrink) / (sig**2))))
            
            conn = get_db_connection()
            loc = conn.execute("SELECT active_bridge_count FROM locations WHERE location_id = ?", (p["home_city_id"],)).fetchone()
            bridge_k = loc["active_bridge_count"] if loc else 0
            conn.close()
            
            nm = p["verified_matches_count"] + (0 if is_dry else 1)
            no = p["unique_opponents_count"] + (0 if is_dry else 1)
            acc_comp, a_rd, a_m, a_d = cls.calc_accuracy(new_rd, nm, no, prov, bridge_k, cfg)
            
            gate = (new_rd <= cfg.get("PROVISIONAL_RD_GATE", 100.0) and nm >= cfg.get("PROVISIONAL_MIN_MATCHES", 10) and no >= cfg.get("PROVISIONAL_MIN_OPPONENTS", 5))
            new_prov = 0 if (gate or is_manual_override) else 1
            tier = "ANCHOR" if (acc_comp >= 90.0 and new_prov == 0 and new_rd <= 60.0) else ("VERIFIED" if (acc_comp >= 70.0 and new_prov == 0) else "PROVISIONAL")
            if is_manual_override and tier == "PROVISIONAL": tier = "VERIFIED"

            res.append({
                "pid": p["player_id"], "name": p["display_name"], "pre_r": r, "post_r": new_r, "delta": final_d,
                "pre_rd": rd, "post_rd": new_rd, "acc": acc_comp, "a_rd": a_rd, "a_m": a_m, "a_d": a_d,
                "tier": tier, "prov": new_prov, "flags": flags
            })
            
        return {"ta_r": ta_r, "tb_r": tb_r, "ea": ea, "mov": s_margin, "applied_m_c": mc, "res": res}

# ==============================================================================
# 3. GUI INTERFACE & HEADER
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
    sys_acc = conn.execute("SELECT AVG(rating_accuracy_pct) FROM players WHERE calibration_tier != 'INACTIVE'").fetchone()[0] or 0.0
    conn.close()

    m1, m2, m3, m4, m5, m6 = st.columns(6)
    m1.metric("Active Players", n_p)
    m2.metric("Matches", n_m)
    m3.metric("Sessions", n_s)
    m4.metric("Venues", n_v)
    m5.metric("Cities", n_c)
    m6.metric("Countries", n_co)

    st.markdown("---")
    with st.expander("💾 Database Snapshot Backup & Restore", expanded=True):
        col_b1, col_b2 = st.columns(2)
        with col_b1:
            st.markdown("#### 📥 Backup Database Snapshot")
            if os.path.exists(DB_FILE):
                with open(DB_FILE, "rb") as f:
                    st.download_button("⬇️ Download System Snapshot (.db)", f.read(), f"RYFT_V16_Backup_{datetime.now().strftime('%Y%m%d_%H%M%S')}.db", mime="application/octet-stream", use_container_width=True)
        with col_b2:
            st.markdown("#### 📤 Upload Saved State")
            up_db = st.file_uploader("Select .db file", type=["db", "sqlite"])
            if up_db and st.button("🚨 Restore Entire System", type="primary", use_container_width=True):
                with open(DB_FILE, "wb") as f: f.write(up_db.getbuffer())
                init_db(); st.success("System Restored!"); st.rerun()

    with st.expander("🚨 Advanced System Resets", expanded=False):
        r_c1, r_c2, r_c3 = st.columns(3)
        res_p = r_c1.checkbox("Reset All Players to Initial Rating")
        res_m = r_c2.checkbox("Delete Match & Session History")
        res_n = r_c3.checkbox("💣 Clean Slate (Erase All Test Data)")

        if st.button("Execute Checked Resets", type="secondary"):
            conn = get_db_connection()
            if res_n:
                for tbl in ["match_logs", "matches", "session_matches", "sessions", "players", "venues", "locations", "progression_speed_rules", "config_changelog", "player_changelog"]:
                    conn.execute(f"DELETE FROM {tbl};")
                st.warning("Database completely wiped to clean slate.")
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
    venues = conn.execute("SELECT venue_id, venue_name, city_id, country_code FROM venues WHERE is_active = 1").fetchall()
    players = conn.execute("SELECT player_id, display_name, latent_mmr, display_rating, rating_deviation, rating_accuracy_pct, is_provisional, is_manually_verified, home_venue_id, home_city_id, home_country_code FROM players WHERE calibration_tier != 'INACTIVE' ORDER BY display_name").fetchall()
    formats = conn.execute("SELECT * FROM match_formats WHERE is_active = 1 AND is_session_bound = 0").fetchall()
    conn.close()

    v_dict = {v["venue_name"]: v for v in venues}
    p_dict = {f"{p['display_name']} (MMR: {p['latent_mmr']:.3f})": p for p in players}
    f_dict = {f["format_name"]: f for f in formats}

    st.markdown("#### 1. Match Schedule & Location")
    c_s1, c_s2, c_s3 = st.columns(3)
    match_date = c_s1.date_input("Match Date", value=date.today())
    match_time = c_s2.time_input("Match Time", value=datetime.now().time())
    ven_sel = c_s3.selectbox("Venue Facility", list(v_dict.keys()) if v_dict else ["No Venues Registered"])

    c_m1, c_m2 = st.columns(2)
    match_mode = c_m1.radio("Game Configuration", ["2v2 Doubles", "1v1 Singles"], horizontal=True)
    is_singles = (match_mode == "1v1 Singles")
    fmt_sel = c_m2.selectbox("Official Scoring Format", list(f_dict.keys()) if f_dict else ["No Formats Active"])

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
            st.info(f"**Multi-Set Match ({sel_f['format_name']}):** Standard FIP sets (6-0..6-4, 7-5, 7-6).")
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

    btn_dry, btn_save = st.columns(2)
    do_dry = btn_dry.button("🔬 Execute Dry Run Simulation", use_container_width=True)
    do_save = btn_save.button("💾 Commit Match to Database", type="primary", use_container_width=True)

    if do_dry or do_save:
        if p1_pick == "-- Select --" or p3_pick == "-- Select --" or (not is_singles and (p2_pick == "-- Select --" or p4_pick == "-- Select --")):
            st.error("Please assign players to all required roster slots.")
        else:
            p1_obj, p3_obj = p_dict[p1_pick], p_dict[p3_pick]
            p2_obj = p_dict[p2_pick] if not is_singles else None
            p4_obj = p_dict[p4_pick] if not is_singles else None
            ven_obj = v_dict[ven_sel]

            sim_out = RyftV16.compute_match(p1_obj, p2_obj, p3_obj, p4_obj, sa, sb, max(gw, gl), min(gw, gl), sel_f["format_id"], ven_obj["venue_id"], is_singles, is_dry=do_dry)

            st.success(f"Match Executed! Team A Odds: {sim_out['ea']*100:.1f}% vs Team B: {(1-sim_out['ea'])*100:.1f}% | Victory Margin Factor: {sim_out['mov']:.4f}")

            for pr in sim_out["res"]:
                with st.container():
                    st.markdown(f"""
                    <div style="background-color: #f8fafc; border: 1px solid #cbd5e1; border-radius: 6px; padding: 10px; margin-bottom: 8px;">
                        <strong>{pr['name']}</strong> | Latent MMR: <code>{pr['pre_r']:.3f} ➔ {pr['post_r']:.3f}</code> (Δ <strong>{pr['delta']:+.4f}</strong>) | Display: <code>{pr['pre_r']:.2f} ➔ {pr['post_r']:.2f}</code> | RD: <code>{pr['pre_rd']:.1f} ➔ {pr['post_rd']:.1f}</code> | Accuracy: <strong>{pr['acc']:.1f}%</strong> ({pr['tier']})
                    </div>
                    """, unsafe_allow_html=True)
                    if pr["flags"]: st.caption("⚡ Active Guardrails: " + " • ".join(pr["flags"]))

            if do_save:
                conn = get_db_connection()
                m_id = f"M_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
                ts = f"{match_date}T{match_time.strftime('%H:%M:%S')}Z"

                conn.execute('''
                    INSERT INTO matches (match_id, venue_id, format_id, is_singles, team_a_p1_id, team_a_p2_id, team_b_p1_id, team_b_p2_id,
                                        score_team_a, score_team_b, set_scores_json, games_winner, games_loser, pre_rating_a, pre_rating_b,
                                        win_expectancy_a, applied_m_c, applied_s_margin, delta_r_p1, delta_r_p2, delta_r_p3, delta_r_p4, match_timestamp)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ''', (
                    m_id, ven_obj["venue_id"], sel_f["format_id"], 1 if is_singles else 0,
                    p1_obj["player_id"], p2_obj["player_id"] if not is_singles else None, p3_obj["player_id"], p4_obj["player_id"] if not is_singles else None,
                    sa, sb, json.dumps(sets_data), max(gw, gl), min(gw, gl), sim_out["ta_r"], sim_out["tb_r"], sim_out["ea"],
                    sel_f["mc_weight"], sim_out["mov"], sim_out["res"][0]["delta"], sim_out["res"][2]["delta"] if not is_singles else 0.0,
                    sim_out["res"][1]["delta"], sim_out["res"][3]["delta"] if not is_singles else 0.0, ts
                ))

                for pr in sim_out["res"]:
                    conn.execute('''UPDATE players SET latent_mmr=?, display_rating=?, rating_deviation=?, rating_accuracy_pct=?,
                                    accuracy_s_rd=?, accuracy_s_matches=?, accuracy_s_diversity=?, calibration_tier=?, is_provisional=?, last_match_time=? WHERE player_id=?''',
                                 (pr["post_r"], pr["post_r"], pr["post_rd"], pr["acc"], pr["a_rd"], pr["a_m"], pr["a_d"], pr["tier"], pr["prov"], ts, pr["pid"]))
                    
                    conn.execute('''INSERT INTO match_logs (log_id, match_id, player_id, pre_latent_mmr, post_latent_mmr, pre_display_rating, post_display_rating,
                                    pre_rd, post_rd, pre_accuracy_pct, post_accuracy_pct, delta_r, guardrails_triggered, logged_at)
                                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)''',
                                 (f"LOG_{pr['pid']}_{m_id}", m_id, pr["pid"], pr["pre_r"], pr["post_r"], pr["pre_r"], pr["post_r"], pr["pre_rd"], pr["post_rd"], pr["acc"], pr["acc"], pr["delta"], json.dumps(pr["flags"]), ts))

                conn.execute("UPDATE venues SET total_matches_played = total_matches_played + 1 WHERE venue_id = ?", (ven_obj["venue_id"],))
                conn.commit()
                conn.close()

                for pid in [p1_obj["player_id"], p3_obj["player_id"]] + ([p2_obj["player_id"], p4_obj["player_id"]] if not is_singles else []):
                    if pid: RyftV16.sync_player_aggregates(pid)
                st.balloons()
                st.success("✅ Match successfully committed to database!")
                st.rerun()

# ------------------------------------------------------------------------------
# TAB 3: CLUB SESSIONS & MIXERS (WITH DYNAMIC QUEUE & REORDERING)
# ------------------------------------------------------------------------------
elif nav == "🗓️ Club Sessions & Mixers":
    st.title("Sessions & Event Traffic Controller")
    st.caption("Manage multi-court check-ins, fixed team builder, queue prioritization, and atomic batch calibrations.")

    conn = get_db_connection()
    venues = conn.execute("SELECT venue_id, venue_name, court_count FROM venues WHERE is_active = 1").fetchall()
    players = conn.execute("SELECT player_id, display_name, latent_mmr FROM players WHERE calibration_tier != 'INACTIVE' ORDER BY display_name").fetchall()
    
    v_dict = {v["venue_name"]: v for v in venues}
    p_dict = {p["display_name"]: p["player_id"] for p in players}

    mode = st.radio("Session Navigation", ["➕ Create New Session", "🎮 Active Sessions Hub"], horizontal=True)

    if mode == "➕ Create New Session":
        st.subheader("1. Setup Session Parameters")
        
        cs1, cs2 = st.columns(2)
        s_title = cs1.text_input("Event / Session Title (e.g. Saturday League)")
        s_ven = cs2.selectbox("Hosting Club / Venue", list(v_dict.keys()) if v_dict else ["No Venues Registered"])
        
        cs3, cs4 = st.columns(2)
        s_date = cs3.date_input("Event Date", value=date.today())
        s_time = cs4.time_input("Event Time", value=datetime.now().time())

        cs5, cs6 = st.columns(2)
        s_mode = cs5.selectbox("Game Mode", ["DOUBLES", "SINGLES"])
        s_team = cs6.selectbox("Team Format", ["FIXED_TEAMS", "ROTATING_TEAMS"] if s_mode == "DOUBLES" else ["SINGLES"])
        
        # Format Compatibility Gates
        if s_team == "ROTATING_TEAMS":
            compat_formats = conn.execute("SELECT format_id, format_name, category FROM match_formats WHERE category IN ('AMERICANO', 'MEXICANO', 'RACE_GAMES') AND is_active = 1").fetchall()
        elif s_team == "FIXED_TEAMS":
            compat_formats = conn.execute("SELECT format_id, format_name, category FROM match_formats WHERE category IN ('MULTI_SET', 'RACE_GAMES') AND is_active = 1").fetchall()
        else:
            compat_formats = conn.execute("SELECT format_id, format_name, category FROM match_formats WHERE category IN ('RACE_GAMES', 'MULTI_SET') AND is_active = 1").fetchall()
        
        f_compat_dict = {f["format_name"]: f for f in compat_formats}
        s_fmt_name = st.selectbox("Official Scoring Format", list(f_compat_dict.keys()) if f_compat_dict else ["None Compatible"])
        sel_format_obj = f_compat_dict[s_fmt_name] if s_fmt_name in f_compat_dict else None

        cs7, cs8 = st.columns(2)
        s_struct = cs7.selectbox("Play Structure", ["ROUND_ROBIN", "KNOCKOUT", "ROUND_ROBIN_AND_KNOCKOUT"])
        s_rounds = cs8.number_input("Rounds to Play", min_value=1, max_value=20, value=1)

        avail_courts = v_dict[s_ven]["court_count"] if s_ven in v_dict else 1
        court_picks = st.multiselect("Select Dedicated Courts", [f"Court {i+1}" for i in range(avail_courts)], default=[f"Court {i+1}" for i in range(min(2, avail_courts))])

        st.markdown("---")
        st.markdown("#### 2. Participants & Team Formation")

        teams_created = []
        enrolled_pids = []

        if s_team == "FIXED_TEAMS":
            st.info("💡 **Fixed Teams Mode:** Create and pair your official doubles teams below. Leaving team name blank defaults to player names.")
            num_teams = st.number_input("How many teams will play?", min_value=2, max_value=32, value=4, step=2)
            
            p_names = list(p_dict.keys())
            for t_idx in range(int(num_teams)):
                st.markdown(f"**Team #{t_idx+1} Pairing**")
                tc1, tc2, tc3 = st.columns([2, 3, 3])
                t_name = tc1.text_input(f"Team {t_idx+1} Name (Optional)", value="", placeholder="Leave blank to use names", key=f"t_name_{t_idx}")
                tp1 = tc2.selectbox(f"Player 1", ["-- Select --"] + p_names, key=f"t_p1_{t_idx}")
                tp2 = tc3.selectbox(f"Player 2", ["-- Select --"] + p_names, key=f"t_p2_{t_idx}")
                if tp1 != "-- Select --" and tp2 != "-- Select --":
                    teams_created.append({"team_name": t_name.strip(), "p1": p_dict[tp1], "p2": p_dict[tp2]})
                    enrolled_pids.extend([p_dict[tp1], p_dict[tp2]])

        else:
            enrolled_names = st.multiselect("Enroll Registered Players (Rotating Roster)", list(p_dict.keys()))
            enrolled_pids = [p_dict[p] for p in enrolled_names]

        if st.button("🚀 Create Session & Generate Fixtures", type="primary"):
            min_req = 4 if s_mode == "DOUBLES" else 2
            if len(enrolled_pids) < min_req:
                st.error(f"❌ Incompatible: Requires at least {min_req} participants.")
            elif s_team == "FIXED_TEAMS" and len(teams_created) < 2:
                st.error("❌ Please setup at least 2 complete teams.")
            elif not court_picks:
                st.error("❌ Please select at least one court.")
            else:
                s_id = f"SESS_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
                conn.execute("""
                    INSERT INTO sessions (session_id, venue_id, session_title, match_mode, team_format, format_id,
                                        tourney_structure, court_ids_json, enrolled_player_ids, teams_json, player_count, total_rounds, created_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, (s_id, v_dict[s_ven]["venue_id"], s_title, s_mode, s_team, sel_format_obj["format_id"],
                      s_struct, json.dumps(court_picks), json.dumps(enrolled_pids), json.dumps(teams_created),
                      len(enrolled_pids), int(s_rounds), datetime.now(timezone.utc).isoformat()))

                fixture_idx = 1
                if s_team == "FIXED_TEAMS":
                    t_list = list(teams_created)
                    if len(t_list) % 2 != 0:
                        t_list.append(None) # Bye
                    num_teams_eff = len(t_list)

                    for r in range(int(s_rounds)):
                        curr_round_matches = []
                        for i in range(num_teams_eff // 2):
                            t1 = t_list[i]
                            t2 = t_list[num_teams_eff - 1 - i]
                            if t1 is not None and t2 is not None:
                                curr_round_matches.append((t1, t2))
                        
                        for m_i, (team_a, team_b) in enumerate(curr_round_matches):
                            crt = court_picks[m_i % len(court_picks)]
                            sm_id = f"SM_{s_id}_{r+1}_{m_i+1}"
                            conn.execute("""
                                INSERT INTO session_matches (session_match_id, session_id, round_number, court_id, match_order,
                                                            team_a_p1_id, team_a_p2_id, team_b_p1_id, team_b_p2_id,
                                                            team_a_name, team_b_name, match_status)
                                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'SCHEDULED')
                            """, (sm_id, s_id, r+1, crt, fixture_idx, team_a["p1"], team_a["p2"], team_b["p1"], team_b["p2"], team_a["team_name"], team_b["team_name"]))
                            fixture_idx += 1

                        t_list = [t_list[0]] + [t_list[-1]] + t_list[1:-1]

                elif s_team == "ROTATING_TEAMS":
                    for rnd in range(int(s_rounds)):
                        rotated = enrolled_pids[rnd:] + enrolled_pids[:rnd]
                        for c_idx, crt in enumerate(court_picks):
                            offset = c_idx * 4
                            if offset + 4 <= len(rotated):
                                g_p = rotated[offset:offset+4]
                                sm_id = f"SM_{s_id}_{rnd+1}_{c_idx+1}"
                                conn.execute("""
                                    INSERT INTO session_matches (session_match_id, session_id, round_number, court_id, match_order,
                                                                team_a_p1_id, team_a_p2_id, team_b_p1_id, team_b_p2_id, match_status)
                                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'SCHEDULED')
                                """, (sm_id, s_id, rnd+1, crt, fixture_idx, g_p[0], g_p[1], g_p[2], g_p[3]))
                                fixture_idx += 1

                conn.commit()
                st.success(f"Session '{s_title}' created with {fixture_idx-1} fixtures scheduled!")
                st.rerun()

    elif mode == "🎮 Active Sessions Hub":
        open_sess = conn.execute("""SELECT s.*, v.venue_name, f.format_name, f.category as fmt_cat 
                                    FROM sessions s JOIN venues v ON s.venue_id = v.venue_id 
                                    JOIN match_formats f ON s.format_id = f.format_id 
                                    WHERE s.session_status IN ('CONFIG', 'LIVE')""").fetchall()
        if not open_sess:
            st.info("No active sessions currently running. Use 'Create New Session' to start an event.")
        else:
            sel_s_title = st.selectbox("Select Session to Manage", [s["session_title"] for s in open_sess])
            s_data = [s for s in open_sess if s["session_title"] == sel_s_title][0]
            s_id = s_data["session_id"]

            enrolled_ids = json.loads(s_data["enrolled_player_ids"])
            checked_in_ids = json.loads(s_data["checked_in_player_ids"])
            crts = json.loads(s_data["court_ids_json"])
            id_to_name = {p["player_id"]: p["display_name"] for p in players}

            # Mini-Header with Discard Option
            hdr1, hdr2 = st.columns([4, 1])
            hdr1.markdown(f"""
            <div style="background-color: #f1f5f9; padding: 12px; border-radius: 8px; border-left: 5px solid #0284c7;">
                <h3 style="margin:0; color:#0f172a;">{s_data['session_title']} <span style="font-size:0.6em; color:#64748b;">({s_data['session_id']})</span></h3>
                <strong>Venue:</strong> {s_data['venue_name']} | <strong>Format:</strong> {s_data['format_name']} | <strong>Mode:</strong> {s_data['team_format']} | <strong>Courts:</strong> {len(crts)}
            </div>
            """, unsafe_allow_html=True)

            with hdr2:
                if st.button("🗑️ Discard Session", type="secondary"):
                    conn.execute("DELETE FROM session_matches WHERE session_id = ?", (s_id,))
                    conn.execute("DELETE FROM sessions WHERE session_id = ?", (s_id,))
                    conn.commit()
                    st.warning("Session discarded.")
                    st.rerun()

            sub_tab = st.radio("Session Console", ["📋 Check-In Drawer", "🏟️ Matches Hub", "📊 Standings & Submit"], horizontal=True)

            # --- SUB-TAB 1: CHECK-IN DRAWER ---
            if sub_tab == "📋 Check-In Drawer":
                st.subheader("Roster Check-In Drawer")
                st.metric("Checked-In Status", f"{len(checked_in_ids)} of {len(enrolled_ids)} Ready")

                with st.form("check_in_drawer_form"):
                    updated_checkins = []
                    for pid in enrolled_ids:
                        p_name = id_to_name.get(pid, pid)
                        if st.checkbox(f"✅ {p_name}", value=(pid in checked_in_ids), key=f"ci_{pid}"):
                            updated_checkins.append(pid)

                    if st.form_submit_button("Save Check-In Updates"):
                        conn.execute("UPDATE sessions SET checked_in_player_ids = ?, active_checked_in_count = ?, session_status = 'LIVE' WHERE session_id = ?",
                                     (json.dumps(updated_checkins), len(updated_checkins), s_id))
                        conn.commit(); st.success("Check-In status locked!"); st.rerun()

            # --- SUB-TAB 2: MATCHES HUB (DYNAMIC DISPATCH & FUNGIBLE SCORING) ---
            elif sub_tab == "🏟️ Matches Hub":
                st.subheader("Court Traffic Controller & Fixtures")
                fixtures = conn.execute("SELECT * FROM session_matches WHERE session_id = ? ORDER BY match_order ASC, round_number ASC", (s_id,)).fetchall()
                fmt_category = s_data["fmt_cat"]

                # Helper to check readiness
                def check_readiness(m):
                    participants = [p for p in [m['team_a_p1_id'], m['team_a_p2_id'], m['team_b_p1_id'], m['team_b_p2_id']] if p]
                    missing = [p for p in participants if p not in checked_in_ids]
                    return len(missing) == 0, [id_to_name.get(x, x) for x in missing]

                # FUNGIBLE MATCH SWAP / ORDER REASSIGNMENT EXPANDER
                with st.expander("🔀 Reorder Schedule / Swap Match Order", expanded=False):
                    st.caption("Change fixture sequence or move any match forward in the queue.")
                    re1, re2 = st.columns(2)
                    sched_matches = [m for m in fixtures if m["match_status"] in ("SCHEDULED", "LIVE")]
                    if len(sched_matches) >= 2:
                        m_choice_1 = re1.selectbox("Move Match", sched_matches, format_func=lambda x: f"Match #{x['match_order']} ({x['court_id']} R{x['round_number']})", key="sw1")
                        m_choice_2 = re2.selectbox("Swap Position With", sched_matches, format_func=lambda x: f"Match #{x['match_order']} ({x['court_id']} R{x['round_number']})", key="sw2")
                        if st.button("Execute Queue Swap"):
                            conn.execute("UPDATE session_matches SET match_order=? WHERE session_match_id=?", (m_choice_2["match_order"], m_choice_1["session_match_id"]))
                            conn.execute("UPDATE session_matches SET match_order=? WHERE session_match_id=?", (m_choice_1["match_order"], m_choice_2["session_match_id"]))
                            conn.commit()
                            st.success("Queue reordered successfully!")
                            st.rerun()
                    else:
                        st.info("Need at least 2 active matches to reorder.")

                # Partition into Queue Buckets
                ready_matches = []
                waiting_matches = []
                completed_matches = []

                for m in fixtures:
                    if m["match_status"] in ("STAGED", "COMMITTED"):
                        completed_matches.append(m)
                    else:
                        is_ready, missing = check_readiness(m)
                        if is_ready:
                            ready_matches.append(m)
                        else:
                            waiting_matches.append((m, missing))

                # SECTION: UPCOMING & READY MATCHES
                st.markdown("### 🟢 Next Up & Ready to Play (All Players Present)")
                if not ready_matches:
                    st.info("No matches have all 4 players checked in. Complete check-ins in the drawer.")
                else:
                    for m in ready_matches:
                        p1_n = id_to_name.get(m['team_a_p1_id'], 'Player A1')
                        p2_n = id_to_name.get(m['team_a_p2_id'], '')
                        p3_n = id_to_name.get(m['team_b_p1_id'], 'Player B1')
                        p4_n = id_to_name.get(m['team_b_p2_id'], '')

                        team_a_players = f"{p1_n} & {p2_n}" if p2_n else p1_n
                        team_b_players = f"{p3_n} & {p4_n}" if p4_n else p3_n
                        raw_ta = m["team_a_name"] or ""
                        raw_tb = m["team_b_name"] or ""

                        display_ta = team_a_players if (not raw_ta or raw_ta.strip().lower() in ["team a", "team 1", "team 2", "team 3", "team 4", ""]) else f"{raw_ta} ({team_a_players})"
                        display_tb = team_b_players if (not raw_tb or raw_tb.strip().lower() in ["team b", "team 1", "team 2", "team 3", "team 4", ""]) else f"{raw_tb} ({team_b_players})"

                        with st.expander(f"🟢 #{m['match_order']} • {m['court_id']} (Round {m['round_number']}) — {display_ta} vs {display_tb}", expanded=(m["match_status"] == "LIVE")):
                            st.markdown(f"""
                            <div style="display: flex; justify-content: space-between; align-items: center; background: #f0fdf4; border: 1px solid #bbf7d0; padding: 10px 14px; border-radius: 6px; margin-bottom: 12px;">
                                <div style="font-weight: 700; color: #15803d; text-align: left; width: 45%; font-size: 1.05em;">🔵 {display_ta}</div>
                                <div style="font-weight: 900; color: #64748b; text-align: center; width: 10%; font-size: 1.1em;">VS</div>
                                <div style="font-weight: 700; color: #b91c1c; text-align: right; width: 45%; font-size: 1.05em;">🔴 {display_tb}</div>
                            </div>
                            """, unsafe_allow_html=True)

                            with st.form(f"score_form_{m['session_match_id']}"):
                                sets_recorded = []
                                sa, sb = 0, 0
                                gw, gl = 0, 0

                                if fmt_category == "MULTI_SET":
                                    s1c1, s1c2 = st.columns(2)
                                    s1a = s1c1.number_input(f"Set 1: {team_a_players}", 0, 7, 6, key=f"s1a_{m['session_match_id']}")
                                    s1b = s1c2.number_input(f"Set 1: {team_b_players}", 0, 7, 3, key=f"s1b_{m['session_match_id']}")
                                    sets_recorded.append((s1a, s1b))

                                    s2c1, s2c2 = st.columns(2)
                                    s2a = s2c1.number_input(f"Set 2: {team_a_players}", 0, 7, 6, key=f"s2a_{m['session_match_id']}")
                                    s2b = s2c2.number_input(f"Set 2: {team_b_players}", 0, 7, 4, key=f"s2b_{m['session_match_id']}")
                                    sets_recorded.append((s2a, s2b))

                                    sa = (1 if s1a > s1b else 0) + (1 if s2a > s2b else 0)
                                    sb = (1 if s1b > s1a else 0) + (1 if s2b > s2a else 0)

                                    if sa == 1 and sb == 1:
                                        st.warning("Sets split 1-1. Set 3 Decider Unlocked:")
                                        s3c1, s3c2 = st.columns(2)
                                        s3a = s3c1.number_input(f"Set 3: {team_a_players}", 0, 7, 6, key=f"s3a_{m['session_match_id']}")
                                        s3b = s3c2.number_input(f"Set 3: {team_b_players}", 0, 7, 4, key=f"s3b_{m['session_match_id']}")
                                        sets_recorded.append((s3a, s3b))
                                        if s3a > s3b: sa += 1
                                        else: sb += 1

                                    gw = sum(x[0] for x in sets_recorded)
                                    gl = sum(x[1] for x in sets_recorded)

                                elif fmt_category == "RACE_GAMES":
                                    rg1, rg2 = st.columns(2)
                                    gw = rg1.number_input(f"Games Won ({team_a_players})", 0, 30, 6, key=f"ga_{m['session_match_id']}")
                                    gl = rg2.number_input(f"Games Won ({team_b_players})", 0, 30, 4, key=f"gb_{m['session_match_id']}")
                                    sa, sb = gw, gl
                                    sets_recorded.append((gw, gl))

                                elif fmt_category in ("AMERICANO", "MEXICANO"):
                                    ap1, ap2 = st.columns(2)
                                    sa = ap1.number_input(f"Points ({team_a_players})", 0, 50, 12, key=f"pa_{m['session_match_id']}")
                                    sb = ap2.number_input(f"Points ({team_b_players})", 0, 50, 12, key=f"pb_{m['session_match_id']}")
                                    gw, gl = sa, sb
                                    sets_recorded.append((sa, sb))

                                m_stat = st.selectbox("Court Status", ["SCHEDULED", "LIVE", "STAGED"], index=["SCHEDULED", "LIVE", "STAGED"].index(m['match_status']), key=f"st_{m['session_match_id']}")

                                if st.form_submit_button("Stage Score in Memory"):
                                    conn.execute("""
                                        UPDATE session_matches SET score_team_a=?, score_team_b=?, games_winner=?, games_loser=?,
                                                                  set_scores_json=?, match_status=? WHERE session_match_id=?
                                    """, (sa, sb, max(gw, gl), min(gw, gl), json.dumps(sets_recorded), m_stat, m['session_match_id']))
                                    conn.commit()
                                    st.success("Score Staged!")
                                    st.rerun()

                # SECTION: WAITING ON CHECK-IN
                if waiting_matches:
                    st.markdown("---")
                    st.markdown("### ⏳ Waiting for Player Arrival (Deprioritized)")
                    for m, missing in waiting_matches:
                        p1_n = id_to_name.get(m['team_a_p1_id'], 'P1')
                        p2_n = id_to_name.get(m['team_a_p2_id'], '')
                        p3_n = id_to_name.get(m['team_b_p1_id'], 'P3')
                        p4_n = id_to_name.get(m['team_b_p2_id'], '')
                        t_a = f"{p1_n} & {p2_n}" if p2_n else p1_n
                        t_b = f"{p3_n} & {p4_n}" if p4_n else p3_n
                        st.caption(f"⚠️ #{m['match_order']} • {m['court_id']} (Round {m['round_number']}) — {t_a} vs {t_b} • **Missing Check-In:** {', '.join(missing)}")

                # SECTION: COMPLETED MATCHES
                if completed_matches:
                    st.markdown("---")
                    st.markdown(f"### ✅ Staged & Completed Fixtures ({len(completed_matches)})")
                    for m in completed_matches:
                        p1_n = id_to_name.get(m['team_a_p1_id'], 'P1')
                        p2_n = id_to_name.get(m['team_a_p2_id'], '')
                        p3_n = id_to_name.get(m['team_b_p1_id'], 'P3')
                        p4_n = id_to_name.get(m['team_b_p2_id'], '')
                        t_a = f"{p1_n} & {p2_n}" if p2_n else p1_n
                        t_b = f"{p3_n} & {p4_n}" if p4_n else p3_n
                        st.write(f"✓ Match #{m['match_order']} ({m['court_id']}) — **{t_a}** `{m['score_team_a']}` vs `{m['score_team_b']}` **{t_b}** [STAGED]")

            # --- SUB-TAB 3: STANDINGS & SUBMIT ---
            elif sub_tab == "📊 Standings & Submit":
                st.subheader("Event Standings & Final Engine Commit")
                staged_matches = conn.execute("SELECT * FROM session_matches WHERE session_id = ? AND match_status = 'STAGED'", (s_id,)).fetchall()
                
                standings = {}
                if s_data["team_format"] == "FIXED_TEAMS":
                    teams_meta = json.loads(s_data["teams_json"])
                    for t in teams_meta:
                        p1_n = id_to_name.get(t["p1"], "")
                        p2_n = id_to_name.get(t["p2"], "")
                        t_label = t["team_name"] if t["team_name"].strip() else f"{p1_n} & {p2_n}"
                        standings[t_label] = {"Participant": t_label, "Matches": 0, "Won": 0, "Lost": 0, "Score For": 0, "Score Against": 0, "Diff": 0}
                    
                    for sm in staged_matches:
                        p1_n = id_to_name.get(sm['team_a_p1_id'], '')
                        p2_n = id_to_name.get(sm['team_a_p2_id'], '')
                        p3_n = id_to_name.get(sm['team_b_p1_id'], '')
                        p4_n = id_to_name.get(sm['team_b_p2_id'], '')
                        tA = sm["team_a_name"] if sm["team_a_name"].strip() else f"{p1_n} & {p2_n}"
                        tB = sm["team_b_name"] if sm["team_b_name"].strip() else f"{p3_n} & {p4_n}"

                        if tA in standings and tB in standings:
                            standings[tA]["Matches"] += 1; standings[tB]["Matches"] += 1
                            standings[tA]["Score For"] += sm["score_team_a"]; standings[tB]["Score For"] += sm["score_team_b"]
                            standings[tA]["Score Against"] += sm["score_team_b"]; standings[tB]["Score Against"] += sm["score_team_a"]
                            if sm["score_team_a"] > sm["score_team_b"]: standings[tA]["Won"] += 1; standings[tB]["Lost"] += 1
                            elif sm["score_team_a"] < sm["score_team_b"]: standings[tB]["Won"] += 1; standings[tA]["Lost"] += 1
                else:
                    for pid in enrolled_ids:
                        standings[pid] = {"Participant": id_to_name.get(pid, pid), "Matches": 0, "Won": 0, "Lost": 0, "Score For": 0, "Score Against": 0, "Diff": 0}
                    
                    for sm in staged_matches:
                        for pid in [sm["team_a_p1_id"], sm["team_a_p2_id"]]:
                            if pid and pid in standings:
                                standings[pid]["Matches"] += 1
                                standings[pid]["Score For"] += sm["score_team_a"]
                                standings[pid]["Score Against"] += sm["score_team_b"]
                                if sm["score_team_a"] > sm["score_team_b"]: standings[pid]["Won"] += 1
                                elif sm["score_team_a"] < sm["score_team_b"]: standings[pid]["Lost"] += 1
                        for pid in [sm["team_b_p1_id"], sm["team_b_p2_id"]]:
                            if pid and pid in standings:
                                standings[pid]["Matches"] += 1
                                standings[pid]["Score For"] += sm["score_team_b"]
                                standings[pid]["Score Against"] += sm["score_team_a"]
                                if sm["score_team_b"] > sm["score_team_a"]: standings[pid]["Won"] += 1
                                elif sm["score_team_b"] < sm["score_team_a"]: standings[pid]["Lost"] += 1

                for item in standings.values():
                    item["Diff"] = item["Score For"] - item["Score Against"]

                df_stand = pd.DataFrame(list(standings.values())).sort_values(by=["Score For", "Diff"], ascending=False)
                st.dataframe(df_stand, use_container_width=True)

                st.markdown("---")
                col_sub1, col_sub2 = st.columns(2)

                if col_sub1.button("💾 Save Session Draft (In-Memory)", use_container_width=True):
                    st.success("Draft saved. Fixtures remain in staging.")

                if col_sub2.button("🚀 VERIFY & COMMIT SESSION TO ENGINE", type="primary", use_container_width=True):
                    if not staged_matches:
                        st.error("No staged matches with completed scores to submit.")
                    else:
                        ts = datetime.now(timezone.utc).isoformat()
                        delta_summary = []

                        for sm in staged_matches:
                            p1_d = dict(conn.execute("SELECT * FROM players WHERE player_id=?", (sm["team_a_p1_id"],)).fetchone())
                            p3_d = dict(conn.execute("SELECT * FROM players WHERE player_id=?", (sm["team_b_p1_id"],)).fetchone())
                            p2_d = dict(conn.execute("SELECT * FROM players WHERE player_id=?", (sm["team_a_p2_id"],)).fetchone()) if sm["team_a_p2_id"] else None
                            p4_d = dict(conn.execute("SELECT * FROM players WHERE player_id=?", (sm["team_b_p2_id"],)).fetchone()) if sm["team_b_p2_id"] else None
                            
                            is_sing = (s_data["match_mode"] == "SINGLES")
                            out = RyftV16.compute_match(
                                p1_d, p2_d, p3_d, p4_d, sm["score_team_a"], sm["score_team_b"],
                                max(sm["games_winner"], sm["score_team_a"]), min(sm["games_loser"], sm["score_team_b"]),
                                s_data["format_id"], s_data["venue_id"], is_singles=is_sing,
                                session_id=s_id, session_checked_in=s_data["active_checked_in_count"]
                            )

                            m_id = f"M_SESS_{sm['session_match_id']}"
                            conn.execute('''
                                INSERT INTO matches (match_id, venue_id, format_id, session_id, is_singles, team_a_p1_id, team_a_p2_id, team_b_p1_id, team_b_p2_id,
                                                    score_team_a, score_team_b, set_scores_json, games_winner, games_loser, pre_rating_a, pre_rating_b,
                                                    win_expectancy_a, applied_m_c, applied_s_margin, delta_r_p1, delta_r_p2, delta_r_p3, delta_r_p4, match_timestamp)
                                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                            ''', (
                                m_id, s_data["venue_id"], s_data["format_id"], s_id, 1 if is_sing else 0,
                                p1_d["player_id"], p2_d["player_id"] if p2_d else None, p3_d["player_id"], p4_d["player_id"] if p4_d else None,
                                sm["score_team_a"], sm["score_team_b"], sm["set_scores_json"], max(sm["games_winner"], sm["score_team_a"]), min(sm["games_loser"], sm["score_team_b"]),
                                out["ta_r"], out["tb_r"], out["ea"], out["applied_m_c"], out["mov"],
                                out["res"][0]["delta"], out["res"][2]["delta"] if not is_sing else 0.0,
                                out["res"][1]["delta"], out["res"][3]["delta"] if not is_sing else 0.0, ts
                            ))

                            for pr in out["res"]:
                                conn.execute("""UPDATE players SET latent_mmr=?, display_rating=?, rating_deviation=?, rating_accuracy_pct=?, calibration_tier=?, is_provisional=? WHERE player_id=?""",
                                             (pr["post_r"], pr["post_r"], pr["post_rd"], pr["acc"], pr["tier"], pr["prov"], pr["pid"]))
                                
                                conn.execute("""INSERT INTO match_logs (log_id, match_id, player_id, pre_latent_mmr, post_latent_mmr, pre_display_rating, post_display_rating, pre_rd, post_rd, pre_accuracy_pct, post_accuracy_pct, delta_r, logged_at)
                                                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                                             (f"L_{pr['pid']}_{m_id}", m_id, pr["pid"], pr["pre_r"], pr["post_r"], pr["pre_r"], pr["post_r"], pr["pre_rd"], pr["post_rd"], pr["acc"], pr["acc"], pr["delta"], ts))
                                
                                delta_summary.append({"Player": pr["name"], "Pre MMR": f"{pr['pre_r']:.3f}", "Delta": f"{pr['delta']:+.4f}", "Post MMR": f"{pr['post_r']:.3f}", "Tier": pr["tier"]})

                            conn.execute("UPDATE session_matches SET match_status='COMMITTED', committed_match_id=? WHERE session_match_id=?", (m_id, sm['session_match_id']))
                            for px in out["res"]: RyftV16.sync_player_aggregates(px["pid"])

                        conn.execute("UPDATE sessions SET session_status='COMPLETED', completed_at=? WHERE session_id=?", (ts, s_id))
                        conn.commit()
                        st.balloons()
                        st.success("✅ Session committed! Player ratings updated.")
                        st.dataframe(pd.DataFrame(delta_summary), use_container_width=True)

    conn.close()

# ------------------------------------------------------------------------------
# TAB 4: HISTORICAL MATCHES
# ------------------------------------------------------------------------------
elif nav == "📜 Historical Matches":
    st.title("Historical Matches & Deep Algorithmic Audit Ledger")
    
    conn = get_db_connection()
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Total Matches", conn.execute("SELECT COUNT(*) FROM matches").fetchone()[0])
    c2.metric("Venue Bridges", conn.execute("SELECT COUNT(*) FROM matches WHERE is_venue_bridge = 1").fetchone()[0])
    c3.metric("City Bridges", conn.execute("SELECT COUNT(*) FROM matches WHERE is_city_bridge = 1").fetchone()[0])
    c4.metric("Country Bridges", conn.execute("SELECT COUNT(*) FROM matches WHERE is_country_bridge = 1").fetchone()[0])

    st.markdown("### Search & Filters")
    f1, f2, f3 = st.columns(3)
    players = conn.execute("SELECT player_id, display_name FROM players").fetchall()
    p_map = {p["display_name"]: p["player_id"] for p in players}
    s_player = f1.selectbox("Filter by Player", ["All Players"] + list(p_map.keys()))
    s_venue = f2.selectbox("Filter by Venue", ["All Venues"] + [r["venue_name"] for r in conn.execute("SELECT venue_name FROM venues").fetchall()])
    s_city = f3.selectbox("Filter by City", ["All Cities"] + [r["location_name"] for r in conn.execute("SELECT location_name FROM locations WHERE location_type = 'CITY'").fetchall()])

    sql = """
        SELECT m.*, v.venue_name, l.location_name as city, f.format_name,
               p1.display_name as p1n, p2.display_name as p2n, p3.display_name as p3n, p4.display_name as p4n
        FROM matches m 
        JOIN venues v ON m.venue_id = v.venue_id 
        JOIN locations l ON v.city_id = l.location_id
        JOIN match_formats f ON m.format_id = f.format_id
        LEFT JOIN players p1 ON m.team_a_p1_id = p1.player_id 
        LEFT JOIN players p2 ON m.team_a_p2_id = p2.player_id
        LEFT JOIN players p3 ON m.team_b_p1_id = p3.player_id 
        LEFT JOIN players p4 ON m.team_b_p2_id = p4.player_id
        WHERE 1=1
    """
    params = []
    if s_player != "All Players":
        sql += " AND (? IN (m.team_a_p1_id, m.team_a_p2_id, m.team_b_p1_id, m.team_b_p2_id))"
        params.append(p_map[s_player])
    if s_venue != "All Venues":
        sql += " AND v.venue_name = ?"
        params.append(s_venue)
    if s_city != "All Cities":
        sql += " AND l.location_name = ?"
        params.append(s_city)

    sql += " ORDER BY m.match_timestamp DESC"
    matches = conn.execute(sql, params).fetchall()

    for m in matches:
        with st.expander(f"🎾 {m['match_timestamp'][:10]} | {m['venue_name']} ({m['city']}) | Score: {m['score_team_a']}-{m['score_team_b']} ({m['format_name']})"):
            st.write(f"**Team A:** {m['p1n']}" + (f" & {m['p2n']}" if not m['is_singles'] else "") + f" | ΔR: `{m['delta_r_p1']:+.4f}`")
            st.write(f"**Team B:** {m['p3n']}" + (f" & {m['p4n']}" if not m['is_singles'] else "") + f" | ΔR: `{m['delta_r_p3']:+.4f}`")
            st.caption(f"Victory Margin Factor: **{m['applied_s_margin']:.4f}**")

            p_logs = conn.execute("SELECT ml.*, p.display_name FROM match_logs ml JOIN players p ON ml.player_id = p.player_id WHERE ml.match_id = ?", (m['match_id'],)).fetchall()
            st.markdown("##### Participant Level Calculations")
            for pl in p_logs:
                st.write(f"- **{pl['display_name']}**: MMR `{pl['pre_latent_mmr']:.3f} ➔ {pl['post_latent_mmr']:.3f}` | Δ: `{pl['delta_r']:+.4f}`")
    conn.close()

# ------------------------------------------------------------------------------
# TAB 5: PLAYER ROSTER & CALIBRATION (24-COLUMN ERROR-FREE INSERTS)
# ------------------------------------------------------------------------------
elif nav == "👥 Player Roster & Calibration":
    st.title("Players Directory & Calibration Roster")

    conn = get_db_connection()
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Total Players", conn.execute("SELECT COUNT(*) FROM players WHERE calibration_tier != 'INACTIVE'").fetchone()[0])
    c2.metric("Provisional [PR]", conn.execute("SELECT COUNT(*) FROM players WHERE is_provisional = 1 AND calibration_tier != 'INACTIVE'").fetchone()[0])
    c3.metric("Verified", conn.execute("SELECT COUNT(*) FROM players WHERE is_provisional = 0 AND calibration_tier != 'INACTIVE'").fetchone()[0])
    c4.metric("Anchors", conn.execute("SELECT COUNT(*) FROM players WHERE is_anchor = 1").fetchone()[0])

    sql = """
        SELECT p.player_id, p.display_name, p.all_time_badge, p.initial_rating, p.latent_mmr, p.display_rating,
               p.rating_deviation, p.rating_accuracy_pct, p.calibration_tier, p.is_provisional, p.is_manually_verified,
               p.verified_matches_count, p.unique_opponents_count, p.is_active_bridge, l.location_name as city
        FROM players p JOIN locations l ON p.home_city_id = l.location_id
        WHERE p.calibration_tier != 'INACTIVE' ORDER BY p.latent_mmr DESC
    """
    df_display = pd.read_sql_query(sql, conn)
    st.dataframe(df_display, use_container_width=True)

    col_add, col_edit = st.columns(2)
    with col_add:
        with st.expander("➕ Register New Player", expanded=False):
            with st.form("add_player_form"):
                p_name = st.text_input("Full Name")
                in_cats = ["Beginner (0.500)", "Beginner+ (1.000)", "Intermediate (2.500)", "Intermediate+ (3.500)", "Advanced (4.500)", "Pro (5.500)", "Elite (6.300)"]
                in_pick = st.selectbox("Base Calibration Category", in_cats)
                cities = conn.execute("SELECT location_id, location_name, country_code FROM locations WHERE location_type = 'CITY'").fetchall()
                c_sel = st.selectbox("Home City", [c["location_name"] for c in cities]) if cities else None
                venues = conn.execute("SELECT venue_id, venue_name FROM venues WHERE is_active = 1").fetchall()
                v_sel = st.selectbox("Home Club / Venue (Optional)", ["None"] + [v["venue_name"] for v in venues])

                c_a1, c_a2 = st.columns(2)
                is_anc = c_a1.checkbox("System Anchor")
                is_ceil = c_a2.checkbox("Ceiling Anchor")

                if st.form_submit_button("Commit Registration"):
                    if not cities:
                        st.error("Create at least one City in 'Venues & Regions' first.")
                    elif not p_name:
                        st.error("Player name cannot be blank.")
                    else:
                        base_map = {
                            "Beginner (0.500)": 0.500, "Beginner+ (1.000)": 1.000, "Intermediate (2.500)": 2.500,
                            "Intermediate+ (3.500)": 3.500, "Advanced (4.500)": 4.500, "Pro (5.500)": 5.500, "Elite (6.300)": 6.300
                        }
                        base_r = base_map[in_pick]
                        cat_name = in_pick.split(" ")[0]
                        c_row = [c for c in cities if c["location_name"] == c_sel][0]
                        v_id = [v["venue_id"] for v in venues if v["venue_name"] == v_sel][0] if v_sel != "None" else None
                        p_uuid = f"P_{datetime.now().strftime('%d%H%M%S')}"

                        conn.execute("""
                            INSERT INTO players (
                                player_id, display_name, initial_rating, home_venue_id, home_city_id, home_country_code,
                                latent_mmr, display_rating, rolling_90d_peak, rolling_180d_peak, rolling_365d_peak,
                                tournament_floor, all_time_badge, rating_deviation, rating_accuracy_pct, accuracy_s_rd,
                                accuracy_s_matches, accuracy_s_diversity, calibration_tier, is_provisional,
                                is_manually_verified, is_anchor, is_ceiling_anchor, created_at
                            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0.0, ?, 350.0, 0.0, 0.0, 0.0, 0.0, 'PROVISIONAL', 1, 0, ?, ?, ?)
                        """, (
                            p_uuid, p_name, base_r, v_id, c_row["location_id"], c_row["country_code"] or 'IND',
                            base_r, base_r, base_r, base_r, base_r,
                            cat_name, 1 if is_anc else 0, 1 if is_ceil else 0, datetime.now(timezone.utc).isoformat()
                        ))
                        conn.commit()
                        st.success(f"Registered {p_name} initialized to {base_r:.3f} ({cat_name})!")
                        st.rerun()

    with col_edit:
        with st.expander("✏️ Inspect & Edit Player", expanded=False):
            all_p = conn.execute("SELECT player_id, display_name FROM players ORDER BY display_name").fetchall()
            if all_p:
                p_pick = st.selectbox("Select Player to Inspect", [p["player_id"] for p in all_p], format_func=lambda x: [p["display_name"] for p in all_p if p["player_id"] == x][0])
                p_data = dict(conn.execute("SELECT * FROM players WHERE player_id = ?", (p_pick,)).fetchone())

                st.write(f"**Player:** {p_data['display_name']} ({p_data['all_time_badge']})")
                st.caption(f"Initial: `{p_data['initial_rating']:.3f}` | Current MMR: `{p_data['latent_mmr']:.3f}` | Display: `{p_data['display_rating']:.2f}`")

                with st.form("edit_player_form"):
                    e_name = st.text_input("Edit Name", value=p_data["display_name"])
                    e_mmr = st.number_input("Latent MMR Override", value=float(p_data["latent_mmr"]), step=0.01, format="%.3f")
                    e_rd = st.number_input("Rating Deviation (RD)", value=float(p_data["rating_deviation"]), step=5.0)
                    e_man_ver = st.checkbox("Manually Verified (Override Tri-Gate Demotion)", value=bool(p_data["is_manually_verified"]))
                    e_anc = st.checkbox("System Anchor", value=bool(p_data["is_anchor"]))
                    e_ceil = st.checkbox("Ceiling Anchor", value=bool(p_data["is_ceiling_anchor"]))

                    if st.form_submit_button("Save Overrides"):
                        cat_str, _, _ = RyftV16.get_cat_for_rating(e_mmr)
                        conn.execute("""UPDATE players SET display_name=?, latent_mmr=?, display_rating=?, rating_deviation=?, is_manually_verified=?, is_anchor=?, is_ceiling_anchor=?, all_time_badge=? WHERE player_id=?""",
                                     (e_name, e_mmr, e_mmr, e_rd, 1 if e_man_ver else 0, 1 if e_anc else 0, 1 if e_ceil else 0, cat_str, p_pick))
                        if e_man_ver:
                            conn.execute("UPDATE players SET calibration_tier = 'VERIFIED', is_provisional = 0 WHERE player_id = ?", (p_pick,))
                        conn.commit()
                        st.success("Player updated!"); st.rerun()

                c_del1, c_del2 = st.columns(2)
                if c_del1.button("Deactivate (Soft Delete)"):
                    conn.execute("UPDATE players SET calibration_tier = 'INACTIVE' WHERE player_id = ?", (p_pick,))
                    conn.commit(); st.warning("Player deactivated."); st.rerun()
                if c_del2.button("💣 Force Purge (Delete Record)"):
                    conn.execute("DELETE FROM players WHERE player_id = ?", (p_pick,))
                    conn.commit(); st.warning("Player hard deleted."); st.rerun()
    conn.close()

# ------------------------------------------------------------------------------
# TAB 6: VENUES & REGIONS (FULL DRILL-DOWNS & OPERATIONAL CRUD)
# ------------------------------------------------------------------------------
elif nav == "🏢 Venues & Regions":
    st.title("Geographical Ecosystem & Regional Drill-Downs")

    conn = get_db_connection()
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Venues", conn.execute("SELECT COUNT(*) FROM venues WHERE is_active = 1").fetchone()[0])
    c2.metric("Cities", conn.execute("SELECT COUNT(*) FROM locations WHERE location_type = 'CITY' AND is_active = 1").fetchone()[0])
    c3.metric("Countries", conn.execute("SELECT COUNT(*) FROM locations WHERE location_type = 'COUNTRY' AND is_active = 1").fetchone()[0])
    c4.metric("Total Physical Courts", conn.execute("SELECT SUM(court_count) FROM venues WHERE is_active = 1").fetchone()[0] or 0)

    ba1, ba2, ba3 = st.columns(3)
    with ba1.expander("➕ Add Venue", expanded=False):
        with st.form("add_v_form"):
            v_name = st.text_input("Venue / Club Name")
            cities = conn.execute("SELECT location_id, location_name, country_code FROM locations WHERE location_type = 'CITY'").fetchall()
            v_c = st.selectbox("Assigned City", [c["location_name"] for c in cities]) if cities else None
            v_courts = st.number_input("Court Count", 1, 50, 3)
            v_ver = st.checkbox("Verified Desk Authority (Tournament Override)", value=True)
            if st.form_submit_button("Register Venue"):
                if v_name and v_c:
                    c_row = [c for c in cities if c["location_name"] == v_c][0]
                    v_uuid = f"VEN_{datetime.now().strftime('%H%M%S')}"
                    conn.execute("""INSERT INTO venues (venue_id, venue_name, city_id, country_code, court_count, is_verified, created_at)
                                    VALUES (?, ?, ?, ?, ?, ?, ?)""",
                                 (v_uuid, v_name, c_row["location_id"], c_row["country_code"], v_courts, 1 if v_ver else 0, datetime.now(timezone.utc).isoformat()))
                    conn.commit(); st.success(f"Added {v_name}!"); st.rerun()

    with ba2.expander("➕ Add City", expanded=False):
        with st.form("add_c_form"):
            c_name = st.text_input("City Name")
            countries = conn.execute("SELECT location_id, location_name FROM locations WHERE location_type = 'COUNTRY'").fetchall()
            co_parent = st.selectbox("Parent Country", [c["location_name"] for c in countries]) if countries else None
            if st.form_submit_button("Register City"):
                if c_name and co_parent:
                    co_row = [c for c in countries if c["location_name"] == co_parent][0]
                    c_uuid = f"LOC_{c_name[:3].upper()}_{datetime.now().strftime('%S')}"
                    conn.execute("""INSERT INTO locations (location_id, location_type, location_name, parent_id, country_code, updated_at)
                                    VALUES (?, 'CITY', ?, ?, 'IND', ?)""",
                                 (c_uuid, c_name, co_row["location_id"], datetime.now(timezone.utc).isoformat()))
                    conn.commit(); st.success(f"Added {c_name}!"); st.rerun()

    with ba3.expander("➕ Add Country", expanded=False):
        with st.form("add_co_form"):
            co_name = st.text_input("Country Name (e.g. United Arab Emirates)")
            co_code = st.text_input("ISO 3-Letter Code (e.g. UAE)").upper()
            if st.form_submit_button("Register Country"):
                if co_name and co_code:
                    conn.execute("""INSERT INTO locations (location_id, location_type, location_name, country_code, updated_at)
                                    VALUES (?, 'COUNTRY', ?, ?, ?)""",
                                 (f"LOC_{co_code}", co_name, co_code, datetime.now(timezone.utc).isoformat()))
                    conn.commit(); st.success(f"Added {co_name}!"); st.rerun()

    view_mode = st.radio("Inspect Hierarchy By:", ["Countries", "Cities", "Venues"], horizontal=True)

    if view_mode == "Countries":
        co_list = conn.execute("SELECT * FROM locations WHERE location_type = 'COUNTRY' AND is_active = 1").fetchall()
        for co in co_list:
            coid = co["location_id"]
            p_in_co = conn.execute("SELECT p.latent_mmr, p.all_time_badge FROM players p JOIN locations l ON p.home_city_id = l.location_id WHERE l.parent_id = ?", (coid,)).fetchall()
            with st.expander(f"🌍 {co['location_name']} ({co['country_code']}) • Total Players: {len(p_in_co)}"):
                st.write(f"**Country Code:** `{co['country_code']}`")
                all_cats = conn.execute("SELECT category_name FROM rating_categories ORDER BY sort_order").fetchall()
                cat_data = []
                for cat in all_cats:
                    cnt = sum(1 for p in p_in_co if p["all_time_badge"] == cat["category_name"])
                    pct = (cnt / len(p_in_co) * 100.0) if p_in_co else 0.0
                    cat_data.append({"Category": cat["category_name"], "Count": cnt, "Share": f"{pct:.1f}%"})
                st.dataframe(pd.DataFrame(cat_data), use_container_width=True)

    elif view_mode == "Cities":
        ci_list = conn.execute("SELECT * FROM locations WHERE location_type = 'CITY' AND is_active = 1").fetchall()
        for ci in ci_list:
            cid = ci["location_id"]
            p_in_ci = conn.execute("SELECT latent_mmr, all_time_badge FROM players WHERE home_city_id = ?", (cid,)).fetchall()
            with st.expander(f"🏙️ {ci['location_name']} • Players: {len(p_in_ci)} | Bridges (K): {ci['active_bridge_count']}"):
                st.write(f"**Readiness Score:** `{ci['readiness_score']:.1f}%` | **Hawking Offset:** `{ci['hawking_offset']:+.4f}`")
                all_cats = conn.execute("SELECT category_name FROM rating_categories ORDER BY sort_order").fetchall()
                cat_data = []
                for cat in all_cats:
                    cnt = sum(1 for p in p_in_ci if p["all_time_badge"] == cat["category_name"])
                    pct = (cnt / len(p_in_ci) * 100.0) if p_in_ci else 0.0
                    cat_data.append({"Category": cat["category_name"], "Count": cnt, "Share": f"{pct:.1f}%"})
                st.dataframe(pd.DataFrame(cat_data), use_container_width=True)

    elif view_mode == "Venues":
        v_list = conn.execute("SELECT v.*, l.location_name as city FROM venues v JOIN locations l ON v.city_id = l.location_id WHERE v.is_active = 1").fetchall()
        for v in v_list:
            with st.expander(f"🏟️ {v['venue_name']} ({v['city']}) • Courts: {v['court_count']} | Matches: {v['total_matches_played']}"):
                st.write(f"**Verified Club Desk Override Authority:** `{'YES' if v['is_verified'] else 'NO'}`")

    conn.close()

# ------------------------------------------------------------------------------
# TAB 7: HAWKING ENGINE
# ------------------------------------------------------------------------------
elif nav == "🌐 Hawking Engine":
    st.title("Hawking Macro Normalization & Regional Diffusion")
    st.caption("Review municipal readiness, traveler bridge density (K), and deploy regularized offsets.")

    conn = get_db_connection()
    cities = conn.execute("SELECT * FROM locations WHERE location_type = 'CITY' AND is_active = 1").fetchall()

    if not cities:
        st.info("No cities registered in the topology yet. Add a City under 'Venues & Regions' to begin tracking.")
    else:
        for c in cities:
            st.markdown(f"### Municipality: **{c['location_name']}**")
            k1, k2, k3, k4 = st.columns(4)
            k1.metric("Bridge Nodes (K)", c["active_bridge_count"])
            k2.metric("Intransitivity Index", f"{c['intransitivity_idx']:.3f}")
            k3.metric("Current Offset", f"{c['hawking_offset']:+0.4f}")
            k4.metric("Suggested Shift", f"{c['suggested_offset']:+0.4f}")

            with st.expander(f"Review & Deploy Offset for {c['location_name']}"):
                shift = st.number_input(f"Approved Offset Step ({c['location_name']})", value=float(c["suggested_offset"]) if c["suggested_offset"] != 0.0 else -0.025, step=0.005, format="%.4f", key=f"h_{c['location_id']}")
                if st.button(f"Apply {shift:+0.4f} to {c['location_name']}", key=f"btn_{c['location_id']}"):
                    conn.execute("UPDATE locations SET hawking_offset = hawking_offset + ? WHERE location_id = ?", (shift, c['location_id']))
                    conn.execute("""
                        UPDATE players SET 
                            latent_mmr = latent_mmr + (? * (latent_mmr / 4.50)),
                            display_rating = display_rating + (? * (latent_mmr / 4.50))
                        WHERE home_city_id = ? AND is_provisional = 0
                    """, (shift, shift, c['location_id']))
                    conn.commit()
                    st.success(f"Deployed {shift:+0.4f} across verified residents in {c['location_name']}!")
                    st.rerun()
    conn.close()

# ------------------------------------------------------------------------------
# TAB 8: GLOBAL CONFIG
# ------------------------------------------------------------------------------
elif nav == "⚙️ Global Config":
    st.title("Parameter Matrix & Rule Controller")
    conn = get_db_connection()

    st.markdown("### 🏆 Rating Category Boundaries (Self-Correcting)")
    cats_data = conn.execute("SELECT * FROM rating_categories ORDER BY sort_order ASC").fetchall()
    with st.form("cat_ranges_form"):
        updated_ranges = []
        for cat in cats_data:
            c1, c2, c3 = st.columns([2, 2, 2])
            c1.write(f"**{cat['sort_order']}. {cat['category_name']}**")
            new_min = c2.number_input(f"Min ({cat['category_name']})", 0.000, 7.000, float(cat["min_rating"]), 0.050, format="%.3f", key=f"min_{cat['category_name']}")
            new_max = c3.number_input(f"Max ({cat['category_name']})", 0.000, 7.000, float(cat["max_rating"]), 0.050, format="%.3f", key=f"max_{cat['category_name']}")
            updated_ranges.append({"name": cat["category_name"], "min": new_min, "max": new_max})

        if st.form_submit_button("Verify & Commit Category Boundaries"):
            has_error = False
            if updated_ranges[0]["min"] != 0.000 or updated_ranges[-1]["max"] != 7.000: has_error = True
            for i in range(len(updated_ranges) - 1):
                if updated_ranges[i]["min"] >= updated_ranges[i]["max"]: has_error = True
            if not has_error:
                for ur in updated_ranges:
                    conn.execute("UPDATE rating_categories SET min_rating = ?, max_rating = ? WHERE category_name = ?", (ur["min"], ur["max"], ur["name"]))
                conn.commit(); st.success("Categories Saved!"); st.rerun()
            else:
                st.error("Boundary error! Ranges must span 0.000 to 7.000 with contiguous thresholds.")

    st.markdown("---")
    st.markdown("### Master Parameter Matrix (Bits 1 to 25)")
    df = pd.read_sql_query("SELECT * FROM global_config ORDER BY module_group, param_key", conn)
    for grp in df["module_group"].unique():
        st.markdown(f"#### 📁 {grp}")
        for _, row in df[df["module_group"] == grp].iterrows():
            with st.expander(f"⚙️ {row['param_key']} - {row['title']}"):
                st.write(row['description'])
                st.caption(row['tuning_guide'])
                v = st.number_input("Value", value=float(row["param_value"]), step=0.05, key=f"val_{row['param_key']}")
                if st.button(f"Save {row['param_key']}", key=f"btn_{row['param_key']}"):
                    conn.execute("UPDATE global_config SET param_value=? WHERE param_key=?", (v, row["param_key"]))
                    conn.commit(); st.toast("Saved")
    conn.close()
