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

# --- 1. SAYFA AYARLARI ---
st.set_page_config(page_title="Muhabese AI", layout="wide", page_icon="🏢")

# --- 2. GÜVENLİK ---
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

# --- 3. DEĞİŞKENLER ---
if 'uploader_key' not in st.session_state: st.session_state['uploader_key'] = 0
if 'hesap_kodlari' not in st.session_state:
    st.session_state['hesap_kodlari'] = {
        "Gıda": "770.01", "Ulaşım": "770.02", "Kırtasiye": "770.03", 
        "Teknoloji": "770.04", "Konaklama": "770.05", "Diğer": "770.99",
        "KDV": "191.18", "Kasa": "100.01", "Banka": "102.01"
    }
# İşlenen verileri hafızada tutmak için
if 'analiz_sonuclari' not in st.session_state:
    st.session_state['analiz_sonuclari'] = []

# --- 4. MOTORLAR ---
def temizle_ve_sayiya_cevir(deger):
    if pd.isna(deger) or deger == "": return 0.0
    try:
        s = str(deger).replace("₺", "").replace("TL", "").strip()
        if "," in s and "." in s: s = s.replace(".", "").replace(",", ".")
        elif "," in s: s = s.replace(",", ".")
        return float(s)
    except: return 0.0

def yeni_dosya_adi_olustur(veri):
    try:
        tarih = str(veri.get("tarih", "00.00.0000")).replace("/", ".").replace("-", ".")
        yer = "".join([c for c in str(veri.get("isyeri_adi","Firma")).upper() if c.isalnum()])[:15]
        tutar = str(veri.get("toplam_tutar", "0")).replace(".", ",")
        uzanti = veri.get("_dosya_turu", "jpg")
        return f"{tarih}_{yer}_{tutar}TL.{uzanti}"
    except: return f"HATA_{veri.get('dosya_adi')}"

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

# --- 5. GOOGLE SHEETS ---
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
        try: 
            ns = sheet.add_worksheet(ad, 1000, 12)
            ns.append_row(["Dosya Adı", "İşyeri", "Fiş No", "Tarih", "Kategori", "Tutar", "KDV", "Zaman", "Durum", "QR"])
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
        
        if not ws.row_values(1):
            ws.append_row(["Dosya Adı", "İşyeri", "Fiş No", "Tarih", "Kategori", "Tutar", "KDV", "Zaman", "Durum", "QR"])

        rows = []
        for v in veri:
            durum = "✅" if float(str(v.get('toplam_tutar',0)).replace(',','.')) > 0 else "⚠️"
            qr_durumu = "📱QR" if v.get("qr_gecerli") else "-"
            temiz_ad = yeni_dosya_adi_olustur(v)
            
            rows.append([
                temiz_ad, v.get("isyeri_adi", "-"), v.get("fiş_no", "-"), 
                v.get("tarih", "-"), v.get("kategori", "Diğer"), 
                str(v.get("toplam_tutar", "0")), str(v.get("toplam_kdv", "0")), 
                datetime.now().strftime("%Y-%m-%d %H:%M:%S"), durum, qr_durumu
            ])
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
        tum = [m['name'].replace("models/", "") for m in data.get('models', []) if 'generateContent' in m.get('supportedGenerationMethods', [])]
        f25 = [m for m in tum if "2.5-flash" in m]
        f20 = [m for m in tum if "2.0-flash" in m]
        f15 = [m for m in tum if "1.5-flash" in m]
        return f25 + f20 + f15 + [m for m in tum if m not in f25+f20+f15]
    except: return ["gemini-1.5-flash"]

def qr_kodu_oku_ve_filtrele(image_bytes):
    try:
        nparr = np.frombuffer(image_bytes, np.uint8)
        img = cv2.imdecode(nparr, cv2.IMREAD_COLOR)
        decoded_objects = decode(img)
        for obj in decoded_objects:
            raw = obj.data.decode("utf-8")
            if len(raw) > 10: return raw 
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

