import requests
import json
import os
import xml.etree.ElementTree as ET
import openai
import edge_tts
from pydub import AudioSegment
import re
import time
import asyncio
import streamlit as st
from datetime import datetime
from dotenv import load_dotenv

load_dotenv()

PODCAST_FILE = "merged_audio.mp3"
SHOW_NOTES_FILE = "show_notes.txt"
CONVERSATION_FILE = "conversation.json"
LAST_RUN_FILE = "last_run.txt"

feed_url = "http://papers.takara.ai/api/feed"
response = requests.get(feed_url)
tree = ET.ElementTree(ET.fromstring(response.text))

daily_feed = ""
items = []
for item in tree.iter("item"):
    title = item.find("title").text
    link = item.find("link").text
    description = item.find("description").text
    items.append({"title": title, "link": link, "description": description})
    daily_feed += f"Title: {title.strip()}\nDescription: {description}\n\n"

client = openai.Client(
    api_key=os.getenv("DEEPINFRA_API"),
    base_url="https://api.deepinfra.com/v1/openai",
)



def build_prompt(text, items):
    """Generates a well-balanced podcast conversation covering all research papers, ensuring correct JSON format."""
    paper_summaries = "\n".join(
        [f"- {item['title']} ({item['link']}): {item['description']}" for item in items]
    )

    template = """
    {
        "conversation": [
            {"speaker": "Brian", "text": ""},
            {"speaker": "Jenny", "text": ""}
        ]
    }
    """

    return (
        f"🎙️ Welcome to Daily Papers! Today, we're diving into the latest AI research in an engaging and "
        f"informative discussion. The goal is to make it a **medium-length podcast** that’s **engaging, natural, and insightful** while covering "
        f"the key points of each paper.\n\n"
        f"Here are today's research papers:\n{paper_summaries}\n\n"
        f"Convert this into a **conversational podcast-style discussion** between two experts, Brian and Jenny. "
        f"Ensure the conversation flows naturally, using a mix of **insightful analysis, casual phrasing, and occasional filler words** like 'uhm' and 'you know' "
        f"to keep it realistic. The tone should be engaging yet professional, making it interesting for the audience.\n\n"
        f"Each research paper should be **discussed meaningfully**, but avoid dragging the conversation too long. "
        f"Focus on key insights and practical takeaways. Keep the pacing dynamic and interactive.\n\n"
        f"Please return the conversation in **this exact JSON format**:\n{template}"
    )


def extract_conversation(text, items, max_retries=3):
    """Extracts podcast conversation from OpenAI API with retries."""
    for attempt in range(1, max_retries + 1):
        try:
            print(f"Attempt {attempt} to generate conversation...")
            chat_completion = client.chat.completions.create(
                messages=[{"role": "user", "content": build_prompt(text, items)}],
                model="meta-llama/Llama-3.3-70B-Instruct-Turbo",
                temperature=0.7,
                max_tokens=4096,
            )
            pattern = r"\{(?:[^{}]|(?:\{[^{}]*\}))*\}"
            json_match = re.search(pattern, chat_completion.choices[0].message.content)
            if json_match:
                return json.loads(json_match.group())
            raise ValueError("No valid JSON found in response")
        except Exception as e:
            print(f"Error: {e}")
            if attempt < max_retries:
                time.sleep(2)
            else:
                raise RuntimeError(f"Failed after {max_retries} attempts.")

def generate_show_notes(items):
    """Creates structured show notes summarizing research papers."""
    notes = "**Show Notes**\n\nIn today's episode:\n\n"
    for i, item in enumerate(items):
        notes += f"{i+1}. **{item['title']}**\n   - {item['description']}\n   - [Read More]({item['link']})\n\n"
    return notes

