import os
import pdfplumber
import pytesseract
from PIL import Image

def extract_pdf_with_ocr(path: str) -> str:
    text = ""
    try:
        with pdfplumber.open(path) as pdf:
            for i, page in enumerate(pdf.pages):
                try:
                    ptext = page.extract_text()
                except Exception:
                    ptext = None
                
                if ptext and ptext.strip():
                    text += ptext + "\n"
                else:
                    try:
                        pil_img = page.to_image(resolution=300).original
                        ocr_text = pytesseract.image_to_string(Image.fromarray(pil_img))
                        text += ocr_text + "\n"
                    except Exception as e:
                        text += f"[OCR error page {i}: {e}]\n"
    except Exception as e:
        return f"[PDF open error: {e}]"
    return text
