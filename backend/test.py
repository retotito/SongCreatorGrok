import os
from pathlib import Path

training_dir = os.path.join(os.getcwd(), "training_data")
print("Training dir:", training_dir)
print("Exists:", os.path.exists(training_dir))
if os.path.exists(training_dir):
    data = []
    for file in os.listdir(training_dir):
        if file.endswith('.mp3'):
            stem = Path(file).stem
            txt_file = f"{stem}.txt"
            json_file = f"{stem}.json"
            has_txt = os.path.exists(os.path.join(training_dir, txt_file))
            has_json = os.path.exists(os.path.join(training_dir, json_file))
            data.append({
                "name": stem,
                "mp3": file,
                "txt": txt_file if has_txt else None,
                "json": json_file if has_json else None,
                "complete": has_txt and has_json
            })
    print("Data:", data)