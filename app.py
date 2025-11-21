import streamlit as st
import os
import pandas as pd
from PIL import Image
import io
import json
import requests
import base64

st.set_page_config(page_title="Mihsap AI - Final", layout="wide", page_icon="🚀")
API_KEY = st.secrets.get("GEMINI_API_KEY")

if not API_KEY:
    st.error("Lütfen Secrets ayarlarından GEMINI_API_KEY'i ekleyin.")
    st.stop()

# --- MODELLERİ GETİR VE SIRALA ---
def modelleri_getir_ve_sirala():
    url = f"https://generativelanguage.googleapis.com/v1beta/models?key={API_KEY}"
    try:
        response = requests.get(url)
        if response.status_code == 200:
            data = response.json()
            tum_modeller = []
            if 'models' in data:
                for m in data['models']:
                    if 'generateContent' in m.get('supportedGenerationMethods', []):
                        ad = m['name'].replace("models/", "")
                        tum_modeller.append(ad)
            
            # --- AKILLI SIRALAMA ---
            # "flash" kelimesi geçenleri listenin en başına al (Çünkü onlar ücretsiz ve hızlı)
            # "exp" (experimental) geçenleri en sona at (Çünkü onlar hata verebilir)
            flash_modeller = [m for m in tum_modeller if "flash" in m]
            diger_modeller = [m for m in tum_modeller if "flash" not in m and "exp" not in m]
            deneysel_modeller = [m for m in tum_modeller if "exp" in m]
            
            return flash_modeller + diger_modeller + deneysel_modeller
        return []
    except:
        return []

# --- ANALİZ ---
def resmi_base64_yap(image_bytes):
    return base64.b64encode(image_bytes).decode('utf-8')

def gemini_ile_analiz_et(image_bytes, secilen_model):
    # DİKKAT: URL yapısını ve model adını doğru birleştirmek önemli
    url = f"https://generativelanguage.googleapis.com/v1beta/models/{secilen_model}:generateContent?key={API_KEY}"
    
    headers = {'Content-Type': 'application/json'}
    base64_image = resmi_base64_yap(image_bytes)
    
    payload = {
        "contents": [{
            "parts": [
                {"text": """Bu fiş görüntüsünü analiz et. 
                Cevabı SADECE aşağıdaki formatta saf JSON olarak ver:
                {
                    "isyeri_adi": "İşyeri Adı",
                    "tarih": "GG.AA.YYYY",
                    "toplam_tutar": "00.00",
                    "toplam_kdv": "00.00"
                }"""},
                {"inline_data": {"mime_type": "image/jpeg", "data": base64_image}}
            ]
        }]
    }

    try:
        response = requests.post(url, headers=headers, json=payload)
        
        # HATA YÖNETİMİ (429 KOTA HATASI İÇİN)
        if response.status_code == 429:
            st.error("🚨 Kota Aşıldı! Bu model ücretsiz planda kullanılamıyor veya çok hızlı istek attınız.")
            st.warning("Lütfen sol menüden içinde 'flash' geçen başka bir model seçin.")
            return None
        elif response.status_code != 200:
            st.error(f"Google Hatası ({response.status_code}): {response.text}")
            return None
            
        sonuc_json = response.json()
        try:
            metin = sonuc_json['candidates'][0]['content']['parts'][0]['text']
            metin = metin.replace("```json", "").replace("```", "").strip()
            return json.loads(metin)
        except:
            return None

    except Exception as e:
        st.error(f"Hata: {e}")
        return None

# --- ARAYÜZ ---
with st.sidebar:
    st.header("⚙️ Model Seçimi")
    mevcut_modeller = modelleri_getir_ve_sirala()
    
    if mevcut_modeller:
        # Varsayılan olarak listenin ilkini (Flash) seçtiriyoruz
        secilen_model = st.selectbox("Kullanılacak Model", mevcut_modeller, index=0)
        
        if "exp" in secilen_model:
            st.warning("⚠️ 'exp' (Deneysel) modeller ücretsiz hesaplarda çalışmayabilir.")
        else:
            st.success("✅ Bu model kararlı ve hızlıdır.")
    else:
        st.error("Model listesi çekilemedi. Fallback kullanılıyor.")
        secilen_model = "gemini-1.5-flash"

st.title("🚀 Mihsap AI - Hazır")
st.write(f"Aktif Beyin: **{secilen_model}**")

yuklenen_dosyalar = st.file_uploader("Fiş Yükle", type=['jpg', 'png', 'jpeg'], accept_multiple_files=True)

if yuklenen_dosyalar:
    tum_veriler = []
    progress_bar = st.progress(0)
    
    for i, dosya in enumerate(yuklenen_dosyalar):
        image = Image.open(dosya)
        buf = io.BytesIO()
        image = image.convert('RGB')
        image.save(buf, format='JPEG')
        
        sonuc = gemini_ile_analiz_et(buf.getvalue(), secilen_model)
        
        if sonuc:
            sonuc["dosya_adi"] = dosya.name
            tum_veriler.append(sonuc)
        
        progress_bar.progress((i + 1) / len(yuklenen_dosyalar))
    
    if tum_veriler:
        df = pd.DataFrame(tum_veriler)
        cols = ["dosya_adi", "isyeri_adi", "tarih", "toplam_tutar", "toplam_kdv"]
        # Sütunları düzenle
        mevcut_cols = [c for c in cols if c in df.columns]
        st.dataframe(df[mevcut_cols], use_container_width=True)
        
        buffer = io.BytesIO()
        with pd.ExcelWriter(buffer, engine='openpyxl') as writer:
            df.to_excel(writer, index=False)
        st.download_button("📥 Excel İndir", data=buffer.getvalue(), file_name="muhasebe_ai.xlsx", type="primary")
