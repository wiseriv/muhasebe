import streamlit as st
import os
import pandas as pd
from PIL import Image
import io
import json
import google.generativeai as genai

# --- AYARLAR ---
# 1. YOL: Anahtarı direkt buraya yazabilirsin (Sadece test için, GitHub'a atma!)
# os.environ["GEMINI_API_KEY"] = "BURAYA_YENI_ALDIGIN_UZUN_ANAHTARI_YAPISTIR"

# 2. YOL: Streamlit Secrets (En Güvenlisi)
# .streamlit/secrets.toml dosyasına veya Cloud'daki Secrets kısmına şunu ekle:
# GEMINI_API_KEY = "AIzaSy..."
if "GEMINI_API_KEY" in st.secrets:
    os.environ["GEMINI_API_KEY"] = st.secrets["GEMINI_API_KEY"]

# Gemini'yi Yapılandır
genai.configure(api_key=os.environ["GEMINI_API_KEY"])

def gemini_ile_analiz_et(image_bytes):
    """Fişi Gemini 1.5 Flash modeline gönderir ve JSON ister."""
    try:
        # Modeli seç (Flash modeli hızlı ve ucuzdur)
        model = genai.GenerativeModel('gemini-1.5-flash')
        
        # Resmi formatla
        image_parts = [
            {
                "mime_type": "image/jpeg",
                "data": image_bytes
            }
        ]

        # YAPAY ZEKAYA VERİLEN EMİR (PROMPT)
        prompt = """
        Sen uzman bir muhasebe asistanısın. Bu fiş görüntüsünü analiz et.
        Aşağıdaki bilgileri saf JSON formatında çıkar. Başka hiçbir yorum yapma.
        
        İstenen JSON Formatı:
        {
            "isyeri_adi": "İşyeri ismi",
            "tarih": "GG.AA.YYYY formatında tarih",
            "toplam_tutar": "Sadece sayı (örn: 150.50)",
            "toplam_kdv": "Sadece sayı (örn: 25.00). Eğer KDV yoksa 0 yaz."
        }
        
        Dikkat et:
        - Fişin 'Genel Toplam'ını bul. Ara toplamlara dikkat et.
        - KDV bazen 'TOPKDV' veya yüzdelik dilimlerin toplamı olarak yazar.
        - Fiş yan veya ters olsa bile düzgün oku.
        """

        response = model.generate_content([prompt, image_parts[0]])
        
        # Gelen metni temizle (Bazen ```json ... ``` diye süsler, onu siliyoruz)
        text = response.text.strip()
        if text.startswith("```json"):
            text = text[7:-3]
        
        return json.loads(text)

    except Exception as e:
        st.error(f"Yapay Zeka Hatası: {e}")
        return None

# --- WEB ARAYÜZÜ ---
st.set_page_config(page_title="Mihsap AI - Yeni Nesil", layout="wide", page_icon="🧠")

st.title("🧠 Gerçek Yapay Zeka Muhasebecisi")
st.write("Kural yok, Regex yok. Gemini 1.5 Flash fişi görüyor ve anlıyor.")

yuklenen_dosyalar = st.file_uploader("Fişleri Yükle", type=['jpg', 'png', 'jpeg'], accept_multiple_files=True)

if yuklenen_dosyalar:
    tum_veriler = []
    progress_bar = st.progress(0)
    
    for i, dosya in enumerate(yuklenen_dosyalar):
        # Resmi hazırla
        image = Image.open(dosya)
        img_byte_arr = io.BytesIO()
        image.save(img_byte_arr, format='JPEG')
        bytes_data = img_byte_arr.getvalue()
        
        # AI'ya sor
        sonuc = gemini_ile_analiz_et(bytes_data)
        
        if sonuc:
            # Dosya adını da ekleyelim
            sonuc["dosya_adi"] = dosya.name
            tum_veriler.append(sonuc)
        
        progress_bar.progress((i + 1) / len(yuklenen_dosyalar))
    
    if tum_veriler:
        df = pd.DataFrame(tum_veriler)
        
        # Sütun sırasını düzeltelim
        cols = ["dosya_adi", "isyeri_adi", "tarih", "toplam_tutar", "toplam_kdv"]
        # Eğer AI bazen farklı key dönerse hata almamak için kontrol
        mevcut_cols = [c for c in cols if c in df.columns]
        df = df[mevcut_cols]

        st.write("### 📊 AI Analiz Sonuçları")
        st.dataframe(df, use_container_width=True)
        
        buffer = io.BytesIO()
        with pd.ExcelWriter(buffer, engine='openpyxl') as writer:
            df.to_excel(writer, index=False)
            
        st.download_button("📥 Excel İndir", data=buffer.getvalue(), file_name="ai_muhasebe.xlsx", type="primary")
