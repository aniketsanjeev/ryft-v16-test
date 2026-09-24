import streamlit as st
import sqlite3
import math
import json
import os
import io
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
    c.execute('''CREATE TABLE IF NOT EXISTS locations (location_id TEXT PRIMARY KEY, location_type TEXT NOT NULL, location_name TEXT NOT NULL, parent_id TEXT, country_code TEXT DEFAULT 'IND', intransitivity_idx REAL DEFAULT 0.0, hawking_offset REAL DEFAULT 0.0, suggested_offset REAL DEFAULT 0.0, readiness_score REAL DEFAULT 0.0, active_bridge_count INTEGER DEFAULT 0, total_active_players INTEGER DEFAULT 0, total_matches_played INTEGER DEFAULT 0, active_venues_count INTEGER DEFAULT 0, median_latent_mmr REAL DEFAULT 3.000, highest_player_mmr REAL DEFAULT 3.000, lowest_player_mmr REAL DEFAULT 3.000, is_normalized INTEGER DEFAULT 0, updated_at TEXT, is_active INTEGER DEFAULT 1)''')
    c.execute('''CREATE TABLE IF NOT EXISTS venues (venue_id TEXT PRIMARY KEY, venue_name TEXT NOT NULL, raw_input_name TEXT, is_verified INTEGER DEFAULT 0, city_id TEXT NOT NULL, country_code TEXT NOT NULL, court_count INTEGER DEFAULT 1, total_matches_played INTEGER DEFAULT 0, unique_players_count INTEGER DEFAULT 0, city_bridge_matches_count INTEGER DEFAULT 0, country_bridge_matches_count INTEGER DEFAULT 0, average_player_mmr REAL DEFAULT 3.000, is_active INTEGER DEFAULT 1, created_at TEXT)''')
    c.execute('''CREATE TABLE IF NOT EXISTS rating_categories (category_name TEXT PRIMARY KEY, min_rating REAL NOT NULL, max_rating REAL NOT NULL, sort_order INTEGER NOT NULL, speed_multiplier REAL DEFAULT 1.00)''')
    c.execute('''CREATE TABLE IF NOT EXISTS match_formats (format_id TEXT PRIMARY KEY, format_name TEXT NOT NULL, category TEXT NOT NULL, mc_weight REAL NOT NULL, target_games INTEGER, total_points INTEGER, is_session_bound INTEGER DEFAULT 0, is_active INTEGER DEFAULT 1)''')
    c.execute('''CREATE TABLE IF NOT EXISTS players (player_id TEXT PRIMARY KEY, display_name TEXT NOT NULL, initial_rating REAL NOT NULL, home_venue_id TEXT, home_city_id TEXT NOT NULL, home_country_code TEXT NOT NULL DEFAULT 'IND', latent_mmr REAL NOT NULL, display_rating REAL NOT NULL, rolling_90d_peak REAL NOT NULL DEFAULT 3.000, rolling_180d_peak REAL NOT NULL DEFAULT 3.000, rolling_365d_peak REAL NOT NULL DEFAULT 3.000, tournament_floor REAL NOT NULL DEFAULT 0.000, all_time_badge TEXT DEFAULT 'Intermediate', consecutive_losses INTEGER DEFAULT 0, rating_deviation REAL NOT NULL DEFAULT 350.000, rating_accuracy_pct REAL DEFAULT 0.0, accuracy_s_rd REAL DEFAULT 0.0, accuracy_s_matches REAL DEFAULT 0.0, accuracy_s_diversity REAL DEFAULT 0.0, calibration_tier TEXT DEFAULT 'PROVISIONAL', is_provisional INTEGER DEFAULT 1, is_manually_verified INTEGER DEFAULT 0, verified_matches_count INTEGER DEFAULT 0, unique_opponents_count INTEGER DEFAULT 0, unique_partners_count INTEGER DEFAULT 0, unique_venues_count INTEGER DEFAULT 0, unique_cities_count INTEGER DEFAULT 0, unique_countries_count INTEGER DEFAULT 0, bridge_matches_count INTEGER DEFAULT 0, is_active_bridge INTEGER DEFAULT 0, is_country_bridge INTEGER DEFAULT 0, graph_centrality REAL DEFAULT 0.20, is_quarantined INTEGER DEFAULT 0, is_anchor INTEGER DEFAULT 0, is_ceiling_anchor INTEGER DEFAULT 0, is_dummy INTEGER DEFAULT 0, last_match_time TEXT, created_at TEXT)''')
    c.execute('''CREATE TABLE IF NOT EXISTS sessions (session_id TEXT PRIMARY KEY, venue_id TEXT NOT NULL, session_title TEXT NOT NULL, session_date TEXT NOT NULL DEFAULT '', start_time TEXT NOT NULL DEFAULT '09:00', end_time TEXT NOT NULL DEFAULT '11:00', match_mode TEXT NOT NULL DEFAULT 'DOUBLES', team_format TEXT NOT NULL, format_id TEXT NOT NULL, tourney_structure TEXT NOT NULL DEFAULT 'ROUND_ROBIN', is_tournament INTEGER DEFAULT 0, court_ids_json TEXT NOT NULL, enrolled_player_ids TEXT NOT NULL, teams_json TEXT DEFAULT '[]', checked_in_player_ids TEXT DEFAULT '[]', player_count INTEGER NOT NULL, active_checked_in_count INTEGER DEFAULT 0, total_rounds INTEGER NOT NULL DEFAULT 1, current_round INTEGER DEFAULT 0, session_status TEXT DEFAULT 'CONFIG', created_at TEXT NOT NULL, completed_at TEXT, current_stage TEXT, lineup_mode TEXT)''')
    c.execute('''CREATE TABLE IF NOT EXISTS session_matches (session_match_id TEXT PRIMARY KEY, session_id TEXT NOT NULL, round_number INTEGER NOT NULL, court_id TEXT NOT NULL, match_order INTEGER NOT NULL, team_a_p1_id TEXT NOT NULL, team_a_p2_id TEXT, team_b_p1_id TEXT NOT NULL, team_b_p2_id TEXT, team_a_name TEXT DEFAULT '', team_b_name TEXT DEFAULT '', score_team_a INTEGER DEFAULT 0, score_team_b INTEGER DEFAULT 0, games_winner INTEGER DEFAULT 0, games_loser INTEGER DEFAULT 0, set_scores_json TEXT DEFAULT '[]', match_status TEXT DEFAULT 'SCHEDULED', started_at TEXT, completed_at TEXT, committed_match_id TEXT, stage TEXT, group_id TEXT, flight_number INTEGER DEFAULT 1)''')
    c.execute('''CREATE TABLE IF NOT EXISTS matches (match_id TEXT PRIMARY KEY, venue_id TEXT NOT NULL, format_id TEXT NOT NULL, session_id TEXT, is_singles INTEGER DEFAULT 0, is_tournament INTEGER DEFAULT 0, is_venue_bridge INTEGER DEFAULT 0, is_city_bridge INTEGER DEFAULT 0, is_country_bridge INTEGER DEFAULT 0, team_a_p1_id TEXT NOT NULL, team_a_p2_id TEXT, team_b_p1_id TEXT NOT NULL, team_b_p2_id TEXT, score_team_a INTEGER DEFAULT 0, score_team_b INTEGER DEFAULT 0, set_scores_json TEXT DEFAULT '[]', games_winner INTEGER DEFAULT 0, games_loser INTEGER DEFAULT 0, pre_rating_a REAL DEFAULT 3.000, pre_rating_b REAL DEFAULT 3.000, win_expectancy_a REAL DEFAULT 0.5000, applied_m_c REAL DEFAULT 1.00, applied_s_margin REAL DEFAULT 1.000, delta_r_p1 REAL DEFAULT 0.0, delta_r_p2 REAL DEFAULT 0.0, delta_r_p3 REAL DEFAULT 0.0, delta_r_p4 REAL DEFAULT 0.0, guardrails_summary TEXT DEFAULT '[]', is_retroactive INTEGER DEFAULT 0, match_timestamp TEXT NOT NULL, processed_at TEXT)''')
    c.execute('''CREATE TABLE IF NOT EXISTS match_logs (log_id TEXT PRIMARY KEY, match_id TEXT NOT NULL, player_id TEXT NOT NULL, pre_latent_mmr REAL NOT NULL, post_latent_mmr REAL NOT NULL, pre_display_rating REAL NOT NULL, post_display_rating REAL NOT NULL, pre_rd REAL NOT NULL, post_rd REAL NOT NULL, pre_accuracy_pct REAL NOT NULL, post_accuracy_pct REAL NOT NULL, delta_r REAL NOT NULL, is_elevator_active INTEGER DEFAULT 0, guardrails_triggered TEXT DEFAULT '[]', is_retroactive INTEGER DEFAULT 0, logged_at TEXT NOT NULL)''')
    c.execute('''CREATE TABLE IF NOT EXISTS global_config (param_key TEXT PRIMARY KEY, param_value REAL NOT NULL, is_active INTEGER DEFAULT 1, title TEXT, description TEXT, tuning_guide TEXT, module_group TEXT)''')
    c.execute('''CREATE TABLE IF NOT EXISTS tournaments (tourney_id TEXT PRIMARY KEY, name TEXT NOT NULL, venue_id TEXT NOT NULL, tourney_date TEXT NOT NULL, floor_category TEXT NOT NULL, status TEXT DEFAULT 'PENDING', created_at TEXT NOT NULL)''')
    c.execute('''CREATE TABLE IF NOT EXISTS tourney_matches (t_match_id TEXT PRIMARY KEY, tourney_id TEXT NOT NULL, match_time TEXT NOT NULL, format_id TEXT NOT NULL, is_singles INTEGER DEFAULT 0, team_a_p1_id TEXT NOT NULL, team_a_p2_id TEXT, team_b_p1_id TEXT NOT NULL, team_b_p2_id TEXT, score_team_a INTEGER DEFAULT 0, score_team_b INTEGER DEFAULT 0, games_winner INTEGER DEFAULT 0, games_loser INTEGER DEFAULT 0, set_scores_json TEXT DEFAULT '[]', status TEXT DEFAULT 'STAGED')''')
    c.execute('''CREATE TABLE IF NOT EXISTS session_rosters (roster_id TEXT PRIMARY KEY, session_id TEXT NOT NULL, entity_type TEXT, player_id_p1 TEXT, player_id_p2 TEXT, display_label TEXT, group_id TEXT DEFAULT 'A', seed_index INTEGER DEFAULT 0, initial_mmr REAL DEFAULT 3.0, initial_rd REAL DEFAULT 350.0, is_checked_in INTEGER DEFAULT 0, is_defected INTEGER DEFAULT 0, matches_played INTEGER DEFAULT 0, matches_won INTEGER DEFAULT 0, matches_lost INTEGER DEFAULT 0, matches_tied INTEGER DEFAULT 0, standing_points INTEGER DEFAULT 0, games_for INTEGER DEFAULT 0, games_against INTEGER DEFAULT 0, net_game_diff INTEGER DEFAULT 0, points_for INTEGER DEFAULT 0, points_against INTEGER DEFAULT 0, net_point_diff INTEGER DEFAULT 0, consecutive_sit INTEGER DEFAULT 0, is_qualified INTEGER DEFAULT 0, knockout_seed INTEGER)''')

    add_column_if_not_exists(c, "players", "consecutive_losses", "INTEGER DEFAULT 0")

    if c.execute("SELECT COUNT(*) FROM rating_categories").fetchone()[0] == 0:
        cats = [("Beginner", 0.0, 0.999, 1, 1.0), ("Beginner+", 1.0, 1.999, 2, 1.0), ("Intermediate", 2.0, 3.499, 3, 1.0), ("Intermediate+", 3.5, 4.499, 4, 1.0), ("Advanced", 4.5, 5.499, 5, 1.0), ("Pro", 5.5, 6.299, 6, 1.0), ("Elite", 6.3, 7.0, 7, 1.0)]
        for cn, cmn, cmx, so, sm in cats: c.execute("INSERT OR IGNORE INTO rating_categories VALUES (?,?,?,?,?)", (cn, cmn, cmx, so, sm))

    fmt = [("STD_B03", "Best of 3 Sets", "MULTI_SET", 1.00, None, None, 0, 1), ("RACE_6", "Race to 6 Games", "RACE_GAMES", 0.70, 6, None, 0, 1), ("AMER_24", "Americano 24 Points", "AMERICANO", 0.30, None, 24, 1, 1), ("MEX_24", "Mexicano 24 Points", "MEXICANO", 0.30, None, 24, 1, 1)]
    for fi, fn, cat, mc, tg, tp, isb, ia in fmt: c.execute("INSERT OR IGNORE INTO match_formats VALUES (?,?,?,?,?,?,?,?)", (fi, fn, cat, mc, tg, tp, isb, ia))

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
        ("MAX_24H_EXCHANGE_CAP", 0.150, 1, "24H Casual Cap", "Net transfer ceiling.", "Prevents farming.", "5. Exchange Caps & Security"),
        ("PROVISIONAL_CAP_MULTIPLIER", 2.5, 1, "Provisional Cap Relaxer", "Multiplier on 24H cap for PRs.", "Allows 0.375 point movement.", "5. Exchange Caps & Security"),
        ("SESSION_EXCHANGE_CAP", 0.300, 1, "Verified Session Cap", "Cap for verified club events.", "Doubles point limits for mixers.", "5. Exchange Caps & Security"),
        ("MIN_SESSION_PLAYERS", 6, 1, "Session Participant Floor", "Min players required to unlock session cap.", "Events with fewer revert to 0.150.", "5. Exchange Caps & Security"),
        ("TOURNAMENT_MULTIPLIER_ACTIVE", 1, 1, "Tournament Multiplier Toggle", "Activates stakes multiplier for tournament.", "1 = Active.", "5. Exchange Caps & Security"),
        ("TOURNAMENT_STAKES_MULTIPLIER", 1.15, 1, "Tournament Stakes Multiplier", "Rating delta multiplier for tournaments.", "Default 1.15 (+15%).", "5. Exchange Caps & Security"),
        ("RD_MIN", 30.0, 1, "Certainty Floor", "Absolute uncertainty floor.", "Prevents RD dropping below 30.0.", "6. Uncertainty & Rust"),
        ("RD_MAX", 350.0, 1, "Unrated Starting RD", "Uncertainty assigned at registration.", "Starting uncertainty.", "6. Uncertainty & Rust"),
        ("RD_INFO_VARIANCE", 65.0, 1, "Contraction Speed", "Denominator in RD shrinkage.", "Lower values shrink RD faster.", "6. Uncertainty & Rust"),
        ("INACTIVITY_CONSTANT", 12.0, 1, "Inactivity Rust Rate", "Monthly uncertainty growth.", "Points of RD regained per month.", "6. Uncertainty & Rust"),
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
        ("ISLAND_ACCURACY_CAP", 80.0, 1, "Island Geographic Cap", "Max accuracy if City has 0 bridges.", "Caps accuracy.", "7. Accuracy & Tri-Gates"),
        ("BRIDGE_RD_THRESHOLD", 80.0, 1, "Bridge Max RD", "Max RD to qualify as Bridge.", "Count if RD <= 80.", "8. Hawking Macro"),
        ("BRIDGE_MIN_MATCHES", 5, 1, "Bridge Min Matches", "Away matches required to link cities.", "Matches required before linking.", "8. Hawking Macro"),
        ("CIRCUIT_BREAKER", 0.0250, 1, "Auto Cron Safety Ceiling", "Max shift per weekly cycle.", "Limits automated macro shifts.", "8. Hawking Macro")
    ]
    for k, v, act, tit, desc, tune, grp in master_params:
        c.execute("INSERT INTO global_config VALUES (?, ?, ?, ?, ?, ?, ?) ON CONFLICT(param_key) DO UPDATE SET title=excluded.title, description=excluded.description, tuning_guide=excluded.tuning_guide, module_group=excluded.module_group", (k, v, act, tit, desc, tune, grp))
    conn.commit(); conn.close()

init_db()