def gemini_ile_analiz_et(dosya_objesi, secilen_model, mod="fis", retries=3):
    for attempt in range(retries):
        try:
            qr_data = None
            if dosya_objesi.type != "application/pdf":
                qr_data = qr_kodu_oku_ve_filtrele(dosya_objesi.getvalue())
            
            base64_data, mime_type = dosyayi_hazirla(dosya_objesi)
            url = f"https://generativelanguage.googleapis.com/v1beta/models/{secilen_model}:generateContent?key={API_KEY}"
            headers = {'Content-Type': 'application/json'}
            
            qr_bilgisi = f"\n[İPUCU]: QR kod bulundu: '{qr_data}'" if qr_data else ""

            if mod == "fis":
                prompt = f"""Bu belgeyi analiz et. {qr_bilgisi}
                GÖREVLER:
                1. Kategori Tespiti: Firma adına aldanma, ürüne bak (Örn: Ofel Turizm -> Kitap -> Kırtasiye).
                2. JSON Çıktı:
                {{"isyeri_adi": "...", "fiş_no": "...", "tarih": "GG.AA.YYYY", "kategori": "Gıda/Akaryakıt/Kırtasiye/Teknoloji/Konaklama/Diğer", "toplam_tutar": "0.00", "toplam_kdv": "0.00"}}
                Tarih formatı Gün.Ay.Yıl olsun.
                """
            else:
                prompt = """Kredi kartı ekstresi satırları. JSON Liste: [{"isyeri_adi": "...", "tarih": "GG.AA.YYYY", "kategori": "...", "toplam_tutar": "0.00", "toplam_kdv": "0"}, ...]"""

            payload = {"contents": [{"parts": [{"text": prompt}, {"inline_data": {"mime_type": mime_type, "data": base64_data}}]}]}
            response = requests.post(url, headers=headers, json=payload)
            
            if response.status_code == 429: time.sleep(2 ** (attempt + 1)); continue 
            if response.status_code != 200: return {"hata": f"API Hatası ({response.status_code})"}
            
            metin = response.json()['candidates'][0]['content']['parts'][0]['text'].replace("```json", "").replace("```", "").strip()
            veri = json.loads(metin)
            
            if isinstance(veri, list):
                for v in veri: v["dosya_adi"] = f"Ekstre_{dosya_objesi.name}"; v["qr_gecerli"] = False
                return veri
            else:
                veri["dosya_adi"] = dosya_objesi.name
                veri["qr_gecerli"] = True if qr_data else False
                veri["_ham_dosya"] = dosya_objesi.getvalue()
                veri["_dosya_turu"] = "pdf" if mime_type == "application/pdf" else "jpg"
                return veri
        except Exception as e: return {"hata": str(e)}
    return {"hata": "Kota limiti nedeniyle işlem yapılamadı."}

def arsiv_olustur(veri_listesi):
    zip_buffer = io.BytesIO()
    with zipfile.ZipFile(zip_buffer, "w", zipfile.ZIP_DEFLATED) as zip_file:
        for veri in veri_listesi:
            if "_ham_dosya" in veri:
                yeni_ad = yeni_dosya_adi_olustur(veri)
                zip_file.writestr(yeni_ad, veri["_ham_dosya"])
    return zip_buffer.getvalue()

# --- 6. ARAYÜZ ---
with st.sidebar:
    st.markdown("""
        <div style="text-align: center;">
            <h1 style="color: #0F52BA; font-size: 28px; margin-bottom: 0;">🏢 Muhabese AI</h1>
            <p style="font-size: 14px; color: gray; margin-top: 0;">Akıllı Finans Asistanı</p>
        </div>
        """, unsafe_allow_html=True)
    st.divider()
    
    st.markdown("### 👥 Müşteri Seçimi")
    musteriler = musteri_listesini_getir()
    secili = st.selectbox("Aktif Müşteri", musteriler, label_visibility="collapsed")
    
    with st.expander("➕ Ekle / ➖ Sil"):
        if st.button("Ekle", use_container_width=True):
            yeni = st.text_input("Firma Adı", key="new_c")
            if yeni and yeni_musteri_ekle(yeni) == True: st.success("Eklendi!"); time.sleep(1); st.rerun()
        if st.button("Sil", use_container_width=True):
            sil = st.selectbox("Silinecek", [m for m in musteriler if m!="Varsayılan Müşteri"], key="del_c")
            if musteri_sil(sil): st.success("Silindi!"); time.sleep(1); st.rerun()

    st.divider()
    modeller = modelleri_getir()
    model = st.selectbox("AI Modeli", modeller, index=0)
    hiz = st.slider("İşlem Hızı", 1, 20, 10) 
    
    if st.button("❌ Ekranı Temizle", use_container_width=True):
        st.session_state['uploader_key'] += 1
        if 'analiz_sonuclari' in st.session_state: del st.session_state['analiz_sonuclari']
        st.rerun()

