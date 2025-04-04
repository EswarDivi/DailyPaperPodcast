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
import boto3
from datetime import datetime
from dotenv import load_dotenv
import schedule

load_dotenv()

PODCAST_FILE = "daily_podcast.mp3"
SHOW_NOTES_FILE = "daily_show_notes.txt"
CONVERSATION_FILE = "daily_conversation.json"
LAST_RUN_FILE = "last_run.txt"

# Cloudflare R2 Configuration
R2_ACCESS_KEY = os.getenv("R2_ACCESS_KEY")
R2_SECRET_KEY = os.getenv("R2_SECRET_KEY")
R2_BUCKET_NAME = os.getenv("R2_BUCKET_NAME")
R2_ENDPOINT_URL = os.getenv("R2_ENDPOINT_URL")

s3_client = boto3.client(
    's3',
    aws_access_key_id=R2_ACCESS_KEY,
    aws_secret_access_key=R2_SECRET_KEY,
    endpoint_url=R2_ENDPOINT_URL
)

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

def extract_conversation(text, items):
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

def generate_show_notes(items):
    notes = "**Show Notes**\n\nIn today's episode:\n\n"
    for i, item in enumerate(items):
        notes += f"{i+1}. **{item['title']}**\n   - {item['description']}\n   - [Read More]({item['link']})\n\n"
    return notes

async def generate_audio_parallel(conversation):
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

def merge_audio_files(audio_files):
    combined = AudioSegment.empty()
    for file in audio_files:
        audio = AudioSegment.from_file(file)
        combined += audio
    combined.export(PODCAST_FILE, format="mp3")

def upload_to_r2(filename, bucket_name):
    s3_client.upload_file(filename, bucket_name, os.path.basename(filename))
    print(f"Uploaded {filename} to {bucket_name}")

def save_to_file(content, filename):
    with open(filename, "w") as file:
        file.write(content)

def run_podcast_generation():
    if os.path.exists(LAST_RUN_FILE):
        with open(LAST_RUN_FILE, "r") as file:
            last_run_date = file.read().strip()
        if last_run_date == datetime.today().strftime("%Y-%m-%d"):
            print("Podcast already generated today. Skipping...")
            return
    
    print("Generating podcast...")
    conversation = extract_conversation(daily_feed, items)
    save_to_file(json.dumps(conversation, indent=2), CONVERSATION_FILE)
    save_to_file(generate_show_notes(items), SHOW_NOTES_FILE)

    print("Generating audio...")
    audio_files = asyncio.run(generate_audio_parallel(conversation))
    merge_audio_files(audio_files)
    for file in audio_files:
        os.remove(file)

    with open(LAST_RUN_FILE, "w") as file:
        file.write(datetime.today().strftime("%Y-%m-%d"))

    upload_to_r2(PODCAST_FILE, R2_BUCKET_NAME)
    upload_to_r2(SHOW_NOTES_FILE, R2_BUCKET_NAME)
    upload_to_r2(CONVERSATION_FILE, R2_BUCKET_NAME)
    print("Podcast and show notes uploaded successfully.")


if __name__ == "__main__":
    run_podcast_generation()
    # Uncomment the following lines to enable daily scheduling
    # schedule.every().day.at("23:15").do(run_podcast_generation)
    # while True:
    #     schedule.run_pending()
    #     time.sleep(60)