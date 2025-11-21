import streamlit as st
import os
import pandas as pd
from PIL import Image
import io
import json
import google.generativeai as genai

# --- AYARLAR ---
if "GEMINI_API_KEY" in st.secrets:
    os.environ["GEMINI_API_KEY"] = st.secrets["GEMINI_API_KEY"]

try:
    genai.configure(api_key=os.environ["GEMINI_API_KEY"])
except Exception as e:
    st.error(f"API Anahtarı Hatası: {e}")

# --- MODEL SEÇİCİ (HATA AYIKLAMA İÇİN) ---
def get_available_models():
    """Kullanılabilir modelleri listeler."""
    try:
        models = []
        for m in genai.list_models():
            if 'generateContent' in m.supported_generation_methods:
                models.append(m.name)
        return models
    except:
        return ["Modeller listelenemedi"]

def gemini_ile_analiz_et(image_bytes, model_adi):
    """Seçilen model ile analiz yapar."""
    try:
        # Modeli yükle
        model = genai.GenerativeModel(model_adi)
        
        image_parts = [{"mime_type": "image/jpeg", "data": image_bytes}]

        prompt = """
        Bu fiş görüntüsünü analiz et. Yan veya ters olsa bile düzeltip oku.
        Aşağıdaki formatta saf JSON verisi çıkar:
        {
            "isyeri_adi": "İşyeri Adı",
            "tarih": "GG.AA.YYYY",
            "toplam_tutar": "00.00",
            "toplam_kdv": "00.00"
        }
        Sadece JSON döndür.
        """

        response = model.generate_content([prompt, image_parts[0]])
        
        text = response.text.strip()
        if text.startswith("```json"): text = text[7:-3]
        if text.startswith("```"): text = text[3:-3]
        
        return json.loads(text)

    except Exception as e:
        st.error(f"Model Hatası ({model_adi}): {e}")
        return None

# --- WEB ARAYÜZÜ ---
st.set_page_config(page_title="Mihsap AI", layout="wide", page_icon="🧠")

# Kenar Çubuğu (Ayarlar)
with st.sidebar:
    st.header("⚙️ Model Ayarları")
    mevcut_modeller = get_available_models()
    
    # Eğer liste boşsa manuel ekle
    if not mevcut_modeller:
        mevcut_modeller = ["models/gemini-1.5-flash", "models/gemini-pro-vision"]
    
    # Kullanıcıya model seçtir (Hata olursa değiştirebilsin diye)
    secilen_model = st.selectbox(
        "Kullanılacak Model", 
        mevcut_modeller, 
        index=0 if "models/gemini-1.5-flash" in mevcut_modeller else 0
    )
    st.info(f"Şu an kullanılan: {secilen_model}")

st.title("🧠 Mihsap AI (Gemini)")
st.write("Google'ın en yeni yapay zekası ile fiş analizi.")

yuklenen_dosyalar = st.file_uploader("Fişleri Yükle", type=['jpg', 'png', 'jpeg'], accept_multiple_files=True)

if yuklenen_dosyalar:
    tum_veriler = []
    progress_bar = st.progress(0)
    
    for i, dosya in enumerate(yuklenen_dosyalar):
        image = Image.open(dosya)
        img_byte_arr = io.BytesIO()
        image.save(img_byte_arr, format='JPEG')
        
        # Seçilen model ile analiz et
        sonuc = gemini_ile_analiz_et(img_byte_arr.getvalue(), secilen_model)
        
        if sonuc:
            sonuc["dosya_adi"] = dosya.name
            tum_veriler.append(sonuc)
        
        progress_bar.progress((i + 1) / len(yuklenen_dosyalar))
    
    if tum_veriler:
        df = pd.DataFrame(tum_veriler)
        cols = ["dosya_adi", "isyeri_adi", "tarih", "toplam_tutar", "toplam_kdv"]
        mevcut_cols = [c for c in cols if c in df.columns]
        st.dataframe(df[mevcut_cols], use_container_width=True)
