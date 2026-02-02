import speech_recognition as sr
import librosa
import soundfile as sf
import os
import tempfile
import numpy as np

def transcribe_audio(file_path: str) -> str:
    """
    Transcribe audio file to text using Google Speech Recognition.
    Supports: WAV, MP3, M4A, OGG, FLAC via librosa conversion
    """
    try:
        # Load audio with librosa (supports multiple formats)
        print(f"Loading audio file: {file_path}")
        y, sr_rate = librosa.load(file_path, sr=16000)  # Resample to 16kHz for better recognition
        
        # Create temporary WAV file for speech recognition
        with tempfile.NamedTemporaryFile(suffix='.wav', delete=False) as tmp:
            tmp_wav_path = tmp.name
            
        try:
            # Write audio to temporary WAV file
            sf.write(tmp_wav_path, y, sr_rate)
            print(f"Converted to temporary WAV: {tmp_wav_path}")
            
            # Transcribe using Google Speech Recognition
            recognizer = sr.Recognizer()
            
            with sr.AudioFile(tmp_wav_path) as source:
                audio_data = recognizer.record(source)
                
            try:
                print("Sending to Google Speech Recognition API...")
                text = recognizer.recognize_google(audio_data)
                print(f"Transcription successful: {len(text)} characters")
                return text
                
            except sr.UnknownValueError:
                return "Could not understand audio. Please ensure clear audio quality."
            except sr.RequestError as e:
                return f"Speech recognition service error: {e}"
                
        finally:
            # Clean up temporary file
            if os.path.exists(tmp_wav_path):
                try:
                    os.remove(tmp_wav_path)
                except:
                    pass
                    
    except FileNotFoundError:
        return f"Audio file not found: {file_path}"
    except Exception as e:
        return f"Transcription error: {str(e)}"

def get_audio_duration(file_path: str) -> int:
    """Get audio duration in seconds using librosa"""
    try:
        y, sr_rate = librosa.load(file_path, sr=None)
        duration = librosa.get_duration(y=y, sr=sr_rate)
        return int(duration)
    except Exception as e:
        print(f"Duration error: {e}")
        return 0
