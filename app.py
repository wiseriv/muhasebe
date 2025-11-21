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

# --- 1. SAYFA AYARLARI (PROFESYONEL GÖRÜNÜM) ---
st.set_page_config(page_title="Mihsap AI", layout="wide", page_icon="💼")

# --- 2. GÜVENLİK DUVARI ---
def giris_kontrol():
    if 'giris_yapildi' not in st.session_state: st.session_state['giris_yapildi'] = False
    if not st.session_state['giris_yapildi']:
        c1, c2, c3 = st.columns([1,2,1])
        with c2:
            st.markdown("## 🔐 Mihsap AI | Yönetici Girişi")
            sifre = st.text_input("Lütfen Şifrenizi Giriniz", type="password")
            if st.button("Giriş Yap"):
                if sifre == "12345":
                    st.session_state['giris_yapildi'] = True
                    st.rerun()
                else:
                    st.error("Hatalı Şifre")
        st.stop()
giris_kontrol()

API_KEY = st.secrets.get("GEMINI_API_KEY")
if not API_KEY: st.error("Sistem Hatası: API Anahtarı Bulunamadı."); st.stop()

# --- 3. YARDIMCI MOTORLAR ---
def temizle_ve_sayiya_cevir(deger):
    if pd.isna(deger) or deger == "": return 0.0
    try:
        s = str(deger).replace("₺", "").replace("TL", "").strip()
        if "," in s and "." in s: s = s.replace(".", "").replace(",", ".")
        elif "," in s: s = s.replace(",", ".")
        return float(s)
    except: return 0.0

def muhasebe_fisne_cevir(df_ham):
    """Verileri 770-191-100 muhasebe kodlarına ayırır."""
    yevmiye_satirlari = []
    for index, row in df_ham.iterrows():
        try:
            toplam = temizle_ve_sayiya_cevir(row.get('toplam_tutar', 0))
            kdv = temizle_ve_sayiya_cevir(row.get('toplam_kdv', 0))
            matrah = toplam - kdv
            tarih = row.get('tarih', datetime.now().strftime('%d.%m.%Y'))
            aciklama = f"{row.get('kategori', 'Genel')} - {row.get('isyeri_adi', 'Fiş')}"
            
            if matrah > 0: yevmiye_satirlari.append({"Tarih": tarih, "Hesap Kodu": "770.01", "Açıklama": aciklama, "Borç": matrah, "Alacak": 0})
            if kdv > 0: yevmiye_satirlari.append({"Tarih": tarih, "Hesap Kodu": "191.18", "Açıklama": "KDV", "Borç": kdv, "Alacak": 0})
            yevmiye_satirlari.append({"Tarih": tarih, "Hesap Kodu": "100.01", "Açıklama": "Ödeme", "Borç": 0, "Alacak": toplam})
        except: continue
    return pd.DataFrame(yevmiye_satirlari)

# --- 4. GOOGLE SHEETS (VERİTABANI) ---
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
        # Başlık temizliği
        df.columns = [c.strip().lower().replace(" ", "").replace("_", "") for c in df.columns]
        # Veri temizliği
        for col in df.columns:
            if "tutar" in col or "kdv" in col: df[col] = df[col].apply(temizle_ve_sayiya_cevir)
            if "tarih" in col: df['tarih_dt'] = pd.to_datetime(df[col], dayfirst=True, errors='coerce')
        return df
    except: return pd.DataFrame()

# --- 5. YAPAY ZEKA (GEMINI) ---
@st.cache_data
def modelleri_getir():
    url = f"https://generativelanguage.googleapis.com/v1beta/models?key={API_KEY}"
    try:
        response = requests.get(url)
        data = response.json()
        flash = [m['name'].replace("models/", "") for m in data.get('models', []) if "flash" in m['name']]
        diger = [m['name'].replace("models/", "") for m in data.get('models', []) if "flash" not in m['name']]
        return flash + diger
    except: return []

def resmi_hazirla(image_bytes):
    img = Image.open(io.BytesIO(image_bytes)).convert("RGB")
    img.thumbnail((1024, 1024))
    buf = io.BytesIO()
    img.save(buf, "JPEG", quality=80)
    return base64.b64encode(buf.getvalue()).decode('utf-8')

def gemini_ile_analiz_et(dosya_objesi, secilen_model):
    try:
        base64_image = resmi_hazirla(dosya_objesi.getvalue())
        url = f"https://generativelanguage.googleapis.com/v1beta/models/{secilen_model}:generateContent?key={API_KEY}"
        headers = {'Content-Type': 'application/json'}
        prompt = """Bu fişi analiz et. JSON formatında dön:
        {"isyeri_adi": "...", "fiş_no": "...", "tarih": "GG.AA.YYYY", "kategori": "Gıda/Akaryakıt/Ofis/Teknoloji/Diğer", "toplam_tutar": "0.00", "toplam_kdv": "0.00"}"""
        payload = {"contents": [{"parts": [{"text": prompt}, {"inline_data": {"mime_type": "image/jpeg", "data": base64_image}}]}]}
        response = requests.post(url, headers=headers, json=payload)
        if response.status_code != 200: return {"hata": "Okunamadı"}
        metin = response.json()['candidates'][0]['content']['parts'][0]['text'].replace("```json", "").replace("```", "").strip()
        veri = json.loads(metin)
        veri["dosya_adi"] = dosya_objesi.name
        return veri
    except: return {"hata": "Hata"}

