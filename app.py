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
import plotly.express as px # Grafik kütüphanesi

# --- AYARLAR ---
st.set_page_config(page_title="Mihsap AI - Dashboard", layout="wide", page_icon="📊")

# GÜVENLİK
def giris_kontrol():
    if 'giris_yapildi' not in st.session_state: st.session_state['giris_yapildi'] = False
    if not st.session_state['giris_yapildi']:
        st.markdown("## 🔐 Panel Girişi")
        if st.text_input("Şifre", type="password") == "12345":
            st.session_state['giris_yapildi'] = True
            st.rerun()
        st.stop()
giris_kontrol()

API_KEY = st.secrets.get("GEMINI_API_KEY")
if not API_KEY: st.error("API Key Eksik!"); st.stop()

# --- FONKSİYONLAR ---
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
    dosya_adi = dosya_objesi.name
    try:
        base64_image = resmi_hazirla(dosya_objesi.getvalue())
        url = f"https://generativelanguage.googleapis.com/v1beta/models/{secilen_model}:generateContent?key={API_KEY}"
        headers = {'Content-Type': 'application/json'}
        
        prompt_text = """
        Bu fişi analiz et. JSON formatında yanıt ver.
        "kategori" alanını şunlardan biri seç: [Gıda/Market, Akaryakıt/Ulaşım, Kırtasiye/Ofis, Teknoloji, Konaklama, Diğer]
        JSON: {"isyeri_adi": "Ad", "fiş_no": "No", "tarih": "YYYY-AA-GG", "kategori": "Kat", "toplam_tutar": "0.00", "toplam_kdv": "0.00"}
        Tarih formatına dikkat et: Yıl-Ay-Gün (ISO format) olsun ki grafik çizebilelim.
        """
        
        payload = {
            "contents": [{"parts": [{"text": prompt_text}, {"inline_data": {"mime_type": "image/jpeg", "data": base64_image}}]}]
        }
        response = requests.post(url, headers=headers, json=payload)
        if response.status_code != 200: return {"dosya_adi": dosya_adi, "hata": f"Hata {response.status_code}"}
        metin = response.json()['candidates'][0]['content']['parts'][0]['text'].replace("```json", "").replace("```", "").strip()
        veri = json.loads(metin)
        veri["dosya_adi"] = dosya_adi
        return veri
    except Exception as e: return {"dosya_adi": dosya_adi, "hata": str(e)}

def muhasebe_fisne_cevir(df_ham):
    yevmiye_satirlari = []
    for index, row in df_ham.iterrows():
        try:
            toplam = float(str(row.get('toplam_tutar', 0)).replace(',', '.'))
            kdv = float(str(row.get('toplam_kdv', 0)).replace(',', '.'))
            matrah = toplam - kdv
            tarih = row.get('tarih', datetime.now().strftime('%d.%m.%Y'))
            aciklama = f"{row.get('kategori', '')} - {row.get('isyeri_adi', '')}"
            
            if matrah > 0: yevmiye_satirlari.append({"Tarih": tarih, "Hesap Kodu": "770.01", "Açıklama": aciklama, "Borç": matrah, "Alacak": 0})
            if kdv > 0: yevmiye_satirlari.append({"Tarih": tarih, "Hesap Kodu": "191.18", "Açıklama": "KDV", "Borç": kdv, "Alacak": 0})
            yevmiye_satirlari.append({"Tarih": tarih, "Hesap Kodu": "100.01", "Açıklama": "Ödeme", "Borç": 0, "Alacak": toplam})
        except: continue
    return pd.DataFrame(yevmiye_satirlari)

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
            rows.append([v.get("dosya_adi"), v.get("isyeri_adi"), v.get("fiş_no"), v.get("tarih"), v.get("kategori"), v.get("toplam_tutar"), v.get("toplam_kdv"), datetime.now().strftime("%Y-%m-%d %H:%M:%S")])
        sheet.append_rows(rows)
        return True
    except: return False

# --- ARAYÜZ VE SEKMELER ---
with st.sidebar:
    st.title("Mihsap AI")
    modeller = modelleri_getir()
    model = st.selectbox("Model", modeller) if modeller else "gemini-1.5-flash"
    hiz = st.slider("Hız", 1, 5, 3)

