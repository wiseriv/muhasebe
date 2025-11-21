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
import plotly.express as px
import zipfile

# --- 1. SAYFA AYARLARI ---
st.set_page_config(page_title="Mihsap AI", layout="wide", page_icon="💼")

# --- 2. GÜVENLİK ---
# --- 2. GÜVENLİK (ŞİFRELİ VERSİYON) ---
def giris_kontrol():
    if 'giris_yapildi' not in st.session_state: st.session_state['giris_yapildi'] = False
    
    if not st.session_state['giris_yapildi']:
        c1, c2, c3 = st.columns([1,2,1])
        with c2:
            st.markdown("## 🔐 Mihsap AI | Yönetici Girişi")
            st.info("Lütfen giriş şifresini giriniz.")
            
            # Şifre Kutusu Geri Geldi
            sifre = st.text_input("Şifre", type="password")
            
            if st.button("Giriş Yap"):
                if sifre == "12345": # Şifreniz burada
                    st.session_state['giris_yapildi'] = True
                    st.rerun()
                else:
                    st.error("❌ Hatalı Şifre! Tekrar deneyin.")
        st.stop()

API_KEY = st.secrets.get("GEMINI_API_KEY")
if not API_KEY: st.error("API Key Eksik!"); st.stop()

# --- YENİ ÖZELLİK: DOSYA KUTUSU SIFIRLAYICI ---
if 'uploader_key' not in st.session_state:
    st.session_state['uploader_key'] = 0

# --- 3. MOTORLAR ---
def temizle_ve_sayiya_cevir(deger):
    if pd.isna(deger) or deger == "": return 0.0
    try:
        s = str(deger).replace("₺", "").replace("TL", "").strip()
        if "," in s and "." in s: s = s.replace(".", "").replace(",", ".")
        elif "," in s: s = s.replace(",", ".")
        return float(s)
    except: return 0.0

def muhasebe_fisne_cevir(df_ham):
    yevmiye_satirlari = []
    for index, row in df_ham.iterrows():
        try:
            toplam = temizle_ve_sayiya_cevir(row.get('toplam_tutar', 0))
            kdv = temizle_ve_sayiya_cevir(row.get('toplam_kdv', 0))
            matrah = toplam - kdv
            tarih = row.get('tarih', datetime.now().strftime('%d.%m.%Y'))
            aciklama = f"{row.get('kategori', 'Genel')} - {row.get('isyeri_adi', 'Evrak')}"
            
            if matrah > 0: yevmiye_satirlari.append({"Tarih": tarih, "Hesap Kodu": "770.01", "Açıklama": aciklama, "Borç": matrah, "Alacak": 0})
            if kdv > 0: yevmiye_satirlari.append({"Tarih": tarih, "Hesap Kodu": "191.18", "Açıklama": "KDV", "Borç": kdv, "Alacak": 0})
            yevmiye_satirlari.append({"Tarih": tarih, "Hesap Kodu": "100.01", "Açıklama": "Ödeme", "Borç": 0, "Alacak": toplam})
        except: continue
    return pd.DataFrame(yevmiye_satirlari)

# --- 4. VERİTABANI ---
@st.cache_resource
def sheets_baglantisi_kur():
    if "gcp_service_account" not in st.secrets: return None
    try:
        scope = ["https://spreadsheets.google.com/feeds", "https://www.googleapis.com/auth/drive"]
        creds = ServiceAccountCredentials.from_json_keyfile_dict(dict(st.secrets["gcp_service_account"]), scope)
        return gspread.authorize(creds)
    except: return None

def sheete_kaydet(veri_listesi):
    client = sheets_baglantisi_kur()
    if not client: return False
    try:
        sheet = client.open("Mihsap Veritabanı").sheet1
        rows = []
        for v in veri_listesi:
            rows.append([
                v.get("dosya_adi", "-"), v.get("isyeri_adi", "-"), v.get("fiş_no", "-"), 
                v.get("tarih", "-"), v.get("kategori", "Diğer"), 
                str(v.get("toplam_tutar", "0")), str(v.get("toplam_kdv", "0")), 
                datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            ])
        sheet.append_rows(rows)
        return True
    except: return False