async def generate_audio_parallel(conversation):
    """Generates audio for the podcast in parallel."""
    tasks = []
    audio_files = []

    for i, item in enumerate(conversation["conversation"]):
        text = item["text"]
        output_file = f"audio_{i}.mp3"
        voice = "en-GB-RyanNeural" if item["speaker"] == "Brian" else "en-US-AvaMultilingualNeural"
        
        tasks.append(edge_tts.Communicate(text=text, voice=voice).save(output_file))
        audio_files.append(output_file)

    await asyncio.gather(*tasks)
    return audio_files

def merge_audio_files(audio_files, output_file="merged_audio.mp3"):
    """Merges multiple audio files into one MP3 file."""
    combined = AudioSegment.empty()
    for file in audio_files:
        audio = AudioSegment.from_file(file)
        combined += audio
    combined.export(output_file, format="mp3")
    print(f"Merged audio saved to {output_file}")

def save_to_file(content, filename):
    """Saves content to a file."""
    with open(filename, "w") as file:
        file.write(content)

def load_conversation():
    """Loads conversation from file if exists."""
    if os.path.exists(CONVERSATION_FILE):
        with open(CONVERSATION_FILE, "r", encoding="utf-8") as file:
            return json.load(file)
    return None

async def generate_podcast():
    """Generates podcast content (once per day or on force generate)."""
    if os.path.exists(LAST_RUN_FILE):
        with open(LAST_RUN_FILE, "r") as file:
            last_run_date = file.read().strip()
        if last_run_date == datetime.today().strftime("%Y-%m-%d"):
            print("Podcast already generated today. Skipping...")
            return

    conversation = extract_conversation(daily_feed, items)
    save_to_file(json.dumps(conversation, indent=2), CONVERSATION_FILE)
    save_to_file(generate_show_notes(items), SHOW_NOTES_FILE)

    print("Generating audio...")
    audio_files = await generate_audio_parallel(conversation)
    merge_audio_files(audio_files)

    for file in audio_files:
        os.remove(file)

    with open(LAST_RUN_FILE, "w") as file:
        file.write(datetime.today().strftime("%Y-%m-%d"))

    print("Podcast and show notes generated successfully.")

asyncio.run(generate_podcast())

st.set_page_config(page_title="Daily Papers Podcast", page_icon="🎙️", layout="wide")

st.title("🎙️ Today's Daily Papers Podcast")
st.subheader("Your Daily AI Research Insights - Engaging & Informative")

col1, col2 = st.columns([0.2, 0.8])

with col1:
    st.image("Logo.png", width=120)  # Ensure logo.png is in the working directory

with col2:
    st.markdown(
        """
        **Powered by:**  
        🔗 [HF Daily Papers Feeds](https://github.com/404missinglink/HF-Daily-Papers-Feeds)  
        🔗 [TLDR Takara AI](https://tldr.takara.ai/)  
        🔗 [Takara AI Papers Feed](http://papers.takara.ai/api/feed)  
        """
    )

# Styling Divider
st.markdown("---")
conversation_data = load_conversation()
show_notes = "**No show notes available.**"
if os.path.exists(SHOW_NOTES_FILE):
    with open(SHOW_NOTES_FILE, "r", encoding="utf-8") as f:
        show_notes = f.read()

col1, col2 = st.columns([2, 1])

with col1:
    st.subheader("🎧 Listen to the Podcast")
    if os.path.exists(PODCAST_FILE):
        audio_bytes = open(PODCAST_FILE, "rb").read()
        st.audio(audio_bytes, format="audio/mp3")
    else:
        st.warning("No podcast available. Please generate an episode.")

    st.subheader("📜 Show Notes")
    st.markdown(show_notes)

with col2:
    st.subheader("🗨️ Podcast Conversation")
    if conversation_data:
        for msg in conversation_data["conversation"]:
            st.write(f"**{msg['speaker']}**: {msg['text']}")
    else:
        st.warning("No conversation data available.")

st.markdown("---")

if st.button("🔄 Force Generate Podcast"):
    asyncio.run(generate_podcast())
    st.rerun()

st.markdown("📢 **Stay tuned for more AI research insights!**")
