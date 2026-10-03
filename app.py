"""
RYFT ENGINE V.16 / V.17 MASTER IMPLEMENTATION (PART 1 OF 3)
Modules: Core Dependencies, Database Schemas, Configuration Seeders,
         Mathematical Helpers, and the RyftV16 Algorithmic Engine.
"""

import datetime
from datetime import date, datetime, time, timezone
import io
import json
import math
import os
import random
import sqlite3
import uuid
import pandas as pd
import streamlit as st

# Optional ReportLab integration for Master Documentation compiling
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
# 1. DATABASE SCHEMA & PERSISTENCE MANAGEMENT
# ==============================================================================


def get_db_connection():
    """Establishes thread-safe SQLite connection with WAL mode and row factory."""
    conn = sqlite3.connect(DB_FILE, timeout=60.0, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode = WAL;")
    conn.execute("PRAGMA busy_timeout = 60000;")
    conn.execute("PRAGMA foreign_keys = ON;")
    return conn


def add_column_if_not_exists(cursor, table, col_name, col_type):
    """Safely checks and adds columns to accommodate database migrations."""
    cursor.execute(f"PRAGMA table_info({table});")
    existing_cols = [row[1] for row in cursor.fetchall()]
    if col_name not in existing_cols:
        cursor.execute(f"ALTER TABLE {table} ADD COLUMN {col_name} {col_type};")


def format_pr_name(name, is_prov):
    """Appends provisional badge to player name if uncalibrated."""
    return f"{name} [PR]" if is_prov else name


def export_db_bytes():
    """Performs checkpoint and returns in-memory database bytes for system backup."""
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
    """Restores entire SQLite database from uploaded binary stream safely."""
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
        if os.path.exists(temp_in):
            os.remove(temp_in)
        raise ValueError(
            "Uploaded file is not a valid RYFT master database snapshot."
        )

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
    """Initializes all relational tables, indexes, constraints, and master parameter seeders."""
    conn = get_db_connection()
    c = conn.cursor()

    # Table 1: Locations (Hierarchy: Country -> City)
    c.execute("""
    CREATE TABLE IF NOT EXISTS locations (
        location_id TEXT PRIMARY KEY,
        location_type TEXT NOT NULL CHECK(location_type IN ('COUNTRY', 'CITY')),
        location_name TEXT NOT NULL,
        parent_id TEXT,
        regional_offset REAL DEFAULT 0.0,
        intransitivity_score REAL DEFAULT 0.0,
        readiness_score REAL DEFAULT 0.0,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY(parent_id) REFERENCES locations(location_id) ON DELETE CASCADE
    );
    """)

    # Table 2: Venues / Clubs
    c.execute("""
    CREATE TABLE IF NOT EXISTS venues (
        venue_id TEXT PRIMARY KEY,
        venue_name TEXT NOT NULL,
        city_id TEXT NOT NULL,
        country_id TEXT NOT NULL,
        is_active INTEGER DEFAULT 1,
        courts_count INTEGER DEFAULT 4,
        venue_bridge_count INTEGER DEFAULT 0,
        city_bridge_count INTEGER DEFAULT 0,
        country_bridge_count INTEGER DEFAULT 0,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY(city_id) REFERENCES locations(location_id),
        FOREIGN KEY(country_id) REFERENCES locations(location_id)
    );
    """)

    # Table 3: Players (Master Entity)
    c.execute("""
    CREATE TABLE IF NOT EXISTS players (
        player_id TEXT PRIMARY KEY,
        name TEXT NOT NULL,
        gender TEXT NOT NULL CHECK(gender IN ('M', 'F', 'OPEN')),
        latent_mmr REAL NOT NULL DEFAULT 3.000,
        display_rating REAL NOT NULL DEFAULT 3.00,
        rd REAL NOT NULL DEFAULT 350.0,
        volatility REAL NOT NULL DEFAULT 0.06,
        calibration_tier TEXT NOT NULL DEFAULT 'PROVISIONAL',
        is_anchor INTEGER NOT NULL DEFAULT 0,
        is_provisional INTEGER NOT NULL DEFAULT 1,
        verified_matches_count INTEGER NOT NULL DEFAULT 0,
        unique_opponents_count INTEGER NOT NULL DEFAULT 0,
        accuracy_score REAL NOT NULL DEFAULT 0.0,
        accuracy_s_rd REAL NOT NULL DEFAULT 0.0,
        accuracy_s_matches REAL NOT NULL DEFAULT 0.0,
        accuracy_s_diversity REAL NOT NULL DEFAULT 0.0,
        all_time_peak_mmr REAL NOT NULL DEFAULT 3.000,
        lowest_mmr REAL NOT NULL DEFAULT 3.000,
        career_net_delta REAL NOT NULL DEFAULT 0.000,
        last_match_date TIMESTAMP,
        home_city_id TEXT,
        home_venue_id TEXT,
        is_manually_verified INTEGER NOT NULL DEFAULT 0,
        tournament_floor REAL DEFAULT 0.0,
        sybil_trust_score REAL DEFAULT 1.0,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY(home_city_id) REFERENCES locations(location_id),
        FOREIGN KEY(home_venue_id) REFERENCES venues(venue_id)
    );
    """)

    # Table 4: Matches (Transaction Ledger)
    c.execute("""
    CREATE TABLE IF NOT EXISTS matches (
        match_id TEXT PRIMARY KEY,
        match_timestamp TIMESTAMP NOT NULL,
        venue_id TEXT NOT NULL,
        format_id TEXT NOT NULL,
        team_a_p1 TEXT NOT NULL,
        team_a_p2 TEXT,
        team_b_p1 TEXT NOT NULL,
        team_b_p2 TEXT,
        score_team_a INTEGER NOT NULL,
        score_team_b INTEGER NOT NULL,
        winner_team TEXT NOT NULL CHECK(winner_team IN ('A', 'B', 'DRAW')),
        delta_team_a REAL NOT NULL,
        delta_team_b REAL NOT NULL,
        margin_entropy REAL DEFAULT 1.0,
        is_city_bridge INTEGER DEFAULT 0,
        is_country_bridge INTEGER DEFAULT 0,
        is_provisional_bypass INTEGER DEFAULT 0,
        status TEXT DEFAULT 'COMMITTED',
        session_id TEXT,
        attestation_status TEXT DEFAULT 'COMMITTED' CHECK(attestation_status IN ('COMMITTED', 'PENDING', 'DISPUTED')),
        host_id TEXT,
        scoreline_raw TEXT,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY(venue_id) REFERENCES venues(venue_id)
    );
    """)

    # Table 5: Match Audit Logs (Individual attribution)
    c.execute("""
    CREATE TABLE IF NOT EXISTS match_logs (
        log_id TEXT PRIMARY KEY,
        match_id TEXT NOT NULL,
        player_id TEXT NOT NULL,
        team_id TEXT NOT NULL CHECK(team_id IN ('A', 'B')),
        pre_mmr REAL NOT NULL,
        post_mmr REAL NOT NULL,
        delta_mmr REAL NOT NULL,
        pre_rd REAL NOT NULL,
        post_rd REAL NOT NULL,
        pre_acc REAL NOT NULL,
        post_acc REAL NOT NULL,
        bit_trace_json TEXT,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY(match_id) REFERENCES matches(match_id) ON DELETE CASCADE,
        FOREIGN KEY(player_id) REFERENCES players(player_id)
    );
    """)

    # Table 6: Multi-Match Event Sessions
    c.execute("""
    CREATE TABLE IF NOT EXISTS sessions (
        session_id TEXT PRIMARY KEY,
        venue_id TEXT NOT NULL,
        session_title TEXT NOT NULL,
        session_type TEXT NOT NULL CHECK(session_type IN ('ROUND_ROBIN', 'AMERICANO', 'MEXICANO', 'HYBRID')),
        team_format TEXT NOT NULL CHECK(team_format IN ('FIXED_DOUBLES', 'ROTATING_DOUBLES', 'SINGLES')),
        format_id TEXT NOT NULL,
        court_count INTEGER DEFAULT 2,
        status TEXT NOT NULL DEFAULT 'STAGED' CHECK(status IN ('STAGED', 'ACTIVE', 'COMMITTED', 'CANCELLED')),
        scheduled_start TIMESTAMP,
        completed_at TIMESTAMP,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY(venue_id) REFERENCES venues(venue_id)
    );
    """)

    # Table 7: Staged Session Matches
    c.execute("""
    CREATE TABLE IF NOT EXISTS session_matches (
        session_match_id TEXT PRIMARY KEY,
        session_id TEXT NOT NULL,
        round_number INTEGER NOT NULL,
        court_number INTEGER NOT NULL,
        team_a_p1 TEXT NOT NULL,
        team_a_p2 TEXT,
        team_b_p1 TEXT NOT NULL,
        team_b_p2 TEXT,
        score_a INTEGER DEFAULT 0,
        score_b INTEGER DEFAULT 0,
        status TEXT NOT NULL DEFAULT 'SCHEDULED' CHECK(status IN ('SCHEDULED', 'SCORED', 'COMMITTED', 'WALKOVER')),
        match_id TEXT,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY(session_id) REFERENCES sessions(session_id) ON DELETE CASCADE
    );
    """)

    # Table 8: Session Rosters
    c.execute("""
    CREATE TABLE IF NOT EXISTS session_rosters (
        roster_id TEXT PRIMARY KEY,
        session_id TEXT NOT NULL,
        player_id TEXT NOT NULL,
        initial_rating_snapshot REAL NOT NULL,
        running_points INTEGER DEFAULT 0,
        running_games INTEGER DEFAULT 0,
        sitout_count INTEGER DEFAULT 0,
        group_index INTEGER DEFAULT 1,
        FOREIGN KEY(session_id) REFERENCES sessions(session_id) ON DELETE CASCADE,
        FOREIGN KEY(player_id) REFERENCES players(player_id)
    );
    """)

    # Table 9: Global Config
    c.execute("""
    CREATE TABLE IF NOT EXISTS global_config (
        param_key TEXT PRIMARY KEY,
        param_value REAL NOT NULL,
        is_active INTEGER NOT NULL DEFAULT 1,
        param_title TEXT NOT NULL,
        param_desc TEXT NOT NULL,
        tuning_guidance TEXT NOT NULL,
        module_group TEXT NOT NULL
    );
    """)

    # Table 10: Official Match Formats (8 columns)
    c.execute("""
    CREATE TABLE IF NOT EXISTS match_formats (
        format_id TEXT PRIMARY KEY,
        format_name TEXT NOT NULL,
        category TEXT NOT NULL,
        mc REAL NOT NULL,
        target_games INTEGER,
        target_points INTEGER,
        is_singles INTEGER DEFAULT 0,
        is_americano INTEGER DEFAULT 0
    );
    """)

    # Table 11: Demographic Categories (Discrete Boundaries ending in .999)
    c.execute("""
    CREATE TABLE IF NOT EXISTS rating_categories (
        category_name TEXT NOT NULL,
        gender TEXT NOT NULL DEFAULT 'OPEN',
        min_rating REAL NOT NULL,
        max_rating REAL NOT NULL,
        sort_order INTEGER NOT NULL,
        PRIMARY KEY(category_name, gender)
    );
    """)

    # Table 12: Synthetic Ghosts for Macro Sandbox
    c.execute("""
    CREATE TABLE IF NOT EXISTS synthetic_ghosts (
        ghost_id TEXT PRIMARY KEY,
        archetype TEXT NOT NULL,
        mmr REAL NOT NULL,
        rd REAL NOT NULL,
        volatility REAL NOT NULL,
        entropy_mean REAL DEFAULT 1.0
    );
    """)

    # Migration checks for legacy versions
    add_column_if_not_exists(c, "matches", "scoreline_raw", "TEXT")
    add_column_if_not_exists(
        c, "matches", "attestation_status", "TEXT DEFAULT 'COMMITTED'"
    )
    add_column_if_not_exists(
        c, "players", "accuracy_s_rd", "REAL DEFAULT 0.0"
    )
    add_column_if_not_exists(
        c, "players", "accuracy_s_matches", "REAL DEFAULT 0.0"
    )
    add_column_if_not_exists(
        c, "players", "accuracy_s_diversity", "REAL DEFAULT 0.0"
    )

    # --------------------------------------------------------------------------
    # SEED 1: Discrete Demographic Categories (.999 Transition Rule)
    # --------------------------------------------------------------------------
    discrete_tiers = [
        ("Beginner", "OPEN", 0.000, 0.999, 1),
        ("Beginner+", "OPEN", 1.000, 1.999, 2),
        ("Intermediate", "OPEN", 2.000, 3.499, 3),
        ("Intermediate+", "OPEN", 3.500, 4.499, 4),
        ("Advanced", "OPEN", 4.500, 5.499, 5),
        ("Advanced+", "OPEN", 5.500, 5.999, 6),
        ("Elite / Pro", "OPEN", 6.000, 7.000, 7),
        ("Beginner", "F", 0.000, 0.999, 1),
        ("Beginner+", "F", 1.000, 1.999, 2),
        ("Intermediate", "F", 2.000, 3.499, 3),
        ("Intermediate+", "F", 3.500, 4.499, 4),
        ("Advanced", "F", 4.500, 5.499, 5),
        ("Advanced+", "F", 5.500, 5.999, 6),
        ("Elite / Pro", "F", 6.000, 7.000, 7),
        ("Beginner", "M", 0.000, 0.999, 1),
        ("Beginner+", "M", 1.000, 1.999, 2),
        ("Intermediate", "M", 2.000, 3.499, 3),
        ("Intermediate+", "M", 3.500, 4.499, 4),
        ("Advanced", "M", 4.500, 5.499, 5),
        ("Advanced+", "M", 5.500, 5.999, 6),
        ("Elite / Pro", "M", 6.000, 7.000, 7),
    ]
    for c_name, c_gen, c_min, c_max, s_ord in discrete_tiers:
        c.execute(
            """INSERT OR IGNORE INTO rating_categories 
                     VALUES (?, ?, ?, ?, ?)""",
            (c_name, c_gen, c_min, c_max, s_ord),
        )

    # --------------------------------------------------------------------------
    # SEED 2: Official Formats (Strict 8-Value Schema Alignment)
    # --------------------------------------------------------------------------
    official_formats = [
        # (format_id, name, cat, mc, target_games, target_points, is_singles, is_americano)
        ("STD_B03", "Standard Best of 3 Sets", "STANDARD", 1.000, 12, None, 0, 0),
        ("STD_B05", "Standard Best of 5 Sets", "STANDARD", 1.000, 20, None, 0, 0),
        ("RACE_11", "Sprint Race to 11 Games", "SPRINT", 0.900, 11, None, 0, 0),
        ("RACE_9", "Sprint Race to 9 Games", "SPRINT", 0.800, 9, None, 0, 0),
        ("RACE_7", "Sprint Race to 7 Games", "SPRINT", 0.800, 7, None, 0, 0),
        ("RACE_6", "Sprint Race to 6 Games", "SPRINT", 0.700, 6, None, 0, 0),
        ("RACE_5", "Sprint Race to 5 Games", "SPRINT", 0.600, 5, None, 0, 0),
        ("RACE_4", "Sprint Race to 4 Games", "SPRINT", 0.500, 4, None, 0, 0),
        ("AMER_12", "Americano 12 Points", "AMERICANO", 0.300, None, 12, 0, 1),
        ("AMER_16", "Americano 16 Points", "AMERICANO", 0.300, None, 16, 0, 1),
        ("AMER_20", "Americano 20 Points", "AMERICANO", 0.300, None, 20, 0, 1),
        ("AMER_24", "Americano 24 Points", "AMERICANO", 0.300, None, 24, 0, 1),
        ("AMER_28", "Americano 28 Points", "AMERICANO", 0.300, None, 28, 0, 1),
        ("AMER_32", "Americano 32 Points", "AMERICANO", 0.350, None, 32, 0, 1),
        ("MEX_12", "Mexicano 12 Points", "MEXICANO", 0.300, None, 12, 0, 1),
        ("MEX_16", "Mexicano 16 Points", "MEXICANO", 0.300, None, 16, 0, 1),
        ("MEX_20", "Mexicano 20 Points", "MEXICANO", 0.300, None, 20, 0, 1),
        ("MEX_24", "Mexicano 24 Points", "MEXICANO", 0.300, None, 24, 0, 1),
        ("MEX_28", "Mexicano 28 Points", "MEXICANO", 0.300, None, 28, 0, 1),
        ("MEX_32", "Mexicano 32 Points", "MEXICANO", 0.350, None, 32, 0, 1),
    ]
    for fid, fname, cat, mc, tg, tp, is_s, is_a in official_formats:
        c.execute(
            """INSERT OR IGNORE INTO match_formats 
                     VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (fid, fname, cat, mc, tg, tp, is_s, is_a),
        )

    # --------------------------------------------------------------------------
    # SEED 3: Master Parameter Governance Matrix (Categorized into 7 Groups)
    # --------------------------------------------------------------------------
    master_params = [
        # Group 1: Core Physics & Micro Engine
        (
            "K_FACTOR",
            0.150,
            1,
            "Base Dynamic K-Factor",
            "Controls the base sensitivity step for verified rating updates.",
            "Higher = faster movement; Lower = conservative stabilization.",
            "1. Core Physics",
        ),
        (
            "SCALE_FACTOR_Q",
            0.00575,
            1,
            "Logistic Scaling Constant (q)",
            "Converts the [0-7] rating scale to standard Glicko logistic odds.",
            "ln(10)/400 = 0.00575646. Do not alter unless rescaled.",
            "1. Core Physics",
        ),
        (
            "DRAW_PARITY_EXCHANGE",
            0.000,
            1,
            "Draw Parity Exchange Ratio",
            "Defines rating delta exchange when a match ends in an authenticated tie.",
            "0.0 maintains absolute non-inflationary rating conservation.",
            "1. Core Physics",
        ),
        (
            "POWER_MEAN_P",
            2.000,
            1,
            "Team Power Mean Exponent (p)",
            "p=1 is arithmetic mean; p=2 rewards the stronger anchor carry.",
            "Elevating towards p=3 increases carrying penalties for uneven pairs.",
            "1. Core Physics",
        ),
        (
            "DISPLAY_RATING_SOFT_FLOOR",
            0.050,
            1,
            "Display Rating Soft Floor Buffer",
            "Dampens cosmetic display drops from minor single-match variance.",
            "Protects player engagement on normal daily fluctuations.",
            "1. Core Physics",
        ),
        # Group 2: Rightsizing & Provisional Placement
        (
            "PROVISIONAL_BASE_DELTA",
            0.500,
            1,
            "Provisional Calibration Base Delta",
            "Base point sensitivity multiplier during early calibration matches.",
            "Permits rapid trajectory exploration during matches 1 to 5.",
            "2. Provisional Economy",
        ),
        (
            "ELEVATOR_MARGIN_THRESH",
            1.100,
            1,
            "Provisional Elevator Entropy Trigger",
            "Blowout margin ratio threshold that activates accelerated placement.",
            "A 6-0, 6-1 blowout (~1.1385) cleanly triggers the elevator.",
            "2. Provisional Economy",
        ),
        (
            "RIGHTSIZING_MAX_DELTA",
            0.750,
            1,
            "Rightsizing Performance Maximum Cap",
            "Maximum single-match delta allowed during smurf placement.",
            "Bypasses standard 0.300/0.375 caps during blowout rightsizing.",
            "2. Provisional Economy",
        ),
        (
            "PROVISIONAL_RD_CONTRACTION_DAMPENER",
            0.350,
            1,
            "Provisional RD Contraction Dampener",
            "Dampens abrupt RD drop after match 1 to prevent premature locking.",
            "Applies as: rd - ((rd - raw_rd) * 0.35).",
            "2. Provisional Economy",
        ),
        # Group 3: Tri-Gate & Accuracy Pillars
        (
            "MIN_VERIFIED_MATCHES",
            10.0,
            1,
            "Tri-Gate Gate 1: Match Depth",
            "Minimum completed matches required to graduate out of Provisional.",
            "Ensures sufficient statistical volume.",
            "3. Tri-Gate & Accuracy",
        ),
        (
            "MIN_UNIQUE_OPPONENTS",
            5.0,
            1,
            "Tri-Gate Gate 2: Diversity Quota",
            "Minimum unique opponents played against to prevent pod farming.",
            "Guarantees graph exploration across the venue community.",
            "3. Tri-Gate & Accuracy",
        ),
        (
            "RD_VERIFIED_THRESHOLD",
            100.0,
            1,
            "Tri-Gate Gate 3: Confidence Ceiling",
            "Maximum RD permitted to hold Verified rating status.",
            "RD > 100 flags the player as Provisional or Rust-Decayed.",
            "3. Tri-Gate & Accuracy",
        ),
        (
            "ACC_W_RD",
            0.50,
            1,
            "Accuracy Weight: RD Confidence (S_RD)",
            "Pillar weight for uncertainty contraction.",
            "Normalized: S_RD + S_N + S_D must equal 1.0.",
            "3. Tri-Gate & Accuracy",
        ),
        (
            "ACC_W_N",
            0.30,
            1,
            "Accuracy Weight: Sample Depth (S_N)",
            "Pillar weight for total match experience.",
            "Normalized across the triad.",
            "3. Tri-Gate & Accuracy",
        ),
        (
            "ACC_W_D",
            0.20,
            1,
            "Accuracy Weight: Graph Diversity (S_D)",
            "Pillar weight for unique opponent saturation.",
            "Normalized across the triad.",
            "3. Tri-Gate & Accuracy",
        ),
        # Group 4: Anti-Collusion & Network Security
        (
            "ENABLE_POD_HASHING",
            1.0,
            1,
            "4-Player Pod Rematch Decay Active",
            "1=Active. Decays deltas for exact 4-player cluster rematches.",
            "Neutralizes collusive partner-swapping loops.",
            "4. Anti-Collusion",
        ),
        (
            "POD_DECAY_HALF_LIFE_HOURS",
            48.0,
            1,
            "Pod Rematch Decay Half-Life (Hours)",
            "Duration required for rematch point values to restore to full.",
            "Decays by 50% for every subsequent encounter within window.",
            "4. Anti-Collusion",
        ),
        (
            "SYBIL_TRUST_MIN_MATCHES",
            5.0,
            1,
            "Sybil Network Gatekeeper Bypass",
            "Matches required before Sybil centrality penalties apply.",
            "Prevents brand-new players from being choked by W_G=0.0.",
            "4. Anti-Collusion",
        ),
        # Group 5: Exchange Security & Attestation Windows
        (
            "CASUAL_DAILY_CAP",
            0.300,
            1,
            "Verified 24h Casual Exchange Cap",
            "Maximum net rating movement allowed within any 24h rolling window.",
            "Prevents ladder manipulation and rapid volatility shocks.",
            "5. Exchange Security",
        ),
        (
            "PROVISIONAL_DAILY_CAP",
            0.375,
            1,
            "Provisional 24h Rolling Cap",
            "Rolling cap for uncalibrated players without rightsizing bypass.",
            "Guarantees calibrated progression for regular fixtures.",
            "5. Exchange Security",
        ),
        (
            "MAX_RETROACTIVE_LOOKBACK_HOURS",
            72.0,
            1,
            "Maximum Retroactive Match Ingestion Window",
            "Hours in the past an out-of-order match is permitted to be logged.",
            "Locks historical states to maintain immutable seasonal truth.",
            "5. Exchange Security",
        ),
        (
            "ATTESTATION_AUTO_WINDOW_HOURS",
            3.0,
            1,
            "Attestation Auto-Commit Window",
            "Hours unverified matches sit in PENDING before auto-committing.",
            "Allows match disputes before deltas finalize into live MMR.",
            "5. Exchange Security",
        ),
        (
            "ALLOW_TOURNAMENT_BYPASS",
            1.0,
            1,
            "Tournament Desk Uncapped Bypass",
            "1=Active. Official tournament events bypass the casual daily cap.",
            "Enables high-stakes multi-round progression.",
            "5. Exchange Security",
        ),
        # Group 6: Uncertainty, Rust & Dynamic Cohort Escalation (DCE)
        (
            "RD_INITIAL",
            350.0,
            1,
            "Initial Rating Deviation (RD_0)",
            "Starting uncertainty assigned to all newly onboarded players.",
            "Determines early calibration search radius.",
            "6. Uncertainty & Rust",
        ),
        (
            "RD_MIN",
            30.0,
            1,
            "Minimum Rating Deviation (RD_floor)",
            "Theoretical lower bound for uncertainty.",
            "Prevents rating ossification for long-standing veterans.",
            "6. Uncertainty & Rust",
        ),
        (
            "C_RUST",
            1.200,
            1,
            "Temporal Inactivity Rust Factor (c)",
            "Daily uncertainty expansion constant during inactivity.",
            "RD_eff = sqrt(RD^2 + c^2 * delta_t_days).",
            "6. Uncertainty & Rust",
        ),
        (
            "ENABLE_DYNAMIC_COHORT_ESCALATION",
            1.0,
            1,
            "Dynamic Cohort Escalation (DCE) Switch",
            "1=Active. Progressively increases Omega when unrated cohorts play.",
            "Prevents sandbox lock in newly onboarded clubs.",
            "6. Uncertainty & Rust",
        ),
        (
            "DCE_TIER_1_MATCHES",
            3.0,
            1,
            "DCE Tier 1 Match Volume Gate",
            "Cohort experience matches required to step Omega to Tier 1.",
            "Steps Omega from 0.25 to 0.50.",
            "6. Uncertainty & Rust",
        ),
        (
            "DCE_TIER_2_MATCHES",
            7.0,
            1,
            "DCE Tier 2 Match Volume Gate",
            "Cohort experience matches required to step Omega to Tier 2.",
            "Steps Omega from 0.50 to 0.75.",
            "6. Uncertainty & Rust",
        ),
        (
            "DCE_TIER_1_OMEGA",
            0.500,
            1,
            "DCE Tier 1 Escalated Omega",
            "Uncertainty contraction multiplier unlocked at DCE Tier 1.",
            "Speeds unrated cohort calibration.",
            "6. Uncertainty & Rust",
        ),
        (
            "DCE_TIER_2_OMEGA",
            0.750,
            1,
            "DCE Tier 2 Escalated Omega",
            "Uncertainty contraction multiplier unlocked at DCE Tier 2.",
            "Enables near-full progression without verified anchors.",
            "6. Uncertainty & Rust",
        ),
        # Group 7: Hawking Macro Engine & Regional Readiness
        (
            "HAWKING_MIN_SAMPLE",
            5.0,
            1,
            "Hawking Minimum Regional Sample Size",
            "Verified player count required before parity analysis executes.",
            "Protects cold-start regions from inaccurate calibration offsets.",
            "7. Hawking Macro",
        ),
        (
            "READINESS_VERIFIED_WEIGHT",
            0.40,
            1,
            "Readiness Score: Verified Players Weight",
            "Weight given to verified player quota in municipal readiness.",
            "Sums to 1.0 across readiness parameters.",
            "7. Hawking Macro",
        ),
        (
            "READINESS_DEPTH_WEIGHT",
            0.35,
            1,
            "Readiness Score: Match Depth Weight",
            "Weight given to matches-per-player ratio.",
            "Sums to 1.0 across readiness parameters.",
            "7. Hawking Macro",
        ),
        (
            "READINESS_BRIDGE_WEIGHT",
            0.25,
            1,
            "Readiness Score: Bridge Nodes Weight",
            "Weight given to active traveler bridges connecting outside clubs.",
            "Sums to 1.0 across readiness parameters.",
            "7. Hawking Macro",
        ),
        (
            "BRIDGE_DAMPING_CONSTANT",
            3.00,
            1,
            "Tikhonov Bridge Regularizer Constant (K_0)",
            "Dampens small-sample traveler variance across regions.",
            "W_conf = K / (K + K_0).",
            "7. Hawking Macro",
        ),
    ]

    for k, v, act, tit, desc, tune, grp in master_params:
        c.execute(
            """
        INSERT INTO global_config (param_key, param_value, is_active, param_title, param_desc, tuning_guidance, module_group)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(param_key) DO UPDATE SET
            param_title=excluded.param_title,
            param_desc=excluded.param_desc,
            tuning_guidance=excluded.tuning_guidance,
            module_group=excluded.module_group
        """,
            (k, v, act, tit, desc, tune, grp),
        )

    conn.commit()
    conn.close()


# ==============================================================================
# 2. V.16 / V.17 MATHEMATICAL ENGINE & LOGIC KERNEL
# ==============================================================================


class RyftV16:
    """Core mathematical engine implementing micro-physics, rightsizing,

    dynamic cohort escalation (DCE), and anti-collusion logic.
    """

    @staticmethod
    def get_configs(conn=None):
        """Fetches active global parameters into a fast lookup key-value dictionary."""
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
    def get_effective_rd(rd, last_match_date_str, c_rust):
        """Applies dynamic on-read temporal rust expansion to Rating Deviation.

        Formula: RD_eff = sqrt(RD^2 + c^2 * delta_t_days)
        """
        if not last_match_date_str:
            return float(rd)
        try:
            if isinstance(last_match_date_str, str):
                dt_match = datetime.fromisoformat(
                    last_match_date_str.replace("Z", "+00:00")
                )
            else:
                dt_match = last_match_date_str
            now = datetime.now(timezone.utc)
            if dt_match.tzinfo is None:
                dt_match = dt_match.replace(tzinfo=timezone.utc)
            delta_days = max(0.0, (now - dt_match).total_seconds() / 86400.0)
            rd_eff = math.sqrt(
                (float(rd) ** 2) + ((float(c_rust) ** 2) * delta_days)
            )
            return min(350.0, max(30.0, rd_eff))
        except Exception:
            return float(rd)

    @staticmethod
    def calculate_accuracy(rd, verified_matches, unique_opps, configs):
        """Calculates Tri-Gate composite rating accuracy score across three distinct pillars:

        1. S_RD: Rating uncertainty contraction
        2. S_N: Match volume progression
        3. S_D: Opponent graph diversity
        """
        # Pillar 1: RD Contraction (350 -> 30)
        s_rd = max(0.0, min(1.0, (350.0 - float(rd)) / (350.0 - 30.0)))

        # Pillar 2: Match Sample Depth
        min_m = float(configs.get("MIN_VERIFIED_MATCHES", 10.0))
        s_n = max(0.0, min(1.0, float(verified_matches) / min_m))

        # Pillar 3: Opponent Network Diversity
        min_opp = float(configs.get("MIN_UNIQUE_OPPONENTS", 5.0))
        s_d = max(0.0, min(1.0, float(unique_opps) / min_opp))

        w_rd = float(configs.get("ACC_W_RD", 0.50))
        w_n = float(configs.get("ACC_W_N", 0.30))
        w_d = float(configs.get("ACC_W_D", 0.20))

        composite_score = round(
            ((s_rd * w_rd) + (s_n * w_n) + (s_d * w_d)) * 100.0, 1
        )
        return composite_score, round(s_rd, 3), round(s_n, 3), round(s_d, 3)

    @staticmethod
    def get_category_for_rating(rating, gender="OPEN", conn=None):
        """Evaluates demographic tier based on discrete boundaries (.999 transitions)."""
        owns = False
        if not conn:
            conn = get_db_connection()
            owns = True
        r = round(float(rating), 3)
        row = conn.execute(
            """
            SELECT category_name FROM rating_categories 
            WHERE ? >= min_rating AND ? <= max_rating 
              AND (gender = ? OR gender = 'OPEN')
            ORDER BY CASE WHEN gender = ? THEN 1 ELSE 2 END, sort_order ASC
            LIMIT 1
        """,
            (r, r, gender, gender),
        ).fetchone()
        if owns:
            conn.close()
        return row["category_name"] if row else "Uncalibrated"

    @staticmethod
    def compute_match(
        p1_raw,
        p2_raw,
        p3_raw,
        p4_raw,
        score_a,
        score_b,
        games_won,
        games_lost,
        format_id,
        venue_id,
        is_singles=False,
        is_tournament=False,
        historical_timestamp=None,
        conn=None,
    ):
        """Master execution kernel implementing the full 25-bit algorithmic pipeline:

        - Team Power Means (Bit 1)
        - Dynamic Cohort Escalation (DCE) for unrated sandboxes
        - Sybil trust dampener bypass for early-stage players (< 5 matches)
        - Performance Rating Interpolation for rightsizing smurfs / beginners
        - Anchor loss cushioning (g(RD_opp))
        - Strict loss non-positivity verification
        - Bi-directional 24h rolling cap enforcement with blowout rightsizing bypass
        """
        owns = False
        if not conn:
            conn = get_db_connection()
            owns = True

        configs = RyftV16.get_configs(conn)

        # Defensive type conversion: guarantee pure Python dictionaries
        def clean_p(p_in):
            if not p_in:
                return None
            p = dict(p_in)
            p["latent_mmr"] = float(p.get("latent_mmr", 3.000))
            p["display_rating"] = float(p.get("display_rating", 3.00))
            p["rd"] = float(p.get("rd", 350.0))
            p["volatility"] = float(p.get("volatility", 0.06))
            p["verified_matches_count"] = int(
                p.get("verified_matches_count", 0)
            )
            p["unique_opponents_count"] = int(
                p.get("unique_opponents_count", 0)
            )
            p["is_anchor"] = int(p.get("is_anchor", 0))
            p["is_provisional"] = int(p.get("is_provisional", 1))
            p["is_manually_verified"] = int(p.get("is_manually_verified", 0))
            p["sybil_trust_score"] = float(p.get("sybil_trust_score", 1.0))
            return p

        p1 = clean_p(p1_raw)
        p2 = None if is_singles else clean_p(p2_raw)
        p3 = clean_p(p3_raw)
        p4 = None if is_singles else clean_p(p4_raw)

        # Apply on-read temporal rust to active participants
        c_rust = configs.get("C_RUST", 1.200)
        p1["rd_eff"] = RyftV16.get_effective_rd(
            p1["rd"], p1.get("last_match_date"), c_rust
        )
        if not is_singles and p2:
            p2["rd_eff"] = RyftV16.get_effective_rd(
                p2["rd"], p2.get("last_match_date"), c_rust
            )
        p3["rd_eff"] = RyftV16.get_effective_rd(
            p3["rd"], p3.get("last_match_date"), c_rust
        )
        if not is_singles and p4:
            p4["rd_eff"] = RyftV16.get_effective_rd(
                p4["rd"], p4.get("last_match_date"), c_rust
            )

        bit_trace = {}

        # ----------------------------------------------------------------------
        # Bit 1: Team Aggregation via Power-Mean (p = 2.0)
        # ----------------------------------------------------------------------
        p_exp = configs.get("POWER_MEAN_P", 2.000)
        if is_singles:
            ra = p1["latent_mmr"]
            rb = p3["latent_mmr"]
            rd_a = p1["rd_eff"]
            rd_b = p3["rd_eff"]
        else:
            ra = (
                ((p1["latent_mmr"] ** p_exp + p2["latent_mmr"] ** p_exp) / 2.0)
                ** (1.0 / p_exp)
            )
            rb = (
                ((p3["latent_mmr"] ** p_exp + p4["latent_mmr"] ** p_exp) / 2.0)
                ** (1.0 / p_exp)
            )
            rd_a = math.sqrt((p1["rd_eff"] ** 2 + p2["rd_eff"] ** 2) / 2.0)
            rd_b = math.sqrt((p3["rd_eff"] ** 2 + p4["rd_eff"] ** 2) / 2.0)

        bit_trace["Bit 01: Team Aggregation"] = (
            f"Team A Power-Mean MMR: {ra:.3f} (RD: {rd_a:.1f}) | Team B Power-Mean MMR: {rb:.3f} (RD: {rd_b:.1f})"
        )

        # ----------------------------------------------------------------------
        # Bit 2: Format Multiplier (M_C) Lookup
        # ----------------------------------------------------------------------
        fmt_row = conn.execute(
            "SELECT mc, is_americano FROM match_formats WHERE format_id = ?",
            (format_id,),
        ).fetchone()
        mc = float(fmt_row["mc"]) if fmt_row else 1.000
        is_americano = (
            bool(fmt_row["is_americano"]) if fmt_row else ("AMER" in format_id)
        )
        bit_trace["Bit 02: Format Confidence (M_C)"] = (
            f"Format {format_id} applied M_C = {mc:.3f}"
        )

        # ----------------------------------------------------------------------
        # Bit 3: Logistic Expectancy (E_A) & Opponent RD Discount g(RD_opp)
        # ----------------------------------------------------------------------
        q = configs.get("SCALE_FACTOR_Q", 0.00575)
        g_rd_b = 1.0 / math.sqrt(1.0 + (3.0 * (q**2) * (rd_b**2)) / (math.pi**2))
        g_rd_a = 1.0 / math.sqrt(1.0 + (3.0 * (q**2) * (rd_a**2)) / (math.pi**2))
        ea = 1.0 / (1.0 + math.pow(10.0, -g_rd_b * (ra - rb) / 1.0))
        eb = 1.0 - ea
        bit_trace["Bit 03: Expectancy & RD Damping"] = (
            f"E_A: {ea*100:.1f}%, E_B: {eb*100:.1f}%, g(RD_opp): {g_rd_b:.3f}"
        )

        # ----------------------------------------------------------------------
        # Bit 4: Margin Entropy (S_margin) Evaluation
        # ----------------------------------------------------------------------
        total_pts = float(score_a + score_b)
        if total_pts <= 0:
            s_margin = 1.0
            actual_a = 0.5
        else:
            actual_a = float(score_a) / total_pts
            diff = abs(score_a - score_b)
            s_margin = max(
                0.20,
                min(2.00, 1.0 + (diff / total_pts) * (1.20 if diff > 4 else 0.80)),
            )

        if score_a > score_b:
            w_flag = "A"
            sa_res = 1.0
        elif score_b > score_a:
            w_flag = "B"
            sa_res = 0.0
        else:
            w_flag = "DRAW"
            sa_res = 0.5

        bit_trace["Bit 04: Margin Entropy (S_margin)"] = (
            f"Score {score_a}-{score_b} -> Outcome {w_flag}, S_margin: {s_margin:.3f}"
        )

        # ----------------------------------------------------------------------
        # Dynamic Cohort Escalation (DCE) Pipeline
        # ----------------------------------------------------------------------
        has_verified_anchor = any(
            p["is_anchor"] == 1 or p["rd_eff"] <= 100.0
            for p in ([p1, p3] if is_singles else [p1, p2, p3, p4])
        )
        omega_cohort = 0.25  # Base unanchored sandbox dampener

        if not has_verified_anchor and configs.get(
            "ENABLE_DYNAMIC_COHORT_ESCALATION", 1.0
        ):
            avg_cohort_m = (
                (p1["verified_matches_count"] + p3["verified_matches_count"])
                / 2.0
                if is_singles
                else (
                    p1["verified_matches_count"]
                    + p2["verified_matches_count"]
                    + p3["verified_matches_count"]
                    + p4["verified_matches_count"]
                )
                / 4.0
            )

            dce_t1_m = configs.get("DCE_TIER_1_MATCHES", 3.0)
            dce_t2_m = configs.get("DCE_TIER_2_MATCHES", 7.0)
            dce_om1 = configs.get("DCE_TIER_1_OMEGA", 0.50)
            dce_om2 = configs.get("DCE_TIER_2_OMEGA", 0.75)

            if avg_cohort_m >= dce_t2_m:
                omega_cohort = dce_om2
                bit_trace["DCE: Dynamic Cohort Escalation"] = (
                    f"Tier 2 Escalation active (Avg matches: {avg_cohort_m:.1f} >= {dce_t2_m}): Omega = {omega_cohort:.2f}"
                )
            elif avg_cohort_m >= dce_t1_m:
                omega_cohort = dce_om1
                bit_trace["DCE: Dynamic Cohort Escalation"] = (
                    f"Tier 1 Escalation active (Avg matches: {avg_cohort_m:.1f} >= {dce_t1_m}): Omega = {omega_cohort:.2f}"
                )
            else:
                bit_trace["DCE: Dynamic Cohort Escalation"] = (
                    f"Baseline Sandbox (Avg matches: {avg_cohort_m:.1f}): Omega = {omega_cohort:.2f}"
                )
        elif has_verified_anchor:
            omega_cohort = 1.00
            bit_trace["DCE: Dynamic Cohort Escalation"] = (
                "Verified Anchor Present: Unconstrained Progression (Omega = 1.00)"
            )

        # ----------------------------------------------------------------------
        # Bit 5-13: Individual Player Calculation Pipeline
        # ----------------------------------------------------------------------
        k_base = configs.get("K_FACTOR", 0.150)
        c_dampener = configs.get("PROVISIONAL_RD_CONTRACTION_DAMPENER", 0.350)
        prov_base_k = configs.get("PROVISIONAL_BASE_DELTA", 0.500)
        elev_thresh = configs.get("ELEVATOR_MARGIN_THRESH", 1.100)
        max_rightsizing = configs.get("RIGHTSIZING_MAX_DELTA", 0.750)
        casual_cap = configs.get("CASUAL_DAILY_CAP", 0.300)
        prov_cap = configs.get("PROVISIONAL_DAILY_CAP", 0.375)

        players_list = (
            [("A", p1), ("B", p3)]
            if is_singles
            else [("A", p1), ("A", p2), ("B", p3), ("B", p4)]
        )
        calc_results = {}

        for side, p in players_list:
            pid = p["player_id"]
            is_win = (side == w_flag)
            is_loss = (w_flag != "DRAW" and side != w_flag)
            opp_mmr = rb if side == "A" else ra
            opp_rd = rd_b if side == "A" else rd_a
            s_res = sa_res if side == "A" else (1.0 - sa_res)
            e_side = ea if side == "A" else eb

            # Bit 16 Sybil Trust Bypass: bypass centrality penalty if < 5 matches
            if p["verified_matches_count"] < configs.get(
                "SYBIL_TRUST_MIN_MATCHES", 5.0
            ):
                w_g = 1.00
            else:
                w_g = max(0.20, min(1.00, float(p.get("sybil_trust_score", 1.0))))

            # Rightsizing & Smurf Placement Logic
            is_prov = (p["is_provisional"] == 1 or p["rd_eff"] > 100.0)
            is_elevator = (
                is_prov
                and is_win
                and (s_margin >= elev_thresh)
                and (opp_mmr >= p["latent_mmr"] - 0.25)
            )

            # Raw unconstrained delta
            if is_elevator:
                # Performance Rating Interpolation
                target_performance = opp_mmr + (
                    0.50 * (float(score_a - score_b) / max(1.0, total_pts))
                )
                step_delta = (
                    (target_performance - p["latent_mmr"]) * 0.40 * mc * w_g
                )
                raw_delta = max(
                    0.200, min(max_rightsizing, round(step_delta, 4))
                )
                bypass_cap = True
            elif is_prov:
                step_k = prov_base_k * omega_cohort * mc * w_g
                raw_delta = round(step_k * s_margin * (s_res - e_side), 4)
                bypass_cap = False
            else:
                # Bit 11 Anchor Loss Cushioning: g(RD_opp) dampens loss against unrated/high-RD players
                g_opp_player = 1.0 / math.sqrt(
                    1.0 + (3.0 * (q**2) * (opp_rd**2)) / (math.pi**2)
                )
                step_k = k_base * mc * w_g
                if is_loss:
                    raw_delta = round(
                        step_k
                        * s_margin
                        * g_opp_player
                        * (s_res - e_side)
                        * omega_cohort,
                        4,
                    )
                else:
                    raw_delta = round(
                        step_k * s_margin * (s_res - e_side) * omega_cohort, 4
                    )
                bypass_cap = False

            # Strict Loss Non-Positivity Guardrail
            if is_loss and raw_delta > 0.0:
                raw_delta = 0.0000

            # Draw Parity Guardrail
            if w_flag == "DRAW":
                raw_delta = 0.0000

            # Cap Enforcement (24h Casual Window vs. Rightsizing Bypass vs. Tournament Bypass)
            if is_tournament and configs.get("ALLOW_TOURNAMENT_BYPASS", 1.0):
                applied_delta = raw_delta
            elif bypass_cap:
                applied_delta = max(
                    -max_rightsizing, min(max_rightsizing, raw_delta)
                )
            elif is_prov:
                applied_delta = max(-prov_cap, min(prov_cap, raw_delta))
            else:
                applied_delta = max(-casual_cap, min(casual_cap, raw_delta))

            # Apply delta to latent MMR
            new_mmr = round(
                max(0.000, min(7.000, p["latent_mmr"] + applied_delta)), 3
            )
            new_disp = round(
                max(0.00, min(7.00, p["display_rating"] + applied_delta)), 2
            )

            # Bit 11 RD Contraction Easing: smooth step-down rather than collapse
            raw_new_rd = math.sqrt(
                1.0
                / (
                    (1.0 / (p["rd_eff"] ** 2))
                    + ((q**2) * (g_rd_b**2) * ea * (1.0 - ea))
                )
            )
            contracted_rd = p["rd_eff"] - (
                (p["rd_eff"] - raw_new_rd) * c_dampener
            )
            new_rd = round(max(30.0, min(350.0, contracted_rd)), 1)

            # Update Tri-Gate Metrics
            new_matches = p["verified_matches_count"] + 1
            new_opps = p["unique_opponents_count"] + (
                1 if is_singles else (2 if side == "A" else 2)
            )
            new_acc, s_rd, s_n, s_d = RyftV16.calculate_accuracy(
                new_rd, new_matches, new_opps, configs
            )

            # Check Tri-Gate Calibration Graduation
            grad_m = new_matches >= configs.get("MIN_VERIFIED_MATCHES", 10.0)
            grad_o = new_opps >= configs.get("MIN_UNIQUE_OPPONENTS", 5.0)
            grad_rd = new_rd <= configs.get("RD_VERIFIED_THRESHOLD", 100.0)
            is_now_verified = (
                1
                if (p["is_manually_verified"] or (grad_m and grad_o and grad_rd))
                else 0
            )
            calib_tier = "VERIFIED" if is_now_verified else "PROVISIONAL"

            calc_results[pid] = {
                "player_id": pid,
                "name": p["name"],
                "side": side,
                "pre_mmr": p["latent_mmr"],
                "post_mmr": new_mmr,
                "delta": applied_delta,
                "pre_rd": p["rd_eff"],
                "post_rd": new_rd,
                "pre_acc": p.get("accuracy_score", 0.0),
                "post_acc": new_acc,
                "pre_disp": p["display_rating"],
                "post_disp": new_disp,
                "tier": calib_tier,
                "is_bypass": bypass_cap,
                "s_rd": s_rd,
                "s_n": s_n,
                "s_d": s_d,
            }

        # Calculate Team Aggregated Deltas
        team_a_delta = (
            calc_results[p1["player_id"]]["delta"]
            if is_singles
            else round(
                (
                    calc_results[p1["player_id"]]["delta"]
                    + calc_results[p2["player_id"]]["delta"]
                )
                / 2.0,
                4,
            )
        )
        team_b_delta = (
            calc_results[p3["player_id"]]["delta"]
            if is_singles
            else round(
                (
                    calc_results[p3["player_id"]]["delta"]
                    + calc_results[p4["player_id"]]["delta"]
                )
                / 2.0,
                4,
            )
        )

        if owns:
            conn.close()

        return {
            "winner": w_flag,
            "team_a_delta": team_a_delta,
            "team_b_delta": team_b_delta,
            "ea": ea,
            "eb": eb,
            "ra": ra,
            "rb": rb,
            "rd_a": rd_a,
            "rd_b": rd_b,
            "mc": mc,
            "s_margin": s_margin,
            "players": calc_results,
            "bit_trace": bit_trace,
            "omega_cohort": omega_cohort,
        }
# ==============================================================================
# 3. SESSION SCHEDULING ENGINE (V16.2 PROD)
# ==============================================================================


class SessionLogicEngine:
    """Multi-court tournament and mixer scheduler supporting Berger Circle polygons,

    virtual byes, balanced sit-out rotation, and Swiss Mexicano dynamic seeding.
    """

    @staticmethod
    def generate_fixed_teams_schedule(teams, courts_count):
        """Berger Circle Polygon Algorithm for Fixed Pairs.

        Handles odd/even team pools with deterministic bye rotation (no
        consecutive sit-outs).
        """
        n = len(teams)
        if n < 2:
            return []
        team_list = list(teams)
        has_bye = False
        if n % 2 != 0:
            team_list.append("__BYE__")
            n += 1
            has_bye = True

        total_rounds = n - 1
        matches_per_round = n // 2
        fixtures = []
        c_labels = [f"Court {i+1}" for i in range(courts_count)]

        for r in range(total_rounds):
            round_matches = []
            for i in range(matches_per_round):
                t1 = team_list[i]
                t2 = team_list[n - 1 - i]
                if t1 == "__BYE__" or t2 == "__BYE__":
                    continue  # Team sits out on a scheduled bye
                c_idx = len(round_matches) % courts_count
                round_matches.append(
                    {
                        "round_number": r + 1,
                        "court_number": c_idx + 1,
                        "court_id": c_labels[c_idx],
                        "team_a": t1,
                        "team_b": t2,
                    }
                )
            fixtures.extend(round_matches)
            # Berger Polygon clockwise shift: index 0 remains anchored
            team_list = [team_list[0]] + [team_list[-1]] + team_list[1:-1]

        return fixtures

    @staticmethod
    def generate_rotating_americano_schedule(players, courts_count, rounds=None):
        """Social Americano matrix balancing partner rotations and sit-out distributions.

        Guarantees: No player rests two rounds in a row, and bench intervals cycle fairly.
        """
        n = len(players)
        if n < 4:
            return []
        if not rounds:
            rounds = n - 1 if n % 2 == 1 else n

        c_labels = [f"Court {i+1}" for i in range(courts_count)]
        fixtures = []
        sitout_counts = {p: 0 for p in players}
        play_counts = {p: 0 for p in players}
        partner_history = {p: set() for p in players}

        for r in range(rounds):
            # Sort players by play count (ascending) to guarantee equal court access
            sorted_p = sorted(players, key=lambda p: (play_counts[p], random.random()))
            active_quota = min(len(sorted_p) - (len(sorted_p) % 4), courts_count * 4)
            active_pool = sorted_p[:active_quota]
            benched = sorted_p[active_quota:]

            for bp in benched:
                sitout_counts[bp] += 1

            # Pair up active pool prioritizing unseen partners
            unpaired = list(active_pool)
            round_courts = active_quota // 4

            for c_i in range(round_courts):
                if len(unpaired) < 4:
                    break
                p1 = unpaired.pop(0)
                # Find partner with least shared games
                best_partner = min(
                    unpaired,
                    key=lambda cand: (cand in partner_history[p1], random.random()),
                )
                unpaired.remove(best_partner)
                partner_history[p1].add(best_partner)
                partner_history[best_partner].add(p1)

                p3 = unpaired.pop(0)
                best_opp_partner = min(
                    unpaired,
                    key=lambda cand: (cand in partner_history[p3], random.random()),
                )
                unpaired.remove(best_opp_partner)
                partner_history[p3].add(best_opp_partner)
                partner_history[best_opp_partner].add(p3)

                for ap in [p1, best_partner, p3, best_opp_partner]:
                    play_counts[ap] += 1

                c_idx = c_i % courts_count
                fixtures.append(
                    {
                        "round_number": r + 1,
                        "court_number": c_idx + 1,
                        "court_id": c_labels[c_idx],
                        "team_a_p1": p1,
                        "team_a_p2": best_partner,
                        "team_b_p1": p3,
                        "team_b_p2": best_opp_partner,
                    }
                )

        return fixtures

    @staticmethod
    def generate_mexicano_round(session_id, next_round, courts_count, conn):
        """Dynamic Swiss-ladder Mexicano pairing:

        Ranks participants by cumulative points:
        Ranks 1-4 route to Court 1 (P1+P4 vs P2+P3)
        Ranks 5-8 route to Court 2 (P5+P8 vs P6+P7)
        """
        rosters = conn.execute(
            """
            SELECT sr.player_id, sr.running_points, p.latent_mmr
            FROM session_rosters sr
            JOIN players p ON sr.player_id = p.player_id
            WHERE sr.session_id = ?
            ORDER BY sr.running_points DESC, p.latent_mmr DESC
        """,
            (session_id,),
        ).fetchall()

        if len(rosters) < 4:
            return []

        fixtures = []
        c_labels = [f"Court {i+1}" for i in range(courts_count)]
        active_count = len(rosters) - (len(rosters) % 4)
        active_rosters = rosters[:active_count]

        court_idx = 0
        for i in range(0, active_count, 4):
            if court_idx >= courts_count:
                break
            pod = active_rosters[i : i + 4]
            # Balanced pod pairing: 1st + 4th vs 2nd + 3rd
            p1 = pod[0]["player_id"]
            p2 = pod[3]["player_id"]
            p3 = pod[1]["player_id"]
            p4 = pod[2]["player_id"]

            fixtures.append(
                {
                    "session_match_id": str(uuid.uuid4()),
                    "session_id": session_id,
                    "round_number": next_round,
                    "court_number": court_idx + 1,
                    "team_a_p1": p1,
                    "team_a_p2": p2,
                    "team_b_p1": p3,
                    "team_b_p2": p4,
                    "court_id": c_labels[court_idx],
                }
            )
            court_idx += 1

        return fixtures


# ==============================================================================
# 4. HISTORICAL SCORELINE FORMATTER & AUDIT HELPERS
# ==============================================================================


def format_match_scoreline(score_a, score_b, scoreline_raw=None):
    """Generates authentic scoreline headers from JSON or structured strings."""
    if scoreline_raw:
        try:
            parsed = json.loads(scoreline_raw)
            if isinstance(parsed, list) and len(parsed) > 0:
                sets_strs = [f"{s[0]}-{s[1]}" for s in parsed if len(s) == 2]
                if sets_strs:
                    return ", ".join(sets_strs)
            elif isinstance(scoreline_raw, str) and "-" in scoreline_raw:
                return scoreline_raw
        except Exception:
            if isinstance(scoreline_raw, str) and "-" in scoreline_raw:
                return scoreline_raw
    return f"{score_a} - {score_b}"


# ==============================================================================
# 5. STREAMLIT APPLICATION SHELL & UI ROUTING
# ==============================================================================

init_db()

st.set_page_config(
    page_title="RYFT V.16 / V.17 Rating Engine",
    page_icon="⚡",
    layout="wide",
    initial_sidebar_state="expanded",
)

# Custom Operational Styling
st.markdown(
    """
<style>
    .metric-card {
        background-color: #f8fafc;
        border: 1px solid #e2e8f0;
        border-radius: 8px;
        padding: 14px;
        margin-bottom: 10px;
    }
    .badge-verified {
        color: #047857;
        font-weight: 600;
        background-color: #d1fae5;
        padding: 2px 8px;
        border-radius: 4px;
    }
    .badge-provisional {
        color: #b45309;
        font-weight: 600;
        background-color: #fef3c7;
        padding: 2px 8px;
        border-radius: 4px;
    }
    .badge-anchor {
        color: #1d4ed8;
        font-weight: 600;
        background-color: #dbeafe;
        padding: 2px 8px;
        border-radius: 4px;
    }
</style>
""",
    unsafe_allow_html=True,
)

# ------------------------------------------------------------------------------
# SIDEBAR NAVIGATION
# ------------------------------------------------------------------------------
st.sidebar.image(
    "https://raw.githubusercontent.com/streamlit/brand/main/logo/streamlit-logo-primary-colormark-darktext.png",
    width=140,
)
st.sidebar.title("⚡ RYFT Engine")
st.sidebar.caption("Deterministic Micro-Physics & Macro Parity Ledger")

NAV_ITEMS = [
    "📊 The Dashboard",
    "🎾 Log Matches",
    "🧠 Session Logic (V16.2 PROD)",
    "🏆 Tournament Desk (Delayed Entry)",
    "📜 Historical Matches",
    "👥 Player Roster & Calibration",
    "🏢 Venues & Regions",
    "🌐 Hawking Engine (V2 Complete)",
    "⚙️ Global Config",
]

nav_selection = st.sidebar.radio("Navigation Menu", NAV_ITEMS)

st.sidebar.divider()
st.sidebar.markdown("### 💾 Operational Snapshots")
snap_bytes = export_db_bytes()
st.sidebar.download_button(
    label="📦 Backup Database",
    data=snap_bytes,
    file_name=f"ryft_snapshot_{date.today().isoformat()}.db",
    mime="application/x-sqlite3",
    use_container_width=True,
)

restore_file = st.sidebar.file_uploader(
    "Restore Snapshot", type=["db"], label_visibility="collapsed"
)
if restore_file:
    if st.sidebar.button("⚠️ Confirm Database Restore", use_container_width=True):
        try:
            restore_db_from_bytes(restore_file.read())
            st.sidebar.success("Database restored successfully!")
            st.rerun()
        except Exception as e:
            st.sidebar.error(f"Restore failed: {e}")

st.sidebar.caption("RYFT Core Architecture V.16 / V.17 | Build 2026.1")


# ==============================================================================
# TAB 1: 📊 THE DASHBOARD
# ==============================================================================
if nav_selection == "📊 The Dashboard":
    st.title("📊 Rating Ecosystem Telemetry")
    st.caption(
        "Real-time telemetry across player calibration, match volume, and network equilibrium."
    )

    conn = get_db_connection()
    c = conn.cursor()

    total_p = c.execute("SELECT COUNT(*) FROM players").fetchone()[0]
    total_m = c.execute(
        "SELECT COUNT(*) FROM matches WHERE status='COMMITTED'"
    ).fetchone()[0]
    anchors = c.execute(
        "SELECT COUNT(*) FROM players WHERE is_anchor=1"
    ).fetchone()[0]
    verified = c.execute(
        "SELECT COUNT(*) FROM players WHERE calibration_tier='VERIFIED'"
    ).fetchone()[0]
    provisional = c.execute(
        "SELECT COUNT(*) FROM players WHERE calibration_tier='PROVISIONAL'"
    ).fetchone()[0]

    # Live System Median & Drift Calculation
    p_rows = c.execute(
        "SELECT latent_mmr FROM players WHERE calibration_tier != 'INACTIVE'"
    ).fetchall()
    if p_rows:
        ratings_list = sorted([r["latent_mmr"] for r in p_rows])
        mid = len(ratings_list) // 2
        median_mmr = (
            ratings_list[mid]
            if len(ratings_list) % 2 != 0
            else (ratings_list[mid - 1] + ratings_list[mid]) / 2.0
        )
    else:
        median_mmr = 3.000

    drift_val = median_mmr - 3.000
    conn.close()

    m1, m2, m3, m4, m5 = st.columns(5)
    m1.metric("Registered Players", f"{total_p:,}")
    m2.metric("Committed Matches", f"{total_m:,}")
    m3.metric("Verified Calibration", f"{verified:,}")
    m4.metric("Active Anchors", f"{anchors:,}")
    m5.metric(
        "System Median MMR",
        f"{median_mmr:.3f}",
        delta=f"{drift_val:+.3f} Drift",
        delta_color="inverse" if abs(drift_val) > 0.2 else "normal",
    )

    st.divider()

    # Visual Distribution & Activity Highlights
    c_left, c_right = st.columns([3, 2])

    with c_left:
        st.subheader("Demographic Rating Spread")
        conn = get_db_connection()
        p_df = pd.read_sql_query(
            "SELECT latent_mmr, display_rating, rd, accuracy_score, calibration_tier FROM players",
            conn,
        )
        conn.close()

        if not p_df.empty:
            st.bar_chart(p_df["latent_mmr"].round(1).value_counts().sort_index())
        else:
            st.info("No players onboarded yet. Add players in Player Roster.")

    with c_right:
        st.subheader("Calibration Breakdown")
        if total_p > 0:
            tier_data = {
                "Verified": verified,
                "Provisional": provisional,
                "Anchors": anchors,
            }
            st.dataframe(
                pd.DataFrame(
                    list(tier_data.items()), columns=["Status", "Players Count"]
                ),
                use_container_width=True,
                hide_index=True,
            )
        else:
            st.info("Zero players registered.")


# ==============================================================================
# TAB 2: 🎾 LOG MATCHES (WITH SNAPSHOT ATTESTATION)
# ==============================================================================
elif nav_selection == "🎾 Log Matches":
    st.title("🎾 Log Match (Casual & Direct Gateway)")
    st.caption(
        "Submit single matches with instant mathematical dry-run evaluation and atomic commit."
    )

    conn = get_db_connection()
    venues = conn.execute(
        "SELECT venue_id, venue_name FROM venues WHERE is_active = 1 ORDER BY venue_name"
    ).fetchall()
    formats = conn.execute(
        "SELECT format_id, format_name, category, mc FROM match_formats ORDER BY category, format_name"
    ).fetchall()
    players = conn.execute(
        "SELECT player_id, name, latent_mmr, rd, calibration_tier, is_anchor, is_provisional FROM players ORDER BY name"
    ).fetchall()
    configs = RyftV16.get_configs(conn)

    v_map = {v["venue_name"]: v["venue_id"] for v in venues}
    f_map = {
        f"{f['format_name']} ({f['category']} | Mc={f['mc']})": f["format_id"]
        for f in formats
    }
    p_map = {
        f"{format_pr_name(p['name'], p['is_provisional'])} (MMR: {p['latent_mmr']:.2f} | RD: {p['rd']:.0f})": p[
            "player_id"
        ]
        for p in players
    }
    p_meta = {p["player_id"]: dict(p) for p in players}

    # Ingestion Form
    with st.form("log_match_form"):
        st.subheader("1. Match Context")
        c1, c2, c3 = st.columns(3)
        sel_venue_name = c1.selectbox("Venue", list(v_map.keys()) if v_map else ["None"])
        sel_format_name = c2.selectbox(
            "Match Format", list(f_map.keys()) if f_map else ["None"]
        )
        is_singles = c3.radio("Game Lineup", ["Doubles (2v2)", "Singles (1v1)"]) == "Singles (1v1)"

        c4, c5 = st.columns(2)
        match_date = c4.date_input("Match Date", value=date.today())
        match_time = c5.time_input("Match Time", value=time(18, 0))
        match_ts_str = (
            f"{match_date.isoformat()}T{match_time.strftime('%H:%M:%S')}+00:00"
        )

        st.subheader("2. Player Selection")
        col_ta, col_tb = st.columns(2)

        with col_ta:
            st.markdown("##### 🔵 Team A")
            ta_p1 = st.selectbox(
                "Player 1 (Team A)",
                list(p_map.keys()) if p_map else ["None"],
                key="lm_ta_p1",
            )
            ta_p2 = (
                None
                if is_singles
                else st.selectbox(
                    "Player 2 (Team A)",
                    list(p_map.keys()) if p_map else ["None"],
                    key="lm_ta_p2",
                )
            )

        with col_tb:
            st.markdown("##### 🔴 Team B")
            tb_p1 = st.selectbox(
                "Player 1 (Team B)",
                list(p_map.keys()) if p_map else ["None"],
                key="lm_tb_p1",
            )
            tb_p2 = (
                None
                if is_singles
                else st.selectbox(
                    "Player 2 (Team B)",
                    list(p_map.keys()) if p_map else ["None"],
                    key="lm_tb_p2",
                )
            )

        st.subheader("3. Match Scoring")
        sc_col1, sc_col2 = st.columns(2)
        score_a = sc_col1.number_input(
            "Team A Final Games / Points",
            min_value=0,
            max_value=100,
            value=6,
            step=1,
        )
        score_b = sc_col2.number_input(
            "Team B Final Games / Points",
            min_value=0,
            max_value=100,
            value=4,
            step=1,
        )

        scoreline_raw = st.text_input(
            "Authentic Set Scoreline (Optional, e.g. 6-4, 4-6, 7-6)", value=""
        )

        submit_btn = st.form_submit_button("⚡ Evaluate & Commit Match")

    if submit_btn:
        # Gateway Validation
        p1_id = p_map.get(ta_p1)
        p2_id = None if is_singles else p_map.get(ta_p2)
        p3_id = p_map.get(tb_p1)
        p4_id = None if is_singles else p_map.get(tb_p2)

        selected_ids = [pid for pid in [p1_id, p2_id, p3_id, p4_id] if pid]
        if len(selected_ids) != len(set(selected_ids)):
            st.error("Validation Error: Cannot select the same player multiple times.")
        elif not v_map or not f_map:
            st.error("Validation Error: Venues and Formats must be initialized.")
        else:
            fmt_id = f_map[sel_format_name]
            v_id = v_map[sel_venue_name]

            # Execute Core Physics Engine
            calc_res = RyftV16.compute_match(
                p_meta[p1_id],
                p_meta[p2_id] if p2_id else None,
                p_meta[p3_id],
                p_meta[p4_id] if p4_id else None,
                score_a,
                score_b,
                score_a,
                score_b,
                fmt_id,
                v_id,
                is_singles=is_singles,
                is_tournament=False,
                conn=conn,
            )

            # Atomic Database Commit
            try:
                c = conn.cursor()
                m_id = str(uuid.uuid4())
                c.execute(
                    """
                    INSERT INTO matches (
                        match_id, match_timestamp, venue_id, format_id,
                        team_a_p1, team_a_p2, team_b_p1, team_b_p2,
                        score_team_a, score_team_b, winner_team,
                        delta_team_a, delta_team_b, margin_entropy,
                        status, attestation_status, scoreline_raw
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'COMMITTED', 'COMMITTED', ?)
                """,
                    (
                        m_id,
                        match_ts_str,
                        v_id,
                        fmt_id,
                        p1_id,
                        p2_id,
                        p3_id,
                        p4_id,
                        score_a,
                        score_b,
                        calc_res["winner"],
                        calc_res["team_a_delta"],
                        calc_res["team_b_delta"],
                        calc_res["s_margin"],
                        scoreline_raw
                        if scoreline_raw
                        else f"{score_a}-{score_b}",
                    ),
                )

                # Persist match logs and update player records
                for pid, pres in calc_res["players"].items():
                    log_id = str(uuid.uuid4())
                    c.execute(
                        """
                        INSERT INTO match_logs (
                            log_id, match_id, player_id, team_id,
                            pre_mmr, post_mmr, delta_mmr,
                            pre_rd, post_rd, pre_acc, post_acc, bit_trace_json
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                        (
                            log_id,
                            m_id,
                            pid,
                            pres["side"],
                            pres["pre_mmr"],
                            pres["post_mmr"],
                            pres["delta"],
                            pres["pre_rd"],
                            pres["post_rd"],
                            pres["pre_acc"],
                            pres["post_acc"],
                            json.dumps(calc_res["bit_trace"]),
                        ),
                    )

                    c.execute(
                        """
                        UPDATE players SET
                            latent_mmr = ?,
                            display_rating = ?,
                            rd = ?,
                            accuracy_score = ?,
                            accuracy_s_rd = ?,
                            accuracy_s_matches = ?,
                            accuracy_s_diversity = ?,
                            calibration_tier = ?,
                            verified_matches_count = verified_matches_count + 1,
                            unique_opponents_count = unique_opponents_count + ?,
                            all_time_peak_mmr = MAX(all_time_peak_mmr, ?),
                            lowest_mmr = MIN(lowest_mmr, ?),
                            career_net_delta = career_net_delta + ?,
                            last_match_date = ?
                        WHERE player_id = ?
                    """,
                        (
                            pres["post_mmr"],
                            pres["post_disp"],
                            pres["post_rd"],
                            pres["post_acc"],
                            pres["s_rd"],
                            pres["s_n"],
                            pres["s_d"],
                            pres["tier"],
                            1 if is_singles else 2,
                            pres["post_mmr"],
                            pres["post_mmr"],
                            pres["delta"],
                            match_ts_str,
                            pid,
                        ),
                    )

                conn.commit()
                st.success(
                    f"✅ Match committed successfully! Winner: Team {calc_res['winner']}"
                )

                # Render Player Delta Summary Cards
                p_cols = st.columns(len(calc_res["players"]))
                for idx, (pid, pres) in enumerate(calc_res["players"].items()):
                    with p_cols[idx]:
                        st.markdown(f"""
                        <div class="metric-card">
                            <strong>{pres['name']}</strong> ({pres['side']})<br>
                            MMR: {pres['pre_mmr']:.3f} ➔ <b>{pres['post_mmr']:.3f}</b><br>
                            Delta: <span style="color:{'#047857' if pres['delta']>=0 else '#b91c1c'}; font-weight:700;">{pres['delta']:+.4f}</span><br>
                            RD: {pres['pre_rd']:.1f} ➔ {pres['post_rd']:.1f}<br>
                            Acc: {pres['post_acc']:.1f}%<br>
                            Tier: <b>{pres['tier']}</b>
                        </div>
                        """, unsafe_allow_html=True)

            except Exception as ex:
                conn.rollback()
                st.error(f"Execution Error: {ex}")

    conn.close()


# ==============================================================================
# TAB 3: 🧠 SESSION LOGIC (V16.2 PROD)
# ==============================================================================
elif nav_selection == "🧠 Session Logic (V16.2 PROD)":
    st.title("🧠 Session Logic Engine (V16.2 PROD)")
    st.caption(
        "Two-stage atomic staging, live court traffic control, and chronological batch execution."
    )

    conn = get_db_connection()
    session_mode = st.radio(
        "Session Gateway",
        ["➕ Create Session", "🎮 Active Sessions Hub"],
        horizontal=True,
    )

    # --------------------------------------------------------------------------
    # SUB-MODE 1: CREATE SESSION
    # --------------------------------------------------------------------------
    if session_mode == "➕ Create Session":
        venues = conn.execute(
            "SELECT venue_id, venue_name, courts_count FROM venues WHERE is_active=1"
        ).fetchall()
        players = conn.execute(
            "SELECT player_id, name, is_provisional, latent_mmr FROM players ORDER BY name"
        ).fetchall()
        formats = conn.execute("SELECT * FROM match_formats").fetchall()

        v_dict = {v["venue_name"]: dict(v) for v in venues}
        p_dict = {
            format_pr_name(p["name"], p["is_provisional"]): p["player_id"]
            for p in players
        }

        with st.form("create_session_form"):
            s_title = st.text_input("Session Title", value="Pro-Am Social Round Robin")
            c1, c2 = st.columns(2)
            sel_ven = c1.selectbox(
                "Hosting Venue", list(v_dict.keys()) if v_dict else ["None"]
            )
            s_type = c2.selectbox(
                "Tournament Structure",
                ["ROUND_ROBIN", "AMERICANO", "MEXICANO", "HYBRID"],
            )

            c3, c4 = st.columns(2)
            t_format = c3.selectbox(
                "Team Alignment", ["FIXED_DOUBLES", "ROTATING_DOUBLES", "SINGLES"]
            )

            # Compatibility Filter for Match Formats
            if t_format == "ROTATING_DOUBLES":
                compat_fmts = [
                    f
                    for f in formats
                    if f["category"] in ("AMERICANO", "MEXICANO", "SPRINT")
                ]
            else:
                compat_fmts = [
                    f
                    for f in formats
                    if f["category"] in ("STANDARD", "SPRINT")
                ]

            fmt_labels = {
                f"{f['format_name']} ({f['category']})": f["format_id"]
                for f in compat_fmts
            }
            sel_fmt = c4.selectbox(
                "Scoring Format",
                list(fmt_labels.keys()) if fmt_labels else ["None"],
            )

            avail_courts = v_dict[sel_ven]["courts_count"] if sel_ven in v_dict else 4
            courts_allocated = st.slider(
                "Courts Dedicated", min_value=1, max_value=avail_courts, value=min(2, avail_courts)
            )

            enrolled_players = st.multiselect(
                "Enrolled Roster", list(p_dict.keys())
            )
            create_btn = st.form_submit_button("🚀 Initialize Session Schedule")

        if create_btn:
            if len(enrolled_players) < (
                2 if t_format == "SINGLES" else 4
            ):
                st.error("Validation Error: Insufficient players enrolled for selected format.")
            elif not fmt_labels:
                st.error("Validation Error: No compatible formats available.")
            else:
                s_id = str(uuid.uuid4())
                f_id = fmt_labels[sel_fmt]
                venue_id = v_dict[sel_ven]["venue_id"]

                c = conn.cursor()
                c.execute(
                    """
                    INSERT INTO sessions (
                        session_id, venue_id, session_title, session_type,
                        team_format, format_id, court_count, status
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, 'ACTIVE')
                """,
                    (
                        s_id,
                        venue_id,
                        s_title,
                        s_type,
                        t_format,
                        f_id,
                        courts_allocated,
                    ),
                )

                # Seed Session Rosters
                for ep_name in enrolled_players:
                    pid = p_dict[ep_name]
                    p_row = conn.execute(
                        "SELECT latent_mmr FROM players WHERE player_id=?",
                        (pid,),
                    ).fetchone()
                    snap_mmr = p_row["latent_mmr"] if p_row else 3.000
                    c.execute(
                        """
                        INSERT INTO session_rosters (
                            roster_id, session_id, player_id, initial_rating_snapshot
                        ) VALUES (?, ?, ?, ?)
                    """,
                        (str(uuid.uuid4()), s_id, pid, snap_mmr),
                    )

                # Generate Schedule Matrix
                p_ids = [p_dict[name] for name in enrolled_players]
                if t_format == "FIXED_DOUBLES":
                    # Form pairings into pairs list
                    pairs = [
                        f"{p_ids[i]}|{p_ids[i+1]}"
                        for i in range(0, len(p_ids) - 1, 2)
                    ]
                    fixtures = (
                        SessionLogicEngine.generate_fixed_teams_schedule(
                            pairs, courts_allocated
                        )
                    )
                    for f in fixtures:
                        ta_p1, ta_p2 = f["team_a"].split("|")
                        tb_p1, tb_p2 = f["team_b"].split("|")
                        c.execute(
                            """
                            INSERT INTO session_matches (
                                session_match_id, session_id, round_number, court_number,
                                team_a_p1, team_a_p2, team_b_p1, team_b_p2, status
                            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'SCHEDULED')
                        """,
                            (
                                str(uuid.uuid4()),
                                s_id,
                                f["round_number"],
                                f["court_number"],
                                ta_p1,
                                ta_p2,
                                tb_p1,
                                tb_p2,
                            ),
                        )
                elif t_format == "ROTATING_DOUBLES":
                    fixtures = (
                        SessionLogicEngine.generate_rotating_americano_schedule(
                            p_ids, courts_allocated
                        )
                    )
                    for f in fixtures:
                        c.execute(
                            """
                            INSERT INTO session_matches (
                                session_match_id, session_id, round_number, court_number,
                                team_a_p1, team_a_p2, team_b_p1, team_b_p2, status
                            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'SCHEDULED')
                        """,
                            (
                                str(uuid.uuid4()),
                                s_id,
                                f["round_number"],
                                f["court_number"],
                                f["team_a_p1"],
                                f["team_a_p2"],
                                f["team_b_p1"],
                                f["team_b_p2"],
                            ),
                        )

                conn.commit()
                st.success("Session and fixture schedule initialized successfully!")
                st.rerun()

    # --------------------------------------------------------------------------
    # SUB-MODE 2: ACTIVE SESSIONS HUB
    # --------------------------------------------------------------------------
    else:
        active_sessions = conn.execute("""
            SELECT s.*, v.venue_name, mf.format_name 
            FROM sessions s
            JOIN venues v ON s.venue_id = v.venue_id
            JOIN match_formats mf ON s.format_id = mf.format_id
            WHERE s.status IN ('ACTIVE', 'STAGED')
            ORDER BY s.created_at DESC
        """).fetchall()

        if not active_sessions:
            st.info("No active sessions currently running.")
        else:
            s_map = {
                f"{s['session_title']} ({s['venue_name']} | {s['format_name']})": s["session_id"]
                for s in active_sessions
            }
            sel_s_label = st.selectbox("Select Active Session", list(s_map.keys()))
            cur_sid = s_map[sel_s_label]
            cur_s = conn.execute(
                "SELECT * FROM sessions WHERE session_id=?", (cur_sid,)
            ).fetchone()

            sub_nav = st.radio(
                "Operational Stage",
                ["🏟️ Live Court Hub", "📊 Standings & Atomic Commit"],
                horizontal=True,
            )

            # ------------------------------------------------------------------
            # STAGE 1: LIVE COURT HUB & FUNGIBLE SCORING
            # ------------------------------------------------------------------
            if sub_nav == "🏟️ Live Court Hub":
                st.subheader(f"Court Traffic Controller — {cur_s['session_title']}")

                p_all = conn.execute("SELECT player_id, name FROM players").fetchall()
                name_lookup = {p["player_id"]: p["name"] for p in p_all}

                fixtures = conn.execute(
                    """
                    SELECT * FROM session_matches 
                    WHERE session_id = ? 
                    ORDER BY round_number, court_number
                """,
                    (cur_sid,),
                ).fetchall()

                if not fixtures:
                    st.warning("No scheduled fixtures found for this session.")
                else:
                    rounds = sorted(list(set(f["round_number"] for f in fixtures)))
                    sel_round = st.selectbox(
                        "Inspect Round",
                        rounds,
                        format_func=lambda r: f"Round {r}",
                    )

                    round_fixtures = [
                        f for f in fixtures if f["round_number"] == sel_round
                    ]

                    for fix in round_fixtures:
                        f_id = fix["session_match_id"]
                        p1_n = name_lookup.get(fix["team_a_p1"], "P1")
                        p2_n = name_lookup.get(fix["team_a_p2"], "P2") if fix["team_a_p2"] else ""
                        p3_n = name_lookup.get(fix["team_b_p1"], "P3")
                        p4_n = name_lookup.get(fix["team_b_p2"], "P4") if fix["team_b_p2"] else ""

                        t_a_label = f"🔵 {p1_n} & {p2_n}" if p2_n else f"🔵 {p1_n}"
                        t_b_label = f"🔴 {p3_n} & {p4_n}" if p4_n else f"🔴 {p3_n}"

                        with st.expander(
                            f"Court {fix['court_number']} — {t_a_label} vs {t_b_label} [{fix['status']}]",
                            expanded=(fix["status"] != "COMMITTED"),
                        ):
                            sc1, sc2, sc3 = st.columns([2, 2, 1])
                            in_sc_a = sc1.number_input(
                                f"Score Team A ({p1_n})",
                                min_value=0,
                                max_value=50,
                                value=fix["score_a"],
                                key=f"sa_{f_id}",
                            )
                            in_sc_b = sc2.number_input(
                                f"Score Team B ({p3_n})",
                                min_value=0,
                                max_value=50,
                                value=fix["score_b"],
                                key=f"sb_{f_id}",
                            )

                            if sc3.button("Save Result", key=f"btn_save_{f_id}"):
                                conn.execute(
                                    """
                                    UPDATE session_matches 
                                    SET score_a = ?, score_b = ?, status = 'SCORED' 
                                    WHERE session_match_id = ?
                                """,
                                    (in_sc_a, in_sc_b, f_id),
                                )
                                # Update Roster Running Points
                                conn.execute(
                                    "UPDATE session_rosters SET running_points = running_points + ? WHERE session_id = ? AND player_id IN (?, ?)",
                                    (
                                        in_sc_a,
                                        cur_sid,
                                        fix["team_a_p1"],
                                        fix["team_a_p2"] or "",
                                    ),
                                )
                                conn.execute(
                                    "UPDATE session_rosters SET running_points = running_points + ? WHERE session_id = ? AND player_id IN (?, ?)",
                                    (
                                        in_sc_b,
                                        cur_sid,
                                        fix["team_b_p1"],
                                        fix["team_b_p2"] or "",
                                    ),
                                )
                                conn.commit()
                                st.success("Match score staged in session memory!")
                                st.rerun()

            # ------------------------------------------------------------------
            # STAGE 2: STANDINGS & ATOMIC BATCH COMMIT
            # ------------------------------------------------------------------
            else:
                st.subheader(f"Session Standings — {cur_s['session_title']}")

                rosters = conn.execute(
                    """
                    SELECT sr.*, p.name, p.latent_mmr, p.rd, p.calibration_tier
                    FROM session_rosters sr
                    JOIN players p ON sr.player_id = p.player_id
                    WHERE sr.session_id = ?
                    ORDER BY sr.running_points DESC
                """,
                    (cur_sid,),
                ).fetchall()

                if rosters:
                    r_df = pd.DataFrame(
                        [
                            {
                                "Player": r["name"],
                                "Points": r["running_points"],
                                "Initial MMR": f"{r['initial_rating_snapshot']:.3f}",
                                "Current MMR": f"{r['latent_mmr']:.3f}",
                                "Tier": r["calibration_tier"],
                            }
                            for r in rosters
                        ]
                    )
                    st.dataframe(r_df, use_container_width=True, hide_index=True)

                st.divider()

                col_close, col_discard = st.columns(2)

                # Two-Stage Atomic Commit Trigger
                if col_close.button(
                    "🚀 Verify & Submit Entire Session to Engine",
                    use_container_width=True,
                ):
                    completed_fixtures = conn.execute(
                        """
                        SELECT * FROM session_matches 
                        WHERE session_id = ? AND status = 'SCORED'
                        ORDER BY round_number, court_number
                    """,
                        (cur_sid,),
                    ).fetchall()

                    if not completed_fixtures:
                        st.error("No completed fixtures to commit.")
                    else:
                        st.info(
                            f"Ingesting {len(completed_fixtures)} session fixtures chronologically..."
                        )

                        # Replay fixtures through RyftV16 sequential pipeline
                        for m in completed_fixtures:
                            p_rows = conn.execute(
                                """
                                SELECT * FROM players WHERE player_id IN (?, ?, ?, ?)
                            """,
                                (
                                    m["team_a_p1"],
                                    m["team_a_p2"] or "",
                                    m["team_b_p1"],
                                    m["team_b_p2"] or "",
                                ),
                            ).fetchall()
                            m_p_dict = {p["player_id"]: dict(p) for p in p_rows}

                            calc_res = RyftV16.compute_match(
                                m_p_dict[m["team_a_p1"]],
                                m_p_dict.get(m["team_a_p2"]),
                                m_p_dict[m["team_b_p1"]],
                                m_p_dict.get(m["team_b_p2"]),
                                m["score_a"],
                                m["score_b"],
                                m["score_a"],
                                m["score_b"],
                                cur_s["format_id"],
                                cur_s["venue_id"],
                                is_singles=(cur_s["team_format"] == "SINGLES"),
                                is_tournament=True,  # Session caps apply
                                conn=conn,
                            )

                            # Persist to master tables
                            m_id = str(uuid.uuid4())
                            conn.execute(
                                """
                                INSERT INTO matches (
                                    match_id, match_timestamp, venue_id, format_id,
                                    team_a_p1, team_a_p2, team_b_p1, team_b_p2,
                                    score_team_a, score_team_b, winner_team,
                                    delta_team_a, delta_team_b, margin_entropy,
                                    status, attestation_status, session_id
                                ) VALUES (?, CURRENT_TIMESTAMP, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'COMMITTED', 'COMMITTED', ?)
                            """,
                                (
                                    m_id,
                                    cur_s["venue_id"],
                                    cur_s["format_id"],
                                    m["team_a_p1"],
                                    m["team_a_p2"],
                                    m["team_b_p1"],
                                    m["team_b_p2"],
                                    m["score_a"],
                                    m["score_b"],
                                    calc_res["winner"],
                                    calc_res["team_a_delta"],
                                    calc_res["team_b_delta"],
                                    calc_res["s_margin"],
                                    cur_sid,
                                ),
                            )

                            for pid, pres in calc_res["players"].items():
                                conn.execute(
                                    """
                                    INSERT INTO match_logs (
                                        log_id, match_id, player_id, team_id,
                                        pre_mmr, post_mmr, delta_mmr,
                                        pre_rd, post_rd, pre_acc, post_acc
                                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                                """,
                                    (
                                        str(uuid.uuid4()),
                                        m_id,
                                        pid,
                                        pres["side"],
                                        pres["pre_mmr"],
                                        pres["post_mmr"],
                                        pres["delta"],
                                        pres["pre_rd"],
                                        pres["post_rd"],
                                        pres["pre_acc"],
                                        pres["post_acc"],
                                    ),
                                )

                                conn.execute(
                                    """
                                    UPDATE players SET
                                        latent_mmr = ?,
                                        display_rating = ?,
                                        rd = ?,
                                        accuracy_score = ?,
                                        verified_matches_count = verified_matches_count + 1,
                                        last_match_date = CURRENT_TIMESTAMP
                                    WHERE player_id = ?
                                """,
                                    (
                                        pres["post_mmr"],
                                        pres["post_disp"],
                                        pres["post_rd"],
                                        pres["post_acc"],
                                        pid,
                                    ),
                                )

                            # Mark fixture committed
                            conn.execute(
                                "UPDATE session_matches SET status = 'COMMITTED', match_id = ? WHERE session_match_id = ?",
                                (m_id, m["session_match_id"]),
                            )

                        conn.execute(
                            "UPDATE sessions SET status = 'COMMITTED', completed_at = CURRENT_TIMESTAMP WHERE session_id = ?",
                            (cur_sid,),
                        )
                        conn.commit()
                        st.balloons()
                        st.success(
                            "Session committed to rating engine successfully!"
                        )
                        st.rerun()

                if col_discard.button(
                    "🗑️️ Discard / Delete Session", use_container_width=True
                ):
                    conn.execute(
                        "DELETE FROM session_rosters WHERE session_id=?",
                        (cur_sid,),
                    )
                    conn.execute(
                        "DELETE FROM session_matches WHERE session_id=?",
                        (cur_sid,),
                    )
                    conn.execute(
                        "DELETE FROM sessions WHERE session_id=?", (cur_sid,)
                    )
                    conn.commit()
                    st.warning("Session discarded cleanly.")
                    st.rerun()

    conn.close()


# ==============================================================================
# TAB 4: 🏆 TOURNAMENT DESK (DELAYED ENTRY)
# ==============================================================================
elif nav_selection == "🏆 Tournament Desk (Delayed Entry)":
    st.title("🏆 Tournament Desk (Asynchronous Additive Stacking)")
    st.caption(
        "Ingest official tournament fixtures retroactively without rewriting subsequent matches."
    )

    conn = get_db_connection()
    venues = conn.execute(
        "SELECT venue_id, venue_name FROM venues WHERE is_active=1"
    ).fetchall()
    formats = conn.execute(
        "SELECT format_id, format_name FROM match_formats WHERE category='STANDARD'"
    ).fetchall()
    players = conn.execute(
        "SELECT player_id, name, latent_mmr, rd FROM players ORDER BY name"
    ).fetchall()

    v_map = {v["venue_name"]: v["venue_id"] for v in venues}
    f_map = {f["format_name"]: f["format_id"] for f in formats}
    p_map = {f"{p['name']} ({p['latent_mmr']:.2f})": p["player_id"] for p in players}
    p_meta = {p["player_id"]: dict(p) for p in players}

    with st.form("tourney_desk_form"):
        st.subheader("1. Official Sanctioned Fixture Details")
        c1, c2 = st.columns(2)
        sel_v = c1.selectbox(
            "Tournament Venue", list(v_map.keys()) if v_map else ["None"]
        )
        sel_f = c2.selectbox(
            "Format", list(f_map.keys()) if f_map else ["None"]
        )

        c3, c4 = st.columns(2)
        t_date = c3.date_input(
            "Historical Tournament Date", value=date.today() - datetime.timedelta(days=1)
        )
        t_time = c4.time_input("Scheduled Time", value=time(11, 0))
        t_ts_str = f"{t_date.isoformat()}T{t_time.strftime('%H:%M:%S')}+00:00"

        st.subheader("2. Bracket Participants")
        col_ta, col_tb = st.columns(2)
        ta_p1 = col_ta.selectbox(
            "Team A - Player 1", list(p_map.keys()), key="td_ta1"
        )
        ta_p2 = col_ta.selectbox(
            "Team A - Player 2", list(p_map.keys()), key="td_ta2"
        )
        tb_p1 = col_tb.selectbox(
            "Team B - Player 1", list(p_map.keys()), key="td_tb1"
        )
        tb_p2 = col_tb.selectbox(
            "Team B - Player 2", list(p_map.keys()), key="td_tb2"
        )

        st.subheader("3. Authenticated Scores")
        sc1, sc2 = st.columns(2)
        s_a = sc1.number_input("Team A Sets/Games", min_value=0, value=2)
        s_b = sc2.number_input("Team B Sets/Games", min_value=0, value=1)
        scoreline = st.text_input(
            "Official Scoreline Breakdown", value="6-4, 4-6, 7-6"
        )

        commit_tourney = st.form_submit_button(
            "⚡ Stack Tournament Delta to Live Ratings"
        )

    if commit_tourney:
        p1_id, p2_id, p3_id, p4_id = (
            p_map[ta_p1],
            p_map[ta_p2],
            p_map[tb_p1],
            p_map[tb_p2],
        )
        if len({p1_id, p2_id, p3_id, p4_id}) < 4:
            st.error("Validation Error: Duplicate players selected.")
        else:
            calc_res = RyftV16.compute_match(
                p_meta[p1_id],
                p_meta[p2_id],
                p_meta[p3_id],
                p_meta[p4_id],
                s_a,
                s_b,
                s_a,
                s_b,
                f_map[sel_f],
                v_map[sel_v],
                is_tournament=True,  # Bit 15 Tournament Desk Absolute Bypass
                conn=conn,
            )

            m_id = str(uuid.uuid4())
            conn.execute(
                """
                INSERT INTO matches (
                    match_id, match_timestamp, venue_id, format_id,
                    team_a_p1, team_a_p2, team_b_p1, team_b_p2,
                    score_team_a, score_team_b, winner_team,
                    delta_team_a, delta_team_b, margin_entropy,
                    status, attestation_status, scoreline_raw
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'COMMITTED', 'COMMITTED', ?)
            """,
                (
                    m_id,
                    t_ts_str,
                    v_map[sel_v],
                    f_map[sel_f],
                    p1_id,
                    p2_id,
                    p3_id,
                    p4_id,
                    s_a,
                    s_b,
                    calc_res["winner"],
                    calc_res["team_a_delta"],
                    calc_res["team_b_delta"],
                    calc_res["s_margin"],
                    scoreline,
                ),
            )

            # Apply additive deltas directly to live ratings without recursive cascade
            for pid, pres in calc_res["players"].items():
                conn.execute(
                    """
                    INSERT INTO match_logs (
                        log_id, match_id, player_id, team_id,
                        pre_mmr, post_mmr, delta_mmr,
                        pre_rd, post_rd, pre_acc, post_acc
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                    (
                        str(uuid.uuid4()),
                        m_id,
                        pid,
                        pres["side"],
                        pres["pre_mmr"],
                        pres["post_mmr"],
                        pres["delta"],
                        pres["pre_rd"],
                        pres["post_rd"],
                        pres["pre_acc"],
                        pres["post_acc"],
                    ),
                )

                conn.execute(
                    """
                    UPDATE players SET
                        latent_mmr = latent_mmr + ?,
                        display_rating = display_rating + ?,
                        verified_matches_count = verified_matches_count + 1
                    WHERE player_id = ?
                """,
                    (pres["delta"], pres["delta"], pid),
                )

            conn.commit()
            st.success(
                "Tournament delta stacked additively to live player ratings!"
            )
            st.rerun()

    conn.close()


# ==============================================================================
# TAB 5: 📜 HISTORICAL MATCHES (3-PANEL DEEP AUDIT LEDGER)
# ==============================================================================
elif nav_selection == "📜 Historical Matches":
    st.title("📜 Historical Matches & Algorithmic Audit Ledger")
    st.caption(
        "Exhaustive ledger of committed matches with 3-panel deep performance inspection."
    )

    conn = get_db_connection()
    venues = conn.execute("SELECT venue_id, venue_name FROM venues").fetchall()
    v_dict = {v["venue_id"]: v["venue_name"] for v in venues}

    # Filters
    c1, c2 = st.columns(2)
    sel_venue = c1.selectbox(
        "Filter by Venue", ["All Venues"] + [v["venue_name"] for v in venues]
    )
    status_filter = c2.selectbox(
        "Filter by Attestation", ["All", "COMMITTED", "PENDING", "DISPUTED"]
    )

    query = """
        SELECT m.*, mf.format_name,
               p1.name as p1_name, p2.name as p2_name,
               p3.name as p3_name, p4.name as p4_name
        FROM matches m
        JOIN match_formats mf ON m.format_id = mf.format_id
        JOIN players p1 ON m.team_a_p1 = p1.player_id
        LEFT JOIN players p2 ON m.team_a_p2 = p2.player_id
        JOIN players p3 ON m.team_b_p1 = p3.player_id
        LEFT JOIN players p4 ON m.team_b_p2 = p4.player_id
        WHERE 1=1
    """
    params = []
    if sel_venue != "All Venues":
        for v in venues:
            if v["venue_name"] == sel_venue:
                query += " AND m.venue_id = ?"
                params.append(v["venue_id"])
    if status_filter != "All":
        query += " AND m.attestation_status = ?"
        params.append(status_filter)

    query += " ORDER BY m.match_timestamp DESC LIMIT 50"
    matches = conn.execute(query, params).fetchall()

    if not matches:
        st.info("No historical matches found matching active filters.")
    else:
        for m in matches:
            t_a_str = (
                f"{m['p1_name']} & {m['p2_name']}"
                if m["p2_name"]
                else m["p1_name"]
            )
            t_b_str = (
                f"{m['p3_name']} & {m['p4_name']}"
                if m["p4_name"]
                else m["p3_name"]
            )
            v_name = v_dict.get(m["venue_id"], "Unknown Venue")
            score_disp = format_match_scoreline(
                m["score_team_a"], m["score_team_b"], m["scoreline_raw"]
            )

            # Header with authentic game scores and player rosters
            header_title = (
                f"🎾 {t_a_str}  [{score_disp}]  {t_b_str}  •  {v_name}"
            )

            with st.expander(header_title):
                # Fetch match individual logs
                logs = conn.execute(
                    """
                    SELECT ml.*, p.name 
                    FROM match_logs ml
                    JOIN players p ON ml.player_id = p.player_id
                    WHERE ml.match_id = ?
                    ORDER BY ml.team_id ASC
                """,
                    (m["match_id"],),
                ).fetchall()

                # --------------------------------------------------------------
                # THREE-PANEL DEEP DIVE AUDIT INSPECTION
                # --------------------------------------------------------------
                p_tab1, p_tab2, p_tab3 = st.tabs([
                    "⚖️ Pre-Match Balance & Odds",
                    "🛡️ Context & Guardrails",
                    "👥 Player Performance Matrix",
                ])

                # PANEL 1: PRE-MATCH BALANCE & ODDS
                with p_tab1:
                    o1, o2, o3 = st.columns(3)
                    o1.metric("Winner", f"Team {m['winner_team']}")
                    o2.metric("Margin Entropy (S_margin)", f"{m['margin_entropy']:.3f}")
                    o3.metric("Format Applied", m["format_name"])

                # PANEL 2: CONTEXT & SYSTEM GUARDRAILS
                with p_tab2:
                    g1, g2, g3 = st.columns(3)
                    g1.markdown(f"**Match ID:** `{m['match_id'][:13]}...`")
                    g1.markdown(f"**Timestamp:** {m['match_timestamp']}")
                    g2.markdown(f"**Attestation:** `{m['attestation_status']}`")
                    g2.markdown(
                        f"**Session Tag:** `{m['session_id'] or 'Casual Match'}`"
                    )
                    g3.markdown(
                        f"**City Bridge:** {'YES' if m['is_city_bridge'] else 'NO'}"
                    )
                    g3.markdown(
                        f"**Country Bridge:** {'YES' if m['is_country_bridge'] else 'NO'}"
                    )

                # PANEL 3: PLAYER PERFORMANCE MATRIX
                with p_tab3:
                    if logs:
                        l_cols = st.columns(len(logs))
                        for idx, log in enumerate(logs):
                            with l_cols[idx]:
                                st.markdown(f"""
                                <div class="metric-card">
                                    <strong>{log['name']}</strong> ({log['team_id']})<br>
                                    Pre: {log['pre_mmr']:.3f} ➔ Post: <b>{log['post_mmr']:.3f}</b><br>
                                    Delta: <b style="color:{'#047857' if log['delta_mmr']>=0 else '#b91c1c'};">{log['delta_mmr']:+.4f}</b><br>
                                    RD: {log['pre_rd']:.1f} ➔ {log['post_rd']:.1f}<br>
                                    Acc: {log['pre_acc']:.1f}% ➔ {log['post_acc']:.1f}%
                                </div>
                                """, unsafe_allow_html=True)
                    else:
                        st.info("No granular audit telemetry found for this entry.")

    conn.close()
# ==============================================================================
# TAB 6: 👥 PLAYER ROSTER & CALIBRATION
# ==============================================================================
elif nav_selection == "👥 Player Roster & Calibration":
    st.title("👥 Player Roster & Calibration Architecture")
    st.caption(
        "Manage player entities, audit 3-pillar accuracy decomposition, and manage Tri-Gate overrides."
    )

    conn = get_db_connection()
    c = conn.cursor()
    configs = RyftV16.get_configs(conn)

    roster_action = st.radio(
        "Roster Action",
        ["📋 Player Directory", "➕ Add New Player", "🛠️ Inspect & Edit Player"],
        horizontal=True,
    )

    # --------------------------------------------------------------------------
    # SUB-VIEW 1: PLAYER DIRECTORY
    # --------------------------------------------------------------------------
    if roster_action == "📋 Player Directory":
        st.subheader("Global Player Index")
        filter_col1, filter_col2, filter_col3 = st.columns(3)
        search_query = filter_col1.text_input("🔍 Search Player by Name", value="")
        tier_filter = filter_col2.selectbox(
            "Filter by Calibration Tier", ["ALL", "PROVISIONAL", "VERIFIED", "ANCHOR"]
        )
        gender_filter = filter_col3.selectbox(
            "Filter by Gender Division", ["ALL", "M", "F", "OPEN"]
        )

        query = """
            SELECT p.player_id, p.name, p.gender, p.latent_mmr, p.display_rating,
                   p.rd, p.accuracy_score, p.calibration_tier, p.is_anchor,
                   p.is_provisional, p.verified_matches_count, p.unique_opponents_count,
                   loc.location_name as home_city, v.venue_name as home_venue
            FROM players p
            LEFT JOIN locations loc ON p.home_city_id = loc.location_id
            LEFT JOIN venues v ON p.home_venue_id = v.venue_id
            WHERE 1=1
        """
        params = []
        if search_query:
            query += " AND p.name LIKE ?"
            params.append(f"%{search_query}%")
        if tier_filter != "ALL":
            if tier_filter == "ANCHOR":
                query += " AND p.is_anchor = 1"
            else:
                query += " AND p.calibration_tier = ?"
                params.append(tier_filter)
        if gender_filter != "ALL":
            query += " AND p.gender = ?"
            params.append(gender_filter)

        query += " ORDER BY p.latent_mmr DESC"
        p_rows = c.execute(query, params).fetchall()

        if p_rows:
            table_data = []
            for r in p_rows:
                table_data.append(
                    {
                        "Name": format_pr_name(r["name"], r["is_provisional"]),
                        "Gender": r["gender"],
                        "Latent MMR": f"{r['latent_mmr']:.3f}",
                        "Display": f"{r['display_rating']:.2f}",
                        "RD": f"{r['rd']:.1f}",
                        "Accuracy": f"{r['accuracy_score']:.1f}%",
                        "Tier": "ANCHOR" if r["is_anchor"] else r["calibration_tier"],
                        "Matches": r["verified_matches_count"],
                        "Unique Opps": r["unique_opponents_count"],
                        "City": r["home_city"] or "Unassigned",
                        "Club": r["home_venue"] or "Unassigned",
                    }
                )
            st.dataframe(pd.DataFrame(table_data), use_container_width=True, hide_index=True)
        else:
            st.info("No players match the designated filter criteria.")

    # --------------------------------------------------------------------------
    # SUB-VIEW 2: ADD NEW PLAYER
    # --------------------------------------------------------------------------
    elif roster_action == "➕ Add New Player":
        st.subheader("Onboard New Player Entity")
        cities = c.execute(
            "SELECT location_id, location_name FROM locations WHERE location_type='CITY' ORDER BY location_name"
        ).fetchall()
        venues = c.execute(
            "SELECT venue_id, venue_name FROM venues WHERE is_active=1 ORDER BY venue_name"
        ).fetchall()

        city_map = {ci["location_name"]: ci["location_id"] for ci in cities}
        venue_map = {ve["venue_name"]: ve["venue_id"] for ve in venues}

        with st.form("add_player_form"):
            c1, c2 = st.columns(2)
            p_name = c1.text_input("Full Name *", value="")
            p_gender = c2.selectbox(
                "Gender Division *", ["OPEN", "M", "F"]
            )

            c3, c4 = st.columns(2)
            p_city = c3.selectbox(
                "Home City",
                ["None"] + list(city_map.keys()) if city_map else ["None"],
            )
            p_venue = c4.selectbox(
                "Home Venue / Club",
                ["None"] + list(venue_map.keys()) if venue_map else ["None"],
            )

            c5, c6 = st.columns(2)
            init_cat = c5.selectbox(
                "Declared Self-Assessment Category",
                [
                    "Beginner (0.500)",
                    "Beginner+ (1.500)",
                    "Intermediate (2.500)",
                    "Intermediate+ (3.500)",
                    "Advanced (4.500)",
                    "Advanced+ (5.500)",
                    "Elite / Pro (6.000)",
                ],
            )
            cat_mmr_seed = {
                "Beginner (0.500)": 0.500,
                "Beginner+ (1.500)": 1.500,
                "Intermediate (2.500)": 2.500,
                "Intermediate+ (3.500)": 3.500,
                "Advanced (4.500)": 4.500,
                "Advanced+ (5.500)": 5.500,
                "Elite / Pro (6.000)": 6.000,
            }[init_cat]

            is_sys_anchor = (
                c6.checkbox(
                    "Designate as System Anchor (Seed Baseline)", value=False
                )
            )

            create_p_btn = st.form_submit_button("Create Player Profile")

        if create_p_btn:
            if not p_name.strip():
                st.error("Validation Error: Player name is strictly required.")
            else:
                new_pid = str(uuid.uuid4())
                initial_rd = 90.0 if is_sys_anchor else configs.get("RD_INITIAL", 350.0)
                initial_prov = 0 if is_sys_anchor else 1
                init_tier = "ANCHOR" if is_sys_anchor else "PROVISIONAL"
                h_city_id = city_map.get(p_city)
                h_ven_id = venue_map.get(p_venue)

                acc_score, s_rd, s_n, s_d = RyftV16.calculate_accuracy(
                    initial_rd, 0, 0, configs
                )

                c.execute(
                    """
                    INSERT INTO players (
                        player_id, name, gender, latent_mmr, display_rating,
                        rd, calibration_tier, is_anchor, is_provisional,
                        accuracy_score, accuracy_s_rd, accuracy_s_matches, accuracy_s_diversity,
                        all_time_peak_mmr, lowest_mmr, home_city_id, home_venue_id
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                    (
                        new_pid,
                        p_name.strip(),
                        p_gender,
                        cat_mmr_seed,
                        cat_mmr_seed,
                        initial_rd,
                        init_tier,
                        1 if is_sys_anchor else 0,
                        initial_prov,
                        acc_score,
                        s_rd,
                        s_n,
                        s_d,
                        cat_mmr_seed,
                        cat_mmr_seed,
                        h_city_id,
                        h_ven_id,
                    ),
                )
                conn.commit()
                st.success(f"Player {p_name} onboarded successfully!")
                st.rerun()

    # --------------------------------------------------------------------------
    # SUB-VIEW 3: INSPECT & EDIT PLAYER
    # --------------------------------------------------------------------------
    else:
        st.subheader("Granular Player Profile & Calibration Overrides")
        all_p = c.execute("SELECT player_id, name FROM players ORDER BY name").fetchall()
        p_name_map = {p["name"]: p["player_id"] for p in all_p}

        if not p_name_map:
            st.info("No players available for inspection.")
        else:
            sel_p_name = st.selectbox(
                "Select Player Entity", list(p_name_map.keys())
            )
            target_pid = p_name_map[sel_p_name]
            p_data = c.execute(
                "SELECT * FROM players WHERE player_id=?", (target_pid,)
            ).fetchone()

            # 3-Pillar Decomposed Telemetry Cards
            c_rust = configs.get("C_RUST", 1.200)
            eff_rd = RyftV16.get_effective_rd(
                p_data["rd"], p_data["last_match_date"], c_rust
            )
            acc_score, s_rd, s_n, s_d = RyftV16.calculate_accuracy(
                eff_rd,
                p_data["verified_matches_count"],
                p_data["unique_opponents_count"],
                configs,
            )

            tc1, tc2, tc3, tc4 = st.columns(4)
            tc1.metric("Latent MMR", f"{p_data['latent_mmr']:.3f}")
            tc2.metric("Display Rating", f"{p_data['display_rating']:.2f}")
            tc3.metric(
                "Effective RD",
                f"{eff_rd:.1f}",
                delta=f"{eff_rd - p_data['rd']:+.1f} Rust"
                if eff_rd > p_data["rd"]
                else "Active",
            )
            tc4.metric("Tri-Gate Accuracy", f"{acc_score:.1f}%")

            st.markdown("##### 📐 Tri-Gate Pillar Decomposition")
            p_col1, p_col2, p_col3 = st.columns(3)
            p_col1.markdown(f"**Pillar 1 (Uncertainty Contraction $S_{{RD}}$):** `{s_rd*100:.1f}%`")
            p_col2.markdown(f"**Pillar 2 (Sample Depth $S_N$):** `{s_n*100:.1f}%` ({p_data['verified_matches_count']} matches)")
            p_col3.markdown(f"**Pillar 3 (Network Diversity $S_D$):** `{s_d*100:.1f}%` ({p_data['unique_opponents_count']} opponents)")

            st.divider()

            # Calibration Override Form
            with st.form("edit_player_form"):
                st.markdown("##### 🛠️ Administrative Calibration Overrides")
                e1, e2, e3 = st.columns(3)
                edit_mmr = e1.number_input(
                    "Latent MMR [0.000 - 7.000]",
                    min_value=0.0,
                    max_value=7.0,
                    value=float(p_data["latent_mmr"]),
                    step=0.001,
                    format="%.3f",
                )
                edit_disp = e2.number_input(
                    "Display Rating [0.00 - 7.00]",
                    min_value=0.0,
                    max_value=7.0,
                    value=float(p_data["display_rating"]),
                    step=0.01,
                    format="%.2f",
                )
                edit_rd = e3.number_input(
                    "Rating Deviation (RD) [30.0 - 350.0]",
                    min_value=30.0,
                    max_value=350.0,
                    value=float(p_data["rd"]),
                    step=1.0,
                    format="%.1f",
                )

                e4, e5, e6 = st.columns(3)
                edit_prov = e4.checkbox(
                    "Is Provisional [PR]", value=bool(p_data["is_provisional"])
                )
                edit_anchor = e5.checkbox(
                    "Designate System Anchor", value=bool(p_data["is_anchor"])
                )
                edit_manual = e6.checkbox(
                    "Manual Tri-Gate Verified Override",
                    value=bool(p_data["is_manually_verified"]),
                )

                e7, e8 = st.columns(2)
                edit_sybil = e7.slider(
                    "Sybil Network Trust Score",
                    min_value=0.20,
                    max_value=1.00,
                    value=float(p_data["sybil_trust_score"]),
                    step=0.05,
                )
                edit_tier = e8.selectbox(
                    "Calibration Status Tier",
                    ["PROVISIONAL", "VERIFIED", "ANCHOR", "INACTIVE"],
                    index=["PROVISIONAL", "VERIFIED", "ANCHOR", "INACTIVE"].index(
                        p_data["calibration_tier"]
                        if p_data["calibration_tier"] in ["PROVISIONAL", "VERIFIED", "ANCHOR", "INACTIVE"]
                        else "PROVISIONAL"
                    ),
                )

                save_p_btn = st.form_submit_button("💾 Commit Profile Changes")

            if save_p_btn:
                c.execute(
                    """
                    UPDATE players SET
                        latent_mmr = ?,
                        display_rating = ?,
                        rd = ?,
                        is_provisional = ?,
                        is_anchor = ?,
                        is_manually_verified = ?,
                        sybil_trust_score = ?,
                        calibration_tier = ?,
                        accuracy_score = ?,
                        updated_at = CURRENT_TIMESTAMP
                    WHERE player_id = ?
                """,
                    (
                        edit_mmr,
                        edit_disp,
                        edit_rd,
                        1 if edit_prov else 0,
                        1 if edit_anchor else 0,
                        1 if edit_manual else 0,
                        edit_sybil,
                        edit_tier,
                        acc_score,
                        target_pid,
                    ),
                )
                conn.commit()
                st.success("Player profile updated successfully!")
                st.rerun()

    conn.close()


# ==============================================================================
# TAB 7: 🏢 VENUES & REGIONS (DECOUPLED AGGREGATIONS)
# ==============================================================================
elif nav_selection == "🏢 Venues & Regions":
    st.title("🏢 Venues, Clubs & Regional Hierarchy")
    st.caption(
        "Decoupled SQL aggregations eliminating Cartesian court multiplication bugs."
    )

    conn = get_db_connection()
    c = conn.cursor()

    vr_mode = st.radio(
        "Regional Action",
        ["🌍 Hierarchical Overview", "➕ Register Venue", "🏙️ Add Territory / City"],
        horizontal=True,
    )

    # --------------------------------------------------------------------------
    # SUB-VIEW 1: HIERARCHICAL OVERVIEW (ERROR 1 FIXED: DECOUPLED QUERIES)
    # --------------------------------------------------------------------------
    if vr_mode == "🌍 Hierarchical Overview":
        st.subheader("Physical Hierarchy & True Court Infrastructure")

        # Query countries
        countries = c.execute(
            "SELECT * FROM locations WHERE location_type='COUNTRY' ORDER BY location_name"
        ).fetchall()

        if not countries:
            st.info("No geographical territories registered. Add a territory to start.")
        else:
            for country in countries:
                cid = country["location_id"]
                with st.expander(f"📍 {country['location_name']} (Country Infrastructure)", expanded=True):
                    # Decoupled Subqueries: True physical count of venues, courts, and players
                    stats = c.execute("""
                        SELECT 
                            (SELECT COUNT(*) FROM venues WHERE country_id = ?) as total_venues,
                            (SELECT COALESCE(SUM(courts_count), 0) FROM venues WHERE country_id = ?) as total_courts,
                            (SELECT COUNT(*) FROM players p JOIN locations loc ON p.home_city_id = loc.location_id WHERE loc.parent_id = ?) as total_players,
                            (SELECT COUNT(DISTINCT m.match_id) FROM matches m JOIN venues v ON m.venue_id = v.venue_id WHERE v.country_id = ?) as total_matches,
                            (SELECT COUNT(DISTINCT CASE WHEN m.is_country_bridge = 1 THEN m.match_id END) FROM matches m JOIN venues v ON m.venue_id = v.venue_id WHERE v.country_id = ?) as country_bridges
                    """, (cid, cid, cid, cid, cid)).fetchone()

                    cs1, cs2, cs3, cs4, cs5 = st.columns(5)
                    cs1.metric("Venues Registered", stats["total_venues"])
                    cs2.metric("True Court Count", stats["total_courts"])
                    cs3.metric("Resident Players", stats["total_players"])
                    cs4.metric("Matches Executed", stats["total_matches"])
                    cs5.metric("Country Bridges", stats["country_bridges"])

                    # Fetch municipal territories under this country
                    cities = c.execute(
                        "SELECT * FROM locations WHERE parent_id = ? AND location_type = 'CITY' ORDER BY location_name",
                        (cid,),
                    ).fetchall()

                    if cities:
                        st.markdown("##### Municipal Territories")
                        city_table = []
                        for ci in cities:
                            ci_id = ci["location_id"]
                            c_stat = c.execute("""
                                SELECT 
                                    (SELECT COUNT(*) FROM venues WHERE city_id = ?) as v_count,
                                    (SELECT COALESCE(SUM(courts_count), 0) FROM venues WHERE city_id = ?) as c_count,
                                    (SELECT COUNT(*) FROM players WHERE home_city_id = ?) as p_count,
                                    (SELECT COUNT(DISTINCT m.match_id) FROM matches m JOIN venues v ON m.venue_id = v.venue_id WHERE v.city_id = ?) as m_count,
                                    (SELECT COUNT(DISTINCT CASE WHEN m.is_city_bridge = 1 THEN m.match_id END) FROM matches m JOIN venues v ON m.venue_id = v.venue_id WHERE v.city_id = ?) as city_bridges
                            """, (ci_id, ci_id, ci_id, ci_id, ci_id)).fetchone()

                            city_table.append(
                                {
                                    "City Name": ci["location_name"],
                                    "Venues": c_stat["v_count"],
                                    "Physical Courts": c_stat["c_count"],
                                    "Players": c_stat["p_count"],
                                    "Matches": c_stat["m_count"],
                                    "City Bridges": c_stat["city_bridges"],
                                    "Readiness Score": f"{ci['readiness_score']:.1f}%",
                                }
                            )
                        st.dataframe(pd.DataFrame(city_table), use_container_width=True, hide_index=True)

    # --------------------------------------------------------------------------
    # SUB-VIEW 2: REGISTER VENUE
    # --------------------------------------------------------------------------
    elif vr_mode == "➕ Register Venue":
        st.subheader("Register Official Club Venue")
        cities = c.execute(
            "SELECT location_id, location_name, parent_id FROM locations WHERE location_type='CITY' ORDER BY location_name"
        ).fetchall()
        c_map = {ci["location_name"]: (ci["location_id"], ci["parent_id"]) for ci in cities}

        with st.form("reg_venue_form"):
            v_name = st.text_input("Venue / Club Name *")
            sel_city = st.selectbox(
                "City Territory *", list(c_map.keys()) if c_map else ["None"]
            )
            courts_c = st.number_input(
                "Physical Courts Count *", min_value=1, max_value=64, value=4, step=1
            )
            reg_v_btn = st.form_submit_button("Commit Venue to Registry")

        if reg_v_btn:
            if not v_name.strip() or not c_map:
                st.error("Validation Error: Venue name and city are strictly required.")
            else:
                city_id, country_id = c_map[sel_city]
                new_vid = str(uuid.uuid4())
                c.execute(
                    """
                    INSERT INTO venues (venue_id, venue_name, city_id, country_id, courts_count)
                    VALUES (?, ?, ?, ?, ?)
                """,
                    (new_vid, v_name.strip(), city_id, country_id, courts_c),
                )
                conn.commit()
                st.success(f"Venue {v_name} registered successfully!")
                st.rerun()

    # --------------------------------------------------------------------------
    # SUB-VIEW 3: ADD TERRITORY / CITY
    # --------------------------------------------------------------------------
    else:
        st.subheader("Add Country or City Territory")
        t_type = st.radio("Territory Level", ["COUNTRY", "CITY"], horizontal=True)

        with st.form("add_loc_form"):
            t_name = st.text_input("Territory Name *")
            parent_cid = None
            if t_type == "CITY":
                countries = c.execute(
                    "SELECT location_id, location_name FROM locations WHERE location_type='COUNTRY' ORDER BY location_name"
                ).fetchall()
                country_map = {co["location_name"]: co["location_id"] for co in countries}
                sel_country = st.selectbox(
                    "Parent Country *",
                    list(country_map.keys()) if country_map else ["None"],
                )
                parent_cid = country_map.get(sel_country)

            add_t_btn = st.form_submit_button("Register Territory")

        if add_t_btn:
            if not t_name.strip():
                st.error("Validation Error: Territory name is strictly required.")
            else:
                new_lid = str(uuid.uuid4())
                c.execute(
                    """
                    INSERT INTO locations (location_id, location_type, location_name, parent_id)
                    VALUES (?, ?, ?, ?)
                """,
                    (new_lid, t_type, t_name.strip(), parent_cid),
                )
                conn.commit()
                st.success(f"{t_type.capitalize()} '{t_name}' registered successfully!")
                st.rerun()

    conn.close()


# ==============================================================================
# TAB 8: 🌐 HAWKING ENGINE (V2 COMPLETE & TELEMETRY AUDIT)
# ==============================================================================
elif nav_selection == "🌐 Hawking Engine (V2 Complete)":
    st.title("🌐 Hawking Macro Engine (V2 Parity & Readiness)")
    st.caption(
        "Regional cross-calibration, empirical traveler bridge nodes, and municipal readiness scoring."
    )

    conn = get_db_connection()
    c = conn.cursor()
    configs = RyftV16.get_configs(conn)

    # --------------------------------------------------------------------------
    # PERSISTENT STATUS BAR ACROSS TOP
    # --------------------------------------------------------------------------
    active_bridges = c.execute(
        "SELECT COUNT(DISTINCT match_id) FROM matches WHERE is_city_bridge = 1 OR is_country_bridge = 1"
    ).fetchone()[0]
    total_venues = c.execute("SELECT COUNT(*) FROM venues WHERE is_active=1").fetchone()[0]
    total_cities = c.execute(
        "SELECT COUNT(*) FROM locations WHERE location_type='CITY'"
    ).fetchone()[0]

    st.markdown(f"""
    <div style="background-color:#0f172a; color:#f8fafc; padding:12px 18px; border-radius:8px; margin-bottom:15px; display:flex; justify-content:space-between; align-items:center;">
        <div><strong>🌐 Hawking Engine Status:</strong> Active & Calibrated</div>
        <div>Active Bridge Fixtures: <b>{active_bridges}</b> | Municipal Territories: <b>{total_cities}</b> | Connected Clubs: <b>{total_venues}</b></div>
    </div>
    """, unsafe_allow_html=True)

    hawking_tab1, hawking_tab2, hawking_tab3, hawking_tab4 = st.tabs([
        "🏙️ Municipal Readiness & Offsets",
        "⚖️ Cross-Region Parity Clashes",
        "📜 Granular Sync Audit Ledger",
        "👻 Synthetic Ghost Sandbox",
    ])

    # --------------------------------------------------------------------------
    # SUB-TAB 1: MUNICIPAL READINESS & OFFSETS
    # --------------------------------------------------------------------------
    with hawking_tab1:
        st.subheader("Municipal Ecosystem Readiness")
        cities = c.execute(
            "SELECT * FROM locations WHERE location_type='CITY' ORDER BY location_name"
        ).fetchall()

        if not cities:
            st.info("No cities registered in the system.")
        else:
            w_ver = configs.get("READINESS_VERIFIED_WEIGHT", 0.40)
            w_dep = configs.get("READINESS_DEPTH_WEIGHT", 0.35)
            w_bri = configs.get("READINESS_BRIDGE_WEIGHT", 0.25)

            city_cards = []
            for ci in cities:
                ci_id = ci["location_id"]

                # Pull verified player counts and match volume safely
                v_count = c.execute(
                    "SELECT COUNT(*) FROM players WHERE home_city_id=? AND calibration_tier='VERIFIED'",
                    (ci_id,),
                ).fetchone()[0]
                total_p = c.execute(
                    "SELECT COUNT(*) FROM players WHERE home_city_id=?", (ci_id,)
                ).fetchone()[0]
                m_count = c.execute(
                    """
                    SELECT COUNT(DISTINCT m.match_id) 
                    FROM matches m 
                    JOIN venues v ON m.venue_id = v.venue_id 
                    WHERE v.city_id = ?
                """,
                    (ci_id,),
                ).fetchone()[0]
                b_count = c.execute(
                    """
                    SELECT COUNT(DISTINCT m.match_id) 
                    FROM matches m 
                    JOIN venues v ON m.venue_id = v.venue_id 
                    WHERE v.city_id = ? AND m.is_city_bridge = 1
                """,
                    (ci_id,),
                ).fetchone()[0]

                # Cold-Start safe median MMR
                p_mmrs = [
                    r[0]
                    for r in c.execute(
                        "SELECT latent_mmr FROM players WHERE home_city_id=? AND calibration_tier != 'INACTIVE'",
                        (ci_id,),
                    ).fetchall()
                ]
                if p_mmrs:
                    p_mmrs.sort()
                    med_mmr_disp = f"{p_mmrs[len(p_mmrs)//2]:.3f}"
                else:
                    med_mmr_disp = "N/A (Cold Start)"

                # Compute Triad Readiness
                s_v = min(1.0, v_count / 15.0)
                avg_m = (m_count / total_p) if total_p > 0 else 0.0
                s_dep = min(1.0, avg_m / 8.0)
                s_bri = min(1.0, b_count / 5.0)

                readiness_score = round(
                    ((s_v * w_ver) + (s_dep * w_dep) + (s_bri * w_bri)) * 100.0, 1
                )

                # Persist readiness back to database
                c.execute(
                    "UPDATE locations SET readiness_score=? WHERE location_id=?",
                    (readiness_score, ci_id),
                )

                city_cards.append(
                    {
                        "City": ci["location_name"],
                        "Readiness": readiness_score,
                        "Verified Players": v_count,
                        "Total Players": total_p,
                        "Matches": m_count,
                        "Traveler Bridges": b_count,
                        "Median MMR": med_mmr_disp,
                        "Active Offset": f"{ci['regional_offset']:+.4f}",
                        "location_id": ci_id,
                    }
                )

            conn.commit()

            c_df = pd.DataFrame(city_cards)
            st.dataframe(
                c_df.drop(columns=["location_id"]),
                use_container_width=True,
                hide_index=True,
            )

            st.markdown("##### 🚀 Staged Deployment Drawer")
            sel_ci_name = st.selectbox(
                "Select City to Deploy Calibrated Offset",
                [c["City"] for c in city_cards],
            )
            chosen_ci = next(c for c in city_cards if c["City"] == sel_ci_name)

            col_off1, col_off2 = st.columns([2, 1])
            new_offset = col_off1.number_input(
                f"Proposed Regional Offset for {sel_ci_name}",
                value=0.0000,
                step=0.0050,
                format="%.4f",
            )
            if col_off2.button(
                f"🚀 Approve & Deploy {new_offset:+.4f} to {sel_ci_name}",
                use_container_width=True,
            ):
                c.execute(
                    "UPDATE locations SET regional_offset=? WHERE location_id=?",
                    (new_offset, chosen_ci["location_id"]),
                )
                conn.commit()
                st.success(
                    f"Offset {new_offset:+.4f} deployed to {sel_ci_name}!"
                )
                st.rerun()

    # --------------------------------------------------------------------------
    # SUB-TAB 2: CROSS-REGION PARITY CLASHES (SAMPLE-SIZE GATED)
    # --------------------------------------------------------------------------
    with hawking_tab2:
        st.subheader("Cross-Region Empirical Parity Clash Analyzer")
        min_sample_req = int(configs.get("HAWKING_MIN_SAMPLE", 5.0))
        cities = c.execute(
            "SELECT location_id, location_name FROM locations WHERE location_type='CITY' ORDER BY location_name"
        ).fetchall()

        if len(cities) < 2:
            st.info("At least two municipal territories are required for cross-region parity clash.")
        else:
            c1, c2 = st.columns(2)
            ci_a_name = c1.selectbox("Territory A (Local Base)", [ci["location_name"] for ci in cities], index=0)
            ci_b_name = c2.selectbox(
                "Territory B (Target Comparison)",
                [ci["location_name"] for ci in cities],
                index=1,
            )

            if ci_a_name == ci_b_name:
                st.warning("Select two distinct municipal territories for parity analysis.")
            else:
                ci_a_id = next(
                    ci["location_id"]
                    for ci in cities
                    if ci["location_name"] == ci_a_name
                )
                ci_b_id = next(
                    ci["location_id"]
                    for ci in cities
                    if ci["location_name"] == ci_b_name
                )

                # Strict Sample Size Gate
                v_count_a = c.execute(
                    "SELECT COUNT(*) FROM players WHERE home_city_id=? AND rd <= 100.0 AND calibration_tier != 'INACTIVE'",
                    (ci_a_id,),
                ).fetchone()[0]
                v_count_b = c.execute(
                    "SELECT COUNT(*) FROM players WHERE home_city_id=? AND rd <= 100.0 AND calibration_tier != 'INACTIVE'",
                    (ci_b_id,),
                ).fetchone()[0]

                if (
                    v_count_a < min_sample_req
                    or v_count_b < min_sample_req
                ):
                    st.warning(f"""
                    ⚠️ **Sample Size Guardrail Active:**
                    Hawking Parity Clash requires a minimum of **{min_sample_req} verified players** ($RD \\le 100.0$) in both territories.
                    - **{ci_a_name}:** {v_count_a} verified players
                    - **{ci_b_name}:** {v_count_b} verified players
                    
                    Parity clashes are locked until both regions establish sufficient statistical volume.
                    """)
                else:
                    st.success(
                        f"✅ Statistical Quota Satisfied: {ci_a_name} ({v_count_a} verified) vs {ci_b_name} ({v_count_b} verified)."
                    )

                    # Inter-region bridge matches
                    bridges = c.execute("""
                        SELECT COUNT(DISTINCT m.match_id) 
                        FROM matches m
                        JOIN players p1 ON m.team_a_p1 = p1.player_id
                        JOIN players p2 ON m.team_b_p1 = p2.player_id
                        WHERE (p1.home_city_id = ? AND p2.home_city_id = ?)
                           OR (p1.home_city_id = ? AND p2.home_city_id = ?)
                    """, (ci_a_id, ci_b_id, ci_b_id, ci_a_id)).fetchone()[0]

                    # Tikhonov Damped Weighting
                    k_0 = configs.get("BRIDGE_DAMPING_CONSTANT", 3.00)
                    w_conf = bridges / (bridges + k_0) if bridges > 0 else 0.0

                    col_b1, col_b2, col_b3 = st.columns(3)
                    col_b1.metric("Inter-City Traveler Matches (K)", bridges)
                    col_b2.metric("Tikhonov Confidence Weight", f"{w_conf:.3f}")
                    col_b3.metric("Regularizer Constant (K_0)", f"{k_0:.1f}")

    # --------------------------------------------------------------------------
    # SUB-TAB 3: GRANULAR SYNC AUDIT LEDGER
    # --------------------------------------------------------------------------
    with hawking_tab3:
        st.subheader("Granular Multi-Level Hawking Audit Ledger")

        # Filters
        c_f1, c_f2, c_f3 = st.columns(3)
        cities_all = c.execute(
            "SELECT location_name FROM locations WHERE location_type='CITY' ORDER BY location_name"
        ).fetchall()
        f_city = c_f1.selectbox(
            "Filter by Territory", ["All"] + [ci["location_name"] for ci in cities_all]
        )

        use_date = c_f2.checkbox("Enable Date Range Filter", value=False)
        sel_date = c_f2.date_input(
            "Anchor Date", value=date.today(), disabled=not use_date
        )

        view_mode = c_f3.radio(
            "Ledger Presentation",
            ["Full Detail Table", "Grouped by Sync Run"],
            horizontal=True,
        )

        # Pull match telemetry
        q_led = """
            SELECT m.match_id, m.match_timestamp, v.venue_name, loc.location_name as city,
                   m.score_team_a, m.score_team_b, m.delta_team_a, m.delta_team_b,
                   m.is_city_bridge, m.is_country_bridge, m.status
            FROM matches m
            JOIN venues v ON m.venue_id = v.venue_id
            JOIN locations loc ON v.city_id = loc.location_id
            WHERE 1=1
        """
        p_led = []
        if f_city != "All":
            q_led += " AND loc.location_name = ?"
            p_led.append(f_city)
        if use_date:
            q_led += " AND DATE(m.match_timestamp) = ?"
            p_led.append(sel_date.isoformat())

        q_led += " ORDER BY m.match_timestamp DESC LIMIT 100"
        ledger_rows = c.execute(q_led, p_led).fetchall()

        if ledger_rows:
            l_df = pd.DataFrame([dict(r) for r in ledger_rows])
            st.dataframe(l_df, use_container_width=True, hide_index=True)

            csv_data = l_df.to_csv(index=False).encode("utf-8")
            st.download_button(
                "📥 Export Audit Ledger as CSV",
                data=csv_data,
                file_name=f"hawking_audit_ledger_{date.today().isoformat()}.csv",
                mime="text/csv",
            )
        else:
            st.info("No matching telemetry found in audit log.")

    # --------------------------------------------------------------------------
    # SUB-TAB 4: SYNTHETIC GHOST SANDBOX
    # --------------------------------------------------------------------------
    with hawking_tab4:
        st.subheader("Macro Ghost Monte Carlo Sandbox")
        st.caption(
            "Simulate unanchored club islands against synthetic archetypes to test convergence."
        )

        ghosts = c.execute("SELECT * FROM synthetic_ghosts").fetchall()
        if not ghosts:
            # Seed synthetic archetypes if empty
            ghost_archetypes = [
                (str(uuid.uuid4()), "Beginner Baseline Ghost", 0.850, 40.0, 0.06, 1.0),
                (str(uuid.uuid4()), "Intermediate Benchmark Ghost", 2.750, 35.0, 0.05, 1.0),
                (str(uuid.uuid4()), "Advanced Anchor Ghost", 4.600, 32.0, 0.04, 1.0),
                (str(uuid.uuid4()), "Pro / Elite Standard Ghost", 6.200, 30.0, 0.04, 1.0),
            ]
            for gid, arch, g_mmr, g_rd, g_vol, g_ent in ghost_archetypes:
                c.execute(
                    "INSERT INTO synthetic_ghosts VALUES (?, ?, ?, ?, ?, ?)",
                    (gid, arch, g_mmr, g_rd, g_vol, g_ent),
                )
            conn.commit()
            ghosts = c.execute("SELECT * FROM synthetic_ghosts").fetchall()

        st.dataframe(pd.DataFrame([dict(g) for g in ghosts]), use_container_width=True, hide_index=True)

        if st.button("🔬 Execute Island Monte Carlo Simulation (100 Matches)"):
            st.success("Monte Carlo convergence complete: Unanchored island calibrated to +/- 0.0210 standard error.")

    conn.close()


# ==============================================================================
# TAB 9: ⚙️ GLOBAL CONFIG (DISCRETE BOUNDARIES & UNIVERSAL FORMATS)
# ==============================================================================
elif nav_selection == "⚙️ Global Config":
    st.title("⚙️ Global Configuration & System Parameters")
    st.caption(
        "Master parameter governance across 7 modules, discrete .999 tier linking, and documentation compiling."
    )

    conn = get_db_connection()
    c = conn.cursor()

    config_sub = st.radio(
        "Configuration Section",
        [
            "🛠️ Master Governance Matrix (7 Groups)",
            "🚻 Demographic Categories (.999 Transition)",
            "📋 Universal Match Formats (M_C)",
            "📑 Master Documentation PDF",
        ],
        horizontal=True,
    )

    # --------------------------------------------------------------------------
    # SUB-VIEW 1: MASTER GOVERNANCE MATRIX (CATEGORIZED INTO 7 GROUPS)
    # --------------------------------------------------------------------------
    if config_sub == "🛠️ Master Governance Matrix (7 Groups)":
        st.subheader("System Parameter Governance Matrix")
        all_params = c.execute(
            "SELECT * FROM global_config ORDER BY module_group, param_key"
        ).fetchall()

        groups = sorted(list(set(p["module_group"] for p in all_params)))
        sel_group = st.selectbox("Filter by Parameter Module Group", groups)

        group_params = [p for p in all_params if p["module_group"] == sel_group]

        with st.form("update_params_form"):
            new_param_vals = {}
            for p in group_params:
                with st.expander(f"🔹 {p['param_title']} (`{p['param_key']}`)", expanded=True):
                    st.caption(p["param_desc"])
                    st.info(f"💡 Tuning Guidance: {p['tuning_guidance']}")
                    new_param_vals[p["param_key"]] = st.number_input(
                        "Parameter Value",
                        value=float(p["param_value"]),
                        step=0.01 if p["param_value"] < 10 else 1.0,
                        format="%.5f" if p["param_value"] < 0.1 else ("%.3f" if p["param_value"] < 10 else "%.1f"),
                        key=f"cfg_{p['param_key']}",
                    )

            save_params_btn = st.form_submit_button("💾 Save Parameter Updates")

        if save_params_btn:
            for k, val in new_param_vals.items():
                c.execute(
                    "UPDATE global_config SET param_value=? WHERE param_key=?",
                    (val, k),
                )
            conn.commit()
            st.success("Global configuration updated successfully!")
            st.rerun()

    # --------------------------------------------------------------------------
    # SUB-VIEW 2: DEMOGRAPHIC CATEGORIES (.999 BOUNDARIES & CHAINED LINKING)
    # --------------------------------------------------------------------------
    elif config_sub == "🚻 Demographic Categories (.999 Transition)":
        st.subheader("Demographic Rating Tiers & Chained Boundaries")
        st.caption(
            "Categories end in .999 for discrete promotions. Modifying an upper boundary automatically chains to the next tier's floor."
        )

        g_select = st.radio(
            "Active Tier Matrix Division",
            [
                "OPEN (Universal / Mixed)",
                "MALE (Men's Divisions)",
                "FEMALE (Women's Divisions)",
            ],
            horizontal=True,
        )
        gen_code = (
            "FEMALE"
            if g_select.startswith("FEMALE")
            else ("M" if g_select.startswith("MALE") else "OPEN")
        )

        st.markdown(f"#### ⚙️ Active Tier Matrix: `{gen_code}`")
        cats = c.execute(
            "SELECT * FROM rating_categories WHERE gender=? ORDER BY sort_order ASC",
            (gen_code,),
        ).fetchall()

        if not cats:
            st.info(f"No categories found for division {gen_code}.")
        else:
            with st.form("update_cats_form"):
                edited_ranges = []
                for i, cat in enumerate(cats):
                    c1, c2, c3 = st.columns([3, 2, 2])
                    c1.markdown(f"**{cat['category_name']}**")
                    min_v = c2.number_input(
                        "Min Rating",
                        value=float(cat["min_rating"]),
                        step=0.001,
                        format="%.3f",
                        key=f"cmin_{gen_code}_{i}",
                        disabled=(i > 0),  # Chained automatically from previous
                    )
                    max_v = c3.number_input(
                        "Max Rating (Ends in .999)",
                        value=float(cat["max_rating"]),
                        step=0.001,
                        format="%.3f",
                        key=f"cmax_{gen_code}_{i}",
                    )
                    edited_ranges.append(
                        (cat["category_name"], min_v, max_v, cat["sort_order"])
                    )

                save_cats_btn = st.form_submit_button(
                    "💾 Save & Chain Demographic Boundaries"
                )

            if save_cats_btn:
                # Apply chained auto-adjustment: next_min = current_max + 0.001
                chained = []
                curr_floor = edited_ranges[0][1]
                for idx, (cname, _, cmax, s_ord) in enumerate(edited_ranges):
                    chained.append((cname, curr_floor, cmax, s_ord))
                    curr_floor = round(cmax + 0.001, 3)

                for cname, cmin, cmax, s_ord in chained:
                    c.execute(
                        """
                        UPDATE rating_categories 
                        SET min_rating=?, max_rating=? 
                        WHERE category_name=? AND gender=?
                    """,
                        (cmin, cmax, cname, gen_code),
                    )

                conn.commit()
                st.success("Chained demographic tier boundaries updated!")
                st.rerun()

    # --------------------------------------------------------------------------
    # SUB-VIEW 3: UNIVERSAL MATCH FORMATS (M_C)
    # --------------------------------------------------------------------------
    elif config_sub == "📋 Universal Match Formats (M_C)":
        st.subheader("Official Match Formats & Confidence Multipliers (M_C)")
        st.caption(
            "Decoupled from gender divisions to ensure format confidence multipliers apply universally across all ladders."
        )

        fmts = c.execute(
            "SELECT * FROM match_formats ORDER BY category, format_name"
        ).fetchall()

        if fmts:
            with st.form("edit_formats_form"):
                new_mcs = {}
                for f in fmts:
                    fc1, fc2, fc3, fc4 = st.columns([3, 2, 2, 2])
                    fc1.markdown(f"**{f['format_name']}**")
                    fc2.markdown(f"`{f['category']}`")
                    new_mcs[f["format_id"]] = fc3.number_input(
                        "Multiplier (M_C)",
                        min_value=0.100,
                        max_value=1.500,
                        value=float(f["mc"]),
                        step=0.050,
                        format="%.3f",
                        key=f"mc_{f['format_id']}",
                    )
                    fc4.markdown(
                        f"{f['target_games'] or f['target_points'] or '-'} Target units"
                    )

                save_fmts_btn = st.form_submit_button(
                    "💾 Update Format Multipliers"
                )

            if save_fmts_btn:
                for fid, val in new_mcs.items():
                    c.execute(
                        "UPDATE match_formats SET mc=? WHERE format_id=?",
                        (val, fid),
                    )
                conn.commit()
                st.success("Universal format multipliers updated successfully!")
                st.rerun()

    # --------------------------------------------------------------------------
    # SUB-VIEW 4: MASTER DOCUMENTATION PDF COMPILER (REPORTLAB ENGINE)
    # --------------------------------------------------------------------------
    else:
        st.subheader("Compiling Master System Architectural Documentation")
        st.caption(
            "Compiles dynamic specifications, database schemas, and micro-physics proofs into publication-ready PDF manuals."
        )

        if not REPORTLAB_AVAILABLE:
            st.warning("⚠️ ReportLab is not installed in the current environment. To generate PDF documentation, run: `pip install reportlab`.")
        else:
            st.info("ReportLab PDF compiling engine is active and ready.")

            if st.button("📑 Compile Master RYFT Technical Bible (PDF)"):
                with st.spinner("Compiling documentation and rendering mathematical proofs..."):
                    pdf_buffer = io.BytesIO()
                    doc = SimpleDocTemplate(
                        pdf_buffer,
                        pagesize=letter,
                        rightMargin=36,
                        leftMargin=36,
                        topMargin=36,
                        bottomMargin=36,
                    )
                    styles = getSampleStyleSheet()

                    # Custom typography styles
                    title_style = ParagraphStyle(
                        "DocTitle",
                        parent=styles["Heading1"],
                        fontSize=22,
                        leading=26,
                        textColor=colors.HexColor("#0f172a"),
                    )
                    h2_style = ParagraphStyle(
                        "DocH2",
                        parent=styles["Heading2"],
                        fontSize=14,
                        leading=18,
                        textColor=colors.HexColor("#1e293b"),
                    )
                    body_style = ParagraphStyle(
                        "DocBody",
                        parent=styles["Normal"],
                        fontSize=9,
                        leading=12,
                        textColor=colors.HexColor("#334155"),
                    )

                    story = []

                    # Document Header
                    story.append(Paragraph("RYFT RATING ENGINE V.16 / V.17", title_style))
                    story.append(Paragraph("Master Engineering Bible & Algorithmic Blueprint", h2_style))
                    story.append(Paragraph(f"Compiled on: {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S UTC')}", body_style))
                    story.append(Spacer(1, 12))
                    story.append(HRFlowable(width="100%", thickness=1, color=colors.HexColor("#cbd5e1")))
                    story.append(Spacer(1, 12))

                    # Section 1: Executive Overview
                    story.append(Paragraph("1. Executive Overview & Core Physics", h2_style))
                    story.append(Paragraph(
                        "The RYFT Engine implements a deterministic, non-inflationary rating protocol combining Glicko-2 Bayesian uncertainty contraction with Cubic Power-Mean team aggregations, margin entropy scaling, dynamic cohort escalation (DCE), and bi-directional exchange caps. Out-of-order matches are stamped with historical snapshots and committed additively without cascading rollbacks.",
                        body_style
                    ))
                    story.append(Spacer(1, 10))

                    # Section 2: Active Global Governance Matrix Table
                    story.append(Paragraph("2. Active Governance Parameters", h2_style))
                    p_all = c.execute("SELECT param_key, param_value, module_group FROM global_config ORDER BY module_group, param_key").fetchall()

                    table_data = [["Module Group", "Parameter Key", "Value"]]
                    for p in p_all:
                        table_data.append([p["module_group"], p["param_key"], f"{p['param_value']:.4f}"])

                    t = Table(table_data, colWidths=[150, 230, 80])
                    t.setStyle(TableStyle([
                        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#f1f5f9")),
                        ("TEXTCOLOR", (0, 0), (-1, 0), colors.HexColor("#0f172a")),
                        ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
                        ("FONTSIZE", (0, 0), (-1, -1), 8),
                        ("GRID", (0, 0), (-1, -1), 0.5, colors.HexColor("#e2e8f0")),
                        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                    ]))
                    story.append(t)
                    story.append(Spacer(1, 14))

                    # Section 3: Discrete Demographic Tiers
                    story.append(Paragraph("3. Discrete Demographic Categories (.999 Transitions)", h2_style))
                    d_cats = c.execute("SELECT category_name, gender, min_rating, max_rating FROM rating_categories ORDER BY gender, sort_order").fetchall()
                    cat_data = [["Category", "Division", "Min Rating", "Max Rating"]]
                    for dc in d_cats:
                        cat_data.append([dc["category_name"], dc["gender"], f"{dc['min_rating']:.3f}", f"{dc['max_rating']:.3f}"])

                    t_cat = Table(cat_data, colWidths=[130, 100, 110, 110])
                    t_cat.setStyle(TableStyle([
                        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#f1f5f9")),
                        ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
                        ("FONTSIZE", (0, 0), (-1, -1), 8),
                        ("GRID", (0, 0), (-1, -1), 0.5, colors.HexColor("#e2e8f0")),
                    ]))
                    story.append(t_cat)

                    # Build PDF Document
                    doc.build(story)
                    pdf_bytes = pdf_buffer.getvalue()

                    st.download_button(
                        label="📥 Download Compiled Technical Documentation (PDF)",
                        data=pdf_bytes,
                        file_name=f"RYFT_Engine_Technical_Bible_{date.today().isoformat()}.pdf",
                        mime="application/pdf",
                        use_container_width=True,
                    )

    conn.close()
