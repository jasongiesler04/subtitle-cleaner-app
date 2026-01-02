import streamlit as st
import pandas as pd
import re
import os

# --- Page title ---
st.title("Script Converter")
st.write("Upload an Excel file. The app will clean, rebuild full sentences, and provide a downloadable Excel file.\nThe Excel file must include 'Start Time', 'End Time', 'Duration' and 'Subtitle Text' columns.")

# -------------------------------------------------
# Regex patterns
# -------------------------------------------------
SPEAKER_REGEX = r"([A-Z][a-zA-Z0-9]*(?:\s+[A-Z][a-zA-Z0-9]*)*):"
SENTENCE_SPLIT_REGEX = r'(?:\n|(?<=[.!?])\s+)'
SENTENCE_REGEX = re.compile(r".*?[.!?](?:['\"\)\]]*)(?=\s|$)")

# -------------------------------
# Upload Excel
# -------------------------------
uploaded_file = st.file_uploader("Upload Excel file", type=["xlsx"])
if uploaded_file is not None:

    # Only process once and store in session_state
    if "df_final" not in st.session_state:

        df = pd.read_excel(uploaded_file)
        input_name = uploaded_file.name
        base_name, ext = os.path.splitext(input_name)

        # -------------------------------
        # 1. Split lines and clean text
        # -------------------------------
        # Split new line
        df["subtitle_text_split"] = df["Subtitle Text"].str.split("\n")
        df_new = df.explode("subtitle_text_split")

        # Split sentences
        df_new["subtitle_text_split"] = df_new["subtitle_text_split"].str.split(SENTENCE_SPLIT_REGEX)
        df_new = df_new.explode("subtitle_text_split")

        # Clean new sentence text
        df_new["subtitle_text_split"] = df_new["subtitle_text_split"].str.strip()
        df_new = df_new[df_new["subtitle_text_split"].notna() &
                        df_new["subtitle_text_split"].ne("")]

        # Extract speaker (allow multi-word like 'Man VO')
        df_new["Character"] = df_new["subtitle_text_split"].str.extract(SPEAKER_REGEX)
        df_new["Character"] = df_new["Character"].ffill()

        # Clean subtitle text
        df_new["line_text"] = (
            df_new["subtitle_text_split"]
            .str.replace("_x000D_", "", regex=False)
            .str.replace('"', "", regex=False)
            .str.replace("\r", "", regex=False)
            .str.strip()
            .str.replace(SPEAKER_REGEX, "", regex=True)
        )

        # -------------------------------
        # 2. Sentence reconstruction
        # -------------------------------
        def rebuild_block(block):
            rows = []
            buffer = ""
            sentence_start_time = None
            speaker_written = False

            for _, row in block.iterrows():
                text = row["line_text"]
                if not text:
                    continue

                if buffer == "":
                    sentence_start_time = row["Start Time"]
                    sentence_end_time = row["End Time"]
                    sentence_duration = row["Duration"]

                buffer += (" " if buffer else "") + text

                while True:
                    match = SENTENCE_REGEX.match(buffer)
                    if not match:
                        break

                    sentence = match.group().strip()
                    if not speaker_written:
                        sentence = f"{row['Character']}: {sentence}"
                        speaker_written = True

                    rows.append({
                        "Character": row["Character"],
                        "Subtitle Text": sentence,
                        "Start Time": sentence_start_time,
                        "End Time": sentence_end_time,
                        "Duration": sentence_duration,
                    })

                    buffer = buffer[len(match.group()):].strip()
                    sentence_start_time = row["Start Time"]

            # Flush remainder
            if buffer:
                sentence = buffer.strip()
                if not speaker_written:
                    sentence = f"{block.iloc[0]['Character']}: {sentence}"

                rows.append({
                    "Character": block.iloc[0]["Character"],
                    "Subtitle Text": sentence,
                    "Start Time": sentence_start_time,
                    "End Time": sentence_end_time,
                    "Duration": sentence_duration,
                })

            return pd.DataFrame(rows)

        df_final = (
            df_new
            .groupby(df_new['Character'].ne(df_new['Character'].shift()).cumsum(), group_keys=False)
            .apply(rebuild_block)
            .reset_index(drop=True)
        )

        # -------------------------------
        # 3. Merge consecutive same-character & timestamp rows
        # -------------------------------
        df_final['merge_group'] = (
            (df_final['Character'] != df_final['Character'].shift()) |
            (df_final['Start Time'] != df_final['Start Time'].shift())
        ).cumsum()

        df_final = (
            df_final
            .groupby('merge_group', as_index=False, sort=False)
            .agg({
                'Character': 'first',
                'Subtitle Text': lambda x: ' '.join(x),
                'Start Time': 'first',
                'End Time': 'last',
                'Duration': 'first'
            })
        ).drop(columns='merge_group')

        # -------------------------------
        # 4. Add row count and word count
        # -------------------------------
        df_final["Index"] = range(1, len(df_final) + 1)

        df_final["Word Count"] = df_final["Subtitle Text"].str.replace(
            r"^[A-Z][a-zA-Z]+:\s*", "", regex=True
        ).str.split().str.len()

        # -------------------------------
        # 5. Reorder columns
        # -------------------------------
        columns_order = ["Index", "Character", "Subtitle Text", "Start Time", "End Time", "Duration", "Word Count"]
        df_final = df_final[columns_order]

        # -------------------------------
        # Store results in session_state
        # -------------------------------
        st.session_state.df_final = df_final
        st.session_state.base_name = base_name

    # Retrieve from session_state
    df_final = st.session_state.df_final
    base_name = st.session_state.base_name

    # Success message
    st.success("Subtitles processed successfully!")

    # --- Show preview ---
    st.write("Preview of cleaned subtitles:")
    st.dataframe(df_final.set_index("Index").head(10))

    # -------------------------------
    # 6. Prepare Excel download
    # -------------------------------
    excel_file_name = f"{base_name}-processed.xlsx"
    df_final.to_excel(excel_file_name, index=False, engine="openpyxl")
    with open(excel_file_name, "rb") as f:
        excel_data = f.read()

    st.download_button(
        label="Download Excel",
        data=excel_data,
        file_name=excel_file_name,
        mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    )

    # -------------------------------
    # 7. Prepare SRT download
    # -------------------------------
    def format_srt_time(ts):
        if isinstance(ts, str):
            ts = ts.replace('.', ',')
            if ',' not in ts:
                ts += ',000'
            return ts
        else:
            total_ms = int(ts * 1000)
            hours = total_ms // 3600000
            minutes = (total_ms % 3600000) // 60000
            seconds = (total_ms % 60000) // 1000
            milliseconds = total_ms % 1000
            return f"{hours:02d}:{minutes:02d}:{seconds:02d},{milliseconds:03d}"


    srt_df = df_final.sort_values(by=["Start Time", "Index"], ascending=[True, False])

    srt_lines = []
    for i, row in enumerate(srt_df.itertuples(), 1):
        start = format_srt_time(row._4)  # Start Time
        end = format_srt_time(row._5)  # End Time
        text = row._3  # Subtitle Text
        srt_lines.append(f"{i}\n{start} --> {end}\n{text}\n\n")

    srt_file_name = f"{base_name}.srt"
    with open(srt_file_name, "w", encoding="utf-8") as f:
        f.writelines(srt_lines)

    with open(srt_file_name, "rb") as f:
        srt_data = f.read()

    st.download_button(
        label="Download SRT",
        data=srt_data,
        file_name=srt_file_name,
        mime="text/plain"
    )