import os
import torch
from transformers import WhisperProcessor, WhisperForConditionalGeneration, Trainer, TrainingArguments, DataCollatorForSeq2Seq
from datasets import Dataset
import pandas as pd
from pathlib import Path
import librosa
import numpy as np
import json

def parse_ultrastar(file_path):
    """Parse Ultrastar .txt file to extract lyrics and timings."""
    lyrics = []
    with open(file_path, 'r', encoding='utf-8', errors='replace') as f:
        for line in f:
            line = line.strip()
            if line.startswith(':'):
                parts = line.split()
                if len(parts) >= 5:  # : start pitch length syllable...
                    start = int(parts[1])
                    pitch = int(parts[2])
                    length = int(parts[3])
                    syllable = ' '.join(parts[4:])
                    lyrics.append({
                        'start': start / 1000,  # to seconds
                        'end': (start + length) / 1000,
                        'text': syllable
                    })
    return lyrics

def load_training_data(data_dir):
    """Load training data from folder."""
    data = []
    for txt_file in Path(data_dir).rglob('*.txt'):
        mp3_file = txt_file.with_suffix('.mp3')
        json_file = txt_file.with_suffix('.json')
        if mp3_file.exists():
            lyrics_text = None
            if json_file.exists():
                # Load from JSON metadata
                with open(json_file, 'r') as f:
                    metadata = json.load(f)
                    lyrics_text = metadata.get('lyrics', '')
            if not lyrics_text:
                # Fallback to parsing TXT
                lyrics = parse_ultrastar(txt_file)
                if lyrics:
                    lyrics_text = ' '.join([s['text'] for s in lyrics])
            if lyrics_text:
                data.append({
                    'audio': str(mp3_file.resolve()),
                    'text': lyrics_text
                })
    print(f"Loaded {len(data)} training samples")
    return data

def prepare_dataset(data):
    """Prepare dataset for training."""
    df = pd.DataFrame(data)
    print(f"Loading audio for {len(df)} samples...")
    audio_data = []
    texts = []
    for _, row in df.iterrows():
        audio, sr = librosa.load(row['audio'], sr=16000)
        # Pad/truncate to fixed length for arrow compatibility
        max_len = 16000 * 60  # 1 minute
        padded_audio = audio[:max_len] if len(audio) > max_len else np.pad(audio, (0, max_len - len(audio)), 'constant')
        audio_data.append({"array": padded_audio, "sampling_rate": sr})
        texts.append(row['text'])
    # Use from_dict to handle fixed-length arrays
    dataset = Dataset.from_dict({
        'audio': audio_data,
        'text': texts
    })
    print(f"Dataset size: {len(dataset)}")
    return dataset

def train_whisper(data_dir):
    """Fine-tune Whisper on Ultrastar data."""
    processor = WhisperProcessor.from_pretrained("openai/whisper-base")
    model = WhisperForConditionalGeneration.from_pretrained("openai/whisper-base")

    data = load_training_data(data_dir)
    if not data:
        print("No training data found.")
        return

    dataset = prepare_dataset(data)

    def preprocess_function(batch):
        audio = batch["audio"]
        inputs = processor(audio["array"], sampling_rate=audio["sampling_rate"], return_tensors="pt")
        labels = processor.tokenizer(batch["text"], max_length=448, truncation=True).input_ids
        return {"input_features": inputs.input_features.squeeze(0), "labels": labels}

    dataset = dataset.map(preprocess_function, remove_columns=["audio", "text"])
    print(f"Processed dataset size: {len(dataset)}")

    # Define training args
    training_args = TrainingArguments(
        output_dir="./whisper_finetuned",
        per_device_train_batch_size=1,
        num_train_epochs=3,
        logging_steps=10,
        save_steps=500,
        evaluation_strategy="no",
        save_total_limit=2,
    )

    def data_collator(features):
        input_features = [torch.tensor(f["input_features"]) for f in features]
        labels = [f["labels"] for f in features]
        
        # Pad labels to max length
        max_label_len = max(len(l) for l in labels)
        labels = [l + [-100] * (max_label_len - len(l)) for l in labels]
        
        return {
            "input_features": torch.stack(input_features),
            "labels": torch.tensor(labels)
        }

    trainer = Trainer(
        model=model,
        args=training_args,
        train_dataset=dataset,
        data_collator=data_collator,
    )

    print("Starting training...")
    trainer.train()
    print("Training done. Saving model...")
    model.save_pretrained("./whisper_finetuned")
    processor.save_pretrained("./whisper_finetuned")
    print("Model saved.")

if __name__ == "__main__":
    data_dir = "training_data"
    train_whisper(data_dir)