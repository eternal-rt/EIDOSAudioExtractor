# EIDOS Audio Extractor

Python tool for extracting and decoding audio from Project Eden `.EDS` sound banks.

---

### Overview

Project Eden (Core Design / Eidos, 2001) stores its sound effects and voice lines inside `.EDS` files.  
These files contain multiple embedded WAVE chunks that use a custom IMA-style ADPCM compression.

This script finds those chunks, decompresses the audio, and writes standard 16-bit PCM `.wav` files.

---

### How the decompression works

Each audio payload starts with a short header:

- 2 bytes → initial predictor (signed 16-bit)
- 1 byte  → starting step index
- 1 byte  → unused / padding

The rest of the data is packed as 4-bit nibbles.  
For every nibble the decoder:

1. Looks up the current step size from the standard IMA step table
2. Calculates the difference value from the nibble bits
3. Adds or subtracts that difference from the running predictor
4. Clamps the result to the 16-bit range
5. Updates the step index using the IMA index table

This is the same core algorithm used by IMA ADPCM, with the exact layout Project Eden expects.

The script also applies optional gain and peak normalization after decoding.

---

### Important

**This decoder is not perfect.**

- Most voice lines come out clean and usable.
- Many sound effects still have noise, crackling, or distortion.
- Some clips decode better than others depending on how they were originally encoded.
- Gain and normalization only change volume — they cannot fix decoding artifacts.

The tool is good enough for ripping dialogue and a lot of the SFX, but don’t expect studio-quality results from every file.

---

### Usage

```bash
python project_eden_extractor.py
