import streamlit as st
import sqlite3
import math
import random
import json
import os
import io
import uuid
from datetime import datetime, timezone, date, time
import pandas as pd

try:
    from reportlab.lib.pagesizes import letter
    from reportlab.lib import colors
    from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
    from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle, HRFlowable
    REPORTLAB_AVAILABLE = True
except ImportError:
    REPORTLAB_AVAILABLE = False

DB_FILE = "ryft_v16_master.db"

# ==============================================================================
# 1. DATABASE SCHEMA & BACKUP MANAGEMENT
# ==============================================================================
def get_db_connection():
    conn = sqlite3.connect(DB_FILE, timeout=60.0, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode = WAL;")
    conn.execute("PRAGMA busy_timeout = 60000;")
    return conn

def add_column_if_not_exists(cursor, table, col_name, col_type):
    cursor.execute(f"PRAGMA table_info({table});")
    if col_name not in [row[1] for row in cursor.fetchall()]:
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
        source.close(); os.remove(temp_in)
        raise ValueError("Invalid RYFT database snapshot.")
    dest = get_db_connection()
    dest.execute("PRAGMA wal_checkpoint(TRUNCATE);")
    source.backup(dest)
    source.close()
    dest.execute("PRAGMA wal_checkpoint(TRUNCATE);")
    dest.commit(); dest.close()
    if os.path.exists(temp_in): os.remove(temp_in)

def init_db():
    conn = get_db_connection(); c = conn.cursor(); c.execute("PRAGMA foreign_keys = ON;")
    c.execute('''CREATE TABLE IF NOT EXISTS locations (location_id TEXT PRIMARY KEY, location_type TEXT NOT NULL, location_name TEXT NOT NULL, parent_id TEXT, country_code TEXT DEFAULT 'IND', intransitivity_idx REAL DEFAULT 0.0, intransitivity_index REAL DEFAULT 0.0, hawking_offset REAL DEFAULT 0.0, suggested_offset REAL DEFAULT 0.0, readiness_score REAL DEFAULT 0.0, active_bridge_count INTEGER DEFAULT 0, total_active_players INTEGER DEFAULT 0, total_matches_played INTEGER DEFAULT 0, active_venues_count INTEGER DEFAULT 0, median_latent_mmr REAL DEFAULT 3.000, highest_player_mmr REAL DEFAULT 3.000, lowest_player_mmr REAL DEFAULT 3.000, is_normalized INTEGER DEFAULT 0, updated_at TEXT, is_active INTEGER DEFAULT 1)''')
    c.execute('''CREATE TABLE IF NOT EXISTS venues (venue_id TEXT PRIMARY KEY, venue_name TEXT NOT NULL, raw_input_name TEXT, is_verified INTEGER DEFAULT 0, city_id TEXT NOT NULL, country_code TEXT NOT NULL, court_count INTEGER DEFAULT 1, total_matches_played INTEGER DEFAULT 0, unique_players_count INTEGER DEFAULT 0, city_bridge_matches_count INTEGER DEFAULT 0, country_bridge_matches_count INTEGER DEFAULT 0, average_player_mmr REAL DEFAULT 3.000, is_active INTEGER DEFAULT 1, created_at TEXT)''')
    c.execute('''CREATE TABLE IF NOT EXISTS rating_categories (category_name TEXT PRIMARY KEY, min_rating REAL NOT NULL, max_rating REAL NOT NULL, sort_order INTEGER NOT NULL, speed_multiplier REAL DEFAULT 1.00)''')
    c.execute('''CREATE TABLE IF NOT EXISTS match_formats (format_id TEXT PRIMARY KEY, format_name TEXT NOT NULL, category TEXT NOT NULL, mc_weight REAL NOT NULL, target_games INTEGER, total_points INTEGER, is_session_bound INTEGER DEFAULT 0, is_active INTEGER DEFAULT 1)''')
    c.execute('''CREATE TABLE IF NOT EXISTS players (player_id TEXT PRIMARY KEY, display_name TEXT NOT NULL, initial_rating REAL NOT NULL, home_venue_id TEXT, home_city_id TEXT NOT NULL, home_country_code TEXT NOT NULL DEFAULT 'IND', latent_mmr REAL NOT NULL, display_rating REAL NOT NULL, rolling_90d_peak REAL NOT NULL DEFAULT 3.000, rolling_180d_peak REAL NOT NULL DEFAULT 3.000, rolling_365d_peak REAL NOT NULL DEFAULT 3.000, tournament_floor REAL NOT NULL DEFAULT 0.000, tournament_floor_rating REAL NOT NULL DEFAULT 0.000, all_time_badge TEXT DEFAULT 'Intermediate', consecutive_losses INTEGER DEFAULT 0, rating_deviation REAL NOT NULL DEFAULT 350.000, rating_accuracy_pct REAL DEFAULT 0.0, accuracy_s_rd REAL DEFAULT 0.0, accuracy_s_matches REAL DEFAULT 0.0, accuracy_s_diversity REAL DEFAULT 0.0, calibration_tier TEXT DEFAULT 'PROVISIONAL', is_provisional INTEGER DEFAULT 1, is_manually_verified INTEGER DEFAULT 0, verified_matches_count INTEGER DEFAULT 0, unique_opponents_count INTEGER DEFAULT 0, unique_partners_count INTEGER DEFAULT 0, unique_venues_count INTEGER DEFAULT 0, unique_cities_count INTEGER DEFAULT 0, unique_countries_count INTEGER DEFAULT 0, bridge_matches_count INTEGER DEFAULT 0, is_active_bridge INTEGER DEFAULT 0, is_country_bridge INTEGER DEFAULT 0, graph_centrality REAL DEFAULT 0.20, is_quarantined INTEGER DEFAULT 0, is_anchor INTEGER DEFAULT 0, is_ceiling_anchor INTEGER DEFAULT 0, is_dummy INTEGER DEFAULT 0, last_match_time TEXT, created_at TEXT)''')
    c.execute('''CREATE TABLE IF NOT EXISTS sessions (session_id TEXT PRIMARY KEY, venue_id TEXT NOT NULL, session_title TEXT NOT NULL, session_date TEXT NOT NULL DEFAULT '', start_time TEXT NOT NULL DEFAULT '09:00', end_time TEXT NOT NULL DEFAULT '11:00', match_mode TEXT NOT NULL DEFAULT 'DOUBLES', team_format TEXT NOT NULL, format_id TEXT NOT NULL, tourney_structure TEXT NOT NULL DEFAULT 'ROUND_ROBIN', is_tournament INTEGER DEFAULT 0, court_ids_json TEXT NOT NULL, enrolled_player_ids TEXT NOT NULL, teams_json TEXT DEFAULT '[]', checked_in_player_ids TEXT DEFAULT '[]', player_count INTEGER NOT NULL, active_checked_in_count INTEGER DEFAULT 0, total_rounds INTEGER NOT NULL DEFAULT 1, current_round INTEGER DEFAULT 0, session_status TEXT DEFAULT 'CONFIG', created_at TEXT NOT NULL, completed_at TEXT, current_stage TEXT, lineup_mode TEXT)''')
    c.execute('''CREATE TABLE IF NOT EXISTS session_matches (session_match_id TEXT PRIMARY KEY, session_id TEXT NOT NULL, round_number INTEGER NOT NULL, court_id TEXT NOT NULL, match_order INTEGER NOT NULL, team_a_p1_id TEXT NOT NULL, team_a_p2_id TEXT, team_b_p1_id TEXT NOT NULL, team_b_p2_id TEXT, team_a_name TEXT DEFAULT '', team_b_name TEXT DEFAULT '', score_team_a INTEGER DEFAULT 0, score_team_b INTEGER DEFAULT 0, games_winner INTEGER DEFAULT 0, games_loser INTEGER DEFAULT 0, set_scores_json TEXT DEFAULT '[]', match_status TEXT DEFAULT 'SCHEDULED', started_at TEXT, completed_at TEXT, committed_match_id TEXT, stage TEXT, group_id TEXT, flight_number INTEGER DEFAULT 1)''')
    c.execute('''CREATE TABLE IF NOT EXISTS matches (match_id TEXT PRIMARY KEY, venue_id TEXT NOT NULL, format_id TEXT NOT NULL, session_id TEXT, is_singles INTEGER DEFAULT 0, is_tournament INTEGER DEFAULT 0, is_venue_bridge INTEGER DEFAULT 0, is_city_bridge INTEGER DEFAULT 0, is_country_bridge INTEGER DEFAULT 0, team_a_p1_id TEXT NOT NULL, team_a_p2_id TEXT, team_b_p1_id TEXT NOT NULL, team_b_p2_id TEXT, score_team_a INTEGER DEFAULT 0, score_team_b INTEGER DEFAULT 0, set_scores_json TEXT DEFAULT '[]', games_winner INTEGER DEFAULT 0, games_loser INTEGER DEFAULT 0, pre_rating_a REAL DEFAULT 3.000, pre_rating_b REAL DEFAULT 3.000, win_expectancy_a REAL DEFAULT 0.5000, applied_m_c REAL DEFAULT 1.00, applied_s_margin REAL DEFAULT 1.000, delta_r_p1 REAL DEFAULT 0.0, delta_r_p2 REAL DEFAULT 0.0, delta_r_p3 REAL DEFAULT 0.0, delta_r_p4 REAL DEFAULT 0.0, guardrails_summary TEXT DEFAULT '[]', is_retroactive INTEGER DEFAULT 0, match_timestamp TEXT NOT NULL, processed_at TEXT)''')
    c.execute('''CREATE TABLE IF NOT EXISTS match_logs (log_id TEXT PRIMARY KEY, match_id TEXT NOT NULL, player_id TEXT NOT NULL, pre_latent_mmr REAL NOT NULL, post_latent_mmr REAL NOT NULL, pre_display_rating REAL NOT NULL, post_display_rating REAL NOT NULL, pre_rd REAL NOT NULL, post_rd REAL NOT NULL, pre_accuracy_pct REAL NOT NULL, post_accuracy_pct REAL NOT NULL, delta_r REAL NOT NULL, is_elevator_active INTEGER DEFAULT 0, guardrails_triggered TEXT DEFAULT '[]', is_retroactive INTEGER DEFAULT 0, logged_at TEXT NOT NULL)''')
    c.execute('''CREATE TABLE IF NOT EXISTS global_config (param_key TEXT PRIMARY KEY, param_value REAL NOT NULL, is_active INTEGER DEFAULT 1, title TEXT, description TEXT, tuning_guide TEXT, module_group TEXT)''')
    c.execute('''CREATE TABLE IF NOT EXISTS tournaments (tourney_id TEXT PRIMARY KEY, name TEXT NOT NULL, venue_id TEXT NOT NULL, tourney_date TEXT NOT NULL, floor_category TEXT NOT NULL, status TEXT DEFAULT 'PENDING', created_at TEXT NOT NULL)''')
    c.execute('''CREATE TABLE IF NOT EXISTS tourney_matches (t_match_id TEXT PRIMARY KEY, tourney_id TEXT NOT NULL, match_time TEXT NOT NULL, format_id TEXT NOT NULL, is_singles INTEGER DEFAULT 0, team_a_p1_id TEXT NOT NULL, team_a_p2_id TEXT, team_b_p1_id TEXT NOT NULL, team_b_p2_id TEXT, score_team_a INTEGER DEFAULT 0, score_team_b INTEGER DEFAULT 0, games_winner INTEGER DEFAULT 0, games_loser INTEGER DEFAULT 0, set_scores_json TEXT DEFAULT '[]', status TEXT DEFAULT 'STAGED')''')
    c.execute('''CREATE TABLE IF NOT EXISTS session_rosters (roster_id TEXT PRIMARY KEY, session_id TEXT NOT NULL, entity_type TEXT, player_id_p1 TEXT, player_id_p2 TEXT, display_label TEXT, group_id TEXT DEFAULT 'A', seed_index INTEGER DEFAULT 0, initial_mmr REAL DEFAULT 3.0, initial_rd REAL DEFAULT 350.0, is_checked_in INTEGER DEFAULT 0, is_defected INTEGER DEFAULT 0, matches_played INTEGER DEFAULT 0, matches_won INTEGER DEFAULT 0, matches_lost INTEGER DEFAULT 0, matches_tied INTEGER DEFAULT 0, standing_points INTEGER DEFAULT 0, games_for INTEGER DEFAULT 0, games_against INTEGER DEFAULT 0, net_game_diff INTEGER DEFAULT 0, points_for INTEGER DEFAULT 0, points_against INTEGER DEFAULT 0, net_point_diff INTEGER DEFAULT 0, consecutive_sit INTEGER DEFAULT 0, is_qualified INTEGER DEFAULT 0, knockout_seed INTEGER)''')
    c.execute('''CREATE TABLE IF NOT EXISTS synthetic_ghosts (
        ghost_id TEXT PRIMARY KEY,
        ghost_name TEXT NOT NULL,
        playstyle TEXT NOT NULL CHECK (playstyle IN ('AGGRESSIVE', 'BALANCED', 'CONSERVATIVE')),
        assigned_mmr REAL NOT NULL,
        target_city TEXT NOT NULL,
        is_active INTEGER NOT NULL DEFAULT 1,
        sims_run_count INTEGER NOT NULL DEFAULT 0,
        created_at TEXT DEFAULT CURRENT_TIMESTAMP
    )''')

    add_column_if_not_exists(c, "players", "consecutive_losses", "INTEGER DEFAULT 0")
    add_column_if_not_exists(c, "players", "tournament_floor_rating", "REAL DEFAULT 0.0")
    add_column_if_not_exists(c, "locations", "intransitivity_index", "REAL DEFAULT 0.0")

    # Seed Default Synthetic Ghosts if empty
    g_count = c.execute("SELECT COUNT(*) FROM synthetic_ghosts").fetchone()[0]
    if g_count == 0:
        default_ghosts = [
            ("GHOST_NAT_AGG_50", "Ghost Aggressive 5.0", "AGGRESSIVE", 5.0000, "NATIONAL", 1, 0),
            ("GHOST_NAT_BAL_50", "Ghost Balanced 5.0", "BALANCED", 5.0000, "NATIONAL", 1, 0),
            ("GHOST_NAT_CON_50", "Ghost Conservative 5.0", "CONSERVATIVE", 5.0000, "NATIONAL", 1, 0)
        ]
        c.executemany("INSERT INTO synthetic_ghosts (ghost_id, ghost_name, playstyle, assigned_mmr, target_city, is_active, sims_run_count) VALUES (?, ?, ?, ?, ?, ?, ?)", default_ghosts)

    if c.execute("SELECT COUNT(*) FROM rating_categories").fetchone()[0] == 0:
        cats = [("Beginner", 0.0, 0.999, 1, 1.0), ("Beginner+", 1.0, 1.999, 2, 1.0), ("Intermediate", 2.0, 3.499, 3, 1.0), ("Intermediate+", 3.5, 4.499, 4, 1.0), ("Advanced", 4.5, 5.499, 5, 1.0), ("Pro", 5.5, 6.299, 6, 1.0), ("Elite", 6.3, 7.0, 7, 1.0)]
        for cn, cmn, cmx, so, sm in cats: c.execute("INSERT OR IGNORE INTO rating_categories VALUES (?,?,?,?,?)", (cn, cmn, cmx, so, sm))

    # All 20 Official Match Formats Active for Standalone & Session Workflows
    official_formats = [
        ("STD_B03", "Best of 3 Sets", "MULTI_SET", 1.00, None, None, 0, 1),
        ("STD_B05", "Best of 5 Sets", "MULTI_SET", 1.00, None, None, 0, 1),
        ("RACE_4", "Race to 4 Games", "RACE_GAMES", 0.50, 4, None, 0, 1),
        ("RACE_5", "Race to 5 Games", "RACE_GAMES", 0.60, 5, None, 0, 1),
        ("RACE_6", "Race to 6 Games", "RACE_GAMES", 0.70, 6, None, 0, 1),
        ("RACE_7", "Race to 7 Games", "RACE_GAMES", 0.80, 7, None, 0, 1),
        ("RACE_9", "Race to 9 Games", "RACE_GAMES", 0.80, 9, None, 0, 1),
        ("RACE_11", "Race to 11 Games", "RACE_GAMES", 0.90, 11, None, 0, 1),
        ("AMER_12", "Americano 12 Points", "AMERICANO", 0.30, None, 12, 0, 1),
        ("MEX_12", "Mexicano 12 Points", "MEXICANO", 0.30, None, 12, 0, 1),
        ("AMER_16", "Americano 16 Points", "AMERICANO", 0.30, None, 16, 0, 1),
        ("MEX_16", "Mexicano 16 Points", "MEXICANO", 0.30, None, 16, 0, 1),
        ("AMER_20", "Americano 20 Points", "AMERICANO", 0.30, None, 20, 0, 1),
        ("MEX_20", "Mexicano 20 Points", "MEXICANO", 0.30, None, 20, 0, 1),
        ("AMER_24", "Americano 24 Points", "AMERICANO", 0.30, None, 24, 0, 1),
        ("MEX_24", "Mexicano 24 Points", "MEXICANO", 0.30, None, 24, 0, 1),
        ("AMER_28", "Americano 28 Points", "AMERICANO", 0.30, None, 28, 0, 1),
        ("MEX_28", "Mexicano 28 Points", "MEXICANO", 0.30, None, 28, 0, 1),
        ("AMER_32", "Americano 32 Points", "AMERICANO", 0.35, None, 32, 0, 1),
        ("MEX_32", "Mexicano 32 Points", "MEXICANO", 0.35, None, 32, 0, 1)
    ]
    for fid, fname, cat, mc, tg, tp, is_sb, is_a in official_formats:
        c.execute("""INSERT INTO match_formats (format_id, format_name, category, mc_weight, target_games, total_points, is_session_bound, is_active)
                     VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                     ON CONFLICT(format_id) DO UPDATE SET 
                         format_name=excluded.format_name, category=excluded.category, target_games=excluded.target_games,
                         total_points=excluded.total_points, is_session_bound=excluded.is_session_bound, is_active=excluded.is_active""",
                  (fid, fname, cat, mc, tg, tp, is_sb, is_a))

    seed_factory_parameters(c, overwrite_existing=False)
    conn.commit()
    conn.close()

def seed_factory_parameters(cursor, overwrite_existing=False):
    master_params = [
        ("R_MIN", 0.000, 1, "Scale Absolute Floor", "Lowest possible rating.", "Clamps rating drops at 0.000.", "1. Core Bounds & Drag"),
        ("R_MAX", 7.000, 1, "Scale Absolute Ceiling", "Maximum rating ceiling.", "LOCKED at 7.000.", "1. Core Bounds & Drag"),
        ("R_ELITE_THRESHOLD", 6.300, 1, "Elite Drag Gate", "Rating where drag starts.", "Lowering applies drag earlier.", "1. Core Bounds & Drag"),
        ("ELITE_DRAG_EXPONENT", 2.5, 1, "Elite Drag Curvature", "Steepness of ceiling resistance.", "Higher values block 7.000.", "1. Core Bounds & Drag"),
        ("POWER_MEAN_P", 3.0, 1, "Doubles Cubic Exponent", "Power mean anchor exponent.", "3.0 gives 70/30 anchor bias.", "2. Volatility & Odds"),
        ("LOGISTIC_BETA", 2.0, 1, "Logistic Scale Factor", "Odds curve steepness.", "Lowering boosts upset deltas.", "2. Volatility & Odds"),
        ("K_MAX", 0.400, 1, "Beginner Max Volatility", "Step size at R=0.000.", "Higher values accelerate progression.", "2. Volatility & Odds"),
        ("K_MIN", 0.080, 1, "Pro Min Volatility", "Step size at R=7.000.", "Lower values lock pro ratings.", "2. Volatility & Odds"),
        ("MARGIN_BASE", 0.80, 1, "Margin Floor Factor", "Min score factor for close matches.", "Points floor for tight finishes.", "3. Margins & Rightsizing"),
        ("MARGIN_SCALE", 0.40, 1, "Margin Blowout Scale", "Max bonus factor for blowouts.", "Full blowout bonus = Base + Scale.", "3. Margins & Rightsizing"),
        ("MAX_PROVISIONAL_DELTA", 0.750, 1, "Placement Ceiling", "Max points won in interpolation.", "Single-match placement cap.", "3. Margins & Rightsizing"),
        ("PROVISIONAL_ABSORPTION_ALPHA", 0.45, 1, "Rightsizing Velocity", "Speed toward performance rating.", "Higher = faster rightsizing.", "3. Margins & Rightsizing"),
        ("PROVISIONAL_BYPASS_EXCHANGE_CAP", 1, 1, "Provisional Cap Bypass", "Allows rightsizing blowouts to reach placement ceiling.", "Default 1 (Active).", "3. Margins & Rightsizing"),
        ("DISPLAY_RATING_SOFT_FLOOR", 0.050, 1, "Display Soft Floor", "Buffer preventing minor drops.", "Default 0.050.", "3. Margins & Rightsizing"),
        ("ICE_OUT_GAP_TIER_1", 1.50, 1, "Ice-Out Gap Threshold 1", "Min partner gap to trigger 20% dampening.", "Applies to Anchor.", "4. Partner Guardrails"),
        ("ICE_OUT_MULT_TIER_1", 0.20, 1, "Ice-Out Dampener 1", "Multiplier applied if Tier 1 Gap breached.", "0.20 = 80% loss reduction.", "4. Partner Guardrails"),
        ("ICE_OUT_GAP_TIER_2", 2.00, 1, "Ice-Out Gap Threshold 2", "Min partner gap to trigger 5% dampening.", "Extreme freeze-outs.", "4. Partner Guardrails"),
        ("ICE_OUT_MULT_TIER_2", 0.05, 1, "Ice-Out Dampener 2", "Multiplier applied if Tier 2 Gap breached.", "0.05 = 95% loss reduction.", "4. Partner Guardrails"),
        ("ANTI_CARRY_GAP_TIER_1", 1.75, 1, "Anti-Carry Gap Threshold 1", "Min gap to trigger 50% carry dampening.", "Applies to weaker partner.", "4. Partner Guardrails"),
        ("ANTI_CARRY_MULT_TIER_1", 0.50, 1, "Anti-Carry Dampener 1", "Multiplier applied if Tier 1 carry breached.", "0.50 = 50% gain reduction.", "4. Partner Guardrails"),
        ("ANTI_CARRY_GAP_TIER_2", 2.50, 1, "Anti-Carry Gap Threshold 2", "Min gap to trigger 25% carry dampening.", "Extreme tow jobs.", "4. Partner Guardrails"),
        ("ANTI_CARRY_MULT_TIER_2", 0.25, 1, "Anti-Carry Dampener 2", "Multiplier applied if Tier 2 carry breached.", "0.25 = 75% gain reduction.", "4. Partner Guardrails"),
        ("MAX_24H_PAIRWISE_EXCHANGE_CAP", 0.150, 1, "24H Pairwise Anti-Collusion Cap", "Max net transfer between specific opponent cluster in 24h.", "Targeted anti-collusion.", "5. Exchange Caps & Security"),
        ("MAX_24H_GLOBAL_CASUAL_CAP", 0.250, 1, "24H Global Daily Casual Governor", "Max cumulative net casual points across all opponents in 24h.", "Macro daily movement governor.", "5. Exchange Caps & Security"),
        ("MAX_24H_EXCHANGE_CAP", 0.150, 1, "24H Casual Cap (Legacy)", "Fallback single-window cap ceiling.", "Legacy parameter.", "5. Exchange Caps & Security"),
        ("PROVISIONAL_CAP_MULTIPLIER", 2.5, 1, "Provisional Cap Relaxer", "Multiplier on 24H cap for PRs.", "Allows accelerated placement.", "5. Exchange Caps & Security"),
        ("SESSION_EXCHANGE_CAP", 0.300, 1, "Verified Session Cap", "Cap for verified club events.", "Doubles point limits for mixers.", "5. Exchange Caps & Security"),
        ("MIN_SESSION_PLAYERS", 6, 1, "Session Participant Floor", "Min players required to unlock session cap.", "Events with fewer revert to casual caps.", "5. Exchange Caps & Security"),
        ("TOURNAMENT_MULTIPLIER_ACTIVE", 1, 1, "Tournament Multiplier Toggle", "Activates stakes multiplier for tournament.", "1 = Active.", "5. Exchange Caps & Security"),
        ("TOURNAMENT_STAKES_MULTIPLIER", 1.15, 1, "Tournament Stakes Multiplier", "Rating delta multiplier for tournaments.", "Default 1.15 (+15%).", "5. Exchange Caps & Security"),
        ("RD_MIN", 30.0, 1, "Certainty Floor", "Absolute uncertainty floor.", "Prevents RD dropping below 30.0.", "6. Uncertainty & Rust"),
        ("RD_MAX", 350.0, 1, "Unrated Starting RD", "Uncertainty assigned at registration.", "Starting uncertainty.", "6. Uncertainty & Rust"),
        ("RD_INFO_VARIANCE", 65.0, 1, "Contraction Speed", "Denominator in RD shrinkage.", "Lower values shrink RD faster.", "6. Uncertainty & Rust"),
        ("INACTIVITY_CONSTANT", 12.0, 1, "Inactivity Rust Rate (Monthly)", "Monthly uncertainty growth for background cron cycles.", "Points of RD regained per month.", "6. Uncertainty & Rust"),
        ("TEMPORAL_DRIFT_CONSTANT", 5.50, 1, "Inactivity Drift Constant (c)", "Daily uncertainty expansion constant: sqrt(RD^2 + c^2 * delta_days).", "Default 5.50 (Tuning: 1.50 mild, 5.50 standard, 7.50 re-qualification).", "6. Uncertainty & Rust"),
        ("INACTIVITY_GRACE_DAYS", 7.0, 1, "Inactivity Grace Window (Days)", "Days of inactivity permitted before temporal rust begins accumulating.", "Default 7.0 days.", "6. Uncertainty & Rust"),
        ("INACTIVITY_REPROVISION_THRESHOLD", 100.0, 1, "Inactivity Reprovisioning RD Ceiling", "RD threshold where inactive verified players are flagged for reprovisioning [PR].", "Default 100.0.", "6. Uncertainty & Rust"),
        ("COHORT_FACTOR_0_PROV", 1.00, 1, "Omega 0 Factor", "Contraction against verified anchors.", "100% gain.", "6. Uncertainty & Rust"),
        ("COHORT_FACTOR_1_PROV", 0.75, 1, "Omega 1 Factor", "Contraction with 1 unrated player.", "75% gain.", "6. Uncertainty & Rust"),
        ("COHORT_FACTOR_2_PROV", 0.50, 1, "Omega 2 Factor", "Contraction with 2 unrated players.", "50% gain.", "6. Uncertainty & Rust"),
        ("COHORT_FACTOR_3_PROV", 0.25, 1, "Omega 3 Factor", "Contraction with 3+ unrated players.", "25% sandbox.", "6. Uncertainty & Rust"),
        ("PROVISIONAL_RD_CONTRACTION_RATIO", 0.35, 1, "Provisional RD Shrink Modifier", "Slows RD drop for unrated players.", "Keeps players provisional longer.", "7. Accuracy & Tri-Gates"),
        ("PROVISIONAL_ACCURACY_DAMPENER", 0.40, 1, "Provisional Accuracy Gain Cap", "Restricts accuracy gain during placement.", "Caps visual accuracy.", "7. Accuracy & Tri-Gates"),
        ("ACCURACY_WEIGHT_RD", 0.50, 1, "Accuracy Weight: RD", "Weight for Pillar 1 (Certainty).", "Controls influence of RD.", "7. Accuracy & Tri-Gates"),
        ("ACCURACY_WEIGHT_MATCHES", 0.25, 1, "Accuracy Weight: Matches", "Weight for Pillar 2 (Match Depth).", "Controls importance of volume.", "7. Accuracy & Tri-Gates"),
        ("ACCURACY_WEIGHT_DIVERSITY", 0.25, 1, "Accuracy Weight: Diversity", "Weight for Pillar 3 (Network).", "Controls importance of unique opponents.", "7. Accuracy & Tri-Gates"),
        ("TIER_PROVISIONAL_MAX", 69.99, 1, "Provisional Score Ceiling", "Upper score bound for Tier 1.", "Players below remain [PR].", "7. Accuracy & Tri-Gates"),
        ("TARGET_MATCHES_PROVISIONAL", 3, 1, "Target Matches: Provisional", "Match quota during onboarding.", "Satisfies depth.", "7. Accuracy & Tri-Gates"),
        ("TARGET_OPPONENTS_PROVISIONAL", 2, 1, "Target Opponents: Provisional", "Opponent quota during onboarding.", "Satisfies diversity.", "7. Accuracy & Tri-Gates"),
        ("TARGET_MATCHES_ANCHOR", 15, 1, "Target Matches: Anchor", "Match quota for Anchor tier.", "Required to reach Anchor.", "7. Accuracy & Tri-Gates"),
        ("PROVISIONAL_RD_GATE", 100.0, 1, "Tri-Gate Max RD", "RD must be <= 100 to exit [PR].", "Uncertainty ceiling to graduate.", "7. Accuracy & Tri-Gates"),
        ("PROVISIONAL_MIN_MATCHES", 5, 1, "Tri-Gate Min Matches", "Verified matches to exit [PR].", "Volume required to shed badge.", "7. Accuracy & Tri-Gates"),
        ("PROVISIONAL_MIN_OPPONENTS", 3, 1, "Tri-Gate Min Opponents", "Unique opponents to exit [PR].", "Distinct opponents required.", "7. Accuracy & Tri-Gates"),
        ("ISLAND_ACCURACY_CAP", 80.0, 1, "Island Geographic Cap", "Max accuracy if City has 0 bridges.", "Caps accuracy.", "7. Accuracy & Tri-Gates"),
        ("BRIDGE_RD_THRESHOLD", 80.0, 1, "Bridge Max RD", "Max RD to qualify as Bridge.", "Count if RD <= 80.", "8. Hawking Macro"),
        ("BRIDGE_MIN_MATCHES", 5, 1, "Bridge Min Matches", "Away matches required to link cities.", "Matches required before linking.", "8. Hawking Macro"),
        ("CIRCUIT_BREAKER", 0.0250, 1, "Auto Cron Safety Ceiling", "Max shift per weekly cycle.", "Limits automated macro shifts.", "8. Hawking Macro"),
        # Format Weights Configuration
        ("MC_STD_B03", 1.000, 1, "Format Multiplier: Best of 3 Sets", "Confidence multiplier for Best of 3 Sets.", "Full standard match.", "9. Format Multipliers (M_C)"),
        ("MC_STD_B05", 1.000, 1, "Format Multiplier: Best of 5 Sets", "Confidence multiplier for Best of 5 Sets.", "Full standard match.", "9. Format Multipliers (M_C)"),
        ("MC_RACE_4", 0.500, 1, "Format Multiplier: Race to 4 Games", "Confidence multiplier for Race to 4 Games.", "Short sprint set.", "9. Format Multipliers (M_C)"),
        ("MC_RACE_5", 0.600, 1, "Format Multiplier: Race to 5 Games", "Confidence multiplier for Race to 5 Games.", "Sprint set.", "9. Format Multipliers (M_C)"),
        ("MC_RACE_6", 0.700, 1, "Format Multiplier: Race to 6 Games", "Confidence multiplier for Race to 6 Games.", "Standard single set.", "9. Format Multipliers (M_C)"),
        ("MC_RACE_7", 0.800, 1, "Format Multiplier: Race to 7 Games", "Confidence multiplier for Race to 7 Games.", "Extended single set.", "9. Format Multipliers (M_C)"),
        ("MC_RACE_9", 0.800, 1, "Format Multiplier: Race to 9 Games", "Confidence multiplier for Race to 9 Games.", "Pro set.", "9. Format Multipliers (M_C)"),
        ("MC_RACE_11", 0.900, 1, "Format Multiplier: Race to 11 Games", "Confidence multiplier for Race to 11 Games.", "Extended pro set.", "9. Format Multipliers (M_C)"),
        ("MC_AMER_12", 0.300, 1, "Format Multiplier: Americano 12", "Confidence multiplier for Americano 12.", "Social mixer weight.", "9. Format Multipliers (M_C)"),
        ("MC_MEX_12", 0.300, 1, "Format Multiplier: Mexicano 12", "Confidence multiplier for Mexicano 12.", "Social mixer weight.", "9. Format Multipliers (M_C)"),
        ("MC_AMER_16", 0.300, 1, "Format Multiplier: Americano 16", "Confidence multiplier for Americano 16.", "Social mixer weight.", "9. Format Multipliers (M_C)"),
        ("MC_MEX_16", 0.300, 1, "Format Multiplier: Mexicano 16", "Confidence multiplier for Mexicano 16.", "Social mixer weight.", "9. Format Multipliers (M_C)"),
        ("MC_AMER_20", 0.300, 1, "Format Multiplier: Americano 20", "Confidence multiplier for Americano 20.", "Social mixer weight.", "9. Format Multipliers (M_C)"),
        ("MC_MEX_20", 0.300, 1, "Format Multiplier: Mexicano 20", "Confidence multiplier for Mexicano 20.", "Social mixer weight.", "9. Format Multipliers (M_C)"),
        ("MC_AMER_24", 0.300, 1, "Format Multiplier: Americano 24", "Confidence multiplier for Americano 24.", "Social mixer weight.", "9. Format Multipliers (M_C)"),
        ("MC_MEX_24", 0.300, 1, "Format Multiplier: Mexicano 24", "Confidence multiplier for Mexicano 24.", "Social mixer weight.", "9. Format Multipliers (M_C)"),
        ("MC_AMER_28", 0.300, 1, "Format Multiplier: Americano 28", "Confidence multiplier for Americano 28.", "Social mixer weight.", "9. Format Multipliers (M_C)"),
        ("MC_MEX_28", 0.300, 1, "Format Multiplier: Mexicano 28", "Confidence multiplier for Mexicano 28.", "Social mixer weight.", "9. Format Multipliers (M_C)"),
        ("MC_AMER_32", 0.350, 1, "Format Multiplier: Americano 32", "Confidence multiplier for Americano 32.", "Social mixer weight.", "9. Format Multipliers (M_C)"),
        ("MC_MEX_32", 0.350, 1, "Format Multiplier: Mexicano 32", "Confidence multiplier for Mexicano 32.", "Social mixer weight.", "9. Format Multipliers (M_C)")
    ]
    for k, v, act, tit, desc, tune, grp in master_params:
        cursor.execute("INSERT INTO global_config VALUES (?, ?, ?, ?, ?, ?, ?) ON CONFLICT(param_key) DO UPDATE SET title=excluded.title, description=excluded.description, tuning_guide=excluded.tuning_guide, module_group=excluded.module_group", (k, v, act, tit, desc, tune, grp))

init_db()

# ==============================================================================
# HAWKING MATHEMATICAL & STATISTICAL HELPERS (BITS 21 & 22)
# ==============================================================================
def compute_decay_multiplier(rating: float) -> float:
    """Evaluates continuous high-tier decay and elite drag (Bit 8)."""
    if rating >= 7.0000:
        return 0.0000001
    base_decay = (7.0000 - rating) / 7.0000
    if rating >= 6.3000:
        drag = ((7.0000 - rating) / (7.0000 - 6.3000)) ** 2.5
        return max(0.0000001, base_decay * drag)
    return max(0.0010000, base_decay)

def evaluate_municipal_readiness(player_count: int, match_count: int, k_bridges: int) -> dict:
    """Computes readiness score (0-100%) and categorizes the risk status (Bit 21)."""
    score = 0.0
    score += min(35.0, (player_count / 20.0) * 35.0)
    score += min(35.0, (match_count / 50.0) * 35.0)
    w_conf = (k_bridges / (k_bridges + 3.0)) if (k_bridges + 3.0) > 0 else 0.0
    score += w_conf * 30.0

    if score >= 80.0:
        status = "GREEN (Calibrated)"
    elif score >= 50.0:
        status = "YELLOW (Maturing Pool)"
    else:
        status = "RED (Isolated Island)"

    return {
        "readiness_pct": round(score, 1),
        "status": status,
        "w_conf": round(w_conf, 4),
    }

def calculate_intransitivity(city_id: str, conn) -> float:
    """Calculates the proportion of severe upsets (EA >= 0.70 lost) in the municipality."""
    query = """
        SELECT win_expectancy_a, score_team_a, score_team_b 
        FROM matches m
        JOIN venues v ON m.venue_id = v.venue_id
        WHERE v.city_id = ?
    """
    rows = conn.execute(query, (city_id,)).fetchall()
    if not rows:
        return 0.0000
    qualifying = 0
    upsets = 0
    for r in rows:
        ea = float(r["win_expectancy_a"] or 0.5)
        sa = int(r["score_team_a"] or 0)
        sb = int(r["score_team_b"] or 0)
        if ea >= 0.70:
            qualifying += 1
            if sa < sb:
                upsets += 1
        elif ea <= 0.30:
            qualifying += 1
            if sa > sb:
                upsets += 1
    return round(upsets / qualifying, 4) if qualifying > 0 else 0.0000

def run_ghost_monte_carlo(local_ratings: list[float], ghost_profiles: list[dict], n_sims: int = 10000) -> dict:
    """Simulates local verified players against AI Ghosts using stochastic Bernoulli trials."""
    if not local_ratings or not ghost_profiles:
        return {
            "ensemble_win_rate": 0.5000,
            "proposed_offset": 0.0000,
            "breakdown": {},
            "total_sims": 0
        }

    sims_per_ghost = max(1, n_sims // len(ghost_profiles))
    breakdown = {}
    total_wins = 0
    total_matches = 0

    for g in ghost_profiles:
        g_rating = float(g["assigned_mmr"])
        style = g.get("playstyle", "BALANCED")
        mod = 0.05 if style == "AGGRESSIVE" else (-0.05 if style == "CONSERVATIVE" else 0.0)
        effective_ghost_r = g_rating + mod
        g_wins = 0

        for _ in range(sims_per_ghost):
            r_local = random.choice(local_ratings)
            ea_local = 1.0 / (1.0 + 10.0 ** ((effective_ghost_r - r_local) / 2.0))
            if random.random() < ea_local:
                g_wins += 1
            total_matches += 1

        win_rate = g_wins / float(sims_per_ghost)
        breakdown[g["ghost_name"]] = round(win_rate, 4)
        total_wins += g_wins

    ensemble_wr = total_wins / float(total_matches) if total_matches > 0 else 0.5000
    raw_offset = (ensemble_wr - 0.5000) * 0.3000
    clamped_offset = max(-0.0750, min(0.0750, raw_offset))

    return {
        "ensemble_win_rate": round(ensemble_wr, 4),
        "proposed_offset": round(clamped_offset, 4),
        "breakdown": breakdown,
        "total_sims": total_matches,
    }

def calculate_hawking_player_delta(r_curr: float, rd_curr: float, target_offset: float) -> float:
    """Evaluates the multi-tier displacement rules (Bit 22)."""
    # Rule 1: Provisional Firewall
    if rd_curr > 100.0:
        return 0.0000

    # Rule 2: Affine Scaling for Intermediate / Novice bands
    if r_curr <= 4.5000:
        scale = max(0.0, r_curr / 4.5000)
        return round(target_offset * scale, 4)

    # Rule 3: Jacobian Elasticity Scaling for Elite Pros
    decay_45 = compute_decay_multiplier(4.5000)
    decay_curr = compute_decay_multiplier(r_curr)
    elasticity = decay_curr / decay_45 if decay_45 > 0 else 1.0
    return round(target_offset * elasticity, 4)

# ==============================================================================
# 🌐 TAB: HAWKING MACRO ENGINE & REGIONAL NORMALIZATION
# ==============================================================================
elif nav == "🌐 Hawking Engine":
    st.title("🌐 Hawking Macro Normalization & Regional Diffusion")
    st.caption("Macro calibration control room: analyze municipal topology, simulate synthetic ghost benchmarks, and deploy staged offsets.")

    conn = get_db_connection()

    h_tab1, h_tab2, h_tab3, h_tab4 = st.tabs([
        "🏙️ Regional Topology & Offsets",
        "👻 Synthetic Ghost Sandbox",
        "🕸️ Graph Centrality & Risks",
        "⚙️ Hawking Governance"
    ])

    # --------------------------------------------------------------------------
    # SUB-TAB 1: REGIONAL TOPOLOGY & OFFSETS
    # --------------------------------------------------------------------------
    with h_tab1:
        st.subheader("Municipal Macro Registry")
        cities = conn.execute("""
            SELECT l.location_id, l.location_name, p.location_name as parent_country,
                   l.hawking_offset, l.intransitivity_index, l.readiness_score,
                   COUNT(DISTINCT pl.player_id) as total_players,
                   COUNT(DISTINCT m.match_id) as total_matches,
                   SUM(CASE WHEN m.is_city_bridge = 1 THEN 1 ELSE 0 END) as city_bridge_matches
            FROM locations l
            LEFT JOIN locations p ON l.parent_id = p.location_id
            LEFT JOIN venues v ON l.location_id = v.city_id AND v.is_active = 1
            LEFT JOIN players pl ON l.location_id = pl.home_city_id AND pl.calibration_tier != 'INACTIVE'
            LEFT JOIN matches m ON v.venue_id = m.venue_id
            WHERE l.location_type = 'CITY' AND l.is_active = 1
            GROUP BY l.location_id
        """).fetchall()

        if not cities:
            st.info("No municipalities registered in database.")
        else:
            for ci in cities:
                cid = ci["location_id"]
                c_name = ci["location_name"]
                country = ci["parent_country"] or "Unknown"
                n_players = ci["total_players"]
                n_matches = ci["total_matches"]

                # Calculate live K bridges (players with RD <= 80 and >= 5 cross-location games)
                k_bridges = conn.execute("""
                    SELECT COUNT(DISTINCT p.player_id) 
                    FROM players p 
                    JOIN match_logs ml ON p.player_id = ml.player_id
                    JOIN matches m ON ml.match_id = m.match_id
                    WHERE p.home_city_id = ? AND p.rating_deviation <= 80.0 AND m.is_city_bridge = 1
                """, (cid,)).fetchone()[0]

                diag = evaluate_municipal_readiness(n_players, n_matches, k_bridges)
                it_val = calculate_intransitivity(cid, conn)
                conn.execute("""
                    UPDATE locations 
                    SET readiness_score = ?, intransitivity_index = ?, intransitivity_idx = ?, active_bridge_count = ?
                    WHERE location_id = ?
                """, (diag["readiness_pct"], it_val, it_val, k_bridges, cid))
                conn.commit()

                with st.expander(f"🏙️ {c_name} ({country}) — Readiness: {diag['readiness_pct']}% [{diag['status']}]"):
                    m1, m2, m3, m4, m5 = st.columns(5)
                    m1.metric("Registered Players", n_players)
                    m2.metric("Matches Hosted", n_matches)
                    m3.metric("Bridge Nodes (K)", k_bridges)
                    m4.metric("Intransitivity (I_T)", f"{it_val:.4f}")
                    m5.metric("Current Offset", f"{ci['hawking_offset']:+.4f}")

                    st.markdown("#### Macro Calibration Pathway")
                    pipe_col1, pipe_col2 = st.columns([2, 1])

                    calc_mode = pipe_col1.selectbox(
                        f"Calculation Mode ({c_name})",
                        [
                            "Path A: Empirical Bridge Diffusion (K ≥ 1)",
                            "Path B: Synthetic Ghost Sandbox (K = 0 Islands)",
                            "Path C: Dynamic Hybrid Synthesis"
                        ],
                        key=f"mode_{cid}"
                    )

                    local_ratings_rows = conn.execute("""
                        SELECT latent_mmr FROM players 
                        WHERE home_city_id = ? AND rating_deviation <= 100.0 AND calibration_tier != 'INACTIVE'
                        ORDER BY latent_mmr DESC
                    """, (cid,)).fetchall()
                    local_ratings = [float(r[0]) for r in local_ratings_rows]

                    suggested_shift = 0.0000

                    if "Path A" in calc_mode:
                        if k_bridges == 0:
                            st.warning("⚠️ Path A requires K ≥ 1 Bridge Nodes. Current K = 0. Tikhonov confidence = 0.0000.")
                            suggested_shift = 0.0000
                        else:
                            w_c = diag["w_conf"]
                            suggested_shift = round(-0.0300 * w_c, 4)
                            st.info(f"Empirical Bridge calculation active: K={k_bridges} ➔ W_conf={w_c:.4f}. Suggested Shift: {suggested_shift:+.4f}")

                    elif "Path B" in calc_mode:
                        ghosts = conn.execute("SELECT * FROM synthetic_ghosts WHERE is_active = 1").fetchall()
                        ghost_list = [dict(g) for g in ghosts]
                        if len(local_ratings) == 0:
                            st.error("No verified players (RD ≤ 100.0) found in this city to simulate against ghosts.")
                        else:
                            sim_res = run_ghost_monte_carlo(local_ratings, ghost_list, n_sims=5000)
                            suggested_shift = sim_res["proposed_offset"]
                            st.success(f"Ghost Ensemble Win Rate: {sim_res['ensemble_win_rate']*100:.1f}% ➔ Projected Offset: {suggested_shift:+.4f}")

                    elif "Path C" in calc_mode:
                        ghosts = conn.execute("SELECT * FROM synthetic_ghosts WHERE is_active = 1").fetchall()
                        ghost_list = [dict(g) for g in ghosts]
                        sim_res = run_ghost_monte_carlo(local_ratings, ghost_list, n_sims=5000) if local_ratings else {"proposed_offset": 0.0}
                        w_c = diag["w_conf"]
                        shift_a = -0.0300 * w_c
                        shift_b = sim_res["proposed_offset"]
                        suggested_shift = round((w_c * shift_a) + ((1.0 - w_c) * shift_b), 4)
                        st.info(f"Hybrid Blend (W_conf: {w_c:.2f}): Bridge={shift_a:+.4f} | Ghost={shift_b:+.4f} ➔ Blended: {suggested_shift:+.4f}")

                    # STAGED REVIEW & DEPLOYMENT DRAWER
                    st.markdown("---")
                    with st.expander(f"🔍 Review & Deploy Offset for {c_name}", expanded=False):
                        st.markdown(
                            "**Deployment Guardrails:** Provisional beginners (RD > 100) receive `+0.0000` (Firewall). "
                            "Players $\le 4.5$ scale affinely ($R/4.5$). Elite pros ($> 4.5$) scale via Jacobian Elasticity."
                        )

                        c_dep1, c_dep2 = st.columns([2, 1])
                        approved_step = c_dep1.number_input(
                            f"Approved Offset Step ({c_name})",
                            value=float(suggested_shift),
                            step=0.0050,
                            format="%.4f",
                            key=f"step_{cid}"
                        )

                        affected_players = conn.execute("""
                            SELECT player_id, display_name, latent_mmr, rating_deviation, calibration_tier 
                            FROM players WHERE home_city_id = ? AND calibration_tier != 'INACTIVE'
                            ORDER BY latent_mmr DESC
                        """, (cid,)).fetchall()

                        preview_data = []
                        for p in affected_players:
                            delta = calculate_hawking_player_delta(float(p["latent_mmr"]), float(p["rating_deviation"]), approved_step)
                            preview_data.append({
                                "Player": p["display_name"],
                                "Pre MMR": f"{p['latent_mmr']:.4f}",
                                "RD": f"{p['rating_deviation']:.1f}",
                                "Tier": p["calibration_tier"],
                                "Applied ΔR": f"{delta:+.4f}",
                                "Post MMR": f"{(p['latent_mmr'] + delta):.4f}",
                                "Guardrail Rule": "Provisional Firewall" if p["rating_deviation"] > 100.0 else ("Jacobian Elasticity" if p["latent_mmr"] > 4.5 else "Affine Scaling")
                            })

                        if preview_data:
                            st.dataframe(pd.DataFrame(preview_data), use_container_width=True)

                        if st.button(f"🚀 Approve & Deploy {approved_step:+.4f} to {c_name}", type="primary", key=f"btn_deploy_{cid}"):
                            try:
                                for p in affected_players:
                                    delta = calculate_hawking_player_delta(float(p["latent_mmr"]), float(p["rating_deviation"]), approved_step)
                                    if delta != 0.0:
                                        conn.execute("""
                                            UPDATE players SET 
                                                latent_mmr = latent_mmr + ?,
                                                display_rating = display_rating + ?,
                                                rolling_90d_peak = rolling_90d_peak + ?,
                                                tournament_floor = tournament_floor + ?,
                                                tournament_floor_rating = tournament_floor_rating + ?
                                            WHERE player_id = ?
                                        """, (delta, delta, delta, delta, delta, p["player_id"]))

                                        log_id = f"L_HAWKING_{p['player_id']}_{datetime.now().strftime('%Y%m%d%H%M%S')}_{uuid.uuid4().hex[:4]}"
                                        conn.execute("""
                                            INSERT INTO match_logs (
                                                log_id, match_id, player_id, pre_latent_mmr, post_latent_mmr,
                                                pre_display_rating, post_display_rating, pre_rd, post_rd,
                                                pre_accuracy_pct, post_accuracy_pct, delta_r, guardrails_triggered, logged_at
                                            ) VALUES (?, 'HAWKING_SYNC', ?, ?, ?, ?, ?, ?, ?, 0.0, 0.0, ?, ?, CURRENT_TIMESTAMP)
                                        """, (
                                            log_id, p["player_id"], p["latent_mmr"], p["latent_mmr"] + delta,
                                            p["latent_mmr"], p["latent_mmr"] + delta, p["rating_deviation"],
                                            p["rating_deviation"], delta, json.dumps(["GLOBAL_HAWKING_SYNC"])
                                        ))

                                conn.execute("""
                                    UPDATE locations SET hawking_offset = hawking_offset + ? WHERE location_id = ?
                                """, (approved_step, cid))

                                conn.commit()
                                st.success(f"Successfully applied {approved_step:+.4f} offset to {c_name}!")
                                st.rerun()
                            except Exception as e:
                                conn.rollback()
                                st.error(f"Deployment failed: {str(e)}")

    # --------------------------------------------------------------------------
    # SUB-TAB 2: SYNTHETIC GHOST SANDBOX
    # --------------------------------------------------------------------------
    with h_tab2:
        st.subheader("Ensemble AI Ghost Benchmark Directory")
        st.caption("Manage synthetic bot profiles and execute on-demand Monte Carlo shadow simulations.")

        ghost_records = conn.execute("SELECT * FROM synthetic_ghosts").fetchall()
        g_cols = st.columns(len(ghost_records) if ghost_records else 1)
        for i, g in enumerate(ghost_records):
            with g_cols[i]:
                st.markdown(f"### 🤖 {g['ghost_name']}")
                st.write(f"**Playstyle:** `{g['playstyle']}`")
                st.write(f"**Baseline MMR:** `{g['assigned_mmr']:.4f}`")
                st.write(f"**Sims Run:** `{g['sims_run_count']}`")
                st.write(f"**Status:** `{'ACTIVE' if g['is_active'] else 'DISABLED'}`")

        st.markdown("---")
        st.subheader("Run Standalone Monte Carlo Simulation")

        sim_c1, sim_c2, sim_c3 = st.columns(3)
        target_c_name = sim_c1.selectbox("Target City", [c["location_name"] for c in cities] if cities else [])
        sim_depth = sim_c2.select_slider("Monte Carlo Iterations", options=[1000, 5000, 10000, 20000], value=10000)

        if st.button("⚡ Run Full Monte Carlo Duels", type="primary"):
            c_row = conn.execute("SELECT location_id FROM locations WHERE location_name = ?", (target_c_name,)).fetchone()
            if c_row:
                p_ratings = [
                    float(r[0]) for r in conn.execute("""
                        SELECT latent_mmr FROM players 
                        WHERE home_city_id = ? AND rating_deviation <= 100.0 AND calibration_tier != 'INACTIVE'
                    """, (c_row[0],)).fetchall()
                ]

                if not p_ratings:
                    st.error("No verified players available in this city to run shadow duels.")
                else:
                    ghost_list = [dict(g) for g in conn.execute("SELECT * FROM synthetic_ghosts WHERE is_active = 1").fetchall()]
                    res = run_ghost_monte_carlo(p_ratings, ghost_list, n_sims=sim_depth)

                    st.markdown("### 📊 Simulation Output")
                    r1, r2, r3 = st.columns(3)
                    r1.metric("Ensemble Win Rate", f"{res['ensemble_win_rate']*100:.2f}%")
                    r2.metric("Projected Raw Offset", f"{res['proposed_offset']:+.4f}")
                    r3.metric("Total Duels Fired", f"{res['total_sims']:,}")

                    st.markdown("#### Archetype Breakdown")
                    for name, wr in res["breakdown"].items():
                        st.write(f"• **{name}:** `{wr*100:.2f}% win rate`")

    # --------------------------------------------------------------------------
    # SUB-TAB 3: GRAPH CENTRALITY & NETWORK RISKS
    # --------------------------------------------------------------------------
    with h_tab3:
        st.subheader("Social Graph Centrality & Disconnection Telemetry")
        st.caption("Detect isolated clusters, smurf rings, and players at risk of rating quarantine.")

        disconnected_players = conn.execute("""
            SELECT p.player_id, p.display_name, l.location_name as city, p.latent_mmr, p.rating_deviation,
                   COUNT(ml.log_id) as total_games,
                   COUNT(DISTINCT ml.match_id) as unique_matches
            FROM players p
            JOIN locations l ON p.home_city_id = l.location_id
            LEFT JOIN match_logs ml ON p.player_id = ml.player_id
            WHERE p.calibration_tier != 'INACTIVE'
            GROUP BY p.player_id
            HAVING total_games < 3
        """).fetchall()

        if disconnected_players:
            st.warning(f"⚠️ Found {len(disconnected_players)} players with low network connectivity (fewer than 3 recorded games):")
            disc_data = [{
                "Player": p["display_name"],
                "City": p["city"],
                "MMR": f"{p['latent_mmr']:.4f}",
                "RD": f"{p['rating_deviation']:.1f}",
                "Games": p["total_games"]
            } for p in disconnected_players]
            st.dataframe(pd.DataFrame(disc_data), use_container_width=True)
        else:
            st.success("✅ All active players meet minimum graph connectivity.")

    # --------------------------------------------------------------------------
    # SUB-TAB 4: HAWKING GOVERNANCE & PARAMETERS
    # --------------------------------------------------------------------------
    with h_tab4:
        st.subheader("Hawking Parameter Controls & Circuit Breakers")
        cfg_rows = conn.execute("""
            SELECT * FROM global_config 
            WHERE param_key LIKE '%HAWKING%' OR param_key LIKE '%BRIDGE%' OR param_key LIKE '%TIKHONOV%' OR param_key LIKE '%DRIFT%' OR param_key LIKE '%INACTIVITY%'
        """).fetchall()

        if not cfg_rows:
            st.info("No specific Hawking parameters registered. Using algorithmic defaults.")
        else:
            for c in cfg_rows:
                with st.expander(f"⚙️ {c['param_key']}"):
                    st.write(f"**Description:** {c['description']}")
                    st.info(f"💡 {c['tuning_guide']}")
                    val = st.number_input(
                        "Parameter Value",
                        value=float(c["param_value"]),
                        key=f"hwk_cfg_{c['param_key']}"
                    )
                    if st.button("Save", key=f"save_hwk_{c['param_key']}"):
                        conn.execute("UPDATE global_config SET param_value = ? WHERE param_key = ?", (val, c["param_key"]))
                        conn.commit()
                        st.success(f"Updated {c['param_key']} -> {val}")
                        st.rerun()

    conn.close()