t1, t2, t3, t4 = st.tabs([f"📤 {secili} - Evraklar", "🔍 İnceleme & Onay", "📊 Raporlar", "⚙️ Hesap Planı"])

# --- TAB 1: EVRAK İŞLEME ---
with t1:
    st.info("Fişleri veya Ekstreleri aşağıya sürükleyin.")
    c1, c2 = st.columns(2)
    with c1: fisler = st.file_uploader("Fiş / Fatura", type=['jpg','png','pdf'], accept_multiple_files=True, key=f"f_{st.session_state['uploader_key']}")
    with c2: ekstre = st.file_uploader("Ekstre", type=['pdf','jpg'], accept_multiple_files=True, key=f"e_{st.session_state['uploader_key']}")
    
    if st.button("🚀 Analizi Başlat", type="primary", use_container_width=True):
        tum = []
        bar = st.progress(0)
        
        # Fiş İşleme
        if fisler:
            with concurrent.futures.ThreadPoolExecutor(max_workers=hiz) as exe:
                futures = {exe.submit(gemini_ile_analiz_et, d, model, "fis"): d for d in fisler}
                completed = 0
                for f in concurrent.futures.as_completed(futures):
                    r = f.result()
                    if "hata" not in r: tum.append(r)
                    completed += 1
                    bar.progress(completed / len(fisler))
        
        # Ekstre İşleme
        if ekstre:
            with st.spinner("Ekstre okunuyor..."):
                for d in ekstre:
                    r = gemini_ile_analiz_et(d, model, "ekstre")
                    if isinstance(r, list): tum.extend(r)
        
        # Sonuçları Hafızaya Al
        if tum:
            st.session_state['analiz_sonuclari'] = tum
            st.success(f"✅ {len(tum)} kayıt işlendi! İnceleme sekmesine gidiniz.")
            # Otomatik kayıt yapmıyoruz, kullanıcı "İnceleme" sekmesinde onaylayacak.
        else:
            st.warning("İşlenecek veri bulunamadı veya hata oluştu.")

# --- TAB 2: İNCELEME & ONAY (YENİ!) ---
with t2:
    if 'analiz_sonuclari' in st.session_state and st.session_state['analiz_sonuclari']:
        st.header("🔍 Detaylı İnceleme")
        
        # Evrak Listesi
        veriler = st.session_state['analiz_sonuclari']
        
        # İşaretli Liste Oluşturma (✅/⚠️)
        dosya_listesi = []
        for i, v in enumerate(veriler):
            tutar = temizle_ve_sayiya_cevir(v.get("toplam_tutar", 0))
            ikon = "✅" if tutar > 0 and v.get("isyeri_adi") else "⚠️"
            etiket = f"{ikon} {i+1}. {v.get('isyeri_adi', 'Bilinmiyor')} ({v.get('toplam_tutar','0')} TL)"
            dosya_listesi.append(etiket)
        
        secilen_etiket = st.selectbox("İncelenecek Evrak", dosya_listesi)
        secilen_index = dosya_listesi.index(secilen_etiket)
        secili_veri = veriler[secilen_index]
        
        col_img, col_form = st.columns([1, 1])
        
        # SOL: Resim
        with col_img:
            if "_ham_dosya" in secili_veri:
                if secili_veri["_dosya_turu"] == "pdf":
                    st.info("📄 Bu bir PDF belgesidir. Önizleme için dosyayı indirin.")
                else:
                    st.image(secili_veri["_ham_dosya"], caption="Orijinal Belge", use_column_width=True)
            else:
                st.info("Bu bir ekstre satırı olduğu için görsel yok.")

        # SAĞ: Düzenlenebilir Form
        with col_form:
            st.subheader("Veri Düzenleme")
            with st.form(key=f"edit_form_{secilen_index}"):
                yeni_isyeri = st.text_input("İşyeri Adı", secili_veri.get("isyeri_adi", ""))
                yeni_tarih = st.text_input("Tarih", secili_veri.get("tarih", ""))
                yeni_tutar = st.text_input("Toplam Tutar", str(secili_veri.get("toplam_tutar", "")))
                yeni_kdv = st.text_input("Toplam KDV", str(secili_veri.get("toplam_kdv", "")))
                
                mevcut_kat = secili_veri.get("kategori", "Diğer")
                kategoriler = ["Gıda", "Ulaşım", "Kırtasiye", "Teknoloji", "Konaklama", "Diğer"]
                idx = kategoriler.index(mevcut_kat) if mevcut_kat in kategoriler else 5
                yeni_kategori = st.selectbox("Kategori", kategoriler, index=idx)
                
                if st.form_submit_button("💾 Değişiklikleri Kaydet"):
                    # Hafızadaki veriyi güncelle
                    st.session_state['analiz_sonuclari'][secilen_index]["isyeri_adi"] = yeni_isyeri
                    st.session_state['analiz_sonuclari'][secilen_index]["tarih"] = yeni_tarih
                    st.session_state['analiz_sonuclari'][secilen_index]["toplam_tutar"] = yeni_tutar
                    st.session_state['analiz_sonuclari'][secilen_index]["toplam_kdv"] = yeni_kdv
                    st.session_state['analiz_sonuclari'][secilen_index]["kategori"] = yeni_kategori
                    st.success("Güncellendi!")
                    st.rerun()

        st.divider()
        
        # TOPLU KAYIT BUTONU
        st.info("Tüm kontroller bitti mi?")
        if st.button("✅ Hepsini Onayla ve Veritabanına Kaydet", type="primary", use_container_width=True):
            if sheete_kaydet(st.session_state['analiz_sonuclari'], secili):
                st.balloons()
                st.success("Tüm veriler Google Sheets'e başarıyla işlendi!")
            else:
                st.error("Kayıt sırasında hata oluştu.")
    else:
        st.info("İncelenecek veri yok. Önce 'Evrak İşleme' sekmesinden dosya yükleyin.")