def sheetten_veri_cek():
    client = sheets_baglantisi_kur()
    if not client: return pd.DataFrame()
    try:
        sheet = client.open("Mihsap Veritabanı").sheet1
        data = sheet.get_all_records()
        if not data: return pd.DataFrame()
        df = pd.DataFrame(data)
        df.columns = [c.strip().lower().replace(" ", "").replace("_", "") for c in df.columns]
        for col in df.columns:
            if "tutar" in col or "kdv" in col: df[col] = df[col].apply(temizle_ve_sayiya_cevir)
            if "tarih" in col: df['tarih_dt'] = pd.to_datetime(df[col], dayfirst=True, errors='coerce')
        return df
    except: return pd.DataFrame()

# --- 5. GEMINI ---
@st.cache_data
def modelleri_getir():
    url = f"https://generativelanguage.googleapis.com/v1beta/models?key={API_KEY}"
    try:
        response = requests.get(url)
        data = response.json()
        flash = [m['name'].replace("models/", "") for m in data.get('models', []) if "flash" in m['name']]
        return flash + [m['name'].replace("models/", "") for m in data.get('models', []) if "flash" not in m['name']]
    except: return []

def dosyayi_hazirla(uploaded_file):
    bytes_data = uploaded_file.getvalue()
    mime_type = uploaded_file.type
    if mime_type == "application/pdf":
        return base64.b64encode(bytes_data).decode('utf-8'), mime_type
    else:
        img = Image.open(io.BytesIO(bytes_data)).convert("RGB")
        img.thumbnail((1024, 1024))
        buf = io.BytesIO()
        img.save(buf, "JPEG", quality=80)
        return base64.b64encode(buf.getvalue()).decode('utf-8'), "image/jpeg"

def gemini_ile_analiz_et(dosya_objesi, secilen_model):
    try:
        base64_data, mime_type = dosyayi_hazirla(dosya_objesi)
        url = f"https://generativelanguage.googleapis.com/v1beta/models/{secilen_model}:generateContent?key={API_KEY}"
        headers = {'Content-Type': 'application/json'}
        prompt = """Bu belgeyi analiz et. JSON dön:
        {"isyeri_adi": "...", "fiş_no": "...", "tarih": "GG.AA.YYYY", "kategori": "Gıda/Akaryakıt/Ofis/Diğer", "toplam_tutar": "0.00", "toplam_kdv": "0.00"}
        Tarih formatı mutlaka Gün.Ay.Yıl olsun."""
        payload = {"contents": [{"parts": [{"text": prompt}, {"inline_data": {"mime_type": mime_type, "data": base64_data}}]}]}
        response = requests.post(url, headers=headers, json=payload)
        if response.status_code != 200: return {"hata": "API Hatası"}
        metin = response.json()['candidates'][0]['content']['parts'][0]['text'].replace("```json", "").replace("```", "").strip()
        veri = json.loads(metin)
        veri["dosya_adi"] = dosya_objesi.name
        veri["_ham_dosya"] = dosya_objesi.getvalue()
        veri["_dosya_turu"] = "pdf" if mime_type == "application/pdf" else "jpg"
        return veri
    except Exception as e: return {"hata": str(e)}

def arsiv_olustur(veri_listesi):
    zip_buffer = io.BytesIO()
    with zipfile.ZipFile(zip_buffer, "w", zipfile.ZIP_DEFLATED) as zip_file:
        for veri in veri_listesi:
            if "_ham_dosya" in veri:
                try:
                    tarih_str = veri.get("tarih", "00.00.0000").replace("/", ".").replace("-", ".")
                    yer = veri.get("isyeri_adi", "Firma").replace(" ", "_").upper()[:15]
                    yer = "".join([c for c in yer if c.isalnum() or c in ('_','-')])
                    tutar = str(veri.get("toplam_tutar", "0")).replace(".", ",")
                    uzanti = veri.get("_dosya_turu", "jpg")
                    yeni_ad = f"{tarih_str}_{yer}_{tutar}TL.{uzanti}"
                    zip_file.writestr(yeni_ad, veri["_ham_dosya"])
                except:
                    zip_file.writestr(f"HATA_{veri.get('dosya_adi')}", veri["_ham_dosya"])
    return zip_buffer.getvalue()