# İki Sekme Oluşturuyoruz
tab1, tab2 = st.tabs(["📤 Fiş İşlemleri", "📊 Patron Paneli (Dashboard)"])

# --- SEKME 1: FİŞ YÜKLEME (ESKİ EKRAN) ---
with tab1:
    st.header("Fiş Yükleme ve Muhasebeleştirme")
    dosyalar = st.file_uploader("Fişleri Yükle", type=['jpg', 'png', 'jpeg'], accept_multiple_files=True)

    if dosyalar and st.button("🚀 Analiz Et"):
        tum_veriler = []
        bar = st.progress(0)
        
        with concurrent.futures.ThreadPoolExecutor(max_workers=hiz) as executor:
            future_to_file = {executor.submit(gemini_ile_analiz_et, d, model): d for d in dosyalar}
            completed = 0
            for future in concurrent.futures.as_completed(future_to_file):
                res = future.result()
                if "hata" not in res: tum_veriler.append(res)
                completed += 1
                bar.progress(completed / len(dosyalar))
                time.sleep(0.5)

        if tum_veriler:
            df = pd.DataFrame(tum_veriler)
            
            # Kayıt ve Session State (Grafikler için veriyi hafızada tut)
            sheete_kaydet(tum_veriler)
            st.session_state['son_analiz'] = df # Veriyi hafızaya al
            
            st.success("✅ İşlem Tamam!")
            
            col1, col2 = st.columns(2)
            with col1:
                st.dataframe(df, use_container_width=True)
            with col2:
                df_muh = muhasebe_fisne_cevir(df)
                st.dataframe(df_muh, use_container_width=True)
                buf = io.BytesIO()
                with pd.ExcelWriter(buf, engine='openpyxl') as writer: df_muh.to_excel(writer, index=False)
                st.download_button("📥 Muhasebe Fişi İndir", buf.getvalue(), "muhasebe.xlsx", type="primary")

# --- SEKME 2: DASHBOARD (YENİ EKRAN) ---
with tab2:
    st.header("📊 Finansal Özet ve Analiz")
    
    # Veriyi nereden alacağız? Ya az önce yüklenenden ya da Google Sheets'ten çekebiliriz.
    # Şimdilik az önce yüklenen veriden gösterelim (Hız için).
    
    if 'son_analiz' in st.session_state and not st.session_state['son_analiz'].empty:
        df_dash = st.session_state['son_analiz'].copy()
        
        # Sayısal verileri düzelt
        df_dash['toplam_tutar'] = df_dash['toplam_tutar'].astype(str).str.replace(',', '.').astype(float)
        df_dash['toplam_kdv'] = df_dash['toplam_kdv'].astype(str).str.replace(',', '.').astype(float)
        
        # --- 1. ÖZET KARTLARI ---
        total_spend = df_dash['toplam_tutar'].sum()
        total_kdv = df_dash['toplam_kdv'].sum()
        top_category = df_dash.groupby('kategori')['toplam_tutar'].sum().idxmax()
        
        c1, c2, c3 = st.columns(3)
        c1.metric("Toplam Harcama", f"{total_spend:,.2f} ₺")
        c2.metric("Ödenen KDV", f"{total_kdv:,.2f} ₺")
        c3.metric("En Çok Harcanan", top_category)
        
        st.divider()
        
        # --- 2. GRAFİKLER ---
        g1, g2 = st.columns(2)
        
        with g1:
            st.subheader("Kategori Bazlı Harcama")
            fig_pie = px.pie(df_dash, values='toplam_tutar', names='kategori', hole=0.4)
            st.plotly_chart(fig_pie, use_container_width=True)
            
        with g2:
            st.subheader("İşyerine Göre Dağılım")
            fig_bar = px.bar(df_dash, x='isyeri_adi', y='toplam_tutar', color='kategori')
            st.plotly_chart(fig_bar, use_container_width=True)
            
    else:
        st.info("Henüz veri yok. Lütfen 'Fiş İşlemleri' sekmesinden fiş yükleyip analiz edin.")
        st.caption("Not: İleride bu ekranı doğrudan Google Sheets'e bağlayıp tüm geçmişi gösterebiliriz.")
