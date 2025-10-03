from flask import Flask, render_template, request, jsonify, url_for
import boto3
import io
from pydub import AudioSegment
from docx import Document
from PyPDF2 import PdfReader
import textwrap
import mammoth
import uuid

app = Flask(__name__)

# Replace with your S3 bucket name and region
S3_BUCKET = 'cloud-tts-21092025'
S3_REGION = 'us-east-1'

# Set up S3 client
s3_client = boto3.client('s3', region_name=S3_REGION)

@app.route('/')
def home():
    return render_template('index.html')

@app.route('/synthesize', methods=['POST'])
def synthesize_speech():
    try:
        text_input = ""
        voice_id = ""
        chunks = []  # store text chunks

        # Check if a file was uploaded from the frontend
        if 'file' in request.files:
            uploaded_file = request.files['file']
            filename = uploaded_file.filename
            voice_id = request.form.get('voice', '')

            if not voice_id:
                return jsonify({'error': 'No voice selected'}), 400

            # Handle TXT
            if filename.endswith('.txt'):
                text_input = uploaded_file.read().decode('utf-8')
                chunks = textwrap.wrap(text_input, width=4900, break_long_words=False, replace_whitespace=False)

            # Handle DOCX
            elif filename.endswith('.docx'):
                document = Document(io.BytesIO(uploaded_file.read()))
                text_input = '\n'.join([para.text for para in document.paragraphs])
                chunks = textwrap.wrap(text_input, width=4900, break_long_words=False, replace_whitespace=False)

            # Handle DOC (Mammoth)
            elif filename.endswith('.doc'):
                try:
                    result = mammoth.extract_raw_text(io.BytesIO(uploaded_file.read()))
                    text_input = result.value
                    if not text_input.strip():
                        return jsonify({'error': 'Cannot extract text from this .doc file. Please convert it to .docx.'}), 400
                    chunks = textwrap.wrap(text_input, width=4900, break_long_words=False, replace_whitespace=False)
                except Exception as e:
                    return jsonify({'error': f'Failed to process .doc: {str(e)}'}), 400

            # Handle PDF (Page-by-Page)
            elif filename.endswith('.pdf'):
                reader = PdfReader(io.BytesIO(uploaded_file.read()))
                for i, page in enumerate(reader.pages):
                    page_text = page.extract_text()
                    if page_text and page_text.strip():
                        # Keep each page as its own chunk
                        chunks.append(f"--- Page {i+1} ---\n{page_text.strip()}")

                if not chunks:
                    return jsonify({'error': 'No extractable text found in PDF. Is it a scanned PDF?'}), 400

            else:
                return jsonify({'error': 'Unsupported file type'}), 400

        # Or if text was sent from the textarea (JSON)
        elif request.json and 'text' in request.json:
            text_input = request.json['text']
            voice_id = request.json.get('voice', '')
            if not voice_id:
                return jsonify({'error': 'No voice selected'}), 400

            chunks = textwrap.wrap(text_input, width=4900, break_long_words=False, replace_whitespace=False)

        else:
            return jsonify({'error': 'No text or file provided'}), 400

        # Final check for empty input
        if not chunks or all(not c.strip() for c in chunks):
            return jsonify({'error': 'No text to synthesize'}), 400

        # Debug: print first chunk preview
        print(f"DEBUG: Extracted {len(chunks)} chunks. First chunk preview:")
        print(chunks[0][:500])

        # Call Polly and generate audio
        polly_client = boto3.client('polly')
        audio_segments = []

        for chunk in chunks:
            if not chunk.strip():
                continue
            response = polly_client.synthesize_speech(
                Text=chunk,
                OutputFormat='mp3',
                VoiceId=voice_id,
            )
            audio_segments.append(response['AudioStream'].read())

        if not audio_segments:
            return jsonify({'error': 'No audio could be generated (empty file?)'}), 400

        # Concatenate audio
        if len(audio_segments) > 1:
            combined_audio = AudioSegment.from_mp3(io.BytesIO(audio_segments[0]))
            for audio_data in audio_segments[1:]:
                combined_audio += AudioSegment.from_mp3(io.BytesIO(audio_data))
            audio_stream = io.BytesIO()
            combined_audio.export(audio_stream, format="mp3")
            audio_stream.seek(0)
        else:
            audio_stream = io.BytesIO(audio_segments[0])

        # Generate a unique filename for S3
        s3_filename = str(uuid.uuid4()) + '.mp3'

        # Upload the audio file to S3
        s3_client.upload_fileobj(audio_stream, S3_BUCKET, s3_filename)

        # Create a public URL for the file
        s3_url = f"https://{S3_BUCKET}.s3.{S3_REGION}.amazonaws.com/{s3_filename}"

        return jsonify({'audio_url': s3_url})

    except Exception as e:
        return jsonify({'error': str(e)}), 500

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000, debug=True)


