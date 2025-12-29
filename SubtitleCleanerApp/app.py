import streamlit as st
import pandas as pd
import re
import os

# --- Page title ---
st.title("Script Converter")
st.write("Upload an Excel file. The app will clean, rebuild full sentences, and provide a downloadable Excel file.\nThe Excel file must include 'Start Time', 'End Time', 'Duration' and 'Subtitle Text' columns.")

# --- File upload ---
uploaded_file = st.file_uploader("Choose an Excel file (.xlsx)", type="xlsx")

if uploaded_file:
    # Read Excel
    df = pd.read_excel(uploaded_file)

    # --- Split on newline and explode ---
    df["subtitle_text_split"] = df["Subtitle Text"].str.split("\n")
    df_new = df.explode("subtitle_text_split")
    df_new["subtitle_text_split"] = df_new["subtitle_text_split"].str.strip()

    # --- Extract speaker (capitalised word ending with colon) ---
    df_new["Character"] = df_new["subtitle_text_split"].str.extract(
        r"^([A-Z][a-zA-Z0-9]*(?:\s+[A-Z][a-zA-Z0-9]*)*):"
    )
    df_new["Character"] = df_new["Character"].ffill()

    # --- Add row index ---
    df_new = df_new.reset_index(drop=True)
    df_new["row_id"] = df_new.index

    # --- Identify change in speaker ---
    df_new["speaker_change"] = df_new["Character"].ne(df_new["Character"].shift())
    df_new["speaker_block"] = df_new["speaker_change"].cumsum()

    # --- Clean subtitle text ---
    df_new["line_text"] = (
        df_new["subtitle_text_split"]
        .str.replace("_x000D_", "", regex=False)
        .str.replace('"', "", regex=False)
        .str.replace("\r", "", regex=False)
        .str.strip()
        .str.replace(
        r"^([A-Z][a-zA-Z0-9]*(?:\s+[A-Z][a-zA-Z0-9]*)*):",
        "",
        regex=True
        )
    )

    # --- Regex to identify end of sentence ---
    SENTENCE_REGEX = re.compile(r".*?[.!?](?:['\"\)\]]*)(?=\s|$)")

    # --- Function to rebuild sentences ---
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
                    "End Time": row["End Time"],
                    "Duration": row["Duration"],
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
                "End Time": block.iloc[-1]["End Time"],
                "Duration": block.iloc[-1]["Duration"],
            })

        return pd.DataFrame(rows)

    # --- Process button ---
    if st.button("Process Subtitles"):
        df_final = (
            df_new
            .groupby("speaker_block", group_keys=False)
            .apply(rebuild_block, include_groups=False)
            .reset_index(drop=True)
        )

        # --- Group by to reduce lines ---
        # Create a group identifier for consecutive runs of same character + timestamp
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
                'Duration': 'first'  # or recalc if needed
            })
        )
        df_final = df_final.drop(columns='merge_group')

        # --- Add row count ---
        df_final["Row Count"] = range(1, len(df_final) + 1)

        # --- Add word count ---
        df_final["Word Count"] = df_final["Subtitle Text"].str.replace(
            r"^[A-Z][a-zA-Z]+:\s*", "", regex=True
        ).str.split().str.len()

        # --- Reorder fields ---
        df_final = df_final[
            ["Row Count", "Character", "Subtitle Text", "Start Time", "End Time", "Duration", "Word Count"]
        ]

        st.success("Subtitles processed successfully!")

        # --- Show preview ---
        st.write("Preview of cleaned subtitles:")
        st.dataframe(df_final.head(10))

        # --- Prepare downloadable Excel ---
        input_name = uploaded_file.name
        base_name, ext = os.path.splitext(input_name)
        output_name = f"{base_name}-processed{ext}"
        df_final.to_excel(output_name, index=False)

        st.download_button(
            label="Download Cleaned Excel",
            data=open(output_name, "rb").read(),
            file_name=output_name,
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
        )
