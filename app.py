import io
import json
import math
import os
import random
import sqlite3
import uuid
from datetime import date, datetime, time, timezone
import numpy as np
import pandas as pd
import streamlit as st

try:
  from reportlab.lib import colors
  from reportlab.lib.pagesizes import letter
  from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
  from reportlab.platypus import (
      HRFlowable,
      Paragraph,
      SimpleDocTemplate,
      Spacer,
      Table,
      TableStyle,
  )

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
  with open(temp_file, "rb") as f:
    data = f.read()
  if os.path.exists(temp_file):
    os.remove(temp_file)
  return data


def restore_db_from_bytes(uploaded_bytes):
  temp_in = "temp_incoming_restore.db"
  with open(temp_in, "wb") as f:
    f.write(uploaded_bytes)
  source = sqlite3.connect(temp_in)
  tables = [
      r[0]
      for r in source.execute(
          "SELECT name FROM sqlite_master WHERE type='table'"
      ).fetchall()
  ]
  if "players" not in tables or "global_config" not in tables:
    source.close()
    os.remove(temp_in)
    raise ValueError("Invalid RYFT database snapshot.")
  source.execute("PRAGMA wal_checkpoint(TRUNCATE);")
  dest = get_db_connection()
  dest.execute("PRAGMA wal_checkpoint(TRUNCATE);")
  source.backup(dest)
  source.close()
  dest.execute("PRAGMA wal_checkpoint(TRUNCATE);")
  dest.commit()
  dest.close()
  if os.path.exists(temp_in):
    os.remove(temp_in)


def init_db():
  conn = get_db_connection()
  c = conn.cursor()
  c.execute("PRAGMA foreign_keys = ON;")
  c.execute("""CREATE TABLE IF NOT EXISTS locations (
        location_id TEXT PRIMARY KEY, location_type TEXT NOT NULL, location_name TEXT NOT NULL, 
        parent_id TEXT, country_code TEXT DEFAULT 'IND', intransitivity_idx REAL DEFAULT 0.0, 
        intransitivity_index REAL DEFAULT 0.0, hawking_offset REAL DEFAULT 0.0, suggested_offset REAL DEFAULT 0.0, 
        readiness_score REAL DEFAULT 0.0, active_bridge_count INTEGER DEFAULT 0, total_active_players INTEGER DEFAULT 0, 
        total_matches_played INTEGER DEFAULT 0, active_venues_count INTEGER DEFAULT 0, median_latent_mmr REAL DEFAULT 3.000, 
        highest_player_mmr REAL DEFAULT 3.000, lowest_player_mmr REAL DEFAULT 3.000, is_normalized INTEGER DEFAULT 0, 
        updated_at TEXT, is_active INTEGER DEFAULT 1
    )""")
  c.execute("""CREATE TABLE IF NOT EXISTS venues (
        venue_id TEXT PRIMARY KEY, venue_name TEXT NOT NULL, raw_input_name TEXT, is_verified INTEGER DEFAULT 0, 
        city_id TEXT NOT NULL, country_code TEXT NOT NULL, court_count INTEGER DEFAULT 1, total_matches_played INTEGER DEFAULT 0, 
        unique_players_count INTEGER DEFAULT 0, city_bridge_matches_count INTEGER DEFAULT 0, country_bridge_matches_count INTEGER DEFAULT 0, 
        average_player_mmr REAL DEFAULT 3.000, is_active INTEGER DEFAULT 1, created_at TEXT
    )""")
  c.execute("""CREATE TABLE IF NOT EXISTS rating_categories (
        category_name TEXT PRIMARY KEY, min_rating REAL NOT NULL, max_rating REAL NOT NULL, 
        sort_order INTEGER NOT NULL, speed_multiplier REAL DEFAULT 1.00
    )""")
  c.execute("""CREATE TABLE IF NOT EXISTS match_formats (
        format_id TEXT PRIMARY KEY, format_name TEXT NOT NULL, category TEXT NOT NULL, mc_weight REAL NOT NULL, 
        target_games INTEGER, total_points INTEGER, is_session_bound INTEGER DEFAULT 0, is_active INTEGER DEFAULT 1
    )""")
  c.execute("""CREATE TABLE IF NOT EXISTS players (
        player_id TEXT PRIMARY KEY, display_name TEXT NOT NULL, initial_rating REAL NOT NULL, home_venue_id TEXT, 
        home_city_id TEXT NOT NULL, home_country_code TEXT NOT NULL DEFAULT 'IND', latent_mmr REAL NOT NULL, 
        display_rating REAL NOT NULL, rolling_90d_peak REAL NOT NULL DEFAULT 3.000, rolling_180d_peak REAL NOT NULL DEFAULT 3.000, 
        rolling_365d_peak REAL NOT NULL DEFAULT 3.000, tournament_floor REAL NOT NULL DEFAULT 0.000, 
        tournament_floor_rating REAL NOT NULL DEFAULT 0.000, all_time_badge TEXT DEFAULT 'Intermediate', 
        consecutive_losses INTEGER DEFAULT 0, rating_deviation REAL NOT NULL DEFAULT 350.000, rating_accuracy_pct REAL DEFAULT 0.0, 
        accuracy_s_rd REAL DEFAULT 0.0, accuracy_s_matches REAL DEFAULT 0.0, accuracy_s_diversity REAL DEFAULT 0.0, 
        calibration_tier TEXT DEFAULT 'PROVISIONAL', is_provisional INTEGER DEFAULT 1, is_manually_verified INTEGER DEFAULT 0, 
        verified_matches_count INTEGER DEFAULT 0, unique_opponents_count INTEGER DEFAULT 0, unique_partners_count INTEGER DEFAULT 0, 
        unique_venues_count INTEGER DEFAULT 0, unique_cities_count INTEGER DEFAULT 0, unique_countries_count INTEGER DEFAULT 0, 
        bridge_matches_count INTEGER DEFAULT 0, is_active_bridge INTEGER DEFAULT 0, is_country_bridge INTEGER DEFAULT 0, 
        graph_centrality REAL DEFAULT 0.20, is_quarantined INTEGER DEFAULT 0, is_anchor INTEGER DEFAULT 0, 
        is_ceiling_anchor INTEGER DEFAULT 0, is_dummy INTEGER DEFAULT 0, last_match_time TEXT, created_at TEXT
    )""")
  c.execute("""CREATE TABLE IF NOT EXISTS sessions (
        session_id TEXT PRIMARY KEY, venue_id TEXT NOT NULL, session_title TEXT NOT NULL, session_date TEXT NOT NULL DEFAULT '', 
        start_time TEXT NOT NULL DEFAULT '09:00', end_time TEXT NOT NULL DEFAULT '11:00', match_mode TEXT NOT NULL DEFAULT 'DOUBLES', 
        team_format TEXT NOT NULL, format_id TEXT NOT NULL, tourney_structure TEXT NOT NULL DEFAULT 'ROUND_ROBIN', 
        is_tournament INTEGER DEFAULT 0, court_ids_json TEXT NOT NULL, enrolled_player_ids TEXT NOT NULL, teams_json TEXT DEFAULT '[]', 
        checked_in_player_ids TEXT DEFAULT '[]', player_count INTEGER NOT NULL, active_checked_in_count INTEGER DEFAULT 0, 
        total_rounds INTEGER NOT NULL DEFAULT 1, current_round INTEGER DEFAULT 0, session_status TEXT DEFAULT 'CONFIG', 
        created_at TEXT NOT NULL, completed_at TEXT, current_stage TEXT, lineup_mode TEXT
    )""")
  c.execute("""CREATE TABLE IF NOT EXISTS session_matches (
        session_match_id TEXT PRIMARY KEY, session_id TEXT NOT NULL, round_number INTEGER NOT NULL, court_id TEXT NOT NULL, 
        match_order INTEGER NOT NULL, team_a_p1_id TEXT NOT NULL, team_a_p2_id TEXT, team_b_p1_id TEXT NOT NULL, 
        team_b_p2_id TEXT, team_a_name TEXT DEFAULT '', team_b_name TEXT DEFAULT '', score_team_a INTEGER DEFAULT 0, 
        score_team_b INTEGER DEFAULT 0, games_winner INTEGER DEFAULT 0, games_loser INTEGER DEFAULT 0, 
        set_scores_json TEXT DEFAULT '[]', match_status TEXT DEFAULT 'SCHEDULED', started_at TEXT, completed_at TEXT, 
        committed_match_id TEXT, stage TEXT, group_id TEXT, flight_number INTEGER DEFAULT 1
    )""")
  c.execute("""CREATE TABLE IF NOT EXISTS matches (
        match_id TEXT PRIMARY KEY, venue_id TEXT NOT NULL, format_id TEXT NOT NULL, session_id TEXT, is_singles INTEGER DEFAULT 0, 
        is_tournament INTEGER DEFAULT 0, is_venue_bridge INTEGER DEFAULT 0, is_city_bridge INTEGER DEFAULT 0, 
        is_country_bridge INTEGER DEFAULT 0, team_a_p1_id TEXT NOT NULL, team_a_p2_id TEXT, team_b_p1_id TEXT NOT NULL, 
        team_b_p2_id TEXT, score_team_a INTEGER DEFAULT 0, score_team_b INTEGER DEFAULT 0, set_scores_json TEXT DEFAULT '[]', 
        games_winner INTEGER DEFAULT 0, games_loser INTEGER DEFAULT 0, pre_rating_a REAL DEFAULT 3.000, pre_rating_b REAL DEFAULT 3.000, 
        win_expectancy_a REAL DEFAULT 0.5000, applied_m_c REAL DEFAULT 1.00, applied_s_margin REAL DEFAULT 1.000, 
        delta_r_p1 REAL DEFAULT 0.0, delta_r_p2 REAL DEFAULT 0.0, delta_r_p3 REAL DEFAULT 0.0, delta_r_p4 REAL DEFAULT 0.0, 
        guardrails_summary TEXT DEFAULT '[]', is_retroactive INTEGER DEFAULT 0, match_timestamp TEXT NOT NULL, processed_at TEXT
    )""")
  c.execute("""CREATE TABLE IF NOT EXISTS match_logs (
        log_id TEXT PRIMARY KEY, match_id TEXT NOT NULL, player_id TEXT NOT NULL, pre_latent_mmr REAL NOT NULL, 
        post_latent_mmr REAL NOT NULL, pre_display_rating REAL NOT NULL, post_display_rating REAL NOT NULL, 
        pre_rd REAL NOT NULL, post_rd REAL NOT NULL, pre_accuracy_pct REAL NOT NULL, post_accuracy_pct REAL NOT NULL, 
        delta_r REAL NOT NULL, is_elevator_active INTEGER DEFAULT 0, guardrails_triggered TEXT DEFAULT '[]', 
        is_retroactive INTEGER DEFAULT 0, logged_at TEXT NOT NULL
    )""")
  c.execute("""CREATE TABLE IF NOT EXISTS global_config (
        param_key TEXT PRIMARY KEY, param_value REAL NOT NULL, is_active INTEGER DEFAULT 1, 
        title TEXT, description TEXT, tuning_guide TEXT, module_group TEXT
    )""")
  c.execute("""CREATE TABLE IF NOT EXISTS tournaments (
        tourney_id TEXT PRIMARY KEY, name TEXT NOT NULL, venue_id TEXT NOT NULL, tourney_date TEXT NOT NULL, 
        floor_category TEXT NOT NULL, status TEXT DEFAULT 'PENDING', created_at TEXT NOT NULL
    )""")
  c.execute("""CREATE TABLE IF NOT EXISTS tourney_matches (
        t_match_id TEXT PRIMARY KEY, tourney_id TEXT NOT NULL, match_time TEXT NOT NULL, format_id TEXT NOT NULL, 
        is_singles INTEGER DEFAULT 0, team_a_p1_id TEXT NOT NULL, team_a_p2_id TEXT, team_b_p1_id TEXT NOT NULL, 
        team_b_p2_id TEXT, score_team_a INTEGER DEFAULT 0, score_team_b INTEGER DEFAULT 0, games_winner INTEGER DEFAULT 0, 
        games_loser INTEGER DEFAULT 0, set_scores_json TEXT DEFAULT '[]', status TEXT DEFAULT 'STAGED'
    )""")
  c.execute("""CREATE TABLE IF NOT EXISTS session_rosters (
        roster_id TEXT PRIMARY KEY, session_id TEXT NOT NULL, entity_type TEXT, player_id_p1 TEXT, player_id_p2 TEXT, 
        display_label TEXT, group_id TEXT DEFAULT 'A', seed_index INTEGER DEFAULT 0, initial_mmr REAL DEFAULT 3.0, 
        initial_rd REAL DEFAULT 350.0, is_checked_in INTEGER DEFAULT 0, is_defected INTEGER DEFAULT 0, matches_played INTEGER DEFAULT 0, 
        matches_won INTEGER DEFAULT 0, matches_lost INTEGER DEFAULT 0, matches_tied INTEGER DEFAULT 0, standing_points INTEGER DEFAULT 0, 
        games_for INTEGER DEFAULT 0, games_against INTEGER DEFAULT 0, net_game_diff INTEGER DEFAULT 0, points_for INTEGER DEFAULT 0, 
        points_against INTEGER DEFAULT 0, net_point_diff INTEGER DEFAULT 0, consecutive_sit INTEGER DEFAULT 0, is_qualified INTEGER DEFAULT 0, 
        knockout_seed INTEGER
    )""")
  c.execute("""CREATE TABLE IF NOT EXISTS synthetic_ghosts (
        ghost_id TEXT PRIMARY KEY, ghost_name TEXT NOT NULL, 
        playstyle TEXT NOT NULL CHECK (playstyle IN ('AGGRESSIVE', 'BALANCED', 'CONSERVATIVE')), 
        assigned_mmr REAL NOT NULL, target_city TEXT NOT NULL, is_active INTEGER NOT NULL DEFAULT 1, 
        sims_run_count INTEGER NOT NULL DEFAULT 0, created_at TEXT DEFAULT CURRENT_TIMESTAMP
    )""")

  add_column_if_not_exists(c, "players", "consecutive_losses", "INTEGER DEFAULT 0")
  add_column_if_not_exists(
      c, "players", "tournament_floor_rating", "REAL DEFAULT 0.0"
  )
  add_column_if_not_exists(
      c, "locations", "intransitivity_index", "REAL DEFAULT 0.0"
  )

  # SEED 6 OFFICIAL RYFT CATEGORY BENCHMARK GHOSTS
  g_count = c.execute("SELECT COUNT(*) FROM synthetic_ghosts").fetchone()[0]
  if g_count == 0:
    default_ghosts = [
        (
            "GHOST_BEG_PLUS_15",
            "Ghost Beginner+ 1.5",
            "BALANCED",
            1.5000,
            "NATIONAL",
            1,
            0,
        ),
        (
            "GHOST_INT_25",
            "Ghost Intermediate 2.5",
            "BALANCED",
            2.5000,
            "NATIONAL",
            1,
            0,
        ),
        (
            "GHOST_INT_PLUS_35",
            "Ghost Intermediate+ 3.5",
            "BALANCED",
            3.5000,
            "NATIONAL",
            1,
            0,
        ),
        (
            "GHOST_ADV_45",
            "Ghost Advanced 4.5",
            "BALANCED",
            4.5000,
            "NATIONAL",
            1,
            0,
        ),
        (
            "GHOST_PRO_55",
            "Ghost Pro 5.5",
            "BALANCED",
            5.5000,
            "NATIONAL",
            1,
            0,
        ),
        (
            "GHOST_ELITE_65",
            "Ghost Elite 6.5",
            "BALANCED",
            6.5000,
            "NATIONAL",
            1,
            0,
        ),
    ]
    c.executemany(
        """INSERT INTO synthetic_ghosts (ghost_id, ghost_name, playstyle, assigned_mmr, target_city, is_active, sims_run_count) 
           VALUES (?, ?, ?, ?, ?, ?, ?)""",
        default_ghosts,
    )

  if c.execute("SELECT COUNT(*) FROM rating_categories").fetchone()[0] == 0:
    cats = [
        ("Beginner", 0.0, 0.999, 1, 1.0),
        ("Beginner+", 1.0, 1.999, 2, 1.0),
        ("Intermediate", 2.0, 3.499, 3, 1.0),
        ("Intermediate+", 3.5, 4.499, 4, 1.0),
        ("Advanced", 4.5, 5.499, 5, 1.0),
        ("Pro", 5.5, 6.299, 6, 1.0),
        ("Elite", 6.3, 7.0, 7, 1.0),
    ]
    for cn, cmn, cmx, so, sm in cats:
      c.execute(
          "INSERT OR IGNORE INTO rating_categories VALUES (?,?,?,?,?)",
          (cn, cmn, cmx, so, sm),
      )

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
      ("MEX_32", "Mexicano 32 Points", "MEXICANO", 0.35, None, 32, 0, 1),
  ]
  for fid, fname, cat, mc, tg, tp, is_sb, is_a in official_formats:
    c.execute(
        """INSERT INTO match_formats (format_id, format_name, category, mc_weight, target_games, total_points, is_session_bound, is_active)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?)
           ON CONFLICT(format_id) DO UPDATE SET 
               format_name=excluded.format_name, category=excluded.category, target_games=excluded.target_games,
               total_points=excluded.total_points, is_session_bound=excluded.is_session_bound, is_active=excluded.is_active""",
        (fid, fname, cat, mc, tg, tp, is_sb, is_a),
    )

  seed_factory_parameters(c, overwrite_existing=False)
  conn.commit()
  conn.close()