# ==============================================================================
# 2. V.16 CALCULATION ENGINE
# ==============================================================================
class RyftV16:
    @staticmethod
    def get_configs(conn=None):
        owns = False
        if not conn: conn = get_db_connection(); owns = True
        rows = conn.execute("SELECT param_key, param_value FROM global_config WHERE is_active = 1").fetchall()
        if owns: conn.close()
        return {r["param_key"]: r["param_value"] for r in rows}

    @staticmethod
    def get_cat_for_rating(r_val, conn=None):
        owns = False
        if not conn: conn = get_db_connection(); owns = True
        cats = conn.execute("SELECT category_name, min_rating, max_rating, speed_multiplier FROM rating_categories ORDER BY sort_order ASC").fetchall()
        if owns: conn.close()
        for c in cats:
            if c["min_rating"] <= r_val <= c["max_rating"]: return c["category_name"], c["min_rating"], c["max_rating"], c["speed_multiplier"] or 1.00
        return "Intermediate", 2.000, 3.499, 1.00

    @staticmethod
    def calc_accuracy(rd, m_count, opp_count, is_prov, k_bridges, cfg):
        s_rd = max(0.0, min(1.0, (cfg.get("RD_MAX", 350.0) - rd) / (cfg.get("RD_MAX", 350.0) - cfg.get("RD_MIN", 30.0))))
        t_m = cfg.get("TARGET_MATCHES_PROVISIONAL", 3) if is_prov else cfg.get("TARGET_MATCHES_ANCHOR", 15)
        t_o = cfg.get("TARGET_OPPONENTS_PROVISIONAL", 2) if is_prov else cfg.get("TARGET_OPPONENTS_ANCHOR", 8)
        s_m = min(1.0, m_count / float(t_m)); s_d = min(1.0, opp_count / float(t_o))
        
        raw_acc = (cfg.get("ACCURACY_WEIGHT_RD", 0.50) * s_rd + cfg.get("ACCURACY_WEIGHT_MATCHES", 0.25) * s_m + cfg.get("ACCURACY_WEIGHT_DIVERSITY", 0.25) * s_d) * 100.0
        if is_prov: raw_acc *= cfg.get("PROVISIONAL_ACCURACY_DAMPENER", 0.40)
        phi = min(1.0, cfg.get("ISLAND_ACCURACY_CAP", 80.0) / 100.0) if k_bridges == 0 else min(1.0, 0.80 + (0.10 * k_bridges))
        return round(raw_acc * phi, 1), round(s_rd * 100.0, 1), round(s_m * 100.0, 1), round(s_d * 100.0, 1)

    @staticmethod
    def sync_player_aggregates(player_id, conn=None):
        owns = False
        if not conn: conn = get_db_connection(); owns = True
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

        conn.execute("UPDATE players SET verified_matches_count=?, unique_opponents_count=?, bridge_matches_count=?, is_active_bridge=?, is_country_bridge=?, all_time_badge=? WHERE player_id=?", (m_count, opp_count, cross_city_matches, is_act_city_bridge, is_act_ctry_bridge, cur_cat, player_id))
        if owns: conn.commit(); conn.close()

    @classmethod
    def compute_match(cls, p1_raw, p2_raw, p3_raw, p4_raw, s_a, s_b, g_w_raw, g_l_raw, fmt_id, v_id, is_singles, is_dry=False, is_tournament=False, session_id=None, session_checked_in=0, conn=None, cumulative_deltas=None):
        owns = False
        if not conn: conn = get_db_connection(); owns = True

        p1, p3 = dict(p1_raw), dict(p3_raw)
        p2, p4 = dict(p2_raw) if p2_raw else None, dict(p4_raw) if p4_raw else None

        cfg = cls.get_configs(conn=conn)
        p_exp = cfg.get("POWER_MEAN_P", 3.0)
        g_w, g_l = max(g_w_raw, g_l_raw + 1), g_l_raw
        
        ta_r = float(p1.get("latent_mmr", 3.0)) if is_singles else ((float(p1.get("latent_mmr", 3.0))**p_exp + float(p2.get("latent_mmr", 3.0))**p_exp) / 2.0)**(1.0 / p_exp)
        tb_r = float(p3.get("latent_mmr", 3.0)) if is_singles else ((float(p3.get("latent_mmr", 3.0))**p_exp + float(p4.get("latent_mmr", 3.0))**p_exp) / 2.0)**(1.0 / p_exp)
        
        ea = 1.0 / (1.0 + 10.0**((tb_r - ta_r) / cfg.get("LOGISTIC_BETA", 2.0)))
        act_a = 1.0 if s_a > s_b else (0.5 if s_a == s_b else 0.0)
        
        tot_g = g_w + g_l
        m_base, m_scale = cfg.get("MARGIN_BASE", 0.80), cfg.get("MARGIN_SCALE", 0.40)
        s_margin = max(0.80, min(1.20, m_base + (m_scale * ((g_w - g_l) / tot_g)) if tot_g > 0 else 1.0))
        
        fmt_row = conn.execute("SELECT mc_weight FROM match_formats WHERE format_id = ?", (fmt_id,)).fetchone()
        mc = float(fmt_row["mc_weight"]) if fmt_row else 1.00

        opp_rd_b = max(float(p3.get("rating_deviation", 350.0)), float(p4.get("rating_deviation", 350.0))) if not is_singles else float(p3.get("rating_deviation", 350.0))
        opp_rd_a = max(float(p1.get("rating_deviation", 350.0)), float(p2.get("rating_deviation", 350.0))) if not is_singles else float(p1.get("rating_deviation", 350.0))

        participants = [(p1, True, p2, opp_rd_b), (p3, False, p4, opp_rd_a)]
        if not is_singles: participants.extend([(p2, True, p1, opp_rd_b), (p4, False, p3, opp_rd_a)])

        res = []
        prov_count = sum(1 for px, _, _, _ in participants if bool(px.get("is_provisional", 1)))
        o_map = [cfg.get("COHORT_FACTOR_0_PROV", 1.0), cfg.get("COHORT_FACTOR_1_PROV", 0.75), cfg.get("COHORT_FACTOR_2_PROV", 0.50), cfg.get("COHORT_FACTOR_3_PROV", 0.25)]
        omega = o_map[min(3, max(0, prov_count - 1))]

        for p, is_a, partner, opp_rd in participants:
            flags = []
            r, rd, prov = float(p.get("latent_mmr", 3.0)), float(p.get("rating_deviation", 350.0)), bool(p.get("is_provisional", 1))
            is_manual_override = bool(p.get("is_manually_verified", 0))
            is_quar = bool(p.get("is_quarantined", 0))
            is_anchor_player = bool(p.get("is_anchor", 0)) or (p.get("calibration_tier") == "ANCHOR") or (not prov and rd <= 100.0)
            won = (is_a and s_a > s_b) or (not is_a and s_b > s_a)
            
            q, sig = 0.0057565, cfg.get("RD_INFO_VARIANCE", 65.0)
            
            lmt = p.get("last_match_time")
            if lmt:
                try:
                    lmt_dt = datetime.fromisoformat(lmt.replace("Z", "+00:00"))
                    delta_t_days = max(0.0, (datetime.now(timezone.utc) - lmt_dt).total_seconds() / 86400.0)
                    rust_c = cfg.get("INACTIVITY_CONSTANT", 12.0) / 30.0
                    rd = min(350.0, math.sqrt(rd**2 + (rust_c**2 * delta_t_days)))
                    if rd > 100.0 and p.get("calibration_tier") == "VERIFIED": flags.append("[ALERT_INACTIVITY_REPROVISION_FLAG]")
                except Exception: pass
            
            g_opp = 1.0 / math.sqrt(1.0 + (3.0 * (q**2) * (opp_rd**2)) / (math.pi**2))
            
            if is_quar:
                raw_d = 0.000; flags.append("[ALERT_QUARANTINE_ISOLATION_ACTIVE]")
            elif prov and s_a != s_b:
                opp_team_r = tb_r if is_a else ta_r
                ratio = (g_w_raw + 0.5) / (g_l_raw + 0.5) if won else (g_l_raw + 0.5) / (g_w_raw + 0.5)
                r_perf = opp_team_r + 2.0 * math.log10(ratio)
                raw_d = (r_perf - r) * cfg.get("PROVISIONAL_ABSORPTION_ALPHA", 0.45) * mc * g_opp
                max_d = cfg.get("MAX_PROVISIONAL_DELTA", 0.750)
                raw_d = max(-max_d, min(max_d, raw_d))
                flags.append("RIGHTSIZING_INTERPOLATION")
            else:
                # FIX 2: Verified Anchors locked to K_MIN (0.080) to buffer rating loss against unrated smurfs
                if is_anchor_player:
                    k_base = cfg.get("K_MIN", 0.080)
                else:
                    k_base = cfg.get("K_MAX", 0.400) - (r / cfg.get("R_MAX", 7.000)) * (cfg.get("K_MAX", 0.400) - cfg.get("K_MIN", 0.080))
                
                _, _, _, cat_speed = cls.get_cat_for_rating(r, conn=conn)
                if cat_speed != 1.00: k_base *= cat_speed; flags.append(f"CAT_SPEED ({cat_speed:.2f}x)")
                drag = ((7.000 - r) / 7.000) * ((7.000 - r) / (7.000 - 6.300))**2.5 if r >= 6.300 else 1.0
                if r >= 6.300: flags.append("[ALERT_ELITE_DRAG_MAX_RESISTANCE]")
                raw_d = k_base * drag * mc * s_margin * g_opp * (1.0 if is_a else -1.0) * (act_a - ea)

            if not is_singles and partner and not is_quar:
                gap = abs(r - float(partner.get("latent_mmr", 3.0)))
                io_gap1, io_m1 = cfg.get("ICE_OUT_GAP_TIER_1", 1.50), cfg.get("ICE_OUT_MULT_TIER_1", 0.20)
                io_gap2, io_m2 = cfg.get("ICE_OUT_GAP_TIER_2", 2.00), cfg.get("ICE_OUT_MULT_TIER_2", 0.05)
                ac_gap1, ac_m1 = cfg.get("ANTI_CARRY_GAP_TIER_1", 1.75), cfg.get("ANTI_CARRY_MULT_TIER_1", 0.50)
                
                part_prov = bool(partner.get("is_provisional", 1))
                part_rd = float(partner.get("rating_deviation", 350.0))
                is_mutual_prov = (prov and part_prov and rd > 200.0 and part_rd > 200.0)

                if is_mutual_prov:
                    flags.append("MUTUAL_PROV_EXEMPTION")
                else:
                    if not won and r > float(partner.get("latent_mmr", 3.0)):
                        dd = io_m2 if gap >= io_gap2 else (io_m1 if gap >= io_gap1 else (0.50 if gap >= 1.0 else 1.00))
                        raw_d *= dd
                        if dd < 1.00: flags.append(f"[ALERT_ICE_OUT_ANCHOR_SHIELD] ({int((1-dd)*100)}%)")
                    elif won and r < float(partner.get("latent_mmr", 3.0)):
                        dd = cfg.get("ANTI_CARRY_MULT_TIER_2", 0.25) if gap >= cfg.get("ANTI_CARRY_GAP_TIER_2", 2.50) else (ac_m1 if gap >= ac_gap1 else (0.75 if gap >= 1.2 else 1.00))
                        raw_d *= dd
                        if dd < 1.00: flags.append(f"ANTI_CARRY ({int((1-dd)*100)}%)")

            # Bit 16 Sybil Trust Bypass (<5 matches)
            m_played = int(p.get("verified_matches_count", 0))
            w_g = 1.0
            if m_played >= 5:
                w_g = min(1.0, float(p.get("graph_centrality", 0.20)) / 0.20) * min(1.0, float(p.get("unique_opponents_count", 0)) / 5.0)
            
            if w_g < 1.0 and not is_quar:
                raw_d *= w_g; flags.append(f"[ALERT_DISCONNECTED_GRAPH_DAMPENING] ({w_g:.2f}x)")

            base_cap = cfg.get("SESSION_EXCHANGE_CAP", 0.300) if session_id and session_checked_in >= cfg.get("MIN_SESSION_PLAYERS", 6) else cfg.get("MAX_24H_EXCHANGE_CAP", 0.150)
            cap = base_cap * (cfg.get("PROVISIONAL_CAP_MULTIPLIER", 2.5) if prov else 1.0)
            
            # FIX 1: Allow Rightsizing & Elevator Blowouts to Bypass the 24H Casual Cap (Bit 12 / Bit 13)
            bypass_cap = prov and (raw_d > 0) and ("RIGHTSIZING_INTERPOLATION" in flags) and bool(cfg.get("PROVISIONAL_BYPASS_EXCHANGE_CAP", 1))

            if bypass_cap:
                max_allowed = cfg.get("MAX_PROVISIONAL_DELTA", 0.750)
                final_d = min(max_allowed, max(0.0, raw_d))
                flags.append("PROVISIONAL_CAP_BYPASS")
            elif is_tournament and bool(cfg.get("TOURNAMENT_MULTIPLIER_ACTIVE", 1)):
                t_mult = cfg.get("TOURNAMENT_STAKES_MULTIPLIER", 1.15)
                final_d = raw_d * t_mult; flags.append(f"TOURNAMENT ({t_mult}x, Uncapped)")
            elif cumulative_deltas is not None:
                cum_d = cumulative_deltas.get(p["player_id"], 0.0)
                target_cum = cum_d + raw_d
                capped_target = max(-cap, min(cap, target_cum))
                final_d = capped_target - cum_d
                if abs(target_cum) > cap: flags.append("SESSION_CUMULATIVE_CAP_ENFORCED")
            else:
                final_d = max(-cap, min(cap, raw_d))
                if abs(raw_d) > cap: flags.append("CAP_ENFORCED")

            new_r = max(0.000, min(6.999, r + final_d))
            c_loss = int(p.get("consecutive_losses", 0))
            pre_disp = float(p.get("display_rating", r))
            
            if final_d < 0:
                if c_loss < 3 and (pre_disp - new_r <= cfg.get("DISPLAY_RATING_SOFT_FLOOR", 0.050)):
                    new_disp = pre_disp; flags.append("[ALERT_SOFT_FLOOR_DECOUPLING_ACTIVE]")
                else:
                    new_disp = new_r
                    if pre_disp - new_r > 0.150: flags.append("[ALERT_SOFT_FLOOR_DECOUPLING_MAX]")
            else:
                new_disp = new_r

            new_c_loss = 0 if won else c_loss + 1
            if (g_w_raw == 0 and not won):
                new_rd = rd; flags.append("[ALERT_ZERO_RESISTANCE_RD_FREEZE]")
            else:
                raw_new_rd = max(30.0, math.sqrt(1.0 / (1.0 / (rd**2) + (mc * s_margin * (g_opp**2) * omega) / (sig**2))))
                new_rd = rd - ((rd - raw_new_rd) * (cfg.get("PROVISIONAL_RD_CONTRACTION_RATIO", 0.35) if prov else 1.0))
            
            loc = conn.execute("SELECT active_bridge_count FROM locations WHERE location_id = ?", (p.get("home_city_id", ""),)).fetchone()
            acc_comp, a_rd, a_m, a_d = cls.calc_accuracy(new_rd, m_played + (0 if is_dry else 1), int(p.get("unique_opponents_count", 0)) + (0 if is_dry else 1), prov, loc["active_bridge_count"] if loc else 0, cfg)
            
            gate = (new_rd <= cfg.get("PROVISIONAL_RD_GATE", 100.0) and m_played >= 10 and int(p.get("unique_opponents_count", 0)) >= 5)
            new_prov = 0 if (gate or is_manual_override) else 1
            tier = "ANCHOR" if (acc_comp >= 90.0 and new_prov == 0 and new_rd <= 60.0) else ("VERIFIED" if (acc_comp >= cfg.get("TIER_PROVISIONAL_MAX", 69.99) and new_prov == 0) else "PROVISIONAL")
            if is_manual_override and tier == "PROVISIONAL": tier = "VERIFIED"

            res.append({
                "pid": p["player_id"], "name": format_pr_name(p.get("display_name", "Unknown"), prov), "raw_name": p.get("display_name", "Unknown"),
                "pre_r": r, "post_r": new_r, "pre_disp": pre_disp, "post_disp": new_disp, "delta": final_d, "pre_rd": rd, "post_rd": new_rd,
                "pre_acc": float(p.get("rating_accuracy_pct", 0.0)), "acc": acc_comp, "pre_tier": p.get("calibration_tier", "PROVISIONAL"), "tier": tier, 
                "a_rd": a_rd, "a_m": a_m, "a_d": a_d, "prov": new_prov, "c_loss": new_c_loss, "flags": flags
            })
            
        if owns: conn.close()
        return {"ta_r": ta_r, "tb_r": tb_r, "ea": ea, "mov": s_margin, "applied_m_c": mc, "res": res}

