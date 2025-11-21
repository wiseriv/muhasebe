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
from datetime import datetime
import gspread
from oauth2client.service_account import ServiceAccountCredentials

# --- AYARLAR ---
st.set_page_config(page_title="Mihsap AI - Veritabanı", layout="wide", page_icon="🗃️")
API_KEY = st.secrets.get("GEMINI_API_KEY")

if not API_KEY:
    st.error("Lütfen Secrets ayarlarından GEMINI_API_KEY'i ekleyin.")
    st.stop()

# --- GOOGLE SHEETS BAĞLANTISI (TANI MODU EKLENDİ) ---
@st.cache_resource
def sheets_baglantisi_kur():
    """Google Sheets'e bağlanır ve detaylı hata raporu verir."""
    scope = ["https://spreadsheets.google.com/feeds", "https://www.googleapis.com/auth/drive"]
    
    # 1. KONTROL: Anahtar var mı?
    if "gcp_service_account" not in st.secrets:
        st.error("❌ HATA: Secrets içinde '[gcp_service_account]' başlığı bulunamadı.")
        st.warning(f"Streamlit'in gördüğü anahtarlar şunlar: {list(st.secrets.keys())}")
        st.info("ÇÖZÜM: Secrets kısmında '[gcp_service_account]' başlığını sildiysen geri ekle.")
        return None

    # 2. KONTROL: İçeriği doğru mu?
    try:
        creds_dict = dict(st.secrets["gcp_service_account"])
        
        # Kritik bilgileri kontrol et
        if "private_key" not in creds_dict:
            st.error("❌ HATA: JSON verisi bozuk. 'private_key' eksik.")
            return None
            
        creds = ServiceAccountCredentials.from_json_keyfile_dict(creds_dict, scope)
        client = gspread.authorize(creds)
        return client
    except Exception as e:
        st.error(f"❌ Bağlantı Hatası: {e}")
        return None

def sheete_kaydet(veri_listesi):
    """Verileri Google E-Tabloya ekler."""
    client = sheets_baglantisi_kur()
    if not client: 
        return False # Bağlantı yoksa dur
        
    try:
        # Tabloyu aç
        sheet = client.open("Mihsap Veritabanı").sheet1
        
        rows_to_add = []
        for v in veri_listesi:
            row = [
                v.get("dosya_adi", "-"),
                v.get("isyeri_adi", "-"),
                v.get("fiş_no", "-"),
                v.get("tarih", "-"),
                v.get("toplam_tutar", "0"),
                v.get("toplam_kdv", "0"),
                datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            ]
            rows_to_add.append(row)
            
        sheet.append_rows(rows_to_add)
        st.toast(f"✅ {len(veri_listesi)} kayıt veritabanına işlendi!", icon="💾")
        return True
        
    except Exception as e:
        st.error(f"❌ Veritabanı Hatası: {e}")
        st.info("1. Tablo adının 'Mihsap Veritabanı' olduğundan emin misin?")
        st.info("2. Tabloyu 'client_email' adresiyle paylaştın mı?")
        # Hangi maille paylaşması gerektiğini göster
        try:
             mail = st.secrets["gcp_service_account"]["client_email"]
             st.code(f"Paylaşılacak Mail: {mail}")
        except:
            pass
        return False

# --- YARDIMCI FONKSİYONLAR ---
@st.cache_data
def modelleri_getir():
    url = f"https://generativelanguage.googleapis.com/v1beta/models?key={API_KEY}"
    try:
        response = requests.get(url)
        if response.status_code == 200:
            data = response.json()
            flash = [m['name'].replace("models/", "") for m in data.get('models', []) if "flash" in m['name']]
            diger = [m['name'].replace("models/", "") for m in data.get('models', []) if "flash" not in m['name']]
            return flash + diger
        return []
    except:
        return []

def resmi_hazirla(image_bytes):
    img = Image.open(io.BytesIO(image_bytes))
    if img.mode in ("RGBA", "P"): img = img.convert("RGB")
    img.thumbnail((1024, 1024))
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=80)
    return base64.b64encode(buf.getvalue()).decode('utf-8')

def gemini_ile_analiz_et(dosya_objesi, secilen_model):
    dosya_adi = dosya_objesi.name
    try:
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
                        "fiş_no": "Fiş No",
                        "tarih": "GG.AA.YYYY",
                        "toplam_tutar": "00.00",
                        "toplam_kdv": "00.00"
                    }"""},
                    {"inline_data": {"mime_type": "image/jpeg", "data": base64_image}}
                ]
            }]
        }
        response = requests.post(url, headers=headers, json=payload)
        if response.status_code != 200: return {"dosya_adi": dosya_adi, "hata": f"Hata ({response.status_code})"}
        
        metin = response.json()['candidates'][0]['content']['parts'][0]['text']
        metin = metin.replace("```json", "").replace("```", "").strip()
        veri = json.loads(metin)
        veri["dosya_adi"] = dosya_adi
        return veri
    except Exception as e:
        return {"dosya_adi": dosya_adi, "hata": str(e)}

# --- ARAYÜZ ---
with st.sidebar:
    st.header("⚙️ Ayarlar")
    mevcut_modeller = modelleri_getir()
    secilen_model = st.selectbox("Model", mevcut_modeller, index=0) if mevcut_modeller else "gemini-1.5-flash"
    isci_sayisi = st.slider("Hız", 1, 5, 3)

st.title("🗃️ Mihsap AI - Veritabanı Modu")

yuklenen_dosyalar = st.file_uploader("Fişleri Yükle", type=['jpg', 'png', 'jpeg'], accept_multiple_files=True)

if yuklenen_dosyalar:
    if st.button("🚀 Başlat ve Kaydet"):
        tum_veriler = []
        bar = st.progress(0)
        
        with concurrent.futures.ThreadPoolExecutor(max_workers=isci_sayisi) as executor:
            future_to_file = {executor.submit(gemini_ile_analiz_et, d, secilen_model): d for d in yuklenen_dosyalar}
            completed = 0
            for future in concurrent.futures.as_completed(future_to_file):
                sonuc = future.result()
                if "hata" not in sonuc:
                    tum_veriler.append(sonuc)
                completed += 1
                bar.progress(completed / len(yuklenen_dosyalar))
                time.sleep(0.5)

        if tum_veriler:
            st.write("### 📊 Analiz Sonuçları")
            df = pd.DataFrame(tum_veriler)
            st.dataframe(df, use_container_width=True)
            
            # --- KAYIT İŞLEMİ ---
            with st.spinner("Veritabanına bağlanılıyor..."):
                basari = sheete_kaydet(tum_veriler)
            
            if basari:
                st.success("✅ BÜYÜK BAŞARI! Veriler Google Sheets'e kaydedildi.")
            else:
                st.error("❌ Kayıt başarısız oldu. Lütfen yukarıdaki hata mesajını oku.")
