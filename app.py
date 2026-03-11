import streamlit as st
import pandas as pd
import json
import re
from io import StringIO
from datetime import datetime
from docx import Document
import altair as alt

# =========================================================
# CONFIG
# =========================================================
REQUIRED_FIELDS = ["timestamp", "system_id", "panel_home_id", "event_type"]
TIMESTAMP_FORMAT = "%Y-%m-%d %H:%M"  # e.g. 2024-03-11 14:30

# =========================================================
# LOGIN SYSTEM
# =========================================================
def login_screen():
    st.title("🔐 Audit Trail Login")
    st.write("Enter your credentials to access the Audit Trail Dashboard.")

    username = st.text_input("Username")
    password = st.text_input("Password", type="password")

    if st.button("Login"):
        if username == "admin" and password == "1234":
            st.session_state["logged_in"] = True
        else:
            st.error("Invalid username or password")

if "logged_in" not in st.session_state:
    st.session_state["logged_in"] = False

if not st.session_state["logged_in"]:
    login_screen()
    st.stop()

# =========================================================
# PARSING HELPERS
# =========================================================
def parse_timestamp(value):
    if pd.isna(value):
        return None
    if isinstance(value, datetime):
        return value
    try:
        return datetime.strptime(str(value).strip(), TIMESTAMP_FORMAT)
    except Exception:
        return None

def normalize_columns(df):
    # Lowercase and strip column names
    df = df.rename(columns={c: c.strip().lower() for c in df.columns})
    return df

def ensure_core_columns(df):
    for col in REQUIRED_FIELDS + ["user_id", "details", "source_file"]:
        if col not in df.columns:
            df[col] = None
    return df

# ---------------- CSV ----------------
def parse_csv(file, source_name):
    df = pd.read_csv(file)
    df = normalize_columns(df)
    df = ensure_core_columns(df)
    df["source_file"] = source_name
    return df

# ---------------- JSON ----------------
def parse_json(file, source_name):
    data = json.load(file)
    # Expect list of events or single dict
    if isinstance(data, dict):
        data = [data]
    df = pd.json_normalize(data)
    df = normalize_columns(df)
    df = ensure_core_columns(df)
    df["source_file"] = source_name
    return df

# ---------------- DOCX ----------------
def parse_docx(file, source_name):
    doc = Document(file)
    events = []

    # Very simple heuristic: each non-empty paragraph is an event line
    for p in doc.paragraphs:
        text = p.text.strip()
        if not text:
            continue

        # Try to extract fields via simple patterns
        # Example expected pattern (flexible): 
        # "2024-03-11 14:30 | PM7 | HOME123 | login | user: jdoe | details..."
        parts = [x.strip() for x in text.split("|")]
        event = {
            "timestamp": None,
            "system_id": None,
            "panel_home_id": None,
            "event_type": None,
            "user_id": None,
            "details": text,
            "source_file": source_name,
        }

        if len(parts) >= 4:
            event["timestamp"] = parts[0]
            event["system_id"] = parts[1]
            event["panel_home_id"] = parts[2]
            event["event_type"] = parts[3]
        # Try to find user in remaining parts
        for part in parts[4:]:
            if part.lower().startswith("user:"):
                event["user_id"] = part.split(":", 1)[1].strip()

        events.append(event)

    df = pd.DataFrame(events)
    df = normalize_columns(df)
    df = ensure_core_columns(df)
    return df

# ---------------- TXT / LOG ----------------
TIMESTAMP_REGEX = r"\d{4}-\d{2}-\d{2} \d{2}:\d{2}"

def parse_txt(file, source_name):
    content = file.read().decode("utf-8", errors="ignore")
    lines = content.splitlines()
    events = []

    for line in lines:
        text = line.strip()
        if not text:
            continue

        # Find timestamp
        ts_match = re.search(TIMESTAMP_REGEX, text)
        ts = ts_match.group(0) if ts_match else None

        # Very naive extraction for system, panel_home, event_type
        # Expect patterns like "system=PM7", "home=HOME123", "event=login"
        system = None
        home = None
        etype = None
        user = None

        for token in text.split():
            lower = token.lower()
            if lower.startswith("system="):
                system = token.split("=", 1)[1]
            elif lower.startswith("home=") or lower.startswith("panel_home="):
                home = token.split("=", 1)[1]
            elif lower.startswith("event="):
                etype = token.split("=", 1)[1]
            elif lower.startswith("user="):
                user = token.split("=", 1)[1]

        events.append(
            {
                "timestamp": ts,
                "system_id": system,
                "panel_home_id": home,
                "event_type": etype,
                "user_id": user,
                "details": text,
                "source_file": source_name,
            }
        )

    df = pd.DataFrame(events)
    df = normalize_columns(df)
    df = ensure_core_columns(df)
    return df

# =========================================================
# VALIDATION
# =========================================================
def validate_events(df):
    df = df.copy()
    # Parse timestamps
    df["parsed_timestamp"] = df["timestamp"].apply(parse_timestamp)

    # Required fields present and non-null
    df["valid_required"] = True
    for col in REQUIRED_FIELDS:
        df["valid_required"] &= df[col].notna() & (df[col].astype(str).str.strip() != "")

    # Timestamp valid
    df["valid_timestamp"] = df["parsed_timestamp"].notna()

    # Overall validity
    df["is_valid"] = df["valid_required"] & df["valid_timestamp"]
    return df

