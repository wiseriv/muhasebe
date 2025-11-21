import streamlit as st
import os
import pandas as pd
from PIL import Image
import io
import json
import requests
import base64
import concurrent.futures
import time

# --- AYARLAR ---
st.set_page_config(page_title="Mihsap AI - Ultimate", layout="wide", page_icon="⚡")
API_KEY = st.secrets.get("GEMINI_API_KEY")

if not API_KEY:
    st.error("Lütfen Secrets ayarlarından GEMINI_API_KEY'i ekleyin.")
    st.stop()

# --- 1. DEDEKTİF: MODELLERİ BUL VE SIRALA ---
@st.cache_data # Google'a her saniye sormasın, hafızaya alsın
def modelleri_getir():
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
            
            # Akıllı Sıralama: Flash'ı en başa al
            flash = [m for m in tum_modeller if "flash" in m]
            diger = [m for m in tum_modeller if "flash" not in m]
            return flash + diger
        return []
    except:
        return []

# --- 2. SIKIŞTIRMA: HIZ İÇİN RESMİ KÜÇÜLT ---
def resmi_hazirla(image_bytes):
    img = Image.open(io.BytesIO(image_bytes))
    if img.mode in ("RGBA", "P"): img = img.convert("RGB")
    
    # Resmi 1024px'e küçült (Google için yeterli)
    img.thumbnail((1024, 1024))
    
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=80)
    return base64.b64encode(buf.getvalue()).decode('utf-8')

# --- 3. ANALİZ MOTORU (TEK DOSYA İÇİN) ---
def gemini_ile_analiz_et(dosya_objesi, secilen_model):
    dosya_adi = dosya_objesi.name
    try:
        # Resmi hazırla
        base64_image = resmi_hazirla(dosya_objesi.getvalue())
        
        url = f"https://generativelanguage.googleapis.com/v1beta/models/{secilen_model}:generateContent?key={API_KEY}"
        headers = {'Content-Type': 'application/json'}
        
        payload = {
            "contents": [{
                "parts": [
                    {"text": """Bu fiş görüntüsünü analiz et. 
                    Cevabı SADECE aşağıdaki formatta saf JSON olarak ver:
                    {
                        "isyeri_adi": "İşyeri Adı",
                        "fiş_no": "Fiş No (Yoksa boş)",
                        "tarih": "GG.AA.YYYY",
                        "toplam_tutar": "00.00",
                        "toplam_kdv": "00.00"
                    }"""},
                    {"inline_data": {"mime_type": "image/jpeg", "data": base64_image}}
                ]
            }]
        }

        response = requests.post(url, headers=headers, json=payload)
        
        if response.status_code == 429:
            return {"dosya_adi": dosya_adi, "hata": "⏳ Hız Sınırı (429)."}
        if response.status_code != 200:
            return {"dosya_adi": dosya_adi, "hata": f"Hata ({response.status_code})"}

        sonuc_json = response.json()
        try:
            metin = sonuc_json['candidates'][0]['content']['parts'][0]['text']
            metin = metin.replace("```json", "").replace("```", "").strip()
            veri = json.loads(metin)
            veri["dosya_adi"] = dosya_adi
            return veri
        except:
            return {"dosya_adi": dosya_adi, "hata": "Veri okunamadı"}

    except Exception as e:
        return {"dosya_adi": dosya_adi, "hata": str(e)}

# --- ARAYÜZ ---
with st.sidebar:
    st.header("⚙️ Ayarlar")
    
    # Modelleri Google'dan çek
    mevcut_modeller = modelleri_getir()
    
    if mevcut_modeller:
        secilen_model = st.selectbox("Model", mevcut_modeller, index=0)
        st.success(f"Aktif Model: {secilen_model}")
    else:
        st.error("Model bulunamadı! Manuel giriş yapın.")
        secilen_model = st.text_input("Model Adı", "gemini-1.5-flash")

    # Hız Ayarı
    isci_sayisi = st.slider("Aynı Anda İşlem", 1, 5, 3)

st.title("⚡ Mihsap AI - Ultimate")
st.write("Doğru model tespiti + Turbo Hız.")

yuklenen_dosyalar = st.file_uploader("Fişleri Yükle", type=['jpg', 'png', 'jpeg'], accept_multiple_files=True)

if yuklenen_dosyalar:
    if st.button("🚀 Başlat"):
        tum_veriler = []
        hatali_dosyalar = []
        
        bar = st.progress(0)
        durum = st.empty()
        
        # Paralel İşleme Başlıyor
        with concurrent.futures.ThreadPoolExecutor(max_workers=isci_sayisi) as executor:
            # Görevleri dağıt
            future_to_file = {executor.submit(gemini_ile_analiz_et, d, secilen_model): d for d in yuklenen_dosyalar}
            
            completed = 0
            for future in concurrent.futures.as_completed(future_to_file):
                sonuc = future.result()
                
                if "hata" in sonuc:
                    hatali_dosyalar.append(sonuc)
                else:
                    tum_veriler.append(sonuc)
                
                completed += 1
                bar.progress(completed / len(yuklenen_dosyalar))
                durum.text(f"İşlenen: {completed} / {len(yuklenen_dosyalar)}")
                
                # Free tier için minik fren
                time.sleep(0.5)

        st.success("Tamamlandı!")
        
        if tum_veriler:
            df = pd.DataFrame(tum_veriler)
            st.write("### ✅ Başarılı Sonuçlar")
            
            cols = ["dosya_adi", "isyeri_adi", "fiş_no", "tarih", "toplam_tutar", "toplam_kdv"]
            mevcut = [c for c in cols if c in df.columns]
            st.dataframe(df[mevcut], use_container_width=True)
            
            buffer = io.BytesIO()
            with pd.ExcelWriter(buffer, engine='openpyxl') as writer:
                df.to_excel(writer, index=False)
            st.download_button("📥 Excel İndir", data=buffer.getvalue(), file_name="ultimate_muhasebe.xlsx", type="primary")
            
        if hatali_dosyalar:
            st.error("Bazı dosyalarda sorun oldu:")
            st.dataframe(pd.DataFrame(hatali_dosyalar))