def seed_factory_parameters(cursor, overwrite_existing=False):
  master_params = [
      (
          "R_MIN",
          0.000,
          1,
          "Scale Absolute Floor",
          "Lowest possible rating.",
          "Clamps rating drops at 0.000.",
          "1. Core Bounds & Drag",
      ),
      (
          "R_MAX",
          7.000,
          1,
          "Scale Absolute Ceiling",
          "Maximum rating ceiling.",
          "LOCKED at 7.000.",
          "1. Core Bounds & Drag",
      ),
      (
          "R_ELITE_THRESHOLD",
          6.300,
          1,
          "Elite Drag Gate",
          "Rating where drag starts.",
          "Lowering applies drag earlier.",
          "1. Core Bounds & Drag",
      ),
      (
          "ELITE_DRAG_EXPONENT",
          2.5,
          1,
          "Elite Drag Curvature",
          "Steepness of ceiling resistance.",
          "Higher values block 7.000.",
          "1. Core Bounds & Drag",
      ),
      (
          "POWER_MEAN_P",
          3.0,
          1,
          "Doubles Cubic Exponent",
          "Power mean anchor exponent.",
          "3.0 gives 70/30 anchor bias.",
          "2. Volatility & Odds",
      ),
      (
          "LOGISTIC_BETA",
          2.0,
          1,
          "Logistic Scale Factor",
          "Odds curve steepness.",
          "Lowering boosts upset deltas.",
          "2. Volatility & Odds",
      ),
      (
          "K_MAX",
          0.400,
          1,
          "Beginner Max Volatility",
          "Step size at R=0.000.",
          "Higher values accelerate progression.",
          "2. Volatility & Odds",
      ),
      (
          "K_MIN",
          0.080,
          1,
          "Pro Min Volatility",
          "Step size at R=7.000.",
          "Lower values lock pro ratings.",
          "2. Volatility & Odds",
      ),
      (
          "MARGIN_BASE",
          0.80,
          1,
          "Margin Floor Factor",
          "Min score factor for close matches.",
          "Points floor for tight finishes.",
          "3. Margins & Rightsizing",
      ),
      (
          "MARGIN_SCALE",
          0.40,
          1,
          "Margin Blowout Scale",
          "Max bonus factor for blowouts.",
          "Full blowout bonus = Base + Scale.",
          "3. Margins & Rightsizing",
      ),
      (
          "MAX_PROVISIONAL_DELTA",
          0.750,
          1,
          "Placement Ceiling",
          "Max points won in interpolation.",
          "Single-match placement cap.",
          "3. Margins & Rightsizing",
      ),
      (
          "PROVISIONAL_ABSORPTION_ALPHA",
          0.45,
          1,
          "Rightsizing Velocity",
          "Speed toward performance rating.",
          "Higher = faster rightsizing.",
          "3. Margins & Rightsizing",
      ),
      (
          "PROVISIONAL_BYPASS_EXCHANGE_CAP",
          1,
          1,
          "Provisional Cap Bypass",
          "Allows rightsizing blowouts to reach placement ceiling.",
          "Default 1 (Active).",
          "3. Margins & Rightsizing",
      ),
      (
          "DISPLAY_RATING_SOFT_FLOOR",
          0.050,
          1,
          "Display Soft Floor",
          "Buffer preventing minor drops.",
          "Default 0.050.",
          "3. Margins & Rightsizing",
      ),
      (
          "ICE_OUT_GAP_TIER_1",
          1.50,
          1,
          "Ice-Out Gap Threshold 1",
          "Min partner gap to trigger 20% dampening.",
          "Applies to Anchor.",
          "4. Partner Guardrails",
      ),
      (
          "ICE_OUT_MULT_TIER_1",
          0.20,
          1,
          "Ice-Out Dampener 1",
          "Multiplier applied if Tier 1 Gap breached.",
          "0.20 = 80% loss reduction.",
          "4. Partner Guardrails",
      ),
      (
          "ICE_OUT_GAP_TIER_2",
          2.00,
          1,
          "Ice-Out Gap Threshold 2",
          "Min partner gap to trigger 5% dampening.",
          "Extreme freeze-outs.",
          "4. Partner Guardrails",
      ),
      (
          "ICE_OUT_MULT_TIER_2",
          0.05,
          1,
          "Ice-Out Dampener 2",
          "Multiplier applied if Tier 2 Gap breached.",
          "0.05 = 95% loss reduction.",
          "4. Partner Guardrails",
      ),
      (
          "ANTI_CARRY_GAP_TIER_1",
          1.75,
          1,
          "Anti-Carry Gap Threshold 1",
          "Min gap to trigger 50% carry dampening.",
          "Applies to weaker partner.",
          "4. Partner Guardrails",
      ),
      (
          "ANTI_CARRY_MULT_TIER_1",
          0.50,
          1,
          "Anti-Carry Dampener 1",
          "Multiplier applied if Tier 1 carry breached.",
          "0.50 = 50% gain reduction.",
          "4. Partner Guardrails",
      ),
      (
          "ANTI_CARRY_GAP_TIER_2",
          2.50,
          1,
          "Anti-Carry Gap Threshold 2",
          "Min gap to trigger 25% carry dampening.",
          "Extreme tow jobs.",
          "4. Partner Guardrails",
      ),
      (
          "ANTI_CARRY_MULT_TIER_2",
          0.25,
          1,
          "Anti-Carry Dampener 2",
          "Multiplier applied if Tier 2 carry breached.",
          "0.25 = 75% gain reduction.",
          "4. Partner Guardrails",
      ),
      (
          "MUTUAL_PROV_MAX_PARTNER_GAP",
          1.500,
          1,
          "Mutual Prov Max Partner Gap",
          "Max initial gap between unrated teammates before Anti-Carry engages.",
          "Blocks true beginners from inflating off self-declared 4.0+ friends.",
          "4. Partner Guardrails",
      ),
      (
          "MAX_24H_PAIRWISE_EXCHANGE_CAP",
          0.150,
          1,
          "24H Pairwise Anti-Collusion Cap",
          "Max net transfer between specific opponent cluster in 24h.",
          "Targeted anti-collusion.",
          "5. Exchange Caps & Security",
      ),
      (
          "MAX_24H_GLOBAL_CASUAL_CAP",
          0.250,
          1,
          "24H Global Daily Casual Governor",
          "Max cumulative net casual points across all opponents in 24h.",
          "Macro daily movement governor.",
          "5. Exchange Caps & Security",
      ),
      (
          "MAX_24H_EXCHANGE_CAP",
          0.150,
          1,
          "24H Casual Cap (Legacy)",
          "Fallback single-window cap ceiling.",
          "Legacy parameter.",
          "5. Exchange Caps & Security",
      ),
      (
          "PROVISIONAL_CAP_MULTIPLIER",
          2.5,
          1,
          "Provisional Cap Relaxer",
          "Multiplier on 24H cap for PRs.",
          "Allows accelerated placement.",
          "5. Exchange Caps & Security",
      ),
      (
          "SESSION_EXCHANGE_CAP",
          0.300,
          1,
          "Verified Session Cap",
          "Cap for verified club events.",
          "Doubles point limits for mixers.",
          "5. Exchange Caps & Security",
      ),
      (
          "MIN_SESSION_PLAYERS",
          6,
          1,
          "Session Participant Floor",
          "Min players required to unlock session cap.",
          "Events with fewer revert to casual caps.",
          "5. Exchange Caps & Security",
      ),
      (
          "GRAPH_DAMPENING_MIN_OPPONENTS",
          5.0,
          1,
          "Graph Dampening Min Opponents",
          "Minimum distinct opponents required before W_G reaches 1.0.",
          "Increase in large clubs to curb clique farming.",
          "5. Exchange Caps & Security",
      ),
      (
          "GRAPH_CENTRALITY_TARGET",
          0.200,
          1,
          "Graph Centrality Target",
          "Eigenvector centrality baseline required before full rating gains.",
          "Lower for casual recreational venues; raise for league finals.",
          "5. Exchange Caps & Security",
      ),
      (
          "TOURNAMENT_MULTIPLIER_ACTIVE",
          1,
          1,
          "Tournament Multiplier Toggle",
          "Activates stakes multiplier for tournament.",
          "1 = Active.",
          "5. Exchange Caps & Security",
      ),
      (
          "TOURNAMENT_STAKES_MULTIPLIER",
          1.15,
          1,
          "Tournament Stakes Multiplier",
          "Rating delta multiplier for tournaments.",
          "Default 1.15 (+15%).",
          "5. Exchange Caps & Security",
      ),
      (
          "RD_MIN",
          30.0,
          1,
          "Certainty Floor",
          "Absolute uncertainty floor.",
          "Prevents RD dropping below 30.0.",
          "6. Uncertainty & Rust",
      ),
      (
          "RD_MAX",
          350.0,
          1,
          "Unrated Starting RD",
          "Uncertainty assigned at registration.",
          "Starting uncertainty.",
          "6. Uncertainty & Rust",
      ),
      (
          "RD_INFO_VARIANCE",
          110.0,
          1,
          "Contraction Speed",
          "Denominator in RD shrinkage. Higher values contract RD slower.",
          "Tuned to 110.0 for gradual, secure contraction.",
          "6. Uncertainty & Rust",
      ),
      (
          "INACTIVITY_CONSTANT",
          12.0,
          1,
          "Inactivity Rust Rate (Monthly)",
          "Monthly uncertainty growth for background cron cycles.",
          "Points of RD regained per month.",
          "6. Uncertainty & Rust",
      ),
      (
          "TEMPORAL_DRIFT_CONSTANT",
          5.50,
          1,
          "Inactivity Drift Constant (c)",
          "Daily uncertainty expansion constant.",
          "Default 5.50.",
          "6. Uncertainty & Rust",
      ),
      (
          "INACTIVITY_GRACE_DAYS",
          7.0,
          1,
          "Inactivity Grace Window (Days)",
          "Days before temporal rust begins accumulating.",
          "Default 7.0 days.",
          "6. Uncertainty & Rust",
      ),
      (
          "INACTIVITY_REPROVISION_THRESHOLD",
          100.0,
          1,
          "Inactivity Reprovisioning RD Ceiling",
          "RD threshold where inactive players are flagged for [PR].",
          "Default 100.0.",
          "6. Uncertainty & Rust",
      ),
      (
          "COHORT_FACTOR_0_PROV",
          1.00,
          1,
          "Omega 0 Factor",
          "Contraction against verified anchors.",
          "100% gain.",
          "6. Uncertainty & Rust",
      ),
      (
          "COHORT_FACTOR_1_PROV",
          0.75,
          1,
          "Omega 1 Factor",
          "Contraction with 1 unrated player.",
          "75% gain.",
          "6. Uncertainty & Rust",
      ),
      (
          "COHORT_FACTOR_2_PROV",
          0.50,
          1,
          "Omega 2 Factor",
          "Contraction with 2 unrated players.",
          "50% gain.",
          "6. Uncertainty & Rust",
      ),
      (
          "COHORT_FACTOR_3_PROV",
          0.25,
          1,
          "Omega 3 Factor",
          "Contraction with 3+ unrated players.",
          "25% sandbox.",
          "6. Uncertainty & Rust",
      ),
      (
          "PROVISIONAL_RD_CONTRACTION_RATIO",
          0.20,
          1,
          "Provisional RD Shrink Modifier",
          "Slows RD drop for unrated players.",
          "Tuned to 0.20 to keep players provisional across adequate sample.",
          "7. Accuracy & Tri-Gates",
      ),
      (
          "PROVISIONAL_ACCURACY_DAMPENER",
          0.40,
          1,
          "Provisional Accuracy Gain Cap",
          "Restricts accuracy gain during placement.",
          "Caps visual accuracy.",
          "7. Accuracy & Tri-Gates",
      ),
      (
          "ACCURACY_HIGH_TRUST_FLOOR",
          65.0,
          1,
          "Accuracy High-Trust Threshold",
          "Inflection boundary where rapid linear ramp transitions to asymptotic ascent.",
          "Ratings above 65% are considered established.",
          "7. Accuracy & Tri-Gates",
      ),
      (
          "ACCURACY_TARGET_MATCHES_ELITE",
          100.0,
          1,
          "100% Accuracy Matches Quota",
          "Match volume required to reach near-100% accuracy.",
          "Demands deep match volume.",
          "7. Accuracy & Tri-Gates",
      ),
      (
          "ACCURACY_TARGET_OPPONENTS_ELITE",
          50.0,
          1,
          "100% Accuracy Opponents Quota",
          "Unique opponents required to reach near-100% accuracy.",
          "Prevents club clique inflation.",
          "7. Accuracy & Tri-Gates",
      ),
      (
          "ACCURACY_TARGET_CITY_BRIDGES",
          25.0,
          1,
          "100% Accuracy City Bridges Quota",
          "Away municipal matches required for complete global calibration.",
          "Ensures regional diffusion.",
          "7. Accuracy & Tri-Gates",
      ),
      (
          "ACCURACY_TARGET_COUNTRY_BRIDGES",
          10.0,
          1,
          "100% Accuracy Country Bridges Quota",
          "Cross-border matches required for international anchor accuracy.",
          "Guarantees international anchor parity.",
          "7. Accuracy & Tri-Gates",
      ),
      (
          "ACCURACY_CURVATURE_GAMMA",
          1.40,
          1,
          "Asymptotic Curvature Gamma",
          "Power exponent governing the steepness of high-trust accuracy ascent.",
          "Higher values (1.6-2.0) make 95%+ exponentially harder to achieve.",
          "7. Accuracy & Tri-Gates",
      ),
      (
          "ACCURACY_WEIGHT_RD",
          0.50,
          1,
          "Accuracy Weight: RD",
          "Weight for Pillar 1 (Certainty).",
          "Controls influence of RD.",
          "7. Accuracy & Tri-Gates",
      ),
      (
          "ACCURACY_WEIGHT_MATCHES",
          0.25,
          1,
          "Accuracy Weight: Matches",
          "Weight for Pillar 2 (Match Depth).",
          "Controls importance of volume.",
          "7. Accuracy & Tri-Gates",
      ),
      (
          "ACCURACY_WEIGHT_DIVERSITY",
          0.25,
          1,
          "Accuracy Weight: Diversity",
          "Weight for Pillar 3 (Network).",
          "Controls importance of unique opponents.",
          "7. Accuracy & Tri-Gates",
      ),
      (
          "TIER_PROVISIONAL_MAX",
          69.99,
          1,
          "Provisional Score Ceiling",
          "Upper score bound for Tier 1.",
          "Players below remain [PR].",
          "7. Accuracy & Tri-Gates",
      ),
      (
          "TARGET_MATCHES_PROVISIONAL",
          3,
          1,
          "Target Matches: Provisional",
          "Match quota during onboarding.",
          "Satisfies depth.",
          "7. Accuracy & Tri-Gates",
      ),
      (
          "TARGET_OPPONENTS_PROVISIONAL",
          2,
          1,
          "Target Opponents: Provisional",
          "Opponent quota during onboarding.",
          "Satisfies diversity.",
          "7. Accuracy & Tri-Gates",
      ),
      (
          "PROVISIONAL_RD_GATE",
          100.0,
          1,
          "Tri-Gate Max RD",
          "RD must be <= 100 to exit [PR].",
          "Uncertainty ceiling to graduate.",
          "7. Accuracy & Tri-Gates",
      ),
      (
          "PROVISIONAL_MIN_MATCHES",
          10,
          1,
          "Tri-Gate Min Matches",
          "Verified matches to exit [PR].",
          "Volume required to shed badge.",
          "7. Accuracy & Tri-Gates",
      ),
      (
          "PROVISIONAL_MIN_OPPONENTS",
          8,
          1,
          "Tri-Gate Min Opponents",
          "Unique opponents to exit [PR].",
          "Distinct opponents required.",
          "7. Accuracy & Tri-Gates",
      ),
      (
          "ISLAND_ACCURACY_CAP",
          80.0,
          1,
          "Island Geographic Cap",
          "Max accuracy if City has 0 bridges.",
          "Caps accuracy.",
          "7. Accuracy & Tri-Gates",
      ),
      (
          "BRIDGE_RD_THRESHOLD",
          80.0,
          1,
          "Bridge Max RD",
          "Max RD to qualify as Bridge.",
          "Count if RD <= 80.",
          "8. Hawking Macro",
      ),
      (
          "BRIDGE_MIN_MATCHES",
          5,
          1,
          "Bridge Min Matches",
          "Away matches required to link cities.",
          "Matches required before linking.",
          "8. Hawking Macro",
      ),
      (
          "CIRCUIT_BREAKER",
          0.0250,
          1,
          "Auto Cron Safety Ceiling",
          "Max shift per weekly cycle.",
          "Limits automated macro shifts.",
          "8. Hawking Macro",
      ),
      (
          "HAWKING_MIN_ACTIVE_VERIFIED_PLAYERS",
          60.0,
          1,
          "Min Active Verified Core",
          "Required verified residents (RD <= 100 with recent matches) for readiness.",
          "Blocks ghost town registration inflation.",
          "8. Hawking Macro",
      ),
      (
          "HAWKING_TARGET_MATCH_DEPTH",
          15.0,
          1,
          "Target Municipal Activity Depth",
          "Average match depth per active resident before city reaches green status.",
          "Ensures deep local match volume.",
          "8. Hawking Macro",
      ),
      (
          "HAWKING_TARGET_BRIDGE_NODES",
          5.0,
          1,
          "Target Bridge Nodes (K)",
          "Qualified traveler nodes required for full connectivity.",
          "Ensures inter-city calibration.",
          "8. Hawking Macro",
      ),
      (
          "HAWKING_TIKHONOV_LAMBDA",
          3.0,
          1,
          "Tikhonov Shrinkage Lambda",
          "Dampening constant in bridge confidence: K / (K + lambda).",
          "Lower values trust bridge evidence faster.",
          "8. Hawking Macro",
      ),
      (
          "MC_STD_B03",
          1.000,
          1,
          "Format Multiplier: Best of 3 Sets",
          "Confidence multiplier for Best of 3 Sets.",
          "Full standard match.",
          "9. Format Multipliers (M_C)",
      ),
      (
          "MC_STD_B05",
          1.000,
          1,
          "Format Multiplier: Best of 5 Sets",
          "Confidence multiplier for Best of 5 Sets.",
          "Full standard match.",
          "9. Format Multipliers (M_C)",
      ),
      (
          "MC_RACE_4",
          0.500,
          1,
          "Format Multiplier: Race to 4 Games",
          "Confidence multiplier for Race to 4 Games.",
          "Short sprint set.",
          "9. Format Multipliers (M_C)",
      ),
      (
          "MC_RACE_5",
          0.600,
          1,
          "Format Multiplier: Race to 5 Games",
          "Confidence multiplier for Race to 5 Games.",
          "Sprint set.",
          "9. Format Multipliers (M_C)",
      ),
      (
          "MC_RACE_6",
          0.700,
          1,
          "Format Multiplier: Race to 6 Games",
          "Confidence multiplier for Race to 6 Games.",
          "Standard single set.",
          "9. Format Multipliers (M_C)",
      ),
      (
          "MC_RACE_7",
          0.800,
          1,
          "Format Multiplier: Race to 7 Games",
          "Confidence multiplier for Race to 7 Games.",
          "Extended single set.",
          "9. Format Multipliers (M_C)",
      ),
      (
          "MC_RACE_9",
          0.800,
          1,
          "Format Multiplier: Race to 9 Games",
          "Confidence multiplier for Race to 9 Games.",
          "Pro set.",
          "9. Format Multipliers (M_C)",
      ),
      (
          "MC_RACE_11",
          0.900,
          1,
          "Format Multiplier: Race to 11 Games",
          "Confidence multiplier for Race to 11 Games.",
          "Extended pro set.",
          "9. Format Multipliers (M_C)",
      ),
      (
          "MC_AMER_12",
          0.300,
          1,
          "Format Multiplier: Americano 12",
          "Confidence multiplier for Americano 12.",
          "Social mixer weight.",
          "9. Format Multipliers (M_C)",
      ),
      (
          "MC_MEX_12",
          0.300,
          1,
          "Format Multiplier: Mexicano 12",
          "Confidence multiplier for Mexicano 12.",
          "Social mixer weight.",
          "9. Format Multipliers (M_C)",
      ),
      (
          "MC_AMER_16",
          0.300,
          1,
          "Format Multiplier: Americano 16",
          "Confidence multiplier for Americano 16.",
          "Social mixer weight.",
          "9. Format Multipliers (M_C)",
      ),
      (
          "MC_MEX_16",
          0.300,
          1,
          "Format Multiplier: Mexicano 16",
          "Confidence multiplier for Mexicano 16.",
          "Social mixer weight.",
          "9. Format Multipliers (M_C)",
      ),
      (
          "MC_AMER_20",
          0.300,
          1,
          "Format Multiplier: Americano 20",
          "Confidence multiplier for Americano 20.",
          "Social mixer weight.",
          "9. Format Multipliers (M_C)",
      ),
      (
          "MC_MEX_20",
          0.300,
          1,
          "Format Multiplier: Mexicano 20",
          "Confidence multiplier for Mexicano 20.",
          "Social mixer weight.",
          "9. Format Multipliers (M_C)",
      ),
      (
          "MC_AMER_24",
          0.300,
          1,
          "Format Multiplier: Americano 24",
          "Confidence multiplier for Americano 24.",
          "Social mixer weight.",
          "9. Format Multipliers (M_C)",
      ),
      (
          "MC_MEX_24",
          0.300,
          1,
          "Format Multiplier: Mexicano 24",
          "Confidence multiplier for Mexicano 24.",
          "Social mixer weight.",
          "9. Format Multipliers (M_C)",
      ),
      (
          "MC_AMER_28",
          0.300,
          1,
          "Format Multiplier: Americano 28",
          "Confidence multiplier for Americano 28.",
          "Social mixer weight.",
          "9. Format Multipliers (M_C)",
      ),
      (
          "MC_MEX_28",
          0.300,
          1,
          "Format Multiplier: Mexicano 28",
          "Confidence multiplier for Mexicano 28.",
          "Social mixer weight.",
          "9. Format Multipliers (M_C)",
      ),
      (
          "MC_AMER_32",
          0.350,
          1,
          "Format Multiplier: Americano 32",
          "Confidence multiplier for Americano 32.",
          "Social mixer weight.",
          "9. Format Multipliers (M_C)",
      ),
      (
          "MC_MEX_32",
          0.350,
          1,
          "Format Multiplier: Mexicano 32",
          "Confidence multiplier for Mexicano 32.",
          "Social mixer weight.",
          "9. Format Multipliers (M_C)",
      ),
  ]
  for k, v, act, tit, desc, tune, grp in master_params:
    cursor.execute(
        """INSERT INTO global_config VALUES (?, ?, ?, ?, ?, ?, ?) 
           ON CONFLICT(param_key) DO UPDATE SET 
               title=excluded.title, description=excluded.description, 
               tuning_guide=excluded.tuning_guide, module_group=excluded.module_group""",
        (k, v, act, tit, desc, tune, grp),
    )


init_db()


# ==============================================================================
# HAWKING MATHEMATICAL & STATISTICAL HELPERS (BITS 21 & 22)
# ==============================================================================
def compute_decay_multiplier(rating: float) -> float:
  if rating >= 7.0000:
    return 0.0000001
  base_decay = (7.0000 - rating) / 7.0000
  if rating >= 6.3000:
    drag = ((7.0000 - rating) / (7.0000 - 6.3000)) ** 2.5
    return max(0.0000001, base_decay * drag)
  return max(0.0010000, base_decay)


def evaluate_municipal_readiness(
    active_verified_count: int,
    total_matches: int,
    k_bridges: int,
    cfg: dict = None,
) -> dict:
  if cfg is None:
    cfg = {}
  target_active = float(cfg.get("HAWKING_MIN_ACTIVE_VERIFIED_PLAYERS", 60.0))
  target_depth = float(cfg.get("HAWKING_TARGET_MATCH_DEPTH", 15.0))
  lambda_bridge = float(cfg.get("HAWKING_TIKHONOV_LAMBDA", 3.0))

  p_score = (
      min(40.0, (active_verified_count / target_active) * 40.0)
      if target_active > 0
      else 40.0
  )
  depth = (
      (total_matches / active_verified_count)
      if active_verified_count > 0
      else 0.0
  )
  d_score = (
      min(30.0, (depth / target_depth) * 30.0) if target_depth > 0 else 30.0
  )
  w_conf = (
      (k_bridges / (k_bridges + lambda_bridge))
      if (k_bridges + lambda_bridge) > 0
      else 0.0
  )
  b_score = w_conf * 30.0

  total_readiness = round(p_score + d_score + b_score, 1)
  if total_readiness >= 80.0:
    status = "GREEN (Calibrated)"
  elif total_readiness >= 50.0:
    status = "YELLOW (Maturing Pool)"
  else:
    status = "RED (Isolated Island)"

  return {
      "readiness_pct": total_readiness,
      "status": status,
      "w_conf": round(w_conf, 4),
      "active_verified": active_verified_count,
      "activity_depth": round(depth, 1),
  }


def calculate_intransitivity(city_id: str, conn) -> float:
  query = """
        SELECT win_expectancy_a, score_team_a, score_team_b 
        FROM matches m
        JOIN venues v ON m.venue_id = v.venue_id
        WHERE v.city_id = ?
    """
  rows = conn.execute(query, (city_id,)).fetchall()
  if not rows:
    return 0.0000
  qualifying, upsets = 0, 0
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