# --- TAB 3: RAPORLAR ---
with t3:
    st.header("📊 Raporlar ve Çıktılar")
    
    if 'analiz_sonuclari' in st.session_state:
        dt = st.session_state['analiz_sonuclari']
        df = pd.DataFrame(dt)
        
        c1, c2, c3 = st.columns(3)
        with c1: st.download_button("📦 ZIP Arşiv", arsiv_olustur(dt), f"{secili}_arsiv.zip", "application/zip", use_container_width=True)
        with c2:
            buf_list = io.BytesIO()
            with pd.ExcelWriter(buf_list, engine='openpyxl') as w: 
                df.drop(columns=["_ham_dosya", "_dosya_turu", "qr_data", "qr_icerigi"], errors='ignore').to_excel(w, index=False)
            st.download_button("📥 Basit Excel", buf_list.getvalue(), f"{secili}_liste.xlsx", use_container_width=True)
        with c3:
            df_m = muhasebe_fisne_cevir(df)
            buf = io.BytesIO()
            with pd.ExcelWriter(buf, engine='openpyxl') as w: df_m.to_excel(w, index=False)
            st.download_button("📥 Muhasebe Fişi", buf.getvalue(), f"{secili}_fiş.xlsx", type="primary", use_container_width=True)

    st.divider()
    st.subheader("Yönetim Paneli (Veritabanı)")
    if st.button("🔄 Verileri Güncelle"): st.rerun()
    df_db = sheetten_veri_cek(secili)
    
    if not df_db.empty:
        col_tutar = next((c for c in df_db.columns if "tutar" in c), None)
        if col_tutar:
            st.metric("Toplam Kayıtlı Harcama", f"{df_db[col_tutar].sum():,.2f} ₺")
            st.dataframe(df_db, use_container_width=True)

# --- TAB 4: AYARLAR ---
with t4:
    st.header("Hesap Planı Ayarları")
    hk = st.session_state['hesap_kodlari']
    c1, c2 = st.columns(2)
    with c1:
        hk["Gıda"] = st.text_input("Gıda", hk["Gıda"])
        hk["Ulaşım"] = st.text_input("Ulaşım", hk["Ulaşım"])
        hk["Kırtasiye"] = st.text_input("Kırtasiye", hk["Kırtasiye"])
    with c2:
        hk["Teknoloji"] = st.text_input("Teknoloji", hk["Teknoloji"])
        hk["Konaklama"] = st.text_input("Konaklama", hk["Konaklama"])
        hk["Diğer"] = st.text_input("Diğer", hk["Diğer"])
    if st.button("Kaydet"): st.success("Kaydedildi!")
