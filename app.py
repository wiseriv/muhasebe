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

# --- 1. AYARLAR VE GÜVENLİK ---
st.set_page_config(page_title="Mihsap AI", layout="wide", page_icon="🏢")

def giris_kontrol():
    if 'giris_yapildi' not in st.session_state: st.session_state['giris_yapildi'] = False
    if not st.session_state['giris_yapildi']:
        c1, c2, c3 = st.columns([1,2,1])
        with c2:
            st.markdown("## 🔐 Mihsap AI | Giriş")
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

# --- 2. HESAP PLANI AYARLARI ---
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

# --- 4. GOOGLE SHEETS BAĞLANTISI ---
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
        sheet = client.open("Mihsap Veritabanı")
        try: ws = sheet.worksheet("Musteriler")
        except:
            ws = sheet.add_worksheet(title="Musteriler", rows=100, cols=2)
            ws.append_row(["Müşteri Adı", "Tarih"])
            ws.append_row(["Varsayılan Müşteri", str(datetime.now())])
        
        musteriler = ws.col_values(1)[1:]
        return musteriler if musteriler else ["Varsayılan Müşteri"]
    except: return ["Varsayılan Müşteri"]

def yeni_musteri_ekle(musteri_adi):
    client = sheets_baglantisi_kur()
    if not client: return False
    try:
        sheet = client.open("Mihsap Veritabanı")
        ws_main = sheet.worksheet("Musteriler")
        if musteri_adi in ws_main.col_values(1): return "Mevcut"
        ws_main.append_row([musteri_adi, str(datetime.now())])
        try:
            new_ws = sheet.add_worksheet(title=musteri_adi, rows=1000, cols=10)
            new_ws.append_row(["Dosya Adı", "İşyeri", "Fiş No", "Tarih", "Kategori", "Tutar", "KDV", "Zaman", "Durum"])
        except: pass
        return True
    except Exception as e: return str(e)

# --- YENİ: MÜŞTERİ SİLME FONKSİYONU ---
def musteri_sil(musteri_adi):
    client = sheets_baglantisi_kur()
    if not client: return False
    try:
        sheet = client.open("Mihsap Veritabanı")
        
        # 1. Listeden Sil
        ws_main = sheet.worksheet("Musteriler")
        cell = ws_main.find(musteri_adi)
        if cell: ws_main.delete_rows(cell.row)
        
        # 2. Sekmeyi (Worksheet) Sil
        try:
            ws_cust = sheet.worksheet(musteri_adi)
            sheet.del_worksheet(ws_cust)
        except: pass # Sekme zaten yoksa sorun yok
        
        return True
    except Exception as e: return str(e)

def sheete_kaydet(veri_listesi, musteri_adi):
    client = sheets_baglantisi_kur()
    if not client: return False
    try:
        sheet = client.open("Mihsap Veritabanı")
        try: ws = sheet.worksheet(musteri_adi)
        except: ws = sheet.add_worksheet(title=musteri_adi, rows=1000, cols=10)
        
        rows = []
        for v in veri_listesi:
            durum = "✅" if float(str(v.get('toplam_tutar',0)).replace(',','.')) > 0 else "⚠️"
            rows.append([v.get("dosya_adi"), v.get("isyeri_adi"), v.get("fiş_no"), v.get("tarih"), v.get("kategori", "Diğer"), str(v.get("toplam_tutar", "0")), str(v.get("toplam_kdv", "0")), datetime.now().strftime("%Y-%m-%d %H:%M:%S"), durum])
        ws.append_rows(rows)
        return True
    except: return False

def sheetten_veri_cek(musteri_adi):
    client = sheets_baglantisi_kur()
    if not client: return pd.DataFrame()
    try:
        sheet = client.open("Mihsap Veritabanı")
        ws = sheet.worksheet(musteri_adi)
        data = ws.get_all_records()
        if not data: return pd.DataFrame()
        df = pd.DataFrame(data)
        df.columns = [c.strip().lower().replace(" ", "") for c in df.columns]
        col_tutar = next((c for c in df.columns if "tutar" in c), None)
        if col_tutar: df[col_tutar] = df[col_tutar].apply(temizle_ve_sayiya_cevir)
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
    if mime_type == "application/pdf": return base64.b64encode(bytes_data).decode('utf-8'), mime_type
    img = Image.open(io.BytesIO(bytes_data)).convert("RGB")
    img.thumbnail((1024, 1024))
    buf = io.BytesIO()
    img.save(buf, "JPEG", quality=80)
    return base64.b64encode(buf.getvalue()).decode('utf-8'), "image/jpeg"

