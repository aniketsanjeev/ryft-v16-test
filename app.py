import streamlit as st
import sqlite3
import math
import json
import os
import io
from datetime import datetime, timezone, date, time
import pandas as pd

# Optional ReportLab import for PDF generation
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
# 1. DATABASE SCHEMA & CONNECTION MANAGEMENT
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
        cursor.execute("INSERT INTO global_config VALUES (?, ?, ?, ?, ?, ?, ?) ON CONFLICT(param_key) DO UPDATE SET param_value=excluded.param_value, is_active=excluded.is_active", (k, v, act, tit, desc, tune, grp))
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
                except: pass
            
            g_opp = 1.0 / math.sqrt(1.0 + (3.0 * (q**2) * (opp_rd**2)) / (math.pi**2))
            
            if is_quar:
                raw_d = 0.000; flags.append("[ALERT_QUARANTINE_ISOLATION_ACTIVE]")
            elif prov and s_a != s_b:
                opp_team_r = tb_r if is_a else ta_r
                ratio = (g_w_raw + 0.5) / (g_l_raw + 0.5) if won else (g_l_raw + 0.5) / (g_w_raw + 0.5)
                r_perf = opp_team_r + 2.0 * math.log10(ratio)
                raw_d = (r_perf - r) * cfg.get("PROVISIONAL_ABSORPTION_ALPHA", 0.45) * mc * g_opp
                raw_d = max(-cfg.get("MAX_PROVISIONAL_DELTA", 0.750), min(cfg.get("MAX_PROVISIONAL_DELTA", 0.750), raw_d))
                flags.append("RIGHTSIZING_INTERPOLATION")
            else:
                k_base = cfg.get("K_MAX", 0.400) - (r / cfg.get("R_MAX", 7.000)) * (cfg.get("K_MAX", 0.400) - cfg.get("K_MIN", 0.080))
                _, _, _, cat_speed = cls.get_cat_for_rating(r, conn=conn)
                if cat_speed != 1.00: k_base *= cat_speed; flags.append(f"CAT_SPEED ({cat_speed:.2f}x)")
                drag = ((7.000 - r) / 7.000) * ((7.000 - r) / (7.000 - 6.300))**2.5 if r >= 6.300 else 1.0
                if r >= 6.300: flags.append("[ALERT_ELITE_DRAG_MAX_RESISTANCE]")
                raw_d = k_base * drag * mc * s_margin * g_opp * (1.0 if is_a else -1.0) * (act_a - ea)

            if not is_singles and partner and not is_quar:
                gap = abs(r - float(partner.get("latent_mmr", 3.0)))
                part_prov = bool(partner.get("is_provisional", 1))
                part_rd = float(partner.get("rating_deviation", 350.0))
                
                if prov and part_prov and rd > 200.0 and part_rd > 200.0:
                    flags.append("MUTUAL_PROV_EXEMPTION")
                else:
                    if not won and r > float(partner.get("latent_mmr", 3.0)):
                        dd = cfg.get("ICE_OUT_MULT_TIER_2", 0.05) if gap >= cfg.get("ICE_OUT_GAP_TIER_2", 2.00) else (cfg.get("ICE_OUT_MULT_TIER_1", 0.20) if gap >= cfg.get("ICE_OUT_GAP_TIER_1", 1.50) else (0.50 if gap >= 1.0 else 1.00))
                        raw_d *= dd
                        if dd < 1.00: flags.append(f"[ALERT_ICE_OUT_ANCHOR_SHIELD] ({int((1-dd)*100)}%)")
                    elif won and r < float(partner.get("latent_mmr", 3.0)):
                        dd = cfg.get("ANTI_CARRY_MULT_TIER_2", 0.25) if gap >= cfg.get("ANTI_CARRY_GAP_TIER_2", 2.50) else (cfg.get("ANTI_CARRY_MULT_TIER_1", 0.50) if gap >= cfg.get("ANTI_CARRY_GAP_TIER_1", 1.75) else (0.75 if gap >= 1.2 else 1.00))
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
            
            if is_tournament and bool(cfg.get("TOURNAMENT_MULTIPLIER_ACTIVE", 1)):
                t_mult = cfg.get("TOURNAMENT_STAKES_MULTIPLIER", 1.15)
                final_d = raw_d * t_mult; flags.append(f"TOURNAMENT ({t_mult}x, Uncapped)")
            elif cumulative_deltas is not None:
                cum_d = cumulative_deltas.get(p["player_id"], 0.0)
                final_d = max(-cap - cum_d, min(cap - cum_d, raw_d))
                if abs(cum_d + raw_d) > cap: flags.append("SESSION_CUMULATIVE_CAP_ENFORCED")
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

