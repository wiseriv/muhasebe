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
from pyzbar.pyzbar import decode
import cv2
import numpy as np

# --- 1. AYARLAR ---
st.set_page_config(page_title="Muhabese AI", layout="wide", page_icon="🏢")

def giris_kontrol():
    if 'giris_yapildi' not in st.session_state: st.session_state['giris_yapildi'] = False
    if not st.session_state['giris_yapildi']:
        c1, c2, c3 = st.columns([1,2,1])
        with c2:
            st.markdown("## 🔐 Muhabese AI | Giriş")
            with st.form("login"):
                sifre = st.text_input("Şifre", type="password")
                if st.form_submit_button("Giriş"):
                    if sifre == "12345":
                        st.session_state['giris_yapildi'] = True
                        st.rerun()
                    else: st.error("Hatalı Şifre")
        st.stop()
giris_kontrol()

API_KEY = st.secrets.get("GEMINI_API_KEY")
if not API_KEY: st.error("API Key Eksik!"); st.stop()

# --- 2. AYARLAR ---
if 'uploader_key' not in st.session_state: st.session_state['uploader_key'] = 0
if 'hesap_kodlari' not in st.session_state:
    st.session_state['hesap_kodlari'] = {
        "Gıda": "770.01", "Ulaşım": "770.02", "Kırtasiye": "770.03", 
        "Teknoloji": "770.04", "Konaklama": "770.05", "Diğer": "770.99",
        "KDV": "191.18", "Kasa": "100.01", "Banka": "102.01"
    }

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
    hk = st.session_state['hesap_kodlari']
    yevmiye = []
    for index, row in df_ham.iterrows():
        try:
            toplam = temizle_ve_sayiya_cevir(row.get('toplam_tutar', 0))
            kdv = temizle_ve_sayiya_cevir(row.get('toplam_kdv', 0))
            matrah = toplam - kdv
            tarih = str(row.get('tarih', datetime.now().strftime('%d.%m.%Y')))
            kategori = row.get('kategori', 'Diğer')
            gider_kodu = hk.get(kategori, hk["Diğer"])
            aciklama = f"{kategori} - {row.get('isyeri_adi', 'Evrak')}"
            
            if matrah > 0: yevmiye.append({"Tarih": tarih, "Hesap Kodu": gider_kodu, "Açıklama": aciklama, "Borç": matrah, "Alacak": 0})
            if kdv > 0: yevmiye.append({"Tarih": tarih, "Hesap Kodu": hk["KDV"], "Açıklama": "KDV", "Borç": kdv, "Alacak": 0})
            alacak_hesabi = hk["Banka"] if "Ekstre" in str(row.get('dosya_adi','')) else hk["Kasa"]
            yevmiye.append({"Tarih": tarih, "Hesap Kodu": alacak_hesabi, "Açıklama": "Ödeme", "Borç": 0, "Alacak": toplam})
        except: continue
    return pd.DataFrame(yevmiye)

# --- 4. SHEETS ---
@st.cache_resource
def sheets_baglantisi_kur():
    if "gcp_service_account" not in st.secrets: return None
    try:
        scope = ["https://spreadsheets.google.com/feeds", "https://www.googleapis.com/auth/drive"]
        creds = ServiceAccountCredentials.from_json_keyfile_dict(dict(st.secrets["gcp_service_account"]), scope)
        return gspread.authorize(creds)
    except: return None

def musteri_listesini_getir():
    client = sheets_baglantisi_kur()
    if not client: return ["Varsayılan Müşteri"]
    try:
        # Veritabanı adı değişti: Muhabese Veritabanı
        sheet = client.open("Muhabese Veritabanı")
        try: ws = sheet.worksheet("Musteriler")
        except: ws = sheet.add_worksheet("Musteriler", 100, 2); ws.append_row(["Müşteri", "Tarih"]); ws.append_row(["Varsayılan Müşteri", str(datetime.now())])
        return ws.col_values(1)[1:] or ["Varsayılan Müşteri"]
    except: return ["Varsayılan Müşteri"]