def gemini_ile_analiz_et(dosya_objesi, secilen_model, mod="fis"):
    try:
        base64_data, mime_type = dosyayi_hazirla(dosya_objesi)
        url = f"https://generativelanguage.googleapis.com/v1beta/models/{secilen_model}:generateContent?key={API_KEY}"
        headers = {'Content-Type': 'application/json'}
        
        if mod == "fis":
            prompt = """Bu belgeyi analiz et. JSON dön:
            {"isyeri_adi": "...", "fiş_no": "...", "tarih": "GG.AA.YYYY", "kategori": "Gıda/Ulaşım/Kırtasiye/Teknoloji/Konaklama/Diğer", "toplam_tutar": "0.00", "toplam_kdv": "0.00"}
            Tarih formatı Gün.Ay.Yıl olsun."""
        else:
            prompt = """Kredi kartı ekstresi. Satırları listele. JSON Liste dön:
            [{"isyeri_adi": "...", "tarih": "GG.AA.YYYY", "kategori": "...", "toplam_tutar": "0.00", "toplam_kdv": "0"}, ...]"""

        payload = {"contents": [{"parts": [{"text": prompt}, {"inline_data": {"mime_type": mime_type, "data": base64_data}}]}]}
        response = requests.post(url, headers=headers, json=payload)
        if response.status_code != 200: return {"hata": "API Hatası"}
        
        metin = response.json()['candidates'][0]['content']['parts'][0]['text'].replace("```json", "").replace("```", "").strip()
        veri = json.loads(metin)
        
        if isinstance(veri, list):
            for v in veri: v["dosya_adi"] = f"Ekstre_{dosya_objesi.name}"
            return veri
        else:
            veri["dosya_adi"] = dosya_objesi.name
            return veri
    except Exception as e: return {"hata": str(e)}

def arsiv_olustur(veri_listesi):
    zip_buffer = io.BytesIO()
    with zipfile.ZipFile(zip_buffer, "w", zipfile.ZIP_DEFLATED) as zip_file:
        for veri in veri_listesi:
            if "_ham_dosya" in veri:
                try:
                    tarih = veri.get("tarih", "00.00.0000").replace("/", ".")
                    yer = "".join([c for c in veri.get("isyeri_adi","").upper() if c.isalnum()])[:10]
                    tutar = str(veri.get("toplam_tutar", "0")).replace(".", ",")
                    ad = f"{tarih}_{yer}_{tutar}TL.{veri.get('_dosya_turu','jpg')}"
                    zip_file.writestr(ad, veri["_ham_dosya"])
                except: zip_file.writestr(f"HATA_{veri.get('dosya_adi')}", veri["_ham_dosya"])
    return zip_buffer.getvalue()

# --- 6. ARAYÜZ ---
with st.sidebar:
    st.title("🏢 Mihsap Enterprise")
    
    # --- MÜŞTERİ YÖNETİMİ ---
    st.markdown("### 👥 Müşteri Paneli")
    musteri_listesi = musteri_listesini_getir()
    secili_musteri = st.selectbox("Aktif Müşteri", musteri_listesi)
    
    # EKLEME
    with st.expander("➕ Müşteri Ekle"):
        yeni_ad = st.text_input("Firma Adı")
        if st.button("Ekle"):
            if yeni_ad:
                sonuc = yeni_musteri_ekle(yeni_ad)
                if sonuc == True:
                    st.success("Eklendi! Yenileniyor...")
                    time.sleep(1)
                    st.rerun()
                else: st.error(f"Hata: {sonuc}")

    # SİLME (YENİ ÖZELLİK)
    with st.expander("➖ Müşteri Sil", expanded=False):
        silinecek = st.selectbox("Silinecek Müşteri", [m for m in musteri_listesi if m != "Varsayılan Müşteri"])
        st.warning(f"Dikkat: '{silinecek}' veritabanından ve tüm kayıtlarından silinecek!")
        if st.button("🗑️ Sil ve Onayla"):
            res = musteri_sil(silinecek)
            if res == True:
                st.success("Müşteri silindi! Sayfa yenileniyor...")
                time.sleep(1)
                st.rerun()
            else:
                st.error(f"Silinemedi: {res}")

    st.divider()
    st.info(f"İşlem: **{secili_musteri}**")
    
    # AYARLAR
    st.divider()
    modeller = modelleri_getir()
    model = st.selectbox("AI Modeli", modeller) if modeller else "gemini-1.5-flash"
    hiz = st.slider("Hız", 1, 5, 3)
    
    if st.button("❌ Temizle"):
        st.session_state['uploader_key'] = st.session_state.get('uploader_key', 0) + 1
        if 'analiz_sonuclari' in st.session_state: del st.session_state['analiz_sonuclari']
        st.rerun()

# --- SEKMELER ---
t1, t2, t3 = st.tabs([f"📤 {secili_musteri} - İşlemler", "📊 Raporlar", "⚙️ Ayarlar"])

