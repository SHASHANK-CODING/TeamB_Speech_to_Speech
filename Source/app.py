from flask import Flask, render_template, request, jsonify, send_file
import os, sys, uuid, json, requests, shutil
import azure.cognitiveservices.speech as speechsdk
from bert_score import score

app = Flask(__name__, template_folder="templates", static_folder="static")

# Azure Keys (replace with yours)
SPEECH_KEY = "7LJydU5wgnXnELtIMse6ss0pmzhKpYoeyL5NmItRJvpzpKt1HAKoJQQJ99BIACYeBjFXJ3w3AAAAACOGr3kS"
SPEECH_REGION = "eastus"
TRANSLATOR_KEY = "9SQdxG8Pm3VImlxCDQZlk7zZPqLXUynGgO6VKCa6AdpGwQd3qNMrJQQJ99BJACYeBjFXJ3w3AAAbACOGDfFY"
TRANSLATOR_REGION = "eastus"
TRANSLATOR_ENDPOINT = "https://api.cognitive.microsofttranslator.com"

# Ensure directories
os.makedirs("uploads", exist_ok=True)
os.makedirs("translations", exist_ok=True)

@app.route("/")
def index():
    return render_template("index.html")

@app.route("/process", methods=["POST"])
def process_audio():
    audio_file = request.files["audio"]
    audio_path = os.path.join("uploads", audio_file.filename)
    audio_file.save(audio_path)

    # Detect language
    speech_lang = "hi-IN" if "hindi" in audio_file.filename.lower() else "en-US"

    # Speech to Text
    speech_config = speechsdk.SpeechConfig(subscription=SPEECH_KEY, region=SPEECH_REGION)
    speech_config.speech_recognition_language = speech_lang
    audio_config = speechsdk.audio.AudioConfig(filename=audio_path)
    recognizer = speechsdk.SpeechRecognizer(speech_config=speech_config, audio_config=audio_config)

    result = recognizer.recognize_once()
    if result.reason == speechsdk.ResultReason.RecognizedSpeech:
        recognized_text = result.text
    else:
        return jsonify({"error": f"Speech recognition failed: {result.reason}"})

    # Translation
    target_languages = ["en", "fr", "es","de","hi","ta","te","ml","ar","zh","ru","ja"]
    translator_url = TRANSLATOR_ENDPOINT + "/translate"
    params = {"api-version": "3.0", "from": speech_lang[:2], "to": target_languages}
    headers = {
        "Ocp-Apim-Subscription-Key": TRANSLATOR_KEY,
        "Ocp-Apim-Subscription-Region": TRANSLATOR_REGION,
        "Content-Type": "application/json",
        "X-ClientTraceId": str(uuid.uuid4())
    }
    body = [{"text": recognized_text}]
    response = requests.post(translator_url, params=params, headers=headers, json=body)
    response.raise_for_status()
    translations = response.json()

    translations_dict = {}
    if isinstance(translations, list) and "translations" in translations[0]:
        for t in translations[0]["translations"]:
            lang, text = t["to"], t["text"]
            translations_dict[lang] = text

            # Save each translation as TXT
            file_path = os.path.join("translations", f"translation_{lang}.txt")
            with open(file_path, "w", encoding="utf-8") as f:
                f.write(text)

    # Load reference translations
    reference_translations = {}
    if os.path.exists("data.json"):
        with open("data.json", "r", encoding="utf-8") as f:
            reference_translations = json.load(f)

    # BERTScore evaluation
    bert_scores = []
    for lang, candidate_text in translations_dict.items():
        references = reference_translations.get(lang, [])

        if references:
            # Compare candidate with all references
            candidates = [candidate_text] * len(references)
            P, R, F1 = score(
                candidates,
                references,
                lang=lang,
                model_type="bert-base-multilingual-cased",
                verbose=False
            )

            # Pick best match
            best_idx = F1.argmax()
            bert_scores.append({
                "lang": lang,
                "precision": P[best_idx].item(),
                "recall": R[best_idx].item(),
                "f1": F1[best_idx].item()
            })
        else:
            bert_scores.append({
                "lang": lang,
                "precision": 0.0,
                "recall": 0.0,
                "f1": 0.0
            })

    return jsonify({
        "recognized_text": recognized_text,
        "translations": translations_dict,
        "bert_scores": bert_scores
    })


@app.route("/download_audio/<lang>", methods=["GET"])
def download_audio(lang):
    file_path = os.path.join("translations", f"translation_{lang}.txt")
    if not os.path.exists(file_path):
        return jsonify({"error": f"No translation found for {lang}"}), 404

    # Read translated text
    with open(file_path, "r", encoding="utf-8") as f:
        text_to_speak = f.read()

    # Azure Speech Config
    speech_config = speechsdk.SpeechConfig(subscription=SPEECH_KEY, region=SPEECH_REGION)

    # Pick voice based on language
    voices = {
        "en": "en-US-AriaNeural",
        "es": "es-ES-ElviraNeural",
        "fr": "fr-FR-DeniseNeural"
    }
    voice = voices.get(lang, "en-US-AriaNeural")  # fallback to English

    speech_config.speech_synthesis_voice_name = voice

    audio_path = os.path.join("translations", f"translation_{lang}.wav")
    audio_output = speechsdk.audio.AudioOutputConfig(filename=audio_path)

    # Synthesize
    synthesizer = speechsdk.SpeechSynthesizer(speech_config=speech_config, audio_config=audio_output)
    result = synthesizer.speak_text_async(text_to_speak).get()

    if result.reason == speechsdk.ResultReason.SynthesizingAudioCompleted:
        return send_file(audio_path, as_attachment=True)
    else:
        return jsonify({"error": "Audio synthesis failed"}), 500


# ✅ Download single file
@app.route("/download/<lang>", methods=["GET"])
def download_translation(lang):
    file_path = os.path.join("translations", f"translation_{lang}.txt")
    if os.path.exists(file_path):
        return send_file(file_path, as_attachment=True)
    return jsonify({"error": f"No translation found for {lang}"}), 404

# ✅ Download all as ZIP
@app.route("/download_all", methods=["GET"])
def download_all():
    zip_path = "translations.zip"
    shutil.make_archive("translations", "zip", "translations")
    return send_file(zip_path, as_attachment=True)

if __name__ == "__main__":
    app.run(debug=True)