# --- 6. ARAYÜZ TASARIMI ---
with st.sidebar:
    st.image("https://cdn-icons-png.flaticon.com/512/2910/2910270.png", width=50) # Temsili Logo
    st.markdown("### Mihsap AI")
    st.caption("Yapay Zeka Destekli Muhasebe")
    st.divider()
    
    modeller = modelleri_getir()
    secilen_model = st.selectbox("Yapay Zeka Modeli", modeller) if modeller else "gemini-1.5-flash"
    hiz = st.slider("İşlem Hızı (Thread)", 1, 5, 3)
    st.divider()
    st.info("v11.0 - Stable Release")

# Ana Sayfa Sekmeleri
tab_islem, tab_rapor = st.tabs(["📤 Fiş & Fatura İşlemleri", "📊 Yönetim Paneli"])

# --- SEKME 1: İŞLEM MERKEZİ ---
with tab_islem:
    st.subheader("Yeni Evrak Girişi")
    st.markdown("Yüklediğiniz fişler yapay zeka ile okunur, kategorize edilir ve muhasebeleştirilir.")
    
    dosyalar = st.file_uploader("Dosyaları Buraya Sürükleyin", type=['jpg', 'png', 'jpeg'], accept_multiple_files=True)
    
    if dosyalar and st.button("Otomasyonu Başlat", type="primary"):
        tum_veriler = []
        bar = st.progress(0)
        st.toast("Yapay zeka motoru çalışıyor...", icon="🤖")
        
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
            df = pd.DataFrame(tum_veriler)
            st.success(f"{len(tum_veriler)} adet evrak başarıyla işlendi.")
            
            # Veritabanına Yaz
            if sheete_kaydet(tum_veriler):
                st.toast("Veriler buluta kaydedildi!", icon="☁️")
            
            # Sonuçları Göster
            col1, col2 = st.columns(2)
            
            with col1:
                st.markdown("#### 📋 Okunan Veriler")
                st.dataframe(df, use_container_width=True, height=300)
                
            with col2:
                st.markdown("#### 💼 Muhasebe Fişi (Önizleme)")
                df_muh = muhasebe_fisne_cevir(df)
                st.dataframe(df_muh, use_container_width=True, height=300)
                
                # İndirme Butonları
                c_btn1, c_btn2 = st.columns(2)
                buf1 = io.BytesIO()
                with pd.ExcelWriter(buf1, engine='openpyxl') as writer: df.to_excel(writer, index=False)
                c_btn1.download_button("📥 Excel Listesi", buf1.getvalue(), "liste.xlsx")
                
                buf2 = io.BytesIO()
                with pd.ExcelWriter(buf2, engine='openpyxl') as writer: df_muh.to_excel(writer, index=False)
                c_btn2.download_button("📥 Luca/Zirve Fişi", buf2.getvalue(), "muhasebe_fisi.xlsx", type="primary")

# --- SEKME 2: YÖNETİM PANELİ ---
with tab_rapor:
    st.subheader("Finansal Durum Özeti")
    
    if st.button("🔄 Verileri Güncelle"):
        st.cache_resource.clear()
        st.rerun()

    df_db = sheetten_veri_cek()
    
    if not df_db.empty:
        # Sütun eşleştirme
        col_tutar = next((c for c in df_db.columns if "tutar" in c), None)
        col_kdv = next((c for c in df_db.columns if "kdv" in c), None)
        col_kat = next((c for c in df_db.columns if "kategori" in c), None)
        col_date = 'tarih_dt' if 'tarih_dt' in df_db.columns else None

        if col_tutar:
            # Kartlar
            toplam_harcama = df_db[col_tutar].sum()
            toplam_kdv = df_db[col_kdv].sum() if col_kdv else 0
            en_cok_kategori = df_db.groupby(col_kat)[col_tutar].sum().idxmax() if col_kat else "-"
            
            m1, m2, m3, m4 = st.columns(4)
            m1.metric("Toplam İşlem", f"{len(df_db)} Adet")
            m2.metric("Toplam Harcama", f"{toplam_harcama:,.2f} ₺")
            m3.metric("Toplam KDV", f"{toplam_kdv:,.2f} ₺")
            m4.metric("Lider Kategori", en_cok_kategori)
            
            st.divider()
            
            # Grafikler
            g1, g2 = st.columns(2)
            with g1:
                if col_kat:
                    fig_pie = px.pie(df_db, values=col_tutar, names=col_kat, title="Harcama Dağılımı (Kategori)", hole=0.4)
                    st.plotly_chart(fig_pie, use_container_width=True)
            with g2:
                if col_date:
                    daily = df_db.groupby(col_date)[col_tutar].sum().reset_index()
                    fig_line = px.line(daily, x=col_date, y=col_tutar, title="Zaman İçindeki Harcama Trendi", markers=True)
                    st.plotly_chart(fig_line, use_container_width=True)
            
            with st.expander("Detaylı Veritabanı Kayıtlarını Gör"):
                st.dataframe(df_db, use_container_width=True)
        else:
            st.warning("Veri var ama 'Tutar' sütunu okunamadı.")
    else:
        st.info("Henüz veritabanında kayıt bulunmuyor.")