with t1:
    st.header(f"Evrak İşleme: {secili_musteri}")
    col_a, col_b = st.columns(2)
    with col_a:
        dosyalar = st.file_uploader("Fiş / Fatura", type=['jpg','png','pdf'], accept_multiple_files=True, key=f"f_{st.session_state.get('uploader_key',0)}")
    with col_b:
        ekstreler = st.file_uploader("Banka Ekstresi", type=['pdf','jpg'], accept_multiple_files=True, key=f"e_{st.session_state.get('uploader_key',0)}")
    
    if st.button("🚀 Başlat", type="primary"):
        tum_veriler = []
        bar = st.progress(0)
        
        # Fişler
        if dosyalar:
            with concurrent.futures.ThreadPoolExecutor(max_workers=hiz) as executor:
                future_to_file = {executor.submit(gemini_ile_analiz_et, d, model, "fis"): d for d in dosyalar}
                for future in concurrent.futures.as_completed(future_to_file):
                    res = future.result()
                    if "hata" not in res: 
                        res["_ham_dosya"] = future_to_file[future].getvalue()
                        res["_dosya_turu"] = future_to_file[future].type.split('/')[-1]
                        tum_veriler.append(res)
        
        # Ekstreler
        if ekstreler:
            with st.spinner("Ekstre okunuyor..."):
                for d in ekstreler:
                    res = gemini_ile_analiz_et(d, model, "ekstre")
                    if isinstance(res, list): tum_veriler.extend(res)
        
        if tum_veriler:
            st.session_state['analiz_sonuclari'] = tum_veriler
            sheete_kaydet(tum_veriler, secili_musteri)
            st.success(f"✅ {len(tum_veriler)} işlem '{secili_musteri}' hesabına kaydedildi!")
        
        bar.progress(100)

    if 'analiz_sonuclari' in st.session_state:
        data = st.session_state['analiz_sonuclari']
        df = pd.DataFrame(data)
        df_show = df.drop(columns=["_ham_dosya", "_dosya_turu"], errors='ignore')
        
        st.dataframe(df_show, use_container_width=True)
        
        c1, c2 = st.columns(2)
        with c1:
            zip_data = arsiv_olustur(data)
            st.download_button("📦 Arşivi İndir (ZIP)", zip_data, f"{secili_musteri}_arsiv.zip", "application/zip")
        with c2:
            df_muh = muhasebe_fisne_cevir(df_show)
            buf = io.BytesIO()
            with pd.ExcelWriter(buf, engine='openpyxl') as writer: df_muh.to_excel(writer, index=False)
            st.download_button("📥 Muhasebe Fişi", buf.getvalue(), f"{secili_musteri}_fiş.xlsx")

with t2:
    st.header(f"Yönetim Paneli: {secili_musteri}")
    if st.button("🔄 Verileri Çek"): st.rerun()
    df_db = sheetten_veri_cek(secili_musteri)
    if not df_db.empty:
        col_tutar = next((c for c in df_db.columns if "tutar" in c), None)
        if col_tutar:
            toplam = df_db[col_tutar].sum()
            st.metric("Toplam Gider", f"{toplam:,.2f} ₺")
            st.dataframe(df_db, use_container_width=True)
        else: st.warning("Veri var ama tutar sütunu bulunamadı.")
    else: st.info(f"'{secili_musteri}' için henüz kayıt yok.")

with t3:
    st.header("⚙️ Hesap Planı Ayarları")
    col1, col2 = st.columns(2)
    yeni_kodlar = st.session_state['hesap_kodlari'].copy()
    
    with col1:
        yeni_kodlar["Gıda"] = st.text_input("Gıda Kodu", yeni_kodlar["Gıda"])
        yeni_kodlar["Ulaşım"] = st.text_input("Ulaşım", yeni_kodlar["Ulaşım"])
        yeni_kodlar["Kırtasiye"] = st.text_input("Kırtasiye", yeni_kodlar["Kırtasiye"])
        yeni_kodlar["KDV"] = st.text_input("KDV (191)", yeni_kodlar["KDV"])
    with col2:
        yeni_kodlar["Teknoloji"] = st.text_input("Teknoloji", yeni_kodlar["Teknoloji"])
        yeni_kodlar["Konaklama"] = st.text_input("Konaklama", yeni_kodlar["Konaklama"])
        yeni_kodlar["Diğer"] = st.text_input("Diğer", yeni_kodlar["Diğer"])
        yeni_kodlar["Kasa"] = st.text_input("Kasa (100)", yeni_kodlar["Kasa"])
        yeni_kodlar["Banka"] = st.text_input("Banka (102)", yeni_kodlar["Banka"])

    if st.button("💾 Ayarları Kaydet"):
        st.session_state['hesap_kodlari'] = yeni_kodlar
        st.success("Ayarlar güncellendi!")