# ==============================================================================
# 3. ADVANCED SESSIONS SCHEDULER (V16.2 PROD BLUEPRINT)
# ==============================================================================
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

# ==============================================================================
# 4. TAB ROUTING
# ==============================================================================
if nav == "👥 Player Roster & Calibration":
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
            p_name = st.text_input("Full Name", key="add_p_name")
            in_cats = ["Beginner (0.500)", "Beginner+ (1.000)", "Intermediate (2.500)", "Intermediate+ (3.500)", "Advanced (4.500)", "Pro (5.500)", "Elite (6.300)"]
            in_pick = st.selectbox("Base Calibration Category", in_cats, key="add_p_cat")
            
            # Non-blocking Cascading Dropdowns
            countries = conn.execute("SELECT DISTINCT country_code FROM locations WHERE location_type = 'CITY'").fetchall()
            c_codes = [c["country_code"] for c in countries] if countries else ['IND']
            sel_co = st.selectbox("Home Country", c_codes, key="add_p_co")
            
            cities = conn.execute("SELECT location_id, location_name FROM locations WHERE location_type = 'CITY' AND country_code = ?", (sel_co,)).fetchall()
            city_map = {c["location_name"]: c["location_id"] for c in cities}
            sel_ci = st.selectbox("Home City", list(city_map.keys()) if city_map else ["None"], key="add_p_ci")
            
            venues = conn.execute("SELECT venue_id, venue_name FROM venues WHERE is_active = 1 AND city_id = ?", (city_map.get(sel_ci),)).fetchall() if city_map else []
            v_map = {v["venue_name"]: v["venue_id"] for v in venues}
            sel_ve = st.selectbox("Home Club / Venue (Optional)", ["None"] + list(v_map.keys()), key="add_p_ve")

            c_a1, c_a2 = st.columns(2)
            is_anc = c_a1.checkbox("System Anchor", key="add_p_anc")
            is_ceil = c_a2.checkbox("Ceiling Anchor", key="add_p_ceil")

            if st.button("Commit Registration", type="primary"):
                if not city_map: st.error("Create at least one City in 'Venues & Regions' first.")
                elif not p_name: st.error("Player name cannot be blank.")
                else:
                    base_map = {"Beginner (0.500)": 0.500, "Beginner+ (1.000)": 1.000, "Intermediate (2.500)": 2.500, "Intermediate+ (3.500)": 3.500, "Advanced (4.500)": 4.500, "Pro (5.500)": 5.500, "Elite (6.300)": 6.300}
                    base_r = base_map[in_pick]
                    v_id = v_map.get(sel_ve)
                    p_uuid = f"P_{datetime.now().strftime('%d%H%M%S')}"

                    conn.execute("""
                        INSERT INTO players (player_id, display_name, initial_rating, home_venue_id, home_city_id, home_country_code, latent_mmr, display_rating, rolling_90d_peak, rolling_180d_peak, rolling_365d_peak, tournament_floor, all_time_badge, rating_deviation, rating_accuracy_pct, accuracy_s_rd, accuracy_s_matches, accuracy_s_diversity, calibration_tier, is_provisional, is_manually_verified, is_anchor, is_ceiling_anchor, created_at)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0.0, ?, 350.0, 0.0, 0.0, 0.0, 0.0, 'PROVISIONAL', 1, 0, ?, ?, ?)
                    """, (p_uuid, p_name, base_r, v_id, city_map[sel_ci], sel_co, base_r, base_r, base_r, base_r, base_r, in_pick.split(" ")[0], 1 if is_anc else 0, 1 if is_ceil else 0, datetime.now(timezone.utc).isoformat()))
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

                c1, c2, c3 = st.columns(3)
                c1.metric("Current MMR", f"{p_data['latent_mmr']:.3f} ({p_data['all_time_badge']})")
                c2.metric("Initial Snapshot", f"{p_data['initial_rating']:.3f}")
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
                        conn.commit(); st.success("Player updated!"); st.rerun()

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
                conn.execute("INSERT INTO locations (location_id, location_type, location_name, country_code, updated_at) VALUES (?, 'COUNTRY', ?, ?, ?)", (f"LOC_{co_code}", co_name, co_code, datetime.now(timezone.utc).isoformat()))
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
                    updated_mc[fmt["format_id"]] = fc3.number_input("Weight", 0.10, 1.50, float(fmt["mc_weight"]), 0.05, key=f"mc_{fmt['format_id']}")
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