# =========================================================
# MAIN APP
# =========================================================
st.set_page_config(
    page_title="Audit Trail Dashboard",
    layout="wide",
    initial_sidebar_state="expanded"
)

st.sidebar.header("📂 Upload Audit Logs")
uploaded_files = st.sidebar.file_uploader(
    "Upload log files (CSV, JSON, DOCX, TXT)",
    type=["csv", "json", "docx", "txt", "log"],
    accept_multiple_files=True,
)
process_btn = st.sidebar.button("Process Logs")

st.title("📜 Audit Trail Dashboard")
st.write("Upload mixed-format audit logs, normalize events, validate required fields, and explore visual reports.")

st.markdown("---")

if uploaded_files and process_btn:
    all_events = []

    for f in uploaded_files:
        name = f.name.lower()
        try:
            if name.endswith(".csv"):
                df = parse_csv(f, f.name)
            elif name.endswith(".json"):
                df = parse_json(f, f.name)
            elif name.endswith(".docx"):
                df = parse_docx(f, f.name)
            elif name.endswith(".txt") or name.endswith(".log"):
                df = parse_txt(f, f.name)
            else:
                st.warning(f"Unsupported file type: {f.name}")
                continue

            all_events.append(df)
        except Exception as e:
            st.error(f"Error parsing {f.name}: {e}")

    if not all_events:
        st.warning("No events parsed from uploaded files.")
        st.stop()

    events = pd.concat(all_events, ignore_index=True)
    events = validate_events(events)

    # ---------------- SUMMARY ----------------
    st.header("📊 Summary")
    total_events = len(events)
    valid_events = events["is_valid"].sum()
    invalid_events = total_events - valid_events

    col1, col2, col3 = st.columns(3)
    col1.metric("Total events", total_events)
    col2.metric("Valid events", valid_events)
    col3.metric("Invalid events", invalid_events)

    st.markdown("### Validation details")
    st.write("Events marked as invalid are missing required fields or have invalid timestamps.")
    st.dataframe(events[["timestamp", "system_id", "panel_home_id", "event_type", "user_id", "source_file", "is_valid"]])

    # Filter to valid events for visuals
    valid_df = events[events["is_valid"]].copy()
    if valid_df.empty:
        st.warning("No valid events available for visualization.")
        st.stop()

    # ---------------- FILTERS ----------------
    st.markdown("---")
    st.header("🔎 Filters")

    systems = ["(All)"] + sorted(valid_df["system_id"].dropna().unique().tolist())
    homes = ["(All)"] + sorted(valid_df["panel_home_id"].dropna().unique().tolist())
    types = ["(All)"] + sorted(valid_df["event_type"].dropna().unique().tolist())

    c1, c2, c3 = st.columns(3)
    sel_system = c1.selectbox("System", systems)
    sel_home = c2.selectbox("Panel Home", homes)
    sel_type = c3.selectbox("Event Type", types)

    filtered = valid_df.copy()
    if sel_system != "(All)":
        filtered = filtered[filtered["system_id"] == sel_system]
    if sel_home != "(All)":
        filtered = filtered[filtered["panel_home_id"] == sel_home]
    if sel_type != "(All)":
        filtered = filtered[filtered["event_type"] == sel_type]

    # ---------------- VISUALS ----------------
    st.markdown("---")
    st.header("📈 Visual Reports")

    # Ensure parsed_timestamp is datetime
    filtered["parsed_timestamp"] = pd.to_datetime(filtered["parsed_timestamp"])

    # Events over time
    st.subheader("Events over time")
    time_df = filtered.sort_values("parsed_timestamp")
    if not time_df.empty:
        chart = (
            alt.Chart(time_df)
            .mark_line(point=True)
            .encode(
                x="parsed_timestamp:T",
                y="count():Q",
                color="system_id:N",
                tooltip=["parsed_timestamp:T", "system_id:N", "count():Q"],
            )
            .properties(height=300)
        )
        st.altair_chart(chart, use_container_width=True)
    else:
        st.info("No events to display in timeline.")

    # Events by system
    st.subheader("Events by system")
    sys_df = filtered.groupby("system_id").size().reset_index(name="count")
    if not sys_df.empty:
        chart_sys = (
            alt.Chart(sys_df)
            .mark_bar()
            .encode(
                x="system_id:N",
                y="count:Q",
                tooltip=["system_id:N", "count:Q"],
            )
            .properties(height=300)
        )
        st.altair_chart(chart_sys, use_container_width=True)
    else:
        st.info("No system data to display.")

    # Events by type
    st.subheader("Events by type")
    type_df = filtered.groupby("event_type").size().reset_index(name="count")
    if not type_df.empty:
        chart_type = (
            alt.Chart(type_df)
            .mark_bar()
            .encode(
                x="event_type:N",
                y="count:Q",
                tooltip=["event_type:N", "count:Q"],
            )
            .properties(height=300)
        )
        st.altair_chart(chart_type, use_container_width=True)
    else:
        st.info("No event-type data to display.")

    # Raw table
    st.markdown("---")
    st.header("📋 Event Table")
    st.dataframe(
        filtered[
            [
                "parsed_timestamp",
                "system_id",
                "panel_home_id",
                "event_type",
                "user_id",
                "details",
                "source_file",
            ]
        ].sort_values("parsed_timestamp")
    )

else:
    st.info("Upload one or more log files and click **Process Logs** to begin.")