# --- 6. ARAYÜZ (KEY TRICK İLE) ---
with st.sidebar:
    st.markdown("### 💼 Mihsap AI Pro")
    modeller = modelleri_getir()
    secilen_model = st.selectbox("Model", modeller) if modeller else "gemini-1.5-flash"
    hiz = st.slider("Hız", 1, 5, 3)
    
    # --- TEMİZLEME BUTONU (GÜNCELLENDİ) ---
    if st.button("❌ Ekranı Temizle"):
        # 1. Sonuçları sil
        if 'analiz_sonuclari' in st.session_state:
            del st.session_state['analiz_sonuclari']
        # 2. Dosya kutusunun ID'sini değiştir (Bu onu sıfırlar)
        st.session_state['uploader_key'] += 1
        st.rerun()

tab1, tab2 = st.tabs(["📤 Evrak Yükle", "📊 Raporlar"])

with tab1:
    st.header("Evrak İşleme")
    
    # DİKKAT: key parametresi dinamik oldu
    dosyalar = st.file_uploader(
        "Dosyaları Bırakın", 
        type=['jpg', 'png', 'jpeg', 'pdf'], 
        accept_multiple_files=True,
        key=f"uploader_{st.session_state['uploader_key']}" 
    )
    
    if dosyalar and st.button("🚀 İşlemi Başlat", type="primary"):
        tum_veriler = []
        bar = st.progress(0)
        
        with concurrent.futures.ThreadPoolExecutor(max_workers=hiz) as executor:
            future_to_file = {executor.submit(gemini_ile_analiz_et, d, secilen_model): d for d in dosyalar}
            completed = 0
            for future in concurrent.futures.as_completed(future_to_file):
                res = future.result()
                if "hata" not in res: tum_veriler.append(res)
                completed += 1
                bar.progress(completed / len(dosyalar))
                time.sleep(0.5)
        
        if tum_veriler:
            st.session_state['analiz_sonuclari'] = tum_veriler
            sheete_kaydet(tum_veriler)
            st.success(f"{len(tum_veriler)} evrak başarıyla işlendi ve veritabanına yazıldı.")
        else:
            st.error("Hiçbir veri işlenemedi.")

    if 'analiz_sonuclari' in st.session_state and st.session_state['analiz_sonuclari']:
        veriler = st.session_state['analiz_sonuclari']
        df = pd.DataFrame(veriler)
        df_gosterim = df.drop(columns=["_ham_dosya", "_dosya_turu"], errors='ignore')
        
        col1, col2 = st.columns(2)
        with col1:
            st.markdown("### 📂 Arşiv (ZIP)")
            zip_data = arsiv_olustur(veriler)
            st.download_button("📦 Arşivi İndir (ZIP)", zip_data, "arsiv.zip", "application/zip", type="primary")

        with col2:
            st.markdown("### 📊 Excel Raporları")
            df_muh = muhasebe_fisne_cevir(df_gosterim)
            
            buf1 = io.BytesIO()
            with pd.ExcelWriter(buf1, engine='openpyxl') as writer: df_gosterim.to_excel(writer, index=False)
            st.download_button("📥 Liste İndir", buf1.getvalue(), "liste.xlsx")
            
            buf2 = io.BytesIO()
            with pd.ExcelWriter(buf2, engine='openpyxl') as writer: df_muh.to_excel(writer, index=False)
            st.download_button("📥 Muhasebe Fişi", buf2.getvalue(), "muhasebe.xlsx")

        st.dataframe(df_gosterim, use_container_width=True)

with tab2:
    st.header("Yönetim Paneli")
    if st.button("🔄 Güncelle"): st.rerun()
    df_db = sheetten_veri_cek()
    if not df_db.empty:
        col_tutar = next((c for c in df_db.columns if "tutar" in c), None)
        if col_tutar:
            st.metric("Toplam Harcama", f"{df_db[col_tutar].sum():,.2f} ₺")
            col_kat = next((c for c in df_db.columns if "kategori" in c), None)
            if col_kat:
                fig = px.pie(df_db, values=col_tutar, names=col_kat, hole=0.4)
                st.plotly_chart(fig, use_container_width=True)