def yeni_musteri_ekle(ad):
    client = sheets_baglantisi_kur()
    if not client: return False
    try:
        sheet = client.open("Muhabese Veritabanı")
        ws = sheet.worksheet("Musteriler")
        if ad in ws.col_values(1): return "Mevcut"
        ws.append_row([ad, str(datetime.now())])
        try: sheet.add_worksheet(ad, 1000, 10).append_row(["Dosya", "İşyeri", "Fiş No", "Tarih", "Kategori", "Tutar", "KDV", "Zaman", "Durum", "QR"])
        except: pass
        return True
    except Exception as e: return str(e)

def musteri_sil(ad):
    client = sheets_baglantisi_kur()
    if not client: return False
    try:
        sheet = client.open("Muhabese Veritabanı")
        ws = sheet.worksheet("Musteriler")
        cell = ws.find(ad)
        if cell: ws.delete_rows(cell.row)
        try: sheet.del_worksheet(sheet.worksheet(ad))
        except: pass
        return True
    except Exception as e: return str(e)

def sheete_kaydet(veri, musteri):
    client = sheets_baglantisi_kur()
    if not client: return False
    try:
        sheet = client.open("Muhabese Veritabanı")
        try: ws = sheet.worksheet(musteri)
        except: ws = sheet.add_worksheet(musteri, 1000, 10)
        rows = []
        for v in veri:
            durum = "✅" if float(str(v.get('toplam_tutar',0)).replace(',','.')) > 0 else "⚠️"
            qr_durumu = "📱QR" if v.get("qr_gecerli") else "-"
            rows.append([v.get("dosya_adi"), v.get("isyeri_adi"), v.get("fiş_no"), v.get("tarih"), v.get("kategori", "Diğer"), str(v.get("toplam_tutar", "0")), str(v.get("toplam_kdv", "0")), datetime.now().strftime("%Y-%m-%d %H:%M:%S"), durum, qr_durumu])
        ws.append_rows(rows)
        return True
    except: return False

def sheetten_veri_cek(musteri):
    client = sheets_baglantisi_kur()
    if not client: return pd.DataFrame()
    try:
        sheet = client.open("Muhabese Veritabanı")
        ws = sheet.worksheet(musteri)
        data = ws.get_all_records()
        if not data: return pd.DataFrame()
        df = pd.DataFrame(data)
        df.columns = [c.strip().lower().replace(" ", "") for c in df.columns]
        col_tutar = next((c for c in df.columns if "tutar" in c), None)
        if col_tutar: df[col_tutar] = df[col_tutar].apply(temizle_ve_sayiya_cevir)
        return df
    except: return pd.DataFrame()

# --- 5. GEMINI & QR ---
@st.cache_data
def modelleri_getir():
    url = f"https://generativelanguage.googleapis.com/v1beta/models?key={API_KEY}"
    try:
        response = requests.get(url)
        data = response.json()
        flash = [m['name'].replace("models/", "") for m in data.get('models', []) if "flash" in m['name']]
        return flash + [m['name'].replace("models/", "") for m in data.get('models', []) if "flash" not in m['name']]
    except: return []

def qr_kodu_oku_ve_filtrele(image_bytes):
    try:
        nparr = np.frombuffer(image_bytes, np.uint8)
        img = cv2.imdecode(nparr, cv2.IMREAD_COLOR)
        decoded_objects = decode(img)
        for obj in decoded_objects:
            raw_data = obj.data.decode("utf-8")
            if "http" in raw_data or "vkno" in raw_data.lower() or len(raw_data) > 50: return raw_data 
            else: continue
        return None
    except: return None

def dosyayi_hazirla(uploaded_file):
    bytes_data = uploaded_file.getvalue()
    mime_type = uploaded_file.type
    if mime_type == "application/pdf": return base64.b64encode(bytes_data).decode('utf-8'), mime_type
    img = Image.open(io.BytesIO(bytes_data)).convert("RGB")
    img.thumbnail((1024, 1024))
    buf = io.BytesIO()
    img.save(buf, "JPEG", quality=80)
    return base64.b64encode(buf.getvalue()).decode('utf-8'), "image/jpeg"

