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

def gemini_ile_analiz_et(image_bytes, model_adi):
    """Seçilen model ile analiz yapar."""
    try:
        # Modeli yükle
        model = genai.GenerativeModel(model_adi)
        
        image_parts = [{"mime_type": "image/jpeg", "data": image_bytes}]

        prompt = """
        Bu fiş görüntüsünü analiz et.
        Aşağıdaki formatta saf JSON verisi çıkar (Markdown kullanma):
        {
            "isyeri_adi": "İşyeri Adı",
            "tarih": "GG.AA.YYYY",
            "toplam_tutar": "00.00",
            "toplam_kdv": "00.00"
        }
        """

        response = model.generate_content([prompt, image_parts[0]])
        
        text = response.text.strip()
        # Markdown temizliği
        if text.startswith("```json"): text = text[7:-3]
        if text.startswith("```"): text = text[3:-3]
        
        return json.loads(text)

    except Exception as e:
        st.error(f"Model ({model_adi}) bu isteği yapamadı. Hata: {e}")
        return None

# --- WEB ARAYÜZÜ ---
st.set_page_config(page_title="Mihsap AI - Final", layout="wide", page_icon="🧠")

# --- SOL MENÜ (AYARLAR) ---
with st.sidebar:
    st.header("🛠️ Motor Ayarları")
    
    # Modelleri biz elle yazıyoruz, Google'a sormuyoruz
    secenekler = [
        "models/gemini-1.5-flash",      # En hızlı ve ucuz
        "models/gemini-1.5-pro",        # En zeki
        "models/gemini-pro-vision",     # Eski ama sağlam
        "Manuel Giriş Yap"              # Listede olmayan bir şey denemek için
    ]
    
    secim = st.selectbox("Kullanılacak Yapay Zeka Modeli", secenekler)
    
    final_model_adi = secim
    if secim == "Manuel Giriş Yap":
        final_model_adi = st.text_input("Model Adını Yaz", "models/gemini-1.5-flash-latest")
    
    st.info(f"Şu an seçili: {final_model_adi}")
    st.warning("Not: Eğer hata alırsan listeden diğer modelleri dene.")

# --- ANA EKRAN ---
st.title("🧠 Mihsap AI (Zeka Modu)")
st.write("Fişinizi yükleyin, Gemini 1.5 Flash analiz etsin.")

yuklenen_dosyalar = st.file_uploader("Fişleri Yükle", type=['jpg', 'png', 'jpeg'], accept_multiple_files=True)

if yuklenen_dosyalar:
    tum_veriler = []
    progress_bar = st.progress(0)
    
    for i, dosya in enumerate(yuklenen_dosyalar):
        image = Image.open(dosya)
        img_byte_arr = io.BytesIO()
        image.save(img_byte_arr, format='JPEG')
        
        # Seçilen model ile analiz et
        sonuc = gemini_ile_analiz_et(img_byte_arr.getvalue(), final_model_adi)
        
        if sonuc:
            sonuc["dosya_adi"] = dosya.name
            tum_veriler.append(sonuc)
        
        progress_bar.progress((i + 1) / len(yuklenen_dosyalar))
    
    if tum_veriler:
        df = pd.DataFrame(tum_veriler)
        
        # Sütun sırası
        cols = ["dosya_adi", "isyeri_adi", "tarih", "toplam_tutar", "toplam_kdv"]
        mevcut = [c for c in cols if c in df.columns]
        
        st.write("### 📊 Sonuçlar")
        st.dataframe(df[mevcut], use_container_width=True)
        
        buffer = io.BytesIO()
        with pd.ExcelWriter(buffer, engine='openpyxl') as writer:
            df.to_excel(writer, index=False)
            
        st.download_button("📥 Excel İndir", data=buffer.getvalue(), file_name="ai_muhasebe_final.xlsx", type="primary")