def run_ghost_monte_carlo(
    local_ratings: list[float], ghost_profiles: list[dict], n_sims: int = 10000
) -> dict:
  if not local_ratings or not ghost_profiles:
    return {
        "ensemble_win_rate": 0.5000,
        "expected_win_rate": 0.5000,
        "performance_residual": 0.0000,
        "proposed_offset": 0.0000,
        "breakdown": {},
        "total_sims": 0,
    }

  sims_per_ghost = max(1, n_sims // len(ghost_profiles))
  breakdown = {}
  total_actual_wins = 0
  total_expected_wins = 0.0
  total_matches = 0

  for g in ghost_profiles:
    g_rating = float(g["assigned_mmr"])
    style = g.get("playstyle", "BALANCED")
    mod = (
        0.05
        if style == "AGGRESSIVE"
        else (-0.05 if style == "CONSERVATIVE" else 0.0)
    )
    eff_ghost_r = g_rating + mod
    g_actual_wins = 0

    for _ in range(sims_per_ghost):
      r_local = random.choice(local_ratings)
      ea_local = 1.0 / (1.0 + 10.0 ** ((eff_ghost_r - r_local) / 2.0))
      total_expected_wins += ea_local
      if random.random() < ea_local:
        g_actual_wins += 1
      total_matches += 1

    win_rate = g_actual_wins / float(sims_per_ghost)
    breakdown[g["ghost_name"]] = round(win_rate, 4)
    total_actual_wins += g_actual_wins

  obs_wr = (
      total_actual_wins / float(total_matches) if total_matches > 0 else 0.5000
  )
  exp_wr = (
      total_expected_wins / float(total_matches)
      if total_matches > 0
      else 0.5000
  )
  residual = obs_wr - exp_wr
  raw_offset = residual * 0.3000
  clamped_offset = max(-0.0750, min(0.0750, raw_offset))

  return {
      "ensemble_win_rate": round(obs_wr, 4),
      "expected_win_rate": round(exp_wr, 4),
      "performance_residual": round(residual, 4),
      "proposed_offset": round(clamped_offset, 4),
      "breakdown": breakdown,
      "total_sims": total_matches,
  }


def calculate_hawking_player_delta(
    r_curr: float, rd_curr: float, target_offset: float
) -> float:
  if rd_curr > 100.0:
    return 0.0000
  if r_curr <= 4.5000:
    scale = max(0.0, r_curr / 4.5000)
    return round(target_offset * scale, 4)
  decay_45 = compute_decay_multiplier(4.5000)
  decay_curr = compute_decay_multiplier(r_curr)
  elasticity = decay_curr / decay_45 if decay_45 > 0 else 1.0
  return round(target_offset * elasticity, 4)


# ==============================================================================
# 2. V.16 CALCULATION ENGINE
# ==============================================================================
class RyftV16:

  @staticmethod
  def get_configs(conn=None):
    owns = False
    if not conn:
      conn = get_db_connection()
      owns = True
    rows = conn.execute(
        "SELECT param_key, param_value FROM global_config WHERE is_active = 1"
    ).fetchall()
    if owns:
      conn.close()
    return {r["param_key"]: r["param_value"] for r in rows}

  @staticmethod
  def get_cat_for_rating(r_val, conn=None):
    owns = False
    if not conn:
      conn = get_db_connection()
      owns = True
    cats = conn.execute(
        "SELECT category_name, min_rating, max_rating, speed_multiplier FROM"
        " rating_categories ORDER BY sort_order ASC"
    ).fetchall()
    if owns:
      conn.close()
    for c in cats:
      if c["min_rating"] <= r_val <= c["max_rating"]:
        return (
            c["category_name"],
            c["min_rating"],
            c["max_rating"],
            c["speed_multiplier"] or 1.00,
        )
    return "Intermediate", 2.000, 3.499, 1.00

  @staticmethod
  def calc_accuracy(
      rd,
      m_count,
      opp_count,
      is_prov,
      k_city_bridges,
      k_country_bridges,
      cfg,
  ):
    s_rd = max(
        0.0,
        min(
            1.0,
            (cfg.get("RD_MAX", 350.0) - rd)
            / (cfg.get("RD_MAX", 350.0) - cfg.get("RD_MIN", 30.0)),
        ),
    )
    t_m = cfg.get("TARGET_MATCHES_PROVISIONAL", 3)
    t_o = cfg.get("TARGET_OPPONENTS_PROVISIONAL", 2)
    s_m = min(1.0, m_count / float(t_m))
    s_d = min(1.0, opp_count / float(t_o))

    stage1_base = (
        cfg.get("ACCURACY_WEIGHT_RD", 0.50) * s_rd
        + cfg.get("ACCURACY_WEIGHT_MATCHES", 0.25) * s_m
        + cfg.get("ACCURACY_WEIGHT_DIVERSITY", 0.25) * s_d
    ) * 100.0
    if is_prov:
      stage1_base *= cfg.get("PROVISIONAL_ACCURACY_DAMPENER", 0.40)

    trust_floor = float(cfg.get("ACCURACY_HIGH_TRUST_FLOOR", 65.0))
    if stage1_base <= trust_floor or is_prov:
      phi = (
          min(1.0, cfg.get("ISLAND_ACCURACY_CAP", 80.0) / 100.0)
          if k_city_bridges == 0
          else 1.0
      )
      return (
          round(stage1_base * phi, 1),
          round(s_rd * 100.0, 1),
          round(s_m * 100.0, 1),
          round(s_d * 100.0, 1),
      )

    t_m_elite = float(cfg.get("ACCURACY_TARGET_MATCHES_ELITE", 100.0))
    t_o_elite = float(cfg.get("ACCURACY_TARGET_OPPONENTS_ELITE", 50.0))
    t_c_elite = float(cfg.get("ACCURACY_TARGET_CITY_BRIDGES", 25.0))
    t_co_elite = float(cfg.get("ACCURACY_TARGET_COUNTRY_BRIDGES", 10.0))
    gamma = float(cfg.get("ACCURACY_CURVATURE_GAMMA", 1.40))

    s_m_h = min(1.0, m_count / t_m_elite)
    s_o_h = min(1.0, opp_count / t_o_elite)
    s_c_h = min(1.0, k_city_bridges / t_c_elite)
    s_co_h = min(1.0, k_country_bridges / t_co_elite)

    h_score = (
        (0.35 * s_m_h) + (0.35 * s_o_h) + (0.20 * s_c_h) + (0.10 * s_co_h)
    )
    h_ascent = (h_score**gamma) * (100.0 - trust_floor)
    total_acc = min(100.0, trust_floor + h_ascent)

    return (
        round(total_acc, 1),
        round(s_rd * 100.0, 1),
        round(s_m_h * 100.0, 1),
        round(s_o_h * 100.0, 1),
    )

  @staticmethod
  def sync_player_aggregates(player_id, conn=None):
    owns = False
    if not conn:
      conn = get_db_connection()
      owns = True
    m_count = conn.execute(
        "SELECT COUNT(*) FROM matches WHERE team_a_p1_id = ? OR team_a_p2_id ="
        " ? OR team_b_p1_id = ? OR team_b_p2_id = ?",
        (player_id, player_id, player_id, player_id),
    ).fetchone()[0]
    opp_count = conn.execute(
        """
            SELECT COUNT(DISTINCT opp_id) FROM (
                SELECT team_b_p1_id as opp_id FROM matches WHERE team_a_p1_id = ? OR team_a_p2_id = ? UNION
                SELECT team_b_p2_id as opp_id FROM matches WHERE (team_a_p1_id = ? OR team_a_p2_id = ?) AND team_b_p2_id IS NOT NULL UNION
                SELECT team_a_p1_id as opp_id FROM matches WHERE team_b_p1_id = ? OR team_b_p2_id = ? UNION
                SELECT team_a_p2_id as opp_id FROM matches WHERE (team_b_p1_id = ? OR team_b_p2_id = ?) AND team_a_p2_id IS NOT NULL
            ) WHERE opp_id IS NOT NULL AND opp_id != ?
        """,
        (
            player_id,
            player_id,
            player_id,
            player_id,
            player_id,
            player_id,
            player_id,
            player_id,
            player_id,
        ),
    ).fetchone()[0]

    p_row = conn.execute(
        "SELECT home_city_id, home_country_code, rating_deviation, latent_mmr,"
        " is_manually_verified FROM players WHERE player_id = ?",
        (player_id,),
    ).fetchone()
    cross_city_matches, cross_country_matches = 0, 0
    if p_row:
      cross_city_matches = conn.execute(
          """SELECT COUNT(*) FROM matches m 
             JOIN venues v ON m.venue_id = v.venue_id 
             WHERE (m.team_a_p1_id = ? OR m.team_a_p2_id = ? OR m.team_b_p1_id = ? OR m.team_b_p2_id = ?) 
               AND v.city_id != ?""",
          (player_id, player_id, player_id, player_id, p_row["home_city_id"]),
      ).fetchone()[0]
      cross_country_matches = conn.execute(
          """SELECT COUNT(*) FROM matches m 
             JOIN venues v ON m.venue_id = v.venue_id 
             WHERE (m.team_a_p1_id = ? OR m.team_a_p2_id = ? OR m.team_b_p1_id = ? OR m.team_b_p2_id = ?) 
               AND v.country_code != ?""",
          (
              player_id,
              player_id,
              player_id,
              player_id,
              p_row["home_country_code"],
          ),
      ).fetchone()[0]

    is_act_city_bridge = (
        1
        if (
            p_row
            and p_row["rating_deviation"] <= 80.0
            and cross_city_matches >= 5
        )
        else 0
    )
    is_act_ctry_bridge = (
        1
        if (
            p_row
            and p_row["rating_deviation"] <= 80.0
            and cross_country_matches >= 3
        )
        else 0
    )
    cur_cat, _, _, _ = RyftV16.get_cat_for_rating(
        p_row["latent_mmr"] if p_row else 3.0, conn=conn
    )

    conn.execute(
        """UPDATE players SET 
            verified_matches_count=?, unique_opponents_count=?, bridge_matches_count=?, 
            is_active_bridge=?, is_country_bridge=?, all_time_badge=? 
        WHERE player_id=?""",
        (
            m_count,
            opp_count,
            cross_city_matches,
            is_act_city_bridge,
            is_act_ctry_bridge,
            cur_cat,
            player_id,
        ),
    )
    if owns:
      conn.commit()
      conn.close()

  @staticmethod
  def get_last_active_timestamp(
      player_id, current_ts_str, conn, fallback_ts=None
  ):
    if conn and current_ts_str:
      row = conn.execute(
          """SELECT MAX(m.match_timestamp) FROM match_logs ml 
             JOIN matches m ON ml.match_id = m.match_id WHERE ml.player_id = ? AND m.match_timestamp < ?""",
          (player_id, current_ts_str),
      ).fetchone()
      if row and row[0]:
        return row[0]
    return fallback_ts

  @staticmethod
  def get_rolling_24h_pairwise_delta(
      player_id, opponent_ids, current_ts_str, conn
  ):
    if not current_ts_str or not conn or not opponent_ids:
      return 0.0
    try:
      curr_dt = datetime.fromisoformat(current_ts_str.replace("Z", "+00:00"))
    except Exception:
      curr_dt = datetime.now(timezone.utc)

    opp_placeholders = ",".join("?" for _ in opponent_ids)
    query = f"""
            SELECT ml.delta_r, m.match_timestamp FROM match_logs ml 
            JOIN matches m ON ml.match_id = m.match_id 
            WHERE ml.player_id = ? AND m.is_tournament = 0 AND (
                ((m.team_a_p1_id = ? OR m.team_a_p2_id = ?) AND (m.team_b_p1_id IN ({opp_placeholders}) OR m.team_b_p2_id IN ({opp_placeholders})))
                OR
                ((m.team_b_p1_id = ? OR m.team_b_p2_id = ?) AND (m.team_a_p1_id IN ({opp_placeholders}) OR m.team_a_p2_id IN ({opp_placeholders})))
            )
        """
    params = (
        [player_id, player_id, player_id]
        + list(opponent_ids)
        + list(opponent_ids)
        + [player_id, player_id]
        + list(opponent_ids)
        + list(opponent_ids)
    )
    rows = conn.execute(query, params).fetchall()

    rolling_delta = 0.0
    for r in rows:
      try:
        m_dt = datetime.fromisoformat(r["match_timestamp"].replace("Z", "+00:00"))
        if 0 <= (curr_dt - m_dt).total_seconds() <= 86400.0:
          rolling_delta += float(r["delta_r"])
      except Exception:
        continue
    return rolling_delta

  @staticmethod
  def get_rolling_24h_global_delta(player_id, current_ts_str, conn):
    if not current_ts_str or not conn:
      return 0.0
    try:
      curr_dt = datetime.fromisoformat(current_ts_str.replace("Z", "+00:00"))
    except Exception:
      curr_dt = datetime.now(timezone.utc)

    rows = conn.execute(
        """SELECT ml.delta_r, m.match_timestamp FROM match_logs ml 
           JOIN matches m ON ml.match_id = m.match_id WHERE ml.player_id = ? AND m.is_tournament = 0""",
        (player_id,),
    ).fetchall()

    rolling_delta = 0.0
    for r in rows:
      try:
        m_dt = datetime.fromisoformat(r["match_timestamp"].replace("Z", "+00:00"))
        if 0 <= (curr_dt - m_dt).total_seconds() <= 86400.0:
          rolling_delta += float(r["delta_r"])
      except Exception:
        continue
    return rolling_delta

  @classmethod
  def compute_match(
      cls,
      p1_raw,
      p2_raw,
      p3_raw,
      p4_raw,
      s_a,
      s_b,
      g_w_raw,
      g_l_raw,
      fmt_id,
      v_id,
      is_singles,
      is_dry=False,
      is_tournament=False,
      session_id=None,
      session_checked_in=0,
      conn=None,
      cumulative_deltas=None,
      match_timestamp=None,
  ):
    owns = False
    if not conn:
      conn = get_db_connection()
      owns = True

    p1, p3 = dict(p1_raw), dict(p3_raw)
    p2 = dict(p2_raw) if p2_raw else None
    p4 = dict(p4_raw) if p4_raw else None

    cfg = cls.get_configs(conn=conn)
    p_exp = cfg.get("POWER_MEAN_P", 3.0)

    is_draw = s_a == s_b
    g_w, g_l = max(g_w_raw, g_l_raw + (0 if is_draw else 1)), g_l_raw

    effective_match_ts = (
        match_timestamp or datetime.now(timezone.utc).isoformat()
    )
    try:
      match_dt = datetime.fromisoformat(
          effective_match_ts.replace("Z", "+00:00")
      )
    except Exception:
      match_dt = datetime.now(timezone.utc)

    ven_row = conn.execute(
        "SELECT venue_id, city_id, country_code FROM venues WHERE venue_id = ?",
        (v_id,),
    ).fetchone()
    venue_city = ven_row["city_id"] if ven_row else None
    venue_country = ven_row["country_code"] if ven_row else None

    all_on_court = [p1, p3] + ([p2, p4] if not is_singles else [])

    is_venue_bridge, is_city_bridge, is_country_bridge = False, False, False
    city_travelers, country_travelers, venue_travelers = [], [], []

    for px in all_on_court:
      p_ven = px.get("home_venue_id")
      p_cit = px.get("home_city_id")
      p_cnt = px.get("home_country_code")
      p_name = px.get("display_name", "Player")

      if p_cnt and venue_country and p_cnt != venue_country:
        is_country_bridge = True
        country_travelers.append(f"{p_name} (Intl)")
      elif p_cit and venue_city and p_cit != venue_city:
        is_city_bridge = True
        city_travelers.append(f"{p_name} (Cross-City)")
      elif p_ven and v_id and p_ven != v_id and p_cit == venue_city:
        is_venue_bridge = True
        venue_travelers.append(f"{p_name} (Cross-Club)")

    for px in all_on_court:
      stored_rd = float(px.get("rating_deviation", 350.0))
      last_ts = cls.get_last_active_timestamp(
          px["player_id"],
          effective_match_ts,
          conn,
          fallback_ts=px.get("last_match_time") or px.get("created_at"),
      )
      px_flags = []
      if last_ts:
        try:
          last_dt = datetime.fromisoformat(last_ts.replace("Z", "+00:00"))
          delta_days = max(
              0.0, (match_dt - last_dt).total_seconds() / 86400.0
          )
          c_drift = float(cfg.get("TEMPORAL_DRIFT_CONSTANT", 5.50))
          grace_days = float(cfg.get("INACTIVITY_GRACE_DAYS", 7.0))
          if delta_days >= grace_days:
            rust_rd = math.sqrt(stored_rd**2 + (c_drift**2) * delta_days)
            eff_rd = min(350.0, rust_rd)
            px_flags.append(
                f"[ALERT_INACTIVITY_RUST] ({int(delta_days)}d layoff)"
            )
          else:
            eff_rd = stored_rd
        except Exception:
          eff_rd = stored_rd
      else:
        eff_rd = stored_rd

      reprov_threshold = float(
          cfg.get("INACTIVITY_REPROVISION_THRESHOLD", 100.0)
      )
      if (
          eff_rd > reprov_threshold
          and px.get("calibration_tier") == "VERIFIED"
      ):
        px_flags.append("[ALERT_INACTIVITY_REPROVISION_FLAG]")

      px["effective_pre_rd"] = eff_rd
      px["inactivity_flags"] = px_flags

    ta_r = (
        float(p1.get("latent_mmr", 3.0))
        if is_singles
        else (
            (
                float(p1.get("latent_mmr", 3.0)) ** p_exp
                + float(p2.get("latent_mmr", 3.0)) ** p_exp
            )
            / 2.0
        )
        ** (1.0 / p_exp)
    )
    tb_r = (
        float(p3.get("latent_mmr", 3.0))
        if is_singles
        else (
            (
                float(p3.get("latent_mmr", 3.0)) ** p_exp
                + float(p4.get("latent_mmr", 3.0)) ** p_exp
            )
            / 2.0
        )
        ** (1.0 / p_exp)
    )

    ea = 1.0 / (1.0 + 10.0 ** ((tb_r - ta_r) / cfg.get("LOGISTIC_BETA", 2.0)))
    act_a = 0.5 if is_draw else (1.0 if s_a > s_b else 0.0)

    tot_g = g_w + g_l
    m_base, m_scale = cfg.get("MARGIN_BASE", 0.80), cfg.get(
        "MARGIN_SCALE", 0.40
    )
    s_margin = max(
        0.80,
        min(
            1.20,
            (
                m_base + (m_scale * ((g_w - g_l) / tot_g))
                if tot_g > 0
                else 1.0
            ),
        ),
    )

    fmt_row = conn.execute(
        "SELECT mc_weight FROM match_formats WHERE format_id = ?", (fmt_id,)
    ).fetchone()
    db_mc = float(fmt_row["mc_weight"]) if fmt_row else 1.00
    mc = float(cfg.get(f"MC_{fmt_id}", db_mc))

    opp_rd_b = (
        max(float(p3["effective_pre_rd"]), float(p4["effective_pre_rd"]))
        if not is_singles
        else float(p3["effective_pre_rd"])
    )
    opp_rd_a = (
        max(float(p1["effective_pre_rd"]), float(p2["effective_pre_rd"]))
        if not is_singles
        else float(p1["effective_pre_rd"])
    )

    participants = [(p1, True, p2, opp_rd_b), (p3, False, p4, opp_rd_a)]
    if not is_singles:
      participants.extend(
          [(p2, True, p1, opp_rd_b), (p4, False, p3, opp_rd_a)]
      )

    res = []
    prov_count = sum(
        1 for px, _, _, _ in participants if bool(px.get("is_provisional", 1))
    )
    o_map = [
        cfg.get("COHORT_FACTOR_0_PROV", 1.0),
        cfg.get("COHORT_FACTOR_1_PROV", 0.75),
        cfg.get("COHORT_FACTOR_2_PROV", 0.50),
        cfg.get("COHORT_FACTOR_3_PROV", 0.25),
    ]
    omega = o_map[min(3, max(0, prov_count - 1))]

    for p, is_a, partner, opp_rd in participants:
      flags = list(p.get("inactivity_flags", []))

      if is_country_bridge:
        flags.append(
            f"[ALERT_CROSS_COUNTRY_BRIDGE] ({', '.join(country_travelers)})"
        )
      elif is_city_bridge:
        flags.append(f"[ALERT_CROSS_CITY_BRIDGE] ({', '.join(city_travelers)})")
      elif is_venue_bridge:
        flags.append(f"[ALERT_VENUE_BRIDGE] ({', '.join(venue_travelers)})")

      r = float(p.get("latent_mmr", 3.0))
      rd = float(
          p.get("effective_pre_rd", p.get("rating_deviation", 350.0))
      )
      prov = bool(p.get("is_provisional", 1))
      is_manual_override = bool(p.get("is_manually_verified", 0))
      is_quar = bool(p.get("is_quarantined", 0))
      won = (is_a and s_a > s_b) or (not is_a and s_b > s_a)

      q, sig = 0.0057565, cfg.get("RD_INFO_VARIANCE", 110.0)
      g_opp = 1.0 / math.sqrt(
          1.0 + (3.0 * (q**2) * (opp_rd**2)) / (math.pi**2)
      )

      k_ind = cfg.get("K_MAX", 0.400) - (r / cfg.get("R_MAX", 7.000)) * (
          cfg.get("K_MAX", 0.400) - cfg.get("K_MIN", 0.080)
      )
      _, _, _, cat_speed = cls.get_cat_for_rating(r, conn=conn)
      if cat_speed != 1.00:
        k_ind *= cat_speed
        flags.append(f"CAT_SPEED ({cat_speed:.2f}x)")

      decay_ind = (
          ((7.000 - r) / 7.000) * (((7.000 - r) / (7.000 - 6.300)) ** 2.5)
          if r >= 6.300
          else ((7.000 - r) / 7.000)
      )
      if r >= 6.300:
        flags.append("[ALERT_ELITE_DRAG_MAX_RESISTANCE]")

      direction = 1.0 if is_a else -1.0
      standard_einstein_delta = (
          k_ind
          * decay_ind
          * mc
          * s_margin
          * g_opp
          * direction
          * (act_a - ea)
      )

      opp_team_r = tb_r if is_a else ta_r
      partner_gap = (
          abs(r - float(partner.get("latent_mmr", 3.0)))
          if (not is_singles and partner)
          else 0.0
      )

      if is_quar:
        raw_d = 0.000
        flags.append("[ALERT_QUARANTINE_ISOLATION_ACTIVE]")
      elif is_draw:
        raw_d = standard_einstein_delta
        flags.append("DRAW_PARITY_EXCHANGE")
      elif won:
        if prov and s_a != s_b:
          actual_game_ratio = (g_w_raw + 0.5) / (g_l_raw + 0.5)
          r_perf_team = opp_team_r + cfg.get(
              "LOGISTIC_BETA", 2.0
          ) * math.log10(actual_game_ratio)

          if (
              not is_singles
              and partner
              and partner_gap >= 1.500
              and r > float(partner.get("latent_mmr", 3.0))
          ):
            raw_d = max(0.0050, standard_einstein_delta)
            flags.append("[ALERT_ANCHOR_CARRY_WIN_SHIELD]")
          else:
            raw_pr_delta = (
                (r_perf_team - r)
                * cfg.get("PROVISIONAL_ABSORPTION_ALPHA", 0.45)
                * mc
                * g_opp
            )
            max_d = cfg.get("MAX_PROVISIONAL_DELTA", 0.750)
            raw_d = max(-max_d, min(max_d, raw_pr_delta))
            flags.append("RIGHTSIZING_INTERPOLATION")
            if raw_d < 0:
              flags.append("[ALERT_OVERRATED_PR_WIN_DOWNWARD_PULL]")
        else:
          raw_d = max(0.0010, standard_einstein_delta)
      else:
        if prov and s_a != s_b:
          actual_game_ratio = (g_l_raw + 0.5) / (g_w_raw + 0.5)
          r_perf_team = opp_team_r + cfg.get(
              "LOGISTIC_BETA", 2.0
          ) * math.log10(actual_game_ratio)

          if r_perf_team > r:
            tot_games = g_w_raw + g_l_raw
            game_win_share = (
                (g_l_raw / tot_games) if tot_games > 0 else 0.0
            )
            if game_win_share < 0.30:
              raw_d = 0.0000
              flags.append("[ALERT_LOSS_NON_POSITIVITY_CLAMP]")
            else:
              raw_discovery_delta = (
                  (r_perf_team - r) * 0.10 * mc * g_opp
              )
              raw_d = min(0.0250, raw_discovery_delta)
              flags.append("[ALERT_UNDERDOG_DEFEAT_MICRO_DISCOVERY]")
          else:
            raw_pr_delta = (
                (r_perf_team - r)
                * cfg.get("PROVISIONAL_ABSORPTION_ALPHA", 0.45)
                * mc
                * g_opp
            )
            max_d = cfg.get("MAX_PROVISIONAL_DELTA", 0.750)
            raw_d = max(-max_d, min(0.0, raw_pr_delta))
            flags.append("RIGHTSIZING_INTERPOLATION")
        else:
          raw_d = min(0.0000, standard_einstein_delta)

      # PARTNER DISPARITY & MUTUAL PROVISIONAL PARITY CLAMP
      if not is_singles and partner and not is_quar and not is_draw:
        gap = abs(r - float(partner.get("latent_mmr", 3.0)))
        part_prov = bool(partner.get("is_provisional", 1))
        part_rd = float(
            partner.get(
                "effective_pre_rd", partner.get("rating_deviation", 350.0)
            )
        )

        max_prov_gap = float(cfg.get("MUTUAL_PROV_MAX_PARTNER_GAP", 1.500))
        if prov and part_prov and rd > 200.0 and part_rd > 200.0:
          if gap <= max_prov_gap:
            flags.append("MUTUAL_PROV_EXEMPTION")
          else:
            flags.append("[ALERT_MUTUAL_PROV_EXTREME_GAP_OVERRIDE]")
            if won and r < float(partner.get("latent_mmr", 3.0)):
              ac_m = (
                  cfg.get("ANTI_CARRY_MULT_TIER_2", 0.25)
                  if gap >= 2.50
                  else cfg.get("ANTI_CARRY_MULT_TIER_1", 0.50)
              )
              raw_d *= ac_m
              flags.append(f"ANTI_CARRY ({int((1-ac_m)*100)}%)")
        else:
          if not won and r > float(partner.get("latent_mmr", 3.0)):
            dd = (
                cfg.get("ICE_OUT_MULT_TIER_2", 0.05)
                if gap >= cfg.get("ICE_OUT_GAP_TIER_2", 2.00)
                else (
                    cfg.get("ICE_OUT_MULT_TIER_1", 0.20)
                    if gap >= cfg.get("ICE_OUT_GAP_TIER_1", 1.50)
                    else (0.50 if gap >= 1.0 else 1.00)
                )
            )
            raw_d *= dd
            if dd < 1.00:
              flags.append(
                  f"[ALERT_ICE_OUT_ANCHOR_SHIELD] ({int((1-dd)*100)}%)"
              )
          elif won and r < float(partner.get("latent_mmr", 3.0)):
            ac_m1 = cfg.get("ANTI_CARRY_MULT_TIER_1", 0.50)
            ac_gap1 = cfg.get("ANTI_CARRY_GAP_TIER_1", 1.75)
            dd = (
                cfg.get("ANTI_CARRY_MULT_TIER_2", 0.25)
                if gap >= cfg.get("ANTI_CARRY_GAP_TIER_2", 2.50)
                else (
                    ac_m1
                    if gap >= ac_gap1
                    else (0.75 if gap >= 1.2 else 1.00)
                )
            )
            raw_d *= dd
            if dd < 1.00:
              flags.append(f"ANTI_CARRY ({int((1-dd)*100)}%)")
            if not prov:
              raw_d = max(0.0010, raw_d)

      m_played = int(p.get("verified_matches_count", 0))
      w_g = 1.0
      min_opp_target = float(cfg.get("GRAPH_DAMPENING_MIN_OPPONENTS", 5.0))
      centrality_target = float(cfg.get("GRAPH_CENTRALITY_TARGET", 0.200))
      if m_played >= 5:
        w_g = min(
            1.0,
            float(p.get("graph_centrality", 0.20)) / centrality_target,
        ) * min(
            1.0,
            float(p.get("unique_opponents_count", 0)) / min_opp_target,
        )

      if w_g < 1.0 and not is_quar and not prov:
        raw_d *= w_g
        flags.append(f"[ALERT_DISCONNECTED_GRAPH_DAMPENING] ({w_g:.2f}x)")

      opp_ids = (
          [p3["player_id"], p4["player_id"]]
          if is_a
          else [p1["player_id"], p2["player_id"]]
      )
      opp_ids = [oid for oid in opp_ids if oid is not None]

      mult = cfg.get("PROVISIONAL_CAP_MULTIPLIER", 2.5) if prov else 1.0
      pairwise_cap = (
          cfg.get(
              "MAX_24H_PAIRWISE_EXCHANGE_CAP",
              cfg.get("MAX_24H_EXCHANGE_CAP", 0.1500),
          )
          * mult
      )
      global_cap = cfg.get("MAX_24H_GLOBAL_CASUAL_CAP", 0.2500) * mult

      prior_pairwise = cls.get_rolling_24h_pairwise_delta(
          p["player_id"], opp_ids, effective_match_ts, conn
      )
      prior_global = cls.get_rolling_24h_global_delta(
          p["player_id"], effective_match_ts, conn
      )
      cum_session_delta = (
          cumulative_deltas.get(p["player_id"], 0.0)
          if cumulative_deltas is not None
          else 0.0
      )

      is_elevator_active = (
          prov
          and won
          and not is_draw
          and (raw_d > pairwise_cap)
          and (s_margin >= 1.00 or (g_w_raw >= 2 * g_l_raw and g_w_raw >= 4))
          and (opp_team_r >= r - 0.50)
      )

      if is_elevator_active and bool(
          cfg.get("PROVISIONAL_BYPASS_EXCHANGE_CAP", 1)
      ):
        max_allowed = cfg.get("MAX_PROVISIONAL_DELTA", 0.750)
        final_d = min(max_allowed, max(0.0, raw_d))
        flags.append("PROVISIONAL_CAP_BYPASS")
        if final_d >= 0.500:
          flags.append("[ALERT_SMURF_RIGHTSIZING_SURGE]")
      elif is_tournament and bool(cfg.get("TOURNAMENT_MULTIPLIER_ACTIVE", 1)):
        t_mult = cfg.get("TOURNAMENT_STAKES_MULTIPLIER", 1.15)
        final_d = raw_d * t_mult
        flags.append(f"TOURNAMENT ({t_mult}x, Uncapped)")
      elif session_id and session_checked_in >= cfg.get(
          "MIN_SESSION_PLAYERS", 6
      ):
        sess_cap = cfg.get("SESSION_EXCHANGE_CAP", 0.300) * mult
        target_cum = cum_session_delta + raw_d
        capped_target = max(-sess_cap, min(sess_cap, target_cum))
        final_d = capped_target - cum_session_delta
        if abs(target_cum) > sess_cap:
          flags.append("SESSION_CUMULATIVE_CAP_ENFORCED")
      else:
        if raw_d >= 0.0:
          headroom_pairwise = max(
              0.0,
              pairwise_cap - max(0.0, prior_pairwise + cum_session_delta),
          )
          headroom_global = max(
              0.0, global_cap - max(0.0, prior_global + cum_session_delta)
          )
          active_headroom = min(headroom_pairwise, headroom_global)

          if raw_d > active_headroom:
            final_d = active_headroom
            flags.append("CAP_ENFORCED")
            if active_headroom == headroom_pairwise:
              flags.append("[ALERT_PAIRWISE_CAP_CLAMPED]")
            else:
              flags.append("[ALERT_GLOBAL_CASUAL_CAP_CLAMPED]")
          else:
            final_d = raw_d
        else:
          lossroom_pairwise = min(
              0.0,
              -pairwise_cap - min(0.0, prior_pairwise + cum_session_delta),
          )
          lossroom_global = min(
              0.0, -global_cap - min(0.0, prior_global + cum_session_delta)
          )
          active_lossroom = max(lossroom_pairwise, lossroom_global)

          if raw_d < active_lossroom:
            final_d = active_lossroom
            flags.append("CAP_ENFORCED")
            if active_lossroom == lossroom_pairwise:
              flags.append("[ALERT_PAIRWISE_CAP_CLAMPED]")
            else:
              flags.append("[ALERT_GLOBAL_CASUAL_CAP_CLAMPED]")
          else:
            final_d = raw_d

      new_r = max(0.000, min(6.999, r + final_d))
      c_loss = int(p.get("consecutive_losses", 0))
      pre_disp = float(p.get("display_rating", r))

      if final_d < 0:
        if c_loss < 3 and (
            pre_disp - new_r
            <= cfg.get("DISPLAY_RATING_SOFT_FLOOR", 0.050)
        ):
          new_disp = pre_disp
          flags.append("[ALERT_SOFT_FLOOR_DECOUPLING_ACTIVE]")
        else:
          new_disp = new_r
          if pre_disp - new_r > 0.150:
            flags.append("[ALERT_SOFT_FLOOR_DECOUPLING_MAX]")
      else:
        new_disp = new_r

      new_c_loss = 0 if (won or is_draw) else c_loss + 1
      if g_w_raw == 0 and not won and not is_draw:
        new_rd = rd
        flags.append("[ALERT_ZERO_RESISTANCE_RD_FREEZE]")
      else:
        raw_new_rd = max(
            30.0,
            math.sqrt(
                1.0
                / (
                    1.0 / (rd**2)
                    + (mc * s_margin * (g_opp**2) * omega) / (sig**2)
                )
            ),
        )
        new_rd = rd - (
            (rd - raw_new_rd)
            * (
                cfg.get("PROVISIONAL_RD_CONTRACTION_RATIO", 0.20)
                if prov
                else 1.0
            )
        )

      past_opps = conn.execute(
          """
                SELECT DISTINCT opp_id FROM (
                    SELECT team_b_p1_id as opp_id FROM matches WHERE team_a_p1_id = ? OR team_a_p2_id = ? UNION
                    SELECT team_b_p2_id as opp_id FROM matches WHERE (team_a_p1_id = ? OR team_a_p2_id = ?) AND team_b_p2_id IS NOT NULL UNION
                    SELECT team_a_p1_id as opp_id FROM matches WHERE team_b_p1_id = ? OR team_b_p2_id = ? UNION
                    SELECT team_a_p2_id as opp_id FROM matches WHERE (team_b_p1_id = ? OR team_b_p2_id = ?) AND team_a_p2_id IS NOT NULL
                ) WHERE opp_id IS NOT NULL
            """,
          (
              p["player_id"],
              p["player_id"],
              p["player_id"],
              p["player_id"],
              p["player_id"],
              p["player_id"],
              p["player_id"],
              p["player_id"],
          ),
      ).fetchall()
      past_opp_set = {row[0] for row in past_opps}
      new_opps_in_match = sum(1 for oid in opp_ids if oid not in past_opp_set)

      projected_m = m_played + 1
      projected_opps = (
          int(p.get("unique_opponents_count", 0)) + new_opps_in_match
      )

      k_c_bridges = int(p.get("bridge_matches_count", 0)) + (
          1 if is_city_bridge else 0
      )
      k_co_bridges = int(p.get("is_country_bridge", 0)) + (
          1 if is_country_bridge else 0
      )

      min_m = int(cfg.get("PROVISIONAL_MIN_MATCHES", 10))
      min_o = int(cfg.get("PROVISIONAL_MIN_OPPONENTS", 8))
      rd_gate = float(cfg.get("PROVISIONAL_RD_GATE", 100.0))

      tri_gate_passed = (
          new_rd <= rd_gate
          and projected_m >= min_m
          and projected_opps >= min_o
      )
      new_prov = 0 if (tri_gate_passed or is_manual_override) else 1

      acc_comp, a_rd, a_m, a_d = cls.calc_accuracy(
          new_rd,
          projected_m,
          projected_opps,
          new_prov,
          k_c_bridges,
          k_co_bridges,
          cfg,
      )

      if new_prov == 1:
        tier = "PROVISIONAL"
      else:
        if acc_comp >= 90.0 and new_rd <= 60.0:
          tier = "ANCHOR"
        else:
          tier = "VERIFIED"

      res.append({
          "pid": p["player_id"],
          "name": format_pr_name(
              p.get("display_name", "Unknown"), prov
          ),
          "raw_name": p.get("display_name", "Unknown"),
          "pre_r": r,
          "post_r": new_r,
          "pre_disp": pre_disp,
          "post_disp": new_disp,
          "delta": final_d,
          "stored_pre_rd": float(p.get("rating_deviation", 350.0)),
          "pre_rd": rd,
          "post_rd": new_rd,
          "pre_acc": float(p.get("rating_accuracy_pct", 0.0)),
          "acc": acc_comp,
          "pre_tier": p.get("calibration_tier", "PROVISIONAL"),
          "tier": tier,
          "a_rd": a_rd,
          "a_m": a_m,
          "a_d": a_d,
          "prov": new_prov,
          "c_loss": new_c_loss,
          "flags": flags,
      })

    if owns:
      conn.close()
    return {
        "ta_r": ta_r,
        "tb_r": tb_r,
        "ea": ea,
        "mov": s_margin,
        "applied_m_c": mc,
        "is_venue_bridge": is_venue_bridge,
        "is_city_bridge": is_city_bridge,
        "is_country_bridge": is_country_bridge,
        "res": res,
    }


class SessionLogicEngine:

  @staticmethod
  def generate_schedule(
      team_format,
      match_mode,
      enrolled_pids,
      teams_created,
      court_picks,
      rounds_count,
  ):
    fixtures, fixture_order, num_courts = [], 1, max(1, len(court_picks))
    if team_format == "FIXED_TEAMS":
      t_list = [dict(t) for t in teams_created]
      if len(t_list) % 2 != 0:
        t_list.append({"p1": None, "p2": None, "is_bye": True})
      for r_cycle in range(int(rounds_count)):
        for r_idx in range(len(t_list) - 1):
          round_num = (r_cycle * (len(t_list) - 1)) + (r_idx + 1)
          round_pairings = [
              (t_list[i], t_list[len(t_list) - 1 - i])
              for i in range(len(t_list) // 2)
              if not t_list[i].get("is_bye")
              and not t_list[len(t_list) - 1 - i].get("is_bye")
          ]
          for m_idx, (ta, tb) in enumerate(round_pairings):
            fixtures.append({
                "round_number": round_num,
                "court_id": court_picks[m_idx % num_courts],
                "match_order": fixture_order,
                "team_a_p1_id": ta["p1"],
                "team_a_p2_id": ta["p2"],
                "team_b_p1_id": tb["p1"],
                "team_b_p2_id": tb["p2"],
                "group_id": "A",
            })
            fixture_order += 1
          t_list = [t_list[0]] + [t_list[-1]] + t_list[1:-1]
    elif team_format == "ROTATING_TEAMS" and match_mode == "DOUBLES":
      n_players = len(enrolled_pids)
      if n_players < 4:
        return []
      matches_played = {p: 0 for p in enrolled_pids}
      sat_out_last = {p: False for p in enrolled_pids}
      partner_matrix = {
          p1: {p2: 0 for p2 in enrolled_pids} for p1 in enrolled_pids
      }
      max_courts = max(1, min(num_courts, n_players // 4))

      for round_num in range(1, int(rounds_count) + 1):
        sorted_pids = sorted(
            enrolled_pids,
            key=lambda pid: (
                0 if sat_out_last[pid] else 1,
                matches_played[pid],
                pid,
            ),
        )
        active_players = sorted_pids[: (max_courts * 4)]
        for p in enrolled_pids:
          sat_out_last[p] = p not in active_players
          if p in active_players:
            matches_played[p] += 1
        court_pool = list(active_players)
        for c_idx in range(max_courts):
          if len(court_pool) < 4:
            break
          m_players, court_pool = court_pool[:4], court_pool[4:]
          combos = [
              (
                  (m_players[0], m_players[1]),
                  (m_players[2], m_players[3]),
              ),
              (
                  (m_players[0], m_players[2]),
                  (m_players[1], m_players[3]),
              ),
              (
                  (m_players[0], m_players[3]),
                  (m_players[1], m_players[2]),
              ),
          ]
          (ta_p1, ta_p2), (tb_p1, tb_p2) = min(
              combos,
              key=lambda c: partner_matrix[c[0][0]][c[0][1]]
              + partner_matrix[c[1][0]][c[1][1]],
          )
          partner_matrix[ta_p1][ta_p2] += 1
          partner_matrix[ta_p2][ta_p1] += 1
          partner_matrix[tb_p1][tb_p2] += 1
          partner_matrix[tb_p2][tb_p1] += 1
          fixtures.append({
              "round_number": round_num,
              "court_id": court_picks[c_idx % num_courts],
              "match_order": fixture_order,
              "team_a_p1_id": ta_p1,
              "team_a_p2_id": ta_p2,
              "team_b_p1_id": tb_p1,
              "team_b_p2_id": tb_p2,
              "group_id": "A",
          })
          fixture_order += 1
    return fixtures


def build_pdf_document():
  if not REPORTLAB_AVAILABLE:
    return None
  buf = io.BytesIO()
  doc = SimpleDocTemplate(
      buf,
      pagesize=letter,
      leftMargin=36,
      rightMargin=36,
      topMargin=48,
      bottomMargin=48,
  )
  styles = getSampleStyleSheet()
  title_style = ParagraphStyle(
      "DocTitle",
      fontName="Helvetica-Bold",
      fontSize=18,
      leading=22,
      textColor=colors.HexColor("#0284c7"),
      spaceAfter=4,
  )
  subtitle_style = ParagraphStyle(
      "DocSub",
      fontName="Helvetica-Bold",
      fontSize=9,
      leading=13,
      textColor=colors.HexColor("#0f172a"),
      spaceAfter=8,
  )
  h1_style = ParagraphStyle(
      "SecH1",
      fontName="Helvetica-Bold",
      fontSize=11,
      leading=15,
      textColor=colors.HexColor("#0369a1"),
      spaceBefore=14,
      spaceAfter=6,
      keepWithNext=True,
  )
  h2_style = ParagraphStyle(
      "SecH2",
      fontName="Helvetica-Bold",
      fontSize=9,
      leading=13,
      textColor=colors.HexColor("#0f172a"),
      spaceBefore=10,
      spaceAfter=4,
      keepWithNext=True,
  )
  body_style = ParagraphStyle(
      "BodyDark",
      fontName="Helvetica",
      fontSize=7.6,
      leading=11,
      textColor=colors.HexColor("#1e293b"),
      spaceAfter=5,
  )
  code_style = ParagraphStyle(
      "CodeSnippet",
      fontName="Courier",
      fontSize=6.8,
      leading=9,
      textColor=colors.HexColor("#0369a1"),
      backColor=colors.HexColor("#f8fafc"),
      borderPadding=3,
      spaceAfter=4,
  )
  th_style = ParagraphStyle(
      "THStyle",
      fontName="Helvetica-Bold",
      fontSize=7,
      leading=9,
      textColor=colors.white,
  )
  td_style = ParagraphStyle(
      "TDStyle",
      fontName="Helvetica",
      fontSize=6.6,
      leading=8.5,
      textColor=colors.HexColor("#0f172a"),
  )

  story = []
  story.append(Paragraph("RYFT ENGINE PRIMARY V.16.4", title_style))
  story.append(
      Paragraph(
          "Master Specification, Algorithmic Governance Matrix & System"
          " Blueprint",
          subtitle_style,
      )
  )
  story.append(
      Paragraph(
          "Dual-Engine Architecture: Einstein (Micro Physics) & Hawking (Macro"
          " Diffusion & Supervised Sandbox)",
          body_style,
      )
  )
  story.append(
      HRFlowable(
          width="100%",
          thickness=1.5,
          color=colors.HexColor("#0284c7"),
          spaceAfter=10,
      )
  )

  story.append(
      Paragraph("SECTION 1: EXECUTIVE SUMMARY & TRUST BLUEPRINT", h1_style)
  )
  story.append(
      Paragraph(
          "The RYFT V.16.4 Engine is a continuous, deterministic rating"
          " architecture designed for modern padel and pickleball. It"
          " eliminates the systemic failures of legacy rating systems"
          " (freeze-outs, unearned novice carry, smurfing, and private"
          " collusion) through asymmetric responsibility modeling, performance"
          " interpolation, and topological diffusion.",
          body_style,
      )
  )

  story.append(Spacer(1, 4))
  story.append(Paragraph("SECTION 2: MASTER DATABASE SCHEMA", h1_style))
  db_rows = [
      [
          Paragraph("Table Name", th_style),
          Paragraph("Primary Key", th_style),
          Paragraph("Architectural Role", th_style),
      ],
      [
          Paragraph("players", td_style),
          Paragraph("player_id (UUID)", td_style),
          Paragraph(
              "Master Player Profile (MMR, Peaks, Accuracy, Tier, Consecutive"
              " Losses)",
              td_style,
          ),
      ],
      [
          Paragraph("venues", td_style),
          Paragraph("venue_id (UUID)", td_style),
          Paragraph("Club Facilities & Bridge Counters", td_style),
      ],
      [
          Paragraph("locations", td_style),
          Paragraph("location_id (Code)", td_style),
          Paragraph("Geospatial Hierarchy & Hawking Offsets", td_style),
      ],
      [
          Paragraph("matches", td_style),
          Paragraph("match_id (Code)", td_style),
          Paragraph("Master Transaction Ledger (Deltas, Multipliers)", td_style),
      ],
      [
          Paragraph("sessions", td_style),
          Paragraph("session_id (Code)", td_style),
          Paragraph("Multi-Match Session Workflows", td_style),
      ],
      [
          Paragraph("global_config", td_style),
          Paragraph("param_key", td_style),
          Paragraph("52-Parameter Live Governance Registry", td_style),
      ],
  ]
  t_db = Table(db_rows, colWidths=[100, 100, 250])
  t_db.setStyle(
      TableStyle([
          ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#0f172a")),
          ("GRID", (0, 0), (-1, -1), 0.5, colors.HexColor("#cbd5e1")),
          ("VALIGN", (0, 0), (-1, -1), "TOP"),
          (
              "ROWBACKGROUNDS",
              (0, 1),
              (-1, -1),
              [colors.white, colors.HexColor("#f8fafc")],
          ),
      ])
  )
  story.append(t_db)

  doc.build(story)
  buf.seek(0)
  return buf.read()


# ==============================================================================
# 4. GLOBAL UI & SIDEBAR INITIALIZATION
# ==============================================================================
st.set_page_config(page_title="RYFT Engine V.16 Master", layout="wide")
st.sidebar.markdown(
    """<div style="text-align: center; padding: 10px 0 15px 0;"><svg width="220" height="55" viewBox="0 0 400 100" xmlns="http://www.w3.org/2000/svg"><defs><linearGradient id="ryftBlue" x1="0%" y1="0%" x2="100%" y2="100%"><stop offset="0%" style="stop-color:#0284c7;stop-opacity:1" /><stop offset="100%" style="stop-color:#1d4ed8;stop-opacity:1" /></linearGradient></defs><text x="15" y="75" font-family="-apple-system, BlinkMacSystemFont, sans-serif" font-size="82" font-weight="900" font-style="italic" fill="url(#ryftBlue)" letter-spacing="-3">RYFT</text><rect x="225" y="28" width="80" height="30" rx="6" fill="#0f172a" /><text x="238" y="50" font-family="monospace" font-size="18" font-weight="700" fill="#38bdf8">V.16.4</text></svg></div>""",
    unsafe_allow_html=True,
)

nav = st.sidebar.radio(
    "Navigation Console",
    [
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
        "📄 RYFT Documentation",
    ],
)

# ==============================================================================
# 5. CONSOLE TAB ROUTING
# ==============================================================================
if nav == "📊 The Dashboard":
  st.title("System Command Center & Macro Health")
  conn = get_db_connection()
  m1, m2, m3, m4, m5, m6 = st.columns(6)
  n_players = conn.execute(
      "SELECT COUNT(*) FROM players WHERE calibration_tier != 'INACTIVE'"
  ).fetchone()[0]
  n_matches = conn.execute("SELECT COUNT(*) FROM matches").fetchone()[0]
  n_sessions = conn.execute("SELECT COUNT(*) FROM sessions").fetchone()[0]
  n_venues = conn.execute(
      "SELECT COUNT(*) FROM venues WHERE is_active = 1"
  ).fetchone()[0]
  n_cities = conn.execute(
      "SELECT COUNT(*) FROM locations WHERE location_type = 'CITY'"
  ).fetchone()[0]
  n_countries = conn.execute(
      "SELECT COUNT(*) FROM locations WHERE location_type = 'COUNTRY'"
  ).fetchone()[0]

  m1.metric("Active Players", n_players)
  m2.metric("Matches", n_matches)
  m3.metric("Sessions", n_sessions)
  m4.metric("Venues", n_venues)
  m5.metric("Cities", n_cities)
  m6.metric("Countries", n_countries)
  conn.close()
  st.markdown("---")

  with st.expander("💾 Database Snapshot Backup & Restore", expanded=True):
    st.info(
        "🛡️ Full state backup captures all players, peak histories, venues,"
        " cities, matches, Hawking offsets, and configurations."
    )
    col_b1, col_b2 = st.columns(2)
    with col_b1:
      st.markdown("#### 📥 Backup Database Snapshot")
      if os.path.exists(DB_FILE):
        try:
          st.download_button(
              "⬇️ Download Full System Snapshot (.db)",
              data=export_db_bytes(),
              file_name=(
                  f"RYFT_V16_FullBackup_{datetime.now().strftime('%Y%m%d_%H%M%S')}.db"
              ),
              mime="application/x-sqlite3",
              use_container_width=True,
          )
        except Exception as ex:
          st.error(f"Error preparing snapshot: {ex}")
    with col_b2:
      st.markdown("#### 📤 Restore Saved State")
      up_db = st.file_uploader(
          "Select .db file", type=["db", "sqlite", "sqlite3"]
      )
      if up_db and st.button(
          "🚨 Restore Entire System from File",
          type="primary",
          use_container_width=True,
      ):
        try:
          restore_db_from_bytes(up_db.getbuffer())
          st.success("✅ System State Successfully Restored!")
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
        for tbl in [
            "match_logs",
            "matches",
            "session_matches",
            "session_rosters",
            "tourney_matches",
            "tournaments",
            "sessions",
            "players",
            "venues",
            "locations",
            "synthetic_ghosts",
            "global_config",
        ]:
          conn.execute(f"DELETE FROM {tbl};")
        seed_factory_parameters(conn.cursor(), overwrite_existing=True)
        st.warning("Database completely wiped & factory defaults restored.")
      else:
        if res_m:
          conn.execute("DELETE FROM match_logs;")
          conn.execute("DELETE FROM matches;")
          conn.execute("DELETE FROM session_matches;")
          conn.execute("DELETE FROM session_rosters;")
          conn.execute("DELETE FROM sessions;")
          conn.execute("DELETE FROM tourney_matches;")
          conn.execute("DELETE FROM tournaments;")
          st.warning("Match history erased.")
        if res_p:
          conn.execute("""UPDATE players SET 
                            latent_mmr = initial_rating, display_rating = initial_rating, 
                            rating_deviation = 350.0, verified_matches_count = 0, 
                            unique_opponents_count = 0, rating_accuracy_pct = 0.0, 
                            calibration_tier = 'PROVISIONAL', is_provisional = 1""")
          st.warning("Player ratings reset.")
      conn.commit()
      conn.close()
      st.rerun()

elif nav == "🎾 Log Matches":
  st.title("Log Matches & Real-Time Simulation Hub")
  conn = get_db_connection()
  venues = conn.execute("SELECT * FROM venues WHERE is_active = 1").fetchall()
  players = conn.execute(
      "SELECT * FROM players WHERE calibration_tier != 'INACTIVE' ORDER BY"
      " display_name"
  ).fetchall()
  formats = conn.execute(
      "SELECT * FROM match_formats WHERE is_active = 1 ORDER BY category,"
      " mc_weight DESC, target_games, total_points"
  ).fetchall()
  conn.close()

  v_dict = {v["venue_name"]: dict(v) for v in venues}
  p_dict = {
      f"{format_pr_name(p['display_name'], p['is_provisional'])} (MMR:"
      f" {p['latent_mmr']:.3f})": dict(p)
      for p in players
  }
  f_dict = {f["format_name"]: dict(f) for f in formats}

  c_s1, c_s2, c_s3 = st.columns(3)
  match_date = c_s1.date_input("Match Date", value=date.today())
  match_time = c_s2.time_input("Match Time", value=datetime.now().time())
  ven_sel = c_s3.selectbox(
      "Venue Facility",
      list(v_dict.keys()) if v_dict else ["No Venues Registered"],
  )

  c_m1, c_m2, c_m3 = st.columns([1.5, 2, 1.5])
  is_singles = (
      c_m1.radio(
          "Game Configuration", ["2v2 Doubles", "1v1 Singles"], horizontal=True
      )
      == "1v1 Singles"
  )
  fmt_sel = c_m2.selectbox(
      "Official Scoring Format",
      list(f_dict.keys()) if f_dict else ["No Formats Active"],
  )
  is_tourney = c_m3.checkbox(
      "🏆 Tournament Match (Uncapped + Boost)", value=False
  )

  col_t1, col_t2 = st.columns(2)
  with col_t1:
    st.markdown("##### 🔵 Team A")
    p1_pick = st.selectbox(
        "Player A1 (Required)", ["-- Select --"] + list(p_dict.keys()), key="p1_sel"
    )
    if p1_pick != "-- Select --":
      pm = p_dict[p1_pick]
      st.caption(
          f"**{format_pr_name(pm['display_name'], pm['is_provisional'])}** |"
          f" Display: `{pm['display_rating']:.2f}` | LMMR:"
          f" `{pm['latent_mmr']:.3f}` | Acc: `{pm['rating_accuracy_pct']:.1f}%`"
          f" | RD: `{pm['rating_deviation']:.1f}`"
      )
    p2_pick = (
        st.selectbox(
            "Player A2 (Teammate)",
            ["-- Select --"] + list(p_dict.keys()),
            key="p2_sel",
        )
        if not is_singles
        else "-- None --"
    )
  with col_t2:
    st.markdown("##### 🔴 Team B")
    p3_pick = st.selectbox(
        "Player B1 (Required)", ["-- Select --"] + list(p_dict.keys()), key="p3_sel"
    )
    if p3_pick != "-- Select --":
      pm = p_dict[p3_pick]
      st.caption(
          f"**{format_pr_name(pm['display_name'], pm['is_provisional'])}** |"
          f" Display: `{pm['display_rating']:.2f}` | LMMR:"
          f" `{pm['latent_mmr']:.3f}` | Acc: `{pm['rating_accuracy_pct']:.1f}%`"
          f" | RD: `{pm['rating_deviation']:.1f}`"
      )
    p4_pick = (
        st.selectbox(
            "Player B2 (Teammate)",
            ["-- Select --"] + list(p_dict.keys()),
            key="p4_sel",
        )
        if not is_singles
        else "-- None --"
    )

  sel_f = f_dict[fmt_sel] if f_dict else None
  sets_data, sa, sb, gw, gl = [], 0, 0, 0, 0

  if sel_f:
    cat = sel_f["category"]
    if cat == "MULTI_SET":
      is_b05 = sel_f["format_id"] == "STD_B05"
      target_sets = 3 if is_b05 else 2

      s1_c1, s1_c2 = st.columns(2)
      s1a = s1_c1.number_input("Set 1: Team A", 0, 7, 6, key="s1a")
      s1b = s1_c2.number_input("Set 1: Team B", 0, 7, 3, key="s1b")
      sets_data.append((s1a, s1b))
      s2_c1, s2_c2 = st.columns(2)
      s2a = s2_c1.number_input("Set 2: Team A", 0, 7, 6, key="s2a")
      s2b = s2_c2.number_input("Set 2: Team B", 0, 7, 4, key="s2b")
      sets_data.append((s2a, s2b))

      w_a = sum(1 for s in sets_data if s[0] > s[1])
      w_b = sum(1 for s in sets_data if s[1] > s[0])

      if max(w_a, w_b) < target_sets:
        s3_c1, s3_c2 = st.columns(2)
        s3a = s3_c1.number_input(
            "Set 3: Team A", 0, 7, 6 if w_b > w_a else 3, key="s3a"
        )
        s3b = s3_c2.number_input(
            "Set 3: Team B", 0, 7, 3 if w_b > w_a else 6, key="s3b"
        )
        sets_data.append((s3a, s3b))
        w_a = sum(1 for s in sets_data if s[0] > s[1])
        w_b = sum(1 for s in sets_data if s[1] > s[0])

      if is_b05 and max(w_a, w_b) < target_sets:
        s4_c1, s4_c2 = st.columns(2)
        s4a = s4_c1.number_input(
            "Set 4: Team A", 0, 7, 6 if w_b > w_a else 4, key="s4a"
        )
        s4b = s4_c2.number_input(
            "Set 4: Team B", 0, 7, 4 if w_b > w_a else 6, key="s4b"
        )
        sets_data.append((s4a, s4b))
        w_a = sum(1 for s in sets_data if s[0] > s[1])
        w_b = sum(1 for s in sets_data if s[1] > s[0])

      if is_b05 and max(w_a, w_b) < target_sets:
        s5_c1, s5_c2 = st.columns(2)
        s5a = s5_c1.number_input(
            "Set 5: Team A", 0, 7, 6 if w_b > w_a else 4, key="s5a"
        )
        s5b = s5_c2.number_input(
            "Set 5: Team B", 0, 7, 4 if w_b > w_a else 6, key="s5b"
        )
        sets_data.append((s5a, s5b))
        w_a = sum(1 for s in sets_data if s[0] > s[1])
        w_b = sum(1 for s in sets_data if s[1] > s[0])

      sa, sb = w_a, w_b
      gw, gl = sum(x[0] for x in sets_data), sum(x[1] for x in sets_data)

    elif cat == "RACE_GAMES":
      rg1, rg2 = st.columns(2)
      tg = sel_f["target_games"] or 6
      gw = rg1.number_input("Team A Games", 0, 30, tg)
      gl = rg2.number_input("Team B Games", 0, 30, max(0, tg - 2))
      sa, sb = (1 if gw > gl else 0), (1 if gl > gw else 0)
      sets_data.append((gw, gl))

    elif cat in ("AMERICANO", "MEXICANO"):
      tp = sel_f["total_points"] or 24
      ap1, ap2 = st.columns(2)
      sa = ap1.number_input("Team A Points", 0, tp, tp // 2)
      sb = ap2.number_input("Team B Points", 0, tp, tp - (tp // 2))
      gw, gl = sa, sb
      sets_data.append((sa, sb))

  btn_dry, btn_save = st.columns(2)
  do_dry = btn_dry.button(
      "🔬 Execute Dry Run Simulation", use_container_width=True
  )
  do_save = btn_save.button(
      "💾 Commit Match to Database", type="primary", use_container_width=True
  )

  if do_dry or do_save:
    if (
        p1_pick == "-- Select --"
        or p3_pick == "-- Select --"
        or (not is_singles and (p2_pick == "-- Select --" or p4_pick == "-- Select --"))
    ):
      st.error("Assign all required roster slots.")
    elif (
        sel_f
        and sel_f["category"] in ("AMERICANO", "MEXICANO")
        and (sa + sb != sel_f["total_points"])
    ):
      st.error(f"Points must sum exactly to {sel_f['total_points']}.")
    else:
      p1_obj, p3_obj = dict(p_dict[p1_pick]), dict(p_dict[p3_pick])
      p2_obj = dict(p_dict[p2_pick]) if not is_singles else None
      p4_obj = dict(p_dict[p4_pick]) if not is_singles else None
      ven_obj = dict(v_dict[ven_sel])
      match_ts_val = f"{match_date}T{match_time.strftime('%H:%M:%S')}Z"

      sim_out = RyftV16.compute_match(
          p1_obj,
          p2_obj,
          p3_obj,
          p4_obj,
          sa,
          sb,
          max(gw, gl),
          min(gw, gl),
          sel_f["format_id"],
          ven_obj["venue_id"],
          is_singles,
          is_dry=do_dry,
          is_tournament=is_tourney,
          match_timestamp=match_ts_val,
      )
      st.success(
          f"Match Executed! Team A Odds: {sim_out['ea']*100:.1f}% vs Team B:"
          f" {(1-sim_out['ea'])*100:.1f}% | Margin: {sim_out['mov']:.4f}"
      )

      res_cols = st.columns(2 if is_singles else 4)
      for idx, pr in enumerate(sim_out["res"]):
        with res_cols[idx]:
          is_eff_rusted = pr["pre_rd"] > pr["stored_pre_rd"] + 0.05
          rd_display = (
              f"{pr['pre_rd']:.1f} (eff) ➔ {pr['post_rd']:.1f}"
              if is_eff_rusted
              else f"{pr['pre_rd']:.1f} ➔ {pr['post_rd']:.1f}"
          )

          st.markdown(
              f"""
                    <div style="background-color: #1e293b; border: 2px solid #0284c7; border-radius: 8px; padding: 12px; margin-bottom: 8px; color: #f8fafc;">
                        <h4 style="margin:0 0 8px 0; color:#38bdf8;">{pr['name']}</h4>
                        <div style="color: #cbd5e1; font-size: 0.9em; line-height: 1.6;">
                            Pre MMR: <code style="color: #38bdf8; background: #0f172a;">{pr['pre_r']:.3f}</code><br/>
                            Post MMR: <code style="color: #38bdf8; background: #0f172a;">{pr['post_r']:.3f}</code><br/>
                            Delta: <span style="font-size:1.1em; font-weight:bold; color:{'#4ade80' if pr['delta']>=0 else '#f87171'}">{pr['delta']:+.4f}</span><br/>
                            RD: <code style="color: #e2e8f0; background: #0f172a;">{rd_display}</code><br/>
                            Acc: <code style="color: #e2e8f0; background: #0f172a;">{pr['pre_acc']:.1f}% ➔ {pr['acc']:.1f}%</code><br/>
                            Tier: <span style="background: #0f172a; padding: 2px 6px; border-radius: 4px; font-weight: bold; color: #38bdf8;">{pr['pre_tier']} ➔ {pr['tier']}</span>
                        </div>
                    </div>
                    """,
              unsafe_allow_html=True,
          )
          if pr["flags"]:
            st.caption("⚡ " + " | ".join(pr["flags"]))

      if do_save:
        conn = get_db_connection()
        try:
          m_id = (
              f"M_{datetime.now().strftime('%Y%m%d_%H%M%S')}_{uuid.uuid4().hex[:6]}"
          )
          match_dt = datetime.combine(match_date, match_time)
          ts = match_dt.isoformat()

          ven_city = ven_obj.get("city_id")
          ven_country = ven_obj.get("country_code")

          is_venue_bridge = 0
          is_city_bridge = 0
          is_country_bridge = 0

          all_players = [p1_obj, p3_obj] + (
              [p2_obj, p4_obj] if not is_singles else []
          )
          for p in all_players:
            if p:
              p_c = p.get("home_city_id")
              p_co = p.get("home_country_code")
              p_v = p.get("home_venue_id")

              if p_co and ven_country and p_co != ven_country:
                is_country_bridge = 1
              if p_c and ven_city and p_c != ven_city:
                is_city_bridge = 1
              if p_v and p_v != ven_obj["venue_id"] and p_c == ven_city:
                is_venue_bridge = 1

          all_guardrails = []
          for pr in sim_out["res"]:
            all_guardrails.extend(pr.get("flags", []))

          conn.execute(
              """
                        INSERT INTO matches (
                            match_id, venue_id, format_id, is_singles, is_tournament,
                            is_venue_bridge, is_city_bridge, is_country_bridge,
                            team_a_p1_id, team_a_p2_id, team_b_p1_id, team_b_p2_id,
                            score_team_a, score_team_b, set_scores_json, games_winner, games_loser,
                            pre_rating_a, pre_rating_b, win_expectancy_a, applied_m_c, applied_s_margin,
                            delta_r_p1, delta_r_p2, delta_r_p3, delta_r_p4, guardrails_summary, match_timestamp
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
              (
                  m_id,
                  ven_obj["venue_id"],
                  sel_f["format_id"],
                  1 if is_singles else 0,
                  1 if is_tourney else 0,
                  is_venue_bridge,
                  is_city_bridge,
                  is_country_bridge,
                  p1_obj["player_id"],
                  p2_obj["player_id"] if not is_singles else None,
                  p3_obj["player_id"],
                  p4_obj["player_id"] if not is_singles else None,
                  sa,
                  sb,
                  json.dumps(sets_data),
                  max(gw, gl),
                  min(gw, gl),
                  sim_out["ta_r"],
                  sim_out["tb_r"],
                  sim_out["ea"],
                  sim_out.get("applied_m_c", sel_f.get("mc_weight", 1.0)),
                  sim_out.get("mov", 1.0),
                  sim_out["res"][0]["delta"],
                  sim_out["res"][2]["delta"] if not is_singles else 0.0,
                  sim_out["res"][1]["delta"],
                  sim_out["res"][3]["delta"] if not is_singles else 0.0,
                  json.dumps(all_guardrails),
                  ts,
              ),
          )

          for pr in sim_out["res"]:
            conn.execute(
                """
                            UPDATE players SET 
                                latent_mmr = ?, display_rating = ?, rating_deviation = ?, 
                                rating_accuracy_pct = ?, accuracy_s_rd = ?, accuracy_s_matches = ?, 
                                accuracy_s_diversity = ?, calibration_tier = ?, is_provisional = ?, 
                                consecutive_losses = ?,
                                rolling_90d_peak = max(rolling_90d_peak, ?), 
                                rolling_180d_peak = max(rolling_180d_peak, ?), 
                                rolling_365d_peak = max(rolling_365d_peak, ?), 
                                last_match_time = ? 
                            WHERE player_id = ?
                        """,
                (
                    pr["post_r"],
                    pr["post_disp"],
                    pr["post_rd"],
                    pr["acc"],
                    pr.get("a_rd", 0.0),
                    pr.get("a_m", 0.0),
                    pr.get("a_d", 0.0),
                    pr["tier"],
                    pr["prov"],
                    pr["c_loss"],
                    pr["post_r"],
                    pr["post_r"],
                    pr["post_r"],
                    ts,
                    pr["pid"],
                ),
            )

            conn.execute(
                """
                            INSERT INTO match_logs (
                                log_id, match_id, player_id, pre_latent_mmr, post_latent_mmr, 
                                pre_display_rating, post_display_rating, pre_rd, post_rd, 
                                pre_accuracy_pct, post_accuracy_pct, delta_r, guardrails_triggered, logged_at
                            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                (
                    f"L_{pr['pid']}_{m_id}",
                    m_id,
                    pr["pid"],
                    pr["pre_r"],
                    pr["post_r"],
                    pr["pre_disp"],
                    pr["post_disp"],
                    pr["pre_rd"],
                    pr["post_rd"],
                    pr["pre_acc"],
                    pr["acc"],
                    pr["delta"],
                    json.dumps(pr.get("flags", [])),
                    ts,
                ),
            )

          conn.execute(
              "UPDATE venues SET total_matches_played = total_matches_played +"
              " 1 WHERE venue_id = ?",
              (ven_obj["venue_id"],),
          )
          if is_city_bridge:
            conn.execute(
                "UPDATE venues SET city_bridge_matches_count ="
                " city_bridge_matches_count + 1 WHERE venue_id = ?",
                (ven_obj["venue_id"],),
            )
          if is_country_bridge:
            conn.execute(
                "UPDATE venues SET country_bridge_matches_count ="
                " country_bridge_matches_count + 1 WHERE venue_id = ?",
                (ven_obj["venue_id"],),
            )
          conn.execute(
              "UPDATE locations SET total_matches_played = total_matches_played"
              " + 1 WHERE location_id = ?",
              (ven_obj["city_id"],),
          )

          for p in all_players:
            if p and p.get("player_id"):
              RyftV16.sync_player_aggregates(p["player_id"], conn=conn)

          conn.commit()
          st.balloons()
          st.success("✅ Match successfully committed to database ledger!")
          st.rerun()

        except Exception as e:
          conn.rollback()
          st.error(f"Commit Failed: {str(e)}")
          raise e
        finally:
          conn.close()
import numpy as np


def _generate_mexicano_round(
    standings_sorted_pids, court_picks, current_round, start_match_order
):
  fixtures = []
  fixture_order = start_match_order
  num_courts = max(1, len(court_picks))
  max_courts = max(1, min(num_courts, len(standings_sorted_pids) // 4))
  court_pool = list(standings_sorted_pids[: (max_courts * 4)])

  for c_idx in range(max_courts):
    if len(court_pool) < 4:
      break
    m_players, court_pool = court_pool[:4], court_pool[4:]
    fixtures.append({
        "round_number": current_round,
        "court_id": court_picks[c_idx % num_courts],
        "match_order": fixture_order,
        "team_a_p1_id": m_players[0],
        "team_a_p2_id": m_players[3],
        "team_b_p1_id": m_players[1],
        "team_b_p2_id": m_players[2],
        "group_id": "A",
    })
    fixture_order += 1
  return fixtures


SessionLogicEngine.generate_mexicano_round = staticmethod(
    _generate_mexicano_round
)

if nav == "🗓️ Club Sessions & Mixers":
  st.title("Sessions & Event Traffic Controller")
  st.info(
      "Please use the upgraded **🧠 Session Logic (V16.2 PROD)** tab for"
      " multi-group stage management."
  )

elif nav == "🧠 Session Logic (V16.2 PROD)":
  st.title("Session Logic Engine (V16.2 PROD)")
  st.caption(
      "Multi-stage atomic scheduling, Mexicano phase-gating, and true"
      " chronological evaluation."
  )

  conn = get_db_connection()
  venues = conn.execute(
      "SELECT venue_id, venue_name, court_count, city_id, country_code FROM"
      " venues WHERE is_active = 1"
  ).fetchall()
  players = conn.execute(
      "SELECT * FROM players WHERE calibration_tier != 'INACTIVE' ORDER BY"
      " display_name"
  ).fetchall()
  v_dict = {v["venue_name"]: dict(v) for v in venues}
  p_dict = {
      format_pr_name(p["display_name"], p["is_provisional"]): p["player_id"]
      for p in players
  }
  p_meta = {p["player_id"]: dict(p) for p in players}

  mode = st.radio(
      "Navigation", ["➕ Create Session", "🎮 Active Sessions Hub"], horizontal=True
  )

  if mode == "➕ Create Session":
    st.subheader("1. Session Gateway")
    cs1, cs2 = st.columns(2)
    s_title = cs1.text_input("Session Title", value="Pro-Am Round Robin")
    s_ven = cs2.selectbox(
        "Hosting Venue", list(v_dict.keys()) if v_dict else ["None"]
    )

    cs3, cs4, cs5 = st.columns(3)
    s_date = cs3.date_input("Event Date", value=date.today())
    s_start = cs4.time_input("Start Time", value=time(9, 0))
    s_end = cs5.time_input("End Time", value=time(11, 0))
    s_date_str, s_start_str, s_end_str = (
        str(s_date),
        s_start.strftime("%H:%M"),
        s_end.strftime("%H:%M"),
    )

    avail_courts = v_dict[s_ven]["court_count"] if s_ven in v_dict else 1
    all_court_labels = [f"Court {i+1}" for i in range(avail_courts)]
    overlap_sessions = conn.execute(
        "SELECT session_title, court_ids_json, start_time, end_time FROM"
        " sessions WHERE venue_id = ? AND session_date = ? AND session_status"
        " != 'CANCELLED'",
        (v_dict[s_ven]["venue_id"] if s_ven in v_dict else "", s_date_str),
    ).fetchall()
    booked_courts = set()
    for osess in overlap_sessions:
      if s_start_str < osess["end_time"] and s_end_str > osess["start_time"]:
        booked_courts.update(json.loads(osess["court_ids_json"]))

    available_court_picks = [
        c for c in all_court_labels if c not in booked_courts
    ]
    court_picks = st.multiselect(
        "Select Dedicated Courts",
        available_court_picks,
        default=available_court_picks[: min(2, len(available_court_picks))],
    )

    st.markdown("#### Configuration")
    cs6, cs7, cs8 = st.columns(3)
    s_mode = cs6.selectbox("Lineup Mode", ["DOUBLES", "SINGLES"])
    s_team = cs7.selectbox(
        "Team Mechanics",
        ["FIXED_TEAMS", "ROTATING_TEAMS"]
        if s_mode == "DOUBLES"
        else ["SINGLES"],
    )
    is_tourney_sess = cs8.checkbox(
        "🏆 Official Tournament Session", value=False
    )

    if s_team == "ROTATING_TEAMS":
      compat_formats = conn.execute("""
                SELECT format_id, format_name, category FROM match_formats 
                WHERE category IN ('AMERICANO', 'MEXICANO', 'RACE_GAMES') AND is_active = 1 
                ORDER BY category, format_name
            """).fetchall()
    elif s_team == "FIXED_TEAMS":
      compat_formats = conn.execute("""
                SELECT format_id, format_name, category FROM match_formats 
                WHERE category IN ('MULTI_SET', 'RACE_GAMES') AND is_active = 1 
                ORDER BY category, format_name
            """).fetchall()
    else:
      compat_formats = conn.execute("""
                SELECT format_id, format_name, category FROM match_formats 
                WHERE category IN ('RACE_GAMES', 'MULTI_SET') AND is_active = 1 
                ORDER BY category, format_name
            """).fetchall()

    f_compat_dict = {f["format_name"]: dict(f) for f in compat_formats}
    s_fmt_name = st.selectbox(
        "Scoring Ruleset",
        list(f_compat_dict.keys()) if f_compat_dict else ["None Compatible"],
    )
    sel_format_obj = (
        f_compat_dict[s_fmt_name] if s_fmt_name in f_compat_dict else None
    )

    st.markdown("#### Roster Setup")
    teams_created = []
    enrolled_pids = []

    if s_team == "FIXED_TEAMS":
      num_teams = st.number_input(
          "How many teams?", min_value=2, max_value=32, value=4, step=1
      )
      cs9, cs10 = st.columns(2)
      s_struct = cs9.selectbox(
          "Bracket Type", ["ROUND_ROBIN", "KNOCKOUT", "HYBRID"]
      )
      s_rounds = cs10.number_input(
          "Pool Rounds", min_value=1, max_value=20, value=3
      )
      p_names = list(p_dict.keys())
      for t_idx in range(int(num_teams)):
        tc1, tc2 = st.columns(2)
        tp1 = tc1.selectbox(
            f"Team {t_idx+1} Player 1",
            ["-- Select --"] + p_names,
            key=f"t_p1_{t_idx}",
        )
        tp2 = tc2.selectbox(
            f"Team {t_idx+1} Player 2",
            ["-- Select --"] + p_names,
            key=f"t_p2_{t_idx}",
        )
        if tp1 != "-- Select --" and tp2 != "-- Select --":
          teams_created.append({"p1": p_dict[tp1], "p2": p_dict[tp2]})
          enrolled_pids.extend([p_dict[tp1], p_dict[tp2]])
    else:
      enrolled_names = st.multiselect(
          "Enroll Registered Players (Rotating Roster)", list(p_dict.keys())
      )
      enrolled_pids = [p_dict[p] for p in enrolled_names]
      s_struct = "ROTATION_ROUNDS"

      n_p = len(enrolled_pids)
      n_c = max(1, len(court_picks))
      default_cycle_rounds = n_p if (n_p > 0 and n_p % 2 == 0) else max(3, n_p)
      if n_p == 10 and n_c == 2:
        default_cycle_rounds = 10
      elif n_p == 8 and n_c == 2:
        default_cycle_rounds = 7

      c_col1, c_col2 = st.columns(2)
      cycle_pick = c_col1.selectbox(
          "Americano / Mixer Schedule Depth",
          [
              "1 Full Cycle (Everyone plays with everyone)",
              "2 Full Cycles (Double rotation)",
              "Custom Rounds",
          ],
      )
      if "1 Full Cycle" in cycle_pick:
        s_rounds = default_cycle_rounds
        c_col2.metric("Total Rounds Generated", f"{s_rounds} Rounds")
      elif "2 Full Cycles" in cycle_pick:
        s_rounds = default_cycle_rounds * 2
        c_col2.metric("Total Rounds Generated", f"{s_rounds} Rounds")
      else:
        s_rounds = c_col2.number_input(
            "Custom Rounds", min_value=1, max_value=30, value=7
        )

      if n_p > 0:
        active_per_round = min(n_c * 4, (n_p // 4) * 4)
        sit_per_round = max(0, n_p - active_per_round)
        st.info(
            f"ℹ️ **SESSION RUNTIME ESTIMATOR:** {n_p} players on {n_c} dedicated"
            f" courts. {active_per_round} active on court, {sit_per_round}"
            f" sitting out per round. Total matches scheduled across {s_rounds}"
            f" rounds: {(active_per_round // 4) * s_rounds}."
        )

    if st.button("🚀 Create Session Stage 1", type="primary"):
      min_req = 4 if s_mode == "DOUBLES" else 2
      if len(enrolled_pids) < min_req:
        st.error(f"❌ Requires at least {min_req} participants.")
      elif not court_picks:
        st.error("❌ Select at least one court.")
      elif not sel_format_obj:
        st.error("❌ Valid scoring format required.")
      else:
        s_id = f"SESS_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
        conn.execute(
            """INSERT INTO sessions (
                    session_id, venue_id, session_title, session_date, start_time, end_time, 
                    match_mode, team_format, format_id, tourney_structure, is_tournament, 
                    court_ids_json, enrolled_player_ids, teams_json, player_count, total_rounds, 
                    current_round, created_at, current_stage, lineup_mode
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1, ?, 'CONFIG', ?)""",
            (
                s_id,
                v_dict[s_ven]["venue_id"],
                s_title,
                s_date_str,
                s_start_str,
                s_end_str,
                s_mode,
                s_team,
                sel_format_obj["format_id"],
                s_struct,
                1 if is_tourney_sess else 0,
                json.dumps(court_picks),
                json.dumps(enrolled_pids),
                json.dumps(teams_created),
                len(enrolled_pids),
                int(s_rounds),
                datetime.now(timezone.utc).isoformat(),
                s_mode,
            ),
        )
        for pid in enrolled_pids:
          p_info = p_meta[pid]
          conn.execute(
              """INSERT INTO session_rosters (
                        roster_id, session_id, entity_type, player_id_p1, display_label, initial_mmr, initial_rd
                    ) VALUES (?, ?, ?, ?, ?, ?, ?)""",
              (
                  f"R_{s_id}_{pid}",
                  s_id,
                  "INDIVIDUAL",
                  pid,
                  p_info["display_name"],
                  p_info["latent_mmr"],
                  p_info["rating_deviation"],
              ),
          )

        if sel_format_obj and sel_format_obj["category"] == "MEXICANO":
          fixtures = SessionLogicEngine.generate_mexicano_round(
              enrolled_pids, court_picks, 1, 1
          )
        else:
          fixtures = SessionLogicEngine.generate_schedule(
              s_team,
              s_mode,
              enrolled_pids,
              teams_created,
              court_picks,
              s_rounds,
          )

        for f in fixtures:
          sm_id = f"SM_{s_id}_{f['round_number']}_{f['match_order']}"
          conn.execute(
              """INSERT INTO session_matches (
                        session_match_id, session_id, round_number, court_id, match_order, 
                        team_a_p1_id, team_a_p2_id, team_b_p1_id, team_b_p2_id, match_status, stage
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'SCHEDULED', 'GROUP_STAGE')""",
              (
                  sm_id,
                  s_id,
                  f["round_number"],
                  f["court_id"],
                  f["match_order"],
                  f["team_a_p1_id"],
                  f["team_a_p2_id"],
                  f["team_b_p1_id"],
                  f["team_b_p2_id"],
              ),
          )

        conn.commit()
        st.success("Session initialized!")
        st.rerun()

  elif mode == "🎮 Active Sessions Hub":
    s_tab1, s_tab2, s_tab3 = st.tabs(["⏳ Upcoming", "🔴 Live", "✅ Completed"])

    def render_session_workspace(s_data):
      s_id = s_data["session_id"]
      enrolled_ids = json.loads(s_data["enrolled_player_ids"])
      crts = json.loads(s_data["court_ids_json"])
      id_to_name = {
          p["player_id"]: format_pr_name(
              p["display_name"], p["is_provisional"]
          )
          for p in players
      }
      fmt_info = conn.execute(
          "SELECT category, target_games, total_points, format_name FROM"
          " match_formats WHERE format_id = ?",
          (s_data["format_id"],),
      ).fetchone()
      fmt_category = (
          fmt_info["category"]
          if fmt_info
          else s_data.get("fmt_cat", "RACE_GAMES")
      )
      rosters = conn.execute(
          "SELECT * FROM session_rosters WHERE session_id = ?", (s_id,)
      ).fetchall()
      checked_in_ids = [
          r["player_id_p1"] for r in rosters if r["is_checked_in"]
      ]

      hdr1, hdr2 = st.columns([4, 1])
      hdr1.markdown(
          f"""<div style="background-color: #f8fafc; padding: 12px; border-radius: 8px; border-left: 5px solid #0284c7; margin-bottom:12px;"><h3 style="margin:0; color:#0f172a;">{s_data['session_title']}</h3><strong>Format:</strong> {s_data['format_name']} | <strong>Mode:</strong> {s_data['team_format']} | <strong>Rounds Scheduled:</strong> {s_data['total_rounds']} | <strong>Tournament:</strong> {'YES' if s_data.get('is_tournament', 0) else 'NO'}</div>""",
          unsafe_allow_html=True,
      )
      with hdr2:
        if st.button("🗑️ Discard", key=f"disc_{s_id}"):
          conn.execute(
              "DELETE FROM session_rosters WHERE session_id = ?", (s_id,)
          )
          conn.execute(
              "DELETE FROM session_matches WHERE session_id = ?", (s_id,)
          )
          conn.execute("DELETE FROM sessions WHERE session_id = ?", (s_id,))
          conn.commit()
          st.rerun()

      sub_nav = st.radio(
          "Stage",
          [
              "📋 Check-In Gate",
              "🏟️ Live Court Hub",
              "📊 Standings & Atomic Commit",
          ],
          key=f"snav_{s_id}",
          horizontal=True,
      )

      if sub_nav == "📋 Check-In Gate":
        st.metric("Ready", f"{len(checked_in_ids)} of {len(enrolled_ids)}")
        btn_ci_all, btn_clr_all = st.columns(2)
        if btn_ci_all.button(
            "✅ Check-In All", use_container_width=True, key=f"btn_all_{s_id}"
        ):
          conn.execute(
              "UPDATE session_rosters SET is_checked_in=1 WHERE session_id=?",
              (s_id,),
          )
          conn.execute(
              "UPDATE sessions SET active_checked_in_count=?,"
              " session_status='LIVE', current_stage='CHECKIN' WHERE"
              " session_id=?",
              (len(enrolled_ids), s_id),
          )
          conn.commit()
          st.rerun()
        if btn_clr_all.button(
            "❌ Clear All", use_container_width=True, key=f"btn_clr_{s_id}"
        ):
          conn.execute(
              "UPDATE session_rosters SET is_checked_in=0 WHERE session_id=?",
              (s_id,),
          )
          conn.execute(
              "UPDATE sessions SET active_checked_in_count=0,"
              " session_status='CONFIG' WHERE session_id=?",
              (s_id,),
          )
          conn.commit()
          st.rerun()

        with st.form(f"ci_form_{s_id}"):
          updated_checkins = []
          for r in rosters:
            if st.checkbox(
                f"✅ {id_to_name.get(r['player_id_p1'], r['player_id_p1'])}",
                value=bool(r["is_checked_in"]),
                key=f"chk_{s_id}_{r['player_id_p1']}",
            ):
              updated_checkins.append(r["player_id_p1"])
          if st.form_submit_button("Save Gates"):
            conn.execute(
                "UPDATE session_rosters SET is_checked_in=0 WHERE session_id=?",
                (s_id,),
            )
            for pid in updated_checkins:
              conn.execute(
                  "UPDATE session_rosters SET is_checked_in=1 WHERE"
                  " session_id=? AND player_id_p1=?",
                  (s_id, pid),
              )
            conn.execute(
                "UPDATE sessions SET active_checked_in_count=?,"
                " session_status=? WHERE session_id=?",
                (
                    len(updated_checkins),
                    "LIVE" if len(updated_checkins) > 0 else "CONFIG",
                    s_id,
                ),
            )
            conn.commit()
            st.rerun()

      elif sub_nav == "🏟️ Live Court Hub":
        fixtures = conn.execute(
            "SELECT * FROM session_matches WHERE session_id = ? ORDER BY"
            " round_number ASC, match_order ASC",
            (s_id,),
        ).fetchall()
        tot_rounds = s_data.get("total_rounds", 1)

        # ROUND SELECTOR
        r_tabs = [f"Round {r}" for r in range(1, tot_rounds + 1)] + [
            "All Rounds"
        ]
        sel_r_tab = st.radio(
            "Navigate Fixture Schedule",
            r_tabs,
            horizontal=True,
            key=f"rtab_{s_id}",
        )

        active_round_filter = (
            int(sel_r_tab.split(" ")[1])
            if "Round" in sel_r_tab and sel_r_tab != "All Rounds"
            else None
        )

        display_fixtures = [
            dict(m)
            for m in fixtures
            if active_round_filter is None
            or m["round_number"] == active_round_filter
        ]

        # RENDER PER-ROUND SECTIONS
        distinct_rounds = sorted(
            list(set(m["round_number"] for m in display_fixtures))
        )
        for r_num in distinct_rounds:
          r_matches = [
              m for m in display_fixtures if m["round_number"] == r_num
          ]

          # SIT-OUT / BYE BANNER
          active_in_round = set()
          for rm in r_matches:
            for p_key in [
                "team_a_p1_id",
                "team_a_p2_id",
                "team_b_p1_id",
                "team_b_p2_id",
            ]:
              if rm.get(p_key):
                active_in_round.add(rm[p_key])

          sitting_out = [
              id_to_name.get(pid, pid)
              for pid in enrolled_ids
              if pid not in active_in_round
          ]
          st.markdown(f"#### 🎾 Round {r_num}")
          if sitting_out:
            st.warning(
                f"🛋️ **Round {r_num} Sit-Outs (Byes):** {', '.join(sitting_out)}"
            )

          for m in r_matches:
            ta_players = (
                f"{id_to_name.get(m['team_a_p1_id'])} &"
                f" {id_to_name.get(m['team_a_p2_id'])}"
                if m["team_a_p2_id"]
                else id_to_name.get(m["team_a_p1_id"])
            )
            tb_players = (
                f"{id_to_name.get(m['team_b_p1_id'])} &"
                f" {id_to_name.get(m['team_b_p2_id'])}"
                if m["team_b_p2_id"]
                else id_to_name.get(m["team_b_p1_id"])
            )
            is_done = m["match_status"] in ("STAGED", "COMMITTED")
            status_emoji = "✅ STAGED" if is_done else "⏳ SCHEDULED"

            with st.expander(
                f"[{status_emoji}] Match #{m['match_order']} • {m['court_id']} —"
                f" {ta_players} vs {tb_players}",
                expanded=(not is_done),
            ):
              with st.form(f"score_form_{m['session_match_id']}"):
                sets_recorded = []
                sa, sb, gw, gl = 0, 0, 0, 0
                if fmt_category == "RACE_GAMES":
                  tg = fmt_info["target_games"] or 6
                  rg1, rg2 = st.columns(2)
                  gw = rg1.number_input(
                      f"Games ({ta_players})",
                      0,
                      30,
                      m["score_team_a"] if m["score_team_a"] > 0 else tg,
                      key=f"ga_{m['session_match_id']}",
                  )
                  gl = rg2.number_input(
                      f"Games ({tb_players})",
                      0,
                      30,
                      (
                          m["score_team_b"]
                          if m["score_team_b"] > 0
                          else max(0, tg - 2)
                      ),
                      key=f"gb_{m['session_match_id']}",
                  )
                  sa, sb = (1 if gw > gl else 0), (1 if gl > gw else 0)
                  sets_recorded.append((gw, gl))
                elif fmt_category in ("AMERICANO", "MEXICANO"):
                  tp = fmt_info["total_points"] or 24
                  ap1, ap2 = st.columns(2)
                  sa = ap1.number_input(
                      f"Points: {ta_players}",
                      0,
                      50,
                      m["score_team_a"] if m["score_team_a"] > 0 else tp // 2,
                      key=f"pa_{m['session_match_id']}",
                  )
                  sb = ap2.number_input(
                      f"Points: {tb_players}",
                      0,
                      50,
                      (
                          m["score_team_b"]
                          if m["score_team_b"] > 0
                          else tp - (tp // 2)
                      ),
                      key=f"pb_{m['session_match_id']}",
                  )
                  gw, gl = sa, sb
                  sets_recorded.append((sa, sb))
                else:
                  s1c1, s1c2 = st.columns(2)
                  s1a = s1c1.number_input(
                      f"Set 1: {ta_players}",
                      0,
                      7,
                      6,
                      key=f"s1a_{m['session_match_id']}",
                  )
                  s1b = s1c2.number_input(
                      f"Set 1: {tb_players}",
                      0,
                      7,
                      3,
                      key=f"s1b_{m['session_match_id']}",
                  )
                  sets_recorded.append((s1a, s1b))
                  sa, sb = (1 if s1a > s1b else 0), (1 if s1b > s1a else 0)
                  gw, gl = s1a, s1b

                m_stat = st.selectbox(
                    "Status",
                    ["SCHEDULED", "LIVE", "STAGED", "CANCELLED"],
                    index=[
                        "SCHEDULED",
                        "LIVE",
                        "STAGED",
                        "CANCELLED",
                    ].index(m["match_status"]),
                    key=f"st_{m['session_match_id']}",
                )
                if st.form_submit_button("✅ Stage Score"):
                  conn.execute(
                      "UPDATE session_matches SET score_team_a=?,"
                      " score_team_b=?, games_winner=?, games_loser=?,"
                      " set_scores_json=?, match_status=? WHERE"
                      " session_match_id=?",
                      (
                          sa,
                          sb,
                          max(gw, gl),
                          min(gw, gl),
                          json.dumps(sets_recorded),
                          "STAGED" if m_stat == "SCHEDULED" else m_stat,
                          m["session_match_id"],
                      ),
                  )
                  conn.execute(
                      "UPDATE sessions SET session_status='LIVE',"
                      " current_stage='GROUP_STAGE' WHERE session_id=?",
                      (s_id,),
                  )
                  conn.commit()
                  st.rerun()

      elif sub_nav == "📊 Standings & Atomic Commit":
        st.subheader("Event Standings & Final Engine Commit")
        staged_matches = conn.execute(
            "SELECT * FROM session_matches WHERE session_id = ? AND"
            " match_status = 'STAGED' ORDER BY match_order ASC",
            (s_id,),
        ).fetchall()

        standings = {}
        for pid in enrolled_ids:
          standings[pid] = {
              "Player": id_to_name.get(pid, pid),
              "Played": 0,
              "Won": 0,
              "Lost": 0,
              "Points Won": 0,
              "Points Lost": 0,
              "Diff": 0,
          }
        for sm in staged_matches:
          for pid in [sm["team_a_p1_id"], sm["team_a_p2_id"]]:
            if pid and pid in standings:
              standings[pid]["Played"] += 1
              standings[pid]["Points Won"] += sm["score_team_a"]
              standings[pid]["Points Lost"] += sm["score_team_b"]
              if sm["score_team_a"] > sm["score_team_b"]:
                standings[pid]["Won"] += 1
              elif sm["score_team_a"] < sm["score_team_b"]:
                standings[pid]["Lost"] += 1
          for pid in [sm["team_b_p1_id"], sm["team_b_p2_id"]]:
            if pid and pid in standings:
              standings[pid]["Played"] += 1
              standings[pid]["Points Won"] += sm["score_team_b"]
              standings[pid]["Points Lost"] += sm["score_team_a"]
              if sm["score_team_b"] > sm["score_team_a"]:
                standings[pid]["Won"] += 1
              elif sm["score_team_b"] < sm["score_team_a"]:
                standings[pid]["Lost"] += 1

        for item in standings.values():
          item["Diff"] = item["Points Won"] - item["Points Lost"]
        df_stand = pd.DataFrame(list(standings.values())).sort_values(
            by=["Won", "Diff", "Points Won"], ascending=[False, False, False]
        )
        st.markdown("#### 🏆 Session Standings")
        st.dataframe(df_stand, use_container_width=True)

        st.markdown("---")
        if st.button(
            "🚀 VERIFY & COMMIT SESSION TO RATING ENGINE",
            type="primary",
            use_container_width=True,
        ):
          if not staged_matches:
            st.error("No staged matches to commit.")
          else:
            ts = datetime.now(timezone.utc).isoformat()
            cumulative_deltas = {pid: 0.0 for pid in enrolled_ids}
            temp_ratings = {pid: dict(p_meta[pid]) for pid in enrolled_ids}

            for sm in staged_matches:
              p1_d = {
                  **temp_ratings[sm["team_a_p1_id"]],
                  "player_id": sm["team_a_p1_id"],
              }
              p3_d = {
                  **temp_ratings[sm["team_b_p1_id"]],
                  "player_id": sm["team_b_p1_id"],
              }
              p2_d = (
                  {
                      **temp_ratings[sm["team_a_p2_id"]],
                      "player_id": sm["team_a_p2_id"],
                  }
                  if sm["team_a_p2_id"]
                  else None
              )
              p4_d = (
                  {
                      **temp_ratings[sm["team_b_p2_id"]],
                      "player_id": sm["team_b_p2_id"],
                  }
                  if sm["team_b_p2_id"]
                  else None
              )

              out = RyftV16.compute_match(
                  p1_d,
                  p2_d,
                  p3_d,
                  p4_d,
                  sm["score_team_a"],
                  sm["score_team_b"],
                  max(sm["games_winner"], sm["score_team_a"]),
                  min(sm["games_loser"], sm["score_team_b"]),
                  s_data["format_id"],
                  s_data["venue_id"],
                  is_singles=(s_data["match_mode"] == "SINGLES"),
                  session_id=s_id,
                  session_checked_in=s_data["active_checked_in_count"],
                  conn=conn,
                  cumulative_deltas=cumulative_deltas,
                  match_timestamp=ts,
              )

              m_id = f"M_SESS_{sm['session_match_id']}_{uuid.uuid4().hex[:6]}"
              conn.execute(
                  """INSERT INTO matches (
                                match_id, venue_id, format_id, session_id, is_singles, is_tournament,
                                is_venue_bridge, is_city_bridge, is_country_bridge, team_a_p1_id, team_a_p2_id, team_b_p1_id, team_b_p2_id,
                                score_team_a, score_team_b, set_scores_json, games_winner, games_loser,
                                pre_rating_a, pre_rating_b, win_expectancy_a, applied_m_c, applied_s_margin,
                                delta_r_p1, delta_r_p2, delta_r_p3, delta_r_p4, guardrails_summary, match_timestamp
                            ) VALUES (?, ?, ?, ?, ?, 0, 0, 0, 0, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                  (
                      m_id,
                      s_data["venue_id"],
                      s_data["format_id"],
                      s_id,
                      1 if (s_data["match_mode"] == "SINGLES") else 0,
                      p1_d["player_id"],
                      p2_d["player_id"] if p2_d else None,
                      p3_d["player_id"],
                      p4_d["player_id"] if p4_d else None,
                      sm["score_team_a"],
                      sm["score_team_b"],
                      sm["set_scores_json"],
                      max(sm["games_winner"], sm["score_team_a"]),
                      min(sm["games_loser"], sm["score_team_b"]),
                      out["ta_r"],
                      out["tb_r"],
                      out["ea"],
                      out["applied_m_c"],
                      out["mov"],
                      out["res"][0]["delta"],
                      (
                          out["res"][2]["delta"]
                          if not (s_data["match_mode"] == "SINGLES")
                          else 0.0
                      ),
                      out["res"][1]["delta"],
                      (
                          out["res"][3]["delta"]
                          if not (s_data["match_mode"] == "SINGLES")
                          else 0.0
                      ),
                      json.dumps([pr["flags"] for pr in out["res"]]),
                      ts,
                  ),
              )

              for pr in out["res"]:
                conn.execute(
                    """UPDATE players SET latent_mmr=?, display_rating=?, rating_deviation=?, rating_accuracy_pct=?, calibration_tier=?, is_provisional=?, consecutive_losses=?, last_match_time=? WHERE player_id=?""",
                    (
                        pr["post_r"],
                        pr["post_disp"],
                        pr["post_rd"],
                        pr["acc"],
                        pr["tier"],
                        pr["prov"],
                        pr["c_loss"],
                        ts,
                        pr["pid"],
                    ),
                )
                conn.execute(
                    """INSERT INTO match_logs (log_id, match_id, player_id, pre_latent_mmr, post_latent_mmr, pre_display_rating, post_display_rating, pre_rd, post_rd, pre_accuracy_pct, post_accuracy_pct, delta_r, guardrails_triggered, logged_at) 
                                            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (
                        f"L_{pr['pid']}_{m_id}",
                        m_id,
                        pr["pid"],
                        pr["pre_r"],
                        pr["post_r"],
                        pr["pre_disp"],
                        pr["post_disp"],
                        pr["pre_rd"],
                        pr["post_rd"],
                        pr["pre_acc"],
                        pr["acc"],
                        pr["delta"],
                        json.dumps(pr["flags"]),
                        ts,
                    ),
                )
                cumulative_deltas[pr["pid"]] += pr["delta"]
                temp_ratings[pr["pid"]]["latent_mmr"] = pr["post_r"]
                temp_ratings[pr["pid"]]["rating_deviation"] = pr["post_rd"]

              conn.execute(
                  "UPDATE session_matches SET match_status='COMMITTED',"
                  " committed_match_id=? WHERE session_match_id=?",
                  (m_id, sm["session_match_id"]),
              )

            conn.execute(
                "UPDATE sessions SET session_status='COMPLETED', completed_at=?,"
                " current_stage='COMPLETED' WHERE session_id=?",
                (ts, s_id),
            )
            for pid in enrolled_ids:
              RyftV16.sync_player_aggregates(pid, conn=conn)
            conn.commit()
            st.balloons()
            st.success("✅ Session committed!")
            st.rerun()

    with s_tab1:
      up_sessions = conn.execute("""
                SELECT s.*, v.venue_name, f.format_name, f.category as fmt_cat 
                FROM sessions s JOIN venues v ON s.venue_id = v.venue_id 
                JOIN match_formats f ON s.format_id = f.format_id 
                WHERE s.session_status = 'CONFIG' ORDER BY s.created_at DESC
            """).fetchall()
      if not up_sessions:
        st.info("No upcoming sessions.")
      else:
        sel_up = st.selectbox(
            "Choose Upcoming Session",
            [s["session_title"] for s in up_sessions],
            key="sb_up",
        )
        render_session_workspace(
            [
                dict(s)
                for s in up_sessions
                if s["session_title"] == sel_up
            ][0]
        )

    with s_tab2:
      live_sessions = conn.execute("""
                SELECT s.*, v.venue_name, f.format_name, f.category as fmt_cat 
                FROM sessions s JOIN venues v ON s.venue_id = v.venue_id 
                JOIN match_formats f ON s.format_id = f.format_id 
                WHERE s.session_status = 'LIVE' ORDER BY s.created_at DESC
            """).fetchall()
      if not live_sessions:
        st.info("No live sessions currently in progress.")
      else:
        sel_live = st.selectbox(
            "Choose Live Session",
            [s["session_title"] for s in live_sessions],
            key="sb_live",
        )
        render_session_workspace(
            [
                dict(s)
                for s in live_sessions
                if s["session_title"] == sel_live
            ][0]
        )

    with s_tab3:
      comp_sessions = conn.execute("""
                SELECT s.*, v.venue_name, f.format_name, f.category as fmt_cat 
                FROM sessions s JOIN venues v ON s.venue_id = v.venue_id 
                JOIN match_formats f ON s.format_id = f.format_id 
                WHERE s.session_status = 'COMPLETED' ORDER BY s.completed_at DESC
            """).fetchall()
      if not comp_sessions:
        st.info("No completed sessions.")
      else:
        for csess in comp_sessions:
          with st.expander(
              f"🏆 {csess['session_title']} — {csess['venue_name']}"
              f" ({csess['completed_at'][:10] if csess['completed_at'] else ''})"
          ):
            st.write(
                f"Format: **{csess['format_name']}** | Mode:"
                f" **{csess['team_format']}** | Enrolled:"
                f" **{csess['player_count']}**"
            )
  conn.close()

elif nav == "🏆 Tournament Desk (Delayed)":
  st.title("Tournament Desk (Delayed Entry)")
  st.caption(
      "Asynchronous batch ingestion. Deltas stack additively onto current MMR."
  )

  conn = get_db_connection()
  venues = conn.execute("SELECT * FROM venues WHERE is_active = 1").fetchall()
  v_dict = {v["venue_name"]: dict(v) for v in venues}
  cats = conn.execute(
      "SELECT category_name, min_rating, max_rating FROM rating_categories"
      " ORDER BY sort_order ASC"
  ).fetchall()
  cat_opts = [c["category_name"] for c in cats]
  formats = conn.execute(
      "SELECT * FROM match_formats WHERE is_active = 1 ORDER BY category,"
      " mc_weight DESC, target_games, total_points"
  ).fetchall()
  f_dict = {f["format_name"]: dict(f) for f in formats}
  players = conn.execute(
      "SELECT * FROM players WHERE calibration_tier != 'INACTIVE' ORDER BY"
      " display_name"
  ).fetchall()
  p_meta = {p["player_id"]: dict(p) for p in players}
  p_dict = {
      format_pr_name(p["display_name"], p["is_provisional"]): p["player_id"]
      for p in players
  }
  id_to_name = {
      p["player_id"]: format_pr_name(
          p["display_name"], p["is_provisional"]
      )
      for p in players
  }

  mode = st.radio(
      "Tournament Module",
      ["➕ Register Tournament", "🎮 Manage Delayed Matches"],
      horizontal=True,
  )

  if mode == "➕ Register Tournament":
    st.subheader("Register Official Tournament Bracket")
    with st.form("create_tourney"):
      t_name = st.text_input("Tournament Name")
      t_ven = st.selectbox("Venue", list(v_dict.keys()) if v_dict else [])
      t_date = st.date_input("Tournament Date")
      t_floor = st.selectbox(
          "Tournament Floor (Bracket Entry Cutoff)", cat_opts, index=0
      )
      if st.form_submit_button("Create Tournament"):
        if t_name and t_ven:
          t_id = f"T_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
          conn.execute(
              "INSERT INTO tournaments (tourney_id, name, venue_id,"
              " tourney_date, floor_category, created_at) VALUES (?, ?, ?, ?,"
              " ?, ?)",
              (
                  t_id,
                  t_name,
                  v_dict[t_ven]["venue_id"],
                  str(t_date),
                  t_floor,
                  datetime.now(timezone.utc).isoformat(),
              ),
          )
          conn.commit()
          st.success(f"Tournament {t_name} created!")
          st.rerun()

  elif mode == "🎮 Manage Delayed Matches":
    tourneys = conn.execute(
        "SELECT * FROM tournaments WHERE status = 'PENDING' ORDER BY"
        " created_at DESC"
    ).fetchall()
    if not tourneys:
      st.info("No pending tournaments.")
    else:
      t_sel_name = st.selectbox(
          "Select Tournament", [t["name"] for t in tourneys]
      )
      t_data = [dict(t) for t in tourneys if t["name"] == t_sel_name][0]
      t_id = t_data["tourney_id"]
      floor_max = [
          c for c in cats if c["category_name"] == t_data["floor_category"]
      ][0]["max_rating"]

      st.markdown(
          f"### {t_data['name']} (Floor: {t_data['floor_category']})"
      )
      with st.expander("➕ Add Scorecard", expanded=False):
        with st.form("add_tourney_match"):
          m_time = st.time_input("Exact Match Time", value=time(10, 0))
          t_fmt = st.selectbox("Format", list(f_dict.keys()))
          t_mode = st.radio(
              "Mode", ["DOUBLES", "SINGLES"], horizontal=True
          )
          c1, c2 = st.columns(2)
          p1 = c1.selectbox(
              "Team A P1",
              ["-- Select --"] + list(p_dict.keys()),
              key="tm_p1",
          )
          p2 = (
              c1.selectbox(
                  "Team A P2",
                  ["-- Select --"] + list(p_dict.keys()),
                  key="tm_p2",
              )
              if t_mode == "DOUBLES"
              else "-- Select --"
          )
          p3 = c2.selectbox(
              "Team B P1",
              ["-- Select --"] + list(p_dict.keys()),
              key="tm_p3",
          )
          p4 = (
              c2.selectbox(
                  "Team B P2",
                  ["-- Select --"] + list(p_dict.keys()),
                  key="tm_p4",
              )
              if t_mode == "DOUBLES"
              else "-- Select --"
          )
          cs1, cs2 = st.columns(2)
          sa = cs1.number_input("Team A Score", 0, 100, 0)
          sb = cs2.number_input("Team B Score", 0, 100, 0)

          if st.form_submit_button("Add to Batch"):
            pids = [
                p_dict[p]
                for p in [p1, p2, p3, p4]
                if p and p != "-- Select --" and p != "-- None --"
            ]
            valid = True
            for pid in pids:
              meta = p_meta[pid]
              if (
                  max(meta["rolling_90d_peak"], meta["rolling_180d_peak"])
                  > floor_max
              ):
                st.error(
                    f"❌ Sandbagging Detected: {meta['display_name']} exceeds"
                    " floor cutoff."
                )
                valid = False
            if valid and len(pids) == (4 if t_mode == "DOUBLES" else 2):
              tm_id = f"TM_{datetime.now().strftime('%M%S%f')}"
              conn.execute(
                  """INSERT INTO tourney_matches (t_match_id, tourney_id, match_time, format_id, is_singles, team_a_p1_id, team_a_p2_id, team_b_p1_id, team_b_p2_id, score_team_a, score_team_b, games_winner, games_loser)
                                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                  (
                      tm_id,
                      t_id,
                      f"{t_data['tourney_date']}T{m_time.strftime('%H:%M:%S')}Z",
                      f_dict[t_fmt]["format_id"],
                      1 if t_mode == "SINGLES" else 0,
                      p_dict[p1],
                      p_dict.get(p2),
                      p_dict[p3],
                      p_dict.get(p4),
                      sa,
                      sb,
                      max(sa, sb),
                      min(sa, sb),
                  ),
              )
              conn.commit()
              st.success("Match Staged!")
              st.rerun()

      t_matches = conn.execute(
          "SELECT * FROM tourney_matches WHERE tourney_id = ? ORDER BY"
          " match_time ASC",
          (t_id,),
      ).fetchall()
      st.markdown(f"#### 📋 Staged Scorecards ({len(t_matches)})")
      if t_matches and st.button(
          "🚀 COMMIT TOURNAMENT BATCH & ADDITIVE STACK", type="primary"
      ):
        ts_proc = datetime.now(timezone.utc).isoformat()
        for tm in t_matches:
          ts = tm["match_time"]
          mock_p1, mock_p3 = dict(p_meta[tm["team_a_p1_id"]]), dict(
              p_meta[tm["team_b_p1_id"]]
          )
          mock_p2 = (
              dict(p_meta[tm["team_a_p2_id"]])
              if tm["team_a_p2_id"]
              else None
          )
          mock_p4 = (
              dict(p_meta[tm["team_b_p2_id"]])
              if tm["team_b_p2_id"]
              else None
          )
          out = RyftV16.compute_match(
              mock_p1,
              mock_p2,
              mock_p3,
              mock_p4,
              tm["score_team_a"],
              tm["score_team_b"],
              tm["games_winner"],
              tm["games_loser"],
              tm["format_id"],
              t_data["venue_id"],
              bool(tm["is_singles"]),
              is_tournament=True,
              conn=conn,
              match_timestamp=ts,
          )

          m_id = f"M_TRNY_{tm['t_match_id']}_{uuid.uuid4().hex[:6]}"
          conn.execute(
              """INSERT INTO matches (match_id, venue_id, format_id, is_singles, is_tournament, team_a_p1_id, team_a_p2_id, team_b_p1_id, team_b_p2_id, score_team_a, score_team_b, games_winner, games_loser, delta_r_p1, delta_r_p2, delta_r_p3, delta_r_p4, guardrails_summary, is_retroactive, match_timestamp)
                            VALUES (?, ?, ?, ?, 1, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1, ?)""",
              (
                  m_id,
                  t_data["venue_id"],
                  tm["format_id"],
                  tm["is_singles"],
                  tm["team_a_p1_id"],
                  tm["team_a_p2_id"],
                  tm["team_b_p1_id"],
                  tm["team_b_p2_id"],
                  tm["score_team_a"],
                  tm["score_team_b"],
                  tm["games_winner"],
                  tm["games_loser"],
                  out["res"][0]["delta"],
                  out["res"][2]["delta"] if not tm["is_singles"] else 0.0,
                  out["res"][1]["delta"],
                  out["res"][3]["delta"] if not tm["is_singles"] else 0.0,
                  json.dumps([pr["flags"] for pr in out["res"]]),
                  ts,
              ),
          )
          for pr in out["res"]:
            conn.execute(
                "UPDATE players SET latent_mmr = latent_mmr + ?, display_rating"
                " = display_rating + ?, tournament_floor = max(tournament_floor,"
                " latent_mmr + ?) WHERE player_id=?",
                (pr["delta"], pr["delta"], pr["delta"], pr["pid"]),
            )
            conn.execute(
                """INSERT INTO match_logs (log_id, match_id, player_id, pre_latent_mmr, post_latent_mmr, pre_display_rating, post_display_rating, pre_rd, post_rd, pre_accuracy_pct, post_accuracy_pct, delta_r, is_retroactive, guardrails_triggered, logged_at)
                                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1, ?, ?)""",
                (
                    f"L_{pr['pid']}_{m_id}",
                    m_id,
                    pr["pid"],
                    pr["pre_r"],
                    pr["post_r"],
                    pr["pre_disp"],
                    pr["post_disp"],
                    pr["pre_rd"],
                    pr["post_rd"],
                    pr["pre_acc"],
                    pr["acc"],
                    pr["delta"],
                    json.dumps(pr["flags"]),
                    ts,
                ),
            )
        conn.execute(
            "UPDATE tournaments SET status='COMMITTED' WHERE tourney_id=?",
            (t_id,),
        )
        conn.commit()
        st.balloons()
        st.success("✅ Tournament batch committed!")
        st.rerun()
  conn.close()

elif nav == "📜 Historical Matches":
  st.title("Historical Matches & Deep Algorithmic Audit Ledger")
  conn = get_db_connection()

  # --------------------------------------------------------------------------
  # GRANULAR ADMINISTRATIVE & HAWKING MACRO SYNC AUDIT LEDGER
  # --------------------------------------------------------------------------
  with st.expander(
      "🌐 Administrative & Hawking Macro Sync Audit Ledger", expanded=False
  ):
    st.caption(
        "Audit trail of macro calibration shifts deployed across regional"
        " clusters. Review shifts per run, by territory, or by date."
    )

    h_countries = [
        c[0]
        for c in conn.execute(
            "SELECT DISTINCT location_name FROM locations WHERE location_type"
            " = 'COUNTRY' AND is_active = 1 ORDER BY location_name"
        ).fetchall()
    ]
    h_cities = [
        c[0]
        for c in conn.execute(
            "SELECT DISTINCT location_name FROM locations WHERE location_type ="
            " 'CITY' AND is_active = 1 ORDER BY location_name"
        ).fetchall()
    ]

    f_h1, f_h2, f_h3, f_h4 = st.columns(4)
    sel_h_co = f_h1.selectbox(
        "Filter by Country",
        ["-- All Countries --"] + h_countries,
        key="flt_h_co",
    )
    sel_h_ci = f_h2.selectbox(
        "Filter by City", ["-- All Cities --"] + h_cities, key="flt_h_ci"
    )

    with f_h3:
      filter_date_active = st.checkbox(
          "Filter by Specific Date", value=False, key="chk_h_date"
      )
      sel_h_date = (
          st.date_input("Select Date", value=date.today(), key="val_h_date")
          if filter_date_active
          else None
      )

    sel_h_view = f_h4.radio(
        "Ledger View Mode",
        ["Grouped by Sync Run", "Full Detail Table"],
        horizontal=True,
        key="flt_h_view",
    )

    h_sql = """
            SELECT ml.log_id, ml.match_id, ml.logged_at, ml.player_id, p.display_name, p.is_provisional,
                   ci.location_name as city, co.location_name as country,
                   ml.pre_latent_mmr, ml.post_latent_mmr, ml.delta_r, ml.pre_rd, ml.post_rd, ml.guardrails_triggered
            FROM match_logs ml
            JOIN players p ON ml.player_id = p.player_id
            LEFT JOIN locations ci ON p.home_city_id = ci.location_id
            LEFT JOIN locations co ON ci.parent_id = co.location_id
            WHERE (ml.match_id = 'HAWKING_SYNC' OR ml.match_id LIKE 'HAWKING_%' OR ml.guardrails_triggered LIKE '%GLOBAL_HAWKING_SYNC%')
        """
    h_params = []
    if sel_h_co != "-- All Countries --":
      h_sql += " AND co.location_name = ?"
      h_params.append(sel_h_co)
    if sel_h_ci != "-- All Cities --":
      h_sql += " AND ci.location_name = ?"
      h_params.append(sel_h_ci)
    if filter_date_active and sel_h_date:
      h_sql += " AND date(ml.logged_at) = ?"
      h_params.append(str(sel_h_date))

    h_sql += " ORDER BY ml.logged_at DESC, ml.log_id DESC"
    raw_h_logs = conn.execute(h_sql, h_params).fetchall()

    if not raw_h_logs:
      st.info("No matching administrative Hawking sync events found.")
    else:
      if sel_h_view == "Grouped by Sync Run":
        runs = {}
        for row in raw_h_logs:
          run_key = row["match_id"]
          if run_key == "HAWKING_SYNC":
            run_key = (
                f"RUN_{row['city']}_{row['logged_at'][:16] if row['logged_at'] else 'BATCH'}"
            )
          if run_key not in runs:
            runs[run_key] = {
                "key": run_key,
                "city": row["city"] or "Unknown",
                "country": row["country"] or "Unknown",
                "timestamp": row["logged_at"] or "",
                "records": [],
            }
          runs[run_key]["records"].append(row)

        st.markdown(f"#### 📦 Identified Sync Runs ({len(runs)} Batches)")
        for rk, rdata in runs.items():
          recs = rdata["records"]
          tot_delta = sum(float(r["delta_r"]) for r in recs)
          active_shifted = sum(1 for r in recs if float(r["delta_r"]) != 0.0)
          firewalled = sum(1 for r in recs if float(r["delta_r"]) == 0.0)

          with st.expander(
              f"📅 {rdata['timestamp'][:19]} | 🏙️ {rdata['city']},"
              f" {rdata['country']} — {active_shifted} Shifted ({firewalled}"
              f" Firewalled) | Net Shift: {tot_delta:+.4f}"
          ):
            m_c1, m_c2, m_c3, m_c4 = st.columns(4)
            m_c1.metric("Run Identifier", rk[:22])
            m_c2.metric("Target City", rdata["city"])
            m_c3.metric("Adjusted Roster Count", len(recs))
            m_c4.metric("Net Batch ΔR", f"{tot_delta:+.4f}")

            run_table = []
            for r in recs:
              delta_val = float(r["delta_r"])
              rd_val = float(r["post_rd"])
              rule = (
                  "Provisional Firewall (0.0000)"
                  if delta_val == 0.0 and rd_val > 100.0
                  else (
                      "Jacobian Elasticity"
                      if float(r["pre_latent_mmr"]) > 4.5
                      else "Affine Scaling"
                  )
              )
              run_table.append({
                  "Player": format_pr_name(
                      r["display_name"], r["is_provisional"]
                  ),
                  "Pre MMR": f"{r['pre_latent_mmr']:.4f}",
                  "Applied ΔR": f"{delta_val:+.4f}",
                  "Post MMR": f"{r['post_latent_mmr']:.4f}",
                  "RD": f"{rd_val:.1f}",
                  "Bit 22 Guardrail": rule,
              })
            st.dataframe(pd.DataFrame(run_table), use_container_width=True)

      else:
        st.markdown(
            f"#### 📋 All Individual Player Adjustments ({len(raw_h_logs)}"
            " records)"
        )
        flat_data = []
        for r in raw_h_logs:
          delta_val = float(r["delta_r"])
          flat_data.append({
              "Timestamp": r["logged_at"][:19] if r["logged_at"] else "",
              "Country": r["country"] or "Unknown",
              "City": r["city"] or "Unknown",
              "Player": format_pr_name(
                  r["display_name"], r["is_provisional"]
              ),
              "Pre MMR": f"{r['pre_latent_mmr']:.4f}",
              "Applied ΔR": f"{delta_val:+.4f}",
              "Post MMR": f"{r['post_latent_mmr']:.4f}",
              "Pre RD": f"{r['pre_rd']:.1f}",
              "Post RD": f"{r['post_rd']:.1f}",
              "Rule Applied": (
                  "Provisional Firewall"
                  if delta_val == 0.0 and float(r["post_rd"]) > 100.0
                  else "Affine / Jacobian Scaling"
              ),
              "Match / Batch ID": r["match_id"],
          })
        df_flat = pd.DataFrame(flat_data)
        st.dataframe(df_flat, use_container_width=True)
        st.download_button(
            "⬇️ Export Filtered Hawking Ledger (CSV)",
            data=df_flat.to_csv(index=False),
            file_name=(
                f"Hawking_Audit_Export_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv"
            ),
            mime="text/csv",
        )

  st.markdown("---")
  c1, c2, c3, c4 = st.columns(4)
  c1.metric(
      "Total Matches",
      conn.execute("SELECT COUNT(*) FROM matches").fetchone()[0],
  )
  c2.metric(
      "Venue Bridges",
      conn.execute(
          "SELECT COUNT(*) FROM matches WHERE is_venue_bridge = 1"
      ).fetchone()[0],
  )
  c3.metric(
      "City Bridges",
      conn.execute(
          "SELECT COUNT(*) FROM matches WHERE is_city_bridge = 1"
      ).fetchone()[0],
  )
  c4.metric(
      "Country Bridges",
      conn.execute(
          "SELECT COUNT(*) FROM matches WHERE is_country_bridge = 1"
      ).fetchone()[0],
  )

  f1, f2, f3, f4 = st.columns(4)
  players_all = conn.execute(
      "SELECT player_id, display_name, is_provisional FROM players"
  ).fetchall()
  p_map = {
      format_pr_name(p["display_name"], p["is_provisional"]): p["player_id"]
      for p in players_all
  }
  s_player = f1.selectbox(
      "Filter by Player", ["All Players"] + list(p_map.keys())
  )
  s_venue = f2.selectbox(
      "Filter by Venue",
      ["All Venues"]
      + [
          r["venue_name"]
          for r in conn.execute("SELECT venue_name FROM venues").fetchall()
      ],
  )
  s_city = f3.selectbox(
      "Filter by City",
      ["All Cities"]
      + [
          r["location_name"]
          for r in conn.execute(
              "SELECT location_name FROM locations WHERE location_type ="
              " 'CITY'"
          ).fetchall()
      ],
  )
  sess_list = conn.execute(
      "SELECT session_id, session_title FROM sessions ORDER BY created_at"
      " DESC"
  ).fetchall()
  s_sess_pick = f4.selectbox(
      "Filter by Session",
      ["-- All Matches --", "Non-Session Matches Only"]
      + [f"{s['session_title']} ({s['session_id']})" for s in sess_list],
  )

  sql = """
        SELECT m.*, v.venue_name, l.location_name as city, f.format_name, s.session_title,
               p1.display_name as p1n, p1.is_provisional as p1_prov, p2.display_name as p2n, p2.is_provisional as p2_prov,
               p3.display_name as p3n, p3.is_provisional as p3_prov, p4.display_name as p4n, p4.is_provisional as p4_prov
        FROM matches m JOIN venues v ON m.venue_id = v.venue_id JOIN locations l ON v.city_id = l.location_id JOIN match_formats f ON m.format_id = f.format_id
        LEFT JOIN sessions s ON m.session_id = s.session_id LEFT JOIN players p1 ON m.team_a_p1_id = p1.player_id LEFT JOIN players p2 ON m.team_a_p2_id = p2.player_id 
        LEFT JOIN players p3 ON m.team_b_p1_id = p3.player_id LEFT JOIN players p4 ON m.team_b_p2_id = p4.player_id WHERE 1=1
    """
  params = []
  if s_player != "All Players":
    sql += (
        " AND (? IN (m.team_a_p1_id, m.team_a_p2_id, m.team_b_p1_id,"
        " m.team_b_p2_id))"
    )
    params.append(p_map[s_player])
  if s_venue != "All Venues":
    sql += " AND v.venue_name = ?"
    params.append(s_venue)
  if s_city != "All Cities":
    sql += " AND l.location_name = ?"
    params.append(s_city)
  if s_sess_pick == "Non-Session Matches Only":
    sql += " AND (m.session_id IS NULL OR m.session_id = '')"
  elif s_sess_pick != "-- All Matches --":
    sql += " AND m.session_id = ?"
    params.append(s_sess_pick.split("(")[-1].replace(")", "").strip())

  sql += " ORDER BY m.match_timestamp DESC"
  for m in conn.execute(sql, params).fetchall():
    p1_lbl = (
        format_pr_name(m["p1n"], m["p1_prov"]) if m["p1n"] else ""
    )
    p2_lbl = (
        format_pr_name(m["p2n"], m["p2_prov"]) if m["p2n"] else ""
    )
    p3_lbl = (
        format_pr_name(m["p3n"], m["p3_prov"]) if m["p3n"] else ""
    )
    p4_lbl = (
        format_pr_name(m["p4n"], m["p4_prov"]) if m["p4n"] else ""
    )
    session_badge = (
        f"🗓️ Session: {m['session_title'] or m['session_id']}"
        if m["session_id"]
        else "⚡ Standalone Match"
    )
    if m["is_retroactive"]:
      session_badge = "🏆 Retroactive Tournament"
    bridge_badge = "🏠 Local"
    if m["is_venue_bridge"]:
      bridge_badge = "🌉 Venue Bridge"
    if m["is_city_bridge"]:
      bridge_badge = "🏙️ City Bridge"
    if m["is_country_bridge"]:
      bridge_badge = "🌍 Country Bridge"

    with st.expander(
        f"🎾 {m['match_timestamp'][:10]} | {m['venue_name']} |"
        f" {m['score_team_a']}-{m['score_team_b']} ({m['format_name']}) —"
        f" {session_badge} [{bridge_badge}]"
    ):
      st.write(
          f"**Team A:** {p1_lbl}"
          + (f" & {p2_lbl}" if not m["is_singles"] else "")
          + f" | ΔR: `{m['delta_r_p1']:+.4f}`"
      )
      st.write(
          f"**Team B:** {p3_lbl}"
          + (f" & {p4_lbl}" if not m["is_singles"] else "")
          + f" | ΔR: `{m['delta_r_p3']:+.4f}`"
      )
      st.caption(
          f"Margin Factor: **{m['applied_s_margin']:.4f}** | Guardrails:"
          f" {m['guardrails_summary']}"
      )
      p_logs = conn.execute(
          "SELECT ml.*, p.display_name, p.is_provisional FROM match_logs ml"
          " JOIN players p ON ml.player_id = p.player_id WHERE ml.match_id = ?",
          (m["match_id"],),
      ).fetchall()
      for pl in p_logs:
        st.write(
            f"- **{format_pr_name(pl['display_name'], pl['is_provisional'])}**:"
            f" MMR `{pl['pre_latent_mmr']:.3f} ➔ {pl['post_latent_mmr']:.3f}`"
            f" | Δ: `{pl['delta_r']:+.4f}`"
        )
  conn.close()

elif nav == "👥 Player Roster & Calibration":
  st.title("Players Directory & Calibration Roster")
  conn = get_db_connection()
  c1, c2, c3, c4 = st.columns(4)
  c1.metric(
      "Total Players",
      conn.execute(
          "SELECT COUNT(*) FROM players WHERE calibration_tier != 'INACTIVE'"
      ).fetchone()[0],
  )
  c2.metric(
      "Provisional [PR]",
      conn.execute(
          "SELECT COUNT(*) FROM players WHERE is_provisional = 1 AND"
          " calibration_tier != 'INACTIVE'"
      ).fetchone()[0],
  )
  c3.metric(
      "Verified",
      conn.execute(
          "SELECT COUNT(*) FROM players WHERE is_provisional = 0 AND"
          " calibration_tier != 'INACTIVE'"
      ).fetchone()[0],
  )
  c4.metric(
      "Anchors",
      conn.execute(
          "SELECT COUNT(*) FROM players WHERE is_anchor = 1"
      ).fetchone()[0],
  )

  sql = """SELECT p.player_id, p.display_name, p.all_time_badge, p.initial_rating, p.latent_mmr, p.display_rating, p.rating_deviation, p.rating_accuracy_pct, p.calibration_tier, p.is_provisional, p.is_manually_verified, p.verified_matches_count, p.unique_opponents_count, p.is_active_bridge, l.location_name as city FROM players p JOIN locations l ON p.home_city_id = l.location_id WHERE p.calibration_tier != 'INACTIVE' ORDER BY p.latent_mmr DESC"""
  df_display = pd.read_sql_query(sql, conn)
  df_display["display_name"] = df_display.apply(
      lambda r: format_pr_name(r["display_name"], r["is_provisional"]), axis=1
  )
  st.dataframe(df_display, use_container_width=True)

  col_add, col_edit = st.columns(2)
  with col_add:
    with st.expander("➕ Register New Player", expanded=False):
      p_name = st.text_input("Full Name", key="reg_p_name")
      in_cats = [
          "Beginner (0.500)",
          "Beginner+ (1.000)",
          "Intermediate (2.500)",
          "Intermediate+ (3.500)",
          "Advanced (4.500)",
          "Pro (5.500)",
          "Elite (6.300)",
      ]
      in_pick = st.selectbox(
          "Base Calibration Category", in_cats, key="reg_p_cat"
      )

      all_countries = conn.execute(
          "SELECT DISTINCT country_code FROM locations WHERE location_type ="
          " 'COUNTRY'"
      ).fetchall()
      country_codes = [c["country_code"] for c in all_countries] or ["IND"]
      sel_country = st.selectbox(
          "Home Country", country_codes, key="reg_p_country"
      )

      cities = conn.execute(
          "SELECT location_id, location_name FROM locations WHERE"
          " location_type = 'CITY' AND country_code = ?",
          (sel_country,),
      ).fetchall()
      city_dict = {c["location_name"]: c["location_id"] for c in cities}
      c_sel = st.selectbox(
          "Home City",
          list(city_dict.keys()) if city_dict else ["None"],
          key="reg_p_city",
      )

      venues = (
          conn.execute(
              "SELECT venue_id, venue_name FROM venues WHERE is_active = 1 AND"
              " city_id = ?",
              (city_dict.get(c_sel),),
          ).fetchall()
          if c_sel and c_sel != "None"
          else []
      )
      v_dict_local = {v["venue_name"]: v["venue_id"] for v in venues}
      v_sel = st.selectbox(
          "Home Club / Venue (Optional)",
          ["None"] + list(v_dict_local.keys()),
          key="reg_p_venue",
      )

      c_a1, c_a2 = st.columns(2)
      is_anc = c_a1.checkbox("System Anchor", key="reg_p_anc")
      is_ceil = c_a2.checkbox("Ceiling Anchor", key="reg_p_ceil")

      if st.button(
          "Commit Registration", type="primary", key="btn_reg_player"
      ):
        if not city_dict or c_sel == "None":
          st.error(
              "Create at least one City in 'Venues & Regions' first."
          )
        elif not p_name:
          st.error("Player name cannot be blank.")
        else:
          base_map = {
              "Beginner (0.500)": 0.500,
              "Beginner+ (1.000)": 1.000,
              "Intermediate (2.500)": 2.500,
              "Intermediate+ (3.500)": 3.500,
              "Advanced (4.500)": 4.500,
              "Pro (5.500)": 5.500,
              "Elite (6.300)": 6.300,
          }
          base_r = base_map[in_pick]
          v_id = v_dict_local.get(v_sel) if v_sel != "None" else None
          p_uuid = f"P_{datetime.now().strftime('%d%H%M%S')}"

          conn.execute(
              """INSERT INTO players (player_id, display_name, initial_rating, home_venue_id, home_city_id, home_country_code, latent_mmr, display_rating, rolling_90d_peak, rolling_180d_peak, rolling_365d_peak, tournament_floor, all_time_badge, rating_deviation, rating_accuracy_pct, accuracy_s_rd, accuracy_s_matches, accuracy_s_diversity, calibration_tier, is_provisional, is_manually_verified, is_anchor, is_ceiling_anchor, created_at)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0.0, ?, 350.0, 0.0, 0.0, 0.0, 0.0, 'PROVISIONAL', 1, 0, ?, ?, ?)""",
              (
                  p_uuid,
                  p_name,
                  base_r,
                  v_id,
                  city_dict[c_sel],
                  sel_country,
                  base_r,
                  base_r,
                  base_r,
                  base_r,
                  base_r,
                  in_pick.split(" ")[0],
                  1 if is_anc else 0,
                  1 if is_ceil else 0,
                  datetime.now(timezone.utc).isoformat(),
              ),
          )
          conn.commit()
          st.success(f"Registered {p_name} (PR) at {base_r:.3f}!")
          st.rerun()

  with col_edit:
    with st.expander("✏️ Inspect & Edit Player", expanded=False):
      all_p = conn.execute(
          "SELECT player_id, display_name, is_provisional FROM players ORDER"
          " BY display_name"
      ).fetchall()
      if all_p:
        p_pick = st.selectbox(
            "Select Player to Inspect",
            [p["player_id"] for p in all_p],
            format_func=lambda x: [
                format_pr_name(p["display_name"], p["is_provisional"])
                for p in all_p
                if p["player_id"] == x
            ][0],
        )
        p_data = dict(
            conn.execute(
                "SELECT * FROM players WHERE player_id = ?", (p_pick,)
            ).fetchone()
        )

        st.markdown(
            "#### Profile:"
            f" **{format_pr_name(p_data['display_name'], p_data['is_provisional'])}**"
        )
        c1, c2 = st.columns(2)
        c1.metric("Current MMR", f"{p_data['latent_mmr']:.3f}")
        c2.metric("Rating Accuracy", f"{p_data['rating_accuracy_pct']:.1f}%")

        with st.form("edit_player_form"):
          e_name = st.text_input("Edit Name", value=p_data["display_name"])
          e_mmr = st.number_input(
              "Latent MMR Override",
              value=float(p_data["latent_mmr"]),
              step=0.01,
              format="%.3f",
          )
          e_rd = st.number_input(
              "Rating Deviation (RD)",
              value=float(p_data["rating_deviation"]),
              step=5.0,
          )
          e_man_ver = st.checkbox(
              "Manually Verified (Override Tri-Gate Demotion)",
              value=bool(p_data["is_manually_verified"]),
          )
          e_anc = st.checkbox(
              "System Anchor", value=bool(p_data["is_anchor"])
          )

          if st.form_submit_button("Save Overrides"):
            cat_str, _, _, _ = RyftV16.get_cat_for_rating(e_mmr, conn=conn)
            conn.execute(
                "UPDATE players SET display_name=?, latent_mmr=?,"
                " display_rating=?, rating_deviation=?, is_manually_verified=?,"
                " is_anchor=?, all_time_badge=? WHERE player_id=?",
                (
                    e_name,
                    e_mmr,
                    e_mmr,
                    e_rd,
                    1 if e_man_ver else 0,
                    1 if e_anc else 0,
                    cat_str,
                    p_pick,
                ),
            )
            if e_man_ver:
              conn.execute(
                  "UPDATE players SET calibration_tier = 'VERIFIED',"
                  " is_provisional = 0 WHERE player_id = ?",
                  (p_pick,),
              )
            conn.commit()
            st.success("Player updated!")
            st.rerun()
  conn.close()

elif nav == "🏢 Venues & Regions":
  st.title("Geographical Ecosystem & Regional Drill-Downs")
  conn = get_db_connection()

  c1, c2, c3, c4 = st.columns(4)
  c1.metric(
      "Total Venues",
      conn.execute(
          "SELECT COUNT(*) FROM venues WHERE is_active = 1"
      ).fetchone()[0],
  )
  c2.metric(
      "Total Cities",
      conn.execute(
          "SELECT COUNT(*) FROM locations WHERE location_type = 'CITY' AND"
          " is_active = 1"
      ).fetchone()[0],
  )
  c3.metric(
      "Total Countries",
      conn.execute(
          "SELECT COUNT(*) FROM locations WHERE location_type = 'COUNTRY' AND"
          " is_active = 1"
      ).fetchone()[0],
  )
  c4.metric(
      "Total Physical Courts",
      conn.execute(
          "SELECT SUM(court_count) FROM venues WHERE is_active = 1"
      ).fetchone()[0]
      or 0,
  )

  ba1, ba2, ba3 = st.columns(3)
  with ba1.expander("➕ Add Venue", expanded=False):
    v_name = st.text_input("Venue Name", key="av_name")
    cities = conn.execute("""
            SELECT location_id, location_name, country_code FROM locations 
            WHERE location_type = 'CITY' AND is_active = 1
        """).fetchall()
    c_map = {c["location_name"]: c for c in cities}
    v_c = st.selectbox(
        "Assigned City",
        list(c_map.keys()) if c_map else ["None"],
        key="av_city",
    )
    v_courts = st.number_input("Court Count", 1, 50, 3, key="av_courts")
    v_ver = st.checkbox("Verified Desk Authority", value=True, key="av_ver")
    if st.button("Register Venue", type="primary"):
      if v_name and c_map:
        v_uuid = f"VEN_{datetime.now().strftime('%H%M%S')}"
        conn.execute(
            """INSERT INTO venues (venue_id, venue_name, city_id, country_code, court_count, is_verified, created_at)
                       VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (
                v_uuid,
                v_name,
                c_map[v_c]["location_id"],
                c_map[v_c]["country_code"],
                v_courts,
                1 if v_ver else 0,
                datetime.now(timezone.utc).isoformat(),
            ),
        )
        conn.commit()
        st.success(f"Added {v_name}!")
        st.rerun()

  with ba2.expander("➕ Add City", expanded=False):
    ci_name = st.text_input("City Name", key="ac_name")
    countries = conn.execute(
        "SELECT location_id, location_name, country_code FROM locations WHERE"
        " location_type = 'COUNTRY' AND is_active = 1"
    ).fetchall()
    co_map = {c["location_name"]: c for c in countries}
    co_parent = st.selectbox(
        "Parent Country",
        list(co_map.keys()) if co_map else ["None"],
        key="ac_parent",
    )
    if st.button("Register City", type="primary"):
      if ci_name and co_map:
        c_uuid = f"LOC_{ci_name[:3].upper()}_{datetime.now().strftime('%S')}"
        conn.execute(
            """INSERT INTO locations (location_id, location_type, location_name, parent_id, country_code, updated_at) 
                       VALUES (?, 'CITY', ?, ?, ?, ?)""",
            (
                c_uuid,
                ci_name,
                co_map[co_parent]["location_id"],
                co_map[co_parent]["country_code"],
                datetime.now(timezone.utc).isoformat(),
            ),
        )
        conn.commit()
        st.success(f"Added {ci_name}!")
        st.rerun()

  with ba3.expander("➕ Add Country", expanded=False):
    co_name = st.text_input("Country Name", key="aco_name")
    co_code = st.text_input("ISO 3-Letter Code", key="aco_code").upper()
    if st.button("Register Country", type="primary"):
      if co_name and co_code:
        conn.execute(
            """INSERT INTO locations (location_id, location_type, location_name, country_code, updated_at) 
                       VALUES (?, 'COUNTRY', ?, ?, ?)""",
            (
                f"LOC_{co_code}",
                co_name,
                co_code,
                datetime.now(timezone.utc).isoformat(),
            ),
        )
        conn.commit()
        st.success(f"Added {co_name}!")
        st.rerun()

  st.markdown("---")
  view_mode = st.radio(
      "Inspect Hierarchy By:", ["Countries", "Cities", "Venues"], horizontal=True
  )

  if view_mode == "Countries":
    for co in conn.execute("""
            SELECT l.location_id, l.location_name, l.country_code,
                   COUNT(DISTINCT v.venue_id) as total_venues,
                   SUM(v.court_count) as total_courts,
                   COUNT(DISTINCT pl.player_id) as total_players,
                   COUNT(DISTINCT m.match_id) as total_matches,
                   COUNT(DISTINCT CASE WHEN m.is_country_bridge = 1 THEN m.match_id ELSE NULL END) as country_bridges
            FROM locations l
            LEFT JOIN venues v ON v.country_code = l.country_code AND v.is_active = 1
            LEFT JOIN players pl ON pl.home_country_code = l.country_code AND pl.calibration_tier != 'INACTIVE'
            LEFT JOIN matches m ON v.venue_id = m.venue_id
            WHERE l.location_type = 'COUNTRY' AND l.is_active = 1
            GROUP BY l.location_id
        """).fetchall():
      with st.expander(
          f"🌍 {co['location_name']} ({co['country_code']}) • Players:"
          f" {co['total_players']} | Matches: {co['total_matches']}"
      ):
        k1, k2, k3, k4 = st.columns(4)
        k1.metric("Venues", co["total_venues"])
        k2.metric("Courts", co["total_courts"] or 0)
        k3.metric("Matches Played", co["total_matches"])
        k4.metric("Country Bridges", co["country_bridges"])

  elif view_mode == "Cities":
    for ci in conn.execute("""
            SELECT c.*, p.location_name as parent_country,
                   COUNT(DISTINCT v.venue_id) as c_venues,
                   SUM(v.court_count) as c_courts,
                   COUNT(DISTINCT pl.player_id) as c_players,
                   COUNT(DISTINCT m.match_id) as c_matches,
                   COUNT(DISTINCT CASE WHEN m.is_city_bridge = 1 THEN m.match_id ELSE NULL END) as city_bridges
            FROM locations c 
            JOIN locations p ON c.parent_id = p.location_id 
            LEFT JOIN venues v ON v.city_id = c.location_id AND v.is_active = 1
            LEFT JOIN players pl ON pl.home_city_id = c.location_id AND pl.calibration_tier != 'INACTIVE'
            LEFT JOIN matches m ON v.venue_id = m.venue_id
            WHERE c.location_type = 'CITY' AND c.is_active = 1
            GROUP BY c.location_id
        """).fetchall():
      with st.expander(
          f"🏙️ {ci['location_name']}, {ci['parent_country']} • Players:"
          f" {ci['c_players']} | Matches: {ci['c_matches']}"
      ):
        q1, q2, q3, q4 = st.columns(4)
        q1.metric("Venues", ci["c_venues"])
        q2.metric("Courts", ci["c_courts"] or 0)
        q3.metric("City Bridges", ci["city_bridges"])
        q4.metric("Hawking Offset", f"{ci['hawking_offset']:+.4f}")

  elif view_mode == "Venues":
    for v in conn.execute("""
            SELECT v.*, l.location_name as city, co.location_name as country,
                   COUNT(DISTINCT ml.player_id) as v_players,
                   COUNT(DISTINCT CASE WHEN m.is_venue_bridge = 1 THEN m.match_id ELSE NULL END) as v_bridges
            FROM venues v 
            JOIN locations l ON v.city_id = l.location_id 
            LEFT JOIN locations co ON l.parent_id = co.location_id
            LEFT JOIN matches m ON v.venue_id = m.venue_id
            LEFT JOIN match_logs ml ON m.match_id = ml.match_id
            WHERE v.is_active = 1 
            GROUP BY v.venue_id
            ORDER BY v.total_matches_played DESC
        """).fetchall():
      with st.expander(
          f"🏟️ {v['venue_name']} ({v['city']}, {v['country']}) • Courts:"
          f" {v['court_count']} | Matches: {v['total_matches_played']}"
      ):
        g1, g2, g3, g4 = st.columns(4)
        g1.metric("Unique Players", v["v_players"])
        g2.metric("Venue Bridges", v["v_bridges"])
        g3.metric("City Bridges", v["city_bridge_matches_count"])
        g4.metric(
            "Verified Desk", "ACTIVE" if v["is_verified"] else "UNVERIFIED"
        )
  conn.close()

elif nav == "🌐 Hawking Engine":
  st.title("🌐 Hawking Macro Normalization & Regional Diffusion")
  st.caption(
      "Global macro-calibration suite: monitor systemic rating drift, analyze"
      " national topology, test inter-region parity, and deploy regularized"
      " offsets."
  )

  conn = get_db_connection()
  cfg = RyftV16.get_configs(conn=conn)

  verified_ratings = [
      float(r[0])
      for r in conn.execute("""
        SELECT latent_mmr FROM players 
        WHERE rating_deviation <= 100.0 AND calibration_tier != 'INACTIVE'
    """).fetchall()
  ]

  sys_median = (
      float(np.median(verified_ratings)) if verified_ratings else 3.0000
  )
  sys_drift = sys_median - 3.0000
  total_country_bridges = (
      conn.execute(
          "SELECT COUNT(DISTINCT match_id) FROM matches WHERE is_country_bridge"
          " = 1"
      ).fetchone()[0]
      or 0
  )
  total_city_bridges = (
      conn.execute(
          "SELECT COUNT(DISTINCT match_id) FROM matches WHERE is_city_bridge = 1"
      ).fetchone()[0]
      or 0
  )

  g_col1, g_col2, g_col3, g_col4 = st.columns(4)
  g_col1.metric("Global Anchor Target", "3.0000 MMR")
  g_col2.metric(
      "Observed System Median",
      f"{sys_median:.4f}",
      delta=f"{sys_drift:+.4f}",
      delta_color="inverse",
  )
  g_col3.metric(
      "Global Bridge Matches", f"{total_country_bridges + total_city_bridges}"
  )
  g_col4.metric(
      "Macro Drift Status",
      (
          "BALANCED"
          if abs(sys_drift) <= 0.0500
          else ("CIRCUIT WARNING" if abs(sys_drift) > 0.1000 else "MILD DRIFT")
      ),
  )

  st.markdown("---")

  h_tab1, h_tab2, h_tab3, h_tab4, h_tab5 = st.tabs([
      "🌍 Country & Municipal Hierarchy",
      "⚔️ Cross-Region Parity Analyzer",
      "👻 Synthetic Ghost Sandbox",
      "🕸️ Graph Centrality & Risks",
      "⚙️ Hawking Governance",
  ])

  with h_tab1:
    st.subheader("National & Municipal Regional Drill-Down")

    countries = conn.execute("""
            SELECT l.location_id, l.location_name, l.country_code,
                   COUNT(DISTINCT pl.player_id) as total_players,
                   COUNT(DISTINCT v.venue_id) as total_venues,
                   COUNT(DISTINCT CASE WHEN m.is_country_bridge = 1 THEN m.match_id ELSE NULL END) as country_bridges
            FROM locations l
            LEFT JOIN venues v ON v.country_code = l.country_code AND v.is_active = 1
            LEFT JOIN players pl ON pl.home_country_code = l.country_code AND pl.calibration_tier != 'INACTIVE'
            LEFT JOIN matches m ON v.venue_id = m.venue_id
            WHERE l.location_type = 'COUNTRY' AND l.is_active = 1
            GROUP BY l.location_id
            ORDER BY total_players DESC
        """).fetchall()

    if not countries:
      st.info("No registered countries in database.")
    else:
      for co in countries:
        co_id = co["location_id"]
        co_name = co["location_name"]
        co_code = co["country_code"]

        co_ratings = [
            float(r[0])
            for r in conn.execute(
                """SELECT latent_mmr FROM players 
                   WHERE home_country_code = ? AND rating_deviation <= 100.0 AND calibration_tier != 'INACTIVE'""",
                (co_code,),
            ).fetchall()
        ]
        co_median = float(np.median(co_ratings)) if co_ratings else 0.0000

        with st.expander(
            f"🌍 {co_name} ({co_code}) — {len(co_ratings)} Verified Players |"
            f" National Median: {co_median:.4f} MMR"
        ):
          ck1, ck2, ck3, ck4 = st.columns(4)
          ck1.metric("Venues Operating", co["total_venues"])
          ck2.metric("National Median MMR", f"{co_median:.4f}")
          ck3.metric("Country Bridges", co["country_bridges"])
          ck4.metric(
              "Deviation from Anchor", f"{(co_median - 3.0000):+.4f} vs 3.000"
          )

          st.markdown("#### Municipal Clusters in " + co_name)

          cities = conn.execute(
              """
                        SELECT l.location_id, l.location_name, l.hawking_offset, l.intransitivity_index, l.readiness_score,
                               COUNT(DISTINCT pl.player_id) as total_players,
                               COUNT(DISTINCT m.match_id) as total_matches
                        FROM locations l
                        LEFT JOIN venues v ON l.location_id = v.city_id AND v.is_active = 1
                        LEFT JOIN players pl ON l.location_id = pl.home_city_id AND pl.calibration_tier != 'INACTIVE'
                        LEFT JOIN matches m ON v.venue_id = m.venue_id
                        WHERE l.parent_id = ? AND l.location_type = 'CITY' AND l.is_active = 1
                        GROUP BY l.location_id
                    """,
              (co_id,),
          ).fetchall()

          if not cities:
            st.info(f"No municipalities configured under {co_name}.")
          else:
            for ci in cities:
              cid = ci["location_id"]
              c_name = ci["location_name"]
              n_matches = ci["total_matches"]

              # ACTIVE VERIFIED DENSITY FILTER
              act_ver_count = conn.execute(
                  """
                                SELECT COUNT(DISTINCT p.player_id) 
                                FROM players p 
                                JOIN match_logs ml ON p.player_id = ml.player_id
                                WHERE p.home_city_id = ? AND p.rating_deviation <= 100.0 AND p.calibration_tier != 'INACTIVE'
                            """,
                  (cid,),
              ).fetchone()[0]

              k_bridges = conn.execute(
                  """
                                SELECT COUNT(DISTINCT p.player_id) 
                                FROM players p 
                                JOIN match_logs ml ON p.player_id = ml.player_id
                                JOIN matches m ON ml.match_id = m.match_id
                                WHERE p.home_city_id = ? AND p.rating_deviation <= 80.0 AND m.is_city_bridge = 1
                            """,
                  (cid,),
              ).fetchone()[0]

              diag = evaluate_municipal_readiness(
                  act_ver_count, n_matches, k_bridges, cfg
              )
              it_val = calculate_intransitivity(cid, conn)
              conn.execute(
                  """UPDATE locations SET readiness_score = ?, intransitivity_index = ?, intransitivity_idx = ?, active_bridge_count = ?
                                WHERE location_id = ?""",
                  (diag["readiness_pct"], it_val, it_val, k_bridges, cid),
              )
              conn.commit()

              with st.expander(
                  f"🏙️ {c_name} — Readiness: {diag['readiness_pct']}%"
                  f" [{diag['status']}]"
              ):
                m1, m2, m3, m4, m5 = st.columns(5)
                m1.metric("Active Verified", f"{act_ver_count} (Core)")
                m2.metric("Depth Ratio", f"{diag['activity_depth']:.1f} m/p")
                m3.metric("Bridge Nodes (K)", k_bridges)
                m4.metric("Intransitivity", f"{it_val:.4f}")
                m5.metric("Current Offset", f"{ci['hawking_offset']:+.4f}")

                st.markdown("##### Macro Calibration Pathway")
                pcol1, pcol2 = st.columns([2, 1])
                calc_mode = pcol1.selectbox(
                    f"Calculation Mode ({c_name})",
                    [
                        "Path A: Empirical Bridge Diffusion (K ≥ 1)",
                        "Path B: Synthetic Ghost Sandbox (K = 0 Islands)",
                        "Path C: Dynamic Hybrid Synthesis",
                    ],
                    key=f"mode_{cid}",
                )

                local_ratings_rows = conn.execute(
                    """SELECT latent_mmr FROM players 
                                    WHERE home_city_id = ? AND rating_deviation <= 100.0 AND calibration_tier != 'INACTIVE'""",
                    (cid,),
                ).fetchall()
                local_ratings = [float(r[0]) for r in local_ratings_rows]
                suggested_shift = 0.0000

                if "Path A" in calc_mode:
                  if k_bridges == 0:
                    st.warning(
                        "⚠️ Path A requires K ≥ 1 Bridge Nodes. Current K = 0."
                    )
                    suggested_shift = 0.0000
                  else:
                    w_c = diag["w_conf"]
                    suggested_shift = round(-0.0300 * w_c, 4)
                    st.info(
                        f"Bridge Diffusion Active: K={k_bridges} ➔"
                        f" W_conf={w_c:.4f}. Suggested Shift:"
                        f" {suggested_shift:+.4f}"
                    )

                elif "Path B" in calc_mode:
                  ghosts = [
                      dict(g)
                      for g in conn.execute(
                          "SELECT * FROM synthetic_ghosts WHERE is_active = 1"
                      ).fetchall()
                  ]
                  if not local_ratings:
                    st.error(
                        "No verified players available in city to simulate."
                    )
                  else:
                    sim_res = run_ghost_monte_carlo(
                        local_ratings, ghosts, n_sims=5000
                    )
                    suggested_shift = sim_res["proposed_offset"]
                    st.success(
                        f"Residual Performance: Observed"
                        f" {sim_res['ensemble_win_rate']*100:.1f}% vs Expected"
                        f" {sim_res['expected_win_rate']*100:.1f}% (Δ"
                        f" {sim_res['performance_residual']:+.4f}) ➔ Projected"
                        f" Offset: {suggested_shift:+.4f}"
                    )

                elif "Path C" in calc_mode:
                  ghosts = [
                      dict(g)
                      for g in conn.execute(
                          "SELECT * FROM synthetic_ghosts WHERE is_active = 1"
                      ).fetchall()
                  ]
                  sim_res = (
                      run_ghost_monte_carlo(local_ratings, ghosts, n_sims=5000)
                      if local_ratings
                      else {"proposed_offset": 0.0}
                  )
                  w_c = diag["w_conf"]
                  shift_a = -0.0300 * w_c
                  shift_b = sim_res["proposed_offset"]
                  suggested_shift = round(
                      (w_c * shift_a) + ((1.0 - w_c) * shift_b), 4
                  )
                  st.info(
                      f"Hybrid Blend (W_conf: {w_c:.2f}): Bridge={shift_a:+.4f}"
                      f" | Ghost={shift_b:+.4f} ➔ Blended:"
                      f" {suggested_shift:+.4f}"
                  )

                st.markdown("---")
                with st.expander(
                    f"🔍 Review & Deploy Offset for {c_name}", expanded=False
                ):
                  st.markdown(
                      "**Deployment Rules:** Provisional (RD > 100) receive"
                      " `+0.0000`. Players $\le 4.5$ scale affinely ($R/4.5$)."
                      " Elite Pros ($> 4.5$) scale via Jacobian Elasticity."
                  )

                  approved_step = st.number_input(
                      f"Approved Step ({c_name})",
                      value=float(suggested_shift),
                      step=0.0050,
                      format="%.4f",
                      key=f"step_{cid}",
                  )

                  affected = conn.execute(
                      """SELECT player_id, display_name, latent_mmr, rating_deviation, calibration_tier 
                                    FROM players WHERE home_city_id = ? AND calibration_tier != 'INACTIVE'
                                    ORDER BY latent_mmr DESC""",
                      (cid,),
                  ).fetchall()

                  p_data = []
                  for p in affected:
                    delta = calculate_hawking_player_delta(
                        float(p["latent_mmr"]),
                        float(p["rating_deviation"]),
                        approved_step,
                    )
                    p_data.append({
                        "Player": p["display_name"],
                        "Pre MMR": f"{p['latent_mmr']:.4f}",
                        "RD": f"{p['rating_deviation']:.1f}",
                        "Tier": p["calibration_tier"],
                        "Applied ΔR": f"{delta:+.4f}",
                        "Post MMR": f"{(p['latent_mmr'] + delta):.4f}",
                        "Guardrail": (
                            "Provisional Firewall"
                            if p["rating_deviation"] > 100.0
                            else (
                                "Jacobian Elasticity"
                                if p["latent_mmr"] > 4.5
                                else "Affine Scaling"
                            )
                        ),
                    })

                  if p_data:
                    st.dataframe(pd.DataFrame(p_data), use_container_width=True)

                  if st.button(
                      f"🚀 Approve & Deploy {approved_step:+.4f} to {c_name}",
                      type="primary",
                      key=f"btn_{cid}",
                  ):
                    try:
                      ts_now = datetime.now()
                      sync_batch_id = (
                          f"HAWKING_{cid}_{ts_now.strftime('%Y%m%d_%H%M%S')}"
                      )
                      for p in affected:
                        delta = calculate_hawking_player_delta(
                            float(p["latent_mmr"]),
                            float(p["rating_deviation"]),
                            approved_step,
                        )
                        if delta != 0.0:
                          conn.execute(
                              """UPDATE players SET 
                                                    latent_mmr = latent_mmr + ?, display_rating = display_rating + ?, 
                                                    rolling_90d_peak = rolling_90d_peak + ?, tournament_floor = tournament_floor + ? 
                                                WHERE player_id = ?""",
                              (delta, delta, delta, delta, p["player_id"]),
                          )
                          log_id = f"L_{p['player_id']}_{sync_batch_id}_{uuid.uuid4().hex[:4]}"
                          conn.execute(
                              """INSERT INTO match_logs (
                                                    log_id, match_id, player_id, pre_latent_mmr, post_latent_mmr,
                                                    pre_display_rating, post_display_rating, pre_rd, post_rd,
                                                    pre_accuracy_pct, post_accuracy_pct, delta_r, guardrails_triggered, logged_at
                                                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 0.0, 0.0, ?, ?, CURRENT_TIMESTAMP)""",
                              (
                                  log_id,
                                  sync_batch_id,
                                  p["player_id"],
                                  p["latent_mmr"],
                                  p["latent_mmr"] + delta,
                                  p["latent_mmr"],
                                  p["latent_mmr"] + delta,
                                  p["rating_deviation"],
                                  p["rating_deviation"],
                                  delta,
                                  json.dumps(["GLOBAL_HAWKING_SYNC"]),
                              ),
                          )
                      conn.execute(
                          "UPDATE locations SET hawking_offset = hawking_offset"
                          " + ? WHERE location_id = ?",
                          (approved_step, cid),
                      )
                      conn.commit()
                      st.success(f"Applied offset to {c_name}!")
                      st.rerun()
                    except Exception as e:
                      conn.rollback()
                      st.error(f"Deployment failed: {str(e)}")

  with h_tab2:
    st.subheader("⚔️ Cross-Region Parity Analyzer & Head-to-Head Duel Sandbox")
    r_type = st.radio(
        "Comparison Scope",
        ["City vs City", "Country vs Country"],
        horizontal=True,
    )
    all_locs = conn.execute("""
            SELECT location_id, location_name, location_type, country_code FROM locations 
            WHERE is_active = 1 ORDER BY location_name ASC
        """).fetchall()
    opts = [
        l
        for l in all_locs
        if l["location_type"] == ("CITY" if r_type == "City vs City" else "COUNTRY")
    ]
    opt_dict = {o["location_name"]: o["location_id"] for o in opts}

    if len(opts) >= 2:
      col_a, col_b = st.columns(2)
      reg_a_name = col_a.selectbox(
          "Select Region A", list(opt_dict.keys()), index=0
      )
      reg_b_name = col_b.selectbox(
          "Select Region B",
          list(opt_dict.keys()),
          index=min(1, len(opts) - 1),
      )
      id_a, id_b = opt_dict[reg_a_name], opt_dict[reg_b_name]

      filter_col = (
          "home_city_id" if r_type == "City vs City" else "home_country_code"
      )
      target_val_a = (
          id_a
          if r_type == "City vs City"
          else [o["country_code"] for o in opts if o["location_id"] == id_a][0]
      )
      target_val_b = (
          id_b
          if r_type == "City vs City"
          else [o["country_code"] for o in opts if o["location_id"] == id_b][0]
      )

      ratings_a = [
          float(r[0])
          for r in conn.execute(
              f"""SELECT latent_mmr FROM players WHERE {filter_col} = ? AND rating_deviation <= 100.0 AND calibration_tier != 'INACTIVE'""",
              (target_val_a,),
          ).fetchall()
      ]
      ratings_b = [
          float(r[0])
          for r in conn.execute(
              f"""SELECT latent_mmr FROM players WHERE {filter_col} = ? AND rating_deviation <= 100.0 AND calibration_tier != 'INACTIVE'""",
              (target_val_b,),
          ).fetchall()
      ]

      st.markdown("#### Baseline Parity Breakdown (Verified Residents)")
      p1, p2 = st.columns(2)
      with p1:
        st.markdown(f"**{reg_a_name} Profile**")
        st.write(f"• Verified Players (RD ≤ 100): `{len(ratings_a)}`")
        st.write(
            f"• Median MMR: `{float(np.median(ratings_a)):.4f}`"
            if ratings_a
            else "• Median MMR: `N/A`"
        )
      with p2:
        st.markdown(f"**{reg_b_name} Profile**")
        st.write(f"• Verified Players (RD ≤ 100): `{len(ratings_b)}`")
        st.write(
            f"• Median MMR: `{float(np.median(ratings_b)):.4f}`"
            if ratings_b
            else "• Median MMR: `N/A`"
        )

      min_sample_req = 5
      insufficient_sample = (len(ratings_a) < min_sample_req) or (
          len(ratings_b) < min_sample_req
      )
      if insufficient_sample:
        st.warning(
            "⚠️ **Insufficient Verified Sample for Statistical Parity:** "
            f"{reg_a_name} has {len(ratings_a)} verified players; {reg_b_name}"
            f" has {len(ratings_b)} verified players. "
            f"Cross-region clashes require at least {min_sample_req} verified"
            " players per territory."
        )

      n_clash = st.select_slider(
          "Simulation Sample Depth",
          options=[1000, 5000, 10000, 20000],
          value=10000,
          key="clash_depth",
      )
      if st.button(
          f"⚡ Run 2v2 Clash: {reg_a_name} vs {reg_b_name}",
          type="primary",
          disabled=insufficient_sample,
      ):
        wins_a = 0
        for _ in range(n_clash):
          p_a = random.sample(ratings_a, 2)
          p_b = random.sample(ratings_b, 2)
          r_ta = ((p_a[0] ** 3 + p_a[1] ** 3) / 2.0) ** (1.0 / 3.0)
          r_tb = ((p_b[0] ** 3 + p_b[1] ** 3) / 2.0) ** (1.0 / 3.0)
          ea = 1.0 / (1.0 + 10.0 ** ((r_tb - r_ta) / 2.0))
          if random.random() < ea:
            wins_a += 1

        win_rate_a = wins_a / float(n_clash)
        win_rate_b = 1.0 - win_rate_a
        implied_gap = 2.0 * math.log10(
            max(0.001, win_rate_a) / max(0.001, win_rate_b)
        )

        st.markdown("### 📊 Clash Projection Results")
        cr1, cr2, cr3 = st.columns(3)
        cr1.metric(
            f"{reg_a_name} Projected Win Share", f"{win_rate_a*100:.2f}%"
        )
        cr2.metric(
            f"{reg_b_name} Projected Win Share", f"{win_rate_b*100:.2f}%"
        )
        cr3.metric(
            "Implied Cluster Divergence",
            f"{implied_gap:+.4f} MMR",
            help="Estimated skill gap between the two pools.",
        )

  with h_tab3:
    st.subheader("Ensemble AI Ghost Benchmark Directory")
    st.caption(
        "Manage synthetic bot profiles and evaluate municipal performance"
        " residuals."
    )

    ghost_records = conn.execute("SELECT * FROM synthetic_ghosts").fetchall()
    g_cols = st.columns(len(ghost_records) if ghost_records else 1)
    for i, g in enumerate(ghost_records):
      with g_cols[i]:
        st.markdown(f"### 🤖 {g['ghost_name']}")
        st.write(f"**Playstyle:** `{g['playstyle']}`")
        st.write(f"**Baseline MMR:** `{g['assigned_mmr']:.4f}`")
        st.write(f"**Sims Run:** `{g['sims_run_count']}`")

    st.markdown("---")
    st.subheader("Run Standalone Monte Carlo Duels")
    all_cities = conn.execute(
        "SELECT location_name FROM locations WHERE location_type = 'CITY' AND"
        " is_active = 1"
    ).fetchall()
    sim_c1, sim_c2 = st.columns(2)
    target_c_name = sim_c1.selectbox(
        "Target City", [c[0] for c in all_cities] if all_cities else []
    )
    sim_depth = sim_c2.select_slider(
        "Monte Carlo Iterations",
        options=[1000, 5000, 10000, 20000],
        value=10000,
        key="standalone_ghost_depth",
    )

    if st.button("⚡ Run Full Monte Carlo Duels", type="primary"):
      c_row = conn.execute(
          "SELECT location_id FROM locations WHERE location_name = ?",
          (target_c_name,),
      ).fetchone()
      if c_row:
        p_ratings = [
            float(r[0])
            for r in conn.execute(
                """SELECT latent_mmr FROM players 
                   WHERE home_city_id = ? AND rating_deviation <= 100.0 AND calibration_tier != 'INACTIVE'""",
                (c_row[0],),
            ).fetchall()
        ]
        if not p_ratings:
          st.error("No verified players in city to simulate against ghosts.")
        else:
          ghost_list = [
              dict(g)
              for g in conn.execute(
                  "SELECT * FROM synthetic_ghosts WHERE is_active = 1"
              ).fetchall()
          ]
          res = run_ghost_monte_carlo(p_ratings, ghost_list, n_sims=sim_depth)
          st.markdown("### 📊 Simulation Output")
          r1, r2, r3, r4 = st.columns(4)
          r1.metric("Observed Win Rate", f"{res['ensemble_win_rate']*100:.2f}%")
          r2.metric("Expected Win Rate", f"{res['expected_win_rate']*100:.2f}%")
          r3.metric(
              "Performance Residual", f"{res['performance_residual']:+.4f}"
          )
          r4.metric("Projected Raw Offset", f"{res['proposed_offset']:+.4f}")

  with h_tab4:
    st.subheader("Social Graph Centrality & Disconnection Telemetry")
    disconnected_players = conn.execute("""
            SELECT p.player_id, p.display_name, l.location_name as city, p.latent_mmr, p.rating_deviation,
                   COUNT(ml.log_id) as total_games
            FROM players p
            JOIN locations l ON p.home_city_id = l.location_id
            LEFT JOIN match_logs ml ON p.player_id = ml.player_id
            WHERE p.calibration_tier != 'INACTIVE'
            GROUP BY p.player_id
            HAVING total_games < 3
        """).fetchall()
    if disconnected_players:
      st.warning(
          f"⚠️ Found {len(disconnected_players)} players with low network"
          " connectivity (fewer than 3 recorded games):"
      )
      disc_data = [{
          "Player": p["display_name"],
          "City": p["city"],
          "MMR": f"{p['latent_mmr']:.4f}",
          "RD": f"{p['rating_deviation']:.1f}",
          "Games": p["total_games"],
      } for p in disconnected_players]
      st.dataframe(pd.DataFrame(disc_data), use_container_width=True)
    else:
      st.success("✅ All active players meet minimum graph connectivity.")

  with h_tab5:
    st.subheader("Hawking Governance, Drift Thresholds & Circuit Breakers")
    cfg_rows = conn.execute("""
            SELECT * FROM global_config 
            WHERE param_key LIKE '%HAWKING%' OR param_key LIKE '%BRIDGE%' OR param_key LIKE '%TIKHONOV%' OR param_key LIKE '%DRIFT%' OR param_key LIKE '%INACTIVITY%'
        """).fetchall()
    for c in cfg_rows:
      with st.expander(f"⚙️ {c['param_key']}"):
        st.write(f"**Description:** {c['description']}")
        st.info(f"💡 {c['tuning_guide']}")
        val = st.number_input(
            "Parameter Value",
            value=float(c["param_value"]),
            key=f"hwk_cfg_{c['param_key']}",
        )
        if st.button("Save Parameter", key=f"save_hwk_{c['param_key']}"):
          conn.execute(
              "UPDATE global_config SET param_value = ? WHERE param_key = ?",
              (val, c["param_key"]),
          )
          conn.commit()
          st.success(f"Updated {c['param_key']} -> {val}")
          st.rerun()
  conn.close()

elif nav == "⚙️ Global Config":
  st.title("Parameter Matrix & Rule Controller")
  conn = get_db_connection()
  c1, c2 = st.columns([4, 1])
  if c2.button("🔄 Reset Matrix to Defaults", type="secondary"):
    seed_factory_parameters(conn.cursor(), overwrite_existing=True)
    conn.commit()
    st.success("All Global Parameters reset to factory V.16 baselines!")
    st.rerun()

  tab_prov, tab_ver, tab_fmt, tab_mac = st.tabs([
      "🚀 Provisional Economy",
      "⚖️ Verified Economy",
      "🎾 Match Formats & Tiers",
      "🌐 System Macros",
  ])
  df = pd.read_sql_query(
      "SELECT * FROM global_config ORDER BY module_group, param_key", conn
  )

  def render_params_by_group(df, group_names):
    for grp in group_names:
      st.markdown(f"#### 📁 {grp}")
      subset = df[df["module_group"] == grp]
      for _, row in subset.iterrows():
        with st.expander(f"⚙️ {row['param_key']} — {row['title']}"):
          st.write(row["description"])
          st.info(f"💡 **Tuning Impact:** {row['tuning_guide']}")
          c1, c2 = st.columns([3, 1])
          new_v = c1.number_input(
              "Parameter Value",
              value=float(row["param_value"]),
              step=0.05,
              key=f"val_{row['param_key']}",
          )
          is_act = c2.checkbox(
              "Active",
              value=bool(row["is_active"]),
              key=f"act_{row['param_key']}",
          )
          if st.button(
              f"Save {row['param_key']}", key=f"btn_{row['param_key']}"
          ):
            conn.execute(
                "UPDATE global_config SET param_value=?, is_active=? WHERE"
                " param_key=?",
                (new_v, 1 if is_act else 0, row["param_key"]),
            )
            conn.commit()
            st.toast(f"Saved {row['param_key']} -> {new_v}")
            st.rerun()

  with tab_prov:
    render_params_by_group(
        df, ["3. Margins & Rightsizing", "7. Accuracy & Tri-Gates"]
    )
  with tab_ver:
    render_params_by_group(df, [
        "2. Volatility & Odds",
        "4. Partner Guardrails",
        "5. Exchange Caps & Security",
    ])
  with tab_fmt:
    st.markdown("### 🏆 Rating Categories, Ranges & Progression Speeds")
    cats_data = conn.execute(
        "SELECT * FROM rating_categories ORDER BY sort_order ASC"
    ).fetchall()
    with st.form("cat_ranges_form"):
      updated_ranges = []
      for cat in cats_data:
        c1, c2, c3, c4 = st.columns([2, 1.5, 1.5, 1.5])
        new_name = c1.text_input(
            f"Category #{cat['sort_order']}",
            value=cat["category_name"],
            key=f"name_{cat['sort_order']}",
        )
        new_min = c2.number_input(
            f"Min ({new_name})",
            0.000,
            7.000,
            float(cat["min_rating"]),
            0.050,
            format="%.3f",
            key=f"min_{cat['sort_order']}",
        )
        new_max = c3.number_input(
            f"Max ({new_name})",
            0.000,
            7.000,
            float(cat["max_rating"]),
            0.050,
            format="%.3f",
            key=f"max_{cat['sort_order']}",
        )
        new_speed = c4.number_input(
            f"Speed ({new_name})",
            0.10,
            3.00,
            float(cat["speed_multiplier"] or 1.00),
            0.05,
            format="%.2f",
            key=f"spd_{cat['sort_order']}",
        )
        updated_ranges.append({
            "name": new_name,
            "orig_name": cat["category_name"],
            "min": new_min,
            "max": new_max,
            "speed": new_speed,
            "order": cat["sort_order"],
        })

      if st.form_submit_button(
          "Verify & Commit Category Boundaries & Speeds"
      ):
        has_error, err_msg = False, ""
        if updated_ranges[0]["min"] != 0.000:
          has_error, err_msg = True, "First category must start at 0.000!"
        elif updated_ranges[-1]["max"] != 7.000:
          has_error, err_msg = True, "Last category must end at 7.000!"
        else:
          for i in range(len(updated_ranges) - 1):
            if updated_ranges[i]["min"] >= updated_ranges[i]["max"]:
              has_error, err_msg = (
                  True,
                  f"Category '{updated_ranges[i]['name']}' has Min >= Max!",
              )
              break
        if has_error:
          st.error(f"❌ {err_msg}")
        else:
          for ur in updated_ranges:
            conn.execute(
                "UPDATE rating_categories SET category_name=?, min_rating=?,"
                " max_rating=?, speed_multiplier=? WHERE sort_order=?",
                (
                    ur["name"],
                    ur["min"],
                    ur["max"],
                    ur["speed"],
                    ur["order"],
                ),
            )
          conn.commit()
          st.success("Categories and Speeds Saved!")
          st.rerun()

    st.markdown("---")
    st.markdown(
        "### 🎾 Official Match Formats & Confidence Multipliers ($M_C$)"
    )
    all_formats = conn.execute(
        "SELECT format_id, format_name, category, mc_weight FROM match_formats"
        " ORDER BY category, mc_weight DESC, target_games, total_points"
    ).fetchall()
    with st.expander(
        "🛠️ Edit Format Weights ($M_C$ Multipliers)", expanded=False
    ):
      with st.form("edit_mc_weights_form"):
        updated_mc = {}
        for fmt in all_formats:
          fc1, fc2, fc3 = st.columns([3, 2, 2])
          fc1.write(f"**{fmt['format_name']}** (`{fmt['format_id']}`)")
          fc2.caption(f"Category: {fmt['category']}")
          updated_mc[fmt["format_id"]] = fc3.number_input(
              "Weight",
              0.10,
              1.50,
              float(fmt["mc_weight"]),
              0.05,
              key=f"mc_{fmt['format_id']}",
          )
        if st.form_submit_button(
            "Save Format Confidence Weights ($M_C$)"
        ):
          for fid, weight in updated_mc.items():
            conn.execute(
                "UPDATE match_formats SET mc_weight = ? WHERE format_id = ?",
                (weight, fid),
            )
            conn.execute(
                "INSERT INTO global_config (param_key, param_value, is_active,"
                " title, description, module_group) VALUES (?, ?, 1, ?, ?,"
                " '9. Format Multipliers (M_C)') ON CONFLICT(param_key) DO"
                " UPDATE SET param_value=excluded.param_value",
                (
                    f"MC_{fid}",
                    weight,
                    f"M_C {fid}",
                    f"Confidence weight for {fid}",
                ),
            )
          conn.commit()
          st.success(
              "Format weights committed and synced with Global Config!"
          )
          st.rerun()
    render_params_by_group(df, ["9. Format Multipliers (M_C)"])

  with tab_mac:
    render_params_by_group(
        df,
        ["1. Core Bounds & Drag", "6. Uncertainty & Rust", "8. Hawking Macro"],
    )
  conn.close()

elif nav == "📄 RYFT Documentation":
  st.title("RYFT Engine V.16.4 — Master Specification Document")
  if REPORTLAB_AVAILABLE:
    st.success("✅ PDF Compiler Engine is active.")
    pdf_bytes = build_pdf_document()
    if pdf_bytes:
      st.download_button(
          "📄 DOWNLOAD RYFT MASTER SPECIFICATION (PDF)",
          data=pdf_bytes,
          file_name="Ryft_Engine_Primary_v16_4.pdf",
          mime="application/pdf",
          use_container_width=True,
      )
  else:
    st.error(
        "❌ `reportlab` library missing. Add `reportlab` to requirements.txt"
    )
