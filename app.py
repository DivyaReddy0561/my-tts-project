from flask import Flask, render_template, request, jsonify, url_for
import io
from pydub import AudioSegment
from docx import Document
from PyPDF2 import PdfReader
import textwrap
import mammoth
import uuid
import boto3

# --- AWS CONFIGURATION ---
# IMPORTANT: All clients are configured globally with the region here.
AWS_REGION = 'us-east-1' # Use 'ap-south-1' if you prefer India region
S3_BUCKET = 'cloud-tts-21092025' # Ensure this bucket name is correct

# Initialize clients globally with the region specified
polly_client = boto3.client('polly', region_name=AWS_REGION)
s3_client = boto3.client('s3', region_name=AWS_REGION)
# -------------------------

app = Flask(__name__)

@app.route('/')
def home():
    return render_template('index.html')

@app.route('/synthesize', methods=['POST'])
def synthesize_speech():
    # The client initializations are now handled globally.
    # We do NOT need to redefine them here.

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
                # Extract text into chunks, keeping page breaks logical
                for i, page in enumerate(reader.pages):
                    page_text = page.extract_text()
                    if page_text and page_text.strip():
                        # We re-chunk based on length to respect Polly's limits (4900 chars)
                        page_chunks = textwrap.wrap(page_text.strip(), width=4900, break_long_words=False, replace_whitespace=False)
                        for chunk in page_chunks:
                            chunks.append(chunk)

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

        # Debug: print first chunk preview (will appear in Render logs)
        print(f"DEBUG: Extracted {len(chunks)} chunks. First chunk preview: {chunks[0][:50]}...")

        # --- AWS POLLY CALL ---
        # Reuse the globally defined polly_client
        audio_segments = []

        for chunk in chunks:
            if not chunk.strip():
                continue
            
            # NOTE: polly_client is defined globally with region_name='us-east-1'
            response = polly_client.synthesize_speech(
                Text=chunk,
                OutputFormat='mp3',
                VoiceId=voice_id,
                # Optionally add text type if your app uses SSML
                # TextType='ssml' 
            )
            audio_segments.append(response['AudioStream'].read())
        
        # ... Audio Concatenation and S3 Upload remains the same ...

        if not audio_segments:
            return jsonify({'error': 'No audio could be generated (empty file?)'}), 400

        # Concatenate audio using pydub
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
        s3_url = f"https://{S3_BUCKET}.s3.{AWS_REGION}.amazonaws.com/{s3_filename}"

        return jsonify({'audio_url': s3_url})

    except Exception as e:
        # In case of AWS authentication failure or other errors
        print(f"ERROR: {str(e)}")
        return jsonify({'error': f'Synthesis failed: {str(e)}'}), 500

if __name__ == '__main__':
    # Render overrides the port, so this host/port setting is only for local testing
    app.run(host='0.0.0.0', port=5000, debug=True)