def gemini_ile_analiz_et(dosya_objesi, secilen_model, mod="fis"):
    try:
        qr_data = None
        if dosya_objesi.type != "application/pdf":
            qr_data = qr_kodu_oku_ve_filtrele(dosya_objesi.getvalue())
        
        base64_data, mime_type = dosyayi_hazirla(dosya_objesi)
        url = f"https://generativelanguage.googleapis.com/v1beta/models/{secilen_model}:generateContent?key={API_KEY}"
        headers = {'Content-Type': 'application/json'}
        
        qr_bilgisi = ""
        if qr_data:
            qr_bilgisi = f"\n[İPUCU]: Belgede resmi bir QR kod bulundu: '{qr_data}'. Eğer içinde tutar/tarih varsa bunu kullan."

        if mod == "fis":
            prompt = f"""Bu belgeyi bir Mali Müşavir hassasiyetiyle analiz et. {qr_bilgisi}
            GÖREVİN: Harcamanın gerçek "Kategori"sini bulmak.
            
            DİKKAT ETMEN GEREKEN TUZAKLAR:
            1. Firma Adına Aldanma: "Ofel Turizm" yazabilir ama logo "HepEğitim" ise ve ürün "Alice" ise bu bir KİTAPTIR (Kırtasiye).
            2. Vergi Kodlarına Bak: Faturanın altında "13/n Maddesi" veya "KDV İstisnası" yazıyorsa bu genellikle Kitap/Yayın demektir.
            3. Ürün Adını Yorumla: "Alice" bir kitap ismidir. "Benzin", "Motorin" akaryakıttır. "Adana Kebap" gıdadır.
            
            JSON: {{"isyeri_adi": "...", "fiş_no": "...", "tarih": "GG.AA.YYYY", "kategori": "Gıda/Akaryakıt/Kırtasiye/Teknoloji/Konaklama/Diğer", "toplam_tutar": "0.00", "toplam_kdv": "0.00"}}
            Tarih formatı Gün.Ay.Yıl olsun.
            """
        else:
            prompt = """Kredi kartı ekstresi satırları. JSON Liste: [{"isyeri_adi": "...", "tarih": "GG.AA.YYYY", "kategori": "...", "toplam_tutar": "0.00", "toplam_kdv": "0"}, ...]"""

        payload = {"contents": [{"parts": [{"text": prompt}, {"inline_data": {"mime_type": mime_type, "data": base64_data}}]}]}
        response = requests.post(url, headers=headers, json=payload)
        if response.status_code != 200: return {"hata": "API Hatası"}
        
        metin = response.json()['candidates'][0]['content']['parts'][0]['text'].replace("```json", "").replace("```", "").strip()
        veri = json.loads(metin)
        
        if isinstance(veri, list):
            for v in veri: 
                v["dosya_adi"] = f"Ekstre_{dosya_objesi.name}"
                v["qr_gecerli"] = False
            return veri
        else:
            veri["dosya_adi"] = dosya_objesi.name
            veri["qr_gecerli"] = True if qr_data else False
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
                    tarih = veri.get("tarih", "00.00.0000").replace("/", ".").replace("-", ".")
                    yer = "".join([c for c in veri.get("isyeri_adi","").upper() if c.isalnum()])[:10]
                    tutar = str(veri.get("toplam_tutar", "0")).replace(".", ",")
                    ad = f"{tarih}_{yer}_{tutar}TL.{veri.get('_dosya_turu','jpg')}"
                    zip_file.writestr(ad, veri["_ham_dosya"])
                except: zip_file.writestr(f"HATA_{veri.get('dosya_adi')}", veri["_ham_dosya"])
    return zip_buffer.getvalue()