class SessionLogicEngine:
    @staticmethod
    def generate_schedule(team_format, match_mode, enrolled_pids, teams_created, court_picks, rounds_count):
        fixtures, fixture_order, num_courts = [], 1, max(1, len(court_picks))
        if team_format == "FIXED_TEAMS":
            t_list = [dict(t) for t in teams_created]
            if len(t_list) % 2 != 0: t_list.append({"p1": None, "p2": None, "is_bye": True})
            for r_cycle in range(int(rounds_count)):
                for r_idx in range(len(t_list) - 1):
                    round_num = (r_cycle * (len(t_list) - 1)) + (r_idx + 1)
                    round_pairings = [(t_list[i], t_list[len(t_list) - 1 - i]) for i in range(len(t_list) // 2) if not t_list[i].get("is_bye") and not t_list[len(t_list) - 1 - i].get("is_bye")]
                    for m_idx, (ta, tb) in enumerate(round_pairings):
                        fixtures.append({"round_number": round_num, "court_id": court_picks[m_idx % num_courts], "match_order": fixture_order, "team_a_p1_id": ta["p1"], "team_a_p2_id": ta["p2"], "team_b_p1_id": tb["p1"], "team_b_p2_id": tb["p2"], "group_id": "A"})
                        fixture_order += 1
                    t_list = [t_list[0]] + [t_list[-1]] + t_list[1:-1]
        elif team_format == "ROTATING_TEAMS" and match_mode == "DOUBLES":
            n_players = len(enrolled_pids)
            if n_players < 4: return []
            matches_played = {p: 0 for p in enrolled_pids}
            sat_out_last = {p: False for p in enrolled_pids}
            partner_matrix = {p1: {p2: 0 for p2 in enrolled_pids} for p1 in enrolled_pids}
            max_courts = max(1, min(num_courts, n_players // 4))

            for round_num in range(1, int(rounds_count) + 1):
                sorted_pids = sorted(enrolled_pids, key=lambda pid: (0 if sat_out_last[pid] else 1, matches_played[pid], pid))
                active_players = sorted_pids[:(max_courts * 4)]
                for p in enrolled_pids:
                    sat_out_last[p] = (p not in active_players)
                    if p in active_players: matches_played[p] += 1
                court_pool = list(active_players)
                for c_idx in range(max_courts):
                    if len(court_pool) < 4: break
                    m_players, court_pool = court_pool[:4], court_pool[4:]
                    combos = [((m_players[0], m_players[1]), (m_players[2], m_players[3])), ((m_players[0], m_players[2]), (m_players[1], m_players[3])), ((m_players[0], m_players[3]), (m_players[1], m_players[2]))]
                    (ta_p1, ta_p2), (tb_p1, tb_p2) = min(combos, key=lambda c: partner_matrix[c[0][0]][c[0][1]] + partner_matrix[c[1][0]][c[1][1]])
                    partner_matrix[ta_p1][ta_p2] += 1; partner_matrix[ta_p2][ta_p1] += 1; partner_matrix[tb_p1][tb_p2] += 1; partner_matrix[tb_p2][tb_p1] += 1
                    fixtures.append({"round_number": round_num, "court_id": court_picks[c_idx % num_courts], "match_order": fixture_order, "team_a_p1_id": ta_p1, "team_a_p2_id": ta_p2, "team_b_p1_id": tb_p1, "team_b_p2_id": tb_p2, "group_id": "A"})
                    fixture_order += 1
        return fixtures

    @staticmethod
    def generate_mexicano_round(standings_sorted_pids, court_picks, current_round, start_match_order):
        fixtures = []
        fixture_order = start_match_order
        num_courts = max(1, len(court_picks))
        max_courts = max(1, min(num_courts, len(standings_sorted_pids) // 4))
        court_pool = list(standings_sorted_pids[:(max_courts * 4)])
        
        for c_idx in range(max_courts):
            if len(court_pool) < 4: break
            m_players, court_pool = court_pool[:4], court_pool[4:]
            fixtures.append({"round_number": current_round, "court_id": court_picks[c_idx % num_courts], "match_order": fixture_order, "team_a_p1_id": m_players[0], "team_a_p2_id": m_players[3], "team_b_p1_id": m_players[1], "team_b_p2_id": m_players[2], "group_id": "A"})
            fixture_order += 1
        return fixtures

def build_pdf_document():
    if not REPORTLAB_AVAILABLE: return None
    buf = io.BytesIO()
    doc = SimpleDocTemplate(buf, pagesize=letter, leftMargin=36, rightMargin=36, topMargin=48, bottomMargin=48)
    styles = getSampleStyleSheet()
    title_style = ParagraphStyle('DocTitle', fontName='Helvetica-Bold', fontSize=18, leading=22, textColor=colors.HexColor('#0284c7'), spaceAfter=4)
    subtitle_style = ParagraphStyle('DocSub', fontName='Helvetica-Bold', fontSize=9, leading=13, textColor=colors.HexColor('#0f172a'), spaceAfter=8)
    h1_style = ParagraphStyle('SecH1', fontName='Helvetica-Bold', fontSize=11, leading=15, textColor=colors.HexColor('#0369a1'), spaceBefore=14, spaceAfter=6, keepWithNext=True)
    h2_style = ParagraphStyle('SecH2', fontName='Helvetica-Bold', fontSize=9, leading=13, textColor=colors.HexColor('#0f172a'), spaceBefore=10, spaceAfter=4, keepWithNext=True)
    body_style = ParagraphStyle('BodyDark', fontName='Helvetica', fontSize=7.6, leading=11, textColor=colors.HexColor('#1e293b'), spaceAfter=5)
    code_style = ParagraphStyle('CodeSnippet', fontName='Courier', fontSize=6.8, leading=9, textColor=colors.HexColor('#0369a1'), backColor=colors.HexColor('#f8fafc'), borderPadding=3, spaceAfter=4)
    th_style = ParagraphStyle('THStyle', fontName='Helvetica-Bold', fontSize=7, leading=9, textColor=colors.white)
    td_style = ParagraphStyle('TDStyle', fontName='Helvetica', fontSize=6.6, leading=8.5, textColor=colors.HexColor('#0f172a'))

    story = []
    story.append(Paragraph("RYFT ENGINE PRIMARY V.16.4", title_style))
    story.append(Paragraph("Master Specification, Algorithmic Governance Matrix & System Blueprint", subtitle_style))
    story.append(Paragraph("Dual-Engine Architecture: Einstein (Micro Physics) & Hawking (Macro Diffusion & Supervised Sandbox)", body_style))
    story.append(HRFlowable(width="100%", thickness=1.5, color=colors.HexColor('#0284c7'), spaceAfter=10))

    story.append(Paragraph("SECTION 1: EXECUTIVE SUMMARY & TRUST BLUEPRINT", h1_style))
    story.append(Paragraph("The RYFT V.16.4 Engine is a continuous, deterministic rating architecture designed for modern padel and pickleball. It eliminates the systemic failures of legacy rating systems (freeze-outs, unearned novice carry, smurfing, and private collusion) through asymmetric responsibility modeling, performance interpolation, and topological diffusion.", body_style))

    story.append(Spacer(1, 4))
    story.append(Paragraph("SECTION 2: MASTER DATABASE SCHEMA", h1_style))
    db_rows = [
        [Paragraph("Table Name", th_style), Paragraph("Primary Key", th_style), Paragraph("Architectural Role", th_style)],
        [Paragraph("players", td_style), Paragraph("player_id (UUID)", td_style), Paragraph("Master Player Profile (MMR, Peaks, Accuracy, Tier, Consecutive Losses)", td_style)],
        [Paragraph("venues", td_style), Paragraph("venue_id (UUID)", td_style), Paragraph("Club Facilities & Bridge Counters", td_style)],
        [Paragraph("locations", td_style), Paragraph("location_id (Code)", td_style), Paragraph("Geospatial Hierarchy & Hawking Offsets", td_style)],
        [Paragraph("matches", td_style), Paragraph("match_id (Code)", td_style), Paragraph("Master Transaction Ledger (Deltas, Multipliers)", td_style)],
        [Paragraph("sessions", td_style), Paragraph("session_id (Code)", td_style), Paragraph("Multi-Match Session Workflows", td_style)],
        [Paragraph("global_config", td_style), Paragraph("param_key", td_style), Paragraph("52-Parameter Live Governance Registry", td_style)]
    ]
    t_db = Table(db_rows, colWidths=[100, 100, 250])
    t_db.setStyle(TableStyle([('BACKGROUND', (0,0), (-1,0), colors.HexColor('#0f172a')), ('GRID', (0,0), (-1,-1), 0.5, colors.HexColor('#cbd5e1')), ('VALIGN', (0,0), (-1,-1), 'TOP'), ('ROWBACKGROUNDS', (0,1), (-1,-1), [colors.white, colors.HexColor('#f8fafc')])]))
    story.append(t_db)

    story.append(Spacer(1, 6))
    story.append(Paragraph("SECTION 3: 25-BIT ALGORITHMIC BIBLE & FORMULAS", h1_style))
    bits_data = [
        ("Bit 4: Power-Mean Doubles MMR", "R_Team = ((R1^3.0 + R2^3.0) / 2)^(1/3.0)", "Cubic power-mean gives 70/30 anchor weighting bias."),
        ("Bit 5: Logistic Win Probability", "E_A = 1 / (1 + 10^((R_TeamB - R_TeamA) / 2.0))", "Calculates statistical win odds bounded (0.0, 1.0)."),
        ("Bit 6: Game Margin & Inversion Clamp", "S_margin = 0.80 + 0.40 * ((GW_eff - GL) / Total_Games)", "Clamps games to resolve multi-set tiebreak inversions."),
        ("Bit 10: Option A Asymmetric Ice-Out", "Defeat Gap>=2.0 => D_D=0.05 | Win Gap>=2.5 => D_D=0.25", "Slashes anchor loss by up to 95% on freeze-outs."),
        ("Bit 11: Decoupled Bayesian RD Contraction", "RD_new = max(30.0, sqrt(1 / (1/RD^2 + Variance)))", "Buffers uncertainty contraction via cohort Omega factor."),
        ("Bit 12: Performance Interpolation", "Delta = (R_perf - R) * Alpha", "Rightsizes unrated smurfs directly to true skill in 3-5 matches."),
        ("Bit 13/14: Unordered Pod & Session Caps", "Rolling Cap: 0.150 Casual | 0.300 Session", "Prevents farming by clamping the sum of all deltas in a session."),
        ("Bit 15: Point-In-Time Tournament Desktop", "Delta_additive = (R_past_perf - R_past) * 1.15", "Asynchronous ingestion calculating deltas via historical timestamps."),
        ("Bit 22: Tikhonov Damping & Affine Diffusion", "W_conf = K / (K + 3.0)", "Scales Hawking macro offsets by bridge traveler count (K)."),
        ("Bit 23: Hysteresis Soft Floor", "Buffer = 0.050 | display_rating remains pinned if losses < 3", "Decouples public ratings from minor daily variance drops.")
    ]
    for bit_title, bit_formula, bit_desc in bits_data:
        story.append(Paragraph(f"<b>{bit_title}</b>", h2_style))
        story.append(Paragraph(bit_formula, code_style))
        story.append(Paragraph(bit_desc, body_style))

    doc.build(story)
    buf.seek(0)
    return buf.read()

# ==============================================================================
# 4. GLOBAL UI & SIDEBAR INITIALIZATION
# ==============================================================================
st.set_page_config(page_title="RYFT Engine V.16 Master", layout="wide")
st.sidebar.markdown("""<div style="text-align: center; padding: 10px 0 15px 0;"><svg width="220" height="55" viewBox="0 0 400 100" xmlns="http://www.w3.org/2000/svg"><defs><linearGradient id="ryftBlue" x1="0%" y1="0%" x2="100%" y2="100%"><stop offset="0%" style="stop-color:#0284c7;stop-opacity:1" /><stop offset="100%" style="stop-color:#1d4ed8;stop-opacity:1" /></linearGradient></defs><text x="15" y="75" font-family="-apple-system, BlinkMacSystemFont, sans-serif" font-size="82" font-weight="900" font-style="italic" fill="url(#ryftBlue)" letter-spacing="-3">RYFT</text><rect x="225" y="28" width="80" height="30" rx="6" fill="#0f172a" /><text x="238" y="50" font-family="monospace" font-size="18" font-weight="700" fill="#38bdf8">V.16.4</text></svg></div>""", unsafe_allow_html=True)

nav = st.sidebar.radio("Navigation Console", [
    "📊 The Dashboard",
    "🎾 Log Matches",
    "🗓️ Club Sessions & Mixers",
    "🧠 Session Logic (V16.2 PROD)",
    "🏆 Tournament Desk (Delayed)",
    "📜 Historical Matches",
    "👥 Player Roster & Calibration",
    "🏢 Venues & Regions",
    "🌐 Hawking Engine",
    "⚙️ Global Config",
    "📄 RYFT Documentation"
])

# ==============================================================================
# 5. CONSOLE TAB ROUTING
# ==============================================================================
if nav == "📊 The Dashboard":
    st.title("System Command Center & Macro Health")
    conn = get_db_connection()
    m1, m2, m3, m4, m5, m6 = st.columns(6)
    m1.metric("Active Players", conn.execute("SELECT COUNT(*) FROM players WHERE calibration_tier != 'INACTIVE'").fetchone()[0])
    m2.metric("Matches", conn.execute("SELECT COUNT(*) FROM matches").fetchone()[0])
    m3.metric("Sessions", conn.execute("SELECT COUNT(*) FROM sessions").fetchone()[0])
    m4.metric("Venues", conn.execute("SELECT COUNT(*) FROM venues WHERE is_active = 1").fetchone()[0])
    m5.metric("Cities", conn.execute("SELECT COUNT(*) FROM locations WHERE location_type = 'CITY'").fetchone()[0])
    m6.metric("Countries", conn.execute("SELECT COUNT(*) FROM locations WHERE location_type = 'COUNTRY'").fetchone()[0])
    conn.close()
    st.markdown("---")
    with st.expander("💾 Database Snapshot Backup & Restore", expanded=True):
        col_b1, col_b2 = st.columns(2)
        with col_b1:
            st.markdown("#### 📥 Backup Database Snapshot")
            if os.path.exists(DB_FILE):
                try: st.download_button("⬇️ Download System Snapshot (.db)", data=export_db_bytes(), file_name=f"RYFT_V16_Backup_{datetime.now().strftime('%Y%m%d_%H%M%S')}.db", mime="application/x-sqlite3", use_container_width=True)
                except Exception as ex: st.error(f"Error preparing snapshot: {ex}")
        with col_b2:
            st.markdown("#### 📤 Upload Saved State")
            up_db = st.file_uploader("Select .db file", type=["db", "sqlite", "sqlite3"])
            if up_db and st.button("🚨 Restore Entire System", type="primary", use_container_width=True):
                try: restore_db_from_bytes(up_db.getbuffer()); st.success("✅ System Restored!"); st.rerun()
                except Exception as err: st.error(f"Failed to restore database: {str(err)}")
    with st.expander("🚨 Advanced System Resets", expanded=False):
        r_c1, r_c2, r_c3 = st.columns(3)
        res_p = r_c1.checkbox("Reset All Players to Initial Rating")
        res_m = r_c2.checkbox("Delete Match History")
        res_n = r_c3.checkbox("💣 Clean Slate (Erase All Test Data)")
        if st.button("Execute Checked Resets", type="secondary"):
            conn = get_db_connection()
            if res_n:
                for tbl in ["match_logs", "matches", "session_matches", "session_rosters", "tourney_matches", "tournaments", "sessions", "players", "venues", "locations", "progression_speed_rules", "config_changelog", "player_changelog"]: conn.execute(f"DELETE FROM {tbl};")
                st.warning("Database completely wiped.")
            else:
                if res_m:
                    conn.execute("DELETE FROM match_logs;"); conn.execute("DELETE FROM matches;"); conn.execute("DELETE FROM session_matches;"); conn.execute("DELETE FROM session_rosters;"); conn.execute("DELETE FROM sessions;"); conn.execute("DELETE FROM tourney_matches;"); conn.execute("DELETE FROM tournaments;")
                    st.warning("Match history erased.")
                if res_p:
                    conn.execute("UPDATE players SET latent_mmr = initial_rating, display_rating = initial_rating, rating_deviation = 350.0, verified_matches_count = 0, unique_opponents_count = 0, rating_accuracy_pct = 0.0, calibration_tier = 'PROVISIONAL', is_provisional = 1")
                    st.warning("Player ratings reset.")
            conn.commit(); conn.close(); st.rerun()

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

    c_s1, c_s2, c_s3 = st.columns(3)
    match_date = c_s1.date_input("Match Date", value=date.today())
    match_time = c_s2.time_input("Match Time", value=datetime.now().time())
    ven_sel = c_s3.selectbox("Venue Facility", list(v_dict.keys()) if v_dict else ["No Venues Registered"])

    c_m1, c_m2, c_m3 = st.columns([1.5, 2, 1.5])
    is_singles = (c_m1.radio("Game Configuration", ["2v2 Doubles", "1v1 Singles"], horizontal=True) == "1v1 Singles")
    fmt_sel = c_m2.selectbox("Official Scoring Format", list(f_dict.keys()) if f_dict else ["No Formats Active"])
    is_tourney = c_m3.checkbox("🏆 Tournament Match (Uncapped + Boost)", value=False)

    col_t1, col_t2 = st.columns(2)
    with col_t1:
        st.markdown("##### 🔵 Team A")
        p1_pick = st.selectbox("Player A1 (Required)", ["-- Select --"] + list(p_dict.keys()), key="p1_sel")
        if p1_pick != "-- Select --":
            pm = p_dict[p1_pick]
            st.caption(f"**{format_pr_name(pm['display_name'], pm['is_provisional'])}** | Display: `{pm['display_rating']:.2f}` | LMMR: `{pm['latent_mmr']:.3f}` | Acc: `{pm['rating_accuracy_pct']:.1f}%` | RD: `{pm['rating_deviation']:.1f}`")
        p2_pick = st.selectbox("Player A2 (Teammate)", ["-- Select --"] + list(p_dict.keys()), key="p2_sel") if not is_singles else "-- None --"
    with col_t2:
        st.markdown("##### 🔴 Team B")
        p3_pick = st.selectbox("Player B1 (Required)", ["-- Select --"] + list(p_dict.keys()), key="p3_sel")
        if p3_pick != "-- Select --":
            pm = p_dict[p3_pick]
            st.caption(f"**{format_pr_name(pm['display_name'], pm['is_provisional'])}** | Display: `{pm['display_rating']:.2f}` | LMMR: `{pm['latent_mmr']:.3f}` | Acc: `{pm['rating_accuracy_pct']:.1f}%` | RD: `{pm['rating_deviation']:.1f}`")
        p4_pick = st.selectbox("Player B2 (Teammate)", ["-- Select --"] + list(p_dict.keys()), key="p4_sel") if not is_singles else "-- None --"

    sel_f = f_dict[fmt_sel] if f_dict else None
    sets_data, sa, sb, gw, gl = [], 0, 0, 0, 0

    if sel_f:
        cat = sel_f["category"]
        if cat == "MULTI_SET":
            s1_c1, s1_c2 = st.columns(2)
            s1a = s1_c1.number_input("Set 1: Team A", 0, 7, 6, key="s1a"); s1b = s1_c2.number_input("Set 1: Team B", 0, 7, 3, key="s1b")
            sets_data.append((s1a, s1b))
            s2_c1, s2_c2 = st.columns(2)
            s2a = s2_c1.number_input("Set 2: Team A", 0, 7, 6, key="s2a"); s2b = s2_c2.number_input("Set 2: Team B", 0, 7, 4, key="s2b")
            sets_data.append((s2a, s2b))
            if sum(1 for s in sets_data if s[0]>s[1]) == 1 and sum(1 for s in sets_data if s[1]>s[0]) == 1:
                st.warning("Set 3 decider:")
                s3_c1, s3_c2 = st.columns(2)
                s3a = s3_c1.number_input("Set 3: Team A", 0, 7, 6, key="s3a"); s3b = s3_c2.number_input("Set 3: Team B", 0, 7, 4, key="s3b")
                sets_data.append((s3a, s3b))
            sa, sb = sum(1 for s in sets_data if s[0]>s[1]), sum(1 for s in sets_data if s[1]>s[0])
            gw, gl = sum(x[0] for x in sets_data), sum(x[1] for x in sets_data)
        elif cat == "RACE_GAMES":
            rg1, rg2 = st.columns(2)
            gw = rg1.number_input("Team A Games", 0, 30, sel_f["target_games"] or 6)
            gl = rg2.number_input("Team B Games", 0, 30, max(0, (sel_f["target_games"] or 6)-2))
            sa, sb = (1 if gw>gl else 0), (1 if gl>gw else 0)
            sets_data.append((gw, gl))
        elif cat in ("AMERICANO", "MEXICANO"):
            tp = sel_f["total_points"] or 24
            ap1, ap2 = st.columns(2)
            sa = ap1.number_input("Team A Points", 0, tp, tp//2); sb = ap2.number_input("Team B Points", 0, tp, tp - (tp//2))
            gw, gl = sa, sb
            sets_data.append((sa, sb))

    btn_dry, btn_save = st.columns(2)
    do_dry = btn_dry.button("🔬 Execute Dry Run Simulation", use_container_width=True)
    do_save = btn_save.button("💾 Commit Match to Database", type="primary", use_container_width=True)

    if do_dry or do_save:
        if p1_pick == "-- Select --" or p3_pick == "-- Select --" or (not is_singles and (p2_pick == "-- Select --" or p4_pick == "-- Select --")): st.error("Assign all required roster slots.")
        elif sel_f and sel_f["category"] in ("AMERICANO", "MEXICANO") and (sa + sb != sel_f["total_points"]): st.error(f"Points must sum exactly to {sel_f['total_points']}.")
        else:
            p1_obj, p3_obj = dict(p_dict[p1_pick]), dict(p_dict[p3_pick])
            p2_obj = dict(p_dict[p2_pick]) if not is_singles else None
            p4_obj = dict(p_dict[p4_pick]) if not is_singles else None
            ven_obj = dict(v_dict[ven_sel])

            sim_out = RyftV16.compute_match(p1_obj, p2_obj, p3_obj, p4_obj, sa, sb, max(gw, gl), min(gw, gl), sel_f["format_id"], ven_obj["venue_id"], is_singles, is_dry=do_dry, is_tournament=is_tourney)
            st.success(f"Match Executed! Team A Odds: {sim_out['ea']*100:.1f}% vs Team B: {(1-sim_out['ea'])*100:.1f}% | Margin: {sim_out['mov']:.4f}")

            res_cols = st.columns(2 if is_singles else 4)
            for idx, pr in enumerate(sim_out["res"]):
                with res_cols[idx]:
                    st.markdown(f"""
                    <div style="background-color: #1e293b; border: 2px solid #0284c7; border-radius: 8px; padding: 12px; margin-bottom: 8px; color: #f8fafc;">
                        <h4 style="margin:0 0 8px 0; color:#38bdf8;">{pr['name']}</h4>
                        <div style="color: #cbd5e1; font-size: 0.9em; line-height: 1.6;">
                            Pre MMR: <code style="color: #38bdf8; background: #0f172a;">{pr['pre_r']:.3f}</code><br/>
                            Post MMR: <code style="color: #38bdf8; background: #0f172a;">{pr['post_r']:.3f}</code><br/>
                            Delta: <span style="font-size:1.1em; font-weight:bold; color:{'#4ade80' if pr['delta']>=0 else '#f87171'}">{pr['delta']:+.4f}</span><br/>
                            RD: <code style="color: #e2e8f0; background: #0f172a;">{pr['pre_rd']:.1f} ➔ {pr['post_rd']:.1f}</code><br/>
                            Acc: <code style="color: #e2e8f0; background: #0f172a;">{pr['pre_acc']:.1f}% ➔ {pr['acc']:.1f}%</code><br/>
                            Tier: <span style="background: #0f172a; padding: 2px 6px; border-radius: 4px; font-weight: bold; color: #38bdf8;">{pr['pre_tier']} ➔ {pr['tier']}</span>
                        </div>
                    </div>
                    """, unsafe_allow_html=True)
                    if pr["flags"]: st.caption("⚡ " + " | ".join(pr["flags"]))

            if do_save:
                conn = get_db_connection()
                m_id = f"M_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
                ts = f"{match_date}T{match_time.strftime('%H:%M:%S')}Z"
                is_v_b = 1 if (p1_obj.get("home_venue_id") != ven_obj["venue_id"] and p1_obj.get("home_city_id") == ven_obj["city_id"]) else 0
                is_c_b = 1 if (p1_obj.get("home_city_id") != ven_obj["city_id"] and p1_obj.get("home_country_code") == ven_obj["country_code"]) else 0
                is_co_b = 1 if (p1_obj.get("home_country_code") != ven_obj["country_code"]) else 0

                all_guardrails = []
                for pr in sim_out["res"]: all_guardrails.extend(pr["flags"])

                conn.execute('''INSERT INTO matches (match_id, venue_id, format_id, is_singles, is_tournament, is_venue_bridge, is_city_bridge, is_country_bridge, team_a_p1_id, team_a_p2_id, team_b_p1_id, team_b_p2_id, score_team_a, score_team_b, set_scores_json, games_winner, games_loser, pre_rating_a, pre_rating_b, win_expectancy_a, applied_m_c, applied_s_margin, delta_r_p1, delta_r_p2, delta_r_p3, delta_r_p4, guardrails_summary, match_timestamp) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)''', (m_id, ven_obj["venue_id"], sel_f["format_id"], 1 if is_singles else 0, 1 if is_tourney else 0, is_v_b, is_c_b, is_co_b, p1_obj["player_id"], p2_obj["player_id"] if not is_singles else None, p3_obj["player_id"], p4_obj["player_id"] if not is_singles else None, sa, sb, json.dumps(sets_data), max(gw, gl), min(gw, gl), sim_out["ta_r"], sim_out["tb_r"], sim_out["ea"], sel_f["mc_weight"], sim_out["mov"], sim_out["res"][0]["delta"], sim_out["res"][2]["delta"] if not is_singles else 0.0, sim_out["res"][1]["delta"], sim_out["res"][3]["delta"] if not is_singles else 0.0, json.dumps(all_guardrails), ts))
                for pr in sim_out["res"]:
                    conn.execute('''UPDATE players SET latent_mmr=?, display_rating=?, rating_deviation=?, rating_accuracy_pct=?, accuracy_s_rd=?, accuracy_s_matches=?, accuracy_s_diversity=?, calibration_tier=?, is_provisional=?, consecutive_losses=?, rolling_90d_peak=max(rolling_90d_peak, ?), rolling_180d_peak=max(rolling_180d_peak, ?), rolling_365d_peak=max(rolling_365d_peak, ?), last_match_time=? WHERE player_id=?''', (pr["post_r"], pr["post_disp"], pr["post_rd"], pr["acc"], pr["a_rd"], pr["a_m"], pr["a_d"], pr["tier"], pr["prov"], pr["c_loss"], pr["post_r"], pr["post_r"], pr["post_r"], ts, pr["pid"]))
                    conn.execute('''INSERT INTO match_logs (log_id, match_id, player_id, pre_latent_mmr, post_latent_mmr, pre_display_rating, post_display_rating, pre_rd, post_rd, pre_accuracy_pct, post_accuracy_pct, delta_r, guardrails_triggered, logged_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)''', (f"L_{pr['pid']}_{m_id}", m_id, pr["pid"], pr["pre_r"], pr["post_r"], pr["pre_disp"], pr["post_disp"], pr["pre_rd"], pr["post_rd"], pr["pre_acc"], pr["acc"], pr["delta"], json.dumps(pr["flags"]), ts))

                conn.execute("UPDATE venues SET total_matches_played = total_matches_played + 1 WHERE venue_id = ?", (ven_obj["venue_id"],))
                if is_c_b: conn.execute("UPDATE venues SET city_bridge_matches_count = city_bridge_matches_count + 1 WHERE venue_id = ?", (ven_obj["venue_id"],))
                if is_co_b: conn.execute("UPDATE venues SET country_bridge_matches_count = country_bridge_matches_count + 1 WHERE venue_id = ?", (ven_obj["venue_id"],))
                conn.execute("UPDATE locations SET total_matches_played = total_matches_played + 1 WHERE location_id = ?", (ven_obj["city_id"],))

                for pid in [p1_obj["player_id"], p3_obj["player_id"]] + ([p2_obj["player_id"], p4_obj["player_id"]] if not is_singles else []):
                    if pid: RyftV16.sync_player_aggregates(pid, conn=conn)

                conn.commit(); conn.close(); st.balloons(); st.success("✅ Match successfully committed!"); st.rerun()

elif nav == "🗓️ Club Sessions & Mixers":
    st.title("Sessions & Event Traffic Controller")
    st.info("Please use the upgraded **🧠 Session Logic (V16.2 PROD)** tab for multi-group stage management.")

elif nav == "🧠 Session Logic (V16.2 PROD)":
    st.title("Session Logic Engine (V16.2 PROD)")
    st.caption("Multi-stage atomic scheduling, Mexicano phase-gating, and true chronological evaluation.")

    conn = get_db_connection()
    venues = conn.execute("SELECT venue_id, venue_name, court_count, city_id, country_code FROM venues WHERE is_active = 1").fetchall()
    players = conn.execute("SELECT * FROM players WHERE calibration_tier != 'INACTIVE' ORDER BY display_name").fetchall()
    v_dict = {v["venue_name"]: dict(v) for v in venues}
    p_dict = {format_pr_name(p["display_name"], p["is_provisional"]): p["player_id"] for p in players}
    p_meta = {p["player_id"]: dict(p) for p in players}

    mode = st.radio("Navigation", ["➕ Create Session", "🎮 Active Sessions Hub"], horizontal=True)

    if mode == "➕ Create Session":
        st.subheader("1. Session Gateway")
        cs1, cs2 = st.columns(2)
        s_title = cs1.text_input("Session Title", value="Pro-Am Round Robin")
        s_ven = cs2.selectbox("Hosting Venue", list(v_dict.keys()) if v_dict else ["None"])
        
        cs3, cs4, cs5 = st.columns(3)
        s_date = cs3.date_input("Event Date", value=date.today())
        s_start = cs4.time_input("Start Time", value=time(9, 0))
        s_end = cs5.time_input("End Time", value=time(11, 0))
        s_date_str, s_start_str, s_end_str = str(s_date), s_start.strftime("%H:%M"), s_end.strftime("%H:%M")

        avail_courts = v_dict[s_ven]["court_count"] if s_ven in v_dict else 1
        all_court_labels = [f"Court {i+1}" for i in range(avail_courts)]
        overlap_sessions = conn.execute("SELECT session_title, court_ids_json, start_time, end_time FROM sessions WHERE venue_id = ? AND session_date = ? AND session_status != 'CANCELLED'", (v_dict[s_ven]["venue_id"] if s_ven in v_dict else "", s_date_str)).fetchall()
        booked_courts = set()
        for osess in overlap_sessions:
            if s_start_str < osess["end_time"] and s_end_str > osess["start_time"]: booked_courts.update(json.loads(osess["court_ids_json"]))

        available_court_picks = [c for c in all_court_labels if c not in booked_courts]
        court_picks = st.multiselect("Select Dedicated Courts", available_court_picks, default=available_court_picks[:min(2, len(available_court_picks))])

        st.markdown("#### Configuration")
        cs6, cs7, cs8 = st.columns(3)
        s_mode = cs6.selectbox("Lineup Mode", ["DOUBLES", "SINGLES"])
        s_team = cs7.selectbox("Team Mechanics", ["FIXED_TEAMS", "ROTATING_TEAMS"] if s_mode == "DOUBLES" else ["SINGLES"])
        is_tourney_sess = cs8.checkbox("🏆 Official Tournament Session", value=False)
        
        if s_team == "ROTATING_TEAMS": compat_formats = conn.execute("SELECT format_id, format_name, category FROM match_formats WHERE category IN ('AMERICANO', 'MEXICANO', 'RACE_GAMES') AND is_active = 1").fetchall()
        elif s_team == "FIXED_TEAMS": compat_formats = conn.execute("SELECT format_id, format_name, category FROM match_formats WHERE category IN ('MULTI_SET', 'RACE_GAMES') AND is_active = 1").fetchall()
        else: compat_formats = conn.execute("SELECT format_id, format_name, category FROM match_formats WHERE category IN ('RACE_GAMES', 'MULTI_SET') AND is_active = 1").fetchall()
        
        f_compat_dict = {f["format_name"]: dict(f) for f in compat_formats}
        s_fmt_name = st.selectbox("Scoring Ruleset", list(f_compat_dict.keys()) if f_compat_dict else ["None Compatible"])
        sel_format_obj = f_compat_dict[s_fmt_name] if s_fmt_name in f_compat_dict else None

        if s_team == "ROTATING_TEAMS":
            s_struct = "ROTATION_ROUNDS"
            s_rounds = st.number_input("Rotation Rounds (Cycles)", min_value=1, max_value=20, value=7 if sel_format_obj and sel_format_obj['category'] in ('AMERICANO', 'MEXICANO') else 3)
            st.info("Rotations enforce equal matches per round and max-1 consecutive sit-out rule.")
        else:
            cs9, cs10 = st.columns(2)
            s_struct = cs9.selectbox("Bracket Type", ["ROUND_ROBIN", "KNOCKOUT", "HYBRID"])
            s_rounds = cs10.number_input("Pool Rounds", min_value=1, max_value=20, value=3)

        st.markdown("#### Roster Setup")
        teams_created = []
        enrolled_pids = []

        if s_team == "FIXED_TEAMS":
            num_teams = st.number_input("How many teams?", min_value=2, max_value=32, value=4, step=1)
            if s_struct == "ROUND_ROBIN" and num_teams > 6:
                st.warning("⚠️ SCHEDULE OPTIMIZATION NOTICE: >6 Entries Detected. Consider enabling 'Multi-Group Stage'.")
            p_names = list(p_dict.keys())
            for t_idx in range(int(num_teams)):
                tc1, tc2 = st.columns(2)
                tp1 = tc1.selectbox(f"Team {t_idx+1} Player 1", ["-- Select --"] + p_names, key=f"t_p1_{t_idx}")
                tp2 = tc2.selectbox(f"Team {t_idx+1} Player 2", ["-- Select --"] + p_names, key=f"t_p2_{t_idx}")
                if tp1 != "-- Select --" and tp2 != "-- Select --":
                    teams_created.append({"p1": p_dict[tp1], "p2": p_dict[tp2]})
                    enrolled_pids.extend([p_dict[tp1], p_dict[tp2]])
        else:
            enrolled_names = st.multiselect("Enroll Registered Players (Rotating Roster)", list(p_dict.keys()))
            enrolled_pids = [p_dict[p] for p in enrolled_names]
            if len(enrolled_pids) > 0:
                est_m = (len(enrolled_pids) * int(s_rounds)) // 4
                st.info(f"ℹ️ **SESSION ESTIMATOR:** {len(enrolled_pids)} players on {len(court_picks)} courts. Matches in 1 cycle: {est_m}.")

        if st.button("🚀 Create Session Stage 1", type="primary"):
            min_req = 4 if s_mode == "DOUBLES" else 2
            if len(enrolled_pids) < min_req: st.error(f"❌ Requires {min_req} participants.")
            elif not court_picks: st.error("❌ Select a court.")
            elif not sel_format_obj: st.error("❌ Valid scoring format required.")
            else:
                s_id = f"SESS_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
                conn.execute("""INSERT INTO sessions (session_id, venue_id, session_title, session_date, start_time, end_time, match_mode, team_format, format_id, tourney_structure, is_tournament, court_ids_json, enrolled_player_ids, teams_json, player_count, total_rounds, current_round, created_at, current_stage, lineup_mode) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1, ?, 'CONFIG', ?)""", (s_id, v_dict[s_ven]["venue_id"], s_title, s_date_str, s_start_str, s_end_str, s_mode, s_team, sel_format_obj["format_id"], s_struct, 1 if is_tourney_sess else 0, json.dumps(court_picks), json.dumps(enrolled_pids), json.dumps(teams_created), len(enrolled_pids), int(s_rounds), datetime.now(timezone.utc).isoformat(), s_mode))
                for pid in enrolled_pids:
                    p_info = p_meta[pid]
                    conn.execute("""INSERT INTO session_rosters (roster_id, session_id, entity_type, player_id_p1, display_label, initial_mmr, initial_rd) VALUES (?, ?, ?, ?, ?, ?, ?)""", (f"R_{s_id}_{pid}", s_id, "INDIVIDUAL", pid, p_info["display_name"], p_info["latent_mmr"], p_info["rating_deviation"]))

                if sel_format_obj and sel_format_obj["category"] == "MEXICANO":
                    fixtures = SessionLogicEngine.generate_mexicano_round(enrolled_pids, court_picks, 1, 1)
                else:
                    fixtures = SessionLogicEngine.generate_schedule(s_team, s_mode, enrolled_pids, teams_created, court_picks, s_rounds)

                for f in fixtures:
                    sm_id = f"SM_{s_id}_{f['round_number']}_{f['match_order']}"
                    conn.execute("""INSERT INTO session_matches (session_match_id, session_id, round_number, court_id, match_order, team_a_p1_id, team_a_p2_id, team_b_p1_id, team_b_p2_id, match_status, stage) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'SCHEDULED', 'GROUP_STAGE')""", (sm_id, s_id, f["round_number"], f["court_id"], f["match_order"], f["team_a_p1_id"], f["team_a_p2_id"], f["team_b_p1_id"], f["team_b_p2_id"]))

                conn.commit(); st.success("Session initialized!"); st.rerun()

    elif mode == "🎮 Active Sessions Hub":
        s_tab1, s_tab2, s_tab3 = st.tabs(["⏳ Upcoming", "🔴 Live", "✅ Completed"])

        def render_session_workspace(s_data):
            s_id = s_data["session_id"]
            enrolled_ids = json.loads(s_data["enrolled_player_ids"])
            crts = json.loads(s_data["court_ids_json"])
            id_to_name = {p["player_id"]: format_pr_name(p["display_name"], p["is_provisional"]) for p in players}
            fmt_info = conn.execute("SELECT category, target_games, total_points, format_name FROM match_formats WHERE format_id = ?", (s_data['format_id'],)).fetchone()
            fmt_category = fmt_info["category"] if fmt_info else s_data.get("fmt_cat", "RACE_GAMES")
            rosters = conn.execute("SELECT * FROM session_rosters WHERE session_id = ?", (s_id,)).fetchall()
            checked_in_ids = [r["player_id_p1"] for r in rosters if r["is_checked_in"]]

            hdr1, hdr2 = st.columns([4, 1])
            hdr1.markdown(f"""<div style="background-color: #f8fafc; padding: 12px; border-radius: 8px; border-left: 5px solid #0284c7; margin-bottom:12px;"><h3 style="margin:0; color:#0f172a;">{s_data['session_title']}</h3><strong>Format:</strong> {s_data['format_name']} | <strong>Mode:</strong> {s_data['team_format']} | <strong>Tournament:</strong> {'YES' if s_data.get('is_tournament', 0) else 'NO'}</div>""", unsafe_allow_html=True)
            with hdr2:
                if st.button("🗑️ Discard", key=f"disc_{s_id}"):
                    conn.execute("DELETE FROM session_rosters WHERE session_id = ?", (s_id,)); conn.execute("DELETE FROM session_matches WHERE session_id = ?", (s_id,)); conn.execute("DELETE FROM sessions WHERE session_id = ?", (s_id,)); conn.commit(); st.rerun()

            sub_nav = st.radio("Stage", ["📋 Check-In Gate", "🏟️ Live Court Hub", "📊 Standings & Atomic Commit"], key=f"snav_{s_id}", horizontal=True)

            if sub_nav == "📋 Check-In Gate":
                st.metric("Ready", f"{len(checked_in_ids)} of {len(enrolled_ids)}")
                btn_ci_all, btn_clr_all = st.columns(2)
                if btn_ci_all.button("✅ Check-In All", use_container_width=True, key=f"btn_all_{s_id}"):
                    conn.execute("UPDATE session_rosters SET is_checked_in=1 WHERE session_id=?", (s_id,)); conn.execute("UPDATE sessions SET active_checked_in_count=?, session_status='LIVE', current_stage='CHECKIN' WHERE session_id=?", (len(enrolled_ids), s_id)); conn.commit(); st.rerun()
                if btn_clr_all.button("❌ Clear All", use_container_width=True, key=f"btn_clr_{s_id}"):
                    conn.execute("UPDATE session_rosters SET is_checked_in=0 WHERE session_id=?", (s_id,)); conn.execute("UPDATE sessions SET active_checked_in_count=0, session_status='CONFIG' WHERE session_id=?", (s_id,)); conn.commit(); st.rerun()

                with st.form(f"ci_form_{s_id}"):
                    updated_checkins = []
                    for r in rosters:
                        if st.checkbox(f"✅ {id_to_name.get(r['player_id_p1'], r['player_id_p1'])}", value=bool(r["is_checked_in"]), key=f"chk_{s_id}_{r['player_id_p1']}"): updated_checkins.append(r["player_id_p1"])
                    if st.form_submit_button("Save Gates"):
                        conn.execute("UPDATE session_rosters SET is_checked_in=0 WHERE session_id=?", (s_id,))
                        for pid in updated_checkins: conn.execute("UPDATE session_rosters SET is_checked_in=1 WHERE session_id=? AND player_id_p1=?", (s_id, pid))
                        conn.execute("UPDATE sessions SET active_checked_in_count=?, session_status=? WHERE session_id=?", (len(updated_checkins), 'LIVE' if len(updated_checkins) > 0 else 'CONFIG', s_id))
                        conn.commit(); st.rerun()

            elif sub_nav == "🏟️ Live Court Hub":
                fixtures = conn.execute("SELECT * FROM session_matches WHERE session_id = ? ORDER BY match_order ASC, round_number ASC", (s_id,)).fetchall()
                uncompleted = [dict(m) for m in fixtures if m["match_status"] not in ("STAGED", "COMMITTED", "CANCELLED")]
                completed = [dict(m) for m in fixtures if m["match_status"] in ("STAGED", "COMMITTED", "CANCELLED")]

                def is_ready(m):
                    parts = [p for p in [m['team_a_p1_id'], m['team_a_p2_id'], m['team_b_p1_id'], m['team_b_p2_id']] if p]
                    missing = [p for p in parts if p not in checked_in_ids]
                    return len(missing) == 0, [id_to_name.get(x, x) for x in missing]

                if fmt_category == "MEXICANO":
                    curr_rnd = s_data.get("current_round", 1)
                    curr_rnd_matches = [m for m in uncompleted if m["round_number"] == curr_rnd]
                    if len(curr_rnd_matches) == 0 and curr_rnd < s_data["total_rounds"]:
                        st.info(f"✅ Round {curr_rnd} complete. Ready for Mexicano Phase-Gated Pairings.")
                        if st.button(f"Generate Next Mexicano Round (Round {curr_rnd + 1})", type="primary"):
                            standings = {pid: {"Points Won": 0, "Diff": 0} for pid in enrolled_ids}
                            for sm in completed:
                                if sm["match_status"] != "CANCELLED":
                                    for pid in [sm["team_a_p1_id"], sm["team_a_p2_id"]]:
                                        if pid: standings[pid]["Points Won"] += sm["score_team_a"]; standings[pid]["Diff"] += (sm["score_team_a"] - sm["score_team_b"])
                                    for pid in [sm["team_b_p1_id"], sm["team_b_p2_id"]]:
                                        if pid: standings[pid]["Points Won"] += sm["score_team_b"]; standings[pid]["Diff"] += (sm["score_team_b"] - sm["score_team_a"])
                            sorted_pids = sorted(standings.keys(), key=lambda x: (standings[x]["Points Won"], standings[x]["Diff"]), reverse=True)
                            last_order = max([m["match_order"] for m in fixtures]) if fixtures else 0
                            new_fixtures = SessionLogicEngine.generate_mexicano_round(sorted_pids, crts, curr_rnd + 1, last_order + 1)
                            for f in new_fixtures:
                                sm_id = f"SM_{s_id}_{f['round_number']}_{f['match_order']}"
                                conn.execute("INSERT INTO session_matches (session_match_id, session_id, round_number, court_id, match_order, team_a_p1_id, team_a_p2_id, team_b_p1_id, team_b_p2_id, match_status) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'SCHEDULED')", (sm_id, s_id, f["round_number"], f["court_id"], f["match_order"], f["team_a_p1_id"], f["team_a_p2_id"], f["team_b_p1_id"], f["team_b_p2_id"]))
                            conn.execute("UPDATE sessions SET current_round = ? WHERE session_id = ?", (curr_rnd + 1, s_id))
                            conn.commit(); st.rerun()

                st.markdown(f"### ⏳ Planned Fixtures ({len(uncompleted)} remaining)")
                if uncompleted:
                    for m in uncompleted:
                        ready_flag, missing_players = is_ready(m)
                        ta_players = f"{id_to_name.get(m['team_a_p1_id'])} & {id_to_name.get(m['team_a_p2_id'])}" if m['team_a_p2_id'] else id_to_name.get(m['team_a_p1_id'])
                        tb_players = f"{id_to_name.get(m['team_b_p1_id'])} & {id_to_name.get(m['team_b_p2_id'])}" if m['team_b_p2_id'] else id_to_name.get(m['team_b_p1_id'])
                        header_label = f"{'🟢 READY' if ready_flag else '⏳ WAITING'} Match #{m['match_order']} • {m['court_id']} (Round {m['round_number']}) — {ta_players} vs {tb_players}"

                        with st.expander(header_label, expanded=ready_flag):
                            if not ready_flag: st.warning(f"Missing Check-Ins: {', '.join(missing_players)}")
                            with st.form(f"score_form_{m['session_match_id']}"):
                                sets_recorded = []
                                sa, sb, gw, gl = 0, 0, 0, 0
                                if fmt_category == "RACE_GAMES":
                                    tg = fmt_info["target_games"] or 6
                                    rg1, rg2 = st.columns(2)
                                    gw = rg1.number_input(f"Games ({ta_players})", 0, 30, m['score_team_a'] if m['score_team_a']>0 else tg, key=f"ga_{m['session_match_id']}")
                                    gl = rg2.number_input(f"Games ({tb_players})", 0, 30, m['score_team_b'] if m['score_team_b']>0 else max(0, tg-2), key=f"gb_{m['session_match_id']}")
                                    sa, sb = (1 if gw>gl else 0), (1 if gl>gw else 0)
                                    sets_recorded.append((gw, gl))
                                elif fmt_category == "MULTI_SET":
                                    s1c1, s1c2 = st.columns(2)
                                    s1a = s1c1.number_input(f"Set 1: {ta_players}", 0, 7, 6, key=f"s1a_{m['session_match_id']}"); s1b = s1c2.number_input(f"Set 1: {tb_players}", 0, 7, 3, key=f"s1b_{m['session_match_id']}")
                                    sets_recorded.append((s1a, s1b))
                                    s2c1, s2c2 = st.columns(2)
                                    s2a = s2c1.number_input(f"Set 2: {ta_players}", 0, 7, 6, key=f"s2a_{m['session_match_id']}"); s2b = s2c2.number_input(f"Set 2: {tb_players}", 0, 7, 4, key=f"s2b_{m['session_match_id']}")
                                    sets_recorded.append((s2a, s2b))
                                    if st.checkbox("Deciding Set 3", key=f"s3_chk_{m['session_match_id']}"):
                                        s3c1, s3c2 = st.columns(2)
                                        s3a = s3c1.number_input(f"Set 3: {ta_players}", 0, 7, 6, key=f"s3a_{m['session_match_id']}"); s3b = s3c2.number_input(f"Set 3: {tb_players}", 0, 7, 4, key=f"s3b_{m['session_match_id']}")
                                        sets_recorded.append((s3a, s3b))
                                    sa, sb = sum(1 for s in sets_recorded if s[0]>s[1]), sum(1 for s in sets_recorded if s[1]>s[0])
                                    gw, gl = sum(x[0] for x in sets_recorded), sum(x[1] for x in sets_recorded)
                                elif fmt_category in ("AMERICANO", "MEXICANO"):
                                    tp = fmt_info["total_points"] or 24
                                    ap1, ap2 = st.columns(2)
                                    sa = ap1.number_input(f"Points: {ta_players}", 0, 50, tp//2, key=f"pa_{m['session_match_id']}"); sb = ap2.number_input(f"Points: {tb_players}", 0, 50, tp-(tp//2), key=f"pb_{m['session_match_id']}")
                                    gw, gl = sa, sb
                                    sets_recorded.append((sa, sb))

                                m_stat = st.selectbox("Status", ["SCHEDULED", "LIVE", "STAGED", "CANCELLED"], index=["SCHEDULED", "LIVE", "STAGED", "CANCELLED"].index(m['match_status']), key=f"st_{m['session_match_id']}")
                                if st.form_submit_button("✅ Stage Score"):
                                    if fmt_category in ("AMERICANO", "MEXICANO") and (sa + sb) != fmt_info["total_points"] and m_stat == "STAGED": st.error(f"Score must total {fmt_info['total_points']} points.")
                                    else:
                                        conn.execute("UPDATE session_matches SET score_team_a=?, score_team_b=?, games_winner=?, games_loser=?, set_scores_json=?, match_status=? WHERE session_match_id=?", (sa, sb, max(gw, gl), min(gw, gl), json.dumps(sets_recorded), 'STAGED' if m_stat=='SCHEDULED' else m_stat, m['session_match_id']))
                                        conn.execute("UPDATE sessions SET session_status='LIVE', current_stage='GROUP_STAGE' WHERE session_id=?", (s_id,))
                                        conn.commit(); st.rerun()

                st.markdown("---")
                st.markdown(f"### ✅ Completed Matches ({len(completed)})")
                if completed:
                    running_state = {pid: dict(p_meta[pid]) for pid in enrolled_ids}
                    cum_deltas = {pid: 0.0 for pid in enrolled_ids}

                    for m in completed:
                        ta_players = f"{id_to_name.get(m['team_a_p1_id'])} & {id_to_name.get(m['team_a_p2_id'])}" if m['team_a_p2_id'] else id_to_name.get(m['team_a_p1_id'])
                        tb_players = f"{id_to_name.get(m['team_b_p1_id'])} & {id_to_name.get(m['team_b_p2_id'])}" if m['team_b_p2_id'] else id_to_name.get(m['team_b_p1_id'])
                        sets_json = json.loads(m['set_scores_json']) if m['set_scores_json'] else []
                        if fmt_category == "MULTI_SET": score_str = ", ".join([f"{s[0]}-{s[1]}" for s in sets_json]) if sets_json else f"{m['score_team_a']}-{m['score_team_b']}"
                        elif fmt_category == "RACE_GAMES": score_str = f"{sets_json[0][0]}-{sets_json[0][1]}" if sets_json else f"{m['games_winner']}-{m['games_loser']}"
                        else: score_str = f"{m['score_team_a']} - {m['score_team_b']}"

                        with st.expander(f"✓ Match #{m['match_order']} • {m['court_id']} — {ta_players} [{'CANCELLED' if m['match_status']=='CANCELLED' else score_str}] {tb_players}", expanded=False):
                            if m['match_status'] != "CANCELLED":
                                p1_d = {**running_state[m["team_a_p1_id"]], "player_id": m["team_a_p1_id"]}
                                p3_d = {**running_state[m["team_b_p1_id"]], "player_id": m["team_b_p1_id"]}
                                p2_d = {**running_state[m["team_a_p2_id"]], "player_id": m["team_a_p2_id"]} if m["team_a_p2_id"] else None
                                p4_d = {**running_state[m["team_b_p2_id"]], "player_id": m["team_b_p2_id"]} if m["team_b_p2_id"] else None
                                
                                out = RyftV16.compute_match(
                                    p1_d, p2_d, p3_d, p4_d, m["score_team_a"], m["score_team_b"],
                                    max(m["games_winner"], m["score_team_a"]), min(m["games_loser"], m["score_team_b"]),
                                    s_data["format_id"], s_data["venue_id"], is_singles=(s_data["match_mode"]=="SINGLES"),
                                    session_id=s_id, session_checked_in=s_data["active_checked_in_count"], 
                                    is_tournament=bool(s_data.get("is_tournament", 0)), is_dry=True, conn=conn, cumulative_deltas=cum_deltas
                                )
                                
                                res_cols = st.columns(2 if (s_data["match_mode"]=="SINGLES") else 4)
                                for idx, pr in enumerate(out["res"]):
                                    pid = pr["pid"]
                                    running_state[pid]["latent_mmr"] = pr["post_r"]
                                    running_state[pid]["rating_deviation"] = pr["post_rd"]
                                    running_state[pid]["rating_accuracy_pct"] = pr["acc"]
                                    running_state[pid]["calibration_tier"] = pr["tier"]
                                    cum_deltas[pid] += pr["delta"]

                                    with res_cols[idx]:
                                        st.markdown(f"""
                                        <div style="background-color: #1e293b; border: 1px solid #0284c7; border-radius: 6px; padding: 10px; margin-bottom: 8px; color: #f8fafc;">
                                            <div style="font-weight: bold; color:#38bdf8;">{pr['name']}</div>
                                            <div style="font-size: 0.85em; color: #cbd5e1;">
                                                Pre: <code style="color:#38bdf8; background:#0f172a;">{pr['pre_r']:.3f}</code> ➔ Post: <code style="color:#38bdf8; background:#0f172a;">{pr['post_r']:.3f}</code><br/>
                                                Delta: <strong style="color:{'#4ade80' if pr['delta']>=0 else '#f87171'}">{pr['delta']:+.4f}</strong><br/>
                                                RD: <code style="color:#e2e8f0; background:#0f172a;">{pr['pre_rd']:.1f} ➔ {pr['post_rd']:.1f}</code><br/>
                                                Acc: {pr['pre_acc']:.1f}% ➔ {pr['acc']:.1f}%<br/>
                                                <span style="color:#38bdf8;">{pr['pre_tier']} ➔ {pr['tier']}</span>
                                            </div>
                                        </div>
                                        """, unsafe_allow_html=True)
                                        if pr["flags"]: st.caption("⚡ " + " | ".join(pr["flags"]))
                            
                            st.markdown("---")
                            with st.form(f"edit_comp_{m['session_match_id']}"):
                                if fmt_category == "RACE_GAMES":
                                    c_ea, c_eb = st.columns(2)
                                    val_a = sets_json[0][0] if sets_json else m['score_team_a']
                                    val_b = sets_json[0][1] if sets_json else m['score_team_b']
                                    new_gw = c_ea.number_input(f"Games {ta_players}", 0, 100, val_a, key=f"ed_ga_{m['session_match_id']}")
                                    new_gl = c_eb.number_input(f"Games {tb_players}", 0, 100, val_b, key=f"ed_gb_{m['session_match_id']}")
                                    if st.form_submit_button("Update Score"):
                                        sa, sb = (1 if new_gw > new_gl else 0), (1 if new_gl > new_gw else 0)
                                        conn.execute("UPDATE session_matches SET score_team_a=?, score_team_b=?, games_winner=?, games_loser=?, set_scores_json=?, match_status='STAGED' WHERE session_match_id=?", (sa, sb, max(new_gw, new_gl), min(new_gw, new_gl), json.dumps([(new_gw, new_gl)]), m['session_match_id']))
                                        conn.commit(); st.rerun()
                                elif fmt_category == "MULTI_SET":
                                    s1a = sets_json[0][0] if len(sets_json) > 0 else 6; s1b = sets_json[0][1] if len(sets_json) > 0 else 3
                                    s2a = sets_json[1][0] if len(sets_json) > 1 else 6; s2b = sets_json[1][1] if len(sets_json) > 1 else 4
                                    s3a = sets_json[2][0] if len(sets_json) > 2 else 0; s3b = sets_json[2][1] if len(sets_json) > 2 else 0
                                    ec1, ec2 = st.columns(2)
                                    ns1a = ec1.number_input(f"Set 1: {ta_players}", 0, 7, s1a, key=f"es1a_{m['session_match_id']}"); ns1b = ec2.number_input(f"Set 1: {tb_players}", 0, 7, s1b, key=f"es1b_{m['session_match_id']}")
                                    ns2a = ec1.number_input(f"Set 2: {ta_players}", 0, 7, s2a, key=f"es2a_{m['session_match_id']}"); ns2b = ec2.number_input(f"Set 2: {tb_players}", 0, 7, s2b, key=f"es2b_{m['session_match_id']}")
                                    ns3a = ec1.number_input(f"Set 3: {ta_players}", 0, 7, s3a, key=f"es3a_{m['session_match_id']}"); ns3b = ec2.number_input(f"Set 3: {tb_players}", 0, 7, s3b, key=f"es3b_{m['session_match_id']}")
                                    if st.form_submit_button("Update Score"):
                                        final_sets = [(ns1a, ns1b), (ns2a, ns2b)]
                                        if ns3a > 0 or ns3b > 0: final_sets.append((ns3a, ns3b))
                                        sa, sb = sum(1 for s in final_sets if s[0]>s[1]), sum(1 for s in final_sets if s[1]>s[0])
                                        gw, gl = sum(x[0] for x in final_sets), sum(x[1] for x in final_sets)
                                        conn.execute("UPDATE session_matches SET score_team_a=?, score_team_b=?, games_winner=?, games_loser=?, set_scores_json=?, match_status='STAGED' WHERE session_match_id=?", (sa, sb, max(gw, gl), min(gw, gl), json.dumps(final_sets), m['session_match_id']))
                                        conn.commit(); st.rerun()
                                else:
                                    c_ea, c_eb = st.columns(2)
                                    val_a = sets_json[0][0] if sets_json else m['score_team_a']
                                    val_b = sets_json[0][1] if sets_json else m['score_team_b']
                                    new_a = c_ea.number_input(f"Points {ta_players}", 0, 100, val_a, key=f"ed_a_{m['session_match_id']}"); new_b = c_eb.number_input(f"Points {tb_players}", 0, 100, val_b, key=f"ed_b_{m['session_match_id']}")
                                    if st.form_submit_button("Update Points"):
                                        if (new_a + new_b) != fmt_info["total_points"]: st.error(f"Score must total {fmt_info['total_points']} points.")
                                        else:
                                            conn.execute("UPDATE session_matches SET score_team_a=?, score_team_b=?, games_winner=?, games_loser=?, set_scores_json=?, match_status='STAGED' WHERE session_match_id=?", (new_a, new_b, max(new_a, new_b), min(new_a, new_b), json.dumps([(new_a, new_b)]), m['session_match_id']))
                                            conn.commit(); st.rerun()

                if uncompleted:
                    st.markdown("---")
                    if st.button("⏹️ End Session Early", type="secondary"):
                        conn.execute("UPDATE session_matches SET match_status='CANCELLED' WHERE session_id=? AND match_status='SCHEDULED'", (s_id,))
                        conn.commit(); st.rerun()

            elif sub_nav == "📊 Standings & Atomic Commit":
                st.subheader("Event Standings & Final Engine Commit")
                staged_matches = conn.execute("SELECT * FROM session_matches WHERE session_id = ? AND match_status = 'STAGED' ORDER BY match_order ASC", (s_id,)).fetchall()

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
                        standings[pid] = {"Player": id_to_name.get(pid, pid), "Played": 0, "Won": 0, "Lost": 0, "Points Won": 0, "Points Lost": 0, "Diff": 0}
                    for sm in staged_matches:
                        for pid in [sm["team_a_p1_id"], sm["team_a_p2_id"]]:
                            if pid and pid in standings:
                                standings[pid]["Played"] += 1; standings[pid]["Points Won"] += sm["score_team_a"]; standings[pid]["Points Lost"] += sm["score_team_b"]
                                if sm["score_team_a"] > sm["score_team_b"]: standings[pid]["Won"] += 1
                                elif sm["score_team_a"] < sm["score_team_b"]: standings[pid]["Lost"] += 1
                        for pid in [sm["team_b_p1_id"], sm["team_b_p2_id"]]:
                            if pid and pid in standings:
                                standings[pid]["Played"] += 1; standings[pid]["Points Won"] += sm["score_team_b"]; standings[pid]["Points Lost"] += sm["score_team_a"]
                                if sm["score_team_b"] > sm["score_team_a"]: standings[pid]["Won"] += 1
                                elif sm["score_team_b"] < sm["score_team_a"]: standings[pid]["Lost"] += 1

                for item in standings.values(): item["Diff"] = item["Points Won"] - item["Points Lost"]
                df_stand = pd.DataFrame(list(standings.values())).sort_values(by=["Won", "Diff", "Points Won"], ascending=[False, False, False])
                st.markdown("#### 🏆 Session Standings")
                st.dataframe(df_stand, use_container_width=True)
                st.caption("Tiebreakers evaluated dynamically: Match Wins -> Point/Game Differential -> Total Points Won.")

                st.markdown("---")
                st.markdown("#### 🔬 Sequential Pre/Post Session Audit")
                if staged_matches:
                    temp_ratings = {}
                    cumulative_deltas = {}
                    for pid in enrolled_ids:
                        p_row = dict(p_meta[pid])
                        p_row["initial_mmr"] = p_row["latent_mmr"]
                        p_row["initial_rd"] = p_row["rating_deviation"]
                        p_row["initial_acc"] = p_row["rating_accuracy_pct"]
                        temp_ratings[pid] = p_row
                        cumulative_deltas[pid] = 0.0
                    
                    for sm in staged_matches:
                        for pid in [sm["team_a_p1_id"], sm["team_a_p2_id"], sm["team_b_p1_id"], sm["team_b_p2_id"]]:
                            if pid and pid not in temp_ratings:
                                p_row = dict(p_meta[pid])
                                p_row["initial_mmr"] = p_row["latent_mmr"]; p_row["initial_rd"] = p_row["rating_deviation"]; p_row["initial_acc"] = p_row["rating_accuracy_pct"]
                                temp_ratings[pid] = p_row; cumulative_deltas[pid] = 0.0

                        p1_d = {**temp_ratings[sm["team_a_p1_id"]], "player_id": sm["team_a_p1_id"]}
                        p3_d = {**temp_ratings[sm["team_b_p1_id"]], "player_id": sm["team_b_p1_id"]}
                        p2_d = {**temp_ratings[sm["team_a_p2_id"]], "player_id": sm["team_a_p2_id"]} if sm["team_a_p2_id"] else None
                        p4_d = {**temp_ratings[sm["team_b_p2_id"]], "player_id": sm["team_b_p2_id"]} if sm["team_b_p2_id"] else None

                        is_sing = (s_data["match_mode"] == "SINGLES")
                        out = RyftV16.compute_match(
                            p1_d, p2_d, p3_d, p4_d, sm["score_team_a"], sm["score_team_b"],
                            max(sm["games_winner"], sm["score_team_a"]), min(sm["games_loser"], sm["score_team_b"]),
                            s_data["format_id"], s_data["venue_id"], is_singles=is_sing,
                            session_id=s_id, session_checked_in=s_data["active_checked_in_count"], 
                            is_tournament=bool(s_data.get("is_tournament", 0)), is_dry=True, conn=conn, cumulative_deltas=cumulative_deltas
                        )
                        for pr in out["res"]:
                            temp_ratings[pr["pid"]]["latent_mmr"] = pr["post_r"]
                            temp_ratings[pr["pid"]]["rating_deviation"] = pr["post_rd"]
                            temp_ratings[pr["pid"]]["rating_accuracy_pct"] = pr["acc"]
                            temp_ratings[pr["pid"]]["calibration_tier"] = pr["tier"]
                            cumulative_deltas[pr["pid"]] += pr["delta"]

                    summary_rows = []
                    for pid, pdata in temp_ratings.items():
                        tot_delta = cumulative_deltas[pid]
                        summary_rows.append({
                            "Player": format_pr_name(pdata["display_name"], pdata["is_provisional"]),
                            "Pre MMR": f"{pdata['initial_mmr']:.3f}", "Pre RD": f"{pdata['initial_rd']:.1f}", "Pre Acc %": f"{pdata['initial_acc']:.1f}%",
                            "Total Capped Delta": f"{tot_delta:+.4f}",
                            "Projected Post MMR": f"{pdata['latent_mmr']:.3f}", "Projected Post RD": f"{pdata['rating_deviation']:.1f}", "Projected Post Acc %": f"{pdata['rating_accuracy_pct']:.1f}%",
                            "Tier": pdata.get("calibration_tier", "VERIFIED")
                        })
                    st.dataframe(pd.DataFrame(summary_rows), use_container_width=True)

                st.markdown("---")
                col_sub1, col_sub2 = st.columns(2)
                if col_sub1.button("💾 Save Draft", use_container_width=True): st.success("Draft saved.")
                if col_sub2.button("🚀 VERIFY & COMMIT SESSION TO RATING ENGINE", type="primary", use_container_width=True):
                    if not staged_matches: st.error("No staged matches.")
                    else:
                        ts = datetime.now(timezone.utc).isoformat()
                        session_pids_to_sync = set()
                        cumulative_deltas = {}
                        temp_ratings = {}
                        for pid in enrolled_ids:
                            temp_ratings[pid] = dict(p_meta[pid])
                            cumulative_deltas[pid] = 0.0

                        ven_row = conn.execute("SELECT city_id, country_code FROM venues WHERE venue_id = ?", (s_data["venue_id"],)).fetchone()
                        v_city = ven_row["city_id"]
                        city_bridge_counts, country_bridge_counts = 0, 0

                        for sm in staged_matches:
                            for pid in [sm["team_a_p1_id"], sm["team_a_p2_id"], sm["team_b_p1_id"], sm["team_b_p2_id"]]:
                                if pid and pid not in temp_ratings:
                                    temp_ratings[pid] = dict(p_meta[pid]); cumulative_deltas[pid] = 0.0

                            p1_d = {**temp_ratings[sm["team_a_p1_id"]], "player_id": sm["team_a_p1_id"]}
                            p3_d = {**temp_ratings[sm["team_b_p1_id"]], "player_id": sm["team_b_p1_id"]}
                            p2_d = {**temp_ratings[sm["team_a_p2_id"]], "player_id": sm["team_a_p2_id"]} if sm["team_a_p2_id"] else None
                            p4_d = {**temp_ratings[sm["team_b_p2_id"]], "player_id": sm["team_b_p2_id"]} if sm["team_b_p2_id"] else None
                            
                            is_sing = (s_data["match_mode"] == "SINGLES")
                            out = RyftV16.compute_match(
                                p1_d, p2_d, p3_d, p4_d, sm["score_team_a"], sm["score_team_b"],
                                max(sm["games_winner"], sm["score_team_a"]), min(sm["games_loser"], sm["score_team_b"]),
                                s_data["format_id"], s_data["venue_id"], is_singles=is_sing,
                                session_id=s_id, session_checked_in=s_data["active_checked_in_count"],
                                is_tournament=bool(s_data.get("is_tournament", 0)), conn=conn, cumulative_deltas=cumulative_deltas
                            )

                            m_id = f"M_SESS_{sm['session_match_id']}"
                            is_v_b = 1 if (p1_d.get("home_venue_id") != s_data["venue_id"] and p1_d.get("home_city_id") == ven_row["city_id"]) else 0
                            is_c_b = 1 if (p1_d.get("home_city_id") != ven_row["city_id"] and p1_d.get("home_country_code") == ven_row["country_code"]) else 0
                            is_co_b = 1 if (p1_d.get("home_country_code") != ven_row["country_code"]) else 0
                            if is_c_b: city_bridge_counts += 1
                            if is_co_b: country_bridge_counts += 1

                            all_guardrails = []
                            for pr in out["res"]: all_guardrails.extend(pr["flags"])

                            conn.execute('''
                                INSERT INTO matches (
                                    match_id, venue_id, format_id, session_id, is_singles, is_tournament,
                                    is_venue_bridge, is_city_bridge, is_country_bridge, team_a_p1_id, team_a_p2_id, team_b_p1_id, team_b_p2_id,
                                    score_team_a, score_team_b, set_scores_json, games_winner, games_loser,
                                    pre_rating_a, pre_rating_b, win_expectancy_a, applied_m_c, applied_s_margin,
                                    delta_r_p1, delta_r_p2, delta_r_p3, delta_r_p4, guardrails_summary, match_timestamp
                                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                            ''', (m_id, s_data["venue_id"], s_data["format_id"], s_id, 1 if is_sing else 0, 1 if s_data.get("is_tournament", 0) else 0,
                                is_v_b, is_c_b, is_co_b, p1_d["player_id"], p2_d["player_id"] if p2_d else None, p3_d["player_id"], p4_d["player_id"] if p4_d else None,
                                sm["score_team_a"], sm["score_team_b"], sm["set_scores_json"], max(sm["games_winner"], sm["score_team_a"]), min(sm["games_loser"], sm["score_team_b"]),
                                out["ta_r"], out["tb_r"], out["ea"], out["applied_m_c"], out["mov"],
                                out["res"][0]["delta"], out["res"][2]["delta"] if not is_sing else 0.0,
                                out["res"][1]["delta"], out["res"][3]["delta"] if not is_singles else 0.0, json.dumps(all_guardrails), ts))

                            for pr in out["res"]:
                                conn.execute("""UPDATE players SET latent_mmr=?, display_rating=?, rating_deviation=?, rating_accuracy_pct=?, calibration_tier=?, is_provisional=?, consecutive_losses=?, rolling_90d_peak=max(rolling_90d_peak, ?), rolling_180d_peak=max(rolling_180d_peak, ?), rolling_365d_peak=max(rolling_365d_peak, ?), last_match_time=? WHERE player_id=?""",
                                             (pr["post_r"], pr["post_disp"], pr["post_rd"], pr["acc"], pr["tier"], pr["prov"], pr["c_loss"], pr["post_r"], pr["post_r"], pr["post_r"], ts, pr["pid"]))
                                conn.execute("""INSERT INTO match_logs (log_id, match_id, player_id, pre_latent_mmr, post_latent_mmr, pre_display_rating, post_display_rating, pre_rd, post_rd, pre_accuracy_pct, post_accuracy_pct, delta_r, guardrails_triggered, logged_at) 
                                                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                                             (f"L_{pr['pid']}_{m_id}", m_id, pr["pid"], pr["pre_r"], pr["post_r"], pr["pre_disp"], pr["post_disp"], pr["pre_rd"], pr["post_rd"], pr["pre_acc"], pr["acc"], pr["delta"], json.dumps(pr["flags"]), ts))
                                session_pids_to_sync.add(pr["pid"])
                                temp_ratings[pr["pid"]]["latent_mmr"] = pr["post_r"]
                                temp_ratings[pr["pid"]]["rating_deviation"] = pr["post_rd"]
                                temp_ratings[pr["pid"]]["rating_accuracy_pct"] = pr["acc"]
                                cumulative_deltas[pr["pid"]] += pr["delta"]

                            conn.execute("UPDATE session_matches SET match_status='COMMITTED', committed_match_id=? WHERE session_match_id=?", (m_id, sm['session_match_id']))

                        conn.execute("UPDATE venues SET total_matches_played = total_matches_played + ?, city_bridge_matches_count = city_bridge_matches_count + ?, country_bridge_matches_count = country_bridge_matches_count + ? WHERE venue_id = ?", (len(staged_matches), city_bridge_counts, country_bridge_counts, s_data["venue_id"]))
                        conn.execute("UPDATE locations SET total_matches_played = total_matches_played + ? WHERE location_id = ?", (len(staged_matches), v_city))
                        conn.execute("UPDATE sessions SET session_status='COMPLETED', completed_at=?, current_stage='COMPLETED' WHERE session_id=?", (ts, s_id))

                        for pid in session_pids_to_sync: RyftV16.sync_player_aggregates(pid, conn=conn)
                        conn.commit(); st.balloons(); st.success("✅ Session committed!"); st.rerun()

        with s_tab1:
            up_sessions = conn.execute("SELECT s.*, v.venue_name, f.format_name, f.category as fmt_cat FROM sessions s JOIN venues v ON s.venue_id = v.venue_id JOIN match_formats f ON s.format_id = f.format_id WHERE s.session_status = 'CONFIG' ORDER BY s.created_at DESC").fetchall()
            if not up_sessions: st.info("No upcoming sessions.")
            else:
                sel_up = st.selectbox("Choose Upcoming Session", [s["session_title"] for s in up_sessions], key="sb_up")
                render_session_workspace([dict(s) for s in up_sessions if s["session_title"] == sel_up][0])

        with s_tab2:
            live_sessions = conn.execute("SELECT s.*, v.venue_name, f.format_name, f.category as fmt_cat FROM sessions s JOIN venues v ON s.venue_id = v.venue_id JOIN match_formats f ON s.format_id = f.format_id WHERE s.session_status = 'LIVE' ORDER BY s.created_at DESC").fetchall()
            if not live_sessions: st.info("No live sessions currently in progress.")
            else:
                sel_live = st.selectbox("Choose Live Session", [s["session_title"] for s in live_sessions], key="sb_live")
                render_session_workspace([dict(s) for s in live_sessions if s["session_title"] == sel_live][0])

        with s_tab3:
            comp_sessions = conn.execute("SELECT s.*, v.venue_name, f.format_name, f.category as fmt_cat FROM sessions s JOIN venues v ON s.venue_id = v.venue_id JOIN match_formats f ON s.format_id = f.format_id WHERE s.session_status = 'COMPLETED' ORDER BY s.completed_at DESC").fetchall()
            if not comp_sessions: st.info("No completed sessions.")
            else:
                for csess in comp_sessions:
                    with st.expander(f"🏆 {csess['session_title']} — {csess['venue_name']} ({csess['completed_at'][:10] if csess['completed_at'] else ''})"):
                        st.write(f"Format: **{csess['format_name']}** | Mode: **{csess['team_format']}** | Enrolled: **{csess['player_count']}**")
    conn.close()

elif nav == "🏆 Tournament Desk (Delayed)":
    st.title("Tournament Desk (Delayed Entry)")
    st.caption("Asynchronous batch ingestion. Deltas stack additively onto current MMR.")

    conn = get_db_connection()
    venues = conn.execute("SELECT * FROM venues WHERE is_active = 1").fetchall()
    v_dict = {v["venue_name"]: dict(v) for v in venues}
    cats = conn.execute("SELECT category_name, min_rating, max_rating FROM rating_categories ORDER BY sort_order ASC").fetchall()
    cat_opts = [c["category_name"] for c in cats]
    formats = conn.execute("SELECT * FROM match_formats WHERE is_active = 1").fetchall()
    f_dict = {f["format_name"]: dict(f) for f in formats}
    players = conn.execute("SELECT * FROM players WHERE calibration_tier != 'INACTIVE' ORDER BY display_name").fetchall()
    p_meta = {p["player_id"]: dict(p) for p in players}
    p_dict = {format_pr_name(p["display_name"], p["is_provisional"]): p["player_id"] for p in players}
    id_to_name = {p["player_id"]: format_pr_name(p["display_name"], p["is_provisional"]) for p in players}

    mode = st.radio("Tournament Module", ["➕ Register Tournament", "🎮 Manage Delayed Matches"], horizontal=True)

    if mode == "➕ Register Tournament":
        st.subheader("Register Official Tournament Bracket")
        with st.form("create_tourney"):
            t_name = st.text_input("Tournament Name")
            t_ven = st.selectbox("Venue", list(v_dict.keys()) if v_dict else [])
            t_date = st.date_input("Tournament Date")
            t_floor = st.selectbox("Tournament Floor (Bracket Entry Cutoff)", cat_opts, index=0)
            st.info("The Floor Category checks every player's trailing Peak High-Water Marks to block sandbagging.")
            if st.form_submit_button("Create Tournament"):
                if t_name and t_ven:
                    t_id = f"T_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
                    conn.execute("INSERT INTO tournaments (tourney_id, name, venue_id, tourney_date, floor_category, created_at) VALUES (?, ?, ?, ?, ?, ?)", (t_id, t_name, v_dict[t_ven]["venue_id"], str(t_date), t_floor, datetime.now(timezone.utc).isoformat()))
                    conn.commit(); st.success(f"Tournament {t_name} created!"); st.rerun()

    elif mode == "🎮 Manage Delayed Matches":
        tourneys = conn.execute("SELECT * FROM tournaments WHERE status = 'PENDING' ORDER BY created_at DESC").fetchall()
        if not tourneys: st.info("No pending tournaments.")
        else:
            t_sel_name = st.selectbox("Select Tournament", [t["name"] for t in tourneys])
            t_data = [dict(t) for t in tourneys if t["name"] == t_sel_name][0]
            t_id = t_data["tourney_id"]
            floor_max = [c for c in cats if c["category_name"] == t_data["floor_category"]][0]["max_rating"]

            st.markdown(f"### {t_data['name']} (Floor: {t_data['floor_category']})")
            with st.expander("➕ Add Scorecard", expanded=False):
                with st.form("add_tourney_match"):
                    m_time = st.time_input("Exact Match Time", value=time(10,0))
                    t_fmt = st.selectbox("Format", list(f_dict.keys()))
                    t_mode = st.radio("Mode", ["DOUBLES", "SINGLES"], horizontal=True)
                    st.write("**Roster**")
                    c1, c2 = st.columns(2)
                    p1 = c1.selectbox("Team A P1", ["-- Select --"] + list(p_dict.keys()), key="tm_p1")
                    p2 = c1.selectbox("Team A P2", ["-- Select --"] + list(p_dict.keys()), key="tm_p2") if t_mode == "DOUBLES" else "-- Select --"
                    p3 = c2.selectbox("Team B P1", ["-- Select --"] + list(p_dict.keys()), key="tm_p3")
                    p4 = c2.selectbox("Team B P2", ["-- Select --"] + list(p_dict.keys()), key="tm_p4") if t_mode == "DOUBLES" else "-- Select --"

                    st.write("**Score**")
                    cs1, cs2 = st.columns(2)
                    sa = cs1.number_input("Team A Sets/Games/Points", 0, 100, 0)
                    sb = cs2.number_input("Team B Sets/Games/Points", 0, 100, 0)

                    if st.form_submit_button("Add to Batch"):
                        valid = True; pids = []
                        if p1 != "-- Select --": pids.append(p_dict[p1])
                        if p3 != "-- Select --": pids.append(p_dict[p3])
                        if t_mode == "DOUBLES":
                            if p2 != "-- Select --": pids.append(p_dict[p2])
                            if p4 != "-- Select --": pids.append(p_dict[p4])
                        for pid in pids:
                            meta = p_meta[pid]
                            if max(meta["rolling_90d_peak"], meta["rolling_180d_peak"]) > floor_max:
                                st.error(f"❌ Sandbagging Detected: {meta['display_name']}'s historical peak exceeds the limit.")
                                valid = False
                        
                        if valid and len(pids) == (4 if t_mode == "DOUBLES" else 2):
                            tm_id = f"TM_{datetime.now().strftime('%M%S%f')}"
                            conn.execute("INSERT INTO tourney_matches (t_match_id, tourney_id, match_time, format_id, is_singles, team_a_p1_id, team_a_p2_id, team_b_p1_id, team_b_p2_id, score_team_a, score_team_b, games_winner, games_loser) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                                         (tm_id, t_id, f"{t_data['tourney_date']}T{m_time.strftime('%H:%M:%S')}Z", f_dict[t_fmt]["format_id"], 1 if t_mode == "SINGLES" else 0, p_dict[p1], p_dict.get(p2), p_dict[p3], p_dict.get(p4), sa, sb, max(sa, sb), min(sa, sb)))
                            conn.commit(); st.success("Match Staged in Batch!"); st.rerun()

            t_matches = conn.execute("SELECT * FROM tourney_matches WHERE tourney_id = ? ORDER BY match_time ASC", (t_id,)).fetchall()
            st.markdown(f"#### 📋 Staged Scorecards ({len(t_matches)})")
            
            def get_historical_state(pid, timestamp, conn):
                log = conn.execute("SELECT post_latent_mmr, post_rd, post_accuracy_pct, post_display_rating FROM match_logs WHERE player_id=? AND logged_at <= ? ORDER BY logged_at DESC LIMIT 1", (pid, timestamp)).fetchone()
                if log: return float(log["post_latent_mmr"]), float(log["post_rd"]), float(log["post_accuracy_pct"]), float(log["post_display_rating"])
                p = conn.execute("SELECT initial_rating FROM players WHERE player_id=?", (pid,)).fetchone()
                return float(p["initial_rating"]), 350.0, 0.0, float(p["initial_rating"])

            if t_matches:
                for tm in t_matches: st.write(f"`{tm['match_time'][11:16]}` | {id_to_name[tm['team_a_p1_id']]} & {id_to_name.get(tm['team_a_p2_id'],'')} **[{tm['score_team_a']}-{tm['score_team_b']}]** {id_to_name[tm['team_b_p1_id']]} & {id_to_name.get(tm['team_b_p2_id'],'')}")
                if st.button("🔬 Preview Asynchronous Deltas", type="secondary"):
                    st.markdown("##### 🔬 Time Machine Calculation")
                    for tm in t_matches:
                        ts = tm["match_time"]
                        mock_p1, mock_p3 = dict(p_meta[tm["team_a_p1_id"]]), dict(p_meta[tm["team_b_p1_id"]])
                        mock_p1["latent_mmr"], mock_p1["rating_deviation"], _, _ = get_historical_state(tm["team_a_p1_id"], ts, conn)
                        mock_p3["latent_mmr"], mock_p3["rating_deviation"], _, _ = get_historical_state(tm["team_b_p1_id"], ts, conn)
                        mock_p2, mock_p4 = None, None
                        if not tm["is_singles"]:
                            mock_p2 = dict(p_meta[tm["team_a_p2_id"]])
                            mock_p2["latent_mmr"], mock_p2["rating_deviation"], _, _ = get_historical_state(tm["team_a_p2_id"], ts, conn)
                            mock_p4 = dict(p_meta[tm["team_b_p2_id"]])
                            mock_p4["latent_mmr"], mock_p4["rating_deviation"], _, _ = get_historical_state(tm["team_b_p2_id"], ts, conn)
                        out = RyftV16.compute_match(mock_p1, mock_p2, mock_p3, mock_p4, tm["score_team_a"], tm["score_team_b"], tm["games_winner"], tm["games_loser"], tm["format_id"], t_data["venue_id"], bool(tm["is_singles"]), is_dry=True, is_tournament=True, conn=conn)
                        for r in out["res"]: st.write(f"- **{r['name']}** (Past MMR: `{r['pre_r']:.3f}`) $\\rightarrow$ Earned $\\Delta R$: `{r['delta']:+.4f}`")

                if st.button("🚀 COMMIT TOURNAMENT BATCH & ADDITIVE STACK", type="primary"):
                    ts_proc = datetime.now(timezone.utc).isoformat()
                    for tm in t_matches:
                        ts = tm["match_time"]
                        mock_p1, mock_p3 = dict(p_meta[tm["team_a_p1_id"]]), dict(p_meta[tm["team_b_p1_id"]])
                        mock_p1["latent_mmr"], mock_p1["rating_deviation"], mock_p1["rating_accuracy_pct"], mock_p1["display_rating"] = get_historical_state(tm["team_a_p1_id"], ts, conn)
                        mock_p3["latent_mmr"], mock_p3["rating_deviation"], mock_p3["rating_accuracy_pct"], mock_p3["display_rating"] = get_historical_state(tm["team_b_p1_id"], ts, conn)
                        mock_p2, mock_p4 = None, None
                        if not tm["is_singles"]:
                            mock_p2 = dict(p_meta[tm["team_a_p2_id"]])
                            mock_p2["latent_mmr"], mock_p2["rating_deviation"], mock_p2["rating_accuracy_pct"], mock_p2["display_rating"] = get_historical_state(tm["team_a_p2_id"], ts, conn)
                            mock_p4 = dict(p_meta[tm["team_b_p2_id"]])
                            mock_p4["latent_mmr"], mock_p4["rating_deviation"], mock_p4["rating_accuracy_pct"], mock_p4["display_rating"] = get_historical_state(tm["team_b_p2_id"], ts, conn)
                        
                        out = RyftV16.compute_match(mock_p1, mock_p2, mock_p3, mock_p4, tm["score_team_a"], tm["score_team_b"], tm["games_winner"], tm["games_loser"], tm["format_id"], t_data["venue_id"], bool(tm["is_singles"]), is_tournament=True, conn=conn)

                        m_id = f"M_TRNY_{tm['t_match_id']}"
                        all_guardrails = []
                        for pr in out["res"]: all_guardrails.extend(pr["flags"])

                        conn.execute('''
                            INSERT INTO matches (
                                match_id, venue_id, format_id, is_singles, is_tournament,
                                team_a_p1_id, team_a_p2_id, team_b_p1_id, team_b_p2_id,
                                score_team_a, score_team_b, set_scores_json, games_winner, games_loser,
                                pre_rating_a, pre_rating_b, win_expectancy_a, applied_m_c, applied_s_margin,
                                delta_r_p1, delta_r_p2, delta_r_p3, delta_r_p4, guardrails_summary, is_retroactive, processed_at, match_timestamp
                            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, '[]', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1, ?, ?)
                        ''', (m_id, t_data["venue_id"], tm["format_id"], tm["is_singles"], 1,
                              tm["team_a_p1_id"], tm["team_a_p2_id"], tm["team_b_p1_id"], tm["team_b_p2_id"],
                              tm["score_team_a"], tm["score_team_b"], tm["games_winner"], tm["games_loser"],
                              out["ta_r"], out["tb_r"], out["ea"], out["applied_m_c"], out["mov"],
                              out["res"][0]["delta"], out["res"][2]["delta"] if not tm["is_singles"] else 0.0,
                              out["res"][1]["delta"], out["res"][3]["delta"] if not tm["is_singles"] else 0.0, json.dumps(all_guardrails), ts_proc, ts))

                        for pr in out["res"]:
                            pid, delta = pr["pid"], pr["delta"]
                            conn.execute("UPDATE players SET latent_mmr = latent_mmr + ?, display_rating = display_rating + ?, tournament_floor = max(tournament_floor, latent_mmr + ?), rolling_90d_peak = max(rolling_90d_peak, latent_mmr + ?), rolling_180d_peak = max(rolling_180d_peak, latent_mmr + ?) WHERE player_id=?", (delta, delta, delta, delta, delta, pid))
                            conn.execute("INSERT INTO match_logs (log_id, match_id, player_id, pre_latent_mmr, post_latent_mmr, pre_display_rating, post_display_rating, pre_rd, post_rd, pre_accuracy_pct, post_accuracy_pct, delta_r, is_retroactive, guardrails_triggered, logged_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1, ?, ?)",
                                         (f"L_{pid}_{m_id}", m_id, pid, pr["pre_r"], pr["post_r"], pr["pre_disp"], pr["post_disp"], pr["pre_rd"], pr["post_rd"], pr["pre_acc"], pr["acc"], delta, json.dumps(pr["flags"]), ts))
                    conn.execute("UPDATE tournaments SET status='COMMITTED' WHERE tourney_id=?", (t_id,))
                    conn.commit(); st.balloons(); st.success("✅ Tournament batch successfully committed! Deltas additively stacked."); st.rerun()
    conn.close()

elif nav == "📜 Historical Matches":
    st.title("Historical Matches & Deep Algorithmic Audit Ledger")
    conn = get_db_connection()
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Total Matches", conn.execute("SELECT COUNT(*) FROM matches").fetchone()[0])
    c2.metric("Venue Bridges", conn.execute("SELECT COUNT(*) FROM matches WHERE is_venue_bridge = 1").fetchone()[0])
    c3.metric("City Bridges", conn.execute("SELECT COUNT(*) FROM matches WHERE is_city_bridge = 1").fetchone()[0])
    c4.metric("Country Bridges", conn.execute("SELECT COUNT(*) FROM matches WHERE is_country_bridge = 1").fetchone()[0])

    f1, f2, f3, f4 = st.columns(4)
    players = conn.execute("SELECT player_id, display_name, is_provisional FROM players").fetchall()
    p_map = {format_pr_name(p["display_name"], p["is_provisional"]): p["player_id"] for p in players}
    s_player = f1.selectbox("Filter by Player", ["All Players"] + list(p_map.keys()))
    s_venue = f2.selectbox("Filter by Venue", ["All Venues"] + [r["venue_name"] for r in conn.execute("SELECT venue_name FROM venues").fetchall()])
    s_city = f3.selectbox("Filter by City", ["All Cities"] + [r["location_name"] for r in conn.execute("SELECT location_name FROM locations WHERE location_type = 'CITY'").fetchall()])

    sess_list = conn.execute("SELECT session_id, session_title FROM sessions ORDER BY created_at DESC").fetchall()
    sess_opts = ["-- All Matches --", "Non-Session Matches Only"] + [f"{s['session_title']} ({s['session_id']})" for s in sess_list]
    s_sess_pick = f4.selectbox("Filter by Session", sess_opts)

    sql = """
        SELECT m.*, v.venue_name, l.location_name as city, f.format_name, s.session_title,
               p1.display_name as p1n, p1.is_provisional as p1_prov, p2.display_name as p2n, p2.is_provisional as p2_prov,
               p3.display_name as p3n, p3.is_provisional as p3_prov, p4.display_name as p4n, p4.is_provisional as p4_prov
        FROM matches m JOIN venues v ON m.venue_id = v.venue_id JOIN locations l ON v.city_id = l.location_id JOIN match_formats f ON m.format_id = f.format_id
        LEFT JOIN sessions s ON m.session_id = s.session_id LEFT JOIN players p1 ON m.team_a_p1_id = p1.player_id LEFT JOIN players p2 ON m.team_a_p2_id = p2.player_id 
        LEFT JOIN players p3 ON m.team_b_p1_id = p3.player_id LEFT JOIN players p4 ON m.team_b_p2_id = p4.player_id WHERE 1=1
    """
    params = []
    if s_player != "All Players": sql += " AND (? IN (m.team_a_p1_id, m.team_a_p2_id, m.team_b_p1_id, m.team_b_p2_id))"; params.append(p_map[s_player])
    if s_venue != "All Venues": sql += " AND v.venue_name = ?"; params.append(s_venue)
    if s_city != "All Cities": sql += " AND l.location_name = ?"; params.append(s_city)
    if s_sess_pick == "Non-Session Matches Only": sql += " AND (m.session_id IS NULL OR m.session_id = '')"
    elif s_sess_pick != "-- All Matches --": sql += " AND m.session_id = ?"; params.append(s_sess_pick.split("(")[-1].replace(")", "").strip())

    sql += " ORDER BY m.match_timestamp DESC"
    matches = conn.execute(sql, params).fetchall()

    for m in matches:
        p1_lbl = format_pr_name(m['p1n'], m['p1_prov']) if m['p1n'] else ""
        p2_lbl = format_pr_name(m['p2n'], m['p2_prov']) if m['p2n'] else ""
        p3_lbl = format_pr_name(m['p3n'], m['p3_prov']) if m['p3n'] else ""
        p4_lbl = format_pr_name(m['p4n'], m['p4_prov']) if m['p4n'] else ""
        session_badge = f"🗓️ Session: {m['session_title'] or m['session_id']}" if m['session_id'] else "⚡ Standalone Match"
        if m['is_retroactive']: session_badge = "🏆 Retroactive Tournament"
        bridge_badge = "🏠 Local"
        if m['is_venue_bridge']: bridge_badge = "🌉 Venue Bridge"
        if m['is_city_bridge']: bridge_badge = "🏙️ City Bridge"
        if m['is_country_bridge']: bridge_badge = "🌍 Country Bridge"

        with st.expander(f"🎾 {m['match_timestamp'][:10]} | {m['venue_name']} | {m['score_team_a']}-{m['score_team_b']} ({m['format_name']}) — {session_badge} [{bridge_badge}]"):
            st.write(f"**Team A:** {p1_lbl}" + (f" & {p2_lbl}" if not m['is_singles'] else "") + f" | ΔR: `{m['delta_r_p1']:+.4f}`")
            st.write(f"**Team B:** {p3_lbl}" + (f" & {p4_lbl}" if not m['is_singles'] else "") + f" | ΔR: `{m['delta_r_p3']:+.4f}`")
            st.caption(f"Margin Factor: **{m['applied_s_margin']:.4f}** | Guardrails: {m['guardrails_summary']}")
            p_logs = conn.execute("SELECT ml.*, p.display_name, p.is_provisional FROM match_logs ml JOIN players p ON ml.player_id = p.player_id WHERE ml.match_id = ?", (m['match_id'],)).fetchall()
            for pl in p_logs: st.write(f"- **{format_pr_name(pl['display_name'], pl['is_provisional'])}**: MMR `{pl['pre_latent_mmr']:.3f} ➔ {pl['post_latent_mmr']:.3f}` | Δ: `{pl['delta_r']:+.4f}`")
    conn.close()

elif nav == "👥 Player Roster & Calibration":
    st.title("Players Directory & Calibration Roster")
    conn = get_db_connection()
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Total Players", conn.execute("SELECT COUNT(*) FROM players WHERE calibration_tier != 'INACTIVE'").fetchone()[0])
    c2.metric("Provisional [PR]", conn.execute("SELECT COUNT(*) FROM players WHERE is_provisional = 1 AND calibration_tier != 'INACTIVE'").fetchone()[0])
    c3.metric("Verified", conn.execute("SELECT COUNT(*) FROM players WHERE is_provisional = 0 AND calibration_tier != 'INACTIVE'").fetchone()[0])
    c4.metric("Anchors", conn.execute("SELECT COUNT(*) FROM players WHERE is_anchor = 1").fetchone()[0])

    sql = """SELECT p.player_id, p.display_name, p.all_time_badge, p.initial_rating, p.latent_mmr, p.display_rating, p.rating_deviation, p.rating_accuracy_pct, p.calibration_tier, p.is_provisional, p.is_manually_verified, p.verified_matches_count, p.unique_opponents_count, p.is_active_bridge, l.location_name as city FROM players p JOIN locations l ON p.home_city_id = l.location_id WHERE p.calibration_tier != 'INACTIVE' ORDER BY p.latent_mmr DESC"""
    df_display = pd.read_sql_query(sql, conn)
    df_display["display_name"] = df_display.apply(lambda r: format_pr_name(r["display_name"], r["is_provisional"]), axis=1)
    st.dataframe(df_display, use_container_width=True)

    col_add, col_edit = st.columns(2)
    with col_add:
        with st.expander("➕ Register New Player", expanded=False):
            p_name = st.text_input("Full Name", key="reg_p_name")
            in_cats = ["Beginner (0.500)", "Beginner+ (1.000)", "Intermediate (2.500)", "Intermediate+ (3.500)", "Advanced (4.500)", "Pro (5.500)", "Elite (6.300)"]
            in_pick = st.selectbox("Base Calibration Category", in_cats, key="reg_p_cat")
            
            all_countries = conn.execute("SELECT DISTINCT country_code FROM locations WHERE location_type = 'COUNTRY'").fetchall()
            country_codes = [c["country_code"] for c in all_countries]
            if not country_codes:
                c_from_cities = conn.execute("SELECT DISTINCT country_code FROM locations WHERE location_type = 'CITY'").fetchall()
                country_codes = [c["country_code"] for c in c_from_cities] if c_from_cities else ["IND"]
            sel_country = st.selectbox("Home Country", country_codes, key="reg_p_country")
            
            cities = conn.execute("SELECT location_id, location_name FROM locations WHERE location_type = 'CITY' AND country_code = ?", (sel_country,)).fetchall()
            city_dict = {c["location_name"]: c["location_id"] for c in cities}
            c_sel = st.selectbox("Home City", list(city_dict.keys()) if city_dict else ["None"], key="reg_p_city")
            
            venues = conn.execute("SELECT venue_id, venue_name FROM venues WHERE is_active = 1 AND city_id = ?", (city_dict.get(c_sel),)).fetchall() if c_sel and c_sel != "None" else []
            v_dict_local = {v["venue_name"]: v["venue_id"] for v in venues}
            v_sel = st.selectbox("Home Club / Venue (Optional)", ["None"] + list(v_dict_local.keys()), key="reg_p_venue")

            c_a1, c_a2 = st.columns(2)
            is_anc = c_a1.checkbox("System Anchor", key="reg_p_anc")
            is_ceil = c_a2.checkbox("Ceiling Anchor", key="reg_p_ceil")

            if st.button("Commit Registration", type="primary", key="btn_reg_player"):
                if not city_dict or c_sel == "None": st.error("Create at least one City in 'Venues & Regions' first.")
                elif not p_name: st.error("Player name cannot be blank.")
                else:
                    base_map = {"Beginner (0.500)": 0.500, "Beginner+ (1.000)": 1.000, "Intermediate (2.500)": 2.500, "Intermediate+ (3.500)": 3.500, "Advanced (4.500)": 4.500, "Pro (5.500)": 5.500, "Elite (6.300)": 6.300}
                    base_r = base_map[in_pick]
                    v_id = v_dict_local.get(v_sel) if v_sel != "None" else None
                    p_uuid = f"P_{datetime.now().strftime('%d%H%M%S')}"

                    conn.execute("""
                        INSERT INTO players (player_id, display_name, initial_rating, home_venue_id, home_city_id, home_country_code, latent_mmr, display_rating, rolling_90d_peak, rolling_180d_peak, rolling_365d_peak, tournament_floor, all_time_badge, rating_deviation, rating_accuracy_pct, accuracy_s_rd, accuracy_s_matches, accuracy_s_diversity, calibration_tier, is_provisional, is_manually_verified, is_anchor, is_ceiling_anchor, created_at)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0.0, ?, 350.0, 0.0, 0.0, 0.0, 0.0, 'PROVISIONAL', 1, 0, ?, ?, ?)
                    """, (p_uuid, p_name, base_r, v_id, city_dict[c_sel], sel_country, base_r, base_r, base_r, base_r, base_r, in_pick.split(" ")[0], 1 if is_anc else 0, 1 if is_ceil else 0, datetime.now(timezone.utc).isoformat()))
                    conn.commit(); st.success(f"Registered {p_name} (PR) at {base_r:.3f}!"); st.rerun()

    with col_edit:
        with st.expander("✏️ Inspect & Edit Player", expanded=False):
            all_p = conn.execute("SELECT player_id, display_name, is_provisional FROM players ORDER BY display_name").fetchall()
            if all_p:
                p_pick = st.selectbox("Select Player to Inspect", [p["player_id"] for p in all_p], format_func=lambda x: [format_pr_name(p["display_name"], p["is_provisional"]) for p in all_p if p["player_id"] == x][0])
                p_data = dict(conn.execute("SELECT * FROM players WHERE player_id = ?", (p_pick,)).fetchone())

                st.markdown(f"#### Profile: **{format_pr_name(p_data['display_name'], p_data['is_provisional'])}**")
                c_e = float(p_data.get("graph_centrality", 0.20)); u_opps = float(p_data.get("unique_opponents_count", 0))
                w_g = 1.0 if int(p_data.get("verified_matches_count", 0)) < 5 else min(1.0, c_e / 0.20) * min(1.0, u_opps / 5.0)

                init_cat, _, _, _ = RyftV16.get_cat_for_rating(p_data['initial_rating'], conn=conn)

                c1, c2, c3 = st.columns(3)
                c1.metric("Current MMR", f"{p_data['latent_mmr']:.3f} ({p_data['all_time_badge']})")
                c2.metric("Initial Snapshot", f"{p_data['initial_rating']:.3f} ({init_cat})")
                c3.metric("Sybil Trust (W_G)", f"{w_g*100:.1f}%")

                st.write("**Tri-Gate Status:**")
                g1, g2, g3 = st.columns(3)
                g1.write(f"{'✅' if p_data['verified_matches_count']>=10 else '❌'} Matches: {p_data['verified_matches_count']}/10")
                g2.write(f"{'✅' if p_data['unique_opponents_count']>=5 else '❌'} Opponents: {p_data['unique_opponents_count']}/5")
                g3.write(f"{'✅' if p_data['rating_deviation']<=100.0 else '❌'} RD: {p_data['rating_deviation']:.1f}")

                with st.form("edit_player_form"):
                    e_name = st.text_input("Edit Name", value=p_data["display_name"])
                    e_mmr = st.number_input("Latent MMR Override", value=float(p_data["latent_mmr"]), step=0.01, format="%.3f")
                    e_rd = st.number_input("Rating Deviation (RD)", value=float(p_data["rating_deviation"]), step=5.0)
                    e_man_ver = st.checkbox("Manually Verified (Override Tri-Gate Demotion)", value=bool(p_data["is_manually_verified"]))
                    e_anc = st.checkbox("System Anchor (W_G = 1.0)", value=bool(p_data["is_anchor"]))
                    e_ceil = st.checkbox("Ceiling Anchor", value=bool(p_data["is_ceiling_anchor"]))

                    if st.form_submit_button("Save Overrides"):
                        cat_str, _, _, _ = RyftV16.get_cat_for_rating(e_mmr, conn=conn)
                        conn.execute("UPDATE players SET display_name=?, latent_mmr=?, display_rating=?, rating_deviation=?, is_manually_verified=?, is_anchor=?, is_ceiling_anchor=?, all_time_badge=? WHERE player_id=?", (e_name, e_mmr, e_mmr, e_rd, 1 if e_man_ver else 0, 1 if e_anc else 0, 1 if e_ceil else 0, cat_str, p_pick))
                        if e_man_ver: conn.execute("UPDATE players SET calibration_tier = 'VERIFIED', is_provisional = 0 WHERE player_id = ?", (p_pick,))
                        conn.commit(); st.success("Player updated & Database Synced!"); st.rerun()

                c_del1, c_del2 = st.columns(2)
                if c_del1.button("Deactivate (Soft Delete)"):
                    conn.execute("UPDATE players SET calibration_tier = 'INACTIVE' WHERE player_id = ?", (p_pick,))
                    conn.commit(); st.warning("Player deactivated."); st.rerun()
                if c_del2.button("💣 Force Purge (Delete Record)"):
                    conn.execute("DELETE FROM players WHERE player_id = ?", (p_pick,))
                    conn.commit(); st.warning("Player hard deleted."); st.rerun()
    conn.close()

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
        v_name = st.text_input("Venue Name", key="av_name")
        cities = conn.execute("SELECT location_id, location_name, country_code FROM locations WHERE location_type = 'CITY'").fetchall()
        c_map = {c["location_name"]: c for c in cities}
        v_c = st.selectbox("Assigned City", list(c_map.keys()) if c_map else ["None"], key="av_city")
        v_courts = st.number_input("Court Count", 1, 50, 3, key="av_courts")
        v_ver = st.checkbox("Verified Desk Authority", value=True, key="av_ver")
        if st.button("Register Venue", type="primary"):
            if v_name and c_map:
                v_uuid = f"VEN_{datetime.now().strftime('%H%M%S')}"
                conn.execute("INSERT INTO venues (venue_id, venue_name, city_id, country_code, court_count, is_verified, created_at) VALUES (?, ?, ?, ?, ?, ?, ?)", (v_uuid, v_name, c_map[v_c]["location_id"], c_map[v_c]["country_code"], v_courts, 1 if v_ver else 0, datetime.now(timezone.utc).isoformat()))
                conn.commit(); st.success(f"Added {v_name}!"); st.rerun()

    with ba2.expander("➕ Add City", expanded=False):
        ci_name = st.text_input("City Name", key="ac_name")
        countries = conn.execute("SELECT location_id, location_name, country_code FROM locations WHERE location_type = 'COUNTRY'").fetchall()
        co_map = {c["location_name"]: c for c in countries}
        co_parent = st.selectbox("Parent Country", list(co_map.keys()) if co_map else ["None"], key="ac_parent")
        if st.button("Register City", type="primary"):
            if ci_name and co_map:
                c_uuid = f"LOC_{ci_name[:3].upper()}_{datetime.now().strftime('%S')}"
                conn.execute("INSERT INTO locations (location_id, location_type, location_name, parent_id, country_code, updated_at) VALUES (?, 'CITY', ?, ?, ?, ?)", (c_uuid, ci_name, co_map[co_parent]["location_id"], co_map[co_parent]["country_code"], datetime.now(timezone.utc).isoformat()))
                conn.commit(); st.success(f"Added {ci_name}!"); st.rerun()

    with ba3.expander("➕ Add Country", expanded=False):
        co_name = st.text_input("Country Name", key="aco_name")
        co_code = st.text_input("ISO 3-Letter Code", key="aco_code").upper()
        if st.button("Register Country", type="primary"):
            if co_name and co_code:
                conn.execute("INSERT INTO locations (location_id, location_type, location_name, country_code, updated_at) VALUES (?, 'COUNTRY', ?, ?, ?)""", (f"LOC_{co_code}", co_name, co_code, datetime.now(timezone.utc).isoformat()))
                conn.commit(); st.success(f"Added {co_name}!"); st.rerun()

    st.markdown("---")
    st.markdown("### ✏️ Edit & Inspect Venues")
    v_list = conn.execute("SELECT v.*, l.location_name as city FROM venues v JOIN locations l ON v.city_id = l.location_id WHERE v.is_active = 1").fetchall()
    if v_list:
        v_pick_id = st.selectbox("Select Venue to Edit", [v["venue_id"] for v in v_list], format_func=lambda x: [v["venue_name"] for v in v_list if v["venue_id"] == x][0])
        v_data = dict(conn.execute("SELECT * FROM venues WHERE venue_id = ?", (v_pick_id,)).fetchone())
        
        c_v1, c_v2, c_v3, c_v4 = st.columns(4)
        c_v1.metric("Matches Hosted", v_data["total_matches_played"])
        c_v2.metric("Unique Players", conn.execute("SELECT COUNT(DISTINCT player_id) FROM match_logs ml JOIN matches m ON ml.match_id = m.match_id WHERE m.venue_id = ?", (v_pick_id,)).fetchone()[0])
        c_v3.metric("Sessions Hosted", conn.execute("SELECT COUNT(*) FROM sessions WHERE venue_id = ?", (v_pick_id,)).fetchone()[0])
        c_v4.metric("City Bridges", v_data["city_bridge_matches_count"])

        with st.form("edit_venue_form"):
            ev_name = st.text_input("Venue Name", value=v_data["venue_name"])
            ev_courts = st.number_input("Court Count", min_value=1, value=v_data["court_count"])
            ev_ver = st.checkbox("Verified Desk Authority", value=bool(v_data["is_verified"]))
            if st.form_submit_button("Save Venue Overrides"):
                conn.execute("UPDATE venues SET venue_name=?, court_count=?, is_verified=? WHERE venue_id=?", (ev_name, ev_courts, 1 if ev_ver else 0, v_pick_id))
                conn.commit(); st.success("Venue Updated!"); st.rerun()

    st.markdown("---")
    view_mode = st.radio("Inspect Hierarchy By:", ["Countries", "Cities", "Venues"], horizontal=True)

    if view_mode == "Countries":
        co_list = conn.execute("SELECT * FROM locations WHERE location_type = 'COUNTRY' AND is_active = 1").fetchall()
        for co in co_list:
            p_in_co = conn.execute("SELECT p.latent_mmr, p.all_time_badge FROM players p JOIN locations l ON p.home_city_id = l.location_id WHERE l.parent_id = ? AND p.calibration_tier != 'INACTIVE'", (co["location_id"],)).fetchall()
            with st.expander(f"🌍 {co['location_name']} ({co['country_code']}) • Total Players: {len(p_in_co)}"):
                st.write(f"Country Code: `{co['country_code']}`")
    elif view_mode == "Cities":
        countries = conn.execute("SELECT location_id, location_name FROM locations WHERE location_type = 'COUNTRY' AND is_active = 1").fetchall()
        co_sel = st.selectbox("Filter by Country", ["-- All Countries --"] + [c["location_name"] for c in countries])
        sql = "SELECT * FROM locations WHERE location_type = 'CITY' AND is_active = 1"
        params = []
        if co_sel != "-- All Countries --":
            co_id = [c["location_id"] for c in countries if c["location_name"] == co_sel][0]
            sql += " AND parent_id = ?"; params.append(co_id)
        for ci in conn.execute(sql, params).fetchall():
            p_in_ci = conn.execute("SELECT latent_mmr FROM players WHERE home_city_id = ?", (ci["location_id"],)).fetchall()
            with st.expander(f"🏙️ {ci['location_name']} • Players: {len(p_in_ci)} | Bridges (K): {ci['active_bridge_count']}"):
                st.write(f"Readiness Score: `{ci['readiness_score']:.1f}%` | Hawking Offset: `{ci['hawking_offset']:+.4f}`")
    elif view_mode == "Venues":
        for v in conn.execute("SELECT v.*, l.location_name as city FROM venues v JOIN locations l ON v.city_id = l.location_id WHERE v.is_active = 1").fetchall():
            with st.expander(f"🏟️ {v['venue_name']} ({v['city']}) • Courts: {v['court_count']} | Matches: {v['total_matches_played']}"):
                st.write(f"Verified Desk Authority: `{'YES' if v['is_verified'] else 'NO'}`")
    conn.close()

elif nav == "🌐 Hawking Engine":
    st.title("Hawking Macro Normalization & Regional Diffusion")
    st.caption("Review municipal readiness, traveler bridge density (K), and deploy regularized offsets.")
    conn = get_db_connection()
    cities = conn.execute("SELECT * FROM locations WHERE location_type = 'CITY' AND is_active = 1").fetchall()
    if not cities: st.info("No cities registered in the topology yet.")
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
                    conn.execute("UPDATE players SET latent_mmr = latent_mmr + (? * (latent_mmr / 4.50)), display_rating = display_rating + (? * (latent_mmr / 4.50)) WHERE home_city_id = ? AND is_provisional = 0", (shift, shift, c['location_id']))
                    conn.commit(); st.success(f"Deployed {shift:+0.4f} across verified residents in {c['location_name']}!"); st.rerun()
    conn.close()

elif nav == "⚙️ Global Config":
    st.title("Parameter Matrix & Rule Controller")
    conn = get_db_connection()
    c1, c2 = st.columns([4, 1])
    if c2.button("🔄 Reset Matrix to Defaults", type="secondary"):
        seed_factory_parameters(conn.cursor(), overwrite_existing=True)
        conn.commit(); st.success("All 52 Global Parameters reset to factory V.16 baselines!"); st.rerun()

    tab_prov, tab_ver, tab_fmt, tab_mac = st.tabs(["🚀 Provisional Economy", "⚖️ Verified Economy", "🎾 Match Formats & Tiers", "🌐 System Macros"])
    df = pd.read_sql_query("SELECT * FROM global_config ORDER BY module_group, param_key", conn)

    def render_params_by_group(df, group_names):
        for grp in group_names:
            st.markdown(f"#### 📁 {grp}")
            subset = df[df["module_group"] == grp]
            for _, row in subset.iterrows():
                with st.expander(f"⚙️ {row['param_key']} — {row['title']}"):
                    st.write(row['description'])
                    st.info(f"💡 **Tuning Impact:** {row['tuning_guide']}")
                    c1, c2 = st.columns([3, 1])
                    new_v = c1.number_input("Parameter Value", value=float(row["param_value"]), step=0.05, key=f"val_{row['param_key']}")
                    is_act = c2.checkbox("Active", value=bool(row["is_active"]), key=f"act_{row['param_key']}")
                    if st.button(f"Save {row['param_key']}", key=f"btn_{row['param_key']}"):
                        conn.execute("UPDATE global_config SET param_value=?, is_active=? WHERE param_key=?", (new_v, 1 if is_act else 0, row["param_key"]))
                        conn.commit(); st.toast(f"Saved {row['param_key']} -> {new_v}"); st.rerun()

    with tab_prov: render_params_by_group(df, ["3. Margins & Rightsizing", "7. Accuracy & Tri-Gates"])
    with tab_ver: render_params_by_group(df, ["2. Volatility & Odds", "4. Partner Guardrails", "5. Exchange Caps & Security"])
    with tab_fmt:
        st.markdown("### 🏆 Rating Categories, Ranges & Progression Speeds")
        cats_data = conn.execute("SELECT * FROM rating_categories ORDER BY sort_order ASC").fetchall()
        with st.form("cat_ranges_form"):
            updated_ranges = []
            for cat in cats_data:
                c1, c2, c3, c4 = st.columns([2, 1.5, 1.5, 1.5])
                new_name = c1.text_input(f"Category #{cat['sort_order']}", value=cat["category_name"], key=f"name_{cat['sort_order']}")
                new_min = c2.number_input(f"Min ({new_name})", 0.000, 7.000, float(cat["min_rating"]), 0.050, format="%.3f", key=f"min_{cat['sort_order']}")
                new_max = c3.number_input(f"Max ({new_name})", 0.000, 7.000, float(cat["max_rating"]), 0.050, format="%.3f", key=f"max_{cat['sort_order']}")
                new_speed = c4.number_input(f"Speed ({new_name})", 0.10, 3.00, float(cat["speed_multiplier"] or 1.00), 0.05, format="%.2f", key=f"spd_{cat['sort_order']}")
                updated_ranges.append({"name": new_name, "orig_name": cat["category_name"], "min": new_min, "max": new_max, "speed": new_speed, "order": cat["sort_order"]})

            if st.form_submit_button("Verify & Commit Category Boundaries & Speeds"):
                has_error, err_msg = False, ""
                if updated_ranges[0]["min"] != 0.000: has_error, err_msg = True, "First category must start at 0.000!"
                elif updated_ranges[-1]["max"] != 7.000: has_error, err_msg = True, "Last category must end at 7.000!"
                else:
                    for i in range(len(updated_ranges) - 1):
                        if updated_ranges[i]["min"] >= updated_ranges[i]["max"]:
                            has_error, err_msg = True, f"Category '{updated_ranges[i]['name']}' has Min >= Max!"; break
                if has_error: st.error(f"❌ {err_msg}")
                else:
                    for ur in updated_ranges:
                        conn.execute("UPDATE rating_categories SET category_name=?, min_rating=?, max_rating=?, speed_multiplier=? WHERE sort_order=?", (ur["name"], ur["min"], ur["max"], ur["speed"], ur["order"]))
                    conn.commit(); st.success("Categories and Speeds Saved!"); st.rerun()

        st.markdown("---")
        st.markdown("### 🎾 Official Match Formats & Confidence Multipliers ($M_C$)")
        all_formats = conn.execute("SELECT format_id, format_name, category, mc_weight FROM match_formats ORDER BY category, mc_weight DESC").fetchall()
        with st.expander("🛠️ Edit Format Weights ($M_C$ Multipliers)", expanded=False):
            with st.form("edit_mc_weights_form"):
                updated_mc = {}
                for fmt in all_formats:
                    fc1, fc2, fc3 = st.columns([3, 2, 2])
                    fc1.write(f"**{fmt['format_name']}** (`{fmt['format_id']}`)")
                    fc2.caption(f"Category: {fmt['category']}")
                    updated_mc[fmt["format_id"]] = new_mc = fc3.number_input("Weight", 0.10, 1.50, float(fmt["mc_weight"]), 0.05, key=f"mc_{fmt['format_id']}")
                if st.form_submit_button("Save Format Confidence Weights ($M_C$)"):
                    for fid, weight in updated_mc.items(): conn.execute("UPDATE match_formats SET mc_weight = ? WHERE format_id = ?", (weight, fid))
                    conn.commit(); st.success("Format weights committed!"); st.rerun()

    with tab_mac: render_params_by_group(df, ["1. Core Bounds & Drag", "6. Uncertainty & Rust", "8. Hawking Macro"])
    conn.close()

elif nav == "📄 RYFT Documentation":
    st.title("RYFT Engine V.16.4 — Master Specification Document")
    if REPORTLAB_AVAILABLE:
        st.success("✅ PDF Compiler Engine is active.")
        pdf_bytes = build_pdf_document()
        if pdf_bytes:
            st.download_button("📄 DOWNLOAD RYFT MASTER SPECIFICATION (PDF)", data=pdf_bytes, file_name="Ryft_Engine_Primary_v16_4.pdf", mime="application/pdf", use_container_width=True)
    else: st.error("❌ `reportlab` library missing. Add `reportlab` to requirements.txt")