# --- 6. ARAYÜZ ---
with st.sidebar:
    st.title("🏢 Muhabese AI")
    
    st.markdown("### 👥 Müşteri")
    musteriler = musteri_listesini_getir()
    secili = st.selectbox("Aktif Müşteri", musteriler)
    
    with st.expander("➕ Ekle / ➖ Sil"):
        yeni = st.text_input("Yeni Firma Adı")
        if st.button("Ekle"):
            res = yeni_musteri_ekle(yeni)
            if res==True: st.success("Eklendi!"); time.sleep(1); st.rerun()
            else: st.error(res)
        
        sil = st.selectbox("Silinecek", [m for m in musteriler if m!="Varsayılan Müşteri"])
        if st.button("Sil"):
            musteri_sil(sil)
            st.success("Silindi!"); time.sleep(1); st.rerun()

    st.divider()
    modeller = modelleri_getir()
    model = st.selectbox("AI Modeli", modeller) if modeller else "gemini-1.5-flash"
    hiz = st.slider("Hız", 1, 5, 3)
    
    if st.button("❌ Temizle"):
        st.session_state['uploader_key'] += 1
        if 'analiz_sonuclari' in st.session_state: del st.session_state['analiz_sonuclari']
        st.rerun()

t1, t2, t3 = st.tabs([f"📤 {secili}", "📊 Rapor", "⚙️ Ayar"])

with t1:
    st.header("Evrak İşleme")
    c1, c2 = st.columns(2)
    with c1: fisler = st.file_uploader("Fiş / Fatura", type=['jpg','png','pdf'], accept_multiple_files=True, key=f"f_{st.session_state['uploader_key']}")
    with c2: ekstre = st.file_uploader("Ekstre", type=['pdf','jpg'], accept_multiple_files=True, key=f"e_{st.session_state['uploader_key']}")
    
    if st.button("🚀 Başlat", type="primary"):
        tum = []
        bar = st.progress(0)
        
        if fisler:
            with concurrent.futures.ThreadPoolExecutor(max_workers=hiz) as exe:
                futures = {exe.submit(gemini_ile_analiz_et, d, model, "fis"): d for d in fisler}
                for f in concurrent.futures.as_completed(futures):
                    r = f.result()
                    if "hata" not in r: tum.append(r)
                    bar.progress(100)
        
        if ekstre:
            with st.spinner("Ekstre okunuyor..."):
                for d in ekstre:
                    r = gemini_ile_analiz_et(d, model, "ekstre")
                    if isinstance(r, list): tum.extend(r)
        
        if tum:
            st.session_state['analiz_sonuclari'] = tum
            sheete_kaydet(tum, secili)
            st.success(f"✅ {len(tum)} kayıt işlendi.")

    if 'analiz_sonuclari' in st.session_state:
        dt = st.session_state['analiz_sonuclari']
        df = pd.DataFrame(dt)
        st.dataframe(df.drop(columns=["_ham_dosya", "_dosya_turu", "qr_data"], errors='ignore'), use_container_width=True)
        
        col1, col2 = st.columns(2)
        with col1: st.download_button("📦 ZIP Arşiv", arsiv_olustur(dt), f"{secili}_arsiv.zip", "application/zip")
        with col2:
            df_m = muhasebe_fisne_cevir(df)
            buf = io.BytesIO()
            with pd.ExcelWriter(buf, engine='openpyxl') as w: df_m.to_excel(w, index=False)
            st.download_button("📥 Muhasebe Fişi", buf.getvalue(), f"{secili}_fiş.xlsx")

with t2:
    st.header("Yönetim Paneli")
    if st.button("🔄 Güncelle"): st.rerun()
    df = sheetten_veri_cek(secili)
    if not df.empty and 'tutar' in df.columns:
        st.metric("Toplam", f"{df['tutar'].sum():,.2f} ₺")
        st.dataframe(df, use_container_width=True)
    else: st.info("Veri yok.")

with t3:
    st.header("Ayarlar")
    hk = st.session_state['hesap_kodlari']
    c1, c2 = st.columns(2)
    with c1:
        hk["Gıda"] = st.text_input("Gıda", hk["Gıda"])
        hk["Ulaşım"] = st.text_input("Ulaşım", hk["Ulaşım"])
    with c2:
        hk["KDV"] = st.text_input("KDV", hk["KDV"])
        hk["Kasa"] = st.text_input("Kasa", hk["Kasa"])
    if st.button("Kaydet"): st.success("Kaydedildi!")
